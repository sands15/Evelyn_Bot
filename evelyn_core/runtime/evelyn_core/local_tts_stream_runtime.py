from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class LocalTtsStreamRuntimeDeps:
    playback_manager: Any
    attach_current_task: Callable[[Any], Any]
    detach_task: Callable[[Any, Any], None]
    tts_running_state: Any
    clean_tts_text: Callable[[str], str]
    strip_omnivoice_tags: Callable[[str], str]
    create_omnivoice_source: Callable[..., Awaitable[Any]]
    mark_turn_stage: Callable[..., None]
    log_voice_latency: Callable[..., None]
    log_turn_event: Callable[..., None]
    record_voice_pipeline_failure: Callable[..., None]
    tts_lock: Any
    tts_prefetch_chunks: int
    create_turn_scoped_task: Callable[..., Any]
    prefetch_tts_sources: Callable[..., Awaitable[None]]
    omnivoice_timeout_sec: float
    cleanup_prepared_tts_item: Callable[[object], None]
    mark_local_tts_first_playback: Callable[..., None]


def cleanup_prepared_tts_item(item: object) -> None:
    if isinstance(item, tuple) and len(item) >= 2:
        cleanup = getattr(item[1], "cleanup", None)
        if cleanup is not None:
            with contextlib.suppress(Exception):
                cleanup()


@dataclass(frozen=True)
class LocalTtsSingleRuntimeDeps:
    playback_manager: Any
    clean_tts_text: Callable[[str], str]
    strip_omnivoice_tags: Callable[[str], str]
    attach_current_task: Callable[[Any], Any]
    detach_task: Callable[[Any, Any], None]
    tts_running_state: Any
    tts_lock: Any
    create_omnivoice_source: Callable[..., Awaitable[Any]]
    mark_turn_stage: Callable[..., None]
    log_voice_latency: Callable[..., None]
    log_turn_event: Callable[..., None]
    mark_local_tts_first_playback: Callable[..., None]
    record_voice_pipeline_failure: Callable[..., None]
    omnivoice_timeout_sec: float


async def speak_answer_local_from_runtime(
    answer: str,
    *,
    deps: LocalTtsSingleRuntimeDeps,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: Any = None,
    metrics: dict | None = None,
) -> bool:
    if not deps.playback_manager.enabled:
        return False
    text = deps.clean_tts_text(deps.strip_omnivoice_tags(answer) or answer)
    if not text:
        return False
    task = deps.attach_current_task(turn_scope)
    if turn_scope is not None:
        turn_scope.transition(deps.tts_running_state, reason="local_speaker_tts")
    try:
        async with deps.tts_lock:
            source = await deps.create_omnivoice_source(
                text,
                turn_id=turn_id,
                chunk_index=1,
                session_key=session_key,
                turn_scope=turn_scope,
                trace_payload={
                    "source_type": "LocalSpeakerOmniVoicePCMStream",
                    "output_mode": "local_speaker",
                },
                on_request_start=lambda: (
                    deps.mark_turn_stage(
                        metrics,
                        "tts_request_start",
                        event_name="local_tts_request_start",
                        chunk_index=1,
                    ),
                    deps.log_voice_latency(metrics, "tts_request_logged", "Local TTS request start"),
                ),
                on_response_headers=lambda: deps.log_voice_latency(
                    metrics,
                    "tts_response_headers_logged",
                    "Local TTS response headers",
                ),
                on_first_byte=lambda: (
                    deps.mark_turn_stage(
                        metrics,
                        "tts_first_byte",
                        event_name="local_tts_first_byte",
                        chunk_index=1,
                    ),
                    deps.log_voice_latency(metrics, "tts_first_byte_logged", "Local TTS first byte"),
                ),
                on_first_frame=lambda: deps.log_voice_latency(
                    metrics,
                    "tts_first_frame_logged",
                    "Local TTS first frame",
                ),
                on_first_packet_sent=lambda: (
                    deps.log_voice_latency(
                        metrics,
                        "first_packet_sent_logged",
                        "Local speaker first packet",
                    ),
                    deps.log_turn_event(
                        "local_tts_first_packet_sent",
                        turn_id=turn_id,
                        chunk_index=1,
                        session_key=session_key,
                    ),
                ),
            )
            wait_until_ready = getattr(source, "wait_until_ready", None)
            if wait_until_ready is not None:
                await wait_until_ready(timeout=max(0.2, deps.omnivoice_timeout_sec))
            return await deps.playback_manager.play_source(
                source,
                cleanup_source=True,
                turn_id=turn_id,
                session_key=session_key,
                metrics=metrics,
                on_first_playback=lambda: deps.mark_local_tts_first_playback(
                    metrics,
                    turn_id=turn_id,
                    chunk_index=1,
                    session_key=session_key,
                ),
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        deps.record_voice_pipeline_failure(
            "tts_playback_failed",
            exc,
            metrics,
            turn_id=turn_id,
            session_key=session_key,
            stage="local_speaker",
        )
        return False
    finally:
        deps.detach_task(turn_scope, task)


async def stream_local_tts_sentences_from_runtime(
    sentence_queue: "asyncio.Queue[str | None]",
    *,
    deps: LocalTtsStreamRuntimeDeps,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: Any = None,
) -> int:
    if not deps.playback_manager.enabled:
        return 0

    task = deps.attach_current_task(turn_scope)
    if turn_scope is not None:
        turn_scope.transition(deps.tts_running_state, reason="local_speaker_stream_tts")

    def check_cancelled() -> None:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()

    async def synthesize_source(sentence: str, chunk_index: int) -> Any:
        text = deps.clean_tts_text(deps.strip_omnivoice_tags(sentence) or sentence)
        return await deps.create_omnivoice_source(
            text,
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
            turn_scope=turn_scope,
            trace_payload={
                "source_type": "LocalSpeakerOmniVoicePCMStream",
                "output_mode": "local_speaker",
                "delivery_mode": "llm_sentence_stream",
            },
            on_request_start=lambda ci=chunk_index: (
                deps.mark_turn_stage(metrics, "tts_request_start", event_name="local_tts_request_start", chunk_index=ci),
                deps.log_voice_latency(metrics, "tts_request_logged", "Local TTS request start"),
            ),
            on_response_headers=lambda: deps.log_voice_latency(
                metrics,
                "tts_response_headers_logged",
                "Local TTS response headers",
            ),
            on_first_byte=lambda ci=chunk_index: (
                deps.mark_turn_stage(metrics, "tts_first_byte", event_name="local_tts_first_byte", chunk_index=ci),
                deps.log_voice_latency(metrics, "tts_first_byte_logged", "Local TTS first byte"),
            ),
            on_first_frame=lambda: deps.log_voice_latency(metrics, "tts_first_frame_logged", "Local TTS first frame"),
            on_first_packet_sent=lambda ci=chunk_index: (
                deps.log_voice_latency(metrics, "first_packet_sent_logged", "Local speaker first packet"),
                deps.log_turn_event(
                    "local_tts_first_packet_sent",
                    turn_id=turn_id,
                    chunk_index=ci,
                    session_key=session_key,
                ),
            ),
        )

    def record_prefetch_failure(exc: Exception) -> None:
        deps.record_voice_pipeline_failure(
            "tts_request_failed",
            exc,
            metrics,
            turn_id=turn_id,
            session_key=session_key,
            stage="local_speaker_stream_prefetch",
        )

    played_chunks = 0
    prepared_queue: asyncio.Queue[object] | None = None
    prefetch_task: asyncio.Task | None = None
    try:
        async with deps.tts_lock:
            prepared_queue = asyncio.Queue(maxsize=max(1, int(deps.tts_prefetch_chunks)))
            prefetch_task = deps.create_turn_scoped_task(
                deps.prefetch_tts_sources(
                    sentence_queue,
                    prepared_queue,
                    synthesize_source=synthesize_source,
                    ready_timeout_sec=deps.omnivoice_timeout_sec,
                    check_cancelled=check_cancelled,
                    on_failure=record_prefetch_failure,
                ),
                turn_scope=turn_scope,
            )
            while True:
                check_cancelled()
                if (
                    isinstance(metrics, dict)
                    and isinstance(metrics.get("meta"), dict)
                    and metrics["meta"].get("qualified_tts_interrupt") is True
                ):
                    break
                item = await prepared_queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                chunk_index, source = item
                try:
                    ok = await deps.playback_manager.play_source(
                        source,
                        cleanup_source=True,
                        turn_id=turn_id,
                        session_key=session_key,
                        metrics=metrics,
                        on_first_playback=lambda ci=int(chunk_index or 0): deps.mark_local_tts_first_playback(
                            metrics,
                            turn_id=turn_id,
                            chunk_index=ci,
                            session_key=session_key,
                        ),
                    )
                except Exception as exc:
                    deps.record_voice_pipeline_failure(
                        "tts_playback_failed",
                        exc,
                        metrics,
                        turn_id=turn_id,
                        session_key=session_key,
                        stage="local_speaker_stream",
                        chunk_index=int(chunk_index or 0),
                    )
                    raise
                if ok:
                    played_chunks += 1
                if (
                    isinstance(metrics, dict)
                    and isinstance(metrics.get("meta"), dict)
                    and metrics["meta"].get("qualified_tts_interrupt") is True
                ):
                    break
    finally:
        if prefetch_task is not None and not prefetch_task.done():
            prefetch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await prefetch_task
        if prepared_queue is not None:
            while True:
                try:
                    leftover = prepared_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                deps.cleanup_prepared_tts_item(leftover)
        deps.detach_task(turn_scope, task)

    return played_chunks
