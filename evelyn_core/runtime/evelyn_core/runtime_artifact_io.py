from __future__ import annotations

import json
import os
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


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
_ARTIFACT_PROCESS_SCOPE = threading.local()


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


def _temporary_path(
    target: Path,
    *,
    temporary_token: str | None,
) -> Path:
    token = str(temporary_token or uuid.uuid4().hex)
    if (
        not token
        or len(token) > 80
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in token)
    ):
        raise ValueError("artifact_temporary_token_invalid")
    return target.with_name(
        f".{target.name}.{os.getpid()}.{token}.tmp"
    )


def _atomic_json_write_direct(
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
    temporary_token: str | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(
        target,
        temporary_token=temporary_token,
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


def _scope_stack() -> list[tuple[Any, float, Callable[[], float]]]:
    stack = getattr(_ARTIFACT_PROCESS_SCOPE, "stack", None)
    if stack is None:
        stack = []
        _ARTIFACT_PROCESS_SCOPE.stack = stack
    return stack


@contextmanager
def durable_artifact_process_scope(
    process: Any | None,
    *,
    timeout_sec: float,
) -> Iterator[None]:
    """Route default artifact primitives through one bounded worker.

    The scope is thread-local so synchronous production callbacks executed by
    a completed-turn commit inherit the same absolute deadline. Nested scopes
    may only reuse the same worker and can shorten, never extend, the deadline.
    """

    if process is None:
        yield
        return
    clock = getattr(process, "monotonic", time.monotonic)
    if not callable(clock):
        clock = time.monotonic
    deadline = float(clock()) + max(0.01, float(timeout_sec))
    stack = _scope_stack()
    if stack:
        parent_process, parent_deadline, parent_clock = stack[-1]
        if parent_process is not process:
            raise RuntimeError("durable_artifact_process_scope_mismatch")
        deadline = min(deadline, parent_deadline)
        clock = parent_clock
    stack.append((process, deadline, clock))
    try:
        yield
    finally:
        popped = stack.pop()
        if popped[0] is not process:
            raise RuntimeError("durable_artifact_process_scope_corrupt")


def _active_artifact_process() -> tuple[Any, float] | None:
    stack = _scope_stack()
    if not stack:
        return None
    process, deadline, clock = stack[-1]
    remaining = float(deadline) - float(clock())
    if remaining <= 0.0:
        from .durable_artifact_process import (
            DurableArtifactProcessTimeout,
        )

        raise DurableArtifactProcessTimeout()
    return process, remaining


def _uses_default_hooks(
    *,
    replace: ReplaceCallable,
    sync: SyncCallable,
    directory_sync: DirectorySyncCallable | None,
    sleep: Callable[[float], None],
) -> bool:
    return bool(
        replace is _DEFAULT_REPLACE
        and sync is os.fsync
        and directory_sync is None
        and sleep is time.sleep
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
    """Write JSON atomically, using the active killable worker when scoped."""

    active = _active_artifact_process()
    if active is not None and _uses_default_hooks(
        replace=replace,
        sync=sync,
        directory_sync=directory_sync,
        sleep=sleep,
    ):
        process, remaining = active
        process.write_json(
            Path(path),
            payload,
            timeout_sec=remaining,
            durable=bool(durable),
            attempts=max(1, int(attempts)),
            retry_delay_sec=max(0.0, float(retry_delay_sec)),
        )
        return
    _atomic_json_write_direct(
        path,
        payload,
        replace=replace,
        sync=sync,
        directory_sync=directory_sync,
        sleep=sleep,
        attempts=attempts,
        retry_delay_sec=retry_delay_sec,
        durable=durable,
    )


def _read_bounded_text_direct(
    path: Path,
    *,
    maximum_bytes: int,
    missing_ok: bool = False,
) -> str | None:
    target = Path(path)
    limit = max(1, int(maximum_bytes))
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(
        metadata.st_mode
    ):
        raise ValueError("artifact_target_rejected")
    if metadata.st_size > limit:
        raise ValueError("artifact_too_large")
    with target.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("artifact_too_large")
    return raw.decode("utf-8")


def read_bounded_text(
    path: Path,
    *,
    maximum_bytes: int,
    missing_ok: bool = False,
) -> str | None:
    active = _active_artifact_process()
    if active is not None:
        process, remaining = active
        return process.read_text(
            Path(path),
            maximum_bytes=max(1, int(maximum_bytes)),
            missing_ok=bool(missing_ok),
            timeout_sec=remaining,
        )
    return _read_bounded_text_direct(
        path,
        maximum_bytes=maximum_bytes,
        missing_ok=missing_ok,
    )


def read_bounded_json(path: Path, *, maximum_bytes: int) -> Any:
    """Read one JSON artifact without allowing an untrusted file to stall."""

    raw = read_bounded_text(
        path,
        maximum_bytes=maximum_bytes,
    )
    if raw is None:
        raise FileNotFoundError(str(path))
    return json.loads(raw)


def _atomic_text_write_direct(
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
    temporary_token: str | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(
        target,
        temporary_token=temporary_token,
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

    active = _active_artifact_process()
    if active is not None and _uses_default_hooks(
        replace=replace,
        sync=sync,
        directory_sync=directory_sync,
        sleep=sleep,
    ):
        process, remaining = active
        process.write_text(
            Path(path),
            str(text),
            timeout_sec=remaining,
            durable=bool(durable),
            attempts=max(1, int(attempts)),
            retry_delay_sec=max(0.0, float(retry_delay_sec)),
        )
        return
    _atomic_text_write_direct(
        path,
        text,
        replace=replace,
        sync=sync,
        directory_sync=directory_sync,
        sleep=sleep,
        attempts=attempts,
        retry_delay_sec=retry_delay_sec,
        durable=durable,
    )


def _artifact_target_allowed_direct(path: Path) -> bool:
    try:
        metadata = Path(path).lstat()
    except FileNotFoundError:
        return True
    return bool(
        not stat.S_ISLNK(metadata.st_mode)
        and stat.S_ISREG(metadata.st_mode)
    )


def artifact_target_allowed(path: Path) -> bool:
    active = _active_artifact_process()
    if active is not None:
        process, remaining = active
        return bool(
            process.target_allowed(
                Path(path),
                timeout_sec=remaining,
            )
        )
    return _artifact_target_allowed_direct(path)


def _unlink_regular_artifact_direct(path: Path) -> bool:
    target = Path(path)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(
        metadata.st_mode
    ):
        return False
    target.unlink()
    return True


def unlink_regular_artifact(path: Path) -> bool:
    active = _active_artifact_process()
    if active is not None:
        process, remaining = active
        return bool(
            process.unlink_regular(
                Path(path),
                timeout_sec=remaining,
            )
        )
    return _unlink_regular_artifact_direct(path)


__all__ = [
    "DEFAULT_REPLACE_ATTEMPTS",
    "DEFAULT_RETRY_DELAY_SEC",
    "DurableCommitError",
    "_artifact_target_allowed_direct",
    "_atomic_json_write_direct",
    "_atomic_text_write_direct",
    "_read_bounded_text_direct",
    "_unlink_regular_artifact_direct",
    "artifact_target_allowed",
    "atomic_json_write",
    "atomic_text_write",
    "durable_artifact_process_scope",
    "read_bounded_json",
    "read_bounded_text",
    "unlink_regular_artifact",
]
