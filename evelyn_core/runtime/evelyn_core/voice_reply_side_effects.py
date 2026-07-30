from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping


@dataclass(frozen=True)
class VoiceReplySideEffectDeps:
    session_speculative_policies: MutableMapping[str, Any]
    append_history: Callable[..., Any]
    compute_runtime_mode: Callable[[dict[str, Any]], str]
    record_context_pipeline_benchmark: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    read_cached_cognitive_state: Callable[..., Any]
    apply_ask_gating: Callable[..., dict[str, Any]]
    schedule_search_followup: Callable[..., Any]
    session_state_snapshot: Callable[[str | None], dict[str, Any]]
    mark_session_active: Callable[..., Any]
    set_room_owner: Callable[..., Any]
    commit_session_continuity: Callable[..., Any]
    log: Callable[..., Any]
    active_conversation_voice_question_sec: float
    active_conversation_voice_sec: float
    active_conversation_awaiting_reply_sec: float


def finalize_voice_reply_side_effects_from_runtime(
    *,
    guild_id: int,
    member: Any,
    session_key: str,
    room_session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    voice_reply: Any,
    plain_answer: str,
    metrics: dict[str, Any],
    turn_scope: Any,
    accepted_turn_id: str,
    segment_id: int,
    deps: VoiceReplySideEffectDeps,
) -> None:
    deps.session_speculative_policies.pop(session_key, None)
    deps.append_history(session_key, voice_reply.history_user_text, plain_answer, guild_id=guild_id)
    runtime_mode = ((metrics.get("meta") or {}).get("runtime_mode")) or deps.compute_runtime_mode(metrics)
    deps.record_context_pipeline_benchmark(
        metrics=metrics,
        user_text=voice_reply.history_user_text,
        answer=plain_answer,
        source="voice",
        guild_id=guild_id,
        session_key=session_key,
    )
    memory_writer_decision = deps.schedule_memory_update(
        guild_id,
        voice_reply.history_user_text,
        plain_answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source="voice",
        user_speaker=getattr(member, "display_name", ""),
        assistant_speaker="Evelyn",
        session_key=session_key,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
    )
    metrics.setdefault("meta", {})["memory_writer_decision"] = memory_writer_decision
    search_requested = bool(
        deps.apply_ask_gating(
            deps.read_cached_cognitive_state(
                guild_id,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            ),
            source="voice",
        ).get("action")
        == "search_then_answer"
    )
    deps.schedule_search_followup(
        guild_id,
        session_key,
        voice_reply.history_user_text,
        plain_answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        channel_id=None,
        source="search-followup-voice",
        force=search_requested,
        turn_scope=None,
        runtime_mode=runtime_mode,
    )
    awaiting_reply = bool(deps.session_state_snapshot(session_key).get("awaiting_user_reply"))
    followup_ttl = deps.active_conversation_voice_question_sec if awaiting_reply else deps.active_conversation_voice_sec
    deps.mark_session_active(
        session_key,
        user_id=getattr(member, "id", None),
        ttl_sec=followup_ttl,
        speaker="assistant",
        awaiting_user_reply=awaiting_reply,
        topic_id=voice_reply.topic_id,
        answer_text=plain_answer,
        user_text=voice_reply.history_user_text,
    )
    deps.set_room_owner(
        room_session_key,
        getattr(member, "id", None),
        ttl_sec=deps.active_conversation_awaiting_reply_sec if awaiting_reply else followup_ttl,
        reason="assistant_reply",
        session_key=session_key,
        turn_id=accepted_turn_id,
        segment_id=segment_id,
    )
    try:
        continuity_status = deps.commit_session_continuity()
        metrics.setdefault("meta", {}).update(
            {
                "continuity_commit": "durable",
                "continuity_generation": int(
                    continuity_status.get(
                        "checkpointGeneration"
                    )
                    or 0
                ),
            }
        )
    except Exception as exc:
        metrics.setdefault("meta", {}).update(
            {
                "continuity_commit": "failed",
                "continuity_error": (
                    "conversation_continuity_commit_failed"
                ),
            }
        )
        deps.log(
            "[VOICE TURN] continuity_commit_failed errorType=",
            type(exc).__name__,
        )
