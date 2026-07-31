from __future__ import annotations

import json
import sys
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

from evelyn_core.continuity_commit_contract import (  # noqa: E402
    CONTINUITY_COMMIT_RECEIPT_SCHEMA,
    ConversationContinuityCommitError,
    require_durable_continuity_receipt,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


PRIVATE = (
    "Bearer continuity-secret "
    "https://internal.example/private "
    r"C:\Users\Admin\checkpoint.json"
)


class ContinuityCommitContractTests(unittest.TestCase):
    def test_exact_owner_status_becomes_minimal_receipt(
        self,
    ) -> None:
        receipt = require_durable_continuity_receipt(
            durable_continuity_status(7)
        )

        self.assertEqual(
            receipt,
            {
                "schema": CONTINUITY_COMMIT_RECEIPT_SCHEMA,
                "durable": True,
                "generation": 7,
                "persistedSessionCount": 1,
            },
        )

    def test_legacy_or_partial_success_is_rejected(self) -> None:
        for value in (
            None,
            {},
            {"generation": 7},
            {
                "state": "ready",
                "rollbackProtected": True,
            },
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ConversationContinuityCommitError,
                    "^conversation_continuity_commit_failed$",
                ):
                    require_durable_continuity_receipt(value)

    def test_each_durability_proof_is_required(self) -> None:
        cases = {
            "schema": "legacy",
            "state": "error",
            "rollbackProtected": False,
            "checkpointIntegrity": "unknown",
            "checkpointHeadState": "lagging",
            "checkpointGeneration": 0,
            "persistedSessionCount": 0,
            "completedTurnCommit": {},
        }
        for field, invalid in cases.items():
            status = durable_continuity_status(3)
            status[field] = invalid
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    ConversationContinuityCommitError,
                    "^conversation_continuity_commit_failed$",
                ):
                    require_durable_continuity_receipt(status)

    def test_metrics_must_prove_current_success(self) -> None:
        cases = {
            "schema": "legacy",
            "attemptCount": 0,
            "successCount": 0,
            "failureCount": 1,
            "sampleCount": 0,
            "lastSucceeded": False,
            "lastTargetVerified": False,
        }
        for field, invalid in cases.items():
            status = durable_continuity_status(3)
            status["completedTurnCommit"][field] = invalid
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    ConversationContinuityCommitError,
                    "^conversation_continuity_commit_failed$",
                ):
                    require_durable_continuity_receipt(status)

    def test_untrusted_private_fields_never_reach_error(self) -> None:
        status = durable_continuity_status(3)
        status["checkpointGeneration"] = PRIVATE
        status["privateMessage"] = PRIVATE

        with self.assertRaises(
            ConversationContinuityCommitError
        ) as raised:
            require_durable_continuity_receipt(status)

        rendered = json.dumps(
            {
                "type": type(raised.exception).__name__,
                "message": str(raised.exception),
            }
        )
        self.assertNotIn("continuity-secret", rendered)
        self.assertNotIn("internal.example", rendered)
        self.assertNotIn("Users", rendered)


if __name__ == "__main__":
    unittest.main()
