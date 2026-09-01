from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from aiohttp import ClientConnectorError, ClientError, ClientSession, ClientTimeout, web

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
from .control_page_http import (
    add_control_page_no_store_headers,
    control_page_cors_middleware,
    control_page_session_handler,
    normalize_request_origin,
    origin_uses_loopback_host,
    request_control_page_host_is_allowed,
    request_control_page_origin,
    request_control_page_self_origin,
)
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
    HOST_LEASE_STALE_SEC as VOICE_CAPTURE_HOST_LEASE_STALE_SEC,
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
from .workspace_task_tools import (
    WORKSPACE_EDIT_MAX_PREVIEW_BYTES,
    WorkspaceMutationHostClient,
)


PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"

HOST = os.getenv("CONTROL_PAGE_HOST", "127.0.0.1")
PORT = int(os.getenv("CONTROL_PAGE_PUBLIC_PORT", os.getenv("CONTROL_PAGE_PORT", "8799")))
BOT_API_HOST = os.getenv("CONTROL_PAGE_BOT_API_HOST", "127.0.0.1")
BOT_API_PORT = int(os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798"))
BOT_API_BASE = f"http://{BOT_API_HOST}:{BOT_API_PORT}"
CONVERSATION_ARCHIVE_ENABLED = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_ENABLED", "false"
).lower() in {"1", "true", "yes", "on"}
CONTROL_PAGE_TLS_CERT_FILE = os.getenv("CONTROL_PAGE_TLS_CERT_FILE", "").strip()
CONTROL_PAGE_TLS_KEY_FILE = os.getenv("CONTROL_PAGE_TLS_KEY_FILE", "").strip()
CONVERSATION_ARCHIVE_PROXY_KEY_FILE = os.getenv(
    "EVELYN_CONVERSATION_ARCHIVE_PROXY_KEY_FILE", ""
).strip()
CONVERSATION_ARCHIVE_CONTROL_PAGE_ORIGIN = normalize_request_origin(
    os.getenv(
        "EVELYN_CONVERSATION_ARCHIVE_CONTROL_PAGE_ORIGIN",
        "https://127.0.0.1:8800",
    )
)
CONVERSATION_ARCHIVE_ADMIN_COOKIE = "__Host-evelyn_archive_admin"
CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX = (
    "/api/control-page/conversation-archive"
)
CONVERSATION_ARCHIVE_ADMIN_UPSTREAM_PREFIX = (
    "/internal/conversation-archive/admin"
)
CONVERSATION_ARCHIVE_ADMIN_MAX_REQUEST_BYTES = 256 * 1024
CONVERSATION_ARCHIVE_ADMIN_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CONVERSATION_ARCHIVE_COOKIE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_CONVERSATION_ARCHIVE_PUBLIC_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_CONVERSATION_ARCHIVE_PROXY_KEY_DOMAIN = (
    b"evelyn.private-conversation-archive.transport-key.v1\ncontrol-proxy"
)
_CONVERSATION_ARCHIVE_PROXY_PURPOSE = "control-proxy"
_CONVERSATION_ARCHIVE_TIMESTAMP_HEADER = "X-Evelyn-Archive-Timestamp"
_CONVERSATION_ARCHIVE_NONCE_HEADER = "X-Evelyn-Archive-Nonce"
_CONVERSATION_ARCHIVE_SIGNATURE_HEADER = "X-Evelyn-Archive-Signature"
_CONVERSATION_ARCHIVE_CONTROL_SCHEME_HEADER = (
    "X-Evelyn-Archive-Control-Scheme"
)
_CONVERSATION_ARCHIVE_CONTROL_HOST_HEADER = "X-Evelyn-Archive-Control-Host"
_CONVERSATION_ARCHIVE_CONTROL_ORIGIN_HEADER = (
    "X-Evelyn-Archive-Control-Origin"
)
EVELYN_INTERNAL_CONTROL_HEADER = "X-Evelyn-Internal-Control-Token"
EVELYN_INTERNAL_CONTROL_TOKEN = os.getenv(
    "EVELYN_INTERNAL_CONTROL_TOKEN",
    "",
).strip()
EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN = os.getenv(
    "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN",
    "",
).strip()
PROXY_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_PROXY_TIMEOUT_SEC", "6.0"))
TASK_APPROVAL_MUTATION_TIMEOUT_SEC = min(
    20.0,
    max(1.0, float(os.getenv("TASK_APPROVAL_MUTATION_TIMEOUT_SEC", "20.0"))),
)
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
VOICE_CAPTURE_POST_ACTIVATION_TIMEOUT_SEC = 10.0
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
    headers = {
        EVELYN_INTERNAL_CONTROL_HEADER: EVELYN_INTERNAL_CONTROL_TOKEN,
    }
    try:
        async with ClientSession(timeout=timeout) as session:
            if method == "POST":
                async with session.post(
                    url,
                    json=body,
                    headers=headers,
                ) as response:
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
            async with session.get(url, headers=headers) as response:
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


def _conversation_archive_public_error(
    error: str,
    *,
    status: int,
) -> web.Response:
    return add_control_page_no_store_headers(
        json_response(
            {"ok": False, "error": error},
            status=status,
        )
    )


def _conversation_archive_request_uses_admin_origin(request: Any) -> bool:
    self_origin = request_control_page_self_origin(request)
    return bool(
        CONVERSATION_ARCHIVE_CONTROL_PAGE_ORIGIN
        and hmac.compare_digest(
            self_origin,
            CONVERSATION_ARCHIVE_CONTROL_PAGE_ORIGIN,
        )
    )


def _conversation_archive_admin_origin_path_allowed(path: str) -> bool:
    return path in {
        "/archive/admin",
        "/api/control-page/session",
        "/assets/evelyn-conversation-archive-admin.css",
        "/assets/evelyn-conversation-archive-admin.js",
    } or path.startswith(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/"
    )


@web.middleware
async def _conversation_archive_admin_origin_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Any],
) -> web.StreamResponse:
    if (
        _conversation_archive_request_uses_admin_origin(request)
        and not _conversation_archive_admin_origin_path_allowed(request.path)
    ):
        raise web.HTTPNotFound(text="control page resource not found")
    return await handler(request)


def _conversation_archive_request_is_secure(request: Any) -> bool:
    self_origin = request_control_page_self_origin(request)
    browser_origin = request_control_page_origin(request)
    origin_required = str(getattr(request, "method", "GET")).upper() != "GET"
    return bool(
        _conversation_archive_request_uses_admin_origin(request)
        and str(getattr(request, "scheme", "")).lower() == "https"
        and request_control_page_host_is_allowed(request)
        and origin_uses_loopback_host(self_origin)
        and (
            hmac.compare_digest(browser_origin, self_origin)
            if browser_origin
            else not origin_required
        )
    )


def _conversation_archive_proxy_key() -> bytes:
    path = Path(CONVERSATION_ARCHIVE_PROXY_KEY_FILE)
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError
        with path.open("rb") as handle:
            key = handle.read(4097)
    except OSError:
        raise ValueError("archive_proxy_key_unavailable") from None
    if len(key) < 32 or len(key) > 4096:
        raise ValueError("archive_proxy_key_invalid")
    return hmac.new(
        key,
        _CONVERSATION_ARCHIVE_PROXY_KEY_DOMAIN,
        hashlib.sha256,
    ).digest()


def _conversation_archive_proxy_headers(
    request: web.Request,
    *,
    method: str,
    upstream_path: str,
    body: bytes,
) -> dict[str, str]:
    self_origin = request_control_page_self_origin(request)
    scheme = urlsplit(self_origin).scheme
    host = urlsplit(self_origin).netloc
    origin = request_control_page_origin(request) or self_origin
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    canonical = "\n".join(
        (
            _CONVERSATION_ARCHIVE_PROXY_PURPOSE,
            method.upper(),
            upstream_path,
            timestamp,
            nonce,
            hashlib.sha256(body).hexdigest(),
            scheme,
            host,
            origin,
        )
    ).encode("utf-8")
    return {
        "Accept": "application/json",
        "Cache-Control": "no-store",
        _CONVERSATION_ARCHIVE_TIMESTAMP_HEADER: timestamp,
        _CONVERSATION_ARCHIVE_NONCE_HEADER: nonce,
        _CONVERSATION_ARCHIVE_SIGNATURE_HEADER: hmac.new(
            _conversation_archive_proxy_key(),
            canonical,
            hashlib.sha256,
        ).hexdigest(),
        _CONVERSATION_ARCHIVE_CONTROL_SCHEME_HEADER: scheme,
        _CONVERSATION_ARCHIVE_CONTROL_HOST_HEADER: host,
        _CONVERSATION_ARCHIVE_CONTROL_ORIGIN_HEADER: origin,
    }


def _conversation_archive_request_cookie(request: web.Request) -> str:
    value = str(request.cookies.get(CONVERSATION_ARCHIVE_ADMIN_COOKIE) or "")
    return value if _CONVERSATION_ARCHIVE_COOKIE_RE.fullmatch(value) else ""


def _conversation_archive_response_cookie(
    headers: Any,
) -> tuple[str, bool] | None:
    matches: list[tuple[str, bool]] = []
    for raw_header in headers.getall("Set-Cookie", []):
        cookie = SimpleCookie()
        try:
            cookie.load(raw_header)
        except Exception as exc:
            if CONVERSATION_ARCHIVE_ADMIN_COOKIE in raw_header:
                raise ValueError("archive_admin_cookie_invalid") from exc
            continue
        if CONVERSATION_ARCHIVE_ADMIN_COOKIE not in cookie:
            if CONVERSATION_ARCHIVE_ADMIN_COOKIE in raw_header:
                raise ValueError("archive_admin_cookie_invalid")
            continue
        if set(cookie) != {CONVERSATION_ARCHIVE_ADMIN_COOKIE}:
            raise ValueError("archive_admin_cookie_invalid")
        morsel = cookie[CONVERSATION_ARCHIVE_ADMIN_COOKIE]
        value = morsel.value
        delete_cookie = morsel["max-age"] == "0" and value == ""
        if (
            (not delete_cookie and not _CONVERSATION_ARCHIVE_COOKIE_RE.fullmatch(value))
            or not morsel["secure"]
            or not morsel["httponly"]
            or str(morsel["samesite"]).lower() != "strict"
            or morsel["path"] != "/"
            or morsel["domain"]
            or morsel["max-age"] not in {"", "0"}
            or (morsel["max-age"] == "0" and not delete_cookie)
        ):
            raise ValueError("archive_admin_cookie_invalid")
        normalized = (
            f"{CONVERSATION_ARCHIVE_ADMIN_COOKIE}={value}; "
            "Secure; HttpOnly; SameSite=Strict; Path=/"
        )
        if delete_cookie:
            normalized += "; Max-Age=0"
        matches.append((normalized, delete_cookie))
    if len(matches) > 1:
        raise ValueError("archive_admin_cookie_invalid")
    return matches[0] if matches else None


def _conversation_archive_response_exposes_auth_secret(
    payload: Any,
) -> bool:
    forbidden = {"code", "cookie", "otp", "sessiontoken", "token"}
    if isinstance(payload, dict):
        if any(str(key).replace("_", "").lower() in forbidden for key in payload):
            return True
        return any(
            _conversation_archive_response_exposes_auth_secret(value)
            for value in payload.values()
        )
    if isinstance(payload, list):
        return any(
            _conversation_archive_response_exposes_auth_secret(value)
            for value in payload
        )
    return False


def _conversation_archive_public_upstream_error(
    payload: dict[str, Any],
) -> dict[str, Any]:
    projected: dict[str, Any] = {"ok": False}
    for key in ("schema", "error", "state"):
        value = payload.get(key)
        if isinstance(value, str) and _CONVERSATION_ARCHIVE_PUBLIC_CODE_RE.fullmatch(
            value
        ):
            projected[key] = value
    if type(payload.get("retryable")) is bool:
        projected["retryable"] = payload["retryable"]
    retry_after = payload.get("retryAfterSec")
    if type(retry_after) is int and 0 < retry_after <= 86400:
        projected["retryAfterSec"] = retry_after
    if len(projected) == 1:
        projected["error"] = "conversation_archive_request_failed"
    return projected


def _conversation_archive_feedback_workflow(value: Any) -> None:
    required = {
        "schema",
        "workflowId",
        "state",
        "category",
        "route",
        "actionable",
        "versionId",
        "activeVersionId",
        "deletionStates",
        "contentFree",
    }
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(required),
        frozenset(required | {"sourceRecordId"}),
    }:
        raise ValueError("archive_admin_response_invalid")
    identifiers = ("workflowId", "activeVersionId")
    if any(
        not isinstance(value.get(key), str)
        or not 1 <= len(value[key]) <= 128
        or re.fullmatch(r"[A-Za-z0-9_.:-]+", value[key]) is None
        for key in identifiers
    ):
        raise ValueError("archive_admin_response_invalid")
    for key in ("versionId", "sourceRecordId"):
        item = value.get(key)
        if item is not None and (
            not isinstance(item, str)
            or not 1 <= len(item) <= 128
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", item) is None
        ):
            raise ValueError("archive_admin_response_invalid")
    category = value.get("category")
    if category is not None and category not in {
        "answer_quality",
        "context_selection",
        "task_routing",
        "tone_identity",
        "tool_failure",
        "permission_safety",
    }:
        raise ValueError("archive_admin_response_invalid")
    if (
        value.get("schema") != "evelyn.feedback-workflow-public.v1"
        or not isinstance(value.get("state"), str)
        or _CONVERSATION_ARCHIVE_PUBLIC_CODE_RE.fullmatch(value["state"]) is None
        or not isinstance(value.get("route"), str)
        or _CONVERSATION_ARCHIVE_PUBLIC_CODE_RE.fullmatch(value["route"]) is None
        or type(value.get("actionable")) is not bool
        or value.get("contentFree") is not True
        or not isinstance(value.get("deletionStates"), list)
        or any(
            not isinstance(item, str)
            or _CONVERSATION_ARCHIVE_PUBLIC_CODE_RE.fullmatch(item) is None
            for item in value["deletionStates"]
        )
    ):
        raise ValueError("archive_admin_response_invalid")


def _conversation_archive_public_upstream_success(
    upstream_path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if payload.get("ok") is not True:
        raise ValueError("archive_admin_response_invalid")
    action = upstream_path.removeprefix(
        f"{CONVERSATION_ARCHIVE_ADMIN_UPSTREAM_PREFIX}/"
    )

    if action == "challenge":
        expected = {"ok", "state", "challengeId"}
    elif action in {"login", "logout"}:
        expected = {"ok", "state"}
    elif action == "records":
        expected = {"ok", "records", "nextCursor"}
    elif action == "participation":
        expected = {"ok", "intervals", "nextCursor"}
    elif action == "voice-state-transitions":
        expected = {"ok", "transitions", "nextCursor"}
    elif action == "legal-minimal":
        expected = {"ok", "events", "nextCursor"}
    elif action in {"delete/preview", "delete/apply"}:
        expected = {"ok", "state", "affectedCount"}
        if action == "delete/preview":
            expected.add("previewToken")
        else:
            expected.add("requestId")
    elif action == "feedback/workflows":
        expected = {"ok", "workflows", "activeVersionId"}
    elif action in {
        "feedback/capture",
        "feedback/generalize",
        "feedback/evaluate",
        "feedback/approval/apply",
        "feedback/canary",
        "feedback/activate",
    }:
        expected = {"ok", "workflow"}
    elif action in {
        "feedback/approval/preview",
        "feedback/rollback/preview",
        "feedback/revoke/preview",
    }:
        expected = {"ok", "state", "previewToken", "versionId", "guidance"}
    elif action == "feedback/rollback/apply":
        expected = {"ok", "state", "versionId", "activeVersionId"}
    elif action == "feedback/failure":
        expected = {"ok", "state", "failureId", "versionId"}
    elif action == "feedback/revoke/apply":
        expected = {"ok", "state", "versionIds", "activeVersionId"}
    else:
        raise ValueError("archive_admin_response_invalid")
    if set(payload) != expected:
        raise ValueError("archive_admin_response_invalid")

    state = payload.get("state")
    if action not in {
        "records",
        "participation",
        "voice-state-transitions",
        "legal-minimal",
        "feedback/workflows",
        "feedback/capture",
        "feedback/generalize",
        "feedback/evaluate",
        "feedback/approval/apply",
        "feedback/canary",
        "feedback/activate",
    } and (
        not isinstance(state, str)
        or _CONVERSATION_ARCHIVE_PUBLIC_CODE_RE.fullmatch(state) is None
    ):
        raise ValueError("archive_admin_response_invalid")

    if action == "challenge":
        challenge_id = payload.get("challengeId")
        if (
            not isinstance(challenge_id, str)
            or not 1 <= len(challenge_id) <= 128
            or re.fullmatch(r"[A-Za-z0-9_-]+", challenge_id) is None
        ):
            raise ValueError("archive_admin_response_invalid")
    elif action == "records":
        records = payload.get("records")
        next_cursor = payload.get("nextCursor")
        if not isinstance(records, list) or len(records) > 2:
            raise ValueError("archive_admin_response_invalid")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or not next_cursor
            or len(next_cursor) > 2048
            or re.fullmatch(r"[A-Za-z0-9_-]+", next_cursor) is None
        ):
            raise ValueError("archive_admin_response_invalid")
        record_fields = {"recordId", "createdAt", "kind", "ownerName", "body"}
        record_ids: set[str] = set()
        for record in records:
            if (
                not isinstance(record, dict)
                or set(record) != record_fields
                or any(not isinstance(record[field], str) for field in record_fields)
                or not record["recordId"]
                or len(record["recordId"]) > 128
                or len(record["createdAt"]) > 64
                or len(record["kind"]) > 128
                or len(record["ownerName"]) > 512
                or len(record["body"].encode("utf-8")) > 128 * 1024
                or record["recordId"] in record_ids
            ):
                raise ValueError("archive_admin_response_invalid")
            record_ids.add(record["recordId"])
    elif action == "participation":
        intervals = payload.get("intervals")
        next_cursor = payload.get("nextCursor")
        if not isinstance(intervals, list) or len(intervals) > 100:
            raise ValueError("archive_admin_response_invalid")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or re.fullmatch(r"[0-9a-f]{64}", next_cursor) is None
        ):
            raise ValueError("archive_admin_response_invalid")
        fields = {
            "intervalId",
            "principalId",
            "ownerName",
            "guildId",
            "channelId",
            "kind",
            "startedAt",
            "endedAt",
        }
        interval_ids: set[str] = set()
        for interval in intervals:
            if (
                not isinstance(interval, dict)
                or set(interval) != fields
                or any(
                    not isinstance(interval[field], str)
                    for field in fields - {"endedAt"}
                )
                or (
                    interval["endedAt"] is not None
                    and not isinstance(interval["endedAt"], str)
                )
                or not 1 <= len(interval["intervalId"]) <= 128
                or not 1 <= len(interval["principalId"]) <= 128
                or not 1 <= len(interval["ownerName"]) <= 512
                or not 1 <= len(interval["guildId"]) <= 64
                or not 1 <= len(interval["channelId"]) <= 64
                or interval["kind"] not in {"presence", "eligible"}
                or not 1 <= len(interval["startedAt"]) <= 64
                or (
                    isinstance(interval["endedAt"], str)
                    and not 1 <= len(interval["endedAt"]) <= 64
                )
                or interval["intervalId"] in interval_ids
            ):
                raise ValueError("archive_admin_response_invalid")
            interval_ids.add(interval["intervalId"])
    elif action == "voice-state-transitions":
        transitions = payload.get("transitions")
        next_cursor = payload.get("nextCursor")
        if not isinstance(transitions, list) or len(transitions) > 100:
            raise ValueError("archive_admin_response_invalid")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or re.fullmatch(r"[0-9a-f]{64}", next_cursor) is None
        ):
            raise ValueError("archive_admin_response_invalid")
        string_fields = {
            "transitionId",
            "principalId",
            "ownerName",
            "guildId",
            "channelId",
            "eventAt",
        }
        boolean_fields = {
            "present",
            "consentCurrent",
            "selfMute",
            "serverMute",
            "selfDeaf",
            "serverDeaf",
            "suppressed",
            "gatewayKnown",
        }
        transition_ids: set[str] = set()
        for transition in transitions:
            if (
                not isinstance(transition, dict)
                or set(transition) != string_fields | boolean_fields
                or any(
                    not isinstance(transition[field], str)
                    for field in string_fields
                )
                or any(type(transition[field]) is not bool for field in boolean_fields)
                or not 1 <= len(transition["transitionId"]) <= 128
                or not 1 <= len(transition["principalId"]) <= 128
                or not 1 <= len(transition["ownerName"]) <= 512
                or not 1 <= len(transition["guildId"]) <= 64
                or not 1 <= len(transition["channelId"]) <= 64
                or not 1 <= len(transition["eventAt"]) <= 64
                or transition["transitionId"] in transition_ids
            ):
                raise ValueError("archive_admin_response_invalid")
            transition_ids.add(transition["transitionId"])
    elif action == "legal-minimal":
        events = payload.get("events")
        next_cursor = payload.get("nextCursor")
        if not isinstance(events, list) or len(events) > 100:
            raise ValueError("archive_admin_response_invalid")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or re.fullmatch(r"[0-9a-f]{64}", next_cursor) is None
        ):
            raise ValueError("archive_admin_response_invalid")
        for event in events:
            if (
                not isinstance(event, dict)
                or set(event) != {"ownerName", "occurredAt"}
                or not isinstance(event["ownerName"], str)
                or not isinstance(event["occurredAt"], str)
                or not 1 <= len(event["ownerName"]) <= 512
                or not 1 <= len(event["occurredAt"]) <= 64
            ):
                raise ValueError("archive_admin_response_invalid")
    elif action in {"delete/preview", "delete/apply"}:
        affected_count = payload.get("affectedCount")
        if type(affected_count) is not int or not 0 <= affected_count <= 1_000_000:
            raise ValueError("archive_admin_response_invalid")
        if action == "delete/preview":
            preview_token = payload.get("previewToken")
            if (
                not isinstance(preview_token, str)
                or not 1 <= len(preview_token) <= 128
                or re.fullmatch(r"[A-Za-z0-9_-]+", preview_token) is None
            ):
                raise ValueError("archive_admin_response_invalid")
        else:
            request_id = payload.get("requestId")
            if (
                not isinstance(request_id, str)
                or not 1 <= len(request_id) <= 64
                or re.fullmatch(r"[A-Za-z0-9_-]+", request_id) is None
            ):
                raise ValueError("archive_admin_response_invalid")
            payload = dict(payload)
            payload.pop("requestId")
    elif action == "feedback/workflows":
        workflows = payload.get("workflows")
        active_version = payload.get("activeVersionId")
        if (
            not isinstance(workflows, list)
            or len(workflows) > 100
            or not isinstance(active_version, str)
            or not 1 <= len(active_version) <= 128
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", active_version) is None
        ):
            raise ValueError("archive_admin_response_invalid")
        for workflow in workflows:
            _conversation_archive_feedback_workflow(workflow)
    elif action in {
        "feedback/capture",
        "feedback/generalize",
        "feedback/evaluate",
        "feedback/approval/apply",
        "feedback/canary",
        "feedback/activate",
    }:
        _conversation_archive_feedback_workflow(payload.get("workflow"))
    elif action in {
        "feedback/approval/preview",
        "feedback/rollback/preview",
        "feedback/revoke/preview",
    }:
        for key in ("previewToken", "versionId"):
            value = payload.get(key)
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 128
                or re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is None
            ):
                raise ValueError("archive_admin_response_invalid")
        guidance = payload.get("guidance")
        if (
            not isinstance(guidance, str)
            or len(guidance) > 8_000
            or (action != "feedback/approval/preview" and guidance != "")
        ):
            raise ValueError("archive_admin_response_invalid")
    elif action == "feedback/rollback/apply":
        if any(
            not isinstance(payload.get(key), str)
            or not 1 <= len(payload[key]) <= 128
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", payload[key]) is None
            for key in ("versionId", "activeVersionId")
        ):
            raise ValueError("archive_admin_response_invalid")
    elif action == "feedback/failure":
        if any(
            not isinstance(payload.get(key), str)
            or not 1 <= len(payload[key]) <= 128
            or re.fullmatch(r"[A-Za-z0-9_.:-]+", payload[key]) is None
            for key in ("failureId", "versionId")
        ):
            raise ValueError("archive_admin_response_invalid")
    elif action == "feedback/revoke/apply":
        version_ids = payload.get("versionIds")
        if (
            not isinstance(version_ids, list)
            or len(version_ids) > 100
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= 128
                or re.fullmatch(r"[A-Za-z0-9_.:-]+", value) is None
                for value in version_ids
            )
            or not isinstance(payload.get("activeVersionId"), str)
            or not 1 <= len(payload["activeVersionId"]) <= 128
            or re.fullmatch(
                r"[A-Za-z0-9_.:-]+", payload["activeVersionId"]
            )
            is None
        ):
            raise ValueError("archive_admin_response_invalid")
    return payload


async def _conversation_archive_request_json(
    request: web.Request,
) -> dict[str, Any]:
    encoded = await request.content.read(
        CONVERSATION_ARCHIVE_ADMIN_MAX_REQUEST_BYTES + 1
    )
    if len(encoded) > CONVERSATION_ARCHIVE_ADMIN_MAX_REQUEST_BYTES:
        raise ValueError("archive_admin_request_too_large")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("archive_admin_request_invalid") from None
    if not isinstance(payload, dict):
        raise ValueError("archive_admin_request_invalid")
    return payload


async def _proxy_conversation_archive_admin(
    request: web.Request,
    *,
    method: str,
    upstream_path: str,
    body: dict[str, Any] | None = None,
    require_session_cookie: bool = False,
    require_login_cookie: bool = False,
) -> web.Response:
    try:
        encoded_body = (
            json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if method == "POST"
            else b""
        )
    except (TypeError, ValueError):
        return _conversation_archive_public_error(
            "conversation_archive_request_invalid",
            status=400,
        )
    try:
        headers = _conversation_archive_proxy_headers(
            request,
            method=method,
            upstream_path=upstream_path,
            body=encoded_body,
        )
    except (TypeError, ValueError):
        return _conversation_archive_public_error(
            "conversation_archive_authorization_unavailable",
            status=503,
        )
    session_cookie = _conversation_archive_request_cookie(request)
    if require_session_cookie and not session_cookie:
        return _conversation_archive_public_error(
            "conversation_archive_admin_authentication_required",
            status=401,
        )
    if require_session_cookie and session_cookie:
        headers["Cookie"] = (
            f"{CONVERSATION_ARCHIVE_ADMIN_COOKIE}={session_cookie}"
        )
    if method == "POST":
        headers["Content-Type"] = "application/json"
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            request_context = (
                session.post(
                    f"{BOT_API_BASE}{upstream_path}",
                    data=encoded_body,
                    headers=headers,
                    allow_redirects=False,
                )
                if method == "POST"
                else session.get(
                    f"{BOT_API_BASE}{upstream_path}",
                    headers=headers,
                    allow_redirects=False,
                )
            )
            async with request_context as upstream:
                if (
                    upstream.content_length is not None
                    and upstream.content_length
                    > CONVERSATION_ARCHIVE_ADMIN_MAX_RESPONSE_BYTES
                ):
                    raise ValueError("archive_admin_response_too_large")
                encoded = await upstream.content.read(
                    CONVERSATION_ARCHIVE_ADMIN_MAX_RESPONSE_BYTES + 1
                )
                if len(encoded) > CONVERSATION_ARCHIVE_ADMIN_MAX_RESPONSE_BYTES:
                    raise ValueError("archive_admin_response_too_large")
                payload = json.loads(encoded.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("archive_admin_response_invalid")
                response_cookie = _conversation_archive_response_cookie(
                    upstream.headers
                )
                if (
                    upstream_path.endswith(("/challenge", "/login"))
                    and _conversation_archive_response_exposes_auth_secret(payload)
                ):
                    raise ValueError("archive_admin_response_invalid")
                successful = upstream.status == 200
                response_status = upstream.status
                cookie_policy = (
                    "login"
                    if require_login_cookie
                    else "logout"
                    if upstream_path.endswith("/logout")
                    else "none"
                )
                if not successful:
                    if response_cookie is not None:
                        raise ValueError("archive_admin_cookie_invalid")
                    payload = _conversation_archive_public_upstream_error(
                        payload
                    )
                    if response_status < 400:
                        response_status = 502
                else:
                    payload = _conversation_archive_public_upstream_success(
                        upstream_path,
                        payload,
                    )
                    if cookie_policy == "login" and (
                        response_cookie is None or response_cookie[1]
                    ):
                        raise ValueError("archive_admin_cookie_missing")
                    if cookie_policy == "logout" and (
                        response_cookie is not None and not response_cookie[1]
                    ):
                        raise ValueError("archive_admin_cookie_invalid")
                    if cookie_policy == "none" and response_cookie is not None:
                        raise ValueError("archive_admin_cookie_invalid")
                response = add_control_page_no_store_headers(
                    json_response(payload, status=response_status)
                )
                if response_cookie is not None:
                    response.headers["Set-Cookie"] = response_cookie[0]
                elif (
                    not successful
                    and payload.get("state") == "authentication_required"
                ):
                    response.headers["Set-Cookie"] = (
                        f"{CONVERSATION_ARCHIVE_ADMIN_COOKIE}=; "
                        "Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                    )
                return response
    except (ClientError, asyncio.TimeoutError):
        return _conversation_archive_public_error(
            "conversation_archive_unavailable",
            status=503,
        )
    except Exception:
        return _conversation_archive_public_error(
            "conversation_archive_proxy_invalid",
            status=502,
        )


async def _conversation_archive_admin_handler(
    request: web.Request,
    *,
    method: str,
    action: str,
    require_session_cookie: bool = False,
    require_login_cookie: bool = False,
) -> web.Response:
    if not CONVERSATION_ARCHIVE_ENABLED:
        return _conversation_archive_public_error(
            "conversation_archive_unavailable",
            status=503,
        )
    if not _conversation_archive_request_is_secure(request):
        return _conversation_archive_public_error(
            "conversation_archive_local_https_required",
            status=403,
        )
    body = None
    if method == "POST":
        try:
            body = await _conversation_archive_request_json(request)
        except ValueError:
            return _conversation_archive_public_error(
                "conversation_archive_request_invalid",
                status=400,
            )
    return await _proxy_conversation_archive_admin(
        request,
        method=method,
        upstream_path=f"{CONVERSATION_ARCHIVE_ADMIN_UPSTREAM_PREFIX}/{action}",
        body=body,
        require_session_cookie=require_session_cookie,
        require_login_cookie=require_login_cookie,
    )


async def conversation_archive_admin_challenge_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="challenge",
    )


async def conversation_archive_admin_login_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="login",
        require_login_cookie=True,
    )


async def _conversation_archive_admin_page_handler(
    request: web.Request,
    *,
    action: str,
    cursor_maximum: int,
) -> web.Response:
    if not CONVERSATION_ARCHIVE_ENABLED:
        return _conversation_archive_public_error(
            "conversation_archive_unavailable",
            status=503,
        )
    if not _conversation_archive_request_is_secure(request):
        return _conversation_archive_public_error(
            "conversation_archive_local_https_required",
            status=403,
        )
    if set(request.query) - {"cursor"}:
        return _conversation_archive_public_error(
            "conversation_archive_request_invalid",
            status=400,
        )
    cursor_values = request.query.getall("cursor", [])
    if len(cursor_values) > 1:
        return _conversation_archive_public_error(
            "conversation_archive_request_invalid",
            status=400,
        )
    cursor = str(cursor_values[0]) if cursor_values else ""
    if (cursor_values and not cursor) or (
        cursor
        and (
            len(cursor) > cursor_maximum
            or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None
        )
    ):
        return _conversation_archive_public_error(
            "conversation_archive_request_invalid",
            status=400,
        )
    return await _proxy_conversation_archive_admin(
        request,
        method="POST",
        upstream_path=f"{CONVERSATION_ARCHIVE_ADMIN_UPSTREAM_PREFIX}/{action}",
        body={"cursor": cursor} if cursor else {},
        require_session_cookie=True,
    )


async def conversation_archive_admin_records_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_page_handler(
        request,
        action="records",
        cursor_maximum=2048,
    )


async def conversation_archive_admin_participation_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_page_handler(
        request,
        action="participation",
        cursor_maximum=64,
    )


async def conversation_archive_admin_voice_state_transitions_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_page_handler(
        request,
        action="voice-state-transitions",
        cursor_maximum=64,
    )


async def conversation_archive_admin_legal_minimal_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_page_handler(
        request,
        action="legal-minimal",
        cursor_maximum=64,
    )


async def conversation_archive_admin_delete_preview_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="delete/preview",
        require_session_cookie=True,
    )


async def conversation_archive_admin_delete_apply_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="delete/apply",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_workflows_handler(
    request: web.Request,
) -> web.Response:
    if request.query_string:
        return _conversation_archive_public_error(
            "conversation_archive_request_invalid",
            status=400,
        )
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/workflows",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_capture_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/capture",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_generalize_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/generalize",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_evaluate_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/evaluate",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_approval_preview_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/approval/preview",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_approval_apply_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/approval/apply",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_canary_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/canary",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_activate_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/activate",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_rollback_preview_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/rollback/preview",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_rollback_apply_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/rollback/apply",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_failure_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/failure",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_revoke_preview_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/revoke/preview",
        require_session_cookie=True,
    )


async def conversation_archive_admin_feedback_revoke_apply_handler(
    request: web.Request,
) -> web.Response:
    return await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="feedback/revoke/apply",
        require_session_cookie=True,
    )


async def conversation_archive_admin_logout_handler(
    request: web.Request,
) -> web.Response:
    response = await _conversation_archive_admin_handler(
        request,
        method="POST",
        action="logout",
        require_session_cookie=True,
    )
    if 200 <= response.status < 300:
        response.headers["Set-Cookie"] = (
            f"{CONVERSATION_ARCHIVE_ADMIN_COOKIE}=; "
            "Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
        )
    return response


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


async def _voice_input_retirement_bot_post(
    path: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{BOT_API_BASE}{path}",
                json=payload,
                headers={
                    EVELYN_INTERNAL_CONTROL_HEADER: (
                        EVELYN_INTERNAL_CONTROL_TOKEN
                    )
                },
            ) as response:
                encoded = await response.content.read(8193)
                if len(encoded) > 8192:
                    raise ValueError("response_too_large")
                body = json.loads(encoded.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("response_invalid")
                return response.status, body
    except Exception as exc:
        print(
            "[CONTROL PAGE] voice_input_retirement_bot_unavailable "
            f"path={path} errorType={type(exc).__name__}",
            flush=True,
        )
        return 503, {
            "ok": False,
            "error": "voice_input_lease_retirement_unavailable",
        }


async def _reconcile_stopped_discord_voice_input_owner() -> dict[str, Any]:
    status, prepared = await _voice_input_retirement_bot_post(
        "/internal/voice-input-lease/retirement/prepare",
        {"source": "discord_voice"},
    )
    required = prepared.get("required")
    if (
        status != 200
        or prepared.get("ok") is not True
        or prepared.get("schema")
        != "voice_input_lease.retirement-claim.v1"
        or type(required) is not bool
    ):
        return {
            "ok": False,
            "error": "voice_input_lease_retirement_unavailable",
            "httpStatus": 503,
        }
    if required is False:
        if set(prepared) != {"ok", "schema", "required"}:
            return {
                "ok": False,
                "error": "voice_input_lease_retirement_unavailable",
                "httpStatus": 503,
            }
        return {"ok": True, "retired": False}
    claim_id = str(prepared.get("claimId") or "")
    if (
        set(prepared)
        != {"ok", "schema", "required", "claimId", "expiresAt"}
        or re.fullmatch(r"voice-retire-[0-9a-f]{32}", claim_id)
        is None
        or isinstance(prepared.get("expiresAt"), bool)
        or not isinstance(prepared.get("expiresAt"), (int, float))
        or not math.isfinite(float(prepared["expiresAt"]))
        or float(prepared["expiresAt"]) <= 0.0
    ):
        return {
            "ok": False,
            "error": "voice_input_lease_retirement_unavailable",
            "httpStatus": 503,
        }
    try:
        attested = await asyncio.to_thread(
            HostSupervisorClient(
                attestation_auth_token=(
                    EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN
                )
            ).attest_discord_stopped,
            claim_id,
        )
    except Exception:
        attested = {
            "ok": False,
            "error": "discord_stop_attestation_unverified",
        }
    if (
        not isinstance(attested, dict)
        or set(attested)
        != {
            "ok",
            "verified",
            "hostInstanceId",
            "requestId",
            "claimId",
        }
        or attested.get("ok") is not True
        or attested.get("verified") is not True
        or attested.get("claimId") != claim_id
    ):
        return {
            "ok": False,
            "error": "voice_input_lease_retirement_unverified",
            "httpStatus": 503,
        }
    completion_status, completed = (
        await _voice_input_retirement_bot_post(
            "/internal/voice-input-lease/retirement/complete",
            {
                "claimId": claim_id,
                "hostInstanceId": str(
                    attested.get("hostInstanceId") or ""
                ),
                "requestId": str(
                    attested.get("requestId") or ""
                ),
            },
        )
    )
    if (
        completion_status != 200
        or completed.get("ok") is not True
        or completed.get("schema")
        != "voice_input_lease.retirement-result.v1"
        or type(completed.get("retired")) is not bool
        or type(completed.get("alreadyReleased")) is not bool
        or set(completed)
        != {"ok", "schema", "retired", "alreadyReleased"}
    ):
        return {
            "ok": False,
            "error": "voice_input_lease_retirement_unverified",
            "httpStatus": 503,
        }
    return {
        "ok": True,
        "retired": completed["retired"],
    }


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
            async def post_control() -> dict[str, Any]:
                async with session.post(
                    url,
                    json=request_payload,
                    headers=headers,
                ) as response:
                    try:
                        result = await response.json(
                            content_type=None
                        )
                    except Exception:
                        result = {}
                    if not isinstance(result, dict):
                        result = {}
                    result.pop("detail", None)
                    if result.get("error"):
                        result["error"] = public_error_code(
                            result.get("error"),
                            fallback="mic_control_failed",
                        )
                    local_bridge = result.get("localBridge")
                    if isinstance(local_bridge, dict):
                        raw_error = local_bridge.get("lastError")
                        if raw_error:
                            local_bridge["lastError"] = (
                                public_error_code(
                                    raw_error,
                                    fallback="local_bridge_failed",
                                )
                            )
                    result.setdefault("httpStatus", response.status)
                    if response.status >= 400:
                        result["ok"] = False
                        result.setdefault(
                            "error",
                            f"mic_control_http_{response.status}",
                        )
                    return result

            result = await post_control()
            if (
                enabled
                and result.get("error")
                == "voice_input_lease_conflict"
            ):
                retirement = (
                    await _reconcile_stopped_discord_voice_input_owner()
                )
                if retirement.get("ok") is True:
                    return await post_control()
                return {
                    "ok": False,
                    "applied": False,
                    "error": str(
                        retirement.get("error")
                        or "voice_input_lease_retirement_unverified"
                    ),
                    "httpStatus": int(
                        retirement.get("httpStatus") or 503
                    ),
                }
            return result
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


async def index_handler(request: web.Request) -> web.StreamResponse:
    if (
        request.path == "/archive/admin"
        and (
            not CONVERSATION_ARCHIVE_ENABLED
            or not _conversation_archive_request_is_secure(request)
        )
    ):
        raise web.HTTPNotFound(text="control page index not found")
    index_path = DOCS_DIR / (
        "archive-admin.html"
        if request.path == "/archive/admin"
        else "index.html"
    )
    if not index_path.exists():
        raise web.HTTPNotFound(text="control page index not found")
    response = web.FileResponse(index_path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Content-Type"] = static_content_type(index_path) or "text/html; charset=utf-8"
    if request.path == "/archive/admin":
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; object-src 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
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
    if not enabled:
        retirement = await _reconcile_stopped_discord_voice_input_owner()
        if retirement.get("ok") is not True:
            CONTROL_PAGE_RUNTIME_HEALTH_CACHE.clear()
            return json_response(
                {
                    "schema": "discord_mode.transition.v1",
                    "ok": False,
                    "enabled": False,
                    "state": "stopped_unreconciled",
                    "error": str(
                        retirement.get("error")
                        or "voice_input_lease_retirement_unverified"
                    ),
                    "automaticRetry": False,
                },
                status=int(retirement.get("httpStatus") or 503),
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


async def _await_shielded_task(task: asyncio.Task[Any]) -> Any:
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
    return await _await_shielded_task(task)


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
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manager = get_voice_capture_consent_manager()
    if validation_session is None:
        validation_session = get_voice_validation_manager().snapshot()
    consent = manager.status()
    reason = manager.revocation_reason(
        validation_session=validation_session,
        include_interrupted_enabling=True,
    )
    local = (
        capabilities.get("voiceLocal")
        if isinstance(capabilities, dict)
        else None
    )
    blocker_codes = {
        str(item.get("code") or "")
        for item in (local.get("blockers") or [])
        if isinstance(item, dict)
    } if isinstance(local, dict) else set()
    activated_at = consent.get("activatedAt")
    active_long_enough = bool(
        isinstance(activated_at, (int, float))
        and not isinstance(activated_at, bool)
        and time.time() - float(activated_at)
        >= VOICE_CAPTURE_HOST_LEASE_STALE_SEC
    )
    if (
        not reason
        and consent.get("state") == "active"
        and active_long_enough
        and blocker_codes
        & {"local_mic_disabled", "local_mic_capture_not_ready"}
    ):
        reason = "voice_capture_runtime_stopped"
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
        await _await_shielded_task(cleanup_task)


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

            phase = "active_lease_projection"
            await _publish_voice_capture_host_lease(manager)
            phase = "post_activation"
            health = await asyncio.wait_for(
                cached_runtime_health(force=True),
                timeout=VOICE_CAPTURE_POST_ACTIVATION_TIMEOUT_SEC,
            )
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
                await _publish_voice_capture_host_lease(manager)
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
                await _await_shielded_task(cleanup_task)
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
            capabilities=(
                health.get("capabilities")
                if isinstance(health, dict)
                else None
            ),
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
                    if bound.get("ok"):
                        await _publish_voice_capture_host_lease(manager)
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


_TASK_APPROVAL_IDENTIFIER = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z"
)
_TASK_APPROVAL_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_TASK_APPROVAL_CONFIRM_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
_TASK_APPROVAL_HTTP_MAX_BYTES = 8192
# ``web.json_response`` may expand one UTF-8 input byte to as many as six
# JSON bytes (for example a control character becomes ``\u00xx``). Keep the
# transport bounded while still carrying every Host-accepted full diff.
_TASK_APPROVAL_BOT_RESPONSE_MAX_BYTES = 8 * WORKSPACE_EDIT_MAX_PREVIEW_BYTES
_TASK_APPROVAL_PREVIEW_RESPONSE_KEYS = frozenset(
    {
        "ok",
        "schema",
        "preview",
        "confirmToken",
        "confirmExpiresAt",
    }
)
_TASK_APPROVAL_PREVIEW_KEYS = frozenset(
    {
        "schema",
        "taskId",
        "approvalId",
        "step",
        "maxSteps",
        "tool",
        "effect",
        "path",
        "mode",
        "baseSha256",
        "candidateSha256",
        "diffSha256",
        "previewDigest",
        "fullDiff",
        "diffTruncated",
        "dirtyStatus",
        "gitStatus",
        "tracked",
        "dirtyBaseAcknowledgementRequired",
        "bytes",
        "requiresExplicitConfirmation",
        "automaticRetry",
    }
)
_TASK_APPROVAL_DIRTY_STATES = frozenset(
    {"modified", "staged", "modified_and_staged", "untracked", "deleted"}
)


def _task_approval_identifier(value: Any) -> str:
    normalized = str(value or "")
    return normalized if _TASK_APPROVAL_IDENTIFIER.fullmatch(normalized) else ""


def _task_approval_public_preview_response(
    value: Any,
    *,
    task_id: str,
    approval_id: str,
) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or set(value) != _TASK_APPROVAL_PREVIEW_RESPONSE_KEYS
        or value.get("ok") is not True
        or value.get("schema") != "task_approval.preview-response.v1"
    ):
        return None
    preview = value.get("preview")
    if not isinstance(preview, dict) or set(preview) != _TASK_APPROVAL_PREVIEW_KEYS:
        return None
    full_diff = preview.get("fullDiff")
    base_sha = str(preview.get("baseSha256") or "")
    candidate_sha = str(preview.get("candidateSha256") or "")
    diff_sha = str(preview.get("diffSha256") or "")
    preview_digest = str(preview.get("previewDigest") or "")
    dirty_status = str(preview.get("dirtyStatus") or "")
    dirty_required = preview.get("dirtyBaseAcknowledgementRequired")
    git_status = preview.get("gitStatus")
    path = preview.get("path")
    confirm_token = value.get("confirmToken")
    try:
        step = int(preview.get("step"))
        max_steps = int(preview.get("maxSteps"))
        byte_count = int(preview.get("bytes"))
        confirm_expires_at = float(value.get("confirmExpiresAt"))
        full_diff_bytes = (
            full_diff.encode("utf-8") if isinstance(full_diff, str) else b""
        )
        git_status_bytes = (
            git_status.encode("utf-8") if isinstance(git_status, str) else b""
        )
    except (TypeError, ValueError, UnicodeError):
        return None
    dirty_values = _TASK_APPROVAL_DIRTY_STATES | {"clean", "absent"}
    if (
        preview.get("schema") != "task_approval.preview.v1"
        or preview.get("taskId") != task_id
        or preview.get("approvalId") != approval_id
        or preview.get("tool") != "workspace_edit"
        or preview.get("mode") not in {"create", "replace"}
        or type(preview.get("step")) is not int
        or type(preview.get("maxSteps")) is not int
        or not 1 <= step <= max_steps <= 10
        or not isinstance(path, str)
        or not path
        or len(path) > 512
        or "\x00" in path
        or not (base_sha == "ABSENT" or _TASK_APPROVAL_SHA256.fullmatch(base_sha))
        or _TASK_APPROVAL_SHA256.fullmatch(candidate_sha) is None
        or _TASK_APPROVAL_SHA256.fullmatch(diff_sha) is None
        or _TASK_APPROVAL_SHA256.fullmatch(preview_digest) is None
        or not isinstance(full_diff, str)
        or not full_diff
        or len(full_diff_bytes) > WORKSPACE_EDIT_MAX_PREVIEW_BYTES
        or hashlib.sha256(full_diff_bytes).hexdigest() != diff_sha
        or preview.get("diffTruncated") is not False
        or dirty_status not in dirty_values
        or type(preview.get("tracked")) is not bool
        or type(dirty_required) is not bool
        or (dirty_status in _TASK_APPROVAL_DIRTY_STATES) is not dirty_required
        or not isinstance(git_status, str)
        or len(git_status_bytes) > 4096
        or "\r" in git_status
        or "\n" in git_status
        or type(preview.get("bytes")) is not int
        or byte_count < 0
        or preview.get("requiresExplicitConfirmation") is not True
        or preview.get("automaticRetry") is not False
        or not isinstance(confirm_token, str)
        or _TASK_APPROVAL_CONFIRM_TOKEN.fullmatch(confirm_token) is None
        or type(value.get("confirmExpiresAt")) not in {int, float}
        or not math.isfinite(confirm_expires_at)
        or not 0.0 < confirm_expires_at <= 8.64e12
    ):
        return None
    if preview["mode"] == "create":
        if base_sha != "ABSENT" or dirty_status != "absent" or preview["tracked"]:
            return None
    elif base_sha == "ABSENT" or dirty_status in {"absent", "deleted"}:
        return None
    return {
        "ok": True,
        "schema": "task_approval.preview-response.v1",
        "preview": {
            "schema": "task_approval.preview.v1",
            "taskId": task_id,
            "approvalId": approval_id,
            "step": step,
            "maxSteps": max_steps,
            "tool": "workspace_edit",
            "effect": "UTF-8 파일 1개 생성 또는 교체",
            "path": path,
            "mode": preview["mode"],
            "baseSha256": base_sha,
            "candidateSha256": candidate_sha,
            "diffSha256": diff_sha,
            "previewDigest": preview_digest,
            "fullDiff": full_diff,
            "diffTruncated": False,
            "dirtyStatus": dirty_status,
            "gitStatus": git_status,
            "tracked": preview["tracked"],
            "dirtyBaseAcknowledgementRequired": dirty_required,
            "bytes": byte_count,
            "requiresExplicitConfirmation": True,
            "automaticRetry": False,
        },
        "confirmToken": confirm_token,
        "confirmExpiresAt": confirm_expires_at,
    }


async def _task_approval_json(
    request: web.Request,
    *,
    exact_fields: frozenset[str],
) -> tuple[dict[str, Any] | None, web.Response | None]:
    if (
        request.content_length is not None
        and request.content_length > _TASK_APPROVAL_HTTP_MAX_BYTES
    ):
        return None, json_response(
            {"ok": False, "error": "task_approval_request_too_large"},
            status=413,
        )
    try:
        encoded = await request.read()
        if len(encoded) > _TASK_APPROVAL_HTTP_MAX_BYTES:
            raise ValueError("payload_too_large")
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None, json_response(
            {"ok": False, "error": "task_approval_request_invalid"},
            status=400,
        )
    if not isinstance(payload, dict) or set(payload) != set(exact_fields):
        return None, json_response(
            {"ok": False, "error": "task_approval_request_invalid"},
            status=400,
        )
    if not _task_approval_identifier(payload.get("taskId")) or not (
        _task_approval_identifier(payload.get("approvalId"))
    ):
        return None, json_response(
            {"ok": False, "error": "task_approval_request_invalid"},
            status=400,
        )
    return payload, None


async def _task_approval_bot_post(
    path: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    timeout = ClientTimeout(total=PROXY_TIMEOUT_SEC)
    headers = {
        EVELYN_INTERNAL_CONTROL_HEADER: EVELYN_INTERNAL_CONTROL_TOKEN,
    }
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{BOT_API_BASE}{path}",
                json=payload,
                headers=headers,
            ) as response:
                if (
                    response.content_length is not None
                    and response.content_length
                    > _TASK_APPROVAL_BOT_RESPONSE_MAX_BYTES
                ):
                    return 502, {
                        "ok": False,
                        "error": "task_approval_bot_response_invalid",
                    }
                encoded = await response.content.read(
                    _TASK_APPROVAL_BOT_RESPONSE_MAX_BYTES + 1
                )
                if len(encoded) > _TASK_APPROVAL_BOT_RESPONSE_MAX_BYTES:
                    return 502, {
                        "ok": False,
                        "error": "task_approval_bot_response_invalid",
                    }
                try:
                    body = json.loads(encoded.decode("utf-8"))
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                    body = None
                if not isinstance(body, dict):
                    return 502, {
                        "ok": False,
                        "error": "task_approval_bot_response_invalid",
                    }
                return response.status, body
    except Exception as exc:
        print(
            "[CONTROL PAGE] task_approval_bot_unavailable "
            f"path={path} errorType={type(exc).__name__}",
            flush=True,
        )
        return 503, {
            "ok": False,
            "error": "task_approval_bot_unavailable",
        }


def _task_approval_public_error(
    status: int,
    payload: dict[str, Any],
) -> web.Response:
    allowed = {
        "task_approval_not_found",
        "task_approval_preview_denied",
        "task_approval_claim_denied",
        "task_approval_cancel_denied",
        "task_approval_bot_unavailable",
        "task_approval_bot_response_invalid",
    }
    error = str(payload.get("error") or "")
    return json_response(
        {
            "ok": False,
            "error": error if error in allowed else "task_approval_denied",
            "automaticRetry": False,
        },
        status=status if 400 <= status <= 599 else 409,
    )


async def task_approval_preview_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _task_approval_json(
        request,
        exact_fields=frozenset({"taskId", "approvalId"}),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    status, result = await _task_approval_bot_post(
        "/internal/task-approval/preview",
        payload,
    )
    if status != 200 or result.get("ok") is not True:
        return _task_approval_public_error(status, result)
    public_result = _task_approval_public_preview_response(
        result,
        task_id=payload["taskId"],
        approval_id=payload["approvalId"],
    )
    if public_result is None:
        return _task_approval_public_error(
            502,
            {
                "ok": False,
                "error": "task_approval_bot_response_invalid",
            },
        )
    return json_response(public_result)


async def task_approval_apply_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _task_approval_json(
        request,
        exact_fields=frozenset(
            {
                "taskId",
                "approvalId",
                "confirmToken",
                "userConfirmed",
                "dirtyBaseAcknowledged",
            }
        ),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    if (
        payload.get("userConfirmed") is not True
        or type(payload.get("dirtyBaseAcknowledged")) is not bool
        or not isinstance(payload.get("confirmToken"), str)
        or not 32 <= len(payload["confirmToken"]) <= 256
    ):
        return json_response(
            {"ok": False, "error": "task_approval_explicit_confirmation_required"},
            status=400,
        )
    status, claimed = await _task_approval_bot_post(
        "/internal/task-approval/claim",
        payload,
    )
    if status != 200 or claimed.get("ok") is not True:
        return _task_approval_public_error(status, claimed)
    claim = claimed.get("claim")
    grant_expires_at = claim.get("grantExpiresAt") if isinstance(claim, dict) else None
    if (
        not isinstance(claim, dict)
        or type(grant_expires_at) not in {int, float}
        or not math.isfinite(float(grant_expires_at))
        or float(grant_expires_at) <= 0.0
    ):
        return json_response(
            {
                "ok": False,
                "error": "task_approval_claim_response_invalid",
                "automaticRetry": False,
            },
            status=503,
        )
    async def complete_claim() -> tuple[int, dict[str, Any]]:
        if time.time() >= float(grant_expires_at):
            mutation_result = {
                "attempted": False,
                "executed": False,
                "observed": True,
                "verified": True,
                "outcome": "blocked",
                "code": "task_grant_expired",
                "summary": "Task grant expired before workspace mutation dispatch.",
                "evidence": {},
            }
        else:
            try:
                mutation_client = WorkspaceMutationHostClient(
                    timeout_sec=TASK_APPROVAL_MUTATION_TIMEOUT_SEC,
                    auth_token=EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN,
                )
                mutation_result = await asyncio.to_thread(
                    mutation_client.apply,
                    claim,
                )
            except Exception:
                mutation_result = {
                    "attempted": True,
                    "executed": False,
                    "observed": False,
                    "verified": False,
                    "outcome": "outcome_unverified",
                    "code": "workspace_edit_apply_outcome_unverified",
                    "summary": "Workspace edit outcome is unverified.",
                    "evidence": {},
                }
        return await _task_approval_bot_post(
            "/internal/task-approval/complete",
            {
                "taskId": payload["taskId"],
                "approvalId": payload["approvalId"],
                "claimId": str(claim.get("claimId") or ""),
                "result": mutation_result,
            },
        )

    lifecycle = asyncio.create_task(
        complete_claim(),
        name="task-approval-apply-completion",
    )
    completion_status, completion = await _await_shielded_task(lifecycle)
    if completion_status != 200 or completion.get("ok") is not True:
        return json_response(
            {
                "ok": False,
                "error": "task_approval_completion_uncertain",
                "automaticRetry": False,
            },
            status=503,
        )
    return json_response(
        {
            "ok": True,
            "schema": "task_approval.apply-accepted.v1",
            "state": "resuming",
            "taskId": payload["taskId"],
            "approvalId": payload["approvalId"],
            "automaticRetry": False,
        },
        status=202,
    )


async def task_approval_cancel_handler(
    request: web.Request,
) -> web.StreamResponse:
    payload, error_response = await _task_approval_json(
        request,
        exact_fields=frozenset({"taskId", "approvalId"}),
    )
    if error_response is not None:
        return error_response
    assert payload is not None
    status, cancelled = await _task_approval_bot_post(
        "/internal/task-approval/cancel",
        payload,
    )
    if status != 200 or cancelled.get("ok") is not True:
        return _task_approval_public_error(status, cancelled)
    claim = cancelled.get("claim")
    if not isinstance(claim, dict):
        return json_response(
            {
                "ok": False,
                "error": "task_approval_cancel_response_invalid",
                "automaticRetry": False,
            },
            status=503,
        )
    async def complete_cancel_claim() -> tuple[dict[str, Any], int, dict[str, Any]]:
        try:
            mutation_client = WorkspaceMutationHostClient(
                timeout_sec=TASK_APPROVAL_MUTATION_TIMEOUT_SEC,
                auth_token=EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN,
            )
            mutation_result = await asyncio.to_thread(
                mutation_client.cancel,
                claim,
            )
        except Exception:
            mutation_result = {
                "attempted": True,
                "executed": False,
                "observed": False,
                "verified": False,
                "outcome": "outcome_unverified",
                "code": "workspace_edit_cancel_outcome_unverified",
                "summary": "Workspace edit cancellation outcome is unverified.",
                "evidence": {},
            }
        completion_status, completion = await _task_approval_bot_post(
            "/internal/task-approval/cancel-complete",
            {
                "taskId": payload["taskId"],
                "approvalId": payload["approvalId"],
                "claimId": str(claim.get("claimId") or ""),
                "result": mutation_result,
            },
        )
        return mutation_result, completion_status, completion

    lifecycle = asyncio.create_task(
        complete_cancel_claim(),
        name="task-approval-cancel-completion",
    )
    mutation_result, completion_status, completion = await _await_shielded_task(
        lifecycle
    )
    if completion_status != 200 or completion.get("ok") is not True:
        return json_response(
            {
                "ok": False,
                "error": "task_approval_cancel_completion_uncertain",
                "automaticRetry": False,
            },
            status=503,
        )
    if (
        mutation_result.get("verified") is not True
        or mutation_result.get("outcome") != "succeeded"
        or mutation_result.get("code") != "workspace_edit_stage_cancelled"
        or completion.get("state") != "cancelled"
    ):
        return json_response(
            {
                "ok": False,
                "error": "task_approval_cancel_outcome_unverified",
                "automaticRetry": False,
            },
            status=503,
        )
    return json_response(
        {
            "ok": True,
            "schema": "task_approval.cancelled.v1",
            "state": "cancelled",
            "taskId": payload["taskId"],
            "approvalId": payload["approvalId"],
            "automaticRetry": False,
        }
    )


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
    app = web.Application(
        middlewares=[
            _conversation_archive_admin_origin_middleware,
            control_page_cors_middleware,
        ]
    )
    app[VOICE_CAPTURE_CONSENT_LOCK_KEY] = asyncio.Lock()
    if manage_voice_capture_consent:
        app.cleanup_ctx.append(_voice_capture_owner_context)
        app.cleanup_ctx.append(_voice_capture_consent_context)
    app.router.add_get("/", index_handler)
    app.router.add_get("/archive/admin", index_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/assets/{asset_path:.*}", asset_handler)
    app.router.add_get("/archive/assets/{asset_path:.*}", asset_handler)
    app.router.add_get("/api/control-page/state", state_handler)
    app.router.add_get("/api/control-page/session", control_page_session_handler)
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/challenge",
        conversation_archive_admin_challenge_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/login",
        conversation_archive_admin_login_handler,
    )
    app.router.add_get(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/records",
        conversation_archive_admin_records_handler,
    )
    app.router.add_get(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/participation",
        conversation_archive_admin_participation_handler,
    )
    app.router.add_get(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/voice-state-transitions",
        conversation_archive_admin_voice_state_transitions_handler,
    )
    app.router.add_get(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/legal-minimal",
        conversation_archive_admin_legal_minimal_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/delete/preview",
        conversation_archive_admin_delete_preview_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/delete/apply",
        conversation_archive_admin_delete_apply_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/workflows",
        conversation_archive_admin_feedback_workflows_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/capture",
        conversation_archive_admin_feedback_capture_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/generalize",
        conversation_archive_admin_feedback_generalize_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/evaluate",
        conversation_archive_admin_feedback_evaluate_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/approval/preview",
        conversation_archive_admin_feedback_approval_preview_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/approval/apply",
        conversation_archive_admin_feedback_approval_apply_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/canary",
        conversation_archive_admin_feedback_canary_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/activate",
        conversation_archive_admin_feedback_activate_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/rollback/preview",
        conversation_archive_admin_feedback_rollback_preview_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/rollback/apply",
        conversation_archive_admin_feedback_rollback_apply_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/failure",
        conversation_archive_admin_feedback_failure_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/revoke/preview",
        conversation_archive_admin_feedback_revoke_preview_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/feedback/revoke/apply",
        conversation_archive_admin_feedback_revoke_apply_handler,
    )
    app.router.add_post(
        f"{CONVERSATION_ARCHIVE_ADMIN_BROWSER_PREFIX}/admin/logout",
        conversation_archive_admin_logout_handler,
    )
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
    app.router.add_post(
        "/api/control-page/task-approval/preview",
        task_approval_preview_handler,
    )
    app.router.add_post(
        "/api/control-page/task-approval/apply",
        task_approval_apply_handler,
    )
    app.router.add_post(
        "/api/control-page/task-approval/cancel",
        task_approval_cancel_handler,
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
        "/api/control-page/task-approval/preview",
        task_approval_preview_handler,
    )
    app.router.add_options(
        "/api/control-page/task-approval/apply",
        task_approval_apply_handler,
    )
    app.router.add_options(
        "/api/control-page/task-approval/cancel",
        task_approval_cancel_handler,
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


def build_control_page_ssl_context(
    *,
    archive_enabled: bool = CONVERSATION_ARCHIVE_ENABLED,
    cert_file: str = CONTROL_PAGE_TLS_CERT_FILE,
    key_file: str = CONTROL_PAGE_TLS_KEY_FILE,
    context_factory: Callable[[], Any] | None = None,
) -> ssl.SSLContext | None:
    cert_path = Path(str(cert_file or "")) if cert_file else None
    key_path = Path(str(key_file or "")) if key_file else None
    configured = cert_path is not None or key_path is not None
    if not configured:
        if archive_enabled:
            raise RuntimeError("conversation_archive_loopback_https_required")
        return None
    if (
        cert_path is None
        or key_path is None
        or not cert_path.is_file()
        or not key_path.is_file()
    ):
        raise RuntimeError("control_page_tls_material_unavailable")
    context = (
        context_factory()
        if context_factory is not None
        else ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    return context


def main() -> None:
    web.run_app(
        create_app(),
        host=HOST,
        port=PORT,
        access_log=None,
        ssl_context=build_control_page_ssl_context(),
    )


if __name__ == "__main__":
    main()
