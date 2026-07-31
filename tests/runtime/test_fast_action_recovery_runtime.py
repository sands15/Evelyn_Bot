from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402


class FastActionRecoveryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        fast_api.clear_background_action_handlers()

    def tearDown(self) -> None:
        fast_api.clear_background_action_handlers()

    @staticmethod
    def owner(
        root: Path,
    ) -> fast_api.FastControlContinuityOwner:
        return fast_api.FastControlContinuityOwner(
            artifacts_root=root,
            enabled=True,
            log=lambda *_args, **_kwargs: None,
        )

    @staticmethod
    def journal(
        root: Path,
    ) -> fast_api.FastActionRecoveryJournal:
        return fast_api.FastActionRecoveryJournal(
            path=(
                root
                / "fast_control_actions"
                / "recovery.json"
            ),
            enabled=True,
        )

    def test_recovery_marker_failure_blocks_background_start(
        self,
    ) -> None:
        class FailedJournal:
            def begin(self, _task_id):
                raise OSError("private journal path")

        fast_api.register_background_action_handler(
            kind="long_check",
            matcher=lambda text: text == "긴 작업",
            runner=AsyncMock(return_value="완료"),
            start_reply="확인해 볼게.",
        )

        with patch.object(
            fast_api,
            "FAST_ACTION_RECOVERY_JOURNAL",
            FailedJournal(),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "fast_action_recovery_unavailable",
            ):
                fast_api.prepare_registered_background_action(
                    "긴 작업",
                    source="control_page",
                )

        task = fast_api.ACTION_COORDINATOR.get(
            "fast-action-1"
        )
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "failed")
        self.assertEqual(
            task.error,
            "fast_action_recovery_unavailable",
        )
        self.assertNotIn(
            "private journal path",
            task.final_reply,
        )

    def test_completed_background_action_clears_recovery_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = self.owner(root)
            journal = self.journal(root)
            fast_api.register_background_action_handler(
                kind="long_check",
                matcher=lambda text: text == "긴 작업",
                runner=AsyncMock(
                    return_value="검증된 최종 결과"
                ),
                start_reply="확인해 볼게.",
            )
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    journal,
                ),
            ):
                prepared = (
                    fast_api.prepare_registered_background_action(
                        "긴 작업",
                        source="control_page",
                    )
                )
                self.assertIsNotNone(prepared)
                task, runner = prepared

                async def execute() -> None:
                    background = (
                        fast_api.launch_background_action(
                            task,
                            runner,
                        )
                    )
                    await background

                asyncio.run(execute())

            self.assertEqual(
                journal.public_status()["pendingCount"],
                0,
            )
            restored = self.owner(root)
            self.assertEqual(
                restored.restored_chat_messages()[-1]["text"],
                "검증된 최종 결과",
            )

    def test_prelaunch_failure_commits_terminal_turn_and_clears_marker(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = self.owner(root)
            journal = self.journal(root)
            task = fast_api.ACTION_COORDINATOR.start(
                kind="long_check",
                source="control_page",
                user_text="긴 작업",
                start_reply="확인해 볼게.",
            )
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    journal,
                ),
            ):
                fast_api.begin_fast_action_recovery(task)
                fast_api.ACTION_COORDINATOR.fail(
                    task.task_id,
                    "fast_control_chat_failed",
                    reply="고정 실패 응답",
                )
                result = (
                    fast_api
                    .commit_fast_control_terminal_turn(
                        task.task_id,
                        "긴 작업",
                        "고정 실패 응답",
                    )
                )

            self.assertTrue(result["durable"])
            self.assertEqual(
                journal.public_status()["pendingCount"],
                0,
            )
            restored = self.owner(root)
            self.assertEqual(
                restored.restored_chat_messages()[-1]["text"],
                "고정 실패 응답",
            )

    def test_restart_recovery_commits_one_fixed_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = self.owner(root)
            owner.record_completed_turn(
                "조사해줘",
                "확인해 볼게.",
            )
            journal = self.journal(root)
            journal.begin("fast-action-1")
            messages: list[dict] = []
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    journal,
                ),
                patch.object(
                    fast_api,
                    "CHAT_MESSAGES",
                    messages,
                ),
            ):
                status = (
                    fast_api
                    .recover_fast_control_actions_after_restart()
                )

            self.assertEqual(status["state"], "recovered")
            self.assertEqual(
                status["lastRecoveryCount"],
                1,
            )
            self.assertEqual(
                messages[-1]["text"],
                fast_api.FAST_ACTION_RECOVERY_NOTICE,
            )
            restored_messages = (
                self.owner(root).restored_chat_messages()
            )
            self.assertEqual(
                sum(
                    message["text"]
                    == fast_api.FAST_ACTION_RECOVERY_NOTICE
                    for message in restored_messages
                ),
                1,
            )

    def test_restart_after_terminal_commit_does_not_add_false_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = self.owner(root)
            owner.record_completed_turn(
                "조사해줘",
                "확인해 볼게.",
            )
            journal = self.journal(root)
            journal.begin("fast-action-1")
            owner.record_assistant_followup(
                "이미 전달된 최종 결과",
                before_commit=lambda generation: (
                    journal.prepare_terminal(
                        "fast-action-1",
                        expected_generation=generation,
                    )
                ),
            )
            restored_owner = self.owner(root)
            restored_journal = self.journal(root)
            restored_messages = (
                restored_owner.restored_chat_messages()
            )
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    restored_owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    restored_journal,
                ),
                patch.object(
                    fast_api,
                    "CHAT_MESSAGES",
                    restored_messages,
                ),
            ):
                status = (
                    fast_api
                    .recover_fast_control_actions_after_restart()
                )

            self.assertEqual(status["state"], "recovered")
            serialized = json.dumps(
                restored_messages,
                ensure_ascii=False,
            )
            self.assertNotIn(
                fast_api.FAST_ACTION_RECOVERY_NOTICE,
                serialized,
            )
            self.assertEqual(
                restored_messages[-1]["text"],
                "이미 전달된 최종 결과",
            )

    def test_corrupt_journal_recovers_only_after_durable_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = self.owner(root)
            owner.record_completed_turn(
                "조사해줘",
                "확인해 볼게.",
            )
            journal_path = (
                root
                / "fast_control_actions"
                / "recovery.json"
            )
            journal_path.parent.mkdir(parents=True)
            journal_path.write_text(
                json.dumps(
                    {
                        "schema": "corrupt",
                        "privateMessage": "노출 금지 원문",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            journal = self.journal(root)
            messages: list[dict] = []
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    journal,
                ),
                patch.object(
                    fast_api,
                    "CHAT_MESSAGES",
                    messages,
                ),
            ):
                status = (
                    fast_api
                    .recover_fast_control_actions_after_restart()
                )

            self.assertEqual(status["state"], "recovered")
            self.assertEqual(
                status["lastErrorCode"],
                "fast_action_recovery_journal_corrupt",
            )
            self.assertEqual(
                messages[-1]["text"],
                fast_api.FAST_ACTION_RECOVERY_NOTICE,
            )
            repaired = journal_path.read_text(
                encoding="utf-8"
            )
            self.assertNotIn("privateMessage", repaired)
            self.assertNotIn("노출 금지 원문", repaired)

    def test_corrupt_journal_blocks_unrelated_generation_advance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal_path = (
                root
                / "fast_control_actions"
                / "recovery.json"
            )
            journal_path.parent.mkdir(parents=True)
            journal_path.write_text(
                '{"schema":"corrupt"}',
                encoding="utf-8",
            )
            owner = self.owner(root)
            journal = self.journal(root)
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    journal,
                ),
            ):
                result = fast_api.commit_fast_control_turn(
                    "다른 질문",
                    "다른 답변",
                )

            self.assertFalse(result["durable"])
            self.assertEqual(owner.status()["generation"], 0)
            self.assertEqual(
                journal.public_status()["state"],
                "corrupt",
            )

    def test_missing_journal_after_head_recovers_only_after_notice(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = self.owner(root)
            owner.record_completed_turn(
                "조사해줘",
                "확인해 볼게.",
            )
            journal = self.journal(root)
            journal.begin("fast-action-1")
            head_generation = journal.public_status()[
                "generation"
            ]
            journal.path.unlink()
            restored_journal = self.journal(root)
            self.assertEqual(
                restored_journal.public_status()["state"],
                "corrupt",
            )
            messages: list[dict] = []
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "FAST_ACTION_RECOVERY_JOURNAL",
                    restored_journal,
                ),
                patch.object(
                    fast_api,
                    "CHAT_MESSAGES",
                    messages,
                ),
            ):
                status = (
                    fast_api
                    .recover_fast_control_actions_after_restart()
                )

            self.assertEqual(status["state"], "recovered")
            self.assertTrue(status["rollbackProtected"])
            self.assertEqual(status["headState"], "current")
            self.assertGreater(
                status["generation"],
                head_generation,
            )
            self.assertEqual(
                status["lastErrorCode"],
                "fast_action_recovery_journal_corrupt",
            )
            self.assertEqual(
                messages[-1]["text"],
                fast_api.FAST_ACTION_RECOVERY_NOTICE,
            )


if __name__ == "__main__":
    unittest.main()
