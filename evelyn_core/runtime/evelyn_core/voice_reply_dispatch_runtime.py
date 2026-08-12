from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from .voice_orchestration import VoiceTranscriptReplyContext, VoiceTranscriptReplyDeps
from .voice_ingress_runtime import voice_listener_binding_is_current


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
    voice_listener_binding: Any = None,
    release_ingress_worker: Callable[[], Any] | None = None,
    reply_deps: VoiceTranscriptReplyDeps,
    deps: VoiceReplyDispatchDeps,
) -> None:
    if not voice_listener_binding_is_current(member, voice_listener_binding):
        return
    if voice_listener_binding is not None:
        get_voice_client = reply_deps.get_voice_client

        def get_current_voice_client() -> Any:
            if not voice_listener_binding_is_current(member, voice_listener_binding):
                return None
            return get_voice_client()

        reply_deps = copy(reply_deps)
        object.__setattr__(reply_deps, "get_voice_client", get_current_voice_client)
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
        release_ingress_worker=release_ingress_worker,
    )
    if not voice_listener_binding_is_current(member, voice_listener_binding):
        return
    await deps.process_voice_reply(context=context, deps=reply_deps)
