from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .discord_ingress import (
    build_discord_attachment_context,
    build_text_ingress_context_from_message,
    build_text_turn_decision,
    decide_text_message_precheck,
    is_reply_to_target_user,
)
from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .public_error_contract import public_failure_message
from .explicit_memory_confirmation import (
    execute_explicit_memory_confirmation,
    is_explicit_memory_confirmation_command,
)
from .memory_confirmation_contract import (
    explicit_memory_writer_skip_decision,
    memory_owner_scope,
)
from .conversation_memory_receipt import (
    memory_receipt_ref_from_metrics,
    not_used_memory_receipt_ref,
)
from .turn_lifecycle import TurnScope


@dataclass(frozen=True)
class DiscordTextMessageHandlerDeps:
    process_commands: Any
    bot_user: Any
    is_thread_parent: Any
    remember_session_followup_target: Any
    get_guild_command_prefix: Any
    get_guild_command_only_channel_ids: Any
    contains_wake_word: Any
    is_session_active_for_user: Any
    strip_voice_wake_word: Any
    empty_wake_text: str
    log_turn_event: Any
    current_turn_id: Any
    resolve_pending_proactive_question_for_turn: Any
    claim_conversation_ingress: Any
    conversation_ingress_recovery_context: Any
    mark_ingress_response_ready: Any
    mark_ingress_delivery_inflight: Any
    mark_ingress_delivery_succeeded: Any
    mark_ingress_delivery_ambiguous: Any
    begin_ingress_terminal_commit: Any
    complete_ingress: Any
    session_locks: dict[str, asyncio.Lock]
    reply_slot_locks: dict[str, asyncio.Lock]
    reply_slot_admission_locks: dict[str, asyncio.Lock]
    begin_user_text_turn: Any
    replace_room_turn_scope: Any
    attach_current_task: Any
    auto_join_voice: bool
    ensure_voice_client: Any
    stream_text_reply: Any
    strip_omnivoice_tags: Any
    execute_voice_delivery_plan: Any
    detach_task: Any
    clear_room_turn_scope: Any
    session_speculative_policies: dict[str, Any]
    compute_runtime_mode: Any
    record_context_pipeline_benchmark: Any
    schedule_memory_update: Any
    should_force_search_followup: Any
    schedule_search_followup: Any
    session_state_snapshot: Any
    finish_assistant_text_turn: Any
    commit_session_continuity: Any
    log_voice_bottleneck_summary: Any
    format_display_text: Any
    log: Any


@dataclass(frozen=True)
class _PreparedDiscordTextTurn:
    entry_id: str
    turn_id: str
    topic_id: str
    state_lock: asyncio.Lock
    reply_lock: asyncio.Lock
    recovery_context: dict[str, Any] | None
    proactive_resolution: dict[str, Any] | None


class _PreAcquiredReplyLock:
    def __init__(self, lock: asyncio.Lock) -> None:
        self.lock = lock

    async def __aenter__(self) -> asyncio.Lock:
        if not self.lock.locked():
            raise RuntimeError("conversation_ingress_reply_lock_lost")
        return self.lock

    async def __aexit__(self, *_args: Any) -> bool:
        self.lock.release()
        return False


async def _prepare_durable_text_turn(
    *,
    message: Any,
    ingress: Any,
    user_text: str,
    deps: DiscordTextMessageHandlerDeps,
) -> _PreparedDiscordTextTurn | None:
    session_key = ingress.session_key
    state_lock = deps.session_locks.setdefault(
        session_key,
        asyncio.Lock(),
    )
    reply_lock = deps.reply_slot_locks.setdefault(
        ingress.reply_slot_key,
        asyncio.Lock(),
    )
    reply_admission_lock = deps.reply_slot_admission_locks.setdefault(
        ingress.reply_slot_key,
        asyncio.Lock(),
    )
    async with reply_admission_lock:
        if reply_lock.locked():
            deps.log_turn_event(
                "turn_drop",
                turn_id=deps.current_turn_id(session_key),
                segment_id=0,
                source="text",
                session_key=session_key,
                reason="conversation_ingress_reply_slot_busy",
                user_id=message.author.id,
            )
            return None
        await reply_lock.acquire()
    keep_reply_lock = False
    try:
        async with state_lock:
            prepared_turn = await _prepare_claimed_text_turn(
                message=message,
                ingress=ingress,
                user_text=user_text,
                deps=deps,
                state_lock=state_lock,
                reply_lock=reply_lock,
            )
            keep_reply_lock = prepared_turn is not None
            return prepared_turn
    finally:
        if not keep_reply_lock and reply_lock.locked():
            reply_lock.release()


async def _prepare_claimed_text_turn(
    *,
    message: Any,
    ingress: Any,
    user_text: str,
    deps: DiscordTextMessageHandlerDeps,
    state_lock: asyncio.Lock,
    reply_lock: asyncio.Lock,
) -> _PreparedDiscordTextTurn | None:
    session_key = ingress.session_key
    try:
        ingress_claim = await asyncio.to_thread(
            deps.claim_conversation_ingress,
            ingress,
            user_text,
        )
    except Exception as exc:
        deps.log_turn_event(
            "turn_drop",
            turn_id=deps.current_turn_id(session_key),
            segment_id=0,
            source="text",
            session_key=session_key,
            reason="conversation_ingress_claim_failed",
            user_id=message.author.id,
        )
        deps.log(
            "[TEXT TURN] conversation_ingress_claim_failed errorType=",
            type(exc).__name__,
        )
        return None
    if ingress_claim.get("shouldProcess") is not True:
        try:
            recovery_context = await asyncio.to_thread(
                deps.conversation_ingress_recovery_context,
                session_key,
                exclude_entry_id=str(
                    ingress_claim.get("entryId") or ""
                ),
            )
            pending_count = int(
                recovery_context.get("pendingCount", 0) or 0
            )
        except Exception:
            pending_count = 0
        deps.log_turn_event(
            "turn_drop",
            turn_id=deps.current_turn_id(session_key),
            segment_id=0,
            source="text",
            session_key=session_key,
            reason="conversation_ingress_redelivery_suppressed",
            ingress_phase=str(
                ingress_claim.get("phase") or "unknown"
            ),
            unanswered_recovery_count=pending_count,
            user_id=message.author.id,
        )
        return None
    entry_id = str(ingress_claim.get("entryId") or "")
    turn_id = str(ingress_claim.get("turnId") or "")
    if not entry_id or not turn_id:
        deps.log_turn_event(
            "turn_drop",
            turn_id=deps.current_turn_id(session_key),
            segment_id=0,
            source="text",
            session_key=session_key,
            reason="conversation_ingress_receipt_invalid",
            user_id=message.author.id,
        )
        return None
    try:
        recovery_context = await asyncio.to_thread(
            deps.conversation_ingress_recovery_context,
            session_key,
            exclude_entry_id=entry_id,
        )
    except Exception as exc:
        recovery_context = None
        deps.log(
            "[TEXT TURN] conversation_ingress_context_failed errorType=",
            type(exc).__name__,
        )
    try:
        proactive_resolution = (
            deps.resolve_pending_proactive_question_for_turn(
                message.guild.id,
                user_text,
                session_key=session_key,
                session_memory_key=ingress.session_memory_key,
            )
        )
        started_turn = deps.begin_user_text_turn(
            session_key,
            user_text,
            guild_id=message.guild.id,
            user_id=message.author.id,
            turn_id=turn_id,
        )
    except Exception as exc:
        deps.log(
            "[TEXT TURN] conversation_ingress_turn_start_failed errorType=",
            type(exc).__name__,
        )
        return None
    if started_turn.turn_id != turn_id:
        deps.log_turn_event(
            "turn_drop",
            turn_id=turn_id,
            segment_id=0,
            source="text",
            session_key=session_key,
            reason="conversation_ingress_turn_binding_mismatch",
            user_id=message.author.id,
        )
        return None
    return _PreparedDiscordTextTurn(
        entry_id=entry_id,
        turn_id=turn_id,
        topic_id=started_turn.topic_id,
        state_lock=state_lock,
        reply_lock=reply_lock,
        recovery_context=recovery_context,
        proactive_resolution=proactive_resolution,
    )


async def handle_discord_text_message(message: Any, deps: DiscordTextMessageHandlerDeps) -> None:
    if message.author.bot:
        return

    if not message.guild:
        await deps.process_commands(message)
        return

    ingress = build_text_ingress_context_from_message(
        message,
        is_thread_parent=deps.is_thread_parent,
    )
    session_key = ingress.session_key
    room_key = ingress.room_key
    person_key = ingress.person_key
    session_memory_key = ingress.session_memory_key
    deps.remember_session_followup_target(session_key, channel_id=message.channel.id, message_id=message.id)

    prefix = deps.get_guild_command_prefix(message.guild.id)
    precheck = decide_text_message_precheck(
        content=message.content,
        prefix=prefix,
        channel_id=message.channel.id,
        command_only_channel_ids=deps.get_guild_command_only_channel_ids(message.guild.id),
    )
    if precheck.action == "process_commands":
        await deps.process_commands(message)
        return
    if precheck.action == "ignore":
        return

    is_wake_word = deps.contains_wake_word(message.content)
    is_active_session = deps.is_session_active_for_user(session_key, message.author.id)
    is_reply = await is_reply_to_target_user(message, deps.bot_user, log=deps.log)

    text_turn_decision = build_text_turn_decision(
        message.content,
        is_wake_word=is_wake_word,
        is_reply=is_reply,
        is_active_session=is_active_session,
        strip_wake_word=deps.strip_voice_wake_word,
        empty_wake_text=deps.empty_wake_text,
        attachment_context=build_discord_attachment_context(message),
    )

    if not text_turn_decision.accepted:
        deps.log_turn_event(
            "turn_drop",
            turn_id=deps.current_turn_id(session_key),
            segment_id=0,
            source="text",
            session_key=session_key,
            reason=text_turn_decision.reason or "text_gate_not_open",
            user_id=message.author.id,
        )
        await deps.process_commands(message)
        return

    user_text = text_turn_decision.user_text
    prepared_turn = await _prepare_durable_text_turn(
        message=message,
        ingress=ingress,
        user_text=user_text,
        deps=deps,
    )
    if prepared_turn is None:
        return
    state_lock = prepared_turn.state_lock
    reply_lock = prepared_turn.reply_lock
    ingress_entry_id = prepared_turn.entry_id
    turn_id = prepared_turn.turn_id
    topic_id = prepared_turn.topic_id
    ingress_recovery_context = prepared_turn.recovery_context
    proactive_resolution = prepared_turn.proactive_resolution

    turn_scope = TurnScope(turn_id)
    deps.replace_room_turn_scope(session_key, turn_scope)
    turn_task = deps.attach_current_task(turn_scope)
    vc = None
    answer = ""
    plain_answer = ""
    text_metrics: dict[str, Any] = {}
    text_delivery_plan = None
    text_delivered = False
    text_turn_summary_logged = False
    memory_command_matched = False
    memory_write_receipt: dict[str, Any] | None = None
    ingress_response_bound = False
    ingress_delivery_inflight = False
    ingress_delivery_succeeded = False
    ingress_answer_text = ""
    ingress_memory_receipt_ref: Any = None
    ingress_delivery_ref = (
        f"discord_text:{message.guild.id}:"
        f"{message.channel.id}:{ingress.message_id}"
    )

    async def prepare_ingress_delivery(
        *,
        answer_text: str,
        final_text: str,
        metrics: dict[str, Any],
        response_memory_ref: Any = None,
    ) -> None:
        nonlocal text_metrics
        nonlocal ingress_response_bound
        nonlocal ingress_delivery_inflight
        nonlocal ingress_answer_text
        nonlocal ingress_memory_receipt_ref
        text_metrics = metrics
        delivered_answer = (
            str(final_text or "").strip()
            or deps.strip_omnivoice_tags(str(answer_text or "")).strip()
            or str(answer_text or "").strip()
        )
        memory_ref = (
            response_memory_ref
            if response_memory_ref is not None
            else memory_receipt_ref_from_metrics(metrics)
        )
        await asyncio.to_thread(
            deps.mark_ingress_response_ready,
            ingress_entry_id,
            assistant_text=delivered_answer,
            memory_receipt_ref=memory_ref,
        )
        ingress_response_bound = True
        ingress_answer_text = delivered_answer
        ingress_memory_receipt_ref = memory_ref
        await asyncio.to_thread(
            deps.mark_ingress_delivery_inflight,
            ingress_entry_id,
            delivery_ref=ingress_delivery_ref,
        )
        ingress_delivery_inflight = True

    async def confirm_ingress_delivery(**_kwargs: Any) -> None:
        nonlocal ingress_delivery_succeeded
        await asyncio.to_thread(
            deps.mark_ingress_delivery_succeeded,
            ingress_entry_id,
            delivery_ref=ingress_delivery_ref,
        )
        ingress_delivery_succeeded = True

    async def mark_ingress_delivery_ambiguous_if_needed() -> None:
        if not ingress_delivery_inflight or ingress_delivery_succeeded:
            return
        try:
            await asyncio.to_thread(
                deps.mark_ingress_delivery_ambiguous,
                ingress_entry_id,
            )
        except Exception as exc:
            deps.log(
                "[TEXT TURN] conversation_ingress_ambiguous_mark_failed errorType=",
                type(exc).__name__,
            )

    async def finish_and_commit_delivered_turn(
        answer_text: str,
        *,
        awaiting_reply: bool,
    ) -> bool:
        deps.finish_assistant_text_turn(
            session_key,
            user_text,
            answer_text,
            guild_id=message.guild.id,
            user_id=message.author.id,
            awaiting_user_reply=awaiting_reply,
            topic_id=topic_id,
            memory_receipt=(
                ingress_memory_receipt_ref
                if ingress_memory_receipt_ref is not None
                else memory_receipt_ref_from_metrics(text_metrics)
            ),
        )
        try:
            continuity_status = (
                await deps.commit_session_continuity(
                    session_key,
                    turn_id,
                    before_commit=lambda generation: (
                        deps.begin_ingress_terminal_commit(
                            ingress_entry_id,
                            continuity_generation=int(generation),
                            assistant_text=ingress_answer_text,
                            memory_receipt_ref=(
                                ingress_memory_receipt_ref
                            ),
                        )
                    ),
                )
            )
            continuity_receipt = (
                require_durable_continuity_receipt(
                    continuity_status
                )
            )
            text_metrics.setdefault("meta", {}).update(
                {
                    "continuity_commit": "durable",
                    "continuity_generation": int(
                        continuity_receipt["generation"]
                    ),
                }
            )
        except Exception as exc:
            text_metrics.setdefault("meta", {}).update(
                {
                    "continuity_commit": "failed",
                    "continuity_error": (
                        "conversation_continuity_commit_failed"
                    ),
                }
            )
            deps.log(
                (
                    "[TEXT TURN] "
                    "continuity_commit_failed "
                    "errorType="
                ),
                type(exc).__name__,
            )
            return False
        try:
            await asyncio.to_thread(
                deps.complete_ingress,
                ingress_entry_id,
                continuity_generation=int(
                    continuity_receipt["generation"]
                ),
                assistant_text=ingress_answer_text,
                memory_receipt_ref=ingress_memory_receipt_ref,
            )
            text_metrics.setdefault("meta", {})[
                "conversation_ingress"
            ] = "completed"
            return True
        except Exception as exc:
            text_metrics.setdefault("meta", {}).update(
                {
                    "conversation_ingress": "terminal_commit_failed",
                    "conversation_ingress_error": (
                        "conversation_ingress_terminal_commit_failed"
                    ),
                }
            )
            deps.log(
                "[TEXT TURN] conversation_ingress_terminal_commit_failed errorType=",
                type(exc).__name__,
            )
            return False

    try:
        async with _PreAcquiredReplyLock(reply_lock):
            async with message.channel.typing():
                if is_explicit_memory_confirmation_command(
                    user_text
                ):
                    (
                        memory_command_matched,
                        memory_command_reply,
                        memory_write_receipt,
                        memory_command_error,
                    ) = await asyncio.to_thread(
                        execute_explicit_memory_confirmation,
                        user_text,
                        action_id=(
                            f"discord-message:{message.guild.id}:"
                            f"{message.channel.id}:{message.id}"
                        ),
                        evidence_turn_id=turn_id,
                        source="discord-user",
                        owner_scope=memory_owner_scope(
                            guild_id=message.guild.id,
                            person_key=person_key,
                        ),
                    )
                else:
                    memory_command_reply = ""
                    memory_command_error = ""
                if memory_command_matched:
                    answer = memory_command_reply
                    plain_answer = memory_command_reply
                    text_metrics = {
                        "meta": {
                            "reply_source": (
                                "explicit_memory_confirmation"
                            ),
                            "memory_write_receipt": (
                                memory_write_receipt
                            ),
                            "memory_write_error": (
                                memory_command_error
                            ),
                        }
                    }
                    await prepare_ingress_delivery(
                        answer_text=memory_command_reply,
                        final_text=memory_command_reply,
                        metrics=text_metrics,
                        response_memory_ref=(
                            not_used_memory_receipt_ref()
                        ),
                    )
                    try:
                        await message.channel.send(memory_command_reply)
                    except Exception:
                        await mark_ingress_delivery_ambiguous_if_needed()
                        raise
                    await confirm_ingress_delivery()
                    text_delivered = ingress_delivery_succeeded
                else:
                    if deps.auto_join_voice:
                        vc = await deps.ensure_voice_client(message)
                    (
                        answer,
                        _sent_message,
                        text_metrics,
                        text_delivery_plan,
                    ) = await deps.stream_text_reply(
                        message.channel,
                        user_text,
                        guild_id=message.guild.id,
                        session_key=session_key,
                        turn_id=turn_id,
                        room_key=room_key,
                        person_key=person_key,
                        session_memory_key=session_memory_key,
                        source="text",
                        debug_text=user_text,
                        include_voice=vc is not None,
                        turn_scope=turn_scope,
                        proactive_resolution=proactive_resolution,
                        ingress_recovery_context=ingress_recovery_context,
                        before_text_delivery=prepare_ingress_delivery,
                        after_text_delivery=confirm_ingress_delivery,
                    )
                    plain_answer = ingress_answer_text
                    text_delivered = ingress_delivery_succeeded

            async with state_lock:
                deps.session_speculative_policies.pop(
                    session_key,
                    None,
                )
                awaiting_reply = bool(
                    deps.session_state_snapshot(session_key).get(
                        "awaiting_user_reply"
                    )
                )
                continuity_committed = await finish_and_commit_delivered_turn(
                    plain_answer,
                    awaiting_reply=awaiting_reply,
                )
                if not continuity_committed:
                    raise RuntimeError(
                        "conversation_continuity_commit_failed"
                    )
                runtime_mode = (
                    (text_metrics.get("meta") or {}).get(
                        "runtime_mode"
                    )
                    or deps.compute_runtime_mode(text_metrics)
                )
                if memory_command_matched:
                    memory_writer_decision = (
                        explicit_memory_writer_skip_decision()
                    )
                else:
                    deps.record_context_pipeline_benchmark(
                        metrics=text_metrics,
                        user_text=user_text,
                        answer=plain_answer,
                        source="text",
                        guild_id=message.guild.id,
                        session_key=session_key,
                    )
                    memory_writer_decision = (
                        deps.schedule_memory_update(
                            message.guild.id,
                            user_text,
                            plain_answer,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            source="text",
                            user_speaker=(
                                message.author.display_name
                            ),
                            assistant_speaker="Evelyn",
                            session_key=session_key,
                            turn_scope=turn_scope,
                            runtime_mode=runtime_mode,
                        )
                    )
                text_metrics.setdefault("meta", {})[
                    "memory_writer_decision"
                ] = memory_writer_decision
                if not memory_command_matched:
                    search_requested = (
                        deps.should_force_search_followup(
                            message.guild.id,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            source="text",
                        )
                    )
                    deps.schedule_search_followup(
                        message.guild.id,
                        session_key,
                        user_text,
                        plain_answer,
                        room_key=room_key,
                        person_key=person_key,
                        session_memory_key=session_memory_key,
                        channel_id=message.channel.id,
                        reply_to_message_id=message.id,
                        source="search-followup-text",
                        force=search_requested,
                        turn_scope=None,
                        runtime_mode=runtime_mode,
                        continuity_generation=(
                            text_metrics.get("meta", {}).get(
                                "continuity_generation"
                            )
                        ),
                    )

            if (
                vc is not None
                and text_delivery_plan is not None
                and text_delivery_plan.should_play_voice
            ):
                try:
                    await deps.execute_voice_delivery_plan(
                        vc,
                        text_delivery_plan,
                        metrics=text_metrics,
                        turn_id=(
                            turn_id
                            or deps.current_turn_id(session_key)
                        ),
                        session_key=session_key,
                        turn_scope=turn_scope,
                    )
                except Exception as exc:
                    text_metrics.setdefault("meta", {}).update(
                        {
                            "error_layer": (
                                "optional_voice_delivery"
                            ),
                            "error": (
                                "optional_voice_delivery_failed"
                            ),
                        }
                    )
                    deps.log(
                        (
                            "[TEXT TURN] "
                            "optional_voice_delivery_failed "
                            "errorType="
                        ),
                        type(exc).__name__,
                    )

        deps.log_voice_bottleneck_summary(
            text_metrics,
            label="text_turn",
            extra=f"chars={len(deps.format_display_text(answer, session_key=session_key).strip())} voice_read={str(vc is not None).lower()}",
            event_name="text_turn_summary",
        )
        text_turn_summary_logged = True

    except Exception as exc:
        deps.log("전체 오류 type=", type(exc).__name__)
        await mark_ingress_delivery_ambiguous_if_needed()
        if not text_delivered and not ingress_response_bound:
            failure_reply = public_failure_message(
                "text_turn_failed"
            )
            text_metrics.setdefault("meta", {}).update(
                {
                    "failure_reply_delivered": False,
                    "error_layer": "text_generation",
                    "error": "text_turn_failed",
                }
            )
            try:
                await prepare_ingress_delivery(
                    answer_text=failure_reply,
                    final_text=failure_reply,
                    metrics=text_metrics,
                )
                await message.channel.send(failure_reply)
                await confirm_ingress_delivery()
            except Exception as delivery_exc:
                await mark_ingress_delivery_ambiguous_if_needed()
                deps.log(
                    (
                        "[TEXT TURN] "
                        "failure_reply_delivery_failed "
                        "errorType="
                    ),
                    type(delivery_exc).__name__,
                )
            else:
                text_delivered = ingress_delivery_succeeded
                answer = failure_reply
                plain_answer = ingress_answer_text
                text_metrics.setdefault("meta", {}).update(
                    {
                        "failure_reply_delivered": True,
                    }
                )
                try:
                    async with state_lock:
                        deps.session_speculative_policies.pop(
                            session_key,
                            None,
                        )
                        await finish_and_commit_delivered_turn(
                            failure_reply,
                            awaiting_reply=False,
                        )
                except Exception as record_exc:
                    text_metrics.setdefault("meta", {}).update(
                        {
                            "continuity_commit": "failed",
                            "continuity_error": (
                                "conversation_continuity_"
                                "commit_failed"
                            ),
                        }
                    )
                    deps.log(
                        (
                            "[TEXT TURN] "
                            "failure_turn_record_failed "
                            "errorType="
                        ),
                        type(record_exc).__name__,
                    )
    finally:
        if text_metrics and not text_turn_summary_logged:
            text_metrics.setdefault("meta", {})["error_layer"] = "text_turn"
            text_metrics.setdefault("meta", {}).setdefault("error", "text_turn_aborted_before_summary")
            deps.log_voice_bottleneck_summary(
                text_metrics,
                label="text_turn",
                extra="error=true",
                event_name="text_turn_summary",
            )
        deps.detach_task(turn_scope, turn_task)
        deps.clear_room_turn_scope(session_key, turn_scope)

    await deps.process_commands(message)


__all__ = [
    "DiscordTextMessageHandlerDeps",
    "handle_discord_text_message",
]
