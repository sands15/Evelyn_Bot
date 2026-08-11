from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

if "numpy" not in sys.modules:
    class _DummyNdArray:
        pass

    sys.modules["numpy"] = types.SimpleNamespace(
        ndarray=_DummyNdArray,
    )

from evelyn_core.voice_reply_side_effects import (  # noqa: E402
    VoiceReplySideEffectDeps,
    checkpoint_accepted_voice_turn_from_runtime,
    current_voice_reply_memory_boundary,
    finalize_voice_reply_side_effects_from_runtime,
)
from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
    not_used_memory_receipt_ref,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MEMORY_INDEX_DB_NAME,
    MemoryExposurePosition,
    capture_memory_exposure_position,
    reset_memory_exposure_position,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)
from evelyn_core.voice_orchestration import (  # noqa: E402
    finalize_delivered_voice_reply,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402


@dataclass(frozen=True)
class FakeMember:
    id: int = 42
    display_name: str = "정훈"


@dataclass(frozen=True)
class FakeVoiceReply:
    history_user_text: str = "검색해줘"
    topic_id: str = "topic-1"


class VoiceReplySideEffectsTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_memory_exposure_position()

    def test_accepted_voice_checkpoint_marks_and_skips_same_execution_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calls: list[str] = []
            deps = self._boundary_deps(
                index_dir=Path(temp_dir),
                calls=calls,
            )
            deps = replace(
                deps,
                begin_user_only_turn=lambda *_args, **_kwargs: calls.append(
                    "begin"
                ),
                commit_session_continuity=lambda *_args, **_kwargs: (
                    calls.append("commit")
                    or durable_continuity_status(7)
                ),
            )
            metrics: dict[str, Any] = {"meta": {}}

            for _ in range(2):
                checkpoint_accepted_voice_turn_from_runtime(
                    session_key="session-1",
                    user_id=42,
                    user_text="계속해 줘",
                    accepted_turn_id="turn-1",
                    ttl_sec=30.0,
                    topic_id="topic-1",
                    metrics=metrics,
                    deps=deps,
                )

        self.assertEqual(calls, ["begin", "commit"])
        self.assertEqual(
            metrics["meta"]["continuity_turn_state"],
            "unanswered_user",
        )
        self.assertEqual(metrics["meta"]["continuity_generation"], 7)
        self.assertTrue(metrics["meta"]["accepted_voice_turn_precommitted"])

    def test_accepted_voice_turn_commit_failure_is_fixed_and_content_free(
        self,
    ) -> None:
        private_user_canary = "PRIVATE_ACCEPTED_VOICE_TEXT"
        private_error_canary = "PRIVATE_ACCEPTED_VOICE_PATH"
        logs: list[str] = []

        def fail_commit(*_args: Any, **_kwargs: Any) -> None:
            raise OSError(private_error_canary)

        with tempfile.TemporaryDirectory() as temp_dir:
            deps = self._boundary_deps(
                index_dir=Path(temp_dir),
                calls=[],
            )
            deps = replace(
                deps,
                commit_session_continuity=fail_commit,
                log=lambda *parts: logs.append("".join(map(str, parts))),
            )
            metrics: dict[str, Any] = {"meta": {}}

            with self.assertRaisesRegex(
                RuntimeError,
                "conversation_continuity_commit_failed",
            ):
                checkpoint_accepted_voice_turn_from_runtime(
                    session_key="session-1",
                    user_id=42,
                    user_text=private_user_canary,
                    accepted_turn_id="turn-1",
                    ttl_sec=30.0,
                    topic_id="topic-1",
                    metrics=metrics,
                    deps=deps,
                )

        self.assertEqual(
            metrics["meta"]["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        for private_canary in (private_user_canary, private_error_canary):
            self.assertNotIn(private_canary, str(metrics))
            self.assertNotIn(private_canary, "".join(logs))

    def test_precommitted_success_adds_only_the_assistant_tail(self) -> None:
        store = SessionStateStore.create_empty()
        store.begin_user_only_turn(
            "session-1",
            "계속해 줘",
            turn_id="turn-1",
            system_prompt="system",
            max_history_items=12,
            user_id=42,
            ttl_sec=30.0,
            topic_id="topic-1",
            active_conversation_awaiting_reply_sec=120.0,
        )

        def append_history(
            session_key: str,
            user_text: str,
            answer: str | None,
            *,
            memory_receipt: Any = None,
            complete_turn_id: str | None = None,
            **_kwargs: Any,
        ) -> None:
            store.append_history(
                session_key,
                user_text,
                answer,
                system_prompt="system",
                max_history_items=12,
                memory_receipt=memory_receipt,
                complete_turn_id=complete_turn_id,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            deps = self._boundary_deps(
                index_dir=Path(temp_dir),
                calls=[],
            )
            deps = replace(
                deps,
                append_history=append_history,
                session_state_snapshot=lambda *_args, **_kwargs: {
                    "awaiting_user_reply": False
                },
            )
            metrics: dict[str, Any] = {
                "meta": {
                    "accepted_voice_turn_precommitted": True,
                    "unanswered_voice_turn_recorded": True,
                }
            }
            for _ in range(2):
                finalize_voice_reply_side_effects_from_runtime(
                    guild_id=7,
                    member=FakeMember(),
                    session_key="session-1",
                    room_session_key="room-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    voice_reply=FakeVoiceReply(
                        history_user_text="계속해 줘"
                    ),
                    plain_answer="완료했어",
                    metrics=metrics,
                    turn_scope="scope",
                    accepted_turn_id="turn-1",
                    segment_id=1,
                    memory_exposure_position=None,
                    memory_receipt=not_used_memory_receipt_ref(),
                    deps=deps,
                )

        self.assertEqual(
            [row["role"] for row in store.histories["session-1"]],
            ["system", "user", "assistant"],
        )
        self.assertFalse(metrics["meta"]["unanswered_voice_turn_recorded"])

    def test_precommitted_failure_keeps_the_existing_user_tail(self) -> None:
        store = SessionStateStore.create_empty()
        store.begin_user_only_turn(
            "session-1",
            "계속해 줘",
            turn_id="turn-1",
            system_prompt="system",
            max_history_items=12,
            user_id=42,
            ttl_sec=30.0,
            topic_id="topic-1",
            active_conversation_awaiting_reply_sec=120.0,
        )

        def unexpected(*_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("precommitted failure must not write again")

        with tempfile.TemporaryDirectory() as temp_dir:
            deps = self._boundary_deps(
                index_dir=Path(temp_dir),
                calls=[],
            )
            deps = replace(
                deps,
                append_history=unexpected,
                commit_session_continuity=unexpected,
                mark_session_active=unexpected,
                set_room_owner=unexpected,
            )
            metrics: dict[str, Any] = {
                "meta": {
                    "accepted_voice_turn_precommitted": True,
                    "unanswered_voice_turn_recorded": True,
                }
            }
            finalize_voice_reply_side_effects_from_runtime(
                guild_id=7,
                member=FakeMember(),
                session_key="session-1",
                room_session_key="room-1",
                room_key=None,
                person_key=None,
                session_memory_key=None,
                voice_reply=FakeVoiceReply(
                    history_user_text="계속해 줘"
                ),
                plain_answer="",
                metrics=metrics,
                turn_scope="scope",
                accepted_turn_id="turn-1",
                segment_id=1,
                delivery_succeeded=False,
                failure_code="voice_delivery_failed",
                deps=deps,
            )

        self.assertEqual(
            [row["role"] for row in store.histories["session-1"]],
            ["system", "user"],
        )

    @staticmethod
    def _bound_receipt(
        *,
        version: int,
        note_id: str,
    ) -> dict[str, Any]:
        return {
            "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
            "state": "bound",
            "memoryVersion": version,
            "suppliedNoteIds": [note_id],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }

    @staticmethod
    def _write_memory_version(index_dir: Path, version: int) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(index_dir / MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(key TEXT PRIMARY KEY, value NOT NULL)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) "
                "VALUES(?, ?)",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @contextmanager
    def _unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    def test_failed_delivery_durably_records_only_unanswered_user_turn(
        self,
    ) -> None:
        calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        def record(name: str, result: Any = None):
            def _inner(*args: Any, **kwargs: Any) -> Any:
                calls.append((name, args, kwargs))
                return result

            return _inner

        def unexpected(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                "failed delivery must not schedule memory or search"
            )

        deps = VoiceReplySideEffectDeps(
            session_speculative_policies={"session-1": {}},
            append_history=record("append_history"),
            begin_user_only_turn=lambda *_args, **_kwargs: None,
            compute_runtime_mode=unexpected,
            record_context_pipeline_benchmark=unexpected,
            schedule_memory_update=unexpected,
            read_cached_cognitive_state=unexpected,
            apply_ask_gating=unexpected,
            schedule_search_followup=unexpected,
            session_state_snapshot=unexpected,
            mark_session_active=record("mark_session_active"),
            set_room_owner=record("set_room_owner"),
            commit_session_continuity=record(
                "commit_session_continuity",
                durable_continuity_status(9),
            ),
            log=record("log"),
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
        )
        metrics: dict[str, Any] = {"meta": {}}
        kwargs = {
            "guild_id": 7,
            "member": FakeMember(),
            "session_key": "session-1",
            "room_session_key": "room-1",
            "room_key": "room-key",
            "person_key": "person-key",
            "session_memory_key": "session-memory",
            "voice_reply": FakeVoiceReply(
                history_user_text="실패해도 이 부탁은 이어가줘"
            ),
            "plain_answer": "",
            "metrics": metrics,
            "turn_scope": "scope",
            "accepted_turn_id": "turn-failed-1",
            "segment_id": 5,
            "delivery_succeeded": False,
            "failure_code": "Bearer private-token C:\\private",
            "deps": deps,
        }

        finalize_voice_reply_side_effects_from_runtime(**kwargs)
        finalize_voice_reply_side_effects_from_runtime(**kwargs)

        by_name = {
            name: (args, call_kwargs)
            for name, args, call_kwargs in calls
        }
        self.assertEqual(
            [name for name, _args, _kwargs in calls],
            [
                "append_history",
                "mark_session_active",
                "set_room_owner",
                "commit_session_continuity",
            ],
        )
        continuity_call = next(
            row
            for row in calls
            if row[0] == "commit_session_continuity"
        )
        self.assertEqual(
            continuity_call[1],
            ("session-1", "turn-failed-1"),
        )
        self.assertEqual(
            by_name["append_history"][0],
            (
                "session-1",
                "실패해도 이 부탁은 이어가줘",
                None,
            ),
        )
        self.assertEqual(
            by_name["mark_session_active"][1]["speaker"],
            "user",
        )
        self.assertFalse(
            by_name["mark_session_active"][1][
                "awaiting_user_reply"
            ]
        )
        self.assertEqual(
            metrics["meta"]["voice_delivery_error"],
            "voice_delivery_failed",
        )
        self.assertEqual(
            metrics["meta"]["continuity_turn_state"],
            "unanswered_user",
        )
        self.assertEqual(
            metrics["meta"]["error"],
            "voice_delivery_failed",
        )
        self.assertEqual(
            metrics["meta"]["error_layer"],
            "voice_delivery",
        )
        self.assertEqual(metrics["meta"]["continuity_commit"], "durable")
        self.assertEqual(metrics["meta"]["continuity_generation"], 9)
        self.assertNotIn("private-token", str(metrics))
        self.assertNotIn("C:\\private", str(metrics))

    def test_failed_delivery_commit_error_is_content_free(self) -> None:
        metrics: dict[str, Any] = {"meta": {}}
        logs: list[tuple[Any, ...]] = []

        def unexpected(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("unexpected normal delivery side effect")

        deps = VoiceReplySideEffectDeps(
            session_speculative_policies={},
            append_history=lambda *_args, **_kwargs: None,
            begin_user_only_turn=lambda *_args, **_kwargs: None,
            compute_runtime_mode=unexpected,
            record_context_pipeline_benchmark=unexpected,
            schedule_memory_update=unexpected,
            read_cached_cognitive_state=unexpected,
            apply_ask_gating=unexpected,
            schedule_search_followup=unexpected,
            session_state_snapshot=unexpected,
            mark_session_active=lambda *_args, **_kwargs: None,
            set_room_owner=lambda *_args, **_kwargs: None,
            commit_session_continuity=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("Bearer private-token C:\\private")
            ),
            log=lambda *args: logs.append(args),
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
        )

        finalize_voice_reply_side_effects_from_runtime(
            guild_id=7,
            member=FakeMember(),
            session_key="session-1",
            room_session_key="room-1",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            voice_reply=FakeVoiceReply(),
            plain_answer="",
            metrics=metrics,
            turn_scope="scope",
            accepted_turn_id="turn-failed-1",
            segment_id=1,
            delivery_succeeded=False,
            failure_code="voice_delivery_failed",
            deps=deps,
        )

        self.assertEqual(metrics["meta"]["continuity_commit"], "failed")
        self.assertEqual(
            metrics["meta"]["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn("private-token", str(metrics))
        self.assertNotIn("private-token", str(logs))
        self.assertIn("RuntimeError", str(logs))

    def test_records_memory_search_and_session_side_effects(self) -> None:
        speculative = {"session-1": {"text": "old"}}
        calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

        def record(name: str):
            def _inner(*args: Any, **kwargs: Any) -> Any:
                calls.append((name, args, kwargs))
                if name == "schedule_memory_update":
                    return {"decision": "queued"}
                if name == "read_cached_cognitive_state":
                    return {"action": "search_then_answer"}
                if name == "apply_ask_gating":
                    return dict(args[0])
                if name == "session_state_snapshot":
                    return {"awaiting_user_reply": True}
                if name == "compute_runtime_mode":
                    return "normal"
                if name == "commit_session_continuity":
                    return durable_continuity_status(4)
                return None

            return _inner

        deps = VoiceReplySideEffectDeps(
            session_speculative_policies=speculative,
            append_history=record("append_history"),
            begin_user_only_turn=lambda *_args, **_kwargs: None,
            compute_runtime_mode=record("compute_runtime_mode"),
            record_context_pipeline_benchmark=record("record_context_pipeline_benchmark"),
            schedule_memory_update=record("schedule_memory_update"),
            read_cached_cognitive_state=record("read_cached_cognitive_state"),
            apply_ask_gating=record("apply_ask_gating"),
            schedule_search_followup=record("schedule_search_followup"),
            session_state_snapshot=record("session_state_snapshot"),
            mark_session_active=record("mark_session_active"),
            set_room_owner=record("set_room_owner"),
            commit_session_continuity=record(
                "commit_session_continuity"
            ),
            log=record("log"),
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
        )
        metrics: dict[str, Any] = {"meta": {}}

        finalize_voice_reply_side_effects_from_runtime(
            guild_id=7,
            member=FakeMember(),
            session_key="session-1",
            room_session_key="room-1",
            room_key="room-key",
            person_key="person-key",
            session_memory_key="session-memory",
            voice_reply=FakeVoiceReply(),
            plain_answer="답변",
            metrics=metrics,
            turn_scope="scope",
            accepted_turn_id="turn-1",
            segment_id=5,
            memory_exposure_position=None,
            memory_receipt=not_used_memory_receipt_ref(),
            deps=deps,
        )

        self.assertNotIn("session-1", speculative)
        self.assertEqual(metrics["meta"]["memory_writer_decision"], {"decision": "queued"})

        by_name = {name: (args, kwargs) for name, args, kwargs in calls}
        self.assertEqual(by_name["append_history"][0], ("session-1", "검색해줘", "답변"))
        self.assertEqual(by_name["schedule_memory_update"][1]["user_speaker"], "정훈")
        self.assertEqual(by_name["schedule_memory_update"][1]["runtime_mode"], "normal")
        self.assertTrue(by_name["schedule_search_followup"][1]["force"])
        self.assertEqual(by_name["schedule_search_followup"][1]["source"], "search-followup-voice")
        self.assertEqual(
            by_name["schedule_search_followup"][1][
                "continuity_generation"
            ],
            4,
        )
        self.assertEqual(by_name["mark_session_active"][1]["ttl_sec"], 12.0)
        self.assertTrue(by_name["mark_session_active"][1]["awaiting_user_reply"])
        self.assertEqual(by_name["set_room_owner"][0], ("room-1", 42))
        self.assertEqual(by_name["set_room_owner"][1]["ttl_sec"], 30.0)
        self.assertEqual(by_name["set_room_owner"][1]["turn_id"], "turn-1")
        self.assertIn("commit_session_continuity", by_name)
        self.assertEqual(
            by_name["commit_session_continuity"][0],
            ("session-1", "turn-1"),
        )
        self.assertEqual(
            metrics["meta"]["continuity_commit"],
            "durable",
        )
        self.assertEqual(
            metrics["meta"]["continuity_generation"],
            4,
        )
        self.assertLess(
            [name for name, _args, _kwargs in calls].index(
                "set_room_owner"
            ),
            [name for name, _args, _kwargs in calls].index(
                "commit_session_continuity"
            ),
        )
        self.assertLess(
            [name for name, _args, _kwargs in calls].index(
                "commit_session_continuity"
            ),
            [name for name, _args, _kwargs in calls].index(
                "schedule_search_followup"
            ),
        )

    def test_commit_failure_is_observable_without_losing_delivered_turn(
        self,
    ) -> None:
        metrics: dict[str, Any] = {"meta": {}}
        calls: list[str] = []

        def noop(*_args: Any, **_kwargs: Any) -> Any:
            return None

        def commit(*_args: Any) -> None:
            raise RuntimeError(
                "Bearer continuity-secret C:\\private"
            )

        deps = VoiceReplySideEffectDeps(
            session_speculative_policies={},
            append_history=lambda *_args, **_kwargs: calls.append(
                "append"
            ),
            begin_user_only_turn=lambda *_args, **_kwargs: None,
            compute_runtime_mode=lambda _metrics: "normal",
            record_context_pipeline_benchmark=noop,
            schedule_memory_update=lambda *_args, **_kwargs: {},
            read_cached_cognitive_state=lambda *_args, **_kwargs: {},
            apply_ask_gating=lambda state, **_kwargs: state,
            schedule_search_followup=noop,
            session_state_snapshot=lambda _key: {
                "awaiting_user_reply": False
            },
            mark_session_active=lambda *_args, **_kwargs: calls.append(
                "active"
            ),
            set_room_owner=lambda *_args, **_kwargs: calls.append(
                "owner"
            ),
            commit_session_continuity=commit,
            log=lambda *_args: calls.append("log"),
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
        )

        finalize_voice_reply_side_effects_from_runtime(
            guild_id=7,
            member=FakeMember(),
            session_key="session-1",
            room_session_key="room-1",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            voice_reply=FakeVoiceReply(),
            plain_answer="답변",
            metrics=metrics,
            turn_scope="scope",
            accepted_turn_id="turn-1",
            segment_id=1,
            memory_exposure_position=None,
            memory_receipt=not_used_memory_receipt_ref(),
            deps=deps,
        )

        self.assertEqual(calls[:3], ["append", "active", "owner"])
        self.assertEqual(
            metrics["meta"]["continuity_commit"],
            "failed",
        )
        self.assertEqual(
            metrics["meta"]["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertIn("log", calls)

    def test_partial_commit_status_is_observable_failure(
        self,
    ) -> None:
        metrics: dict[str, Any] = {"meta": {}}
        private = (
            "Bearer voice-continuity-secret "
            "https://internal.example/private"
        )

        def noop(*_args: Any, **_kwargs: Any) -> Any:
            return None

        deps = VoiceReplySideEffectDeps(
            session_speculative_policies={},
            append_history=noop,
            begin_user_only_turn=noop,
            compute_runtime_mode=lambda _metrics: "normal",
            record_context_pipeline_benchmark=noop,
            schedule_memory_update=lambda *_args, **_kwargs: {},
            read_cached_cognitive_state=lambda *_args, **_kwargs: {},
            apply_ask_gating=lambda state, **_kwargs: state,
            schedule_search_followup=noop,
            session_state_snapshot=lambda _key: {
                "awaiting_user_reply": False
            },
            mark_session_active=noop,
            set_room_owner=noop,
            commit_session_continuity=lambda *_args: {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            },
            log=noop,
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
        )

        finalize_voice_reply_side_effects_from_runtime(
            guild_id=7,
            member=FakeMember(),
            session_key="session-1",
            room_session_key="room-1",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            voice_reply=FakeVoiceReply(),
            plain_answer="답변",
            metrics=metrics,
            turn_scope="scope",
            accepted_turn_id="turn-1",
            segment_id=1,
            memory_exposure_position=None,
            memory_receipt=not_used_memory_receipt_ref(),
            deps=deps,
        )

        self.assertEqual(
            metrics["meta"]["continuity_commit"],
            "failed",
        )
        self.assertNotIn(private, str(metrics))

    def test_explicit_memory_confirmation_skips_duplicate_memory_and_search(self) -> None:
        metrics: dict[str, Any] = {
            "meta": {
                "runtime_mode": "normal",
                "memory_write_receipt": {
                    "schema": "memory.user-confirmation.v1",
                    "state": "stored",
                    "noteId": "concept-0123456789abcdef",
                    "sourceRef": (
                        "turn:opaque-turn-"
                        + ("a" * 64)
                        + ":user"
                    ),
                    "confirmedAt": "2026-07-31T00:00:00+00:00",
                    "contentFree": True,
                },
            }
        }
        calls: list[str] = []

        def unexpected(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                "normal memory/search path must be skipped"
            )

        deps = VoiceReplySideEffectDeps(
            session_speculative_policies={},
            append_history=(
                lambda *_args, **_kwargs: calls.append("append")
            ),
            begin_user_only_turn=lambda *_args, **_kwargs: None,
            compute_runtime_mode=unexpected,
            record_context_pipeline_benchmark=unexpected,
            schedule_memory_update=unexpected,
            read_cached_cognitive_state=unexpected,
            apply_ask_gating=unexpected,
            schedule_search_followup=unexpected,
            session_state_snapshot=(
                lambda _key: {"awaiting_user_reply": False}
            ),
            mark_session_active=(
                lambda *_args, **_kwargs: calls.append("active")
            ),
            set_room_owner=(
                lambda *_args, **_kwargs: calls.append("owner")
            ),
            commit_session_continuity=(
                lambda *_args: calls.append("commit")
                or durable_continuity_status(8)
            ),
            log=lambda *_args: calls.append("log"),
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
        )

        finalize_voice_reply_side_effects_from_runtime(
            guild_id=7,
            member=FakeMember(),
            session_key="session-1",
            room_session_key="room-1",
            room_key="room-key",
            person_key="person-key",
            session_memory_key="session-memory",
            voice_reply=FakeVoiceReply(
                history_user_text=(
                    "기억해줘: 나는 비 오는 날 산책을 좋아해"
                )
            ),
            plain_answer="지금 요청을 근거로 새 기억에 저장했어.",
            metrics=metrics,
            turn_scope="scope",
            accepted_turn_id="turn-voice-1",
            segment_id=5,
            memory_exposure_position=None,
            memory_receipt=not_used_memory_receipt_ref(),
            deps=deps,
        )

        self.assertEqual(
            calls,
            ["append", "active", "owner", "commit"],
        )
        self.assertEqual(
            metrics["meta"]["memory_writer_decision"]["reason"],
            "explicit_user_confirmation",
        )
        self.assertFalse(
            metrics["meta"]["memory_writer_decision"][
                "write_raw_transcript"
            ]
        )
        self.assertEqual(
            metrics["meta"]["continuity_generation"],
            8,
        )

    def test_orchestration_forwards_exact_reply_boundary(self) -> None:
        note_id = "concept-0123456789abcdef"
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self._write_memory_version(index_dir, 1)
            with self._unconfigured_authenticity():
                position = MemoryExposurePosition(
                    deletion_position=(
                        journal.memory_deletion_journal_position(
                            index_dir
                        )
                    ),
                    memory_version=1,
                    supplied_note_ids=(note_id,),
                )
                capture_memory_exposure_position(position)
                receipt = self._bound_receipt(
                    version=1,
                    note_id=note_id,
                )
                captured: dict[str, Any] = {}
                metrics: dict[str, Any] = {
                    "meta": {"reply_source": "llm_streaming"}
                }

                def capture_boundary(**_kwargs: Any) -> None:
                    boundary = current_voice_reply_memory_boundary()
                    self.assertIsNotNone(boundary)
                    captured["memory_exposure_position"] = (
                        boundary.memory_exposure_position
                    )
                    captured["memory_receipt"] = (
                        boundary.memory_receipt
                    )

                with patch(
                    "evelyn_core.voice_orchestration."
                    "memory_receipt_ref_from_metrics",
                    return_value=receipt,
                ):
                    finalize_delivered_voice_reply(
                        guild_id=7,
                        member=FakeMember(),
                        session_key="session-1",
                        room_session_key="room-1",
                        room_key=None,
                        person_key=None,
                        session_memory_key=None,
                        voice_reply=FakeVoiceReply(),
                        plain_answer="답변",
                        metrics=metrics,
                        turn_scope="scope",
                        accepted_turn_id="turn-1",
                        segment_id=1,
                        gate_mode="wake_entry",
                        finalize_voice_reply_side_effects=(
                            capture_boundary
                        ),
                        log_voice_stage=(
                            lambda *_args, **_kwargs: None
                        ),
                    )

        self.assertIs(
            captured["memory_exposure_position"],
            position,
        )
        self.assertEqual(captured["memory_receipt"], receipt)

    def test_canned_reply_cannot_inherit_prior_memory_exposure(
        self,
    ) -> None:
        note_id = "concept-0123456789abcdef"
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self._write_memory_version(index_dir, 1)
            with self._unconfigured_authenticity():
                capture_memory_exposure_position(
                    MemoryExposurePosition(
                        deletion_position=(
                            journal.memory_deletion_journal_position(
                                index_dir
                            )
                        ),
                        memory_version=1,
                        supplied_note_ids=(note_id,),
                    )
                )
                captured: dict[str, Any] = {}

                def capture_boundary(**_kwargs: Any) -> None:
                    boundary = current_voice_reply_memory_boundary()
                    self.assertIsNotNone(boundary)
                    captured["memory_exposure_position"] = (
                        boundary.memory_exposure_position
                    )
                    captured["memory_receipt"] = (
                        boundary.memory_receipt
                    )

                finalize_delivered_voice_reply(
                    guild_id=7,
                    member=FakeMember(),
                    session_key="session-1",
                    room_session_key="room-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    voice_reply=FakeVoiceReply(),
                    plain_answer="응",
                    metrics={
                        "meta": {"reply_source": "canned_wake_reply"}
                    },
                    turn_scope="scope",
                    accepted_turn_id="turn-1",
                    segment_id=1,
                    gate_mode="wake_entry",
                    finalize_voice_reply_side_effects=(
                        capture_boundary
                    ),
                    log_voice_stage=lambda *_args, **_kwargs: None,
                )

        self.assertIsNone(captured["memory_exposure_position"])
        self.assertEqual(
            captured["memory_receipt"]["state"],
            "not_used",
        )

    def _boundary_deps(
        self,
        *,
        index_dir: Path,
        calls: list[str],
        speculative: dict[str, Any] | None = None,
    ) -> VoiceReplySideEffectDeps:
        def record(name: str, result: Any = None):
            def _inner(*_args: Any, **_kwargs: Any) -> Any:
                calls.append(name)
                return result

            return _inner

        return VoiceReplySideEffectDeps(
            session_speculative_policies=(
                speculative if speculative is not None else {}
            ),
            append_history=record("append_history"),
            begin_user_only_turn=lambda *_args, **_kwargs: None,
            compute_runtime_mode=record(
                "compute_runtime_mode", "normal"
            ),
            record_context_pipeline_benchmark=record("benchmark"),
            schedule_memory_update=record("memory_update", {}),
            read_cached_cognitive_state=record(
                "read_cognitive", {}
            ),
            apply_ask_gating=record("ask_gating", {}),
            schedule_search_followup=record("followup"),
            session_state_snapshot=record("session_snapshot", {}),
            mark_session_active=record("active"),
            set_room_owner=record("owner"),
            commit_session_continuity=record(
                "continuity",
                durable_continuity_status(1),
            ),
            log=record("log"),
            active_conversation_voice_question_sec=12.0,
            active_conversation_voice_sec=3.0,
            active_conversation_awaiting_reply_sec=30.0,
            memory_index_dir=index_dir,
        )

    def _finalize_bound_reply(
        self,
        *,
        deps: VoiceReplySideEffectDeps,
        metrics: dict[str, Any],
        position: MemoryExposurePosition,
        receipt: dict[str, Any],
    ) -> None:
        finalize_voice_reply_side_effects_from_runtime(
            guild_id=7,
            member=FakeMember(),
            session_key="session-1",
            room_session_key="room-1",
            room_key=None,
            person_key=None,
            session_memory_key=None,
            voice_reply=FakeVoiceReply(),
            plain_answer="private reply text",
            metrics=metrics,
            turn_scope="scope",
            accepted_turn_id="turn-1",
            segment_id=1,
            memory_exposure_position=position,
            memory_receipt=receipt,
            deps=deps,
        )

    def test_stale_reply_exposure_rejects_all_success_side_effects(
        self,
    ) -> None:
        note_id = "concept-0123456789abcdef"
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self._write_memory_version(index_dir, 1)
            with self._unconfigured_authenticity():
                position = MemoryExposurePosition(
                    deletion_position=(
                        journal.memory_deletion_journal_position(
                            index_dir
                        )
                    ),
                    memory_version=1,
                    supplied_note_ids=(note_id,),
                )
                self._write_memory_version(index_dir, 2)
                calls: list[str] = []
                speculative = {"session-1": {}}
                deps = self._boundary_deps(
                    index_dir=index_dir,
                    calls=calls,
                    speculative=speculative,
                )
                metrics: dict[str, Any] = {"meta": {}}

                self._finalize_bound_reply(
                    deps=deps,
                    metrics=metrics,
                    position=position,
                    receipt=self._bound_receipt(
                        version=1,
                        note_id=note_id,
                    ),
                )

        self.assertEqual(calls, ["log"])
        self.assertIn("session-1", speculative)
        self.assertEqual(
            metrics["meta"]["voice_reply_side_effects_error"],
            "memory_deletion_journal_integrity_failed",
        )
        self.assertEqual(
            metrics["meta"]["continuity_commit"],
            "skipped",
        )
        self.assertNotIn("private reply text", str(metrics))
        self.assertNotIn("private reply text", str(calls))

    def test_receipt_exposure_mismatch_fails_before_persistence(
        self,
    ) -> None:
        first_note_id = "concept-0123456789abcdef"
        second_note_id = "concept-fedcba9876543210"
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self._write_memory_version(index_dir, 1)
            with self._unconfigured_authenticity():
                position = MemoryExposurePosition(
                    deletion_position=(
                        journal.memory_deletion_journal_position(
                            index_dir
                        )
                    ),
                    memory_version=1,
                    supplied_note_ids=(first_note_id,),
                )
                calls: list[str] = []
                deps = self._boundary_deps(
                    index_dir=index_dir,
                    calls=calls,
                )
                metrics: dict[str, Any] = {"meta": {}}

                self._finalize_bound_reply(
                    deps=deps,
                    metrics=metrics,
                    position=position,
                    receipt=self._bound_receipt(
                        version=1,
                        note_id=second_note_id,
                    ),
                )

        self.assertEqual(calls, ["log"])
        self.assertEqual(
            metrics["meta"]["voice_reply_side_effects_error"],
            "memory_deletion_journal_integrity_failed",
        )

    def test_bound_reply_persists_exact_receipt_under_fresh_guard(
        self,
    ) -> None:
        note_id = "concept-0123456789abcdef"
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self._write_memory_version(index_dir, 1)
            with self._unconfigured_authenticity():
                position = MemoryExposurePosition(
                    deletion_position=(
                        journal.memory_deletion_journal_position(
                            index_dir
                        )
                    ),
                    memory_version=1,
                    supplied_note_ids=(note_id,),
                )
                receipt = self._bound_receipt(
                    version=1,
                    note_id=note_id,
                )
                calls: list[str] = []
                stored_receipts: list[dict[str, Any]] = []
                deps = self._boundary_deps(
                    index_dir=index_dir,
                    calls=calls,
                )

                def append_history(
                    *_args: Any,
                    **kwargs: Any,
                ) -> None:
                    calls.append("append_history")
                    stored_receipts.append(kwargs["memory_receipt"])

                deps = replace(deps, append_history=append_history)
                metrics: dict[str, Any] = {"meta": {}}
                self._finalize_bound_reply(
                    deps=deps,
                    metrics=metrics,
                    position=position,
                    receipt=receipt,
                )

        self.assertEqual(stored_receipts, [receipt])
        self.assertIn("continuity", calls)
        self.assertIn("followup", calls)
        self.assertEqual(
            metrics["meta"]["voice_reply_side_effects_state"],
            "durable",
        )

    def test_integrity_failure_detail_is_never_exposed(self) -> None:
        private = "Bearer voice-secret C:\\private\\memory"
        calls: list[str] = []
        logs: list[tuple[Any, ...]] = []
        with tempfile.TemporaryDirectory() as temporary:
            deps = self._boundary_deps(
                index_dir=Path(temporary) / "memory_index",
                calls=calls,
            )

            def safe_log(*args: Any) -> None:
                calls.append("log")
                logs.append(args)

            deps = replace(deps, log=safe_log)
            metrics: dict[str, Any] = {"meta": {}}
            with patch(
                "evelyn_core.voice_reply_side_effects."
                "memory_exposure_guard",
                side_effect=MemoryDeletionJournalIntegrityError(private),
            ):
                finalize_voice_reply_side_effects_from_runtime(
                    guild_id=7,
                    member=FakeMember(),
                    session_key="session-1",
                    room_session_key="room-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    voice_reply=FakeVoiceReply(),
                    plain_answer="private reply text",
                    metrics=metrics,
                    turn_scope="scope",
                    accepted_turn_id="turn-1",
                    segment_id=1,
                    memory_exposure_position=None,
                    memory_receipt=not_used_memory_receipt_ref(),
                    deps=deps,
                )

        self.assertEqual(calls, ["log"])
        rendered = repr((metrics, logs))
        self.assertNotIn("voice-secret", rendered)
        self.assertNotIn("C:\\private", rendered)
        self.assertNotIn("private reply text", rendered)
        self.assertIn("memory_deletion_journal_integrity_failed", rendered)


if __name__ == "__main__":
    unittest.main()
