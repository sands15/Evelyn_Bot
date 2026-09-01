from __future__ import annotations

import json
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.identity_review import (
    cleanup_identity_review_artifacts,
    export_identity_review,
    read_identity_review_rows,
)


class IdentityReviewExportTests(unittest.TestCase):
    def test_cleanup_rebuilds_registered_export_when_queue_is_already_clean(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            output_dir = root / "exports" / "registered"
            queue_path.write_text("", encoding="utf-8")
            output_dir.mkdir(parents=True)
            stale = "PRIVATE_ALREADY_REMOVED"
            (output_dir / "evelyn_identity_review.tsv").write_text(
                stale, encoding="utf-8"
            )
            (output_dir / "evelyn_identity_review.md").write_text(
                stale, encoding="utf-8"
            )
            (output_dir / "evelyn_identity_review_summary.json").write_text(
                json.dumps({"candidate_count": 1}), encoding="utf-8"
            )

            result = cleanup_identity_review_artifacts(
                time_predicate=lambda _row: True,
                lineage_predicate=lambda _lineage: True,
                queue_path=queue_path,
                registered_export_dirs=(output_dir,),
                allowed_export_root=root / "exports",
            )

            self.assertEqual(result, (0, 0, 0))
            for artifact in output_dir.iterdir():
                self.assertNotIn(stale, artifact.read_text(encoding="utf-8-sig"))

    def test_cleanup_removes_exact_lineage_and_reports_legacy_manual(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            output_dir = root / "exports" / "registered"
            rows = [
                {
                    "recorded_at": 100.0,
                    "lineage": {"turn": "target"},
                    "user_text": "PRIVATE_TARGET",
                    "status": "review_candidate",
                },
                {
                    "recorded_at": 100.0,
                    "user_text": "PRIVATE_LEGACY",
                    "status": "review_candidate",
                },
                {
                    "recorded_at": 200.0,
                    "lineage": {"turn": "survivor"},
                    "user_text": "SURVIVOR",
                    "status": "review_candidate",
                },
            ]
            queue_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            export_identity_review(input_path=queue_path, output_dir=output_dir)

            result = cleanup_identity_review_artifacts(
                time_predicate=lambda row: row.get("recorded_at") == 100.0,
                lineage_predicate=lambda lineage: lineage.get("turn") == "target",
                queue_path=queue_path,
                registered_export_dirs=(output_dir,),
                allowed_export_root=root / "exports",
            )

            self.assertEqual(result, (1, 0, 1))
            fresh = queue_path.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE_TARGET", fresh)
            self.assertIn("PRIVATE_LEGACY", fresh)
            self.assertIn("SURVIVOR", fresh)
            for artifact in output_dir.iterdir():
                self.assertNotIn("PRIVATE_TARGET", artifact.read_text(encoding="utf-8-sig"))

    def test_cleanup_fails_closed_for_malformed_queue(self) -> None:
        with TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "queue.jsonl"
            original = "{malformed\n"
            queue_path.write_text(original, encoding="utf-8")

            result = cleanup_identity_review_artifacts(
                time_predicate=lambda _row: True,
                lineage_predicate=lambda _lineage: True,
                queue_path=queue_path,
            )

            self.assertEqual(result, (0, 1, 1))
            self.assertEqual(queue_path.read_text(encoding="utf-8"), original)

    def test_cleanup_rejects_unregistered_output_outside_allowed_root(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            original = json.dumps(
                {
                    "recorded_at": 100.0,
                    "lineage": {"turn": "target"},
                    "user_text": "PRIVATE_TARGET",
                }
            ) + "\n"
            queue_path.write_text(original, encoding="utf-8")

            result = cleanup_identity_review_artifacts(
                time_predicate=lambda _row: True,
                lineage_predicate=lambda _lineage: True,
                queue_path=queue_path,
                registered_export_dirs=(root / "outside",),
                allowed_export_root=root / "allowed",
            )

            self.assertEqual(result, (0, 1, 1))
            self.assertEqual(queue_path.read_text(encoding="utf-8"), original)

    def test_exports_pending_candidates_to_review_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue.jsonl"
            output_dir = root / "out"
            queue_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "recorded_at": 1780830000.0,
                                "source": "text",
                                "labels": ["tone_feedback", "suffix_balance"],
                                "user_text": "말투가 아직 딱딱하고 ~할게가 많아.",
                                "assistant_text": "응, 바로 고쳐볼게.",
                                "status": "review_candidate",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "source": "text",
                                "labels": ["identity_feedback"],
                                "user_text": "이건 이미 반영된 후보야.",
                                "assistant_text": "반영 완료.",
                                "status": "accepted",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            rows = read_identity_review_rows(queue_path)
            self.assertEqual(len(rows), 1)
            self.assertIn("tone_feedback", rows[0]["labels"])

            result = export_identity_review(input_path=queue_path, output_dir=output_dir)

            self.assertEqual(result["candidate_count"], 1)
            tsv = (output_dir / "evelyn_identity_review.tsv").read_text(encoding="utf-8-sig")
            markdown = (output_dir / "evelyn_identity_review.md").read_text(encoding="utf-8")
            summary = json.loads((output_dir / "evelyn_identity_review_summary.json").read_text(encoding="utf-8"))

            self.assertIn("review_action", tsv)
            self.assertIn("말투가 아직 딱딱하고", tsv)
            self.assertIn("Evelyn Identity Review", markdown)
            self.assertIn("suffix_balance", markdown)
            self.assertEqual(summary["candidate_count"], 1)
            self.assertEqual(summary["labels"]["tone_feedback"], 1)

    def test_include_all_keeps_handled_candidates(self) -> None:
        with TemporaryDirectory() as tmp:
            queue_path = Path(tmp) / "queue.jsonl"
            queue_path.write_text(
                json.dumps(
                    {
                        "labels": ["identity_feedback"],
                        "user_text": "반영된 후보도 같이 볼래.",
                        "assistant_text": "응.",
                        "status": "accepted",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(read_identity_review_rows(queue_path), [])
            self.assertEqual(len(read_identity_review_rows(queue_path, include_all=True)), 1)


if __name__ == "__main__":
    unittest.main()
