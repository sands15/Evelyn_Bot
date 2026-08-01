from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib import error as urllib_error
from urllib import request as urllib_request

from .minecraft_action_contract import (
    MINECRAFT_ACTION_RESULT_SCHEMA,
    bind_minecraft_action_request,
    validate_minecraft_action_dispatch,
    validate_minecraft_action_request,
    validate_minecraft_action_result,
)
from .minecraft_mode_composition import (
    MINECRAFT_STOPPED_OUTCOME,
    minecraft_stop_confirmed,
)
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
        action_poll_interval_sec: float = 0.25,
        action_timeout_sec: float = 120.0,
        monotonic: Callable[[], float] = time.monotonic,
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
        self.action_poll_interval_sec = max(
            0.01,
            float(action_poll_interval_sec),
        )
        self.action_timeout_sec = max(
            self.action_poll_interval_sec,
            float(action_timeout_sec),
        )
        self.monotonic = monotonic
        self._action_execution_lock = asyncio.Lock()
        self._inflight_actions: dict[str, dict[str, Any]] = {}
        self._shutting_down = False
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
        self._inflight_actions.clear()
        self._shutting_down = False
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
        self._inflight_actions.clear()
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
        self._inflight_actions.clear()
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

    def _bound_remote_action(
        self,
        request: dict[str, Any],
        dispatch: dict[str, Any],
    ) -> dict[str, Any]:
        status = self.status()
        lease = status.get("lease")
        if (
            status.get("active") is not True
            or not isinstance(lease, dict)
        ):
            raise RuntimeError(
                "minecraft_world_authorization_required"
            )
        return bind_minecraft_action_request(
            request,
            goal_run_id=str(dispatch.get("goalRunId") or ""),
            lease_id=str(lease.get("leaseId") or ""),
            lease_process_nonce=str(
                status.get("processNonce") or ""
            ),
        )

    async def _cancel_uncertain_dispatch(
        self,
        guild_id: int,
        request: dict[str, Any],
    ) -> None:
        try:
            response = await self._call(
                "POST",
                "/internal/minecraft-world-lease/cancel_action",
                payload={
                    "guildId": int(guild_id),
                    "actionRunId": request["actionRunId"],
                },
                mutation=True,
            )
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(
                    "minecraft_action_cancel_unverified"
                )
            bound = self._bound_remote_action(request, result)
            cancelled = validate_minecraft_action_dispatch(
                result,
                expected_request=bound,
            )
            if cancelled["status"] != "cancelled":
                raise RuntimeError(
                    "minecraft_action_cancel_unverified"
                )
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._verified_disconnect_after_uncertain_cancel(
                guild_id
            )

    async def _verified_disconnect_after_uncertain_cancel(
        self,
        guild_id: int,
    ) -> dict[str, Any]:
        """Revoke the owner lease and verify the whole runtime stopped.

        This is deliberately separate from the ordinary remote ``disconnect``
        adapter: cancellation cleanup must not discard local correlation merely
        because an authenticated response happened to contain a mapping.
        """

        response = await self._call(
            "POST",
            "/internal/minecraft-world-lease/disconnect",
            payload={"guildId": int(guild_id)},
            mutation=True,
        )
        result = response.get("result")
        lease_status = self.status()
        if (
            not isinstance(result, dict)
            or result.get("outcome_verified") is not True
            or result.get("outcome_code")
            != MINECRAFT_STOPPED_OUTCOME
            or not minecraft_stop_confirmed(result)
            or lease_status.get("active") is not False
            or lease_status.get("lease") is not None
            or lease_status.get("auditReady") is not True
            or lease_status.get("statusReady") is not True
        ):
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            )
        self._inflight_actions.clear()
        return dict(result)

    async def _cancel_or_disconnect_inflight(
        self,
        guild_id: int,
        action_run_id: str,
    ) -> bool:
        """Finish cancellation despite caller cancellation.

        Returns whether an additional cancellation request arrived while the
        cleanup steps were shielded.  Correlation is retained unless exact
        cancellation or a verified owner-level stop succeeds.
        """

        cancellation_requested = False
        try:
            _, cancelled = await self._await_shutdown_step(
                self.cancel_action(guild_id, action_run_id)
            )
            return cancelled
        except asyncio.CancelledError:
            cancellation_requested = True
        except Exception:
            pass
        try:
            _, cancelled = await self._await_shutdown_step(
                self._verified_disconnect_after_uncertain_cancel(
                    guild_id
                )
            )
            return cancellation_requested or cancelled
        except asyncio.CancelledError as exc:
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            ) from exc

    async def dispatch_action(
        self,
        guild_id: int,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if self._shutting_down:
            raise RuntimeError("minecraft_world_owner_shutting_down")
        normalized = validate_minecraft_action_request(
            request,
            bound=False,
        )
        if normalized["guildId"] != int(guild_id):
            raise RuntimeError("minecraft_action_correlation_mismatch")
        if self._inflight_actions:
            raise RuntimeError("minecraft_world_action_busy")
        try:
            response = await self._call(
                "POST",
                "/internal/minecraft-world-lease/action",
                payload={
                    "guildId": int(guild_id),
                    "request": normalized,
                },
                mutation=True,
            )
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(
                    "minecraft_world_lease_response_invalid"
                )
            bound = self._bound_remote_action(normalized, result)
            dispatch = validate_minecraft_action_dispatch(
                result,
                expected_request=bound,
            )
            if dispatch["status"] not in {"accepted", "running"}:
                raise RuntimeError(
                    "minecraft_action_dispatch_unverified"
                )
        except asyncio.CancelledError:
            _, cancelled = await self._await_shutdown_step(
                self._cancel_uncertain_dispatch(
                    guild_id,
                    normalized,
                )
            )
            _ = cancelled
            raise
        except Exception:
            _, cancelled = await self._await_shutdown_step(
                self._cancel_uncertain_dispatch(
                    guild_id,
                    normalized,
                )
            )
            if cancelled:
                raise asyncio.CancelledError()
            raise
        self._inflight_actions[dispatch["actionRunId"]] = {
            "guildId": int(guild_id),
            "request": bound,
        }
        return dispatch

    async def action_status(
        self,
        guild_id: int,
        *,
        action_run_id: str,
    ) -> dict[str, Any]:
        raw_run_id = str(action_run_id or "")
        run_id = raw_run_id.strip()
        record = self._inflight_actions.get(run_id)
        if (
            run_id != raw_run_id
            or not isinstance(record, dict)
            or record.get("guildId") != int(guild_id)
            or not isinstance(record.get("request"), dict)
        ):
            raise RuntimeError("minecraft_action_not_inflight")
        request = validate_minecraft_action_request(
            record["request"],
            bound=True,
        )
        response = await self._call(
            "POST",
            "/internal/minecraft-world-lease/action_status",
            payload={
                "guildId": int(guild_id),
                "goalRunId": request["goalRunId"],
                "actionRunId": request["actionRunId"],
                "actionKey": request["actionKey"],
                "contractCode": request["contractCode"],
            },
            mutation=True,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                "minecraft_world_lease_response_invalid"
            )
        if result.get("schema") == MINECRAFT_ACTION_RESULT_SCHEMA:
            verified = validate_minecraft_action_result(
                result,
                expected_request=request,
            )
            self._inflight_actions.pop(
                request["actionRunId"],
                None,
            )
            return verified
        status = validate_minecraft_action_dispatch(
            result,
            expected_request=request,
        )
        if status["status"] in {"failed", "cancelled"}:
            self._inflight_actions.pop(
                request["actionRunId"],
                None,
            )
        return status

    async def cancel_action(
        self,
        guild_id: int,
        action_run_id: str,
    ) -> dict[str, Any]:
        raw_run_id = str(action_run_id or "")
        run_id = raw_run_id.strip()
        record = self._inflight_actions.get(run_id)
        if (
            run_id != raw_run_id
            or not isinstance(record, dict)
            or record.get("guildId") != int(guild_id)
            or not isinstance(record.get("request"), dict)
        ):
            raise RuntimeError("minecraft_action_not_inflight")
        request = validate_minecraft_action_request(
            record["request"],
            bound=True,
        )
        response = await self._call(
            "POST",
            "/internal/minecraft-world-lease/cancel_action",
            payload={
                "guildId": int(guild_id),
                "actionRunId": request["actionRunId"],
            },
            mutation=True,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            )
        cancelled = validate_minecraft_action_dispatch(
            result,
            expected_request=request,
        )
        if cancelled["status"] != "cancelled":
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            )
        self._inflight_actions.pop(run_id, None)
        return cancelled

    async def execute_action(
        self,
        guild_id: int,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._action_execution_lock:
            dispatch = await self.dispatch_action(
                guild_id,
                request,
            )
            action_run_id = dispatch["actionRunId"]
            deadline = self.monotonic() + self.action_timeout_sec
            try:
                while self.monotonic() < deadline:
                    status = await self.action_status(
                        guild_id,
                        action_run_id=action_run_id,
                    )
                    if status.get("schema") == MINECRAFT_ACTION_RESULT_SCHEMA:
                        return status
                    if status.get("status") == "failed":
                        raise RuntimeError("minecraft_action_failed")
                    if status.get("status") == "cancelled":
                        raise RuntimeError("minecraft_action_cancelled")
                    await self.sleep(self.action_poll_interval_sec)
                await self.cancel_action(guild_id, action_run_id)
                raise RuntimeError("minecraft_action_timeout")
            except asyncio.CancelledError:
                await self._cancel_or_disconnect_inflight(
                    guild_id,
                    action_run_id,
                )
                raise
            except Exception:
                if action_run_id in self._inflight_actions:
                    cancellation_requested = (
                        await self._cancel_or_disconnect_inflight(
                            guild_id,
                            action_run_id,
                        )
                    )
                    if cancellation_requested:
                        raise asyncio.CancelledError()
                raise

    async def _await_shutdown_step(
        self,
        awaitable: Awaitable[Any],
    ) -> tuple[Any, bool]:
        step = asyncio.create_task(awaitable)
        cancellation_requested = False
        while not step.done():
            try:
                await asyncio.shield(step)
            except asyncio.CancelledError:
                cancellation_requested = True
                continue
        return step.result(), cancellation_requested

    async def shutdown(
        self,
        *,
        reason: str = "shutdown",
    ) -> dict[str, Any]:
        _ = reason
        self._shutting_down = True
        cancellation_requested = False
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                cancellation_requested = bool(
                    current is not None and current.cancelling()
                )
        cancelled_actions = 0
        fallback_disconnects = 0
        shutdown_error: BaseException | None = None
        records = tuple(self._inflight_actions.values())
        for record in records:
            guild_id = int(record.get("guildId") or 0)
            request = record.get("request") or {}
            action_run_id = str(
                request.get("actionRunId") or ""
            )
            try:
                _, cancelled = await self._await_shutdown_step(
                    self.cancel_action(guild_id, action_run_id)
                )
                cancellation_requested = (
                    cancellation_requested or cancelled
                )
                cancelled_actions += 1
                continue
            except asyncio.CancelledError as exc:
                shutdown_error = exc
            except Exception as exc:
                shutdown_error = exc
            try:
                _, cancelled = await self._await_shutdown_step(
                    self._verified_disconnect_after_uncertain_cancel(
                        guild_id
                    )
                )
                cancellation_requested = (
                    cancellation_requested or cancelled
                )
                fallback_disconnects += 1
                shutdown_error = None
            except asyncio.CancelledError as exc:
                shutdown_error = exc
            except Exception as exc:
                shutdown_error = exc
        if shutdown_error is not None:
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            ) from shutdown_error
        result = {
            "stopped": False,
            "action": "remote_delegation_closed",
            "actionsCancelled": cancelled_actions,
            "fallbackDisconnects": fallback_disconnects,
        }
        if cancellation_requested:
            raise asyncio.CancelledError()
        return result


__all__ = [
    "MinecraftWorldLeaseRemote",
    "RemoteRequest",
]
