from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.upstream_voyager_runner import (  # noqa: E402
    RunnerStatus,
    _copy_task_status_from_voyager,
    _derive_last_success,
)


class UpstreamVoyagerRunnerStatusTests(unittest.TestCase):
    def test_derive_last_success_prefers_explicit_top_level_value(self) -> None:
        self.assertFalse(
            _derive_last_success(
                last_success=False,
                last_task_result={"success": True},
                current_task_bookkeeping={"success": True},
                last_task_bookkeeping=None,
                last_critic_result={"success": True},
            )
        )

    def test_derive_last_success_from_task_result_when_top_level_missing(self) -> None:
        self.assertTrue(
            _derive_last_success(
                last_success=None,
                last_task_result={"success": True},
                current_task_bookkeeping=None,
                last_task_bookkeeping=None,
                last_critic_result=None,
            )
        )

    def test_derive_last_success_from_bookkeeping_failure(self) -> None:
        self.assertFalse(
            _derive_last_success(
                last_success=None,
                last_task_result=None,
                current_task_bookkeeping={"success": False, "status": "failed"},
                last_task_bookkeeping={"success": True},
                last_critic_result=None,
            )
        )

    def test_copy_task_status_promotes_structured_success_to_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            status = RunnerStatus(Path(tmpdir) / "status.json", "learn", "goal")
            voyager = SimpleNamespace(
                last_rollout_info={"iteration": 3},
                last_task_result={"task": "Craft torches", "success": True},
                last_completion_reason="critic_success",
                last_success=None,
                last_search_metrics={"success": True},
                current_speculative_next_task={"task": "Mine coal"},
                last_speculative_decision={"accepted": True},
                last_inventory_plan={"target": "torch"},
                curriculum_agent=SimpleNamespace(
                    active_plan_state={"node": 1},
                    last_task_contract_decision={"contract": "inventory"},
                ),
                last_task_contract_decision=None,
                current_task_bookkeeping={"status": "completed"},
                last_task_bookkeeping=None,
                last_world_effect_verification={"outcome": "success"},
                last_critic_result={"success": True},
                last_recovery_boundary=None,
                execution_session={"id": "session-1"},
                reset_audit_log=[{"reason": "test"}],
            )

            _copy_task_status_from_voyager(status, voyager)

            self.assertTrue(status.last_success)
            self.assertEqual(status.last_task_result["task"], "Craft torches")
            self.assertEqual(status.last_completion_reason, "critic_success")
            self.assertEqual(status.last_task_contract_decision["contract"], "inventory")
            self.assertEqual(status.reset_audit_log, [{"reason": "test"}])


if __name__ == "__main__":
    unittest.main()
