from __future__ import annotations

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

from evelyn_core.fast_action_recovery import (  # noqa: E402
    FAST_ACTION_RECOVERY_SCHEMA,
    FastActionRecoveryJournal,
)
from evelyn_core.fast_control_continuity import (  # noqa: E402
    FastControlContinuityOwner,
)


class Clock:
    def __init__(self) -> None:
        self.wall = 1000.0
        self.mono = 200.0

    def wall_time(self) -> float:
        return self.wall

    def monotonic(self) -> float:
        return self.mono


class FastActionRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = (
            self.root
            / "fast_control_actions"
            / "recovery.json"
        )
        self.clock = Clock()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def journal(self) -> FastActionRecoveryJournal:
        return FastActionRecoveryJournal(
            path=self.path,
            enabled=True,
            wall_time=self.clock.wall_time,
        )

    def owner(self) -> FastControlContinuityOwner:
        return FastControlContinuityOwner(
            artifacts_root=self.root,
            enabled=True,
            wall_time=self.clock.wall_time,
            monotonic=self.clock.monotonic,
            log=lambda *_args, **_kwargs: None,
        )

    def test_running_marker_is_exact_and_content_free(
        self,
    ) -> None:
        journal = self.journal()

        journal.begin("fast-action-1")

        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(payload),
            {
                "schema",
                "updatedAt",
                "actions",
                "lastRecoveryAt",
                "lastRecoveryCount",
                "lastErrorCode",
                "policy",
            },
        )
        self.assertEqual(
            payload["schema"],
            FAST_ACTION_RECOVERY_SCHEMA,
        )
        self.assertEqual(
            set(payload["actions"][0]),
            {
                "actionId",
                "state",
                "startedAt",
                "expectedGeneration",
            },
        )
        self.assertEqual(
            payload["actions"][0]["state"],
            "running",
        )
        self.assertEqual(
            payload["actions"][0][
                "expectedGeneration"
            ],
            0,
        )
        self.assertEqual(
            payload["policy"],
            {
                "contentFree": True,
                "rawText": False,
                "automaticRetry": False,
                "maxActions": 40,
            },
        )
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
        )
        self.assertNotIn("사용자 질문", serialized)
        self.assertNotIn("최종 답변", serialized)

    def test_generation_proves_terminal_delivery_after_restart(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin("fast-action-1")
        journal.prepare_terminal(
            "fast-action-1",
            expected_generation=4,
        )

        restored = self.journal()

        before = restored.recovery_decision(
            continuity_generation=3,
        )
        delivered = restored.recovery_decision(
            continuity_generation=4,
        )
        self.assertTrue(before["noticeRequired"])
        self.assertEqual(
            before["reasonCode"],
            "fast_action_recovery_interrupted",
        )
        self.assertFalse(delivered["noticeRequired"])
        self.assertEqual(
            delivered["state"],
            "delivery_verified",
        )
        not_ready = restored.recovery_decision(
            continuity_generation=4,
            continuity_ready=False,
        )
        self.assertTrue(not_ready["noticeRequired"])
        restored.acknowledge_recovery(
            recovered_count=1,
        )
        self.assertEqual(
            restored.public_status()["state"],
            "recovered",
        )
        self.assertEqual(
            restored.public_status()["pendingCount"],
            0,
        )

    def test_owner_lock_binds_expected_generation_to_followup(
        self,
    ) -> None:
        owner = self.owner()
        owner.record_completed_turn("질문", "시작 답변")
        journal = self.journal()
        journal.begin("fast-action-1")

        status = owner.record_assistant_followup(
            "최종 답변",
            before_commit=lambda generation: (
                journal.prepare_terminal(
                    "fast-action-1",
                    expected_generation=generation,
                )
            ),
        )

        generation = status["checkpointGeneration"]
        self.assertEqual(generation, 2)
        restored_owner = self.owner()
        restored_journal = self.journal()
        self.assertEqual(
            restored_owner.status()["generation"],
            generation,
        )
        self.assertEqual(
            restored_journal.recovery_decision(
                continuity_generation=generation,
            )["state"],
            "delivery_verified",
        )

    def test_crash_before_followup_commit_requires_notice(
        self,
    ) -> None:
        owner = self.owner()
        owner.record_completed_turn("질문", "시작 답변")
        journal = self.journal()
        journal.begin("fast-action-1")
        journal.prepare_terminal(
            "fast-action-1",
            expected_generation=2,
        )

        restored_owner = self.owner()
        restored_journal = self.journal()
        decision = restored_journal.recovery_decision(
            continuity_generation=(
                restored_owner.status()["generation"]
            ),
        )

        self.assertEqual(
            restored_owner.status()["generation"],
            1,
        )
        self.assertTrue(decision["noticeRequired"])
        self.assertEqual(
            decision["pendingCount"],
            1,
        )

    def test_failed_action_generation_cannot_be_reused(
        self,
    ) -> None:
        owner = self.owner()
        owner.record_completed_turn("질문", "시작 답변")
        journal = self.journal()
        journal.begin("fast-action-1")
        journal.prepare_terminal(
            "fast-action-1",
            expected_generation=2,
        )
        journal.mark_interrupted("fast-action-1")

        owner.record_completed_turn(
            "다른 질문",
            "다른 답변",
        )
        restored_owner = self.owner()
        restored_journal = self.journal()
        decision = restored_journal.recovery_decision(
            continuity_generation=(
                restored_owner.status()["generation"]
            ),
        )

        self.assertEqual(
            restored_owner.status()["generation"],
            2,
        )
        self.assertTrue(decision["noticeRequired"])
        self.assertEqual(
            decision["reasonCode"],
            "fast_action_recovery_interrupted",
        )

    def test_corrupt_journal_fails_closed_until_acknowledged(
        self,
    ) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            '{"schema":"bad","privateMessage":"원문"}',
            encoding="utf-8",
        )

        journal = self.journal()

        self.assertEqual(
            journal.public_status()["state"],
            "corrupt",
        )
        self.assertTrue(
            journal.recovery_decision(
                continuity_generation=0,
            )["noticeRequired"]
        )
        with self.assertRaises(RuntimeError):
            journal.begin("fast-action-1")
        journal.acknowledge_recovery(
            recovered_count=1,
            error_code=(
                "fast_action_recovery_journal_corrupt"
            ),
        )
        serialized = self.path.read_text(encoding="utf-8")
        self.assertNotIn("privateMessage", serialized)
        self.assertNotIn("원문", serialized)

    def test_write_failure_does_not_leave_in_memory_action(
        self,
    ) -> None:
        journal = self.journal()

        with patch(
            "evelyn_core.fast_action_recovery.atomic_json_write",
            side_effect=OSError("disk path private"),
        ):
            with self.assertRaises(OSError):
                journal.begin("fast-action-1")

        status = journal.public_status()
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["pendingCount"], 0)
        self.assertFalse(
            journal.continuity_commit_allowed()
        )
        self.assertEqual(
            status["lastErrorCode"],
            "fast_action_recovery_write_failed",
        )
        self.assertNotIn(
            "disk path private",
            json.dumps(status),
        )


if __name__ == "__main__":
    unittest.main()
