from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from .voice_orchestration import VoiceTranscriptReplyContext, VoiceTranscriptReplyDeps


@dataclass(frozen=True)
class VoiceReplyDispatchDeps:
    room_state_snapshot: Callable[[str], dict[str, Any]]
    session_topic_ids: Mapping[str, str]
    monotonic: Callable[[], float]
    process_voice_reply: Callable[..., Awaitable[Any]]
    active_conversation_awaiting_reply_sec: float
    active_conversation_voice_sec: float
    canned_wake_reply: str


async def dispatch_voice_reply_from_runtime(
    *,
    guild_id: int,
    transcript: Any,
    voice_segment: Any,
    session_key: str,
    room_session_key: str,
    owner_user_id: int | None,
    source_turn_id: str,
    segment_id: int,
    voiced_ms: float,
    raw_seconds: float,
    rms: float,
    wake_detected: bool,
    metrics: dict[str, Any],
    member: Any,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    reply_deps: VoiceTranscriptReplyDeps,
    deps: VoiceReplyDispatchDeps,
) -> None:
    meta = metrics.setdefault("meta", {})
    context = VoiceTranscriptReplyContext(
        guild_id=guild_id,
        transcript=transcript,
        voice_segment=voice_segment,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        source_turn_id=source_turn_id,
        segment_id=segment_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=rms,
        wake_detected=wake_detected,
        reply_in_progress=bool(deps.room_state_snapshot(room_session_key).get("reply_in_progress")),
        metrics=metrics,
        session_topic_seed=deps.session_topic_ids.get(session_key, ""),
        now_monotonic=deps.monotonic(),
        ingress_source=str(meta.get("ingress_source") or "discord_voice"),
        queue_wait_ms=float(meta.get("voice_queue_wait_ms") or 0.0),
        active_conversation_awaiting_reply_sec=deps.active_conversation_awaiting_reply_sec,
        active_conversation_voice_sec=deps.active_conversation_voice_sec,
        member=member,
        canned_wake_reply=deps.canned_wake_reply,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    await deps.process_voice_reply(context=context, deps=reply_deps)
