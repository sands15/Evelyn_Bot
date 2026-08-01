from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping

from aiohttp import web

from .memory_deletion_journal import (
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MemoryDeletionJournalIntegrityError,
)
from .memory_exposure import (
    MemoryExposurePosition,
    memory_exposure_guard,
    memory_exposure_position_from_dict,
    memory_exposure_position_to_dict,
)


CONTROL_PAGE_MEMORY_STATE_HEADER = "X-Evelyn-Memory-State"
CONTROL_PAGE_MEMORY_BOUNDARY_HEADER = "X-Evelyn-Memory-Boundary"
_NO_MEMORY_BOUNDARY = "-"
_MAX_BOUNDARY_HEADER_CHARS = 8192


def control_page_memory_handoff_headers(
    position: MemoryExposurePosition | None,
) -> dict[str, str]:
    if position is None:
        return {
            CONTROL_PAGE_MEMORY_STATE_HEADER: "not_used",
            CONTROL_PAGE_MEMORY_BOUNDARY_HEADER: _NO_MEMORY_BOUNDARY,
        }
    encoded = base64.urlsafe_b64encode(
        json.dumps(
            memory_exposure_position_to_dict(position),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    ).decode("ascii")
    if len(encoded) > _MAX_BOUNDARY_HEADER_CHARS:
        raise MemoryDeletionJournalIntegrityError()
    return {
        CONTROL_PAGE_MEMORY_STATE_HEADER: "bound",
        CONTROL_PAGE_MEMORY_BOUNDARY_HEADER: encoded,
    }


def parse_control_page_memory_handoff_headers(
    headers: Mapping[str, Any],
) -> MemoryExposurePosition | None:
    state = str(headers.get(CONTROL_PAGE_MEMORY_STATE_HEADER) or "")
    encoded = str(
        headers.get(CONTROL_PAGE_MEMORY_BOUNDARY_HEADER) or ""
    )
    if state == "not_used" and encoded == _NO_MEMORY_BOUNDARY:
        return None
    if (
        state != "bound"
        or not encoded
        or encoded == _NO_MEMORY_BOUNDARY
        or len(encoded) > _MAX_BOUNDARY_HEADER_CHARS
    ):
        raise MemoryDeletionJournalIntegrityError()
    try:
        raw = base64.b64decode(
            encoded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("ascii"))
    except (
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        raise MemoryDeletionJournalIntegrityError() from None
    return memory_exposure_position_from_dict(payload)


class ControlPageMemoryGuardedJsonResponse(web.Response):
    """Hold an exact memory lease until aiohttp finishes the body write."""

    def __init__(
        self,
        payload: Any,
        *,
        expected_position: MemoryExposurePosition | None,
        memory_index_dir: Path,
        status: int = 200,
        emit_handoff_headers: bool = True,
    ) -> None:
        super().__init__(
            text=json.dumps(payload, ensure_ascii=False),
            status=status,
            content_type="application/json",
            charset="utf-8",
        )
        self.headers["Cache-Control"] = "no-store"
        self._memory_expected_position = expected_position
        self._memory_index_dir = Path(memory_index_dir)
        self._memory_guard: Any | None = None
        self._memory_guard_disabled = expected_position is None
        self._emit_handoff_headers = bool(emit_handoff_headers)
        if self._emit_handoff_headers:
            self.headers.update(
                control_page_memory_handoff_headers(expected_position)
            )

    @property
    def memory_expected_position(self) -> MemoryExposurePosition | None:
        return self._memory_expected_position

    def _enter_memory_guard(self) -> None:
        if self._memory_guard is not None or self._memory_guard_disabled:
            return
        guard = memory_exposure_guard(
            expected_position=self._memory_expected_position,
            required=True,
            index_dir=self._memory_index_dir,
        )
        guard.__enter__()
        self._memory_guard = guard

    def _exit_memory_guard(self, exc: BaseException | None = None) -> None:
        guard = self._memory_guard
        self._memory_guard = None
        if guard is None:
            return
        guard.__exit__(
            type(exc) if exc is not None else None,
            exc,
            exc.__traceback__ if exc is not None else None,
        )

    def _replace_with_integrity_failure(self) -> None:
        self._memory_expected_position = None
        self._memory_guard_disabled = True
        self.set_status(503)
        self.text = json.dumps(
            {
                "ok": False,
                "error": MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            },
            ensure_ascii=False,
        )
        self.headers["Cache-Control"] = "no-store"
        if self._emit_handoff_headers:
            self.headers.update(control_page_memory_handoff_headers(None))

    async def prepare(self, request: web.BaseRequest) -> Any:
        try:
            self._enter_memory_guard()
        except MemoryDeletionJournalIntegrityError:
            self._replace_with_integrity_failure()
        try:
            return await super().prepare(request)
        except BaseException as exc:
            self._exit_memory_guard(exc)
            raise

    async def write_eof(self, data: bytes = b"") -> None:
        try:
            self._enter_memory_guard()
            await super().write_eof(data)
        except BaseException as exc:
            self._exit_memory_guard(exc)
            raise
        else:
            self._exit_memory_guard()


def control_page_memory_guarded_json_response(
    payload: Any,
    *,
    expected_position: MemoryExposurePosition | None,
    memory_index_dir: Path,
    status: int = 200,
    emit_handoff_headers: bool = True,
) -> ControlPageMemoryGuardedJsonResponse:
    return ControlPageMemoryGuardedJsonResponse(
        payload,
        expected_position=expected_position,
        memory_index_dir=memory_index_dir,
        status=status,
        emit_handoff_headers=emit_handoff_headers,
    )


__all__ = [
    "ControlPageMemoryGuardedJsonResponse",
    "CONTROL_PAGE_MEMORY_BOUNDARY_HEADER",
    "CONTROL_PAGE_MEMORY_STATE_HEADER",
    "control_page_memory_handoff_headers",
    "control_page_memory_guarded_json_response",
    "parse_control_page_memory_handoff_headers",
]
