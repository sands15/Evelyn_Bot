from __future__ import annotations

import asyncio
import json
import math
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .minecraft_mode_composition import (
    MINECRAFT_CONNECTED_OUTCOME,
    MINECRAFT_STOPPED_OUTCOME,
    minecraft_connection_confirmed,
    minecraft_stop_confirmed,
)
from .minecraft_world_lease_contract import (
    MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
    build_world_lease_proof,
)
from .runtime_artifact_io import atomic_json_write


MINECRAFT_WORLD_LEASE_EVENT_SCHEMA = "minecraft_world_lease.event.v1"
DEFAULT_WORLD_LEASE_TTL_SEC = 60 * 60.0
MAX_WORLD_LEASE_TTL_SEC = 4 * 60 * 60.0
MIN_WORLD_LEASE_TTL_SEC = 60.0
DEFAULT_WATCHDOG_INTERVAL_SEC = 5.0
STOP_RETRY_WINDOW_SEC = 10 * 60.0
STOP_RETRY_LIMIT = 3

_ALLOWED_SOURCES = frozenset(
    {
        "discord_command",
        "control_page",
        "local_operator",
        "test",
    }
)
_ALLOWED_REASONS = frozenset(
    {
        "process_restart",
        "explicit_connect",
        "explicit_disconnect",
        "explicit_goal",
        "lease_expired",
        "lease_replaced",
        "connect_failed",
        "shutdown",
        "unauthorized_runtime",
        "watchdog_retry",
    }
)
_ALLOWED_EVENTS = frozenset(
    {
        "process_started",
        "lease_issued",
        "lease_revoked",
        "runtime_stop_attempted",
        "runtime_stop_verified",
        "runtime_stop_failed",
        "goal_verified",
    }
)
_ALLOWED_OUTCOMES = frozenset(
    {
        "",
        MINECRAFT_CONNECTED_OUTCOME,
        MINECRAFT_STOPPED_OUTCOME,
        "minecraft_goal_confirmed",
        "minecraft_stop_failed",
        "minecraft_stop_retry_budget_exhausted",
    }
)


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_identifier(value: Any, *, limit: int = 96) -> str:
    text = str(value or "").strip()
    allowed = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789:_-."
    )
    if not text or any(character not in allowed for character in text):
        return ""
    return text[:limit]


def _safe_guild_id(value: Any) -> int | None:
    try:
        guild_id = int(value)
    except (TypeError, ValueError):
        return None
    return guild_id if guild_id >= 0 else None


def minecraft_runtime_active(status: Any) -> bool:
    if not isinstance(status, dict):
        return False
    return any(
        status.get(key) is True
        for key in (
            "running",
            "loop_running",
            "connected",
            "minecraft_connected",
            "voyager_connected",
        )
    )


@dataclass(frozen=True)
class MinecraftWorldLease:
    lease_id: str
    guild_id: int
    issuer_ref: str
    source: str
    issued_at: float
    expires_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "leaseId": self.lease_id,
            "guildId": self.guild_id,
            "source": self.source,
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
        }


class MinecraftWorldLeaseOwner:
    """Owns the single Voyager world-action authorization boundary."""

    def __init__(
        self,
        *,
        status_path: Path,
        events_dir: Path,
        secret_path: Path | None = None,
        get_runtime_status: Callable[
            [],
            Awaitable[dict[str, Any]],
        ],
        enable_mode: Callable[..., Awaitable[dict[str, Any]]],
        disable_mode: Callable[[int], Awaitable[dict[str, Any]]],
        set_goal: Callable[..., Awaitable[dict[str, Any]]],
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        create_task: Callable[[Awaitable[Any]], Any] = asyncio.create_task,
        default_ttl_sec: float = DEFAULT_WORLD_LEASE_TTL_SEC,
        max_ttl_sec: float = MAX_WORLD_LEASE_TTL_SEC,
        watchdog_interval_sec: float = DEFAULT_WATCHDOG_INTERVAL_SEC,
        log: Callable[..., Any] = print,
    ) -> None:
        self.status_path = Path(status_path)
        self.events_dir = Path(events_dir)
        artifacts_root = (
            self.status_path.parent.parent
            if self.status_path.parent.name
            == "minecraft_world_lease"
            else self.status_path.parent
        )
        self.secret_path = Path(
            secret_path
            or artifacts_root
            / "secrets"
            / "minecraft_world_lease.json"
        )
        self.get_runtime_status = get_runtime_status
        self.enable_mode = enable_mode
        self.disable_mode = disable_mode
        self.set_goal_callback = set_goal
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.create_task = create_task
        self.default_ttl_sec = max(
            MIN_WORLD_LEASE_TTL_SEC,
            _finite_float(
                default_ttl_sec,
                DEFAULT_WORLD_LEASE_TTL_SEC,
            ),
        )
        self.max_ttl_sec = max(
            self.default_ttl_sec,
            _finite_float(
                max_ttl_sec,
                MAX_WORLD_LEASE_TTL_SEC,
            ),
        )
        self.watchdog_interval_sec = max(
            0.5,
            _finite_float(
                watchdog_interval_sec,
                DEFAULT_WATCHDOG_INTERVAL_SEC,
            ),
        )
        self.log = log
        self.process_nonce = secrets.token_hex(8)
        self.authorization_token = secrets.token_urlsafe(32)
        self._secret_ready = False
        self._lease: MinecraftWorldLease | None = None
        self._state = "not_initialized"
        self._last_event_at: float | None = None
        self._last_stop_outcome = ""
        self._last_error_code = ""
        self._stop_attempts: deque[float] = deque()
        self._data_lock = threading.RLock()
        self._operation_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._watchdog_task: Any = None

    def _append_event(
        self,
        event: str,
        *,
        lease: MinecraftWorldLease | None = None,
        guild_id: int | None = None,
        reason: str = "",
        outcome: str = "",
        verified: bool | None = None,
    ) -> None:
        timestamp = self.now()
        safe_event = _safe_identifier(event)
        safe_reason = _safe_identifier(reason)
        safe_outcome = _safe_identifier(outcome)
        record = {
            "schema": MINECRAFT_WORLD_LEASE_EVENT_SCHEMA,
            "eventId": secrets.token_hex(12),
            "at": timestamp,
            "event": (
                safe_event
                if safe_event in _ALLOWED_EVENTS
                else "runtime_stop_failed"
            ),
            "processNonce": self.process_nonce,
            "leaseId": lease.lease_id if lease is not None else "",
            "guildId": (
                lease.guild_id
                if lease is not None
                else guild_id
            ),
            "issuerRef": (
                lease.issuer_ref
                if lease is not None
                else ""
            ),
            "source": (
                lease.source
                if lease is not None
                else ""
            ),
            "expiresAt": (
                lease.expires_at
                if lease is not None
                else None
            ),
            "reasonCode": (
                safe_reason
                if safe_reason in _ALLOWED_REASONS
                else "unauthorized_runtime"
            ),
            "outcomeCode": (
                safe_outcome
                if safe_outcome in _ALLOWED_OUTCOMES
                else ""
            ),
            "verified": verified,
        }
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
            date_key = datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ).strftime("%Y%m%d")
            event_path = self.events_dir / f"{date_key}.jsonl"
            with event_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
            self._last_event_at = timestamp
        except OSError:
            return

    def _prune_stop_attempts(self) -> None:
        threshold = self.monotonic() - STOP_RETRY_WINDOW_SEC
        while self._stop_attempts and self._stop_attempts[0] < threshold:
            self._stop_attempts.popleft()

    def _status_payload(self) -> dict[str, Any]:
        timestamp = self.now()
        lease = self._lease
        active_lease = (
            lease.public_dict()
            if lease is not None and lease.expires_at > timestamp
            else None
        )
        self._prune_stop_attempts()
        return {
            "schema": MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
            "state": self._state,
            "updatedAt": timestamp,
            "processNonce": self.process_nonce,
            "active": active_lease is not None,
            "lease": active_lease,
            "lastEventAt": self._last_event_at,
            "lastStopOutcome": self._last_stop_outcome,
            "lastErrorCode": self._last_error_code,
            "stopAttemptCount": len(self._stop_attempts),
            "manualInterventionRequired": (
                self._state == "manual_intervention_required"
            ),
            "policy": {
                "restoredAfterRestart": False,
                "singleWorldOwner": True,
                "defaultTtlSec": self.default_ttl_sec,
                "maxTtlSec": self.max_ttl_sec,
                "watchdogIntervalSec": self.watchdog_interval_sec,
                "stopRetryLimit": STOP_RETRY_LIMIT,
                "stopRetryWindowSec": STOP_RETRY_WINDOW_SEC,
                "issuerRefPublic": False,
                "rawGoal": False,
                "rawArguments": False,
                "transcript": False,
            },
        }

    def _write_status(self) -> None:
        try:
            atomic_json_write(
                self.status_path,
                self._status_payload(),
            )
        except OSError:
            return

    def _write_secret(self) -> None:
        atomic_json_write(
            self.secret_path,
            {
                "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                "processNonce": self.process_nonce,
                "authorizationToken": self.authorization_token,
                "issuedAt": self.now(),
            },
        )
        self._secret_ready = True

    def _lease_proof(
        self,
        lease: MinecraftWorldLease,
    ) -> dict[str, Any]:
        status = self._status_payload()
        if (
            self._lease is None
            or self._lease.lease_id != lease.lease_id
        ):
            return {}
        return build_world_lease_proof(
            status,
            authorization_token=(
                self.authorization_token
                if self._secret_ready
                else ""
            ),
        )

    def initialize(self) -> dict[str, Any]:
        with self._data_lock:
            self.process_nonce = secrets.token_hex(8)
            self.authorization_token = secrets.token_urlsafe(32)
            self._secret_ready = False
            self._lease = None
            self._state = "authorization_required"
            self._last_stop_outcome = ""
            self._last_error_code = ""
            self._stop_attempts.clear()
            try:
                self._write_secret()
            except OSError:
                self._state = "manual_intervention_required"
                self._last_error_code = (
                    "minecraft_world_lease_secret_write_failed"
                )
            self._append_event(
                "process_started",
                reason="process_restart",
            )
            self._write_status()
            return self._status_payload()

    def status(self) -> dict[str, Any]:
        with self._data_lock:
            return self._status_payload()

    def _issue_lease(
        self,
        *,
        guild_id: int,
        issuer_ref: str,
        source: str,
        ttl_sec: float | None,
    ) -> MinecraftWorldLease:
        resolved_guild_id = _safe_guild_id(guild_id)
        resolved_issuer = _safe_identifier(issuer_ref)
        resolved_source = _safe_identifier(source)
        if resolved_guild_id is None:
            raise RuntimeError("minecraft_world_guild_invalid")
        if not resolved_issuer:
            raise RuntimeError("minecraft_world_issuer_invalid")
        if resolved_source not in _ALLOWED_SOURCES:
            raise RuntimeError("minecraft_world_source_invalid")
        requested_ttl = (
            self.default_ttl_sec
            if ttl_sec is None
            else _finite_float(ttl_sec, self.default_ttl_sec)
        )
        effective_ttl = max(
            MIN_WORLD_LEASE_TTL_SEC,
            min(self.max_ttl_sec, requested_ttl),
        )
        issued_at = self.now()
        lease = MinecraftWorldLease(
            lease_id=secrets.token_urlsafe(18),
            guild_id=resolved_guild_id,
            issuer_ref=resolved_issuer,
            source=resolved_source,
            issued_at=issued_at,
            expires_at=issued_at + effective_ttl,
        )
        previous = self._lease
        if previous is not None:
            self._append_event(
                "lease_revoked",
                lease=previous,
                reason="lease_replaced",
            )
        self._lease = lease
        self._state = "authorized"
        self._last_error_code = ""
        self._append_event(
            "lease_issued",
            lease=lease,
            reason="explicit_connect",
        )
        self._write_status()
        return lease

    def _revoke_lease(self, *, reason: str) -> None:
        lease = self._lease
        self._lease = None
        if lease is not None:
            self._append_event(
                "lease_revoked",
                lease=lease,
                reason=reason,
            )
        if self._state != "manual_intervention_required":
            self._state = "authorization_required"
        self._write_status()

    async def _runtime_status(self) -> dict[str, Any]:
        try:
            status = await self.get_runtime_status()
        except Exception:
            self._last_error_code = "minecraft_status_unavailable"
            self._write_status()
            return {"_status_unavailable": True}
        return status if isinstance(status, dict) else {}

    async def _stop_runtime(
        self,
        *,
        guild_id: int,
        reason: str,
        force: bool = False,
    ) -> bool:
        self._prune_stop_attempts()
        if not force and len(self._stop_attempts) >= STOP_RETRY_LIMIT:
            self._state = "manual_intervention_required"
            self._last_error_code = (
                "minecraft_stop_retry_budget_exhausted"
            )
            self._last_stop_outcome = (
                "minecraft_stop_retry_budget_exhausted"
            )
            self._append_event(
                "runtime_stop_failed",
                guild_id=guild_id,
                reason=reason,
                outcome="minecraft_stop_retry_budget_exhausted",
                verified=False,
            )
            self._write_status()
            return False
        self._stop_attempts.append(self.monotonic())
        self._state = "revoking"
        self._append_event(
            "runtime_stop_attempted",
            guild_id=guild_id,
            reason=reason,
        )
        self._write_status()
        try:
            stopped = await self.disable_mode(guild_id)
        except Exception:
            stopped = {}
        verified = bool(
            isinstance(stopped, dict)
            and stopped.get("outcome_verified") is True
            and stopped.get("outcome_code")
            == MINECRAFT_STOPPED_OUTCOME
            and minecraft_stop_confirmed(stopped)
        )
        if verified:
            post_status = await self._runtime_status()
            verified = bool(
                not post_status.get("_status_unavailable")
                and not minecraft_runtime_active(post_status)
            )
        if verified:
            self._state = "authorization_required"
            self._last_error_code = ""
            self._last_stop_outcome = MINECRAFT_STOPPED_OUTCOME
            self._stop_attempts.clear()
            self._append_event(
                "runtime_stop_verified",
                guild_id=guild_id,
                reason=reason,
                outcome=MINECRAFT_STOPPED_OUTCOME,
                verified=True,
            )
            self._write_status()
            return True
        self._state = "manual_intervention_required"
        self._last_error_code = "minecraft_stop_unverified"
        self._last_stop_outcome = "minecraft_stop_failed"
        self._append_event(
            "runtime_stop_failed",
            guild_id=guild_id,
            reason=reason,
            outcome="minecraft_stop_failed",
            verified=False,
        )
        self._write_status()
        return False

    async def reconcile_once(
        self,
        *,
        reason: str = "unauthorized_runtime",
        force_stop: bool = False,
    ) -> dict[str, Any]:
        async with self._operation_lock:
            lease = self._lease
            if lease is not None and lease.expires_at <= self.now():
                guild_id = lease.guild_id
                self._revoke_lease(reason="lease_expired")
                stopped = await self._stop_runtime(
                    guild_id=guild_id,
                    reason="lease_expired",
                    force=force_stop,
                )
                return {
                    "action": "stop_expired_runtime",
                    "stopped": stopped,
                }
            if lease is not None:
                self._state = "authorized"
                self._write_status()
                return {
                    "action": "lease_active",
                    "stopped": False,
                }
            runtime_status = await self._runtime_status()
            if runtime_status.get("_status_unavailable"):
                stopped = await self._stop_runtime(
                    guild_id=0,
                    reason=reason,
                    force=force_stop,
                )
                return {
                    "action": "stop_status_unknown_runtime",
                    "stopped": stopped,
                }
            if not minecraft_runtime_active(runtime_status):
                if self._state != "manual_intervention_required":
                    self._state = "authorization_required"
                    self._last_error_code = ""
                self._write_status()
                return {
                    "action": "already_stopped",
                    "stopped": True,
                }
            stopped = await self._stop_runtime(
                guild_id=0,
                reason=reason,
                force=force_stop,
            )
            return {
                "action": "stop_unauthorized_runtime",
                "stopped": stopped,
            }

    async def ensure_started(self) -> dict[str, Any]:
        async with self._start_lock:
            task = self._watchdog_task
            if task is not None and not task.done():
                return self.status()
            await self.reconcile_once(
                reason="process_restart",
                force_stop=True,
            )
            self._watchdog_task = self.create_task(
                self._watchdog_loop()
            )
            return self.status()

    async def _watchdog_loop(self) -> None:
        while True:
            try:
                await self.sleep(self.watchdog_interval_sec)
                with self._data_lock:
                    lease = self._lease
                    if (
                        lease is not None
                        and lease.expires_at > self.now()
                    ):
                        self._state = "authorized"
                        self._write_status()
                        continue
                await self.reconcile_once(
                    reason="watchdog_retry",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._last_error_code = "minecraft_watchdog_failed"
                self._write_status()
                self.log(
                    "[MINECRAFT LEASE] watchdog failure "
                    "code=minecraft_watchdog_failed"
                )

    async def connect(
        self,
        guild_id: int,
        *,
        issuer_ref: str,
        source: str,
        goal: str | None = None,
        ttl_sec: float | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            if not self._secret_ready:
                raise RuntimeError(
                    "minecraft_world_lease_secret_unavailable"
                )
            current = self._lease
            if current is not None and current.expires_at <= self.now():
                expired_guild_id = current.guild_id
                self._revoke_lease(reason="lease_expired")
                if not await self._stop_runtime(
                    guild_id=expired_guild_id,
                    reason="lease_expired",
                    force=True,
                ):
                    raise RuntimeError(
                        "minecraft_stale_runtime_stop_unverified"
                    )
            elif (
                current is not None
                and current.guild_id != int(guild_id)
            ):
                previous_guild_id = current.guild_id
                self._revoke_lease(reason="lease_replaced")
                if not await self._stop_runtime(
                    guild_id=previous_guild_id,
                    reason="lease_replaced",
                    force=True,
                ):
                    raise RuntimeError(
                        "minecraft_previous_owner_stop_unverified"
                    )
            elif current is None:
                runtime_status = await self._runtime_status()
                if (
                    runtime_status.get("_status_unavailable")
                    or minecraft_runtime_active(runtime_status)
                ):
                    if not await self._stop_runtime(
                        guild_id=0,
                        reason="unauthorized_runtime",
                        force=True,
                    ):
                        raise RuntimeError(
                            "minecraft_stale_runtime_stop_unverified"
                        )
            lease = self._issue_lease(
                guild_id=guild_id,
                issuer_ref=issuer_ref,
                source=source,
                ttl_sec=ttl_sec,
            )
            try:
                observed = await self.enable_mode(
                    guild_id,
                    goal=goal,
                    world_lease=self._lease_proof(lease),
                )
            except Exception:
                self._revoke_lease(reason="connect_failed")
                await self._stop_runtime(
                    guild_id=guild_id,
                    reason="connect_failed",
                    force=True,
                )
                raise
            verified = bool(
                isinstance(observed, dict)
                and observed.get("outcome_verified") is True
                and observed.get("outcome_code")
                == MINECRAFT_CONNECTED_OUTCOME
                and minecraft_connection_confirmed(observed)
            )
            if not verified:
                self._revoke_lease(reason="connect_failed")
                await self._stop_runtime(
                    guild_id=guild_id,
                    reason="connect_failed",
                    force=True,
                )
                raise RuntimeError("minecraft_start_unverified")
            result = dict(observed)
            result["worldLease"] = lease.public_dict()
            self._state = "authorized"
            self._write_status()
            return result

    async def disconnect(self, guild_id: int) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            lease = self._lease
            if (
                lease is not None
                and lease.expires_at > self.now()
                and lease.guild_id != int(guild_id)
            ):
                raise RuntimeError(
                    "minecraft_world_lease_owner_mismatch"
                )
            stopped = await self._stop_runtime(
                guild_id=guild_id,
                reason="explicit_disconnect",
                force=True,
            )
            self._revoke_lease(reason="explicit_disconnect")
            if not stopped:
                raise RuntimeError("minecraft_stop_unverified")
            return {
                "running": False,
                "connected": False,
                "outcome_verified": True,
                "outcome_code": MINECRAFT_STOPPED_OUTCOME,
            }

    async def set_goal(
        self,
        guild_id: int,
        goal: str,
    ) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            lease = self._lease
            if (
                lease is None
                or lease.expires_at <= self.now()
                or lease.guild_id != int(guild_id)
            ):
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            result = await self.set_goal_callback(
                goal,
                world_lease=self._lease_proof(lease),
            )
            verified = bool(
                isinstance(result, dict)
                and result.get("outcome_verified") is True
                and result.get("outcome_code")
                == "minecraft_goal_confirmed"
            )
            if not verified:
                raise RuntimeError("minecraft_goal_unverified")
            self._append_event(
                "goal_verified",
                lease=lease,
                reason="explicit_goal",
                outcome="minecraft_goal_confirmed",
                verified=True,
            )
            self._write_status()
            return result

    async def shutdown(self, *, reason: str = "shutdown") -> dict[str, Any]:
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        async with self._operation_lock:
            lease = self._lease
            guild_id = lease.guild_id if lease is not None else 0
            self._revoke_lease(reason=reason)
            runtime_status = await self._runtime_status()
            if (
                not runtime_status.get("_status_unavailable")
                and not minecraft_runtime_active(runtime_status)
            ):
                self._state = "authorization_required"
                self._write_status()
                return {
                    "stopped": True,
                    "action": "already_stopped",
                }
            stopped = await self._stop_runtime(
                guild_id=guild_id,
                reason=reason,
                force=True,
            )
            return {
                "stopped": stopped,
                "action": "shutdown_stop",
            }


__all__ = [
    "DEFAULT_WATCHDOG_INTERVAL_SEC",
    "DEFAULT_WORLD_LEASE_TTL_SEC",
    "MAX_WORLD_LEASE_TTL_SEC",
    "MINECRAFT_WORLD_LEASE_EVENT_SCHEMA",
    "MINECRAFT_WORLD_LEASE_STATUS_SCHEMA",
    "MinecraftWorldLease",
    "MinecraftWorldLeaseOwner",
    "minecraft_runtime_active",
]
