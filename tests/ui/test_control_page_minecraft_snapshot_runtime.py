from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_minecraft_snapshot_runtime import (  # noqa: E402
    ControlPageBackgroundTasksRuntimeDeps,
    ControlPageMinecraftSnapshotRuntimeDeps,
    ensure_control_page_background_tasks_started_from_runtime,
    ensure_control_page_minecraft_snapshot_from_runtime,
    get_control_page_minecraft_snapshot_cache_copy_from_runtime,
    safe_get_control_page_minecraft_snapshot_from_runtime,
    refresh_control_page_minecraft_snapshot_once_from_runtime,
    stop_control_page_background_tasks_from_runtime,
)


class FakeMinecraftSnapshotCache:
    def __init__(self, *, fresh: bool = False) -> None:
        self.fresh = fresh
        self.snapshot: dict[str, object] = {"inventory_summary": "cached"}
        self.errors: list[str] = []

    def snapshot_copy(self) -> dict[str, object]:
        return dict(self.snapshot)

    def is_fresh(self) -> bool:
        return self.fresh

    def store_success(self, snapshot: dict[str, object]) -> dict[str, object]:
        self.snapshot = dict(snapshot)
        self.fresh = True
        return self.snapshot_copy()

    def store_error(self, error_text: str) -> dict[str, object]:
        self.errors.append(error_text)
        self.snapshot = {"last_error": error_text, "inventory_summary": "inventory unavailable"}
        self.fresh = False
        return self.snapshot_copy()


class ControlPageMinecraftSnapshotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(self, *, cache: FakeMinecraftSnapshotCache, get_snapshot):
        state: dict[str, object] = {"lock": None, "task": None}
        tasks: list[asyncio.Task] = []

        def create_task(coro):
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task

        deps = ControlPageMinecraftSnapshotRuntimeDeps(
            cache=cache,
            get_refresh_task=lambda: state["task"],
            set_refresh_task=lambda task: state.__setitem__("task", task),
            get_lock=lambda: state["lock"],
            set_lock=lambda lock: state.__setitem__("lock", lock),
            lock_factory=asyncio.Lock,
            create_task=create_task,
            wait_for=asyncio.wait_for,
            get_snapshot=get_snapshot,
            clean_text=lambda text: text.strip(),
            timeout_sec=0.5,
        )
        return deps, tasks, state

    async def test_none_guild_returns_cached_snapshot_without_lock(self) -> None:
        cache = FakeMinecraftSnapshotCache(fresh=False)

        async def get_snapshot(_guild_id):
            raise AssertionError("snapshot should not be fetched")

        deps, tasks, state = self.build_deps(cache=cache, get_snapshot=get_snapshot)

        snapshot = await ensure_control_page_minecraft_snapshot_from_runtime(None, deps=deps)

        self.assertEqual(snapshot["inventory_summary"], "cached")
        self.assertEqual(tasks, [])
        self.assertIsNone(state["lock"])

    async def test_fresh_cache_returns_cached_snapshot_without_task(self) -> None:
        cache = FakeMinecraftSnapshotCache(fresh=True)

        async def get_snapshot(_guild_id):
            raise AssertionError("fresh cache should not fetch")

        deps, tasks, _ = self.build_deps(cache=cache, get_snapshot=get_snapshot)

        snapshot = await ensure_control_page_minecraft_snapshot_from_runtime(7, deps=deps)

        self.assertEqual(snapshot["inventory_summary"], "cached")
        self.assertEqual(tasks, [])

    async def test_refresh_waits_and_stores_success_when_requested(self) -> None:
        cache = FakeMinecraftSnapshotCache(fresh=False)

        async def get_snapshot(guild_id):
            return {"guild_id": guild_id, "inventory_summary": "fresh"}

        deps, tasks, _ = self.build_deps(cache=cache, get_snapshot=get_snapshot)

        snapshot = await ensure_control_page_minecraft_snapshot_from_runtime(7, deps=deps, wait=True)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(snapshot["guild_id"], 7)
        self.assertEqual(snapshot["inventory_summary"], "fresh")

    async def test_refresh_once_stores_error_payload(self) -> None:
        cache = FakeMinecraftSnapshotCache(fresh=False)

        async def get_snapshot(_guild_id):
            raise RuntimeError("minecraft down")

        deps, _, _ = self.build_deps(cache=cache, get_snapshot=get_snapshot)

        snapshot = await refresh_control_page_minecraft_snapshot_once_from_runtime(7, deps=deps)

        self.assertEqual(snapshot["last_error"], "minecraft down")
        self.assertEqual(cache.errors, ["minecraft down"])

    async def test_safe_get_snapshot_returns_error_payload_on_failure(self) -> None:
        cache = FakeMinecraftSnapshotCache(fresh=False)

        async def get_snapshot(_guild_id):
            raise RuntimeError("minecraft timeout")

        deps, _, _ = self.build_deps(cache=cache, get_snapshot=get_snapshot)

        snapshot = await safe_get_control_page_minecraft_snapshot_from_runtime(
            9,
            deps=deps,
            timeout_seconds=0.01,
        )

        self.assertEqual(snapshot["last_error"], "minecraft timeout")
        self.assertEqual(snapshot["inventory_summary"], "inventory unavailable")

    async def test_safe_get_snapshot_returns_snapshot_when_successful(self) -> None:
        cache = FakeMinecraftSnapshotCache(fresh=False)

        async def get_snapshot(_guild_id):
            return {"ok": True, "guild": _guild_id}

        deps, _, _ = self.build_deps(cache=cache, get_snapshot=get_snapshot)

        snapshot = await safe_get_control_page_minecraft_snapshot_from_runtime(12, deps=deps, timeout_seconds=0.5)

        self.assertEqual(snapshot["ok"], True)
        self.assertEqual(snapshot["guild"], 12)

    async def test_background_start_primes_snapshot_and_creates_poll_task(self) -> None:
        state: dict[str, object] = {"poll": None, "snapshot": None, "services": None}
        ensured: list[tuple[int, bool, bool]] = []

        class FakeTask:
            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                pass

        def create_task(coro):
            coro.close()
            return FakeTask()

        async def ensure_snapshot(guild_id, *, force=False, wait=False):
            ensured.append((guild_id, force, wait))
            return {"ok": True}

        deps = ControlPageBackgroundTasksRuntimeDeps(
            get_poll_task=lambda: state["poll"],
            set_poll_task=lambda task: state.__setitem__("poll", task),
            get_snapshot_refresh_task=lambda: state["snapshot"],
            set_snapshot_refresh_task=lambda task: state.__setitem__("snapshot", task),
            get_runtime_services_refresh_task=lambda: state["services"],
            set_runtime_services_refresh_task=lambda task: state.__setitem__("services", task),
            create_task=create_task,
            select_control_page_guild=lambda: type("Guild", (), {"id": 77})(),
            ensure_minecraft_snapshot=ensure_snapshot,
            sleep=asyncio.sleep,
            log=lambda *_args, **_kwargs: None,
            refresh_interval_sec=1.0,
        )

        await ensure_control_page_background_tasks_started_from_runtime(deps=deps)

        self.assertEqual(ensured, [(77, True, True)])
        self.assertIsNotNone(state["poll"])

    async def test_background_stop_cancels_running_tasks_and_clears_slots(self) -> None:
        cancelled: list[str] = []

        class FakeTask:
            def __init__(self, name: str) -> None:
                self.name = name

            def done(self) -> bool:
                return False

            def cancel(self) -> None:
                cancelled.append(self.name)

        state: dict[str, object] = {
            "poll": FakeTask("poll"),
            "snapshot": FakeTask("snapshot"),
            "services": FakeTask("services"),
        }
        deps = ControlPageBackgroundTasksRuntimeDeps(
            get_poll_task=lambda: state["poll"],
            set_poll_task=lambda task: state.__setitem__("poll", task),
            get_snapshot_refresh_task=lambda: state["snapshot"],
            set_snapshot_refresh_task=lambda task: state.__setitem__("snapshot", task),
            get_runtime_services_refresh_task=lambda: state["services"],
            set_runtime_services_refresh_task=lambda task: state.__setitem__("services", task),
            create_task=lambda coro: coro,
            select_control_page_guild=lambda: None,
            ensure_minecraft_snapshot=lambda *args, **kwargs: None,
            sleep=asyncio.sleep,
            log=lambda *_args, **_kwargs: None,
            refresh_interval_sec=1.0,
        )

        stop_control_page_background_tasks_from_runtime(deps=deps)

        self.assertEqual(cancelled, ["poll", "snapshot", "services"])
        self.assertIsNone(state["poll"])
        self.assertIsNone(state["snapshot"])
        self.assertIsNone(state["services"])


if __name__ == "__main__":
    unittest.main()
