from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .cognitive_policy_state import apply_ask_gating, ask_confidence_threshold_for_source, build_fast_cognitive_state
from .context_pipeline import (
    build_basic_context_packet,
    build_context_policy_for_turn,
    build_conversation_state_context,
    build_minecraft_skill_context,
    build_runtime_state_context,
    build_skill_context_hint,
    build_tool_use_decisions,
    build_vision_context_hint,
    render_tool_use_context,
)
from .llm_context_assembly import LlmContextAssemblyDeps
from .local_runtime_context import build_evelyn_runtime_dependency_context_from_payload
from .local_tool_diagnostic_context import build_local_tool_diagnostic_context
from .memory_context_state import build_memory_context
from .minecraft_runtime_snapshot import attach_minecraft_runtime_snapshot
from .route_fallback_policy import classify_llm_route_fallback
from .self_model import render_self_judgment_context, render_self_state_context, update_self_state_for_turn
from .text import clean_text, visible_text
from .vision_watch import render_vision_watch_context


@dataclass(frozen=True)
class LlmContextAssemblyCompositionDeps:
    compute_runtime_mode: Callable[[dict | None], str]
    apply_runtime_mode: Callable[..., dict[str, Any]]
    classify_llm_route_async: Callable[..., Awaitable[tuple[str, dict | None]]]
    session_topic_ids: dict[str, str]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    read_cached_cognitive_state: Callable[..., dict | None]
    get_matching_speculative_policy: Callable[..., dict | None]
    fast_path_policy: Callable[..., dict | None]
    session_state_snapshot: Callable[..., dict[str, Any]]
    context_policy_for_fast_path_policy: Callable[..., dict[str, Any]]
    extract_question_policy_from_route_meta: Callable[..., dict[str, Any]]
    update_cognitive_state: Callable[..., Awaitable[dict[str, Any]]]
    schedule_cognitive_refresh: Callable[..., Any]
    build_runtime_status_context: Callable[..., Awaitable[str]]
    project_root: Path
    observe_live_minecraft_state: Callable[..., Awaitable[dict[str, Any] | None]]
    control_page_minecraft_cache_refresh_sec: float
    control_page_minecraft_cache_max_stale_sec: float
    local_tts_snapshot: Callable[[], dict[str, Any]]
    local_mic_snapshot: Callable[[], dict[str, Any]]
    local_only_mode: bool
    discord_enabled: bool
    model_name: str
    llm_server_url: str
    router_model_name: str
    summary_model_name: str
    stt_model_name: str
    stt_backend: str
    omnivoice_server_url: str
    omnivoice_voice: str
    omnivoice_speed: float
    voice_input_mode_status_line: Callable[[], str]
    odyssey_capability_json_dir: Path
    build_live_vision_context: Callable[..., Awaitable[str]]
    log_turn_event: Callable[..., Any]
    log: Callable[..., Any] = print


class LlmContextAssemblyComposition:
    """Builds the live LLM context contract from runtime-owned state."""

    def __init__(self, deps: LlmContextAssemblyCompositionDeps) -> None:
        self.deps = deps

    def build_evelyn_runtime_dependency_context(self) -> str:
        deps = self.deps
        return build_evelyn_runtime_dependency_context_from_payload(
            local_tts=deps.local_tts_snapshot(),
            local_mic=deps.local_mic_snapshot(),
            local_only_mode=deps.local_only_mode,
            discord_enabled=deps.discord_enabled,
            model_name=deps.model_name,
            llm_server_url=deps.llm_server_url,
            router_model_name=deps.router_model_name,
            summary_model_name=deps.summary_model_name,
            stt_model_name=deps.stt_model_name,
            stt_backend=deps.stt_backend,
            omnivoice_server_url=deps.omnivoice_server_url,
            omnivoice_voice=deps.omnivoice_voice,
            omnivoice_speed=deps.omnivoice_speed,
            voice_input_mode_status_line=deps.voice_input_mode_status_line(),
        )

    def build_runtime_deps(self) -> LlmContextAssemblyDeps:
        deps = self.deps
        return LlmContextAssemblyDeps(
            compute_runtime_mode=deps.compute_runtime_mode,
            apply_runtime_mode=deps.apply_runtime_mode,
            classify_llm_route_fallback=classify_llm_route_fallback,
            classify_llm_route_async=deps.classify_llm_route_async,
            session_topic_ids=deps.session_topic_ids,
            get_conversation_history=deps.get_conversation_history,
            read_cached_cognitive_state=deps.read_cached_cognitive_state,
            get_matching_speculative_policy=deps.get_matching_speculative_policy,
            fast_path_policy=deps.fast_path_policy,
            session_state_snapshot=deps.session_state_snapshot,
            context_policy_for_fast_path_policy=deps.context_policy_for_fast_path_policy,
            extract_question_policy_from_route_meta=deps.extract_question_policy_from_route_meta,
            build_fast_cognitive_state=build_fast_cognitive_state,
            update_cognitive_state=deps.update_cognitive_state,
            schedule_cognitive_refresh=deps.schedule_cognitive_refresh,
            build_context_policy_for_turn=build_context_policy_for_turn,
            build_tool_use_decisions=build_tool_use_decisions,
            build_runtime_status_context=deps.build_runtime_status_context,
            clean_text=clean_text,
            build_local_tool_diagnostic_context=build_local_tool_diagnostic_context,
            project_root=deps.project_root,
            build_memory_context=build_memory_context,
            update_self_state_for_turn=update_self_state_for_turn,
            observe_live_minecraft_state=deps.observe_live_minecraft_state,
            attach_minecraft_runtime_snapshot=attach_minecraft_runtime_snapshot,
            control_page_minecraft_cache_refresh_sec=deps.control_page_minecraft_cache_refresh_sec,
            control_page_minecraft_cache_max_stale_sec=deps.control_page_minecraft_cache_max_stale_sec,
            build_conversation_state_context=build_conversation_state_context,
            build_runtime_state_context=build_runtime_state_context,
            build_evelyn_runtime_dependency_context=self.build_evelyn_runtime_dependency_context,
            render_self_judgment_context=render_self_judgment_context,
            render_self_state_context=render_self_state_context,
            render_vision_watch_context=render_vision_watch_context,
            build_minecraft_skill_context=build_minecraft_skill_context,
            odyssey_capability_json_dir=deps.odyssey_capability_json_dir,
            build_skill_context_hint=build_skill_context_hint,
            build_vision_context_hint=build_vision_context_hint,
            build_live_vision_context=deps.build_live_vision_context,
            render_tool_use_context=render_tool_use_context,
            build_basic_context_packet=build_basic_context_packet,
            ask_confidence_threshold_for_source=ask_confidence_threshold_for_source,
            apply_ask_gating=apply_ask_gating,
            log_turn_event=deps.log_turn_event,
            visible_text=visible_text,
            log=deps.log,
        )


__all__ = ["LlmContextAssemblyComposition", "LlmContextAssemblyCompositionDeps"]
