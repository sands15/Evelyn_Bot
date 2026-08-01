from __future__ import annotations

from typing import Any

from .conversation_memory_receipt import sanitize_memory_receipt_ref
from .memory_deletion_journal import MemoryDeletionJournalIntegrityError
from .memory_exposure import MemoryExposurePosition


def validate_reply_memory_boundary(
    *,
    memory_exposure_position: MemoryExposurePosition | None,
    memory_receipt: Any,
) -> tuple[MemoryExposurePosition | None, dict[str, Any]]:
    """Bind reply side effects to the exact content-free memory inputs."""

    receipt = sanitize_memory_receipt_ref(memory_receipt)
    if receipt is None or receipt["state"] == "unattributed":
        raise MemoryDeletionJournalIntegrityError()

    exposure = memory_exposure_position
    if exposure is None:
        if receipt["state"] == "bound":
            raise MemoryDeletionJournalIntegrityError()
        return None, receipt
    if not isinstance(exposure, MemoryExposurePosition):
        raise MemoryDeletionJournalIntegrityError()

    receipt_note_ids = tuple(receipt["suppliedNoteIds"])
    if (
        receipt["memoryVersion"] != exposure.memory_version
        or receipt_note_ids != exposure.supplied_note_ids
        or (receipt["state"] == "bound") != bool(receipt_note_ids)
    ):
        raise MemoryDeletionJournalIntegrityError()
    return exposure, receipt


__all__ = ["validate_reply_memory_boundary"]
