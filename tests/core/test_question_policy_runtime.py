from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from unittest import TestCase

from evelyn_core.question_policy_runtime import (  # noqa: E402
    QuestionPolicyRuntimeDeps,
    QuestionPolicyStateRuntimeDeps,
    extract_question_policy_from_route_meta_from_runtime,
    is_continuable_technical_topic_from_runtime,
    normalize_question_policy_mapping_from_runtime,
    apply_fast_path_question_policy_from_runtime,
    proactive_question_scope_candidates_from_runtime,
    question_cooldown_hit_from_runtime,
    record_question_trace_from_runtime,
    summarize_question_metrics_from_runtime,
    record_session_question_asked_from_runtime,
    resolve_pending_proactive_question_for_turn_from_runtime,
    select_and_mark_proactive_question_from_runtime,
    maybe_append_proactive_question_from_runtime,
    user_frustration_with_questions_from_runtime,
    user_wants_direct_answer_from_runtime,
)


class QuestionPolicyRuntimeTests(TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple] = []
        self.deps = QuestionPolicyRuntimeDeps(
            normalize_question_policy_mapping_payload=self._normalize,
            extract_question_policy_from_route_meta_payload=self._extract_meta,
            user_wants_direct_answer_payload=self._wants_direct,
            user_frustration_with_questions_payload=self._frustration,
            is_continuable_technical_topic_payload=self._continuable,
        )
        self.state_calls: list[tuple] = []
        self.state_deps = QuestionPolicyStateRuntimeDeps(
            question_cooldown_hit_payload=self._cooldown,
            apply_fast_path_question_policy_payload=self._apply_fast_path,
            record_question_trace_payload=self._record_trace,
            summarize_question_metrics_payload=self._summarize_metrics,
            proactive_scope_candidates_payload=self._scope_candidates,
            record_session_question_asked_payload=self._record_question_asked,
            resolve_pending_proactive_question_for_turn_payload=self._resolve_pending,
            select_and_mark_proactive_question_payload=self._select_and_mark,
            maybe_append_proactive_question_payload=self._append_proactive,
        )

    def _normalize(self, value, *, default_source="none"):
        self.calls.append(("normalize", value, default_source))
        return {"value": value, "default_source": default_source}

    def _extract_meta(self, route_meta):
        self.calls.append(("extract", route_meta))
        return {"policy": route_meta}

    def _wants_direct(self, text):
        self.calls.append(("wants_direct", text))
        return text == "direct"

    def _frustration(self, text):
        self.calls.append(("frustration", text))
        return text == "frustrate"

    def _continuable(self, text):
        self.calls.append(("continuable", text))
        return text == "technical"

    def _cooldown(self, session_key, *, now=None):
        self.state_calls.append(("cooldown", session_key, now))
        return session_key == "cool"

    def _apply_fast_path(self, route_decision, *, user_text, session_key, route_meta_question_policy=None):
        self.state_calls.append(("apply_fast_path", route_decision, user_text, session_key, route_meta_question_policy))
        return (route_decision, route_meta_question_policy is not None)

    def _record_trace(self, *, route_decision, answer, shape_meta, metrics, cooldown_hit=False):
        self.state_calls.append(("record_trace", route_decision, answer, shape_meta, metrics, cooldown_hit))

    def _summarize_metrics(self):
        self.state_calls.append(("summarize_metrics",))
        return {"metric": "ok"}

    def _scope_candidates(self, *, room_key=None, person_key=None, session_memory_key=None):
        self.state_calls.append(("scope_candidates", room_key, person_key, session_memory_key))
        return [("room", room_key), ("person", person_key), ("session", session_memory_key)]

    def _record_question_asked(self, session_key, *, now=None):
        self.state_calls.append(("record_question_asked", session_key, now))

    def _resolve_pending(self, guild_id, user_text, *, session_key=None, session_memory_key=None, metrics=None):
        self.state_calls.append(("resolve_pending", guild_id, user_text, session_key, session_memory_key, metrics))
        return {"resolved": True}

    def _select_and_mark(self, *, guild_id, source, user_text, answer_text, awaiting_user_reply, room_key, person_key, session_key, session_memory_key, runtime_block_reason, metrics):
        self.state_calls.append(
            (
                "select_and_mark",
                guild_id,
                source,
                user_text,
                answer_text,
                awaiting_user_reply,
                room_key,
                person_key,
                session_key,
                session_memory_key,
                runtime_block_reason,
                metrics,
            )
        )
        return {"id": "Q1", "ask_text": "ask", "scope_type": "room", "scope_key": room_key}

    def _append_proactive(
        self,
        answer_text,
        *,
        guild_id,
        source,
        user_text,
        awaiting_user_reply,
        room_key,
        person_key,
        session_key,
        session_memory_key,
        metrics,
    ):
        self.state_calls.append(
            (
                "append_proactive",
                answer_text,
                guild_id,
                source,
                user_text,
                awaiting_user_reply,
                room_key,
                person_key,
                session_key,
                session_memory_key,
                metrics,
            )
        )
        return (f"{answer_text}\nappend", True)

    def test_runtime_dispatches_to_payload_callables(self) -> None:
        self.assertEqual(
            normalize_question_policy_mapping_from_runtime({"a": 1}, default_source="meta", deps=self.deps),
            {"value": {"a": 1}, "default_source": "meta"},
        )
        self.assertEqual(
            extract_question_policy_from_route_meta_from_runtime({"route": "x"}, deps=self.deps),
            {"policy": {"route": "x"}},
        )
        self.assertTrue(user_wants_direct_answer_from_runtime("direct", deps=self.deps))
        self.assertFalse(user_wants_direct_answer_from_runtime("other", deps=self.deps))
        self.assertTrue(user_frustration_with_questions_from_runtime("frustrate", deps=self.deps))
        self.assertFalse(user_frustration_with_questions_from_runtime("ok", deps=self.deps))
        self.assertTrue(is_continuable_technical_topic_from_runtime("technical", deps=self.deps))
        self.assertFalse(is_continuable_technical_topic_from_runtime("other", deps=self.deps))

        self.assertIn(("normalize", {"a": 1}, "meta"), self.calls)
        self.assertIn(("extract", {"route": "x"}), self.calls)
        self.assertIn(("wants_direct", "direct"), self.calls)
        self.assertIn(("frustration", "frustrate"), self.calls)
        self.assertIn(("continuable", "technical"), self.calls)

    def test_state_runtime_dispatches_payloads(self) -> None:
        self.assertTrue(question_cooldown_hit_from_runtime("cool", now=1.0, deps=self.state_deps))
        self.assertFalse(question_cooldown_hit_from_runtime("hot", now=2.0, deps=self.state_deps))
        self.assertEqual(
            apply_fast_path_question_policy_from_runtime(
                "route",
                user_text="hello",
                session_key="room",
                route_meta_question_policy={"ask_mode": "clarify"},
                deps=self.state_deps,
            ),
            ("route", True),
        )
        self.assertIsNone(record_question_trace_from_runtime(
            route_decision="route",
            answer="answer",
            shape_meta={},
            metrics=None,
            cooldown_hit=False,
            deps=self.state_deps,
        ))
        self.assertEqual(summarize_question_metrics_from_runtime(deps=self.state_deps), {"metric": "ok"})
        self.assertEqual(
            proactive_question_scope_candidates_from_runtime(
                room_key="r1",
                person_key="p1",
                session_memory_key="s1",
                deps=self.state_deps,
            ),
            [("room", "r1"), ("person", "p1"), ("session", "s1")],
        )
        self.assertIsNone(record_session_question_asked_from_runtime("s1", now=3.0, deps=self.state_deps))
        self.assertEqual(
            resolve_pending_proactive_question_for_turn_from_runtime(
                1,
                "need",
                session_key="s1",
                session_memory_key="sm",
                metrics={"meta": {}},
                deps=self.state_deps,
            ),
            {"resolved": True},
        )
        self.assertEqual(
            select_and_mark_proactive_question_from_runtime(
                guild_id=2,
                source="voice",
                user_text="u",
                answer_text="a",
                awaiting_user_reply=True,
                room_key="r",
                person_key="p",
                session_key="s",
                session_memory_key="sm",
                runtime_block_reason="",
                metrics={"meta": {}},
                deps=self.state_deps,
            ),
            {"id": "Q1", "ask_text": "ask", "scope_type": "room", "scope_key": "r"},
        )
        self.assertEqual(
            maybe_append_proactive_question_from_runtime(
                "base",
                guild_id=3,
                source="text",
                user_text="u2",
                awaiting_user_reply=False,
                room_key="r",
                person_key="p",
                session_key="s",
                session_memory_key="sm",
                metrics={"meta": {}},
                deps=self.state_deps,
            ),
            ("base\nappend", True),
        )

        self.assertIn(("cooldown", "cool", 1.0), self.state_calls)
        self.assertIn(("apply_fast_path", "route", "hello", "room", {"ask_mode": "clarify"}), self.state_calls)
        self.assertIn(("scope_candidates", "r1", "p1", "s1"), self.state_calls)
        self.assertIn(("resolve_pending", 1, "need", "s1", "sm", {"meta": {}}), self.state_calls)
        self.assertIn(("select_and_mark", 2, "voice", "u", "a", True, "r", "p", "s", "sm", "", {"meta": {}}), self.state_calls)
        self.assertIn(("append_proactive", "base", 3, "text", "u2", False, "r", "p", "s", "sm", {"meta": {}}), self.state_calls)


if __name__ == "__main__":
    import unittest

    unittest.main()
