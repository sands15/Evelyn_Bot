from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
LOCAL_SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
FAST_API = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "fast_control_api.py"
INDEX_HTML = REPO_ROOT / "docs" / "index.html"
RUNTIME_LIFECYCLE = REPO_ROOT / "runtime_lifecycle.py"
CONTROL_PAGE_TOOLS = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
CONTROL_PAGE_STATE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"
CONTROL_PAGE_TOOL_RUNTIME = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tool_runtime.py"
CONTROL_PAGE_COMPOSITION = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
RUNTIME_LIFECYCLE_COMPOSITION = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "runtime_lifecycle_composition.py"


class ControlPageRestartCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.local_server = LOCAL_SERVER.read_text(encoding="utf-8")
        cls.fast_api = FAST_API.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")
        cls.runtime_lifecycle = RUNTIME_LIFECYCLE.read_text(encoding="utf-8")
        cls.control_page_tools = CONTROL_PAGE_TOOLS.read_text(encoding="utf-8")
        cls.control_page_state = CONTROL_PAGE_STATE.read_text(encoding="utf-8")
        cls.control_page_tool_runtime = CONTROL_PAGE_TOOL_RUNTIME.read_text(encoding="utf-8")
        cls.control_page_composition = CONTROL_PAGE_COMPOSITION.read_text(encoding="utf-8")
        cls.runtime_lifecycle_composition = RUNTIME_LIFECYCLE_COMPOSITION.read_text(encoding="utf-8")

    def test_control_page_exposes_restart_command(self) -> None:
        self.assertIn('{"command": "/restart", "template": "/restart"', self.control_page_tools)
        self.assertIn("build_fast_control_default_commands", self.local_server)
        self.assertIn('{ command: "/restart", template: "/restart"', self.index_html)

    def test_control_page_slash_restart_runs_restart_path(self) -> None:
        self.assertIn("def execute_restart_command(self) -> str:", self.control_page_composition)
        self.assertIn("deps.create_task(deps.restart_bot_process())", self.control_page_tool_runtime)
        self.assertIn("create_task=asyncio.create_task", self.main_py)
        self.assertIn("restart_bot_process=restart_bot_process", self.main_py)
        self.assertIn('"/restart": "runtime.restart_bot"', self.control_page_tools)
        self.assertIn('"/재시작": "runtime.restart_bot"', self.control_page_tools)
        self.assertIn("execute_control_page_runtime_tool(", self.control_page_tool_runtime)
        self.assertIn("execute_restart_command=lambda: execute_control_page_restart_command_from_runtime(deps=deps)", self.control_page_tool_runtime)
        self.assertIn('if tool_name == "runtime.restart_bot":', self.control_page_state)
        self.assertIn("return execute_restart_command()", self.control_page_state)

    def test_fast_control_api_slash_restart_requests_local_bridge_restart(self) -> None:
        self.assertIn("build_fast_control_default_commands", self.fast_api)
        self.assertIn("def request_local_restart(", self.fast_api)
        self.assertIn("runtime_command = detect_local_runtime_command(text)", self.fast_api)
        self.assertIn('if runtime_command == "restart":', self.fast_api)
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
        self.assertIn("from runtime_lifecycle import (", self.main_py)
        self.assertIn("def runtime_prefers_local_restart(", self.runtime_lifecycle)
        self.assertIn("return bool(local_only_mode or not discord_enabled)", self.runtime_lifecycle)
        self.assertIn('project_dir / "evelyn_core" / "start_local.bat"', self.runtime_lifecycle)
        self.assertIn('"DISCORD_ENABLED": "false"', self.runtime_lifecycle)
        self.assertIn('"LOCAL_ONLY": "true"', self.runtime_lifecycle)
        self.assertIn("launch_runtime_restart_sequence=launch_runtime_restart_sequence", self.main_py)
        self.assertIn("deps.launch_runtime_restart_sequence(", self.runtime_lifecycle_composition)

    def test_natural_language_restart_is_routed_before_general_llm(self) -> None:
        self.assertIn("cheap_decision = deps.cheap_control_page_tool_decision(text)", self.control_page_tool_runtime)
        cheap_index = self.control_page_tool_runtime.index("cheap_decision = deps.cheap_control_page_tool_decision(text)")
        tool_router = self.control_page_tool_runtime.index("tool_decision_raw = await deps.decide_control_page_tool_call(")
        self.assertLess(cheap_index, tool_router)
        self.assertIn("def is_explicit_control_page_restart_request(text: str) -> bool:", self.control_page_tools)
        self.assertIn('"재시작해줘"', self.control_page_tools)
        self.assertIn('"restartnow"', self.control_page_tools)
        self.assertIn('"다시켜줘"', self.control_page_tools)

    def test_restart_questions_are_not_treated_as_restart_commands(self) -> None:
        self.assertIn("question_starts = (", self.control_page_tools)
        self.assertIn('"왜"', self.control_page_tools)
        self.assertIn('"재시작하면"', self.control_page_tools)
        self.assertIn('"재시작해야"', self.control_page_tools)
        self.assertIn('if normalized.startswith(question_starts):', self.control_page_tools)
        self.assertIn("[.!?]*", self.control_page_tools)
        self.assertIn("해줄수있어", self.control_page_tools)


if __name__ == "__main__":
    unittest.main()
