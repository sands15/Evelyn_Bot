from __future__ import annotations

import json
import sys
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

from evelyn_core.autonomy import (  # noqa: E402
    AutonomyEngine,
    AutonomyGoal,
    AutonomyPlan,
)
from evelyn_core.autonomy_failure_contract import (  # noqa: E402
    AUTONOMY_CYCLE_FAILED,
    AUTONOMY_EXECUTOR_EXECUTE_FAILED,
    AUTONOMY_EXECUTOR_OBSERVE_FAILED,
    AUTONOMY_FAILURE_SCHEMA,
    sanitize_autonomy_observation,
    sanitize_autonomy_step_result,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)


PRIVATE = (
    "Bearer autonomy-secret "
    "https://internal.example/private "
    r"C:\Users\Admin\executor.py"
)


class DummyExecutor:
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def observe(self) -> dict:
        return {"environment": "assistant"}

    async def execute_step(self, step: dict) -> dict:
        return {"status": "blocked", "reason": "unused"}


class AutonomyFailureContractTests(
    unittest.IsolatedAsyncioTestCase
):
    def test_legacy_executor_error_is_content_free(self) -> None:
        observation = sanitize_autonomy_observation(
            {
                "executor_errors": {
                    "minecraft": PRIVATE,
                    PRIVATE: {"code": PRIVATE},
                },
                "active_environment": "assistant",
            }
        )

        serialized = json.dumps(observation)
        self.assertNotIn("autonomy-secret", serialized)
        self.assertNotIn("internal.example", serialized)
        self.assertNotIn("Users", serialized)
        self.assertEqual(
            observation["executor_errors"]["minecraft"]["schema"],
            AUTONOMY_FAILURE_SCHEMA,
        )
        self.assertEqual(
            observation["executor_errors"]["minecraft"]["code"],
            AUTONOMY_EXECUTOR_OBSERVE_FAILED,
        )
        self.assertIn("unknown", observation["executor_errors"])

    def test_persist_state_sanitizes_failure_fields_at_writer(self) -> None:
        engine = AutonomyEngine(
            guild_id=7,
            executor=DummyExecutor(),
        )
        engine.state.last_error = PRIVATE
        engine.state.last_observation = {
            "executor_errors": {"minecraft": PRIVATE},
            "latest_user_text": PRIVATE,
            "recent_visible": [PRIVATE],
            "recent_context_items": 2,
            "unresolved_items": 1,
            "search_pending": True,
            "cognitive_refresh_needed": True,
            "user_unresolved_items": 1,
        }
        engine.state.last_step_result = {
            "status": "ok",
            "reason": "summary_ready",
            "summary": PRIVATE,
            "step": {"text": PRIVATE},
        }
        engine.state.last_router_refresh_result = {
            "status": "ok",
            "reason": "router_refreshed",
            "text": PRIVATE,
        }

        with (
            patch(
                "evelyn_core.autonomy.read_json_file",
                return_value={},
            ),
            patch(
                "evelyn_core.autonomy.write_json_file"
            ) as write_json,
        ):
            engine.persist_state()

        persisted = write_json.call_args.args[1][
            "autonomy_runtime"
        ]
        serialized = json.dumps(persisted)
        self.assertNotIn("autonomy-secret", serialized)
        self.assertNotIn("internal.example", serialized)
        self.assertNotIn("Users", serialized)
        self.assertEqual(
            persisted["last_error"],
            AUTONOMY_CYCLE_FAILED,
        )
        self.assertEqual(
            persisted["last_observation"]["executor_errors"][
                "minecraft"
            ]["code"],
            AUTONOMY_EXECUTOR_OBSERVE_FAILED,
        )
        self.assertEqual(
            persisted["last_step_result"],
            {"status": "ok", "reason": "summary_ready"},
        )
        self.assertEqual(
            persisted["last_router_refresh_result"],
            {"status": "ok", "reason": "router_refreshed"},
        )
        self.assertEqual(
            persisted["last_observation"]["user_unresolved_items"],
            1,
        )
        for dropped in (
            "latest_user_text",
            "recent_visible",
            "recent_context_items",
            "unresolved_items",
            "search_pending",
            "cognitive_refresh_needed",
        ):
            self.assertNotIn(
                dropped,
                persisted["last_observation"],
            )

    def test_load_state_replaces_legacy_raw_failure(self) -> None:
        engine = AutonomyEngine(
            guild_id=7,
            executor=DummyExecutor(),
        )
        saved = {
            "autonomy_runtime": {
                "last_error": PRIVATE,
                "safety_mode": PRIVATE,
                "failure_count": "bad",
                "updated_at": {"private": PRIVATE},
                "current_goal": {
                    "kind": "maintain",
                    "summary": PRIVATE,
                    "priority": 0.9,
                },
                "current_plan": {
                    "goal_kind": "maintain",
                    "summary": PRIVATE,
                    "steps": [{"text": PRIVATE}],
                },
                "last_observation": {
                    "executor_errors": {"minecraft": PRIVATE},
                    "latest_user_text": PRIVATE,
                    "recent_visible": [PRIVATE],
                },
                "last_step_result": {
                    "status": "ok",
                    "reason": "summary_ready",
                    "summary": PRIVATE,
                },
                "last_router_refresh_result": {
                    "status": "ok",
                    "reason": "router_refreshed",
                    "text": PRIVATE,
                },
            }
        }

        with (
            patch(
                "evelyn_core.autonomy.read_json_file",
                return_value=saved,
            ),
            patch(
                "evelyn_core.autonomy.write_json_file"
            ) as write_json,
        ):
            engine.load_persisted_state()

        serialized = json.dumps(
            {
                "last_error": engine.state.last_error,
                "last_observation": engine.state.last_observation,
                "last_step_result": engine.state.last_step_result,
                "last_router_refresh_result": (
                    engine.state.last_router_refresh_result
                ),
                "current_goal": engine.state.current_goal,
                "current_plan": engine.state.current_plan,
            }
        )
        self.assertNotIn("autonomy-secret", serialized)
        self.assertEqual(
            engine.state.last_error,
            AUTONOMY_CYCLE_FAILED,
        )
        self.assertIsNone(engine.state.current_goal)
        self.assertIsNone(engine.state.current_plan)
        self.assertEqual(engine.state.last_observation, {})
        self.assertEqual(engine.state.last_step_result, {})
        self.assertEqual(engine.state.last_router_refresh_result, {})
        self.assertEqual(engine.state.drive_state, {})
        self.assertEqual(engine.state.safety_mode, "constrained")
        self.assertEqual(engine.state.failure_count, 0)
        self.assertNotIn(
            "autonomy-secret",
            json.dumps(write_json.call_args.args[1]),
        )

    def test_load_missing_or_malformed_state_discards_live_caches(self) -> None:
        for saved in ({}, {"autonomy_runtime": "bad"}):
            with self.subTest(saved=saved):
                engine = AutonomyEngine(
                    guild_id=7,
                    executor=DummyExecutor(),
                )
                engine.state.last_observation = {"latest_user_text": PRIVATE}
                engine.state.current_goal = AutonomyGoal(
                    kind="maintain",
                    summary=PRIVATE,
                    priority=0.9,
                )
                engine.state.current_plan = AutonomyPlan(
                    goal_kind="maintain",
                    summary=PRIVATE,
                    steps=[{"text": PRIVATE}],
                )
                engine.state.last_step_result = {"summary": PRIVATE}
                engine.state.last_router_refresh_result = {"text": PRIVATE}
                engine.state.drive_state = {"private": PRIVATE}
                engine._blocked_counts[PRIVATE] = 1

                with (
                    patch(
                        "evelyn_core.autonomy.read_json_file",
                        return_value=saved,
                    ),
                    patch(
                        "evelyn_core.autonomy.write_json_file"
                    ) as write_json,
                ):
                    engine.load_persisted_state()

                self.assertEqual(engine.state.last_observation, {})
                self.assertIsNone(engine.state.current_goal)
                self.assertIsNone(engine.state.current_plan)
                self.assertEqual(engine.state.last_step_result, {})
                self.assertEqual(engine.state.last_router_refresh_result, {})
                self.assertEqual(engine.state.drive_state, {})
                self.assertEqual(engine._blocked_counts, {})
                self.assertNotIn(
                    "autonomy-secret",
                    json.dumps(write_json.call_args.args[1]),
                )

    def test_step_result_projection_is_content_free(self) -> None:
        projected = sanitize_autonomy_step_result(
            {
                "status": "ok",
                "reason": "summary_ready",
                "summary": PRIVATE,
                "text": PRIVATE,
                "step": {"text": PRIVATE},
                "verified": True,
                "count": 2,
            }
        )

        self.assertEqual(
            projected,
            {
                "status": "ok",
                "reason": "summary_ready",
                "verified": True,
                "count": 2,
            },
        )

    def test_step_result_projection_rejects_secret_like_codes(self) -> None:
        private_token = "sk_live_ABC123"

        projected = sanitize_autonomy_step_result(
            {
                "status": "PASSWORD123",
                "reason": private_token,
                "evidence_code": "SecretToken",
                "verified": True,
            }
        )

        self.assertEqual(projected, {"verified": True})
        self.assertNotIn(private_token, json.dumps(projected))

        minecraft = sanitize_autonomy_step_result(
            {
                "status": "ok",
                "evidence_code": "minecraft_gather_logs_completed",
                "verified": True,
            }
        )
        self.assertEqual(
            minecraft["evidence_code"],
            "minecraft_gather_logs_completed",
        )

    def test_replan_summary_uses_only_safe_reason_code(self) -> None:
        private_token = "sk_live_ABC123"
        engine = AutonomyEngine(
            guild_id=7,
            executor=DummyExecutor(),
        )
        goal = AutonomyGoal(
            kind="idle",
            summary="대기한다",
            priority=0.1,
            metadata={"domain": "assistant"},
        )

        plan = engine.replan_goal(
            goal,
            {"environment": "assistant"},
            {"status": "failed", "reason": private_token},
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.summary, "대기한다 (replan 재계획)")
        self.assertNotIn(private_token, plan.summary)

    async def test_memory_integrity_failure_is_not_downgraded_to_executor_error(
        self,
    ) -> None:
        class IntegrityFailingExecutor(DummyExecutor):
            async def execute_step(self, step: dict) -> dict:
                raise MemoryDeletionJournalIntegrityError()

        engine = AutonomyEngine(
            guild_id=7,
            executor=IntegrityFailingExecutor(),
            authorize_action=lambda _guild_id, _action: {
                "allowed": True,
                "code": "authorized",
                "grantId": "grant-safe",
            },
        )
        engine.state.allowed_actions = ["assistant:idle"]
        plan = AutonomyPlan(
            goal_kind="idle",
            summary="idle",
            steps=[{"domain": "assistant", "action": "idle"}],
        )

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            await engine.execute_next_step(plan)

    async def test_cycle_exception_notifies_only_fixed_code(
        self,
    ) -> None:
        notifications: list[str] = []

        async def notify(text: str) -> None:
            notifications.append(text)

        engine = AutonomyEngine(
            guild_id=7,
            executor=DummyExecutor(),
            notify=notify,
            poll_interval_sec=1.0,
        )
        engine.state.enabled = True

        async def failing_cycle():
            engine.state.enabled = False
            raise RuntimeError(PRIVATE)

        engine.run_cycle = failing_cycle
        with (
            patch.object(engine, "persist_state"),
            patch(
                "evelyn_core.autonomy.asyncio.sleep",
                return_value=None,
            ),
        ):
            await engine._run_loop()

        self.assertEqual(
            engine.state.last_error,
            AUTONOMY_CYCLE_FAILED,
        )
        self.assertEqual(
            notifications,
            [f"[자율봇] 오류: {AUTONOMY_CYCLE_FAILED}"],
        )
        self.assertNotIn(PRIVATE, json.dumps(notifications))

    async def test_action_exception_becomes_failed_cycle_state(
        self,
    ) -> None:
        notifications: list[str] = []

        class FailingExecutor(DummyExecutor):
            async def observe(self) -> dict:
                return {
                    "environment": "assistant",
                    "quiet_hours": True,
                }

            async def execute_step(self, step: dict) -> dict:
                raise RuntimeError(PRIVATE)

        async def notify(text: str) -> None:
            notifications.append(text)

        engine = AutonomyEngine(
            guild_id=7,
            executor=FailingExecutor(),
            notify=notify,
            authorize_action=lambda _guild_id, action: {
                "allowed": action == "assistant:idle",
                "code": "authorized",
                "grantId": "grant-safe",
            },
        )
        engine.state.enabled = True
        engine.state.allowed_actions = ["assistant:idle"]

        with patch.object(engine, "persist_state"):
            cycle = await engine.run_cycle()

        serialized = json.dumps(
            {
                "result": cycle.step_result,
                "error": engine.state.last_error,
                "notifications": notifications,
            }
        )
        self.assertEqual(
            cycle.step_result["reason"],
            AUTONOMY_EXECUTOR_EXECUTE_FAILED,
        )
        self.assertEqual(engine.state.status, "error")
        self.assertEqual(engine.state.failure_count, 1)
        self.assertEqual(
            engine.state.last_error,
            AUTONOMY_EXECUTOR_EXECUTE_FAILED,
        )
        self.assertNotIn("autonomy-secret", serialized)
        self.assertNotIn("internal.example", serialized)
        self.assertNotIn("Users", serialized)


if __name__ == "__main__":
    unittest.main()
