import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.context_pipeline import ContextPolicy, build_context_policy_for_turn  # noqa: E402
from evelyn_core.voice_pipeline import build_route_decision, route_decision_policy_dict  # noqa: E402


class RoutePolicyTests(unittest.TestCase):
    def test_route_decision_policy_defaults_keep_old_callers_safe(self) -> None:
        decision = build_route_decision(
            action="answer",
            route="main_direct",
            source="text",
            prompt_text="hello",
        )

        self.assertEqual(decision.needs_main_llm, True)
        self.assertEqual(decision.needs_memory, True)
        self.assertEqual(decision.needs_runtime_state, True)
        self.assertEqual(decision.needs_tts, True)
        self.assertEqual(decision.response_mode, "normal")
        self.assertEqual(decision.priority, "latency")

    def test_route_decision_policy_dict_includes_explicit_flags(self) -> None:
        decision = build_route_decision(
            action="search_then_answer",
            route="search_executor",
            source="voice",
            prompt_text="latest news",
            needs_search=True,
            needs_minecraft_state=True,
            response_mode="short",
            priority="accuracy",
            ask_mode="topic_continue",
            max_question_count=1,
            question_hint="ask what to tune next",
            question_reason="technical_topic",
            question_source="fast_path",
        )

        policy = route_decision_policy_dict(decision)

        self.assertEqual(policy["needs_search"], True)
        self.assertEqual(policy["needs_minecraft_state"], True)
        self.assertEqual(policy["response_mode"], "short")
        self.assertEqual(policy["priority"], "accuracy")
        self.assertEqual(policy["ask_mode"], "topic_continue")
        self.assertEqual(policy["max_question_count"], 1)
        self.assertEqual(policy["question_hint"], "ask what to tune next")
        self.assertEqual(policy["question_reason"], "technical_topic")
        self.assertEqual(policy["question_source"], "fast_path")

    def test_context_policy_from_old_mapping_defaults_new_flags(self) -> None:
        policy = ContextPolicy.from_mapping({"needs_memory": False})

        self.assertEqual(policy.needs_memory, False)
        self.assertEqual(policy.needs_search, False)
        self.assertEqual(policy.needs_tts, True)

    def test_search_action_requests_search_policy(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="latest price",
            source="text",
            route="sub_wait",
            cognitive_state={"action": "search_then_answer"},
        )

        self.assertEqual(policy.needs_search, True)
        self.assertEqual(policy.priority, "accuracy")

    def test_normal_answer_path_keeps_memory_and_tts(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="tell me about this",
            source="text",
            route="main_direct",
            cognitive_state={"action": "answer"},
        )

        self.assertEqual(policy.needs_main_llm, True)
        self.assertEqual(policy.needs_memory, True)
        self.assertEqual(policy.needs_tts, True)
        self.assertEqual(policy.response_mode, "normal")

    def test_fast_ack_policy_can_skip_memory_runtime_and_main_llm(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="ok",
            source="voice",
            route="main_direct",
            route_meta={
                "context_policy": {
                    "needs_main_llm": False,
                    "needs_memory": False,
                    "needs_runtime_state": False,
                    "needs_tts": True,
                    "response_mode": "short",
                    "priority": "latency",
                }
            },
            cognitive_state={"action": "wait"},
        )

        self.assertEqual(policy.needs_main_llm, False)
        self.assertEqual(policy.needs_memory, False)
        self.assertEqual(policy.needs_runtime_state, False)
        self.assertEqual(policy.needs_tts, True)
        self.assertEqual(policy.response_mode, "short")

    def test_minecraft_status_path_requests_runtime_state_intentionally(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="minecraft status",
            source="text",
            route="main_direct",
            cognitive_state={"action": "answer"},
        )

        self.assertEqual(policy.needs_runtime_state, True)
        self.assertEqual(policy.needs_minecraft_state, True)
        self.assertEqual(policy.needs_skill_graph, True)
        self.assertEqual(policy.priority, "action")


if __name__ == "__main__":
    unittest.main()
