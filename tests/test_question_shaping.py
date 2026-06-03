from __future__ import annotations

import ast
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.question_shaping import (  # noqa: E402
    enforce_question_limits,
    filter_stream_chunk_for_question_limits,
    split_answer_sentences,
)


@dataclass(frozen=True)
class QuestionPolicy:
    max_question_count: int = 0


class MissingQuestionPolicy:
    pass


class QuestionShapingTests(unittest.TestCase):
    def test_keeps_non_question_answer_unchanged(self) -> None:
        shaped, meta = enforce_question_limits("좋아. 바로 정리했어.", QuestionPolicy(max_question_count=0))

        self.assertEqual(shaped, "좋아. 바로 정리했어.")
        self.assertEqual(meta["question_count_before"], 0)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertFalse(meta["question_removed"])

    def test_keeps_only_one_question_when_allowed(self) -> None:
        shaped, meta = enforce_question_limits(
            "정리했어. 다음은 UI 볼까? 아니면 로그 볼까?",
            QuestionPolicy(max_question_count=1),
        )

        self.assertEqual(shaped, "정리했어. 다음은 UI 볼까?")
        self.assertEqual(meta["question_count_before"], 2)
        self.assertEqual(meta["question_count_after"], 1)
        self.assertTrue(meta["question_removed"])

    def test_removes_all_questions_when_not_allowed(self) -> None:
        shaped, meta = enforce_question_limits(
            "정리했어. 다음은 UI 볼까?",
            QuestionPolicy(max_question_count=0),
        )

        self.assertEqual(shaped, "정리했어.")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertTrue(meta["question_removed"])

    def test_all_questions_removed_use_non_question_fallback(self) -> None:
        shaped, meta = enforce_question_limits(
            "UI 볼까? 로그 볼까?",
            QuestionPolicy(max_question_count=0),
        )

        self.assertEqual(shaped, "응, 알겠어.")
        self.assertFalse(shaped.rstrip().endswith("?"))
        self.assertEqual(meta["question_count_before"], 2)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertTrue(meta["question_removed"])

    def test_stream_filter_removes_question_after_limit(self) -> None:
        shaped, meta = filter_stream_chunk_for_question_limits(
            "다음은 UI 볼까?",
            max_question_count=1,
            question_count_so_far=1,
        )

        self.assertEqual(shaped, "")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertTrue(meta["question_removed"])

    def test_stream_filter_preserves_first_allowed_question(self) -> None:
        shaped, meta = filter_stream_chunk_for_question_limits(
            "다음은 UI 볼까?",
            max_question_count=1,
            question_count_so_far=0,
        )

        self.assertEqual(shaped, "다음은 UI 볼까?")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 1)
        self.assertFalse(meta["question_removed"])

    def test_stream_filter_handles_mixed_chunk_with_multiple_questions(self) -> None:
        shaped, meta = filter_stream_chunk_for_question_limits(
            "Ready. Next?Logs?",
            max_question_count=1,
            question_count_so_far=0,
        )

        self.assertEqual(shaped, "Ready. Next?")
        self.assertEqual(meta["question_count_before"], 2)
        self.assertEqual(meta["question_count_after"], 1)
        self.assertTrue(meta["question_removed"])

    def test_stream_filter_keeps_plain_text_when_questions_are_blocked(self) -> None:
        shaped, meta = filter_stream_chunk_for_question_limits(
            "Ready. Next?",
            max_question_count=0,
            question_count_so_far=0,
        )

        self.assertEqual(shaped, "Ready.")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertTrue(meta["question_removed"])

    def test_sentence_split_handles_missing_space_after_punctuation(self) -> None:
        self.assertEqual(split_answer_sentences("Done. Next step?Logs?"), ["Done.", "Next step?", "Logs?"])

    def test_question_limit_handles_missing_space_after_question(self) -> None:
        shaped, meta = enforce_question_limits(
            "Done. Next step?Logs?",
            QuestionPolicy(max_question_count=1),
        )

        self.assertEqual(shaped, "Done. Next step?")
        self.assertEqual(meta["question_count_before"], 2)
        self.assertEqual(meta["question_count_after"], 1)
        self.assertTrue(meta["question_removed"])

    def test_sentence_split_does_not_break_decimal_or_version_text(self) -> None:
        self.assertEqual(split_answer_sentences("Use v1.2. Next step?Logs?"), ["Use v1.2.", "Next step?", "Logs?"])

    def test_missing_policy_defaults_to_no_questions(self) -> None:
        shaped, meta = enforce_question_limits("Ready. Next?", MissingQuestionPolicy())

        self.assertEqual(shaped, "Ready.")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertTrue(meta["question_removed"])

    def test_negative_max_question_count_is_clamped_to_zero(self) -> None:
        shaped, meta = enforce_question_limits("Ready. Next?", QuestionPolicy(max_question_count=-3))

        self.assertEqual(shaped, "Ready.")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 0)
        self.assertTrue(meta["question_removed"])

    def test_stream_filter_clamps_negative_question_count_so_far(self) -> None:
        shaped, meta = filter_stream_chunk_for_question_limits(
            "Next?",
            max_question_count=1,
            question_count_so_far=-10,
        )

        self.assertEqual(shaped, "Next?")
        self.assertEqual(meta["question_count_before"], 1)
        self.assertEqual(meta["question_count_after"], 1)
        self.assertFalse(meta["question_removed"])

    def test_main_uses_extracted_question_shaping_module(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn("from evelyn_core.question_shaping import", main_py)
        self.assertNotIn("def split_answer_sentences(", main_py)
        self.assertNotIn("def sentence_is_question(", main_py)
        self.assertNotIn("def enforce_question_limits(", main_py)
        self.assertNotIn("_enforce_question_limits", main_py)

    def test_main_filters_streamed_tts_chunks_before_delivery(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        module = ast.parse(main_py)

        function_sources = {
            node.name: ast.get_source_segment(main_py, node) or ""
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"emit_stream_delta_chunks", "flush_streamed_answer_chunks"}
        }

        self.assertEqual(set(function_sources), {"emit_stream_delta_chunks", "flush_streamed_answer_chunks"})
        for source in function_sources.values():
            filter_pos = source.find("filter_stream_chunk_for_question_limits(")
            delivery_pos = source.find("await on_sentence(chunk)")
            self.assertGreaterEqual(filter_pos, 0)
            self.assertGreater(delivery_pos, filter_pos)
            self.assertIn("if not chunk:", source)


if __name__ == "__main__":
    unittest.main()
