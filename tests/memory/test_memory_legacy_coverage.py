from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_legacy_coverage import (  # noqa: E402
    LEGACY_MEMORY_CONTEXT_COVERAGE_SCHEMA,
    summarize_legacy_memory_context_coverage,
)


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def _bucket(payload: dict, dimension: str, key: str) -> dict:
    return next(row for row in payload[dimension] if row["key"] == key)


class LegacyMemoryContextCoverageTests(unittest.TestCase):
    def test_coverage_counts_stored_items_without_exposing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scope = root / "guild_7"
            scope.mkdir(parents=True)
            summary = "PRIVATE_SUMMARY_CANARY"
            (scope / "rolling_summary.txt").write_text(summary, encoding="utf-8")
            (scope / "rolling_summary.provenance.json").write_text(
                json.dumps(
                    {
                        "schema": "memory.legacy-evidence.v1",
                        "evidence_kind": "derived_summary",
                        "evidence_id": "summary:derived:one",
                        "source_evidence_ids": ["turn:source:user"],
                        "source_turn_ids": ["source"],
                        "content_sha256": hashlib.sha256(
                            summary.encode("utf-8")
                        ).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            _write_jsonl(
                scope / "raw_transcript.jsonl",
                [
                    {
                        "role": "user",
                        "text": "PRIVATE_RAW_ATTRIBUTED",
                        "source_turn_id": "turn-a",
                        "evidence_kind": "conversation_turn",
                        "evidence_id": "turn:turn-a:user",
                    },
                    {"role": "assistant", "text": "PRIVATE_RAW_LEGACY"},
                    "{malformed",
                    {"role": "user", "text": ""},
                ],
            )
            _write_jsonl(
                scope / "durable_facts.jsonl",
                [
                    {
                        "text": "PRIVATE_FACT_ATTRIBUTED",
                        "evidence_kind": "derived_fact",
                        "evidence_id": "fact:derived:one",
                        "source_evidence_ids": ["turn:turn-a:user"],
                        "source_turn_ids": ["turn-a"],
                    },
                    {"text": "PRIVATE_FACT_LEGACY"},
                ],
            )
            _write_jsonl(
                scope / "open_questions.jsonl",
                [{"text": "PRIVATE_QUESTION_LEGACY"}],
            )
            _write_jsonl(
                scope / "vault" / "raw" / "2026-07-31.jsonl",
                [
                    {
                        "role": "assistant",
                        "text": "PRIVATE_VAULT_RAW",
                        "source_turn_id": "turn-b",
                        "evidence_kind": "conversation_turn",
                        "evidence_id": "turn:turn-b:assistant",
                    }
                ],
            )
            _write_jsonl(
                scope / "person_private-person" / "raw_transcript.jsonl",
                [{"role": "user", "text": "PRIVATE_PERSON_SCOPE_RAW"}],
            )

            payload = summarize_legacy_memory_context_coverage(
                root=root,
                now=datetime(2026, 7, 31, tzinfo=timezone.utc),
            )

        self.assertEqual(payload["schema"], LEGACY_MEMORY_CONTEXT_COVERAGE_SCHEMA)
        self.assertEqual(payload["policy"], "memory.context-use.v1")
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["contentFree"])
        self.assertFalse(payload["identifiersIncluded"])
        self.assertFalse(payload["storageLocationsIncluded"])
        self.assertFalse(payload["transcriptsIncluded"])
        self.assertEqual(payload["groundingState"], "partial")
        self.assertEqual(payload["totalStoredItemCount"], 8)
        self.assertEqual(payload["attributedStoredItemCount"], 4)
        self.assertEqual(payload["confirmOnlyStoredItemCount"], 4)
        self.assertEqual(payload["missingEvidenceItemCount"], 4)
        self.assertEqual(payload["invalidEvidenceItemCount"], 0)
        self.assertEqual(payload["malformedLineCount"], 1)
        self.assertEqual(payload["scopeCount"], 2)
        self.assertEqual(payload["scannedFileCount"], 7)
        self.assertEqual(_bucket(payload, "byKind", "summary")["attributedStoredItemCount"], 1)
        self.assertEqual(_bucket(payload, "byKind", "raw")["totalStoredItemCount"], 4)
        self.assertEqual(_bucket(payload, "byKind", "fact")["confirmOnlyStoredItemCount"], 1)
        self.assertEqual(_bucket(payload, "byStorage", "vault")["totalStoredItemCount"], 1)
        self.assertEqual(
            _bucket(payload, "byScopeType", "person")["confirmOnlyStoredItemCount"],
            1,
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        for private_value in (
            "PRIVATE_SUMMARY_CANARY",
            "PRIVATE_RAW_ATTRIBUTED",
            "PRIVATE_RAW_LEGACY",
            "PRIVATE_FACT_ATTRIBUTED",
            "PRIVATE_FACT_LEGACY",
            "PRIVATE_QUESTION_LEGACY",
            "PRIVATE_VAULT_RAW",
            "PRIVATE_PERSON_SCOPE_RAW",
            "guild_7",
            "private-person",
            "turn:turn-a:user",
        ):
            self.assertNotIn(private_value, serialized)

    def test_invalid_evidence_is_confirmation_only_and_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scope = root / "guild_9"
            scope.mkdir(parents=True)
            (scope / "rolling_summary.txt").write_text(
                "PRIVATE_INVALID_SUMMARY",
                encoding="utf-8",
            )
            (scope / "rolling_summary.provenance.json").write_text(
                json.dumps(
                    {
                        "schema": "memory.legacy-evidence.v1",
                        "evidence_kind": "derived_summary",
                        "evidence_id": "summary:derived:bad",
                        "source_evidence_ids": ["turn:source:user"],
                        "content_sha256": "wrong",
                    }
                ),
                encoding="utf-8",
            )
            _write_jsonl(
                scope / "raw_transcript.jsonl",
                [
                    {
                        "role": "assistant",
                        "text": "PRIVATE_INVALID_RAW",
                        "source_turn_id": "turn-c",
                        "evidence_kind": "conversation_turn",
                        "evidence_id": "turn:turn-c:user",
                    }
                ],
            )

            payload = summarize_legacy_memory_context_coverage(root=root)

        self.assertEqual(payload["groundingState"], "unattributed")
        self.assertEqual(payload["totalStoredItemCount"], 2)
        self.assertEqual(payload["attributedStoredItemCount"], 0)
        self.assertEqual(payload["confirmOnlyStoredItemCount"], 2)
        self.assertEqual(payload["invalidEvidenceItemCount"], 2)
        self.assertEqual(payload["missingEvidenceItemCount"], 0)
        self.assertEqual(payload["malformedFileCount"], 1)

    def test_missing_root_returns_empty_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "does-not-exist"

            payload = summarize_legacy_memory_context_coverage(root=root)

            self.assertFalse(root.exists())

        self.assertEqual(payload["groundingState"], "empty")
        self.assertEqual(payload["totalStoredItemCount"], 0)
        self.assertEqual(payload["attributionRatio"], 1.0)


if __name__ == "__main__":
    unittest.main()
