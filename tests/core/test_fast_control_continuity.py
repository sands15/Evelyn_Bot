from __future__ import annotations

import json
import sys
import tempfile
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

from evelyn_core.continuity_commit_contract import (  # noqa: E402
    require_durable_continuity_receipt,
)
from evelyn_core.fast_control_continuity import (  # noqa: E402
    FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
    FastControlContinuityOwner,
)


class FastControlContinuityTests(unittest.TestCase):
    def test_disabled_owner_never_creates_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=False,
            )

            self.assertEqual(owner.restored_chat_messages(), [])
            self.assertEqual(owner.status()["state"], "disabled")
            self.assertFalse(
                (root / "fast_control_continuity").exists()
            )

    def test_completed_turn_restores_after_owner_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            raw_status = first.record_completed_turn(
                "실패 전 질문",
                "고정 실패 응답",
            )
            receipt = require_durable_continuity_receipt(
                raw_status
            )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = second.restored_chat_messages()
            checkpoint_text = (
                root
                / "fast_control_continuity"
                / "active.json"
            ).read_text(encoding="utf-8")

        self.assertTrue(receipt["durable"])
        self.assertGreaterEqual(receipt["generation"], 1)
        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in restored
            ],
            [
                ("user", "실패 전 질문"),
                ("assistant", "고정 실패 응답"),
            ],
        )
        self.assertEqual(
            second.restore_status["state"],
            "restored",
        )
        self.assertNotIn(
            "fast-control short-lived conversation continuity",
            checkpoint_text,
        )

    def test_background_followup_preserves_exact_message_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            first.record_completed_turn(
                "긴 작업 해줘",
                "작업을 시작했어.",
            )
            first.record_assistant_followup(
                "작업을 완료했어."
            )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = second.restored_chat_messages()

        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in restored
            ],
            [
                ("user", "긴 작업 해줘"),
                ("assistant", "작업을 시작했어."),
                ("assistant", "작업을 완료했어."),
            ],
        )

    def test_status_is_content_free_and_exact(self) -> None:
        private_user = (
            "Bearer fast-control-user-secret "
            r"C:\Users\Admin\private.txt"
        )
        private_answer = (
            "https://internal.example/answer"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            owner.record_completed_turn(
                private_user,
                private_answer,
            )
            status = owner.status()

        rendered = json.dumps(status, ensure_ascii=False)
        self.assertEqual(
            status["schema"],
            FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
        )
        self.assertTrue(status["enabled"])
        self.assertTrue(status["durableReady"])
        self.assertEqual(status["messageCount"], 2)
        self.assertTrue(status["policy"]["contentFree"])
        self.assertNotIn("fast-control-user-secret", rendered)
        self.assertNotIn("internal.example", rendered)
        self.assertNotIn("Users", rendered)

    def test_corrupt_checkpoint_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            continuity_root = (
                root / "fast_control_continuity"
            )
            continuity_root.mkdir(parents=True)
            (continuity_root / "active.json").write_text(
                "{broken",
                encoding="utf-8",
            )

            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(owner.restored_chat_messages(), [])
        self.assertEqual(
            owner.restore_status["state"],
            "error",
        )
        self.assertEqual(
            owner.restore_status["lastErrorCode"],
            "conversation_continuity_restore_failed",
        )

    def test_history_is_bounded_to_configured_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                max_history_items=4,
                log=lambda *_args, **_kwargs: None,
            )
            for index in range(4):
                owner.record_completed_turn(
                    f"user-{index}",
                    f"answer-{index}",
                )

            restored = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                max_history_items=4,
                log=lambda *_args, **_kwargs: None,
            ).restored_chat_messages()

        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in restored
            ],
            [
                ("user", "user-2"),
                ("assistant", "answer-2"),
                ("user", "user-3"),
                ("assistant", "answer-3"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
