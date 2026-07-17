from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .main_llm_runtime import AskLlmOnceRuntimeDeps, MainLlmRuntimeDeps
from .voice_response_runtime import VoiceResponseRuntimeDeps
from .voice_stream_chunks import VoiceStreamChunkDeps


@dataclass(frozen=True)
class VoiceResponseDependencyCompositionDeps:
    model_name: str
    llm_server_url: str
    main_llm_chat_content_format: str
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    voice_llm_max_tokens: int
    get_http_session: Callable[..., Any]
    build_chat_messages: Callable[..., Any]
    fallback_answer_for: Callable[..., str]
    split_tts_sentences: Callable[..., Any]
    build_answer_payload_from_text: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    prepare_route_context: Callable[..., Any]
    prepare_llm_messages: Callable[..., Any]
    is_user_echo_answer: Callable[..., bool]
    is_casual_call_or_status_question: Callable[..., bool]
    observe_live_minecraft_state: Callable[..., Any]
    build_runtime_status_context: Callable[..., Any]
    build_main_response_guidance: Callable[..., str]
    sanitize_model_output: Callable[..., str]
    parse_response_action_tag: Callable[..., Any]
    extract_answer_from_reasoning: Callable[..., str]
    sanitize_unrequested_minecraft_leak: Callable[..., str]
    enforce_question_limits: Callable[..., Any]
    record_question_trace: Callable[..., Any]
    format_minecraft_state_summary: Callable[..., str]
    extract_main_llm_answer_from_choice: Callable[..., Any]
    compact_memory_text: Callable[..., str]
    build_main_llm_payload: Callable[..., dict[str, Any]]
    strip_search_answer_sources: Callable[..., str]
    answer_promises_search: Callable[..., bool]
    has_negated_search_marker: Callable[..., bool]
    execute_search_then_answer_action: Callable[..., Any]
    clean_text: Callable[[str], str]
    maybe_execute_registered_route: Callable[..., Any]
    update_session_state: Callable[..., Any]
    execute_main_llm_once: Callable[..., Any]
    resolve_promised_search_final_answer: Callable[..., Any]
    tts_first_chunk_min_chars: int
    tts_first_chunk_target_chars: int
    tts_first_chunk_max_chars: int
    tts_next_chunk_min_chars: int
    tts_next_chunk_target_chars: int
    tts_next_chunk_max_chars: int
    log: Callable[..., Any]


class VoiceResponseDependencyComposition:
    """Builds voice response, main LLM, and stream-chunk contracts."""

    def __init__(self, deps: VoiceResponseDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_response_runtime_deps(self) -> VoiceResponseRuntimeDeps:
        deps = self.deps
        return VoiceResponseRuntimeDeps(
            model_name=deps.model_name,
            llm_server_url=deps.llm_server_url,
            main_llm_chat_content_format=deps.main_llm_chat_content_format,
            main_llm_stop_tokens=tuple(deps.main_llm_stop_tokens),
            voice_llm_max_tokens=deps.voice_llm_max_tokens,
            get_http_session=deps.get_http_session,
            build_chat_messages=deps.build_chat_messages,
            fallback_answer_for=deps.fallback_answer_for,
            split_tts_sentences=deps.split_tts_sentences,
            build_answer_payload_from_text=deps.build_answer_payload_from_text,
            log_voice_stage=deps.log_voice_stage,
            prepare_route_context=deps.prepare_route_context,
            prepare_llm_messages=deps.prepare_llm_messages,
            is_user_echo_answer=deps.is_user_echo_answer,
            is_casual_call_or_status_question=deps.is_casual_call_or_status_question,
            observe_live_minecraft_state=deps.observe_live_minecraft_state,
            build_runtime_status_context=deps.build_runtime_status_context,
            build_main_response_guidance=deps.build_main_response_guidance,
            sanitize_model_output=deps.sanitize_model_output,
            parse_response_action_tag=deps.parse_response_action_tag,
            extract_answer_from_reasoning=deps.extract_answer_from_reasoning,
            sanitize_unrequested_minecraft_leak=deps.sanitize_unrequested_minecraft_leak,
            enforce_question_limits=deps.enforce_question_limits,
            record_question_trace=deps.record_question_trace,
            format_minecraft_state_summary=deps.format_minecraft_state_summary,
            log=deps.log,
        )

    def build_main_llm_runtime_deps(self) -> MainLlmRuntimeDeps:
        deps = self.deps
        return MainLlmRuntimeDeps(
            model_name=deps.model_name,
            llm_server_url=deps.llm_server_url,
            main_llm_chat_content_format=deps.main_llm_chat_content_format,
            main_llm_stop_tokens=tuple(deps.main_llm_stop_tokens),
            voice_llm_max_tokens=deps.voice_llm_max_tokens,
            get_http_session=deps.get_http_session,
            fallback_answer_for=deps.fallback_answer_for,
            extract_main_llm_answer_from_choice=deps.extract_main_llm_answer_from_choice,
            sanitize_model_output=deps.sanitize_model_output,
            parse_response_action_tag=deps.parse_response_action_tag,
            extract_answer_from_reasoning=deps.extract_answer_from_reasoning,
            compact_memory_text=deps.compact_memory_text,
            build_main_response_guidance=deps.build_main_response_guidance,
            build_main_llm_payload=deps.build_main_llm_payload,
            strip_search_answer_sources=deps.strip_search_answer_sources,
            enforce_question_limits=deps.enforce_question_limits,
            record_question_trace=deps.record_question_trace,
            answer_promises_search=deps.answer_promises_search,
            has_negated_search_marker=deps.has_negated_search_marker,
            execute_search_then_answer_action=deps.execute_search_then_answer_action,
            log=deps.log,
        )

    def build_ask_llm_once_runtime_deps(self) -> AskLlmOnceRuntimeDeps:
        deps = self.deps
        return AskLlmOnceRuntimeDeps(
            log_voice_stage=deps.log_voice_stage,
            clean_text=deps.clean_text,
            prepare_route_context=deps.prepare_route_context,
            maybe_execute_registered_route=deps.maybe_execute_registered_route,
            is_user_echo_answer=deps.is_user_echo_answer,
            update_session_state=deps.update_session_state,
            build_answer_payload_from_text=deps.build_answer_payload_from_text,
            session_is_casual_call_or_status_question=(
                deps.is_casual_call_or_status_question
            ),
            observe_live_minecraft_state=deps.observe_live_minecraft_state,
            build_runtime_status_context=deps.build_runtime_status_context,
            build_main_response_guidance=deps.build_main_response_guidance,
            build_main_llm_payload=deps.build_main_llm_payload,
            execute_main_llm_once=deps.execute_main_llm_once,
            sanitize_unrequested_minecraft_leak=deps.sanitize_unrequested_minecraft_leak,
            resolve_promised_search_final_answer=deps.resolve_promised_search_final_answer,
            enforce_question_limits=deps.enforce_question_limits,
            record_question_trace=deps.record_question_trace,
            model_name=deps.model_name,
            main_llm_chat_content_format=deps.main_llm_chat_content_format,
            voice_llm_max_tokens=deps.voice_llm_max_tokens,
            main_llm_stop_tokens=deps.main_llm_stop_tokens,
        )

    def build_voice_stream_chunk_deps(self) -> VoiceStreamChunkDeps:
        deps = self.deps
        return VoiceStreamChunkDeps(
            tts_first_chunk_min_chars=deps.tts_first_chunk_min_chars,
            tts_first_chunk_target_chars=deps.tts_first_chunk_target_chars,
            tts_first_chunk_max_chars=deps.tts_first_chunk_max_chars,
            tts_next_chunk_min_chars=deps.tts_next_chunk_min_chars,
            tts_next_chunk_target_chars=deps.tts_next_chunk_target_chars,
            tts_next_chunk_max_chars=deps.tts_next_chunk_max_chars,
        )


__all__ = [
    "VoiceResponseDependencyComposition",
    "VoiceResponseDependencyCompositionDeps",
]
