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


if __name__ == "__main__":
    unittest.main()
