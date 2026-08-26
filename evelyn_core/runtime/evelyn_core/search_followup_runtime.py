from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .continuity_commit_contract import (
    await_continuity_commit_without_early_unlock,
    require_durable_continuity_receipt,
)
from .discord_ingress import build_text_ingress_context
from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
    memory_receipt_ref_from_exposure,
)
from .memory_deletion_journal import MemoryDeletionJournalIntegrityError
from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
    reset_memory_exposure_position,
)
from .search_followup_recovery import content_sha256
from .text import clean_text, is_similar, strip_omnivoice_tags
from .search_query_context import build_search_query_from_context


@dataclass(frozen=True)
class SearchFollowupRuntimeDeps:
    memory_index_dir: Path
    bot: Any
    discord_object_factory: Callable[..., Any]
    session_followup_targets: dict[str, dict[str, int]]
    background_search_tasks: dict[str, asyncio.Task]
    inflight_search_tasks: dict[str, asyncio.Task]
    session_locks: dict[str, asyncio.Lock]
    reply_slot_locks: dict[str, asyncio.Lock]
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
    start_new_turn: Callable[[str | None], str]
    append_history: Callable[..., Any]
    mark_session_active: Callable[..., Any]
    build_topic_id: Callable[..., str]
    active_conversation_text_sec: float
    schedule_memory_update: Callable[..., Any]
    create_turn_scoped_task: Callable[..., asyncio.Task]
    attach_current_task: Callable[[Any], asyncio.Task | None]
    detach_task: Callable[[Any, asyncio.Task | None], Any]
    record_search_followup_queued: Callable[[], Any]
    commit_session_continuity: Callable[..., Awaitable[dict[str, Any]]]
    search_followup_recovery: Any | None = None
    continuity_status: Callable[[], dict[str, Any]] | None = None
    guild_is_open: Callable[[int], bool] | None = None
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    log: Callable[..., Any] = print


_TEXT_SEARCH_SESSION = re.compile(
    r"^guild:(?P<guild>\d+):text:(?P<channel>\d+)"
    r"(?::thread:(?P<thread>\d+))?:user:(?P<user>\d+)$"
)


def _text_search_ingress(
    session_key: object,
    *,
    guild_id: int,
    channel_id: object,
) -> Any | None:
    if not isinstance(session_key, str):
        return None
    match = _TEXT_SEARCH_SESSION.fullmatch(session_key)
    if match is None:
        return None
    parsed = {
        name: int(value) if value is not None else None
        for name, value in match.groupdict().items()
    }
    if (
        parsed["guild"] != guild_id
        or any(
            value is not None and value <= 0
            for value in parsed.values()
        )
        or type(channel_id) is not int
        or channel_id != parsed["channel"]
    ):
        return None
    ingress = build_text_ingress_context(
        guild_id=parsed["guild"],
        channel_id=parsed["channel"],
        user_id=parsed["user"],
        thread_id=parsed["thread"],
    )
    return ingress if ingress.session_key == session_key else None


def build_search_query_from_runtime(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    deps: SearchFollowupRuntimeDeps,
) -> str:
    context_messages = list(messages or [])
    return build_search_query_from_context(
        user_text,
        messages=context_messages,
        memory_summary="",
        has_memory_scope=False,
    )


def _require_exact_delivery_receipt(
    result: Any,
    expected_text: str,
) -> int:
    message = getattr(result, "message", None)
    message_id = getattr(message, "id", None)
    if (
        type(message_id) is not int
        or message_id < 1
        or str(getattr(message, "content", "")) != expected_text
    ):
        raise RuntimeError(
            "search_followup_delivery_receipt_invalid"
        )
    return message_id


def _require_current_durable_continuity_generation(
    deps: SearchFollowupRuntimeDeps,
) -> int:
    if deps.continuity_status is None:
        raise RuntimeError(
            "search_followup_continuity_status_unavailable"
        )
    receipt = require_durable_continuity_receipt(
        deps.continuity_status()
    )
    return int(receipt["generation"])


def _mark_delivery_uncertain_best_effort(
    recovery: Any,
    intent_id: str,
    *,
    deps: SearchFollowupRuntimeDeps,
    error_code: str,
) -> None:
    try:
        recovery.mark_delivery_uncertain(
            intent_id,
            error_code=error_code,
        )
    except Exception as exc:
        deps.log(
            "[SEARCH] recovery_uncertain_mark_failed "
            f"errorType={type(exc).__name__}"
        )


async def _commit_search_followup_canonical(
    *,
    guild_id: int,
    query: str,
    answer: str,
    deps: SearchFollowupRuntimeDeps,
    session_key: str,
    ingress: Any,
    delivery_turn_id: str,
    exposure_position: Any,
    memory_receipt_ref: dict[str, Any] | None,
    stage_canonical: bool,
) -> dict[str, Any]:
    with memory_exposure_guard(
        expected_position=exposure_position,
        required=(exposure_position is not None),
        index_dir=deps.memory_index_dir,
    ):
        if stage_canonical:
            deps.start_new_turn(
                session_key,
                turn_id=delivery_turn_id,
            )
            deps.append_history(
                session_key,
                query,
                answer,
                guild_id=guild_id,
                memory_receipt=memory_receipt_ref,
            )
            deps.mark_session_active(
                session_key,
                user_id=ingress.user_id,
                ttl_sec=deps.active_conversation_text_sec,
                speaker="assistant",
                awaiting_user_reply=False,
                topic_id=deps.build_topic_id(
                    query,
                    "search_followup",
                    answer,
                ),
                answer_text=answer,
                user_text=query,
            )
        try:
            continuity_status = (
                await await_continuity_commit_without_early_unlock(
                    deps.commit_session_continuity(
                        session_key,
                        delivery_turn_id,
                    )
                )
            )
            return require_durable_continuity_receipt(
                continuity_status
            )
        except Exception as exc:
            deps.log(
                "[SEARCH] followup_continuity_commit_failed "
                f"guild={guild_id} session={session_key} "
                f"errorType={type(exc).__name__}"
            )
            raise


def _project_search_followup_delivery(
    *,
    guild_id: int,
    query: str,
    answer: str,
    deps: SearchFollowupRuntimeDeps,
    ingress: Any,
    source: str,
    completed_state: dict[str, Any] | None,
    turn_scope: Any | None,
    runtime_mode: str | None,
    exposure_position: Any,
) -> None:
    with memory_exposure_guard(
        expected_position=exposure_position,
        required=(exposure_position is not None),
        index_dir=deps.memory_index_dir,
    ):
        try:
            deps.schedule_memory_update(
                guild_id,
                query,
                answer,
                room_key=ingress.room_key,
                person_key=ingress.person_key,
                session_memory_key=ingress.session_memory_key,
                source=source,
                user_speaker="search_task",
                assistant_speaker="Evelyn",
                turn_scope=turn_scope,
                runtime_mode=runtime_mode,
            )
        except MemoryDeletionJournalIntegrityError:
            raise
        except Exception as exc:
            deps.log(
                "[SEARCH] memory_update_failed "
                f"guild={guild_id} errorType={type(exc).__name__}"
            )
        try:
            removed = deps.resolve_open_question_rows(
                guild_id,
                query,
                answer,
            )
            for scope_type, scope_key in (
                ("room", ingress.room_key),
                ("person", ingress.person_key),
                ("session", ingress.session_memory_key),
            ):
                if scope_key:
                    removed += deps.resolve_open_question_rows(
                        guild_id,
                        query,
                        answer,
                        scope_type=scope_type,
                        scope_key=scope_key,
                    )
            if removed:
                deps.log(
                    "[SEARCH] resolved_open_questions "
                    f"guild={guild_id} removed={removed}"
                )
        except Exception as exc:
            deps.log(
                "[SEARCH] open_question_resolution_failed "
                f"guild={guild_id} errorType={type(exc).__name__}"
            )
        if completed_state is not None:
            try:
                deps.write_json_file(
                    deps.cognitive_state_path(guild_id),
                    completed_state,
                )
                for scope_type, scope_key in (
                    ("room", ingress.room_key),
                    ("person", ingress.person_key),
                    ("session", ingress.session_memory_key),
                ):
                    if scope_key:
                        deps.write_json_file(
                            deps.cognitive_state_path(
                                guild_id,
                                scope_type=scope_type,
                                scope_key=scope_key,
                            ),
                            completed_state,
                        )
            except Exception as exc:
                deps.log(
                    "[SEARCH] cognitive_state_update_failed "
                    f"guild={guild_id} errorType={type(exc).__name__}"
                )


def _completed_search_state(
    query: str,
    *,
    failed: bool,
) -> dict[str, Any]:
    return {
        "action": "answer",
        "confidence": 0.0 if failed else 1.0,
        "user_intent": clean_text(query),
        "state_summary": (
            "검색 시도가 실패해 사용자에게 실패 상태를 전달했다."
            if failed
            else "검색을 마쳤고 결과를 사용자에게 전달했다."
        ),
        "question_for_user": "",
        "main_prompt_hint": "찾은 내용을 바로 전달해라.",
        "reason_brief": (
            "search_failed" if failed else "search_completed"
        ),
        "retrieved_context_ids": [],
        "updated_at": int(time.time()),
    }


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
    source_turn_id: str | None,
    completed_state: dict[str, Any] | None = None,
    turn_scope: Any | None = None,
    runtime_mode: str | None = None,
    recovery_intent_id: str | None = None,
) -> bool:
    def guild_open() -> bool:
        return (
            deps.guild_is_open is None
            or deps.guild_is_open(guild_id)
        )

    if not guild_open():
        return False
    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    if "voice" in clean_text(source).lower():
        if recovery_intent_id is not None and deps.search_followup_recovery is not None:
            deps.search_followup_recovery.mark_delivery_uncertain(
                recovery_intent_id,
                error_code="search_followup_delivery_owner_unavailable",
            )
        deps.log(
            "[SEARCH] voice_followup_owner_unavailable "
            f"guild={guild_id}"
        )
        return False
    plain_answer = strip_omnivoice_tags(answer) or answer
    target_channel_id = channel_id
    stored_target = deps.session_followup_targets.get(session_key, {}) if session_key is not None else {}
    if target_channel_id is None and session_key is not None:
        target_channel_id = stored_target.get("channel_id")
    reply_target_id = reply_to_message_id if reply_to_message_id is not None else stored_target.get("message_id")
    ingress = _text_search_ingress(
        session_key,
        guild_id=guild_id,
        channel_id=target_channel_id,
    )
    source_turn_id = clean_text(source_turn_id)
    if ingress is None or not source_turn_id:
        if recovery_intent_id is not None and deps.search_followup_recovery is not None:
            deps.search_followup_recovery.mark_delivery_uncertain(
                recovery_intent_id,
                error_code="search_followup_delivery_owner_unavailable",
            )
        deps.log(
            "[SEARCH] followup_delivery_owner_unavailable "
            f"guild={guild_id} session={session_key}"
        )
        return False
    prepared = False
    display_answer = deps.format_display_text(
        plain_answer,
        session_key=session_key,
    )
    delivery_exposure_position = current_memory_exposure_position()
    delivery_memory_receipt_ref = memory_receipt_ref_from_exposure(
        delivery_exposure_position
    )

    channel = None
    if target_channel_id is not None:
        channel = deps.bot.get_channel(target_channel_id)
        if channel is None:
            try:
                channel = await deps.bot.fetch_channel(target_channel_id)
            except Exception:
                channel = None
    reply_lock = deps.reply_slot_locks.setdefault(
        ingress.reply_slot_key,
        asyncio.Lock(),
    )
    state_lock = deps.session_locks.setdefault(
        ingress.session_key,
        asyncio.Lock(),
    )
    async with reply_lock:
        async with state_lock:
            if not guild_open():
                return False
            if clean_text(deps.current_turn_id(session_key)) != source_turn_id:
                if recovery_intent_id is not None and deps.search_followup_recovery is not None:
                    deps.search_followup_recovery.mark_delivery_uncertain(
                        recovery_intent_id,
                        error_code="search_followup_source_turn_superseded",
                    )
                deps.log(
                    "[SEARCH] followup_source_turn_superseded "
                    f"guild={guild_id} session={session_key}"
                )
                return False
            if channel is not None and hasattr(channel, "send"):
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                if not guild_open():
                    return False
                delivery_turn_id = deps.start_new_turn(None)
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
                        delivery_turn_id=delivery_turn_id,
                    )
                    recovery.mark_delivery_baseline(
                        recovery_intent_id,
                        continuity_generation=(
                            _require_current_durable_continuity_generation(
                                deps
                            )
                        ),
                    )
                    recovery.mark_delivery_attempted(
                        recovery_intent_id
                    )
                try:
                    with memory_exposure_guard(
                        expected_position=delivery_exposure_position,
                        required=(delivery_exposure_position is not None),
                        index_dir=deps.memory_index_dir,
                    ):
                        delivery_result = (
                            await await_continuity_commit_without_early_unlock(
                                deps.send_discord_text(
                                    channel,
                                    display_answer,
                                    reference_message_id=reply_target_id,
                                    reference_factory=lambda message_id: deps.discord_object_factory(id=message_id),
                                )
                            )
                        )
                except asyncio.CancelledError:
                    if recovery_intent_id is not None:
                        _mark_delivery_uncertain_best_effort(
                            recovery,
                            recovery_intent_id,
                            deps=deps,
                            error_code=(
                                "search_followup_delivery_cancelled"
                            ),
                        )
                    raise
                except Exception:
                    if recovery_intent_id is not None:
                        _mark_delivery_uncertain_best_effort(
                            recovery,
                            recovery_intent_id,
                            deps=deps,
                            error_code=(
                                "search_followup_delivery_failed"
                            ),
                        )
                    raise
                try:
                    delivery_message_id = (
                        _require_exact_delivery_receipt(
                            delivery_result,
                            display_answer,
                        )
                    )
                except Exception:
                    if recovery_intent_id is not None:
                        _mark_delivery_uncertain_best_effort(
                            recovery,
                            recovery_intent_id,
                            deps=deps,
                            error_code=(
                                "search_followup_delivery_receipt_invalid"
                            ),
                        )
                    raise
                if recovery_intent_id is not None:
                    recovery.mark_delivery_succeeded(
                        recovery_intent_id,
                        delivery_message_id=delivery_message_id,
                    )
                continuity_receipt = await _commit_search_followup_canonical(
                    guild_id=guild_id,
                    query=query,
                    answer=plain_answer,
                    deps=deps,
                    session_key=ingress.session_key,
                    ingress=ingress,
                    delivery_turn_id=delivery_turn_id,
                    exposure_position=delivery_exposure_position,
                    memory_receipt_ref=delivery_memory_receipt_ref,
                    stage_canonical=True,
                )
                if recovery_intent_id is not None:
                    recovery.mark_canonical_committed(
                        recovery_intent_id,
                        continuity_generation=int(
                            continuity_receipt["generation"]
                        ),
                    )
                    recovery.complete(recovery_intent_id)
                _project_search_followup_delivery(
                    guild_id=guild_id,
                    query=query,
                    answer=plain_answer,
                    deps=deps,
                    ingress=ingress,
                    source=source,
                    completed_state=completed_state,
                    turn_scope=turn_scope,
                    runtime_mode=runtime_mode,
                    exposure_position=delivery_exposure_position,
                )
                prepared = True

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    if recovery_intent_id is None:
        return prepared
    return not deps.search_followup_recovery.is_active(
        recovery_intent_id
    )


def normalize_search_key(session_key: str, query: str) -> str:
    return f"{session_key}:{clean_text(query).lower()}"


def _track_search_task_drain(
    guild_id: int,
    task: asyncio.Task,
    *,
    deps: SearchFollowupRuntimeDeps,
) -> None:
    if task.done():
        return
    drain_key = f"guild:{guild_id}:search-drain:{id(task)}"
    if deps.background_search_tasks.get(drain_key) is task:
        return
    deps.background_search_tasks[drain_key] = task

    def release(completed: asyncio.Task) -> None:
        if deps.background_search_tasks.get(drain_key) is completed:
            deps.background_search_tasks.pop(drain_key, None)

    task.add_done_callback(release)


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
    generation: int | None,
) -> bool:
    if generation is None:
        return False
    required = max(
        int(entry.get("continuityGeneration") or 0),
        int(entry.get("deliveryGeneration") or 0),
    )
    return required >= 1 and generation >= required


def _discord_reply_reference_message_id(message: Any) -> int | None:
    def field(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    try:
        reference = getattr(message, "reference", None)
        if reference is None:
            return None
        resolved = field(reference, "resolved")
        cached = field(reference, "cached_message")
        raw_candidates = (
            field(reference, "message_id"),
            field(reference, "id"),
            field(resolved, "id"),
            field(cached, "id"),
        )
    except Exception:
        return None
    candidates: list[int] = []
    for value in raw_candidates:
        if value is None:
            continue
        if type(value) is not int or value < 1:
            return None
        candidates.append(value)
    if not candidates or any(value != candidates[0] for value in candidates[1:]):
        return None
    return candidates[0]


async def _channel_contains_followup(
    channel: Any,
    display_text: str,
    *,
    bot_user_id: int | None,
    after_message_id: int | None,
    discord_object_factory: Callable[..., Any],
) -> int | bool | None:
    history_method = getattr(channel, "history", None)
    if (
        not callable(history_method)
        or type(bot_user_id) is not int
        or bot_user_id < 1
        or type(after_message_id) is not int
        or after_message_id < 1
    ):
        return None
    kwargs: dict[str, Any] = {
        "limit": 50,
        "after": discord_object_factory(id=after_message_id),
    }
    try:
        iterator = history_method(**kwargs)
    except TypeError:
        return None
    seen = 0
    matches: list[tuple[int | None, int | None]] = []
    async for message in iterator:
        seen += 1
        author_id = getattr(getattr(message, "author", None), "id", None)
        content = getattr(message, "content", None)
        if (
            type(author_id) is int
            and author_id == bot_user_id
            and type(content) is str
            and content == display_text
        ):
            message_id = getattr(message, "id", None)
            matches.append(
                (
                    message_id
                    if type(message_id) is int and message_id >= 1
                    else None,
                    _discord_reply_reference_message_id(message),
                )
            )
    if seen >= 50:
        return None
    if not matches:
        return False
    if len(matches) != 1:
        return None
    message_id, reference_message_id = matches[0]
    if message_id is None or reference_message_id != after_message_id:
        return None
    return message_id


async def recover_search_followups_from_runtime(
    *,
    deps: SearchFollowupRuntimeDeps,
) -> dict[str, int]:
    def guild_open(guild_id: int) -> bool:
        return (
            deps.guild_is_open is None
            or deps.guild_is_open(guild_id)
        )

    def schedule_source_search(
        entry: dict[str, Any],
        *,
        guild_id: int,
        session_key: str,
        query: str,
        intent_id: str,
    ) -> None:
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
            source_turn_id=clean_text(entry.get("turnId")),
            recovery_intent_id=intent_id,
        )
        existing = deps.background_search_tasks.get(session_key)
        if (
            existing is not None
            and existing is not task
            and not existing.done()
        ):
            _track_search_task_drain(
                guild_id,
                existing,
                deps=deps,
            )
        deps.background_search_tasks[session_key] = task

    reset_memory_exposure_position()
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
    for entry in entries:
        reset_memory_exposure_position()
        intent_id = str(entry["intentId"])
        session_key = str(entry["sessionKey"])
        guild_id = int(entry["guildId"])
        delivery_claimed = False
        owner_task = asyncio.current_task()
        owner_key = (
            f"guild:{guild_id}:search-recovery:{intent_id}:{id(owner_task)}"
            if owner_task is not None
            else None
        )
        if owner_key is not None:
            deps.background_search_tasks[owner_key] = owner_task
        try:
            phase = str(entry["phase"])
            if not guild_open(guild_id):
                continue
            if phase == "request_unrecoverable":
                counts["uncertain"] += 1
                continue
            if entry["source"] == "voice":
                recovery.mark_delivery_uncertain(
                    intent_id,
                    error_code=(
                        "search_followup_delivery_owner_unavailable"
                    ),
                )
                counts["uncertain"] += 1
                continue
            if (
                phase != "running"
                and not clean_text(entry.get("deliveryTurnId"))
            ):
                recovery.mark_delivery_uncertain(
                    intent_id,
                    error_code=(
                        "search_followup_delivery_turn_unavailable"
                    ),
                )
                counts["uncertain"] += 1
                continue
            try:
                status_generation = (
                    _require_current_durable_continuity_generation(
                        deps
                    )
                )
            except Exception:
                status_generation = None
            if not _continuity_can_recover(entry, status_generation):
                recovery.mark_delivery_uncertain(
                    intent_id,
                    error_code="search_followup_continuity_unavailable",
                )
                counts["uncertain"] += 1
                continue

            persisted_history = list(
                deps.get_conversation_history(
                    session_key=session_key,
                    guild_id=guild_id,
                )
            )
            history_outcome = (
                filter_conversation_history_for_memory_exposure(
                    persisted_history,
                    memory_index_dir=Path(deps.memory_index_dir),
                )
            )
            history = list(history_outcome.messages)
            exposure_position = capture_combined_memory_exposure(
                history_outcome.memory_exposure_position
            )
            raw_request_matches = _hashed_history_pairs(
                persisted_history,
                user_hash=str(entry["requestUserHash"]),
                assistant_hash=str(entry["requestAnswerHash"]),
            )
            request_matches = _hashed_history_pairs(
                history,
                user_hash=str(entry["requestUserHash"]),
                assistant_hash=str(entry["requestAnswerHash"]),
            )
            raw_delivery_matches = _hashed_history_pairs(
                persisted_history,
                user_hash=str(entry["queryHash"]),
                assistant_hash=str(entry["answerHash"]),
            )
            delivery_matches = _hashed_history_pairs(
                history,
                user_hash=str(entry["queryHash"]),
                assistant_hash=str(entry["answerHash"]),
            )
            if (
                len(raw_request_matches) != len(request_matches)
                or len(raw_delivery_matches) != len(delivery_matches)
            ):
                raise RuntimeError(
                    "search_followup_memory_exposure_rejected"
                )
            request_pair = _find_hashed_history_pair(
                history,
                user_hash=str(entry["requestUserHash"]),
                assistant_hash=str(entry["requestAnswerHash"]),
            )
            if request_pair is None:
                raise RuntimeError(
                    "search_followup_request_context_ambiguous"
                )
            query = (
                delivery_matches[0][0]
                if len(delivery_matches) == 1
                else deps.build_search_query(
                    guild_id,
                    request_pair[0],
                    session_key=session_key,
                )
            )
            if content_sha256(query) != entry["queryHash"]:
                raise RuntimeError(
                    "search_followup_query_reconstruction_failed"
                )

            prepared_answer = str(entry.get("preparedAnswer") or "")
            if phase == "running" or (
                phase == "delivery_preparing"
                and not prepared_answer
            ):
                schedule_source_search(
                    entry,
                    guild_id=guild_id,
                    session_key=session_key,
                    query=query,
                    intent_id=intent_id,
                )
                counts["resumed"] += 1
                continue

            if len(delivery_matches) > 1:
                raise RuntimeError(
                    "search_followup_delivery_context_ambiguous"
                )
            legacy_canonical = (
                phase == "delivery_ready"
                or (
                    phase == "delivery_preparing"
                    and len(delivery_matches) == 1
                    and int(
                        status_generation or 0
                    )
                    > int(entry["continuityGeneration"])
                )
                or (
                    phase in {
                        "delivery_attempted",
                        "delivery_uncertain",
                    }
                    and int(entry.get("deliveryGeneration") or 0)
                    >= 1
                    and not prepared_answer
                )
            )
            if prepared_answer:
                answer = prepared_answer
                if content_sha256(answer) != entry["answerHash"]:
                    raise RuntimeError(
                        "search_followup_answer_reconstruction_failed"
                    )
            elif len(delivery_matches) == 1:
                _delivery_query, answer = delivery_matches[0]
            else:
                raise RuntimeError(
                    "search_followup_delivery_context_ambiguous"
                )
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
            ingress = _text_search_ingress(
                session_key,
                guild_id=guild_id,
                channel_id=channel_id,
            )
            if ingress is None:
                recovery.mark_delivery_uncertain(
                    intent_id,
                    error_code=(
                        "search_followup_delivery_unverifiable"
                    ),
                )
                counts["uncertain"] += 1
                continue
            if not recovery.claim_recovery(intent_id):
                continue
            delivery_claimed = True
            reply_lock = deps.reply_slot_locks.setdefault(
                ingress.reply_slot_key,
                asyncio.Lock(),
            )
            state_lock = deps.session_locks.setdefault(
                ingress.session_key,
                asyncio.Lock(),
            )
            async with reply_lock:
                async with state_lock:
                    if not guild_open(guild_id):
                        recovery.release_recovery_claim(intent_id)
                        delivery_claimed = False
                        continue
                    delivery_turn_id = clean_text(
                        entry.get("deliveryTurnId")
                    )
                    source_turn_id = clean_text(entry.get("turnId"))
                    current_turn_id = clean_text(
                        deps.current_turn_id(session_key)
                    )
                    status_generation = (
                        _require_current_durable_continuity_generation(
                            deps
                        )
                    )
                    source_generation = int(
                        entry["continuityGeneration"]
                    )
                    delivery_generation = int(
                        entry.get("deliveryGeneration") or 0
                    )
                    delivery_receipt_valid = (
                        type(entry.get("deliveryMessageId")) is int
                        and int(entry["deliveryMessageId"]) >= 1
                    )
                    delivery_baseline_valid = (
                        delivery_generation >= source_generation
                        and source_generation >= 1
                    )
                    canonical_persisted = (
                        len(delivery_matches) == 1
                        and delivery_receipt_valid
                        and delivery_baseline_valid
                        and (
                            (
                                phase == "delivery_succeeded"
                                and status_generation
                                > delivery_generation
                                and current_turn_id
                                not in {
                                    source_turn_id,
                                    delivery_turn_id,
                                }
                            )
                            or (
                                phase == "canonical_committed"
                                and status_generation
                                >= delivery_generation
                            )
                        )
                    )

                    if legacy_canonical:
                        if (
                            len(delivery_matches) != 1
                            or current_turn_id != delivery_turn_id
                        ):
                            raise RuntimeError(
                                "search_followup_delivery_context_ambiguous"
                            )
                        channel = await _search_followup_channel(
                            channel_id,
                            deps=deps,
                        )
                        if channel is None:
                            raise RuntimeError(
                                "search_followup_delivery_unverifiable"
                            )
                        if not guild_open(guild_id):
                            recovery.release_recovery_claim(intent_id)
                            delivery_claimed = False
                            continue
                        if phase not in {
                            "delivery_preparing",
                            "delivery_ready",
                        }:
                            existing_message_id = (
                                await _channel_contains_followup(
                                    channel,
                                    display_text,
                                    bot_user_id=getattr(
                                        getattr(deps.bot, "user", None),
                                        "id",
                                        None,
                                    ),
                                    after_message_id=(
                                        reply_to_message_id
                                    ),
                                    discord_object_factory=(
                                        deps.discord_object_factory
                                    ),
                                )
                            )
                            if type(existing_message_id) is int:
                                recovery.complete(intent_id)
                                counts["verified"] += 1
                                continue
                            if existing_message_id is None:
                                recovery.mark_delivery_uncertain(
                                    intent_id,
                                    error_code=(
                                        "search_followup_delivery_history_inconclusive"
                                    ),
                                )
                                recovery.release_recovery_claim(intent_id)
                                delivery_claimed = False
                                counts["uncertain"] += 1
                                continue
                        recovery.mark_delivery_attempted(intent_id)
                        try:
                            delivery_result = await _send_search_followup(
                                channel,
                                display_text,
                                reply_to_message_id=reply_to_message_id,
                                exposure_position=exposure_position,
                                deps=deps,
                            )
                        except asyncio.CancelledError:
                            _mark_delivery_uncertain_best_effort(
                                recovery,
                                intent_id,
                                deps=deps,
                                error_code=(
                                    "search_followup_delivery_cancelled"
                                ),
                            )
                            raise
                        _require_exact_delivery_receipt(
                            delivery_result,
                            display_text,
                        )
                        recovery.complete(intent_id)
                        counts["redelivered"] += 1
                        continue

                    delivery_was_verified = phase in {
                        "delivery_succeeded",
                        "canonical_committed",
                    }
                    if phase == "canonical_committed":
                        if not canonical_persisted:
                            raise RuntimeError(
                                "search_followup_delivery_not_durable"
                            )
                    elif phase == "delivery_succeeded":
                        if not (
                            delivery_receipt_valid
                            and delivery_baseline_valid
                        ):
                            recovery.mark_delivery_uncertain(
                                intent_id,
                                error_code=(
                                    "search_followup_delivery_baseline_unavailable"
                                ),
                            )
                            recovery.release_recovery_claim(intent_id)
                            delivery_claimed = False
                            counts["uncertain"] += 1
                            continue
                        if (
                            not canonical_persisted
                            and current_turn_id not in {
                                source_turn_id,
                                delivery_turn_id,
                            }
                        ):
                            raise RuntimeError(
                                "search_followup_source_turn_superseded"
                            )
                    else:
                        if current_turn_id != source_turn_id:
                            raise RuntimeError(
                                "search_followup_source_turn_superseded"
                            )
                        channel = await _search_followup_channel(
                            channel_id,
                            deps=deps,
                        )
                        if channel is None:
                            raise RuntimeError(
                                "search_followup_delivery_unverifiable"
                            )
                        if not guild_open(guild_id):
                            recovery.release_recovery_claim(intent_id)
                            delivery_claimed = False
                            continue
                        if phase in {
                            "delivery_attempted",
                            "delivery_uncertain",
                        }:
                            existing_message_id = (
                                await _channel_contains_followup(
                                    channel,
                                    display_text,
                                    bot_user_id=getattr(
                                        getattr(deps.bot, "user", None),
                                        "id",
                                        None,
                                    ),
                                    after_message_id=(
                                        reply_to_message_id
                                    ),
                                    discord_object_factory=(
                                        deps.discord_object_factory
                                    ),
                                )
                            )
                            if type(existing_message_id) is int:
                                recovery.mark_delivery_baseline(
                                    intent_id,
                                    continuity_generation=(
                                        _require_current_durable_continuity_generation(
                                            deps
                                        )
                                    ),
                                )
                                recovery.mark_delivery_succeeded(
                                    intent_id,
                                    delivery_message_id=(
                                        existing_message_id
                                    ),
                                )
                                delivery_was_verified = True
                            elif existing_message_id is None:
                                recovery.mark_delivery_uncertain(
                                    intent_id,
                                    error_code=(
                                        "search_followup_delivery_history_inconclusive"
                                    ),
                                )
                                recovery.release_recovery_claim(intent_id)
                                delivery_claimed = False
                                counts["uncertain"] += 1
                                continue
                        if not delivery_was_verified:
                            recovery.mark_delivery_baseline(
                                intent_id,
                                continuity_generation=(
                                    _require_current_durable_continuity_generation(
                                        deps
                                    )
                                ),
                            )
                            recovery.mark_delivery_attempted(intent_id)
                            try:
                                delivery_result = (
                                    await _send_search_followup(
                                        channel,
                                        display_text,
                                        reply_to_message_id=(
                                            reply_to_message_id
                                        ),
                                        exposure_position=(
                                            exposure_position
                                        ),
                                        deps=deps,
                                    )
                                )
                                delivery_message_id = (
                                    _require_exact_delivery_receipt(
                                        delivery_result,
                                        display_text,
                                    )
                                )
                            except asyncio.CancelledError:
                                _mark_delivery_uncertain_best_effort(
                                    recovery,
                                    intent_id,
                                    deps=deps,
                                    error_code=(
                                        "search_followup_delivery_cancelled"
                                    ),
                                )
                                raise
                            except Exception:
                                _mark_delivery_uncertain_best_effort(
                                    recovery,
                                    intent_id,
                                    deps=deps,
                                    error_code=(
                                        "search_followup_delivery_failed"
                                    ),
                                )
                                raise
                            recovery.mark_delivery_succeeded(
                                intent_id,
                                delivery_message_id=delivery_message_id,
                            )

                    if not canonical_persisted:
                        if (
                            len(delivery_matches) == 0
                            and current_turn_id == source_turn_id
                        ):
                            stage_canonical = True
                        elif (
                            len(delivery_matches) == 1
                            and current_turn_id == delivery_turn_id
                        ):
                            stage_canonical = False
                        else:
                            raise RuntimeError(
                                "search_followup_delivery_context_ambiguous"
                            )
                        continuity_receipt = (
                            await _commit_search_followup_canonical(
                                guild_id=guild_id,
                                query=query,
                                answer=answer,
                                deps=deps,
                                session_key=ingress.session_key,
                                ingress=ingress,
                                delivery_turn_id=delivery_turn_id,
                                exposure_position=exposure_position,
                                memory_receipt_ref=(
                                    memory_receipt_ref_from_exposure(
                                        exposure_position
                                    )
                                ),
                                stage_canonical=stage_canonical,
                            )
                        )
                        recovery.mark_canonical_committed(
                            intent_id,
                            continuity_generation=int(
                                continuity_receipt["generation"]
                            ),
                        )
                    elif phase != "canonical_committed":
                        recovery.mark_canonical_committed(
                            intent_id,
                            continuity_generation=status_generation,
                        )
                    recovery.complete(intent_id)
                    _project_search_followup_delivery(
                        guild_id=guild_id,
                        query=query,
                        answer=answer,
                        deps=deps,
                        ingress=ingress,
                        source=(
                            f"search-followup-recovery-{entry['source']}"
                        ),
                        completed_state=_completed_search_state(
                            query,
                            failed=(
                                int(entry.get("attemptCount") or 0)
                                >= 3
                            ),
                        ),
                        turn_scope=None,
                        runtime_mode=None,
                        exposure_position=exposure_position,
                    )
                    if delivery_was_verified:
                        counts["verified"] += 1
                    else:
                        counts["redelivered"] += 1
                    continue
        except asyncio.CancelledError:
            if delivery_claimed:
                recovery.release_recovery_claim(intent_id)
            raise
        except Exception as exc:
            if delivery_claimed:
                recovery.release_recovery_claim(intent_id)
            _mark_delivery_uncertain_best_effort(
                recovery,
                intent_id,
                deps=deps,
                error_code="search_followup_recovery_failed",
            )
            counts["uncertain"] += 1
            deps.log(
                "[SEARCH] recovery_failed "
                f"guild={guild_id} session={session_key} "
                f"errorType={type(exc).__name__}"
            )
        finally:
            if (
                owner_key is not None
                and deps.background_search_tasks.get(owner_key)
                is owner_task
            ):
                deps.background_search_tasks.pop(owner_key, None)
    reset_memory_exposure_position()
    return counts


async def _search_followup_channel(
    channel_id: int | None,
    *,
    deps: SearchFollowupRuntimeDeps,
) -> Any | None:
    if channel_id is None:
        return None
    channel = deps.bot.get_channel(channel_id)
    if channel is not None:
        return channel if hasattr(channel, "send") else None
    fetch = getattr(deps.bot, "fetch_channel", None)
    if not callable(fetch):
        return None
    try:
        channel = await fetch(channel_id)
    except Exception:
        return None
    return channel if hasattr(channel, "send") else None


async def _send_search_followup(
    channel: Any,
    display_text: str,
    *,
    reply_to_message_id: int | None,
    exposure_position: Any,
    deps: SearchFollowupRuntimeDeps,
) -> Any:
    with memory_exposure_guard(
        expected_position=exposure_position,
        required=(exposure_position is not None),
        index_dir=deps.memory_index_dir,
    ):
        return await await_continuity_commit_without_early_unlock(
            deps.send_discord_text(
                channel,
                display_text,
                reference_message_id=reply_to_message_id,
                reference_factory=lambda message_id: (
                    deps.discord_object_factory(id=message_id)
                ),
            )
        )


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
    source_turn_id: str,
    turn_scope: Any | None = None,
    runtime_mode: str | None = None,
    recovery_intent_id: str | None = None,
) -> asyncio.Task:
    search_key = normalize_search_key(session_key, query)
    existing = deps.inflight_search_tasks.get(search_key)
    if existing is not None and not existing.done():
        _track_search_task_drain(guild_id, existing, deps=deps)
        deps.inflight_search_tasks.pop(search_key, None)
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
            source_turn_id=source_turn_id,
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
    source_turn_id: str,
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
                with memory_exposure_guard(
                    index_dir=deps.memory_index_dir,
                ):
                    results = await deps.search_duckduckgo(query)
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                with memory_exposure_guard(
                    index_dir=deps.memory_index_dir,
                ):
                    answer = await deps.answer_from_search_results(
                        query,
                        results,
                    )
                last_error = None
                break
            except asyncio.CancelledError:
                raise
            except MemoryDeletionJournalIntegrityError as exc:
                last_error = exc
                break
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
            source_turn_id=source_turn_id,
            completed_state=completed_state,
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
    if "voice" in clean_text(source).lower():
        deps.log(
            "[SEARCH] voice_followup_owner_unavailable "
            f"guild={guild_id}"
        )
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
    source_turn_id = clean_text(deps.current_turn_id(task_key))
    if not source_turn_id:
        deps.log(
            "[SEARCH] source_turn_unavailable "
            f"guild={guild_id} session={task_key}"
        )
        return
    if channel_id is not None or reply_to_message_id is not None:
        deps.remember_session_followup_target(task_key, channel_id=channel_id, message_id=reply_to_message_id)
    search_key = normalize_search_key(task_key, query)
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
                turn_id=source_turn_id,
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
    if (
        deps.search_followup_recovery is not None
        and recovery_intent_id is None
    ):
        return
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
            _track_search_task_drain(
                guild_id,
                existing_task,
                deps=deps,
            )
            deps.inflight_search_tasks.pop(
                existing_key,
                None,
            )
    existing = deps.background_search_tasks.get(task_key)
    if existing is not None and not existing.done():
        _track_search_task_drain(guild_id, existing, deps=deps)
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
        source_turn_id=source_turn_id,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
        recovery_intent_id=recovery_intent_id,
    )
    deps.background_search_tasks[task_key] = task
