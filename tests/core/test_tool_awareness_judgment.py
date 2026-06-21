from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
MAIN_PY = REPO_ROOT / "main.py"
ROUTE_EXECUTION_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_route_execution.py"
MAIN_LLM_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "main_llm_runtime.py"
SEARCH_FOLLOWUP_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_followup_runtime.py"
TOOL_AWARENESS_POLICY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "tool_awareness_policy.py"
SEARCH_FOLLOWUP_POLICY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_followup_policy.py"
BLUEPRINT = REPO_ROOT / "docs" / "tool_awareness_blueprint.md"


class ToolAwarenessJudgmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.route_execution_py = ROUTE_EXECUTION_PY.read_text(encoding="utf-8")
        cls.main_llm_runtime_py = MAIN_LLM_RUNTIME_PY.read_text(encoding="utf-8")
        cls.search_followup_runtime_py = SEARCH_FOLLOWUP_RUNTIME_PY.read_text(encoding="utf-8")
        cls.tool_awareness_policy = TOOL_AWARENESS_POLICY.read_text(encoding="utf-8")
        cls.search_followup_policy = SEARCH_FOLLOWUP_POLICY.read_text(encoding="utf-8")
        cls.blueprint = BLUEPRINT.read_text(encoding="utf-8")

    def test_blueprint_exists_for_implementation_contract(self) -> None:
        self.assertIn("Tool Awareness Judgment Blueprint", self.blueprint)
        self.assertIn("Promise Escalation", self.blueprint)
        self.assertIn("Tool Selection Rules", self.blueprint)

    def test_main_guidance_gets_runtime_tool_awareness_context(self) -> None:
        self.assertIn("from evelyn_core.tool_awareness_policy import build_tool_awareness_context", self.main_py)
        self.assertIn("TOOL_AWARENESS: Runtime, not memory, is the source of truth for tools.", self.tool_awareness_policy)
        self.assertIn("Available tool shortlist for this turn", self.tool_awareness_policy)
        self.assertIn("do not give only a promise", self.tool_awareness_policy)
        self.assertIn("tool_awareness_context = build_tool_awareness_context", self.main_py)
        self.assertIn("route_available=_skill_route_available", self.main_py)
        self.assertIn("parts.append(tool_awareness_context)", self.main_py)

    def test_tool_awareness_uses_runtime_skill_registry_for_search(self) -> None:
        self.assertIn("def _skill_route_available", self.main_py)
        self.assertIn('skill_registry.find_by_route(route_name, source=source)', self.main_py)
        self.assertIn('route_available(route_name, source=source)', self.tool_awareness_policy)
        self.assertIn('_route_available(route_available, "search_executor", source=source)', self.tool_awareness_policy)
        self.assertIn("- search: use for current info, weather, prices, news", self.tool_awareness_policy)

    def test_promised_search_is_escalated_to_tool_result_synthesis(self) -> None:
        self.assertIn("async def resolve_promised_search_final_answer", self.main_py)
        self.assertIn("deps.answer_promises_search(answer)", self.main_llm_runtime_py)
        self.assertIn("promised_search_escalated", self.main_llm_runtime_py)
        self.assertIn("action_result = await deps.execute_search_then_answer_action", self.main_llm_runtime_py)
        self.assertIn("final_answer = await synthesize_tool_result_with_main_llm_from_runtime", self.main_llm_runtime_py)
        self.assertIn("return clean_text(action_result.answer_text) or answer", self.main_llm_runtime_py)

    def test_realtime_skip_does_not_drop_search_promises(self) -> None:
        skip_index = self.search_followup_runtime_py.index('opts.get("skip_search_followup")')
        fallback_index = self.search_followup_runtime_py.index("wants_search_by_fallback = deps.answer_promises_search")
        self.assertGreater(skip_index, fallback_index)
        self.assertIn("and not wants_search_by_tag and not wants_search_by_fallback", self.search_followup_runtime_py)

    def test_main_paths_call_promise_escalation(self) -> None:
        combined = self.main_py + self.route_execution_py
        self.assertGreaterEqual(
            self.main_py.count("await resolve_promised_search_final_answer")
            + self.route_execution_py.count("await deps.resolve_promised_search_final_answer"),
            3,
        )
        self.assertIn("messages=messages", combined)
        self.assertIn("route_decision=route_decision", combined)

    def test_promise_detector_has_plain_korean_and_english_fallbacks(self) -> None:
        self.assertIn("promise_regexes = (", self.search_followup_policy)
        self.assertIn("찾아|검색|확인|알아|조사", self.search_followup_policy)
        self.assertIn("search|look up|check|find", self.search_followup_policy)


if __name__ == "__main__":
    unittest.main()
