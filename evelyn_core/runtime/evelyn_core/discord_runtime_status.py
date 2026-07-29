from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .paths import get_runtime_artifacts_root


DISCORD_STATUS_SCHEMA = "discord_runtime.status.v1"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


class DiscordRuntimeStatus:
    def __init__(
        self,
        *,
        bot_user: Callable[[], Any],
        bot_guilds: Callable[[], list[Any]],
        voice_client_type: type,
        status_path: Path | None = None,
        interval_sec: float = 1.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.bot_user = bot_user
        self.bot_guilds = bot_guilds
        self.voice_client_type = voice_client_type
        self.status_path = status_path or (
            get_runtime_artifacts_root() / "discord" / "status.json"
        )
        self.interval_sec = max(0.2, float(interval_sec))
        self.now = now
        self.started_at = self.now()
        self.task: asyncio.Task[Any] | None = None
        self.last_error = ""

    def snapshot(self) -> dict[str, Any]:
        guilds = list(self.bot_guilds() or [])
        voice_rows: list[dict[str, Any]] = []
        for guild in guilds:
            voice_client = getattr(guild, "voice_client", None)
            if not isinstance(voice_client, self.voice_client_type):
                continue
            channel = getattr(voice_client, "channel", None)
            try:
                listening = bool(voice_client.is_listening())
            except Exception:
                listening = False
            try:
                connected = bool(voice_client.is_connected())
            except Exception:
                connected = channel is not None
            voice_rows.append(
                {
                    "guildId": getattr(guild, "id", None),
                    "channelId": getattr(channel, "id", None),
                    "connected": connected,
                    "listening": listening,
                }
            )
        return {
            "schema": DISCORD_STATUS_SCHEMA,
            "heartbeatAt": self.now(),
            "startedAt": self.started_at,
            "pid": os.getpid(),
            "gatewayConnected": self.bot_user() is not None,
            "guildConnected": bool(guilds),
            "guildCount": len(guilds),
            "voiceConnected": any(row["connected"] for row in voice_rows),
            "listening": any(row["listening"] for row in voice_rows),
            "voiceConnections": voice_rows,
            "lastError": self.last_error,
        }

    def write_once(self) -> dict[str, Any]:
        payload = self.snapshot()
        try:
            _atomic_json_write(self.status_path, payload)
            self.last_error = ""
        except Exception as exc:
            self.last_error = f"status_write_failed:{type(exc).__name__}"
        return payload

    def start(self) -> asyncio.Task[Any]:
        if self.task is not None and not self.task.done():
            return self.task
        self.task = asyncio.create_task(
            self._run(),
            name="discord-runtime-heartbeat",
        )
        return self.task

    async def _run(self) -> None:
        while True:
            await asyncio.to_thread(self.write_once)
            await asyncio.sleep(self.interval_sec)


__all__ = ["DISCORD_STATUS_SCHEMA", "DiscordRuntimeStatus"]
