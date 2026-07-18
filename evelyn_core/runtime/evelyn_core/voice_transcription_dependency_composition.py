from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .text import apply_stt_post_corrections, clean_text
from .voice_barge_in import maybe_merge_barge_in_utterance
from .voice_stt_execution_runtime import VoiceSttExecutionDeps
from .voice_stt_flow import (
    build_final_transcript_flow,
    run_full_stt_with_optional_rescore,
    run_partial_stt_flow,
)
from .voice_transcript_finalize_runtime import VoiceTranscriptFinalizeDeps


@dataclass(frozen=True)
class VoiceTranscriptionDependencyCompositionDeps:
    build_partial_stt_window: Callable[..., Any]
    get_partial_transcript: Callable[..., Any]
    session_committed_stt_text: MutableMapping[str, str]
    run_blocking_stt_task: Callable[..., Any]
    speculate_from_committed_stt: Callable[..., Any]
    room_state_snapshot: Callable[..., Any]
    remember_speculative_policy: Callable[..., Any]
    transcribe_audio: Callable[..., str]
    choose_full_stt_candidate: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    mark_turn_stage: Callable[..., Any]
    save_voice_debug_audio: Callable[..., Any]
    full_stt_timeout_sec: float
    voice_stt_max_new_tokens: int
    rescore_enabled: bool
    rescore_extra_tokens: int
    rescore_min_audio_sec: float
    rescore_min_text_len: int
    rescore_timeout_sec: float
    session_partial_stt_text: MutableMapping[str, str]
    commit_stable_transcript: Callable[..., Any]
    build_transcript_result: Callable[..., Any]
    room_last_voice_utterance_for_merge: MutableMapping[str, Any]
    merge_window_sec: float
    tts_interrupted_window_sec: float
    incomplete_window_sec: float
    complete_question_window_sec: float
    adaptive_window_enabled: bool
    log: Callable[..., Any] = print


class VoiceTranscriptionDependencyComposition:
    """Builds full-STT and transcript-finalization contracts from live state."""

    def __init__(self, deps: VoiceTranscriptionDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_stt_execution_deps(self) -> VoiceSttExecutionDeps:
        deps = self.deps
        return VoiceSttExecutionDeps(
            run_partial_stt_flow=run_partial_stt_flow,
            run_full_stt_with_optional_rescore=run_full_stt_with_optional_rescore,
            build_partial_stt_window=deps.build_partial_stt_window,
            get_partial_transcript=deps.get_partial_transcript,
            read_committed_text=lambda key: deps.session_committed_stt_text.get(key or "", ""),
            run_blocking_stt_task=deps.run_blocking_stt_task,
            speculate_from_committed_stt=deps.speculate_from_committed_stt,
            room_state_snapshot=deps.room_state_snapshot,
            clean_text=clean_text,
            remember_speculative_policy=deps.remember_speculative_policy,
            transcribe_audio=deps.transcribe_audio,
            choose_full_stt_candidate=deps.choose_full_stt_candidate,
            log_voice_stage=deps.log_voice_stage,
            mark_turn_stage=deps.mark_turn_stage,
            save_voice_debug_audio=deps.save_voice_debug_audio,
            print_fn=deps.log,
            full_stt_timeout_sec=deps.full_stt_timeout_sec,
            voice_stt_max_new_tokens=deps.voice_stt_max_new_tokens,
            rescore_enabled=deps.rescore_enabled,
            rescore_extra_tokens=deps.rescore_extra_tokens,
            rescore_min_audio_sec=deps.rescore_min_audio_sec,
            rescore_min_text_len=deps.rescore_min_text_len,
            rescore_timeout_sec=deps.rescore_timeout_sec,
        )

    def build_voice_transcript_finalize_deps(self) -> VoiceTranscriptFinalizeDeps:
        deps = self.deps
        return VoiceTranscriptFinalizeDeps(
            build_final_transcript_flow=build_final_transcript_flow,
            room_state_snapshot=deps.room_state_snapshot,
            apply_stt_post_corrections=apply_stt_post_corrections,
            clean_text=clean_text,
            set_partial_text=lambda key, value: deps.session_partial_stt_text.__setitem__(
                key, value
            ),
            commit_stable_transcript=deps.commit_stable_transcript,
            build_transcript_result=deps.build_transcript_result,
            speculate_from_committed_stt=deps.speculate_from_committed_stt,
            remember_speculative_policy=deps.remember_speculative_policy,
            room_last_voice_utterance_for_merge=deps.room_last_voice_utterance_for_merge,
            maybe_merge_barge_in_utterance=maybe_merge_barge_in_utterance,
            log_voice_stage=deps.log_voice_stage,
            print_fn=deps.log,
            merge_window_sec=deps.merge_window_sec,
            tts_interrupted_window_sec=deps.tts_interrupted_window_sec,
            incomplete_window_sec=deps.incomplete_window_sec,
            complete_question_window_sec=deps.complete_question_window_sec,
            adaptive_window_enabled=deps.adaptive_window_enabled,
        )


__all__ = [
    "VoiceTranscriptionDependencyComposition",
    "VoiceTranscriptionDependencyCompositionDeps",
]
