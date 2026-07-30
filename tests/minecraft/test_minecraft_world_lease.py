from __future__ import annotations

import json
import sys
import tempfile
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

from evelyn_core.minecraft_world_lease import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
    STOP_RETRY_LIMIT,
    MinecraftWorldLeaseOwner,
)


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class RunningTask:
    @staticmethod
    def done() -> bool:
        return False


class FakeMinecraftRuntime:
    def __init__(self) -> None:
        self.statuses: list[object] = [
            {"running": False, "connected": False}
        ]
        self.enable_result: object = {
            "connected": True,
            "outcome_verified": True,
            "outcome_code": "minecraft_connected",
        }
        self.disable_result: object = {
            "running": False,
            "connected": False,
            "outcome_verified": True,
            "outcome_code": "minecraft_stopped",
        }
        self.goal_result: object = {
            "goal": "diamond",
            "outcome_verified": True,
            "outcome_code": "minecraft_goal_confirmed",
        }
        self.calls: list[tuple[str, object]] = []

    async def status(self) -> dict:
        self.calls.append(("status", None))
        if len(self.statuses) > 1:
            value = self.statuses.pop(0)
        else:
            value = self.statuses[0]
        if isinstance(value, BaseException):
            raise value
        return dict(value)

    async def enable(
        self,
        guild_id: int,
        *,
        goal: str | None = None,
        world_lease: dict | None = None,
    ) -> dict:
        self.calls.append(
            (
                "enable",
                (
                    guild_id,
                    goal,
                    dict(world_lease or {}),
                ),
            )
        )
        if isinstance(self.enable_result, BaseException):
            raise self.enable_result
        return dict(self.enable_result)

    async def disable(self, guild_id: int) -> dict:
        self.calls.append(("disable", guild_id))
        if isinstance(self.disable_result, BaseException):
            raise self.disable_result
        return dict(self.disable_result)

    async def set_goal(
        self,
        goal: str,
        *,
        world_lease: dict | None = None,
    ) -> dict:
        self.calls.append(
            ("goal", (goal, dict(world_lease or {})))
        )
        if isinstance(self.goal_result, BaseException):
            raise self.goal_result
        result = dict(self.goal_result)
        result["goal"] = goal
        return result


class MinecraftWorldLeaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.clock = FakeClock()
        self.runtime = FakeMinecraftRuntime()
        self.owner = MinecraftWorldLeaseOwner(
            status_path=self.root / "status.json",
            events_dir=self.root / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            default_ttl_sec=60.0,
            max_ttl_sec=60.0,
            watchdog_interval_sec=1.0,
            log=lambda *_args: None,
        )
        self.owner.initialize()
        self.owner._watchdog_task = RunningTask()

    def read_events(self) -> list[dict]:
        rows: list[dict] = []
        for path in sorted((self.root / "events").glob("*.jsonl")):
            rows.extend(
                json.loads(line)
                for line in path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
        return rows

    async def connect(self, guild_id: int = 7) -> dict:
        return await self.owner.connect(
            guild_id,
            issuer_ref="discord_user:123",
            source="discord_command",
            goal="diamond",
            ttl_sec=60.0,
        )

    async def test_connect_issues_single_process_lease(self) -> None:
        result = await self.connect()

        status = self.owner.status()
        self.assertEqual(
            status["schema"],
            MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
        )
        self.assertTrue(status["active"])
        self.assertEqual(status["lease"]["guildId"], 7)
        self.assertNotIn("discord_user:123", json.dumps(status))
        self.assertEqual(result["worldLease"]["guildId"], 7)
        self.assertNotIn(
            self.owner.authorization_token,
            json.dumps(status),
        )
        self.assertNotIn(
            self.owner.authorization_token,
            json.dumps(result),
        )
        enable = next(
            call
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        self.assertEqual(enable[1][0:2], (7, "diamond"))
        self.assertEqual(
            enable[1][2]["leaseId"],
            status["lease"]["leaseId"],
        )
        self.assertEqual(
            enable[1][2]["processNonce"],
            status["processNonce"],
        )
        self.assertEqual(
            enable[1][2]["authorizationToken"],
            self.owner.authorization_token,
        )
        secret_payload = json.loads(
            self.owner.secret_path.read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            secret_payload["authorizationToken"],
            self.owner.authorization_token,
        )

    async def test_process_restart_does_not_restore_lease(self) -> None:
        await self.connect()
        previous_token = self.owner.authorization_token
        replacement = MinecraftWorldLeaseOwner(
            status_path=self.root / "status.json",
            events_dir=self.root / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )

        status = replacement.initialize()

        self.assertFalse(status["active"])
        self.assertEqual(status["state"], "authorization_required")
        self.assertNotEqual(
            replacement.authorization_token,
            previous_token,
        )

    async def test_restart_reconcile_stops_stale_runner(self) -> None:
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]

        result = await self.owner.reconcile_once(
            reason="process_restart",
            force_stop=True,
        )

        self.assertTrue(result["stopped"])
        self.assertIn(("disable", 0), self.runtime.calls)
        self.assertEqual(
            self.owner.status()["lastStopOutcome"],
            "minecraft_stopped",
        )

    async def test_connect_stops_unauthorized_runtime_first(
        self,
    ) -> None:
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]

        await self.connect()

        disable_index = self.runtime.calls.index(("disable", 0))
        enable_index = next(
            index
            for index, call in enumerate(self.runtime.calls)
            if call[0] == "enable"
        )
        self.assertLess(disable_index, enable_index)

    async def test_expired_lease_stops_runtime(self) -> None:
        await self.connect()
        self.runtime.calls.clear()
        self.runtime.statuses = [
            {"running": False, "connected": False}
        ]
        self.clock.value = 1061.0

        result = await self.owner.reconcile_once()

        self.assertTrue(result["stopped"])
        self.assertFalse(self.owner.status()["active"])
        self.assertIn(("disable", 7), self.runtime.calls)
        reasons = [row["reasonCode"] for row in self.read_events()]
        self.assertIn("lease_expired", reasons)

    async def test_goal_requires_active_matching_lease(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await self.owner.set_goal(7, "diamond")

        await self.connect()
        result = await self.owner.set_goal(
            7,
            "private_goal_text",
        )

        self.assertTrue(result["outcome_verified"])
        serialized = json.dumps(self.read_events())
        self.assertNotIn("private_goal_text", serialized)

    async def test_disconnect_rejects_other_guild_owner(self) -> None:
        await self.connect(guild_id=7)

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_owner_mismatch",
        ):
            await self.owner.disconnect(8)

        self.assertTrue(self.owner.status()["active"])

    async def test_connect_failure_revokes_and_stops(self) -> None:
        self.runtime.enable_result = RuntimeError("start failed")
        self.runtime.statuses = [
            {"running": False, "connected": False}
        ]

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            await self.connect()

        self.assertFalse(self.owner.status()["active"])
        self.assertIn(("disable", 7), self.runtime.calls)

    async def test_status_failure_does_not_assume_runtime_stopped(
        self,
    ) -> None:
        self.runtime.statuses = [
            RuntimeError("status down"),
            {"running": False, "connected": False},
        ]

        result = await self.owner.reconcile_once(
            reason="process_restart",
            force_stop=True,
        )

        self.assertEqual(
            result["action"],
            "stop_status_unknown_runtime",
        )
        self.assertTrue(result["stopped"])
        self.assertIn(("disable", 0), self.runtime.calls)

    async def test_stop_retry_budget_requires_manual_intervention(
        self,
    ) -> None:
        self.runtime.disable_result = {
            "running": True,
            "connected": True,
            "outcome_verified": False,
        }
        self.runtime.statuses = [
            {"running": True, "connected": True}
        ]

        for _ in range(STOP_RETRY_LIMIT + 1):
            await self.owner.reconcile_once()

        status = self.owner.status()
        self.assertTrue(status["manualInterventionRequired"])
        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_stop_retry_budget_exhausted",
        )


if __name__ == "__main__":
    unittest.main()
