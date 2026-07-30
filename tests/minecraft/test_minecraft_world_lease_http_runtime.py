from __future__ import annotations

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

from evelyn_core.minecraft_world_lease_http_runtime import (  # noqa: E402
    MinecraftWorldLeaseHttpRuntime,
)


class MinecraftWorldLeaseHttpRuntimeTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_start_and_goal_forward_lease_proof(self) -> None:
        calls: list[tuple[str, str, object]] = []

        async def request(method, path, payload):
            calls.append((method, path, payload))
            return (
                {
                    "goal": "diamond",
                    "connected": True,
                },
                "",
            )

        runtime = MinecraftWorldLeaseHttpRuntime(
            request=request,
            is_offline_error=lambda _error: False,
        )
        proof = {
            "schema": "minecraft_world_lease.proof.v1",
            "leaseId": "lease-1",
        }

        await runtime.start(
            "diamond",
            world_lease=proof,
        )
        goal = await runtime.set_goal(
            "diamond",
            world_lease=proof,
        )

        self.assertEqual(
            calls[0][2]["worldLease"],
            proof,
        )
        self.assertEqual(
            calls[1][2]["worldLease"],
            proof,
        )
        self.assertTrue(goal["outcome_verified"])

    async def test_offline_status_and_stop_are_safe_stopped(
        self,
    ) -> None:
        async def request(_method, _path, _payload):
            return None, "connection refused"

        runtime = MinecraftWorldLeaseHttpRuntime(
            request=request,
            is_offline_error=lambda _error: True,
        )

        status = await runtime.status()
        stopped = await runtime.stop()

        self.assertFalse(status["service_available"])
        self.assertFalse(status["running"])
        self.assertFalse(stopped["connected"])

    async def test_non_offline_failure_is_not_reported_stopped(
        self,
    ) -> None:
        async def request(_method, _path, _payload):
            return None, "http_500"

        runtime = MinecraftWorldLeaseHttpRuntime(
            request=request,
            is_offline_error=lambda _error: False,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_service_request_failed",
        ):
            await runtime.status()


if __name__ == "__main__":
    unittest.main()
