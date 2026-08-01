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

_IMPORT_ERROR: str | None = None
try:
    from evelyn_core.minecraft_autonomy_client import (  # noqa: E402
        MinecraftAutonomyClient,
    )
except ModuleNotFoundError as exc:
    _IMPORT_ERROR = exc.name


@unittest.skipIf(
    _IMPORT_ERROR is not None,
    f"runtime dependency unavailable: {_IMPORT_ERROR}",
)
class MinecraftAutonomyClientOutcomeTests(
    unittest.IsolatedAsyncioTestCase
):
    def client(self, response: object):
        client = object.__new__(MinecraftAutonomyClient)
        client._request = AsyncMock(return_value=response)
        client._persist_goal_override = Mock()
        return client

    @staticmethod
    def bound_action() -> dict:
        return {
            "schema": "minecraft_autonomy.action-request.v1",
            "guildId": 7,
            "actionKey": "minecraft:find_food_source",
            "actionRunId": "action-run-1",
            "authorizationGrantId": "grant-1",
            "contractCode": "mindcraft_food_recovery.v1",
            "parameters": {},
            "goalRunId": "goal-run-1",
            "leaseId": "lease-1",
            "leaseProcessNonce": "process-1",
        }

    async def test_start_forwards_world_lease_proof(self) -> None:
        client = self.client({"connected": True})
        client.ensure_codex_gateway = AsyncMock()
        proof = {
            "schema": "minecraft_world_lease.proof.v1",
            "leaseId": "lease-1",
            "authorizationToken": "secret-1",
        }

        await client.start(
            "diamond",
            world_lease=proof,
        )

        client.ensure_codex_gateway.assert_awaited_once_with()
        client._request.assert_awaited_once_with(
            "POST",
            "/start",
            {
                "goal": "diamond",
                "worldLease": proof,
            },
        )

    async def test_functional_readiness_requires_exact_contract(
        self,
    ) -> None:
        client = self.client(
            {
                "runtime": "mindcraft",
                "running": True,
                "telemetry_fresh": True,
                "minecraft_connected": True,
                "world_lease_authorized": True,
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
        )

        self.assertTrue(await client.is_functionally_ready())
        client._request.assert_awaited_once_with(
            "GET",
            "/status",
            ensure_service=False,
            timeout_sec=1.0,
        )

    async def test_functional_readiness_rejects_http_only_status(
        self,
    ) -> None:
        client = self.client(
            {
                "runtime": "mindcraft",
                "running": False,
                "connected": False,
            }
        )

        self.assertFalse(await client.is_functionally_ready())

    async def test_legacy_voyager_healthy_boundary_remains_supported(
        self,
    ) -> None:
        client = self.client(
            {
                "service": "voyager_minecraft",
                "recovery_state": {
                    "scope": "healthy",
                    "domain": "healthy",
                    "healthy": True,
                },
            }
        )

        self.assertTrue(await client.is_functionally_ready())

    async def test_set_goal_persists_only_confirmed_echo(self) -> None:
        client = self.client(
            {
                "goal": "diamond",
                "stage": "mine",
            }
        )

        result = await client.set_goal(" diamond ")

        client._request.assert_awaited_once_with(
            "POST",
            "/goal",
            {"goal": "diamond"},
        )
        client._persist_goal_override.assert_called_once_with("diamond")
        self.assertTrue(result["outcome_verified"])
        self.assertEqual(
            result["outcome_code"],
            "minecraft_goal_confirmed",
        )

    async def test_set_goal_rejects_mismatch_without_local_persist(
        self,
    ) -> None:
        client = self.client({"goal": "other"})

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_goal_unverified",
        ):
            await client.set_goal("diamond")

        client._persist_goal_override.assert_not_called()

    async def test_set_goal_rejects_untyped_response(self) -> None:
        client = self.client(None)

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_goal_unverified",
        ):
            await client.set_goal("diamond")

        client._persist_goal_override.assert_not_called()

    async def test_action_transport_is_proof_bound_and_non_spawning(
        self,
    ) -> None:
        client = self.client({"ok": True})
        request = self.bound_action()
        proof = {
            "schema": "minecraft_world_lease.proof.v1",
            "leaseId": "lease-1",
            "processNonce": "process-1",
        }

        await client.dispatch_action(
            request,
            world_lease=proof,
        )
        client._request.assert_awaited_once_with(
            "POST",
            "/action",
            {"request": request, "worldLease": proof},
            ensure_service=False,
        )

        client._request.reset_mock()
        await client.action_status("goal-run-1")
        client._request.assert_awaited_once_with(
            "GET",
            "/action/goal-run-1",
            ensure_service=False,
        )

        client._request.reset_mock()
        await client.cancel_action(
            request,
            world_lease=proof,
        )
        client._request.assert_awaited_once_with(
            "POST",
            "/action/cancel",
            {"request": request, "worldLease": proof},
            ensure_service=False,
        )

    async def test_action_transport_rejects_raw_or_missing_proof(
        self,
    ) -> None:
        client = self.client({"ok": True})
        request = self.bound_action()
        request["goal"] = "private"

        with self.assertRaises(ValueError):
            await client.dispatch_action(
                request,
                world_lease={"leaseId": "lease-1"},
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await client.cancel_action(
                self.bound_action(),
                world_lease={},
            )
        client._request.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
