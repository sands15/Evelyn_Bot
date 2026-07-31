from __future__ import annotations

import asyncio
import contextlib
import copy
import time
from typing import Any, Awaitable, Callable


RUNTIME_HEALTH_CACHE_SCHEMA = "runtime_health.cache.v1"
RUNTIME_HEALTH_CACHE_STALE_ERROR = "runtime_health_cache_stale"
RUNTIME_HEALTH_REFRESH_ERROR = "runtime_health_refresh_failed"


def fail_closed_runtime_health_snapshot(
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Prevent an old readiness snapshot from being presented as live."""

    health = copy.deepcopy(snapshot)
    services: list[dict[str, Any]] = []
    for raw_service in health.get("services") or []:
        if not isinstance(raw_service, dict):
            continue
        service = dict(raw_service)
        service["cachedState"] = str(
            service.get("state") or "unknown"
        )
        service["state"] = "unknown"
        service["ready"] = False
        service["reason"] = RUNTIME_HEALTH_CACHE_STALE_ERROR
        services.append(service)
    health["services"] = services

    legacy = dict(health.get("legacyServices") or {})
    for key in tuple(legacy):
        if key.endswith("Ready"):
            legacy[key] = False
    legacy["summary"] = (
        "Runtime readiness is unknown because the health snapshot is stale."
    )
    health["legacyServices"] = legacy

    capabilities: dict[str, Any] = {}
    for capability_id, raw_capability in (
        health.get("capabilities") or {}
    ).items():
        if not isinstance(raw_capability, dict):
            continue
        capability = copy.deepcopy(raw_capability)
        blockers = [
            str(item)
            for item in capability.get("blockers") or []
            if str(item)
        ]
        if RUNTIME_HEALTH_CACHE_STALE_ERROR not in blockers:
            blockers.append(RUNTIME_HEALTH_CACHE_STALE_ERROR)
        capability["state"] = "unknown"
        capability["ready"] = False
        capability["blockers"] = blockers
        capabilities[str(capability_id)] = capability
    health["capabilities"] = capabilities

    diagnostics = [
        copy.deepcopy(item)
        for item in health.get("diagnostics") or []
        if isinstance(item, dict)
    ]
    diagnostics.append(
        {
            "code": "RUNTIME_HEALTH_CACHE_STALE",
            "severity": "error",
            "message": (
                "Runtime readiness is unknown because the last health "
                "snapshot exceeded its freshness boundary."
            ),
            "details": (
                "A fresh probe must succeed before readiness can be trusted."
            ),
            "serviceIds": [],
            "suggestedActions": [],
        }
    )
    health.update(
        {
            "ok": False,
            "fullyHealthy": False,
            "coreState": "unknown",
            "optionalDegraded": True,
            "overallState": "unknown",
            "summary": (
                "Runtime readiness is unknown because the health "
                "snapshot is stale."
            ),
            "diagnostics": diagnostics,
        }
    )
    return health


class RuntimeHealthSnapshotCache:
    """Single-flight stale-while-revalidate cache for health snapshots."""

    def __init__(
        self,
        *,
        collector: Callable[[], Awaitable[dict[str, Any]]],
        refresh_after_sec: float,
        max_stale_sec: float,
        monotonic: Callable[[], float] = time.monotonic,
        create_task: Callable[[Awaitable[Any]], asyncio.Task[Any]] = (
            asyncio.create_task
        ),
        stale_transform: Callable[
            [dict[str, Any]],
            dict[str, Any],
        ] = fail_closed_runtime_health_snapshot,
    ) -> None:
        self.collector = collector
        self.refresh_after_sec = max(
            0.05,
            float(refresh_after_sec),
        )
        self.max_stale_sec = max(
            self.refresh_after_sec,
            float(max_stale_sec),
        )
        self.monotonic = monotonic
        self.create_task = create_task
        self.stale_transform = stale_transform
        self._snapshot: dict[str, Any] | None = None
        self._collected_at = 0.0
        self._refresh_task: asyncio.Task[Any] | None = None
        self._last_refresh_error = ""

    def clear(self) -> None:
        task = self._refresh_task
        if task is not None and not task.done():
            with contextlib.suppress(RuntimeError):
                task.cancel()
        self._snapshot = None
        self._collected_at = 0.0
        self._refresh_task = None
        self._last_refresh_error = ""

    def _age_sec(self) -> float:
        if self._snapshot is None:
            return 0.0
        return max(0.0, self.monotonic() - self._collected_at)

    def _refreshing(self) -> bool:
        task = self._refresh_task
        return task is not None and not task.done()

    def _public_snapshot(self) -> dict[str, Any]:
        if self._snapshot is None:
            raise RuntimeError("runtime_health_snapshot_unavailable")
        age_sec = self._age_sec()
        stale = age_sec > self.max_stale_sec
        snapshot = copy.deepcopy(self._snapshot)
        if stale:
            snapshot = self.stale_transform(snapshot)
        snapshot["cache"] = {
            "schema": RUNTIME_HEALTH_CACHE_SCHEMA,
            "ageSec": round(age_sec, 3),
            "stale": stale,
            "refreshing": self._refreshing(),
            "refreshAfterSec": self.refresh_after_sec,
            "maxStaleSec": self.max_stale_sec,
            "lastRefreshError": self._last_refresh_error,
        }
        return snapshot

    async def _collect_once(self) -> None:
        try:
            snapshot = await self.collector()
            if not isinstance(snapshot, dict):
                raise TypeError("runtime_health_snapshot_invalid")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last_refresh_error = RUNTIME_HEALTH_REFRESH_ERROR
            if self._snapshot is None:
                raise
            return
        self._snapshot = copy.deepcopy(snapshot)
        self._collected_at = self.monotonic()
        self._last_refresh_error = ""

    def _finish_refresh(self, task: asyncio.Task[Any]) -> None:
        if self._refresh_task is task:
            self._refresh_task = None
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.exception()

    def _start_refresh(self) -> asyncio.Task[Any]:
        task = self._refresh_task
        if task is not None and not task.done():
            return task
        task = self.create_task(self._collect_once())
        self._refresh_task = task
        task.add_done_callback(self._finish_refresh)
        return task

    async def refresh(self) -> dict[str, Any]:
        task = self._start_refresh()
        await asyncio.shield(task)
        return self._public_snapshot()

    async def get(self, *, force: bool = False) -> dict[str, Any]:
        if self._snapshot is None:
            return await self.refresh()
        age_sec = self._age_sec()
        if force or age_sec > self.max_stale_sec:
            return await self.refresh()
        if age_sec >= self.refresh_after_sec:
            self._start_refresh()
        return self._public_snapshot()

    async def close(self) -> None:
        task = self._refresh_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if self._refresh_task is task:
            self._refresh_task = None
