from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"


class ControlPageStatusCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")

    def test_natural_language_status_is_routed_before_general_questions(self) -> None:
        runtime_status_check = self.main_py.index("if is_control_page_runtime_status_request(normalized):")
        question_check = self.main_py.index("if is_control_page_question_text(normalized):")
        self.assertLess(runtime_status_check, question_check)
        self.assertIn("def is_control_page_runtime_status_request(text: str) -> bool:", self.main_py)
        self.assertIn('"상태" in compact', self.main_py)
        self.assertIn('"어떄"', self.main_py)
        self.assertIn('"어떠"', self.main_py)
        self.assertIn('return control_page_tool_decision("runtime.status", source="cheap")', self.main_py)

    def test_local_status_uses_real_local_runtime_summary(self) -> None:
        self.assertIn("def build_control_page_local_status_text", self.main_py)
        self.assertIn('"Evelyn 로컬 상태"', self.main_py)
        self.assertIn("control_page_local_url()", self.main_py)
        self.assertIn("Main LLM", self.main_py)
        self.assertIn("Router LLM", self.main_py)
        self.assertIn("Summary LLM", self.main_py)
        self.assertIn("local_mic_status_line()", self.main_py)
        self.assertIn("return build_control_page_local_status_text(services)", self.main_py)
        self.assertIn('"statusText": build_control_page_local_status_text(runtime_services)', self.main_py)


if __name__ == "__main__":
    unittest.main()
