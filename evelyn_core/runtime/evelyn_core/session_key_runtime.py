from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .discord_ingress import (
    make_person_memory_key as make_discord_person_memory_key,
    make_room_memory_key as make_discord_room_memory_key,
    make_session_memory_key as make_discord_session_memory_key,
    make_text_reply_slot_key as make_discord_text_reply_slot_key,
    make_text_session_key as make_discord_text_session_key,
    make_voice_room_session_key as make_discord_voice_room_session_key,
    make_voice_session_key as make_discord_voice_session_key,
)
from .session_memory_state import runtime_session_key as resolve_runtime_session_key


@dataclass(frozen=True)
class SessionKeyRuntimeDeps:
    resolve_runtime_session_key: Callable[..., str | None]
    make_text_session_key_fn: Callable[..., str]
    make_text_reply_slot_key_fn: Callable[..., str]
    make_voice_room_session_key_fn: Callable[..., str]
    make_voice_session_key_fn: Callable[..., str]
    make_room_memory_key_fn: Callable[..., str]
    make_person_memory_key_fn: Callable[..., str | None]
    make_session_memory_key_fn: Callable[..., str | None]


def build_session_key_runtime_deps() -> SessionKeyRuntimeDeps:
    return SessionKeyRuntimeDeps(
        resolve_runtime_session_key=resolve_runtime_session_key,
        make_text_session_key_fn=make_discord_text_session_key,
        make_text_reply_slot_key_fn=make_discord_text_reply_slot_key,
        make_voice_room_session_key_fn=make_discord_voice_room_session_key,
        make_voice_session_key_fn=make_discord_voice_session_key,
        make_room_memory_key_fn=make_discord_room_memory_key,
        make_person_memory_key_fn=make_discord_person_memory_key,
        make_session_memory_key_fn=make_discord_session_memory_key,
    )


def runtime_session_key_from_runtime(*, session_key: str | None = None, guild_id: int | None = None, deps: SessionKeyRuntimeDeps) -> str | None:
    return deps.resolve_runtime_session_key(session_key=session_key, guild_id=guild_id)


def make_text_session_key_from_runtime(
    guild_id: int,
    channel_id: int,
    user_id: int | None = None,
    *,
    thread_id: int | None = None,
    deps: SessionKeyRuntimeDeps,
) -> str:
    return deps.make_text_session_key_fn(guild_id, channel_id, user_id, thread_id=thread_id)


def make_text_reply_slot_key_from_runtime(guild_id: int, channel_id: int, *, thread_id: int | None = None, deps: SessionKeyRuntimeDeps) -> str:
    return deps.make_text_reply_slot_key_fn(guild_id, channel_id, thread_id=thread_id)


def make_voice_room_session_key_from_runtime(guild_id: int, voice_channel_id: int | None, *, deps: SessionKeyRuntimeDeps) -> str:
    return deps.make_voice_room_session_key_fn(guild_id, voice_channel_id)


def make_voice_session_key_from_runtime(
    guild_id: int,
    voice_channel_id: int | None,
    user_id: int | None = None,
    *,
    deps: SessionKeyRuntimeDeps,
) -> str:
    return deps.make_voice_session_key_fn(guild_id, voice_channel_id, user_id)


def make_room_memory_key_from_runtime(kind: str, room_id: int | None, *, deps: SessionKeyRuntimeDeps) -> str:
    return deps.make_room_memory_key_fn(kind, room_id)


def make_person_memory_key_from_runtime(user_id: int | None, *, deps: SessionKeyRuntimeDeps) -> str | None:
    return deps.make_person_memory_key_fn(user_id)


def make_session_memory_key_from_runtime(
    session_key: str | None,
    user_id: int | None,
    *,
    deps: SessionKeyRuntimeDeps,
) -> str | None:
    return deps.make_session_memory_key_fn(session_key, user_id)


def runtime_session_key(*, session_key: str | None = None, guild_id: int | None = None, deps: SessionKeyRuntimeDeps | None = None) -> str | None:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return runtime_session_key_from_runtime(
        session_key=session_key,
        guild_id=guild_id,
        deps=runtime_deps,
    )


def make_text_session_key(guild_id: int, channel_id: int, user_id: int | None = None, *, thread_id: int | None = None, deps: SessionKeyRuntimeDeps | None = None) -> str:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_text_session_key_from_runtime(
        guild_id,
        channel_id,
        user_id,
        thread_id=thread_id,
        deps=runtime_deps,
    )


def make_text_reply_slot_key(guild_id: int, channel_id: int, *, thread_id: int | None = None, deps: SessionKeyRuntimeDeps | None = None) -> str:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_text_reply_slot_key_from_runtime(guild_id, channel_id, thread_id=thread_id, deps=runtime_deps)


def make_voice_room_session_key(guild_id: int, voice_channel_id: int | None, deps: SessionKeyRuntimeDeps | None = None) -> str:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_voice_room_session_key_from_runtime(guild_id, voice_channel_id, deps=runtime_deps)


def make_voice_session_key(guild_id: int, voice_channel_id: int | None, user_id: int | None = None, *, deps: SessionKeyRuntimeDeps | None = None) -> str:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_voice_session_key_from_runtime(guild_id, voice_channel_id, user_id, deps=runtime_deps)


def make_room_memory_key(kind: str, room_id: int | None, deps: SessionKeyRuntimeDeps | None = None) -> str:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_room_memory_key_from_runtime(kind, room_id, deps=runtime_deps)


def make_person_memory_key(user_id: int | None, *, deps: SessionKeyRuntimeDeps | None = None) -> str | None:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_person_memory_key_from_runtime(user_id, deps=runtime_deps)


def make_session_memory_key(session_key: str | None, user_id: int | None = None, *, deps: SessionKeyRuntimeDeps | None = None) -> str | None:
    runtime_deps = deps if deps is not None else build_session_key_runtime_deps()
    return make_session_memory_key_from_runtime(session_key, user_id, deps=runtime_deps)


__all__ = [
    "SessionKeyRuntimeDeps",
    "build_session_key_runtime_deps",
    "runtime_session_key_from_runtime",
    "make_text_session_key_from_runtime",
    "make_text_reply_slot_key_from_runtime",
    "make_voice_room_session_key_from_runtime",
    "make_voice_session_key_from_runtime",
    "make_room_memory_key_from_runtime",
    "make_person_memory_key_from_runtime",
    "make_session_memory_key_from_runtime",
    "runtime_session_key",
    "make_text_session_key",
    "make_text_reply_slot_key",
    "make_voice_room_session_key",
    "make_voice_session_key",
    "make_room_memory_key",
    "make_person_memory_key",
    "make_session_memory_key",
]
