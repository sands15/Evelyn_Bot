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

    def test_voice_pipeline_codes_remain_actionable(self) -> None:
        counter = RuntimeErrorCounter(now=lambda: 1000.0)

        for code in (
            "stt_timeout",
            "tts_request_failed",
            "tts_producer_cancelled",
            "tts_playback_failed",
            "voice_connection_unavailable",
            "voice_delivery_empty",
            "voice_delivery_failed",
        ):
            snapshot = counter.record(code, RuntimeError("private"))
            self.assertEqual(snapshot["lastErrorCode"], code)

    def test_non_exception_type_text_is_rejected(self) -> None:
        counter = RuntimeErrorCounter(now=lambda: 1000.0)

        snapshot = counter.record(
            "voice_delivery_failed",
            type(
                "Bearer_private_token",
                (Exception,),
                {},
            )(),
        )

        self.assertEqual(snapshot["lastErrorType"], "")
        self.assertNotIn("private", json.dumps(snapshot).lower())

    def test_guild_reset_revocation_error_keeps_its_actionable_code(self) -> None:
        counter = RuntimeErrorCounter(now=lambda: 1000.0)

        snapshot = counter.record(
            "conversation_continuity_guild_reset_revoke_failed",
            PermissionError("private checkpoint path"),
        )

        self.assertEqual(
            snapshot["lastErrorCode"],
            "conversation_continuity_guild_reset_revoke_failed",
        )
        self.assertNotIn("private", json.dumps(snapshot))


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
        self.assertEqual(summary["summary"]["sourceCount"], 9)
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

    def test_fast_control_continuity_error_is_collected(self) -> None:
        write_status(
            self.root,
            "fast_control_continuity/status.json",
            {
                "schema": "conversation_continuity.status.v1",
                "state": "error",
                "heartbeatAt": self.now,
                "errorCount": 1,
                "lastErrorAt": self.now,
                "lastErrorCode": "conversation_continuity_commit_failed",
                "lastErrorType": "OSError",
                "errorCounters": {
                    "conversation_continuity_commit_failed": 1,
                },
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        source = summary["sources"]["fastControlContinuity"]
        self.assertEqual(summary["state"], "error")
        self.assertEqual(summary["summary"]["sourceCount"], 9)
        self.assertEqual(summary["summary"]["totalCount"], 1)
        self.assertEqual(summary["summary"]["currentErrorCount"], 1)
        self.assertTrue(source["hasCurrentError"])

    def test_required_probe_failure_is_current_without_exception_count(self) -> None:
        required_sources = {
            "control_page": "controlPage",
            "bot_api": "botApi",
            "main_llm": "mainLlm",
            "sub_llm": "subLlm",
            "router_llm": "routerLlm",
            "tts": "tts",
            "stt": "stt",
        }

        def failed_service(service_id: str, *, required: bool) -> dict:
            return {
                "id": service_id,
                "required": required,
                "state": "down",
                "checkedAt": self.now,
                "checks": [
                    {"kind": "tcp", "ok": False, "reason": "timeout"}
                ],
            }

        service_health = {
            service_id: failed_service(service_id, required=True)
            for service_id in required_sources
        }
        service_health["vision"] = failed_service(
            "vision",
            required=False,
        )
        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
            service_health=service_health,
        )

        self.assertEqual(summary["state"], "error")
        self.assertEqual(summary["summary"]["totalCount"], 0)
        self.assertEqual(summary["summary"]["sourceCount"], 15)
        self.assertEqual(summary["summary"]["currentErrorCount"], 7)
        for source_id in required_sources.values():
            source = summary["sources"][source_id]
            self.assertEqual(source["state"], "down")
            self.assertTrue(source["available"])
            self.assertTrue(source["hasCurrentError"])
        optional_source = summary["sources"]["vision"]
        self.assertFalse(optional_source["available"])
        self.assertFalse(optional_source["hasCurrentError"])

    def test_continuity_commit_latency_warning_is_projected_privately(
        self,
    ) -> None:
        write_status(
            self.root,
            "conversation_continuity/status.json",
            {
                "schema": "conversation_continuity.status.v1",
                "state": "ready",
                "heartbeatAt": self.now,
                "errorCount": 0,
                "lastErrorAt": None,
                "lastErrorCode": "",
                "lastErrorType": "",
                "errorCounters": {},
                "completedTurnCommit": {
                    "schema": (
                        "conversation_continuity.commit-metrics.v1"
                    ),
                    "state": "warning",
                    "attemptCount": 20,
                    "successCount": 20,
                    "failureCount": 0,
                    "sampleCount": 20,
                    "lastMs": 140.0,
                    "p50Ms": 80.0,
                    "p95Ms": 125.0,
                    "maxMs": 140.0,
                    "lastAt": self.now - 1,
                    "lastSucceeded": True,
                    "lastTargetVerified": True,
                    "warningThresholdMs": 100.0,
                    "warningCode": (
                        "conversation_continuity_commit_latency_high"
                    ),
                    "privateMessage": "C:\\private\\turn",
                },
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        source = summary["sources"]["conversationContinuity"]
        self.assertEqual(summary["state"], "attention")
        self.assertEqual(source["state"], "degraded")
        self.assertEqual(
            source["completedTurnCommit"]["p95Ms"],
            125.0,
        )
        self.assertTrue(
            source["completedTurnCommit"][
                "lastTargetVerified"
            ]
        )
        self.assertEqual(
            summary["warnings"],
            [
                {
                    "source": "conversationContinuity",
                    "code": (
                        "conversation_continuity_commit_latency_high"
                    ),
                }
            ],
        )
        self.assertNotIn("private", json.dumps(summary))

    def test_stale_continuity_latency_does_not_raise_current_warning(
        self,
    ) -> None:
        write_status(
            self.root,
            "conversation_continuity/status.json",
            {
                "schema": "conversation_continuity.status.v1",
                "state": "ready",
                "heartbeatAt": self.now - 10,
                "errorCount": 0,
                "lastErrorAt": None,
                "lastErrorCode": "",
                "lastErrorType": "",
                "errorCounters": {},
                "completedTurnCommit": {
                    "schema": (
                        "conversation_continuity.commit-metrics.v1"
                    ),
                    "state": "warning",
                    "attemptCount": 20,
                    "successCount": 20,
                    "failureCount": 0,
                    "sampleCount": 20,
                    "p95Ms": 125.0,
                    "lastSucceeded": True,
                    "warningThresholdMs": 100.0,
                    "warningCode": (
                        "conversation_continuity_commit_latency_high"
                    ),
                },
            },
        )

        summary = collect_runtime_error_observability(
            artifacts_root=self.root,
            now=self.now,
        )

        source = summary["sources"]["conversationContinuity"]
        self.assertEqual(summary["state"], "unknown")
        self.assertEqual(source["state"], "stale")
        self.assertEqual(summary["warnings"], [])


if __name__ == "__main__":
    unittest.main()
