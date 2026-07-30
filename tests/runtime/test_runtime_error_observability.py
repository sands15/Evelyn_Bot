from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_error_observability import (  # noqa: E402
    RUNTIME_ERROR_SUMMARY_SCHEMA,
    RuntimeErrorCounter,
    collect_runtime_error_observability,
)


def write_status(root: Path, relative: str, payload: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class RuntimeErrorCounterTests(unittest.TestCase):
    def test_counter_stores_only_sanitized_code_type_and_time(self) -> None:
        counter = RuntimeErrorCounter(now=lambda: 1234.5)

        snapshot = counter.record("../Secret path", RuntimeError("C:\\private\\token"))

        self.assertEqual(snapshot["errorCount"], 1)
        self.assertEqual(snapshot["lastErrorAt"], 1234.5)
        self.assertEqual(snapshot["lastErrorCode"], "runtime_error")
        self.assertEqual(snapshot["lastErrorType"], "RuntimeError")
        serialized = json.dumps(snapshot)
        self.assertNotIn("private", serialized)
        self.assertNotIn("token", serialized)

    def test_counter_groups_repeated_fixed_codes(self) -> None:
        counter = RuntimeErrorCounter(now=lambda: 1000.0)

        counter.record("voice_rearm_failed", ValueError())
        snapshot = counter.record("voice_rearm_failed", TimeoutError())

        self.assertEqual(snapshot["errorCount"], 2)
        self.assertEqual(snapshot["errorCounters"], {"voice_rearm_failed": 2})
        self.assertEqual(snapshot["lastErrorType"], "TimeoutError")


class RuntimeErrorObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.now = 2000.0

    def test_summary_aggregates_counts_without_raw_error_text_or_paths(self) -> None:
        write_status(
            self.root,
            "host_supervisor/status.json",
            {
                "schema": "host_supervisor.status.v1",
                "heartbeatAt": self.now,
                "lastError": "C:\\private\\token was rejected",
                "errorCount": 2,
                "lastErrorAt": self.now - 5,
                "lastErrorCode": "docker_compose_failed",
                "lastErrorType": "CalledProcessError",
                "errorCounters": {"docker_compose_failed": 2},
            },
        )
        write_status(
            self.root,
            "local_bridge/status.json",
            {
                "schema": "local_io_bridge.status.v1",
                "heartbeatAt": self.now,
                "lastError": "",
                "errorCount": 1,
                "lastErrorAt": self.now - 120,
                "lastErrorCode": "tts_warmup_attempt_failed",
                "lastErrorType": "TimeoutError",
                "errorCounters": {"tts_warmup_attempt_failed": 1},
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        self.assertEqual(summary["schema"], RUNTIME_ERROR_SUMMARY_SCHEMA)
        self.assertEqual(summary["state"], "error")
        self.assertEqual(summary["summary"]["sourceCount"], 8)
        self.assertEqual(summary["summary"]["totalCount"], 3)
        self.assertEqual(summary["summary"]["currentErrorCount"], 1)
        self.assertEqual(summary["summary"]["recentErrorCount"], 2)
        self.assertFalse(summary["privacy"]["exceptionMessages"])
        serialized = json.dumps(summary)
        self.assertNotIn("private", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_stale_current_error_is_not_reported_as_active(self) -> None:
        write_status(
            self.root,
            "host_supervisor/status.json",
            {
                "schema": "host_supervisor.status.v1",
                "heartbeatAt": self.now - 10,
                "lastError": "still present",
                "errorCount": 1,
                "lastErrorAt": self.now - 10,
                "lastErrorCode": "heartbeat_write_failed",
                "lastErrorType": "PermissionError",
                "errorCounters": {"heartbeat_write_failed": 1},
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        self.assertEqual(summary["state"], "attention")
        self.assertEqual(summary["summary"]["currentErrorCount"], 0)
        self.assertEqual(summary["summary"]["staleCount"], 1)
        self.assertTrue(summary["sources"]["hostSupervisor"]["stale"])

    def test_missing_and_corrupt_sources_are_safe(self) -> None:
        write_status(
            self.root,
            "discord/status.json",
            {
                "schema": "wrong.schema",
                "heartbeatAt": self.now,
                "lastError": "secret",
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        self.assertEqual(summary["state"], "unknown")
        self.assertEqual(summary["sources"]["hostSupervisor"]["state"], "missing")
        self.assertEqual(summary["sources"]["discord"]["state"], "invalid")
        self.assertEqual(summary["summary"]["totalCount"], 0)
        self.assertNotIn("secret", json.dumps(summary))

    def test_only_stale_sources_without_recent_errors_are_unknown(self) -> None:
        write_status(
            self.root,
            "local_bridge/status.json",
            {
                "schema": "local_io_bridge.status.v1",
                "heartbeatAt": self.now - 4000,
                "lastError": "",
                "errorCount": 0,
                "lastErrorAt": None,
                "lastErrorCode": "",
                "lastErrorType": "",
                "errorCounters": {},
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        self.assertEqual(summary["state"], "unknown")
        self.assertEqual(summary["summary"]["staleCount"], 1)

    def test_http_owner_counters_are_merged_without_error_messages(self) -> None:
        service_health = {
            "stt": {
                "id": "stt",
                "state": "up",
                "checkedAt": self.now,
                "checks": [
                    {
                        "kind": "http",
                        "payload": {
                            "ok": True,
                            "ready": True,
                            "errorCount": 2,
                            "lastErrorAt": self.now - 3,
                            "lastErrorCode": "stt_transcribe_failed",
                            "lastErrorType": "RuntimeError",
                            "errorCounters": {
                                "stt_transcribe_failed": 2,
                            },
                            "privateMessage": "C:\\secret\\model",
                        },
                    }
                ],
            }
        }

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
            service_health=service_health,
        )

        self.assertEqual(summary["state"], "attention")
        self.assertEqual(summary["sources"]["stt"]["errorCount"], 2)
        self.assertEqual(
            summary["sources"]["stt"]["lastErrorCode"],
            "stt_transcribe_failed",
        )
        serialized = json.dumps(summary)
        self.assertNotIn("privateMessage", serialized)
        self.assertNotIn("secret", serialized)

    def test_conversation_continuity_error_is_current_and_private(self) -> None:
        write_status(
            self.root,
            "conversation_continuity/status.json",
            {
                "schema": "conversation_continuity.status.v1",
                "state": "error",
                "heartbeatAt": self.now,
                "errorCount": 1,
                "lastErrorAt": self.now,
                "lastErrorCode": "conversation_continuity_flush_failed",
                "lastErrorType": "PermissionError",
                "errorCounters": {
                    "conversation_continuity_flush_failed": 1,
                },
                "privateMessage": "C:\\private\\conversation",
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        source = summary["sources"]["conversationContinuity"]
        self.assertEqual(summary["state"], "error")
        self.assertTrue(source["hasCurrentError"])
        self.assertEqual(
            source["lastErrorCode"],
            "conversation_continuity_flush_failed",
        )
        self.assertNotIn("private", json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
