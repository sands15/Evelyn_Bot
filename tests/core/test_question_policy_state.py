from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.question_policy_state import (  # noqa: E402
    QuestionPolicyState,
    default_question_metrics,
    normalize_question_policy_mapping,
)
from evelyn_core.voice_pipeline import RouteDecision  # noqa: E402


def make_store(logs: list[tuple[str, dict]] | None = None) -> QuestionPolicyState:
    def log_turn_event(event: str, **payload) -> None:
        if logs is not None:
            logs.append((event, payload))

    return QuestionPolicyState(
        question_metrics=default_question_metrics(),
        session_question_state={},
        log_turn_event=log_turn_event,
        question_feature_enabled=True,
        min_turn_gap=3,
        min_seconds_gap=60.0,
        max_per_10_turns=3,
        disable_after_frustration_sec=300.0,
    )


class QuestionPolicyStateTests(unittest.TestCase):
    def test_normalize_question_policy_caps_question_count(self) -> None:
        policy = normalize_question_policy_mapping(
            {"ask_mode": "topic_continue", "max_question_count": 5, "question_source": "Router"},
            default_source="fallback",
        )

        self.assertEqual(policy["ask_mode"], "topic_continue")
        self.assertEqual(policy["max_question_count"], 1)
        self.assertEqual(policy["question_source"], "router")

    def test_fast_path_direct_answer_disables_question(self) -> None:
        store = make_store()
        decision = RouteDecision(action="answer", route="main", source="router", prompt_text="")

        updated, cooldown = store.apply_fast_path_policy(
            decision,
            user_text="짧게 답만 해줘",
            session_key="session-1",
        )

        self.assertFalse(cooldown)
        self.assertEqual(updated.ask_mode, "none")
        self.assertEqual(updated.question_reason, "direct_answer_requested")

    def test_record_question_trace_updates_metrics_and_session_state(self) -> None:
        logs: list[tuple[str, dict]] = []
        store = make_store(logs)
        decision = RouteDecision(
            action="answer",
            route="main",
            source="router",
            prompt_text="",
            ask_mode="topic_continue",
            question_source="fast_path",
            question_reason="fast_path_topic_continue",
        )
        store.session_question_state["session-1"] = {"turn_index": 4, "question_turns": [], "frustration_until": 0.0}

        store.record_question_trace(
            route_decision=decision,
            answer="answer?",
            shape_meta={"question_count_after": 1},
            metrics={"meta": {"session_key": "session-1", "turn_id": "turn-1", "source": "text"}},
        )

        summary = store.summarize_question_metrics()
        self.assertEqual(summary["turnCount"], 1)
        self.assertEqual(summary["questionAddedCount"], 1)
        self.assertEqual(summary["askModeDistribution"], {"topic_continue": 1})
        self.assertEqual(store.session_question_state["session-1"]["question_turns"], [4])
        self.assertEqual(logs[0][0], "question_trace")

    def test_proactive_scope_candidates_order_specific_to_general(self) -> None:
        store = make_store()

        scopes = store.proactive_scope_candidates(
            room_key="room-1",
            person_key="person-1",
            session_memory_key="session-1",
        )

        self.assertEqual(
            scopes,
            [
                ("session", "session-1"),
                ("person", "person-1"),
                ("room", "room-1"),
                ("guild", None),
            ],
        )


if __name__ == "__main__":
    unittest.main()
