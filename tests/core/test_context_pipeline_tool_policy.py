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
    ToolUseDecision,
    build_basic_context_packet,
    build_conversation_state_context,
    build_context_policy_for_turn,
    build_required_evidence_failure_reply,
    build_tool_use_decisions,
    build_vision_context_hint,
    has_unanswered_user_turn,
    render_tool_use_context,
)


class ContextPipelineToolPolicyTests(unittest.TestCase):
    def test_every_nonexecuted_required_status_is_a_hard_gate(self) -> None:
        for status in (
            "planned",
            "needs_local_tool",
            "needs_permission_or_external_tool",
            "executed_empty",
            "executed_withheld",
            "failed",
            "failed_or_unavailable",
            "skipped_no_memory_scope",
        ):
            with self.subTest(status=status):
                reply = build_required_evidence_failure_reply(
                    [
                        ToolUseDecision(
                            tool_name="memory_recall",
                            reason="required",
                            required_before_answer=True,
                            status=status,
                        )
                    ]
                )
                self.assertIn("추측해서 답하지 않을게", reply)

    def test_optional_or_deferred_evidence_does_not_gate(self) -> None:
        optional = ToolUseDecision(
            tool_name="memory_recall",
            reason="optional",
            required_before_answer=False,
            status="failed",
        )
        deferred_web = ToolUseDecision(
            tool_name="web_current_info",
            reason="search executor owns this next",
            required_before_answer=True,
            status="needs_permission_or_external_tool",
        )

        self.assertEqual(build_required_evidence_failure_reply([optional]), "")
        self.assertEqual(
            build_required_evidence_failure_reply(
                [deferred_web],
                deferred_tool_names={"web_current_info"},
            ),
            "",
        )

    def test_unanswered_turn_detection_uses_latest_conversational_row(self) -> None:
        self.assertTrue(
            has_unanswered_user_turn(
                [
                    {"role": "assistant", "content": "answered"},
                    {"role": "system", "content": "ignored"},
                    {"role": "user", "content": "still pending"},
                ]
            )
        )
        self.assertFalse(
            has_unanswered_user_turn(
                [
                    {"role": "user", "content": "question"},
                    {"role": "assistant", "content": "answer"},
                ]
            )
        )
        self.assertFalse(
            has_unanswered_user_turn(
                [{"role": "user", "content": "   "}]
            )
        )

    def test_unanswered_turn_context_is_fixed_and_content_free(self) -> None:
        private_text = "PRIVATE_UNANSWERED_USER_TEXT"

        context = build_conversation_state_context(
            route="chat",
            unanswered_user_turn=has_unanswered_user_turn(
                [{"role": "user", "content": private_text}]
            ),
        )

        self.assertIn(
            "continuity_schema: conversation.unanswered-user.v1",
            context,
        )
        self.assertIn("unanswered_user_turn: true", context)
        self.assertIn("continuity_content_free: true", context)
        self.assertIn("no delivered assistant reply", context)
        self.assertNotIn(private_text, context)

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

    def test_failed_tool_evidence_is_content_free(self) -> None:
        private_path = "C:/secret/runtime-token"
        private_canary = f"PRIVATE_TOOL_FAILURE_CANARY {private_path}"
        decision = ToolUseDecision(
            tool_name="runtime_status",
            reason="runtime check",
            status="failed",
            evidence=repr(RuntimeError(private_canary)),
        )

        projected = decision.to_dict()
        rendered = render_tool_use_context([decision])

        self.assertEqual(projected["evidence"], "runtime_status_failed")
        self.assertIn("evidence=runtime_status_failed", rendered)
        self.assertNotIn(private_canary, str(projected))
        self.assertNotIn(private_canary, rendered)
        self.assertNotIn(private_path, str(projected))
        self.assertNotIn(private_path, rendered)


if __name__ == "__main__":
    unittest.main()
