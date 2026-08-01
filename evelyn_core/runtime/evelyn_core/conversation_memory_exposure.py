from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .conversation_memory_receipt import (
    memory_receipt_ref_from_receipt,
    merge_memory_receipt_refs,
    sanitize_memory_receipt_ref,
)
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
    memory_deletion_journal_guard,
    read_memory_deletion_tombstones,
)
from .memory_exposure import (
    MemoryExposurePosition,
    capture_memory_exposure_position,
    combine_memory_exposure_positions,
    read_memory_version,
)


@dataclass(frozen=True)
class ConversationMemoryHistoryOutcome:
    """Fail-closed, content-free result of screening persisted dialogue."""

    messages: tuple[dict[str, Any], ...]
    memory_receipt_ref: dict[str, Any]
    memory_exposure_position: MemoryExposurePosition | None
    dropped_missing_receipt_count: int = 0
    dropped_unattributed_count: int = 0
    dropped_stale_version_count: int = 0
    dropped_tombstoned_count: int = 0

    def public_status(self) -> dict[str, Any]:
        return {
            "schema": "conversation.memory-history-filter.v1",
            "keptMessageCount": len(self.messages),
            "droppedMissingReceiptCount": self.dropped_missing_receipt_count,
            "droppedUnattributedCount": self.dropped_unattributed_count,
            "droppedStaleVersionCount": self.dropped_stale_version_count,
            "droppedTombstonedCount": self.dropped_tombstoned_count,
            "memoryBound": self.memory_exposure_position is not None,
            "contentFree": True,
        }


def memory_exposure_position_from_receipt(
    receipt: Any,
    *,
    deletion_position: MemoryDeletionPosition | None,
    required: bool = False,
) -> MemoryExposurePosition | None:
    """Build typed exposure coordinates from an attributed receipt only."""

    receipt_ref = memory_receipt_ref_from_receipt(receipt)
    if receipt_ref["state"] != "bound":
        if required:
            raise MemoryDeletionJournalIntegrityError()
        return None
    if not isinstance(deletion_position, MemoryDeletionPosition):
        raise MemoryDeletionJournalIntegrityError()
    return MemoryExposurePosition(
        deletion_position=deletion_position,
        memory_version=receipt_ref["memoryVersion"],
        supplied_note_ids=tuple(receipt_ref["suppliedNoteIds"]),
    )


def capture_combined_memory_exposure(
    *positions: MemoryExposurePosition | None,
) -> MemoryExposurePosition | None:
    present = tuple(position for position in positions if position is not None)
    if not present:
        return None
    combined = (
        present[0]
        if len(present) == 1
        else combine_memory_exposure_positions(*present)
    )
    return capture_memory_exposure_position(combined)


def memory_receipt_ref_from_exposure(
    position: MemoryExposurePosition | None,
) -> dict[str, Any]:
    if position is None or not position.supplied_note_ids:
        return memory_receipt_ref_from_receipt(None)
    return memory_receipt_ref_from_receipt(
        {
            "schema": "memory.context-receipt.v1",
            "state": "provided",
            "groundingState": "attributed",
            "memoryVersion": position.memory_version,
            "suppliedNoteIds": list(position.supplied_note_ids),
            "suppliedNoteCount": len(position.supplied_note_ids),
            "contentFree": True,
        }
    )


def _assistant_receipt_ref(message: Mapping[str, Any]) -> dict[str, Any] | None:
    if "_memoryReceiptRef" in message:
        return sanitize_memory_receipt_ref(message.get("_memoryReceiptRef"))
    if "memoryReceiptRef" in message:
        return sanitize_memory_receipt_ref(message.get("memoryReceiptRef"))
    # In-process Fast Control messages from the pre-migration shape can still
    # carry the full, content-free receipt. Durable restore never relies on it.
    if "memoryReceipt" in message:
        return memory_receipt_ref_from_receipt(message.get("memoryReceipt"))
    return None


def _tombstoned_note_ids(rows: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(
        str(row.get("noteId"))
        for row in rows
        if isinstance(row.get("noteId"), str)
    )


def filter_conversation_history_for_memory_exposure(
    messages: Iterable[Mapping[str, Any]],
    *,
    memory_index_dir: Path,
) -> ConversationMemoryHistoryOutcome:
    """Remove assistant text whose memory independence cannot be proven.

    User turns and explicitly ``not_used`` assistant turns survive. Legacy or
    invalid assistant rows, unattributed rows, stale versions, and rows bound
    to a tombstoned note are excluded before any prompt or tool sees them.
    """

    candidates: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    dropped_missing = 0
    dropped_unattributed = 0
    for raw_message in messages:
        if not isinstance(raw_message, Mapping):
            continue
        message = dict(raw_message)
        role = str(message.get("role") or "").strip().lower()
        if role != "assistant":
            message.pop("memoryReceipt", None)
            message.pop("memoryReceiptRef", None)
            message.pop("_memoryReceiptRef", None)
            candidates.append((message, None))
            continue
        receipt_ref = _assistant_receipt_ref(message)
        if receipt_ref is None:
            dropped_missing += 1
            continue
        if receipt_ref["state"] == "unattributed":
            dropped_unattributed += 1
            continue
        message.pop("memoryReceipt", None)
        message.pop("_memoryReceiptRef", None)
        message["memoryReceiptRef"] = receipt_ref
        candidates.append((message, receipt_ref))

    bound_refs = [
        receipt_ref
        for _message, receipt_ref in candidates
        if receipt_ref is not None and receipt_ref["state"] == "bound"
    ]
    current_version: int | None = None
    current_position: MemoryDeletionPosition | None = None
    tombstoned_ids: frozenset[str] = frozenset()
    if bound_refs:
        with memory_deletion_journal_guard(
            Path(memory_index_dir),
            require_stable=True,
        ) as current_position:
            current_version = read_memory_version(Path(memory_index_dir))
            tombstoned_ids = _tombstoned_note_ids(
                read_memory_deletion_tombstones(Path(memory_index_dir))
            )

    kept: list[dict[str, Any]] = []
    kept_refs: list[dict[str, Any]] = []
    supplied_note_ids: set[str] = set()
    dropped_stale = 0
    dropped_tombstoned = 0
    for message, receipt_ref in candidates:
        if receipt_ref is None:
            kept.append(message)
            continue
        if receipt_ref["state"] == "bound":
            if receipt_ref["memoryVersion"] != current_version:
                dropped_stale += 1
                continue
            if any(
                note_id in tombstoned_ids
                for note_id in receipt_ref["suppliedNoteIds"]
            ):
                dropped_tombstoned += 1
                continue
            supplied_note_ids.update(receipt_ref["suppliedNoteIds"])
        kept.append(message)
        kept_refs.append(receipt_ref)

    exposure_position: MemoryExposurePosition | None = None
    if supplied_note_ids:
        if current_position is None or current_version is None:
            raise MemoryDeletionJournalIntegrityError()
        exposure_position = MemoryExposurePosition(
            deletion_position=current_position,
            memory_version=current_version,
            supplied_note_ids=tuple(sorted(supplied_note_ids)),
        )

    merged_ref = merge_memory_receipt_refs(*kept_refs)
    if merged_ref is None:
        merged_ref = memory_receipt_ref_from_receipt(None)
    return ConversationMemoryHistoryOutcome(
        messages=tuple(kept),
        memory_receipt_ref=merged_ref,
        memory_exposure_position=exposure_position,
        dropped_missing_receipt_count=dropped_missing,
        dropped_unattributed_count=dropped_unattributed,
        dropped_stale_version_count=dropped_stale,
        dropped_tombstoned_count=dropped_tombstoned,
    )


__all__ = [
    "ConversationMemoryHistoryOutcome",
    "capture_combined_memory_exposure",
    "filter_conversation_history_for_memory_exposure",
    "memory_exposure_position_from_receipt",
    "memory_receipt_ref_from_exposure",
]
