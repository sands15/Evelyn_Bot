from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
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

from evelyn_core import memory_vault  # noqa: E402
from evelyn_core.memory_vault import (  # noqa: E402
    apply_memory_provenance_backfill,
    memory_provenance_backfill_preview,
    memory_vault_user_note,
    parse_memory_note,
    preview_memory_provenance_backfill_application,
    write_memory_vault_note,
)


FRESH_PROCESS_APPLY = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.memory_vault import (
        apply_memory_provenance_backfill,
    )

    result = apply_memory_provenance_backfill(
        sys.argv[2],
        sys.argv[3],
        root=Path(sys.argv[1]),
    )
    print(json.dumps(result, ensure_ascii=False))
    """
)

FRESH_PROCESS_READ = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.memory_vault import memory_vault_user_note

    result = memory_vault_user_note(
        sys.argv[2],
        root=Path(sys.argv[1]),
    )
    print(json.dumps(result, ensure_ascii=False))
    """
)


class MemoryProvenanceBackfillTests(unittest.TestCase):
    def create_fixture(
        self,
        root: Path,
    ) -> dict[str, object]:
        source_path = write_memory_vault_note(
            note_type="daily",
            title="Backfill Source",
            body="source evidence body",
            source="conversation-turn-log",
            root=root,
        )
        source = parse_memory_note(source_path)
        source_ref = (
            source_path.relative_to(
                root / "memory_vault"
            )
            .with_suffix("")
            .as_posix()
        )
        target_path = write_memory_vault_note(
            note_type="episode",
            title="Legacy Derived Candidate",
            body=(
                "target body must remain byte-for-byte "
                "stable after metadata backfill"
            ),
            source="legacy-sub-llm-semantic-consolidation",
            source_refs=[source_ref],
            evidence_hashes=[source.source_hash],
            root=root,
        )
        target = parse_memory_note(target_path)
        return {
            "source_path": source_path,
            "source": source,
            "source_ref": source_ref,
            "target_path": target_path,
            "target": target,
        }

    def test_two_step_apply_preserves_body_and_consumes_token(
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

            preview = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
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
            detail = memory_vault_user_note(
                target.note_id,
                root=root,
            )
            audit = memory_provenance_backfill_preview(
                root=root
            )
            reused = apply_memory_provenance_backfill(
                target.note_id,
                preview["confirmToken"],
                root=root,
                now=lambda: 102.0,
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(RUNTIME_ROOT)
            restarted = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    FRESH_PROCESS_READ,
                    str(root),
                    target.note_id,
                ],
                cwd=str(REPO_ROOT),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            restarted_detail = json.loads(
                restarted.stdout.strip().splitlines()[-1]
            )

        self.assertTrue(preview["ok"])
        self.assertEqual(
            preview["schema"],
            "memory.provenance.backfill-preview.v1",
        )
        self.assertEqual(
            preview["candidateState"],
            "verified",
        )
        self.assertTrue(applied["ok"])
        self.assertTrue(applied["applied"])
        self.assertNotEqual(
            applied["previousContentHash"],
            applied["contentHash"],
        )
        self.assertEqual(
            after_raw.split("---", 2)[2],
            before_suffix,
        )
        self.assertEqual(after_note.title, target.title)
        self.assertEqual(after_note.body, target.body)
        self.assertEqual(
            detail["card"]["provenance"]["derivedFrom"],
            [source.note_id],
        )
        self.assertEqual(
            restarted_detail["card"]["provenance"][
                "derivedFrom"
            ],
            [source.note_id],
        )
        self.assertEqual(
            after_note.metadata[
                "provenance_backfill_method"
            ],
            "exact-metadata-user-confirmed",
        )
        self.assertEqual(
            audit["summary"]["candidateTargetCount"],
            0,
        )
        self.assertFalse(reused["ok"])
        self.assertEqual(
            reused["error"],
            "memory_provenance_backfill_token_reused",
        )
        self.assertNotIn("body", applied)
        self.assertNotIn("title", applied)

    def test_target_source_or_graph_change_refuses_apply(
        self,
    ) -> None:
        mutations = ("target", "source", "graph")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    fixture = self.create_fixture(root)
                    source = fixture["source"]
                    target = fixture["target"]
                    target_path = fixture["target_path"]
                    source_path = fixture["source_path"]
                    target_before = target_path.read_text(
                        encoding="utf-8"
                    )
                    preview = (
                        preview_memory_provenance_backfill_application(
                            target.note_id,
                            [source.note_id],
                            root=root,
                            now=lambda: 200.0,
                        )
                    )
                    if mutation == "target":
                        target_path.write_text(
                            target_before.replace(
                                "target body",
                                "changed target body",
                            ),
                            encoding="utf-8",
                        )
                    elif mutation == "source":
                        source_path.write_text(
                            source_path.read_text(
                                encoding="utf-8"
                            ).replace(
                                "source evidence body",
                                "changed source evidence body",
                            ),
                            encoding="utf-8",
                        )
                    else:
                        write_memory_vault_note(
                            note_type="concept",
                            title="Unrelated Graph Change",
                            body="changes the audited graph fingerprint",
                            root=root,
                        )
                    target_at_apply = target_path.read_text(
                        encoding="utf-8"
                    )
                    result = apply_memory_provenance_backfill(
                        target.note_id,
                        preview["confirmToken"],
                        root=root,
                        now=lambda: 201.0,
                    )
                    target_after = target_path.read_text(
                        encoding="utf-8"
                    )

                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"],
                    (
                        "memory_provenance_backfill_changed_since_preview"
                    ),
                )
                self.assertEqual(target_after, target_at_apply)
                self.assertNotIn(
                    "provenance_backfilled_at",
                    target_after,
                )

    def test_ambiguous_wrong_source_and_expired_preview_are_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            wrong_source_path = write_memory_vault_note(
                note_type="daily",
                title="Wrong Source",
                body="wrong source body",
                source="conversation-turn-log",
                root=root,
            )
            wrong_source = parse_memory_note(
                wrong_source_path
            )
            mismatch = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [wrong_source.note_id],
                    root=root,
                )
            )
            preview = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
                    root=root,
                    now=lambda: 300.0,
                )
            )
            expired = apply_memory_provenance_backfill(
                target.note_id,
                preview["confirmToken"],
                root=root,
                now=lambda: 421.0,
            )

            source_b_path = write_memory_vault_note(
                note_type="daily",
                title="Ambiguous Source B",
                body="source B",
                source="conversation-turn-log",
                root=root,
            )
            source_b = parse_memory_note(source_b_path)
            source_ref = fixture["source_ref"]
            ambiguous_path = write_memory_vault_note(
                note_type="episode",
                title="Ambiguous Legacy Candidate",
                body="ambiguous body",
                source=(
                    "legacy-sub-llm-semantic-consolidation"
                ),
                source_refs=[source_ref],
                evidence_hashes=[source_b.source_hash],
                root=root,
            )
            ambiguous = parse_memory_note(ambiguous_path)
            ambiguous_result = (
                preview_memory_provenance_backfill_application(
                    ambiguous.note_id,
                    [source.note_id, source_b.note_id],
                    root=root,
                )
            )

        self.assertEqual(
            mismatch["error"],
            "memory_provenance_backfill_source_mismatch",
        )
        self.assertEqual(
            expired["error"],
            "memory_provenance_backfill_token_expired",
        )
        self.assertEqual(
            ambiguous_result["error"],
            "memory_provenance_backfill_ambiguous",
        )

    def test_atomic_write_failure_preserves_original_note(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            original = target_path.read_text(encoding="utf-8")
            preview = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
                    root=root,
                )
            )
            with patch.object(
                memory_vault,
                "atomic_text_write",
                side_effect=OSError("disk full"),
            ):
                result = apply_memory_provenance_backfill(
                    target.note_id,
                    preview["confirmToken"],
                    root=root,
                )
            after = target_path.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(
            result["error"],
            "memory_provenance_backfill_failed",
        )
        self.assertEqual(after, original)

    def test_preview_token_is_not_recovered_after_process_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = self.create_fixture(root)
            source = fixture["source"]
            target = fixture["target"]
            target_path = fixture["target_path"]
            before = target_path.read_text(encoding="utf-8")
            preview = (
                preview_memory_provenance_backfill_application(
                    target.note_id,
                    [source.note_id],
                    root=root,
                )
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(RUNTIME_ROOT)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    FRESH_PROCESS_APPLY,
                    str(root),
                    target.note_id,
                    preview["confirmToken"],
                ],
                cwd=str(REPO_ROOT),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(
                completed.stdout.strip().splitlines()[-1]
            )
            after = target_path.read_text(encoding="utf-8")

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"],
            "memory_provenance_backfill_token_invalid",
        )
        self.assertEqual(after, before)

    def test_new_derived_writer_requires_declared_source_ids(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(
                ValueError,
                "memory_derived_from_required",
            ):
                write_memory_vault_note(
                    note_type="episode",
                    title="Invalid New Derived Note",
                    body="must not be created",
                    source="sub-llm-semantic-consolidation",
                    root=root,
                )
            invalid_files = list(
                (root / "memory_vault").rglob(
                    "invalid-new-derived-note.md"
                )
            )
            runtime_path = write_memory_vault_note(
                note_type="concept",
                title="Direct Runtime Note",
                body="direct runtime evidence",
                root=root,
            )
            runtime_note = parse_memory_note(runtime_path)

        self.assertEqual(invalid_files, [])
        self.assertEqual(
            runtime_note.metadata["source"],
            "runtime",
        )


if __name__ == "__main__":
    unittest.main()
