from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .minecraft_action_contract import (
    MINECRAFT_ACTION_RESULT_SCHEMA,
    bind_minecraft_action_request,
    validate_minecraft_action_dispatch,
    validate_minecraft_action_request,
    validate_minecraft_action_result,
)
from .minecraft_mode_composition import (
    MINECRAFT_CONNECTED_OUTCOME,
    MINECRAFT_STOPPED_OUTCOME,
    minecraft_connection_confirmed,
    minecraft_stop_confirmed,
)
from .minecraft_world_lease_contract import (
    DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
    MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
    MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
    MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
    MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
    MINECRAFT_WORLD_LEASE_STATUS_SCHEMA,
    build_world_lease_proof,
)
from .minecraft_owner_lock import (
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)
from .runtime_artifact_io import atomic_json_write


MINECRAFT_WORLD_LEASE_EVENT_SCHEMA = "minecraft_world_lease.event.v1"
MINECRAFT_WORLD_LEASE_OWNER_CONFLICT = (
    "minecraft_world_lease_owner_conflict"
)
MINECRAFT_WORLD_LEASE_OWNER_LOCK_UNAVAILABLE = (
    "minecraft_world_lease_owner_lock_unavailable"
)
MINECRAFT_WORLD_LEASE_OWNER_CLAIM_WRITE_FAILED = (
    "minecraft_world_lease_owner_claim_write_failed"
)
MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED = (
    "minecraft_world_lease_owner_claim_failed"
)
MINECRAFT_WORLD_ACTION_LOCK_BUSY = (
    "minecraft_world_action_lock_busy"
)
MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE = (
    "minecraft_world_action_lock_unavailable"
)
MINECRAFT_WORLD_ACTION_LOCK_TIMEOUT = (
    "minecraft_world_action_lock_timeout"
)
_OWNER_AUTHORITY_ERROR_CODES = frozenset(
    {
        MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
        MINECRAFT_WORLD_LEASE_OWNER_LOCK_UNAVAILABLE,
        MINECRAFT_WORLD_LEASE_OWNER_CLAIM_WRITE_FAILED,
        MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED,
        MINECRAFT_WORLD_ACTION_LOCK_BUSY,
        MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE,
        MINECRAFT_WORLD_ACTION_LOCK_TIMEOUT,
    }
)
DEFAULT_WORLD_LEASE_TTL_SEC = 60 * 60.0
MAX_WORLD_LEASE_TTL_SEC = 4 * 60 * 60.0
MIN_WORLD_LEASE_TTL_SEC = 60.0
DEFAULT_WATCHDOG_INTERVAL_SEC = 5.0
DEFAULT_STANDBY_PROBE_INTERVAL_SEC = 30.0
MIN_OWNER_CLAIM_STALE_SEC = 15.0
WORLD_LEASE_ARTIFACT_FENCE_GRACE_SEC = (
    DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC * 2.0 + 1.0
)
WORLD_ACTION_LOCK_RETRY_SEC = 0.05
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
        "audit_unavailable",
        "status_write_failed",
        "secret_unavailable",
        "autonomy_action",
        "action_cancelled",
        "action_failed",
        "action_timeout",
    }
)
_ALLOWED_EVENTS = frozenset(
    {
        "process_started",
        "lease_issued",
        "lease_revoked",
        "runtime_start_verified",
        "runtime_stop_attempted",
        "runtime_stop_verified",
        "runtime_stop_failed",
        "goal_attempted",
        "goal_failed",
        "goal_verified",
        "action_dispatch_attempted",
        "action_dispatch_verified",
        "action_completed",
        "action_failed",
        "action_cancel_attempted",
        "action_cancel_verified",
        "action_cancel_failed",
    }
)
_ALLOWED_OUTCOMES = frozenset(
    {
        "",
        MINECRAFT_CONNECTED_OUTCOME,
        MINECRAFT_STOPPED_OUTCOME,
        "minecraft_goal_confirmed",
        "minecraft_goal_failed",
        "minecraft_stop_failed",
        "minecraft_stop_retry_budget_exhausted",
        "minecraft_action_dispatched",
        "minecraft_action_completed",
        "minecraft_action_failed",
        "minecraft_action_cancelled",
        "minecraft_action_cancel_unverified",
    }
)

DEFAULT_ACTION_POLL_INTERVAL_SEC = 0.25
DEFAULT_ACTION_TIMEOUT_SEC = 120.0
DEFAULT_ACTION_CANCEL_TIMEOUT_SEC = 5.0
DEFAULT_ACTION_SHUTDOWN_LOCK_TIMEOUT_SEC = 5.0


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


def _sync_directory_entry(directory: Path) -> None:
    """Persist a newly-created audit file's directory entry on POSIX."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    issued_monotonic: float
    expires_monotonic: float

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
        owner_claim_path: Path | None = None,
        owner_lock_path: Path | None = None,
        world_action_lock_path: Path | None = None,
        get_runtime_status: Callable[
            [],
            Awaitable[dict[str, Any]],
        ],
        enable_mode: Callable[..., Awaitable[dict[str, Any]]],
        disable_mode: Callable[[int], Awaitable[dict[str, Any]]],
        set_goal: Callable[..., Awaitable[dict[str, Any]]],
        dispatch_action: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        get_action_status: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        cancel_action: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        create_task: Callable[[Awaitable[Any]], Any] = asyncio.create_task,
        default_ttl_sec: float = DEFAULT_WORLD_LEASE_TTL_SEC,
        max_ttl_sec: float = MAX_WORLD_LEASE_TTL_SEC,
        watchdog_interval_sec: float = DEFAULT_WATCHDOG_INTERVAL_SEC,
        standby_probe_interval_sec: float = (
            DEFAULT_STANDBY_PROBE_INTERVAL_SEC
        ),
        action_poll_interval_sec: float = (
            DEFAULT_ACTION_POLL_INTERVAL_SEC
        ),
        action_timeout_sec: float = DEFAULT_ACTION_TIMEOUT_SEC,
        action_cancel_timeout_sec: float = (
            DEFAULT_ACTION_CANCEL_TIMEOUT_SEC
        ),
        action_shutdown_lock_timeout_sec: float = (
            DEFAULT_ACTION_SHUTDOWN_LOCK_TIMEOUT_SEC
        ),
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
        self.owner_claim_path = Path(
            owner_claim_path
            or self.status_path.parent
            / "owner_claim.json"
        )
        self.owner_lock_path = Path(
            owner_lock_path
            or self.owner_claim_path.parent
            / "owner_claim.lock"
        )
        self._owner_lock = MinecraftOwnerLock(self.owner_lock_path)
        self.world_action_lock_path = Path(
            world_action_lock_path
            or self.owner_claim_path.parent
            / "world_action.lock"
        )
        self._world_action_lock = MinecraftOwnerLock(
            self.world_action_lock_path
        )
        self._world_action_lock_quarantined = False
        self.get_runtime_status = get_runtime_status
        self.enable_mode = enable_mode
        self.disable_mode = disable_mode
        self.set_goal_callback = set_goal
        self.dispatch_action_callback = dispatch_action
        self.get_action_status_callback = get_action_status
        self.cancel_action_callback = cancel_action
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
        self.owner_claim_stale_sec = max(
            MIN_OWNER_CLAIM_STALE_SEC,
            self.watchdog_interval_sec * 3.0,
        )
        self.standby_probe_interval_sec = max(
            self.watchdog_interval_sec,
            _finite_float(
                standby_probe_interval_sec,
                DEFAULT_STANDBY_PROBE_INTERVAL_SEC,
            ),
        )
        self.action_poll_interval_sec = max(
            0.01,
            _finite_float(
                action_poll_interval_sec,
                DEFAULT_ACTION_POLL_INTERVAL_SEC,
            ),
        )
        self.action_timeout_sec = max(
            self.action_poll_interval_sec,
            _finite_float(
                action_timeout_sec,
                DEFAULT_ACTION_TIMEOUT_SEC,
            ),
        )
        self.action_cancel_timeout_sec = max(
            0.05,
            _finite_float(
                action_cancel_timeout_sec,
                DEFAULT_ACTION_CANCEL_TIMEOUT_SEC,
            ),
        )
        self.action_shutdown_lock_timeout_sec = max(
            0.05,
            _finite_float(
                action_shutdown_lock_timeout_sec,
                DEFAULT_ACTION_SHUTDOWN_LOCK_TIMEOUT_SEC,
            ),
        )
        self.log = log
        self.process_nonce = secrets.token_hex(8)
        self.authorization_token = secrets.token_urlsafe(32)
        self._secret_ready = False
        self._audit_ready = False
        self._status_ready = False
        self._owner_claim_owned = False
        self._owner_epoch_published = False
        self._lease: MinecraftWorldLease | None = None
        self._state = "not_initialized"
        self._last_event_at: float | None = None
        self._last_stop_outcome = ""
        self._last_error_code = ""
        self._stop_attempts: deque[float] = deque()
        self._data_lock = threading.RLock()
        self._operation_lock = asyncio.Lock()
        self._action_execution_lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._watchdog_task: Any = None
        self._next_standby_probe_at = 0.0
        self._inflight_actions: dict[str, dict[str, Any]] = {}
        self._shutting_down = False

    def _append_event(
        self,
        event: str,
        *,
        lease: MinecraftWorldLease | None = None,
        guild_id: int | None = None,
        reason: str = "",
        outcome: str = "",
        verified: bool | None = None,
        action_binding: dict[str, Any] | None = None,
    ) -> bool:
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
        if action_binding is not None:
            record.update(
                {
                    "actionRunId": _safe_identifier(
                        action_binding.get("actionRunId"),
                        limit=128,
                    ),
                    "authorizationGrantId": _safe_identifier(
                        action_binding.get(
                            "authorizationGrantId"
                        ),
                        limit=128,
                    ),
                    "goalRunId": _safe_identifier(
                        action_binding.get("goalRunId"),
                        limit=128,
                    ),
                    "actionKey": _safe_identifier(
                        action_binding.get("actionKey"),
                        limit=128,
                    ),
                    "contractCode": _safe_identifier(
                        action_binding.get("contractCode"),
                        limit=128,
                    ),
                }
            )
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
            date_key = datetime.fromtimestamp(
                timestamp,
                timezone.utc,
            ).strftime("%Y%m%d")
            event_path = self.events_dir / f"{date_key}.jsonl"
            event_file_existed = event_path.exists()
            with event_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            if not event_file_existed:
                _sync_directory_entry(self.events_dir)
            self._last_event_at = timestamp
            return True
        except OSError:
            return False

    def _mark_owner_conflict(self) -> None:
        """Drop only local authority; never mutate a successor's artifacts."""

        self._owner_claim_owned = False
        self._secret_ready = False
        self._lease = None
        self._state = "owner_conflict"
        self._last_error_code = MINECRAFT_WORLD_LEASE_OWNER_CONFLICT

    def _mark_owner_lock_unavailable(self) -> None:
        self._owner_claim_owned = False
        self._secret_ready = False
        self._lease = None
        self._state = "manual_intervention_required"
        self._last_error_code = (
            MINECRAFT_WORLD_LEASE_OWNER_LOCK_UNAVAILABLE
        )

    def _invalidate_secret_artifact(
        self,
        *,
        require_owner_claim: bool = True,
    ) -> bool:
        """Invalidate the shared token while this process is still owner."""

        if not self._owner_lock.acquired:
            return False
        # The lifetime OS lock, not the mutable diagnostic claim, is the
        # authority to revoke.  Re-reading owner_claim.json here used to turn
        # a transient read error into local owner loss before either external
        # fence could be changed, leaving an otherwise valid old proof behind.
        if require_owner_claim and not self._owner_claim_owned:
            return False
        try:
            self.secret_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            try:
                atomic_json_write(
                    self.secret_path,
                    {
                        "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                        "processNonce": secrets.token_hex(8),
                        "authorizationToken": secrets.token_urlsafe(32),
                        "issuedAt": self.now(),
                    },
                    durable=True,
                )
                return True
            except OSError:
                return False

    def _invalidate_owner_claim_artifact(self) -> bool:
        """Fence old status when the secret artifact cannot be revoked."""

        if not self._owner_lock.acquired:
            return False
        invalidated = False
        try:
            self.owner_claim_path.unlink()
            invalidated = True
        except FileNotFoundError:
            invalidated = True
        except OSError:
            try:
                atomic_json_write(
                    self.owner_claim_path,
                    {
                        "schema": (
                            "minecraft_world_lease.owner_claim.invalid.v1"
                        ),
                        "processNonce": secrets.token_hex(8),
                        "updatedAt": self.now(),
                        "pid": os.getpid(),
                    },
                    durable=True,
                )
                invalidated = True
            except OSError:
                # _read_owner_claim() deliberately conflates missing,
                # malformed, and unreadable input for consumers.  It cannot
                # prove a destructive fence after both mutations failed.
                invalidated = False
        if invalidated:
            self._owner_claim_owned = False
            self._secret_ready = False
            self._lease = None
        return invalidated

    def _quarantine_world_action_barrier(self) -> bool:
        """Keep new service effects out when artifacts cannot be fenced."""

        if self._world_action_lock.acquired:
            self._world_action_lock_quarantined = True
            return True
        try:
            self._world_action_lock.acquire_blocking()
        except (MinecraftOwnerLockBusy, MinecraftOwnerLockUnavailable, OSError):
            return False
        self._world_action_lock_quarantined = True
        return True

    def _withhold_delegation_capability(self) -> bool:
        self._lease = None
        self._secret_ready = False
        claim_state, _ = self._read_owner_claim_snapshot()
        if (
            self._owner_epoch_published
            and claim_state in {"missing", "invalid", "mismatch"}
        ):
            # The published status nonce can no longer match the claim seen
            # by consumers.  Preserve a different explicit owner artifact;
            # it is already an externally observable denial fence.
            self._owner_claim_owned = False
            if claim_state == "mismatch":
                self._mark_owner_conflict()
            return True
        if self._invalidate_secret_artifact():
            return True
        if self._invalidate_owner_claim_artifact():
            return True
        self._quarantine_world_action_barrier()
        return False

    def _mark_status_write_failed(self) -> None:
        self._status_ready = False
        self._withhold_delegation_capability()
        if self._last_error_code in _OWNER_AUTHORITY_ERROR_CODES:
            return
        self._state = "manual_intervention_required"
        self._last_error_code = (
            MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED
        )

    def _mark_audit_unavailable(self) -> None:
        self._audit_ready = False
        self._withhold_delegation_capability()
        if self._last_error_code in _OWNER_AUTHORITY_ERROR_CODES:
            return
        self._state = "manual_intervention_required"
        self._last_error_code = (
            MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
        )
        self._write_status()

    def _append_required_event(
        self,
        event: str,
        **kwargs: Any,
    ) -> bool:
        if not self._owner_claim_matches():
            return False
        if not self._status_ready:
            self._mark_status_write_failed()
            return False
        if not self._audit_ready or not self._append_event(
            event,
            **kwargs,
        ):
            self._mark_audit_unavailable()
            return False
        return True

    def _prune_stop_attempts(self) -> None:
        threshold = self.monotonic() - STOP_RETRY_WINDOW_SEC
        while self._stop_attempts and self._stop_attempts[0] < threshold:
            self._stop_attempts.popleft()

    def _standby_probe_due(self) -> bool:
        return self.monotonic() >= self._next_standby_probe_at

    def _defer_standby_probe(self) -> None:
        self._next_standby_probe_at = (
            self.monotonic() + self.standby_probe_interval_sec
        )

    def _status_payload(self) -> dict[str, Any]:
        timestamp = self.now()
        lease = self._lease
        active_lease = (
            lease.public_dict()
            if lease is not None
            and self._lease_is_active(lease, now=timestamp)
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
            "auditReady": self._audit_ready,
            "statusReady": self._status_ready,
            "stopAttemptCount": len(self._stop_attempts),
            "manualInterventionRequired": (
                self._state == "manual_intervention_required"
            ),
            "ownerClaimOwned": self._owner_claim_owned,
            "ownerLockHeld": self._owner_lock.acquired,
            "policy": {
                "restoredAfterRestart": False,
                "singleWorldOwner": True,
                "ownerClaimStaleSec": self.owner_claim_stale_sec,
                "ownerAuthority": "process_lifetime_os_lock",
                "effectHandoffLock": True,
                "staleClaimTakeover": False,
                "defaultTtlSec": self.default_ttl_sec,
                "maxTtlSec": self.max_ttl_sec,
                "watchdogIntervalSec": self.watchdog_interval_sec,
                "standbyProbeIntervalSec": (
                    self.standby_probe_interval_sec
                ),
                "stopRetryLimit": STOP_RETRY_LIMIT,
                "stopRetryWindowSec": STOP_RETRY_WINDOW_SEC,
                "issuerRefPublic": False,
                "rawGoal": False,
                "rawArguments": False,
                "transcript": False,
                "durableAuditRequired": True,
                "eventFsync": True,
            },
        }

    def _write_status(self) -> bool:
        if not self._owner_claim_matches():
            self._status_ready = False
            return False
        try:
            atomic_json_write(
                self.status_path,
                self._status_payload(),
            )
        except OSError:
            self._mark_status_write_failed()
            return False
        if not self._owner_claim_matches():
            self._status_ready = False
            return False
        return True

    def _boundary_error_code(self) -> str:
        if self._last_error_code in _OWNER_AUTHORITY_ERROR_CODES:
            return self._last_error_code
        if not self._status_ready:
            return MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED
        if not self._audit_ready:
            return MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
        if not self._secret_ready:
            return "minecraft_world_lease_secret_unavailable"
        return ""

    def _boundary_stop_reason(self) -> str:
        if self._last_error_code in _OWNER_AUTHORITY_ERROR_CODES:
            return "unauthorized_runtime"
        if not self._status_ready:
            return "status_write_failed"
        if not self._audit_ready:
            return "audit_unavailable"
        if not self._secret_ready:
            return "secret_unavailable"
        return "unauthorized_runtime"

    def _owner_claim_payload(self) -> dict[str, Any]:
        return {
            "schema": MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA,
            "processNonce": self.process_nonce,
            "updatedAt": self.now(),
            "pid": os.getpid(),
        }

    def _read_owner_claim_snapshot(self) -> tuple[str, dict[str, Any]]:
        try:
            payload = json.loads(
                self.owner_claim_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return "missing", {}
        except json.JSONDecodeError:
            return "invalid", {}
        except OSError:
            return "unreadable", {}
        if not isinstance(payload, dict):
            return "invalid", {}
        process_nonce = payload.get("processNonce")
        if (
            payload.get("schema")
            != MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA
            or not isinstance(process_nonce, str)
            or not process_nonce
            or process_nonce != process_nonce.strip()
        ):
            return "invalid", payload
        if process_nonce == self.process_nonce:
            return "match", payload
        return "mismatch", payload

    def _read_owner_claim(self) -> dict[str, Any]:
        state, payload = self._read_owner_claim_snapshot()
        return payload if state in {"match", "mismatch"} else {}

    def _owner_claim_matches(self) -> bool:
        if not self._owner_claim_owned:
            return False
        if not self._owner_lock.acquired:
            self._mark_owner_lock_unavailable()
            return False
        state, _ = self._read_owner_claim_snapshot()
        matches = state == "match"
        if not matches:
            self._mark_owner_conflict()
        return matches

    def _acquire_owner_claim(self) -> bool:
        if not self._owner_lock.acquired:
            raise MinecraftOwnerLockUnavailable(
                MINECRAFT_WORLD_LEASE_OWNER_LOCK_UNAVAILABLE
            )
        atomic_json_write(
            self.owner_claim_path,
            self._owner_claim_payload(),
            durable=True,
        )
        self._owner_epoch_published = True
        self._owner_claim_owned = True
        if not self._owner_claim_matches():
            raise OSError("owner claim commit was not readable")
        return True

    def _refresh_owner_claim(self) -> bool:
        claim_state, _ = self._read_owner_claim_snapshot()
        if claim_state != "match":
            if claim_state == "unreadable":
                self._withhold_delegation_capability()
                if self._last_error_code not in _OWNER_AUTHORITY_ERROR_CODES:
                    self._state = "manual_intervention_required"
                    self._last_error_code = (
                        MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED
                    )
            else:
                self._mark_owner_conflict()
            return False
        try:
            atomic_json_write(
                self.owner_claim_path,
                self._owner_claim_payload(),
                durable=True,
            )
        except OSError:
            self._withhold_delegation_capability()
            self._state = "manual_intervention_required"
            self._last_error_code = (
                MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED
            )
            return False
        return self._owner_claim_matches()

    def _release_owner_claim(self) -> bool:
        """Fence published authority before releasing the lifetime lock.

        A diagnostic-claim read is not authority and may itself be the failed
        I/O boundary.  While the lifetime lock is held it is safe to revoke
        either shared artifact directly.  If neither fence can be committed,
        retain the owner lock and quarantine the action lock until shutdown's
        heartbeat grace has elapsed.
        """

        if not self._owner_lock.acquired:
            self._owner_claim_owned = False
            self._owner_epoch_published = False
            self._secret_ready = False
            self._lease = None
            return True

        claim_state, _ = self._read_owner_claim_snapshot()
        if (
            self._owner_epoch_published
            and claim_state in {"missing", "invalid", "mismatch"}
        ):
            fenced = True
            if claim_state == "mismatch":
                self._mark_owner_conflict()
        else:
            fenced = self._invalidate_secret_artifact(
                require_owner_claim=False,
            )
            if fenced:
                try:
                    self.owner_claim_path.unlink()
                except (FileNotFoundError, OSError):
                    # The secret fence is already externally sufficient.
                    pass
            else:
                fenced = self._invalidate_owner_claim_artifact()

        self._owner_claim_owned = False
        self._secret_ready = False
        self._lease = None
        if not fenced:
            self._quarantine_world_action_barrier()
            return False
        self._owner_lock.release()
        self._owner_epoch_published = False
        return True

    def _force_release_owner_lock_after_fence_grace(self) -> None:
        self._owner_claim_owned = False
        self._owner_epoch_published = False
        self._secret_ready = False
        self._lease = None
        self._owner_lock.release()

    def _require_owner_claim(self) -> None:
        if not self._owner_claim_matches():
            raise RuntimeError(
                self._boundary_error_code()
                or MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
            )

    def _write_secret(
        self,
        *,
        lease: MinecraftWorldLease | None = None,
    ) -> None:
        self._require_owner_claim()
        payload = {
            "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
            "processNonce": self.process_nonce,
            "authorizationToken": self.authorization_token,
            "issuedAt": self.now(),
        }
        if lease is not None:
            payload.update(
                {
                    "leaseId": lease.lease_id,
                    "expiresMonotonic": lease.expires_monotonic,
                }
            )
        atomic_json_write(
            self.secret_path,
            payload,
        )
        self._require_owner_claim()
        self._secret_ready = True

    def _lease_proof(
        self,
        lease: MinecraftWorldLease,
    ) -> dict[str, Any]:
        if not self._owner_claim_matches():
            return {}
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
                if (
                    self._secret_ready
                    and self._audit_ready
                    and self._status_ready
                )
                else ""
            ),
        )

    def _initialize_owner_epoch(self) -> dict[str, Any]:
        """Publish one new epoch while the effect-handoff lock is held."""

        # The kernel owner lock is already authoritative. Invalidate any
        # predecessor token before publishing the successor epoch so even a
        # not-yet-upgraded consumer cannot admit an old proof.
        predecessor_secret_invalidated = (
            self._invalidate_secret_artifact(
                require_owner_claim=False,
            )
        )
        try:
            claim_acquired = self._acquire_owner_claim()
        except (MinecraftOwnerLockUnavailable, OSError):
            if predecessor_secret_invalidated:
                self._owner_lock.release()
            else:
                # Both external epoch fences may still describe the dead
                # predecessor.  Retain both kernel barriers until shutdown
                # has carried the old heartbeat through its stale window.
                self._quarantine_world_action_barrier()
            self._state = "manual_intervention_required"
            self._last_error_code = (
                MINECRAFT_WORLD_LEASE_OWNER_CLAIM_WRITE_FAILED
            )
            return self._status_payload()
        if not claim_acquired:
            if predecessor_secret_invalidated:
                self._owner_lock.release()
            else:
                self._quarantine_world_action_barrier()
            self._state = "owner_conflict"
            self._last_error_code = (
                MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
            )
            return self._status_payload()
        # Claim publication is the epoch switch for current consumers. A
        # predecessor-token failure remains manual/fail-closed even though
        # the new nonce independently fences old status.
        if not predecessor_secret_invalidated:
            self._state = "manual_intervention_required"
            self._last_error_code = (
                "minecraft_world_lease_secret_unavailable"
            )
            self._status_ready = True
            self._write_status()
            return self._status_payload()
        # This optimistic value is committed into the first status artifact.
        # A failed commit makes it sticky-false in _write_status(); secret
        # readiness is an independent boundary.
        self._status_ready = True
        if not self._append_event(
            "process_started",
            reason="process_restart",
        ):
            self._mark_audit_unavailable()
            return self._status_payload()
        self._audit_ready = True
        try:
            self._write_secret()
        except OSError:
            self._withhold_delegation_capability()
            self._state = "manual_intervention_required"
            self._last_error_code = (
                "minecraft_world_lease_secret_unavailable"
            )
        self._write_status()
        return self._status_payload()

    def initialize(self) -> dict[str, Any]:
        with self._data_lock:
            # Initialization is deliberately idempotent while this process
            # holds the lifetime lock. Rotating authority synchronously could
            # orphan an already-running external action.
            if self._owner_lock.acquired:
                if self._owner_claim_owned:
                    self._owner_claim_matches()
                return self._status_payload()
            self.process_nonce = secrets.token_hex(8)
            self.authorization_token = secrets.token_urlsafe(32)
            self._secret_ready = False
            self._audit_ready = False
            self._status_ready = False
            self._owner_claim_owned = False
            self._owner_epoch_published = False
            self._lease = None
            self._state = "authorization_required"
            self._last_stop_outcome = ""
            self._last_error_code = ""
            self._stop_attempts.clear()
            self._inflight_actions.clear()
            self._shutting_down = False
            self._next_standby_probe_at = 0.0
            self._world_action_lock_quarantined = False
            try:
                self._owner_lock.acquire()
            except MinecraftOwnerLockBusy:
                self._state = "owner_conflict"
                self._last_error_code = (
                    MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
                )
                return self._status_payload()
            except (MinecraftOwnerLockUnavailable, OSError):
                self._mark_owner_lock_unavailable()
                return self._status_payload()
            try:
                self._world_action_lock.acquire()
            except MinecraftOwnerLockBusy:
                self._owner_lock.release()
                self._state = "manual_intervention_required"
                self._last_error_code = MINECRAFT_WORLD_ACTION_LOCK_BUSY
                return self._status_payload()
            except (MinecraftOwnerLockUnavailable, OSError):
                self._owner_lock.release()
                self._state = "manual_intervention_required"
                self._last_error_code = (
                    MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE
                )
                return self._status_payload()
            try:
                return self._initialize_owner_epoch()
            finally:
                if not self._world_action_lock_quarantined:
                    self._world_action_lock.release()

    def status(self) -> dict[str, Any]:
        with self._data_lock:
            if self._owner_claim_owned:
                self._owner_claim_matches()
            return self._status_payload()

    def _lease_is_active(
        self,
        lease: MinecraftWorldLease,
        *,
        now: float | None = None,
        monotonic_now: float | None = None,
    ) -> bool:
        return (
            lease.expires_at > (self.now() if now is None else now)
            and lease.expires_monotonic
            > (
                self.monotonic()
                if monotonic_now is None
                else monotonic_now
            )
        )

    def delegation_token(self) -> str:
        with self._data_lock:
            if not self._owner_claim_matches():
                return ""
            return (
                self.authorization_token
                if (
                    self._secret_ready
                    and self._audit_ready
                    and self._status_ready
                )
                else ""
            )

    def _issue_lease(
        self,
        *,
        guild_id: int,
        issuer_ref: str,
        source: str,
        ttl_sec: float | None,
    ) -> MinecraftWorldLease:
        boundary_error = self._boundary_error_code()
        if boundary_error:
            if boundary_error == MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE:
                self._mark_audit_unavailable()
            raise RuntimeError(boundary_error)
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
        issued_monotonic = self.monotonic()
        lease = MinecraftWorldLease(
            lease_id=f"lease-{secrets.token_urlsafe(18)}",
            guild_id=resolved_guild_id,
            issuer_ref=resolved_issuer,
            source=resolved_source,
            issued_at=issued_at,
            expires_at=issued_at + effective_ttl,
            issued_monotonic=issued_monotonic,
            expires_monotonic=issued_monotonic + effective_ttl,
        )
        previous = self._lease
        if previous is not None:
            if not self._append_required_event(
                "lease_revoked",
                lease=previous,
                reason="lease_replaced",
            ):
                raise RuntimeError(
                    self._boundary_error_code()
                    or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                )
            self._lease = None
        if not self._append_required_event(
            "lease_issued",
            lease=lease,
            reason="explicit_connect",
        ):
            raise RuntimeError(
                self._boundary_error_code()
                or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
            )
        self._lease = lease
        self._state = "authorized"
        self._last_error_code = ""
        try:
            self._write_secret(lease=lease)
        except OSError:
            self._withhold_delegation_capability()
            if self._last_error_code not in _OWNER_AUTHORITY_ERROR_CODES:
                self._state = "manual_intervention_required"
                self._last_error_code = (
                    "minecraft_world_lease_secret_unavailable"
                )
            self._write_status()
            raise RuntimeError(self._boundary_error_code()) from None
        if not self._write_status():
            raise RuntimeError(self._boundary_error_code())
        return lease

    def _revoke_lease(self, *, reason: str) -> bool:
        lease = self._lease
        self._lease = None
        self._inflight_actions.clear()
        audit_ok = self._audit_ready
        if lease is not None:
            audit_ok = bool(
                self._audit_ready
                and self._append_event(
                    "lease_revoked",
                    lease=lease,
                    reason=reason,
                )
            )
            if not audit_ok:
                self._mark_audit_unavailable()
        if audit_ok and self._state != "manual_intervention_required":
            self._state = "authorization_required"
        elif not audit_ok:
            self._state = "manual_intervention_required"
            self._last_error_code = (
                MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
            )
        status_ok = self._write_status()
        return audit_ok and status_ok

    async def _runtime_status(self) -> dict[str, Any]:
        try:
            status = await self.get_runtime_status()
        except Exception:
            if not self._boundary_error_code():
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
        lease: MinecraftWorldLease | None = None,
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
            event_written = bool(
                self._audit_ready
                and self._append_event(
                    "runtime_stop_failed",
                    lease=lease,
                    guild_id=guild_id,
                    reason=reason,
                    outcome="minecraft_stop_retry_budget_exhausted",
                    verified=False,
                )
            )
            if not event_written:
                self._mark_audit_unavailable()
            self._write_status()
            return False
        self._stop_attempts.append(self.monotonic())
        if self._audit_ready:
            self._state = "revoking"
        attempt_audited = bool(
            self._audit_ready
            and self._append_event(
                "runtime_stop_attempted",
                lease=lease,
                guild_id=guild_id,
                reason=reason,
            )
        )
        if not attempt_audited:
            self._mark_audit_unavailable()
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
            self._last_stop_outcome = MINECRAFT_STOPPED_OUTCOME
            self._stop_attempts.clear()
            outcome_audited = bool(
                self._audit_ready
                and self._append_event(
                    "runtime_stop_verified",
                    lease=lease,
                    guild_id=guild_id,
                    reason=reason,
                    outcome=MINECRAFT_STOPPED_OUTCOME,
                    verified=True,
                )
            )
            boundary_error = self._boundary_error_code()
            if outcome_audited and not boundary_error:
                self._state = "authorization_required"
                self._last_error_code = ""
            elif not self._status_ready:
                self._mark_status_write_failed()
            elif not self._audit_ready:
                self._mark_audit_unavailable()
            else:
                self._state = "manual_intervention_required"
                self._last_error_code = boundary_error
            self._write_status()
            return True
        self._state = "manual_intervention_required"
        self._last_stop_outcome = "minecraft_stop_failed"
        failure_audited = bool(
            self._audit_ready
            and self._append_event(
                "runtime_stop_failed",
                lease=lease,
                guild_id=guild_id,
                reason=reason,
                outcome="minecraft_stop_failed",
                verified=False,
            )
        )
        boundary_error = self._boundary_error_code()
        if failure_audited and not boundary_error:
            self._last_error_code = "minecraft_stop_unverified"
        elif not self._status_ready:
            self._mark_status_write_failed()
        elif not self._audit_ready:
            self._mark_audit_unavailable()
        else:
            self._last_error_code = boundary_error
        self._write_status()
        return False

    async def _shielded_stop_runtime(
        self,
        *,
        guild_id: int,
        reason: str,
        force: bool = True,
        lease: MinecraftWorldLease | None = None,
    ) -> bool:
        stop_task = asyncio.create_task(
            self._stop_runtime(
                guild_id=guild_id,
                reason=reason,
                force=force,
                lease=lease,
            )
        )
        cancellation_requested = False
        while not stop_task.done():
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError:
                cancellation_requested = True
                continue
        result = stop_task.result()
        if cancellation_requested:
            raise asyncio.CancelledError()
        return result
    async def reconcile_once(
        self,
        *,
        reason: str = "unauthorized_runtime",
        force_stop: bool = False,
    ) -> dict[str, Any]:
        async with self._operation_lock:
            lease = self._lease
            if lease is not None and not self._lease_is_active(lease):
                guild_id = lease.guild_id
                self._revoke_lease(reason="lease_expired")
                stopped = await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="lease_expired",
                    force=force_stop,
                    lease=lease,
                )
                return {
                    "action": "stop_expired_runtime",
                    "stopped": stopped,
                }
            if lease is not None:
                self._state = "authorized"
                if not self._write_status():
                    stopped = await self._shielded_stop_runtime(
                        guild_id=lease.guild_id,
                        reason="status_write_failed",
                        force=True,
                        lease=lease,
                    )
                    return {
                        "action": "stop_status_write_failed_runtime",
                        "stopped": stopped,
                        "error": self._boundary_error_code(),
                    }
                return {
                    "action": "lease_active",
                    "stopped": False,
                }
            runtime_status = await self._runtime_status()
            if runtime_status.get("_status_unavailable"):
                stopped = await self._shielded_stop_runtime(
                    guild_id=0,
                    reason=reason,
                    force=force_stop,
                )
                return {
                    "action": "stop_status_unknown_runtime",
                    "stopped": stopped,
                }
            if not minecraft_runtime_active(runtime_status):
                if self._last_stop_outcome != MINECRAFT_STOPPED_OUTCOME:
                    if not self._append_required_event(
                        "runtime_stop_verified",
                        guild_id=0,
                        reason=reason,
                        outcome=MINECRAFT_STOPPED_OUTCOME,
                        verified=True,
                    ):
                        self._write_status()
                        return {
                            "action": "already_stopped",
                            "stopped": True,
                            "error": self._boundary_error_code(),
                        }
                    self._last_stop_outcome = MINECRAFT_STOPPED_OUTCOME
                    self._stop_attempts.clear()
                if self._state != "manual_intervention_required":
                    self._state = "authorization_required"
                    self._last_error_code = ""
                self._write_status()
                return {
                    "action": "already_stopped",
                    "stopped": True,
                }
            stopped = await self._shielded_stop_runtime(
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
            with self._data_lock:
                self._require_owner_claim()
                if not self._refresh_owner_claim():
                    raise RuntimeError(
                        self._boundary_error_code()
                        or MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED
                    )
            task = self._watchdog_task
            if task is not None and not task.done():
                return self.status()
            await self.reconcile_once(
                reason="process_restart",
                force_stop=True,
            )
            with self._data_lock:
                self._defer_standby_probe()
            self._watchdog_task = self.create_task(
                self._watchdog_loop()
            )
            return self.status()

    async def _watchdog_loop(self) -> None:
        while True:
            try:
                await self.sleep(self.watchdog_interval_sec)
                force_safety_stop = False
                owner_refresh_failed = False
                with self._data_lock:
                    if not self._refresh_owner_claim():
                        owner_refresh_failed = True
                    if owner_refresh_failed:
                        lease = None
                    else:
                        lease = self._lease
                    if (
                        not owner_refresh_failed
                        and lease is not None
                        and self._lease_is_active(lease)
                        and self._audit_ready
                    ):
                        self._state = "authorized"
                        if self._write_status():
                            continue
                        force_safety_stop = True
                    if (
                        not owner_refresh_failed
                        and lease is None
                        and not self._standby_probe_due()
                    ):
                        if self._write_status():
                            continue
                        force_safety_stop = True
                    if not owner_refresh_failed and lease is None:
                        self._defer_standby_probe()
                if owner_refresh_failed:
                    await self._shielded_stop_runtime(
                        guild_id=0,
                        reason="unauthorized_runtime",
                        force=True,
                    )
                    return
                await self.reconcile_once(
                    reason=(
                        "status_write_failed"
                        if force_safety_stop
                        else "watchdog_retry"
                    ),
                    force_stop=force_safety_stop,
                )
                with self._data_lock:
                    if lease is not None and self._lease is None:
                        self._defer_standby_probe()
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._boundary_error_code():
                    self._last_error_code = "minecraft_watchdog_failed"
                self._write_status()
                self.log(
                    "[MINECRAFT LEASE] watchdog failure "
                    "code=minecraft_watchdog_failed"
                )

    async def _reject_expired_connect_lease(
        self,
        *,
        guild_id: int,
        lease: MinecraftWorldLease,
        requested_goal: str,
    ) -> None:
        goal_failure_audited = bool(
            not requested_goal
            or self._append_required_event(
                "goal_failed",
                lease=lease,
                reason="explicit_goal",
                outcome="minecraft_goal_failed",
                verified=False,
            )
        )
        revoke_audited = self._revoke_lease(reason="lease_expired")
        stopped = await self._shielded_stop_runtime(
            guild_id=guild_id,
            reason=(
                self._boundary_stop_reason()
                if self._boundary_error_code()
                else "lease_expired"
            ),
            force=True,
            lease=lease,
        )
        boundary_error = self._boundary_error_code()
        if (
            not goal_failure_audited
            or not revoke_audited
            or boundary_error
        ):
            raise RuntimeError(
                boundary_error
                or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
            )
        if not stopped:
            raise RuntimeError("minecraft_stop_unverified")
        raise RuntimeError("minecraft_world_authorization_required")

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
            if self._shutting_down:
                raise RuntimeError("minecraft_world_owner_shutting_down")
            if self._inflight_actions:
                raise RuntimeError("minecraft_world_action_busy")
            boundary_error = self._boundary_error_code()
            if boundary_error:
                if boundary_error == MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE:
                    self._mark_audit_unavailable()
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=self._lease,
                )
                raise RuntimeError(boundary_error)
            requested_goal = str(goal or "").strip()
            current = self._lease
            if current is not None and not self._lease_is_active(current):
                expired_guild_id = current.guild_id
                self._revoke_lease(reason="lease_expired")
                if not await self._shielded_stop_runtime(
                    guild_id=expired_guild_id,
                    reason="lease_expired",
                    force=True,
                    lease=current,
                ):
                    raise RuntimeError(
                        "minecraft_stale_runtime_stop_unverified"
                    )
            elif (
                current is not None
                and current.guild_id != int(guild_id)
            ):
                raise RuntimeError(
                    "minecraft_world_lease_owner_mismatch"
                )
            elif current is None:
                runtime_status = await self._runtime_status()
                if (
                    runtime_status.get("_status_unavailable")
                    or minecraft_runtime_active(runtime_status)
                ):
                    if not await self._shielded_stop_runtime(
                        guild_id=0,
                        reason="unauthorized_runtime",
                        force=True,
                    ):
                        raise RuntimeError(
                            "minecraft_stale_runtime_stop_unverified"
                        )
            cleanup_guild_id = (
                current.guild_id
                if current is not None
                else int(guild_id)
            )
            try:
                lease = self._issue_lease(
                    guild_id=guild_id,
                    issuer_ref=issuer_ref,
                    source=source,
                    ttl_sec=ttl_sec,
                )
            except RuntimeError as exc:
                if str(exc) in {
                    MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE,
                    MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED,
                    "minecraft_world_lease_secret_unavailable",
                }:
                    await self._shielded_stop_runtime(
                        guild_id=cleanup_guild_id,
                        reason=self._boundary_stop_reason(),
                        force=True,
                        lease=self._lease,
                    )
                raise
            if requested_goal and not self._append_required_event(
                "goal_attempted",
                lease=lease,
                reason="explicit_goal",
            ):
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=self._lease,
                )
                raise RuntimeError(self._boundary_error_code())
            try:
                observed = await self.enable_mode(
                    guild_id,
                    goal=requested_goal or None,
                    world_lease=self._lease_proof(lease),
                )
            except asyncio.CancelledError:
                if requested_goal:
                    self._append_required_event(
                        "goal_failed",
                        lease=lease,
                        reason="explicit_goal",
                        outcome="minecraft_goal_failed",
                        verified=False,
                    )
                self._revoke_lease(reason="connect_failed")
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="connect_failed",
                    force=True,
                    lease=lease,
                )
                raise
            except Exception:
                goal_failure_audited = bool(
                    not requested_goal
                    or self._append_required_event(
                        "goal_failed",
                        lease=lease,
                        reason="explicit_goal",
                        outcome="minecraft_goal_failed",
                        verified=False,
                    )
                )
                revoke_audited = self._revoke_lease(
                    reason="connect_failed"
                )
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="connect_failed",
                    force=True,
                    lease=lease,
                )
                boundary_error = self._boundary_error_code()
                if (
                    not goal_failure_audited
                    or not revoke_audited
                    or boundary_error
                ):
                    raise RuntimeError(
                        boundary_error
                        or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                    ) from None
                raise
            if not self._lease_is_active(lease):
                await self._reject_expired_connect_lease(
                    guild_id=guild_id,
                    lease=lease,
                    requested_goal=requested_goal,
                )
            verified = bool(
                isinstance(observed, dict)
                and observed.get("outcome_verified") is True
                and observed.get("outcome_code")
                == MINECRAFT_CONNECTED_OUTCOME
                and minecraft_connection_confirmed(observed)
            )
            if not verified:
                goal_failure_audited = bool(
                    not requested_goal
                    or self._append_required_event(
                        "goal_failed",
                        lease=lease,
                        reason="explicit_goal",
                        outcome="minecraft_goal_failed",
                        verified=False,
                    )
                )
                revoke_audited = self._revoke_lease(
                    reason="connect_failed"
                )
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="connect_failed",
                    force=True,
                    lease=lease,
                )
                boundary_error = self._boundary_error_code()
                if (
                    not goal_failure_audited
                    or not revoke_audited
                    or boundary_error
                ):
                    raise RuntimeError(
                        boundary_error
                        or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                    )
                raise RuntimeError("minecraft_start_unverified")
            if not self._append_required_event(
                "runtime_start_verified",
                lease=lease,
                reason="explicit_connect",
                outcome=MINECRAFT_CONNECTED_OUTCOME,
                verified=True,
            ):
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="audit_unavailable",
                    force=True,
                    lease=lease,
                )
                raise RuntimeError(
                    self._boundary_error_code()
                )
            if requested_goal:
                reported_goal = str(
                    observed.get("goal")
                    or observed.get("goal_override")
                    or observed.get("objective_goal")
                    or ""
                ).strip()
                if reported_goal != requested_goal:
                    failure_audited = self._append_required_event(
                        "goal_failed",
                        lease=lease,
                        reason="explicit_goal",
                        outcome="minecraft_goal_failed",
                        verified=False,
                    )
                    revoke_audited = self._revoke_lease(
                        reason="connect_failed"
                    )
                    await self._shielded_stop_runtime(
                        guild_id=guild_id,
                        reason=(
                            "audit_unavailable"
                            if not self._audit_ready
                            else "connect_failed"
                        ),
                        force=True,
                        lease=lease,
                    )
                    if (
                        not failure_audited
                        or not revoke_audited
                        or self._boundary_error_code()
                    ):
                        raise RuntimeError(
                            self._boundary_error_code()
                            or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                        )
                    raise RuntimeError("minecraft_goal_unverified")
                if not self._append_required_event(
                    "goal_verified",
                    lease=lease,
                    reason="explicit_goal",
                    outcome="minecraft_goal_confirmed",
                    verified=True,
                ):
                    await self._shielded_stop_runtime(
                        guild_id=guild_id,
                        reason=(
                            "audit_unavailable"
                            if not self._audit_ready
                            else "status_write_failed"
                        ),
                        force=True,
                        lease=lease,
                    )
                    raise RuntimeError(self._boundary_error_code())
            result = dict(observed)
            result["worldLease"] = lease.public_dict()
            self._state = "authorized"
            if not self._write_status():
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="status_write_failed",
                    force=True,
                    lease=lease,
                )
                raise RuntimeError(self._boundary_error_code())
            if not self._lease_is_active(lease):
                await self._reject_expired_connect_lease(
                    guild_id=guild_id,
                    lease=lease,
                    requested_goal=requested_goal,
                )
            return result

    async def disconnect(
        self,
        guild_id: int,
        *,
        expected_lease_id: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            lease = self._lease
            if (
                expected_lease_id is not None
                and (
                    not expected_lease_id
                    or lease is None
                    or lease.lease_id != expected_lease_id
                )
            ):
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            if (
                lease is not None
                and self._lease_is_active(lease)
                and lease.guild_id != int(guild_id)
            ):
                raise RuntimeError(
                    "minecraft_world_lease_owner_mismatch"
                )
            action_cancel_failed = False
            for record in tuple(self._inflight_actions.values()):
                request = record.get("request") or {}
                if request.get("guildId") != int(guild_id):
                    continue
                try:
                    await self._cancel_bound_action_locked(record)
                except Exception:
                    action_cancel_failed = True
            revoke_audited = self._revoke_lease(
                reason="explicit_disconnect"
            )
            stopped = await self._shielded_stop_runtime(
                guild_id=guild_id,
                reason="explicit_disconnect",
                force=True,
                lease=lease,
            )
            boundary_error = self._boundary_error_code()
            if not stopped:
                if boundary_error:
                    raise RuntimeError(boundary_error)
                raise RuntimeError("minecraft_stop_unverified")
            if not revoke_audited or boundary_error:
                raise RuntimeError(
                    boundary_error
                    or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                )
            if action_cancel_failed:
                raise RuntimeError(
                    "minecraft_action_cancel_unverified"
                )
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
        *,
        expected_lease_id: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            if self._shutting_down:
                raise RuntimeError("minecraft_world_owner_shutting_down")
            lease = self._lease
            if (
                expected_lease_id is not None
                and (
                    not expected_lease_id
                    or lease is None
                    or lease.lease_id != expected_lease_id
                )
            ):
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            if self._inflight_actions:
                raise RuntimeError("minecraft_world_action_busy")
            boundary_error = self._boundary_error_code()
            if boundary_error:
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=self._lease,
                )
                raise RuntimeError(boundary_error)
            if (
                lease is None
                or not self._lease_is_active(lease)
                or lease.guild_id != int(guild_id)
            ):
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            requested_goal = str(goal or "").strip()
            if not self._append_required_event(
                "goal_attempted",
                lease=lease,
                reason="explicit_goal",
            ):
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=lease,
                )
                raise RuntimeError(self._boundary_error_code())
            try:
                result = await self.set_goal_callback(
                    requested_goal,
                    world_lease=self._lease_proof(lease),
                )
            except asyncio.CancelledError:
                self._append_required_event(
                    "goal_failed",
                    lease=lease,
                    reason="explicit_goal",
                    outcome="minecraft_goal_failed",
                    verified=False,
                )
                self._revoke_lease(reason="explicit_goal")
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=(
                        self._boundary_stop_reason()
                        if self._boundary_error_code()
                        else "explicit_goal"
                    ),
                    force=True,
                    lease=lease,
                )
                raise
            except Exception:
                failure_audited = self._append_required_event(
                    "goal_failed",
                    lease=lease,
                    reason="explicit_goal",
                    outcome="minecraft_goal_failed",
                    verified=False,
                )
                revoke_audited = self._revoke_lease(
                    reason="explicit_goal"
                )
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="audit_unavailable"
                    if not self._audit_ready
                    else "explicit_goal",
                    force=True,
                    lease=lease,
                )
                boundary_error = self._boundary_error_code()
                if (
                    not failure_audited
                    or not revoke_audited
                    or boundary_error
                ):
                    raise RuntimeError(
                        boundary_error
                        or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                    ) from None
                raise
            reported_goal = (
                str(
                    result.get("goal")
                    or result.get("goal_override")
                    or result.get("objective_goal")
                    or ""
                ).strip()
                if isinstance(result, dict)
                else ""
            )
            verified = bool(
                isinstance(result, dict)
                and result.get("outcome_verified") is True
                and result.get("outcome_code")
                == "minecraft_goal_confirmed"
                and reported_goal == requested_goal
            )
            if not verified:
                failure_audited = self._append_required_event(
                    "goal_failed",
                    lease=lease,
                    reason="explicit_goal",
                    outcome="minecraft_goal_failed",
                    verified=False,
                )
                revoke_audited = self._revoke_lease(
                    reason="explicit_goal"
                )
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="audit_unavailable"
                    if not self._audit_ready
                    else "explicit_goal",
                    force=True,
                    lease=lease,
                )
                boundary_error = self._boundary_error_code()
                if (
                    not failure_audited
                    or not revoke_audited
                    or boundary_error
                ):
                    raise RuntimeError(
                        boundary_error
                        or MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
                    )
                raise RuntimeError("minecraft_goal_unverified")
            if not self._append_required_event(
                "goal_verified",
                lease=lease,
                reason="explicit_goal",
                outcome="minecraft_goal_confirmed",
                verified=True,
            ):
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=lease,
                )
                raise RuntimeError(self._boundary_error_code())
            if not self._write_status():
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason="status_write_failed",
                    force=True,
                    lease=lease,
                )
                raise RuntimeError(self._boundary_error_code())
            return result

    def _require_action_transport(self) -> None:
        if (
            self.dispatch_action_callback is None
            or self.get_action_status_callback is None
            or self.cancel_action_callback is None
        ):
            raise RuntimeError(
                "minecraft_action_transport_unavailable"
            )

    def _authorized_action_lease(
        self,
        guild_id: int,
        *,
        expected_lease_id: str = "",
    ) -> tuple[MinecraftWorldLease, dict[str, Any]]:
        boundary_error = self._boundary_error_code()
        if boundary_error:
            raise RuntimeError(boundary_error)
        lease = self._lease
        if (
            lease is None
            or not self._lease_is_active(lease)
            or lease.guild_id != int(guild_id)
            or (
                expected_lease_id
                and lease.lease_id != expected_lease_id
            )
        ):
            raise RuntimeError(
                "minecraft_world_authorization_required"
            )
        proof = self._lease_proof(lease)
        if not proof:
            raise RuntimeError(
                "minecraft_world_authorization_required"
            )
        return lease, proof

    def _get_inflight_action(
        self,
        *,
        guild_id: int,
        action_run_id: str,
        goal_run_id: str = "",
        action_key: str = "",
        contract_code: str = "",
    ) -> dict[str, Any]:
        raw_run_id = str(action_run_id or "")
        run_id = _safe_identifier(raw_run_id, limit=128)
        record = self._inflight_actions.get(run_id)
        if (
            not run_id
            or run_id != raw_run_id
            or not isinstance(record, dict)
        ):
            raise RuntimeError("minecraft_action_not_inflight")
        request = record.get("request")
        if not isinstance(request, dict):
            raise RuntimeError("minecraft_action_not_inflight")
        exact = {
            "guildId": int(guild_id),
            "actionRunId": run_id,
        }
        for field, value in (
            ("goalRunId", goal_run_id),
            ("actionKey", action_key),
            ("contractCode", contract_code),
        ):
            if value:
                raw_value = str(value)
                if (
                    _safe_identifier(raw_value, limit=128)
                    != raw_value
                ):
                    raise RuntimeError(
                        "minecraft_action_correlation_mismatch"
                    )
                exact[field] = raw_value
        if any(request.get(key) != value for key, value in exact.items()):
            raise RuntimeError("minecraft_action_correlation_mismatch")
        return record

    async def _force_stop_after_action_failure(
        self,
        record: dict[str, Any],
    ) -> None:
        lease = record.get("lease")
        request = record.get("request") or {}
        if (
            isinstance(lease, MinecraftWorldLease)
            and self._lease is not None
            and self._lease.lease_id == lease.lease_id
        ):
            self._revoke_lease(reason="action_failed")
        await self._shielded_stop_runtime(
            guild_id=int(request.get("guildId") or 0),
            reason="action_failed",
            force=True,
            lease=(
                lease
                if isinstance(lease, MinecraftWorldLease)
                else None
            ),
        )

    async def _cancel_bound_action_locked(
        self,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_action_transport()
        request = validate_minecraft_action_request(
            record.get("request"),
            bound=True,
        )
        lease = record.get("lease")
        if not isinstance(lease, MinecraftWorldLease):
            raise RuntimeError("minecraft_action_not_inflight")
        proof = self._lease_proof(lease) or dict(
            record.get("worldLease") or {}
        )
        if not self._append_required_event(
            "action_cancel_attempted",
            lease=lease,
            reason="action_cancelled",
            outcome="",
            verified=False,
            action_binding=request,
        ):
            await self._force_stop_after_action_failure(record)
            raise RuntimeError(self._boundary_error_code())
        try:
            response = await asyncio.wait_for(
                self.cancel_action_callback(
                    request,
                    world_lease=proof,
                ),
                timeout=self.action_cancel_timeout_sec,
            )
            acknowledged = validate_minecraft_action_dispatch(
                response,
                expected_request=request,
            )
            if acknowledged["status"] != "cancelled":
                raise RuntimeError(
                    "minecraft_action_cancel_unverified"
                )
        except asyncio.CancelledError:
            await self._force_stop_after_action_failure(record)
            raise
        except Exception as exc:
            self._append_required_event(
                "action_cancel_failed",
                lease=lease,
                reason="action_failed",
                outcome="minecraft_action_cancel_unverified",
                verified=False,
                action_binding=request,
            )
            await self._force_stop_after_action_failure(record)
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            ) from exc
        if not self._append_required_event(
            "action_cancel_verified",
            lease=lease,
            reason="action_cancelled",
            outcome="minecraft_action_cancelled",
            verified=True,
            action_binding=request,
        ):
            await self._force_stop_after_action_failure(record)
            raise RuntimeError(self._boundary_error_code())
        self._inflight_actions.pop(request["actionRunId"], None)
        return acknowledged

    async def dispatch_action(
        self,
        guild_id: int,
        request: dict[str, Any],
        *,
        expected_lease_id: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        normalized = validate_minecraft_action_request(
            request,
            bound=False,
        )
        if normalized["guildId"] != int(guild_id):
            raise RuntimeError("minecraft_action_correlation_mismatch")
        async with self._operation_lock:
            if self._shutting_down:
                raise RuntimeError("minecraft_world_owner_shutting_down")
            self._require_action_transport()
            if expected_lease_id is not None and not expected_lease_id:
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            lease, proof = self._authorized_action_lease(
                guild_id,
                expected_lease_id=expected_lease_id or "",
            )
            if self._inflight_actions:
                raise RuntimeError("minecraft_world_action_busy")
            bound = bind_minecraft_action_request(
                normalized,
                goal_run_id=secrets.token_hex(12),
                lease_id=lease.lease_id,
                lease_process_nonce=self.process_nonce,
            )
            if not self._append_required_event(
                "action_dispatch_attempted",
                lease=lease,
                reason="autonomy_action",
                verified=False,
                action_binding=bound,
            ):
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=lease,
                )
                raise RuntimeError(self._boundary_error_code())
            record = {
                "request": bound,
                "lease": lease,
                "worldLease": proof,
                "dispatchedAtMonotonic": self.monotonic(),
            }
            self._inflight_actions[bound["actionRunId"]] = record
            try:
                response = await self.dispatch_action_callback(
                    bound,
                    world_lease=proof,
                )
                acknowledged = validate_minecraft_action_dispatch(
                    response,
                    expected_request=bound,
                )
                if acknowledged["status"] not in {
                    "accepted",
                    "running",
                }:
                    raise RuntimeError(
                        "minecraft_action_dispatch_unverified"
                    )
            except asyncio.CancelledError:
                try:
                    await self._cancel_bound_action_locked(record)
                finally:
                    self._inflight_actions.pop(
                        bound["actionRunId"],
                        None,
                    )
                raise
            except Exception as exc:
                try:
                    await self._cancel_bound_action_locked(record)
                except Exception:
                    pass
                self._append_required_event(
                    "action_failed",
                    lease=lease,
                    reason="action_failed",
                    outcome="minecraft_action_failed",
                    verified=False,
                    action_binding=bound,
                )
                self._inflight_actions.pop(
                    bound["actionRunId"],
                    None,
                )
                raise RuntimeError(
                    "minecraft_action_dispatch_unverified"
                ) from exc
            if not self._append_required_event(
                "action_dispatch_verified",
                lease=lease,
                reason="autonomy_action",
                outcome="minecraft_action_dispatched",
                verified=True,
                action_binding=bound,
            ):
                try:
                    await self._cancel_bound_action_locked(record)
                except Exception:
                    pass
                await self._force_stop_after_action_failure(record)
                raise RuntimeError(self._boundary_error_code())
            return acknowledged

    async def action_status(
        self,
        guild_id: int,
        *,
        goal_run_id: str,
        action_run_id: str,
        action_key: str,
        contract_code: str,
    ) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            self._require_action_transport()
            record = self._get_inflight_action(
                guild_id=guild_id,
                action_run_id=action_run_id,
                goal_run_id=goal_run_id,
                action_key=action_key,
                contract_code=contract_code,
            )
            request = validate_minecraft_action_request(
                record["request"],
                bound=True,
            )
            self._authorized_action_lease(
                guild_id,
                expected_lease_id=request["leaseId"],
            )
            response = await self.get_action_status_callback(
                request["goalRunId"]
            )
            if (
                isinstance(response, dict)
                and response.get("schema")
                == MINECRAFT_ACTION_RESULT_SCHEMA
            ):
                result = validate_minecraft_action_result(
                    response,
                    expected_request=request,
                )
                if not self._append_required_event(
                    "action_completed",
                    lease=record["lease"],
                    reason="autonomy_action",
                    outcome="minecraft_action_completed",
                    verified=True,
                    action_binding=request,
                ):
                    await self._force_stop_after_action_failure(
                        record
                    )
                    raise RuntimeError(self._boundary_error_code())
                self._inflight_actions.pop(
                    request["actionRunId"],
                    None,
                )
                return result
            status = validate_minecraft_action_dispatch(
                response,
                expected_request=request,
            )
            if status["status"] in {"failed", "cancelled"}:
                event = (
                    "action_cancel_verified"
                    if status["status"] == "cancelled"
                    else "action_failed"
                )
                reason = (
                    "action_cancelled"
                    if status["status"] == "cancelled"
                    else "action_failed"
                )
                outcome = (
                    "minecraft_action_cancelled"
                    if status["status"] == "cancelled"
                    else "minecraft_action_failed"
                )
                if not self._append_required_event(
                    event,
                    lease=record["lease"],
                    reason=reason,
                    outcome=outcome,
                    verified=(status["status"] == "cancelled"),
                    action_binding=request,
                ):
                    await self._force_stop_after_action_failure(
                        record
                    )
                    raise RuntimeError(self._boundary_error_code())
                self._inflight_actions.pop(
                    request["actionRunId"],
                    None,
                )
            return status

    async def cancel_action(
        self,
        guild_id: int,
        action_run_id: str,
        *,
        expected_lease_id: str | None = None,
    ) -> dict[str, Any]:
        await self.ensure_started()
        async with self._operation_lock:
            record = self._get_inflight_action(
                guild_id=guild_id,
                action_run_id=action_run_id,
            )
            request = validate_minecraft_action_request(
                record["request"],
                bound=True,
            )
            if (
                expected_lease_id is not None
                and request["leaseId"] != expected_lease_id
            ):
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            return await self._cancel_bound_action_locked(record)

    async def execute_action(
        self,
        guild_id: int,
        request: dict[str, Any],
        *,
        expected_lease_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._action_execution_lock:
            dispatch = await self.dispatch_action(
                guild_id,
                request,
                expected_lease_id=expected_lease_id,
            )
            action_run_id = dispatch["actionRunId"]
            action_lease_id = dispatch["leaseId"]
            deadline = self.monotonic() + self.action_timeout_sec
            try:
                while self.monotonic() < deadline:
                    status = await self.action_status(
                        guild_id,
                        goal_run_id=dispatch["goalRunId"],
                        action_run_id=action_run_id,
                        action_key=dispatch["actionKey"],
                        contract_code=dispatch["contractCode"],
                    )
                    if status.get("schema") == MINECRAFT_ACTION_RESULT_SCHEMA:
                        return status
                    if status.get("status") == "failed":
                        raise RuntimeError("minecraft_action_failed")
                    if status.get("status") == "cancelled":
                        raise RuntimeError("minecraft_action_cancelled")
                    await self.sleep(self.action_poll_interval_sec)
                await self.cancel_action(
                    guild_id,
                    action_run_id,
                    expected_lease_id=action_lease_id,
                )
                raise RuntimeError("minecraft_action_timeout")
            except asyncio.CancelledError:
                try:
                    await self.cancel_action(
                        guild_id,
                        action_run_id,
                        expected_lease_id=action_lease_id,
                    )
                except Exception:
                    pass
                raise
            except Exception:
                if action_run_id in self._inflight_actions:
                    try:
                        await self.cancel_action(
                            guild_id,
                            action_run_id,
                            expected_lease_id=action_lease_id,
                        )
                    except Exception as cancel_exc:
                        raise RuntimeError(
                            "minecraft_action_cancel_unverified"
                        ) from cancel_exc
                raise

    async def _cancel_inflight_before_shutdown(self) -> tuple[bool, bool]:
        """Release a known service-held action lock before handoff fencing."""

        async with self._operation_lock:
            records = tuple(self._inflight_actions.values())
            cancellation_failed = False
            for record in records:
                try:
                    await self._cancel_bound_action_locked(record)
                except asyncio.CancelledError:
                    cancellation_failed = True
                    request = record.get("request") or {}
                    if request.get("actionRunId") in self._inflight_actions:
                        await self._force_stop_after_action_failure(
                            record
                        )
                except Exception:
                    cancellation_failed = True
                    request = record.get("request") or {}
                    if request.get("actionRunId") in self._inflight_actions:
                        await self._force_stop_after_action_failure(
                            record
                        )
            return bool(records), cancellation_failed

    async def _shutdown_runtime_cleanup(
        self,
        *,
        reason: str,
    ) -> dict[str, Any]:
        async with self._operation_lock:
            lease = self._lease
            guild_id = lease.guild_id if lease is not None else 0
            revoke_audited = self._revoke_lease(reason=reason)
            try:
                runtime_status = await self._runtime_status()
            except asyncio.CancelledError:
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=reason,
                    force=True,
                    lease=lease,
                )
                raise
            if (
                not runtime_status.get("_status_unavailable")
                and not minecraft_runtime_active(runtime_status)
            ):
                stop_audited = True
                if lease is not None:
                    stop_audited = self._append_required_event(
                        "runtime_stop_verified",
                        lease=lease,
                        reason=reason,
                        outcome=MINECRAFT_STOPPED_OUTCOME,
                        verified=True,
                    )
                    if stop_audited:
                        self._last_stop_outcome = MINECRAFT_STOPPED_OUTCOME
                        self._stop_attempts.clear()
                if (
                    revoke_audited
                    and stop_audited
                    and self._audit_ready
                    and self._status_ready
                    and self._secret_ready
                ):
                    self._state = "authorization_required"
                    self._last_error_code = ""
                self._write_status()
                result = {
                    "stopped": True,
                    "action": "already_stopped",
                }
                boundary_error = self._boundary_error_code()
                if boundary_error:
                    result["error"] = boundary_error
                return result
            stopped = await self._shielded_stop_runtime(
                guild_id=guild_id,
                reason=reason,
                force=True,
                lease=lease,
            )
            result = {
                "stopped": stopped,
                "action": "shutdown_stop",
            }
            boundary_error = self._boundary_error_code()
            if boundary_error:
                result["error"] = boundary_error
            return result

    async def _await_shutdown_step(
        self,
        awaitable: Awaitable[Any],
    ) -> tuple[Any, bool]:
        """Finish one shutdown step even if its caller is cancelled."""

        step_task = asyncio.create_task(awaitable)
        cancellation_requested = False
        while not step_task.done():
            try:
                await asyncio.shield(step_task)
            except asyncio.CancelledError:
                cancellation_requested = True
                continue
        return step_task.result(), cancellation_requested

    async def _acquire_world_action_lock_for_shutdown(
        self,
        *,
        timeout_sec: float | None = None,
    ) -> bool:
        """Poll the nonblocking primitive without orphan worker threads."""

        cancellation_requested = False
        deadline = (
            asyncio.get_running_loop().time() + timeout_sec
            if timeout_sec is not None
            else None
        )
        while True:
            try:
                self._world_action_lock.acquire()
                return cancellation_requested
            except MinecraftOwnerLockBusy:
                if (
                    deadline is not None
                    and asyncio.get_running_loop().time() >= deadline
                ):
                    raise TimeoutError(
                        MINECRAFT_WORLD_ACTION_LOCK_TIMEOUT
                    )
                try:
                    await asyncio.sleep(WORLD_ACTION_LOCK_RETRY_SEC)
                except asyncio.CancelledError:
                    cancellation_requested = True
                    continue

    async def _shutdown_serialized_cleanup(
        self,
        *,
        reason: str,
    ) -> dict[str, Any]:
        with self._data_lock:
            if not self._owner_lock.acquired:
                return {
                    "stopped": False,
                    "action": "owner_conflict",
                }

        action_lock_acquired = False
        action_lock_error = ""
        stale_grace_elapsed = False
        cancellation_requested = False
        result: dict[str, Any]
        try:
            pre_cancel_value, cancelled = await self._await_shutdown_step(
                self._cancel_inflight_before_shutdown()
            )
            known_inflight, action_cancel_failed = pre_cancel_value
            cancellation_requested = cancellation_requested or cancelled
            # A service may already have validated a proof while holding this
            # lock. A locally tracked action is cancelled first because its
            # service lock is intentionally held until terminal/cancel.
            # Unknown external admissions retain the original wait semantics.
            try:
                lock_wait_cancelled = (
                    await self._acquire_world_action_lock_for_shutdown(
                        timeout_sec=(
                            self.action_shutdown_lock_timeout_sec
                            if known_inflight
                            else None
                        ),
                    )
                )
                cancellation_requested = (
                    cancellation_requested or lock_wait_cancelled
                )
                action_lock_acquired = True
            except TimeoutError:
                action_lock_error = MINECRAFT_WORLD_ACTION_LOCK_TIMEOUT
            except (MinecraftOwnerLockUnavailable, OSError):
                action_lock_error = (
                    MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE
                )
            if action_lock_error:
                with self._data_lock:
                    self._state = "manual_intervention_required"
                    self._last_error_code = action_lock_error
                    self._withhold_delegation_capability()
                # No new proof can be admitted through a shared unavailable
                # or bounded-out boundary. Carry any request admitted before
                # the fault past the stale window, then safety-stop the world.
                _, cancelled = await self._await_shutdown_step(
                    self.sleep(WORLD_LEASE_ARTIFACT_FENCE_GRACE_SEC)
                )
                cancellation_requested = (
                    cancellation_requested or cancelled
                )
                stale_grace_elapsed = True

            result_value, cancelled = await self._await_shutdown_step(
                self._shutdown_runtime_cleanup(reason=reason)
            )
            result = dict(result_value)
            cancellation_requested = cancellation_requested or cancelled
            if action_lock_error:
                result["error"] = action_lock_error
            elif action_cancel_failed:
                result["error"] = (
                    "minecraft_action_cancel_unverified"
                )
        finally:
            with self._data_lock:
                authority_fenced = self._release_owner_claim()
            release_ready = authority_fenced
            if not authority_fenced:
                # Neither shared artifact could be changed.  The admission
                # lock remains held while every previously published status
                # crosses the contract's maximum clock-skew + stale window.
                if not stale_grace_elapsed:
                    _, cancelled = await self._await_shutdown_step(
                        self.sleep(
                            WORLD_LEASE_ARTIFACT_FENCE_GRACE_SEC
                        )
                    )
                    cancellation_requested = (
                        cancellation_requested or cancelled
                    )
                with self._data_lock:
                    self._force_release_owner_lock_after_fence_grace()
                release_ready = True
            if release_ready and (
                action_lock_acquired
                or self._world_action_lock.acquired
            ):
                self._world_action_lock.release()
            if release_ready:
                self._world_action_lock_quarantined = False
        if cancellation_requested:
            raise asyncio.CancelledError()
        return result

    async def _shielded_shutdown_runtime_cleanup(
        self,
        *,
        reason: str,
    ) -> dict[str, Any]:
        cleanup_task = asyncio.create_task(
            self._shutdown_serialized_cleanup(reason=reason)
        )
        cancellation_requested = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancellation_requested = True
                continue
        result = cleanup_task.result()
        if cancellation_requested:
            raise asyncio.CancelledError()
        return result

    async def shutdown(self, *, reason: str = "shutdown") -> dict[str, Any]:
        self._shutting_down = True
        cancellation_requested = False
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                cancellation_requested = bool(
                    current_task is not None
                    and current_task.cancelling()
                )
            except Exception as exc:
                self.log(
                    "[MINECRAFT WORLD LEASE] watchdog shutdown failed",
                    "type=",
                    type(exc).__name__,
                )
        result = await self._shielded_shutdown_runtime_cleanup(reason=reason)
        if cancellation_requested:
            raise asyncio.CancelledError()
        return result


def build_local_minecraft_world_lease_owner(
    *,
    status_path: Path,
    events_dir: Path,
    get_client: Callable[[], Any],
    enable_mode: Callable[..., Awaitable[dict[str, Any]]],
    disable_mode: Callable[[int], Awaitable[dict[str, Any]]],
    create_task: Callable[[Awaitable[Any]], Any] = asyncio.create_task,
    log: Callable[..., Any] = print,
) -> MinecraftWorldLeaseOwner:
    """Compose the in-process owner with the typed Mindcraft client."""

    return MinecraftWorldLeaseOwner(
        status_path=status_path,
        events_dir=events_dir,
        get_runtime_status=lambda: get_client().status(),
        enable_mode=enable_mode,
        disable_mode=disable_mode,
        set_goal=lambda goal, **kwargs: get_client().set_goal(goal, **kwargs),
        dispatch_action=(
            lambda request, **kwargs: get_client().dispatch_action(
                request,
                **kwargs,
            )
        ),
        get_action_status=(
            lambda goal_run_id: get_client().action_status(goal_run_id)
        ),
        cancel_action=(
            lambda request, **kwargs: get_client().cancel_action(
                request,
                **kwargs,
            )
        ),
        create_task=create_task,
        log=log,
    )


__all__ = [
    "DEFAULT_WATCHDOG_INTERVAL_SEC",
    "DEFAULT_WORLD_LEASE_TTL_SEC",
    "MAX_WORLD_LEASE_TTL_SEC",
    "MINECRAFT_WORLD_ACTION_LOCK_BUSY",
    "MINECRAFT_WORLD_ACTION_LOCK_TIMEOUT",
    "MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE",
    "MINECRAFT_WORLD_LEASE_EVENT_SCHEMA",
    "MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED",
    "MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA",
    "MINECRAFT_WORLD_LEASE_OWNER_CLAIM_WRITE_FAILED",
    "MINECRAFT_WORLD_LEASE_OWNER_CONFLICT",
    "MINECRAFT_WORLD_LEASE_OWNER_LOCK_UNAVAILABLE",
    "MINECRAFT_WORLD_LEASE_STATUS_SCHEMA",
    "MinecraftWorldLease",
    "MinecraftWorldLeaseOwner",
    "build_local_minecraft_world_lease_owner",
    "minecraft_runtime_active",
]
