from __future__ import annotations

import errno
import os
import stat
import threading
import time
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


_BUSY_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        errno.EWOULDBLOCK,
    }
)
_WINDOWS_LOCK_VIOLATION = 33
_BLOCKING_RETRY_INTERVAL_SEC = 0.05


class MinecraftOwnerLockBusy(RuntimeError):
    """Raised when another live process owns the world-action boundary."""


class MinecraftOwnerLockUnavailable(RuntimeError):
    """Raised when the operating-system lock boundary cannot be established."""


class MinecraftOwnerLock:
    """Hold one nonblocking OS file lock for this object's process lifetime.

    The path is a stable lock inode and is deliberately never unlinked.  The
    mutable owner-claim JSON must use a different path because atomic replace
    would otherwise detach the lock from the name observed by contenders.
    """

    def __init__(
        self,
        path: Path,
        *,
        msvcrt_module: Any | None = None,
        fcntl_module: Any | None = None,
        open_file: Callable[..., Any] = open,
    ) -> None:
        self._path = Path(path)
        if msvcrt_module is None and fcntl_module is None:
            msvcrt_module = _MSVCRT
            fcntl_module = _FCNTL
        self._msvcrt_module = msvcrt_module
        self._fcntl_module = fcntl_module
        self._open_file = open_file
        self._state_lock = threading.RLock()
        self._handle: Any | None = None
        self._backend = ""

    @property
    def path(self) -> Path:
        return self._path

    @property
    def acquired(self) -> bool:
        with self._state_lock:
            return self._handle is not None

    def _selected_backend(self) -> str:
        if self._msvcrt_module is not None:
            return "msvcrt"
        if self._fcntl_module is not None:
            return "fcntl"
        return ""

    @staticmethod
    def _lock_is_busy(error: OSError) -> bool:
        return bool(
            error.errno in _BUSY_ERRNOS
            or getattr(error, "winerror", None)
            == _WINDOWS_LOCK_VIOLATION
        )

    def _verify_stable_regular_file(self, handle: Any) -> None:
        if self.path.is_symlink():
            raise MinecraftOwnerLockUnavailable(
                "minecraft_owner_lock_unavailable"
            )
        try:
            handle_stat = os.fstat(handle.fileno())
            path_stat = self.path.lstat()
        except (OSError, ValueError):
            raise MinecraftOwnerLockUnavailable(
                "minecraft_owner_lock_unavailable"
            ) from None
        if (
            not stat.S_ISREG(path_stat.st_mode)
            or not os.path.samestat(handle_stat, path_stat)
        ):
            raise MinecraftOwnerLockUnavailable(
                "minecraft_owner_lock_unavailable"
            )

    @staticmethod
    def _seed_lock_byte(handle: Any) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)

    def _lock_handle(
        self,
        handle: Any,
        backend: str,
    ) -> None:
        try:
            if backend == "msvcrt":
                handle.seek(0)
                self._msvcrt_module.locking(
                    handle.fileno(),
                    self._msvcrt_module.LK_NBLCK,
                    1,
                )
                return
            handle.seek(0)
            operation = (
                self._fcntl_module.LOCK_EX
                | self._fcntl_module.LOCK_NB
            )
            self._fcntl_module.flock(handle.fileno(), operation)
        except OSError as exc:
            if self._lock_is_busy(exc):
                raise MinecraftOwnerLockBusy(
                    "minecraft_owner_lock_busy"
                ) from None
            raise MinecraftOwnerLockUnavailable(
                "minecraft_owner_lock_unavailable"
            ) from None

    def _unlock_handle(self, handle: Any, backend: str) -> None:
        if backend == "msvcrt":
            handle.seek(0)
            self._msvcrt_module.locking(
                handle.fileno(),
                self._msvcrt_module.LK_UNLCK,
                1,
            )
            return
        if backend == "fcntl":
            self._fcntl_module.flock(
                handle.fileno(),
                self._fcntl_module.LOCK_UN,
            )

    def _acquire(self) -> None:
        with self._state_lock:
            if self._handle is not None:
                return
            backend = self._selected_backend()
            if not backend:
                raise MinecraftOwnerLockUnavailable(
                    "minecraft_owner_lock_unavailable"
                )

            handle: Any | None = None
            locked = False
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.is_symlink():
                    raise MinecraftOwnerLockUnavailable(
                        "minecraft_owner_lock_unavailable"
                    )
                handle = self._open_file(self.path, "a+b")
                self._verify_stable_regular_file(handle)
                self._seed_lock_byte(handle)
                self._lock_handle(handle, backend)
                locked = True
                self._verify_stable_regular_file(handle)
                handle.seek(0, os.SEEK_END)
                if handle.tell() != 1:
                    handle.truncate(1)
                    handle.flush()
                    os.fsync(handle.fileno())
            except (MinecraftOwnerLockBusy, MinecraftOwnerLockUnavailable):
                if handle is not None:
                    if locked:
                        try:
                            self._unlock_handle(handle, backend)
                        except Exception:
                            pass
                    try:
                        handle.close()
                    except Exception:
                        pass
                raise
            except (OSError, ValueError):
                if handle is not None:
                    if locked:
                        try:
                            self._unlock_handle(handle, backend)
                        except Exception:
                            pass
                    try:
                        handle.close()
                    except Exception:
                        pass
                raise MinecraftOwnerLockUnavailable(
                    "minecraft_owner_lock_unavailable"
                ) from None

            self._handle = handle
            self._backend = backend

    def acquire(self) -> None:
        self._acquire()

    def acquire_blocking(self) -> None:
        """Wait for the stable OS lock instead of admitting a race.

        Long-lived authority acquisition remains nonblocking.  This blocking
        form is reserved for shutdown/revocation, where returning before an
        already-admitted world effect commits would be unsafe.
        """

        while True:
            try:
                self._acquire()
                return
            except MinecraftOwnerLockBusy:
                # msvcrt.LK_LOCK has a fixed ~10 second retry budget.  Use
                # the same nonblocking primitive on every platform and retry
                # explicitly so shutdown never mistakes a long effect for an
                # unavailable boundary.
                time.sleep(_BLOCKING_RETRY_INTERVAL_SEC)

    def release(self) -> None:
        with self._state_lock:
            handle = self._handle
            backend = self._backend
            self._handle = None
            self._backend = ""
            if handle is None:
                return
            try:
                self._unlock_handle(handle, backend)
            except Exception:
                pass
            try:
                handle.close()
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


__all__ = [
    "MinecraftOwnerLock",
    "MinecraftOwnerLockBusy",
    "MinecraftOwnerLockUnavailable",
]
