from __future__ import annotations

import hashlib
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
    store_explicit_memory_confirmation,
)
from evelyn_core.memory_confirmation_contract import (  # noqa: E402
    memory_owner_scope,
)
from evelyn_core.memory_prompt_policy import (  # noqa: E402
    prepare_memory_context_for_prompt,
    reconcile_memory_receipt_for_prompt,
    validated_memory_grounding_state,
)
from evelyn_core.memory_vault import (  # noqa: E402
    build_memory_vault_context,
    delete_memory_vault_user_note,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    preview_memory_vault_user_note_deletion,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


class ExplicitMemoryLifecycleTests(unittest.TestCase):
    owner_scope = memory_owner_scope(
        guild_id=7,
        person_key="user:11",
    )

    def test_owner_scope_isolates_recall_cache_and_legacy_notes(
        self,
    ) -> None:
        canary = "evelyn-owner-scope-canary-991"
        owner_b = memory_owner_scope(
            guild_id=7,
            person_key="user:12",
        )
        local_owner = memory_owner_scope(
            guild_id=None,
            person_key="control-page:local",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                f"개인 기억 표식은 {canary}",
                action_id="owner-scope-action-991",
                owner_scope=self.owner_scope,
                root=root,
            )
            other_first_context = build_memory_vault_context(
                7,
                "개인 기억 표식",
                owner_scope=owner_b,
                root=root,
            )
            owner_after_empty_cache = build_memory_vault_context(
                7,
                "개인 기억 표식",
                owner_scope=self.owner_scope,
                root=root,
            )
            owner_receipt: dict = {}
            owner_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
                receipt=owner_receipt,
            )
            for other_scope in (owner_b, local_owner):
                other_receipt: dict = {}
                other_context = build_memory_vault_context(
                    7,
                    canary,
                    owner_scope=other_scope,
                    root=root,
                    receipt=other_receipt,
                )
                self.assertNotIn(canary, other_context)
                self.assertNotIn(
                    stored["noteId"],
                    other_receipt["suppliedNoteIds"],
                )
            owner_after_other_cache = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
            )
            detail = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(
                    line
                    for line in raw.splitlines()
                    if not line.startswith("owner_scope:")
                )
                + "\n",
                encoding="utf-8",
            )
            legacy_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                root=root,
            )

        self.assertIn(canary, owner_context)
        self.assertNotIn(canary, other_first_context)
        self.assertIn(canary, owner_after_empty_cache)
        self.assertIn(
            stored["noteId"],
            owner_receipt["suppliedNoteIds"],
        )
        self.assertIn(canary, owner_after_other_cache)
        self.assertNotIn(self.owner_scope, str(stored))
        self.assertNotIn(self.owner_scope, str(detail))
        self.assertNotIn(canary, legacy_context)

    def test_renamed_marker_only_v1_note_is_not_recalled(self) -> None:
        fact = "legacy-marker-only-canary-991"
        other_owner = memory_owner_scope(
            guild_id=7,
            person_key="user:99",
        )
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_memory_vault_note(
                note_type="concept",
                title="Renamed Legacy Memory",
                body=fact,
                storage_key="renamed-memory",
                source="control-page-user",
                source_refs=["turn:legacy-v1:user"],
                evidence_hashes=[
                    hashlib.sha256(fact.encode("utf-8")).hexdigest()
                ],
                confirmed_at="2026-07-31T00:00:00+00:00",
                memory_contract="memory.user-confirmation.note.v1",
                root=root,
            )
            context = build_memory_vault_context(
                7,
                fact,
                owner_scope=other_owner,
                root=root,
            )

        self.assertNotIn(fact, context)

    def test_confirmed_memory_is_attributed_then_disappears_after_delete(self) -> None:
        canary = "evelyn-canary-orchid-731"
        fact = f"내가 좋아하는 암호명은 {canary}이야"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                fact,
                action_id="discord-message:7:8:900",
                evidence_turn_id="discord-turn-memory-900",
                source="discord-user",
                owner_scope=self.owner_scope,
                root=root,
            )
            before_receipt: dict = {}
            before_context = build_memory_vault_context(
                7,
                canary,
                source="discord-text",
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=before_receipt,
            )
            grounding_state = validated_memory_grounding_state(
                before_receipt,
                has_context=bool(before_context),
            )
            before_boundary = prepare_memory_context_for_prompt(
                before_context,
                grounding_state=grounding_state,
            )
            reconcile_memory_receipt_for_prompt(
                before_receipt,
                before_boundary,
            )

            preview = preview_memory_vault_user_note_deletion(
                stored["noteId"],
                reason="privacy_request",
                root=root,
                now=lambda: 100.0,
            )
            deleted = delete_memory_vault_user_note(
                stored["noteId"],
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
                now=lambda: 101.0,
            )
            after_receipt: dict = {}
            after_context = build_memory_vault_context(
                7,
                canary,
                source="discord-text",
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=after_receipt,
            )
            tombstone = (
                root
                / "memory_index"
                / "memory_deletions.jsonl"
            ).read_text(encoding="utf-8")

        self.assertEqual(stored["state"], "stored")
        self.assertEqual(before_receipt["groundingState"], "attributed")
        self.assertIn(stored["noteId"], before_receipt["suppliedNoteIds"])
        self.assertEqual(before_receipt["sourceTypeCounts"]["user"], 1)
        self.assertIn(fact, before_boundary.context)
        self.assertNotIn("MEMORY_CONFIRMATION_RULE:", before_boundary.context)
        self.assertTrue(preview["ok"])
        self.assertNotIn(canary, str(preview))
        self.assertTrue(deleted["ok"])
        self.assertNotIn(canary, str(deleted))
        self.assertGreater(
            deleted["memoryVersion"],
            before_receipt["memoryVersion"],
        )
        self.assertNotIn(stored["noteId"], after_receipt["suppliedNoteIds"])
        self.assertNotIn(fact, after_context)
        self.assertNotIn(canary, tombstone)

    def test_damaged_confirmed_memory_is_evicted_from_cached_recall(self) -> None:
        canary = "evelyn-damaged-memory-842"
        fact = f"손상 차단 표식은 {canary}"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                fact,
                action_id="control-damaged-memory-842",
                owner_scope=self.owner_scope,
                root=root,
            )
            warm_receipt: dict = {}
            build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=warm_receipt,
            )
            cached_receipt: dict = {}
            cached_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=cached_receipt,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            lines = path.read_text(encoding="utf-8").splitlines()
            path.write_text(
                "\n".join(
                    "evidence_hashes: []"
                    if line.startswith("evidence_hashes:")
                    else line
                    for line in lines
                )
                + "\n",
                encoding="utf-8",
            )

            blocked_receipt: dict = {}
            blocked_context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=blocked_receipt,
            )
            detail = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            snapshot = memory_vault_user_snapshot(root=root)

        self.assertIn(fact, cached_context)
        self.assertTrue(cached_receipt["cacheHit"])
        self.assertNotIn(fact, blocked_context)
        self.assertNotIn(
            stored["noteId"],
            blocked_receipt["suppliedNoteIds"],
        )
        self.assertGreater(
            blocked_receipt["memoryVersion"],
            cached_receipt["memoryVersion"],
        )
        self.assertEqual(
            detail["card"]["userConfirmationIntegrity"],
            "invalid",
        )
        self.assertFalse(detail["card"]["recallEligible"])
        self.assertFalse(detail["card"]["canConfirm"])
        self.assertTrue(detail["card"]["canEdit"])
        self.assertEqual(
            detail["card"]["recallBlockedReason"],
            "user_confirmation_integrity_invalid",
        )
        self.assertEqual(snapshot["counts"]["integrityInvalid"], 1)

    def test_legacy_confirmed_memory_without_contract_marker_still_recalls(self) -> None:
        canary = "evelyn-legacy-confirmed-memory-854"
        fact = f"기존 확인 기억 표식은 {canary}"
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                fact,
                action_id="control-legacy-memory-854",
                owner_scope=self.owner_scope,
                root=root,
            )
            path = next(
                (root / "memory_vault" / "concepts").glob(
                    "user-confirmed-*.md"
                )
            )
            raw = path.read_text(encoding="utf-8")
            path.write_text(
                "\n".join(
                    line
                    for line in raw.splitlines()
                    if not line.startswith("memory_contract:")
                )
                + "\n",
                encoding="utf-8",
            )

            receipt: dict = {}
            context = build_memory_vault_context(
                7,
                canary,
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=receipt,
            )
            detail = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )

        self.assertIn(fact, context)
        self.assertIn(stored["noteId"], receipt["suppliedNoteIds"])
        self.assertEqual(
            detail["card"]["userConfirmationIntegrity"],
            "verified",
        )
        self.assertTrue(detail["card"]["recallEligible"])

    def test_user_edit_rebinds_confirmed_memory_integrity(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stored = store_explicit_memory_confirmation(
                "수정 전 표식 old-confirmed-memory-913",
                action_id="control-edit-memory-913",
                owner_scope=self.owner_scope,
                root=root,
            )
            before = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            edited = update_memory_vault_user_note(
                stored["noteId"],
                "edit",
                title="사용자가 수정한 확인 기억",
                body=(
                    "수정 후 표식 new-confirmed-memory-913\n"
                    "두 번째 줄도 같은 사용자 수정 근거야"
                ),
                expected_content_hash=before["card"]["sourceHash"],
                root=root,
            )
            after = memory_vault_user_note(
                stored["noteId"],
                root=root,
            )
            receipt: dict = {}
            context = build_memory_vault_context(
                7,
                "new-confirmed-memory-913",
                owner_scope=self.owner_scope,
                max_items=1,
                root=root,
                receipt=receipt,
            )

        self.assertTrue(edited["ok"])
        self.assertEqual(
            after["card"]["userConfirmationIntegrity"],
            "verified",
        )
        self.assertTrue(after["card"]["recallEligible"])
        self.assertEqual(
            after["card"]["provenance"]["source"],
            "user-edit",
        )
        self.assertIn(stored["noteId"], receipt["suppliedNoteIds"])
        self.assertIn("new-confirmed-memory-913", context)
        self.assertIn("두 번째 줄도 같은 사용자 수정 근거야", context)
        self.assertNotIn("old-confirmed-memory-913", context)


if __name__ == "__main__":
    unittest.main()
