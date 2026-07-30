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
                    "changeId": "provcorr-v2-anchor",
                    "failedAt": "2026-07-31T00:00:00Z",
                    "errorCode": "test_failure",
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
                "'changeId':'provcorr-child',"
                "'failedAt':'2026-07-31T00:00:00Z',"
                "'errorCode':'test_failure'},"
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
                    "changeId": "provcorr-parent",
                    "failedAt": "2026-07-31T00:00:00Z",
                    "errorCode": "test_failure",
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
            ["provcorr-parent"],
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
