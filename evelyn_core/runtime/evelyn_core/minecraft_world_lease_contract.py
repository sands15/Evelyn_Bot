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
MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA = (
    "minecraft_world_lease.owner_claim.v1"
)
MINECRAFT_WORLD_LEASE_OWNER_CONFLICT = (
    "minecraft_world_lease_owner_conflict"
)
MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE = (
    "minecraft_world_lease_audit_unavailable"
)
MINECRAFT_WORLD_LEASE_STATUS_WRITE_FAILED = (
    "minecraft_world_lease_status_write_failed"
)
DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC = 15.0


def _canonical_nonce(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return value if value == value.strip() else ""


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


def _owner_claim_path(
    status_path: Path,
    owner_claim_path: Path | None,
) -> Path:
    return Path(
        owner_claim_path
        if owner_claim_path is not None
        else Path(status_path).parent / "owner_claim.json"
    )


def _read_owner_claim_nonce(path: Path) -> str:
    payload = _read_json_object(Path(path))
    process_nonce = _canonical_nonce(payload.get("processNonce"))
    if (
        payload.get("schema")
        != MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA
        or not process_nonce
    ):
        return ""
    return process_nonce


def _owner_claim_matches(
    path: Path,
    *,
    process_nonce: str,
) -> bool:
    expected = _canonical_nonce(process_nonce)
    observed = _read_owner_claim_nonce(Path(path))
    return bool(
        expected
        and observed
        and hmac.compare_digest(expected, observed)
    )


def build_world_lease_proof(
    status: dict[str, Any],
    *,
    authorization_token: str = "",
) -> dict[str, Any]:
    lease = status.get("lease")
    if not isinstance(lease, dict):
        return {}
    lease_id = str(lease.get("leaseId") or "").strip()
    process_nonce = _canonical_nonce(status.get("processNonce"))
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
    lease_id: str = "",
    monotonic_now: float | None = None,
) -> tuple[str, str]:
    payload = _read_json_object(Path(secret_path))
    if (
        payload.get("schema")
        != MINECRAFT_WORLD_LEASE_SECRET_SCHEMA
    ):
        return "", "minecraft_world_lease_secret_missing"
    stored_nonce = _canonical_nonce(payload.get("processNonce"))
    expected_nonce = _canonical_nonce(process_nonce)
    token = str(payload.get("authorizationToken") or "").strip()
    if (
        not stored_nonce
        or not expected_nonce
        or not token
        or not hmac.compare_digest(stored_nonce, expected_nonce)
    ):
        return "", "minecraft_world_lease_secret_mismatch"
    expected_lease_id = str(lease_id or "").strip()
    if expected_lease_id:
        stored_lease_id = str(payload.get("leaseId") or "").strip()
        expires_monotonic = _finite_float(
            payload.get("expiresMonotonic")
        )
        current_monotonic = _finite_float(monotonic_now)
        if (
            not stored_lease_id
            or not hmac.compare_digest(
                stored_lease_id,
                expected_lease_id,
            )
            or expires_monotonic is None
            or current_monotonic is None
        ):
            return "", "minecraft_world_lease_secret_mismatch"
        if expires_monotonic <= current_monotonic:
            return "", "minecraft_world_lease_expired"
    return token, ""


def load_guarded_world_lease(
    status_path: Path,
    secret_path: Path,
    *,
    owner_claim_path: Path | None = None,
    now: float | None = None,
    monotonic_now: float | None = None,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[dict[str, Any], str]:
    status, error = load_valid_world_lease(
        status_path,
        owner_claim_path=owner_claim_path,
        now=now,
        heartbeat_max_age_sec=heartbeat_max_age_sec,
    )
    if error:
        return {}, error
    _, secret_error = load_world_lease_authorization_token(
        secret_path,
        process_nonce=str(status.get("processNonce") or ""),
        lease_id=str((status.get("lease") or {}).get("leaseId") or ""),
        monotonic_now=(
            time.monotonic()
            if monotonic_now is None
            else monotonic_now
        ),
    )
    if secret_error:
        return {}, secret_error
    claim_path = _owner_claim_path(status_path, owner_claim_path)
    if not _owner_claim_matches(
        claim_path,
        process_nonce=str(status.get("processNonce") or ""),
    ):
        return {}, MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
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
    owner_claim_path: Path | None = None,
    now: float | None = None,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[dict[str, Any], str]:
    status = _read_json_object(Path(status_path))
    valid, error = validate_world_lease_status(
        status,
        now=now,
        heartbeat_max_age_sec=heartbeat_max_age_sec,
    )
    if not valid:
        return {}, error
    process_nonce = str(status.get("processNonce") or "")
    claim_path = _owner_claim_path(status_path, owner_claim_path)
    if not _owner_claim_matches(
        claim_path,
        process_nonce=process_nonce,
    ):
        return {}, MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
    # Re-read the authoritative claim as the final artifact-snapshot fence.
    # Effect consumers additionally hold world_action.lock across this read
    # and the effect commit; this helper alone cannot serialize an effect.
    if not _owner_claim_matches(
        claim_path,
        process_nonce=process_nonce,
    ):
        return {}, MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
    return status, ""


def validate_world_lease_request(
    payload: Any,
    *,
    status_path: Path,
    secret_path: Path,
    owner_claim_path: Path | None = None,
    now: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    heartbeat_max_age_sec: float = DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC,
) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "minecraft_world_lease_proof_missing"
    presented = payload.get("worldLease")
    if not isinstance(presented, dict):
        return False, "minecraft_world_lease_proof_missing"
    status, error = load_valid_world_lease(
        status_path,
        owner_claim_path=owner_claim_path,
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
        lease_id=str(expected.get("leaseId") or ""),
        monotonic_now=monotonic(),
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
    claim_path = _owner_claim_path(status_path, owner_claim_path)
    if not _owner_claim_matches(
        claim_path,
        process_nonce=str(expected.get("processNonce") or ""),
    ):
        return False, MINECRAFT_WORLD_LEASE_OWNER_CONFLICT
    return True, ""


__all__ = [
    "DEFAULT_WORLD_LEASE_HEARTBEAT_MAX_AGE_SEC",
    "MINECRAFT_WORLD_LEASE_AUDIT_UNAVAILABLE",
    "MINECRAFT_WORLD_LEASE_OWNER_CLAIM_SCHEMA",
    "MINECRAFT_WORLD_LEASE_OWNER_CONFLICT",
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
