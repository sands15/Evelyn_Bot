from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .omnivoice_request_runtime import OmniVoiceRequestRuntimeDeps
from .omnivoice_source_runtime import OmniVoiceSourceRuntimeDeps
from .tts_warmup_runtime import TtsWarmupRuntimeDeps
from .voice_timing_runtime import (
    VoiceTimingRuntimeDeps,
    build_voice_timing_runtime_deps,
)


@dataclass(frozen=True)
class VoiceAudioSupportDependencyCompositionDeps:
    get_http_session: Callable[..., Any]
    client_timeout_factory: Callable[..., Any]
    mark_startup_component: Callable[..., Any]
    startup_component_done: Callable[..., bool]
    omnivoice_server_url: str
    omnivoice_model: str
    omnivoice_voice: str
    omnivoice_language: str
    getenv: Callable[..., str]
    monotonic: Callable[[], float]
    voice_timing_log_threshold_ms: float
    voice_bottleneck_logs: bool
    record_turn_stage: Callable[..., Any]
    record_turn_path_summary: Callable[..., Any]
    summarize_p95_metrics: Callable[..., Any]
    build_turn_summary_payload: Callable[..., Any]
    log_turn_event: Callable[..., Any]
    request_id_suffix: Callable[[], str]
    tts_synth_request_factory: Callable[..., Any]
    tts_synth_result_factory: Callable[..., Any]
    omnivoice_pcm_rate: int
    omnivoice_stream: bool
    omnivoice_num_step: int
    omnivoice_speed: float
    clean_tts_text: Callable[[str], str]
    merge_log_event_payload: Callable[..., dict[str, Any]]
    source_factory: Callable[..., Any]
    omnivoice_timeout_sec: float
    record_voice_pipeline_failure: Callable[..., Any]
    create_turn_scoped_task: Callable[..., Any]
    log: Callable[..., Any]


class VoiceAudioSupportDependencyComposition:
    """Builds TTS warmup, timing, and OmniVoice support contracts."""

    def __init__(self, deps: VoiceAudioSupportDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_tts_warmup_runtime_deps(self) -> TtsWarmupRuntimeDeps:
        deps = self.deps
        return TtsWarmupRuntimeDeps(
            get_http_session=deps.get_http_session,
            client_timeout=deps.client_timeout_factory,
            mark_startup_component=deps.mark_startup_component,
            startup_component_done=deps.startup_component_done,
            omnivoice_server_url=deps.omnivoice_server_url,
            omnivoice_model=deps.omnivoice_model,
            omnivoice_voice=deps.omnivoice_voice,
            omnivoice_language=deps.omnivoice_language,
            getenv=deps.getenv,
            log=deps.log,
        )

    def build_voice_timing_runtime_deps(self) -> VoiceTimingRuntimeDeps:
        deps = self.deps
        return build_voice_timing_runtime_deps(
            monotonic=deps.monotonic,
            voice_timing_log_threshold_ms=deps.voice_timing_log_threshold_ms,
            voice_bottleneck_logs=deps.voice_bottleneck_logs,
            record_turn_stage=deps.record_turn_stage,
            record_turn_path_summary=deps.record_turn_path_summary,
            summarize_p95_metrics=deps.summarize_p95_metrics,
            build_turn_summary_payload=deps.build_turn_summary_payload,
            log_turn_event=deps.log_turn_event,
            log=deps.log,
        )

    def build_omnivoice_request_runtime_deps(self) -> OmniVoiceRequestRuntimeDeps:
        deps = self.deps
        return OmniVoiceRequestRuntimeDeps(
            request_id_suffix=deps.request_id_suffix,
            tts_synth_request_factory=deps.tts_synth_request_factory,
            tts_synth_result_factory=deps.tts_synth_result_factory,
            omnivoice_model=deps.omnivoice_model,
            omnivoice_pcm_rate=deps.omnivoice_pcm_rate,
            omnivoice_stream=deps.omnivoice_stream,
            omnivoice_num_step=deps.omnivoice_num_step,
            omnivoice_speed=deps.omnivoice_speed,
            omnivoice_language=deps.omnivoice_language,
        )

    def build_omnivoice_source_runtime_deps(self) -> OmniVoiceSourceRuntimeDeps:
        deps = self.deps
        return OmniVoiceSourceRuntimeDeps(
            clean_tts_text=deps.clean_tts_text,
            merge_log_event_payload=deps.merge_log_event_payload,
            source_factory=deps.source_factory,
            get_http_session=deps.get_http_session,
            client_timeout_factory=deps.client_timeout_factory,
            omnivoice_timeout_sec=deps.omnivoice_timeout_sec,
            omnivoice_server_url=deps.omnivoice_server_url,
            omnivoice_voice=deps.omnivoice_voice,
            request_runtime_deps_factory=self.build_omnivoice_request_runtime_deps,
            monotonic=deps.monotonic,
            log_turn_event=deps.log_turn_event,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
            create_turn_scoped_task=deps.create_turn_scoped_task,
            log=deps.log,
        )


__all__ = [
    "VoiceAudioSupportDependencyComposition",
    "VoiceAudioSupportDependencyCompositionDeps",
]
