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

from evelyn_core.conversation_ingress_restart_runtime import (  # noqa: E402
    ConversationIngressRestartDeps,
    reconcile_recovered_delivery_succeeded,
    verify_recovered_terminal_commit,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    unattributed_memory_receipt_ref,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402
from tests.continuity_test_support import durable_continuity_status  # noqa: E402


class FakeCheckpoint:
    def __init__(self, generation: int = 4) -> None:
        self.generation = generation
        self.commits: list[tuple[str, str]] = []
        self.before_commit = None

    def commit_completed_turn(self, scope: str, turn_id: str):
        if self.before_commit is not None:
            self.before_commit(scope, turn_id)
        self.commits.append((scope, turn_id))
        self.generation += 1
        return durable_continuity_status(self.generation)

    def status(self):
        return durable_continuity_status(self.generation)


class ConversationIngressRestartRuntimeTests(unittest.TestCase):
    def deps(self, *, generation: int = 4):
        store = SessionStateStore.create_empty()
        checkpoint = FakeCheckpoint(generation)
        deps = ConversationIngressRestartDeps(
            session_state_store=store,
            session_continuity_checkpoint=checkpoint,
            system_prompt="system",
            max_history_items=10,
            normal_ttl_sec=90.0,
            question_ttl_sec=300.0,
            log=lambda *_args: None,
        )
        return deps, store, checkpoint

    @staticmethod
    def record(*, phase: str, generation: int = 0):
        return {
            "surface": "discord_text",
            "scope": "guild:1:text:2:user:3",
            "turnId": "journal-turn-1",
            "phase": phase,
            "acceptedText": "prior user turn",
            "assistantText": "delivered answer",
            "memoryReceiptRef": unattributed_memory_receipt_ref(),
            "continuityGeneration": generation,
        }

    def test_delivery_succeeded_rebuilds_history_once_and_commits_exact_turn(
        self,
    ) -> None:
        deps, store, checkpoint = self.deps()
        scope = "guild:1:text:2:user:3"
        store.active_until[scope] = 12.5
        generation = reconcile_recovered_delivery_succeeded(
            self.record(phase="delivery_succeeded"),
            deps=deps,
        )

        self.assertEqual(generation, 5)
        self.assertEqual(
            checkpoint.commits,
            [("guild:1:text:2:user:3", "journal-turn-1")],
        )
        self.assertEqual(store.current_turn_id("guild:1:text:2:user:3"), "journal-turn-1")
        history = store.get_conversation_history(
            system_prompt="system",
            session_key="guild:1:text:2:user:3",
        )
        self.assertEqual([row["role"] for row in history[-2:]], ["user", "assistant"])
        self.assertEqual(store.active_until[scope], 12.5)

        store.last_speaker[scope] = "user"
        store.awaiting_user_reply[scope] = True
        store.active_until[scope] = 12.5
        reconcile_recovered_delivery_succeeded(
            self.record(phase="delivery_succeeded"),
            deps=deps,
        )
        self.assertEqual(len(history), 3)
        self.assertEqual(store.last_speaker[scope], "assistant")
        self.assertFalse(store.awaiting_user_reply[scope])
        self.assertEqual(store.active_until[scope], 12.5)

    def test_prior_checkpoint_then_next_delivered_turn_reconciles(self) -> None:
        deps, store, checkpoint = self.deps()
        store.start_new_turn(
            "guild:1:text:2:user:3",
            turn_id="prior-turn",
        )
        store.finish_assistant_text_turn(
            "guild:1:text:2:user:3",
            "prior checkpoint user",
            "prior checkpoint answer",
            system_prompt="system",
            max_history_items=10,
            guild_id=1,
            user_id=3,
            awaiting_user_reply=False,
            normal_ttl_sec=90.0,
            question_ttl_sec=300.0,
        )

        generation = reconcile_recovered_delivery_succeeded(
            self.record(phase="delivery_succeeded"),
            deps=deps,
        )

        self.assertEqual(generation, 5)
        self.assertEqual(
            store.current_turn_id("guild:1:text:2:user:3"),
            "journal-turn-1",
        )
        history = store.get_conversation_history(
            system_prompt="system",
            session_key="guild:1:text:2:user:3",
        )
        self.assertEqual(
            [row["content"] for row in history[-2:]],
            ["prior user turn", "delivered answer"],
        )

    def test_identical_prior_pair_does_not_complete_a_different_turn(self) -> None:
        deps, store, checkpoint = self.deps()
        record = self.record(phase="delivery_succeeded")
        store.start_new_turn(
            "guild:1:text:2:user:3",
            turn_id="older-identical-turn",
        )
        store.finish_assistant_text_turn(
            "guild:1:text:2:user:3",
            "prior user turn",
            "delivered answer",
            system_prompt="system",
            max_history_items=10,
            guild_id=1,
            user_id=3,
            awaiting_user_reply=False,
            normal_ttl_sec=90.0,
            question_ttl_sec=300.0,
            memory_receipt=record["memoryReceiptRef"],
        )

        generation = reconcile_recovered_delivery_succeeded(
            record,
            deps=deps,
        )

        self.assertEqual(generation, 5)
        self.assertEqual(
            checkpoint.commits,
            [("guild:1:text:2:user:3", "journal-turn-1")],
        )
        self.assertEqual(
            store.current_turn_id("guild:1:text:2:user:3"),
            "journal-turn-1",
        )
        history = store.get_conversation_history(
            system_prompt="system",
            session_key="guild:1:text:2:user:3",
        )
        self.assertEqual(len(history), 5)
        self.assertEqual(
            [row["content"] for row in history[-2:]],
            ["prior user turn", "delivered answer"],
        )

    def test_exact_current_user_only_tail_completes_delivered_turn(
        self,
    ) -> None:
        deps, store, checkpoint = self.deps()
        record = self.record(phase="delivery_succeeded")
        scope = record["scope"]
        store.start_new_turn(scope, turn_id=record["turnId"])
        store.append_history(
            scope,
            "ｐｒｉｏｒ　ｕｓｅｒ　ｔｕｒｎ",
            None,
            system_prompt="system",
            max_history_items=10,
        )
        store.active_until[scope] = 12.5

        def assert_recovery_state(_scope: str, _turn_id: str) -> None:
            history = store.get_conversation_history(
                system_prompt="system",
                session_key=scope,
            )
            self.assertEqual(
                [row["role"] for row in history],
                ["system", "user", "assistant"],
            )
            self.assertEqual(store.last_speaker[scope], "assistant")
            self.assertFalse(store.awaiting_user_reply[scope])
            self.assertEqual(store.active_until[scope], 12.5)

        checkpoint.before_commit = assert_recovery_state

        generation = reconcile_recovered_delivery_succeeded(
            record,
            deps=deps,
        )

        self.assertEqual(generation, 5)
        self.assertEqual(checkpoint.commits, [(scope, record["turnId"])])
        history = store.get_conversation_history(
            system_prompt="system",
            session_key=scope,
        )
        self.assertEqual(
            [row["role"] for row in history],
            ["system", "user", "assistant"],
        )
        self.assertEqual(
            history[-1]["memoryReceiptRef"],
            record["memoryReceiptRef"],
        )
        self.assertEqual(store.last_speaker[scope], "assistant")
        self.assertEqual(store.active_until[scope], 12.5)

    def test_different_current_turn_keeps_user_only_tail_blocked(
        self,
    ) -> None:
        deps, store, checkpoint = self.deps()
        record = self.record(phase="delivery_succeeded")
        scope = record["scope"]
        store.start_new_turn(scope, turn_id="different-turn")
        store.append_history(
            scope,
            "ｐｒｉｏｒ　ｕｓｅｒ　ｔｕｒｎ",
            None,
            system_prompt="system",
            max_history_items=10,
        )

        generation = reconcile_recovered_delivery_succeeded(
            record,
            deps=deps,
        )

        self.assertIsNone(generation)
        self.assertEqual(checkpoint.commits, [])
        self.assertEqual(store.current_turn_id(scope), "different-turn")
        history = store.get_conversation_history(
            system_prompt="system",
            session_key=scope,
        )
        self.assertEqual(
            [row["role"] for row in history],
            ["system", "user"],
        )

    def test_terminal_commit_requires_exact_turn_history_and_generation(
        self,
    ) -> None:
        deps, store, checkpoint = self.deps(generation=7)
        record = self.record(phase="terminal_committing", generation=7)
        store.start_new_turn(
            "guild:1:text:2:user:3",
            turn_id="journal-turn-1",
        )
        store.finish_assistant_text_turn(
            "guild:1:text:2:user:3",
            "ｐｒｉｏｒ　ｕｓｅｒ　ｔｕｒｎ",
            "ｄｅｌｉｖｅｒｅｄ　ａｎｓｗｅｒ",
            system_prompt="system",
            max_history_items=10,
            guild_id=1,
            user_id=3,
            awaiting_user_reply=False,
            normal_ttl_sec=90.0,
            question_ttl_sec=300.0,
            memory_receipt=record["memoryReceiptRef"],
        )

        self.assertTrue(verify_recovered_terminal_commit(record, deps=deps))
        checkpoint.generation = 8
        self.assertFalse(verify_recovered_terminal_commit(record, deps=deps))


if __name__ == "__main__":
    unittest.main()
