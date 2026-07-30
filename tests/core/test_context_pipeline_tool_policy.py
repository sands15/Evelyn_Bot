from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.context_pipeline import (
    ContextBuilder,
    ContextPolicy,
    build_basic_context_packet,
    build_context_policy_for_turn,
    build_tool_use_decisions,
    build_vision_context_hint,
    render_tool_use_context,
)


class ContextPipelineToolPolicyTests(unittest.TestCase):
    def test_runtime_status_question_requests_runtime_tool(self) -> None:
        policy = build_context_policy_for_turn(user_text="지금 VRAM이랑 OOM 상태 어때?", source="text", route="main_direct")

        decisions = build_tool_use_decisions("지금 VRAM이랑 OOM 상태 어때?", policy)

        runtime = next((item for item in decisions if item.tool_name == "runtime_status"), None)
        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertTrue(runtime.auto_allowed)
        self.assertTrue(runtime.required_before_answer)

    def test_screen_text_question_requests_vision_and_ocr_tools(self) -> None:
        policy = ContextPolicy()

        decisions = build_tool_use_decisions("화면 글자 읽고 설명해줘", policy)

        names = {item.tool_name for item in decisions}
        self.assertIn("vision_capture_or_watch", names)
        self.assertIn("vision_ocr", names)
        ocr = next(item for item in decisions if item.tool_name == "vision_ocr")
        self.assertEqual(ocr.cost, "high")
        self.assertTrue(ocr.auto_allowed)

    def test_korean_screen_requests_need_vision_policy(self) -> None:
        policy = build_context_policy_for_turn(user_text="화면 봐", source="text", route="main_direct")

        decisions = build_tool_use_decisions("화면 봐", policy)

        self.assertTrue(policy.needs_vision)
        self.assertEqual(policy.intent, "vision_question")
        self.assertIn("vision_capture_or_watch", {item.tool_name for item in decisions})

    def test_korean_screen_text_request_needs_ocr_tool(self) -> None:
        policy = build_context_policy_for_turn(user_text="화면 글자 읽고 설명해줘", source="text", route="main_direct")

        decisions = build_tool_use_decisions("화면 글자 읽고 설명해줘", policy)

        names = {item.tool_name for item in decisions}
        self.assertTrue(policy.needs_vision)
        self.assertIn("vision_capture_or_watch", names)
        self.assertIn("vision_ocr", names)

    def test_screen_title_and_button_request_needs_ocr_tool(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="화면에서 가장 큰 제목과 버튼을 말해줘",
            source="text",
            route="main_direct",
        )

        names = {
            item.tool_name
            for item in build_tool_use_decisions(
                "화면에서 가장 큰 제목과 버튼을 말해줘",
                policy,
            )
        }

        self.assertIn("vision_capture_or_watch", names)
        self.assertIn("vision_ocr", names)

    def test_plain_document_read_request_does_not_capture_screen(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="이 문서를 읽고 요약해줘",
            source="text",
            route="main_direct",
        )

        decisions = build_tool_use_decisions("이 문서를 읽고 요약해줘", policy)
        names = {item.tool_name for item in decisions}

        self.assertFalse(policy.needs_vision)
        self.assertNotIn("vision_capture_or_watch", names)
        self.assertNotIn("vision_ocr", names)

    def test_generic_show_me_request_does_not_capture_screen(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="예시를 하나 보여줘",
            source="text",
            route="main_direct",
        )

        self.assertFalse(policy.needs_vision)

    def test_current_time_request_does_not_capture_screen(self) -> None:
        policy = build_context_policy_for_turn(
            user_text="현재 시각을 알려줘",
            source="text",
            route="main_direct",
        )
        decisions = build_tool_use_decisions("현재 시각을 알려줘", policy)

        self.assertFalse(policy.needs_vision)
        self.assertNotIn(
            "vision_capture_or_watch",
            {item.tool_name for item in decisions},
        )

    def test_vision_hint_requires_using_observation_evidence(self) -> None:
        hint = build_vision_context_hint(ContextPolicy(needs_vision=True), user_text="화면 봐")
        packet = build_basic_context_packet(
            current_user_input="",
            vision_context=hint,
            policy=ContextPolicy(needs_vision=True),
        )

        rendered = ContextBuilder().render_system_context(packet)

        self.assertIn("[Vision Context]", rendered)
        self.assertIn("answer from that evidence", rendered)

    def test_external_current_info_is_not_marked_auto_allowed(self) -> None:
        decisions = build_tool_use_decisions("인터넷에서 최신 가격 검색해줘", ContextPolicy())

        web = next((item for item in decisions if item.tool_name == "web_current_info"), None)
        self.assertIsNotNone(web)
        assert web is not None
        self.assertFalse(web.auto_allowed)
        self.assertEqual(web.risk, "external")
        self.assertTrue(web.required_before_answer)

    def test_korean_weather_question_requests_current_info_tool(self) -> None:
        decisions = build_tool_use_decisions("날씨 알려줘", ContextPolicy())

        web = next((item for item in decisions if item.tool_name == "web_current_info"), None)
        self.assertIsNotNone(web)
        assert web is not None
        self.assertTrue(web.required_before_answer)

    def test_casual_today_conversation_does_not_request_web_tool(self) -> None:
        decisions = build_tool_use_decisions("오늘 기분은 어때?", ContextPolicy())

        self.assertNotIn("web_current_info", {item.tool_name for item in decisions})

    def test_current_external_role_requests_web_tool(self) -> None:
        decisions = build_tool_use_decisions("현재 대통령이 누구야?", ContextPolicy())

        self.assertIn("web_current_info", {item.tool_name for item in decisions})

    def test_tool_context_renders_into_context_packet(self) -> None:
        decisions = build_tool_use_decisions("로그 파일 확인해줘", ContextPolicy())
        tool_context = render_tool_use_context(decisions)
        packet = build_basic_context_packet(current_user_input="", tool_context=tool_context)

        rendered = ContextBuilder().render_system_context(packet)

        self.assertIn("[Tool Use Policy]", rendered)
        self.assertIn("local_file_or_log_read", rendered)
        self.assertIn("avoid claiming tool-backed evidence", rendered)

    def test_main_llm_tool_calling_complaint_requires_diagnostic_tools(self) -> None:
        decisions = build_tool_use_decisions("메인 llm의 도구 호출이 너무 약해", ContextPolicy())

        by_name = {item.tool_name: item for item in decisions}
        self.assertIn("runtime_status", by_name)
        self.assertIn("local_file_or_log_read", by_name)
        self.assertTrue(by_name["runtime_status"].auto_allowed)
        self.assertTrue(by_name["runtime_status"].required_before_answer)
        self.assertTrue(by_name["local_file_or_log_read"].auto_allowed)
        self.assertTrue(by_name["local_file_or_log_read"].required_before_answer)

    def test_tool_context_renders_required_tool_as_hard_gate(self) -> None:
        decisions = build_tool_use_decisions("메인 llm의 도구 호출이 너무 약해", ContextPolicy())

        rendered = render_tool_use_context(decisions)

        self.assertIn("Required tool evidence is a hard gate", rendered)
        self.assertIn("do not answer from guesswork", rendered)
        self.assertIn("local_file_or_log_read", rendered)


if __name__ == "__main__":
    unittest.main()
