from __future__ import annotations

import contextlib
import re
import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from .config import MEMORY_ROOT
from .memory_deletion_journal import (
    MEMORY_DELETION_POSITION_SCHEMA,
    MEMORY_DELETE_TOMBSTONE_WRITER_LOCK_NAME,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
    memory_deletion_journal_read_guard,
    memory_deletion_note_id_is_canonical,
    read_memory_deletion_tombstones,
)
from .memory_deletion_outbound import (
    current_memory_deletion_outbound_position,
)


MEMORY_EXPOSURE_POSITION_SCHEMA = "memory.exposure.position.v1"
MEMORY_INDEX_DB_NAME = "memory.sqlite"

_MAX_SQLITE_INTEGER = (1 << 63) - 1
_CANONICAL_NONNEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_UNSET = object()
_POSITION_KEYS = frozenset(
    {
        "schema",
        "deletionPosition",
        "memoryVersion",
        "suppliedNoteIds",
        "contentFree",
    }
)
_DELETION_POSITION_KEYS = frozenset(
    {"schema", "rootDigest", "sequence", "positionDigest"}
)


def _integrity_failure() -> MemoryDeletionJournalIntegrityError:
    return MemoryDeletionJournalIntegrityError()


def _deletion_position_is_well_formed(
    position: object,
) -> bool:
    return (
        isinstance(position, MemoryDeletionPosition)
        and position.schema == MEMORY_DELETION_POSITION_SCHEMA
        and isinstance(position.root_digest, str)
        and _LOWERCASE_SHA256.fullmatch(position.root_digest) is not None
        and type(position.sequence) is int
        and position.sequence >= 0
        and isinstance(position.position_digest, str)
        and _LOWERCASE_SHA256.fullmatch(position.position_digest) is not None
    )


def _memory_version_is_valid(value: object) -> bool:
    return (
        type(value) is int
        and 0 <= value <= _MAX_SQLITE_INTEGER
    )


def _supplied_note_ids_are_canonical(value: object) -> bool:
    if type(value) is not tuple:
        return False
    note_ids = value
    if any(
        type(note_id) is not str
        or not memory_deletion_note_id_is_canonical(note_id)
        for note_id in note_ids
    ):
        return False
    return note_ids == tuple(sorted(set(note_ids)))


@dataclass(frozen=True)
class MemoryExposurePosition:
    """Content-free coordinates for memory supplied to one response."""

    deletion_position: MemoryDeletionPosition
    memory_version: int
    supplied_note_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not _deletion_position_is_well_formed(self.deletion_position)
            or not _memory_version_is_valid(self.memory_version)
            or not _supplied_note_ids_are_canonical(
                self.supplied_note_ids
            )
        ):
            raise _integrity_failure()


_memory_exposure_position: ContextVar[MemoryExposurePosition | None] = (
    ContextVar("memory_exposure_position", default=None)
)


def reset_memory_exposure_position() -> None:
    """Clear a prior turn's exposure coordinates in this async context."""

    _memory_exposure_position.set(None)


def capture_memory_exposure_position(
    position: MemoryExposurePosition,
) -> MemoryExposurePosition:
    """Bind validated, content-free memory coordinates to this turn."""

    if not isinstance(position, MemoryExposurePosition):
        raise _integrity_failure()
    # Revalidate in case a caller bypassed the frozen constructor through
    # low-level object mutation or deserialization tricks.
    if (
        not _deletion_position_is_well_formed(position.deletion_position)
        or not _memory_version_is_valid(position.memory_version)
        or not _supplied_note_ids_are_canonical(
            position.supplied_note_ids
        )
    ):
        raise _integrity_failure()
    _memory_exposure_position.set(position)
    return position


def current_memory_exposure_position() -> MemoryExposurePosition | None:
    return _memory_exposure_position.get()


def memory_exposure_position_to_dict(
    position: MemoryExposurePosition,
) -> dict[str, Any]:
    if (
        not isinstance(position, MemoryExposurePosition)
        or not _deletion_position_is_well_formed(
            position.deletion_position
        )
        or not _memory_version_is_valid(position.memory_version)
        or not _supplied_note_ids_are_canonical(
            position.supplied_note_ids
        )
    ):
        raise _integrity_failure()
    deletion = position.deletion_position
    return {
        "schema": MEMORY_EXPOSURE_POSITION_SCHEMA,
        "deletionPosition": {
            "schema": deletion.schema,
            "rootDigest": deletion.root_digest,
            "sequence": deletion.sequence,
            "positionDigest": deletion.position_digest,
        },
        "memoryVersion": position.memory_version,
        "suppliedNoteIds": list(position.supplied_note_ids),
        "contentFree": True,
    }


def memory_exposure_position_from_dict(
    value: object,
) -> MemoryExposurePosition:
    if not isinstance(value, dict) or set(value) != _POSITION_KEYS:
        raise _integrity_failure()
    deletion = value.get("deletionPosition")
    if (
        value.get("schema") != MEMORY_EXPOSURE_POSITION_SCHEMA
        or value.get("contentFree") is not True
        or not isinstance(deletion, dict)
        or set(deletion) != _DELETION_POSITION_KEYS
        or deletion.get("schema") != MEMORY_DELETION_POSITION_SCHEMA
        or not isinstance(value.get("suppliedNoteIds"), list)
    ):
        raise _integrity_failure()
    try:
        position = MemoryExposurePosition(
            deletion_position=MemoryDeletionPosition(
                schema=deletion["schema"],
                root_digest=deletion["rootDigest"],
                sequence=deletion["sequence"],
                position_digest=deletion["positionDigest"],
            ),
            memory_version=value["memoryVersion"],
            supplied_note_ids=tuple(value["suppliedNoteIds"]),
        )
    except (KeyError, TypeError, ValueError):
        raise _integrity_failure() from None
    return position


def combine_memory_exposure_positions(
    *positions: MemoryExposurePosition,
) -> MemoryExposurePosition:
    """Combine note attribution only when all exposure coordinates agree."""

    if not positions:
        raise _integrity_failure()
    for position in positions:
        if not isinstance(position, MemoryExposurePosition):
            raise _integrity_failure()
        capture_safe = (
            _deletion_position_is_well_formed(position.deletion_position)
            and _memory_version_is_valid(position.memory_version)
            and _supplied_note_ids_are_canonical(
                position.supplied_note_ids
            )
        )
        if not capture_safe:
            raise _integrity_failure()

    first = positions[0]
    if any(
        position.deletion_position != first.deletion_position
        or position.memory_version != first.memory_version
        for position in positions[1:]
    ):
        raise _integrity_failure()
    supplied_note_ids = tuple(
        sorted(
            {
                note_id
                for position in positions
                for note_id in position.supplied_note_ids
            }
        )
    )
    return MemoryExposurePosition(
        deletion_position=first.deletion_position,
        memory_version=first.memory_version,
        supplied_note_ids=supplied_note_ids,
    )


def _empty_new_index(index_dir: Path) -> bool:
    """Recognize only an untouched index plus the deletion lease file."""

    try:
        if index_dir.is_symlink():
            return False
        if not index_dir.exists():
            return True
        if not index_dir.is_dir():
            return False
        allowed = {MEMORY_DELETE_TOMBSTONE_WRITER_LOCK_NAME}
        entries = list(index_dir.iterdir())
        return all(
            entry.name in allowed
            and not entry.is_symlink()
            and entry.is_file()
            for entry in entries
        )
    except OSError:
        return False


def read_memory_version(index_dir: Path) -> int:
    """Read the exact SQLite memory version without creating or repairing it.

    A genuinely new empty index has version zero. Any existing index state
    without a readable, canonical metadata value fails closed.
    """

    try:
        candidate = Path(index_dir)
        if candidate.is_symlink() or (
            candidate.exists() and not candidate.is_dir()
        ):
            raise _integrity_failure()
        resolved_index = candidate.resolve()
        db_path = resolved_index / MEMORY_INDEX_DB_NAME
        if not db_path.exists():
            if _empty_new_index(resolved_index):
                return 0
            raise _integrity_failure()
        if db_path.is_symlink() or not db_path.is_file():
            raise _integrity_failure()
        resolved_db = db_path.resolve(strict=True)
        if resolved_db.parent != resolved_index:
            raise _integrity_failure()

        uri = f"{resolved_db.as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                "SELECT value FROM metadata WHERE key = ? LIMIT 2",
                ("memory_version",),
            ).fetchall()
        finally:
            connection.close()
    except MemoryDeletionJournalIntegrityError:
        raise
    except (
        OSError,
        RuntimeError,
        sqlite3.Error,
        TypeError,
        ValueError,
    ):
        raise _integrity_failure() from None

    if len(rows) != 1:
        raise _integrity_failure()
    raw_version = rows[0][0]
    if type(raw_version) is int:
        encoded_version = str(raw_version)
    elif type(raw_version) is str:
        encoded_version = raw_version
    else:
        raise _integrity_failure()
    if (
        len(encoded_version) > 19
        or _CANONICAL_NONNEGATIVE_INTEGER.fullmatch(encoded_version) is None
    ):
        raise _integrity_failure()
    version = int(encoded_version)
    if not _memory_version_is_valid(version):
        raise _integrity_failure()
    return version


@contextlib.contextmanager
def memory_exposure_guard(
    *,
    expected_position: MemoryExposurePosition | None | object = _UNSET,
    required: bool | None = None,
    index_dir: Path | None = None,
) -> Iterator[
    MemoryExposurePosition | MemoryDeletionPosition | None
]:
    """Validate and lease memory coordinates across one response boundary."""

    exposure = (
        current_memory_exposure_position()
        if expected_position is _UNSET
        else expected_position
    )
    target_index_dir = (
        Path(index_dir)
        if index_dir is not None
        else Path(MEMORY_ROOT) / "memory_index"
    )

    if exposure is None:
        if required:
            raise _integrity_failure()
        deletion_position = current_memory_deletion_outbound_position()
        if deletion_position is None:
            yield None
            return
        with memory_deletion_journal_read_guard(
            target_index_dir,
            expected_position=deletion_position,
            require_stable=True,
        ) as current_deletion_position:
            yield current_deletion_position
        return

    if not isinstance(exposure, MemoryExposurePosition):
        raise _integrity_failure()
    if (
        not _deletion_position_is_well_formed(
            exposure.deletion_position
        )
        or not _memory_version_is_valid(exposure.memory_version)
        or not _supplied_note_ids_are_canonical(
            exposure.supplied_note_ids
        )
    ):
        raise _integrity_failure()

    # The deletion lease is intentionally acquired first. Memory corrections
    # and tombstones share it, so neither coordinate can change between this
    # comparison and the end of response consumption.
    with memory_deletion_journal_read_guard(
        target_index_dir,
        expected_position=exposure.deletion_position,
        require_stable=True,
    ):
        if read_memory_version(target_index_dir) != exposure.memory_version:
            raise _integrity_failure()
        tombstoned_note_ids = {
            str(row.get("noteId"))
            for row in read_memory_deletion_tombstones(
                target_index_dir
            )
            if isinstance(row, dict)
            and isinstance(row.get("noteId"), str)
        }
        if any(
            note_id in tombstoned_note_ids
            for note_id in exposure.supplied_note_ids
        ):
            raise _integrity_failure()
        yield exposure
        if read_memory_version(target_index_dir) != exposure.memory_version:
            raise _integrity_failure()


@contextlib.asynccontextmanager
async def memory_exposure_request(
    request_factory: Callable[..., Any],
    *args: Any,
    expected_position: MemoryExposurePosition | None | object = _UNSET,
    memory_boundary_required: bool | None = None,
    memory_index_dir: Path | None = None,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Enter the exposure lease before constructing an outbound request."""

    with memory_exposure_guard(
        expected_position=expected_position,
        required=memory_boundary_required,
        index_dir=memory_index_dir,
    ):
        async with request_factory(*args, **kwargs) as response:
            yield response


__all__ = [
    "MEMORY_EXPOSURE_POSITION_SCHEMA",
    "MEMORY_INDEX_DB_NAME",
    "MemoryExposurePosition",
    "capture_memory_exposure_position",
    "combine_memory_exposure_positions",
    "current_memory_exposure_position",
    "memory_exposure_guard",
    "memory_exposure_position_from_dict",
    "memory_exposure_position_to_dict",
    "memory_exposure_request",
    "read_memory_version",
    "reset_memory_exposure_position",
]
