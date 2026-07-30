from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"

if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_vault  # noqa: E402
from evelyn_core.memory_provenance_audit import (  # noqa: E402
    ProvenanceAuditNode,
    audit_missing_derivations,
    summarize_provenance_coverage,
)
from evelyn_core.memory_vault import (  # noqa: E402
    apply_memory_provenance_backfill,
    memory_provenance_backfill_preview,
    memory_provenance_manual_source_options,
    parse_memory_note,
    preview_memory_provenance_backfill_application,
    update_memory_vault_user_note,
    write_memory_vault_note,
)


class MemoryProvenanceCoverageTests(unittest.TestCase):
    def test_coverage_counts_signal_free_and_unmatched_notes(
        self,
    ) -> None:
        nodes = [
            ProvenanceAuditNode(
                note_id="direct",
                note_type="daily",
                source_type="conversation",
                updated_at="2026-07-29T00:00:00Z",
            ),
            ProvenanceAuditNode(
                note_id="declared",
                note_type="episode",
                source_type="derived",
                derived_from=("direct",),
                updated_at="2026-07-10T00:00:00Z",
            ),
            ProvenanceAuditNode(
                note_id="missing",
                note_type="concept",
                source_type="derived",
                updated_at="2026-05-01T00:00:00Z",
            ),
            ProvenanceAuditNode(
                note_id="unmatched",
                note_type="episode",
                source_type="derived",
                source_refs=("not-a-note",),
                updated_at="2025-01-01T00:00:00Z",
            ),
        ]

        audit = audit_missing_derivations(nodes)
        coverage = summarize_provenance_coverage(
            nodes,
            audit=audit,
            forward_write_rejections={
                "count": 2,
                "byNoteType": {"episode": 2},
            },
            now=datetime(
                2026,
                7,
                30,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(
            audit.missing_signal_target_ids,
            ("missing",),
        )
        self.assertEqual(
            audit.unmatched_target_ids,
            ("unmatched",),
        )
        self.assertEqual(coverage["totalNoteCount"], 4)
        self.assertEqual(coverage["groundedNoteCount"], 2)
        self.assertEqual(coverage["needsReviewCount"], 2)
        self.assertEqual(coverage["coverageRatio"], 0.5)
        self.assertEqual(
            coverage["stateCounts"]["missing_signal"],
            1,
        )
        self.assertEqual(
            coverage["stateCounts"]["unmatched_metadata"],
            1,
        )
        self.assertEqual(
            {
                row["key"]
                for row in coverage["byAgeBucket"]
            },
            {"0_7d", "8_30d", "31_180d", "over_180d"},
        )
        self.assertEqual(
            coverage["forwardWriteRejections"]["count"],
            2,
        )

    def test_forward_rejection_counter_is_content_free(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for title in ("Canary Secret A", "Canary Secret B"):
                with self.assertRaisesRegex(
                    ValueError,
                    "memory_derived_from_required",
                ):
                    write_memory_vault_note(
                        note_type="episode",
                        title=title,
                        body="private body canary",
                        source="sub-llm-semantic-consolidation",
                        root=root,
                    )
            preview = memory_provenance_backfill_preview(
                root=root
            )
            counter_path = (
                root
                / "memory_index"
                / (
                    "memory_provenance_"
                    "forward_write_rejections.json"
                )
            )
            payload = json.loads(
                counter_path.read_text(encoding="utf-8")
            )
            raw = counter_path.read_text(encoding="utf-8")

        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["byNoteType"], {"episode": 2})
        self.assertTrue(payload["contentFree"])
        self.assertEqual(
            preview["coverage"][
                "forwardWriteRejections"
            ]["count"],
            2,
        )
        self.assertNotIn("Canary Secret", raw)
        self.assertNotIn("private body", raw)
        self.assertNotIn('"title"', raw)
        self.assertNotIn('"body"', raw)
        self.assertNotIn('"sourceRefs"', raw)

    def test_corrupt_forward_counter_values_fail_closed_to_zero(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter_path = root / "memory_index" / (
                "memory_provenance_"
                "forward_write_rejections.json"
            )
            counter_path.parent.mkdir(parents=True)
            counter_path.write_text(
                json.dumps(
                    {
                        "schema": (
                            "memory.provenance."
                            "forward-write-rejections.v1"
                        ),
                        "count": "not-a-number",
                        "byNoteType": {
                            "episode": {"invalid": True}
                        },
                    }
                ),
                encoding="utf-8",
            )
            preview = memory_provenance_backfill_preview(
                root=root
            )

        self.assertEqual(
            preview["coverage"]["forwardWriteRejections"],
            {
                "count": 0,
                "byNoteType": {"episode": 0},
            },
        )


class MemoryProvenanceManualSelectionTests(
    unittest.TestCase
):
    def create_fixture(
        self,
        root: Path,
    ) -> dict[str, object]:
        source_path = write_memory_vault_note(
            note_type="daily",
            title="Manual Grounded Source",
            body="grounded source body",
            source="conversation-turn-log",
            root=root,
        )
        source = parse_memory_note(source_path)
        target_path = write_memory_vault_note(
            note_type="episode",
            title="Signal Free Historical Target",
            body=(
                "manual target body must remain byte-for-byte "
                "stable"
            ),
            source="legacy-sub-llm-semantic-consolidation",
            root=root,
        )
        target = parse_memory_note(target_path)
        return {
            "source_path": source_path,
            "source": source,
            "target_path": target_path,
            "target": target,
        }

    def test_manual_source_selection_applies_without_inference(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            before_raw = target_path.read_text(encoding="utf-8")
            before_suffix = before_raw.split("---", 2)[2]
            audit_before = memory_provenance_backfill_preview(
                root=root
            )
            options = memory_provenance_manual_source_options(
                target.note_id,
                root=root,
            )
            preview = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
                    selection_mode="user_selected",
                    root=root,
                    now=lambda: 100.0,
                )
            )
            applied = apply_memory_provenance_backfill(
                target.note_id,
                preview["confirmToken"],
                root=root,
                now=lambda: 101.0,
            )
            after_raw = target_path.read_text(encoding="utf-8")
            after_note = parse_memory_note(
                target_path,
                after_raw,
            )
            audit_after = memory_provenance_backfill_preview(
                root=root
            )

        self.assertEqual(
            audit_before["summary"][
                "missingSignalTargetCount"
            ],
            1,
        )
        self.assertEqual(
            audit_before["manualReviewTargets"][0][
                "target"
            ]["id"],
            target.note_id,
        )
        self.assertEqual(
            options["schema"],
            "memory.provenance.manual-source-options.v1",
        )
        self.assertTrue(options["readOnly"])
        self.assertFalse(options["autoApply"])
        self.assertEqual(
            [item["id"] for item in options["sourceOptions"]],
            [source.note_id],
        )
        self.assertEqual(
            preview["selectionMode"],
            "user_selected",
        )
        self.assertEqual(
            preview["manualReason"],
            "missing_explicit_signal",
        )
        self.assertTrue(
            preview["consequences"]["userSelectedSources"]
        )
        self.assertFalse(
            preview["consequences"]["automaticInferenceUsed"]
        )
        self.assertTrue(applied["applied"])
        self.assertEqual(
            applied["selectionMode"],
            "user_selected",
        )
        self.assertIn(
            source.note_id,
            str(after_note.metadata["derived_from"]),
        )
        self.assertEqual(
            after_note.metadata[
                "provenance_backfill_method"
            ],
            "user-selected-source-note-ids",
        )
        self.assertEqual(
            after_raw.split("---", 2)[2],
            before_suffix,
        )
        self.assertEqual(
            audit_after["manualReviewTargets"],
            [],
        )
        self.assertEqual(
            audit_after["coverage"]["needsReviewCount"],
            0,
        )

    def test_manual_selection_refuses_exact_ambiguous_and_direct_targets(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            source_path = fixture["source_path"]
            source_ref = (
                source_path.relative_to(
                    root / "memory_vault"
                )
                .with_suffix("")
                .as_posix()
            )
            exact_path = write_memory_vault_note(
                note_type="episode",
                title="Exact Candidate Target",
                body="exact candidate",
                source=(
                    "legacy-sub-llm-semantic-consolidation"
                ),
                source_refs=[source_ref],
                root=root,
            )
            exact = parse_memory_note(exact_path)
            other_path = write_memory_vault_note(
                note_type="daily",
                title="Other Direct Source",
                body="other source",
                source="conversation-turn-log",
                root=root,
            )
            other = parse_memory_note(other_path)
            ambiguous_path = write_memory_vault_note(
                note_type="episode",
                title="Ambiguous Manual Target",
                body="ambiguous manual",
                source=(
                    "legacy-sub-llm-semantic-consolidation"
                ),
                source_refs=[source_ref],
                evidence_hashes=[other.source_hash],
                root=root,
            )
            ambiguous = parse_memory_note(ambiguous_path)

            exact_result = (
                preview_memory_provenance_backfill_application(
                    exact.note_id,
                    [source.note_id],
                    selection_mode="user_selected",
                    root=root,
                )
            )
            ambiguous_result = (
                preview_memory_provenance_backfill_application(
                    ambiguous.note_id,
                    [source.note_id],
                    selection_mode="user_selected",
                    root=root,
                )
            )
            direct_result = (
                preview_memory_provenance_backfill_application(
                    source.note_id,
                    [other.note_id],
                    selection_mode="user_selected",
                    root=root,
                )
            )
            exact_options = (
                memory_provenance_manual_source_options(
                    exact.note_id,
                    root=root,
                )
            )

        self.assertEqual(
            exact_result["error"],
            "memory_provenance_manual_exact_candidate_available",
        )
        self.assertEqual(
            exact_options["error"],
            "memory_provenance_manual_exact_candidate_available",
        )
        self.assertEqual(
            ambiguous_result["error"],
            "memory_provenance_backfill_ambiguous",
        )
        self.assertEqual(
            direct_result["error"],
            "memory_provenance_manual_target_ineligible",
        )

    def test_manual_selection_refuses_ungrounded_hidden_and_cycle_sources(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            ungrounded_path = write_memory_vault_note(
                note_type="concept",
                title="Ungrounded Source",
                body="ungrounded source",
                source="legacy-importer",
                root=root,
            )
            ungrounded = parse_memory_note(ungrounded_path)
            descendant_path = write_memory_vault_note(
                note_type="concept",
                title="Target Descendant",
                body="depends on the target",
                source="semantic-consolidation",
                derived_from=[target.note_id],
                root=root,
            )
            descendant = parse_memory_note(descendant_path)

            ungrounded_result = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [ungrounded.note_id],
                    selection_mode="user_selected",
                    root=root,
                )
            )
            cycle_result = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [descendant.note_id],
                    selection_mode="user_selected",
                    root=root,
                )
            )
            update_memory_vault_user_note(
                source.note_id,
                "hide",
                root=root,
            )
            hidden_result = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
                    selection_mode="user_selected",
                    root=root,
                )
            )
            options = memory_provenance_manual_source_options(
                target.note_id,
                root=root,
            )

        self.assertEqual(
            ungrounded_result["error"],
            "memory_provenance_manual_source_ungrounded",
        )
        self.assertEqual(
            cycle_result["error"],
            "memory_provenance_manual_cycle",
        )
        self.assertEqual(
            hidden_result["error"],
            "memory_provenance_source_hidden",
        )
        self.assertEqual(options["sourceOptions"], [])

    def test_manual_preview_refuses_any_graph_change(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            preview = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
                    selection_mode="user_selected",
                    root=root,
                    now=lambda: 500.0,
                )
            )
            write_memory_vault_note(
                note_type="daily",
                title="Unrelated Manual Graph Change",
                body="new direct source changes graph fingerprint",
                source="conversation-turn-log",
                root=root,
            )
            before_apply = target_path.read_text(
                encoding="utf-8"
            )
            result = apply_memory_provenance_backfill(
                target.note_id,
                preview["confirmToken"],
                root=root,
                now=lambda: 501.0,
            )
            after_apply = target_path.read_text(
                encoding="utf-8"
            )

        self.assertEqual(
            result["error"],
            "memory_provenance_backfill_changed_since_preview",
        )
        self.assertEqual(after_apply, before_apply)
        self.assertNotIn(
            "provenance_backfilled_at",
            after_apply,
        )

    def test_hidden_target_or_source_never_allows_exact_apply(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            source_path = fixture["source_path"]
            source_ref = (
                source_path.relative_to(
                    root / "memory_vault"
                )
                .with_suffix("")
                .as_posix()
            )
            exact_path = write_memory_vault_note(
                note_type="episode",
                title="Hidden Exact Target",
                body="hidden target body",
                source=(
                    "legacy-sub-llm-semantic-consolidation"
                ),
                source_refs=[source_ref],
                root=root,
            )
            exact = parse_memory_note(exact_path)
            update_memory_vault_user_note(
                exact.note_id,
                "hide",
                root=root,
            )
            hidden_target_audit = (
                memory_provenance_backfill_preview(
                    root=root
                )
            )
            hidden_target_result = (
                preview_memory_provenance_backfill_application(
                    exact.note_id,
                    [source.note_id],
                    root=root,
                )
            )
            update_memory_vault_user_note(
                exact.note_id,
                "unhide",
                root=root,
            )
            update_memory_vault_user_note(
                source.note_id,
                "hide",
                root=root,
            )
            hidden_source_audit = (
                memory_provenance_backfill_preview(
                    root=root
                )
            )
            hidden_source_result = (
                preview_memory_provenance_backfill_application(
                    exact.note_id,
                    [source.note_id],
                    root=root,
                )
            )

        target_candidate = next(
            item
            for item in hidden_target_audit["candidates"]
            if item["target"]["id"] == exact.note_id
        )
        source_candidate = next(
            item
            for item in hidden_source_audit["candidates"]
            if item["target"]["id"] == exact.note_id
        )
        self.assertFalse(target_candidate["canApply"])
        self.assertEqual(
            target_candidate["applyBlocker"],
            "memory_note_hidden",
        )
        self.assertEqual(
            hidden_target_result["error"],
            "memory_provenance_backfill_protected",
        )
        self.assertEqual(
            hidden_target_result["reason"],
            "memory_note_hidden",
        )
        self.assertFalse(source_candidate["canApply"])
        self.assertEqual(
            source_candidate["applyBlocker"],
            "memory_provenance_source_hidden",
        )
        self.assertEqual(
            hidden_source_result["error"],
            "memory_provenance_source_hidden",
        )


if __name__ == "__main__":
    unittest.main()
