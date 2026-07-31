from __future__ import annotations

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

from evelyn_core.search_followup_recovery import (  # noqa: E402
    SearchFollowupRecoveryJournal,
)


class SearchFollowupRecoveryJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "active.json"
        self.ids = iter(
            [
                "search-followup-000000000000000000000001",
                "search-followup-000000000000000000000002",
            ]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def journal(self) -> SearchFollowupRecoveryJournal:
        return SearchFollowupRecoveryJournal(
            path=self.path,
            wall_time=lambda: 1000.0,
            intent_id_factory=lambda: next(self.ids),
        )

    def begin(self, journal: SearchFollowupRecoveryJournal) -> str:
        intent_id = journal.begin(
            guild_id=7,
            session_key="guild:7:text:8:user:9",
            source="text",
            turn_id="turn-1",
            room_key="text:8",
            person_key="user:9",
            session_memory_key="guild:7:text:8:user:9",
            channel_id=8,
            reply_to_message_id=10,
            request_user_text="민감한 원문 검색 질문",
            request_answer_text="찾아보고 알려줄게",
            query="민감한 실제 검색어",
            continuity_generation=4,
        )
        assert intent_id is not None
        return intent_id

    def test_round_trip_is_content_free_and_rollback_protected(self) -> None:
        journal = self.journal()
        intent_id = self.begin(journal)
        journal.begin_delivery_prepare(
            intent_id,
            answer="민감한 검색 결과",
            display_text="민감한 검색 결과",
        )
        journal.mark_delivery_ready(
            intent_id,
            answer="민감한 검색 결과",
            display_text="민감한 검색 결과",
            continuity_generation=5,
        )
        journal.mark_delivery_attempted(intent_id)

        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("민감한", raw)
        restored = self.journal()
        self.assertEqual(
            restored.pending()[0]["phase"],
            "delivery_attempted",
        )
        self.assertTrue(restored.public_status()["rollbackProtected"])

    def test_same_session_supersedes_prior_intent(self) -> None:
        journal = self.journal()
        first = self.begin(journal)
        second = self.begin(journal)

        self.assertNotEqual(first, second)
        self.assertFalse(journal.is_active(first))
        self.assertTrue(journal.is_active(second))
        self.assertEqual(len(journal.pending()), 1)

    def test_deleted_or_tampered_journal_fails_closed(self) -> None:
        journal = self.journal()
        self.begin(journal)
        self.path.unlink()

        deleted = self.journal()
        self.assertEqual(deleted.pending(), [])
        self.assertEqual(deleted.public_status()["state"], "corrupt")

        self.path.write_text("{}", encoding="utf-8")
        tampered = self.journal()
        self.assertEqual(tampered.pending(), [])
        self.assertFalse(tampered.public_status()["rollbackProtected"])

    def test_one_ahead_payload_repairs_lagging_head(self) -> None:
        journal = self.journal()
        intent_id = self.begin(journal)
        old_head = json.loads(
            journal.head_path.read_text(encoding="utf-8")
        )
        journal.record_attempt_failure(
            intent_id,
            error_code="search_followup_execution_failed",
        )
        journal.head_path.write_text(
            json.dumps(old_head),
            encoding="utf-8",
        )

        restored = self.journal()
        self.assertEqual(
            restored.pending()[0]["attemptCount"],
            1,
        )
        repaired_head = json.loads(
            journal.head_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            repaired_head["generation"],
            restored.public_status()["generation"],
        )

    def test_guild_reset_removes_pending_metadata_durably(self) -> None:
        journal = self.journal()
        self.begin(journal)

        self.assertEqual(journal.reset_guild(7), 1)
        self.assertEqual(journal.pending(), [])
        self.assertEqual(self.journal().pending(), [])


if __name__ == "__main__":
    unittest.main()
