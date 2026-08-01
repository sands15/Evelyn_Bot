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
from .memory_confirmation_contract import (
    MEMORY_USER_CONFIRMATION_NOTE_SCHEMA,
    MEMORY_USER_CONFIRMATION_SCHEMA,
    MEMORY_USER_CONFIRMATION_SOURCES,
    MEMORY_USER_CONFIRMATION_TAG,
    explicit_memory_writer_skip_decision,
    is_explicit_memory_confirmation_receipt,
    is_user_confirmed_memory_integrity_valid,
)
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
    memory_deletion_ledger_note_id,
)
from .memory_content_free_ids import (
    memory_content_free_source_ref,
)
from .text import clean_text


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


def is_explicit_memory_confirmation_command(
    text: object,
) -> bool:
    raw = str(text or "").strip()
    return any(
        pattern.fullmatch(raw) is not None
        for pattern in _COMMAND_PATTERNS
    )


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
        "noteId": memory_deletion_ledger_note_id(note_id),
        "sourceRef": memory_content_free_source_ref(source_ref),
        "confirmedAt": confirmed_at,
        "contentFree": True,
    }


def _verified_receipt(
    *,
    state: str,
    note_id: str,
    source_ref: str,
    confirmed_at: str,
) -> dict[str, Any]:
    receipt = _receipt(
        state=state,
        note_id=note_id,
        source_ref=source_ref,
        confirmed_at=confirmed_at,
    )
    if not is_explicit_memory_confirmation_receipt(receipt):
        raise ExplicitMemoryConfirmationError(
            "memory_confirmation_write_unverified"
        )
    return receipt


def _verified_stored_card(
    card: dict[str, Any],
    *,
    expected_fact: str,
    expected_evidence_hash: str,
    expected_source: str,
    expected_source_ref: str | None = None,
) -> tuple[str, str, str]:
    provenance = dict(card.get("provenance") or {})
    source_refs = [
        clean_text(item)
        for item in (provenance.get("sourceRefs") or [])
        if clean_text(item)
    ]
    evidence_hashes = [
        clean_text(item).lower()
        for item in (provenance.get("evidenceHashes") or [])
        if clean_text(item)
    ]
    note_id = clean_text(card.get("id"))
    confirmed_at = clean_text(card.get("confirmedAt"))
    valid = bool(
        is_user_confirmed_memory_integrity_valid(
            title=card.get("title"),
            body=card.get("body"),
            source=provenance.get("source"),
            source_type=provenance.get("sourceType"),
            source_refs=source_refs,
            evidence_hashes=evidence_hashes,
            confirmed_at=confirmed_at,
        )
        and clean_text(card.get("body")) == expected_fact
        and card.get("confirmed") is True
        and card.get("recallEligible") is True
        and note_id
        and confirmed_at
        and clean_text(provenance.get("source")).lower()
        == expected_source
        and clean_text(provenance.get("sourceType")).lower()
        == "user"
        and len(source_refs) == 1
        and evidence_hashes == [expected_evidence_hash]
        and (
            expected_source_ref is None
            or source_refs == [expected_source_ref]
        )
    )
    if not valid:
        raise ExplicitMemoryConfirmationError(
            "memory_confirmation_write_unverified"
        )
    return note_id, source_refs[0], confirmed_at


def store_explicit_memory_confirmation(
    fact: str,
    *,
    action_id: object = "",
    evidence_turn_id: object | None = None,
    source: str = "control-page-user",
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
    normalized_source = clean_text(source).lower()
    if normalized_source not in MEMORY_USER_CONFIRMATION_SOURCES:
        raise ExplicitMemoryConfirmationError(
            "memory_confirmation_source_invalid"
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
    normalized_evidence_turn_id = (
        _normalized_action_id(evidence_turn_id)
        if evidence_turn_id is not None
        else normalized_action_id
    )
    source_ref = f"turn:{normalized_evidence_turn_id}:user"

    with _write_lock:
        existing = memory_vault_user_note(rel_path, root=root)
        if existing.get("ok"):
            card = dict(existing.get("card") or {})
            if clean_text(card.get("body")) != normalized_fact:
                raise ExplicitMemoryConfirmationError(
                    "memory_confirmation_hash_collision"
                )
            (
                note_id,
                stored_source_ref,
                stored_confirmed_at,
            ) = _verified_stored_card(
                card,
                expected_fact=normalized_fact,
                expected_evidence_hash=evidence_hash,
                expected_source=normalized_source,
            )
            return _verified_receipt(
                state="duplicate",
                note_id=note_id,
                source_ref=stored_source_ref,
                confirmed_at=stored_confirmed_at,
            )

        confirmed_at = _confirmed_at()
        write_memory_vault_note(
            note_type="concept",
            title="사용자 확인 기억",
            body=normalized_fact,
            storage_key=storage_key,
            tags=[MEMORY_USER_CONFIRMATION_TAG],
            projects=["evelyn"],
            source=normalized_source,
            source_refs=[source_ref],
            evidence_hashes=[evidence_hash],
            confirmed_at=confirmed_at,
            memory_contract=(
                MEMORY_USER_CONFIRMATION_NOTE_SCHEMA
            ),
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
        (
            note_id,
            stored_source_ref,
            stored_confirmed_at,
        ) = _verified_stored_card(
            card,
            expected_fact=normalized_fact,
            expected_evidence_hash=evidence_hash,
            expected_source=normalized_source,
            expected_source_ref=source_ref,
        )
        return _verified_receipt(
            state="stored",
            note_id=note_id,
            source_ref=stored_source_ref,
            confirmed_at=stored_confirmed_at,
        )


def execute_explicit_memory_confirmation(
    text: str,
    *,
    action_id: object = "",
    evidence_turn_id: object | None = None,
    source: str = "control-page-user",
) -> tuple[bool, str, dict[str, Any] | None, str]:
    try:
        fact = parse_explicit_memory_confirmation(text)
    except ExplicitMemoryConfirmationError as exc:
        return (
            True,
            "기억할 내용을 함께 적어줘. 예: /remember 나는 산책을 좋아해",
            {
                "schema": MEMORY_USER_CONFIRMATION_SCHEMA,
                "state": "rejected",
                "error": exc.code,
                "contentFree": True,
            },
            exc.code,
        )
    if fact is None:
        return False, "", None, ""
    try:
        receipt = store_explicit_memory_confirmation(
            fact,
            action_id=action_id,
            evidence_turn_id=evidence_turn_id,
            source=source,
        )
        if not is_explicit_memory_confirmation_receipt(
            receipt
        ):
            raise ExplicitMemoryConfirmationError(
                "memory_confirmation_write_unverified"
            )
    except ExplicitMemoryConfirmationError as exc:
        return (
            True,
            "지금은 그 기억을 근거와 함께 저장하지 못했어. 다시 시도해줘.",
            {
                "schema": MEMORY_USER_CONFIRMATION_SCHEMA,
                "state": "failed",
                "error": exc.code,
                "contentFree": True,
            },
            exc.code,
        )
    except MemoryDeletionJournalIntegrityError:
        raise
    except Exception as exc:
        print(
            "[MEMORY CONFIRMATION] write_failed "
            f"errorType={type(exc).__name__}",
            flush=True,
        )
        return (
            True,
            "지금은 그 기억을 근거와 함께 저장하지 못했어. 다시 시도해줘.",
            {
                "schema": MEMORY_USER_CONFIRMATION_SCHEMA,
                "state": "failed",
                "error": "memory_confirmation_write_failed",
                "contentFree": True,
            },
            "memory_confirmation_write_failed",
        )
    state = clean_text(receipt.get("state"))
    reply = (
        "이미 같은 내용이 근거 있는 기억으로 저장되어 있어."
        if state == "duplicate"
        else "지금 요청을 근거로 새 기억에 저장했어."
    )
    return True, reply, receipt, ""


__all__ = [
    "ExplicitMemoryConfirmationError",
    "MEMORY_USER_CONFIRMATION_MAX_CHARS",
    "MEMORY_USER_CONFIRMATION_SOURCES",
    "MEMORY_USER_CONFIRMATION_SCHEMA",
    "execute_explicit_memory_confirmation",
    "explicit_memory_writer_skip_decision",
    "is_explicit_memory_confirmation_command",
    "is_explicit_memory_confirmation_receipt",
    "parse_explicit_memory_confirmation",
    "store_explicit_memory_confirmation",
]
