from __future__ import annotations

import re
from datetime import datetime
from typing import Any


MEMORY_USER_CONFIRMATION_SCHEMA = "memory.user-confirmation.v1"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SOURCE_REF_RE = re.compile(
    r"^turn:[A-Za-z0-9._:-]{8,120}:user$"
)
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{1,80}$")
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
            and _SAFE_ID_RE.fullmatch(
                str(value.get("noteId") or "")
            )
            is not None
            and _SOURCE_REF_RE.fullmatch(
                str(value.get("sourceRef") or "")
            )
            is not None
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
    "MEMORY_USER_CONFIRMATION_SCHEMA",
    "explicit_memory_writer_skip_decision",
    "is_explicit_memory_confirmation_receipt",
]
