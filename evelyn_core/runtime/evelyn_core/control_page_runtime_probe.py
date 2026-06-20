from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import aiohttp

from .control_page_state import build_control_page_runtime_services_payload
from .runtime_status_context import probe_runtime_tcp_service, runtime_status_port_from_url
from .text import clean_text


TcpProbe = Callable[[str, str, int], Awaitable[tuple[str, bool]]]
HttpJsonGet = Callable[[str, float], Awaitable[tuple[int, Any]]]
VoyagerAliveProbe = Callable[[], Awaitable[bool]]


async def http_get_json(url: str, timeout_sec: float) -> tuple[int, Any]:
    timeout = aiohttp.ClientTimeout(total=max(0.001, float(timeout_sec)))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            return resp.status, await resp.json(content_type=None)


def build_bot_api_state_url(*, host: str, port: int, path: str) -> str:
    state_path = clean_text(str(path or ""))
    if not state_path.startswith("/"):
        state_path = f"/{state_path}"
    return f"http://{host}:{int(port)}{state_path}"


async def probe_control_page_runtime_services(
    *,
    service_urls: dict[str, str],
    bot_api_host: str,
    bot_api_port: int,
    bot_api_state_path: str,
    bot_api_probe_timeout_sec: float,
    action_backend: str,
    codex_gateway_port: int,
    voyager_alive_probe: VoyagerAliveProbe,
    tcp_probe: TcpProbe = probe_runtime_tcp_service,
    http_json_get: HttpJsonGet = http_get_json,
) -> dict[str, Any]:
    bot_api_url = build_bot_api_state_url(
        host=bot_api_host,
        port=bot_api_port,
        path=bot_api_state_path,
    )
    bot_api_port_open = False
    bot_api_http_ready = False
    bot_api_state = "down"
    bot_api_reason = ""
    bot_api_error = ""
    bot_api_error_kind = ""

    service_probe_targets: list[tuple[str, str, int]] = []
    for label, url in service_urls.items():
        target = runtime_status_port_from_url(url)
        if target is not None:
            host, port = target
            service_probe_targets.append((label, host, port))

    service_results: dict[str, bool] = {}
    if service_probe_targets:
        probed = await asyncio.gather(
            *(tcp_probe(label, host, port) for label, host, port in service_probe_targets),
            return_exceptions=True,
        )
        for item in probed:
            if isinstance(item, tuple) and len(item) == 2:
                service_results[str(item[0])] = bool(item[1])

    voyager_ready = False
    voyager_error = ""
    try:
        voyager_ready = bool(await voyager_alive_probe())
    except Exception as exc:
        voyager_error = clean_text(str(exc)) or type(exc).__name__

    try:
        _, bot_api_port_open = await tcp_probe("bot_api", bot_api_host, int(bot_api_port))
    except Exception as exc:
        bot_api_error = clean_text(str(exc)) or type(exc).__name__
        bot_api_error_kind = type(exc).__name__

    if bot_api_port_open:
        try:
            status, payload = await http_json_get(bot_api_url, float(bot_api_probe_timeout_sec))
            if status != 200:
                bot_api_state = f"bot api contract http_{status}"
                bot_api_reason = f"CP_BOT_HTTP_{status}"
            elif isinstance(payload, dict):
                bot_api_http_ready = True
                bot_api_state = clean_text(str(payload.get("status") or payload.get("state") or "up"))
                if not bot_api_state:
                    bot_api_state = "up"
            else:
                bot_api_state = "state_not_dict"
                bot_api_error = "invalid state payload"
                bot_api_error_kind = "bot_api_state_payload"
                bot_api_reason = "CP_BOT_STATE_NOT_DICT"
        except asyncio.TimeoutError:
            bot_api_state = "partial"
            bot_api_error = "bot api contract timeout"
            bot_api_error_kind = "bot_api_timeout"
            bot_api_reason = "CP_BOT_PROXY_TIMEOUT"
        except Exception as exc:
            bot_api_state = "partial"
            bot_api_error = clean_text(str(exc)) or type(exc).__name__
            bot_api_error_kind = type(exc).__name__
            bot_api_reason = "CP_BOT_PROXY_ERROR"
    else:
        bot_api_reason = "CP_UP_BOT_DOWN"

    codex_backend = clean_text(str(action_backend or "")) or "unknown"
    codex_required = codex_backend.lower() == "codex-gateway"
    codex_ready: bool | None = None
    codex_error = ""
    if codex_required:
        codex_ready = False
        codex_health_url = f"http://127.0.0.1:{int(codex_gateway_port)}/health"
        try:
            status, payload = await http_json_get(codex_health_url, 0.45)
            if isinstance(payload, dict):
                codex_backend = clean_text(str(payload.get("backend") or codex_backend)) or codex_backend
                codex_ready = status == 200 and bool(payload.get("ok", True))
                if not codex_ready:
                    codex_error = clean_text(str(payload.get("error") or payload.get("codex_login_message") or "")) or codex_error
            else:
                codex_ready = status == 200
        except Exception as exc:
            codex_error = clean_text(str(exc)) or type(exc).__name__

    return build_control_page_runtime_services_payload(
        service_results=service_results,
        voyager_ready=voyager_ready,
        voyager_error=voyager_error,
        bot_api_port_open=bot_api_port_open,
        bot_api_http_ready=bot_api_http_ready,
        bot_api_state=bot_api_state,
        bot_api_reason=bot_api_reason,
        bot_api_error=bot_api_error,
        bot_api_error_kind=bot_api_error_kind,
        codex_required=codex_required,
        codex_ready=codex_ready,
        codex_backend=codex_backend,
        codex_error=codex_error,
    )
