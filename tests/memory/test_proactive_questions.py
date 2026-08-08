from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.memory as memory  # noqa: E402
from evelyn_core.explicit_memory_confirmation import (  # noqa: E402
    store_explicit_memory_confirmation,
)
from evelyn_core.memory_vault import (  # noqa: E402
    delete_memory_vault_user_note,
    preview_memory_vault_user_note_deletion,
)
from evelyn_core.proactive_questions import (  # noqa: E402
    evaluate_proactive_question_gate,
    load_proactive_questions,
    mark_question_asked,
    pending_proactive_question_path,
    promote_open_questions,
    read_pending_question,
    resolve_pending_question_answer,
    select_question_to_ask,
    should_offer_proactive_question,
    write_proactive_questions,
)
from evelyn_core.question_policy_state import (  # noqa: E402
    QuestionPolicyState,
    default_question_metrics,
)


def derived_question(text: str, *, question_type: str = "detail") -> dict:
    return {
        "type": question_type,
        "text": text,
        "evidence_id": "memory:question:test",
        "evidence_kind": "derived_question",
        "source_evidence_ids": ["turn:source:user"],
        "source_turn_ids": ["source"],
    }


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
    def test_promotion_does_not_duplicate_unreceipted_text(self) -> None:
        with TemporaryMemoryRoot(self):
            write_proactive_questions(
                123,
                [{"id": "legacy", "raw_text": "legacy private text"}],
            )
            rows = promote_open_questions(
                123,
                [
                    derived_question(
                        "weather detail \ud655\uc778 \ud544\uc694",
                        question_type="preference",
                    )
                ],
                now=1000,
            )

            stored = load_proactive_questions(123)

        self.assertEqual(rows, [])
        self.assertEqual(stored, [])

    def test_queue_consumption_is_fail_closed_without_current_receipt(self) -> None:
        with TemporaryMemoryRoot(self):
            rows = [{"id": "legacy-question", "ask_text": "tool call?", "status": "pending"}]
            write_proactive_questions(123, rows)

            selected = select_question_to_ask(123, scope_type="guild", session_scope_key="session-a", now=1001)
            marked = mark_question_asked(
                123,
                rows[0]["id"],
                scope_type="guild",
                session_scope_key="session-a",
                asked_text=rows[0]["ask_text"],
                now=1002,
            )
            pending = read_pending_question(123, session_scope_key="session-a")
            stored = load_proactive_questions(123)

        self.assertIsNone(selected)
        self.assertEqual(marked, {})
        self.assertEqual(pending, {})
        self.assertEqual(stored, rows)

    def test_resolve_pending_question_records_answer_and_clears_pending(self) -> None:
        with TemporaryMemoryRoot(self):
            rows = [{"id": "legacy-question", "ask_text": "middle gear?", "status": "asked", "ask_count": 1}]
            write_proactive_questions(123, rows)
            memory.write_json_file(
                pending_proactive_question_path(123, scope_key="session-a"),
                {
                    "question_id": rows[0]["id"],
                    "scope_type": "guild",
                    "scope_key": None,
                    "asked_text": rows[0]["ask_text"],
                    "asked_at": 1001,
                    "expires_at": 2000,
                },
            )

            result = resolve_pending_question_answer(123, "yes, add the proactive manager", session_scope_key="session-a", now=1003)
            pending = read_pending_question(123, session_scope_key="session-a")
            stored = load_proactive_questions(123)

        self.assertEqual(result["resolution"], "answered")
        self.assertEqual(pending, {})
        self.assertEqual(stored, rows)
        self.assertNotIn("answer_text", stored[0])

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

    def test_gate_blocks_active_pending_question(self) -> None:
        with TemporaryMemoryRoot(self):
            memory.write_json_file(
                pending_proactive_question_path(123, scope_key="session-a"),
                {
                    "question_id": "legacy-pending",
                    "asked_at": 1002,
                    "expires_at": 2000,
                },
            )

            decision = evaluate_proactive_question_gate(
                guild_id=123,
                source="autonomy",
                user_text="latest user text",
                session_scope_key="session-a",
                now=1003,
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "pending_question_active")
        self.assertTrue(decision.pending_active)

    def test_gate_blocks_session_cooldown_and_candidate_echo(self) -> None:
        cooldown = evaluate_proactive_question_gate(
            guild_id=123,
            source="autonomy",
            user_text="latest user text",
            session_scope_key="session-a",
            session_cooldown_hit=True,
        )
        echo = evaluate_proactive_question_gate(
            guild_id=123,
            source="autonomy",
            user_text="same question?",
            session_scope_key="session-a",
            candidate_text="same question?",
        )

        self.assertFalse(cooldown.allowed)
        self.assertEqual(cooldown.reason, "session_question_cooldown")
        self.assertTrue(cooldown.session_cooldown_hit)
        self.assertFalse(echo.allowed)
        self.assertEqual(echo.reason, "candidate_echoes_user")

    def test_deleted_memory_canary_is_never_selected_marked_or_spoken(self) -> None:
        canary = "deleted-proactive-canary-731"
        with TemporaryMemoryRoot(self) as root:
            source = store_explicit_memory_confirmation(
                f"private source {canary}",
                action_id="proactive-delete-canary",
                root=root,
            )
            question = derived_question(f"ask about {canary}")
            question["source_evidence_ids"] = [f"memory:{source['noteId']}"]
            rows = [
                {
                    "id": "legacy-deleted-question",
                    "raw_text": question["text"],
                    "ask_text": question["text"],
                    "status": "pending",
                }
            ]
            write_proactive_questions(123, rows)
            preview = preview_memory_vault_user_note_deletion(
                source["noteId"],
                reason="privacy_request",
                root=root,
                now=lambda: 1001.0,
            )
            deleted = delete_memory_vault_user_note(
                source["noteId"],
                preview["confirmToken"],
                reason="privacy_request",
                root=root,
                now=lambda: 1002.0,
            )
            selected = select_question_to_ask(
                123,
                scope_type="guild",
                session_scope_key="session-a",
                now=1001,
            )
            marked = mark_question_asked(
                123,
                rows[0]["id"],
                scope_type="guild",
                session_scope_key="session-a",
                asked_text=rows[0]["ask_text"],
                now=1002,
            )
            policy = QuestionPolicyState(
                question_metrics=default_question_metrics(),
                session_question_state={},
                log_turn_event=lambda *_args, **_kwargs: None,
                question_feature_enabled=True,
                min_turn_gap=3,
                min_seconds_gap=60.0,
                max_per_10_turns=3,
                disable_after_frustration_sec=300.0,
            )
            spoken, proactive_asked = policy.maybe_append_proactive_question(
                "safe base answer",
                guild_id=123,
                source="autonomy",
                user_text="continue",
                awaiting_user_reply=False,
                session_key="session-a",
            )
            explicit, explicit_proactive = policy.maybe_append_proactive_question(
                "Do this?",
                guild_id=123,
                source="text",
                user_text="continue",
                awaiting_user_reply=False,
                session_key="session-a",
            )
            promoted = promote_open_questions(
                123,
                [question],
                now=1003,
            )
            pending = read_pending_question(123, session_scope_key="session-a")
            stored = load_proactive_questions(123)

        self.assertTrue(deleted["ok"])
        self.assertIsNone(selected)
        self.assertEqual(marked, {})
        self.assertEqual(spoken, "safe base answer")
        self.assertFalse(proactive_asked)
        self.assertNotIn(canary, spoken)
        self.assertEqual(explicit, "Do this?")
        self.assertFalse(explicit_proactive)
        self.assertEqual(promoted, [])
        self.assertEqual(pending, {})
        self.assertEqual(stored, [])
        self.assertNotIn(canary, str(stored))


if __name__ == "__main__":
    unittest.main()
