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
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
    validate_world_lease_status,
)
from .minecraft_world_lease_delegation import (
    MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER,
    minecraft_world_lease_delegation_error_code,
)


RemoteRequest = Callable[
    [str, str, dict[str, Any] | None, dict[str, str]],
    Awaitable[dict[str, Any]],
]


_REMOTE_DELEGATION_ERROR = "minecraft_world_lease_delegation_failed"
_SENSITIVE_STATUS_KEYS = frozenset(
    {
        "authorizationToken",
        "issuerRef",
        "goal",
        "rawArguments",
        "rawGoal",
        "secret",
        "token",
        "transcript",
    }
)


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
            "auditReady": False,
            "statusReady": False,
            "lease": None,
            "updatedAt": time.time(),
            "delegated": True,
        }

    def initialize(self) -> dict[str, Any]:
        self._status = {
            "schema": "minecraft_world_lease.status.v1",
            "state": "remote_initializing",
            "active": False,
            "auditReady": False,
            "statusReady": False,
            "lease": None,
            "updatedAt": time.time(),
            "delegated": True,
        }
        return self.status()

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    def _inactive_error_status(self) -> dict[str, Any]:
        return {
            "schema": MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
            "state": "remote_error",
            "active": False,
            "auditReady": False,
            "statusReady": False,
            "lease": None,
            "updatedAt": time.time(),
            "delegated": True,
            "lastErrorCode": _REMOTE_DELEGATION_ERROR,
        }

    def _ingest_lease_status(self, value: Any) -> bool:
        if (
            not isinstance(value, dict)
            or value.get("schema")
            != MINECRAFT_WORLD_LEASE_STATUS_SCHEMA
            or not isinstance(value.get("state"), str)
            or not str(value.get("state") or "").strip()
            or not isinstance(value.get("active"), bool)
            or not isinstance(value.get("auditReady"), bool)
            or not isinstance(value.get("statusReady"), bool)
        ):
            return False
        active = value.get("active") is True
        lease = value.get("lease")
        if active and (
            value.get("state") != "authorized"
            or value.get("auditReady") is not True
            or value.get("statusReady") is not True
            or not isinstance(lease, dict)
        ):
            return False
        if active:
            valid, _ = validate_world_lease_status(value)
            if not valid:
                return False
        safe_status = {
            key: item
            for key, item in value.items()
            if key not in _SENSITIVE_STATUS_KEYS
        }
        if active:
            safe_status["lease"] = {
                key: item
                for key, item in lease.items()
                if key not in _SENSITIVE_STATUS_KEYS
            }
        else:
            safe_status["lease"] = None
        safe_status["delegated"] = True
        self._status = safe_status
        return True

    def _clear_stale_authorization(self) -> None:
        self._status = self._inactive_error_status()

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
                response = (
                    dict(error_payload)
                    if isinstance(error_payload, dict)
                    else {}
                )
                response["ok"] = False
                response["error"] = code
                return response
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
        try:
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
        except asyncio.CancelledError:
            self._clear_stale_authorization()
            raise
        except Exception:
            self._clear_stale_authorization()
            raise
        if not isinstance(response, dict):
            self._clear_stale_authorization()
            raise RuntimeError(
                "minecraft_world_lease_response_invalid"
            )
        lease_status = response.get("leaseStatus")
        status_ingested = self._ingest_lease_status(lease_status)
        if response.get("ok") is False:
            if not status_ingested:
                self._clear_stale_authorization()
            raise RuntimeError(
                minecraft_world_lease_delegation_error_code(
                    str(
                        response.get("error")
                        or _REMOTE_DELEGATION_ERROR
                    )
                )
            )
        if not status_ingested:
            self._clear_stale_authorization()
            raise RuntimeError(
                "minecraft_world_lease_response_invalid"
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
                "auditReady": False,
                "statusReady": False,
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
            self._clear_stale_authorization()
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
            self._clear_stale_authorization()
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
            self._clear_stale_authorization()
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
