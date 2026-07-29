from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_capabilities import build_voice_capabilities  # noqa: E402


def service(service_id: str, *, state: str = "up", payload=None):
    checks = []
    if payload is not None:
        checks.append({"kind": "artifact_json", "ok": True, "payload": payload})
    return {
        "id": service_id,
        "label": service_id,
        "state": state,
        "ready": state == "up",
        "reason": "ok" if state == "up" else "down",
        "checks": checks,
    }


class VoiceCapabilitiesTests(unittest.TestCase):
    def ready_health(self):
        return {
            "services": [
                service("host_supervisor", payload={"schema": "host_supervisor.status.v1"}),
                service(
                    "local_io_bridge",
                    payload={
                        "schema": "local_io_bridge.status.v1",
                        "micEnabled": True,
                        "mic": {"enabled": True, "captureReady": True},
                        "outputDevice": "default",
                        "ttsWarmup": {"enabled": True, "done": True, "error": ""},
                    },
                ),
                service(
                    "discord_bot",
                    payload={
                        "schema": "discord_runtime.status.v1",
                        "gatewayConnected": True,
                        "guildConnected": True,
                        "voiceConnected": True,
                        "listening": True,
                    },
                ),
                service("main_llm"),
                service("stt"),
                service("tts"),
            ]
        }

    def test_both_voice_surfaces_ready(self):
        capabilities = build_voice_capabilities(self.ready_health())
        self.assertTrue(capabilities["voiceLocal"]["ready"])
        self.assertEqual(capabilities["voiceLocal"]["state"], "ready")
        self.assertTrue(capabilities["voiceDiscord"]["ready"])

    def test_local_mic_and_warmup_are_hard_blockers(self):
        health = self.ready_health()
        bridge = next(row for row in health["services"] if row["id"] == "local_io_bridge")
        bridge["checks"][0]["payload"]["mic"]["captureReady"] = False
        bridge["checks"][0]["payload"]["ttsWarmup"]["done"] = False
        capabilities = build_voice_capabilities(health)
        codes = {item["code"] for item in capabilities["voiceLocal"]["blockers"]}
        self.assertEqual(capabilities["voiceLocal"]["state"], "unavailable")
        self.assertIn("local_mic_capture_not_ready", codes)
        self.assertIn("tts_warmup_incomplete", codes)

    def test_discord_connection_and_listening_are_hard_blockers(self):
        health = self.ready_health()
        discord = next(row for row in health["services"] if row["id"] == "discord_bot")
        discord["checks"][0]["payload"]["voiceConnected"] = False
        discord["checks"][0]["payload"]["listening"] = False
        capability = build_voice_capabilities(health)["voiceDiscord"]
        codes = {item["code"] for item in capability["blockers"]}
        self.assertFalse(capability["ready"])
        self.assertIn("discord_voice_disconnected", codes)
        self.assertIn("discord_not_listening", codes)

    def test_missing_supervisor_returns_manual_start_instruction(self):
        health = self.ready_health()
        health["services"] = [row for row in health["services"] if row["id"] != "host_supervisor"]
        capability = build_voice_capabilities(health)["voiceLocal"]
        actions = {item["actionId"]: item for item in capability["repairActions"]}
        self.assertIn("start_host_supervisor_manual", actions)
        self.assertEqual(actions["start_host_supervisor_manual"]["manualCommand"], "start_local.bat --background")

    def test_non_blocking_runtime_error_marks_capability_degraded(self):
        health = self.ready_health()
        bridge = next(row for row in health["services"] if row["id"] == "local_io_bridge")
        bridge["checks"][0]["payload"]["lastError"] = "previous transient error"

        capability = build_voice_capabilities(health)["voiceLocal"]

        self.assertTrue(capability["ready"])
        self.assertEqual(capability["state"], "degraded")
        self.assertEqual(capability["warnings"][0]["code"], "local_bridge_reported_error")


if __name__ == "__main__":
    unittest.main()
