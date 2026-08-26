from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Any


OWNED_LAB_CAMPAIGN_LOCK = (
    Path(tempfile.gettempdir()) / "evelyn-main-latency-campaign.lock"
)


def lock_campaign_file(descriptor: int, platform_name: str) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if platform_name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock_campaign_file(descriptor: int, platform_name: str) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if platform_name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


class OwnedLabCampaignLock:
    """One host-wide owned-lab campaign, held through terminal cleanup."""

    __slots__ = ("__descriptor", "__path")

    def __init__(self, path: Path = OWNED_LAB_CAMPAIGN_LOCK) -> None:
        self.__descriptor: int | None = None
        self.__path = Path(path)

    def __enter__(self) -> OwnedLabCampaignLock:
        flags = os.O_CREAT | os.O_RDWR
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(self.__path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("owned_lab_campaign_lock_invalid")
            if os.fstat(descriptor).st_size < 1:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            lock_campaign_file(descriptor, os.name)
        except (BlockingIOError, OSError):
            if descriptor is not None:
                os.close(descriptor)
            raise RuntimeError("owned_lab_campaign_locked") from None
        self.__descriptor = descriptor
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        descriptor = self.__descriptor
        self.__descriptor = None
        if descriptor is None:
            return
        try:
            unlock_campaign_file(descriptor, os.name)
        finally:
            os.close(descriptor)
