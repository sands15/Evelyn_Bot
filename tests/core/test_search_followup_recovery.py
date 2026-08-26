from __future__ import annotations

import json
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

from evelyn_core.search_followup_recovery import (  # noqa: E402
    SearchFollowupRecoveryJournal,
)
from evelyn_core import search_followup_recovery as recovery_module  # noqa: E402


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

    def test_round_trip_keeps_only_private_prepared_answer_and_receipt(
        self,
    ) -> None:
        journal = self.journal()
        intent_id = self.begin(journal)
        journal.begin_delivery_prepare(
            intent_id,
            answer="민감한 검색 결과",
            display_text="민감한 검색 결과",
            delivery_turn_id="turn-delivery",
        )
        journal.mark_delivery_baseline(
            intent_id,
            continuity_generation=4,
        )
        journal.mark_delivery_attempted(intent_id)
        journal.mark_delivery_succeeded(
            intent_id,
            delivery_message_id=77,
        )
        journal.mark_canonical_committed(
            intent_id,
            continuity_generation=5,
        )

        raw = self.path.read_text(encoding="utf-8")
        self.assertIn("민감한 검색 결과", raw)
        self.assertNotIn("민감한 원문 검색 질문", raw)
        self.assertNotIn("민감한 실제 검색어", raw)
        restored = self.journal()
        self.assertEqual(
            restored.pending()[0]["phase"],
            "canonical_committed",
        )
        self.assertEqual(
            restored.pending()[0]["preparedAnswer"],
            "민감한 검색 결과",
        )
        self.assertEqual(
            restored.pending()[0]["deliveryMessageId"],
            77,
        )
        self.assertEqual(restored.pending()[0]["turnId"], "turn-1")
        self.assertEqual(
            restored.pending()[0]["deliveryTurnId"],
            "turn-delivery",
        )
        self.assertTrue(restored.public_status()["rollbackProtected"])
        self.assertFalse(
            restored.public_status()["policy"]["contentFree"]
        )
        self.assertNotIn(
            "민감한 검색 결과",
            json.dumps(
                restored.public_status(),
                ensure_ascii=False,
            ),
        )

    def test_private_answer_and_total_artifact_bounds_fail_closed(
        self,
    ) -> None:
        answer_limit = (
            recovery_module.SEARCH_FOLLOWUP_PREPARED_ANSWER_MAX_CHARS
        )
        journal = self.journal()
        intent_id = self.begin(journal)
        with self.assertRaisesRegex(
            ValueError,
            "^search_followup_prepared_answer_invalid$",
        ):
            journal.begin_delivery_prepare(
                intent_id,
                answer=(
                    "x"
                    * (answer_limit + 1)
                ),
                display_text="x",
                delivery_turn_id="turn-too-large",
            )

        ids = iter(range(1, 100))
        bounded_path = self.root / "bounded.json"
        bounded = SearchFollowupRecoveryJournal(
            path=bounded_path,
            wall_time=lambda: 1000.0,
            intent_id_factory=lambda: (
                f"search-followup-{next(ids):024x}"
            ),
        )
        overflowed = False
        for index in range(40):
            try:
                current = bounded.begin(
                    guild_id=7,
                    session_key=f"guild:7:text:{index + 1}:user:9",
                    source="text",
                    turn_id=f"turn-{index}",
                    room_key=f"text:{index + 1}",
                    person_key="user:9",
                    session_memory_key=None,
                    channel_id=index + 1,
                    reply_to_message_id=index + 100,
                    request_user_text="question",
                    request_answer_text="promise",
                    query="query",
                    continuity_generation=4,
                )
                bounded.begin_delivery_prepare(
                    current,
                    answer=(
                        "a"
                        * answer_limit
                    ),
                    display_text="display",
                    delivery_turn_id=f"delivery-{index}",
                )
            except ValueError as exc:
                self.assertEqual(
                    str(exc),
                    "search_followup_recovery_size_invalid",
                )
                overflowed = True
                break
        self.assertTrue(overflowed)
        self.assertLessEqual(
            bounded_path.stat().st_size,
            recovery_module.SEARCH_FOLLOWUP_RECOVERY_MAX_BYTES,
        )
        restored = SearchFollowupRecoveryJournal(path=bounded_path)
        self.assertEqual(restored.public_status()["state"], "ready")

    def test_same_session_supersedes_prior_intent(self) -> None:
        journal = self.journal()
        first = self.begin(journal)
        second = self.begin(journal)

        self.assertNotEqual(first, second)
        self.assertFalse(journal.is_active(first))
        self.assertTrue(journal.is_active(second))
        self.assertEqual(len(journal.pending()), 1)

    def test_same_session_physical_delivery_cannot_be_superseded(
        self,
    ) -> None:
        for phase in (
            "delivery_attempted",
            "delivery_uncertain",
            "delivery_succeeded",
            "canonical_committed",
        ):
            with self.subTest(phase=phase):
                ids = iter(range(1, 3))
                path = self.root / f"{phase}.json"
                journal = SearchFollowupRecoveryJournal(
                    path=path,
                    wall_time=lambda: 1000.0,
                    intent_id_factory=lambda: (
                        f"search-followup-{next(ids):024x}"
                    ),
                )
                first = self.begin(journal)
                journal.begin_delivery_prepare(
                    first,
                    answer="검색 결과",
                    display_text="검색 결과",
                    delivery_turn_id="delivery-turn",
                )
                journal.mark_delivery_baseline(
                    first,
                    continuity_generation=4,
                )
                journal.mark_delivery_attempted(first)
                if phase == "delivery_uncertain":
                    journal.mark_delivery_uncertain(
                        first,
                        error_code="search_followup_delivery_failed",
                    )
                elif phase in {
                    "delivery_succeeded",
                    "canonical_committed",
                }:
                    journal.mark_delivery_succeeded(
                        first,
                        delivery_message_id=77,
                    )
                    if phase == "canonical_committed":
                        journal.mark_canonical_committed(
                            first,
                            continuity_generation=5,
                        )

                with self.assertRaisesRegex(
                    RuntimeError,
                    "^search_followup_prior_delivery_unresolved$",
                ):
                    self.begin(journal)

                self.assertTrue(journal.is_active(first))
                self.assertEqual(journal.pending()[0]["phase"], phase)
                self.assertEqual(
                    SearchFollowupRecoveryJournal(path=path)
                    .pending()[0]["phase"],
                    phase,
                )

    def test_capacity_never_evicts_unresolved_other_session(self) -> None:
        ids = iter(range(1, 42))
        path = self.root / "capacity.json"
        journal = SearchFollowupRecoveryJournal(
            path=path,
            wall_time=lambda: 1000.0,
            intent_id_factory=lambda: (
                f"search-followup-{next(ids):024x}"
            ),
        )
        first: str | None = None
        for index in range(
            recovery_module.SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES
        ):
            current = journal.begin(
                guild_id=7,
                session_key=f"guild:7:text:{index + 1}:user:9",
                source="text",
                turn_id=f"turn-{index}",
                room_key=f"text:{index + 1}",
                person_key="user:9",
                session_memory_key=None,
                channel_id=index + 1,
                reply_to_message_id=index + 100,
                request_user_text="question",
                request_answer_text="promise",
                query="query",
                continuity_generation=4,
            )
            first = first or current

        with self.assertRaisesRegex(
            RuntimeError,
            "^search_followup_recovery_capacity_exhausted$",
        ):
            journal.begin(
                guild_id=7,
                session_key="guild:7:text:99:user:9",
                source="text",
                turn_id="turn-overflow",
                room_key="text:99",
                person_key="user:9",
                session_memory_key=None,
                channel_id=99,
                reply_to_message_id=999,
                request_user_text="question",
                request_answer_text="promise",
                query="query",
                continuity_generation=4,
            )

        self.assertEqual(
            len(journal.pending()),
            recovery_module.SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES,
        )
        self.assertTrue(journal.is_active(first))
        self.assertEqual(
            len(SearchFollowupRecoveryJournal(path=path).pending()),
            recovery_module.SEARCH_FOLLOWUP_RECOVERY_MAX_ENTRIES,
        )

    def test_delivery_baseline_cannot_precede_source_generation(
        self,
    ) -> None:
        journal = self.journal()
        intent_id = self.begin(journal)
        journal.begin_delivery_prepare(
            intent_id,
            answer="검색 결과",
            display_text="검색 결과",
            delivery_turn_id="delivery-turn",
        )

        with self.assertRaisesRegex(
            ValueError,
            "^search_followup_delivery_generation_invalid$",
        ):
            journal.mark_delivery_baseline(
                intent_id,
                continuity_generation=3,
            )

        self.assertEqual(
            journal.pending()[0]["deliveryGeneration"],
            0,
        )

    def test_precommit_uncertain_round_trip_stays_valid(self) -> None:
        journal = self.journal()
        intent_id = self.begin(journal)
        journal.begin_delivery_prepare(
            intent_id,
            answer="민감한 검색 결과",
            display_text="민감한 검색 결과",
            delivery_turn_id="turn-delivery",
        )

        journal.mark_delivery_uncertain(
            intent_id,
            error_code="search_followup_source_turn_superseded",
        )

        restored = self.journal()
        self.assertEqual(restored.public_status()["integrity"], "verified")
        self.assertEqual(
            restored.pending()[0]["phase"],
            "delivery_uncertain",
        )

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

    def test_guild_reset_write_failure_fences_then_retries_exact_target(
        self,
    ) -> None:
        journal = self.journal()
        self.begin(journal)
        real_atomic_json_write = recovery_module.atomic_json_write
        failed = False

        def fail_first_reset(path, payload, **kwargs):
            nonlocal failed
            if Path(path) == self.path and not payload["entries"] and not failed:
                failed = True
                raise OSError(
                    r"PRIVATE C:\secret\search-token-canary"
                )
            return real_atomic_json_write(path, payload, **kwargs)

        with patch.object(
            recovery_module,
            "atomic_json_write",
            side_effect=fail_first_reset,
        ):
            with self.assertRaises(OSError):
                journal.reset_guild(7)
            self.assertEqual(journal.pending(), [])
            self.assertEqual(journal.public_status()["state"], "error")
            self.assertFalse(
                journal.public_status()["rollbackProtected"]
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "^search_followup_recovery_unavailable$",
            ):
                journal.begin(
                    guild_id=8,
                    session_key="guild:8:text:8:user:9",
                    source="text",
                    turn_id="turn-2",
                    room_key="text:8",
                    person_key="user:9",
                    session_memory_key="guild:8:text:8:user:9",
                    channel_id=8,
                    reply_to_message_id=11,
                    request_user_text="other",
                    request_answer_text="other",
                    query="other",
                    continuity_generation=5,
                )
            self.assertEqual(journal.reset_guild(7), 1)

        self.assertEqual(self.journal().pending(), [])

    def test_guild_reset_head_failure_cannot_resurrect_target(self) -> None:
        journal = self.journal()
        self.begin(journal)
        real_atomic_json_write = recovery_module.atomic_json_write
        failures = 0

        def fail_first_reset_head(path, payload, **kwargs):
            nonlocal failures
            if (
                Path(path) == journal.head_path
                and payload["generation"] == 2
                and failures < 2
            ):
                failures += 1
                raise OSError("PRIVATE head-write-token")
            return real_atomic_json_write(path, payload, **kwargs)

        with patch.object(
            recovery_module,
            "atomic_json_write",
            side_effect=fail_first_reset_head,
        ):
            with self.assertRaises(OSError):
                journal.reset_guild(7)
            self.assertFalse(
                journal.public_status()["rollbackProtected"]
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "^search_followup_recovery_unavailable$",
            ):
                journal.begin(
                    guild_id=8,
                    session_key="guild:8:text:8:user:9",
                    source="text",
                    turn_id="turn-2",
                    room_key="text:8",
                    person_key="user:9",
                    session_memory_key="guild:8:text:8:user:9",
                    channel_id=8,
                    reply_to_message_id=11,
                    request_user_text="other",
                    request_answer_text="other",
                    query="other",
                    continuity_generation=5,
                )
            with self.assertRaisesRegex(
                RuntimeError,
                "^search_followup_recovery_unavailable$",
            ):
                journal.reset_guild(7)
            self.assertEqual(
                journal.public_status()["lastErrorCode"],
                "search_followup_recovery_write_failed",
            )
            self.assertEqual(
                journal.public_status()["state"],
                "error",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "^search_followup_recovery_unavailable$",
            ):
                journal.begin(
                    guild_id=8,
                    session_key="guild:8:text:8:user:9",
                    source="text",
                    turn_id="turn-3",
                    room_key="text:8",
                    person_key="user:9",
                    session_memory_key="guild:8:text:8:user:9",
                    channel_id=8,
                    reply_to_message_id=12,
                    request_user_text="other",
                    request_answer_text="other",
                    query="other",
                    continuity_generation=5,
                )
            self.assertEqual(journal.reset_guild(7), 0)
            restarted = self.journal()

        self.assertEqual(restarted.pending(), [])

    def test_guild_reset_rejects_unreadable_recovery_state(self) -> None:
        self.path.write_text("{}", encoding="utf-8")
        journal = self.journal()

        with self.assertRaisesRegex(
            RuntimeError,
            "^search_followup_recovery_unavailable$",
        ):
            journal.reset_guild(7)


if __name__ == "__main__":
    unittest.main()
