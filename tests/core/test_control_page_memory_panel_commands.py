from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
LOCAL_SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"


class ControlPageMemoryPanelCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.local_server = LOCAL_SERVER.read_text(encoding="utf-8")

    def test_main_routes_memory_panel_commands_through_llm_tool_router(self) -> None:
        self.assertIn("async def decide_control_page_ui_tool_call(", self.main_py)
        self.assertIn("def control_page_ui_tool_action_from_decision(decision: dict[str, Any] | None) -> str | None:", self.main_py)
        self.assertIn("def execute_control_page_memory_panel_action(action: str) -> str:", self.main_py)
        self.assertIn("You are Evelyn's control-page tool router.", self.main_py)
        self.assertIn('"name":"control_page.memory_panel"', self.main_py)
        self.assertIn('decision.get("tool_calls")', self.main_py)
        self.assertIn("parsed_arguments = json.loads(arguments)", self.main_py)
        self.assertIn('{"tool_call":null,"confidence":0.0,"reply":""}', self.main_py)
        self.assertIn('"confidence":0.92', self.main_py)
        self.assertIn('purpose="control_page_ui_tool"', self.main_py)
        self.assertIn('enqueue_control_page_ui_command(cleaned_action, panel_id="memory")', self.main_py)
        self.assertIn("tool_decision = await decide_control_page_ui_tool_call(", self.main_py)
        self.assertIn("memory_panel_action = control_page_ui_tool_action_from_decision(tool_decision)", self.main_py)
        self.assertIn("execute_reply = execute_control_page_memory_panel_action(memory_panel_action)", self.main_py)
        self.assertIn("return reply or execute_reply", self.main_py)
        self.assertNotIn("def control_page_memory_panel_action(text: str) -> str | None:", self.main_py)

    def test_local_server_only_falls_back_for_explicit_memory_command(self) -> None:
        self.assertNotIn("def local_memory_panel_action(text: str) -> str | None:", self.local_server)
        self.assertNotIn("memory_action = local_memory_panel_action(text)", self.local_server)
        self.assertIn("def with_memory_panel_command(state: dict[str, Any], action: str) -> dict[str, Any]:", self.local_server)
        self.assertIn('"action": action if action in {"open", "close", "toggle"} else "toggle"', self.local_server)
        self.assertIn('if normalized == "/memory":', self.local_server)
        self.assertIn('with_memory_panel_command(state, "toggle")', self.local_server)


if __name__ == "__main__":
    unittest.main()
