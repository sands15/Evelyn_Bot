from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscordTextIngressContext:
    guild_id: int
    channel_id: int
    user_id: int
    thread_id: int | None
    session_key: str
    room_key: str
    person_key: str | None
    session_memory_key: str | None
    reply_slot_key: str


@dataclass(frozen=True)
class DiscordVoiceIngressContext:
    guild_id: int
    voice_channel_id: int | None
    user_id: int
    room_session_key: str
    session_key: str
    room_key: str
    person_key: str | None
    session_memory_key: str | None


def make_text_session_key(guild_id: int, channel_id: int, user_id: int | None = None, thread_id: int | None = None) -> str:
    thread_part = f":thread:{thread_id}" if thread_id is not None else ""
    user_part = f":user:{user_id}" if user_id is not None else ""
    return f"guild:{guild_id}:text:{channel_id}{thread_part}{user_part}"


def make_text_reply_slot_key(guild_id: int, channel_id: int, thread_id: int | None = None) -> str:
    thread_part = f":thread:{thread_id}" if thread_id is not None else ""
    return f"guild:{guild_id}:reply:text:{channel_id}{thread_part}"


def make_voice_room_session_key(guild_id: int, voice_channel_id: int | None) -> str:
    channel_part = voice_channel_id if voice_channel_id is not None else "none"
    return f"guild:{guild_id}:voice:{channel_part}"


def make_voice_session_key(guild_id: int, voice_channel_id: int | None, user_id: int | None = None) -> str:
    room_session_key = make_voice_room_session_key(guild_id, voice_channel_id)
    user_part = f":user:{user_id}" if user_id is not None else ""
    return f"{room_session_key}{user_part}"


def make_room_memory_key(kind: str, room_id: int | None) -> str:
    room_part = room_id if room_id is not None else "none"
    return f"{kind}:{room_part}"


def make_person_memory_key(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return f"user:{user_id}"


def make_session_memory_key(session_key: str | None, user_id: int | None = None) -> str | None:
    if not session_key:
        return None
    if user_id is None:
        return session_key
    return f"{session_key}:user:{user_id}"


def build_text_ingress_context(
    *,
    guild_id: int,
    channel_id: int,
    user_id: int,
    thread_id: int | None = None,
) -> DiscordTextIngressContext:
    session_key = make_text_session_key(guild_id, channel_id, user_id, thread_id=thread_id)
    return DiscordTextIngressContext(
        guild_id=guild_id,
        channel_id=channel_id,
        user_id=user_id,
        thread_id=thread_id,
        session_key=session_key,
        room_key=make_room_memory_key("text", channel_id),
        person_key=make_person_memory_key(user_id),
        session_memory_key=make_session_memory_key(session_key, user_id),
        reply_slot_key=make_text_reply_slot_key(guild_id, channel_id, thread_id=thread_id),
    )


def build_voice_ingress_context(
    *,
    guild_id: int,
    voice_channel_id: int | None,
    user_id: int,
) -> DiscordVoiceIngressContext:
    room_session_key = make_voice_room_session_key(guild_id, voice_channel_id)
    session_key = make_voice_session_key(guild_id, voice_channel_id, user_id)
    return DiscordVoiceIngressContext(
        guild_id=guild_id,
        voice_channel_id=voice_channel_id,
        user_id=user_id,
        room_session_key=room_session_key,
        session_key=session_key,
        room_key=make_room_memory_key("voice", voice_channel_id),
        person_key=make_person_memory_key(user_id),
        session_memory_key=make_session_memory_key(session_key, user_id),
    )


def should_accept_text_turn(*, is_wake_word: bool, is_reply: bool, is_active_session: bool) -> bool:
    return bool(is_wake_word or is_reply or is_active_session)


def normalize_voice_debug_meta(debug_meta: dict | None) -> dict:
    return dict(debug_meta) if isinstance(debug_meta, dict) else {}


def voice_ingress_source(debug_meta: dict | None, *, default: str = "discord_voice") -> str:
    meta = normalize_voice_debug_meta(debug_meta)
    return str(meta.get("source") or default)


def build_text_turn_user_text(
    content: str | None,
    *,
    is_wake_word: bool,
    strip_wake_word: Callable[[str], str],
    empty_wake_text: str,
    attachment_context: str = "",
) -> str:
    raw_content = content or ""
    user_text = strip_wake_word(raw_content) if is_wake_word else raw_content.strip()
    if not user_text:
        user_text = empty_wake_text
    if attachment_context:
        user_text = f"{user_text}\n\n[Attached Visual Inputs]\n{attachment_context}"
    return user_text
