from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .discord_session_policy import VoiceReplyGateInput, decide_voice_reply_gate


@dataclass(frozen=True)
class VoiceReplyGateRuntimeDeps:
    session_state_snapshot: Callable[[str | None], dict[str, Any]]
    room_state_snapshot: Callable[[str | None], dict[str, Any]]
    is_room_owner_active: Callable[[str | None, int | None], bool]
    is_session_active_for_user: Callable[[str, int | None], bool]
    tts_input_suppression_reason: Callable[..., str | None]
    room_last_voice_reply_at: MutableMapping[str, float]
    post_tts_ignore_sec: float
    reply_cooldown_sec: float
    normalize_voice_text: Callable[[str], str]
    contains_wake_word: Callable[[str], bool]
    looks_like_brief_filler_text: Callable[[str], bool]
    looks_like_repetitive_noise_text: Callable[[str], bool]
    is_similar: Callable[[str, str], bool]
    min_text_len: int
    monotonic: Callable[[], float] = time.monotonic


def should_reply_to_voice_from_runtime(
    *,
    guild_id: int,
    text: str,
    wake_detected: bool = False,
    wake_match_mode: str = "",
    session_key: str | None = None,
    room_session_key: str | None = None,
    user_id: int | None = None,
    active_speaker_user_id: int | None = None,
    ignore_tts_suppression: bool = False,
    deps: VoiceReplyGateRuntimeDeps,
) -> tuple[bool, str, str]:
    now = deps.monotonic()
    session_state = deps.session_state_snapshot(session_key)
    room_state = deps.room_state_snapshot(room_session_key)
    owner_user_id = room_state.get("owner_user_id")
    owner_active = deps.is_room_owner_active(room_session_key, user_id)
    active_session = session_key is not None and deps.is_session_active_for_user(session_key, user_id)
    if active_speaker_user_id is None:
        active_speaker_user_id = room_state.get("active_speaker_user_id")

    tts_suppression = None
    if not ignore_tts_suppression:
        tts_suppression = deps.tts_input_suppression_reason(
            guild_id=guild_id,
            post_tts_ignore_sec=deps.post_tts_ignore_sec,
            now=now,
        )
    cooldown_active = bool(
        room_session_key and (now - deps.room_last_voice_reply_at.get(room_session_key, 0.0) < deps.reply_cooldown_sec)
    )
    decision = decide_voice_reply_gate(
        VoiceReplyGateInput(
            text=text,
            wake_detected=wake_detected,
            wake_match_mode=wake_match_mode,
            user_id=user_id,
            owner_user_id=owner_user_id,
            owner_active=owner_active,
            active_session=active_session,
            awaiting_user_reply=bool(session_state.get("awaiting_user_reply")),
            active_speaker_user_id=active_speaker_user_id,
            last_stt_text=str(session_state.get("last_stt_text", "")),
            tts_suppression=tts_suppression,
            cooldown_active=cooldown_active,
        ),
        normalize_voice_text=deps.normalize_voice_text,
        contains_wake_word=deps.contains_wake_word,
        looks_like_brief_filler_text=deps.looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=deps.looks_like_repetitive_noise_text,
        is_similar=deps.is_similar,
        min_text_len=deps.min_text_len,
    )
    return decision.accepted, decision.reason, decision.gate_mode
