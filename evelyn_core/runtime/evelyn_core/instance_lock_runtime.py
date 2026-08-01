from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    import msvcrt as _MSVCRT
except Exception:
    _MSVCRT = None

try:
    import fcntl as _FCNTL
except Exception:
    _FCNTL = None


@dataclass(frozen=True)
class InstanceLockRuntimeDeps:
    lock_path: Path
    open_file: Callable[..., Any]
    getpid: Callable[[], int]
    monotonic: Callable[[], float]
    sleep: Callable[[float], Any]
    msvcrt_module: Any | None
    fcntl_module: Any | None


def build_instance_lock_runtime_deps(
    lock_path: Path,
    *,
    open_file: Callable[..., Any] = open,
    getpid: Callable[[], int] = os.getpid,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = time.sleep,
    msvcrt_module: Any | None = None,
    fcntl_module: Any | None = None,
) -> InstanceLockRuntimeDeps:
    if msvcrt_module is None and fcntl_module is None:
        msvcrt_module = _MSVCRT
        fcntl_module = _FCNTL
    return InstanceLockRuntimeDeps(
        lock_path=lock_path,
        open_file=open_file,
        getpid=getpid,
        monotonic=monotonic,
        sleep=sleep,
        msvcrt_module=msvcrt_module,
        fcntl_module=fcntl_module,
    )


def _lock_handle(handle: Any, *, deps: InstanceLockRuntimeDeps) -> None:
    if deps.msvcrt_module is not None:
        deps.msvcrt_module.locking(handle.fileno(), deps.msvcrt_module.LK_NBLCK, 1)
    elif deps.fcntl_module is not None:
        deps.fcntl_module.flock(handle.fileno(), deps.fcntl_module.LOCK_EX | deps.fcntl_module.LOCK_NB)


def _unlock_handle(handle: Any, *, deps: InstanceLockRuntimeDeps) -> None:
    if deps.msvcrt_module is not None:
        deps.msvcrt_module.locking(handle.fileno(), deps.msvcrt_module.LK_UNLCK, 1)
    elif deps.fcntl_module is not None:
        deps.fcntl_module.flock(handle.fileno(), deps.fcntl_module.LOCK_UN)


def release_instance_lock_from_runtime(current_handle: Any, *, deps: InstanceLockRuntimeDeps) -> None:
    if current_handle is None:
        return
    try:
        current_handle.seek(0)
        _unlock_handle(current_handle, deps=deps)
    except Exception:
        pass
    try:
        current_handle.close()
    except Exception:
        pass


def acquire_instance_lock_from_runtime(
    current_handle: Any,
    *,
    deps: InstanceLockRuntimeDeps,
    wait_sec: float = 15.0,
    poll_sec: float = 0.25,
) -> Any:
    if current_handle is not None:
        return current_handle

    deps.lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = deps.open_file(deps.lock_path, "a+", encoding="utf-8")
    deadline = deps.monotonic() + max(0.0, wait_sec)

    while True:
        try:
            handle.seek(0)
            _lock_handle(handle, deps=deps)
            handle.seek(0)
            handle.truncate()
            handle.write(str(deps.getpid()))
            handle.flush()
            return handle
        except OSError:
            if deps.monotonic() >= deadline:
                try:
                    handle.close()
                except Exception:
                    pass
                raise RuntimeError("Another Evelyn bot instance is already running.")
            deps.sleep(max(0.05, poll_sec))


def acquire_instance_lock_from_main(
    current_handle: Any,
    *,
    lock_path: Path,
    wait_sec: float = 15.0,
    poll_sec: float = 0.25,
    open_file: Callable[..., Any] = open,
    getpid: Callable[[], int] = os.getpid,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = time.sleep,
    msvcrt_module: Any | None = None,
    fcntl_module: Any | None = None,
) -> Any:
    deps = build_instance_lock_runtime_deps(
        lock_path,
        open_file=open_file,
        getpid=getpid,
        monotonic=monotonic,
        sleep=sleep,
        msvcrt_module=msvcrt_module,
        fcntl_module=fcntl_module,
    )
    return acquire_instance_lock_from_runtime(
        current_handle,
        deps=deps,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )


def release_instance_lock_from_main(
    current_handle: Any,
    *,
    lock_path: Path,
    open_file: Callable[..., Any] = open,
    getpid: Callable[[], int] = os.getpid,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = time.sleep,
    msvcrt_module: Any | None = None,
    fcntl_module: Any | None = None,
) -> None:
    deps = build_instance_lock_runtime_deps(
        lock_path,
        open_file=open_file,
        getpid=getpid,
        monotonic=monotonic,
        sleep=sleep,
        msvcrt_module=msvcrt_module,
        fcntl_module=fcntl_module,
    )
    release_instance_lock_from_runtime(current_handle, deps=deps)


class InstanceLockManager:
    def __init__(self, deps: InstanceLockRuntimeDeps) -> None:
        self._deps = deps
        self._handle: Any = None

    def acquire(self, wait_sec: float = 15.0, poll_sec: float = 0.25) -> None:
        self._handle = acquire_instance_lock_from_runtime(
            self._handle,
            deps=self._deps,
            wait_sec=wait_sec,
            poll_sec=poll_sec,
        )

    def release(self) -> None:
        release_instance_lock_from_runtime(self._handle, deps=self._deps)
        self._handle = None
