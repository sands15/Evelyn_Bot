from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .control_page_tool_runtime import ControlPageInputRuntimeDeps
from .control_page_tools import (
    cheap_control_page_tool_decision,
    control_page_tool_decision_from_llm,
    control_page_tool_policy_error,
    control_page_tool_reply_from_execution,
    should_route_control_page_tool_candidate,
)
from .query_intents import should_force_search_query
from .text import clean_text


@dataclass(frozen=True)
class ControlPageInputDependencyCompositionDeps:
    control_page: Callable[[], Any]
    effective_guild_id: Callable[..., int]
    session_key_for_guild: Callable[..., str]


class ControlPageInputDependencyComposition:
    """Builds the Control Page cheap-tool/router/search/text input contract."""

    def __init__(self, deps: ControlPageInputDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_control_page_input_runtime_deps(self) -> ControlPageInputRuntimeDeps:
        deps = self.deps
        control_page = deps.control_page()
        return ControlPageInputRuntimeDeps(
            clean_text=clean_text,
            control_page_effective_guild_id=deps.effective_guild_id,
            control_page_session_key=deps.session_key_for_guild,
            cheap_control_page_tool_decision=cheap_control_page_tool_decision,
            execute_control_page_tool=control_page.execute_tool,
            remember_control_page_tool_turn=control_page.remember_tool_turn,
            should_route_control_page_tool_candidate=should_route_control_page_tool_candidate,
            decide_control_page_tool_call=control_page.decide_tool_call,
            control_page_tool_decision_from_llm=control_page_tool_decision_from_llm,
            control_page_tool_policy_error=control_page_tool_policy_error,
            control_page_tool_reply_from_execution=control_page_tool_reply_from_execution,
            should_force_search_query=should_force_search_query,
            answer_control_page_search_text=control_page.answer_search_text,
            answer_control_page_text=control_page.answer_text,
        )


__all__ = [
    "ControlPageInputDependencyComposition",
    "ControlPageInputDependencyCompositionDeps",
]
