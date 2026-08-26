from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
MAIN_PY = REPO_ROOT / "main.py"
ROUTE_EXECUTION_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_route_execution.py"
MAIN_LLM_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "main_llm_runtime.py"
SEARCH_FOLLOWUP_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_followup_runtime.py"
SEARCH_ANSWER_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_answer_runtime.py"
CONTROL_PAGE_TOOL_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tool_runtime.py"
CONTROL_PAGE_SEARCH_RUNTIME_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_search_runtime.py"
FAST_PATH_POLICY_PY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "fast_path_policy.py"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
try:
    import numpy as _numpy  # noqa: F401
except ImportError:
    sys.modules.setdefault("numpy", SimpleNamespace(ndarray=object))

from evelyn_core.skills import delivery, main_synthesis, search  # noqa: E402
from evelyn_core.skills.registry import skill_registry  # noqa: E402


class ControlPageSearchRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = MAIN_PY.read_text(encoding="utf-8")
        cls.route_execution_py = ROUTE_EXECUTION_PY.read_text(encoding="utf-8")
        cls.main_llm_runtime_py = MAIN_LLM_RUNTIME_PY.read_text(encoding="utf-8")
        cls.search_followup_runtime_py = SEARCH_FOLLOWUP_RUNTIME_PY.read_text(encoding="utf-8")
        cls.search_answer_runtime_py = SEARCH_ANSWER_RUNTIME_PY.read_text(encoding="utf-8")
        cls.control_page_tool_runtime_py = CONTROL_PAGE_TOOL_RUNTIME_PY.read_text(encoding="utf-8")
        cls.control_page_search_runtime_py = CONTROL_PAGE_SEARCH_RUNTIME_PY.read_text(encoding="utf-8")
        cls.fast_path_policy_py = FAST_PATH_POLICY_PY.read_text(encoding="utf-8")

    def test_control_page_can_use_search_and_delivery_skills(self) -> None:
        self.assertIn("control_page", search.sources)
        self.assertIn("control_page", delivery.sources)
        self.assertIn("control_page", main_synthesis.sources)

        self.assertTrue(skill_registry.find_by_route("search_executor", source="control_page"))
        self.assertTrue(skill_registry.find_by_route("main_synthesis", source="control_page"))
        self.assertTrue(skill_registry.find_by_route("delivery", source="control_page"))

    def test_search_needed_promotes_route_to_search_executor(self) -> None:
        self.assertIn("search_needed = bool(route_decision.needs_search or context_policy.needs_search)", self.route_execution_py)
        self.assertIn("if search_needed:", self.route_execution_py)
        self.assertIn('action="search_then_answer"', self.route_execution_py)
        self.assertIn('route="search_executor"', self.route_execution_py)
        self.assertIn("needs_main_llm=False", self.route_execution_py)
        self.assertIn("needs_search=True", self.route_execution_py)

    def test_search_answers_use_deterministic_external_cards(self) -> None:
        self.assertIn("render_search_results_for_user", self.search_answer_runtime_py)
        self.assertNotIn("memory_exposure_request", self.search_answer_runtime_py)
        self.assertNotIn("session.post(", self.search_answer_runtime_py)

    def test_search_result_recurses_through_main_synthesis(self) -> None:
        search_py = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "skills" / "search" / "__init__.py").read_text(encoding="utf-8")
        composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "llm_route_composition_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertIn('followup_route="main_synthesis"', search_py)
        self.assertIn('"tool_result_text": answer_text', search_py)
        self.assertIn("def synthesize_tool_result_with_main_llm", composition)
        self.assertIn("final_answer = await deps.synthesize_tool_result_with_main_llm", self.route_execution_py)
        self.assertIn('"phase": "main_synthesis"', self.route_execution_py)

    def test_short_search_followups_use_recent_context(self) -> None:
        policy_py = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_followup_policy.py").read_text(encoding="utf-8")
        query_context_py = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_query_context.py").read_text(encoding="utf-8")

        self.assertIn("def is_generic_search_followup_text", policy_py)
        self.assertIn("def is_underspecified_weather_query", policy_py)
        self.assertIn("def resolve_contextual_search_query", query_context_py)
        self.assertIn("contextual = resolve_contextual_search_query", query_context_py)
        self.assertIn("build_search_query_from_context", self.search_followup_runtime_py)
        self.assertIn("messages=list(extras.get(\"messages\") or [])", search_py := (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "skills" / "search" / "__init__.py").read_text(encoding="utf-8"))
        self.assertIn("session_key=context.session_key", search_py)

    def test_main_synthesis_is_isolated_from_raw_history(self) -> None:
        self.assertIn("def render_tool_synthesis_recent_context", self.main_llm_runtime_py)
        self.assertIn("Ground the final answer in the tool result below.", self.main_llm_runtime_py)
        self.assertIn("messages=[]", self.main_llm_runtime_py)
        self.assertIn("def tool_synthesis_answer_drifted", self.main_llm_runtime_py)
        self.assertIn("main_synthesis_drift_guard", self.main_llm_runtime_py)
        self.assertIn('clean_text(tool_name).lower() == "search"', self.main_llm_runtime_py)
        self.assertIn("deterministic_external_cards", self.main_llm_runtime_py)

    def test_control_page_chat_does_not_use_broad_fast_path(self) -> None:
        self.assertIn("if is_control_page_source_from_runtime(source, deps=deps):\n        return None", self.fast_path_policy_py)
        self.assertIn("control_page_light_request_max_chars", self.fast_path_policy_py)

    def test_control_page_current_info_bypasses_main_llm_guessing(self) -> None:
        composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("async def answer_search_text", composition)
        self.assertIn("selected_path\": \"control_page_search_direct", self.control_page_search_runtime_py)
        self.assertIn("if deps.should_force_search_query(text):", self.control_page_tool_runtime_py)
        self.assertIn("return await deps.answer_control_page_search_text(guild, text)", self.control_page_tool_runtime_py)

    def test_weather_search_query_can_use_recent_location_memory(self) -> None:
        query_context_py = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "search_query_context.py").read_text(encoding="utf-8")

        self.assertIn("def enrich_weather_search_query_from_context", query_context_py)
        self.assertIn("resolve_recent_weather_location(recent_users)", query_context_py)
        self.assertIn('return clean_text(f"{location} {text}")', query_context_py)


if __name__ == "__main__":
    unittest.main()
