from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error as urllib_error


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


def active_lease_status() -> dict:
    now = time.time()
    return {
        "schema": "minecraft_world_lease.status.v1",
        "state": "authorized",
        "updatedAt": now,
        "processNonce": "process-1",
        "active": True,
        "auditReady": True,
        "statusReady": True,
        "lease": {
            "leaseId": "lease-1",
            "guildId": 7,
            "source": "discord_command",
            "issuedAt": now,
            "expiresAt": now + 60.0,
        },
    }


def inactive_lease_status() -> dict:
    return {
        "schema": "minecraft_world_lease.status.v1",
        "state": "authorization_required",
        "updatedAt": time.time(),
        "processNonce": "process-1",
        "active": False,
        "auditReady": True,
        "statusReady": True,
        "lease": None,
    }


class FakeOwner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def status(self) -> dict:
        return active_lease_status()

    async def connect(self, guild_id: int, **kwargs) -> dict:
        self.calls.append(("connect", (guild_id, kwargs)))
        return {"connected": True}

    async def disconnect(self, guild_id: int) -> dict:
        self.calls.append(("disconnect", guild_id))
        return {"connected": False}

    async def set_goal(self, guild_id: int, goal: str) -> dict:
        self.calls.append(("goal", (guild_id, goal)))
        return {"goal": goal, "outcome_verified": True}

    async def dispatch_action(
        self,
        guild_id: int,
        request: dict,
    ) -> dict:
        self.calls.append(("action", (guild_id, request)))
        return {"status": "accepted"}

    async def action_status(self, guild_id: int, **kwargs) -> dict:
        self.calls.append(
            ("action_status", (guild_id, kwargs))
        )
        return {"status": "running"}

    async def cancel_action(
        self,
        guild_id: int,
        action_run_id: str,
    ) -> dict:
        self.calls.append(
            ("cancel_action", (guild_id, action_run_id))
        )
        return {"status": "cancelled"}


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

    async def test_action_delegation_is_quick_typed_and_exact(
        self,
    ) -> None:
        owner = FakeOwner()
        request = {
            "schema": "minecraft_autonomy.action-request.v1",
            "guildId": 7,
            "actionKey": "minecraft:find_food_source",
            "actionRunId": "action-run-1",
            "authorizationGrantId": "grant-1",
            "contractCode": "mindcraft_food_recovery.v1",
            "parameters": {},
        }

        await execute_minecraft_world_lease_delegation(
            owner,
            action="action",
            payload={"guildId": 7, "request": request},
        )
        await execute_minecraft_world_lease_delegation(
            owner,
            action="action_status",
            payload={
                "guildId": 7,
                "goalRunId": "goal-run-1",
                "actionRunId": "action-run-1",
                "actionKey": "minecraft:find_food_source",
                "contractCode": "mindcraft_food_recovery.v1",
            },
        )
        await execute_minecraft_world_lease_delegation(
            owner,
            action="cancel_action",
            payload={
                "guildId": 7,
                "actionRunId": "action-run-1",
            },
        )

        self.assertEqual(
            [row[0] for row in owner.calls],
            ["action", "action_status", "cancel_action"],
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_delegation_fields_invalid",
        ):
            await execute_minecraft_world_lease_delegation(
                owner,
                action="action",
                payload={
                    "guildId": 7,
                    "request": request,
                    "goal": "raw content",
                },
            )

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
                        "auditReady": True,
                        "statusReady": True,
                        "lease": None,
                    },
                }
            return {
                "ok": True,
                "result": {
                    "connected": path.endswith("/connect"),
                    "outcome_verified": True,
                },
                "leaseStatus": active_lease_status(),
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

    async def test_mutation_error_ingests_authoritative_status_before_raising(
        self,
    ) -> None:
        async def rejected(*_args):
            return {
                "ok": False,
                "error": "minecraft_world_lease_audit_unavailable",
                "leaseStatus": {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": False,
                    "statusReady": True,
                    "lease": None,
                    "lastErrorCode": (
                        "minecraft_world_lease_audit_unavailable"
                    ),
                },
            }

        self.remote.request = rejected
        self.remote._status = {
            **active_lease_status(),
            "delegated": True,
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_audit_unavailable",
        ):
            await self.remote.disconnect(7)

        status = self.remote.status()
        self.assertEqual(
            status["state"],
            "manual_intervention_required",
        )
        self.assertFalse(status["active"])
        self.assertFalse(status["auditReady"])
        self.assertIsNone(status["lease"])

    async def test_error_without_valid_status_clears_cached_authorization(
        self,
    ) -> None:
        audit_not_ready = active_lease_status()
        audit_not_ready["auditReady"] = False
        missing_status_ready = active_lease_status()
        missing_status_ready.pop("statusReady")
        status_not_ready = active_lease_status()
        status_not_ready["statusReady"] = False
        status_ready_not_boolean = active_lease_status()
        status_ready_not_boolean["statusReady"] = "true"
        missing_lease_id = active_lease_status()
        missing_lease_id["lease"] = {
            **missing_lease_id["lease"],
            "leaseId": "",
        }
        missing_process_nonce = active_lease_status()
        missing_process_nonce["processNonce"] = ""
        invalid_guild = active_lease_status()
        invalid_guild["lease"] = {
            **invalid_guild["lease"],
            "guildId": -1,
        }
        expired = active_lease_status()
        expired["lease"] = {
            **expired["lease"],
            "expiresAt": time.time() - 1.0,
        }
        stale = active_lease_status()
        stale["updatedAt"] = time.time() - 60.0
        invalid_statuses = (
            None,
            {},
            audit_not_ready,
            missing_status_ready,
            status_not_ready,
            status_ready_not_boolean,
            missing_lease_id,
            missing_process_nonce,
            invalid_guild,
            expired,
            stale,
        )
        for invalid_status in invalid_statuses:
            with self.subTest(lease_status=invalid_status):
                async def rejected(*_args):
                    return {
                        "ok": False,
                        "error": "private C:\\path token=secret",
                        "leaseStatus": invalid_status,
                    }

                self.remote.request = rejected
                self.remote._status = {
                    **active_lease_status(),
                    "delegated": True,
                }

                with self.assertRaisesRegex(
                    RuntimeError,
                    "minecraft_world_lease_delegation_failed",
                ):
                    await self.remote.disconnect(7)

                status = self.remote.status()
                self.assertEqual(status["state"], "remote_error")
                self.assertFalse(status["active"])
                self.assertIsNone(status["lease"])
                self.assertEqual(
                    status["lastErrorCode"],
                    "minecraft_world_lease_delegation_failed",
                )

    async def test_inactive_status_preserves_boundary_booleans(
        self,
    ) -> None:
        async def rejected(*_args):
            return {
                "ok": False,
                "error": "minecraft_world_lease_status_write_failed",
                "leaseStatus": {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": True,
                    "statusReady": False,
                    "lease": {"guildId": 7},
                },
            }

        self.remote.request = rejected

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_status_write_failed",
        ):
            await self.remote.disconnect(7)

        status = self.remote.status()
        self.assertFalse(status["active"])
        self.assertTrue(status["auditReady"])
        self.assertFalse(status["statusReady"])
        self.assertIsNone(status["lease"])

    async def test_unauthorized_error_clears_cached_authorization(
        self,
    ) -> None:
        async def unauthorized(*_args):
            return {
                "ok": False,
                "error": (
                    "minecraft_world_lease_delegation_unauthorized"
                ),
            }

        self.remote.request = unauthorized
        self.remote._status = {
            **active_lease_status(),
            "delegated": True,
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_delegation_unauthorized",
        ):
            await self.remote.disconnect(7)

        status = self.remote.status()
        self.assertEqual(status["state"], "remote_error")
        self.assertFalse(status["active"])
        self.assertIsNone(status["lease"])

    async def test_http_error_body_status_is_ingested_before_raise(
        self,
    ) -> None:
        body = json.dumps(
            {
                "ok": False,
                "error": "minecraft_world_lease_audit_unavailable",
                "leaseStatus": {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": False,
                    "statusReady": True,
                    "lease": None,
                },
            }
        ).encode("utf-8")
        http_error = urllib_error.HTTPError(
            "http://bot-api:8798/internal/minecraft-world-lease/disconnect",
            409,
            "Conflict",
            None,
            io.BytesIO(body),
        )
        remote = MinecraftWorldLeaseRemote(
            base_url="http://bot-api:8798",
            secret_path=self.secret_path,
        )
        remote.initialize()
        remote._status = {
            **active_lease_status(),
            "delegated": True,
        }

        with patch(
            "evelyn_core.minecraft_world_lease_remote.urllib_request.urlopen",
            side_effect=http_error,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "minecraft_world_lease_audit_unavailable",
            ):
                await remote.disconnect(7)

        status = remote.status()
        self.assertEqual(
            status["state"],
            "manual_intervention_required",
        )
        self.assertFalse(status["active"])
        self.assertFalse(status["auditReady"])

    async def test_success_without_typed_result_clears_authorization(
        self,
    ) -> None:
        operations = (
            (
                "connect",
                None,
                lambda: self.remote.connect(
                    7,
                    issuer_ref="discord_user:1",
                    source="discord_command",
                ),
            ),
            (
                "disconnect",
                [],
                lambda: self.remote.disconnect(7),
            ),
            (
                "goal",
                "invalid",
                lambda: self.remote.set_goal(7, "diamond"),
            ),
        )
        for action, invalid_result, operation in operations:
            with self.subTest(action=action):
                async def incomplete(*_args):
                    return {
                        "ok": True,
                        "result": invalid_result,
                        "leaseStatus": active_lease_status(),
                    }

                self.remote.request = incomplete
                with self.assertRaisesRegex(
                    RuntimeError,
                    "minecraft_world_lease_response_invalid",
                ):
                    await operation()

                status = self.remote.status()
                self.assertEqual(status["state"], "remote_error")
                self.assertFalse(status["active"])
                self.assertIsNone(status["lease"])

    async def test_missing_secret_blocks_mutation(self) -> None:
        self.secret_path.unlink()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_secret_unavailable",
        ):
            await self.remote.disconnect(7)

        self.assertEqual(self.calls, [])
        self.assertEqual(self.remote.status()["state"], "remote_error")
        self.assertFalse(self.remote.status()["active"])

    async def test_remote_action_dispatches_then_polls_exact_result(
        self,
    ) -> None:
        calls: list[tuple[str, dict, dict[str, str]]] = []
        bound: dict = {}

        def dispatch(status: str) -> dict:
            return {
                "schema": "minecraft_autonomy.action-dispatch.v1",
                "status": status,
                **{
                    key: bound[key]
                    for key in (
                        "guildId",
                        "actionKey",
                        "actionRunId",
                        "authorizationGrantId",
                        "goalRunId",
                        "leaseId",
                        "leaseProcessNonce",
                        "contractCode",
                    )
                },
                "accepted": status in {"accepted", "running"},
                "contentFree": True,
                "errorCode": "",
            }

        async def action_request(method, path, payload, headers):
            calls.append((path, dict(payload or {}), dict(headers)))
            if path.endswith("/action"):
                bound.update(payload["request"])
                bound.update(
                    {
                        "goalRunId": "goal-run-1",
                        "leaseId": "lease-1",
                        "leaseProcessNonce": "process-1",
                    }
                )
                result = dispatch("accepted")
            elif path.endswith("/action_status"):
                result = {
                    "schema": "minecraft_autonomy.action-result.v1",
                    "status": "completed",
                    **{
                        key: bound[key]
                        for key in (
                            "guildId",
                            "actionKey",
                            "actionRunId",
                            "authorizationGrantId",
                            "goalRunId",
                            "leaseId",
                            "leaseProcessNonce",
                            "contractCode",
                        )
                    },
                    "postconditionCode": "food_reserve_ready",
                    "evidenceCode": (
                        "minecraft_find_food_source_completed"
                    ),
                    "verified": True,
                    "contentFree": True,
                }
            else:
                self.fail(path)
            return {
                "ok": True,
                "result": result,
                "leaseStatus": active_lease_status(),
            }

        self.remote.request = action_request
        result = await self.remote.execute_action(
            7,
            {
                "schema": "minecraft_autonomy.action-request.v1",
                "guildId": 7,
                "actionKey": "minecraft:find_food_source",
                "actionRunId": "action-run-1",
                "authorizationGrantId": "grant-1",
                "contractCode": "mindcraft_food_recovery.v1",
                "parameters": {},
            },
        )

        self.assertTrue(result["verified"])
        self.assertEqual(
            [path.rsplit("/", 1)[-1] for path, _, _ in calls],
            ["action", "action_status"],
        )
        self.assertTrue(
            all(
                headers[
                    MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER
                ]
                == "secret-1"
                for _, _, headers in calls
            )
        )
        self.assertNotIn(
            '"goal":',
            json.dumps(calls).lower(),
        )

    async def test_cancel_transport_failure_uses_verified_disconnect(
        self,
    ) -> None:
        paths: list[str] = []
        bound: dict = {}
        status_started = asyncio.Event()

        def accepted() -> dict:
            return {
                "schema": "minecraft_autonomy.action-dispatch.v1",
                "status": "accepted",
                **{
                    key: bound[key]
                    for key in (
                        "guildId",
                        "actionKey",
                        "actionRunId",
                        "authorizationGrantId",
                        "goalRunId",
                        "leaseId",
                        "leaseProcessNonce",
                        "contractCode",
                    )
                },
                "accepted": True,
                "contentFree": True,
                "errorCode": "",
            }

        async def request(_method, path, payload, _headers):
            paths.append(path)
            if path.endswith("/action"):
                bound.update(payload["request"])
                bound.update(
                    {
                        "goalRunId": "goal-run-1",
                        "leaseId": "lease-1",
                        "leaseProcessNonce": "process-1",
                    }
                )
                return {
                    "ok": True,
                    "result": accepted(),
                    "leaseStatus": active_lease_status(),
                }
            if path.endswith("/action_status"):
                status_started.set()
                await asyncio.Future()
            if path.endswith("/cancel_action"):
                raise RuntimeError(
                    "minecraft_world_lease_owner_unavailable"
                )
            if path.endswith("/disconnect"):
                return {
                    "ok": True,
                    "result": {
                        "running": False,
                        "connected": False,
                        "outcome_verified": True,
                        "outcome_code": "minecraft_stopped",
                    },
                    "leaseStatus": inactive_lease_status(),
                }
            self.fail(path)

        self.remote.request = request
        task = asyncio.create_task(
            self.remote.execute_action(
                7,
                {
                    "schema": "minecraft_autonomy.action-request.v1",
                    "guildId": 7,
                    "actionKey": "minecraft:find_food_source",
                    "actionRunId": "action-run-1",
                    "authorizationGrantId": "grant-1",
                    "contractCode": "mindcraft_food_recovery.v1",
                    "parameters": {},
                },
            )
        )
        await status_started.wait()
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(
            paths,
            [
                "/internal/minecraft-world-lease/action",
                "/internal/minecraft-world-lease/action_status",
                "/internal/minecraft-world-lease/cancel_action",
                "/internal/minecraft-world-lease/disconnect",
            ],
        )
        self.assertEqual(self.remote._inflight_actions, {})
        self.assertFalse(self.remote.status()["active"])

    async def test_cancel_and_disconnect_failure_retains_correlation(
        self,
    ) -> None:
        bound: dict = {}
        status_started = asyncio.Event()

        def accepted() -> dict:
            return {
                "schema": "minecraft_autonomy.action-dispatch.v1",
                "status": "accepted",
                **{
                    key: bound[key]
                    for key in (
                        "guildId",
                        "actionKey",
                        "actionRunId",
                        "authorizationGrantId",
                        "goalRunId",
                        "leaseId",
                        "leaseProcessNonce",
                        "contractCode",
                    )
                },
                "accepted": True,
                "contentFree": True,
                "errorCode": "",
            }

        async def request(_method, path, payload, _headers):
            if path.endswith("/action"):
                bound.update(payload["request"])
                bound.update(
                    {
                        "goalRunId": "goal-run-1",
                        "leaseId": "lease-1",
                        "leaseProcessNonce": "process-1",
                    }
                )
                return {
                    "ok": True,
                    "result": accepted(),
                    "leaseStatus": active_lease_status(),
                }
            if path.endswith("/action_status"):
                status_started.set()
                await asyncio.Future()
            if path.endswith(("/cancel_action", "/disconnect")):
                raise RuntimeError(
                    "minecraft_world_lease_owner_unavailable"
                )
            self.fail(path)

        self.remote.request = request
        task = asyncio.create_task(
            self.remote.execute_action(
                7,
                {
                    "schema": "minecraft_autonomy.action-request.v1",
                    "guildId": 7,
                    "actionKey": "minecraft:find_food_source",
                    "actionRunId": "action-run-1",
                    "authorizationGrantId": "grant-1",
                    "contractCode": "mindcraft_food_recovery.v1",
                    "parameters": {},
                },
            )
        )
        await status_started.wait()
        task.cancel()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await task

        self.assertIn(
            "action-run-1",
            self.remote._inflight_actions,
        )

    async def test_malformed_remote_dispatch_is_cancelled_by_run_id(
        self,
    ) -> None:
        paths: list[str] = []
        bound: dict = {}

        def ack(status: str) -> dict:
            return {
                "schema": "minecraft_autonomy.action-dispatch.v1",
                "status": status,
                **{
                    key: bound[key]
                    for key in (
                        "guildId",
                        "actionKey",
                        "actionRunId",
                        "authorizationGrantId",
                        "goalRunId",
                        "leaseId",
                        "leaseProcessNonce",
                        "contractCode",
                    )
                },
                "accepted": status in {"accepted", "running"},
                "contentFree": True,
                "errorCode": (
                    "" if status in {"accepted", "running"}
                    else "minecraft_action_cancelled"
                ),
            }

        async def request(_method, path, payload, _headers):
            paths.append(path)
            if path.endswith("/action"):
                bound.update(payload["request"])
                bound.update(
                    {
                        "goalRunId": "goal-run-1",
                        "leaseId": "lease-1",
                        "leaseProcessNonce": "process-1",
                    }
                )
                result = ack("accepted")
                result["actionRunId"] = "wrong-run"
            elif path.endswith("/cancel_action"):
                result = ack("cancelled")
            else:
                self.fail(path)
            return {
                "ok": True,
                "result": result,
                "leaseStatus": active_lease_status(),
            }

        self.remote.request = request
        with self.assertRaises(ValueError):
            await self.remote.dispatch_action(
                7,
                {
                    "schema": "minecraft_autonomy.action-request.v1",
                    "guildId": 7,
                    "actionKey": "minecraft:find_food_source",
                    "actionRunId": "action-run-1",
                    "authorizationGrantId": "grant-1",
                    "contractCode": "mindcraft_food_recovery.v1",
                    "parameters": {},
                },
            )

        self.assertEqual(
            paths,
            [
                "/internal/minecraft-world-lease/action",
                "/internal/minecraft-world-lease/cancel_action",
            ],
        )
        self.assertEqual(self.remote._inflight_actions, {})

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

    async def test_remote_shutdown_cancels_inflight_without_revoking_lease(
        self,
    ) -> None:
        bound = {
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
        self.remote._inflight_actions["action-run-1"] = {
            "guildId": 7,
            "request": bound,
        }
        paths: list[str] = []

        async def cancel_request(_method, path, _payload, _headers):
            paths.append(path)
            return {
                "ok": True,
                "result": {
                    "schema": "minecraft_autonomy.action-dispatch.v1",
                    "status": "cancelled",
                    **{
                        key: bound[key]
                        for key in (
                            "guildId",
                            "actionKey",
                            "actionRunId",
                            "authorizationGrantId",
                            "goalRunId",
                            "leaseId",
                            "leaseProcessNonce",
                            "contractCode",
                        )
                    },
                    "accepted": False,
                    "contentFree": True,
                    "errorCode": "minecraft_action_cancelled",
                },
                "leaseStatus": active_lease_status(),
            }

        self.remote.request = cancel_request
        result = await self.remote.shutdown()

        self.assertEqual(result["actionsCancelled"], 1)
        self.assertEqual(result["fallbackDisconnects"], 0)
        self.assertEqual(
            paths,
            ["/internal/minecraft-world-lease/cancel_action"],
        )
        self.assertTrue(self.remote.status()["active"])

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
