from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
LOCAL_SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
FAST_API = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "fast_control_api.py"
INDEX_HTML = REPO_ROOT / "docs" / "index.html"
CONTROL_PAGE_JS = REPO_ROOT / "docs" / "assets" / "evelyn-page.js"


class ControlPageRestartCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.local_server = LOCAL_SERVER.read_text(encoding="utf-8")
        cls.fast_api = FAST_API.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")
        cls.control_page_js = CONTROL_PAGE_JS.read_text(encoding="utf-8")

    def test_control_page_exposes_restart_command(self) -> None:
        self.assertIn('{"command": "/restart", "template": "/restart"', self.main_py)
        self.assertIn('{"command": "/restart", "template": "/restart"', self.local_server)
        self.assertIn('{ command: "/restart", template: "/restart"', self.index_html)
        self.assertIn('{ command: "/restart", template: "/restart"', self.control_page_js)
        self.assertIn('summary: "Restart Evelyn runtime"', self.control_page_js)

    def test_control_page_slash_restart_runs_restart_path(self) -> None:
        self.assertIn("def execute_control_page_restart_command() -> str:", self.main_py)
        self.assertIn("asyncio.create_task(restart_bot_process())", self.main_py)
        self.assertIn('"/restart": "runtime.restart_bot"', self.main_py)
        self.assertIn('"/재시작": "runtime.restart_bot"', self.main_py)
        self.assertIn('if tool_name == "runtime.restart_bot":', self.main_py)
        self.assertIn("return execute_control_page_restart_command()", self.main_py)

    def test_fast_control_api_slash_restart_requests_local_bridge_restart(self) -> None:
        self.assertIn("build_fast_control_default_commands", self.fast_api)
        self.assertIn("def request_local_restart(", self.fast_api)
        self.assertIn('if normalized in {"/restart", "restart"}:', self.fast_api)
        self.assertIn("request_local_restart(source=source, reason=\"chat_command\")", self.fast_api)
        self.assertIn('"restart": dict(RESTART_REQUEST)', self.fast_api)

    def test_public_control_page_restart_proxies_then_falls_back_to_local_restart(self) -> None:
        self.assertIn('LOCAL_RESTART_COMMANDS = {"/restart", "restart"}', self.local_server)
        self.assertIn("def schedule_local_stack_restart(", self.local_server)
        restart_branch = self.local_server[self.local_server.index("if normalized in LOCAL_RESTART_COMMANDS:") :]
        proxy_index = restart_branch.index('proxy_json(request, "POST", "/api/control-page/chat", body=payload)')
        fallback_index = restart_branch.index("ok, detail = schedule_local_stack_restart()")
        self.assertLess(proxy_index, fallback_index)

    def test_local_runtime_restart_uses_local_launcher(self) -> None:
        self.assertIn("def current_runtime_prefers_local_restart() -> bool:", self.main_py)
        self.assertIn("return bool(LOCAL_ONLY_MODE or not DISCORD_ENABLED)", self.main_py)
        self.assertIn('project_dir / "evelyn_core" / "start_local.bat"', self.main_py)
        self.assertIn('"DISCORD_ENABLED": "false"', self.main_py)
        self.assertIn('"LOCAL_ONLY": "true"', self.main_py)
        self.assertIn("restart_launcher_for_current_mode(project_dir)", self.main_py)

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
        self.assertIn('if normalized.startswith(question_starts):', self.main_py)
        self.assertIn("[.!?]*", self.main_py)
        self.assertIn("해줄수있어", self.main_py)


if __name__ == "__main__":
    unittest.main()
