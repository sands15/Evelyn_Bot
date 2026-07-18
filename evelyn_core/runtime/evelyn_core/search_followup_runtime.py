from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

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
) -> None:
    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    plain_answer = strip_omnivoice_tags(answer) or answer
    guild = deps.bot.get_guild(guild_id)
    target_channel_id = channel_id
    stored_target = deps.session_followup_targets.get(session_key, {}) if session_key is not None else {}
    if target_channel_id is None and session_key is not None:
        target_channel_id = stored_target.get("channel_id")
    reply_target_id = reply_to_message_id if reply_to_message_id is not None else stored_target.get("message_id")

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
            await deps.send_discord_text(
                channel,
                deps.format_display_text(answer, session_key=session_key),
                reference_message_id=reply_target_id,
                reference_factory=lambda message_id: deps.discord_object_factory(id=message_id),
            )

    vc = guild.voice_client if guild else None
    if vc is not None and vc.is_connected():
        try:
            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            await deps.speak_answer(
                vc,
                answer,
                turn_id=deps.current_turn_id(session_key),
                session_key=session_key,
                turn_scope=turn_scope,
            )
        except Exception as e:
            deps.log(f"[SEARCH] proactive TTS 실패: {e!r}")

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    deps.append_history(session_key, query, plain_answer, guild_id=guild_id)
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


def normalize_search_key(session_key: str, query: str) -> str:
    return f"{session_key}:{clean_text(query).lower()}"


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
) -> None:
    task = deps.attach_current_task(turn_scope)
    try:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        results = await deps.search_duckduckgo(query)
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()
        answer = await deps.answer_from_search_results(query, results)
        removed = deps.resolve_open_question_rows(guild_id, query, answer)
        if room_key:
            removed += deps.resolve_open_question_rows(guild_id, query, answer, scope_type="room", scope_key=room_key)
        if person_key:
            removed += deps.resolve_open_question_rows(guild_id, query, answer, scope_type="person", scope_key=person_key)
        if session_memory_key:
            removed += deps.resolve_open_question_rows(guild_id, query, answer, scope_type="session", scope_key=session_memory_key)
        if removed:
            deps.log(f"[SEARCH] resolved_open_questions guild={guild_id} removed={removed}")
        completed_state = {
            "action": "answer",
            "confidence": 1.0,
            "user_intent": clean_text(query),
            "state_summary": "검색을 마쳤고 결과를 사용자에게 전달했다.",
            "question_for_user": "",
            "main_prompt_hint": "찾은 내용을 바로 전달해라.",
            "reason_brief": "search_completed",
            "retrieved_context_ids": [],
            "updated_at": int(time.time()),
        }
        deps.write_json_file(deps.cognitive_state_path(guild_id), completed_state)
        if room_key:
            deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type="room", scope_key=room_key), completed_state)
        if person_key:
            deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type="person", scope_key=person_key), completed_state)
        if session_memory_key:
            deps.write_json_file(deps.cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key), completed_state)
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
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        deps.log(f"[SEARCH] follow-up 실패 guild={guild_id} query={query!r} err={e!r}")
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
        prior_query = existing_key.split(":", 1)[1]
        if existing_key == search_key:
            if existing_task is not None and not existing_task.done():
                return
            continue
        if is_similar(prior_query, clean_text(query).lower()) and existing_task is not None and not existing_task.done():
            existing_task.cancel()
            deps.inflight_search_tasks.pop(existing_key, None)
    existing = deps.background_search_tasks.get(task_key)
    if existing is not None and not existing.done():
        existing.cancel()
    deps.log(f"[SEARCH] scheduled guild={guild_id} session={task_key!r} query={query!r} source={source}")
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
    )
    deps.background_search_tasks[task_key] = task
