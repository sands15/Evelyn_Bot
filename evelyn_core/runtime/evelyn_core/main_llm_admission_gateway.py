from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import web

from .main_inference_contract import (
    DEFAULT_MAIN_FOREGROUND_RESERVATION_TTL_MS,
    MAIN_ADMISSION_KIND_HEADER,
    MAIN_ADMISSION_QUEUE_MS_HEADER,
    MAIN_ADMISSION_REQUEST_ID_HEADER,
    MAIN_ADMISSION_RECEIPT_HEADER,
    MAIN_ADMISSION_RECEIPT_VALUE,
    MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER,
    MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER,
    MAIN_FOREGROUND_BACKEND_EPOCH_HEADER,
    MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER,
    MAIN_FOREGROUND_RESERVATION_CANCEL_PATH,
    MAIN_FOREGROUND_RESERVATION_ID_HEADER,
    MAIN_FOREGROUND_RESERVATION_PATH,
    MAIN_FOREGROUND_RESERVATION_RESULT_HEADER,
    MAIN_FOREGROUND_RESERVATION_SCHEMA,
    MainForegroundReservationBinding,
    MainForegroundReservationRejected,
    MainInferenceLane,
    MainRequestKind,
    current_main_llm_backend_epoch,
    main_backend_epoch_from_wire,
    main_capture_generation_from_wire,
    main_foreground_reservation_binding,
    main_foreground_reservation_id_from_wire,
    main_foreground_reservation_ttl_ms,
    main_request_kind_from_header,
)
from .text import clean_text


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9819
DEFAULT_MAX_BODY_BYTES = 4 * 1024 * 1024
DEFAULT_UPSTREAM_URL = "http://main_llm:9820/v1/chat/completions"
DEFAULT_UPSTREAM_HEADERS_TIMEOUT_SEC = 30.0
DEFAULT_UPSTREAM_STREAM_IDLE_TIMEOUT_SEC = 30.0
DEFAULT_UPSTREAM_TOTAL_TIMEOUT_SEC = 300.0
DEFAULT_CLIENT_DISCONNECT_POLL_SEC = 0.05

_SESSION_KEY = web.AppKey("main_llm_gateway_session", aiohttp.ClientSession)
_UPSTREAM_CHAT_KEY = web.AppKey("main_llm_gateway_upstream_chat", str)
_UPSTREAM_HEALTH_KEY = web.AppKey("main_llm_gateway_upstream_health", str)
_UPSTREAM_MODELS_KEY = web.AppKey("main_llm_gateway_upstream_models", str)
_MAX_BODY_KEY = web.AppKey("main_llm_gateway_max_body", int)
_LANE_KEY = web.AppKey("main_llm_gateway_lane", MainInferenceLane)
_RESERVATION_TTL_KEY = web.AppKey("main_llm_gateway_reservation_ttl_ms", int)
_BACKEND_EPOCH_PROVIDER_KEY = web.AppKey(
    "main_llm_gateway_backend_epoch_provider",
    Callable,
)
_UPSTREAM_HEADERS_TIMEOUT_KEY = web.AppKey(
    "main_llm_gateway_upstream_headers_timeout_sec",
    float,
)
_UPSTREAM_STREAM_IDLE_TIMEOUT_KEY = web.AppKey(
    "main_llm_gateway_upstream_stream_idle_timeout_sec",
    float,
)
_UPSTREAM_TOTAL_TIMEOUT_KEY = web.AppKey(
    "main_llm_gateway_upstream_total_timeout_sec",
    float,
)
_CLIENT_DISCONNECT_POLL_KEY = web.AppKey(
    "main_llm_gateway_client_disconnect_poll_sec",
    float,
)

# This process is the only production owner; another process lock would only
# duplicate serialization and cannot improve cross-client priority ordering.
MAIN_LLM_GATEWAY_LANE = MainInferenceLane(use_process_lock=False)


class _ClientDisconnected(RuntimeError):
    pass


class _ForegroundPreempted(RuntimeError):
    pass


def _bounded_timeout(
    value: object,
    *,
    minimum: float,
    maximum: float,
    error: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(error)
    if isinstance(value, str) and (not value or value != value.strip()):
        raise ValueError(error)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(error) from None
    if not minimum <= parsed <= maximum:
        raise ValueError(error)
    return parsed


async def _await_with_client_fence(
    request: web.Request,
    operation: Awaitable[Any],
    *,
    timeout_sec: float,
    preempted: asyncio.Event | None = None,
) -> Any:
    operation_task = asyncio.ensure_future(operation)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    poll_sec = request.app[_CLIENT_DISCONNECT_POLL_KEY]
    try:
        while True:
            if preempted is not None and preempted.is_set():
                raise _ForegroundPreempted()
            if operation_task.done():
                return await operation_task
            transport = request.transport
            if transport is None or transport.is_closing():
                raise _ClientDisconnected()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            try:
                return await asyncio.wait_for(
                    asyncio.shield(operation_task),
                    timeout=min(poll_sec, remaining),
                )
            except asyncio.TimeoutError:
                if operation_task.done():
                    return await operation_task
    finally:
        if not operation_task.done():
            operation_task.cancel()
        await asyncio.gather(operation_task, return_exceptions=True)


def _bounded_operation_timeout(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise asyncio.TimeoutError()
    return min(remaining, maximum)


def _upstream_sibling(chat_url: str, path: str) -> str:
    parsed = urlsplit(clean_text(chat_url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("main_llm_gateway_upstream_url_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _admission_response_headers(
    *,
    request_id: str,
    queue_ms: float,
    upstream_headers_ms: float,
    upstream_write_ms: float | None,
    reservation_id: str | None = None,
) -> dict[str, str]:
    headers = {
        MAIN_ADMISSION_RECEIPT_HEADER: MAIN_ADMISSION_RECEIPT_VALUE,
        MAIN_ADMISSION_REQUEST_ID_HEADER: request_id,
        MAIN_ADMISSION_QUEUE_MS_HEADER: f"{max(0.0, queue_ms):.3f}",
        MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER: (
            f"{max(0.0, upstream_headers_ms):.3f}"
        ),
    }
    if upstream_write_ms is not None:
        headers[MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER] = (
            f"{max(0.0, upstream_write_ms):.3f}"
        )
    if reservation_id is not None:
        headers[MAIN_FOREGROUND_RESERVATION_ID_HEADER] = reservation_id
        headers[MAIN_FOREGROUND_RESERVATION_RESULT_HEADER] = "redeemed"
    return headers


def _upstream_trace_config() -> aiohttp.TraceConfig:
    trace_config = aiohttp.TraceConfig()

    async def request_chunk_sent(
        _session: aiohttp.ClientSession,
        trace_context: object,
        _params: object,
    ) -> None:
        request_state = getattr(trace_context, "trace_request_ctx", None)
        if isinstance(request_state, dict):
            request_state["last_write_at"] = time.monotonic()

    trace_config.on_request_chunk_sent.append(request_chunk_sent)
    return trace_config


def _upstream_write_ms(
    request_state: dict[str, object],
    *,
    admitted_at: float,
    observed_at: float,
) -> float | None:
    write_at = request_state.get("last_write_at")
    if (
        isinstance(write_at, bool)
        or not isinstance(write_at, (int, float))
        or not admitted_at <= float(write_at) <= observed_at
    ):
        return None
    return (float(write_at) - admitted_at) * 1000.0


def _request_kind(request: web.Request):
    values = request.headers.getall(MAIN_ADMISSION_KIND_HEADER, [])
    if len(values) != 1:
        raise ValueError("main_llm_admission_kind_invalid")
    return main_request_kind_from_header(values[0])


async def _strict_control_payload(
    request: web.Request,
    *,
    keys: set[str],
) -> dict[str, object]:
    if request.content_type != "application/json":
        raise ValueError("main_llm_foreground_reservation_request_invalid")
    if request.content_length is not None and request.content_length > 1024:
        raise ValueError("main_llm_foreground_reservation_request_invalid")
    try:
        raw = await request.read()
    except web.HTTPRequestEntityTooLarge:
        raise ValueError("main_llm_foreground_reservation_request_invalid") from None
    if not raw or len(raw) > 1024:
        raise ValueError("main_llm_foreground_reservation_request_invalid")

    def pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        payload = json.loads(raw, object_pairs_hook=pairs_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("main_llm_foreground_reservation_request_invalid") from None
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("main_llm_foreground_reservation_request_invalid")
    return payload


def _current_backend_epoch(request: web.Request) -> str | None:
    try:
        return main_backend_epoch_from_wire(
            request.app[_BACKEND_EPOCH_PROVIDER_KEY]()
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _reservation_rejection(reason: str) -> web.Response:
    status = {
        "expired": 410,
        "stale_epoch": 412,
        "backend_epoch_unavailable": 503,
    }.get(reason, 409)
    return web.json_response(
        {
            "ok": False,
            "schema": MAIN_FOREGROUND_RESERVATION_SCHEMA,
            "error": "main_llm_foreground_reservation_rejected",
        },
        status=status,
        headers={MAIN_FOREGROUND_RESERVATION_RESULT_HEADER: "rejected"},
    )


def _reservation_binding_from_request(
    request: web.Request,
    *,
    kind: MainRequestKind,
) -> MainForegroundReservationBinding | None:
    names = (
        MAIN_FOREGROUND_RESERVATION_ID_HEADER,
        MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER,
        MAIN_FOREGROUND_BACKEND_EPOCH_HEADER,
    )
    values = [request.headers.getall(name, []) for name in names]
    if not any(values):
        return None
    if kind != MainRequestKind.REALTIME or any(len(items) != 1 for items in values):
        raise MainForegroundReservationRejected("unavailable")
    raw_generation = values[1][0]
    try:
        generation = int(raw_generation)
    except (TypeError, ValueError):
        raise MainForegroundReservationRejected("unavailable") from None
    if raw_generation != str(generation):
        raise MainForegroundReservationRejected("unavailable")
    try:
        return main_foreground_reservation_binding(
            reservation_id=values[0][0],
            capture_generation=generation,
            backend_epoch=values[2][0],
        )
    except ValueError:
        raise MainForegroundReservationRejected("unavailable") from None


async def reserve_foreground(request: web.Request) -> web.Response:
    try:
        payload = await _strict_control_payload(
            request,
            keys={"captureGeneration", "backendEpoch"},
        )
        generation = main_capture_generation_from_wire(
            payload["captureGeneration"]
        )
        epoch = main_backend_epoch_from_wire(payload["backendEpoch"])
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    current_epoch = _current_backend_epoch(request)
    if current_epoch is None:
        return _reservation_rejection("backend_epoch_unavailable")
    if epoch != current_epoch:
        return _reservation_rejection("stale_epoch")
    try:
        reservation = await request.app[_LANE_KEY].reserve_foreground(
            capture_generation=generation,
            backend_epoch=epoch,
            ttl_ms=request.app[_RESERVATION_TTL_KEY],
        )
    except MainForegroundReservationRejected as exc:
        return _reservation_rejection(exc.reason)
    return web.json_response(
        {
            "ok": True,
            "schema": MAIN_FOREGROUND_RESERVATION_SCHEMA,
            "reservationId": reservation.reservation_id,
            "captureGeneration": reservation.capture_generation,
            "backendEpoch": reservation.backend_epoch,
            "ttlMs": reservation.ttl_ms,
        },
        status=201,
        headers={MAIN_FOREGROUND_RESERVATION_RESULT_HEADER: "reserved"},
    )


async def cancel_foreground(request: web.Request) -> web.Response:
    try:
        payload = await _strict_control_payload(
            request,
            keys={"reservationId", "captureGeneration", "backendEpoch"},
        )
        binding = main_foreground_reservation_binding(
            reservation_id=payload["reservationId"],
            capture_generation=payload["captureGeneration"],
            backend_epoch=payload["backendEpoch"],
        )
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    current_epoch = _current_backend_epoch(request)
    if current_epoch is None:
        return _reservation_rejection("backend_epoch_unavailable")
    if binding.backend_epoch != current_epoch:
        with contextlib.suppress(MainForegroundReservationRejected):
            await request.app[_LANE_KEY].cancel_foreground(binding)
        return _reservation_rejection("stale_epoch")
    try:
        await request.app[_LANE_KEY].cancel_foreground(binding)
    except MainForegroundReservationRejected as exc:
        return _reservation_rejection(exc.reason)
    return web.json_response(
        {
            "ok": True,
            "schema": MAIN_FOREGROUND_RESERVATION_SCHEMA,
            "reservationId": binding.reservation_id,
        },
        headers={MAIN_FOREGROUND_RESERVATION_RESULT_HEADER: "cancelled"},
    )


async def _start_http_client(app: web.Application) -> None:
    app[_SESSION_KEY] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        auto_decompress=False,
        headers={"Accept-Encoding": "identity"},
        trace_configs=[_upstream_trace_config()],
    )


async def _stop_http_client(app: web.Application) -> None:
    session = app.get(_SESSION_KEY)
    if session is not None:
        await session.close()


async def health(request: web.Request) -> web.Response:
    try:
        async with request.app[_SESSION_KEY].get(
            request.app[_UPSTREAM_HEALTH_KEY],
            timeout=aiohttp.ClientTimeout(total=2),
        ) as response:
            upstream_ready = 200 <= response.status < 300
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        upstream_ready = False
    reservation_ready = _current_backend_epoch(request) is not None
    ready = upstream_ready and reservation_ready
    return web.json_response(
        {
            "ok": ready,
            "service": "main_llm_admission_gateway",
            "upstreamReady": upstream_ready,
            "admission": "priority_serial",
            "reservationReady": reservation_ready,
            "reservationTtlMs": request.app[_RESERVATION_TTL_KEY],
        },
        status=200 if ready else 503,
    )


async def models(request: web.Request) -> web.Response:
    try:
        async with request.app[_SESSION_KEY].get(
            request.app[_UPSTREAM_MODELS_KEY],
            timeout=aiohttp.ClientTimeout(total=5),
        ) as upstream:
            body = await upstream.read()
            return web.Response(
                body=body,
                status=upstream.status,
                headers={
                    "Content-Type": upstream.headers.get(
                        "Content-Type", "application/json"
                    )
                },
            )
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return web.json_response(
            {"ok": False, "error": "main_llm_upstream_unavailable"},
            status=502,
        )


async def chat_completions(request: web.Request) -> web.StreamResponse:
    reservation: MainForegroundReservationBinding | None = None
    try:
        kind = _request_kind(request)
    except ValueError as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)
    try:
        reservation = _reservation_binding_from_request(request, kind=kind)
    except MainForegroundReservationRejected as exc:
        return _reservation_rejection(exc.reason)
    if reservation is not None:
        current_epoch = _current_backend_epoch(request)
        if current_epoch is None:
            return _reservation_rejection("backend_epoch_unavailable")
        if reservation.backend_epoch != current_epoch:
            with contextlib.suppress(MainForegroundReservationRejected):
                await request.app[_LANE_KEY].cancel_foreground(reservation)
            return _reservation_rejection("stale_epoch")

    if request.content_type != "application/json":
        return web.json_response(
            {"ok": False, "error": "main_llm_request_content_type_invalid"},
            status=415,
        )
    max_body = request.app[_MAX_BODY_KEY]
    if request.content_length is not None and request.content_length > max_body:
        return web.json_response(
            {"ok": False, "error": "main_llm_request_body_too_large"},
            status=413,
        )
    try:
        body = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return web.json_response(
            {"ok": False, "error": "main_llm_request_body_too_large"},
            status=413,
        )
    if not body:
        return web.json_response(
            {"ok": False, "error": "main_llm_request_body_invalid"},
            status=400,
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return web.json_response(
            {"ok": False, "error": "main_llm_request_body_invalid"},
            status=400,
        )

    try:
        async with request.app[_LANE_KEY].admit(
            kind,
            reservation=reservation,
        ) as lease:
            if reservation is not None and (
                _current_backend_epoch(request) != reservation.backend_epoch
            ):
                return _reservation_rejection("stale_epoch")
            upstream_timing: dict[str, object] = {}
            upstream_deadline = (
                time.monotonic()
                + request.app[_UPSTREAM_TOTAL_TIMEOUT_KEY]
            )
            try:
                upstream = await _await_with_client_fence(
                    request,
                    request.app[_SESSION_KEY].post(
                        request.app[_UPSTREAM_CHAT_KEY],
                        data=body,
                        headers={
                            "Content-Type": "application/json",
                            "Accept": request.headers.get("Accept", "*/*"),
                        },
                        trace_request_ctx=upstream_timing,
                    ),
                    timeout_sec=_bounded_operation_timeout(
                        upstream_deadline,
                        request.app[_UPSTREAM_HEADERS_TIMEOUT_KEY],
                    ),
                    preempted=lease._preempted,
                )
            except _ClientDisconnected:
                return web.Response(status=499)
            except _ForegroundPreempted:
                failed_at = time.monotonic()
                return web.json_response(
                    {"ok": False, "error": "main_llm_background_preempted"},
                    status=409,
                    headers=_admission_response_headers(
                        request_id=lease.request_id,
                        queue_ms=lease.queue_ms,
                        upstream_headers_ms=(
                            failed_at - lease.admitted_at
                        )
                        * 1000.0,
                        upstream_write_ms=_upstream_write_ms(
                            upstream_timing,
                            admitted_at=lease.admitted_at,
                            observed_at=failed_at,
                        ),
                    ),
                )
            except asyncio.TimeoutError:
                failed_at = time.monotonic()
                return web.json_response(
                    {"ok": False, "error": "main_llm_upstream_timeout"},
                    status=504,
                    headers=_admission_response_headers(
                        request_id=lease.request_id,
                        queue_ms=lease.queue_ms,
                        upstream_headers_ms=(
                            failed_at - lease.admitted_at
                        )
                        * 1000.0,
                        upstream_write_ms=_upstream_write_ms(
                            upstream_timing,
                            admitted_at=lease.admitted_at,
                            observed_at=failed_at,
                        ),
                        reservation_id=(
                            reservation.reservation_id
                            if reservation is not None
                            else None
                        ),
                    ),
                )
            except (aiohttp.ClientError, OSError):
                failed_at = time.monotonic()
                return web.json_response(
                    {"ok": False, "error": "main_llm_upstream_unavailable"},
                    status=502,
                    headers=_admission_response_headers(
                        request_id=lease.request_id,
                        queue_ms=lease.queue_ms,
                        upstream_headers_ms=(
                            failed_at - lease.admitted_at
                        )
                        * 1000.0,
                        upstream_write_ms=_upstream_write_ms(
                            upstream_timing,
                            admitted_at=lease.admitted_at,
                            observed_at=failed_at,
                        ),
                        reservation_id=(
                            reservation.reservation_id
                            if reservation is not None
                            else None
                        ),
                    ),
                )

            upstream_headers_at = time.monotonic()
            upstream_write_ms = _upstream_write_ms(
                upstream_timing,
                admitted_at=lease.admitted_at,
                observed_at=upstream_headers_at,
            )
            if upstream_write_ms is None:
                upstream.close()
                return web.json_response(
                    {
                        "ok": False,
                        "error": "main_llm_upstream_write_unobserved",
                    },
                    status=502,
                    headers=_admission_response_headers(
                        request_id=lease.request_id,
                        queue_ms=lease.queue_ms,
                        upstream_headers_ms=(
                            upstream_headers_at - lease.admitted_at
                        )
                        * 1000.0,
                        upstream_write_ms=None,
                        reservation_id=(
                            reservation.reservation_id
                            if reservation is not None
                            else None
                        ),
                    ),
                )
            admission_headers = _admission_response_headers(
                request_id=lease.request_id,
                queue_ms=lease.queue_ms,
                upstream_headers_ms=(
                    upstream_headers_at - lease.admitted_at
                )
                * 1000.0,
                upstream_write_ms=upstream_write_ms,
                reservation_id=(
                    reservation.reservation_id
                    if reservation is not None
                    else None
                ),
            )

            downstream_headers = dict(admission_headers)
            for name in ("Content-Type", "Cache-Control", "Content-Encoding"):
                value = upstream.headers.get(name)
                if value:
                    downstream_headers[name] = value
            downstream = web.StreamResponse(
                status=upstream.status,
                reason=upstream.reason,
                headers=downstream_headers,
            )
            try:
                await _await_with_client_fence(
                    request,
                    downstream.prepare(request),
                    timeout_sec=_bounded_operation_timeout(
                        upstream_deadline,
                        request.app[_UPSTREAM_STREAM_IDLE_TIMEOUT_KEY],
                    ),
                    preempted=lease._preempted,
                )
                while True:
                    read_timeout = _bounded_operation_timeout(
                        upstream_deadline,
                        request.app[_UPSTREAM_STREAM_IDLE_TIMEOUT_KEY],
                    )
                    chunk = await _await_with_client_fence(
                        request,
                        upstream.content.readany(),
                        timeout_sec=read_timeout,
                        preempted=lease._preempted,
                    )
                    if not chunk:
                        break
                    if chunk:
                        write_timeout = _bounded_operation_timeout(
                            upstream_deadline,
                            request.app[_UPSTREAM_STREAM_IDLE_TIMEOUT_KEY],
                        )
                        await _await_with_client_fence(
                            request,
                            downstream.write(chunk),
                            timeout_sec=write_timeout,
                            preempted=lease._preempted,
                        )
                        if upstream.content.at_eof():
                            break
                with contextlib.suppress(ConnectionResetError, RuntimeError):
                    await downstream.write_eof()
                return downstream
            except (
                _ClientDisconnected,
                _ForegroundPreempted,
                asyncio.TimeoutError,
                ConnectionResetError,
                BrokenPipeError,
            ):
                transport = request.transport
                if transport is not None and not transport.is_closing():
                    transport.close()
                return downstream
            finally:
                upstream.close()
    except MainForegroundReservationRejected as exc:
        return _reservation_rejection(exc.reason)
    except TimeoutError:
        if reservation is not None:
            return _reservation_rejection("unavailable")
        return web.json_response(
            {"ok": False, "error": "main_llm_admission_timeout"},
            status=503,
        )


def build_app(
    *,
    upstream_url: str | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    lane: MainInferenceLane = MAIN_LLM_GATEWAY_LANE,
    reservation_ttl_ms: int | None = None,
    backend_epoch_provider: Callable[[], str | None] | None = None,
    upstream_headers_timeout_sec: float | None = None,
    upstream_stream_idle_timeout_sec: float | None = None,
    upstream_total_timeout_sec: float | None = None,
    client_disconnect_poll_sec: float = DEFAULT_CLIENT_DISCONNECT_POLL_SEC,
) -> web.Application:
    chat_url = clean_text(
        upstream_url
        or os.getenv("MAIN_LLM_ADMISSION_UPSTREAM_URL", DEFAULT_UPSTREAM_URL)
    )
    max_body = int(max_body_bytes)
    if max_body < 1:
        raise ValueError("main_llm_gateway_max_body_invalid")
    if reservation_ttl_ms is None:
        configured_ttl = clean_text(
            os.getenv(
                "MAIN_LLM_FOREGROUND_RESERVATION_TTL_MS",
                str(DEFAULT_MAIN_FOREGROUND_RESERVATION_TTL_MS),
            )
        )
        try:
            reservation_ttl_ms = int(configured_ttl)
        except ValueError:
            raise ValueError(
                "main_llm_foreground_reservation_ttl_invalid"
            ) from None
    ttl_ms = main_foreground_reservation_ttl_ms(reservation_ttl_ms)
    headers_timeout = _bounded_timeout(
        upstream_headers_timeout_sec
        if upstream_headers_timeout_sec is not None
        else os.getenv(
            "MAIN_LLM_GATEWAY_UPSTREAM_HEADERS_TIMEOUT_SEC",
            str(DEFAULT_UPSTREAM_HEADERS_TIMEOUT_SEC),
        ),
        minimum=0.05,
        maximum=300.0,
        error="main_llm_gateway_upstream_headers_timeout_invalid",
    )
    stream_idle_timeout = _bounded_timeout(
        upstream_stream_idle_timeout_sec
        if upstream_stream_idle_timeout_sec is not None
        else os.getenv(
            "MAIN_LLM_GATEWAY_STREAM_IDLE_TIMEOUT_SEC",
            str(DEFAULT_UPSTREAM_STREAM_IDLE_TIMEOUT_SEC),
        ),
        minimum=0.05,
        maximum=300.0,
        error="main_llm_gateway_stream_idle_timeout_invalid",
    )
    total_timeout = _bounded_timeout(
        upstream_total_timeout_sec
        if upstream_total_timeout_sec is not None
        else os.getenv(
            "MAIN_LLM_GATEWAY_UPSTREAM_TOTAL_TIMEOUT_SEC",
            str(DEFAULT_UPSTREAM_TOTAL_TIMEOUT_SEC),
        ),
        minimum=max(headers_timeout, stream_idle_timeout),
        maximum=1800.0,
        error="main_llm_gateway_upstream_total_timeout_invalid",
    )
    disconnect_poll = _bounded_timeout(
        client_disconnect_poll_sec,
        minimum=0.01,
        maximum=1.0,
        error="main_llm_gateway_client_disconnect_poll_invalid",
    )
    app = web.Application(client_max_size=max_body)
    app[_UPSTREAM_CHAT_KEY] = _upstream_sibling(chat_url, "/v1/chat/completions")
    app[_UPSTREAM_HEALTH_KEY] = _upstream_sibling(chat_url, "/health")
    app[_UPSTREAM_MODELS_KEY] = _upstream_sibling(chat_url, "/v1/models")
    app[_MAX_BODY_KEY] = max_body
    app[_LANE_KEY] = lane
    app[_RESERVATION_TTL_KEY] = ttl_ms
    app[_BACKEND_EPOCH_PROVIDER_KEY] = (
        backend_epoch_provider or current_main_llm_backend_epoch
    )
    app[_UPSTREAM_HEADERS_TIMEOUT_KEY] = headers_timeout
    app[_UPSTREAM_STREAM_IDLE_TIMEOUT_KEY] = stream_idle_timeout
    app[_UPSTREAM_TOTAL_TIMEOUT_KEY] = total_timeout
    app[_CLIENT_DISCONNECT_POLL_KEY] = disconnect_poll
    app.on_startup.append(_start_http_client)
    app.on_cleanup.append(_stop_http_client)
    app.router.add_get("/health", health)
    app.router.add_get("/v1/models", models)
    app.router.add_post(MAIN_FOREGROUND_RESERVATION_PATH, reserve_foreground)
    app.router.add_post(
        MAIN_FOREGROUND_RESERVATION_CANCEL_PATH,
        cancel_foreground,
    )
    app.router.add_post("/v1/chat/completions", chat_completions)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("MAIN_LLM_GATEWAY_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MAIN_LLM_GATEWAY_PORT", str(DEFAULT_PORT))),
    )
    args = parser.parse_args()
    web.run_app(
        build_app(),
        host=args.host,
        port=args.port,
        print=None,
        handler_cancellation=True,
    )


if __name__ == "__main__":
    main()
