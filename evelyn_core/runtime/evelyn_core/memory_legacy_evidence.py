from __future__ import annotations

import re
from typing import Any

from .text import clean_text


MEMORY_LEGACY_EVIDENCE_SCHEMA = "memory.legacy-evidence.v1"
_MEMORY_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")


def validate_legacy_memory_evidence(
    row: dict[str, Any],
    *,
    expected_kind: str,
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    evidence_kind = clean_text(str(row.get("evidence_kind") or ""))
    if evidence_kind != expected_kind:
        return None
    evidence_id = clean_text(str(row.get("evidence_id") or ""))[:120]
    if not _MEMORY_EVIDENCE_ID_RE.fullmatch(evidence_id):
        return None
    if evidence_kind == "conversation_turn":
        source_turn_id = clean_text(str(row.get("source_turn_id") or ""))[:80]
        if not _MEMORY_EVIDENCE_ID_RE.fullmatch(source_turn_id):
            return None
        role = clean_text(str(row.get("role") or "user")).lower()
        if role not in {"user", "assistant"}:
            return None
        if evidence_id != f"turn:{source_turn_id}:{role}":
            return None
        return evidence_id, (), (source_turn_id,)
    if evidence_kind not in {
        "derived_summary",
        "derived_fact",
        "derived_question",
    }:
        return None
    source_evidence_ids = row.get("source_evidence_ids")
    if not isinstance(source_evidence_ids, (list, tuple)):
        return None
    cleaned_source_evidence_ids = tuple(
        dict.fromkeys(
            cleaned
            for item in source_evidence_ids[:64]
            if _MEMORY_EVIDENCE_ID_RE.fullmatch(
                (cleaned := clean_text(str(item))[:120])
            )
        )
    )
    if not cleaned_source_evidence_ids:
        return None
    source_turn_ids = (
        tuple(
            dict.fromkeys(
                cleaned
                for item in (row.get("source_turn_ids") or [])[:32]
                if _MEMORY_EVIDENCE_ID_RE.fullmatch(
                    (cleaned := clean_text(str(item))[:80])
                )
            )
        )
        if isinstance(row.get("source_turn_ids"), (list, tuple))
        else ()
    )
    return evidence_id, cleaned_source_evidence_ids, source_turn_ids


__all__ = [
    "MEMORY_LEGACY_EVIDENCE_SCHEMA",
    "validate_legacy_memory_evidence",
]
