from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MemoryUpdateRuntimeDeps:
    write_memory_turn_records: Callable[..., Any]
    vision_memory_write_enabled: bool
    record_self_identity_turn: Callable[..., Any]
    append_raw_transcript_rows: Callable[..., Any]
    append_turn_rows_to_memory_vault: Callable[..., Any]
    schedule_memory_vault_maintenance: Callable[..., Any]
    memory_refresh_inputs_for_turn: Callable[..., Any]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    session_last_active_at: dict[str, float]
    needs_search_or_deep_routing: Callable[..., bool]
    build_memory_writer_decision_for_turn: Callable[..., Any]
    build_memory_writer_decision: Callable[..., Any]
    build_memory_writer_decision_payload: Callable[..., dict[str, Any]]
    plan_memory_writebehind_schedule: Callable[..., Any]
    runtime_session_key: Callable[..., str | None]
    memory_writebehind_task_key: Callable[..., str | None]
    should_replace_existing_memory_task: Callable[..., bool]
    mark_memory_writer_status: Callable[..., Any]
    memory_writebehind_status_log: Any
    background_memory_tasks: dict[str, asyncio.Task]
    create_turn_scoped_task: Callable[..., asyncio.Task]
    run_memory_writebehind_steps: Callable[..., Any]
    update_long_term_memory: Callable[..., Any]
    update_cognitive_state: Callable[..., Any]
    log: Callable[..., Any] = print


def _track_memory_task_drain(
    guild_id: int,
    task: asyncio.Task,
    *,
    deps: MemoryUpdateRuntimeDeps,
) -> None:
    if task.done():
        return
    drain_key = f"guild:{guild_id}:memory-drain:{id(task)}"
    if deps.background_memory_tasks.get(drain_key) is task:
        return
    deps.background_memory_tasks[drain_key] = task

    def release(completed: asyncio.Task) -> None:
        if deps.background_memory_tasks.get(drain_key) is completed:
            deps.background_memory_tasks.pop(drain_key, None)

    task.add_done_callback(release)


def schedule_memory_update_from_runtime(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    deps: MemoryUpdateRuntimeDeps,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "chat",
    user_speaker: str = "user",
    assistant_speaker: str = "Evelyn",
    session_key: str | None = None,
    turn_scope: Any = None,
    runtime_mode: str | None = None,
) -> dict[str, Any]:
    turn_write = deps.write_memory_turn_records(
        guild_id,
        user_text,
        answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        user_speaker=user_speaker,
        assistant_speaker=assistant_speaker,
        turn_id=getattr(turn_scope, "turn_id", None),
        vision_memory_write_enabled=deps.vision_memory_write_enabled,
        record_identity_turn=deps.record_self_identity_turn,
        append_raw_rows=deps.append_raw_transcript_rows,
        append_vault_rows=deps.append_turn_rows_to_memory_vault,
        log=deps.log,
    )
    memory_user_text = turn_write.memory_user_text
    memory_answer = turn_write.memory_answer

    mode = runtime_mode or "normal"
    if mode != "realtime":
        deps.schedule_memory_vault_maintenance(guild_id, turn_scope=turn_scope)
    refresh_inputs = deps.memory_refresh_inputs_for_turn(
        user_text=memory_user_text,
        source=source,
        session_key=session_key,
        guild_id=guild_id,
        history_reader=deps.get_conversation_history,
        last_active_at=deps.session_last_active_at,
        deep_routing_needed=deps.needs_search_or_deep_routing,
    )
    memory_writer_decision = deps.build_memory_writer_decision_for_turn(
        user_text=memory_user_text,
        answer=memory_answer,
        source=source,
        runtime_mode=mode,
        refresh_inputs=refresh_inputs,
        decision_builder=deps.build_memory_writer_decision,
    )
    decision_payload = deps.build_memory_writer_decision_payload(
        memory_writer_decision,
        source=source,
        session_key=session_key,
        raw_transcript_written=True,
        vault_mirrored=turn_write.vault_mirrored,
        identity_record_decision=turn_write.identity_record_decision,
    )
    schedule_plan = deps.plan_memory_writebehind_schedule(
        memory_writer_decision,
        mode=mode,
        guild_id=guild_id,
        session_memory_key=session_memory_key,
        room_key=room_key,
        session_key=session_key,
        decision_payload=decision_payload,
        runtime_session_key=deps.runtime_session_key,
        task_key_builder=deps.memory_writebehind_task_key,
        should_replace_task=deps.should_replace_existing_memory_task,
    )
    if schedule_plan.action == "skip":
        deps.mark_memory_writer_status(
            decision_payload,
            schedule_plan.status,
            event_path=deps.memory_writebehind_status_log,
            log=deps.log,
            writebehind_reason=schedule_plan.writebehind_reason,
        )
        return decision_payload

    if schedule_plan.action == "defer":
        deps.mark_memory_writer_status(
            decision_payload,
            schedule_plan.status,
            event_path=deps.memory_writebehind_status_log,
            log=deps.log,
            writebehind_reason=schedule_plan.writebehind_reason,
        )
        return decision_payload

    if schedule_plan.action == "batch" and schedule_plan.task_key is not None:
        memory_task_key = schedule_plan.task_key
        existing = deps.background_memory_tasks.get(memory_task_key)
        if existing is not None and not existing.done() and schedule_plan.replace_existing:
            existing.cancel()
            _track_memory_task_drain(guild_id, existing, deps=deps)

        async def _batched_memory_refresh() -> None:
            try:
                await asyncio.sleep(1.5)
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                await deps.run_memory_writebehind_steps(
                    decision_payload,
                    [
                        lambda: deps.update_long_term_memory(
                            guild_id,
                            memory_user_text,
                            memory_answer,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            source_turn_id=getattr(turn_scope, "turn_id", None),
                            turn_scope=turn_scope,
                        ),
                        lambda: deps.update_cognitive_state(
                            guild_id,
                            memory_user_text,
                            session_key=session_key,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            source=source,
                            turn_scope=turn_scope,
                        ),
                    ],
                    log=deps.log,
                    event_path=deps.memory_writebehind_status_log,
                )
            finally:
                task = deps.background_memory_tasks.get(memory_task_key)
                if task is asyncio.current_task():
                    deps.background_memory_tasks.pop(memory_task_key, None)

        deps.mark_memory_writer_status(
            decision_payload,
            schedule_plan.status,
            event_path=deps.memory_writebehind_status_log,
            log=deps.log,
            writebehind_mode=schedule_plan.writebehind_mode,
        )
        deps.background_memory_tasks[memory_task_key] = deps.create_turn_scoped_task(_batched_memory_refresh(), turn_scope=turn_scope)
        return decision_payload

    async def _memory_writebehind() -> None:
        await deps.run_memory_writebehind_steps(
            decision_payload,
            [
                lambda: deps.update_long_term_memory(
                    guild_id,
                    memory_user_text,
                    memory_answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source_turn_id=getattr(turn_scope, "turn_id", None),
                    turn_scope=turn_scope,
                ),
                lambda: deps.update_cognitive_state(
                    guild_id,
                    memory_user_text,
                    session_key=session_key,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source=source,
                    turn_scope=turn_scope,
                ),
            ],
            log=deps.log,
            event_path=deps.memory_writebehind_status_log,
        )

    deps.mark_memory_writer_status(
        decision_payload,
        schedule_plan.status,
        event_path=deps.memory_writebehind_status_log,
        log=deps.log,
        writebehind_mode=schedule_plan.writebehind_mode,
    )
    task = deps.create_turn_scoped_task(
        _memory_writebehind(),
        turn_scope=turn_scope,
    )
    memory_task_key = (
        f"guild:{guild_id}:memory-writebehind:normal:{id(task)}"
    )
    deps.background_memory_tasks[memory_task_key] = task

    def _discard_memory_task(done_task: asyncio.Task) -> None:
        if deps.background_memory_tasks.get(memory_task_key) is done_task:
            deps.background_memory_tasks.pop(memory_task_key, None)

    task.add_done_callback(_discard_memory_task)
    return decision_payload
