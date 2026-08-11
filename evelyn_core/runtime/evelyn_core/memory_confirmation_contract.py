from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from .memory_content_free_ids import (
    memory_content_free_source_ref_is_canonical,
)
from .memory_deletion_journal import (
    memory_deletion_note_id_is_canonical,
)


MEMORY_USER_CONFIRMATION_SCHEMA = "memory.user-confirmation.v1"
MEMORY_USER_CONFIRMATION_NOTE_SCHEMA = (
    "memory.user-confirmation.note.v2"
)
MEMORY_OWNER_SCOPE_SCHEMA = "memory.owner-scope.v1"
MEMORY_USER_CONFIRMATION_SOURCES = frozenset(
    {"control-page-user", "discord-user"}
)
MEMORY_USER_CONFIRMATION_TAG = "user-confirmed"
MEMORY_USER_EDIT_SOURCE = "user-edit"
MEMORY_USER_EDIT_SOURCE_REF = "control-page-memory-editor"
_SOURCE_REF_RE = re.compile(
    r"^turn:[A-Za-z0-9._:-]{8,120}:user$"
)
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_EVIDENCE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNER_SCOPE_RE = re.compile(r"^memory-owner-[0-9a-f]{64}$")
_SUCCESS_KEYS = frozenset(
    {
        "schema",
        "state",
        "noteId",
        "sourceRef",
        "confirmedAt",
        "contentFree",
    }
)
_FAILURE_KEYS = frozenset(
    {"schema", "state", "error", "contentFree"}
)


def _valid_iso_datetime(value: object) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
    )


def is_explicit_memory_confirmation_receipt(
    value: object,
) -> bool:
    if not isinstance(value, dict):
        return False
    if (
        value.get("schema") != MEMORY_USER_CONFIRMATION_SCHEMA
        or value.get("contentFree") is not True
    ):
        return False
    state = value.get("state")
    keys = frozenset(value)
    if state in {"stored", "duplicate"}:
        return (
            keys == _SUCCESS_KEYS
            and memory_deletion_note_id_is_canonical(
                value.get("noteId")
            )
            and memory_content_free_source_ref_is_canonical(
                value.get("sourceRef")
            )
            and _valid_iso_datetime(value.get("confirmedAt"))
        )
    if state in {"rejected", "failed"}:
        return (
            keys == _FAILURE_KEYS
            and _ERROR_CODE_RE.fullmatch(
                str(value.get("error") or "")
            )
            is not None
        )
    return False


def _clean(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_values(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [
        cleaned
        for item in value
        if (cleaned := _clean(item))
    ]


def memory_owner_scope(
    *,
    guild_id: object | None,
    person_key: object,
) -> str:
    normalized_person_key = _clean(person_key)
    if not normalized_person_key or len(normalized_person_key) > 256:
        raise ValueError("memory_owner_scope_invalid")
    if guild_id is None:
        if normalized_person_key != "control-page:local":
            raise ValueError("memory_owner_scope_invalid")
        normalized_guild_id = None
    else:
        if isinstance(guild_id, bool):
            raise ValueError("memory_owner_scope_invalid")
        try:
            normalized_guild_id = int(guild_id)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("memory_owner_scope_invalid") from None
        if normalized_guild_id <= 0:
            raise ValueError("memory_owner_scope_invalid")
    payload = json.dumps(
        {
            "guildId": normalized_guild_id,
            "personKey": normalized_person_key,
            "schema": MEMORY_OWNER_SCOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "memory-owner-" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def memory_owner_scope_is_canonical(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and _clean(value) == value
        and _OWNER_SCOPE_RE.fullmatch(value)
    )


def _user_edit_evidence_hash(*, title: str, body: str) -> str:
    payload = json.dumps(
        {"body": body, "title": title},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_user_edit_body(value: object) -> str:
    normalized = str(value or "").replace(
        "\r\n",
        "\n",
    ).replace("\r", "\n")
    normalized = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+",
        " ",
        normalized,
    )
    return "\n".join(
        line.rstrip()
        for line in normalized.splitlines()
    ).strip()


def is_user_confirmed_memory_integrity_valid(
    *,
    title: object,
    body: object,
    source: object,
    source_type: object,
    source_refs: object,
    evidence_hashes: object,
    confirmed_at: object,
    owner_scope: object,
) -> bool:
    normalized_title = _clean(title)
    normalized_body = _clean(body)
    normalized_edit_body = _normalize_user_edit_body(body)
    normalized_source = _clean(source).lower()
    normalized_source_type = _clean(source_type).lower()
    normalized_refs = _clean_values(source_refs)
    normalized_hashes = [
        value.lower()
        for value in _clean_values(evidence_hashes)
    ]
    if (
        not normalized_body
        or normalized_source_type != "user"
        or len(normalized_refs) != 1
        or len(normalized_hashes) != 1
        or _EVIDENCE_HASH_RE.fullmatch(
            normalized_hashes[0]
        )
        is None
        or not _valid_iso_datetime(confirmed_at)
        or not memory_owner_scope_is_canonical(owner_scope)
    ):
        return False
    if normalized_source in MEMORY_USER_CONFIRMATION_SOURCES:
        return bool(
            _SOURCE_REF_RE.fullmatch(normalized_refs[0])
            and normalized_hashes[0]
            == hashlib.sha256(
                normalized_body.encode("utf-8")
            ).hexdigest()
        )
    if normalized_source == MEMORY_USER_EDIT_SOURCE:
        return bool(
            normalized_title
            and normalized_refs
            == [MEMORY_USER_EDIT_SOURCE_REF]
            and normalized_hashes[0]
            == _user_edit_evidence_hash(
                title=normalized_title,
                body=normalized_edit_body,
            )
        )
    return False


def explicit_memory_writer_skip_decision() -> dict[str, Any]:
    return {
        "write_raw_transcript": False,
        "update_conversation_summary": False,
        "update_runtime_state": False,
        "store_long_term_memory": False,
        "store_open_questions": False,
        "store_minecraft_failure": False,
        "reason": "explicit_user_confirmation",
    }


__all__ = [
    "MEMORY_USER_CONFIRMATION_NOTE_SCHEMA",
    "MEMORY_OWNER_SCOPE_SCHEMA",
    "MEMORY_USER_CONFIRMATION_SCHEMA",
    "MEMORY_USER_CONFIRMATION_SOURCES",
    "MEMORY_USER_CONFIRMATION_TAG",
    "MEMORY_USER_EDIT_SOURCE",
    "MEMORY_USER_EDIT_SOURCE_REF",
    "explicit_memory_writer_skip_decision",
    "is_explicit_memory_confirmation_receipt",
    "is_user_confirmed_memory_integrity_valid",
    "memory_owner_scope",
    "memory_owner_scope_is_canonical",
]
