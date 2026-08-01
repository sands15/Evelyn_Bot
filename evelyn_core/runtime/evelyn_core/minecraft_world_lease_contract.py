from __future__ import annotations

import json
import hmac
import math
import time
from pathlib import Path
from typing import Any, Callable


MINECRAFT_WORLD_LEASE_STATUS_SCHEMA = "minecraft_world_lease.status.v1"
MINECRAFT_WORLD_LEASE_PROOF_SCHEMA = "minecraft_world_lease.proof.v1"
MINECRAFT_WORLD_LEASE_SECRET_SCHEMA = "minecraft_world_lease.secret.v1"
MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE = (
    "minecraft_world_lease_audit_unavailable"
)
MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED = (
    "minecraft_world_lease_status_write_failed"
)
DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC = 15.0


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_guild_id(value: Any) -> int | None:
    try:
        guild_id = int(value)
    except (TypeError, ValueError):
        return None
    return guild_id if guild_id >= 0 else None


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_world_lease_proof(
    status: dict[str, Any],
    *,
    authorization_token: str = "",
) -> dict[str, Any]:
    lease = status.get("lease")
    if not isinstance(lease, dict):
        return {}
    lease_id = str(lease.get("leaseId") or "").strip()
    process_nonce = str(status.get("processNonce") or "").strip()
    guild_id = _safe_guild_id(lease.get("guildId"))
    expires_at = _finite_float(lease.get("expiresAt"))
    if not lease_id or not process_nonce or guild_id is None or expires_at is None:
        return {}
    proof = {
        "schema": MINECRAFT_WORLD_LEASE_PROOF_SCHEMA,
        "leaseId": lease_id,
        "guildId": guild_id,
        "processNonce": process_nonce,
        "expiresAt": expires_at,
    }
    token = str(authorization_token or "").strip()
    if token:
        proof["authorizationToken"] = token
    return proof


def load_world_lease_authorization_token(
    secret_path: Path,
    *,
    process_nonce: str,
) -> tuple[str, str]:
    payload = _read_json_object(Path(secret_path))
    if (
        payload.get("schema")
        != MINECRAFT_WORLD_LEASE_SECRET_SCHEMA
    ):
        return "", "minecraft_world_lease_secret_missing"
    stored_nonce = str(payload.get("processNonce") or "").strip()
    token = str(payload.get("authorizationToken") or "").strip()
    if (
        not stored_nonce
        or not token
        or not hmac.compare_digest(stored_nonce, process_nonce)
    ):
        return "", "minecraft_world_lease_secret_mismatch"
    return token, ""


def load_guarded_world_lease(
    status_path: Path,
    secret_path: Path,
    *,
    now: float | None = None,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[dict[str, Any], str]:
    status, error = load_valid_world_lease(
        status_path,
        now=now,
        heartbeat_max_age_sec=heartbeat_max_age_sec,
    )
    if error:
        return {}, error
    _, secret_error = load_world_lease_authorization_token(
        secret_path,
        process_nonce=str(status.get("processNonce") or ""),
    )
    if secret_error:
        return {}, secret_error
    return status, ""


def validate_world_lease_status(
    status: Any,
    *,
    now: float | None = None,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[bool, str]:
    if not isinstance(status, dict) or not status:
        return False, "minecraft_world_lease_status_missing"
    if status.get("schema") != MINECRAFT_WORLD_LEASE_STATUS_SCHEMA:
        return False, "minecraft_world_lease_status_invalid"
    if status.get("auditReady") is not True:
        return False, MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE
    if status.get("statusReady") is not True:
        return False, MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED
    if status.get("state") != "authorized" or status.get("active") is not True:
        return False, "minecraft_world_authorization_required"
    lease = status.get("lease")
    if not isinstance(lease, dict):
        return False, "minecraft_world_lease_status_invalid"
    current_time = time.time() if now is None else float(now)
    updated_at = _finite_float(status.get("updatedAt"))
    expires_at = _finite_float(lease.get("expiresAt"))
    if updated_at is None or current_time - updated_at > max(1.0, heartbeat_max_age_sec):
        return False, "minecraft_world_lease_heartbeat_stale"
    if updated_at > current_time + max(1.0, heartbeat_max_age_sec):
        return False, "minecraft_world_lease_clock_invalid"
    if expires_at is None or expires_at <= current_time:
        return False, "minecraft_world_lease_expired"
    if not build_world_lease_proof(status):
        return False, "minecraft_world_lease_status_invalid"
    return True, ""


def load_valid_world_lease(
    status_path: Path,
    *,
    now: float | None = None,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[dict[str, Any], str]:
    status = _read_json_object(Path(status_path))
    valid, error = validate_world_lease_status(
        status,
        now=now,
        heartbeat_max_age_sec=heartbeat_max_age_sec,
    )
    return (status if valid else {}), error


def validate_world_lease_request(
    payload: Any,
    *,
    status_path: Path,
    secret_path: Path,
    now: Callable[[], float] = time.time,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "minecraft_world_lease_proof_missing"
    presented = payload.get("worldLease")
    if not isinstance(presented, dict):
        return False, "minecraft_world_lease_proof_missing"
    status, error = load_valid_world_lease(
        status_path,
        now=now(),
        heartbeat_max_age_sec=heartbeat_max_age_sec,
    )
    if error:
        return False, error
    expected = build_world_lease_proof(status)
    if presented.get("schema") != MINECRAFT_WORLD_LEASE_PROOF_SCHEMA:
        return False, "minecraft_world_lease_proof_invalid"
    for key in ("leaseId", "guildId", "processNonce", "expiresAt"):
        if presented.get(key) != expected.get(key):
            return False, "minecraft_world_lease_proof_mismatch"
    expected_token, token_error = load_world_lease_authorization_token(
        secret_path,
        process_nonce=str(expected.get("processNonce") or ""),
    )
    if token_error:
        return False, token_error
    presented_token = str(
        presented.get("authorizationToken") or ""
    ).strip()
    if (
        not presented_token
        or not hmac.compare_digest(
            presented_token,
            expected_token,
        )
    ):
        return False, "minecraft_world_lease_secret_mismatch"
    return True, ""


__all__ = [
    "DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC",
    "MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE",
    "MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED",
    "MINECRAFT_WORLD_LEASE_PROOF_SCHEMA",
    "MINECRAFT_WORLD_LEASE_SECRET_SCHEMA",
    "MINECRAFT_WORLD_LEASE_STATUS_SCHEMA",
    "build_world_lease_proof",
    "load_guarded_world_lease",
    "load_valid_world_lease",
    "load_world_lease_authorization_token",
    "validate_world_lease_request",
    "validate_world_lease_status",
]
