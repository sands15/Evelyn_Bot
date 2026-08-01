from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .voice_barge_in_continuity import VoiceBargeInContinuityRuntimeDeps
from .voice_ingress_runtime import VoiceIngressEntrypointDeps, VoiceIngressRuntimeDeps
from .voice_reply_gate_runtime import VoiceReplyGateRuntimeDeps
from .voice_reply_side_effects import VoiceReplySideEffectDeps


@dataclass(frozen=True)
class VoiceTurnDependencyCompositionDeps:
    barge_in_tracker: Any
    command_status: Callable[..., str]
    session_speculative_policies: MutableMapping[str, Any]
    append_history: Callable[..., Any]
    compute_runtime_mode: Callable[..., str]
    record_context_pipeline_benchmark: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    read_cached_cognitive_state: Callable[..., Any]
    apply_ask_gating: Callable[..., Any]
    schedule_search_followup: Callable[..., Any]
    session_state_snapshot: Callable[..., Any]
    mark_session_active: Callable[..., Any]
    set_room_owner: Callable[..., Any]
    commit_session_continuity: Callable[..., Any]
    active_conversation_voice_question_sec: float
    active_conversation_voice_sec: float
    active_conversation_awaiting_reply_sec: float
    room_state_snapshot: Callable[..., Any]
    is_room_owner_active: Callable[..., bool]
    is_session_active_for_user: Callable[..., bool]
    tts_input_suppression_reason: Callable[..., str | None]
    room_last_voice_reply_at: MutableMapping[str, float]
    post_tts_ignore_sec: float
    reply_cooldown_sec: float
    normalize_voice_text: Callable[[str], str]
    contains_wake_word: Callable[..., bool]
    looks_like_brief_filler_text: Callable[..., bool]
    looks_like_repetitive_noise_text: Callable[..., bool]
    is_similar: Callable[..., bool]
    min_text_len: int
    voice_ingress_queue: Any
    voice_utterance_buffers: MutableMapping[str, Any]
    voice_utterance_flush_tasks: MutableMapping[str, Any]
    voice_utterance_assembly_config: Any
    voice_ingress_max_age_sec: float
    voice_ingress_drop_oldest_on_full: bool
    voice_ingress_queue_max: int
    evaluate_voice_ingress_dequeue: Callable[..., Any]
    apply_voice_ingress_dequeue_debug_meta: Callable[..., Any]
    enqueue_voice_ingress_item: Callable[..., Any]
    increment_voice_pipeline_counter: Callable[..., Any]
    process_member_audio: Callable[..., Any]
    create_task: Callable[..., Any]
    ensure_startup_components_ready: Callable[..., Any]
    normalize_voice_debug_meta: Callable[..., Any]
    voice_ingress_source: Callable[..., str]
    should_drop_discord_audio_for_local_mic: Callable[..., bool]
    ensure_voice_worker_started: Callable[..., Any]
    build_voice_ingress_context: Callable[..., Any]
    next_segment_id: Callable[..., int]
    new_turn_id: Callable[..., str]
    validation_context_provider: Callable[..., dict[str, Any] | None]
    build_voice_ingress_item: Callable[..., Any]
    voice_ingress_queue_depth: Callable[[], int]
    schedule_voice_utterance_item: Callable[..., Any]
    monotonic: Callable[[], float]
    log: Callable[..., Any]


class VoiceTurnDependencyComposition:
    """Builds barge-in, reply, and ingress dependency contracts."""

    def __init__(self, deps: VoiceTurnDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_barge_in_continuity_runtime_deps(
        self,
    ) -> VoiceBargeInContinuityRuntimeDeps:
        return VoiceBargeInContinuityRuntimeDeps(
            tracker=self.deps.barge_in_tracker,
            command_status=self.deps.command_status,
        )

    def build_voice_reply_side_effect_deps(self) -> VoiceReplySideEffectDeps:
        deps = self.deps
        return VoiceReplySideEffectDeps(
            session_speculative_policies=deps.session_speculative_policies,
            append_history=deps.append_history,
            compute_runtime_mode=deps.compute_runtime_mode,
            record_context_pipeline_benchmark=deps.record_context_pipeline_benchmark,
            schedule_memory_update=deps.schedule_memory_update,
            read_cached_cognitive_state=deps.read_cached_cognitive_state,
            apply_ask_gating=deps.apply_ask_gating,
            schedule_search_followup=deps.schedule_search_followup,
            session_state_snapshot=deps.session_state_snapshot,
            mark_session_active=deps.mark_session_active,
            set_room_owner=deps.set_room_owner,
            commit_session_continuity=(
                deps.commit_session_continuity
            ),
            log=deps.log,
            active_conversation_voice_question_sec=deps.active_conversation_voice_question_sec,
            active_conversation_voice_sec=deps.active_conversation_voice_sec,
            active_conversation_awaiting_reply_sec=(
                deps.active_conversation_awaiting_reply_sec
            ),
        )

    def build_voice_reply_gate_runtime_deps(self) -> VoiceReplyGateRuntimeDeps:
        deps = self.deps
        return VoiceReplyGateRuntimeDeps(
            session_state_snapshot=deps.session_state_snapshot,
            room_state_snapshot=deps.room_state_snapshot,
            is_room_owner_active=deps.is_room_owner_active,
            is_session_active_for_user=deps.is_session_active_for_user,
            tts_input_suppression_reason=deps.tts_input_suppression_reason,
            room_last_voice_reply_at=deps.room_last_voice_reply_at,
            post_tts_ignore_sec=deps.post_tts_ignore_sec,
            reply_cooldown_sec=deps.reply_cooldown_sec,
            normalize_voice_text=deps.normalize_voice_text,
            contains_wake_word=deps.contains_wake_word,
            looks_like_brief_filler_text=deps.looks_like_brief_filler_text,
            looks_like_repetitive_noise_text=deps.looks_like_repetitive_noise_text,
            is_similar=deps.is_similar,
            min_text_len=deps.min_text_len,
            monotonic=deps.monotonic,
        )

    def build_voice_ingress_runtime_deps(self) -> VoiceIngressRuntimeDeps:
        deps = self.deps
        return VoiceIngressRuntimeDeps(
            voice_ingress_queue=deps.voice_ingress_queue,
            voice_utterance_buffers=deps.voice_utterance_buffers,
            voice_utterance_flush_tasks=deps.voice_utterance_flush_tasks,
            voice_utterance_assembly_config=deps.voice_utterance_assembly_config,
            voice_ingress_max_age_sec=deps.voice_ingress_max_age_sec,
            voice_ingress_drop_oldest_on_full=deps.voice_ingress_drop_oldest_on_full,
            voice_ingress_queue_max=deps.voice_ingress_queue_max,
            evaluate_voice_ingress_dequeue=deps.evaluate_voice_ingress_dequeue,
            apply_voice_ingress_dequeue_debug_meta=(
                deps.apply_voice_ingress_dequeue_debug_meta
            ),
            enqueue_voice_ingress_item=deps.enqueue_voice_ingress_item,
            increment_voice_pipeline_counter=deps.increment_voice_pipeline_counter,
            process_member_audio=deps.process_member_audio,
            create_task=deps.create_task,
            log=deps.log,
            monotonic=deps.monotonic,
        )

    def build_voice_ingress_entrypoint_deps(self) -> VoiceIngressEntrypointDeps:
        deps = self.deps
        return VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=deps.ensure_startup_components_ready,
            normalize_voice_debug_meta=deps.normalize_voice_debug_meta,
            voice_ingress_source=deps.voice_ingress_source,
            should_drop_discord_audio_for_local_mic=(
                deps.should_drop_discord_audio_for_local_mic
            ),
            ensure_voice_worker_started=deps.ensure_voice_worker_started,
            build_voice_ingress_context=deps.build_voice_ingress_context,
            next_segment_id=deps.next_segment_id,
            new_turn_id=deps.new_turn_id,
            room_state_snapshot=deps.room_state_snapshot,
            validation_context_provider=deps.validation_context_provider,
            build_voice_ingress_item=deps.build_voice_ingress_item,
            voice_ingress_queue_depth=deps.voice_ingress_queue_depth,
            schedule_voice_utterance_item=deps.schedule_voice_utterance_item,
            monotonic=deps.monotonic,
        )


__all__ = ["VoiceTurnDependencyComposition", "VoiceTurnDependencyCompositionDeps"]
