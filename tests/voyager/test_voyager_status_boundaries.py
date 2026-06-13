from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import voyager_service  # noqa: E402
from evelyn_core.voyager_service import _death_recovery_boundary, _task_recovery_boundary  # noqa: E402


class VoyagerStatusBoundaryTests(unittest.TestCase):
    def test_explicit_success_marks_task_completed(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason="critic_passed",
            last_success=True,
            last_task_result={"task": "Craft 4 torches"},
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result={"success": True},
        )

        self.assertTrue(boundary["healthy"])
        self.assertEqual(boundary["domain"], "task_completed")

    def test_completion_reason_without_success_is_unverified(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason="critic_passed",
            last_success=None,
            last_task_result=None,
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result={"success": True},
        )

        self.assertFalse(boundary["healthy"])
        self.assertEqual(boundary["domain"], "task_unverified")
        self.assertIn("critic_passed", boundary["reason"])

    def test_task_result_without_success_is_not_treated_as_healthy(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason=None,
            last_success=None,
            last_task_result={"task": "Mine 8 coal", "success": True},
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result=None,
        )

        self.assertFalse(boundary["healthy"])
        self.assertEqual(boundary["domain"], "task_result_unverified")

    def test_verified_bookkeeping_without_success_is_unverified(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason=None,
            last_success=None,
            last_task_result=None,
            current_task_bookkeeping={"status": "effect_verified"},
            last_task_bookkeeping=None,
            last_critic_result=None,
        )

        self.assertFalse(boundary["healthy"])
        self.assertEqual(boundary["domain"], "task_bookkeeping_unverified")

    def test_existing_recovery_boundary_is_preserved(self) -> None:
        existing = {"scope": "task", "domain": "recovery_required", "healthy": False}

        boundary = _task_recovery_boundary(
            last_recovery_boundary=existing,
            last_completion_reason=None,
            last_success=None,
            last_task_result={"task": "ignored"},
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result=None,
        )

        self.assertIs(boundary, existing)

    def test_existing_recovery_boundary_is_preserved_and_enriched(self) -> None:
        existing = {
            "scope": "task",
            "domain": "task_local_execution",
            "reason_code": "goal_reset_loop",
            "recommended_action": "replan_task",
            "healthy": False,
        }

        boundary = _task_recovery_boundary(
            last_recovery_boundary=existing,
            last_completion_reason=None,
            last_success=None,
            last_task_result=None,
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result=None,
        )

        self.assertIs(boundary, existing)
        self.assertEqual(boundary["domain"], "task_local_execution")
        self.assertEqual(boundary["subdomain"], "goal_reset")
        self.assertEqual(boundary["recommended_action"], "replan_task")
        self.assertGreater(len(boundary["next_steps"]), 0)

    def test_pathfinding_failure_exposes_recovery_guidance(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason="move_distance_unmet",
            last_success=False,
            last_task_result=None,
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result=None,
        )

        self.assertEqual(boundary["domain"], "task_failed")
        self.assertEqual(boundary["subdomain"], "pathfinding")
        self.assertEqual(boundary["recommended_action"], "replan_route")
        self.assertGreater(len(boundary["next_steps"]), 0)

    def test_mining_failure_exposes_recovery_guidance(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason="mine_path_blocked",
            last_success=False,
            last_task_result={"task": "Mine 8 coal"},
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result=None,
        )

        self.assertEqual(boundary["domain"], "task_failed")
        self.assertEqual(boundary["subdomain"], "mining")
        self.assertEqual(boundary["recommended_action"], "verify_target_block_and_tool")
        self.assertGreater(len(boundary["next_steps"]), 0)

    def test_recovery_failure_exposes_stabilization_guidance(self) -> None:
        boundary = _task_recovery_boundary(
            last_recovery_boundary=None,
            last_completion_reason="low_health_recovery_failed",
            last_success=False,
            last_task_result=None,
            current_task_bookkeeping=None,
            last_task_bookkeeping=None,
            last_critic_result={"reason": "health remained low"},
        )

        self.assertEqual(boundary["domain"], "task_failed")
        self.assertEqual(boundary["subdomain"], "recovery")
        self.assertEqual(boundary["recommended_action"], "stabilize_before_retry")
        self.assertGreater(len(boundary["next_steps"]), 0)

    def test_recovery_state_includes_task_guidance_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            status_path.write_text(
                json.dumps(
                    {
                        "updated_at": time.time(),
                        "mode": "learn",
                        "current_task": "Move to oak tree",
                        "last_completion_reason": "move_distance_unmet",
                        "last_success": False,
                        "observation": {
                            "connection_state": "connected",
                            "position": {"x": 1, "y": 64, "z": 1},
                        },
                    }
                ),
                encoding="utf-8",
            )
            bridge = voyager_service.UpstreamDirectBridge()
            bridge.runner_mode = "learn"
            bridge._process_alive = lambda: True  # type: ignore[method-assign]
            bridge._collect_runtime_probes = lambda *, running: {  # type: ignore[method-assign]
                "live_telemetry": {},
                "bridge_http_reachable": True,
                "bridge_telemetry_alive": True,
                "minecraft_tcp_reachable": True,
            }

            with patch.object(voyager_service, "RUNNER_STATUS_PATH", status_path):
                payload = bridge.build_status()

        recovery_state = payload["recovery_state"]
        self.assertEqual(recovery_state["scope"], "task")
        self.assertEqual(recovery_state["domain"], "task_failed")
        self.assertEqual(recovery_state["subdomain"], "pathfinding")
        self.assertEqual(recovery_state["recommended_action"], "replan_route")
        self.assertGreater(len(recovery_state["next_steps"]), 0)
        self.assertEqual(recovery_state["task_boundary"]["subdomain"], "pathfinding")

    def test_recent_death_event_requires_recovery_even_without_phase_signal(self) -> None:
        boundary = _death_recovery_boundary(
            {
                "recorded_at": "2026-06-13T12:00:00Z",
                "death_message": "Evelyn was slain by Zombie",
            },
            now_ts=1781352060.0,
        )

        self.assertFalse(boundary["healthy"])
        self.assertEqual(boundary["domain"], "death_recovery_required")
        self.assertIn("Zombie", boundary["reason"])

    def test_stale_death_event_does_not_keep_recovery_active(self) -> None:
        boundary = _death_recovery_boundary(
            {
                "recorded_at": "2026-06-13T12:00:00Z",
                "death_message": "Evelyn fell from a high place",
            },
            now_ts=1781352600.0,
        )

        self.assertTrue(boundary["healthy"])
        self.assertEqual(boundary["domain"], "healthy")
        self.assertEqual(boundary["reason"], "death event is stale")


if __name__ == "__main__":
    unittest.main()
