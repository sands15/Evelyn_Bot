from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.explicit_memory_confirmation import (  # noqa: E402
    ExplicitMemoryConfirmationError,
    execute_explicit_memory_confirmation,
    is_explicit_memory_confirmation_command,
    parse_explicit_memory_confirmation,
    store_explicit_memory_confirmation,
)
from evelyn_core.memory_vault import memory_vault_user_note  # noqa: E402
from evelyn_core.memory_confirmation_contract import (  # noqa: E402
    is_explicit_memory_confirmation_receipt,
)


class ExplicitMemoryConfirmationTests(unittest.TestCase):
    def test_parser_accepts_only_explicit_commands(self) -> None:
        self.assertEqual(
            parse_explicit_memory_confirmation(
                "/remember 나는 산책을 좋아해"
            ),
            "나는 산책을 좋아해",
        )
        self.assertEqual(
            parse_explicit_memory_confirmation(
                "기억해 줘: 나는 아침 커피를 좋아해"
            ),
            "나는 아침 커피를 좋아해",
        )
        self.assertIsNone(
            parse_explicit_memory_confirmation(
                "언젠가 이 이야기를 기억해 줬으면 좋겠어"
            )
        )
        self.assertTrue(
            is_explicit_memory_confirmation_command(
                "기억해줘: 사용자 확인 사실"
            )
        )
        self.assertFalse(
            is_explicit_memory_confirmation_command(
                "이 이야기를 기억해 줬으면 좋겠어"
            )
        )
        with self.assertRaises(ExplicitMemoryConfirmationError) as caught:
            parse_explicit_memory_confirmation("/remember")
        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_text_required",
        )

    def test_store_is_grounded_confirmed_idempotent_and_content_free(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = store_explicit_memory_confirmation(
                "나는 산책을 좋아해",
                action_id="control-request-123",
                root=root,
            )
            second = store_explicit_memory_confirmation(
                "나는 산책을 좋아해",
                action_id="control-request-123",
                root=root,
            )
            note = memory_vault_user_note(
                first["noteId"],
                root=root,
            )
            markdown_files = list(
                (root / "memory_vault" / "concepts").glob("*.md")
            )
            raw = markdown_files[0].read_text(encoding="utf-8")

        self.assertEqual(first["state"], "stored")
        self.assertEqual(second["state"], "duplicate")
        self.assertEqual(first["noteId"], second["noteId"])
        self.assertTrue(first["contentFree"])
        self.assertNotIn("산책", str(first))
        self.assertTrue(note["ok"])
        self.assertEqual(note["card"]["body"], "나는 산책을 좋아해")
        self.assertTrue(note["card"]["confirmed"])
        self.assertEqual(len(markdown_files), 1)
        self.assertIn("source: control-page-user", raw)
        self.assertIn(
            "source_refs: [turn:control-request-123:user]",
            raw,
        )
        self.assertIn("confirmed_at:", raw)
        self.assertIn("evidence_hashes:", raw)

    def test_invalid_action_id_is_not_written_to_source_ref(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = store_explicit_memory_confirmation(
                "사용자 확인 사실",
                action_id="../../private path",
                root=root,
            )
            raw = next(
                (root / "memory_vault" / "concepts").glob("*.md")
            ).read_text(encoding="utf-8")

        self.assertNotIn("private path", raw)
        self.assertNotIn("private path", str(receipt))
        self.assertRegex(
            receipt["sourceRef"],
            r"^turn:[0-9a-f]{32}:user$",
        )

    def test_same_action_cannot_be_reused_for_different_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_explicit_memory_confirmation(
                "첫 번째 사실",
                action_id="same-action-123",
                root=root,
            )
            with self.assertRaises(
                ExplicitMemoryConfirmationError
            ) as caught:
                store_explicit_memory_confirmation(
                    "다른 사실",
                    action_id="same-action-123",
                    root=root,
                )

        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_hash_collision",
        )

    def test_action_identity_and_evidence_turn_are_separate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = store_explicit_memory_confirmation(
                "분리된 근거",
                action_id="discord-message:1:2:99",
                evidence_turn_id="discord-turn-abc",
                source="discord-user",
                root=root,
            )
            raw = next(
                (root / "memory_vault" / "concepts").glob("*.md")
            ).read_text(encoding="utf-8")
            note = memory_vault_user_note(
                receipt["noteId"],
                root=root,
            )

        self.assertEqual(
            receipt["sourceRef"],
            "turn:discord-turn-abc:user",
        )
        self.assertIn(
            "source_refs: [turn:discord-turn-abc:user]",
            raw,
        )
        self.assertNotIn("discord-message:1:2:99", raw)
        self.assertIn("source: discord-user", raw)
        self.assertEqual(
            note["card"]["provenance"]["source"],
            "discord-user",
        )
        self.assertEqual(
            note["card"]["provenance"]["sourceType"],
            "user",
        )

    def test_duplicate_receipt_keeps_original_evidence_turn(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = store_explicit_memory_confirmation(
                "재시도에도 같은 근거",
                action_id="discord-message:1:2:100",
                evidence_turn_id="discord-turn-original",
                source="discord-user",
                root=root,
            )
            duplicate = store_explicit_memory_confirmation(
                "재시도에도 같은 근거",
                action_id="discord-message:1:2:100",
                evidence_turn_id="discord-turn-retry",
                source="discord-user",
                root=root,
            )

        self.assertEqual(first["state"], "stored")
        self.assertEqual(duplicate["state"], "duplicate")
        self.assertEqual(
            duplicate["sourceRef"],
            "turn:discord-turn-original:user",
        )

    def test_unknown_source_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(
                ExplicitMemoryConfirmationError
            ) as caught:
                store_explicit_memory_confirmation(
                    "출처가 명확해야 해",
                    action_id="unknown-source-123",
                    source="untrusted-surface",
                    root=Path(temp_dir),
                )

        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_source_invalid",
        )

    def test_executor_rejects_empty_command_without_content(self) -> None:
        matched, reply, receipt, error = (
            execute_explicit_memory_confirmation("/remember")
        )

        self.assertTrue(matched)
        self.assertIn("기억할 내용", reply)
        self.assertEqual(receipt["state"], "rejected")
        self.assertTrue(receipt["contentFree"])
        self.assertEqual(error, "memory_confirmation_text_required")

    def test_receipt_validator_rejects_extra_or_private_fields(self) -> None:
        valid = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-safe-id",
            "sourceRef": "turn:discord-turn-abc:user",
            "confirmedAt": "2026-07-31T00:00:00+00:00",
            "contentFree": True,
        }

        self.assertTrue(
            is_explicit_memory_confirmation_receipt(valid)
        )
        self.assertFalse(
            is_explicit_memory_confirmation_receipt(
                {**valid, "body": "private memory"}
            )
        )
        self.assertFalse(
            is_explicit_memory_confirmation_receipt(
                {**valid, "noteId": "../../private"}
            )
        )
        self.assertFalse(
            is_explicit_memory_confirmation_receipt(
                {
                    **valid,
                    "confirmedAt": "2026-07-31T00:00:00",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
