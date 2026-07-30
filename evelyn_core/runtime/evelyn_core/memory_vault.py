from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .assistant_contracts import MemoryRecallRequest, MemoryRecallResult
from .config import MEMORY_ROOT, MEMORY_ROW_MAX_CHARS, SUMMARY_LLM_URL, SUMMARY_MODEL_NAME
from .memory_derivation_revocation import (
    DerivationNode,
    DerivationResolution,
    changed_quarantine_ids,
    resolve_derivation_states,
)
from .memory_provenance_audit import (
    DIRECT_SOURCE_TYPES,
    ProvenanceAuditNode,
    ProvenanceAuditResult,
    audit_missing_derivations,
    summarize_provenance_coverage,
)
from .runtime_artifact_io import atomic_json_write, atomic_text_write
from .text import clean_text


VAULT_DIR_NAME = "memory_vault"
INDEX_DIR_NAME = "memory_index"
INDEX_DB_NAME = "memory.sqlite"
USER_NOTE_STATE_NAME = "user_note_state.json"
DELETION_TOMBSTONES_NAME = "memory_deletions.jsonl"
DERIVATION_REVOCATIONS_NAME = "memory_derivation_revocations.json"
PROVENANCE_AUDIT_NAME = "memory_provenance_backfill_audit.json"
PROVENANCE_FORWARD_REJECTIONS_NAME = (
    "memory_provenance_forward_write_rejections.json"
)
RETRIEVAL_CACHE_TTL_SECONDS = 300
MEMORY_DELETE_PREVIEW_TTL_SECONDS = 120
MEMORY_PROVENANCE_BACKFILL_PREVIEW_TTL_SECONDS = 120
DEFAULT_PROJECT = "evelyn"
DAILY_USER_LABEL = os.getenv("MEMORY_DAILY_USER_LABEL", "정훈")
DAILY_ASSISTANT_LABEL = os.getenv("MEMORY_DAILY_ASSISTANT_LABEL", "이블린")
VECTOR_INDEX_MODEL = "hashing-v1"
VECTOR_INDEX_DIMENSIONS = 384
CONSOLIDATION_MIN_DAILY_CHARS = 1200
HOT_CONTEXT_MAX_CHARS = 2400
BOOTSTRAP_NOTE_SOURCE = "memory-vault-bootstrap"
SEMANTIC_CONSOLIDATION_MAX_SOURCE_CHARS = 7000
SEMANTIC_CONSOLIDATION_MAX_NOTES = 6
MEMORY_GRAPH_MAX_NODES = 160
MEMORY_GRAPH_MAX_GROUP_EDGES = 180
MEMORY_GRAPH_MAX_VECTOR_EDGES = 220
MEMORY_INTERNAL_NOTE_TYPES = {"procedure", "internal", "system", "debug", "runtime", "tool"}
MEMORY_GRAPH_INTERNAL_NOTE_TYPES = frozenset(MEMORY_INTERNAL_NOTE_TYPES)
MEMORY_PROVENANCE_SCHEMA = "memory.provenance.v1"
MEMORY_DELETE_PREVIEW_SCHEMA = "memory.deletion.preview.v1"
MEMORY_DELETE_RESULT_SCHEMA = "memory.deletion.result.v1"
MEMORY_DELETE_TOMBSTONE_SCHEMA = "memory.deletion.tombstone.v1"
MEMORY_DERIVATION_IMPACT_SCHEMA = "memory.derivation.impact.v1"
MEMORY_DERIVATION_REVOCATIONS_SCHEMA = "memory.derivation.revocations.v1"
MEMORY_DERIVATION_RECOMPOSITION_SCHEMA = "memory.derivation.recomposition.v1"
MEMORY_PROVENANCE_BACKFILL_AUDIT_SCHEMA = (
    "memory.provenance.backfill-audit.v1"
)
MEMORY_PROVENANCE_BACKFILL_PREVIEW_SCHEMA = (
    "memory.provenance.backfill-preview.v1"
)
MEMORY_PROVENANCE_BACKFILL_RESULT_SCHEMA = (
    "memory.provenance.backfill-result.v1"
)
MEMORY_PROVENANCE_MANUAL_OPTIONS_SCHEMA = (
    "memory.provenance.manual-source-options.v1"
)
MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA = (
    "memory.provenance.forward-write-rejections.v1"
)
MEMORY_QUARANTINE_STATUS_SCHEMA = "memory.quarantine.status.v1"
MEMORY_EDIT_RESULT_SCHEMA = "memory.edit.result.v1"
MEMORY_EDIT_MAX_TITLE_CHARS = 160
MEMORY_EDIT_MAX_BODY_CHARS = 100_000
MEMORY_ADMIN_RECALL_MARKERS = (
    "memory vault",
    "memory system",
    "vault maintenance",
    "memory maintenance",
    "메모리 vault",
    "메모리 보관함",
    "메모리 금고",
    "메모리 시스템",
    "메모리 관리",
    "메모리 정리",
    "메모리 유지보수",
    "보관함 관리",
    "보관함 정리",
)

_memory_delete_lock = threading.RLock()
_memory_delete_tokens: dict[str, dict[str, Any]] = {}
_memory_edit_lock = threading.RLock()
_memory_provenance_backfill_lock = threading.RLock()
_memory_provenance_observability_lock = threading.RLock()
_memory_provenance_backfill_tokens: dict[
    str,
    dict[str, Any],
] = {}


class MemoryNoteDeletedError(RuntimeError):
    pass

BOOTSTRAP_NOTES: tuple[dict[str, Any], ...] = (
    {
        "note_type": "core",
        "title": "Evelyn Memory Source Contract",
        "body": (
            "Evelyn's durable long-term memory source is the Obsidian-compatible "
            "Markdown vault. Runtime JSONL files remain compatibility inputs and "
            "live state stores, but durable memory should converge into Markdown "
            "notes, SQLite metadata, retrieval cache, graph links, and hot prompt "
            "blocks."
        ),
        "tags": ["memory", "vault", "contract"],
        "importance": 0.92,
        "confidence": "high",
        "links": ["memory-vault-architecture"],
    },
    {
        "note_type": "project",
        "title": "Evelyn Project Memory",
        "body": (
            "Evelyn memory should stay editable by the user, fast enough for the "
            "voice path, and explicit about what is durable memory versus live "
            "runtime state. Heavy consolidation must run outside first-audio "
            "delivery."
        ),
        "tags": ["evelyn", "memory", "runtime"],
        "importance": 0.8,
        "confidence": "high",
        "links": ["Evelyn Memory Source Contract"],
    },
    {
        "note_type": "concept",
        "title": "Memory Vault Architecture",
        "body": (
            "The memory vault combines Markdown notes, SQLite metadata, full-text "
            "search, lightweight vector retrieval, graph links, retrieval cache, "
            "and hot prompt blocks. Markdown remains the human-readable source of "
            "truth; generated indexes are rebuildable runtime acceleration."
        ),
        "tags": ["memory", "architecture", "obsidian"],
        "importance": 0.76,
        "confidence": "high",
        "links": ["Evelyn Memory Source Contract"],
    },
    {
        "note_type": "procedure",
        "title": "Memory Vault Maintenance",
        "body": (
            "To activate memory for a guild, bootstrap core notes, mirror legacy "
            "guild facts and summaries, append daily turn notes, consolidate large "
            "daily notes into episodes, rebuild the index, and refresh hot prompt "
            "blocks. This must not block realtime voice response delivery."
        ),
        "tags": ["memory", "maintenance", "procedure"],
        "importance": 0.72,
        "confidence": "high",
        "links": ["Memory Vault Architecture"],
    },
)


@dataclass(slots=True)
class MemoryVaultNote:
    note_id: str
    path: Path
    rel_path: str
    note_type: str
    title: str
    body: str
    tags: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    status: str = "active"
    updated_at: str = ""
    source_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def memory_vault_root(root: Path | None = None) -> Path:
    return (root or MEMORY_ROOT) / VAULT_DIR_NAME


def memory_index_dir(root: Path | None = None) -> Path:
    return (root or MEMORY_ROOT) / INDEX_DIR_NAME


def memory_index_db_path(root: Path | None = None) -> Path:
    return memory_index_dir(root) / INDEX_DB_NAME


def ensure_memory_vault_layout(root: Path | None = None) -> Path:
    vault = memory_vault_root(root)
    for name in ("core", "daily", "episodes", "concepts", "procedures", "projects", "legacy"):
        (vault / name).mkdir(parents=True, exist_ok=True)
    memory_index_dir(root).mkdir(parents=True, exist_ok=True)
    return vault


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _models_url(chat_url: str) -> str:
    match = re.match(r"^(https?://[^/]+)(/.*)?$", clean_text(chat_url))
    if not match:
        return clean_text(chat_url)
    origin = match.group(1)
    path = match.group(2) or ""
    if path.endswith("/v1/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")] + "/models"
    elif not path.endswith("/models"):
        path = "/v1/models"
    return origin + path


def _json_object_from_text(text: str) -> dict[str, Any]:
    cleaned = clean_text(text)
    if not cleaned:
        return {}
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stable_id(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()
    return digest[:16]


def _slug(value: str, default: str = "note") -> str:
    text = clean_text(value).lower()
    text = re.sub(r"[^a-z0-9가-힣._-]+", "-", text)
    text = text.strip("-._")
    return text[:96] or default


def _as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        raw = clean_text(str(value))
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        items = raw.split(",")
    cleaned = []
    for item in items:
        item_text = clean_text(str(item)).strip().strip("'\"")
        if item_text:
            cleaned.append(item_text)
    return tuple(dict.fromkeys(cleaned))


def _extract_note_links(metadata: dict[str, Any], body: str) -> tuple[str, ...]:
    links = list(_as_list(metadata.get("links")))
    links.extend(match.strip() for match in re.findall(r"\[\[([^\]]+)\]\]", body))
    cleaned: list[str] = []
    for link in links:
        value = clean_text(str(link)).strip()
        if not value:
            continue
        if "|" in value:
            value = value.split("|", 1)[0].strip()
        if ":" in value:
            _kind, value = value.split(":", 1)
            value = value.strip()
        if value:
            cleaned.append(value)
    return tuple(dict.fromkeys(cleaned))


def _format_front_matter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, (list, tuple, set)):
            escaped = [str(item).replace('"', '\\"') for item in value if clean_text(str(item))]
            lines.append(f"{key}: [{', '.join(escaped)}]")
        else:
            lines.append(f"{key}: {clean_text(str(value))}")
    lines.append("---")
    return "\n".join(lines)


def _split_front_matter(raw: str) -> tuple[dict[str, str], str]:
    metadata: dict[str, str] = {}
    body = raw
    if not raw.startswith("---"):
        return metadata, body
    lines = raw.splitlines()
    if len(lines) <= 1:
        return metadata, body
    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return metadata, body
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[clean_text(key).strip()] = clean_text(value).strip()
    body = "\n".join(lines[end_index + 1 :]).strip()
    return metadata, body


def parse_memory_note(path: Path, text: str | None = None) -> MemoryVaultNote:
    raw = path.read_text(encoding="utf-8", errors="ignore") if text is None else text
    metadata, body = _split_front_matter(raw)

    title = clean_text(str(metadata.get("title") or ""))
    if not title:
        for line in body.splitlines():
            candidate = clean_text(line).lstrip("#").strip()
            if candidate:
                title = candidate
                break
    if not title:
        title = path.stem.replace("-", " ").replace("_", " ").strip() or "memory note"

    note_type = clean_text(str(metadata.get("type") or path.parent.name or "note")).lower()
    rel_path = path.as_posix()
    note_id = clean_text(str(metadata.get("id") or "")) or _stable_id(rel_path)
    source_hash = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
    return MemoryVaultNote(
        note_id=note_id,
        path=path,
        rel_path=rel_path,
        note_type=note_type,
        title=title,
        body=clean_text(body),
        tags=_as_list(metadata.get("tags")),
        projects=_as_list(metadata.get("projects")),
        links=_extract_note_links(metadata, body),
        status=clean_text(str(metadata.get("status") or "active")).lower(),
        updated_at=clean_text(str(metadata.get("updated_at") or "")),
        source_hash=source_hash,
        metadata=metadata,
    )


def _connect_index(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            note_id TEXT PRIMARY KEY,
            rel_path TEXT NOT NULL UNIQUE,
            note_type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            projects TEXT NOT NULL DEFAULT '[]',
            links TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            importance REAL NOT NULL DEFAULT 0.5,
            confidence TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            source_refs TEXT NOT NULL DEFAULT '[]',
            derived_from TEXT NOT NULL DEFAULT '[]',
            origin_derived_from TEXT NOT NULL DEFAULT '[]',
            evidence_hashes TEXT NOT NULL DEFAULT '[]',
            origin_source TEXT NOT NULL DEFAULT '',
            origin_source_refs TEXT NOT NULL DEFAULT '[]',
            revision INTEGER NOT NULL DEFAULT 0,
            revised_from_evidence_hashes TEXT NOT NULL DEFAULT '[]',
            mtime_ns INTEGER NOT NULL,
            source_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS retrieval_cache (
            cache_key TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            memory_version INTEGER NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS graph_links (
            src_note_id TEXT NOT NULL,
            dst_key TEXT NOT NULL,
            link_type TEXT NOT NULL DEFAULT 'related',
            weight REAL NOT NULL DEFAULT 1.0,
            PRIMARY KEY(src_note_id, dst_key, link_type)
        );
        CREATE TABLE IF NOT EXISTS prompt_block_cache (
            block_key TEXT PRIMARY KEY,
            memory_version INTEGER NOT NULL,
            created_at REAL NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS note_vectors (
            note_id TEXT NOT NULL,
            model TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            norm REAL NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY(note_id, model)
        );
        """
    )
    for statement in (
        "ALTER TABLE notes ADD COLUMN links TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE notes ADD COLUMN importance REAL NOT NULL DEFAULT 0.5",
        "ALTER TABLE notes ADD COLUMN confidence TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE notes ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE notes ADD COLUMN source TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE notes ADD COLUMN source_refs TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE notes ADD COLUMN derived_from TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE notes ADD COLUMN origin_derived_from TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE notes ADD COLUMN evidence_hashes TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE notes ADD COLUMN origin_source TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE notes ADD COLUMN origin_source_refs TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE notes ADD COLUMN revision INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE notes ADD COLUMN revised_from_evidence_hashes TEXT NOT NULL DEFAULT '[]'",
    ):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts
            USING fts5(note_id UNINDEXED, title, body, tags, projects, tokenize='unicode61')
            """
        )
        _set_metadata(conn, "fts_available", "1")
    except sqlite3.OperationalError:
        _set_metadata(conn, "fts_available", "0")
    conn.commit()


def _get_metadata_int(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return int(row["value"])
    except Exception:
        return default


def _set_metadata(conn: sqlite3.Connection, key: str, value: str | int) -> None:
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _front_matter_float(metadata: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(str(metadata.get(key, default)))
    except Exception:
        return default


def _front_matter_int(metadata: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(str(metadata.get(key, default)))
    except Exception:
        return default


def _link_key(value: str) -> str:
    cleaned = clean_text(value).strip()
    if cleaned.endswith(".md"):
        cleaned = cleaned[:-3]
    return _slug(cleaned, default="link")


def _upsert_note_search_rows(conn: sqlite3.Connection, note: MemoryVaultNote) -> None:
    tags_text = " ".join(note.tags)
    projects_text = " ".join(note.projects)
    try:
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note.note_id,))
        conn.execute(
            "INSERT INTO notes_fts(note_id, title, body, tags, projects) VALUES(?, ?, ?, ?, ?)",
            (note.note_id, note.title, note.body, tags_text, projects_text),
        )
    except sqlite3.OperationalError:
        _set_metadata(conn, "fts_available", "0")

    conn.execute("DELETE FROM graph_links WHERE src_note_id = ?", (note.note_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO graph_links(src_note_id, dst_key, link_type, weight) VALUES(?, ?, ?, ?)",
        [(note.note_id, _link_key(link), "related", 1.0) for link in note.links],
    )


def _hashing_vector(text: str, *, dimensions: int = VECTOR_INDEX_DIMENSIONS) -> tuple[dict[str, float], float]:
    values: dict[int, float] = {}
    for token in _tokenize(text):
        if not token:
            continue
        digest = hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        values[bucket] = values.get(bucket, 0.0) + sign
    norm = math.sqrt(sum(value * value for value in values.values()))
    if norm <= 0:
        return {}, 0.0
    normalized = {str(index): value / norm for index, value in values.items() if value}
    return normalized, 1.0


def _vector_text_for_note(note: MemoryVaultNote) -> str:
    return " ".join(
        [
            note.title,
            note.body,
            " ".join(note.tags),
            " ".join(note.projects),
            " ".join(note.links),
        ]
    )


def _upsert_note_vector(conn: sqlite3.Connection, note: MemoryVaultNote) -> None:
    vector, norm = _hashing_vector(_vector_text_for_note(note))
    conn.execute(
        """
        INSERT INTO note_vectors(note_id, model, source_hash, vector_json, norm, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_id, model) DO UPDATE SET
            source_hash = excluded.source_hash,
            vector_json = excluded.vector_json,
            norm = excluded.norm,
            updated_at = excluded.updated_at
        """,
        (
            note.note_id,
            VECTOR_INDEX_MODEL,
            note.source_hash,
            json.dumps(vector, ensure_ascii=False, sort_keys=True),
            norm,
            time.time(),
        ),
    )


def _dot_sparse_vectors(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    total = 0.0
    for key, value in left.items():
        total += value * float(right.get(key, 0.0))
    return total


def sync_memory_vault_index(*, root: Path | None = None, db_path: Path | None = None) -> int:
    vault = ensure_memory_vault_layout(root)
    _reconcile_memory_deletion_tombstones(root=root)
    derivation_state = _reconcile_memory_derivation_revocations(
        root=root
    )
    deleted_note_ids = _memory_deleted_note_ids(root)
    quarantined_note_ids = set(
        derivation_state.get("quarantinedNoteIds") or []
    )
    _invalidate_stale_memory_hot_context(root=root)
    index_path = db_path or memory_index_db_path(root)
    note_paths = sorted(path for path in vault.rglob("*.md") if path.is_file())

    with closing(_connect_index(index_path)) as conn:
        _ensure_schema(conn)
        force_reindex = _get_metadata_int(conn, "schema_version", 0) < 6
        existing = {
            row["rel_path"]: row
            for row in conn.execute("SELECT rel_path, source_hash FROM notes").fetchall()
        }
        seen: set[str] = set()
        changed = False

        for path in note_paths:
            try:
                note = parse_memory_note(path)
            except Exception:
                continue
            if (
                note.note_id in deleted_note_ids
                or note.note_id in quarantined_note_ids
            ):
                continue
            rel_path = path.relative_to(vault).as_posix()
            seen.add(rel_path)
            if not force_reindex and existing.get(rel_path) and existing[rel_path]["source_hash"] == note.source_hash:
                continue
            conn.execute(
                """
                INSERT INTO notes(
                    note_id, rel_path, note_type, title, body, tags, projects, links,
                    status, created_at, updated_at, importance, confidence, source,
                    source_refs, derived_from, origin_derived_from,
                    evidence_hashes, origin_source,
                    origin_source_refs, revision, revised_from_evidence_hashes,
                    mtime_ns, source_hash
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    note_id = excluded.note_id,
                    note_type = excluded.note_type,
                    title = excluded.title,
                    body = excluded.body,
                    tags = excluded.tags,
                    projects = excluded.projects,
                    links = excluded.links,
                    status = excluded.status,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    importance = excluded.importance,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    source_refs = excluded.source_refs,
                    derived_from = excluded.derived_from,
                    origin_derived_from = excluded.origin_derived_from,
                    evidence_hashes = excluded.evidence_hashes,
                    origin_source = excluded.origin_source,
                    origin_source_refs = excluded.origin_source_refs,
                    revision = excluded.revision,
                    revised_from_evidence_hashes = excluded.revised_from_evidence_hashes,
                    mtime_ns = excluded.mtime_ns,
                    source_hash = excluded.source_hash
                """,
                (
                    note.note_id,
                    rel_path,
                    note.note_type,
                    note.title,
                    note.body,
                    json.dumps(note.tags, ensure_ascii=False),
                    json.dumps(note.projects, ensure_ascii=False),
                    json.dumps(note.links, ensure_ascii=False),
                    note.status,
                    clean_text(str(note.metadata.get("created_at") or "")),
                    note.updated_at,
                    _front_matter_float(note.metadata, "importance", 0.5),
                    clean_text(str(note.metadata.get("confidence") or "")),
                    clean_text(str(note.metadata.get("source") or "")),
                    json.dumps(_as_list(note.metadata.get("source_refs") or note.metadata.get("source_ref")), ensure_ascii=False),
                    json.dumps(_as_list(note.metadata.get("derived_from")), ensure_ascii=False),
                    json.dumps(_as_list(note.metadata.get("origin_derived_from")), ensure_ascii=False),
                    json.dumps(_as_list(note.metadata.get("evidence_hashes") or note.metadata.get("source_hash")), ensure_ascii=False),
                    clean_text(str(note.metadata.get("origin_source") or "")),
                    json.dumps(_as_list(note.metadata.get("origin_source_refs")), ensure_ascii=False),
                    max(0, _front_matter_int(note.metadata, "revision", 0)),
                    json.dumps(_as_list(note.metadata.get("revised_from_evidence_hashes")), ensure_ascii=False),
                    path.stat().st_mtime_ns,
                    note.source_hash,
                ),
            )
            _upsert_note_search_rows(conn, note)
            _upsert_note_vector(conn, note)
            changed = True

        stale_paths = sorted(set(existing) - seen)
        if stale_paths:
            placeholders = ",".join("?" for _ in stale_paths)
            stale_ids = [
                row["note_id"]
                for row in conn.execute(
                    f"SELECT note_id FROM notes WHERE rel_path IN ({placeholders})",
                    stale_paths,
                ).fetchall()
            ]
            conn.executemany("DELETE FROM notes WHERE rel_path = ?", [(item,) for item in stale_paths])
            conn.executemany("DELETE FROM graph_links WHERE src_note_id = ?", [(item,) for item in stale_ids])
            conn.executemany("DELETE FROM note_vectors WHERE note_id = ?", [(item,) for item in stale_ids])
            try:
                conn.executemany("DELETE FROM notes_fts WHERE note_id = ?", [(item,) for item in stale_ids])
            except sqlite3.OperationalError:
                pass
            changed = True

        version = _get_metadata_int(conn, "memory_version", 0)
        if changed:
            version += 1
            _set_metadata(conn, "memory_version", version)
            _set_metadata(conn, "last_indexed_at", _utc_now_iso())
            _set_metadata(conn, "schema_version", 6)
            _set_metadata(conn, "vector_model", VECTOR_INDEX_MODEL)
            _set_metadata(conn, "vector_dimensions", VECTOR_INDEX_DIMENSIONS)
            conn.execute("DELETE FROM retrieval_cache")
        conn.commit()
        return version


def _safe_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [clean_text(str(item)) for item in parsed if clean_text(str(item))]


def _memory_source_type(source: str, note_type: str) -> str:
    normalized = clean_text(source).lower()
    if normalized == BOOTSTRAP_NOTE_SOURCE:
        return "system"
    if "legacy" in normalized:
        return "legacy"
    if (
        "consolidation" in normalized
        or "recomposition" in normalized
    ):
        return "derived"
    if normalized in {"conversation-turn-log", "daily-turn-log"} or note_type == "daily":
        return "conversation"
    if normalized in {"user", "user-edit", "control-page-user"}:
        return "user"
    return "unknown" if not normalized else "runtime"


def _public_memory_source_ref(value: str) -> str:
    cleaned = clean_text(value).strip()
    if not cleaned:
        return ""
    if re.match(r"^(?:[a-zA-Z]:[\\/]|/)", cleaned):
        leaf = re.split(r"[\\/]+", cleaned.rstrip("/\\"))[-1]
        return f"local:{leaf or _stable_id(cleaned)}"
    return cleaned[:180]


def _memory_note_provenance(
    note: MemoryVaultNote,
    *,
    rel_path: str,
    note_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = note_state or {}
    source = clean_text(str(note.metadata.get("source") or ""))
    source_refs = [
        item
        for item in (
            _public_memory_source_ref(value)
            for value in _as_list(
                note.metadata.get("source_refs") or note.metadata.get("source_ref")
            )
        )
        if item
    ]
    derived_from = list(_as_list(note.metadata.get("derived_from")))
    origin_derived_from = list(
        _as_list(note.metadata.get("origin_derived_from"))
    )
    evidence_hashes = list(
        _as_list(
            note.metadata.get("evidence_hashes")
            or note.metadata.get("source_hash")
        )
    )
    origin_source = clean_text(
        str(note.metadata.get("origin_source") or "")
    )
    origin_source_refs = [
        item
        for item in (
            _public_memory_source_ref(value)
            for value in _as_list(
                note.metadata.get("origin_source_refs")
            )
        )
        if item
    ]
    revised_from_evidence_hashes = list(
        _as_list(
            note.metadata.get("revised_from_evidence_hashes")
        )
    )
    if not source_refs and note.note_type == "daily":
        source_refs = [rel_path]
    if not derived_from and "consolidation" in source.lower():
        derived_from = [
            value
            for value in note.links
            if clean_text(value).lower().startswith("daily/")
        ]
    confirmed_at = clean_text(
        str(state.get("confirmed_at") or note.metadata.get("confirmed_at") or "")
    )
    return {
        "schema": MEMORY_PROVENANCE_SCHEMA,
        "noteId": note.note_id,
        "source": source or "unknown",
        "sourceType": _memory_source_type(source, note.note_type),
        "sourceRefs": list(dict.fromkeys(source_refs))[:12],
        "derivedFrom": list(dict.fromkeys(derived_from))[:12],
        "originDerivedFrom": list(
            dict.fromkeys(origin_derived_from)
        )[:12],
        "evidenceHashes": list(dict.fromkeys(evidence_hashes))[:12],
        "originSource": origin_source,
        "originSourceRefs": list(
            dict.fromkeys(origin_source_refs)
        )[:12],
        "revision": max(
            0,
            _front_matter_int(note.metadata, "revision", 0),
        ),
        "revisedFromEvidenceHashes": list(
            dict.fromkeys(revised_from_evidence_hashes)
        )[:12],
        "createdAt": clean_text(str(note.metadata.get("created_at") or "")),
        "updatedAt": clean_text(
            str(note.metadata.get("updated_at") or note.updated_at or "")
        ),
        "contentHash": note.source_hash,
        "confidence": clean_text(str(note.metadata.get("confidence") or "")),
        "userConfirmed": bool(confirmed_at),
        "confirmedAt": confirmed_at,
        "userEditedAt": clean_text(str(state.get("edited_at") or "")),
    }


def _memory_row_provenance(row: sqlite3.Row) -> dict[str, Any]:
    source = clean_text(
        str(row["source"] if "source" in row.keys() else "")
    )
    note_type = clean_text(str(row["note_type"]))
    try:
        revision = max(
            0,
            int(
                row["revision"]
                if "revision" in row.keys()
                else 0
            ),
        )
    except (TypeError, ValueError):
        revision = 0
    return {
        "schema": MEMORY_PROVENANCE_SCHEMA,
        "noteId": clean_text(str(row["note_id"])),
        "source": source or "unknown",
        "sourceType": _memory_source_type(source, note_type),
        "sourceRefs": [
            item
            for item in (
                _public_memory_source_ref(value)
                for value in _safe_json_list(
                    str(row["source_refs"] if "source_refs" in row.keys() else "[]")
                )
            )
            if item
        ],
        "derivedFrom": _safe_json_list(
            str(row["derived_from"] if "derived_from" in row.keys() else "[]")
        ),
        "originDerivedFrom": _safe_json_list(
            str(
                row["origin_derived_from"]
                if "origin_derived_from" in row.keys()
                else "[]"
            )
        ),
        "evidenceHashes": _safe_json_list(
            str(
                row["evidence_hashes"]
                if "evidence_hashes" in row.keys()
                else "[]"
            )
        ),
        "originSource": clean_text(
            str(
                row["origin_source"]
                if "origin_source" in row.keys()
                else ""
            )
        ),
        "originSourceRefs": _safe_json_list(
            str(
                row["origin_source_refs"]
                if "origin_source_refs" in row.keys()
                else "[]"
            )
        ),
        "revision": revision,
        "revisedFromEvidenceHashes": _safe_json_list(
            str(
                row["revised_from_evidence_hashes"]
                if "revised_from_evidence_hashes" in row.keys()
                else "[]"
            )
        ),
        "createdAt": clean_text(
            str(row["created_at"] if "created_at" in row.keys() else "")
        ),
        "updatedAt": clean_text(str(row["updated_at"])),
        "contentHash": clean_text(str(row["source_hash"])),
        "confidence": clean_text(str(row["confidence"])),
    }


def _memory_provenance_context_line(provenance: dict[str, Any]) -> str:
    evidence = list(provenance.get("evidenceHashes") or [])
    evidence_label = clean_text(str(evidence[0]))[:16] if evidence else "-"
    refs = list(provenance.get("sourceRefs") or [])
    ref_label = clean_text(str(refs[0])) if refs else "-"
    origin = clean_text(str(provenance.get("originSource") or ""))
    revision = max(0, int(provenance.get("revision") or 0))
    revision_label = (
        f"; revision={revision}; origin={origin or '-'}"
        if revision
        else ""
    )
    return (
        f"- {clean_text(str(provenance.get('noteId') or 'unknown'))}: "
        f"source={clean_text(str(provenance.get('source') or 'unknown'))}; "
        f"ref={ref_label}; evidence={evidence_label}; "
        f"confidence={clean_text(str(provenance.get('confidence') or 'unknown'))}"
        f"{revision_label}"
    )


def _note_row_timestamp(row: sqlite3.Row) -> float:
    raw = clean_text(str(row["updated_at"] if "updated_at" in row.keys() else ""))
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _graph_node_size(row: sqlite3.Row, degree: int = 0) -> float:
    try:
        importance = float(row["importance"])
    except Exception:
        importance = 0.5
    type_bonus = {
        "core": 8.0,
        "project": 5.0,
        "procedure": 3.0,
        "concept": 2.0,
        "episode": 1.5,
        "daily": 0.0,
    }.get(clean_text(str(row["note_type"])).lower(), 0.0)
    return round(12.0 + min(12.0, importance * 10.0) + min(14.0, degree * 1.4) + type_bonus, 2)


def _is_legacy_memory_note_type(note_type: str, rel_path: str = "") -> bool:
    normalized_type = clean_text(note_type).lower()
    normalized_rel = clean_text(rel_path).lower()
    return (
        normalized_type == "legacy"
        or normalized_rel.startswith("legacy/")
        or normalized_rel.startswith("core/legacy-guild-")
    )


def _legacy_to_public_title(note_type: str, rel_path: str, title: str) -> str:
    if _is_legacy_memory_note_type(note_type, rel_path):
        return "Archived memory"
    return clean_text(title)


def _locked_memory_preview(note_type: str, rel_path: str = "") -> str:
    if _is_legacy_memory_note_type(note_type, rel_path):
        return "Archived memory is visible as a locked node. Its contents are hidden in public views."
    return ""


def _graph_edge_key(source: str, target: str, edge_type: str) -> tuple[str, str, str]:
    left = clean_text(source)
    right = clean_text(target)
    if left > right and edge_type in {"shared_tag", "shared_project", "semantic_similarity", "recall_cohit"}:
        left, right = right, left
    return left, right, edge_type


def _add_graph_edge(
    edges_by_key: dict[tuple[str, str, str], dict[str, Any]],
    source: str,
    target: str,
    edge_type: str,
    *,
    weight: float = 1.0,
    label: str = "",
) -> None:
    source = clean_text(source)
    target = clean_text(target)
    if not source or not target or source == target:
        return
    key = _graph_edge_key(source, target, edge_type)
    current = edges_by_key.get(key)
    if current is None:
        edges_by_key[key] = {
            "source": key[0],
            "target": key[1],
            "type": edge_type,
            "weight": round(max(0.05, float(weight)), 4),
            "label": clean_text(label or edge_type),
        }
        return
    current["weight"] = round(max(float(current.get("weight", 0.0)), float(weight)), 4)
    if label and not current.get("label"):
        current["label"] = clean_text(label)


def _load_note_vector_map(conn: sqlite3.Connection, note_ids: set[str]) -> dict[str, dict[str, float]]:
    if not note_ids:
        return {}
    placeholders = ",".join("?" for _ in note_ids)
    rows = conn.execute(
        f"""
        SELECT note_id, vector_json
        FROM note_vectors
        WHERE model = ? AND note_id IN ({placeholders})
        """,
        [VECTOR_INDEX_MODEL, *sorted(note_ids)],
    ).fetchall()
    vectors: dict[str, dict[str, float]] = {}
    for row in rows:
        try:
            parsed = json.loads(clean_text(str(row["vector_json"])))
        except Exception:
            continue
        if isinstance(parsed, dict):
            vectors[clean_text(str(row["note_id"]))] = {
                clean_text(str(key)): float(value)
                for key, value in parsed.items()
                if isinstance(value, (int, float))
            }
    return vectors


def export_memory_graph(
    *,
    root: Path | None = None,
    max_nodes: int = MEMORY_GRAPH_MAX_NODES,
    include_internal: bool = False,
) -> dict[str, Any]:
    """Export a rebuildable graph JSON view for the control page."""
    started = time.monotonic()
    version = sync_memory_vault_index(root=root)
    max_nodes = max(20, min(500, int(max_nodes or MEMORY_GRAPH_MAX_NODES)))
    hidden_types = sorted(MEMORY_GRAPH_INTERNAL_NOTE_TYPES) if not include_internal else []
    hidden_filter = ""
    hidden_params: list[str] = []
    if hidden_types:
        hidden_filter = f"AND lower(note_type) NOT IN ({','.join('?' for _ in hidden_types)})"
        hidden_params = hidden_types

    with closing(_connect_index(memory_index_db_path(root))) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            f"""
            SELECT *
            FROM notes
            WHERE status NOT IN ('archived', 'superseded')
              {hidden_filter}
            ORDER BY
                CASE note_type
                    WHEN 'core' THEN 0
                    WHEN 'project' THEN 1
                    WHEN 'concept' THEN 3
                    WHEN 'episode' THEN 4
                    WHEN 'daily' THEN 5
                    ELSE 6
                END,
                importance DESC,
                updated_at DESC,
                title ASC
            LIMIT ?
            """,
            (*hidden_params, max_nodes),
        ).fetchall()
        row_by_id = {clean_text(str(row["note_id"])): row for row in rows}
        if not row_by_id:
            return {
                "ok": True,
                "memory_version": version,
                "generated_at": time.time(),
                "vault_path": str(memory_vault_root(root)),
                "index_path": str(memory_index_db_path(root)),
                "nodes": [],
                "edges": [],
                "stats": {
                    "node_count": 0,
                    "edge_count": 0,
                    "type_counts": {},
                    "include_internal": bool(include_internal),
                    "hidden_types": hidden_types,
                },
                "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
            }

        resolver: dict[str, str] = {}
        for row in rows:
            note_id = clean_text(str(row["note_id"]))
            rel_path = clean_text(str(row["rel_path"]))
            title = clean_text(str(row["title"]))
            for key in {
                note_id,
                _link_key(note_id),
                _link_key(title),
                _link_key(Path(rel_path).stem),
                _link_key(rel_path.removesuffix(".md")),
            }:
                if key:
                    resolver[key] = note_id

        edges_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        note_ids = set(row_by_id)
        explicit_rows = conn.execute(
            f"""
            SELECT src_note_id, dst_key, link_type, weight
            FROM graph_links
            WHERE src_note_id IN ({','.join('?' for _ in note_ids)})
            """,
            sorted(note_ids),
        ).fetchall()
        for row in explicit_rows:
            source = clean_text(str(row["src_note_id"]))
            target = resolver.get(clean_text(str(row["dst_key"])))
            if target:
                _add_graph_edge(
                    edges_by_key,
                    source,
                    target,
                    clean_text(str(row["link_type"] or "related")),
                    weight=float(row["weight"] or 1.0) * 2.0,
                    label="link",
                )

        def add_group_edges(field: str, edge_type: str, cap: int) -> None:
            groups: dict[str, list[sqlite3.Row]] = {}
            for note_row in rows:
                for item in _safe_json_list(clean_text(str(note_row[field]))):
                    key = clean_text(item).lower()
                    if key:
                        groups.setdefault(key, []).append(note_row)
            added = 0
            for key, group_rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
                ranked = sorted(
                    group_rows,
                    key=lambda item: (float(item["importance"] or 0.0), _note_row_timestamp(item)),
                    reverse=True,
                )[:8]
                for index, left in enumerate(ranked):
                    for right in ranked[index + 1 :]:
                        _add_graph_edge(
                            edges_by_key,
                            clean_text(str(left["note_id"])),
                            clean_text(str(right["note_id"])),
                            edge_type,
                            weight=0.42 if edge_type == "shared_tag" else 0.34,
                            label=key,
                        )
                        added += 1
                        if added >= cap:
                            return

        add_group_edges("tags", "shared_tag", MEMORY_GRAPH_MAX_GROUP_EDGES)
        add_group_edges("projects", "shared_project", MEMORY_GRAPH_MAX_GROUP_EDGES // 2)

        cache_rows = conn.execute(
            """
            SELECT payload
            FROM retrieval_cache
            ORDER BY created_at DESC
            LIMIT 48
            """
        ).fetchall()
        for cache_row in cache_rows:
            try:
                payload = json.loads(clean_text(str(cache_row["payload"])))
            except Exception:
                continue
            sources = payload.get("sources") if isinstance(payload, dict) else None
            if not isinstance(sources, list) or len(sources) < 2:
                continue
            linked_ids = []
            for source in sources[:8]:
                rel_key = clean_text(str(source))
                row = next((candidate for candidate in rows if clean_text(str(candidate["rel_path"])) == rel_key), None)
                if row is not None:
                    linked_ids.append(clean_text(str(row["note_id"])))
            for index, left in enumerate(linked_ids):
                for right in linked_ids[index + 1 :]:
                    _add_graph_edge(edges_by_key, left, right, "recall_cohit", weight=0.25, label="recall")

        vectors = _load_note_vector_map(conn, note_ids)
        vector_pairs: list[tuple[float, str, str]] = []
        vector_ids = sorted(vectors)
        for index, left_id in enumerate(vector_ids):
            for right_id in vector_ids[index + 1 :]:
                score = _dot_sparse_vectors(vectors[left_id], vectors[right_id])
                if score >= 0.18:
                    vector_pairs.append((score, left_id, right_id))
        for score, left_id, right_id in sorted(vector_pairs, reverse=True)[:MEMORY_GRAPH_MAX_VECTOR_EDGES]:
            _add_graph_edge(
                edges_by_key,
                left_id,
                right_id,
                "semantic_similarity",
                weight=0.18 + score,
                label="semantic",
            )

        degrees: dict[str, int] = {note_id: 0 for note_id in note_ids}
        for edge in edges_by_key.values():
            degrees[clean_text(str(edge["source"]))] = degrees.get(clean_text(str(edge["source"])), 0) + 1
            degrees[clean_text(str(edge["target"]))] = degrees.get(clean_text(str(edge["target"])), 0) + 1

        nodes = []
        type_counts: dict[str, int] = {}
        for row in rows:
            note_id = clean_text(str(row["note_id"]))
            note_type = clean_text(str(row["note_type"] or "note")).lower()
            rel_path = clean_text(str(row["rel_path"]))
            is_locked_legacy = _is_legacy_memory_note_type(note_type, rel_path)
            type_counts[note_type] = type_counts.get(note_type, 0) + 1
            nodes.append(
                {
                    "id": note_id,
                    "title": _legacy_to_public_title(note_type, rel_path, str(row["title"])),
                    "type": note_type,
                    "rel_path": rel_path,
                    "tags": _safe_json_list(clean_text(str(row["tags"]))),
                    "projects": _safe_json_list(clean_text(str(row["projects"]))),
                    "links": _safe_json_list(clean_text(str(row["links"]))),
                    "status": clean_text(str(row["status"])),
                    "updated_at": clean_text(str(row["updated_at"])),
                    "importance": float(row["importance"] or 0.0),
                    "confidence": clean_text(str(row["confidence"])),
                    "degree": degrees.get(note_id, 0),
                    "size": _graph_node_size(row, degrees.get(note_id, 0)),
                    "snippet": "" if is_locked_legacy else clean_text(str(row["body"]))[:260],
                    "locked": is_locked_legacy,
                    "canEdit": not is_locked_legacy,
                    "contentHidden": is_locked_legacy,
                }
            )

        edges = sorted(
            edges_by_key.values(),
            key=lambda item: (clean_text(str(item.get("type"))), clean_text(str(item.get("source"))), clean_text(str(item.get("target")))),
        )
        stats = {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "type_counts": type_counts,
            "memory_version": version,
            "vector_model": VECTOR_INDEX_MODEL,
            "include_internal": bool(include_internal),
            "hidden_types": hidden_types,
        }
        return {
            "ok": True,
            "memory_version": version,
            "generated_at": time.time(),
            "vault_path": str(memory_vault_root(root)),
            "index_path": str(memory_index_db_path(root)),
            "nodes": nodes,
            "edges": edges,
            "stats": stats,
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        }
    return [clean_text(str(item)) for item in parsed if clean_text(str(item))]


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", clean_text(text).lower()))


def _allows_internal_memory_recall(request: MemoryRecallRequest, focus_items: list[Any] | None = None) -> bool:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    if metadata.get("allow_internal_memory") is True:
        return True
    haystack = clean_text(
        " ".join(
            [
                request.user_text,
                request.topic_id or "",
                request.source or "",
                " ".join(clean_text(str(item)) for item in (focus_items or [])),
            ]
        )
    ).lower()
    return any(marker in haystack for marker in MEMORY_ADMIN_RECALL_MARKERS)


def _is_internal_memory_note(row: sqlite3.Row) -> bool:
    return clean_text(str(row["note_type"])).lower() in MEMORY_INTERNAL_NOTE_TYPES


def _is_internal_memory_note_type(note_type: str) -> bool:
    return clean_text(note_type).lower() in MEMORY_INTERNAL_NOTE_TYPES


def _note_score(row: sqlite3.Row, query_tokens: set[str], focus_tokens: set[str], active_project: str) -> int:
    title = clean_text(str(row["title"]))
    body = clean_text(str(row["body"]))
    tags = _safe_json_list(str(row["tags"]))
    projects = _safe_json_list(str(row["projects"]))
    haystack = " ".join([title, body, " ".join(tags), " ".join(projects)])
    tokens = _tokenize(haystack)
    score = len(query_tokens & tokens) * 8
    score += len(focus_tokens & tokens) * 4
    if active_project and active_project in {item.lower() for item in projects}:
        score += 6
    if row["note_type"] in {"core", "procedure", "project"}:
        score += 2
    try:
        score += int(float(row["importance"]) * 4)
    except Exception:
        pass
    return score


def _fts_query(tokens: set[str]) -> str:
    terms = []
    for token in sorted(tokens, key=len, reverse=True):
        cleaned = re.sub(r"[^A-Za-z0-9_가-힣]+", "", token)
        if len(cleaned) >= 2:
            terms.append(f'"{cleaned}"')
        if len(terms) >= 12:
            break
    return " OR ".join(terms)


def _fetch_candidate_rows(
    conn: sqlite3.Connection,
    *,
    query_tokens: set[str],
    focus_tokens: set[str],
    limit: int,
) -> tuple[list[sqlite3.Row], str]:
    fts = _fts_query(query_tokens | focus_tokens)
    if fts:
        try:
            rows = conn.execute(
                """
                SELECT n.*, bm25(notes_fts) AS fts_rank
                FROM notes_fts
                JOIN notes n ON n.note_id = notes_fts.note_id
                WHERE notes_fts MATCH ? AND n.status NOT IN ('archived', 'superseded')
                ORDER BY fts_rank
                LIMIT ?
                """,
                (fts, max(limit, 80)),
            ).fetchall()
            if rows:
                return rows, "fts"
        except sqlite3.OperationalError:
            _set_metadata(conn, "fts_available", "0")

    rows = conn.execute(
        "SELECT *, 0.0 AS fts_rank FROM notes WHERE status NOT IN ('archived', 'superseded') ORDER BY mtime_ns DESC LIMIT ?",
        (max(limit, 500),),
    ).fetchall()
    return rows, "scan"


def _fetch_vector_scores(
    conn: sqlite3.Connection,
    query_text: str,
    *,
    limit: int,
) -> dict[str, float]:
    query_vector, query_norm = _hashing_vector(query_text)
    if not query_vector or query_norm <= 0:
        return {}
    rows = conn.execute(
        """
        SELECT v.note_id, v.vector_json
        FROM note_vectors v
        JOIN notes n ON n.note_id = v.note_id
        WHERE v.model = ? AND n.status NOT IN ('archived', 'superseded')
        """,
        (VECTOR_INDEX_MODEL,),
    ).fetchall()
    scored: list[tuple[float, str]] = []
    for row in rows:
        try:
            vector = json.loads(str(row["vector_json"]))
        except Exception:
            continue
        if not isinstance(vector, dict):
            continue
        score = _dot_sparse_vectors(query_vector, {str(k): float(v) for k, v in vector.items()})
        if score > 0:
            scored.append((score, clean_text(str(row["note_id"]))))
    scored.sort(reverse=True)
    return {note_id: score for score, note_id in scored[: max(1, limit)]}


def _fetch_notes_by_ids(conn: sqlite3.Connection, note_ids: list[str]) -> list[sqlite3.Row]:
    cleaned_ids = [clean_text(note_id) for note_id in note_ids if clean_text(note_id)]
    if not cleaned_ids:
        return []
    placeholders = ",".join("?" for _ in cleaned_ids)
    return conn.execute(
        f"SELECT *, 0.0 AS fts_rank FROM notes WHERE note_id IN ({placeholders}) AND status NOT IN ('archived', 'superseded')",
        cleaned_ids,
    ).fetchall()


def _expand_graph_neighbors(
    conn: sqlite3.Connection,
    selected: list[sqlite3.Row],
    *,
    max_extra: int,
) -> list[sqlite3.Row]:
    if max_extra <= 0 or not selected:
        return []
    selected_ids = {clean_text(str(row["note_id"])) for row in selected}
    dst_keys = [
        row["dst_key"]
        for row in conn.execute(
            f"SELECT dst_key FROM graph_links WHERE src_note_id IN ({','.join('?' for _ in selected_ids)})",
            tuple(selected_ids),
        ).fetchall()
    ]
    if not dst_keys:
        return []

    rows = conn.execute("SELECT *, 0.0 AS fts_rank FROM notes WHERE status NOT IN ('archived', 'superseded')").fetchall()
    extras: list[sqlite3.Row] = []
    wanted = set(dst_keys)
    for row in rows:
        if row["note_id"] in selected_ids:
            continue
        candidates = {
            _link_key(str(row["note_id"])),
            _link_key(str(row["rel_path"])),
            _link_key(str(row["title"])),
        }
        if wanted & candidates:
            extras.append(row)
        if len(extras) >= max_extra:
            break
    return extras


def _truncate_note(row: sqlite3.Row, max_chars: int = 420) -> str:
    title = clean_text(str(row["title"]))
    note_type = clean_text(str(row["note_type"]))
    body = clean_text(str(row["body"]))
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) > max_chars:
        body = body[: max(0, max_chars - 3)].rstrip() + "..."
    label = f"{title} ({note_type})" if note_type else title
    return f"- {label}: {body}" if body else f"- {label}"


def _cache_key(request: MemoryRecallRequest, memory_version: int) -> str:
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    payload = {
        "version": memory_version,
        "guild_id": request.guild_id,
        "session_key": request.session_key,
        "user_text": clean_text(request.user_text).lower(),
        "topic_id": request.topic_id,
        "source": request.source,
        "max_items": request.max_items,
        "active_project": clean_text(str(metadata.get("active_project") or DEFAULT_PROJECT)).lower(),
        "context_focus": metadata.get("context_focus") if isinstance(metadata.get("context_focus"), list) else [],
        "allow_internal_memory": _allows_internal_memory_recall(
            request,
            metadata.get("context_focus") if isinstance(metadata.get("context_focus"), list) else [],
        ),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _read_retrieval_cache(conn: sqlite3.Connection, key: str, memory_version: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT created_at, memory_version, payload FROM retrieval_cache WHERE cache_key = ?", (key,)).fetchone()
    if row is None:
        return None
    if int(row["memory_version"]) != memory_version:
        return None
    if time.time() - float(row["created_at"]) > RETRIEVAL_CACHE_TTL_SECONDS:
        return None
    try:
        payload = json.loads(str(row["payload"]))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_retrieval_cache(conn: sqlite3.Connection, key: str, memory_version: int, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO retrieval_cache(cache_key, created_at, memory_version, payload) VALUES(?, ?, ?, ?)",
        (key, time.time(), memory_version, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()


def _sanitize_legacy_display_text(text: str) -> str:
    cleaned = clean_text(text)
    replacements = {
        "사용자(캣초코 크런치)": "사용자(정훈)",
        "사용자(이름만 부름)": "사용자가 이름만 부를 때",
        "캣초코 크런치": "정훈",
        "시스템(에밀리)은": "이블린은",
        "시스템(이블린)은": "이블린은",
        "시스템(에밀리)": "이블린",
        "시스템(이블린)": "이블린",
        "에밀리(레몬에이드)": "이블린(레몬에이드)",
        "에밀리": "이블린",
        "에블린": "이블린",
    }
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"시스템은 ['\"]이블린['\"]라는 이름으로 파일 정리 중이며", "이블린은 파일 정리 중이며", cleaned)
    cleaned = re.sub(
        r"시스템은 ['\"]이블린['\"]라는 이름으로 사용자에게 도움을 제공하는 역할을 수행",
        "이블린은 사용자에게 도움을 제공하는 역할을 수행",
        cleaned,
    )
    cleaned = cleaned.replace("사용자(정훈)는", "정훈은")
    cleaned = cleaned.replace("사용자(정훈)가", "정훈이")
    cleaned = cleaned.replace("사용자(정훈)의", "정훈의")
    cleaned = cleaned.replace("사용자(정훈)을", "정훈을")
    cleaned = cleaned.replace("사용자(정훈)를", "정훈을")
    cleaned = cleaned.replace("사용자(정훈)", "정훈")
    cleaned = cleaned.replace("사용자의 이름은 정훈입니다.", "사용자는 정훈입니다.")
    cleaned = cleaned.replace("이블린은 사용자에게 도움을 제공하는 역할을 수행 중임.", "이블린은 정훈을 돕는 역할을 수행 중입니다.")
    return cleaned


def _legacy_display_group(kind: str, text: str) -> str:
    label = clean_text(kind)
    combined = f"{label} {text}"
    if any(token in combined for token in ("이름", "페르소나", "역할")):
        return "기본 정보"
    if any(token in combined for token in ("확인", "질문", "필요", "open")):
        return "정리 필요"
    if any(token in combined for token in ("진행", "업무", "계획", "상태", "방문", "선택")):
        return "현재 맥락"
    if any(token in combined for token in ("선호", "취미", "좋아", "음료", "과자")):
        return "취향"
    return "기타 기억"


def _legacy_display_key(text: str) -> str:
    normalized = _sanitize_legacy_display_text(text).lower()
    if "이블린" in normalized and ("도움" in normalized or "돕" in normalized):
        return "topic:assistant-role"
    if "정훈" in normalized and ("이름" in normalized or "사용자는" in normalized):
        return "topic:user-name"
    if "굼벵이" in normalized or ("농담" in normalized and ("표현" in normalized or "욕" in normalized)):
        return "topic:joke-expression"
    if "욕" in normalized and ("스타일" in normalized or "강도" in normalized):
        return "topic:joke-style-question"
    if "추가" in normalized and "요청" in normalized:
        return "topic:followup-request"
    topic_rules = (
        ("user-name", ("정훈", "이름")),
        ("persona-short-call", ("친구처럼", "반말")),
        ("assistant-role", ("이블린", "도움")),
        ("baguette", ("바게트",)),
        ("caramel-macchiato", ("카라멜", "마끼아또")),
        ("lemonade", ("레몬에이드",)),
        ("chocolate-wafer", ("초코", "웨이퍼")),
        ("chocolate-snack", ("초콜릿",)),
        ("new-cafe-question", ("새로", "오픈", "카페")),
        ("cafe-visit", ("카페", "방문")),
        ("cafe-drinks", ("음료",)),
        ("stranger-book", ("이방인",)),
        ("cafe-time-question", ("구체", "시간")),
        ("snack-question", ("과자", "확인")),
        ("work-status", ("업무",)),
    )
    for topic_key, tokens in topic_rules:
        if all(token in normalized for token in tokens):
            return f"topic:{topic_key}"
    normalized = re.sub(r"사용자\((정훈)\)", r"\1", normalized)
    normalized = normalized.replace("사용자의", "사용자")
    normalized = normalized.replace("좋아함", "선호")
    normalized = normalized.replace("좋아하며", "선호")
    normalized = normalized.replace("좋아한다", "선호")
    normalized = normalized.replace("선호합니다", "선호")
    normalized = normalized.replace("선호함", "선호")
    normalized = normalized.replace("확정되었습니다", "확정")
    normalized = normalized.replace("확정되었으며", "확정")
    normalized = normalized.replace("확정되었다", "확정")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"[.!?。！？,，~…\"'“”‘’`()\[\]{}:;_-]+", "", normalized)
    return normalized


def _append_legacy_unique(target: list[str], seen: set[str], text: str, *, max_items: int) -> None:
    cleaned = _sanitize_legacy_display_text(text)
    if not cleaned:
        return
    if any(
        noise in cleaned
        for noise in (
            "미르스페벨",
            "나킨",
            "쏟아냈고",
            "과자 취향은 무엇인지 확인",
            "우선순위가 높은 업무",
            "특별히 지정된 업무 없이",
            "현재 요청받은 일(",
            "추가적인 요청 사항이나 변경 사항",
            "추가적인 도움 요청 사항",
            "어떤 구체적인 도움",
            "사용자가 어떤 도움",
            "원하시는 욕의 스타일",
            "돕는 것을 좋아한다",
        )
    ):
        return
    if len(cleaned) > 260:
        cleaned = cleaned[:257].rstrip() + "..."
    key = _legacy_display_key(cleaned)
    if not key or key in seen:
        return
    if any((len(key) > 16 and key in existing) or (len(existing) > 16 and existing in key) for existing in seen):
        return
    seen.add(key)
    if len(target) < max_items:
        target.append(cleaned)


def _format_memory_callout(kind: str, title: str, items: list[str], *, empty_text: str | None = None) -> list[str]:
    lines = [f"> [!{kind}] {title}"]
    if items:
        lines.extend(f"> - {item}" for item in items)
    elif empty_text:
        lines.append(f"> {empty_text}")
    lines.append("")
    return lines


def _daily_intro_block(
    day_key: str,
    *,
    note_id: str,
    guild_id: int,
    scope_type: str,
    scope_key: str | None,
    scope_labels: list[str] | tuple[str, ...] | None,
) -> str:
    now = _utc_now_iso()
    scope_ref = f"{clean_text(scope_type).lower() or 'guild'}:{clean_text(scope_key or str(guild_id))}"
    return "\n".join(
        [
            _format_front_matter(
                {
                    "id": note_id,
                    "type": "daily",
                    "title": f"이블린 일일 메모 {day_key}",
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                    "source": "conversation-turn-log",
                    "source_refs": [scope_ref],
                    "tags": ["daily", "conversation"],
                    "projects": [DEFAULT_PROJECT],
                    "confidence": "high",
                }
            ),
            "",
            f"# 이블린 일일 메모 {day_key}",
            "",
            "> [!summary] 오늘 보기",
            "> 중요한 내용은 짧게 정리하고, 원문 대화는 아래 기록에 모읍니다.",
            "",
            "> [!example]- 대화 원문 보기",
            "",
        ]
    )


def refresh_legacy_memory_mirror(guild_id: int, *, root: Path | None = None, max_items: int = 12) -> Path | None:
    base_root = root or MEMORY_ROOT
    guild_dir = base_root / f"guild_{guild_id}"
    if not guild_dir.exists():
        return None

    vault = ensure_memory_vault_layout(root)
    target = vault / "core" / f"legacy-guild-{guild_id}.md"
    scope_dirs = [guild_dir]
    for pattern in ("room_*", "person_*", "session_*"):
        scope_dirs.extend(sorted(guild_dir.glob(pattern))[:20])

    summaries: list[str] = []
    summary_seen: set[str] = set()
    grouped: dict[str, list[str]] = {
        "기본 정보": [],
        "취향": [],
        "현재 맥락": [],
        "정리 필요": [],
        "기타 기억": [],
    }
    group_seen: dict[str, set[str]] = {name: set() for name in grouped}

    for scope_dir in scope_dirs:
        summary = (scope_dir / "rolling_summary.txt").read_text(encoding="utf-8", errors="ignore").strip() if (scope_dir / "rolling_summary.txt").exists() else ""
        if summary:
            _append_legacy_unique(summaries, summary_seen, summary, max_items=2)
        rows: list[dict[str, Any]] = []
        for rel in ("durable_facts.jsonl", "vault/facts.jsonl", "open_questions.jsonl", "vault/questions.jsonl"):
            path = scope_dir / rel
            if not path.exists():
                continue
            for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    row = json.loads(raw_line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        for row in reversed(rows):
            text = clean_text(str(row.get("text") or ""))
            if text:
                kind = clean_text(str(row.get("type") or "memory")) or "memory"
                cleaned = _sanitize_legacy_display_text(text)
                group = _legacy_display_group(kind, cleaned)
                _append_legacy_unique(grouped[group], group_seen[group], cleaned, max_items=max_items)

    if not summaries and not any(grouped.values()):
        return None

    display_limits = {
        "기본 정보": min(max_items, 4),
        "취향": min(max_items, 5),
        "현재 맥락": min(max_items, 5),
        "정리 필요": min(max_items, 2),
        "기타 기억": min(max_items, 0),
    }
    limited = {title: (grouped.get(title) or [])[: display_limits[title]] for title in grouped}
    glance: list[str] = []
    if limited["기본 정보"]:
        glance.append(limited["기본 정보"][0])
    if limited["취향"]:
        glance.append(limited["취향"][0])
    if limited["현재 맥락"]:
        glance.append(limited["현재 맥락"][0])
    if limited["정리 필요"]:
        glance.append(f"확인 필요: {limited['정리 필요'][0]}")

    sections: list[str] = [
        "# 이블린 메모리",
        "",
        "> 사람이 읽기 편하도록 정리한 메모리 카드입니다. 원본 로그는 JSONL에 따로 보관됩니다.",
        "",
    ]
    sections.extend(_format_memory_callout("summary", "한눈에 보기", summaries[:1] + glance[:4], empty_text="아직 정리된 핵심 기억이 없습니다."))
    if limited["기본 정보"]:
        sections.extend(_format_memory_callout("info", "기본 정보", limited["기본 정보"]))
    if limited["취향"]:
        sections.extend(_format_memory_callout("tip", "취향", limited["취향"]))
    if limited["현재 맥락"]:
        sections.extend(_format_memory_callout("note", "현재 맥락", limited["현재 맥락"]))
    if limited["정리 필요"]:
        sections.extend(_format_memory_callout("todo", "확인할 것", limited["정리 필요"]))

    body = "\n".join(sections).strip() + "\n"
    if target.exists():
        try:
            existing_raw = target.read_text(encoding="utf-8", errors="ignore")
            existing_note = parse_memory_note(target, existing_raw)
            if (
                existing_raw.startswith("---")
                and clean_text(existing_note.body) == clean_text(body)
                and clean_text(str(existing_note.metadata.get("source") or ""))
                == "legacy-memory-mirror"
            ):
                return target
        except Exception:
            pass
    content = "\n".join(
        [
            _format_front_matter(
                {
                    "id": f"legacy-guild-{guild_id}",
                    "type": "legacy",
                    "title": "이블린 메모리",
                    "status": "active",
                    "updated_at": _utc_now_iso(),
                    "source": "legacy-memory-mirror",
                    "source_refs": [f"guild:{guild_id}"],
                    "confidence": "medium",
                    "projects": [DEFAULT_PROJECT],
                }
            ),
            "",
            body,
        ]
    )
    with _memory_edit_lock:
        atomic_text_write(target, content, durable=True)
    return target


def _legacy_scope_dirs(guild_dir: Path, *, max_scope_dirs: int = 24) -> list[Path]:
    scope_dirs = [guild_dir]
    for pattern in ("room_*", "person_*", "session_*"):
        scope_dirs.extend(sorted(guild_dir.glob(pattern))[:max_scope_dirs])
    return list(dict.fromkeys(scope_dirs))


def _legacy_source_updated_at(path: Path) -> str:
    try:
        return datetime.utcfromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat() + "Z"
    except Exception:
        return ""


def _legacy_node_title(guild_id: int, scope_dir: Path, source_label: str) -> str:
    scope_label = scope_dir.name if scope_dir.name != f"guild_{guild_id}" else "guild"
    scope_label = scope_label.replace("_", " ")
    source_label = source_label.replace("_", " ").replace("/", " ")
    return f"Legacy {scope_label} {source_label}".strip()


def _legacy_node_path(vault: Path, guild_id: int, scope_dir: Path, source_label: str) -> Path:
    key = f"{guild_id}:{scope_dir.as_posix()}:{source_label}"
    digest = _stable_id(key)
    slug = _slug(f"{scope_dir.name}-{source_label}", default="legacy-memory")
    return vault / "legacy" / f"guild-{guild_id}" / f"{slug}-{digest}.md"


def _legacy_jsonl_preview(path: Path, *, max_items: int = 16) -> list[str]:
    lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if len(lines) >= max_items:
            break
        try:
            row = json.loads(raw_line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        text = clean_text(str(row.get("text") or row.get("content") or row.get("message") or ""))
        if not text:
            continue
        label = clean_text(str(row.get("type") or row.get("role") or row.get("speaker") or "entry"))
        if len(text) > 220:
            text = text[:217].rstrip() + "..."
        lines.append(f"- **{label or 'entry'}**: {text}")
    return lines


def _legacy_text_preview(path: Path, *, max_chars: int = 1200) -> list[str]:
    text = clean_text(path.read_text(encoding="utf-8", errors="ignore"))
    if not text:
        return []
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return [text]


def _legacy_json_preview(path: Path, *, max_chars: int = 1200) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return ["```json", text, "```"]


def _write_legacy_node_note(
    target: Path,
    *,
    guild_id: int,
    title: str,
    source_path: Path,
    source_label: str,
    body_lines: list[str],
) -> Path | None:
    if not body_lines:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    note_id = f"legacy-{_stable_id(str(source_path))}"
    source_rel = source_path.as_posix()
    front_matter = _format_front_matter(
        {
            "id": note_id,
            "type": "legacy",
            "title": title,
            "tags": ["legacy-memory", source_label.replace("/", "-"), f"guild-{guild_id}"],
            "projects": [DEFAULT_PROJECT],
            "links": [f"legacy-guild-{guild_id}"],
            "source": "legacy-memory-node-mirror",
            "source_refs": [source_rel],
            "importance": "0.42",
            "confidence": "medium",
            "updated_at": _legacy_source_updated_at(source_path),
        }
    )
    content = "\n".join(
        [
            front_matter,
            "",
            f"# {title}",
            "",
            f"Source: `{source_rel}`",
            "",
            "## Entries",
            "",
            *body_lines,
            "",
        ]
    )
    with _memory_edit_lock:
        if (
            target.exists()
            and target.read_text(encoding="utf-8", errors="ignore")
            == content
        ):
            return target
        atomic_text_write(target, content, durable=True)
    return target


def refresh_legacy_memory_node_notes(
    guild_id: int,
    *,
    root: Path | None = None,
    max_scope_dirs: int = 24,
    max_items_per_note: int = 16,
) -> list[Path]:
    """Mirror legacy scoped memory files as individual Obsidian graph nodes."""
    base_root = root or MEMORY_ROOT
    guild_dir = base_root / f"guild_{guild_id}"
    if not guild_dir.exists():
        return []

    vault = ensure_memory_vault_layout(root)
    created_or_existing: list[Path] = []
    for scope_dir in _legacy_scope_dirs(guild_dir, max_scope_dirs=max_scope_dirs):
        sources: list[tuple[str, Path, str]] = [
            ("summary", scope_dir / "rolling_summary.txt", "text"),
            ("facts", scope_dir / "vault" / "facts.jsonl", "jsonl"),
            ("questions", scope_dir / "vault" / "questions.jsonl", "jsonl"),
            ("state", scope_dir / "cognitive_state.json", "json"),
        ]
        raw_dir = scope_dir / "vault" / "raw"
        if raw_dir.exists():
            for raw_path in sorted(raw_dir.glob("*.jsonl")):
                sources.append((f"raw-{raw_path.stem}", raw_path, "jsonl"))

        for source_label, source_path, source_kind in sources:
            if not source_path.exists() or not source_path.is_file():
                continue
            if source_kind == "jsonl":
                body_lines = _legacy_jsonl_preview(source_path, max_items=max_items_per_note)
            elif source_kind == "json":
                body_lines = _legacy_json_preview(source_path)
            else:
                body_lines = _legacy_text_preview(source_path)
            title = _legacy_node_title(guild_id, scope_dir, source_label)
            target = _legacy_node_path(vault, guild_id, scope_dir, source_label)
            path = _write_legacy_node_note(
                target,
                guild_id=guild_id,
                title=title,
                source_path=source_path,
                source_label=source_label,
                body_lines=body_lines,
            )
            if path is not None:
                created_or_existing.append(path)

    if created_or_existing:
        sync_memory_vault_index(root=root)
    return created_or_existing


def append_turn_rows_to_memory_vault(
    guild_id: int,
    rows: list[dict[str, Any]],
    *,
    scope_type: str = "guild",
    scope_key: str | None = None,
    scope_labels: list[str] | tuple[str, ...] | None = None,
    root: Path | None = None,
) -> Path | None:
    normalized: list[str] = []
    meaningful_user_seen = False
    for row in rows:
        text = clean_text(str(row.get("text") or ""))
        if not text:
            continue
        role = clean_text(str(row.get("role") or "memory")) or "memory"
        if len(text) > MEMORY_ROW_MAX_CHARS * 2:
            text = text[: MEMORY_ROW_MAX_CHARS * 2 - 3].rstrip() + "..."
        label = _daily_display_label(role)
        if role.lower() == "user" and _is_meaningful_daily_user_text(text):
            meaningful_user_seen = True
        normalized.append(f"- {label}: {text}")
    if not normalized or not meaningful_user_seen:
        return None

    vault = ensure_memory_vault_layout(root)
    day_key = time.strftime("%Y-%m-%d")
    path = vault / "daily" / f"{day_key}.md"
    note_id = f"daily-{day_key}"
    initialize_note = not path.exists()
    if path.exists():
        try:
            existing = parse_memory_note(path)
        except Exception:
            existing = None
        if existing is not None:
            if not memory_note_was_deleted(
                existing.note_id,
                root=root,
            ):
                note_id = existing.note_id
            else:
                initialize_note = True
    if memory_note_was_deleted(note_id, root=root):
        generation = 1
        while memory_note_was_deleted(
            f"daily-{day_key}-continuation-{generation}",
            root=root,
        ):
            generation += 1
        note_id = f"daily-{day_key}-continuation-{generation}"
        initialize_note = True
    block = "\n".join(
        [
            ">",
            f"> ### {time.strftime('%H:%M:%S')}",
            *(f"> {line}" for line in normalized),
            ">",
        ]
    )
    with _memory_edit_lock:
        should_initialize = initialize_note
        if should_initialize and path.exists():
            try:
                locked_existing = parse_memory_note(path)
            except Exception:
                locked_existing = None
            if (
                locked_existing is not None
                and locked_existing.note_id == note_id
                and not memory_note_was_deleted(
                    locked_existing.note_id,
                    root=root,
                )
            ):
                should_initialize = False
        if should_initialize:
            atomic_text_write(
                path,
                _daily_intro_block(
                    day_key,
                    note_id=note_id,
                    guild_id=guild_id,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    scope_labels=scope_labels,
                ),
                durable=True,
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
    return path


def _daily_display_label(role: str) -> str:
    normalized = clean_text(role).lower()
    if normalized == "assistant":
        return DAILY_ASSISTANT_LABEL
    if normalized == "user":
        return DAILY_USER_LABEL
    return "기록"


def _is_meaningful_daily_user_text(text: str) -> bool:
    cleaned = clean_text(text).strip()
    if not cleaned:
        return False
    stripped = re.sub(r"[\s.!?。！？,，~…\"'“”‘’`]+", "", cleaned).lower()
    if stripped in {"이블린", "이블린아", "야", "응", "어", "음", "그", "소"}:
        return False
    return len(stripped) >= 3


def consolidate_daily_memory_once(
    guild_id: int | None = None,
    *,
    root: Path | None = None,
    day_key: str | None = None,
    min_chars: int = CONSOLIDATION_MIN_DAILY_CHARS,
) -> Path | None:
    vault = ensure_memory_vault_layout(root)
    day = day_key or time.strftime("%Y-%m-%d")
    source_path = vault / "daily" / f"{day}.md"
    if not source_path.exists():
        return None
    source_text = source_path.read_text(encoding="utf-8", errors="ignore")
    source_note = parse_memory_note(source_path, source_text)
    body_raw = source_text
    if source_text.startswith("---") and "\n---" in source_text:
        parts = source_text.split("---", 2)
        if len(parts) == 3:
            body_raw = parts[2]
    body = clean_text(source_note.body)
    if len(body) < min_chars:
        return None

    digest = hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()[:12]
    target = vault / "episodes" / f"{day}-daily-consolidation.md"
    if memory_note_was_deleted(f"daily-consolidation-{day}", root=root):
        return None
    if target.exists():
        try:
            existing = parse_memory_note(target)
            if clean_text(str(existing.metadata.get("source_hash") or "")) == digest:
                return target
        except Exception:
            pass

    lines = [clean_text(line).strip("- ") for line in body_raw.splitlines()]
    highlights = [line for line in lines if line and not line.startswith("#")][:40]
    if not highlights:
        return None
    title = f"Daily Memory Consolidation {day}"
    front_matter = _format_front_matter(
        {
            "id": f"daily-consolidation-{day}",
            "type": "episode",
            "title": title,
            "status": "active",
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "importance": 0.45,
            "confidence": "medium",
            "source": "daily-consolidation",
            "source_refs": [f"daily/{day}"],
            "derived_from": [source_note.note_id],
            "evidence_hashes": [digest],
            "source_hash": digest,
            "tags": ["daily", "consolidated", "conversation"],
            "projects": [DEFAULT_PROJECT],
            "links": [f"daily/{day}"],
        }
    )
    scope = f"guild:{guild_id}" if guild_id is not None else "guild:unknown"
    content = "\n".join(
        [
            front_matter,
            "",
            f"# {title}",
            "",
            f"Source: [[daily/{day}]]",
            f"Scope: {scope}",
            "",
            "## Highlights",
            *[f"- {item}" for item in highlights],
            "",
        ]
    )
    with _memory_edit_lock:
        atomic_text_write(target, content, durable=True)
    sync_memory_vault_index(root=root)
    return target


def request_sub_llm_json(
    messages: list[dict[str, Any]],
    *,
    summary_llm_url: str | None = None,
    model_name: str | None = None,
    max_tokens: int = 900,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    endpoint = clean_text(summary_llm_url or SUMMARY_LLM_URL)
    if not endpoint:
        raise RuntimeError("SUMMARY_LLM_URL is empty")
    payload = {
        "model": clean_text(model_name or SUMMARY_MODEL_NAME),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=max(1.0, timeout_s)) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    data = json.loads(raw)
    choices = data.get("choices", []) if isinstance(data, dict) else []
    if not choices:
        return {}
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    text = clean_text(str(message.get("content") or message.get("reasoning_content") or ""))
    return _json_object_from_text(text)


def run_semantic_memory_consolidation_once(
    guild_id: int,
    *,
    root: Path | None = None,
    day_key: str | None = None,
    sub_llm_health: dict[str, Any] | None = None,
    llm_client: Any | None = None,
    min_chars: int = CONSOLIDATION_MIN_DAILY_CHARS,
    max_source_chars: int = SEMANTIC_CONSOLIDATION_MAX_SOURCE_CHARS,
) -> dict[str, Any]:
    started = time.monotonic()
    health = sub_llm_health if isinstance(sub_llm_health, dict) else probe_sub_llm_dependency()
    if not health.get("available"):
        return {
            "status": "skipped_sub_llm_unavailable",
            "created_notes": [],
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        }

    vault = ensure_memory_vault_layout(root)
    day = day_key or time.strftime("%Y-%m-%d")
    source_path = vault / "daily" / f"{day}.md"
    if not source_path.exists():
        return {
            "status": "skipped_missing_daily_note",
            "created_notes": [],
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        }
    source_text = source_path.read_text(encoding="utf-8", errors="ignore")
    source_note = parse_memory_note(source_path, source_text)
    source_body = clean_text(source_note.body)
    if len(source_body) < min_chars:
        return {
            "status": "skipped_not_enough_daily_memory",
            "created_notes": [],
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        }
    source_body = source_body[:max_source_chars]
    digest = hashlib.sha1(source_body.encode("utf-8", errors="ignore")).hexdigest()[:12]

    messages = [
        {
            "role": "system",
            "content": (
                "You are Evelyn's memory consolidation worker. Return exactly one JSON object. "
                "Create durable Obsidian-compatible notes only for important memory. "
                "Required shape: {\"notes\":[{\"type\":\"episode|concept|procedure|project\","
                "\"title\":\"short title\",\"body\":\"specific durable memory\","
                "\"tags\":[\"tag\"],\"links\":[\"related-note\"],\"importance\":0.0,"
                "\"confidence\":\"low|medium|high\"}]}. "
                "Do not include secrets. Do not create generic notes. Prefer concise Korean or English matching the source."
            ),
        },
        {
            "role": "user",
            "content": (
                f"guild_id={guild_id}\n"
                f"day={day}\n"
                f"source_hash={digest}\n\n"
                f"Daily memory markdown body:\n{source_body}"
            ),
        },
    ]

    try:
        result = llm_client(messages) if llm_client is not None else request_sub_llm_json(messages)
    except Exception as exc:
        return {
            "status": "failed_sub_llm_request",
            "error": clean_text(repr(exc))[:300],
            "created_notes": [],
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        }

    notes = result.get("notes", []) if isinstance(result, dict) else []
    if not isinstance(notes, list):
        notes = []

    created: list[str] = []
    for item in notes[:SEMANTIC_CONSOLIDATION_MAX_NOTES]:
        if not isinstance(item, dict):
            continue
        note_type = clean_text(str(item.get("type") or "episode")).lower()
        if note_type not in {"episode", "concept", "procedure", "project"}:
            note_type = "episode"
        title = clean_text(str(item.get("title") or "Semantic Memory Consolidation"))[:120]
        body = clean_text(str(item.get("body") or ""))
        if len(body) < 20:
            continue
        tags = list(_as_list(item.get("tags")))[:12]
        links = list(_as_list(item.get("links")))
        links.append(f"daily/{day}")
        try:
            importance = max(0.0, min(1.0, float(item.get("importance", 0.55) or 0.55)))
        except Exception:
            importance = 0.55
        confidence = clean_text(str(item.get("confidence") or "medium")) or "medium"
        try:
            path = write_memory_vault_note(
                note_type=note_type,
                title=title,
                body=body,
                tags=tags or ["semantic-consolidation"],
                links=links,
                source="sub-llm-semantic-consolidation",
                source_refs=[f"daily/{day}"],
                derived_from=[source_note.note_id],
                evidence_hashes=[digest],
                importance=importance,
                confidence=confidence,
                root=root,
            )
        except MemoryNoteDeletedError:
            continue
        created.append(str(path))

    sync_memory_vault_index(root=root)
    return {
        "status": "created" if created else "no_notes_created",
        "created_notes": created,
        "source_daily": str(source_path),
        "source_hash": digest,
        "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
    }


def _write_recomposed_memory_note(
    path: Path,
    raw: str,
    note: MemoryVaultNote,
    *,
    title: str,
    body: str,
    tags: list[str],
    links: list[str],
    confidence: str,
    source_note_ids: list[str],
    source_hashes: list[str],
    revoked_source_ids: list[str],
) -> MemoryVaultNote:
    metadata, _old_body = _split_front_matter(raw)
    recomposed_at = _utc_now_iso()
    next_title = clean_text(title)[:MEMORY_EDIT_MAX_TITLE_CHARS]
    next_body = _normalize_memory_edit_body(body)
    if not next_title or not next_body:
        raise ValueError("memory_recomposition_empty")

    if not metadata:
        metadata = {
            "id": note.note_id,
            "type": note.note_type,
            "status": note.status or "active",
            "created_at": recomposed_at,
            "projects": [DEFAULT_PROJECT],
        }
    if not clean_text(str(metadata.get("origin_source") or "")):
        metadata["origin_source"] = clean_text(
            str(metadata.get("source") or "unknown")
        )
    if not _as_list(metadata.get("origin_source_refs")):
        metadata["origin_source_refs"] = list(
            _as_list(
                metadata.get("source_refs")
                or metadata.get("source_ref")
            )
        )
    origin_derivations = list(
        _as_list(metadata.get("origin_derived_from"))
    )
    origin_derivations.extend(
        _as_list(metadata.get("derived_from"))
    )
    metadata.update(
        {
            "title": next_title,
            "updated_at": recomposed_at,
            "source": "sub-llm-partial-recomposition",
            "source_refs": source_note_ids,
            "derived_from": source_note_ids,
            "origin_derived_from": list(
                dict.fromkeys(origin_derivations)
            )[:12],
            "evidence_hashes": source_hashes,
            "revoked_source_ids": list(
                dict.fromkeys(revoked_source_ids)
            )[:12],
            "tags": list(dict.fromkeys(tags))[:12],
            "links": list(dict.fromkeys(links))[:12],
            "confidence": confidence,
            "revision": max(
                0,
                _front_matter_int(
                    metadata,
                    "revision",
                    0,
                ),
            )
            + 1,
            "revocation_resolved_at": recomposed_at,
            "revocation_resolution": (
                "sub-llm-partial-recomposition"
            ),
            "recomposition_of": note.note_id,
        }
    )
    metadata.pop("source_hash", None)
    content = (
        _format_front_matter(metadata)
        + "\n\n"
        + f"# {next_title}\n\n{next_body}\n"
    )
    atomic_text_write(path, content, durable=True)
    return parse_memory_note(path, content)


def run_memory_derivation_recomposition_once(
    *,
    root: Path | None = None,
    sub_llm_health: dict[str, Any] | None = None,
    llm_client: Any | None = None,
    max_notes: int = 4,
    max_source_chars: int = 2400,
) -> dict[str, Any]:
    """Rebuild quarantined notes from remaining live sources only."""

    started = time.monotonic()
    sync_memory_vault_index(root=root)
    health = (
        sub_llm_health
        if isinstance(sub_llm_health, dict)
        else probe_sub_llm_dependency()
    )
    entries = _read_memory_derivation_revocations(root)
    if not entries:
        return {
            "schema": MEMORY_DERIVATION_RECOMPOSITION_SCHEMA,
            "status": "clear",
            "recomposedNoteIds": [],
            "pendingNoteIds": [],
            "latencyMs": round(
                (time.monotonic() - started) * 1000.0,
                1,
            ),
        }
    if not health.get("available"):
        return {
            "schema": MEMORY_DERIVATION_RECOMPOSITION_SCHEMA,
            "status": "skipped_sub_llm_unavailable",
            "recomposedNoteIds": [],
            "pendingNoteIds": sorted(entries),
            "latencyMs": round(
                (time.monotonic() - started) * 1000.0,
                1,
            ),
        }

    recomposed: list[str] = []
    errors: list[dict[str, str]] = []
    attempted_note_ids: set[str] = set()
    attempts = max(1, min(20, int(max_notes or 1)))
    for _index in range(attempts):
        sync_memory_vault_index(root=root)
        entries = _read_memory_derivation_revocations(root)
        if not entries:
            break
        _nodes, note_sources = _memory_derivation_nodes(
            root=root
        )
        deleted_ids = _memory_deleted_note_ids(root)
        quarantined_ids = set(entries)
        ready: tuple[
            str,
            dict[str, Any],
            list[str],
        ] | None = None
        for note_id in sorted(entries):
            if note_id in attempted_note_ids:
                continue
            source = note_sources.get(note_id)
            if source is None:
                continue
            _target_path, target_note = source
            dependencies = list(
                _as_list(
                    target_note.metadata.get(
                        "derived_from"
                    )
                )
            )
            live_source_ids = [
                source_id
                for source_id in dependencies
                if (
                    source_id not in deleted_ids
                    and source_id not in quarantined_ids
                    and source_id in note_sources
                )
            ]
            blocked_source_ids = [
                source_id
                for source_id in dependencies
                if source_id in quarantined_ids
            ]
            if live_source_ids and not blocked_source_ids:
                ready = (
                    note_id,
                    entries[note_id],
                    live_source_ids,
                )
                break
        if ready is None:
            break

        note_id, revocation, live_source_ids = ready
        attempted_note_ids.add(note_id)
        _target_path, target_note = note_sources[note_id]
        target_hash = target_note.source_hash
        source_documents: list[dict[str, Any]] = []
        source_versions: dict[str, str] = {}
        for source_id in live_source_ids:
            _source_path, source_note = note_sources[source_id]
            source_versions[source_id] = (
                source_note.source_hash
            )
            source_documents.append(
                {
                    "id": source_id,
                    "type": source_note.note_type,
                    "title": source_note.title,
                    "body": source_note.body[
                        : max(200, max_source_chars)
                    ],
                    "evidenceHash": (
                        source_note.source_hash
                    ),
                }
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Evelyn's privacy-preserving memory "
                    "recomposition worker. Return exactly one JSON "
                    "object shaped as {\"note\":{\"title\":\"...\","
                    "\"body\":\"...\",\"tags\":[\"...\"],"
                    "\"links\":[\"...\"],"
                    "\"confidence\":\"low|medium|high\"}}. "
                    "Use only the supplied live source notes. Never "
                    "infer, restore, mention, or preserve information "
                    "from a revoked source. If the live sources do not "
                    "support a fact, omit it."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "targetType": target_note.note_type,
                        "liveSources": source_documents,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        try:
            result = (
                llm_client(messages)
                if llm_client is not None
                else request_sub_llm_json(messages)
            )
        except Exception as exc:
            errors.append(
                {
                    "noteId": note_id,
                    "error": type(exc).__name__,
                }
            )
            continue
        item = (
            result.get("note")
            if isinstance(result, dict)
            and isinstance(result.get("note"), dict)
            else {}
        )
        next_title = clean_text(
            str(item.get("title") or "")
        )
        next_body = _normalize_memory_edit_body(
            str(item.get("body") or "")
        )
        if not next_title or len(next_body) < 10:
            errors.append(
                {
                    "noteId": note_id,
                    "error": "memory_recomposition_invalid",
                }
            )
            continue
        confidence = clean_text(
            str(item.get("confidence") or "medium")
        ).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        tags = list(_as_list(item.get("tags")))
        links = list(_as_list(item.get("links")))

        with _memory_edit_lock:
            current = _memory_vault_find_note(
                note_id,
                root=root,
            )
            if current is None:
                continue
            current_path, current_note, current_raw = current
            if not secrets.compare_digest(
                current_note.source_hash,
                target_hash,
            ):
                errors.append(
                    {
                        "noteId": note_id,
                        "error": (
                            "memory_recomposition_target_changed"
                        ),
                    }
                )
                continue
            sources_changed = False
            for source_id, expected_hash in (
                source_versions.items()
            ):
                current_source = _memory_vault_find_note(
                    source_id,
                    root=root,
                )
                if (
                    current_source is None
                    or not secrets.compare_digest(
                        current_source[1].source_hash,
                        expected_hash,
                    )
                ):
                    sources_changed = True
                    break
            if sources_changed:
                errors.append(
                    {
                        "noteId": note_id,
                        "error": (
                            "memory_recomposition_source_changed"
                        ),
                    }
                )
                continue
            try:
                _write_recomposed_memory_note(
                    current_path,
                    current_raw,
                    current_note,
                    title=next_title,
                    body=next_body,
                    tags=tags,
                    links=links,
                    confidence=confidence,
                    source_note_ids=live_source_ids,
                    source_hashes=[
                        source_versions[source_id]
                        for source_id in live_source_ids
                    ],
                    revoked_source_ids=list(
                        revocation.get(
                            "revokedSourceIds"
                        )
                        or []
                    ),
                )
            except (OSError, ValueError) as exc:
                errors.append(
                    {
                        "noteId": note_id,
                        "error": type(exc).__name__,
                    }
                )
                continue
        recomposed.append(note_id)

    sync_memory_vault_index(root=root)
    pending = sorted(
        _read_memory_derivation_revocations(root)
    )
    return {
        "schema": MEMORY_DERIVATION_RECOMPOSITION_SCHEMA,
        "status": (
            "recomposed"
            if recomposed
            else "pending"
            if pending
            else "clear"
        ),
        "recomposedNoteIds": recomposed,
        "pendingNoteIds": pending,
        "errors": errors,
        "latencyMs": round(
            (time.monotonic() - started) * 1000.0,
            1,
        ),
    }


def run_memory_vault_maintenance_once(guild_id: int, *, root: Path | None = None) -> dict[str, Any]:
    started = time.monotonic()
    sub_llm = probe_sub_llm_dependency()
    derivation_recomposition = (
        run_memory_derivation_recomposition_once(
            root=root,
            sub_llm_health=sub_llm,
        )
    )
    bootstrap_paths = bootstrap_memory_vault_source(root=root)
    legacy_path = refresh_legacy_memory_mirror(guild_id, root=root)
    legacy_nodes = refresh_legacy_memory_node_notes(guild_id, root=root)
    consolidated_path = consolidate_daily_memory_once(guild_id, root=root)
    semantic_consolidation = run_semantic_memory_consolidation_once(
        guild_id,
        root=root,
        sub_llm_health=sub_llm,
    )
    version = sync_memory_vault_index(root=root)
    hot_context = refresh_memory_hot_context(root=root, dependency_health={"sub_llm": sub_llm})
    return {
        "guild_id": guild_id,
        "memory_version": version,
        "vault_path": str(memory_vault_root(root)),
        "index_path": str(memory_index_db_path(root)),
        "bootstrap_notes": [str(path) for path in bootstrap_paths],
        "legacy_mirror": str(legacy_path) if legacy_path else "",
        "legacy_nodes": [str(path) for path in legacy_nodes],
        "daily_consolidation": str(consolidated_path) if consolidated_path else "",
        "semantic_consolidation": semantic_consolidation,
        "derivation_recomposition": (
            derivation_recomposition
        ),
        "hot_context_sources": hot_context.get("sources", []),
        "dependencies": {"sub_llm": sub_llm},
        "semantic_consolidation_enabled": bool(sub_llm.get("available")),
        "fallback_mode": "" if sub_llm.get("available") else sub_llm.get("fallback_mode"),
        "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
    }


def recall_memory_vault(
    request: MemoryRecallRequest,
    *,
    root: Path | None = None,
    db_path: Path | None = None,
) -> MemoryRecallResult:
    started = time.monotonic()
    try:
        if request.guild_id is not None:
            refresh_legacy_memory_mirror(request.guild_id, root=root)
            refresh_legacy_memory_node_notes(request.guild_id, root=root)
        version = sync_memory_vault_index(root=root, db_path=db_path)
        index_path = db_path or memory_index_db_path(root)
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        active_project = clean_text(str(metadata.get("active_project") or DEFAULT_PROJECT)).lower()
        focus_items = metadata.get("context_focus") if isinstance(metadata.get("context_focus"), list) else []
        allow_internal_memory = _allows_internal_memory_recall(request, focus_items)
        query_tokens = _tokenize(request.user_text)
        focus_tokens = _tokenize(" ".join(clean_text(str(item)) for item in focus_items))
        cache_key = _cache_key(request, version)

        with closing(_connect_index(index_path)) as conn:
            _ensure_schema(conn)
            cached = _read_retrieval_cache(conn, cache_key, version)
            if cached is not None:
                context_text = clean_text(str(cached.get("context_text") or ""))
                return MemoryRecallResult(
                    turn_id=request.turn_id,
                    ok=True,
                    context_text=context_text,
                    facts=tuple(cached.get("facts") or ()),
                    sources=tuple(cached.get("sources") or ()),
                    latency_ms=(time.monotonic() - started) * 1000.0,
                    metadata={
                        "cache_hit": True,
                        "memory_version": version,
                        "retrieval_mode": cached.get("retrieval_mode") or "cache",
                        "provenance": list(cached.get("provenance") or []),
                    },
                )

            rows, retrieval_mode = _fetch_candidate_rows(
                conn,
                query_tokens=query_tokens,
                focus_tokens=focus_tokens,
                limit=max(80, request.max_items * 20),
            )
            if not allow_internal_memory:
                rows = [row for row in rows if not _is_internal_memory_note(row)]
            vector_scores = _fetch_vector_scores(
                conn,
                " ".join([request.user_text, " ".join(clean_text(str(item)) for item in focus_items)]),
                limit=max(20, request.max_items * 8),
            )
            if vector_scores:
                existing_note_ids = {clean_text(str(row["note_id"])) for row in rows}
                missing_vector_ids = [note_id for note_id in vector_scores if note_id not in existing_note_ids]
                if missing_vector_ids:
                    rows.extend(_fetch_notes_by_ids(conn, missing_vector_ids))
                if not allow_internal_memory:
                    rows = [row for row in rows if not _is_internal_memory_note(row)]
                retrieval_mode = f"{retrieval_mode}+vector"
            scored: list[tuple[int, int, sqlite3.Row]] = []
            for recency, row in enumerate(rows):
                score = _note_score(row, query_tokens, focus_tokens, active_project)
                score += int(vector_scores.get(clean_text(str(row["note_id"])), 0.0) * 24)
                if score > 0:
                    scored.append((score, -recency, row))
            if not scored and rows:
                scored = [(1, -index, row) for index, row in enumerate(rows[: request.max_items])]
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            selected = [row for _, _, row in scored[: max(1, request.max_items)]]
            graph_neighbors = _expand_graph_neighbors(
                conn,
                selected,
                max_extra=max(0, request.max_items - len(selected)),
            )
            if not allow_internal_memory:
                graph_neighbors = [row for row in graph_neighbors if not _is_internal_memory_note(row)]
            if graph_neighbors:
                selected.extend(graph_neighbors)
            snippets = [_truncate_note(row) for row in selected]
            sources = [clean_text(str(row["rel_path"])) for row in selected]
            provenance = [_memory_row_provenance(row) for row in selected]
            procedure_rows = [
                row for _, _, row in scored
                if allow_internal_memory and clean_text(str(row["note_type"])) == "procedure"
            ][:2]
            procedure_snippets = [_truncate_note(row, max_chars=300) for row in procedure_rows]

            context_parts: list[str] = []
            if snippets:
                context_parts.append("[Memory Vault Notes]\n" + "\n".join(snippets))
            if procedure_snippets:
                context_parts.append("[Procedural Memory]\n" + "\n".join(procedure_snippets))
            if provenance:
                context_parts.append(
                    "[Memory Provenance]\n"
                    + "\n".join(
                        _memory_provenance_context_line(item)
                        for item in provenance
                    )
                )
            context_text = "\n\n".join(context_parts)
            payload = {
                "context_text": context_text,
                "facts": snippets,
                "sources": sources,
                "retrieval_mode": retrieval_mode,
                "provenance": provenance,
            }
            _write_retrieval_cache(conn, cache_key, version, payload)

        return MemoryRecallResult(
            turn_id=request.turn_id,
            ok=True,
            context_text=context_text,
            facts=tuple(snippets),
            sources=tuple(sources),
            latency_ms=(time.monotonic() - started) * 1000.0,
            metadata={
                "cache_hit": False,
                "memory_version": version,
                "retrieval_mode": retrieval_mode,
                "provenance": provenance,
            },
        )
    except Exception as exc:
        return MemoryRecallResult(
            turn_id=request.turn_id,
            ok=False,
            context_text="",
            latency_ms=(time.monotonic() - started) * 1000.0,
            error_text=clean_text(repr(exc))[:300],
        )


def build_memory_vault_context(
    guild_id: int,
    user_text: str,
    *,
    session_key: str | None = None,
    topic_id: str | None = None,
    source: str = "context",
    context_focus: list[str] | None = None,
    max_items: int = 5,
    root: Path | None = None,
) -> str:
    request = MemoryRecallRequest(
        turn_id=f"memory-vault-{int(time.time() * 1000)}",
        session_key=session_key,
        guild_id=guild_id,
        user_text=user_text,
        topic_id=topic_id,
        source=source,
        max_items=max_items,
        metadata={
            "active_project": DEFAULT_PROJECT,
            "context_focus": context_focus or [],
        },
    )
    result = recall_memory_vault(request, root=root)
    if not result.ok or not result.context_text:
        hot_context = read_memory_hot_context(root=root, max_chars=1200)
        return f"[Pinned Memory Vault]\n{hot_context}" if hot_context else ""
    cache_label = "hit" if result.metadata.get("cache_hit") else "miss"
    version = result.metadata.get("memory_version", 0)
    mode = clean_text(str(result.metadata.get("retrieval_mode") or "unknown"))
    hot_context = read_memory_hot_context(root=root, max_chars=1200)
    parts = []
    if hot_context:
        parts.append("[Pinned Memory Vault]\n" + hot_context)
    parts.append(result.context_text)
    parts.append(f"[Memory Cache]\n- retrieval_cache: {cache_label}\n- retrieval_mode: {mode}\n- memory_version: {version}")
    return "\n\n".join(parts)


def write_memory_vault_note(
    *,
    note_type: str,
    title: str,
    body: str,
    tags: list[str] | None = None,
    projects: list[str] | None = None,
    links: list[str] | None = None,
    source: str = "runtime",
    source_refs: list[str] | None = None,
    derived_from: list[str] | None = None,
    evidence_hashes: list[str] | None = None,
    importance: float = 0.5,
    confidence: str = "medium",
    root: Path | None = None,
) -> Path:
    vault = ensure_memory_vault_layout(root)
    normalized_type = clean_text(note_type).lower() or "concept"
    folder_by_type = {
        "core": "core",
        "daily": "daily",
        "episode": "episodes",
        "episodes": "episodes",
        "concept": "concepts",
        "semantic": "concepts",
        "procedure": "procedures",
        "procedural": "procedures",
        "project": "projects",
    }
    folder = folder_by_type.get(normalized_type, "concepts")
    slug = _slug(title, default=normalized_type)
    path = vault / folder / f"{slug}.md"
    note_id = f"{normalized_type}-{_stable_id(folder + '/' + slug)}"
    normalized_derivations = list(
        dict.fromkeys(_as_list(derived_from))
    )[:12]
    if (
        _memory_source_type(source, normalized_type)
        == "derived"
        and not normalized_derivations
    ):
        try:
            _record_memory_provenance_forward_rejection(
                normalized_type,
                root=root,
            )
        except Exception:
            pass
        raise ValueError("memory_derived_from_required")
    if note_id in normalized_derivations:
        raise ValueError("memory_derivation_self_reference")
    if memory_note_was_deleted(note_id, root=root):
        raise MemoryNoteDeletedError(
            f"memory note {note_id} was permanently deleted"
        )
    now = _utc_now_iso()
    front_matter = _format_front_matter(
        {
            "id": note_id,
            "type": normalized_type,
            "title": title,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "importance": max(0.0, min(1.0, importance)),
            "confidence": confidence,
            "source": source,
            "source_refs": source_refs or [],
            "derived_from": normalized_derivations,
            "evidence_hashes": evidence_hashes or [],
            "tags": tags or [],
            "projects": projects or [DEFAULT_PROJECT],
            "links": links or [],
        }
    )
    content = f"{front_matter}\n\n# {clean_text(title)}\n\n{clean_text(body)}\n"
    with _memory_edit_lock:
        atomic_text_write(path, content, durable=True)
    sync_memory_vault_index(root=root)
    return path


def _user_note_state_path(root: Path | None = None) -> Path:
    return memory_index_dir(root) / USER_NOTE_STATE_NAME


def _read_user_note_state(root: Path | None = None) -> dict[str, dict[str, Any]]:
    path = _user_note_state_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    notes = payload.get("notes", payload)
    if not isinstance(notes, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for key, value in notes.items():
        note_id = clean_text(str(key))
        if note_id and isinstance(value, dict):
            output[note_id] = dict(value)
    return output


def _write_user_note_state(state: dict[str, dict[str, Any]], root: Path | None = None) -> None:
    path = _user_note_state_path(root)
    atomic_json_write(
        path,
        {
            "updated_at": _utc_now_iso(),
            "notes": state,
        },
    )


def _memory_vault_note_preview(body: str, *, max_chars: int = 340) -> str:
    lines: list[str] = []
    for raw_line in clean_text(body).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^\[![^\]]+\]-?\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)
        line = clean_text(line)
        if line:
            lines.append(line)
    preview = clean_text(" ".join(lines))
    if len(preview) > max_chars:
        preview = preview[: max_chars - 1].rstrip() + "…"
    return preview


def _memory_vault_edit_body(note: MemoryVaultNote, raw: str | None = None) -> str:
    if raw is not None:
        _metadata, body = _split_front_matter(raw)
    else:
        body = note.body or ""
    lines = body.strip().splitlines()
    if not lines:
        return ""
    first = clean_text(lines[0]).lstrip("#").strip()
    if first and clean_text(first).lower() == clean_text(note.title).lower():
        lines = lines[1:]
        while lines and not clean_text(lines[0]):
            lines = lines[1:]
    return "\n".join(lines).strip()


def _memory_vault_note_category(note: MemoryVaultNote) -> str:
    rel = note.rel_path.lower()
    note_type = note.note_type.lower()
    if rel.startswith("core/legacy-guild-"):
        return "핵심 기억"
    if note_type == "daily" or rel.startswith("daily/"):
        return "대화 기록"
    if note_type in {"project", "projects"}:
        return "프로젝트"
    if note_type in {"procedure", "procedural"}:
        return "운영 방법"
    if note_type in {"episode", "episodes"}:
        return "대화 요약"
    return "지식"


def _memory_vault_user_card(
    path: Path,
    note: MemoryVaultNote,
    raw: str,
    note_state: dict[str, Any],
    *,
    hidden: bool,
    rel_path: str,
    revocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    confirmed_at = clean_text(str(note_state.get("confirmed_at") or note.metadata.get("confirmed_at") or ""))
    is_locked_legacy = _is_legacy_memory_note_type(note.note_type, rel_path)
    is_internal_note = _is_internal_memory_note_type(note.note_type)
    is_bootstrap_note = (
        clean_text(str(note.metadata.get("source") or "")) == BOOTSTRAP_NOTE_SOURCE
    )
    locked_preview = _locked_memory_preview(note.note_type, rel_path)
    revocation_state = (
        dict(revocation)
        if isinstance(revocation, dict)
        else {}
    )
    quarantined = (
        revocation_state.get("state") == "quarantined"
    )
    return {
        "id": note.note_id,
        "title": _legacy_to_public_title(note.note_type, rel_path, note.title),
        "category": _memory_vault_note_category(note),
        "type": note.note_type,
        "path": rel_path,
        "body": "" if is_locked_legacy else _memory_vault_edit_body(note, raw),
        "preview": locked_preview if is_locked_legacy else _memory_vault_note_preview(note.body),
        "confirmed": bool(confirmed_at),
        "confirmedAt": confirmed_at,
        "pinned": bool(note_state.get("pinned")),
        "hidden": hidden,
        "quarantined": quarantined,
        "recallEligible": not quarantined,
        "revocation": revocation_state,
        "locked": is_locked_legacy,
        "canEdit": not is_locked_legacy,
        "canConfirm": not quarantined,
        "canDelete": (
            not is_internal_note
            and not is_locked_legacy
            and not is_bootstrap_note
        ),
        "deleteProtectedReason": (
            "internal_note_not_public"
            if is_internal_note
            else "legacy_source_managed"
            if is_locked_legacy
            else "bootstrap_contract_note"
            if is_bootstrap_note
            else ""
        ),
        "contentHidden": is_locked_legacy,
        "confidence": clean_text(str(note.metadata.get("confidence") or "")),
        "importance": _front_matter_float(note.metadata, "importance", 0.5),
        "updatedAt": clean_text(str(note.metadata.get("updated_at") or note.updated_at or "")),
        "sourceHash": note.source_hash,
        "provenance": _memory_note_provenance(
            note,
            rel_path=rel_path,
            note_state=note_state,
        ),
    }


def _memory_vault_find_note(note_id_or_rel_path: str, *, root: Path | None = None) -> tuple[Path, MemoryVaultNote, str] | None:
    vault = ensure_memory_vault_layout(root)
    deleted_note_ids = _memory_deleted_note_ids(root)
    target = clean_text(note_id_or_rel_path)
    if not target:
        return None
    for path in vault.rglob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            note = parse_memory_note(path, raw)
        except Exception:
            continue
        if note.note_id in deleted_note_ids:
            continue
        rel_path = path.relative_to(vault).as_posix()
        if target in {note.note_id, rel_path, path.stem}:
            return path, note, raw
    return None


def _normalize_memory_edit_body(value: str) -> str:
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+",
        " ",
        normalized,
    )
    return "\n".join(line.rstrip() for line in normalized.splitlines()).strip()


def _memory_edit_evidence_hash(*, title: str, body: str) -> str:
    payload = json.dumps(
        {"body": body, "title": title},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_memory_vault_note_body(
    path: Path,
    raw: str,
    note: MemoryVaultNote,
    *,
    title: str,
    body: str,
) -> MemoryVaultNote:
    metadata, current_body = _split_front_matter(raw)
    next_title = clean_text(title or note.title or path.stem)
    next_body = _normalize_memory_edit_body(
        body if body is not None else current_body
    )
    edited_at = _utc_now_iso()
    if metadata:
        metadata["title"] = next_title
        metadata["updated_at"] = edited_at
        metadata["user_edited_at"] = edited_at
        if not clean_text(str(metadata.get("origin_source") or "")):
            metadata["origin_source"] = clean_text(
                str(metadata.get("source") or "unknown")
            )
        if not _as_list(metadata.get("origin_source_refs")):
            metadata["origin_source_refs"] = list(
                _as_list(
                    metadata.get("source_refs")
                    or metadata.get("source_ref")
                )
            )
        previous_derivations = list(
            _as_list(metadata.get("origin_derived_from"))
        )
        previous_derivations.extend(
            _as_list(metadata.get("derived_from"))
        )
        metadata["origin_derived_from"] = list(
            dict.fromkeys(previous_derivations)
        )[:12]
        metadata["derived_from"] = []
        previous_evidence = list(
            _as_list(
                metadata.get("revised_from_evidence_hashes")
            )
        )
        previous_evidence.extend(
            _as_list(
                metadata.get("evidence_hashes")
                or metadata.get("source_hash")
            )
        )
        metadata["revised_from_evidence_hashes"] = list(
            dict.fromkeys(previous_evidence)
        )[:12]
        metadata["source"] = "user-edit"
        metadata["source_refs"] = [
            "control-page-memory-editor"
        ]
        metadata["evidence_hashes"] = [
            _memory_edit_evidence_hash(
                title=next_title,
                body=next_body,
            )
        ]
        metadata.pop("source_hash", None)
        metadata["confidence"] = "high"
        metadata["revocation_resolved_at"] = edited_at
        metadata["revocation_resolution"] = "user-edit"
        metadata["revision"] = max(
            0,
            _front_matter_int(metadata, "revision", 0),
        ) + 1
        content = (
            _format_front_matter(metadata)
            + "\n\n"
            + f"# {next_title}\n\n{next_body}\n"
        )
    else:
        metadata = {
            "id": note.note_id,
            "type": note.note_type,
            "title": next_title,
            "status": note.status or "active",
            "created_at": edited_at,
            "updated_at": edited_at,
            "user_edited_at": edited_at,
            "confidence": "high",
            "source": "user-edit",
            "source_refs": ["control-page-memory-editor"],
            "origin_source": "unknown",
            "origin_source_refs": [],
            "derived_from": [],
            "origin_derived_from": [],
            "evidence_hashes": [
                _memory_edit_evidence_hash(
                    title=next_title,
                    body=next_body,
                )
            ],
            "revised_from_evidence_hashes": [],
            "revision": 1,
            "revocation_resolved_at": edited_at,
            "revocation_resolution": "user-edit",
        }
        content = (
            _format_front_matter(metadata)
            + "\n\n"
            + f"# {next_title}\n\n{next_body}\n"
        )
    atomic_text_write(path, content, durable=True)
    return parse_memory_note(path, content)


def memory_vault_user_snapshot(
    *,
    root: Path | None = None,
    include_hidden: bool = False,
    include_internal: bool = False,
    limit: int = 80,
) -> dict[str, Any]:
    version = sync_memory_vault_index(root=root)
    vault = ensure_memory_vault_layout(root)
    state = _read_user_note_state(root)
    revocations = _read_memory_derivation_revocations(root)
    deleted_note_ids = _memory_deleted_note_ids(root)
    cards: list[dict[str, Any]] = []
    counts = {
        "total": 0,
        "confirmed": 0,
        "unconfirmed": 0,
        "pinned": 0,
        "hidden": 0,
        "quarantined": 0,
    }
    for path in vault.rglob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            note = parse_memory_note(path, raw)
        except Exception:
            continue
        if note.note_id in deleted_note_ids:
            continue
        rel_path = path.relative_to(vault).as_posix()
        if not include_internal and _is_internal_memory_note_type(note.note_type):
            continue
        note_state = state.get(note.note_id, {})
        hidden = bool(note_state.get("hidden")) or note.status in {"archived", "superseded"}
        if hidden:
            counts["hidden"] += 1
            if not include_hidden:
                continue
        confirmed_at = clean_text(str(note_state.get("confirmed_at") or note.metadata.get("confirmed_at") or ""))
        pinned = bool(note_state.get("pinned"))
        counts["total"] += 1
        if confirmed_at:
            counts["confirmed"] += 1
        else:
            counts["unconfirmed"] += 1
        if pinned:
            counts["pinned"] += 1
        revocation = revocations.get(note.note_id)
        if revocation:
            counts["quarantined"] += 1
        cards.append(
            _memory_vault_user_card(
                path,
                note,
                raw,
                note_state,
                hidden=hidden,
                rel_path=rel_path,
                revocation=revocation,
            )
        )
    cards.sort(
        key=lambda item: (
            not item["quarantined"],
            not item["pinned"],
            item["confirmed"],
            -float(item["importance"] or 0),
            item["title"],
        )
    )
    cards = cards[: max(1, limit)]
    return {
        "ok": True,
        "memoryVersion": version,
        "vaultPath": str(vault),
        "counts": counts,
        "quarantineStatus": memory_quarantine_status(
            root=root,
            entries=revocations,
        ),
        "cards": cards,
        "includeInternal": bool(include_internal),
        "hiddenTypes": sorted(MEMORY_INTERNAL_NOTE_TYPES) if not include_internal else [],
        "checkedAt": _utc_now_iso(),
    }


def memory_vault_user_note(
    note_id_or_rel_path: str,
    *,
    root: Path | None = None,
    include_internal: bool = False,
) -> dict[str, Any]:
    target = _memory_vault_find_note(note_id_or_rel_path, root=root)
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    path, note, raw = target
    if not include_internal and _is_internal_memory_note_type(note.note_type):
        return {"ok": False, "error": "note_not_found"}
    vault = ensure_memory_vault_layout(root)
    rel_path = path.relative_to(vault).as_posix()
    state = _read_user_note_state(root)
    note_state = state.get(note.note_id, {})
    (
        derivation_resolution,
        _derivation_nodes,
        _derivation_sources,
        revocations,
    ) = _resolve_current_memory_derivations(root=root)
    if note.note_id in derivation_resolution.deleted_note_ids:
        return {"ok": False, "error": "note_not_found"}
    revocation = revocations.get(note.note_id)
    if (
        revocation is None
        and note.note_id
        in derivation_resolution.quarantined_note_ids
    ):
        reason = derivation_resolution.reasons.get(
            note.note_id
        )
        revocation = {
            "noteId": note.note_id,
            "state": "quarantined",
            "directSourceIds": list(
                _as_list(note.metadata.get("derived_from"))
            ),
            "revokedSourceIds": list(
                reason.revoked_source_ids
                if reason is not None
                else ()
            ),
            "blockedSourceIds": list(
                reason.blocked_source_ids
                if reason is not None
                else ()
            ),
            "remainingSourceIds": list(
                reason.remaining_source_ids
                if reason is not None
                else ()
            ),
            "quarantinedAt": _utc_now_iso(),
        }
    hidden = bool(note_state.get("hidden")) or note.status in {"archived", "superseded"}
    return {
        "ok": True,
        "card": _memory_vault_user_card(
            path,
            note,
            raw,
            note_state,
            hidden=hidden,
            rel_path=rel_path,
            revocation=revocation,
        ),
        "checkedAt": _utc_now_iso(),
    }


def update_memory_vault_user_note(
    note_id_or_rel_path: str,
    action: str,
    *,
    title: str | None = None,
    body: str | None = None,
    expected_content_hash: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    sync_memory_vault_index(root=root)
    target = _memory_vault_find_note(note_id_or_rel_path, root=root)
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    path, note, raw = target
    state = _read_user_note_state(root)
    note_state = dict(state.get(note.note_id, {}))
    normalized_action = clean_text(action).lower()
    now = _utc_now_iso()
    if normalized_action == "confirm":
        if note.note_id in _memory_quarantined_note_ids(root):
            return {
                "ok": False,
                "error": "memory_note_quarantined",
            }
        note_state["confirmed_at"] = now
    elif normalized_action == "unconfirm":
        note_state.pop("confirmed_at", None)
    elif normalized_action == "pin":
        note_state["pinned"] = True
    elif normalized_action == "unpin":
        note_state["pinned"] = False
    elif normalized_action == "hide":
        note_state["hidden"] = True
        note_state["hidden_at"] = now
    elif normalized_action == "unhide":
        note_state["hidden"] = False
    elif normalized_action == "edit":
        vault = ensure_memory_vault_layout(root)
        rel_path = path.relative_to(vault).as_posix()
        if _is_legacy_memory_note_type(note.note_type, rel_path):
            return {"ok": False, "error": "locked_legacy_note"}
        expected_hash = clean_text(expected_content_hash)
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            return {
                "ok": False,
                "error": "memory_edit_content_hash_required",
            }
        if title is not None and not isinstance(title, str):
            return {"ok": False, "error": "memory_edit_invalid_title"}
        if body is not None and not isinstance(body, str):
            return {"ok": False, "error": "memory_edit_invalid_body"}
        next_title = clean_text(title or note.title)
        next_body = _normalize_memory_edit_body(
            body
            if body is not None
            else _memory_vault_edit_body(note, raw)
        )
        if not next_title or len(next_title) > MEMORY_EDIT_MAX_TITLE_CHARS:
            return {"ok": False, "error": "memory_edit_invalid_title"}
        if len(next_body) > MEMORY_EDIT_MAX_BODY_CHARS:
            return {"ok": False, "error": "memory_edit_body_too_large"}
        with _memory_edit_lock:
            current = _memory_vault_find_note(
                note_id_or_rel_path,
                root=root,
            )
            if current is None:
                return {"ok": False, "error": "note_not_found"}
            path, note, raw = current
            state = _read_user_note_state(root)
            note_state = dict(state.get(note.note_id, {}))
            if not secrets.compare_digest(
                note.source_hash,
                expected_hash,
            ):
                return {
                    "ok": False,
                    "error": "memory_note_changed_since_read",
                    "noteId": note.note_id,
                    "contentHash": note.source_hash,
                }
            previous_content_hash = note.source_hash
            try:
                updated_note = _write_memory_vault_note_body(
                    path,
                    raw,
                    note,
                    title=next_title,
                    body=next_body,
                )
            except OSError:
                return {
                    "ok": False,
                    "schema": MEMORY_EDIT_RESULT_SCHEMA,
                    "action": "edit",
                    "noteId": note.note_id,
                    "edited": False,
                    "error": "memory_edit_failed",
                }
        note_state["edited_at"] = now
    else:
        return {"ok": False, "error": "unsupported_action"}
    note_state["updated_at"] = now
    state[note.note_id] = note_state
    cleanup_errors: list[str] = []
    try:
        _write_user_note_state(state, root)
    except OSError:
        if normalized_action != "edit":
            raise
        cleanup_errors.append(
            "memory_edit_user_state_cleanup_failed"
        )
    if normalized_action == "edit":
        try:
            sync_memory_vault_index(root=root)
        except Exception:
            cleanup_errors.append(
                "memory_edit_index_cleanup_failed"
            )
        try:
            refresh_memory_hot_context(root=root)
        except Exception:
            cleanup_errors.append(
                "memory_edit_hot_context_cleanup_failed"
            )
        else:
            if "memory_edit_index_cleanup_failed" in cleanup_errors:
                cleanup_errors.remove(
                    "memory_edit_index_cleanup_failed"
                )
        provenance = _memory_note_provenance(
            updated_note,
            rel_path=path.relative_to(
                ensure_memory_vault_layout(root)
            ).as_posix(),
            note_state=note_state,
        )
        if cleanup_errors:
            return {
                "ok": False,
                "schema": MEMORY_EDIT_RESULT_SCHEMA,
                "action": "edit",
                "noteId": note.note_id,
                "edited": True,
                "previousContentHash": previous_content_hash,
                "contentHash": updated_note.source_hash,
                "provenance": provenance,
                "error": "memory_edit_cleanup_required",
                "cleanupErrors": cleanup_errors,
            }
        return {
            "ok": True,
            "schema": MEMORY_EDIT_RESULT_SCHEMA,
            "action": "edit",
            "noteId": note.note_id,
            "edited": True,
            "previousContentHash": previous_content_hash,
            "contentHash": updated_note.source_hash,
            "provenance": provenance,
            "state": note_state,
        }
    return {
        "ok": True,
        "noteId": note.note_id,
        "action": normalized_action,
        "state": note_state,
    }


def _memory_deletion_tombstones_path(root: Path | None = None) -> Path:
    return memory_index_dir(root) / DELETION_TOMBSTONES_NAME


def _memory_deletion_reason(value: str) -> str:
    normalized = clean_text(value).lower().replace("-", "_").replace(" ", "_")
    return (
        normalized
        if normalized
        in {
            "user_requested",
            "incorrect_memory",
            "privacy_request",
            "obsolete_memory",
            "test_cleanup",
        }
        else "user_requested"
    )


def _read_memory_deletion_tombstones(
    root: Path | None = None,
) -> list[dict[str, Any]]:
    path = _memory_deletion_tombstones_path(root)
    with _memory_delete_lock:
        if not path.exists():
            return []
        lines = path.read_text(
            encoding="utf-8",
            errors="ignore",
        ).splitlines()
    output: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("schema") == MEMORY_DELETE_TOMBSTONE_SCHEMA
        ):
            output.append(payload)
    return output


def _memory_deleted_note_ids(
    root: Path | None = None,
) -> set[str]:
    return {
        note_id
        for item in _read_memory_deletion_tombstones(root)
        if (
            note_id := clean_text(
                str(item.get("noteId") or "")
            )
        )
    }


def _memory_deletion_journal_state(
    root: Path | None = None,
) -> tuple[int, int]:
    try:
        journal_stat = _memory_deletion_tombstones_path(root).stat()
    except FileNotFoundError:
        return (0, 0)
    except OSError:
        return (-1, -1)
    return (journal_stat.st_mtime_ns, journal_stat.st_size)


def _memory_derivation_revocations_path(
    root: Path | None = None,
) -> Path:
    return memory_index_dir(root) / DERIVATION_REVOCATIONS_NAME


def _memory_provenance_audit_path(
    root: Path | None = None,
) -> Path:
    return memory_index_dir(root) / PROVENANCE_AUDIT_NAME


def _memory_provenance_forward_rejections_path(
    root: Path | None = None,
) -> Path:
    return (
        memory_index_dir(root)
        / PROVENANCE_FORWARD_REJECTIONS_NAME
    )


def _read_memory_provenance_forward_rejections(
    root: Path | None = None,
) -> dict[str, Any]:
    path = _memory_provenance_forward_rejections_path(root)
    if not path.exists():
        return {
            "schema": (
                MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA
            ),
            "contentFree": True,
            "count": 0,
            "byNoteType": {},
            "firstRejectedAt": "",
            "lastRejectedAt": "",
        }
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {
            "schema": (
                MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA
            ),
            "contentFree": True,
            "count": 0,
            "byNoteType": {},
            "firstRejectedAt": "",
            "lastRejectedAt": "",
        }
    if (
        not isinstance(payload, dict)
        or payload.get("schema")
        != MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA
    ):
        return {
            "schema": (
                MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA
            ),
            "contentFree": True,
            "count": 0,
            "byNoteType": {},
            "firstRejectedAt": "",
            "lastRejectedAt": "",
        }
    by_note_type = (
        payload.get("byNoteType")
        if isinstance(payload.get("byNoteType"), dict)
        else {}
    )

    def non_negative_int(value: object) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    return {
        "schema": (
            MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA
        ),
        "contentFree": True,
        "count": non_negative_int(payload.get("count")),
        "byNoteType": {
            clean_text(str(key)).lower() or "unknown": (
                non_negative_int(value)
            )
            for key, value in by_note_type.items()
        },
        "firstRejectedAt": clean_text(
            str(payload.get("firstRejectedAt") or "")
        ),
        "lastRejectedAt": clean_text(
            str(payload.get("lastRejectedAt") or "")
        ),
    }


def _record_memory_provenance_forward_rejection(
    note_type: str,
    *,
    root: Path | None = None,
) -> None:
    normalized_type = (
        clean_text(note_type).lower() or "unknown"
    )
    rejected_at = _utc_now_iso()
    with _memory_provenance_observability_lock:
        payload = (
            _read_memory_provenance_forward_rejections(
                root
            )
        )
        by_note_type = dict(
            payload.get("byNoteType") or {}
        )
        by_note_type[normalized_type] = (
            max(
                0,
                int(by_note_type.get(normalized_type) or 0),
            )
            + 1
        )
        atomic_json_write(
            _memory_provenance_forward_rejections_path(
                root
            ),
            {
                "schema": (
                    MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA
                ),
                "contentFree": True,
                "count": (
                    max(
                        0,
                        int(payload.get("count") or 0),
                    )
                    + 1
                ),
                "byNoteType": {
                    key: by_note_type[key]
                    for key in sorted(by_note_type)
                },
                "firstRejectedAt": (
                    payload.get("firstRejectedAt")
                    or rejected_at
                ),
                "lastRejectedAt": rejected_at,
            },
            durable=True,
        )


def _parse_memory_utc_timestamp(
    value: object,
) -> datetime | None:
    cleaned = clean_text(str(value or ""))
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(
            cleaned.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def memory_quarantine_status(
    *,
    root: Path | None = None,
    entries: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_entries = (
        dict(entries)
        if entries is not None
        else _read_memory_derivation_revocations(root)
    )
    checked_time = now or datetime.now(timezone.utc)
    if checked_time.tzinfo is None:
        checked_time = checked_time.replace(tzinfo=timezone.utc)
    checked_time = checked_time.astimezone(timezone.utc)

    timestamps: list[datetime] = []
    unknown_age_count = 0
    recomposition_ready_count = 0
    blocked_count = 0
    for entry in current_entries.values():
        quarantined_at = _parse_memory_utc_timestamp(
            entry.get("quarantinedAt")
        )
        if quarantined_at is None:
            unknown_age_count += 1
        else:
            timestamps.append(quarantined_at)
        remaining = set(
            _as_list(entry.get("remainingSourceIds"))
        )
        blocked = set(
            _as_list(entry.get("blockedSourceIds"))
        )
        if remaining and not blocked:
            recomposition_ready_count += 1
        else:
            blocked_count += 1

    oldest = min(timestamps) if timestamps else None
    oldest_age_seconds = (
        max(
            0,
            int((checked_time - oldest).total_seconds()),
        )
        if oldest is not None
        else None
    )
    return {
        "schema": MEMORY_QUARANTINE_STATUS_SCHEMA,
        "state": "pending" if current_entries else "clear",
        "count": len(current_entries),
        "recompositionReadyCount": recomposition_ready_count,
        "blockedCount": blocked_count,
        "oldestQuarantinedAt": (
            oldest.isoformat().replace("+00:00", "Z")
            if oldest is not None
            else ""
        ),
        "oldestAgeSeconds": oldest_age_seconds,
        "unknownAgeCount": unknown_age_count,
        "checkedAt": checked_time.isoformat().replace(
            "+00:00",
            "Z",
        ),
    }


def _memory_derivation_revocation_file_state(
    root: Path | None = None,
) -> tuple[int, int]:
    try:
        state = _memory_derivation_revocations_path(root).stat()
    except FileNotFoundError:
        return (0, 0)
    except OSError:
        return (-1, -1)
    return (state.st_mtime_ns, state.st_size)


def _read_memory_derivation_revocations(
    root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    path = _memory_derivation_revocations_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "memory_derivation_revocations_corrupt"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema")
        != MEMORY_DERIVATION_REVOCATIONS_SCHEMA
        or not isinstance(payload.get("entries"), dict)
    ):
        raise RuntimeError(
            "memory_derivation_revocations_corrupt"
        )
    output: dict[str, dict[str, Any]] = {}
    for note_id, entry in payload["entries"].items():
        cleaned_id = clean_text(str(note_id))
        if (
            cleaned_id
            and isinstance(entry, dict)
            and entry.get("state") == "quarantined"
        ):
            output[cleaned_id] = dict(entry)
    return output


def _write_memory_derivation_revocations(
    entries: dict[str, dict[str, Any]],
    *,
    root: Path | None = None,
) -> None:
    path = _memory_derivation_revocations_path(root)
    if not entries and not path.exists():
        return
    atomic_json_write(
        path,
        {
            "schema": MEMORY_DERIVATION_REVOCATIONS_SCHEMA,
            "updatedAt": _utc_now_iso(),
            "entries": {
                note_id: entries[note_id]
                for note_id in sorted(entries)
            },
        },
        durable=True,
    )


def _memory_derivation_nodes(
    *,
    root: Path | None = None,
) -> tuple[
    dict[str, DerivationNode],
    dict[str, tuple[Path, MemoryVaultNote]],
]:
    vault = ensure_memory_vault_layout(root)
    deleted_note_ids = _memory_deleted_note_ids(root)
    nodes: dict[str, DerivationNode] = {}
    note_sources: dict[str, tuple[Path, MemoryVaultNote]] = {}
    for path in sorted(vault.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            note = parse_memory_note(path)
        except Exception:
            continue
        if note.note_id in deleted_note_ids:
            continue
        dependencies = tuple(
            _as_list(note.metadata.get("derived_from"))
        )
        nodes[note.note_id] = DerivationNode(
            note_id=note.note_id,
            title=note.title,
            note_type=note.note_type,
            source_hash=note.source_hash,
            derived_from=dependencies,
        )
        note_sources[note.note_id] = (path, note)
    return nodes, note_sources


def _memory_audit_reference_aliases(
    vault: Path,
    path: Path,
    note: MemoryVaultNote,
) -> tuple[str, ...]:
    rel_path = path.relative_to(vault).as_posix()
    without_suffix = (
        rel_path[:-3]
        if rel_path.lower().endswith(".md")
        else rel_path
    )
    return tuple(
        dict.fromkeys(
            (
                note.note_id,
                rel_path,
                without_suffix,
                f"{VAULT_DIR_NAME}/{rel_path}",
                f"{VAULT_DIR_NAME}/{without_suffix}",
            )
        )
    )


def _memory_audit_evidence_aliases(
    note: MemoryVaultNote,
) -> tuple[str, ...]:
    body_bytes = clean_text(note.body).encode(
        "utf-8",
        errors="ignore",
    )
    sha1_body = hashlib.sha1(body_bytes).hexdigest()
    return tuple(
        dict.fromkeys(
            (
                note.source_hash,
                sha1_body,
                sha1_body[:12],
                hashlib.sha256(body_bytes).hexdigest(),
            )
        )
    )


def _memory_provenance_audit_nodes(
    *,
    root: Path | None = None,
) -> tuple[
    list[ProvenanceAuditNode],
    dict[str, tuple[Path, MemoryVaultNote]],
]:
    vault = ensure_memory_vault_layout(root)
    _nodes, note_sources = _memory_derivation_nodes(root=root)
    output: list[ProvenanceAuditNode] = []
    for note_id in sorted(note_sources):
        path, note = note_sources[note_id]
        source = clean_text(
            str(note.metadata.get("source") or "")
        )
        origin_derived_from = tuple(
            _as_list(note.metadata.get("origin_derived_from"))
        )
        revocation_resolution = clean_text(
            str(
                note.metadata.get(
                    "revocation_resolution"
                )
                or ""
            )
        )
        explicitly_detached = bool(
            not _as_list(note.metadata.get("derived_from"))
            and (
                origin_derived_from
                or revocation_resolution == "user-edit"
                or _memory_source_type(
                    source,
                    note.note_type,
                )
                == "user"
            )
        )
        output.append(
            ProvenanceAuditNode(
                note_id=note.note_id,
                note_type=note.note_type,
                source_type=_memory_source_type(
                    source,
                    note.note_type,
                ),
                source_refs=tuple(
                    _as_list(
                        note.metadata.get("source_refs")
                        or note.metadata.get("source_ref")
                    )
                ),
                derived_from=tuple(
                    _as_list(
                        note.metadata.get("derived_from")
                    )
                ),
                origin_derived_from=origin_derived_from,
                evidence_hashes=tuple(
                    _as_list(
                        note.metadata.get("evidence_hashes")
                        or note.metadata.get("source_hash")
                    )
                ),
                reference_aliases=(
                    _memory_audit_reference_aliases(
                        vault,
                        path,
                        note,
                    )
                ),
                evidence_aliases=(
                    _memory_audit_evidence_aliases(note)
                ),
                explicitly_detached=explicitly_detached,
                updated_at=clean_text(
                    str(
                        note.metadata.get("updated_at")
                        or note.metadata.get("created_at")
                        or ""
                    )
                ),
            )
        )
    return output, note_sources


def _memory_provenance_audit_fingerprint(
    nodes: list[ProvenanceAuditNode],
) -> str:
    payload = [
        {
            "id": node.note_id,
            "type": node.note_type,
            "sourceType": node.source_type,
            "sourceRefs": list(node.source_refs),
            "derivedFrom": list(node.derived_from),
            "originDerivedFrom": list(
                node.origin_derived_from
            ),
            "evidenceHashes": list(node.evidence_hashes),
            "referenceAliases": list(
                node.reference_aliases
            ),
            "evidenceAliases": list(node.evidence_aliases),
            "explicitlyDetached": node.explicitly_detached,
        }
        for node in sorted(nodes, key=lambda item: item.note_id)
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _memory_provenance_audit_summary(
    audit: ProvenanceAuditResult,
) -> dict[str, int]:
    return {
        "auditedNoteCount": audit.audited_note_count,
        "declaredDerivationCount": (
            audit.declared_derivation_count
        ),
        "explicitlyDetachedCount": (
            audit.explicitly_detached_count
        ),
        "auditableMissingCount": (
            audit.auditable_missing_count
        ),
        "candidateTargetCount": len(audit.candidates),
        "verifiedCount": audit.verified_count,
        "reviewCount": audit.review_count,
        "ambiguousCount": audit.ambiguous_count,
        "unmatchedTargetCount": (
            audit.unmatched_target_count
        ),
        "missingSignalTargetCount": len(
            audit.missing_signal_target_ids
        ),
        "manualReviewTargetCount": (
            len(audit.missing_signal_target_ids)
            + len(audit.unmatched_target_ids)
        ),
        "cycleRejectedSignalCount": (
            audit.cycle_rejected_signal_count
        ),
    }


def _memory_persisted_provenance_audit(
    *,
    root: Path | None,
    graph_fingerprint: str,
    audit: ProvenanceAuditResult,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    path = _memory_provenance_audit_path(root)
    entries = [
        {
            "targetNoteId": candidate.target_note_id,
            "state": candidate.state,
            "candidateSourceIds": list(
                candidate.candidate_source_ids
            ),
            "signals": [
                {
                    "sourceNoteId": signal.source_note_id,
                    "reasonCodes": list(
                        signal.reason_codes
                    ),
                }
                for signal in candidate.signals
            ],
            "reasonCodes": list(candidate.reason_codes),
        }
        for candidate in audit.candidates
    ]
    stable_payload = {
        "schema": MEMORY_PROVENANCE_BACKFILL_AUDIT_SCHEMA,
        "readOnly": True,
        "autoApply": False,
        "contentSimilarityUsed": False,
        "graphFingerprint": graph_fingerprint,
        "summary": _memory_provenance_audit_summary(
            audit
        ),
        "coverage": {
            key: value
            for key, value in coverage.items()
            if key != "checkedAt"
        },
        "entries": entries,
    }
    generated_at = ""
    try:
        existing = json.loads(
            path.read_text(encoding="utf-8")
        )
        if (
            isinstance(existing, dict)
            and {
                key: value
                for key, value in existing.items()
                if key != "generatedAt"
            }
            == stable_payload
        ):
            generated_at = clean_text(
                str(existing.get("generatedAt") or "")
            )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    payload = {
        **stable_payload,
        "generatedAt": generated_at or _utc_now_iso(),
    }
    if not generated_at:
        atomic_json_write(path, payload, durable=True)
    return payload


def _memory_public_audit_note(
    note_id: str,
    note_sources: dict[
        str,
        tuple[Path, MemoryVaultNote],
    ],
    *,
    root: Path | None,
    include_internal: bool,
) -> dict[str, Any] | None:
    source = note_sources.get(note_id)
    if source is None:
        return None
    path, note = source
    vault = ensure_memory_vault_layout(root)
    rel_path = path.relative_to(vault).as_posix()
    if (
        _is_internal_memory_note_type(note.note_type)
        and not include_internal
    ):
        return {
            "id": note.note_id,
            "title": "관리 기억",
            "type": "internal",
            "contentHidden": True,
        }
    locked = _is_legacy_memory_note_type(
        note.note_type,
        rel_path,
    )
    return {
        "id": note.note_id,
        "title": _legacy_to_public_title(
            note.note_type,
            rel_path,
            note.title,
        ),
        "type": note.note_type,
        "contentHidden": locked,
    }


def memory_provenance_backfill_preview(
    *,
    root: Path | None = None,
    include_internal: bool = False,
) -> dict[str, Any]:
    sync_memory_vault_index(root=root)
    nodes, note_sources = _memory_provenance_audit_nodes(
        root=root
    )
    audit = audit_missing_derivations(nodes)
    graph_fingerprint = (
        _memory_provenance_audit_fingerprint(nodes)
    )
    coverage = summarize_provenance_coverage(
        nodes,
        audit=audit,
        forward_write_rejections=(
            _read_memory_provenance_forward_rejections(
                root
            )
        ),
    )
    persisted = _memory_persisted_provenance_audit(
        root=root,
        graph_fingerprint=graph_fingerprint,
        audit=audit,
        coverage=coverage,
    )
    public_candidates: list[dict[str, Any]] = []
    for candidate in audit.candidates:
        target = _memory_public_audit_note(
            candidate.target_note_id,
            note_sources,
            root=root,
            include_internal=include_internal,
        )
        if target is None:
            continue
        target_source = note_sources.get(
            candidate.target_note_id
        )
        protection = ""
        if target_source is not None:
            target_path, target_note = target_source
            protection = _memory_provenance_target_blocker(
                target_path,
                target_note,
                root=root,
            )
        sources: list[dict[str, Any]] = []
        source_protection = ""
        for signal in candidate.signals:
            source_entry = note_sources.get(
                signal.source_note_id
            )
            if source_entry is not None and not source_protection:
                source_path, source_note = source_entry
                source_protection = (
                    _memory_provenance_source_blocker(
                        source_path,
                        source_note,
                        root=root,
                    )
                )
            public_source = _memory_public_audit_note(
                signal.source_note_id,
                note_sources,
                root=root,
                include_internal=include_internal,
            )
            if public_source is None:
                continue
            sources.append(
                {
                    **public_source,
                    "reasonCodes": list(
                        signal.reason_codes
                    ),
                }
            )
        can_apply = (
            candidate.state in {"verified", "review"}
            and not protection
            and not source_protection
            and not bool(target.get("contentHidden"))
        )
        public_candidates.append(
            {
                "target": target,
                "state": candidate.state,
                "candidateSources": sources,
                "reasonCodes": list(
                    candidate.reason_codes
                ),
                "canApply": can_apply,
                "applyBlocker": (
                    ""
                    if can_apply
                    else (
                        "memory_provenance_backfill_ambiguous"
                        if candidate.state == "ambiguous"
                        else protection
                        or source_protection
                        or "memory_provenance_backfill_protected"
                    )
                ),
            }
        )
    manual_review_targets: list[dict[str, Any]] = []
    manual_target_reasons = {
        **{
            note_id: "missing_explicit_signal"
            for note_id in audit.missing_signal_target_ids
        },
        **{
            note_id: "unmatched_explicit_metadata"
            for note_id in audit.unmatched_target_ids
        },
    }
    for target_note_id in sorted(manual_target_reasons):
        target = _memory_public_audit_note(
            target_note_id,
            note_sources,
            root=root,
            include_internal=include_internal,
        )
        target_source = note_sources.get(target_note_id)
        if target is None or target_source is None:
            continue
        target_path, target_note = target_source
        protection = _memory_provenance_target_blocker(
            target_path,
            target_note,
            root=root,
        )
        can_select_sources = bool(
            not protection
            and not bool(target.get("contentHidden"))
        )
        manual_review_targets.append(
            {
                "target": target,
                "reason": manual_target_reasons[
                    target_note_id
                ],
                "canSelectSources": can_select_sources,
                "selectionBlocker": (
                    ""
                    if can_select_sources
                    else protection
                    or "memory_provenance_backfill_protected"
                ),
            }
        )
    return {
        "ok": True,
        "schema": MEMORY_PROVENANCE_BACKFILL_AUDIT_SCHEMA,
        "readOnly": True,
        "autoApply": False,
        "contentSimilarityUsed": False,
        "policy": "exact_metadata_only",
        "graphFingerprint": graph_fingerprint,
        "summary": dict(persisted["summary"]),
        "coverage": coverage,
        "candidates": public_candidates,
        "manualReviewTargets": manual_review_targets,
        "manualSelectionPolicy": (
            "user_selected_source_note_ids_only"
        ),
        "reportPath": str(
            _memory_provenance_audit_path(root)
        ),
        "generatedAt": persisted["generatedAt"],
        "checkedAt": _utc_now_iso(),
    }


def memory_provenance_manual_source_options(
    note_id_or_rel_path: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    sync_memory_vault_index(root=root)
    target = _memory_vault_find_note(
        note_id_or_rel_path,
        root=root,
    )
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    target_path, target_note, _target_raw = target
    target_blocker = _memory_provenance_target_blocker(
        target_path,
        target_note,
        root=root,
    )
    if target_blocker == "internal_note_not_public":
        return {"ok": False, "error": "note_not_found"}
    if target_blocker:
        return {
            "ok": False,
            "error": "memory_provenance_backfill_protected",
            "reason": target_blocker,
        }

    nodes, note_sources = _memory_provenance_audit_nodes(
        root=root
    )
    audit = audit_missing_derivations(nodes)
    target_reason = _memory_provenance_manual_target_reason(
        target_note.note_id,
        audit,
    )
    if target_reason == "ambiguous_exact_metadata":
        return {
            "ok": False,
            "error": "memory_provenance_backfill_ambiguous",
        }
    if target_reason == "exact_candidate_available":
        return {
            "ok": False,
            "error": (
                "memory_provenance_manual_exact_candidate_available"
            ),
        }
    if target_reason not in {
        "missing_explicit_signal",
        "unmatched_explicit_metadata",
    }:
        return {
            "ok": False,
            "error": (
                "memory_provenance_manual_target_ineligible"
            ),
        }

    node_by_id = {
        node.note_id: node
        for node in nodes
    }
    source_options: list[dict[str, Any]] = []
    for source_id in sorted(note_sources):
        if source_id == target_note.note_id:
            continue
        source_path, source_note = note_sources[source_id]
        source_node = node_by_id.get(source_id)
        if (
            source_node is None
            or not _memory_provenance_source_is_grounded(
                source_node
            )
            or _memory_provenance_source_blocker(
                source_path,
                source_note,
                root=root,
            )
            or _memory_provenance_depends_on(
                node_by_id,
                source_id,
                target_note.note_id,
            )
        ):
            continue
        public_source = _memory_public_audit_note(
            source_id,
            note_sources,
            root=root,
            include_internal=False,
        )
        if (
            public_source is None
            or bool(public_source.get("contentHidden"))
        ):
            continue
        source_options.append(
            {
                **public_source,
                "sourceType": source_node.source_type,
            }
        )

    target_public = _memory_public_audit_note(
        target_note.note_id,
        note_sources,
        root=root,
        include_internal=False,
    )
    return {
        "ok": True,
        "schema": MEMORY_PROVENANCE_MANUAL_OPTIONS_SCHEMA,
        "readOnly": True,
        "autoApply": False,
        "contentSimilarityUsed": False,
        "selectionMode": "user_selected",
        "target": target_public,
        "reason": target_reason,
        "sourceOptions": source_options,
        "sourceOptionCount": len(source_options),
        "graphFingerprint": (
            _memory_provenance_audit_fingerprint(nodes)
        ),
        "checkedAt": _utc_now_iso(),
    }


def _memory_revocation_entry_resolved(
    note: MemoryVaultNote,
    entry: dict[str, Any],
) -> bool:
    resolved_at = clean_text(
        str(note.metadata.get("revocation_resolved_at") or "")
    )
    quarantined_at = clean_text(
        str(entry.get("quarantinedAt") or "")
    )
    if not resolved_at:
        return False
    try:
        resolved_time = datetime.fromisoformat(
            resolved_at.replace("Z", "+00:00")
        ).timestamp()
        quarantined_time = datetime.fromisoformat(
            quarantined_at.replace("Z", "+00:00")
        ).timestamp()
    except (TypeError, ValueError):
        return False
    return resolved_time >= quarantined_time


def _active_memory_quarantine_seeds(
    entries: dict[str, dict[str, Any]],
    note_sources: dict[
        str,
        tuple[Path, MemoryVaultNote],
    ],
) -> set[str]:
    seeds: set[str] = set()
    for note_id, entry in entries.items():
        source = note_sources.get(note_id)
        if source is None:
            continue
        _path, note = source
        if not _memory_revocation_entry_resolved(note, entry):
            seeds.add(note_id)
    return seeds


def _resolve_current_memory_derivations(
    *,
    root: Path | None = None,
    additional_deleted_ids: set[str] | None = None,
) -> tuple[
    DerivationResolution,
    dict[str, DerivationNode],
    dict[str, tuple[Path, MemoryVaultNote]],
    dict[str, dict[str, Any]],
]:
    nodes, note_sources = _memory_derivation_nodes(root=root)
    entries = _read_memory_derivation_revocations(root)
    seeds = _active_memory_quarantine_seeds(
        entries,
        note_sources,
    )
    deleted_note_ids = _memory_deleted_note_ids(root)
    deleted_note_ids.update(additional_deleted_ids or set())
    resolution = resolve_derivation_states(
        nodes,
        deleted_note_ids=deleted_note_ids,
        seeded_quarantine_ids=seeds,
    )
    return resolution, nodes, note_sources, entries


def _memory_derivation_public_source(
    source_id: str,
    nodes: dict[str, DerivationNode],
) -> dict[str, Any]:
    node = nodes.get(source_id)
    return {
        "id": source_id,
        "title": node.title if node is not None else source_id,
        "type": node.note_type if node is not None else "unknown",
    }


def _memory_derivation_deletion_impact(
    trigger_note_id: str,
    *,
    root: Path | None = None,
) -> tuple[dict[str, Any], str]:
    baseline, nodes, note_sources, entries = (
        _resolve_current_memory_derivations(root=root)
    )
    candidate = resolve_derivation_states(
        nodes,
        deleted_note_ids=(
            set(baseline.deleted_note_ids)
            | {clean_text(trigger_note_id)}
        ),
        seeded_quarantine_ids=_active_memory_quarantine_seeds(
            entries,
            note_sources,
        ),
    )
    trigger_id = clean_text(trigger_note_id)
    cascade_ids = sorted(
        (
            set(candidate.deleted_note_ids)
            - set(baseline.deleted_note_ids)
        )
        - {trigger_id}
    )
    quarantine_ids = sorted(
        set(changed_quarantine_ids(baseline, candidate))
        - set(candidate.deleted_note_ids)
        - {trigger_id}
    )

    cascade: list[dict[str, Any]] = []
    for note_id in cascade_ids:
        node = nodes.get(note_id)
        if node is None:
            continue
        revoked = sorted(
            set(node.derived_from)
            & set(candidate.deleted_note_ids)
        )
        cascade.append(
            {
                "id": note_id,
                "title": node.title,
                "type": node.note_type,
                "revokedSourceIds": revoked,
            }
        )

    quarantine: list[dict[str, Any]] = []
    for note_id in quarantine_ids:
        node = nodes.get(note_id)
        reason = candidate.reasons.get(note_id)
        if node is None or reason is None:
            continue
        quarantine.append(
            {
                "id": note_id,
                "title": node.title,
                "type": node.note_type,
                "revokedSourceIds": list(
                    reason.revoked_source_ids
                ),
                "blockedSourceIds": list(
                    reason.blocked_source_ids
                ),
                "remainingSources": [
                    _memory_derivation_public_source(
                        source_id,
                        nodes,
                    )
                    for source_id in reason.remaining_source_ids
                ],
            }
        )

    impact = {
        "schema": MEMORY_DERIVATION_IMPACT_SCHEMA,
        "triggerNoteId": trigger_id,
        "affectedCount": len(cascade) + len(quarantine),
        "cascadeDeleteCount": len(cascade),
        "quarantineCount": len(quarantine),
        "cascadeDelete": cascade,
        "quarantine": quarantine,
        "recompositionQueued": bool(quarantine),
    }
    fingerprint_payload = {
        "triggerNoteId": trigger_id,
        "triggerContentHash": (
            nodes[trigger_id].source_hash
            if trigger_id in nodes
            else ""
        ),
        "baselineDeleted": sorted(
            baseline.deleted_note_ids
        ),
        "baselineQuarantined": sorted(
            baseline.quarantined_note_ids
        ),
        "impact": impact,
        "affectedSourceHashes": {
            note_id: nodes[note_id].source_hash
            for note_id in sorted(
                set(cascade_ids) | set(quarantine_ids)
            )
            if note_id in nodes
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return impact, fingerprint


def _reconcile_memory_derivation_revocations(
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    resolution, nodes, note_sources, prior_entries = (
        _resolve_current_memory_derivations(root=root)
    )
    deleted_before = _memory_deleted_note_ids(root)
    cascade_ids = sorted(
        set(resolution.deleted_note_ids) - deleted_before
    )
    now_iso = _utc_now_iso()
    for note_id in cascade_ids:
        node = nodes.get(note_id)
        if node is None:
            continue
        reason = {
            "schema": MEMORY_DELETE_TOMBSTONE_SCHEMA,
            "noteId": note_id,
            "noteType": node.note_type,
            "sourceType": "derived",
            "reason": "source_revoked",
            "deletedAt": now_iso,
            "revokedByNoteIds": sorted(
                set(node.derived_from)
                & set(resolution.deleted_note_ids)
            )[:12],
        }
        _append_memory_deletion_tombstone(
            reason,
            root=root,
        )

    if cascade_ids:
        _reconcile_memory_deletion_tombstones(root=root)

    entries: dict[str, dict[str, Any]] = {}
    for note_id in sorted(
        set(resolution.quarantined_note_ids)
        - set(resolution.deleted_note_ids)
    ):
        node = nodes.get(note_id)
        reason = resolution.reasons.get(note_id)
        if node is None or reason is None:
            continue
        previous = prior_entries.get(note_id, {})
        entries[note_id] = {
            "noteId": note_id,
            "state": "quarantined",
            "directSourceIds": list(node.derived_from),
            "revokedSourceIds": list(
                reason.revoked_source_ids
            ),
            "blockedSourceIds": list(
                reason.blocked_source_ids
            ),
            "remainingSourceIds": list(
                reason.remaining_source_ids
            ),
            "quarantinedAt": clean_text(
                str(previous.get("quarantinedAt") or "")
            )
            or now_iso,
        }
    if entries != prior_entries:
        _write_memory_derivation_revocations(
            entries,
            root=root,
        )
        _remove_memory_hot_context_files(root=root)
    return {
        "cascadeDeletedNoteIds": cascade_ids,
        "quarantinedNoteIds": sorted(entries),
        "cascadeDeletedCount": len(cascade_ids),
        "quarantinedCount": len(entries),
    }


def _memory_quarantined_note_ids(
    root: Path | None = None,
) -> set[str]:
    return set(_read_memory_derivation_revocations(root))


def _remove_memory_hot_context_files(
    *,
    root: Path | None = None,
) -> None:
    index_dir = memory_index_dir(root)
    for path in (
        index_dir / "hot_context.json",
        index_dir / "prompt_blocks" / "core_prompt.txt",
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _invalidate_stale_memory_hot_context(
    *,
    root: Path | None = None,
) -> None:
    index_dir = memory_index_dir(root)
    hot_path = index_dir / "hot_context.json"
    cache_state: tuple[int, int, int, int] | None = None
    if hot_path.exists():
        try:
            payload = json.loads(
                hot_path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
            )
            if isinstance(payload, dict):
                cache_state = (
                    int(
                        payload.get(
                            "deletion_journal_mtime_ns"
                        )
                        or 0
                    ),
                    int(
                        payload.get(
                            "deletion_journal_size"
                        )
                        or 0
                    ),
                    int(
                        payload.get(
                            "derivation_revocations_mtime_ns"
                        )
                        or 0
                    ),
                    int(
                        payload.get(
                            "derivation_revocations_size"
                        )
                        or 0
                    ),
                )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            cache_state = None
    if cache_state == (
        *_memory_deletion_journal_state(root),
        *_memory_derivation_revocation_file_state(root),
    ):
        return
    _remove_memory_hot_context_files(root=root)


def _reconcile_memory_deletion_tombstones(
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    deleted_note_ids = _memory_deleted_note_ids(root)
    if not deleted_note_ids:
        return {
            "deletedNoteCount": 0,
            "sourceFileCount": 0,
            "cleanupErrorCount": 0,
        }
    vault = ensure_memory_vault_layout(root)
    source_file_count = 0
    cleanup_error_count = 0
    for path in vault.rglob("*.md"):
        try:
            note = parse_memory_note(path)
        except Exception:
            continue
        if note.note_id not in deleted_note_ids:
            continue
        try:
            path.unlink()
            source_file_count += 1
        except FileNotFoundError:
            pass
        except OSError:
            cleanup_error_count += 1
    state = _read_user_note_state(root)
    next_state = {
        note_id: payload
        for note_id, payload in state.items()
        if note_id not in deleted_note_ids
    }
    if next_state != state:
        try:
            _write_user_note_state(next_state, root)
        except OSError:
            cleanup_error_count += 1
    return {
        "deletedNoteCount": len(deleted_note_ids),
        "sourceFileCount": source_file_count,
        "cleanupErrorCount": cleanup_error_count,
    }


def _append_memory_deletion_tombstone(
    payload: dict[str, Any],
    *,
    root: Path | None = None,
) -> None:
    path = _memory_deletion_tombstones_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def _memory_note_deletion_protection(
    note: MemoryVaultNote,
    rel_path: str,
) -> str:
    if _is_internal_memory_note_type(note.note_type):
        return "internal_note_not_public"
    if _is_legacy_memory_note_type(note.note_type, rel_path):
        return "legacy_source_managed"
    if clean_text(str(note.metadata.get("source") or "")) == BOOTSTRAP_NOTE_SOURCE:
        return "bootstrap_contract_note"
    return ""


def _memory_provenance_target_blocker(
    path: Path,
    note: MemoryVaultNote,
    *,
    root: Path | None = None,
) -> str:
    vault = ensure_memory_vault_layout(root)
    rel_path = path.relative_to(vault).as_posix()
    protection = _memory_note_deletion_protection(
        note,
        rel_path,
    )
    if protection:
        return protection
    note_state = _read_user_note_state(root).get(
        note.note_id,
        {},
    )
    if bool(note_state.get("hidden")):
        return "memory_note_hidden"
    if note.note_id in _memory_quarantined_note_ids(root):
        return "memory_note_quarantined"
    return ""


def _memory_provenance_source_blocker(
    path: Path,
    note: MemoryVaultNote,
    *,
    root: Path | None = None,
) -> str:
    vault = ensure_memory_vault_layout(root)
    rel_path = path.relative_to(vault).as_posix()
    if _is_internal_memory_note_type(note.note_type):
        return "memory_provenance_source_not_public"
    if _is_legacy_memory_note_type(
        note.note_type,
        rel_path,
    ):
        return "memory_provenance_source_not_public"
    note_state = _read_user_note_state(root).get(
        note.note_id,
        {},
    )
    if bool(note_state.get("hidden")):
        return "memory_provenance_source_hidden"
    if note.note_id in _memory_quarantined_note_ids(root):
        return "memory_provenance_source_quarantined"
    return ""


def _memory_provenance_manual_target_reason(
    target_note_id: str,
    audit: ProvenanceAuditResult,
) -> str:
    candidate = next(
        (
            item
            for item in audit.candidates
            if item.target_note_id == target_note_id
        ),
        None,
    )
    if candidate is not None:
        return (
            "ambiguous_exact_metadata"
            if candidate.state == "ambiguous"
            else "exact_candidate_available"
        )
    if target_note_id in set(
        audit.missing_signal_target_ids
    ):
        return "missing_explicit_signal"
    if target_note_id in set(audit.unmatched_target_ids):
        return "unmatched_explicit_metadata"
    return "manual_target_ineligible"


def _memory_provenance_depends_on(
    nodes: dict[str, ProvenanceAuditNode],
    start_note_id: str,
    target_note_id: str,
) -> bool:
    pending = [start_note_id]
    visited: set[str] = set()
    while pending:
        current_id = pending.pop()
        if current_id in visited:
            continue
        visited.add(current_id)
        current = nodes.get(current_id)
        if current is None:
            continue
        for source_id in current.derived_from:
            if source_id == target_note_id:
                return True
            if source_id not in visited:
                pending.append(source_id)
    return False


def _memory_provenance_source_is_grounded(
    node: ProvenanceAuditNode,
) -> bool:
    return bool(
        node.derived_from
        or node.explicitly_detached
        or node.origin_derived_from
        or node.source_type in DIRECT_SOURCE_TYPES
    )


def _memory_provenance_backfill_binding_fingerprint(
    *,
    target_note_id: str,
    target_content_hash: str,
    source_content_hashes: dict[str, str],
    graph_fingerprint: str,
    candidate_state: str,
    selection_mode: str,
    manual_reason: str = "",
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "targetNoteId": target_note_id,
                "targetContentHash": target_content_hash,
                "sourceContentHashes": {
                    note_id: source_content_hashes[note_id]
                    for note_id in sorted(source_content_hashes)
                },
                "graphFingerprint": graph_fingerprint,
                "candidateState": candidate_state,
                "selectionMode": selection_mode,
                "manualReason": manual_reason,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _memory_provenance_backfill_candidate_binding(
    note_id_or_rel_path: str,
    source_note_ids: list[str] | tuple[str, ...],
    *,
    selection_mode: str = "exact_metadata",
    root: Path | None = None,
) -> dict[str, Any]:
    normalized_selection_mode = clean_text(
        selection_mode
    ).lower()
    if normalized_selection_mode not in {
        "exact_metadata",
        "user_selected",
    }:
        return {
            "ok": False,
            "error": (
                "memory_provenance_backfill_selection_mode_invalid"
            ),
        }
    target = _memory_vault_find_note(
        note_id_or_rel_path,
        root=root,
    )
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    target_path, target_note, target_raw = target
    vault = ensure_memory_vault_layout(root)
    rel_path = target_path.relative_to(vault).as_posix()
    protection = _memory_provenance_target_blocker(
        target_path,
        target_note,
        root=root,
    )
    if protection == "internal_note_not_public":
        return {"ok": False, "error": "note_not_found"}
    if protection:
        return {
            "ok": False,
            "error": "memory_provenance_backfill_protected",
            "reason": protection,
        }

    nodes, note_sources = _memory_provenance_audit_nodes(
        root=root
    )
    audit = audit_missing_derivations(nodes)
    candidate = next(
        (
            item
            for item in audit.candidates
            if item.target_note_id == target_note.note_id
        ),
        None,
    )
    if (
        candidate is not None
        and candidate.state == "ambiguous"
    ):
        return {
            "ok": False,
            "error": "memory_provenance_backfill_ambiguous",
        }
    manual_reason = ""
    if normalized_selection_mode == "exact_metadata":
        if candidate is None:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_candidate_unavailable"
                ),
            }
    else:
        if candidate is not None:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_manual_exact_candidate_available"
                ),
            }
        manual_reason = _memory_provenance_manual_target_reason(
            target_note.note_id,
            audit,
        )
        if manual_reason not in {
            "missing_explicit_signal",
            "unmatched_explicit_metadata",
        }:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_manual_target_ineligible"
                ),
            }

    requested_source_ids = sorted(
        dict.fromkeys(
            clean_text(str(item))
            for item in source_note_ids
            if clean_text(str(item))
        )
    )
    if not requested_source_ids:
        return {
            "ok": False,
            "error": (
                "memory_provenance_backfill_source_ids_required"
            ),
        }
    if len(requested_source_ids) > 12:
        return {
            "ok": False,
            "error": (
                "memory_provenance_backfill_source_ids_invalid"
            ),
        }
    if normalized_selection_mode == "exact_metadata":
        candidate_source_ids = sorted(
            candidate.candidate_source_ids
        )
        if requested_source_ids != candidate_source_ids:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_source_mismatch"
                ),
            }

    source_content_hashes: dict[str, str] = {}
    node_by_id = {
        node.note_id: node
        for node in nodes
    }
    for source_id in requested_source_ids:
        source = note_sources.get(source_id)
        if source is None:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_source_unavailable"
                ),
            }
        source_path, source_note = source
        source_blocker = _memory_provenance_source_blocker(
            source_path,
            source_note,
            root=root,
        )
        if source_blocker:
            return {
                "ok": False,
                "error": source_blocker,
            }
        if normalized_selection_mode == "user_selected":
            source_node = node_by_id.get(source_id)
            if (
                source_node is None
                or not _memory_provenance_source_is_grounded(
                    source_node
                )
            ):
                return {
                    "ok": False,
                    "error": (
                        "memory_provenance_manual_source_ungrounded"
                    ),
                }
            if (
                source_id == target_note.note_id
                or _memory_provenance_depends_on(
                    node_by_id,
                    source_id,
                    target_note.note_id,
                )
            ):
                return {
                    "ok": False,
                    "error": (
                        "memory_provenance_manual_cycle"
                    ),
                }
        source_content_hashes[source_id] = (
            source_note.source_hash
        )

    candidate_state = (
        candidate.state
        if candidate is not None
        else "user_selected"
    )
    graph_fingerprint = (
        _memory_provenance_audit_fingerprint(nodes)
    )
    binding_fingerprint = (
        _memory_provenance_backfill_binding_fingerprint(
            target_note_id=target_note.note_id,
            target_content_hash=target_note.source_hash,
            source_content_hashes=source_content_hashes,
            graph_fingerprint=graph_fingerprint,
            candidate_state=candidate_state,
            selection_mode=normalized_selection_mode,
            manual_reason=manual_reason,
        )
    )
    return {
        "ok": True,
        "targetPath": target_path,
        "targetRelPath": rel_path,
        "targetNote": target_note,
        "targetRaw": target_raw,
        "sourceNoteIds": requested_source_ids,
        "sourceContentHashes": source_content_hashes,
        "candidate": candidate,
        "candidateState": candidate_state,
        "selectionMode": normalized_selection_mode,
        "manualReason": manual_reason,
        "graphFingerprint": graph_fingerprint,
        "bindingFingerprint": binding_fingerprint,
        "noteSources": note_sources,
    }


def _prune_memory_provenance_backfill_tokens(
    now: float,
) -> None:
    stale = [
        token
        for token, payload
        in _memory_provenance_backfill_tokens.items()
        if float(payload.get("expiresAt") or 0) < now - 60
    ]
    for token in stale:
        _memory_provenance_backfill_tokens.pop(
            token,
            None,
        )


def preview_memory_provenance_backfill_application(
    note_id_or_rel_path: str,
    source_note_ids: list[str] | tuple[str, ...],
    *,
    selection_mode: str = "exact_metadata",
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    sync_memory_vault_index(root=root)
    binding = _memory_provenance_backfill_candidate_binding(
        note_id_or_rel_path,
        source_note_ids,
        selection_mode=selection_mode,
        root=root,
    )
    if not binding.get("ok"):
        return binding

    target_note = binding["targetNote"]
    candidate = binding["candidate"]
    note_sources = binding["noteSources"]
    timestamp = float(now())
    expires_at = (
        timestamp
        + MEMORY_PROVENANCE_BACKFILL_PREVIEW_TTL_SECONDS
    )
    token = secrets.token_urlsafe(32)
    root_key = str((root or MEMORY_ROOT).resolve())
    with _memory_provenance_backfill_lock:
        _prune_memory_provenance_backfill_tokens(
            timestamp
        )
        _memory_provenance_backfill_tokens[token] = {
            "targetNoteId": target_note.note_id,
            "root": root_key,
            "targetContentHash": target_note.source_hash,
            "sourceNoteIds": list(
                binding["sourceNoteIds"]
            ),
            "sourceContentHashes": dict(
                binding["sourceContentHashes"]
            ),
            "graphFingerprint": binding[
                "graphFingerprint"
            ],
            "bindingFingerprint": binding[
                "bindingFingerprint"
            ],
            "candidateState": binding["candidateState"],
            "selectionMode": binding["selectionMode"],
            "manualReason": binding["manualReason"],
            "expiresAt": expires_at,
            "used": False,
        }

    target_public = _memory_public_audit_note(
        target_note.note_id,
        note_sources,
        root=root,
        include_internal=False,
    )
    source_signals = {
        signal.source_note_id: list(
            signal.reason_codes
        )
        for signal in (
            candidate.signals
            if candidate is not None
            else ()
        )
    }
    public_sources: list[dict[str, Any]] = []
    for source_id in binding["sourceNoteIds"]:
        public_source = _memory_public_audit_note(
            source_id,
            note_sources,
            root=root,
            include_internal=False,
        )
        if public_source is not None:
            public_sources.append(
                {
                    **public_source,
                    "reasonCodes": source_signals.get(
                        source_id,
                        (
                            []
                            if binding["selectionMode"]
                            == "exact_metadata"
                            else ["user_selected"]
                        ),
                    ),
                }
            )
    return {
        "ok": True,
        "schema": MEMORY_PROVENANCE_BACKFILL_PREVIEW_SCHEMA,
        "action": "provenance_backfill",
        "target": target_public,
        "candidateState": binding["candidateState"],
        "selectionMode": binding["selectionMode"],
        "manualReason": binding["manualReason"],
        "candidateSources": public_sources,
        "sourceNoteIds": list(binding["sourceNoteIds"]),
        "graphFingerprint": binding[
            "graphFingerprint"
        ],
        "bindingFingerprint": binding[
            "bindingFingerprint"
        ],
        "consequences": {
            "bodyChanged": False,
            "titleChanged": False,
            "derivedFromAdded": True,
            "searchIndexRebuilt": True,
            "hotContextRebuilt": True,
            "automaticInferenceUsed": False,
            "userSelectedSources": (
                binding["selectionMode"]
                == "user_selected"
            ),
        },
        "confirmToken": token,
        "expiresAt": expires_at,
    }


def _memory_provenance_backfill_binding_matches(
    binding: dict[str, Any],
    preview: dict[str, Any],
) -> bool:
    if not binding.get("ok"):
        return False
    checks = (
        (
            clean_text(
                str(binding.get("bindingFingerprint") or "")
            ),
            clean_text(
                str(preview.get("bindingFingerprint") or "")
            ),
        ),
        (
            clean_text(
                str(binding.get("graphFingerprint") or "")
            ),
            clean_text(
                str(preview.get("graphFingerprint") or "")
            ),
        ),
        (
            clean_text(
                str(
                    binding["targetNote"].source_hash
                    if binding.get("targetNote")
                    else ""
                )
            ),
            clean_text(
                str(preview.get("targetContentHash") or "")
            ),
        ),
    )
    if not all(
        left
        and right
        and secrets.compare_digest(left, right)
        for left, right in checks
    ):
        return False
    if list(binding.get("sourceNoteIds") or []) != list(
        preview.get("sourceNoteIds") or []
    ):
        return False
    current_source_hashes = dict(
        binding.get("sourceContentHashes") or {}
    )
    preview_source_hashes = dict(
        preview.get("sourceContentHashes") or {}
    )
    if set(current_source_hashes) != set(
        preview_source_hashes
    ):
        return False
    return all(
        secrets.compare_digest(
            clean_text(str(current_source_hashes[note_id])),
            clean_text(str(preview_source_hashes[note_id])),
        )
        for note_id in current_source_hashes
    )


def _replace_memory_front_matter(
    raw: str,
    metadata: dict[str, Any],
) -> str:
    formatted = _format_front_matter(metadata)
    if not raw.startswith("---"):
        return formatted + "\n\n" + raw
    lines = raw.splitlines(keepends=True)
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return formatted + "\n\n" + raw
    suffix = "".join(lines[end_index + 1 :])
    if not suffix:
        return formatted + "\n"
    return formatted + "\n" + suffix


def apply_memory_provenance_backfill(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    token = clean_text(confirm_token)
    timestamp = float(now())
    root_key = str((root or MEMORY_ROOT).resolve())
    with _memory_provenance_backfill_lock:
        _prune_memory_provenance_backfill_tokens(
            timestamp
        )
        preview = _memory_provenance_backfill_tokens.get(
            token
        )
        if preview is None:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_token_invalid"
                ),
            }
        if preview.get("used"):
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_token_reused"
                ),
            }
        preview["used"] = True
        if float(preview.get("expiresAt") or 0) < timestamp:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_token_expired"
                ),
            }
        if clean_text(
            str(preview.get("root") or "")
        ) != root_key:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_backfill_token_mismatch"
                ),
            }

    requested_target = _memory_vault_find_note(
        note_id_or_rel_path,
        root=root,
    )
    if requested_target is None:
        return {"ok": False, "error": "note_not_found"}
    if clean_text(
        str(preview.get("targetNoteId") or "")
    ) != requested_target[1].note_id:
        return {
            "ok": False,
            "error": (
                "memory_provenance_backfill_token_mismatch"
            ),
        }

    sync_memory_vault_index(root=root)
    binding = _memory_provenance_backfill_candidate_binding(
        note_id_or_rel_path,
        list(preview.get("sourceNoteIds") or []),
        selection_mode=clean_text(
            str(
                preview.get("selectionMode")
                or "exact_metadata"
            )
        ),
        root=root,
    )
    if not _memory_provenance_backfill_binding_matches(
        binding,
        preview,
    ):
        return {
            "ok": False,
            "error": (
                "memory_provenance_backfill_changed_since_preview"
            ),
        }

    applied_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(timestamp),
    )
    updated_note: MemoryVaultNote | None = None
    previous_content_hash = clean_text(
        str(preview.get("targetContentHash") or "")
    )
    try:
        with (
            _memory_delete_lock,
            _memory_edit_lock,
            _memory_provenance_backfill_lock,
        ):
            locked_binding = (
                _memory_provenance_backfill_candidate_binding(
                    note_id_or_rel_path,
                    list(
                        preview.get("sourceNoteIds") or []
                    ),
                    selection_mode=clean_text(
                        str(
                            preview.get("selectionMode")
                            or "exact_metadata"
                        )
                    ),
                    root=root,
                )
            )
            if not _memory_provenance_backfill_binding_matches(
                locked_binding,
                preview,
            ):
                return {
                    "ok": False,
                    "error": (
                        "memory_provenance_backfill_changed_since_preview"
                    ),
                }
            path = Path(locked_binding["targetPath"])
            current_raw = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
            current_note = parse_memory_note(
                path,
                current_raw,
            )
            if not secrets.compare_digest(
                current_note.source_hash,
                previous_content_hash,
            ):
                return {
                    "ok": False,
                    "error": (
                        "memory_provenance_backfill_changed_since_preview"
                    ),
                }
            metadata, _body = _split_front_matter(
                current_raw
            )
            if _as_list(metadata.get("derived_from")):
                return {
                    "ok": False,
                    "error": (
                        "memory_provenance_backfill_changed_since_preview"
                    ),
                }
            if not metadata:
                metadata = {
                    "id": current_note.note_id,
                    "type": current_note.note_type,
                    "title": current_note.title,
                    "status": current_note.status or "active",
                    "created_at": applied_at,
                    "source": "runtime",
                }
            metadata["derived_from"] = list(
                locked_binding["sourceNoteIds"]
            )
            metadata["updated_at"] = applied_at
            metadata["provenance_backfilled_at"] = applied_at
            metadata["provenance_backfill_method"] = (
                (
                    "user-selected-source-note-ids"
                    if preview.get("selectionMode")
                    == "user_selected"
                    else "exact-metadata-user-confirmed"
                )
            )
            metadata["provenance_backfill_audit_hash"] = (
                locked_binding["graphFingerprint"]
            )
            metadata["revision"] = max(
                0,
                _front_matter_int(
                    metadata,
                    "revision",
                    0,
                ),
            ) + 1
            updated_raw = _replace_memory_front_matter(
                current_raw,
                metadata,
            )
            atomic_text_write(
                path,
                updated_raw,
                durable=True,
            )
            updated_note = parse_memory_note(
                path,
                updated_raw,
            )
    except Exception as exc:
        return {
            "ok": False,
            "schema": (
                MEMORY_PROVENANCE_BACKFILL_RESULT_SCHEMA
            ),
            "action": "provenance_backfill",
            "applied": False,
            "error": "memory_provenance_backfill_failed",
            "detail": type(exc).__name__,
        }

    cleanup_errors: list[str] = []
    try:
        version = sync_memory_vault_index(root=root)
    except Exception:
        version = 0
        cleanup_errors.append(
            "memory_provenance_backfill_index_cleanup_failed"
        )
    try:
        refresh_memory_hot_context(root=root)
    except Exception:
        cleanup_errors.append(
            "memory_provenance_backfill_hot_context_cleanup_failed"
        )
    try:
        memory_provenance_backfill_preview(root=root)
    except Exception:
        cleanup_errors.append(
            "memory_provenance_backfill_audit_refresh_failed"
        )

    result = {
        "ok": not cleanup_errors,
        "schema": MEMORY_PROVENANCE_BACKFILL_RESULT_SCHEMA,
        "action": "provenance_backfill",
        "noteId": updated_note.note_id if updated_note else "",
        "applied": updated_note is not None,
        "previousContentHash": previous_content_hash,
        "contentHash": (
            updated_note.source_hash
            if updated_note is not None
            else ""
        ),
        "sourceNoteIds": list(
            preview.get("sourceNoteIds") or []
        ),
        "candidateState": clean_text(
            str(preview.get("candidateState") or "")
        ),
        "selectionMode": clean_text(
            str(preview.get("selectionMode") or "")
        ),
        "manualReason": clean_text(
            str(preview.get("manualReason") or "")
        ),
        "graphFingerprint": clean_text(
            str(preview.get("graphFingerprint") or "")
        ),
        "appliedAt": applied_at,
        "memoryVersion": version,
    }
    if cleanup_errors:
        result.update(
            {
                "error": (
                    "memory_provenance_backfill_cleanup_required"
                ),
                "cleanupErrors": cleanup_errors,
            }
        )
    return result


def _prune_memory_delete_tokens(now: float) -> None:
    stale = [
        token
        for token, payload in _memory_delete_tokens.items()
        if float(payload.get("expiresAt") or 0) < now - 60
    ]
    for token in stale:
        _memory_delete_tokens.pop(token, None)


def preview_memory_vault_user_note_deletion(
    note_id_or_rel_path: str,
    *,
    reason: str = "user_requested",
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    sync_memory_vault_index(root=root)
    target = _memory_vault_find_note(note_id_or_rel_path, root=root)
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    path, note, _raw = target
    vault = ensure_memory_vault_layout(root)
    rel_path = path.relative_to(vault).as_posix()
    protection = _memory_note_deletion_protection(note, rel_path)
    if protection == "internal_note_not_public":
        return {"ok": False, "error": "note_not_found"}
    if protection:
        return {
            "ok": False,
            "error": "memory_note_delete_protected",
            "reason": protection,
        }
    timestamp = float(now())
    expires_at = timestamp + MEMORY_DELETE_PREVIEW_TTL_SECONDS
    token = secrets.token_urlsafe(32)
    root_key = str((root or MEMORY_ROOT).resolve())
    normalized_reason = _memory_deletion_reason(reason)
    derivation_impact, derivation_impact_hash = (
        _memory_derivation_deletion_impact(
            note.note_id,
            root=root,
        )
    )
    with _memory_delete_lock:
        _prune_memory_delete_tokens(timestamp)
        _memory_delete_tokens[token] = {
            "noteId": note.note_id,
            "root": root_key,
            "contentHash": note.source_hash,
            "derivationImpactHash": derivation_impact_hash,
            "reason": normalized_reason,
            "expiresAt": expires_at,
            "used": False,
        }
    return {
        "ok": True,
        "schema": MEMORY_DELETE_PREVIEW_SCHEMA,
        "action": "delete",
        "note": {
            "id": note.note_id,
            "title": note.title,
            "type": note.note_type,
            "path": rel_path,
            "contentHash": note.source_hash,
            "provenance": _memory_note_provenance(
                note,
                rel_path=rel_path,
                note_state=_read_user_note_state(root).get(note.note_id, {}),
            ),
        },
        "consequences": {
            "sourceFileDeleted": True,
            "userStateRemoved": True,
            "searchIndexRemoved": True,
            "retrievalCacheInvalidated": True,
            "hotContextRebuilt": True,
            "contentFreeTombstoneRetained": True,
        },
        "derivationImpact": derivation_impact,
        "reason": normalized_reason,
        "confirmToken": token,
        "expiresAt": expires_at,
    }


def delete_memory_vault_user_note(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    reason: str = "user_requested",
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    token = clean_text(confirm_token)
    timestamp = float(now())
    root_key = str((root or MEMORY_ROOT).resolve())
    with _memory_delete_lock:
        _prune_memory_delete_tokens(timestamp)
        preview = _memory_delete_tokens.get(token)
        if preview is None:
            return {"ok": False, "error": "memory_delete_token_invalid"}
        if preview.get("used"):
            return {"ok": False, "error": "memory_delete_token_reused"}
        preview["used"] = True
        if float(preview.get("expiresAt") or 0) < timestamp:
            return {"ok": False, "error": "memory_delete_token_expired"}
        if clean_text(str(preview.get("root") or "")) != root_key:
            return {"ok": False, "error": "memory_delete_token_mismatch"}

    target = _memory_vault_find_note(note_id_or_rel_path, root=root)
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    path, note, _raw = target
    if (
        clean_text(str(preview.get("noteId") or ""))
        != clean_text(note.note_id)
    ):
        return {"ok": False, "error": "memory_delete_token_mismatch"}
    vault = ensure_memory_vault_layout(root).resolve()
    resolved_path = path.resolve()
    if (
        not resolved_path.is_relative_to(vault)
        or resolved_path.suffix.lower() != ".md"
    ):
        return {"ok": False, "error": "memory_delete_target_invalid"}
    rel_path = resolved_path.relative_to(vault).as_posix()
    protection = _memory_note_deletion_protection(note, rel_path)
    if protection:
        return {
            "ok": False,
            "error": "memory_note_delete_protected",
            "reason": protection,
        }
    if note.source_hash != clean_text(str(preview.get("contentHash") or "")):
        return {"ok": False, "error": "memory_note_changed_since_preview"}
    derivation_impact, derivation_impact_hash = (
        _memory_derivation_deletion_impact(
            note.note_id,
            root=root,
        )
    )
    if not secrets.compare_digest(
        derivation_impact_hash,
        clean_text(
            str(preview.get("derivationImpactHash") or "")
        ),
    ):
        return {
            "ok": False,
            "error": (
                "memory_derivation_impact_changed_since_preview"
            ),
        }

    state = _read_user_note_state(root)
    previous_note_state = state.pop(note.note_id, None)
    normalized_reason = _memory_deletion_reason(
        reason or clean_text(str(preview.get("reason") or ""))
    )
    deleted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))
    provenance = _memory_note_provenance(
        note,
        rel_path=rel_path,
        note_state=previous_note_state or {},
    )
    tombstone = {
        "schema": MEMORY_DELETE_TOMBSTONE_SCHEMA,
        "noteId": note.note_id,
        "noteType": note.note_type,
        "sourceType": provenance["sourceType"],
        "reason": normalized_reason,
        "deletedAt": deleted_at,
    }

    source_file_deleted = False
    try:
        with _memory_delete_lock:
            if not resolved_path.exists():
                return {"ok": False, "error": "note_not_found"}
            current_raw = resolved_path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
            current_note = parse_memory_note(resolved_path, current_raw)
            if current_note.source_hash != clean_text(
                str(preview.get("contentHash") or "")
            ):
                return {
                    "ok": False,
                    "error": "memory_note_changed_since_preview",
                }
            locked_impact, locked_impact_hash = (
                _memory_derivation_deletion_impact(
                    current_note.note_id,
                    root=root,
                )
            )
            if not secrets.compare_digest(
                locked_impact_hash,
                clean_text(
                    str(
                        preview.get(
                            "derivationImpactHash"
                        )
                        or ""
                    )
                ),
            ):
                return {
                    "ok": False,
                    "error": (
                        "memory_derivation_impact_changed_since_preview"
                    ),
                }
            derivation_impact = locked_impact
            _append_memory_deletion_tombstone(
                tombstone,
                root=root,
            )
            try:
                resolved_path.unlink()
                source_file_deleted = True
            except FileNotFoundError:
                source_file_deleted = True
            except OSError:
                pass
    except Exception as exc:
        return {
            "ok": False,
            "error": "memory_delete_failed",
            "detail": type(exc).__name__,
        }

    cleanup_errors: list[str] = []
    try:
        _write_user_note_state(state, root)
    except OSError:
        cleanup_errors.append("memory_delete_user_state_cleanup_failed")
    try:
        version = sync_memory_vault_index(root=root)
    except Exception:
        version = 0
        cleanup_errors.append("memory_delete_index_cleanup_failed")
    if "memory_delete_user_state_cleanup_failed" in cleanup_errors:
        try:
            if note.note_id not in _read_user_note_state(root):
                cleanup_errors.remove(
                    "memory_delete_user_state_cleanup_failed"
                )
        except OSError:
            pass
    source_file_deleted = not resolved_path.exists()
    if not source_file_deleted:
        cleanup_errors.append("memory_delete_source_cleanup_failed")
    try:
        refresh_memory_hot_context(root=root)
    except Exception:
        cleanup_errors.append("memory_delete_hot_context_cleanup_failed")
    if cleanup_errors:
        return {
            "ok": False,
            "schema": MEMORY_DELETE_RESULT_SCHEMA,
            "action": "delete",
            "noteId": note.note_id,
            "deleted": False,
            "tombstoned": True,
            "sourceFileDeleted": source_file_deleted,
            "error": "memory_delete_cleanup_required",
            "cleanupErrors": list(dict.fromkeys(cleanup_errors)),
            "tombstone": tombstone,
            "derivationImpact": derivation_impact,
        }

    return {
        "ok": True,
        "schema": MEMORY_DELETE_RESULT_SCHEMA,
        "action": "delete",
        "noteId": note.note_id,
        "deleted": True,
        "deletedAt": deleted_at,
        "reason": normalized_reason,
        "memoryVersion": version,
        "sourceFileDeleted": True,
        "tombstoned": True,
        "tombstone": tombstone,
        "derivationImpact": derivation_impact,
    }


def memory_note_was_deleted(
    note_id: str,
    *,
    root: Path | None = None,
) -> bool:
    target = clean_text(note_id)
    return any(
        clean_text(str(item.get("noteId") or "")) == target
        for item in _read_memory_deletion_tombstones(root)
    )


def _memory_vault_note_path(*, note_type: str, title: str, root: Path | None = None) -> Path:
    vault = ensure_memory_vault_layout(root)
    normalized_type = clean_text(note_type).lower() or "concept"
    folder_by_type = {
        "core": "core",
        "daily": "daily",
        "episode": "episodes",
        "episodes": "episodes",
        "concept": "concepts",
        "semantic": "concepts",
        "procedure": "procedures",
        "procedural": "procedures",
        "project": "projects",
    }
    folder = folder_by_type.get(normalized_type, "concepts")
    return vault / folder / f"{_slug(title, default=normalized_type)}.md"


def bootstrap_memory_vault_source(*, root: Path | None = None, overwrite: bool = False) -> list[Path]:
    """Create the minimum Markdown source notes needed for an active vault."""
    created_or_existing: list[Path] = []
    for note in BOOTSTRAP_NOTES:
        path = _memory_vault_note_path(
            note_type=clean_text(str(note.get("note_type") or "concept")),
            title=clean_text(str(note.get("title") or "Memory Note")),
            root=root,
        )
        if path.exists() and not overwrite:
            created_or_existing.append(path)
            continue
        created_or_existing.append(
            write_memory_vault_note(
                note_type=clean_text(str(note.get("note_type") or "concept")),
                title=clean_text(str(note.get("title") or "Memory Note")),
                body=clean_text(str(note.get("body") or "")),
                tags=list(note.get("tags") or []),
                projects=list(note.get("projects") or [DEFAULT_PROJECT]),
                links=list(note.get("links") or []),
                source=BOOTSTRAP_NOTE_SOURCE,
                importance=float(note.get("importance", 0.5) or 0.5),
                confidence=clean_text(str(note.get("confidence") or "medium")),
                root=root,
            )
        )
    sync_memory_vault_index(root=root)
    return created_or_existing


def refresh_memory_hot_context(
    *,
    root: Path | None = None,
    max_chars: int = HOT_CONTEXT_MAX_CHARS,
    dependency_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh rebuildable hot prompt/cache files from active vault notes."""
    version = sync_memory_vault_index(root=root)
    index_path = memory_index_db_path(root)
    prompt_dir = memory_index_dir(root) / "prompt_blocks"
    prompt_dir.mkdir(parents=True, exist_ok=True)

    with closing(_connect_index(index_path)) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM notes
            WHERE status NOT IN ('archived', 'superseded')
              AND note_type IN ('core', 'project')
              AND rel_path NOT LIKE 'core/legacy-guild-%'
            ORDER BY importance DESC, updated_at DESC, title ASC
            LIMIT 24
            """
        ).fetchall()
        with _memory_delete_lock:
            deletion_journal_state_before = (
                _memory_deletion_journal_state(root)
            )
            derivation_revocation_state_before = (
                _memory_derivation_revocation_file_state(
                    root
                )
            )
            deleted_note_ids = _memory_deleted_note_ids(root)
        rows = [
            row
            for row in rows
            if clean_text(str(row["note_id"])) not in deleted_note_ids
        ]

        block_lines: list[str] = []
        source_paths: list[str] = []
        for row in rows:
            snippet = _truncate_note(row, max_chars=360)
            if snippet:
                block_lines.append(snippet)
                source_paths.append(clean_text(str(row["rel_path"])))

        content = "\n".join(block_lines)
        if len(content) > max_chars:
            content = content[: max(0, max_chars - 3)].rstrip() + "..."

        deletion_journal_state = _memory_deletion_journal_state(root)
        derivation_revocation_state = (
            _memory_derivation_revocation_file_state(root)
        )
        if (
            deletion_journal_state
            != deletion_journal_state_before
            or derivation_revocation_state
            != derivation_revocation_state_before
        ):
            content = ""
            source_paths = []
        payload = {
            "memory_version": version,
            "created_at": time.time(),
            "content": content,
            "sources": source_paths,
            "deletion_journal_mtime_ns": deletion_journal_state[0],
            "deletion_journal_size": deletion_journal_state[1],
            "derivation_revocations_mtime_ns": (
                derivation_revocation_state[0]
            ),
            "derivation_revocations_size": (
                derivation_revocation_state[1]
            ),
            "max_chars": max_chars,
            "dependencies": dependency_health or {},
        }
        atomic_json_write(
            memory_index_dir(root) / "hot_context.json",
            payload,
            durable=True,
        )
        atomic_text_write(
            prompt_dir / "core_prompt.txt",
            content,
            durable=True,
        )
        conn.execute(
            """
            INSERT INTO prompt_block_cache(block_key, memory_version, created_at, content)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(block_key) DO UPDATE SET
                memory_version = excluded.memory_version,
                created_at = excluded.created_at,
                content = excluded.content
            """,
            ("hot_context", version, time.time(), content),
        )
        conn.commit()

    return payload


def read_memory_hot_context(*, root: Path | None = None, max_chars: int = HOT_CONTEXT_MAX_CHARS) -> str:
    path = memory_index_dir(root) / "hot_context.json"
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    try:
        cached_deletion_journal_mtime_ns = int(
            payload.get("deletion_journal_mtime_ns") or 0
        )
        cached_deletion_journal_size = int(
            payload.get("deletion_journal_size") or 0
        )
        cached_derivation_revocations_mtime_ns = int(
            payload.get(
                "derivation_revocations_mtime_ns"
            )
            or 0
        )
        cached_derivation_revocations_size = int(
            payload.get("derivation_revocations_size")
            or 0
        )
    except (TypeError, ValueError):
        return ""
    if (
        (
            cached_deletion_journal_mtime_ns,
            cached_deletion_journal_size,
        )
        != _memory_deletion_journal_state(root)
        or (
            cached_derivation_revocations_mtime_ns,
            cached_derivation_revocations_size,
        )
        != _memory_derivation_revocation_file_state(root)
    ):
        return ""
    content = clean_text(str(payload.get("content") or ""))
    if len(content) > max_chars:
        return content[: max(0, max_chars - 3)].rstrip() + "..."
    return content


def probe_sub_llm_dependency(
    *,
    summary_llm_url: str | None = None,
    model_name: str | None = None,
    timeout_s: float = 0.25,
) -> dict[str, Any]:
    endpoint = clean_text(summary_llm_url or SUMMARY_LLM_URL)
    health_url = _models_url(endpoint)
    result: dict[str, Any] = {
        "name": "sub_llm",
        "role": "summary_memory_or_deeper_state_reasoning",
        "endpoint": endpoint,
        "health_url": health_url,
        "model": clean_text(model_name or SUMMARY_MODEL_NAME),
        "available": False,
        "status": "unknown",
        "fallback_mode": "deterministic_memory_vault_maintenance",
    }
    if not endpoint:
        result["status"] = "missing_url"
        return result

    started = time.monotonic()
    try:
        request = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(request, timeout=max(0.05, timeout_s)) as response:
            body = response.read(512).decode("utf-8", errors="ignore")
            status_code = int(getattr(response, "status", 0) or response.getcode())
            result["available"] = 200 <= status_code < 300
            result["status"] = status_code
            result["sample"] = body[:240]
    except urllib.error.HTTPError as exc:
        result["status"] = int(exc.code)
        result["error"] = clean_text(str(exc))[:240]
    except Exception as exc:
        result["status"] = type(exc).__name__
        result["error"] = clean_text(str(exc))[:240]
    finally:
        result["latency_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    return result


def activate_memory_vault_for_guild(guild_id: int, *, root: Path | None = None) -> dict[str, Any]:
    """Activate the full Markdown vault/index/cache pipeline for a guild."""
    started = time.monotonic()
    sub_llm = probe_sub_llm_dependency()
    derivation_recomposition = (
        run_memory_derivation_recomposition_once(
            root=root,
            sub_llm_health=sub_llm,
        )
    )
    bootstrap_paths = bootstrap_memory_vault_source(root=root)
    legacy_path = refresh_legacy_memory_mirror(guild_id, root=root)
    legacy_nodes = refresh_legacy_memory_node_notes(guild_id, root=root)
    consolidated_path = consolidate_daily_memory_once(guild_id, root=root)
    semantic_consolidation = run_semantic_memory_consolidation_once(
        guild_id,
        root=root,
        sub_llm_health=sub_llm,
    )
    version = sync_memory_vault_index(root=root)
    hot_context = refresh_memory_hot_context(root=root, dependency_health={"sub_llm": sub_llm})
    return {
        "guild_id": guild_id,
        "vault_path": str(memory_vault_root(root)),
        "index_path": str(memory_index_db_path(root)),
        "bootstrap_notes": [str(path) for path in bootstrap_paths],
        "legacy_mirror": str(legacy_path) if legacy_path else "",
        "legacy_nodes": [str(path) for path in legacy_nodes],
        "daily_consolidation": str(consolidated_path) if consolidated_path else "",
        "semantic_consolidation": semantic_consolidation,
        "derivation_recomposition": (
            derivation_recomposition
        ),
        "memory_version": version,
        "hot_context_sources": hot_context.get("sources", []),
        "dependencies": {"sub_llm": sub_llm},
        "semantic_consolidation_enabled": bool(sub_llm.get("available")),
        "fallback_mode": "" if sub_llm.get("available") else sub_llm.get("fallback_mode"),
        "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
    }


def mark_memory_note_superseded(note_id_or_rel_path: str, *, root: Path | None = None) -> bool:
    vault = ensure_memory_vault_layout(root)
    deleted_note_ids = _memory_deleted_note_ids(root)
    target_key = clean_text(note_id_or_rel_path)
    with _memory_edit_lock:
        for path in vault.rglob("*.md"):
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore")
                note = parse_memory_note(path, raw)
            except Exception:
                continue
            if note.note_id in deleted_note_ids:
                continue
            rel_path = path.relative_to(vault).as_posix()
            if target_key not in {note.note_id, rel_path, path.stem}:
                continue
            if raw.startswith("---"):
                raw = re.sub(
                    r"(?m)^status:\s*.*$",
                    "status: superseded",
                    raw,
                    count=1,
                )
                if "status:" not in raw.split("---", 2)[1]:
                    raw = raw.replace(
                        "---",
                        "---\nstatus: superseded",
                        1,
                    )
            else:
                raw = _format_front_matter(
                    {
                        "id": note.note_id,
                        "type": note.note_type,
                        "title": note.title,
                        "status": "superseded",
                        "updated_at": _utc_now_iso(),
                    }
                ) + "\n\n" + raw
            atomic_text_write(path, raw, durable=True)
            sync_memory_vault_index(root=root)
            return True
    return False


__all__ = [
    "MEMORY_DERIVATION_IMPACT_SCHEMA",
    "MEMORY_DERIVATION_RECOMPOSITION_SCHEMA",
    "MEMORY_DERIVATION_REVOCATIONS_SCHEMA",
    "MEMORY_DELETE_PREVIEW_SCHEMA",
    "MEMORY_DELETE_RESULT_SCHEMA",
    "MEMORY_DELETE_TOMBSTONE_SCHEMA",
    "MEMORY_EDIT_RESULT_SCHEMA",
    "MEMORY_PROVENANCE_BACKFILL_AUDIT_SCHEMA",
    "MEMORY_PROVENANCE_BACKFILL_PREVIEW_SCHEMA",
    "MEMORY_PROVENANCE_BACKFILL_RESULT_SCHEMA",
    "MEMORY_PROVENANCE_FORWARD_REJECTIONS_SCHEMA",
    "MEMORY_PROVENANCE_MANUAL_OPTIONS_SCHEMA",
    "MEMORY_PROVENANCE_SCHEMA",
    "MEMORY_QUARANTINE_STATUS_SCHEMA",
    "MemoryNoteDeletedError",
    "MemoryVaultNote",
    "activate_memory_vault_for_guild",
    "apply_memory_provenance_backfill",
    "append_turn_rows_to_memory_vault",
    "bootstrap_memory_vault_source",
    "build_memory_vault_context",
    "ensure_memory_vault_layout",
    "export_memory_graph",
    "mark_memory_note_superseded",
    "memory_note_was_deleted",
    "memory_index_db_path",
    "memory_provenance_backfill_preview",
    "memory_provenance_manual_source_options",
    "memory_quarantine_status",
    "memory_vault_user_snapshot",
    "memory_vault_root",
    "parse_memory_note",
    "probe_sub_llm_dependency",
    "preview_memory_vault_user_note_deletion",
    "preview_memory_provenance_backfill_application",
    "recall_memory_vault",
    "read_memory_hot_context",
    "request_sub_llm_json",
    "refresh_legacy_memory_mirror",
    "refresh_legacy_memory_node_notes",
    "refresh_memory_hot_context",
    "consolidate_daily_memory_once",
    "run_memory_vault_maintenance_once",
    "run_memory_derivation_recomposition_once",
    "run_semantic_memory_consolidation_once",
    "sync_memory_vault_index",
    "delete_memory_vault_user_note",
    "update_memory_vault_user_note",
    "write_memory_vault_note",
]
