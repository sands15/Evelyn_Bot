from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.minecraft_world_lease as lease_module  # noqa: E402
from evelyn_core.minecraft_world_lease import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
    STOP_RETRY_LIMIT,
    MinecraftWorldLeaseOwner,
)
from evelyn_core.minecraft_world_lease_contract import (  # noqa: E402
    MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
    MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
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
        result = dict(self.enable_result)
        if goal:
            result.setdefault("goal", goal)
        return result

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
        result.setdefault("goal", goal)
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
            standby_probe_interval_sec=5.0,
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

    def fail_status_writes(
        self,
        owner: MinecraftWorldLeaseOwner | None = None,
    ):
        target = owner or self.owner
        original = lease_module.atomic_json_write

        def write(path: Path, payload: dict) -> None:
            if Path(path) == target.status_path:
                raise OSError("status artifact unavailable")
            original(path, payload)

        return patch.object(
            lease_module,
            "atomic_json_write",
            side_effect=write,
        )

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

    def test_event_append_flushes_and_fsyncs_before_success(self) -> None:
        with patch(
            "evelyn_core.minecraft_world_lease.os.fsync"
        ) as fsync:
            written = self.owner._append_event(
                "runtime_stop_attempted",
                guild_id=7,
                reason="explicit_disconnect",
            )

        self.assertTrue(written)
        fsync.assert_called_once()

    def test_new_event_file_requires_directory_entry_sync(self) -> None:
        self.clock.value += 24 * 60 * 60

        with patch(
            "evelyn_core.minecraft_world_lease._sync_directory_entry"
        ) as sync_directory:
            written = self.owner._append_event(
                "runtime_stop_attempted",
                guild_id=7,
                reason="explicit_disconnect",
            )

        self.assertTrue(written)
        sync_directory.assert_called_once_with(self.owner.events_dir)

    def test_directory_entry_sync_failure_is_not_audited_success(self) -> None:
        self.clock.value += 24 * 60 * 60

        with patch(
            "evelyn_core.minecraft_world_lease._sync_directory_entry",
            side_effect=OSError("directory sync failed"),
        ):
            written = self.owner._append_event(
                "runtime_stop_attempted",
                guild_id=7,
                reason="explicit_disconnect",
            )

        self.assertFalse(written)

    def test_initialization_audit_failure_withholds_capability(self) -> None:
        blocked_events = self.root / "blocked-events"
        blocked_events.write_text("not a directory", encoding="utf-8")
        owner = MinecraftWorldLeaseOwner(
            status_path=self.root / "audit-failure" / "status.json",
            events_dir=blocked_events,
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )

        status = owner.initialize()

        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertFalse(status["auditReady"])
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )
        self.assertEqual(owner.delegation_token(), "")
        self.assertFalse(owner.secret_path.exists())

    async def test_initialization_secret_failure_keeps_status_ready(
        self,
    ) -> None:
        with patch.object(
            self.owner,
            "_write_secret",
            side_effect=OSError("secret unavailable"),
        ):
            status = self.owner.initialize()

        self.assertTrue(status["auditReady"])
        self.assertTrue(status["statusReady"])
        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_world_lease_secret_unavailable",
        )
        self.assertEqual(self.owner.delegation_token(), "")
        self.assertFalse(self.owner.secret_path.exists())

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_secret_unavailable",
        ):
            await self.connect()
        self.assertFalse(any(call[0] == "enable" for call in self.runtime.calls))

    def test_initialization_status_failure_withholds_capability(self) -> None:
        owner = MinecraftWorldLeaseOwner(
            status_path=self.root / "status-failure" / "status.json",
            events_dir=self.root / "status-failure" / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )

        with self.fail_status_writes(owner):
            status = owner.initialize()

        self.assertTrue(status["auditReady"])
        self.assertFalse(status["statusReady"])
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
        )
        self.assertEqual(owner.delegation_token(), "")
        self.assertFalse(owner.secret_path.exists())

    async def test_lease_audit_failure_blocks_connect_before_effect(
        self,
    ) -> None:
        def append(event: str, **_kwargs) -> bool:
            return event != "lease_issued"

        with patch.object(self.owner, "_append_event", side_effect=append):
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
            ):
                await self.connect()

        self.assertFalse(any(call[0] == "enable" for call in self.runtime.calls))
        self.assertFalse(self.owner.status()["active"])
        self.assertFalse(self.owner.status()["auditReady"])
        self.assertEqual(
            self.owner.status()["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_connect_post_effect_audit_failure_stops_and_is_unverified(
        self,
    ) -> None:
        def append(event: str, **_kwargs) -> bool:
            return event != "runtime_start_verified"

        with patch.object(self.owner, "_append_event", side_effect=append):
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
            ):
                await self.connect()

        self.assertTrue(any(call[0] == "enable" for call in self.runtime.calls))
        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertFalse(status["active"])
        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )

    async def test_connect_lost_boundary_stops_possible_active_runtime(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()
        self.owner._mark_status_write_failed()

        with self.assertRaisesRegex(
            RuntimeError,
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
        ):
            await self.connect()

        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])

    async def test_connect_rejects_mismatched_initial_goal_echo(self) -> None:
        self.runtime.enable_result = {
            "connected": True,
            "goal": "different goal",
            "outcome_verified": True,
            "outcome_code": "minecraft_connected",
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_goal_unverified",
        ):
            await self.connect()

        events = self.read_events()
        self.assertTrue(any(row["event"] == "goal_attempted" for row in events))
        self.assertTrue(any(row["event"] == "goal_failed" for row in events))
        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])

    async def test_process_restart_does_not_restore_lease(self) -> None:
        await self.connect()
        previous_token = self.owner.authorization_token
        self.clock.value += self.owner.owner_claim_stale_sec + 1.0
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

    async def test_competing_owner_cannot_replace_live_claim(
        self,
    ) -> None:
        original_secret = json.loads(
            self.owner.secret_path.read_text(encoding="utf-8")
        )
        competitor = MinecraftWorldLeaseOwner(
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

        status = competitor.initialize()

        self.assertEqual(status["state"], "owner_conflict")
        self.assertFalse(status["ownerClaimOwned"])
        self.assertEqual(competitor.delegation_token(), "")
        self.assertEqual(
            json.loads(
                self.owner.secret_path.read_text(encoding="utf-8")
            ),
            original_secret,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_owner_conflict",
        ):
            await competitor.connect(
                7,
                issuer_ref="discord_user:456",
                source="discord_command",
            )

    async def test_stale_claim_takeover_invalidates_old_owner(
        self,
    ) -> None:
        await self.connect()
        self.clock.value += self.owner.owner_claim_stale_sec + 1.0
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

        replacement_status = replacement.initialize()
        old_status = self.owner.status()

        self.assertTrue(replacement_status["ownerClaimOwned"])
        self.assertEqual(
            replacement_status["state"],
            "authorization_required",
        )
        self.assertEqual(old_status["state"], "owner_conflict")
        self.assertFalse(old_status["active"])
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_clean_shutdown_releases_claim_and_token(
        self,
    ) -> None:
        self.owner._watchdog_task = None

        result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertFalse(self.owner.owner_claim_path.exists())
        self.assertFalse(self.owner.status()["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_shutdown_still_stops_when_audit_fails(self) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]

        with patch.object(self.owner, "_append_event", return_value=False):
            result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertEqual(
            result["error"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )
        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )
        self.assertFalse(status["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_shutdown_still_stops_and_surfaces_status_failure(
        self,
    ) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]

        with self.fail_status_writes():
            result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertEqual(
            result["error"],
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
        )
        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertFalse(status["statusReady"])
        self.assertFalse(status["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_cancelled_shutdown_still_releases_claim_and_token(
        self,
    ) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [
            asyncio.CancelledError(),
            {"running": False, "connected": False},
        ]

        with self.assertRaises(asyncio.CancelledError):
            await self.owner.shutdown()

        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.owner_claim_path.exists())
        self.assertFalse(self.owner.status()["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_double_cancelled_shutdown_completes_safe_stop(self) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]
        stop_started = asyncio.Event()
        allow_stop = asyncio.Event()
        stop_completed = asyncio.Event()

        async def blocking_disable(guild_id: int) -> dict:
            self.runtime.calls.append(("disable", guild_id))
            stop_started.set()
            await allow_stop.wait()
            stop_completed.set()
            return dict(self.runtime.disable_result)

        self.owner.disable_mode = blocking_disable
        task = asyncio.create_task(self.owner.shutdown())
        await asyncio.wait_for(stop_started.wait(), timeout=1.0)
        task.cancel()
        task.cancel()
        allow_stop.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(stop_completed.is_set())
        self.assertFalse(self.owner.owner_claim_path.exists())
        self.assertFalse(self.owner.status()["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")

    async def test_cancelled_shutdown_waiting_for_operation_lock_stops(self) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]
        await self.owner._operation_lock.acquire()
        task = asyncio.create_task(self.owner.shutdown())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)

        self.assertFalse(task.done())
        self.owner._operation_lock.release()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])
        self.assertFalse(self.owner.owner_claim_path.exists())
        self.assertEqual(self.owner.delegation_token(), "")

    def test_unwritable_claim_fails_closed_without_secret(
        self,
    ) -> None:
        blocking_parent = self.root / "not-a-directory"
        blocking_parent.write_text("blocked", encoding="utf-8")
        owner = MinecraftWorldLeaseOwner(
            status_path=self.root / "other-status.json",
            events_dir=self.root / "other-events",
            owner_claim_path=blocking_parent / "claim.json",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )

        status = owner.initialize()

        self.assertEqual(
            status["state"],
            "manual_intervention_required",
        )
        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_world_lease_owner_claim_write_failed",
        )
        self.assertEqual(owner.delegation_token(), "")

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

    async def test_active_lease_status_failure_forces_runtime_stop(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()

        with self.fail_status_writes():
            result = await self.owner.reconcile_once()

        self.assertEqual(
            result["action"],
            "stop_status_write_failed_runtime",
        )
        self.assertEqual(
            result["error"],
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
        )
        self.assertTrue(result["stopped"])
        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])

    async def test_watchdog_refreshes_claim_while_throttling_standby_probe(
        self,
    ) -> None:
        ticks = 0

        async def advance_clock(_interval: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks > 5:
                raise asyncio.CancelledError()
            self.clock.value += 1.0

        self.owner.sleep = advance_clock
        self.owner._defer_standby_probe()
        self.runtime.calls.clear()

        with self.assertRaises(asyncio.CancelledError):
            await self.owner._watchdog_loop()

        self.assertEqual(
            self.runtime.calls,
            [("status", None)],
        )
        claim = json.loads(
            self.owner.owner_claim_path.read_text(encoding="utf-8")
        )
        self.assertEqual(claim["updatedAt"], 1005.0)
        policy = self.owner.status()["policy"]
        self.assertEqual(policy["watchdogIntervalSec"], 1.0)
        self.assertEqual(policy["standbyProbeIntervalSec"], 5.0)

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

    async def test_goal_pre_effect_audit_failure_blocks_goal_and_stops(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()

        def append(event: str, **_kwargs) -> bool:
            return event != "goal_attempted"

        with patch.object(self.owner, "_append_event", side_effect=append):
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
            ):
                await self.owner.set_goal(7, "private_goal_text")

        self.assertFalse(any(call[0] == "goal" for call in self.runtime.calls))
        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["auditReady"])

    async def test_goal_post_effect_audit_failure_stops_and_is_unverified(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()

        def append(event: str, **_kwargs) -> bool:
            return event != "goal_verified"

        with patch.object(self.owner, "_append_event", side_effect=append):
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
            ):
                await self.owner.set_goal(7, "private_goal_text")

        self.assertTrue(any(call[0] == "goal" for call in self.runtime.calls))
        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )

    async def test_goal_unverified_outcome_is_audited_revoked_and_stopped(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()
        self.runtime.goal_result = {
            "goal": "diamond",
            "outcome_verified": False,
            "outcome_code": "minecraft_goal_confirmed",
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_goal_unverified",
        ):
            await self.owner.set_goal(7, "private_goal_text")

        self.assertTrue(any(call[0] == "goal" for call in self.runtime.calls))
        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertFalse(status["active"])
        self.assertTrue(status["auditReady"])
        events = self.read_events()
        self.assertTrue(any(row["event"] == "goal_attempted" for row in events))
        self.assertTrue(any(row["event"] == "goal_failed" for row in events))
        self.assertNotIn("private_goal_text", json.dumps(events))

    async def test_goal_exception_is_audited_revoked_and_stopped(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()
        self.runtime.goal_result = RuntimeError("private goal failure")

        with self.assertRaisesRegex(RuntimeError, "private goal failure"):
            await self.owner.set_goal(7, "private_goal_text")

        self.assertTrue(any(call[0] == "goal" for call in self.runtime.calls))
        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])
        events = self.read_events()
        self.assertTrue(any(row["event"] == "goal_failed" for row in events))
        serialized = json.dumps(events)
        self.assertNotIn("private goal failure", serialized)
        self.assertNotIn("private_goal_text", serialized)

    async def test_goal_requires_exact_requested_goal_echo(self) -> None:
        await self.connect()
        self.runtime.calls.clear()
        self.runtime.goal_result = {
            "goal": "different goal",
            "outcome_verified": True,
            "outcome_code": "minecraft_goal_confirmed",
        }

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_goal_unverified",
        ):
            await self.owner.set_goal(7, "private_goal_text")

        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])

    async def test_goal_final_status_failure_stops_and_fails_closed(
        self,
    ) -> None:
        await self.connect()
        self.runtime.calls.clear()

        with self.fail_status_writes():
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
            ):
                await self.owner.set_goal(7, "private_goal_text")

        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertFalse(status["active"])
        self.assertFalse(status["statusReady"])
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
        )

    async def test_cancelled_goal_completes_shielded_safe_stop(self) -> None:
        await self.connect()
        self.runtime.calls.clear()
        goal_started = asyncio.Event()
        stop_started = asyncio.Event()
        allow_stop = asyncio.Event()
        stop_completed = asyncio.Event()

        async def blocking_goal(*_args, **_kwargs) -> dict:
            goal_started.set()
            await asyncio.Future()
            return {}

        async def blocking_disable(guild_id: int) -> dict:
            self.runtime.calls.append(("disable", guild_id))
            stop_started.set()
            await allow_stop.wait()
            stop_completed.set()
            return dict(self.runtime.disable_result)

        self.owner.set_goal_callback = blocking_goal
        self.owner.disable_mode = blocking_disable
        task = asyncio.create_task(
            self.owner.set_goal(7, "private_goal_text")
        )
        await asyncio.wait_for(goal_started.wait(), timeout=1.0)
        task.cancel()
        await asyncio.wait_for(stop_started.wait(), timeout=1.0)
        task.cancel()
        allow_stop.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(stop_completed.is_set())
        self.assertFalse(self.owner.status()["active"])

    async def test_disconnect_rejects_other_guild_owner(self) -> None:
        await self.connect(guild_id=7)

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_owner_mismatch",
        ):
            await self.owner.disconnect(8)

        self.assertTrue(self.owner.status()["active"])

    async def test_disconnect_still_stops_when_audit_fails(self) -> None:
        await self.connect(guild_id=7)
        self.runtime.calls.clear()

        with patch.object(self.owner, "_append_event", return_value=False):
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
            ):
                await self.owner.disconnect(7)

        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertFalse(status["active"])
        self.assertFalse(status["auditReady"])
        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertEqual(
            status["lastErrorCode"],
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
        )

    async def test_disconnect_surfaces_status_failure_after_safe_stop(
        self,
    ) -> None:
        await self.connect(guild_id=7)
        self.runtime.calls.clear()

        with self.fail_status_writes():
            with self.assertRaisesRegex(
                RuntimeError,
                MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
            ):
                await self.owner.disconnect(7)

        self.assertIn(("disable", 7), self.runtime.calls)
        status = self.owner.status()
        self.assertFalse(status["active"])
        self.assertFalse(status["statusReady"])

    async def test_double_cancelled_disconnect_completes_safe_stop(self) -> None:
        await self.connect(guild_id=7)
        self.runtime.calls.clear()
        stop_started = asyncio.Event()
        allow_stop = asyncio.Event()
        stop_completed = asyncio.Event()

        async def blocking_disable(guild_id: int) -> dict:
            self.runtime.calls.append(("disable", guild_id))
            stop_started.set()
            await allow_stop.wait()
            stop_completed.set()
            return dict(self.runtime.disable_result)

        self.owner.disable_mode = blocking_disable
        task = asyncio.create_task(self.owner.disconnect(7))
        await asyncio.wait_for(stop_started.wait(), timeout=1.0)
        task.cancel()
        task.cancel()
        allow_stop.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(stop_completed.is_set())
        self.assertFalse(self.owner.status()["active"])

    async def test_connect_cannot_replace_other_guild_owner(self) -> None:
        await self.connect(guild_id=7)
        self.runtime.calls.clear()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_owner_mismatch",
        ):
            await self.connect(guild_id=8)

        self.assertEqual(
            self.owner.status()["lease"]["guildId"],
            7,
        )
        self.assertFalse(
            any(call[0] == "disable" for call in self.runtime.calls)
        )

    async def test_connect_failure_revokes_and_stops(self) -> None:
        self.runtime.enable_result = RuntimeError("start failed")
        self.runtime.statuses = [
            {"running": False, "connected": False}
        ]

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            await self.connect()

        self.assertFalse(self.owner.status()["active"])
        self.assertIn(("disable", 7), self.runtime.calls)
        events = self.read_events()
        self.assertEqual(
            sum(row["event"] == "goal_attempted" for row in events),
            1,
        )
        self.assertEqual(
            sum(row["event"] == "goal_failed" for row in events),
            1,
        )

    async def test_cancelled_connect_completes_shielded_safe_stop(self) -> None:
        enable_started = asyncio.Event()
        stop_started = asyncio.Event()
        allow_stop = asyncio.Event()
        stop_completed = asyncio.Event()

        async def blocking_enable(*_args, **_kwargs) -> dict:
            enable_started.set()
            await asyncio.Future()
            return {}

        async def blocking_disable(guild_id: int) -> dict:
            self.runtime.calls.append(("disable", guild_id))
            stop_started.set()
            await allow_stop.wait()
            stop_completed.set()
            return dict(self.runtime.disable_result)

        self.owner.enable_mode = blocking_enable
        self.owner.disable_mode = blocking_disable
        task = asyncio.create_task(self.connect())
        await asyncio.wait_for(enable_started.wait(), timeout=1.0)
        task.cancel()
        await asyncio.wait_for(stop_started.wait(), timeout=1.0)
        task.cancel()
        allow_stop.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(stop_completed.is_set())
        self.assertFalse(self.owner.status()["active"])
        events = self.read_events()
        self.assertTrue(any(row["event"] == "goal_failed" for row in events))

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
