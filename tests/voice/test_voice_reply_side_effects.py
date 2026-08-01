from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_reply_side_effects import (  # noqa: E402
    VoiceReplySideEffectDeps,
    finalize_voice_reply_side_effects_from_runtime,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


@dataclass(frozen=True)
class FakeMember:
    id: int = 42
    display_name: str = "정훈"


@dataclass(frozen=True)
class FakeVoiceReply:
    history_user_text: str = "검색해줘"
    topic_id: str = "topic-1"


class VoiceReplySideEffectsTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
