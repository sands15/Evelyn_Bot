from __future__ import annotations

import ctypes
import json
import math
import os
import re
import signal
import stat
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .durable_artifact_process import (
    ARTIFACT_PROCESS_PROTOCOL,
    MAX_ARTIFACT_FRAME_BYTES,
)


MAX_ARTIFACT_PATH_BYTES = 32 * 1024
MAX_ARTIFACT_SEQUENCE = (1 << 63) - 1
MAX_REPLACE_ATTEMPTS = 32
MAX_RETRY_DELAY_SEC = 60.0
_FRAME_HEADER = struct.Struct("!I")
_HEX_TOKEN = re.compile(r"[0-9a-f]{32}")
_TEMPORARY_NAME = re.compile(
    r"\..+\.[1-9][0-9]*\.[0-9a-f]{32}\.tmp",
    re.DOTALL,
)
_COMMON_FIELDS = frozenset(
    {"protocol", "workerNonce", "requestId", "sequence", "operation"}
)
_OPERATION_FIELDS = {
    "READ_BOUNDED": frozenset({"path", "maximumBytes", "missingOk"}),
    "TARGET_ALLOWED": frozenset({"path"}),
    "ATOMIC_JSON_WRITE": frozenset(
        {"path", "payload", "durable", "attempts", "retryDelaySec"}
    ),
    "ATOMIC_TEXT_WRITE": frozenset(
        {"path", "text", "durable", "attempts", "retryDelaySec"}
    ),
    "UNLINK_REGULAR": frozenset({"path"}),
    "UNLINK_EXACT": frozenset({"path"}),
    "SYNC_EXISTING": frozenset({"path"}),
}


class _ProtocolError(Exception):
    pass


class _RequestError(Exception):
    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _reject_constant(_: str) -> None:
    raise ValueError("non_finite_json")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _canonical_frame(payload: dict[str, Any]) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _ProtocolError() from exc
    if not encoded or len(encoded) > MAX_ARTIFACT_FRAME_BYTES:
        raise _ProtocolError()
    return encoded


def _read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError()
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(stream: Any) -> dict[str, Any]:
    size = _FRAME_HEADER.unpack(_read_exact(stream, _FRAME_HEADER.size))[0]
    if size <= 0 or size > MAX_ARTIFACT_FRAME_BYTES:
        raise _ProtocolError()
    try:
        decoded = _read_exact(stream, size).decode("utf-8")
        payload = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise _ProtocolError() from exc
    if not isinstance(payload, dict):
        raise _ProtocolError()
    return payload


def _write_frame(stream: Any, payload: dict[str, Any]) -> None:
    encoded = _canonical_frame(payload)
    stream.write(_FRAME_HEADER.pack(len(encoded)))
    stream.write(encoded)
    stream.flush()


def _is_exact_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_token(value: Any) -> bool:
    return isinstance(value, str) and _HEX_TOKEN.fullmatch(value) is not None


def _path_from_request(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _RequestError("durable_artifact_path_rejected")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise _RequestError("durable_artifact_path_rejected") from exc
    if len(encoded) > MAX_ARTIFACT_PATH_BYTES:
        raise _RequestError("durable_artifact_path_rejected")
    path = Path(value)
    if not path.is_absolute():
        raise _RequestError("durable_artifact_path_rejected")
    return path


def _validate_envelope(
    request: dict[str, Any],
    *,
    worker_nonce: str,
    previous_sequence: int | None,
) -> int:
    if request.get("protocol") != ARTIFACT_PROCESS_PROTOCOL:
        raise _ProtocolError()
    if request.get("workerNonce") != worker_nonce:
        raise _ProtocolError()
    if not _is_token(request.get("requestId")):
        raise _ProtocolError()
    sequence = request.get("sequence")
    if (
        not _is_exact_int(sequence)
        or sequence <= 0
        or sequence > MAX_ARTIFACT_SEQUENCE
        or (
            previous_sequence is not None
            and sequence != previous_sequence + 1
        )
    ):
        raise _ProtocolError()
    if not isinstance(request.get("operation"), str):
        raise _ProtocolError()
    return int(sequence)


def _validate_request(request: dict[str, Any]) -> None:
    operation = str(request["operation"])
    operation_fields = _OPERATION_FIELDS.get(operation)
    if operation_fields is None:
        raise _RequestError("durable_artifact_operation_rejected")
    if frozenset(request) != _COMMON_FIELDS | operation_fields:
        raise _RequestError("durable_artifact_request_rejected")
    _path_from_request(request["path"])
    if operation == "READ_BOUNDED":
        maximum_bytes = request["maximumBytes"]
        if (
            not _is_exact_int(maximum_bytes)
            or maximum_bytes <= 0
            or maximum_bytes > MAX_ARTIFACT_FRAME_BYTES
            or not isinstance(request["missingOk"], bool)
        ):
            raise _RequestError("durable_artifact_request_rejected")
        return
    if operation not in {"ATOMIC_JSON_WRITE", "ATOMIC_TEXT_WRITE"}:
        return
    if not isinstance(request["durable"], bool):
        raise _RequestError("durable_artifact_request_rejected")
    attempts = request["attempts"]
    if (
        not _is_exact_int(attempts)
        or attempts <= 0
        or attempts > MAX_REPLACE_ATTEMPTS
    ):
        raise _RequestError("durable_artifact_request_rejected")
    retry_delay = request["retryDelaySec"]
    if (
        isinstance(retry_delay, bool)
        or not isinstance(retry_delay, (int, float))
        or not math.isfinite(float(retry_delay))
        or float(retry_delay) < 0.0
        or float(retry_delay) > MAX_RETRY_DELAY_SEC
    ):
        raise _RequestError("durable_artifact_request_rejected")
    if operation == "ATOMIC_JSON_WRITE":
        if not isinstance(request["payload"], dict):
            raise _RequestError("durable_artifact_request_rejected")
        _serialize_json(request["payload"])
    elif not isinstance(request["text"], str):
        raise _RequestError("durable_artifact_request_rejected")
    else:
        _encode_text(request["text"])


def _response_base(
    request: dict[str, Any],
    *,
    worker_nonce: str,
    phase: str,
) -> dict[str, Any]:
    return {
        "protocol": ARTIFACT_PROCESS_PROTOCOL,
        "workerNonce": worker_nonce,
        "requestId": request["requestId"],
        "sequence": request["sequence"],
        "phase": phase,
    }


def _target_allowed(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode)


def _open_regular(path: Path, *, writable: bool = False) -> int:
    try:
        before = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _RequestError("durable_artifact_target_rejected") from exc
    if not stat.S_ISREG(before.st_mode):
        raise _RequestError("durable_artifact_target_rejected")
    flags = (
        os.O_RDWR
        if writable
        else os.O_RDONLY
    ) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _RequestError("durable_artifact_target_rejected") from exc
    try:
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(after.st_mode)
            or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise _RequestError("durable_artifact_target_rejected")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_bounded(
    path: Path,
    *,
    maximum_bytes: int,
    missing_ok: bool,
) -> dict[str, Any]:
    try:
        descriptor = _open_regular(path)
    except FileNotFoundError:
        if missing_ok and not path.is_symlink():
            return {"missing": True}
        raise _RequestError("durable_artifact_missing") from None
    try:
        with os.fdopen(descriptor, "rb") as handle:
            raw = handle.read(maximum_bytes + 1)
    except OSError as exc:
        raise _RequestError("durable_artifact_read_failed") from exc
    if len(raw) > maximum_bytes:
        raise _RequestError("durable_artifact_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise _RequestError("durable_artifact_invalid_utf8") from exc
    return {"missing": False, "text": text}


def _serialize_json(payload: dict[str, Any]) -> str:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise _RequestError("durable_artifact_request_rejected") from exc
    _encode_text(text)
    return text


def _encode_text(text: str) -> bytes:
    try:
        encoded = text.encode("utf-8")
    except UnicodeError as exc:
        raise _RequestError("durable_artifact_request_rejected") from exc
    if len(encoded) > MAX_ARTIFACT_FRAME_BYTES:
        raise _RequestError("durable_artifact_too_large")
    return encoded


def _temporary_path(target: Path, token: str) -> Path:
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{token}.tmp"
    )
    _path_from_request(str(temporary))
    return temporary


def _open_temporary(path: Path) -> Any:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise _RequestError("durable_artifact_temporary_rejected") from exc
    return os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")


def _sync_parent(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise _RequestError("durable_artifact_parent_sync_failed") from exc


def _windows_write_through_replace(source: Path, target: Path) -> None:
    from ctypes import wintypes

    move_file_ex = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file_ex.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    )
    move_file_ex.restype = wintypes.BOOL
    flags = 0x00000001 | 0x00000008
    if not move_file_ex(str(source), str(target), flags):
        raise ctypes.WinError(ctypes.get_last_error())


def _replace(source: Path, target: Path, *, durable: bool) -> None:
    if durable and os.name == "nt":
        _windows_write_through_replace(source, target)
    else:
        os.replace(source, target)


def _replace_with_retry(
    temporary: Path,
    target: Path,
    *,
    durable: bool,
    attempts: int,
    retry_delay_sec: float,
) -> None:
    for attempt in range(attempts):
        try:
            _replace(temporary, target, durable=durable)
            break
        except PermissionError:
            if attempt + 1 >= attempts:
                raise
            time.sleep(retry_delay_sec * (2**attempt))
    if durable:
        _sync_parent(target.parent)


def _atomic_write(
    target: Path,
    text: str,
    *,
    token: str,
    durable: bool,
    attempts: int,
    retry_delay_sec: float,
) -> None:
    if not _target_allowed(target):
        raise _RequestError("durable_artifact_target_rejected")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _RequestError("durable_artifact_parent_rejected") from exc
    if not _target_allowed(target):
        raise _RequestError("durable_artifact_target_rejected")
    temporary = _temporary_path(target, token)
    try:
        try:
            with _open_temporary(temporary) as handle:
                handle.write(text)
                if durable:
                    handle.flush()
                    os.fsync(handle.fileno())
            _replace_with_retry(
                temporary,
                target,
                durable=durable,
                attempts=attempts,
                retry_delay_sec=retry_delay_sec,
            )
        except _RequestError:
            raise
        except OSError as exc:
            raise _RequestError("durable_artifact_write_failed") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _unlink(path: Path, *, exact_temporary: bool) -> dict[str, Any]:
    if exact_temporary and _TEMPORARY_NAME.fullmatch(path.name) is None:
        raise _RequestError("durable_artifact_temporary_rejected")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"removed": False}
    except OSError as exc:
        raise _RequestError("durable_artifact_unlink_failed") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise _RequestError("durable_artifact_target_rejected")
    try:
        path.unlink()
        _sync_parent(path.parent)
    except FileNotFoundError:
        return {"removed": False}
    except OSError as exc:
        raise _RequestError("durable_artifact_unlink_failed") from exc
    return {"removed": True}


def _sync_existing(path: Path) -> None:
    try:
        descriptor = _open_regular(
            path,
            writable=os.name == "nt",
        )
    except FileNotFoundError:
        raise _RequestError("durable_artifact_missing") from None
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise _RequestError("durable_artifact_sync_failed") from exc
    finally:
        os.close(descriptor)
    _sync_parent(path.parent)


def _execute(request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request["operation"])
    path = _path_from_request(request["path"])
    if operation == "READ_BOUNDED":
        return _read_bounded(
            path,
            maximum_bytes=int(request["maximumBytes"]),
            missing_ok=bool(request["missingOk"]),
        )
    if operation == "TARGET_ALLOWED":
        return {"allowed": _target_allowed(path)}
    if operation == "UNLINK_REGULAR":
        return _unlink(path, exact_temporary=False)
    if operation == "UNLINK_EXACT":
        return _unlink(path, exact_temporary=True)
    if operation == "SYNC_EXISTING":
        _sync_existing(path)
        return {}
    if operation == "ATOMIC_JSON_WRITE":
        text = _serialize_json(request["payload"])
    else:
        text = str(request["text"])
    _atomic_write(
        path,
        text,
        token=str(request["requestId"]),
        durable=bool(request["durable"]),
        attempts=int(request["attempts"]),
        retry_delay_sec=float(request["retryDelaySec"]),
    )
    return {}


def _start_posix_parent_watcher(parent_pid: int) -> None:
    if os.getppid() != parent_pid:
        raise _ProtocolError()
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            prctl = libc.prctl
            prctl.argtypes = (
                ctypes.c_int,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.c_ulong,
                ctypes.c_ulong,
            )
            prctl.restype = ctypes.c_int
            if prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "prctl")
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGKILL)
            return
        except (AttributeError, OSError):
            pass

    def watch() -> None:
        while True:
            if os.getppid() != parent_pid:
                os._exit(1)
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                os._exit(1)
            except PermissionError:
                pass
            time.sleep(0.25)

    threading.Thread(
        target=watch,
        name="evelyn-artifact-parent-watch",
        daemon=True,
    ).start()


def _start_windows_parent_watcher(parent_pid: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait_for_single = kernel32.WaitForSingleObject
    wait_for_single.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x00100000, False, parent_pid)
    if not handle:
        raise _ProtocolError()

    def watch() -> None:
        try:
            if wait_for_single(handle, 0xFFFFFFFF) == 0:
                os._exit(1)
            os._exit(1)
        finally:
            close_handle(handle)

    threading.Thread(
        target=watch,
        name="evelyn-artifact-parent-watch",
        daemon=True,
    ).start()


def _start_parent_watcher(parent_pid: int) -> None:
    if parent_pid <= 0 or parent_pid == os.getpid():
        raise _ProtocolError()
    if os.name == "nt":
        _start_windows_parent_watcher(parent_pid)
    else:
        _start_posix_parent_watcher(parent_pid)


def _parse_parent_pid(arguments: list[str]) -> int:
    if len(arguments) != 2 or arguments[0] != "--parent-pid":
        raise _ProtocolError()
    try:
        parent_pid = int(arguments[1])
    except (TypeError, ValueError) as exc:
        raise _ProtocolError() from exc
    if str(parent_pid) != arguments[1] or parent_pid <= 0:
        raise _ProtocolError()
    return parent_pid


def run_worker(parent_pid: int, *, stdin: Any, stdout: Any) -> None:
    _start_parent_watcher(parent_pid)
    worker_nonce = os.urandom(16).hex()
    _write_frame(
        stdout,
        {
            "protocol": ARTIFACT_PROCESS_PROTOCOL,
            "phase": "READY",
            "pid": os.getpid(),
            "workerNonce": worker_nonce,
        },
    )
    previous_sequence: int | None = None
    while True:
        request = _read_frame(stdin)
        sequence = _validate_envelope(
            request,
            worker_nonce=worker_nonce,
            previous_sequence=previous_sequence,
        )
        previous_sequence = sequence
        _write_frame(
            stdout,
            _response_base(
                request,
                worker_nonce=worker_nonce,
                phase="PREPARED",
            ),
        )
        try:
            _validate_request(request)
            result = _execute(request)
            response = _response_base(
                request,
                worker_nonce=worker_nonce,
                phase="COMMIT",
            )
            response.update(result)
            _canonical_frame(response)
        except _RequestError as exc:
            response = _response_base(
                request,
                worker_nonce=worker_nonce,
                phase="ABORT",
            )
            response["code"] = exc.code
        except Exception:
            response = _response_base(
                request,
                worker_nonce=worker_nonce,
                phase="ABORT",
            )
            response["code"] = "durable_artifact_operation_failed"
        _write_frame(stdout, response)


def main(arguments: list[str] | None = None) -> int:
    try:
        parent_pid = _parse_parent_pid(
            sys.argv[1:] if arguments is None else arguments
        )
        run_worker(
            parent_pid,
            stdin=sys.stdin.buffer,
            stdout=sys.stdout.buffer,
        )
    except (BrokenPipeError, EOFError, OSError, _ProtocolError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MAX_ARTIFACT_PATH_BYTES",
    "MAX_REPLACE_ATTEMPTS",
    "MAX_RETRY_DELAY_SEC",
    "main",
    "run_worker",
]
