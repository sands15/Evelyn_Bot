from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import secrets
import threading
import time
import unicodedata
from typing import Any, Callable

from .conversation_ingress_recovery import (
    CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
    CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA,
)
from .fast_action_runtime import (
    detect_local_mic_command,
    detect_local_runtime_command,
    detect_minecraft_control_command,
    detect_minecraft_runtime_command,
)


STATUS_SCHEMA = "local_voice.admission.status.v1"
DEFAULT_TOKEN_TTL_SEC = 10.0
DEFAULT_FOLLOWUP_TTL_SEC = 45.0
DEFAULT_REPLAY_TTL_SEC = 120.0
WAKE_WORD = "이블린"

_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z", re.ASCII)
_WAKE_PREFIX_PATTERN = re.compile(
    rf"^{re.escape(WAKE_WORD)}(?=$|[\s,，:：.!?。！？…\-])"
    r"[\s,，:：.!?。！？…\-]*(.*)$",
    re.DOTALL,
)
_MAX_TEXT_CHARS = 16_000
_MAX_LIVE_TOKENS = 256
_MAX_TERMINAL_TOKENS = 512
_MAX_REPLAY_TURNS = 512
_ADMISSION_MODES = frozenset({"wake_entry", "followup", "validation"})
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

ValidationCurrent = Callable[[dict[str, Any]], bool]
DurableRecoveryCurrent = Callable[[], bool]


class LocalVoiceAdmissionTransactionError(RuntimeError):
    """Raised before token consumption when durable claim proof is invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LocalVoiceAdmissionTransaction:
    admission: dict[str, Any]
    ingress_claim: LocalVoiceDurableIngressClaim | None


@dataclass(frozen=True)
class LocalVoiceIssuanceTransaction:
    admission: dict[str, Any]
    reservation: LocalVoiceDurableIssuanceReservation | None


@dataclass(frozen=True)
class LocalVoiceIssuanceReservationRequest:
    bridge_instance_id: str
    turn_id: str
    forward_text_digest: str
    validation_binding_digest: str
    mode: str
    token_digest: str
    capture_fence_digest: str
    ingress_turn_id: str
    reservation_ref: str
    ttl_sec: float


@dataclass(frozen=True)
class LocalVoiceDurableIssuanceReservation:
    schema: str
    durable: bool
    bridge_instance_id: str
    local_turn_id: str
    forward_text_digest: str
    reservation_ref: str
    entry_id: str
    ingress_turn_id: str
    phase: str
    disposition: str
    should_process: bool
    text_hash: str
    journal_generation: int


@dataclass(frozen=True)
class LocalVoiceIngressClaimRequest:
    bridge_instance_id: str
    turn_id: str
    forward_text: str
    forward_text_digest: str
    validation_binding_digest: str
    mode: str
    token_digest: str
    capture_fence_digest: str
    ingress_turn_id: str
    reservation_ref: str


@dataclass(frozen=True)
class LocalVoiceReservationRevocationRequest:
    bridge_instance_id: str
    turn_id: str
    forward_text_digest: str
    validation_binding_digest: str
    mode: str
    token_digest: str
    capture_fence_digest: str
    ingress_turn_id: str
    reservation_ref: str


@dataclass(frozen=True)
class LocalVoiceDurableReservationRevocation:
    schema: str
    durable: bool
    bindings: tuple[tuple[str, str, str, str], ...]
    revoked_count: int
    journal_generation: int


@dataclass(frozen=True)
class LocalVoiceDurableIngressClaim:
    schema: str
    durable: bool
    bridge_instance_id: str
    local_turn_id: str
    forward_text_digest: str
    entry_id: str
    ingress_turn_id: str
    phase: str
    disposition: str
    should_process: bool
    text_hash: str
    journal_generation: int
    reservation_ref: str = ""
    reservation_verified: bool = False
    _validation_lease_held: bool = field(
        default=False,
        compare=False,
        repr=False,
    )


DurableIngressClaim = Callable[
    [LocalVoiceIngressClaimRequest],
    LocalVoiceDurableIngressClaim,
]
DurableIssuanceReservation = Callable[
    [LocalVoiceIssuanceReservationRequest],
    LocalVoiceDurableIssuanceReservation,
]
DurableReservationRevocation = Callable[
    [tuple[LocalVoiceReservationRevocationRequest, ...]],
    LocalVoiceDurableReservationRevocation,
]


def normalize_local_voice_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def split_exact_leading_wake(value: Any) -> tuple[bool, str]:
    normalized = normalize_local_voice_text(value)
    match = _WAKE_PREFIX_PATTERN.fullmatch(normalized)
    if match is None:
        return False, normalized
    remainder = normalize_local_voice_text(match.group(1))
    # A wake-only turn is a real turn in voice-p0 and must not become empty.
    return True, remainder or WAKE_WORD


def _identifier(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized if _IDENTIFIER_PATTERN.fullmatch(normalized) else ""


def _text_digest(value: str) -> str:
    return sha256(normalize_local_voice_text(value).encode("utf-8")).hexdigest()


def _binding_digest(binding: dict[str, Any]) -> str:
    material = json.dumps(binding, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _capture_fence_digest(value: Any, *, required: bool) -> str:
    digest = str(value or "").strip()
    if not digest and not required:
        return ""
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_capture_fence_digest_invalid"
        )
    return digest


def _admission_proof_digest(
    domain: str,
    *,
    bridge_instance_id: str,
    turn_id: str,
    forward_text_digest: str,
    validation_binding_digest: str,
    mode: str,
    token_digest: str,
    capture_fence_digest: str = "",
    include_token_digest: bool = True,
) -> str:
    bridge_id = _identifier(bridge_instance_id)
    local_turn_id = _identifier(turn_id)
    if (
        not bridge_id
        or not local_turn_id
        or mode not in _ADMISSION_MODES
        or _SHA256_PATTERN.fullmatch(forward_text_digest) is None
        or _SHA256_PATTERN.fullmatch(validation_binding_digest) is None
        or _SHA256_PATTERN.fullmatch(token_digest) is None
    ):
        raise LocalVoiceAdmissionTransactionError(
            "local_voice_admission_proof_binding_invalid"
        )
    fields = {
        "bridgeInstanceId": bridge_id,
        "forwardTextDigest": forward_text_digest,
        "mode": mode,
        "turnId": local_turn_id,
        "validationBindingDigest": validation_binding_digest,
    }
    if include_token_digest:
        fields["tokenDigest"] = token_digest
    if capture_fence_digest:
        fields["captureFenceDigest"] = _capture_fence_digest(
            capture_fence_digest,
            required=True,
        )
    material = json.dumps(
        fields,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(f"{domain}\0{material}".encode("utf-8")).hexdigest()


def local_voice_ingress_turn_id(
    *,
    bridge_instance_id: str,
    turn_id: str,
    forward_text_digest: str,
    validation_binding_digest: str,
    mode: str,
    token_digest: str,
    capture_fence_digest: str = "",
) -> str:
    """Return a content-free stable identifier for one local turn binding."""

    digest = _admission_proof_digest(
        (
            "local_voice.admission.ingress-turn.v2"
            if capture_fence_digest
            else "local_voice.admission.ingress-turn.v1"
        ),
        bridge_instance_id=bridge_instance_id,
        turn_id=turn_id,
        forward_text_digest=forward_text_digest,
        validation_binding_digest=validation_binding_digest,
        mode=mode,
        token_digest=token_digest,
        capture_fence_digest=capture_fence_digest,
        include_token_digest=False,
    )
    return f"lva-{digest}"


def local_voice_reservation_ref(
    *,
    bridge_instance_id: str,
    turn_id: str,
    forward_text_digest: str,
    validation_binding_digest: str,
    mode: str,
    token_digest: str,
    capture_fence_digest: str = "",
) -> str:
    """Return the exact content-free proof used by the durable reservation."""

    return _admission_proof_digest(
        (
            "local_voice.admission.reservation.v2"
            if capture_fence_digest
            else "local_voice.admission.reservation.v1"
        ),
        bridge_instance_id=bridge_instance_id,
        turn_id=turn_id,
        forward_text_digest=forward_text_digest,
        validation_binding_digest=validation_binding_digest,
        mode=mode,
        token_digest=token_digest,
        capture_fence_digest=capture_fence_digest,
    )


def normalize_validation_binding(value: Any) -> dict[str, Any] | None:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        return None
    session_id = _identifier(
        value.get("sessionId") or value.get("validationSessionId")
    )
    step_id = _identifier(value.get("stepId") or value.get("validationStepId"))
    attempt_id = _identifier(
        value.get("attemptId") or value.get("validationAttemptId")
    )
    raw_attempt = value.get("attempt")
    if raw_attempt is None:
        raw_attempt = value.get("validationAttempt")
    if isinstance(raw_attempt, bool):
        return None
    try:
        attempt = int(raw_attempt)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (session_id and step_id and attempt_id and attempt > 0):
        return None
    return {
        "sessionId": session_id,
        "stepId": step_id,
        "attempt": attempt,
        "attemptId": attempt_id,
    }


def local_voice_requires_fresh_wake(value: Any) -> bool:
    text = normalize_local_voice_text(value)
    if not text:
        return False
    if detect_local_runtime_command(text) in {"restart", "shutdown"}:
        return True
    if detect_local_mic_command(text) in {"on", "off"}:
        return True
    if detect_minecraft_control_command(text) == "disconnect":
        return True
    return detect_minecraft_runtime_command(text) in {"start", "goal"}


@dataclass(frozen=True)
class _TokenRecord:
    token_digest: str
    bridge_instance_id: str
    turn_id: str
    forward_text_digest: str
    validation_binding_digest: str
    validation_bound: bool
    mode: str
    durably_reserved: bool
    capture_fence_digest: str
    issued_at: float
    expires_at: float


class LocalVoiceAdmissionManager:
    """Process-local, fail-closed admission capability for the Windows bridge."""

    def __init__(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
        token_ttl_sec: float = DEFAULT_TOKEN_TTL_SEC,
        followup_ttl_sec: float = DEFAULT_FOLLOWUP_TTL_SEC,
        replay_ttl_sec: float = DEFAULT_REPLAY_TTL_SEC,
    ) -> None:
        self._now = now
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self.token_ttl_sec = max(0.1, float(token_ttl_sec))
        self.followup_ttl_sec = max(0.1, float(followup_ttl_sec))
        self.replay_ttl_sec = max(
            self.token_ttl_sec,
            self.followup_ttl_sec,
            float(replay_ttl_sec),
        )
        self._lock = threading.RLock()
        self._bridge_instance_id = ""
        self._active_until = 0.0
        self._tokens: dict[str, _TokenRecord] = {}
        self._pending_turn_tokens: dict[tuple[str, str], str] = {}
        self._terminal_tokens: dict[str, tuple[str, float]] = {}
        self._consumed_turns: dict[tuple[str, str], float] = {}
        self._accepted_count = 0
        self._rejected_count = 0
        self._last_reason = "not_started"
        self._last_mode = "inactive"
        self._revocation_fenced = False

    def _cleanup(self, now_value: float) -> None:
        for digest, record in list(self._tokens.items()):
            if now_value < record.expires_at:
                continue
            self._tokens.pop(digest, None)
            turn_key = (record.bridge_instance_id, record.turn_id)
            if self._pending_turn_tokens.get(turn_key) == digest:
                self._pending_turn_tokens.pop(turn_key, None)
            self._terminal_tokens[digest] = (
                "admission_token_expired",
                now_value + self.replay_ttl_sec,
            )
        for digest, (_reason, expires_at) in list(self._terminal_tokens.items()):
            if now_value >= expires_at:
                self._terminal_tokens.pop(digest, None)
        for turn_key, expires_at in list(self._consumed_turns.items()):
            if now_value >= expires_at:
                self._consumed_turns.pop(turn_key, None)
        if len(self._terminal_tokens) > _MAX_TERMINAL_TOKENS:
            ordered = sorted(self._terminal_tokens.items(), key=lambda item: item[1][1])
            for digest, _entry in ordered[: len(ordered) - _MAX_TERMINAL_TOKENS]:
                self._terminal_tokens.pop(digest, None)

    def _invalidate_live_tokens(self, *, reason: str, now_value: float) -> None:
        for digest in tuple(self._tokens):
            self._terminal_tokens[digest] = (
                reason,
                now_value + self.replay_ttl_sec,
            )
        self._tokens.clear()
        self._pending_turn_tokens.clear()

    @staticmethod
    def _revocation_request(
        record: _TokenRecord,
    ) -> LocalVoiceReservationRevocationRequest:
        return LocalVoiceReservationRevocationRequest(
            bridge_instance_id=record.bridge_instance_id,
            turn_id=record.turn_id,
            forward_text_digest=record.forward_text_digest,
            validation_binding_digest=record.validation_binding_digest,
            mode=record.mode,
            token_digest=record.token_digest,
            capture_fence_digest=record.capture_fence_digest,
            ingress_turn_id=local_voice_ingress_turn_id(
                bridge_instance_id=record.bridge_instance_id,
                turn_id=record.turn_id,
                forward_text_digest=record.forward_text_digest,
                validation_binding_digest=record.validation_binding_digest,
                mode=record.mode,
                token_digest=record.token_digest,
                capture_fence_digest=record.capture_fence_digest,
            ),
            reservation_ref=local_voice_reservation_ref(
                bridge_instance_id=record.bridge_instance_id,
                turn_id=record.turn_id,
                forward_text_digest=record.forward_text_digest,
                validation_binding_digest=record.validation_binding_digest,
                mode=record.mode,
                token_digest=record.token_digest,
                capture_fence_digest=record.capture_fence_digest,
            ),
        )

    @staticmethod
    def _durable_revocation_receipt(
        value: Any,
        requests: tuple[LocalVoiceReservationRevocationRequest, ...],
    ) -> LocalVoiceDurableReservationRevocation:
        if (
            not isinstance(value, LocalVoiceDurableReservationRevocation)
            or value.schema
            != CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA
            or value.durable is not True
            or type(value.revoked_count) is not int
            or value.revoked_count != len(requests)
            or type(value.journal_generation) is not int
            or value.journal_generation <= 0
            or not isinstance(value.bindings, tuple)
            or any(
                not isinstance(binding, tuple)
                or len(binding) != 4
                or not _identifier(binding[0])
                or not _identifier(binding[1])
                or _SHA256_PATTERN.fullmatch(binding[2]) is None
                or _SHA256_PATTERN.fullmatch(binding[3]) is None
                for binding in value.bindings
            )
            or len({binding[0] for binding in value.bindings})
            != len(value.bindings)
        ):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_reservation_revocation_invalid"
            )
        expected = sorted(
            (
                request.ingress_turn_id,
                request.forward_text_digest,
                request.reservation_ref,
            )
            for request in requests
        )
        if sorted(binding[1:] for binding in value.bindings) != expected:
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_reservation_revocation_binding_mismatch"
            )
        return value

    def _set_revocation_fence(self) -> None:
        self._revocation_fenced = True
        self._active_until = 0.0
        self._last_mode = "inactive"
        self._last_reason = "local_voice_reservation_revocation_failed"

    def _require_revocation_clear(self) -> None:
        if self._revocation_fenced:
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_reservation_revocation_required"
            )

    def require_durable_revocation(self) -> dict[str, Any]:
        """Fence admission after an outer durable scope-revocation failure."""

        with self._lock:
            self._cleanup(float(self._now()))
            self._set_revocation_fence()
            return self.public_status()

    def _revoke_records(
        self,
        records: tuple[_TokenRecord, ...],
        durable_revocation: DurableReservationRevocation | None,
    ) -> LocalVoiceDurableReservationRevocation | None:
        requests = tuple(
            sorted(
                (
                    self._revocation_request(record)
                    for record in records
                    if record.durably_reserved
                ),
                key=lambda request: (
                    request.ingress_turn_id,
                    request.reservation_ref,
                ),
            )
        )
        if not requests:
            return None
        try:
            if durable_revocation is None:
                raise LocalVoiceAdmissionTransactionError(
                    "local_voice_reservation_revocation_failed"
                )
            return self._durable_revocation_receipt(
                durable_revocation(requests),
                requests,
            )
        except LocalVoiceAdmissionTransactionError:
            self._set_revocation_fence()
            raise
        except Exception:
            self._set_revocation_fence()
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_reservation_revocation_failed"
            ) from None

    def _validation_is_current(
        self,
        binding: dict[str, Any],
        callback: ValidationCurrent | None,
    ) -> bool:
        if callback is None:
            return not binding
        try:
            return callback(dict(binding)) is True
        except Exception:
            return False

    def _reject(self, reason: str) -> dict[str, Any]:
        self._rejected_count += 1
        fixed_reason = normalize_local_voice_text(reason).lower()
        fixed_reason = re.sub(r"[^a-z0-9_]+", "_", fixed_reason).strip("_")
        self._last_reason = fixed_reason[:80] or "local_voice_wake_required"
        return {
            "ok": False,
            "admitted": False,
            "error": "local_voice_wake_required",
            "reason": self._last_reason,
            "admission": self.public_status(),
        }

    def reject(self, reason: str) -> dict[str, Any]:
        """Record a policy rejection decided by a content-free outer boundary."""

        with self._lock:
            self._cleanup(float(self._now()))
            return self._reject(reason)

    def _rotate_bridge(
        self,
        bridge_instance_id: str,
        *,
        now_value: float,
        durable_revocation: DurableReservationRevocation | None = None,
    ) -> None:
        if bridge_instance_id == self._bridge_instance_id:
            return
        self._revoke_records(
            tuple(self._tokens.values()),
            durable_revocation,
        )
        self._invalidate_live_tokens(
            reason="bridge_instance_rotated",
            now_value=now_value,
        )
        self._bridge_instance_id = bridge_instance_id
        self._active_until = 0.0
        self._last_mode = "inactive"
        self._last_reason = "bridge_instance_rotated"

    def observe_bridge_instance(
        self,
        bridge_instance_id: Any,
        *,
        durable_revocation: DurableReservationRevocation | None = None,
    ) -> dict[str, Any]:
        normalized = _identifier(bridge_instance_id)
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            self._require_revocation_clear()
            if not normalized:
                return self._reject("bridge_instance_invalid")
            self._rotate_bridge(
                normalized,
                now_value=now_value,
                durable_revocation=durable_revocation,
            )
            return self.public_status()

    def reset(
        self,
        reason: str = "reset",
        *,
        durable_revocation: DurableReservationRevocation | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            fixed_reason = normalize_local_voice_text(reason).lower()
            fixed_reason = re.sub(r"[^a-z0-9_]+", "_", fixed_reason).strip("_")
            fixed_reason = fixed_reason[:80] or "reset"
            self._revoke_records(
                tuple(self._tokens.values()),
                durable_revocation,
            )
            self._invalidate_live_tokens(reason=fixed_reason, now_value=now_value)
            self._active_until = 0.0
            self._last_mode = "inactive"
            self._last_reason = fixed_reason
            self._revocation_fenced = False
            return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            active = bool(
                not self._revocation_fenced
                and self._bridge_instance_id
                and now_value < self._active_until
            )
            return {
                "schema": STATUS_SCHEMA,
                "active": active,
                "mode": self._last_mode if active else "inactive",
                "acceptedCount": self._accepted_count,
                "rejectedCount": self._rejected_count,
                "lastReason": self._last_reason,
                "revocationFenced": self._revocation_fenced,
                "contentFree": True,
            }

    def issue(
        self,
        bridge_instance_id: Any,
        turn_id: Any,
        text: Any,
        *,
        validation_binding: Any = None,
        validation_is_current: ValidationCurrent | None = None,
        durable_revocation: DurableReservationRevocation | None = None,
    ) -> dict[str, Any]:
        return self._issue(
            bridge_instance_id,
            turn_id,
            text,
            validation_binding=validation_binding,
            validation_is_current=validation_is_current,
            durable_reservation=None,
            durable_revocation=durable_revocation,
            capture_fence_digest="",
        ).admission

    def issue_with_durable_reservation(
        self,
        bridge_instance_id: Any,
        turn_id: Any,
        text: Any,
        *,
        durable_reservation: DurableIssuanceReservation,
        capture_fence_digest: Any,
        durable_revocation: DurableReservationRevocation | None = None,
        validation_binding: Any = None,
        validation_is_current: ValidationCurrent | None = None,
    ) -> LocalVoiceIssuanceTransaction:
        """Reserve an exact content-free capability before returning it."""

        return self._issue(
            bridge_instance_id,
            turn_id,
            text,
            validation_binding=validation_binding,
            validation_is_current=validation_is_current,
            durable_reservation=durable_reservation,
            durable_revocation=durable_revocation,
            capture_fence_digest=_capture_fence_digest(
                capture_fence_digest,
                required=True,
            ),
        )

    @staticmethod
    def _durable_issuance_reservation_receipt(
        value: Any,
        request: LocalVoiceIssuanceReservationRequest,
    ) -> LocalVoiceDurableIssuanceReservation:
        if (
            not isinstance(value, LocalVoiceDurableIssuanceReservation)
            or value.bridge_instance_id != request.bridge_instance_id
            or value.local_turn_id != request.turn_id
            or value.forward_text_digest != request.forward_text_digest
            or value.reservation_ref != request.reservation_ref
            or value.ingress_turn_id != request.ingress_turn_id
        ):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_issuance_reservation_binding_mismatch"
            )
        if (
            value.schema != CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA
            or value.durable is not True
            or not _identifier(value.entry_id)
            or value.phase != "reserved"
            or value.disposition != "reserved"
            or value.should_process is not False
            or value.text_hash != request.forward_text_digest
            or isinstance(value.journal_generation, bool)
        ):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_issuance_reservation_invalid"
            )
        try:
            generation = int(value.journal_generation)
        except (TypeError, ValueError, OverflowError):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_issuance_reservation_invalid"
            ) from None
        if generation <= 0:
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_issuance_reservation_invalid"
            )
        return value

    def _issue(
        self,
        bridge_instance_id: Any,
        turn_id: Any,
        text: Any,
        *,
        validation_binding: Any,
        validation_is_current: ValidationCurrent | None,
        durable_reservation: DurableIssuanceReservation | None,
        durable_revocation: DurableReservationRevocation | None,
        capture_fence_digest: str,
    ) -> LocalVoiceIssuanceTransaction:
        bridge_id = _identifier(bridge_instance_id)
        normalized_turn_id = _identifier(turn_id)
        original_text = normalize_local_voice_text(text)
        binding = normalize_validation_binding(validation_binding)
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            self._require_revocation_clear()
            if not bridge_id:
                return LocalVoiceIssuanceTransaction(
                    self._reject("bridge_instance_invalid"),
                    None,
                )
            if not normalized_turn_id:
                return LocalVoiceIssuanceTransaction(
                    self._reject("turn_id_invalid"),
                    None,
                )
            if not original_text or len(original_text) > _MAX_TEXT_CHARS:
                return LocalVoiceIssuanceTransaction(
                    self._reject("voice_text_invalid"),
                    None,
                )
            if binding is None:
                return LocalVoiceIssuanceTransaction(
                    self._reject("validation_binding_invalid"),
                    None,
                )

            bridge_rotated = bridge_id != self._bridge_instance_id

            def reject_after_bridge_observation(
                reason: str,
                *,
                clear_followup: bool = False,
            ) -> LocalVoiceIssuanceTransaction:
                if bridge_rotated:
                    self._rotate_bridge(
                        bridge_id,
                        now_value=now_value,
                        durable_revocation=durable_revocation,
                    )
                if clear_followup:
                    self._active_until = 0.0
                return LocalVoiceIssuanceTransaction(
                    self._reject(reason),
                    None,
                )

            turn_key = (bridge_id, normalized_turn_id)
            if turn_key in self._consumed_turns:
                return reject_after_bridge_observation(
                    "local_voice_turn_already_consumed"
                )

            fresh_wake, wake_forward_text = split_exact_leading_wake(original_text)
            if binding:
                if not self._validation_is_current(binding, validation_is_current):
                    return reject_after_bridge_observation(
                        "validation_attempt_stale"
                    )
                # A validation prompt is an exact, attempt-bound bypass.  It must
                # never inherit or create an ordinary hands-free follow-up lease.
                forward_text = (
                    wake_forward_text
                    if fresh_wake and local_voice_requires_fresh_wake(wake_forward_text)
                    else original_text
                )
                if local_voice_requires_fresh_wake(forward_text) and not fresh_wake:
                    return reject_after_bridge_observation(
                        "fresh_wake_required",
                        clear_followup=True,
                    )
                mode = "validation"
            else:
                if not self._validation_is_current({}, validation_is_current):
                    return reject_after_bridge_observation(
                        "validation_binding_required"
                    )
                if fresh_wake:
                    forward_text = wake_forward_text
                    mode = "wake_entry"
                elif (
                    not bridge_rotated
                    and now_value < self._active_until
                ):
                    forward_text = original_text
                    if local_voice_requires_fresh_wake(forward_text):
                        return reject_after_bridge_observation(
                            "fresh_wake_required"
                        )
                    mode = "followup"
                else:
                    return reject_after_bridge_observation(
                        "wake_word_required"
                    )

            forward_digest = _text_digest(forward_text)
            binding_digest = _binding_digest(binding)
            previous_digest = self._pending_turn_tokens.get(turn_key)
            previous = None
            if previous_digest:
                previous = self._tokens.get(previous_digest)
                if previous is not None and (
                    previous.forward_text_digest != forward_digest
                    or previous.validation_binding_digest != binding_digest
                    or previous.mode != mode
                ):
                    return reject_after_bridge_observation(
                        "local_voice_turn_binding_mismatch"
                    )
            if (
                not bridge_rotated
                and previous is None
                and len(self._tokens) >= _MAX_LIVE_TOKENS
            ):
                return reject_after_bridge_observation(
                    "admission_token_capacity_exhausted"
                )

            token = ""
            token_digest = ""
            for _attempt in range(8):
                candidate = str(self._token_factory() or "")
                if len(candidate) < 24 or len(candidate) > 512:
                    continue
                candidate_digest = sha256(candidate.encode("utf-8")).hexdigest()
                if (
                    candidate_digest not in self._tokens
                    and candidate_digest not in self._terminal_tokens
                ):
                    token = candidate
                    token_digest = candidate_digest
                    break
            if not token:
                return reject_after_bridge_observation(
                    "admission_token_generation_failed"
                )

            ingress_turn_id = local_voice_ingress_turn_id(
                bridge_instance_id=bridge_id,
                turn_id=normalized_turn_id,
                forward_text_digest=forward_digest,
                validation_binding_digest=binding_digest,
                mode=mode,
                token_digest=token_digest,
                capture_fence_digest=capture_fence_digest,
            )
            reservation_ref = local_voice_reservation_ref(
                bridge_instance_id=bridge_id,
                turn_id=normalized_turn_id,
                forward_text_digest=forward_digest,
                validation_binding_digest=binding_digest,
                mode=mode,
                token_digest=token_digest,
                capture_fence_digest=capture_fence_digest,
            )
            if bridge_rotated:
                # Revoke old-bridge reservations before creating a reservation
                # for the new bridge. A failed new reservation can then reduce
                # availability, but it cannot leave an old token recoverable.
                self._rotate_bridge(
                    bridge_id,
                    now_value=now_value,
                    durable_revocation=durable_revocation,
                )
            elif (
                previous is not None
                and previous.durably_reserved
                and durable_reservation is None
            ):
                # A durable same-turn reissue replaces the old row atomically.
                # A non-durable reissue must explicitly revoke it first.
                self._revoke_records((previous,), durable_revocation)

            reservation = None
            if durable_reservation is not None:
                reservation_request = LocalVoiceIssuanceReservationRequest(
                    bridge_instance_id=bridge_id,
                    turn_id=normalized_turn_id,
                    forward_text_digest=forward_digest,
                    validation_binding_digest=binding_digest,
                    mode=mode,
                    token_digest=token_digest,
                    capture_fence_digest=capture_fence_digest,
                    ingress_turn_id=ingress_turn_id,
                    reservation_ref=reservation_ref,
                    ttl_sec=self.token_ttl_sec,
                )
                reservation = self._durable_issuance_reservation_receipt(
                    durable_reservation(reservation_request),
                    reservation_request,
                )

            # Only an exact durable receipt may supersede the prior capability
            # or make the new process-local token visible.
            if binding:
                self._active_until = 0.0
            if previous_digest:
                self._tokens.pop(previous_digest, None)
                self._terminal_tokens[previous_digest] = (
                    "admission_token_superseded",
                    now_value + self.replay_ttl_sec,
                )

            self._tokens[token_digest] = _TokenRecord(
                token_digest=token_digest,
                bridge_instance_id=bridge_id,
                turn_id=normalized_turn_id,
                forward_text_digest=forward_digest,
                validation_binding_digest=binding_digest,
                validation_bound=bool(binding),
                mode=mode,
                durably_reserved=durable_reservation is not None,
                capture_fence_digest=capture_fence_digest,
                issued_at=now_value,
                expires_at=now_value + self.token_ttl_sec,
            )
            self._pending_turn_tokens[turn_key] = token_digest
            self._last_mode = mode
            self._last_reason = "admission_token_issued"
            result = {
                "ok": True,
                "admitted": True,
                "mode": mode,
                "forwardText": forward_text,
                "admissionToken": token,
                "expiresInSec": self.token_ttl_sec,
                "admission": self.public_status(),
            }
            return LocalVoiceIssuanceTransaction(result, reservation)

    def consume(
        self,
        token: Any,
        bridge_instance_id: Any,
        turn_id: Any,
        text: Any,
        *,
        admission_mode: Any = None,
        validation_binding: Any = None,
        validation_is_current: ValidationCurrent | None = None,
        durable_recovery_is_current: DurableRecoveryCurrent | None = None,
        durable_revocation: DurableReservationRevocation | None = None,
        _allow_durable_recovery: bool = False,
        _capture_fence_digest: str = "",
        _before_commit: (
            Callable[[_TokenRecord, str, bool], bool | None] | None
        ) = None,
    ) -> dict[str, Any]:
        presented_token = str(token or "")
        bridge_id = _identifier(bridge_instance_id)
        normalized_turn_id = _identifier(turn_id)
        forward_text = normalize_local_voice_text(text)
        binding = normalize_validation_binding(validation_binding)
        presented_mode = str(admission_mode or "").strip()
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            self._require_revocation_clear()
            if not presented_token:
                return self._reject("admission_token_missing")
            if len(presented_token) > 512:
                return self._reject("admission_token_invalid")
            token_digest = sha256(presented_token.encode("utf-8")).hexdigest()
            terminal = self._terminal_tokens.get(token_digest)
            if terminal is not None:
                return self._reject(terminal[0])
            record = self._tokens.get(token_digest)
            recovered_unknown = record is None
            if recovered_unknown and not _allow_durable_recovery:
                return self._reject("admission_token_unknown")
            if recovered_unknown:
                if len(presented_token) < 24:
                    return self._reject("admission_token_invalid")
                if (
                    not bridge_id
                    or not normalized_turn_id
                    or not forward_text
                    or len(forward_text) > _MAX_TEXT_CHARS
                ):
                    return self._reject("admission_recovery_binding_invalid")
                if presented_mode not in _ADMISSION_MODES:
                    return self._reject("admission_mode_invalid")
                if binding is None or (
                    (presented_mode == "validation") != bool(binding)
                ):
                    return self._reject("admission_validation_mismatch")
                if not self._validation_is_current(
                    binding or {},
                    validation_is_current,
                ):
                    return self._reject(
                        "validation_attempt_stale"
                        if binding
                        else "validation_binding_required"
                    )
                if durable_recovery_is_current is None:
                    return self._reject("admission_token_unknown")
                try:
                    recovery_is_current = (
                        durable_recovery_is_current() is True
                    )
                except Exception:
                    recovery_is_current = False
                if not recovery_is_current:
                    return {
                        "ok": False,
                        "admitted": False,
                        "error": "local_voice_wake_required",
                        "reason": "admission_recovery_context_stale",
                        "admission": self.public_status(),
                    }
                forward_digest = _text_digest(forward_text)
                binding_digest = _binding_digest(binding or {})
                record = _TokenRecord(
                    token_digest=token_digest,
                    bridge_instance_id=bridge_id,
                    turn_id=normalized_turn_id,
                    forward_text_digest=forward_digest,
                    validation_binding_digest=binding_digest,
                    validation_bound=bool(binding),
                    mode=presented_mode,
                    durably_reserved=True,
                    capture_fence_digest=_capture_fence_digest,
                    issued_at=now_value,
                    expires_at=now_value + self.token_ttl_sec,
                )

            assert record is not None

            def invalidate(reason: str) -> dict[str, Any]:
                if not recovered_unknown:
                    self._revoke_records((record,), durable_revocation)
                self._tokens.pop(token_digest, None)
                turn_key = (record.bridge_instance_id, record.turn_id)
                if self._pending_turn_tokens.get(turn_key) == token_digest:
                    self._pending_turn_tokens.pop(turn_key, None)
                self._terminal_tokens[token_digest] = (
                    reason,
                    now_value + self.replay_ttl_sec,
                )
                return self._reject(reason)

            if (
                not recovered_unknown
                and record.durably_reserved
                and _capture_fence_digest
                and not secrets.compare_digest(
                    record.capture_fence_digest,
                    _capture_fence_digest,
                )
            ):
                return invalidate("voice_capture_consent_not_current")

            if not bridge_id or bridge_id != record.bridge_instance_id:
                return invalidate("admission_bridge_mismatch")
            if not normalized_turn_id or normalized_turn_id != record.turn_id:
                return invalidate("admission_turn_mismatch")
            if not forward_text or _text_digest(forward_text) != record.forward_text_digest:
                return invalidate("admission_text_mismatch")
            if binding is None or _binding_digest(binding or {}) != record.validation_binding_digest:
                return invalidate("admission_validation_mismatch")
            if bool(binding) != record.validation_bound:
                return invalidate("admission_validation_mismatch")
            if admission_mode is not None and (
                presented_mode not in _ADMISSION_MODES
                or presented_mode != record.mode
            ):
                return invalidate("admission_mode_mismatch")
            if not self._validation_is_current(binding or {}, validation_is_current):
                return invalidate(
                    "validation_attempt_stale"
                    if record.validation_bound
                    else "validation_binding_required"
                )
            if not recovered_unknown and record.mode == "followup" and not (
                self._bridge_instance_id == bridge_id
                and now_value < self._active_until
            ):
                return invalidate("followup_session_expired")
            if len(self._consumed_turns) >= _MAX_REPLAY_TURNS:
                return invalidate("admission_replay_capacity_exhausted")

            if recovered_unknown and bridge_id != self._bridge_instance_id:
                self._rotate_bridge(
                    bridge_id,
                    now_value=now_value,
                    durable_revocation=durable_revocation,
                )

            # A durable ingress claim is the irreversible side of local voice
            # admission.  Run it while this token is still live and while the
            # manager lock excludes a concurrent consume.  If it raises, none
            # of the token, replay-ledger, or follow-up-lease mutations below
            # are committed, so a retry can safely attempt the same claim.
            should_admit = True
            if _before_commit is not None:
                should_admit = (
                    _before_commit(
                        record,
                        forward_text,
                        recovered_unknown,
                    )
                    is not False
                )
            elif recovered_unknown:
                return self._reject("admission_token_unknown")
            elif record.durably_reserved:
                raise LocalVoiceAdmissionTransactionError(
                    "local_voice_durable_claim_required"
                )

            self._tokens.pop(token_digest, None)
            turn_key = (record.bridge_instance_id, record.turn_id)
            if self._pending_turn_tokens.get(turn_key) == token_digest:
                self._pending_turn_tokens.pop(turn_key, None)
            self._terminal_tokens[token_digest] = (
                "admission_token_reused",
                now_value + self.replay_ttl_sec,
            )
            self._consumed_turns[turn_key] = now_value + self.replay_ttl_sec
            if not should_admit:
                # The stable ingress key already exists. Terminalize this
                # capability and remember the replay, but do not turn a
                # recovered duplicate into fresh conversational consent.
                self._last_reason = "admission_ingress_duplicate"
                return {
                    "ok": False,
                    "admitted": False,
                    "suppressed": True,
                    "reason": "admission_ingress_duplicate",
                    "mode": record.mode,
                    "forwardText": forward_text,
                    "admission": self.public_status(),
                }
            self._bridge_instance_id = bridge_id
            if record.mode in {"wake_entry", "followup"}:
                self._active_until = now_value + self.followup_ttl_sec
            else:
                self._active_until = 0.0
            self._accepted_count += 1
            self._last_mode = record.mode
            self._last_reason = "admission_consumed"
            return {
                "ok": True,
                "admitted": True,
                "mode": record.mode,
                "forwardText": forward_text,
                "admission": self.public_status(),
            }

    @staticmethod
    def _durable_ingress_claim_receipt(
        value: Any,
        request: LocalVoiceIngressClaimRequest,
    ) -> LocalVoiceDurableIngressClaim:
        if (
            not isinstance(value, LocalVoiceDurableIngressClaim)
            or value.bridge_instance_id != request.bridge_instance_id
            or value.local_turn_id != request.turn_id
            or value.forward_text_digest
            != request.forward_text_digest
            or (
                value.reservation_ref
                and value.reservation_ref != request.reservation_ref
            )
        ):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_ingress_claim_binding_mismatch"
            )
        expected_disposition = {
            "accepted": "pending",
            "response_ready": "pending",
            "delivery_inflight": "delivery_inflight",
            "delivery_succeeded": "delivered_pending_commit",
            "delivery_ambiguous": "delivery_ambiguous",
            "terminal_committing": "terminal_pending",
            "completed": "completed",
        }.get(value.phase)
        if (
            value.schema
            != CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA
            or value.durable is not True
            or not _identifier(value.entry_id)
            or not _identifier(value.ingress_turn_id)
            or _SHA256_PATTERN.fullmatch(value.text_hash) is None
            or value.text_hash != request.forward_text_digest
            or type(value.should_process) is not bool
            or type(value.reservation_verified) is not bool
            or type(value._validation_lease_held) is not bool
            or (
                value.reservation_verified
                and (
                    value.reservation_ref != request.reservation_ref
                    or value.ingress_turn_id != request.ingress_turn_id
                )
            )
            or expected_disposition is None
            or (
                value.should_process is True
                and (
                    value.phase != "accepted"
                    or value.disposition != "claimed"
                )
            )
            or (
                value.should_process is False
                and value.disposition != expected_disposition
            )
            or isinstance(value.journal_generation, bool)
        ):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_ingress_claim_invalid"
            )
        try:
            generation = int(value.journal_generation)
        except (TypeError, ValueError, OverflowError):
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_ingress_claim_invalid"
            ) from None
        if generation <= 0:
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_ingress_claim_invalid"
            )
        return value

    def consume_with_durable_claim(
        self,
        token: Any,
        bridge_instance_id: Any,
        turn_id: Any,
        text: Any,
        *,
        durable_claim: DurableIngressClaim,
        capture_fence_digest: Any,
        durable_revocation: DurableReservationRevocation | None = None,
        admission_mode: Any = None,
        validation_binding: Any = None,
        validation_is_current: ValidationCurrent | None = None,
        durable_recovery_is_current: DurableRecoveryCurrent | None = None,
    ) -> LocalVoiceAdmissionTransaction:
        """Bind one valid capability to durable ingress before consuming it.

        The callback executes after every admission check but before any
        one-shot token or follow-up-session mutation.  Therefore a callback
        failure leaves the capability retryable, while every returned
        admitted result has an exact durable claim receipt.
        """

        capture_digest = _capture_fence_digest(
            capture_fence_digest,
            required=True,
        )
        captured_claim: list[LocalVoiceDurableIngressClaim] = []

        def claim_before_commit(
            record: _TokenRecord,
            forward_text: str,
            recovered_unknown: bool,
        ) -> bool:
            ingress_turn_id = local_voice_ingress_turn_id(
                bridge_instance_id=record.bridge_instance_id,
                turn_id=record.turn_id,
                forward_text_digest=record.forward_text_digest,
                validation_binding_digest=(
                    record.validation_binding_digest
                ),
                mode=record.mode,
                token_digest=record.token_digest,
                capture_fence_digest=capture_digest,
            )
            reservation_ref = local_voice_reservation_ref(
                bridge_instance_id=record.bridge_instance_id,
                turn_id=record.turn_id,
                forward_text_digest=record.forward_text_digest,
                validation_binding_digest=(
                    record.validation_binding_digest
                ),
                mode=record.mode,
                token_digest=record.token_digest,
                capture_fence_digest=capture_digest,
            )
            request = LocalVoiceIngressClaimRequest(
                bridge_instance_id=record.bridge_instance_id,
                turn_id=record.turn_id,
                forward_text=forward_text,
                forward_text_digest=record.forward_text_digest,
                validation_binding_digest=(
                    record.validation_binding_digest
                ),
                mode=record.mode,
                token_digest=record.token_digest,
                capture_fence_digest=capture_digest,
                ingress_turn_id=ingress_turn_id,
                reservation_ref=reservation_ref,
            )
            validated_claim = self._durable_ingress_claim_receipt(
                durable_claim(request),
                request,
            )
            if record.durably_reserved:
                if validated_claim.reservation_verified is not True:
                    raise LocalVoiceAdmissionTransactionError(
                        "local_voice_ingress_reservation_unverified"
                    )
                if (
                    validated_claim.reservation_ref
                    != request.reservation_ref
                    or validated_claim.ingress_turn_id
                    != request.ingress_turn_id
                ):
                    raise LocalVoiceAdmissionTransactionError(
                        "local_voice_ingress_claim_binding_mismatch"
                    )
            if (
                record.validation_bound
                and validated_claim._validation_lease_held is not True
            ):
                raise LocalVoiceAdmissionTransactionError(
                    "local_voice_validation_attempt_lease_required"
                )
            captured_claim.append(validated_claim)
            return validated_claim.should_process

        admission = self.consume(
            token,
            bridge_instance_id,
            turn_id,
            text,
            admission_mode=admission_mode,
            validation_binding=validation_binding,
            validation_is_current=validation_is_current,
            durable_recovery_is_current=(
                durable_recovery_is_current
            ),
            durable_revocation=durable_revocation,
            _allow_durable_recovery=True,
            _capture_fence_digest=capture_digest,
            _before_commit=claim_before_commit,
        )
        if admission.get("suppressed") is True:
            if (
                len(captured_claim) != 1
                or captured_claim[0].should_process is not False
            ):
                raise LocalVoiceAdmissionTransactionError(
                    "local_voice_ingress_claim_invalid"
                )
            return LocalVoiceAdmissionTransaction(
                admission=dict(admission),
                ingress_claim=captured_claim[0],
            )
        if admission.get("admitted") is not True:
            return LocalVoiceAdmissionTransaction(
                admission=dict(admission),
                ingress_claim=None,
            )
        if len(captured_claim) != 1:
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_ingress_claim_invalid"
            )
        return LocalVoiceAdmissionTransaction(
            admission=dict(admission),
            ingress_claim=captured_claim[0],
        )


__all__ = [
    "DEFAULT_FOLLOWUP_TTL_SEC",
    "DEFAULT_REPLAY_TTL_SEC",
    "DEFAULT_TOKEN_TTL_SEC",
    "LocalVoiceAdmissionTransaction",
    "LocalVoiceAdmissionTransactionError",
    "LocalVoiceDurableIssuanceReservation",
    "LocalVoiceDurableIngressClaim",
    "LocalVoiceDurableReservationRevocation",
    "LocalVoiceIssuanceReservationRequest",
    "LocalVoiceIssuanceTransaction",
    "LocalVoiceIngressClaimRequest",
    "LocalVoiceReservationRevocationRequest",
    "LocalVoiceAdmissionManager",
    "STATUS_SCHEMA",
    "WAKE_WORD",
    "local_voice_requires_fresh_wake",
    "local_voice_ingress_turn_id",
    "local_voice_reservation_ref",
    "normalize_local_voice_text",
    "normalize_validation_binding",
    "split_exact_leading_wake",
]
