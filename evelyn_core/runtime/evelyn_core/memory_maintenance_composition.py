from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .memory_update_runtime import schedule_memory_update_from_runtime


@dataclass(frozen=True)
class MemoryMaintenanceCompositionDeps:
    memory_update: Callable[[], Any]
    memory_locks: dict[int, Any]
    background_vault_tasks: dict[int, Any]
    vault_last_maintenance_at: dict[int, float]
    attach_current_task: Callable[[Any | None], Any]
    detach_task: Callable[[Any | None, Any | None], None]
    run_long_term_memory_update: Callable[..., Awaitable[Any]]
    collect_memory_layers: Callable[..., Any]
    ask_summary_llm: Callable[..., Awaitable[Any]]
    is_context_size_error: Callable[[Any], bool]
    should_log_voice_timing: Callable[..., bool]
    memory_fact_limit: int
    memory_loop_limit: int
    raw_limit: int
    run_vault_maintenance_once: Callable[[int], dict[str, Any]]
    create_scoped_task: Callable[..., Any]
    lock_factory: Callable[[], Any]
    sleep: Callable[[float], Awaitable[Any]]
    to_thread: Callable[..., Awaitable[Any]]
    current_task: Callable[[], Any]
    monotonic: Callable[[], float]
    getenv: Callable[[str, str], str]
    log: Callable[..., Any]


class MemoryMaintenanceComposition:
    """Owns long-term memory updates and vault maintenance task lifecycle."""

    def __init__(self, deps: MemoryMaintenanceCompositionDeps) -> None:
        self.deps = deps

    async def update_long_term_memory(
        self,
        guild_id: int,
        user_text: str,
        answer: str,
        *,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        turn_scope: Any | None = None,
    ) -> None:
        deps = self.deps
        task = deps.attach_current_task(turn_scope)
        lock = deps.memory_locks.setdefault(guild_id, deps.lock_factory())
        try:
            async with lock:
                await deps.run_long_term_memory_update(
                    guild_id,
                    user_text,
                    answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    turn_scope=turn_scope,
                    collect_layers=deps.collect_memory_layers,
                    ask_summary_llm=deps.ask_summary_llm,
                    is_context_size_error=deps.is_context_size_error,
                    should_log_latency=deps.should_log_voice_timing,
                    memory_fact_limit=deps.memory_fact_limit,
                    memory_loop_limit=deps.memory_loop_limit,
                    raw_limit=deps.raw_limit,
                    log=deps.log,
                )
        finally:
            deps.detach_task(turn_scope, task)

    def schedule_memory_vault_maintenance(
        self, guild_id: int, *, turn_scope: Any | None = None
    ) -> None:
        deps = self.deps
        interval_sec = float(deps.getenv("MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC", "900"))
        now = deps.monotonic()
        last_run = float(deps.vault_last_maintenance_at.get(guild_id, 0.0) or 0.0)
        if now - last_run < interval_sec:
            return
        existing = deps.background_vault_tasks.get(guild_id)
        if existing is not None and not existing.done():
            return

        async def maintain_memory_vault() -> None:
            try:
                await deps.sleep(0.2)
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                result = await deps.to_thread(deps.run_vault_maintenance_once, guild_id)
                deps.vault_last_maintenance_at[guild_id] = deps.monotonic()
                if result.get("daily_consolidation"):
                    deps.log(
                        f"[MEMORY VAULT] maintenance guild={guild_id} "
                        f"version={result.get('memory_version')} "
                        f"consolidated={result.get('daily_consolidation')} "
                        f"ms={result.get('latency_ms')}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                deps.log(f"[MEMORY VAULT] maintenance failed guild={guild_id}: {exc!r}")
            finally:
                task = deps.background_vault_tasks.get(guild_id)
                if task is deps.current_task():
                    deps.background_vault_tasks.pop(guild_id, None)

        deps.background_vault_tasks[guild_id] = deps.create_scoped_task(
            maintain_memory_vault(), turn_scope=turn_scope
        )

    def schedule_memory_update(
        self,
        guild_id: int,
        user_text: str,
        answer: str,
        *,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "chat",
        user_speaker: str = "user",
        assistant_speaker: str = "Evelyn",
        session_key: str | None = None,
        turn_scope: Any | None = None,
        runtime_mode: str | None = None,
    ) -> dict[str, Any]:
        return schedule_memory_update_from_runtime(
            guild_id,
            user_text,
            answer,
            deps=self.deps.memory_update(),
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            user_speaker=user_speaker,
            assistant_speaker=assistant_speaker,
            session_key=session_key,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
        )
