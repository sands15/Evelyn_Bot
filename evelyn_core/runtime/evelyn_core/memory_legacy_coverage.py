from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .config import MEMORY_ROOT
from .memory_legacy_evidence import (
    MEMORY_LEGACY_EVIDENCE_SCHEMA,
    validate_legacy_memory_evidence,
)
from .memory_prompt_policy import MEMORY_CONTEXT_USE_POLICY
from .text import clean_text


LEGACY_MEMORY_CONTEXT_COVERAGE_SCHEMA = "memory.legacy-context-coverage.v1"
_MAX_COVERAGE_FILE_BYTES = 16 * 1024 * 1024
_EVIDENCE_FIELDS = {
    "evidence_id",
    "evidence_kind",
    "source_turn_id",
    "source_evidence_ids",
    "source_turn_ids",
}


def _empty_bucket() -> dict[str, int]:
    return {
        "totalStoredItemCount": 0,
        "attributedStoredItemCount": 0,
        "confirmOnlyStoredItemCount": 0,
    }


@dataclass
class _CoverageAccumulator:
    scope_count: int = 0
    scanned_file_count: int = 0
    unreadable_file_count: int = 0
    oversized_file_count: int = 0
    malformed_file_count: int = 0
    malformed_line_count: int = 0
    unsafe_location_count: int = 0
    missing_evidence_item_count: int = 0
    invalid_evidence_item_count: int = 0
    by_kind: dict[str, dict[str, int]] = field(default_factory=dict)
    by_scope_type: dict[str, dict[str, int]] = field(default_factory=dict)
    by_storage: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(
        self,
        *,
        kind: str,
        scope_type: str,
        storage: str,
        attributed: bool,
        confirmation_reason: str = "",
    ) -> None:
        for buckets, key in (
            (self.by_kind, kind),
            (self.by_scope_type, scope_type),
            (self.by_storage, storage),
        ):
            bucket = buckets.setdefault(key, _empty_bucket())
            bucket["totalStoredItemCount"] += 1
            if attributed:
                bucket["attributedStoredItemCount"] += 1
            else:
                bucket["confirmOnlyStoredItemCount"] += 1
        if confirmation_reason == "missing_evidence":
            self.missing_evidence_item_count += 1
        elif confirmation_reason == "invalid_evidence":
            self.invalid_evidence_item_count += 1

    @property
    def total_count(self) -> int:
        return sum(
            bucket["totalStoredItemCount"]
            for bucket in self.by_kind.values()
        )

    @property
    def attributed_count(self) -> int:
        return sum(
            bucket["attributedStoredItemCount"]
            for bucket in self.by_kind.values()
        )


def _bucket_rows(buckets: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    return [
        {"key": key, **dict(values)}
        for key, values in sorted(buckets.items())
    ]


def _is_confined(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError):
        return False
    return True


def _read_text(
    path: Path,
    *,
    root: Path,
    accumulator: _CoverageAccumulator,
) -> tuple[str, str]:
    try:
        if path.is_symlink() or not _is_confined(path, root):
            accumulator.unsafe_location_count += 1
            return "unsafe", ""
        if not path.exists():
            return "missing", ""
        if not path.is_file():
            return "missing", ""
        if path.stat().st_size > _MAX_COVERAGE_FILE_BYTES:
            accumulator.oversized_file_count += 1
            return "oversized", ""
        accumulator.scanned_file_count += 1
        return "read", path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        accumulator.unreadable_file_count += 1
        return "unreadable", ""


def _has_evidence_claim(row: dict[str, Any]) -> bool:
    return any(key in row for key in _EVIDENCE_FIELDS)


def _record_summary(
    scope_dir: Path,
    *,
    scope_type: str,
    root: Path,
    accumulator: _CoverageAccumulator,
) -> None:
    summary_status, summary_raw = _read_text(
        scope_dir / "rolling_summary.txt",
        root=root,
        accumulator=accumulator,
    )
    summary = summary_raw.strip()
    if summary_status != "read" or not summary:
        return

    provenance_status, provenance_raw = _read_text(
        scope_dir / "rolling_summary.provenance.json",
        root=root,
        accumulator=accumulator,
    )
    provenance: dict[str, Any] = {}
    metadata_valid = False
    if provenance_status == "read":
        try:
            parsed = json.loads(provenance_raw)
        except json.JSONDecodeError:
            accumulator.malformed_file_count += 1
        else:
            if isinstance(parsed, dict):
                provenance = parsed
                expected_hash = hashlib.sha256(
                    summary.encode("utf-8", errors="ignore")
                ).hexdigest()
                metadata_valid = bool(
                    provenance.get("schema")
                    == MEMORY_LEGACY_EVIDENCE_SCHEMA
                    and clean_text(
                        str(provenance.get("content_sha256") or "")
                    )
                    == expected_hash
                    and validate_legacy_memory_evidence(
                        provenance,
                        expected_kind="derived_summary",
                    )
                    is not None
                )
                if not metadata_valid:
                    accumulator.malformed_file_count += 1
            else:
                accumulator.malformed_file_count += 1

    confirmation_reason = ""
    if not metadata_valid:
        confirmation_reason = (
            "missing_evidence"
            if provenance_status == "missing"
            else "invalid_evidence"
        )
    accumulator.record(
        kind="summary",
        scope_type=scope_type,
        storage="hot",
        attributed=metadata_valid,
        confirmation_reason=confirmation_reason,
    )


def _record_jsonl(
    path: Path,
    *,
    expected_kind: str,
    item_kind: str,
    scope_type: str,
    storage: str,
    min_text_chars: int,
    root: Path,
    accumulator: _CoverageAccumulator,
) -> None:
    status, raw = _read_text(
        path,
        root=root,
        accumulator=accumulator,
    )
    if status != "read":
        return
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            accumulator.malformed_line_count += 1
            continue
        if not isinstance(parsed, dict):
            accumulator.malformed_line_count += 1
            continue
        text = clean_text(str(parsed.get("text") or ""))
        if len(text) < min_text_chars:
            continue
        evidence = validate_legacy_memory_evidence(
            parsed,
            expected_kind=expected_kind,
        )
        accumulator.record(
            kind=item_kind,
            scope_type=scope_type,
            storage=storage,
            attributed=evidence is not None,
            confirmation_reason=(
                ""
                if evidence is not None
                else (
                    "invalid_evidence"
                    if _has_evidence_claim(parsed)
                    else "missing_evidence"
                )
            ),
        )


def _scope_directories(
    root: Path,
    accumulator: _CoverageAccumulator,
) -> list[tuple[Path, str]]:
    scopes: list[tuple[Path, str]] = []
    try:
        guild_dirs = sorted(root.glob("guild_*"), key=lambda path: path.name)
    except OSError:
        return scopes
    for guild_dir in guild_dirs:
        try:
            if guild_dir.is_symlink() or not _is_confined(guild_dir, root):
                accumulator.unsafe_location_count += 1
                continue
            if not guild_dir.is_dir():
                continue
        except OSError:
            accumulator.unreadable_file_count += 1
            continue
        scopes.append((guild_dir, "guild"))
        try:
            children = sorted(guild_dir.iterdir(), key=lambda path: path.name)
        except OSError:
            accumulator.unreadable_file_count += 1
            continue
        for child in children:
            scope_type = next(
                (
                    candidate
                    for candidate in ("room", "person", "session")
                    if child.name.startswith(candidate + "_")
                ),
                "",
            )
            if not scope_type:
                continue
            try:
                if child.is_symlink() or not _is_confined(child, root):
                    accumulator.unsafe_location_count += 1
                    continue
                if child.is_dir():
                    scopes.append((child, scope_type))
            except OSError:
                accumulator.unreadable_file_count += 1
    return scopes


def _record_scope(
    scope_dir: Path,
    *,
    scope_type: str,
    root: Path,
    accumulator: _CoverageAccumulator,
) -> None:
    accumulator.scope_count += 1
    _record_summary(
        scope_dir,
        scope_type=scope_type,
        root=root,
        accumulator=accumulator,
    )
    for filename, expected_kind, item_kind, min_chars in (
        ("raw_transcript.jsonl", "conversation_turn", "raw", 1),
        ("durable_facts.jsonl", "derived_fact", "fact", 1),
        ("open_questions.jsonl", "derived_question", "question", 2),
    ):
        _record_jsonl(
            scope_dir / filename,
            expected_kind=expected_kind,
            item_kind=item_kind,
            scope_type=scope_type,
            storage="hot",
            min_text_chars=min_chars,
            root=root,
            accumulator=accumulator,
        )
    vault_dir = scope_dir / "vault"
    _record_jsonl(
        vault_dir / "facts.jsonl",
        expected_kind="derived_fact",
        item_kind="fact",
        scope_type=scope_type,
        storage="vault",
        min_text_chars=1,
        root=root,
        accumulator=accumulator,
    )
    _record_jsonl(
        vault_dir / "questions.jsonl",
        expected_kind="derived_question",
        item_kind="question",
        scope_type=scope_type,
        storage="vault",
        min_text_chars=2,
        root=root,
        accumulator=accumulator,
    )
    raw_dir = vault_dir / "raw"
    try:
        if raw_dir.is_symlink() or (
            raw_dir.exists() and not _is_confined(raw_dir, root)
        ):
            accumulator.unsafe_location_count += 1
            raw_paths = []
        else:
            raw_paths = sorted(
                raw_dir.glob("*.jsonl"),
                key=lambda path: path.name,
            )
    except OSError:
        accumulator.unreadable_file_count += 1
        raw_paths = []
    for path in raw_paths:
        _record_jsonl(
            path,
            expected_kind="conversation_turn",
            item_kind="raw",
            scope_type=scope_type,
            storage="vault",
            min_text_chars=1,
            root=root,
            accumulator=accumulator,
        )


def summarize_legacy_memory_context_coverage(
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    memory_root = Path(root or MEMORY_ROOT)
    accumulator = _CoverageAccumulator()
    try:
        root_exists = memory_root.exists()
        root_is_symlink = memory_root.is_symlink()
    except OSError:
        accumulator.unreadable_file_count += 1
        root_exists = False
        root_is_symlink = False
    if root_exists and not root_is_symlink:
        for scope_dir, scope_type in _scope_directories(
            memory_root,
            accumulator,
        ):
            _record_scope(
                scope_dir,
                scope_type=scope_type,
                root=memory_root,
                accumulator=accumulator,
            )
    elif root_is_symlink:
        accumulator.unsafe_location_count += 1

    total_count = accumulator.total_count
    attributed_count = accumulator.attributed_count
    confirm_only_count = max(0, total_count - attributed_count)
    if not total_count:
        grounding_state = "empty"
    elif not confirm_only_count:
        grounding_state = "attributed"
    elif attributed_count:
        grounding_state = "partial"
    else:
        grounding_state = "unattributed"
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    return {
        "schema": LEGACY_MEMORY_CONTEXT_COVERAGE_SCHEMA,
        "policy": MEMORY_CONTEXT_USE_POLICY,
        "readOnly": True,
        "contentFree": True,
        "identifiersIncluded": False,
        "storageLocationsIncluded": False,
        "transcriptsIncluded": False,
        "itemSemantics": "stored_rows_and_summaries_not_prompt_selection",
        "mayContainMirrors": True,
        "groundingState": grounding_state,
        "totalStoredItemCount": total_count,
        "attributedStoredItemCount": attributed_count,
        "confirmOnlyStoredItemCount": confirm_only_count,
        "attributionRatio": (
            round(attributed_count / total_count, 6)
            if total_count
            else 1.0
        ),
        "missingEvidenceItemCount": accumulator.missing_evidence_item_count,
        "invalidEvidenceItemCount": accumulator.invalid_evidence_item_count,
        "scopeCount": accumulator.scope_count,
        "scannedFileCount": accumulator.scanned_file_count,
        "unreadableFileCount": accumulator.unreadable_file_count,
        "oversizedFileCount": accumulator.oversized_file_count,
        "malformedFileCount": accumulator.malformed_file_count,
        "malformedLineCount": accumulator.malformed_line_count,
        "unsafeLocationCount": accumulator.unsafe_location_count,
        "byKind": _bucket_rows(accumulator.by_kind),
        "byScopeType": _bucket_rows(accumulator.by_scope_type),
        "byStorage": _bucket_rows(accumulator.by_storage),
        "checkedAt": checked_at.isoformat().replace("+00:00", "Z"),
    }


__all__ = [
    "LEGACY_MEMORY_CONTEXT_COVERAGE_SCHEMA",
    "summarize_legacy_memory_context_coverage",
]
