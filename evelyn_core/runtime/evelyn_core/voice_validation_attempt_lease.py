from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from .minecraft_owner_lock import (
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)
from .paths import get_runtime_artifacts_root


class VoiceValidationAttemptLeaseBusy(RuntimeError):
    pass


class VoiceValidationAttemptLeaseUnavailable(RuntimeError):
    pass


def normalize_attempt_binding(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    session_id = str(
        value.get("sessionId")
        or value.get("validationSessionId")
        or value.get("validation_session_id")
        or ""
    ).strip()
    step_id = str(
        value.get("stepId")
        or value.get("validationStepId")
        or value.get("validation_step_id")
        or ""
    ).strip()
    attempt_id = str(
        value.get("attemptId")
        or value.get("validationAttemptId")
        or value.get("validation_attempt_id")
        or ""
    ).strip()
    raw_attempt = value.get("attempt")
    if raw_attempt is None:
        raw_attempt = value.get("validationAttempt")
    if raw_attempt is None:
        raw_attempt = value.get("validation_attempt")
    if isinstance(raw_attempt, bool):
        return None
    try:
        attempt = int(raw_attempt)
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        not session_id
        or not step_id
        or not attempt_id
        or attempt < 1
        or any(len(item) > 128 for item in (session_id, step_id, attempt_id))
    ):
        return None
    return {
        "sessionId": session_id,
        "stepId": step_id,
        "attempt": attempt,
        "attemptId": attempt_id,
    }


def attempt_binding_digest(value: Any) -> str:
    binding = normalize_attempt_binding(value)
    if binding is None:
        raise VoiceValidationAttemptLeaseUnavailable(
            "voice_validation_attempt_binding_invalid"
        )
    material = json.dumps(
        binding,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(material.encode("utf-8")).hexdigest()


_MUTEXES_GUARD = threading.Lock()
_MUTEXES: dict[str, threading.Lock] = {}


def _process_mutex(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False)).casefold()
    with _MUTEXES_GUARD:
        return _MUTEXES.setdefault(key, threading.Lock())


@dataclass
class VoiceValidationAttemptLease:
    digest: str
    path: Path
    _owner_lock: MinecraftOwnerLock
    _process_lock: threading.Lock
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._owner_lock.release()
        finally:
            self._process_lock.release()


class VoiceValidationAttemptLeaseSet:
    def __init__(self, leases: Iterable[VoiceValidationAttemptLease] = ()) -> None:
        self._leases = list(leases)
        self._released = False

    @property
    def acquired(self) -> bool:
        return bool(self._leases) and not self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        for lease in reversed(self._leases):
            lease.release()


def acquire_attempt_leases(
    bindings: Iterable[dict[str, Any]],
    *,
    root: Path | None = None,
) -> VoiceValidationAttemptLeaseSet:
    artifacts_root = Path(root or get_runtime_artifacts_root())
    normalized: dict[str, dict[str, Any]] = {}
    for value in bindings:
        binding = normalize_attempt_binding(value)
        if binding is None:
            raise VoiceValidationAttemptLeaseUnavailable(
                "voice_validation_attempt_binding_invalid"
            )
        normalized[attempt_binding_digest(binding)] = binding

    acquired: list[VoiceValidationAttemptLease] = []
    try:
        for digest in sorted(normalized):
            path = (
                artifacts_root
                / "voice_validation"
                / "attempt_locks"
                / digest
                / "owner_claim.lock"
            )
            process_lock = _process_mutex(path)
            if not process_lock.acquire(blocking=False):
                raise VoiceValidationAttemptLeaseBusy(
                    "voice_validation_attempt_inflight"
                )
            owner_lock = MinecraftOwnerLock(path)
            try:
                owner_lock.acquire()
            except MinecraftOwnerLockBusy:
                process_lock.release()
                raise VoiceValidationAttemptLeaseBusy(
                    "voice_validation_attempt_inflight"
                ) from None
            except (MinecraftOwnerLockUnavailable, OSError):
                process_lock.release()
                raise VoiceValidationAttemptLeaseUnavailable(
                    "voice_validation_attempt_lease_unavailable"
                ) from None
            acquired.append(
                VoiceValidationAttemptLease(
                    digest=digest,
                    path=path,
                    _owner_lock=owner_lock,
                    _process_lock=process_lock,
                )
            )
    except Exception:
        VoiceValidationAttemptLeaseSet(acquired).release()
        raise
    return VoiceValidationAttemptLeaseSet(acquired)


def acquire_attempt_lease(
    binding: dict[str, Any],
    *,
    root: Path | None = None,
) -> VoiceValidationAttemptLeaseSet:
    return acquire_attempt_leases((binding,), root=root)


__all__ = [
    "VoiceValidationAttemptLeaseBusy",
    "VoiceValidationAttemptLeaseSet",
    "VoiceValidationAttemptLeaseUnavailable",
    "acquire_attempt_lease",
    "acquire_attempt_leases",
    "attempt_binding_digest",
    "normalize_attempt_binding",
]
