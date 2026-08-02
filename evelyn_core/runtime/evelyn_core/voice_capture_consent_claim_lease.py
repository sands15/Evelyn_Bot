from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from .minecraft_owner_lock import (
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)
from .paths import get_runtime_artifacts_root


class VoiceCaptureConsentClaimLeaseBusy(RuntimeError):
    code = "voice_capture_consent_claim_inflight"

    def __init__(self) -> None:
        super().__init__(self.code)


class VoiceCaptureConsentClaimLeaseUnavailable(RuntimeError):
    code = "voice_capture_consent_claim_lease_unavailable"

    def __init__(self) -> None:
        super().__init__(self.code)


class VoiceCaptureConsentClaimLeaseTimeout(RuntimeError):
    code = "voice_capture_consent_claim_lease_timeout"

    def __init__(self) -> None:
        super().__init__(self.code)


DEFAULT_BLOCKING_TIMEOUT_SEC = 2.0
_BLOCKING_RETRY_INTERVAL_SEC = 0.05
_MUTEXES_GUARD = threading.Lock()
_MUTEXES: dict[str, threading.Lock] = {}


def _process_mutex(path: Path) -> threading.Lock:
    key = str(path.resolve(strict=False)).casefold()
    with _MUTEXES_GUARD:
        return _MUTEXES.setdefault(key, threading.Lock())


def _acquire_owner_lock(
    owner_lock: MinecraftOwnerLock,
    *,
    deadline: float | None,
) -> None:
    if deadline is None:
        owner_lock.acquire()
        return
    while True:
        try:
            owner_lock.acquire()
            return
        except MinecraftOwnerLockBusy:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceCaptureConsentClaimLeaseTimeout() from None
            time.sleep(min(_BLOCKING_RETRY_INTERVAL_SEC, remaining))


@dataclass
class VoiceCaptureConsentClaimLease:
    path: Path
    _owner_lock: MinecraftOwnerLock
    _process_lock: threading.Lock
    _released: bool = False

    @property
    def acquired(self) -> bool:
        return not self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._owner_lock.release()
        finally:
            self._process_lock.release()

    def __enter__(self) -> VoiceCaptureConsentClaimLease:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.release()


def acquire_voice_capture_consent_claim_lease(
    *,
    root: Path | None = None,
    blocking: bool = False,
    timeout_sec: float = DEFAULT_BLOCKING_TIMEOUT_SEC,
) -> VoiceCaptureConsentClaimLease:
    path = (
        Path(root or get_runtime_artifacts_root())
        / "voice_capture_consent"
        / "claim_lease.lock"
    )
    process_lock = _process_mutex(path)
    deadline = None
    if blocking:
        timeout = max(0.0, float(timeout_sec))
        deadline = time.monotonic() + timeout
        if not process_lock.acquire(timeout=timeout):
            raise VoiceCaptureConsentClaimLeaseTimeout()
    elif not process_lock.acquire(blocking=False):
        raise VoiceCaptureConsentClaimLeaseBusy()

    owner_lock: MinecraftOwnerLock | None = None
    try:
        owner_lock = MinecraftOwnerLock(path)
        _acquire_owner_lock(owner_lock, deadline=deadline)
    except MinecraftOwnerLockBusy:
        process_lock.release()
        raise VoiceCaptureConsentClaimLeaseBusy() from None
    except VoiceCaptureConsentClaimLeaseTimeout:
        process_lock.release()
        raise
    except (MinecraftOwnerLockUnavailable, OSError):
        process_lock.release()
        raise VoiceCaptureConsentClaimLeaseUnavailable() from None
    except BaseException:
        if owner_lock is not None:
            owner_lock.release()
        process_lock.release()
        raise
    assert owner_lock is not None
    return VoiceCaptureConsentClaimLease(
        path=path,
        _owner_lock=owner_lock,
        _process_lock=process_lock,
    )


__all__ = [
    "DEFAULT_BLOCKING_TIMEOUT_SEC",
    "VoiceCaptureConsentClaimLease",
    "VoiceCaptureConsentClaimLeaseBusy",
    "VoiceCaptureConsentClaimLeaseTimeout",
    "VoiceCaptureConsentClaimLeaseUnavailable",
    "acquire_voice_capture_consent_claim_lease",
]
