from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .instance_lock_runtime import (
    InstanceLockManager,
    build_instance_lock_runtime_deps,
)


ARCHIVE_SCHEMA_VERSION = 2
_LEGACY_ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_RECORD_SCHEMA = "evelyn.private-conversation-record.v1"
ARCHIVE_TOMBSTONE_TEXT = "사용자의 요청으로 삭제됨"
ARCHIVE_DEPENDENT_REDACTION_TEXT = "삭제된 사용자 기록에 의존하여 숨김"
ARCHIVE_RETENTION_TOMBSTONE_TEXT = "보존 기간 만료로 삭제됨"
ARCHIVE_DELETION_AUDIT_TEXT = "사용자의 삭제 요청에 따라 삭제됨"
ARCHIVE_ADMIN_TOMBSTONE_TEXT = "관리자 요청으로 삭제됨"
ARCHIVE_ADMIN_DELETION_AUDIT_TEXT = "관리자 삭제 요청에 따라 삭제됨"
ARCHIVE_RETENTION_DAYS = 30
ARCHIVE_BACKUP_GRACE_SECONDS = 10 * 60
ARCHIVE_DELETION_CONFIRM_SECONDS = 60
ARCHIVE_ANCHOR_SCHEMA = "evelyn.private-conversation-archive.anchor.v1"
ARCHIVE_LINEAGE_KINDS = frozenset(
    {
        "turn",
        "session",
        "memory_owner",
        "memory_note",
        "memory_evidence",
    }
)
ARCHIVE_REQUIRED_PURGE_SINKS = (
    "bot_memory",
    "memory_deletion_journal",
    "continuity_checkpoint",
    "ingress_journal",
    "search_cache",
    "prompt_tool_cache",
    "embedding_index",
    "persona_state",
    "cognitive_state",
    "autonomy_state",
    "open_question_state",
    "feedback_state",
    "outbound_retry",
    "stt_buffer",
    "tts_buffer",
    "voice_debug_audio",
    "registered_exports",
)

_STATE_DOMAIN = b"evelyn.private-conversation-archive.state.v1\n"
_CHAIN_DOMAIN = b"evelyn.private-conversation-archive.chain.v1\n"
_PRINCIPAL_DOMAIN = b"evelyn.private-conversation-archive.principal.v1\n"
_PREVIEW_DOMAIN = b"evelyn.private-conversation-archive.preview.v1\n"
_ADMIN_PREVIEW_DOMAIN = b"evelyn.private-conversation-archive.admin-preview.v1\n"
_ADMIN_READ_CURSOR_DOMAIN = (
    b"evelyn.private-conversation-archive.admin-read-cursor.v1\n"
)
_ADMIN_READ_QUERY_DOMAIN = (
    b"evelyn.private-conversation-archive.admin-read-query.v1\n"
)
_SELF_READ_CURSOR_DOMAIN = (
    b"evelyn.private-conversation-archive.self-read-cursor.v1\n"
)
_SELF_READ_QUERY_DOMAIN = (
    b"evelyn.private-conversation-archive.self-read-query.v1\n"
)
_ADMIN_LIST_CURSOR_DOMAIN = (
    b"evelyn.private-conversation-archive.admin-list-cursor.v1\n"
)
_ADMIN_LIST_FILTER_DOMAIN = (
    b"evelyn.private-conversation-archive.admin-list-filter.v1\n"
)
_IDEMPOTENCY_DOMAIN = b"evelyn.private-conversation-archive.idempotency.v1\n"
_RECORD_IDEMPOTENCY_DOMAIN = (
    b"evelyn.private-conversation-archive.record-idempotency.v1\n"
)
_RECORD_PAYLOAD_DOMAIN = b"evelyn.private-conversation-archive.record-payload.v1\n"
_FEEDBACK_WORKFLOW_DOMAIN = (
    b"evelyn.private-conversation-archive.feedback-workflow.v1\n"
)
_RETIRED_RECORD_ID_DOMAIN = (
    b"evelyn.private-conversation-archive.retired-record-id.v1\n"
)
_LINEAGE_DOMAIN = b"evelyn.private-conversation-archive.lineage.v1\n"
_VOICE_EVENT_DOMAIN = b"evelyn.private-conversation-archive.voice-event.v1\n"
_ANCHOR_DOMAIN = b"evelyn.private-conversation-archive.anchor.v1\n"
_CHAIN_GENESIS = "0" * 64
_MIN_INTEGRITY_KEY_BYTES = 32
_MAX_BODY_BYTES = 128 * 1024
_MAX_OWNER_NAME_CHARACTERS = 80
_MAX_OWNER_NAME_BYTES = 256

_MODES = frozenset({"local_private", "discord_shared"})
_SURFACES = frozenset({"local", "discord", "minecraft"})
_USER_OWNED_RECORD_TYPES = frozenset(
    {
        "user_text",
        "final_stt",
        "minecraft_command",
        "feedback_correction",
    }
)
_DERIVED_RECORD_TYPES = frozenset(
    {
        "evelyn_reply",
        "task_result",
        "action_result",
        "minecraft_result",
        "feedback_source_candidate",
    }
)
_SYSTEM_RECORD_TYPES = frozenset(
    {
        "feedback_independent_version",
        "feedback_evaluation",
        "feedback_approval",
        "feedback_canary",
        "feedback_activation",
        "feedback_failure",
        "feedback_rollback",
        "feedback_revocation",
    }
)
_DURABLE_SYSTEM_RECORD_TYPES = frozenset(
    {
        "feedback_independent_version",
        "feedback_evaluation",
        "feedback_approval",
        "feedback_canary",
        "feedback_activation",
        "feedback_rollback",
        "feedback_revocation",
    }
)
_RECORD_TYPES = (
    _USER_OWNED_RECORD_TYPES | _DERIVED_RECORD_TYPES | _SYSTEM_RECORD_TYPES
)
_EXPECTED_TABLES = frozenset(
    {
        "metadata",
        "principals",
        "records",
        "record_receipts",
        "retired_receipts",
        "record_parents",
        "participation_intervals",
        "voice_state",
        "voice_state_transitions",
        "voice_sources",
        "ingest_receipts",
        "deletion_previews",
        "used_previews",
        "tombstone_audiences",
        "deletion_audits",
        "legal_minimal_events",
    }
)
_STATE_TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "principals": (
        "principal_id",
        "identity_surface",
        "lookup_digest",
        "current_display_name",
        "display_name_updated_at_us",
        "created_at_us",
    ),
    "records": (
        "record_id",
        "record_schema",
        "mode",
        "surface",
        "record_type",
        "owner_principal_id",
        "owner_name_snapshot",
        "guild_id",
        "channel_id",
        "started_at_us",
        "ended_at_us",
        "body",
        "lineage_json",
        "status",
        "placeholder_id",
        "deletion_reason",
        "created_seq",
        "created_generation",
    ),
    "record_parents": ("child_id", "parent_id"),
    "record_receipts": (
        "idempotency_digest",
        "payload_digest",
        "record_id",
        "created_at_us",
    ),
    "retired_receipts": (
        "receipt_kind",
        "receipt_digest",
        "payload_digest",
        "retired_generation",
        "reason",
    ),
    "participation_intervals": (
        "interval_id",
        "principal_id",
        "owner_name_snapshot",
        "guild_id",
        "channel_id",
        "interval_kind",
        "started_at_us",
        "ended_at_us",
    ),
    "voice_state": (
        "principal_id",
        "guild_id",
        "channel_id",
        "present",
        "consent_current",
        "self_mute",
        "server_mute",
        "self_deaf",
        "server_deaf",
        "suppressed",
        "gateway_known",
        "presence_interval_id",
        "eligible_interval_id",
        "updated_at_us",
    ),
    "voice_state_transitions": (
        "transition_id",
        "principal_id",
        "owner_name_snapshot",
        "guild_id",
        "channel_id",
        "event_at_us",
        "present",
        "consent_current",
        "self_mute",
        "server_mute",
        "self_deaf",
        "server_deaf",
        "suppressed",
        "gateway_known",
        "idempotency_digest",
    ),
    "voice_sources": (
        "source_id",
        "generation",
        "last_sequence",
        "activated_at_us",
    ),
    "ingest_receipts": (
        "idempotency_digest",
        "payload_digest",
        "principal_id",
        "source_id",
        "generation",
        "event_sequence",
        "event_at_us",
    ),
    "deletion_previews": (
        "preview_id",
        "preview_kind",
        "actor_lookup_digest",
        "request_guild_id",
        "admin_target_principal_id",
        "admin_record_ids_json",
        "scope_all",
        "started_at_us",
        "ended_at_us",
        "target_fingerprint",
        "snapshot_generation",
        "created_at_us",
        "expires_at_us",
    ),
    "used_previews": ("preview_id", "consumed_at_us"),
    "tombstone_audiences": (
        "placeholder_id",
        "principal_id",
        "guild_id",
    ),
    "deletion_audits": (
        "request_id",
        "reason",
        "status",
        "primary_status",
        "replica_status",
        "display_text",
        "requested_at_us",
        "completed_at_us",
        "deletion_generation",
        "required_sinks_json",
        "completed_sinks_json",
        "purge_scope_json",
    ),
    "legal_minimal_events": (
        "event_id",
        "owner_name",
        "occurred_at_us",
    ),
}
_LEGACY_EXPECTED_TABLES = _EXPECTED_TABLES - {"voice_state_transitions"}
_LEGACY_STATE_TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    table: columns
    for table, columns in _STATE_TABLE_COLUMNS.items()
    if table != "voice_state_transitions"
}


_SCHEMA_SQL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE principals (
    principal_id TEXT PRIMARY KEY,
    identity_surface TEXT NOT NULL,
    lookup_digest TEXT NOT NULL UNIQUE,
    current_display_name TEXT NOT NULL,
    display_name_updated_at_us INTEGER NOT NULL,
    created_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE records (
    record_id TEXT PRIMARY KEY,
    record_schema TEXT NOT NULL,
    mode TEXT,
    surface TEXT,
    record_type TEXT NOT NULL,
    owner_principal_id TEXT REFERENCES principals(principal_id) ON DELETE SET NULL,
    owner_name_snapshot TEXT,
    guild_id TEXT,
    channel_id TEXT,
    started_at_us INTEGER NOT NULL,
    ended_at_us INTEGER NOT NULL,
    body TEXT NOT NULL,
    lineage_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    placeholder_id TEXT,
    deletion_reason TEXT,
    created_seq INTEGER NOT NULL UNIQUE,
    created_generation INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE record_parents (
    child_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
    parent_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
    PRIMARY KEY (child_id, parent_id),
    CHECK (child_id <> parent_id)
) WITHOUT ROWID;

CREATE TABLE record_receipts (
    idempotency_digest TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL,
    record_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
    created_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE retired_receipts (
    receipt_kind TEXT NOT NULL CHECK (
        receipt_kind IN ('record_idempotency', 'record_id', 'voice_idempotency')
    ),
    receipt_digest TEXT NOT NULL,
    payload_digest TEXT,
    retired_generation INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (receipt_kind, receipt_digest)
) WITHOUT ROWID;

CREATE TABLE participation_intervals (
    interval_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    owner_name_snapshot TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    interval_kind TEXT NOT NULL CHECK (interval_kind IN ('presence', 'eligible')),
    started_at_us INTEGER NOT NULL,
    ended_at_us INTEGER,
    CHECK (ended_at_us IS NULL OR ended_at_us >= started_at_us)
) WITHOUT ROWID;

CREATE TABLE voice_state (
    principal_id TEXT NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    present INTEGER NOT NULL,
    consent_current INTEGER NOT NULL,
    self_mute INTEGER NOT NULL,
    server_mute INTEGER NOT NULL,
    self_deaf INTEGER NOT NULL,
    server_deaf INTEGER NOT NULL,
    suppressed INTEGER NOT NULL,
    gateway_known INTEGER NOT NULL,
    presence_interval_id TEXT REFERENCES participation_intervals(interval_id) ON DELETE SET NULL,
    eligible_interval_id TEXT REFERENCES participation_intervals(interval_id) ON DELETE SET NULL,
    updated_at_us INTEGER NOT NULL,
    PRIMARY KEY (principal_id, guild_id)
) WITHOUT ROWID;

CREATE TABLE voice_state_transitions (
    transition_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    owner_name_snapshot TEXT NOT NULL,
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    event_at_us INTEGER NOT NULL,
    present INTEGER NOT NULL,
    consent_current INTEGER NOT NULL,
    self_mute INTEGER NOT NULL,
    server_mute INTEGER NOT NULL,
    self_deaf INTEGER NOT NULL,
    server_deaf INTEGER NOT NULL,
    suppressed INTEGER NOT NULL,
    gateway_known INTEGER NOT NULL,
    idempotency_digest TEXT NOT NULL UNIQUE
) WITHOUT ROWID;

CREATE TABLE voice_sources (
    source_id TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    last_sequence INTEGER NOT NULL,
    activated_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE ingest_receipts (
    idempotency_digest TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    event_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE deletion_previews (
    preview_id TEXT PRIMARY KEY,
    preview_kind TEXT NOT NULL CHECK (preview_kind IN ('self', 'admin')),
    actor_lookup_digest TEXT,
    request_guild_id TEXT,
    admin_target_principal_id TEXT,
    admin_record_ids_json TEXT NOT NULL,
    scope_all INTEGER NOT NULL,
    started_at_us INTEGER,
    ended_at_us INTEGER,
    target_fingerprint TEXT NOT NULL,
    snapshot_generation INTEGER NOT NULL,
    created_at_us INTEGER NOT NULL,
    expires_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE used_previews (
    preview_id TEXT PRIMARY KEY,
    consumed_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE tombstone_audiences (
    placeholder_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE,
    guild_id TEXT NOT NULL,
    PRIMARY KEY (placeholder_id, principal_id, guild_id)
) WITHOUT ROWID;

CREATE TABLE deletion_audits (
    request_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    primary_status TEXT NOT NULL,
    replica_status TEXT NOT NULL,
    display_text TEXT NOT NULL,
    requested_at_us INTEGER NOT NULL,
    completed_at_us INTEGER,
    deletion_generation INTEGER NOT NULL,
    required_sinks_json TEXT NOT NULL,
    completed_sinks_json TEXT NOT NULL,
    purge_scope_json TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE legal_minimal_events (
    event_id TEXT PRIMARY KEY,
    owner_name TEXT NOT NULL,
    occurred_at_us INTEGER NOT NULL
) WITHOUT ROWID;

CREATE INDEX records_owner_time_idx
    ON records(owner_principal_id, guild_id, started_at_us, ended_at_us);
CREATE INDEX records_status_expiry_idx
    ON records(status, ended_at_us, created_seq);
CREATE INDEX record_parents_parent_idx
    ON record_parents(parent_id, child_id);
CREATE INDEX record_receipts_record_idx ON record_receipts(record_id);
CREATE INDEX intervals_owner_time_idx
    ON participation_intervals(principal_id, guild_id, started_at_us, ended_at_us);
CREATE INDEX intervals_expiry_idx
    ON participation_intervals(ended_at_us, started_at_us);
CREATE INDEX voice_transitions_owner_time_idx
    ON voice_state_transitions(principal_id, guild_id, event_at_us, transition_id);
CREATE INDEX voice_transitions_expiry_idx
    ON voice_state_transitions(event_at_us, transition_id);
CREATE INDEX ingest_receipts_owner_time_idx
    ON ingest_receipts(principal_id, event_at_us);
CREATE INDEX previews_expiry_idx ON deletion_previews(expires_at_us);
CREATE INDEX tombstone_audience_idx
    ON tombstone_audiences(principal_id, guild_id, placeholder_id);
CREATE INDEX legal_minimal_events_time_idx
    ON legal_minimal_events(occurred_at_us, event_id);
"""


class ConversationArchiveError(RuntimeError):
    """Stable, content-free base failure for the private archive."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


class ArchiveValidationError(ConversationArchiveError):
    pass


class ArchiveIntegrityError(ConversationArchiveError):
    pass


class ArchiveUnavailableError(ConversationArchiveError):
    pass


class ArchiveAuthorizationError(ConversationArchiveError):
    pass


class ArchivePreviewExpired(ConversationArchiveError):
    pass


class ArchivePreviewConsumed(ConversationArchiveError):
    pass


class ArchivePreviewConflict(ConversationArchiveError):
    pass


class ArchiveStaleEvent(ConversationArchiveError):
    pass


@dataclass(frozen=True)
class ArchiveRecord:
    record_id: str
    mode: str | None
    surface: str | None
    record_type: str
    owner_principal_id: str | None
    owner_name: str | None
    guild_id: str | None
    channel_id: str | None
    started_at: datetime
    ended_at: datetime
    body: str
    status: str
    deletion_reason: str | None
    created_sequence: int
    created_generation: int


@dataclass(frozen=True)
class ArchiveRecordPage:
    records: tuple[ArchiveRecord, ...]
    next_cursor: str | None
    snapshot_generation: int


@dataclass(frozen=True)
class FeedbackSourceBinding:
    record_id: str
    record_type: str
    mode: str
    surface: str
    guild_id: str | None
    channel_id: str | None
    owner_principal_id: str
    archive_generation: int


@dataclass(frozen=True)
class ParticipationInterval:
    interval_id: str
    principal_id: str
    owner_name: str
    guild_id: str
    channel_id: str
    interval_kind: str
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class ParticipationIntervalPage:
    intervals: tuple[ParticipationInterval, ...]
    next_cursor: str | None
    snapshot_generation: int


@dataclass(frozen=True)
class VoiceStateTransition:
    transition_id: str
    principal_id: str
    owner_name: str
    guild_id: str
    channel_id: str
    event_at: datetime
    present: bool
    consent_current: bool
    self_mute: bool
    server_mute: bool
    self_deaf: bool
    server_deaf: bool
    suppressed: bool
    gateway_known: bool


@dataclass(frozen=True)
class VoiceStateTransitionPage:
    transitions: tuple[VoiceStateTransition, ...]
    next_cursor: str | None
    snapshot_generation: int


@dataclass(frozen=True)
class LegalMinimalEvent:
    event_id: str
    owner_name: str
    occurred_at: datetime


@dataclass(frozen=True)
class LegalMinimalEventPage:
    events: tuple[LegalMinimalEvent, ...]
    next_cursor: str | None
    snapshot_generation: int


@dataclass(frozen=True)
class DeletionPreview:
    preview_id: str
    expires_at: datetime
    snapshot_generation: int
    counts_by_guild: Mapping[str, int]
    owned_record_count: int
    dependent_record_count: int
    interval_count: int
    all_guilds: bool


@dataclass(frozen=True)
class DeletionResult:
    request_id: str
    status: str
    primary_status: str
    replica_status: str
    affected_records: int
    dependent_records: int
    affected_intervals: int
    display_text: str


@dataclass(frozen=True)
class DeletionPurgeWorkOrder:
    request_id: str
    reason: str
    requested_at: datetime
    deletion_generation: int
    principal_id: str | None
    owned_record_ids: tuple[str, ...]
    dependent_record_ids: tuple[str, ...]
    interval_ids: tuple[str, ...]
    scope_all: bool
    guild_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    required_sinks: tuple[str, ...]
    principal_ids: tuple[str, ...] = ()
    principal_lookup_digests: tuple[str, ...] = ()
    lineage_handles: tuple[tuple[str, str], ...] = ()
    lineage_complete: bool = False
    transition_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetentionResult:
    request_id: str
    status: str
    affected_records: int
    dependent_records: int
    affected_intervals: int


@dataclass(frozen=True)
class ArchiveHealth:
    status: str
    generation: int
    backup_pending_since: datetime | None
    writes_allowed: bool


@dataclass(frozen=True)
class _DeletionTargets:
    principal_id: str | None
    owned_record_ids: tuple[str, ...]
    dependent_record_ids: tuple[str, ...]
    interval_ids: tuple[str, ...]
    transition_ids: tuple[str, ...]
    counts_by_guild: Mapping[str, int]
    scope_all: bool
    guild_id: str | None
    started_at_us: int | None
    ended_at_us: int | None
    principal_ids: tuple[str, ...]
    principal_lookup_digests: tuple[str, ...]
    lineage_handles: tuple[tuple[str, str], ...]
    lineage_complete: bool


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def archive_lineage_handle(
    integrity_key: bytes,
    kind: str,
    raw_value: str,
) -> str:
    """Return the content-free, domain-separated handle stored by the archive."""

    key = bytes(integrity_key)
    if len(key) < _MIN_INTEGRITY_KEY_BYTES:
        raise ArchiveIntegrityError("archive_integrity_key_invalid")
    if kind not in ARCHIVE_LINEAGE_KINDS:
        raise ArchiveValidationError("archive_lineage_kind_invalid")
    value = _require_text(
        raw_value,
        code="archive_lineage_value_invalid",
        maximum=256,
    )
    digest = hmac.new(key, digestmod=hashlib.sha256)
    digest.update(_LINEAGE_DOMAIN)
    digest.update(kind.encode("ascii"))
    digest.update(b"\n")
    digest.update(_canonical_json({"value": value}))
    return digest.hexdigest()


def _lineage_handles_from_raw(
    integrity_key: bytes,
    lineage: Mapping[str, Iterable[str]] | None,
) -> tuple[tuple[str, str], ...]:
    if lineage is None:
        return ()
    if not isinstance(lineage, Mapping):
        raise ArchiveValidationError("archive_lineage_invalid")
    handles: set[tuple[str, str]] = set()
    for kind, values in lineage.items():
        if kind not in ARCHIVE_LINEAGE_KINDS or isinstance(
            values, (str, bytes)
        ):
            raise ArchiveValidationError("archive_lineage_invalid")
        try:
            raw_values = tuple(values)
        except TypeError:
            raise ArchiveValidationError("archive_lineage_invalid") from None
        if len(raw_values) > 32:
            raise ArchiveValidationError("archive_lineage_invalid")
        for value in raw_values:
            if not isinstance(value, str):
                raise ArchiveValidationError("archive_lineage_invalid")
            handles.add(
                (
                    kind,
                    archive_lineage_handle(integrity_key, kind, value),
                )
            )
    if len(handles) > 96:
        raise ArchiveValidationError("archive_lineage_invalid")
    return tuple(sorted(handles))


def _lineage_handles_json(
    handles: Iterable[tuple[str, str]],
) -> str:
    return json.dumps(
        [
            {"kind": kind, "digest": digest}
            for kind, digest in sorted(set(handles))
        ],
        sort_keys=True,
        separators=(",", ":"),
    )


def _lineage_handles_from_json(value: object) -> tuple[tuple[str, str], ...]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ArchiveIntegrityError("archive_lineage_invalid") from None
    if not isinstance(parsed, list) or len(parsed) > 96:
        raise ArchiveIntegrityError("archive_lineage_invalid")
    handles: list[tuple[str, str]] = []
    for item in parsed:
        if (
            not isinstance(item, dict)
            or set(item) != {"kind", "digest"}
            or item.get("kind") not in ARCHIVE_LINEAGE_KINDS
            or not ConversationArchive._sha256_text(item.get("digest"))
        ):
            raise ArchiveIntegrityError("archive_lineage_invalid")
        handles.append((str(item["kind"]), str(item["digest"])))
    normalized = tuple(sorted(set(handles)))
    if len(normalized) != len(handles):
        raise ArchiveIntegrityError("archive_lineage_invalid")
    return normalized


def _require_text(value: Any, *, code: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise ArchiveValidationError(code)
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or "\x00" in normalized:
        raise ArchiveValidationError(code)
    return normalized


def _normalize_owner_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ArchiveValidationError("archive_owner_name_invalid")
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > _MAX_OWNER_NAME_CHARACTERS
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized)
    ):
        raise ArchiveValidationError("archive_owner_name_invalid")
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise ArchiveValidationError("archive_owner_name_invalid") from None
    if len(encoded) > _MAX_OWNER_NAME_BYTES:
        raise ArchiveValidationError("archive_owner_name_invalid")
    return normalized


def _as_utc_us(value: datetime, *, code: str) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ArchiveValidationError(code)
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _from_utc_us(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)


def _minute_floor(value: int) -> int:
    return value - (value % 60_000_000)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _unlink_required(path: Path) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()


def _fsync_file(path: Path) -> None:
    # Windows' CRT rejects fsync on a descriptor opened read-only.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class ConversationArchive:
    """Sole-writer SQLite archive for private conversation evidence.

    The class deliberately owns both the primary transaction and the verified
    online-backup copy.  Callers submit typed final events; there is no generic
    blob/log API that could accept raw audio, partial STT, or prompts.
    """

    def __init__(
        self,
        *,
        primary_path: Path,
        replica_path: Path,
        anchor_path: Path | None,
        integrity_key: bytes,
        lineage_key: bytes | None = None,
        clock: Callable[[], datetime] | None = None,
        retention_days: int = ARCHIVE_RETENTION_DAYS,
        backup_grace_seconds: int = ARCHIVE_BACKUP_GRACE_SECONDS,
        writer_lock_wait_seconds: float = 0.0,
        allow_unanchored_test_mode: bool = False,
        required_purge_sinks: Iterable[str] = (),
        purge_freeze: Callable[[DeletionPurgeWorkOrder], None] | None = None,
    ) -> None:
        if not isinstance(integrity_key, bytes) or len(integrity_key) < _MIN_INTEGRITY_KEY_BYTES:
            raise ArchiveIntegrityError("archive_integrity_key_invalid")
        if lineage_key is not None and (
            not isinstance(lineage_key, bytes)
            or len(lineage_key) < _MIN_INTEGRITY_KEY_BYTES
        ):
            raise ArchiveIntegrityError("archive_lineage_key_invalid")
        self.primary_path = Path(primary_path)
        self.replica_path = Path(replica_path)
        self.anchor_path = None if anchor_path is None else Path(anchor_path)
        if self.anchor_path is None and not allow_unanchored_test_mode:
            raise ArchiveIntegrityError("archive_anchor_required")
        if self.anchor_path is not None and self.anchor_path in {
            self.primary_path,
            self.replica_path,
        }:
            raise ArchiveValidationError("archive_anchor_must_be_distinct")
        if self.primary_path == self.replica_path:
            raise ArchiveValidationError("archive_replica_must_be_distinct")
        if retention_days <= 0 or backup_grace_seconds < 0:
            raise ArchiveValidationError("archive_policy_invalid")
        self._key = bytes(integrity_key)
        self._lineage_key_explicit = lineage_key is not None
        self._lineage_key = bytes(
            integrity_key if lineage_key is None else lineage_key
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._retention = timedelta(days=retention_days)
        self._backup_grace_seconds = int(backup_grace_seconds)
        self._writer_lock_wait_seconds = float(writer_lock_wait_seconds)
        normalized_sinks = tuple(
            sorted(
                {
                    _require_text(
                        sink,
                        code="archive_purge_sink_invalid",
                        maximum=64,
                    )
                    for sink in required_purge_sinks
                }
            )
        )
        if any(sink not in ARCHIVE_REQUIRED_PURGE_SINKS for sink in normalized_sinks):
            raise ArchiveValidationError("archive_purge_sink_invalid")
        if purge_freeze is not None and not callable(purge_freeze):
            raise ArchiveValidationError("archive_purge_freeze_invalid")
        self._required_purge_sinks = normalized_sinks
        self._purge_freeze = purge_freeze
        self._thread_lock = threading.RLock()
        self._lock_manager: InstanceLockManager | None = None
        self._opened = False
        self._fault: str | None = None

    def __enter__(self) -> "ConversationArchive":
        return self.open()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    @property
    def generation(self) -> int:
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                return self._metadata_int(connection, "generation")

    def open(self) -> "ConversationArchive":
        with self._thread_lock:
            if self._opened:
                return self
            self.primary_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.replica_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                # A detached/read-only backup volume enters the persisted
                # primary-only grace period after the first copy attempt.
                pass
            if self.anchor_path is not None:
                try:
                    self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
                except OSError:
                    raise ArchiveUnavailableError("anchor_unavailable") from None
            if (
                self.primary_path.is_symlink()
                or self.replica_path.is_symlink()
                or (self.anchor_path is not None and self.anchor_path.is_symlink())
            ):
                raise ArchiveUnavailableError("archive_path_rejected")
            self._lock_manager = InstanceLockManager(
                build_instance_lock_runtime_deps(
                    self.primary_path.with_name(self.primary_path.name + ".writer.lock")
                )
            )
            try:
                try:
                    self._lock_manager.acquire(
                        wait_sec=self._writer_lock_wait_seconds
                    )
                except RuntimeError:
                    raise ArchiveUnavailableError("writer_lease_lost") from None
                if self.primary_path.exists():
                    if self._database_user_version(self.primary_path) == (
                        _LEGACY_ARCHIVE_SCHEMA_VERSION
                    ):
                        self._migrate_legacy_primary()
                    else:
                        self._verify_primary()
                else:
                    if self.anchor_path is not None and self.anchor_path.exists():
                        raise ArchiveIntegrityError("archive_anchor_primary_missing")
                    self._initialize_primary()
                self._opened = True
                self._inspect_or_reconcile_replica(self._now_us())
                if self._fault is None:
                    with closing(
                        self._connect(self.primary_path, read_only=True)
                    ) as connection:
                        pending = connection.execute(
                            "SELECT 1 FROM deletion_audits "
                            "WHERE status = 'local_cleanup_pending' LIMIT 1"
                        ).fetchone()
                    if pending is not None:
                        self._fault = "local_cleanup_pending"
            except Exception:
                self._opened = False
                self._lock_manager.release()
                self._lock_manager = None
                raise
            return self

    def close(self) -> None:
        with self._thread_lock:
            if self._lock_manager is not None:
                self._lock_manager.release()
            self._lock_manager = None
            self._opened = False

    def _require_open(self) -> None:
        if not self._opened:
            raise ArchiveUnavailableError("archive_closed")

    def _now_us(self, value: datetime | None = None) -> int:
        return _as_utc_us(
            value if value is not None else self._clock(),
            code="archive_time_invalid",
        )

    @staticmethod
    def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"{path.resolve(strict=True).as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(str(path), timeout=5.0)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA secure_delete=ON")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @classmethod
    def _database_user_version(cls, path: Path) -> int:
        try:
            with closing(cls._connect(path, read_only=True)) as connection:
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
        except (OSError, sqlite3.Error, TypeError, ValueError):
            raise ArchiveIntegrityError("archive_primary_unreadable") from None

    def _initialize_primary(self) -> None:
        connection = self._connect(self.primary_path)
        try:
            connection.executescript(_SCHEMA_SQL)
            connection.execute(f"PRAGMA user_version={ARCHIVE_SCHEMA_VERSION}")
            initial_metadata = {
                "schema_version": str(ARCHIVE_SCHEMA_VERSION),
                "auth_key_id": hashlib.sha256(self._key).hexdigest()[:16],
                "lineage_key_id": hashlib.sha256(self._lineage_key).hexdigest()[
                    :16
                ],
                "generation": "0",
                "next_record_sequence": "1",
                "minimum_restorable_generation": "0",
                "backup_pending_since_us": "",
                "chain_epoch": "1",
                "chain_prev": _CHAIN_GENESIS,
                "chain_nonce": secrets.token_hex(16),
                "chain_kind": "initialize",
                "chain_head": "",
                "cutover_generation": "0",
                "cutover_epoch": "1",
                "cutover_nonce": "",
                "state_tag": "",
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                tuple(initial_metadata.items()),
            )
            self._set_metadata(
                connection,
                "chain_head",
                self._expected_chain_head(connection),
            )
            self._set_metadata(
                connection,
                "state_tag",
                self._expected_state_tag(connection),
            )
            self._commit_and_anchor(connection)
        except Exception:
            connection.rollback()
            connection.close()
            _safe_unlink(self.primary_path)
            if self.anchor_path is not None:
                _safe_unlink(self.anchor_path)
            raise
        finally:
            try:
                connection.close()
            except Exception:
                pass
        _fsync_file(self.primary_path)
        _fsync_directory(self.primary_path.parent)
        self._verify_database(self.primary_path)

    @staticmethod
    def _metadata(connection: sqlite3.Connection, key: str) -> str:
        rows = connection.execute(
            "SELECT value FROM metadata WHERE key = ? LIMIT 2", (key,)
        ).fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str):
            raise ArchiveIntegrityError("archive_metadata_invalid")
        return rows[0][0]

    @classmethod
    def _metadata_int(cls, connection: sqlite3.Connection, key: str) -> int:
        value = cls._metadata(connection, key)
        if not value.isascii() or not value.isdigit():
            raise ArchiveIntegrityError("archive_metadata_invalid")
        number = int(value)
        if number < 0 or number > (1 << 63) - 1:
            raise ArchiveIntegrityError("archive_metadata_invalid")
        return number

    @staticmethod
    def _set_metadata(connection: sqlite3.Connection, key: str, value: Any) -> None:
        cursor = connection.execute(
            "UPDATE metadata SET value = ? WHERE key = ?", (str(value), key)
        )
        if cursor.rowcount != 1:
            raise ArchiveIntegrityError("archive_metadata_invalid")

    def _verify_lineage_key_id(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        rows = connection.execute(
            "SELECT value FROM metadata WHERE key = 'lineage_key_id' LIMIT 2"
        ).fetchall()
        if rows:
            if (
                len(rows) != 1
                or not isinstance(rows[0][0], str)
                or not hmac.compare_digest(
                    rows[0][0],
                    hashlib.sha256(self._lineage_key).hexdigest()[:16],
                )
            ):
                raise ArchiveIntegrityError("archive_lineage_key_mismatch")
        elif self._lineage_key_explicit:
            # Existing opaque handles cannot be re-keyed without recovering
            # raw identifiers that the archive intentionally does not retain.
            raise ArchiveIntegrityError(
                "archive_lineage_key_migration_required"
            )

    def _hmac(self, domain: bytes, value: Any) -> str:
        digest = hmac.new(self._key, digestmod=hashlib.sha256)
        digest.update(domain)
        digest.update(_canonical_json(value))
        return digest.hexdigest()

    @staticmethod
    def _sha256_text(value: Any) -> bool:
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    def _anchor_unsigned_from_connection(
        self, connection: sqlite3.Connection
    ) -> dict[str, Any]:
        return {
            "schema": ARCHIVE_ANCHOR_SCHEMA,
            "authAlgorithm": "hmac-sha256",
            "authKeyId": hashlib.sha256(self._key).hexdigest()[:16],
            "generation": self._metadata_int(connection, "generation"),
            "chainEpoch": self._metadata_int(connection, "chain_epoch"),
            "chainHead": self._metadata(connection, "chain_head"),
            "stateTag": self._metadata(connection, "state_tag"),
            "minimumRestorableGeneration": self._metadata_int(
                connection, "minimum_restorable_generation"
            ),
            "cutoverWitness": {
                "contentFree": True,
                "generation": self._metadata_int(
                    connection, "cutover_generation"
                ),
                "chainEpoch": self._metadata_int(connection, "cutover_epoch"),
                "nonce": self._metadata(connection, "cutover_nonce"),
            },
        }

    def _read_anchor(self) -> dict[str, Any] | None:
        if self.anchor_path is None:
            return None
        try:
            if self.anchor_path.is_symlink() or not self.anchor_path.is_file():
                raise ArchiveIntegrityError("anchor_unavailable")
            raw = self.anchor_path.read_bytes()
        except ConversationArchiveError:
            raise
        except OSError:
            raise ArchiveIntegrityError("anchor_unavailable") from None
        if not raw or len(raw) > 16 * 1024:
            raise ArchiveIntegrityError("anchor_integrity_blocked")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ArchiveIntegrityError("anchor_integrity_blocked") from None
        expected_keys = {
            "schema",
            "authAlgorithm",
            "authKeyId",
            "generation",
            "chainEpoch",
            "chainHead",
            "stateTag",
            "minimumRestorableGeneration",
            "cutoverWitness",
            "authTag",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ArchiveIntegrityError("anchor_integrity_blocked")
        supplied_tag = payload.get("authTag")
        unsigned = {key: value for key, value in payload.items() if key != "authTag"}
        expected_tag = self._hmac(_ANCHOR_DOMAIN, unsigned)
        witness = payload.get("cutoverWitness")
        if (
            payload.get("schema") != ARCHIVE_ANCHOR_SCHEMA
            or payload.get("authAlgorithm") != "hmac-sha256"
            or payload.get("authKeyId")
            != hashlib.sha256(self._key).hexdigest()[:16]
            or type(payload.get("generation")) is not int
            or type(payload.get("chainEpoch")) is not int
            or type(payload.get("minimumRestorableGeneration")) is not int
            or not self._sha256_text(payload.get("chainHead"))
            or not self._sha256_text(payload.get("stateTag"))
            or not self._sha256_text(supplied_tag)
            or not hmac.compare_digest(str(supplied_tag), expected_tag)
            or not isinstance(witness, dict)
            or set(witness) != {"contentFree", "generation", "chainEpoch", "nonce"}
            or witness.get("contentFree") is not True
            or type(witness.get("generation")) is not int
            or type(witness.get("chainEpoch")) is not int
            or not isinstance(witness.get("nonce"), str)
            or len(witness["nonce"]) > 64
        ):
            raise ArchiveIntegrityError("anchor_integrity_blocked")
        return payload

    def _write_anchor_from_primary(self) -> None:
        if self.anchor_path is None:
            return
        self._verify_database(self.primary_path)
        with closing(self._connect(self.primary_path, read_only=True)) as connection:
            unsigned = self._anchor_unsigned_from_connection(connection)
        payload = {
            **unsigned,
            "authTag": self._hmac(_ANCHOR_DOMAIN, unsigned),
        }
        encoded = _canonical_json(payload) + b"\n"
        staging = self.anchor_path.with_name(
            f".{self.anchor_path.name}.staging-{uuid.uuid4().hex}"
        )
        try:
            if self.anchor_path.is_symlink():
                raise ArchiveUnavailableError("anchor_unavailable")
            with staging.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, self.anchor_path)
            _fsync_directory(self.anchor_path.parent)
        except ConversationArchiveError:
            self._fault = "anchor_unavailable"
            raise
        except OSError:
            self._fault = "anchor_unavailable"
            raise ArchiveUnavailableError("anchor_unavailable") from None
        finally:
            _safe_unlink(staging)
        self._read_anchor()

    def _verify_anchor_against_primary(self) -> None:
        if self.anchor_path is None:
            return
        anchor = self._read_anchor()
        assert anchor is not None
        with closing(self._connect(self.primary_path, read_only=True)) as connection:
            expected = self._anchor_unsigned_from_connection(connection)
        if anchor["generation"] < expected["generation"]:
            raise ArchiveIntegrityError("anchor_stale_commit_unknown")
        if anchor["generation"] > expected["generation"]:
            raise ArchiveIntegrityError("anchor_rollback_detected")
        for key in (
            "chainEpoch",
            "chainHead",
            "stateTag",
            "minimumRestorableGeneration",
            "cutoverWitness",
        ):
            if anchor[key] != expected[key]:
                raise ArchiveIntegrityError("anchor_database_mismatch")

    def _commit_and_anchor(self, connection: sqlite3.Connection) -> None:
        connection.commit()
        try:
            self._write_anchor_from_primary()
        except ConversationArchiveError:
            self._fault = "anchor_unavailable"
            raise

    def _expected_chain_head(self, connection: sqlite3.Connection) -> str:
        payload = {
            "epoch": self._metadata_int(connection, "chain_epoch"),
            "generation": self._metadata_int(connection, "generation"),
            "previous": self._metadata(connection, "chain_prev"),
            "nonce": self._metadata(connection, "chain_nonce"),
            "kind": self._metadata(connection, "chain_kind"),
        }
        return self._hmac(_CHAIN_DOMAIN, payload)

    @staticmethod
    def _state_rows(
        connection: sqlite3.Connection,
        table: str,
        columns: Sequence[str],
    ) -> list[list[Any]]:
        select = ", ".join(columns)
        order = ", ".join(columns)
        rows = connection.execute(f"SELECT {select} FROM {table} ORDER BY {order}").fetchall()
        return [[row[column] for column in columns] for row in rows]

    def _state_payload_for_columns(
        self,
        connection: sqlite3.Connection,
        table_columns: Mapping[str, tuple[str, ...]],
    ) -> Mapping[str, Any]:
        metadata_rows = connection.execute(
            "SELECT key, value FROM metadata WHERE key <> 'state_tag' ORDER BY key"
        ).fetchall()
        return {
            "metadata": [[row["key"], row["value"]] for row in metadata_rows],
            "tables": {
                table: self._state_rows(connection, table, columns)
                for table, columns in table_columns.items()
            },
        }

    def _state_payload(self, connection: sqlite3.Connection) -> Mapping[str, Any]:
        return self._state_payload_for_columns(
            connection,
            _STATE_TABLE_COLUMNS,
        )

    def _expected_state_tag(self, connection: sqlite3.Connection) -> str:
        return self._hmac(_STATE_DOMAIN, self._state_payload(connection))

    def _verify_legacy_database(
        self,
        path: Path,
        *,
        verify_anchor: bool = False,
    ) -> tuple[int, str]:
        try:
            if path.is_symlink() or not path.is_file():
                raise ArchiveIntegrityError("archive_database_path_rejected")
            with closing(self._connect(path, read_only=True)) as connection:
                if int(connection.execute("PRAGMA user_version").fetchone()[0]) != (
                    _LEGACY_ARCHIVE_SCHEMA_VERSION
                ):
                    raise ArchiveIntegrityError("archive_schema_invalid")
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if table_names != _LEGACY_EXPECTED_TABLES:
                    raise ArchiveIntegrityError("archive_schema_invalid")
                integrity = connection.execute("PRAGMA integrity_check(1)").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise ArchiveIntegrityError("archive_primary_unreadable")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ArchiveIntegrityError("archive_integrity_blocked")
                if self._metadata(connection, "schema_version") != str(
                    _LEGACY_ARCHIVE_SCHEMA_VERSION
                ):
                    raise ArchiveIntegrityError("archive_schema_invalid")
                expected_key_id = hashlib.sha256(self._key).hexdigest()[:16]
                if not hmac.compare_digest(
                    self._metadata(connection, "auth_key_id"), expected_key_id
                ):
                    raise ArchiveIntegrityError("archive_integrity_key_mismatch")
                self._verify_lineage_key_id(connection)
                if not hmac.compare_digest(
                    self._metadata(connection, "chain_head"),
                    self._expected_chain_head(connection),
                ):
                    raise ArchiveIntegrityError("archive_integrity_blocked")
                supplied_tag = self._metadata(connection, "state_tag")
                expected_tag = self._hmac(
                    _STATE_DOMAIN,
                    self._state_payload_for_columns(
                        connection,
                        _LEGACY_STATE_TABLE_COLUMNS,
                    ),
                )
                if not hmac.compare_digest(supplied_tag, expected_tag):
                    raise ArchiveIntegrityError("archive_integrity_blocked")
                snapshot = (
                    self._metadata_int(connection, "generation"),
                    supplied_tag,
                )
            if verify_anchor:
                self._verify_anchor_against_primary()
            return snapshot
        except ConversationArchiveError:
            raise
        except OSError:
            raise ArchiveIntegrityError("archive_database_unavailable") from None
        except (sqlite3.Error, TypeError, ValueError):
            raise ArchiveIntegrityError("archive_primary_unreadable") from None

    def _migrate_legacy_primary(self) -> None:
        self._verify_legacy_database(self.primary_path, verify_anchor=True)
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE voice_state_transitions ("
                "transition_id TEXT PRIMARY KEY, "
                "principal_id TEXT NOT NULL REFERENCES principals(principal_id) ON DELETE CASCADE, "
                "owner_name_snapshot TEXT NOT NULL, guild_id TEXT NOT NULL, "
                "channel_id TEXT NOT NULL, event_at_us INTEGER NOT NULL, "
                "present INTEGER NOT NULL, consent_current INTEGER NOT NULL, "
                "self_mute INTEGER NOT NULL, server_mute INTEGER NOT NULL, "
                "self_deaf INTEGER NOT NULL, server_deaf INTEGER NOT NULL, "
                "suppressed INTEGER NOT NULL, gateway_known INTEGER NOT NULL, "
                "idempotency_digest TEXT NOT NULL UNIQUE) WITHOUT ROWID"
            )
            connection.execute(
                "CREATE INDEX voice_transitions_owner_time_idx ON "
                "voice_state_transitions(principal_id, guild_id, event_at_us, transition_id)"
            )
            connection.execute(
                "CREATE INDEX voice_transitions_expiry_idx ON "
                "voice_state_transitions(event_at_us, transition_id)"
            )
            connection.execute(f"PRAGMA user_version={ARCHIVE_SCHEMA_VERSION}")
            self._set_metadata(connection, "schema_version", ARCHIVE_SCHEMA_VERSION)
            self._commit_mutation(connection, kind="schema_migrated")
            self._commit_and_anchor(connection)
        _fsync_file(self.primary_path)
        _fsync_directory(self.primary_path.parent)
        self._verify_primary()

    def _verify_database(self, path: Path) -> tuple[int, str]:
        try:
            if path.is_symlink() or not path.is_file():
                raise ArchiveIntegrityError("archive_database_path_rejected")
            with closing(self._connect(path, read_only=True)) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version != ARCHIVE_SCHEMA_VERSION:
                    raise ArchiveIntegrityError("archive_schema_invalid")
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if table_names != _EXPECTED_TABLES:
                    raise ArchiveIntegrityError("archive_schema_invalid")
                integrity = connection.execute("PRAGMA integrity_check(1)").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise ArchiveIntegrityError("archive_primary_unreadable")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ArchiveIntegrityError("archive_integrity_blocked")
                if self._metadata(connection, "schema_version") != str(ARCHIVE_SCHEMA_VERSION):
                    raise ArchiveIntegrityError("archive_schema_invalid")
                expected_key_id = hashlib.sha256(self._key).hexdigest()[:16]
                if not hmac.compare_digest(
                    self._metadata(connection, "auth_key_id"), expected_key_id
                ):
                    raise ArchiveIntegrityError("archive_integrity_key_mismatch")
                self._verify_lineage_key_id(connection)
                supplied_head = self._metadata(connection, "chain_head")
                expected_head = self._expected_chain_head(connection)
                if not hmac.compare_digest(supplied_head, expected_head):
                    raise ArchiveIntegrityError("archive_integrity_blocked")
                supplied_tag = self._metadata(connection, "state_tag")
                expected_tag = self._expected_state_tag(connection)
                if not hmac.compare_digest(supplied_tag, expected_tag):
                    raise ArchiveIntegrityError("archive_integrity_blocked")
                return self._metadata_int(connection, "generation"), supplied_tag
        except ConversationArchiveError:
            raise
        except OSError:
            raise ArchiveIntegrityError("archive_database_unavailable") from None
        except (sqlite3.Error, TypeError, ValueError):
            raise ArchiveIntegrityError("archive_primary_unreadable") from None

    def _verify_primary(self) -> tuple[int, str]:
        try:
            snapshot = self._verify_database(self.primary_path)
            self._verify_anchor_against_primary()
            if self._fault in {
                "anchor_unavailable",
                "commit_unknown",
                "integrity_blocked",
                "primary_unreadable",
                "primary_write_rejected",
            }:
                self._fault = None
            return snapshot
        except ArchiveIntegrityError as exc:
            if exc.code in {"archive_database_unavailable", "archive_primary_unreadable"}:
                self._fault = "primary_unreadable"
            elif exc.code == "anchor_unavailable":
                self._fault = "anchor_unavailable"
            elif exc.code == "anchor_stale_commit_unknown":
                self._fault = "commit_unknown"
            else:
                self._fault = "integrity_blocked"
            raise

    def _commit_mutation(
        self,
        connection: sqlite3.Connection,
        *,
        kind: str,
        reset_chain: bool = False,
    ) -> int:
        current_generation = self._metadata_int(connection, "generation")
        next_generation = current_generation + 1
        old_head = self._metadata(connection, "chain_head")
        if reset_chain:
            self._set_metadata(
                connection,
                "chain_epoch",
                self._metadata_int(connection, "chain_epoch") + 1,
            )
            old_head = _CHAIN_GENESIS
        nonce = secrets.token_hex(16)
        self._set_metadata(connection, "generation", next_generation)
        self._set_metadata(connection, "chain_prev", old_head)
        self._set_metadata(connection, "chain_nonce", nonce)
        self._set_metadata(connection, "chain_kind", kind)
        if reset_chain:
            self._set_metadata(
                connection, "cutover_generation", next_generation
            )
            self._set_metadata(
                connection,
                "cutover_epoch",
                self._metadata_int(connection, "chain_epoch"),
            )
            self._set_metadata(connection, "cutover_nonce", nonce)
        self._set_metadata(connection, "chain_head", self._expected_chain_head(connection))
        self._set_metadata(connection, "state_tag", self._expected_state_tag(connection))
        return next_generation

    def _principal_lookup(self, identity_surface: str, external_id: str) -> str:
        surface = _require_text(
            identity_surface,
            code="archive_identity_surface_invalid",
            maximum=32,
        )
        if surface not in {"discord", "local"}:
            raise ArchiveValidationError("archive_identity_surface_invalid")
        actor = _require_text(external_id, code="archive_actor_invalid", maximum=256)
        return self._hmac(_PRINCIPAL_DOMAIN, {"surface": surface, "externalId": actor})

    def _find_principal(
        self, connection: sqlite3.Connection, identity_surface: str, external_id: str
    ) -> str | None:
        lookup = self._principal_lookup(identity_surface, external_id)
        row = connection.execute(
            "SELECT principal_id FROM principals WHERE lookup_digest = ?", (lookup,)
        ).fetchone()
        return None if row is None else str(row[0])

    def _get_or_create_principal(
        self,
        connection: sqlite3.Connection,
        identity_surface: str,
        external_id: str,
        event_at_us: int,
        owner_name: str,
    ) -> str:
        lookup = self._principal_lookup(identity_surface, external_id)
        row = connection.execute(
            "SELECT principal_id, display_name_updated_at_us FROM principals "
            "WHERE lookup_digest = ?",
            (lookup,),
        ).fetchone()
        if row is not None:
            if event_at_us >= int(row["display_name_updated_at_us"]):
                connection.execute(
                    "UPDATE principals SET current_display_name = ?, "
                    "display_name_updated_at_us = ? WHERE principal_id = ?",
                    (owner_name, event_at_us, row["principal_id"]),
                )
            return str(row[0])
        principal_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO principals("
            "principal_id, identity_surface, lookup_digest, current_display_name, "
            "display_name_updated_at_us, created_at_us) VALUES (?, ?, ?, ?, ?, ?)",
            (
                principal_id,
                identity_surface,
                lookup,
                owner_name,
                event_at_us,
                event_at_us,
            ),
        )
        return principal_id

    def _pending_since_us(self) -> int | None:
        with closing(self._connect(self.primary_path, read_only=True)) as connection:
            raw = self._metadata(connection, "backup_pending_since_us")
        if not raw:
            return None
        if not raw.isascii() or not raw.isdigit():
            raise ArchiveIntegrityError("archive_metadata_invalid")
        return int(raw)

    def _ensure_writable(self, now_us: int, *, deletion: bool = False) -> None:
        self._require_open()
        self._verify_primary()
        if self._fault in {
            "commit_unknown",
            "primary_write_rejected",
            "writer_lease_lost",
            "integrity_blocked",
        }:
            raise ArchiveUnavailableError(self._fault)
        if self._fault == "backup_integrity_blocked" and not deletion:
            raise ArchiveUnavailableError("backup_integrity_blocked")
        pending_since = self._pending_since_us()
        if (
            not deletion
            and pending_since is not None
            and now_us - pending_since >= self._backup_grace_seconds * 1_000_000
        ):
            raise ArchiveUnavailableError("backup_grace_expired")

    def _ensure_principal_not_frozen(
        self,
        connection: sqlite3.Connection,
        lookup_digest: str | None,
    ) -> None:
        if lookup_digest is None:
            return
        rows = connection.execute(
            "SELECT * FROM deletion_audits "
            "WHERE status = 'local_cleanup_pending'"
        ).fetchall()
        for row in rows:
            if row["required_sinks_json"] == "[]":
                try:
                    scope = json.loads(str(row["purge_scope_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    scope = None
                if (
                    isinstance(scope, dict)
                    and scope.get("principalId") is None
                    and scope.get("principalIds") == []
                    and scope.get("principalLookupDigests") == []
                    and scope.get("lineageHandles") == []
                    and scope.get("lineageComplete") is False
                    and scope.get("ownedRecordIds") == []
                    and scope.get("dependentRecordIds") == []
                    and scope.get("intervalIds") == []
                    and scope.get("transitionIds") == []
                    and scope.get("scopeAll") is True
                    and scope.get("guildId") is None
                    and scope.get("startedAtUs") is None
                    and scope.get("endedAtUs") is None
                ):
                    # Legal-minimal-only retention has no principal or lineage
                    # target to freeze, but its core compaction/replica audit
                    # remains durable for reconcile_replica().
                    continue
            work_order = self._purge_work_order_from_row(row)
            if lookup_digest in work_order.principal_lookup_digests:
                raise ArchiveUnavailableError(
                    "archive_target_cleanup_pending"
                )

    def _snapshot(self, path: Path) -> tuple[int, str]:
        return self._verify_database(path)

    def _copy_primary_to_replica(self) -> None:
        primary_snapshot = self._verify_primary()
        if self.replica_path.exists():
            try:
                replica_snapshot = self._snapshot(self.replica_path)
            except ArchiveIntegrityError as exc:
                if exc.code == "archive_database_unavailable":
                    raise ArchiveUnavailableError("backup_pending") from None
                self._fault = "backup_integrity_blocked"
                raise ArchiveUnavailableError("backup_integrity_blocked") from None
            if replica_snapshot[0] > primary_snapshot[0] or (
                replica_snapshot[0] == primary_snapshot[0]
                and replica_snapshot[1] != primary_snapshot[1]
            ):
                self._fault = "backup_integrity_blocked"
                raise ArchiveUnavailableError("backup_integrity_blocked")

        staging = self.replica_path.with_name(
            f".{self.replica_path.name}.staging-{uuid.uuid4().hex}"
        )
        _safe_unlink(staging)
        try:
            with closing(self._connect(self.primary_path, read_only=True)) as source:
                destination = sqlite3.connect(str(staging), timeout=5.0)
                try:
                    source.backup(destination)
                    destination.commit()
                finally:
                    destination.close()
            _fsync_file(staging)
            if self._snapshot(staging) != primary_snapshot:
                raise ArchiveIntegrityError("archive_replica_verification_failed")
            _unlink_required(Path(str(self.replica_path) + "-wal"))
            _unlink_required(Path(str(self.replica_path) + "-shm"))
            os.replace(staging, self.replica_path)
            _fsync_directory(self.replica_path.parent)
            if self._snapshot(self.replica_path) != primary_snapshot:
                raise ArchiveIntegrityError("archive_replica_verification_failed")
        finally:
            _safe_unlink(staging)

    def _replace_legacy_replica(self) -> None:
        legacy_generation, _ = self._verify_legacy_database(self.replica_path)
        primary_snapshot = self._verify_primary()
        if legacy_generation >= primary_snapshot[0]:
            raise ArchiveIntegrityError("archive_replica_generation_invalid")
        staging = self.replica_path.with_name(
            f".{self.replica_path.name}.staging-{uuid.uuid4().hex}"
        )
        _safe_unlink(staging)
        try:
            with closing(self._connect(self.primary_path, read_only=True)) as source:
                destination = sqlite3.connect(str(staging), timeout=5.0)
                try:
                    source.backup(destination)
                    destination.commit()
                finally:
                    destination.close()
            _fsync_file(staging)
            if self._snapshot(staging) != primary_snapshot:
                raise ArchiveIntegrityError("archive_replica_verification_failed")
            _unlink_required(Path(str(self.replica_path) + "-wal"))
            _unlink_required(Path(str(self.replica_path) + "-shm"))
            os.replace(staging, self.replica_path)
            _fsync_directory(self.replica_path.parent)
            if self._snapshot(self.replica_path) != primary_snapshot:
                raise ArchiveIntegrityError("archive_replica_verification_failed")
        finally:
            _safe_unlink(staging)

    def _write_backup_pending(self, started_at_us: int) -> None:
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._metadata(connection, "backup_pending_since_us")
            if current:
                connection.rollback()
                return
            self._set_metadata(connection, "backup_pending_since_us", started_at_us)
            self._commit_mutation(connection, kind="backup_pending")
            self._commit_and_anchor(connection)

    def _clear_backup_pending(self, original_started_at_us: int) -> bool:
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._set_metadata(connection, "backup_pending_since_us", "")
            self._commit_mutation(connection, kind="backup_reconciled")
            self._commit_and_anchor(connection)
        try:
            self._copy_primary_to_replica()
            return True
        except (ArchiveUnavailableError, ArchiveIntegrityError, OSError, sqlite3.Error):
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._set_metadata(
                    connection,
                    "backup_pending_since_us",
                    original_started_at_us,
                )
                self._commit_mutation(connection, kind="backup_pending_restored")
                self._commit_and_anchor(connection)
            return False

    def _replicate_after_commit(self, now_us: int) -> bool:
        if self._fault == "backup_integrity_blocked":
            self._write_backup_pending(now_us)
            return False
        try:
            self._copy_primary_to_replica()
        except (ArchiveUnavailableError, ArchiveIntegrityError, OSError, sqlite3.Error):
            if self._fault != "backup_integrity_blocked":
                self._write_backup_pending(now_us)
            return False
        pending = self._pending_since_us()
        if pending is not None:
            return self._clear_backup_pending(pending)
        self._fault = None
        return True

    def _inspect_or_reconcile_replica(self, now_us: int) -> None:
        if not self.replica_path.exists():
            self._replicate_after_commit(now_us)
            return
        try:
            if self._database_user_version(self.replica_path) == (
                _LEGACY_ARCHIVE_SCHEMA_VERSION
            ):
                self._replace_legacy_replica()
                self._fault = None
                return
        except ArchiveIntegrityError:
            self._fault = "backup_integrity_blocked"
            return
        primary = self._verify_primary()
        try:
            replica = self._snapshot(self.replica_path)
        except ArchiveIntegrityError as exc:
            if exc.code == "archive_database_unavailable":
                self._replicate_after_commit(now_us)
                return
            self._fault = "backup_integrity_blocked"
            return
        if replica == primary:
            if self._pending_since_us() is None:
                self._fault = None
            else:
                self._replicate_after_commit(now_us)
            return
        if replica[0] < primary[0]:
            self._replicate_after_commit(now_us)
            return
        self._fault = "backup_integrity_blocked"

    def health(self, *, now: datetime | None = None) -> ArchiveHealth:
        with self._thread_lock:
            self._require_open()
            now_us = self._now_us(now)
            generation, _ = self._verify_primary()
            pending = self._pending_since_us()
            if self._fault == "backup_integrity_blocked":
                status = "backup_integrity_blocked"
                writes_allowed = False
            elif self._fault == "local_cleanup_pending":
                status = "local_cleanup_pending"
                # Cleanup is fenced by exact principal/record targets. It is
                # not a global integrity fault and must not stop unrelated
                # principals from creating new records.
                writes_allowed = True
            elif pending is None:
                status = "healthy"
                writes_allowed = True
            elif now_us - pending >= self._backup_grace_seconds * 1_000_000:
                status = "backup_grace_expired"
                writes_allowed = False
            else:
                status = "backup_pending"
                writes_allowed = True
            return ArchiveHealth(
                status=status,
                generation=generation,
                backup_pending_since=_from_utc_us(pending),
                writes_allowed=writes_allowed,
            )

    def _next_record_sequence(self, connection: sqlite3.Connection) -> int:
        sequence = self._metadata_int(connection, "next_record_sequence")
        self._set_metadata(connection, "next_record_sequence", sequence + 1)
        return sequence

    def _record_from_row(
        self,
        row: sqlite3.Row,
        *,
        include_owner_name: bool = True,
    ) -> ArchiveRecord:
        started = _from_utc_us(int(row["started_at_us"]))
        ended = _from_utc_us(int(row["ended_at_us"]))
        assert started is not None and ended is not None
        return ArchiveRecord(
            record_id=str(row["record_id"]),
            mode=row["mode"],
            surface=row["surface"],
            record_type=str(row["record_type"]),
            owner_principal_id=row["owner_principal_id"],
            owner_name=(
                row["owner_name_snapshot"] if include_owner_name else None
            ),
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            started_at=started,
            ended_at=ended,
            body=str(row["body"]),
            status=str(row["status"]),
            deletion_reason=row["deletion_reason"],
            created_sequence=int(row["created_seq"]),
            created_generation=int(row["created_generation"]),
        )

    @staticmethod
    def _participation_from_row(row: sqlite3.Row) -> ParticipationInterval:
        started = _from_utc_us(int(row["started_at_us"]))
        assert started is not None
        return ParticipationInterval(
            interval_id=str(row["interval_id"]),
            principal_id=str(row["principal_id"]),
            owner_name=str(row["owner_name_snapshot"]),
            guild_id=str(row["guild_id"]),
            channel_id=str(row["channel_id"]),
            interval_kind=str(row["interval_kind"]),
            started_at=started,
            ended_at=_from_utc_us(row["ended_at_us"]),
        )

    @staticmethod
    def _voice_transition_from_row(row: sqlite3.Row) -> VoiceStateTransition:
        event_at = _from_utc_us(int(row["event_at_us"]))
        assert event_at is not None
        return VoiceStateTransition(
            transition_id=str(row["transition_id"]),
            principal_id=str(row["principal_id"]),
            owner_name=str(row["owner_name_snapshot"]),
            guild_id=str(row["guild_id"]),
            channel_id=str(row["channel_id"]),
            event_at=event_at,
            present=bool(row["present"]),
            consent_current=bool(row["consent_current"]),
            self_mute=bool(row["self_mute"]),
            server_mute=bool(row["server_mute"]),
            self_deaf=bool(row["self_deaf"]),
            server_deaf=bool(row["server_deaf"]),
            suppressed=bool(row["suppressed"]),
            gateway_known=bool(row["gateway_known"]),
        )

    @staticmethod
    def _legal_minimal_from_row(row: sqlite3.Row) -> LegalMinimalEvent:
        occurred_at = _from_utc_us(int(row["occurred_at_us"]))
        assert occurred_at is not None
        return LegalMinimalEvent(
            event_id=str(row["event_id"]),
            owner_name=str(row["owner_name"]),
            occurred_at=occurred_at,
        )

    def _encode_admin_list_cursor(
        self,
        *,
        generation: int,
        list_kind: str,
        filter_digest: str,
        last_key: Sequence[Any],
    ) -> str:
        payload = {
            "generation": generation,
            "listKind": list_kind,
            "filterDigest": filter_digest,
            "lastKey": list(last_key),
        }
        envelope = {
            "payload": payload,
            "authTag": self._hmac(_ADMIN_LIST_CURSOR_DOMAIN, payload),
        }
        return base64.urlsafe_b64encode(_canonical_json(envelope)).decode(
            "ascii"
        ).rstrip("=")

    def _decode_admin_list_cursor(self, cursor: str) -> Mapping[str, Any]:
        token = _require_text(
            cursor,
            code="archive_admin_list_cursor_invalid",
            maximum=2048,
        )
        try:
            encoded = token.encode("ascii")
            raw = base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
            envelope = json.loads(raw.decode("utf-8"))
        except (
            UnicodeEncodeError,
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
            ValueError,
        ):
            raise ArchiveValidationError(
                "archive_admin_list_cursor_invalid"
            ) from None
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        supplied_tag = envelope.get("authTag") if isinstance(envelope, dict) else None
        last_key = payload.get("lastKey") if isinstance(payload, dict) else None
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"payload", "authTag"}
            or not isinstance(payload, dict)
            or set(payload)
            != {"generation", "listKind", "filterDigest", "lastKey"}
            or type(payload.get("generation")) is not int
            or payload["generation"] < 0
            or payload.get("listKind")
            not in {"participation", "voice_transition", "legal_minimal"}
            or not self._sha256_text(payload.get("filterDigest"))
            or not isinstance(last_key, list)
            or len(last_key) not in {2, 3}
            or type(last_key[0]) is not int
            or last_key[0] < 0
            or any(
                not isinstance(value, str) or not value or len(value) > 64
                for value in last_key[1:]
            )
            or not self._sha256_text(supplied_tag)
            or not hmac.compare_digest(
                str(supplied_tag),
                self._hmac(_ADMIN_LIST_CURSOR_DOMAIN, payload),
            )
        ):
            raise ArchiveValidationError("archive_admin_list_cursor_invalid")
        return payload

    def _admin_list_cursor_position(
        self,
        *,
        connection: sqlite3.Connection,
        cursor: str | None,
        list_kind: str,
        filter_payload: Mapping[str, Any],
    ) -> tuple[int, str, Sequence[Any] | None]:
        generation = self._metadata_int(connection, "generation")
        filter_digest = self._hmac(
            _ADMIN_LIST_FILTER_DOMAIN,
            {"listKind": list_kind, "filter": filter_payload},
        )
        if cursor is None:
            return generation, filter_digest, None
        decoded = self._decode_admin_list_cursor(cursor)
        if decoded["generation"] != generation:
            raise ArchiveStaleEvent("archive_admin_list_cursor_stale")
        if decoded["listKind"] != list_kind or not hmac.compare_digest(
            str(decoded["filterDigest"]), filter_digest
        ):
            raise ArchiveValidationError(
                "archive_admin_list_cursor_scope_mismatch"
            )
        return generation, filter_digest, decoded["lastKey"]

    def _encode_admin_cursor(
        self,
        *,
        generation: int,
        created_sequence: int,
        record_id: str,
        query_digest: str,
    ) -> str:
        payload = {
            "generation": generation,
            "createdSequence": created_sequence,
            "recordId": record_id,
            "queryDigest": query_digest,
        }
        envelope = {
            "payload": payload,
            "authTag": self._hmac(_ADMIN_READ_CURSOR_DOMAIN, payload),
        }
        return base64.urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")

    def _decode_admin_cursor(self, cursor: str) -> Mapping[str, Any]:
        token = _require_text(
            cursor,
            code="archive_admin_cursor_invalid",
            maximum=2048,
        )
        try:
            encoded = token.encode("ascii")
            raw = base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
            envelope = json.loads(raw.decode("utf-8"))
        except (
            UnicodeEncodeError,
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
            ValueError,
        ):
            raise ArchiveValidationError("archive_admin_cursor_invalid") from None
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"payload", "authTag"}
            or not isinstance(envelope.get("payload"), dict)
        ):
            raise ArchiveValidationError("archive_admin_cursor_invalid")
        payload = envelope["payload"]
        supplied_tag = envelope["authTag"]
        if (
            set(payload)
            != {"generation", "createdSequence", "recordId", "queryDigest"}
            or type(payload.get("generation")) is not int
            or payload["generation"] < 0
            or type(payload.get("createdSequence")) is not int
            or payload["createdSequence"] < 1
            or not isinstance(payload.get("recordId"), str)
            or not payload["recordId"]
            or len(payload["recordId"]) > 64
            or not self._sha256_text(payload.get("queryDigest"))
            or not self._sha256_text(supplied_tag)
            or not hmac.compare_digest(
                supplied_tag,
                self._hmac(_ADMIN_READ_CURSOR_DOMAIN, payload),
            )
        ):
            raise ArchiveValidationError("archive_admin_cursor_invalid")
        return payload

    def _encode_self_cursor(
        self,
        *,
        generation: int,
        created_sequence: int,
        record_id: str,
        query_digest: str,
    ) -> str:
        payload = {
            "generation": generation,
            "createdSequence": created_sequence,
            "recordId": record_id,
            "queryDigest": query_digest,
        }
        envelope = {
            "payload": payload,
            "authTag": self._hmac(_SELF_READ_CURSOR_DOMAIN, payload),
        }
        return base64.urlsafe_b64encode(_canonical_json(envelope)).decode("ascii").rstrip("=")

    def _decode_self_cursor(self, cursor: str) -> Mapping[str, Any]:
        token = _require_text(
            cursor,
            code="archive_self_cursor_invalid",
            maximum=2048,
        )
        try:
            encoded = token.encode("ascii")
            raw = base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
            envelope = json.loads(raw.decode("utf-8"))
        except (
            UnicodeEncodeError,
            UnicodeDecodeError,
            binascii.Error,
            json.JSONDecodeError,
            ValueError,
        ):
            raise ArchiveValidationError("archive_self_cursor_invalid") from None
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"payload", "authTag"}
            or not isinstance(envelope.get("payload"), dict)
        ):
            raise ArchiveValidationError("archive_self_cursor_invalid")
        payload = envelope["payload"]
        supplied_tag = envelope["authTag"]
        if (
            set(payload)
            != {"generation", "createdSequence", "recordId", "queryDigest"}
            or type(payload.get("generation")) is not int
            or payload["generation"] < 0
            or type(payload.get("createdSequence")) is not int
            or payload["createdSequence"] < 1
            or not isinstance(payload.get("recordId"), str)
            or not payload["recordId"]
            or len(payload["recordId"]) > 64
            or not self._sha256_text(payload.get("queryDigest"))
            or not self._sha256_text(supplied_tag)
            or not hmac.compare_digest(
                supplied_tag,
                self._hmac(_SELF_READ_CURSOR_DOMAIN, payload),
            )
        ):
            raise ArchiveValidationError("archive_self_cursor_invalid")
        return payload

    def append_record(
        self,
        *,
        mode: str,
        surface: str,
        record_type: str,
        body: str,
        started_at: datetime,
        ended_at: datetime,
        actor_external_id: str | None = None,
        owner_name: str | None = None,
        guild_id: str | None = None,
        channel_id: str | None = None,
        parent_ids: Iterable[str] = (),
        lineage: Mapping[str, Iterable[str]] | None = None,
        idempotency_key: str,
        record_id: str | None = None,
        expected_generation: int | None = None,
        now: datetime | None = None,
    ) -> ArchiveRecord:
        with self._thread_lock:
            now_us = self._now_us(now)
            self._require_open()
            self._verify_primary()
            if mode not in _MODES:
                raise ArchiveValidationError("archive_mode_invalid")
            if surface not in _SURFACES:
                raise ArchiveValidationError("archive_surface_invalid")
            if record_type not in _RECORD_TYPES:
                raise ArchiveValidationError("archive_record_type_rejected")
            if not isinstance(body, str) or not body or "\x00" in body:
                raise ArchiveValidationError("archive_body_invalid")
            if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
                raise ArchiveValidationError("archive_body_too_large")
            started_us = _as_utc_us(started_at, code="archive_started_at_invalid")
            ended_us = _as_utc_us(ended_at, code="archive_ended_at_invalid")
            if ended_us < started_us:
                raise ArchiveValidationError("archive_time_range_invalid")
            if mode == "discord_shared":
                guild_id = _require_text(guild_id, code="archive_guild_invalid", maximum=64)
                channel_id = _require_text(channel_id, code="archive_channel_invalid", maximum=64)
                identity_surface = "discord"
            else:
                if guild_id is not None or channel_id is not None:
                    raise ArchiveValidationError("archive_local_scope_invalid")
                identity_surface = "local"
            normalized_parents = tuple(
                sorted(
                    {
                        _require_text(parent, code="archive_parent_invalid", maximum=64)
                        for parent in parent_ids
                    }
                )
            )
            lineage_handles = _lineage_handles_from_raw(
                self._lineage_key,
                lineage,
            )
            normalized_owner_name: str | None = None
            if record_type in _USER_OWNED_RECORD_TYPES:
                if actor_external_id is None:
                    raise ArchiveValidationError("archive_actor_required")
                normalized_owner_name = _normalize_owner_name(owner_name)
            if record_type in _DERIVED_RECORD_TYPES:
                if actor_external_id is not None or not normalized_parents:
                    raise ArchiveValidationError("archive_lineage_required")
                if owner_name is not None:
                    raise ArchiveValidationError("archive_owner_name_not_allowed")
            if record_type in _SYSTEM_RECORD_TYPES:
                if actor_external_id is not None or owner_name is not None:
                    raise ArchiveValidationError("archive_system_owner_not_allowed")
                if mode != "local_private" or surface != "local":
                    raise ArchiveValidationError("archive_system_scope_invalid")
            if record_type == "final_stt" and (
                mode != "discord_shared" or surface != "discord"
            ):
                raise ArchiveValidationError("archive_final_stt_scope_invalid")
            if record_type == "final_stt" and ended_us <= started_us:
                raise ArchiveValidationError("archive_final_stt_interval_invalid")
            if record_type.startswith("minecraft_") and surface != "minecraft":
                raise ArchiveValidationError("archive_minecraft_surface_invalid")
            idem = _require_text(
                idempotency_key,
                code="archive_idempotency_key_invalid",
                maximum=256,
            )
            idempotency_digest = self._hmac(
                _RECORD_IDEMPOTENCY_DOMAIN, {"key": idem}
            )
            actor_lookup = (
                None
                if actor_external_id is None
                else self._principal_lookup(identity_surface, actor_external_id)
            )
            if normalized_parents:
                placeholders = ",".join("?" for _ in normalized_parents)
                with closing(
                    self._connect(self.primary_path, read_only=True)
                ) as connection:
                    parent_lineage_rows = connection.execute(
                        "SELECT record_id, lineage_json FROM records "
                        f"WHERE record_id IN ({placeholders}) AND status = 'active'",
                        normalized_parents,
                    ).fetchall()
                if len(parent_lineage_rows) != len(normalized_parents):
                    raise ArchiveValidationError("archive_parent_scope_invalid")
                lineage_handles = tuple(
                    sorted(
                        set(lineage_handles).union(
                            *(
                                set(
                                    _lineage_handles_from_json(
                                        row["lineage_json"]
                                    )
                                )
                                for row in parent_lineage_rows
                            )
                        )
                    )
                )
            lineage_json = _lineage_handles_json(lineage_handles)
            payload_digest = self._hmac(
                _RECORD_PAYLOAD_DOMAIN,
                {
                    "mode": mode,
                    "surface": surface,
                    "recordType": record_type,
                    "body": body,
                    "actor": actor_lookup,
                    "ownerName": normalized_owner_name,
                    "guild": guild_id,
                    "channel": channel_id,
                    "startedAtUs": started_us,
                    "endedAtUs": ended_us,
                    "parents": list(normalized_parents),
                    "lineageHandles": [list(item) for item in lineage_handles],
                },
            )
            assigned_id = _require_text(
                record_id or uuid.uuid4().hex,
                code="archive_record_id_invalid",
                maximum=64,
            )
            retired_record_digest = self._hmac(
                _RETIRED_RECORD_ID_DOMAIN, {"recordId": assigned_id}
            )
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                retired = connection.execute(
                    "SELECT receipt_kind, payload_digest FROM retired_receipts "
                    "WHERE (receipt_kind = 'record_idempotency' AND receipt_digest = ?) "
                    "OR (receipt_kind = 'record_id' AND receipt_digest = ?) "
                    "ORDER BY receipt_kind",
                    (idempotency_digest, retired_record_digest),
                ).fetchall()
                for receipt in retired:
                    if (
                        receipt["receipt_kind"] == "record_idempotency"
                        and receipt["payload_digest"] is not None
                        and not hmac.compare_digest(
                            str(receipt["payload_digest"]), payload_digest
                        )
                    ):
                        raise ArchiveStaleEvent("archive_idempotency_conflict")
                    raise ArchiveStaleEvent("archive_idempotency_retired")
                duplicate = connection.execute(
                    "SELECT receipt.payload_digest, record.* "
                    "FROM record_receipts receipt "
                    "JOIN records record ON record.record_id = receipt.record_id "
                    "WHERE receipt.idempotency_digest = ?",
                    (idempotency_digest,),
                ).fetchone()
                if duplicate is not None:
                    if not hmac.compare_digest(
                        str(duplicate["payload_digest"]), payload_digest
                    ):
                        raise ArchiveStaleEvent("archive_idempotency_conflict")
                    if duplicate["status"] != "active":
                        raise ArchiveStaleEvent("archive_idempotency_retired")
                    return self._record_from_row(duplicate)
                self._ensure_principal_not_frozen(connection, actor_lookup)
            self._ensure_writable(now_us)

            try:
                with closing(self._connect(self.primary_path)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    retired = connection.execute(
                        "SELECT receipt_kind, payload_digest FROM retired_receipts "
                        "WHERE (receipt_kind = 'record_idempotency' AND receipt_digest = ?) "
                        "OR (receipt_kind = 'record_id' AND receipt_digest = ?) "
                        "ORDER BY receipt_kind",
                        (idempotency_digest, retired_record_digest),
                    ).fetchall()
                    for receipt in retired:
                        if (
                            receipt["receipt_kind"] == "record_idempotency"
                            and receipt["payload_digest"] is not None
                            and not hmac.compare_digest(
                                str(receipt["payload_digest"]), payload_digest
                            )
                        ):
                            raise ArchiveStaleEvent("archive_idempotency_conflict")
                        raise ArchiveStaleEvent("archive_idempotency_retired")
                    duplicate = connection.execute(
                        "SELECT receipt.payload_digest, record.* "
                        "FROM record_receipts receipt "
                        "JOIN records record ON record.record_id = receipt.record_id "
                        "WHERE receipt.idempotency_digest = ?",
                        (idempotency_digest,),
                    ).fetchone()
                    if duplicate is not None:
                        if not hmac.compare_digest(
                            str(duplicate["payload_digest"]), payload_digest
                        ):
                            raise ArchiveStaleEvent("archive_idempotency_conflict")
                        if duplicate["status"] != "active":
                            raise ArchiveStaleEvent("archive_idempotency_retired")
                        connection.rollback()
                        return self._record_from_row(duplicate)
                    self._ensure_principal_not_frozen(
                        connection, actor_lookup
                    )
                    current_generation = self._metadata_int(connection, "generation")
                    if (
                        expected_generation is not None
                        and expected_generation != current_generation
                    ):
                        raise ArchiveStaleEvent("archive_generation_changed")
                    owner_id = None
                    if actor_external_id is not None:
                        owner_id = self._get_or_create_principal(
                            connection,
                            identity_surface,
                            actor_external_id,
                            ended_us,
                            normalized_owner_name,
                        )
                    for parent_id in normalized_parents:
                        parent = connection.execute(
                            "SELECT mode, guild_id, status FROM records WHERE record_id = ?",
                            (parent_id,),
                        ).fetchone()
                        if (
                            parent is None
                            or parent["status"] != "active"
                            or parent["mode"] != mode
                            or parent["guild_id"] != guild_id
                        ):
                            raise ArchiveValidationError("archive_parent_scope_invalid")
                    if record_type == "final_stt":
                        eligible = connection.execute(
                            "SELECT 1 FROM participation_intervals "
                            "WHERE principal_id = ? AND guild_id = ? AND channel_id = ? "
                            "AND interval_kind = 'eligible' AND started_at_us <= ? "
                            "AND (ended_at_us IS NULL OR ended_at_us >= ?) LIMIT 1",
                            (owner_id, guild_id, channel_id, started_us, ended_us),
                        ).fetchone()
                        if eligible is None:
                            raise ArchiveAuthorizationError("archive_voice_interval_ineligible")
                    sequence = self._next_record_sequence(connection)
                    created_generation = current_generation + 1
                    connection.execute(
                        "INSERT INTO records("
                        "record_id, record_schema, mode, surface, record_type, owner_principal_id, "
                        "owner_name_snapshot, guild_id, channel_id, started_at_us, "
                        "ended_at_us, body, lineage_json, status, "
                        "placeholder_id, deletion_reason, created_seq, created_generation"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', "
                        "NULL, NULL, ?, ?)",
                        (
                            assigned_id,
                            ARCHIVE_RECORD_SCHEMA,
                            mode,
                            surface,
                            record_type,
                            owner_id,
                            normalized_owner_name,
                            guild_id,
                            channel_id,
                            started_us,
                            ended_us,
                            body,
                            lineage_json,
                            sequence,
                            created_generation,
                        ),
                    )
                    connection.executemany(
                        "INSERT INTO record_parents(child_id, parent_id) VALUES (?, ?)",
                        tuple((assigned_id, parent_id) for parent_id in normalized_parents),
                    )
                    connection.execute(
                        "INSERT INTO record_receipts("
                        "idempotency_digest, payload_digest, record_id, created_at_us"
                        ") VALUES (?, ?, ?, ?)",
                        (
                            idempotency_digest,
                            payload_digest,
                            assigned_id,
                            now_us,
                        ),
                    )
                    self._commit_mutation(connection, kind="record_appended")
                    self._commit_and_anchor(connection)
                    row = connection.execute(
                        "SELECT * FROM records WHERE record_id = ?", (assigned_id,)
                    ).fetchone()
            except ConversationArchiveError:
                raise
            except sqlite3.IntegrityError:
                raise ArchiveValidationError("archive_record_conflict") from None
            except sqlite3.Error:
                self._fault = "primary_write_rejected"
                raise ArchiveUnavailableError("primary_write_rejected") from None
            self._replicate_after_commit(now_us)
            assert row is not None
            return self._record_from_row(row)

    def append_derived_record(
        self,
        *,
        surface: str,
        record_type: str,
        body: str,
        started_at: datetime,
        ended_at: datetime,
        parent_ids: Iterable[str],
        idempotency_key: str,
        record_id: str | None = None,
        expected_generation: int | None = None,
        now: datetime | None = None,
    ) -> ArchiveRecord:
        """Append a derived row using the one unambiguous active-parent scope."""

        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if record_type not in _DERIVED_RECORD_TYPES:
                raise ArchiveValidationError("archive_derived_record_type_required")
            normalized_parents = tuple(
                sorted(
                    {
                        _require_text(
                            parent,
                            code="archive_parent_invalid",
                            maximum=64,
                        )
                        for parent in parent_ids
                    }
                )
            )
            if not normalized_parents:
                raise ArchiveValidationError("archive_lineage_required")
            placeholders = ",".join("?" for _ in normalized_parents)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                parents = connection.execute(
                    f"SELECT record_id, record_type, mode, guild_id, channel_id, status FROM records "
                    f"WHERE record_id IN ({placeholders}) ORDER BY record_id",
                    normalized_parents,
                ).fetchall()
            if len(parents) != len(normalized_parents) or any(
                row["status"] != "active" for row in parents
            ):
                raise ArchiveValidationError("archive_parent_scope_invalid")
            if surface == "minecraft" and record_type == "minecraft_result" and any(
                row["record_type"] != "minecraft_command" for row in parents
            ):
                raise ArchiveValidationError("archive_minecraft_parent_invalid")
            scopes = {
                (row["mode"], row["guild_id"], row["channel_id"])
                for row in parents
            }
            if len(scopes) != 1:
                raise ArchiveValidationError("archive_parent_scope_ambiguous")
            mode, guild_id, channel_id = scopes.pop()
            if mode not in _MODES or (
                mode == "discord_shared"
                and (guild_id is None or channel_id is None)
            ):
                raise ArchiveIntegrityError("archive_parent_scope_invalid")
            return self.append_record(
                mode=str(mode),
                surface=surface,
                record_type=record_type,
                body=body,
                guild_id=guild_id,
                channel_id=channel_id,
                started_at=started_at,
                ended_at=ended_at,
                parent_ids=normalized_parents,
                idempotency_key=idempotency_key,
                record_id=record_id,
            expected_generation=expected_generation,
            now=now,
        )

    def append_system_record(
        self,
        *,
        record_type: str,
        body: str,
        started_at: datetime,
        ended_at: datetime,
        parent_ids: Iterable[str] = (),
        idempotency_key: str,
        record_id: str | None = None,
        expected_generation: int | None = None,
        now: datetime | None = None,
    ) -> ArchiveRecord:
        """Append one local, ownerless feedback-version ledger record.

        This deliberately is not a generic system log API.  The closed record
        type allowlist keeps prompts, model output, tool arguments, and raw
        runtime artifacts out of the private archive.
        """

        if record_type not in _SYSTEM_RECORD_TYPES:
            raise ArchiveValidationError("archive_system_record_type_required")
        return self.append_record(
            mode="local_private",
            surface="local",
            record_type=record_type,
            body=body,
            started_at=started_at,
            ended_at=ended_at,
            parent_ids=parent_ids,
            idempotency_key=idempotency_key,
            record_id=record_id,
            expected_generation=expected_generation,
            now=now,
        )

    def begin_ingest_generation(
        self,
        *,
        source_id: str,
        generation: str,
        activated_at: datetime,
        now: datetime | None = None,
    ) -> bool:
        """Activate a gateway generation and close every prior unknown gap.

        Reusing the same generation is idempotent.  A different generation
        closes open intervals at the supplied activation instant; callbacks
        carrying the previous generation are then rejected.
        """

        with self._thread_lock:
            now_us = self._now_us(now)
            activated_us = _as_utc_us(activated_at, code="archive_voice_time_invalid")
            self._ensure_writable(now_us)
            source = _require_text(source_id, code="archive_voice_source_invalid", maximum=64)
            boot = _require_text(generation, code="archive_voice_generation_invalid", maximum=128)
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT generation FROM voice_sources WHERE source_id = ?",
                    (source,),
                ).fetchone()
                if existing is not None and existing[0] == boot:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE participation_intervals SET ended_at_us = ? "
                    "WHERE ended_at_us IS NULL AND started_at_us <= ?",
                    (activated_us, activated_us),
                )
                connection.execute(
                    "DELETE FROM participation_intervals "
                    "WHERE ended_at_us IS NULL AND started_at_us > ?",
                    (activated_us,),
                )
                connection.execute("DELETE FROM voice_state")
                connection.execute(
                    "INSERT INTO voice_sources("
                    "source_id, generation, last_sequence, activated_at_us) "
                    "VALUES (?, ?, 0, ?) "
                    "ON CONFLICT(source_id) DO UPDATE SET "
                    "generation=excluded.generation, last_sequence=0, "
                    "activated_at_us=excluded.activated_at_us",
                    (source, boot, activated_us),
                )
                self._commit_mutation(connection, kind="voice_generation_activated")
                self._commit_and_anchor(connection)
            self._replicate_after_commit(now_us)
            return True

    def apply_voice_state(
        self,
        *,
        source_id: str,
        generation: str,
        event_sequence: int,
        idempotency_key: str,
        actor_external_id: str,
        owner_name: str,
        guild_id: str,
        channel_id: str,
        event_at: datetime,
        present: bool,
        consent_current: bool,
        self_mute: bool,
        server_mute: bool,
        self_deaf: bool,
        server_deaf: bool,
        suppressed: bool,
        gateway_known: bool,
        now: datetime | None = None,
    ) -> bool:
        """Apply one exact Discord voice-state transition.

        Presence and eligibility are separate half-open intervals.  Mute,
        deaf, Stage suppression, missing consent, and gateway-unknown state
        close eligibility immediately.
        """

        with self._thread_lock:
            now_us = self._now_us(now)
            event_us = _as_utc_us(event_at, code="archive_voice_time_invalid")
            self._require_open()
            self._verify_primary()
            source = _require_text(source_id, code="archive_voice_source_invalid", maximum=64)
            boot = _require_text(generation, code="archive_voice_generation_invalid", maximum=128)
            if type(event_sequence) is not int or event_sequence <= 0:
                raise ArchiveValidationError("archive_voice_sequence_invalid")
            idem = _require_text(
                idempotency_key,
                code="archive_idempotency_key_invalid",
                maximum=256,
            )
            guild = _require_text(guild_id, code="archive_guild_invalid", maximum=64)
            channel = _require_text(channel_id, code="archive_channel_invalid", maximum=64)
            normalized_owner_name = _normalize_owner_name(owner_name)
            lookup = self._principal_lookup("discord", actor_external_id)
            idem_digest = self._hmac(_IDEMPOTENCY_DOMAIN, {"key": idem})
            payload = {
                "source": source,
                "generation": boot,
                "sequence": event_sequence,
                "principal": lookup,
                "ownerName": normalized_owner_name,
                "guild": guild,
                "channel": channel,
                "eventAtUs": event_us,
                "present": bool(present),
                "consent": bool(consent_current),
                "selfMute": bool(self_mute),
                "serverMute": bool(server_mute),
                "selfDeaf": bool(self_deaf),
                "serverDeaf": bool(server_deaf),
                "suppressed": bool(suppressed),
                "known": bool(gateway_known),
            }
            payload_digest = self._hmac(_VOICE_EVENT_DOMAIN, payload)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                retired = connection.execute(
                    "SELECT payload_digest FROM retired_receipts "
                    "WHERE receipt_kind = 'voice_idempotency' "
                    "AND receipt_digest = ?",
                    (idem_digest,),
                ).fetchone()
                if retired is not None:
                    if (
                        retired["payload_digest"] is not None
                        and not hmac.compare_digest(
                            str(retired["payload_digest"]), payload_digest
                        )
                    ):
                        raise ArchiveStaleEvent("archive_idempotency_conflict")
                    raise ArchiveStaleEvent("archive_idempotency_retired")
                duplicate = connection.execute(
                    "SELECT payload_digest FROM ingest_receipts "
                    "WHERE idempotency_digest = ?",
                    (idem_digest,),
                ).fetchone()
                if duplicate is not None:
                    if not hmac.compare_digest(str(duplicate[0]), payload_digest):
                        raise ArchiveStaleEvent("archive_idempotency_conflict")
                    return False
                self._ensure_principal_not_frozen(connection, lookup)
            self._ensure_writable(now_us)
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                retired = connection.execute(
                    "SELECT payload_digest FROM retired_receipts "
                    "WHERE receipt_kind = 'voice_idempotency' "
                    "AND receipt_digest = ?",
                    (idem_digest,),
                ).fetchone()
                if retired is not None:
                    if (
                        retired["payload_digest"] is not None
                        and not hmac.compare_digest(
                            str(retired["payload_digest"]), payload_digest
                        )
                    ):
                        raise ArchiveStaleEvent("archive_idempotency_conflict")
                    raise ArchiveStaleEvent("archive_idempotency_retired")
                duplicate = connection.execute(
                    "SELECT payload_digest FROM ingest_receipts WHERE idempotency_digest = ?",
                    (idem_digest,),
                ).fetchone()
                if duplicate is not None:
                    connection.rollback()
                    if not hmac.compare_digest(str(duplicate[0]), payload_digest):
                        raise ArchiveStaleEvent("archive_idempotency_conflict")
                    return False
                self._ensure_principal_not_frozen(connection, lookup)
                source_row = connection.execute(
                    "SELECT generation, last_sequence FROM voice_sources WHERE source_id = ?",
                    (source,),
                ).fetchone()
                if source_row is None or source_row["generation"] != boot:
                    raise ArchiveStaleEvent("archive_voice_generation_stale")
                if event_sequence <= int(source_row["last_sequence"]):
                    raise ArchiveStaleEvent("archive_voice_sequence_stale")
                principal_id = self._get_or_create_principal(
                    connection,
                    "discord",
                    actor_external_id,
                    event_us,
                    normalized_owner_name,
                )
                old = connection.execute(
                    "SELECT * FROM voice_state WHERE principal_id = ? AND guild_id = ?",
                    (principal_id, guild),
                ).fetchone()
                if old is not None and event_us < int(old["updated_at_us"]):
                    raise ArchiveStaleEvent("archive_voice_time_stale")

                if old is not None:
                    channel_changed = old["channel_id"] != channel
                    old_presence = old["presence_interval_id"]
                    old_eligible = old["eligible_interval_id"]
                else:
                    channel_changed = False
                    old_presence = None
                    old_eligible = None
                desired_presence = bool(gateway_known and present)
                desired_eligible = bool(
                    desired_presence
                    and consent_current
                    and not self_mute
                    and not server_mute
                    and not self_deaf
                    and not server_deaf
                    and not suppressed
                )
                if old_presence is not None and (channel_changed or not desired_presence):
                    connection.execute(
                        "UPDATE participation_intervals SET ended_at_us = ? "
                        "WHERE interval_id = ? AND ended_at_us IS NULL",
                        (event_us, old_presence),
                    )
                    old_presence = None
                if old_eligible is not None and (channel_changed or not desired_eligible):
                    connection.execute(
                        "UPDATE participation_intervals SET ended_at_us = ? "
                        "WHERE interval_id = ? AND ended_at_us IS NULL",
                        (event_us, old_eligible),
                    )
                    old_eligible = None
                if desired_presence and old_presence is None:
                    old_presence = uuid.uuid4().hex
                    connection.execute(
                        "INSERT INTO participation_intervals("
                        "interval_id, principal_id, owner_name_snapshot, guild_id, channel_id, "
                        "interval_kind, started_at_us, ended_at_us"
                        ") VALUES (?, ?, ?, ?, ?, 'presence', ?, NULL)",
                        (
                            old_presence,
                            principal_id,
                            normalized_owner_name,
                            guild,
                            channel,
                            event_us,
                        ),
                    )
                if desired_eligible and old_eligible is None:
                    old_eligible = uuid.uuid4().hex
                    connection.execute(
                        "INSERT INTO participation_intervals("
                        "interval_id, principal_id, owner_name_snapshot, guild_id, channel_id, "
                        "interval_kind, started_at_us, ended_at_us"
                        ") VALUES (?, ?, ?, ?, ?, 'eligible', ?, NULL)",
                        (
                            old_eligible,
                            principal_id,
                            normalized_owner_name,
                            guild,
                            channel,
                            event_us,
                        ),
                    )
                connection.execute(
                    "INSERT INTO voice_state("
                    "principal_id, guild_id, channel_id, present, consent_current, "
                    "self_mute, server_mute, "
                    "self_deaf, server_deaf, suppressed, gateway_known, presence_interval_id, "
                    "eligible_interval_id, updated_at_us"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(principal_id, guild_id) DO UPDATE SET "
                    "channel_id=excluded.channel_id, present=excluded.present, "
                    "consent_current=excluded.consent_current, self_mute=excluded.self_mute, "
                    "server_mute=excluded.server_mute, self_deaf=excluded.self_deaf, "
                    "server_deaf=excluded.server_deaf, suppressed=excluded.suppressed, "
                    "gateway_known=excluded.gateway_known, "
                    "presence_interval_id=excluded.presence_interval_id, "
                    "eligible_interval_id=excluded.eligible_interval_id, "
                    "updated_at_us=excluded.updated_at_us",
                    (
                        principal_id,
                        guild,
                        channel,
                        int(bool(present)),
                        int(bool(consent_current)),
                        int(bool(self_mute)),
                        int(bool(server_mute)),
                        int(bool(self_deaf)),
                        int(bool(server_deaf)),
                        int(bool(suppressed)),
                        int(bool(gateway_known)),
                        old_presence,
                        old_eligible,
                        event_us,
                    ),
                )
                connection.execute(
                    "INSERT INTO voice_state_transitions("
                    "transition_id, principal_id, owner_name_snapshot, guild_id, "
                    "channel_id, event_at_us, present, consent_current, self_mute, "
                    "server_mute, self_deaf, server_deaf, suppressed, gateway_known, "
                    "idempotency_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        uuid.uuid4().hex,
                        principal_id,
                        normalized_owner_name,
                        guild,
                        channel,
                        event_us,
                        int(bool(present)),
                        int(bool(consent_current)),
                        int(bool(self_mute)),
                        int(bool(server_mute)),
                        int(bool(self_deaf)),
                        int(bool(server_deaf)),
                        int(bool(suppressed)),
                        int(bool(gateway_known)),
                        idem_digest,
                    ),
                )
                connection.execute(
                    "UPDATE voice_sources SET last_sequence = ? WHERE source_id = ?",
                    (event_sequence, source),
                )
                connection.execute(
                    "INSERT INTO ingest_receipts("
                    "idempotency_digest, payload_digest, principal_id, source_id, generation, "
                    "event_sequence, event_at_us"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        idem_digest,
                        payload_digest,
                        principal_id,
                        source,
                        boot,
                        event_sequence,
                        event_us,
                    ),
                )
                self._commit_mutation(connection, kind="voice_state_applied")
                self._commit_and_anchor(connection)
            self._replicate_after_commit(now_us)
            return True

    def read_participation_admin(
        self,
        *,
        authorized: bool,
        guild_id: str | None = None,
    ) -> tuple[ParticipationInterval, ...]:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            parameters: tuple[Any, ...] = ()
            where = ""
            if guild_id is not None:
                where = " WHERE guild_id = ?"
                parameters = (
                    _require_text(guild_id, code="archive_guild_invalid", maximum=64),
                )
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                rows = connection.execute(
                    "SELECT * FROM participation_intervals"
                    + where
                    + " ORDER BY started_at_us, interval_kind, interval_id",
                    parameters,
                ).fetchall()
            result: list[ParticipationInterval] = []
            for row in rows:
                result.append(self._participation_from_row(row))
            return tuple(result)

    def read_participation_admin_page(
        self,
        *,
        authorized: bool,
        guild_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ParticipationIntervalPage:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ArchiveValidationError("archive_read_limit_invalid")
            guild = (
                None
                if guild_id is None
                else _require_text(
                    guild_id,
                    code="archive_guild_invalid",
                    maximum=64,
                )
            )
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                generation, filter_digest, last_key = (
                    self._admin_list_cursor_position(
                        connection=connection,
                        cursor=cursor,
                        list_kind="participation",
                        filter_payload={"guildId": guild},
                    )
                )
                clauses: list[str] = []
                parameters: list[Any] = []
                if guild is not None:
                    clauses.append("guild_id = ?")
                    parameters.append(guild)
                if last_key is not None:
                    if len(last_key) != 3 or last_key[1] not in {
                        "presence",
                        "eligible",
                    }:
                        raise ArchiveValidationError(
                            "archive_admin_list_cursor_invalid"
                        )
                    clauses.append(
                        "(started_at_us > ? OR "
                        "(started_at_us = ? AND interval_kind > ?) OR "
                        "(started_at_us = ? AND interval_kind = ? AND interval_id > ?))"
                    )
                    parameters.extend(
                        (
                            last_key[0],
                            last_key[0],
                            last_key[1],
                            last_key[0],
                            last_key[1],
                            last_key[2],
                        )
                    )
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = connection.execute(
                    "SELECT * FROM participation_intervals"
                    + where
                    + " ORDER BY started_at_us, interval_kind, interval_id LIMIT ?",
                    (*parameters, limit + 1),
                ).fetchall()
            page_rows = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page_rows[-1]
                next_cursor = self._encode_admin_list_cursor(
                    generation=generation,
                    list_kind="participation",
                    filter_digest=filter_digest,
                    last_key=(
                        int(last["started_at_us"]),
                        str(last["interval_kind"]),
                        str(last["interval_id"]),
                    ),
                )
            return ParticipationIntervalPage(
                intervals=tuple(
                    self._participation_from_row(row) for row in page_rows
                ),
                next_cursor=next_cursor,
                snapshot_generation=generation,
            )

    def read_voice_state_transitions_admin_page(
        self,
        *,
        authorized: bool,
        guild_id: str | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> VoiceStateTransitionPage:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ArchiveValidationError("archive_read_limit_invalid")
            guild = (
                None
                if guild_id is None
                else _require_text(
                    guild_id,
                    code="archive_guild_invalid",
                    maximum=64,
                )
            )
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                generation, filter_digest, last_key = (
                    self._admin_list_cursor_position(
                        connection=connection,
                        cursor=cursor,
                        list_kind="voice_transition",
                        filter_payload={"guildId": guild},
                    )
                )
                clauses: list[str] = []
                parameters: list[Any] = []
                if guild is not None:
                    clauses.append("guild_id = ?")
                    parameters.append(guild)
                if last_key is not None:
                    if len(last_key) != 2:
                        raise ArchiveValidationError(
                            "archive_admin_list_cursor_invalid"
                        )
                    clauses.append(
                        "(event_at_us > ? OR "
                        "(event_at_us = ? AND transition_id > ?))"
                    )
                    parameters.extend((last_key[0], last_key[0], last_key[1]))
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = connection.execute(
                    "SELECT * FROM voice_state_transitions"
                    + where
                    + " ORDER BY event_at_us, transition_id LIMIT ?",
                    (*parameters, limit + 1),
                ).fetchall()
            page_rows = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page_rows[-1]
                next_cursor = self._encode_admin_list_cursor(
                    generation=generation,
                    list_kind="voice_transition",
                    filter_digest=filter_digest,
                    last_key=(
                        int(last["event_at_us"]),
                        str(last["transition_id"]),
                    ),
                )
            return VoiceStateTransitionPage(
                transitions=tuple(
                    self._voice_transition_from_row(row) for row in page_rows
                ),
                next_cursor=next_cursor,
                snapshot_generation=generation,
            )

    def read_voice_transitions_admin_page(
        self,
        **kwargs: Any,
    ) -> VoiceStateTransitionPage:
        return self.read_voice_state_transitions_admin_page(**kwargs)

    def read_legal_minimal_events(
        self,
        *,
        authorized: bool,
        limit: int = 5000,
    ) -> tuple[LegalMinimalEvent, ...]:
        """Return admin-only name/time remnants of deleted participation."""

        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 5000:
                raise ArchiveValidationError("archive_read_limit_invalid")
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                rows = connection.execute(
                    "SELECT event_id, owner_name, occurred_at_us "
                    "FROM legal_minimal_events "
                    "ORDER BY occurred_at_us, event_id LIMIT ?",
                    (limit,),
                ).fetchall()
            result: list[LegalMinimalEvent] = []
            for row in rows:
                result.append(self._legal_minimal_from_row(row))
            return tuple(result)

    def read_legal_minimal_events_page(
        self,
        *,
        authorized: bool,
        cursor: str | None = None,
        limit: int = 100,
    ) -> LegalMinimalEventPage:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ArchiveValidationError("archive_read_limit_invalid")
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                generation, filter_digest, last_key = (
                    self._admin_list_cursor_position(
                        connection=connection,
                        cursor=cursor,
                        list_kind="legal_minimal",
                        filter_payload={},
                    )
                )
                parameters: list[Any] = []
                where = ""
                if last_key is not None:
                    if len(last_key) != 2:
                        raise ArchiveValidationError(
                            "archive_admin_list_cursor_invalid"
                        )
                    where = (
                        " WHERE (occurred_at_us > ? OR "
                        "(occurred_at_us = ? AND event_id > ?))"
                    )
                    parameters.extend((last_key[0], last_key[0], last_key[1]))
                rows = connection.execute(
                    "SELECT event_id, owner_name, occurred_at_us "
                    "FROM legal_minimal_events"
                    + where
                    + " ORDER BY occurred_at_us, event_id LIMIT ?",
                    (*parameters, limit + 1),
                ).fetchall()
            page_rows = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page_rows[-1]
                next_cursor = self._encode_admin_list_cursor(
                    generation=generation,
                    list_kind="legal_minimal",
                    filter_digest=filter_digest,
                    last_key=(
                        int(last["occurred_at_us"]),
                        str(last["event_id"]),
                    ),
                )
            return LegalMinimalEventPage(
                events=tuple(
                    self._legal_minimal_from_row(row) for row in page_rows
                ),
                next_cursor=next_cursor,
                snapshot_generation=generation,
            )

    def is_voice_capture_eligible(
        self,
        *,
        actor_external_id: str,
        guild_id: str,
        channel_id: str,
        at: datetime,
    ) -> bool:
        """Check the exact half-open eligible interval before capture/STT."""

        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            guild = _require_text(guild_id, code="archive_guild_invalid", maximum=64)
            channel = _require_text(
                channel_id, code="archive_channel_invalid", maximum=64
            )
            at_us = _as_utc_us(at, code="archive_voice_time_invalid")
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                principal_id = self._find_principal(
                    connection, "discord", actor_external_id
                )
                if principal_id is None:
                    return False
                row = connection.execute(
                    "SELECT 1 FROM participation_intervals "
                    "WHERE principal_id = ? AND guild_id = ? AND channel_id = ? "
                    "AND interval_kind = 'eligible' AND started_at_us <= ? "
                    "AND (ended_at_us IS NULL OR ? < ended_at_us) LIMIT 1",
                    (principal_id, guild, channel, at_us, at_us),
                ).fetchone()
                return row is not None

    @staticmethod
    def _range_predicate(
        *,
        started_at_us: int | None,
        ended_at_us: int | None,
        alias: str = "r",
    ) -> tuple[str, tuple[int, ...]]:
        clauses: list[str] = []
        values: list[int] = []
        if started_at_us is not None:
            clauses.append(f"{alias}.ended_at_us >= ?")
            values.append(started_at_us)
        if ended_at_us is not None:
            clauses.append(f"{alias}.started_at_us < ?")
            values.append(ended_at_us)
        return (" AND ".join(clauses), tuple(values))

    def read_self(
        self,
        *,
        actor_external_id: str,
        guild_id: str,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        limit: int = 100,
    ) -> tuple[ArchiveRecord, ...]:
        return self.read_self_page(
            actor_external_id=actor_external_id,
            guild_id=guild_id,
            started_at=started_at,
            ended_at=ended_at,
            limit=limit,
        ).records

    def read_self_page(
        self,
        *,
        actor_external_id: str,
        guild_id: str,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ArchiveRecordPage:
        """Return a caller/guild/query/generation-bound page of self records."""

        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            guild = _require_text(guild_id, code="archive_guild_invalid", maximum=64)
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ArchiveValidationError("archive_read_limit_invalid")
            start_us = None if started_at is None else _as_utc_us(
                started_at, code="archive_started_at_invalid"
            )
            end_us = None if ended_at is None else _as_utc_us(
                ended_at, code="archive_ended_at_invalid"
            )
            if start_us is not None and end_us is not None and end_us <= start_us:
                raise ArchiveValidationError("archive_time_range_invalid")
            actor_lookup = self._principal_lookup("discord", actor_external_id)
            query_digest = self._hmac(
                _SELF_READ_QUERY_DOMAIN,
                {
                    "actor": actor_lookup,
                    "guild": guild,
                    "startedAtUs": start_us,
                    "endedAtUs": end_us,
                },
            )
            decoded = None if cursor is None else self._decode_self_cursor(cursor)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                generation = self._metadata_int(connection, "generation")
                if decoded is not None:
                    if decoded["generation"] != generation:
                        raise ArchiveStaleEvent("archive_self_cursor_stale")
                    if not hmac.compare_digest(
                        str(decoded["queryDigest"]), query_digest
                    ):
                        raise ArchiveAuthorizationError(
                            "archive_self_cursor_scope_mismatch"
                        )
                principal_id = self._find_principal(
                    connection, "discord", actor_external_id
                )
                if principal_id is None:
                    return ArchiveRecordPage((), None, generation)
                range_sql, range_values = self._range_predicate(
                    started_at_us=start_us,
                    ended_at_us=end_us,
                )
                if range_sql:
                    range_sql = " AND " + range_sql
                seed_rows = connection.execute(
                    "SELECT r.record_id FROM records r "
                    "WHERE r.owner_principal_id = ? AND r.guild_id = ? "
                    "AND r.status = 'active' "
                    "  AND (r.record_type <> 'final_stt' OR EXISTS ("
                    "    SELECT 1 FROM participation_intervals i "
                    "    WHERE i.principal_id = ? AND i.guild_id = r.guild_id "
                    "    AND i.channel_id = r.channel_id AND i.interval_kind = 'eligible' "
                    "    AND i.started_at_us <= r.started_at_us "
                    "    AND (i.ended_at_us IS NULL OR i.ended_at_us >= r.ended_at_us)"
                    "  ))"
                    + range_sql
                    + " ORDER BY r.created_seq",
                    (principal_id, guild, principal_id, *range_values),
                ).fetchall()
                selected_ids = {str(row[0]) for row in seed_rows}
                child_ids = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT record_id FROM records WHERE guild_id = ? "
                        "AND status = 'active' AND owner_principal_id IS NULL",
                        (guild,),
                    ).fetchall()
                }
                parent_sets: dict[str, set[str]] = {child: set() for child in child_ids}
                if child_ids:
                    placeholders = ",".join("?" for _ in child_ids)
                    for edge in connection.execute(
                        f"SELECT child_id, parent_id FROM record_parents "
                        f"WHERE child_id IN ({placeholders})",
                        tuple(sorted(child_ids)),
                    ).fetchall():
                        parent_sets[str(edge["child_id"])].add(str(edge["parent_id"]))
                changed = True
                while changed:
                    changed = False
                    for child_id in sorted(child_ids - selected_ids):
                        parents = parent_sets[child_id]
                        if parents and parents.issubset(selected_ids):
                            selected_ids.add(child_id)
                            changed = True
                rows: list[sqlite3.Row] = []
                if selected_ids:
                    placeholders = ",".join("?" for _ in selected_ids)
                    rows.extend(
                        connection.execute(
                            f"SELECT * FROM records WHERE record_id IN ({placeholders})",
                            tuple(sorted(selected_ids)),
                        ).fetchall()
                    )
                rows.extend(
                    connection.execute(
                    "SELECT r.* FROM records r JOIN tombstone_audiences a "
                    "ON a.placeholder_id = r.record_id "
                    "WHERE a.principal_id = ? AND a.guild_id = ? AND r.status = 'tombstone' "
                    + range_sql
                    + " ORDER BY r.created_seq",
                        (principal_id, guild, *range_values),
                    ).fetchall()
                )
            rows.sort(key=lambda row: (int(row["created_seq"]), str(row["record_id"])))
            if decoded is not None:
                rows = [
                    row
                    for row in rows
                    if (
                        int(row["created_seq"]),
                        str(row["record_id"]),
                    )
                    > (
                        int(decoded["createdSequence"]),
                        str(decoded["recordId"]),
                    )
                ]
            page_rows = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page_rows[-1]
                next_cursor = self._encode_self_cursor(
                    generation=generation,
                    created_sequence=int(last["created_seq"]),
                    record_id=str(last["record_id"]),
                    query_digest=query_digest,
                )
            return ArchiveRecordPage(
                records=tuple(
                self._record_from_row(row, include_owner_name=False)
                    for row in page_rows
                ),
                next_cursor=next_cursor,
                snapshot_generation=generation,
            )

    def read_record_admin(
        self,
        *,
        authorized: bool,
        record_id: str,
        include_quarantined: bool = False,
    ) -> ArchiveRecord | None:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            normalized_id = _require_text(
                record_id,
                code="archive_record_id_invalid",
                maximum=64,
            )
            statuses = (
                ("active", "tombstone", "quarantined")
                if include_quarantined
                else ("active",)
            )
            placeholders = ",".join("?" for _ in statuses)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                row = connection.execute(
                    "SELECT * FROM records WHERE record_id = ? "
                    f"AND status IN ({placeholders})",
                    (normalized_id, *statuses),
                ).fetchone()
            return None if row is None else self._record_from_row(row)

    def read_feedback_records_admin(
        self,
        *,
        authorized: bool,
        record_types: Iterable[str] | None = None,
        limit: int = 5000,
        after_created_sequence: int = 0,
    ) -> tuple[ArchiveRecord, ...]:
        """Read only the closed P1-5 ledger type set for local administration."""

        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 5000:
                raise ArchiveValidationError("archive_read_limit_invalid")
            if (
                type(after_created_sequence) is not int
                or after_created_sequence < 0
            ):
                raise ArchiveValidationError("archive_feedback_cursor_invalid")
            allowed_feedback_types = frozenset(
                record_type
                for record_type in _RECORD_TYPES
                if record_type.startswith("feedback_")
            )
            if record_types is None:
                feedback_types = allowed_feedback_types
            else:
                requested = tuple(record_types)
                if (
                    not requested
                    or any(
                        not isinstance(record_type, str)
                        or record_type not in allowed_feedback_types
                        for record_type in requested
                    )
                ):
                    raise ArchiveValidationError(
                        "archive_feedback_record_type_invalid"
                    )
                feedback_types = frozenset(requested)
            if not feedback_types:
                raise ArchiveValidationError("archive_feedback_record_type_invalid")
            ordered = tuple(sorted(feedback_types))
            placeholders = ",".join("?" for _ in ordered)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                rows = connection.execute(
                    "SELECT * FROM records WHERE status = 'active' "
                    f"AND record_type IN ({placeholders}) "
                    "AND created_seq > ? "
                    "ORDER BY created_seq LIMIT ?",
                    (*ordered, after_created_sequence, limit),
                ).fetchall()
            return tuple(self._record_from_row(row) for row in rows)

    def feedback_source_binding(
        self,
        *,
        authorized: bool,
        source_record_id: str,
        identity_surface: str,
        actor_external_id: str,
        task_id: str | None = None,
        session_id: str | None = None,
        guild_id: str | None = None,
        channel_id: str | None = None,
        feedback_surface: str | None = None,
    ) -> FeedbackSourceBinding:
        """Bind feedback to one active source and its unique owning principal.

        The returned projection contains no body, external actor identifier, or
        integrity secret.  Callers use ``archive_generation`` as the CAS input
        for the subsequent correction append.
        """

        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            normalized_id = _require_text(
                source_record_id,
                code="archive_record_id_invalid",
                maximum=64,
            )
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                generation = self._metadata_int(connection, "generation")
                source = connection.execute(
                    "SELECT record_id, record_type, mode, surface, guild_id, "
                    "channel_id, status FROM records WHERE record_id = ?",
                    (normalized_id,),
                ).fetchone()
                if source is None or source["status"] != "active":
                    raise ArchiveValidationError("archive_feedback_source_missing")
                if source["record_type"] not in {
                    "evelyn_reply",
                    "task_result",
                    "action_result",
                    "minecraft_result",
                }:
                    raise ArchiveValidationError("archive_feedback_source_invalid")
                actor_principal = self._find_principal(
                    connection,
                    identity_surface,
                    actor_external_id,
                )
                owner_rows = connection.execute(
                    "WITH RECURSIVE ancestors(record_id) AS ("
                    " SELECT ? UNION "
                    " SELECT edge.parent_id FROM record_parents edge "
                    " JOIN ancestors prior ON edge.child_id = prior.record_id"
                    ") SELECT DISTINCT record.owner_principal_id "
                    "FROM ancestors item JOIN records record "
                    "ON record.record_id = item.record_id "
                    "WHERE record.status = 'active' "
                    "AND record.owner_principal_id IS NOT NULL",
                    (normalized_id,),
                ).fetchall()
                source_root_rows = connection.execute(
                    "WITH RECURSIVE ancestors(record_id) AS ("
                    " SELECT ? UNION "
                    " SELECT edge.parent_id FROM record_parents edge "
                    " JOIN ancestors prior ON edge.child_id = prior.record_id"
                    ") SELECT DISTINCT record.record_type "
                    "FROM ancestors item JOIN records record "
                    "ON record.record_id = item.record_id "
                    "WHERE record.status = 'active' "
                    "AND record.record_type IN ('user_text', 'final_stt')",
                    (normalized_id,),
                ).fetchall()
                source_lineage_rows = connection.execute(
                    "WITH RECURSIVE ancestors(record_id) AS ("
                    " SELECT ? UNION "
                    " SELECT edge.parent_id FROM record_parents edge "
                    " JOIN ancestors prior ON edge.child_id = prior.record_id"
                    ") SELECT record.lineage_json "
                    "FROM ancestors item JOIN records record "
                    "ON record.record_id = item.record_id "
                    "WHERE record.status = 'active'",
                    (normalized_id,),
                ).fetchall()
            owners = tuple(sorted(str(row[0]) for row in owner_rows))
            if (
                actor_principal is None
                or len(owners) != 1
                or not hmac.compare_digest(owners[0], actor_principal)
            ):
                raise ArchiveAuthorizationError("archive_feedback_owner_mismatch")
            if guild_id is not None and not hmac.compare_digest(
                str(source["guild_id"] or ""),
                _require_text(
                    guild_id,
                    code="archive_guild_invalid",
                    maximum=64,
                ),
            ):
                raise ArchiveAuthorizationError("archive_feedback_scope_mismatch")
            if channel_id is not None and not hmac.compare_digest(
                str(source["channel_id"] or ""),
                _require_text(
                    channel_id,
                    code="archive_channel_invalid",
                    maximum=64,
                ),
            ):
                raise ArchiveAuthorizationError("archive_feedback_scope_mismatch")
            if feedback_surface is not None:
                normalized_feedback_surface = _require_text(
                    feedback_surface,
                    code="archive_feedback_surface_invalid",
                    maximum=16,
                )
                expected_root_type = {
                    "discord": "user_text",
                    "voice": "final_stt",
                }.get(normalized_feedback_surface)
                source_root_types = {
                    str(row[0]) for row in source_root_rows
                }
                if (
                    expected_root_type is None
                    or source_root_types != {expected_root_type}
                ):
                    raise ArchiveAuthorizationError(
                        "archive_feedback_surface_mismatch"
                    )
            expected_lineage: set[tuple[str, str]] = set()
            if task_id is not None:
                expected_lineage.add(
                    (
                        "turn",
                        archive_lineage_handle(
                            self._lineage_key,
                            "turn",
                            task_id,
                        ),
                    )
                )
            if session_id is not None:
                expected_lineage.add(
                    (
                        "session",
                        archive_lineage_handle(
                            self._lineage_key,
                            "session",
                            session_id,
                        ),
                    )
                )
            if expected_lineage:
                source_lineage = {
                    lineage
                    for row in source_lineage_rows
                    for lineage in _lineage_handles_from_json(row[0])
                }
                if not expected_lineage.issubset(source_lineage):
                    raise ArchiveAuthorizationError(
                        "archive_feedback_lineage_mismatch"
                    )
            mode = source["mode"]
            surface = source["surface"]
            if mode not in _MODES or surface not in _SURFACES:
                raise ArchiveIntegrityError("archive_feedback_source_scope_invalid")
            return FeedbackSourceBinding(
                record_id=normalized_id,
                record_type=str(source["record_type"]),
                mode=str(mode),
                surface=str(surface),
                guild_id=source["guild_id"],
                channel_id=source["channel_id"],
                owner_principal_id=owners[0],
                archive_generation=generation,
            )

    def feedback_workflow_id(
        self,
        *,
        identity_surface: str,
        actor_external_id: str,
        nonce: str,
    ) -> str:
        """Return an opaque, retry-stable ID without exposing actor/nonce hashes."""

        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            surface = _require_text(
                identity_surface,
                code="archive_identity_surface_invalid",
                maximum=32,
            )
            actor = _require_text(
                actor_external_id,
                code="archive_actor_invalid",
                maximum=256,
            )
            normalized_nonce = _require_text(
                nonce,
                code="archive_feedback_nonce_invalid",
                maximum=128,
            )
            digest = self._hmac(
                _FEEDBACK_WORKFLOW_DOMAIN,
                {
                    "surface": surface,
                    "actor": actor,
                    "nonce": normalized_nonce,
                },
            )
            return f"fb-{digest[:48]}"

    def read_admin(
        self,
        *,
        authorized: bool,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        include_quarantined: bool = False,
        limit: int = 500,
    ) -> tuple[ArchiveRecord, ...]:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 5000:
                raise ArchiveValidationError("archive_read_limit_invalid")
            start_us = None if started_at is None else _as_utc_us(
                started_at, code="archive_started_at_invalid"
            )
            end_us = None if ended_at is None else _as_utc_us(
                ended_at, code="archive_ended_at_invalid"
            )
            range_sql, range_values = self._range_predicate(
                started_at_us=start_us,
                ended_at_us=end_us,
            )
            statuses = ("active", "tombstone", "quarantined") if include_quarantined else (
                "active",
                "tombstone",
            )
            placeholders = ",".join("?" for _ in statuses)
            clauses = [f"status IN ({placeholders})"]
            if range_sql:
                clauses.append(range_sql)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                rows = connection.execute(
                    "SELECT * FROM records WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY created_seq LIMIT ?",
                    (*statuses, *range_values, limit),
                ).fetchall()
            return tuple(self._record_from_row(row) for row in rows)

    def read_admin_page(
        self,
        *,
        authorized: bool,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        include_quarantined: bool = False,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ArchiveRecordPage:
        """Read one stable admin page with an authenticated generation cursor."""

        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 500:
                raise ArchiveValidationError("archive_read_limit_invalid")
            start_us = None if started_at is None else _as_utc_us(
                started_at, code="archive_started_at_invalid"
            )
            end_us = None if ended_at is None else _as_utc_us(
                ended_at, code="archive_ended_at_invalid"
            )
            if start_us is not None and end_us is not None and end_us <= start_us:
                raise ArchiveValidationError("archive_time_range_invalid")
            statuses = (
                ("active", "tombstone", "quarantined")
                if include_quarantined
                else ("active", "tombstone")
            )
            query_digest = self._hmac(
                _ADMIN_READ_QUERY_DOMAIN,
                {
                    "startedAtUs": start_us,
                    "endedAtUs": end_us,
                    "statuses": list(statuses),
                },
            )
            decoded = None if cursor is None else self._decode_admin_cursor(cursor)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                generation = self._metadata_int(connection, "generation")
                if decoded is not None:
                    if decoded["generation"] != generation:
                        raise ArchiveStaleEvent("archive_admin_cursor_stale")
                    if not hmac.compare_digest(
                        str(decoded["queryDigest"]), query_digest
                    ):
                        raise ArchiveValidationError(
                            "archive_admin_cursor_query_mismatch"
                        )
                placeholders = ",".join("?" for _ in statuses)
                clauses = [f"r.status IN ({placeholders})"]
                parameters: list[Any] = list(statuses)
                range_sql, range_values = self._range_predicate(
                    started_at_us=start_us,
                    ended_at_us=end_us,
                )
                if range_sql:
                    clauses.append(range_sql)
                    parameters.extend(range_values)
                if decoded is not None:
                    clauses.append(
                        "(r.created_seq > ? OR "
                        "(r.created_seq = ? AND r.record_id > ?))"
                    )
                    parameters.extend(
                        (
                            decoded["createdSequence"],
                            decoded["createdSequence"],
                            decoded["recordId"],
                        )
                    )
                rows = connection.execute(
                    "SELECT r.* FROM records r WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY r.created_seq, r.record_id LIMIT ?",
                    (*parameters, limit + 1),
                ).fetchall()
            page_rows = rows[:limit]
            next_cursor = None
            if len(rows) > limit:
                last = page_rows[-1]
                next_cursor = self._encode_admin_cursor(
                    generation=generation,
                    created_sequence=int(last["created_seq"]),
                    record_id=str(last["record_id"]),
                    query_digest=query_digest,
                )
            return ArchiveRecordPage(
                records=tuple(self._record_from_row(row) for row in page_rows),
                next_cursor=next_cursor,
                snapshot_generation=generation,
            )

    def _deletion_targets(
        self,
        connection: sqlite3.Connection,
        *,
        principal_id: str | None,
        scope_all: bool,
        guild_id: str | None,
        started_at_us: int | None,
        ended_at_us: int | None,
        explicit_record_ids: Sequence[str] = (),
        explicit_interval_ids: Sequence[str] = (),
        explicit_transition_ids: Sequence[str] = (),
    ) -> _DeletionTargets:
        if explicit_record_ids:
            placeholders = ",".join("?" for _ in explicit_record_ids)
            clauses = ["status = 'active'", f"record_id IN ({placeholders})"]
            parameters: list[Any] = list(explicit_record_ids)
            if started_at_us is not None:
                clauses.append("ended_at_us >= ?")
                parameters.append(started_at_us)
            if ended_at_us is not None:
                clauses.append("started_at_us < ?")
                parameters.append(ended_at_us)
            seed_rows = connection.execute(
                "SELECT record_id, guild_id, owner_principal_id "
                "FROM records WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_seq",
                tuple(parameters),
            ).fetchall()
        elif principal_id is None:
            seed_rows = []
        else:
            clauses = ["owner_principal_id = ?", "status = 'active'"]
            parameters: list[Any] = [principal_id]
            if not scope_all:
                if guild_id is not None:
                    clauses.append("guild_id = ?")
                    parameters.append(guild_id)
                if started_at_us is not None:
                    clauses.append("ended_at_us >= ?")
                    parameters.append(started_at_us)
                if ended_at_us is not None:
                    clauses.append("started_at_us < ?")
                    parameters.append(ended_at_us)
            seed_rows = connection.execute(
                "SELECT record_id, guild_id, owner_principal_id "
                "FROM records WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_seq",
                tuple(parameters),
            ).fetchall()
        owned_ids = tuple(str(row["record_id"]) for row in seed_rows)
        counts: dict[str, int] = {}
        for row in seed_rows:
            key = "local" if row["guild_id"] is None else str(row["guild_id"])
            counts[key] = counts.get(key, 0) + 1

        dependent_ids: tuple[str, ...] = ()
        if owned_ids:
            placeholders = ",".join("?" for _ in owned_ids)
            descendants = connection.execute(
                "WITH RECURSIVE affected(record_id) AS ("
                f" SELECT record_id FROM records WHERE record_id IN ({placeholders})"
                " UNION "
                " SELECT edge.child_id FROM record_parents edge "
                " JOIN affected prior ON prior.record_id = edge.parent_id "
                " JOIN records child ON child.record_id = edge.child_id "
                " WHERE child.status = 'active'"
                ") SELECT record_id FROM affected ORDER BY record_id",
                owned_ids,
            ).fetchall()
            owned_set = set(owned_ids)
            dependent_ids = tuple(
                str(row[0]) for row in descendants if str(row[0]) not in owned_set
            )

        if explicit_interval_ids:
            interval_ids = tuple(sorted(set(explicit_interval_ids)))
        elif principal_id is None:
            interval_ids = ()
        else:
            clauses = ["principal_id = ?"]
            parameters = [principal_id]
            if not scope_all:
                if guild_id is not None:
                    clauses.append("guild_id = ?")
                    parameters.append(guild_id)
                if started_at_us is not None:
                    clauses.append("(ended_at_us IS NULL OR ended_at_us > ?)")
                    parameters.append(started_at_us)
                if ended_at_us is not None:
                    clauses.append("started_at_us < ?")
                    parameters.append(ended_at_us)
            interval_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT interval_id FROM participation_intervals WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY started_at_us, interval_id",
                    tuple(parameters),
                ).fetchall()
            )
        if explicit_transition_ids:
            transition_ids = tuple(sorted(set(explicit_transition_ids)))
        elif principal_id is None:
            transition_ids = ()
        else:
            clauses = ["principal_id = ?"]
            parameters = [principal_id]
            if not scope_all:
                if guild_id is not None:
                    clauses.append("guild_id = ?")
                    parameters.append(guild_id)
                if started_at_us is not None:
                    clauses.append("event_at_us >= ?")
                    parameters.append(started_at_us)
                if ended_at_us is not None:
                    clauses.append("event_at_us < ?")
                    parameters.append(ended_at_us)
            transition_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT transition_id FROM voice_state_transitions WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY event_at_us, transition_id",
                    tuple(parameters),
                ).fetchall()
            )
        affected_principals = {
            str(row["owner_principal_id"])
            for row in seed_rows
            if row["owner_principal_id"] is not None
        }
        if dependent_ids:
            dependent_placeholders = ",".join("?" for _ in dependent_ids)
            affected_principals.update(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT owner_principal_id FROM records "
                    f"WHERE record_id IN ({dependent_placeholders}) "
                    "AND owner_principal_id IS NOT NULL",
                    dependent_ids,
                ).fetchall()
            )
        if principal_id is not None:
            affected_principals.add(principal_id)
        if interval_ids:
            interval_placeholders = ",".join("?" for _ in interval_ids)
            affected_principals.update(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT principal_id FROM participation_intervals "
                    f"WHERE interval_id IN ({interval_placeholders})",
                    interval_ids,
                ).fetchall()
            )
        if transition_ids:
            transition_placeholders = ",".join("?" for _ in transition_ids)
            affected_principals.update(
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT principal_id FROM voice_state_transitions "
                    f"WHERE transition_id IN ({transition_placeholders})",
                    transition_ids,
                ).fetchall()
            )
        principal_lookup_digests: tuple[str, ...] = ()
        if affected_principals:
            principal_placeholders = ",".join(
                "?" for _ in affected_principals
            )
            principal_lookup_digests = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT lookup_digest FROM principals "
                        f"WHERE principal_id IN ({principal_placeholders})",
                        tuple(sorted(affected_principals)),
                    ).fetchall()
                )
            )
        lineage_handles: set[tuple[str, str]] = set()
        affected_record_ids = owned_ids + dependent_ids
        lineage_complete = bool(affected_record_ids)
        if affected_record_ids:
            lineage_placeholders = ",".join("?" for _ in affected_record_ids)
            lineage_rows = connection.execute(
                "SELECT lineage_json FROM records "
                f"WHERE record_id IN ({lineage_placeholders})",
                affected_record_ids,
            ).fetchall()
            lineage_complete = len(lineage_rows) == len(affected_record_ids)
            for lineage_row in lineage_rows:
                row_handles = _lineage_handles_from_json(
                    lineage_row["lineage_json"]
                )
                if not row_handles:
                    lineage_complete = False
                lineage_handles.update(row_handles)
        return _DeletionTargets(
            principal_id=principal_id,
            owned_record_ids=owned_ids,
            dependent_record_ids=dependent_ids,
            interval_ids=interval_ids,
            transition_ids=transition_ids,
            counts_by_guild=counts,
            scope_all=scope_all,
            guild_id=guild_id,
            started_at_us=started_at_us,
            ended_at_us=ended_at_us,
            principal_ids=tuple(sorted(affected_principals)),
            principal_lookup_digests=principal_lookup_digests,
            lineage_handles=tuple(sorted(lineage_handles)),
            lineage_complete=lineage_complete,
        )

    def _target_fingerprint(
        self,
        connection: sqlite3.Connection,
        targets: _DeletionTargets,
        *,
        generation: int,
        domain: bytes = _PREVIEW_DOMAIN,
    ) -> str:
        intervals: list[list[Any]] = []
        if targets.interval_ids:
            placeholders = ",".join("?" for _ in targets.interval_ids)
            intervals = [
                [row[0], row[1], row[2]]
                for row in connection.execute(
                    f"SELECT interval_id, started_at_us, ended_at_us "
                    f"FROM participation_intervals WHERE interval_id IN ({placeholders}) "
                    "ORDER BY interval_id",
                    targets.interval_ids,
                ).fetchall()
            ]
        transitions: list[list[Any]] = []
        if targets.transition_ids:
            placeholders = ",".join("?" for _ in targets.transition_ids)
            transitions = [
                [row[0], row[1]]
                for row in connection.execute(
                    f"SELECT transition_id, event_at_us FROM voice_state_transitions "
                    f"WHERE transition_id IN ({placeholders}) ORDER BY transition_id",
                    targets.transition_ids,
                ).fetchall()
            ]
        payload = {
            "generation": generation,
            "scopeAll": targets.scope_all,
            "guild": targets.guild_id,
            "start": targets.started_at_us,
            "end": targets.ended_at_us,
            "owned": list(targets.owned_record_ids),
            "dependent": list(targets.dependent_record_ids),
            "intervals": intervals,
            "transitions": transitions,
        }
        return self._hmac(domain, payload)

    @staticmethod
    def _validate_deletion_scope(
        *,
        guild_id: str | None,
        started_at: datetime | None,
        ended_at: datetime | None,
    ) -> tuple[bool, str | None, int | None, int | None]:
        if started_at is None and ended_at is None:
            return True, None, None, None
        if started_at is None or ended_at is None:
            raise ArchiveValidationError("archive_deletion_period_incomplete")
        guild = _require_text(guild_id, code="archive_guild_invalid", maximum=64)
        start_us = _as_utc_us(started_at, code="archive_started_at_invalid")
        end_us = _as_utc_us(ended_at, code="archive_ended_at_invalid")
        if end_us <= start_us:
            raise ArchiveValidationError("archive_time_range_invalid")
        return False, guild, start_us, end_us

    def preview_user_deletion(
        self,
        *,
        actor_external_id: str,
        request_guild_id: str,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        now: datetime | None = None,
    ) -> DeletionPreview:
        """Create a 60-second, caller-bound, one-use deletion preview.

        Omitting the period intentionally means every record for this actor in
        every guild.  A bounded period is limited to the invoking guild.
        """

        with self._thread_lock:
            now_us = self._now_us(now)
            self._ensure_writable(now_us, deletion=True)
            request_guild = _require_text(
                request_guild_id, code="archive_guild_invalid", maximum=64
            )
            scope_all, target_guild, start_us, end_us = self._validate_deletion_scope(
                guild_id=request_guild,
                started_at=started_at,
                ended_at=ended_at,
            )
            actor_lookup = self._principal_lookup("discord", actor_external_id)
            preview_id = uuid.uuid4().hex
            expires_us = now_us + ARCHIVE_DELETION_CONFIRM_SECONDS * 1_000_000
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM deletion_previews WHERE expires_at_us <= ?", (now_us,)
                )
                principal_id = self._find_principal(
                    connection, "discord", actor_external_id
                )
                targets = self._deletion_targets(
                    connection,
                    principal_id=principal_id,
                    scope_all=scope_all,
                    guild_id=target_guild,
                    started_at_us=start_us,
                    ended_at_us=end_us,
                )
                snapshot_generation = self._metadata_int(connection, "generation") + 1
                fingerprint = self._target_fingerprint(
                    connection, targets, generation=snapshot_generation
                )
                connection.execute(
                    "INSERT INTO deletion_previews("
                    "preview_id, preview_kind, actor_lookup_digest, request_guild_id, "
                    "admin_target_principal_id, admin_record_ids_json, scope_all, "
                    "started_at_us, ended_at_us, target_fingerprint, snapshot_generation, "
                    "created_at_us, expires_at_us"
                    ") VALUES (?, 'self', ?, ?, NULL, '[]', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        preview_id,
                        actor_lookup,
                        request_guild,
                        int(scope_all),
                        start_us,
                        end_us,
                        fingerprint,
                        snapshot_generation,
                        now_us,
                        expires_us,
                    ),
                )
                actual_generation = self._commit_mutation(
                    connection, kind="deletion_preview_created"
                )
                if actual_generation != snapshot_generation:
                    raise ArchiveIntegrityError("archive_generation_invalid")
                self._commit_and_anchor(connection)
            self._replicate_after_commit(now_us)
            return DeletionPreview(
                preview_id=preview_id,
                expires_at=_from_utc_us(expires_us),  # type: ignore[arg-type]
                snapshot_generation=snapshot_generation,
                counts_by_guild=dict(targets.counts_by_guild),
                owned_record_count=len(targets.owned_record_ids),
                dependent_record_count=len(targets.dependent_record_ids),
                interval_count=len(targets.interval_ids),
                all_guilds=scope_all,
            )

    def preview_admin_deletion(
        self,
        *,
        authorized: bool,
        target_principal_id: str | None = None,
        record_ids: Iterable[str] = (),
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        now: datetime | None = None,
    ) -> DeletionPreview:
        """Create an admin-domain preview for one exact opaque target.

        Authentication and OTP step-up are owned by the local admin adapter;
        this method deliberately accepts neither Discord actor IDs nor a self
        preview token.
        """

        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            now_us = self._now_us(now)
            self._ensure_writable(now_us, deletion=True)
            normalized_ids = tuple(
                sorted(
                    {
                        _require_text(
                            record_id,
                            code="archive_record_id_invalid",
                            maximum=64,
                        )
                        for record_id in record_ids
                    }
                )
            )
            if len(normalized_ids) > 200:
                raise ArchiveValidationError("archive_admin_target_too_large")
            normalized_principal = (
                None
                if target_principal_id is None
                else _require_text(
                    target_principal_id,
                    code="archive_principal_id_invalid",
                    maximum=64,
                )
            )
            if (normalized_principal is None) == (not normalized_ids):
                raise ArchiveValidationError("archive_admin_target_ambiguous")
            if (started_at is None) != (ended_at is None):
                raise ArchiveValidationError("archive_deletion_period_incomplete")
            start_us = (
                None
                if started_at is None
                else _as_utc_us(started_at, code="archive_started_at_invalid")
            )
            end_us = (
                None
                if ended_at is None
                else _as_utc_us(ended_at, code="archive_ended_at_invalid")
            )
            if start_us is not None and end_us is not None and end_us <= start_us:
                raise ArchiveValidationError("archive_time_range_invalid")
            scope_all = start_us is None and end_us is None
            preview_id = uuid.uuid4().hex
            expires_us = now_us + ARCHIVE_DELETION_CONFIRM_SECONDS * 1_000_000
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM deletion_previews WHERE expires_at_us <= ?", (now_us,)
                )
                if normalized_principal is not None:
                    exists = connection.execute(
                        "SELECT 1 FROM principals WHERE principal_id = ?",
                        (normalized_principal,),
                    ).fetchone()
                    if exists is None:
                        raise ArchiveValidationError("archive_admin_target_missing")
                targets = self._deletion_targets(
                    connection,
                    principal_id=normalized_principal,
                    scope_all=scope_all,
                    guild_id=None,
                    started_at_us=start_us,
                    ended_at_us=end_us,
                    explicit_record_ids=normalized_ids,
                )
                snapshot_generation = self._metadata_int(connection, "generation") + 1
                fingerprint = self._target_fingerprint(
                    connection,
                    targets,
                    generation=snapshot_generation,
                    domain=_ADMIN_PREVIEW_DOMAIN,
                )
                connection.execute(
                    "INSERT INTO deletion_previews("
                    "preview_id, preview_kind, actor_lookup_digest, request_guild_id, "
                    "admin_target_principal_id, admin_record_ids_json, scope_all, "
                    "started_at_us, ended_at_us, target_fingerprint, snapshot_generation, "
                    "created_at_us, expires_at_us"
                    ") VALUES (?, 'admin', NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        preview_id,
                        normalized_principal,
                        json.dumps(list(normalized_ids), separators=(",", ":")),
                        int(scope_all),
                        start_us,
                        end_us,
                        fingerprint,
                        snapshot_generation,
                        now_us,
                        expires_us,
                    ),
                )
                actual_generation = self._commit_mutation(
                    connection, kind="admin_deletion_preview_created"
                )
                if actual_generation != snapshot_generation:
                    raise ArchiveIntegrityError("archive_generation_invalid")
                self._commit_and_anchor(connection)
            self._replicate_after_commit(now_us)
            return DeletionPreview(
                preview_id=preview_id,
                expires_at=_from_utc_us(expires_us),  # type: ignore[arg-type]
                snapshot_generation=snapshot_generation,
                counts_by_guild=dict(targets.counts_by_guild),
                owned_record_count=len(targets.owned_record_ids),
                dependent_record_count=len(targets.dependent_record_ids),
                interval_count=len(targets.interval_ids),
                all_guilds=scope_all,
            )

    def _truncate_intervals(
        self,
        connection: sqlite3.Connection,
        targets: _DeletionTargets,
        *,
        reason: str,
        legal_cutoff_us: int,
    ) -> None:
        if not targets.interval_ids:
            return
        placeholders = ",".join("?" for _ in targets.interval_ids)
        rows = connection.execute(
            f"SELECT * FROM participation_intervals WHERE interval_id IN ({placeholders})",
            targets.interval_ids,
        ).fetchall()
        for row in rows:
            occurred_at = int(row["started_at_us"])
            if not targets.scope_all:
                assert targets.started_at_us is not None
                occurred_at = max(occurred_at, targets.started_at_us)
            if (
                reason in {"user_requested", "admin_requested"}
                and occurred_at > legal_cutoff_us
            ):
                connection.execute(
                    "INSERT INTO legal_minimal_events(event_id, owner_name, occurred_at_us) "
                    "VALUES (?, ?, ?)",
                    (
                        uuid.uuid4().hex,
                        row["owner_name_snapshot"],
                        occurred_at,
                    ),
                )
        if targets.scope_all:
            connection.execute(
                f"DELETE FROM participation_intervals WHERE interval_id IN ({placeholders})",
                targets.interval_ids,
            )
            return
        assert targets.started_at_us is not None and targets.ended_at_us is not None
        delete_start = targets.started_at_us
        delete_end = targets.ended_at_us
        for row in rows:
            interval_id = str(row["interval_id"])
            interval_start = int(row["started_at_us"])
            interval_end = row["ended_at_us"]
            effective_end = (1 << 62) if interval_end is None else int(interval_end)
            if delete_start <= interval_start and delete_end >= effective_end:
                connection.execute(
                    "DELETE FROM participation_intervals WHERE interval_id = ?",
                    (interval_id,),
                )
            elif interval_start < delete_start and effective_end > delete_end:
                connection.execute(
                    "UPDATE participation_intervals SET ended_at_us = ? WHERE interval_id = ?",
                    (delete_start, interval_id),
                )
                right_id = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO participation_intervals("
                    "interval_id, principal_id, owner_name_snapshot, guild_id, channel_id, "
                    "interval_kind, started_at_us, ended_at_us"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        right_id,
                        row["principal_id"],
                        row["owner_name_snapshot"],
                        row["guild_id"],
                        row["channel_id"],
                        row["interval_kind"],
                        delete_end,
                        interval_end,
                    ),
                )
                connection.execute(
                    "UPDATE voice_state SET presence_interval_id = ? "
                    "WHERE presence_interval_id = ? AND ? = 'presence'",
                    (right_id, interval_id, row["interval_kind"]),
                )
                connection.execute(
                    "UPDATE voice_state SET eligible_interval_id = ? "
                    "WHERE eligible_interval_id = ? AND ? = 'eligible'",
                    (right_id, interval_id, row["interval_kind"]),
                )
            elif interval_start < delete_start < effective_end:
                connection.execute(
                    "UPDATE participation_intervals SET ended_at_us = ? WHERE interval_id = ?",
                    (delete_start, interval_id),
                )
                connection.execute(
                    "UPDATE voice_state SET presence_interval_id = NULL "
                    "WHERE presence_interval_id = ?",
                    (interval_id,),
                )
                connection.execute(
                    "UPDATE voice_state SET eligible_interval_id = NULL "
                    "WHERE eligible_interval_id = ?",
                    (interval_id,),
                )
            elif interval_start < delete_end < effective_end:
                connection.execute(
                    "UPDATE participation_intervals SET started_at_us = ? WHERE interval_id = ?",
                    (delete_end, interval_id),
                )

    def _delete_voice_transitions(
        self,
        connection: sqlite3.Connection,
        targets: _DeletionTargets,
        *,
        reason: str,
        retired_generation: int,
        legal_cutoff_us: int,
    ) -> None:
        if not targets.transition_ids:
            return
        placeholders = ",".join("?" for _ in targets.transition_ids)
        rows = connection.execute(
            "SELECT transition.*, receipt.payload_digest "
            "FROM voice_state_transitions transition "
            "LEFT JOIN ingest_receipts receipt ON "
            "receipt.idempotency_digest = transition.idempotency_digest "
            f"WHERE transition.transition_id IN ({placeholders}) "
            "ORDER BY transition.event_at_us, transition.transition_id",
            targets.transition_ids,
        ).fetchall()
        if reason in {"user_requested", "admin_requested"}:
            connection.executemany(
                "INSERT INTO legal_minimal_events(event_id, owner_name, occurred_at_us) "
                "VALUES (?, ?, ?)",
                tuple(
                    (
                        uuid.uuid4().hex,
                        str(row["owner_name_snapshot"]),
                        int(row["event_at_us"]),
                    )
                    for row in rows
                    if int(row["event_at_us"]) > legal_cutoff_us
                ),
            )
        connection.executemany(
            "INSERT OR IGNORE INTO retired_receipts("
            "receipt_kind, receipt_digest, payload_digest, retired_generation, reason"
            ") VALUES ('voice_idempotency', ?, ?, ?, ?)",
            tuple(
                (
                    str(row["idempotency_digest"]),
                    row["payload_digest"],
                    retired_generation,
                    reason,
                )
                for row in rows
            ),
        )
        connection.execute(
            f"DELETE FROM voice_state_transitions WHERE transition_id IN ({placeholders})",
            targets.transition_ids,
        )
        connection.executemany(
            "DELETE FROM ingest_receipts WHERE idempotency_digest = ?",
            tuple((str(row["idempotency_digest"]),) for row in rows),
        )

    def _replace_deleted_records(
        self,
        connection: sqlite3.Connection,
        targets: _DeletionTargets,
        *,
        reason: str,
        created_generation: int,
        legal_cutoff_us: int,
    ) -> tuple[int, int]:
        all_ids = targets.owned_record_ids + targets.dependent_record_ids
        if not all_ids:
            return 0, 0
        placeholders = ",".join("?" for _ in all_ids)
        rows = connection.execute(
            f"SELECT * FROM records WHERE record_id IN ({placeholders}) ORDER BY created_seq",
            all_ids,
        ).fetchall()
        by_id = {str(row["record_id"]): row for row in rows}
        receipt_rows = connection.execute(
            f"SELECT idempotency_digest, payload_digest FROM record_receipts "
            f"WHERE record_id IN ({placeholders}) ORDER BY idempotency_digest",
            all_ids,
        ).fetchall()
        connection.executemany(
            "INSERT OR IGNORE INTO retired_receipts("
            "receipt_kind, receipt_digest, payload_digest, retired_generation, reason"
            ") VALUES ('record_idempotency', ?, ?, ?, ?)",
            tuple(
                (
                    str(receipt["idempotency_digest"]),
                    str(receipt["payload_digest"]),
                    created_generation,
                    reason,
                )
                for receipt in receipt_rows
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO retired_receipts("
            "receipt_kind, receipt_digest, payload_digest, retired_generation, reason"
            ") VALUES ('record_id', ?, NULL, ?, ?)",
            tuple(
                (
                    self._hmac(
                        _RETIRED_RECORD_ID_DOMAIN,
                        {"recordId": old_id},
                    ),
                    created_generation,
                    reason,
                )
                for old_id in all_ids
            ),
        )
        if reason in {"user_requested", "admin_requested"}:
            connection.executemany(
                "INSERT INTO legal_minimal_events(event_id, owner_name, occurred_at_us) "
                "VALUES (?, ?, ?)",
                tuple(
                    (
                        uuid.uuid4().hex,
                        row["owner_name_snapshot"],
                        int(row["ended_at_us"]),
                    )
                    for row in rows
                    if row["owner_name_snapshot"] is not None
                    and int(row["ended_at_us"]) > legal_cutoff_us
                ),
            )
        deleted_principal = targets.principal_id
        audiences_by_record: dict[str, tuple[str, ...]] = {}
        if reason in {"user_requested", "admin_requested"}:
            for source_id in targets.owned_record_ids:
                audience_rows = connection.execute(
                    "WITH RECURSIVE descendants(record_id) AS ("
                    " SELECT ? "
                    " UNION "
                    " SELECT edge.child_id FROM record_parents edge "
                    " JOIN descendants prior ON prior.record_id = edge.parent_id"
                    ") SELECT DISTINCT record.owner_principal_id "
                    "FROM descendants item JOIN records record "
                    "ON record.record_id = item.record_id "
                    "WHERE record.owner_principal_id IS NOT NULL "
                    "AND record.owner_principal_id <> ?",
                    (source_id, deleted_principal or ""),
                ).fetchall()
                audiences_by_record[source_id] = tuple(
                    sorted(str(item[0]) for item in audience_rows)
                )
        connection.execute(
            f"DELETE FROM records WHERE record_id IN ({placeholders})", all_ids
        )
        if reason == "user_requested":
            tombstone_text = ARCHIVE_TOMBSTONE_TEXT
        elif reason == "admin_requested":
            tombstone_text = ARCHIVE_ADMIN_TOMBSTONE_TEXT
        else:
            tombstone_text = ARCHIVE_RETENTION_TOMBSTONE_TEXT
        for old_id in targets.owned_record_ids:
            row = by_id.get(old_id)
            if row is None:
                continue
            placeholder_id = uuid.uuid4().hex
            rounded = _minute_floor(int(row["ended_at_us"]))
            connection.execute(
                "INSERT INTO records("
                "record_id, record_schema, mode, surface, record_type, owner_principal_id, "
                "owner_name_snapshot, guild_id, channel_id, started_at_us, "
                "ended_at_us, body, status, "
                "placeholder_id, deletion_reason, created_seq, created_generation"
                ") VALUES (?, ?, NULL, NULL, 'tombstone', NULL, NULL, NULL, NULL, ?, ?, ?, "
                "'tombstone', ?, ?, ?, ?)",
                (
                    placeholder_id,
                    ARCHIVE_RECORD_SCHEMA,
                    rounded,
                    rounded,
                    tombstone_text,
                    placeholder_id,
                    reason,
                    row["created_seq"],
                    created_generation,
                ),
            )
            if row["guild_id"] is not None:
                connection.executemany(
                    "INSERT OR IGNORE INTO tombstone_audiences("
                    "placeholder_id, principal_id, guild_id) VALUES (?, ?, ?)",
                    tuple(
                        (placeholder_id, principal, row["guild_id"])
                        for principal in audiences_by_record.get(old_id, ())
                    ),
                )
        for old_id in targets.dependent_record_ids:
            row = by_id.get(old_id)
            if row is None:
                continue
            quarantine_id = uuid.uuid4().hex
            rounded = _minute_floor(int(row["ended_at_us"]))
            connection.execute(
                "INSERT INTO records("
                "record_id, record_schema, mode, surface, record_type, owner_principal_id, "
                "owner_name_snapshot, guild_id, channel_id, started_at_us, "
                "ended_at_us, body, status, "
                "placeholder_id, deletion_reason, created_seq, created_generation"
                ") VALUES (?, ?, NULL, NULL, 'quarantined', NULL, NULL, NULL, NULL, ?, ?, ?, "
                "'quarantined', ?, ?, ?, ?)",
                (
                    quarantine_id,
                    ARCHIVE_RECORD_SCHEMA,
                    rounded,
                    rounded,
                    ARCHIVE_DEPENDENT_REDACTION_TEXT,
                    quarantine_id,
                    reason,
                    row["created_seq"],
                    created_generation,
                ),
            )
        return len(targets.owned_record_ids), len(targets.dependent_record_ids)

    def _apply_deletion_rows(
        self,
        connection: sqlite3.Connection,
        targets: _DeletionTargets,
        *,
        reason: str,
        request_id: str,
        requested_at_us: int,
        required_sinks: tuple[str, ...] | None = None,
    ) -> tuple[int, int, int]:
        effective_required_sinks = (
            self._required_purge_sinks
            if required_sinks is None
            else required_sinks
        )
        legal_cutoff_us = requested_at_us - int(
            self._retention.total_seconds() * 1_000_000
        )
        next_generation = self._metadata_int(connection, "generation") + 1
        purge_work_order = DeletionPurgeWorkOrder(
            request_id=request_id,
            reason=reason,
            requested_at=_from_utc_us(requested_at_us),
            deletion_generation=next_generation,
            principal_id=targets.principal_id,
            owned_record_ids=targets.owned_record_ids,
            dependent_record_ids=targets.dependent_record_ids,
            interval_ids=targets.interval_ids,
            scope_all=targets.scope_all,
            guild_id=targets.guild_id,
            started_at=_from_utc_us(targets.started_at_us),
            ended_at=_from_utc_us(targets.ended_at_us),
            required_sinks=effective_required_sinks,
            principal_ids=targets.principal_ids,
            principal_lookup_digests=targets.principal_lookup_digests,
            lineage_handles=targets.lineage_handles,
            lineage_complete=targets.lineage_complete,
            transition_ids=targets.transition_ids,
        )
        if effective_required_sinks and self._purge_freeze is not None:
            try:
                self._purge_freeze(purge_work_order)
            except Exception:
                raise ArchiveUnavailableError(
                    "archive_purge_freeze_failed"
                ) from None
        owned_count, dependent_count = self._replace_deleted_records(
            connection,
            targets,
            reason=reason,
            created_generation=next_generation,
            legal_cutoff_us=legal_cutoff_us,
        )
        self._truncate_intervals(
            connection,
            targets,
            reason=reason,
            legal_cutoff_us=legal_cutoff_us,
        )
        self._delete_voice_transitions(
            connection,
            targets,
            reason=reason,
            retired_generation=next_generation,
            legal_cutoff_us=legal_cutoff_us,
        )
        if targets.principal_id is not None:
            if targets.scope_all:
                connection.execute(
                    "INSERT OR IGNORE INTO retired_receipts("
                    "receipt_kind, receipt_digest, payload_digest, "
                    "retired_generation, reason"
                    ") SELECT 'voice_idempotency', idempotency_digest, "
                    "payload_digest, ?, ? FROM ingest_receipts "
                    "WHERE principal_id = ?",
                    (next_generation, reason, targets.principal_id),
                )
                connection.execute(
                    "DELETE FROM voice_state WHERE principal_id = ?",
                    (targets.principal_id,),
                )
                connection.execute(
                    "DELETE FROM ingest_receipts WHERE principal_id = ?",
                    (targets.principal_id,),
                )
            else:
                assert targets.started_at_us is not None and targets.ended_at_us is not None
                connection.execute(
                    "INSERT OR IGNORE INTO retired_receipts("
                    "receipt_kind, receipt_digest, payload_digest, "
                    "retired_generation, reason"
                    ") SELECT 'voice_idempotency', idempotency_digest, "
                    "payload_digest, ?, ? FROM ingest_receipts "
                    "WHERE principal_id = ? AND event_at_us >= ? AND event_at_us < ?",
                    (
                        next_generation,
                        reason,
                        targets.principal_id,
                        targets.started_at_us,
                        targets.ended_at_us,
                    ),
                )
                connection.execute(
                    "DELETE FROM ingest_receipts WHERE principal_id = ? "
                    "AND event_at_us >= ? AND event_at_us < ?",
                    (
                        targets.principal_id,
                        targets.started_at_us,
                        targets.ended_at_us,
                    ),
                )
                if targets.guild_id is None:
                    connection.execute(
                        "DELETE FROM voice_state WHERE principal_id = ? "
                        "AND updated_at_us >= ? AND updated_at_us < ?",
                        (
                            targets.principal_id,
                            targets.started_at_us,
                            targets.ended_at_us,
                        ),
                    )
                else:
                    connection.execute(
                        "DELETE FROM voice_state WHERE principal_id = ? AND guild_id = ? "
                        "AND updated_at_us >= ? AND updated_at_us < ?",
                        (
                            targets.principal_id,
                            targets.guild_id,
                            targets.started_at_us,
                            targets.ended_at_us,
                        ),
                    )
            if targets.scope_all:
                connection.execute(
                    "DELETE FROM principals WHERE principal_id = ?",
                    (targets.principal_id,),
                )
        if reason == "retention_expired":
            connection.execute(
                "DELETE FROM principals WHERE "
                "NOT EXISTS (SELECT 1 FROM records "
                "            WHERE records.owner_principal_id = principals.principal_id) "
                "AND NOT EXISTS (SELECT 1 FROM participation_intervals "
                "                WHERE participation_intervals.principal_id "
                "                      = principals.principal_id) "
                "AND NOT EXISTS (SELECT 1 FROM voice_state "
                "                WHERE voice_state.principal_id = principals.principal_id) "
                "AND NOT EXISTS (SELECT 1 FROM voice_state_transitions "
                "                WHERE voice_state_transitions.principal_id "
                "                      = principals.principal_id) "
                "AND NOT EXISTS (SELECT 1 FROM tombstone_audiences "
                "                WHERE tombstone_audiences.principal_id = principals.principal_id)"
            )
        if reason == "user_requested":
            display = ARCHIVE_DELETION_AUDIT_TEXT
        elif reason == "admin_requested":
            display = ARCHIVE_ADMIN_DELETION_AUDIT_TEXT
        else:
            display = ARCHIVE_RETENTION_TOMBSTONE_TEXT
        connection.execute(
            "INSERT INTO deletion_audits("
            "request_id, reason, status, primary_status, replica_status, display_text, "
            "requested_at_us, completed_at_us, deletion_generation, "
            "required_sinks_json, completed_sinks_json, purge_scope_json"
            ") VALUES (?, ?, 'local_cleanup_pending', 'local_cleanup_pending', "
            "'pending', ?, ?, NULL, ?, ?, '[]', ?)",
            (
                request_id,
                reason,
                display,
                requested_at_us,
                next_generation,
                json.dumps(
                    list(effective_required_sinks),
                    separators=(",", ":"),
                ),
                json.dumps(
                    {
                        "principalId": purge_work_order.principal_id,
                        "principalIds": list(
                            purge_work_order.principal_ids
                        ),
                        "principalLookupDigests": list(
                            purge_work_order.principal_lookup_digests
                        ),
                        "lineageHandles": [
                            {"kind": kind, "digest": digest}
                            for kind, digest in purge_work_order.lineage_handles
                        ],
                        "lineageComplete": purge_work_order.lineage_complete,
                        "ownedRecordIds": list(
                            purge_work_order.owned_record_ids
                        ),
                        "dependentRecordIds": list(
                            purge_work_order.dependent_record_ids
                        ),
                        "intervalIds": list(purge_work_order.interval_ids),
                        "transitionIds": list(
                            purge_work_order.transition_ids
                        ),
                        "scopeAll": purge_work_order.scope_all,
                        "guildId": purge_work_order.guild_id,
                        "startedAtUs": targets.started_at_us,
                        "endedAtUs": targets.ended_at_us,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        self._set_metadata(
            connection,
            "minimum_restorable_generation",
            next_generation,
        )
        return owned_count, dependent_count, len(targets.interval_ids)

    def _compact_primary(self) -> None:
        staging = self.primary_path.with_name(
            f".{self.primary_path.name}.compact-{uuid.uuid4().hex}"
        )
        _safe_unlink(staging)
        try:
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                connection.execute("VACUUM INTO ?", (str(staging),))
            _fsync_file(staging)
            if self._snapshot(staging) != self._snapshot(self.primary_path):
                raise ArchiveIntegrityError("archive_compaction_verification_failed")
            _unlink_required(Path(str(self.primary_path) + "-wal"))
            _unlink_required(Path(str(self.primary_path) + "-shm"))
            os.replace(staging, self.primary_path)
            _fsync_directory(self.primary_path.parent)
            self._verify_primary()
        finally:
            _safe_unlink(staging)

    def _set_audits_completed(
        self,
        request_ids: Sequence[str],
        *,
        completed_at_us: int,
    ) -> dict[str, tuple[str, str, str]]:
        if not request_ids:
            return {}
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in request_ids)
            rows = connection.execute(
                "SELECT request_id, required_sinks_json, "
                "completed_sinks_json, purge_scope_json "
                f"FROM deletion_audits WHERE request_id IN ({placeholders})",
                tuple(request_ids),
            ).fetchall()
            snapshots = {
                str(row["request_id"]): (
                    str(row["required_sinks_json"]),
                    str(row["completed_sinks_json"]),
                    str(row["purge_scope_json"]),
                )
                for row in rows
            }
            if set(snapshots) != set(request_ids):
                raise ArchiveIntegrityError(
                    "archive_deletion_request_missing"
                )
            connection.executemany(
                "UPDATE deletion_audits SET status = 'local_fully_purged', "
                "replica_status = 'deleted_verified', completed_at_us = ?, "
                "required_sinks_json = '[]', completed_sinks_json = '[]', "
                "purge_scope_json = '{}' "
                "WHERE request_id = ?",
                tuple((completed_at_us, request_id) for request_id in request_ids),
            )
            self._commit_mutation(connection, kind="deletion_cleanup_completed")
            self._commit_and_anchor(connection)
        return snapshots

    def _set_audits_replica_purged(self, request_ids: Sequence[str]) -> None:
        if not request_ids:
            return
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "UPDATE deletion_audits SET replica_status = 'deleted_verified' "
                "WHERE request_id = ?",
                tuple((request_id,) for request_id in request_ids),
            )
            self._commit_mutation(connection, kind="deletion_replica_verified")
            self._commit_and_anchor(connection)

    def _set_audits_primary_purged(self, request_ids: Sequence[str]) -> None:
        if not request_ids:
            return
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "UPDATE deletion_audits SET primary_status = 'local_fully_purged' "
                "WHERE request_id = ?",
                tuple((request_id,) for request_id in request_ids),
            )
            self._commit_mutation(connection, kind="deletion_primary_compacted")
            self._commit_and_anchor(connection)

    def _set_audits_pending(
        self,
        request_ids: Sequence[str],
        *,
        snapshots: Mapping[str, tuple[str, str, str]] | None = None,
    ) -> None:
        if not request_ids:
            return
        with closing(self._connect(self.primary_path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if snapshots is None:
                connection.executemany(
                    "UPDATE deletion_audits SET status = 'local_cleanup_pending', "
                    "replica_status = 'pending', completed_at_us = NULL "
                    "WHERE request_id = ?",
                    tuple((request_id,) for request_id in request_ids),
                )
            else:
                if set(snapshots) != set(request_ids):
                    raise ArchiveIntegrityError(
                        "archive_deletion_request_missing"
                    )
                connection.executemany(
                    "UPDATE deletion_audits SET "
                    "status = 'local_cleanup_pending', "
                    "replica_status = 'pending', completed_at_us = NULL, "
                    "required_sinks_json = ?, completed_sinks_json = ?, "
                    "purge_scope_json = ? WHERE request_id = ?",
                    tuple(
                        (*snapshots[request_id], request_id)
                        for request_id in request_ids
                    ),
                )
            self._commit_mutation(connection, kind="deletion_cleanup_pending")
            self._commit_and_anchor(connection)

    def _finish_deletion(
        self,
        *,
        request_id: str,
        now_us: int,
        owned_count: int,
        dependent_count: int,
        interval_count: int,
        display_text: str,
    ) -> DeletionResult:
        with closing(self._connect(self.primary_path, read_only=True)) as connection:
            audit = connection.execute(
                "SELECT required_sinks_json FROM deletion_audits WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        if audit is None:
            raise ArchiveIntegrityError("archive_deletion_request_missing")
        try:
            required_sinks = json.loads(str(audit["required_sinks_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ArchiveIntegrityError("archive_purge_work_order_invalid") from None
        if not isinstance(required_sinks, list):
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")
        try:
            self._compact_primary()
        except (ArchiveIntegrityError, OSError, sqlite3.Error):
            self._fault = "local_cleanup_pending"
            return DeletionResult(
                request_id=request_id,
                status="local_cleanup_pending",
                primary_status="local_cleanup_pending",
                replica_status="pending",
                affected_records=owned_count,
                dependent_records=dependent_count,
                affected_intervals=interval_count,
                display_text=display_text,
            )
        self._fault = None
        self._set_audits_primary_purged((request_id,))
        if not self._replicate_after_commit(now_us):
            return DeletionResult(
                request_id=request_id,
                status="local_cleanup_pending",
                primary_status="local_fully_purged",
                replica_status="pending",
                affected_records=owned_count,
                dependent_records=dependent_count,
                affected_intervals=interval_count,
                display_text=display_text,
            )
        if required_sinks:
            self._set_audits_replica_purged((request_id,))
            replica_verified = self._replicate_after_commit(now_us)
            if not replica_verified:
                self._set_audits_pending((request_id,))
            self._fault = "local_cleanup_pending"
            return DeletionResult(
                request_id=request_id,
                status="local_cleanup_pending",
                primary_status="local_fully_purged",
                replica_status=(
                    "deleted_verified" if replica_verified else "pending"
                ),
                affected_records=owned_count,
                dependent_records=dependent_count,
                affected_intervals=interval_count,
                display_text=display_text,
            )
        audit_snapshots = self._set_audits_completed(
            (request_id,), completed_at_us=now_us
        )
        if not self._replicate_after_commit(now_us):
            self._set_audits_pending(
                (request_id,), snapshots=audit_snapshots
            )
            return DeletionResult(
                request_id=request_id,
                status="local_cleanup_pending",
                primary_status="local_fully_purged",
                replica_status="pending",
                affected_records=owned_count,
                dependent_records=dependent_count,
                affected_intervals=interval_count,
                display_text=display_text,
            )
        return DeletionResult(
            request_id=request_id,
            status="local_fully_purged",
            primary_status="local_fully_purged",
            replica_status="deleted_verified",
            affected_records=owned_count,
            dependent_records=dependent_count,
            affected_intervals=interval_count,
            display_text=display_text,
        )

    @staticmethod
    def _purge_work_order_from_row(
        row: sqlite3.Row,
    ) -> DeletionPurgeWorkOrder:
        try:
            required = json.loads(str(row["required_sinks_json"]))
            scope = json.loads(str(row["purge_scope_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ArchiveIntegrityError(
                "archive_purge_work_order_invalid"
            ) from None
        expected_scope_keys = {
            "principalId",
            "principalIds",
            "principalLookupDigests",
            "lineageHandles",
            "lineageComplete",
            "ownedRecordIds",
            "dependentRecordIds",
            "intervalIds",
            "transitionIds",
            "scopeAll",
            "guildId",
            "startedAtUs",
            "endedAtUs",
        }
        if (
            not isinstance(required, list)
            or tuple(required) != tuple(sorted(set(required)))
            or any(
                not isinstance(sink, str)
                or sink not in ARCHIVE_REQUIRED_PURGE_SINKS
                for sink in required
            )
            or not isinstance(scope, dict)
            or frozenset(scope) not in {
                frozenset(expected_scope_keys),
                frozenset(expected_scope_keys - {"transitionIds"}),
            }
            or type(scope.get("scopeAll")) is not bool
            or type(scope.get("lineageComplete")) is not bool
        ):
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")

        def optional_id(value: object, *, maximum: int) -> str | None:
            if value is None:
                return None
            if (
                not isinstance(value, str)
                or not value
                or len(value) > maximum
                or value != value.strip()
                or any(ord(character) < 32 for character in value)
            ):
                raise ArchiveIntegrityError(
                    "archive_purge_work_order_invalid"
                )
            return value

        def id_tuple(value: object) -> tuple[str, ...]:
            if not isinstance(value, list):
                raise ArchiveIntegrityError(
                    "archive_purge_work_order_invalid"
                )
            normalized = tuple(
                optional_id(item, maximum=128) for item in value
            )
            if any(item is None for item in normalized) or len(
                set(normalized)
            ) != len(normalized):
                raise ArchiveIntegrityError(
                    "archive_purge_work_order_invalid"
                )
            return tuple(str(item) for item in normalized)

        def optional_us(value: object) -> int | None:
            if value is None:
                return None
            if type(value) is not int or value < 0:
                raise ArchiveIntegrityError(
                    "archive_purge_work_order_invalid"
                )
            return value

        owned_ids = id_tuple(scope.get("ownedRecordIds"))
        dependent_ids = id_tuple(scope.get("dependentRecordIds"))
        interval_ids = id_tuple(scope.get("intervalIds"))
        transition_ids = id_tuple(scope.get("transitionIds", []))
        principal_ids = id_tuple(scope.get("principalIds"))
        principal_lookup_digests = id_tuple(
            scope.get("principalLookupDigests")
        )
        raw_lineage_handles = scope.get("lineageHandles")
        if not isinstance(raw_lineage_handles, list):
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")
        lineage_handles: list[tuple[str, str]] = []
        for item in raw_lineage_handles:
            if (
                not isinstance(item, dict)
                or set(item) != {"kind", "digest"}
                or item.get("kind") not in ARCHIVE_LINEAGE_KINDS
                or not ConversationArchive._sha256_text(item.get("digest"))
            ):
                raise ArchiveIntegrityError(
                    "archive_purge_work_order_invalid"
                )
            lineage_handles.append(
                (str(item["kind"]), str(item["digest"]))
            )
        normalized_lineage_handles = tuple(sorted(set(lineage_handles)))
        if (
            len(lineage_handles) > 96
            or len(normalized_lineage_handles) != len(lineage_handles)
        ):
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")
        started_at_us = optional_us(scope.get("startedAtUs"))
        ended_at_us = optional_us(scope.get("endedAtUs"))
        requested_at_us = row["requested_at_us"]
        deletion_generation = row["deletion_generation"]
        reason = row["reason"]
        if (
            not (owned_ids or dependent_ids or interval_ids or transition_ids)
            or type(requested_at_us) is not int
            or requested_at_us < 0
            or type(deletion_generation) is not int
            or deletion_generation < 1
            or reason
            not in {"user_requested", "admin_requested", "retention_expired"}
            or any(
                not ConversationArchive._sha256_text(value)
                for value in principal_lookup_digests
            )
            or (
                started_at_us is not None
                and ended_at_us is not None
                and ended_at_us <= started_at_us
            )
        ):
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")
        requested_at = _from_utc_us(requested_at_us)
        if requested_at is None:
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")
        principal = optional_id(scope.get("principalId"), maximum=128)
        if principal is not None and principal not in principal_ids:
            raise ArchiveIntegrityError("archive_purge_work_order_invalid")
        return DeletionPurgeWorkOrder(
            request_id=optional_id(row["request_id"], maximum=64) or "",
            reason=str(reason),
            requested_at=requested_at,
            deletion_generation=deletion_generation,
            principal_id=principal,
            owned_record_ids=owned_ids,
            dependent_record_ids=dependent_ids,
            interval_ids=interval_ids,
            scope_all=scope["scopeAll"],
            guild_id=optional_id(scope.get("guildId"), maximum=64),
            started_at=_from_utc_us(started_at_us),
            ended_at=_from_utc_us(ended_at_us),
            required_sinks=tuple(required),
            principal_ids=principal_ids,
            principal_lookup_digests=principal_lookup_digests,
            lineage_handles=normalized_lineage_handles,
            lineage_complete=scope["lineageComplete"],
            transition_ids=transition_ids,
        )

    def pending_purge_work_orders(
        self,
        *,
        limit: int = 100,
        after: tuple[datetime, str] | None = None,
    ) -> tuple[DeletionPurgeWorkOrder, ...]:
        """Return bounded, content-free work needed by registered sink owners."""

        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ArchiveValidationError("archive_purge_limit_invalid")
            cursor: tuple[int, str] | None = None
            if after is not None:
                if type(after) is not tuple or len(after) != 2:
                    raise ArchiveValidationError(
                        "archive_purge_cursor_invalid"
                    )
                try:
                    cursor = (
                        _as_utc_us(
                            after[0],
                            code="archive_purge_cursor_invalid",
                        ),
                        _require_text(
                            after[1],
                            code="archive_purge_cursor_invalid",
                            maximum=64,
                        ),
                    )
                except (TypeError, ValueError):
                    raise ArchiveValidationError(
                        "archive_purge_cursor_invalid"
                    ) from None
            query = (
                "SELECT * FROM deletion_audits "
                "WHERE status = 'local_cleanup_pending' "
                "AND required_sinks_json <> '[]' "
            )
            parameters: tuple[Any, ...]
            if cursor is None:
                parameters = (limit,)
            else:
                query += (
                    "AND (requested_at_us > ? OR "
                    "(requested_at_us = ? AND request_id > ?)) "
                )
                parameters = (cursor[0], cursor[0], cursor[1], limit)
            query += "ORDER BY requested_at_us, request_id LIMIT ?"
            with closing(
                self._connect(self.primary_path, read_only=True)
            ) as connection:
                rows = connection.execute(query, parameters).fetchall()
            return tuple(self._purge_work_order_from_row(row) for row in rows)

    def deletion_purge_work_order(
        self,
        *,
        request_id: str,
    ) -> DeletionPurgeWorkOrder | None:
        """Return one pending work order without exposing deleted content."""

        with self._thread_lock:
            self._require_open()
            self._verify_primary()
            normalized = _require_text(
                request_id,
                code="archive_deletion_request_invalid",
                maximum=64,
            )
            with closing(
                self._connect(self.primary_path, read_only=True)
            ) as connection:
                row = connection.execute(
                    "SELECT * FROM deletion_audits WHERE request_id = ? "
                    "AND status = 'local_cleanup_pending' "
                    "AND required_sinks_json <> '[]'",
                    (normalized,),
                ).fetchone()
            return None if row is None else self._purge_work_order_from_row(row)

    def submit_purge_receipts(
        self,
        *,
        request_id: str,
        receipts: Iterable[Mapping[str, Any]],
        now: datetime | None = None,
    ) -> bool:
        """Complete a deletion only after every registered sink proves zero copies.

        Receipts are deliberately content-free and bound to the deletion
        cutover generation.  Partial, duplicated, stale, or unregistered sink
        claims leave the request hidden in ``local_cleanup_pending``.
        """

        with self._thread_lock:
            now_us = self._now_us(now)
            self._ensure_writable(now_us, deletion=True)
            normalized_request = _require_text(
                request_id,
                code="archive_deletion_request_invalid",
                maximum=64,
            )
            supplied = tuple(receipts)
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                audit = connection.execute(
                    "SELECT * FROM deletion_audits WHERE request_id = ?",
                    (normalized_request,),
                ).fetchone()
                if audit is None:
                    raise ArchiveValidationError(
                        "archive_deletion_request_missing"
                    )
                if audit["status"] == "local_fully_purged":
                    connection.rollback()
                    return False
                try:
                    required = tuple(
                        json.loads(str(audit["required_sinks_json"]))
                    )
                except (TypeError, json.JSONDecodeError):
                    raise ArchiveIntegrityError(
                        "archive_purge_receipts_invalid"
                    ) from None
                if required != tuple(sorted(set(required))) or any(
                    sink not in ARCHIVE_REQUIRED_PURGE_SINKS
                    for sink in required
                ):
                    raise ArchiveIntegrityError(
                        "archive_purge_receipts_invalid"
                    )
                expected_generation = int(audit["deletion_generation"])
                verified_sinks: list[str] = []
                for receipt in supplied:
                    if not isinstance(receipt, Mapping) or set(receipt) != {
                        "sink",
                        "deletionGeneration",
                        "contentFree",
                        "complete",
                        "remainingCopies",
                        "manualReviewCount",
                    }:
                        raise ArchiveValidationError(
                            "archive_purge_receipt_invalid"
                        )
                    sink = receipt.get("sink")
                    if (
                        not isinstance(sink, str)
                        or sink not in required
                        or type(receipt.get("deletionGeneration")) is not int
                        or receipt["deletionGeneration"] != expected_generation
                        or receipt.get("contentFree") is not True
                        or receipt.get("complete") is not True
                        or receipt.get("remainingCopies") != 0
                        or receipt.get("manualReviewCount") != 0
                    ):
                        raise ArchiveValidationError(
                            "archive_purge_receipt_invalid"
                        )
                    verified_sinks.append(sink)
                if tuple(sorted(verified_sinks)) != required:
                    raise ArchiveValidationError(
                        "archive_purge_receipts_incomplete"
                    )
                if (
                    audit["primary_status"] != "local_fully_purged"
                    or audit["replica_status"] != "deleted_verified"
                ):
                    raise ArchiveUnavailableError("local_cleanup_pending")
                if self._snapshot(self.replica_path) != self._snapshot(
                    self.primary_path
                ):
                    raise ArchiveUnavailableError("backup_pending")
                connection.execute(
                    "UPDATE deletion_audits SET completed_sinks_json = ? "
                    "WHERE request_id = ?",
                    (
                        json.dumps(verified_sinks, separators=(",", ":")),
                        normalized_request,
                    ),
                )
                self._commit_mutation(
                    connection, kind="deletion_external_sinks_verified"
                )
                self._commit_and_anchor(connection)
            if not self._replicate_after_commit(now_us):
                self._fault = "local_cleanup_pending"
                return False
            audit_snapshots = self._set_audits_completed(
                (normalized_request,), completed_at_us=now_us
            )
            if not self._replicate_after_commit(now_us):
                self._set_audits_pending(
                    (normalized_request,), snapshots=audit_snapshots
                )
                self._fault = "local_cleanup_pending"
                return False
            with closing(
                self._connect(self.primary_path, read_only=True)
            ) as connection:
                still_pending = connection.execute(
                    "SELECT 1 FROM deletion_audits "
                    "WHERE status = 'local_cleanup_pending' LIMIT 1"
                ).fetchone()
            self._fault = None if still_pending is None else "local_cleanup_pending"
            return True

    def apply_user_deletion(
        self,
        *,
        preview_id: str,
        actor_external_id: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        with self._thread_lock:
            now_us = self._now_us(now)
            self._ensure_writable(now_us, deletion=True)
            preview = _require_text(
                preview_id, code="archive_preview_id_invalid", maximum=64
            )
            actor_lookup = self._principal_lookup("discord", actor_external_id)
            conflict: str | None = None
            request_id = uuid.uuid4().hex
            owned_count = dependent_count = interval_count = 0
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM deletion_previews WHERE preview_id = ?", (preview,)
                ).fetchone()
                if row is None:
                    used = connection.execute(
                        "SELECT 1 FROM used_previews WHERE preview_id = ?", (preview,)
                    ).fetchone()
                    if used is not None:
                        raise ArchivePreviewConsumed("archive_preview_consumed")
                    raise ArchivePreviewExpired("archive_preview_missing")
                if row["preview_kind"] != "self":
                    raise ArchiveAuthorizationError("archive_preview_domain_mismatch")
                if not hmac.compare_digest(str(row["actor_lookup_digest"]), actor_lookup):
                    raise ArchiveAuthorizationError("archive_preview_wrong_actor")
                if now_us >= int(row["expires_at_us"]):
                    connection.execute(
                        "DELETE FROM deletion_previews WHERE preview_id = ?", (preview,)
                    )
                    connection.execute(
                        "INSERT INTO used_previews(preview_id, consumed_at_us) VALUES (?, ?)",
                        (preview, now_us),
                    )
                    self._commit_mutation(connection, kind="deletion_preview_expired")
                    self._commit_and_anchor(connection)
                    conflict = "expired"
                else:
                    principal_id = self._find_principal(
                        connection, "discord", actor_external_id
                    )
                    targets = self._deletion_targets(
                        connection,
                        principal_id=principal_id,
                        scope_all=bool(row["scope_all"]),
                        guild_id=row["request_guild_id"] if not row["scope_all"] else None,
                        started_at_us=row["started_at_us"],
                        ended_at_us=row["ended_at_us"],
                    )
                    current_generation = self._metadata_int(connection, "generation")
                    fingerprint = self._target_fingerprint(
                        connection, targets, generation=current_generation
                    )
                    if (
                        current_generation != int(row["snapshot_generation"])
                        or not hmac.compare_digest(
                            fingerprint, str(row["target_fingerprint"])
                        )
                    ):
                        connection.execute(
                            "DELETE FROM deletion_previews WHERE preview_id = ?", (preview,)
                        )
                        connection.execute(
                            "INSERT INTO used_previews(preview_id, consumed_at_us) VALUES (?, ?)",
                            (preview, now_us),
                        )
                        self._commit_mutation(connection, kind="deletion_preview_conflict")
                        self._commit_and_anchor(connection)
                        conflict = "changed"
                    else:
                        connection.execute(
                            "DELETE FROM deletion_previews WHERE preview_id = ?", (preview,)
                        )
                        connection.execute(
                            "INSERT INTO used_previews(preview_id, consumed_at_us) VALUES (?, ?)",
                            (preview, now_us),
                        )
                        owned_count, dependent_count, interval_count = self._apply_deletion_rows(
                            connection,
                            targets,
                            reason="user_requested",
                            request_id=request_id,
                            requested_at_us=now_us,
                        )
                        self._commit_mutation(
                            connection,
                            kind="user_deletion_applied",
                            reset_chain=True,
                        )
                        self._commit_and_anchor(connection)
            if conflict is not None:
                self._replicate_after_commit(now_us)
                if conflict == "expired":
                    raise ArchivePreviewExpired("archive_preview_expired")
                raise ArchivePreviewConflict("archive_preview_changed")
            return self._finish_deletion(
                request_id=request_id,
                now_us=now_us,
                owned_count=owned_count,
                dependent_count=dependent_count,
                interval_count=interval_count,
                display_text=ARCHIVE_DELETION_AUDIT_TEXT,
            )

    def apply_admin_deletion(
        self,
        *,
        authorized: bool,
        preview_id: str,
        now: datetime | None = None,
    ) -> DeletionResult:
        if authorized is not True:
            raise ArchiveAuthorizationError("archive_admin_required")
        with self._thread_lock:
            now_us = self._now_us(now)
            self._ensure_writable(now_us, deletion=True)
            preview = _require_text(
                preview_id, code="archive_preview_id_invalid", maximum=64
            )
            request_id = uuid.uuid4().hex
            conflict: str | None = None
            owned_count = dependent_count = interval_count = 0
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM deletion_previews WHERE preview_id = ?", (preview,)
                ).fetchone()
                if row is None:
                    used = connection.execute(
                        "SELECT 1 FROM used_previews WHERE preview_id = ?", (preview,)
                    ).fetchone()
                    if used is not None:
                        raise ArchivePreviewConsumed("archive_preview_consumed")
                    raise ArchivePreviewExpired("archive_preview_missing")
                if row["preview_kind"] != "admin":
                    raise ArchiveAuthorizationError("archive_preview_domain_mismatch")
                if now_us >= int(row["expires_at_us"]):
                    connection.execute(
                        "DELETE FROM deletion_previews WHERE preview_id = ?", (preview,)
                    )
                    connection.execute(
                        "INSERT INTO used_previews(preview_id, consumed_at_us) VALUES (?, ?)",
                        (preview, now_us),
                    )
                    self._commit_mutation(
                        connection, kind="admin_deletion_preview_expired"
                    )
                    self._commit_and_anchor(connection)
                    conflict = "expired"
                else:
                    try:
                        decoded_ids = json.loads(str(row["admin_record_ids_json"]))
                    except json.JSONDecodeError:
                        raise ArchiveIntegrityError("archive_admin_preview_invalid") from None
                    if (
                        not isinstance(decoded_ids, list)
                        or any(
                            not isinstance(record_id, str) or not record_id
                            for record_id in decoded_ids
                        )
                        or decoded_ids != sorted(set(decoded_ids))
                    ):
                        raise ArchiveIntegrityError("archive_admin_preview_invalid")
                    target_principal = row["admin_target_principal_id"]
                    if (target_principal is None) == (not decoded_ids):
                        raise ArchiveIntegrityError("archive_admin_preview_invalid")
                    targets = self._deletion_targets(
                        connection,
                        principal_id=target_principal,
                        scope_all=bool(row["scope_all"]),
                        guild_id=None,
                        started_at_us=row["started_at_us"],
                        ended_at_us=row["ended_at_us"],
                        explicit_record_ids=tuple(decoded_ids),
                    )
                    current_generation = self._metadata_int(connection, "generation")
                    fingerprint = self._target_fingerprint(
                        connection,
                        targets,
                        generation=current_generation,
                        domain=_ADMIN_PREVIEW_DOMAIN,
                    )
                    if (
                        current_generation != int(row["snapshot_generation"])
                        or not hmac.compare_digest(
                            fingerprint, str(row["target_fingerprint"])
                        )
                    ):
                        connection.execute(
                            "DELETE FROM deletion_previews WHERE preview_id = ?", (preview,)
                        )
                        connection.execute(
                            "INSERT INTO used_previews(preview_id, consumed_at_us) VALUES (?, ?)",
                            (preview, now_us),
                        )
                        self._commit_mutation(
                            connection, kind="admin_deletion_preview_conflict"
                        )
                        self._commit_and_anchor(connection)
                        conflict = "changed"
                    else:
                        connection.execute(
                            "DELETE FROM deletion_previews WHERE preview_id = ?", (preview,)
                        )
                        connection.execute(
                            "INSERT INTO used_previews(preview_id, consumed_at_us) VALUES (?, ?)",
                            (preview, now_us),
                        )
                        (
                            owned_count,
                            dependent_count,
                            interval_count,
                        ) = self._apply_deletion_rows(
                            connection,
                            targets,
                            reason="admin_requested",
                            request_id=request_id,
                            requested_at_us=now_us,
                        )
                        self._commit_mutation(
                            connection,
                            kind="admin_deletion_applied",
                            reset_chain=True,
                        )
                        self._commit_and_anchor(connection)
            if conflict is not None:
                self._replicate_after_commit(now_us)
                if conflict == "expired":
                    raise ArchivePreviewExpired("archive_preview_expired")
                raise ArchivePreviewConflict("archive_preview_changed")
            return self._finish_deletion(
                request_id=request_id,
                now_us=now_us,
                owned_count=owned_count,
                dependent_count=dependent_count,
                interval_count=interval_count,
                display_text=ARCHIVE_ADMIN_DELETION_AUDIT_TEXT,
            )

    def prune_expired(
        self,
        *,
        now: datetime | None = None,
        batch_size: int = 100,
    ) -> RetentionResult | None:
        """Purge the oldest expired archive and legal-minimal rows first."""

        with self._thread_lock:
            now_us = self._now_us(now)
            self._ensure_writable(now_us, deletion=True)
            if type(batch_size) is not int or not 1 <= batch_size <= 1000:
                raise ArchiveValidationError("archive_retention_batch_invalid")
            cutoff_us = now_us - int(self._retention.total_seconds() * 1_000_000)
            request_id = uuid.uuid4().hex
            with closing(self._connect(self.primary_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                durable_types = tuple(sorted(_DURABLE_SYSTEM_RECORD_TYPES))
                durable_placeholders = ",".join("?" for _ in durable_types)
                candidates = connection.execute(
                    "SELECT candidate_kind, candidate_id FROM ("
                    " SELECT 'record' AS candidate_kind, record_id AS candidate_id, "
                    " ended_at_us AS expiry_time, created_seq AS stable_order "
                    " FROM records WHERE status = 'active' AND ended_at_us <= ? "
                    f" AND record_type NOT IN ({durable_placeholders}) "
                    " UNION ALL "
                    " SELECT 'interval', interval_id, ended_at_us, started_at_us "
                    " FROM participation_intervals "
                    " WHERE ended_at_us IS NOT NULL AND ended_at_us <= ?"
                    " UNION ALL "
                    " SELECT 'transition', transition_id, event_at_us, event_at_us "
                    " FROM voice_state_transitions WHERE event_at_us <= ?"
                    " UNION ALL "
                    " SELECT 'legal_minimal', event_id, occurred_at_us, occurred_at_us "
                    " FROM legal_minimal_events WHERE occurred_at_us <= ?"
                    ") ORDER BY expiry_time, stable_order, candidate_id LIMIT ?",
                    (
                        cutoff_us,
                        *durable_types,
                        cutoff_us,
                        cutoff_us,
                        cutoff_us,
                        batch_size,
                    ),
                ).fetchall()
                if not candidates:
                    connection.rollback()
                    return None
                record_ids = tuple(
                    str(row["candidate_id"])
                    for row in candidates
                    if row["candidate_kind"] == "record"
                )
                interval_ids = tuple(
                    str(row["candidate_id"])
                    for row in candidates
                    if row["candidate_kind"] == "interval"
                )
                transition_ids = tuple(
                    str(row["candidate_id"])
                    for row in candidates
                    if row["candidate_kind"] == "transition"
                )
                legal_event_ids = tuple(
                    str(row["candidate_id"])
                    for row in candidates
                    if row["candidate_kind"] == "legal_minimal"
                )
                targets = self._deletion_targets(
                    connection,
                    principal_id=None,
                    scope_all=True,
                    guild_id=None,
                    started_at_us=None,
                    ended_at_us=None,
                    explicit_record_ids=record_ids,
                    explicit_interval_ids=interval_ids,
                    explicit_transition_ids=transition_ids,
                )
                if legal_event_ids:
                    placeholders = ",".join("?" for _ in legal_event_ids)
                    connection.execute(
                        f"DELETE FROM legal_minimal_events WHERE event_id IN ({placeholders})",
                        legal_event_ids,
                    )
                has_external_targets = bool(
                    targets.owned_record_ids
                    or targets.dependent_record_ids
                    or targets.interval_ids
                    or targets.transition_ids
                )
                owned_count, dependent_count, interval_count = self._apply_deletion_rows(
                    connection,
                    targets,
                    reason="retention_expired",
                    request_id=request_id,
                    requested_at_us=now_us,
                    required_sinks=(
                        self._required_purge_sinks if has_external_targets else ()
                    ),
                )
                self._commit_mutation(
                    connection,
                    kind="retention_expired",
                    reset_chain=True,
                )
                self._commit_and_anchor(connection)
            result = self._finish_deletion(
                request_id=request_id,
                now_us=now_us,
                owned_count=owned_count,
                dependent_count=dependent_count,
                interval_count=interval_count,
                display_text=ARCHIVE_RETENTION_TOMBSTONE_TEXT,
            )
            return RetentionResult(
                request_id=result.request_id,
                status=result.status,
                affected_records=result.affected_records,
                dependent_records=result.dependent_records,
                affected_intervals=result.affected_intervals,
            )

    def reconcile_replica(self, *, now: datetime | None = None) -> ArchiveHealth:
        """Rebuild a missing/stale valid replica and finish pending deletions.

        A corrupt, same-generation divergent, or future replica is never
        overwritten automatically.  Removing/quarantining that file is an
        explicit host-operator action; a missing destination may then be
        recreated here.
        """

        with self._thread_lock:
            self._require_open()
            now_us = self._now_us(now)
            self._verify_primary()
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                pending_rows = connection.execute(
                    "SELECT request_id, required_sinks_json FROM deletion_audits "
                    "WHERE status = 'local_cleanup_pending' ORDER BY requested_at_us"
                ).fetchall()
                pending_ids = tuple(str(row["request_id"]) for row in pending_rows)
                core_only_ids = tuple(
                    str(row["request_id"])
                    for row in pending_rows
                    if row["required_sinks_json"] == "[]"
                )
            if pending_ids:
                try:
                    self._compact_primary()
                except (ArchiveIntegrityError, OSError, sqlite3.Error):
                    self._fault = "local_cleanup_pending"
                    return self.health(now=now)
                self._fault = None
                self._set_audits_primary_purged(pending_ids)
            if self._fault == "backup_integrity_blocked":
                if self.replica_path.exists():
                    return self.health(now=now)
                self._fault = None
            if not self._replicate_after_commit(now_us):
                return self.health(now=now)
            if pending_ids:
                self._set_audits_replica_purged(pending_ids)
                if not self._replicate_after_commit(now_us):
                    self._set_audits_pending(pending_ids)
                    return self.health(now=now)
            if core_only_ids:
                audit_snapshots = self._set_audits_completed(
                    core_only_ids, completed_at_us=now_us
                )
                if not self._replicate_after_commit(now_us):
                    self._set_audits_pending(
                        core_only_ids, snapshots=audit_snapshots
                    )
            if pending_ids and len(core_only_ids) != len(pending_ids):
                self._fault = "local_cleanup_pending"
            return self.health(now=now)

    def assert_restore_candidate(self, candidate_path: Path) -> tuple[int, str]:
        """Validate that a candidate is not older than the deletion cutover."""

        with self._thread_lock:
            self._require_open()
            current_generation, current_tag = self._verify_primary()
            candidate = Path(candidate_path)
            candidate_generation, candidate_tag = self._snapshot(candidate)
            with closing(self._connect(self.primary_path, read_only=True)) as connection:
                minimum = self._metadata_int(
                    connection, "minimum_restorable_generation"
                )
                current_witness = {
                    "generation": self._metadata_int(
                        connection, "cutover_generation"
                    ),
                    "chainEpoch": self._metadata_int(connection, "cutover_epoch"),
                    "nonce": self._metadata(connection, "cutover_nonce"),
                }
            anchor = self._read_anchor()
            if anchor is not None:
                if int(anchor["minimumRestorableGeneration"]) != minimum:
                    raise ArchiveIntegrityError("anchor_database_mismatch")
                minimum = int(anchor["minimumRestorableGeneration"])
                anchor_witness = anchor["cutoverWitness"]
                if any(
                    anchor_witness[key] != current_witness[key]
                    for key in ("generation", "chainEpoch", "nonce")
                ):
                    raise ArchiveIntegrityError("anchor_database_mismatch")
            if candidate_generation < minimum or candidate_generation > current_generation:
                raise ArchiveIntegrityError("archive_restore_generation_rejected")
            with closing(self._connect(candidate, read_only=True)) as connection:
                candidate_minimum = self._metadata_int(
                    connection, "minimum_restorable_generation"
                )
                candidate_witness = {
                    "generation": self._metadata_int(
                        connection, "cutover_generation"
                    ),
                    "chainEpoch": self._metadata_int(connection, "cutover_epoch"),
                    "nonce": self._metadata(connection, "cutover_nonce"),
                }
            if candidate_minimum != minimum or candidate_witness != current_witness:
                raise ArchiveIntegrityError("archive_restore_cutover_rejected")
            if candidate_generation == current_generation and not hmac.compare_digest(
                candidate_tag, current_tag
            ):
                raise ArchiveIntegrityError("archive_restore_state_rejected")
            return candidate_generation, candidate_tag

    def restore_from_replica(self) -> tuple[int, str]:
        """Atomically restore the primary from the exact anchor-bound D: replica.

        Recovery is deliberately stricter than candidate inspection: only the
        configured replica may be used and every anchor field must match.  A
        lagging, future, divergent, pre-delete, or foreign-key database is
        rejected before the primary path is touched.
        """

        with self._thread_lock:
            acquired_here = False
            if not self._opened:
                self.primary_path.parent.mkdir(parents=True, exist_ok=True)
                if (
                    self.primary_path.is_symlink()
                    or self.replica_path.is_symlink()
                    or self.anchor_path is None
                    or self.anchor_path.is_symlink()
                ):
                    raise ArchiveUnavailableError("archive_path_rejected")
                self._lock_manager = InstanceLockManager(
                    build_instance_lock_runtime_deps(
                        self.primary_path.with_name(
                            self.primary_path.name + ".writer.lock"
                        )
                    )
                )
                try:
                    self._lock_manager.acquire(
                        wait_sec=self._writer_lock_wait_seconds
                    )
                except RuntimeError:
                    self._lock_manager = None
                    raise ArchiveUnavailableError("writer_lease_lost") from None
                acquired_here = True
            elif self._lock_manager is None:
                self._fault = "writer_lease_lost"
                raise ArchiveUnavailableError("writer_lease_lost")

            staging = self.primary_path.with_name(
                f".{self.primary_path.name}.restore-{uuid.uuid4().hex}"
            )
            try:
                anchor = self._read_anchor()
                if anchor is None:
                    raise ArchiveIntegrityError("archive_anchor_required")
                replica_snapshot = self._snapshot(self.replica_path)
                with closing(
                    self._connect(self.replica_path, read_only=True)
                ) as replica_connection:
                    replica_anchor = self._anchor_unsigned_from_connection(
                        replica_connection
                    )
                expected_anchor = {
                    key: value for key, value in anchor.items() if key != "authTag"
                }
                if replica_anchor["generation"] < anchor["minimumRestorableGeneration"]:
                    raise ArchiveIntegrityError(
                        "archive_restore_generation_rejected"
                    )
                if replica_anchor["generation"] != anchor["generation"]:
                    raise ArchiveIntegrityError(
                        "archive_restore_generation_rejected"
                    )
                if replica_anchor["cutoverWitness"] != anchor["cutoverWitness"]:
                    raise ArchiveIntegrityError("archive_restore_cutover_rejected")
                if replica_anchor != expected_anchor:
                    raise ArchiveIntegrityError("archive_restore_state_rejected")

                _safe_unlink(staging)
                with closing(
                    self._connect(self.replica_path, read_only=True)
                ) as source:
                    destination = sqlite3.connect(str(staging), timeout=5.0)
                    try:
                        source.backup(destination)
                        destination.commit()
                    finally:
                        destination.close()
                _fsync_file(staging)
                if self._snapshot(staging) != replica_snapshot:
                    raise ArchiveIntegrityError(
                        "archive_restore_staging_verification_failed"
                    )

                _unlink_required(Path(str(self.primary_path) + "-wal"))
                _unlink_required(Path(str(self.primary_path) + "-shm"))
                os.replace(staging, self.primary_path)
                _fsync_directory(self.primary_path.parent)
                restored = self._verify_database(self.primary_path)
                self._verify_anchor_against_primary()
                if restored != replica_snapshot:
                    raise ArchiveIntegrityError(
                        "archive_restore_state_rejected"
                    )
                self._opened = True
                self._fault = None
                return restored
            except Exception:
                if acquired_here:
                    assert self._lock_manager is not None
                    self._lock_manager.release()
                    self._lock_manager = None
                    self._opened = False
                raise
            finally:
                _safe_unlink(staging)


__all__ = [
    "ARCHIVE_ADMIN_DELETION_AUDIT_TEXT",
    "ARCHIVE_ADMIN_TOMBSTONE_TEXT",
    "ARCHIVE_ANCHOR_SCHEMA",
    "ARCHIVE_BACKUP_GRACE_SECONDS",
    "ARCHIVE_DELETION_AUDIT_TEXT",
    "ARCHIVE_DELETION_CONFIRM_SECONDS",
    "ARCHIVE_DEPENDENT_REDACTION_TEXT",
    "ARCHIVE_LINEAGE_KINDS",
    "ARCHIVE_RECORD_SCHEMA",
    "ARCHIVE_REQUIRED_PURGE_SINKS",
    "ARCHIVE_RETENTION_DAYS",
    "ARCHIVE_RETENTION_TOMBSTONE_TEXT",
    "ARCHIVE_SCHEMA_VERSION",
    "ARCHIVE_TOMBSTONE_TEXT",
    "ArchiveAuthorizationError",
    "ArchiveHealth",
    "ArchiveIntegrityError",
    "ArchivePreviewConflict",
    "ArchivePreviewConsumed",
    "ArchivePreviewExpired",
    "ArchiveRecord",
    "ArchiveRecordPage",
    "ArchiveStaleEvent",
    "ArchiveUnavailableError",
    "ArchiveValidationError",
    "ConversationArchive",
    "ConversationArchiveError",
    "DeletionPreview",
    "DeletionPurgeWorkOrder",
    "DeletionResult",
    "FeedbackSourceBinding",
    "LegalMinimalEvent",
    "LegalMinimalEventPage",
    "ParticipationInterval",
    "ParticipationIntervalPage",
    "RetentionResult",
    "VoiceStateTransition",
    "VoiceStateTransitionPage",
    "archive_lineage_handle",
]
