from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .host_supervisor_client import (
    ALLOWED_HOST_ACTIONS,
    LOCAL_BRIDGE_RESTART_EXIT_CODE,
    SUPERVISOR_REQUEST_SCHEMA,
    SUPERVISOR_RESPONSE_SCHEMA,
    SUPERVISOR_STATUS_SCHEMA,
)
from .instance_lock_runtime import (
    InstanceLockManager,
    build_instance_lock_runtime_deps,
)
from .paths import get_repo_root, get_runtime_artifacts_root
from .process_identity import (
    birth_identity_matches_current_platform,
    process_birth_identity,
    terminate_process_identity,
)
from .runtime_artifact_io import atomic_json_write, read_bounded_json
from .runtime_error_observability import RuntimeErrorCounter
from .storage_retention_report import StorageRetentionReporter
from .voice_capture_consent import (
    BRIDGE_STATUS_AUTH_SCOPE,
    SUPERVISOR_STOP_AUTH_SCOPE,
    VOICE_CAPTURE_AUTH_ENV,
    resolve_voice_capture_auth_token,
    sign_voice_capture_artifact,
    voice_capture_artifact_is_authentic,
)
from .voice_validation import active_validation_context, emit_voice_validation_event
from .windows_process_job import KillOnCloseProcessOwner


PREVIEW_TTL_SEC = 120.0
AUTO_RESTART_LIMIT = 3
AUTO_RESTART_WINDOW_SEC = 10 * 60
HEARTBEAT_INTERVAL_SEC = 1.0
_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")
_CREDENTIAL_ENV_PATTERN = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIALS?|API_KEY|PRIVATE_KEY|ACCESS_KEY)(?:_|$)",
    re.IGNORECASE,
)
BRIDGE_PROCESS_IDENTITY_SCHEMA = (
    "host_supervisor.local-bridge-process-identity.v1"
)
BRIDGE_STATUS_FRESH_SEC = 3.0
BRIDGE_STATUS_MAX_BYTES = 131072
VOICE_CAPTURE_STOP_SCHEMA = "host_supervisor.voice-capture-stop.v1"


class HostSupervisor:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        artifacts_root: Path | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        run_command: Callable[..., Any] = subprocess.run,
        now: Callable[[], float] = time.time,
        retention_reporter: Any | None = None,
        birth_identity_reader: Callable[[int], str | None] = process_birth_identity,
        exact_process_terminator: Callable[[int, str], bool] = terminate_process_identity,
        process_owner: Any | None = None,
        bridge_lock_probe: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        voice_capture_auth_token: str | None = None,
    ) -> None:
        self.project_root = Path(project_root or get_repo_root()).resolve()
        self.artifacts_root = Path(
            artifacts_root or get_runtime_artifacts_root()
        ).resolve()
        self.root = self.artifacts_root / "host_supervisor"
        self.requests_dir = self.root / "requests"
        self.responses_dir = self.root / "responses"
        self.status_path = self.root / "status.json"
        self.stop_request_path = self.root / "stop.request"
        self.bridge_status_path = self.artifacts_root / "local_bridge" / "status.json"
        self.bridge_lock_path = self.artifacts_root / "local_bridge" / "instance.lock"
        self.bridge_identity_path = self.root / "local_bridge_process_identity.json"
        self.bridge_log_path = self.artifacts_root / "logs" / "Local-IO-Bridge.log"
        self.popen = popen
        self.run_command = run_command
        self.now = now
        self.sleep = sleep
        self.voice_capture_auth_token = resolve_voice_capture_auth_token(
            voice_capture_auth_token
        )
        self.birth_identity_reader = birth_identity_reader
        self.exact_process_terminator = exact_process_terminator
        self.bridge_lock_probe = bridge_lock_probe
        self.started_at = self.now()
        self.child: Any | None = None
        self.child_started_at: float | None = None
        self.child_exit_code: int | None = None
        self.child_birth_identity = ""
        self.bridge_status_instance_id = ""
        self.bridge_status_seq_high_water = 0
        self.bridge_identity_state = "unknown"
        self._startup_reconciled = False
        self.restart_history: deque[float] = deque()
        self.manual_intervention_required = False
        self.last_error = ""
        self.last_action: dict[str, Any] = {}
        self.runtime_errors = RuntimeErrorCounter(now=self.now)
        self._tokens: dict[str, dict[str, Any]] = {}
        self._stopping = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self.process_owner_error = ""
        self.process_owner = process_owner
        if self.process_owner is None:
            try:
                self.process_owner = KillOnCloseProcessOwner()
            except Exception as exc:
                self.process_owner = None
                self.process_owner_error = "host_supervisor_process_owner_unavailable"
                self.runtime_errors.record(
                    self.process_owner_error,
                    exc,
                )
        self.retention_reporter = retention_reporter or StorageRetentionReporter(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            now=self.now,
        )

    @staticmethod
    def _bridge_identity_payload(
        *,
        state: str,
        pid: int = 0,
        birth_identity: str = "",
        updated_at: float,
    ) -> dict[str, Any]:
        return {
            "schema": BRIDGE_PROCESS_IDENTITY_SCHEMA,
            "state": state,
            "pid": int(pid),
            "birthIdentity": str(birth_identity),
            "updatedAt": float(updated_at),
            "contentFree": True,
        }

    @staticmethod
    def _valid_bridge_identity(payload: Any) -> bool:
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "state",
            "pid",
            "birthIdentity",
            "updatedAt",
            "contentFree",
        }:
            return False
        if (
            payload.get("schema") != BRIDGE_PROCESS_IDENTITY_SCHEMA
            or payload.get("state") not in {"starting", "active", "stopped"}
            or isinstance(payload.get("pid"), bool)
            or not isinstance(payload.get("pid"), int)
            or isinstance(payload.get("updatedAt"), bool)
            or not isinstance(payload.get("updatedAt"), (int, float))
            or payload.get("contentFree") is not True
            or not isinstance(payload.get("birthIdentity"), str)
        ):
            return False
        if payload["state"] in {"starting", "stopped"}:
            return payload["pid"] == 0 and payload["birthIdentity"] == ""
        prefix, separator, value = payload["birthIdentity"].partition(":")
        return bool(
            payload["pid"] > 0
            and separator
            and prefix in {"linux", "windows"}
            and value.isdigit()
            and len(value) <= 32
        )

    def _write_bridge_identity(
        self,
        *,
        state: str,
        pid: int = 0,
        birth_identity: str = "",
    ) -> None:
        payload = self._bridge_identity_payload(
            state=state,
            pid=pid,
            birth_identity=birth_identity,
            updated_at=self.now(),
        )
        if not self._valid_bridge_identity(payload):
            raise ValueError("local_bridge_process_identity_invalid")
        atomic_json_write(self.bridge_identity_path, payload, durable=True)
        self.bridge_identity_state = state

    def _load_bridge_identity(self) -> tuple[dict[str, Any] | None, str]:
        try:
            payload = json.loads(self.bridge_identity_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, "local_bridge_prior_process_identity_missing"
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None, "local_bridge_prior_process_identity_invalid"
        if not self._valid_bridge_identity(payload):
            return None, "local_bridge_prior_process_identity_invalid"
        self.bridge_identity_state = str(payload["state"])
        return payload, ""

    def _bridge_lock_is_free(self) -> bool:
        if self.bridge_lock_probe is not None:
            return bool(self.bridge_lock_probe())
        deps = build_instance_lock_runtime_deps(self.bridge_lock_path)
        if deps.msvcrt_module is None and deps.fcntl_module is None:
            raise RuntimeError("local_bridge_instance_lock_backend_unavailable")
        manager = InstanceLockManager(deps)
        try:
            manager.acquire(wait_sec=0.0)
        except RuntimeError:
            return False
        finally:
            manager.release()
        return True

    def _bridge_status_is_fresh(self) -> bool:
        try:
            payload = read_bounded_json(
                self.bridge_status_path,
                maximum_bytes=BRIDGE_STATUS_MAX_BYTES,
            )
            heartbeat_at = float(payload.get("heartbeatAt"))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        age = self.now() - heartbeat_at
        return (
            payload.get("schema") == "local_io_bridge.status.v1"
            and 0.0 <= age <= BRIDGE_STATUS_FRESH_SEC
        )

    def _voice_capture_stop_evidence(self) -> dict[str, Any]:
        result = {
            "schema": VOICE_CAPTURE_STOP_SCHEMA,
            "state": "unverified",
            "reason": "local_bridge_status_unverified",
            "observedAt": None,
            "statusSeq": 0,
            "micEnabled": None,
            "captureStopped": None,
            "contentFree": True,
        }

        def signed_result() -> dict[str, Any]:
            return sign_voice_capture_artifact(
                result,
                auth_scope=SUPERVISOR_STOP_AUTH_SCOPE,
                auth_token=self.voice_capture_auth_token,
            )

        child = self.child
        if child is None or child.poll() is not None:
            return signed_result()
        try:
            if self.bridge_status_path.is_symlink():
                return signed_result()
            payload = read_bounded_json(
                self.bridge_status_path,
                maximum_bytes=BRIDGE_STATUS_MAX_BYTES,
            )
            if not isinstance(payload, dict):
                return signed_result()
            heartbeat_at = payload.get("heartbeatAt")
            status_seq = payload.get("statusSeq")
            pid = payload.get("pid")
            bridge_instance_id = payload.get("bridgeInstanceId")
            watchdog = payload.get("voiceCaptureWatchdog")
            mic = payload.get("mic")
            if (
                payload.get("schema") != "local_io_bridge.status.v1"
                or not voice_capture_artifact_is_authentic(
                    payload,
                    auth_scope=BRIDGE_STATUS_AUTH_SCOPE,
                    auth_token=self.voice_capture_auth_token,
                )
                or isinstance(heartbeat_at, bool)
                or not isinstance(heartbeat_at, (int, float))
                or not math.isfinite(float(heartbeat_at))
                or not 0.0 <= self.now() - float(heartbeat_at) <= BRIDGE_STATUS_FRESH_SEC
                or self.child_started_at is None
                or float(heartbeat_at) < float(self.child_started_at)
                or isinstance(status_seq, bool)
                or not isinstance(status_seq, int)
                or status_seq <= 0
                or (
                    self.bridge_status_instance_id
                    and bridge_instance_id != self.bridge_status_instance_id
                )
                or status_seq < self.bridge_status_seq_high_water
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid != int(child.pid)
                or not isinstance(bridge_instance_id, str)
                or len(bridge_instance_id) != 32
                or not all(
                    character in "0123456789abcdef"
                    for character in bridge_instance_id
                )
                or type(payload.get("micEnabled")) is not bool
                or type(payload.get("micCaptureStopped")) is not bool
                or not isinstance(watchdog, dict)
                or set(watchdog) != {
                    "schema", "state", "reason", "checkedAt",
                    "captureStopped", "stoppedAt", "contentFree",
                }
                or watchdog.get("schema") != "voice.capture-consent.watchdog-status.v1"
                or watchdog.get("state") not in {"authorized", "blocked", "stop_failed"}
                or not isinstance(watchdog.get("reason"), str)
                or (watchdog["state"] == "authorized") is not (watchdog["reason"] == "")
                or not isinstance(watchdog.get("checkedAt"), (int, float))
                or isinstance(watchdog.get("checkedAt"), bool)
                or not math.isfinite(float(watchdog["checkedAt"]))
                or float(watchdog["checkedAt"]) > float(heartbeat_at)
                or type(watchdog.get("captureStopped")) is not bool
                or watchdog["captureStopped"] is not payload["micCaptureStopped"]
                or watchdog.get("contentFree") is not True
                or (
                    watchdog.get("stoppedAt") is not None
                    and (
                        isinstance(watchdog["stoppedAt"], bool)
                        or not isinstance(watchdog["stoppedAt"], (int, float))
                        or not math.isfinite(float(watchdog["stoppedAt"]))
                    )
                )
                or not isinstance(mic, dict)
            ):
                return signed_result()
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return signed_result()
        if not self.bridge_status_instance_id:
            self.bridge_status_instance_id = bridge_instance_id
        self.bridge_status_seq_high_water = max(
            self.bridge_status_seq_high_water,
            status_seq,
        )
        result.update(
            observedAt=float(heartbeat_at),
            statusSeq=status_seq,
            micEnabled=payload["micEnabled"],
            captureStopped=payload["micCaptureStopped"],
        )
        stopped_at = watchdog.get("stoppedAt")
        physical_off = (
            payload["micEnabled"] is False
            and payload["micCaptureStopped"] is True
            and mic.get("enabled") is False
            and mic.get("captureReady") is False
            and mic.get("captureActive") is False
            and mic.get("captureStopped") is True
        )
        stopped = (
            watchdog["state"] == "blocked"
            and stopped_at is not None
            and float(watchdog["checkedAt"]) <= float(stopped_at) <= float(heartbeat_at)
            and physical_off
        )
        if stopped:
            result.update(state="verified", reason="voice_capture_watchdog_stop")
        elif watchdog["state"] == "authorized" or (
            watchdog["state"] == "blocked"
            and stopped_at is None
            and physical_off
        ):
            result.update(state="not_required", reason="")
        else:
            result["reason"] = "voice_capture_stop_unverified"
        return signed_result()

    def _manual_failure(self, error: str) -> dict[str, Any]:
        self.manual_intervention_required = True
        self.last_error = error
        self.runtime_errors.record(error)
        return {
            "ok": False,
            "error": error,
            "manualInterventionRequired": True,
        }

    def reconcile_prior_bridge(self) -> dict[str, Any]:
        """Prove no prior Local Bridge owns host I/O before a new spawn."""

        if self._startup_reconciled:
            return {"ok": True, "status": "already_reconciled"}
        payload, load_error = self._load_bridge_identity()
        if payload is None and load_error == "local_bridge_prior_process_identity_invalid":
            return self._manual_failure(load_error)
        try:
            lock_free = self._bridge_lock_is_free()
        except Exception:
            return self._manual_failure("local_bridge_instance_lock_unverified")
        if payload is None:
            # A fresh legacy heartbeat without a durable identity is an
            # authority ambiguity.  Never guess a PID from process listings.
            if not lock_free or self._bridge_status_is_fresh():
                return self._manual_failure(
                    "local_bridge_prior_process_identity_missing_live_bridge"
                )
        elif payload["state"] == "active":
            pid = int(payload["pid"])
            birth_identity = str(payload["birthIdentity"])
            if not birth_identity_matches_current_platform(birth_identity):
                return self._manual_failure(
                    "local_bridge_prior_process_identity_unverified"
                )
            try:
                observed = self.birth_identity_reader(pid)
            except (OSError, ValueError):
                return self._manual_failure(
                    "local_bridge_prior_process_identity_unverified"
                )
            if observed == birth_identity:
                try:
                    stopped = self.exact_process_terminator(pid, birth_identity)
                except (OSError, ValueError):
                    stopped = False
                if not stopped:
                    return self._manual_failure(
                        "local_bridge_prior_process_stop_unverified"
                    )
                try:
                    remaining = self.birth_identity_reader(pid)
                except (OSError, ValueError):
                    return self._manual_failure(
                        "local_bridge_prior_process_identity_unverified"
                    )
                if remaining == birth_identity:
                    return self._manual_failure(
                        "local_bridge_prior_process_stop_unverified"
                    )
            # A different birth identity proves PID reuse.  It must never be
            # signalled; the old Local Bridge is already gone.
            try:
                lock_free = self._bridge_lock_is_free()
            except Exception:
                return self._manual_failure("local_bridge_instance_lock_unverified")
            if not lock_free:
                return self._manual_failure("local_bridge_instance_lock_held")
        elif not lock_free:
            error = (
                "local_bridge_prior_process_start_ambiguous"
                if payload["state"] == "starting"
                else "local_bridge_instance_lock_held"
            )
            return self._manual_failure(error)
        try:
            self._write_bridge_identity(state="stopped")
        except (OSError, TypeError, ValueError):
            return self._manual_failure(
                "local_bridge_prior_process_identity_write_failed"
            )
        self._startup_reconciled = True
        self.manual_intervention_required = False
        self.last_error = ""
        return {"ok": True, "status": "reconciled"}

    def _bridge_command(self) -> list[str]:
        executable = sys.executable
        bootstrap: list[str] = ["-m", "evelyn_core.local_io_bridge"]
        if os.name == "nt":
            base_executable = Path(
                str(getattr(sys, "_base_executable", "") or "")
            )
            venv_site_packages = Path(sys.prefix) / "Lib" / "site-packages"
            if base_executable.resolve() != Path(sys.executable).resolve():
                if not base_executable.is_file() or not venv_site_packages.is_dir():
                    raise RuntimeError("host_supervisor_bridge_runtime_unavailable")
                site_packages = str(venv_site_packages)
                bootstrap_code = "\n".join(
                    (
                        "import runpy, site, sys",
                        f"_site = {site_packages!r}",
                        "site.addsitedir(_site)",
                        "if _site in sys.path:",
                        "    sys.path.remove(_site)",
                        "sys.path.insert(0, _site)",
                        "sys.argv = ['evelyn_core.local_io_bridge', *sys.argv[1:]]",
                        "runpy.run_module('evelyn_core.local_io_bridge', run_name='__main__')",
                    )
                )
                executable = str(base_executable)
                bootstrap = ["-c", bootstrap_code]
        return [
            executable,
            *bootstrap,
            "--project-root",
            str(self.project_root),
        ]

    def _bridge_environment(self) -> dict[str, str]:
        env = self._credential_scoped_environment(
            allowed_credentials={
                "LOCAL_BRIDGE_STATUS_AUTH_TOKEN",
                VOICE_CAPTURE_AUTH_ENV,
            }
        )
        runtime_root = self.project_root / "evelyn_core" / "runtime"
        existing_python_path = str(env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = (
            f"{runtime_root}{os.pathsep}{existing_python_path}"
            if existing_python_path
            else str(runtime_root)
        )
        env["EVELYN_PROJECT_ROOT"] = str(self.project_root)
        env["EVELYN_CORE_ROOT"] = str(self.project_root / "evelyn_core")
        env["EVELYN_CORE_RUNTIME"] = str(runtime_root)
        env["EVELYN_RUNTIME_ARTIFACTS_DIR"] = str(self.artifacts_root)
        env.setdefault("LOCAL_BRIDGE_BOT_API_BASE", "http://127.0.0.1:8798")
        env.setdefault("STT_SERVICE_URL", "http://127.0.0.1:8892")
        env.setdefault("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880")
        return env

    @staticmethod
    def _credential_scoped_environment(
        *,
        allowed_credentials: set[str] | None = None,
    ) -> dict[str, str]:
        allowed = {name.upper() for name in (allowed_credentials or set())}
        return {
            name: value
            for name, value in os.environ.items()
            if name.upper() in allowed or not _CREDENTIAL_ENV_PATTERN.search(name)
        }

    def start_bridge(self, *, automatic: bool = False) -> dict[str, Any]:
        if self.child is not None:
            observed_exit = self.child.poll()
            if observed_exit is None:
                return {"ok": True, "status": "already_running", "pid": self.child.pid}
            self.child_exit_code = int(observed_exit)
            self.child = None
            self.child_birth_identity = ""
            try:
                self._write_bridge_identity(state="stopped")
            except (OSError, TypeError, ValueError):
                return self._manual_failure(
                    "local_bridge_process_identity_write_failed"
                )
        reconciled = self.reconcile_prior_bridge()
        if not reconciled.get("ok"):
            return reconciled
        if self.process_owner is None or not bool(
            getattr(self.process_owner, "ready", False)
        ):
            return self._manual_failure(
                self.process_owner_error
                or "host_supervisor_process_owner_unavailable"
            )
        if automatic and not self._consume_restart_budget():
            self.manual_intervention_required = True
            self.last_error = "automatic_restart_budget_exhausted"
            self.runtime_errors.record("automatic_restart_budget_exhausted")
            return {
                "ok": False,
                "error": self.last_error,
                "manualInterventionRequired": True,
            }
        try:
            self._write_bridge_identity(state="starting")
        except (OSError, TypeError, ValueError):
            return self._manual_failure(
                "local_bridge_process_identity_write_failed"
            )
        self.bridge_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.bridge_log_path.open("a", encoding="utf-8")
        try:
            try:
                child = self.popen(
                    self._bridge_command(),
                    cwd=str(self.project_root),
                    env=self._bridge_environment(),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                try:
                    self._write_bridge_identity(state="stopped")
                except Exception:
                    pass
                self.runtime_errors.record("local_bridge_launch_failed", exc)
                return self._manual_failure("local_bridge_launch_failed")
        finally:
            log_handle.close()
        birth_identity = ""
        for _ in range(20):
            try:
                birth_identity = self.birth_identity_reader(int(child.pid)) or ""
            except (OSError, ValueError) as exc:
                self.runtime_errors.record(
                    "local_bridge_process_identity_unavailable",
                    exc,
                )
                break
            if birth_identity or child.poll() is not None:
                break
            self.sleep(0.01)
        if not birth_identity or not birth_identity_matches_current_platform(
            birth_identity
        ):
            stopped, _ = self._terminate_spawned_child(child)
            if not stopped:
                self.child = child
                self.child_started_at = self.now()
                return self._manual_failure(
                    "local_bridge_failed_spawn_stop_unverified"
                )
            try:
                self._write_bridge_identity(state="stopped")
            except Exception:
                pass
            return self._manual_failure(
                "local_bridge_process_identity_unavailable"
            )
        try:
            assigned = bool(self.process_owner.assign(child, birth_identity))
        except Exception as exc:
            self.runtime_errors.record(
                "local_bridge_process_owner_assignment_failed",
                exc,
            )
            assigned = False
        if not assigned:
            stopped, _ = self._terminate_spawned_child(child)
            if not stopped:
                self.child = child
                self.child_started_at = self.now()
                return self._manual_failure(
                    "local_bridge_failed_spawn_stop_unverified"
                )
            try:
                self._write_bridge_identity(state="stopped")
            except Exception:
                pass
            return self._manual_failure("local_bridge_process_owner_assignment_failed")
        try:
            self._write_bridge_identity(
                state="active",
                pid=int(child.pid),
                birth_identity=birth_identity,
            )
        except (OSError, TypeError, ValueError):
            stopped, _ = self._terminate_spawned_child(child)
            if not stopped:
                try:
                    self.process_owner.close()
                    child.wait(timeout=2)
                except Exception:
                    pass
                stopped = child.poll() is not None
            if not stopped:
                self.child = child
                self.child_started_at = self.now()
                self.child_birth_identity = birth_identity
                return self._manual_failure(
                    "local_bridge_failed_spawn_stop_unverified"
                )
            try:
                self._write_bridge_identity(state="stopped")
            except Exception:
                pass
            return self._manual_failure(
                "local_bridge_process_identity_write_failed"
            )
        self.child = child
        self.child_birth_identity = birth_identity
        self.child_started_at = self.now()
        self.bridge_status_instance_id = ""
        self.bridge_status_seq_high_water = 0
        self.child_exit_code = None
        self.manual_intervention_required = False
        self.last_error = ""
        return {"ok": True, "status": "started", "pid": self.child.pid}

    @staticmethod
    def _terminate_spawned_child(
        child: Any,
        *,
        graceful_timeout_sec: float = 5.0,
    ) -> tuple[bool, str]:
        """Stop the exact Popen handle; never rediscover or signal by PID."""

        if child.poll() is not None:
            return True, "already_exited"
        try:
            child.terminate()
            child.wait(timeout=max(0.1, graceful_timeout_sec))
            return child.poll() is not None, "terminated"
        except subprocess.TimeoutExpired:
            try:
                child.kill()
                child.wait(timeout=2)
            except Exception:
                return False, "kill_failed"
            return child.poll() is not None, "killed_after_timeout"
        except Exception:
            return False, "terminate_failed"

    def stop_bridge(self, *, graceful_timeout_sec: float = 5.0) -> dict[str, Any]:
        child = self.child
        if child is None:
            payload, load_error = self._load_bridge_identity()
            if payload is None and load_error.endswith("_invalid"):
                return self._manual_failure(load_error)
            if payload is not None and payload["state"] in {"active", "starting"}:
                # No in-memory Popen handle means this supervisor does not own
                # authority to declare the process stopped.  Reconcile the
                # exact durable identity (or fail closed) before transition.
                self._startup_reconciled = False
                reconciled = self.reconcile_prior_bridge()
                if not reconciled.get("ok"):
                    return reconciled
            elif payload is None and not self._startup_reconciled:
                reconciled = self.reconcile_prior_bridge()
                if not reconciled.get("ok"):
                    return reconciled
            self.child = None
            self.child_birth_identity = ""
            return {"ok": True, "status": "not_running"}
        if child.poll() is not None:
            self.child_exit_code = child.returncode
            self.child = None
            self.child_birth_identity = ""
            try:
                self._write_bridge_identity(state="stopped")
            except (OSError, TypeError, ValueError):
                return self._manual_failure(
                    "local_bridge_process_identity_write_failed"
                )
            return {
                "ok": True,
                "status": "already_exited",
                "exitCode": self.child_exit_code,
            }
        stopped, status = self._terminate_spawned_child(
            child,
            graceful_timeout_sec=graceful_timeout_sec,
        )
        if not stopped:
            return self._manual_failure("local_bridge_process_stop_unverified")
        try:
            self._write_bridge_identity(state="stopped")
        except (OSError, TypeError, ValueError):
            return self._manual_failure(
                "local_bridge_process_identity_write_failed"
            )
        self.child_exit_code = child.returncode
        self.child = None
        self.child_birth_identity = ""
        return {"ok": True, "status": status, "exitCode": self.child_exit_code}

    def restart_bridge(self) -> dict[str, Any]:
        stopped = self.stop_bridge()
        if not stopped.get("ok"):
            return {
                "ok": False,
                "stopped": stopped,
                "started": {
                    "ok": False,
                    "status": "not_attempted",
                    "error": "local_bridge_stop_required",
                },
            }
        started = self.start_bridge(automatic=False)
        return {"ok": bool(started.get("ok")), "stopped": stopped, "started": started}

    def _consume_restart_budget(self) -> bool:
        now = self.now()
        while self.restart_history and now - self.restart_history[0] > AUTO_RESTART_WINDOW_SEC:
            self.restart_history.popleft()
        if len(self.restart_history) >= AUTO_RESTART_LIMIT:
            return False
        self.restart_history.append(now)
        return True

    def _docker_action_command(self, action_id: str) -> list[str] | None:
        service_map = {
            "start_discord_bot": ("discord", "discord_bot"),
            "start_main_llm": ("llm", "main_llm"),
            "start_stt": ("stt", "stt"),
            "start_tts": ("tts", "tts"),
        }
        row = service_map.get(action_id)
        if row is None:
            return None
        profile, service = row
        return [
            "docker",
            "compose",
            "-f",
            str(self.project_root / "docker-compose.fast-control.yml"),
            "--profile",
            profile,
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            service,
        ]

    def _docker_action_environment(self, action_id: str) -> dict[str, str]:
        allowed = {"DISCORD_BOT_TOKEN"} if action_id == "start_discord_bot" else set()
        env = self._credential_scoped_environment(allowed_credentials=allowed)
        if action_id != "start_discord_bot":
            env["DISCORD_BOT_TOKEN"] = "local-only-disabled"
        return env

    def _restart_handoff_environment(self) -> dict[str, str]:
        return self._credential_scoped_environment(
            allowed_credentials={
                "DISCORD_BOT_TOKEN",
                "EVELYN_CODEX_CREDENTIALS_DIR",
            }
        )

    def _start_restart_handoff(self) -> dict[str, Any]:
        stop_script = (
            self.project_root
            / "evelyn_core"
            / "runtime"
            / "launchers"
            / "stop_evelyn_local.ps1"
        )
        start_script = self.project_root / "evelyn_core" / "start_local.bat"
        if not stop_script.is_file() or not start_script.is_file():
            return {"ok": False, "error": "local_restart_launcher_unavailable"}
        stop_literal = str(stop_script).replace("'", "''")
        start_literal = str(start_script).replace("'", "''")
        restart_script = (
            "$ErrorActionPreference = 'Continue'; "
            f"& '{stop_literal}' -DelayMs 200; "
            "Start-Sleep -Seconds 2; "
            f"& '{start_literal}' --background"
        )
        try:
            self.popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    restart_script,
                ],
                cwd=str(self.project_root),
                env=self._restart_handoff_environment(),
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                ),
            )
        except Exception as exc:
            self.runtime_errors.record("local_restart_handoff_failed", exc)
            return {"ok": False, "error": "local_restart_handoff_failed"}
        return {"ok": True, "status": "restart_handoff_started"}

    def _execute_action(self, action_id: str) -> dict[str, Any]:
        if action_id == "restart_local_bridge":
            return self.restart_bridge()
        command = self._docker_action_command(action_id)
        if command is None:
            return {"ok": False, "error": "unsupported_host_action"}
        try:
            completed = self.run_command(
                command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=self._docker_action_environment(action_id),
            )
        except Exception as exc:
            self.runtime_errors.record("host_action_launch_failed", exc)
            return {
                "ok": False,
                "error": "host_action_launch_failed",
                "detail": type(exc).__name__,
            }
        if completed.returncode != 0:
            self.runtime_errors.record("docker_compose_failed")
        return {
            "ok": completed.returncode == 0,
            "status": "started" if completed.returncode == 0 else "failed",
            "exitCode": int(completed.returncode),
            "error": None if completed.returncode == 0 else "docker_compose_failed",
        }

    def issue_preview_token(self, action_id: str) -> dict[str, Any]:
        if action_id not in ALLOWED_HOST_ACTIONS:
            return {"ok": False, "error": "unsupported_host_action", "actionId": action_id}
        now = self.now()
        self._tokens = {
            token: state
            for token, state in self._tokens.items()
            if not state.get("used") and float(state.get("expiresAt") or 0.0) >= now
        }
        token = secrets.token_urlsafe(32)
        expires_at = now + PREVIEW_TTL_SEC
        self._tokens[token] = {
            "actionId": action_id,
            "expiresAt": expires_at,
            "used": False,
        }
        return {
            "ok": True,
            "operation": "preview",
            "actionId": action_id,
            "previewToken": token,
            "expiresAt": expires_at,
            "requiresConfirm": True,
            "commandPreview": [action_id],
        }

    def apply_preview_token(self, action_id: str, token: str) -> dict[str, Any]:
        token_state = self._tokens.get(str(token or ""))
        if not token_state or token_state.get("actionId") != action_id:
            return {"ok": False, "error": "preview_token_invalid", "actionId": action_id}
        if token_state.get("used"):
            return {"ok": False, "error": "preview_token_reused", "actionId": action_id}
        if self.now() > float(token_state.get("expiresAt") or 0.0):
            self._tokens.pop(token, None)
            return {"ok": False, "error": "preview_token_expired", "actionId": action_id}
        token_state["used"] = True
        result = self._execute_action(action_id)
        self.last_action = {
            "actionId": action_id,
            "at": self.now(),
            "ok": bool(result.get("ok")),
            "error": result.get("error"),
        }
        return {**result, "actionId": action_id, "operation": "apply"}

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("requestId") or "")
        base = {
            "schema": SUPERVISOR_RESPONSE_SCHEMA,
            "requestId": request_id if _REQUEST_ID_PATTERN.fullmatch(request_id) else "",
            "respondedAt": self.now(),
        }
        allowed_keys = {
            "schema",
            "requestId",
            "operation",
            "actionId",
            "previewToken",
            "requestedAt",
        }
        unexpected = sorted(set(request) - allowed_keys)
        if unexpected:
            return {**base, "ok": False, "error": "unexpected_request_fields", "fields": unexpected}
        if request.get("schema") != SUPERVISOR_REQUEST_SCHEMA:
            return {**base, "ok": False, "error": "invalid_request_schema"}
        if not _REQUEST_ID_PATTERN.fullmatch(request_id):
            return {**base, "ok": False, "error": "invalid_request_id"}
        action_id = str(request.get("actionId") or "")
        if action_id not in ALLOWED_HOST_ACTIONS:
            return {**base, "ok": False, "error": "unsupported_host_action", "actionId": action_id}
        operation = str(request.get("operation") or "")
        if operation == "preview":
            return {**base, **self.issue_preview_token(action_id)}
        if operation == "apply":
            return {
                **base,
                **self.apply_preview_token(
                    action_id,
                    str(request.get("previewToken") or ""),
                ),
            }
        return {**base, "ok": False, "error": "unsupported_operation", "actionId": action_id}

    def process_request_queue(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        for request_path in sorted(self.requests_dir.glob("*.json")):
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
                if not isinstance(request, dict):
                    request = {}
            except (OSError, ValueError, TypeError):
                request = {}
            requested_id = str(request.get("requestId") or "")
            request_id = (
                requested_id
                if _REQUEST_ID_PATTERN.fullmatch(requested_id)
                else request_path.stem
                if _REQUEST_ID_PATTERN.fullmatch(request_path.stem)
                else uuid.uuid4().hex
            )
            response = self.handle_request(request)
            atomic_json_write(self.responses_dir / f"{request_id}.json", response)
            try:
                request_path.unlink()
            except OSError:
                pass

    def status(self) -> dict[str, Any]:
        child_running = bool(self.child is not None and self.child.poll() is None)
        owner_ready = bool(
            self.process_owner is not None
            and getattr(self.process_owner, "ready", False)
        )
        return {
            "schema": SUPERVISOR_STATUS_SCHEMA,
            "heartbeatAt": self.now(),
            "startedAt": self.started_at,
            "pid": os.getpid(),
            "state": "stopping"
            if self._stopping
            else "manual_intervention_required"
            if self.manual_intervention_required
            else "running",
            "manualInterventionRequired": self.manual_intervention_required,
            "lastError": self.last_error,
            "localBridge": {
                "running": child_running,
                "pid": self.child.pid if child_running else None,
                "startedAt": self.child_started_at,
                "lastExitCode": self.child_exit_code,
                "automaticRestartsInWindow": len(self.restart_history),
                "automaticRestartLimit": AUTO_RESTART_LIMIT,
                "ownershipMode": str(
                    getattr(
                        self.process_owner,
                        "mode",
                        "unavailable",
                    )
                ),
                "ownershipReady": bool(
                    owner_ready
                    and self._startup_reconciled
                    and (not child_running or bool(self.child_birth_identity))
                ),
                "birthIdentityRecorded": bool(
                    child_running and self.child_birth_identity
                ),
                "processIdentityState": self.bridge_identity_state,
                "voiceCaptureStop": self._voice_capture_stop_evidence(),
            },
            "lastAction": dict(self.last_action),
            "allowedActions": sorted(ALLOWED_HOST_ACTIONS),
            "storageRetention": self.retention_reporter.status(),
            **self.runtime_errors.snapshot(),
        }

    def write_status(self) -> None:
        recovering_heartbeat_error = self.last_error.startswith(
            "heartbeat_write_failed:"
        )
        payload = self.status()
        if recovering_heartbeat_error:
            payload["lastError"] = ""
        atomic_json_write(self.status_path, payload)
        if recovering_heartbeat_error and self.last_error.startswith(
            "heartbeat_write_failed:"
        ):
            self.last_error = ""

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(HEARTBEAT_INTERVAL_SEC):
            try:
                self.write_status()
            except Exception as exc:
                self.last_error = f"heartbeat_write_failed:{type(exc).__name__}"
                self.runtime_errors.record("heartbeat_write_failed", exc)

    def _observe_child(self) -> None:
        if self.child is None:
            return
        exit_code = self.child.poll()
        if exit_code is None:
            return
        self.child_exit_code = int(exit_code)
        self.child = None
        self.child_birth_identity = ""
        try:
            self._write_bridge_identity(state="stopped")
        except (OSError, TypeError, ValueError):
            self.manual_intervention_required = True
            self.last_error = "local_bridge_process_identity_write_failed"
            self.runtime_errors.record(self.last_error)
            return
        if self._stopping:
            return
        if self.child_exit_code == LOCAL_BRIDGE_RESTART_EXIT_CODE:
            result = self._start_restart_handoff()
            self.last_action = {
                "actionId": "restart_evelyn_local",
                "at": self.now(),
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
            }
            if result.get("ok"):
                self._stopping = True
            else:
                self.manual_intervention_required = True
                self.last_error = str(
                    result.get("error") or "local_restart_handoff_failed"
                )
            return
        self.runtime_errors.record("local_bridge_unexpected_exit")
        validation_context = active_validation_context(
            surface="local",
            root=self.artifacts_root,
            now=self.now,
        )
        if validation_context is not None:
            self.manual_intervention_required = True
            self.last_error = "validation_active_local_bridge_exit"
            self.runtime_errors.record(self.last_error)
            try:
                emit_voice_validation_event(
                    "local",
                    "error",
                    root=self.artifacts_root,
                    session_id=str(validation_context.get("sessionId") or ""),
                    step_id=str(validation_context.get("stepId") or ""),
                    attempt_id=str(validation_context.get("attemptId") or ""),
                    now=self.now,
                    errorCode="local_bridge_unexpected_exit",
                )
            except Exception as exc:
                self.runtime_errors.record(
                    "validation_bridge_exit_event_write_failed",
                    exc,
                )
            return
        result = self.start_bridge(automatic=True)
        if not result.get("ok"):
            self.manual_intervention_required = True

    def request_stop(self) -> None:
        self._stopping = True

    def run(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.stop_request_path.unlink()
        except OSError:
            pass
        self.start_bridge()
        try:
            self.retention_reporter.start()
            self.write_status()
            self._heartbeat_stop.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="evelyn-host-supervisor-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()
            while not self._stopping:
                if self.stop_request_path.exists():
                    self._stopping = True
                    break
                self.process_request_queue()
                self._observe_child()
                time.sleep(0.1)
        finally:
            self._heartbeat_stop.set()
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=2.0)
            try:
                self.retention_reporter.stop()
            except Exception as exc:
                self.runtime_errors.record("retention_reporter_stop_failed", exc)
                self.last_error = (
                    f"retention_reporter_stop_failed:{type(exc).__name__}"
                )
            self.stop_bridge()
            if self.process_owner is not None:
                try:
                    self.process_owner.close()
                except Exception as exc:
                    self.runtime_errors.record(
                        "host_supervisor_process_owner_close_failed",
                        exc,
                    )
                    self.last_error = (
                        "host_supervisor_process_owner_close_failed"
                    )
            self.write_status()
        return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Evelyn Windows Host Supervisor.")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--artifacts-root", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    supervisor = HostSupervisor(
        project_root=args.project_root,
        artifacts_root=args.artifacts_root,
    )
    signal.signal(signal.SIGINT, lambda *_: supervisor.request_stop())
    signal.signal(signal.SIGTERM, lambda *_: supervisor.request_stop())
    return supervisor.run()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BRIDGE_PROCESS_IDENTITY_SCHEMA",
    "HostSupervisor",
    "main",
]
