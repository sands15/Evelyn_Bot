from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.host_supervisor import (  # noqa: E402
    BRIDGE_PROCESS_IDENTITY_SCHEMA,
    BRIDGE_STATUS_MAX_BYTES,
    HostSupervisor,
    LOCAL_BRIDGE_RESTART_EXIT_CODE,
    VOICE_CAPTURE_STOP_SCHEMA,
)
import evelyn_core.host_supervisor as host_supervisor_module  # noqa: E402
from evelyn_core.host_supervisor_client import SUPERVISOR_REQUEST_SCHEMA  # noqa: E402
from evelyn_core.voice_capture_consent import (  # noqa: E402
    BRIDGE_STATUS_AUTH_SCOPE,
    SUPERVISOR_STOP_AUTH_SCOPE,
    VOICE_CAPTURE_AUTH_ENV,
    sign_voice_capture_artifact,
    voice_capture_artifact_is_authentic,
)
from evelyn_core.voice_validation import SUITE_ID, VoiceValidationManager  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class FakeProcess:
    pid = 1234

    def __init__(self) -> None:
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = 1


class UnstoppableFakeProcess(FakeProcess):
    def terminate(self):
        raise OSError("cannot stop")

    def kill(self):
        raise OSError("cannot kill")


class FakeProcessOwner:
    mode = "windows_job_kill_on_close"

    def __init__(self, *, assign_result: bool = True) -> None:
        self.ready = True
        self.assign_result = assign_result
        self.assignments: list[tuple[int, str]] = []
        self.closed = False

    def assign(self, process, birth_identity: str) -> bool:
        self.assignments.append((int(process.pid), birth_identity))
        return self.assign_result

    def close(self) -> None:
        self.closed = True
        self.ready = False


class FakeRetentionReporter:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def status(self):
        return {
            "state": "clear",
            "dryRun": True,
            "automaticDeletion": False,
        }


class HostSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.clock = FakeClock()
        self.commands = []
        self.birth_identity = (
            "windows:123456" if os.name == "nt" else "linux:123456"
        )
        self.process_owner = FakeProcessOwner()
        self.voice_capture_auth_token = (
            "voice-capture-test-auth-token-0123456789"
        )

        def run(command, **kwargs):
            self.commands.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        self.supervisor = HostSupervisor(
            project_root=root,
            artifacts_root=root / "runtime_artifacts",
            run_command=run,
            now=self.clock,
            birth_identity_reader=lambda pid: (
                self.birth_identity if pid == FakeProcess.pid else None
            ),
            exact_process_terminator=lambda _pid, _birth: True,
            process_owner=self.process_owner,
            bridge_lock_probe=lambda: True,
            voice_capture_auth_token=self.voice_capture_auth_token,
        )

    def request(self, **overrides):
        payload = {
            "schema": SUPERVISOR_REQUEST_SCHEMA,
            "requestId": "request-1",
            "operation": "preview",
            "actionId": "start_tts",
            "previewToken": "",
            "requestedAt": self.clock(),
        }
        payload.update(overrides)
        return payload

    def test_arbitrary_command_fields_are_rejected(self):
        response = self.supervisor.handle_request(
            self.request(command="powershell.exe", argv=["-Command", "whoami"], cwd="C:\\")
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "unexpected_request_fields")

    def test_only_allowlisted_action_ids_are_accepted(self):
        response = self.supervisor.handle_request(self.request(actionId="run_anything"))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "unsupported_host_action")

    def test_request_id_cannot_escape_response_directory(self):
        request_path = self.supervisor.requests_dir / "safe-request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(
            json.dumps(self.request(requestId="../escaped")),
            encoding="utf-8",
        )

        self.supervisor.process_request_queue()

        self.assertTrue((self.supervisor.responses_dir / "safe-request.json").exists())
        self.assertFalse((self.supervisor.root / "escaped.json").exists())

    def test_preview_token_is_single_use(self):
        preview = self.supervisor.handle_request(self.request())
        token = preview["previewToken"]
        first = self.supervisor.handle_request(
            self.request(operation="apply", previewToken=token)
        )
        second = self.supervisor.handle_request(
            self.request(operation="apply", previewToken=token)
        )
        self.assertTrue(first["ok"])
        self.assertEqual(self.commands[0][0][-1], "tts")
        self.assertFalse(second["ok"])
        self.assertEqual(second["error"], "preview_token_reused")

    def test_host_actions_do_not_recreate_dependencies_or_leak_credentials(self):
        credentials = {
            "LOCAL_BRIDGE_STATUS_AUTH_TOKEN": "reporter-secret",
            "EVELYN_INTERNAL_CONTROL_TOKEN": "internal-secret",
            VOICE_CAPTURE_AUTH_ENV: self.voice_capture_auth_token,
            "DISCORD_BOT_TOKEN": "discord-secret",
            "OPENAI_API_KEY": "model-secret",
        }
        with patch.dict(os.environ, credentials, clear=False):
            bridge_env = self.supervisor._bridge_environment()
            result = self.supervisor._execute_action("start_discord_bot")
            tts_env = self.supervisor._docker_action_environment("start_tts")

        self.assertTrue(result["ok"], result)
        command, command_options = self.commands[-1]
        discord_env = command_options["env"]
        self.assertEqual(command[-2:], ["--no-deps", "discord_bot"])
        self.assertIn("--no-build", command)
        self.assertEqual(
            bridge_env["LOCAL_BRIDGE_STATUS_AUTH_TOKEN"],
            "reporter-secret",
        )
        self.assertEqual(
            bridge_env[VOICE_CAPTURE_AUTH_ENV],
            self.voice_capture_auth_token,
        )
        self.assertNotIn("EVELYN_INTERNAL_CONTROL_TOKEN", bridge_env)
        self.assertNotIn("DISCORD_BOT_TOKEN", bridge_env)
        self.assertNotIn("OPENAI_API_KEY", bridge_env)
        self.assertEqual(discord_env["DISCORD_BOT_TOKEN"], "discord-secret")
        self.assertNotIn("LOCAL_BRIDGE_STATUS_AUTH_TOKEN", discord_env)
        self.assertNotIn("EVELYN_INTERNAL_CONTROL_TOKEN", discord_env)
        self.assertNotIn(VOICE_CAPTURE_AUTH_ENV, discord_env)
        self.assertNotIn("OPENAI_API_KEY", discord_env)
        self.assertEqual(
            tts_env["DISCORD_BOT_TOKEN"],
            "local-only-disabled",
        )

    def test_preview_token_expires_after_two_minutes(self):
        preview = self.supervisor.handle_request(self.request())
        self.clock.value += 121
        response = self.supervisor.handle_request(
            self.request(operation="apply", previewToken=preview["previewToken"])
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"], "preview_token_expired")

    def test_automatic_restart_budget_is_three_per_ten_minutes(self):
        self.assertTrue(self.supervisor._consume_restart_budget())
        self.assertTrue(self.supervisor._consume_restart_budget())
        self.assertTrue(self.supervisor._consume_restart_budget())
        self.assertFalse(self.supervisor._consume_restart_budget())
        self.clock.value += 601
        self.assertTrue(self.supervisor._consume_restart_budget())

    def test_unexpected_bridge_exit_does_not_restart_during_validation(self):
        manager = VoiceValidationManager(
            root=self.supervisor.artifacts_root,
            now=self.clock,
        )
        started = manager.start(
            suite=SUITE_ID,
            surfaces=("local",),
            capabilities={
                "voiceLocal": {"state": "ready", "ready": True, "blockers": []},
                "voiceDiscord": {"state": "ready", "ready": True, "blockers": []},
            },
        )
        self.assertTrue(started["ok"], started)
        session_id = started["session"]["sessionId"]
        failed_child = FakeProcess()
        failed_child.returncode = 9
        self.supervisor.child = failed_child
        starts: list[bool] = []
        self.supervisor.start_bridge = lambda *, automatic=False: (
            starts.append(automatic) or {"ok": True}
        )

        self.supervisor._observe_child()

        self.assertEqual(starts, [])
        self.assertTrue(self.supervisor.manual_intervention_required)
        self.assertEqual(
            self.supervisor.last_error,
            "validation_active_local_bridge_exit",
        )
        event_path = (
            self.supervisor.artifacts_root
            / "voice_validation"
            / "events"
            / f"{session_id}.jsonl"
        )
        event = json.loads(event_path.read_text(encoding="utf-8"))
        self.assertEqual(event["event"], "error")
        self.assertEqual(event["errorCode"], "local_bridge_unexpected_exit")
        failed_session = manager.snapshot()
        self.assertEqual(failed_session["currentStep"]["status"], "failed")
        self.assertIn(
            "local_bridge_unexpected_exit",
            failed_session["currentStep"]["errors"],
        )

    def test_unexpected_bridge_exit_restarts_when_validation_is_inactive(self):
        failed_child = FakeProcess()
        failed_child.returncode = 9
        self.supervisor.child = failed_child
        starts: list[bool] = []
        self.supervisor.start_bridge = lambda *, automatic=False: (
            starts.append(automatic) or {"ok": True}
        )

        self.supervisor._observe_child()

        self.assertEqual(starts, [True])
        self.assertFalse(self.supervisor.manual_intervention_required)

    def test_restart_exit_uses_supervisor_owned_credential_handoff(self):
        stop_script = (
            self.supervisor.project_root
            / "evelyn_core"
            / "runtime"
            / "launchers"
            / "stop_evelyn_local.ps1"
        )
        start_script = (
            self.supervisor.project_root / "evelyn_core" / "start_local.bat"
        )
        stop_script.parent.mkdir(parents=True, exist_ok=True)
        stop_script.write_text("# test", encoding="utf-8")
        start_script.write_text("@rem test", encoding="utf-8")
        launches: list[tuple[list[str], dict[str, object]]] = []
        self.supervisor.popen = lambda command, **kwargs: (
            launches.append((command, kwargs)) or FakeProcess()
        )
        child = FakeProcess()
        child.returncode = LOCAL_BRIDGE_RESTART_EXIT_CODE
        self.supervisor.child = child
        credentials = {
            "LOCAL_BRIDGE_STATUS_AUTH_TOKEN": "reporter-secret",
            "EVELYN_INTERNAL_CONTROL_TOKEN": "internal-secret",
            VOICE_CAPTURE_AUTH_ENV: self.voice_capture_auth_token,
            "DISCORD_BOT_TOKEN": "discord-secret",
            "EVELYN_CODEX_CREDENTIALS_DIR": "C:\\secure-codex",
            "OPENAI_API_KEY": "model-secret",
        }

        with patch.dict(os.environ, credentials, clear=False):
            self.supervisor._observe_child()

        self.assertTrue(self.supervisor._stopping)
        self.assertEqual(len(launches), 1)
        command, options = launches[0]
        environment = options["env"]
        self.assertEqual(environment["DISCORD_BOT_TOKEN"], "discord-secret")
        self.assertEqual(
            environment["EVELYN_CODEX_CREDENTIALS_DIR"],
            "C:\\secure-codex",
        )
        self.assertNotIn("LOCAL_BRIDGE_STATUS_AUTH_TOKEN", environment)
        self.assertNotIn("EVELYN_INTERNAL_CONTROL_TOKEN", environment)
        self.assertNotIn(VOICE_CAPTURE_AUTH_ENV, environment)
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertIn(str(stop_script), command[-1])
        self.assertIn(str(start_script), command[-1])
        self.assertTrue(self.supervisor.last_action["ok"])

    def test_wrong_schema_active_file_does_not_suppress_bridge_restart(self):
        manager = VoiceValidationManager(
            root=self.supervisor.artifacts_root,
            now=self.clock,
        )
        started = manager.start(
            suite=SUITE_ID,
            surfaces=("local",),
            capabilities={
                "voiceLocal": {"state": "ready", "ready": True, "blockers": []},
                "voiceDiscord": {"state": "ready", "ready": True, "blockers": []},
            },
        )
        self.assertTrue(started["ok"], started)
        active_path = (
            self.supervisor.artifacts_root
            / "voice_validation"
            / "active.json"
        )
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["schema"] = "voice_validation.session.v0"
        active_path.write_text(json.dumps(active), encoding="utf-8")
        failed_child = FakeProcess()
        failed_child.returncode = 9
        self.supervisor.child = failed_child
        starts: list[bool] = []
        self.supervisor.start_bridge = lambda *, automatic=False: (
            starts.append(automatic) or {"ok": True}
        )

        self.supervisor._observe_child()

        self.assertEqual(starts, [True])
        self.assertFalse(self.supervisor.manual_intervention_required)

    def test_successful_status_write_clears_transient_heartbeat_error(self):
        self.supervisor.last_error = "heartbeat_write_failed:PermissionError"

        self.supervisor.write_status()

        payload = json.loads(
            self.supervisor.status_path.read_text(encoding="utf-8")
        )
        self.assertEqual(payload["lastError"], "")
        self.assertEqual(self.supervisor.last_error, "")

    def test_status_exposes_dry_run_retention_policy(self):
        payload = self.supervisor.status()

        self.assertTrue(payload["storageRetention"]["dryRun"])
        self.assertFalse(payload["storageRetention"]["automaticDeletion"])

    def bridge_watchdog_status(self) -> dict:
        return self.sign_bridge_status({
            "schema": "local_io_bridge.status.v1",
            "statusSeq": 7,
            "heartbeatAt": self.clock(),
            "pid": FakeProcess.pid,
            "bridgeInstanceId": "a" * 32,
            "micEnabled": False,
            "micCaptureStopped": True,
            "mic": {
                "enabled": False,
                "captureReady": False,
                "captureActive": False,
                "captureStopped": True,
            },
            "voiceCaptureWatchdog": {
                "schema": "voice.capture-consent.watchdog-status.v1",
                "state": "blocked",
                "reason": "voice_capture_consent_heartbeat_stale",
                "checkedAt": self.clock() - 0.1,
                "captureStopped": True,
                "stoppedAt": self.clock() - 0.05,
                "contentFree": True,
            },
        })

    def sign_bridge_status(self, payload: dict) -> dict:
        return sign_voice_capture_artifact(
            payload,
            auth_scope=BRIDGE_STATUS_AUTH_SCOPE,
            auth_token=self.voice_capture_auth_token,
        )

    def write_bridge_watchdog_status(self, payload: dict) -> None:
        self.supervisor.child = FakeProcess()
        self.supervisor.child_started_at = self.clock() - 1
        self.supervisor.bridge_status_path.parent.mkdir(parents=True, exist_ok=True)
        self.supervisor.bridge_status_path.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def test_status_publishes_only_child_bound_exact_capture_stop_evidence(self):
        self.write_bridge_watchdog_status(self.bridge_watchdog_status())

        evidence = self.supervisor.status()["localBridge"]["voiceCaptureStop"]
        serialized = json.dumps(evidence)

        self.assertEqual(evidence["schema"], VOICE_CAPTURE_STOP_SCHEMA)
        self.assertEqual(evidence["state"], "verified")
        self.assertTrue(evidence["captureStopped"])
        self.assertFalse(evidence["micEnabled"])
        self.assertTrue(
            voice_capture_artifact_is_authentic(
                evidence,
                auth_scope=SUPERVISOR_STOP_AUTH_SCOPE,
                auth_token=self.voice_capture_auth_token,
            )
        )
        self.assertNotIn("owner", serialized.lower())
        self.assertNotIn("lease", serialized.lower())
        self.assertNotIn(self.voice_capture_auth_token, serialized)

    def test_capture_stop_evidence_rejects_stale_or_contradictory_bridge_status(self):
        base = self.bridge_watchdog_status()
        cases = {
            "stale": {**base, "heartbeatAt": self.clock() - 4},
            "wrong_pid": {**base, "pid": 9999},
            "top_level_on": {**base, "micEnabled": True},
            "nested_ready": {
                **base,
                "mic": {**base["mic"], "captureReady": True},
            },
            "prior_child_generation": {
                **base,
                "heartbeatAt": self.clock() - 2,
            },
            "malformed_stopped_at": {
                **base,
                "voiceCaptureWatchdog": {
                    **base["voiceCaptureWatchdog"],
                    "stoppedAt": "not-a-time",
                },
            },
            "stop_failed": {
                **base,
                "voiceCaptureWatchdog": {
                    **base["voiceCaptureWatchdog"],
                    "state": "stop_failed",
                    "stoppedAt": None,
                },
            },
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                self.write_bridge_watchdog_status(
                    self.sign_bridge_status(payload)
                )
                evidence = self.supervisor.status()["localBridge"][
                    "voiceCaptureStop"
                ]
                self.assertEqual(evidence["state"], "unverified")

    def test_capture_stop_not_required_still_requires_exact_physical_off(self):
        base = self.bridge_watchdog_status()
        payload = {
            **base,
            "mic": {**base["mic"], "captureActive": True},
            "voiceCaptureWatchdog": {
                **base["voiceCaptureWatchdog"],
                "stoppedAt": None,
            },
        }
        self.write_bridge_watchdog_status(self.sign_bridge_status(payload))

        evidence = self.supervisor.status()["localBridge"]["voiceCaptureStop"]

        self.assertEqual(evidence["state"], "unverified")

    def test_capture_stop_evidence_rejects_signed_status_tampering(self):
        payload = {**self.bridge_watchdog_status(), "statusSeq": 8}
        self.write_bridge_watchdog_status(payload)

        evidence = self.supervisor.status()["localBridge"]["voiceCaptureStop"]

        self.assertEqual(evidence["state"], "unverified")

    def test_capture_stop_evidence_rejects_signed_sequence_replay(self):
        replay = self.bridge_watchdog_status()
        current = self.sign_bridge_status({**replay, "statusSeq": 8})
        self.write_bridge_watchdog_status(current)

        evidence = self.supervisor.status()["localBridge"]["voiceCaptureStop"]
        self.assertEqual(evidence["state"], "verified")
        self.assertEqual(self.supervisor.bridge_status_seq_high_water, 8)

        self.supervisor.bridge_status_path.write_text(
            json.dumps(replay),
            encoding="utf-8",
        )
        replayed = self.supervisor.status()["localBridge"]["voiceCaptureStop"]

        self.assertEqual(replayed["state"], "unverified")
        self.assertEqual(self.supervisor.bridge_status_seq_high_water, 8)

    def test_capture_stop_evidence_rejects_new_signed_instance_for_same_child(self):
        current = self.bridge_watchdog_status()
        self.write_bridge_watchdog_status(current)

        evidence = self.supervisor.status()["localBridge"]["voiceCaptureStop"]
        self.assertEqual(evidence["state"], "verified")
        self.assertEqual(self.supervisor.bridge_status_instance_id, "a" * 32)

        replacement = self.sign_bridge_status(
            {
                **current,
                "statusSeq": 8,
                "bridgeInstanceId": "b" * 32,
            }
        )
        self.supervisor.bridge_status_path.write_text(
            json.dumps(replacement),
            encoding="utf-8",
        )
        replaced = self.supervisor.status()["localBridge"]["voiceCaptureStop"]

        self.assertEqual(replaced["state"], "unverified")
        self.assertEqual(self.supervisor.bridge_status_instance_id, "a" * 32)

    def test_successful_bridge_start_resets_status_instance_and_sequence(self):
        self.supervisor.bridge_status_instance_id = "a" * 32
        self.supervisor.bridge_status_seq_high_water = 8
        self.supervisor.popen = lambda *_args, **_kwargs: FakeProcess()

        result = self.supervisor.start_bridge()

        self.assertTrue(result["ok"], result)
        self.assertEqual(self.supervisor.bridge_status_instance_id, "")
        self.assertEqual(self.supervisor.bridge_status_seq_high_water, 0)

    @unittest.skipUnless(os.name == "nt", "Windows venv launcher contract")
    def test_windows_bridge_command_directly_owns_base_python(self):
        runtime_root = Path(self.temp_dir.name) / "host-runtime"
        base_python = runtime_root / "base-python.exe"
        venv_python = runtime_root / "Scripts" / "python.exe"
        site_packages = runtime_root / "Lib" / "site-packages"
        base_python.parent.mkdir(parents=True, exist_ok=True)
        base_python.touch()
        venv_python.parent.mkdir(parents=True, exist_ok=True)
        venv_python.touch()
        site_packages.mkdir(parents=True, exist_ok=True)

        with (
            patch.object(host_supervisor_module.sys, "executable", str(venv_python)),
            patch.object(host_supervisor_module.sys, "_base_executable", str(base_python)),
            patch.object(host_supervisor_module.sys, "prefix", str(runtime_root)),
        ):
            command = self.supervisor._bridge_command()

        self.assertEqual(command[0], str(base_python))
        self.assertEqual(command[1], "-c")
        self.assertIn("site.addsitedir", command[2])
        self.assertIn("runpy.run_module('evelyn_core.local_io_bridge'", command[2])
        self.assertEqual(command[-2:], ["--project-root", str(self.supervisor.project_root)])

    def test_capture_stop_evidence_bounds_untrusted_status_file(self):
        self.supervisor.child = FakeProcess()
        self.supervisor.child_started_at = self.clock() - 1
        self.supervisor.bridge_status_path.parent.mkdir(parents=True, exist_ok=True)
        self.supervisor.bridge_status_path.write_bytes(
            b"{" + b" " * BRIDGE_STATUS_MAX_BYTES
        )

        evidence = self.supervisor.status()["localBridge"]["voiceCaptureStop"]

        self.assertEqual(evidence["state"], "unverified")

    def test_failed_host_action_updates_error_counter_without_command_text(self):
        self.supervisor.run_command = lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="private output",
            stderr="C:\\private\\token",
        )

        result = self.supervisor._execute_action("start_tts")
        payload = self.supervisor.status()

        self.assertFalse(result["ok"])
        self.assertEqual(payload["errorCount"], 1)
        self.assertEqual(payload["lastErrorCode"], "docker_compose_failed")
        self.assertEqual(payload["errorCounters"], {"docker_compose_failed": 1})
        self.assertNotIn("private", json.dumps(payload))
        self.assertNotIn("token", json.dumps(payload))

    def test_invalid_request_and_filename_use_a_safe_generated_response_id(self):
        request_path = self.supervisor.requests_dir / "bad.name.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text("{", encoding="utf-8")

        self.supervisor.process_request_queue()

        responses = list(self.supervisor.responses_dir.glob("*.json"))
        self.assertEqual(len(responses), 1)
        self.assertRegex(responses[0].stem, r"^[0-9a-f]{32}$")

    def test_run_starts_and_stops_retention_reporter(self):
        reporter = FakeRetentionReporter()
        supervisor = HostSupervisor(
            project_root=self.supervisor.project_root,
            artifacts_root=self.supervisor.artifacts_root,
            popen=lambda *args, **kwargs: FakeProcess(),
            run_command=self.supervisor.run_command,
            now=self.clock,
            retention_reporter=reporter,
            birth_identity_reader=lambda _pid: self.birth_identity,
            exact_process_terminator=lambda _pid, _birth: True,
            process_owner=self.process_owner,
            bridge_lock_probe=lambda: True,
        )
        supervisor.request_stop()

        exit_code = supervisor.run()

        self.assertEqual(exit_code, 0)
        self.assertTrue(reporter.started)
        self.assertTrue(reporter.stopped)
        self.assertTrue(self.process_owner.closed)

    def write_prior_identity(
        self,
        *,
        state: str,
        pid: int = 0,
        birth_identity: str = "",
    ) -> None:
        self.supervisor.bridge_identity_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.supervisor.bridge_identity_path.write_text(
            json.dumps(
                {
                    "schema": BRIDGE_PROCESS_IDENTITY_SCHEMA,
                    "state": state,
                    "pid": pid,
                    "birthIdentity": birth_identity,
                    "updatedAt": self.clock(),
                    "contentFree": True,
                }
            ),
            encoding="utf-8",
        )

    def test_startup_reconciles_exact_orphan_from_crashed_supervisor(self):
        prior_pid = 7788
        self.write_prior_identity(
            state="active",
            pid=prior_pid,
            birth_identity=self.birth_identity,
        )
        observations = [self.birth_identity, None]
        terminated: list[tuple[int, str]] = []
        self.supervisor.birth_identity_reader = lambda _pid: observations.pop(0)
        self.supervisor.exact_process_terminator = (
            lambda pid, birth: terminated.append((pid, birth)) or True
        )

        result = self.supervisor.reconcile_prior_bridge()

        self.assertTrue(result["ok"], result)
        self.assertEqual(terminated, [(prior_pid, self.birth_identity)])
        persisted = json.loads(
            self.supervisor.bridge_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "stopped")
        self.assertEqual(persisted["pid"], 0)

    def test_startup_never_signals_a_reused_pid(self):
        prior_pid = 7788
        reused_identity = (
            "windows:999999" if os.name == "nt" else "linux:999999"
        )
        self.write_prior_identity(
            state="active",
            pid=prior_pid,
            birth_identity=self.birth_identity,
        )
        terminate_calls: list[tuple[int, str]] = []
        self.supervisor.birth_identity_reader = lambda _pid: reused_identity
        self.supervisor.exact_process_terminator = (
            lambda pid, birth: terminate_calls.append((pid, birth)) or True
        )

        result = self.supervisor.reconcile_prior_bridge()

        self.assertTrue(result["ok"], result)
        self.assertEqual(terminate_calls, [])

    def test_startup_fails_closed_when_orphan_stop_cannot_be_verified(self):
        self.write_prior_identity(
            state="active",
            pid=7788,
            birth_identity=self.birth_identity,
        )
        self.supervisor.birth_identity_reader = lambda _pid: self.birth_identity
        self.supervisor.exact_process_terminator = lambda _pid, _birth: True

        result = self.supervisor.reconcile_prior_bridge()

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "local_bridge_prior_process_stop_unverified",
        )
        self.assertTrue(self.supervisor.manual_intervention_required)

    def test_stop_without_owned_handle_preserves_unverified_active_identity(self):
        self.write_prior_identity(
            state="active",
            pid=7788,
            birth_identity=self.birth_identity,
        )
        self.supervisor.birth_identity_reader = lambda _pid: self.birth_identity
        self.supervisor.exact_process_terminator = lambda _pid, _birth: False
        self.supervisor.child = None

        result = self.supervisor.stop_bridge()

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "local_bridge_prior_process_stop_unverified",
        )
        persisted = json.loads(
            self.supervisor.bridge_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "active")
        self.assertEqual(persisted["pid"], 7788)
        self.assertEqual(persisted["birthIdentity"], self.birth_identity)

    def test_stop_without_owned_handle_exactly_reconciles_orphan(self):
        self.write_prior_identity(
            state="active",
            pid=7788,
            birth_identity=self.birth_identity,
        )
        observations = [self.birth_identity, None]
        self.supervisor.birth_identity_reader = lambda _pid: observations.pop(0)
        self.supervisor.exact_process_terminator = lambda _pid, _birth: True
        self.supervisor.child = None

        result = self.supervisor.stop_bridge()

        self.assertTrue(result["ok"], result)
        persisted = json.loads(
            self.supervisor.bridge_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "stopped")

    def test_startup_fails_closed_on_ambiguous_start_with_held_lock(self):
        self.write_prior_identity(state="starting")
        launches: list[bool] = []
        self.supervisor.bridge_lock_probe = lambda: False
        self.supervisor.popen = lambda *_args, **_kwargs: (
            launches.append(True) or FakeProcess()
        )

        result = self.supervisor.start_bridge()

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "local_bridge_prior_process_start_ambiguous",
        )
        self.assertEqual(launches, [])

    def test_job_assignment_failure_stops_exact_spawned_handle(self):
        process = FakeProcess()
        owner = FakeProcessOwner(assign_result=False)
        supervisor = HostSupervisor(
            project_root=self.supervisor.project_root,
            artifacts_root=self.supervisor.artifacts_root,
            popen=lambda *_args, **_kwargs: process,
            run_command=self.supervisor.run_command,
            now=self.clock,
            birth_identity_reader=lambda _pid: self.birth_identity,
            exact_process_terminator=lambda _pid, _birth: True,
            process_owner=owner,
            bridge_lock_probe=lambda: True,
        )

        result = supervisor.start_bridge()

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "local_bridge_process_owner_assignment_failed",
        )
        self.assertEqual(process.returncode, 0)
        self.assertIsNone(supervisor.child)

    def test_unstoppable_failed_spawn_keeps_handle_and_starting_identity(self):
        process = UnstoppableFakeProcess()
        owner = FakeProcessOwner(assign_result=False)
        supervisor = HostSupervisor(
            project_root=self.supervisor.project_root,
            artifacts_root=self.supervisor.artifacts_root,
            popen=lambda *_args, **_kwargs: process,
            run_command=self.supervisor.run_command,
            now=self.clock,
            birth_identity_reader=lambda _pid: self.birth_identity,
            exact_process_terminator=lambda _pid, _birth: True,
            process_owner=owner,
            bridge_lock_probe=lambda: True,
        )

        result = supervisor.start_bridge()

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "local_bridge_failed_spawn_stop_unverified",
        )
        self.assertIs(supervisor.child, process)
        persisted = json.loads(
            supervisor.bridge_identity_path.read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["state"], "starting")

    def test_restart_never_starts_when_exact_stop_failed(self):
        starts: list[bool] = []
        self.supervisor.stop_bridge = lambda: {
            "ok": False,
            "error": "local_bridge_process_stop_unverified",
        }
        self.supervisor.start_bridge = lambda *, automatic=False: (
            starts.append(automatic) or {"ok": True}
        )

        result = self.supervisor.restart_bridge()

        self.assertFalse(result["ok"])
        self.assertEqual(starts, [])
        self.assertEqual(result["started"]["status"], "not_attempted")

    def test_launch_failure_exposes_only_fixed_error_code(self):
        def fail_launch(*_args, **_kwargs):
            raise LookupError("private process detail")

        self.supervisor.popen = fail_launch

        result = self.supervisor.start_bridge()
        public = json.dumps(self.supervisor.status())

        self.assertEqual(result["error"], "local_bridge_launch_failed")
        self.assertNotIn("LookupError", public)
        self.assertNotIn("private process detail", public)

    def test_identity_failure_exposes_only_fixed_error_code(self):
        self.supervisor.popen = lambda *_args, **_kwargs: FakeProcess()

        def fail_identity(_pid):
            raise OSError("private identity detail")

        self.supervisor.birth_identity_reader = fail_identity

        result = self.supervisor.start_bridge()
        public = json.dumps(self.supervisor.status())

        self.assertEqual(
            result["error"],
            "local_bridge_process_identity_unavailable",
        )
        self.assertNotIn("OSError", public)
        self.assertNotIn("private identity detail", public)

    def test_status_requires_job_and_birth_identity_for_owned_child(self):
        self.supervisor._startup_reconciled = True
        self.supervisor.child = FakeProcess()
        self.supervisor.child_birth_identity = self.birth_identity

        payload = self.supervisor.status()

        self.assertTrue(payload["localBridge"]["ownershipReady"])
        self.assertTrue(payload["localBridge"]["birthIdentityRecorded"])
        self.assertEqual(
            payload["localBridge"]["ownershipMode"],
            "windows_job_kill_on_close",
        )


if __name__ == "__main__":
    unittest.main()
