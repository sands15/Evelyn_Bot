from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .text import clean_text


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


@dataclass(frozen=True)
class DiscordTextTurnDecision:
    action: str
    reason: str = ""
    user_text: str = ""

    @property
    def accepted(self) -> bool:
        return self.action == "accept"


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


def resolve_text_thread_id(channel: object, *, is_thread_parent: Callable[[Any], bool] | None = None) -> int | None:
    parent = getattr(channel, "parent", None)
    if parent is None:
        return None
    if is_thread_parent is not None and not is_thread_parent(parent):
        return None
    return getattr(channel, "id", None)


def build_text_ingress_context_from_message(
    message: object,
    *,
    is_thread_parent: Callable[[Any], bool] | None = None,
) -> DiscordTextIngressContext:
    guild = getattr(message, "guild")
    channel = getattr(message, "channel")
    author = getattr(message, "author")
    return build_text_ingress_context(
        guild_id=int(getattr(guild, "id")),
        channel_id=int(getattr(channel, "id")),
        user_id=int(getattr(author, "id")),
        thread_id=resolve_text_thread_id(channel, is_thread_parent=is_thread_parent),
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


def decide_text_message_precheck(
    *,
    content: str | None,
    prefix: str,
    channel_id: int,
    command_only_channel_ids: set[int] | list[int] | tuple[int, ...],
) -> DiscordTextTurnDecision:
    content_stripped = (content or "").lstrip()
    if prefix and content_stripped.startswith(prefix):
        return DiscordTextTurnDecision("process_commands", "command_prefix")
    if int(channel_id) in {int(item) for item in command_only_channel_ids}:
        return DiscordTextTurnDecision("ignore", "command_only_channel")
    return DiscordTextTurnDecision("continue")


def build_text_turn_decision(
    content: str | None,
    *,
    is_wake_word: bool,
    is_reply: bool,
    is_active_session: bool,
    strip_wake_word: Callable[[str], str],
    empty_wake_text: str,
    attachment_context: str = "",
) -> DiscordTextTurnDecision:
    if not should_accept_text_turn(is_wake_word=is_wake_word, is_reply=is_reply, is_active_session=is_active_session):
        return DiscordTextTurnDecision("drop", "text_gate_not_open")
    user_text = build_text_turn_user_text(
        content,
        is_wake_word=is_wake_word,
        strip_wake_word=strip_wake_word,
        empty_wake_text=empty_wake_text,
        attachment_context=attachment_context,
    )
    return DiscordTextTurnDecision("accept", user_text=user_text)


async def is_reply_to_target_user(message: object, target_user: object | None, *, log: Callable[[str], None] | None = None) -> bool:
    if target_user is None:
        return False
    reference = getattr(message, "reference", None)
    if not reference:
        return False
    message_id = getattr(reference, "message_id", None)
    if message_id is None:
        return False
    channel = getattr(message, "channel", None)
    if channel is None or not hasattr(channel, "fetch_message"):
        return False
    try:
        replied_msg = await channel.fetch_message(message_id)
        return getattr(replied_msg, "author", None) == target_user
    except Exception as exc:
        if log is not None:
            log(f"답장 확인 오류: {exc!r}")
        return False


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


def build_discord_attachment_context(message: object, *, limit: int = 4) -> str:
    attachments = list(getattr(message, "attachments", []) or [])[: max(0, limit)]
    rows: list[str] = []
    for attachment in attachments:
        content_type = clean_text(str(getattr(attachment, "content_type", "") or ""))
        filename = clean_text(str(getattr(attachment, "filename", "") or "attachment"))
        url = clean_text(str(getattr(attachment, "url", "") or ""))
        width = getattr(attachment, "width", None)
        height = getattr(attachment, "height", None)
        is_image = content_type.startswith("image/") or filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        label = "image" if is_image else "attachment"
        size_bits = []
        if width and height:
            size_bits.append(f"{width}x{height}")
        if content_type:
            size_bits.append(content_type)
        rows.append(f"- {label}: filename={filename}; meta={', '.join(size_bits) or 'unknown'}; url={url}")
    return "\n".join(rows)
