from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from aiohttp import ClientConnectorError, ClientSession, ClientTimeout, web

from .autonomy_validation import (
    SUITE_ID as AUTONOMY_VALIDATION_SUITE_ID,
    get_autonomy_validation_manager,
)
from .config import MEMORY_ROOT
from .control_page_memory_http import (
    CONTROL_PAGE_MEMORY_BOUNDARY_HEADER,
    CONTROL_PAGE_MEMORY_STATE_HEADER,
    ControlPageMemoryGuardedJsonResponse,
    control_page_memory_guarded_json_response,
    parse_control_page_memory_handoff_headers,
)
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
from .memory_deletion_journal import (
    MEMORY_DELETION_JOURNAL_BUSY_ERROR,
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_guard,
)
from .memory_exposure import MemoryExposurePosition
from .host_supervisor_client import HostSupervisorClient
from .memory_vault import (
    apply_memory_provenance_backfill,
    delete_memory_vault_user_note,
    ensure_memory_vault_layout,
    export_memory_graph,
    memory_index_dir,
    memory_provenance_backfill_preview,
    memory_provenance_manual_source_options,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    preview_memory_provenance_backfill_application,
    preview_memory_vault_user_note_deletion,
    update_memory_vault_user_note,
)
from .minecraft_owner_lock import (
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)
from .paths import get_runtime_artifacts_root
from .public_error_contract import public_error_code, public_failure_message
from .runtime_health import (
    apply_runtime_health_overrides,
    collect_runtime_health,
    public_runtime_health_snapshot,
)
from .runtime_health_snapshot_cache import (
    RuntimeHealthSnapshotCache,
)
from .runtime_source_identity import (
    runtime_source_identity,
    source_identities_compatible,
)
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
    VALIDATION_BINDING_SCHEMA as VOICE_CAPTURE_VALIDATION_BINDING_SCHEMA,
    attach_voice_capture_consent,
    get_voice_capture_consent_manager,
    voice_capture_auth_scrubbed_environment,
)
from .voice_validation import (
    SUITE_ID,
    get_voice_validation_manager,
    resolve_discord_validation_target,
)


PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"

HOST = os.getenv("CONTROL_PAGE_HOST", "127.0.0.1")
PORT = int(os.getenv("CONTROL_PAGE_PUBLIC_PORT", os.getenv("CONTROL_PAGE_PORT", "8799")))
BOT_API_HOST = os.getenv("CONTROL_PAGE_BOT_API_HOST", "127.0.0.1")
BOT_API_PORT = int(os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798"))
BOT_API_BASE = f"http://{BOT_API_HOST}:{BOT_API_PORT}"
EVELYN_INTERNAL_CONTROL_HEADER = "X-Evelyn-Internal-Control-Token"
EVELYN_INTERNAL_CONTROL_TOKEN = os.getenv(
    "EVELYN_INTERNAL_CONTROL_TOKEN",
    "",
).strip()
PROXY_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_PROXY_TIMEOUT_SEC", "6.0"))
MEMORY_MUTATION_ADMISSION_TIMEOUT_SEC = 2.0
MEMORY_MUTATION_ADMISSION_RETRY_SEC = 0.05
LOCAL_HELP_COMMANDS = {"/", "/help"}
LOCAL_STATUS_COMMANDS = {"/status"}
LOCAL_MEMORY_COMMANDS = {"/memory", "/obsidian"}
LOCAL_RESTART_COMMANDS = {"/restart", "restart"}
LOCAL_SHUTDOWN_COMMANDS = {"/shutdown", "/quit", "/exit"}
MEMORY_HANDOFF_PROXY_PATHS = frozenset(
    {
        "/api/control-page/action-events",
        "/api/control-page/chat",
        "/api/control-page/shutdown",
        "/api/control-page/state",
    }
)

MODEL_PORTS = {
    "main": int(os.getenv("MAIN_LLM_PORT", "9820")),
    "router": int(os.getenv("ROUTER_LLM_PORT", "9822")),
    "sub": int(os.getenv("SUB_LLM_PORT", "9821")),
    "tts": int(os.getenv("TTS_PORT", "8880")),
    "voyager": int(os.getenv("MINECRAFT_AUTONOMY_SERVICE_PORT", "8765")),
    "codex": int(os.getenv("VOYAGER_CODEX_GATEWAY_PORT", "8787")),
    "bot": BOT_API_PORT,
}
RUNTIME_HEALTH_CACHE_TTL_SEC = max(
    0.5,
    float(
        os.getenv(
            "CONTROL_PAGE_RUNTIME_HEALTH_CACHE_TTL_SEC",
            "2.0",
        )
    ),
)
RUNTIME_HEALTH_CACHE_MAX_STALE_SEC = max(
    RUNTIME_HEALTH_CACHE_TTL_SEC,
    float(
        os.getenv(
            "CONTROL_PAGE_RUNTIME_HEALTH_CACHE_MAX_STALE_SEC",
            "6.0",
        )
    ),
)
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
VOICE_CAPTURE_MIC_CONTROL_ACK_SCHEMA = "local_io_bridge.mic-control-ack.v1"


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


def source_identity_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("sourceIdentity")
    if isinstance(direct, dict):
        return dict(direct)
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        return None
    nested = runtime.get("sourceIdentity")
    return dict(nested) if isinstance(nested, dict) else None


def bot_source_identity_compatible(payload: Any) -> bool:
    local_identity = runtime_source_identity()
    remote_identity = source_identity_from_payload(payload)
    if (
        local_identity.get("mode") == "development"
        and remote_identity is None
    ):
        # Backward-compatible only for an unversioned host development pair.
        return True
    return bool(
        remote_identity is not None
        and source_identities_compatible(
            local_identity,
            remote_identity,
        )
    )


async def probe_bot_health_identity() -> tuple[bool, dict[str, Any] | None]:
    timeout = ClientTimeout(total=min(PROXY_TIMEOUT_SEC, 3.0))
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.get(f"{BOT_API_BASE}/health") as response:
                payload = await response.json(content_type=None)
                identity = source_identity_from_payload(payload)
                ready = bool(
                    response.status == 200
                    and isinstance(payload, dict)
                    and payload.get("ok") is True
                    and bot_source_identity_compatible(payload)
                )
                return ready, identity
    except Exception:
        return False, None


async def collect_control_page_runtime_health() -> dict[str, Any]:
    manifest = load_service_manifest()
    health = await collect_runtime_health(manifest=manifest)
    prune_runtime_health_overrides()
    return apply_runtime_health_overrides(
        health,
        runtime_health_overrides,
        manifest=manifest,
    )


CONTROL_PAGE_RUNTIME_HEALTH_CACHE = RuntimeHealthSnapshotCache(
    collector=collect_control_page_runtime_health,
    refresh_after_sec=RUNTIME_HEALTH_CACHE_TTL_SEC,
    max_stale_sec=RUNTIME_HEALTH_CACHE_MAX_STALE_SEC,
)


async def cached_runtime_health(*, force: bool = False) -> dict[str, Any]:
    return public_runtime_health_snapshot(
        await CONTROL_PAGE_RUNTIME_HEALTH_CACHE.get(force=force)
    )


def proxy_json_response(
    *,
    status: int,
    text: str,
    content_type: str,
    expected_position: MemoryExposurePosition | None = None,
    memory_handoff_present: bool = False,
) -> web.Response:
    if status == 503:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, RecursionError):
            payload = None
        if isinstance(payload, dict):
            if payload.get("error") == MEMORY_DELETION_JOURNAL_BUSY_ERROR:
                raise MemoryDeletionJournalBusyError()
            if (
                payload.get("error")
                == MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
            ):
                raise MemoryDeletionJournalIntegrityError()
    if memory_handoff_present:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, RecursionError):
            raise MemoryDeletionJournalIntegrityError() from None
        if not isinstance(payload, dict):
            raise MemoryDeletionJournalIntegrityError()
        return control_page_memory_guarded_json_response(
            payload,
            expected_position=expected_position,
            memory_index_dir=Path(MEMORY_ROOT) / "memory_index",
            status=status,
            emit_handoff_headers=False,
        )
    response = web.Response(
        status=status,
        text=text,
        content_type=content_type or "application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def proxy_memory_handoff(
    *,
    path: str,
    headers: Any,
) -> tuple[bool, MemoryExposurePosition | None]:
    state_present = CONTROL_PAGE_MEMORY_STATE_HEADER in headers
    boundary_present = CONTROL_PAGE_MEMORY_BOUNDARY_HEADER in headers
    if state_present != boundary_present:
        raise MemoryDeletionJournalIntegrityError()
    if not state_present:
        if path in MEMORY_HANDOFF_PROXY_PATHS:
            raise MemoryDeletionJournalIntegrityError()
        return False, None
    return True, parse_control_page_memory_handoff_headers(headers)


async def proxy_json(request: web.Request, method: str, path: str, *, body: Any = None) -> web.Response | None:
    query = request.query_string
    url = f"{BOT_API_BASE}{path}" + (f"?{query}" if query else "")
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            if method == "POST":
                async with session.post(url, json=body) as response:
                    text = await response.text()
                    handoff_present, expected_position = (
                        proxy_memory_handoff(
                            path=path,
                            headers=response.headers,
                        )
                    )
                    return proxy_json_response(
                        status=response.status,
                        text=text,
                        content_type=(
                            response.content_type
                            or "application/json"
                        ),
                        expected_position=expected_position,
                        memory_handoff_present=handoff_present,
                    )
            async with session.get(url) as response:
                text = await response.text()
                handoff_present, expected_position = proxy_memory_handoff(
                    path=path,
                    headers=response.headers,
                )
                return proxy_json_response(
                    status=response.status,
                    text=text,
                    content_type=(
                        response.content_type
                        or "application/json"
                    ),
                    expected_position=expected_position,
                    memory_handoff_present=handoff_present,
                )
    except MemoryDeletionJournalIntegrityError:
        raise
    except Exception as exc:
        print(
            "[CONTROL PAGE] proxy_failed "
            f"method={method} path={path} "
            f"errorType={type(exc).__name__}"
        )
        remember_proxy_failure(
            request,
            proxy_failure_payload(classify_proxy_exception(exc), url=url),
        )
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
    headers = {
        EVELYN_INTERNAL_CONTROL_HEADER: EVELYN_INTERNAL_CONTROL_TOKEN,
    }
    try:
        async with ClientSession(timeout=timeout) as session:
            enable_fence: dict[str, Any] | None = None
            if enabled:
                async with session.get(url, headers=headers) as response:
                    try:
                        fence_payload = await response.json(content_type=None)
                    except Exception:
                        fence_payload = {}
                    if not isinstance(fence_payload, dict):
                        fence_payload = {}
                    raw_fence = fence_payload.get("enableFence")
                    if (
                        response.status != 200
                        or fence_payload.get("ok") is not True
                        or not isinstance(raw_fence, dict)
                    ):
                        return {
                            "ok": False,
                            "applied": False,
                            "error": public_error_code(
                                fence_payload.get("error"),
                                fallback="mic_enable_fence_unavailable",
                            ),
                            "httpStatus": response.status,
                        }
                    enable_fence = dict(raw_fence)
            request_payload: dict[str, Any] = {
                "enabled": bool(enabled),
                "source": str(source or "control_page"),
            }
            if enabled:
                request_payload.update(
                    {
                        "purpose": "voice_capture_consent",
                        "enableFence": enable_fence,
                    }
                )
            async with session.post(
                url,
                json=request_payload,
                headers=headers,
            ) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                payload.pop("detail", None)
                if payload.get("error"):
                    payload["error"] = public_error_code(
                        payload.get("error"),
                        fallback="mic_control_failed",
                    )
                local_bridge = payload.get("localBridge")
                if isinstance(local_bridge, dict):
                    raw_error = local_bridge.get("lastError")
                    if raw_error:
                        local_bridge["lastError"] = public_error_code(
                            raw_error,
                            fallback="local_bridge_failed",
                        )
                payload.setdefault("httpStatus", response.status)
                if response.status >= 400:
                    payload["ok"] = False
                    payload.setdefault("error", f"mic_control_http_{response.status}")
                return payload
    except Exception as exc:
        print(
            "[CONTROL PAGE] mic_control_proxy_failed "
            f"enabled={bool(enabled)} source={source} "
            f"errorType={type(exc).__name__}"
        )
        return {
            "ok": False,
            "applied": False,
            "error": public_error_code(
                classify_proxy_exception(exc),
                fallback="mic_control_failed",
            ),
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
    _ = url, detail
    return {
        "kind": public_error_code(
            kind,
            fallback="proxy_failed",
        ),
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
        and RUNTIME_HEALTH_CACHE_MAX_STALE_SEC > 0
        and cache_age_sec > RUNTIME_HEALTH_CACHE_MAX_STALE_SEC
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
        if kind == "source_revision_mismatch":
            return (
                "Control-Page is live; Bot API source revision does not "
                f"match the running Control-Page.{cache_note}"
            )
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
    safe_proxy_failure: dict[str, Any] = {}
    if isinstance(proxy_failure, dict):
        safe_proxy_failure["kind"] = public_error_code(
            proxy_failure.get("kind"),
            fallback="proxy_failed",
        )
        failure_at = proxy_failure.get("at")
        if isinstance(failure_at, (int, float)):
            safe_proxy_failure["at"] = float(failure_at)
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
        "lastProxyFailure": safe_proxy_failure,
        "healthCache": {
            "ageSec": round(float(cache_age_sec or 0.0), 1),
            "stale": cache_stale,
            "ttlSec": RUNTIME_HEALTH_CACHE_TTL_SEC,
            "maxStaleSec": RUNTIME_HEALTH_CACHE_MAX_STALE_SEC,
        },
        "statusText": control_plane_status_text(ports=ports, proxy_failure=proxy_failure, cache_age_sec=cache_age_sec),
    }


def build_boot_progress_from_ports(
    ports: dict[str, bool],
    *,
    source_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    steps = [
        {
            "key": key,
            "label": label,
            "done": bool(ports.get(key)),
            "status": "done" if ports.get(key) else "pending",
        }
        for key, label in BOOT_PORT_STEPS
    ]
    identity = (
        dict(source_identity)
        if isinstance(source_identity, dict)
        else runtime_source_identity()
    )
    source_ready = identity.get("ready") is True
    steps.append(
        {
            "key": "source_identity",
            "label": "Runtime source",
            "done": source_ready,
            "status": (
                "done"
                if source_ready
                else str(identity.get("state") or "unverified")
            ),
        }
    )
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
    health_cache = dict(service_health.get("cache") or {})
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
        "healthCacheAgeSec": (
            max(0.0, float(health_cache.get("ageSec") or 0.0))
            if health_cache
            else None
        ),
        "botApiCheckedAt": runtime_service_checked_at(service_health, "bot_api"),
        "botStateLastSuccessAt": bot_state_last_success_at if bot_state_last_success_at > 0 else None,
    }


async def degraded_state(*, proxy_failure: dict[str, Any] | None = None) -> dict[str, Any]:
    progress_state = await current_boot_progress()
    ports = dict(progress_state["ports"])
    inferred_bot_port_open = bool(
        proxy_failure
        and str(proxy_failure.get("kind") or "") != "port_closed"
    )
    if inferred_bot_port_open:
        ports["bot"] = True
    boot_ports = dict(ports)
    if proxy_failure:
        boot_ports["bot"] = False
    boot_progress = (
        build_boot_progress_from_ports(boot_ports)
        if inferred_bot_port_open
        else progress_state["bootProgress"]
    )
    service_health = progress_state.get("serviceHealth")
    legacy_services = (
        dict(service_health.get("legacyServices") or {})
        if isinstance(service_health, dict)
        else {}
    )
    control_plane = build_control_plane_state(
        ports=ports,
        proxy_failure=proxy_failure,
        cache_age_sec=progress_state.get("healthCacheAgeSec"),
        bot_checked_at=progress_state.get("botApiCheckedAt"),
        bot_state_success_at=progress_state.get("botStateLastSuccessAt"),
    )
    source_identity = runtime_source_identity()
    source_aligned = bool(
        source_identity.get("ready") is True
        and str((proxy_failure or {}).get("kind") or "")
        != "source_revision_mismatch"
    )
    return {
        "ok": False,
        "generatedAt": time.time(),
        "localUrl": f"http://{HOST}:{PORT}/",
        "bootProgress": boot_progress,
        "ui": {
            "mode": "default",
            "submode": (
                "offline"
                if proxy_failure or not ports.get("bot")
                else "idle"
            ),
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
                "botReady": bool(ports.get("bot") and not proxy_failure),
                "sourceAligned": source_aligned,
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
            "sourceIdentity": source_identity,
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
    response = web.Response(
        status=status,
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def schedule_local_stack_shutdown(delay_ms: int = 1500) -> tuple[bool, str]:
    stop_script = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
    if not stop_script.exists():
        return False, "local_shutdown_helper_missing"
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
            env=voice_capture_auth_scrubbed_environment(),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "local shutdown scheduled"
    except Exception as exc:
        print(
            "[CONTROL PAGE] local_shutdown_schedule_failed "
            f"errorType={type(exc).__name__}"
        )
        return False, "local_shutdown_failed"


def schedule_local_stack_restart(delay_ms: int = 500) -> tuple[bool, str]:
    stop_script = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
    start_script = PROJECT_ROOT / "evelyn_core" / "start_local.bat"
    if not stop_script.exists():
        return False, "local_restart_stop_helper_missing"
    if not start_script.exists():
        return False, "local_restart_start_helper_missing"
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
            env=voice_capture_auth_scrubbed_environment(),
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "local restart scheduled"
    except Exception as exc:
        print(
            "[CONTROL PAGE] local_restart_schedule_failed "
            f"errorType={type(exc).__name__}"
        )
        return False, "local_restart_failed"


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
                if not bot_source_identity_compatible(payload):
                    failure = proxy_failure_payload(
                        "source_revision_mismatch",
                        url=f"{BOT_API_BASE}/api/control-page/state",
                        detail="Bot API source identity is missing or incompatible.",
                    )
                    remember_proxy_failure(request, failure)
                    return json_response(
                        await degraded_state(proxy_failure=failure)
                    )
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
                runtime["sourceIdentity"] = runtime_source_identity()
                runtime["bootProgress"] = boot_progress
                runtime["manifestVersion"] = service_health.get("manifestVersion") if isinstance(service_health, dict) else None
                runtime["capabilities"] = dict(service_health.get("capabilities") or {}) if isinstance(service_health, dict) else {}
                runtime["observability"] = dict(service_health.get("observability") or {}) if isinstance(service_health, dict) else {}
                runtime["serviceHealth"] = service_health
                payload["runtime"] = runtime
                payload["bootProgress"] = boot_progress
                payload["statusText"] = control_plane["statusText"]
                if isinstance(
                    proxied,
                    ControlPageMemoryGuardedJsonResponse,
                ):
                    return control_page_memory_guarded_json_response(
                        payload,
                        expected_position=(
                            proxied.memory_expected_position
                        ),
                        memory_index_dir=(
                            Path(MEMORY_ROOT) / "memory_index"
                        ),
                        status=proxied.status,
                        emit_handoff_headers=False,
                    )
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
    source_identity = runtime_source_identity()
    bot_ready, bot_identity = await probe_bot_health_identity()
    ready = bool(source_identity.get("ready") is True and bot_ready)
    return json_response(
        {
            "ok": ready,
            "role": "control-page",
            "botProxyReady": bot_ready,
            "botApiPort": BOT_API_PORT,
            "sourceIdentity": source_identity,
            "botSourceIdentity": bot_identity,
        },
        status=200 if ready else 503,
    )


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
    CONTROL_PAGE_RUNTIME_HEALTH_CACHE.clear()
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
        print(
            "[CONTROL PAGE] repair_log_write_failed "
            f"errorType={type(exc).__name__}"
        )
        response["repairLog"] = {
            "ok": False,
            "error": "repair_log_write_failed",
        }
    if response.get("ok"):
        return json_response(response, status=202)
    error = response.get("error")
    status = 409 if error in {"repair_cooldown_active", "confirm_token_required"} else 400
    return json_response(response, status=status)


DISCORD_MODE_PUBLIC_ERRORS = frozenset(
    {
        "docker_compose_failed",
        "host_action_launch_failed",
        "host_supervisor_timeout",
        "host_supervisor_unavailable",
        "preview_token_action_mismatch",
        "preview_token_expired",
        "preview_token_invalid",
        "preview_token_reused",
        "unsupported_host_action",
    }
)


def _discord_mode_request(
    payload: Any,
    *,
    require_confirm_token: bool,
) -> tuple[bool, str] | None:
    required = {"enabled", "confirmToken"} if require_confirm_token else {"enabled"}
    if not isinstance(payload, dict) or set(payload) != required:
        return None
    enabled = payload.get("enabled")
    if type(enabled) is not bool:
        return None
    confirm_token = str(payload.get("confirmToken") or "").strip()
    if require_confirm_token and not confirm_token:
        return None
    return enabled, confirm_token


def _discord_mode_action_id(enabled: bool) -> str:
    return "start_discord_bot" if enabled else "stop_discord_bot"


def _discord_mode_public_error(result: Any) -> str:
    candidate = str(result.get("error") or "") if isinstance(result, dict) else ""
    return (
        candidate
        if candidate in DISCORD_MODE_PUBLIC_ERRORS
        else "discord_mode_transition_failed"
    )


async def discord_mode_preview_handler(request: web.Request) -> web.StreamResponse:
    try:
        parsed = _discord_mode_request(
            await request.json(),
            require_confirm_token=False,
        )
    except Exception:
        parsed = None
    if parsed is None:
        return json_response(
            {"ok": False, "error": "invalid_discord_mode_request"},
            status=400,
        )
    enabled, _ = parsed
    action_id = _discord_mode_action_id(enabled)
    preview = await asyncio.to_thread(HostSupervisorClient().preview, action_id)
    if not preview.get("ok"):
        error = _discord_mode_public_error(preview)
        return json_response(
            {"ok": False, "enabled": enabled, "error": error},
            status=503 if error.startswith("host_supervisor_") else 409,
        )
    confirm_token = str(preview.get("previewToken") or "").strip()
    if not confirm_token:
        return json_response(
            {"ok": False, "enabled": enabled, "error": "discord_mode_transition_failed"},
            status=503,
        )
    return json_response(
        {
            "schema": "discord_mode.preview.v1",
            "ok": True,
            "enabled": enabled,
            "actionId": action_id,
            "confirmToken": confirm_token,
            "expiresAt": preview.get("expiresAt"),
            "requiresConfirm": True,
        }
    )


async def discord_mode_apply_handler(request: web.Request) -> web.StreamResponse:
    try:
        parsed = _discord_mode_request(
            await request.json(),
            require_confirm_token=True,
        )
    except Exception:
        parsed = None
    if parsed is None:
        return json_response(
            {"ok": False, "error": "invalid_discord_mode_request"},
            status=400,
        )
    enabled, confirm_token = parsed
    action_id = _discord_mode_action_id(enabled)
    result = await asyncio.to_thread(
        HostSupervisorClient().apply,
        action_id,
        confirm_token,
    )
    if not result.get("ok"):
        error = _discord_mode_public_error(result)
        status = (
            503
            if error.startswith("host_supervisor_")
            else 502 if error in {"docker_compose_failed", "host_action_launch_failed"} else 409
        )
        return json_response(
            {"ok": False, "enabled": enabled, "error": error},
            status=status,
        )
    CONTROL_PAGE_RUNTIME_HEALTH_CACHE.clear()
    return json_response(
        {
            "schema": "discord_mode.transition.v1",
            "ok": True,
            "enabled": enabled,
            "state": "starting" if enabled else "stopping",
            "message": (
                "Discord 모드를 켜는 중이야."
                if enabled
                else "Discord 모드를 끄는 중이야. Control Page와 Evelyn core는 계속 실행돼."
            ),
        },
        status=202,
    )


def _voice_validation_uses_local(session: dict[str, Any]) -> bool:
    return "local" in {
        str(item or "").strip().lower() for item in (session.get("surfaces") or [])
    }


def _voice_capture_validation_binding(session: Any) -> dict[str, Any]:
    value = session if isinstance(session, dict) else {}
    return {
        "schema": VOICE_CAPTURE_VALIDATION_BINDING_SCHEMA,
        "sessionId": str(value.get("sessionId") or ""),
        "state": str(value.get("state") or "idle"),
        "usesLocal": _voice_validation_uses_local(value),
    }


def _voice_capture_cleanup_failure_response(
    cleanup: dict[str, Any],
    *,
    session: dict[str, Any] | None = None,
) -> web.Response:
    payload: dict[str, Any] = {
        "ok": False,
        "error": "voice_capture_consent_cleanup_failed",
        "cleanup": dict(cleanup or {}),
        "consent": get_voice_capture_consent_manager().status(),
    }
    if isinstance(session, dict):
        payload["session"] = session
    return json_response(payload, status=503)


def _voice_capabilities_with_capture_consent(
    health: dict[str, Any],
) -> dict[str, Any]:
    capabilities = (
        dict(health.get("capabilities") or {}) if isinstance(health, dict) else {}
    )
    consent_manager = get_voice_capture_consent_manager()
    consent = consent_manager.status()
    return attach_voice_capture_consent(capabilities, consent)


def _voice_capture_local_bridge_snapshot(control: Any) -> dict[str, Any]:
    if not isinstance(control, dict):
        return {}
    bridge = control.get("localBridge")
    return dict(bridge) if isinstance(bridge, dict) else {}


def _voice_capture_mic_control_ack(control: Any, *, enabled: bool) -> bool:
    if (
        not isinstance(control, dict)
        or control.get("ok") is not True
        or control.get("applied") is not True
        or control.get("httpStatus") != 200
    ):
        return False
    request_state = control.get("request")
    ack = control.get("ack")
    bridge = control.get("localBridge")
    if (
        not isinstance(request_state, dict)
        or not isinstance(ack, dict)
        or not isinstance(bridge, dict)
    ):
        return False
    requested_revision = request_state.get("revision")
    action_id = request_state.get("actionId")
    observed_revision = bridge.get("micControlRevision")
    if (
        not isinstance(requested_revision, int)
        or isinstance(requested_revision, bool)
        or not isinstance(observed_revision, int)
        or isinstance(observed_revision, bool)
    ):
        return False
    bridge_digest = str(request_state.get("bridgeInstanceDigest") or "")
    if (
        requested_revision <= 0
        or not isinstance(action_id, str)
        or len(action_id) != 32
        or any(char not in "0123456789abcdef" for char in action_id)
        or request_state.get("enabled") is not enabled
        or len(bridge_digest) != 64
        or any(char not in "0123456789abcdef" for char in bridge_digest)
        or set(ack)
        != {
            "schema",
            "actionId",
            "requestRevision",
            "observedRevision",
            "enabled",
            "bridgeInstanceDigest",
            "state",
            "captureStopped",
        }
        or ack.get("schema") != VOICE_CAPTURE_MIC_CONTROL_ACK_SCHEMA
        or ack.get("actionId") != action_id
        or ack.get("requestRevision") != requested_revision
        or ack.get("observedRevision") != requested_revision
        or ack.get("enabled") is not enabled
        or ack.get("bridgeInstanceDigest") != bridge_digest
        or ack.get("state") != "applied"
        or ack.get("captureStopped") is not (not enabled)
        or observed_revision != requested_revision
        or bridge.get("micControlActionId") != action_id
        or bridge.get("micControlPendingRevision") != 0
        or str(bridge.get("micControlPendingActionId") or "")
        or bridge.get("micControlState") != "applied"
        or bridge.get("micControlDesiredEnabled") is not enabled
        or str(bridge.get("micControlError") or "")
        or bridge.get("stale") is not False
        or str(control.get("error") or "")
    ):
        return False
    mic = bridge.get("mic")
    if not isinstance(mic, dict):
        return False
    if enabled:
        return bool(
            bridge.get("enabled") is True
            and bridge.get("ready") is True
            and bridge.get("micEnabled") is True
            and bridge.get("micCaptureStopped") is False
            and mic.get("enabled") is True
            and mic.get("captureReady") is True
            and mic.get("captureStopped") is False
        )
    return bool(
        bridge.get("enabled") is True
        and bridge.get("micEnabled") is False
        and bridge.get("micCaptureStopped") is True
        and mic.get("enabled") is False
        and mic.get("captureReady") is False
        and mic.get("captureActive") is False
        and mic.get("captureStopped") is True
    )


def _voice_capture_mic_disabled_ack(control: Any) -> bool:
    return _voice_capture_mic_control_ack(control, enabled=False)


async def _await_voice_capture_task(task: asyncio.Task[Any]) -> Any:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancellation = exc
    if cancellation is not None:
        with contextlib.suppress(Exception):
            task.result()
        raise cancellation
    return task.result()


async def _run_voice_capture_manager_call(
    callback: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
    return await _await_voice_capture_task(task)


async def _publish_voice_capture_host_lease(manager: Any) -> dict[str, Any]:
    return await _run_voice_capture_manager_call(manager.publish_host_lease)


async def _shutdown_voice_capture_consent(
    app: web.Application,
    tasks: tuple[asyncio.Task[Any], ...],
) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await _revoke_voice_capture_consent(
        app,
        reason="control_page_shutdown",
    )


async def _revoke_voice_capture_consent_locked(
    app: web.Application,
    *,
    reason: str,
) -> dict[str, Any]:
    manager = get_voice_capture_consent_manager()
    state_write_failed = False
    current = manager.status()
    if current.get("recoveryRequired"):
        pending = {
            "ok": True,
            "controlRequired": True,
            "consent": current,
        }
    else:
        try:
            pending = await _run_voice_capture_manager_call(
                manager.begin_revoke,
                reason=reason,
            )
        except Exception as exc:
            state_write_failed = True
            print(
                "[CONTROL PAGE] voice_consent_revoke_state_write_failed "
                f"reason={reason} errorType={type(exc).__name__}"
            )
            current = manager.status()
            if current.get("recoveryRequired"):
                pending = {
                    "ok": False,
                    "controlRequired": True,
                    "consent": current,
                }
            else:
                try:
                    pending = await _run_voice_capture_manager_call(
                        manager.require_recovery,
                        reason=reason,
                        error="voice_capture_consent_state_write_failed",
                    )
                except Exception as recovery_exc:
                    print(
                        "[CONTROL PAGE] voice_consent_recovery_state_write_failed "
                        f"reason={reason} errorType={type(recovery_exc).__name__}"
                    )
                    pending = {
                        "ok": False,
                        "controlRequired": True,
                        "consent": manager.status(),
                    }
    try:
        await _publish_voice_capture_host_lease(manager)
    except Exception as exc:
        print(
            "[CONTROL PAGE] voice_consent_host_lease_write_failed "
            f"reason={reason} errorType={type(exc).__name__}"
        )
    if not pending.get("controlRequired"):
        return {"ok": True, "consent": manager.status(), "controlApplied": False}

    try:
        control = await request_local_bridge_mic_control(
            False,
            source=(
                f"voice_capture_consent:{reason}:state_error"
                if state_write_failed
                else f"voice_capture_consent:{reason}"
            ),
        )
    except asyncio.CancelledError:
        try:
            await _run_voice_capture_manager_call(
                manager.require_recovery,
                reason=reason,
                error="voice_capture_disable_cancelled",
            )
        except Exception:
            pass
        raise
    except Exception as exc:
        print(
            "[CONTROL PAGE] voice_consent_mic_off_failed "
            f"reason={reason} errorType={type(exc).__name__}"
        )
        control = {
            "ok": False,
            "applied": False,
            "error": "mic_control_failed",
        }

    bridge = _voice_capture_local_bridge_snapshot(control)
    applied = _voice_capture_mic_disabled_ack(control)
    try:
        completed = await _run_voice_capture_manager_call(
            manager.finish_revoke,
            applied=applied,
            error=str(control.get("error") or "mic_control_ack_invalid"),
        )
    except Exception as exc:
        print(
            "[CONTROL PAGE] voice_consent_revoke_finish_write_failed "
            f"reason={reason} errorType={type(exc).__name__}"
        )
        if not manager.status().get("recoveryRequired"):
            try:
                await _run_voice_capture_manager_call(
                    manager.require_recovery,
                    reason=reason,
                    error="voice_capture_consent_state_write_failed",
                )
            except Exception:
                pass
        return {
            "ok": False,
            "error": "voice_capture_consent_state_write_failed",
            "controlApplied": applied,
            "localBridge": bridge,
            "consent": manager.status(),
        }
    return {
        **completed,
        "controlApplied": applied,
        "localBridge": bridge,
    }


async def _revoke_voice_capture_consent(
    app: web.Application,
    *,
    reason: str,
) -> dict[str, Any]:
    lock = app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        return await _revoke_voice_capture_consent_locked(app, reason=reason)


async def _force_voice_capture_recovery_locked(
    app: web.Application,
    *,
    reason: str,
    error: str,
) -> dict[str, Any]:
    manager = get_voice_capture_consent_manager()
    try:
        await _run_voice_capture_manager_call(
            manager.require_recovery,
            reason=reason,
            error=error,
        )
    except Exception as exc:
        print(
            "[CONTROL PAGE] voice_consent_force_recovery_write_failed "
            f"reason={reason} errorType={type(exc).__name__}"
        )
    return await _revoke_voice_capture_consent_locked(app, reason=reason)


async def _force_voice_capture_recovery(
    app: web.Application,
    *,
    reason: str,
    error: str,
) -> dict[str, Any]:
    lock = app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        return await _force_voice_capture_recovery_locked(
            app,
            reason=reason,
            error=error,
        )


async def _reconcile_voice_capture_consent_locked(
    app: web.Application,
    *,
    validation_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manager = get_voice_capture_consent_manager()
    if validation_session is None:
        validation_session = get_voice_validation_manager().snapshot()
    reason = manager.revocation_reason(
        validation_session=validation_session,
        include_interrupted_enabling=True,
    )
    if reason:
        return await _revoke_voice_capture_consent_locked(app, reason=reason)
    return {"ok": True, "consent": manager.status(), "controlApplied": False}


async def _reconcile_voice_capture_consent(
    app: web.Application,
    *,
    validation_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lock = app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        return await _reconcile_voice_capture_consent_locked(
            app,
            validation_session=validation_session,
        )


async def _voice_capture_consent_context(app: web.Application):
    manager = get_voice_capture_consent_manager()

    async def monitor() -> None:
        while True:
            try:
                await _reconcile_voice_capture_consent(app)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A broken lease store must never leave capture on indefinitely.
                try:
                    await _force_voice_capture_recovery(
                        app,
                        reason="consent_monitor_error",
                        error=f"consent_monitor_{type(exc).__name__}",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as recovery_exc:
                    print(
                        "[CONTROL PAGE] voice_consent_monitor_recovery_failed "
                        f"errorType={type(recovery_exc).__name__}"
                    )
            await asyncio.sleep(VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC)

    async def heartbeat() -> None:
        while True:
            try:
                status = await _run_voice_capture_manager_call(manager.status)
                if status.get("captureMayBeActive"):
                    await _publish_voice_capture_host_lease(manager)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await _force_voice_capture_recovery(
                        app,
                        reason="consent_host_lease_write_failed",
                        error=f"consent_host_lease_{type(exc).__name__}",
                    )
            await asyncio.sleep(VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC)

    try:
        await _publish_voice_capture_host_lease(manager)
        await _reconcile_voice_capture_consent(app)
    except Exception as exc:
        try:
            await _force_voice_capture_recovery(
                app,
                reason="consent_startup_error",
                error=f"consent_startup_{type(exc).__name__}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as recovery_exc:
            print(
                "[CONTROL PAGE] voice_consent_startup_recovery_failed "
                f"errorType={type(recovery_exc).__name__}"
            )
    tasks = (
        asyncio.create_task(monitor(), name="voice-capture-consent-monitor"),
        asyncio.create_task(heartbeat(), name="voice-capture-consent-heartbeat"),
    )
    try:
        yield
    finally:
        cleanup_task = asyncio.create_task(
            _shutdown_voice_capture_consent(
                app,
                tasks,
            ),
            name="voice-capture-consent-shutdown-cleanup",
        )
        await _await_voice_capture_task(cleanup_task)


async def _voice_capture_owner_context(_: web.Application):
    owner_lock = MinecraftOwnerLock(
        get_runtime_artifacts_root()
        / "voice_capture_consent"
        / "owner_claim.lock"
    )
    try:
        owner_lock.acquire()
    except MinecraftOwnerLockBusy:
        raise RuntimeError("voice_capture_owner_conflict") from None
    except MinecraftOwnerLockUnavailable:
        raise RuntimeError("voice_capture_owner_lock_unavailable") from None
    try:
        yield
    finally:
        owner_lock.release()


async def voice_capture_consent_handler(request: web.Request) -> web.StreamResponse:
    result = await _reconcile_voice_capture_consent(request.app)
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
    manager = get_voice_capture_consent_manager()
    validation_manager = get_voice_validation_manager()
    lock = request.app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        validation = validation_manager.snapshot()
        cleanup = await _reconcile_voice_capture_consent_locked(
            request.app,
            validation_session=validation,
        )
        if not cleanup.get("ok"):
            return _voice_capture_cleanup_failure_response(
                cleanup,
                session=validation,
            )
        result = manager.preview(
            scope=str(
                (payload or {}).get("scope")
                or VOICE_CAPTURE_CONSENT_SCOPE
            ),
            validation_binding=_voice_capture_validation_binding(validation),
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
    validation_manager = get_voice_validation_manager()
    lock = request.app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        validation_before = validation_manager.snapshot()
        cleanup = await _reconcile_voice_capture_consent_locked(
            request.app,
            validation_session=validation_before,
        )
        if not cleanup.get("ok"):
            return _voice_capture_cleanup_failure_response(
                cleanup,
                session=validation_before,
            )
        try:
            started = await _run_voice_capture_manager_call(
                manager.begin_apply,
                confirm_token=str((payload or {}).get("confirmToken") or ""),
                scope=str(
                    (payload or {}).get("scope")
                    or VOICE_CAPTURE_CONSENT_SCOPE
                ),
                validation_binding=_voice_capture_validation_binding(
                    validation_before
                ),
            )
        except Exception as exc:
            print(
                "[CONTROL PAGE] voice_consent_begin_apply_write_failed "
                f"errorType={type(exc).__name__}"
            )
            return json_response(
                {
                    "ok": False,
                    "error": "voice_capture_consent_state_write_failed",
                    "consent": manager.status(),
                },
                status=503,
            )
        if not started.get("ok"):
            return json_response(started, status=409)
        lease_id = str(started.get("leaseId") or "")
        try:
            await _publish_voice_capture_host_lease(manager)
        except Exception as exc:
            print(
                "[CONTROL PAGE] voice_consent_enable_lease_write_failed "
                f"errorType={type(exc).__name__}"
            )
            cleanup = await _force_voice_capture_recovery_locked(
                request.app,
                reason="consent_host_lease_write_failed",
                error="voice_capture_consent_heartbeat_write_failed",
            )
            return json_response(
                {
                    "ok": False,
                    "error": "voice_capture_consent_heartbeat_write_failed",
                    "consent": manager.status(),
                    "cleanup": cleanup,
                },
                status=503,
            )
        bridge: dict[str, Any] = {}
        phase = "mic_enable"
        try:
            control = await request_local_bridge_mic_control(
                True,
                source="voice_capture_consent:validation",
            )
            bridge = _voice_capture_local_bridge_snapshot(control)
            applied = _voice_capture_mic_control_ack(control, enabled=True)
            phase = "active_commit"
            completed = await _run_voice_capture_manager_call(
                manager.finish_apply,
                lease_id=lease_id,
                applied=applied,
                capture_ready=applied,
                error=str(control.get("error") or bridge.get("lastError") or ""),
            )
            if not completed.get("ok"):
                cleanup = await _force_voice_capture_recovery_locked(
                    request.app,
                    reason="activation_failed",
                    error=str(completed.get("error") or "voice_capture_not_ready"),
                )
                return json_response(
                    {
                        **completed,
                        "consent": manager.status(),
                        "cleanup": cleanup,
                        "localBridge": (
                            _voice_capture_local_bridge_snapshot(cleanup) or bridge
                        ),
                    },
                    status=503,
                )

            phase = "post_activation"
            health = await cached_runtime_health(force=True)
            capabilities = _voice_capabilities_with_capture_consent(health)
            validation_manager = get_voice_validation_manager()
            validation = validation_manager.snapshot(capabilities=capabilities)
            if validation.get("state") in {"passed", "failed", "aborted"} and (
                _voice_validation_uses_local(validation)
            ):
                raise RuntimeError("voice_validation_terminal_during_activation")
            if validation.get("state") == "preflight" and _voice_validation_uses_local(
                validation
            ):
                resumed = validation_manager.resume_after_preflight(
                    capabilities=capabilities
                )
                if not resumed.get("ok"):
                    raise RuntimeError("voice_validation_resume_failed")
                validation = dict(resumed.get("session") or validation)
            if validation.get("state") == "running" and _voice_validation_uses_local(
                validation
            ):
                bound_session_id = str(validation.get("sessionId") or "")
                bound = await _run_voice_capture_manager_call(
                    manager.bind_validation_session,
                    bound_session_id,
                )
                if not bound.get("ok"):
                    raise RuntimeError("voice_capture_validation_bind_failed")
                validation = validation_manager.snapshot(capabilities=capabilities)
                if not (
                    validation.get("state") == "running"
                    and _voice_validation_uses_local(validation)
                    and str(validation.get("sessionId") or "")
                    == bound_session_id
                ):
                    raise RuntimeError(
                        "voice_validation_changed_during_activation"
                    )
            return json_response(
                {
                    "ok": True,
                    "consent": manager.status(),
                    "validationSession": validation,
                    "localBridge": bridge,
                }
            )
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(
                _force_voice_capture_recovery_locked(
                    request.app,
                    reason="activation_cancelled",
                    error="voice_capture_apply_cancelled",
                ),
                name="voice-capture-consent-cancel-cleanup",
            )
            try:
                await _await_voice_capture_task(cleanup_task)
            except Exception as cleanup_exc:
                print(
                    "[CONTROL PAGE] voice_consent_cancel_cleanup_failed "
                    f"errorType={type(cleanup_exc).__name__}"
                )
            raise
        except Exception as exc:
            print(
                "[CONTROL PAGE] voice_consent_apply_failed "
                f"phase={phase} errorType={type(exc).__name__}"
            )
            cleanup = await _force_voice_capture_recovery_locked(
                request.app,
                reason=(
                    "state_write_failed"
                    if phase == "active_commit"
                    else "activation_failed"
                ),
                error=(
                    "voice_capture_consent_state_write_failed"
                    if phase == "active_commit"
                    else "voice_capture_post_activation_failed"
                ),
            )
            error_code = (
                "voice_capture_consent_state_write_failed"
                if phase == "active_commit"
                else "voice_capture_consent_activation_failed"
            )
            return json_response(
                {
                    "ok": False,
                    "error": error_code,
                    "consent": manager.status(),
                    "cleanup": cleanup,
                    "controlApplied": cleanup.get("controlApplied") is True,
                    "localBridge": (
                        _voice_capture_local_bridge_snapshot(cleanup) or bridge
                    ),
                },
                status=503,
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
    health = await cached_runtime_health(force=True)
    lock = request.app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        session = validation_manager.snapshot()
        cleanup = await _reconcile_voice_capture_consent_locked(
            request.app,
            validation_session=session,
        )
        session["capabilities"] = _voice_capabilities_with_capture_consent(health)
        if not cleanup.get("ok"):
            return _voice_capture_cleanup_failure_response(
                cleanup,
                session=session,
            )
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
    lock = request.app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        current_validation = validation_manager.snapshot()
        cleanup = await _reconcile_voice_capture_consent_locked(
            request.app,
            validation_session=current_validation,
        )
        if not cleanup.get("ok"):
            return _voice_capture_cleanup_failure_response(
                cleanup,
                session=current_validation,
            )
    raw_health = await CONTROL_PAGE_RUNTIME_HEALTH_CACHE.get(force=True)
    health = public_runtime_health_snapshot(raw_health)
    capabilities = _voice_capabilities_with_capture_consent(health)
    normalized_requested_surfaces = {
        str(item or "").strip().lower() for item in surfaces
    }
    discord_target = None
    if suite == SUITE_ID and "discord" in normalized_requested_surfaces:
        target_resolution = resolve_discord_validation_target(raw_health)
        if not target_resolution.get("ok") and current_validation.get("state") in {
            "idle",
            "passed",
            "failed",
            "aborted",
        }:
            return json_response(target_resolution, status=409)
        discord_target = target_resolution.get("discordTarget")
    start_args = {
        "suite": suite,
        "surfaces": [str(item) for item in surfaces],
        "capabilities": capabilities,
        "discord_target": discord_target,
    }
    if "local" in normalized_requested_surfaces:
        manager = get_voice_capture_consent_manager()
        async with lock:
            current_validation = validation_manager.snapshot()
            cleanup = await _reconcile_voice_capture_consent_locked(
                request.app,
                validation_session=current_validation,
            )
            if not cleanup.get("ok"):
                return _voice_capture_cleanup_failure_response(
                    cleanup,
                    session=current_validation,
                )
            capabilities = _voice_capabilities_with_capture_consent(health)
            start_args["capabilities"] = capabilities
            try:
                result = validation_manager.start(**start_args)
            except Exception as exc:
                print(
                    "[CONTROL PAGE] local_voice_validation_start_failed "
                    f"errorType={type(exc).__name__}"
                )
                cleanup = await _force_voice_capture_recovery_locked(
                    request.app,
                    reason="validation_start_failed",
                    error="voice_validation_start_failed",
                )
                return json_response(
                    {
                        "ok": False,
                        "error": "voice_validation_start_failed",
                        "cleanup": cleanup,
                        "consent": manager.status(),
                    },
                    status=503,
                )
            session = dict(result.get("session") or {})
            if (
                result.get("ok")
                and session.get("state") == "running"
                and _voice_validation_uses_local(session)
            ):
                try:
                    bound = await _run_voice_capture_manager_call(
                        manager.bind_validation_session,
                        str(session.get("sessionId") or ""),
                    )
                except Exception:
                    bound = {
                        "ok": False,
                        "error": "voice_capture_validation_bind_failed",
                    }
                if not bound.get("ok"):
                    aborted_session = session
                    try:
                        aborted = validation_manager.abort(
                            session_id=str(session.get("sessionId") or "")
                        )
                        aborted_session = dict(
                            aborted.get("session") or aborted_session
                        )
                    except Exception as exc:
                        print(
                            "[CONTROL PAGE] local_voice_validation_abort_failed "
                            f"errorType={type(exc).__name__}"
                        )
                    cleanup = await _force_voice_capture_recovery_locked(
                        request.app,
                        reason="validation_bind_failed",
                        error="voice_capture_validation_bind_failed",
                    )
                    return json_response(
                        {
                            "ok": False,
                            "error": "voice_capture_validation_bind_failed",
                            "session": aborted_session,
                            "cleanup": cleanup,
                            "consent": manager.status(),
                        },
                        status=503,
                    )
                post_bind_session = validation_manager.snapshot(
                    capabilities=capabilities
                )
                if not (
                    post_bind_session.get("state") == "running"
                    and _voice_validation_uses_local(post_bind_session)
                    and str(post_bind_session.get("sessionId") or "")
                    == str(session.get("sessionId") or "")
                ):
                    cleanup = await _force_voice_capture_recovery_locked(
                        request.app,
                        reason="validation_changed_during_bind",
                        error="voice_capture_validation_changed_during_bind",
                    )
                    return json_response(
                        {
                            "ok": False,
                            "error": "voice_capture_validation_changed_during_bind",
                            "session": post_bind_session,
                            "cleanup": cleanup,
                            "consent": manager.status(),
                        },
                        status=503,
                    )
                result = {**result, "session": post_bind_session}
    else:
        async with lock:
            current_validation = validation_manager.snapshot()
            cleanup = await _reconcile_voice_capture_consent_locked(
                request.app,
                validation_session=current_validation,
            )
            if not cleanup.get("ok"):
                return _voice_capture_cleanup_failure_response(
                    cleanup,
                    session=current_validation,
                )
            result = validation_manager.start(**start_args)
            session = dict(result.get("session") or validation_manager.snapshot())
            cleanup = await _reconcile_voice_capture_consent_locked(
                request.app,
                validation_session=session,
            )
            if not cleanup.get("ok"):
                return _voice_capture_cleanup_failure_response(
                    cleanup,
                    session=session,
                )
    status = (
        201
        if result.get("ok")
        else 503
        if result.get("error") == "validation_attempt_lease_unavailable"
        else 409
        if result.get("error")
        in {
            "validation_session_active",
            "discord_target_unavailable",
            "ambiguous_discord_target",
            "validation_attempt_inflight",
        }
        else 400
    )
    return json_response(result, status=status)


async def _voice_validation_mutation_response(
    request: web.Request,
    *,
    operation: str,
    mutation: Callable[[], dict[str, Any]],
) -> web.StreamResponse:
    validation_manager = get_voice_validation_manager()
    lock = request.app[VOICE_CAPTURE_CONSENT_LOCK_KEY]
    async with lock:
        try:
            result = mutation()
            session = dict(
                result.get("session") or validation_manager.snapshot()
            )
        except Exception as exc:
            print(
                "[CONTROL PAGE] voice_validation_mutation_failed "
                f"operation={operation} errorType={type(exc).__name__}"
            )
            cleanup = await _force_voice_capture_recovery_locked(
                request.app,
                reason="validation_mutation_failed",
                error="voice_validation_mutation_failed",
            )
            return json_response(
                {
                    "ok": False,
                    "error": "voice_validation_mutation_failed",
                    "cleanup": cleanup,
                    "consent": get_voice_capture_consent_manager().status(),
                },
                status=503,
            )
        error_code = str(result.get("error") or "")
        if operation == "abort" and error_code in {
            "validation_attempt_inflight",
            "validation_attempt_lease_unavailable",
        }:
            # The in-flight Bot turn owns the attempt transition. Do not
            # rotate its binding, but honor the user's safety intent by
            # revoking host capture immediately.
            cleanup = await _force_voice_capture_recovery_locked(
                request.app,
                reason="validation_abort_deferred",
                error=error_code,
            )
            result = {**result, "cleanup": cleanup}
        else:
            cleanup = await _reconcile_voice_capture_consent_locked(
                request.app,
                validation_session=session,
            )
        if not cleanup.get("ok"):
            return _voice_capture_cleanup_failure_response(
                cleanup,
                session=session,
            )
    status = 200 if result.get("ok") else (
        503
        if result.get("error") == "validation_attempt_lease_unavailable"
        else 409
    )
    return json_response(result, status=status)


async def voice_validation_confirm_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict) or type(payload.get("heard")) is not bool:
        return json_response(
            {"ok": False, "error": "heard_boolean_required"},
            status=400,
        )
    validation_manager = get_voice_validation_manager()
    confirm_args: dict[str, Any] = {
        "session_id": str(payload.get("sessionId") or ""),
        "step_id": str(payload.get("stepId") or ""),
        "heard": payload["heard"],
    }
    if "attempt" in payload:
        confirm_args["attempt"] = payload["attempt"]
    return await _voice_validation_mutation_response(
        request,
        operation="confirm",
        mutation=lambda: validation_manager.confirm(**confirm_args),
    )


async def voice_validation_retry_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    validation_manager = get_voice_validation_manager()
    return await _voice_validation_mutation_response(
        request,
        operation="retry",
        mutation=lambda: validation_manager.retry(
            session_id=str((payload or {}).get("sessionId") or ""),
            step_id=str((payload or {}).get("stepId") or ""),
            attempt=(payload or {}).get("attempt"),
        ),
    )


async def voice_validation_abort_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    validation_manager = get_voice_validation_manager()
    return await _voice_validation_mutation_response(
        request,
        operation="abort",
        mutation=lambda: validation_manager.abort(
            session_id=str((payload or {}).get("sessionId") or ""),
        ),
    )


async def _strict_autonomy_validation_payload(
    request: web.Request,
    *,
    allowed_fields: frozenset[str],
) -> tuple[dict[str, Any] | None, web.Response | None]:
    try:
        payload = await request.json()
    except Exception:
        return None, json_response(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )
    if not isinstance(payload, dict):
        return None, json_response(
            {"ok": False, "error": "json_object_required"},
            status=400,
        )
    if not set(payload).issubset(allowed_fields):
        return None, json_response(
            {"ok": False, "error": "invalid_request_fields"},
            status=400,
        )
    return payload, None


def _autonomy_validation_id(value: Any, *, max_length: int) -> str | None:
    if type(value) is not str:
        return None
    if not value or value != value.strip() or len(value) > max_length:
        return None
    return value


def _autonomy_validation_attempt(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _autonomy_validation_guild_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 < value <= (2**64 - 1) else None
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 20
        and value[0] in "123456789"
        and value.isascii()
        and value.isdecimal()
    ):
        parsed = int(value)
        return parsed if parsed <= (2**64 - 1) else None
    return None


async def autonomy_validation_handler(_: web.Request) -> web.StreamResponse:
    session = get_autonomy_validation_manager().snapshot()
    return json_response({"ok": True, "session": session})


async def autonomy_validation_start_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _strict_autonomy_validation_payload(
        request,
        allowed_fields=frozenset({"suite", "guildId", "dryRun"}),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    if payload.get("dryRun") is False:
        return json_response(
            {
                "ok": False,
                "error": "autonomy_execution_not_enabled",
                "dryRun": True,
                "dryRunOnly": True,
            },
            status=409,
        )
    if payload.get("dryRun") is not True:
        return json_response(
            {"ok": False, "error": "dry_run_required"},
            status=400,
        )
    if payload.get("suite") != AUTONOMY_VALIDATION_SUITE_ID:
        return json_response(
            {"ok": False, "error": "unsupported_suite"},
            status=400,
        )
    guild_id = _autonomy_validation_guild_id(payload.get("guildId"))
    if guild_id is None:
        return json_response(
            {"ok": False, "error": "guild_id_positive_required"},
            status=400,
        )
    result = get_autonomy_validation_manager().start(
        suite=AUTONOMY_VALIDATION_SUITE_ID,
        guild_id=guild_id,
        dry_run=True,
    )
    return json_response(result, status=201 if result.get("ok") else 409)


async def autonomy_validation_confirm_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _strict_autonomy_validation_payload(
        request,
        allowed_fields=frozenset(
            {"sessionId", "stepId", "attempt", "userConfirmed"}
        ),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    session_id = _autonomy_validation_id(
        payload.get("sessionId"),
        max_length=128,
    )
    if session_id is None:
        return json_response(
            {"ok": False, "error": "session_id_required"},
            status=400,
        )
    step_id = _autonomy_validation_id(payload.get("stepId"), max_length=128)
    if step_id is None:
        return json_response(
            {"ok": False, "error": "step_id_required"},
            status=400,
        )
    attempt = _autonomy_validation_attempt(payload.get("attempt"))
    if attempt is None:
        return json_response(
            {"ok": False, "error": "attempt_positive_required"},
            status=400,
        )
    if payload.get("userConfirmed") is not True:
        return json_response(
            {"ok": False, "error": "user_confirmation_required"},
            status=400,
        )
    result = get_autonomy_validation_manager().confirm(
        session_id=session_id,
        step_id=step_id,
        attempt=attempt,
        acknowledged=True,
    )
    return json_response(result, status=200 if result.get("ok") else 409)


async def autonomy_validation_retry_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _strict_autonomy_validation_payload(
        request,
        allowed_fields=frozenset({"sessionId", "stepId", "attempt"}),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    session_id = _autonomy_validation_id(
        payload.get("sessionId"),
        max_length=128,
    )
    if session_id is None:
        return json_response(
            {"ok": False, "error": "session_id_required"},
            status=400,
        )
    step_id = _autonomy_validation_id(payload.get("stepId"), max_length=128)
    if step_id is None:
        return json_response(
            {"ok": False, "error": "step_id_required"},
            status=400,
        )
    attempt = _autonomy_validation_attempt(payload.get("attempt"))
    if attempt is None:
        return json_response(
            {"ok": False, "error": "attempt_positive_required"},
            status=400,
        )
    result = get_autonomy_validation_manager().retry(
        session_id=session_id,
        step_id=step_id,
        attempt=attempt,
    )
    return json_response(result, status=200 if result.get("ok") else 409)


async def autonomy_validation_abort_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _strict_autonomy_validation_payload(
        request,
        allowed_fields=frozenset({"sessionId"}),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    session_id = _autonomy_validation_id(
        payload.get("sessionId"),
        max_length=128,
    )
    if session_id is None:
        return json_response(
            {"ok": False, "error": "session_id_required"},
            status=400,
        )
    result = get_autonomy_validation_manager().abort(session_id=session_id)
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
        subprocess.Popen(
            ["explorer.exe", str(path)],
            env=voice_capture_auth_scrubbed_environment(),
        )
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen(
        [opener, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=voice_capture_auth_scrubbed_environment(),
    )


def open_url_with_system(url: str) -> None:
    if os.name == "nt":
        subprocess.Popen(
            ["explorer.exe", url],
            env=voice_capture_auth_scrubbed_environment(),
        )
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen(
        [opener, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=voice_capture_auth_scrubbed_environment(),
    )


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
        print(
            "[CONTROL PAGE] obsidian_protocol_open_failed "
            f"errorType={type(exc).__name__}"
        )
        try:
            open_path_with_system(vault)
            return {
                "ok": True,
                "message": "Obsidian protocol failed, so the vault folder was opened instead.",
                "vaultPath": str(vault),
                "url": obsidian_url,
                "fallback": "folder",
            }
        except Exception as fallback_exc:
            print(
                "[CONTROL PAGE] memory_vault_open_failed "
                f"errorType={type(fallback_exc).__name__}"
            )
            return {
                "ok": False,
                "error": "open_memory_vault_failed",
                "message": public_failure_message(
                    "open_memory_vault_failed"
                ),
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


async def _run_memory_mutation_with_bounded_admission(
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    deadline = time.monotonic() + MEMORY_MUTATION_ADMISSION_TIMEOUT_SEC
    cancelled = False
    state_lock = threading.Lock()
    admission_busy = object()

    def attempt() -> dict[str, Any] | object:
        nonlocal cancelled
        entered = False
        if time.monotonic() >= deadline:
            return admission_busy
        try:
            with memory_deletion_journal_guard(
                memory_index_dir(),
                require_stable=False,
            ):
                with state_lock:
                    if cancelled or time.monotonic() >= deadline:
                        return admission_busy
                    entered = True
                return operation()
        except MemoryDeletionJournalBusyError:
            if entered:
                raise
            return admission_busy

    while True:
        worker = asyncio.create_task(asyncio.to_thread(attempt))
        cancellation: asyncio.CancelledError | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as exc:
                with state_lock:
                    cancelled = True
                cancellation = exc
        result = worker.result()
        if cancellation is not None:
            raise cancellation
        if result is not admission_busy:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MemoryDeletionJournalBusyError()
        await asyncio.sleep(
            min(MEMORY_MUTATION_ADMISSION_RETRY_SEC, remaining)
        )


async def memory_note_action_handler(request: web.Request) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    action = str((payload or {}).get("action") or "").strip()
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: update_memory_vault_user_note(
            note_id,
            action,
            title=(payload or {}).get("title"),
            body=(payload or {}).get("body"),
            expected_content_hash=(payload or {}).get(
                "expectedContentHash"
            ),
        )
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
        "memory_confirmation_content_hidden",
        "memory_note_changed_since_read",
        "memory_note_integrity_invalid",
        "memory_note_quarantined",
    }:
        return 409
    if error in {
        "memory_edit_failed",
        "memory_confirmation_write_failed",
    }:
        return 500
    if error in {
        "memory_edit_cleanup_required",
        MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    }:
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
    if error in {
        "memory_delete_cleanup_required",
        MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    }:
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
    if error in {
        "memory_provenance_backfill_cleanup_required",
        MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    }:
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
    if error in {
        "memory_provenance_correction_cleanup_required",
        MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    }:
        return 503
    if (
        error
        in {
            (
                "memory_provenance_correction_"
                "journal_integrity_failed"
            ),
            "memory_provenance_correction_journal_unreadable",
            "memory_provenance_correction_writer_marker_unavailable",
            "memory_provenance_correction_writer_unavailable",
        }
        or error.startswith("memory_provenance_correction_auth_")
        or error.startswith("memory_provenance_correction_anchor_")
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
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: preview_memory_provenance_backfill_application(
            note_id,
            [str(item) for item in source_note_ids],
        )
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
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: apply_memory_provenance_backfill(
            note_id,
            str((payload or {}).get("confirmToken") or ""),
        )
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
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: preview_memory_provenance_backfill_application(
            note_id,
            [str(item) for item in source_note_ids],
            selection_mode="user_selected",
        )
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
    result = memory_provenance_correction_overview()
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
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
    note_id = request.match_info.get("note_id", "")
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
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: preview_memory_provenance_correction(
            note_id,
            source_note_ids,
        )
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: apply_memory_provenance_correction(
            note_id,
            str((payload or {}).get("confirmToken") or ""),
        )
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_undo_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: preview_memory_provenance_correction_undo(
            note_id,
            str((payload or {}).get("changeId") or ""),
        )
    )
    return json_response(
        result,
        status=memory_provenance_correction_status(result),
    )


async def memory_provenance_correction_undo_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: apply_memory_provenance_correction_undo(
            note_id,
            str((payload or {}).get("confirmToken") or ""),
        )
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
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: preview_memory_vault_user_note_deletion(
            note_id,
            reason=str(
                (payload or {}).get("reason") or "user_requested"
            ),
        )
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
    result = await _run_memory_mutation_with_bounded_admission(
        lambda: delete_memory_vault_user_note(
            note_id,
            str((payload or {}).get("confirmToken") or ""),
            reason=str(
                (payload or {}).get("reason") or "user_requested"
            ),
        )
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
    if not isinstance(payload, dict):
        payload = {}
    requested_source = str(payload.get("source") or "").strip().lower()
    reserved_voice_fields = {
        "admissionToken",
        "bridgeInstanceId",
        "validation",
        "validationBinding",
    }
    if (
        requested_source not in {"", "control_page"}
        or any(field in payload for field in reserved_voice_fields)
    ):
        return json_response(
            {"ok": False, "error": "unsupported_chat_source"},
            status=400,
        )
    payload = {**payload, "source": "control_page"}
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
                "reply": public_failure_message("local_restart_failed"),
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
                "reply": public_failure_message("local_shutdown_failed"),
                "state": state,
            },
            status=500,
        )
    proxied = await proxy_json(request, "POST", "/api/control-page/chat", body=payload)
    if proxied is not None:
        return proxied
    state = await degraded_state(proxy_failure=last_proxy_failure(request))
    return json_response(
        {
            "ok": False,
            "error": "bot_api_unavailable",
            "reply": state.get("statusText") or "Control-Page is live, but Bot API is unavailable.",
            "state": state,
        },
        status=503,
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


def create_app(*, manage_voice_capture_consent: bool = True) -> web.Application:
    app = web.Application(middlewares=[control_page_cors_middleware])
    app[VOICE_CAPTURE_CONSENT_LOCK_KEY] = asyncio.Lock()
    if manage_voice_capture_consent:
        app.cleanup_ctx.append(_voice_capture_owner_context)
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
    app.router.add_post("/api/control-page/discord-mode/preview", discord_mode_preview_handler)
    app.router.add_post("/api/control-page/discord-mode/apply", discord_mode_apply_handler)
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
        "/api/control-page/autonomy-validation",
        autonomy_validation_handler,
    )
    app.router.add_post(
        "/api/control-page/autonomy-validation/start",
        autonomy_validation_start_handler,
    )
    app.router.add_post(
        "/api/control-page/autonomy-validation/confirm",
        autonomy_validation_confirm_handler,
    )
    app.router.add_post(
        "/api/control-page/autonomy-validation/retry",
        autonomy_validation_retry_handler,
    )
    app.router.add_post(
        "/api/control-page/autonomy-validation/abort",
        autonomy_validation_abort_handler,
    )
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
        "/api/control-page/autonomy-validation/start",
        autonomy_validation_start_handler,
    )
    app.router.add_options(
        "/api/control-page/autonomy-validation/confirm",
        autonomy_validation_confirm_handler,
    )
    app.router.add_options(
        "/api/control-page/autonomy-validation/retry",
        autonomy_validation_retry_handler,
    )
    app.router.add_options(
        "/api/control-page/autonomy-validation/abort",
        autonomy_validation_abort_handler,
    )
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
