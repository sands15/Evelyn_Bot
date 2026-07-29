from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.fast_tool_planner import (  # noqa: E402
    FAST_TOOL_CAPABILITY_BY_NAME,
    answer_fast_tool_capability_question,
    enforce_registered_tool_capability_truth,
    normalize_stt_tool_text,
    plan_fast_tool_request,
    render_fast_tool_registry_context,
)


class FastToolPlannerTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_contains_real_inline_command_and_background_tools(self) -> None:
        expected = {
            "web_search",
            "research_compare",
            "runtime_investigation",
            "memory_recall",
            "runtime_status",
            "runtime_log_read",
            "microphone_control",
            "runtime_restart",
            "runtime_shutdown",
            "minecraft_start",
            "minecraft_goal",
        }

        self.assertTrue(expected.issubset(FAST_TOOL_CAPABILITY_BY_NAME))
        context = render_fast_tool_registry_context()
        self.assertIn("web_search", context)
        self.assertIn("available=true", context)
        self.assertIn("Router LLM cannot select restart", context)

    def test_stt_terms_and_contextual_external_search_are_normalized(self) -> None:
        recent = [{"role": "user", "content": "S T T 모델을 교체하려고 알아보는 중이야"}]

        normalized = normalize_stt_tool_text("외부 검사.", recent_messages=recent)

        self.assertEqual(normalized, "외부 검색.")
        self.assertEqual(normalize_stt_tool_text("S T T와 T T S"), "STT와 TTS")
        self.assertEqual(normalize_stt_tool_text("Vox CPM"), "VoxCPM")

    async def test_model_research_request_becomes_background_action(self) -> None:
        plan = await plan_fast_tool_request(
            "S T T 모델들 좀 알아봐줘",
            recent_messages=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool_name, "research_compare")
        self.assertEqual(plan.mode, "background")
        self.assertIn("STT", plan.query)

    async def test_short_correction_keeps_previous_research_topic(self) -> None:
        recent = [
            {"role": "user", "content": "로컬 STT 모델을 교체하고 싶어"},
            {"role": "assistant", "content": "현재 모델 상태는 확인할 수 있어."},
        ]

        plan = await plan_fast_tool_request(
            "아니, 그거 찾아보라고",
            recent_messages=recent,
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool_name, "research_compare")
        self.assertIn("로컬 STT 모델", plan.query)
        self.assertIn("찾아보라고", plan.query)

    async def test_contextual_external_search_stt_misrecognition_keeps_topic(self) -> None:
        recent = [{"role": "user", "content": "로컬 STT 모델 교체 후보를 알아봐줘"}]

        plan = await plan_fast_tool_request(
            "외부 검사.",
            recent_messages=recent,
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool_name, "research_compare")
        self.assertIn("로컬 STT 모델", plan.query)
        self.assertIn("외부 검색", plan.query)

    async def test_simple_weather_lookup_stays_inline(self) -> None:
        plan = await plan_fast_tool_request(
            "오늘 날씨 알아봐줘",
            recent_messages=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool_name, "web_search")
        self.assertEqual(plan.mode, "inline")

    async def test_runtime_problem_request_becomes_background_investigation(self) -> None:
        plan = await plan_fast_tool_request(
            "이블린 TTS 문제 원인을 조사해봐",
            recent_messages=[],
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool_name, "runtime_investigation")
        self.assertTrue(plan.is_background)

    async def test_ambiguous_followup_uses_read_only_router_plan(self) -> None:
        calls = []

        async def fake_router(text, recent_messages):
            calls.append((text, recent_messages))
            return {
                "intent": "web_lookup",
                "tool": "web_search",
                "query": "Qwen3 ASR current release",
                "confidence": 0.91,
                "reason": "previous turn requested model information",
            }

        plan = await plan_fast_tool_request(
            "그거 해줘",
            recent_messages=[{"role": "user", "content": "Qwen3-ASR 설정이 궁금해"}],
            router_provider=fake_router,
        )

        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.tool_name, "web_search")
        self.assertEqual(plan.source, "router_llm")

    async def test_router_cannot_select_dangerous_command(self) -> None:
        async def fake_router(text, recent_messages):
            return {
                "intent": "restart",
                "tool": "runtime_restart",
                "query": "",
                "confidence": 1.0,
            }

        plan = await plan_fast_tool_request(
            "그거 해줘",
            recent_messages=[{"role": "user", "content": "이블린 상태를 확인하고 있었어"}],
            router_provider=fake_router,
        )

        self.assertIsNone(plan)

    def test_false_web_permission_claim_is_replaced_with_runtime_truth(self) -> None:
        reply = enforce_registered_tool_capability_truth(
            "웹 검색 도구가 지원되지 않아서 사용할 수 없어."
        )

        self.assertIn("웹 검색 도구는 연결돼 있어", reply)
        self.assertNotIn("지원되지", reply)

    def test_web_capability_question_is_answered_from_registry(self) -> None:
        reply = answer_fast_tool_capability_question("웹검색 권한 없어?")

        self.assertEqual(
            reply,
            "웹 검색 도구는 연결돼 있고 읽기 전용 외부 검색을 실행할 수 있어.",
        )


if __name__ == "__main__":
    unittest.main()
