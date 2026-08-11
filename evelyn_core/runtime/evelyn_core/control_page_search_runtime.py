from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
)
from .conversation_memory_receipt import (
    capture_conversation_memory_receipt_ref,
    memory_receipt_ref_from_metrics,
    merge_memory_receipt_refs,
    unattributed_memory_receipt_ref,
)
from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
    reset_memory_exposure_position,
)
from .memory_deletion_journal import MemoryDeletionJournalIntegrityError
from .reply_memory_boundary import validate_reply_memory_boundary

@dataclass(frozen=True)
class ControlPageSearchRuntimeDeps:
    control_page_effective_guild_id: Callable[[Any], int]
    control_page_session_key: Callable[[int | None], str]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    memory_index_dir: Path
    build_route_decision: Callable[..., Any]
    monotonic: Callable[[], float]
    execute_search_then_answer_action: Callable[..., Awaitable[Any]]
    synthesize_tool_result_with_main_llm: Callable[..., Awaitable[str]]
    clean_text: Callable[[str], str]
    get_session_lock: Callable[[str], Any]
    begin_user_text_turn: Callable[..., Any]
    turn_scope_factory: Callable[[str], Any]
    replace_room_turn_scope: Callable[[str, Any], Any]
    get_room_turn_scope: Callable[[str], Any]
    attach_current_task: Callable[[Any], Any]
    append_history: Callable[..., None]
    mark_session_active: Callable[..., None]
    commit_session_continuity: Callable[..., Awaitable[dict[str, Any]]]
    active_conversation_text_sec: float
    build_topic_id: Callable[..., str]
    schedule_local_control_tts: Callable[..., Any]
    format_display_text: Callable[..., str]
    fallback_answer_for: Callable[[str], str]
    detach_task: Callable[[Any, Any], None]
    clear_room_turn_scope: Callable[[str, Any], None]
    log: Callable[..., Any]


async def answer_control_page_search_text_from_runtime(
    guild: Any | None,
    user_text: str,
    *,
    deps: ControlPageSearchRuntimeDeps,
) -> str:
    guild_id = deps.control_page_effective_guild_id(guild)
    session_key = deps.control_page_session_key(guild_id)
    state_lock = deps.get_session_lock(session_key)
    turn_scope = None
    turn_task = None
    scope_handed_off = False
    try:
        async with state_lock:
            started_turn = deps.begin_user_text_turn(
                session_key,
                user_text,
                guild_id=guild_id,
            )
            turn_id = started_turn.turn_id
            turn_scope = deps.turn_scope_factory(turn_id)
            deps.replace_room_turn_scope(session_key, turn_scope)
            turn_task = deps.attach_current_task(turn_scope)

        reset_memory_exposure_position()
        history_outcome = filter_conversation_history_for_memory_exposure(
            deps.get_conversation_history(
                session_key=session_key,
                guild_id=guild_id,
            ),
            memory_index_dir=deps.memory_index_dir,
        )
        exposure_position = capture_combined_memory_exposure(
            history_outcome.memory_exposure_position
        )
        messages: list[dict[str, Any]] = []
        for history_message in history_outcome.messages:
            projected = dict(history_message)
            projected.pop("memoryReceipt", None)
            projected.pop("memoryReceiptRef", None)
            messages.append(projected)
        route_decision = deps.build_route_decision(
            action="search_then_answer",
            route="search_executor",
            source="control_page",
            prompt_text=user_text,
            needs_main_llm=False,
            needs_search=True,
            needs_tts=False,
            priority="accuracy",
        )
        metrics: dict[str, Any] = {
            "started_at": deps.monotonic(),
            "meta": {
                "turn_id": turn_id,
                "source": "control_page",
                "session_key": session_key,
                "guild_id": guild_id,
                "selected_path": "control_page_search_direct",
            },
            "marks": {},
        }
        with memory_exposure_guard(
            expected_position=exposure_position,
            required=(exposure_position is not None),
            index_dir=deps.memory_index_dir,
        ):
            action_result = await deps.execute_search_then_answer_action(
                guild_id=guild_id,
                user_text=user_text,
                session_key=session_key,
                messages=messages,
            )
            final_answer = await deps.synthesize_tool_result_with_main_llm(
                user_text=user_text,
                tool_name="search",
                tool_result_text=action_result.answer_text,
                guild_id=guild_id,
                session_key=session_key,
                source="control_page",
                messages=messages,
                cognitive_state={
                    "action": "search_then_answer",
                    "user_intent": user_text,
                },
                route_decision=route_decision,
                metrics=metrics,
            )
        exposure_position = capture_combined_memory_exposure(
            exposure_position,
            current_memory_exposure_position(),
        )
        response_receipt_ref = merge_memory_receipt_refs(
            history_outcome.memory_receipt_ref,
            memory_receipt_ref_from_metrics(metrics),
        )
        if response_receipt_ref is None:
            response_receipt_ref = unattributed_memory_receipt_ref()
        exposure_position, response_receipt_ref = (
            validate_reply_memory_boundary(
                memory_exposure_position=exposure_position,
                memory_receipt=response_receipt_ref,
            )
        )
        capture_conversation_memory_receipt_ref(
            response_receipt_ref
        )
        reply = (
            deps.clean_text(final_answer)
            or deps.clean_text(action_result.answer_text)
            or "지금 검색 결과를 정리하지 못했어. 잠깐 뒤에 다시 시도해줘."
        )
        async with state_lock:
            turn_scope.raise_if_cancelled()
            if deps.get_room_turn_scope(session_key) is not turn_scope:
                raise asyncio.CancelledError()
            with memory_exposure_guard(
                expected_position=exposure_position,
                required=(exposure_position is not None),
                index_dir=deps.memory_index_dir,
            ):
                deps.append_history(
                    session_key,
                    user_text,
                    reply,
                    guild_id=guild_id,
                    memory_receipt=response_receipt_ref,
                )
                deps.mark_session_active(
                    session_key,
                    ttl_sec=deps.active_conversation_text_sec,
                    speaker="assistant",
                    awaiting_user_reply=False,
                    topic_id=deps.build_topic_id(
                        user_text,
                        "search_executor",
                        reply,
                    ),
                    answer_text=reply,
                    user_text=user_text,
                )
                try:
                    continuity_status = await deps.commit_session_continuity(
                        session_key,
                        turn_id,
                    )
                    continuity_receipt = (
                        require_durable_continuity_receipt(
                            continuity_status
                        )
                    )
                    metrics["meta"]["continuity_commit"] = "durable"
                    metrics["meta"]["continuity_generation"] = int(
                        continuity_receipt["generation"]
                    )
                except MemoryDeletionJournalIntegrityError:
                    raise
                except Exception as exc:
                    metrics["meta"]["continuity_commit"] = "failed"
                    metrics["meta"]["continuity_error"] = (
                        "conversation_continuity_commit_failed"
                    )
                    deps.log(
                        "[CONTROL PAGE] search_continuity_commit_failed "
                        f"session={session_key} "
                        f"errorType={type(exc).__name__}"
                    )
                tts_task = deps.schedule_local_control_tts(
                    reply,
                    turn_id=turn_id,
                    session_key=session_key,
                    turn_scope=turn_scope,
                )
                if tts_task is not None:
                    tts_task.add_done_callback(
                        lambda _done, key=session_key, scope=turn_scope: deps.clear_room_turn_scope(
                            key,
                            scope,
                        )
                    )
                    scope_handed_off = True
                return (
                    deps.format_display_text(
                        reply,
                        session_key=session_key,
                    ).strip()
                    or deps.fallback_answer_for(user_text)
                )
    finally:
        if turn_scope is not None:
            deps.detach_task(turn_scope, turn_task)
            if not scope_handed_off:
                deps.clear_room_turn_scope(session_key, turn_scope)


__all__ = [
    "ControlPageSearchRuntimeDeps",
    "answer_control_page_search_text_from_runtime",
]
