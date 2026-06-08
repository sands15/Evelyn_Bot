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

from aiohttp import ClientSession, ClientTimeout, web

from .memory_vault import (
    ensure_memory_vault_layout,
    export_memory_graph,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    update_memory_vault_user_note,
)


PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
DOCS_DIR = PROJECT_ROOT / "docs"
ASSETS_DIR = DOCS_DIR / "assets"

HOST = os.getenv("CONTROL_PAGE_HOST", "127.0.0.1")
PORT = int(os.getenv("CONTROL_PAGE_PUBLIC_PORT", os.getenv("CONTROL_PAGE_PORT", "8799")))
BOT_API_HOST = os.getenv("CONTROL_PAGE_BOT_API_HOST", "127.0.0.1")
BOT_API_PORT = int(os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798"))
BOT_API_BASE = f"http://{BOT_API_HOST}:{BOT_API_PORT}"
PROXY_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_PROXY_TIMEOUT_SEC", "1.2"))
LOCAL_HELP_COMMANDS = {"/", "/help"}
LOCAL_STATUS_COMMANDS = {"/status"}
LOCAL_MEMORY_COMMANDS = {"/memory", "/obsidian"}
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
    except Exception:
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


def default_commands() -> list[dict[str, str]]:
    return [
        {"command": "/help", "template": "/help", "summary": "명령어 목록 보기", "visibility": "always", "group": "기본"},
        {"command": "/status", "template": "/status", "summary": "Evelyn local runtime 상태 보기", "visibility": "always", "group": "기본"},
        {"command": "/memory", "template": "/memory", "summary": "메모리 패널 열기/숨기기", "visibility": "always", "group": "페이지"},
        {"command": "/obsidian", "template": "/obsidian", "summary": "메모리 패널 열기/숨기기", "visibility": "always", "group": "페이지"},
        {"command": "/shutdown", "template": "/shutdown", "summary": "Evelyn local runtime 종료", "visibility": "always", "group": "시스템"},
    ]


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
        return "control page live | bot processor down"
    if services.get("main") and services.get("router") and services.get("sub") and services.get("tts"):
        return "control page live | bot processor ready"
    return "control page live | model services starting"


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
    phase = "전체 서버 준비 완료" if percent >= 100 else f"{current['label']} 대기 중"
    return {
        "percent": percent,
        "phase": phase,
        "ready": percent >= 100,
        "done": done_count,
        "total": len(steps),
        "source": "control_page_proxy",
        "steps": steps,
    }


async def current_boot_progress() -> dict[str, Any]:
    names = tuple(MODEL_PORTS.keys())
    results = await asyncio.gather(*(probe_port(MODEL_PORTS[name]) for name in names))
    ports = dict(zip(names, results))
    return {
        "ports": ports,
        "bootProgress": build_boot_progress_from_ports(ports),
    }


async def degraded_state() -> dict[str, Any]:
    progress_state = await current_boot_progress()
    ports = progress_state["ports"]
    boot_progress = progress_state["bootProgress"]
    return {
        "ok": False,
        "generatedAt": time.time(),
        "localUrl": f"http://{HOST}:{PORT}/",
        "bootProgress": boot_progress,
        "ui": {
            "mode": "default",
            "submode": "offline" if not ports.get("bot") else "idle",
            "reason": "bot_processor_unavailable" if not ports.get("bot") else "bot_processor_proxy_pending",
        },
        "commands": default_commands(),
        "allCommands": default_commands(),
        "chat": {
            "messages": [
                {
                    "role": "assistant",
                    "author": "Control",
                    "text": "Control page는 살아 있지만 Discord bot processor API가 아직 응답하지 않습니다.",
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
                "codexRequired": True,
                "codexBackend": "codex-gateway",
                "summary": service_summary(ports),
            },
            "bootProgress": boot_progress,
        },
        "minecraft": {
            "running": bool(ports.get("voyager")),
            "connected": False,
            "sessionActive": False,
            "goal": "없음",
            "stage": "없음",
            "task": "없음",
            "taskStage": "없음",
            "progress": "Bot processor API 대기 중",
            "position": "미확인",
            "inventorySummary": "인벤토리 정보 없음",
            "inventoryTop": [],
            "inventorySlots": [],
            "recentActivity": [],
            "snapshotStale": True,
            "snapshotExpired": False,
            "idleSummary": "Control page는 살아 있지만 bot processor가 아직 붙지 않았습니다.",
        },
        "statusText": "Control page live. Discord bot processor API is unavailable.",
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


@web.middleware
async def cors_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    if request.method == "OPTIONS" and request.path.startswith("/api/control-page/"):
        response: web.StreamResponse = web.Response(status=204)
    else:
        response = await handler(request)
    if request.path.startswith("/api/control-page/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


async def index_handler(_: web.Request) -> web.StreamResponse:
    index_path = DOCS_DIR / "index.html"
    if not index_path.exists():
        raise web.HTTPNotFound(text="control page index not found")
    response = web.FileResponse(index_path)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
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
    return response


async def state_handler(request: web.Request) -> web.StreamResponse:
    proxied = await proxy_json(request, "GET", "/api/control-page/state")
    if proxied is not None and proxied.status < 500:
        try:
            payload = json.loads(proxied.text or "{}")
            if isinstance(payload, dict):
                progress_state = await current_boot_progress()
                ports = progress_state["ports"]
                boot_progress = progress_state["bootProgress"]
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
                        "summary": service_summary(ports),
                    }
                )
                runtime["services"] = services
                runtime["bootProgress"] = boot_progress
                payload["runtime"] = runtime
                payload["bootProgress"] = boot_progress
                return json_response(payload, status=proxied.status)
        except Exception:
            return proxied
        return proxied
    return json_response(await degraded_state())


async def health_handler(_: web.Request) -> web.StreamResponse:
    bot_ready = await probe_port(BOT_API_PORT)
    return json_response({"ok": True, "role": "control-page", "botProxyReady": bot_ready, "botApiPort": BOT_API_PORT})


async def shutdown_handler(_: web.Request) -> web.StreamResponse:
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
    return json_response(export_memory_graph(max_nodes=max_nodes))


async def memory_snapshot_handler(request: web.Request) -> web.StreamResponse:
    include_hidden = str(request.query.get("include_hidden", "")).lower() in {"1", "true", "yes", "on"}
    try:
        limit = int(request.query.get("limit", "80"))
    except Exception:
        limit = 80
    return json_response(memory_vault_user_snapshot(include_hidden=include_hidden, limit=limit))


async def memory_note_handler(request: web.Request) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    result = memory_vault_user_note(note_id)
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
    )
    return json_response(result, status=200 if result.get("ok") else 404)


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
        state = await degraded_state()
        runtime = state.get("runtime") if isinstance(state, dict) else {}
        services = runtime.get("services") if isinstance(runtime, dict) else {}
        summary = services.get("summary") if isinstance(services, dict) else ""
        return json_response({"ok": True, "reply": str(summary or state.get("statusText") or "Control page live."), "state": state})
    if normalized == "/memory":
        state = await degraded_state()
        state["controlPagePanels"] = {
            "revision": int(time.time() * 1000),
            "commands": [
                {
                    "id": int(time.time() * 1000),
                    "action": "toggle",
                    "panel": "memory",
                    "at": time.time(),
                }
            ],
            "panels": [
                {"id": "runtime", "label": "Runtime"},
                {"id": "diagnostics", "label": "Diagnostics"},
                {"id": "avatar", "label": "Avatar"},
                {"id": "chat", "label": "Chat"},
                {"id": "memory", "label": "Memory"},
            ],
        }
        return json_response({"ok": True, "reply": "메모리 패널을 열거나 숨길게.", "state": state})
    if normalized == "/obsidian":
        state = await degraded_state()
        result = open_memory_vault_payload()
        state["memoryVaultOpen"] = result
        reply = result.get("message") if result.get("ok") else f"Obsidian open failed: {result.get('message')}"
        return json_response(
            {"ok": bool(result.get("ok")), "reply": reply, "state": state, "openResult": result},
            status=200 if result.get("ok") else 500,
        )
    if normalized in LOCAL_SHUTDOWN_COMMANDS:
        ok, detail = schedule_local_stack_shutdown()
        state = await degraded_state()
        if ok:
            return json_response(
                {
                    "ok": True,
                    "reply": "Local Evelyn shutdown started. This works even when the bot processor is down.",
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
    state = await degraded_state()
    return json_response(
        {
            "ok": False,
            "error": "bot_processor_unavailable",
            "reply": "Discord bot processor API가 꺼져 있어서 명령을 전달할 수 없습니다.",
            "state": state,
        }
    )


async def icon_handler(request: web.Request) -> web.StreamResponse:
    item_name = request.match_info.get("item_name", "")
    proxied = await proxy_raw(request, f"/api/control-page/minecraft-item-icon/{item_name}")
    if proxied is not None and proxied.status == 200:
        return proxied
    raise web.HTTPNotFound(text="item icon not available")


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get("/", index_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/assets/{asset_path:.*}", asset_handler)
    app.router.add_get("/api/control-page/state", state_handler)
    app.router.add_get("/api/control-page/memory", memory_snapshot_handler)
    app.router.add_get("/api/control-page/memory-graph", memory_graph_handler)
    app.router.add_get("/api/control-page/memory/{note_id}", memory_note_handler)
    app.router.add_post("/api/control-page/open-memory-vault", open_memory_vault_handler)
    app.router.add_post("/api/control-page/memory/{note_id}", memory_note_action_handler)
    app.router.add_post("/api/control-page/shutdown", shutdown_handler)
    app.router.add_post("/api/control-page/chat", chat_handler)
    app.router.add_get("/api/control-page/minecraft-item-icon/{item_name}", icon_handler)
    app.router.add_options("/api/control-page/state", state_handler)
    app.router.add_options("/api/control-page/memory", memory_snapshot_handler)
    app.router.add_options("/api/control-page/memory-graph", memory_graph_handler)
    app.router.add_options("/api/control-page/memory/{note_id}", memory_note_handler)
    app.router.add_options("/api/control-page/open-memory-vault", open_memory_vault_options_handler)
    app.router.add_options("/api/control-page/shutdown", shutdown_handler)
    app.router.add_options("/api/control-page/chat", chat_handler)
    return app


def main() -> None:
    web.run_app(create_app(), host=HOST, port=PORT, access_log=None)


if __name__ == "__main__":
    main()
