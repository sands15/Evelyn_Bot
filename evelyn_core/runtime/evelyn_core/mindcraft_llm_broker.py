from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import secrets
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, TypeVar
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, web

from .config import (
    MEMORY_ROOT,
    QWEN_ADMISSION_QUEUE_TIMEOUT_SEC,
    SPECIALIST_LLM_TIMEOUT_SEC,
)
from .conversation_memory_exposure import (
    filter_conversation_history_for_memory_exposure,
    memory_receipt_ref_from_exposure,
)
from .conversation_memory_receipt import sanitize_memory_receipt_ref
from .memory_deletion_journal import (
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_error_code,
)
from .memory_exposure import (
    MemoryExposurePosition,
    memory_exposure_guard,
)


MINDCRAFT_LLM_REQUEST_SCHEMA = "mindcraft.llm-request.v1"
MINDCRAFT_LLM_RESULT_SCHEMA = "mindcraft.llm-result.v1"
MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA = (
    "mindcraft.llm-delivery-lease.v1"
)
MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA = "mindcraft.llm-delivery-ack.v1"
MINDCRAFT_LLM_TOKEN_HEADER = "X-Evelyn-Mindcraft-LLM-Token"
MINDCRAFT_LLM_TOKEN_FILE_ENV = "MINDCRAFT_LLM_BROKER_TOKEN_FILE"
MINDCRAFT_QWEN_EPOCH_FILE_ENV = "MINDCRAFT_QWEN_EPOCH_FILE"

MINDCRAFT_LLM_MAX_REQUEST_BYTES = 256 * 1024
MINDCRAFT_LLM_MAX_MESSAGES = 24
MINDCRAFT_LLM_MAX_MESSAGE_CHARS = 64 * 1024
MINDCRAFT_LLM_MAX_RESPONSE_BYTES = 128 * 1024
MINDCRAFT_LLM_MAX_RESPONSE_CHARS = 32 * 1024
MINDCRAFT_LLM_MAX_OUTSTANDING = 4
MINDCRAFT_LLM_MAX_SEEN_REQUESTS = 64
MINDCRAFT_LLM_DELIVERY_TTL_SEC = 660.0
MINDCRAFT_LLM_DISCONNECT_POLL_SEC = 0.25
MINDCRAFT_LLM_REQUEST_REPLAY_TTL_SEC = 660.0
MINDCRAFT_LLM_CLIENT_ACK_TIMEOUT_SEC = 10.0
MINDCRAFT_LLM_CLIENT_GRACE_SEC = 2.0

MINDCRAFT_LOCAL_LLM_URL = os.getenv(
    "MINDCRAFT_LOCAL_LLM_URL",
    "http://minecraft_llm:9823/v1/chat/completions",
)
MINDCRAFT_LOCAL_MODEL = os.getenv(
    "MINDCRAFT_LOCAL_MODEL",
    "Qwen3-14B-Q4_K_M.gguf",
)
MINDCRAFT_LOCAL_TIMEOUT_SEC = 90.0
MINDCRAFT_LOCAL_HARD_DRAIN_TIMEOUT_SEC = 120.0
MINDCRAFT_TASK_TIMEOUT_SEC = 6.0
MINDCRAFT_ROUTER_LLM_URL = os.getenv(
    "MINDCRAFT_ROUTER_URL",
    os.getenv(
        "ROUTER_LLM_URL",
        "http://router_llm:9822/v1/chat/completions",
    ),
)
MINDCRAFT_ROUTER_MODEL = os.getenv(
    "MINDCRAFT_ROUTER_MODEL",
    "gemma-4-E2B-it-Q4_K_M.gguf",
)
MINDCRAFT_ROUTER_TIMEOUT_SEC = 4.0

_TOKEN = web.AppKey("mindcraft_llm_broker_token", str)
_LEASES = web.AppKey("mindcraft_llm_delivery_leases", dict)
_SEEN_REQUESTS = web.AppKey("mindcraft_llm_seen_requests", dict)
_QWEN_ADMISSION = web.AppKey("qwen_admission", object)
_QWEN_INVOCATIONS = web.AppKey("qwen_invocations", set)
_QWEN_INFLIGHT_MARKER = web.AppKey("qwen_inflight_marker", object)
_QWEN_EPOCH = web.AppKey("qwen_epoch", object)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_LEASE_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_REQUEST_KINDS = frozenset(
    {
        "action",
        "chat",
        "classifier",
        "memory",
        "recovery",
        "router",
        "specialist",
        "subgoal",
        "task",
    }
)

_T = TypeVar("_T")


class _RequestError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class _DeliveryLease:
    request_id: str
    acknowledged: asyncio.Future[str]
    released: asyncio.Future[None]


@dataclass
class _AdmissionWaiter:
    token: object
    ready: asyncio.Future[None]


@dataclass
class _QwenInvocation:
    admitted: asyncio.Future[float]
    result: asyncio.Future[str]
    delivery_done: asyncio.Future[None]
    finalized: asyncio.Future[None]
    task: asyncio.Task[None] | None = None


class _QwenAdmissionOwner:
    """One fair, bounded Qwen inference owner for the whole Bot API."""

    def __init__(self, *, max_waiters: int) -> None:
        self._lock = asyncio.Lock()
        self._owner: object | None = None
        self._waiters: deque[_AdmissionWaiter] = deque()
        self._max_waiters = max(0, int(max_waiters))
        self._poisoned = False

    @property
    def available(self) -> bool:
        return not self._poisoned

    def _grant_next_locked(self) -> None:
        while self._owner is None and self._waiters:
            waiter = self._waiters.popleft()
            if waiter.ready.cancelled():
                continue
            self._owner = waiter.token
            if not waiter.ready.done():
                waiter.ready.set_result(None)
            return

    async def _withdraw(self, token: object) -> None:
        async with self._lock:
            if self._owner is token:
                self._owner = None
                self._grant_next_locked()
                return
            for waiter in tuple(self._waiters):
                if waiter.token is token:
                    self._waiters.remove(waiter)
                    if not waiter.ready.done():
                        waiter.ready.cancel()
                    return

    async def poison(self) -> None:
        async with self._lock:
            self._poisoned = True
            for waiter in tuple(self._waiters):
                if not waiter.ready.done():
                    waiter.ready.set_exception(
                        _RequestError("qwen_admission_unavailable", 503)
                    )
            self._waiters.clear()

    async def acquire(
        self,
        request: web.Request,
        *,
        abandoned: asyncio.Future[Any] | None = None,
    ) -> object:
        token = object()
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        async with self._lock:
            if self._poisoned:
                raise _RequestError("qwen_admission_unavailable", 503)
            if self._owner is None and not self._waiters:
                self._owner = token
                ready.set_result(None)
            else:
                if len(self._waiters) >= self._max_waiters:
                    raise _RequestError("qwen_admission_busy", 503)
                self._waiters.append(_AdmissionWaiter(token, ready))
        deadline = loop.time() + QWEN_ADMISSION_QUEUE_TIMEOUT_SEC
        try:
            while not ready.done():
                if abandoned is not None and abandoned.done():
                    raise ConnectionResetError(
                        "qwen_admission_client_disconnected"
                    )
                transport = request.transport
                if transport is None or transport.is_closing():
                    raise ConnectionResetError("qwen_admission_client_disconnected")
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise _RequestError("qwen_admission_queue_timeout", 503)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(ready),
                        timeout=min(MINDCRAFT_LLM_DISCONNECT_POLL_SEC, remaining),
                    )
                except TimeoutError:
                    continue
            ready.result()
            if self._poisoned:
                raise _RequestError("qwen_admission_unavailable", 503)
            if abandoned is not None and abandoned.done():
                raise ConnectionResetError(
                    "qwen_admission_client_disconnected"
                )
            transport = request.transport
            if transport is None or transport.is_closing():
                raise ConnectionResetError("qwen_admission_client_disconnected")
            if loop.time() >= deadline:
                raise _RequestError("qwen_admission_queue_timeout", 503)
            return token
        except BaseException:
            await asyncio.shield(self._withdraw(token))
            raise

    async def release(self, token: object) -> None:
        await asyncio.shield(self._withdraw(token))

    @contextlib.asynccontextmanager
    async def slot(self, request: web.Request) -> AsyncIterator[None]:
        token = await self.acquire(request)
        try:
            yield
        finally:
            await self.release(token)


def _no_store_json(
    payload: Mapping[str, Any],
    *,
    status: int = 200,
) -> web.Response:
    response = web.json_response(dict(payload), status=status)
    response.headers.update(
        {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )
    return response


def _failure(code: str, *, status: int) -> web.Response:
    return _no_store_json(
        {
            "ok": False,
            "error": code,
            "contentFree": True,
        },
        status=status,
    )


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate_json_key")
        payload[key] = value
    return payload


async def _read_json(
    request: web.Request,
    *,
    maximum_bytes: int,
    error_code: str,
) -> Any:
    if (
        request.content_length is not None
        and request.content_length > maximum_bytes
    ):
        raise _RequestError(error_code, 413)
    encoded = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        if len(encoded) + len(chunk) > maximum_bytes:
            raise _RequestError(error_code, 413)
        encoded.extend(chunk)
    try:
        return json.loads(
            bytes(encoded).decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise _RequestError(error_code) from exc


def _token_path() -> Path | None:
    configured = str(os.getenv(MINDCRAFT_LLM_TOKEN_FILE_ENV) or "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        raise RuntimeError("mindcraft_llm_token_path_invalid")
    return path


def _canonical_token(value: str) -> str:
    token = str(value or "").strip()
    if _TOKEN_PATTERN.fullmatch(token) is None:
        raise RuntimeError("mindcraft_llm_token_invalid")
    return token


def _load_or_create_token(path: Path) -> str:
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError("mindcraft_llm_token_path_invalid")
    if target.exists():
        if not target.is_file() or target.stat().st_size > 256:
            raise RuntimeError("mindcraft_llm_token_path_invalid")
        return _canonical_token(target.read_text(encoding="utf-8"))
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise RuntimeError("mindcraft_llm_token_path_invalid")
    generated = secrets.token_urlsafe(48)
    try:
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        if target.is_symlink() or not target.is_file():
            raise RuntimeError("mindcraft_llm_token_path_invalid") from None
        return _canonical_token(target.read_text(encoding="utf-8"))
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(generated + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return generated


def _qwen_marker_path(token_path: Path | None) -> Path | None:
    if token_path is None:
        return None
    return token_path.with_name("qwen-inflight")


def _qwen_epoch_path() -> Path | None:
    configured = str(os.getenv(MINDCRAFT_QWEN_EPOCH_FILE_ENV) or "").strip()
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute():
        raise RuntimeError("qwen_admission_unavailable")
    return path


def _load_qwen_epoch(path: Path | None) -> str:
    if (
        path is None
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 128
    ):
        raise RuntimeError("qwen_admission_unavailable")
    epoch = path.read_text(encoding="utf-8").strip()
    if _REQUEST_ID_PATTERN.fullmatch(epoch) is None:
        raise RuntimeError("qwen_admission_unavailable")
    return epoch


def _load_qwen_marker(path: Path | None) -> str:
    if (
        path is None
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 128
    ):
        raise RuntimeError("qwen_admission_recovery_required")
    epoch = path.read_text(encoding="utf-8").strip()
    if _REQUEST_ID_PATTERN.fullmatch(epoch) is None:
        raise RuntimeError("qwen_admission_recovery_required")
    return epoch


def _claim_qwen_marker(path: Path | None, epoch_path: Path | None) -> None:
    if path is None:
        raise RuntimeError("qwen_admission_unavailable")
    epoch = _load_qwen_epoch(epoch_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except (FileExistsError, OSError) as exc:
        raise RuntimeError("qwen_admission_recovery_required") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write((epoch + "\n").encode("ascii"))
        handle.flush()
        os.fsync(handle.fileno())
    if _load_qwen_epoch(epoch_path) != epoch:
        raise RuntimeError("qwen_admission_recovery_required")


def _clear_qwen_marker(path: Path | None, *, missing_ok: bool = False) -> None:
    if path is None:
        raise RuntimeError("qwen_admission_unavailable")
    if not path.exists():
        if missing_ok and not path.is_symlink():
            return
        raise RuntimeError("qwen_admission_recovery_required")
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("qwen_admission_recovery_required")
    path.unlink()


async def _recover_stale_qwen_marker(
    path: Path | None,
    epoch_path: Path | None,
) -> None:
    if path is None or (not path.exists() and not path.is_symlink()):
        return
    previous_epoch = _load_qwen_marker(path)
    deadline = (
        asyncio.get_running_loop().time()
        + MINDCRAFT_LOCAL_HARD_DRAIN_TIMEOUT_SEC
    )
    parsed = urlsplit(MINDCRAFT_LOCAL_LLM_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("qwen_admission_recovery_required")
    health_url = parsed._replace(
        path="/health",
        query="",
        fragment="",
    ).geturl()
    timeout = ClientTimeout(total=2.0)
    async with ClientSession(timeout=timeout) as session:
        while True:
            if not path.exists() and not path.is_symlink():
                return
            try:
                candidate_epoch = _load_qwen_epoch(epoch_path)
                if candidate_epoch != previous_epoch:
                    async with session.get(
                        health_url,
                        allow_redirects=False,
                    ) as response:
                        encoded = await response.content.read(4097)
                    payload = json.loads(
                        encoded.decode("utf-8"),
                        object_pairs_hook=_strict_json_object,
                    )
                    if (
                        response.status == 200
                        and len(encoded) <= 4096
                        and isinstance(payload, Mapping)
                        and payload.get("status") == "ok"
                        and _load_qwen_epoch(epoch_path) == candidate_epoch
                    ):
                        _clear_qwen_marker(path, missing_ok=True)
                        return
            except (
                ClientError,
                TimeoutError,
                UnicodeError,
                ValueError,
                TypeError,
                RecursionError,
                RuntimeError,
            ):
                pass
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("qwen_admission_recovery_required")
            await asyncio.sleep(min(MINDCRAFT_LLM_DISCONNECT_POLL_SEC, remaining))


def _authorized(request: web.Request) -> tuple[bool, str, int]:
    expected = request.app.get(_TOKEN, "")
    if not expected:
        return False, "mindcraft_llm_broker_unconfigured", 503
    presented = str(request.headers.get(MINDCRAFT_LLM_TOKEN_HEADER) or "")
    if not hmac.compare_digest(presented, expected):
        return False, "mindcraft_llm_broker_unauthorized", 403
    return True, "", 200


def _messages(value: Any) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MINDCRAFT_LLM_MAX_MESSAGES
    ):
        raise _RequestError("mindcraft_llm_request_invalid")
    normalized: list[dict[str, Any]] = []
    total_chars = 0
    for raw in value:
        if not isinstance(raw, Mapping):
            raise _RequestError("mindcraft_llm_request_invalid")
        role = raw.get("role")
        content = raw.get("content")
        allowed_keys = {"role", "content", "memoryReceiptRef"}
        if (
            set(raw) != allowed_keys
            or role not in {"assistant", "system", "user"}
            or not isinstance(content, str)
            or not content
            or len(content) > MINDCRAFT_LLM_MAX_MESSAGE_CHARS
        ):
            raise _RequestError("mindcraft_llm_request_invalid")
        total_chars += len(content)
        if total_chars > MINDCRAFT_LLM_MAX_REQUEST_BYTES:
            raise _RequestError("mindcraft_llm_request_invalid", 413)
        receipt = sanitize_memory_receipt_ref(raw.get("memoryReceiptRef"))
        if receipt is None:
            raise _RequestError("mindcraft_llm_request_invalid")
        message: dict[str, Any] = {
            "role": role,
            "content": content,
            "memoryReceiptRef": receipt,
        }
        normalized.append(message)
    return tuple(normalized)


def _request_payload(
    value: Any,
) -> tuple[str, str, tuple[dict[str, Any], ...], dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema",
        "requestId",
        "requestKind",
        "messages",
        "historyReceiptRef",
    }:
        raise _RequestError("mindcraft_llm_request_invalid")
    request_id = str(value.get("requestId") or "")
    kind = value.get("requestKind")
    if (
        value.get("schema") != MINDCRAFT_LLM_REQUEST_SCHEMA
        or _REQUEST_ID_PATTERN.fullmatch(request_id) is None
        or kind not in _REQUEST_KINDS
    ):
        raise _RequestError("mindcraft_llm_request_invalid")
    history_receipt_ref = sanitize_memory_receipt_ref(
        value.get("historyReceiptRef")
    )
    if history_receipt_ref is None:
        raise _RequestError("mindcraft_llm_request_invalid")
    return (
        request_id,
        str(kind),
        _messages(value.get("messages")),
        history_receipt_ref,
    )


def _admit_request_id(app: web.Application, request_id: str) -> None:
    now = asyncio.get_running_loop().time()
    seen: dict[str, float] = app[_SEEN_REQUESTS]
    active_request_ids = {
        lease.request_id
        for lease in app.get(_LEASES, {}).values()
    }
    for expired in tuple(
        key
        for key, deadline in seen.items()
        if deadline <= now and key not in active_request_ids
    ):
        seen.pop(expired, None)
    if request_id in seen:
        raise _RequestError("mindcraft_llm_request_replayed", 409)
    if len(seen) >= MINDCRAFT_LLM_MAX_SEEN_REQUESTS:
        completed = (
            (key, deadline)
            for key, deadline in seen.items()
            if key not in active_request_ids
        )
        oldest_completed = min(
            completed,
            key=lambda item: item[1],
            default=None,
        )
        if oldest_completed is None:
            raise _RequestError("mindcraft_llm_broker_busy", 503)
        seen.pop(oldest_completed[0], None)
    seen[request_id] = now + MINDCRAFT_LLM_REQUEST_REPLAY_TTL_SEC


def _screen_history(
    messages: tuple[dict[str, Any], ...],
    history_receipt_ref: Mapping[str, Any],
) -> tuple[Any, list[dict[str, str]]]:
    role_marker = "_mindcraftOriginalRole"
    aggregate_marker = "_mindcraftAggregateReceipt"
    candidates: list[dict[str, Any]] = []
    for raw in messages:
        message = dict(raw)
        message[role_marker] = message["role"]
        message["role"] = "assistant"
        candidates.append(message)
    candidates.append(
        {
            "role": "assistant",
            "content": "mindcraft_history_aggregate_receipt",
            "memoryReceiptRef": dict(history_receipt_ref),
            aggregate_marker: True,
        }
    )
    outcome = filter_conversation_history_for_memory_exposure(
        candidates,
        memory_index_dir=Path(MEMORY_ROOT) / "memory_index",
    )
    if (
        outcome.dropped_missing_receipt_count
        or outcome.dropped_unattributed_count
        or outcome.dropped_stale_version_count
        or outcome.dropped_tombstoned_count
        or len(outcome.messages) != len(candidates)
    ):
        raise _RequestError("mindcraft_llm_history_stale", 409)
    projected: list[dict[str, str]] = []
    for raw in outcome.messages:
        if raw.get(aggregate_marker) is True:
            continue
        role = raw.get(role_marker)
        content = raw.get("content")
        if role not in {"assistant", "system", "user"} or not isinstance(
            content,
            str,
        ):
            raise MemoryDeletionJournalIntegrityError()
        projected.append({"role": role, "content": content})
    if not projected:
        raise _RequestError("mindcraft_llm_history_unavailable", 409)
    return outcome, projected


def _upstream_policy(
    kind: str,
    messages: list[dict[str, str]],
) -> tuple[str, float, dict[str, Any]]:
    if kind == "router":
        return (
            MINDCRAFT_ROUTER_LLM_URL,
            MINDCRAFT_ROUTER_TIMEOUT_SEC,
            {
                "model": MINDCRAFT_ROUTER_MODEL,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 24,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
    if kind == "task":
        return (
            MINDCRAFT_LOCAL_LLM_URL,
            MINDCRAFT_TASK_TIMEOUT_SEC,
            {
                "model": MINDCRAFT_LOCAL_MODEL,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 384,
                "stream": False,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
    if kind == "specialist":
        return (
            MINDCRAFT_LOCAL_LLM_URL,
            max(0.1, float(SPECIALIST_LLM_TIMEOUT_SEC)),
            {
                "model": MINDCRAFT_LOCAL_MODEL,
                "messages": messages,
                "temperature": 0,
                "top_p": 0.8,
                "max_tokens": 256,
                "stream": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
    max_tokens = {
        "action": 64,
        "chat": 160,
        "classifier": 8,
        "memory": 256,
        "recovery": 512,
        "subgoal": 512,
    }[kind]
    payload: dict[str, Any] = {
        "model": MINDCRAFT_LOCAL_MODEL,
        "messages": messages,
        "temperature": 0.05 if kind in {"action", "recovery", "subgoal"} else 0.1,
        "top_p": 0.8,
        "max_tokens": max_tokens,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if kind in {"action", "chat", "memory"}:
        payload["stop"] = ["***"]
    return MINDCRAFT_LOCAL_LLM_URL, MINDCRAFT_LOCAL_TIMEOUT_SEC, payload


async def _upstream_json(
    response: Any,
    *,
    on_eof: Callable[[], None] | None = None,
) -> Mapping[str, Any]:
    encoded = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        if len(encoded) + len(chunk) > MINDCRAFT_LLM_MAX_RESPONSE_BYTES:
            raise RuntimeError("mindcraft_llm_upstream_invalid")
        encoded.extend(chunk)
    if on_eof is not None:
        on_eof()
    if response.status != 200:
        raise RuntimeError("mindcraft_llm_upstream_failed")
    try:
        payload = json.loads(
            bytes(encoded).decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise RuntimeError("mindcraft_llm_upstream_invalid") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("mindcraft_llm_upstream_invalid")
    return payload


def _response_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise RuntimeError("mindcraft_llm_upstream_invalid")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if (
        not isinstance(content, str)
        or not content.strip()
        or len(content) > MINDCRAFT_LLM_MAX_RESPONSE_CHARS
    ):
        raise RuntimeError("mindcraft_llm_upstream_invalid")
    return content


def _consume_future_result(future: asyncio.Future[Any]) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        future.exception()


async def _run_qwen_llm_invocation(
    request: web.Request,
    invocation: _QwenInvocation,
    *,
    outcome: Any,
    url: str,
    upstream_payload: Mapping[str, Any],
) -> None:
    owner = request.app[_QWEN_ADMISSION]
    marker_path = request.app[_QWEN_INFLIGHT_MARKER]
    epoch_path = request.app[_QWEN_EPOCH]
    token: object | None = None
    marker_claimed = False
    try:
        token = await owner.acquire(
            request,
            abandoned=invocation.delivery_done,
        )
        if invocation.delivery_done.done():
            if not invocation.finalized.done():
                invocation.finalized.set_result(None)
            return
        with memory_exposure_guard(
            expected_position=outcome.memory_exposure_position,
            required=outcome.memory_exposure_position is not None,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        ):
            try:
                try:
                    _claim_qwen_marker(marker_path, epoch_path)
                    marker_claimed = True
                except RuntimeError as exc:
                    if str(exc) == "qwen_admission_recovery_required":
                        await owner.poison()
                    raise _RequestError(str(exc), 503) from exc
                if not invocation.admitted.done():
                    invocation.admitted.set_result(
                        asyncio.get_running_loop().time()
                    )
                timeout = ClientTimeout(
                    total=MINDCRAFT_LOCAL_HARD_DRAIN_TIMEOUT_SEC
                )
                async with ClientSession(timeout=timeout) as session:
                    def upstream_eof() -> None:
                        nonlocal marker_claimed
                        _clear_qwen_marker(marker_path)
                        marker_claimed = False

                    async with session.post(
                        url,
                        json=dict(upstream_payload),
                        allow_redirects=False,
                    ) as upstream:
                        upstream_result = await _upstream_json(
                            upstream,
                            on_eof=upstream_eof,
                        )
                        content = _response_content(upstream_result)
                        if not invocation.result.done():
                            invocation.result.set_result(content)
            except (TimeoutError, ClientError) as exc:
                await owner.poison()
                raise _RequestError(
                    "qwen_admission_unavailable",
                    503,
                ) from exc
            await owner.release(token)
            token = None
            await asyncio.shield(invocation.delivery_done)
        if not invocation.finalized.done():
            invocation.finalized.set_result(None)
    except asyncio.CancelledError:
        if marker_claimed:
            await owner.poison()
        if not invocation.result.done():
            invocation.result.cancel()
        if not invocation.finalized.done():
            invocation.finalized.set_exception(
                _RequestError("qwen_admission_unavailable", 503)
            )
        raise
    except Exception as exc:
        if marker_claimed:
            await owner.poison()
        if not invocation.result.done():
            invocation.result.set_exception(exc)
        if not invocation.finalized.done():
            invocation.finalized.set_exception(exc)
    finally:
        if token is not None:
            await owner.release(token)


def _start_qwen_invocation(
    request: web.Request,
    *,
    outcome: Any,
    url: str,
    upstream_payload: Mapping[str, Any],
) -> _QwenInvocation:
    loop = asyncio.get_running_loop()
    invocation = _QwenInvocation(
        admitted=loop.create_future(),
        result=loop.create_future(),
        delivery_done=loop.create_future(),
        finalized=loop.create_future(),
    )
    invocation.result.add_done_callback(_consume_future_result)
    invocation.finalized.add_done_callback(_consume_future_result)
    task = asyncio.create_task(
        _run_qwen_llm_invocation(
            request,
            invocation,
            outcome=outcome,
            url=url,
            upstream_payload=upstream_payload,
        )
    )
    invocation.task = task
    tasks: set[asyncio.Task[None]] = request.app[_QWEN_INVOCATIONS]
    tasks.add(task)

    def finished(done: asyncio.Task[None]) -> None:
        tasks.discard(done)
        _consume_future_result(done)

    task.add_done_callback(finished)
    return invocation


def _bind_delivery_release(
    invocation: _QwenInvocation,
    delivery_lease: _DeliveryLease,
) -> None:
    def finalized(done: asyncio.Future[None]) -> None:
        if delivery_lease.released.done():
            return
        if done.cancelled():
            delivery_lease.released.set_exception(
                _RequestError("qwen_admission_unavailable", 503)
            )
            return
        error = done.exception()
        if error is not None:
            delivery_lease.released.set_exception(error)
            return
        delivery_lease.released.set_result(None)

    invocation.finalized.add_done_callback(finalized)


async def _wait_for_qwen_result(
    request: web.Request,
    invocation: _QwenInvocation,
    *,
    inference_timeout_sec: float,
) -> str:
    while not invocation.admitted.done():
        if invocation.result.done():
            return invocation.result.result()
        transport = request.transport
        if transport is None or transport.is_closing():
            raise ConnectionResetError("qwen_admission_client_disconnected")
        await asyncio.wait(
            {invocation.admitted, invocation.result},
            timeout=MINDCRAFT_LLM_DISCONNECT_POLL_SEC,
            return_when=asyncio.FIRST_COMPLETED,
        )
    admitted_at = invocation.admitted.result()
    deadline = admitted_at + max(0.1, float(inference_timeout_sec))
    while not invocation.result.done():
        transport = request.transport
        if transport is None or transport.is_closing():
            raise ConnectionResetError("qwen_admission_client_disconnected")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise _RequestError("qwen_inference_timeout", 503)
        await asyncio.wait(
            {invocation.result},
            timeout=min(MINDCRAFT_LLM_DISCONNECT_POLL_SEC, remaining),
        )
    return invocation.result.result()


def _broker_client_urls(value: str) -> tuple[str, str]:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/internal/mindcraft-llm"
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("mindcraft_llm_broker_url_invalid")
    return parsed.geturl(), parsed._replace(
        path="/internal/mindcraft-llm/ack"
    ).geturl()


def _broker_client_token(value: str | Path) -> str:
    path = Path(value)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 256
    ):
        raise RuntimeError("mindcraft_llm_broker_token_invalid")
    return _canonical_token(path.read_text(encoding="utf-8"))


async def _broker_failure_code(response: Any) -> str:
    encoded = await response.content.read(4097)
    if len(encoded) > 4096:
        return ""
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return ""
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"ok", "error", "contentFree"}
        or payload.get("ok") is not False
        or payload.get("contentFree") is not True
        or not isinstance(payload.get("error"), str)
    ):
        return ""
    return str(payload["error"])


async def _broker_result_frame(
    response: Any,
    *,
    request_id: str,
    expected_receipt: Mapping[str, Any],
) -> tuple[str, str]:
    try:
        encoded = await response.content.readline()
    except (ValueError, ClientError) as exc:
        raise RuntimeError("mindcraft_llm_broker_frame_invalid") from exc
    if (
        not encoded
        or not encoded.endswith(b"\n")
        or len(encoded) > MINDCRAFT_LLM_MAX_RESPONSE_BYTES
    ):
        raise RuntimeError("mindcraft_llm_broker_frame_invalid")
    try:
        frame = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise RuntimeError("mindcraft_llm_broker_frame_invalid") from exc
    if not isinstance(frame, Mapping) or set(frame) != {
        "schema",
        "requestId",
        "content",
        "memoryReceiptRef",
        "deliveryLease",
    }:
        raise RuntimeError("mindcraft_llm_broker_frame_invalid")
    content = frame.get("content")
    receipt = sanitize_memory_receipt_ref(frame.get("memoryReceiptRef"))
    lease = frame.get("deliveryLease")
    if (
        frame.get("schema") != MINDCRAFT_LLM_RESULT_SCHEMA
        or frame.get("requestId") != request_id
        or not isinstance(content, str)
        or not content.strip()
        or len(content) > MINDCRAFT_LLM_MAX_RESPONSE_CHARS
        or receipt is None
        or receipt != dict(expected_receipt)
        or not isinstance(lease, Mapping)
        or set(lease) != {"schema", "leaseId", "ttlMs", "contentFree"}
        or lease.get("schema") != MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA
        or _LEASE_ID_PATTERN.fullmatch(str(lease.get("leaseId") or ""))
        is None
        or not isinstance(lease.get("ttlMs"), int)
        or isinstance(lease.get("ttlMs"), bool)
        or not 0 < int(lease["ttlMs"]) <= int(
            MINDCRAFT_LLM_DELIVERY_TTL_SEC * 1000
        )
        or lease.get("contentFree") is not True
    ):
        raise RuntimeError("mindcraft_llm_broker_frame_invalid")
    return content, str(lease["leaseId"])


async def _complete_broker_delivery(
    _session: Any,
    response: Any,
    *,
    ack_url: str,
    headers: Mapping[str, str],
    request_id: str,
    lease_id: str,
    outcome: str,
) -> None:
    async def acknowledge() -> None:
        timeout = ClientTimeout(total=MINDCRAFT_LLM_CLIENT_ACK_TIMEOUT_SEC)
        async with ClientSession(timeout=timeout) as ack_session:
            async with ack_session.post(
                ack_url,
                headers=dict(headers),
                json={
                    "schema": MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA,
                    "requestId": request_id,
                    "leaseId": lease_id,
                    "outcome": outcome,
                    "contentFree": True,
                },
                allow_redirects=False,
            ) as ack:
                if ack.status != 200:
                    raise RuntimeError("mindcraft_llm_delivery_ack_failed")
                encoded = await ack.content.read(4097)
                if len(encoded) > 4096:
                    raise RuntimeError("mindcraft_llm_delivery_ack_failed")
                try:
                    payload = json.loads(
                        encoded.decode("utf-8"),
                        object_pairs_hook=_strict_json_object,
                    )
                except (
                    UnicodeError,
                    ValueError,
                    TypeError,
                    RecursionError,
                ) as exc:
                    raise RuntimeError(
                        "mindcraft_llm_delivery_ack_failed"
                    ) from exc
                if payload != {"ok": True, "contentFree": True}:
                    raise RuntimeError("mindcraft_llm_delivery_ack_failed")

    ack_task = asyncio.create_task(acknowledge())
    drain_task = asyncio.create_task(response.content.read(1))
    try:
        _ack, trailing = await asyncio.gather(ack_task, drain_task)
        if trailing:
            raise RuntimeError("mindcraft_llm_broker_frame_invalid")
    except BaseException:
        response.close()
        for task in (ack_task, drain_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(ack_task, drain_task, return_exceptions=True)
        raise


async def request_mindcraft_llm_from_broker(
    *,
    session: Any,
    broker_url: str,
    token_file: str | Path,
    request_kind: str,
    messages: list[Mapping[str, Any]],
    expected_memory_exposure: MemoryExposurePosition | None,
    memory_index_dir: Path,
    inference_timeout_sec: float,
    consume: Callable[[str], _T],
    queue_timeout_sec: float = QWEN_ADMISSION_QUEUE_TIMEOUT_SEC,
) -> _T:
    if request_kind not in _REQUEST_KINDS:
        raise RuntimeError("mindcraft_llm_request_invalid")
    endpoint, ack_url = _broker_client_urls(broker_url)
    token = _broker_client_token(token_file)
    receipt = memory_receipt_ref_from_exposure(expected_memory_exposure)
    projected: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role") if isinstance(message, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if role not in {"assistant", "system", "user"} or not isinstance(
            content,
            str,
        ) or not content:
            raise RuntimeError("mindcraft_llm_request_invalid")
        projected.append(
            {
                "role": role,
                "content": content,
                "memoryReceiptRef": dict(receipt),
            }
        )
    if not projected or len(projected) > MINDCRAFT_LLM_MAX_MESSAGES:
        raise RuntimeError("mindcraft_llm_request_invalid")
    request_id = str(uuid.uuid4())
    headers = {MINDCRAFT_LLM_TOKEN_HEADER: token}
    timeout = ClientTimeout(
        total=(
            max(0.1, float(queue_timeout_sec))
            + max(0.1, float(inference_timeout_sec))
            + MINDCRAFT_LLM_CLIENT_ACK_TIMEOUT_SEC
            + MINDCRAFT_LLM_CLIENT_GRACE_SEC
        )
    )
    with memory_exposure_guard(
        expected_position=expected_memory_exposure,
        required=expected_memory_exposure is not None,
        index_dir=Path(memory_index_dir),
    ):
        async with session.post(
            endpoint,
            headers=headers,
            json={
                "schema": MINDCRAFT_LLM_REQUEST_SCHEMA,
                "requestId": request_id,
                "requestKind": request_kind,
                "messages": projected,
                "historyReceiptRef": dict(receipt),
            },
            timeout=timeout,
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                code = await _broker_failure_code(response)
                if code in {
                    "qwen_admission_queue_timeout",
                    "qwen_inference_timeout",
                }:
                    raise TimeoutError(code)
                raise RuntimeError("mindcraft_llm_broker_failed")
            content, lease_id = await _broker_result_frame(
                response,
                request_id=request_id,
                expected_receipt=receipt,
            )
            try:
                result = consume(content)
            except BaseException:
                with contextlib.suppress(BaseException):
                    await _complete_broker_delivery(
                        session,
                        response,
                        ack_url=ack_url,
                        headers=headers,
                        request_id=request_id,
                        lease_id=lease_id,
                        outcome="discarded",
                    )
                raise
            await _complete_broker_delivery(
                session,
                response,
                ack_url=ack_url,
                headers=headers,
                request_id=request_id,
                lease_id=lease_id,
                outcome="delivered",
            )
            return result


async def _wait_for_delivery(
    request: web.Request,
    delivery_lease: _DeliveryLease,
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MINDCRAFT_LLM_DELIVERY_TTL_SEC
    while not delivery_lease.acknowledged.done():
        transport = request.transport
        if transport is None or transport.is_closing():
            return "discarded"
        remaining = deadline - loop.time()
        if remaining <= 0:
            return "discarded"
        try:
            return await asyncio.wait_for(
                asyncio.shield(delivery_lease.acknowledged),
                timeout=min(
                    MINDCRAFT_LLM_DISCONNECT_POLL_SEC,
                    remaining,
                ),
            )
        except TimeoutError:
            continue
    return delivery_lease.acknowledged.result()


async def mindcraft_llm_broker_context(
    app: web.Application,
) -> AsyncIterator[None]:
    token_path = _token_path()
    epoch_path = _qwen_epoch_path()
    app[_TOKEN] = (
        _load_or_create_token(token_path) if token_path is not None else ""
    )
    app[_QWEN_INFLIGHT_MARKER] = _qwen_marker_path(token_path)
    app[_QWEN_EPOCH] = epoch_path
    await _recover_stale_qwen_marker(
        app[_QWEN_INFLIGHT_MARKER],
        epoch_path,
    )
    app[_LEASES] = {}
    app[_SEEN_REQUESTS] = {}
    app[_QWEN_ADMISSION] = _QwenAdmissionOwner(
        max_waiters=max(0, MINDCRAFT_LLM_MAX_OUTSTANDING - 1)
    )
    app[_QWEN_INVOCATIONS] = set()
    try:
        yield
    finally:
        await app[_QWEN_ADMISSION].poison()
        leases = app.get(_LEASES, {})
        for lease in tuple(leases.values()):
            if not lease.acknowledged.done():
                lease.acknowledged.set_result("discarded")
        await asyncio.sleep(0)
        leases.clear()
        app[_SEEN_REQUESTS].clear()
        tasks = tuple(app[_QWEN_INVOCATIONS])
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        app[_QWEN_INVOCATIONS].clear()


async def _shutdown_qwen_admission(app: web.Application) -> None:
    owner = app.get(_QWEN_ADMISSION)
    if owner is not None:
        await owner.poison()


async def mindcraft_llm_handler(
    request: web.Request,
) -> web.StreamResponse:
    authorized, error, status = _authorized(request)
    if not authorized:
        return _failure(error, status=status)
    stream: web.StreamResponse | None = None
    lease_id = ""
    delivery_lease: _DeliveryLease | None = None
    invocation: _QwenInvocation | None = None
    router_delivery_finalized = False
    leases: dict[str, _DeliveryLease] = request.app[_LEASES]
    try:
        payload = await _read_json(
            request,
            maximum_bytes=MINDCRAFT_LLM_MAX_REQUEST_BYTES,
            error_code="mindcraft_llm_request_invalid",
        )
        request_id, kind, messages, history_receipt_ref = _request_payload(
            payload
        )
        _admit_request_id(request.app, request_id)
        outcome, upstream_messages = _screen_history(
            messages,
            history_receipt_ref,
        )
        if len(leases) >= MINDCRAFT_LLM_MAX_OUTSTANDING:
            raise _RequestError("mindcraft_llm_broker_busy", 503)
        lease_id = secrets.token_hex(32)
        loop = asyncio.get_running_loop()
        delivery_lease = _DeliveryLease(
            request_id=request_id,
            acknowledged=loop.create_future(),
            released=loop.create_future(),
        )
        delivery_lease.released.add_done_callback(_consume_future_result)
        leases[lease_id] = delivery_lease
        url, timeout_sec, upstream_payload = _upstream_policy(
            kind,
            upstream_messages,
        )

        async def deliver(content: str) -> web.StreamResponse:
            nonlocal stream
            stream = web.StreamResponse(
                status=200,
                headers={
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "Content-Type": "application/x-ndjson; charset=utf-8",
                },
            )
            await stream.prepare(request)
            frame = {
                "schema": MINDCRAFT_LLM_RESULT_SCHEMA,
                "requestId": request_id,
                "content": content,
                "memoryReceiptRef": dict(outcome.memory_receipt_ref),
                "deliveryLease": {
                    "schema": MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA,
                    "leaseId": lease_id,
                    "ttlMs": int(
                        MINDCRAFT_LLM_DELIVERY_TTL_SEC * 1000
                    ),
                    "contentFree": True,
                },
            }
            await stream.write(
                (
                    json.dumps(
                        frame,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
            )
            await _wait_for_delivery(request, delivery_lease)
            await stream.write_eof()
            return stream

        if kind == "router":
            with memory_exposure_guard(
                expected_position=outcome.memory_exposure_position,
                required=(
                    outcome.memory_exposure_position is not None
                ),
                index_dir=Path(MEMORY_ROOT) / "memory_index",
            ):
                timeout = ClientTimeout(total=timeout_sec)
                async with ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url,
                        json=upstream_payload,
                        allow_redirects=False,
                    ) as upstream:
                        content = _response_content(
                            await _upstream_json(upstream)
                        )
                delivered = await deliver(content)
            router_delivery_finalized = True
            return delivered
        invocation = _start_qwen_invocation(
            request,
            outcome=outcome,
            url=url,
            upstream_payload=upstream_payload,
        )
        _bind_delivery_release(invocation, delivery_lease)
        content = await _wait_for_qwen_result(
            request,
            invocation,
            inference_timeout_sec=timeout_sec,
        )
        return await deliver(content)
    except asyncio.CancelledError:
        raise
    except _RequestError as exc:
        if stream is None or not stream.prepared:
            return _failure(exc.code, status=exc.status)
    except MemoryDeletionJournalBusyError:
        if stream is None or not stream.prepared:
            return _failure(
                "memory_deletion_journal_busy",
                status=503,
            )
    except MemoryDeletionJournalIntegrityError as exc:
        if stream is None or not stream.prepared:
            return _failure(
                memory_deletion_journal_error_code(exc),
                status=503,
            )
    except Exception:
        if stream is None or not stream.prepared:
            return _failure("mindcraft_llm_broker_failed", status=503)
    finally:
        if invocation is not None and not invocation.delivery_done.done():
            invocation.delivery_done.set_result(None)
        if delivery_lease is not None:
            if not delivery_lease.acknowledged.done():
                delivery_lease.acknowledged.cancel()
            if invocation is None and not delivery_lease.released.done():
                if router_delivery_finalized:
                    delivery_lease.released.set_result(None)
                else:
                    delivery_lease.released.set_exception(
                        RuntimeError("mindcraft_llm_delivery_guard_failed")
                    )
        if lease_id:
            leases.pop(lease_id, None)
    if stream is None:
        return _failure("mindcraft_llm_broker_failed", status=503)
    with contextlib.suppress(Exception):
        await stream.write_eof()
    return stream


async def mindcraft_llm_ack_handler(
    request: web.Request,
) -> web.StreamResponse:
    authorized, error, status = _authorized(request)
    if not authorized:
        return _failure(error, status=status)
    try:
        payload = await _read_json(
            request,
            maximum_bytes=4096,
            error_code="mindcraft_llm_delivery_ack_invalid",
        )
    except _RequestError as exc:
        return _failure(exc.code, status=exc.status)
    if (
        not isinstance(payload, Mapping)
        or set(payload)
        != {
            "schema",
            "requestId",
            "leaseId",
            "outcome",
            "contentFree",
        }
        or payload.get("schema") != MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA
        or _REQUEST_ID_PATTERN.fullmatch(
            str(payload.get("requestId") or "")
        )
        is None
        or _LEASE_ID_PATTERN.fullmatch(str(payload.get("leaseId") or ""))
        is None
        or payload.get("outcome") not in {"delivered", "discarded"}
        or payload.get("contentFree") is not True
    ):
        return _failure("mindcraft_llm_delivery_ack_invalid", status=400)
    delivery_lease = request.app[_LEASES].get(str(payload["leaseId"]))
    if (
        delivery_lease is None
        or delivery_lease.request_id != payload.get("requestId")
        or delivery_lease.acknowledged.done()
    ):
        return _failure("mindcraft_llm_delivery_lease_expired", status=409)
    delivery_lease.acknowledged.set_result(str(payload["outcome"]))
    try:
        await asyncio.shield(delivery_lease.released)
    except asyncio.CancelledError:
        raise
    except Exception:
        return _failure("mindcraft_llm_delivery_guard_failed", status=503)
    return _no_store_json({"ok": True, "contentFree": True})


async def qwen_admission_health_handler(
    request: web.Request,
) -> web.StreamResponse:
    owner = request.app[_QWEN_ADMISSION]
    if not owner.available:
        return _failure("qwen_admission_unavailable", status=503)

    def ready_epoch() -> str:
        epoch = _load_qwen_epoch(request.app[_QWEN_EPOCH])
        marker_path = request.app[_QWEN_INFLIGHT_MARKER]
        if marker_path is not None and (
            marker_path.exists() or marker_path.is_symlink()
        ) and _load_qwen_marker(marker_path) != epoch:
            raise RuntimeError("qwen_admission_recovery_required")
        return epoch

    try:
        epoch = ready_epoch()
    except (OSError, UnicodeError, RuntimeError):
        return _failure("qwen_admission_unavailable", status=503)
    parsed = urlsplit(MINDCRAFT_LOCAL_LLM_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _failure("qwen_admission_unavailable", status=503)
    health_url = parsed._replace(
        path="/health",
        query="",
        fragment="",
    ).geturl()
    try:
        timeout = ClientTimeout(total=2.0)
        async with ClientSession(timeout=timeout) as session:
            async with session.get(
                health_url,
                allow_redirects=False,
            ) as response:
                encoded = await response.content.read(4097)
                if response.status != 200 or len(encoded) > 4096:
                    raise RuntimeError("qwen_health_unavailable")
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
        )
        if (
            not isinstance(payload, Mapping)
            or payload.get("status") != "ok"
            or not owner.available
            or ready_epoch() != epoch
        ):
            raise RuntimeError("qwen_health_unavailable")
    except (
        ClientError,
        TimeoutError,
        UnicodeError,
        ValueError,
        TypeError,
        RuntimeError,
        RecursionError,
    ):
        return _failure("qwen_admission_unavailable", status=503)
    return _no_store_json(
        {
            "ok": True,
            "ready": True,
            "contentFree": True,
        }
    )


def install_mindcraft_llm_broker(app: web.Application) -> None:
    app.cleanup_ctx.append(mindcraft_llm_broker_context)
    app.on_shutdown.append(_shutdown_qwen_admission)
    app.router.add_post("/internal/mindcraft-llm", mindcraft_llm_handler)
    app.router.add_post(
        "/internal/mindcraft-llm/ack",
        mindcraft_llm_ack_handler,
    )
    app.router.add_get(
        "/internal/mindcraft-llm/health",
        qwen_admission_health_handler,
    )


__all__ = [
    "MINDCRAFT_LLM_CLIENT_ACK_TIMEOUT_SEC",
    "MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA",
    "MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA",
    "MINDCRAFT_LLM_CLIENT_GRACE_SEC",
    "MINDCRAFT_LLM_REQUEST_SCHEMA",
    "MINDCRAFT_LLM_RESULT_SCHEMA",
    "MINDCRAFT_LLM_TOKEN_FILE_ENV",
    "MINDCRAFT_LLM_TOKEN_HEADER",
    "install_mindcraft_llm_broker",
    "mindcraft_llm_ack_handler",
    "mindcraft_llm_broker_context",
    "mindcraft_llm_handler",
    "qwen_admission_health_handler",
    "request_mindcraft_llm_from_broker",
]
