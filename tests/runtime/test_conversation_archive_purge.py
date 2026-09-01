from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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

from evelyn_core.conversation_archive import (  # noqa: E402
    ArchiveStaleEvent,
    ArchiveUnavailableError,
    ConversationArchive,
    archive_lineage_handle,
)
from evelyn_core.conversation_archive_memory_purge import (  # noqa: E402
    MEMORY_BUNDLE_PURGE_SINKS,
    memory_bundle_purge_owners,
)
from evelyn_core.conversation_archive_purge import (  # noqa: E402
    ConversationArchivePurgeCoordinator,
    ConversationArchivePurgeError,
    DeletionLateCommitRejected,
    LocalPurgeOwner,
    PurgePass,
    voice_debug_audio_purge_owner,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_position,
)
from evelyn_core.memory_deletion_outbound import (  # noqa: E402
    memory_deletion_late_commit_guard,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.memory_vault import (  # noqa: E402
    append_turn_rows_to_memory_vault,
    memory_index_db_path,
)


BASE = datetime(2026, 8, 28, 1, 2, 3, tzinfo=timezone.utc)
CANARY = "SYNTHETIC_PURGE_CANARY_7cb537"


class ConversationArchivePurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.primary = self.root / "primary" / "conversation.sqlite3"
        self.replica = self.root / "replica" / "conversation.sqlite3"
        self.anchor = self.root / "anchor" / "head.json"
        self.memory_index = self.root / "memory" / "memory_index"
        self.archive: ConversationArchive | None = None
        self.environment = patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        if self.archive is not None:
            self.archive.close()
        self.environment.stop()
        self.temporary.cleanup()

    def open_archive(
        self,
        *,
        required_sinks: tuple[str, ...],
        coordinator: ConversationArchivePurgeCoordinator,
    ) -> ConversationArchive:
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-purge-test-key-32-bytes-minimum",
            required_purge_sinks=required_sinks,
            purge_freeze=coordinator.freeze,
        ).open()
        return self.archive

    @staticmethod
    def append_target(archive: ConversationArchive) -> None:
        archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body=CANARY,
            actor_external_id="synthetic-user",
            owner_name="Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE,
            ended_at=BASE,
            idempotency_key="synthetic-purge-record-key",
            record_id="synthetic-purge-record",
            now=BASE,
        )

    @staticmethod
    def delete_target(archive: ConversationArchive):
        preview = archive.preview_user_deletion(
            actor_external_id="synthetic-user",
            request_guild_id="42",
            now=BASE + timedelta(seconds=1),
        )
        return archive.apply_user_deletion(
            preview_id=preview.preview_id,
            actor_external_id="synthetic-user",
            now=BASE + timedelta(seconds=2),
        )

    @staticmethod
    def append_memory_target(
        archive: ConversationArchive,
        *,
        target_turn: str,
        record_id: str,
    ) -> None:
        archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body=CANARY,
            actor_external_id="synthetic-user",
            owner_name="Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE,
            ended_at=BASE,
            lineage={
                "turn": (target_turn,),
                "memory_evidence": (f"turn:{target_turn}:user",),
            },
            idempotency_key=f"{record_id}-key",
            record_id=record_id,
            now=BASE,
        )

    def test_real_voice_and_journal_purge_completes_and_compacts_scope(
        self,
    ) -> None:
        debug_root = self.root / "debug_audio"
        guild_dir = debug_root / "42"
        guild_dir.mkdir(parents=True)
        (guild_dir / "bundle.pcm").write_bytes(CANARY.encode("utf-8"))
        (guild_dir / "bundle.json").write_text(
            json.dumps(
                {"turn_id": "synthetic-turn", "final_text": CANARY}
            ),
            encoding="utf-8",
        )
        voice_owner = voice_debug_audio_purge_owner(
            debug_root,
            resolve_turn_ids=lambda work_order: (
                ("synthetic-turn",)
                if "synthetic-purge-record"
                in work_order.owned_record_ids
                else None
            ),
        )
        coordinator = ConversationArchivePurgeCoordinator(
            owners=(voice_owner,),
            memory_deletion_index_dir=self.memory_index,
        )
        archive = self.open_archive(
            required_sinks=(
                "memory_deletion_journal",
                "voice_debug_audio",
            ),
            coordinator=coordinator,
        )
        self.append_target(archive)

        result = self.delete_target(archive)
        self.assertEqual(result.status, "local_cleanup_pending")
        work_orders = archive.pending_purge_work_orders()
        self.assertEqual(len(work_orders), 1)
        self.assertEqual(
            work_orders[0].owned_record_ids,
            ("synthetic-purge-record",),
        )

        runs = coordinator.purge_pending(archive)

        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].archive_completed)
        self.assertEqual(runs[0].state, "local_fully_purged")
        self.assertEqual(
            {status.state for status in runs[0].sinks}, {"purged"}
        )
        self.assertEqual(archive.pending_purge_work_orders(), ())
        self.assertTrue(archive.health().writes_allowed)
        with closing(sqlite3.connect(self.primary)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT purge_scope_json FROM deletion_audits"
                ).fetchone()[0],
                "{}",
            )
        for path in (self.primary, self.replica):
            self.assertNotIn(CANARY.encode("utf-8"), path.read_bytes())
        self.assertFalse(any(guild_dir.iterdir()))
        raw_run = json.dumps(
            {
                "requestId": runs[0].request_id,
                "state": runs[0].state,
                "receipts": list(runs[0].receipts),
                "sinks": [status.__dict__ for status in runs[0].sinks],
            },
            ensure_ascii=False,
        )
        self.assertNotIn(CANARY, raw_run)

    def test_missing_owner_stays_manual_and_restart_fences_only_target(
        self,
    ) -> None:
        coordinator = ConversationArchivePurgeCoordinator(
            memory_deletion_index_dir=self.memory_index
        )
        archive = self.open_archive(
            required_sinks=("bot_memory", "memory_deletion_journal"),
            coordinator=coordinator,
        )
        self.append_target(archive)
        self.delete_target(archive)

        run = coordinator.purge_pending(archive)[0]

        self.assertFalse(run.archive_completed)
        self.assertEqual(run.state, "manual_review")
        states = {status.sink: status.state for status in run.sinks}
        self.assertEqual(states["memory_deletion_journal"], "purged")
        self.assertEqual(states["bot_memory"], "manual_review")
        self.assertEqual(
            {receipt["sink"] for receipt in run.receipts},
            {"memory_deletion_journal"},
        )

        archive.close()
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-purge-test-key-32-bytes-minimum",
            required_purge_sinks=(
                "bot_memory",
                "memory_deletion_journal",
            ),
            purge_freeze=coordinator.freeze,
        ).open()
        self.assertEqual(
            self.archive.health().status, "local_cleanup_pending"
        )
        self.assertTrue(self.archive.health().writes_allowed)
        with self.assertRaises(ArchiveUnavailableError) as raised:
            self.archive.append_record(
                mode="discord_shared",
                surface="discord",
                record_type="user_text",
                body="late target row",
                actor_external_id="synthetic-user",
                owner_name="Synthetic User",
                guild_id="42",
                channel_id="7",
                started_at=BASE,
                ended_at=BASE,
                idempotency_key="blocked-target-write",
                now=BASE,
            )
        self.assertEqual(
            raised.exception.code, "archive_target_cleanup_pending"
        )
        unrelated = self.archive.append_record(
            mode="local_private",
            surface="local",
            record_type="user_text",
            body="unrelated synthetic row",
            actor_external_id="another-user",
            owner_name="Another User",
            started_at=BASE,
            ended_at=BASE,
            idempotency_key="allowed-unrelated-write",
            now=BASE,
        )
        self.assertEqual(unrelated.body, "unrelated synthetic row")

    def test_negative_recall_failure_never_creates_success_receipt(self) -> None:
        owner = LocalPurgeOwner(
            sink="bot_memory",
            purge=lambda _work_order: PurgePass(removed_count=1),
            negative_recall=lambda _work_order: PurgePass(
                remaining_copies=1
            ),
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=(owner,))
        archive = self.open_archive(
            required_sinks=("bot_memory",), coordinator=coordinator
        )
        self.append_target(archive)
        self.delete_target(archive)

        run = coordinator.purge_pending(archive)[0]

        self.assertFalse(run.archive_completed)
        self.assertEqual(run.state, "local_cleanup_pending")
        self.assertEqual(run.receipts, ())
        self.assertEqual(run.sinks[0].remaining_copies, 1)
        self.assertIsNotNone(archive.deletion_purge_work_order(
            request_id=run.request_id
        ))

    def test_voice_owner_without_exact_resolver_refuses_legacy_bundles(
        self,
    ) -> None:
        debug_root = self.root / "legacy_debug_audio"
        guild_dir = debug_root / "42"
        guild_dir.mkdir(parents=True)
        metadata = guild_dir / "legacy.json"
        metadata.write_text(
            json.dumps({"turn_id": "unattributed-legacy-turn"}),
            encoding="utf-8",
        )
        owner = voice_debug_audio_purge_owner(debug_root)
        coordinator = ConversationArchivePurgeCoordinator(owners=(owner,))
        archive = self.open_archive(
            required_sinks=("voice_debug_audio",),
            coordinator=coordinator,
        )
        self.append_target(archive)
        self.delete_target(archive)

        run = coordinator.purge_pending(archive)[0]

        self.assertFalse(run.archive_completed)
        self.assertEqual(run.sinks[0].state, "manual_review")
        self.assertTrue(metadata.exists())

    def test_voice_owner_without_root_stays_manual_review(self) -> None:
        owner = voice_debug_audio_purge_owner(None)
        coordinator = ConversationArchivePurgeCoordinator(owners=(owner,))
        archive = self.open_archive(
            required_sinks=("voice_debug_audio",),
            coordinator=coordinator,
        )
        self.append_target(archive)
        self.delete_target(archive)

        run = coordinator.purge_pending(archive)[0]

        self.assertFalse(run.archive_completed)
        self.assertEqual(run.sinks[0].state, "manual_review")
        self.assertEqual(run.receipts, ())
        self.assertEqual(len(archive.pending_purge_work_orders()), 1)

    def test_freeze_invalidates_exact_and_memory_late_commit_fences(
        self,
    ) -> None:
        coordinator = ConversationArchivePurgeCoordinator(
            memory_deletion_index_dir=self.memory_index
        )
        archive = self.open_archive(
            required_sinks=("memory_deletion_journal",),
            coordinator=coordinator,
        )
        self.append_target(archive)
        exact_fence = coordinator.capture_late_commit_fence(
            record_ids=("synthetic-purge-record",)
        )
        memory_fence = memory_deletion_journal_position(self.memory_index)

        self.delete_target(archive)

        with self.assertRaises(DeletionLateCommitRejected):
            coordinator.assert_late_commit_current(exact_fence)
        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            with memory_deletion_late_commit_guard(
                expected_position=memory_fence,
                index_dir=self.memory_index,
            ):
                self.fail("stale memory commit reached its sink")
        run = coordinator.purge_pending(archive)[0]
        self.assertTrue(run.archive_completed)
        with self.assertRaises(DeletionLateCommitRejected):
            coordinator.capture_late_commit_fence(
                record_ids=("synthetic-purge-record",)
            )
        replacement = archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body="new post-deletion synthetic row",
            actor_external_id="synthetic-user",
            owner_name="Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE + timedelta(seconds=3),
            ended_at=BASE + timedelta(seconds=3),
            idempotency_key="post-deletion-new-record",
            record_id="post-deletion-new-record",
            now=BASE + timedelta(seconds=3),
        )
        self.assertEqual(replacement.record_id, "post-deletion-new-record")

    def test_work_order_generation_or_target_cannot_be_substituted(self) -> None:
        coordinator = ConversationArchivePurgeCoordinator()
        archive = self.open_archive(
            required_sinks=("bot_memory",), coordinator=coordinator
        )
        self.append_target(archive)
        self.delete_target(archive)
        work_order = archive.pending_purge_work_orders()[0]

        with self.assertRaises(ConversationArchivePurgeError):
            coordinator.purge_work_order(
                archive,
                replace(
                    work_order,
                    deletion_generation=work_order.deletion_generation + 1,
                ),
            )

    def test_failed_freeze_rolls_back_before_logical_deletion(self) -> None:
        def fail_freeze(_work_order) -> None:
            raise RuntimeError("synthetic failure")

        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-purge-test-key-32-bytes-minimum",
            required_purge_sinks=("bot_memory",),
            purge_freeze=fail_freeze,
        ).open()
        self.append_target(self.archive)
        preview = self.archive.preview_user_deletion(
            actor_external_id="synthetic-user",
            request_guild_id="42",
            now=BASE + timedelta(seconds=1),
        )

        with self.assertRaises(ArchiveUnavailableError) as raised:
            self.archive.apply_user_deletion(
                preview_id=preview.preview_id,
                actor_external_id="synthetic-user",
                now=BASE + timedelta(seconds=2),
            )

        self.assertEqual(raised.exception.code, "archive_purge_freeze_failed")
        visible = self.archive.read_self(
            actor_external_id="synthetic-user", guild_id="42"
        )
        self.assertEqual([record.body for record in visible], [CANARY])
        self.assertEqual(self.archive.pending_purge_work_orders(), ())

    def test_retention_freezes_every_affected_principal(self) -> None:
        owner = LocalPurgeOwner(
            sink="bot_memory",
            purge=lambda _work_order: PurgePass(),
            negative_recall=lambda _work_order: PurgePass(),
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=(owner,))
        archive = self.open_archive(
            required_sinks=("bot_memory",), coordinator=coordinator
        )
        self.append_target(archive)
        archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body="second synthetic row",
            actor_external_id="second-synthetic-user",
            owner_name="Second Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE,
            ended_at=BASE,
            idempotency_key="second-synthetic-record-key",
            record_id="second-synthetic-record",
            now=BASE,
        )
        principal_ids = tuple(
            sorted(
                str(record.owner_principal_id)
                for record in archive.read_admin(authorized=True)
                if record.owner_principal_id is not None
            )
        )
        old_fence = coordinator.capture_late_commit_fence(
            principal_ids=principal_ids
        )

        result = archive.prune_expired(
            now=BASE + timedelta(days=31), batch_size=10
        )

        self.assertIsNotNone(result)
        work_order = archive.pending_purge_work_orders()[0]
        self.assertEqual(work_order.principal_ids, principal_ids)
        with self.assertRaises(DeletionLateCommitRejected):
            coordinator.assert_late_commit_current(old_fence)
        self.assertTrue(
            coordinator.purge_work_order(archive, work_order).archive_completed
        )

    def test_failed_final_replica_copy_restores_exact_purge_scope(self) -> None:
        copies = {"synthetic-purge-record"}

        def purge(work_order) -> PurgePass:
            targets = set(work_order.owned_record_ids)
            removed = len(copies & targets)
            copies.difference_update(targets)
            return PurgePass(
                removed_count=removed,
                remaining_copies=len(copies & targets),
            )

        owner = LocalPurgeOwner(
            sink="bot_memory",
            purge=purge,
            negative_recall=lambda work_order: PurgePass(
                remaining_copies=len(
                    copies & set(work_order.owned_record_ids)
                )
            ),
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=(owner,))
        archive = self.open_archive(
            required_sinks=("bot_memory",), coordinator=coordinator
        )
        self.append_target(archive)
        deletion = self.delete_target(archive)
        original = archive.deletion_purge_work_order(
            request_id=deletion.request_id
        )
        self.assertIsNotNone(original)

        with patch.object(
            archive,
            "_replicate_after_commit",
            side_effect=(True, False),
        ):
            first = coordinator.purge_pending(archive)[0]

        self.assertFalse(first.archive_completed)
        restored = archive.deletion_purge_work_order(
            request_id=deletion.request_id
        )
        self.assertEqual(restored, original)
        archive.reconcile_replica(now=BASE + timedelta(seconds=3))
        self.assertTrue(coordinator.purge_pending(archive)[0].archive_completed)

    def test_completed_deletion_rejects_retired_record_after_restart(self) -> None:
        owner = LocalPurgeOwner(
            sink="bot_memory",
            purge=lambda _work_order: PurgePass(),
            negative_recall=lambda _work_order: PurgePass(),
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=(owner,))
        archive = self.open_archive(
            required_sinks=("bot_memory",), coordinator=coordinator
        )
        self.append_target(archive)
        self.delete_target(archive)
        self.assertTrue(coordinator.purge_pending(archive)[0].archive_completed)
        archive.close()
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=self.anchor,
            integrity_key=b"archive-purge-test-key-32-bytes-minimum",
            required_purge_sinks=("bot_memory",),
            purge_freeze=ConversationArchivePurgeCoordinator(
                owners=(owner,)
            ).freeze,
        ).open()

        with self.assertRaises(ArchiveStaleEvent):
            self.append_target(self.archive)

    def test_typed_lineage_is_opaque_at_rest_and_survives_work_order(self) -> None:
        coordinator = ConversationArchivePurgeCoordinator()
        archive = self.open_archive(
            required_sinks=("bot_memory",), coordinator=coordinator
        )
        raw_turn = "turn-lineage-canary-01"
        archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body=CANARY,
            actor_external_id="synthetic-user",
            owner_name="Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE,
            ended_at=BASE,
            lineage={
                "turn": (raw_turn,),
                "memory_evidence": (f"turn:{raw_turn}:user",),
            },
            idempotency_key="typed-lineage-record-key",
            record_id="typed-lineage-record",
            now=BASE,
        )
        with closing(sqlite3.connect(self.primary)) as connection:
            stored = str(
                connection.execute(
                    "SELECT lineage_json FROM records WHERE record_id = ?",
                    ("typed-lineage-record",),
                ).fetchone()[0]
            )
        self.assertNotIn(raw_turn, stored)
        self.assertIn(
            archive_lineage_handle(
                b"archive-purge-test-key-32-bytes-minimum",
                "turn",
                raw_turn,
            ),
            stored,
        )

        deletion = self.delete_target(archive)
        work_order = archive.deletion_purge_work_order(
            request_id=deletion.request_id
        )

        self.assertIsNotNone(work_order)
        assert work_order is not None
        self.assertTrue(work_order.lineage_complete)
        self.assertIn(
            (
                "turn",
                archive_lineage_handle(
                    b"archive-purge-test-key-32-bytes-minimum",
                    "turn",
                    raw_turn,
                ),
            ),
            work_order.lineage_handles,
        )

    def test_memory_bundle_owner_exactly_purges_and_rebuilds_derived_state(
        self,
    ) -> None:
        lineage_key = b"archive-purge-test-key-32-bytes-minimum"
        memory_root = self.root / "memory"
        scope = memory_root / "guild_42" / "person_user_7"
        (scope / "vault" / "raw").mkdir(parents=True)
        target_turn = "turn-target-01"
        survivor_turn = "turn-survivor-02"

        def row(turn_id: str, text: str) -> dict[str, object]:
            return {
                "role": "user",
                "speaker": "Synthetic User",
                "source": "chat",
                "text": text,
                "saved_at": 1,
                "evidence_id": f"turn:{turn_id}:user",
                "source_turn_id": turn_id,
                "evidence_kind": "conversation_turn",
            }

        raw_rows = [row(target_turn, CANARY), row(survivor_turn, "survivor")]
        encoded_rows = "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in raw_rows
        )
        (scope / "raw_transcript.jsonl").write_text(
            encoded_rows, encoding="utf-8"
        )
        (scope / "vault" / "raw" / "2026-08-28.jsonl").write_text(
            encoded_rows, encoding="utf-8"
        )
        (scope / "open_questions.jsonl").write_text(
            json.dumps(
                {
                    "text": CANARY,
                    "evidence_id": "memory:question:target",
                    "evidence_kind": "derived_question",
                    "source_evidence_ids": [f"turn:{target_turn}:user"],
                    "source_turn_ids": [target_turn],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (scope / "rolling_summary.txt").write_text(CANARY, encoding="utf-8")
        (scope / "rolling_summary.provenance.json").write_text(
            json.dumps(
                {
                    "evidence_id": "memory:summary:target",
                    "source_evidence_ids": [f"turn:{target_turn}:user"],
                    "source_turn_ids": [target_turn],
                }
            ),
            encoding="utf-8",
        )
        (scope / "cognitive_state.json").write_text(
            json.dumps(
                {"action": "answer", "source_turn_ids": [target_turn]}
            ),
            encoding="utf-8",
        )
        append_turn_rows_to_memory_vault(
            42,
            [raw_rows[0]],
            scope_type="person",
            scope_key="user_7",
            root=memory_root,
        )
        append_turn_rows_to_memory_vault(
            42,
            [raw_rows[1]],
            scope_type="person",
            scope_key="user_7",
            root=memory_root,
        )

        owners = memory_bundle_purge_owners(
            memory_root=memory_root,
            lineage_key=lineage_key,
            process_tool_cache_purge=lambda _work_order: PurgePass(),
            writer_fence_current=lambda _work_order: True,
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=owners)
        archive = self.open_archive(
            required_sinks=MEMORY_BUNDLE_PURGE_SINKS,
            coordinator=coordinator,
        )
        archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body=CANARY,
            actor_external_id="synthetic-user",
            owner_name="Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE,
            ended_at=BASE,
            lineage={
                "turn": (target_turn,),
                "memory_evidence": (f"turn:{target_turn}:user",),
            },
            idempotency_key="memory-bundle-record-key",
            record_id="memory-bundle-record",
            now=BASE,
        )

        deletion = self.delete_target(archive)
        result = coordinator.purge_pending(archive)[0]

        self.assertEqual(result.request_id, deletion.request_id)
        self.assertTrue(result.archive_completed)
        self.assertTrue(all(status.state == "purged" for status in result.sinks))
        remaining = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in memory_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".json", ".jsonl", ".md", ".txt"}
        )
        self.assertNotIn(CANARY, remaining)
        self.assertNotIn(target_turn, remaining)
        self.assertIn("survivor", remaining)
        self.assertTrue(memory_index_db_path(memory_root).is_file())

    def test_memory_bundle_retry_repurges_a_late_lineage_copy(self) -> None:
        lineage_key = b"archive-purge-test-key-32-bytes-minimum"
        memory_root = self.root / "memory-retry"
        scope = memory_root / "guild_42" / "person_user_7"
        scope.mkdir(parents=True)
        target_turn = "turn-late-retry-01"
        raw_path = scope / "raw_transcript.jsonl"

        def encoded_target() -> str:
            return json.dumps(
                {
                    "role": "user",
                    "text": CANARY,
                    "source_turn_id": target_turn,
                    "evidence_id": f"turn:{target_turn}:user",
                },
                ensure_ascii=False,
            ) + "\n"

        raw_path.write_text(encoded_target(), encoding="utf-8")
        tool_cache_pending = {"value": True}

        def purge_process_tool_cache(_work_order) -> PurgePass:
            return PurgePass(
                manual_review_count=1 if tool_cache_pending["value"] else 0
            )

        owners = memory_bundle_purge_owners(
            memory_root=memory_root,
            lineage_key=lineage_key,
            process_tool_cache_purge=purge_process_tool_cache,
            writer_fence_current=lambda _work_order: True,
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=owners)
        archive = self.open_archive(
            required_sinks=MEMORY_BUNDLE_PURGE_SINKS,
            coordinator=coordinator,
        )
        archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body=CANARY,
            actor_external_id="synthetic-user",
            owner_name="Synthetic User",
            guild_id="42",
            channel_id="7",
            started_at=BASE,
            ended_at=BASE,
            lineage={"turn": (target_turn,)},
            idempotency_key="memory-retry-record-key",
            record_id="memory-retry-record",
            now=BASE,
        )
        self.delete_target(archive)

        first = coordinator.purge_pending(archive)[0]
        self.assertFalse(first.archive_completed)
        self.assertEqual(raw_path.read_text(encoding="utf-8"), "")

        # Simulate a worker that committed an already-started write after the
        # first physical purge.  The next coordinator pass must really purge
        # again rather than reuse the prior bundle report.
        raw_path.write_text(encoded_target(), encoding="utf-8")
        tool_cache_pending["value"] = False
        second = coordinator.purge_pending(archive)[0]

        self.assertTrue(second.archive_completed)
        self.assertEqual(raw_path.read_text(encoding="utf-8"), "")

    def test_memory_bundle_missing_root_is_manual_and_is_not_created(self) -> None:
        memory_root = self.root / "missing-memory-root"
        owners = memory_bundle_purge_owners(
            memory_root=memory_root,
            lineage_key=b"archive-purge-test-key-32-bytes-minimum",
            process_tool_cache_purge=lambda _work_order: PurgePass(),
            writer_fence_current=lambda _work_order: True,
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=owners)
        archive = self.open_archive(
            required_sinks=MEMORY_BUNDLE_PURGE_SINKS,
            coordinator=coordinator,
        )
        self.append_memory_target(
            archive,
            target_turn="turn-missing-root-01",
            record_id="memory-missing-root-record",
        )
        self.delete_target(archive)

        result = coordinator.purge_pending(archive)[0]

        self.assertFalse(result.archive_completed)
        self.assertTrue(
            all(status.state == "manual_review" for status in result.sinks)
        )
        self.assertEqual(result.receipts, ())
        self.assertFalse(memory_root.exists())

        memory_root.write_text("not-a-directory", encoding="utf-8")
        work_order = archive.pending_purge_work_orders()[0]
        invalid_root = owners[0].purge(work_order)
        self.assertGreaterEqual(invalid_root.manual_review_count, 1)
        self.assertEqual(
            memory_root.read_text(encoding="utf-8"),
            "not-a-directory",
        )

    def test_memory_bundle_malformed_candidates_fail_closed(self) -> None:
        memory_root = self.root / "malformed-memory"
        scope = memory_root / "guild_42" / "person_user_7"
        daily = memory_root / "memory_vault" / "daily"
        scope.mkdir(parents=True)
        daily.mkdir(parents=True)
        malformed = f'{{"private":"{CANARY}"'
        raw_path = scope / "raw_transcript.jsonl"
        valid_target = scope / "durable_facts.jsonl"
        provenance = scope / "rolling_summary.provenance.json"
        cognitive = scope / "cognitive_state.json"
        pending = scope / "pending_proactive_question.json"
        note = daily / "2026-08-28.md"
        raw_path.write_text(malformed, encoding="utf-8")
        valid_target.write_text(
            json.dumps({"source_turn_id": "turn-malformed-01"}) + "\n",
            encoding="utf-8",
        )
        (scope / "rolling_summary.txt").write_text(CANARY, encoding="utf-8")
        provenance.write_text(malformed, encoding="utf-8")
        cognitive.write_text(malformed, encoding="utf-8")
        pending.write_text(malformed, encoding="utf-8")
        note.write_text("---\nid: torn-front-matter\n", encoding="utf-8")

        owners = memory_bundle_purge_owners(
            memory_root=memory_root,
            lineage_key=b"archive-purge-test-key-32-bytes-minimum",
            process_tool_cache_purge=lambda _work_order: PurgePass(),
            writer_fence_current=lambda _work_order: True,
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=owners)
        archive = self.open_archive(
            required_sinks=MEMORY_BUNDLE_PURGE_SINKS,
            coordinator=coordinator,
        )
        self.append_memory_target(
            archive,
            target_turn="turn-malformed-01",
            record_id="memory-malformed-record",
        )
        self.delete_target(archive)

        result = coordinator.purge_pending(archive)[0]

        self.assertFalse(result.archive_completed)
        self.assertTrue(
            all(status.state == "manual_review" for status in result.sinks)
        )
        self.assertEqual(result.receipts, ())
        for candidate in (
            raw_path,
            valid_target,
            provenance,
            cognitive,
            pending,
            note,
        ):
            self.assertTrue(candidate.exists(), candidate.name)
        self.assertIn(
            "turn-malformed-01",
            valid_target.read_text(encoding="utf-8"),
        )
        self.assertNotIn(CANARY, repr(result))

    def test_memory_bundle_shared_scan_blocks_late_copy_and_retries(self) -> None:
        memory_root = self.root / "memory-shared-scan"
        scope = memory_root / "guild_42" / "person_user_7"
        scope.mkdir(parents=True)
        target_turn = "turn-late-shared-scan-01"
        raw_path = scope / "raw_transcript.jsonl"

        def encoded_target() -> str:
            return json.dumps(
                {
                    "text": CANARY,
                    "source_turn_id": target_turn,
                    "evidence_id": f"turn:{target_turn}:user",
                }
            ) + "\n"

        raw_path.write_text(encoded_target(), encoding="utf-8")
        owners = memory_bundle_purge_owners(
            memory_root=memory_root,
            lineage_key=b"archive-purge-test-key-32-bytes-minimum",
            process_tool_cache_purge=lambda _work_order: PurgePass(),
            writer_fence_current=lambda _work_order: True,
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=owners)
        archive = self.open_archive(
            required_sinks=MEMORY_BUNDLE_PURGE_SINKS,
            coordinator=coordinator,
        )
        self.append_memory_target(
            archive,
            target_turn=target_turn,
            record_id="memory-shared-scan-record",
        )
        self.delete_target(archive)
        work_order = archive.pending_purge_work_orders()[0]
        owner_by_sink = {owner.sink: owner for owner in owners}

        bot_owner = owner_by_sink["bot_memory"]
        bot_owner.purge(work_order)
        self.assertEqual(bot_owner.negative_recall(work_order).remaining_copies, 0)
        raw_path.write_text(encoded_target(), encoding="utf-8")

        search_owner = owner_by_sink["search_cache"]
        search_owner.purge(work_order)
        self.assertGreaterEqual(
            search_owner.negative_recall(work_order).remaining_copies,
            1,
        )
        for sink in MEMORY_BUNDLE_PURGE_SINKS[2:]:
            owner_by_sink[sink].purge(work_order)
            owner_by_sink[sink].negative_recall(work_order)

        retried = bot_owner.purge(work_order)
        self.assertGreaterEqual(retried.removed_count, 1)
        self.assertEqual(raw_path.read_text(encoding="utf-8"), "")

    def test_memory_bundle_without_writer_fence_stays_manual(self) -> None:
        memory_root = self.root / "memory-without-writer-fence"
        memory_root.mkdir()
        owners = memory_bundle_purge_owners(
            memory_root=memory_root,
            lineage_key=b"archive-purge-test-key-32-bytes-minimum",
            process_tool_cache_purge=lambda _work_order: PurgePass(),
        )
        coordinator = ConversationArchivePurgeCoordinator(owners=owners)
        archive = self.open_archive(
            required_sinks=MEMORY_BUNDLE_PURGE_SINKS,
            coordinator=coordinator,
        )
        self.append_memory_target(
            archive,
            target_turn="turn-no-writer-fence-01",
            record_id="memory-no-writer-fence-record",
        )
        self.delete_target(archive)

        result = coordinator.purge_pending(archive)[0]

        self.assertFalse(result.archive_completed)
        self.assertTrue(
            all(status.state == "manual_review" for status in result.sinks)
        )
        self.assertEqual(result.receipts, ())


if __name__ == "__main__":
    unittest.main()
