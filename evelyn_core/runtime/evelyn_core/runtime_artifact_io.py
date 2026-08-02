from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable


DEFAULT_REPLACE_ATTEMPTS = 6
DEFAULT_RETRY_DELAY_SEC = 0.02

ReplaceCallable = Callable[
    [
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
        str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ],
    None,
]
SyncCallable = Callable[[int], None]
DirectorySyncCallable = Callable[[Path], None]

_DEFAULT_REPLACE = os.replace
_MOVEFILE_REPLACE_EXISTING = 0x00000001
_MOVEFILE_WRITE_THROUGH = 0x00000008


class DurableCommitError(OSError):
    """A replacement committed but its directory metadata did not sync."""

    def __init__(self) -> None:
        super().__init__("durable_parent_sync_failed")


def _windows_write_through_replace(source: Path, target: Path) -> None:
    """Atomically replace ``target`` through the durable Win32 primitive."""

    import ctypes
    from ctypes import wintypes

    move_file_ex = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    ).MoveFileExW
    move_file_ex.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    move_file_ex.restype = wintypes.BOOL
    flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
    if not move_file_ex(str(source), str(target), flags):
        raise ctypes.WinError(ctypes.get_last_error())


def _sync_parent_directory(directory: Path) -> None:
    """Persist a POSIX rename by syncing its containing directory."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_once(
    source: Path,
    target: Path,
    *,
    replace: ReplaceCallable,
    durable: bool,
) -> None:
    if durable and os.name == "nt" and replace is _DEFAULT_REPLACE:
        _windows_write_through_replace(source, target)
        return
    replace(source, target)


def _persist_parent_metadata(
    directory: Path,
    *,
    durable: bool,
    directory_sync: DirectorySyncCallable | None,
) -> None:
    if not durable:
        return
    if directory_sync is not None:
        try:
            directory_sync(directory)
        except OSError:
            raise DurableCommitError() from None
        return
    if os.name != "nt":
        try:
            _sync_parent_directory(directory)
        except OSError:
            raise DurableCommitError() from None


def _replace_with_retry(
    temporary: Path,
    target: Path,
    *,
    replace: ReplaceCallable,
    sleep: Callable[[float], None],
    attempts: int,
    retry_delay_sec: float,
    durable: bool,
    directory_sync: DirectorySyncCallable | None,
) -> None:
    maximum_attempts = max(1, int(attempts))
    delay = max(0.0, float(retry_delay_sec))
    for attempt in range(maximum_attempts):
        try:
            _replace_once(
                temporary,
                target,
                replace=replace,
                durable=durable,
            )
            break
        except PermissionError:
            if attempt + 1 >= maximum_attempts:
                raise
            sleep(delay * (2**attempt))
    _persist_parent_metadata(
        target.parent,
        durable=durable,
        directory_sync=directory_sync,
    )


def atomic_json_write(
    path: Path,
    payload: dict[str, Any],
    *,
    replace: ReplaceCallable = _DEFAULT_REPLACE,
    sync: SyncCallable = os.fsync,
    directory_sync: DirectorySyncCallable | None = None,
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
    ``durable=True`` syncs the temporary file, performs a write-through replace
    on Windows, and syncs the parent directory after replacement on POSIX.
    ``directory_sync`` is an optional deterministic test/integration hook; it
    runs after a successful replacement on every platform.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
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
        _replace_with_retry(
            temporary,
            target,
            replace=replace,
            sleep=sleep,
            attempts=attempts,
            retry_delay_sec=retry_delay_sec,
            durable=durable,
            directory_sync=directory_sync,
        )
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_bounded_json(path: Path, *, maximum_bytes: int) -> Any:
    """Read one JSON artifact without allowing an untrusted file to stall."""

    limit = max(1, int(maximum_bytes))
    with Path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("artifact_too_large")
    return json.loads(raw)


def atomic_text_write(
    path: Path,
    text: str,
    *,
    replace: ReplaceCallable = _DEFAULT_REPLACE,
    sync: SyncCallable = os.fsync,
    directory_sync: DirectorySyncCallable | None = None,
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
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(str(text))
            if durable:
                handle.flush()
                sync(handle.fileno())
        _replace_with_retry(
            temporary,
            target,
            replace=replace,
            sleep=sleep,
            attempts=attempts,
            retry_delay_sec=retry_delay_sec,
            durable=durable,
            directory_sync=directory_sync,
        )
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "DEFAULT_REPLACE_ATTEMPTS",
    "DEFAULT_RETRY_DELAY_SEC",
    "DurableCommitError",
    "atomic_json_write",
    "atomic_text_write",
]
