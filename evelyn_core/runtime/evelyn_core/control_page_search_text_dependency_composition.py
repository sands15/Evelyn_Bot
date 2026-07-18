from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .control_page_search_runtime import ControlPageSearchRuntimeDeps
from .control_page_text_runtime import ControlPageTextRuntimeDeps
from .text import clean_text, strip_omnivoice_tags
from .turn_lifecycle import TurnScope
from .voice_pipeline import build_route_decision


@dataclass(frozen=True)
class ControlPageSearchTextDependencyCompositionDeps:
    effective_guild_id: Callable[..., int]
    session_key_for_guild: Callable[..., str]
    get_conversation_history: Callable[..., Any]
    monotonic: Callable[[], float]
    execute_search_then_answer_action: Callable[..., Any]
    synthesize_tool_result_with_main_llm: Callable[..., Any]
    session_locks: MutableMapping[str, Any]
    lock_factory: Callable[..., Any]
    append_history: Callable[..., Any]
    mark_session_active: Callable[..., Any]
    active_conversation_text_sec: float
    build_topic_id: Callable[..., str]
    schedule_local_control_tts: Callable[..., Any]
    current_turn_id: Callable[..., Any]
    format_display_text: Callable[..., str]
    fallback_answer_for: Callable[..., str]
    begin_user_text_turn: Callable[..., Any]
    replace_room_turn_scope: Callable[..., Any]
    attach_current_task: Callable[..., Any]
    resolve_pending_proactive_question_for_turn: Callable[..., Any]
    ask_llm_streaming: Callable[..., Any]
    session_state_snapshot: Callable[..., Any]
    maybe_append_proactive_question: Callable[..., Any]
    finish_assistant_text_turn: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    detach_task: Callable[..., Any]
    clear_room_turn_scope: Callable[..., Any]


class ControlPageSearchTextDependencyComposition:
    """Builds Control Page forced-search and normal-text turn contracts."""

    def __init__(self, deps: ControlPageSearchTextDependencyCompositionDeps) -> None:
        self.deps = deps

    def _get_session_lock(self, session_key: str) -> Any:
        return self.deps.session_locks.setdefault(session_key, self.deps.lock_factory())

    def build_control_page_search_runtime_deps(self) -> ControlPageSearchRuntimeDeps:
        deps = self.deps
        return ControlPageSearchRuntimeDeps(
            control_page_effective_guild_id=deps.effective_guild_id,
            control_page_session_key=deps.session_key_for_guild,
            get_conversation_history=deps.get_conversation_history,
            build_route_decision=build_route_decision,
            monotonic=deps.monotonic,
            execute_search_then_answer_action=deps.execute_search_then_answer_action,
            synthesize_tool_result_with_main_llm=deps.synthesize_tool_result_with_main_llm,
            clean_text=clean_text,
            get_session_lock=self._get_session_lock,
            append_history=deps.append_history,
            mark_session_active=deps.mark_session_active,
            active_conversation_text_sec=deps.active_conversation_text_sec,
            build_topic_id=deps.build_topic_id,
            schedule_local_control_tts=deps.schedule_local_control_tts,
            current_turn_id=deps.current_turn_id,
            format_display_text=deps.format_display_text,
            fallback_answer_for=deps.fallback_answer_for,
        )

    def build_control_page_text_runtime_deps(self) -> ControlPageTextRuntimeDeps:
        deps = self.deps
        return ControlPageTextRuntimeDeps(
            effective_guild_id=deps.effective_guild_id,
            session_key_for_guild=deps.session_key_for_guild,
            get_session_lock=self._get_session_lock,
            begin_user_text_turn=deps.begin_user_text_turn,
            turn_scope_factory=TurnScope,
            replace_room_turn_scope=deps.replace_room_turn_scope,
            attach_current_task=deps.attach_current_task,
            monotonic=deps.monotonic,
            resolve_pending_proactive_question_for_turn=deps.resolve_pending_proactive_question_for_turn,
            ask_llm_streaming=deps.ask_llm_streaming,
            clean_text=clean_text,
            strip_omnivoice_tags=strip_omnivoice_tags,
            session_state_snapshot=deps.session_state_snapshot,
            maybe_append_proactive_question=deps.maybe_append_proactive_question,
            finish_assistant_text_turn=deps.finish_assistant_text_turn,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            schedule_local_control_tts=deps.schedule_local_control_tts,
            format_display_text=deps.format_display_text,
            fallback_answer_for=deps.fallback_answer_for,
            detach_task=deps.detach_task,
            clear_room_turn_scope=deps.clear_room_turn_scope,
        )


__all__ = [
    "ControlPageSearchTextDependencyComposition",
    "ControlPageSearchTextDependencyCompositionDeps",
]
