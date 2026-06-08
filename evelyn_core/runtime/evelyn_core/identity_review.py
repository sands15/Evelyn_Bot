from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_FIELDNAMES, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
