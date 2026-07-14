from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_control_voice_runtime import (  # noqa: E402
    build_local_control_voice_member_from_runtime,
    is_local_speaker_voice_client_from_runtime,
)


class LocalControlVoiceRuntimeTests(unittest.TestCase):
    def test_builds_member_with_preferred_lowest_discord_user_id(self) -> None:
        member = build_local_control_voice_member_from_runtime(
            local_control_guild_id=9000,
            local_control_guild_name="Local",
            local_mic_discord_user_ids={42, 7},
            local_mic_user_name="정훈",
        )

        self.assertEqual(member.id, 7)
        self.assertEqual(member.display_name, "정훈")
        self.assertEqual(member.name, "정훈")
        self.assertEqual(member.guild.id, 9000)
        self.assertEqual(member.guild.name, "Local")
        self.assertIs(member.guild.voice_client.guild, member.guild)
        self.assertTrue(member.guild.voice_client.local_speaker_output)
        self.assertFalse(member.bot)

    def test_builds_member_with_local_control_guild_id_when_no_user_ids(self) -> None:
        member = build_local_control_voice_member_from_runtime(
            local_control_guild_id=9000,
            local_control_guild_name="Local",
            local_mic_discord_user_ids=set(),
            local_mic_user_name="정훈",
        )

        self.assertEqual(member.id, 9000)

    def test_is_local_speaker_voice_client_reads_marker_attribute(self) -> None:
        self.assertTrue(is_local_speaker_voice_client_from_runtime(SimpleNamespace(local_speaker_output=True)))
        self.assertFalse(is_local_speaker_voice_client_from_runtime(SimpleNamespace(local_speaker_output=False)))
        self.assertFalse(is_local_speaker_voice_client_from_runtime(object()))


if __name__ == "__main__":
    unittest.main()
