from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .local_control_tts_runtime import schedule_local_control_tts_from_runtime
from .memory_exposure import (
    MemoryExposurePosition,
    current_memory_exposure_position,
    memory_exposure_guard,
)


async def _run_delivery_under_memory_exposure(
    awaitable: Any,
    *,
    position: MemoryExposurePosition | None,
    memory_index_dir: Path,
) -> Any:
    entered = False
    try:
        with memory_exposure_guard(
            expected_position=position,
            required=position is not None,
            index_dir=memory_index_dir,
        ):
            entered = True
            return await awaitable
    finally:
        if not entered:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()


@dataclass(frozen=True)
class LocalDeliveryEntryDeps:
    memory_index_dir: Path
    queue_factory: Callable[[], Any]
    sink_factory: Callable[..., Any]
    stream_local_tts_sentences: Callable[..., Any]
    create_scoped_task: Callable[..., Any]
    streaming_delivery_factory: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    mark_turn_stage: Callable[..., Any]
    log_voice_latency: Callable[..., Any]
    local_control_tts: Callable[[], Any]
    prefetch_chunks: int
    log: Callable[..., Any]


@dataclass(frozen=True)
class DiscordDeliveryEntryDeps:
    memory_index_dir: Path
    request_factory: Callable[..., Any]
    build_streaming_delivery: Callable[[Any], Any]
    stream_tts_sentences: Callable[..., Any]
    create_scoped_task: Callable[..., Any]
    log_voice_stage: Callable[..., Any]
    prefetch_chunks: int
    log: Callable[..., Any]


class DeliveryEntryComposition:
    """Owns local and Discord streaming delivery entry adapters."""

    def __init__(
        self,
        local: LocalDeliveryEntryDeps,
        discord: DiscordDeliveryEntryDeps,
    ) -> None:
        self.local = local
        self.discord = discord

    def mark_local_tts_first_playback(
        self,
        metrics: dict | None,
        *,
        turn_id: str | None,
        chunk_index: int,
        session_key: str | None,
    ) -> None:
        deps = self.local
        if not metrics or "local_tts_first_playback" not in (metrics.get("marks") or {}):
            deps.mark_turn_stage(
                metrics,
                "local_tts_first_playback",
                event_name="local_tts_first_playback",
                turn_id=turn_id,
                chunk_index=chunk_index,
                session_key=session_key,
                output_mode="local_speaker",
            )
        deps.log_voice_latency(
            metrics, "local_first_playback_logged", "Local speaker first playback"
        )

    def start_streaming_local_voice_delivery(
        self,
        *,
        metrics: dict,
        turn_id: str | None,
        session_key: str | None,
        turn_scope: Any | None,
    ) -> Any:
        deps = self.local
        sentence_queue = deps.queue_factory()
        tts_sink = deps.sink_factory(sentence_queue, log=deps.log)

        def start_playback_task() -> Any:
            memory_exposure = current_memory_exposure_position()
            return deps.create_scoped_task(
                _run_delivery_under_memory_exposure(
                    deps.stream_local_tts_sentences(
                        sentence_queue,
                        metrics=metrics,
                        turn_id=turn_id,
                        session_key=session_key,
                        turn_scope=turn_scope,
                    ),
                    position=memory_exposure,
                    memory_index_dir=deps.memory_index_dir,
                ),
                turn_scope=turn_scope,
            )

        return deps.streaming_delivery_factory(
            sentence_queue,
            tts_sink,
            start_playback_task,
            metrics=metrics,
            log_stage=deps.log_voice_stage,
            prefetch_chunks=deps.prefetch_chunks,
        )

    def schedule_local_control_tts(
        self,
        answer: str,
        *,
        turn_id: str | None = None,
        session_key: str | None = None,
        turn_scope: Any | None = None,
    ) -> Any:
        return schedule_local_control_tts_from_runtime(
            answer,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
            deps=self.local.local_control_tts(),
        )

    def start_streaming_voice_delivery(
        self,
        vc: Any,
        *,
        metrics: dict,
        turn_id: str | None,
        session_key: str | None,
        turn_scope: Any | None,
    ) -> Any:
        deps = self.discord
        return deps.build_streaming_delivery(
            deps.request_factory(
                voice_client=vc,
                metrics=metrics,
                turn_id=turn_id,
                session_key=session_key,
                turn_scope=turn_scope,
                stream_tts_sentences=lambda *args, **kwargs: (
                    _run_delivery_under_memory_exposure(
                        deps.stream_tts_sentences(*args, **kwargs),
                        position=current_memory_exposure_position(),
                        memory_index_dir=deps.memory_index_dir,
                    )
                ),
                create_playback_task=lambda coro, scope: deps.create_scoped_task(
                    coro, turn_scope=scope
                ),
                log_stage=deps.log_voice_stage,
                prefetch_chunks=deps.prefetch_chunks,
                log=deps.log,
            )
        )
