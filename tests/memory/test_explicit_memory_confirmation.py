from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


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
    memory_owner_scope,
    memory_owner_scope_for_local_surface,
    memory_reset_scope,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)


class ExplicitMemoryConfirmationTests(unittest.TestCase):
    owner_scope = memory_owner_scope(
        guild_id=None,
        person_key="control-page:local",
    )

    def test_local_surface_owner_matches_fast_principal_policy(self) -> None:
        self.assertEqual(
            memory_owner_scope_for_local_surface(),
            self.owner_scope,
        )
        configured_owner_scope = memory_owner_scope_for_local_surface(
            configured_guild_id=77,
            configured_user_id=88,
        )
        self.assertEqual(
            configured_owner_scope,
            memory_owner_scope(
                guild_id=77,
                person_key="user:88",
            ),
        )
        self.assertNotEqual(configured_owner_scope, self.owner_scope)
        with self.assertRaisesRegex(
            ValueError,
            "memory_owner_scope_invalid",
        ):
            memory_owner_scope_for_local_surface(
                configured_guild_id=77,
            )
        with self.assertRaisesRegex(
            ValueError,
            "memory_owner_scope_invalid",
        ):
            memory_owner_scope(
                guild_id=0,
                person_key="user:88",
            )

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
                owner_scope=self.owner_scope,
                root=root,
            )
            second = store_explicit_memory_confirmation(
                "나는 산책을 좋아해",
                action_id="control-request-123",
                owner_scope=self.owner_scope,
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
        self.assertNotIn("control-request-123", str(first))
        self.assertRegex(
            first["sourceRef"],
            r"^turn:opaque-turn-[0-9a-f]{64}:user$",
        )
        self.assertTrue(note["ok"])
        self.assertEqual(note["card"]["body"], "나는 산책을 좋아해")
        self.assertTrue(note["card"]["confirmed"])
        self.assertEqual(
            note["card"]["userConfirmationIntegrity"],
            "verified",
        )
        self.assertTrue(note["card"]["recallEligible"])
        self.assertEqual(len(markdown_files), 1)
        self.assertIn("source: control-page-user", raw)
        self.assertIn(
            "source_refs: [turn:control-request-123:user]",
            raw,
        )
        self.assertIn("confirmed_at:", raw)
        self.assertIn(
            "memory_contract: memory.user-confirmation.note.v2",
            raw,
        )
        self.assertIn("evidence_hashes:", raw)

    def test_invalid_action_id_is_not_written_to_source_ref(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = store_explicit_memory_confirmation(
                "사용자 확인 사실",
                action_id="../../private path",
                owner_scope=self.owner_scope,
                root=root,
            )
            raw = next(
                (root / "memory_vault" / "concepts").glob("*.md")
            ).read_text(encoding="utf-8")

        self.assertNotIn("private path", raw)
        self.assertNotIn("private path", str(receipt))
        self.assertRegex(
            receipt["sourceRef"],
            r"^turn:opaque-turn-[0-9a-f]{64}:user$",
        )

    def test_same_action_cannot_be_reused_for_different_content(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_explicit_memory_confirmation(
                "첫 번째 사실",
                action_id="same-action-123",
                owner_scope=self.owner_scope,
                root=root,
            )
            with self.assertRaises(
                ExplicitMemoryConfirmationError
            ) as caught:
                store_explicit_memory_confirmation(
                    "다른 사실",
                    action_id="same-action-123",
                    owner_scope=self.owner_scope,
                    root=root,
                )

        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_hash_collision",
        )

    def test_same_action_is_isolated_by_owner_scope(self) -> None:
        other_owner_scope = memory_owner_scope(
            guild_id=77,
            person_key="user:88",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = store_explicit_memory_confirmation(
                "각자 저장한 같은 사실",
                action_id="shared-action-123",
                owner_scope=self.owner_scope,
                root=root,
            )
            second = store_explicit_memory_confirmation(
                "각자 저장한 같은 사실",
                action_id="shared-action-123",
                owner_scope=other_owner_scope,
                reset_scope=memory_reset_scope(77),
                root=root,
            )

        self.assertNotEqual(first["noteId"], second["noteId"])
        self.assertEqual(first["state"], "stored")
        self.assertEqual(second["state"], "stored")

    def test_action_identity_and_evidence_turn_are_separate(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            receipt = store_explicit_memory_confirmation(
                "분리된 근거",
                action_id="discord-message:1:2:99",
                evidence_turn_id="discord-turn-abc",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(1),
                root=root,
            )
            raw = next(
                (root / "memory_vault" / "concepts").glob("*.md")
            ).read_text(encoding="utf-8")
            note = memory_vault_user_note(
                receipt["noteId"],
                root=root,
            )

        self.assertRegex(
            receipt["sourceRef"],
            r"^turn:opaque-turn-[0-9a-f]{64}:user$",
        )
        self.assertNotIn("discord-turn-abc", str(receipt))
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
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(1),
                root=root,
            )
            duplicate = store_explicit_memory_confirmation(
                "재시도에도 같은 근거",
                action_id="discord-message:1:2:100",
                evidence_turn_id="discord-turn-retry",
                source="discord-user",
                owner_scope=self.owner_scope,
                reset_scope=memory_reset_scope(1),
                root=root,
            )

        self.assertEqual(first["state"], "stored")
        self.assertEqual(duplicate["state"], "duplicate")
        self.assertEqual(
            duplicate["sourceRef"],
            first["sourceRef"],
        )
        self.assertNotIn("discord-turn-original", str(duplicate))
        self.assertNotIn("discord-turn-retry", str(duplicate))

    def test_unknown_source_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(
                ExplicitMemoryConfirmationError
            ) as caught:
                store_explicit_memory_confirmation(
                    "출처가 명확해야 해",
                    action_id="unknown-source-123",
                    source="untrusted-surface",
                    owner_scope=self.owner_scope,
                    root=Path(temp_dir),
                )

        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_source_invalid",
        )

    def test_nonlocal_write_requires_exact_reset_scope(self) -> None:
        cases = (
            ("discord-user", self.owner_scope),
            (
                "control-page-user",
                memory_owner_scope(
                    guild_id=77,
                    person_key="user:88",
                ),
            ),
        )
        for index, (source, owner_scope) in enumerate(cases):
            with self.subTest(source=source):
                with TemporaryDirectory() as temp_dir:
                    with self.assertRaises(
                        ExplicitMemoryConfirmationError
                    ) as caught:
                        store_explicit_memory_confirmation(
                            "길드 범위가 없는 쓰기는 막아",
                            action_id=(
                                f"missing-reset-scope-{index}"
                            ),
                            source=source,
                            owner_scope=owner_scope,
                            root=Path(temp_dir),
                        )

                self.assertEqual(
                    caught.exception.code,
                    "memory_confirmation_reset_scope_required",
                )

    def test_duplicate_fails_closed_when_stored_provenance_is_damaged(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store_explicit_memory_confirmation(
                "손상되면 성공으로 답하지 마",
                action_id="damaged-provenance-123",
                owner_scope=self.owner_scope,
                root=root,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob("*.md")
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            damaged = [
                "evidence_hashes: []"
                if line.startswith("evidence_hashes:")
                else line
                for line in lines
            ]
            path.write_text(
                "\n".join(damaged) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                ExplicitMemoryConfirmationError
            ) as caught:
                store_explicit_memory_confirmation(
                    "손상되면 성공으로 답하지 마",
                    action_id="damaged-provenance-123",
                    owner_scope=self.owner_scope,
                    root=root,
                )

        self.assertEqual(
            caught.exception.code,
            "memory_confirmation_write_unverified",
        )

    def test_executor_rejects_empty_command_without_content(self) -> None:
        matched, reply, receipt, error = (
            execute_explicit_memory_confirmation(
                "/remember",
                owner_scope=self.owner_scope,
            )
        )

        self.assertTrue(matched)
        self.assertIn("기억할 내용", reply)
        self.assertEqual(receipt["state"], "rejected")
        self.assertTrue(receipt["contentFree"])
        self.assertEqual(error, "memory_confirmation_text_required")

    def test_executor_reports_unverified_write_without_memory_content(self) -> None:
        with patch(
            "evelyn_core.explicit_memory_confirmation."
            "store_explicit_memory_confirmation",
            side_effect=ExplicitMemoryConfirmationError(
                "memory_confirmation_write_unverified"
            ),
        ):
            matched, reply, receipt, error = (
                execute_explicit_memory_confirmation(
                    "/remember private-canary-must-not-leak",
                    action_id="failed-write-action-123",
                    owner_scope=self.owner_scope,
                )
            )

        self.assertTrue(matched)
        self.assertIn("저장하지 못했어", reply)
        self.assertEqual(receipt["state"], "failed")
        self.assertEqual(
            receipt["error"],
            "memory_confirmation_write_unverified",
        )
        self.assertTrue(receipt["contentFree"])
        self.assertEqual(error, "memory_confirmation_write_unverified")
        self.assertNotIn("private-canary", str(receipt))

    def test_executor_does_not_downgrade_deletion_integrity_failure(
        self,
    ) -> None:
        with patch(
            "evelyn_core.explicit_memory_confirmation."
            "store_explicit_memory_confirmation",
            side_effect=MemoryDeletionJournalIntegrityError(
                "PRIVATE_MUST_NOT_SURVIVE"
            ),
        ):
            with self.assertRaisesRegex(
                MemoryDeletionJournalIntegrityError,
                "^memory_deletion_journal_integrity_failed$",
            ):
                execute_explicit_memory_confirmation(
                    "/remember private-canary-must-not-leak",
                    action_id="integrity-failure-action-123",
                    owner_scope=self.owner_scope,
                )

    def test_receipt_validator_rejects_extra_or_private_fields(self) -> None:
        valid = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-0123456789abcdef",
            "sourceRef": (
                "turn:opaque-turn-"
                + ("a" * 64)
                + ":user"
            ),
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
