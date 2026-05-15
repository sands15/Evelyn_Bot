from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from prompt_templates import build_action_prompt

APP_ROOT = Path(__file__).resolve().parent
LOG_DIR = APP_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
REQUEST_LOG_PATH = LOG_DIR / "gateway_requests.jsonl"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_ENDPOINT = "/codex/action"

app = FastAPI(title="Codex Gateway", version="1.0.0")


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    model: str = DEFAULT_MODEL
    timeout_sec: int = 240


class GenerateResponse(BaseModel):
    ok: bool
    content: str = ""
    error: str = ""
    raw_stdout: str = ""


def extract_js_code(text: str) -> str:
    match = re.search(r"```(?:javascript|js)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text.strip()


def append_log(entry: dict[str, Any]) -> None:
    safe = dict(entry)
    safe.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with REQUEST_LOG_PATH.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(safe, ensure_ascii=False) + "\n")


def find_codex_executable() -> str | None:
    return shutil.which("codex")


def build_codex_command(wrapped_prompt: str, model: str) -> list[str]:
    codex_executable = find_codex_executable() or "codex"
    return [
        codex_executable,
        "exec",
        "--skip-git-repo-check",
        "--model",
        model,
        "--sandbox",
        "read-only",
        "-",
    ]


def check_codex_login_status(timeout_sec: int = 10) -> dict[str, Any]:
    codex_executable = find_codex_executable()
    if not codex_executable:
        return {
            "logged_in": False,
            "status_ok": False,
            "error": "codex CLI not found",
        }
    try:
        result = subprocess.run(
            [codex_executable, "login", "status"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
            cwd=str(APP_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {
            "logged_in": False,
            "status_ok": False,
            "error": f"codex login status timed out after {timeout_sec}s",
        }
    except Exception as exc:
        return {
            "logged_in": False,
            "status_ok": False,
            "error": repr(exc),
        }

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    combined = "\n".join(part for part in [stdout, stderr] if part).strip()
    logged_in = result.returncode == 0 and "not logged in" not in combined.lower()
    return {
        "logged_in": logged_in,
        "status_ok": result.returncode == 0,
        "returncode": result.returncode,
        "message": combined,
    }


def run_codex_generate(req: GenerateRequest) -> GenerateResponse:
    wrapped_prompt = build_action_prompt(req.prompt)
    command = build_codex_command(wrapped_prompt, req.model)
    append_log({
        "event": "request",
        "model": req.model,
        "timeout_sec": req.timeout_sec,
        "prompt_preview": req.prompt[:400],
        "command": command[:-1] + ["<prompt elided>"],
    })
    login = check_codex_login_status()
    if not login.get("logged_in"):
        return GenerateResponse(ok=False, error=login.get("message") or login.get("error") or "codex is not logged in")
    try:
        result = subprocess.run(
            command,
            input=wrapped_prompt,
            capture_output=True,
            text=True,
            timeout=req.timeout_sec,
            encoding="utf-8",
            errors="replace",
            cwd=str(APP_ROOT),
        )
    except FileNotFoundError as exc:
        append_log({"event": "error", "error": repr(exc)})
        return GenerateResponse(ok=False, error="codex CLI not found. Run `codex login` after installing Codex CLI.")
    except subprocess.TimeoutExpired as exc:
        append_log({"event": "timeout", "error": repr(exc)})
        return GenerateResponse(ok=False, error=f"codex exec timed out after {req.timeout_sec}s")
    except Exception as exc:
        append_log({"event": "error", "error": repr(exc)})
        return GenerateResponse(ok=False, error=repr(exc))

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    append_log({
        "event": "response",
        "returncode": result.returncode,
        "stdout_preview": stdout[:1200],
        "stderr_preview": stderr[:1200],
    })
    if result.returncode != 0:
        return GenerateResponse(ok=False, error=stderr or f"codex exec failed with code {result.returncode}", raw_stdout=stdout)

    js_code = extract_js_code(stdout)
    if not js_code:
        return GenerateResponse(ok=False, error="codex returned empty output", raw_stdout=stdout)
    return GenerateResponse(ok=True, content=js_code, raw_stdout=stdout)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "codex_gateway", "endpoint": DEFAULT_ENDPOINT}


@app.get("/ready")
def ready() -> dict[str, Any]:
    codex_path = find_codex_executable()
    login = check_codex_login_status()
    return {
        "ok": True,
        "service": "codex_gateway",
        "endpoint": DEFAULT_ENDPOINT,
        "ready": bool(codex_path) and bool(login.get("logged_in")),
        "codex_cli_found": bool(codex_path),
        "codex_cli_path": codex_path,
        "codex_logged_in": bool(login.get("logged_in")),
        "codex_login_status_ok": bool(login.get("status_ok")),
        "codex_login_message": login.get("message") or login.get("error"),
        "default_model": DEFAULT_MODEL,
    }


@app.post("/generate_js", response_model=GenerateResponse)
def generate_js(req: GenerateRequest) -> GenerateResponse:
    return run_codex_generate(req)


@app.post(DEFAULT_ENDPOINT, response_model=GenerateResponse)
def codex_action(req: GenerateRequest) -> GenerateResponse:
    return run_codex_generate(req)
