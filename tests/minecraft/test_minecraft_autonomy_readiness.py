from __future__ import annotations

import copy
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

from evelyn_core.minecraft_autonomy_readiness import (  # noqa: E402
    validate_minecraft_autonomy_readiness,
)


def ready_status() -> dict[str, object]:
    return {
        "runtime": "mindcraft",
        "running": True,
        "telemetry_fresh": True,
        "minecraft_connected": True,
        "world_lease_authorized": True,
        "functional_readiness": {
            "schema": "minecraft_autonomy.readiness.v1",
            "state": "ready",
            "ready": True,
            "blockers": [],
            "dependencies": {
                "worldLeaseAuthorized": True,
                "runnerAlive": True,
                "telemetryFresh": True,
                "minecraftConnected": True,
                "taskContractReady": True,
                "autonomyActive": True,
            },
            "taskContract": {
                "schema": "mindcraft.task-contract.v1",
                "goalManagerMode": "gated",
                "autonomyState": "active",
                "commandGate": "evelyn_goal_manager",
                "effectVerification": "explicit_postcondition",
            },
            "contentFree": True,
        },
    }


class MinecraftAutonomyReadinessTests(unittest.TestCase):
    def test_exact_ready_contract_is_accepted(self) -> None:
        readiness, state = validate_minecraft_autonomy_readiness(
            ready_status()
        )

        self.assertEqual(state, "valid")
        self.assertIsNotNone(readiness)
        self.assertTrue(readiness["ready"])
        self.assertNotIn("taskContract", readiness)

    def test_missing_contract_is_distinct_from_invalid_contract(
        self,
    ) -> None:
        readiness, state = validate_minecraft_autonomy_readiness(
            {"runtime": "mindcraft"}
        )

        self.assertIsNone(readiness)
        self.assertEqual(state, "missing")

    def test_claimed_blockers_are_recomputed_exactly(self) -> None:
        payload = ready_status()
        payload["functional_readiness"]["blockers"] = [
            "arbitrary_text"
        ]

        readiness, state = validate_minecraft_autonomy_readiness(
            payload
        )

        self.assertIsNone(readiness)
        self.assertEqual(state, "invalid")

    def test_top_level_mindcraft_state_cannot_contradict_contract(
        self,
    ) -> None:
        payload = ready_status()
        payload["minecraft_connected"] = False

        readiness, state = validate_minecraft_autonomy_readiness(
            payload
        )

        self.assertIsNone(readiness)
        self.assertEqual(state, "invalid")

    def test_task_contract_requires_gate_and_postcondition(
        self,
    ) -> None:
        for field in ("commandGate", "effectVerification"):
            with self.subTest(field=field):
                payload = copy.deepcopy(ready_status())
                del payload["functional_readiness"][
                    "taskContract"
                ][field]

                readiness, state = (
                    validate_minecraft_autonomy_readiness(
                        payload
                    )
                )

                self.assertIsNone(readiness)
                self.assertEqual(state, "invalid")


if __name__ == "__main__":
    unittest.main()
