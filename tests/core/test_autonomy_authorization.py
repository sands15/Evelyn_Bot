from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy import (  # noqa: E402
    AutonomyEngine,
    AutonomyPlan,
)
from evelyn_core.autonomy_authorization import (  # noqa: E402
    ASSISTANT_AUTONOMY_ACTIONS,
    AUTONOMY_AUTHORIZATION_DECISION_SCHEMA,
    AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
    SUPPORTED_AUTONOMY_ACTIONS,
    AutonomyAuthorizationManager,
)
from evelyn_core.autonomy_outcome_evidence import (  # noqa: E402
    autonomy_outcome_verified,
    expected_autonomy_evidence_codes,
)
from evelyn_core.self_model import EvelynSelfState  # noqa: E402


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.value = now

    def __call__(self) -> float:
        return self.value


class DummyExecutor:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {
            "status": "ok",
            "verified": True,
            "evidence_code": "no_side_effect_required",
        }
        self.connect_count = 0
        self.disconnect_count = 0
        self.execute_count = 0

    async def connect(self) -> None:
        self.connect_count += 1

    async def disconnect(self) -> None:
        self.disconnect_count += 1

    async def observe(self) -> dict:
        return {
            "environment": "assistant",
            "quiet_hours": True,
        }

    async def execute_step(self, step: dict) -> dict:
        self.execute_count += 1
        return dict(self.result)


class MinecraftExecutor(DummyExecutor):
    def __init__(
        self,
        *,
        hunger: float = 20,
        inventory: dict[str, int] | None = None,
        result: dict | None = None,
    ) -> None:
        super().__init__(result)
        self.hunger = hunger
        self.inventory = inventory or {}

    async def observe(self) -> dict:
        return {
            "active_environment": "minecraft",
            "environments": {
                "minecraft": {
                    "connected": True,
                    "health": 20,
                    "hunger": self.hunger,
                    "inventory": dict(self.inventory),
                    "hostiles_nearby": [],
                    "immediate_hazards": [],
                }
            },
        }


class AutonomyAuthorizationManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.clock = FakeClock()
        self.manager = AutonomyAuthorizationManager(
            status_path=self.root / "status.json",
            events_dir=self.root / "events",
            now=self.clock,
            default_ttl_sec=300.0,
            max_ttl_sec=600.0,
        )
        self.manager.initialize()

    def read_events(self) -> list[dict]:
        rows: list[dict] = []
        for path in (self.root / "events").glob("*.jsonl"):
            rows.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        return rows

    def test_cleanup_exact_issuer_keeps_unattributed_legacy_manual(self) -> None:
        granted = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:target",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        self.assertTrue(granted["ok"])
        event_path = next((self.root / "events").glob("*.jsonl"))
        with event_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"guildId": 7, "at": 1000.0}) + "\n")

        def target(row: dict) -> bool | None:
            if row.get("guildId") != 7:
                return False
            issuer = row.get("issuerRef")
            if not issuer:
                return None
            return issuer == "discord_user:target"

        result = self.manager.cleanup_exact_targets(target)

        self.assertEqual(result, (2, 0, 1))
        self.assertEqual(self.manager.authorized_actions(7), [])
        fresh = event_path.read_text(encoding="utf-8")
        self.assertNotIn("discord_user:target", fresh)
        self.assertIn('"guildId": 7', fresh)

    def test_cleanup_fails_closed_on_malformed_event(self) -> None:
        event_path = next((self.root / "events").glob("*.jsonl"))
        event_path.write_text("{malformed\n", encoding="utf-8")

        result = self.manager.cleanup_exact_targets(lambda _row: True)

        self.assertEqual(result, (0, 1, 1))
        self.assertEqual(event_path.read_text(encoding="utf-8"), "{malformed\n")

    def test_grant_is_scoped_short_lived_and_public_status_hides_issuer(
        self,
    ) -> None:
        granted = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=[
                "assistant:idle",
                "minecraft:melee_attack",
                "shell:run",
            ],
            ttl_sec=9999.0,
        )

        self.assertTrue(granted["ok"])
        grant = granted["grant"]
        self.assertEqual(
            grant["scopes"],
            ["assistant:idle", "minecraft:melee_attack"],
        )
        self.assertEqual(grant["expiresAt"], 1600.0)
        status = self.manager.status()
        self.assertEqual(
            status["schema"],
            AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
        )
        self.assertEqual(status["activeGrantCount"], 1)
        serialized_status = json.dumps(status)
        self.assertNotIn("discord_user", serialized_status)
        self.assertFalse(status["policy"]["restoredAfterRestart"])

    def test_authorize_requires_exact_active_scope_and_expires(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
            ttl_sec=60.0,
        )

        allowed = self.manager.authorize(7, "assistant:idle")
        denied = self.manager.authorize(
            7,
            "assistant:send_followup",
        )
        self.clock.value = 1061.0
        expired = self.manager.authorize(7, "assistant:idle")

        self.assertEqual(
            allowed["schema"],
            AUTONOMY_AUTHORIZATION_DECISION_SCHEMA,
        )
        self.assertTrue(allowed["allowed"])
        self.assertEqual(denied["code"], "authorization_scope_denied")
        self.assertEqual(expired["code"], "authorization_required")
        self.assertEqual(self.manager.status()["activeGrantCount"], 0)

    def test_monotonic_ttl_survives_wall_clock_rollback(self) -> None:
        monotonic = FakeClock(0.0)
        self.manager.monotonic = monotonic
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
            ttl_sec=60.0,
        )

        self.clock.value = 900.0
        monotonic.value = 61.0
        expired = self.manager.authorize(7, "assistant:idle")

        self.assertFalse(expired["allowed"])
        self.assertEqual(expired["code"], "authorization_required")
        self.assertEqual(self.manager.status()["activeGrantCount"], 0)

    def test_authorization_and_outcome_share_explicit_action_run_id(
        self,
    ) -> None:
        grant = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )["grant"]
        action_run_id = "action-run-123"

        decision = self.manager.authorize(
            7,
            "assistant:idle",
            action_run_id=action_run_id,
        )
        self.manager.record_outcome(
            7,
            "assistant:idle",
            {
                "status": "ok",
                "verified": True,
                "evidence_code": "no_side_effect_required",
                "_authorization_grant_id": grant["grantId"],
                "_action_run_id": action_run_id,
            },
        )

        relevant = [
            row
            for row in self.read_events()
            if row["event"] in {"action_authorized", "action_outcome"}
        ]
        self.assertEqual(decision["actionRunId"], action_run_id)
        self.assertEqual(len(relevant), 2)
        self.assertEqual(
            {row["actionRunId"] for row in relevant},
            {action_run_id},
        )
        self.assertTrue(relevant[-1]["verified"])
        self.assertEqual(
            relevant[-1]["evidenceCode"],
            "no_side_effect_required",
        )

    def test_grant_rejects_unknown_authorization_source(self) -> None:
        result = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="untrusted_proxy",
            scopes=["assistant:idle"],
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "authorization_source_invalid",
        )
        self.assertEqual(self.manager.status()["activeGrantCount"], 0)

    def test_initialize_never_restores_prior_process_grants(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        replacement = AutonomyAuthorizationManager(
            status_path=self.root / "status.json",
            events_dir=self.root / "events",
            now=self.clock,
        )

        status = replacement.initialize()

        self.assertEqual(status["activeGrantCount"], 0)
        self.assertEqual(status["state"], "authorization_required")
        self.assertEqual(replacement.authorized_actions(7), [])

    def test_outcome_journal_excludes_raw_arguments_and_payloads(self) -> None:
        private_evidence = "sk_live_PRIVATE_TOKEN_123"
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        self.manager.record_outcome(
            7,
            "assistant:idle",
            {
                "status": "ok",
                "verified": True,
                "evidence_code": private_evidence,
                "raw": "private transcript C:\\secret",
            },
        )

        serialized = json.dumps(self.read_events())

        self.assertNotIn("private transcript", serialized)
        self.assertNotIn("C:\\\\secret", serialized)
        self.assertNotIn(private_evidence, serialized)
        self.assertIn("discord_user:123", serialized)
        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertEqual(outcome["outcomeStatus"], "unverified")
        self.assertFalse(outcome["verified"])
        self.assertEqual(outcome["evidenceCode"], "")

    def test_outcome_journal_drops_cross_action_evidence(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )

        receipt = self.manager.record_outcome(
            7,
            "assistant:idle",
            {
                "status": "ok",
                "verified": True,
                "evidence_code": "discord_send_completed",
            },
        )

        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertEqual(
            receipt,
            {
                "recorded": True,
                "verified": False,
                "authorizationCurrent": True,
            },
        )
        self.assertEqual(outcome["evidenceCode"], "")

    def test_every_supported_action_has_exact_evidence_policy(self) -> None:
        for action in SUPPORTED_AUTONOMY_ACTIONS:
            with self.subTest(action=action):
                codes = expected_autonomy_evidence_codes(action)
                self.assertTrue(codes)
                for code in codes:
                    self.assertTrue(code)
                    self.assertEqual(code, code.strip())

    def test_outcome_policy_rejects_unrelated_nonempty_evidence(self) -> None:
        self.assertFalse(
            autonomy_outcome_verified(
                "assistant:idle",
                {
                    "status": "ok",
                    "verified": True,
                    "evidence_code": "discord_send_completed",
                },
            )
        )

    def test_audit_event_is_flushed_and_synced_before_return(self) -> None:
        with patch(
            "evelyn_core.autonomy_authorization.os.fsync"
        ) as sync:
            self.manager.record_outcome(
                7,
                "assistant:idle",
                {
                    "status": "ok",
                    "verified": True,
                    "evidence_code": "no_side_effect_required",
                },
            )

        sync.assert_called_once()

    def test_grant_fails_closed_when_audit_journal_is_unavailable(
        self,
    ) -> None:
        blocked_root = self.root / "blocked-events"
        blocked_root.write_text("not a directory", encoding="utf-8")
        manager = AutonomyAuthorizationManager(
            status_path=self.root / "blocked-status.json",
            events_dir=blocked_root,
            now=self.clock,
        )

        initialized = manager.initialize()
        granted = manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )

        self.assertEqual(
            initialized["state"],
            "authorization_audit_unavailable",
        )
        self.assertFalse(initialized["auditReady"])
        self.assertFalse(granted["ok"])
        self.assertEqual(
            granted["error"],
            "authorization_audit_unavailable",
        )
        self.assertEqual(manager.authorized_actions(7), [])

    def test_authorize_fails_closed_if_decision_cannot_be_audited(
        self,
    ) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )

        with patch.object(
            self.manager,
            "_append_event",
            return_value=False,
        ):
            decision = self.manager.authorize(
                7,
                "assistant:idle",
            )

        self.assertFalse(decision["allowed"])
        self.assertEqual(
            decision["code"],
            "authorization_audit_unavailable",
        )
        self.assertEqual(self.manager.authorized_actions(7), [])
        self.assertEqual(
            self.manager.status()["state"],
            "authorization_audit_unavailable",
        )

    def test_outcome_is_not_attributed_to_replacement_grant(self) -> None:
        first = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )["grant"]
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:456",
            source="discord_command",
            scopes=["assistant:idle"],
        )

        self.manager.record_outcome(
            7,
            "assistant:idle",
            {
                "status": "ok",
                "verified": True,
                "evidence_code": "no_side_effect_required",
                "_authorization_grant_id": first["grantId"],
            },
        )

        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertEqual(outcome["grantId"], first["grantId"])
        self.assertFalse(outcome["authorizationCurrent"])
        self.assertFalse(outcome["verified"])
        self.assertEqual(outcome["outcomeStatus"], "unverified")


class AutonomyEngineAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.clock = FakeClock()
        self.manager = AutonomyAuthorizationManager(
            status_path=self.root / "status.json",
            events_dir=self.root / "events",
            now=self.clock,
            default_ttl_sec=60.0,
            max_ttl_sec=60.0,
        )
        self.manager.initialize()

    def read_events(self) -> list[dict]:
        rows: list[dict] = []
        for path in (self.root / "events").glob("*.jsonl"):
            rows.extend(
                json.loads(line)
                for line in path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            )
        return rows

    def engine(self, executor: DummyExecutor) -> AutonomyEngine:
        engine = AutonomyEngine(
            guild_id=7,
            executor=executor,
            get_authorized_actions=self.manager.authorized_actions,
            authorize_action=self.manager.authorize,
            record_action_outcome=self.manager.record_outcome,
        )
        engine.load_persisted_state = lambda: None
        engine.persist_state = lambda: None
        return engine

    def minecraft_engine(self, executor: DummyExecutor) -> AutonomyEngine:
        scopes = [
            *ASSISTANT_AUTONOMY_ACTIONS,
            "minecraft:find_food_source",
        ]
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=scopes,
        )
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = scopes
        return engine

    async def test_disconnect_failure_keeps_retry_state(self) -> None:
        class FlakyDisconnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.fail_disconnect = True

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                if inner_self.fail_disconnect:
                    raise RuntimeError(
                        "minecraft_action_cancel_unverified"
                    )

        executor = FlakyDisconnectExecutor()
        engine = self.engine(executor)
        await engine._connect_executor_once()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await engine._disconnect_executor_once()

        self.assertTrue(engine._executor_connected)
        executor.fail_disconnect = False
        await engine._disconnect_executor_once()
        self.assertFalse(engine._executor_connected)
        self.assertEqual(executor.disconnect_count, 2)

    async def test_cleanup_stops_exact_engine_and_freshly_clears_history(self) -> None:
        path = self.root / "guild-7-autonomy.json"
        executor = DummyExecutor()
        engine = AutonomyEngine(guild_id=7, executor=executor)
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.last_observation = {"private": "PRIVATE_OBSERVATION"}
        engine.state.current_plan = AutonomyPlan(
            goal_kind="private",
            summary="PRIVATE_PLAN",
        )
        engine.state.drive_state = {"private": "PRIVATE_DRIVE"}
        engine._executor_connected = True
        engine._task = asyncio.create_task(asyncio.sleep(60.0))

        with patch(
            "evelyn_core.autonomy.cognitive_state_path",
            return_value=path,
        ):
            result = await engine.cleanup_history_state(timeout_sec=1.0)

        self.assertEqual(result, (1, 0, 0))
        self.assertFalse(engine._executor_connected)
        self.assertIsNone(engine._task)
        self.assertEqual(engine.state.last_observation, {})
        self.assertIsNone(engine.state.current_plan)
        self.assertNotIn("PRIVATE", path.read_text(encoding="utf-8"))

    async def test_cleanup_timeout_is_retryable_until_disconnect_drains(self) -> None:
        class BlockingDisconnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.disconnect_started = asyncio.Event()
                inner_self.disconnect_release = asyncio.Event()

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                inner_self.disconnect_started.set()
                await inner_self.disconnect_release.wait()

        path = self.root / "guild-7-autonomy.json"
        executor = BlockingDisconnectExecutor()
        engine = AutonomyEngine(guild_id=7, executor=executor)
        engine._executor_connected = True
        with patch(
            "evelyn_core.autonomy.cognitive_state_path",
            return_value=path,
        ):
            pending = await engine.cleanup_history_state(timeout_sec=0.01)
            self.assertEqual(pending, (0, 1, 0))
            self.assertTrue(engine._executor_connected)
            executor.disconnect_release.set()
            completed = await engine.cleanup_history_state(timeout_sec=1.0)

        self.assertEqual(completed, (1, 0, 0))
        self.assertFalse(engine._executor_connected)

    async def test_start_fails_closed_without_current_process_grant(
        self,
    ) -> None:
        executor = DummyExecutor()
        engine = self.engine(executor)

        with self.assertRaisesRegex(
            PermissionError,
            "autonomy_authorization_required",
        ):
            await engine.start()

        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "authorization_required")
        self.assertEqual(executor.connect_count, 0)

    async def test_start_rechecks_currentness_after_connect_before_commit(
        self,
    ) -> None:
        class BlockingConnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.connect_started = asyncio.Event()
                inner_self.connect_release = asyncio.Event()
                inner_self.connected = False

            async def connect(inner_self) -> None:
                inner_self.connect_count += 1
                inner_self.connect_started.set()
                await inner_self.connect_release.wait()
                inner_self.connected = True

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                inner_self.connected = False

        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        executor = BlockingConnectExecutor()
        engine = self.engine(executor)
        current = True
        start_task = asyncio.create_task(
            engine.start(is_current=lambda: current)
        )
        connect_wait = asyncio.create_task(
            executor.connect_started.wait()
        )
        done, _pending = await asyncio.wait(
            {start_task, connect_wait},
            timeout=1.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if start_task in done:
            await start_task
        self.assertIn(connect_wait, done)

        # A guild reset can pass while start is awaiting executor connect:
        # no loop is visible yet and the persisted state is still idle.
        self.assertIsNone(engine._task)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "idle")
        current = False
        engine.state.allowed_actions = []
        executor.connect_release.set()

        started = await asyncio.wait_for(start_task, timeout=1.0)
        self.assertFalse(started)
        self.assertFalse(executor.connected)
        self.assertEqual(executor.disconnect_count, 1)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "idle")
        self.assertIsNone(engine._task)

    async def test_start_rechecks_grant_expiry_after_executor_connect(
        self,
    ) -> None:
        class BlockingConnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.connect_started = asyncio.Event()
                inner_self.connect_release = asyncio.Event()
                inner_self.connected = False

            async def connect(inner_self) -> None:
                inner_self.connect_count += 1
                inner_self.connect_started.set()
                await inner_self.connect_release.wait()
                inner_self.connected = True

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                inner_self.connected = False

        monotonic = FakeClock(0.0)
        self.manager.monotonic = monotonic
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
            ttl_sec=60.0,
        )
        executor = BlockingConnectExecutor()
        engine = self.engine(executor)
        start_task = asyncio.create_task(engine.start())
        await asyncio.wait_for(
            executor.connect_started.wait(),
            timeout=1.0,
        )

        self.clock.value = 900.0
        monotonic.value = 61.0
        executor.connect_release.set()

        with self.assertRaisesRegex(
            PermissionError,
            "autonomy_authorization_required",
        ):
            await asyncio.wait_for(start_task, timeout=1.0)

        self.assertFalse(executor.connected)
        self.assertEqual(executor.disconnect_count, 1)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "authorization_required")
        self.assertEqual(engine.state.allowed_actions, [])
        self.assertIsNone(engine._task)

    async def test_start_replaces_live_disabled_loop(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        executor = DummyExecutor()
        engine = self.engine(executor)
        await engine.start()
        old_task = engine._task
        await asyncio.sleep(0)

        engine.state.enabled = False
        engine.state.status = "authorization_required"
        await engine.start()

        self.assertIsNotNone(old_task)
        self.assertTrue(old_task.done())
        self.assertIsNot(engine._task, old_task)
        self.assertTrue(engine.state.enabled)
        self.assertEqual(engine.state.status, "running")
        self.assertEqual(executor.connect_count, 2)
        self.assertEqual(executor.disconnect_count, 1)
        await engine.stop()

    async def test_start_waits_for_inflight_stop_cleanup(self) -> None:
        class BlockingDisconnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.connected = False
                inner_self.disconnect_started = asyncio.Event()
                inner_self.disconnect_release = asyncio.Event()
                inner_self.events: list[str] = []

            async def connect(inner_self) -> None:
                await super().connect()
                inner_self.connected = True
                inner_self.events.append("connect")

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                inner_self.events.append("disconnect_started")
                inner_self.disconnect_started.set()
                await inner_self.disconnect_release.wait()
                inner_self.connected = False
                inner_self.events.append("disconnect_finished")

        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        executor = BlockingDisconnectExecutor()
        engine = self.engine(executor)
        await engine.start()

        stop_task = asyncio.create_task(engine.stop())
        await executor.disconnect_started.wait()
        start_task = asyncio.create_task(engine.start())
        await asyncio.sleep(0)
        start_completed_during_stop = start_task.done()

        executor.disconnect_release.set()
        await stop_task
        await start_task

        self.assertFalse(start_completed_during_stop)
        self.assertEqual(
            executor.events[:3],
            ["connect", "disconnect_started", "disconnect_finished"],
        )
        self.assertEqual(executor.events[3], "connect")
        self.assertTrue(executor.connected)
        self.assertTrue(engine.state.enabled)
        self.assertEqual(engine.state.status, "running")
        await engine.stop()

    async def test_stop_propagates_caller_cancellation_during_cleanup(
        self,
    ) -> None:
        class BlockingDisconnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.disconnect_started = asyncio.Event()

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                if inner_self.disconnect_count == 1:
                    inner_self.disconnect_started.set()
                    await asyncio.Event().wait()

        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        executor = BlockingDisconnectExecutor()
        engine = self.engine(executor)
        await engine.start()
        stop_task = asyncio.create_task(engine.stop())
        await asyncio.wait_for(
            executor.disconnect_started.wait(),
            timeout=1.0,
        )

        stop_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await stop_task

        self.assertEqual(executor.disconnect_count, 1)
        self.assertEqual(engine.state.status, "stopping")
        await engine.stop()
        self.assertEqual(executor.disconnect_count, 2)
        self.assertEqual(engine.state.status, "idle")

    async def test_start_retries_failed_stop_cleanup_before_reconnect(
        self,
    ) -> None:
        class FlakyDisconnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.disconnect_failures = 2

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                if inner_self.disconnect_failures > 0:
                    inner_self.disconnect_failures -= 1
                    raise RuntimeError(
                        "minecraft_action_cancel_unverified"
                    )

        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        executor = FlakyDisconnectExecutor()
        engine = self.engine(executor)
        await engine.start()

        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await engine.stop()
        with self.assertRaisesRegex(
            RuntimeError,
            "minecraft_action_cancel_unverified",
        ):
            await engine.start()

        self.assertIsNone(engine._task)
        self.assertFalse(engine.state.enabled)
        self.assertTrue(engine._executor_connected)
        await engine.start()

        self.assertTrue(engine.state.enabled)
        self.assertEqual(engine.state.status, "running")
        self.assertEqual(executor.disconnect_count, 3)
        self.assertEqual(executor.connect_count, 2)
        await engine.stop()

    async def test_start_propagates_cancellation_during_stale_cleanup(
        self,
    ) -> None:
        class BlockingDisconnectExecutor(DummyExecutor):
            def __init__(inner_self) -> None:
                super().__init__()
                inner_self.disconnect_started = asyncio.Event()
                inner_self.disconnect_release = asyncio.Event()

            async def disconnect(inner_self) -> None:
                inner_self.disconnect_count += 1
                inner_self.disconnect_started.set()
                await inner_self.disconnect_release.wait()

        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=ASSISTANT_AUTONOMY_ACTIONS,
        )
        executor = BlockingDisconnectExecutor()
        engine = self.engine(executor)
        await engine.start()
        self.addAsyncCleanup(engine.stop)
        await asyncio.sleep(0)

        engine.state.enabled = False
        engine.state.status = "authorization_required"
        start_task = asyncio.create_task(engine.start())
        await executor.disconnect_started.wait()
        start_task.cancel()
        executor.disconnect_release.set()

        with self.assertRaises(asyncio.CancelledError):
            await start_task
        self.assertIsNone(engine._task)
        self.assertFalse(engine.state.enabled)

    async def test_generated_minecraft_plan_waits_outside_current_grant(
        self,
    ) -> None:
        executor = MinecraftExecutor()
        engine = self.minecraft_engine(executor)

        with patch(
            "evelyn_core.autonomy.update_self_state_from_observation",
            return_value=EvelynSelfState(),
        ):
            cycle = await engine.run_cycle()

        self.assertEqual(cycle.selected_goal.kind, "progress")
        self.assertIsNone(cycle.planned)
        self.assertEqual(cycle.step_result, {})
        self.assertTrue(engine.state.enabled)
        self.assertEqual(engine.state.status, "running")
        self.assertEqual(executor.execute_count, 0)

    async def test_food_plan_runs_only_currently_authorized_prefix(
        self,
    ) -> None:
        executor = MinecraftExecutor(
            hunger=8,
            result={
                "status": "ok",
                "verified": True,
                "evidence_code": "minecraft_find_food_source_completed",
            },
        )
        engine = self.minecraft_engine(executor)

        with patch(
            "evelyn_core.autonomy.update_self_state_from_observation",
            return_value=EvelynSelfState(),
        ):
            first = await engine.run_cycle()
            executor.inventory = {"bread": 1}
            second = await engine.run_cycle()

        self.assertEqual(
            [step["action"] for step in first.planned.steps],
            ["find_food_source"],
        )
        self.assertEqual(first.planned.cursor, 1)
        self.assertIsNone(second.planned)
        self.assertEqual(second.step_result, {})
        self.assertEqual(executor.execute_count, 1)
        self.assertTrue(engine.state.enabled)
        self.assertEqual(engine.state.status, "running")

    async def test_injected_minecraft_plan_outside_grant_still_denies(
        self,
    ) -> None:
        executor = DummyExecutor()
        engine = self.minecraft_engine(executor)
        plan = AutonomyPlan(
            goal_kind="progress",
            summary="gather logs",
            steps=[
                {
                    "domain": "minecraft",
                    "action": "gather_logs",
                }
            ],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "authorization_scope_denied")
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.allowed_actions, [])
        self.assertEqual(plan.cursor, 0)
        self.assertEqual(executor.execute_count, 0)

    async def test_verified_outcome_advances_but_unverified_does_not(
        self,
    ) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        executor = DummyExecutor(
            {
                "status": "ok",
                "verified": False,
            }
        )
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[
                {
                    "domain": "assistant",
                    "action": "idle",
                }
            ],
        )

        unverified = await engine.execute_next_step(plan)
        self.assertEqual(plan.cursor, 0)
        executor.result = {
            "status": "ok",
            "verified": True,
            "evidence_code": "no_side_effect_required",
        }
        verified = await engine.execute_next_step(plan)

        self.assertEqual(unverified["status"], "unverified")
        self.assertEqual(unverified["reason"], "outcome_unverified")
        self.assertEqual(plan.cursor, 1)
        self.assertEqual(verified["status"], "ok")

    async def test_outcome_audit_failure_does_not_advance_minecraft_plan(
        self,
    ) -> None:
        executor = MinecraftExecutor(
            hunger=8,
            result={
                "status": "ok",
                "verified": True,
                "evidence_code": (
                    "minecraft_find_food_source_completed"
                ),
            },
        )
        engine = self.minecraft_engine(executor)
        plan = AutonomyPlan(
            goal_kind="eat",
            summary="find food",
            steps=[
                {
                    "domain": "minecraft",
                    "action": "find_food_source",
                }
            ],
        )
        append_event = self.manager._append_event

        def fail_outcome_audit(
            event: str,
            **kwargs: object,
        ) -> bool:
            if event == "action_outcome":
                return False
            return append_event(event, **kwargs)

        with patch.object(
            self.manager,
            "_append_event",
            side_effect=fail_outcome_audit,
        ):
            result = await engine.execute_next_step(plan)

        self.assertEqual(executor.execute_count, 1)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["reason"],
            "authorization_audit_unavailable",
        )
        self.assertFalse(result["verified"])
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "authorization_required")
        self.assertEqual(engine.state.allowed_actions, [])
        self.assertEqual(self.manager.authorized_actions(7), [])
        self.assertFalse(self.manager.status()["auditReady"])
        self.assertFalse(
            any(
                row["event"] == "action_outcome"
                for row in self.read_events()
            )
        )

    async def test_outcome_audit_exception_disables_engine_before_effect_can_repeat(
        self,
    ) -> None:
        executor = DummyExecutor()
        engine = self.engine(executor)
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        def unavailable_audit(
            _guild_id: int,
            _action: str,
            _result: dict,
        ) -> dict:
            raise OSError("audit storage unavailable")

        engine.record_action_outcome = unavailable_audit
        result = await engine.execute_next_step(plan)

        self.assertEqual(executor.execute_count, 1)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "authorization_audit_unavailable")
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "authorization_required")
        self.assertEqual(engine.state.allowed_actions, [])

    async def test_missing_outcome_audit_receipt_cannot_advance_effect(self) -> None:
        executor = DummyExecutor()
        engine = self.engine(executor)
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        engine.record_action_outcome = lambda *_args: None
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "authorization_audit_unavailable")
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)

    async def test_outcome_revoked_before_record_does_not_advance_plan(
        self,
    ) -> None:
        executor = DummyExecutor()
        engine = self.engine(executor)
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        def revoke_before_outcome(
            guild_id: int,
            action: str,
            result: dict[str, object],
        ) -> dict[str, bool]:
            self.manager.revoke(guild_id)
            return self.manager.record_outcome(
                guild_id,
                action,
                result,
            )

        engine.record_action_outcome = revoke_before_outcome
        result = await engine.execute_next_step(plan)

        self.assertEqual(executor.execute_count, 1)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["reason"],
            "authorization_changed_during_action",
        )
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertFalse(outcome["verified"])
        self.assertFalse(outcome["authorizationCurrent"])

    async def test_failed_outcome_with_expired_grant_stops_engine(
        self,
    ) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
            ttl_sec=60.0,
        )

        class ExpiringFailureExecutor(DummyExecutor):
            async def execute_step(
                inner_self,
                step: dict,
            ) -> dict:
                inner_self.execute_count += 1
                self.clock.value = 1061.0
                return {
                    "status": "failed",
                    "reason": "synthetic_failure",
                    "verified": False,
                }

        executor = ExpiringFailureExecutor()
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(executor.execute_count, 1)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["reason"],
            "authorization_changed_during_action",
        )
        self.assertEqual(result["reportedStatus"], "failed")
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.status, "authorization_required")
        self.assertEqual(engine.state.allowed_actions, [])
        self.assertEqual(self.manager.authorized_actions(7), [])
        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertFalse(outcome["verified"])
        self.assertFalse(outcome["authorizationCurrent"])

    async def test_each_execution_correlates_both_checks_and_outcome(
        self,
    ) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        engine = self.engine(DummyExecutor())
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]

        for _ in range(2):
            plan = AutonomyPlan(
                goal_kind="idle",
                summary="wait",
                steps=[{"domain": "assistant", "action": "idle"}],
            )
            result = await engine.execute_next_step(plan)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(plan.cursor, 1)

        rows = self.read_events()
        outcomes = [
            row for row in rows if row["event"] == "action_outcome"
        ]
        authorized = [
            row for row in rows if row["event"] == "action_authorized"
        ]
        run_ids = [row["actionRunId"] for row in outcomes]

        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(run_ids))
        self.assertEqual(len(set(run_ids)), 2)
        for action_run_id in run_ids:
            matching_checks = [
                row
                for row in authorized
                if row["actionRunId"] == action_run_id
            ]
            self.assertEqual(len(matching_checks), 2)

    async def test_execution_context_reaches_typed_executor(
        self,
    ) -> None:
        granted = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        captured: list[object] = []

        class ContextExecutor(DummyExecutor):
            async def execute_step(
                inner_self,
                step: dict,
                *,
                context: object = None,
            ) -> dict:
                captured.append(context)
                return await super().execute_step(step)

        engine = self.engine(ContextExecutor())
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(captured), 1)
        context = captured[0]
        self.assertEqual(context.guild_id, 7)
        self.assertEqual(context.action_key, "assistant:idle")
        self.assertTrue(context.action_run_id)
        self.assertEqual(
            context.authorization_grant_id,
            granted["grant"]["grantId"],
        )

        rows = self.read_events()
        correlated = [
            row
            for row in rows
            if row.get("actionRunId") == context.action_run_id
        ]
        self.assertEqual(
            [row["event"] for row in correlated],
            [
                "action_authorized",
                "action_authorized",
                "action_outcome",
            ],
        )

    async def test_skip_reason_without_evidence_does_not_advance(
        self,
    ) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["minecraft:equip_shield"],
        )
        executor = DummyExecutor(
            {
                "status": "ok",
                "reason": "shield_not_in_inventory",
                "verified": True,
            }
        )
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.allowed_actions = [
            "minecraft:equip_shield",
        ]
        plan = AutonomyPlan(
            goal_kind="survival",
            summary="equip shield",
            steps=[
                {
                    "domain": "minecraft",
                    "action": "equip_shield",
                }
            ],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(plan.cursor, 0)

    async def test_expired_grant_stops_future_action_execution(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
            ttl_sec=60.0,
        )
        executor = DummyExecutor()
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        self.clock.value = 1061.0
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[
                {
                    "domain": "assistant",
                    "action": "idle",
                }
            ],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "authorization_required")
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.allowed_actions, [])
        self.assertEqual(plan.cursor, 0)

    async def test_wrong_action_evidence_does_not_advance_plan(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        executor = DummyExecutor(
            {
                "status": "ok",
                "verified": True,
                "evidence_code": "discord_send_completed",
            }
        )
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "outcome_unverified")
        self.assertEqual(plan.cursor, 0)

    async def test_retry_budget_never_impersonates_effect_evidence(self) -> None:
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )
        executor = DummyExecutor()
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        engine._blocked_counts["assistant:idle"] = 2
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "retry_budget_exhausted")
        self.assertFalse(result["verified"])
        self.assertEqual(plan.cursor, 0)
        self.assertEqual(executor.execute_count, 0)

    async def test_executor_exception_is_content_free_failed_outcome(
        self,
    ) -> None:
        private = (
            "Bearer execute-secret "
            "https://internal.example/private "
            r"C:\Users\Admin\executor.py"
        )
        self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )

        class FailingExecutor(DummyExecutor):
            async def execute_step(
                self,
                step: dict,
            ) -> dict:
                self.execute_count += 1
                raise RuntimeError(private)

        engine = self.engine(FailingExecutor())
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)
        rendered = json.dumps(result)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["reason"],
            "autonomy_executor_execute_failed",
        )
        self.assertFalse(result["verified"])
        self.assertEqual(plan.cursor, 0)
        self.assertNotIn("execute-secret", rendered)
        self.assertNotIn("internal.example", rendered)
        self.assertNotIn("Users", rendered)
        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertEqual(outcome["outcomeStatus"], "failed")
        self.assertFalse(outcome["verified"])

    async def test_grant_replacement_during_action_blocks_plan_progress(
        self,
    ) -> None:
        first = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
        )["grant"]

        class ReplacingExecutor(DummyExecutor):
            async def execute_step(inner_self, step: dict) -> dict:
                inner_self.execute_count += 1
                self.manager.grant(
                    guild_id=7,
                    issuer_ref="discord_user:456",
                    source="discord_command",
                    scopes=["assistant:idle"],
                )
                return dict(inner_self.result)

        executor = ReplacingExecutor()
        engine = self.engine(executor)
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["reason"],
            "authorization_changed_during_action",
        )
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(engine.state.allowed_actions, [])
        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ]
        self.assertEqual(outcome[-1]["grantId"], first["grantId"])
        self.assertFalse(outcome[-1]["authorizationCurrent"])

    async def test_grant_expiry_during_action_blocks_plan_progress(
        self,
    ) -> None:
        first = self.manager.grant(
            guild_id=7,
            issuer_ref="discord_user:123",
            source="discord_command",
            scopes=["assistant:idle"],
            ttl_sec=60.0,
        )["grant"]

        class ExpiringExecutor(DummyExecutor):
            async def execute_step(inner_self, step: dict) -> dict:
                inner_self.execute_count += 1
                self.clock.value = 1061.0
                return dict(inner_self.result)

        engine = self.engine(ExpiringExecutor())
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="wait",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "authorization_required")
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        outcome = [
            row
            for row in self.read_events()
            if row["event"] == "action_outcome"
        ][-1]
        self.assertEqual(outcome["grantId"], first["grantId"])
        self.assertFalse(outcome["authorizationCurrent"])


if __name__ == "__main__":
    unittest.main()
