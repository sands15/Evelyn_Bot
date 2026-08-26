from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class AsrStreamStatus(str, Enum):
    STREAMING = "streaming"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AsrRevision:
    revision: int
    text: str
    stable_prefix: str
    volatile_suffix: str
    is_final: bool
    conflicts_with_stable_prefix: bool = False

    @property
    def authoritative(self) -> bool:
        """Only a non-empty, internally consistent final may leave ASR."""

        return self.is_final and bool(self.text) and not self.conflicts_with_stable_prefix


def _clean_revision_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _longest_common_prefix(left: str, right: str) -> str:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return left[:index]


def _safe_stable_prefix(common_prefix: str, *, holdback_chars: int) -> str:
    trimmed = common_prefix.rstrip()
    boundary = trimmed.rfind(" ")
    if boundary >= 0:
        return trimmed[: boundary + 1]
    if len(trimmed) <= holdback_chars:
        return ""
    return trimmed[:-holdback_chars]


class AsrStreamSession:
    """Pure revision state; transport, admission, and side effects stay outside."""

    def __init__(self, *, holdback_chars: int = 3) -> None:
        if holdback_chars < 1:
            raise ValueError("holdback_chars_must_be_positive")
        self._holdback_chars = holdback_chars
        self._status = AsrStreamStatus.STREAMING
        self._last_revision = 0
        self._previous_text: str | None = None
        self._stable_prefix = ""

    @property
    def status(self) -> AsrStreamStatus:
        return self._status

    @property
    def stable_prefix(self) -> str:
        return self._stable_prefix

    @property
    def last_revision(self) -> int:
        return self._last_revision

    def apply(self, *, revision: int, text: str, is_final: bool) -> AsrRevision:
        if self._status is not AsrStreamStatus.STREAMING:
            raise RuntimeError("asr_stream_not_active")
        if isinstance(revision, bool) or revision != self._last_revision + 1:
            raise ValueError("asr_revision_not_monotonic")

        current = _clean_revision_text(text)
        conflict = bool(self._stable_prefix and not current.startswith(self._stable_prefix))

        if not is_final and self._previous_text is not None and not conflict:
            common = _longest_common_prefix(self._previous_text, current)
            candidate = _safe_stable_prefix(common, holdback_chars=self._holdback_chars)
            if candidate.startswith(self._stable_prefix) and len(candidate) > len(self._stable_prefix):
                self._stable_prefix = candidate

        volatile = current[len(self._stable_prefix) :] if current.startswith(self._stable_prefix) else current
        result = AsrRevision(
            revision=revision,
            text=current,
            stable_prefix=self._stable_prefix,
            volatile_suffix=volatile,
            is_final=is_final,
            conflicts_with_stable_prefix=conflict,
        )
        self._last_revision = revision
        self._previous_text = current
        if is_final:
            self._status = AsrStreamStatus.FINISHED
        return result

    def cancel(self) -> None:
        if self._status is AsrStreamStatus.STREAMING:
            self._status = AsrStreamStatus.CANCELLED


__all__ = ["AsrRevision", "AsrStreamSession", "AsrStreamStatus"]
