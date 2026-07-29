from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.host_supervisor import HostSupervisor  # noqa: E402
from evelyn_core.host_supervisor_client import SUPERVISOR_REQUEST_SCHEMA  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class HostSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.clock = FakeClock()
        self.commands = []

        def run(command, **kwargs):
            self.commands.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        self.supervisor = HostSupervisor(
            project_root=root,
            artifacts_root=root / "runtime_artifacts",
            run_command=run,
            now=self.clock,
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

    def test_successful_status_write_clears_transient_heartbeat_error(self):
        self.supervisor.last_error = "heartbeat_write_failed:PermissionError"

        self.supervisor.write_status()

        payload = json.loads(
            self.supervisor.status_path.read_text(encoding="utf-8")
        )
        self.assertEqual(payload["lastError"], "")
        self.assertEqual(self.supervisor.last_error, "")


if __name__ == "__main__":
    unittest.main()
