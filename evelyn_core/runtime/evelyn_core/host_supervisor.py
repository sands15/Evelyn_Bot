from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .host_supervisor_client import (
    ALLOWED_HOST_ACTIONS,
    SUPERVISOR_REQUEST_SCHEMA,
    SUPERVISOR_RESPONSE_SCHEMA,
    SUPERVISOR_STATUS_SCHEMA,
)
from .paths import get_repo_root, get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write


PREVIEW_TTL_SEC = 120.0
AUTO_RESTART_LIMIT = 3
AUTO_RESTART_WINDOW_SEC = 10 * 60
HEARTBEAT_INTERVAL_SEC = 1.0
_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")


class HostSupervisor:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        artifacts_root: Path | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        run_command: Callable[..., Any] = subprocess.run,
        now: Callable[[], float] = time.time,
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
        self.bridge_log_path = self.artifacts_root / "logs" / "Local-IO-Bridge.log"
        self.popen = popen
        self.run_command = run_command
        self.now = now
        self.started_at = self.now()
        self.child: Any | None = None
        self.child_started_at: float | None = None
        self.child_exit_code: int | None = None
        self.restart_history: deque[float] = deque()
        self.manual_intervention_required = False
        self.last_error = ""
        self.last_action: dict[str, Any] = {}
        self._tokens: dict[str, dict[str, Any]] = {}
        self._stopping = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    def _bridge_command(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "evelyn_core.local_io_bridge",
            "--project-root",
            str(self.project_root),
        ]

    def _bridge_environment(self) -> dict[str, str]:
        env = dict(os.environ)
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

    def start_bridge(self, *, automatic: bool = False) -> dict[str, Any]:
        if self.child is not None and self.child.poll() is None:
            return {"ok": True, "status": "already_running", "pid": self.child.pid}
        if automatic and not self._consume_restart_budget():
            self.manual_intervention_required = True
            self.last_error = "automatic_restart_budget_exhausted"
            return {
                "ok": False,
                "error": self.last_error,
                "manualInterventionRequired": True,
            }
        self.bridge_log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.bridge_log_path.open("a", encoding="utf-8")
        try:
            self.child = self.popen(
                self._bridge_command(),
                cwd=str(self.project_root),
                env=self._bridge_environment(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log_handle.close()
        self.child_started_at = self.now()
        self.child_exit_code = None
        self.manual_intervention_required = False
        self.last_error = ""
        return {"ok": True, "status": "started", "pid": self.child.pid}

    def stop_bridge(self, *, graceful_timeout_sec: float = 5.0) -> dict[str, Any]:
        child = self.child
        if child is None or child.poll() is not None:
            self.child = None
            return {"ok": True, "status": "not_running"}
        child.terminate()
        try:
            child.wait(timeout=max(0.1, graceful_timeout_sec))
            status = "terminated"
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
            status = "killed_after_timeout"
        self.child_exit_code = child.returncode
        self.child = None
        return {"ok": True, "status": status, "exitCode": self.child_exit_code}

    def restart_bridge(self) -> dict[str, Any]:
        stopped = self.stop_bridge()
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
            service,
        ]

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
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": "host_action_launch_failed",
                "detail": type(exc).__name__,
            }
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
            },
            "lastAction": dict(self.last_action),
            "allowedActions": sorted(ALLOWED_HOST_ACTIONS),
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

    def _observe_child(self) -> None:
        if self.child is None:
            return
        exit_code = self.child.poll()
        if exit_code is None:
            return
        self.child_exit_code = int(exit_code)
        self.child = None
        if self._stopping:
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
        self.write_status()
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="evelyn-host-supervisor-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
        try:
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
            self.stop_bridge()
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


__all__ = ["HostSupervisor", "main"]
