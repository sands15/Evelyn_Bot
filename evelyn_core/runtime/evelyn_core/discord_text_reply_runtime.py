from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
)
from .conversation_ingress_context import (
    render_conversation_ingress_recovery_context,
)


@dataclass(frozen=True)
class DiscordTextReplyRuntimeDeps:
    memory_index_dir: Path
    attach_current_task: Callable[[Any], Any]
    detach_task: Callable[[Any, Any], Any]
    new_turn_metrics: Callable[..., dict[str, Any]]
    session_topic_id: Callable[[str | None], str | None]
    ask_llm_streaming: Callable[..., Awaitable[str]]
    log_llm_first_chunk: Callable[[dict[str, Any]], Any]
    session_state_snapshot: Callable[[str | None], dict[str, Any]]
    maybe_append_proactive_question: Callable[..., tuple[str, bool]]
    update_session_state: Callable[..., Any]
    build_answer_payload_from_text: Callable[[str], Any]
    format_display_text: Callable[..., str]
    fallback_answer_for: Callable[[str], str]
    build_delivery_plan: Callable[..., Any]
    split_tts_sentences: Callable[[str], list[str]]
    send_discord_text: Callable[..., Awaitable[Any]]


class BufferedEditStreamer:
    def __init__(
        self,
        message: Any,
        *,
        session_key: str | None = None,
        format_display_text: Callable[..., str],
        monotonic: Callable[[], float],
        min_edit_interval_ms: int,
        min_delta_chars: int,
        max_hold_ms: int,
    ):
        self.message = message
        self.session_key = session_key
        self.format_display_text = format_display_text
        self.monotonic = monotonic
        self.min_edit_interval_ms = int(min_edit_interval_ms)
        self.min_delta_chars = int(min_delta_chars)
        self.max_hold_ms = int(max_hold_ms)
        self.rendered_text = str(getattr(message, "content", "") or "")
        self.pending_text = self.rendered_text
        self.last_flush_at = 0.0
        self.first_pending_at = 0.0

    async def push(self, full_text: str, *, force: bool = False) -> None:
        candidate = self.format_display_text(full_text, session_key=self.session_key).strip()
        if not candidate or candidate == self.rendered_text:
            return
        now = self.monotonic()
        if self.pending_text != candidate:
            self.pending_text = candidate
            if self.first_pending_at <= 0.0:
                self.first_pending_at = now
        delta_chars = max(0, len(candidate) - len(self.rendered_text))
        elapsed_ms = (now - self.last_flush_at) * 1000.0 if self.last_flush_at > 0 else 10000.0
        held_ms = (now - self.first_pending_at) * 1000.0 if self.first_pending_at > 0 else elapsed_ms
        hard_break = candidate.endswith((".", "!", "?", "\n"))
        should_flush = (
            force
            or hard_break
            or held_ms >= self.max_hold_ms
            or (delta_chars >= self.min_delta_chars and elapsed_ms >= self.min_edit_interval_ms)
        )
        if not should_flush:
            return
        await self.message.edit(content=candidate)
        self.rendered_text = candidate
        self.pending_text = candidate
        self.last_flush_at = now
        self.first_pending_at = 0.0

    async def close(self, final_text: str) -> None:
        await self.push(final_text, force=True)


class DiscordEditSink:
    def __init__(self, streamer: BufferedEditStreamer):
        self.streamer = streamer
        self.parts: list[str] = []

    async def on_chunk(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)
        await self.streamer.push("".join(self.parts))

    async def close(self, final_text: str) -> None:
        await self.streamer.close(final_text)


async def _invoke_delivery_callback(
    callback: Callable[..., Any] | None,
    **kwargs: Any,
) -> None:
    if callback is None:
        return
    result = callback(**kwargs)
    if inspect.isawaitable(result):
        await result


async def stream_text_reply_from_runtime(
    channel: Any,
    user_text: str,
    *,
    guild_id: int,
    session_key: str,
    turn_id: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    include_voice: bool = False,
    turn_scope: Any = None,
    proactive_resolution: dict[str, Any] | None = None,
    ingress_recovery_context: dict[str, Any] | None = None,
    before_text_delivery: Callable[..., Any] | None = None,
    after_text_delivery: Callable[..., Any] | None = None,
    deps: DiscordTextReplyRuntimeDeps,
) -> tuple[str, Any, dict[str, Any], Any]:
    task = deps.attach_current_task(turn_scope)
    try:
        metrics = deps.new_turn_metrics(
            source=source,
            session_key=session_key,
            guild_id=guild_id,
            topic_id=deps.session_topic_id(session_key),
            turn_id=turn_id,
            segment_id=0,
        )
        metrics.setdefault("meta", {})["needs_tts"] = bool(include_voice)

        rendered_recovery_context = (
            render_conversation_ingress_recovery_context(
                ingress_recovery_context
            )
        )
        llm_user_text = (
            f"{rendered_recovery_context}\n\n"
            f"[현재 사용자 메시지]\n{user_text}"
            if rendered_recovery_context
            else user_text
        )
        if rendered_recovery_context:
            metrics.setdefault("meta", {})[
                "conversation_ingress_recovery_context"
            ] = True
        answer = await deps.ask_llm_streaming(
            llm_user_text,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            on_first_chunk=lambda: deps.log_llm_first_chunk(metrics),
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )
        response_exposure = current_memory_exposure_position()
        with memory_exposure_guard(
            expected_position=response_exposure,
            required=response_exposure is not None,
            index_dir=deps.memory_index_dir,
        ):
            awaiting_reply = bool(deps.session_state_snapshot(session_key).get("awaiting_user_reply"))
            if proactive_resolution is not None:
                metrics.setdefault("meta", {})["proactive_question_resolution"] = proactive_resolution
            proactive_asked = False
            if not (proactive_resolution or {}).get("resolved"):
                answer, proactive_asked = deps.maybe_append_proactive_question(
                    answer,
                    guild_id=guild_id,
                    source=source,
                    user_text=user_text,
                    awaiting_user_reply=awaiting_reply,
                    room_key=room_key,
                    person_key=person_key,
                    session_key=session_key,
                    session_memory_key=session_memory_key,
                    metrics=metrics,
                )
            if proactive_asked:
                deps.update_session_state(
                    session_key,
                    speaker="assistant",
                    awaiting_user_reply=True,
                    answer_text=answer,
                    user_text=user_text,
                )
            answer_payload = deps.build_answer_payload_from_text(answer)
            final_text = (
                deps.format_display_text(answer_payload.display_text, session_key=session_key).strip()
                or deps.fallback_answer_for(user_text)
            )
            delivery_plan = deps.build_delivery_plan(
                answer_payload,
                include_voice=include_voice,
                text_message=final_text,
                split_chunks=deps.split_tts_sentences,
            )
            await _invoke_delivery_callback(
                before_text_delivery,
                answer_text=answer,
                final_text=final_text,
                metrics=metrics,
            )
            sent_message = (
                await deps.send_discord_text(channel, final_text)
            ).message
            await _invoke_delivery_callback(
                after_text_delivery,
                sent_message=sent_message,
                final_text=final_text,
                metrics=metrics,
            )
            return answer, sent_message, metrics, delivery_plan
    finally:
        deps.detach_task(turn_scope, task)
