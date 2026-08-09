from __future__ import annotations

import asyncio
import copy
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

from evelyn_core.autonomy import (  # noqa: E402
    AutonomyEngine,
    AutonomyExecutionContext,
)
from evelyn_core.minecraft_action_contract import (  # noqa: E402
    MINECRAFT_ACTION_RESULT_SCHEMA,
)
from evelyn_core.minecraft_autonomy_executor import (  # noqa: E402
    MinecraftAutonomyExecutor,
    MinecraftAutonomyExecutorDeps,
    build_minecraft_autonomy_executor_from_runtime,
)


def lease_status(*, now: float = 1000.0, guild_id: int = 7) -> dict:
    return {
        "schema": "minecraft_world_lease.status.v1",
        "state": "authorized",
        "active": True,
        "auditReady": True,
        "statusReady": True,
        "processNonce": "lease-process-1",
        "updatedAt": now,
        "lease": {
            "leaseId": "lease-1",
            "guildId": guild_id,
            "expiresAt": now + 300.0,
        },
    }


def runtime_status() -> dict:
    return {
        "runtime": "mindcraft",
        "running": True,
        "telemetry_fresh": True,
        "minecraft_connected": True,
        "world_lease_authorized": True,
        "observation": {
            "connected": True,
            "active": True,
            "health": 12,
            "hunger": 8,
            "inventory": {},
            "goal_manager": {"last_execution": "private"},
        },
        "functional_readiness": {
            "schema": "minecraft_autonomy.readiness.v1",
            "state": "ready",
            "ready": True,
            "blockers": [],
            "dependencies": {
                "worldLeaseAuthorized": True,
                "runnerAlive": True,
                "telemetryFresh": True,
                "minecraftConnected": True,
                "taskContractReady": True,
                "effectObserverReady": True,
                "autonomyActive": True,
            },
            "taskContract": {
                "schema": "mindcraft.task-contract.v1",
                "goalManagerMode": "gated",
                "autonomyState": "active",
                "commandGate": "evelyn_goal_manager",
                "effectVerification": "explicit_postcondition",
            },
            "contentFree": True,
        },
    }


def repeat_runtime_status() -> dict:
    payload = runtime_status()
    payload.update(
        {
            "running": False,
            "telemetry_fresh": False,
            "minecraft_connected": False,
            "action_gateway_ready": True,
            "action_gateway": {
                "schema": "mindcraft_action_gateway.readiness.v1",
                "state": "terminal",
                "ready": True,
                "acceptsNewAction": True,
                "active": False,
                "terminalStatus": "completed",
                "repeatActionReady": True,
                "contentFree": True,
            },
        }
    )
    payload["observation"]["connected"] = False
    readiness = payload["functional_readiness"]
    readiness.update(
        {
            "state": "blocked",
            "ready": False,
            "blockers": [
                "runner_not_alive",
                "telemetry_stale",
                "minecraft_not_connected",
                "autonomy_not_active",
            ],
        }
    )
    readiness["dependencies"].update(
        {
            "runnerAlive": False,
            "telemetryFresh": False,
            "minecraftConnected": False,
            "autonomyActive": False,
        }
    )
    readiness["taskContract"]["autonomyState"] = "manual_pause"
    return payload


class MinecraftAutonomyExecutorTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.now = 1000.0
        self.lease = lease_status(now=self.now)
        self.runtime = runtime_status()
        self.execute_action = AsyncMock(
            side_effect=self._verified_result
        )
        self.cancel_action = AsyncMock()
        self.force_disconnect = AsyncMock(
            return_value=self._stopped_result()
        )
        self.executor = MinecraftAutonomyExecutor(
            guild_id=7,
            deps=MinecraftAutonomyExecutorDeps(
                get_world_lease_status=lambda: copy.deepcopy(
                    self.lease
                ),
                get_runtime_status=AsyncMock(
                    side_effect=lambda: copy.deepcopy(self.runtime)
                ),
                execute_action=self.execute_action,
                cancel_action=self.cancel_action,
                force_disconnect=self.force_disconnect,
                now=lambda: self.now,
            ),
        )

    def context(self) -> AutonomyExecutionContext:
        return AutonomyExecutionContext(
            guild_id=7,
            action_key="minecraft:find_food_source",
            action_run_id="action-run-1",
            authorization_grant_id="grant-1",
        )

    def step(self) -> dict:
        return {
            "domain": "minecraft",
            "action": "find_food_source",
            "reason": "low_health_no_food",
        }

    def cancelled_ack(self) -> dict:
        return {
            "schema": "minecraft_autonomy.action-dispatch.v1",
            "status": "cancelled",
            "guildId": 7,
            "actionKey": "minecraft:find_food_source",
            "actionRunId": "action-run-1",
            "authorizationGrantId": "grant-1",
            "goalRunId": "goal-run-1",
            "leaseId": "lease-1",
            "leaseProcessNonce": "lease-process-1",
            "contractCode": "mindcraft_food_recovery.v1",
            "accepted": False,
            "contentFree": True,
            "errorCode": "minecraft_action_cancelled",
        }

    def _stopped_result(self) -> dict:
        return {
            "running": False,
            "connected": False,
            "outcome_verified": True,
            "outcome_code": "minecraft_stopped",
        }

    async def test_runtime_builder_wires_lazy_owner_and_client(self) -> None:
        owner = Mock()
        owner.status.return_value = copy.deepcopy(self.lease)
        owner.execute_action = AsyncMock(return_value={})
        owner.cancel_action = AsyncMock()
        owner.disconnect = AsyncMock(return_value=self._stopped_result())
        client = Mock()
        client.status = AsyncMock(return_value=copy.deepcopy(self.runtime))

        executor = build_minecraft_autonomy_executor_from_runtime(
            7,
            get_world_lease_owner=lambda: owner,
            get_client=lambda: client,
            now=lambda: self.now,
        )

        self.assertEqual(executor.guild_id, 7)
        self.assertEqual(
            executor.deps.get_world_lease_status(),
            self.lease,
        )
        self.assertEqual(
            await executor.deps.get_runtime_status(),
            self.runtime,
        )
        await executor.deps.execute_action(7, {"request": True})
        await executor.deps.cancel_action(7, "action-run-1")
        await executor.deps.force_disconnect(7)
        owner.execute_action.assert_awaited_once_with(7, {"request": True})
        owner.cancel_action.assert_awaited_once_with(7, "action-run-1")
        owner.disconnect.assert_awaited_once_with(7)

    async def _verified_result(
        self,
        guild_id: int,
        request: dict,
    ) -> dict:
        self.assertEqual(guild_id, 7)
        return {
            "schema": MINECRAFT_ACTION_RESULT_SCHEMA,
            "status": "completed",
            "guildId": guild_id,
            "actionKey": request["actionKey"],
            "actionRunId": request["actionRunId"],
            "authorizationGrantId": request[
                "authorizationGrantId"
            ],
            "goalRunId": "goal-run-1",
            "leaseId": "lease-1",
            "leaseProcessNonce": "lease-process-1",
            "contractCode": "mindcraft_food_recovery.v1",
            "postconditionCode": "food_reserve_ready",
            "evidenceCode": (
                "minecraft_find_food_source_completed"
            ),
            "verified": True,
            "contentFree": True,
        }

    async def test_connect_is_observational_and_requires_bound_readiness(
        self,
    ) -> None:
        await self.executor.connect()

        self.execute_action.assert_not_awaited()
        self.cancel_action.assert_not_awaited()
        observed = await self.executor.observe()
        self.assertTrue(observed["ready"])
        self.assertEqual(observed["health"], 12)
        self.assertNotIn("goal_manager", observed)

    async def test_connect_rejects_other_guild_or_stale_lease(self) -> None:
        self.lease["lease"]["guildId"] = 8
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_owner_mismatch",
        ):
            await self.executor.connect()

        self.lease = lease_status(now=self.now)
        self.lease["updatedAt"] = self.now - 30
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_heartbeat_stale",
        ):
            await self.executor.connect()

    async def test_connect_rejects_liveness_without_exact_readiness(
        self,
    ) -> None:
        self.runtime.pop("functional_readiness")
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_readiness_contract_invalid",
        ):
            await self.executor.connect()

    async def test_verified_action_requires_exact_context_and_result(
        self,
    ) -> None:
        await self.executor.connect()

        result = await self.executor.execute_step(
            self.step(),
            context=self.context(),
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["verified"])
        self.assertEqual(
            result["evidence_code"],
            "minecraft_find_food_source_completed",
        )
        request = self.execute_action.await_args.args[1]
        self.assertEqual(request["actionRunId"], "action-run-1")
        self.assertEqual(
            request["authorizationGrantId"],
            "grant-1",
        )

    async def test_disconnect_blocks_action_waiting_on_readiness(
        self,
    ) -> None:
        await self.executor.connect()
        readiness_started = asyncio.Event()
        release_readiness = asyncio.Event()

        async def delayed_readiness() -> dict:
            readiness_started.set()
            await release_readiness.wait()
            return copy.deepcopy(self.runtime)

        self.executor.deps.get_runtime_status.side_effect = delayed_readiness
        action = asyncio.create_task(
            self.executor.execute_step(
                self.step(),
                context=self.context(),
            )
        )
        await readiness_started.wait()

        await self.executor.disconnect()
        release_readiness.set()
        result = await action

        self.assertEqual(result["reason"], "minecraft_executor_disabled")
        self.execute_action.assert_not_awaited()
        self.assertEqual(self.executor._inflight_action_run_id, "")

    async def test_connected_executor_accepts_exact_stopped_repeat_gateway(
        self,
    ) -> None:
        await self.executor.connect()
        self.runtime = repeat_runtime_status()

        result = await self.executor.execute_step(
            self.step(),
            context=self.context(),
        )

        self.assertTrue(result["verified"])
        self.execute_action.assert_awaited_once()

    async def test_repeat_gateway_cannot_replace_initial_route_readiness(
        self,
    ) -> None:
        self.runtime = repeat_runtime_status()

        with self.assertRaisesRegex(
            RuntimeError,
            "runner_not_alive",
        ):
            await self.executor.connect()

        self.runtime = runtime_status()
        await self.executor.connect()
        self.runtime = repeat_runtime_status()
        self.runtime["action_gateway"]["rawGoal"] = "PRIVATE"
        result = await self.executor.execute_step(
            self.step(),
            context=self.context(),
        )
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "runner_not_alive")
        self.execute_action.assert_not_awaited()

    async def test_disabled_missing_context_and_raw_fields_fail_closed(
        self,
    ) -> None:
        disabled = await self.executor.execute_step(
            self.step(),
            context=self.context(),
        )
        self.assertEqual(disabled["reason"], "minecraft_executor_disabled")

        await self.executor.connect()
        missing = await self.executor.execute_step(
            self.step(),
            context=None,
        )
        self.assertEqual(
            missing["reason"],
            "minecraft_action_context_required",
        )
        raw_step = {**self.step(), "command": "/give @s bread"}
        rejected = await self.executor.execute_step(
            raw_step,
            context=self.context(),
        )
        self.assertEqual(
            rejected["reason"],
            "minecraft_action_step_fields_invalid",
        )
        self.execute_action.assert_not_awaited()

    async def test_result_replay_mismatch_and_goal_echo_fail_closed(
        self,
    ) -> None:
        await self.executor.connect()
        for payload in (
            {
                "goal": "find food",
                "outcome_verified": True,
                "outcome_code": "minecraft_goal_confirmed",
            },
            {
                **await self._verified_result(
                    7,
                    {
                        "actionKey": "minecraft:find_food_source",
                        "actionRunId": "other-run",
                        "authorizationGrantId": "grant-1",
                    },
                ),
                "actionRunId": "other-run",
            },
        ):
            with self.subTest(payload=payload):
                self.execute_action.side_effect = None
                self.execute_action.return_value = payload
                result = await self.executor.execute_step(
                    self.step(),
                    context=self.context(),
                )
                self.assertFalse(result["verified"])
                self.assertNotEqual(result["status"], "ok")

    async def test_lease_change_during_action_is_not_verified(self) -> None:
        await self.executor.connect()

        async def change_lease(_guild_id: int, _request: dict) -> dict:
            result = await self._verified_result(7, _request)
            self.lease["lease"]["leaseId"] = "lease-2"
            return result

        self.execute_action.side_effect = change_lease
        result = await self.executor.execute_step(
            self.step(),
            context=self.context(),
        )

        self.assertEqual(
            result["reason"],
            "minecraft_world_lease_changed",
        )
        self.assertFalse(result["verified"])

    async def test_cancellation_requests_same_action_stop(self) -> None:
        await self.executor.connect()
        started = asyncio.Event()

        async def wait_forever(_guild_id: int, _request: dict) -> dict:
            started.set()
            await asyncio.Future()

        self.execute_action.side_effect = wait_forever
        self.cancel_action.return_value = self.cancelled_ack()
        task = asyncio.create_task(
            self.executor.execute_step(
                self.step(),
                context=self.context(),
            )
        )
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.cancel_action.assert_awaited_once_with(
            7,
            "action-run-1",
        )

    async def test_unverified_cancel_retains_correlation(self) -> None:
        await self.executor.connect()
        started = asyncio.Event()

        async def wait_forever(_guild_id: int, _request: dict) -> dict:
            started.set()
            await asyncio.Future()

        self.execute_action.side_effect = wait_forever
        self.cancel_action.side_effect = RuntimeError(
            "minecraft_world_lease_owner_unavailable"
        )
        self.force_disconnect.side_effect = RuntimeError(
            "minecraft_world_lease_owner_unavailable"
        )
        task = asyncio.create_task(
            self.executor.execute_step(
                self.step(),
                context=self.context(),
            )
        )
        await started.wait()
        task.cancel()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await task

        self.assertEqual(
            self.executor._inflight_action_run_id,
            "action-run-1",
        )
        self.cancel_action.side_effect = None
        self.cancel_action.return_value = self.cancelled_ack()
        await self.executor.disconnect()
        self.assertEqual(
            self.executor._inflight_action_run_id,
            "",
        )

    async def test_engine_stop_cannot_report_idle_when_cancel_unverified(
        self,
    ) -> None:
        await self.executor.connect()
        started = asyncio.Event()

        async def wait_forever(_guild_id: int, _request: dict) -> dict:
            started.set()
            await asyncio.Future()

        self.execute_action.side_effect = wait_forever
        self.cancel_action.side_effect = RuntimeError(
            "minecraft_world_lease_owner_unavailable"
        )
        self.force_disconnect.side_effect = RuntimeError(
            "minecraft_world_lease_owner_unavailable"
        )
        engine = AutonomyEngine(
            guild_id=7,
            executor=self.executor,
        )
        engine._executor_connected = True
        engine.state.enabled = True
        engine.state.status = "running"
        engine._task = asyncio.create_task(
            self.executor.execute_step(
                self.step(),
                context=self.context(),
            )
        )
        await started.wait()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await engine.stop()

        self.assertEqual(engine.state.status, "stopping")
        self.assertEqual(
            self.executor._inflight_action_run_id,
            "action-run-1",
        )

        self.cancel_action.side_effect = None
        self.cancel_action.return_value = self.cancelled_ack()
        await engine.stop()
        self.assertEqual(engine.state.status, "idle")

    async def test_malformed_cancel_requires_verified_disconnect(
        self,
    ) -> None:
        await self.executor.connect()
        started = asyncio.Event()

        async def wait_forever(_guild_id: int, _request: dict) -> dict:
            started.set()
            await asyncio.Future()

        self.execute_action.side_effect = wait_forever
        self.cancel_action.return_value = {
            **self.cancelled_ack(),
            "contentFree": False,
        }
        task = asyncio.create_task(
            self.executor.execute_step(
                self.step(),
                context=self.context(),
            )
        )
        await started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.force_disconnect.assert_awaited_once_with(7)
        self.assertEqual(
            self.executor._inflight_action_run_id,
            "",
        )


if __name__ == "__main__":
    unittest.main()
