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
    session_locks: dict[str, asyncio.Lock]
    reply_slot_locks: dict[str, asyncio.Lock]
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
    proactive_resolution = deps.resolve_pending_proactive_question_for_turn(
        message.guild.id,
        user_text,
        session_key=session_key,
        session_memory_key=session_memory_key,
    )

    state_lock = deps.session_locks.setdefault(session_key, asyncio.Lock())
    reply_lock = deps.reply_slot_locks.setdefault(ingress.reply_slot_key, asyncio.Lock())

    if reply_lock.locked():
        await message.channel.send("\u23f3 지금 다른 응답을 처리 중이야. 잠깐만.")
        await deps.process_commands(message)
        return

    async with state_lock:
        started_turn = deps.begin_user_text_turn(
            session_key,
            user_text,
            guild_id=message.guild.id,
            user_id=message.author.id,
        )
        topic_id = started_turn.topic_id
        turn_id = started_turn.turn_id

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
    try:
        async with reply_lock:
            async with message.channel.typing():
                if deps.auto_join_voice:
                    vc = await deps.ensure_voice_client(message)

                answer, _sent_message, text_metrics, text_delivery_plan = await deps.stream_text_reply(
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
                )
                plain_answer = deps.strip_omnivoice_tags(answer) or answer
                text_delivered = True

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
                deps.finish_assistant_text_turn(
                    session_key,
                    user_text,
                    plain_answer,
                    guild_id=message.guild.id,
                    user_id=message.author.id,
                    awaiting_user_reply=awaiting_reply,
                    topic_id=topic_id,
                )
                try:
                    continuity_status = (
                        await deps.commit_session_continuity()
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
                                "conversation_continuity_"
                                "commit_failed"
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
                runtime_mode = (
                    (text_metrics.get("meta") or {}).get(
                        "runtime_mode"
                    )
                    or deps.compute_runtime_mode(text_metrics)
                )
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
        if not text_delivered:
            await message.channel.send(
                public_failure_message("text_turn_failed")
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
