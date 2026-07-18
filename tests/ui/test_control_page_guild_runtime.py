from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_guild_runtime import (
    ControlPageGuildSelectionRuntimeDeps,
    current_tts_target_name_from_runtime,
    resolve_guild_member_name_from_runtime,
    select_control_page_guild_from_runtime,
)  # noqa: E402


def _build_deps(
    *,
    guilds=(),
    requested_map=None,
    tracked_playback_ids=(),
    playback_by_guild=None,
    active_session_users=None,
    member_map=None,
):
    requested_map = dict(requested_map or {})
    playback_by_guild = dict(playback_by_guild or {})
    active_session_users = dict(active_session_users or {})
    member_map = dict(member_map or {})

    return ControlPageGuildSelectionRuntimeDeps(
        get_requested_guild=lambda guild_id: requested_map.get(int(guild_id)),
        bot_guilds=lambda: list(guilds),
        tracked_tts_playback_guild_ids=lambda: list(tracked_playback_ids),
        get_tracked_tts_playback=lambda guild_id: playback_by_guild.get(int(guild_id)),
        get_active_session_user_id=lambda session_key: active_session_users.get(str(session_key)),
        get_guild_member=lambda guild, user_id: member_map.get(int(guild.id), {}).get(int(user_id)),
        clean_text=lambda text: str(text).strip(),
    )


class ControlPageGuildSelectionRuntimeTests(unittest.TestCase):
    def test_select_control_page_guild_prefers_requested_id(self) -> None:
        requested = SimpleNamespace(id=10)
        deps = _build_deps(
            guilds=(SimpleNamespace(id=20),),
            requested_map={10: requested},
            tracked_playback_ids=(30,),
        )

        self.assertIs(select_control_page_guild_from_runtime(10, deps=deps), requested)

    def test_select_control_page_guild_uses_tracking_and_voice_then_fallback(self) -> None:
        voice_guild = SimpleNamespace(id=3, voice_client=True)
        active_guild = SimpleNamespace(id=2, voice_client=None)
        fallback_guild = SimpleNamespace(id=1, voice_client=None)
        deps = _build_deps(
            guilds=(active_guild, fallback_guild),
            tracked_playback_ids=(3,),
            requested_map={3: voice_guild, 2: active_guild, 1: fallback_guild},
        )

        self.assertIs(select_control_page_guild_from_runtime(None, deps=deps), voice_guild)
        self.assertIs(select_control_page_guild_from_runtime(None, deps=_build_deps(guilds=(active_guild, fallback_guild), requested_map={2: active_guild, 1: fallback_guild})), active_guild)
        self.assertIs(select_control_page_guild_from_runtime(None, deps=_build_deps(guilds=(fallback_guild,), requested_map={1: fallback_guild})), fallback_guild)

    def test_resolve_guild_member_name_and_current_tts_target(self) -> None:
        member = SimpleNamespace(id=77, display_name="Alice", name="AL")
        guild = SimpleNamespace(id=101, get_member=lambda user_id: member if int(user_id) == 77 else None)
        deps = _build_deps(
            playback_by_guild={101: {"session_key": "sess-1"}},
            active_session_users={"sess-1": 77},
            member_map={101: {77: member}},
        )

        self.assertEqual(resolve_guild_member_name_from_runtime(guild, 77, deps=deps), "Alice")
        self.assertEqual(current_tts_target_name_from_runtime(guild, deps=deps), "Alice")


if __name__ == "__main__":
    unittest.main()
