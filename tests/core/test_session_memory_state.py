from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


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
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    memory_receipt_ref_from_receipt,
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.conversation_memory_exposure import (  # noqa: E402
    filter_conversation_history_for_memory_exposure,
)
from evelyn_core.context_pipeline import has_unanswered_user_turn  # noqa: E402
from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core import memory_exposure  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


NOTE_A = "concept-0123456789abcdef"
NOTE_STALE = "concept-1111111111111111"
NOTE_TOMBSTONED = "concept-fedcba9876543210"


def unattributed_ref() -> dict:
    return unattributed_memory_receipt_ref()


def bound_ref(note_id: str, *, memory_version: int) -> dict:
    return memory_receipt_ref_from_receipt(
        {
            "schema": "memory.context-receipt.v1",
            "state": "provided",
            "groundingState": "attributed",
            "memoryVersion": memory_version,
            "suppliedNoteIds": [note_id],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }
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
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory_index_dir = (
            Path(self.temp_dir.name) / "memory_index"
        )
        memory_exposure.reset_memory_exposure_position()

    def tearDown(self) -> None:
        memory_exposure.reset_memory_exposure_position()
        self.temp_dir.cleanup()

    @contextmanager
    def unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    def write_memory_version(self, version: int) -> None:
        self.memory_index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(
                self.memory_index_dir
                / memory_exposure.MEMORY_INDEX_DB_NAME
            )
        )
        try:
            connection.execute(
                """
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def tombstone(note_id: str, *, second: int = 0) -> dict[str, object]:
        return {
            "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
            "noteId": note_id,
            "noteType": "concept",
            "sourceType": "conversation",
            "reason": "privacy_request",
            "deletedAt": f"2026-08-01T00:00:{second:02d}Z",
        }

    def test_create_empty_owns_independent_backing_maps(self) -> None:
        first = SessionStateStore.create_empty()
        second = SessionStateStore.create_empty()
        first.histories["room"] = []

        self.assertEqual(first.histories, {"room": []})
        self.assertEqual(second.histories, {})
        self.assertIsNot(first.histories, first.followup_targets)

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
                memory_receipt=not_used_memory_receipt_ref(),
            )

        history = store.get_conversation_history(system_prompt="system", guild_id=1)
        self.assertEqual(history[0], {"role": "system", "content": "system"})
        self.assertEqual(len(history), 5)
        self.assertEqual(history[1]["content"], "user 1")
        self.assertEqual(
            store.recent_assistant_reply_summary(
                system_prompt="system",
                memory_index_dir=self.memory_index_dir,
                guild_id=1,
                limit=2,
            ),
            "answer 1 / answer 2",
        )

    def test_unanswered_user_turn_is_preserved_without_fake_assistant_reply(
        self,
    ) -> None:
        store = make_store()

        store.append_history(
            "guild:1:voice:2:user:3",
            "내가 방금 부탁한 내용을 이어서 해줘",
            None,
            system_prompt="system",
            max_history_items=10,
        )

        history = store.get_conversation_history(
            system_prompt="system",
            session_key="guild:1:voice:2:user:3",
        )
        self.assertEqual(
            history,
            [
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": "내가 방금 부탁한 내용을 이어서 해줘",
                },
            ],
        )

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
        self.assertTrue(store.is_active_for_user("s1", 10, now_monotonic=219.0))
        self.assertFalse(store.is_active_for_user("s1", 10, now_monotonic=221.0))
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
            turn_id="claimed-turn-1",
            now_monotonic=5.0,
        )

        self.assertEqual(started.turn_id, "claimed-turn-1")
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
        self.assertEqual(
            history[-2:],
            [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": "answer",
                    "memoryReceiptRef": unattributed_ref(),
                },
            ],
        )
        self.assertEqual(finished.ttl_sec, 300.0)
        self.assertTrue(finished.awaiting_user_reply)
        self.assertEqual(store.active_until["s1"], 310.0)
        self.assertEqual(store.active_user_ids["s1"], 7)
        self.assertEqual(store.last_speaker["s1"], "assistant")
        self.assertEqual(store.topic_ids["s1"], "topic-1")

    def test_record_command_assistant_turn_tracks_followup_history_and_activity(self) -> None:
        store = make_store()

        finished = store.record_command_assistant_turn(
            "s1",
            "마크상태",
            "status reply",
            system_prompt="system",
            max_history_items=10,
            guild_id=1,
            user_id=7,
            channel_id=22,
            message_id=33,
            awaiting_user_reply=False,
            normal_ttl_sec=90.0,
            question_ttl_sec=300.0,
            now_monotonic=20.0,
        )

        self.assertFalse(finished.awaiting_user_reply)
        self.assertEqual(finished.ttl_sec, 90.0)
        self.assertEqual(store.followup_targets["s1"], {"channel_id": 22, "message_id": 33})
        history = store.get_conversation_history(
            system_prompt="system",
            session_key="s1",
            guild_id=1,
        )
        self.assertEqual(
            history[-2:],
            [
                {"role": "user", "content": "마크상태"},
                {
                    "role": "assistant",
                    "content": "status reply",
                    "memoryReceiptRef": not_used_memory_receipt_ref(),
                },
            ],
        )
        filtered = filter_conversation_history_for_memory_exposure(
            history,
            memory_index_dir=self.memory_index_dir,
        )
        self.assertEqual(
            [row.get("role") for row in filtered.messages],
            ["system", "user", "assistant"],
        )
        self.assertEqual(filtered.dropped_unattributed_count, 0)
        self.assertFalse(has_unanswered_user_turn(list(filtered.messages)))
        self.assertEqual(store.active_until["s1"], 110.0)
        self.assertEqual(store.topic_ids["s1"], build_topic_id("마크상태", "status reply"))

    def test_record_tool_assistant_turn_tracks_tool_history_and_reply_activity(self) -> None:
        store = make_store()

        finished = store.record_tool_assistant_turn(
            "s1",
            "메모리 열어줘",
            "응, 열게.",
            tool_name="memory.open_vault",
            system_prompt="system",
            max_history_items=10,
            guild_id=1,
            ttl_sec=90.0,
            now_monotonic=30.0,
        )

        history = store.get_conversation_history(system_prompt="system", session_key="s1", guild_id=1)
        self.assertEqual(
            history[-2:],
            [
                {"role": "user", "content": "메모리 열어줘"},
                {
                    "role": "assistant",
                    "content": "도구 실행: memory.open_vault 결과: 응, 열게.",
                    "memoryReceiptRef": unattributed_ref(),
                },
            ],
        )
        self.assertFalse(finished.awaiting_user_reply)
        self.assertEqual(finished.ttl_sec, 90.0)
        self.assertEqual(store.active_until["s1"], 120.0)
        self.assertFalse(store.awaiting_user_reply["s1"])
        self.assertEqual(store.topic_ids["s1"], build_topic_id("메모리 열어줘", "memory.open_vault", "응, 열게."))

    def test_followup_target_merges_partial_updates(self) -> None:
        store = make_store()

        store.remember_followup_target("s1", channel_id=1)
        store.remember_followup_target("s1", message_id=2)

        self.assertEqual(store.followup_targets["s1"], {"channel_id": 1, "message_id": 2})

    def test_assistant_history_always_has_compact_receipt_ref(self) -> None:
        store = make_store()
        full_receipt = {
            "schema": "memory.context-receipt.v1",
            "state": "provided",
            "groundingState": "attributed",
            "memoryVersion": 3,
            "suppliedNoteIds": [NOTE_A],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }

        store.append_history(
            "s1",
            "기억을 사용해 답해줘",
            "기억에 근거한 답",
            system_prompt="system",
            max_history_items=10,
            memory_receipt=full_receipt,
        )

        user_row, assistant_row = store.histories["s1"][-2:]
        self.assertNotIn("memoryReceiptRef", user_row)
        self.assertEqual(
            assistant_row["memoryReceiptRef"]["state"],
            "bound",
        )
        self.assertEqual(
            assistant_row["memoryReceiptRef"][
                "suppliedNoteIds"
            ],
            [NOTE_A],
        )

    def test_casual_status_question_detection(self) -> None:
        self.assertTrue(is_casual_call_or_status_question("이블린"))
        self.assertTrue(is_casual_call_or_status_question("뭐하고 있어?"))
        self.assertFalse(is_casual_call_or_status_question("오늘 할 일 정리해줘"))

    def test_persona_hint_uses_recent_reply_context(self) -> None:
        store = make_store()
        store.append_history(
            "s1",
            "user",
            "최근 답변",
            system_prompt="system",
            max_history_items=10,
            memory_receipt=not_used_memory_receipt_ref(),
        )

        hint = store.persona_state_hint_for_turn(
            "뭐해?",
            system_prompt="system",
            memory_index_dir=self.memory_index_dir,
            session_key="s1",
        )

        self.assertIn("호출/근황 질문", hint)
        self.assertIn("최근 답변", hint)

    def test_persona_hint_filters_unproven_stale_and_tombstoned_replies(
        self,
    ) -> None:
        store = make_store()
        self.write_memory_version(7)
        with self.unconfigured_authenticity():
            journal.append_memory_deletion_tombstone(
                self.memory_index_dir,
                self.tombstone(NOTE_TOMBSTONED),
            )
            store.histories["s1"] = [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "MISSING_CANARY"},
                {
                    "role": "assistant",
                    "content": "UNATTRIBUTED_CANARY",
                    "memoryReceiptRef": unattributed_ref(),
                },
                {
                    "role": "assistant",
                    "content": "STALE_CANARY",
                    "memoryReceiptRef": bound_ref(
                        NOTE_STALE,
                        memory_version=6,
                    ),
                },
                {
                    "role": "assistant",
                    "content": "TOMBSTONED_CANARY",
                    "memoryReceiptRef": bound_ref(
                        NOTE_TOMBSTONED,
                        memory_version=7,
                    ),
                },
                {
                    "role": "assistant",
                    "content": "SAFE_STATIC_REPLY",
                    "memoryReceiptRef": not_used_memory_receipt_ref(
                        memory_version=7
                    ),
                },
                {
                    "role": "assistant",
                    "content": "SAFE_BOUND_REPLY",
                    "memoryReceiptRef": bound_ref(
                        NOTE_A,
                        memory_version=7,
                    ),
                },
            ]

            hint = store.persona_state_hint_for_turn(
                "뭐해?",
                system_prompt="system",
                memory_index_dir=self.memory_index_dir,
                session_key="s1",
            )

        self.assertIn("SAFE_STATIC_REPLY", hint)
        self.assertIn("SAFE_BOUND_REPLY", hint)
        for canary in (
            "MISSING_CANARY",
            "UNATTRIBUTED_CANARY",
            "STALE_CANARY",
            "TOMBSTONED_CANARY",
            NOTE_A,
            NOTE_STALE,
            NOTE_TOMBSTONED,
            "memoryReceiptRef",
        ):
            self.assertNotIn(canary, hint)
        captured = memory_exposure.current_memory_exposure_position()
        self.assertIsNotNone(captured)
        self.assertEqual(captured.supplied_note_ids, (NOTE_A,))

    def test_persona_hint_boundary_rejects_delete_race_before_delivery(
        self,
    ) -> None:
        store = make_store()
        self.write_memory_version(7)
        store.histories["s1"] = [
            {"role": "system", "content": "system"},
            {
                "role": "assistant",
                "content": "BOUND_REPLY_CANARY",
                "memoryReceiptRef": bound_ref(
                    NOTE_A,
                    memory_version=7,
                ),
            },
        ]

        with self.unconfigured_authenticity():
            hint = store.persona_state_hint_for_turn(
                "이블린",
                system_prompt="system",
                memory_index_dir=self.memory_index_dir,
                session_key="s1",
            )
            captured = memory_exposure.current_memory_exposure_position()
            journal.append_memory_deletion_tombstone(
                self.memory_index_dir,
                self.tombstone(NOTE_A),
            )
            with self.assertRaises(
                journal.MemoryDeletionJournalIntegrityError
            ):
                with memory_exposure.memory_exposure_guard(
                    expected_position=captured,
                    required=True,
                    index_dir=self.memory_index_dir,
                ):
                    self.fail("stale persona hint reached delivery")

        self.assertIn("BOUND_REPLY_CANARY", hint)

    def test_persona_hint_delivery_lease_rejects_concurrent_delete(
        self,
    ) -> None:
        async def exercise() -> None:
            store = make_store()
            self.write_memory_version(7)
            store.histories["s1"] = [
                {"role": "system", "content": "system"},
                {
                    "role": "assistant",
                    "content": "LEASED_REPLY_CANARY",
                    "memoryReceiptRef": bound_ref(
                        NOTE_A,
                        memory_version=7,
                    ),
                },
            ]
            hint = store.persona_state_hint_for_turn(
                "뭐해?",
                system_prompt="system",
                memory_index_dir=self.memory_index_dir,
                session_key="s1",
            )
            captured = memory_exposure.current_memory_exposure_position()
            self.assertIn("LEASED_REPLY_CANARY", hint)

            with memory_exposure.memory_exposure_guard(
                expected_position=captured,
                required=True,
                index_dir=self.memory_index_dir,
            ):
                async def delete_now() -> None:
                    journal.append_memory_deletion_tombstone(
                        self.memory_index_dir,
                        self.tombstone(NOTE_A),
                    )

                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ):
                    await asyncio.create_task(delete_now())

            journal.append_memory_deletion_tombstone(
                self.memory_index_dir,
                self.tombstone(NOTE_A),
            )

        with self.unconfigured_authenticity():
            asyncio.run(exercise())

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
