from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .text import extract_leading_wake_alias, strip_omnivoice_tags, visible_text
from .turn_lifecycle import TurnScope
from .tts_interrupt_runtime import run_voice_tts_interrupt_gate_from_runtime
from .voice_audio_ingress_runtime import prepare_voice_audio_ingress_from_runtime
from .voice_member_audio_pipeline_runtime import VoiceMemberAudioPipelineDeps
from .voice_orchestration import (
    VoiceTranscriptReplyDeps,
    process_voice_reply_from_transcript_context,
)
from .voice_pipeline import build_voice_reply_request
from .voice_reply_dispatch_runtime import VoiceReplyDispatchDeps, dispatch_voice_reply_from_runtime
from .voice_session_gate_runtime import VoiceSessionGateDeps, run_voice_session_gate_from_runtime
from .voice_stt_execution_runtime import run_voice_stt_execution_from_runtime
from .voice_stt_flow import decide_final_wake_veto
from .voice_transcript_finalize_runtime import finalize_voice_transcript_from_runtime
from .voice_wake_probe_runtime import run_voice_wake_probe_from_runtime


@dataclass(frozen=True)
class VoiceMemberPipelineDependencyCompositionDeps:
    is_short_followup_candidate: Callable[..., bool]
    should_ignore_short_transcription: Callable[..., bool]
    register_drop_reason: Callable[..., Any]
    save_voice_debug_audio: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    room_state_snapshot: Callable[..., Any]
    session_topic_ids: MutableMapping[str, str]
    monotonic: Callable[[], float]
    active_conversation_awaiting_reply_sec: float
    active_conversation_voice_sec: float
    canned_wake_reply: str
    should_reply_to_voice: Callable[..., Any]
    reset_session_bad_audio: Callable[..., Any]
    build_topic_id: Callable[..., str]
    session_last_stt_text: MutableMapping[str, str]
    room_last_voice_reply_at: MutableMapping[str, Any]
    room_last_voice_utterance_for_merge: MutableMapping[str, Any]
    update_room_speaker_activity: Callable[..., Any]
    pick_active_speaker: Callable[..., Any]
    start_new_turn: Callable[..., Any]
    update_session_state: Callable[..., Any]
    checkpoint_accepted_voice_turn: Callable[..., Any]
    set_room_owner: Callable[..., Any]
    session_partial_stt_text: MutableMapping[str, str]
    session_committed_stt_text: MutableMapping[str, str]
    partial_stt_cache: MutableMapping[str, Any]
    replace_room_turn_scope: Callable[..., Any]
    attach_current_task: Callable[..., Any]
    set_room_reply_in_progress: Callable[..., Any]
    session_locks: MutableMapping[str, Any]
    speak_answer: Callable[..., Any]
    ask_llm_and_speak_streaming: Callable[..., Any]
    record_voice_pipeline_failure: Callable[..., Any]
    record_runtime_error: Callable[..., Any]
    finalize_voice_reply_side_effects: Callable[..., Any]
    get_room_turn_scope: Callable[..., Any]
    detach_task: Callable[..., Any]
    clear_room_turn_scope: Callable[..., Any]
    build_audio_ingress_deps: Callable[[], Any]
    build_wake_probe_deps: Callable[[], Any]
    build_tts_interrupt_gate_deps: Callable[[], Any]
    build_stt_execution_deps: Callable[[], Any]
    build_transcript_finalize_deps: Callable[[], Any]
    log: Callable[..., Any] = print


class VoiceMemberPipelineDependencyComposition:
    """Owns session, reply, and member-audio pipeline dependency assembly."""

    def __init__(self, deps: VoiceMemberPipelineDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_session_gate_deps(self) -> VoiceSessionGateDeps:
        deps = self.deps
        return VoiceSessionGateDeps(
            is_short_followup_candidate=deps.is_short_followup_candidate,
            should_ignore_short_transcription=deps.should_ignore_short_transcription,
            decide_final_wake_veto=decide_final_wake_veto,
            extract_leading_wake_alias=extract_leading_wake_alias,
            register_drop_reason=deps.register_drop_reason,
            save_voice_debug_audio=deps.save_voice_debug_audio,
            log_voice_stage=deps.log_voice_stage,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            print_fn=deps.log,
        )

    def build_voice_reply_dispatch_deps(self) -> VoiceReplyDispatchDeps:
        deps = self.deps
        return VoiceReplyDispatchDeps(
            room_state_snapshot=deps.room_state_snapshot,
            session_topic_ids=deps.session_topic_ids,
            monotonic=deps.monotonic,
            process_voice_reply=process_voice_reply_from_transcript_context,
            active_conversation_awaiting_reply_sec=deps.active_conversation_awaiting_reply_sec,
            active_conversation_voice_sec=deps.active_conversation_voice_sec,
            canned_wake_reply=deps.canned_wake_reply,
        )

    def build_voice_transcript_reply_deps(self, guild: Any) -> VoiceTranscriptReplyDeps:
        deps = self.deps
        return VoiceTranscriptReplyDeps(
            should_reply_to_voice=deps.should_reply_to_voice,
            register_drop_reason=deps.register_drop_reason,
            log_voice_stage=deps.log_voice_stage,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            reset_session_bad_audio=deps.reset_session_bad_audio,
            build_voice_reply_request=build_voice_reply_request,
            build_topic_id=deps.build_topic_id,
            session_last_stt_text=deps.session_last_stt_text,
            room_last_voice_reply_at=deps.room_last_voice_reply_at,
            room_last_voice_utterance_for_merge=deps.room_last_voice_utterance_for_merge,
            update_room_speaker_activity=deps.update_room_speaker_activity,
            pick_active_speaker=deps.pick_active_speaker,
            start_new_turn=deps.start_new_turn,
            update_session_state=deps.update_session_state,
            checkpoint_accepted_voice_turn=(
                deps.checkpoint_accepted_voice_turn
            ),
            set_room_owner=deps.set_room_owner,
            session_partial_stt_text=deps.session_partial_stt_text,
            session_committed_stt_text=deps.session_committed_stt_text,
            partial_stt_cache=deps.partial_stt_cache,
            make_turn_scope=TurnScope,
            replace_room_turn_scope=deps.replace_room_turn_scope,
            attach_current_task=deps.attach_current_task,
            set_room_reply_in_progress=deps.set_room_reply_in_progress,
            session_locks=deps.session_locks,
            visible_text=visible_text,
            print_fn=deps.log,
            get_voice_client=lambda: guild.voice_client,
            speak_answer=deps.speak_answer,
            ask_llm_and_speak_streaming=deps.ask_llm_and_speak_streaming,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
            record_runtime_error=deps.record_runtime_error,
            finalize_voice_reply_side_effects=deps.finalize_voice_reply_side_effects,
            strip_omnivoice_tags=strip_omnivoice_tags,
            get_room_turn_scope=deps.get_room_turn_scope,
            detach_task=deps.detach_task,
            clear_room_turn_scope=deps.clear_room_turn_scope,
        )

    def build_voice_member_audio_pipeline_deps(self) -> VoiceMemberAudioPipelineDeps:
        deps = self.deps
        return VoiceMemberAudioPipelineDeps(
            prepare_audio_ingress=prepare_voice_audio_ingress_from_runtime,
            build_audio_ingress_deps=deps.build_audio_ingress_deps,
            run_wake_probe=run_voice_wake_probe_from_runtime,
            build_wake_probe_deps=deps.build_wake_probe_deps,
            run_tts_interrupt_gate=run_voice_tts_interrupt_gate_from_runtime,
            build_tts_interrupt_gate_deps=deps.build_tts_interrupt_gate_deps,
            run_stt_execution=run_voice_stt_execution_from_runtime,
            build_stt_execution_deps=deps.build_stt_execution_deps,
            finalize_transcript=finalize_voice_transcript_from_runtime,
            build_transcript_finalize_deps=deps.build_transcript_finalize_deps,
            run_session_gate=run_voice_session_gate_from_runtime,
            build_session_gate_deps=self.build_voice_session_gate_deps,
            dispatch_voice_reply=dispatch_voice_reply_from_runtime,
            build_transcript_reply_deps=self.build_voice_transcript_reply_deps,
            build_reply_dispatch_deps=self.build_voice_reply_dispatch_deps,
        )

__all__ = [
    "VoiceMemberPipelineDependencyComposition",
    "VoiceMemberPipelineDependencyCompositionDeps",
]
