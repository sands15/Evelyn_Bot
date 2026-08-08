from __future__ import annotations

import argparse
import asyncio
import contextlib
import ctypes
import itertools
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from evelyn_core.codex_gateway_auth import AUTHORIZATION_HEADER, gateway_request_authorized, resolve_gateway_token
from evelyn_core.codex_gateway_credentials import prepare_codex_credentials
from evelyn_core.paths import get_repo_root, get_runtime_artifacts_root
from evelyn_core.runtime_config_schema import (
    CODEX_GATEWAY_SETTINGS,
    load_runtime_settings,
)
from evelyn_core.runtime_error_observability import RuntimeErrorCounter

REPO_ROOT = get_repo_root()
RUNTIME_ARTIFACTS_ROOT = get_runtime_artifacts_root()
SOURCE_IMAGE_ROOT = Path(__file__).resolve().parents[3]
_GATEWAY_CONFIG = load_runtime_settings("codex_gateway", CODEX_GATEWAY_SETTINGS)
_RUNTIME_ERRORS = RuntimeErrorCounter()
_CREDENTIAL_STATUS: dict[str, Any] = {
    "ready": False,
    "mode": "not-initialized",
    "errorCode": "codex_credentials_not_initialized",
    "authPresent": False,
    "configPresent": False,
}
DEFAULT_HOST = str(_GATEWAY_CONFIG["VOYAGER_CODEX_GATEWAY_HOST"])
DEFAULT_PORT = int(_GATEWAY_CONFIG["VOYAGER_CODEX_GATEWAY_PORT"])
DEFAULT_MODEL = str(_GATEWAY_CONFIG["VOYAGER_CODEX_MODEL"])
DEFAULT_TIMEOUT_SEC = float(_GATEWAY_CONFIG["VOYAGER_CODEX_GATEWAY_TIMEOUT_SEC"])
DEFAULT_WORKDIR = str(
    _GATEWAY_CONFIG["VOYAGER_CODEX_GATEWAY_WORKDIR"]
    or (Path(tempfile.gettempdir()) / "evelyn-codex-action-workspace")
)
ISOLATED_RUNTIME_ENABLED = str(
    os.getenv("EVELYN_CODEX_GATEWAY_ISOLATED_RUNTIME") or ""
).strip().lower() in {"1", "true", "yes", "on"}
TOOLLESS_RUNTIME_VERIFIED = bool(
    _GATEWAY_CONFIG["EVELYN_CODEX_GATEWAY_TOOLLESS_RUNTIME_VERIFIED"]
)
DEFAULT_BACKEND_MODE = str(
    _GATEWAY_CONFIG["VOYAGER_CODEX_GATEWAY_BACKEND"]
).strip().lower()
LAST_REQUEST_STATUS_PATH = RUNTIME_ARTIFACTS_ROOT / "codex_gateway" / "last_request.json"
GATEWAY_ERROR_LOG_PATH = RUNTIME_ARTIFACTS_ROOT / "codex_gateway" / "errors.log"
_GATEWAY_STATUS_LINE_LENGTH = 0
_VT_MODE_ENABLED: bool | None = None
_ALT_SCREEN_ENABLED = False
_GATEWAY_NOTICE = "waiting for action request"
_QUEUE: asyncio.PriorityQueue[tuple[int, int, "GatewayQueueItem"]] | None = None
_QUEUE_WORKER: asyncio.Task[None] | None = None
_QUEUE_SEQUENCE = itertools.count()
_ACTIVE_QUEUE_ITEM: dict[str, Any] | None = None
ACTION_TOKEN_KEY = web.AppKey("codex_gateway_action_token", str)
QUEUE_WORKER_KEY = web.AppKey("codex_gateway_queue_worker", asyncio.Task)


@dataclass
class GatewayQueueItem:
    prompt: str
    model: str
    timeout_sec: float
    cwd: str
    source: str
    priority: int
    enqueued_at: float
    future: asyncio.Future[str]


def _set_console_title(text: str) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(str(text)[:240])
    except Exception:
        pass


def _append_error_log(path: Path, source: str, message: str, details: str | None = None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{stamp}] {source}: {message}\n")
            if details:
                handle.write(f"{details}\n")
            handle.write("\n")
    except Exception:
        pass


def _enable_vt_mode() -> bool:
    global _VT_MODE_ENABLED
    if _VT_MODE_ENABLED is not None:
        return _VT_MODE_ENABLED
    if os.name != "nt":
        _VT_MODE_ENABLED = True
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _VT_MODE_ENABLED = False
            return False
        enable_vt = 0x0004
        if mode.value & enable_vt:
            _VT_MODE_ENABLED = True
            return True
        _VT_MODE_ENABLED = bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
        return _VT_MODE_ENABLED
    except Exception:
        _VT_MODE_ENABLED = False
        return False


def _enter_alternate_screen() -> None:
    global _ALT_SCREEN_ENABLED
    if _ALT_SCREEN_ENABLED:
        return
    if _enable_vt_mode():
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
        _ALT_SCREEN_ENABLED = True


def _leave_alternate_screen() -> None:
    global _ALT_SCREEN_ENABLED
    if not _ALT_SCREEN_ENABLED:
        return
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()
    _ALT_SCREEN_ENABLED = False


def _write_status_line(block: str) -> None:
    global _GATEWAY_STATUS_LINE_LENGTH
    if _enable_vt_mode():
        _enter_alternate_screen()
        sys.stdout.write("\033[H\033[2J" + block.rstrip("\n"))
        sys.stdout.flush()
        return
    if os.name == "nt":
        os.system("cls")
        sys.stdout.write(block.rstrip("\n"))
        sys.stdout.flush()
        return
    padded = block
    if _GATEWAY_STATUS_LINE_LENGTH > len(block):
        padded = block + (" " * (_GATEWAY_STATUS_LINE_LENGTH - len(block)))
    _GATEWAY_STATUS_LINE_LENGTH = len(block)
    sys.stdout.write("\r" + padded)
    sys.stdout.flush()


def _render_gateway_block(payload: dict[str, Any] | None = None, notice: str | None = None) -> str:
    current = payload or _load_last_request_status() or {}
    phase = current.get("phase") or "idle"
    model = current.get("model") or DEFAULT_MODEL
    prompt_chars = current.get("prompt_chars") or "-"
    elapsed = current.get("elapsed_sec") or "-"
    output_chars = current.get("output_chars") or "-"
    pid = current.get("pid") or "-"
    lines = [
        "==================== Minecraft Status ====================",
        "Connection : codex gateway ready",
        f"State      : {notice or _GATEWAY_NOTICE}",
        f"Phase      : {phase}",
        f"Model      : {model}",
        f"Prompt     : {prompt_chars} chars",
        f"Elapsed    : {elapsed}s",
        f"Output     : {output_chars} chars",
        f"PID        : {pid}",
        f"Errors     : {GATEWAY_ERROR_LOG_PATH}",
    ]
    return "\n".join(lines) + "\n"


def _announce_gateway_status(message: str) -> None:
    global _GATEWAY_NOTICE
    _GATEWAY_NOTICE = message
    _write_status_line(_render_gateway_block(notice=message))
    _set_console_title("Codex-Gateway | Minecraft status board")


def _backend_command() -> str:
    if not bool(_GATEWAY_CONFIG["EVELYN_ALLOW_CUSTOM_GATEWAY_COMMAND"]):
        return ""
    return str(_GATEWAY_CONFIG["VOYAGER_CODEX_GATEWAY_COMMAND"] or "").strip()


def _backend_mode(env: dict[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    mode = str(source.get("VOYAGER_CODEX_GATEWAY_BACKEND") or DEFAULT_BACKEND_MODE or "codex-exec").strip().lower()
    aliases = {
        "cli": "codex-exec",
        "codex": "codex-exec",
    }
    resolved = aliases.get(mode, mode)
    if resolved != "codex-exec":
        return "codex-exec"
    return resolved


def _backend_label(env: dict[str, str] | None = None) -> str:
    return _backend_mode(env)


def _request_priority(payload: dict[str, Any], source: str) -> int:
    raw_priority = payload.get("priority")
    try:
        return max(0, int(raw_priority))
    except (TypeError, ValueError):
        pass

    normalized = " ".join(
        str(value or "").strip().lower()
        for value in (
            source,
            payload.get("kind"),
            payload.get("request_type"),
            payload.get("route"),
        )
    )
    if any(token in normalized for token in ("interactive", "user", "control", "manual")):
        return 0
    if "recovery" in normalized or "health" in normalized:
        return 10
    if "background" in normalized or "learning" in normalized:
        return 100
    return 50


def _safe_request_label(value: Any, *, fallback: str, allow_slash: bool = False) -> str:
    pattern = r"[^A-Za-z0-9_.:/-]+" if allow_slash else r"[^A-Za-z0-9_.-]+"
    cleaned = re.sub(pattern, "_", str(value or "").strip()).strip("._-")
    return cleaned[:80] or fallback


def _request_timeout(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("codex_timeout_invalid") from exc
    if not math.isfinite(timeout) or not 1.0 <= timeout <= 1800.0:
        raise ValueError("codex_timeout_invalid")
    return timeout


def _load_last_request_status() -> dict[str, Any] | None:
    try:
        payload = json.loads(LAST_REQUEST_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _public_last_request(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    allowed_keys = (
        "phase",
        "backend",
        "model",
        "started_at",
        "finished_at",
        "elapsed_sec",
        "returncode",
        "prompt_chars",
        "output_chars",
        "source",
        "priority",
        "queue_size",
    )
    return {
        key: payload[key]
        for key in allowed_keys
        if key in payload
    }


def initialize_codex_credentials() -> dict[str, Any]:
    global _CREDENTIAL_STATUS
    _CREDENTIAL_STATUS = prepare_codex_credentials()
    if not _CREDENTIAL_STATUS.get("ready"):
        _RUNTIME_ERRORS.record("codex_credentials_failed")
    return dict(_CREDENTIAL_STATUS)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _isolated_action_workdir(value: Any = None) -> str:
    if value not in (None, ""):
        raise ValueError("codex_workdir_override_forbidden")
    if not ISOLATED_RUNTIME_ENABLED:
        raise RuntimeError("codex_isolated_runtime_required")

    configured = Path(DEFAULT_WORKDIR).expanduser()
    if not configured.is_absolute() or configured.is_symlink():
        raise RuntimeError("codex_isolated_workspace_unavailable")
    try:
        workspace = configured.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("codex_isolated_workspace_unavailable") from exc
    if not workspace.is_dir():
        raise RuntimeError("codex_isolated_workspace_unavailable")
    try:
        if any(workspace.iterdir()):
            raise RuntimeError("codex_isolated_workspace_not_empty")
    except OSError as exc:
        raise RuntimeError("codex_isolated_workspace_unavailable") from exc

    protected_roots = (
        SOURCE_IMAGE_ROOT.resolve(),
        RUNTIME_ARTIFACTS_ROOT.resolve(),
        Path(os.getenv("EVELYN_CODEX_CREDENTIALS_DIR") or ".").expanduser().resolve(),
        Path(os.getenv("CODEX_HOME") or ".").expanduser().resolve(),
    )
    if any(_paths_overlap(workspace, root) for root in protected_roots):
        raise RuntimeError("codex_isolated_workspace_overlaps_private_root")
    if any(
        (SOURCE_IMAGE_ROOT / name).exists()
        for name in (".git", ".env", "bot_memory", "docs", "runtime_artifacts")
    ):
        raise RuntimeError("codex_isolated_runtime_required")
    return str(workspace)


def _resolve_request_workdir(value: Any) -> str:
    return _isolated_action_workdir(value)


def _write_last_request_status(payload: dict[str, Any]) -> None:
    LAST_REQUEST_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_REQUEST_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    phase = payload.get("phase")
    if phase == "backend_running":
        _announce_gateway_status("generating action code")
    elif phase == "success":
        elapsed = payload.get("elapsed_sec")
        _announce_gateway_status(f"action code ready ({elapsed}s)")
    elif phase == "timeout":
        _announce_gateway_status("codex request timed out")
        _append_error_log(GATEWAY_ERROR_LOG_PATH, "codex_gateway", "codex request timed out")
    elif phase == "error":
        _announce_gateway_status("codex request failed")
        _append_error_log(
            GATEWAY_ERROR_LOG_PATH,
            "codex_gateway",
            "codex request failed",
        )
    elif phase == "empty_output":
        _announce_gateway_status("codex returned empty output")
        _append_error_log(
            GATEWAY_ERROR_LOG_PATH,
            "codex_gateway",
            "codex returned empty output",
        )
    elif phase == "queued":
        queue_size = payload.get("queue_size")
        priority = payload.get("priority")
        _announce_gateway_status(f"queued request priority={priority} queue={queue_size}")
    elif phase == "starting":
        _announce_gateway_status("waiting for action request")


def _gateway_status() -> dict[str, Any]:
    queue = _QUEUE
    backend_status = _backend_readiness()
    last_request = _load_last_request_status()
    last_action_ready = _last_action_ready(last_request)
    return {
        "ok": True,
        "service": "voyager_codex_gateway",
        "configured": True,
        "backend": _backend_label(),
        "backendReady": bool(backend_status.get("ready")),
        "backendStatus": backend_status,
        "isolatedRuntime": bool(ISOLATED_RUNTIME_ENABLED),
        "toolAccessVerified": bool(TOOLLESS_RUNTIME_VERIFIED),
        "credentials": dict(_CREDENTIAL_STATUS),
        "configuration": _GATEWAY_CONFIG.public_summary(),
        "lastActionReady": last_action_ready,
        "route": "/codex/action",
        "actionAuthRequired": True,
        "actionAuthScheme": "Bearer",
        "queue": {
            "mode": "priority-serial",
            "size": queue.qsize() if queue is not None else 0,
            "active": _ACTIVE_QUEUE_ITEM,
        },
        "lastRequest": _public_last_request(last_request),
        **_RUNTIME_ERRORS.snapshot(),
    }


def _resolve_codex_cli() -> str:
    explicit = _GATEWAY_CONFIG["VOYAGER_CODEX_CLI"]
    if explicit is not None:
        candidate = Path(explicit)
        if candidate.exists():
            return str(candidate)
        raise RuntimeError("configured Codex CLI is unavailable")

    for name in ("codex", "codex.cmd", "codex.exe"):
        found = shutil.which(name)
        if found:
            return found

    appdata = os.getenv("APPDATA", "")
    if appdata:
        npm_dir = Path(appdata) / "npm"
        for name in ("codex.cmd", "codex.exe", "codex"):
            candidate = npm_dir / name
            if candidate.exists():
                return str(candidate)

    raise RuntimeError(
        "Codex CLI executable not found. Install Codex or set VOYAGER_CODEX_CLI to the full path."
    )


def _backend_readiness() -> dict[str, Any]:
    command = _backend_command()
    if command:
        return {
            "ready": False,
            "backend": _backend_mode(),
            "commandConfigured": True,
            "errorCode": "codex_custom_backend_forbidden",
        }
    if not TOOLLESS_RUNTIME_VERIFIED:
        return {
            "ready": False,
            "backend": _backend_mode(),
            "commandConfigured": False,
            "errorCode": "codex_toolless_runtime_unverified",
        }
    if not _CREDENTIAL_STATUS.get("ready"):
        return {
            "ready": False,
            "backend": _backend_mode(),
            "commandConfigured": False,
            "errorCode": str(
                _CREDENTIAL_STATUS.get("errorCode")
                or "codex_credentials_unavailable"
            ),
        }
    try:
        _isolated_action_workdir()
    except RuntimeError as exc:
        return {
            "ready": False,
            "backend": _backend_mode(),
            "commandConfigured": False,
            "errorCode": str(exc),
        }
    try:
        _resolve_codex_cli()
    except Exception:
        return {
            "ready": False,
            "backend": _backend_mode(),
            "commandConfigured": False,
            "errorCode": "codex_cli_unavailable",
        }
    return {
        "ready": True,
        "backend": _backend_mode(),
        "commandConfigured": False,
        "cliAvailable": True,
    }


def _last_action_ready(last_request: dict[str, Any] | None) -> bool | None:
    if not last_request:
        return None
    phase = str(last_request.get("phase") or "").strip().lower()
    if phase == "success":
        return True
    if phase in {"error", "timeout", "empty_output", "handler_exception"}:
        return False
    return None


def _strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


async def _run_backend(prompt: str, model: str, timeout_sec: float, cwd: str) -> str:
    command = _backend_command()
    if command:
        raise RuntimeError("codex_custom_backend_forbidden")
    if not TOOLLESS_RUNTIME_VERIFIED:
        raise RuntimeError("codex_toolless_runtime_unverified")
    if not _CREDENTIAL_STATUS.get("ready"):
        raise RuntimeError("codex_credentials_unavailable")
    isolated_cwd = _isolated_action_workdir()
    if Path(cwd).resolve() != Path(isolated_cwd):
        raise RuntimeError("codex_workdir_override_forbidden")
    env = {
        name: os.environ[name]
        for name in (
            "CODEX_HOME",
            "HOME",
            "LANG",
            "PATH",
            "SSL_CERT_FILE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
        )
        if os.environ.get(name)
    }
    started_at = time.time()
    status_payload = {
        "started_at": started_at,
        "phase": "starting",
        "backend": _backend_label(env),
        "model": model,
        "timeout_sec": timeout_sec,
        "prompt_chars": len(prompt),
    }
    _write_last_request_status(status_payload)

    codex_cli = _resolve_codex_cli()
    proc = await asyncio.create_subprocess_exec(
        codex_cli,
        "exec",
        "-m",
        model,
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-c",
        "approval_policy=\"never\"",
        "-c",
        "features.shell_tool=false",
        "-c",
        "features.unified_exec=false",
        "-c",
        "features.multi_agent=false",
        "-c",
        "features.apps=false",
        "-c",
        "web_search=\"disabled\"",
        "-",
        cwd=isolated_cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdin_data = prompt.encode("utf-8")

    status_payload.update({
        "phase": "backend_running",
        "pid": proc.pid,
    })
    _write_last_request_status(status_payload)

    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(stdin_data),
            timeout=max(1.0, timeout_sec),
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        status_payload.update({
            "phase": "timeout",
            "finished_at": time.time(),
            "elapsed_sec": round(time.time() - started_at, 3),
        })
        _write_last_request_status(status_payload)
        raise RuntimeError(f"Codex gateway backend timed out after {timeout_sec:.0f}s") from exc

    output = stdout.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        status_payload.update({
            "phase": "error",
            "finished_at": time.time(),
            "elapsed_sec": round(time.time() - started_at, 3),
            "returncode": proc.returncode,
        })
        _write_last_request_status(status_payload)
        raise RuntimeError("codex_backend_process_failed")
    if not output:
        status_payload.update({
            "phase": "empty_output",
            "finished_at": time.time(),
            "elapsed_sec": round(time.time() - started_at, 3),
            "returncode": proc.returncode,
        })
        _write_last_request_status(status_payload)
        raise RuntimeError("codex_backend_empty_output")
    cleaned = _strip_outer_fence(output)
    status_payload.update({
        "phase": "success",
        "finished_at": time.time(),
        "elapsed_sec": round(time.time() - started_at, 3),
        "returncode": proc.returncode,
        "output_chars": len(cleaned),
    })
    _write_last_request_status(status_payload)
    return cleaned


async def _queue_worker() -> None:
    global _ACTIVE_QUEUE_ITEM
    assert _QUEUE is not None
    while True:
        priority, sequence, item = await _QUEUE.get()
        _ACTIVE_QUEUE_ITEM = {
            "source": item.source,
            "priority": priority,
            "sequence": sequence,
            "queued_sec": round(time.time() - item.enqueued_at, 3),
        }
        try:
            if item.future.cancelled():
                continue
            try:
                result = await _run_backend(item.prompt, item.model, item.timeout_sec, item.cwd)
            except Exception as exc:
                if not item.future.cancelled():
                    item.future.set_exception(exc)
            else:
                if not item.future.cancelled():
                    item.future.set_result(result)
        finally:
            _ACTIVE_QUEUE_ITEM = None
            _QUEUE.task_done()


async def _start_queue(app: web.Application) -> None:
    global _QUEUE, _QUEUE_WORKER
    _QUEUE = asyncio.PriorityQueue()
    _QUEUE_WORKER = asyncio.create_task(_queue_worker())
    app[QUEUE_WORKER_KEY] = _QUEUE_WORKER


async def _stop_queue(app: web.Application) -> None:
    global _QUEUE_WORKER
    worker = app.get(QUEUE_WORKER_KEY) or _QUEUE_WORKER
    if worker is None:
        return
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker
    _QUEUE_WORKER = None


async def _submit_backend(
    *,
    prompt: str,
    model: str,
    timeout_sec: float,
    cwd: str,
    source: str,
    priority: int,
) -> str:
    if _QUEUE is None:
        return await _run_backend(prompt, model, timeout_sec, cwd)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()
    sequence = next(_QUEUE_SEQUENCE)
    item = GatewayQueueItem(
        prompt=prompt,
        model=model,
        timeout_sec=timeout_sec,
        cwd=cwd,
        source=source,
        priority=priority,
        enqueued_at=time.time(),
        future=future,
    )
    await _QUEUE.put((priority, sequence, item))
    _write_last_request_status({
        "started_at": item.enqueued_at,
        "phase": "queued",
        "backend": _backend_label(),
        "model": model,
        "timeout_sec": timeout_sec,
        "prompt_chars": len(prompt),
        "source": source,
        "priority": priority,
        "queue_size": _QUEUE.qsize(),
        "sequence": sequence,
    })
    return await future


async def health(_: web.Request) -> web.Response:
    return web.json_response(_gateway_status())


async def status(_: web.Request) -> web.Response:
    return web.json_response(_gateway_status())


async def codex_action(request: web.Request) -> web.Response:
    if not gateway_request_authorized(request.headers.get(AUTHORIZATION_HEADER), request.app[ACTION_TOKEN_KEY]):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if not TOOLLESS_RUNTIME_VERIFIED:
        return web.json_response(
            {"ok": False, "error": "codex_toolless_runtime_unverified"},
            status=503,
        )

    try:
        payload = await request.json() if request.can_read_body else {}
    except Exception:
        payload = {}

    prompt = str((payload or {}).get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"ok": False, "error": "prompt is empty"}, status=400)

    model = _safe_request_label(
        (payload or {}).get("model") or DEFAULT_MODEL,
        fallback=DEFAULT_MODEL,
        allow_slash=True,
    )
    try:
        timeout_sec = _request_timeout(
            (payload or {}).get("timeout_sec") or DEFAULT_TIMEOUT_SEC
        )
    except ValueError as exc:
        return web.json_response(
            {"ok": False, "error": str(exc)},
            status=400,
        )
    try:
        cwd = _resolve_request_workdir((payload or {}).get("cwd"))
    except ValueError as exc:
        return web.json_response(
            {"ok": False, "error": str(exc)},
            status=400,
        )
    except RuntimeError:
        return web.json_response(
            {"ok": False, "error": "codex_isolated_runtime_unavailable"},
            status=503,
        )
    source = _safe_request_label(
        (payload or {}).get("source")
        or (payload or {}).get("request_type")
        or "voyager-action",
        fallback="voyager-action",
    )
    priority = _request_priority(payload or {}, source)

    try:
        content = await _submit_backend(
            prompt=prompt,
            model=model,
            timeout_sec=timeout_sec,
            cwd=cwd,
            source=source,
            priority=priority,
        )
    except RuntimeError as exc:
        _RUNTIME_ERRORS.record("codex_backend_failed", exc)
        return web.json_response(
            {"ok": False, "error": "codex_backend_failed"},
            status=500,
        )
    except Exception as exc:
        _RUNTIME_ERRORS.record("codex_handler_failed", exc)
        _write_last_request_status({
            "started_at": time.time(),
            "phase": "handler_exception",
            "errorType": type(exc).__name__,
            "model": model,
            "timeout_sec": timeout_sec,
            "prompt_chars": len(prompt),
        })
        _append_error_log(
            GATEWAY_ERROR_LOG_PATH,
            "codex_gateway_handler",
            type(exc).__name__,
        )
        return web.json_response(
            {"ok": False, "error": "codex_handler_failed"},
            status=500,
        )

    return web.json_response({"ok": True, "content": content, "source": source, "priority": priority})


def build_app(*, action_token: str | None = None) -> web.Application:
    app = web.Application()
    app[ACTION_TOKEN_KEY] = str(action_token or resolve_gateway_token(create=True))
    app.on_startup.append(_start_queue)
    app.on_cleanup.append(_stop_queue)
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_post("/codex/action", codex_action)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    initialize_codex_credentials()
    _announce_gateway_status(f"HTTP ready at http://{args.host}:{args.port}")
    _announce_gateway_status("waiting for action request")
    try:
        web.run_app(build_app(), host=args.host, port=args.port, handle_signals=True, print=None)
    finally:
        _leave_alternate_screen()
        sys.stdout.write("\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
