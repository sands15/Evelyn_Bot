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
_OWNER_AUTHORITY_ERROR_CODES = frozenset(
    {
        MINECRAFT_WORLD_LEASE_OWNER_CONFLICT,
        MINECRAFT_WORLD_LEASE_OWNER_LOCK_UNAVAILABLE,
        MINECRAFT_WORLD_LEASE_OWNER_CLAIM_WRITE_FAILED,
        MINECRAFT_WORLD_LEASE_OWNER_CLAIM_FAILED,
        MINECRAFT_WORLD_ACTION_LOCK_BUSY,
        MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE,
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
        self._start_lock = asyncio.Lock()
        self._watchdog_task: Any = None
        self._next_standby_probe_at = 0.0

    def _append_event(
        self,
        event: str,
        *,
        lease: MinecraftWorldLease | None = None,
        guild_id: int | None = None,
        reason: str = "",
        outcome: str = "",
        verified: bool | None = None,
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

    def _write_secret(self) -> None:
        self._require_owner_claim()
        atomic_json_write(
            self.secret_path,
            {
                "schema": MINECRAFT_WORLD_LEASE_SECRET_SCHEMA,
                "processNonce": self.process_nonce,
                "authorizationToken": self.authorization_token,
                "issuedAt": self.now(),
            },
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
        if not self._write_status():
            raise RuntimeError(self._boundary_error_code())
        return lease

    def _revoke_lease(self, *, reason: str) -> bool:
        lease = self._lease
        self._lease = None
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
            if lease is not None and lease.expires_at <= self.now():
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
                        and lease.expires_at > self.now()
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
            if current is not None and current.expires_at <= self.now():
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
            boundary_error = self._boundary_error_code()
            if boundary_error:
                await self._shielded_stop_runtime(
                    guild_id=guild_id,
                    reason=self._boundary_stop_reason(),
                    force=True,
                    lease=self._lease,
                )
                raise RuntimeError(boundary_error)
            lease = self._lease
            if (
                lease is None
                or lease.expires_at <= self.now()
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

    async def _acquire_world_action_lock_for_shutdown(self) -> bool:
        """Poll the nonblocking primitive without orphan worker threads."""

        cancellation_requested = False
        while True:
            try:
                self._world_action_lock.acquire()
                return cancellation_requested
            except MinecraftOwnerLockBusy:
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
        action_lock_unavailable = False
        stale_grace_elapsed = False
        cancellation_requested = False
        result: dict[str, Any]
        try:
            # A service may already have validated a proof while holding this
            # lock.  Wait for that effect to commit before stopping the world,
            # and keep the lock through artifact fencing and owner release.
            try:
                cancellation_requested = (
                    await self._acquire_world_action_lock_for_shutdown()
                )
                action_lock_acquired = True
            except (MinecraftOwnerLockUnavailable, OSError):
                action_lock_unavailable = True
                with self._data_lock:
                    self._state = "manual_intervention_required"
                    self._last_error_code = (
                        MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE
                    )
                    self._withhold_delegation_capability()
                # No new proof can be admitted through a shared unavailable
                # boundary.  Carry any request admitted before the fault past
                # the same stale window, then perform the final safety stop.
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
            if action_lock_unavailable:
                result["error"] = MINECRAFT_WORLD_ACTION_LOCK_UNAVAILABLE
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


__all__ = [
    "DEFAULT_WATCHDOG_INTERVAL_SEC",
    "DEFAULT_WORLD_LEASE_TTL_SEC",
    "MAX_WORLD_LEASE_TTL_SEC",
    "MINECRAFT_WORLD_ACTION_LOCK_BUSY",
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
    "minecraft_runtime_active",
]
