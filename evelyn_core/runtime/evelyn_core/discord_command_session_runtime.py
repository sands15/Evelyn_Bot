from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)


@dataclass(frozen=True)
class DiscordCommandSessionRuntimeDeps:
    resolve_text_thread_id: Callable[..., int | None]
    is_text_thread_parent: Callable[[Any], bool]
    make_text_session_key: Callable[..., str]
    start_new_turn: Callable[[str], str]
    record_command_assistant_turn: Callable[..., None]
    system_prompt: str
    max_history_items: int
    normal_ttl_sec: float
    question_ttl_sec: float
    commit_session_continuity: Callable[..., dict[str, Any]]
    log: Callable[..., Any]


class ContinuityRecordingCommandContext:
    """Delegate a Discord command context and commit each delivered text reply."""

    def __init__(
        self,
        ctx: Any,
        *,
        record_reply: Callable[[Any, str, str], None],
        log: Callable[..., Any],
    ) -> None:
        self._ctx = ctx
        self._record_reply = record_reply
        self._log = log

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    async def send(
        self,
        content: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        delivered = await self._ctx.send(content, *args, **kwargs)
        if not isinstance(content, str) or not content.strip():
            return delivered

        message = getattr(self._ctx, "message", None)
        user_text = getattr(message, "content", None)
        if not isinstance(user_text, str) or not user_text.strip():
            user_text = "[discord command]"
        try:
            self._record_reply(
                self._ctx,
                user_text,
                content,
            )
        except Exception as exc:
            self._log(
                "[DISCORD] command_continuity_record_failed "
                f"errorType={type(exc).__name__}"
            )
        return delivered


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
    turn_id = deps.start_new_turn(session_key)
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
    try:
        require_durable_continuity_receipt(
            deps.commit_session_continuity(
                session_key,
                turn_id,
            )
        )
    except Exception as exc:
        deps.log(
            "[DISCORD] command_continuity_commit_failed "
            f"session={session_key} errorType={type(exc).__name__}"
        )


__all__ = [
    "ContinuityRecordingCommandContext",
    "DiscordCommandSessionRuntimeDeps",
    "mark_text_session_from_command_runtime",
]
