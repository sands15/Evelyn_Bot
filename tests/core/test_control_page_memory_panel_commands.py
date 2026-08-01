from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
LOCAL_SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"
CONTROL_PAGE_TOOLS = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
CONTROL_PAGE_STATE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"
CONTROL_PAGE_STATE_HANDLER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state_handler.py"
CONTROL_PAGE_CONTRACTS = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_contracts.py"
CONTROL_PAGE_TOOL_RUNTIME = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tool_runtime.py"
CONTROL_PAGE_COMPOSITION = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"


class ControlPageMemoryPanelCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.local_server = LOCAL_SERVER.read_text(encoding="utf-8")
        cls.control_page_tools = CONTROL_PAGE_TOOLS.read_text(encoding="utf-8")
        cls.control_page_state = CONTROL_PAGE_STATE.read_text(encoding="utf-8")
        cls.control_page_state_handler = CONTROL_PAGE_STATE_HANDLER.read_text(encoding="utf-8")
        cls.control_page_contracts = CONTROL_PAGE_CONTRACTS.read_text(encoding="utf-8")
        cls.control_page_tool_runtime = CONTROL_PAGE_TOOL_RUNTIME.read_text(encoding="utf-8")
        cls.control_page_composition = CONTROL_PAGE_COMPOSITION.read_text(encoding="utf-8")

    def test_main_routes_memory_panel_commands_through_llm_tool_router(self) -> None:
        self.assertIn("async def decide_control_page_tool_call_from_runtime(", self.control_page_tool_runtime)
        self.assertIn("async def decide_tool_call(", self.control_page_composition)
        self.assertIn("def control_page_ui_tool_action_from_decision(decision: dict[str, Any] | None) -> str | None:", self.control_page_tools)
        self.assertIn("def execute_memory_panel_action(self, action: str) -> str:", self.control_page_composition)
        self.assertIn("return deps.memory_panel_reply(cleaned_action)", self.control_page_tool_runtime)
        self.assertIn("def memory_panel_reply(action: str) -> str:", self.control_page_contracts)
        self.assertIn("class ControlPageToolSpec", self.control_page_tools)
        self.assertIn('"control_page.memory_panel": ControlPageToolSpec', self.control_page_tools)
        self.assertIn("You are Evelyn's control-page tool router.", self.control_page_tool_runtime)
        self.assertIn('"name": spec.name', self.control_page_tools)
        self.assertIn('return json.dumps(tools, ensure_ascii=False, separators=(",", ":"))', self.control_page_tools)
        self.assertIn('decision.get("tool_calls")', self.control_page_tools)
        self.assertIn("parsed = json.loads(arguments)", self.control_page_tools)
        self.assertIn('{"tool_call":null,"confidence":0.0,"reply":""}', self.control_page_tool_runtime)
        self.assertIn('"confidence":0.92', self.control_page_tool_runtime)
        self.assertIn('"reply":"응, 메모리 패널 열어둘게."', self.control_page_tool_runtime)
        self.assertIn("write reply in Evelyn's style", self.control_page_tool_runtime)
        self.assertIn("casual 반말", self.control_page_tool_runtime)
        self.assertIn("no stiff '~습니다' or '~입니다' endings", self.control_page_tool_runtime)
        self.assertIn('purpose="control_page_ui_tool"', self.control_page_tool_runtime)
        self.assertIn('deps.enqueue_control_page_ui_command(cleaned_action, panel_id="memory")', self.control_page_tool_runtime)
        self.assertIn("cheap_decision = deps.cheap_control_page_tool_decision(text)", self.control_page_tool_runtime)
        self.assertIn("if deps.should_route_control_page_tool_candidate(text):", self.control_page_tool_runtime)
        self.assertIn("tool_decision_raw = await deps.decide_control_page_tool_call(", self.control_page_tool_runtime)
        self.assertIn("tool_decision = deps.control_page_tool_decision_from_llm(tool_decision_raw)", self.control_page_tool_runtime)
        self.assertIn("memory_receipt_ref=router_receipt_ref", self.control_page_tool_runtime)
        self.assertNotIn("def control_page_memory_panel_action(text: str) -> str | None:", self.main_py)

    def test_local_server_only_falls_back_for_explicit_memory_command(self) -> None:
        self.assertNotIn("def local_memory_panel_action(text: str) -> str | None:", self.local_server)
        self.assertNotIn("memory_action = local_memory_panel_action(text)", self.local_server)
        self.assertIn("def with_memory_panel_command(state: dict[str, Any], action: str) -> dict[str, Any]:", self.local_server)
        self.assertIn('"action": action if action in {"open", "close", "toggle"} else "toggle"', self.local_server)
        self.assertIn('if normalized == "/memory":', self.local_server)
        self.assertIn('with_memory_panel_command(state, "toggle")', self.local_server)

    def test_main_exposes_panel_commands_at_top_level_for_frontend(self) -> None:
        state_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state_composition.py"
        ).read_text(encoding="utf-8")
        self.assertIn("build_control_page_state_from_runtime(", state_composition)
        self.assertIn("build_control_page_state = control_page_state_composition.build_control_page_state", self.main_py)
        self.assertIn("return build_control_page_local_state_view(", self.control_page_state_handler)
        self.assertIn("return build_control_page_guild_state_view(", self.control_page_state_handler)
        self.assertIn("def build_control_page_local_state_view(", self.control_page_state)
        self.assertIn("def build_control_page_guild_state_view(", self.control_page_state)
        self.assertGreaterEqual(self.control_page_state.count('"controlPagePanels": dict(control_page_panels or {}),'), 2)
        self.assertIn('"controlPagePanels": dict(control_page_panels or {}),', self.control_page_state)

    def test_local_server_proxy_timeout_allows_full_bot_state_payload(self) -> None:
        self.assertIn('PROXY_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_PROXY_TIMEOUT_SEC", "6.0"))', self.local_server)


if __name__ == "__main__":
    unittest.main()
