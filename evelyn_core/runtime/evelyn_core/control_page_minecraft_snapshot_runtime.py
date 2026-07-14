from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ControlPageMinecraftSnapshotRuntimeDeps:
    cache: Any
    get_refresh_task: Callable[[], Any | None]
    set_refresh_task: Callable[[Any | None], None]
    get_lock: Callable[[], Any | None]
    set_lock: Callable[[Any], None]
    lock_factory: Callable[[], Any]
    create_task: Callable[[Awaitable[Any]], Any]
    wait_for: Callable[..., Awaitable[Any]]
    get_snapshot: Callable[[int | None], Awaitable[dict[str, Any]]]
    clean_text: Callable[[str], str]
    timeout_sec: float


@dataclass(frozen=True)
class ControlPageBackgroundTasksRuntimeDeps:
    get_poll_task: Callable[[], Any | None]
    set_poll_task: Callable[[Any | None], None]
    get_snapshot_refresh_task: Callable[[], Any | None]
    set_snapshot_refresh_task: Callable[[Any | None], None]
    get_runtime_services_refresh_task: Callable[[], Any | None]
    set_runtime_services_refresh_task: Callable[[Any | None], None]
    create_task: Callable[[Awaitable[Any]], Any]
    select_control_page_guild: Callable[[], Any | None]
    ensure_minecraft_snapshot: Callable[..., Awaitable[dict[str, Any]]]
    sleep: Callable[[float], Awaitable[Any]]
    log: Callable[..., Any]
    refresh_interval_sec: float


def _task_running(task: Any | None) -> bool:
    return bool(task is not None and not task.done())


def get_control_page_minecraft_snapshot_cache_copy_from_runtime(
    *,
    deps: ControlPageMinecraftSnapshotRuntimeDeps,
) -> dict[str, Any]:
    return deps.cache.snapshot_copy()


async def safe_get_control_page_minecraft_snapshot_from_runtime(
    guild_id: int | None,
    *,
    deps: ControlPageMinecraftSnapshotRuntimeDeps,
    timeout_seconds: float = 0.75,
) -> dict[str, Any]:
    try:
        return await deps.wait_for(
            deps.get_snapshot(guild_id),
            timeout=max(0.0, float(timeout_seconds)),
        )
    except Exception as exc:
        return {
            "last_error": deps.clean_text(str(exc)) or repr(exc),
            "inventory_top": [],
            "inventory_summary": "inventory unavailable",
            "recent_activity": [],
        }


async def refresh_control_page_minecraft_snapshot_once_from_runtime(
    guild_id: int | None,
    *,
    deps: ControlPageMinecraftSnapshotRuntimeDeps,
) -> dict[str, Any]:
    try:
        snapshot = await deps.wait_for(
            deps.get_snapshot(guild_id),
            timeout=max(0.5, float(deps.timeout_sec)),
        )
    except Exception as exc:
        error_text = deps.clean_text(str(exc)) or repr(exc)
        return deps.cache.store_error(error_text)

    return deps.cache.store_success(snapshot)


async def ensure_control_page_minecraft_snapshot_from_runtime(
    guild_id: int | None,
    *,
    deps: ControlPageMinecraftSnapshotRuntimeDeps,
    force: bool = False,
    wait: bool = False,
) -> dict[str, Any]:
    if guild_id is None:
        return get_control_page_minecraft_snapshot_cache_copy_from_runtime(deps=deps)

    lock = deps.get_lock()
    if lock is None:
        lock = deps.lock_factory()
        deps.set_lock(lock)

    async with lock:
        if not force and deps.cache.is_fresh():
            return get_control_page_minecraft_snapshot_cache_copy_from_runtime(deps=deps)
        task = deps.get_refresh_task()
        if task is None or task.done():
            task = deps.create_task(refresh_control_page_minecraft_snapshot_once_from_runtime(guild_id, deps=deps))
            deps.set_refresh_task(task)

    if wait:
        try:
            await task
        except Exception:
            pass
    return get_control_page_minecraft_snapshot_cache_copy_from_runtime(deps=deps)


async def control_page_minecraft_snapshot_poller_from_runtime(
    *,
    deps: ControlPageBackgroundTasksRuntimeDeps,
) -> None:
    while True:
        try:
            guild = deps.select_control_page_guild()
            if guild is not None:
                await deps.ensure_minecraft_snapshot(guild.id, force=True, wait=True)
        except Exception as exc:
            if type(exc).__name__ == "CancelledError":
                raise
            deps.log(f"[CONTROL PAGE] minecraft_snapshot_poll_failed err={exc!r}")
        await deps.sleep(max(0.5, float(deps.refresh_interval_sec)))


async def ensure_control_page_background_tasks_started_from_runtime(
    *,
    deps: ControlPageBackgroundTasksRuntimeDeps,
) -> None:
    if _task_running(deps.get_poll_task()):
        return
    guild = deps.select_control_page_guild()
    if guild is not None:
        await deps.ensure_minecraft_snapshot(guild.id, force=True, wait=True)
    deps.set_poll_task(deps.create_task(control_page_minecraft_snapshot_poller_from_runtime(deps=deps)))


def stop_control_page_background_tasks_from_runtime(
    *,
    deps: ControlPageBackgroundTasksRuntimeDeps,
) -> None:
    for task in (
        deps.get_poll_task(),
        deps.get_snapshot_refresh_task(),
        deps.get_runtime_services_refresh_task(),
    ):
        if _task_running(task):
            task.cancel()
    deps.set_poll_task(None)
    deps.set_snapshot_refresh_task(None)
    deps.set_runtime_services_refresh_task(None)


__all__ = [
    "ControlPageBackgroundTasksRuntimeDeps",
    "ControlPageMinecraftSnapshotRuntimeDeps",
    "control_page_minecraft_snapshot_poller_from_runtime",
    "ensure_control_page_background_tasks_started_from_runtime",
    "ensure_control_page_minecraft_snapshot_from_runtime",
    "get_control_page_minecraft_snapshot_cache_copy_from_runtime",
    "safe_get_control_page_minecraft_snapshot_from_runtime",
    "refresh_control_page_minecraft_snapshot_once_from_runtime",
    "stop_control_page_background_tasks_from_runtime",
]
