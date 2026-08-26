from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, MutableMapping

from .cognitive_policy_state import apply_ask_gating, policy_response_for_state
from .query_intents import answer_current_datetime_query
from .question_shaping import enforce_question_limits
from .response_output_policy import (
    answer_contains_minecraft_leak,
    answer_simple_local_chat_query,
    user_explicitly_mentions_minecraft,
)
from .runtime_status_context import answer_gpu_runtime_status_query
from .session_memory_state import is_casual_call_or_status_question
from .skills.routing import (
    build_main_llm_payload,
    build_route_decision_from_state,
    decode_sse_stream_line,
    extract_main_llm_answer_from_choice,
    should_await_user_reply_for_route,
)
from .voice_pipeline import build_answer_payload_from_text, build_delivery_plan, build_route_decision
from .voice_route_execution import (
    VoiceMainLlmStreamingDeps,
    VoiceRouteExecutionDeps,
    build_voice_main_llm_streaming_deps as build_voice_main_llm_streaming_deps_from_runtime,
)


@dataclass(frozen=True)
class VoiceExecutionDependencyCompositionDeps:
    memory_index_dir: Path
    update_session_state: Callable[..., Any]
    emit_delivery_plan_chunks: Callable[..., Awaitable[Any]]
    split_tts_sentences: Callable[..., Any]
    build_search_query: Callable[..., str]
    search_duckduckgo: Callable[..., Awaitable[list[dict[str, Any]]]]
    answer_from_search_results: Callable[..., Awaitable[str]]
    prepare_llm_messages: Callable[..., Awaitable[Any]]
    apply_fast_path_question_policy: Callable[..., Any]
    synthesize_tool_result_with_main_llm: Callable[..., Awaitable[str]]
    execute_selected_specialist: Callable[..., Awaitable[str | None]]
    observe_live_minecraft_state: Callable[..., Awaitable[dict[str, Any] | None]]
    skill_registry: Any
    recent_skill_dispatches: MutableMapping[str, float]
    build_main_response_guidance: Callable[..., str]
    execute_main_llm_once: Callable[..., Awaitable[Any]]
    resolve_route_executor: Callable[..., Any]
    model_name: str
    llm_server_url: str
    main_llm_chat_content_format: str
    main_llm_stop_tokens: tuple[str, ...]
    voice_llm_max_tokens: int
    default_internal_routes: set[str]
    disabled_main_app_skill_routes: set[str]
    skill_dispatch_cache_ttl_sec: float
    skill_dispatch_repeat_window_sec: float
    skill_dispatch_cache_max: int
    router_route_timeout_sec: float
    cognitive_timeout_sec: float
    router_llm_enabled: bool
    get_http_session: Callable[..., Awaitable[Any]]
    build_runtime_status_context: Callable[..., Awaitable[str]]
    mark_turn_stage: Callable[..., Any]
    build_stream_speech_chunker: Callable[..., Any]
    sanitize_model_output: Callable[[str], str]
    parse_response_action_tag: Callable[[str], Any]
    extract_answer_from_reasoning: Callable[[str, str], str]
    resolve_promised_search_final_answer: Callable[..., Awaitable[str]]
    record_question_trace: Callable[..., Any]
    emit_stream_delta_chunks: Callable[..., Awaitable[bool]]
    record_model_call_trace: Callable[..., Any]
    sanitize_unrequested_minecraft_leak: Callable[[str, str], str]
    flush_streamed_answer_chunks: Callable[..., Awaitable[Any]]
    increment_inflight_llm_requests: Callable[[], Any]
    decrement_inflight_llm_requests: Callable[[], Any]
    log: Callable[..., Any] = print


class VoiceExecutionDependencyComposition:
    """Builds route and main-stream contracts from live voice/LLM adapters."""

    def __init__(self, deps: VoiceExecutionDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_route_execution_deps(self) -> VoiceRouteExecutionDeps:
        deps = self.deps
        return VoiceRouteExecutionDeps(
            memory_index_dir=deps.memory_index_dir,
            update_session_state=deps.update_session_state,
            emit_delivery_plan_chunks=deps.emit_delivery_plan_chunks,
            build_delivery_plan=build_delivery_plan,
            split_tts_sentences=deps.split_tts_sentences,
            build_search_query=deps.build_search_query,
            search_duckduckgo=deps.search_duckduckgo,
            answer_from_search_results=deps.answer_from_search_results,
            prepare_llm_messages=deps.prepare_llm_messages,
            policy_response_for_state=policy_response_for_state,
            build_route_decision_from_state=build_route_decision_from_state,
            apply_ask_gating=apply_ask_gating,
            build_route_decision=build_route_decision,
            apply_fast_path_question_policy=deps.apply_fast_path_question_policy,
            should_await_user_reply_for_route=should_await_user_reply_for_route,
            answer_simple_local_chat_query=answer_simple_local_chat_query,
            answer_current_datetime_query=answer_current_datetime_query,
            answer_gpu_runtime_status_query=answer_gpu_runtime_status_query,
            synthesize_tool_result_with_main_llm=deps.synthesize_tool_result_with_main_llm,
            execute_selected_specialist=deps.execute_selected_specialist,
            observe_live_minecraft_state=deps.observe_live_minecraft_state,
            skill_registry=deps.skill_registry,
            recent_skill_dispatches=deps.recent_skill_dispatches,
            build_main_response_guidance=deps.build_main_response_guidance,
            build_main_llm_payload=build_main_llm_payload,
            execute_main_llm_once=deps.execute_main_llm_once,
            build_answer_payload_from_text=build_answer_payload_from_text,
            resolve_route_executor=deps.resolve_route_executor,
            model_name=deps.model_name,
            main_llm_stop_tokens=deps.main_llm_stop_tokens,
            voice_llm_max_tokens=deps.voice_llm_max_tokens,
            default_internal_routes=deps.default_internal_routes,
            disabled_main_app_skill_routes=deps.disabled_main_app_skill_routes,
            skill_dispatch_cache_ttl_sec=deps.skill_dispatch_cache_ttl_sec,
            skill_dispatch_repeat_window_sec=deps.skill_dispatch_repeat_window_sec,
            skill_dispatch_cache_max=deps.skill_dispatch_cache_max,
            router_route_timeout_sec=deps.router_route_timeout_sec,
            cognitive_timeout_sec=deps.cognitive_timeout_sec,
            router_llm_enabled=deps.router_llm_enabled,
            log=deps.log,
        )

    def build_voice_main_llm_streaming_deps(self) -> VoiceMainLlmStreamingDeps:
        deps = self.deps
        return build_voice_main_llm_streaming_deps_from_runtime(
            model_name=deps.model_name,
            llm_server_url=deps.llm_server_url,
            memory_index_dir=deps.memory_index_dir,
            main_llm_chat_content_format=deps.main_llm_chat_content_format,
            voice_llm_max_tokens=deps.voice_llm_max_tokens,
            main_llm_stop_tokens=deps.main_llm_stop_tokens,
            get_http_session=deps.get_http_session,
            is_casual_call_or_status_question=is_casual_call_or_status_question,
            observe_live_minecraft_state=deps.observe_live_minecraft_state,
            build_runtime_status_context=deps.build_runtime_status_context,
            build_main_response_guidance=deps.build_main_response_guidance,
            mark_turn_stage=deps.mark_turn_stage,
            build_main_llm_payload=build_main_llm_payload,
            build_stream_speech_chunker=deps.build_stream_speech_chunker,
            user_explicitly_mentions_minecraft=user_explicitly_mentions_minecraft,
            extract_main_llm_answer_from_choice=extract_main_llm_answer_from_choice,
            sanitize_model_output=deps.sanitize_model_output,
            parse_response_action_tag=deps.parse_response_action_tag,
            extract_answer_from_reasoning=deps.extract_answer_from_reasoning,
            execute_main_llm_once=deps.execute_main_llm_once,
            resolve_promised_search_final_answer=deps.resolve_promised_search_final_answer,
            enforce_question_limits=enforce_question_limits,
            record_question_trace=deps.record_question_trace,
            emit_delivery_plan_chunks=deps.emit_delivery_plan_chunks,
            build_delivery_plan=build_delivery_plan,
            build_answer_payload_from_text=build_answer_payload_from_text,
            split_tts_sentences=deps.split_tts_sentences,
            decode_sse_stream_line=decode_sse_stream_line,
            answer_contains_minecraft_leak=answer_contains_minecraft_leak,
            emit_stream_delta_chunks=deps.emit_stream_delta_chunks,
            record_model_call_trace=deps.record_model_call_trace,
            sanitize_unrequested_minecraft_leak=deps.sanitize_unrequested_minecraft_leak,
            flush_streamed_answer_chunks=deps.flush_streamed_answer_chunks,
            increment_inflight_llm_requests=deps.increment_inflight_llm_requests,
            decrement_inflight_llm_requests=deps.decrement_inflight_llm_requests,
            log=deps.log,
        )


__all__ = ["VoiceExecutionDependencyComposition", "VoiceExecutionDependencyCompositionDeps"]
