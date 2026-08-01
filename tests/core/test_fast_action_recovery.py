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
    FAST_ACTION_RECOVERY_HEAD_SCHEMA,
    FAST_ACTION_RECOVERY_LEGACY_SCHEMA,
    FAST_ACTION_RECOVERY_SCHEMA,
    FAST_ACTION_RECOVERY_V2_SCHEMA,
    FastActionRecoveryJournal,
)
from evelyn_core import fast_action_recovery as recovery_module  # noqa: E402
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
                "generation",
                "previousHash",
                "journalHash",
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
        self.assertEqual(payload["generation"], 2)
        self.assertRegex(
            payload["previousHash"],
            r"^[0-9a-f]{64}$",
        )
        self.assertRegex(
            payload["journalHash"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            set(payload["actions"][0]),
            {
                "actionId",
                "state",
                "startedAt",
                "expectedGeneration",
                "startedGeneration",
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
            payload["actions"][0][
                "startedGeneration"
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
        head = json.loads(
            journal.head_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(head),
            {
                "schema",
                "generation",
                "journalHash",
                "updatedAt",
                "contentFree",
            },
        )
        self.assertEqual(
            head["schema"],
            FAST_ACTION_RECOVERY_HEAD_SCHEMA,
        )
        self.assertEqual(
            head["generation"],
            payload["generation"],
        )
        self.assertEqual(
            head["journalHash"],
            payload["journalHash"],
        )
        self.assertTrue(
            journal.public_status()["rollbackProtected"]
        )

    def test_empty_chain_is_initialized_and_protected(
        self,
    ) -> None:
        journal = self.journal()

        status = journal.public_status()

        self.assertTrue(self.path.is_file())
        self.assertTrue(journal.head_path.is_file())
        self.assertEqual(status["generation"], 1)
        self.assertEqual(status["integrity"], "verified")
        self.assertEqual(status["headState"], "current")
        self.assertTrue(status["rollbackProtected"])

    def test_missing_journal_after_head_fails_closed(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin("fast-action-1")
        self.path.unlink()

        restored = self.journal()

        status = restored.public_status()
        self.assertEqual(status["state"], "corrupt")
        self.assertEqual(status["integrity"], "failed")
        self.assertEqual(status["headState"], "orphaned")
        self.assertFalse(status["rollbackProtected"])
        self.assertTrue(
            restored.recovery_decision(
                continuity_generation=0,
            )["noticeRequired"]
        )
        with self.assertRaises(RuntimeError):
            restored.begin("fast-action-2")

    def test_rolled_back_journal_is_rejected_by_current_head(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin("fast-action-1")
        old_journal = self.path.read_text(encoding="utf-8")
        journal.prepare_terminal(
            "fast-action-1",
            expected_generation=3,
        )
        self.path.write_text(old_journal, encoding="utf-8")

        restored = self.journal()

        status = restored.public_status()
        self.assertEqual(status["state"], "corrupt")
        self.assertEqual(status["headState"], "orphaned")
        self.assertFalse(status["rollbackProtected"])

    def test_missing_head_after_chain_advance_fails_closed(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin("fast-action-1")
        journal.head_path.unlink()

        restored = self.journal()

        status = restored.public_status()
        self.assertEqual(status["state"], "corrupt")
        self.assertEqual(status["integrity"], "failed")
        self.assertEqual(status["headState"], "missing")
        self.assertFalse(status["rollbackProtected"])

    def test_self_hash_mismatch_fails_closed(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin("fast-action-1")
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        payload["actions"][0]["startedAt"] = 2000.0
        self.path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        restored = self.journal()

        status = restored.public_status()
        self.assertEqual(status["state"], "corrupt")
        self.assertEqual(status["integrity"], "failed")
        self.assertFalse(status["rollbackProtected"])

    def test_one_ahead_journal_recovers_after_head_write_crash(
        self,
    ) -> None:
        journal = self.journal()
        failed = False
        real_write = recovery_module.atomic_json_write

        def write(path, payload, **kwargs):
            nonlocal failed
            if Path(path) == journal.head_path and not failed:
                failed = True
                raise OSError("private head path")
            return real_write(path, payload, **kwargs)

        with patch.object(
            recovery_module,
            "atomic_json_write",
            side_effect=write,
        ):
            with self.assertRaises(OSError):
                journal.begin("fast-action-1")

        self.assertEqual(
            journal.public_status()["state"],
            "error",
        )
        restored = self.journal()
        status = restored.public_status()
        self.assertEqual(status["state"], "pending")
        self.assertEqual(status["integrity"], "verified")
        self.assertEqual(status["headState"], "current")
        self.assertTrue(status["rollbackProtected"])
        self.assertTrue(
            restored.recovery_decision(
                continuity_generation=0,
            )["noticeRequired"]
        )

    def test_v1_journal_is_anchored_then_migrated(
        self,
    ) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "schema": FAST_ACTION_RECOVERY_LEGACY_SCHEMA,
                    "updatedAt": 1000.0,
                    "actions": [],
                    "lastRecoveryAt": 0.0,
                    "lastRecoveryCount": 0,
                    "lastErrorCode": "",
                    "policy": {
                        "contentFree": True,
                        "rawText": False,
                        "automaticRetry": False,
                        "maxActions": 40,
                    },
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        journal = self.journal()

        anchored = journal.public_status()
        self.assertEqual(anchored["generation"], 0)
        self.assertEqual(
            anchored["integrity"],
            "legacy_anchored",
        )
        self.assertTrue(anchored["rollbackProtected"])
        journal.begin("fast-action-1")
        migrated = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            migrated["schema"],
            FAST_ACTION_RECOVERY_SCHEMA,
        )
        self.assertEqual(migrated["generation"], 1)

    def test_v2_pending_action_requires_new_notice_then_migrates(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin(
            "fast-action-1",
            continuity_generation=7,
        )
        payload = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        payload["schema"] = FAST_ACTION_RECOVERY_V2_SCHEMA
        for entry in payload["actions"]:
            entry.pop("startedGeneration")
        payload["journalHash"] = recovery_module._journal_hash(
            payload
        )
        self.path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        head = json.loads(
            journal.head_path.read_text(encoding="utf-8")
        )
        head["journalHash"] = payload["journalHash"]
        journal.head_path.write_text(
            json.dumps(
                head,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        restored = self.journal()

        self.assertEqual(
            restored.public_status()["state"],
            "pending",
        )
        self.assertFalse(
            restored.public_status()[
                "noticeCorrelationReady"
            ]
        )
        self.assertFalse(
            restored.restored_notice_matches(
                continuity_generation=8,
            )
        )
        restored.acknowledge_recovery(
            recovered_count=1,
            error_code="fast_action_recovery_interrupted",
        )
        migrated = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            migrated["schema"],
            FAST_ACTION_RECOVERY_SCHEMA,
        )

    def test_restored_notice_must_postdate_action_start(
        self,
    ) -> None:
        journal = self.journal()
        journal.begin(
            "fast-action-1",
            continuity_generation=4,
        )

        self.assertFalse(
            journal.restored_notice_matches(
                continuity_generation=4,
            )
        )
        self.assertTrue(
            journal.restored_notice_matches(
                continuity_generation=5,
            )
        )

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
        # Generation 1 is the durable, content-free bootstrap head.
        self.assertEqual(generation, 3)
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
            expected_generation=3,
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
            expected_generation=3,
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
            3,
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
