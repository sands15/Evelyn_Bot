from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from .continuity_commit_contract import (
    await_continuity_commit_without_early_unlock,
    require_durable_continuity_receipt,
)
from .conversation_memory_receipt import (
    not_used_memory_receipt_ref,
)
from .discord_delivery import (
    is_definitive_discord_send_failure,
)


@dataclass(frozen=True)
class DiscordCommandSessionRuntimeDeps:
    resolve_text_thread_id: Callable[..., int | None]
    is_text_thread_parent: Callable[[Any], bool]
    make_text_session_key: Callable[..., str]
    start_new_turn: Callable[..., str]
    record_command_assistant_turn: Callable[..., None]
    system_prompt: str
    max_history_items: int
    normal_ttl_sec: float
    question_ttl_sec: float
    commit_session_continuity: Callable[..., dict[str, Any]]
    log: Callable[..., Any]
    conversation_ingress: Any | None = None


class ContinuityRecordingCommandContext:
    """Delegate a Discord command context and commit each delivered text reply."""

    def __init__(
        self,
        ctx: Any,
        *,
        record_reply: Callable[..., Any],
        log: Callable[..., Any],
        runtime_deps: DiscordCommandSessionRuntimeDeps | None = None,
    ) -> None:
        self._ctx = ctx
        self._record_reply = record_reply
        self._log = log
        self._runtime_deps = runtime_deps
        self._reply_ordinal = 0
        self._guild_epoch: int | None = None
        self.refresh_ingress_epoch()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ctx, name)

    def refresh_ingress_epoch(self) -> None:
        """Explicitly rebind this wrapper after a successful guild reset."""

        deps = self._runtime_deps
        guild = getattr(self._ctx, "guild", None)
        ingress = (
            getattr(deps, "conversation_ingress", None)
            if deps is not None
            else None
        )
        if guild is None or ingress is None:
            self._guild_epoch = None
            return
        try:
            epoch = ingress.guild_epoch(int(guild.id))
        except Exception:
            self._guild_epoch = None
            return
        self._guild_epoch = (
            epoch
            if type(epoch) is int and epoch >= 0
            else None
        )

    def _next_reply_ordinal(self) -> int:
        ordinal = self._reply_ordinal
        self._reply_ordinal += 1
        return ordinal

    def _command_user_text(self) -> str:
        message = getattr(self._ctx, "message", None)
        user_text = getattr(message, "content", None)
        if not isinstance(user_text, str) or not user_text.strip():
            return "[discord command]"
        return user_text

    def _command_session_key(
        self,
        deps: DiscordCommandSessionRuntimeDeps,
    ) -> str:
        ctx = self._ctx
        thread_id = deps.resolve_text_thread_id(
            ctx.channel,
            is_thread_parent=deps.is_text_thread_parent,
        )
        return deps.make_text_session_key(
            ctx.guild.id,
            ctx.channel.id,
            ctx.author.id,
            thread_id=thread_id,
        )

    def _fixed_log(self, event: str, exc: BaseException) -> None:
        self._log(
            f"[DISCORD] {event} errorType={type(exc).__name__}"
        )

    def _record_delivered_text(self, content: Any) -> None:
        if not isinstance(content, str) or not content.strip():
            return

        try:
            self._record_reply(
                self._ctx,
                self._command_user_text(),
                content,
            )
        except Exception as exc:
            self._log(
                "[DISCORD] command_continuity_record_failed "
                f"errorType={type(exc).__name__}"
            )

    async def _send(
        self,
        content: Any = None,
        *args: Any,
        after_physical_delivery: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> tuple[Any, bool]:
        ordinal = self._next_reply_ordinal()
        deps = self._runtime_deps
        ingress = (
            getattr(deps, "conversation_ingress", None)
            if deps is not None
            else None
        )
        guild = getattr(self._ctx, "guild", None)
        if (
            deps is None
            or ingress is None
            or guild is None
            or not isinstance(content, str)
            or not content.strip()
        ):
            async def complete_fallback_delivery() -> tuple[Any, bool]:
                delivered = await self._ctx.send(
                    content,
                    *args,
                    **kwargs,
                )
                try:
                    if after_physical_delivery is not None:
                        after_physical_delivery()
                finally:
                    self._record_delivered_text(content)
                return delivered, True

            return await await_continuity_commit_without_early_unlock(
                complete_fallback_delivery()
            )

        message = getattr(self._ctx, "message", None)
        message_id = getattr(message, "id", None)
        source_delivery_id = f"command:{message_id}:{ordinal}"
        memory_ref = not_used_memory_receipt_ref()
        guild_id = int(guild.id)
        expected_epoch = self._guild_epoch
        try:
            if expected_epoch is None:
                raise RuntimeError(
                    "conversation_ingress_epoch_not_current"
                )
            session_key = self._command_session_key(deps)
            claim = ingress.claim_discord_command(
                guild_id=guild_id,
                expected_guild_epoch=expected_epoch,
                scope=session_key,
                source_delivery_id=source_delivery_id,
                accepted_text=self._command_user_text(),
            )
            if claim.get("shouldProcess") is not True:
                return None, False
            if claim.get("guildEpoch") != expected_epoch:
                raise RuntimeError(
                    "conversation_ingress_epoch_not_current"
                )
            entry_id = str(claim["entryId"])
            turn_id = str(claim["turnId"])
            binding = ingress.bind_response(
                entry_id,
                guild_id=guild_id,
                expected_guild_epoch=expected_epoch,
                assistant_text=content,
                memory_receipt_ref=memory_ref,
            )
            assistant_hash = str(binding["assistantHash"])
            ingress.mark_delivery_inflight(
                entry_id,
                guild_id=guild_id,
                expected_guild_epoch=expected_epoch,
                delivery_ref=source_delivery_id,
            )
        except Exception as exc:
            self._fixed_log("command_delivery_admission_failed", exc)
            return None, False

        async def complete_journaled_delivery() -> tuple[Any, bool]:
            try:
                delivered = await self._ctx.send(
                    content,
                    *args,
                    **kwargs,
                )
            except asyncio.CancelledError as exc:
                try:
                    ingress.mark_delivery_ambiguous(
                        entry_id,
                        guild_id=guild_id,
                        expected_guild_epoch=expected_epoch,
                    )
                except Exception as recovery_exc:
                    self._fixed_log(
                        "command_delivery_ambiguous_record_failed",
                        recovery_exc,
                    )
                self._fixed_log("command_delivery_ambiguous", exc)
                raise
            except Exception as exc:
                definitive = is_definitive_discord_send_failure(exc)
                recovery_succeeded = False
                try:
                    error_code = (
                        "conversation_ingress_delivery_failed"
                        if definitive
                        else "conversation_ingress_delivery_ambiguous"
                    )
                    ingress.mark_delivery_ambiguous(
                        entry_id,
                        guild_id=guild_id,
                        expected_guild_epoch=expected_epoch,
                        error_code=error_code,
                    )
                    if definitive:
                        ingress.discard_ambiguous(
                            entry_id,
                            guild_id=guild_id,
                            expected_guild_epoch=expected_epoch,
                            assistant_hash=assistant_hash,
                            delivery_ref=source_delivery_id,
                            error_code=error_code,
                        )
                    recovery_succeeded = True
                except Exception as recovery_exc:
                    self._fixed_log(
                        "command_delivery_ambiguous_record_failed",
                        recovery_exc,
                    )
                if definitive and recovery_succeeded:
                    raise
                self._fixed_log("command_delivery_ambiguous", exc)
                return None, False

            continuity_error: Exception | None = None
            try:
                ingress.mark_delivery_succeeded(
                    entry_id,
                    guild_id=guild_id,
                    expected_guild_epoch=expected_epoch,
                    delivery_ref=source_delivery_id,
                )
            except Exception as exc:
                continuity_error = exc

            hook_error: BaseException | None = None
            try:
                if after_physical_delivery is not None:
                    after_physical_delivery()
            except BaseException as exc:
                hook_error = exc

            try:
                if continuity_error is not None:
                    raise continuity_error
                continuity_receipt = self._record_reply(
                    self._ctx,
                    self._command_user_text(),
                    content,
                    turn_id=turn_id,
                    before_commit=lambda generation: (
                        ingress.begin_terminal_commit(
                            entry_id,
                            guild_id=guild_id,
                            expected_guild_epoch=expected_epoch,
                            continuity_generation=generation,
                            assistant_text=content,
                            memory_receipt_ref=memory_ref,
                        )
                    ),
                )
                generation = continuity_receipt["generation"]
                if type(generation) is not int or generation < 1:
                    raise RuntimeError(
                        "conversation_continuity_commit_failed"
                    )
                ingress.complete(
                    entry_id,
                    guild_id=guild_id,
                    expected_guild_epoch=expected_epoch,
                    continuity_generation=generation,
                    assistant_text=content,
                    memory_receipt_ref=memory_ref,
                )
            except Exception as exc:
                self._fixed_log("sent_but_continuity_pending", exc)
            if hook_error is not None:
                raise hook_error
            return delivered, True

        return await await_continuity_commit_without_early_unlock(
            complete_journaled_delivery()
        )

    async def send(
        self,
        content: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        delivered, _ = await self._send(
            content,
            *args,
            **kwargs,
        )
        return delivered

    async def send_with_post_delivery_hook(
        self,
        content: Any,
        *,
        after_delivery: Callable[[], Any],
    ) -> Any:
        delivered, _ = await self._send(
            content,
            after_physical_delivery=after_delivery,
        )
        return delivered


def mark_text_session_from_command_runtime(
    ctx: Any,
    user_text: str,
    answer_text: str,
    *,
    awaiting_user_reply: bool = False,
    turn_id: str | None = None,
    before_commit: Callable[[int], Any] | None = None,
    deps: DiscordCommandSessionRuntimeDeps,
) -> dict[str, Any] | None:
    if ctx.guild is None:
        return None

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
    if turn_id is None:
        active_turn_id = deps.start_new_turn(session_key)
    else:
        active_turn_id = deps.start_new_turn(
            session_key,
            turn_id=turn_id,
        )
        if active_turn_id != turn_id:
            raise RuntimeError(
                "conversation_ingress_turn_binding_mismatch"
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
    if before_commit is None:
        status = deps.commit_session_continuity(
            session_key,
            active_turn_id,
        )
    else:
        status = deps.commit_session_continuity(
            session_key,
            active_turn_id,
            before_commit=before_commit,
        )
    return require_durable_continuity_receipt(status)


__all__ = [
    "ContinuityRecordingCommandContext",
    "DiscordCommandSessionRuntimeDeps",
    "mark_text_session_from_command_runtime",
]
