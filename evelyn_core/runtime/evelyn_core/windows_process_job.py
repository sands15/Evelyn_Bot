from __future__ import annotations

import ctypes
import os
from typing import Any


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _windows_birth_identity_from_handle(kernel32: Any, handle: Any) -> str:
    from ctypes import wintypes

    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    if not kernel32.GetProcessTimes(
        handle,
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


class KillOnCloseProcessOwner:
    """Own Local Bridge lifetime through a Windows kill-on-close Job Object.

    On non-Windows hosts the durable PID/birth reconciliation remains active
    and this object intentionally becomes a no-op.  The production supervisor
    is a Windows host process, where assignment failure is fail-closed.
    """

    def __init__(self) -> None:
        self.mode = (
            "windows_job_kill_on_close"
            if os.name == "nt"
            else "pid_birth_identity_reconcile"
        )
        self.ready = os.name != "nt"
        self._kernel32: Any | None = None
        self._handle: Any | None = None
        if os.name != "nt":
            return
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            error = ctypes.get_last_error()
            raise OSError(error, "host_supervisor_job_create_failed")
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel32.SetInformationJobObject(
            handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "host_supervisor_job_configure_failed")
        self._kernel32 = kernel32
        self._handle = handle
        self.ready = True

    def assign(self, process: Any, expected_birth_identity: str) -> bool:
        if os.name != "nt":
            return True
        if not self.ready or self._kernel32 is None or self._handle is None:
            return False
        from ctypes import wintypes

        process_handle: Any | None = None
        close_process_handle = False
        raw_handle = getattr(process, "_handle", None)
        if raw_handle is not None:
            process_handle = wintypes.HANDLE(int(raw_handle))
        else:
            # Opened handles are verified before assignment, so PID reuse can
            # never transfer ownership to an unrelated process.
            process_terminate = 0x0001
            process_set_quota = 0x0100
            process_query_limited_information = 0x1000
            synchronize = 0x00100000
            process_handle = self._kernel32.OpenProcess(
                process_terminate
                | process_set_quota
                | process_query_limited_information
                | synchronize,
                False,
                int(process.pid),
            )
            if not process_handle:
                return False
            close_process_handle = True
        try:
            try:
                observed = _windows_birth_identity_from_handle(
                    self._kernel32,
                    process_handle,
                )
            except OSError:
                return False
            if observed != expected_birth_identity:
                return False
            return bool(
                self._kernel32.AssignProcessToJobObject(
                    self._handle,
                    process_handle,
                )
            )
        finally:
            if close_process_handle:
                self._kernel32.CloseHandle(process_handle)

    def close(self) -> None:
        if self._handle is None or self._kernel32 is None:
            self.ready = os.name != "nt"
            return
        self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self.ready = False


__all__ = [
    "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE",
    "KillOnCloseProcessOwner",
]
