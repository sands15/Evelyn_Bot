from __future__ import annotations

import json
import ipaddress
import os
import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from aiohttp import web

from .memory_deletion_journal import (
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalIntegrityError,
)


CONTROL_PAGE_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

CONTROL_PAGE_CSRF_HEADER = "X-Evelyn-CSRF-Token"
CONTROL_PAGE_CSRF_TOKEN = secrets.token_urlsafe(32)
CONTROL_PAGE_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def control_page_json_response(data: Any, *, status: int = 200) -> web.Response:
    return web.Response(
        status=status,
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
    )


def add_control_page_no_store_headers(response: web.StreamResponse) -> web.StreamResponse:
    response.headers.update(CONTROL_PAGE_NO_STORE_HEADERS)
    return response


def control_page_file_response(path: Path, *, not_found_text: str) -> web.FileResponse:
    if not path.exists() or not path.is_file():
        raise web.HTTPNotFound(text=not_found_text)
    response = web.FileResponse(path)
    add_control_page_no_store_headers(response)
    return response


def resolve_control_page_asset_path(assets_dir: Path, requested_asset_path: Any) -> Path:
    requested = Path(str(requested_asset_path or ""))
    asset_path = (assets_dir / requested).resolve()
    assets_root = assets_dir.resolve()
    try:
        asset_path.relative_to(assets_root)
    except ValueError as exc:
        raise web.HTTPForbidden(text="invalid asset path") from exc
    if not asset_path.exists() or not asset_path.is_file():
        raise web.HTTPNotFound(text="asset not found")
    return asset_path


def build_control_page_health_payload(
    *,
    local_only_mode: bool,
    discord_enabled: bool,
    port: int,
) -> dict[str, Any]:
    return {
        "ok": True,
        "role": "bot-api",
        "controlPage": True,
        "localOnly": bool(local_only_mode),
        "discordEnabled": bool(discord_enabled),
        "port": int(port),
    }


def control_page_api_cors_applies(path: str) -> bool:
    return str(path or "").startswith("/api/control-page/")


def configured_control_page_origins() -> frozenset[str]:
    raw = os.getenv("CONTROL_PAGE_ALLOWED_ORIGINS", "")
    return frozenset(
        normalized
        for candidate in raw.split(",")
        if (normalized := normalize_request_origin(candidate))
    )


def normalize_request_origin(value: Any) -> str:
    origin = str(value or "").strip().rstrip("/")
    if not origin or origin == "null":
        return ""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"}:
        return ""
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def request_control_page_origin(request: Any) -> str:
    headers = getattr(request, "headers", {}) or {}
    return normalize_request_origin(headers.get("Origin"))


def request_control_page_self_origin(request: Any) -> str:
    headers = getattr(request, "headers", {}) or {}
    host = str(headers.get("Host") or getattr(request, "host", "") or "").strip()
    scheme = str(getattr(request, "scheme", "http") or "http").strip().lower()
    return normalize_request_origin(f"{scheme}://{host}")


def origin_uses_loopback_host(origin: str) -> bool:
    try:
        hostname = (urlsplit(origin).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def request_control_page_host_is_allowed(
    request: Any,
    *,
    allowed_origins: frozenset[str] | None = None,
) -> bool:
    self_origin = request_control_page_self_origin(request)
    if not self_origin:
        return False
    if origin_uses_loopback_host(self_origin):
        return True
    configured = configured_control_page_origins() if allowed_origins is None else allowed_origins
    return self_origin in configured


def control_page_origin_is_allowed(request: Any, *, allowed_origins: frozenset[str] | None = None) -> bool:
    headers = getattr(request, "headers", {}) or {}
    raw_origin = str(headers.get("Origin") or "").strip()
    if not raw_origin:
        return True
    origin = request_control_page_origin(request)
    if not origin:
        return False
    if origin == request_control_page_self_origin(request) and origin_uses_loopback_host(origin):
        return True
    configured = configured_control_page_origins() if allowed_origins is None else allowed_origins
    return origin in configured


def add_control_page_cors_headers(
    response: web.StreamResponse,
    *,
    path: str,
    origin: str = "",
) -> web.StreamResponse:
    if control_page_api_cors_applies(path) and origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = f"Content-Type, {CONTROL_PAGE_CSRF_HEADER}"
        response.headers["Vary"] = "Origin"
    return response


def control_page_security_error(error: str, *, status: int = 403) -> web.Response:
    return control_page_json_response({"ok": False, "error": error}, status=status)


def memory_deletion_journal_integrity_response() -> web.Response:
    return add_control_page_no_store_headers(
        control_page_json_response(
            {
                "ok": False,
                "error": MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            },
            status=503,
        )
    )


def normalize_memory_deletion_journal_integrity_response(
    response: web.StreamResponse,
) -> web.StreamResponse:
    """Collapse result-shaped integrity failures at the HTTP boundary.

    Some memory helpers intentionally return public error dictionaries rather
    than raising. Treat the stable deletion-integrity code as a privileged
    sentinel and discard every sibling field before any response can leave
    either Control Page application.
    """

    if (
        not isinstance(response, web.Response)
        or response.content_type != "application/json"
    ):
        return response
    body = response.body
    if not isinstance(body, (bytes, bytearray, memoryview)):
        return response
    encoded = bytes(body)
    if MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR.encode("ascii") not in encoded:
        return response
    try:
        payload = json.loads(encoded.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return response
    if (
        isinstance(payload, dict)
        and payload.get("error")
        == MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
    ):
        return memory_deletion_journal_integrity_response()
    return response


async def control_page_session_handler(_: web.Request) -> web.StreamResponse:
    response = control_page_json_response(
        {
            "ok": True,
            "csrfToken": CONTROL_PAGE_CSRF_TOKEN,
            "csrfHeader": CONTROL_PAGE_CSRF_HEADER,
        }
    )
    return add_control_page_no_store_headers(response)


@web.middleware
async def control_page_cors_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    api_request = control_page_api_cors_applies(request.path)
    origin = request_control_page_origin(request)
    if api_request and not request_control_page_host_is_allowed(request):
        return control_page_security_error("host_not_allowed")
    if api_request and not control_page_origin_is_allowed(request):
        return control_page_security_error("origin_not_allowed")
    if request.method in CONTROL_PAGE_MUTATING_METHODS and api_request:
        supplied_token = str(request.headers.get(CONTROL_PAGE_CSRF_HEADER) or "")
        if not secrets.compare_digest(supplied_token, CONTROL_PAGE_CSRF_TOKEN):
            return control_page_security_error("csrf_token_required")
        content_type = str(request.headers.get("Content-Type") or "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            return control_page_security_error("json_content_type_required", status=415)
    if request.method == "OPTIONS" and api_request:
        response: web.StreamResponse = web.Response(status=204)
    else:
        try:
            response = await handler(request)
        except MemoryDeletionJournalIntegrityError:
            response = memory_deletion_journal_integrity_response()
    response = normalize_memory_deletion_journal_integrity_response(
        response
    )
    return add_control_page_cors_headers(response, path=request.path, origin=origin)


@web.middleware
async def reject_browser_origin_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if str(request.headers.get("Origin") or "").strip():
        return control_page_security_error("browser_origin_not_allowed")
    if request.method in CONTROL_PAGE_MUTATING_METHODS and request.path.startswith("/api/"):
        content_type = str(request.headers.get("Content-Type") or "").partition(";")[0].strip().lower()
        if content_type != "application/json":
            return control_page_security_error("json_content_type_required", status=415)
    try:
        response = await handler(request)
    except MemoryDeletionJournalIntegrityError:
        response = memory_deletion_journal_integrity_response()
    return normalize_memory_deletion_journal_integrity_response(response)


__all__ = [
    "CONTROL_PAGE_NO_STORE_HEADERS",
    "CONTROL_PAGE_CSRF_HEADER",
    "CONTROL_PAGE_CSRF_TOKEN",
    "add_control_page_cors_headers",
    "add_control_page_no_store_headers",
    "build_control_page_health_payload",
    "control_page_api_cors_applies",
    "control_page_origin_is_allowed",
    "request_control_page_host_is_allowed",
    "control_page_cors_middleware",
    "control_page_session_handler",
    "control_page_file_response",
    "control_page_json_response",
    "memory_deletion_journal_integrity_response",
    "normalize_memory_deletion_journal_integrity_response",
    "resolve_control_page_asset_path",
    "reject_browser_origin_middleware",
]
