from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .discord_tts_stream_runtime import (
    DiscordTtsSingleRuntimeDeps,
    DiscordTtsStreamRuntimeDeps,
)


@dataclass(frozen=True)
class DiscordTtsDependencyCompositionDeps:
    memory_index_dir: Path
    is_local_speaker_voice_client: Callable[..., bool]
    speak_answer_local: Callable[..., Any]
    tts_running_state: Any
    play_cached_answer_audio: Callable[..., Any]
    tts_lock: Any
    create_omnivoice_source: Callable[..., Any]
    log_turn_event: Callable[..., Any]
    log_voice_latency: Callable[..., Any]
    playback_manager: Any
    source_playback_request_factory: Callable[..., Any]
    attach_current_task: Callable[..., Any]
    detach_task: Callable[..., Any]
    mark_turn_stage: Callable[..., Any]
    record_voice_pipeline_failure: Callable[..., Any]
    streaming_playback_request_factory: Callable[..., Any]
    omnivoice_timeout_sec: float
    tts_prefetch_chunks: int
    playback_start_lookahead_chunks: int
    playback_start_lookahead_timeout_ms: int
    create_turn_scoped_task: Callable[..., Any]
    log: Callable[[str], Any]


class DiscordTtsDependencyComposition:
    """Builds Discord single-answer and streaming TTS contracts."""

    def __init__(self, deps: DiscordTtsDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_discord_tts_single_runtime_deps(self) -> DiscordTtsSingleRuntimeDeps:
        deps = self.deps
        return DiscordTtsSingleRuntimeDeps(
            memory_index_dir=deps.memory_index_dir,
            is_local_speaker_voice_client=deps.is_local_speaker_voice_client,
            speak_answer_local=deps.speak_answer_local,
            tts_running_state=deps.tts_running_state,
            play_cached_answer_audio=deps.play_cached_answer_audio,
            tts_lock=deps.tts_lock,
            create_omnivoice_source=deps.create_omnivoice_source,
            log_turn_event=deps.log_turn_event,
            log_voice_latency=deps.log_voice_latency,
            playback_manager=deps.playback_manager,
            source_playback_request_factory=deps.source_playback_request_factory,
        )

    def build_discord_tts_stream_runtime_deps(self) -> DiscordTtsStreamRuntimeDeps:
        deps = self.deps
        return DiscordTtsStreamRuntimeDeps(
            attach_current_task=deps.attach_current_task,
            detach_task=deps.detach_task,
            tts_running_state=deps.tts_running_state,
            create_omnivoice_source=deps.create_omnivoice_source,
            mark_turn_stage=deps.mark_turn_stage,
            log_voice_latency=deps.log_voice_latency,
            log_turn_event=deps.log_turn_event,
            record_voice_pipeline_failure=deps.record_voice_pipeline_failure,
            tts_lock=deps.tts_lock,
            playback_manager=deps.playback_manager,
            streaming_playback_request_factory=deps.streaming_playback_request_factory,
            omnivoice_timeout_sec=deps.omnivoice_timeout_sec,
            tts_prefetch_chunks=deps.tts_prefetch_chunks,
            playback_start_lookahead_chunks=deps.playback_start_lookahead_chunks,
            playback_start_lookahead_timeout_ms=(
                deps.playback_start_lookahead_timeout_ms
            ),
            create_turn_scoped_task=deps.create_turn_scoped_task,
            log=deps.log,
        )


__all__ = [
    "DiscordTtsDependencyComposition",
    "DiscordTtsDependencyCompositionDeps",
]
