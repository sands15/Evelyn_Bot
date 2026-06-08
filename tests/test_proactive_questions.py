from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.memory as memory  # noqa: E402
from evelyn_core.proactive_questions import (  # noqa: E402
    load_proactive_questions,
    mark_question_asked,
    promote_open_questions,
    read_pending_question,
    resolve_pending_question_answer,
    select_question_to_ask,
    should_offer_proactive_question,
)


class TemporaryMemoryRoot:
    def __init__(self, test_case: unittest.TestCase) -> None:
        self.test_case = test_case
        self.tmp = TemporaryDirectory()
        self.old_root = memory.MEMORY_ROOT

    def __enter__(self) -> Path:
        memory.MEMORY_ROOT = Path(self.tmp.name)
        return memory.MEMORY_ROOT

    def __exit__(self, exc_type, exc, tb) -> None:
        memory.MEMORY_ROOT = self.old_root
        self.tmp.cleanup()


class ProactiveQuestionTests(unittest.TestCase):
    def test_promotes_open_questions_into_pending_queue(self) -> None:
        with TemporaryMemoryRoot(self):
            rows = promote_open_questions(
                123,
                [{"type": "preference", "text": "weather detail \ud655\uc778 \ud544\uc694"}],
                now=1000,
            )

            stored = load_proactive_questions(123)

        self.assertEqual(len(rows), 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "pending")
        self.assertEqual(stored[0]["raw_text"], "weather detail \ud655\uc778 \ud544\uc694")
        self.assertNotEqual(stored[0]["ask_text"], stored[0]["raw_text"])
        self.assertGreater(stored[0]["priority"], 0.45)

    def test_select_and_mark_question_creates_session_pending_file(self) -> None:
        with TemporaryMemoryRoot(self):
            promote_open_questions(123, [{"type": "detail", "text": "tool call strength \ud655\uc778 \ud544\uc694"}], now=1000)

            selected = select_question_to_ask(123, scope_type="guild", session_scope_key="session-a", now=1001)
            self.assertIsNotNone(selected)
            marked = mark_question_asked(
                123,
                selected["id"],
                scope_type="guild",
                session_scope_key="session-a",
                asked_text=selected["ask_text"],
                now=1002,
            )
            pending = read_pending_question(123, session_scope_key="session-a")
            stored = load_proactive_questions(123)

        self.assertEqual(marked["status"], "asked")
        self.assertEqual(marked["ask_count"], 1)
        self.assertEqual(pending["question_id"], selected["id"])
        self.assertEqual(stored[0]["status"], "asked")

    def test_resolve_pending_question_records_answer_and_clears_pending(self) -> None:
        with TemporaryMemoryRoot(self):
            promote_open_questions(123, [{"type": "detail", "text": "middle gear \ud655\uc778 \ud544\uc694"}], now=1000)
            selected = select_question_to_ask(123, scope_type="guild", session_scope_key="session-a", now=1001)
            assert selected is not None
            mark_question_asked(123, selected["id"], scope_type="guild", session_scope_key="session-a", asked_text=selected["ask_text"], now=1002)

            result = resolve_pending_question_answer(123, "yes, add the proactive manager", session_scope_key="session-a", now=1003)
            pending = read_pending_question(123, session_scope_key="session-a")
            stored = load_proactive_questions(123)

        self.assertEqual(result["resolution"], "answered")
        self.assertEqual(pending, {})
        self.assertEqual(stored[0]["status"], "answered")
        self.assertEqual(stored[0]["answer_text"], "yes, add the proactive manager")

    def test_offer_policy_blocks_existing_questions_and_awaiting_turns(self) -> None:
        self.assertFalse(
            should_offer_proactive_question(
                source="text",
                user_text="ok",
                answer_text="Do this?",
                awaiting_user_reply=False,
            )
        )
        self.assertFalse(
            should_offer_proactive_question(
                source="text",
                user_text="ok",
                answer_text="done",
                awaiting_user_reply=True,
            )
        )
        self.assertFalse(
            should_offer_proactive_question(
                source="voice",
                user_text="ok",
                answer_text="done",
                awaiting_user_reply=False,
            )
        )
        self.assertTrue(
            should_offer_proactive_question(
                source="text",
                user_text="ok",
                answer_text="done",
                awaiting_user_reply=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
