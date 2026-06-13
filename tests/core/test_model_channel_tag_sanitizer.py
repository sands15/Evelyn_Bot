from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.text import strip_model_channel_tags, visible_text  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
