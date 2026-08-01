from __future__ import annotations

import json
import asyncio
import sys
import tempfile
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

from evelyn_core.minecraft_action_contract import (  # noqa: E402
    MINECRAFT_ACTION_DISPATCH_SCHEMA,
    MINECRAFT_ACTION_REQUEST_SCHEMA,
    MINECRAFT_ACTION_RESULT_SCHEMA,
)
from evelyn_core.minecraft_world_lease import (  # noqa: E402
    MinecraftWorldLeaseOwner,
    build_local_minecraft_world_lease_owner,
)


class RunningTask:
    @staticmethod
    def done() -> bool:
        return False


def action_request() -> dict:
    return {
        "schema": MINECRAFT_ACTION_REQUEST_SCHEMA,
        "guildId": 7,
        "actionKey": "minecraft:find_food_source",
        "actionRunId": "action-run-1",
        "authorizationGrantId": "grant-1",
        "contractCode": "mindcraft_food_recovery.v1",
        "parameters": {},
    }


def dispatch_for(request: dict, status: str = "accepted") -> dict:
    return {
        "schema": MINECRAFT_ACTION_DISPATCH_SCHEMA,
        "status": status,
        "guildId": request["guildId"],
        "actionKey": request["actionKey"],
        "actionRunId": request["actionRunId"],
        "authorizationGrantId": request["authorizationGrantId"],
        "goalRunId": request["goalRunId"],
        "leaseId": request["leaseId"],
        "leaseProcessNonce": request["leaseProcessNonce"],
        "contractCode": request["contractCode"],
        "accepted": status in {"accepted", "running"},
        "contentFree": True,
        "errorCode": (
            "" if status in {"accepted", "running"}
            else "minecraft_action_failed"
        ),
    }


def result_for(request: dict) -> dict:
    return {
        "schema": MINECRAFT_ACTION_RESULT_SCHEMA,
        "status": "completed",
        "guildId": request["guildId"],
        "actionKey": request["actionKey"],
        "actionRunId": request["actionRunId"],
        "authorizationGrantId": request["authorizationGrantId"],
        "goalRunId": request["goalRunId"],
        "leaseId": request["leaseId"],
        "leaseProcessNonce": request["leaseProcessNonce"],
        "contractCode": request["contractCode"],
        "postconditionCode": "food_reserve_ready",
        "evidenceCode": "minecraft_find_food_source_completed",
        "verified": True,
        "contentFree": True,
    }


class FakeActionRuntime:
    def __init__(self) -> None:
        self.bound: dict | None = None
        self.calls: list[tuple[str, object]] = []
        self.status_payload: dict | None = None
        self.dispatch_payload: dict | None = None

    async def status(self) -> dict:
        return {"running": False, "connected": False}

    async def enable(self, _guild_id: int, **_kwargs) -> dict:
        return {
            "connected": True,
            "outcome_verified": True,
            "outcome_code": "minecraft_connected",
        }

    async def disable(self, _guild_id: int) -> dict:
        self.calls.append(("stop", None))
        return {
            "running": False,
            "connected": False,
            "outcome_verified": True,
            "outcome_code": "minecraft_stopped",
        }

    async def goal(self, goal: str, **_kwargs) -> dict:
        return {
            "goal": goal,
            "outcome_verified": True,
            "outcome_code": "minecraft_goal_confirmed",
        }

    async def dispatch(
        self,
        request: dict,
        *,
        world_lease: dict,
    ) -> dict:
        self.bound = dict(request)
        self.calls.append(
            (
                "dispatch",
                (dict(request), dict(world_lease)),
            )
        )
        return dict(
            self.dispatch_payload
            or dispatch_for(request)
        )

    async def action_status(self, goal_run_id: str) -> dict:
        self.calls.append(("status", goal_run_id))
        assert self.bound is not None
        return dict(
            self.status_payload or result_for(self.bound)
        )

    async def cancel(
        self,
        request: dict,
        *,
        world_lease: dict,
    ) -> dict:
        self.calls.append(
            (
                "cancel",
                (dict(request), dict(world_lease)),
            )
        )
        return dispatch_for(request, "cancelled")


class MinecraftWorldLeaseOwnerFactoryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_factory_wires_all_typed_client_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            client = Mock()
            client.status = AsyncMock(return_value={"running": False})
            client.set_goal = AsyncMock(return_value={"goal": "fixed"})
            client.dispatch_action = AsyncMock(return_value={"status": "accepted"})
            client.action_status = AsyncMock(return_value={"status": "running"})
            client.cancel_action = AsyncMock(return_value={"status": "cancelled"})
            owner = build_local_minecraft_world_lease_owner(
                status_path=Path(temp_dir) / "status.json",
                events_dir=Path(temp_dir) / "events",
                get_client=lambda: client,
                enable_mode=AsyncMock(),
                disable_mode=AsyncMock(),
                log=lambda *_args: None,
            )

            self.assertEqual(await owner.get_runtime_status(), {"running": False})
            await owner.set_goal_callback("fixed", world_lease={"leaseId": "lease-1"})
            await owner.dispatch_action_callback({"request": True}, world_lease={})
            await owner.get_action_status_callback("goal-run-1")
            await owner.cancel_action_callback({"request": True}, world_lease={})
            client.set_goal.assert_awaited_once_with(
                "fixed",
                world_lease={"leaseId": "lease-1"},
            )
            client.dispatch_action.assert_awaited_once_with(
                {"request": True},
                world_lease={},
            )
            client.action_status.assert_awaited_once_with("goal-run-1")
            client.cancel_action.assert_awaited_once_with(
                {"request": True},
                world_lease={},
            )


class MinecraftActionTransportTests(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.runtime = FakeActionRuntime()
        self.owner = MinecraftWorldLeaseOwner(
            status_path=root / "status.json",
            events_dir=root / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.goal,
            dispatch_action=self.runtime.dispatch,
            get_action_status=self.runtime.action_status,
            cancel_action=self.runtime.cancel,
            action_poll_interval_sec=0.01,
            action_timeout_sec=1.0,
            log=lambda *_args: None,
        )
        self.events_dir = root / "events"
        self.owner.initialize()
        self.addCleanup(self.owner._owner_lock.release)
        self.owner._watchdog_task = RunningTask()
        await self.owner.connect(
            7,
            issuer_ref="discord_user:1",
            source="discord_command",
        )

    def events(self) -> list[dict]:
        rows: list[dict] = []
        for path in sorted(self.events_dir.glob("*.jsonl")):
            rows.extend(
                json.loads(line)
                for line in path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
        return rows

    async def test_execute_binds_and_verifies_one_exact_world_effect(
        self,
    ) -> None:
        result = await self.owner.execute_action(
            7,
            action_request(),
        )

        self.assertEqual(
            result["actionRunId"],
            "action-run-1",
        )
        self.assertTrue(result["verified"])
        bound, proof = next(
            value
            for name, value in self.runtime.calls
            if name == "dispatch"
        )
        self.assertEqual(bound["leaseId"], proof["leaseId"])
        self.assertEqual(
            bound["leaseProcessNonce"],
            proof["processNonce"],
        )
        self.assertEqual(
            next(
                value
                for name, value in self.runtime.calls
                if name == "status"
            ),
            bound["goalRunId"],
        )
        action_events = [
            row for row in self.events()
            if row["event"].startswith("action_")
        ]
        self.assertEqual(
            [row["event"] for row in action_events],
            [
                "action_dispatch_attempted",
                "action_dispatch_verified",
                "action_completed",
            ],
        )
        serialized = json.dumps(action_events).lower()
        for forbidden in (
            "transcript",
            "rawgoal",
            "rawarguments",
            '"goal":',
            '"command":',
        ):
            self.assertNotIn(forbidden, serialized)

    async def test_mismatched_result_is_cancelled_and_never_completed(
        self,
    ) -> None:
        await self.owner.dispatch_action(7, action_request())
        assert self.runtime.bound is not None
        mismatch = result_for(self.runtime.bound)
        mismatch["goalRunId"] = "other-goal"
        self.runtime.status_payload = mismatch

        with self.assertRaises(ValueError):
            await self.owner.action_status(
                7,
                goal_run_id=self.runtime.bound["goalRunId"],
                action_run_id="action-run-1",
                action_key="minecraft:find_food_source",
                contract_code="mindcraft_food_recovery.v1",
            )
        await self.owner.cancel_action(7, "action-run-1")

        self.assertNotIn(
            "action_completed",
            [row["event"] for row in self.events()],
        )
        self.assertEqual(
            sum(name == "cancel" for name, _ in self.runtime.calls),
            1,
        )

    async def test_cancel_rejects_wrong_correlation_before_runtime(
        self,
    ) -> None:
        await self.owner.dispatch_action(7, action_request())

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_correlation_mismatch",
        ):
            await self.owner.cancel_action(8, "action-run-1")
        self.assertEqual(
            sum(name == "cancel" for name, _ in self.runtime.calls),
            0,
        )

        cancelled = await self.owner.cancel_action(
            7,
            "action-run-1",
        )
        self.assertEqual(cancelled["status"], "cancelled")

    async def test_dispatch_mismatch_triggers_fail_closed_cancel(
        self,
    ) -> None:
        original = self.runtime.dispatch

        async def mismatch(request, *, world_lease):
            result = await original(
                request,
                world_lease=world_lease,
            )
            result["actionRunId"] = "other-run"
            return result

        self.owner.dispatch_action_callback = mismatch

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_dispatch_unverified",
        ):
            await self.owner.dispatch_action(
                7,
                action_request(),
            )
        self.assertEqual(self.owner._inflight_actions, {})
        self.assertEqual(
            sum(name == "cancel" for name, _ in self.runtime.calls),
            1,
        )

    async def test_unverified_cancel_forces_runtime_stop(
        self,
    ) -> None:
        await self.owner.dispatch_action(7, action_request())

        async def not_cancelled(request, *, world_lease):
            _ = world_lease
            return dispatch_for(request, "running")

        self.owner.cancel_action_callback = not_cancelled
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await self.owner.cancel_action(7, "action-run-1")

        self.assertIn(
            "stop",
            [name for name, _ in self.runtime.calls],
        )
        self.assertFalse(self.owner.status()["active"])
        self.assertNotIn(
            "action_cancel_verified",
            [row["event"] for row in self.events()],
        )

    async def test_shutdown_cancels_known_action_before_lock_handoff(
        self,
    ) -> None:
        await self.owner.dispatch_action(7, action_request())
        self.owner._watchdog_task = None
        order: list[str] = []
        original_cancel = self.owner.cancel_action_callback

        async def releasing_cancel(request, *, world_lease):
            order.append("cancel")
            return await original_cancel(
                request,
                world_lease=world_lease,
            )

        class ServiceHeldLock:
            acquired = False

            def acquire(inner_self) -> None:
                order.append("lock")
                if "cancel" not in order:
                    raise RuntimeError("lock acquired before cancel")
                inner_self.acquired = True

            def release(inner_self) -> None:
                inner_self.acquired = False

        self.owner.cancel_action_callback = releasing_cancel
        self.owner._world_action_lock = ServiceHeldLock()

        result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertEqual(order[:2], ["cancel", "lock"])
        self.assertFalse(self.owner._owner_lock.acquired)

    async def test_shutdown_lock_wait_is_bounded_after_cancel_timeout(
        self,
    ) -> None:
        await self.owner.dispatch_action(7, action_request())
        self.owner._watchdog_task = None
        self.owner.action_cancel_timeout_sec = 0.05
        self.owner.action_shutdown_lock_timeout_sec = 0.05

        async def hanging_cancel(_request, *, world_lease):
            _ = world_lease
            await asyncio.Event().wait()

        async def no_delay(_seconds: float) -> None:
            return None

        class PermanentlyHeldLock:
            acquired = False

            @staticmethod
            def acquire() -> None:
                from evelyn_core.minecraft_owner_lock import (
                    MinecraftOwnerLockBusy,
                )

                raise MinecraftOwnerLockBusy("busy")

            @staticmethod
            def release() -> None:
                return None

        self.owner.cancel_action_callback = hanging_cancel
        self.owner.sleep = no_delay
        self.owner._world_action_lock = PermanentlyHeldLock()

        result = await asyncio.wait_for(
            self.owner.shutdown(),
            timeout=1.0,
        )

        self.assertEqual(
            result["error"],
            "minecraft_world_action_lock_timeout",
        )
        self.assertFalse(self.owner._owner_lock.acquired)


if __name__ == "__main__":
    unittest.main()
