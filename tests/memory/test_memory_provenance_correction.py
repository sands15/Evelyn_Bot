from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
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

        self.assertEqual(
            [row["eventType"] for row in rows],
            ["prepared", "committed"],
        )
        self.assertTrue(rows[0]["contentFree"])
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
            self.assertNotIn(forbidden, raw)

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
