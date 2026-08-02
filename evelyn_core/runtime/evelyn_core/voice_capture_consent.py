from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import secrets
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Literal

from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write, read_bounded_json


CONSENT_SCHEMA = "voice.capture-consent.v1"
PREVIEW_SCHEMA = "voice.capture-consent.preview.v1"
VALIDATION_BINDING_SCHEMA = "voice.capture-consent.validation-binding.v1"
HOST_LEASE_SCHEMA = "voice.capture-consent.host-lease.v1"
WATCHDOG_STATUS_SCHEMA = "voice.capture-consent.watchdog-status.v1"
VOICE_CAPTURE_AUTH_ALGORITHM = "hmac-sha256"
VOICE_CAPTURE_AUTH_ENV = "EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN"
HOST_LEASE_AUTH_SCOPE = "host_lease"
BRIDGE_STATUS_AUTH_SCOPE = "bridge_status"
SUPERVISOR_STOP_AUTH_SCOPE = "supervisor_stop"
SCOPE = "voice_validation_local"
PREVIEW_TTL_SEC = 120.0
ARMED_TTL_SEC = 300.0
ACTIVE_TTL_SEC = 1800.0
HOST_LEASE_STALE_SEC = 4.0
HOST_LEASE_MAX_BYTES = 4096
ACTIVE_STATES = frozenset({"enabling", "active", "revoking"})
TERMINAL_VALIDATION_STATES = frozenset({"passed", "failed", "aborted"})
LOAD_STATES = frozenset({"verified", "missing", "untrusted"})
_CONSENT_STATES = frozenset({"inactive", *ACTIVE_STATES})
_VALIDATION_STATES = frozenset(
    {"idle", "preflight", "running", *TERMINAL_VALIDATION_STATES}
)
_STATE_KEYS = frozenset(
    {
        "schema",
        "state",
        "scope",
        "ownerNonce",
        "leaseId",
        "validationSessionId",
        "requestedAt",
        "activatedAt",
        "expiresAt",
        "updatedAt",
        "lastError",
        "lastRevocationReason",
        "revokedAt",
    }
)
_HOST_LEASE_KEYS = frozenset(
    {
        "schema",
        "scope",
        "state",
        "ownerDigest",
        "leaseDigest",
        "expiresAt",
        "heartbeatAt",
        "contentFree",
        "authAlgorithm",
        "authTag",
    }
)
_AUTH_DOMAINS = {
    HOST_LEASE_AUTH_SCOPE: b"evelyn.voice-capture.host-lease.v1\n",
    BRIDGE_STATUS_AUTH_SCOPE: b"evelyn.voice-capture.bridge-status.v1\n",
    SUPERVISOR_STOP_AUTH_SCOPE: b"evelyn.voice-capture.supervisor-stop.v1\n",
}
LoadState = Literal["verified", "missing", "untrusted"]


def _finite_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _bounded_string(value: Any, *, maximum: int, allow_empty: bool) -> str | None:
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum:
        return None
    if not value and not allow_empty:
        return None
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def _hex_digest(value: Any, *, allow_empty: bool) -> str | None:
    if value == "" and allow_empty:
        return ""
    if not isinstance(value, str) or len(value) != 64:
        return None
    return value if all(character in "0123456789abcdef" for character in value) else None


def resolve_voice_capture_auth_token(value: str | None = None) -> str:
    return str(
        os.getenv(VOICE_CAPTURE_AUTH_ENV, "") if value is None else value
    ).strip()


def voice_capture_auth_scrubbed_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop(VOICE_CAPTURE_AUTH_ENV, None)
    return environment


def sign_voice_capture_artifact(
    payload: dict[str, Any],
    *,
    auth_scope: str,
    auth_token: str | None = None,
) -> dict[str, Any]:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"authAlgorithm", "authTag"}
    }
    signed = {**unsigned, "authAlgorithm": VOICE_CAPTURE_AUTH_ALGORITHM}
    token = resolve_voice_capture_auth_token(auth_token).encode("utf-8")
    domain = _AUTH_DOMAINS.get(auth_scope)
    if domain is None:
        raise ValueError("voice_capture_auth_scope_invalid")
    if not 32 <= len(token) <= 512:
        signed["authTag"] = ""
        return signed
    digest = hmac.new(token, digestmod=hashlib.sha256)
    digest.update(domain)
    digest.update(
        json.dumps(
            signed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signed["authTag"] = digest.hexdigest()
    return signed


def voice_capture_artifact_is_authentic(
    payload: dict[str, Any],
    *,
    auth_scope: str,
    auth_token: str | None = None,
) -> bool:
    supplied = _hex_digest(payload.get("authTag"), allow_empty=False)
    expected = sign_voice_capture_artifact(
        payload,
        auth_scope=auth_scope,
        auth_token=auth_token,
    )
    return bool(
        payload.get("authAlgorithm") == VOICE_CAPTURE_AUTH_ALGORITHM
        and supplied
        and hmac.compare_digest(supplied, str(expected.get("authTag") or ""))
    )


def inspect_voice_capture_host_lease(
    path: Path,
    *,
    now: Callable[[], float] = time.time,
    stale_after_sec: float = HOST_LEASE_STALE_SEC,
    auth_token: str | None = None,
) -> dict[str, Any]:
    def blocked(
        reason: str,
        *,
        checked_at: float | None = None,
    ) -> dict[str, Any]:
        return {
            "authorized": False,
            "reason": reason,
            "ownerDigest": "",
            "leaseDigest": "",
            "heartbeatAt": None,
            "expiresAt": None,
            "checkedAt": float(now()) if checked_at is None else checked_at,
        }

    try:
        if Path(path).is_symlink():
            return blocked("voice_capture_consent_heartbeat_untrusted")
        payload = read_bounded_json(
            Path(path),
            maximum_bytes=HOST_LEASE_MAX_BYTES,
        )
    except FileNotFoundError:
        return blocked("voice_capture_consent_heartbeat_missing")
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return blocked("voice_capture_consent_heartbeat_untrusted")
    if not isinstance(payload, dict) or set(payload) != _HOST_LEASE_KEYS:
        return blocked("voice_capture_consent_heartbeat_untrusted")
    state = payload.get("state")
    owner_digest = _hex_digest(payload.get("ownerDigest"), allow_empty=False)
    lease_digest = _hex_digest(payload.get("leaseDigest"), allow_empty=True)
    heartbeat_at = _finite_timestamp(payload.get("heartbeatAt"))
    expires_at = _finite_timestamp(payload.get("expiresAt"))
    if (
        payload.get("schema") != HOST_LEASE_SCHEMA
        or payload.get("scope") != SCOPE
        or state not in _CONSENT_STATES
        or owner_digest is None
        or lease_digest is None
        or heartbeat_at is None
        or payload.get("contentFree") is not True
        or (state == "inactive" and (lease_digest or payload.get("expiresAt") is not None))
        or (state != "inactive" and (not lease_digest or expires_at is None))
    ):
        return blocked("voice_capture_consent_heartbeat_untrusted")
    if not voice_capture_artifact_is_authentic(
        payload,
        auth_scope=HOST_LEASE_AUTH_SCOPE,
        auth_token=auth_token,
    ):
        return blocked("voice_capture_consent_heartbeat_untrusted")
    if state not in {"enabling", "active"}:
        return blocked(f"voice_capture_consent_{state}")
    checked_at = float(now())
    age = checked_at - heartbeat_at
    if age < 0.0 or age > max(0.1, float(stale_after_sec)):
        return blocked(
            "voice_capture_consent_heartbeat_stale",
            checked_at=checked_at,
        )
    if checked_at >= expires_at:
        return blocked("voice_capture_consent_expired", checked_at=checked_at)
    return {
        "authorized": True,
        "reason": "",
        "ownerDigest": owner_digest,
        "leaseDigest": lease_digest,
        "heartbeatAt": heartbeat_at,
        "expiresAt": expires_at,
        "checkedAt": checked_at,
    }


def _validated_validation_binding(value: Any) -> dict[str, Any] | None:
    if value is None:
        return {
            "schema": VALIDATION_BINDING_SCHEMA,
            "sessionId": "",
            "state": "idle",
            "usesLocal": False,
        }
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "sessionId",
        "state",
        "usesLocal",
    }:
        return None
    session_id = _bounded_string(
        value.get("sessionId"),
        maximum=512,
        allow_empty=True,
    )
    state = value.get("state")
    if (
        value.get("schema") != VALIDATION_BINDING_SCHEMA
        or session_id is None
        or state not in _VALIDATION_STATES
        or type(value.get("usesLocal")) is not bool
        or (state == "idle") is not (session_id == "")
        or (state == "idle" and value.get("usesLocal") is not False)
    ):
        return None
    return {
        "schema": VALIDATION_BINDING_SCHEMA,
        "sessionId": session_id,
        "state": state,
        "usesLocal": value["usesLocal"],
    }


def _validation_binding_allows_capture(value: dict[str, Any]) -> bool:
    return bool(
        (value.get("state") == "idle" and not value.get("sessionId"))
        or (
            value.get("usesLocal") is True
            and value.get("state") in {"preflight", "running"}
            and bool(value.get("sessionId"))
        )
    )


def _validated_state(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != _STATE_KEYS:
        return None
    state = value.get("state")
    owner_nonce = _bounded_string(
        value.get("ownerNonce"),
        maximum=512,
        allow_empty=False,
    )
    lease_id = _bounded_string(
        value.get("leaseId"),
        maximum=512,
        allow_empty=True,
    )
    validation_session_id = _bounded_string(
        value.get("validationSessionId"),
        maximum=512,
        allow_empty=True,
    )
    last_error = _bounded_string(
        value.get("lastError"),
        maximum=160,
        allow_empty=True,
    )
    last_reason = _bounded_string(
        value.get("lastRevocationReason"),
        maximum=120,
        allow_empty=True,
    )
    updated_at = _finite_timestamp(value.get("updatedAt"))
    revoked_at = (
        None
        if value.get("revokedAt") is None
        else _finite_timestamp(value.get("revokedAt"))
    )
    if (
        value.get("schema") != CONSENT_SCHEMA
        or state not in _CONSENT_STATES
        or value.get("scope") != SCOPE
        or owner_nonce is None
        or lease_id is None
        or validation_session_id is None
        or last_error is None
        or last_reason is None
        or updated_at is None
        or (value.get("revokedAt") is not None and revoked_at is None)
    ):
        return None

    raw_requested_at = value.get("requestedAt")
    raw_activated_at = value.get("activatedAt")
    raw_expires_at = value.get("expiresAt")
    requested_at = (
        None
        if raw_requested_at is None
        else _finite_timestamp(raw_requested_at)
    )
    activated_at = (
        None
        if raw_activated_at is None
        else _finite_timestamp(raw_activated_at)
    )
    expires_at = (
        None
        if raw_expires_at is None
        else _finite_timestamp(raw_expires_at)
    )
    if (
        (raw_requested_at is not None and requested_at is None)
        or (raw_activated_at is not None and activated_at is None)
        or (raw_expires_at is not None and expires_at is None)
    ):
        return None
    if state == "inactive":
        if (
            lease_id
            or validation_session_id
            or requested_at is not None
            or activated_at is not None
            or expires_at is not None
        ):
            return None
    else:
        if (
            not lease_id
            or requested_at is None
            or expires_at is None
            or requested_at > expires_at
            or revoked_at is not None
        ):
            return None
        if state == "enabling" and (
            validation_session_id or activated_at is not None
        ):
            return None
        if state == "active" and (
            activated_at is None
            or activated_at < requested_at
            or activated_at > expires_at
        ):
            return None
    return deepcopy(value)


def _load_state_file(path: Path) -> tuple[LoadState, dict[str, Any] | None]:
    try:
        if path.is_symlink():
            return "untrusted", None
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", None
    except (OSError, UnicodeError):
        return "untrusted", None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return "untrusted", None
    validated = _validated_state(payload)
    return ("verified", validated) if validated is not None else ("untrusted", None)


class VoiceCaptureConsentManager:
    """Content-free, process-owned lease for local validation microphone capture."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        now: Callable[[], float] = time.time,
        owner_nonce: str | None = None,
        preview_ttl_sec: float = PREVIEW_TTL_SEC,
        armed_ttl_sec: float = ARMED_TTL_SEC,
        active_ttl_sec: float = ACTIVE_TTL_SEC,
        auth_token: str | None = None,
    ) -> None:
        base = Path(root or get_runtime_artifacts_root())
        self.state_path = base / "voice_capture_consent" / "state.json"
        self.host_lease_path = (
            base / "voice_capture_consent" / "owner_heartbeat.json"
        )
        self.now = now
        self.owner_nonce = str(owner_nonce or secrets.token_urlsafe(18))
        self.preview_ttl_sec = max(1.0, float(preview_ttl_sec))
        self.armed_ttl_sec = max(1.0, float(armed_ttl_sec))
        self.active_ttl_sec = max(1.0, float(active_ttl_sec))
        self.auth_token = resolve_voice_capture_auth_token(auth_token)
        self._lock = threading.RLock()
        self._previews: dict[str, dict[str, Any]] = {}
        load_state, loaded = _load_state_file(self.state_path)
        self._load_status = load_state
        if loaded is None:
            self._state = self._recovery(
                "consent_state_missing"
                if load_state == "missing"
                else "consent_state_untrusted"
            )
        elif str(loaded.get("ownerNonce") or "") != self.owner_nonce:
            self._state = self._recovery("control_page_restarted")
        elif loaded.get("state") == "enabling":
            self._state = self._recovery("activation_interrupted")
        else:
            self._state = loaded

    def _inactive(
        self,
        *,
        last_error: str = "",
        last_revocation_reason: str = "",
        revoked_at: float | None = None,
    ) -> dict[str, Any]:
        timestamp = self.now()
        return {
            "schema": CONSENT_SCHEMA,
            "state": "inactive",
            "scope": SCOPE,
            "ownerNonce": self.owner_nonce,
            "leaseId": "",
            "validationSessionId": "",
            "requestedAt": None,
            "activatedAt": None,
            "expiresAt": None,
            "updatedAt": timestamp,
            "lastError": str(last_error or "")[:160],
            "lastRevocationReason": str(last_revocation_reason or "")[:120],
            "revokedAt": revoked_at,
        }

    def _recovery(self, reason: str) -> dict[str, Any]:
        timestamp = self.now()
        return {
            "schema": CONSENT_SCHEMA,
            "state": "revoking",
            "scope": SCOPE,
            "ownerNonce": self.owner_nonce,
            "leaseId": f"voice-consent-recovery-{uuid.uuid4().hex}",
            "validationSessionId": "",
            "requestedAt": timestamp,
            "activatedAt": None,
            "expiresAt": timestamp + self.armed_ttl_sec,
            "updatedAt": timestamp,
            "lastError": "",
            "lastRevocationReason": str(reason or "consent_recovery_required")[
                :120
            ],
            "revokedAt": None,
        }

    def _commit_state(self, candidate: dict[str, Any]) -> None:
        committed = deepcopy(candidate)
        committed["updatedAt"] = self.now()
        if _validated_state(committed) is None:
            raise RuntimeError("voice_capture_consent_state_invalid")
        atomic_json_write(self.state_path, committed, durable=True)
        self._state = committed
        self._load_status = "verified"

    def _public(self) -> dict[str, Any]:
        state = deepcopy(self._state)
        state.pop("ownerNonce", None)
        state["active"] = state.get("state") == "active"
        state["loadState"] = self._load_status
        state["recoveryRequired"] = state.get("state") == "revoking"
        state["captureMayBeActive"] = state.get("state") in ACTIVE_STATES
        state["controlRequired"] = state.get("state") == "revoking"
        state["retryRequired"] = state.get("state") == "revoking"
        now = self.now()
        expires_at = _finite_timestamp(state.get("expiresAt"))
        state["remainingSec"] = (
            max(0, int(math.ceil(expires_at - now))) if expires_at is not None else 0
        )
        state["privacy"] = {
            "storesAudio": False,
            "storesTranscript": False,
            "scope": SCOPE,
        }
        return state

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._public()

    def publish_host_lease(self) -> dict[str, Any]:
        """Publish the current content-free owner lease for the Windows host."""
        with self._lock:
            state = self._state
            payload = sign_voice_capture_artifact({
                "schema": HOST_LEASE_SCHEMA,
                "scope": SCOPE,
                "state": state["state"],
                "ownerDigest": _digest(str(state["ownerNonce"])),
                "leaseDigest": _digest(str(state["leaseId"])),
                "expiresAt": state["expiresAt"],
                "heartbeatAt": self.now(),
                "contentFree": True,
            }, auth_scope=HOST_LEASE_AUTH_SCOPE, auth_token=self.auth_token)
            if not payload["authTag"]:
                raise RuntimeError("voice_capture_auth_token_unavailable")
            atomic_json_write(self.host_lease_path, payload)
            return payload

    def _invalidate_previews_locked(self, *, except_token: str = "") -> None:
        """Make every older user-confirmation capability permanently unusable."""
        for token, preview in self._previews.items():
            if token != except_token:
                preview["used"] = True

    def preview(
        self,
        *,
        scope: str = SCOPE,
        validation_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if str(scope or "") != SCOPE:
                return {"ok": False, "error": "voice_capture_scope_not_allowed"}
            binding = _validated_validation_binding(validation_binding)
            if binding is None or not _validation_binding_allows_capture(binding):
                return {
                    "ok": False,
                    "error": "voice_capture_validation_context_not_allowed",
                }
            if self._state.get("state") == "revoking":
                return {
                    "ok": False,
                    "error": "voice_capture_consent_recovery_required",
                    "consent": self._public(),
                }
            if self._state.get("state") in ACTIVE_STATES:
                return {
                    "ok": False,
                    "error": "voice_capture_consent_already_active",
                    "consent": self._public(),
                }
            now = self.now()
            # Only the latest explicit preview may authorize a future ON. A
            # second dialog invalidates an older tab/dialog instead of leaving
            # several independent capabilities alive for the same user intent.
            self._previews.clear()
            token = secrets.token_urlsafe(32)
            expires_at = now + self.preview_ttl_sec
            self._previews[token] = {
                "scope": SCOPE,
                "validationBinding": binding,
                "issuedAt": now,
                "expiresAt": expires_at,
                "used": False,
            }
            return {
                "ok": True,
                "schema": PREVIEW_SCHEMA,
                "scope": SCOPE,
                "confirmToken": token,
                "expiresAt": expires_at,
                "maxConsentSec": int(self.active_ttl_sec),
                "unboundConsentSec": int(self.armed_ttl_sec),
                "privacy": {
                    "storesAudio": False,
                    "storesTranscript": False,
                },
            }

    def begin_apply(
        self,
        *,
        confirm_token: str,
        scope: str = SCOPE,
        validation_binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if str(scope or "") != SCOPE:
                return {"ok": False, "error": "voice_capture_scope_not_allowed"}
            token = str(confirm_token or "")
            preview = self._previews.get(token)
            if preview is None:
                return {"ok": False, "error": "voice_capture_confirm_token_invalid"}
            if preview.get("used"):
                return {"ok": False, "error": "voice_capture_confirm_token_reused"}
            preview["used"] = True
            self._invalidate_previews_locked(except_token=token)
            if self.now() > float(preview.get("expiresAt") or 0.0):
                return {"ok": False, "error": "voice_capture_confirm_token_expired"}
            if self._state.get("state") == "revoking":
                return {
                    "ok": False,
                    "error": "voice_capture_consent_recovery_required",
                    "consent": self._public(),
                }
            binding = _validated_validation_binding(validation_binding)
            if (
                binding is None
                or not _validation_binding_allows_capture(binding)
                or preview.get("validationBinding") != binding
            ):
                return {
                    "ok": False,
                    "error": "voice_capture_confirm_token_stale",
                }
            if self._state.get("state") in ACTIVE_STATES:
                return {
                    "ok": False,
                    "error": "voice_capture_consent_already_active",
                    "consent": self._public(),
                }
            now = self.now()
            lease_id = f"voice-consent-{uuid.uuid4().hex}"
            candidate = {
                "schema": CONSENT_SCHEMA,
                "state": "enabling",
                "scope": SCOPE,
                "ownerNonce": self.owner_nonce,
                "leaseId": lease_id,
                "validationSessionId": "",
                "requestedAt": now,
                "activatedAt": None,
                "expiresAt": now + min(self.active_ttl_sec, self.armed_ttl_sec),
                "updatedAt": now,
                "lastError": "",
                "lastRevocationReason": "",
                "revokedAt": None,
            }
            self._commit_state(candidate)
            return {"ok": True, "leaseId": lease_id, "consent": self._public()}

    def finish_apply(
        self,
        *,
        lease_id: str,
        applied: bool,
        capture_ready: bool,
        error: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if (
                self._state.get("state") != "enabling"
                or str(self._state.get("leaseId") or "") != str(lease_id or "")
            ):
                return {"ok": False, "error": "voice_capture_apply_not_pending"}
            if not applied or not capture_ready:
                reason = str(
                    error
                    or (
                        "voice_capture_not_ready"
                        if applied
                        else "voice_capture_control_not_applied"
                    )
                )[:160]
                # The caller may have lost the ACK after the bridge changed state.
                # Keep the lease recoverable until an explicit mic-off ACK arrives.
                candidate = deepcopy(self._state)
                candidate["state"] = "revoking"
                candidate["lastError"] = reason
                candidate["lastRevocationReason"] = "activation_failed"
                self._commit_state(candidate)
                return {
                    "ok": False,
                    "error": reason,
                    "consent": self._public(),
                }
            now = self.now()
            candidate = deepcopy(self._state)
            candidate["state"] = "active"
            candidate["activatedAt"] = now
            candidate["expiresAt"] = now + min(
                self.active_ttl_sec, self.armed_ttl_sec
            )
            candidate["lastError"] = ""
            self._commit_state(candidate)
            return {"ok": True, "consent": self._public()}

    def bind_validation_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            if self._state.get("state") != "active":
                return {"ok": False, "error": "voice_capture_consent_not_active"}
            normalized = str(session_id or "").strip()
            if not normalized:
                return {"ok": False, "error": "validation_session_id_required"}
            existing = str(self._state.get("validationSessionId") or "")
            if existing and existing != normalized:
                return {
                    "ok": False,
                    "error": "voice_capture_consent_bound_to_other_session",
                }
            candidate = deepcopy(self._state)
            candidate["validationSessionId"] = normalized
            candidate["expiresAt"] = min(
                self.now() + self.active_ttl_sec,
                float(self._state.get("activatedAt") or self.now())
                + self.active_ttl_sec,
            )
            self._commit_state(candidate)
            return {"ok": True, "consent": self._public()}

    def begin_revoke(self, *, reason: str) -> dict[str, Any]:
        with self._lock:
            # Revocation cancels user intent as well as an active lease. A late
            # apply from a dialog opened before OFF must never resurrect capture.
            self._invalidate_previews_locked()
            if self._state.get("state") not in ACTIVE_STATES:
                return {
                    "ok": True,
                    "controlRequired": False,
                    "consent": self._public(),
                }
            candidate = deepcopy(self._state)
            candidate["state"] = "revoking"
            candidate["lastRevocationReason"] = str(reason or "revoked")[:120]
            self._commit_state(candidate)
            return {
                "ok": True,
                "controlRequired": True,
                "leaseId": self._state.get("leaseId"),
                "consent": self._public(),
            }

    def require_recovery(
        self,
        *,
        reason: str,
        error: str = "",
    ) -> dict[str, Any]:
        """Install an in-memory OFF fence even when durable state is unavailable."""
        with self._lock:
            self._invalidate_previews_locked()
            if self._state.get("state") in ACTIVE_STATES:
                candidate = deepcopy(self._state)
                candidate["state"] = "revoking"
                candidate["lastRevocationReason"] = str(
                    reason or "consent_recovery_required"
                )[:120]
                candidate["lastError"] = str(error or candidate.get("lastError") or "")[
                    :160
                ]
            else:
                candidate = self._recovery(reason)
                candidate["lastError"] = str(error or "")[:160]

            # This exceptional transition is intentionally memory-first: once an
            # ON result or persistence outcome is ambiguous, no new grant may be
            # issued even if the recovery record itself cannot be committed.
            self._state = candidate
            try:
                self._commit_state(candidate)
            except BaseException:
                self._state = candidate
                self._load_status = "untrusted"
                raise
            return {
                "ok": True,
                "controlRequired": True,
                "consent": self._public(),
            }

    def finish_revoke(self, *, applied: bool, error: str = "") -> dict[str, Any]:
        with self._lock:
            self._invalidate_previews_locked()
            if self._state.get("state") != "revoking":
                return {"ok": False, "error": "voice_capture_revoke_not_pending"}
            reason = str(self._state.get("lastRevocationReason") or "revoked")
            if not applied:
                candidate = deepcopy(self._state)
                candidate["lastError"] = str(
                    error or "voice_capture_disable_not_applied"
                )[:160]
                self._commit_state(candidate)
                return {
                    "ok": False,
                    "error": candidate["lastError"],
                    "consent": self._public(),
                }
            now = self.now()
            candidate = self._inactive(
                last_revocation_reason=reason,
                revoked_at=now,
            )
            self._commit_state(candidate)
            return {"ok": True, "consent": self._public()}

    def revocation_reason(
        self,
        *,
        validation_session: dict[str, Any] | None = None,
        include_interrupted_enabling: bool = False,
    ) -> str:
        with self._lock:
            state = str(self._state.get("state") or "")
            if state not in ACTIVE_STATES:
                return ""
            if str(self._state.get("ownerNonce") or "") != self.owner_nonce:
                return "control_page_restarted"
            if state == "revoking":
                return str(
                    self._state.get("lastRevocationReason")
                    or "consent_recovery_required"
                )
            if state == "enabling" and include_interrupted_enabling:
                return "activation_interrupted"
            expires_at = _finite_timestamp(self._state.get("expiresAt"))
            if expires_at is None or self.now() >= expires_at:
                return "consent_expired"
            bound_session = str(self._state.get("validationSessionId") or "")
            session = validation_session if isinstance(validation_session, dict) else {}
            current_session = str(session.get("sessionId") or "")
            current_state = str(session.get("state") or "")
            if not bound_session:
                if current_session or current_state != "idle":
                    return "validation_session_started_before_consent_binding"
                return ""
            if current_session != bound_session:
                return "validation_session_replaced"
            if (
                current_session == bound_session
                and current_state in TERMINAL_VALIDATION_STATES
            ):
                return f"validation_session_{session.get('state')}"
            if current_state != "running":
                return "validation_session_state_invalid"
            return ""


_manager: VoiceCaptureConsentManager | None = None
_manager_lock = threading.Lock()


def get_voice_capture_consent_manager() -> VoiceCaptureConsentManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = VoiceCaptureConsentManager()
        return _manager


def attach_voice_capture_consent(
    capabilities: dict[str, Any],
    consent: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(capabilities or {})
    local = dict(result.get("voiceLocal") or {})
    blockers = [
        dict(item)
        for item in (local.get("blockers") or [])
        if isinstance(item, dict)
        and str(item.get("code") or "")
        not in {
            "local_mic_consent_required",
            "local_mic_consent_recovery_required",
        }
    ]
    actions = [
        dict(item)
        for item in (local.get("repairActions") or [])
        if isinstance(item, dict)
        and str(item.get("actionId") or "")
        != "grant_voice_validation_mic_consent"
    ]
    active = bool(consent.get("active"))
    recovery_required = consent.get("recoveryRequired") is True
    if not active:
        blockers.append(
            {
                "code": "local_mic_consent_required",
                "message": "로컬 음성 검증을 위한 시간 제한 마이크 동의가 필요합니다.",
                "serviceId": "local_io_bridge",
            }
        )
        if recovery_required:
            blockers.append(
                {
                    "code": "local_mic_consent_recovery_required",
                    "message": "마이크 OFF 확인 뒤 음성 검증 동의를 다시 시도해야 합니다.",
                    "serviceId": "local_io_bridge",
                }
            )
        bridge_ready = any(
            str(item.get("id") or "") == "local_io_bridge"
            and bool(item.get("ready"))
            for item in (local.get("dependencies") or [])
            if isinstance(item, dict)
        )
        if bridge_ready and not recovery_required:
            actions.append(
                {
                    "actionId": "grant_voice_validation_mic_consent",
                    "serviceId": "local_io_bridge",
                    "label": "검증 세션 동안 마이크 허용",
                    "requiresConfirm": True,
                    "consent": True,
                }
            )
    local["consent"] = deepcopy(consent)
    local["blockers"] = blockers
    local["repairActions"] = actions
    if blockers:
        local["ready"] = False
        local["state"] = "unavailable"
    result["voiceLocal"] = local
    return result


__all__ = [
    "ACTIVE_TTL_SEC",
    "ARMED_TTL_SEC",
    "CONSENT_SCHEMA",
    "BRIDGE_STATUS_AUTH_SCOPE",
    "HOST_LEASE_SCHEMA",
    "HOST_LEASE_AUTH_SCOPE",
    "HOST_LEASE_MAX_BYTES",
    "HOST_LEASE_STALE_SEC",
    "PREVIEW_SCHEMA",
    "SCOPE",
    "SUPERVISOR_STOP_AUTH_SCOPE",
    "VOICE_CAPTURE_AUTH_ALGORITHM",
    "VOICE_CAPTURE_AUTH_ENV",
    "WATCHDOG_STATUS_SCHEMA",
    "VoiceCaptureConsentManager",
    "attach_voice_capture_consent",
    "get_voice_capture_consent_manager",
    "inspect_voice_capture_host_lease",
    "resolve_voice_capture_auth_token",
    "sign_voice_capture_artifact",
    "voice_capture_auth_scrubbed_environment",
    "voice_capture_artifact_is_authentic",
]
