from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any


def _safe_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item) for item in value]
    return str(value)


def append_memory_writebehind_event(path: Path, payload: dict[str, Any], status: str) -> None:
    record = {
        "ts": round(time.time(), 3),
        "status": status,
        "source": payload.get("source"),
        "reason": payload.get("reason"),
        "session_key": payload.get("session_key"),
        "writebehind_mode": payload.get("writebehind_mode"),
        "writebehind_reason": payload.get("writebehind_reason"),
        "writebehind_error": payload.get("writebehind_error"),
        "store_long_term_memory": payload.get("store_long_term_memory"),
        "store_open_questions": payload.get("store_open_questions"),
        "store_minecraft_failure": payload.get("store_minecraft_failure"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(_safe_json_value(record), ensure_ascii=False, sort_keys=True) + "\n")


def mark_memory_writer_status(
    payload: dict[str, Any],
    status: str,
    *,
    event_path: Path | None = None,
    log: Callable[[str], Any] | None = None,
    **details: Any,
) -> dict[str, Any]:
    payload["writebehind_status"] = status
    payload["writebehind_queued"] = status in {"queued", "running"}
    payload["writebehind_updated_at"] = time.time()
    for key, value in details.items():
        payload[key] = value
    if event_path is not None:
        try:
            append_memory_writebehind_event(event_path, payload, status)
        except Exception as exc:
            payload["writebehind_event_error"] = repr(exc)
            if log is not None:
                log(f"[MEMORY WRITEBEHIND] event log failed: {exc!r}")
    return payload


def should_replace_existing_memory_task(decision_payload: dict[str, Any]) -> bool:
    return not bool(decision_payload.get("store_long_term_memory"))


def memory_writebehind_task_key(
    base_key: str,
    decision_payload: dict[str, Any],
    *,
    nonce: int | None = None,
) -> str:
    if should_replace_existing_memory_task(decision_payload):
        return base_key
    unique = time.monotonic_ns() if nonce is None else nonce
    return f"{base_key}:explicit:{unique}"


async def run_memory_writebehind_steps(
    payload: dict[str, Any],
    steps: list[Callable[[], Any | Awaitable[Any]]],
    *,
    log: Callable[[str], Any] | None = None,
    event_path: Path | None = None,
) -> None:
    mark_memory_writer_status(payload, "running", event_path=event_path, log=log)
    try:
        for step in steps:
            result = step()
            if inspect.isawaitable(result):
                await result
    except asyncio.CancelledError:
        mark_memory_writer_status(payload, "cancelled", event_path=event_path, log=log)
        raise
    except Exception as exc:
        mark_memory_writer_status(payload, "failed", event_path=event_path, log=log, writebehind_error=repr(exc))
        if log is not None:
            log(f"[MEMORY WRITEBEHIND] failed: {exc!r}")
        return
    mark_memory_writer_status(payload, "completed", event_path=event_path, log=log)
