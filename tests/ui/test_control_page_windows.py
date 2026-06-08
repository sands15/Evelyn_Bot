from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_windows import (  # noqa: E402
    control_page_window_choices_text,
    resolve_control_page_window_key,
)


class ControlPageWindowsTests(unittest.TestCase):
    def test_resolve_aliases_to_canonical_keys(self) -> None:
        self.assertEqual(resolve_control_page_window_key("main"), "main-llm")
        self.assertEqual(resolve_control_page_window_key("router_llm"), "router-llm")
        self.assertEqual(resolve_control_page_window_key("page"), "control-page")
        self.assertEqual(resolve_control_page_window_key("evelyn"), "bot")

    def test_unknown_window_key_returns_none(self) -> None:
        self.assertIsNone(resolve_control_page_window_key("unknown-window"))
        self.assertIsNone(resolve_control_page_window_key(""))

    def test_choices_text_mentions_supported_windows(self) -> None:
        choices = control_page_window_choices_text()
        self.assertIn("main-llm", choices)
        self.assertIn("bot", choices)
        self.assertIn("control-page", choices)


if __name__ == "__main__":
    unittest.main()
