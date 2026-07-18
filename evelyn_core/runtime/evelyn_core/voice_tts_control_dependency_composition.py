from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .cached_tts_runtime import CachedTtsRuntimeDeps
from .tts_interrupt_runtime import TtsInterruptRuntimeDeps, VoiceTtsInterruptGateDeps


@dataclass(frozen=True)
class VoiceTtsControlDependencyCompositionDeps:
    tts_playback_manager: Any
    local_tts_playback_manager: Any
    log_turn_event: Callable[..., Any]
    speaker_verification_applies: Callable[..., bool]
    speaker_verification_result_factory: Callable[..., Any]
    speaker_verifier: Any
    speaker_verification_apply_to: str
    speaker_verification_threshold: float
    to_thread: Callable[..., Any]
    resolve_cached_tts_audio_path: Callable[..., Any]
    cached_audio_enabled: bool
    canned_wake_reply_text: str
    canned_wake_reply_audio: str
    project_root: Path
    cached_wave_audio_source_factory: Callable[..., Any]
    tts_source_playback_request_factory: Callable[..., Any]
    clean_text: Callable[[str], str]
    log_voice_latency: Callable[..., Any]
    should_interrupt_tts: Callable[..., bool]
    verify_speaker_for_tts_interrupt: Callable[..., Any]
    speaker_verification_allows_tts_interrupt: Callable[..., bool]
    stop_active_tts_playback: Callable[..., Any]
    register_drop_reason: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    start_voice_barge_in_continuity_probe: Callable[..., Any]
    sleep: Callable[..., Any]
    monotonic: Callable[[], float]
    local_only_mode: bool
    post_tts_ignore_sec: float
    tts_interrupt_debounce_sec: float
    voice_waveform_body_rms_min: float


class VoiceTtsControlDependencyComposition:
    """Builds TTS interruption and cached-playback dependency contracts."""

    def __init__(self, deps: VoiceTtsControlDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_tts_interrupt_runtime_deps(self) -> TtsInterruptRuntimeDeps:
        deps = self.deps
        return TtsInterruptRuntimeDeps(
            tts_playback_manager=deps.tts_playback_manager,
            log_turn_event=deps.log_turn_event,
            speaker_verification_applies=deps.speaker_verification_applies,
            speaker_verification_result_factory=deps.speaker_verification_result_factory,
            speaker_verifier=deps.speaker_verifier,
            speaker_verification_apply_to=deps.speaker_verification_apply_to,
            speaker_verification_threshold=deps.speaker_verification_threshold,
            to_thread=deps.to_thread,
        )

    def build_cached_tts_runtime_deps(self) -> CachedTtsRuntimeDeps:
        deps = self.deps
        return CachedTtsRuntimeDeps(
            resolve_cached_tts_audio_path=deps.resolve_cached_tts_audio_path,
            cached_audio_enabled=deps.cached_audio_enabled,
            canned_wake_reply_text=deps.canned_wake_reply_text,
            canned_wake_reply_audio=deps.canned_wake_reply_audio,
            project_root=deps.project_root,
            cached_wave_audio_source_factory=deps.cached_wave_audio_source_factory,
            tts_source_playback_request_factory=deps.tts_source_playback_request_factory,
            tts_playback_manager=deps.tts_playback_manager,
            clean_text=deps.clean_text,
            log_turn_event=deps.log_turn_event,
            log_voice_latency=deps.log_voice_latency,
        )

    def build_voice_tts_interrupt_gate_deps(self) -> VoiceTtsInterruptGateDeps:
        deps = self.deps
        return VoiceTtsInterruptGateDeps(
            should_interrupt_tts=deps.should_interrupt_tts,
            local_tts_playback_manager=deps.local_tts_playback_manager,
            tts_playback_manager=deps.tts_playback_manager,
            verify_speaker_for_tts_interrupt=deps.verify_speaker_for_tts_interrupt,
            speaker_verification_allows_tts_interrupt=(
                deps.speaker_verification_allows_tts_interrupt
            ),
            stop_active_tts_playback=deps.stop_active_tts_playback,
            register_drop_reason=deps.register_drop_reason,
            log_voice_stage=deps.log_voice_stage,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            start_voice_barge_in_continuity_probe=(
                deps.start_voice_barge_in_continuity_probe
            ),
            log_turn_event=deps.log_turn_event,
            sleep=deps.sleep,
            monotonic=deps.monotonic,
            local_only_mode=deps.local_only_mode,
            post_tts_ignore_sec=deps.post_tts_ignore_sec,
            tts_interrupt_debounce_sec=deps.tts_interrupt_debounce_sec,
            voice_waveform_body_rms_min=deps.voice_waveform_body_rms_min,
        )


__all__ = [
    "VoiceTtsControlDependencyComposition",
    "VoiceTtsControlDependencyCompositionDeps",
]
