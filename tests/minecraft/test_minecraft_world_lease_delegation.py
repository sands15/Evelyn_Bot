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

from evelyn_core.minecraft_world_lease_contract import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
)
from evelyn_core.minecraft_world_lease_delegation import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER,
    execute_minecraft_world_lease_delegation,
    minecraft_world_lease_delegation_authorized,
    minecraft_world_lease_delegation_error_code,
)
from evelyn_core.minecraft_world_lease_remote import (  # noqa: E402
    MinecraftWorldLeaseRemote,
)


class FakeOwner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def status(self) -> dict:
        return {
            "schema": "minecraft_world_lease.status.v1",
            "state": "authorized",
            "active": True,
            "lease": {"guildId": 7},
        }

    async def connect(self, guild_id: int, **kwargs) -> dict:
        self.calls.append(("connect", (guild_id, kwargs)))
        return {"connected": True}

    async def disconnect(self, guild_id: int) -> dict:
        self.calls.append(("disconnect", guild_id))
        return {"connected": False}

    async def set_goal(self, guild_id: int, goal: str) -> dict:
        self.calls.append(("goal", (guild_id, goal)))
        return {"goal": goal, "outcome_verified": True}


class MinecraftWorldLeaseDelegationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_typed_delegation_routes_mutations(self) -> None:
        owner = FakeOwner()

        connected = await execute_minecraft_world_lease_delegation(
            owner,
            action="connect",
            payload={
                "guildId": 7,
                "issuerRef": "discord_user:1",
                "source": "discord_command",
                "goal": "diamond",
                "ttlSec": 120,
            },
        )
        goal = await execute_minecraft_world_lease_delegation(
            owner,
            action="goal",
            payload={"guildId": 7, "goal": "iron"},
        )
        stopped = await execute_minecraft_world_lease_delegation(
            owner,
            action="disconnect",
            payload={"guildId": 7},
        )

        self.assertTrue(connected["ok"])
        self.assertEqual(goal["result"]["goal"], "iron")
        self.assertFalse(stopped["result"]["connected"])
        self.assertEqual(
            [call[0] for call in owner.calls],
            ["connect", "goal", "disconnect"],
        )

    async def test_delegation_rejects_untyped_or_unknown_action(
        self,
    ) -> None:
        owner = FakeOwner()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_payload_invalid",
        ):
            await execute_minecraft_world_lease_delegation(
                owner,
                action="connect",
                payload=[],
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_delegation_action_invalid",
        ):
            await execute_minecraft_world_lease_delegation(
                owner,
                action="shell",
                payload={"guildId": 7},
            )

        self.assertEqual(owner.calls, [])

    def test_token_comparison_and_error_redaction(self) -> None:
        self.assertTrue(
            minecraft_world_lease_delegation_authorized(
                expected_token="secret",
                presented_token="secret",
            )
        )
        self.assertFalse(
            minecraft_world_lease_delegation_authorized(
                expected_token="secret",
                presented_token="other",
            )
        )
        self.assertEqual(
            minecraft_world_lease_delegation_error_code(
                RuntimeError("minecraft_service_unavailable")
            ),
            "minecraft_service_unavailable",
        )
        self.assertEqual(
            minecraft_world_lease_delegation_error_code(
                RuntimeError("private C:\\path token=secret")
            ),
            "minecraft_world_lease_delegation_failed",
        )


class MinecraftWorldLeaseRemoteTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.secret_path = (
            Path(self.temp_dir.name) / "secret.json"
        )
        self.secret_path.write_text(
            json.dumps(
                {
                    "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                    "processNonce": "process-1",
                    "authorizationToken": "secret-1",
                }
            ),
            encoding="utf-8",
        )
        self.calls: list[
            tuple[str, str, object, dict[str, str]]
        ] = []

        async def request(method, path, payload, headers):
            self.calls.append(
                (method, path, payload, dict(headers))
            )
            if method == "GET":
                return {
                    "ok": True,
                    "leaseStatus": {
                        "schema": "minecraft_world_lease.status.v1",
                        "state": "authorization_required",
                        "active": False,
                        "lease": None,
                    },
                }
            return {
                "ok": True,
                "result": {
                    "connected": path.endswith("/connect"),
                    "outcome_verified": True,
                },
                "leaseStatus": {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "authorized",
                    "active": True,
                    "lease": {"guildId": 7},
                },
            }

        self.remote = MinecraftWorldLeaseRemote(
            base_url="http://bot-api:8798",
            secret_path=self.secret_path,
            request=request,
        )
        self.remote.initialize()

    async def test_status_is_public_and_mutation_uses_rotated_token(
        self,
    ) -> None:
        await self.remote.poll_once()
        await self.remote.connect(
            7,
            issuer_ref="discord_user:1",
            source="discord_command",
        )

        get_call, post_call = self.calls
        self.assertEqual(get_call[3], {})
        self.assertEqual(
            post_call[3][
                MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER
            ],
            "secret-1",
        )
        self.secret_path.write_text(
            json.dumps(
                {
                    "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                    "processNonce": "process-2",
                    "authorizationToken": "secret-2",
                }
            ),
            encoding="utf-8",
        )
        await self.remote.disconnect(7)
        self.assertEqual(
            self.calls[-1][3][
                MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER
            ],
            "secret-2",
        )
        self.assertEqual(
            post_call[2]["issuerRef"],
            "discord_user:1",
        )
        self.assertTrue(self.remote.status()["delegated"])

    async def test_missing_secret_blocks_mutation(self) -> None:
        self.secret_path.unlink()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_secret_unavailable",
        ):
            await self.remote.disconnect(7)

        self.assertEqual(self.calls, [])

    async def test_remote_shutdown_never_revokes_central_lease(
        self,
    ) -> None:
        result = await self.remote.shutdown(
            reason="discord_restart"
        )

        self.assertEqual(
            result["action"],
            "remote_delegation_closed",
        )
        self.assertEqual(self.calls, [])

    async def test_poll_failure_is_fail_closed(self) -> None:
        async def unavailable(*_args):
            raise RuntimeError(
                "minecraft_world_lease_owner_unavailable"
            )

        remote = MinecraftWorldLeaseRemote(
            base_url="http://bot-api:8798",
            secret_path=self.secret_path,
            request=unavailable,
        )
        remote.initialize()

        status = await remote.poll_once()

        self.assertEqual(status["state"], "remote_unavailable")
        self.assertFalse(status["active"])
        self.assertEqual(status["lease"], None)


if __name__ == "__main__":
    unittest.main()
