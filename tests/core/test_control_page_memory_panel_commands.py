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

    def test_main_routes_natural_memory_panel_commands(self) -> None:
        self.assertIn("def control_page_memory_panel_action(text: str) -> str | None:", self.main_py)
        self.assertIn("def execute_control_page_memory_panel_action(action: str) -> str:", self.main_py)
        self.assertIn("열어줘|열어|열기|보여줘|보여|띄워줘|띄워|켜줘|켜|open|show", self.main_py)
        self.assertIn("닫아줘|닫아|닫기|숨겨줘|숨겨|숨기|꺼줘|꺼|close|hide", self.main_py)
        self.assertIn('enqueue_control_page_ui_command(cleaned_action, panel_id="memory")', self.main_py)
        self.assertIn("memory_panel_action = control_page_memory_panel_action(text)", self.main_py)
        self.assertIn("return execute_control_page_memory_panel_action(memory_panel_action)", self.main_py)

    def test_local_server_can_fallback_memory_panel_commands(self) -> None:
        self.assertIn("def local_memory_panel_action(text: str) -> str | None:", self.local_server)
        self.assertIn("def with_memory_panel_command(state: dict[str, Any], action: str) -> dict[str, Any]:", self.local_server)
        self.assertIn('"action": action if action in {"open", "close", "toggle"} else "toggle"', self.local_server)
        self.assertIn("memory_action = local_memory_panel_action(text)", self.local_server)
        self.assertIn("return json_response({\"ok\": True, \"reply\": memory_panel_reply(memory_action)", self.local_server)


if __name__ == "__main__":
    unittest.main()
