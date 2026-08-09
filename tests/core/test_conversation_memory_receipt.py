from __future__ import annotations

import sys
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

from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
    capture_conversation_memory_receipt_ref,
    current_conversation_memory_receipt_ref,
    memory_receipt_ref_from_metrics,
    memory_receipt_ref_from_receipt,
    merge_memory_receipt_refs,
    reset_conversation_memory_receipt_ref,
    sanitize_memory_receipt_ref,
)


NOTE_A = "concept-0123456789abcdef"
NOTE_B = "concept-fedcba9876543210"


def full_receipt(*note_ids: str) -> dict:
    return {
        "schema": "memory.context-receipt.v1",
        "state": "provided",
        "groundingState": "attributed",
        "memoryVersion": 7,
        "suppliedNoteIds": list(note_ids),
        "suppliedNoteCount": len(note_ids),
        "contentFree": True,
    }


class ConversationMemoryReceiptTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_conversation_memory_receipt_ref()

    def test_full_receipt_compacts_to_exact_bound_ref(self) -> None:
        receipt_ref = memory_receipt_ref_from_receipt(
            full_receipt(NOTE_B, NOTE_A)
        )

        self.assertEqual(
            receipt_ref,
            {
                "schema": (
                    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA
                ),
                "state": "bound",
                "memoryVersion": 7,
                "suppliedNoteIds": [NOTE_A, NOTE_B],
                "suppliedNoteCount": 2,
                "contentFree": True,
            },
        )

    def test_provided_unbound_and_unused_receipts_are_distinct(self) -> None:
        unbound = full_receipt()
        unbound["groundingState"] = "unattributed"
        unused = {
            "schema": "memory.context-receipt.v1",
            "state": "empty",
            "memoryVersion": 4,
            "contentFree": True,
        }

        self.assertEqual(
            memory_receipt_ref_from_receipt(unbound)["state"],
            "unattributed",
        )
        unused_ref = memory_receipt_ref_from_receipt(unused)
        self.assertEqual(unused_ref["state"], "not_used")
        self.assertEqual(unused_ref["memoryVersion"], 4)
        self.assertEqual(unused_ref["suppliedNoteIds"], [])

    def test_contradictory_unused_receipt_fails_closed(self) -> None:
        contradictory = {
            "schema": "memory.context-receipt.v1",
            "state": "empty",
            "groundingState": "empty",
            "memoryVersion": 4,
            "suppliedNoteIds": [NOTE_A],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }

        receipt_ref = memory_receipt_ref_from_receipt(contradictory)

        self.assertEqual(receipt_ref["state"], "unattributed")
        self.assertEqual(receipt_ref["memoryVersion"], 4)
        self.assertEqual(receipt_ref["suppliedNoteIds"], [])

    def test_missing_and_unavailable_receipts_remain_not_used(self) -> None:
        unavailable = {
            "schema": "memory.context-receipt.v1",
            "state": "unavailable",
            "groundingState": "empty",
            "contentFree": True,
        }

        self.assertEqual(
            memory_receipt_ref_from_receipt(None)["state"],
            "not_used",
        )
        self.assertEqual(
            memory_receipt_ref_from_receipt(unavailable)["state"],
            "not_used",
        )
        self.assertEqual(
            memory_receipt_ref_from_receipt("malformed")["state"],
            "unattributed",
        )

    def test_unhashable_receipt_state_fails_closed(self) -> None:
        for state in ([], {}):
            with self.subTest(state=state):
                malformed_ref = {
                    "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
                    "state": state,
                    "memoryVersion": 0,
                    "suppliedNoteIds": [],
                    "suppliedNoteCount": 0,
                    "contentFree": True,
                }
                self.assertIsNone(
                    sanitize_memory_receipt_ref(malformed_ref)
                )
                receipt_ref = memory_receipt_ref_from_receipt(malformed_ref)
                self.assertEqual(
                    receipt_ref["state"],
                    "unattributed",
                )

    def test_metrics_missing_receipt_fails_closed(self) -> None:
        for context_pipeline in ({}, {"memory_receipt": None}):
            with self.subTest(context_pipeline=context_pipeline):
                receipt_ref = memory_receipt_ref_from_metrics(
                    {"meta": {"context_pipeline": context_pipeline}}
                )
                self.assertEqual(
                    receipt_ref["state"],
                    "unattributed",
                )

    def test_legacy_dependency_signals_fail_closed(self) -> None:
        mixed = {
            **full_receipt(NOTE_A),
            "legacyItemCount": 1,
            "legacyAttributedItemCount": 1,
            "legacyEvidenceIds": ["turn:a:user"],
        }
        legacy_only = {
            **full_receipt(),
            "legacyAttributedItemCount": 1,
            "legacyEvidenceIds": ["turn:b:user"],
        }
        future_legacy_coordinate = {
            **full_receipt(NOTE_A),
            "suppliedLegacyItemIds": ["legacy:item:1"],
        }

        for receipt in (
            mixed,
            legacy_only,
            future_legacy_coordinate,
        ):
            with self.subTest(receipt=receipt):
                receipt_ref = memory_receipt_ref_from_receipt(
                    receipt
                )
                self.assertEqual(
                    receipt_ref["state"],
                    "unattributed",
                )
                self.assertEqual(
                    receipt_ref["suppliedNoteIds"],
                    [],
                )
                self.assertEqual(
                    receipt_ref["suppliedNoteCount"],
                    0,
                )

    def test_empty_legacy_receipt_fields_do_not_block_vault_binding(
        self,
    ) -> None:
        receipt = {
            **full_receipt(NOTE_A),
            "legacyItemCount": 0,
            "legacyAttributedItemCount": 0,
            "legacyItemCounts": {},
            "legacyEvidenceIds": [],
            "legacySourceEvidenceIds": [],
            "legacySourceTurnIds": [],
        }

        receipt_ref = memory_receipt_ref_from_receipt(receipt)

        self.assertEqual(receipt_ref["state"], "bound")
        self.assertEqual(
            receipt_ref["suppliedNoteIds"],
            [NOTE_A],
        )

    def test_sanitizer_rejects_extra_fields_and_inexact_counts(self) -> None:
        valid = memory_receipt_ref_from_receipt(
            full_receipt(NOTE_A)
        )
        extra = {**valid, "privateText": "must not persist"}
        wrong_count = {**valid, "suppliedNoteCount": 0}
        unsorted = memory_receipt_ref_from_receipt(
            full_receipt(NOTE_A, NOTE_B)
        )
        unsorted["suppliedNoteIds"] = list(
            reversed(unsorted["suppliedNoteIds"])
        )

        self.assertIsNone(sanitize_memory_receipt_ref(extra))
        self.assertEqual(
            memory_receipt_ref_from_receipt(extra)["state"],
            "unattributed",
        )
        self.assertIsNone(
            sanitize_memory_receipt_ref(wrong_count)
        )
        self.assertIsNone(sanitize_memory_receipt_ref(unsorted))

    def test_dedupe_merge_preserves_and_unions_bound_dependencies(self) -> None:
        bound_a = memory_receipt_ref_from_receipt(
            full_receipt(NOTE_A)
        )
        bound_b = memory_receipt_ref_from_receipt(
            full_receipt(NOTE_B)
        )
        not_used = memory_receipt_ref_from_receipt(None)

        merged_with_unbound = merge_memory_receipt_refs(
            not_used,
            bound_a,
        )
        merged_bound = merge_memory_receipt_refs(
            bound_b,
            bound_a,
        )

        self.assertEqual(merged_with_unbound, bound_a)
        self.assertEqual(
            merged_bound["suppliedNoteIds"],
            [NOTE_A, NOTE_B],
        )
        self.assertEqual(merged_bound["state"], "bound")

    def test_dedupe_merge_fails_closed_for_unknown_or_mixed_versions(
        self,
    ) -> None:
        bound_a = memory_receipt_ref_from_receipt(
            full_receipt(NOTE_A)
        )
        bound_b = memory_receipt_ref_from_receipt(
            {
                **full_receipt(NOTE_B),
                "memoryVersion": 8,
            }
        )
        unattributed = memory_receipt_ref_from_receipt(
            {
                **full_receipt(),
                "groundingState": "unattributed",
            }
        )

        self.assertIsNone(
            merge_memory_receipt_refs(bound_a, None)
        )
        self.assertEqual(
            merge_memory_receipt_refs(
                bound_a,
                unattributed,
            )["state"],
            "unattributed",
        )
        self.assertEqual(
            merge_memory_receipt_refs(bound_a, bound_b)[
                "state"
            ],
            "unattributed",
        )

    def test_metrics_merges_current_and_conversation_receipts(self) -> None:
        metrics = {
            "meta": {
                "context_pipeline": {
                    "memory_receipt": full_receipt(NOTE_A),
                    "conversation_memory_receipt_ref": (
                        memory_receipt_ref_from_receipt(
                            full_receipt(NOTE_B)
                        )
                    ),
                }
            }
        }

        receipt_ref = memory_receipt_ref_from_metrics(metrics)

        self.assertEqual(receipt_ref["state"], "bound")
        self.assertEqual(
            receipt_ref["suppliedNoteIds"],
            [NOTE_A, NOTE_B],
        )

    def test_response_receipt_context_is_defensive_and_fail_closed(self) -> None:
        reset_conversation_memory_receipt_ref()
        self.assertIsNone(current_conversation_memory_receipt_ref())

        captured = capture_conversation_memory_receipt_ref(
            memory_receipt_ref_from_receipt(full_receipt(NOTE_A))
        )
        captured["suppliedNoteIds"].clear()
        self.assertEqual(
            current_conversation_memory_receipt_ref()["suppliedNoteIds"],
            [NOTE_A],
        )

        invalid = capture_conversation_memory_receipt_ref(
            {"privateText": "must not persist"}
        )
        self.assertEqual(invalid["state"], "unattributed")
        self.assertNotIn("privateText", str(invalid))


if __name__ == "__main__":
    unittest.main()
