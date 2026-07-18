from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .local_control_tts_runtime import schedule_local_control_tts_from_runtime


@dataclass(frozen=True)
class LocalDeliveryEntryDeps:
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
        playback_task = deps.create_scoped_task(
            deps.stream_local_tts_sentences(
                sentence_queue,
                metrics=metrics,
                turn_id=turn_id,
                session_key=session_key,
                turn_scope=turn_scope,
            ),
            turn_scope=turn_scope,
        )
        return deps.streaming_delivery_factory(
            sentence_queue,
            tts_sink,
            playback_task,
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
                stream_tts_sentences=deps.stream_tts_sentences,
                create_playback_task=lambda coro, scope: deps.create_scoped_task(
                    coro, turn_scope=scope
                ),
                log_stage=deps.log_voice_stage,
                prefetch_chunks=deps.prefetch_chunks,
                log=deps.log,
            )
        )
