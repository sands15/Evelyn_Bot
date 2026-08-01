from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_ingress_context import (  # noqa: E402
    render_conversation_ingress_recovery_context,
)


class ConversationIngressContextTests(unittest.TestCase):
    def test_deleted_assistant_text_is_never_projected_after_crash(
        self,
    ) -> None:
        rendered = render_conversation_ingress_recovery_context(
            {
                "schema": "conversation.ingress-recovery-context.v1",
                "automaticReplay": False,
                "records": [
                    {
                        "phase": "delivery_succeeded",
                        "acceptedText": "prior user turn",
                        "assistantText": "deleted-memory-secret",
                    }
                ],
            }
        )

        self.assertIn("prior user turn", rendered)
        self.assertIn("delivered", rendered)
        self.assertNotIn("deleted-memory-secret", rendered)
        self.assertNotIn("quotedDeliveredAssistantText", rendered)

    def test_unanswered_and_ambiguous_rows_are_bounded_quoted_data(
        self,
    ) -> None:
        rendered = render_conversation_ingress_recovery_context(
            {
                "schema": "conversation.ingress-recovery-context.v1",
                "automaticReplay": False,
                "records": [
                    {
                        "phase": "accepted",
                        "acceptedText": "first user turn",
                    },
                    {
                        "phase": "delivery_ambiguous",
                        "acceptedText": "second user turn",
                    },
                ],
            }
        )

        self.assertIn("unanswered", rendered)
        self.assertIn("delivery_ambiguous", rendered)
        self.assertIn("인용 데이터", rendered)


if __name__ == "__main__":
    unittest.main()
