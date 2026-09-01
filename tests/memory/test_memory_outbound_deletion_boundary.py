from __future__ import annotations

import asyncio
import os
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


class MemoryOutboundDeletionBoundaryTests(unittest.TestCase):
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
    def tombstone(note_id: str, second: int = 0) -> dict[str, object]:
        return {
            "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
            "noteId": note_id,
            "noteType": "concept",
            "sourceType": "conversation",
            "reason": "privacy_request",
            "deletedAt": f"2026-08-01T00:00:{second:02d}Z",
        }

    def test_unchanged_position_admits_request(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                with self.unconfigured_authenticity():
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-0123456789abcdef"),
                    )
                    position = journal.memory_deletion_journal_position(
                        index_dir
                    )
                    outbound.capture_memory_deletion_outbound_position(
                        position
                    )
                    calls: list[dict[str, object]] = []

                    def request_factory(*_args, **kwargs):
                        calls.append(dict(kwargs))
                        return _Response()

                    async with outbound.memory_deletion_outbound_request(
                        request_factory,
                        "http://llm.invalid/v1/chat/completions",
                        json={"messages": [{"content": "memory canary"}]},
                        memory_index_dir=index_dir,
                    ) as response:
                        self.assertEqual(response.status, 200)
                    self.assertEqual(len(calls), 1)

        asyncio.run(run())

    def test_delete_between_context_and_send_blocks_before_request_factory(
        self,
    ) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                with self.unconfigured_authenticity():
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-0123456789abcdef"),
                    )
                    outbound.capture_memory_deletion_outbound_position(
                        journal.memory_deletion_journal_position(index_dir)
                    )
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-fedcba9876543210", 1),
                    )
                    captured_payloads: list[dict[str, object]] = []

                    def request_factory(*_args, **kwargs):
                        captured_payloads.append(dict(kwargs))
                        return _Response()

                    with self.assertRaises(
                        journal.MemoryDeletionJournalIntegrityError
                    ) as raised:
                        async with outbound.memory_deletion_outbound_request(
                            request_factory,
                            "http://llm.invalid/v1/chat/completions",
                            json={
                                "messages": [
                                    {"content": "PRIVATE deleted memory canary"}
                                ]
                            },
                            memory_index_dir=index_dir,
                        ):
                            self.fail("stale memory reached outbound request")
                    self.assertEqual(
                        str(raised.exception),
                        journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
                    )
                    self.assertEqual(captured_payloads, [])

        asyncio.run(run())

    def test_request_lease_rejects_concurrent_delete(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                with self.unconfigured_authenticity():
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-0123456789abcdef"),
                    )
                    outbound.capture_memory_deletion_outbound_position(
                        journal.memory_deletion_journal_position(index_dir)
                    )

                    def request_factory(*_args, **_kwargs):
                        return _Response()

                    async with outbound.memory_deletion_outbound_request(
                        request_factory,
                        "http://llm.invalid/v1/chat/completions",
                        json={"messages": [{"content": "memory canary"}]},
                        memory_index_dir=index_dir,
                    ):
                        async def delete_now() -> None:
                            journal.append_memory_deletion_tombstone(
                                index_dir,
                                self.tombstone(
                                    "concept-fedcba9876543210",
                                    1,
                                ),
                            )

                        with self.assertRaises(
                            journal.MemoryDeletionJournalIntegrityError
                        ):
                            await asyncio.create_task(delete_now())

                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-fedcba9876543210", 1),
                    )
                    self.assertEqual(
                        len(
                            journal.read_memory_deletion_tombstones(
                                index_dir
                            )
                        ),
                        2,
                    )

        asyncio.run(run())

    def test_required_boundary_and_invalid_capture_fail_closed(self) -> None:
        outbound.reset_memory_deletion_outbound_position()
        with self.assertRaises(
            journal.MemoryDeletionJournalIntegrityError
        ):
            with outbound.memory_deletion_outbound_guard(required=True):
                self.fail("missing boundary was accepted")
        with self.assertRaises(
            journal.MemoryDeletionJournalIntegrityError
        ):
            outbound.capture_memory_deletion_outbound_position("invalid")  # type: ignore[arg-type]

    def test_late_commit_guard_requires_exact_current_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp) / "memory_index"
            with self.unconfigured_authenticity():
                position = journal.memory_deletion_journal_position(index_dir)
                outbound.capture_memory_deletion_outbound_position(position)

                with outbound.memory_deletion_late_commit_guard(
                    expected_deletion_generation=0,
                    index_dir=index_dir,
                ) as current:
                    self.assertEqual(current, position)

                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ):
                    with outbound.memory_deletion_late_commit_guard(
                        expected_deletion_generation=1,
                        index_dir=index_dir,
                    ):
                        self.fail("stale generation reached late commit")

                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-fedcba9876543210", 1),
                )
                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ):
                    with outbound.memory_deletion_late_commit_guard(
                        expected_deletion_generation=0,
                        index_dir=index_dir,
                    ):
                        self.fail("deleted source reached late commit")


if __name__ == "__main__":
    unittest.main()
