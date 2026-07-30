from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import ClientConnectorError, ClientSession, ClientTimeout, web

from .control_page_http import control_page_cors_middleware, control_page_session_handler
from .control_page_contracts import (
    build_control_page_panel_state_payload,
    build_fast_control_default_commands,
    local_restart_requested_reply,
    memory_panel_reply as shared_memory_panel_reply,
)
from .fast_action_runtime import detect_local_runtime_command
from .memory_provenance_correction import (
    apply_memory_provenance_correction,
    apply_memory_provenance_correction_undo,
    memory_provenance_correction_overview,
    memory_provenance_correction_source_options,
    preview_memory_provenance_correction,
    preview_memory_provenance_correction_undo,
)
from .memory_vault import (
    apply_memory_provenance_backfill,
    delete_memory_vault_user_note,
    ensure_memory_vault_layout,
    export_memory_graph,
    memory_provenance_backfill_preview,
    memory_provenance_manual_source_options,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    preview_memory_provenance_backfill_application,
    preview_memory_vault_user_note_deletion,
    update_memory_vault_user_note,
)
from .runtime_health import apply_runtime_health_overrides, collect_runtime_health
from .runtime_error_observability import collect_runtime_error_observability
from .runtime_repair import (
    append_repair_event,
    build_runtime_repair_plan,
    execute_runtime_repair_plan,
    runtime_repair_capabilities,
)
from .runtime_services import load_service_manifest, manifest_to_dict
from .storage_retention_report import read_storage_retention_report
from .voice_capture_consent import (
    SCOPE as VOICE_CAPTURE_CONSENT_SCOPE,
    attach_voice_capture_consent,
    get_voice_capture_consent_manager,
)
from .voice_validation import SUITE_ID, get_voice_validation_manager


PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"

HOST = os.getenv("CONTROL_PAGE_HOST", "127.0.0.1")
PORT = int(os.getenv("CONTROL_PAGE_PUBLIC_PORT", os.getenv("CONTROL_PAGE_PORT", "8799")))
BOT_API_HOST = os.getenv("CONTROL_PAGE_BOT_API_HOST", "127.0.0.1")
BOT_API_PORT = int(os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798"))
BOT_API_BASE = f"http://{BOT_API_HOST}:{BOT_API_PORT}"
PROXY_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_PROXY_TIMEOUT_SEC", "6.0"))
LOCAL_HELP_COMMANDS = {"/", "/help"}
LOCAL_STATUS_COMMANDS = {"/status"}
LOCAL_MEMORY_COMMANDS = {"/memory", "/obsidian"}
LOCAL_RESTART_COMMANDS = {"/restart", "restart"}
LOCAL_SHUTDOWN_COMMANDS = {"/shutdown", "/quit", "/exit"}

MODEL_PORTS = {
    "main": int(os.getenv("MAIN_LLM_PORT", "9820")),
    "router": int(os.getenv("ROUTER_LLM_PORT", "9822")),
    "sub": int(os.getenv("SUB_LLM_PORT", "9821")),
    "tts": int(os.getenv("TTS_PORT", "8880")),
    "voyager": int(os.getenv("MINECRAFT_AUTONOMY_SERVICE_PORT", "8765")),
    "codex": int(os.getenv("VOYAGER_CODEX_GATEWAY_PORT", "8787")),
    "bot": BOT_API_PORT,
}
RUNTIME_HEALTH_CACHE_TTL_SEC = float(os.getenv("CONTROL_PAGE_RUNTIME_HEALTH_CACHE_TTL_SEC", "1.5"))
runtime_health_cache: dict[str, Any] | None = None
runtime_health_cache_at = 0.0
runtime_health_cache_lock: asyncio.Lock | None = None
runtime_health_overrides: dict[str, dict[str, Any]] = {}
bot_state_last_success_at = 0.0
VOICE_CAPTURE_CONSENT_LOCK_KEY = web.AppKey(
    "voice_capture_consent_lock",
    asyncio.Lock,
)
VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC = max(
    0.25,
    float(os.getenv("VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC", "1.0")),
)


STATIC_UTF8_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
}


def static_content_type(path: Path) -> str | None:
    return STATIC_UTF8_CONTENT_TYPES.get(path.suffix.lower())


def request_is_loopback(request: web.Request) -> bool:
    remote = str(request.remote or "")
    return remote in {"127.0.0.1", "::1", "localhost"}


def prune_runtime_health_overrides() -> None:
    now = time.time()
    expired = [
        service_id
        for service_id, override in runtime_health_overrides.items()
        if float(override.get("expiresAt") or 0) <= now
    ]
    for service_id in expired:
        runtime_health_overrides.pop(service_id, None)


def with_memory_panel_command(state: dict[str, Any], action: str) -> dict[str, Any]:
    command_id = int(time.time() * 1000)
    state["controlPagePanels"] = build_control_page_panel_state_payload(
        [
            {
                "id": command_id,
                "action": action if action in {"open", "close", "toggle"} else "toggle",
                "panel": "memory",
                "at": time.time(),
            }
        ],
        revision=command_id,
    )
    return state


def memory_panel_reply(action: str) -> str:
    return shared_memory_panel_reply(action)

BOOT_PORT_STEPS = (
    ("main", "Main LLM"),
    ("router", "Router LLM"),
    ("sub", "Sub LLM"),
    ("tts", "TTS"),
    ("bot", "Bot API"),
)


async def probe_port(port: int, host: str = "127.0.0.1", timeout_sec: float = 0.18) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_sec)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True
    except Exception:
        return False


async def cached_runtime_health(*, force: bool = False) -> dict[str, Any]:
    global runtime_health_cache
    global runtime_health_cache_at
    global runtime_health_cache_lock

    now = time.time()
    if (
        not force
        and runtime_health_cache is not None
        and RUNTIME_HEALTH_CACHE_TTL_SEC > 0
        and now - runtime_health_cache_at < RUNTIME_HEALTH_CACHE_TTL_SEC
    ):
        return dict(runtime_health_cache)
    if runtime_health_cache_lock is None:
        runtime_health_cache_lock = asyncio.Lock()
    async with runtime_health_cache_lock:
        now = time.time()
        if (
            not force
            and runtime_health_cache is not None
            and RUNTIME_HEALTH_CACHE_TTL_SEC > 0
            and now - runtime_health_cache_at < RUNTIME_HEALTH_CACHE_TTL_SEC
        ):
            return dict(runtime_health_cache)
        manifest = load_service_manifest()
        health = await collect_runtime_health(manifest=manifest)
        prune_runtime_health_overrides()
        health = apply_runtime_health_overrides(health, runtime_health_overrides, manifest=manifest)
        runtime_health_cache = dict(health)
        runtime_health_cache_at = time.time()
        return dict(runtime_health_cache)


async def proxy_json(request: web.Request, method: str, path: str, *, body: Any = None) -> web.Response | None:
    query = request.query_string
    url = f"{BOT_API_BASE}{path}" + (f"?{query}" if query else "")
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            if method == "POST":
                async with session.post(url, json=body) as response:
                    text = await response.text()
                    return web.Response(status=response.status, text=text, content_type=response.content_type or "application/json")
            async with session.get(url) as response:
                text = await response.text()
                return web.Response(status=response.status, text=text, content_type=response.content_type or "application/json")
    except Exception as exc:
        remember_proxy_failure(request, proxy_failure_payload(classify_proxy_exception(exc), url=url, detail=repr(exc)))
        return None


async def proxy_raw(request: web.Request, path: str) -> web.Response | None:
    query = request.query_string
    url = f"{BOT_API_BASE}{path}" + (f"?{query}" if query else "")
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                body = await response.read()
                headers = {}
                if response.content_type:
                    headers["Content-Type"] = response.content_type
                return web.Response(status=response.status, body=body, headers=headers)
    except Exception:
        return None


async def request_local_bridge_mic_control(
    enabled: bool,
    *,
    source: str,
) -> dict[str, Any]:
    """Use the internal Bot API and return its structured mic ACK."""
    url = f"{BOT_API_BASE}/api/local-bridge/mic"
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"enabled": bool(enabled), "source": str(source or "control_page")},
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                payload.setdefault("httpStatus", response.status)
                if response.status >= 400:
                    payload["ok"] = False
                    payload.setdefault("error", f"mic_control_http_{response.status}")
                return payload
    except Exception as exc:
        return {
            "ok": False,
            "applied": False,
            "error": classify_proxy_exception(exc),
            "detail": repr(exc),
        }


def default_commands() -> list[dict[str, str]]:
    return build_fast_control_default_commands()


def format_command_help(commands: list[dict[str, str]]) -> str:
    lines = ["페이지 명령어"]
    groups = ["기본", "페이지", "음성", "Minecraft", "자율 행동", "시스템"]
    for group in groups:
        items = [item for item in commands if isinstance(item, dict) and item.get("group") == group]
        if not items:
            continue
        lines.extend(["", group])
        for item in items:
            command = item.get("command")
            summary = item.get("summary")
            if command and summary:
                lines.append(f"- {command} - {summary}")
    return "\n".join(lines)


def service_summary(services: dict[str, bool]) -> str:
    if not services.get("bot"):
        return "Control-Page is live; Bot API is down."
    if services.get("main") and services.get("router") and services.get("sub") and services.get("tts"):
        return "Control-Page and Bot API are ready."
    return "Control-Page is live; model services are still starting."


def classify_proxy_exception(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "http_timeout"
    if isinstance(exc, (ClientConnectorError, ConnectionRefusedError, OSError)):
        return "port_closed"
    return "proxy_error"


def proxy_failure_payload(kind: str, *, url: str, detail: str = "") -> dict[str, Any]:
    detail_text = str(detail or "")
    return {
        "kind": kind,
        "target": url,
        "botApiHost": BOT_API_HOST,
        "botApiPort": BOT_API_PORT,
        "detail": detail_text[:240],
        "at": time.time(),
    }


def remember_proxy_failure(request: web.Request, failure: dict[str, Any]) -> None:
    try:
        request["lastProxyFailure"] = failure
    except Exception:
        pass


def last_proxy_failure(request: web.Request) -> dict[str, Any] | None:
    try:
        failure = request.get("lastProxyFailure")
    except Exception:
        failure = None
    return dict(failure) if isinstance(failure, dict) else None


def runtime_health_cache_stale(cache_age_sec: float | None) -> bool:
    return bool(
        cache_age_sec is not None
        and RUNTIME_HEALTH_CACHE_TTL_SEC > 0
        and cache_age_sec > RUNTIME_HEALTH_CACHE_TTL_SEC
    )


def runtime_service_checked_at(service_health: dict[str, Any] | None, service_id: str) -> float | None:
    if not isinstance(service_health, dict):
        return None
    for service in service_health.get("services") or []:
        if not isinstance(service, dict) or service.get("id") != service_id:
            continue
        try:
            checked_at = float(service.get("checkedAt") or 0.0)
        except (TypeError, ValueError):
            return None
        return checked_at if checked_at > 0 else None
    return None


def control_plane_status_text(
    *,
    ports: dict[str, bool],
    proxy_failure: dict[str, Any] | None = None,
    cache_age_sec: float | None = None,
) -> str:
    cache_note = " Runtime health data is stale; refresh before trusting readiness." if runtime_health_cache_stale(cache_age_sec) else ""
    if not ports.get("bot"):
        return f"Control-Page is live on {PORT}; Bot API is not reachable on {BOT_API_PORT}.{cache_note}"
    if proxy_failure:
        kind = str(proxy_failure.get("kind") or "proxy_error")
        if kind == "http_timeout":
            return f"Control-Page is live; Bot API port {BOT_API_PORT} is open but the proxy timed out.{cache_note}"
        if kind == "json_parse_failed":
            return f"Control-Page is live; Bot API responded but returned invalid state JSON.{cache_note}"
        return f"Control-Page is live; Bot API port {BOT_API_PORT} is open but proxying failed.{cache_note}"
    return f"Control-Page and Bot API are both responding.{cache_note}"


def build_control_plane_state(
    *,
    ports: dict[str, bool],
    proxy_failure: dict[str, Any] | None = None,
    cache_age_sec: float | None = None,
    bot_checked_at: float | None = None,
    bot_state_success_at: float | None = None,
) -> dict[str, Any]:
    bot_port_open = bool(ports.get("bot"))
    cache_stale = runtime_health_cache_stale(cache_age_sec)
    return {
        "controlPage": {
            "ready": True,
            "host": HOST,
            "port": PORT,
            "role": "Control-Page",
        },
        "botApi": {
            "ready": bool(bot_port_open and not proxy_failure),
            "portOpen": bot_port_open,
            "host": BOT_API_HOST,
            "port": BOT_API_PORT,
            "role": "Bot API",
            "state": "ready" if bot_port_open and not proxy_failure else ("proxy_failed" if bot_port_open else "down"),
            "lastCheckedAt": bot_checked_at,
            "lastSuccessfulStateAt": bot_state_success_at,
        },
        "lastProxyFailure": dict(proxy_failure or {}),
        "healthCache": {
            "ageSec": round(float(cache_age_sec or 0.0), 1),
            "stale": cache_stale,
            "ttlSec": RUNTIME_HEALTH_CACHE_TTL_SEC,
        },
        "statusText": control_plane_status_text(ports=ports, proxy_failure=proxy_failure, cache_age_sec=cache_age_sec),
    }


def build_boot_progress_from_ports(ports: dict[str, bool]) -> dict[str, Any]:
    steps = [
        {
            "key": key,
            "label": label,
            "done": bool(ports.get(key)),
            "status": "done" if ports.get(key) else "pending",
        }
        for key, label in BOOT_PORT_STEPS
    ]
    done_count = sum(1 for step in steps if step["done"])
    percent = round((done_count / max(1, len(steps))) * 100)
    current = next((step for step in steps if not step["done"]), steps[-1])
    phase = "핵심 서비스 준비 완료" if percent >= 100 else f"{current['label']} 대기 중"
    return {
        "percent": percent,
        "phase": phase,
        "ready": percent >= 100,
        "componentsReady": percent >= 100,
        "done": done_count,
        "total": len(steps),
        "source": "control_page_proxy",
        "steps": steps,
    }


async def current_boot_progress() -> dict[str, Any]:
    service_health = await cached_runtime_health()
    legacy = dict(service_health.get("legacyServices") or {})
    ports = {
        "main": bool(legacy.get("mainReady")),
        "router": bool(legacy.get("routerReady")),
        "sub": bool(legacy.get("subReady")),
        "tts": bool(legacy.get("ttsReady")),
        "voyager": bool(legacy.get("voyagerReady")),
        "codex": bool(legacy.get("codexReady")),
        "bot": bool(legacy.get("botReady")),
    }
    return {
        "ports": ports,
        "bootProgress": build_boot_progress_from_ports(ports),
        "serviceHealth": service_health,
        "healthCacheAgeSec": max(0.0, time.time() - runtime_health_cache_at) if runtime_health_cache_at > 0 else None,
        "botApiCheckedAt": runtime_service_checked_at(service_health, "bot_api"),
        "botStateLastSuccessAt": bot_state_last_success_at if bot_state_last_success_at > 0 else None,
    }


async def degraded_state(*, proxy_failure: dict[str, Any] | None = None) -> dict[str, Any]:
    progress_state = await current_boot_progress()
    ports = dict(progress_state["ports"])
    inferred_bot_port_open = bool(proxy_failure and str(proxy_failure.get("kind") or "") != "port_closed")
    if inferred_bot_port_open:
        ports["bot"] = True
    boot_progress = build_boot_progress_from_ports(ports) if inferred_bot_port_open else progress_state["bootProgress"]
    service_health = progress_state.get("serviceHealth")
    legacy_services = dict(service_health.get("legacyServices") or {}) if isinstance(service_health, dict) else {}
    control_plane = build_control_plane_state(
        ports=ports,
        proxy_failure=proxy_failure,
        cache_age_sec=progress_state.get("healthCacheAgeSec"),
        bot_checked_at=progress_state.get("botApiCheckedAt"),
        bot_state_success_at=progress_state.get("botStateLastSuccessAt"),
    )
    return {
        "ok": False,
        "generatedAt": time.time(),
        "localUrl": f"http://{HOST}:{PORT}/",
        "bootProgress": boot_progress,
        "ui": {
            "mode": "default",
            "submode": "offline" if not ports.get("bot") else "idle",
            "reason": "bot_api_unavailable" if not ports.get("bot") else "bot_api_proxy_pending",
        },
        "commands": default_commands(),
        "allCommands": default_commands(),
        "chat": {
            "messages": [
                {
                    "role": "assistant",
                    "author": "Control",
                    "text": control_plane["statusText"],
                    "at": time.time(),
                }
            ]
        },
        "voice": {"channelName": "없음", "listening": False, "speaking": False, "ttsTargetName": "없음"},
        "runtime": {
            "mainModel": os.getenv("MODEL_NAME", "unknown"),
            "routerModel": os.getenv("ROUTER_MODEL_NAME", "unknown"),
            "summaryModel": os.getenv("SUMMARY_MODEL_NAME", "unknown"),
            "sttModel": os.getenv("STT_MODEL_NAME", "unknown"),
            "inflightLlmRequests": 0,
            "ttsBacklog": 0,
            "services": {
                "botReady": bool(ports.get("bot")),
                "mainReady": bool(ports.get("main")),
                "routerReady": bool(ports.get("router")),
                "subReady": bool(ports.get("sub")),
                "ttsReady": bool(ports.get("tts")),
                "voyagerReady": bool(ports.get("voyager")),
                "codexReady": bool(ports.get("codex")),
                "codexRequired": bool(legacy_services.get("codexRequired", True)),
                "codexBackend": str(legacy_services.get("codexBackend") or "codex-gateway"),
                "summary": str(legacy_services.get("summary") or service_summary(ports)),
            },
            "controlPlane": control_plane,
            "bootProgress": boot_progress,
            "manifestVersion": service_health.get("manifestVersion") if isinstance(service_health, dict) else None,
            "capabilities": dict(service_health.get("capabilities") or {}) if isinstance(service_health, dict) else {},
            "observability": dict(service_health.get("observability") or {}) if isinstance(service_health, dict) else {},
            "serviceHealth": service_health,
        },
        "minecraft": {
            "running": bool(ports.get("voyager")),
            "connected": False,
            "sessionActive": False,
            "goal": "없음",
            "stage": "없음",
            "task": "없음",
            "taskStage": "없음",
            "progress": "Bot API waiting",
            "position": "미확인",
            "inventorySummary": "인벤토리 정보 없음",
            "inventoryTop": [],
            "inventorySlots": [],
            "recentActivity": [],
            "snapshotStale": True,
            "snapshotExpired": False,
            "idleSummary": control_plane["statusText"],
        },
        "statusText": control_plane["statusText"],
    }


def json_response(data: Any, *, status: int = 200) -> web.Response:
    return web.Response(status=status, text=json.dumps(data, ensure_ascii=False), content_type="application/json")


def schedule_local_stack_shutdown(delay_ms: int = 1500) -> tuple[bool, str]:
    stop_script = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
    if not stop_script.exists():
        return False, f"shutdown helper not found: {stop_script}"
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(stop_script),
                "-DelayMs",
                str(max(0, int(delay_ms))),
            ],
            cwd=str(PROJECT_ROOT),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "local shutdown scheduled"
    except Exception as exc:
        return False, repr(exc)


def schedule_local_stack_restart(delay_ms: int = 500) -> tuple[bool, str]:
    stop_script = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
    start_script = PROJECT_ROOT / "evelyn_core" / "start_local.bat"
    if not stop_script.exists():
        return False, f"restart stop helper not found: {stop_script}"
    if not start_script.exists():
        return False, f"restart start helper not found: {start_script}"
    try:
        restart_script = (
            "$ErrorActionPreference = 'Continue'; "
            f"& '{stop_script}' -DelayMs {max(0, int(delay_ms))}; "
            "Start-Sleep -Seconds 2; "
            f"& '{start_script}' --background"
        )
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                restart_script,
            ],
            cwd=str(PROJECT_ROOT),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "local restart scheduled"
    except Exception as exc:
        return False, repr(exc)


async def index_handler(_: web.Request) -> web.StreamResponse:
    index_path = DOCS_DIR / "index.html"
    if not index_path.exists():
        raise web.HTTPNotFound(text="control page index not found")
    response = web.FileResponse(index_path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Content-Type"] = static_content_type(index_path) or "text/html; charset=utf-8"
    return response


async def asset_handler(request: web.Request) -> web.StreamResponse:
    requested = Path(request.match_info.get("asset_path", ""))
    asset_path = (ASSETS_DIR / requested).resolve()
    assets_root = ASSETS_DIR.resolve()
    try:
        asset_path.relative_to(assets_root)
    except ValueError as exc:
        raise web.HTTPForbidden(text="invalid asset path") from exc
    if not asset_path.exists() or not asset_path.is_file():
        raise web.HTTPNotFound(text="asset not found")
    response = web.FileResponse(asset_path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    content_type = static_content_type(asset_path)
    if content_type:
        response.headers["Content-Type"] = content_type
    return response


async def state_handler(request: web.Request) -> web.StreamResponse:
    global bot_state_last_success_at
    proxied = await proxy_json(request, "GET", "/api/control-page/state")
    if proxied is not None and proxied.status < 500:
        try:
            payload = json.loads(proxied.text or "{}")
            if isinstance(payload, dict):
                if 200 <= proxied.status < 300:
                    bot_state_last_success_at = time.time()
                progress_state = await current_boot_progress()
                ports = progress_state["ports"]
                boot_progress = progress_state["bootProgress"]
                service_health = progress_state.get("serviceHealth")
                legacy_services = dict(service_health.get("legacyServices") or {}) if isinstance(service_health, dict) else {}
                control_plane = build_control_plane_state(
                    ports=ports,
                    cache_age_sec=progress_state.get("healthCacheAgeSec"),
                    bot_checked_at=progress_state.get("botApiCheckedAt"),
                    bot_state_success_at=bot_state_last_success_at if bot_state_last_success_at > 0 else None,
                )
                runtime = dict(payload.get("runtime") or {})
                services = dict(runtime.get("services") or {})
                services.update(
                    {
                        "botReady": bool(ports.get("bot")),
                        "mainReady": bool(ports.get("main")),
                        "routerReady": bool(ports.get("router")),
                        "subReady": bool(ports.get("sub")),
                        "ttsReady": bool(ports.get("tts")),
                        "voyagerReady": bool(ports.get("voyager")),
                        "codexReady": bool(ports.get("codex")),
                        "summary": str(legacy_services.get("summary") or service_summary(ports)),
                    }
                )
                for key, value in legacy_services.items():
                    services[key] = value
                runtime["services"] = services
                runtime["controlPlane"] = control_plane
                runtime["bootProgress"] = boot_progress
                runtime["manifestVersion"] = service_health.get("manifestVersion") if isinstance(service_health, dict) else None
                runtime["capabilities"] = dict(service_health.get("capabilities") or {}) if isinstance(service_health, dict) else {}
                runtime["observability"] = dict(service_health.get("observability") or {}) if isinstance(service_health, dict) else {}
                runtime["serviceHealth"] = service_health
                payload["runtime"] = runtime
                payload["bootProgress"] = boot_progress
                payload["statusText"] = control_plane["statusText"]
                return json_response(payload, status=proxied.status)
        except Exception:
            remember_proxy_failure(
                request,
                proxy_failure_payload("json_parse_failed", url=f"{BOT_API_BASE}/api/control-page/state", detail="Bot API returned invalid state JSON."),
            )
            return json_response(await degraded_state(proxy_failure=last_proxy_failure(request)))
        return proxied
    if proxied is not None:
        remember_proxy_failure(
            request,
            proxy_failure_payload(
                "http_error",
                url=f"{BOT_API_BASE}/api/control-page/state",
                detail=f"status={proxied.status}",
            ),
        )
    return json_response(await degraded_state(proxy_failure=last_proxy_failure(request)))


async def health_handler(_: web.Request) -> web.StreamResponse:
    bot_ready = await probe_port(BOT_API_PORT, host=BOT_API_HOST)
    return json_response({"ok": True, "role": "control-page", "botProxyReady": bot_ready, "botApiPort": BOT_API_PORT})


async def runtime_health_handler(_: web.Request) -> web.StreamResponse:
    return json_response(await cached_runtime_health(force=True))


async def runtime_errors_handler(_: web.Request) -> web.StreamResponse:
    health = await cached_runtime_health(force=True)
    observability = (
        health.get("observability")
        if isinstance(health, dict)
        else None
    )
    errors = (
        observability.get("exceptions")
        if isinstance(observability, dict)
        else None
    )
    return json_response(
        {
            "ok": True,
            "errors": (
                errors
                if isinstance(errors, dict)
                else collect_runtime_error_observability()
            ),
        }
    )


async def runtime_health_override_handler(request: web.Request) -> web.StreamResponse:
    global runtime_health_cache
    global runtime_health_cache_at

    if not request_is_loopback(request):
        return json_response({"ok": False, "error": "loopback_only"}, status=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    service_id = str((payload or {}).get("serviceId") or (payload or {}).get("service_id") or "").strip()
    if not service_id:
        return json_response({"ok": False, "error": "service_id_required"}, status=400)
    manifest = load_service_manifest()
    service_ids = {service.id for service in manifest.services}
    if service_id not in service_ids:
        return json_response({"ok": False, "error": "unknown_service", "serviceId": service_id}, status=400)
    state = str((payload or {}).get("state") or "down").lower()
    if state in {"clear", "up", "none"}:
        runtime_health_overrides.pop(service_id, None)
    else:
        if state not in {"down", "partial", "unknown"}:
            return json_response({"ok": False, "error": "unsupported_override_state", "state": state}, status=400)
        ttl_sec = max(1, min(900, int((payload or {}).get("ttlSec") or (payload or {}).get("ttl_sec") or 300)))
        runtime_health_overrides[service_id] = {
            "serviceId": service_id,
            "state": state,
            "reason": str((payload or {}).get("reason") or "operator_simulated_down"),
            "message": str((payload or {}).get("message") or f"{service_id} is safely simulated as {state}."),
            "expiresAt": time.time() + ttl_sec,
        }
    runtime_health_cache = None
    runtime_health_cache_at = 0.0
    health = await cached_runtime_health(force=True)
    return json_response(
        {
            "ok": True,
            "serviceId": service_id,
            "overrides": list(runtime_health_overrides.values()),
            "serviceHealth": health,
        }
    )


async def runtime_manifest_handler(_: web.Request) -> web.StreamResponse:
    manifest = load_service_manifest()
    return json_response(manifest_to_dict(manifest))


async def runtime_repair_handler(_: web.Request) -> web.StreamResponse:
    manifest = load_service_manifest()
    health = await cached_runtime_health()
    return json_response(runtime_repair_capabilities(manifest=manifest, health=health))


async def runtime_repair_preview_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict) and payload.get("dryRun") is False:
        return json_response(
            {
                "ok": False,
                "dryRun": True,
                "dryRunOnly": True,
                "error": "repair_execution_not_enabled",
                "message": "Runtime repair execution is not enabled in this phase. Use dryRun=true.",
            },
            status=409,
        )
    service_id = str((payload or {}).get("serviceId") or (payload or {}).get("service_id") or "").strip()
    action_id = str((payload or {}).get("actionId") or (payload or {}).get("action_id") or "").strip()
    refresh_health = bool((payload or {}).get("refreshHealth", True))
    manifest = load_service_manifest()
    health = await cached_runtime_health(force=refresh_health)
    plan = await asyncio.to_thread(
        build_runtime_repair_plan,
        service_id=service_id or None,
        action_id=action_id or None,
        manifest=manifest,
        health=health,
    )
    return json_response(plan, status=200 if plan.get("ok") else 400)


async def runtime_repair_apply_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    service_id = str((payload or {}).get("serviceId") or (payload or {}).get("service_id") or "").strip()
    action_id = str((payload or {}).get("actionId") or (payload or {}).get("action_id") or "").strip()
    confirm_token = str((payload or {}).get("confirmToken") or (payload or {}).get("confirm_token") or "").strip()
    reason = str((payload or {}).get("reason") or "").strip()
    manifest = load_service_manifest()
    health = await cached_runtime_health(force=True)
    plan = await asyncio.to_thread(
        build_runtime_repair_plan,
        service_id=service_id or None,
        action_id=action_id or None,
        manifest=manifest,
        health=health,
        issue_supervisor_preview=False,
    )
    response = await asyncio.to_thread(
        execute_runtime_repair_plan,
        plan=plan,
        confirm_token=confirm_token,
        reason=reason,
    )
    try:
        log_result = append_repair_event(
            {
                "event": "apply_response",
                "serviceId": response.get("serviceId") or service_id,
                "actionId": response.get("actionId") or action_id,
                "ok": bool(response.get("ok")),
                "error": response.get("error"),
                "reason": reason,
                "remote": request.remote,
                "planOk": bool(plan.get("ok")),
                "planStatus": plan.get("planStatus"),
            }
        )
        response["repairLog"] = {"ok": True, "path": log_result.get("logPath")}
    except Exception as exc:
        response["repairLog"] = {"ok": False, "error": str(exc)}
    if response.get("ok"):
        return json_response(response, status=202)
    error = response.get("error")
    status = 409 if error in {"repair_cooldown_active", "confirm_token_required"} else 400
    return json_response(response, status=status)


def _voice_validation_uses_local(session: dict[str, Any]) -> bool:
    return "local" in {
        str(item or "").strip().lower() for item in (session.get("surfaces") or [])
    }


def _voice_capabilities_with_capture_consent(
    health: dict[str, Any],
) -> dict[str, Any]:
    capabilities = (
        dict(health.get("capabilities") or {}) if isinstance(health, dict) else {}
    )
    consent_manager = get_voice_capture_consent_manager()
    consent = consent_manager.status()
    return attach_voice_capture_consent(capabilities, consent)


async def _revoke_voice_capture_consent(
    app: web.Application,
    *,
    reason: str,
) -> dict[str, Any]:
    manager = get_voice_capture_consent_manager()
    lock = app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        try:
            pending = manager.begin_revoke(reason=reason)
        except Exception as exc:
            control = await request_local_bridge_mic_control(
                False,
                source=f"voice_capture_consent:{reason}:state_error",
            )
            bridge = dict(control.get("localBridge") or {})
            applied = bool(control.get("applied")) and not bool(
                bridge.get("micEnabled")
            )
            return {
                "ok": False,
                "error": "voice_capture_consent_state_write_failed",
                "detail": repr(exc),
                "controlApplied": applied,
                "localBridge": bridge,
                "consent": manager.status(),
            }
        if not pending.get("controlRequired"):
            return {"ok": True, "consent": manager.status(), "controlApplied": False}
        control = await request_local_bridge_mic_control(
            False,
            source=f"voice_capture_consent:{reason}",
        )
        bridge = dict(control.get("localBridge") or {})
        applied = bool(control.get("applied")) and not bool(bridge.get("micEnabled"))
        completed = manager.finish_revoke(
            applied=applied,
            error=str(control.get("error") or "mic_control_ack_timeout"),
        )
        return {
            **completed,
            "controlApplied": applied,
            "localBridge": bridge,
        }


async def _reconcile_voice_capture_consent(
    app: web.Application,
    *,
    validation_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manager = get_voice_capture_consent_manager()
    reason = manager.revocation_reason(validation_session=validation_session)
    if reason:
        return await _revoke_voice_capture_consent(app, reason=reason)
    return {"ok": True, "consent": manager.status(), "controlApplied": False}


async def _voice_capture_consent_context(app: web.Application):
    async def monitor() -> None:
        while True:
            try:
                validation = get_voice_validation_manager().snapshot()
                await _reconcile_voice_capture_consent(
                    app,
                    validation_session=validation,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A broken lease store must never leave capture on indefinitely.
                await request_local_bridge_mic_control(
                    False,
                    source="voice_capture_consent:monitor_error",
                )
            await asyncio.sleep(VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC)

    try:
        validation = get_voice_validation_manager().snapshot()
        await _reconcile_voice_capture_consent(app, validation_session=validation)
    except Exception:
        await request_local_bridge_mic_control(
            False,
            source="voice_capture_consent:startup_error",
        )
        raise
    task = asyncio.create_task(monitor(), name="voice-capture-consent-monitor")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await _revoke_voice_capture_consent(app, reason="control_page_shutdown")


async def voice_capture_consent_handler(request: web.Request) -> web.StreamResponse:
    validation = get_voice_validation_manager().snapshot()
    result = await _reconcile_voice_capture_consent(
        request.app,
        validation_session=validation,
    )
    return json_response(
        {
            "ok": bool(result.get("ok")),
            "consent": get_voice_capture_consent_manager().status(),
        },
        status=200 if result.get("ok") else 503,
    )


async def voice_capture_consent_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = get_voice_capture_consent_manager().preview(
        scope=str((payload or {}).get("scope") or VOICE_CAPTURE_CONSENT_SCOPE),
    )
    return json_response(result, status=200 if result.get("ok") else 409)


async def voice_capture_consent_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    manager = get_voice_capture_consent_manager()
    lock = request.app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        started = manager.begin_apply(
            confirm_token=str((payload or {}).get("confirmToken") or ""),
            scope=str((payload or {}).get("scope") or VOICE_CAPTURE_CONSENT_SCOPE),
        )
        if not started.get("ok"):
            return json_response(started, status=409)
        lease_id = str(started.get("leaseId") or "")
        control = await request_local_bridge_mic_control(
            True,
            source="voice_capture_consent:validation",
        )
        bridge = dict(control.get("localBridge") or {})
        mic = dict(bridge.get("mic") or {})
        applied = bool(control.get("applied")) and bool(bridge.get("micEnabled"))
        capture_ready = (
            applied
            and bool(bridge.get("ready"))
            and bool(mic.get("captureReady"))
        )
        try:
            completed = manager.finish_apply(
                lease_id=lease_id,
                applied=applied,
                capture_ready=capture_ready,
                error=str(control.get("error") or bridge.get("lastError") or ""),
            )
        except Exception as exc:
            disable = await request_local_bridge_mic_control(
                False,
                source="voice_capture_consent:state_write_failed",
            )
            return json_response(
                {
                    "ok": False,
                    "error": "voice_capture_consent_state_write_failed",
                    "detail": repr(exc),
                    "controlApplied": bool(disable.get("applied")),
                    "localBridge": dict(disable.get("localBridge") or bridge),
                },
                status=503,
            )
        if not completed.get("ok"):
            disable = await request_local_bridge_mic_control(
                False,
                source="voice_capture_consent:activation_failed",
            )
            disabled_bridge = dict(disable.get("localBridge") or {})
            disabled = bool(disable.get("applied")) and not bool(
                disabled_bridge.get("micEnabled")
            )
            cleanup = manager.finish_revoke(
                applied=disabled,
                error=str(disable.get("error") or "mic_control_ack_timeout"),
            )
            return json_response(
                {
                    **completed,
                    "consent": manager.status(),
                    "cleanup": cleanup,
                    "localBridge": disabled_bridge or bridge,
                },
                status=503,
            )

    health = await cached_runtime_health(force=True)
    capabilities = _voice_capabilities_with_capture_consent(health)
    validation_manager = get_voice_validation_manager()
    validation = validation_manager.snapshot(capabilities=capabilities)
    if validation.get("state") == "preflight" and _voice_validation_uses_local(
        validation
    ):
        resumed = validation_manager.resume_after_preflight(
            capabilities=capabilities
        )
        if resumed.get("ok"):
            validation = dict(resumed.get("session") or validation)
    if validation.get("state") == "running" and _voice_validation_uses_local(
        validation
    ):
        manager.bind_validation_session(str(validation.get("sessionId") or ""))
        validation = validation_manager.snapshot(capabilities=capabilities)
    return json_response(
        {
            "ok": True,
            "consent": manager.status(),
            "validationSession": validation,
            "localBridge": bridge,
        }
    )


async def voice_capture_consent_revoke_handler(
    request: web.Request,
) -> web.StreamResponse:
    result = await _revoke_voice_capture_consent(
        request.app,
        reason="user_revoked",
    )
    return json_response(
        {
            **result,
            "consent": get_voice_capture_consent_manager().status(),
        },
        status=200 if result.get("ok") else 503,
    )


async def voice_validation_handler(request: web.Request) -> web.StreamResponse:
    validation_manager = get_voice_validation_manager()
    validation = validation_manager.snapshot()
    await _reconcile_voice_capture_consent(
        request.app,
        validation_session=validation,
    )
    health = await cached_runtime_health(force=True)
    capabilities = _voice_capabilities_with_capture_consent(health)
    session = validation_manager.snapshot(capabilities=capabilities)
    return json_response({"ok": True, "session": session})


async def storage_retention_handler(_: web.Request) -> web.StreamResponse:
    return json_response(read_storage_retention_report())


async def voice_validation_start_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    suite = str((payload or {}).get("suite") or SUITE_ID).strip()
    surfaces = (payload or {}).get("surfaces")
    if not isinstance(surfaces, list):
        return json_response({"ok": False, "error": "surfaces_required"}, status=400)
    validation_manager = get_voice_validation_manager()
    current_validation = validation_manager.snapshot()
    await _reconcile_voice_capture_consent(
        request.app,
        validation_session=current_validation,
    )
    health = await cached_runtime_health(force=True)
    capabilities = _voice_capabilities_with_capture_consent(health)
    result = validation_manager.start(
        suite=suite,
        surfaces=[str(item) for item in surfaces],
        capabilities=capabilities,
    )
    session = dict(result.get("session") or {})
    if (
        result.get("ok")
        and session.get("state") == "running"
        and _voice_validation_uses_local(session)
    ):
        get_voice_capture_consent_manager().bind_validation_session(
            str(session.get("sessionId") or "")
        )
        result["session"] = validation_manager.snapshot(capabilities=capabilities)
    status = 201 if result.get("ok") else 409 if result.get("error") == "validation_session_active" else 400
    return json_response(result, status=status)


async def voice_validation_confirm_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = get_voice_validation_manager().confirm(
        session_id=str((payload or {}).get("sessionId") or ""),
        step_id=str((payload or {}).get("stepId") or ""),
        heard=bool((payload or {}).get("heard")),
    )
    session = dict(result.get("session") or {})
    if result.get("ok") and session.get("state") in {"passed", "failed", "aborted"}:
        await _reconcile_voice_capture_consent(
            request.app,
            validation_session=session,
        )
    return json_response(result, status=200 if result.get("ok") else 409)


async def voice_validation_retry_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = get_voice_validation_manager().retry(
        session_id=str((payload or {}).get("sessionId") or ""),
        step_id=str((payload or {}).get("stepId") or ""),
    )
    return json_response(result, status=200 if result.get("ok") else 409)


async def voice_validation_abort_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = get_voice_validation_manager().abort(
        session_id=str((payload or {}).get("sessionId") or ""),
    )
    session = dict(result.get("session") or {})
    if result.get("ok"):
        await _reconcile_voice_capture_consent(
            request.app,
            validation_session=session,
        )
    return json_response(result, status=200 if result.get("ok") else 409)


async def ui_action_status_handler(
    request: web.Request,
) -> web.StreamResponse:
    proxied = await proxy_json(
        request,
        "GET",
        "/api/control-page/ui-action",
    )
    if proxied is not None:
        return proxied
    return json_response(
        {
            "ok": False,
            "schema": "ui_action.control-status.v1",
            "status": {},
            "error": "bot_api_unavailable",
        },
        status=503,
    )


async def ui_action_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    proxied = await proxy_json(
        request,
        "POST",
        "/api/control-page/ui-action/preview",
        body=payload,
    )
    if proxied is not None:
        return proxied
    return json_response(
        {"ok": False, "error": "bot_api_unavailable"},
        status=503,
    )


async def ui_action_targets_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    proxied = await proxy_json(
        request,
        "POST",
        "/api/control-page/ui-action/targets",
        body=payload,
    )
    if proxied is not None:
        return proxied
    return json_response(
        {"ok": False, "error": "bot_api_unavailable"},
        status=503,
    )


async def ui_action_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    proxied = await proxy_json(
        request,
        "POST",
        "/api/control-page/ui-action/apply",
        body=payload,
    )
    if proxied is not None:
        return proxied
    return json_response(
        {"ok": False, "error": "bot_api_unavailable"},
        status=503,
    )


async def shutdown_handler(_: web.Request) -> web.StreamResponse:
    proxied = await proxy_json(_, "POST", "/api/control-page/shutdown", body={"source": "control_page", "reason": "shutdown_button"})
    if proxied is not None and proxied.status < 500:
        return proxied
    ok, detail = schedule_local_stack_shutdown(delay_ms=500)
    status = 200 if ok else 500
    return json_response(
        {
            "ok": ok,
            "message": "Local Evelyn shutdown is running." if ok else f"Shutdown failed: {detail}",
            "detail": detail,
        },
        status=status,
    )


def open_path_with_system(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_url_with_system(url: str) -> None:
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_memory_vault_payload() -> dict[str, Any]:
    vault = ensure_memory_vault_layout()
    obsidian_url = "obsidian://open?path=" + quote(str(vault), safe="")
    try:
        open_url_with_system(obsidian_url)
        return {
            "ok": True,
            "message": "Obsidian memory vault open request sent.",
            "vaultPath": str(vault),
            "url": obsidian_url,
        }
    except Exception as exc:
        try:
            open_path_with_system(vault)
            return {
                "ok": True,
                "message": f"Obsidian protocol failed, opened the vault folder instead: {exc}",
                "vaultPath": str(vault),
                "url": obsidian_url,
                "fallback": "folder",
            }
        except Exception as fallback_exc:
            return {
                "ok": False,
                "error": "open_memory_vault_failed",
                "message": str(fallback_exc),
                "vaultPath": str(vault),
                "url": obsidian_url,
            }


async def open_memory_vault_handler(_: web.Request) -> web.StreamResponse:
    payload = open_memory_vault_payload()
    return json_response(payload, status=200 if payload.get("ok") else 500)


async def open_memory_vault_options_handler(_: web.Request) -> web.StreamResponse:
    return json_response({"ok": True, "methods": ["POST", "OPTIONS"]})


async def memory_graph_handler(request: web.Request) -> web.StreamResponse:
    try:
        max_nodes = int(request.query.get("max_nodes", "160"))
    except Exception:
        max_nodes = 160
    include_internal = str(request.query.get("include_internal", "")).lower() in {"1", "true", "yes", "on"}
    return json_response(export_memory_graph(max_nodes=max_nodes, include_internal=include_internal))


async def memory_snapshot_handler(request: web.Request) -> web.StreamResponse:
    include_hidden = str(request.query.get("include_hidden", "")).lower() in {"1", "true", "yes", "on"}
    include_internal = str(request.query.get("include_internal", "")).lower() in {"1", "true", "yes", "on"}
    try:
        limit = int(request.query.get("limit", "80"))
    except Exception:
        limit = 80
    return json_response(memory_vault_user_snapshot(include_hidden=include_hidden, include_internal=include_internal, limit=limit))


async def memory_provenance_audit_handler(
    request: web.Request,
) -> web.StreamResponse:
    include_internal = str(
        request.query.get("include_internal", "")
    ).lower() in {"1", "true", "yes", "on"}
    return json_response(
        memory_provenance_backfill_preview(
            include_internal=include_internal,
        )
    )


async def memory_note_handler(request: web.Request) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    include_internal = str(request.query.get("include_internal", "")).lower() in {"1", "true", "yes", "on"}
    result = memory_vault_user_note(note_id, include_internal=include_internal)
    return json_response(result, status=200 if result.get("ok") else 404)


async def memory_note_action_handler(request: web.Request) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    action = str((payload or {}).get("action") or "").strip()
    result = update_memory_vault_user_note(
        note_id,
        action,
        title=(payload or {}).get("title"),
        body=(payload or {}).get("body"),
        expected_content_hash=(payload or {}).get(
            "expectedContentHash"
        ),
    )
    return json_response(
        result,
        status=memory_note_action_status(result),
    )


def memory_note_action_status(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    error = str(result.get("error") or "")
    if error == "note_not_found":
        return 404
    if error in {
        "locked_legacy_note",
        "memory_note_changed_since_read",
        "memory_note_quarantined",
    }:
        return 409
    if error == "memory_edit_failed":
        return 500
    if error == "memory_edit_cleanup_required":
        return 503
    return 400


def memory_note_delete_status(result: dict[str, Any]) -> int:
    if result.get("ok"):
        return 200
    error = str(result.get("error") or "")
    if error == "note_not_found":
        return 404
    if error == "memory_delete_failed":
        return 500
    if error == "memory_delete_cleanup_required":
        return 503
    if error in {
        "memory_delete_token_expired",
        "memory_delete_token_mismatch",
        "memory_delete_token_reused",
        "memory_note_changed_since_preview",
        "memory_derivation_impact_changed_since_preview",
        "memory_note_delete_protected",
    }:
        return 409
    return 400


def memory_provenance_backfill_status(
    result: dict[str, Any],
) -> int:
    if result.get("ok"):
        return 200
    error = str(result.get("error") or "")
    if error == "note_not_found":
        return 404
    if error == "memory_provenance_backfill_failed":
        return 500
    if (
        error
        == "memory_provenance_backfill_cleanup_required"
    ):
        return 503
    if error in {
        "memory_provenance_backfill_ambiguous",
        "memory_provenance_backfill_candidate_unavailable",
        "memory_provenance_backfill_changed_since_preview",
        "memory_provenance_backfill_protected",
        "memory_provenance_backfill_source_mismatch",
        "memory_provenance_backfill_source_unavailable",
        "memory_provenance_backfill_token_expired",
        "memory_provenance_backfill_token_invalid",
        "memory_provenance_backfill_token_mismatch",
        "memory_provenance_backfill_token_reused",
        "memory_provenance_manual_cycle",
        "memory_provenance_manual_exact_candidate_available",
        "memory_provenance_manual_source_ungrounded",
        "memory_provenance_manual_target_ineligible",
        "memory_provenance_source_hidden",
        "memory_provenance_source_not_public",
        "memory_provenance_source_quarantined",
    }:
        return 409
    return 400


def memory_provenance_correction_status(
    result: dict[str, Any],
) -> int:
    if result.get("ok"):
        return 200
    error = str(result.get("error") or "")
    if error == "note_not_found":
        return 404
    if error == "memory_provenance_correction_failed":
        return 500
    if (
        error
        == "memory_provenance_correction_cleanup_required"
    ):
        return 503
    if error in {
        "memory_provenance_correction_changed_since_preview",
        "memory_provenance_correction_cycle",
        "memory_provenance_correction_no_change",
        "memory_provenance_correction_protected",
        "memory_provenance_correction_source_unavailable",
        "memory_provenance_correction_source_ungrounded",
        "memory_provenance_correction_target_ineligible",
        "memory_provenance_correction_token_expired",
        "memory_provenance_correction_token_invalid",
        "memory_provenance_correction_token_mismatch",
        "memory_provenance_correction_token_reused",
        "memory_provenance_correction_undo_unavailable",
        "memory_provenance_source_hidden",
        "memory_provenance_source_not_public",
        "memory_provenance_source_quarantined",
    }:
        return 409
    return 400


async def memory_provenance_backfill_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    source_note_ids = (
        (payload or {}).get("sourceNoteIds")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(source_note_ids, list):
        source_note_ids = []
    result = preview_memory_provenance_backfill_application(
        note_id,
        [str(item) for item in source_note_ids],
    )
    return json_response(
        result,
        status=memory_provenance_backfill_status(result),
    )


async def memory_provenance_backfill_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = apply_memory_provenance_backfill(
        note_id,
        str((payload or {}).get("confirmToken") or ""),
    )
    return json_response(
        result,
        status=memory_provenance_backfill_status(result),
    )


async def memory_provenance_manual_sources_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    result = memory_provenance_manual_source_options(
        note_id
    )
    return json_response(
        result,
        status=memory_provenance_backfill_status(result),
    )


async def memory_provenance_manual_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    source_note_ids = (
        (payload or {}).get("sourceNoteIds")
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(source_note_ids, list):
        source_note_ids = []
    result = preview_memory_provenance_backfill_application(
        note_id,
        [str(item) for item in source_note_ids],
        selection_mode="user_selected",
    )
    return json_response(
        result,
        status=memory_provenance_backfill_status(result),
    )


async def memory_provenance_backfill_options_handler(
    _: web.Request,
) -> web.StreamResponse:
    return json_response(
        {"ok": True, "methods": ["POST", "OPTIONS"]}
    )


async def memory_provenance_corrections_handler(
    _: web.Request,
) -> web.StreamResponse:
    return json_response(
        memory_provenance_correction_overview()
    )


async def memory_provenance_correction_sources_handler(
    request: web.Request,
) -> web.StreamResponse:
    result = memory_provenance_correction_source_options(
        request.match_info.get("note_id", "")
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = None
    source_note_ids = (
        payload.get("sourceNoteIds")
        if isinstance(payload, dict)
        and "sourceNoteIds" in payload
        else None
    )
    if (
        not isinstance(source_note_ids, list)
        or not all(
            isinstance(item, str)
            for item in source_note_ids
        )
    ):
        result = {
            "ok": False,
            "error": (
                "memory_provenance_correction_source_ids_invalid"
            ),
        }
        return json_response(
            result,
            status=memory_provenance_correction_status(result),
        )
    result = preview_memory_provenance_correction(
        request.match_info.get("note_id", ""),
        source_note_ids,
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = apply_memory_provenance_correction(
        request.match_info.get("note_id", ""),
        str((payload or {}).get("confirmToken") or ""),
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_undo_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = preview_memory_provenance_correction_undo(
        request.match_info.get("note_id", ""),
        str((payload or {}).get("changeId") or ""),
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_undo_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = apply_memory_provenance_correction_undo(
        request.match_info.get("note_id", ""),
        str((payload or {}).get("confirmToken") or ""),
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_note_delete_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = preview_memory_vault_user_note_deletion(
        note_id,
        reason=str((payload or {}).get("reason") or "user_requested"),
    )
    return json_response(result, status=memory_note_delete_status(result))


async def memory_note_delete_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = delete_memory_vault_user_note(
        note_id,
        str((payload or {}).get("confirmToken") or ""),
        reason=str((payload or {}).get("reason") or "user_requested"),
    )
    return json_response(result, status=memory_note_delete_status(result))


async def memory_note_delete_options_handler(
    _: web.Request,
) -> web.StreamResponse:
    return json_response({"ok": True, "methods": ["POST", "OPTIONS"]})


async def chat_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    text = str((payload or {}).get("text") or "").strip()
    normalized = text.lower()
    if normalized in LOCAL_HELP_COMMANDS:
        state = await degraded_state()
        commands = state.get("commands") if isinstance(state, dict) else []
        reply = format_command_help(commands if isinstance(commands, list) else [])
        return json_response({"ok": True, "reply": reply, "state": state})
    if normalized in LOCAL_STATUS_COMMANDS:
        proxied = await proxy_json(request, "POST", "/api/control-page/chat", body=payload)
        if proxied is not None and proxied.status < 500:
            return proxied
        state = await degraded_state()
        runtime = state.get("runtime") if isinstance(state, dict) else {}
        services = runtime.get("services") if isinstance(runtime, dict) else {}
        summary = services.get("summary") if isinstance(services, dict) else ""
        return json_response({"ok": True, "reply": str(summary or state.get("statusText") or "Control-Page is live."), "state": state})
    if normalized == "/memory":
        state = await degraded_state(proxy_failure=last_proxy_failure(request))
        return json_response({"ok": True, "reply": memory_panel_reply("toggle"), "state": with_memory_panel_command(state, "toggle")})
    if normalized == "/obsidian":
        state = await degraded_state()
        result = open_memory_vault_payload()
        state["memoryVaultOpen"] = result
        reply = result.get("message") if result.get("ok") else f"Obsidian open failed: {result.get('message')}"
        return json_response(
            {"ok": bool(result.get("ok")), "reply": reply, "state": state, "openResult": result},
            status=200 if result.get("ok") else 500,
        )
    runtime_command = detect_local_runtime_command(text)
    restart_requested = runtime_command == "restart"
    if normalized in LOCAL_RESTART_COMMANDS:
        restart_requested = True
    if restart_requested:
        proxied = await proxy_json(request, "POST", "/api/control-page/chat", body=payload)
        if proxied is not None and proxied.status < 500:
            return proxied
        ok, detail = schedule_local_stack_restart()
        state = await degraded_state()
        if ok:
            return json_response(
                {
                    "ok": True,
                    "reply": local_restart_requested_reply(),
                    "state": state,
                }
            )
        return json_response(
            {
                "ok": False,
                "error": "local_restart_failed",
                "reply": f"Local restart helper failed: {detail}",
                "state": state,
            },
            status=500,
        )
    shutdown_requested = runtime_command == "shutdown"
    if normalized in LOCAL_SHUTDOWN_COMMANDS:
        shutdown_requested = True
    if shutdown_requested:
        proxied = await proxy_json(request, "POST", "/api/control-page/chat", body=payload)
        if proxied is not None and proxied.status < 500:
            return proxied
        ok, detail = schedule_local_stack_shutdown()
        state = await degraded_state()
        if ok:
            return json_response(
                {
                    "ok": True,
                    "reply": "Local Evelyn shutdown started. This works even when Bot API is down.",
                    "state": state,
                }
            )
        return json_response(
            {
                "ok": False,
                "error": "local_shutdown_failed",
                "reply": f"Local shutdown helper failed: {detail}",
                "state": state,
            },
            status=500,
        )
    proxied = await proxy_json(request, "POST", "/api/control-page/chat", body=payload)
    if proxied is not None and proxied.status < 500:
        return proxied
    state = await degraded_state(proxy_failure=last_proxy_failure(request))
    return json_response(
        {
            "ok": False,
            "error": "bot_api_unavailable",
            "reply": state.get("statusText") or "Control-Page is live, but Bot API is unavailable.",
            "state": state,
        }
    )


async def action_events_handler(request: web.Request) -> web.StreamResponse:
    proxied = await proxy_json(request, "GET", "/api/control-page/action-events")
    if proxied is not None:
        return proxied
    return json_response(
        {
            "ok": False,
            "error": "bot_api_unavailable",
            "events": [],
            "tasks": [],
            "activeCount": 0,
        },
        status=503,
    )


async def icon_handler(request: web.Request) -> web.StreamResponse:
    item_name = request.match_info.get("item_name", "")
    proxied = await proxy_raw(request, f"/api/control-page/minecraft-item-icon/{item_name}")
    if proxied is not None and proxied.status == 200:
        return proxied
    raise web.HTTPNotFound(text="item icon not available")


def create_app() -> web.Application:
    app = web.Application(middlewares=[control_page_cors_middleware])
    app[VOICE_CAPTURE_CONSENT_LOCK_KEY] = asyncio.Lock()
    app.cleanup_ctx.append(_voice_capture_consent_context)
    app.router.add_get("/", index_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/assets/{asset_path:.*}", asset_handler)
    app.router.add_get("/api/control-page/state", state_handler)
    app.router.add_get("/api/control-page/session", control_page_session_handler)
    app.router.add_get("/api/control-page/runtime-health", runtime_health_handler)
    app.router.add_get("/api/control-page/runtime-errors", runtime_errors_handler)
    app.router.add_post("/api/control-page/runtime-health/override", runtime_health_override_handler)
    app.router.add_get("/api/control-page/runtime-manifest", runtime_manifest_handler)
    app.router.add_get("/api/control-page/runtime-repair", runtime_repair_handler)
    app.router.add_post("/api/control-page/runtime-repair/preview", runtime_repair_preview_handler)
    app.router.add_post("/api/control-page/runtime-repair/apply", runtime_repair_apply_handler)
    app.router.add_get("/api/control-page/storage-retention", storage_retention_handler)
    app.router.add_get(
        "/api/control-page/voice-capture-consent",
        voice_capture_consent_handler,
    )
    app.router.add_post(
        "/api/control-page/voice-capture-consent/preview",
        voice_capture_consent_preview_handler,
    )
    app.router.add_post(
        "/api/control-page/voice-capture-consent/apply",
        voice_capture_consent_apply_handler,
    )
    app.router.add_post(
        "/api/control-page/voice-capture-consent/revoke",
        voice_capture_consent_revoke_handler,
    )
    app.router.add_get("/api/control-page/voice-validation", voice_validation_handler)
    app.router.add_post("/api/control-page/voice-validation/start", voice_validation_start_handler)
    app.router.add_post("/api/control-page/voice-validation/confirm", voice_validation_confirm_handler)
    app.router.add_post("/api/control-page/voice-validation/retry", voice_validation_retry_handler)
    app.router.add_post("/api/control-page/voice-validation/abort", voice_validation_abort_handler)
    app.router.add_get(
        "/api/control-page/ui-action",
        ui_action_status_handler,
    )
    app.router.add_post(
        "/api/control-page/ui-action/targets",
        ui_action_targets_handler,
    )
    app.router.add_post(
        "/api/control-page/ui-action/preview",
        ui_action_preview_handler,
    )
    app.router.add_post(
        "/api/control-page/ui-action/apply",
        ui_action_apply_handler,
    )
    app.router.add_get("/api/control-page/memory", memory_snapshot_handler)
    app.router.add_get("/api/control-page/memory-graph", memory_graph_handler)
    app.router.add_get(
        "/api/control-page/memory-provenance-audit",
        memory_provenance_audit_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-backfill/"
            "{note_id}/preview"
        ),
        memory_provenance_backfill_preview_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-backfill/"
            "{note_id}/apply"
        ),
        memory_provenance_backfill_apply_handler,
    )
    app.router.add_get(
        (
            "/api/control-page/memory-provenance-manual/"
            "{note_id}/sources"
        ),
        memory_provenance_manual_sources_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-manual/"
            "{note_id}/preview"
        ),
        memory_provenance_manual_preview_handler,
    )
    app.router.add_get(
        "/api/control-page/memory-provenance-corrections",
        memory_provenance_corrections_handler,
    )
    app.router.add_get(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/sources"
        ),
        memory_provenance_correction_sources_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/preview"
        ),
        memory_provenance_correction_preview_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/apply"
        ),
        memory_provenance_correction_apply_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/undo/preview"
        ),
        memory_provenance_correction_undo_preview_handler,
    )
    app.router.add_post(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/undo/apply"
        ),
        memory_provenance_correction_undo_apply_handler,
    )
    app.router.add_get("/api/control-page/memory/{note_id}", memory_note_handler)
    app.router.add_post("/api/control-page/open-memory-vault", open_memory_vault_handler)
    app.router.add_post("/api/control-page/memory/{note_id}", memory_note_action_handler)
    app.router.add_post(
        "/api/control-page/memory/{note_id}/delete/preview",
        memory_note_delete_preview_handler,
    )
    app.router.add_post(
        "/api/control-page/memory/{note_id}/delete/apply",
        memory_note_delete_apply_handler,
    )
    app.router.add_post("/api/control-page/shutdown", shutdown_handler)
    app.router.add_post("/api/control-page/chat", chat_handler)
    app.router.add_get("/api/control-page/action-events", action_events_handler)
    app.router.add_get("/api/control-page/minecraft-item-icon/{item_name}", icon_handler)
    app.router.add_options("/api/control-page/state", state_handler)
    app.router.add_options("/api/control-page/memory", memory_snapshot_handler)
    app.router.add_options("/api/control-page/memory-graph", memory_graph_handler)
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-backfill/"
            "{note_id}/preview"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        "/api/control-page/memory-provenance-corrections",
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/sources"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/preview"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/apply"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/undo/preview"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-corrections/"
            "{note_id}/undo/apply"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-manual/"
            "{note_id}/sources"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-manual/"
            "{note_id}/preview"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options(
        (
            "/api/control-page/memory-provenance-backfill/"
            "{note_id}/apply"
        ),
        memory_provenance_backfill_options_handler,
    )
    app.router.add_options("/api/control-page/memory/{note_id}", memory_note_handler)
    app.router.add_options(
        "/api/control-page/memory/{note_id}/delete/preview",
        memory_note_delete_options_handler,
    )
    app.router.add_options(
        "/api/control-page/memory/{note_id}/delete/apply",
        memory_note_delete_options_handler,
    )
    app.router.add_options("/api/control-page/open-memory-vault", open_memory_vault_options_handler)
    app.router.add_options("/api/control-page/shutdown", shutdown_handler)
    app.router.add_options("/api/control-page/chat", chat_handler)
    app.router.add_options(
        "/api/control-page/voice-capture-consent/preview",
        voice_capture_consent_preview_handler,
    )
    app.router.add_options(
        "/api/control-page/voice-capture-consent/apply",
        voice_capture_consent_apply_handler,
    )
    app.router.add_options(
        "/api/control-page/voice-capture-consent/revoke",
        voice_capture_consent_revoke_handler,
    )
    app.router.add_options("/api/control-page/voice-validation/start", voice_validation_start_handler)
    app.router.add_options("/api/control-page/voice-validation/confirm", voice_validation_confirm_handler)
    app.router.add_options("/api/control-page/voice-validation/retry", voice_validation_retry_handler)
    app.router.add_options("/api/control-page/voice-validation/abort", voice_validation_abort_handler)
    app.router.add_options(
        "/api/control-page/ui-action/targets",
        ui_action_targets_handler,
    )
    app.router.add_options(
        "/api/control-page/ui-action/preview",
        ui_action_preview_handler,
    )
    app.router.add_options(
        "/api/control-page/ui-action/apply",
        ui_action_apply_handler,
    )
    return app


def main() -> None:
    web.run_app(create_app(), host=HOST, port=PORT, access_log=None)


if __name__ == "__main__":
    main()
