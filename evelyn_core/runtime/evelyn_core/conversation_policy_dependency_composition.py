from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, MutableMapping

from .discord_session_policy_runtime import DiscordSessionPolicyRuntimeDeps
from .question_policy_runtime import QuestionPolicyRuntimeDeps, QuestionPolicyStateRuntimeDeps
from .response_output_policy import ResponseOutputPolicyRuntimeDeps
from .session_turn_runtime import SessionTurnRuntimeDeps


@dataclass(frozen=True)
class ConversationPolicyDependencyCompositionDeps:
    normalize_question_policy_mapping_payload: Callable[..., Any]
    extract_question_policy_from_route_meta_payload: Callable[..., Any]
    user_wants_direct_answer_payload: Callable[..., bool]
    user_frustration_with_questions_payload: Callable[..., bool]
    is_continuable_technical_topic_payload: Callable[..., bool]
    question_cooldown_hit_payload: Callable[..., bool]
    apply_fast_path_question_policy_payload: Callable[..., Any]
    record_question_trace_payload: Callable[..., Any]
    summarize_question_metrics_payload: Callable[..., Any]
    proactive_scope_candidates_payload: Callable[..., Any]
    record_session_question_asked_payload: Callable[..., Any]
    resolve_pending_proactive_question_for_turn_payload: Callable[..., Any]
    select_and_mark_proactive_question_payload: Callable[..., Any]
    maybe_append_proactive_question_payload: Callable[..., Any]
    session_state_store: Any
    system_prompt: str
    memory_index_dir: Path
    active_conversation_awaiting_reply_sec: float
    active_conversation_text_question_sec: float
    active_conversation_text_sec: float
    max_history_items: int
    session_topic_ids: MutableMapping[str, str]
    build_topic_id: Callable[..., str]
    new_turn_id: Callable[[], str]
    session_last_turn_accepted_at_get: Callable[[str], float]
    monotonic: Callable[[], float]
    should_require_confirm_exact_for_wake_payload: Callable[..., bool]
    is_transport_corrupted_audio_payload: Callable[..., bool]
    no_wake_max_continue_sec: float
    clean_text: Callable[[str], str]
    looks_like_brief_filler_text: Callable[..., bool]
    looks_like_repetitive_noise_text: Callable[..., bool]
    tail_fragment_window_sec: float
    tail_fragment_max_raw_sec: float
    tail_fragment_max_voiced_ms: float
    tail_fragment_max_longest_ms: float
    normalize_voice_text: Callable[[str], str]
    normalized_wake_words: Callable[[], set[str]]
    min_audio_sec: float
    min_transcribed_len: int
    wake_short_text_keep_len: int
    audio_duration: Callable[[bytes], float]
    session_state_snapshot: Callable[..., dict[str, Any]]
    answer_gpu_status: Callable[..., str | None]
    model_output_stop_tokens: tuple[str, ...] | list[str]
    sanitize_model_output_cleanup: Callable[[str], str]


class ConversationPolicyDependencyComposition:
    """Builds question, session-turn, Discord-session, and output policy contracts."""

    def __init__(self, deps: ConversationPolicyDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_question_policy_runtime_deps(self) -> QuestionPolicyRuntimeDeps:
        deps = self.deps
        return QuestionPolicyRuntimeDeps(
            normalize_question_policy_mapping_payload=(
                deps.normalize_question_policy_mapping_payload
            ),
            extract_question_policy_from_route_meta_payload=(
                deps.extract_question_policy_from_route_meta_payload
            ),
            user_wants_direct_answer_payload=deps.user_wants_direct_answer_payload,
            user_frustration_with_questions_payload=(
                deps.user_frustration_with_questions_payload
            ),
            is_continuable_technical_topic_payload=(
                deps.is_continuable_technical_topic_payload
            ),
        )

    def build_question_policy_state_runtime_deps(self) -> QuestionPolicyStateRuntimeDeps:
        deps = self.deps
        return QuestionPolicyStateRuntimeDeps(
            question_cooldown_hit_payload=deps.question_cooldown_hit_payload,
            apply_fast_path_question_policy_payload=(
                deps.apply_fast_path_question_policy_payload
            ),
            record_question_trace_payload=deps.record_question_trace_payload,
            summarize_question_metrics_payload=deps.summarize_question_metrics_payload,
            proactive_scope_candidates_payload=deps.proactive_scope_candidates_payload,
            record_session_question_asked_payload=(
                deps.record_session_question_asked_payload
            ),
            resolve_pending_proactive_question_for_turn_payload=(
                deps.resolve_pending_proactive_question_for_turn_payload
            ),
            select_and_mark_proactive_question_payload=(
                deps.select_and_mark_proactive_question_payload
            ),
            maybe_append_proactive_question_payload=(
                deps.maybe_append_proactive_question_payload
            ),
        )

    def build_session_turn_runtime_deps(self) -> SessionTurnRuntimeDeps:
        deps = self.deps
        return SessionTurnRuntimeDeps(
            session_state_store=deps.session_state_store,
            system_prompt=deps.system_prompt,
            memory_index_dir=deps.memory_index_dir,
            active_conversation_awaiting_reply_sec=deps.active_conversation_awaiting_reply_sec,
            active_conversation_text_question_sec=(
                deps.active_conversation_text_question_sec
            ),
            active_conversation_text_sec=deps.active_conversation_text_sec,
            max_history_items=deps.max_history_items,
            session_topic_ids=deps.session_topic_ids,
            build_topic_id_fn=deps.build_topic_id,
            new_turn_id_fn=deps.new_turn_id,
        )

    def build_discord_session_policy_runtime_deps(self) -> DiscordSessionPolicyRuntimeDeps:
        deps = self.deps
        return DiscordSessionPolicyRuntimeDeps(
            session_last_turn_accepted_at_get=deps.session_last_turn_accepted_at_get,
            monotonic_fn=deps.monotonic,
            should_require_confirm_exact_for_wake_payload=(
                deps.should_require_confirm_exact_for_wake_payload
            ),
            is_transport_corrupted_audio_payload=deps.is_transport_corrupted_audio_payload,
            no_wake_max_continue_sec=deps.no_wake_max_continue_sec,
            clean_text=deps.clean_text,
            looks_like_brief_filler_text=deps.looks_like_brief_filler_text,
            looks_like_repetitive_noise_text=deps.looks_like_repetitive_noise_text,
            tail_fragment_window_sec=deps.tail_fragment_window_sec,
            tail_fragment_max_raw_sec=deps.tail_fragment_max_raw_sec,
            tail_fragment_max_voiced_ms=deps.tail_fragment_max_voiced_ms,
            tail_fragment_max_longest_ms=deps.tail_fragment_max_longest_ms,
            normalize_voice_text=deps.normalize_voice_text,
            normalized_wake_words=deps.normalized_wake_words,
            min_audio_sec=deps.min_audio_sec,
            min_transcribed_len=deps.min_transcribed_len,
            wake_short_text_keep_len=deps.wake_short_text_keep_len,
            audio_duration_fn=deps.audio_duration,
        )

    def build_response_output_policy_runtime_deps(self) -> ResponseOutputPolicyRuntimeDeps:
        deps = self.deps
        return ResponseOutputPolicyRuntimeDeps(
            session_state_snapshot_fn=deps.session_state_snapshot,
            answer_gpu_status_answer_fn=deps.answer_gpu_status,
            model_output_stop_tokens=tuple(deps.model_output_stop_tokens),
            sanitize_model_output_cleanup_fn=deps.sanitize_model_output_cleanup,
        )


__all__ = [
    "ConversationPolicyDependencyComposition",
    "ConversationPolicyDependencyCompositionDeps",
]
