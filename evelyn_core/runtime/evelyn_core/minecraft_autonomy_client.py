from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import psutil

from .config import (
    MINECRAFT_AUTONOMY_SERVICE_HOST,
    MINECRAFT_AUTONOMY_SERVICE_PORT,
    VOYAGER_ACTION_BACKEND,
    VOYAGER_CODEX_GATEWAY_PORT,
    VOYAGER_CODEX_GATEWAY_PYTHON_EXE,
    VOYAGER_PYTHON_EXE,
)
from .paths import get_repo_root, get_runtime_artifacts_root


REPO_ROOT = get_repo_root()
START_SERVICE_PS1 = REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_voyager_service.ps1"
START_CODEX_GATEWAY_PS1 = REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_codex_gateway.ps1"


class MinecraftAutonomyClient:
    def __init__(self, *, host: str = MINECRAFT_AUTONOMY_SERVICE_HOST, port: int = MINECRAFT_AUTONOMY_SERVICE_PORT) -> None:
        self.host = host
        self.port = int(port)
        self.base_url = f"http://{self.host}:{self.port}"
        self._session: aiohttp.ClientSession | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._gateway_proc: asyncio.subprocess.Process | None = None
        self._log_handle = None
        self._cwd = str(REPO_ROOT)
        self._startup_lock = asyncio.Lock()
        self._gateway_startup_lock = asyncio.Lock()
        self._python_exe = self._resolve_python_exe()
        self._gateway_python_exe = self._resolve_gateway_python_exe()
        self._startup_lock_path = Path(self._cwd) / ".voyager_service.start.lock"
        self._goal_state_path = get_runtime_artifacts_root() / "voyager" / "voyager_goal_state.json"
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

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        *,
        ensure_service: bool = True,
        timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        if ensure_service:
            await self.ensure_service()
        session = await self._get_session()
        timeout = aiohttp.ClientTimeout(total=max(0.1, float(timeout_sec))) if timeout_sec is not None else None
        async with session.request(method, self.base_url + path, json=json_data, timeout=timeout) as resp:
            payload = await self._decode_response(resp)
            if resp.status >= 400:
                raise RuntimeError(str(payload.get("error") or payload))
            return payload

    async def is_service_alive(self, timeout_sec: float = 0.8) -> bool:
        try:
            session = await self._get_session()
            timeout = aiohttp.ClientTimeout(total=max(0.1, float(timeout_sec)))
            async with session.get(self.base_url + "/health", timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def ensure_service(self, timeout_sec: float = 20.0) -> None:
        if await self.is_service_alive():
            return
        async with self._startup_lock:
            if await self.is_service_alive():
                return
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(1.0, timeout_sec)
            lock_acquired = False
            while loop.time() < deadline:
                if await self.is_service_alive():
                    return
                if self._service_process_exists():
                    await asyncio.sleep(0.25)
                    continue
                lock_acquired = self._try_acquire_startup_lock()
                if lock_acquired:
                    break
                await asyncio.sleep(0.25)
            if not lock_acquired:
                if self._service_process_exists():
                    while loop.time() < deadline:
                        if await self.is_service_alive():
                            return
                        if not self._service_process_exists():
                            break
                        await asyncio.sleep(0.25)
                raise RuntimeError("Voyager Minecraft startup lock could not be acquired in time")
            try:
                if await self.is_service_alive():
                    return
                if self._service_process_exists():
                    while loop.time() < deadline:
                        if await self.is_service_alive():
                            return
                        if not self._service_process_exists():
                            break
                        await asyncio.sleep(0.25)
                await self._spawn_service_process()
                while loop.time() < deadline:
                    if await self.is_service_alive():
                        return
                    if self._proc is not None and self._proc.returncode is not None:
                        raise RuntimeError(f"Voyager Minecraft service exited early with code {self._proc.returncode}")
                    await asyncio.sleep(0.25)
                raise RuntimeError("Voyager Minecraft service did not become ready in time")
            finally:
                self._release_startup_lock()

    def _resolve_python_exe(self) -> str:
        configured = Path(str(VOYAGER_PYTHON_EXE or "")).expanduser()
        if configured.exists():
            return str(configured)
        return sys.executable

    def _resolve_gateway_python_exe(self) -> str:
        configured = Path(str(VOYAGER_CODEX_GATEWAY_PYTHON_EXE or "")).expanduser()
        if configured.exists():
            return str(configured)
        return self._python_exe

    def _iter_existing_service_processes(self):
        wanted_port = int(self.port)
        seen_pids: set[int] = set()
        try:
            for conn in psutil.net_connections(kind="tcp"):
                try:
                    local_port = int(getattr(conn.laddr, "port", 0) or 0)
                except Exception:
                    local_port = 0
                if local_port != wanted_port or getattr(conn, "status", None) != psutil.CONN_LISTEN:
                    continue
                pid = getattr(conn, "pid", None)
                if not pid or pid == os.getpid() or pid in seen_pids:
                    continue
                try:
                    proc = psutil.Process(pid)
                except Exception:
                    continue
                seen_pids.add(pid)
                yield proc
        except Exception:
            pass

        markers = (
            "evelyn_core.voyager_service",
            "start_voyager_service.ps1",
            "start_voyager_service.bat",
            "start_voyager.bat",
        )
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
            except Exception:
                continue
            pid = int(proc.info.get("pid") or 0)
            if not cmdline or not pid or pid == os.getpid() or pid in seen_pids:
                continue
            joined = " ".join(cmdline)
            if not any(marker in joined for marker in markers):
                continue
            if "--port" in cmdline:
                try:
                    port_value = str(cmdline[cmdline.index("--port") + 1])
                except Exception:
                    port_value = None
                if port_value and int(port_value) != wanted_port:
                    continue
            seen_pids.add(pid)
            yield proc

    def _service_process_exists(self) -> bool:
        return any(True for _ in self._iter_existing_service_processes())

    def _try_acquire_startup_lock(self) -> bool:
        try:
            fd = os.open(str(self._startup_lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                stat = self._startup_lock_path.stat()
                if (time.time() - stat.st_mtime) > 30:
                    self._startup_lock_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _release_startup_lock(self) -> None:
        try:
            self._startup_lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    async def _spawn_service_process(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        if self._service_process_exists():
            return
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = self._launcher_env.copy()
        env["VOYAGER_PYTHON_EXE"] = self._python_exe
        env.setdefault("MINECRAFT_AUTONOMY_SERVICE_HOST", self.host)
        env.setdefault("MINECRAFT_AUTONOMY_SERVICE_PORT", str(self.port))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        if os.name == "nt" and START_SERVICE_PS1.exists():
            self._proc = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(START_SERVICE_PS1),
                cwd=self._cwd,
                env=env,
                creationflags=creationflags,
            )
            return
        self._proc = await asyncio.create_subprocess_exec(
            self._python_exe,
            "-m",
            "evelyn_core.voyager_service",
            "--host",
            self.host,
            "--port",
            str(self.port),
            cwd=self._cwd,
            env=env,
            creationflags=creationflags,
        )

    async def is_codex_gateway_alive(self, timeout_sec: float = 0.8) -> bool:
        gateway_url = f"http://127.0.0.1:{int(VOYAGER_CODEX_GATEWAY_PORT)}/health"
        try:
            session = await self._get_session()
            timeout = aiohttp.ClientTimeout(total=max(0.1, float(timeout_sec)))
            async with session.get(gateway_url, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _spawn_codex_gateway_process(self) -> None:
        if self._gateway_proc is not None and self._gateway_proc.returncode is None:
            return
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = self._launcher_env.copy()
        env["VOYAGER_CODEX_GATEWAY_PYTHON_EXE"] = self._gateway_python_exe
        env.setdefault("VOYAGER_CODEX_GATEWAY_PORT", str(int(VOYAGER_CODEX_GATEWAY_PORT)))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        if os.name == "nt" and START_CODEX_GATEWAY_PS1.exists():
            self._gateway_proc = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(START_CODEX_GATEWAY_PS1),
                cwd=self._cwd,
                env=env,
                creationflags=creationflags,
            )
            return
        self._gateway_proc = await asyncio.create_subprocess_exec(
            self._gateway_python_exe,
            "-m",
            "evelyn_core.codex_gateway_server",
            "--host",
            "127.0.0.1",
            "--port",
            str(int(VOYAGER_CODEX_GATEWAY_PORT)),
            cwd=self._cwd,
            env=env,
            creationflags=creationflags,
        )

    async def ensure_codex_gateway(self, timeout_sec: float = 15.0) -> None:
        if str(VOYAGER_ACTION_BACKEND or "").strip().lower() != "codex-gateway":
            return
        if await self.is_codex_gateway_alive():
            return
        async with self._gateway_startup_lock:
            if await self.is_codex_gateway_alive():
                return
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(1.0, timeout_sec)
            await self._spawn_codex_gateway_process()
            while loop.time() < deadline:
                if await self.is_codex_gateway_alive():
                    return
                if self._gateway_proc is not None and self._gateway_proc.returncode is not None:
                    raise RuntimeError(f"Codex gateway exited early with code {self._gateway_proc.returncode}")
                await asyncio.sleep(0.25)
            raise RuntimeError("Codex gateway did not become ready in time")

    async def start(
        self,
        goal: str | None = None,
        *,
        world_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.ensure_codex_gateway()
        payload: dict[str, Any] = {}
        if goal:
            payload["goal"] = goal
        if world_lease:
            payload["worldLease"] = dict(world_lease)
        return await self._request("POST", "/start", payload)

    async def stop(self) -> dict[str, Any]:
        if not await self.is_service_alive():
            return {
                "service": "voyager_minecraft",
                "running": False,
                "connected": False,
            }
        return await self._request("POST", "/stop", {}, ensure_service=False)

    async def status(self) -> dict[str, Any]:
        async def _live_probe_status() -> dict[str, Any] | None:
            try:
                probe = await self._request("GET", "/observe", ensure_service=False, timeout_sec=1.25)
            except Exception:
                return None
            if isinstance(probe, dict) and (probe.get("connected") or probe.get("active") or probe.get("position")):
                return {
                    "service": "voyager_minecraft",
                    "running": True,
                    "connected": True,
                    "last_error": None,
                    "goal": None,
                    "observation": probe,
                    "live_probe_used": True,
                }
            return None

        if not await self.is_service_alive(timeout_sec=0.6):
            live = await _live_probe_status()
            if live is not None:
                return live
            return {
                "service": "voyager_minecraft",
                "running": False,
                "connected": False,
                "last_error": None,
                "goal": None,
                "observation": {},
            }
        status = await self._request("GET", "/status", ensure_service=False, timeout_sec=1.5)
        if isinstance(status, dict) and not status.get("connected"):
            live = await _live_probe_status()
            if live is not None:
                merged = dict(status)
                merged.update(live)
                return merged
        return status

    async def observe(self, *, ensure_service: bool = True, timeout_sec: float | None = 1.25) -> dict[str, Any]:
        return await self._request("GET", "/observe", ensure_service=ensure_service, timeout_sec=timeout_sec)

    def _persist_goal_override(self, goal: str) -> None:
        goal_text = str(goal or "").strip()
        self._goal_state_path.parent.mkdir(parents=True, exist_ok=True)
        self._goal_state_path.write_text(
            json.dumps(
                {
                    "goal_override": goal_text,
                    "goal": goal_text,
                    "updated_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    async def set_goal(
        self,
        goal: str,
        *,
        world_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal_text = str(goal or "").strip()
        if not goal_text:
            raise RuntimeError("goal text is empty")
        payload: dict[str, Any] = {"goal": goal_text}
        if world_lease:
            payload["worldLease"] = dict(world_lease)
        status = await self._request("POST", "/goal", payload)
        if not isinstance(status, dict):
            raise RuntimeError("minecraft_goal_unverified")
        echoed_goal = str(
            status.get("goal")
            or status.get("goal_override")
            or ""
        ).strip()
        if echoed_goal != goal_text:
            raise RuntimeError("minecraft_goal_unverified")
        self._persist_goal_override(goal_text)
        verified = dict(status)
        verified["outcome_verified"] = True
        verified["outcome_code"] = "minecraft_goal_confirmed"
        return verified

    async def is_connected(self) -> bool:
        status = await self.status()
        return bool(status.get("connected"))

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
