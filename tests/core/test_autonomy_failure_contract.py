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

from evelyn_core.autonomy import AutonomyEngine  # noqa: E402
from evelyn_core.autonomy_failure_contract import (  # noqa: E402
    AUTONOMY_CYCLE_FAILED,
    AUTONOMY_EXECUTOR_EXECUTE_FAILED,
    AUTONOMY_EXECUTOR_OBSERVE_FAILED,
    AUTONOMY_FAILURE_SCHEMA,
    sanitize_autonomy_observation,
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
            "executor_errors": {"minecraft": PRIVATE}
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

    def test_load_state_replaces_legacy_raw_failure(self) -> None:
        engine = AutonomyEngine(
            guild_id=7,
            executor=DummyExecutor(),
        )
        saved = {
            "autonomy_runtime": {
                "last_error": PRIVATE,
                "last_observation": {
                    "executor_errors": {"minecraft": PRIVATE}
                },
            }
        }

        with patch(
            "evelyn_core.autonomy.read_json_file",
            return_value=saved,
        ):
            engine.load_persisted_state()

        serialized = json.dumps(
            {
                "last_error": engine.state.last_error,
                "last_observation": engine.state.last_observation,
            }
        )
        self.assertNotIn("autonomy-secret", serialized)
        self.assertEqual(
            engine.state.last_error,
            AUTONOMY_CYCLE_FAILED,
        )

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
