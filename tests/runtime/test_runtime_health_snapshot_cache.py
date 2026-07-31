from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_health_snapshot_cache import (  # noqa: E402
    RUNTIME_HEALTH_CACHE_STALE_ERROR,
    RUNTIME_HEALTH_REFRESH_ERROR,
    RuntimeHealthSnapshotCache,
)


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def health_snapshot(revision: int) -> dict:
    return {
        "ok": True,
        "fullyHealthy": True,
        "coreState": "up",
        "overallState": "up",
        "summary": "ready",
        "legacyServices": {
            "botReady": True,
            "mainReady": True,
        },
        "services": [
            {
                "id": "main_llm",
                "state": "up",
                "ready": True,
                "reason": "ok",
            }
        ],
        "capabilities": {
            "voiceLocal": {
                "state": "ready",
                "ready": True,
                "blockers": [],
            }
        },
        "diagnostics": [],
        "revision": revision,
    }


class RuntimeHealthSnapshotCacheTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_stale_while_revalidate_is_single_flight(
        self,
    ) -> None:
        clock = FakeClock()
        release_refresh = asyncio.Event()
        calls = 0

        async def collect() -> dict:
            nonlocal calls
            calls += 1
            if calls == 2:
                await release_refresh.wait()
            return health_snapshot(calls)

        cache = RuntimeHealthSnapshotCache(
            collector=collect,
            refresh_after_sec=2.0,
            max_stale_sec=6.0,
            monotonic=clock,
        )
        first = await cache.get()
        clock.value += 2.1

        second = await cache.get()
        third = await cache.get()
        await asyncio.sleep(0)

        self.assertEqual(first["revision"], 1)
        self.assertEqual(second["revision"], 1)
        self.assertEqual(third["revision"], 1)
        self.assertTrue(second["cache"]["refreshing"])
        self.assertEqual(calls, 2)

        release_refresh.set()
        refreshed = await cache.refresh()

        self.assertEqual(refreshed["revision"], 2)
        self.assertFalse(refreshed["cache"]["refreshing"])

    async def test_snapshot_beyond_max_stale_waits_for_refresh(
        self,
    ) -> None:
        clock = FakeClock()
        calls = 0

        async def collect() -> dict:
            nonlocal calls
            calls += 1
            return health_snapshot(calls)

        cache = RuntimeHealthSnapshotCache(
            collector=collect,
            refresh_after_sec=2.0,
            max_stale_sec=6.0,
            monotonic=clock,
        )
        await cache.get()
        clock.value += 6.1

        refreshed = await cache.get()

        self.assertEqual(calls, 2)
        self.assertEqual(refreshed["revision"], 2)
        self.assertFalse(refreshed["cache"]["stale"])

    async def test_failed_refresh_preserves_recent_snapshot(
        self,
    ) -> None:
        clock = FakeClock()
        fail = False

        async def collect() -> dict:
            if fail:
                raise RuntimeError("private failure detail")
            return health_snapshot(1)

        cache = RuntimeHealthSnapshotCache(
            collector=collect,
            refresh_after_sec=2.0,
            max_stale_sec=6.0,
            monotonic=clock,
        )
        await cache.get()
        clock.value += 2.1
        fail = True

        snapshot = await cache.refresh()

        self.assertEqual(snapshot["revision"], 1)
        self.assertFalse(snapshot["cache"]["stale"])
        self.assertEqual(
            snapshot["cache"]["lastRefreshError"],
            RUNTIME_HEALTH_REFRESH_ERROR,
        )
        self.assertNotIn("private failure", str(snapshot))

    async def test_failed_refresh_beyond_max_stale_fails_closed(
        self,
    ) -> None:
        clock = FakeClock()
        fail = False

        async def collect() -> dict:
            if fail:
                raise RuntimeError("down")
            return health_snapshot(1)

        cache = RuntimeHealthSnapshotCache(
            collector=collect,
            refresh_after_sec=2.0,
            max_stale_sec=6.0,
            monotonic=clock,
        )
        await cache.get()
        clock.value += 6.1
        fail = True

        snapshot = await cache.get()

        self.assertFalse(snapshot["ok"])
        self.assertEqual(snapshot["overallState"], "unknown")
        self.assertTrue(snapshot["cache"]["stale"])
        self.assertFalse(snapshot["legacyServices"]["mainReady"])
        self.assertFalse(
            snapshot["capabilities"]["voiceLocal"]["ready"]
        )
        self.assertIn(
            RUNTIME_HEALTH_CACHE_STALE_ERROR,
            snapshot["capabilities"]["voiceLocal"]["blockers"],
        )
        self.assertEqual(
            snapshot["services"][0]["reason"],
            RUNTIME_HEALTH_CACHE_STALE_ERROR,
        )


if __name__ == "__main__":
    unittest.main()
