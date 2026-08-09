from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping

from .memory_deletion_journal import (
    memory_deletion_note_id_is_canonical,
)


CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA = (
    "conversation.memory-receipt-ref.v1"
)
MEMORY_CONTEXT_RECEIPT_SCHEMA = "memory.context-receipt.v1"
MEMORY_RECEIPT_REF_STATES = frozenset(
    {"bound", "not_used", "unattributed"}
)
MAX_MEMORY_RECEIPT_NOTE_IDS = 12

_REF_KEYS = frozenset(
    {
        "schema",
        "state",
        "memoryVersion",
        "suppliedNoteIds",
        "suppliedNoteCount",
        "contentFree",
    }
)

_conversation_memory_receipt_ref: ContextVar[
    dict[str, Any] | None
] = ContextVar(
    "conversation_memory_receipt_ref",
    default=None,
)


def _memory_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value >= 0 else 0


def _ref(
    state: str,
    *,
    memory_version: int = 0,
    supplied_note_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    note_ids = list(supplied_note_ids)
    return {
        "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
        "state": state,
        "memoryVersion": _memory_version(memory_version),
        "suppliedNoteIds": note_ids,
        "suppliedNoteCount": len(note_ids),
        "contentFree": True,
    }


def not_used_memory_receipt_ref(
    *,
    memory_version: int = 0,
) -> dict[str, Any]:
    return _ref(
        "not_used",
        memory_version=memory_version,
    )


def unattributed_memory_receipt_ref(
    *,
    memory_version: int = 0,
) -> dict[str, Any]:
    return _ref(
        "unattributed",
        memory_version=memory_version,
    )


def reset_conversation_memory_receipt_ref() -> None:
    """Clear the response receipt carried by the current async context."""

    _conversation_memory_receipt_ref.set(None)


def capture_conversation_memory_receipt_ref(
    value: Any,
) -> dict[str, Any]:
    """Capture one strict response receipt, failing closed on invalid input."""

    receipt_ref = sanitize_memory_receipt_ref(value)
    if receipt_ref is None:
        receipt_ref = unattributed_memory_receipt_ref()
    _conversation_memory_receipt_ref.set(receipt_ref)
    captured_copy = sanitize_memory_receipt_ref(receipt_ref)
    if captured_copy is None:
        return unattributed_memory_receipt_ref()
    return captured_copy


def current_conversation_memory_receipt_ref() -> dict[str, Any] | None:
    """Return a defensive copy of the current response receipt, if captured."""

    receipt_ref = _conversation_memory_receipt_ref.get()
    sanitized = sanitize_memory_receipt_ref(receipt_ref)
    return dict(sanitized) if sanitized is not None else None


def _has_legacy_memory_signal(receipt: Mapping[Any, Any]) -> bool:
    """Return whether a full receipt reports any legacy dependency.

    The compact v1 reference can identify vault notes only.  Until legacy
    coordinates have their own typed representation, retaining only the
    vault half of a mixed receipt would incorrectly claim complete
    attribution.  Treat present and future ``legacy`` fields
    conservatively: any non-empty/non-zero value is a dependency signal.
    """

    empty_states = frozenset(
        {
            "",
            "0",
            "false",
            "none",
            "empty",
            "not_requested",
            "not_provided",
            "unavailable",
        }
    )

    def meaningful(value: Any) -> bool:
        if value is None or value is False:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() not in empty_states
        if isinstance(value, Mapping):
            return any(meaningful(item) for item in value.values())
        if isinstance(value, (list, tuple, set, frozenset)):
            return any(meaningful(item) for item in value)
        return True

    for raw_key, value in receipt.items():
        if not isinstance(raw_key, str) or "legacy" not in raw_key.lower():
            continue
        if meaningful(value):
            return True
    return False


def sanitize_memory_receipt_ref(
    value: Any,
) -> dict[str, Any] | None:
    """Return a strict content-free receipt ref, or ``None`` when invalid."""

    if not isinstance(value, Mapping) or set(value) != _REF_KEYS:
        return None
    state = value.get("state")
    memory_version = value.get("memoryVersion")
    note_ids = value.get("suppliedNoteIds")
    supplied_count = value.get("suppliedNoteCount")
    if (
        value.get("schema")
        != CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA
        or not isinstance(state, str)
        or state not in MEMORY_RECEIPT_REF_STATES
        or isinstance(memory_version, bool)
        or not isinstance(memory_version, int)
        or memory_version < 0
        or not isinstance(note_ids, list)
        or len(note_ids) > MAX_MEMORY_RECEIPT_NOTE_IDS
        or any(
            not isinstance(note_id, str)
            or not memory_deletion_note_id_is_canonical(note_id)
            for note_id in note_ids
        )
        or note_ids != sorted(set(note_ids))
        or isinstance(supplied_count, bool)
        or not isinstance(supplied_count, int)
        or supplied_count != len(note_ids)
        or value.get("contentFree") is not True
        or (state == "bound") != bool(note_ids)
    ):
        return None
    return _ref(
        str(state),
        memory_version=memory_version,
        supplied_note_ids=note_ids,
    )


def memory_receipt_ref_from_receipt(
    receipt: Any,
) -> dict[str, Any]:
    """Compact a full memory receipt (or validate an existing compact ref)."""

    compact = sanitize_memory_receipt_ref(receipt)
    if compact is not None:
        return compact
    if receipt is None:
        return _ref("not_used")
    if not isinstance(receipt, Mapping):
        return _ref("unattributed")

    memory_version = _memory_version(receipt.get("memoryVersion"))
    if (
        receipt.get("schema")
        == CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA
    ):
        return _ref(
            "unattributed",
            memory_version=memory_version,
        )
    if _has_legacy_memory_signal(receipt):
        return _ref(
            "unattributed",
            memory_version=memory_version,
        )
    state = receipt.get("state")
    if state != "provided":
        grounding_state = receipt.get("groundingState")
        raw_memory_version = receipt.get("memoryVersion")
        raw_note_ids = receipt.get("suppliedNoteIds")
        supplied_count = receipt.get("suppliedNoteCount")
        valid_memory_version = raw_memory_version is None or (
            isinstance(raw_memory_version, int)
            and not isinstance(raw_memory_version, bool)
            and raw_memory_version >= 0
        )
        valid_supplied_count = supplied_count is None or (
            isinstance(supplied_count, int)
            and not isinstance(supplied_count, bool)
            and supplied_count == 0
        )
        valid_grounding_states = (
            (None, "partial", "unattributed")
            if state == "withheld"
            else (None, "empty", "unavailable")
            if state == "unavailable"
            else (None, state)
        )
        explicitly_not_used = bool(
            receipt.get("schema") == MEMORY_CONTEXT_RECEIPT_SCHEMA
            and receipt.get("contentFree") is True
            and isinstance(state, str)
            and state in {
                "not_requested",
                "empty",
                "unavailable",
                "withheld",
            }
            and grounding_state in valid_grounding_states
            and valid_memory_version
            and raw_note_ids in (None, [])
            and valid_supplied_count
        )
        return _ref(
            "not_used" if explicitly_not_used else "unattributed",
            memory_version=memory_version,
        )

    raw_note_ids = receipt.get("suppliedNoteIds")
    supplied_count = receipt.get("suppliedNoteCount")
    canonical_note_ids: list[str] = []
    note_ids_valid = bool(
        isinstance(raw_note_ids, list)
        and 1 <= len(raw_note_ids) <= MAX_MEMORY_RECEIPT_NOTE_IDS
        and all(
            isinstance(note_id, str)
            and memory_deletion_note_id_is_canonical(note_id)
            for note_id in raw_note_ids
        )
    )
    if note_ids_valid:
        canonical_note_ids = sorted(set(raw_note_ids))
        note_ids_valid = bool(
            isinstance(supplied_count, int)
            and not isinstance(supplied_count, bool)
            and supplied_count == len(canonical_note_ids)
        )
    is_bound = bool(
        receipt.get("schema") == MEMORY_CONTEXT_RECEIPT_SCHEMA
        and receipt.get("contentFree") is True
        and receipt.get("groundingState") == "attributed"
        and isinstance(receipt.get("memoryVersion"), int)
        and not isinstance(receipt.get("memoryVersion"), bool)
        and receipt.get("memoryVersion") >= 0
        and note_ids_valid
    )
    if is_bound:
        return _ref(
            "bound",
            memory_version=memory_version,
            supplied_note_ids=canonical_note_ids,
        )
    return _ref(
        "unattributed",
        memory_version=memory_version,
    )


def memory_receipt_ref_from_metrics(
    metrics: Any,
) -> dict[str, Any]:
    """Extract and compact the LLM context receipt carried by turn metrics."""

    if not isinstance(metrics, Mapping):
        return unattributed_memory_receipt_ref()
    meta = metrics.get("meta")
    if not isinstance(meta, Mapping):
        return unattributed_memory_receipt_ref()
    context_pipeline = meta.get("context_pipeline")
    if not isinstance(context_pipeline, Mapping):
        return unattributed_memory_receipt_ref()
    raw_current_receipt = context_pipeline.get("memory_receipt")
    current_ref = (
        memory_receipt_ref_from_receipt(raw_current_receipt)
        if raw_current_receipt is not None
        else unattributed_memory_receipt_ref()
    )
    if "conversation_memory_receipt_ref" not in context_pipeline:
        return current_ref
    history_ref = sanitize_memory_receipt_ref(
        context_pipeline.get(
            "conversation_memory_receipt_ref"
        )
    )
    if history_ref is None:
        history_ref = _ref(
            "unattributed",
            memory_version=current_ref["memoryVersion"],
        )
    return (
        merge_memory_receipt_refs(current_ref, history_ref)
        or current_ref
    )


def merge_memory_receipt_refs(
    *values: Any,
) -> dict[str, Any] | None:
    """Conservatively merge receipt refs for adjacent duplicate messages."""

    refs: list[dict[str, Any]] = []
    saw_missing = False
    for value in values:
        if value is None:
            saw_missing = True
            continue
        sanitized = sanitize_memory_receipt_ref(value)
        if sanitized is None:
            continue
        refs.append(sanitized)
    if not refs:
        return None

    memory_version = max(
        ref["memoryVersion"] for ref in refs
    )
    if saw_missing:
        return None
    if any(ref["state"] == "unattributed" for ref in refs):
        return _ref(
            "unattributed",
            memory_version=memory_version,
        )
    bound_refs = [
        ref for ref in refs if ref["state"] == "bound"
    ]
    bound_versions = {
        ref["memoryVersion"] for ref in bound_refs
    }
    if len(bound_versions) > 1:
        return _ref(
            "unattributed",
            memory_version=memory_version,
        )
    bound_ids = sorted(
        {
            note_id
            for ref in bound_refs
            for note_id in ref["suppliedNoteIds"]
        }
    )
    if bound_ids:
        if len(bound_ids) > MAX_MEMORY_RECEIPT_NOTE_IDS:
            return _ref(
                "unattributed",
                memory_version=memory_version,
            )
        return _ref(
            "bound",
            memory_version=next(iter(bound_versions)),
            supplied_note_ids=bound_ids,
        )
    return _ref(
        "not_used",
        memory_version=memory_version,
    )


__all__ = [
    "CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA",
    "MAX_MEMORY_RECEIPT_NOTE_IDS",
    "MEMORY_CONTEXT_RECEIPT_SCHEMA",
    "MEMORY_RECEIPT_REF_STATES",
    "capture_conversation_memory_receipt_ref",
    "current_conversation_memory_receipt_ref",
    "memory_receipt_ref_from_metrics",
    "memory_receipt_ref_from_receipt",
    "merge_memory_receipt_refs",
    "not_used_memory_receipt_ref",
    "reset_conversation_memory_receipt_ref",
    "sanitize_memory_receipt_ref",
    "unattributed_memory_receipt_ref",
]
