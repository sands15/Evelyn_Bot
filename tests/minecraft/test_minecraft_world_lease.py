from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import threading
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
    validate_world_lease_request,
)
from evelyn_core.minecraft_owner_lock import (  # noqa: E402
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
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
        self.addCleanup(self.owner._owner_lock.release)
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

        def write(path: Path, payload: dict, **kwargs) -> None:
            if Path(path) == target.status_path:
                raise OSError("status artifact unavailable")
            original(path, payload, **kwargs)

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
        self.assertNotIn("expiresMonotonic", json.dumps(status))
        self.assertNotIn("expiresMonotonic", json.dumps(result))
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
        self.assertEqual(
            secret_payload["expiresMonotonic"],
            self.owner._lease.expires_monotonic,
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
        self.addCleanup(owner._owner_lock.release)

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
        owner = MinecraftWorldLeaseOwner(
            status_path=self.root / "secret-failure" / "status.json",
            events_dir=self.root / "secret-failure" / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )
        self.addCleanup(owner._owner_lock.release)
        with patch.object(
            owner,
            "_write_secret",
            side_effect=OSError("secret unavailable"),
        ):
            status = owner.initialize()

        self.assertTrue(status["auditReady"])
        self.assertTrue(status["statusReady"])
        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_world_lease_secret_unavailable",
        )
        self.assertEqual(owner.delegation_token(), "")
        self.assertFalse(owner.secret_path.exists())

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_lease_secret_unavailable",
        ):
            await owner.connect(
                7,
                issuer_ref="discord_user:123",
                source="discord_command",
                goal="diamond",
                ttl_sec=60.0,
            )
        self.assertFalse(any(call[0] == "enable" for call in self.runtime.calls))

    async def test_lease_deadline_secret_failure_withholds_capability(
        self,
    ) -> None:
        original_write = lease_module.atomic_json_write

        def fail_lease_secret(path: Path, payload: dict, **kwargs) -> None:
            if (
                Path(path) == self.owner.secret_path
                and payload.get("leaseId")
            ):
                raise OSError("lease secret unavailable")
            original_write(path, payload, **kwargs)

        with patch.object(
            lease_module,
            "atomic_json_write",
            side_effect=fail_lease_secret,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "minecraft_world_lease_secret_unavailable",
            ):
                await self.connect()

        self.assertIsNone(self.owner._lease)
        self.assertFalse(self.owner.status()["active"])
        self.assertEqual(self.owner.delegation_token(), "")
        self.assertFalse(self.owner.secret_path.exists())
        self.assertFalse(any(call[0] == "enable" for call in self.runtime.calls))
        self.assertIn(("disable", 7), self.runtime.calls)

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
        self.addCleanup(owner._owner_lock.release)

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
        previous_nonce = self.owner.process_nonce
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        # Model an abrupt process death: the kernel releases the OS lock,
        # while claim/status/secret artifacts remain exactly as they were.
        self.owner._owner_lock.release()
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
        self.addCleanup(replacement._owner_lock.release)

        status = replacement.initialize()

        self.assertFalse(status["active"])
        self.assertEqual(status["state"], "authorization_required")
        self.assertNotEqual(
            replacement.authorization_token,
            previous_token,
        )
        self.assertNotEqual(status["processNonce"], previous_nonce)
        self.assertFalse(self.owner.status()["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")
        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=replacement.status_path,
            secret_path=replacement.secret_path,
            owner_claim_path=replacement.owner_claim_path,
            now=self.clock,
            monotonic=replacement.monotonic,
        )
        self.assertFalse(valid)
        self.assertIn(
            error,
            {
                "minecraft_world_authorization_required",
                "minecraft_world_lease_owner_conflict",
                "minecraft_world_lease_secret_mismatch",
            },
        )

    async def test_crash_takeover_invalidates_old_token_before_claim_swap(
        self,
    ) -> None:
        await self.connect()
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        self.owner._owner_lock.release()
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
        self.addCleanup(replacement._owner_lock.release)
        claim_swap_entered = threading.Event()
        allow_claim_swap = threading.Event()
        replacement_status: list[dict] = []
        original_acquire_claim = replacement._acquire_owner_claim

        def blocking_acquire_claim() -> bool:
            claim_swap_entered.set()
            if not allow_claim_swap.wait(timeout=2.0):
                raise OSError("test claim-swap gate timed out")
            return original_acquire_claim()

        worker = threading.Thread(
            target=lambda: replacement_status.append(
                replacement.initialize()
            ),
            daemon=True,
        )
        with patch.object(
            replacement,
            "_acquire_owner_claim",
            side_effect=blocking_acquire_claim,
        ):
            worker.start()
            self.assertTrue(claim_swap_entered.wait(timeout=2.0))
            try:
                action_contender = MinecraftOwnerLock(
                    replacement.world_action_lock_path
                )
                try:
                    with self.assertRaises(MinecraftOwnerLockBusy):
                        action_contender.acquire()
                finally:
                    action_contender.release()
                valid, error = validate_world_lease_request(
                    {"worldLease": old_proof},
                    status_path=self.owner.status_path,
                    secret_path=self.owner.secret_path,
                    owner_claim_path=self.owner.owner_claim_path,
                    now=self.clock,
                    monotonic=self.owner.monotonic,
                )
            finally:
                allow_claim_swap.set()
                worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertFalse(valid)
        self.assertIn(
            error,
            {
                "minecraft_world_lease_secret_missing",
                "minecraft_world_lease_secret_mismatch",
            },
        )
        self.assertEqual(len(replacement_status), 1)
        self.assertEqual(
            replacement_status[0]["state"],
            "authorization_required",
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
        self.addCleanup(competitor._owner_lock.release)

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

    async def test_stale_live_claim_cannot_take_over_lifetime_lock(
        self,
    ) -> None:
        await self.connect()
        original_claim = self.owner.owner_claim_path.read_bytes()
        original_status = self.owner.status_path.read_bytes()
        original_secret = self.owner.secret_path.read_bytes()
        original_events = {
            path.name: path.read_bytes()
            for path in self.owner.events_dir.glob("*.jsonl")
        }
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
        self.addCleanup(replacement._owner_lock.release)

        replacement_status = replacement.initialize()
        old_status = self.owner.status()

        self.assertFalse(replacement_status["ownerClaimOwned"])
        self.assertEqual(
            replacement_status["state"],
            "owner_conflict",
        )
        self.assertTrue(old_status["ownerClaimOwned"])
        self.assertTrue(old_status["active"])
        self.assertNotEqual(self.owner.delegation_token(), "")
        self.assertEqual(
            self.owner.owner_claim_path.read_bytes(),
            original_claim,
        )
        self.assertEqual(self.owner.status_path.read_bytes(), original_status)
        self.assertEqual(self.owner.secret_path.read_bytes(), original_secret)
        self.assertEqual(
            {
                path.name: path.read_bytes()
                for path in self.owner.events_dir.glob("*.jsonl")
            },
            original_events,
        )

    def test_refresh_interleaving_cannot_admit_second_owner(self) -> None:
        self.clock.value += self.owner.owner_claim_stale_sec + 1.0
        refresh_entered = threading.Event()
        allow_refresh = threading.Event()
        refresh_result: list[bool] = []
        original_write = lease_module.atomic_json_write

        def blocking_write(path: Path, payload: dict, **kwargs) -> None:
            if (
                Path(path) == self.owner.owner_claim_path
                and threading.current_thread() is not threading.main_thread()
            ):
                refresh_entered.set()
                if not allow_refresh.wait(timeout=2.0):
                    raise OSError("test refresh gate timed out")
            original_write(path, payload, **kwargs)

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
        self.addCleanup(competitor._owner_lock.release)
        worker = threading.Thread(
            target=lambda: refresh_result.append(
                self.owner._refresh_owner_claim()
            ),
            daemon=True,
        )

        with patch.object(
            lease_module,
            "atomic_json_write",
            side_effect=blocking_write,
        ):
            worker.start()
            self.assertTrue(refresh_entered.wait(timeout=2.0))
            try:
                competitor_status = competitor.initialize()
            finally:
                allow_refresh.set()
                worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(refresh_result, [True])
        self.assertEqual(competitor_status["state"], "owner_conflict")
        self.assertFalse(competitor_status["ownerClaimOwned"])
        self.assertTrue(self.owner.status()["ownerClaimOwned"])
        self.assertEqual(
            json.loads(
                self.owner.owner_claim_path.read_text(encoding="utf-8")
            )["processNonce"],
            self.owner.process_nonce,
        )
        self.assertEqual(competitor.delegation_token(), "")

    async def test_status_interleaving_cannot_overwrite_successor(self) -> None:
        await self.connect()
        self.clock.value += self.owner.owner_claim_stale_sec + 1.0
        status_write_entered = threading.Event()
        allow_status_write = threading.Event()
        status_result: list[bool] = []
        original_write = lease_module.atomic_json_write

        def blocking_write(path: Path, payload: dict, **kwargs) -> None:
            if (
                Path(path) == self.owner.status_path
                and threading.current_thread() is not threading.main_thread()
            ):
                status_write_entered.set()
                if not allow_status_write.wait(timeout=2.0):
                    raise OSError("test status gate timed out")
            original_write(path, payload, **kwargs)

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
        self.addCleanup(competitor._owner_lock.release)
        worker = threading.Thread(
            target=lambda: status_result.append(self.owner._write_status()),
            daemon=True,
        )

        with patch.object(
            lease_module,
            "atomic_json_write",
            side_effect=blocking_write,
        ):
            worker.start()
            self.assertTrue(status_write_entered.wait(timeout=2.0))
            try:
                competitor_status = competitor.initialize()
            finally:
                allow_status_write.set()
                worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(status_result, [True])
        self.assertEqual(competitor_status["state"], "owner_conflict")
        claim = json.loads(
            self.owner.owner_claim_path.read_text(encoding="utf-8")
        )
        status = json.loads(self.owner.status_path.read_text(encoding="utf-8"))
        secret = json.loads(self.owner.secret_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {claim["processNonce"], status["processNonce"], secret["processNonce"]},
            {self.owner.process_nonce},
        )
        self.assertTrue(status["active"])

    def test_release_interleaving_cannot_delete_successor_claim(self) -> None:
        self.clock.value += self.owner.owner_claim_stale_sec + 1.0
        release_entered = threading.Event()
        allow_release = threading.Event()
        original_unlink = Path.unlink

        def blocking_unlink(path: Path, *args, **kwargs) -> None:
            if (
                Path(path) == self.owner.owner_claim_path
                and threading.current_thread() is not threading.main_thread()
            ):
                release_entered.set()
                if not allow_release.wait(timeout=2.0):
                    raise OSError("test release gate timed out")
            original_unlink(path, *args, **kwargs)

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
        self.addCleanup(competitor._owner_lock.release)
        worker = threading.Thread(
            target=self.owner._release_owner_claim,
            daemon=True,
        )

        with patch.object(Path, "unlink", new=blocking_unlink):
            worker.start()
            self.assertTrue(release_entered.wait(timeout=2.0))
            try:
                blocked_status = competitor.initialize()
            finally:
                allow_release.set()
                worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(blocked_status["state"], "owner_conflict")
        self.assertFalse(self.owner._owner_lock.acquired)

        replacement_status = competitor.initialize()

        self.assertTrue(replacement_status["ownerClaimOwned"])
        self.assertTrue(competitor.owner_claim_path.exists())
        self.assertEqual(
            json.loads(
                competitor.owner_claim_path.read_text(encoding="utf-8")
            )["processNonce"],
            competitor.process_nonce,
        )

    def test_owner_lock_unavailable_fails_closed_without_artifacts(
        self,
    ) -> None:
        root = self.root / "lock-unavailable"
        owner = MinecraftWorldLeaseOwner(
            status_path=root / "status.json",
            events_dir=root / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )
        self.addCleanup(owner._owner_lock.release)

        with patch.object(
            owner._owner_lock,
            "acquire",
            side_effect=MinecraftOwnerLockUnavailable(
                "minecraft_owner_lock_unavailable"
            ),
        ):
            status = owner.initialize()

        self.assertEqual(status["state"], "manual_intervention_required")
        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_world_lease_owner_lock_unavailable",
        )
        self.assertFalse(status["ownerLockHeld"])
        self.assertEqual(owner.delegation_token(), "")
        self.assertFalse(owner.owner_claim_path.exists())
        self.assertFalse(owner.status_path.exists())
        self.assertFalse(owner.secret_path.exists())

    def test_inflight_world_action_defers_successor_epoch(self) -> None:
        root = self.root / "action-lock-busy"
        owner = MinecraftWorldLeaseOwner(
            status_path=root / "status.json",
            events_dir=root / "events",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )
        self.addCleanup(owner._owner_lock.release)
        self.addCleanup(owner._world_action_lock.release)
        inflight_action = MinecraftOwnerLock(owner.world_action_lock_path)
        self.addCleanup(inflight_action.release)
        inflight_action.acquire()

        blocked_status = owner.initialize()

        self.assertEqual(
            blocked_status["lastErrorCode"],
            "minecraft_world_action_lock_busy",
        )
        self.assertFalse(blocked_status["ownerLockHeld"])
        self.assertFalse(owner.owner_claim_path.exists())
        self.assertFalse(owner.secret_path.exists())
        self.assertFalse(owner.status_path.exists())

        inflight_action.release()
        replacement_status = owner.initialize()

        self.assertTrue(replacement_status["ownerLockHeld"])
        self.assertTrue(replacement_status["ownerClaimOwned"])
        self.assertEqual(
            replacement_status["state"],
            "authorization_required",
        )

    async def test_clean_shutdown_releases_claim_and_token(
        self,
    ) -> None:
        self.owner._watchdog_task = None

        result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertFalse(self.owner.owner_claim_path.exists())
        self.assertFalse(self.owner.status()["ownerClaimOwned"])
        self.assertEqual(self.owner.delegation_token(), "")
        self.assertTrue(self.owner.owner_lock_path.exists())
        self.assertFalse(self.owner._owner_lock.acquired)

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
        self.addCleanup(replacement._owner_lock.release)
        replacement_status = replacement.initialize()
        self.assertTrue(replacement_status["ownerClaimOwned"])
        self.assertTrue(replacement_status["ownerLockHeld"])
        self.assertEqual(
            replacement_status["state"],
            "authorization_required",
        )

    async def test_already_stopped_shutdown_audits_original_lease(self) -> None:
        connected = await self.connect()
        lease_id = connected["worldLease"]["leaseId"]
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [{"running": False, "connected": False}]

        result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertNotIn(("disable", 7), self.runtime.calls)
        correlated = [
            row
            for row in self.read_events()
            if row["leaseId"] == lease_id
            and row["event"]
            in {"lease_revoked", "runtime_stop_verified"}
        ]
        self.assertEqual(
            [row["event"] for row in correlated],
            ["lease_revoked", "runtime_stop_verified"],
        )
        self.assertTrue(correlated[-1]["verified"])

    async def test_shutdown_waits_for_admitted_world_effect_boundary(
        self,
    ) -> None:
        self.owner._watchdog_task = None
        entered = threading.Event()
        allow_effect_commit = threading.Event()

        class PausedWorldActionLock:
            acquired = False

            def acquire(inner_self) -> None:
                entered.set()
                if not allow_effect_commit.is_set():
                    raise MinecraftOwnerLockBusy(
                        "minecraft_owner_lock_busy"
                    )
                inner_self.acquired = True

            def release(inner_self) -> None:
                inner_self.acquired = False

        action_lock = PausedWorldActionLock()
        self.owner._world_action_lock = action_lock
        self.runtime.calls.clear()

        task = asyncio.create_task(self.owner.shutdown())
        acquired_wait = await asyncio.to_thread(entered.wait, 1.0)

        self.assertTrue(acquired_wait)
        self.assertFalse(task.done())
        self.assertTrue(self.owner._owner_lock.acquired)
        self.assertTrue(self.owner.owner_claim_path.exists())
        self.assertEqual(self.runtime.calls, [])

        allow_effect_commit.set()
        result = await asyncio.wait_for(task, timeout=2.0)

        self.assertTrue(result["stopped"])
        self.assertFalse(self.owner._owner_lock.acquired)
        self.assertFalse(action_lock.acquired)
        self.assertFalse(self.owner.owner_claim_path.exists())

    async def test_cancelled_action_lock_wait_has_no_late_lock_leak(
        self,
    ) -> None:
        self.owner._watchdog_task = None
        holder = MinecraftOwnerLock(self.owner.world_action_lock_path)
        holder.acquire()
        self.addCleanup(holder.release)

        task = asyncio.create_task(
            self.owner._shutdown_serialized_cleanup(reason="shutdown")
        )
        await asyncio.sleep(lease_module.WORLD_ACTION_LOCK_RETRY_SEC * 2)
        task.cancel()
        await asyncio.sleep(lease_module.WORLD_ACTION_LOCK_RETRY_SEC * 2)

        self.assertFalse(task.done())
        self.assertTrue(self.owner._owner_lock.acquired)
        holder.release()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)

        self.assertFalse(self.owner._owner_lock.acquired)
        self.assertFalse(self.owner._world_action_lock.acquired)
        probe = MinecraftOwnerLock(self.owner.world_action_lock_path)
        probe.acquire()
        probe.release()

    async def test_unavailable_action_lock_fences_waits_then_stops_runtime(
        self,
    ) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        self.runtime.calls.clear()
        self.runtime.statuses = [
            {"running": True, "connected": True},
            {"running": False, "connected": False},
        ]
        grace_observed: list[float] = []

        class UnavailableWorldActionLock:
            acquired = False

            @staticmethod
            def acquire() -> None:
                raise MinecraftOwnerLockUnavailable(
                    "minecraft_owner_lock_unavailable"
                )

            @staticmethod
            def acquire_blocking() -> None:
                raise MinecraftOwnerLockUnavailable(
                    "minecraft_owner_lock_unavailable"
                )

            @staticmethod
            def release() -> None:
                return None

        async def cross_stale_window(delay: float) -> None:
            self.assertTrue(self.owner._owner_lock.acquired)
            self.assertFalse(self.owner.secret_path.exists())
            self.assertEqual(self.runtime.calls, [])
            grace_observed.append(delay)
            self.clock.value += delay

        self.owner._world_action_lock = UnavailableWorldActionLock()
        self.owner.sleep = cross_stale_window

        result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertEqual(
            result["error"],
            "minecraft_world_action_lock_unavailable",
        )
        self.assertEqual(
            grace_observed,
            [lease_module.WORLD_LEASE_ARTIFACT_FENCE_GRACE_SEC],
        )
        self.assertIn(("disable", 0), self.runtime.calls)
        self.assertFalse(self.owner._owner_lock.acquired)

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
        isolated_root = self.root / "unwritable-claim"
        isolated_root.mkdir()
        blocking_parent = isolated_root / "not-a-directory"
        blocking_parent.write_text("blocked", encoding="utf-8")
        owner = MinecraftWorldLeaseOwner(
            status_path=isolated_root / "status.json",
            events_dir=isolated_root / "events",
            secret_path=isolated_root / "secret.json",
            owner_claim_path=blocking_parent / "claim.json",
            owner_lock_path=isolated_root / "owner.lock",
            world_action_lock_path=isolated_root / "world-action.lock",
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )
        self.addCleanup(owner._owner_lock.release)

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

    async def test_status_and_secret_failure_invalidates_claim_fallback(
        self,
    ) -> None:
        await self.connect()
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        original_write = lease_module.atomic_json_write
        original_unlink = Path.unlink

        def fail_boundary_write(path: Path, payload: dict, **kwargs) -> None:
            if Path(path) in {self.owner.status_path, self.owner.secret_path}:
                raise OSError("boundary artifact unavailable")
            original_write(path, payload, **kwargs)

        def fail_secret_unlink(path: Path, *args, **kwargs) -> None:
            if Path(path) == self.owner.secret_path:
                raise OSError("secret unlink unavailable")
            original_unlink(path, *args, **kwargs)

        with (
            patch.object(
                lease_module,
                "atomic_json_write",
                side_effect=fail_boundary_write,
            ),
            patch.object(Path, "unlink", new=fail_secret_unlink),
        ):
            result = await self.owner.reconcile_once()

        self.assertEqual(
            result["error"],
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
        )
        self.assertFalse(self.owner.owner_claim_path.exists())
        self.assertEqual(self.owner.delegation_token(), "")
        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=self.owner.status_path,
            secret_path=self.owner.secret_path,
            owner_claim_path=self.owner.owner_claim_path,
            now=self.clock,
            monotonic=self.owner.monotonic,
        )
        self.assertFalse(valid)
        self.assertEqual(error, "minecraft_world_lease_owner_conflict")

    async def test_total_artifact_fence_failure_holds_action_lock_until_stale(
        self,
    ) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        original_write = lease_module.atomic_json_write
        original_unlink = Path.unlink
        grace_observations: list[float] = []

        def fail_boundary_write(path: Path, payload: dict, **kwargs) -> None:
            if Path(path) in {
                self.owner.status_path,
                self.owner.secret_path,
                self.owner.owner_claim_path,
            }:
                raise OSError("all authority artifacts unavailable")
            original_write(path, payload, **kwargs)

        def fail_boundary_unlink(path: Path, *args, **kwargs) -> None:
            if Path(path) in {
                self.owner.secret_path,
                self.owner.owner_claim_path,
            }:
                raise OSError("authority artifact unlink unavailable")
            original_unlink(path, *args, **kwargs)

        async def cross_stale_window(delay: float) -> None:
            self.assertTrue(self.owner._owner_lock.acquired)
            self.assertTrue(self.owner._world_action_lock.acquired)
            contender = MinecraftOwnerLock(
                self.owner.world_action_lock_path,
            )
            with self.assertRaises(MinecraftOwnerLockBusy):
                contender.acquire()
            grace_observations.append(delay)
            self.clock.value += delay

        self.owner.sleep = cross_stale_window
        with (
            patch.object(
                lease_module,
                "atomic_json_write",
                side_effect=fail_boundary_write,
            ),
            patch.object(Path, "unlink", new=fail_boundary_unlink),
        ):
            result = await self.owner.shutdown()

        self.assertTrue(result["stopped"])
        self.assertEqual(
            grace_observations,
            [lease_module.WORLD_LEASE_ARTIFACT_FENCE_GRACE_SEC],
        )
        self.assertFalse(self.owner._owner_lock.acquired)
        self.assertFalse(self.owner._world_action_lock.acquired)
        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=self.owner.status_path,
            secret_path=self.owner.secret_path,
            owner_claim_path=self.owner.owner_claim_path,
            now=self.clock,
            monotonic=self.owner.monotonic,
        )
        self.assertFalse(valid)
        self.assertEqual(error, "minecraft_world_lease_heartbeat_stale")

    async def test_cancelled_fence_grace_finishes_before_lock_release(
        self,
    ) -> None:
        await self.connect()
        self.owner._watchdog_task = None
        original_write = lease_module.atomic_json_write
        original_unlink = Path.unlink
        grace_started = asyncio.Event()
        allow_grace = asyncio.Event()

        def fail_boundary_write(path: Path, payload: dict, **kwargs) -> None:
            if Path(path) in {
                self.owner.status_path,
                self.owner.secret_path,
                self.owner.owner_claim_path,
            }:
                raise OSError("all authority artifacts unavailable")
            original_write(path, payload, **kwargs)

        def fail_boundary_unlink(path: Path, *args, **kwargs) -> None:
            if Path(path) in {
                self.owner.secret_path,
                self.owner.owner_claim_path,
            }:
                raise OSError("authority artifact unlink unavailable")
            original_unlink(path, *args, **kwargs)

        async def blocking_grace(delay: float) -> None:
            grace_started.set()
            await allow_grace.wait()
            self.clock.value += delay

        self.owner.sleep = blocking_grace
        with (
            patch.object(
                lease_module,
                "atomic_json_write",
                side_effect=fail_boundary_write,
            ),
            patch.object(Path, "unlink", new=fail_boundary_unlink),
        ):
            task = asyncio.create_task(
                self.owner._shutdown_serialized_cleanup(
                    reason="shutdown"
                )
            )
            await asyncio.wait_for(grace_started.wait(), timeout=1.0)
            task.cancel()
            task.cancel()
            await asyncio.sleep(0)

            self.assertFalse(task.done())
            self.assertTrue(self.owner._owner_lock.acquired)
            self.assertTrue(self.owner._world_action_lock.acquired)
            allow_grace.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2.0)

        self.assertFalse(self.owner._owner_lock.acquired)
        self.assertFalse(self.owner._world_action_lock.acquired)

    async def test_startup_double_fence_failure_quarantines_action_lock(
        self,
    ) -> None:
        await self.connect()
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        self.owner._owner_lock.release()
        successor = MinecraftWorldLeaseOwner(
            status_path=self.owner.status_path,
            events_dir=self.owner.events_dir,
            secret_path=self.owner.secret_path,
            owner_claim_path=self.owner.owner_claim_path,
            owner_lock_path=self.owner.owner_lock_path,
            world_action_lock_path=self.owner.world_action_lock_path,
            get_runtime_status=self.runtime.status,
            enable_mode=self.runtime.enable,
            disable_mode=self.runtime.disable,
            set_goal=self.runtime.set_goal,
            now=self.clock,
            monotonic=self.clock,
            log=lambda *_args: None,
        )
        self.addCleanup(successor._owner_lock.release)
        self.addCleanup(successor._world_action_lock.release)
        original_write = lease_module.atomic_json_write
        original_unlink = Path.unlink

        def fail_epoch_write(path: Path, payload: dict, **kwargs) -> None:
            if Path(path) in {
                successor.secret_path,
                successor.owner_claim_path,
            }:
                raise OSError("successor epoch unavailable")
            original_write(path, payload, **kwargs)

        def fail_epoch_unlink(path: Path, *args, **kwargs) -> None:
            if Path(path) in {
                successor.secret_path,
                successor.owner_claim_path,
            }:
                raise OSError("predecessor epoch unlink unavailable")
            original_unlink(path, *args, **kwargs)

        with (
            patch.object(
                lease_module,
                "atomic_json_write",
                side_effect=fail_epoch_write,
            ),
            patch.object(Path, "unlink", new=fail_epoch_unlink),
        ):
            status = successor.initialize()

        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_world_lease_owner_claim_write_failed",
        )
        self.assertTrue(successor._owner_lock.acquired)
        self.assertTrue(successor._world_action_lock.acquired)
        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=successor.status_path,
            secret_path=successor.secret_path,
            owner_claim_path=successor.owner_claim_path,
            now=self.clock,
            monotonic=successor.monotonic,
        )
        self.assertTrue(valid, error)
        contender = MinecraftOwnerLock(successor.world_action_lock_path)
        with self.assertRaises(MinecraftOwnerLockBusy):
            contender.acquire()

        successor._watchdog_task = None
        result = await successor.shutdown()
        self.assertTrue(result["stopped"])
        self.assertFalse(successor._owner_lock.acquired)
        self.assertFalse(successor._world_action_lock.acquired)

    async def test_transient_claim_read_failure_revokes_secret_fence(
        self,
    ) -> None:
        await self.connect()
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        original_read_text = Path.read_text

        def fail_claim_read(path: Path, *args, **kwargs) -> str:
            if Path(path) == self.owner.owner_claim_path:
                raise OSError("transient claim read failure")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", new=fail_claim_read):
            self.assertFalse(self.owner._refresh_owner_claim())

        self.assertFalse(self.owner.secret_path.exists())
        self.assertFalse(self.owner._world_action_lock.acquired)
        self.assertEqual(
            self.owner.status()["lastErrorCode"],
            "minecraft_world_lease_owner_claim_failed",
        )
        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=self.owner.status_path,
            secret_path=self.owner.secret_path,
            owner_claim_path=self.owner.owner_claim_path,
            now=self.clock,
            monotonic=self.owner.monotonic,
        )
        self.assertFalse(valid)
        self.assertEqual(error, "minecraft_world_lease_secret_missing")

    async def test_noncanonical_claim_nonce_is_same_fence_for_owner_and_consumer(
        self,
    ) -> None:
        await self.connect()
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )
        original_secret = self.owner.secret_path.read_bytes()
        claim = json.loads(
            self.owner.owner_claim_path.read_text(encoding="utf-8")
        )
        claim["processNonce"] = f" {self.owner.process_nonce} "
        self.owner.owner_claim_path.write_text(
            json.dumps(claim),
            encoding="utf-8",
        )

        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=self.owner.status_path,
            secret_path=self.owner.secret_path,
            owner_claim_path=self.owner.owner_claim_path,
            now=self.clock,
            monotonic=self.owner.monotonic,
        )
        self.assertFalse(valid)
        self.assertEqual(error, "minecraft_world_lease_owner_conflict")

        self.owner._mark_status_write_failed()

        self.assertEqual(self.owner.secret_path.read_bytes(), original_secret)
        self.assertEqual(
            self.owner.status()["lastErrorCode"],
            "minecraft_world_lease_status_write_failed",
        )

    def test_claim_conflict_is_not_overwritten_by_status_failure(self) -> None:
        original_secret = self.owner.secret_path.read_bytes()
        replacement_claim = {
            "schema": "minecraft_world_lease.owner_claim.v1",
            "processNonce": "replacement-owner",
            "updatedAt": self.clock(),
            "pid": 999,
        }
        self.owner.owner_claim_path.write_text(
            json.dumps(replacement_claim),
            encoding="utf-8",
        )

        self.owner._mark_status_write_failed()

        status = self.owner.status()
        self.assertEqual(status["state"], "owner_conflict")
        self.assertEqual(
            status["lastErrorCode"],
            "minecraft_world_lease_owner_conflict",
        )
        self.assertEqual(self.owner.secret_path.read_bytes(), original_secret)

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

    async def test_reconcile_audits_inactive_runtime_once_per_epoch(self) -> None:
        first = await self.owner.reconcile_once(reason="process_restart")
        second = await self.owner.reconcile_once(reason="watchdog_retry")

        self.assertEqual(first["action"], "already_stopped")
        self.assertEqual(second["action"], "already_stopped")
        stops = [
            row
            for row in self.read_events()
            if row["event"] == "runtime_stop_verified"
            and row["leaseId"] == ""
        ]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["guildId"], 0)
        self.assertTrue(stops[0]["verified"])

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

    async def test_monotonic_ttl_blocks_goal_after_wall_clock_rollback(
        self,
    ) -> None:
        monotonic = FakeClock(0.0)
        self.owner.monotonic = monotonic
        await self.connect()
        self.runtime.calls.clear()

        self.clock.value = 900.0
        monotonic.value = 61.0

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await self.owner.set_goal(7, "post-ttl-effect")

        self.assertFalse(
            any(call[0] == "goal" for call in self.runtime.calls)
        )
        self.assertFalse(self.owner.status()["active"])

    async def test_old_proof_fails_after_monotonic_expiry_and_wall_rollback(
        self,
    ) -> None:
        monotonic = FakeClock(0.0)
        self.owner.monotonic = monotonic
        await self.connect()
        old_proof = next(
            call[1][2]
            for call in self.runtime.calls
            if call[0] == "enable"
        )

        self.clock.value = 999.0
        monotonic.value = 30.0
        self.assertTrue(self.owner._write_status())
        monotonic.value = 61.0

        valid, error = validate_world_lease_request(
            {"worldLease": old_proof},
            status_path=self.owner.status_path,
            secret_path=self.owner.secret_path,
            owner_claim_path=self.owner.owner_claim_path,
            now=self.clock,
            monotonic=monotonic,
        )

        self.assertFalse(valid)
        self.assertEqual(error, "minecraft_world_lease_expired")

    async def test_goal_requires_active_matching_lease(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await self.owner.set_goal(7, "diamond")

        await self.connect()
        lease_id = self.owner.status()["lease"]["leaseId"]
        self.runtime.calls.clear()
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await self.owner.set_goal(
                7,
                "private_goal_text",
                expected_lease_id="stale-lease",
            )
        self.assertFalse(
            any(call[0] == "goal" for call in self.runtime.calls)
        )

        result = await self.owner.set_goal(
            7,
            "private_goal_text",
            expected_lease_id=lease_id,
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

    async def test_exact_disconnect_rejects_replaced_same_guild_lease(
        self,
    ) -> None:
        await self.owner.connect(
            7,
            issuer_ref="discord_user:1",
            source="discord_command",
        )
        old_lease_id = self.owner.status()["lease"]["leaseId"]
        await self.owner.connect(
            7,
            issuer_ref="discord_user:2",
            source="discord_command",
        )
        current_lease_id = self.owner.status()["lease"]["leaseId"]

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await self.owner.disconnect(
                7,
                expected_lease_id=old_lease_id,
            )

        self.assertNotEqual(old_lease_id, current_lease_id)
        self.assertEqual(
            self.owner.status()["lease"]["leaseId"],
            current_lease_id,
        )

        self.assertTrue(self.owner.status()["active"])

    async def test_disconnect_stop_audit_keeps_revoked_lease_id(self) -> None:
        connected = await self.connect(guild_id=7)
        lease_id = connected["worldLease"]["leaseId"]

        stopped = await self.owner.disconnect(7)

        self.assertTrue(stopped["outcome_verified"])
        events = self.read_events()
        correlated = [
            row
            for row in events
            if row["event"]
            in {
                "lease_revoked",
                "runtime_stop_attempted",
                "runtime_stop_verified",
            }
        ]
        self.assertEqual(len(correlated), 3)
        self.assertEqual({row["leaseId"] for row in correlated}, {lease_id})

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

    async def test_connect_rechecks_lease_expiry_after_enable(self) -> None:
        monotonic = FakeClock(0.0)
        self.owner.monotonic = monotonic

        async def expiring_enable(
            guild_id: int,
            *,
            goal: str | None = None,
            world_lease: dict | None = None,
        ) -> dict:
            self.runtime.calls.append(
                (
                    "enable",
                    (guild_id, goal, dict(world_lease or {})),
                )
            )
            self.clock.value = 999.0
            monotonic.value = 61.0
            return {
                "connected": True,
                "goal": goal,
                "outcome_verified": True,
                "outcome_code": "minecraft_connected",
            }

        self.owner.enable_mode = expiring_enable

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_world_authorization_required",
        ):
            await self.connect()

        self.assertIn(("disable", 7), self.runtime.calls)
        self.assertFalse(self.owner.status()["active"])
        events = self.read_events()
        self.assertFalse(
            any(row["event"] == "runtime_start_verified" for row in events)
        )
        self.assertFalse(
            any(row["event"] == "goal_verified" for row in events)
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
