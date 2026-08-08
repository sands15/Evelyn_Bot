from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from aiohttp import ClientSession, ClientTimeout, web

from .config import MEMORY_ROOT
from .conversation_memory_exposure import (
    filter_conversation_history_for_memory_exposure,
)
from .conversation_memory_receipt import sanitize_memory_receipt_ref
from .memory_deletion_journal import (
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_error_code,
)
from .memory_exposure import memory_exposure_request


MINDCRAFT_LLM_REQUEST_SCHEMA = "mindcraft.llm-request.v1"
MINDCRAFT_LLM_RESULT_SCHEMA = "mindcraft.llm-result.v1"
MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA = (
    "mindcraft.llm-delivery-lease.v1"
)
MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA = "mindcraft.llm-delivery-ack.v1"
MINDCRAFT_LLM_TOKEN_HEADER = "X-Evelyn-Mindcraft-LLM-Token"
MINDCRAFT_LLM_TOKEN_FILE_ENV = "MINDCRAFT_LLM_BROKER_TOKEN_FILE"

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

MINDCRAFT_LOCAL_LLM_URL = os.getenv(
    "MINDCRAFT_LOCAL_LLM_URL",
    "http://minecraft_llm:9823/v1/chat/completions",
)
MINDCRAFT_LOCAL_MODEL = os.getenv(
    "MINDCRAFT_LOCAL_MODEL",
    "Qwen3-14B-Q4_K_M.gguf",
)
MINDCRAFT_LOCAL_TIMEOUT_SEC = 90.0
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
        "subgoal",
    }
)


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


async def _upstream_json(response: Any) -> Mapping[str, Any]:
    if response.status != 200:
        raise RuntimeError("mindcraft_llm_upstream_failed")
    encoded = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        if len(encoded) + len(chunk) > MINDCRAFT_LLM_MAX_RESPONSE_BYTES:
            raise RuntimeError("mindcraft_llm_upstream_invalid")
        encoded.extend(chunk)
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
    app[_TOKEN] = (
        _load_or_create_token(token_path) if token_path is not None else ""
    )
    app[_LEASES] = {}
    app[_SEEN_REQUESTS] = {}
    try:
        yield
    finally:
        leases = app.get(_LEASES, {})
        for lease in tuple(leases.values()):
            if not lease.acknowledged.done():
                lease.acknowledged.set_result("discarded")
        await asyncio.sleep(0)
        leases.clear()
        app[_SEEN_REQUESTS].clear()


async def mindcraft_llm_handler(
    request: web.Request,
) -> web.StreamResponse:
    authorized, error, status = _authorized(request)
    if not authorized:
        return _failure(error, status=status)
    stream: web.StreamResponse | None = None
    lease_id = ""
    delivery_lease: _DeliveryLease | None = None
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
        leases[lease_id] = delivery_lease
        url, timeout_sec, upstream_payload = _upstream_policy(
            kind,
            upstream_messages,
        )
        timeout = ClientTimeout(total=timeout_sec)
        async with ClientSession(timeout=timeout) as session:
            async with memory_exposure_request(
                session.post,
                url,
                json=upstream_payload,
                allow_redirects=False,
                expected_position=outcome.memory_exposure_position,
                memory_boundary_required=(
                    outcome.memory_exposure_position is not None
                ),
                memory_index_dir=Path(MEMORY_ROOT) / "memory_index",
            ) as upstream:
                content = _response_content(await _upstream_json(upstream))
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
        if delivery_lease is not None:
            if not delivery_lease.acknowledged.done():
                delivery_lease.acknowledged.cancel()
            if not delivery_lease.released.done():
                delivery_lease.released.set_result(None)
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
    await asyncio.shield(delivery_lease.released)
    return _no_store_json({"ok": True, "contentFree": True})


def install_mindcraft_llm_broker(app: web.Application) -> None:
    app.cleanup_ctx.append(mindcraft_llm_broker_context)
    app.router.add_post("/internal/mindcraft-llm", mindcraft_llm_handler)
    app.router.add_post(
        "/internal/mindcraft-llm/ack",
        mindcraft_llm_ack_handler,
    )


__all__ = [
    "MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA",
    "MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA",
    "MINDCRAFT_LLM_REQUEST_SCHEMA",
    "MINDCRAFT_LLM_RESULT_SCHEMA",
    "MINDCRAFT_LLM_TOKEN_FILE_ENV",
    "MINDCRAFT_LLM_TOKEN_HEADER",
    "install_mindcraft_llm_broker",
    "mindcraft_llm_ack_handler",
    "mindcraft_llm_broker_context",
    "mindcraft_llm_handler",
]
