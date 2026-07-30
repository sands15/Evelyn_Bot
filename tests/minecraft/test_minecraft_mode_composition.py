from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.minecraft_mode_composition import (  # noqa: E402
    MINECRAFT_CONNECTED_OUTCOME,
    MINECRAFT_STOPPED_OUTCOME,
    MinecraftModeComposition,
    MinecraftModeCompositionDeps,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        self.value += delay


class MinecraftModeCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(
        self,
        statuses: list[dict],
        *,
        start_status: dict | None = None,
        stop_status: dict | None = None,
    ):
        remaining = [dict(row) for row in statuses]

        async def status() -> dict:
            if len(remaining) > 1:
                return remaining.pop(0)
            return dict(remaining[0]) if remaining else {}

        client = Mock()
        client.status = AsyncMock(side_effect=status)
        client.start = AsyncMock(
            return_value=(
                dict(start_status)
                if start_status is not None
                else {"voyager_repo_present": True}
            )
        )
        client.stop = AsyncMock(
            return_value=(
                dict(stop_status)
                if stop_status is not None
                else {"running": False, "connected": False}
            )
        )
        clock = FakeClock()
        deps = MinecraftModeCompositionDeps(
            get_client=lambda: client,
            merge_status=lambda _status, observed: dict(observed or {}),
            clean_text=str.strip,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        return MinecraftModeComposition(deps), client, clock

    async def test_wait_returns_connected_observation(self) -> None:
        composition, _, _ = self.build(
            [{"connected": True, "position": {"x": 1}}]
        )

        result = await composition.wait_for_minecraft_ready(1)

        self.assertTrue(result["connected"])

    async def test_wait_does_not_treat_position_alone_as_connection(self) -> None:
        composition, _, _ = self.build(
            [{"connected": False, "position": {"x": 1}}]
        )

        result = await composition.wait_for_minecraft_ready(
            1,
            timeout_sec=0.5,
            poll_sec=0.1,
        )

        self.assertFalse(result["connected"])
        self.assertNotIn("outcome_verified", result)

    async def test_enable_returns_explicit_verified_connection(self) -> None:
        composition, client, _ = self.build([{"connected": True}])

        result = await composition.enable_minecraft_mode(1, "goal")

        self.assertTrue(result["voyager_repo_present"])
        self.assertTrue(result["outcome_verified"])
        self.assertEqual(
            result["outcome_code"],
            MINECRAFT_CONNECTED_OUTCOME,
        )
        client.start.assert_awaited_once_with(goal="goal")

    async def test_enable_rejects_unverified_start(self) -> None:
        composition, _, _ = self.build(
            [{"running": True, "connected": False, "position": {"x": 1}}]
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_start_unverified",
        ):
            await composition.enable_minecraft_mode(1)

    async def test_disable_returns_explicit_verified_stop(self) -> None:
        composition, client, _ = self.build(
            [],
            stop_status={"running": False, "connected": False},
        )

        result = await composition.disable_minecraft_mode(1)

        self.assertTrue(result["outcome_verified"])
        self.assertEqual(
            result["outcome_code"],
            MINECRAFT_STOPPED_OUTCOME,
        )
        client.stop.assert_awaited_once_with()
        client.status.assert_not_awaited()

    async def test_disable_waits_for_observed_stop(self) -> None:
        composition, client, _ = self.build(
            [
                {"running": True, "connected": True},
                {"running": False, "connected": False},
            ],
            stop_status={"running": True, "connected": True},
        )

        result = await composition.disable_minecraft_mode(1)

        self.assertTrue(result["outcome_verified"])
        self.assertEqual(client.status.await_count, 2)

    async def test_disable_rejects_unverified_stop(self) -> None:
        composition, _, _ = self.build(
            [{"running": True, "connected": True}],
            stop_status={"running": True, "connected": True},
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_stop_unverified",
        ):
            await composition.disable_minecraft_mode(1)

    def test_main_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            "minecraft_mode_composition = MinecraftModeComposition(",
            source,
        )
        self.assertIn(
            (
                "enable_minecraft_mode = "
                "minecraft_mode_composition.enable_minecraft_mode"
            ),
            source,
        )


if __name__ == "__main__":
    unittest.main()
