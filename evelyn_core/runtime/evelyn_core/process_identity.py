from __future__ import annotations

import ctypes
import os
import select
import signal
from pathlib import Path


def _windows_birth_identity_from_handle(handle: int) -> str:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        wintypes.HANDLE(handle),
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel_time),
        ctypes.byref(user_time),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, "process identity unavailable")
    value = (int(creation.dwHighDateTime) << 32) | int(
        creation.dwLowDateTime
    )
    return f"windows:{value}"


def process_birth_identity(pid: int) -> str | None:
    """Return a content-free OS creation identity for one live PID.

    A PID is never sufficient authority to signal a process because the OS can
    reuse it.  Callers must persist and compare this identity before acting.
    Unsupported or unreadable process tables raise so lifecycle owners fail
    closed instead of guessing.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("process_identity_invalid")
    if os.name == "nt":
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        error_invalid_parameter = 87
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            error = ctypes.get_last_error()
            if error == error_invalid_parameter:
                return None
            raise OSError(error, "process identity unavailable")
        try:
            return _windows_birth_identity_from_handle(int(handle))
        finally:
            kernel32.CloseHandle(handle)

    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise OSError("process identity unavailable") from exc
    closing = raw.rfind(")")
    if closing < 0:
        raise OSError("process identity unavailable")
    fields_after_name = raw[closing + 1 :].strip().split()
    # /proc/<pid>/stat field 22 is starttime; this suffix begins at field 3.
    if len(fields_after_name) <= 19 or not fields_after_name[19].isdigit():
        raise OSError("process identity unavailable")
    return f"linux:{fields_after_name[19]}"


def terminate_process_identity(
    pid: int,
    expected_birth_identity: str,
    *,
    timeout_sec: float = 5.0,
) -> bool:
    """Terminate only the exact PID/birth pair and prove it is gone.

    A mismatched birth identity is treated as already gone.  In particular,
    this function never signals a new process that happens to reuse an old PID.
    """

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ValueError("process_identity_invalid")
    if not isinstance(expected_birth_identity, str):
        raise ValueError("process_identity_invalid")
    if os.name == "nt":
        from ctypes import wintypes

        process_terminate = 0x0001
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        error_invalid_parameter = 87
        wait_object_0 = 0
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_terminate
            | process_query_limited_information
            | synchronize,
            False,
            pid,
        )
        if not handle:
            return ctypes.get_last_error() == error_invalid_parameter
        try:
            try:
                current = _windows_birth_identity_from_handle(int(handle))
            except OSError:
                return False
            if current != expected_birth_identity:
                return True
            if not kernel32.TerminateProcess(handle, 1):
                return False
            wait_ms = max(1, int(max(0.1, timeout_sec) * 1000))
            return kernel32.WaitForSingleObject(handle, wait_ms) == wait_object_0
        finally:
            kernel32.CloseHandle(handle)

    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if not callable(pidfd_open) or not callable(pidfd_send_signal):
        return False
    try:
        pidfd = pidfd_open(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    try:
        try:
            current = process_birth_identity(pid)
        except (OSError, ValueError):
            return False
        if current is None or current != expected_birth_identity:
            return True
        try:
            pidfd_send_signal(pidfd, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        readable, _, _ = select.select(
            [pidfd],
            [],
            [],
            max(0.1, float(timeout_sec)),
        )
        if readable:
            return True
        try:
            pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        readable, _, _ = select.select(
            [pidfd],
            [],
            [],
            max(0.1, float(timeout_sec)),
        )
        return bool(readable)
    finally:
        os.close(pidfd)


def birth_identity_matches_current_platform(value: str) -> bool:
    prefix = "windows:" if os.name == "nt" else "linux:"
    return isinstance(value, str) and value.startswith(prefix)


__all__ = [
    "birth_identity_matches_current_platform",
    "process_birth_identity",
    "terminate_process_identity",
]
