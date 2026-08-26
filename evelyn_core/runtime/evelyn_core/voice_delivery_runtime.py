from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .text import clean_text


@dataclass(frozen=True)
class VoiceDeliveryRuntimeDeps:
    attach_current_task: Callable[[Any], asyncio.Task | None]
    detach_task: Callable[[Any, asyncio.Task | None], None]
    current_turn_id: Callable[[str | None], str | None]
    session_topic_id: Callable[[str | None], str | None]
    new_turn_metrics: Callable[..., dict[str, Any]]
    is_local_speaker_voice_client: Callable[[Any], bool]
    start_streaming_voice_delivery: Callable[..., Any]
    start_streaming_local_voice_delivery: Callable[..., Any]
    ask_llm_streaming: Callable[..., Awaitable[str]]
    speak_answer_local: Callable[..., Awaitable[Any]]
    local_playback_count: Callable[[], int]
    mark_barge_in_continuity_probe: Callable[..., None]
    record_voice_pipeline_failure: Callable[..., None]
    log_voice_latency: Callable[..., None]
    log_voice_stage: Callable[..., None]
    log_voice_bottleneck_summary: Callable[..., None]
    false_trigger_reason_code: str
    false_trigger_reason_label: str


class ReplyStreamFanout:
    def __init__(
        self,
        sinks: list[Any],
        *,
        on_reply_started: Callable[[], None] | None = None,
    ):
        self.sinks = [sink for sink in sinks if sink is not None]
        self.on_reply_started = on_reply_started

    async def on_chunk(self, text: str) -> None:
        if clean_text(text) and self.on_reply_started is not None:
            self.on_reply_started()
        for sink in self.sinks:
            await sink.on_chunk(text)

    async def close(self, final_text: str) -> None:
        for sink in self.sinks:
            close = getattr(sink, "close", None)
            if close is not None:
                await close(final_text)


def _prepare_voice_metrics(
    *,
    deps: VoiceDeliveryRuntimeDeps,
    metrics: dict[str, Any] | None,
    source: str,
    session_key: str | None,
    guild_id: int | None,
    local_speaker: bool,
) -> dict[str, Any]:
    if metrics is None:
        metrics = deps.new_turn_metrics(
            source=source,
            session_key=session_key,
            guild_id=guild_id,
            topic_id=deps.session_topic_id(session_key),
            turn_id=deps.current_turn_id(session_key),
            segment_id=0,
        )
    else:
        metrics.setdefault("started_at", time.monotonic())
        metrics.setdefault("marks", {})
        metrics.setdefault("meta", {})
    meta = metrics.setdefault("meta", {})
    meta["needs_tts"] = True
    meta.setdefault("reply_started", False)
    meta.setdefault("reply_final", False)
    meta.setdefault("qualified_tts_interrupt", False)
    if local_speaker:
        meta["output_mode"] = "local_speaker"
        meta["delivery_mode"] = "llm_sentence_stream"
        meta.setdefault("local_tts_playback_attempted", False)
        meta.setdefault("local_tts_playback_terminal_no_fallback", False)
    metrics.setdefault("tts_request_logged", False)
    metrics.setdefault("tts_response_headers_logged", False)
    metrics.setdefault("tts_first_byte_logged", False)
    metrics.setdefault("tts_first_frame_logged", False)
    metrics.setdefault("first_packet_sent_logged", False)
    metrics.setdefault("local_first_playback_logged", False)
    return metrics


def _local_tts_fallback_block_reason(metrics: dict[str, Any]) -> str | None:
    meta = metrics.get("meta")
    if isinstance(meta, dict):
        if meta.get("local_tts_playback_terminal_no_fallback") is True:
            return str(
                meta.get("local_tts_playback_rejected_reason")
                or "terminal_no_fallback"
            )
        if meta.get("local_tts_playback_attempted") is True:
            return "playback_attempted"
    marks = metrics.get("marks")
    if isinstance(marks, dict) and "local_tts_first_playback" in marks:
        return "playback_attempted"
    return None


async def finalize_voice_answer_from_runtime(
    answer: str,
    *,
    on_final_answer: Callable[[str], Awaitable[None]] | None,
    delivery: Any,
    metrics: dict[str, Any],
    deps: VoiceDeliveryRuntimeDeps,
) -> tuple[str, int]:
    cleaned_answer = clean_text(answer)
    meta = metrics.setdefault("meta", {})
    if cleaned_answer:
        meta["reply_started"] = True
    meta["reply_final"] = bool(cleaned_answer)
    if not cleaned_answer:
        meta["voice_delivery_failure_code"] = "voice_delivery_empty"
    deps.log_voice_stage(metrics, "LLM 완료", extra=f"chars={len(cleaned_answer)}", key="llm_done")
    if cleaned_answer and on_final_answer is not None:
        await on_final_answer(cleaned_answer)
    try:
        await delivery.close(cleaned_answer)
        queued_sentence_count = await delivery.finalize()
        if cleaned_answer:
            deps.mark_barge_in_continuity_probe(
                metrics,
                success=True,
                reason="finalize_complete",
                queued_sentence_count=queued_sentence_count,
            )
        else:
            deps.mark_barge_in_continuity_probe(
                metrics,
                success=False,
                reason="finalize_empty_answer",
                queued_sentence_count=queued_sentence_count,
                reason_code=deps.false_trigger_reason_code,
                reason_label=deps.false_trigger_reason_label,
            )
    except Exception as exc:
        deps.mark_barge_in_continuity_probe(
            metrics,
            success=False,
            reason=(
                "finalize_exception:"
                f"{type(exc).__name__}"
            ),
            queued_sentence_count=0,
        )
        raise
    return cleaned_answer, queued_sentence_count


async def execute_voice_delivery_plan_from_runtime(
    vc: Any,
    delivery_plan,
    *,
    deps: VoiceDeliveryRuntimeDeps,
    metrics: dict[str, Any],
    turn_id: str | None,
    session_key: str | None,
    turn_scope: Any = None,
) -> int:
    from evelyn_core.discord_delivery import execute_streaming_voice_delivery_plan

    return await execute_streaming_voice_delivery_plan(
        delivery_plan,
        start_delivery=lambda: deps.start_streaming_voice_delivery(
            vc,
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        ),
    )


async def ask_llm_and_speak_local_from_runtime(
    _vc: Any,
    user_text: str,
    *,
    deps: VoiceDeliveryRuntimeDeps,
    guild_id: int | None = None,
    on_final_answer: Callable[[str], Awaitable[None]] | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    memory_owner_scope: str | None = None,
    source: str = "voice",
    debug_text: str | None = None,
    metrics: dict[str, Any] | None = None,
    turn_scope: Any = None,
) -> str:
    task = deps.attach_current_task(turn_scope)
    try:
        metrics = _prepare_voice_metrics(
            deps=deps,
            metrics=metrics,
            source=source,
            session_key=session_key,
            guild_id=guild_id,
            local_speaker=True,
        )
        turn_id = metrics.get("meta", {}).get("turn_id") or deps.current_turn_id(session_key)
        deps.log_voice_stage(metrics, "LLM/local streaming TTS pipeline start", extra=f"source={source} mode=local_speaker_stream")

        delivery = deps.start_streaming_local_voice_delivery(
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        )
        fanout = ReplyStreamFanout(
            [delivery],
            on_reply_started=lambda: metrics.setdefault("meta", {}).__setitem__(
                "reply_started",
                True,
            ),
        )
        answer = ""
        cleaned_answer = ""
        queued_sentence_count = 0
        fallback_needed = False
        playback_count_before = deps.local_playback_count()
        try:
            answer = await deps.ask_llm_streaming(
                user_text,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                memory_owner_scope=memory_owner_scope,
                on_sentence=fanout.on_chunk,
                on_first_chunk=lambda: deps.log_voice_latency(metrics, "llm_first_chunk_logged", "LLM first chunk"),
                source=source,
                debug_text=debug_text,
                metrics=metrics,
                turn_scope=turn_scope,
            )
            try:
                cleaned_answer, queued_sentence_count = await finalize_voice_answer_from_runtime(
                    answer,
                    on_final_answer=on_final_answer,
                    delivery=delivery,
                    metrics=metrics,
                    deps=deps,
                )
            except Exception as exc:
                cleaned_answer = clean_text(answer)
                metrics.setdefault("meta", {})[
                    "local_streaming_tts_error"
                ] = type(exc).__name__
                deps.record_voice_pipeline_failure(
                    "tts_playback_failed",
                    exc,
                    metrics,
                    turn_id=turn_id,
                    session_key=session_key,
                    stage="local_speaker_stream_finalize",
                )
                fallback_needed = True
            playback_count_after = deps.local_playback_count()
            meta = metrics.setdefault("meta", {})
            exact_played_chunks = meta.get("local_tts_played_chunk_count")
            if isinstance(exact_played_chunks, int) and not isinstance(
                exact_played_chunks,
                bool,
            ):
                played_chunk_count = max(0, exact_played_chunks)
            else:
                played_chunk_count = max(
                    0,
                    playback_count_after - playback_count_before,
                )
            stream_completed = (
                not fallback_needed
                and queued_sentence_count > 0
                and played_chunk_count >= queued_sentence_count
                and meta.get("qualified_tts_interrupt") is not True
            )
            fallback_block_reason = _local_tts_fallback_block_reason(metrics)
            if fallback_block_reason is None and played_chunk_count > 0:
                fallback_block_reason = "playback_attempted"
            stream_incomplete = not stream_completed
            if fallback_block_reason is not None:
                if fallback_needed or stream_incomplete:
                    metrics.setdefault("meta", {})[
                        "local_streaming_tts_fallback_suppressed_reason"
                    ] = fallback_block_reason
                fallback_needed = False
            elif stream_incomplete:
                fallback_needed = True
                metrics.setdefault("meta", {})["local_streaming_tts_fallback_reason"] = (
                    "no_sentence_queued" if queued_sentence_count <= 0 else "no_local_playback"
                )
        finally:
            await delivery.abort()

        playback_completed = stream_completed
        if fallback_needed and cleaned_answer:
            metrics.setdefault("meta", {})["local_streaming_tts_fallback_used"] = True
            fallback_played = await deps.speak_answer_local(
                cleaned_answer,
                turn_id=turn_id,
                session_key=session_key,
                turn_scope=turn_scope,
                metrics=metrics,
            )
            playback_completed = (
                bool(fallback_played)
                and meta.get("qualified_tts_interrupt") is not True
            )
        if cleaned_answer:
            metrics.setdefault("meta", {})["playback_completed"] = playback_completed

        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra=f"source={source} chars={len(cleaned_answer)} mode=local_speaker_stream sentences={queued_sentence_count} fallback={fallback_needed}",
            event_name="voice_turn_summary",
        )
        return cleaned_answer
    except asyncio.CancelledError:
        metrics = metrics or {}
        metrics.setdefault("meta", {})["playback_cancelled"] = True
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = "cancelled"
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="cancelled=true mode=local_speaker",
            event_name="voice_turn_summary",
        )
        raise
    except Exception as exc:
        metrics = metrics or {}
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = type(exc).__name__
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="error=true mode=local_speaker",
            event_name="voice_turn_summary",
        )
        raise
    finally:
        deps.detach_task(turn_scope, task)


async def ask_llm_and_speak_streaming_from_runtime(
    vc: Any,
    user_text: str,
    *,
    deps: VoiceDeliveryRuntimeDeps,
    guild_id: int | None = None,
    on_final_answer: Callable[[str], Awaitable[None]] | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    memory_owner_scope: str | None = None,
    source: str = "voice",
    debug_text: str | None = None,
    metrics: dict[str, Any] | None = None,
    turn_scope: Any = None,
) -> str:
    if deps.is_local_speaker_voice_client(vc):
        return await ask_llm_and_speak_local_from_runtime(
            vc,
            user_text,
            deps=deps,
            guild_id=guild_id,
            on_final_answer=on_final_answer,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            memory_owner_scope=memory_owner_scope,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )

    task = deps.attach_current_task(turn_scope)
    try:
        metrics = _prepare_voice_metrics(
            deps=deps,
            metrics=metrics,
            source=source,
            session_key=session_key,
            guild_id=guild_id,
            local_speaker=False,
        )
        deps.log_voice_stage(metrics, "LLM/TTS 파이프라인 시작", extra=f"source={source} mode=llm_streaming")

        delivery = deps.start_streaming_voice_delivery(
            vc,
            metrics=metrics,
            turn_id=metrics.get("meta", {}).get("turn_id") or deps.current_turn_id(session_key),
            session_key=session_key,
            turn_scope=turn_scope,
        )
        fanout = ReplyStreamFanout(
            [delivery],
            on_reply_started=lambda: metrics.setdefault("meta", {}).__setitem__(
                "reply_started",
                True,
            ),
        )

        answer = ""
        queued_sentence_count = 0
        try:
            answer = await deps.ask_llm_streaming(
                user_text,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                memory_owner_scope=memory_owner_scope,
                on_sentence=fanout.on_chunk,
                on_first_chunk=lambda: deps.log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
                source=source,
                debug_text=debug_text,
                metrics=metrics,
                turn_scope=turn_scope,
            )
            answer, queued_sentence_count = await finalize_voice_answer_from_runtime(
                answer,
                on_final_answer=on_final_answer,
                delivery=delivery,
                metrics=metrics,
                deps=deps,
            )
        finally:
            await delivery.abort()

        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra=f"source={source} chars={len(answer)} mode=llm_streaming sentences={queued_sentence_count}",
            event_name="voice_turn_summary",
        )
        return answer
    except asyncio.CancelledError:
        metrics = metrics or {}
        metrics.setdefault("meta", {})["playback_cancelled"] = True
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = "cancelled"
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="cancelled=true mode=llm_streaming",
            event_name="voice_turn_summary",
        )
        raise
    except Exception as exc:
        metrics = metrics or {}
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = type(exc).__name__
        deps.log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="error=true mode=llm_streaming",
            event_name="voice_turn_summary",
        )
        raise
    finally:
        deps.detach_task(turn_scope, task)
