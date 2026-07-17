from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .local_tts_stream_runtime import LocalTtsSingleRuntimeDeps, LocalTtsStreamRuntimeDeps


@dataclass(frozen=True)
class LocalTtsDependencyCompositionDeps:
    playback_manager: Any
    clean_tts_text: Callable[[str], str]
    strip_omnivoice_tags: Callable[[str], str]
    attach_current_task: Callable[..., Any]
    detach_task: Callable[..., Any]
    tts_running_state: Any
    tts_lock: Any
    create_omnivoice_source: Callable[..., Any]
    mark_turn_stage: Callable[..., Any]
    log_voice_latency: Callable[..., Any]
    log_turn_event: Callable[..., Any]
    mark_local_tts_first_playback: Callable[..., Any]
    record_voice_pipeline_failure: Callable[..., Any]
    omnivoice_timeout_sec: float
    tts_prefetch_chunks: int
    create_turn_scoped_task: Callable[..., Any]
    prefetch_tts_sources: Callable[..., Any]
    cleanup_prepared_tts_item: Callable[[object], None]


class LocalTtsDependencyComposition:
    """Builds local-speaker single-answer and streaming TTS contracts."""

    def __init__(self, deps: LocalTtsDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_local_tts_single_runtime_deps(self) -> LocalTtsSingleRuntimeDeps:
        deps = self.deps
        return LocalTtsSingleRuntimeDeps(
            playback_manager=deps.playback_manager,
            clean_tts_text=deps.clean_tts_text,
            strip_omnivoice_tags=deps.strip_omnivoice_tags,
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            tts_running_state=deps.tts_running_state,
            tts_lock=deps.tts_lock,
            create_omnivoice_source=deps.create_omnivoice_source,
            mark_turn_stage=deps.mark_turn_stage,
            log_voice_latency=deps.log_voice_latency,
            log_turn_event=deps.log_turn_event,
            mark_local_tts_first_playback=deps.mark_local_tts_first_playback,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
            omnivoice_timeout_sec=deps.omnivoice_timeout_sec,
        )

    def build_local_tts_stream_runtime_deps(self) -> LocalTtsStreamRuntimeDeps:
        deps = self.deps
        return LocalTtsStreamRuntimeDeps(
            playback_manager=deps.playback_manager,
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            tts_running_state=deps.tts_running_state,
            clean_tts_text=deps.clean_tts_text,
            strip_omnivoice_tags=deps.strip_omnivoice_tags,
            create_omnivoice_source=deps.create_omnivoice_source,
            mark_turn_stage=deps.mark_turn_stage,
            log_voice_latency=deps.log_voice_latency,
            log_turn_event=deps.log_turn_event,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
            tts_lock=deps.tts_lock,
            tts_prefetch_chunks=deps.tts_prefetch_chunks,
            create_turn_scoped_task=deps.create_turn_scoped_task,
            prefetch_tts_sources=deps.prefetch_tts_sources,
            omnivoice_timeout_sec=deps.omnivoice_timeout_sec,
            cleanup_prepared_tts_item=deps.cleanup_prepared_tts_item,
            mark_local_tts_first_playback=deps.mark_local_tts_first_playback,
        )


__all__ = [
    "LocalTtsDependencyComposition",
    "LocalTtsDependencyCompositionDeps",
]
