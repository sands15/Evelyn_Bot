from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path


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
    AutonomyAuthorizationManager,
)


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
            "evidence_code": "dummy_effect_verified",
        }
        self.connect_count = 0
        self.disconnect_count = 0

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
        return dict(self.result)


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
                "evidence_code": "idle_effect_verified",
                "raw": "private transcript C:\\secret",
            },
        )

        serialized = json.dumps(self.read_events())

        self.assertNotIn("private transcript", serialized)
        self.assertNotIn("C:\\\\secret", serialized)
        self.assertIn("discord_user:123", serialized)
        self.assertIn("idle_effect_verified", serialized)


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
            "evidence_code": "dummy_effect_verified",
        }
        verified = await engine.execute_next_step(plan)

        self.assertEqual(unverified["status"], "unverified")
        self.assertEqual(unverified["reason"], "outcome_unverified")
        self.assertEqual(plan.cursor, 1)
        self.assertEqual(verified["status"], "ok")

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


if __name__ == "__main__":
    unittest.main()
