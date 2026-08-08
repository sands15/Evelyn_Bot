from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import subprocess

import aiohttp

from .config import VOYAGER_CODEX_GATEWAY_PORT, VOYAGER_CODEX_GATEWAY_URL
from .paths import get_repo_root


REPO_ROOT = get_repo_root()
START_GATEWAY_PS1 = REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_codex_gateway.ps1"


class CodexGatewayClient:
    def __init__(self, url: str = VOYAGER_CODEX_GATEWAY_URL) -> None:
        parsed = urlparse(url)
        self.url = url
        self.host = parsed.hostname or "127.0.0.1"
        self.port = int(parsed.port or VOYAGER_CODEX_GATEWAY_PORT)
        self.base_url = f"http://{self.host}:{self.port}"
        self._session: aiohttp.ClientSession | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._log_handle = None
        self._startup_lock = asyncio.Lock()
        self._cwd = REPO_ROOT
        self._launcher_env = os.environ.copy()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _decode_response(self, resp: aiohttp.ClientResponse) -> dict[str, Any]:
        try:
            payload = await resp.json(content_type=None)
        except Exception:
            text = await resp.text()
            payload = {"ok": False, "error": text.strip() or f"HTTP {resp.status}"}
        return payload if isinstance(payload, dict) else {"value": payload}

    async def health(self) -> dict[str, Any]:
        try:
            session = await self._get_session()
            async with session.get(self.base_url + "/health") as resp:
                payload = await self._decode_response(resp)
                payload.setdefault("http_status", resp.status)
                payload["alive"] = resp.status == 200
                return payload
        except Exception as exc:
            return {"alive": False, "error": str(exc)}

    async def ready(self) -> dict[str, Any]:
        try:
            session = await self._get_session()
            async with session.get(self.base_url + "/ready") as resp:
                payload = await self._decode_response(resp)
                payload.setdefault("http_status", resp.status)
                payload["alive"] = resp.status < 500
                return payload
        except Exception as exc:
            return {"alive": False, "ready": False, "error": str(exc)}

    async def is_alive(self) -> bool:
        status = await self.health()
        return bool(
            status.get("alive")
            and status.get("backendReady")
            and status.get("isolatedRuntime")
            and status.get("toolAccessVerified")
        )

    async def ensure_service(self, timeout_sec: float = 15.0) -> None:
        if await self.is_alive():
            return
        async with self._startup_lock:
            if await self.is_alive():
                return
            await self._spawn_service_process()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(1.0, timeout_sec)
            while loop.time() < deadline:
                if await self.is_alive():
                    return
                if self._proc is not None and self._proc.returncode not in (None, 0):
                    raise RuntimeError("codex_gateway_isolated_runtime_failed")
                await asyncio.sleep(0.25)
            raise RuntimeError("Codex gateway did not become ready in time")

    async def _spawn_service_process(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        env = self._launcher_env.copy()
        env.setdefault("VOYAGER_CODEX_GATEWAY_HOST", self.host)
        env.setdefault("VOYAGER_CODEX_GATEWAY_PORT", str(self.port))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        if os.name != "nt" or not START_GATEWAY_PS1.exists():
            raise RuntimeError("codex_gateway_isolated_runtime_required")
        self._proc = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(START_GATEWAY_PS1),
            cwd=str(self._cwd),
            env=env,
            creationflags=creationflags,
        )

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
