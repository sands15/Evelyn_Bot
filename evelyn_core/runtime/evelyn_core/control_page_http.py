from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web


CONTROL_PAGE_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


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


def add_control_page_cors_headers(response: web.StreamResponse, *, path: str) -> web.StreamResponse:
    if control_page_api_cors_applies(path):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@web.middleware
async def control_page_cors_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    if request.method == "OPTIONS" and control_page_api_cors_applies(request.path):
        response: web.StreamResponse = web.Response(status=204)
    else:
        response = await handler(request)
    return add_control_page_cors_headers(response, path=request.path)


__all__ = [
    "CONTROL_PAGE_NO_STORE_HEADERS",
    "add_control_page_cors_headers",
    "add_control_page_no_store_headers",
    "build_control_page_health_payload",
    "control_page_api_cors_applies",
    "control_page_cors_middleware",
    "control_page_file_response",
    "control_page_json_response",
    "resolve_control_page_asset_path",
]
