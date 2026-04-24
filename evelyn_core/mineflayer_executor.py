from __future__ import annotations

import asyncio
import json
from typing import Any

from .text import clean_text


class MineflayerExecutor:
    """A thin async adapter that talks to an external Mineflayer sidecar over stdio.

    Protocol shape (JSON lines):
    request  -> {"id": 1, "method": "observe", "params": {...}}
    response -> {"id": 1, "ok": true, "result": {...}}
    """

    def __init__(self, command: list[str], *, cwd: str | None = None) -> None:
        self.command = command
        self.cwd = cwd
        self.proc: asyncio.subprocess.Process | None = None
        self._seq = 0
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def disconnect(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

    async def observe(self) -> dict[str, Any]:
        observed = await self._request("observe", {})
        if isinstance(observed, dict):
            observed.setdefault("environment", "minecraft")
        return observed

    async def execute_step(self, step: dict[str, Any]) -> dict[str, Any]:
        return await self._request("execute_step", {"step": step})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self.proc is None or self.proc.returncode is not None or self.proc.stdin is None or self.proc.stdout is None:
                raise RuntimeError("Mineflayer executor is not connected")
            self._seq += 1
            req_id = self._seq
            payload = json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False) + "\n"
            self.proc.stdin.write(payload.encode("utf-8"))
            await self.proc.stdin.drain()
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("Mineflayer sidecar closed stdout")
                data = json.loads(line.decode("utf-8", errors="ignore"))
                if int(data.get("id", -1)) != req_id:
                    continue
                if data.get("ok"):
                    result = data.get("result")
                    return result if isinstance(result, dict) else {"value": result}
                err = clean_text(str(data.get("error", "unknown_error"))) or "unknown_error"
                raise RuntimeError(f"Mineflayer sidecar error: {err}")
