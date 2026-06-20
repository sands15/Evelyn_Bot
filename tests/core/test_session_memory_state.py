from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.session_memory_state import (  # noqa: E402
    SessionStateStore,
    build_topic_id,
    is_casual_call_or_status_question,
    new_conversation_history,
    runtime_session_key,
)


def make_store() -> SessionStateStore:
    return SessionStateStore(
        histories={},
        followup_targets={},
        active_until={},
        active_user_ids={},
        last_active_at={},
        awaiting_user_reply={},
        last_speaker={},
        topic_ids={},
        turn_ids={},
        segment_counters={},
        last_turn_accepted_at={},
        last_stt_text={},
        partial_stt_text={},
        committed_stt_text={},
        bad_audio_counts={},
    )


class SessionMemoryStateTests(unittest.TestCase):
    def test_runtime_session_key_falls_back_to_guild_default(self) -> None:
        self.assertEqual(runtime_session_key(session_key="custom", guild_id=1), "custom")
        self.assertEqual(runtime_session_key(guild_id=42), "guild:42:default")
        self.assertIsNone(runtime_session_key())

    def test_history_is_created_and_trimmed_per_resolved_session(self) -> None:
        store = make_store()

        history = store.get_conversation_history(system_prompt="system", guild_id=1)
        self.assertEqual(history, new_conversation_history("system"))

        for index in range(3):
            store.append_history(
                "guild:1:default",
                f"user {index}",
                f"answer {index}",
                system_prompt="system",
                max_history_items=4,
            )

        history = store.get_conversation_history(system_prompt="system", guild_id=1)
        self.assertEqual(history[0], {"role": "system", "content": "system"})
        self.assertEqual(len(history), 5)
        self.assertEqual(history[1]["content"], "user 1")
        self.assertEqual(store.recent_assistant_reply_summary(system_prompt="system", guild_id=1, limit=2), "answer 1 / answer 2")

    def test_session_state_lifecycle_mutates_backing_maps(self) -> None:
        store = make_store()

        store.update_session_state(
            "s1",
            user_id=10,
            speaker="assistant",
            awaiting_user_reply=True,
            user_text="hello",
            answer_text="world",
            active_conversation_awaiting_reply_sec=120.0,
            now_monotonic=100.0,
        )

        self.assertEqual(store.active_until["s1"], 220.0)
        self.assertEqual(store.last_active_at["s1"], 100.0)
        self.assertEqual(store.active_user_ids["s1"], 10)
        self.assertEqual(store.last_speaker["s1"], "assistant")
        self.assertTrue(store.awaiting_user_reply["s1"])
        self.assertEqual(store.topic_ids["s1"], build_topic_id("hello", "world"))
        self.assertTrue(store.snapshot("s1")["turn_id"])
        self.assertTrue(store.is_active_for_user("s1", 10, now_monotonic=221.0))
        self.assertFalse(store.is_active_for_user("s1", 11, now_monotonic=101.0))

        store.update_session_state(
            "s1",
            awaiting_user_reply=False,
            ttl_sec=5.0,
            active_conversation_awaiting_reply_sec=120.0,
            now_monotonic=200.0,
        )
        self.assertTrue(store.is_active_for_user("s1", 10, now_monotonic=204.0))
        self.assertFalse(store.is_active_for_user("s1", 10, now_monotonic=206.0))

    def test_turn_and_bad_audio_helpers(self) -> None:
        store = make_store()

        self.assertEqual(store.next_segment_id(None), 1)
        self.assertEqual(store.next_segment_id("s1"), 1)
        self.assertEqual(store.next_segment_id("s1"), 2)
        self.assertEqual(store.start_new_turn("s1", turn_id="turn-1", now_monotonic=5.0), "turn-1")
        self.assertEqual(store.current_turn_id("s1"), "turn-1")
        self.assertEqual(store.last_turn_accepted_at["s1"], 5.0)
        self.assertEqual(store.increment_bad_audio("s1"), 1)
        self.assertEqual(store.increment_bad_audio("s1"), 2)
        store.reset_bad_audio("s1")
        self.assertEqual(store.bad_audio_counts["s1"], 0)

    def test_begin_user_text_turn_starts_turn_and_ensures_history(self) -> None:
        store = make_store()

        started = store.begin_user_text_turn(
            "s1",
            "hello",
            system_prompt="system",
            active_conversation_awaiting_reply_sec=120.0,
            max_history_items=10,
            guild_id=1,
            user_id=7,
            previous_topic_id="old-topic",
            now_monotonic=5.0,
        )

        self.assertEqual(started.turn_id, store.current_turn_id("s1"))
        self.assertEqual(started.topic_id, build_topic_id("hello", "old-topic"))
        self.assertEqual(started.history, [{"role": "system", "content": "system"}])
        self.assertEqual(store.active_user_ids["s1"], 7)
        self.assertEqual(store.last_speaker["s1"], "user")
        self.assertFalse(store.awaiting_user_reply["s1"])
        self.assertEqual(store.last_turn_accepted_at["s1"], 5.0)

    def test_finish_assistant_text_turn_appends_history_and_marks_active(self) -> None:
        store = make_store()

        finished = store.finish_assistant_text_turn(
            "s1",
            "hello",
            "answer",
            system_prompt="system",
            max_history_items=10,
            guild_id=1,
            user_id=7,
            awaiting_user_reply=True,
            normal_ttl_sec=90.0,
            question_ttl_sec=300.0,
            topic_id="topic-1",
            now_monotonic=10.0,
        )

        history = store.get_conversation_history(system_prompt="system", session_key="s1", guild_id=1)
        self.assertEqual(history[-2:], [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "answer"}])
        self.assertEqual(finished.ttl_sec, 300.0)
        self.assertTrue(finished.awaiting_user_reply)
        self.assertEqual(store.active_until["s1"], 310.0)
        self.assertEqual(store.active_user_ids["s1"], 7)
        self.assertEqual(store.last_speaker["s1"], "assistant")
        self.assertEqual(store.topic_ids["s1"], "topic-1")

    def test_followup_target_merges_partial_updates(self) -> None:
        store = make_store()

        store.remember_followup_target("s1", channel_id=1)
        store.remember_followup_target("s1", message_id=2)

        self.assertEqual(store.followup_targets["s1"], {"channel_id": 1, "message_id": 2})

    def test_casual_status_question_detection(self) -> None:
        self.assertTrue(is_casual_call_or_status_question("이블린"))
        self.assertTrue(is_casual_call_or_status_question("뭐하고 있어?"))
        self.assertFalse(is_casual_call_or_status_question("오늘 할 일 정리해줘"))

    def test_persona_hint_uses_recent_reply_context(self) -> None:
        store = make_store()
        store.append_history("s1", "user", "최근 답변", system_prompt="system", max_history_items=10)

        hint = store.persona_state_hint_for_turn("뭐해?", system_prompt="system", session_key="s1")

        self.assertIn("호출/근황 질문", hint)
        self.assertIn("최근 답변", hint)

    def test_recent_history_for_router_formats_role_lines(self) -> None:
        store = make_store()
        store.append_history("s1", "hello", "answer", system_prompt="system", max_history_items=10)
        store.append_history("s1", "follow up", "second answer", system_prompt="system", max_history_items=10)

        rendered = store.recent_history_for_router(
            system_prompt="system",
            session_key="s1",
            limit=3,
            max_content_chars=8,
        )

        self.assertEqual(rendered, "assistant: answer\nuser: follow u\nassistant: second a")


if __name__ == "__main__":
    unittest.main()
