from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable


DEFAULT_REPLACE_ATTEMPTS = 6
DEFAULT_RETRY_DELAY_SEC = 0.02


def atomic_json_write(
    path: Path,
    payload: dict[str, Any],
    *,
    replace: Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None] = os.replace,
    sync: Callable[[int], None] = os.fsync,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = DEFAULT_REPLACE_ATTEMPTS,
    retry_delay_sec: float = DEFAULT_RETRY_DELAY_SEC,
    durable: bool = False,
) -> None:
    """Write JSON atomically, tolerating short Windows reader locks.

    Docker Desktop and antivirus scanners can briefly open a bind-mounted
    artifact without delete sharing. Windows then rejects ``os.replace`` with
    ``PermissionError`` even though both paths are writable. Retrying only that
    transient class preserves atomic readers without masking other I/O errors.
    ``durable=True`` flushes and syncs the temporary file before replacement.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    maximum_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_sec))
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            if durable:
                handle.flush()
                sync(handle.fileno())
        for attempt in range(maximum_attempts):
            try:
                replace(temporary, target)
                return
            except PermissionError:
                if attempt + 1 >= maximum_attempts:
                    raise
                sleep(delay * (2**attempt))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def atomic_text_write(
    path: Path,
    text: str,
    *,
    replace: Callable[[str | bytes | os.PathLike[str] | os.PathLike[bytes], str | bytes | os.PathLike[str] | os.PathLike[bytes]], None] = os.replace,
    sync: Callable[[int], None] = os.fsync,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = DEFAULT_REPLACE_ATTEMPTS,
    retry_delay_sec: float = DEFAULT_RETRY_DELAY_SEC,
    durable: bool = False,
) -> None:
    """Write UTF-8 text through a same-directory atomic replacement."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    maximum_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_sec))
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(str(text))
            if durable:
                handle.flush()
                sync(handle.fileno())
        for attempt in range(maximum_attempts):
            try:
                replace(temporary, target)
                return
            except PermissionError:
                if attempt + 1 >= maximum_attempts:
                    raise
                sleep(delay * (2**attempt))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "DEFAULT_REPLACE_ATTEMPTS",
    "DEFAULT_RETRY_DELAY_SEC",
    "atomic_json_write",
    "atomic_text_write",
]
