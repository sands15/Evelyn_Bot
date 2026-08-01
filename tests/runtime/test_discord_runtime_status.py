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

from evelyn_core.discord_runtime_status import (  # noqa: E402
    DiscordRuntimeStatus,
    discord_gateway_connected,
)


class FakeVoiceClient:
    def __init__(self, *, connected=True, listening=True):
        self.channel = SimpleNamespace(id=44)
        self.connected = connected
        self.listening = listening

    def is_connected(self):
        return self.connected

    def is_listening(self):
        return self.listening


class DiscordRuntimeStatusTests(unittest.TestCase):
    def test_gateway_probe_requires_live_ready_websocket(self):
        websocket = SimpleNamespace(open=True)
        bot = SimpleNamespace(
            is_ready=lambda: True,
            is_closed=lambda: False,
            ws=websocket,
        )

        self.assertTrue(discord_gateway_connected(bot))

        websocket.open = False
        self.assertFalse(discord_gateway_connected(bot))

    def test_gateway_probe_rejects_cached_ready_state_without_socket(self):
        bot = SimpleNamespace(
            is_ready=lambda: True,
            is_closed=lambda: False,
            ws=None,
        )

        self.assertFalse(discord_gateway_connected(bot))

    def test_gateway_probe_requires_exact_booleans_and_open_client(self):
        bot = SimpleNamespace(
            is_ready=lambda: 1,
            is_closed=lambda: False,
            ws=SimpleNamespace(open=True),
        )
        self.assertFalse(discord_gateway_connected(bot))

        bot.is_ready = lambda: True
        bot.is_closed = lambda: True
        self.assertFalse(discord_gateway_connected(bot))

    def test_snapshot_and_heartbeat_reflect_gateway_voice_and_listening(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord" / "status.json"
            guild = SimpleNamespace(id=11, voice_client=FakeVoiceClient())
            status = DiscordRuntimeStatus(
                gateway_ready=lambda: True,
                bot_guilds=lambda: [guild],
                voice_client_type=FakeVoiceClient,
                status_path=path,
                now=lambda: 1234.5,
                search_followup_recovery_status=lambda: {
                    "state": "ready",
                    "pendingCount": 1,
                    "policy": {"contentFree": True},
                },
            )

            payload = status.write_once()
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(payload["gatewayConnected"])
        self.assertTrue(payload["guildConnected"])
        self.assertTrue(payload["voiceConnected"])
        self.assertTrue(payload["listening"])
        self.assertEqual(persisted["heartbeatAt"], 1234.5)
        self.assertEqual(
            persisted["searchFollowupRecovery"]["pendingCount"],
            1,
        )
        self.assertNotIn("transcript", persisted)

    def test_disconnected_voice_is_not_reported_as_ready(self):
        guild = SimpleNamespace(
            id=11,
            voice_client=FakeVoiceClient(connected=False, listening=False),
        )
        status = DiscordRuntimeStatus(
            gateway_ready=lambda: True,
            bot_guilds=lambda: [guild],
            voice_client_type=FakeVoiceClient,
        )
        payload = status.snapshot()
        self.assertFalse(payload["voiceConnected"])
        self.assertFalse(payload["listening"])

    def test_cached_guild_and_voice_do_not_mask_gateway_disconnect(self):
        gateway = {"ready": True}
        guild = SimpleNamespace(id=11, voice_client=FakeVoiceClient())
        status = DiscordRuntimeStatus(
            gateway_ready=lambda: gateway["ready"],
            bot_guilds=lambda: [guild],
            voice_client_type=FakeVoiceClient,
        )

        self.assertTrue(status.snapshot()["gatewayConnected"])
        gateway["ready"] = False
        disconnected = status.snapshot()

        self.assertFalse(disconnected["gatewayConnected"])
        self.assertTrue(disconnected["guildConnected"])
        self.assertTrue(disconnected["voiceConnected"])

    def test_gateway_probe_failure_is_fail_closed_and_content_free(self):
        def fail_gateway_probe():
            raise RuntimeError("private gateway token")

        status = DiscordRuntimeStatus(
            gateway_ready=fail_gateway_probe,
            bot_guilds=lambda: [],
            voice_client_type=FakeVoiceClient,
            now=lambda: 1234.5,
        )

        payload = status.snapshot()

        self.assertFalse(payload["gatewayConnected"])
        self.assertEqual(
            payload["lastErrorCode"],
            "gateway_readiness_probe_failed",
        )
        self.assertEqual(payload["lastErrorType"], "RuntimeError")
        self.assertNotIn("private", json.dumps(payload))
        self.assertNotIn("token", json.dumps(payload))

    def test_recorded_error_is_exposed_as_code_type_count_only(self):
        status = DiscordRuntimeStatus(
            gateway_ready=lambda: True,
            bot_guilds=lambda: [],
            voice_client_type=FakeVoiceClient,
            now=lambda: 1234.5,
        )

        status.record_error("voice/rearm failed", RuntimeError("private token"))
        payload = status.snapshot()

        self.assertEqual(payload["errorCount"], 1)
        self.assertEqual(payload["lastErrorAt"], 1234.5)
        self.assertEqual(payload["lastErrorCode"], "voice_rearm_failed")
        self.assertEqual(payload["lastErrorType"], "RuntimeError")
        self.assertNotIn("private", json.dumps(payload))
        self.assertNotIn("token", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
