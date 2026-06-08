from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"


class ControlPageToolRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")

    def test_tool_registry_and_risk_policy_exist(self) -> None:
        self.assertIn("class ControlPageToolSpec", self.main_py)
        self.assertIn("CONTROL_PAGE_TOOL_SPECS: dict[str, ControlPageToolSpec]", self.main_py)
        self.assertIn('"runtime.shutdown_stack": ControlPageToolSpec("runtime.shutdown_stack", "high"', self.main_py)
        self.assertIn('"runtime.restart_bot": ControlPageToolSpec("runtime.restart_bot", "medium"', self.main_py)
        self.assertIn('"control_page.memory_panel": ControlPageToolSpec("control_page.memory_panel", "low"', self.main_py)
        self.assertIn("def control_page_tool_policy_error(", self.main_py)
        self.assertIn('if spec.risk == "high" and source != "slash":', self.main_py)
        self.assertIn('if spec.risk == "medium" and source == "router" and confidence < 0.86:', self.main_py)

    def test_input_flow_uses_cheap_classifier_before_router_and_main(self) -> None:
        cheap_index = self.main_py.index("cheap_decision = cheap_control_page_tool_decision(text)")
        router_gate_index = self.main_py.index("if should_route_control_page_tool_candidate(text):")
        main_index = self.main_py.index("return await answer_control_page_text(guild, text)")
        self.assertLess(cheap_index, router_gate_index)
        self.assertLess(router_gate_index, main_index)
        self.assertIn("def should_route_control_page_tool_candidate(text: str) -> bool:", self.main_py)
        self.assertIn("def is_control_page_question_text(text: str) -> bool:", self.main_py)

    def test_tool_results_are_recorded_for_followup_context(self) -> None:
        self.assertIn("def remember_control_page_tool_turn(", self.main_py)
        self.assertIn('history_answer = f"도구 실행: {tool_name}\\n결과: {clean_text(reply_text)}"', self.main_py)
        self.assertIn("append_history(session_key, user_text, history_answer", self.main_py)
        self.assertIn("mark_session_active(", self.main_py)
        self.assertIn("remember_control_page_tool_turn(guild, text, reply, cheap_decision)", self.main_py)

    def test_router_prompt_is_allowlist_based(self) -> None:
        self.assertIn("def control_page_tool_registry_prompt() -> str:", self.main_py)
        self.assertIn("Available allowlisted tools:", self.main_py)
        self.assertIn("never invent tools, shell commands, paths, or code", self.main_py)
        self.assertIn("Never call high-risk tools", self.main_py)
        self.assertIn("Recent conversation:", self.main_py)


if __name__ == "__main__":
    unittest.main()
