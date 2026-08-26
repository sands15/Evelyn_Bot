from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path
from typing import Any

from evelyn_core.durable_artifact_process import ARTIFACT_PROCESS_PROTOCOL
from evelyn_core.durable_artifact_worker import (
    _RequestError,
    _execute,
    _open_temporary,
    _read_frame,
    _response_base,
    _serialize_json,
    _start_parent_watcher,
    _temporary_path,
    _validate_envelope,
    _validate_request,
    _write_frame,
)


def _claim_fault(
    state_path: Path,
    *,
    scenario: str,
    request: dict[str, Any],
    phase: str,
    temporary: Path | None = None,
) -> bool:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(state_path, flags, 0o600)
    except FileExistsError:
        return False
    state = {
        "scenario": scenario,
        "faultCount": 1,
        "pid": os.getpid(),
        "requestId": request["requestId"],
        "target": request["path"],
        "phase": phase,
        "tempPath": None if temporary is None else str(temporary),
    }
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _matches_fault(
    request: dict[str, Any],
    *,
    scenario: str,
    target: Path | None,
) -> bool:
    if target is not None and Path(request["path"]) != target:
        return False
    operation = request["operation"]
    if scenario == "stall_read_once":
        return operation == "READ_BOUNDED"
    return operation in {"ATOMIC_JSON_WRITE", "ATOMIC_TEXT_WRITE"}


def _stall_before_replace(
    request: dict[str, Any],
    *,
    state_path: Path,
    scenario: str,
) -> bool:
    target = Path(request["path"])
    temporary = _temporary_path(target, str(request["requestId"]))
    if not _claim_fault(
        state_path,
        scenario=scenario,
        request=request,
        phase="before_replace",
        temporary=temporary,
    ):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    text = (
        _serialize_json(request["payload"])
        if request["operation"] == "ATOMIC_JSON_WRITE"
        else str(request["text"])
    )
    with _open_temporary(temporary) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    threading.Event().wait()
    raise AssertionError("unreachable")


def _fault_once(
    request: dict[str, Any],
    *,
    scenario: str,
    state_path: Path,
) -> None:
    if scenario == "stall_before_replace_once":
        _stall_before_replace(
            request,
            state_path=state_path,
            scenario=scenario,
        )
        return
    phase = "after_replace" if scenario == "stall_after_replace_once" else "read"
    if not _claim_fault(
        state_path,
        scenario=scenario,
        request=request,
        phase=phase,
    ):
        return
    if scenario == "stall_after_replace_once":
        _execute(request)
    threading.Event().wait()
    raise AssertionError("unreachable")


def run(
    *,
    parent_pid: int,
    scenario: str,
    state_path: Path,
    target: Path | None,
) -> None:
    _start_parent_watcher(parent_pid)
    worker_nonce = os.urandom(16).hex()
    _write_frame(
        os.sys.stdout.buffer,
        {
            "protocol": ARTIFACT_PROCESS_PROTOCOL,
            "phase": "READY",
            "pid": os.getpid(),
            "workerNonce": worker_nonce,
        },
    )
    previous_sequence: int | None = None
    while True:
        request = _read_frame(os.sys.stdin.buffer)
        previous_sequence = _validate_envelope(
            request,
            worker_nonce=worker_nonce,
            previous_sequence=previous_sequence,
        )
        _write_frame(
            os.sys.stdout.buffer,
            _response_base(request, worker_nonce=worker_nonce, phase="PREPARED"),
        )
        try:
            _validate_request(request)
            if _matches_fault(request, scenario=scenario, target=target):
                _fault_once(
                    request,
                    scenario=scenario,
                    state_path=state_path,
                )
            result = _execute(request)
            response = _response_base(
                request,
                worker_nonce=worker_nonce,
                phase="COMMIT",
            )
            response.update(result)
        except _RequestError as exc:
            response = _response_base(
                request,
                worker_nonce=worker_nonce,
                phase="ABORT",
            )
            response["code"] = exc.code
        _write_frame(os.sys.stdout.buffer, response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        required=True,
        choices=(
            "stall_before_replace_once",
            "stall_after_replace_once",
            "stall_read_once",
        ),
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    arguments = parser.parse_args()
    run(
        parent_pid=arguments.parent_pid,
        scenario=arguments.scenario,
        state_path=arguments.state.resolve(),
        target=None if arguments.target is None else arguments.target.resolve(),
    )


if __name__ == "__main__":
    main()
