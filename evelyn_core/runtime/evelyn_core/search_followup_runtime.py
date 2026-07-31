from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .search_followup_recovery import content_sha256
from .text import clean_text, is_similar, strip_omnivoice_tags
from .search_query_context import build_search_query_from_context


@dataclass(frozen=True)
class SearchFollowupRuntimeDeps:
    bot: Any
    discord_object_factory: Callable[..., Any]
    session_followup_targets: dict[str, dict[str, int]]
    background_search_tasks: dict[str, asyncio.Task]
    inflight_search_tasks: dict[str, asyncio.Task]
    apply_runtime_mode: Callable[[str], dict[str, Any]]
    parse_response_action_tag: Callable[[str], tuple[Any, str]]
    answer_promises_search: Callable[[str], bool]
    build_search_query: Callable[..., str]
    runtime_session_key: Callable[..., str | None]
    remember_session_followup_target: Callable[..., Any]
    search_duckduckgo: Callable[[str], Awaitable[list[dict[str, Any]]]]
    answer_from_search_results: Callable[[str, list[dict[str, Any]]], Awaitable[str]]
    resolve_open_question_rows: Callable[..., int]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    memory_summary_path: Callable[[int], Any]
    read_text_file: Callable[[Any], str]
    compact_working_summary: Callable[[str], str]
    write_json_file: Callable[[Any, Any], Any]
    cognitive_state_path: Callable[..., Any]
    send_discord_text: Callable[..., Awaitable[Any]]
    format_display_text: Callable[..., str]
    speak_answer: Callable[..., Awaitable[Any]]
    current_turn_id: Callable[[str | None], Any]
    append_history: Callable[..., Any]
    schedule_memory_update: Callable[..., Any]
    create_turn_scoped_task: Callable[..., asyncio.Task]
    attach_current_task: Callable[[Any], asyncio.Task | None]
    detach_task: Callable[[Any, asyncio.Task | None], Any]
    record_search_followup_queued: Callable[[], Any]
    commit_session_continuity: Callable[[], Awaitable[dict[str, Any]]]
    search_followup_recovery: Any | None = None
    continuity_status: Callable[[], dict[str, Any]] | None = None
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    log: Callable[..., Any] = print


def build_search_query_from_runtime(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    deps: SearchFollowupRuntimeDeps,
) -> str:
    context_messages = list(messages or [])
    if not context_messages and session_key is not None:
        context_messages = list(deps.get_conversation_history(session_key=session_key, guild_id=guild_id))
    summary = ""
    if guild_id is not None:
        summary = deps.compact_working_summary(deps.read_text_file(deps.memory_summary_path(guild_id)))
    return build_search_query_from_context(
        user_text,
        messages=context_messages,
        memory_summary=summary,
        has_memory_scope=guild_id is not None,
    )


async def deliver_proactive_followup_from_runtime(
    guild_id: int,
    query: str,
    answer: str,
    *,
    deps: SearchFollowupRuntimeDeps,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    reply_to_message_id: int | None = None,
    source: str,
    turn_scope: Any | None = None,
    runtime_mode: str | None = None,
    recovery_intent_id: str | None = None,
) -> bool:
    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    plain_answer = strip_omnivoice_tags(answer) or answer
    guild = deps.bot.get_guild(guild_id)
    target_channel_id = channel_id
    stored_target = deps.session_followup_targets.get(session_key, {}) if session_key is not None else {}
    if target_channel_id is None and session_key is not None:
        target_channel_id = stored_target.get("channel_id")
    reply_target_id = reply_to_message_id if reply_to_message_id is not None else stored_target.get("message_id")
    prepared = False
    display_answer = deps.format_display_text(
        answer,
        session_key=session_key,
    )

    async def prepare_durable_followup() -> None:
        nonlocal prepared
        if prepared:
            return
        if recovery_intent_id is not None:
            recovery = deps.search_followup_recovery
            if recovery is None:
                raise RuntimeError(
                    "search_followup_recovery_unavailable"
                )
            recovery.begin_delivery_prepare(
                recovery_intent_id,
                answer=plain_answer,
                display_text=display_answer,
            )
        deps.append_history(session_key, query, plain_answer, guild_id=guild_id)
        try:
            continuity_status = (
                await deps.commit_session_continuity()
            )
            continuity_receipt = require_durable_continuity_receipt(
                continuity_status
            )
        except Exception as exc:
            deps.log(
                "[SEARCH] followup_continuity_commit_failed "
                f"guild={guild_id} session={session_key} "
                f"errorType={type(exc).__name__}"
            )
            raise
        deps.schedule_memory_update(
            guild_id,
            query,
            plain_answer,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            user_speaker="search_task",
            assistant_speaker="Evelyn",
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
        )
        if recovery_intent_id is not None:
            recovery.mark_delivery_ready(
                recovery_intent_id,
                answer=plain_answer,
                display_text=display_answer,
                continuity_generation=int(
                    continuity_receipt["generation"]
                ),
            )
        prepared = True

    if recovery_intent_id is not None:
        await prepare_durable_followup()

    if target_channel_id is not None:
        channel = deps.bot.get_channel(target_channel_id)
        if channel is None:
            try:
                channel = await deps.bot.fetch_channel(target_channel_id)
            except Exception:
                channel = None
        if channel is not None and hasattr(channel, "send"):
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            if recovery_intent_id is not None:
                deps.search_followup_recovery.mark_delivery_attempted(
                    recovery_intent_id
                )
            await deps.send_discord_text(
                channel,
                display_answer,
                reference_message_id=reply_target_id,
                reference_factory=lambda message_id: deps.discord_object_factory(id=message_id),
            )
            if recovery_intent_id is not None:
                deps.search_followup_recovery.complete(
                    recovery_intent_id
                )
            else:
                await prepare_durable_followup()

    vc = guild.voice_client if guild else None
    if vc is not None and vc.is_connected():
        try:
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            if recovery_intent_id is not None and deps.search_followup_recovery.is_active(
                recovery_intent_id
            ):
                deps.search_followup_recovery.mark_delivery_attempted(
                    recovery_intent_id
                )
            await deps.speak_answer(
                vc,
                answer,
                turn_id=deps.current_turn_id(session_key),
                session_key=session_key,
                turn_scope=turn_scope,
            )
            if recovery_intent_id is not None:
                deps.search_followup_recovery.complete(
                    recovery_intent_id
                )
            else:
                await prepare_durable_followup()
        except Exception as e:
            deps.log(
                "[SEARCH] proactive TTS 실패 "
                f"errorType={type(e).__name__}"
            )

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    if recovery_intent_id is None:
        return prepared
    return not deps.search_followup_recovery.is_active(
        recovery_intent_id
    )


def normalize_search_key(session_key: str, query: str) -> str:
    return f"{session_key}:{clean_text(query).lower()}"


def _hashed_history_pairs(
    history: list[dict[str, Any]],
    *,
    user_hash: str,
    assistant_hash: str,
) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for index in range(max(0, len(history) - 1)):
        user_item = history[index]
        assistant_item = history[index + 1]
        if not isinstance(user_item, dict) or not isinstance(
            assistant_item,
            dict,
        ):
            continue
        if user_item.get("role") != "user" or assistant_item.get(
            "role"
        ) != "assistant":
            continue
        user_text = clean_text(user_item.get("content"))
        assistant_text = clean_text(assistant_item.get("content"))
        if (
            content_sha256(user_text) == user_hash
            and content_sha256(assistant_text) == assistant_hash
        ):
            matches.append((user_text, assistant_text))
    return matches


def _find_hashed_history_pair(
    history: list[dict[str, Any]],
    *,
    user_hash: str,
    assistant_hash: str,
) -> tuple[str, str] | None:
    matches = _hashed_history_pairs(
        history,
        user_hash=user_hash,
        assistant_hash=assistant_hash,
    )
    return matches[0] if len(matches) == 1 else None


def _continuity_can_recover(
    entry: dict[str, Any],
    status: dict[str, Any],
) -> bool:
    if not isinstance(status, dict) or not bool(
        status.get("rollbackProtected")
    ):
        return False
    generation = status.get("checkpointGeneration")
    if isinstance(generation, bool) or not isinstance(generation, int):
        return False
    required = max(
        int(entry.get("continuityGeneration") or 0),
        int(entry.get("deliveryGeneration") or 0),
    )
    return required >= 1 and generation >= required


async def _channel_contains_followup(
    channel: Any,
    display_text: str,
    *,
    bot_user_id: int | None,
    after_message_id: int | None,
    discord_object_factory: Callable[..., Any],
) -> bool | None:
    history_method = getattr(channel, "history", None)
    if not callable(history_method) or bot_user_id is None:
        return None
    kwargs: dict[str, Any] = {"limit": 50}
    if after_message_id is not None:
        kwargs["after"] = discord_object_factory(
            id=after_message_id
        )
    try:
        iterator = history_method(**kwargs)
    except TypeError:
        iterator = history_method(limit=50)
    seen = 0
    async for message in iterator:
        seen += 1
        author_id = getattr(getattr(message, "author", None), "id", None)
        if author_id == bot_user_id and str(
            getattr(message, "content", "")
        ) == display_text:
            return True
    if after_message_id is None or seen >= 50:
        return None
    return False


async def recover_search_followups_from_runtime(
    *,
    deps: SearchFollowupRuntimeDeps,
) -> dict[str, int]:
    recovery = deps.search_followup_recovery
    counts = {
        "pending": 0,
        "resumed": 0,
        "verified": 0,
        "redelivered": 0,
        "uncertain": 0,
    }
    if recovery is None or deps.continuity_status is None:
        return counts
    entries = recovery.pending()
    counts["pending"] = len(entries)
    continuity_status = deps.continuity_status()
    for entry in entries:
        intent_id = str(entry["intentId"])
        session_key = str(entry["sessionKey"])
        guild_id = int(entry["guildId"])
        delivery_claimed = False
        try:
            if entry["phase"] == "request_unrecoverable":
                counts["uncertain"] += 1
                continue
            if not _continuity_can_recover(
                entry,
                continuity_status,
            ):
                recovery.mark_delivery_uncertain(
                    intent_id,
                    error_code="search_followup_continuity_unavailable",
                )
                counts["uncertain"] += 1
                continue
            history = list(
                deps.get_conversation_history(
                    session_key=session_key,
                    guild_id=guild_id,
                )
            )
            if entry["phase"] in {
                "running",
                "delivery_preparing",
            }:
                delivery_matches: list[tuple[str, str]] = []
                if entry["phase"] == "delivery_preparing":
                    delivery_matches = _hashed_history_pairs(
                        history,
                        user_hash=str(entry["queryHash"]),
                        assistant_hash=str(entry["answerHash"]),
                    )
                    if len(delivery_matches) > 1:
                        raise RuntimeError(
                            "search_followup_delivery_context_ambiguous"
                        )
                if delivery_matches:
                    if int(
                        continuity_status[
                            "checkpointGeneration"
                        ]
                    ) <= int(entry["continuityGeneration"]):
                        raise RuntimeError(
                            "search_followup_delivery_not_durable"
                        )
                    _query, answer = delivery_matches[0]
                    display_text = deps.format_display_text(
                        answer,
                        session_key=session_key,
                    )
                    if content_sha256(
                        display_text,
                        normalized=False,
                    ) != entry.get("displayHash"):
                        raise RuntimeError(
                            "search_followup_display_reconstruction_failed"
                        )
                    recovery.mark_delivery_ready(
                        intent_id,
                        answer=answer,
                        display_text=display_text,
                        continuity_generation=int(
                            continuity_status[
                                "checkpointGeneration"
                            ]
                        ),
                    )
                    entry = {
                        **entry,
                        "phase": "delivery_ready",
                        "deliveryGeneration": int(
                            continuity_status[
                                "checkpointGeneration"
                            ]
                        ),
                    }
                else:
                    pair = _find_hashed_history_pair(
                        history,
                        user_hash=str(entry["requestUserHash"]),
                        assistant_hash=str(entry["requestAnswerHash"]),
                    )
                    if pair is None:
                        raise RuntimeError(
                            "search_followup_request_context_ambiguous"
                        )
                    user_text, _request_answer = pair
                    query = deps.build_search_query(
                        guild_id,
                        user_text,
                        session_key=session_key,
                    )
                    if content_sha256(query) != entry["queryHash"]:
                        raise RuntimeError(
                            "search_followup_query_reconstruction_failed"
                        )
                    task = schedule_search_followup_singleflight_from_runtime(
                        guild_id,
                        query,
                        deps=deps,
                        session_key=session_key,
                        room_key=entry.get("roomKey"),
                        person_key=entry.get("personKey"),
                        session_memory_key=entry.get("sessionMemoryKey"),
                        channel_id=entry.get("channelId"),
                        reply_to_message_id=entry.get("replyToMessageId"),
                        source=f"search-followup-recovery-{entry['source']}",
                        recovery_intent_id=intent_id,
                    )
                    deps.background_search_tasks[session_key] = task
                    counts["resumed"] += 1
                    continue

            pair = _find_hashed_history_pair(
                history,
                user_hash=str(entry["queryHash"]),
                assistant_hash=str(entry["answerHash"]),
            )
            if pair is None:
                raise RuntimeError(
                    "search_followup_delivery_context_ambiguous"
                )
            _query, answer = pair
            display_text = deps.format_display_text(
                answer,
                session_key=session_key,
            )
            if content_sha256(
                display_text,
                normalized=False,
            ) != entry.get("displayHash"):
                raise RuntimeError(
                    "search_followup_display_reconstruction_failed"
                )
            if not recovery.claim_recovery(intent_id):
                continue
            delivery_claimed = True
            target = deps.session_followup_targets.get(
                session_key,
                {},
            )
            channel_id = entry.get("channelId") or target.get(
                "channel_id"
            )
            reply_to_message_id = entry.get(
                "replyToMessageId"
            ) or target.get("message_id")
            channel = None
            if channel_id is not None:
                channel = deps.bot.get_channel(channel_id)
                if channel is None:
                    channel = await deps.bot.fetch_channel(channel_id)
            if channel is not None and hasattr(channel, "send"):
                bot_user_id = getattr(
                    getattr(deps.bot, "user", None),
                    "id",
                    None,
                )
                if entry["phase"] != "delivery_ready":
                    delivery_exists = await _channel_contains_followup(
                        channel,
                        display_text,
                        bot_user_id=bot_user_id,
                        after_message_id=reply_to_message_id,
                        discord_object_factory=deps.discord_object_factory,
                    )
                    if delivery_exists is True:
                        recovery.complete(intent_id)
                        counts["verified"] += 1
                        continue
                    if delivery_exists is None:
                        recovery.mark_delivery_uncertain(
                            intent_id,
                            error_code=(
                                "search_followup_delivery_history_inconclusive"
                            ),
                        )
                        recovery.release_recovery_claim(
                            intent_id
                        )
                        counts["uncertain"] += 1
                        continue
                recovery.mark_delivery_attempted(intent_id)
                await deps.send_discord_text(
                    channel,
                    display_text,
                    reference_message_id=reply_to_message_id,
                    reference_factory=lambda message_id: deps.discord_object_factory(
                        id=message_id
                    ),
                )
                recovery.complete(intent_id)
                counts["redelivered"] += 1
                continue

            guild = deps.bot.get_guild(guild_id)
            voice_client = getattr(guild, "voice_client", None)
            if (
                entry["phase"] == "delivery_ready"
                and entry["source"] == "voice"
                and voice_client is not None
                and voice_client.is_connected()
            ):
                recovery.mark_delivery_attempted(intent_id)
                await deps.speak_answer(
                    voice_client,
                    answer,
                    turn_id=entry.get("turnId"),
                    session_key=session_key,
                    turn_scope=None,
                )
                recovery.complete(intent_id)
                counts["redelivered"] += 1
                continue
            recovery.mark_delivery_uncertain(
                intent_id,
                error_code="search_followup_delivery_unverifiable",
            )
            counts["uncertain"] += 1
            recovery.release_recovery_claim(intent_id)
        except Exception as exc:
            if delivery_claimed:
                recovery.release_recovery_claim(intent_id)
            recovery.mark_delivery_uncertain(
                intent_id,
                error_code="search_followup_recovery_failed",
            )
            counts["uncertain"] += 1
            deps.log(
                "[SEARCH] recovery_failed "
                f"guild={guild_id} session={session_key} "
                f"errorType={type(exc).__name__}"
            )
    return counts


def schedule_search_followup_singleflight_from_runtime(
    guild_id: int,
    query: str,
    *,
    deps: SearchFollowupRuntimeDeps,
    session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    reply_to_message_id: int | None,
    source: str,
    turn_scope: Any | None = None,
    runtime_mode: str | None = None,
    recovery_intent_id: str | None = None,
) -> asyncio.Task:
    search_key = normalize_search_key(session_key, query)
    existing = deps.inflight_search_tasks.get(search_key)
    if existing is not None and not existing.done():
        return existing
    task = deps.create_turn_scoped_task(
        run_search_followup_from_runtime(
            guild_id,
            query,
            deps=deps,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
            search_key=search_key,
            recovery_intent_id=recovery_intent_id,
        ),
        turn_scope=turn_scope,
    )
    deps.inflight_search_tasks[search_key] = task
    return task


async def run_search_followup_from_runtime(
    guild_id: int,
    query: str,
    *,
    deps: SearchFollowupRuntimeDeps,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    reply_to_message_id: int | None = None,
    source: str,
    turn_scope: Any | None = None,
    runtime_mode: str | None = None,
    search_key: str | None = None,
    recovery_intent_id: str | None = None,
) -> None:
    task = deps.attach_current_task(turn_scope)
    try:
        answer = ""
        starting_attempt = (
            deps.search_followup_recovery.attempt_count(
                recovery_intent_id
            )
            if recovery_intent_id is not None
            else 0
        )
        remaining_attempts = max(0, 3 - starting_attempt)
        last_error: Exception | None = (
            RuntimeError("search_followup_attempt_budget_exhausted")
            if remaining_attempts == 0
            else None
        )
        for attempt_offset in range(remaining_attempts):
            try:
                if recovery_intent_id is not None and not deps.search_followup_recovery.is_active(
                    recovery_intent_id
                ):
                    return
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                results = await deps.search_duckduckgo(query)
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                answer = await deps.answer_from_search_results(query, results)
                last_error = None
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if recovery_intent_id is not None:
                    deps.search_followup_recovery.record_attempt_failure(
                        recovery_intent_id,
                        error_code="search_followup_execution_failed",
                    )
                deps.log(
                    "[SEARCH] followup_execution_failed "
                    f"guild={guild_id} session={session_key} "
                    f"attempt={starting_attempt + attempt_offset + 1} "
                    f"errorType={type(exc).__name__}"
                )
                if attempt_offset + 1 < remaining_attempts:
                    await deps.sleep(float(attempt_offset + 1))
        if last_error is not None:
            answer = (
                "검색을 세 번 시도했지만 마치지 못했어. "
                "잠시 뒤 다시 요청해 줘."
            )
        if recovery_intent_id is not None and not deps.search_followup_recovery.is_active(
            recovery_intent_id
        ):
            return
        try:
            removed = deps.resolve_open_question_rows(guild_id, query, answer)
            if room_key:
                removed += deps.resolve_open_question_rows(guild_id, query, answer, scope_type="room", scope_key=room_key)
            if person_key:
                removed += deps.resolve_open_question_rows(guild_id, query, answer, scope_type="person", scope_key=person_key)
            if session_memory_key:
                removed += deps.resolve_open_question_rows(guild_id, query, answer, scope_type="session", scope_key=session_memory_key)
            if removed:
                deps.log(f"[SEARCH] resolved_open_questions guild={guild_id} removed={removed}")
        except Exception as exc:
            deps.log(
                "[SEARCH] open_question_resolution_failed "
                f"guild={guild_id} errorType={type(exc).__name__}"
            )
        completed_state = {
            "action": "answer",
            "confidence": 0.0 if last_error is not None else 1.0,
            "user_intent": clean_text(query),
            "state_summary": (
                "검색 시도가 실패해 사용자에게 실패 상태를 전달할 준비를 했다."
                if last_error is not None
                else "검색을 마쳤고 결과를 사용자에게 전달할 준비를 했다."
            ),
            "question_for_user": "",
            "main_prompt_hint": "찾은 내용을 바로 전달해라.",
            "reason_brief": (
                "search_failed"
                if last_error is not None
                else "search_completed"
            ),
            "retrieved_context_ids": [],
            "updated_at": int(time.time()),
        }
        try:
            deps.write_json_file(deps.cognitive_state_path(guild_id), completed_state)
            if room_key:
                deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type="room", scope_key=room_key), completed_state)
            if person_key:
                deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type="person", scope_key=person_key), completed_state)
            if session_memory_key:
                deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key), completed_state)
        except Exception as exc:
            deps.log(
                "[SEARCH] cognitive_state_update_failed "
                f"guild={guild_id} errorType={type(exc).__name__}"
            )
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        await deliver_proactive_followup_from_runtime(
            guild_id,
            query,
            answer,
            deps=deps,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
            recovery_intent_id=recovery_intent_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        deps.log(
            "[SEARCH] followup_failed "
            f"guild={guild_id} session={session_key} "
            f"errorType={type(e).__name__}"
        )
    finally:
        task_key = deps.runtime_session_key(session_key=session_key, guild_id=guild_id)
        task_ref = deps.background_search_tasks.get(task_key) if task_key is not None else None
        if task_ref is asyncio.current_task() and task_key is not None:
            deps.background_search_tasks.pop(task_key, None)
        if search_key:
            inflight = deps.inflight_search_tasks.get(search_key)
            if inflight is asyncio.current_task():
                deps.inflight_search_tasks.pop(search_key, None)
        deps.detach_task(turn_scope, task)


def schedule_search_followup_from_runtime(
    guild_id: int,
    session_key: str | None,
    user_text: str,
    answer: str,
    *,
    deps: SearchFollowupRuntimeDeps,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    channel_id: int | None,
    reply_to_message_id: int | None = None,
    source: str,
    force: bool = False,
    turn_scope: Any | None = None,
    runtime_mode: str | None = None,
    continuity_generation: int | None = None,
) -> None:
    if not guild_id:
        return
    opts = deps.apply_runtime_mode(runtime_mode or "normal")
    tagged_action, stripped_answer = deps.parse_response_action_tag(answer)
    wants_search_by_tag = tagged_action == "search"
    wants_search_by_fallback = deps.answer_promises_search(stripped_answer)
    if wants_search_by_tag:
        wants_search_by_fallback = False
    if opts.get("skip_search_followup") and not force and not wants_search_by_tag and not wants_search_by_fallback:
        return
    if not force and not wants_search_by_tag and not wants_search_by_fallback:
        return
    query = deps.build_search_query(guild_id, user_text, session_key=session_key)
    if len(query) < 2:
        return
    task_key = deps.runtime_session_key(session_key=session_key, guild_id=guild_id)
    if task_key is None:
        return
    if channel_id is not None or reply_to_message_id is not None:
        deps.remember_session_followup_target(task_key, channel_id=channel_id, message_id=reply_to_message_id)
    search_key = normalize_search_key(task_key, query)
    for existing_key, existing_task in list(deps.inflight_search_tasks.items()):
        if not existing_key.startswith(f"{task_key}:"):
            continue
        if existing_key == search_key:
            if existing_task is not None and not existing_task.done():
                return
    recovery_intent_id = None
    if (
        deps.search_followup_recovery is not None
        and isinstance(continuity_generation, int)
        and not isinstance(continuity_generation, bool)
        and continuity_generation >= 1
    ):
        try:
            recovery_intent_id = deps.search_followup_recovery.begin(
                guild_id=guild_id,
                session_key=task_key,
                source="voice" if "voice" in source else "text",
                turn_id=deps.current_turn_id(task_key),
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                channel_id=channel_id,
                reply_to_message_id=reply_to_message_id,
                request_user_text=user_text,
                request_answer_text=stripped_answer,
                query=query,
                continuity_generation=int(
                    continuity_generation or 0
                ),
            )
        except Exception as exc:
            deps.log(
                "[SEARCH] recovery_begin_failed "
                f"guild={guild_id} session={task_key} "
                f"errorType={type(exc).__name__}"
            )
    elif deps.search_followup_recovery is not None:
        deps.log(
            "[SEARCH] recovery_anchor_unavailable "
            f"guild={guild_id} session={task_key}"
        )
    for existing_key, existing_task in list(
        deps.inflight_search_tasks.items()
    ):
        if not existing_key.startswith(f"{task_key}:"):
            continue
        prior_query = existing_key.split(":", 1)[1]
        if (
            existing_key != search_key
            and is_similar(
                prior_query,
                clean_text(query).lower(),
            )
            and existing_task is not None
            and not existing_task.done()
        ):
            existing_task.cancel()
            deps.inflight_search_tasks.pop(
                existing_key,
                None,
            )
    existing = deps.background_search_tasks.get(task_key)
    if existing is not None and not existing.done():
        existing.cancel()
    deps.log(
        f"[SEARCH] scheduled guild={guild_id} "
        f"session={task_key!r} source={source}"
    )
    deps.record_search_followup_queued()
    task = schedule_search_followup_singleflight_from_runtime(
        guild_id,
        query,
        deps=deps,
        session_key=task_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        channel_id=channel_id,
        reply_to_message_id=reply_to_message_id,
        source=source,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
        recovery_intent_id=recovery_intent_id,
    )
    deps.background_search_tasks[task_key] = task
