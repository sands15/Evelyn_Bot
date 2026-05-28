from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .assistant_contracts import MemoryRecallRequest, MemoryRecallResult
from .config import MEMORY_ROOT, MEMORY_ROW_MAX_CHARS, SUMMARY_LLM_URL, SUMMARY_MODEL_NAME
from .text import clean_text


VAULT_DIR_NAME = "memory_vault"
INDEX_DIR_NAME = "memory_index"
INDEX_DB_NAME = "memory.sqlite"
RETRIEVAL_CACHE_TTL_SECONDS = 300
DEFAULT_PROJECT = "evelyn"
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
    for name in ("core", "daily", "episodes", "concepts", "procedures", "projects"):
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


def parse_memory_note(path: Path, text: str | None = None) -> MemoryVaultNote:
    raw = path.read_text(encoding="utf-8", errors="ignore") if text is None else text
    metadata: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        lines = raw.splitlines()
        if len(lines) > 1:
            end_index = None
            for index, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    end_index = index
                    break
            if end_index is not None:
                for line in lines[1:end_index]:
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    metadata[clean_text(key).strip()] = clean_text(value).strip()
                body = "\n".join(lines[end_index + 1 :]).strip()

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
            updated_at TEXT NOT NULL DEFAULT '',
            importance REAL NOT NULL DEFAULT 0.5,
            confidence TEXT NOT NULL DEFAULT '',
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
    index_path = db_path or memory_index_db_path(root)
    note_paths = sorted(path for path in vault.rglob("*.md") if path.is_file())

    with closing(_connect_index(index_path)) as conn:
        _ensure_schema(conn)
        force_reindex = _get_metadata_int(conn, "schema_version", 0) < 3
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
            rel_path = path.relative_to(vault).as_posix()
            seen.add(rel_path)
            if not force_reindex and existing.get(rel_path) and existing[rel_path]["source_hash"] == note.source_hash:
                continue
            conn.execute(
                """
                INSERT INTO notes(
                    note_id, rel_path, note_type, title, body, tags, projects, links,
                    status, updated_at, importance, confidence, mtime_ns, source_hash
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    note_id = excluded.note_id,
                    note_type = excluded.note_type,
                    title = excluded.title,
                    body = excluded.body,
                    tags = excluded.tags,
                    projects = excluded.projects,
                    links = excluded.links,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    importance = excluded.importance,
                    confidence = excluded.confidence,
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
                    note.updated_at,
                    _front_matter_float(note.metadata, "importance", 0.5),
                    clean_text(str(note.metadata.get("confidence") or "")),
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
            _set_metadata(conn, "schema_version", 3)
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
) -> dict[str, Any]:
    """Export a rebuildable graph JSON view for the control page."""
    started = time.monotonic()
    version = sync_memory_vault_index(root=root)
    max_nodes = max(20, min(500, int(max_nodes or MEMORY_GRAPH_MAX_NODES)))

    with closing(_connect_index(memory_index_db_path(root))) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM notes
            WHERE status NOT IN ('archived', 'superseded')
            ORDER BY
                CASE note_type
                    WHEN 'core' THEN 0
                    WHEN 'project' THEN 1
                    WHEN 'procedure' THEN 2
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
            (max_nodes,),
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
                "stats": {"node_count": 0, "edge_count": 0, "type_counts": {}},
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
            type_counts[note_type] = type_counts.get(note_type, 0) + 1
            nodes.append(
                {
                    "id": note_id,
                    "title": clean_text(str(row["title"])),
                    "type": note_type,
                    "rel_path": clean_text(str(row["rel_path"])),
                    "tags": _safe_json_list(clean_text(str(row["tags"]))),
                    "projects": _safe_json_list(clean_text(str(row["projects"]))),
                    "links": _safe_json_list(clean_text(str(row["links"]))),
                    "status": clean_text(str(row["status"])),
                    "updated_at": clean_text(str(row["updated_at"])),
                    "importance": float(row["importance"] or 0.0),
                    "confidence": clean_text(str(row["confidence"])),
                    "degree": degrees.get(note_id, 0),
                    "size": _graph_node_size(row, degrees.get(note_id, 0)),
                    "snippet": clean_text(str(row["body"]))[:260],
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


def refresh_legacy_memory_mirror(guild_id: int, *, root: Path | None = None, max_items: int = 80) -> Path | None:
    base_root = root or MEMORY_ROOT
    guild_dir = base_root / f"guild_{guild_id}"
    if not guild_dir.exists():
        return None

    vault = ensure_memory_vault_layout(root)
    target = vault / "core" / f"legacy-guild-{guild_id}.md"
    sections: list[str] = [f"# Legacy Guild Memory {guild_id}", ""]
    scope_dirs = [guild_dir]
    for pattern in ("room_*", "person_*", "session_*"):
        scope_dirs.extend(sorted(guild_dir.glob(pattern))[:20])

    for scope_dir in scope_dirs:
        scope_name = scope_dir.name
        lines: list[str] = []
        summary = (scope_dir / "rolling_summary.txt").read_text(encoding="utf-8", errors="ignore").strip() if (scope_dir / "rolling_summary.txt").exists() else ""
        if summary:
            lines.append(f"Summary: {clean_text(summary)}")
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
        for row in rows[-max_items:]:
            text = clean_text(str(row.get("text") or ""))
            if text:
                kind = clean_text(str(row.get("type") or "memory")) or "memory"
                lines.append(f"- {kind}: {text}")
        if lines:
            sections.append(f"## {scope_name}")
            sections.extend(lines)
            sections.append("")

    if len(sections) <= 2:
        return None

    body = "\n".join(sections).strip() + "\n"
    if target.exists():
        try:
            existing_note = parse_memory_note(target)
            if clean_text(existing_note.body) == clean_text(body):
                return target
        except Exception:
            pass

    front_matter = _format_front_matter(
        {
            "id": f"legacy-guild-{guild_id}",
            "type": "core",
            "title": f"Legacy Guild Memory {guild_id}",
            "status": "active",
            "tags": ["legacy", "memory"],
            "projects": [DEFAULT_PROJECT],
            "updated_at": _utc_now_iso(),
        }
    )
    target.write_text(front_matter + "\n\n" + body, encoding="utf-8")
    return target


def append_turn_rows_to_memory_vault(
    guild_id: int,
    rows: list[dict[str, Any]],
    *,
    scope_type: str = "guild",
    scope_key: str | None = None,
    root: Path | None = None,
) -> Path | None:
    normalized: list[str] = []
    for row in rows:
        text = clean_text(str(row.get("text") or ""))
        if not text:
            continue
        role = clean_text(str(row.get("role") or "memory")) or "memory"
        speaker = clean_text(str(row.get("speaker") or role)) or role
        source = clean_text(str(row.get("source") or "unknown")) or "unknown"
        if len(text) > MEMORY_ROW_MAX_CHARS * 2:
            text = text[: MEMORY_ROW_MAX_CHARS * 2 - 3].rstrip() + "..."
        normalized.append(f"- {role}/{speaker}/{source}: {text}")
    if not normalized:
        return None

    vault = ensure_memory_vault_layout(root)
    day_key = time.strftime("%Y-%m-%d")
    path = vault / "daily" / f"{day_key}.md"
    now = _utc_now_iso()
    if not path.exists():
        front_matter = _format_front_matter(
            {
                "id": f"daily-{day_key}",
                "type": "daily",
                "title": f"Daily Memory {day_key}",
                "status": "active",
                "tags": ["daily", "conversation"],
                "projects": [DEFAULT_PROJECT],
                "updated_at": now,
            }
        )
        path.write_text(front_matter + f"\n\n# Daily Memory {day_key}\n\n", encoding="utf-8")

    scope_label = scope_type if not scope_key else f"{scope_type}:{_slug(scope_key, default='scope')}"
    block = "\n".join(
        [
            f"## {time.strftime('%H:%M:%S')} guild:{guild_id} scope:{scope_label}",
            *normalized,
            "",
        ]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    return path


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
    target.write_text(content, encoding="utf-8")
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
        path = write_memory_vault_note(
            note_type=note_type,
            title=title,
            body=body,
            tags=tags or ["semantic-consolidation"],
            links=links,
            source="sub-llm-semantic-consolidation",
            importance=importance,
            confidence=confidence,
            root=root,
        )
        created.append(str(path))

    sync_memory_vault_index(root=root)
    return {
        "status": "created" if created else "no_notes_created",
        "created_notes": created,
        "source_daily": str(source_path),
        "source_hash": digest,
        "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
    }


def run_memory_vault_maintenance_once(guild_id: int, *, root: Path | None = None) -> dict[str, Any]:
    started = time.monotonic()
    sub_llm = probe_sub_llm_dependency()
    bootstrap_paths = bootstrap_memory_vault_source(root=root)
    legacy_path = refresh_legacy_memory_mirror(guild_id, root=root)
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
        "daily_consolidation": str(consolidated_path) if consolidated_path else "",
        "semantic_consolidation": semantic_consolidation,
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
        version = sync_memory_vault_index(root=root, db_path=db_path)
        index_path = db_path or memory_index_db_path(root)
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        active_project = clean_text(str(metadata.get("active_project") or DEFAULT_PROJECT)).lower()
        focus_items = metadata.get("context_focus") if isinstance(metadata.get("context_focus"), list) else []
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
                    },
                )

            rows, retrieval_mode = _fetch_candidate_rows(
                conn,
                query_tokens=query_tokens,
                focus_tokens=focus_tokens,
                limit=max(80, request.max_items * 20),
            )
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
            if graph_neighbors:
                selected.extend(graph_neighbors)
            snippets = [_truncate_note(row) for row in selected]
            sources = [clean_text(str(row["rel_path"])) for row in selected]
            procedure_rows = [
                row for _, _, row in scored
                if clean_text(str(row["note_type"])) == "procedure"
            ][:2]
            procedure_snippets = [_truncate_note(row, max_chars=300) for row in procedure_rows]

            context_parts: list[str] = []
            if snippets:
                context_parts.append("[Memory Vault Notes]\n" + "\n".join(snippets))
            if procedure_snippets:
                context_parts.append("[Procedural Memory]\n" + "\n".join(procedure_snippets))
            context_text = "\n\n".join(context_parts)
            payload = {
                "context_text": context_text,
                "facts": snippets,
                "sources": sources,
                "retrieval_mode": retrieval_mode,
            }
            _write_retrieval_cache(conn, cache_key, version, payload)

        return MemoryRecallResult(
            turn_id=request.turn_id,
            ok=True,
            context_text=context_text,
            facts=tuple(snippets),
            sources=tuple(sources),
            latency_ms=(time.monotonic() - started) * 1000.0,
            metadata={"cache_hit": False, "memory_version": version, "retrieval_mode": retrieval_mode},
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
    source: str = "consolidation",
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
            "tags": tags or [],
            "projects": projects or [DEFAULT_PROJECT],
            "links": links or [],
        }
    )
    content = f"{front_matter}\n\n# {clean_text(title)}\n\n{clean_text(body)}\n"
    path.write_text(content, encoding="utf-8")
    sync_memory_vault_index(root=root)
    return path


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

        payload = {
            "memory_version": version,
            "created_at": time.time(),
            "content": content,
            "sources": source_paths,
            "max_chars": max_chars,
            "dependencies": dependency_health or {},
        }
        (memory_index_dir(root) / "hot_context.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (prompt_dir / "core_prompt.txt").write_text(content, encoding="utf-8")
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
    bootstrap_paths = bootstrap_memory_vault_source(root=root)
    legacy_path = refresh_legacy_memory_mirror(guild_id, root=root)
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
        "daily_consolidation": str(consolidated_path) if consolidated_path else "",
        "semantic_consolidation": semantic_consolidation,
        "memory_version": version,
        "hot_context_sources": hot_context.get("sources", []),
        "dependencies": {"sub_llm": sub_llm},
        "semantic_consolidation_enabled": bool(sub_llm.get("available")),
        "fallback_mode": "" if sub_llm.get("available") else sub_llm.get("fallback_mode"),
        "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
    }


def mark_memory_note_superseded(note_id_or_rel_path: str, *, root: Path | None = None) -> bool:
    vault = ensure_memory_vault_layout(root)
    target_key = clean_text(note_id_or_rel_path)
    for path in vault.rglob("*.md"):
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            note = parse_memory_note(path, raw)
        except Exception:
            continue
        rel_path = path.relative_to(vault).as_posix()
        if target_key not in {note.note_id, rel_path, path.stem}:
            continue
        if raw.startswith("---"):
            raw = re.sub(r"(?m)^status:\s*.*$", "status: superseded", raw, count=1)
            if "status:" not in raw.split("---", 2)[1]:
                raw = raw.replace("---", "---\nstatus: superseded", 1)
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
        path.write_text(raw, encoding="utf-8")
        sync_memory_vault_index(root=root)
        return True
    return False


__all__ = [
    "MemoryVaultNote",
    "activate_memory_vault_for_guild",
    "append_turn_rows_to_memory_vault",
    "bootstrap_memory_vault_source",
    "build_memory_vault_context",
    "ensure_memory_vault_layout",
    "export_memory_graph",
    "mark_memory_note_superseded",
    "memory_index_db_path",
    "memory_vault_root",
    "parse_memory_note",
    "probe_sub_llm_dependency",
    "recall_memory_vault",
    "read_memory_hot_context",
    "request_sub_llm_json",
    "refresh_legacy_memory_mirror",
    "refresh_memory_hot_context",
    "consolidate_daily_memory_once",
    "run_memory_vault_maintenance_once",
    "run_semantic_memory_consolidation_once",
    "sync_memory_vault_index",
    "write_memory_vault_note",
]
