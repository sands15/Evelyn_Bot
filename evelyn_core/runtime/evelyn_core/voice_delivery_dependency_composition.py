from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, MutableMapping

from .discord_text_reply_runtime import DiscordTextReplyRuntimeDeps
from .tts_playback import split_tts_sentences
from .voice_delivery_runtime import VoiceDeliveryRuntimeDeps
from .voice_pipeline import build_answer_payload_from_text, build_delivery_plan
from .voice_turn_entry_runtime import VoiceTurnEntryRuntimeDeps


@dataclass(frozen=True)
class VoiceDeliveryDependencyCompositionDeps:
    memory_index_dir: Path
    attach_current_task: Callable[..., Any]
    detach_task: Callable[..., Any]
    prepare_route_context: Callable[..., Any]
    maybe_handle_short_circuit_route: Callable[..., Any]
    maybe_execute_registered_route: Callable[..., Any]
    run_main_llm_turn: Callable[..., Any]
    emit_delivery_plan_chunks: Callable[..., Any]
    record_voice_pipeline_failure: Callable[..., Any]
    current_turn_id: Callable[..., Any]
    session_topic_ids: MutableMapping[str, str]
    new_turn_metrics: Callable[..., Any]
    is_local_speaker_voice_client: Callable[..., bool]
    start_streaming_voice_delivery: Callable[..., Any]
    start_streaming_local_voice_delivery: Callable[..., Any]
    ask_llm_streaming: Callable[..., Any]
    speak_answer_local: Callable[..., Any]
    local_tts_snapshot: Callable[[], dict[str, Any]]
    mark_barge_in_continuity_probe: Callable[..., Any]
    log_voice_latency: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    false_trigger_reason_code: str
    false_trigger_reason_label: str
    session_state_snapshot: Callable[..., Any]
    maybe_append_proactive_question: Callable[..., Any]
    update_session_state: Callable[..., Any]
    format_display_text: Callable[..., str]
    fallback_answer_for: Callable[..., str]
    send_discord_text: Callable[..., Any]


class VoiceDeliveryDependencyComposition:
    """Builds route-entry, voice-delivery, and Discord text-reply contracts."""

    def __init__(self, deps: VoiceDeliveryDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_turn_entry_runtime_deps(self) -> VoiceTurnEntryRuntimeDeps:
        deps = self.deps
        return VoiceTurnEntryRuntimeDeps(
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            prepare_route_context=deps.prepare_route_context,
            maybe_handle_short_circuit_route=deps.maybe_handle_short_circuit_route,
            maybe_execute_registered_route=deps.maybe_execute_registered_route,
            run_main_llm_turn=deps.run_main_llm_turn,
            emit_delivery_plan_chunks=deps.emit_delivery_plan_chunks,
            build_answer_payload_from_text=build_answer_payload_from_text,
            build_delivery_plan=build_delivery_plan,
            split_tts_sentences=split_tts_sentences,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
        )

    def build_voice_delivery_runtime_deps(self) -> VoiceDeliveryRuntimeDeps:
        deps = self.deps
        return VoiceDeliveryRuntimeDeps(
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            current_turn_id=deps.current_turn_id,
            session_topic_id=lambda session_key: deps.session_topic_ids.get(session_key),
            new_turn_metrics=deps.new_turn_metrics,
            is_local_speaker_voice_client=deps.is_local_speaker_voice_client,
            start_streaming_voice_delivery=deps.start_streaming_voice_delivery,
            start_streaming_local_voice_delivery=deps.start_streaming_local_voice_delivery,
            ask_llm_streaming=deps.ask_llm_streaming,
            speak_answer_local=deps.speak_answer_local,
            local_playback_count=lambda: int(deps.local_tts_snapshot().get("playCount") or 0),
            mark_barge_in_continuity_probe=deps.mark_barge_in_continuity_probe,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
            log_voice_latency=deps.log_voice_latency,
            log_voice_stage=deps.log_voice_stage,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            false_trigger_reason_code=deps.false_trigger_reason_code,
            false_trigger_reason_label=deps.false_trigger_reason_label,
        )

    def build_discord_text_reply_runtime_deps(self) -> DiscordTextReplyRuntimeDeps:
        deps = self.deps
        return DiscordTextReplyRuntimeDeps(
            memory_index_dir=deps.memory_index_dir,
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            new_turn_metrics=deps.new_turn_metrics,
            session_topic_id=lambda session_key: deps.session_topic_ids.get(session_key),
            ask_llm_streaming=deps.ask_llm_streaming,
            log_llm_first_chunk=lambda metrics: deps.log_voice_latency(
                metrics,
                "llm_first_chunk_logged",
                "LLM 첫 chunk 시간",
            ),
            session_state_snapshot=deps.session_state_snapshot,
            maybe_append_proactive_question=deps.maybe_append_proactive_question,
            update_session_state=deps.update_session_state,
            build_answer_payload_from_text=build_answer_payload_from_text,
            format_display_text=deps.format_display_text,
            fallback_answer_for=deps.fallback_answer_for,
            build_delivery_plan=build_delivery_plan,
            split_tts_sentences=split_tts_sentences,
            send_discord_text=deps.send_discord_text,
        )


__all__ = [
    "VoiceDeliveryDependencyComposition",
    "VoiceDeliveryDependencyCompositionDeps",
]
