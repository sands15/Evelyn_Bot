from __future__ import annotations

import atexit
import json
import os
import struct
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


ARTIFACT_PROCESS_PROTOCOL = "evelyn.artifact-process.v1"
DEFAULT_ARTIFACT_DEADLINE_SEC = 5.0
DEFAULT_ARTIFACT_START_TIMEOUT_SEC = 5.0
DEFAULT_ARTIFACT_REPLACE_ATTEMPTS = 6
DEFAULT_ARTIFACT_RETRY_DELAY_SEC = 0.02
MAX_ARTIFACT_FRAME_BYTES = 8 * 1024 * 1024
_FRAME_HEADER = struct.Struct("!I")


class DurableArtifactProcessError(OSError):
    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


class DurableArtifactProcessOutcomeUnknown(
    DurableArtifactProcessError
):
    def __init__(
        self,
        code: str = "durable_artifact_process_outcome_unknown",
    ) -> None:
        super().__init__(code)


class DurableArtifactProcessTimeout(
    DurableArtifactProcessOutcomeUnknown
):
    def __init__(self) -> None:
        super().__init__("durable_artifact_process_timeout")


def _canonical_frame(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_ARTIFACT_FRAME_BYTES:
        raise DurableArtifactProcessError(
            "durable_artifact_frame_too_large"
        )
    return encoded


def _read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("durable_artifact_process_closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(stream: Any) -> dict[str, Any]:
    size = _FRAME_HEADER.unpack(_read_exact(stream, _FRAME_HEADER.size))[0]
    if size <= 0 or size > MAX_ARTIFACT_FRAME_BYTES:
        raise DurableArtifactProcessError(
            "durable_artifact_frame_rejected"
        )
    payload = json.loads(_read_exact(stream, size))
    if not isinstance(payload, dict):
        raise DurableArtifactProcessError(
            "durable_artifact_frame_rejected"
        )
    return payload


def _write_frame(stream: Any, payload: dict[str, Any]) -> None:
    encoded = _canonical_frame(payload)
    stream.write(_FRAME_HEADER.pack(len(encoded)))
    stream.write(encoded)
    stream.flush()


class DurableArtifactProcess:
    """Own one warm, killable process for continuity artifact I/O."""

    def __init__(
        self,
        *,
        deadline_sec: float = DEFAULT_ARTIFACT_DEADLINE_SEC,
        start_timeout_sec: float = DEFAULT_ARTIFACT_START_TIMEOUT_SEC,
        command: tuple[str, ...] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.deadline_sec = max(0.1, float(deadline_sec))
        self.start_timeout_sec = max(0.1, float(start_timeout_sec))
        self.command = command
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._worker_nonce = ""
        self._sequence = 0
        self._closed = False
        self._replacement_blocked = False
        self._pending_cleanup: list[Path] = []
        atexit.register(self.close)

    @property
    def pid(self) -> int | None:
        process = self._process
        return int(process.pid) if process is not None else None

    @staticmethod
    def _absolute_path(path: Path) -> Path:
        target = Path(path)
        if not target.is_absolute():
            raise DurableArtifactProcessError(
                "durable_artifact_path_rejected"
            )
        return target

    def _remaining(self, deadline: float) -> float:
        remaining = float(deadline) - float(self.monotonic())
        if remaining <= 0.0:
            raise DurableArtifactProcessTimeout()
        return remaining

    @contextmanager
    def _locked_until(self, deadline: float) -> Iterator[None]:
        if not self._lock.acquire(
            timeout=self._remaining(deadline)
        ):
            raise DurableArtifactProcessTimeout()
        try:
            yield
        finally:
            self._lock.release()

    def _worker_command(self) -> list[str]:
        if self.command is not None:
            return [*self.command, "--parent-pid", str(os.getpid())]
        executable = Path(sys.executable)
        if os.name == "nt":
            base_executable = Path(
                str(getattr(sys, "_base_executable", "") or "")
            )
            if base_executable.is_file():
                executable = base_executable
            elif Path(sys.prefix) != Path(sys.base_prefix):
                raise DurableArtifactProcessError(
                    "durable_artifact_process_runtime_unavailable"
                )
        return [
            str(executable),
            "-u",
            "-m",
            "evelyn_core.durable_artifact_worker",
            "--parent-pid",
            str(os.getpid()),
        ]

    def _worker_env(self) -> dict[str, str]:
        inherited = {
            "COMSPEC",
            "LANG",
            "LC_ALL",
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TZ",
            "WINDIR",
        }
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in inherited
        }
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_root
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONNOUSERSITE"] = "1"
        return env

    @staticmethod
    def _run_bounded(
        callback: Callable[[], Any],
        timeout_sec: float,
    ) -> tuple[bool, Any, threading.Thread]:
        done = threading.Event()
        result: list[Any] = []

        def run() -> None:
            try:
                result.append((True, callback()))
            except BaseException as exc:
                result.append((False, exc))
            finally:
                done.set()

        thread = threading.Thread(
            target=run,
            name="evelyn-artifact-ipc",
            daemon=True,
        )
        thread.start()
        if not done.wait(max(0.0, float(timeout_sec))):
            return False, DurableArtifactProcessTimeout(), thread
        succeeded, value = result[0]
        if succeeded:
            return True, value, thread
        return False, value, thread

    def _spawn(self, deadline: float) -> None:
        if self._closed:
            raise DurableArtifactProcessError(
                "durable_artifact_process_closed"
            )
        if self._replacement_blocked:
            raise DurableArtifactProcessError(
                "durable_artifact_process_reap_failed"
            )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            self._worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent),
            env=self._worker_env(),
            bufsize=0,
            creationflags=creationflags,
        )
        self._process = process
        assert process.stdout is not None
        try:
            timeout = min(
                self.start_timeout_sec,
                self._remaining(deadline),
            )
        except DurableArtifactProcessTimeout:
            self._abandon(process)
            raise
        succeeded, value, thread = self._run_bounded(
            lambda: _read_frame(process.stdout),
            timeout,
        )
        if not succeeded:
            try:
                self._abandon(process)
            finally:
                thread.join(timeout=0.5)
            if isinstance(value, DurableArtifactProcessTimeout):
                raise value
            raise DurableArtifactProcessError(
                "durable_artifact_process_start_failed"
            ) from None
        ready = value
        if (
            ready.get("protocol") != ARTIFACT_PROCESS_PROTOCOL
            or ready.get("phase") != "READY"
            or int(ready.get("pid") or 0) != int(process.pid)
            or not isinstance(ready.get("workerNonce"), str)
            or len(ready["workerNonce"]) != 32
        ):
            self._abandon(process)
            raise DurableArtifactProcessError(
                "durable_artifact_process_start_rejected"
            )
        try:
            self._remaining(deadline)
        except DurableArtifactProcessTimeout:
            self._abandon(process)
            raise
        self._worker_nonce = str(ready["workerNonce"])

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> bool:
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        reaped = process.poll() is not None
        if not reaped:
            return False
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        return True

    def _abandon(self, process: subprocess.Popen[bytes]) -> None:
        if not self._terminate(process):
            self._replacement_blocked = True
            raise DurableArtifactProcessError(
                "durable_artifact_process_reap_failed"
            )
        if self._process is process:
            self._process = None
            self._worker_nonce = ""
        self._replacement_blocked = False

    def _ensure_process(self, deadline: float) -> subprocess.Popen[bytes]:
        process = self._process
        if self._replacement_blocked:
            if process is None or not self._terminate(process):
                raise DurableArtifactProcessError(
                    "durable_artifact_process_reap_failed"
                )
            self._process = None
            self._worker_nonce = ""
            self._replacement_blocked = False
            process = None
        if process is not None and process.poll() is None:
            return process
        if process is not None:
            if not self._terminate(process):
                self._replacement_blocked = True
                raise DurableArtifactProcessError(
                    "durable_artifact_process_reap_failed"
                )
            self._process = None
            self._worker_nonce = ""
        self._spawn(deadline)
        assert self._process is not None
        return self._process

    def _next_request(
        self,
        operation: str,
        **payload: Any,
    ) -> dict[str, Any]:
        self._sequence += 1
        return {
            "protocol": ARTIFACT_PROCESS_PROTOCOL,
            "workerNonce": self._worker_nonce,
            "requestId": uuid.uuid4().hex,
            "sequence": self._sequence,
            "operation": str(operation),
            **payload,
        }

    def _exchange(
        self,
        process: subprocess.Popen[bytes],
        request: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        assert process.stdin is not None
        assert process.stdout is not None

        def communicate() -> dict[str, Any]:
            _write_frame(process.stdin, request)
            prepared = _read_frame(process.stdout)
            if not self._matches_response(
                prepared,
                request,
                phase="PREPARED",
            ):
                raise DurableArtifactProcessError(
                    "durable_artifact_response_rejected"
                )
            completed = _read_frame(process.stdout)
            if not self._matches_response(
                completed,
                request,
                phase=None,
            ) or completed.get("phase") not in {
                "COMMIT",
                "ABORT",
            }:
                raise DurableArtifactProcessError(
                    "durable_artifact_response_rejected"
                )
            return completed

        succeeded, value, thread = self._run_bounded(
            communicate,
            self._remaining(deadline),
        )
        if not succeeded:
            try:
                self._abandon(process)
            finally:
                thread.join(timeout=0.5)
            if isinstance(value, DurableArtifactProcessTimeout):
                raise value
            if isinstance(value, DurableArtifactProcessError):
                raise DurableArtifactProcessOutcomeUnknown(
                    value.code
                ) from None
            raise DurableArtifactProcessOutcomeUnknown(
                "durable_artifact_process_disconnected"
            ) from None
        try:
            self._remaining(deadline)
        except DurableArtifactProcessTimeout:
            self._abandon(process)
            raise
        if value.get("phase") == "ABORT":
            raise DurableArtifactProcessError(
                str(value.get("code") or "durable_artifact_operation_failed")
            )
        return value

    def _matches_response(
        self,
        response: dict[str, Any],
        request: dict[str, Any],
        *,
        phase: str | None,
    ) -> bool:
        sequence = response.get("sequence")
        return bool(
            response.get("protocol") == ARTIFACT_PROCESS_PROTOCOL
            and response.get("workerNonce")
            == request.get("workerNonce")
            and response.get("requestId") == request.get("requestId")
            and type(sequence) is int
            and sequence == request.get("sequence")
            and (
                phase is None
                or response.get("phase") == phase
            )
        )

    def _request(
        self,
        operation: str,
        *,
        deadline: float,
        **payload: Any,
    ) -> dict[str, Any]:
        process = self._ensure_process(deadline)
        request = self._next_request(operation, **payload)
        return self._exchange(process, request, deadline)

    def _request_read_only(
        self,
        operation: str,
        *,
        deadline: float,
        **payload: Any,
    ) -> dict[str, Any]:
        for attempt in range(2):
            attempt_deadline = deadline
            if attempt == 0:
                remaining = self._remaining(deadline)
                attempt_deadline = (
                    float(self.monotonic()) + remaining * 0.5
                )
            try:
                return self._request(
                    operation,
                    deadline=attempt_deadline,
                    **payload,
                )
            except DurableArtifactProcessOutcomeUnknown:
                if attempt > 0:
                    raise
                self._remaining(deadline)
        raise AssertionError("unreachable")

    def _read_text_locked(
        self,
        path: Path,
        *,
        maximum_bytes: int,
        missing_ok: bool,
        deadline: float,
    ) -> str | None:
        response = self._request_read_only(
            "READ_BOUNDED",
            deadline=deadline,
            path=str(Path(path)),
            maximumBytes=max(1, int(maximum_bytes)),
            missingOk=bool(missing_ok),
        )
        if response.get("missing") is True:
            return None
        text = response.get("text")
        if not isinstance(text, str):
            raise DurableArtifactProcessError(
                "durable_artifact_response_rejected"
            )
        return text

    def _remember_cleanup(self, temporary: Path) -> None:
        if temporary not in self._pending_cleanup:
            self._pending_cleanup.append(temporary)

    def _cleanup_pending(self, deadline: float) -> None:
        while self._pending_cleanup:
            temporary = self._pending_cleanup[0]
            self._request(
                "UNLINK_EXACT",
                deadline=deadline,
                path=str(temporary),
            )
            self._pending_cleanup.pop(0)

    def _deadline(self, timeout_sec: float | None) -> float:
        timeout = (
            self.deadline_sec
            if timeout_sec is None
            else max(0.01, float(timeout_sec))
        )
        return float(self.monotonic()) + timeout

    def ensure_started(self) -> None:
        deadline = self._deadline(self.start_timeout_sec)
        with self._locked_until(deadline):
            self._ensure_process(deadline)
            self._cleanup_pending(deadline)

    def read_text(
        self,
        path: Path,
        *,
        maximum_bytes: int,
        missing_ok: bool,
        timeout_sec: float | None = None,
    ) -> str | None:
        path = self._absolute_path(path)
        deadline = self._deadline(timeout_sec)
        with self._locked_until(deadline):
            self._cleanup_pending(deadline)
            return self._read_text_locked(
                path,
                maximum_bytes=maximum_bytes,
                missing_ok=missing_ok,
                deadline=deadline,
            )

    def target_allowed(
        self,
        path: Path,
        *,
        timeout_sec: float | None = None,
    ) -> bool:
        path = self._absolute_path(path)
        deadline = self._deadline(timeout_sec)
        with self._locked_until(deadline):
            self._cleanup_pending(deadline)
            response = self._request_read_only(
                "TARGET_ALLOWED",
                deadline=deadline,
                path=str(path),
            )
            return response.get("allowed") is True

    def unlink_regular(
        self,
        path: Path,
        *,
        timeout_sec: float | None = None,
    ) -> bool:
        path = self._absolute_path(path)
        deadline = self._deadline(timeout_sec)
        with self._locked_until(deadline):
            self._cleanup_pending(deadline)
            response = self._request(
                "UNLINK_REGULAR",
                deadline=deadline,
                path=str(path),
            )
            return response.get("removed") is True

    def write_json(
        self,
        path: Path,
        payload: dict[str, Any],
        *,
        durable: bool = False,
        attempts: int = DEFAULT_ARTIFACT_REPLACE_ATTEMPTS,
        retry_delay_sec: float = DEFAULT_ARTIFACT_RETRY_DELAY_SEC,
        timeout_sec: float | None = None,
    ) -> None:
        path = self._absolute_path(path)
        self._write(
            "ATOMIC_JSON_WRITE",
            path,
            payload=payload,
            durable=durable,
            attempts=attempts,
            retry_delay_sec=retry_delay_sec,
            timeout_sec=timeout_sec,
        )

    def write_text(
        self,
        path: Path,
        text: str,
        *,
        durable: bool = False,
        attempts: int = DEFAULT_ARTIFACT_REPLACE_ATTEMPTS,
        retry_delay_sec: float = DEFAULT_ARTIFACT_RETRY_DELAY_SEC,
        timeout_sec: float | None = None,
    ) -> None:
        path = self._absolute_path(path)
        self._write(
            "ATOMIC_TEXT_WRITE",
            path,
            text=str(text),
            durable=durable,
            attempts=attempts,
            retry_delay_sec=retry_delay_sec,
            timeout_sec=timeout_sec,
        )

    def _write(
        self,
        operation: str,
        path: Path,
        *,
        durable: bool,
        attempts: int,
        retry_delay_sec: float,
        timeout_sec: float | None,
        **payload: Any,
    ) -> None:
        deadline = self._deadline(timeout_sec)
        with self._locked_until(deadline):
            self._cleanup_pending(deadline)
            process = self._ensure_process(deadline)
            request = self._next_request(
                operation,
                path=str(path),
                durable=bool(durable),
                attempts=max(1, int(attempts)),
                retryDelaySec=max(0.0, float(retry_delay_sec)),
                **payload,
            )
            temporary = path.with_name(
                f".{path.name}.{process.pid}.{request['requestId']}.tmp"
            )
            try:
                remaining = self._remaining(deadline)
                operation_deadline = (
                    float(self.monotonic()) + remaining * 0.6
                )
                self._exchange(
                    process,
                    request,
                    operation_deadline,
                )
                return
            except DurableArtifactProcessError as error:
                mutation_error = error
                self._remember_cleanup(temporary)
                self._cleanup_pending(deadline)
            if operation == "ATOMIC_JSON_WRITE":
                expected = json.dumps(
                    payload["payload"],
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            else:
                expected = str(payload["text"])
            try:
                current = self._read_text_locked(
                    path,
                    maximum_bytes=MAX_ARTIFACT_FRAME_BYTES,
                    missing_ok=True,
                    deadline=deadline,
                )
            except DurableArtifactProcessError:
                raise mutation_error from None
            if current != expected:
                raise mutation_error
            self._request(
                "SYNC_EXISTING",
                deadline=deadline,
                path=str(path),
            )

    def close(self) -> None:
        with self._lock:
            if self._closed and self._process is None:
                return
            self._closed = True
            process = self._process
            if process is None:
                return
            if self._terminate(process):
                self._process = None
                self._worker_nonce = ""
                self._replacement_blocked = False
            else:
                self._replacement_blocked = True


_SHARED_PROCESS: DurableArtifactProcess | None = None
_SHARED_PROCESS_LOCK = threading.Lock()


def shared_durable_artifact_process() -> DurableArtifactProcess:
    global _SHARED_PROCESS
    with _SHARED_PROCESS_LOCK:
        if _SHARED_PROCESS is None:
            _SHARED_PROCESS = DurableArtifactProcess()
        return _SHARED_PROCESS


__all__ = [
    "ARTIFACT_PROCESS_PROTOCOL",
    "DEFAULT_ARTIFACT_DEADLINE_SEC",
    "DEFAULT_ARTIFACT_REPLACE_ATTEMPTS",
    "DEFAULT_ARTIFACT_RETRY_DELAY_SEC",
    "DurableArtifactProcess",
    "DurableArtifactProcessError",
    "DurableArtifactProcessOutcomeUnknown",
    "DurableArtifactProcessTimeout",
    "shared_durable_artifact_process",
]
