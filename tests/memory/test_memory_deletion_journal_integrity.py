from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.runtime_artifact_io import (  # noqa: E402
    DurableCommitError,
)


class MemoryDeletionJournalIntegrityTests(unittest.TestCase):
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

    def tombstone(
        self,
        note_id: str = "concept-deletion-canary",
    ) -> dict[str, object]:
        return {
            "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
            "noteId": note_id,
            "noteType": "concept",
            "sourceType": "conversation-turn-log",
            "reason": "privacy_request",
            "deletedAt": "2026-08-01T00:00:00Z",
        }

    def ledger_id(self, note_id: str) -> str:
        return journal.memory_deletion_ledger_note_id(note_id)

    def assert_integrity_failure(self, callable_) -> None:
        with self.assertRaises(
            journal.MemoryDeletionJournalIntegrityError
        ) as raised:
            callable_()
        self.assertEqual(
            str(raised.exception),
            journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )

    def test_application_ids_and_taxonomies_are_canonicalized(self) -> None:
        native_ids = (
            "0123456789abcdef",
            "concept-0123456789abcdef",
            "daily-2026-08-01",
            "daily-2026-08-01-continuation-2",
            "daily-consolidation-2026-08-01",
            "legacy-guild-123456789",
        )
        for note_id in native_ids:
            with self.subTest(note_id=note_id):
                self.assertEqual(self.ledger_id(note_id), note_id)
                self.assertTrue(
                    journal.memory_deletion_note_id_is_canonical(note_id)
                )

        private_id = "PRIVATE transcript canary full sentence"
        opaque = self.ledger_id(private_id)
        self.assertRegex(opaque, r"^opaque-[0-9a-f]{64}$")
        self.assertNotIn(private_id, opaque)
        self.assertTrue(
            journal.memory_deletion_note_id_is_canonical(opaque)
        )
        self.assertNotEqual(self.ledger_id(opaque), opaque)
        self.assertEqual(
            journal.normalize_memory_deletion_note_type("episodes"),
            "episode",
        )
        self.assertEqual(
            journal.normalize_memory_deletion_note_type("semantic"),
            "concept",
        )
        self.assertEqual(
            journal.normalize_memory_deletion_note_type("custom prose"),
            "unknown",
        )
        self.assertEqual(
            journal.normalize_memory_deletion_source_type(
                "conversation-turn-log"
            ),
            "conversation",
        )
        self.assertEqual(
            journal.normalize_memory_deletion_source_type("custom prose"),
            "unknown",
        )

    def test_public_tombstone_canonicalization_is_content_free(self) -> None:
        private_id = "PRIVATE transcript canary full sentence"
        payload = {
            **self.tombstone(private_id),
            "noteType": "semantic",
            "sourceType": "conversation-turn-log",
        }
        canonical = (
            journal.canonicalize_memory_deletion_tombstone_payload(
                payload
            )
        )

        self.assertEqual(
            canonical,
            {
                **payload,
                "noteId": self.ledger_id(private_id),
                "noteType": "concept",
                "sourceType": "conversation",
            },
        )
        self.assertNotIn(
            private_id,
            json.dumps(canonical, ensure_ascii=False),
        )

    def test_append_writes_v2_chain_and_content_free_head(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                first = journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-first"),
                )
                second = journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-second"),
                )
                rows = journal.read_memory_deletion_tombstones(
                    index_dir
                )
                raw = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                ).read_text(encoding="utf-8")
                head_raw = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                ).read_text(encoding="utf-8")
                head = json.loads(head_raw)
                state = journal.memory_deletion_journal_state(index_dir)
                status = journal.memory_deletion_journal_status(
                    index_dir
                )
                expected_state = (
                    (
                        index_dir
                        / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                    ).stat().st_mtime_ns,
                    (
                        index_dir
                        / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                    ).stat().st_size,
                    (
                        index_dir
                        / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                    ).stat().st_mtime_ns,
                    (
                        index_dir
                        / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                    ).stat().st_size,
                )

        self.assertEqual([row["sequence"] for row in rows], [1, 2])
        self.assertEqual(
            first["previousHash"],
            journal.MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
        )
        self.assertEqual(second["previousHash"], first["eventHash"])
        self.assertEqual(first["noteId"], self.ledger_id("concept-first"))
        self.assertEqual(first["sourceType"], "conversation")
        self.assertEqual(head["sequence"], 2)
        self.assertEqual(head["eventHash"], second["eventHash"])
        self.assertTrue(head["contentFree"])
        self.assertEqual(state, expected_state)
        self.assertEqual(
            status["schema"],
            "memory.deletion.integrity.v1",
        )
        self.assertEqual(status["state"], "locally_verified")
        self.assertFalse(status["rollbackProtected"])
        self.assertTrue(status["contentFree"])
        persisted = raw + head_raw
        for forbidden in (
            '"title"',
            '"body"',
            '"path"',
            '"contentHash"',
            '"transcript"',
            "private deletion body canary",
        ):
            self.assertNotIn(forbidden, persisted)

    def test_source_revocation_shape_is_strict_and_content_free(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                payload = {
                    **self.tombstone("concept-derived"),
                    "sourceType": "derived",
                    "reason": "source_revoked",
                    "revokedByNoteIds": [
                        "concept-source-a",
                        "concept-source-b",
                    ],
                }
                event = journal.append_memory_deletion_tombstone(
                    index_dir,
                    payload,
                )
                raw = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                ).read_text(encoding="utf-8")
                invalid = {
                    **payload,
                    "revokedByNoteIds": [
                        "concept-source-b",
                        "concept-source-a",
                    ],
                }
                self.assert_integrity_failure(
                    lambda: journal.append_memory_deletion_tombstone(
                        index_dir,
                        invalid,
                    )
                )

        self.assertEqual(
            event["revokedByNoteIds"],
            sorted(
                [
                    self.ledger_id("concept-source-a"),
                    self.ledger_id("concept-source-b"),
                ]
            ),
        )
        self.assertNotIn('"body"', raw)

    def test_legacy_raw_prefix_is_strict_and_anchored(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                index_dir.mkdir(parents=True)
                legacy = self.tombstone("concept-legacy")
                legacy_line = json.dumps(
                    legacy,
                    ensure_ascii=False,
                    sort_keys=False,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\r\n"
                path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                path.write_bytes(legacy_line)
                event = journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-v2"),
                )
                expected = hashlib.sha256(
                    b"evelyn.memory.deletion.legacy-prefix.v1\n"
                    + legacy_line
                ).hexdigest()
                rows = path.read_bytes().splitlines(keepends=True)
                changed = self.tombstone("concept-changed-legacy")
                changed_line = json.dumps(
                    changed,
                    ensure_ascii=False,
                    sort_keys=False,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\r\n"
                path.write_bytes(changed_line + rows[1])

                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

        self.assertEqual(event["sequence"], 1)
        self.assertEqual(event["previousHash"], expected)
        self.assertNotEqual(
            expected,
            journal.MEMORY_DELETE_TOMBSTONE_CHAIN_GENESIS,
        )

    def test_legacy_only_journal_is_pinned_before_content_is_returned(
        self,
    ) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                index_dir.mkdir(parents=True)
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                first_line = (
                    json.dumps(
                        self.tombstone("concept-first"),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                second_line = (
                    json.dumps(
                        self.tombstone("concept-second"),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                )
                journal_path.write_bytes(first_line + second_line)

                rows = journal.read_memory_deletion_tombstones(
                    index_dir
                )
                legacy_raw = journal_path.read_text(encoding="utf-8")
                head_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                )
                head = json.loads(
                    head_path.read_text(encoding="utf-8")
                )
                journal_path.write_bytes(first_line)

                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

        self.assertEqual(
            [row["noteId"] for row in rows],
            [
                self.ledger_id("concept-first"),
                self.ledger_id("concept-second"),
            ],
        )
        self.assertIn("concept-first", legacy_raw)
        self.assertNotEqual(rows[0]["noteId"], "concept-first")
        self.assertEqual(rows[0]["sourceType"], "conversation")
        self.assertEqual(head["sequence"], 0)
        self.assertEqual(
            head["legacyPrefixHash"],
            hashlib.sha256(
                b"evelyn.memory.deletion.legacy-prefix.v1\n"
                + first_line
                + second_line
            ).hexdigest(),
        )

    def test_malformed_unknown_incomplete_and_truncated_rows_fail(self) -> None:
        cases = {
            "malformed": b'{"schema":\n',
            "blank": b"\n",
            "unknown": (
                json.dumps({**self.tombstone(), "schema": "unknown.v1"})
                .encode("utf-8")
                + b"\n"
            ),
            "missing": (
                json.dumps(
                    {
                        key: value
                        for key, value in self.tombstone().items()
                        if key != "noteId"
                    }
                ).encode("utf-8")
                + b"\n"
            ),
            "extra": (
                json.dumps(
                    {**self.tombstone(), "body": "private body"}
                ).encode("utf-8")
                + b"\n"
            ),
            "truncated": json.dumps(self.tombstone()).encode("utf-8"),
        }
        with self.unconfigured_authenticity():
            for name, raw in cases.items():
                with self.subTest(name=name):
                    with tempfile.TemporaryDirectory() as tmp:
                        index_dir = Path(tmp) / "memory_index"
                        index_dir.mkdir(parents=True)
                        (
                            index_dir
                            / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                        ).write_bytes(raw)
                        self.assert_integrity_failure(
                            lambda: journal.read_memory_deletion_tombstones(
                                index_dir
                            )
                        )

    def test_missing_head_and_valid_tail_rollback_fail_closed(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-first")
                )
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                head_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                )
                head_path.unlink()
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-first")
                )
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-second")
                )
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                first_line = journal_path.read_bytes().splitlines(
                    keepends=True
                )[0]
                journal_path.write_bytes(first_line)
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

    def test_exact_one_event_head_lag_recovers_under_writer_guard(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-first")
                )
                head_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                )
                prior_head = head_path.read_bytes()
                second = journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-second")
                )
                head_path.write_bytes(prior_head)

                rows = journal.read_memory_deletion_tombstones(index_dir)
                repaired = json.loads(head_path.read_text(encoding="utf-8"))

        self.assertEqual(len(rows), 2)
        self.assertEqual(repaired["sequence"], 2)
        self.assertEqual(repaired["eventHash"], second["eventHash"])

    def test_more_than_one_event_head_lag_is_not_recovered(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-first")
                )
                head_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                )
                first_head = head_path.read_bytes()
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-second")
                )
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-third")
                )
                head_path.write_bytes(first_head)
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

    def test_crash_between_journal_fsync_and_head_recovers_once(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                real_atomic_write = journal.atomic_json_write
                calls = 0

                def fail_event_head(path, payload, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("simulated private host detail")
                    return real_atomic_write(path, payload, **kwargs)

                with patch.object(
                    journal,
                    "atomic_json_write",
                    side_effect=fail_event_head,
                ):
                    self.assert_integrity_failure(
                        lambda: journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-crash"),
                        )
                    )
                rows = journal.read_memory_deletion_tombstones(index_dir)
                head = json.loads(
                    (
                        index_dir
                        / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                    ).read_text(encoding="utf-8")
                )

        self.assertEqual(len(rows), 1)
        self.assertEqual(head["sequence"], 1)

    def test_head_directory_sync_failure_is_never_accepted_as_durable(
        self,
    ) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                real_atomic_write = journal.atomic_json_write
                calls = 0

                def commit_then_fail_directory_sync(
                    path,
                    payload,
                    **kwargs,
                ):
                    nonlocal calls
                    calls += 1
                    real_atomic_write(path, payload, **kwargs)
                    if calls == 2:
                        raise DurableCommitError()

                with patch.object(
                    journal,
                    "atomic_json_write",
                    side_effect=commit_then_fail_directory_sync,
                ):
                    self.assert_integrity_failure(
                        lambda: journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-uncertain-durability"),
                        )
                    )
                rows = journal.read_memory_deletion_tombstones(
                    index_dir
                )

        self.assertEqual(
            [row["noteId"] for row in rows],
            [self.ledger_id("concept-uncertain-durability")],
        )

    def test_external_anchor_rejects_past_journal_and_head_replay(self) -> None:
        with (
            tempfile.TemporaryDirectory() as memory_tmp,
            tempfile.TemporaryDirectory() as key_tmp,
            tempfile.TemporaryDirectory() as anchor_tmp,
        ):
            key_path = Path(key_tmp) / "integrity.key"
            key_path.write_bytes(b"deletion-integrity-key-material!" * 2)
            index_dir = Path(memory_tmp) / "memory_root" / "memory_index"
            with patch.dict(
                os.environ,
                {
                    MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                    MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
                    MEMORY_INTEGRITY_BOOTSTRAP_ENV: "1",
                },
            ):
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-first")
                )
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                head_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                )
                old_journal = journal_path.read_bytes()
                old_head = head_path.read_bytes()
                journal.append_memory_deletion_tombstone(
                    index_dir, self.tombstone("concept-second")
                )
                anchor_path = (
                    Path(anchor_tmp)
                    / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME
                )
                signed_head = json.loads(
                    head_path.read_text(encoding="utf-8")
                )
                anchor = json.loads(
                    anchor_path.read_text(encoding="utf-8")
                )
                protected_status = (
                    journal.memory_deletion_journal_status(
                        index_dir
                    )
                )
                journal_path.write_bytes(old_journal)
                head_path.write_bytes(old_head)
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

        self.assertEqual(
            signed_head["schema"],
            journal.MEMORY_DELETE_TOMBSTONE_SIGNED_CHAIN_HEAD_SCHEMA,
        )
        self.assertEqual(
            signed_head["authScope"],
            journal.MEMORY_DELETE_TOMBSTONE_AUTH_SCOPE,
        )
        self.assertEqual(anchor["sequence"], 2)
        self.assertTrue(anchor["contentFree"])
        self.assertEqual(
            protected_status["state"],
            "rollback_protected",
        )
        self.assertTrue(protected_status["rollbackProtected"])

    def test_external_anchor_total_delete_requires_explicit_bootstrap(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as memory_tmp,
            tempfile.TemporaryDirectory() as key_tmp,
            tempfile.TemporaryDirectory() as anchor_tmp,
        ):
            key_path = Path(key_tmp) / "integrity.key"
            key_path.write_bytes(b"deletion-integrity-key-material!" * 2)
            index_dir = Path(memory_tmp) / "memory_root" / "memory_index"
            configured = {
                MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
            }
            with patch.dict(
                os.environ,
                {**configured, MEMORY_INTEGRITY_BOOTSTRAP_ENV: "1"},
            ):
                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-total-delete"),
                )
            initialization_path = (
                Path(anchor_tmp)
                / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME
            )
            self.assertTrue(initialization_path.exists())
            for path in (
                index_dir / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME,
                index_dir / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME,
                Path(anchor_tmp)
                / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME,
            ):
                path.unlink()
            with patch.dict(
                os.environ,
                {**configured, MEMORY_INTEGRITY_BOOTSTRAP_ENV: "0"},
            ):
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )
                self.assert_integrity_failure(
                    lambda: journal.memory_deletion_journal_status(
                        index_dir
                    )
                )
            self.assertTrue(initialization_path.exists())

    def test_shared_anchor_allows_a_truly_uninitialized_deletion_ledger(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as memory_tmp,
            tempfile.TemporaryDirectory() as key_tmp,
            tempfile.TemporaryDirectory() as anchor_tmp,
        ):
            key_path = Path(key_tmp) / "integrity.key"
            key_path.write_bytes(
                b"shared-integrity-key-material!" * 2
            )
            anchor_root = Path(anchor_tmp)
            # A sibling journal already using the shared anchor directory
            # must not make this never-created ledger look deleted.
            (anchor_root / "memory-provenance-corrections.json").write_text(
                "{}",
                encoding="utf-8",
            )
            index_dir = Path(memory_tmp) / "memory_root" / "memory_index"
            with patch.dict(
                os.environ,
                {
                    MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                    MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
                    MEMORY_INTEGRITY_BOOTSTRAP_ENV: "0",
                },
            ):
                self.assertEqual(
                    journal.read_memory_deletion_tombstones(index_dir),
                    [],
                )
                status = journal.memory_deletion_journal_status(
                    index_dir
                )
                event = journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-first-strict-init"),
                )

            marker_path = (
                anchor_root
                / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME
            )
            anchor_path = (
                anchor_root
                / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME
            )
            marker_exists = marker_path.exists()
            anchor_exists = anchor_path.exists()

        self.assertEqual(status["state"], "uninitialized")
        self.assertEqual(status["externalAnchorState"], "uninitialized")
        self.assertEqual(event["sequence"], 1)
        self.assertTrue(marker_exists)
        self.assertTrue(anchor_exists)

    def test_existing_authenticated_anchor_migrates_initialization_marker(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as memory_tmp,
            tempfile.TemporaryDirectory() as key_tmp,
            tempfile.TemporaryDirectory() as anchor_tmp,
        ):
            key_path = Path(key_tmp) / "integrity.key"
            key_path.write_bytes(
                b"deletion-integrity-key-material!" * 2
            )
            index_dir = Path(memory_tmp) / "memory_root" / "memory_index"
            configured = {
                MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
            }
            with patch.dict(
                os.environ,
                {**configured, MEMORY_INTEGRITY_BOOTSTRAP_ENV: "1"},
            ):
                committed = journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-marker-migration"),
                )
            marker_path = (
                Path(anchor_tmp)
                / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME
            )
            marker_path.unlink()
            with patch.dict(
                os.environ,
                {**configured, MEMORY_INTEGRITY_BOOTSTRAP_ENV: "0"},
            ):
                rows = journal.read_memory_deletion_tombstones(
                    index_dir
                )
                status = journal.memory_deletion_journal_status(
                    index_dir
                )

            marker = json.loads(marker_path.read_text(encoding="utf-8"))

        self.assertEqual(rows[0]["eventHash"], committed["eventHash"])
        self.assertEqual(status["externalAnchorState"], "verified")
        self.assertTrue(status["rollbackProtected"])
        self.assertEqual(
            marker["schema"],
            journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_SCHEMA,
        )
        self.assertTrue(marker["initialized"])
        self.assertTrue(marker["contentFree"])

    def test_signed_v2_event_rejects_duplicate_keys_and_whitespace(
        self,
    ) -> None:
        for mutation in ("duplicate-key", "whitespace"):
            with self.subTest(mutation=mutation):
                with (
                    tempfile.TemporaryDirectory() as memory_tmp,
                    tempfile.TemporaryDirectory() as key_tmp,
                    tempfile.TemporaryDirectory() as anchor_tmp,
                ):
                    key_path = Path(key_tmp) / "integrity.key"
                    key_path.write_bytes(
                        b"deletion-integrity-key-material!" * 2
                    )
                    index_dir = (
                        Path(memory_tmp) / "memory_root" / "memory_index"
                    )
                    with patch.dict(
                        os.environ,
                        {
                            MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                            MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
                            MEMORY_INTEGRITY_BOOTSTRAP_ENV: "1",
                        },
                    ):
                        event = journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-canonical"),
                        )
                        journal_path = (
                            index_dir
                            / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                        )
                        raw = journal_path.read_bytes()
                        if mutation == "duplicate-key":
                            encoded_note_id = str(
                                event["noteId"]
                            ).encode("ascii")
                            raw = raw.replace(
                                b'"noteId":"' + encoded_note_id + b'"',
                                (
                                    b'"noteId":"PRIVATE-TRANSCRIPT-CANARY",'
                                    b'"noteId":"'
                                    + encoded_note_id
                                    + b'"'
                                ),
                                1,
                            )
                        else:
                            raw = b" " + raw
                        journal_path.write_bytes(raw)
                        self.assert_integrity_failure(
                            lambda: journal.read_memory_deletion_tombstones(
                                index_dir
                            )
                        )

    def test_v2_rows_require_canonical_ids_and_closed_taxonomies(
        self,
    ) -> None:
        mutations = {
            "raw-id": ("noteId", "concept-not-a-native-id"),
            "note-type-alias": ("noteType", "concepts"),
            "source-type-alias": (
                "sourceType",
                "conversation-turn-log",
            ),
        }
        with self.unconfigured_authenticity():
            for name, (field, value) in mutations.items():
                with self.subTest(name=name):
                    with tempfile.TemporaryDirectory() as tmp:
                        index_dir = Path(tmp) / "memory_index"
                        event = journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-canonical-row"),
                        )
                        event[field] = value
                        event["eventHash"] = journal._event_hash(event)
                        journal_path = (
                            index_dir
                            / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                        )
                        journal_path.write_bytes(
                            journal._canonical_json(event) + b"\n"
                        )
                        head_path = (
                            index_dir
                            / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                        )
                        head = json.loads(
                            head_path.read_text(encoding="utf-8")
                        )
                        head["eventHash"] = event["eventHash"]
                        head_path.write_text(
                            json.dumps(head, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        self.assert_integrity_failure(
                            lambda: journal.read_memory_deletion_tombstones(
                                index_dir
                            )
                        )

    def test_pathological_json_is_normalized_to_integrity_failure(
        self,
    ) -> None:
        hostile_rows = (
            b'{"sequence":' + (b"9" * 5000) + b"}\n",
            (b"[" * 2000) + b"0" + (b"]" * 2000) + b"\n",
        )
        with self.unconfigured_authenticity():
            for hostile in hostile_rows:
                with self.subTest(size=len(hostile)):
                    with tempfile.TemporaryDirectory() as tmp:
                        index_dir = Path(tmp) / "memory_index"
                        index_dir.mkdir()
                        (
                            index_dir
                            / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                        ).write_bytes(hostile)
                        self.assert_integrity_failure(
                            lambda: journal.read_memory_deletion_tombstones(
                                index_dir
                            )
                        )

    def test_signed_head_and_anchor_reject_duplicate_keys(self) -> None:
        for target in ("head", "anchor"):
            with self.subTest(target=target):
                with (
                    tempfile.TemporaryDirectory() as memory_tmp,
                    tempfile.TemporaryDirectory() as key_tmp,
                    tempfile.TemporaryDirectory() as anchor_tmp,
                ):
                    key_path = Path(key_tmp) / "integrity.key"
                    key_path.write_bytes(
                        b"deletion-integrity-key-material!" * 2
                    )
                    index_dir = (
                        Path(memory_tmp) / "memory_root" / "memory_index"
                    )
                    with patch.dict(
                        os.environ,
                        {
                            MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                            MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
                            MEMORY_INTEGRITY_BOOTSTRAP_ENV: "1",
                        },
                    ):
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-duplicate-metadata"),
                        )
                        target_path = (
                            index_dir
                            / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                            if target == "head"
                            else Path(anchor_tmp)
                            / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME
                        )
                        raw = target_path.read_text(encoding="utf-8")
                        raw = raw.replace(
                            '"sequence": 1',
                            '"sequence": 999,\n  "sequence": 1',
                            1,
                        )
                        target_path.write_text(raw, encoding="utf-8")
                        self.assert_integrity_failure(
                            lambda: journal.read_memory_deletion_tombstones(
                                index_dir
                            )
                        )

    def test_metadata_requires_canonical_artifact_json(self) -> None:
        with (
            tempfile.TemporaryDirectory() as memory_tmp,
            tempfile.TemporaryDirectory() as key_tmp,
            tempfile.TemporaryDirectory() as anchor_tmp,
            tempfile.TemporaryDirectory() as unsigned_tmp,
        ):
            key_path = Path(key_tmp) / "integrity.key"
            key_path.write_bytes(
                b"deletion-integrity-key-material!" * 2
            )
            index_dir = Path(memory_tmp) / "memory_root" / "memory_index"
            unsigned_index_dir = Path(unsigned_tmp) / "memory_index"
            anchor_root = Path(anchor_tmp)
            configured = {
                MEMORY_INTEGRITY_KEY_FILE_ENV: str(key_path),
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: anchor_tmp,
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "1",
            }
            unconfigured = {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            }
            with patch.dict(
                os.environ,
                configured,
            ):
                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-canonical-metadata"),
                )
            with patch.dict(os.environ, unconfigured):
                journal.append_memory_deletion_tombstone(
                    unsigned_index_dir,
                    self.tombstone("concept-canonical-unsigned-head"),
                )
            artifacts = (
                (
                    "signed-head",
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME,
                    index_dir,
                    configured,
                ),
                (
                    "anchor",
                    anchor_root
                    / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME,
                    index_dir,
                    configured,
                ),
                (
                    "witness",
                    anchor_root
                    / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME,
                    index_dir,
                    configured,
                ),
                (
                    "unsigned-head",
                    unsigned_index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME,
                    unsigned_index_dir,
                    unconfigured,
                ),
            )
            for target, target_path, read_index_dir, environment in artifacts:
                canonical = target_path.read_text(encoding="utf-8")
                payload = json.loads(canonical)
                mutations = {
                    "whitespace": " " + canonical,
                    "key-order": json.dumps(
                        {
                            key: payload[key]
                            for key in reversed(payload)
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                }
                for mutation, raw in mutations.items():
                    with (
                        self.subTest(target=target, mutation=mutation),
                        patch.dict(os.environ, environment),
                    ):
                        try:
                            target_path.write_text(raw, encoding="utf-8")
                            self.assert_integrity_failure(
                                lambda: journal.read_memory_deletion_tombstones(
                                    read_index_dir
                                )
                            )
                        finally:
                            target_path.write_text(
                                canonical,
                                encoding="utf-8",
                            )

    def test_competing_process_writer_fails_closed(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                payload = self.tombstone("concept-child")
                script = (
                    "import json,sys;"
                    f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                    "from pathlib import Path;"
                    "from evelyn_core import memory_deletion_journal as j;"
                    f"p={payload!r};"
                    "\ntry:\n"
                    f" j.append_memory_deletion_tombstone(Path({str(index_dir)!r}),p)\n"
                    " print(json.dumps({'ok':True}))\n"
                    "except Exception as exc:\n"
                    " print(json.dumps({'ok':False,'type':type(exc).__name__,'error':str(exc)}))"
                )
                with journal._writer_guard(index_dir):
                    child = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=str(REPO_ROOT),
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=True,
                    )
                result = json.loads(child.stdout)

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["type"],
            "MemoryDeletionJournalBusyError",
        )
        self.assertEqual(
            result["error"],
            journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        )

    def test_shared_readers_coexist_across_processes(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                script = (
                    "import json,sys;"
                    f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                    "from pathlib import Path;"
                    "from evelyn_core import memory_deletion_journal as j;"
                    f"p=Path({str(index_dir)!r});"
                    "\ntry:\n"
                    " with j.memory_deletion_journal_read_guard(p) as position:\n"
                    "  print(json.dumps({'ok':True,'sequence':position.sequence}))\n"
                    "except Exception as exc:\n"
                    " print(json.dumps({'ok':False,'type':type(exc).__name__,'error':str(exc)}))"
                )
                with journal.memory_deletion_journal_read_guard(index_dir):
                    child = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=str(REPO_ROOT),
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=True,
                    )
                result = json.loads(child.stdout)

        self.assertEqual(result, {"ok": True, "sequence": 0})

    def test_shared_reader_blocks_competing_process_writer(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                payload = self.tombstone("concept-child-reader")
                script = (
                    "import json,sys;"
                    f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                    "from pathlib import Path;"
                    "from evelyn_core import memory_deletion_journal as j;"
                    f"p={payload!r};"
                    "\ntry:\n"
                    f" j.append_memory_deletion_tombstone(Path({str(index_dir)!r}),p)\n"
                    " print(json.dumps({'ok':True}))\n"
                    "except Exception as exc:\n"
                    " print(json.dumps({'ok':False,'type':type(exc).__name__,'error':str(exc)}))"
                )
                with journal.memory_deletion_journal_read_guard(index_dir):
                    child = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=str(REPO_ROOT),
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=True,
                    )
                result = json.loads(child.stdout)
                rows = journal.read_memory_deletion_tombstones(index_dir)

        self.assertEqual(
            result,
            {
                "ok": False,
                "type": "MemoryDeletionJournalBusyError",
                "error": journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
            },
        )
        self.assertEqual(rows, [])

    def test_writer_blocks_competing_process_reader(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                script = (
                    "import json,sys;"
                    f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                    "from pathlib import Path;"
                    "from evelyn_core import memory_deletion_journal as j;"
                    f"p=Path({str(index_dir)!r});"
                    "\ntry:\n"
                    " with j.memory_deletion_journal_read_guard(p):\n"
                    "  print(json.dumps({'ok':True}))\n"
                    "except Exception as exc:\n"
                    " print(json.dumps({'ok':False,'type':type(exc).__name__,'error':str(exc)}))"
                )
                with journal._writer_guard(index_dir):
                    child = subprocess.run(
                        [sys.executable, "-c", script],
                        cwd=str(REPO_ROOT),
                        text=True,
                        capture_output=True,
                        timeout=10,
                        check=True,
                    )
                result = json.loads(child.stdout)

        self.assertEqual(
            result,
            {
                "ok": False,
                "type": "MemoryDeletionJournalBusyError",
                "error": journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
            },
        )

    def test_public_read_guard_linearizes_against_append(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"

                def append_from_other_thread() -> tuple[str, str]:
                    try:
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-private-canary"),
                        )
                    except Exception as exc:
                        return type(exc).__name__, str(exc)
                    return "ok", ""

                with journal.memory_deletion_journal_read_guard(index_dir):
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        result = executor.submit(
                            append_from_other_thread
                        ).result(timeout=2)
                    self.assertEqual(
                        journal.read_memory_deletion_tombstones(index_dir),
                        [],
                    )
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                raw = (
                    journal_path.read_text(encoding="utf-8")
                    if journal_path.exists()
                    else ""
                )

        self.assertEqual(
            result,
            (
                "MemoryDeletionJournalBusyError",
                journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
            ),
        )
        self.assertNotIn("concept-private-canary", raw)

    def test_exposure_guard_rejects_a_valid_position_change(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ) as raised:
                    with journal.memory_deletion_journal_guard(index_dir):
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-out-of-band"),
                        )
                rows = journal.read_memory_deletion_tombstones(index_dir)

        self.assertEqual(
            str(raised.exception),
            journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )
        self.assertEqual(
            [row["noteId"] for row in rows],
            [self.ledger_id("concept-out-of-band")],
        )

    def test_position_can_be_reused_only_for_its_unchanged_ledger(
        self,
    ) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                expected = journal.memory_deletion_journal_position(
                    index_dir
                )
                with journal.memory_deletion_journal_guard(
                    index_dir,
                    expected_position=expected,
                ) as current:
                    self.assertEqual(current, expected)
                self.assertEqual(
                    journal.memory_deletion_journal_position(index_dir),
                    expected,
                )

                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-position-change"),
                )
                entered = False
                with self.assertRaises(
                    journal.MemoryDeletionJournalIntegrityError
                ):
                    with journal.memory_deletion_journal_guard(
                        index_dir,
                        expected_position=expected,
                    ):
                        entered = True
                self.assertFalse(entered)

    def test_purge_receipt_is_content_free_and_bound_to_current_generation(
        self,
    ) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("PRIVATE purge receipt source"),
                )
                receipt = journal.build_memory_deletion_purge_receipt(
                    index_dir,
                    deletion_generation=17,
                    purged_count=2,
                )

                self.assertEqual(
                    receipt["schema"],
                    journal.MEMORY_DELETION_PURGE_RECEIPT_SCHEMA,
                )
                self.assertEqual(receipt["journalGeneration"], 1)
                self.assertEqual(
                    journal.memory_deletion_journal_position(
                        index_dir
                    ).deletion_generation,
                    1,
                )
                self.assertTrue(receipt["complete"])
                self.assertNotIn(
                    "PRIVATE purge receipt source",
                    json.dumps(receipt, ensure_ascii=False),
                )
                self.assertEqual(
                    journal.validate_memory_deletion_purge_receipt(
                        index_dir,
                        receipt,
                        expected_deletion_generation=17,
                    ),
                    receipt,
                )

                journal.append_memory_deletion_tombstone(
                    index_dir,
                    self.tombstone("concept-receipt-stale"),
                )
                self.assert_integrity_failure(
                    lambda: journal.validate_memory_deletion_purge_receipt(
                        index_dir,
                        receipt,
                        expected_deletion_generation=17,
                    )
                )

    def test_incomplete_or_tampered_purge_receipt_fails_closed(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                receipt = journal.build_memory_deletion_purge_receipt(
                    index_dir,
                    deletion_generation=4,
                    purged_count=1,
                    pending_count=1,
                )

                self.assertFalse(receipt["complete"])
                self.assertEqual(receipt["status"], "cleanup_pending")
                self.assert_integrity_failure(
                    lambda: journal.validate_memory_deletion_purge_receipt(
                        index_dir,
                        receipt,
                    )
                )
                self.assertEqual(
                    journal.validate_memory_deletion_purge_receipt(
                        index_dir,
                        receipt,
                        require_complete=False,
                    ),
                    receipt,
                )
                tampered = dict(receipt)
                tampered["purgedCount"] = 2
                self.assert_integrity_failure(
                    lambda: journal.validate_memory_deletion_purge_receipt(
                        index_dir,
                        tampered,
                        require_complete=False,
                    )
                )

    def test_position_rejects_wrong_root_and_malformed_expected(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as first_tmp:
                with tempfile.TemporaryDirectory() as second_tmp:
                    first = Path(first_tmp) / "memory_index"
                    second = Path(second_tmp) / "memory_index"
                    expected = (
                        journal.memory_deletion_journal_position(first)
                    )
                    self.assertNotEqual(
                        expected.root_digest,
                        journal.memory_deletion_journal_position(
                            second
                        ).root_digest,
                    )
                    self.assert_integrity_failure(
                        lambda: journal.memory_deletion_journal_guard(
                            second,
                            expected_position=expected,
                        ).__enter__()
                    )
                    self.assert_integrity_failure(
                        lambda: journal.memory_deletion_journal_guard(
                            first,
                            expected_position={
                                "schema": (
                                    journal.MEMORY_DELETION_POSITION_SCHEMA
                                )
                            },
                        ).__enter__()
                    )

    def test_async_tasks_do_not_share_a_reentrant_writer_owner(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"

                async def exercise() -> tuple[str, str]:
                    entered = asyncio.Event()
                    release = asyncio.Event()

                    async def holder() -> None:
                        with journal.memory_deletion_journal_guard(index_dir):
                            entered.set()
                            await release.wait()

                    task = asyncio.create_task(holder())
                    await entered.wait()
                    try:
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-other-task"),
                        )
                    except Exception as exc:
                        result = type(exc).__name__, str(exc)
                    else:
                        result = "ok", ""
                    release.set()
                    await task
                    return result

                result = asyncio.run(exercise())

        self.assertEqual(
            result,
            (
                "MemoryDeletionJournalBusyError",
                journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
            ),
        )

    def test_async_readers_keep_independent_ownership(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"

                async def exercise() -> tuple[type[BaseException], type[BaseException]]:
                    entered = [asyncio.Event(), asyncio.Event()]
                    release = [asyncio.Event(), asyncio.Event()]

                    async def holder(index: int) -> None:
                        with journal.memory_deletion_journal_read_guard(
                            index_dir
                        ):
                            entered[index].set()
                            await release[index].wait()

                    tasks = [
                        asyncio.create_task(holder(0)),
                        asyncio.create_task(holder(1)),
                    ]
                    await entered[0].wait()
                    await entered[1].wait()
                    with self.assertRaises(
                        journal.MemoryDeletionJournalBusyError
                    ) as both_busy:
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-both-readers"),
                        )
                    release[0].set()
                    await tasks[0]
                    with self.assertRaises(
                        journal.MemoryDeletionJournalBusyError
                    ) as one_busy:
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-one-reader"),
                        )
                    release[1].set()
                    await tasks[1]
                    journal.append_memory_deletion_tombstone(
                        index_dir,
                        self.tombstone("concept-readers-done"),
                    )
                    return type(both_busy.exception), type(one_busy.exception)

                result = asyncio.run(exercise())
                rows = journal.read_memory_deletion_tombstones(index_dir)

        self.assertEqual(
            result,
            (
                journal.MemoryDeletionJournalBusyError,
                journal.MemoryDeletionJournalBusyError,
            ),
        )
        self.assertEqual(
            [row["noteId"] for row in rows],
            [self.ledger_id("concept-readers-done")],
        )

    def test_read_reentrancy_and_upgrade_contract(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                with journal.memory_deletion_journal_read_guard(
                    index_dir
                ) as outer:
                    with journal.memory_deletion_journal_read_guard(
                        index_dir,
                        expected_position=outer,
                    ) as nested:
                        self.assertEqual(nested, outer)
                    with self.assertRaises(
                        journal.MemoryDeletionJournalBusyError
                    ):
                        journal.append_memory_deletion_tombstone(
                            index_dir,
                            self.tombstone("concept-upgrade"),
                        )
                with journal.memory_deletion_journal_guard(index_dir):
                    with journal.memory_deletion_journal_read_guard(
                        index_dir
                    ):
                        pass

    def test_guard_body_oserror_is_not_rewritten_by_lock_layer(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                guards = (
                    journal.memory_deletion_journal_guard,
                    journal.memory_deletion_journal_read_guard,
                )
                for guard in guards:
                    for reentrant in (False, True):
                        with self.subTest(
                            guard=guard.__name__,
                            reentrant=reentrant,
                        ):
                            sentinel = OSError("caller_body_failure")
                            try:
                                with guard(index_dir):
                                    if reentrant:
                                        with guard(index_dir):
                                            raise sentinel
                                    raise sentinel
                            except OSError as exc:
                                self.assertIs(exc, sentinel)
                            else:
                                self.fail("caller body OSError was swallowed")

    def test_busy_exception_never_echoes_details(self) -> None:
        exception = journal.MemoryDeletionJournalBusyError(
            "private transcript canary",
            path="C:/private/path",
        )
        self.assertEqual(
            str(exception),
            journal.MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        )

    def test_exception_and_input_rejection_never_echo_details(self) -> None:
        exception = journal.MemoryDeletionJournalIntegrityError(
            "private transcript canary",
            path="C:/private/path",
        )
        self.assertEqual(
            str(exception),
            journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                payload = {
                    **self.tombstone(),
                    "transcript": "private transcript canary",
                }
                self.assert_integrity_failure(
                    lambda: journal.append_memory_deletion_tombstone(
                        Path(tmp) / "memory_index",
                        payload,
                    )
                )

            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                private_id = "PRIVATE transcript canary full sentence"
                payload = {
                    **self.tombstone(),
                    "noteId": private_id,
                }
                event = journal.append_memory_deletion_tombstone(
                    index_dir,
                    payload,
                )
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                raw = (
                    journal_path.read_text(encoding="utf-8")
                    if journal_path.exists()
                    else ""
                )
                self.assertEqual(
                    event["noteId"],
                    self.ledger_id(private_id),
                )
                self.assertNotIn(private_id, raw)

            for invalid_id in (
                "",
                " leading",
                "trailing ",
                "line\nbreak",
                "x" * 513,
                7,
            ):
                with self.subTest(invalid_id=invalid_id):
                    with tempfile.TemporaryDirectory() as tmp:
                        self.assert_integrity_failure(
                            lambda invalid_id=invalid_id: (
                                journal.append_memory_deletion_tombstone(
                                    Path(tmp) / "memory_index",
                                    {
                                        **self.tombstone(),
                                        "noteId": invalid_id,
                                    },
                                )
                            )
                        )

    def test_index_symlink_and_oversized_artifacts_are_rejected(self) -> None:
        with self.unconfigured_authenticity():
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                target = base / "target"
                target.mkdir()
                linked = base / "linked-index"
                try:
                    linked.symlink_to(target, target_is_directory=True)
                except OSError:
                    linked = None
                if linked is not None:
                    self.assert_integrity_failure(
                        lambda: journal.read_memory_deletion_tombstones(
                            linked
                        )
                    )

            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                index_dir.mkdir()
                journal_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                )
                with journal_path.open("wb") as handle:
                    handle.truncate(
                        journal.MEMORY_DELETE_TOMBSTONE_MAX_JOURNAL_BYTES
                        + 1
                    )
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )

            with tempfile.TemporaryDirectory() as tmp:
                index_dir = Path(tmp) / "memory_index"
                index_dir.mkdir()
                (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
                ).write_bytes(b"")
                head_path = (
                    index_dir
                    / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
                )
                with head_path.open("wb") as handle:
                    handle.truncate(
                        journal.MEMORY_DELETE_TOMBSTONE_MAX_HEAD_BYTES + 1
                    )
                self.assert_integrity_failure(
                    lambda: journal.read_memory_deletion_tombstones(
                        index_dir
                    )
                )


if __name__ == "__main__":
    unittest.main()
