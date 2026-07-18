from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class ControlPageRuntimeServicesRuntimeDeps:
    cache: Any
    get_refresh_task: Callable[[], Any | None]
    set_refresh_task: Callable[[Any | None], None]
    get_lock: Callable[[], Any | None]
    set_lock: Callable[[Any], None]
    lock_factory: Callable[[], Any]
    create_task: Callable[[Awaitable[Any]], Any]
    probe_runtime_services_once: Callable[[], Awaitable[dict[str, Any]]]
    build_runtime_services_error_payload: Callable[..., dict[str, Any]]
    clean_text: Callable[[str], str]
    action_backend: str
    now: Callable[[], float]


@dataclass(frozen=True)
class ControlPageRuntimeServicesProbeDeps:
    service_urls: dict[str, str]
    bot_api_host: str
    bot_api_port: int
    bot_api_state_path: str
    bot_api_probe_timeout_sec: float
    action_backend: str
    codex_gateway_port: int
    voyager_alive_probe: Callable[[], Awaitable[bool]]
    probe_runtime_services_once: Callable[..., Awaitable[dict[str, Any]]]


async def probe_control_page_runtime_services_once_from_runtime(
    *,
    deps: ControlPageRuntimeServicesProbeDeps,
) -> dict[str, Any]:
    async def _voyager_alive() -> bool:
        return bool(await deps.voyager_alive_probe())

    return await deps.probe_runtime_services_once(
        service_urls=dict(deps.service_urls),
        bot_api_host=deps.bot_api_host,
        bot_api_port=deps.bot_api_port,
        bot_api_state_path=deps.bot_api_state_path,
        bot_api_probe_timeout_sec=deps.bot_api_probe_timeout_sec,
        action_backend=deps.action_backend,
        codex_gateway_port=deps.codex_gateway_port,
        voyager_alive_probe=_voyager_alive,
    )


def _task_running(task: Any | None) -> bool:
    return bool(task is not None and not task.done())


def build_control_page_runtime_services_snapshot_from_runtime(
    *,
    deps: ControlPageRuntimeServicesRuntimeDeps,
    now: float | None = None,
) -> dict[str, Any]:
    return deps.cache.snapshot_copy(refreshing=_task_running(deps.get_refresh_task()), now=now)


def can_schedule_control_page_runtime_services_refresh_from_runtime(
    *,
    deps: ControlPageRuntimeServicesRuntimeDeps,
    now: float | None = None,
) -> bool:
    return deps.cache.can_schedule_refresh(refreshing=_task_running(deps.get_refresh_task()), now=now)


async def refresh_control_page_runtime_services_cache_once_from_runtime(
    *,
    deps: ControlPageRuntimeServicesRuntimeDeps,
) -> None:
    try:
        services = await deps.probe_runtime_services_once()
    except Exception as exc:
        error_text = deps.clean_text(str(exc)) or type(exc).__name__
        services = deps.build_runtime_services_error_payload(
            error_text,
            action_backend=deps.action_backend,
        )
    deps.cache.store_success(services)


def start_control_page_runtime_services_background_refresh_from_runtime(
    *,
    deps: ControlPageRuntimeServicesRuntimeDeps,
    now: float | None = None,
) -> None:
    now_ts = deps.now() if now is None else float(now)
    if not can_schedule_control_page_runtime_services_refresh_from_runtime(deps=deps, now=now_ts):
        return
    deps.cache.mark_refresh_request(now=now_ts)
    deps.set_refresh_task(
        deps.create_task(refresh_control_page_runtime_services_cache_once_from_runtime(deps=deps))
    )


async def get_control_page_runtime_services_from_runtime(
    *,
    deps: ControlPageRuntimeServicesRuntimeDeps,
    force: bool = False,
) -> dict[str, Any]:
    lock = deps.get_lock()
    if lock is None:
        lock = deps.lock_factory()
        deps.set_lock(lock)

    async with lock:
        now_ts = deps.now()
        if deps.cache.is_fresh(now=now_ts) and not force:
            return build_control_page_runtime_services_snapshot_from_runtime(deps=deps)
        if (not force) and deps.cache.is_stale_not_expired(now=now_ts):
            start_control_page_runtime_services_background_refresh_from_runtime(deps=deps, now=now_ts)
            return build_control_page_runtime_services_snapshot_from_runtime(deps=deps)
        await refresh_control_page_runtime_services_cache_once_from_runtime(deps=deps)
        return build_control_page_runtime_services_snapshot_from_runtime(deps=deps)


__all__ = [
    "ControlPageRuntimeServicesRuntimeDeps",
    "ControlPageRuntimeServicesProbeDeps",
    "probe_control_page_runtime_services_once_from_runtime",
    "build_control_page_runtime_services_snapshot_from_runtime",
    "can_schedule_control_page_runtime_services_refresh_from_runtime",
    "get_control_page_runtime_services_from_runtime",
    "refresh_control_page_runtime_services_cache_once_from_runtime",
    "start_control_page_runtime_services_background_refresh_from_runtime",
]
