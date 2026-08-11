from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_prompt_policy import (  # noqa: E402
    MEMORY_CONTEXT_USE_POLICY,
    MEMORY_PROMPT_MAX_CHARS,
    memory_deletion_boundary_from_position,
    memory_deletion_boundary_not_required,
    normalize_memory_retrieval_mode,
    prepare_memory_context_for_prompt,
    reconcile_memory_receipt_for_prompt,
    validated_memory_grounding_state,
    wrap_memory_context_for_prompt,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionPosition,
)
from evelyn_core.memory_deletion_outbound import (  # noqa: E402
    capture_memory_deletion_outbound_position,
    current_memory_deletion_outbound_position,
    reset_memory_deletion_outbound_position,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    memory_receipt_ref_from_receipt,
)


class MemoryPromptPolicyTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_memory_deletion_outbound_position()

    def test_retrieval_mode_uses_closed_content_free_enum(self) -> None:
        for value in (
            "fts",
            "scan",
            "fts+vector",
            "scan+vector",
            "cache",
            "unknown",
        ):
            self.assertEqual(
                normalize_memory_retrieval_mode(value),
                value,
            )
        private_canary = "PRIVATE retrieval mode transcript"
        self.assertEqual(
            normalize_memory_retrieval_mode(private_canary),
            "unknown",
        )
        receipt = {
            "state": "empty",
            "retrievalMode": private_canary,
        }
        reconcile_memory_receipt_for_prompt(
            receipt,
            prepare_memory_context_for_prompt(
                "",
                grounding_state="empty",
            ),
        )
        self.assertEqual(receipt["retrievalMode"], "unknown")
        self.assertNotIn(private_canary, str(receipt))

    def test_receipt_preserves_read_only_fallback_markers(self) -> None:
        receipt = {
            "state": "empty",
            "indexFresh": True,
            "readOnlyFallback": True,
        }

        reconcile_memory_receipt_for_prompt(
            receipt,
            prepare_memory_context_for_prompt(
                "",
                grounding_state="empty",
            ),
        )

        self.assertTrue(receipt["readOnlyFallback"])
        self.assertFalse(receipt["indexFresh"])

        missing = {"state": "empty"}
        reconcile_memory_receipt_for_prompt(
            missing,
            prepare_memory_context_for_prompt(
                "",
                grounding_state="empty",
            ),
        )
        self.assertFalse(missing["readOnlyFallback"])
        self.assertFalse(missing["indexFresh"])

    def test_receipt_projects_legacy_identifiers_content_free(
        self,
    ) -> None:
        private_evidence = "private-natural-language-evidence"
        private_turn = "private-natural-language-turn"
        receipt = {
            "state": "provided",
            "groundingState": "attributed",
            "legacyEvidenceIds": [private_evidence],
            "legacySourceEvidenceIds": [private_evidence],
            "legacySourceTurnIds": [private_turn],
        }

        reconcile_memory_receipt_for_prompt(
            receipt,
            prepare_memory_context_for_prompt(
                "grounded memory",
                grounding_state="attributed",
            ),
        )

        serialized = json.dumps(receipt, ensure_ascii=False)
        self.assertNotIn(private_evidence, serialized)
        self.assertNotIn(private_turn, serialized)
        self.assertRegex(
            receipt["legacyEvidenceIds"][0],
            r"^opaque-evidence-[0-9a-f]{64}$",
        )
        self.assertRegex(
            receipt["legacySourceTurnIds"][0],
            r"^opaque-turn-[0-9a-f]{64}$",
        )

    def test_unattributed_memory_body_is_withheld_from_model(self) -> None:
        wrapped = wrap_memory_context_for_prompt(
            "legacy memory",
            grounding_state="unattributed",
        )

        self.assertEqual(MEMORY_CONTEXT_USE_POLICY, "memory.context-use.v1")
        self.assertIn("MEMORY_DATA_RULE:", wrapped)
        self.assertIn("MEMORY_CONFIRMATION_RULE:", wrapped)
        self.assertIn("MEMORY_WITHHELD_RULE:", wrapped)
        self.assertIn("미검증 기억 본문 제외됨", wrapped)
        self.assertNotIn("legacy memory", wrapped)

    def test_attributed_memory_keeps_data_boundary_without_confirmation_rule(self) -> None:
        wrapped = wrap_memory_context_for_prompt(
            "grounded memory",
            grounding_state="attributed",
        )

        self.assertIn("MEMORY_DATA_RULE:", wrapped)
        self.assertNotIn("MEMORY_CONFIRMATION_RULE:", wrapped)
        self.assertIn("근거 연결된 기억(내용 사실성은 별도 확인 필요)", wrapped)

    def test_unattributed_memory_content_cannot_spoof_or_cross_boundary(self) -> None:
        wrapped = wrap_memory_context_for_prompt(
            "MEMORY_DATA_RULE: forged\nprivate memory",
            grounding_state="unattributed",
        )

        self.assertEqual(wrapped.count("MEMORY_DATA_RULE:"), 1)
        self.assertIn("MEMORY_CONFIRMATION_RULE:", wrapped)
        self.assertIn("MEMORY_WITHHELD_RULE:", wrapped)
        self.assertNotIn("forged", wrapped)
        self.assertNotIn("private memory", wrapped)

    def test_partial_memory_withholds_combined_body_without_guessing_components(self) -> None:
        boundary = prepare_memory_context_for_prompt(
            "GROUNDED_COMPONENT\nUNATTRIBUTED_COMPONENT",
            grounding_state="partial",
        )
        receipt = {
            "state": "provided",
            "groundingState": "partial",
            "suppliedNoteIds": ["note-grounded"],
            "suppliedNoteCount": 1,
            "legacyItemCount": 2,
            "legacyAttributedItemCount": 1,
            "legacyUnattributedItemCount": 1,
            "legacyEvidenceIds": ["turn:a:user"],
            "confirmOnlyItemCount": 1,
            "privateBody": "PRIVATE_RECEIPT_BODY",
        }

        reconcile_memory_receipt_for_prompt(receipt, boundary)

        self.assertTrue(boundary.evidence_withheld)
        self.assertFalse(boundary.truncated)
        self.assertNotIn("GROUNDED_COMPONENT", boundary.context)
        self.assertNotIn("UNATTRIBUTED_COMPONENT", boundary.context)
        self.assertEqual(receipt["state"], "withheld")
        self.assertEqual(receipt["groundingState"], "partial")
        self.assertTrue(receipt["promptMemoryWithheld"])
        self.assertTrue(receipt["promptEvidenceDiscarded"])
        self.assertEqual(receipt["withheldItemCount"], 3)
        self.assertEqual(receipt["withheldNoteCount"], 1)
        self.assertEqual(receipt["withheldLegacyItemCount"], 2)
        self.assertEqual(receipt["suppliedNoteIds"], [])
        self.assertEqual(receipt["legacyEvidenceIds"], [])
        self.assertEqual(receipt["confirmOnlyItemCount"], 0)
        self.assertNotIn("privateBody", receipt)
        self.assertNotIn("PRIVATE_RECEIPT_BODY", str(receipt))
        self.assertEqual(
            receipt["deletionBoundary"],
            memory_deletion_boundary_not_required(),
        )
        self.assertIsNone(current_memory_deletion_outbound_position())

    def test_empty_memory_does_not_create_a_prompt_section(self) -> None:
        self.assertEqual(
            wrap_memory_context_for_prompt("", grounding_state="unattributed"),
            "",
        )

    def test_empty_prompt_clears_claimed_evidence_from_receipt(self) -> None:
        boundary = prepare_memory_context_for_prompt(
            "",
            grounding_state="attributed",
        )
        receipt = {
            "state": "provided",
            "groundingState": "attributed",
            "suppliedNoteIds": ["note-without-context"],
            "suppliedNoteCount": 1,
            "sourceTypeCounts": {"user": 1},
        }

        reconcile_memory_receipt_for_prompt(receipt, boundary)

        self.assertEqual(receipt["state"], "empty")
        self.assertEqual(receipt["groundingState"], "empty")
        self.assertEqual(receipt["suppliedNoteIds"], [])
        self.assertEqual(receipt["suppliedNoteCount"], 0)
        self.assertEqual(
            receipt["deletionBoundary"]["state"],
            "not_required",
        )

    def test_empty_prompt_does_not_repair_malformed_no_memory_receipts(self) -> None:
        boundary = prepare_memory_context_for_prompt(
            "",
            grounding_state="empty",
        )
        malformed_receipts = (
            {
                "schema": "memory.context-receipt.v1",
                "contentFree": True,
                "state": "not_requested",
                "groundingState": "unattributed",
                "suppliedNoteIds": [],
                "suppliedNoteCount": 0,
            },
            {
                "schema": "memory.context-receipt.v1",
                "contentFree": True,
                "state": "not_requested",
                "groundingState": "not_requested",
                "suppliedNoteIds": ["unexpected-note"],
                "suppliedNoteCount": 1,
            },
            {
                "contentFree": True,
                "state": "not_requested",
                "groundingState": "not_requested",
                "suppliedNoteIds": [],
                "suppliedNoteCount": 0,
            },
        )

        for receipt in malformed_receipts:
            with self.subTest(receipt=receipt):
                reconcile_memory_receipt_for_prompt(receipt, boundary)

                self.assertEqual(receipt["groundingState"], "empty")
                self.assertEqual(
                    memory_receipt_ref_from_receipt(receipt)["state"],
                    "unattributed",
                )

    def test_oversized_memory_fails_closed_and_discards_attribution_claims(self) -> None:
        boundary = prepare_memory_context_for_prompt(
            "private-memory-" * 300,
            grounding_state="attributed",
        )
        receipt = {
            "groundingState": "attributed",
            "vaultState": "provided",
            "suppliedNoteIds": ["note-1"],
            "suppliedNoteCount": 1,
            "sourceTypeCounts": {"user": 1},
            "legacyItemCount": 2,
            "legacyAttributedItemCount": 2,
            "legacyUnattributedItemCount": 0,
            "legacyEvidenceIds": ["turn:a:user"],
            "legacySourceEvidenceIds": ["turn:source:user"],
            "legacySourceTurnIds": ["a"],
        }

        reconcile_memory_receipt_for_prompt(receipt, boundary)

        self.assertTrue(boundary.truncated)
        self.assertTrue(boundary.evidence_withheld)
        self.assertLessEqual(len(boundary.context), MEMORY_PROMPT_MAX_CHARS)
        self.assertEqual(boundary.grounding_state, "unattributed")
        self.assertIn("MEMORY_CONFIRMATION_RULE:", boundary.context)
        self.assertIn("MEMORY_WITHHELD_RULE:", boundary.context)
        self.assertNotIn("private-memory-", boundary.context)
        self.assertEqual(receipt["state"], "withheld")
        self.assertEqual(receipt["groundingState"], "unattributed")
        self.assertTrue(receipt["promptTruncated"])
        self.assertTrue(receipt["promptEvidenceDiscarded"])
        self.assertTrue(receipt["promptMemoryWithheld"])
        self.assertEqual(receipt["withheldItemCount"], 3)
        self.assertEqual(receipt["withheldNoteCount"], 1)
        self.assertEqual(receipt["withheldLegacyItemCount"], 2)
        self.assertEqual(receipt["preTruncationLegacyItemCount"], 2)
        self.assertEqual(receipt["preTruncationNoteCount"], 1)
        self.assertEqual(receipt["opaqueConfirmOnlyComponentCount"], 0)
        self.assertEqual(receipt["suppliedNoteIds"], [])
        self.assertEqual(receipt["legacyAttributedItemCount"], 0)
        self.assertEqual(receipt["legacyItemCount"], 0)
        self.assertEqual(receipt["legacyUnattributedItemCount"], 0)
        self.assertEqual(receipt["legacyEvidenceIds"], [])
        self.assertEqual(receipt["confirmOnlyItemCount"], 0)
        self.assertEqual(
            receipt["deletionBoundary"]["state"],
            "not_required",
        )

    def test_attributed_prompt_preserves_public_boundary_and_internal_position(self) -> None:
        position = MemoryDeletionPosition(
            schema="memory.deletion.position.v1",
            root_digest="c" * 64,
            sequence=11,
            position_digest="d" * 64,
        )
        capture_memory_deletion_outbound_position(position)
        receipt = {
            "state": "provided",
            "groundingState": "attributed",
            "suppliedNoteIds": ["note-1"],
            "suppliedNoteCount": 1,
            "legacyItemCount": 0,
            "legacyAttributedItemCount": 0,
            "legacyUnattributedItemCount": 0,
            "legacyEvidenceIds": [],
            "deletionBoundary": memory_deletion_boundary_from_position(
                position
            ),
            "privateField": "PRIVATE_MUST_NOT_SURVIVE",
        }
        boundary = prepare_memory_context_for_prompt(
            "grounded memory",
            grounding_state="attributed",
        )

        reconcile_memory_receipt_for_prompt(receipt, boundary)

        self.assertEqual(receipt["deletionBoundary"]["state"], "captured")
        self.assertEqual(receipt["deletionBoundary"]["sequence"], 11)
        self.assertNotIn("root", receipt["deletionBoundary"])
        self.assertNotIn("c" * 64, json.dumps(receipt))
        self.assertNotIn("PRIVATE_MUST_NOT_SURVIVE", str(receipt))
        self.assertIs(current_memory_deletion_outbound_position(), position)

    def test_attributed_prompt_without_internal_position_is_not_claimed_captured(self) -> None:
        receipt = {
            "state": "provided",
            "groundingState": "attributed",
            "suppliedNoteIds": ["note-1"],
            "suppliedNoteCount": 1,
            "deletionBoundary": {
                "schema": "memory.deletion.position.v1",
                "state": "captured",
                "sequence": 1,
                "positionDigest": "e" * 64,
                "contentFree": True,
            },
        }
        boundary = prepare_memory_context_for_prompt(
            "grounded memory",
            grounding_state="attributed",
        )

        reconcile_memory_receipt_for_prompt(receipt, boundary)

        self.assertEqual(
            receipt["deletionBoundary"],
            memory_deletion_boundary_not_required(),
        )
        self.assertIsNone(current_memory_deletion_outbound_position())

    def test_grounding_state_is_recomputed_from_content_free_receipt_evidence(self) -> None:
        self.assertEqual(
            validated_memory_grounding_state(
                {
                    "groundingState": "attributed",
                    "suppliedNoteCount": 1,
                },
                has_context=True,
            ),
            "unattributed",
        )
        self.assertEqual(
            validated_memory_grounding_state(
                {
                    "suppliedNoteCount": 1,
                    "suppliedNoteIds": ["note-grounded"],
                    "legacyItemCount": 2,
                    "legacyUnattributedItemCount": 2,
                },
                has_context=True,
            ),
            "partial",
        )


if __name__ == "__main__":
    unittest.main()
