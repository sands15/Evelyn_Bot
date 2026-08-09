from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .memory_update_runtime import schedule_memory_update_from_runtime


DEFAULT_MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC = 900.0
DEFAULT_MEMORY_DERIVATION_RETRY_INTERVAL_SEC = 60.0
MIN_MEMORY_DERIVATION_RETRY_INTERVAL_SEC = 5.0


def _safe_interval(value: Any, default: float) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        return default
    return interval if math.isfinite(interval) else default


def _pending_recomposition_count(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    recomposition = result.get("derivation_recomposition")
    if not isinstance(recomposition, dict):
        return 0
    pending = recomposition.get("pendingNoteIds")
    if not isinstance(pending, list):
        return 0
    return sum(1 for note_id in pending if str(note_id or "").strip())


def _maintenance_last_run_marker(
    *,
    finished_at: float,
    interval_sec: float,
    retry_interval_sec: float,
    pending_count: int,
) -> float:
    if pending_count <= 0:
        return finished_at
    retry_delay = min(
        interval_sec,
        max(
            MIN_MEMORY_DERIVATION_RETRY_INTERVAL_SEC,
            retry_interval_sec,
        ),
    )
    return finished_at - max(0.0, interval_sec - retry_delay)


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
        source_turn_id: str | None = None,
        turn_scope: Any | None = None,
    ) -> None:
        deps = self.deps
        task = deps.attach_current_task(turn_scope)
        lock = deps.memory_locks.setdefault(guild_id, deps.lock_factory())
        try:
            async with lock:
                result = await deps.run_long_term_memory_update(
                    guild_id,
                    user_text,
                    answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source_turn_id=source_turn_id,
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
                if isinstance(result, dict) and result.get("ok") is False:
                    raise RuntimeError("long_term_memory_update_failed")
        finally:
            deps.detach_task(turn_scope, task)

    def schedule_memory_vault_maintenance(
        self, guild_id: int, *, turn_scope: Any | None = None
    ) -> None:
        deps = self.deps
        interval_sec = max(
            1.0,
            _safe_interval(
                deps.getenv(
                    "MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC",
                    str(
                        DEFAULT_MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC
                    ),
                ),
                DEFAULT_MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC,
            ),
        )
        retry_interval_sec = _safe_interval(
            deps.getenv(
                "MEMORY_DERIVATION_RETRY_INTERVAL_SEC",
                str(DEFAULT_MEMORY_DERIVATION_RETRY_INTERVAL_SEC),
            ),
            DEFAULT_MEMORY_DERIVATION_RETRY_INTERVAL_SEC,
        )
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
                pending_count = _pending_recomposition_count(
                    result
                )
                finished_at = deps.monotonic()
                # This in-memory value is the interval-gate marker, not an
                # audit timestamp. Backdating only pending recomposition
                # allows an earlier next-turn retry without a tight loop.
                deps.vault_last_maintenance_at[guild_id] = (
                    _maintenance_last_run_marker(
                        finished_at=finished_at,
                        interval_sec=interval_sec,
                        retry_interval_sec=retry_interval_sec,
                        pending_count=pending_count,
                    )
                )
                if pending_count:
                    retry_delay = min(
                        interval_sec,
                        max(
                            MIN_MEMORY_DERIVATION_RETRY_INTERVAL_SEC,
                            retry_interval_sec,
                        ),
                    )
                    deps.log(
                        "[MEMORY VAULT] derivation recomposition "
                        f"pending guild={guild_id} "
                        f"count={pending_count} "
                        f"retrySec={round(retry_delay, 1)}"
                    )
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
                deps.log(
                    f"[MEMORY VAULT] maintenance failed guild={guild_id} "
                    f"errorType={type(exc).__name__}"
                )
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
