from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.response_output_policy import (  # noqa: E402
    answer_contains_minecraft_leak,
    answer_simple_local_chat_query,
    cleanup_assistant_display_artifacts,
    extract_answer_from_reasoning,
    extract_answer_from_reasoning_from_runtime,
    fallback_answer_for,
    fallback_for_unrequested_minecraft_leak,
    format_display_text,
    sanitize_model_output_from_runtime,
    looks_like_meta_line,
    normalize_friend_style_output,
    parse_response_action_tag,
    ResponseOutputPolicyRuntimeDeps,
    sanitize_model_output,
    sanitize_unrequested_minecraft_leak,
    strip_markdown_noise,
    should_label_question_response,
    user_explicitly_mentions_minecraft,
)


class ResponseOutputPolicyTests(unittest.TestCase):
    def test_fallback_answer_for_handles_empty_and_nonempty_text(self) -> None:
        self.assertEqual(fallback_answer_for(""), "응, 듣고 있어.")
        self.assertEqual(fallback_answer_for("계속해"), "응, 잠깐만.")

    def test_parse_response_action_tag_returns_action_and_clean_text(self) -> None:
        self.assertEqual(parse_response_action_tag(" [찾기]  검색할게 "), ("search", "검색할게"))
        self.assertEqual(parse_response_action_tag("[질문] 어디야?"), ("ask", "어디야?"))
        self.assertEqual(parse_response_action_tag("[답변] 바로 말할게"), ("answer", "바로 말할게"))
        self.assertEqual(parse_response_action_tag("[응답] 바로 말할게"), ("answer", "바로 말할게"))
        self.assertEqual(parse_response_action_tag("그냥 답"), (None, "그냥 답"))

    def test_normalize_friend_style_output_removes_formal_phrases_and_emoji(self) -> None:
        self.assertEqual(
            normalize_friend_style_output("부르셨나요? 말씀하세요 🙂"),
            "불렀어?? 말해",
        )

    def test_sanitize_model_output_strips_think_action_tags_stops_and_uses_cleanup(self) -> None:
        calls: list[str] = []

        def cleanup(text: str) -> str:
            calls.append(text)
            return text.replace("artifact", "").strip()

        output = sanitize_model_output(
            "<think>hidden</think>[응답] 부르셨나요 artifact <END>",
            stop_tokens=["<END>"],
            cleanup_artifacts_fn=cleanup,
        )

        self.assertEqual(output, "불렀어?")
        self.assertEqual(calls, ["불렀어? artifact"])

    def test_sanitize_model_output_from_runtime_uses_injected_settings(self) -> None:
        dep = ResponseOutputPolicyRuntimeDeps(
            model_output_stop_tokens=("END",),
            sanitize_model_output_cleanup_fn=lambda text: text.replace("artifact", "").strip(),
        )
        self.assertEqual(
            sanitize_model_output_from_runtime("abcEND artifact", deps=dep),
            "abc",
        )

    def test_reasoning_answer_extraction_filters_meta_and_user_echo(self) -> None:
        reasoning = (
            'thinking process: "Analyze the request"\n'
            '답변: 질문 그대로\n'
            '최종 답변: 이제 이어서 정리할게'
        )

        self.assertEqual(
            extract_answer_from_reasoning(reasoning, "질문 그대로", sanitize_output_fn=lambda text: text),
            "이제 이어서 정리할게",
        )

    def test_extract_answer_from_reasoning_from_runtime_uses_runtime_sanitization(self) -> None:
        dep = ResponseOutputPolicyRuntimeDeps(
            model_output_stop_tokens=("END",),
            sanitize_model_output_cleanup_fn=lambda text: text.replace("artifact", ""),
        )
        reasoning = (
            'thinking process: "Analyze the request"\n'
            "질문 그대로\n"
            '"질문 그대로"\n'
            '최종 답변: "이제 이어서 정리할게" artifact END'
        )
        self.assertEqual(
            extract_answer_from_reasoning_from_runtime(reasoning, "질문 그대로", deps=dep),
            "이제 이어서 정리할게",
        )

    def test_markdown_noise_and_meta_line_helpers(self) -> None:
        self.assertEqual(strip_markdown_noise("- **좋아**"), "좋아")
        self.assertTrue(looks_like_meta_line("Thinking process: draft"))
        self.assertTrue(looks_like_meta_line("**draft**"))
        self.assertFalse(looks_like_meta_line("이제 이어서 정리할게"))

    def test_question_label_policy_uses_session_state(self) -> None:
        def snapshot(_: str | None) -> dict[str, bool]:
            return {"awaiting_user_reply": True}

        self.assertTrue(should_label_question_response("안녕?", session_key="guild:1", session_state_snapshot_fn=snapshot))
        self.assertFalse(should_label_question_response("안녕?", session_key="guild:1", session_state_snapshot_fn=lambda _: {}))
        self.assertFalse(should_label_question_response("안녕?"))

    def test_display_cleanup_and_question_labeling(self) -> None:
        cleaned = cleanup_assistant_display_artifacts("Ready to tackle this directly now\nplain english sentence only")

        self.assertEqual(cleaned, "")
        self.assertEqual(
            format_display_text(
                "[voice] 부르셨나요?",
                session_key="s",
                should_label_question_response_fn=lambda text, *, session_key: session_key == "s",
            ),
            "[질문] 불렀어??",
        )

    def test_minecraft_leak_policy_allows_explicit_mentions_only(self) -> None:
        self.assertTrue(user_explicitly_mentions_minecraft("마크 좌표 알려줘"))
        self.assertFalse(user_explicitly_mentions_minecraft("마크 얘기는 하지 마"))
        self.assertTrue(answer_contains_minecraft_leak("Voyager pathfinding 좌표"))

        self.assertEqual(
            sanitize_unrequested_minecraft_leak("안녕", "마크 좌표는 1 2 3"),
            "응, 안녕. 짧게 말할게.",
        )
        self.assertEqual(
            sanitize_unrequested_minecraft_leak("마크 좌표 알려줘", "마크 좌표는 1 2 3"),
            "마크 좌표는 1 2 3",
        )

    def test_simple_local_chat_and_gpu_fallback_policy(self) -> None:
        self.assertEqual(answer_simple_local_chat_query("안녕"), "응, 안녕.")
        self.assertIsNone(answer_simple_local_chat_query("마크 상태 알려줘"))
        self.assertEqual(
            fallback_for_unrequested_minecraft_leak("gpu 상태", gpu_status_answer_fn=lambda _text: "GPU 괜찮아"),
            "GPU 괜찮아",
        )


if __name__ == "__main__":
    unittest.main()
