from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .memory_integrity_authenticity import (
    MEMORY_INTEGRITY_ALGORITHM,
    MemoryIntegrityAuthenticity,
    MemoryIntegrityAuthenticityError,
    load_memory_integrity_authenticity,
)
from .paths import get_repo_root
from .runtime_artifact_io import (
    DurableCommitError,
    atomic_json_write,
    atomic_text_write,
)


MEMORY_DELETE_TOMBSTONE_V1_SCHEMA = "memory.deletion.tombstone.v1"
MEMORY_DELETE_TOMBSTONE_V2_SCHEMA = "memory.deletion.tombstone.v2"
MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_SCHEMA = (
    "memory.deletion.chain-head.v1"
)
MEMORY_DELETE_TOMBSTONE_SIGNED_CHAIN_HEAD_SCHEMA = (
    "memory.deletion.chain-head.v2"
)
MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_SCHEMA = (
    "memory.deletion.external-anchor.v1"
)
MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_SCHEMA = (
    "memory.deletion.external-initialization.v1"
)
MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME = "memory_deletions.jsonl"
MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME = (
    "memory_deletions_chain_head.json"
)
MEMORY_DELETE_TOMBSTONE_WRITER_LOCK_NAME = (
    ".memory_deletions_writer.lock"
)
MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME = (
    "memory-deletions.json"
)
MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME = (
    "memory-deletions.initialized.json"
)
MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR = (
    "memory_deletion_journal_integrity_failed"
)
MEMORY_DELETION_JOURNAL_BUSY_ERROR = "memory_deletion_journal_busy"
MEMORY_DELETION_POSITION_SCHEMA = "memory.deletion.position.v1"
MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS = "0" * 64
MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE = "memory.deletion-journal"
MEMORY_DELETE_TOMBSTONE_MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MEMORY_DELETE_TOMBSTONE_MAX_RECORD_BYTES = 16 * 1024
MEMORY_DELETE_TOMBSTONE_MAX_HEAD_BYTES = 128 * 1024
MEMORY_DELETE_TOMBSTONE_ALLOWED_NOTE_TYPES = frozenset(
    {
        "concept",
        "core",
        "daily",
        "episode",
        "internal",
        "legacy",
        "procedure",
        "project",
        "unknown",
    }
)
MEMORY_DELETE_TOMBSTONE_ALLOWED_SOURCE_TYPES = frozenset(
    {
        "conversation",
        "derived",
        "legacy",
        "runtime",
        "system",
        "unknown",
        "user",
    }
)

_LEGACY_ANCHOR_DOMAIN = b"evelyn.memory.deletion.legacy-prefix.v1\n"
_HEAD_AUTH_DOMAIN = b"evelyn.memory.deletion.chain-head.v2\n"
_EXTERNAL_ANCHOR_AUTH_DOMAIN = (
    b"evelyn.memory.deletion.external-anchor.v1\n"
)
_EXTERNAL_INITIALIZATION_AUTH_DOMAIN = (
    b"evelyn.memory.deletion.external-initialization.v1\n"
)
_OPAQUE_NOTE_ID_DOMAIN = b"evelyn.memory.deletion.note-id.v1\n"
_NATIVE_NOTE_ID = re.compile(
    r"(?:"
    r"[0-9a-f]{16}"
    r"|(?:core|daily|episode|concept|procedure|project|internal|legacy|unknown)-[0-9a-f]{16}"
    r"|daily-[0-9]{4}-[0-9]{2}-[0-9]{2}(?:-continuation-(?:0|[1-9][0-9]*))?"
    r"|daily-consolidation-[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|legacy-guild-(?:0|[1-9][0-9]*)"
    r")"
)
_LEDGER_NOTE_ID = re.compile(
    rf"(?:{_NATIVE_NOTE_ID.pattern}|opaque-[0-9a-f]{{64}})"
)
_NOTE_TYPE_ALIASES = {
    "concept": "concept",
    "concepts": "concept",
    "semantic": "concept",
    "core": "core",
    "daily": "daily",
    "episode": "episode",
    "episodes": "episode",
    "procedure": "procedure",
    "procedural": "procedure",
    "procedures": "procedure",
    "project": "project",
    "projects": "project",
    "internal": "internal",
    "system": "internal",
    "debug": "internal",
    "runtime": "internal",
    "tool": "internal",
    "legacy": "legacy",
    "unknown": "unknown",
}
_SOURCE_TYPE_ALIASES = {
    "conversation": "conversation",
    "conversation-turn-log": "conversation",
    "daily-turn-log": "conversation",
    "derived": "derived",
    "legacy": "legacy",
    "runtime": "runtime",
    "system": "system",
    "unknown": "unknown",
    "user": "user",
}
_MAX_IDENTIFIER_CHARS = 512
_MAX_TYPE_CHARS = 128
_MAX_ANCHOR_BYTES = 128 * 1024
_V1_KEYS = {
    "schema",
    "noteId",
    "noteType",
    "sourceType",
    "reason",
    "deletedAt",
}
_V2_KEYS = {
    *_V1_KEYS,
    "contentFree",
    "sequence",
    "previousHash",
    "eventHash",
}
_REVOCATION_KEYS = {"revokedByNoteIds"}
_HEAD_KEYS = {
    "schema",
    "sequence",
    "eventHash",
    "previousHash",
    "legacyPrefixHash",
    "updatedAt",
    "contentFree",
}
_SIGNED_HEAD_KEYS = {
    *_HEAD_KEYS,
    "authAlgorithm",
    "authScope",
    "authKeyId",
    "authTag",
}
_ANCHOR_KEYS = {
    "schema",
    "sequence",
    "eventHash",
    "previousHash",
    "legacyPrefixHash",
    "updatedAt",
    "contentFree",
    "authAlgorithm",
    "authScope",
    "authKeyId",
    "authTag",
}
_INITIALIZATION_KEYS = {
    "schema",
    "initialized",
    "initializedAt",
    "contentFree",
    "authAlgorithm",
    "authScope",
    "authKeyId",
    "authTag",
}
_ALLOWED_REASONS = {
    "user_requested",
    "incorrect_memory",
    "privacy_request",
    "obsolete_memory",
    "test_cleanup",
    "source_revoked",
}

_process_lock = threading.RLock()
_writer_owners: dict[str, tuple[int, int]] = {}
_reader_owners: dict[str, set[tuple[int, int]]] = {}


class MemoryDeletionJournalIntegrityError(RuntimeError):
    """A content-free, stable deletion-ledger integrity failure."""

    code = MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR

    def __init__(self, *_details: object, **_metadata: object) -> None:
        super().__init__(self.code)


class MemoryDeletionJournalBusyError(
    MemoryDeletionJournalIntegrityError
):
    """A content-free, retryable deletion-ledger lock conflict."""

    code = MEMORY_DELETION_JOURNAL_BUSY_ERROR


@dataclass(frozen=True)
class MemoryDeletionPosition:
    schema: str
    root_digest: str
    sequence: int
    position_digest: str


@dataclass(frozen=True)
class _JournalPaths:
    index_dir: Path
    journal: Path
    head: Path
    writer_lock: Path


@dataclass(frozen=True)
class _JournalSnapshot:
    events: tuple[dict[str, Any], ...]
    sequence: int
    last_hash: str
    last_previous_hash: str
    legacy_hash: str
    hashes: Mapping[int, str]
    previous_hashes: Mapping[int, str]
    journal_exists: bool
    head: Mapping[str, Any] | None
    head_state: str
    head_auth_state: str
    anchor_state: str


def _integrity_failure(
    _cause: BaseException | None = None,
) -> MemoryDeletionJournalIntegrityError:
    # Deliberately discard parser, path, and operating-system details. They
    # can contain memory text or host information and are not part of the
    # public failure contract.
    return MemoryDeletionJournalIntegrityError()


def _busy_failure(
    _cause: BaseException | None = None,
) -> MemoryDeletionJournalBusyError:
    return MemoryDeletionJournalBusyError()


def memory_deletion_journal_error_code(
    error: BaseException,
) -> str:
    """Project one of the two fixed, content-free public error codes."""

    if isinstance(error, MemoryDeletionJournalBusyError):
        return MEMORY_DELETION_JOURNAL_BUSY_ERROR
    return MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR


def _paths(index_dir: Path) -> _JournalPaths:
    try:
        candidate = Path(index_dir)
        if candidate.is_symlink() or (
            candidate.exists() and not candidate.is_dir()
        ):
            raise _integrity_failure()
        resolved = candidate.resolve()
    except MemoryDeletionJournalIntegrityError:
        raise
    except (OSError, RuntimeError) as exc:
        raise _integrity_failure(exc) from None
    return _JournalPaths(
        index_dir=resolved,
        journal=resolved / MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME,
        head=resolved / MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME,
        writer_lock=(
            resolved / MEMORY_DELETE_TOMBSTONE_WRITER_LOCK_NAME
        ),
    )


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_artifact_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate_json_key")
        payload[key] = value
    return payload


def _strict_json_loads(encoded: str) -> Any:
    return json.loads(encoded, object_pairs_hook=_strict_json_object)


def _valid_hash(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        return ""
    lowered = value.lower()
    if value != lowered:
        return ""
    if not all(character in "0123456789abcdef" for character in lowered):
        return ""
    return lowered


def _event_hash(payload: Mapping[str, Any]) -> str:
    unsigned = {
        key: value for key, value in payload.items() if key != "eventHash"
    }
    return hashlib.sha256(_canonical_json(unsigned)).hexdigest()


def _legacy_anchor(raw_prefix: bytes) -> str:
    if not raw_prefix:
        return MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
    digest = hashlib.sha256(_LEGACY_ANCHOR_DOMAIN)
    digest.update(raw_prefix)
    return digest.hexdigest()


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _auth_tag(
    key: bytes,
    payload: Mapping[str, Any],
    *,
    domain: bytes,
) -> str:
    digest = hmac.new(key, digestmod=hashlib.sha256)
    digest.update(domain)
    digest.update(_canonical_json(payload))
    return digest.hexdigest()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 20:
        return False
    try:
        time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _valid_text(value: Any, *, maximum: int) -> bool:
    if not isinstance(value, str) or not (1 <= len(value) <= maximum):
        return False
    if value != value.strip():
        return False
    return not any(ord(character) < 32 for character in value)


def _native_note_id_is_valid(value: object) -> bool:
    if not isinstance(value, str) or not _NATIVE_NOTE_ID.fullmatch(value):
        return False
    date_match = re.fullmatch(
        r"daily-(?:consolidation-)?([0-9]{4}-[0-9]{2}-[0-9]{2})"
        r"(?:-continuation-(?:0|[1-9][0-9]*))?",
        value,
    )
    if date_match is None:
        return True
    try:
        time.strptime(date_match.group(1), "%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def memory_deletion_ledger_note_id(value: object) -> str:
    """Map an application note ID to its content-free ledger identity.

    Only Evelyn's fixed machine ID formats pass through. Everything else is
    represented by a domain-separated digest, so user-authored front matter
    cannot put transcript-like text in a newly persisted deletion event.
    The ``opaque-*`` namespace is reserved for the ledger and is therefore
    hashed when supplied as an application ID.
    """

    if not _valid_text(value, maximum=_MAX_IDENTIFIER_CHARS):
        raise _integrity_failure()
    raw = str(value)
    if _native_note_id_is_valid(raw):
        return raw
    digest = hashlib.sha256(_OPAQUE_NOTE_ID_DOMAIN)
    digest.update(raw.encode("utf-8", errors="strict"))
    return f"opaque-{digest.hexdigest()}"


def normalize_memory_deletion_note_id(value: object) -> str:
    """Compatibility alias for application-ID ledger canonicalization."""

    return memory_deletion_ledger_note_id(value)


def memory_deletion_note_id_is_canonical(value: object) -> bool:
    if not isinstance(value, str) or not _LEDGER_NOTE_ID.fullmatch(value):
        return False
    if value.startswith("opaque-"):
        return True
    return _native_note_id_is_valid(value)


def normalize_memory_deletion_note_type(value: object) -> str:
    if not _valid_text(value, maximum=_MAX_TYPE_CHARS):
        raise _integrity_failure()
    return _NOTE_TYPE_ALIASES.get(str(value).lower(), "unknown")


def normalize_memory_deletion_source_type(value: object) -> str:
    if not _valid_text(value, maximum=_MAX_TYPE_CHARS):
        raise _integrity_failure()
    return _SOURCE_TYPE_ALIASES.get(str(value).lower(), "unknown")


def canonicalize_memory_deletion_tombstone_payload(
    payload: Any,
) -> dict[str, Any]:
    """Validate a public v1 tombstone and return its content-free form."""

    is_revocation = (
        isinstance(payload, dict)
        and payload.get("reason") == "source_revoked"
    )
    expected = (
        _V1_KEYS | _REVOCATION_KEYS if is_revocation else _V1_KEYS
    )
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("schema") != MEMORY_DELETE_TOMBSTONE_V1_SCHEMA
        or payload.get("reason") not in _ALLOWED_REASONS
        or not _valid_timestamp(payload.get("deletedAt"))
    ):
        raise _integrity_failure()
    canonical: dict[str, Any] = {
        "schema": MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
        "noteId": memory_deletion_ledger_note_id(payload.get("noteId")),
        "noteType": normalize_memory_deletion_note_type(
            payload.get("noteType")
        ),
        "sourceType": normalize_memory_deletion_source_type(
            payload.get("sourceType")
        ),
        "reason": payload["reason"],
        "deletedAt": payload["deletedAt"],
    }
    if is_revocation:
        revoked_ids = payload.get("revokedByNoteIds")
        if (
            not isinstance(revoked_ids, list)
            or not (1 <= len(revoked_ids) <= 12)
            or any(
                not _valid_text(
                    note_id,
                    maximum=_MAX_IDENTIFIER_CHARS,
                )
                for note_id in revoked_ids
            )
            or revoked_ids != sorted(set(revoked_ids))
        ):
            raise _integrity_failure()
        canonical["revokedByNoteIds"] = sorted(
            {
                memory_deletion_ledger_note_id(note_id)
                for note_id in revoked_ids
            }
        )
    return canonical


def _validate_v2_tombstone_payload(payload: Any) -> dict[str, Any]:
    is_revocation = (
        isinstance(payload, dict)
        and payload.get("reason") == "source_revoked"
    )
    expected = _V2_KEYS | _REVOCATION_KEYS if is_revocation else _V2_KEYS
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("schema") != MEMORY_DELETE_TOMBSTONE_V2_SCHEMA
        or not memory_deletion_note_id_is_canonical(
            payload.get("noteId")
        )
        or payload.get("noteType")
        not in MEMORY_DELETE_TOMBSTONE_ALLOWED_NOTE_TYPES
        or payload.get("sourceType")
        not in MEMORY_DELETE_TOMBSTONE_ALLOWED_SOURCE_TYPES
        or payload.get("reason") not in _ALLOWED_REASONS
        or not _valid_timestamp(payload.get("deletedAt"))
    ):
        raise _integrity_failure()
    if is_revocation:
        revoked_ids = payload.get("revokedByNoteIds")
        if (
            not isinstance(revoked_ids, list)
            or not (1 <= len(revoked_ids) <= 12)
            or any(
                not memory_deletion_note_id_is_canonical(note_id)
                for note_id in revoked_ids
            )
            or revoked_ids != sorted(set(revoked_ids))
        ):
            raise _integrity_failure()
    sequence = payload.get("sequence")
    if (
        payload.get("contentFree") is not True
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 1
        or not _valid_hash(payload.get("previousHash"))
        or not _valid_hash(payload.get("eventHash"))
    ):
        raise _integrity_failure()
    return dict(payload)


def _validate_tombstone_payload(
    payload: Any,
    *,
    schema: str,
) -> dict[str, Any]:
    if schema == MEMORY_DELETE_TOMBSTONE_V1_SCHEMA:
        return canonicalize_memory_deletion_tombstone_payload(payload)
    if schema == MEMORY_DELETE_TOMBSTONE_V2_SCHEMA:
        return _validate_v2_tombstone_payload(payload)
    raise _integrity_failure()


def _read_journal(
    paths: _JournalPaths,
) -> tuple[
    tuple[dict[str, Any], ...],
    int,
    str,
    str,
    str,
    dict[int, str],
    dict[int, str],
    bool,
]:
    path = paths.journal
    try:
        if path.is_symlink():
            raise _integrity_failure()
        if not path.exists():
            return (
                (),
                0,
                MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
                MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
                MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
                {0: MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS},
                {0: MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS},
                False,
            )
        if not path.is_file():
            raise _integrity_failure()
        if path.stat().st_size > MEMORY_DELETE_TOMBSTONE_MAX_JOURNAL_BYTES:
            raise _integrity_failure()
        raw = path.read_bytes()
    except MemoryDeletionJournalIntegrityError:
        raise
    except OSError as exc:
        raise _integrity_failure(exc) from None

    if raw and not raw.endswith(b"\n"):
        raise _integrity_failure()
    records = raw.splitlines(keepends=True)
    events: list[dict[str, Any]] = []
    legacy_prefix = bytearray()
    sequence = 0
    chain_started = False
    expected_previous = MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
    hashes: dict[int, str] = {}
    previous_hashes: dict[int, str] = {}

    for record in records:
        if not record.endswith(b"\n"):
            raise _integrity_failure()
        encoded = record[:-1]
        if encoded.endswith(b"\r"):
            encoded = encoded[:-1]
        if (
            not encoded
            or len(encoded) > MEMORY_DELETE_TOMBSTONE_MAX_RECORD_BYTES
        ):
            raise _integrity_failure()
        try:
            decoded = encoded.decode("utf-8", errors="strict")
            payload = _strict_json_loads(decoded)
        except (
            UnicodeError,
            ValueError,
            TypeError,
            OverflowError,
            RecursionError,
        ) as exc:
            raise _integrity_failure(exc) from None
        if not isinstance(payload, dict):
            raise _integrity_failure()
        schema = payload.get("schema")
        if schema == MEMORY_DELETE_TOMBSTONE_V1_SCHEMA:
            if chain_started:
                raise _integrity_failure()
            legacy = _validate_tombstone_payload(
                payload,
                schema=MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
            )
            legacy_prefix.extend(record)
            events.append(legacy)
            continue
        if schema != MEMORY_DELETE_TOMBSTONE_V2_SCHEMA:
            raise _integrity_failure()
        if not chain_started:
            chain_started = True
            expected_previous = _legacy_anchor(bytes(legacy_prefix))
        event = _validate_tombstone_payload(
            payload,
            schema=MEMORY_DELETE_TOMBSTONE_V2_SCHEMA,
        )
        if encoded != _canonical_json(event):
            raise _integrity_failure()
        event_sequence = int(event["sequence"])
        supplied_hash = str(event["eventHash"])
        if (
            event_sequence != sequence + 1
            or event["previousHash"] != expected_previous
            or not secrets.compare_digest(
                supplied_hash,
                _event_hash(event),
            )
        ):
            raise _integrity_failure()
        previous_hashes[event_sequence] = expected_previous
        hashes[event_sequence] = supplied_hash
        sequence = event_sequence
        expected_previous = supplied_hash
        events.append(event)

    legacy_hash = _legacy_anchor(bytes(legacy_prefix))
    hashes[0] = legacy_hash
    previous_hashes[0] = MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
    last_hash = hashes.get(sequence, legacy_hash)
    last_previous_hash = previous_hashes.get(
        sequence,
        MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
    )
    return (
        tuple(events),
        sequence,
        last_hash,
        last_previous_hash,
        legacy_hash,
        hashes,
        previous_hashes,
        True,
    )


def _load_authenticity(
    paths: _JournalPaths,
) -> MemoryIntegrityAuthenticity:
    try:
        return load_memory_integrity_authenticity(
            protected_root=get_repo_root(),
            additional_protected_roots=(paths.index_dir.parent,),
        )
    except MemoryIntegrityAuthenticityError as exc:
        raise _integrity_failure(exc) from None


def _verify_head_authenticity(
    payload: dict[str, Any],
    authenticity: MemoryIntegrityAuthenticity,
) -> str:
    schema = payload.get("schema")
    if schema == MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_SCHEMA:
        if set(payload) != _HEAD_KEYS:
            raise _integrity_failure()
        if authenticity.configured:
            if not authenticity.allow_unsigned_bootstrap:
                raise _integrity_failure()
            return "bootstrap_required"
        return "unconfigured"
    if (
        schema != MEMORY_DELETE_TOMBSTONE_SIGNED_CHAIN_HEAD_SCHEMA
        or set(payload) != _SIGNED_HEAD_KEYS
        or not authenticity.configured
        or authenticity.key is None
        or payload.get("authAlgorithm") != MEMORY_INTEGRITY_ALGORITHM
        or payload.get("authScope") != MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE
        or payload.get("authKeyId") != _key_id(authenticity.key)
    ):
        raise _integrity_failure()
    supplied = _valid_hash(payload.get("authTag"))
    unsigned = {
        key: value for key, value in payload.items() if key != "authTag"
    }
    expected = _auth_tag(
        authenticity.key,
        unsigned,
        domain=_HEAD_AUTH_DOMAIN,
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise _integrity_failure()
    return "verified"


def _read_head(
    paths: _JournalPaths,
    authenticity: MemoryIntegrityAuthenticity,
) -> tuple[dict[str, Any] | None, str]:
    path = paths.head
    try:
        if path.is_symlink():
            raise _integrity_failure()
        if not path.exists():
            return None, "missing"
        if not path.is_file():
            raise _integrity_failure()
        if path.stat().st_size > MEMORY_DELETE_TOMBSTONE_MAX_HEAD_BYTES:
            raise _integrity_failure()
        raw = path.read_text(encoding="utf-8")
        payload = _strict_json_loads(raw)
    except MemoryDeletionJournalIntegrityError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise _integrity_failure(exc) from None
    if (
        not isinstance(payload, dict)
        or payload.get("contentFree") is not True
        or not _valid_timestamp(payload.get("updatedAt"))
    ):
        raise _integrity_failure()
    if raw != _canonical_artifact_json(payload):
        raise _integrity_failure()
    auth_state = _verify_head_authenticity(payload, authenticity)
    sequence = payload.get("sequence")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not _valid_hash(payload.get("eventHash"))
        or not _valid_hash(payload.get("previousHash"))
        or not _valid_hash(payload.get("legacyPrefixHash"))
    ):
        raise _integrity_failure()
    return payload, auth_state


def _anchor_path(authenticity: MemoryIntegrityAuthenticity) -> Path:
    if authenticity.anchor_root is None:
        raise _integrity_failure()
    return (
        authenticity.anchor_root
        / MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME
    )


def _initialization_path(
    authenticity: MemoryIntegrityAuthenticity,
) -> Path:
    if authenticity.anchor_root is None:
        raise _integrity_failure()
    return (
        authenticity.anchor_root
        / MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME
    )


def _read_external_initialization(
    authenticity: MemoryIntegrityAuthenticity,
) -> dict[str, Any] | None:
    if not authenticity.external_anchor_configured:
        return None
    if authenticity.key is None:
        raise _integrity_failure()
    path = _initialization_path(authenticity)
    try:
        if path.is_symlink():
            raise _integrity_failure()
        if not path.exists():
            return None
        if (
            not path.is_file()
            or path.stat().st_size > _MAX_ANCHOR_BYTES
        ):
            raise _integrity_failure()
        raw = path.read_text(encoding="utf-8")
        payload = _strict_json_loads(raw)
    except MemoryDeletionJournalIntegrityError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise _integrity_failure(exc) from None
    initialized_at = (
        payload.get("initializedAt")
        if isinstance(payload, dict)
        else None
    )
    if (
        not isinstance(payload, dict)
        or set(payload) != _INITIALIZATION_KEYS
        or payload.get("schema")
        != MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_SCHEMA
        or payload.get("initialized") is not True
        or payload.get("contentFree") is not True
        or payload.get("authAlgorithm") != MEMORY_INTEGRITY_ALGORITHM
        or payload.get("authScope") != MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE
        or payload.get("authKeyId") != _key_id(authenticity.key)
        or isinstance(initialized_at, bool)
        or not isinstance(initialized_at, (int, float))
        or not math.isfinite(float(initialized_at))
        or float(initialized_at) < 0.0
    ):
        raise _integrity_failure()
    if raw != _canonical_artifact_json(payload):
        raise _integrity_failure()
    supplied = _valid_hash(payload.get("authTag"))
    unsigned = {
        key: value for key, value in payload.items() if key != "authTag"
    }
    expected = _auth_tag(
        authenticity.key,
        unsigned,
        domain=_EXTERNAL_INITIALIZATION_AUTH_DOMAIN,
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise _integrity_failure()
    return payload


def _read_external_anchor(
    authenticity: MemoryIntegrityAuthenticity,
) -> dict[str, Any] | None:
    if not authenticity.external_anchor_configured:
        return None
    if authenticity.key is None:
        raise _integrity_failure()
    path = _anchor_path(authenticity)
    try:
        if path.is_symlink():
            raise _integrity_failure()
        if not path.exists():
            return None
        if (
            not path.is_file()
            or path.stat().st_size > _MAX_ANCHOR_BYTES
        ):
            raise _integrity_failure()
        raw = path.read_text(encoding="utf-8")
        payload = _strict_json_loads(raw)
    except MemoryDeletionJournalIntegrityError:
        raise
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise _integrity_failure(exc) from None
    sequence = payload.get("sequence") if isinstance(payload, dict) else None
    updated_at = payload.get("updatedAt") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != _ANCHOR_KEYS
        or payload.get("schema")
        != MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_SCHEMA
        or payload.get("contentFree") is not True
        or payload.get("authAlgorithm") != MEMORY_INTEGRITY_ALGORITHM
        or payload.get("authScope") != MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE
        or payload.get("authKeyId") != _key_id(authenticity.key)
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not _valid_hash(payload.get("eventHash"))
        or not _valid_hash(payload.get("previousHash"))
        or not _valid_hash(payload.get("legacyPrefixHash"))
        or isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not math.isfinite(float(updated_at))
        or float(updated_at) < 0.0
    ):
        raise _integrity_failure()
    if raw != _canonical_artifact_json(payload):
        raise _integrity_failure()
    supplied = _valid_hash(payload.get("authTag"))
    unsigned = {
        key: value for key, value in payload.items() if key != "authTag"
    }
    expected = _auth_tag(
        authenticity.key,
        unsigned,
        domain=_EXTERNAL_ANCHOR_AUTH_DOMAIN,
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise _integrity_failure()
    return payload


def _position_matches(
    payload: Mapping[str, Any],
    *,
    sequence: int,
    event_hash: str,
    previous_hash: str,
    legacy_hash: str,
) -> bool:
    return (
        payload.get("sequence") == sequence
        and payload.get("eventHash") == event_hash
        and payload.get("previousHash") == previous_hash
        and payload.get("legacyPrefixHash") == legacy_hash
    )


def _inspect_external_anchor(
    authenticity: MemoryIntegrityAuthenticity,
    *,
    sequence: int,
    event_hash: str,
    previous_hash: str,
    legacy_hash: str,
    hashes: Mapping[int, str],
    previous_hashes: Mapping[int, str],
    allow_uninitialized: bool = False,
) -> str:
    if not authenticity.external_anchor_configured:
        return "unconfigured"
    initialization = _read_external_initialization(authenticity)
    anchor = _read_external_anchor(authenticity)
    if anchor is None:
        if initialization is not None:
            # Once initialization has been durably witnessed, a missing
            # anchor is a total-deletion/replay signal, never a bootstrap.
            raise _integrity_failure()
        if allow_uninitialized:
            return "uninitialized"
        if authenticity.allow_unsigned_bootstrap:
            return "bootstrap_required"
        raise _integrity_failure()
    if _position_matches(
        anchor,
        sequence=sequence,
        event_hash=event_hash,
        previous_hash=previous_hash,
        legacy_hash=legacy_hash,
    ):
        return (
            "verified"
            if initialization is not None
            else "marker_required"
        )
    prior_sequence = sequence - 1
    if (
        prior_sequence >= 0
        and _position_matches(
            anchor,
            sequence=prior_sequence,
            event_hash=hashes.get(prior_sequence, ""),
            previous_hash=previous_hashes.get(prior_sequence, ""),
            legacy_hash=legacy_hash,
        )
    ):
        return (
            "lagging"
            if initialization is not None
            else "marker_required"
        )
    raise _integrity_failure()


def _journal_snapshot(paths: _JournalPaths) -> _JournalSnapshot:
    authenticity = _load_authenticity(paths)
    (
        events,
        sequence,
        last_hash,
        last_previous_hash,
        legacy_hash,
        hashes,
        previous_hashes,
        journal_exists,
    ) = _read_journal(paths)
    head, head_auth_state = _read_head(paths, authenticity)
    if not journal_exists and head is not None:
        raise _integrity_failure()
    if head is None:
        if sequence > 0:
            raise _integrity_failure()
        head_state = "missing"
        anchor_sequence = 0
        anchor_hash = legacy_hash
        anchor_previous = MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
        if (
            authenticity.configured
            and journal_exists
            and events
            and not authenticity.allow_unsigned_bootstrap
        ):
            raise _integrity_failure()
    else:
        head_sequence = int(head["sequence"])
        if (
            head_sequence > sequence
            or sequence - head_sequence > 1
            or not _position_matches(
                head,
                sequence=head_sequence,
                event_hash=hashes.get(head_sequence, ""),
                previous_hash=previous_hashes.get(head_sequence, ""),
                legacy_hash=legacy_hash,
            )
        ):
            raise _integrity_failure()
        head_state = (
            "current" if head_sequence == sequence else "lagging"
        )
        anchor_sequence = head_sequence
        anchor_hash = str(head["eventHash"])
        anchor_previous = str(head["previousHash"])
    anchor_state = _inspect_external_anchor(
        authenticity,
        sequence=anchor_sequence,
        event_hash=anchor_hash,
        previous_hash=anchor_previous,
        legacy_hash=legacy_hash,
        hashes=hashes,
        previous_hashes=previous_hashes,
        allow_uninitialized=(
            head is None
            and sequence == 0
            and not events
        ),
    )
    return _JournalSnapshot(
        events=events,
        sequence=sequence,
        last_hash=last_hash,
        last_previous_hash=last_previous_hash,
        legacy_hash=legacy_hash,
        hashes=hashes,
        previous_hashes=previous_hashes,
        journal_exists=journal_exists,
        head=head,
        head_state=head_state,
        head_auth_state=head_auth_state,
        anchor_state=anchor_state,
    )


def _signed_head(
    authenticity: MemoryIntegrityAuthenticity,
    unsigned: Mapping[str, Any],
) -> dict[str, Any]:
    if not authenticity.configured:
        return dict(unsigned)
    if authenticity.key is None:
        raise _integrity_failure()
    payload = {
        **dict(unsigned),
        "schema": MEMORY_DELETE_TOMBSTONE_SIGNED_CHAIN_HEAD_SCHEMA,
        "authAlgorithm": MEMORY_INTEGRITY_ALGORITHM,
        "authScope": MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE,
        "authKeyId": _key_id(authenticity.key),
    }
    payload["authTag"] = _auth_tag(
        authenticity.key,
        payload,
        domain=_HEAD_AUTH_DOMAIN,
    )
    return payload


def _ensure_external_initialization(
    authenticity: MemoryIntegrityAuthenticity,
) -> None:
    if not authenticity.external_anchor_configured:
        return
    if authenticity.key is None:
        raise _integrity_failure()
    if _read_external_initialization(authenticity) is not None:
        return
    path = _initialization_path(authenticity)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _integrity_failure()
    payload: dict[str, Any] = {
        "schema": MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_SCHEMA,
        "initialized": True,
        "initializedAt": time.time(),
        "contentFree": True,
        "authAlgorithm": MEMORY_INTEGRITY_ALGORITHM,
        "authScope": MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE,
        "authKeyId": _key_id(authenticity.key),
    }
    payload["authTag"] = _auth_tag(
        authenticity.key,
        payload,
        domain=_EXTERNAL_INITIALIZATION_AUTH_DOMAIN,
    )
    try:
        atomic_json_write(path, payload, durable=True)
    except DurableCommitError as exc:
        raise _integrity_failure(exc) from None
    except OSError as exc:
        try:
            persisted = _read_external_initialization(authenticity)
        except MemoryDeletionJournalIntegrityError:
            raise _integrity_failure(exc) from None
        if persisted is None:
            raise _integrity_failure(exc) from None


def _write_external_anchor(
    authenticity: MemoryIntegrityAuthenticity,
    *,
    sequence: int,
    event_hash: str,
    previous_hash: str,
    legacy_hash: str,
) -> None:
    if not authenticity.external_anchor_configured:
        return
    if authenticity.key is None:
        raise _integrity_failure()
    # The durable marker is written first. If the following anchor commit is
    # interrupted, its presence makes the missing anchor fail closed.
    _ensure_external_initialization(authenticity)
    path = _anchor_path(authenticity)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise _integrity_failure()
    payload: dict[str, Any] = {
        "schema": MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_SCHEMA,
        "sequence": int(sequence),
        "eventHash": str(event_hash),
        "previousHash": str(previous_hash),
        "legacyPrefixHash": str(legacy_hash),
        "updatedAt": time.time(),
        "contentFree": True,
        "authAlgorithm": MEMORY_INTEGRITY_ALGORITHM,
        "authScope": MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE,
        "authKeyId": _key_id(authenticity.key),
    }
    payload["authTag"] = _auth_tag(
        authenticity.key,
        payload,
        domain=_EXTERNAL_ANCHOR_AUTH_DOMAIN,
    )
    try:
        atomic_json_write(path, payload, durable=True)
    except DurableCommitError as exc:
        raise _integrity_failure(exc) from None
    except OSError as exc:
        # ``atomic_json_write`` can report a temporary-file cleanup failure
        # after the durable replacement already committed. Accept only the
        # exact authenticated position we intended to persist.
        try:
            persisted = _read_external_anchor(authenticity)
        except MemoryDeletionJournalIntegrityError:
            raise _integrity_failure(exc) from None
        if persisted is None or not _position_matches(
            persisted,
            sequence=sequence,
            event_hash=event_hash,
            previous_hash=previous_hash,
            legacy_hash=legacy_hash,
        ):
            raise _integrity_failure(exc) from None


def _reconcile_external_anchor(
    authenticity: MemoryIntegrityAuthenticity,
    *,
    sequence: int,
    event_hash: str,
    previous_hash: str,
    legacy_hash: str,
    hashes: Mapping[int, str],
    previous_hashes: Mapping[int, str],
) -> None:
    state = _inspect_external_anchor(
        authenticity,
        sequence=sequence,
        event_hash=event_hash,
        previous_hash=previous_hash,
        legacy_hash=legacy_hash,
        hashes=hashes,
        previous_hashes=previous_hashes,
        allow_uninitialized=(
            sequence == 0
            and event_hash == MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
            and previous_hash == MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
            and legacy_hash == MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS
        ),
    )
    if state in {"unconfigured", "verified"}:
        return
    if state not in {
        "bootstrap_required",
        "lagging",
        "marker_required",
        "uninitialized",
    }:
        raise _integrity_failure()
    _write_external_anchor(
        authenticity,
        sequence=sequence,
        event_hash=event_hash,
        previous_hash=previous_hash,
        legacy_hash=legacy_hash,
    )


def _write_chain_head(
    paths: _JournalPaths,
    *,
    sequence: int,
    event_hash: str,
    previous_hash: str,
    legacy_hash: str,
    hashes: Mapping[int, str],
    previous_hashes: Mapping[int, str],
) -> None:
    authenticity = _load_authenticity(paths)
    unsigned = {
        "schema": MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_SCHEMA,
        "sequence": int(sequence),
        "eventHash": str(event_hash),
        "previousHash": str(previous_hash),
        "legacyPrefixHash": str(legacy_hash),
        "updatedAt": _now_iso(),
        "contentFree": True,
    }
    persisted_payload = _signed_head(authenticity, unsigned)
    try:
        atomic_json_write(
            paths.head,
            persisted_payload,
            durable=True,
        )
    except DurableCommitError as exc:
        raise _integrity_failure(exc) from None
    except MemoryDeletionJournalIntegrityError:
        raise
    except OSError as exc:
        # A post-replace cleanup error is not a failed commit. Re-read and
        # authenticate the head, accepting only the exact intended position.
        try:
            persisted, _auth_state = _read_head(
                paths,
                authenticity,
            )
        except MemoryDeletionJournalIntegrityError:
            raise _integrity_failure(exc) from None
        if persisted is None or not _position_matches(
            persisted,
            sequence=sequence,
            event_hash=event_hash,
            previous_hash=previous_hash,
            legacy_hash=legacy_hash,
        ):
            raise _integrity_failure(exc) from None
    _reconcile_external_anchor(
        authenticity,
        sequence=sequence,
        event_hash=event_hash,
        previous_hash=previous_hash,
        legacy_hash=legacy_hash,
        hashes=hashes,
        previous_hashes=previous_hashes,
    )


def _writer_owner() -> tuple[int, int]:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (
        threading.get_ident(),
        id(task) if task is not None else 0,
    )


def _lock_windows_handle(
    handle: Any,
    *,
    exclusive: bool,
) -> object:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    overlapped = _Overlapped()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lock_file = kernel32.LockFileEx
    lock_file.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    lock_file.restype = wintypes.BOOL
    flags = 0x00000001 | (0x00000002 if exclusive else 0)
    locked = lock_file(
        wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno())),
        flags,
        0,
        1,
        0,
        ctypes.byref(overlapped),
    )
    if not locked:
        error_code = ctypes.get_last_error()
        if error_code == 33:
            raise _busy_failure()
        raise OSError(error_code, "file lock failed")
    return overlapped


def _unlock_windows_handle(handle: Any, token: object | None) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    unlock_file = kernel32.UnlockFileEx
    unlock_file.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    unlock_file.restype = wintypes.BOOL
    if token is None or not unlock_file(
        wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno())),
        0,
        1,
        0,
        ctypes.byref(token),
    ):
        raise OSError(ctypes.get_last_error(), "file unlock failed")


def _lock_writer_handle(handle: Any) -> object | None:
    if os.name == "nt":
        return _lock_windows_handle(handle, exclusive=True)
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return None


def _unlock_writer_handle(handle: Any, token: object | None) -> None:
    if os.name == "nt":
        _unlock_windows_handle(handle, token)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_reader_handle(handle: Any) -> object | None:
    if os.name == "nt":
        return _lock_windows_handle(handle, exclusive=False)
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    return None


def _unlock_reader_handle(handle: Any, token: object | None) -> None:
    if os.name == "nt":
        _unlock_windows_handle(handle, token)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_contention(error: OSError) -> bool:
    return isinstance(error, BlockingIOError) or error.errno in {
        errno.EACCES,
        errno.EAGAIN,
    }


@contextlib.contextmanager
def _writer_guard(index_dir: Path):
    paths = _paths(index_dir)
    root_key = str(paths.index_dir)
    owner = _writer_owner()
    reentrant = False
    with _process_lock:
        current_owner = _writer_owners.get(root_key)
        if current_owner == owner:
            reentrant = True
        elif current_owner is not None or _reader_owners.get(root_key):
            raise _busy_failure()
        else:
            _writer_owners[root_key] = owner
    if reentrant:
        yield
        return
    locked = False
    token: object | None = None
    try:
        try:
            paths.index_dir.mkdir(parents=True, exist_ok=True)
            if paths.writer_lock.is_symlink():
                raise _integrity_failure()
            with paths.writer_lock.open("a+b") as handle:
                try:
                    token = _lock_writer_handle(handle)
                    locked = True
                except MemoryDeletionJournalBusyError:
                    raise
                except OSError as exc:
                    if _lock_contention(exc):
                        raise _busy_failure(exc) from None
                    raise _integrity_failure(exc) from None
                try:
                    yield
                finally:
                    if locked:
                        with contextlib.suppress(OSError):
                            _unlock_writer_handle(handle, token)
        except MemoryDeletionJournalIntegrityError:
            raise
        except OSError as exc:
            raise _integrity_failure(exc) from None
    finally:
        with _process_lock:
            if _writer_owners.get(root_key) == owner:
                _writer_owners.pop(root_key, None)


@contextlib.contextmanager
def _reader_guard(index_dir: Path):
    paths = _paths(index_dir)
    root_key = str(paths.index_dir)
    owner = _writer_owner()
    reentrant = False
    with _process_lock:
        current_writer = _writer_owners.get(root_key)
        readers = _reader_owners.get(root_key, set())
        if current_writer == owner or owner in readers:
            reentrant = True
        elif current_writer is not None:
            raise _busy_failure()
        else:
            _reader_owners.setdefault(root_key, set()).add(owner)
    if reentrant:
        yield
        return
    locked = False
    token: object | None = None
    try:
        try:
            paths.index_dir.mkdir(parents=True, exist_ok=True)
            if paths.writer_lock.is_symlink():
                raise _integrity_failure()
            with paths.writer_lock.open("a+b") as handle:
                try:
                    token = _lock_reader_handle(handle)
                    locked = True
                except MemoryDeletionJournalBusyError:
                    raise
                except OSError as exc:
                    if _lock_contention(exc):
                        raise _busy_failure(exc) from None
                    raise _integrity_failure(exc) from None
                try:
                    yield
                finally:
                    if locked:
                        with contextlib.suppress(OSError):
                            _unlock_reader_handle(handle, token)
        except MemoryDeletionJournalIntegrityError:
            raise
        except OSError as exc:
            raise _integrity_failure(exc) from None
    finally:
        with _process_lock:
            readers = _reader_owners.get(root_key)
            if readers is not None:
                readers.discard(owner)
                if not readers:
                    _reader_owners.pop(root_key, None)


def _repair_snapshot(
    paths: _JournalPaths,
    snapshot: _JournalSnapshot,
) -> _JournalSnapshot:
    target_sequence = snapshot.sequence
    needs_head = (
        snapshot.head_state == "lagging"
        or snapshot.head_auth_state == "bootstrap_required"
        or (
            snapshot.head_state == "missing"
            and snapshot.journal_exists
            and bool(snapshot.events)
        )
    )
    if needs_head:
        _write_chain_head(
            paths,
            sequence=target_sequence,
            event_hash=snapshot.last_hash,
            previous_hash=snapshot.last_previous_hash,
            legacy_hash=snapshot.legacy_hash,
            hashes=snapshot.hashes,
            previous_hashes=snapshot.previous_hashes,
        )
    elif snapshot.anchor_state in {
        "bootstrap_required",
        "lagging",
        "marker_required",
    }:
        if snapshot.head is None:
            raise _integrity_failure()
        _write_chain_head(
            paths,
            sequence=target_sequence,
            event_hash=snapshot.last_hash,
            previous_hash=snapshot.last_previous_hash,
            legacy_hash=snapshot.legacy_hash,
            hashes=snapshot.hashes,
            previous_hashes=snapshot.previous_hashes,
        )
    repaired = _journal_snapshot(paths)
    if (
        repaired.head_state not in {"current", "missing"}
        or repaired.head_auth_state == "bootstrap_required"
        or repaired.anchor_state
        in {"bootstrap_required", "lagging", "marker_required"}
    ):
        raise _integrity_failure()
    return repaired


def _needs_repair(snapshot: _JournalSnapshot) -> bool:
    return (
        snapshot.head_state == "lagging"
        or snapshot.head_auth_state == "bootstrap_required"
        or snapshot.anchor_state
        in {"bootstrap_required", "lagging", "marker_required"}
        or (
            snapshot.head_state == "missing"
            and snapshot.journal_exists
            and bool(snapshot.events)
        )
    )


@contextlib.contextmanager
def _validated_reader_guard(
    paths: _JournalPaths,
    *,
    allow_repair: bool = True,
):
    while True:
        with _reader_guard(paths.index_dir):
            with _process_lock:
                snapshot = _journal_snapshot(paths)
            if not _needs_repair(snapshot):
                yield snapshot
                return
            if not allow_repair:
                raise _integrity_failure()
        with _writer_guard(paths.index_dir):
            with _process_lock:
                snapshot = _journal_snapshot(paths)
                if _needs_repair(snapshot):
                    _repair_snapshot(paths, snapshot)


def _snapshot_position(snapshot: _JournalSnapshot) -> tuple[Any, ...]:
    head = snapshot.head or {}
    return (
        snapshot.sequence,
        snapshot.last_hash,
        snapshot.last_previous_hash,
        snapshot.legacy_hash,
        snapshot.journal_exists,
        head.get("sequence"),
        head.get("eventHash"),
        head.get("previousHash"),
        head.get("legacyPrefixHash"),
        snapshot.head_auth_state,
        snapshot.anchor_state,
    )


def _root_identity_digest(paths: _JournalPaths) -> str:
    identity = {
        "schema": "memory.deletion.root-identity.v1",
        "resolvedIndexRoot": os.path.normcase(str(paths.index_dir)),
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _public_position(
    paths: _JournalPaths,
    snapshot: _JournalSnapshot,
) -> MemoryDeletionPosition:
    root_digest = _root_identity_digest(paths)
    position_payload = {
        "schema": MEMORY_DELETION_POSITION_SCHEMA,
        "rootDigest": root_digest,
        "sequence": snapshot.sequence,
        "position": list(_snapshot_position(snapshot)),
    }
    return MemoryDeletionPosition(
        schema=MEMORY_DELETION_POSITION_SCHEMA,
        root_digest=root_digest,
        sequence=snapshot.sequence,
        position_digest=hashlib.sha256(
            _canonical_json(position_payload)
        ).hexdigest(),
    )


def _position_is_valid(position: object) -> bool:
    return bool(
        isinstance(position, MemoryDeletionPosition)
        and position.schema == MEMORY_DELETION_POSITION_SCHEMA
        and _valid_hash(position.root_digest)
        and not isinstance(position.sequence, bool)
        and isinstance(position.sequence, int)
        and position.sequence >= 0
        and _valid_hash(position.position_digest)
    )


def _positions_match(
    expected: object,
    current: MemoryDeletionPosition,
) -> bool:
    if not _position_is_valid(expected):
        return False
    assert isinstance(expected, MemoryDeletionPosition)
    return bool(
        expected.sequence == current.sequence
        and secrets.compare_digest(
            expected.root_digest,
            current.root_digest,
        )
        and secrets.compare_digest(
            expected.position_digest,
            current.position_digest,
        )
    )


def read_memory_deletion_tombstones(
    index_dir: Path,
) -> list[dict[str, Any]]:
    """Read and validate all deletion tombstones, repairing one commit lag.

    The function never returns a partial prefix. Any malformed row, broken
    chain, invalid head, or replay signal raises the stable integrity error.
    """

    paths = _paths(index_dir)
    with _validated_reader_guard(paths) as snapshot:
        return [dict(event) for event in snapshot.events]


def memory_deletion_journal_position(
    index_dir: Path,
) -> MemoryDeletionPosition:
    """Return the validated, content-free position of one deletion ledger."""

    paths = _paths(index_dir)
    with _validated_reader_guard(paths) as snapshot:
        return _public_position(paths, snapshot)


@contextlib.contextmanager
def memory_deletion_journal_read_guard(
    index_dir: Path,
    *,
    expected_position: MemoryDeletionPosition | None = None,
    require_stable: bool = True,
    allow_repair: bool = True,
):
    """Hold a shared deletion lease; optionally reject repairable state."""

    paths = _paths(index_dir)
    with _validated_reader_guard(
        paths,
        allow_repair=allow_repair,
    ) as snapshot:
        initial_position = _snapshot_position(snapshot)
        public_position = _public_position(paths, snapshot)
        if expected_position is not None and not _positions_match(
            expected_position,
            public_position,
        ):
            raise _integrity_failure()
        yield public_position
        with _process_lock:
            final_snapshot = _journal_snapshot(paths)
            if _needs_repair(final_snapshot):
                raise _integrity_failure()
            if (
                require_stable
                and _snapshot_position(final_snapshot)
                != initial_position
            ):
                raise _integrity_failure()


@contextlib.contextmanager
def memory_deletion_journal_guard(
    index_dir: Path,
    *,
    expected_position: MemoryDeletionPosition | None = None,
    require_stable: bool = True,
):
    """Hold the exclusive deletion writer lease across a mutation boundary.

    Read-only response and outbound exposure callers must use
    ``memory_deletion_journal_read_guard`` so independent readers can coexist.
    """

    paths = _paths(index_dir)
    with _writer_guard(paths.index_dir):
        with _process_lock:
            snapshot = _journal_snapshot(paths)
            if _needs_repair(snapshot):
                snapshot = _repair_snapshot(paths, snapshot)
            initial_position = _snapshot_position(snapshot)
            public_position = _public_position(paths, snapshot)
            if expected_position is not None and not _positions_match(
                expected_position,
                public_position,
            ):
                raise _integrity_failure()
        yield public_position
        with _process_lock:
            final_snapshot = _journal_snapshot(paths)
            if _needs_repair(final_snapshot):
                final_snapshot = _repair_snapshot(
                    paths,
                    final_snapshot,
                )
            if (
                require_stable
                and _snapshot_position(final_snapshot)
                != initial_position
            ):
                raise _integrity_failure()


def append_memory_deletion_tombstone(
    index_dir: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Append a strict v1 payload as a chained, content-free v2 event."""

    legacy_payload = _validate_tombstone_payload(
        payload,
        schema=MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
    )
    paths = _paths(index_dir)
    with _writer_guard(paths.index_dir):
        with _process_lock:
            snapshot = _journal_snapshot(paths)
            try:
                paths.index_dir.mkdir(parents=True, exist_ok=True)
                if not snapshot.journal_exists:
                    try:
                        atomic_text_write(paths.journal, "", durable=True)
                    except DurableCommitError as exc:
                        raise _integrity_failure(exc) from None
                    except OSError as exc:
                        try:
                            committed_empty = (
                                not paths.journal.is_symlink()
                                and paths.journal.is_file()
                                and paths.journal.stat().st_size == 0
                            )
                        except OSError:
                            committed_empty = False
                        if not committed_empty:
                            raise _integrity_failure(exc) from None
                    snapshot = _journal_snapshot(paths)
            except MemoryDeletionJournalIntegrityError:
                raise
            except OSError as exc:
                raise _integrity_failure(exc) from None
            if _needs_repair(snapshot):
                snapshot = _repair_snapshot(paths, snapshot)
            if snapshot.head is None:
                _write_chain_head(
                    paths,
                    sequence=0,
                    event_hash=snapshot.legacy_hash,
                    previous_hash=MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
                    legacy_hash=snapshot.legacy_hash,
                    hashes=snapshot.hashes,
                    previous_hashes=snapshot.previous_hashes,
                )
                snapshot = _journal_snapshot(paths)
            event: dict[str, Any] = {
                **legacy_payload,
                "schema": MEMORY_DELETE_TOMBSTONE_V2_SCHEMA,
                "contentFree": True,
                "sequence": snapshot.sequence + 1,
                "previousHash": snapshot.last_hash,
            }
            event["eventHash"] = _event_hash(event)
            line = _canonical_json(event) + b"\n"
            try:
                current_size = paths.journal.stat().st_size
            except OSError as exc:
                raise _integrity_failure(exc) from None
            if (
                current_size + len(line)
                > MEMORY_DELETE_TOMBSTONE_MAX_JOURNAL_BYTES
            ):
                raise _integrity_failure()
            try:
                with paths.journal.open("ab") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise _integrity_failure(exc) from None
            next_hashes = dict(snapshot.hashes)
            next_previous = dict(snapshot.previous_hashes)
            next_hashes[int(event["sequence"])] = str(event["eventHash"])
            next_previous[int(event["sequence"])] = str(
                event["previousHash"]
            )
            _write_chain_head(
                paths,
                sequence=int(event["sequence"]),
                event_hash=str(event["eventHash"]),
                previous_hash=str(event["previousHash"]),
                legacy_hash=snapshot.legacy_hash,
                hashes=next_hashes,
                previous_hashes=next_previous,
            )
            verified = _journal_snapshot(paths)
            if (
                verified.sequence != int(event["sequence"])
                or verified.head_state != "current"
                or verified.anchor_state
                in {
                    "bootstrap_required",
                    "lagging",
                    "marker_required",
                    "uninitialized",
                }
            ):
                raise _integrity_failure()
            return dict(event)


def _file_state(path: Path) -> tuple[int, int]:
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return (-1, -1)
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    except OSError:
        return (-1, -1)
    return (int(stat.st_mtime_ns), int(stat.st_size))


def memory_deletion_journal_state(
    index_dir: Path,
) -> tuple[int, int, int, int]:
    """Return cache state covering both the append log and chain head."""

    paths = _paths(index_dir)
    return (*_file_state(paths.journal), *_file_state(paths.head))


def memory_deletion_journal_status(
    index_dir: Path,
) -> dict[str, Any]:
    """Return content-free integrity and rollback-protection status."""

    paths = _paths(index_dir)
    with _validated_reader_guard(paths) as snapshot:
        authenticity = _load_authenticity(paths)
    initialized = bool(
        snapshot.journal_exists
        or snapshot.head is not None
        or snapshot.events
    )
    rollback_protected = bool(
        authenticity.configured
        and authenticity.external_anchor_configured
        and snapshot.head_auth_state == "verified"
        and snapshot.anchor_state == "verified"
    )
    state = (
        "uninitialized"
        if not initialized
        else "rollback_protected"
        if rollback_protected
        else "locally_verified"
    )
    return {
        "schema": "memory.deletion.integrity.v1",
        "state": state,
        "journalInitialized": initialized,
        "chainHeadState": snapshot.head_state,
        "headAuthenticity": snapshot.head_auth_state,
        "externalAnchorState": snapshot.anchor_state,
        "authenticityConfigured": authenticity.configured,
        "externalAnchorConfigured": (
            authenticity.external_anchor_configured
        ),
        "rollbackProtected": rollback_protected,
        "contentFree": True,
    }


__all__ = [
    "MEMORY_DELETION_JOURNAL_BUSY_ERROR",
    "MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR",
    "MEMORY_DELETION_POSITION_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE",
    "MEMORY_DELETE_TOMBSTONE_ALLOWED_NOTE_TYPES",
    "MEMORY_DELETE_TOMBSTONE_ALLOWED_SOURCE_TYPES",
    "MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS",
    "MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME",
    "MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME",
    "MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME",
    "MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME",
    "MEMORY_DELETE_TOMBSTONE_MAX_HEAD_BYTES",
    "MEMORY_DELETE_TOMBSTONE_MAX_JOURNAL_BYTES",
    "MEMORY_DELETE_TOMBSTONE_MAX_RECORD_BYTES",
    "MEMORY_DELETE_TOMBSTONE_SIGNED_CHAIN_HEAD_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_V1_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_V2_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_WRITER_LOCK_NAME",
    "MemoryDeletionJournalBusyError",
    "MemoryDeletionJournalIntegrityError",
    "MemoryDeletionPosition",
    "append_memory_deletion_tombstone",
    "canonicalize_memory_deletion_tombstone_payload",
    "memory_deletion_journal_position",
    "memory_deletion_journal_read_guard",
    "memory_deletion_journal_guard",
    "memory_deletion_journal_error_code",
    "memory_deletion_journal_state",
    "memory_deletion_journal_status",
    "memory_deletion_ledger_note_id",
    "memory_deletion_note_id_is_canonical",
    "normalize_memory_deletion_note_id",
    "normalize_memory_deletion_note_type",
    "normalize_memory_deletion_source_type",
    "read_memory_deletion_tombstones",
]
