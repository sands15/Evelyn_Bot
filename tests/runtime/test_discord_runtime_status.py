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

from evelyn_core.discord_runtime_status import DiscordRuntimeStatus  # noqa: E402


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
    def test_snapshot_and_heartbeat_reflect_gateway_voice_and_listening(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "discord" / "status.json"
            guild = SimpleNamespace(id=11, voice_client=FakeVoiceClient())
            status = DiscordRuntimeStatus(
                bot_user=lambda: SimpleNamespace(id=7),
                bot_guilds=lambda: [guild],
                voice_client_type=FakeVoiceClient,
                status_path=path,
                now=lambda: 1234.5,
            )

            payload = status.write_once()
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(payload["gatewayConnected"])
        self.assertTrue(payload["guildConnected"])
        self.assertTrue(payload["voiceConnected"])
        self.assertTrue(payload["listening"])
        self.assertEqual(persisted["heartbeatAt"], 1234.5)
        self.assertNotIn("transcript", persisted)

    def test_disconnected_voice_is_not_reported_as_ready(self):
        guild = SimpleNamespace(
            id=11,
            voice_client=FakeVoiceClient(connected=False, listening=False),
        )
        status = DiscordRuntimeStatus(
            bot_user=lambda: SimpleNamespace(id=7),
            bot_guilds=lambda: [guild],
            voice_client_type=FakeVoiceClient,
        )
        payload = status.snapshot()
        self.assertFalse(payload["voiceConnected"])
        self.assertFalse(payload["listening"])


if __name__ == "__main__":
    unittest.main()
