from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voyager_service import _task_recovery_boundary  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
