from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
CONTROL_PAGE_TOOLS = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
SESSION_MEMORY_STATE = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "session_memory_state.py"


class ControlPageToolRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.control_page_tools = CONTROL_PAGE_TOOLS.read_text(encoding="utf-8")
        cls.session_memory_state = SESSION_MEMORY_STATE.read_text(encoding="utf-8")

    def test_tool_registry_and_risk_policy_exist(self) -> None:
        self.assertIn("from evelyn_core.control_page_tools import (", self.main_py)
        self.assertIn("class ControlPageToolSpec", self.control_page_tools)
        self.assertIn("CONTROL_PAGE_TOOL_SPECS: dict[str, ControlPageToolSpec]", self.control_page_tools)
        self.assertIn('"runtime.shutdown_stack": ControlPageToolSpec("runtime.shutdown_stack", "high"', self.control_page_tools)
        self.assertIn('"runtime.restart_bot": ControlPageToolSpec("runtime.restart_bot", "medium"', self.control_page_tools)
        self.assertIn('"control_page.memory_panel": ControlPageToolSpec("control_page.memory_panel", "low"', self.control_page_tools)
        self.assertIn("def control_page_tool_policy_error(", self.control_page_tools)
        self.assertIn('if spec.risk == "high" and source != "slash":', self.control_page_tools)
        self.assertIn('if spec.risk == "medium" and source == "router" and confidence < 0.86:', self.control_page_tools)

    def test_input_flow_uses_cheap_classifier_before_router_and_main(self) -> None:
        cheap_index = self.main_py.index("cheap_decision = cheap_control_page_tool_decision(text)")
        router_gate_index = self.main_py.index("if should_route_control_page_tool_candidate(text):")
        main_index = self.main_py.index("return await answer_control_page_text(guild, text)")
        self.assertLess(cheap_index, router_gate_index)
        self.assertLess(router_gate_index, main_index)
        self.assertIn("def should_route_control_page_tool_candidate(text: str) -> bool:", self.control_page_tools)
        self.assertIn("def is_control_page_question_text(text: str) -> bool:", self.control_page_tools)

    def test_tool_results_are_recorded_for_followup_context(self) -> None:
        self.assertIn("def remember_control_page_tool_turn(", self.main_py)
        self.assertIn("session_state_store.record_tool_assistant_turn(", self.main_py)
        self.assertIn("def record_tool_assistant_turn(", self.session_memory_state)
        self.assertIn('history_answer = f"도구 실행: {cleaned_tool}\\n결과: {clean_text(reply_text)}"', self.session_memory_state)
        self.assertIn("self.append_history(", self.session_memory_state)
        self.assertIn("self.mark_active(", self.session_memory_state)
        self.assertIn("remember_control_page_tool_turn(guild, text, reply, cheap_decision)", self.main_py)

    def test_router_policy_blocks_before_router_reply_is_used(self) -> None:
        policy_index = self.main_py.index("router_policy_error = control_page_tool_policy_error(tool_decision, guild_available=guild is not None)")
        reply_index = self.main_py.index("final_reply = control_page_tool_reply_from_execution(tool_decision, execute_reply)")
        self.assertLess(policy_index, reply_index)
        self.assertIn("if router_policy_error:", self.main_py)
        self.assertIn("return router_policy_error", self.main_py)
        self.assertIn("remember_control_page_tool_turn(guild, text, router_policy_error, tool_decision)", self.main_py)

    def test_router_reply_only_masks_execution_for_memory_panel(self) -> None:
        self.assertIn("def control_page_tool_reply_from_execution(decision: dict[str, Any], execute_reply: str) -> str:", self.control_page_tools)
        self.assertIn('if tool_name == "control_page.memory_panel" and router_reply:', self.control_page_tools)
        self.assertIn("return clean_text(execute_reply)", self.control_page_tools)
        self.assertNotIn("final_reply = reply or execute_reply", self.main_py)

    def test_router_prompt_is_allowlist_based(self) -> None:
        self.assertIn("def control_page_tool_registry_prompt() -> str:", self.control_page_tools)
        self.assertIn("Available allowlisted tools:", self.main_py)
        self.assertIn("never invent tools, shell commands, paths, or code", self.main_py)
        self.assertIn("Never call high-risk tools", self.main_py)
        self.assertIn("Recent conversation:", self.main_py)


if __name__ == "__main__":
    unittest.main()
