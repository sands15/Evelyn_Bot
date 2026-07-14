from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import unittest
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_runtime_services_runtime import (  # noqa: E402
    ControlPageRuntimeServicesRuntimeDeps,
    ControlPageRuntimeServicesProbeDeps,
    get_control_page_runtime_services_from_runtime,
    probe_control_page_runtime_services_once_from_runtime,
)


class FakeRuntimeServicesCache:
    def __init__(self, mode: str = "expired") -> None:
        self.mode = mode
        self.services: dict[str, object] = {"main": {"ready": True}}
        self.refresh_requests: list[float | None] = []
        self.stored: list[dict[str, object]] = []

    def snapshot_copy(self, *, refreshing: bool = False, now: float | None = None) -> dict[str, object]:
        return {
            "refreshing": bool(refreshing),
            "now": now,
            "services": dict(self.services),
        }

    def is_fresh(self, *, now: float | None = None) -> bool:
        return self.mode == "fresh"

    def is_stale_not_expired(self, *, now: float | None = None) -> bool:
        return self.mode == "stale"

    def can_schedule_refresh(self, *, refreshing: bool = False, now: float | None = None) -> bool:
        return not refreshing

    def mark_refresh_request(self, *, now: float | None = None) -> None:
        self.refresh_requests.append(now)

    def store_success(self, services: dict[str, object]) -> dict[str, object]:
        self.services = dict(services)
        self.stored.append(dict(services))
        self.mode = "fresh"
        return self.snapshot_copy()


class ControlPageRuntimeServicesRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        *,
        cache: FakeRuntimeServicesCache,
        probe,
    ) -> tuple[ControlPageRuntimeServicesRuntimeDeps, list[asyncio.Task], dict[str, object]]:
        state: dict[str, object] = {"lock": None, "task": None}
        tasks: list[asyncio.Task] = []

        def create_task(coro):
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task

        deps = ControlPageRuntimeServicesRuntimeDeps(
            cache=cache,
            get_refresh_task=lambda: state["task"],
            set_refresh_task=lambda task: state.__setitem__("task", task),
            get_lock=lambda: state["lock"],
            set_lock=lambda lock: state.__setitem__("lock", lock),
            lock_factory=asyncio.Lock,
            create_task=create_task,
            probe_runtime_services_once=probe,
            build_runtime_services_error_payload=lambda error_text, *, action_backend: {
                "error": error_text,
                "backend": action_backend,
            },
            clean_text=lambda text: text.strip(),
            action_backend="voyager",
            now=lambda: 10.0,
        )
        return deps, tasks, state

    async def test_fresh_cache_returns_snapshot_without_probe(self) -> None:
        cache = FakeRuntimeServicesCache(mode="fresh")
        probe_calls = 0

        async def probe():
            nonlocal probe_calls
            probe_calls += 1
            return {"main": {"ready": False}}

        deps, _, _ = self.build_deps(cache=cache, probe=probe)

        snapshot = await get_control_page_runtime_services_from_runtime(deps=deps)

        self.assertEqual(probe_calls, 0)
        self.assertFalse(snapshot["refreshing"])
        self.assertTrue(snapshot["services"]["main"]["ready"])

    async def test_stale_cache_schedules_background_refresh_and_returns_snapshot(self) -> None:
        cache = FakeRuntimeServicesCache(mode="stale")

        async def probe():
            await asyncio.sleep(0.01)
            return {"main": {"ready": False}}

        deps, tasks, _ = self.build_deps(cache=cache, probe=probe)

        snapshot = await get_control_page_runtime_services_from_runtime(deps=deps)

        self.assertEqual(cache.refresh_requests, [10.0])
        self.assertTrue(snapshot["refreshing"])
        self.assertEqual(len(tasks), 1)
        await tasks[0]
        self.assertFalse(cache.services["main"]["ready"])

    async def test_expired_cache_refreshes_inline_and_stores_error_payload(self) -> None:
        cache = FakeRuntimeServicesCache(mode="expired")

        async def probe():
            raise RuntimeError("service down")

        deps, tasks, _ = self.build_deps(cache=cache, probe=probe)

        snapshot = await get_control_page_runtime_services_from_runtime(deps=deps)

        self.assertEqual(tasks, [])
        self.assertFalse(snapshot["refreshing"])
        self.assertEqual(cache.services["error"], "service down")
        self.assertEqual(cache.services["backend"], "voyager")

    async def test_probe_runtime_services_once_from_runtime_invokes_voyager_probe(self) -> None:
        calls = {"voyager": 0}

        async def voyager_alive() -> bool:
            calls["voyager"] += 1
            return True

        async def probe_runtime_services_once(**kwargs) -> dict[str, Any]:
            return {
                "voyager_ready": await kwargs["voyager_alive_probe"](),
                "bot_api_host": kwargs["bot_api_host"],
                "bot_api_port": kwargs["bot_api_port"],
            }

        deps = ControlPageRuntimeServicesProbeDeps(
            service_urls={"main": "http://127.0.0.1:8080"},
            bot_api_host="127.0.0.1",
            bot_api_port=8798,
            bot_api_state_path="/state",
            bot_api_probe_timeout_sec=0.12,
            action_backend="voyager",
            codex_gateway_port=9820,
            voyager_alive_probe=voyager_alive,
            probe_runtime_services_once=probe_runtime_services_once,
        )

        result = await probe_control_page_runtime_services_once_from_runtime(deps=deps)

        self.assertEqual(calls["voyager"], 1)
        self.assertEqual(result["voyager_ready"], True)
        self.assertEqual(result["bot_api_host"], "127.0.0.1")
        self.assertEqual(result["bot_api_port"], 8798)


if __name__ == "__main__":
    unittest.main()
