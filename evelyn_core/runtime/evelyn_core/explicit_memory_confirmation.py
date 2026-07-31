from __future__ import annotations

import hashlib
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory_vault import (
    memory_vault_user_note,
    write_memory_vault_note,
)
from .text import clean_text


MEMORY_USER_CONFIRMATION_SCHEMA = "memory.user-confirmation.v1"
MEMORY_USER_CONFIRMATION_MAX_CHARS = 2_000
_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,120}$")
_COMMAND_PATTERNS = (
    re.compile(r"^/remember(?:\s+(.*))?$", re.IGNORECASE),
    re.compile(r"^/memory\s+remember(?:\s+(.*))?$", re.IGNORECASE),
    re.compile(r"^기억해\s*줘\s*:\s*(.*)$"),
    re.compile(r"^기억해\s*:\s*(.*)$"),
)
_write_lock = threading.RLock()


class ExplicitMemoryConfirmationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def parse_explicit_memory_confirmation(text: object) -> str | None:
    raw = str(text or "").strip()
    for pattern in _COMMAND_PATTERNS:
        match = pattern.fullmatch(raw)
        if match is None:
            continue
        fact = clean_text(match.group(1) or "")
        if not fact:
            raise ExplicitMemoryConfirmationError(
                "memory_confirmation_text_required"
            )
        if len(fact) > MEMORY_USER_CONFIRMATION_MAX_CHARS:
            raise ExplicitMemoryConfirmationError(
                "memory_confirmation_text_too_long"
            )
        return fact
    return None


def _confirmed_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_action_id(value: object) -> str:
    candidate = clean_text(value)
    if _ACTION_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _receipt(
    *,
    state: str,
    note_id: str,
    source_ref: str,
    confirmed_at: str,
) -> dict[str, Any]:
    return {
        "schema": MEMORY_USER_CONFIRMATION_SCHEMA,
        "state": state,
        "noteId": note_id,
        "sourceRef": source_ref,
        "confirmedAt": confirmed_at,
        "contentFree": True,
    }


def store_explicit_memory_confirmation(
    fact: str,
    *,
    action_id: object = "",
    root: Path | None = None,
) -> dict[str, Any]:
    normalized_fact = clean_text(fact)
    if not normalized_fact:
        raise ExplicitMemoryConfirmationError(
            "memory_confirmation_text_required"
        )
    if len(normalized_fact) > MEMORY_USER_CONFIRMATION_MAX_CHARS:
        raise ExplicitMemoryConfirmationError(
            "memory_confirmation_text_too_long"
        )

    evidence_hash = hashlib.sha256(
        normalized_fact.encode("utf-8")
    ).hexdigest()
    normalized_action_id = _normalized_action_id(action_id)
    action_fingerprint = hashlib.sha256(
        normalized_action_id.encode("utf-8")
    ).hexdigest()[:24]
    storage_key = f"user-confirmed-{action_fingerprint}"
    rel_path = f"concepts/{storage_key}.md"
    source_ref = f"turn:{normalized_action_id}:user"

    with _write_lock:
        existing = memory_vault_user_note(rel_path, root=root)
        if existing.get("ok"):
            card = dict(existing.get("card") or {})
            if clean_text(card.get("body")) != normalized_fact:
                raise ExplicitMemoryConfirmationError(
                    "memory_confirmation_hash_collision"
                )
            return _receipt(
                state="duplicate",
                note_id=clean_text(card.get("id")),
                source_ref=source_ref,
                confirmed_at=clean_text(card.get("confirmedAt")),
            )

        confirmed_at = _confirmed_at()
        write_memory_vault_note(
            note_type="concept",
            title="사용자 확인 기억",
            body=normalized_fact,
            storage_key=storage_key,
            tags=["user-confirmed"],
            projects=["evelyn"],
            source="control-page-user",
            source_refs=[source_ref],
            evidence_hashes=[evidence_hash],
            confirmed_at=confirmed_at,
            importance=0.75,
            confidence="high",
            root=root,
        )
        stored = memory_vault_user_note(rel_path, root=root)
        if not stored.get("ok"):
            raise ExplicitMemoryConfirmationError(
                "memory_confirmation_write_unverified"
            )
        card = dict(stored.get("card") or {})
        if clean_text(card.get("body")) != normalized_fact:
            raise ExplicitMemoryConfirmationError(
                "memory_confirmation_write_unverified"
            )
        return _receipt(
            state="stored",
            note_id=clean_text(card.get("id")),
            source_ref=source_ref,
            confirmed_at=clean_text(card.get("confirmedAt")) or confirmed_at,
        )


__all__ = [
    "ExplicitMemoryConfirmationError",
    "MEMORY_USER_CONFIRMATION_MAX_CHARS",
    "MEMORY_USER_CONFIRMATION_SCHEMA",
    "parse_explicit_memory_confirmation",
    "store_explicit_memory_confirmation",
]
