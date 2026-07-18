from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .cognitive_followup_policy import ShouldForceSearchFollowupRuntimeDeps
from .cognitive_state_runtime import CognitiveStateRuntimeDeps
from .json_llm_request_runtime import JsonLlmRequestRuntimeDeps
from .llm_route_runtime import LlmRouteRuntimeDeps


@dataclass(frozen=True)
class LlmCognitiveDependencyCompositionDeps:
    read_cached_cognitive_state: Callable[..., Any]
    apply_ask_gating: Callable[..., Any]
    clean_text: Callable[[str], str]
    summary_model_name: str
    summary_llm_url: str
    router_model_name: str
    router_llm_url: str
    get_http_session: Callable[..., Any]
    client_timeout_factory: Callable[..., Any]
    monotonic: Callable[[], float]
    extract_json_object: Callable[..., dict[str, Any]]
    record_model_call_trace: Callable[..., Any]
    classify_llm_route_fallback: Callable[..., str]
    fast_path_policy: Callable[..., Any]
    session_state_snapshot: Callable[..., Any]
    load_working_summary: Callable[..., str]
    load_cognitive_state: Callable[..., dict[str, Any]]
    normalize_cognitive_state: Callable[..., dict[str, Any]]
    load_recent_raw: Callable[..., Any]
    load_recent_facts: Callable[..., Any]
    format_memory_rows_for_llm: Callable[..., str]
    compact_memory_text: Callable[..., str]
    ask_router_llm: Callable[..., Any]
    current_turn_id: Callable[..., Any]
    normalize_question_policy_mapping: Callable[..., dict[str, Any]]
    router_route_timeout_sec: float
    cognitive_timeout_sec: float
    router_llm_enabled: bool
    router_route_max_tokens: int
    attach_current_task: Callable[..., Any]
    detach_task: Callable[..., Any]
    cognitive_locks: MutableMapping[int, Any]
    collect_memory_layers: Callable[..., Any]
    layered_summary_text: Callable[..., str]
    read_layered_cognitive_state: Callable[..., Any]
    get_matching_speculative_policy: Callable[..., Any]
    build_fast_cognitive_state: Callable[..., dict[str, Any]]
    write_json_file: Callable[..., Any]
    cognitive_state_path: Callable[..., Any]
    recent_memory_groups: Callable[..., Any]
    memory_cognitive_raw_limit: int
    build_cognitive_state_messages: Callable[..., Any]
    cognitive_max_tokens: int
    is_context_size_error: Callable[..., bool]
    build_compact_cognitive_state_messages: Callable[..., Any]
    should_log_voice_timing: Callable[..., bool]
    build_cognitive_fallback_state: Callable[..., dict[str, Any]]
    finalize_cognitive_state: Callable[..., dict[str, Any]]
    log: Callable[..., Any]


class LlmCognitiveDependencyComposition:
    """Builds cognitive follow-up, JSON LLM, routing, and cognitive-state contracts."""

    def __init__(self, deps: LlmCognitiveDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_cognitive_followup_runtime_deps(self) -> ShouldForceSearchFollowupRuntimeDeps:
        deps = self.deps
        return ShouldForceSearchFollowupRuntimeDeps(
            read_cached_cognitive_state_fn=deps.read_cached_cognitive_state,
            apply_ask_gating_fn=deps.apply_ask_gating,
            clean_text_fn=deps.clean_text,
        )

    def _build_json_llm_runtime_deps(
        self, *, model_name: str, endpoint: str, model_role: str, error_label: str
    ) -> JsonLlmRequestRuntimeDeps:
        deps = self.deps
        return JsonLlmRequestRuntimeDeps(
            model_name=model_name,
            endpoint=endpoint,
            model_role=model_role,
            error_label=error_label,
            get_http_session=deps.get_http_session,
            client_timeout_factory=deps.client_timeout_factory,
            monotonic=deps.monotonic,
            clean_text=deps.clean_text,
            extract_json_object=deps.extract_json_object,
            record_model_call_trace=deps.record_model_call_trace,
        )

    def build_summary_json_llm_runtime_deps(self) -> JsonLlmRequestRuntimeDeps:
        return self._build_json_llm_runtime_deps(
            model_name=self.deps.summary_model_name,
            endpoint=self.deps.summary_llm_url,
            model_role="summary",
            error_label="요약 LLM",
        )

    def build_router_json_llm_runtime_deps(self) -> JsonLlmRequestRuntimeDeps:
        return self._build_json_llm_runtime_deps(
            model_name=self.deps.router_model_name,
            endpoint=self.deps.router_llm_url,
            model_role="router",
            error_label="router LLM",
        )

    def build_llm_route_runtime_deps(self) -> LlmRouteRuntimeDeps:
        deps = self.deps
        return LlmRouteRuntimeDeps(
            classify_llm_route_fallback=deps.classify_llm_route_fallback,
            fast_path_policy=deps.fast_path_policy,
            session_state_snapshot=deps.session_state_snapshot,
            load_working_summary=deps.load_working_summary,
            load_cognitive_state=deps.load_cognitive_state,
            normalize_cognitive_state=deps.normalize_cognitive_state,
            load_recent_raw=deps.load_recent_raw,
            load_recent_facts=deps.load_recent_facts,
            format_memory_rows_for_llm=deps.format_memory_rows_for_llm,
            compact_memory_text=deps.compact_memory_text,
            ask_router_llm=deps.ask_router_llm,
            current_turn_id=deps.current_turn_id,
            clean_text=deps.clean_text,
            normalize_question_policy_mapping=deps.normalize_question_policy_mapping,
            router_route_timeout_sec=deps.router_route_timeout_sec,
            cognitive_timeout_sec=deps.cognitive_timeout_sec,
            router_llm_enabled=deps.router_llm_enabled,
            router_route_max_tokens=deps.router_route_max_tokens,
            log=deps.log,
        )

    def build_cognitive_state_runtime_deps(self) -> CognitiveStateRuntimeDeps:
        deps = self.deps
        return CognitiveStateRuntimeDeps(
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            cognitive_locks=deps.cognitive_locks,
            collect_memory_layers=deps.collect_memory_layers,
            layered_summary_text=deps.layered_summary_text,
            normalize_cognitive_state=deps.normalize_cognitive_state,
            read_layered_cognitive_state=deps.read_layered_cognitive_state,
            get_matching_speculative_policy=deps.get_matching_speculative_policy,
            fast_path_policy=deps.fast_path_policy,
            session_state_snapshot=deps.session_state_snapshot,
            build_fast_cognitive_state=deps.build_fast_cognitive_state,
            write_json_file=deps.write_json_file,
            cognitive_state_path=deps.cognitive_state_path,
            recent_memory_groups=deps.recent_memory_groups,
            memory_cognitive_raw_limit=deps.memory_cognitive_raw_limit,
            build_cognitive_state_messages=deps.build_cognitive_state_messages,
            ask_router_llm=deps.ask_router_llm,
            cognitive_max_tokens=deps.cognitive_max_tokens,
            cognitive_timeout_sec=deps.cognitive_timeout_sec,
            current_turn_id=deps.current_turn_id,
            is_context_size_error=deps.is_context_size_error,
            build_compact_cognitive_state_messages=deps.build_compact_cognitive_state_messages,
            should_log_voice_timing=deps.should_log_voice_timing,
            build_cognitive_fallback_state=deps.build_cognitive_fallback_state,
            finalize_cognitive_state=deps.finalize_cognitive_state,
            log=deps.log,
        )


__all__ = ["LlmCognitiveDependencyComposition", "LlmCognitiveDependencyCompositionDeps"]
