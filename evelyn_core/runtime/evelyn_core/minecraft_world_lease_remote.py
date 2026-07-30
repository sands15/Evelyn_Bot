from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from .minecraft_world_lease_contract import (
    MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
)
from .minecraft_world_lease_delegation import (
    MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER,
)


RemoteRequest = Callable[
    [str, str, dict[str, Any] | None, dict[str, str]],
    Awaitable[dict[str, Any]],
]


class MinecraftWorldLeaseRemote:
    """Delegates lease mutations to the single Bot API owner."""

    def __init__(
        self,
        *,
        base_url: str,
        secret_path: Path,
        request: RemoteRequest | None = None,
        create_task: Callable[[Awaitable[Any]], Any] = (
            asyncio.create_task
        ),
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        poll_interval_sec: float = 5.0,
        request_timeout_sec: float = 5.0,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        if not self.base_url:
            raise ValueError("minecraft_world_lease_owner_url_missing")
        self.secret_path = Path(secret_path)
        self.request = request or self._default_request
        self.create_task = create_task
        self.sleep = sleep
        self.poll_interval_sec = max(
            1.0,
            float(poll_interval_sec),
        )
        self.request_timeout_sec = max(
            0.5,
            float(request_timeout_sec),
        )
        self._watchdog_task: Any = None
        self._status: dict[str, Any] = {
            "schema": "minecraft_world_lease.status.v1",
            "state": "remote_not_initialized",
            "active": False,
            "lease": None,
            "updatedAt": time.time(),
            "delegated": True,
        }

    def initialize(self) -> dict[str, Any]:
        self._status = {
            "schema": "minecraft_world_lease.status.v1",
            "state": "remote_initializing",
            "active": False,
            "lease": None,
            "updatedAt": time.time(),
            "delegated": True,
        }
        return self.status()

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def _authorization_token(self) -> str:
        try:
            payload = json.loads(
                self.secret_path.read_text(encoding="utf-8")
            )
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            OSError,
        ) as exc:
            raise RuntimeError(
                "minecraft_world_lease_secret_unavailable"
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema")
            != MINECRAFT_WORLD_LEASE_SECRET_SCHEMA
        ):
            raise RuntimeError(
                "minecraft_world_lease_secret_unavailable"
            )
        token = str(
            payload.get("authorizationToken") or ""
        ).strip()
        if not token:
            raise RuntimeError(
                "minecraft_world_lease_secret_unavailable"
            )
        return token

    async def _default_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        def send() -> dict[str, Any]:
            body = (
                json.dumps(payload).encode("utf-8")
                if payload is not None
                else None
            )
            request = urllib_request.Request(
                self.base_url + path,
                data=body,
                method=method.upper(),
                headers={
                    "Accept": "application/json",
                    **(
                        {"Content-Type": "application/json"}
                        if body is not None
                        else {}
                    ),
                    **headers,
                },
            )
            try:
                with urllib_request.urlopen(
                    request,
                    timeout=self.request_timeout_sec,
                ) as response:
                    raw = response.read()
            except urllib_error.HTTPError as exc:
                raw = exc.read()
                try:
                    error_payload = json.loads(
                        raw.decode("utf-8") or "{}"
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    error_payload = {}
                code = str(
                    (
                        error_payload
                        if isinstance(error_payload, dict)
                        else {}
                    ).get("error")
                    or f"http_{exc.code}"
                )
                raise RuntimeError(code) from exc
            except (OSError, urllib_error.URLError) as exc:
                raise RuntimeError(
                    "minecraft_world_lease_owner_unavailable"
                ) from exc
            try:
                decoded = json.loads(
                    raw.decode("utf-8") or "{}"
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "minecraft_world_lease_response_invalid"
                ) from exc
            if not isinstance(decoded, dict):
                raise RuntimeError(
                    "minecraft_world_lease_response_invalid"
                )
            return decoded

        return await asyncio.to_thread(send)

    async def _call(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        mutation: bool = False,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if mutation:
            headers[
                MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER
            ] = self._authorization_token()
        response = await self.request(
            method,
            path,
            payload,
            headers,
        )
        lease_status = response.get("leaseStatus")
        if isinstance(lease_status, dict):
            self._status = {
                **lease_status,
                "delegated": True,
            }
        if response.get("ok") is False:
            raise RuntimeError(
                str(
                    response.get("error")
                    or "minecraft_world_lease_delegation_failed"
                )
            )
        return response

    async def poll_once(self) -> dict[str, Any]:
        try:
            await self._call(
                "GET",
                "/internal/minecraft-world-lease",
            )
        except Exception:
            self._status = {
                "schema": "minecraft_world_lease.status.v1",
                "state": "remote_unavailable",
                "active": False,
                "lease": None,
                "updatedAt": time.time(),
                "delegated": True,
                "lastErrorCode": (
                    "minecraft_world_lease_owner_unavailable"
                ),
            }
        return self.status()

    async def _poll_loop(self) -> None:
        while True:
            await self.sleep(self.poll_interval_sec)
            await self.poll_once()

    async def ensure_started(self) -> dict[str, Any]:
        task = self._watchdog_task
        if task is not None and not task.done():
            return self.status()
        await self.poll_once()
        self._watchdog_task = self.create_task(
            self._poll_loop()
        )
        return self.status()

    async def connect(
        self,
        guild_id: int,
        *,
        issuer_ref: str,
        source: str,
        goal: str | None = None,
        ttl_sec: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "guildId": int(guild_id),
            "issuerRef": str(issuer_ref),
            "source": str(source),
        }
        if goal is not None:
            payload["goal"] = str(goal)
        if ttl_sec is not None:
            payload["ttlSec"] = float(ttl_sec)
        response = await self._call(
            "POST",
            "/internal/minecraft-world-lease/connect",
            payload=payload,
            mutation=True,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                "minecraft_world_lease_response_invalid"
            )
        return dict(result)

    async def disconnect(self, guild_id: int) -> dict[str, Any]:
        response = await self._call(
            "POST",
            "/internal/minecraft-world-lease/disconnect",
            payload={"guildId": int(guild_id)},
            mutation=True,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                "minecraft_world_lease_response_invalid"
            )
        return dict(result)

    async def set_goal(
        self,
        guild_id: int,
        goal: str,
    ) -> dict[str, Any]:
        response = await self._call(
            "POST",
            "/internal/minecraft-world-lease/goal",
            payload={
                "guildId": int(guild_id),
                "goal": str(goal),
            },
            mutation=True,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                "minecraft_world_lease_response_invalid"
            )
        return dict(result)

    async def shutdown(
        self,
        *,
        reason: str = "shutdown",
    ) -> dict[str, Any]:
        _ = reason
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return {
            "stopped": False,
            "action": "remote_delegation_closed",
        }


__all__ = [
    "MinecraftWorldLeaseRemote",
    "RemoteRequest",
]
