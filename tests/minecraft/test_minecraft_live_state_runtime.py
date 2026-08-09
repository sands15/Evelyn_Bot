from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.minecraft_live_state_runtime import (  # noqa: E402
    ControlPageMinecraftLiveSnapshotRuntimeDeps,
    MinecraftLiveObservationRuntimeDeps,
    get_control_page_minecraft_snapshot_from_runtime,
    observe_live_minecraft_state_from_runtime,
)


class FakeClient:
    def __init__(self, *, status=None, observation=None) -> None:
        self.status_value = status
        self.observation_value = observation
        self.status_error: Exception | None = None
        self.observe_error: Exception | None = None
        self.observe_calls: list[dict] = []

    async def status(self):
        if self.status_error is not None:
            raise self.status_error
        return self.status_value

    async def observe(self, **kwargs):
        self.observe_calls.append(kwargs)
        if self.observe_error is not None:
            raise self.observe_error
        return self.observation_value


class MinecraftLiveStateRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_main_binds_live_observation_builder_with_partial(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("def build_minecraft_live_observation_runtime_deps(", source)
        self.assertIn("build_minecraft_live_observation_runtime_deps = partial(", source)
        self.assertIn("MinecraftLiveObservationRuntimeDeps,", source)

    def setUp(self) -> None:
        self.client = FakeClient()
        self.attach_calls: list[tuple[dict, dict]] = []
        self.times = iter([10.0, 10.1, 20.0, 20.1])

    def attach(self, state, **kwargs):
        self.attach_calls.append((dict(state), kwargs))
        return {**state, "snapshot_source": kwargs["source"]}

    def observation_deps(self) -> MinecraftLiveObservationRuntimeDeps:
        return MinecraftLiveObservationRuntimeDeps(
            get_minecraft_client=lambda: self.client,
            merge_voyager_status_into_state=lambda status, observed: dict(status or observed or {}),
            attach_minecraft_runtime_snapshot=self.attach,
            clean_text=lambda text: str(text).strip(),
            now=lambda: next(self.times),
            stale_after_sec=2.0,
            expired_after_sec=5.0,
        )

    async def test_status_with_context_returns_live_status_snapshot(self) -> None:
        private_error = "PRIVATE_MINECRAFT_CONTEXT:/synthetic/status-token.json"
        self.client.status_value = {
            "running": True,
            "goal": "wood",
            "last_error": private_error,
            "observation": {"position": {"x": 1}},
        }

        result = await observe_live_minecraft_state_from_runtime(77, deps=self.observation_deps())

        self.assertEqual(result["snapshot_source"], "live_status")
        self.assertEqual(result["last_error"], "minecraft_status_failed")
        self.assertEqual(self.attach_calls[0][0]["last_error"], "minecraft_status_failed")
        self.assertEqual(self.attach_calls[0][1]["last_error"], "minecraft_status_failed")
        self.assertNotIn(private_error, repr(result))
        self.assertNotIn(private_error, repr(self.attach_calls))
        self.assertEqual(self.client.observe_calls, [])
        self.assertEqual(self.attach_calls[0][1]["stale_after_sec"], 2.0)

    async def test_empty_status_falls_back_to_live_observe(self) -> None:
        private_error = "PRIVATE_MINECRAFT_OBSERVE:/synthetic/observe-token.json"
        self.client.status_value = {}
        self.client.observation_value = {
            "connected": True,
            "position": {"x": 2},
            "last_error": private_error,
        }

        result = await observe_live_minecraft_state_from_runtime(None, deps=self.observation_deps())

        self.assertEqual(result["snapshot_source"], "live_observe")
        self.assertEqual(result["last_error"], "minecraft_status_failed")
        self.assertEqual(self.attach_calls[0][0]["last_error"], "minecraft_status_failed")
        self.assertEqual(self.attach_calls[0][1]["last_error"], "minecraft_status_failed")
        self.assertNotIn(private_error, repr(result))
        self.assertNotIn(private_error, repr(self.attach_calls))
        self.assertEqual(self.client.observe_calls, [{"ensure_service": False}])

    async def test_status_and_observe_failures_return_none(self) -> None:
        self.client.status_error = RuntimeError("status down")
        self.client.observe_error = RuntimeError("observe down")

        result = await observe_live_minecraft_state_from_runtime(1, deps=self.observation_deps())

        self.assertIsNone(result)
        self.assertEqual(self.attach_calls, [])

    def snapshot_deps(self, *, fallback=None) -> ControlPageMinecraftLiveSnapshotRuntimeDeps:
        async def observe(_guild_id):
            return fallback

        return ControlPageMinecraftLiveSnapshotRuntimeDeps(
            get_minecraft_client=lambda: self.client,
            observe_live_minecraft_state=observe,
            merge_voyager_status_into_state=lambda status, observed: dict(status or observed or {}),
            normalize_inventory_top_entries=lambda inventory: list(inventory or [])[:2],
            summarize_inventory_top=lambda rows: ", ".join(str(row) for row in rows) or "empty",
            normalize_inventory_slot_entries=lambda slots, **_kwargs: list(slots or []),
            normalize_inventory_used_slots=lambda used, slots: int(used or len(slots)),
            extract_recent_activity=lambda status, **kwargs: list(status.get("activity") or [])[: kwargs["base_limit"]],
            format_position_short=lambda position: f"pos:{position}" if position else "",
            attach_minecraft_runtime_snapshot=self.attach,
            clean_text=lambda text: str(text).strip(),
            now=lambda: next(self.times),
            stale_after_sec=2.0,
            expired_after_sec=5.0,
        )

    async def test_control_snapshot_normalizes_live_status_fields(self) -> None:
        private_error = "PRIVATE_MINECRAFT_UPSTREAM:/synthetic/runner-token.json"
        self.client.status_value = {
            "connected": True,
            "goal": " wood ",
            "stage": " collect ",
            "current_task": " chop ",
            "current_task_stage": " run ",
            "last_progress_message": " one log ",
            "completed_tasks": [1, 2],
            "failed_tasks": [3],
            "last_error": private_error,
            "activity": ["a", "b", "c"],
            "observation": {
                "inventory": ["oak", "stone", "dirt"],
                "inventory_slots": [1, 2],
                "position": {"x": 3},
            },
        }

        result = await get_control_page_minecraft_snapshot_from_runtime(77, deps=self.snapshot_deps())

        self.assertEqual(result["inventory_top"], ["oak", "stone"])
        self.assertEqual(result["inventory_summary"], "oak, stone")
        self.assertEqual(result["inventory_used"], 2)
        self.assertEqual(result["recent_activity"], ["a", "b"])
        self.assertEqual(result["completed_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["goal"], "wood")
        self.assertEqual(result["position_text"], "pos:{'x': 3}")
        self.assertEqual(result["last_error"], "minecraft_status_failed")
        self.assertEqual(
            self.attach_calls[0][1]["last_error"],
            "minecraft_status_failed",
        )
        self.assertNotIn(private_error, repr(result))
        self.assertNotIn(private_error, repr(self.attach_calls))
        self.assertEqual(result["snapshot_source"], "control_page_live")

    async def test_control_snapshot_redacts_status_error_on_fallback(self) -> None:
        private_error = "PRIVATE_MINECRAFT_STATUS:/synthetic/server-token.json"
        self.client.status_error = RuntimeError(private_error)
        fallback = {"connected": True, "inventory": ["oak"]}

        result = await get_control_page_minecraft_snapshot_from_runtime(
            77,
            deps=self.snapshot_deps(fallback=fallback),
        )

        self.assertEqual(result["last_error"], "minecraft_status_failed:RuntimeError")
        self.assertEqual(
            self.attach_calls[0][1]["last_error"],
            "minecraft_status_failed:RuntimeError",
        )
        self.assertNotIn(private_error, repr(result))
        self.assertNotIn(private_error, repr(self.attach_calls))
        self.assertEqual(result["inventory_top"], ["oak"])

    def test_main_binds_minecraft_observation_and_delegates_snapshot(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        snapshot_start = composition.index("async def get_minecraft_snapshot(")
        snapshot_end = composition.index("async def safe_get_minecraft_snapshot", snapshot_start)

        snapshot_source = composition[snapshot_start:snapshot_end]
        self.assertNotIn("async def observe_live_minecraft_state(", source)
        self.assertIn("observe_live_minecraft_state = partial(", source)
        self.assertIn("observe_live_minecraft_state_from_runtime,", source)
        self.assertIn("get_control_page_minecraft_snapshot_from_runtime(", snapshot_source)
        self.assertNotIn("normalize_inventory_top_entries(", snapshot_source)
        self.assertIn("async def get_minecraft_snapshot(", composition)


if __name__ == "__main__":
    unittest.main()
