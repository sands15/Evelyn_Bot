from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
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

from evelyn_core import memory_provenance_correction as correction  # noqa: E402
from evelyn_core.memory_vault import (  # noqa: E402
    memory_provenance_backfill_preview,
    parse_memory_note,
    preview_memory_provenance_backfill_application,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


class MemoryProvenanceCorrectionTests(unittest.TestCase):
    def create_fixture(
        self,
        root: Path,
    ) -> dict[str, object]:
        source_a_path = write_memory_vault_note(
            note_type="daily",
            title="Correction Source A Canary",
            body="private source A body canary",
            source="conversation-turn-log",
            root=root,
        )
        source_b_path = write_memory_vault_note(
            note_type="daily",
            title="Correction Source B Canary",
            body="private source B body canary",
            source="conversation-turn-log",
            root=root,
        )
        source_a = parse_memory_note(source_a_path)
        source_b = parse_memory_note(source_b_path)
        target_path = write_memory_vault_note(
            note_type="episode",
            title="Correction Target Canary",
            body="private target body must remain stable",
            source="sub-llm-semantic-consolidation",
            derived_from=[source_a.note_id],
            root=root,
        )
        target = parse_memory_note(target_path)
        return {
            "source_a": source_a,
            "source_b": source_b,
            "target": target,
            "target_path": target_path,
        }

    def create_natural_id_fixture(
        self,
        root: Path,
    ) -> dict[str, object]:
        vault_root = root / "memory_vault"
        source_a_id = "PRIVATE correction source alpha sentence"
        source_b_id = "PRIVATE correction source beta sentence"
        target_id = "PRIVATE correction target transcript sentence"

        def write_note(
            relative_path: str,
            *,
            note_id: str,
            note_type: str,
            source: str,
            derived_from: list[str] | None = None,
        ) -> Path:
            path = vault_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "\n".join(
                    [
                        "---",
                        f"id: {note_id}",
                        f"type: {note_type}",
                        f"title: {note_id}",
                        "status: active",
                        f"source: {source}",
                        (
                            "derived_from: ["
                            + ", ".join(derived_from or [])
                            + "]"
                        ),
                        "revision: 0",
                        "---",
                        f"# {note_id}",
                        "private correction body canary",
                    ]
                ),
                encoding="utf-8",
            )
            return path

        source_a_path = write_note(
            "daily/natural-source-a.md",
            note_id=source_a_id,
            note_type="daily",
            source="conversation-turn-log",
        )
        source_b_path = write_note(
            "daily/natural-source-b.md",
            note_id=source_b_id,
            note_type="daily",
            source="conversation-turn-log",
        )
        target_path = write_note(
            "episodes/natural-target.md",
            note_id=target_id,
            note_type="episode",
            source="sub-llm-semantic-consolidation",
            derived_from=[source_a_id],
        )
        return {
            "source_a": parse_memory_note(source_a_path),
            "source_b": parse_memory_note(source_b_path),
            "target": parse_memory_note(target_path),
            "target_path": target_path,
        }

    def test_relink_then_explicit_undo_preserves_body(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            before_raw = target_path.read_text(encoding="utf-8")
            before_suffix = before_raw.split("---", 2)[2]

            options = (
                correction
                .memory_provenance_correction_source_options(
                    target.note_id,
                    root=root,
                )
            )
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                    now=lambda: 100.0,
                )
            )
            applied = (
                correction.apply_memory_provenance_correction(
                    target.note_id,
                    preview["confirmToken"],
                    root=root,
                    now=lambda: 101.0,
                )
            )
            after_relink_raw = target_path.read_text(
                encoding="utf-8"
            )
            after_relink = parse_memory_note(
                target_path,
                after_relink_raw,
            )
            overview = (
                correction.memory_provenance_correction_overview(
                    root=root
                )
            )
            undo_preview = (
                correction
                .preview_memory_provenance_correction_undo(
                    target.note_id,
                    applied["changeId"],
                    root=root,
                    now=lambda: 102.0,
                )
            )
            undone = (
                correction
                .apply_memory_provenance_correction_undo(
                    target.note_id,
                    undo_preview["confirmToken"],
                    root=root,
                    now=lambda: 103.0,
                )
            )
            after_undo_raw = target_path.read_text(
                encoding="utf-8"
            )
            after_undo = parse_memory_note(
                target_path,
                after_undo_raw,
            )
            final_overview = (
                correction.memory_provenance_correction_overview(
                    root=root
                )
            )

        self.assertEqual(
            options["currentSourceIds"],
            [source_a.note_id],
        )
        self.assertEqual(
            {
                item["id"]
                for item in options["sourceOptions"]
            },
            {source_a.note_id, source_b.note_id},
        )
        self.assertEqual(preview["action"], "relink")
        self.assertFalse(
            preview["consequences"]["automaticInferenceUsed"]
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(
            after_relink.metadata["derived_from"],
            f"[{source_b.note_id}]",
        )
        self.assertEqual(
            after_relink.metadata["origin_derived_from"],
            f"[{source_a.note_id}]",
        )
        self.assertEqual(
            after_relink.metadata[
                "provenance_correction_method"
            ],
            "user-relinked-source-note-ids",
        )
        self.assertEqual(
            after_relink_raw.split("---", 2)[2],
            before_suffix,
        )
        self.assertTrue(
            overview["relationships"][0]["latestChange"][
                "canUndo"
            ]
        )
        self.assertEqual(undo_preview["previewKind"], "undo")
        self.assertTrue(undone["applied"])
        self.assertEqual(undone["action"], "undo")
        self.assertEqual(
            after_undo.metadata["derived_from"],
            f"[{source_a.note_id}]",
        )
        self.assertEqual(
            after_undo.metadata.get("origin_derived_from"),
            "[]",
        )
        self.assertEqual(
            after_undo.metadata[
                "provenance_correction_method"
            ],
            "user-undo",
        )
        self.assertEqual(
            after_undo_raw.split("---", 2)[2],
            before_suffix,
        )
        self.assertFalse(
            final_overview["relationships"][0][
                "latestChange"
            ]["canUndo"]
        )

    def test_unlink_is_user_detach_and_can_relink(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]

            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [],
                    root=root,
                )
            )
            applied = (
                correction.apply_memory_provenance_correction(
                    target.note_id,
                    preview["confirmToken"],
                    root=root,
                )
            )
            detached = parse_memory_note(target_path)
            audit = memory_provenance_backfill_preview(
                root=root
            )
            relink_preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            relinked = (
                correction.apply_memory_provenance_correction(
                    target.note_id,
                    relink_preview["confirmToken"],
                    root=root,
                )
            )
            final_note = parse_memory_note(target_path)

        self.assertEqual(preview["action"], "unlink")
        self.assertTrue(applied["applied"])
        self.assertEqual(
            detached.metadata["derived_from"],
            "[]",
        )
        self.assertEqual(
            detached.metadata["origin_derived_from"],
            f"[{source_a.note_id}]",
        )
        self.assertEqual(
            audit["coverage"]["needsReviewCount"],
            0,
        )
        self.assertEqual(
            audit["coverage"]["stateCounts"][
                "user_detached"
            ],
            1,
        )
        self.assertTrue(relinked["applied"])
        self.assertEqual(
            final_note.metadata["derived_from"],
            f"[{source_b.note_id}]",
        )
        self.assertEqual(
            final_note.metadata["origin_derived_from"],
            f"[{source_a.note_id}]",
        )

    def test_journal_is_content_free(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            correction.apply_memory_provenance_correction(
                target.note_id,
                preview["confirmToken"],
                root=root,
            )
            journal_path = (
                root
                / "memory_index"
                / "memory_provenance_corrections.jsonl"
            )
            raw = journal_path.read_text(encoding="utf-8")
            rows = [
                json.loads(line)
                for line in raw.splitlines()
            ]
            head_raw = correction._chain_head_path(
                root
            ).read_text(encoding="utf-8")
            head = json.loads(head_raw)
            marker_raw = correction._writer_marker_path(
                root
            ).read_text(encoding="utf-8")
            marker = json.loads(marker_raw)

        self.assertEqual(
            [row["eventType"] for row in rows],
            ["prepared", "committed"],
        )
        self.assertEqual(
            [row["schema"] for row in rows],
            [
                correction.MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA,
                correction.MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA,
            ],
        )
        self.assertEqual(
            [row["sequence"] for row in rows],
            [1, 2],
        )
        self.assertEqual(
            rows[0]["previousHash"],
            correction.MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS,
        )
        self.assertEqual(
            rows[1]["previousHash"],
            rows[0]["eventHash"],
        )
        self.assertTrue(
            all(len(row["eventHash"]) == 64 for row in rows)
        )
        self.assertEqual(head["sequence"], 2)
        self.assertEqual(head["eventHash"], rows[1]["eventHash"])
        self.assertTrue(head["contentFree"])
        self.assertEqual(marker["state"], "released")
        self.assertTrue(marker["contentFree"])
        self.assertTrue(rows[0]["contentFree"])
        persisted = "\n".join(
            (raw, head_raw, marker_raw)
        )
        for forbidden in (
            "Correction Source",
            "Correction Target",
            "private source",
            "private target",
            '"title"',
            '"body"',
            '"path"',
            '"contentHash"',
            '"sourceHash"',
            '"evidenceHash"',
            '"transcript"',
        ):
            self.assertNotIn(forbidden, persisted)

    def test_v2_journal_rejects_noncanonical_and_wrong_event_shapes(
        self,
    ) -> None:
        for mutation in (
            "whitespace",
            "crlf",
            "missing-lf",
            "extra-field",
            "missing-field",
            "private-change-id",
            "private-error-code",
        ):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    correction._append_journal_event(
                        {
                            "eventType": "failed",
                            "changeId": "provcorr-000000000000000000000001",
                            "failedAt": "2026-08-01T00:00:00Z",
                            "errorCode": (
                                "memory_provenance_correction_failed"
                            ),
                        },
                        root=root,
                    )
                    journal_path = correction._journal_path(root)
                    raw = journal_path.read_text(encoding="utf-8")
                    if mutation == "whitespace":
                        journal_path.write_text(
                            " " + raw,
                            encoding="utf-8",
                        )
                    elif mutation == "crlf":
                        journal_path.write_bytes(
                            raw.encode("utf-8").replace(b"\n", b"\r\n")
                        )
                    elif mutation == "missing-lf":
                        journal_path.write_text(
                            raw.rstrip("\n"),
                            encoding="utf-8",
                        )
                    else:
                        event = json.loads(raw)
                        if mutation == "extra-field":
                            event["privateActor"] = (
                                "PRIVATE TRANSCRIPT CANARY"
                            )
                        elif mutation == "missing-field":
                            event.pop("errorCode")
                        elif mutation == "private-change-id":
                            event["changeId"] = (
                                "PRIVATE TRANSCRIPT CANARY"
                            )
                        else:
                            event["errorCode"] = (
                                "private_transcript_canary"
                            )
                        event["eventHash"] = correction._event_hash(event)
                        journal_path.write_text(
                            correction._canonical_json(event) + "\n",
                            encoding="utf-8",
                        )
                        head_path = correction._chain_head_path(root)
                        head = json.loads(
                            head_path.read_text(encoding="utf-8")
                        )
                        head["eventHash"] = event["eventHash"]
                        head_path.write_text(
                            correction._canonical_artifact_json(head),
                            encoding="utf-8",
                        )

                    with self.assertRaises(
                        correction
                        .MemoryProvenanceCorrectionJournalIntegrityError
                    ) as raised:
                        correction._journal_snapshot(root)

                self.assertEqual(
                    str(raised.exception),
                    (
                        "memory_provenance_correction_"
                        "journal_integrity_failed"
                    ),
                )

    def test_v2_writer_rejects_extra_fields_before_persisting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(
                correction.MemoryProvenanceCorrectionJournalIntegrityError
            ) as raised:
                correction._append_journal_event(
                    {
                        "eventType": "failed",
                        "changeId": "provcorr-000000000000000000000002",
                        "failedAt": "2026-08-01T00:00:00Z",
                        "errorCode": (
                            "memory_provenance_correction_failed"
                        ),
                        "privateActor": "PRIVATE TRANSCRIPT CANARY",
                    },
                    root=root,
                )
            journal_exists = correction._journal_path(root).exists()

        self.assertEqual(
            str(raised.exception),
            "memory_provenance_correction_journal_integrity_failed",
        )
        self.assertFalse(journal_exists)

    def test_unsigned_head_rejects_private_updated_at_without_rehash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            correction._append_journal_event(
                {
                    "eventType": "failed",
                    "changeId": "provcorr-00000000000000000000000a",
                    "failedAt": "2026-08-01T00:00:00Z",
                    "errorCode": "memory_provenance_correction_failed",
                },
                root=root,
            )
            head_path = correction._chain_head_path(root)
            head = json.loads(head_path.read_text(encoding="utf-8"))
            original_event_hash = head["eventHash"]
            head["updatedAt"] = "PRIVATE TRANSCRIPT CANARY"
            head_path.write_text(
                correction._canonical_artifact_json(head),
                encoding="utf-8",
            )

            with self.assertRaises(
                correction.MemoryProvenanceCorrectionJournalIntegrityError
            ) as raised:
                correction._journal_snapshot(root)

        self.assertEqual(head["eventHash"], original_event_hash)
        self.assertEqual(
            str(raised.exception),
            "memory_provenance_correction_journal_integrity_failed",
        )

    def test_writer_marker_reader_rejects_and_lease_scrubs_private_json(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            correction._write_writer_marker(
                root=root,
                state="released",
                acquired_at="2026-08-01T00:00:00Z",
                recovered_stale_owner=False,
            )
            marker_path = correction._writer_marker_path(root)
            raw = marker_path.read_text(encoding="utf-8")
            mutated = raw.replace(
                '"state": "released"',
                (
                    '"secret": "PRIVATE TRANSCRIPT CANARY",\n'
                    '  "state": "held",\n'
                    '  "state": "released"'
                ),
                1,
            )
            self.assertEqual(json.loads(mutated)["state"], "released")
            marker_path.write_text(mutated, encoding="utf-8")

            self.assertEqual(
                correction._writer_public_state(root),
                "unknown",
            )
            self.assertEqual(
                marker_path.read_text(encoding="utf-8"),
                mutated,
            )
            with correction._writer_guard(root):
                held = marker_path.read_text(encoding="utf-8")
                self.assertEqual(
                    correction._writer_public_state(root),
                    "held",
                )
                self.assertNotIn("PRIVATE", held)
            released = marker_path.read_text(encoding="utf-8")
            released_payload = correction._read_valid_writer_marker(
                marker_path
            )

        self.assertEqual(released_payload["state"], "released")
        self.assertNotIn("PRIVATE", released)

    def test_noncanonical_legacy_v1_prefix_remains_immutable_compatible(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_path = correction._journal_path(root)
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            legacy = {
                "schema": (
                    correction
                    .MEMORY_PROVENANCE_CORRECTION_LEGACY_EVENT_SCHEMA
                ),
                "eventType": "failed",
                "changeId": "provcorr-legacy-noncanonical",
                "legacyCompatibility": True,
            }
            legacy_line = json.dumps(
                legacy,
                ensure_ascii=False,
                sort_keys=False,
            )
            self.assertNotEqual(
                legacy_line,
                correction._canonical_json(legacy),
            )
            journal_path.write_text(
                legacy_line + "\n",
                encoding="utf-8",
            )
            correction._append_journal_event(
                {
                    "eventType": "failed",
                    "changeId": "provcorr-000000000000000000000003",
                    "failedAt": "2026-08-01T00:00:00Z",
                    "errorCode": "memory_provenance_correction_failed",
                },
                root=root,
            )
            snapshot = correction._journal_snapshot(root)

        self.assertEqual(len(snapshot["events"]), 2)
        self.assertTrue(snapshot["events"][0]["legacyCompatibility"])
        self.assertEqual(
            snapshot["events"][1]["previousHash"],
            correction._legacy_anchor([legacy_line]),
        )

    def test_v2_journal_canonicalizes_natural_ids_and_undo_round_trips(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_natural_id_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            preview = correction.preview_memory_provenance_correction(
                target.note_id,
                [source_b.note_id],
                root=root,
            )
            applied = correction.apply_memory_provenance_correction(
                target.note_id,
                preview["confirmToken"],
                root=root,
            )
            journal_path = correction._journal_path(root)
            persisted_raw = journal_path.read_text(encoding="utf-8")
            persisted_rows = [
                json.loads(line)
                for line in persisted_raw.splitlines()
            ]
            prepared = persisted_rows[0]
            semantic_prepared = correction._read_journal_events(
                root
            )[0]
            undo_preview = (
                correction.preview_memory_provenance_correction_undo(
                    target.note_id,
                    applied["changeId"],
                    root=root,
                )
            )
            undone = (
                correction.apply_memory_provenance_correction_undo(
                    target.note_id,
                    undo_preview["confirmToken"],
                    root=root,
                )
            )
            restored = parse_memory_note(target_path)
            final_journal_raw = journal_path.read_text(
                encoding="utf-8"
            )

        self.assertTrue(applied["applied"], applied)
        self.assertEqual(
            prepared["targetNoteId"],
            correction.memory_deletion_ledger_note_id(
                target.note_id
            ),
        )
        for field in (
            "previousSourceIds",
            "previousOriginSourceIds",
            "newSourceIds",
            "newOriginSourceIds",
        ):
            self.assertTrue(
                all(
                    correction.memory_deletion_note_id_is_canonical(
                        note_id
                    )
                    for note_id in prepared[field]
                )
            )
        self.assertEqual(
            semantic_prepared["targetNoteId"],
            target.note_id,
        )
        self.assertEqual(
            semantic_prepared["previousSourceIds"],
            [source_a.note_id],
        )
        self.assertEqual(
            semantic_prepared["newSourceIds"],
            [source_b.note_id],
        )
        self.assertTrue(undone["applied"], undone)
        self.assertEqual(
            restored.metadata["derived_from"],
            f"[{source_a.note_id}]",
        )
        for raw_id in (
            source_a.note_id,
            source_b.note_id,
            target.note_id,
        ):
            self.assertNotIn(raw_id, persisted_raw)
            self.assertNotIn(raw_id, final_journal_raw)

    def test_v2_journal_rejects_unmapped_canonical_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            correction._append_journal_event(
                {
                    "eventType": "prepared",
                    "changeId": "provcorr-000000000000000000000004",
                    "action": "relink",
                    "targetNoteId": "PRIVATE absent target sentence",
                    "previousSourceIds": [],
                    "previousOriginSourceIds": [],
                    "newSourceIds": [
                        "PRIVATE absent source sentence"
                    ],
                    "newOriginSourceIds": [],
                    "previousRevision": 0,
                    "nextRevision": 1,
                    "undoOfChangeId": "",
                    "actor": "control-page-user",
                    "preparedAt": "2026-08-01T00:00:00Z",
                    "contentFree": True,
                },
                root=root,
            )
            persisted_raw = correction._journal_path(root).read_text(
                encoding="utf-8"
            )

            with self.assertRaises(
                correction
                .MemoryProvenanceCorrectionJournalIntegrityError
            ):
                correction._read_journal_events(root)

        self.assertNotIn("PRIVATE absent", persisted_raw)

    def test_v2_journal_rejects_ambiguous_canonical_mapping(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_natural_id_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            correction._append_journal_event(
                {
                    "eventType": "prepared",
                    "changeId": "provcorr-000000000000000000000005",
                    "action": "relink",
                    "targetNoteId": target.note_id,
                    "previousSourceIds": [source_a.note_id],
                    "previousOriginSourceIds": [],
                    "newSourceIds": [source_b.note_id],
                    "newOriginSourceIds": [source_a.note_id],
                    "previousRevision": 0,
                    "nextRevision": 1,
                    "undoOfChangeId": "",
                    "actor": "control-page-user",
                    "preparedAt": "2026-08-01T00:00:00Z",
                    "contentFree": True,
                },
                root=root,
            )
            stored_target_id = (
                correction.memory_deletion_ledger_note_id(
                    target.note_id
                )
            )
            real_canonicalize = (
                correction.memory_deletion_ledger_note_id
            )

            def collide(value: object) -> str:
                if value == source_a.note_id:
                    return stored_target_id
                return real_canonicalize(value)

            with patch.object(
                correction,
                "memory_deletion_ledger_note_id",
                side_effect=collide,
            ):
                with self.assertRaises(
                    correction
                    .MemoryProvenanceCorrectionJournalIntegrityError
                ):
                    correction._read_journal_events(root)

    def test_natural_id_prepared_event_recovers_after_new_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_natural_id_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            preview = correction.preview_memory_provenance_correction(
                target.note_id,
                [source_b.note_id],
                root=root,
            )
            original_append = correction._append_journal_event

            def fail_commit(
                payload: dict[str, object],
                *,
                root: Path | None = None,
            ) -> None:
                if payload.get("eventType") == "committed":
                    raise OSError("simulated commit crash")
                original_append(payload, root=root)

            with patch.object(
                correction,
                "_append_journal_event",
                side_effect=fail_commit,
            ):
                applied = (
                    correction.apply_memory_provenance_correction(
                        target.note_id,
                        preview["confirmToken"],
                        root=root,
                    )
                )

            script = (
                "import json,sys;"
                f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                "from pathlib import Path;"
                "from evelyn_core.memory_provenance_correction "
                "import memory_provenance_correction_overview;"
                "payload=memory_provenance_correction_overview("
                f"root=Path({str(root)!r}));"
                "print(json.dumps(payload))"
            )
            recovered = json.loads(
                subprocess.check_output(
                    [sys.executable, "-c", script],
                    text=True,
                    cwd=str(REPO_ROOT),
                )
            )
            undo_preview = (
                correction.preview_memory_provenance_correction_undo(
                    target.note_id,
                    applied["changeId"],
                    root=root,
                )
            )
            undone = (
                correction.apply_memory_provenance_correction_undo(
                    target.note_id,
                    undo_preview["confirmToken"],
                    root=root,
                )
            )
            restored = parse_memory_note(target_path)
            journal_raw = correction._journal_path(root).read_text(
                encoding="utf-8"
            )

        self.assertTrue(applied["applied"], applied)
        self.assertFalse(applied["ok"], applied)
        self.assertTrue(recovered["ok"], recovered)
        self.assertTrue(
            recovered["relationships"][0]["latestChange"][
                "canUndo"
            ]
        )
        self.assertTrue(undone["applied"], undone)
        self.assertEqual(
            restored.metadata["derived_from"],
            f"[{source_a.note_id}]",
        )
        self.assertIn('"recoveredAfterRestart":true', journal_raw)
        for raw_id in (
            source_a.note_id,
            source_b.note_id,
            target.note_id,
        ):
            self.assertNotIn(raw_id, journal_raw)

    def test_tampered_journal_blocks_apply_without_note_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            before_raw = target_path.read_text(encoding="utf-8")
            journal_path = correction._journal_path(root)
            journal_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            journal_path.write_text(
                json.dumps(
                    {
                        "schema": (
                            correction
                            .MEMORY_PROVENANCE_CORRECTION_EVENT_SCHEMA
                        )
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            applied = (
                correction.apply_memory_provenance_correction(
                    target.note_id,
                    preview["confirmToken"],
                    root=root,
                )
            )
            overview = (
                correction.memory_provenance_correction_overview(
                    root=root
                )
            )
            after_raw = target_path.read_text(
                encoding="utf-8"
            )

        self.assertFalse(applied["ok"])
        self.assertFalse(applied["applied"])
        self.assertEqual(
            applied["error"],
            (
                "memory_provenance_correction_"
                "journal_integrity_failed"
            ),
        )
        self.assertEqual(
            after_raw,
            before_raw,
        )
        self.assertFalse(overview["ok"])
        self.assertEqual(
            overview["journalIntegrity"],
            "failed",
        )
        self.assertEqual(overview["relationships"], [])

    def test_tail_deletion_is_detected_by_chain_head(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            correction.apply_memory_provenance_correction(
                target.note_id,
                preview["confirmToken"],
                root=root,
            )
            journal_path = correction._journal_path(root)
            rows = journal_path.read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(rows), 2)
            journal_path.write_text(
                rows[0] + "\n",
                encoding="utf-8",
            )

            overview = (
                correction.memory_provenance_correction_overview(
                    root=root
                )
            )

        self.assertFalse(overview["ok"])
        self.assertEqual(
            overview["error"],
            (
                "memory_provenance_correction_"
                "journal_integrity_failed"
            ),
        )

    def test_legacy_prefix_is_anchored_by_first_v2_event(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal_path = correction._journal_path(root)
            journal_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            legacy = {
                "schema": (
                    correction
                    .MEMORY_PROVENANCE_CORRECTION_LEGACY_EVENT_SCHEMA
                ),
                "eventType": "failed",
                "changeId": "provcorr-legacy-prefix",
            }
            legacy_line = json.dumps(
                legacy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            journal_path.write_text(
                legacy_line + "\n",
                encoding="utf-8",
            )

            correction._append_journal_event(
                {
                    "eventType": "failed",
                    "changeId": "provcorr-000000000000000000000006",
                    "failedAt": "2026-07-31T00:00:00Z",
                    "errorCode": "memory_provenance_correction_failed",
                },
                root=root,
            )
            rows = [
                json.loads(line)
                for line in journal_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            head = json.loads(
                correction._chain_head_path(root).read_text(
                    encoding="utf-8"
                )
            )
            legacy["eventType"] = "committed"
            changed_legacy_line = json.dumps(
                legacy,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            journal_path.write_text(
                changed_legacy_line
                + "\n"
                + json.dumps(
                    rows[1],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(
                correction
                .MemoryProvenanceCorrectionJournalIntegrityError
            ):
                correction._read_journal_events(root)

        self.assertEqual(
            rows[1]["previousHash"],
            correction._legacy_anchor([legacy_line]),
        )
        self.assertNotEqual(
            rows[1]["previousHash"],
            correction.MEMORY_PROVENANCE_CORRECTION_CHAIN_GENESIS,
        )
        self.assertEqual(head["sequence"], 1)
        self.assertEqual(head["eventHash"], rows[1]["eventHash"])

    def test_competing_process_writer_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = (
                "import json,sys;"
                f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                "from pathlib import Path;"
                "from evelyn_core import "
                "memory_provenance_correction as c;"
                "\ntry:\n"
                " c._append_journal_event("
                "{'eventType':'failed',"
                "'changeId':'provcorr-000000000000000000000007',"
                "'failedAt':'2026-07-31T00:00:00Z',"
                "'errorCode':'memory_provenance_correction_failed'},"
                f"root=Path({str(root)!r}))\n"
                " print(json.dumps({'ok':True}))\n"
                "except Exception as exc:\n"
                " print(json.dumps({"
                "'ok':False,"
                "'type':type(exc).__name__,"
                "'error':str(exc)}))"
            )
            with correction._writer_guard(root):
                child = subprocess.run(
                    [sys.executable, "-c", script],
                    text=True,
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    check=True,
                )
            child_result = json.loads(child.stdout)
            correction._append_journal_event(
                {
                    "eventType": "failed",
                    "changeId": "provcorr-000000000000000000000008",
                    "failedAt": "2026-07-31T00:00:00Z",
                    "errorCode": "memory_provenance_correction_failed",
                },
                root=root,
            )
            rows = correction._read_journal_events(root)
            marker = json.loads(
                correction._writer_marker_path(root).read_text(
                    encoding="utf-8"
                )
            )

        self.assertFalse(child_result["ok"])
        self.assertEqual(
            child_result["type"],
            "MemoryProvenanceCorrectionWriterUnavailable",
        )
        self.assertEqual(
            child_result["error"],
            "memory_provenance_correction_writer_unavailable",
        )
        self.assertEqual(
            [row["changeId"] for row in rows],
            ["provcorr-000000000000000000000008"],
        )
        self.assertEqual(marker["state"], "released")

    def test_competing_thread_writer_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def try_writer() -> tuple[str, str]:
                try:
                    with correction._writer_guard(root):
                        return ("ok", "")
                except Exception as exc:
                    return (type(exc).__name__, str(exc))

            with correction._writer_guard(root):
                with ThreadPoolExecutor(
                    max_workers=1
                ) as executor:
                    result = executor.submit(
                        try_writer
                    ).result(timeout=2)

        self.assertEqual(
            result,
            (
                "MemoryProvenanceCorrectionWriterUnavailable",
                (
                    "memory_provenance_correction_"
                    "writer_unavailable"
                ),
            ),
        )

    def test_lagging_chain_head_recovers_after_commit_crash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            original_write_head = correction._write_chain_head
            calls = 0

            def fail_second_head(
                *,
                root: Path | None,
                sequence: int,
                event_hash: str,
            ) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated head crash")
                original_write_head(
                    root=root,
                    sequence=sequence,
                    event_hash=event_hash,
                )

            with patch.object(
                correction,
                "_write_chain_head",
                side_effect=fail_second_head,
            ):
                applied = (
                    correction.apply_memory_provenance_correction(
                        target.note_id,
                        preview["confirmToken"],
                        root=root,
                    )
                )
            lagging = correction._journal_snapshot(root)
            overview = (
                correction.memory_provenance_correction_overview(
                    root=root
                )
            )
            repaired = correction._journal_snapshot(root)
            rows = correction._read_journal_events(root)
            head = json.loads(
                correction._chain_head_path(root).read_text(
                    encoding="utf-8"
                )
            )

        self.assertTrue(applied["applied"])
        self.assertFalse(applied["ok"])
        self.assertIn(
            (
                "memory_provenance_correction_"
                "journal_commit_failed"
            ),
            applied["cleanupErrors"],
        )
        self.assertEqual(lagging["headState"], "lagging")
        self.assertTrue(overview["ok"])
        self.assertEqual(repaired["headState"], "current")
        self.assertEqual(head["sequence"], 2)
        self.assertEqual(head["eventHash"], rows[-1]["eventHash"])

    def test_post_write_exception_is_committed_not_failed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            original_write = correction.atomic_text_write

            def write_then_raise(
                path: Path,
                text: str,
                *,
                durable: bool = False,
            ) -> None:
                original_write(
                    path,
                    text,
                    durable=durable,
                )
                raise OSError("simulated post-write failure")

            with patch.object(
                correction,
                "atomic_text_write",
                side_effect=write_then_raise,
            ):
                applied = (
                    correction.apply_memory_provenance_correction(
                        target.note_id,
                        preview["confirmToken"],
                        root=root,
                    )
                )
            updated = parse_memory_note(target_path)
            journal_path = (
                root
                / "memory_index"
                / "memory_provenance_corrections.jsonl"
            )
            event_types = [
                json.loads(line)["eventType"]
                for line in journal_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]

        self.assertTrue(applied["ok"])
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["recoveredDuringApply"])
        self.assertNotIn("contentHash", applied)
        self.assertNotIn("previousContentHash", applied)
        self.assertEqual(
            updated.metadata["derived_from"],
            f"[{source_b.note_id}]",
        )
        self.assertEqual(
            event_types,
            ["prepared", "committed"],
        )

    def test_rejects_noop_unavailable_ungrounded_cycle_and_hidden(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            ungrounded_path = write_memory_vault_note(
                note_type="episode",
                title="Ungrounded Runtime Source",
                body="ungrounded body",
                source="runtime",
                root=root,
            )
            ungrounded = parse_memory_note(ungrounded_path)
            descendant_path = write_memory_vault_note(
                note_type="episode",
                title="Descendant Source",
                body="descendant body",
                source="sub-llm-semantic-consolidation",
                derived_from=[target.note_id],
                root=root,
            )
            descendant = parse_memory_note(descendant_path)

            noop = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_a.note_id],
                    root=root,
                )
            )
            unavailable = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    ["missing-source"],
                    root=root,
                )
            )
            ungrounded_result = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [ungrounded.note_id],
                    root=root,
                )
            )
            cycle = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [descendant.note_id],
                    root=root,
                )
            )
            update_memory_vault_user_note(
                source_b.note_id,
                "hide",
                root=root,
            )
            hidden = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            direct_target = (
                correction.preview_memory_provenance_correction(
                    ungrounded.note_id,
                    [source_a.note_id],
                    root=root,
                )
            )

        self.assertEqual(
            noop["error"],
            "memory_provenance_correction_no_change",
        )
        self.assertEqual(
            unavailable["error"],
            "memory_provenance_correction_source_unavailable",
        )
        self.assertEqual(
            ungrounded_result["error"],
            "memory_provenance_correction_source_ungrounded",
        )
        self.assertEqual(
            cycle["error"],
            "memory_provenance_correction_cycle",
        )
        self.assertEqual(
            hidden["error"],
            "memory_provenance_source_hidden",
        )
        self.assertEqual(
            direct_target["error"],
            "memory_provenance_correction_target_ineligible",
        )

    def test_full_graph_change_consumes_token_without_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            before_raw = target_path.read_text(encoding="utf-8")
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            write_memory_vault_note(
                note_type="daily",
                title="Unrelated Graph Change",
                body="unrelated",
                source="conversation-turn-log",
                root=root,
            )
            changed = (
                correction.apply_memory_provenance_correction(
                    target.note_id,
                    preview["confirmToken"],
                    root=root,
                )
            )
            reused = (
                correction.apply_memory_provenance_correction(
                    target.note_id,
                    preview["confirmToken"],
                    root=root,
                )
            )
            after_raw = target_path.read_text(
                encoding="utf-8"
            )
            after_source_id = parse_memory_note(
                target_path
            ).metadata["derived_from"].strip("[]")

        self.assertEqual(
            changed["error"],
            (
                "memory_provenance_correction_"
                "changed_since_preview"
            ),
        )
        self.assertEqual(
            reused["error"],
            "memory_provenance_correction_token_reused",
        )
        self.assertEqual(after_raw, before_raw)
        self.assertEqual(source_a.note_id, after_source_id)

    def test_commit_event_recovers_after_new_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            original_append = correction._append_journal_event

            def fail_commit(
                payload: dict[str, object],
                *,
                root: Path | None = None,
            ) -> None:
                if payload.get("eventType") == "committed":
                    raise OSError("simulated commit crash")
                original_append(payload, root=root)

            with patch.object(
                correction,
                "_append_journal_event",
                side_effect=fail_commit,
            ):
                applied = (
                    correction.apply_memory_provenance_correction(
                        target.note_id,
                        preview["confirmToken"],
                        root=root,
                    )
                )

            script = (
                "import json,sys;"
                f"sys.path.insert(0,{str(RUNTIME_ROOT)!r});"
                "from pathlib import Path;"
                "from evelyn_core.memory_provenance_correction "
                "import memory_provenance_correction_overview;"
                "payload=memory_provenance_correction_overview("
                f"root=Path({str(root)!r}));"
                "print(json.dumps(payload))"
            )
            recovered = json.loads(
                subprocess.check_output(
                    [sys.executable, "-c", script],
                    text=True,
                    cwd=str(REPO_ROOT),
                )
            )
            journal_raw = (
                root
                / "memory_index"
                / "memory_provenance_corrections.jsonl"
            ).read_text(encoding="utf-8")

        self.assertTrue(applied["applied"])
        self.assertFalse(applied["ok"])
        self.assertIn(
            (
                "memory_provenance_correction_"
                "journal_commit_failed"
            ),
            applied["cleanupErrors"],
        )
        self.assertTrue(
            recovered["relationships"][0][
                "latestChange"
            ]["canUndo"]
        )
        self.assertIn('"recoveredAfterRestart":true', journal_raw)

    def test_undo_refuses_stale_or_nonlatest_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_a = fixture["source_a"]
            source_b = fixture["source_b"]
            target = fixture["target"]
            first_preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )
            first = correction.apply_memory_provenance_correction(
                target.note_id,
                first_preview["confirmToken"],
                root=root,
            )
            second_preview = (
                correction.preview_memory_provenance_correction(
                    target.note_id,
                    [source_a.note_id],
                    root=root,
                )
            )
            second = correction.apply_memory_provenance_correction(
                target.note_id,
                second_preview["confirmToken"],
                root=root,
            )
            stale = (
                correction
                .preview_memory_provenance_correction_undo(
                    target.note_id,
                    first["changeId"],
                    root=root,
                )
            )
            current = (
                correction
                .preview_memory_provenance_correction_undo(
                    target.note_id,
                    second["changeId"],
                    root=root,
                )
            )

        self.assertEqual(
            stale["error"],
            "memory_provenance_correction_undo_unavailable",
        )
        self.assertTrue(current["ok"])

    def test_backfill_remains_separate_from_correction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source_b = fixture["source_b"]
            target = fixture["target"]
            backfill = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source_b.note_id],
                    root=root,
                )
            )

        self.assertEqual(
            backfill["error"],
            "memory_provenance_backfill_candidate_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
