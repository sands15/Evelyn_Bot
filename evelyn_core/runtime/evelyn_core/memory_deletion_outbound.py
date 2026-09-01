from __future__ import annotations

import contextlib
from contextvars import ContextVar
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from .config import MEMORY_ROOT
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
    memory_deletion_journal_read_guard,
)


_UNSET = object()
_memory_deletion_outbound_position: ContextVar[
    MemoryDeletionPosition | None
] = ContextVar(
    "memory_deletion_outbound_position",
    default=None,
)


def reset_memory_deletion_outbound_position() -> None:
    """Clear a prior turn's deletion boundary in the current async context."""

    _memory_deletion_outbound_position.set(None)


def capture_memory_deletion_outbound_position(
    position: MemoryDeletionPosition,
) -> MemoryDeletionPosition:
    """Bind a verified, content-free deletion position to the current turn."""

    if not isinstance(position, MemoryDeletionPosition):
        raise MemoryDeletionJournalIntegrityError()
    _memory_deletion_outbound_position.set(position)
    return position


def current_memory_deletion_outbound_position() -> (
    MemoryDeletionPosition | None
):
    return _memory_deletion_outbound_position.get()


@contextlib.contextmanager
def memory_deletion_outbound_guard(
    *,
    expected_position: MemoryDeletionPosition | None | object = _UNSET,
    expected_deletion_generation: int | None = None,
    required: bool | None = None,
    index_dir: Path | None = None,
) -> Iterator[MemoryDeletionPosition | None]:
    """Revalidate and lease a prepared memory context at an outbound sink.

    The position object stays internal to the request task.  A caller that
    knows it is sending memory can set ``required=True`` so a missing capture
    fails before an HTTP client sees the request body.
    """

    position = (
        current_memory_deletion_outbound_position()
        if expected_position is _UNSET
        else expected_position
    )
    if position is None:
        if required:
            raise MemoryDeletionJournalIntegrityError()
        yield None
        return
    if not isinstance(position, MemoryDeletionPosition):
        raise MemoryDeletionJournalIntegrityError()
    if (
        expected_deletion_generation is not None
        and (
            isinstance(expected_deletion_generation, bool)
            or not isinstance(expected_deletion_generation, int)
            or expected_deletion_generation < 0
            or position.deletion_generation
            != expected_deletion_generation
        )
    ):
        raise MemoryDeletionJournalIntegrityError()
    target_index_dir = (
        Path(index_dir)
        if index_dir is not None
        else Path(MEMORY_ROOT) / "memory_index"
    )
    with memory_deletion_journal_read_guard(
        target_index_dir,
        expected_position=position,
        require_stable=True,
    ) as current_position:
        yield current_position


@contextlib.contextmanager
def memory_deletion_late_commit_guard(
    *,
    expected_position: MemoryDeletionPosition | None | object = _UNSET,
    expected_deletion_generation: int | None = None,
    index_dir: Path | None = None,
) -> Iterator[MemoryDeletionPosition]:
    """Hold the captured deletion generation stable through a late commit."""

    with memory_deletion_outbound_guard(
        expected_position=expected_position,
        expected_deletion_generation=expected_deletion_generation,
        required=True,
        index_dir=index_dir,
    ) as position:
        if position is None:  # pragma: no cover - required=True rejects it.
            raise MemoryDeletionJournalIntegrityError()
        yield position


@contextlib.asynccontextmanager
async def memory_deletion_outbound_request(
    request_factory: Callable[..., Any],
    *args: Any,
    expected_position: MemoryDeletionPosition | None | object = _UNSET,
    expected_deletion_generation: int | None = None,
    memory_boundary_required: bool | None = None,
    memory_index_dir: Path | None = None,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Enter the deletion lease before constructing an outbound request."""

    with memory_deletion_outbound_guard(
        expected_position=expected_position,
        expected_deletion_generation=expected_deletion_generation,
        required=memory_boundary_required,
        index_dir=memory_index_dir,
    ):
        async with request_factory(*args, **kwargs) as response:
            yield response


__all__ = [
    "capture_memory_deletion_outbound_position",
    "current_memory_deletion_outbound_position",
    "memory_deletion_late_commit_guard",
    "memory_deletion_outbound_guard",
    "memory_deletion_outbound_request",
    "reset_memory_deletion_outbound_position",
]
