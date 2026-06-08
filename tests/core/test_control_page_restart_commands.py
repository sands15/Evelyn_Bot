from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
LOCAL_SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
INDEX_HTML = REPO_ROOT / "docs" / "index.html"


class ControlPageRestartCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.local_server = LOCAL_SERVER.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")

    def test_control_page_exposes_restart_command(self) -> None:
        self.assertIn('{"command": "/restart", "template": "/restart"', self.main_py)
        self.assertIn('{"command": "/restart", "template": "/restart"', self.local_server)
        self.assertIn('{ command: "/restart", template: "/restart"', self.index_html)

    def test_control_page_slash_restart_runs_restart_path(self) -> None:
        self.assertIn("def execute_control_page_restart_command() -> str:", self.main_py)
        self.assertIn("asyncio.create_task(restart_bot_process())", self.main_py)
        self.assertIn('"/restart": "runtime.restart_bot"', self.main_py)
        self.assertIn('"/재시작": "runtime.restart_bot"', self.main_py)
        self.assertIn('if tool_name == "runtime.restart_bot":', self.main_py)
        self.assertIn("return execute_control_page_restart_command()", self.main_py)

    def test_natural_language_restart_is_routed_before_general_llm(self) -> None:
        restart_check = self.main_py.index("if is_explicit_control_page_restart_request(normalized):")
        tool_router = self.main_py.index("tool_decision_raw = await decide_control_page_tool_call(")
        self.assertLess(restart_check, tool_router)
        self.assertIn("cheap_decision = cheap_control_page_tool_decision(text)", self.main_py)
        self.assertIn("def is_explicit_control_page_restart_request(text: str) -> bool:", self.main_py)
        self.assertIn('"재시작해줘"', self.main_py)
        self.assertIn('"restartnow"', self.main_py)
        self.assertIn('"다시켜줘"', self.main_py)

    def test_restart_questions_are_not_treated_as_restart_commands(self) -> None:
        self.assertIn("question_starts = (", self.main_py)
        self.assertIn('"왜"', self.main_py)
        self.assertIn('"재시작하면"', self.main_py)
        self.assertIn('"재시작해야"', self.main_py)
        self.assertIn('or "?" in normalized', self.main_py)


if __name__ == "__main__":
    unittest.main()
