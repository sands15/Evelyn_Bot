from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from .paths import get_runtime_artifacts_root
from .text import clean_text


IDENTITY_REVIEW_QUEUE_PATH = get_runtime_artifacts_root() / "evelyn_identity_review_queue.jsonl"
IDENTITY_REVIEW_EXPORT_DIR = get_runtime_artifacts_root() / "identity_review"

REVIEW_FIELDNAMES = [
    "candidate_id",
    "line_no",
    "review_action",
    "status",
    "labels",
    "source",
    "recorded_at",
    "user_text",
    "assistant_text",
    "notes",
]


def _stable_candidate_id(line_no: int, row: dict[str, Any]) -> str:
    seed = "|".join(
        [
            str(line_no),
            clean_text(str(row.get("source") or "")),
            clean_text(str(row.get("user_text") or "")),
            clean_text(str(row.get("assistant_text") or "")),
        ]
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"id_{line_no:04d}_{digest}"


def _format_timestamp(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return ""
    if number <= 0:
        return ""
    try:
        return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def read_identity_review_rows(path: Path | None = None, *, include_all: bool = False) -> list[dict[str, str]]:
    source_path = path or IDENTITY_REVIEW_QUEUE_PATH
    try:
        lines = source_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    rows: list[dict[str, str]] = []
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        status = clean_text(str(raw.get("status") or "review_candidate"))
        if not include_all and status not in {"", "review_candidate", "pending"}:
            continue
        labels_raw = raw.get("labels")
        if isinstance(labels_raw, list):
            labels = ", ".join(clean_text(str(item)) for item in labels_raw if clean_text(str(item)))
        else:
            labels = clean_text(str(labels_raw or ""))
        rows.append(
            {
                "candidate_id": _stable_candidate_id(line_no, raw),
                "line_no": str(line_no),
                "review_action": "",
                "status": status or "review_candidate",
                "labels": labels,
                "source": clean_text(str(raw.get("source") or "")),
                "recorded_at": _format_timestamp(raw.get("recorded_at")),
                "user_text": clean_text(str(raw.get("user_text") or "")),
                "assistant_text": clean_text(str(raw.get("assistant_text") or "")),
                "notes": "",
            }
        )
    return rows


def write_identity_review_tsv(rows: list[dict[str, str]], path: Path) -> None:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDNAMES, delimiter="\t", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    _atomic_text_write(path, "\ufeff" + stream.getvalue())


def _escape_md_cell(text: str) -> str:
    return clean_text(text).replace("|", "\\|")


def write_identity_review_markdown(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    label_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in rows:
        status_counts[row.get("status", "") or "review_candidate"] += 1
        for label in (row.get("labels") or "").split(","):
            label = clean_text(label)
            if label:
                label_counts[label] += 1

    lines = [
        "# Evelyn Identity Review",
        "",
        "Use `review_action` values like `accept`, `hold`, or `reject` in the TSV after inspection.",
        "",
        "## Summary",
        "",
        f"- Candidates: {len(rows)}",
    ]
    if status_counts:
        lines.append(f"- Status: {', '.join(f'{key}={value}' for key, value in sorted(status_counts.items()))}")
    if label_counts:
        lines.append(f"- Labels: {', '.join(f'{key}={value}' for key, value in sorted(label_counts.items()))}")

    lines.extend(
        [
            "",
            "## Candidates",
            "",
            "| ID | Action | Labels | User Feedback | Assistant Text | Notes |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _escape_md_cell(row["candidate_id"]),
                    "",
                    _escape_md_cell(row["labels"]),
                    _escape_md_cell(row["user_text"]),
                    _escape_md_cell(row["assistant_text"]),
                    "",
                ]
            )
            + " |"
        )
    _atomic_text_write(path, "\n".join(lines) + "\n")


def write_identity_review_summary(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    labels: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    for row in rows:
        statuses[row.get("status", "") or "review_candidate"] += 1
        sources[row.get("source", "") or "unknown"] += 1
        for label in (row.get("labels") or "").split(","):
            label = clean_text(label)
            if label:
                labels[label] += 1
    payload = {
        "candidate_count": len(rows),
        "labels": dict(sorted(labels.items())),
        "statuses": dict(sorted(statuses.items())),
        "sources": dict(sorted(sources.items())),
    }
    _atomic_text_write(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _path_is_local_regular(path: Path, *, directory: bool = False) -> bool:
    for candidate in (path, *path.parents):
        try:
            junction = getattr(candidate, "is_junction", None)
            if candidate.exists() and (
                candidate.is_symlink()
                or bool(callable(junction) and junction())
            ):
                return False
        except OSError:
            return False
    try:
        return not path.exists() or (path.is_dir() if directory else path.is_file())
    except OSError:
        return False


def _atomic_text_write(path: Path, text: str) -> None:
    if not _path_is_local_regular(path):
        raise OSError("identity_review_path_unsafe")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _strict_queue_rows(path: Path) -> list[dict[str, Any]]:
    if not _path_is_local_regular(path):
        raise OSError("identity_review_path_unsafe")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("identity_review_row_invalid")
        rows.append(value)
    return rows


def cleanup_identity_review_artifacts(
    *,
    time_predicate: Callable[[dict[str, Any]], bool],
    lineage_predicate: Callable[[dict[str, Any]], bool],
    queue_path: Path | None = None,
    registered_export_dirs: Iterable[Path] = (),
    allowed_export_root: Path | None = None,
) -> tuple[int, int, int]:
    """Remove only time-selected rows with explicit, exactly matching lineage."""

    target = Path(queue_path or IDENTITY_REVIEW_QUEUE_PATH)
    export_dirs = tuple(Path(item) for item in registered_export_dirs)
    if not callable(time_predicate) or not callable(lineage_predicate):
        return (0, 1, 1)
    if export_dirs:
        if allowed_export_root is None:
            return (0, 1, 1)
        root = Path(allowed_export_root)
        if not _path_is_local_regular(root, directory=True):
            return (0, 1, 1)
        try:
            root_resolved = root.resolve(strict=False)
            for directory in export_dirs:
                if not _path_is_local_regular(directory, directory=True):
                    return (0, 1, 1)
                directory.resolve(strict=False).relative_to(root_resolved)
        except (OSError, ValueError):
            return (0, 1, 1)
    removed = 0
    manual = 0
    try:
        rows = _strict_queue_rows(target)
        survivors: list[dict[str, Any]] = []
        for row in rows:
            if not bool(time_predicate(dict(row))):
                survivors.append(row)
                continue
            lineage = row.get("lineage")
            if not isinstance(lineage, dict) or not lineage:
                survivors.append(row)
                manual += 1
                continue
            if bool(lineage_predicate(dict(lineage))):
                removed += 1
            else:
                survivors.append(row)
        if removed:
            body = "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in survivors
            )
            _atomic_text_write(target, body)
        if export_dirs:
            for directory in export_dirs:
                export_identity_review(
                    input_path=target,
                    output_dir=directory,
                    include_all=True,
                )
                expected_count = len(
                    read_identity_review_rows(target, include_all=True)
                )
                summary = json.loads(
                    (directory / "evelyn_identity_review_summary.json").read_text(
                        encoding="utf-8"
                    )
                )
                with (directory / "evelyn_identity_review.tsv").open(
                    "r", encoding="utf-8-sig", newline=""
                ) as stream:
                    tsv_count = sum(1 for _row in csv.DictReader(stream, delimiter="\t"))
                if (
                    summary.get("candidate_count") != expected_count
                    or tsv_count != expected_count
                    or not (directory / "evelyn_identity_review.md").is_file()
                ):
                    raise ValueError("identity_review_export_mismatch")
        fresh = _strict_queue_rows(target)
        remaining = 0
        for row in fresh:
            if not bool(time_predicate(dict(row))):
                continue
            lineage = row.get("lineage")
            if isinstance(lineage, dict) and lineage and bool(
                lineage_predicate(dict(lineage))
            ):
                remaining += 1
    except Exception:
        return (removed, 1, manual + 1)
    return (removed, remaining, manual)


def export_identity_review(
    *,
    input_path: Path | None = None,
    output_dir: Path | None = None,
    include_all: bool = False,
) -> dict[str, Any]:
    rows = read_identity_review_rows(input_path, include_all=include_all)
    target_dir = output_dir or IDENTITY_REVIEW_EXPORT_DIR
    tsv_path = target_dir / "evelyn_identity_review.tsv"
    md_path = target_dir / "evelyn_identity_review.md"
    summary_path = target_dir / "evelyn_identity_review_summary.json"
    write_identity_review_tsv(rows, tsv_path)
    write_identity_review_markdown(rows, md_path)
    write_identity_review_summary(rows, summary_path)
    return {
        "candidate_count": len(rows),
        "tsv": str(tsv_path),
        "markdown": str(md_path),
        "summary": str(summary_path),
    }
