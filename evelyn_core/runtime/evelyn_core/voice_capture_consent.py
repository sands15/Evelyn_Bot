from __future__ import annotations

import json
import math
import os
import secrets
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .paths import get_runtime_artifacts_root


CONSENT_SCHEMA = "voice.capture-consent.v1"
PREVIEW_SCHEMA = "voice.capture-consent.preview.v1"
SCOPE = "voice_validation_local"
PREVIEW_TTL_SEC = 120.0
ARMED_TTL_SEC = 300.0
ACTIVE_TTL_SEC = 1800.0
ACTIVE_STATES = frozenset({"enabling", "active", "revoking"})
TERMINAL_VALIDATION_STATES = frozenset({"passed", "failed", "aborted"})


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _safe_json_read(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _finite_timestamp(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


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
    ) -> None:
        base = Path(root or get_runtime_artifacts_root())
        self.state_path = base / "voice_capture_consent" / "state.json"
        self.now = now
        self.owner_nonce = str(owner_nonce or secrets.token_urlsafe(18))
        self.preview_ttl_sec = max(1.0, float(preview_ttl_sec))
        self.armed_ttl_sec = max(1.0, float(armed_ttl_sec))
        self.active_ttl_sec = max(1.0, float(active_ttl_sec))
        self._lock = threading.RLock()
        self._previews: dict[str, dict[str, Any]] = {}
        self._state = self._load_state()

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

    def _load_state(self) -> dict[str, Any]:
        payload = _safe_json_read(self.state_path)
        if payload and payload.get("schema") == CONSENT_SCHEMA:
            return payload
        return self._inactive()

    def _persist(self) -> None:
        self._state["updatedAt"] = self.now()
        _atomic_json_write(self.state_path, self._state)

    def _public(self) -> dict[str, Any]:
        state = deepcopy(self._state)
        state.pop("ownerNonce", None)
        state["active"] = state.get("state") == "active"
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

    def preview(self, *, scope: str = SCOPE) -> dict[str, Any]:
        with self._lock:
            if str(scope or "") != SCOPE:
                return {"ok": False, "error": "voice_capture_scope_not_allowed"}
            if self._state.get("state") in ACTIVE_STATES:
                return {
                    "ok": False,
                    "error": "voice_capture_consent_already_active",
                    "consent": self._public(),
                }
            now = self.now()
            self._previews = {
                token: preview
                for token, preview in self._previews.items()
                if not preview.get("used")
                and float(preview.get("expiresAt") or 0.0) >= now
            }
            token = secrets.token_urlsafe(32)
            expires_at = now + self.preview_ttl_sec
            self._previews[token] = {
                "scope": SCOPE,
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

    def begin_apply(self, *, confirm_token: str, scope: str = SCOPE) -> dict[str, Any]:
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
            if self.now() > float(preview.get("expiresAt") or 0.0):
                return {"ok": False, "error": "voice_capture_confirm_token_expired"}
            if self._state.get("state") in ACTIVE_STATES:
                return {
                    "ok": False,
                    "error": "voice_capture_consent_already_active",
                    "consent": self._public(),
                }
            now = self.now()
            lease_id = f"voice-consent-{uuid.uuid4().hex}"
            self._state = {
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
            self._persist()
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
                self._state["state"] = "revoking"
                self._state["lastError"] = reason
                self._state["lastRevocationReason"] = "activation_failed"
                self._persist()
                return {
                    "ok": False,
                    "error": reason,
                    "consent": self._public(),
                }
            now = self.now()
            self._state["state"] = "active"
            self._state["activatedAt"] = now
            self._state["expiresAt"] = now + min(
                self.active_ttl_sec, self.armed_ttl_sec
            )
            self._state["lastError"] = ""
            self._persist()
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
            self._state["validationSessionId"] = normalized
            self._state["expiresAt"] = min(
                self.now() + self.active_ttl_sec,
                float(self._state.get("activatedAt") or self.now())
                + self.active_ttl_sec,
            )
            self._persist()
            return {"ok": True, "consent": self._public()}

    def begin_revoke(self, *, reason: str) -> dict[str, Any]:
        with self._lock:
            if self._state.get("state") not in ACTIVE_STATES:
                return {
                    "ok": True,
                    "controlRequired": False,
                    "consent": self._public(),
                }
            self._state["state"] = "revoking"
            self._state["lastRevocationReason"] = str(reason or "revoked")[:120]
            self._persist()
            return {
                "ok": True,
                "controlRequired": True,
                "leaseId": self._state.get("leaseId"),
                "consent": self._public(),
            }

    def finish_revoke(self, *, applied: bool, error: str = "") -> dict[str, Any]:
        with self._lock:
            if self._state.get("state") != "revoking":
                return {"ok": False, "error": "voice_capture_revoke_not_pending"}
            reason = str(self._state.get("lastRevocationReason") or "revoked")
            if not applied:
                self._state["lastError"] = str(
                    error or "voice_capture_disable_not_applied"
                )[:160]
                self._persist()
                return {
                    "ok": False,
                    "error": self._state["lastError"],
                    "consent": self._public(),
                }
            now = self.now()
            self._state = self._inactive(
                last_revocation_reason=reason,
                revoked_at=now,
            )
            self._persist()
            return {"ok": True, "consent": self._public()}

    def revocation_reason(
        self,
        *,
        validation_session: dict[str, Any] | None = None,
    ) -> str:
        with self._lock:
            state = str(self._state.get("state") or "")
            if state not in ACTIVE_STATES:
                return ""
            if str(self._state.get("ownerNonce") or "") != self.owner_nonce:
                return "control_page_restarted"
            expires_at = _finite_timestamp(self._state.get("expiresAt"))
            if expires_at is None or self.now() >= expires_at:
                return "consent_expired"
            if state == "revoking":
                return str(self._state.get("lastRevocationReason") or "revoke_retry")
            bound_session = str(self._state.get("validationSessionId") or "")
            if not bound_session:
                return ""
            session = validation_session if isinstance(validation_session, dict) else {}
            current_session = str(session.get("sessionId") or "")
            if current_session and current_session != bound_session:
                return "validation_session_replaced"
            if (
                current_session == bound_session
                and str(session.get("state") or "") in TERMINAL_VALIDATION_STATES
            ):
                return f"validation_session_{session.get('state')}"
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
        and str(item.get("code") or "") != "local_mic_consent_required"
    ]
    actions = [
        dict(item)
        for item in (local.get("repairActions") or [])
        if isinstance(item, dict)
        and str(item.get("actionId") or "")
        != "grant_voice_validation_mic_consent"
    ]
    active = bool(consent.get("active"))
    if not active:
        blockers.append(
            {
                "code": "local_mic_consent_required",
                "message": "로컬 음성 검증을 위한 시간 제한 마이크 동의가 필요합니다.",
                "serviceId": "local_io_bridge",
            }
        )
        bridge_ready = any(
            str(item.get("id") or "") == "local_io_bridge"
            and bool(item.get("ready"))
            for item in (local.get("dependencies") or [])
            if isinstance(item, dict)
        )
        if bridge_ready:
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
    "PREVIEW_SCHEMA",
    "SCOPE",
    "VoiceCaptureConsentManager",
    "attach_voice_capture_consent",
    "get_voice_capture_consent_manager",
]
