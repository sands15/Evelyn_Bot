from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .tts_playback import StreamingVoiceDelivery, TTSQueueSink


@dataclass(frozen=True)
class DiscordTextDeliveryResult:
    message: Any
    used_reference: bool
    fallback_used: bool


@dataclass(frozen=True)
class DiscordStreamingVoiceDeliveryRequest:
    voice_client: Any
    metrics: dict[str, Any]
    turn_id: str | None
    session_key: str | None
    turn_scope: Any
    stream_tts_sentences: Callable[..., Awaitable[Any]]
    create_playback_task: Callable[[Awaitable[Any], Any], asyncio.Task]
    log_stage: Callable[..., Any] | None
    prefetch_chunks: int | None
    log: Callable[[str], None] | None = None


def _discord_http_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status", None)
    try:
        normalized = int(status)
    except (TypeError, ValueError):
        return None
    return normalized if 100 <= normalized <= 599 else None


def _is_definitive_reference_rejection(exc: BaseException) -> bool:
    """Return true only when Discord definitely rejected the first send.

    A timeout, connection failure, or 5xx may happen after Discord accepted
    the message. Retrying those failures without the reference can duplicate
    a reply, so only non-ambiguous client rejections are eligible.
    """

    status = _discord_http_status(exc)
    return bool(
        status is not None
        and 400 <= status < 500
        and status not in {408, 409, 425, 429}
    )


async def send_discord_text(
    channel: Any,
    text: str,
    *,
    reference_message_id: int | str | None = None,
    reference_factory: Callable[[int], Any] | None = None,
) -> DiscordTextDeliveryResult:
    if reference_message_id is None or reference_factory is None:
        return DiscordTextDeliveryResult(
            message=await channel.send(text),
            used_reference=False,
            fallback_used=False,
        )

    try:
        reference = reference_factory(int(reference_message_id))
    except (TypeError, ValueError, OverflowError):
        # Reference construction failed locally, before any network send.
        return DiscordTextDeliveryResult(
            message=await channel.send(text),
            used_reference=False,
            fallback_used=True,
        )

    try:
        message = await channel.send(text, reference=reference)
        return DiscordTextDeliveryResult(
            message=message,
            used_reference=True,
            fallback_used=False,
        )
    except Exception as exc:
        if not _is_definitive_reference_rejection(exc):
            raise
        return DiscordTextDeliveryResult(
            message=await channel.send(text),
            used_reference=False,
            fallback_used=True,
        )


def build_streaming_voice_delivery(request: DiscordStreamingVoiceDeliveryRequest) -> StreamingVoiceDelivery:
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    tts_sink = TTSQueueSink(sentence_queue, log=request.log)
    playback_task = request.create_playback_task(
        request.stream_tts_sentences(
            request.voice_client,
            sentence_queue,
            metrics=request.metrics,
            turn_id=request.turn_id,
            session_key=request.session_key,
            turn_scope=request.turn_scope,
        ),
        request.turn_scope,
    )
    return StreamingVoiceDelivery(
        sentence_queue,
        tts_sink,
        playback_task,
        metrics=request.metrics,
        log_stage=request.log_stage,
        prefetch_chunks=request.prefetch_chunks,
    )


async def execute_streaming_voice_delivery_plan(
    delivery_plan: Any,
    *,
    start_delivery: Callable[[], StreamingVoiceDelivery],
) -> int:
    if not delivery_plan.should_play_voice or not delivery_plan.tts_chunks:
        return 0

    delivery = start_delivery()
    try:
        for chunk in delivery_plan.tts_chunks:
            await delivery.on_chunk(chunk)
        await delivery.close(delivery_plan.text_message or "")
        return await delivery.finalize()
    finally:
        await delivery.abort()
