from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.text import ModelStreamPrefixFilter, strip_model_channel_tags, visible_text  # noqa: E402


class ModelChannelTagSanitizerTests(unittest.TestCase):
    def test_strips_empty_gemma_thought_channel_prefix(self) -> None:
        self.assertEqual(
            strip_model_channel_tags("<|channel>thought\n<channel|>[answer] visible reply"),
            "[answer] visible reply",
        )

    def test_strips_thought_section_before_closed_final_channel(self) -> None:
        self.assertEqual(
            strip_model_channel_tags("<|channel>thought<channel|>hidden<|channel>final<channel|>visible answer"),
            "visible answer",
        )

    def test_strips_thought_section_before_open_final_channel(self) -> None:
        self.assertEqual(
            strip_model_channel_tags("<|channel>thought\nhidden\n<channel|><|channel>final\nvisible answer"),
            "visible answer",
        )

    def test_visible_text_does_not_show_channel_or_action_tags(self) -> None:
        self.assertEqual(
            visible_text("<|channel>thought\n<channel|>[answer] visible reply"),
            "visible reply",
        )

    def test_stream_filter_holds_and_removes_fragmented_action_tag(self) -> None:
        stream_filter = ModelStreamPrefixFilter()

        emitted = [
            stream_filter.push("["),
            stream_filter.push("질"),
            stream_filter.push("문"),
            stream_filter.push("] "),
            stream_filter.push("마이크 입력은 꺼져 있어."),
            stream_filter.finish(),
        ]

        self.assertEqual("".join(emitted), "마이크 입력은 꺼져 있어.")
        self.assertNotIn("질문", "".join(emitted))

    def test_stream_filter_removes_fragmented_channel_and_action_prefixes(self) -> None:
        stream_filter = ModelStreamPrefixFilter()

        emitted = [
            stream_filter.push("<|chan"),
            stream_filter.push("nel>th"),
            stream_filter.push("ought\n"),
            stream_filter.push("<channel"),
            stream_filter.push("|>[답"),
            stream_filter.push("변] "),
            stream_filter.push("바로 답할게."),
            stream_filter.finish(),
        ]

        self.assertEqual("".join(emitted), "바로 답할게.")
        self.assertNotIn("channel", "".join(emitted).lower())

    def test_stream_filter_discards_think_block_before_visible_text(self) -> None:
        stream_filter = ModelStreamPrefixFilter()

        emitted = [
            stream_filter.push("<think>내부"),
            stream_filter.push(" 추론</think>"),
            stream_filter.push("최종 답변"),
            stream_filter.finish(),
        ]

        self.assertEqual("".join(emitted), "최종 답변")

    def test_stream_filter_does_not_delay_normal_visible_text(self) -> None:
        stream_filter = ModelStreamPrefixFilter()

        self.assertEqual(stream_filter.push("안"), "안")
        self.assertEqual(stream_filter.push("녕"), "녕")
        self.assertEqual(stream_filter.finish(), "")

    def test_stream_filter_drops_incomplete_internal_prefix_at_end(self) -> None:
        stream_filter = ModelStreamPrefixFilter()

        self.assertEqual(stream_filter.push("[질"), "")
        self.assertEqual(stream_filter.finish(), "")


if __name__ == "__main__":
    unittest.main()
