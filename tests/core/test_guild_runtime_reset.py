from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.guild_runtime_reset import (  # noqa: E402
    GuildRuntimeResetDeps,
    build_guild_runtime_reset_deps,
    reset_guild_runtime_state_from_runtime,
)


class FakeTask:
    def __init__(self, done: bool = False) -> None:
        self._done = done
        self.cancelled = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancelled = True


class FakeTurnScopeRegistry:
    def __init__(self) -> None:
        self.cancelled_prefixes: list[str] = []

    def cancel_matching_prefix(self, prefix: str) -> None:
        self.cancelled_prefixes.append(prefix)


class GuildRuntimeResetTests(unittest.TestCase):
    def build_deps(self) -> tuple[GuildRuntimeResetDeps, dict[str, Any]]:
        guild_key = "guild:7:voice:42"
        other_key = "guild:8:voice:42"
        search_task = FakeTask()
        done_search_task = FakeTask(done=True)
        cognitive_task = FakeTask()
        refresh_task = FakeTask()
        registry = FakeTurnScopeRegistry()
        cleared_tts: list[int] = []
        state: dict[str, Any] = {
            "session_histories": {guild_key: [], other_key: []},
            "session_followup_targets": {guild_key: "target", other_key: "target"},
            "active_session_until": {guild_key: 1.0, other_key: 2.0},
            "active_session_user_ids": {guild_key: 7},
            "session_last_active_at": {guild_key: 1.0},
            "session_awaiting_user_reply": {guild_key: True},
            "session_last_speaker": {guild_key: "user"},
            "session_topic_ids": {guild_key: "topic"},
            "session_turn_ids": {guild_key: "turn"},
            "session_segment_counters": {guild_key: 3},
            "session_last_turn_accepted_at": {guild_key: 1.0},
            "session_last_stt_text": {guild_key: "hi"},
            "room_last_voice_utterance_for_merge": {
                "room-a": SimpleNamespace(session_key=guild_key),
                "room-b": SimpleNamespace(session_key=other_key),
            },
            "session_partial_stt_text": {guild_key: "partial"},
            "session_committed_stt_text": {guild_key: "committed"},
            "session_bad_audio_counts": {guild_key: 2},
            "room_owner_user_ids": {guild_key: 7, other_key: 8},
            "room_owner_until": {guild_key: 1.0},
            "room_reply_in_progress": {guild_key: True},
            "room_last_voice_reply_at": {guild_key: 1.0},
            "session_locks": {guild_key: object(), other_key: object()},
            "background_search_tasks": {
                guild_key: search_task,
                f"{guild_key}:done": done_search_task,
                other_key: FakeTask(),
            },
            "memory_locks": {7: object(), 8: object()},
            "cognitive_locks": {7: object(), 8: object()},
            "background_cognitive_tasks": {guild_key: cognitive_task, other_key: FakeTask()},
            "autonomy_last_cognitive_refresh_at": {7: 1.0, 8: 2.0},
            "autonomy_cognitive_refresh_tasks": {7: refresh_task, 8: FakeTask()},
            "registry": registry,
            "cleared_tts": cleared_tts,
            "tasks": {
                "search": search_task,
                "done_search": done_search_task,
                "cognitive": cognitive_task,
                "refresh": refresh_task,
            },
        }
        deps = GuildRuntimeResetDeps(
            session_histories=state["session_histories"],
            session_followup_targets=state["session_followup_targets"],
            active_session_until=state["active_session_until"],
            active_session_user_ids=state["active_session_user_ids"],
            session_last_active_at=state["session_last_active_at"],
            session_awaiting_user_reply=state["session_awaiting_user_reply"],
            session_last_speaker=state["session_last_speaker"],
            session_topic_ids=state["session_topic_ids"],
            session_turn_ids=state["session_turn_ids"],
            session_segment_counters=state["session_segment_counters"],
            session_last_turn_accepted_at=state["session_last_turn_accepted_at"],
            session_last_stt_text=state["session_last_stt_text"],
            room_last_voice_utterance_for_merge=state["room_last_voice_utterance_for_merge"],
            session_partial_stt_text=state["session_partial_stt_text"],
            session_committed_stt_text=state["session_committed_stt_text"],
            session_bad_audio_counts=state["session_bad_audio_counts"],
            room_owner_user_ids=state["room_owner_user_ids"],
            room_owner_until=state["room_owner_until"],
            room_reply_in_progress=state["room_reply_in_progress"],
            room_last_voice_reply_at=state["room_last_voice_reply_at"],
            turn_scope_registry=registry,
            session_locks=state["session_locks"],
            background_search_tasks=state["background_search_tasks"],
            clear_tts_playback_tracking=lambda *, tracker, guild_id: cleared_tts.append(guild_id),
            tts_playback_tracker=object(),
            memory_locks=state["memory_locks"],
            cognitive_locks=state["cognitive_locks"],
            background_cognitive_tasks=state["background_cognitive_tasks"],
            autonomy_last_cognitive_refresh_at=state["autonomy_last_cognitive_refresh_at"],
            autonomy_cognitive_refresh_tasks=state["autonomy_cognitive_refresh_tasks"],
        )
        return deps, state

    def test_reset_removes_only_target_guild_state_and_cancels_live_tasks(self) -> None:
        deps, state = self.build_deps()

        reset_guild_runtime_state_from_runtime(7, deps=deps)

        self.assertEqual(state["session_histories"], {"guild:8:voice:42": []})
        self.assertEqual(state["session_followup_targets"], {"guild:8:voice:42": "target"})
        self.assertEqual(state["active_session_until"], {"guild:8:voice:42": 2.0})
        self.assertEqual(state["room_owner_user_ids"], {"guild:8:voice:42": 8})
        self.assertEqual(state["session_locks"], {"guild:8:voice:42": next(iter(state["session_locks"].values()))})
        self.assertEqual(list(state["room_last_voice_utterance_for_merge"].keys()), ["room-b"])
        self.assertEqual(state["memory_locks"], {8: state["memory_locks"][8]})
        self.assertEqual(state["cognitive_locks"], {8: state["cognitive_locks"][8]})
        self.assertEqual(state["autonomy_last_cognitive_refresh_at"], {8: 2.0})
        self.assertEqual(state["registry"].cancelled_prefixes, ["guild:7:"])
        self.assertEqual(state["cleared_tts"], [7])
        self.assertTrue(state["tasks"]["search"].cancelled)
        self.assertFalse(state["tasks"]["done_search"].cancelled)
        self.assertTrue(state["tasks"]["cognitive"].cancelled)
        self.assertTrue(state["tasks"]["refresh"].cancelled)

    def test_reset_removes_orphaned_state_without_active_or_owner_anchor(self) -> None:
        deps, state = self.build_deps()
        guild_key = "guild:7:voice:42"
        state["active_session_until"].pop(guild_key)
        state["room_owner_user_ids"].pop(guild_key)

        reset_guild_runtime_state_from_runtime(7, deps=deps)

        prefixed_mappings = (
            state["session_histories"],
            state["session_followup_targets"],
            state["active_session_until"],
            state["active_session_user_ids"],
            state["session_last_active_at"],
            state["session_awaiting_user_reply"],
            state["session_last_speaker"],
            state["session_topic_ids"],
            state["session_turn_ids"],
            state["session_segment_counters"],
            state["session_last_turn_accepted_at"],
            state["session_last_stt_text"],
            state["session_partial_stt_text"],
            state["session_committed_stt_text"],
            state["session_bad_audio_counts"],
            state["room_owner_user_ids"],
            state["room_owner_until"],
            state["room_reply_in_progress"],
            state["room_last_voice_reply_at"],
            state["session_locks"],
            state["background_search_tasks"],
            state["background_cognitive_tasks"],
        )
        for mapping in prefixed_mappings:
            self.assertFalse(
                any(
                    isinstance(key, str)
                    and key.startswith("guild:7:")
                    for key in mapping
                ),
                mapping,
            )
        self.assertFalse(
            any(
                getattr(record, "session_key", "").startswith("guild:7:")
                for record in state["room_last_voice_utterance_for_merge"].values()
            )
        )


def test_build_guild_runtime_reset_deps_identity() -> None:
    deps = build_guild_runtime_reset_deps(
        session_histories={},
        session_followup_targets={},
        active_session_until={},
        active_session_user_ids={},
        session_last_active_at={},
        session_awaiting_user_reply={},
        session_last_speaker={},
        session_topic_ids={},
        session_turn_ids={},
        session_segment_counters={},
        session_last_turn_accepted_at={},
        session_last_stt_text={},
        room_last_voice_utterance_for_merge={},
        session_partial_stt_text={},
        session_committed_stt_text={},
        session_bad_audio_counts={},
        room_owner_user_ids={},
        room_owner_until={},
        room_reply_in_progress={},
        room_last_voice_reply_at={},
        turn_scope_registry=FakeTurnScopeRegistry(),
        session_locks={},
        background_search_tasks={},
        clear_tts_playback_tracking=lambda *, tracker, guild_id: None,
        tts_playback_tracker=object(),
        memory_locks={},
        cognitive_locks={},
        background_cognitive_tasks={},
        autonomy_last_cognitive_refresh_at={},
        autonomy_cognitive_refresh_tasks={},
    )
    assert isinstance(deps, GuildRuntimeResetDeps)


if __name__ == "__main__":
    unittest.main()
