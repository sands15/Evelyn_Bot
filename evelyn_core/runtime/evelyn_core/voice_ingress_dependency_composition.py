from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .audio import (
    apply_light_denoise,
    compute_voice_band_metrics,
    compute_waveform_activity_stats,
    downmix_int16_stereo_to_mono_float,
    is_likely_environment_noise,
    is_probably_silent,
    prepare_stt_audio,
)
from .text import (
    apply_stt_post_corrections,
    clean_text,
    fuzzy_leading_wake_alias,
    looks_like_brief_filler_text,
    looks_like_repetitive_noise_text,
)
from .voice_audio_ingress_runtime import VoiceAudioIngressDeps
from .voice_stt_flow import (
    apply_fuzzy_wake_near_miss,
    apply_strict_wake_confirm_policy,
    interpret_wake_probe_result,
)
from .voice_wake_probe_runtime import VoiceWakeProbeDeps


@dataclass(frozen=True)
class VoiceIngressDependencyCompositionDeps:
    voice_pipeline_state: MutableMapping[str, Any]
    save_voice_debug_audio: Callable[..., Any]
    room_state_snapshot: Callable[..., Any]
    session_topic_ids: MutableMapping[str, str]
    build_topic_id: Callable[..., str]
    new_turn_metrics: Callable[..., dict[str, Any]]
    log_voice_stage: Callable[..., Any]
    register_drop_reason: Callable[..., Any]
    log_voice_bottleneck_summary: Callable[..., Any]
    is_transport_corrupted_audio: Callable[..., bool]
    build_voice_segment: Callable[..., Any]
    estimate_voice_like_probability: Callable[..., float]
    update_room_speaker_activity: Callable[..., Any]
    increment_session_bad_audio: Callable[..., Any]
    is_tail_fragment_candidate: Callable[..., bool]
    stt_use_raw_48k: bool
    rate: int
    channels: int
    target_rate: int
    voice_min_total_sec: float
    tail_fragment_max_raw_sec: float
    vad_enabled: bool
    voice_waveform_min_voiced_ms: float
    voice_waveform_min_run_ms: float
    voice_waveform_body_rms_min: float
    voice_waveform_body_peak_min: float
    is_room_owner_active: Callable[..., bool]
    is_session_active_for_user: Callable[..., bool]
    pick_active_speaker: Callable[..., Any]
    run_blocking_stt_task: Callable[..., Any]
    detect_wake_word_sync: Callable[..., Any]
    should_require_confirm_exact_for_wake: Callable[..., bool]
    increment_session_bad_audio_for_wake: Callable[..., Any]
    should_skip_full_stt_after_wake_probe: Callable[..., bool]
    wake_stt_timeout_sec: float
    voice_no_wake_max_continue_sec: float
    log: Callable[..., Any] = print


class VoiceIngressDependencyComposition:
    """Builds audio-ingress and wake-probe contracts from live voice state."""

    def __init__(self, deps: VoiceIngressDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_voice_audio_ingress_runtime_deps(self) -> VoiceAudioIngressDeps:
        deps = self.deps
        return VoiceAudioIngressDeps(
            voice_pipeline_state=deps.voice_pipeline_state,
            prepare_stt_audio=prepare_stt_audio,
            save_voice_debug_audio=deps.save_voice_debug_audio,
            room_state_snapshot=deps.room_state_snapshot,
            session_topic_ids=deps.session_topic_ids,
            build_topic_id=deps.build_topic_id,
            new_turn_metrics=deps.new_turn_metrics,
            log_voice_stage=deps.log_voice_stage,
            register_drop_reason=deps.register_drop_reason,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            downmix_int16_stereo_to_mono_float=downmix_int16_stereo_to_mono_float,
            apply_light_denoise=apply_light_denoise,
            is_transport_corrupted_audio=deps.is_transport_corrupted_audio,
            build_voice_segment=deps.build_voice_segment,
            compute_waveform_activity_stats=compute_waveform_activity_stats,
            estimate_voice_like_probability=deps.estimate_voice_like_probability,
            update_room_speaker_activity=deps.update_room_speaker_activity,
            increment_session_bad_audio=deps.increment_session_bad_audio,
            is_tail_fragment_candidate=deps.is_tail_fragment_candidate,
            is_probably_silent=is_probably_silent,
            print_fn=deps.log,
            stt_use_raw_48k=deps.stt_use_raw_48k,
            rate=deps.rate,
            channels=deps.channels,
            target_rate=deps.target_rate,
            voice_min_total_sec=deps.voice_min_total_sec,
            tail_fragment_max_raw_sec=deps.tail_fragment_max_raw_sec,
            vad_enabled=deps.vad_enabled,
            voice_waveform_min_voiced_ms=deps.voice_waveform_min_voiced_ms,
            voice_waveform_min_run_ms=deps.voice_waveform_min_run_ms,
            voice_waveform_body_rms_min=deps.voice_waveform_body_rms_min,
            voice_waveform_body_peak_min=deps.voice_waveform_body_peak_min,
        )

    def build_voice_wake_probe_runtime_deps(self) -> VoiceWakeProbeDeps:
        deps = self.deps
        return VoiceWakeProbeDeps(
            is_room_owner_active=deps.is_room_owner_active,
            is_session_active_for_user=deps.is_session_active_for_user,
            pick_active_speaker=deps.pick_active_speaker,
            log_voice_stage=deps.log_voice_stage,
            run_blocking_stt_task=deps.run_blocking_stt_task,
            detect_wake_word_sync=deps.detect_wake_word_sync,
            interpret_wake_probe_result=interpret_wake_probe_result,
            clean_text=clean_text,
            apply_stt_post_corrections=apply_stt_post_corrections,
            should_require_confirm_exact_for_wake=deps.should_require_confirm_exact_for_wake,
            apply_strict_wake_confirm_policy=apply_strict_wake_confirm_policy,
            apply_fuzzy_wake_near_miss=apply_fuzzy_wake_near_miss,
            fuzzy_leading_wake_alias=fuzzy_leading_wake_alias,
            register_drop_reason=deps.register_drop_reason,
            log_voice_bottleneck_summary=deps.log_voice_bottleneck_summary,
            is_likely_environment_noise=is_likely_environment_noise,
            looks_like_brief_filler_text=looks_like_brief_filler_text,
            looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
            compute_voice_band_metrics=compute_voice_band_metrics,
            save_voice_debug_audio=deps.save_voice_debug_audio,
            increment_session_bad_audio=deps.increment_session_bad_audio_for_wake,
            should_skip_full_stt_after_wake_probe=deps.should_skip_full_stt_after_wake_probe,
            print_fn=deps.log,
            wake_stt_timeout_sec=deps.wake_stt_timeout_sec,
            voice_no_wake_max_continue_sec=deps.voice_no_wake_max_continue_sec,
        )


__all__ = [
    "VoiceIngressDependencyComposition",
    "VoiceIngressDependencyCompositionDeps",
]
