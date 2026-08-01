from __future__ import annotations

from pathlib import Path
import sys
import unittest


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
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_POSITION_SCHEMA,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
)
from evelyn_core.memory_exposure import MemoryExposurePosition  # noqa: E402
from evelyn_core.reply_memory_boundary import (  # noqa: E402
    validate_reply_memory_boundary,
)


NOTE_A = "concept-0123456789abcdef"
NOTE_B = "concept-fedcba9876543210"


def exposure(
    version: int,
    note_ids: tuple[str, ...],
) -> MemoryExposurePosition:
    return MemoryExposurePosition(
        deletion_position=MemoryDeletionPosition(
            schema=MEMORY_DELETION_POSITION_SCHEMA,
            root_digest="1" * 64,
            sequence=3,
            position_digest="2" * 64,
        ),
        memory_version=version,
        supplied_note_ids=note_ids,
    )


def bound_receipt(
    version: int,
    note_ids: list[str],
) -> dict[str, object]:
    return {
        "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
        "state": "bound",
        "memoryVersion": version,
        "suppliedNoteIds": note_ids,
        "suppliedNoteCount": len(note_ids),
        "contentFree": True,
    }


class ReplyMemoryBoundaryTests(unittest.TestCase):
    def test_valid_bound_and_not_used_boundaries(self) -> None:
        for position, receipt, expected_state in (
            (None, not_used_memory_receipt_ref(), "not_used"),
            (
                exposure(4, ()),
                not_used_memory_receipt_ref(memory_version=4),
                "not_used",
            ),
            (
                exposure(4, (NOTE_A,)),
                bound_receipt(4, [NOTE_A]),
                "bound",
            ),
        ):
            with self.subTest(position=position, expected_state=expected_state):
                validated_position, validated_receipt = (
                    validate_reply_memory_boundary(
                        memory_exposure_position=position,
                        memory_receipt=receipt,
                    )
                )
                self.assertIs(validated_position, position)
                self.assertEqual(validated_receipt["state"], expected_state)

    def test_invalid_or_mismatched_boundaries_fail_closed(self) -> None:
        for position, receipt in (
            (None, None),
            (None, unattributed_memory_receipt_ref()),
            (None, bound_receipt(4, [NOTE_A])),
            (exposure(4, (NOTE_A,)), bound_receipt(5, [NOTE_A])),
            (exposure(4, (NOTE_A,)), bound_receipt(4, [NOTE_B])),
            (
                exposure(4, (NOTE_A,)),
                not_used_memory_receipt_ref(memory_version=4),
            ),
        ):
            with self.subTest(position=position, receipt=receipt):
                with self.assertRaises(MemoryDeletionJournalIntegrityError):
                    validate_reply_memory_boundary(
                        memory_exposure_position=position,
                        memory_receipt=receipt,
                    )


if __name__ == "__main__":
    unittest.main()
