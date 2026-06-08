from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.identity_review import export_identity_review, read_identity_review_rows


class IdentityReviewExportTests(unittest.TestCase):
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
