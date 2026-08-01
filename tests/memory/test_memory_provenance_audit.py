from __future__ import annotations

import hashlib
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

from evelyn_core import memory_vault as memory_vault_module  # noqa: E402
from evelyn_core.memory_provenance_audit import (  # noqa: E402
    ProvenanceAuditNode,
    audit_missing_derivations,
    summarize_provenance_coverage,
)
from evelyn_core.memory_vault import (  # noqa: E402
    delete_memory_vault_user_note,
    memory_provenance_backfill_preview,
    memory_quarantine_status,
    memory_vault_user_snapshot,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    write_memory_vault_note,
)


class ProvenanceAuditContractTests(unittest.TestCase):
    def node(
        self,
        note_id: str,
        *,
        note_type: str = "concept",
        source_type: str = "derived",
        source_refs: tuple[str, ...] = (),
        derived_from: tuple[str, ...] = (),
        origin_derived_from: tuple[str, ...] = (),
        evidence_hashes: tuple[str, ...] = (),
        reference_aliases: tuple[str, ...] = (),
        evidence_aliases: tuple[str, ...] = (),
        explicitly_detached: bool = False,
    ) -> ProvenanceAuditNode:
        return ProvenanceAuditNode(
            note_id=note_id,
            note_type=note_type,
            source_type=source_type,
            source_refs=source_refs,
            derived_from=derived_from,
            origin_derived_from=origin_derived_from,
            evidence_hashes=evidence_hashes,
            reference_aliases=reference_aliases,
            evidence_aliases=evidence_aliases,
            explicitly_detached=explicitly_detached,
        )

    def test_exact_ref_and_hash_are_cross_verified(self) -> None:
        source = self.node(
            "source",
            source_type="conversation",
            reference_aliases=("daily/2026-07-30",),
            evidence_aliases=("abc123",),
        )
        target = self.node(
            "target",
            source_refs=("daily\\2026-07-30",),
            evidence_hashes=("ABC123",),
        )

        result = audit_missing_derivations([source, target])

        self.assertEqual(result.verified_count, 1)
        self.assertEqual(
            result.candidates[0].candidate_source_ids,
            ("source",),
        )
        self.assertEqual(
            result.candidates[0].signals[0].reason_codes,
            ("exact_source_ref", "exact_evidence_hash"),
        )

    def test_content_similarity_is_not_an_audit_signal(self) -> None:
        source = self.node(
            "source",
            source_type="conversation",
        )
        target = self.node(
            "target",
            source_refs=("external/transcript",),
            evidence_hashes=("unmatched",),
        )

        result = audit_missing_derivations([source, target])

        self.assertEqual(result.candidates, ())
        self.assertEqual(result.unmatched_target_count, 1)

    def test_conflicting_exact_signals_stay_ambiguous(self) -> None:
        source_a = self.node(
            "source-a",
            source_type="conversation",
            reference_aliases=("daily/a",),
            evidence_aliases=("hash-a",),
        )
        source_b = self.node(
            "source-b",
            source_type="conversation",
            reference_aliases=("daily/b",),
            evidence_aliases=("hash-b",),
        )
        target = self.node(
            "target",
            source_refs=("daily/a",),
            evidence_hashes=("hash-b",),
        )

        result = audit_missing_derivations(
            [source_a, source_b, target]
        )

        self.assertEqual(result.ambiguous_count, 1)
        self.assertEqual(
            result.candidates[0].candidate_source_ids,
            ("source-a", "source-b"),
        )
        self.assertIn(
            "conflicting_exact_signals",
            result.candidates[0].reason_codes,
        )

    def test_user_detach_and_cycles_are_never_suggested(self) -> None:
        detached = self.node(
            "detached",
            source_refs=("source",),
            evidence_hashes=("hash-source",),
            origin_derived_from=("source",),
            explicitly_detached=True,
        )
        cyclic_target = self.node(
            "cyclic-target",
            source_refs=("cyclic-source",),
        )
        cyclic_source = self.node(
            "cyclic-source",
            derived_from=("cyclic-target",),
            reference_aliases=("cyclic-source",),
        )
        source = self.node(
            "source",
            source_type="conversation",
            reference_aliases=("source",),
            evidence_aliases=("hash-source",),
        )

        result = audit_missing_derivations(
            [detached, cyclic_target, cyclic_source, source]
        )

        self.assertEqual(result.explicitly_detached_count, 1)
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.cycle_rejected_signal_count, 1)

    def test_coverage_dimensions_use_closed_aggregated_enums(
        self,
    ) -> None:
        source_canary = "PRIVATE source transcript canary sentence"
        type_canary = "PRIVATE note type transcript canary sentence"
        coverage = summarize_provenance_coverage(
            [
                self.node(
                    "one",
                    note_type=type_canary,
                    source_type=source_canary,
                ),
                self.node(
                    "two",
                    note_type="concepts",
                    source_type="conversation-turn-log",
                ),
                self.node(
                    "three",
                    note_type="semantic",
                    source_type="conversation",
                ),
            ],
            forward_write_rejections={
                "count": 4,
                "byNoteType": {
                    type_canary: 2,
                    "concepts": 1,
                    "semantic": 1,
                },
            },
        )
        encoded = json.dumps(coverage, ensure_ascii=False)

        self.assertNotIn(source_canary, encoded)
        self.assertNotIn(type_canary, encoded)
        self.assertEqual(
            {
                row["key"]: row["totalNoteCount"]
                for row in coverage["bySourceType"]
            },
            {"conversation": 2, "unknown": 1},
        )
        self.assertEqual(
            {
                row["key"]: row["totalNoteCount"]
                for row in coverage["byNoteType"]
            },
            {"concept": 2, "unknown": 1},
        )
        self.assertEqual(
            coverage["forwardWriteRejections"]["byNoteType"],
            {"concept": 2, "unknown": 2},
        )


class ProvenanceAuditIntegrationTests(unittest.TestCase):
    def test_persisted_coverage_reprojects_free_form_dimensions(
        self,
    ) -> None:
        source_canary = "PRIVATE persisted source transcript sentence"
        type_canary = "PRIVATE persisted note type transcript sentence"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory_vault_module.ensure_memory_vault_layout(root)
            persisted = (
                memory_vault_module
                ._memory_persisted_provenance_audit(  # noqa: SLF001
                    root=root,
                    graph_fingerprint="0" * 64,
                    audit=audit_missing_derivations([]),
                    coverage={
                        "schema": "memory.provenance.coverage.v1",
                        "contentFree": True,
                        "bySourceType": [
                            {
                                "key": source_canary,
                                "totalNoteCount": 1,
                                "groundedNoteCount": 0,
                                "needsReviewCount": 1,
                            },
                            {
                                "key": "runtime",
                                "totalNoteCount": 2,
                                "groundedNoteCount": 2,
                                "needsReviewCount": 0,
                            },
                        ],
                        "byNoteType": [
                            {
                                "key": type_canary,
                                "totalNoteCount": 1,
                                "groundedNoteCount": 0,
                                "needsReviewCount": 1,
                            },
                            {
                                "key": "concepts",
                                "totalNoteCount": 2,
                                "groundedNoteCount": 2,
                                "needsReviewCount": 0,
                            },
                        ],
                        "forwardWriteRejections": {
                            "count": 3,
                            "byNoteType": {
                                type_canary: 1,
                                "concepts": 2,
                            },
                        },
                    },
                    legacy_context_coverage={},
                )
            )
            report_path = (
                root
                / "memory_index"
                / "memory_provenance_backfill_audit.json"
            )
            persisted_raw = report_path.read_text(
                encoding="utf-8"
            )

        self.assertNotIn(source_canary, persisted_raw)
        self.assertNotIn(type_canary, persisted_raw)
        self.assertEqual(
            {
                row["key"]: row["totalNoteCount"]
                for row in persisted["coverage"]["bySourceType"]
            },
            {"runtime": 2, "unknown": 1},
        )
        self.assertEqual(
            {
                row["key"]: row["totalNoteCount"]
                for row in persisted["coverage"]["byNoteType"]
            },
            {"concept": 2, "unknown": 1},
        )
        self.assertEqual(
            persisted["coverage"]["forwardWriteRejections"][
                "byNoteType"
            ],
            {"concept": 2, "unknown": 1},
        )

    def test_preview_is_read_only_and_persisted_report_is_content_free(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = write_memory_vault_note(
                note_type="daily",
                title="Opaque Source Title Canary",
                body="private source body canary",
                source="conversation-turn-log",
                root=root,
            )
            source = parse_memory_note(source_path)
            source_digest = hashlib.sha1(
                source.body.encode("utf-8")
            ).hexdigest()[:12]
            source_ref = (
                source_path.relative_to(
                    root / "memory_vault"
                )
                .with_suffix("")
                .as_posix()
            )
            target_path = write_memory_vault_note(
                note_type="episode",
                title="Opaque Target Title Canary",
                body="private target body canary",
                source=(
                    "legacy-sub-llm-semantic-consolidation"
                ),
                source_refs=[source_ref],
                evidence_hashes=[source_digest],
                root=root,
            )
            source_before = source_path.read_bytes()
            target_before = target_path.read_bytes()

            preview = memory_provenance_backfill_preview(
                root=root
            )
            report_path = (
                root
                / "memory_index"
                / "memory_provenance_backfill_audit.json"
            )
            persisted_raw = report_path.read_text(
                encoding="utf-8"
            )
            persisted = json.loads(persisted_raw)

            self.assertEqual(
                source_path.read_bytes(),
                source_before,
            )
            self.assertEqual(
                target_path.read_bytes(),
                target_before,
            )

        self.assertTrue(preview["readOnly"])
        self.assertFalse(preview["autoApply"])
        self.assertFalse(preview["contentSimilarityUsed"])
        self.assertEqual(preview["policy"], "exact_metadata_only")
        self.assertEqual(
            preview["summary"]["verifiedCount"],
            1,
        )
        self.assertEqual(
            preview["candidates"][0]["candidateSources"][0][
                "id"
            ],
            source.note_id,
        )
        self.assertTrue(
            preview["candidates"][0]["canApply"]
        )
        self.assertEqual(
            persisted["schema"],
            "memory.provenance.backfill-audit.v1",
        )
        self.assertNotIn("Opaque Source Title Canary", persisted_raw)
        self.assertNotIn("Opaque Target Title Canary", persisted_raw)
        self.assertNotIn("private source body canary", persisted_raw)
        self.assertNotIn("private target body canary", persisted_raw)
        self.assertNotIn(source_ref, persisted_raw)
        self.assertNotIn(source.source_hash, persisted_raw)
        self.assertNotIn(source_digest, persisted_raw)
        self.assertNotIn("title", persisted_raw.lower())
        self.assertNotIn("body", persisted_raw.lower())
        self.assertNotIn("path", persisted_raw.lower())

    def test_quarantine_status_exposes_count_and_oldest_age(
        self,
    ) -> None:
        status = memory_quarantine_status(
            entries={
                "one": {
                    "quarantinedAt": "2026-07-30T00:00:00Z",
                    "remainingSourceIds": ["live"],
                    "blockedSourceIds": [],
                },
                "two": {
                    "quarantinedAt": "not-a-time",
                    "remainingSourceIds": ["blocked"],
                    "blockedSourceIds": ["blocked"],
                },
            },
            now=datetime(
                2026,
                7,
                30,
                1,
                0,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(status["state"], "pending")
        self.assertEqual(status["count"], 2)
        self.assertEqual(status["recompositionReadyCount"], 1)
        self.assertEqual(status["blockedCount"], 1)
        self.assertEqual(status["oldestAgeSeconds"], 3600)
        self.assertEqual(status["unknownAgeCount"], 1)

    def test_snapshot_reports_real_quarantine_without_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_a_path = write_memory_vault_note(
                note_type="concept",
                title="Status Source A",
                body="source A",
                source="control-page-user",
                root=root,
            )
            source_b_path = write_memory_vault_note(
                note_type="concept",
                title="Status Source B",
                body="source B",
                source="control-page-user",
                root=root,
            )
            source_a = parse_memory_note(source_a_path)
            source_b = parse_memory_note(source_b_path)
            write_memory_vault_note(
                note_type="episode",
                title="Status Multi",
                body="multi source result",
                source="sub-llm-semantic-consolidation",
                derived_from=[
                    source_a.note_id,
                    source_b.note_id,
                ],
                root=root,
            )
            preview = preview_memory_vault_user_note_deletion(
                source_a.note_id,
                root=root,
            )
            applied = delete_memory_vault_user_note(
                source_a.note_id,
                preview["confirmToken"],
                root=root,
            )
            snapshot = memory_vault_user_snapshot(root=root)

        self.assertTrue(applied["ok"])
        self.assertEqual(
            snapshot["quarantineStatus"]["count"],
            1,
        )
        self.assertEqual(
            snapshot["quarantineStatus"]["state"],
            "pending",
        )
        self.assertNotIn(
            "noteIds",
            snapshot["quarantineStatus"],
        )


if __name__ == "__main__":
    unittest.main()
