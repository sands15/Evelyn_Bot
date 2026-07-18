from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LOG_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 4


@dataclass(frozen=True)
class LogRotationResult:
    rotated: bool
    previous_bytes: int
    backup_path: Path | None = None


def rotate_log_if_needed(
    path: Path,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    incoming_bytes: int = 0,
) -> LogRotationResult:
    log_path = Path(path)
    try:
        current_bytes = int(log_path.stat().st_size)
    except FileNotFoundError:
        return LogRotationResult(rotated=False, previous_bytes=0)
    if max_bytes <= 0 or current_bytes + max(0, int(incoming_bytes)) <= int(max_bytes):
        return LogRotationResult(rotated=False, previous_bytes=current_bytes)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    keep = max(0, int(backup_count))
    if keep == 0:
        log_path.unlink()
        return LogRotationResult(rotated=True, previous_bytes=current_bytes)

    oldest = log_path.with_name(f"{log_path.name}.{keep}")
    oldest.unlink(missing_ok=True)
    for index in range(keep - 1, 0, -1):
        source = log_path.with_name(f"{log_path.name}.{index}")
        if source.exists():
            os.replace(source, log_path.with_name(f"{log_path.name}.{index + 1}"))
    backup_path = log_path.with_name(f"{log_path.name}.1")
    os.replace(log_path, backup_path)
    return LogRotationResult(rotated=True, previous_bytes=current_bytes, backup_path=backup_path)


def append_bounded_log(
    path: Path,
    text: str,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
    encoding: str = "utf-8",
) -> LogRotationResult:
    payload = str(text)
    encoded_size = len(payload.encode(encoding, errors="replace"))
    result = rotate_log_if_needed(
        path,
        max_bytes=max_bytes,
        backup_count=backup_count,
        incoming_bytes=encoded_size,
    )
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding=encoding) as handle:
        handle.write(payload)
    return result


__all__ = [
    "DEFAULT_LOG_BACKUP_COUNT",
    "DEFAULT_LOG_MAX_BYTES",
    "LogRotationResult",
    "append_bounded_log",
    "rotate_log_if_needed",
]
