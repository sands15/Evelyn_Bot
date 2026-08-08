from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core import memory_deletion_outbound as outbound  # noqa: E402
from evelyn_core import memory_exposure as exposure  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class MemoryExposureTests(unittest.TestCase):
    def setUp(self) -> None:
        exposure.reset_memory_exposure_position()
        outbound.reset_memory_deletion_outbound_position()

    def tearDown(self) -> None:
        exposure.reset_memory_exposure_position()
        outbound.reset_memory_deletion_outbound_position()

    @contextmanager
    def unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    @staticmethod
    def write_memory_version(
        index_dir: Path,
        value: object,
        *,
        include_metadata: bool = True,
        include_row: bool = True,
    ) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(index_dir / exposure.MEMORY_INDEX_DB_NAME)
        )
        try:
            if include_metadata:
                connection.execute(
                    """
                    CREATE TABLE metadata (
                        key TEXT PRIMARY KEY,
                        value NOT NULL
                    )
                    """
                )
                if include_row:
                    connection.execute(
                        "INSERT INTO metadata(key, value) VALUES(?, ?)",
                        ("memory_version", value),
                    )
            else:
                connection.execute(
                    "CREATE TABLE unrelated(value TEXT NOT NULL)"
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def replace_memory_version(index_dir: Path, value: object) -> None:
        connection = sqlite3.connect(
            str(index_dir / exposure.MEMORY_INDEX_DB_NAME)
        )
        try:
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?",
                (value, "memory_version"),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def tombstone(note_id: str, second: int = 0) -> dict[str, object]:
        return {
            "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
            "noteId": note_id,
            "noteType": "concept",
            "sourceType": "conversation",
            "reason": "privacy_request",
            "deletedAt": f"2026-08-01T00:00:{second:02d}Z",
        }

    def make_position(
        self,
        index_dir: Path,
        *,
        version: int,
        note_ids: tuple[str, ...] = (),
    ) -> exposure.MemoryExposurePosition:
        return exposure.MemoryExposurePosition(
            deletion_position=journal.memory_deletion_journal_position(
                index_dir
            ),
            memory_version=version,
            supplied_note_ids=note_ids,
        )

    def assert_integrity_failure(self, callable_, *args, **kwargs) -> None:
        with self.assertRaises(
            journal.MemoryDeletionJournalIntegrityError
        ) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(
            str(raised.exception),
            journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )

    def test_same_version_calls_request_factory(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "7")
                with self.unconfigured_authenticity():
                    captured = self.make_position(
                        index_dir,
                        version=7,
                        note_ids=("concept-0123456789abcdef",),
                    )
                    self.assertIs(
                        exposure.capture_memory_exposure_position(captured),
                        captured,
                    )
                    self.assertIs(
                        exposure.current_memory_exposure_position(),
                        captured,
                    )
                    calls: list[dict[str, object]] = []

                    def request_factory(*_args, **kwargs):
                        calls.append(dict(kwargs))
                        return _Response()

                    async with exposure.memory_exposure_request(
                        request_factory,
                        "http://llm.invalid/v1/chat/completions",
                        json={"messages": [{"content": "canary"}]},
                        memory_boundary_required=True,
                        memory_index_dir=index_dir,
                    ) as response:
                        self.assertEqual(response.status, 200)
                    self.assertEqual(len(calls), 1)

        asyncio.run(run())

    def test_version_advance_blocks_before_request_factory(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "1")
                with self.unconfigured_authenticity():
                    exposure.capture_memory_exposure_position(
                        self.make_position(index_dir, version=1)
                    )
                    self.replace_memory_version(index_dir, "2")
                    calls = 0

                    def request_factory(*_args, **_kwargs):
                        nonlocal calls
                        calls += 1
                        return _Response()

                    with self.assertRaises(
                        journal.MemoryDeletionJournalIntegrityError
                    ) as raised:
                        async with exposure.memory_exposure_request(
                            request_factory,
                            "http://llm.invalid/v1/chat/completions",
                            memory_boundary_required=True,
                            memory_index_dir=index_dir,
                        ):
                            self.fail("stale memory reached request")
                    self.assertEqual(
                        str(raised.exception),
                        journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
                    )
                    self.assertEqual(calls, 0)

        asyncio.run(run())

    def test_expected_position_is_consumed_and_not_forwarded(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "3")
                with self.unconfigured_authenticity():
                    captured = self.make_position(index_dir, version=3)
                    forwarded: list[dict[str, object]] = []

                    def request_factory(*_args, **kwargs):
                        forwarded.append(dict(kwargs))
                        return _Response()

                    async with exposure.memory_exposure_request(
                        request_factory,
                        "http://llm.invalid/v1/chat/completions",
                        expected_position=captured,
                        marker="kept",
                        memory_boundary_required=True,
                        memory_index_dir=index_dir,
                    ):
                        pass
                    self.assertEqual(forwarded, [{"marker": "kept"}])

                    stale = exposure.MemoryExposurePosition(
                        captured.deletion_position,
                        2,
                    )
                    with self.assertRaises(
                        journal.MemoryDeletionJournalIntegrityError
                    ):
                        async with exposure.memory_exposure_request(
                            request_factory,
                            "http://llm.invalid/v1/chat/completions",
                            expected_position=stale,
                            marker="must-not-send",
                            memory_boundary_required=True,
                            memory_index_dir=index_dir,
                        ):
                            self.fail("stale expected position was accepted")
                    self.assertEqual(forwarded, [{"marker": "kept"}])

        asyncio.run(run())

    def test_tombstone_advance_blocks_before_request_factory(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "1")
                with self.unconfigured_authenticity():
                    exposure.capture_memory_exposure_position(
                        self.make_position(index_dir, version=1)
                    )
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-0123456789abcdef"),
                    )
                    calls = 0

                    def request_factory(*_args, **_kwargs):
                        nonlocal calls
                        calls += 1
                        return _Response()

                    with self.assertRaises(
                        journal.MemoryDeletionJournalIntegrityError
                    ):
                        async with exposure.memory_exposure_request(
                            request_factory,
                            "http://llm.invalid/v1/chat/completions",
                            memory_boundary_required=True,
                            memory_index_dir=index_dir,
                        ):
                            self.fail("deleted memory reached request")
                    self.assertEqual(calls, 0)

        asyncio.run(run())

    def test_current_position_cannot_rebind_an_already_tombstoned_note(
        self,
    ) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "1")
                note_id = "concept-0123456789abcdef"
                with self.unconfigured_authenticity():
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone(note_id),
                    )
                    current_but_deleted = self.make_position(
                        index_dir,
                        version=1,
                        note_ids=(note_id,),
                    )
                    calls = 0

                    def request_factory(*_args, **_kwargs):
                        nonlocal calls
                        calls += 1
                        return _Response()

                    with self.assertRaises(
                        journal.MemoryDeletionJournalIntegrityError
                    ):
                        async with exposure.memory_exposure_request(
                            request_factory,
                            "http://llm.invalid/v1/chat/completions",
                            expected_position=current_but_deleted,
                            memory_boundary_required=True,
                            memory_index_dir=index_dir,
                        ):
                            self.fail("tombstoned note was rebound")
                    self.assertEqual(calls, 0)

        asyncio.run(run())

    def test_read_memory_version_missing_and_corrupt_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            new_index = root / "new" / "memory_index"
            self.assertEqual(exposure.read_memory_version(new_index), 0)
            self.assertFalse(new_index.exists())

            locked_empty_index = root / "locked" / "memory_index"
            with self.unconfigured_authenticity():
                journal.memory_deletion_journal_position(
                    locked_empty_index
                )
            self.assertEqual(
                exposure.read_memory_version(locked_empty_index),
                0,
            )

            missing_db = root / "missing" / "memory_index"
            missing_db.mkdir(parents=True)
            (missing_db / "orphan.json").write_text(
                "{}",
                encoding="utf-8",
            )
            self.assert_integrity_failure(
                exposure.read_memory_version,
                missing_db,
            )

            corrupt_index = root / "corrupt" / "memory_index"
            corrupt_index.mkdir(parents=True)
            (corrupt_index / exposure.MEMORY_INDEX_DB_NAME).write_bytes(
                b"not a sqlite database"
            )
            self.assert_integrity_failure(
                exposure.read_memory_version,
                corrupt_index,
            )

            missing_table = root / "missing-table" / "memory_index"
            self.write_memory_version(
                missing_table,
                "0",
                include_metadata=False,
            )
            self.assert_integrity_failure(
                exposure.read_memory_version,
                missing_table,
            )

            missing_row = root / "missing-row" / "memory_index"
            self.write_memory_version(
                missing_row,
                "0",
                include_row=False,
            )
            self.assert_integrity_failure(
                exposure.read_memory_version,
                missing_row,
            )

            for offset, invalid in enumerate(
                ("", "-1", "01", "1.0", " 1", b"1")
            ):
                with self.subTest(value=invalid):
                    invalid_index = (
                        root / f"invalid-{offset}" / "memory_index"
                    )
                    self.write_memory_version(invalid_index, invalid)
                    self.assert_integrity_failure(
                        exposure.read_memory_version,
                        invalid_index,
                    )

            valid_index = root / "valid" / "memory_index"
            self.write_memory_version(valid_index, "0")
            self.assertEqual(exposure.read_memory_version(valid_index), 0)

    def test_combine_requires_exact_coordinates_and_unions_all_note_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_index = Path(tmp) / "first" / "memory_index"
            second_index = Path(tmp) / "second" / "memory_index"
            self.write_memory_version(first_index, "4")
            self.write_memory_version(second_index, "4")
            with self.unconfigured_authenticity():
                deletion_position = (
                    journal.memory_deletion_journal_position(first_index)
                )
                first = exposure.MemoryExposurePosition(
                    deletion_position,
                    4,
                    ("concept-0123456789abcdef",),
                )
                second = exposure.MemoryExposurePosition(
                    deletion_position,
                    4,
                    ("concept-fedcba9876543210",),
                )
                combined = exposure.combine_memory_exposure_positions(
                    first,
                    second,
                )
                self.assertEqual(
                    combined.supplied_note_ids,
                    (
                        "concept-0123456789abcdef",
                        "concept-fedcba9876543210",
                    ),
                )

                version_mismatch = exposure.MemoryExposurePosition(
                    deletion_position,
                    5,
                )
                self.assert_integrity_failure(
                    exposure.combine_memory_exposure_positions,
                    first,
                    version_mismatch,
                )

                root_mismatch = exposure.MemoryExposurePosition(
                    journal.memory_deletion_journal_position(second_index),
                    4,
                )
                self.assert_integrity_failure(
                    exposure.combine_memory_exposure_positions,
                    first,
                    root_mismatch,
                )

                journal.append_memory_deletion_tombstone(
                    first_index,
                    self.tombstone("concept-aaaaaaaaaaaaaaaa"),
                )
                deletion_mismatch = exposure.MemoryExposurePosition(
                    journal.memory_deletion_journal_position(first_index),
                    4,
                )
                self.assert_integrity_failure(
                    exposure.combine_memory_exposure_positions,
                    first,
                    deletion_mismatch,
                )

                many = tuple(
                    f"concept-{number:016x}"
                    for number in range(12)
                )
                full = exposure.MemoryExposurePosition(
                    deletion_position,
                    4,
                    many,
                )
                additional = exposure.MemoryExposurePosition(
                    deletion_position,
                    4,
                    ("concept-ffffffffffffffff",),
                )
                large_union = exposure.combine_memory_exposure_positions(
                    full,
                    additional,
                )
                self.assertEqual(
                    len(large_union.supplied_note_ids),
                    13,
                )
                self.assertEqual(
                    large_union.supplied_note_ids[-1],
                    "concept-ffffffffffffffff",
                )

    def test_position_wire_contract_round_trips_and_is_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"
            self.write_memory_version(index_dir, "4")
            with self.unconfigured_authenticity():
                position = self.make_position(
                    index_dir,
                    version=4,
                    note_ids=("concept-0123456789abcdef",),
                )
                payload = exposure.memory_exposure_position_to_dict(
                    position
                )
                self.assertEqual(
                    exposure.memory_exposure_position_from_dict(
                        payload
                    ),
                    position,
                )
                self.assertTrue(payload["contentFree"])
                self.assertNotIn("path", str(payload).lower())

                invalid = dict(payload)
                invalid["unexpected"] = True
                self.assert_integrity_failure(
                    exposure.memory_exposure_position_from_dict,
                    invalid,
                )

    def test_response_consumption_holds_correction_writer_lease(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "1")
                with self.unconfigured_authenticity():
                    exposure.capture_memory_exposure_position(
                        self.make_position(index_dir, version=1)
                    )

                    async def correct_to(version: int) -> None:
                        with journal.memory_deletion_journal_guard(
                            index_dir,
                            require_stable=True,
                        ):
                            self.replace_memory_version(
                                index_dir,
                                str(version),
                            )

                    def request_factory(*_args, **_kwargs):
                        return _Response()

                    async with exposure.memory_exposure_request(
                        request_factory,
                        "http://llm.invalid/v1/chat/completions",
                        memory_boundary_required=True,
                        memory_index_dir=index_dir,
                    ):
                        with self.assertRaises(
                            journal.MemoryDeletionJournalIntegrityError
                        ):
                            await asyncio.create_task(correct_to(2))
                        self.assertEqual(
                            exposure.read_memory_version(index_dir),
                            1,
                        )

                    await asyncio.create_task(correct_to(2))
                    self.assertEqual(
                        exposure.read_memory_version(index_dir),
                        2,
                    )

        asyncio.run(run())

    def test_concurrent_response_consumers_share_the_read_lease(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                self.write_memory_version(index_dir, "1")
                with self.unconfigured_authenticity():
                    exposure.capture_memory_exposure_position(
                        self.make_position(index_dir, version=1)
                    )
                    entered = [asyncio.Event(), asyncio.Event()]
                    release = [asyncio.Event(), asyncio.Event()]

                    def request_factory(*_args, **_kwargs):
                        return _Response()

                    async def consume(index: int) -> None:
                        async with exposure.memory_exposure_request(
                            request_factory,
                            "http://llm.invalid/v1/chat/completions",
                            memory_boundary_required=True,
                            memory_index_dir=index_dir,
                        ):
                            entered[index].set()
                            await release[index].wait()

                    tasks = [
                        asyncio.create_task(consume(0)),
                        asyncio.create_task(consume(1)),
                    ]
                    await entered[0].wait()
                    await entered[1].wait()

                    def correct_to(version: int) -> None:
                        with journal.memory_deletion_journal_guard(
                            index_dir,
                            require_stable=True,
                        ):
                            self.replace_memory_version(
                                index_dir,
                                str(version),
                            )

                    with self.assertRaises(
                        journal.MemoryDeletionJournalBusyError
                    ):
                        correct_to(2)
                    release[0].set()
                    await tasks[0]
                    with self.assertRaises(
                        journal.MemoryDeletionJournalBusyError
                    ):
                        correct_to(2)
                    release[1].set()
                    await tasks[1]
                    correct_to(2)
                    self.assertEqual(
                        exposure.read_memory_version(index_dir),
                        2,
                    )

        asyncio.run(run())

    def test_legacy_deletion_capture_requires_explicit_compatibility_mode(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"
            with self.unconfigured_authenticity():
                outbound.capture_memory_deletion_outbound_position(
                    journal.memory_deletion_journal_position(index_dir)
                )
                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ):
                    with exposure.memory_exposure_guard(
                        required=True,
                        index_dir=index_dir,
                    ):
                        self.fail("typed boundary requirement was downgraded")
                with exposure.memory_exposure_guard(
                    required=False,
                    index_dir=index_dir,
                ) as position:
                    self.assertIsInstance(
                        position,
                        journal.MemoryDeletionPosition,
                    )
                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-0123456789abcdef"),
                )
                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ):
                    with exposure.memory_exposure_guard(
                        required=False,
                        index_dir=index_dir,
                    ):
                        self.fail("stale fallback boundary was accepted")

    def test_position_rejects_noncanonical_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"
            with self.unconfigured_authenticity():
                deletion_position = (
                    journal.memory_deletion_journal_position(index_dir)
                )
                for invalid_version in (-1, True, 1.5, "1"):
                    with self.subTest(version=invalid_version):
                        self.assert_integrity_failure(
                            exposure.MemoryExposurePosition,
                            deletion_position,
                            invalid_version,
                        )
                for invalid_ids in (
                    ["concept-0123456789abcdef"],
                    (
                        "concept-fedcba9876543210",
                        "concept-0123456789abcdef",
                    ),
                    (
                        "concept-0123456789abcdef",
                        "concept-0123456789abcdef",
                    ),
                    ("user-authored transcript",),
                ):
                    with self.subTest(note_ids=invalid_ids):
                        self.assert_integrity_failure(
                            exposure.MemoryExposurePosition,
                            deletion_position,
                            0,
                            invalid_ids,
                        )


if __name__ == "__main__":
    unittest.main()
