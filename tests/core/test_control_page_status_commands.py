from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
CONTROL_PAGE_TOOLS = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
CONTROL_PAGE_STATE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"
CONTROL_PAGE_STATE_HANDLER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state_handler.py"


class ControlPageStatusCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.control_page_tools = CONTROL_PAGE_TOOLS.read_text(encoding="utf-8")
        cls.control_page_state = CONTROL_PAGE_STATE.read_text(encoding="utf-8")
        cls.control_page_state_handler = CONTROL_PAGE_STATE_HANDLER.read_text(encoding="utf-8")

    def test_natural_language_status_is_routed_before_general_questions(self) -> None:
        runtime_status_check = self.control_page_tools.index("if is_control_page_runtime_status_request(normalized):")
        question_check = self.control_page_tools.index("if is_control_page_question_text(normalized):")
        self.assertLess(runtime_status_check, question_check)
        self.assertIn("def is_control_page_runtime_status_request(text: str) -> bool:", self.control_page_tools)
        self.assertIn('"상태" in compact', self.control_page_tools)
        self.assertIn('"어떄"', self.control_page_tools)
        self.assertIn('"어떠"', self.control_page_tools)
        self.assertIn('return control_page_tool_decision("runtime.status", source="cheap")', self.control_page_tools)

    def test_local_status_uses_real_local_runtime_summary(self) -> None:
        composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        voice_runtime_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_runtime_composition_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def build_local_status_text", composition)
        self.assertIn("build_control_page_local_status_text_from_runtime(", composition)
        self.assertIn('"Evelyn 로컬 상태"', self.control_page_state)
        self.assertIn("control_page_local_url()", self.main_py)
        self.assertIn("Main LLM", self.control_page_state)
        self.assertIn("Router LLM", self.control_page_state)
        self.assertIn("Summary LLM", self.control_page_state)
        self.assertIn("local_mic_status_line_from_payload(self.serialize_local_mic_runtime_state())", voice_runtime_composition)
        self.assertIn("local_mic_status_line = voice_runtime_composition.local_mic_status_line", self.main_py)
        self.assertIn("build_local_status_text=control_page_composition.build_local_status_text", self.main_py)
        self.assertIn("status_text=deps.build_local_status_text(runtime_services)", self.control_page_state_handler)
        self.assertIn('"statusText": clean_text(status_text)', self.control_page_state)


if __name__ == "__main__":
    unittest.main()
