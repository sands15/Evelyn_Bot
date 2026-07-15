from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class DiscordTtsStreamRuntimeDeps:
    attach_current_task: Callable[[Any], Any]
    detach_task: Callable[[Any, Any], None]
    tts_running_state: Any
    create_omnivoice_source: Callable[..., Awaitable[Any]]
    mark_turn_stage: Callable[..., None]
    log_voice_latency: Callable[..., None]
    log_turn_event: Callable[..., None]
    record_voice_pipeline_failure: Callable[..., None]
    tts_lock: Any
    playback_manager: Any
    streaming_playback_request_factory: Callable[..., Any]
    omnivoice_timeout_sec: float
    tts_prefetch_chunks: int
    playback_start_lookahead_chunks: int
    playback_start_lookahead_timeout_ms: int
    create_turn_scoped_task: Callable[..., Any]
    log: Callable[[str], Any]


async def stream_tts_sentences_from_runtime(
    vc: Any,
    sentence_queue: Any,
    *,
    deps: DiscordTtsStreamRuntimeDeps,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: Any = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    task = deps.attach_current_task(turn_scope)
    if turn_scope is not None:
        turn_scope.transition(deps.tts_running_state, reason="stream_tts_sentences")

    def check_cancelled() -> None:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()

    async def synthesize_source(sentence: str, chunk_index: int) -> Any:
        return await deps.create_omnivoice_source(
            sentence,
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
            turn_scope=turn_scope,
            trace_payload={"source_type": "OmniVoicePCMStream"},
            on_request_start=lambda: (
                deps.mark_turn_stage(
                    metrics,
                    "tts_request_start",
                    event_name="tts_request_start",
                    chunk_index=chunk_index,
                ),
                deps.log_voice_latency(metrics, "tts_request_logged", "TTS request start"),
            ),
            on_response_headers=lambda: deps.log_voice_latency(
                metrics,
                "tts_response_headers_logged",
                "TTS response headers",
            ),
            on_first_byte=lambda: (
                deps.mark_turn_stage(
                    metrics,
                    "tts_first_byte",
                    event_name="tts_first_byte",
                    chunk_index=chunk_index,
                ),
                deps.log_voice_latency(metrics, "tts_first_byte_logged", "TTS first byte"),
            ),
            on_first_frame=lambda: deps.log_voice_latency(
                metrics,
                "tts_first_frame_logged",
                "TTS first frame",
            ),
            on_first_packet_sent=lambda ci=chunk_index: (
                deps.log_voice_latency(metrics, "first_packet_sent_logged", "first packet sent"),
                deps.log_turn_event(
                    "first_packet_sent",
                    turn_id=turn_id,
                    chunk_index=ci,
                    session_key=session_key,
                ),
            ),
        )

    def record_playback_failure(exc: Exception, *, stage: str) -> None:
        deps.record_voice_pipeline_failure(
            "tts_playback_failed",
            exc,
            metrics,
            turn_id=turn_id,
            session_key=session_key,
            stage=stage,
        )

    try:
        async with deps.tts_lock:
            await deps.playback_manager.stream_sentences(
                deps.streaming_playback_request_factory(
                    vc=vc,
                    sentence_queue=sentence_queue,
                    synthesize_source=synthesize_source,
                    guild_id=guild_id,
                    turn_id=turn_id,
                    session_key=session_key,
                    metrics=metrics,
                    ready_timeout_sec=deps.omnivoice_timeout_sec,
                    prefetch_chunks=deps.tts_prefetch_chunks,
                    lookahead_chunks=deps.playback_start_lookahead_chunks,
                    lookahead_timeout_ms=deps.playback_start_lookahead_timeout_ms,
                    create_task=lambda coro: deps.create_turn_scoped_task(coro, turn_scope=turn_scope),
                    check_cancelled=check_cancelled,
                    log=deps.log,
                    on_prefetch_failure=lambda exc: record_playback_failure(exc, stage="prefetch"),
                    on_prepared_failure=lambda exc: record_playback_failure(exc, stage="prepared_exception"),
                )
            )
    finally:
        deps.detach_task(turn_scope, task)
