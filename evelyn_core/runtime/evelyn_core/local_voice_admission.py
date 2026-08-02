from __future__ import annotations

from dataclasses import dataclass
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

ValidationCurrent = Callable[[dict[str, Any]], bool]


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
class LocalVoiceIngressClaimRequest:
    bridge_instance_id: str
    turn_id: str
    forward_text: str
    forward_text_digest: str
    validation_binding_digest: str
    mode: str


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


DurableIngressClaim = Callable[
    [LocalVoiceIngressClaimRequest],
    LocalVoiceDurableIngressClaim,
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

    def _rotate_bridge(self, bridge_instance_id: str, *, now_value: float) -> None:
        if bridge_instance_id == self._bridge_instance_id:
            return
        self._invalidate_live_tokens(
            reason="bridge_instance_rotated",
            now_value=now_value,
        )
        self._bridge_instance_id = bridge_instance_id
        self._active_until = 0.0
        self._last_mode = "inactive"
        self._last_reason = "bridge_instance_rotated"

    def observe_bridge_instance(self, bridge_instance_id: Any) -> dict[str, Any]:
        normalized = _identifier(bridge_instance_id)
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            if not normalized:
                return self._reject("bridge_instance_invalid")
            self._rotate_bridge(normalized, now_value=now_value)
            return self.public_status()

    def reset(self, reason: str = "reset") -> dict[str, Any]:
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            fixed_reason = normalize_local_voice_text(reason).lower()
            fixed_reason = re.sub(r"[^a-z0-9_]+", "_", fixed_reason).strip("_")
            fixed_reason = fixed_reason[:80] or "reset"
            self._invalidate_live_tokens(reason=fixed_reason, now_value=now_value)
            self._active_until = 0.0
            self._last_mode = "inactive"
            self._last_reason = fixed_reason
            return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            active = bool(
                self._bridge_instance_id and now_value < self._active_until
            )
            return {
                "schema": STATUS_SCHEMA,
                "active": active,
                "mode": self._last_mode if active else "inactive",
                "acceptedCount": self._accepted_count,
                "rejectedCount": self._rejected_count,
                "lastReason": self._last_reason,
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
    ) -> dict[str, Any]:
        bridge_id = _identifier(bridge_instance_id)
        normalized_turn_id = _identifier(turn_id)
        original_text = normalize_local_voice_text(text)
        binding = normalize_validation_binding(validation_binding)
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            if not bridge_id:
                return self._reject("bridge_instance_invalid")
            if not normalized_turn_id:
                return self._reject("turn_id_invalid")
            if not original_text or len(original_text) > _MAX_TEXT_CHARS:
                return self._reject("voice_text_invalid")
            if binding is None:
                return self._reject("validation_binding_invalid")

            self._rotate_bridge(bridge_id, now_value=now_value)
            turn_key = (bridge_id, normalized_turn_id)
            if turn_key in self._consumed_turns:
                return self._reject("local_voice_turn_already_consumed")

            fresh_wake, wake_forward_text = split_exact_leading_wake(original_text)
            if binding:
                if not self._validation_is_current(binding, validation_is_current):
                    return self._reject("validation_attempt_stale")
                # A validation prompt is an exact, attempt-bound bypass.  It must
                # never inherit or create an ordinary hands-free follow-up lease.
                self._active_until = 0.0
                forward_text = (
                    wake_forward_text
                    if fresh_wake and local_voice_requires_fresh_wake(wake_forward_text)
                    else original_text
                )
                if local_voice_requires_fresh_wake(forward_text) and not fresh_wake:
                    return self._reject("fresh_wake_required")
                mode = "validation"
            else:
                if not self._validation_is_current({}, validation_is_current):
                    return self._reject("validation_binding_required")
                if fresh_wake:
                    forward_text = wake_forward_text
                    mode = "wake_entry"
                elif (
                    self._bridge_instance_id == bridge_id
                    and now_value < self._active_until
                ):
                    forward_text = original_text
                    if local_voice_requires_fresh_wake(forward_text):
                        return self._reject("fresh_wake_required")
                    mode = "followup"
                else:
                    return self._reject("wake_word_required")

            forward_digest = _text_digest(forward_text)
            binding_digest = _binding_digest(binding)
            previous_digest = self._pending_turn_tokens.get(turn_key)
            if previous_digest:
                previous = self._tokens.get(previous_digest)
                if previous is not None and (
                    previous.forward_text_digest != forward_digest
                    or previous.validation_binding_digest != binding_digest
                    or previous.mode != mode
                ):
                    return self._reject("local_voice_turn_binding_mismatch")
                self._tokens.pop(previous_digest, None)
                self._terminal_tokens[previous_digest] = (
                    "admission_token_superseded",
                    now_value + self.replay_ttl_sec,
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
                return self._reject("admission_token_generation_failed")

            if len(self._tokens) >= _MAX_LIVE_TOKENS:
                oldest_digest, oldest = min(
                    self._tokens.items(),
                    key=lambda item: item[1].issued_at,
                )
                self._tokens.pop(oldest_digest, None)
                oldest_key = (oldest.bridge_instance_id, oldest.turn_id)
                if self._pending_turn_tokens.get(oldest_key) == oldest_digest:
                    self._pending_turn_tokens.pop(oldest_key, None)
                self._terminal_tokens[oldest_digest] = (
                    "admission_token_evicted",
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
                issued_at=now_value,
                expires_at=now_value + self.token_ttl_sec,
            )
            self._pending_turn_tokens[turn_key] = token_digest
            self._last_mode = mode
            self._last_reason = "admission_token_issued"
            return {
                "ok": True,
                "admitted": True,
                "mode": mode,
                "forwardText": forward_text,
                "admissionToken": token,
                "expiresInSec": self.token_ttl_sec,
                "admission": self.public_status(),
            }

    def consume(
        self,
        token: Any,
        bridge_instance_id: Any,
        turn_id: Any,
        text: Any,
        *,
        validation_binding: Any = None,
        validation_is_current: ValidationCurrent | None = None,
        _before_commit: (
            Callable[[_TokenRecord, str], bool | None] | None
        ) = None,
    ) -> dict[str, Any]:
        presented_token = str(token or "")
        bridge_id = _identifier(bridge_instance_id)
        normalized_turn_id = _identifier(turn_id)
        forward_text = normalize_local_voice_text(text)
        binding = normalize_validation_binding(validation_binding)
        with self._lock:
            now_value = float(self._now())
            self._cleanup(now_value)
            if not presented_token:
                return self._reject("admission_token_missing")
            if len(presented_token) > 512:
                return self._reject("admission_token_invalid")
            token_digest = sha256(presented_token.encode("utf-8")).hexdigest()
            terminal = self._terminal_tokens.get(token_digest)
            if terminal is not None:
                return self._reject(terminal[0])
            record = self._tokens.get(token_digest)
            if record is None:
                return self._reject("admission_token_unknown")

            def invalidate(reason: str) -> dict[str, Any]:
                self._tokens.pop(token_digest, None)
                turn_key = (record.bridge_instance_id, record.turn_id)
                if self._pending_turn_tokens.get(turn_key) == token_digest:
                    self._pending_turn_tokens.pop(turn_key, None)
                self._terminal_tokens[token_digest] = (
                    reason,
                    now_value + self.replay_ttl_sec,
                )
                return self._reject(reason)

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
            if not self._validation_is_current(binding or {}, validation_is_current):
                return invalidate(
                    "validation_attempt_stale"
                    if record.validation_bound
                    else "validation_binding_required"
                )
            if record.mode == "followup" and not (
                self._bridge_instance_id == bridge_id
                and now_value < self._active_until
            ):
                return invalidate("followup_session_expired")
            if len(self._consumed_turns) >= _MAX_REPLAY_TURNS:
                return invalidate("admission_replay_capacity_exhausted")

            # A durable ingress claim is the irreversible side of local voice
            # admission.  Run it while this token is still live and while the
            # manager lock excludes a concurrent consume.  If it raises, none
            # of the token, replay-ledger, or follow-up-lease mutations below
            # are committed, so a retry can safely attempt the same claim.
            should_admit = True
            if _before_commit is not None:
                should_admit = (
                    _before_commit(record, forward_text) is not False
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
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                value.text_hash,
            )
            or value.text_hash != request.forward_text_digest
            or type(value.should_process) is not bool
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
        validation_binding: Any = None,
        validation_is_current: ValidationCurrent | None = None,
    ) -> LocalVoiceAdmissionTransaction:
        """Bind one valid capability to durable ingress before consuming it.

        The callback executes after every admission check but before any
        one-shot token or follow-up-session mutation.  Therefore a callback
        failure leaves the capability retryable, while every returned
        admitted result has an exact durable claim receipt.
        """

        captured_claim: list[LocalVoiceDurableIngressClaim] = []

        def claim_before_commit(
            record: _TokenRecord,
            forward_text: str,
        ) -> bool:
            request = LocalVoiceIngressClaimRequest(
                bridge_instance_id=record.bridge_instance_id,
                turn_id=record.turn_id,
                forward_text=forward_text,
                forward_text_digest=record.forward_text_digest,
                validation_binding_digest=(
                    record.validation_binding_digest
                ),
                mode=record.mode,
            )
            validated_claim = self._durable_ingress_claim_receipt(
                durable_claim(request),
                request,
            )
            captured_claim.append(validated_claim)
            return validated_claim.should_process

        admission = self.consume(
            token,
            bridge_instance_id,
            turn_id,
            text,
            validation_binding=validation_binding,
            validation_is_current=validation_is_current,
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
    "LocalVoiceDurableIngressClaim",
    "LocalVoiceIngressClaimRequest",
    "LocalVoiceAdmissionManager",
    "STATUS_SCHEMA",
    "WAKE_WORD",
    "local_voice_requires_fresh_wake",
    "normalize_local_voice_text",
    "normalize_validation_binding",
    "split_exact_leading_wake",
]
