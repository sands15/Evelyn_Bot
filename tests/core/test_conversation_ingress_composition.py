from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_ingress_composition import (  # noqa: E402
    ConversationIngressComposition,
    ConversationIngressCompositionDeps,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressRecoveryJournal,
)


MANUAL_COUNTS = {
    "removedCount": 0,
    "remainingCopies": 0,
    "manualReviewCount": 1,
    "contentFree": True,
}


class ConversationIngressCompositionPurgeTests(unittest.TestCase):
    @staticmethod
    def owner(journal_factory) -> ConversationIngressComposition:
        return ConversationIngressComposition(
            ConversationIngressCompositionDeps(
                journal_factory=journal_factory,
                log=lambda *_args: None,
                active_guild_revocation_ids=lambda: (),
                reset_session_continuity_guild=(
                    lambda _guild_id, reset: reset()
                ),
                reset_guild_persistent_memory=lambda _guild_id: None,
            )
        )

    def test_unready_owner_returns_manual_without_creating_journal(
        self,
    ) -> None:
        factory = MagicMock()
        owner = self.owner(factory)

        purged = owner.purge_exact_lineage(
            match_turn=lambda _value: True,
            match_session=lambda _value: True,
            full_user_delete=True,
        )
        recalled = owner.negative_recall_exact_lineage(
            match_turn=lambda _value: True,
            match_session=lambda _value: True,
            full_user_delete=True,
        )

        self.assertEqual(purged, MANUAL_COUNTS)
        self.assertEqual(recalled, MANUAL_COUNTS)
        factory.assert_not_called()

    def test_ready_owner_delegates_exact_purge_and_negative_recall(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = ConversationIngressRecoveryJournal(
                path=Path(temp_dir) / "ingress.json",
            )
            target = journal.claim(
                surface="discord_text",
                scope="guild:1:text:2:user:3",
                source_delivery_id="target",
                accepted_text="삭제 대상",
            )
            survivor = journal.claim(
                surface="discord_text",
                scope="guild:2:text:4:user:5",
                source_delivery_id="survivor",
                accepted_text="보존 대상",
            )
            owner = self.owner(lambda: journal)
            owner.activate_after_continuity_restore()

            purged = owner.purge_exact_lineage(
                match_turn=lambda value: value == target["turnId"],
                match_session=lambda value: value
                == "guild:1:text:2:user:3",
                full_user_delete=False,
            )
            recalled = owner.negative_recall_exact_lineage(
                match_turn=lambda value: value == target["turnId"],
                match_session=lambda value: value
                == "guild:1:text:2:user:3",
                full_user_delete=False,
            )

        self.assertEqual(purged["removedCount"], 1)
        self.assertEqual(purged["manualReviewCount"], 0)
        self.assertTrue(purged["contentFree"])
        self.assertEqual(recalled["remainingCopies"], 0)
        self.assertIsNone(journal.record_for(target["entryId"]))
        self.assertIsNotNone(journal.record_for(survivor["entryId"]))

    def test_unverified_journal_returns_manual_without_purge_call(
        self,
    ) -> None:
        journal = MagicMock()
        journal.public_status.return_value = {
            "enabled": True,
            "state": "ready",
            "rollbackProtected": True,
        }
        journal.recovery_records.return_value = []
        owner = self.owner(lambda: journal)
        owner.activate_after_continuity_restore()
        journal.public_status.return_value = {
            "enabled": True,
            "state": "ready",
            "rollbackProtected": False,
        }

        result = owner.purge_exact_lineage(
            match_turn=lambda _value: True,
            match_session=lambda _value: True,
            full_user_delete=True,
        )

        self.assertEqual(result, MANUAL_COUNTS)
        journal.purge_exact_lineage.assert_not_called()

    def test_journal_access_or_result_failure_is_content_free_manual(
        self,
    ) -> None:
        journal = MagicMock()
        ready = {
            "enabled": True,
            "state": "ready",
            "rollbackProtected": True,
        }
        journal.public_status.return_value = ready
        journal.recovery_records.return_value = []
        owner = self.owner(lambda: journal)
        owner.activate_after_continuity_restore()
        journal.purge_exact_lineage.return_value = {
            **MANUAL_COUNTS,
            "private": "must-not-escape",
        }

        invalid = owner.purge_exact_lineage(
            match_turn=lambda _value: True,
            match_session=lambda _value: True,
            full_user_delete=True,
        )
        journal.public_status.side_effect = RuntimeError(
            "PRIVATE journal failure"
        )
        failed = owner.negative_recall_exact_lineage(
            match_turn=lambda _value: True,
            match_session=lambda _value: True,
            full_user_delete=True,
        )

        self.assertEqual(invalid, MANUAL_COUNTS)
        self.assertEqual(failed, MANUAL_COUNTS)
        self.assertNotIn("private", failed)
        journal.negative_recall_exact_lineage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
