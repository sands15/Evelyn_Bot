from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceReplyLifecycle:
    accepted_turn_id: str
    should_cancel_old_scope: bool
    owner_ttl_sec: float
    topic_id: str
    history_user_text: str


def build_voice_reply_lifecycle(
    *,
    accepted_turn_id: str,
    gate_mode: str,
    reply_in_progress: bool,
    active_conversation_awaiting_reply_sec: float,
    active_conversation_voice_sec: float,
    topic_id: str,
    history_user_text: str,
) -> VoiceReplyLifecycle:
    owner_ttl_sec = (
        active_conversation_awaiting_reply_sec
        if gate_mode == "owner_followup"
        else active_conversation_voice_sec
    )
    should_cancel_old_scope = not (gate_mode == "owner_followup" and reply_in_progress)
    return VoiceReplyLifecycle(
        accepted_turn_id=accepted_turn_id,
        should_cancel_old_scope=should_cancel_old_scope,
        owner_ttl_sec=owner_ttl_sec,
        topic_id=topic_id,
        history_user_text=history_user_text,
    )
