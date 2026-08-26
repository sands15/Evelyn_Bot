from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .paths import get_runtime_artifacts_root
from .runtime_error_observability import RuntimeErrorCounter
from .runtime_source_identity import runtime_source_identity
from .voice_input_lease import discord_voice_input_instance_id
from .voice_validation import emit_silence_liveness_event


DISCORD_STATUS_SCHEMA = "discord_runtime.status.v1"


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def discord_gateway_connected(bot: Any) -> bool:
    """Return true only while discord.py has a live, ready gateway socket."""

    if bot.is_closed() is True or bot.is_ready() is not True:
        return False
    websocket = getattr(bot, "ws", None)
    if websocket is None:
        return False
    return getattr(websocket, "open", False) is True


class DiscordRuntimeStatus:
    def __init__(
        self,
        *,
        gateway_ready: Callable[[], bool],
        bot_guilds: Callable[[], list[Any]],
        voice_client_type: type,
        status_path: Path | None = None,
        interval_sec: float = 1.0,
        now: Callable[[], float] = time.time,
        search_followup_recovery_status: Callable[[], dict[str, Any]] | None = None,
        conversation_ingress_recovery_status: (
            Callable[[], dict[str, Any]] | None
        ) = None,
        instance_id: str | None = None,
    ) -> None:
        self.gateway_ready = gateway_ready
        self.bot_guilds = bot_guilds
        self.voice_client_type = voice_client_type
        self.status_path = status_path or (
            get_runtime_artifacts_root() / "discord" / "status.json"
        )
        self.interval_sec = max(0.2, float(interval_sec))
        self.now = now
        self.search_followup_recovery_status = (
            search_followup_recovery_status
        )
        self.conversation_ingress_recovery_status = (
            conversation_ingress_recovery_status
        )
        self.instance_id = instance_id or discord_voice_input_instance_id()
        self.started_at = self.now()
        self.task: asyncio.Task[Any] | None = None
        self.last_error = ""
        self.runtime_errors = RuntimeErrorCounter(now=self.now)

    def record_error(
        self,
        code: str,
        error: BaseException | type[BaseException] | None = None,
    ) -> None:
        snapshot = self.runtime_errors.record(code, error)
        error_type = str(snapshot.get("lastErrorType") or "")
        self.last_error = f"{snapshot['lastErrorCode']}:{error_type}".rstrip(":")

    def snapshot(self) -> dict[str, Any]:
        source_identity = runtime_source_identity()
        try:
            gateway_connected = self.gateway_ready() is True
        except Exception as exc:
            self.record_error("gateway_readiness_probe_failed", exc)
            gateway_connected = False
        guilds = list(self.bot_guilds() or [])
        voice_rows: list[dict[str, Any]] = []
        for guild in guilds:
            voice_client = getattr(guild, "voice_client", None)
            if not isinstance(voice_client, self.voice_client_type):
                continue
            channel = getattr(voice_client, "channel", None)
            try:
                listening = bool(voice_client.is_listening())
            except Exception as exc:
                self.record_error("voice_listening_probe_failed", exc)
                listening = False
            try:
                connected = bool(voice_client.is_connected())
            except Exception as exc:
                self.record_error("voice_connection_probe_failed", exc)
                connected = channel is not None
            voice_rows.append(
                {
                    "guildId": getattr(guild, "id", None),
                    "channelId": getattr(channel, "id", None),
                    "connected": connected,
                    "listening": listening,
                }
            )
        search_recovery: dict[str, Any] = {}
        if self.search_followup_recovery_status is not None:
            try:
                search_recovery = dict(
                    self.search_followup_recovery_status() or {}
                )
            except Exception as exc:
                self.record_error(
                    "search_followup_recovery_status_failed",
                    exc,
                )
        conversation_ingress_recovery: dict[str, Any] = {}
        if self.conversation_ingress_recovery_status is not None:
            try:
                raw_ingress_status = dict(
                    self.conversation_ingress_recovery_status() or {}
                )
                raw_phases = raw_ingress_status.get("phases")
                raw_phases = (
                    raw_phases if isinstance(raw_phases, dict) else {}
                )
                conversation_ingress_recovery = {
                    "schema": str(
                        raw_ingress_status.get("schema") or ""
                    ),
                    "state": str(
                        raw_ingress_status.get("state") or "unknown"
                    ),
                    "enabled": (
                        raw_ingress_status.get("enabled") is True
                    ),
                    "ownerReady": (
                        raw_ingress_status.get("ownerReady") is True
                    ),
                    "entryCount": int(
                        raw_ingress_status.get("entryCount", 0) or 0
                    ),
                    "phases": {
                        phase: int(raw_phases.get(phase, 0) or 0)
                        for phase in (
                            "accepted",
                            "response_ready",
                            "delivery_inflight",
                            "delivery_succeeded",
                            "delivery_ambiguous",
                            "terminal_committing",
                            "completed",
                        )
                    },
                    "unansweredRecoveryCount": int(
                        raw_ingress_status.get(
                            "unansweredRecoveryCount",
                            0,
                        )
                        or 0
                    ),
                    "ambiguousRecoveryCount": int(
                        raw_ingress_status.get(
                            "ambiguousRecoveryCount",
                            0,
                        )
                        or 0
                    ),
                    "reconciledRecoveryCount": int(
                        raw_ingress_status.get(
                            "reconciledRecoveryCount",
                            0,
                        )
                        or 0
                    ),
                    "reconciliationFailureCount": int(
                        raw_ingress_status.get(
                            "reconciliationFailureCount",
                            0,
                        )
                        or 0
                    ),
                    "lastErrorCode": str(
                        raw_ingress_status.get("lastErrorCode") or ""
                    ),
                }
            except Exception as exc:
                self.record_error(
                    "conversation_ingress_recovery_status_failed",
                    exc,
                )
        return {
            "schema": DISCORD_STATUS_SCHEMA,
            "instanceId": self.instance_id,
            "heartbeatAt": self.now(),
            "startedAt": self.started_at,
            "pid": os.getpid(),
            "gatewayConnected": gateway_connected,
            "guildConnected": bool(guilds),
            "guildCount": len(guilds),
            "voiceConnected": any(row["connected"] for row in voice_rows),
            "listening": any(row["listening"] for row in voice_rows),
            "voiceConnections": voice_rows,
            "sourceReady": source_identity.get("ready") is True,
            "sourceIdentity": source_identity,
            "searchFollowupRecovery": search_recovery,
            "conversationIngressRecovery": (
                conversation_ingress_recovery
            ),
            "lastError": self.last_error,
            **self.runtime_errors.snapshot(),
        }

    def write_once(self) -> dict[str, Any]:
        payload = self.snapshot()
        try:
            _atomic_json_write(self.status_path, payload)
            self.last_error = ""
        except Exception as exc:
            self.record_error("status_write_failed", exc)
        try:
            emit_silence_liveness_event(
                "discord",
                root=self.status_path.parent.parent,
                heartbeat_at=payload["heartbeatAt"],
                gateway_connected=payload["gatewayConnected"],
                voice_connections=payload["voiceConnections"],
            )
        except Exception as exc:
            self.record_error("silence_liveness_emit_failed", exc)
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


__all__ = [
    "DISCORD_STATUS_SCHEMA",
    "DiscordRuntimeStatus",
    "discord_gateway_connected",
]
