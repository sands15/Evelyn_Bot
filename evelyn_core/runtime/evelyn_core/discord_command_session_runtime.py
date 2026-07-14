from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DiscordCommandSessionRuntimeDeps:
    resolve_text_thread_id: Callable[..., int | None]
    is_text_thread_parent: Callable[[Any], bool]
    make_text_session_key: Callable[..., str]
    record_command_assistant_turn: Callable[..., None]
    system_prompt: str
    max_history_items: int
    normal_ttl_sec: float
    question_ttl_sec: float


def mark_text_session_from_command_runtime(
    ctx: Any,
    user_text: str,
    answer_text: str,
    *,
    awaiting_user_reply: bool = False,
    deps: DiscordCommandSessionRuntimeDeps,
) -> None:
    if ctx.guild is None:
        return

    thread_id = deps.resolve_text_thread_id(
        ctx.channel,
        is_thread_parent=deps.is_text_thread_parent,
    )
    session_key = deps.make_text_session_key(
        ctx.guild.id,
        ctx.channel.id,
        ctx.author.id,
        thread_id=thread_id,
    )
    deps.record_command_assistant_turn(
        session_key,
        user_text,
        answer_text,
        system_prompt=deps.system_prompt,
        max_history_items=deps.max_history_items,
        guild_id=ctx.guild.id,
        user_id=ctx.author.id,
        channel_id=ctx.channel.id,
        message_id=getattr(ctx.message, "id", None),
        awaiting_user_reply=awaiting_user_reply,
        normal_ttl_sec=deps.normal_ttl_sec,
        question_ttl_sec=deps.question_ttl_sec,
    )


__all__ = [
    "DiscordCommandSessionRuntimeDeps",
    "mark_text_session_from_command_runtime",
]
