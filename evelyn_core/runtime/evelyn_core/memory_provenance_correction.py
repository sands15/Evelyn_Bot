from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import memory_vault as vault
from .runtime_artifact_io import atomic_json_write, atomic_text_write
from .text import clean_text


MEMORY_PROVENANCE_CORRECTION_OVERVIEW_SCHEMA = (
    "memory.provenance.corrections.v1"
)
MEMORY_PROVENANCE_CORRECTION_OPTIONS_SCHEMA = (
    "memory.provenance.correction-options.v1"
)
MEMORY_PROVENANCE_CORRECTION_PREVIEW_SCHEMA = (
    "memory.provenance.correction-preview.v1"
)
MEMORY_PROVENANCE_CORRECTION_RESULT_SCHEMA = (
    "memory.provenance.correction-result.v1"
)
MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA = (
    "memory.provenance.correction.event.v2"
)
MEMORY_PROVENANCE_CORRECTION_LEGACY_EVENT_SCHEMA = (
    "memory.provenance.correction.event.v1"
)
MEMORY_PROVENANCE_CORRECTION_WRITER_SCHEMA = (
    "memory.provenance.correction-writer.v1"
)
MEMORY_PROVENANCE_CORRECTION_CHAIN_HEAD_SCHEMA = (
    "memory.provenance.correction-chain-head.v1"
)
MEMORY_PROVENANCE_CORRECTION_JOURNAL_NAME = (
    "memory_provenance_corrections.jsonl"
)
MEMORY_PROVENANCE_CORRECTION_WRITER_LOCK_NAME = (
    ".memory_provenance_correction_writer.lock"
)
MEMORY_PROVENANCE_CORRECTION_WRITER_MARKER_NAME = (
    "memory_provenance_correction_writer.json"
)
MEMORY_PROVENANCE_CORRECTION_CHAIN_HEAD_NAME = (
    "memory_provenance_correction_chain_head.json"
)
MEMORY_PROVENANCE_CORRECTION_TOKEN_TTL_SECONDS = 120
MEMORY_PROVENANCE_CORRECTION_MAX_SOURCES = 12
MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS = "0" * 64

_correction_lock = threading.RLock()
_correction_tokens: dict[str, dict[str, Any]] = {}
_correction_process_nonce = secrets.token_hex(12)
_writer_local = threading.local()
_writer_owned_roots: dict[str, int] = {}


class MemoryProvenanceCorrectionJournalIntegrityError(OSError):
    pass


class MemoryProvenanceCorrectionWriterUnavailable(OSError):
    pass


def _now_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(value),
    )


def _journal_path(root: Path | None = None) -> Path:
    return (
        vault.memory_index_dir(root)
        / MEMORY_PROVENANCE_CORRECTION_JOURNAL_NAME
    )


def _writer_lock_path(root: Path | None = None) -> Path:
    return (
        vault.memory_index_dir(root)
        / MEMORY_PROVENANCE_CORRECTION_WRITER_LOCK_NAME
    )


def _writer_marker_path(root: Path | None = None) -> Path:
    return (
        vault.memory_index_dir(root)
        / MEMORY_PROVENANCE_CORRECTION_WRITER_MARKER_NAME
    )


def _chain_head_path(root: Path | None = None) -> Path:
    return (
        vault.memory_index_dir(root)
        / MEMORY_PROVENANCE_CORRECTION_CHAIN_HEAD_NAME
    )


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _event_hash(payload: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key != "eventHash"
    }
    return hashlib.sha256(
        _canonical_json(unsigned).encode("utf-8")
    ).hexdigest()


def _legacy_anchor(lines: list[str]) -> str:
    digest = hashlib.sha256(
        b"memory.provenance.correction.legacy-anchor.v1\n"
    )
    for line in lines:
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _write_writer_marker(
    *,
    root: Path | None,
    state: str,
    acquired_at: str,
    recovered_stale_owner: bool,
) -> None:
    payload = {
        "schema": MEMORY_PROVENANCE_CORRECTION_WRITER_SCHEMA,
        "state": state,
        "processNonce": _correction_process_nonce,
        "pid": os.getpid(),
        "acquiredAt": acquired_at,
        "updatedAt": _now_iso(),
        "recoveredStaleOwner": recovered_stale_owner,
        "contentFree": True,
    }
    atomic_json_write(
        _writer_marker_path(root),
        payload,
        durable=True,
    )


def _lock_writer_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(
        handle.fileno(),
        fcntl.LOCK_EX | fcntl.LOCK_NB,
    )


def _unlock_writer_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _held_writer_roots() -> set[str]:
    held = getattr(_writer_local, "held_roots", None)
    if held is None:
        held = set()
        _writer_local.held_roots = held
    return held


@contextlib.contextmanager
def _writer_guard(root: Path | None = None):
    root_key = str((root or vault.MEMORY_ROOT).resolve())
    held_roots = _held_writer_roots()
    if root_key in held_roots:
        yield
        return
    owner_thread_id = threading.get_ident()
    with _correction_lock:
        if root_key in _writer_owned_roots:
            raise MemoryProvenanceCorrectionWriterUnavailable(
                "memory_provenance_correction_writer_unavailable"
            )
        _writer_owned_roots[root_key] = owner_thread_id
    lock_path = _writer_lock_path(root)
    locked = False
    marker_held = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        acquired_at = _now_iso()
        with lock_path.open("a+b") as handle:
            try:
                _lock_writer_handle(handle)
                locked = True
            except OSError as exc:
                raise MemoryProvenanceCorrectionWriterUnavailable(
                    "memory_provenance_correction_writer_unavailable"
                ) from exc
            marker_path = _writer_marker_path(root)
            recovered_stale_owner = False
            try:
                marker = json.loads(
                    marker_path.read_text(encoding="utf-8")
                )
                recovered_stale_owner = bool(
                    isinstance(marker, dict)
                    and marker.get("state") == "held"
                    and marker.get("processNonce")
                    != _correction_process_nonce
                )
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                recovered_stale_owner = False
            try:
                _write_writer_marker(
                    root=root,
                    state="held",
                    acquired_at=acquired_at,
                    recovered_stale_owner=recovered_stale_owner,
                )
            except OSError as exc:
                with contextlib.suppress(OSError):
                    _unlock_writer_handle(handle)
                locked = False
                raise MemoryProvenanceCorrectionWriterUnavailable(
                    "memory_provenance_correction_writer_marker_unavailable"
                ) from exc
            marker_held = True
            held_roots.add(root_key)
            try:
                yield
            finally:
                held_roots.discard(root_key)
                if marker_held:
                    with contextlib.suppress(OSError):
                        _write_writer_marker(
                            root=root,
                            state="released",
                            acquired_at=acquired_at,
                            recovered_stale_owner=recovered_stale_owner,
                        )
                if locked:
                    with contextlib.suppress(OSError):
                        _unlock_writer_handle(handle)
    finally:
        with _correction_lock:
            if (
                _writer_owned_roots.get(root_key)
                == owner_thread_id
            ):
                _writer_owned_roots.pop(root_key, None)


def _journal_snapshot(
    root: Path | None = None,
) -> dict[str, Any]:
    path = _journal_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if _chain_head_path(root).exists():
            raise MemoryProvenanceCorrectionJournalIntegrityError(
                "memory_provenance_correction_journal_integrity_failed"
            )
        return {
            "events": [],
            "sequence": 0,
            "lastHash": MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS,
            "integrity": "empty",
            "headState": "missing",
        }
    except (OSError, UnicodeError) as exc:
        raise MemoryProvenanceCorrectionJournalIntegrityError(
            "memory_provenance_correction_journal_unreadable"
        ) from exc
    raw_lines = text.splitlines()
    if any(not line.strip() for line in raw_lines):
        raise MemoryProvenanceCorrectionJournalIntegrityError(
            "memory_provenance_correction_journal_integrity_failed"
        )
    events: list[dict[str, Any]] = []
    legacy_lines: list[str] = []
    chain_hashes: dict[int, str] = {}
    chain_started = False
    sequence = 0
    expected_previous = MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS
    for raw_line in raw_lines:
        try:
            payload = json.loads(raw_line)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MemoryProvenanceCorrectionJournalIntegrityError(
                "memory_provenance_correction_journal_integrity_failed"
            ) from exc
        if not isinstance(payload, dict):
            raise MemoryProvenanceCorrectionJournalIntegrityError(
                "memory_provenance_correction_journal_integrity_failed"
            )
        schema = payload.get("schema")
        if (
            schema == MEMORY_PROVENANCE_CORRECTION_LEGACY_EVENT_SCHEMA
            and not chain_started
        ):
            legacy_lines.append(raw_line)
            events.append(payload)
            continue
        if schema != MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA:
            raise MemoryProvenanceCorrectionJournalIntegrityError(
                "memory_provenance_correction_journal_integrity_failed"
            )
        if not chain_started:
            chain_started = True
            expected_previous = (
                _legacy_anchor(legacy_lines)
                if legacy_lines
                else MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS
            )
        event_sequence = payload.get("sequence")
        previous_hash = payload.get("previousHash")
        event_hash = payload.get("eventHash")
        if (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or event_sequence != sequence + 1
            or previous_hash != expected_previous
            or not isinstance(event_hash, str)
            or len(event_hash) != 64
            or not secrets.compare_digest(
                event_hash,
                _event_hash(payload),
            )
        ):
            raise MemoryProvenanceCorrectionJournalIntegrityError(
                "memory_provenance_correction_journal_integrity_failed"
            )
        sequence = event_sequence
        expected_previous = event_hash
        chain_hashes[sequence] = event_hash
        events.append(payload)
    legacy_hash = (
        _legacy_anchor(legacy_lines)
        if legacy_lines
        else MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS
    )
    head_state = "missing"
    try:
        head = json.loads(
            _chain_head_path(root).read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        head = None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryProvenanceCorrectionJournalIntegrityError(
            "memory_provenance_correction_journal_integrity_failed"
        ) from exc
    if head is not None:
        head_sequence = head.get("sequence") if isinstance(head, dict) else None
        head_hash = head.get("eventHash") if isinstance(head, dict) else None
        if (
            not isinstance(head, dict)
            or set(head)
            != {
                "schema",
                "sequence",
                "eventHash",
                "updatedAt",
                "contentFree",
            }
            or head.get("schema")
            != MEMORY_PROVENANCE_CORRECTION_CHAIN_HEAD_SCHEMA
            or isinstance(head_sequence, bool)
            or not isinstance(head_sequence, int)
            or head_sequence < 0
            or head_sequence > sequence
            or not isinstance(head_hash, str)
            or len(head_hash) != 64
            or head.get("contentFree") is not True
            or not isinstance(head.get("updatedAt"), str)
            or not secrets.compare_digest(
                head_hash,
                (
                    legacy_hash
                    if head_sequence == 0
                    else chain_hashes.get(head_sequence, "")
                ),
            )
        ):
            raise MemoryProvenanceCorrectionJournalIntegrityError(
                "memory_provenance_correction_journal_integrity_failed"
            )
        head_state = (
            "current"
            if head_sequence == sequence
            else "lagging"
        )
    return {
        "events": events,
        "sequence": sequence,
        "lastHash": (
            expected_previous
            if chain_started
            else (
                legacy_hash
            )
        ),
        "integrity": (
            "verified"
            if chain_started
            else ("legacy" if legacy_lines else "empty")
        ),
        "headState": head_state,
    }


def _write_chain_head(
    *,
    root: Path | None,
    sequence: int,
    event_hash: str,
) -> None:
    atomic_json_write(
        _chain_head_path(root),
        {
            "schema": (
                MEMORY_PROVENANCE_CORRECTION_CHAIN_HEAD_SCHEMA
            ),
            "sequence": int(sequence),
            "eventHash": str(event_hash),
            "updatedAt": _now_iso(),
            "contentFree": True,
        },
        durable=True,
    )


def _append_journal_event(
    payload: dict[str, Any],
    *,
    root: Path | None = None,
) -> None:
    path = _journal_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _writer_guard(root):
        with _correction_lock:
            snapshot = _journal_snapshot(root)
            event = {
                "schema": MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA,
                **payload,
                "sequence": int(snapshot["sequence"]) + 1,
                "previousHash": str(snapshot["lastHash"]),
            }
            event["eventHash"] = _event_hash(event)
            with path.open(
                "a",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(_canonical_json(event) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            _write_chain_head(
                root=root,
                sequence=event["sequence"],
                event_hash=event["eventHash"],
            )


def _read_journal_events(
    root: Path | None = None,
) -> list[dict[str, Any]]:
    with _correction_lock:
        return list(_journal_snapshot(root)["events"])


def _journal_records(
    events: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    prepared: dict[str, dict[str, Any]] = {}
    terminal: dict[str, dict[str, Any]] = {}
    for event in events:
        change_id = clean_text(
            str(event.get("changeId") or "")
        )
        if not change_id:
            continue
        event_type = clean_text(
            str(event.get("eventType") or "")
        )
        if event_type == "prepared":
            prepared[change_id] = event
        elif event_type in {"committed", "failed"}:
            terminal[change_id] = event
    return prepared, terminal


def _journal_error(exc: BaseException) -> str:
    code = clean_text(str(exc))
    return (
        code
        if code.startswith("memory_provenance_correction_")
        else "memory_provenance_correction_journal_integrity_failed"
    )


def _writer_public_state(root: Path | None = None) -> str:
    try:
        payload = json.loads(
            _writer_marker_path(root).read_text(encoding="utf-8")
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "unknown"
    if (
        not isinstance(payload, dict)
        or payload.get("schema")
        != MEMORY_PROVENANCE_CORRECTION_WRITER_SCHEMA
        or payload.get("state") not in {"held", "released"}
    ):
        return "unknown"
    return str(payload["state"])


def _as_ids(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    elif value is None:
        items = ()
    else:
        items = vault._as_list(value)  # noqa: SLF001
    return sorted(
        dict.fromkeys(
            cleaned
            for item in items
            if (cleaned := clean_text(str(item)))
        )
    )[:MEMORY_PROVENANCE_CORRECTION_MAX_SOURCES]


def _non_negative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _revision(note: vault.MemoryVaultNote) -> int:
    return max(
        0,
        vault._front_matter_int(  # noqa: SLF001
            note.metadata,
            "revision",
            0,
        ),
    )


def _prepared_matches_note(
    prepared: dict[str, Any],
    note: vault.MemoryVaultNote,
) -> bool:
    return bool(
        clean_text(
            str(
                note.metadata.get(
                    "provenance_correction_change_id"
                )
                or ""
            )
        )
        == clean_text(str(prepared.get("changeId") or ""))
        and _revision(note)
        == _non_negative_int(
            prepared.get("nextRevision"),
            -1,
        )
        and _as_ids(note.metadata.get("derived_from"))
        == _as_ids(prepared.get("newSourceIds"))
        and _as_ids(
            note.metadata.get("origin_derived_from")
        )
        == _as_ids(prepared.get("newOriginSourceIds"))
    )


def _reconcile_journal(
    root: Path | None = None,
) -> list[dict[str, Any]]:
    with _correction_lock:
        snapshot = _journal_snapshot(root)
    if (
        bool(snapshot["events"])
        and snapshot["headState"] != "current"
    ):
        try:
            with _writer_guard(root):
                with _correction_lock:
                    snapshot = _journal_snapshot(root)
                    if (
                        bool(snapshot["events"])
                        and snapshot["headState"] != "current"
                    ):
                        _write_chain_head(
                            root=root,
                            sequence=int(snapshot["sequence"]),
                            event_hash=str(snapshot["lastHash"]),
                        )
                        snapshot = _journal_snapshot(root)
        except MemoryProvenanceCorrectionWriterUnavailable:
            pass
    events = list(snapshot["events"])
    prepared, terminal = _journal_records(events)
    recovered_any = False
    for change_id, intent in prepared.items():
        if change_id in terminal:
            continue
        try:
            with _writer_guard(root):
                with _correction_lock:
                    _latest_prepared, latest_terminal = (
                        _journal_records(
                            _read_journal_events(root)
                        )
                    )
                    if change_id in latest_terminal:
                        continue
                    target = (
                        vault._memory_vault_find_note(  # noqa: SLF001
                            clean_text(
                                str(
                                    intent.get("targetNoteId")
                                    or ""
                                )
                            ),
                            root=root,
                        )
                    )
                    if (
                        target is None
                        or not _prepared_matches_note(
                            intent,
                            target[1],
                        )
                    ):
                        continue
                    recovered = {
                        "eventType": "committed",
                        "changeId": change_id,
                        "committedAt": _now_iso(),
                        "recoveredAfterRestart": True,
                    }
                _append_journal_event(
                    recovered,
                    root=root,
                )
                recovered_any = True
        except OSError:
            continue
    return (
        _read_journal_events(root)
        if recovered_any
        else events
    )


def _committed_records(
    root: Path | None = None,
) -> list[dict[str, Any]]:
    events = _reconcile_journal(root)
    prepared, terminal = _journal_records(events)
    records: list[dict[str, Any]] = []
    for change_id, intent in prepared.items():
        outcome = terminal.get(change_id)
        if (
            not outcome
            or outcome.get("eventType") != "committed"
        ):
            continue
        records.append(
            {
                **intent,
                "committedAt": clean_text(
                    str(outcome.get("committedAt") or "")
                ),
                "recoveredAfterRestart": bool(
                    outcome.get("recoveredAfterRestart")
                ),
            }
        )
    return records


def _public_note(
    note_id: str,
    note_sources: dict[
        str,
        tuple[Path, vault.MemoryVaultNote],
    ],
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    return vault._memory_public_audit_note(  # noqa: SLF001
        note_id,
        note_sources,
        root=root,
        include_internal=False,
    )


def _source_options(
    target_note_id: str,
    nodes: list[Any],
    note_sources: dict[
        str,
        tuple[Path, vault.MemoryVaultNote],
    ],
    *,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    node_by_id = {node.note_id: node for node in nodes}
    output: list[dict[str, Any]] = []
    for source_id in sorted(note_sources):
        if source_id == target_note_id:
            continue
        source_path, source_note = note_sources[source_id]
        source_node = node_by_id.get(source_id)
        if (
            source_node is None
            or not vault._memory_provenance_source_is_grounded(  # noqa: SLF001
                source_node
            )
            or vault._memory_provenance_source_blocker(  # noqa: SLF001
                source_path,
                source_note,
                root=root,
            )
            or vault._memory_provenance_depends_on(  # noqa: SLF001
                node_by_id,
                source_id,
                target_note_id,
            )
        ):
            continue
        public = _public_note(
            source_id,
            note_sources,
            root=root,
        )
        if public is None or bool(public.get("contentHidden")):
            continue
        output.append(
            {
                **public,
                "sourceType": source_node.source_type,
            }
        )
    return output


def _target_is_managed(
    note: vault.MemoryVaultNote,
) -> bool:
    return bool(
        _as_ids(note.metadata.get("derived_from"))
        or (
            clean_text(
                str(
                    note.metadata.get(
                        "provenance_correction_change_id"
                    )
                    or ""
                )
            )
            and _as_ids(
                note.metadata.get("origin_derived_from")
            )
        )
    )


def _target_binding(
    note_id_or_rel_path: str,
    proposed_source_ids: list[str] | tuple[str, ...],
    *,
    proposed_origin_source_ids: (
        list[str] | tuple[str, ...] | None
    ) = None,
    expected_current_source_ids: (
        list[str] | tuple[str, ...] | None
    ) = None,
    expected_current_origin_source_ids: (
        list[str] | tuple[str, ...] | None
    ) = None,
    root: Path | None = None,
) -> dict[str, Any]:
    target = vault._memory_vault_find_note(  # noqa: SLF001
        note_id_or_rel_path,
        root=root,
    )
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    target_path, target_note, target_raw = target
    blocker = vault._memory_provenance_target_blocker(  # noqa: SLF001
        target_path,
        target_note,
        root=root,
    )
    if blocker == "internal_note_not_public":
        return {"ok": False, "error": "note_not_found"}
    if blocker:
        return {
            "ok": False,
            "error": "memory_provenance_correction_protected",
            "reason": blocker,
        }
    if not _target_is_managed(target_note):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_target_ineligible"
            ),
        }

    current_source_ids = _as_ids(
        target_note.metadata.get("derived_from")
    )
    current_origin_ids = _as_ids(
        target_note.metadata.get("origin_derived_from")
    )
    if (
        expected_current_source_ids is not None
        and current_source_ids
        != _as_ids(expected_current_source_ids)
    ):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_changed_since_preview"
            ),
        }
    if (
        expected_current_origin_source_ids is not None
        and current_origin_ids
        != _as_ids(expected_current_origin_source_ids)
    ):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_changed_since_preview"
            ),
        }

    proposed_ids = sorted(
        dict.fromkeys(
            clean_text(str(item))
            for item in proposed_source_ids
            if clean_text(str(item))
        )
    )
    if (
        len(proposed_ids)
        > MEMORY_PROVENANCE_CORRECTION_MAX_SOURCES
    ):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_source_ids_invalid"
            ),
        }
    if proposed_origin_source_ids is None:
        removed_ids = [
            source_id
            for source_id in current_source_ids
            if source_id not in proposed_ids
        ]
        proposed_origin_ids = _as_ids(
            [*current_origin_ids, *removed_ids]
        )
        proposed_origin_ids = [
            source_id
            for source_id in proposed_origin_ids
            if source_id not in proposed_ids
        ]
    else:
        proposed_origin_ids = _as_ids(
            proposed_origin_source_ids
        )
    if (
        proposed_ids == current_source_ids
        and proposed_origin_ids == current_origin_ids
    ):
        return {
            "ok": False,
            "error": "memory_provenance_correction_no_change",
        }

    nodes, note_sources = (
        vault._memory_provenance_audit_nodes(  # noqa: SLF001
            root=root
        )
    )
    node_by_id = {node.note_id: node for node in nodes}
    source_content_hashes: dict[str, str] = {}
    for source_id in proposed_ids:
        source = note_sources.get(source_id)
        if source is None:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_source_unavailable"
                ),
            }
        source_path, source_note = source
        source_blocker = (
            vault._memory_provenance_source_blocker(  # noqa: SLF001
                source_path,
                source_note,
                root=root,
            )
        )
        if source_blocker:
            return {
                "ok": False,
                "error": source_blocker,
            }
        source_node = node_by_id.get(source_id)
        if (
            source_node is None
            or not vault._memory_provenance_source_is_grounded(  # noqa: SLF001
                source_node
            )
        ):
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_source_ungrounded"
                ),
            }
        if (
            source_id == target_note.note_id
            or vault._memory_provenance_depends_on(  # noqa: SLF001
                node_by_id,
                source_id,
                target_note.note_id,
            )
        ):
            return {
                "ok": False,
                "error": "memory_provenance_correction_cycle",
            }
        source_content_hashes[source_id] = (
            source_note.source_hash
        )

    action = "unlink" if not proposed_ids else "relink"
    graph_fingerprint = (
        vault._memory_provenance_audit_fingerprint(  # noqa: SLF001
            nodes
        )
    )
    binding_payload = {
        "targetNoteId": target_note.note_id,
        "targetContentHash": target_note.source_hash,
        "currentSourceIds": current_source_ids,
        "currentOriginSourceIds": current_origin_ids,
        "proposedSourceIds": proposed_ids,
        "proposedOriginSourceIds": proposed_origin_ids,
        "sourceContentHashes": {
            source_id: source_content_hashes[source_id]
            for source_id in sorted(source_content_hashes)
        },
        "graphFingerprint": graph_fingerprint,
        "action": action,
        "currentRevision": _revision(target_note),
    }
    binding_fingerprint = hashlib.sha256(
        json.dumps(
            binding_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True,
        **binding_payload,
        "bindingFingerprint": binding_fingerprint,
        "targetPath": target_path,
        "targetNote": target_note,
        "targetRaw": target_raw,
        "noteSources": note_sources,
    }


def _binding_matches(
    binding: dict[str, Any],
    preview: dict[str, Any],
) -> bool:
    if not binding.get("ok"):
        return False
    scalar_keys = (
        "targetNoteId",
        "targetContentHash",
        "graphFingerprint",
        "bindingFingerprint",
        "action",
    )
    for key in scalar_keys:
        left = clean_text(str(binding.get(key) or ""))
        right = clean_text(str(preview.get(key) or ""))
        if (
            not left
            or not right
            or not secrets.compare_digest(left, right)
        ):
            return False
    list_keys = (
        "currentSourceIds",
        "currentOriginSourceIds",
        "proposedSourceIds",
        "proposedOriginSourceIds",
    )
    if any(
        _as_ids(binding.get(key))
        != _as_ids(preview.get(key))
        for key in list_keys
    ):
        return False
    return (
        int(binding.get("currentRevision") or 0)
        == int(preview.get("currentRevision") or 0)
        and dict(binding.get("sourceContentHashes") or {})
        == dict(preview.get("sourceContentHashes") or {})
    )


def _latest_target_record(
    note_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    records = [
        record
        for record in _committed_records(root)
        if clean_text(str(record.get("targetNoteId") or ""))
        == note_id
    ]
    return records[-1] if records else None


def _record_can_undo(
    record: dict[str, Any],
    note: vault.MemoryVaultNote,
) -> bool:
    return bool(
        record.get("action") in {"relink", "unlink"}
        and clean_text(
            str(
                note.metadata.get(
                    "provenance_correction_change_id"
                )
                or ""
            )
        )
        == clean_text(str(record.get("changeId") or ""))
        and _revision(note)
        == _non_negative_int(
            record.get("nextRevision"),
            -1,
        )
        and _as_ids(note.metadata.get("derived_from"))
        == _as_ids(record.get("newSourceIds"))
        and _as_ids(
            note.metadata.get("origin_derived_from")
        )
        == _as_ids(record.get("newOriginSourceIds"))
    )


def memory_provenance_correction_overview(
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    vault.sync_memory_vault_index(root=root)
    nodes, note_sources = (
        vault._memory_provenance_audit_nodes(  # noqa: SLF001
            root=root
        )
    )
    try:
        records = _committed_records(root)
        with _correction_lock:
            journal_snapshot = _journal_snapshot(root)
        journal_integrity = str(
            journal_snapshot["integrity"]
        )
        journal_chain_ready = bool(
            not journal_snapshot["events"]
            or journal_snapshot["headState"] == "current"
        )
    except MemoryProvenanceCorrectionJournalIntegrityError as exc:
        return {
            "ok": False,
            "schema": (
                MEMORY_PROVENANCE_CORRECTION_OVERVIEW_SCHEMA
            ),
            "error": _journal_error(exc),
            "readOnly": True,
            "autoApply": False,
            "contentSimilarityUsed": False,
            "journalContentFree": True,
            "journalIntegrity": "failed",
            "journalChainReady": False,
            "journalWriterProtected": True,
            "writerState": _writer_public_state(root),
            "relationshipCount": 0,
            "relationships": [],
            "checkedAt": _now_iso(),
        }
    latest_by_target: dict[str, dict[str, Any]] = {}
    for record in records:
        target_id = clean_text(
            str(record.get("targetNoteId") or "")
        )
        if target_id:
            latest_by_target[target_id] = record

    relationships: list[dict[str, Any]] = []
    for node in sorted(nodes, key=lambda item: item.note_id):
        source_entry = note_sources.get(node.note_id)
        if source_entry is None:
            continue
        target_path, target_note = source_entry
        if not _target_is_managed(target_note):
            continue
        target = _public_note(
            node.note_id,
            note_sources,
            root=root,
        )
        if target is None or bool(target.get("contentHidden")):
            continue
        blocker = vault._memory_provenance_target_blocker(  # noqa: SLF001
            target_path,
            target_note,
            root=root,
        )
        current_ids = _as_ids(
            target_note.metadata.get("derived_from")
        )
        current_sources: list[dict[str, Any]] = []
        for source_id in current_ids:
            public_source = _public_note(
                source_id,
                note_sources,
                root=root,
            )
            source_pair = note_sources.get(source_id)
            source_blocker = (
                "memory_provenance_correction_source_unavailable"
                if source_pair is None
                else vault._memory_provenance_source_blocker(  # noqa: SLF001
                    source_pair[0],
                    source_pair[1],
                    root=root,
                )
            )
            current_sources.append(
                {
                    **public_source,
                    "available": True,
                    "blocker": "",
                }
                if (
                    not source_blocker
                    and public_source is not None
                    and not bool(
                        public_source.get("contentHidden")
                    )
                )
                else {
                    "id": source_id,
                    "title": "사용할 수 없는 근거",
                    "type": "unavailable",
                    "contentHidden": True,
                    "available": False,
                    "blocker": source_blocker,
                }
            )
        latest = latest_by_target.get(node.note_id)
        can_undo = bool(
            latest
            and not blocker
            and _record_can_undo(latest, target_note)
        )
        relationships.append(
            {
                "target": target,
                "currentSourceIds": current_ids,
                "currentSources": current_sources,
                "canCorrect": not bool(blocker),
                "correctionBlocker": blocker,
                "latestChange": (
                    {
                        "changeId": latest.get("changeId"),
                        "action": latest.get("action"),
                        "appliedAt": latest.get("committedAt"),
                        "canUndo": can_undo,
                        "undoOfChangeId": latest.get(
                            "undoOfChangeId"
                        )
                        or "",
                    }
                    if latest
                    else None
                ),
            }
        )
    return {
        "ok": True,
        "schema": (
            MEMORY_PROVENANCE_CORRECTION_OVERVIEW_SCHEMA
        ),
        "readOnly": True,
        "autoApply": False,
        "contentSimilarityUsed": False,
        "journalContentFree": True,
        "journalIntegrity": journal_integrity,
        "journalChainReady": journal_chain_ready,
        "journalWriterProtected": True,
        "writerState": _writer_public_state(root),
        "relationshipCount": len(relationships),
        "relationships": relationships,
        "checkedAt": _now_iso(),
    }


def memory_provenance_correction_source_options(
    note_id_or_rel_path: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    vault.sync_memory_vault_index(root=root)
    try:
        _reconcile_journal(root)
    except MemoryProvenanceCorrectionJournalIntegrityError as exc:
        return {"ok": False, "error": _journal_error(exc)}
    target = vault._memory_vault_find_note(  # noqa: SLF001
        note_id_or_rel_path,
        root=root,
    )
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    target_path, target_note, _raw = target
    blocker = vault._memory_provenance_target_blocker(  # noqa: SLF001
        target_path,
        target_note,
        root=root,
    )
    if blocker == "internal_note_not_public":
        return {"ok": False, "error": "note_not_found"}
    if blocker:
        return {
            "ok": False,
            "error": "memory_provenance_correction_protected",
            "reason": blocker,
        }
    if not _target_is_managed(target_note):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_target_ineligible"
            ),
        }
    nodes, note_sources = (
        vault._memory_provenance_audit_nodes(  # noqa: SLF001
            root=root
        )
    )
    return {
        "ok": True,
        "schema": (
            MEMORY_PROVENANCE_CORRECTION_OPTIONS_SCHEMA
        ),
        "readOnly": True,
        "autoApply": False,
        "contentSimilarityUsed": False,
        "selectionMode": "user_selected",
        "target": _public_note(
            target_note.note_id,
            note_sources,
            root=root,
        ),
        "currentSourceIds": _as_ids(
            target_note.metadata.get("derived_from")
        ),
        "sourceOptions": _source_options(
            target_note.note_id,
            nodes,
            note_sources,
            root=root,
        ),
        "unlinkAllowed": True,
        "maxSources": (
            MEMORY_PROVENANCE_CORRECTION_MAX_SOURCES
        ),
        "checkedAt": _now_iso(),
    }


def _prune_tokens(now: float) -> None:
    stale = [
        token
        for token, payload in _correction_tokens.items()
        if float(payload.get("expiresAt") or 0) < now - 60
    ]
    for token in stale:
        _correction_tokens.pop(token, None)


def _preview_from_binding(
    binding: dict[str, Any],
    *,
    kind: str,
    undo_of_change_id: str = "",
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    if not binding.get("ok"):
        return binding
    timestamp = float(now())
    expires_at = (
        timestamp
        + MEMORY_PROVENANCE_CORRECTION_TOKEN_TTL_SECONDS
    )
    token = secrets.token_urlsafe(32)
    change_id = f"provcorr-{secrets.token_hex(12)}"
    root_key = str((root or vault.MEMORY_ROOT).resolve())
    preview = {
        "kind": kind,
        "changeId": change_id,
        "undoOfChangeId": undo_of_change_id,
        "root": root_key,
        "targetNoteId": binding["targetNoteId"],
        "targetContentHash": binding["targetContentHash"],
        "currentSourceIds": list(
            binding["currentSourceIds"]
        ),
        "currentOriginSourceIds": list(
            binding["currentOriginSourceIds"]
        ),
        "proposedSourceIds": list(
            binding["proposedSourceIds"]
        ),
        "proposedOriginSourceIds": list(
            binding["proposedOriginSourceIds"]
        ),
        "sourceContentHashes": dict(
            binding["sourceContentHashes"]
        ),
        "graphFingerprint": binding["graphFingerprint"],
        "bindingFingerprint": binding[
            "bindingFingerprint"
        ],
        "action": binding["action"],
        "currentRevision": binding["currentRevision"],
        "expiresAt": expires_at,
        "used": False,
    }
    with _correction_lock:
        _prune_tokens(timestamp)
        _correction_tokens[token] = preview

    note_sources = binding["noteSources"]
    public_sources = [
        public
        for source_id in binding["proposedSourceIds"]
        if (
            public := _public_note(
                source_id,
                note_sources,
                root=root,
            )
        )
        is not None
    ]
    return {
        "ok": True,
        "schema": (
            MEMORY_PROVENANCE_CORRECTION_PREVIEW_SCHEMA
        ),
        "action": binding["action"],
        "previewKind": kind,
        "changeId": change_id,
        "undoOfChangeId": undo_of_change_id,
        "target": _public_note(
            binding["targetNoteId"],
            note_sources,
            root=root,
        ),
        "currentSourceIds": list(
            binding["currentSourceIds"]
        ),
        "proposedSourceIds": list(
            binding["proposedSourceIds"]
        ),
        "proposedSources": public_sources,
        "consequences": {
            "bodyChanged": False,
            "titleChanged": False,
            "derivedFromReplaced": True,
            "originHistoryPreserved": True,
            "contentFreeJournalWritten": True,
            "searchIndexRebuilt": True,
            "hotContextRebuilt": True,
            "automaticInferenceUsed": False,
        },
        "graphFingerprint": binding["graphFingerprint"],
        "confirmToken": token,
        "expiresAt": expires_at,
    }


def preview_memory_provenance_correction(
    note_id_or_rel_path: str,
    source_note_ids: list[str] | tuple[str, ...],
    *,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    vault.sync_memory_vault_index(root=root)
    try:
        _reconcile_journal(root)
    except MemoryProvenanceCorrectionJournalIntegrityError as exc:
        return {"ok": False, "error": _journal_error(exc)}
    binding = _target_binding(
        note_id_or_rel_path,
        source_note_ids,
        root=root,
    )
    return _preview_from_binding(
        binding,
        kind="correction",
        root=root,
        now=now,
    )


def preview_memory_provenance_correction_undo(
    note_id_or_rel_path: str,
    change_id: str,
    *,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    vault.sync_memory_vault_index(root=root)
    try:
        _reconcile_journal(root)
    except MemoryProvenanceCorrectionJournalIntegrityError as exc:
        return {"ok": False, "error": _journal_error(exc)}
    target = vault._memory_vault_find_note(  # noqa: SLF001
        note_id_or_rel_path,
        root=root,
    )
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    target_note = target[1]
    requested_change_id = clean_text(change_id)
    latest = _latest_target_record(
        target_note.note_id,
        root=root,
    )
    if (
        latest is None
        or clean_text(str(latest.get("changeId") or ""))
        != requested_change_id
        or not _record_can_undo(latest, target_note)
    ):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_undo_unavailable"
            ),
        }
    binding = _target_binding(
        target_note.note_id,
        _as_ids(latest.get("previousSourceIds")),
        proposed_origin_source_ids=_as_ids(
            latest.get("previousOriginSourceIds")
        ),
        expected_current_source_ids=_as_ids(
            latest.get("newSourceIds")
        ),
        expected_current_origin_source_ids=_as_ids(
            latest.get("newOriginSourceIds")
        ),
        root=root,
    )
    return _preview_from_binding(
        binding,
        kind="undo",
        undo_of_change_id=requested_change_id,
        root=root,
        now=now,
    )


def _consume_token(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    expected_kind: str,
    root: Path | None,
    now: Callable[[], float],
) -> dict[str, Any]:
    token = clean_text(confirm_token)
    timestamp = float(now())
    root_key = str((root or vault.MEMORY_ROOT).resolve())
    with _correction_lock:
        _prune_tokens(timestamp)
        preview = _correction_tokens.get(token)
        if preview is None:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_token_invalid"
                ),
            }
        if preview.get("used"):
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_token_reused"
                ),
            }
        preview["used"] = True
        if float(preview.get("expiresAt") or 0) < timestamp:
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_token_expired"
                ),
            }
        if (
            clean_text(str(preview.get("root") or ""))
            != root_key
            or clean_text(str(preview.get("kind") or ""))
            != expected_kind
        ):
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_token_mismatch"
                ),
            }
    target = vault._memory_vault_find_note(  # noqa: SLF001
        note_id_or_rel_path,
        root=root,
    )
    if target is None:
        return {"ok": False, "error": "note_not_found"}
    if (
        clean_text(str(preview.get("targetNoteId") or ""))
        != target[1].note_id
    ):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_token_mismatch"
            ),
        }
    return {"ok": True, "preview": preview}


def _apply_preview(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    expected_kind: str,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    consumed = _consume_token(
        note_id_or_rel_path,
        confirm_token,
        expected_kind=expected_kind,
        root=root,
        now=now,
    )
    if not consumed.get("ok"):
        return consumed
    preview = consumed["preview"]
    if expected_kind == "undo":
        target = vault._memory_vault_find_note(  # noqa: SLF001
            note_id_or_rel_path,
            root=root,
        )
        latest = (
            _latest_target_record(target[1].note_id, root=root)
            if target
            else None
        )
        if (
            latest is None
            or clean_text(
                str(latest.get("changeId") or "")
            )
            != clean_text(
                str(preview.get("undoOfChangeId") or "")
            )
            or not _record_can_undo(latest, target[1])
        ):
            return {
                "ok": False,
                "error": (
                    "memory_provenance_correction_undo_unavailable"
                ),
            }

    binding = _target_binding(
        note_id_or_rel_path,
        _as_ids(preview.get("proposedSourceIds")),
        proposed_origin_source_ids=_as_ids(
            preview.get("proposedOriginSourceIds")
        ),
        expected_current_source_ids=_as_ids(
            preview.get("currentSourceIds")
        ),
        expected_current_origin_source_ids=_as_ids(
            preview.get("currentOriginSourceIds")
        ),
        root=root,
    )
    if not _binding_matches(binding, preview):
        return {
            "ok": False,
            "error": (
                "memory_provenance_correction_changed_since_preview"
            ),
        }

    applied_at = _now_iso(float(now()))
    change_id = clean_text(
        str(preview.get("changeId") or "")
    )
    prepared = {
        "eventType": "prepared",
        "changeId": change_id,
        "action": (
            "undo"
            if expected_kind == "undo"
            else binding["action"]
        ),
        "targetNoteId": binding["targetNoteId"],
        "previousSourceIds": list(
            binding["currentSourceIds"]
        ),
        "previousOriginSourceIds": list(
            binding["currentOriginSourceIds"]
        ),
        "newSourceIds": list(
            binding["proposedSourceIds"]
        ),
        "newOriginSourceIds": list(
            binding["proposedOriginSourceIds"]
        ),
        "previousRevision": binding["currentRevision"],
        "nextRevision": binding["currentRevision"] + 1,
        "undoOfChangeId": clean_text(
            str(preview.get("undoOfChangeId") or "")
        ),
        "actor": "control-page-user",
        "preparedAt": applied_at,
        "contentFree": True,
    }
    updated_note: vault.MemoryVaultNote | None = None
    recovered_during_apply = False
    previous_content_hash = binding["targetContentHash"]
    try:
        with (
            vault._memory_delete_lock,  # noqa: SLF001
            vault._memory_edit_lock,  # noqa: SLF001
            _correction_lock,
        ):
            locked_binding = _target_binding(
                note_id_or_rel_path,
                _as_ids(preview.get("proposedSourceIds")),
                proposed_origin_source_ids=_as_ids(
                    preview.get(
                        "proposedOriginSourceIds"
                    )
                ),
                expected_current_source_ids=_as_ids(
                    preview.get("currentSourceIds")
                ),
                expected_current_origin_source_ids=_as_ids(
                    preview.get(
                        "currentOriginSourceIds"
                    )
                ),
                root=root,
            )
            if not _binding_matches(
                locked_binding,
                preview,
            ):
                return {
                    "ok": False,
                    "error": (
                        "memory_provenance_correction_"
                        "changed_since_preview"
                    ),
                }
            path = Path(locked_binding["targetPath"])
            current_raw = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
            current_note = vault.parse_memory_note(
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
                        "memory_provenance_correction_"
                        "changed_since_preview"
                    ),
                }
            _append_journal_event(prepared, root=root)
            metadata, _body = vault._split_front_matter(  # noqa: SLF001
                current_raw
            )
            if not metadata:
                raise ValueError(
                    "memory_provenance_correction_metadata_required"
                )
            metadata["derived_from"] = list(
                locked_binding["proposedSourceIds"]
            )
            metadata["origin_derived_from"] = list(
                locked_binding["proposedOriginSourceIds"]
            )
            metadata["updated_at"] = applied_at
            metadata["provenance_corrected_at"] = applied_at
            metadata["provenance_correction_method"] = (
                "user-undo"
                if expected_kind == "undo"
                else (
                    "user-unlinked-source-note-ids"
                    if locked_binding["action"] == "unlink"
                    else "user-relinked-source-note-ids"
                )
            )
            metadata["provenance_correction_change_id"] = (
                change_id
            )
            if expected_kind == "undo":
                metadata["provenance_correction_undo_of"] = (
                    clean_text(
                        str(
                            preview.get("undoOfChangeId")
                            or ""
                        )
                    )
                )
            else:
                metadata.pop(
                    "provenance_correction_undo_of",
                    None,
                )
            metadata["revision"] = (
                locked_binding["currentRevision"] + 1
            )
            updated_raw = (
                vault._replace_memory_front_matter(  # noqa: SLF001
                    current_raw,
                    metadata,
                )
            )
            atomic_text_write(
                path,
                updated_raw,
                durable=True,
            )
            updated_note = vault.parse_memory_note(
                path,
                updated_raw,
            )
    except Exception as exc:
        current = vault._memory_vault_find_note(  # noqa: SLF001
            binding["targetNoteId"],
            root=root,
        )
        if (
            current is not None
            and _prepared_matches_note(prepared, current[1])
        ):
            updated_note = current[1]
            recovered_during_apply = True
        else:
            try:
                _append_journal_event(
                    {
                        "eventType": "failed",
                        "changeId": change_id,
                        "failedAt": _now_iso(),
                        "errorCode": (
                            "memory_provenance_correction_failed"
                        ),
                    },
                    root=root,
                )
            except OSError:
                pass
            return {
                "ok": False,
                "schema": (
                    MEMORY_PROVENANCE_CORRECTION_RESULT_SCHEMA
                ),
                "action": (
                    "undo"
                    if expected_kind == "undo"
                    else binding.get("action")
                ),
                "applied": False,
                "error": "memory_provenance_correction_failed",
                "detail": type(exc).__name__,
            }

    cleanup_errors: list[str] = []
    try:
        _append_journal_event(
            {
                "eventType": "committed",
                "changeId": change_id,
                "committedAt": applied_at,
                "recoveredAfterRestart": False,
            },
            root=root,
        )
    except OSError:
        cleanup_errors.append(
            "memory_provenance_correction_journal_commit_failed"
        )
    try:
        memory_version = vault.sync_memory_vault_index(
            root=root
        )
    except Exception:
        memory_version = 0
        cleanup_errors.append(
            "memory_provenance_correction_index_cleanup_failed"
        )
    try:
        vault.refresh_memory_hot_context(root=root)
    except Exception:
        cleanup_errors.append(
            "memory_provenance_correction_hot_context_cleanup_failed"
        )
    try:
        vault.memory_provenance_backfill_preview(root=root)
    except Exception:
        cleanup_errors.append(
            "memory_provenance_correction_audit_refresh_failed"
        )
    result = {
        "ok": not cleanup_errors,
        "schema": (
            MEMORY_PROVENANCE_CORRECTION_RESULT_SCHEMA
        ),
        "action": (
            "undo"
            if expected_kind == "undo"
            else binding["action"]
        ),
        "noteId": (
            updated_note.note_id if updated_note else ""
        ),
        "changeId": change_id,
        "undoOfChangeId": clean_text(
            str(preview.get("undoOfChangeId") or "")
        ),
        "applied": updated_note is not None,
        "recoveredDuringApply": recovered_during_apply,
        "previousSourceIds": list(
            binding["currentSourceIds"]
        ),
        "sourceNoteIds": list(
            binding["proposedSourceIds"]
        ),
        "appliedAt": applied_at,
        "memoryVersion": memory_version,
    }
    if cleanup_errors:
        result.update(
            {
                "error": (
                    "memory_provenance_correction_cleanup_required"
                ),
                "cleanupErrors": cleanup_errors,
            }
        )
    return result


def _apply_with_writer(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    expected_kind: str,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    try:
        with _writer_guard(root):
            _journal_snapshot(root)
            return _apply_preview(
                note_id_or_rel_path,
                confirm_token,
                expected_kind=expected_kind,
                root=root,
                now=now,
            )
    except MemoryProvenanceCorrectionJournalIntegrityError as exc:
        return {
            "ok": False,
            "schema": (
                MEMORY_PROVENANCE_CORRECTION_RESULT_SCHEMA
            ),
            "action": expected_kind,
            "applied": False,
            "error": _journal_error(exc),
        }
    except MemoryProvenanceCorrectionWriterUnavailable as exc:
        return {
            "ok": False,
            "schema": (
                MEMORY_PROVENANCE_CORRECTION_RESULT_SCHEMA
            ),
            "action": expected_kind,
            "applied": False,
            "error": _journal_error(exc),
        }


def apply_memory_provenance_correction(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    return _apply_with_writer(
        note_id_or_rel_path,
        confirm_token,
        expected_kind="correction",
        root=root,
        now=now,
    )


def apply_memory_provenance_correction_undo(
    note_id_or_rel_path: str,
    confirm_token: str,
    *,
    root: Path | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    return _apply_with_writer(
        note_id_or_rel_path,
        confirm_token,
        expected_kind="undo",
        root=root,
        now=now,
    )


__all__ = [
    "MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA",
    "MEMORY_PROVENANCE_CORRECTION_OPTIONS_SCHEMA",
    "MEMORY_PROVENANCE_CORRECTION_OVERVIEW_SCHEMA",
    "MEMORY_PROVENANCE_CORRECTION_PREVIEW_SCHEMA",
    "MEMORY_PROVENANCE_CORRECTION_RESULT_SCHEMA",
    "apply_memory_provenance_correction",
    "apply_memory_provenance_correction_undo",
    "memory_provenance_correction_overview",
    "memory_provenance_correction_source_options",
    "preview_memory_provenance_correction",
    "preview_memory_provenance_correction_undo",
]
