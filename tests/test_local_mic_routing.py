from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_mic import (  # noqa: E402
    mono16k_float_to_discord_pcm,
    normalize_sounddevice_identifier,
    parse_user_id_set,
    resolve_local_mic_target,
    serialize_local_mic_target,
    should_route_discord_user_to_local_mic,
)


class LocalMicRoutingTests(unittest.TestCase):
    def test_parse_user_id_set_ignores_invalid_tokens(self) -> None:
        parsed = parse_user_id_set("441943340624248843, nope; 405351496012791808")
        self.assertEqual(parsed, {441943340624248843, 405351496012791808})

    def test_normalize_sounddevice_identifier_accepts_numeric_string_index(self) -> None:
        self.assertEqual(normalize_sounddevice_identifier("18"), 18)
        self.assertEqual(normalize_sounddevice_identifier(" fifine Microphone "), "fifine Microphone")
        self.assertIsNone(normalize_sounddevice_identifier(" "))

    def test_should_route_only_when_capture_ready(self) -> None:
        self.assertTrue(
            should_route_discord_user_to_local_mic(
                441943340624248843,
                preferred_user_ids={441943340624248843},
                capture_ready=True,
            )
        )
        self.assertFalse(
            should_route_discord_user_to_local_mic(
                441943340624248843,
                preferred_user_ids={441943340624248843},
                capture_ready=False,
            )
        )
        self.assertFalse(
            should_route_discord_user_to_local_mic(
                405351496012791808,
                preferred_user_ids={441943340624248843},
                capture_ready=True,
            )
        )

    def test_resolve_local_mic_target_uses_member_in_active_voice_channel(self) -> None:
        target_member = SimpleNamespace(id=441943340624248843, bot=False)
        other_member = SimpleNamespace(id=405351496012791808, bot=False)
        channel = SimpleNamespace(id=99, members=[other_member, target_member])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.guild_id, 7)
        self.assertEqual(target.voice_channel_id, 99)
        self.assertIs(target.member, target_member)

    def test_resolve_local_mic_target_returns_none_without_match(self) -> None:
        channel = SimpleNamespace(id=99, members=[SimpleNamespace(id=1, bot=False)])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})

        self.assertIsNone(target)

    def test_serialize_local_mic_target_returns_json_safe_snapshot(self) -> None:
        member = SimpleNamespace(id=441943340624248843, name="JH", display_name="정훈", bot=False)
        channel = SimpleNamespace(id=99, members=[member])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})
        snapshot = serialize_local_mic_target(target)

        self.assertEqual(
            snapshot,
            {
                "guildId": 7,
                "voiceChannelId": 99,
                "memberId": 441943340624248843,
                "memberName": "정훈",
            },
        )

    def test_mono16k_float_to_discord_pcm_matches_discord_shape(self) -> None:
        audio = np.full(16000, 0.1, dtype=np.float32)

        pcm_bytes = mono16k_float_to_discord_pcm(audio, sampling_rate=16000)

        self.assertEqual(len(pcm_bytes), 48000 * 2 * 2)


if __name__ == "__main__":
    unittest.main()
