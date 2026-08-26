from __future__ import annotations

import sys
import tempfile
import unittest
from functools import partial
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
    reconcile_recovered_terminal_commit,
    verify_recovered_terminal_commit,
)
from evelyn_core.conversation_ingress_composition import (  # noqa: E402
    ConversationIngressComposition,
    ConversationIngressCompositionDeps,
    build_main_conversation_ingress_composition,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressRecoveryJournal,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from tests.continuity_test_support import durable_continuity_status  # noqa: E402


class FakeCheckpoint:
    def __init__(self, generation: int = 4) -> None:
        self.generation = generation
        self.commits: list[tuple[str, str]] = []
        self.before_commit = None

    def commit_completed_turn(
        self,
        scope: str,
        turn_id: str,
        *,
        before_commit=None,
    ):
        if self.before_commit is not None:
            self.before_commit(scope, turn_id)
        if before_commit is not None:
            before_commit(self.generation + 1)
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
    def terminal_journal(
        root: Path,
        *,
        generation: int,
    ) -> tuple[str, dict]:
        scope = "guild:1:text:2:user:3"
        journal = ConversationIngressRecoveryJournal(
            path=root / "conversation_ingress" / "main.json",
            head_path=root / "conversation_ingress" / "main.head.json",
        )
        claim = journal.claim(
            surface="discord_text",
            scope=scope,
            source_delivery_id=f"delivery-{generation}",
            accepted_text="delivered user",
            turn_id="delivered-turn",
        )
        memory_ref = unattributed_memory_receipt_ref()
        journal.mark_response_ready(
            claim["entryId"],
            assistant_text="delivered answer",
            memory_receipt_ref=memory_ref,
        )
        journal.mark_delivery_inflight(claim["entryId"])
        journal.mark_delivery_succeeded(claim["entryId"])
        journal.begin_terminal_commit(
            claim["entryId"],
            continuity_generation=generation,
            assistant_text="delivered answer",
            memory_receipt_ref=memory_ref,
        )
        return scope, memory_ref

    @staticmethod
    def record(
        *,
        phase: str,
        generation: int = 0,
        source_delivery_id: str = "",
    ):
        return {
            "surface": "discord_text",
            "scope": "guild:1:text:2:user:3",
            "sourceDeliveryId": source_delivery_id,
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

    def test_autonomy_recovery_restores_awaiting_only_for_ping(self) -> None:
        cases = (
            ("autonomy:ping:run-ping", True),
            ("autonomy:followup:run-followup", False),
            ("autonomy:notify:run-notify", False),
            ("command:123:1", False),
        )
        for source_delivery_id, expected_awaiting in cases:
            with self.subTest(source_delivery_id=source_delivery_id):
                deps, store, _checkpoint = self.deps()
                reconcile_recovered_delivery_succeeded(
                    self.record(
                        phase="delivery_succeeded",
                        source_delivery_id=source_delivery_id,
                    ),
                    deps=deps,
                )
                self.assertEqual(
                    store.awaiting_user_reply[
                        "guild:1:text:2:user:3"
                    ],
                    expected_awaiting,
                )

    def test_fresh_restart_completes_autonomy_delivery_once_without_replay(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scope = "guild:1:text:2:user:3"
            source_delivery_id = "autonomy:ping:run-restart-1"
            journal = ConversationIngressRecoveryJournal(
                path=root / "conversation_ingress" / "main.json",
                head_path=(
                    root / "conversation_ingress" / "main.head.json"
                ),
            )
            claim = journal.claim(
                surface="discord_text",
                scope=scope,
                source_delivery_id=source_delivery_id,
                accepted_text="[autonomy]",
                turn_id="autonomy-turn-restart-1",
            )
            memory_ref = not_used_memory_receipt_ref()
            journal.mark_response_ready(
                claim["entryId"],
                assistant_text="restart answer",
                memory_receipt_ref=memory_ref,
            )
            journal.mark_delivery_inflight(
                claim["entryId"],
                delivery_ref=source_delivery_id,
            )
            journal.mark_delivery_succeeded(
                claim["entryId"],
                delivery_ref=source_delivery_id,
            )
            continuity_root = root / "conversation_continuity"
            first_store = SessionStateStore.create_empty()
            first_checkpoint = SessionContinuityCheckpoint(
                store=first_store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
            )
            first_owner = build_main_conversation_ingress_composition(
                artifacts_root=root,
                enabled=True,
                session_continuity_checkpoint=first_checkpoint,
                normal_ttl_sec=90.0,
                question_ttl_sec=300.0,
                log=lambda *_args: None,
                reset_guild_recovery_metadata=lambda _guild_id: None,
            )

            first_status = first_owner.activate_after_continuity_restore()

            self.assertTrue(first_status["ownerReady"])
            self.assertEqual(first_status["phases"]["completed"], 1)
            self.assertEqual(
                first_checkpoint.status()["checkpointGeneration"],
                1,
            )
            self.assertTrue(first_store.awaiting_user_reply[scope])

            restored_store = SessionStateStore.create_empty()
            restored_checkpoint = SessionContinuityCheckpoint(
                store=restored_store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
            )
            restored_checkpoint.restore()
            restored_owner = build_main_conversation_ingress_composition(
                artifacts_root=root,
                enabled=True,
                session_continuity_checkpoint=restored_checkpoint,
                normal_ttl_sec=90.0,
                question_ttl_sec=300.0,
                log=lambda *_args: None,
                reset_guild_recovery_metadata=lambda _guild_id: None,
            )

            restored_status = (
                restored_owner.activate_after_continuity_restore()
            )
            duplicate = restored_owner.claim_discord_autonomy(
                guild_id=1,
                expected_guild_epoch=restored_owner.guild_epoch(1),
                scope=scope,
                source_delivery_id=source_delivery_id,
                accepted_text="[autonomy]",
            )

            self.assertTrue(restored_status["ownerReady"])
            self.assertEqual(restored_status["phases"]["completed"], 1)
            self.assertFalse(duplicate["shouldProcess"])
            self.assertEqual(duplicate["phase"], "completed")
            self.assertEqual(
                restored_checkpoint.status()["checkpointGeneration"],
                1,
            )
            history = restored_store.get_conversation_history(
                system_prompt="system",
                session_key=scope,
            )
            self.assertEqual(
                [row["content"] for row in history[-2:]],
                ["[autonomy]", "restart answer"],
            )
            self.assertTrue(restored_store.awaiting_user_reply[scope])

    def test_autonomy_inflight_blocks_other_source_after_ambiguous_write_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            journal = ConversationIngressRecoveryJournal(
                path=root / "conversation_ingress" / "main.json",
                head_path=(
                    root / "conversation_ingress" / "main.head.json"
                ),
            )
            owner = ConversationIngressComposition(
                ConversationIngressCompositionDeps(
                    journal_factory=lambda: journal,
                    log=lambda *_args: None,
                    active_guild_revocation_ids=lambda: (),
                    reset_session_continuity_guild=(
                        lambda _guild_id, callback: callback()
                    ),
                    reset_guild_persistent_memory=(
                        lambda _guild_id: None
                    ),
                )
            )
            self.assertTrue(
                owner.activate_after_continuity_restore()["ownerReady"]
            )
            scope = "guild:1:text:2:user:3"
            epoch = owner.guild_epoch(1)
            first = owner.claim_discord_autonomy(
                guild_id=1,
                expected_guild_epoch=epoch,
                scope=scope,
                source_delivery_id="autonomy:followup:run-1",
                accepted_text="[autonomy]",
            )
            memory_ref = not_used_memory_receipt_ref()
            owner.bind_response(
                first["entryId"],
                guild_id=1,
                expected_guild_epoch=epoch,
                assistant_text="possibly delivered",
                memory_receipt_ref=memory_ref,
            )
            owner.mark_delivery_inflight(
                first["entryId"],
                guild_id=1,
                expected_guild_epoch=epoch,
                delivery_ref="autonomy:followup:run-1",
            )
            journal.mark_delivery_ambiguous = (  # type: ignore[method-assign]
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("ambiguous receipt unavailable")
                )
            )

            with self.assertRaisesRegex(
                OSError,
                "ambiguous receipt unavailable",
            ):
                owner.mark_delivery_ambiguous(
                    first["entryId"],
                    guild_id=1,
                    expected_guild_epoch=epoch,
                )

            replay = owner.claim_discord_autonomy(
                guild_id=1,
                expected_guild_epoch=epoch,
                scope=scope,
                source_delivery_id="autonomy:followup:run-1",
                accepted_text="[autonomy]",
            )
            successor_sends: list[str] = []
            canonical = SessionStateStore.create_empty()

            def attempt_successor() -> None:
                owner.claim_discord_autonomy(
                    guild_id=1,
                    expected_guild_epoch=epoch,
                    scope=scope,
                    source_delivery_id="autonomy:followup:run-2",
                    accepted_text="[autonomy]",
                )
                successor_sends.append("duplicate")
                canonical.append_history(
                    scope,
                    "[autonomy]",
                    "duplicate",
                    system_prompt="system",
                    max_history_items=10,
                )

            with self.assertRaisesRegex(
                RuntimeError,
                "conversation_ingress_reconciliation_required",
            ):
                attempt_successor()

            self.assertFalse(replay["shouldProcess"])
            self.assertEqual(replay["phase"], "delivery_inflight")
            self.assertEqual(successor_sends, [])
            self.assertEqual(canonical.histories, {})

    def test_fresh_owner_autonomy_inflight_blocks_other_source_without_projection(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ingress_root = root / "conversation_ingress"
            journal = ConversationIngressRecoveryJournal(
                path=ingress_root / "main.json",
                head_path=ingress_root / "main.head.json",
            )
            scope = "guild:1:text:2:user:3"
            first = journal.claim(
                surface="discord_text",
                scope=scope,
                source_delivery_id="autonomy:followup:run-1",
                accepted_text="[autonomy]",
            )
            journal.mark_response_ready(
                first["entryId"],
                assistant_text="possibly delivered",
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )
            journal.mark_delivery_inflight(
                first["entryId"],
                delivery_ref="autonomy:followup:run-1",
            )
            owner = ConversationIngressComposition(
                ConversationIngressCompositionDeps(
                    journal_factory=lambda: journal,
                    log=lambda *_args: None,
                    active_guild_revocation_ids=lambda: (),
                    reset_session_continuity_guild=(
                        lambda _guild_id, callback: callback()
                    ),
                    reset_guild_persistent_memory=(
                        lambda _guild_id: None
                    ),
                )
            )
            status = owner.activate_after_continuity_restore()
            epoch = owner.guild_epoch(1)
            replay = owner.claim_discord_autonomy(
                guild_id=1,
                expected_guild_epoch=epoch,
                scope=scope,
                source_delivery_id="autonomy:followup:run-1",
                accepted_text="[autonomy]",
            )
            successor_sends: list[str] = []
            canonical = SessionStateStore.create_empty()

            def attempt_successor() -> None:
                owner.claim_discord_autonomy(
                    guild_id=1,
                    expected_guild_epoch=epoch,
                    scope=scope,
                    source_delivery_id="autonomy:followup:run-2",
                    accepted_text="[autonomy]",
                )
                successor_sends.append("duplicate")
                canonical.append_history(
                    scope,
                    "[autonomy]",
                    "duplicate",
                    system_prompt="system",
                    max_history_items=10,
                )

            with self.assertRaisesRegex(
                RuntimeError,
                "conversation_ingress_reconciliation_required",
            ):
                attempt_successor()

            self.assertTrue(status["ownerReady"])
            self.assertEqual(status["phases"]["delivery_inflight"], 1)
            self.assertFalse(replay["shouldProcess"])
            self.assertEqual(replay["phase"], "delivery_inflight")
            self.assertEqual(successor_sends, [])
            self.assertEqual(canonical.histories, {})

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
        self.assertTrue(
            reconcile_recovered_terminal_commit(record, deps=deps)
        )
        self.assertEqual(checkpoint.commits, [])
        checkpoint.generation = 8
        self.assertFalse(verify_recovered_terminal_commit(record, deps=deps))

    def test_terminal_commit_recovery_requires_exact_predecessor(self) -> None:
        deps, store, checkpoint = self.deps(generation=7)
        record = self.record(phase="terminal_committing", generation=9)

        reconciled = reconcile_recovered_terminal_commit(record, deps=deps)

        self.assertFalse(reconciled)
        self.assertEqual(checkpoint.commits, [])
        self.assertEqual(store.histories, {})

    def test_terminal_commit_recovery_rejects_wrong_receipt_generation(self) -> None:
        deps, store, checkpoint = self.deps(generation=7)
        record = self.record(phase="terminal_committing", generation=8)
        checkpoint.status = lambda: {
            "state": "ready",
            "rollbackProtected": True,
            "checkpointIntegrity": "verified",
            "checkpointHeadState": "current",
            "checkpointGeneration": 7,
            "persistedSessionCount": 1,
            "restoredSessionCount": 1,
            "keyedAuthenticity": False,
            "externalAnchorConfigured": False,
        }
        calls: list[tuple[str, str]] = []

        def wrong_generation(scope, turn_id, *, before_commit=None):
            calls.append((scope, turn_id))
            return durable_continuity_status(9)

        checkpoint.commit_completed_turn = wrong_generation

        reconciled = reconcile_recovered_terminal_commit(record, deps=deps)

        self.assertFalse(reconciled)
        self.assertEqual(calls, [(record["scope"], record["turnId"])])
        self.assertEqual(store.histories, {})
        self.assertEqual(store.turn_ids, {})
        self.assertEqual(store.last_active_at, {})

    def test_terminal_commit_rejects_same_turn_with_conflicting_tail(
        self,
    ) -> None:
        for case in ("pair", "receipt", "user_only"):
            with self.subTest(case=case):
                deps, store, checkpoint = self.deps(generation=7)
                record = self.record(
                    phase="terminal_committing",
                    generation=8,
                )
                scope = record["scope"]
                store.start_new_turn(scope, turn_id=record["turnId"])
                if case == "user_only":
                    store.append_history(
                        scope,
                        "different user",
                        None,
                        system_prompt="system",
                        max_history_items=10,
                    )
                else:
                    store.finish_assistant_text_turn(
                        scope,
                        (
                            "prior user turn"
                            if case == "receipt"
                            else "different user"
                        ),
                        (
                            "delivered answer"
                            if case == "receipt"
                            else "different answer"
                        ),
                        system_prompt="system",
                        max_history_items=10,
                        guild_id=1,
                        user_id=3,
                        awaiting_user_reply=False,
                        normal_ttl_sec=90.0,
                        question_ttl_sec=300.0,
                        memory_receipt=(
                            unattributed_memory_receipt_ref()
                            if case == "pair"
                            else not_used_memory_receipt_ref()
                        ),
                    )
                checkpoint.status = lambda: {
                    "state": "ready",
                    "rollbackProtected": True,
                    "checkpointIntegrity": "verified",
                    "checkpointHeadState": "current",
                    "checkpointGeneration": 7,
                    "persistedSessionCount": 1,
                    "restoredSessionCount": 1,
                    "keyedAuthenticity": False,
                    "externalAnchorConfigured": False,
                }
                before = store.get_conversation_history(
                    system_prompt="system",
                    session_key=scope,
                )

                self.assertFalse(
                    reconcile_recovered_terminal_commit(record, deps=deps)
                )
                self.assertEqual(checkpoint.commits, [])
                self.assertEqual(
                    store.get_conversation_history(
                        system_prompt="system",
                        session_key=scope,
                    ),
                    before,
                )

    def test_terminal_commit_recovery_accepts_exact_fresh_predecessor(
        self,
    ) -> None:
        deps, store, checkpoint = self.deps(generation=0)
        record = self.record(phase="terminal_committing", generation=1)

        def status():
            if checkpoint.commits:
                return durable_continuity_status(checkpoint.generation)
            return {
                "state": "missing",
                "rollbackProtected": False,
                "checkpointIntegrity": "empty",
                "checkpointHeadState": "missing",
                "checkpointGeneration": 0,
                "persistedSessionCount": 0,
                "restoredSessionCount": 0,
                "keyedAuthenticity": False,
                "externalAnchorConfigured": False,
            }

        checkpoint.status = status

        self.assertTrue(
            reconcile_recovered_terminal_commit(record, deps=deps)
        )
        self.assertEqual(checkpoint.commits, [(record["scope"], record["turnId"])])
        self.assertEqual(store.current_turn_id(record["scope"]), record["turnId"])

    def test_terminal_commit_recovery_accepts_exact_empty_predecessor(
        self,
    ) -> None:
        deps, _store, checkpoint = self.deps(generation=1)
        record = self.record(phase="terminal_committing", generation=2)

        def status():
            if checkpoint.commits:
                return durable_continuity_status(checkpoint.generation)
            return {
                "state": "empty",
                "rollbackProtected": True,
                "checkpointIntegrity": "empty",
                "checkpointHeadState": "empty",
                "checkpointGeneration": 1,
                "persistedSessionCount": 0,
                "restoredSessionCount": 0,
                "keyedAuthenticity": False,
                "externalAnchorConfigured": False,
            }

        checkpoint.status = status

        self.assertTrue(
            reconcile_recovered_terminal_commit(record, deps=deps)
        )
        self.assertEqual(checkpoint.commits, [(record["scope"], record["turnId"])])

    def test_terminal_commit_recovery_rejects_unverified_fresh_auth(
        self,
    ) -> None:
        deps, store, checkpoint = self.deps(generation=0)
        record = self.record(phase="terminal_committing", generation=1)
        checkpoint.status = lambda: {
            "state": "missing",
            "rollbackProtected": False,
            "checkpointIntegrity": "empty",
            "checkpointHeadState": "missing",
            "checkpointGeneration": 0,
            "persistedSessionCount": 0,
            "restoredSessionCount": 0,
            "keyedAuthenticity": True,
            "checkpointHeadAuthenticity": "missing",
            "tamperEvident": False,
            "externalAnchorConfigured": False,
        }

        self.assertFalse(
            reconcile_recovered_terminal_commit(record, deps=deps)
        )
        self.assertEqual(checkpoint.commits, [])
        self.assertEqual(store.histories, {})

    def test_delivery_recovery_rolls_back_store_when_commit_fails(self) -> None:
        deps, store, checkpoint = self.deps()
        record = self.record(phase="delivery_succeeded")
        checkpoint.before_commit = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("commit failed")
        )

        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            reconcile_recovered_delivery_succeeded(record, deps=deps)

        self.assertEqual(store.histories, {})
        self.assertEqual(store.turn_ids, {})
        self.assertEqual(store.last_active_at, {})
        self.assertEqual(checkpoint.commits, [])

    def test_delivery_recovery_crash_after_checkpoint_does_not_recommit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            continuity_root = root / "conversation_continuity"
            initial_store = SessionStateStore.create_empty()
            scope = "guild:1:text:2:user:3"
            initial_store.start_new_turn(scope, turn_id="prior-turn")
            initial_store.append_history(
                scope,
                "prior user",
                "prior answer",
                system_prompt="system",
                max_history_items=10,
            )
            initial = SessionContinuityCheckpoint(
                store=initial_store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
            )
            self.assertEqual(
                initial.commit_completed_turn(scope, "prior-turn")[
                    "checkpointGeneration"
                ],
                1,
            )

            ingress_root = root / "conversation_ingress"
            journal = ConversationIngressRecoveryJournal(
                path=ingress_root / "main.json",
                head_path=ingress_root / "main.head.json",
            )
            claim = journal.claim(
                surface="discord_text",
                scope=scope,
                source_delivery_id="delivery-2",
                accepted_text="delivered user",
                turn_id="delivered-turn",
            )
            memory_ref = unattributed_memory_receipt_ref()
            journal.mark_response_ready(
                claim["entryId"],
                assistant_text="delivered answer",
                memory_receipt_ref=memory_ref,
            )
            journal.mark_delivery_inflight(claim["entryId"])
            journal.mark_delivery_succeeded(claim["entryId"])

            def build_owner(
                checkpoint: SessionContinuityCheckpoint,
                recovery_journal: ConversationIngressRecoveryJournal,
            ) -> ConversationIngressComposition:
                restart_deps = ConversationIngressRestartDeps(
                    session_state_store=checkpoint.store,
                    session_continuity_checkpoint=checkpoint,
                    system_prompt="system",
                    max_history_items=10,
                    normal_ttl_sec=90.0,
                    question_ttl_sec=300.0,
                    log=lambda *_args: None,
                )
                return ConversationIngressComposition(
                    ConversationIngressCompositionDeps(
                        journal_factory=lambda: recovery_journal,
                        log=lambda *_args: None,
                        active_guild_revocation_ids=lambda: (),
                        reset_session_continuity_guild=(
                            checkpoint.reset_guild
                        ),
                        reset_guild_persistent_memory=lambda _guild_id: None,
                        reconcile_delivery_succeeded=partial(
                            reconcile_recovered_delivery_succeeded,
                            deps=restart_deps,
                        ),
                        verify_terminal_commit=partial(
                            reconcile_recovered_terminal_commit,
                            deps=restart_deps,
                        ),
                    )
                )

            first_store = SessionStateStore.create_empty()
            first_checkpoint = SessionContinuityCheckpoint(
                store=first_store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
            )
            self.assertEqual(
                first_checkpoint.restore()["checkpointGeneration"],
                1,
            )
            first_owner = build_owner(first_checkpoint, journal)
            journal.complete = (  # type: ignore[method-assign]
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("crash after checkpoint")
                )
            )

            first_status = first_owner.activate_after_continuity_restore()

            self.assertFalse(first_status["ownerReady"])
            self.assertEqual(
                first_checkpoint.status()["checkpointGeneration"],
                2,
            )
            self.assertEqual(
                journal.recovery_records()[0]["phase"],
                "terminal_committing",
            )

            second_store = SessionStateStore.create_empty()
            second_checkpoint = SessionContinuityCheckpoint(
                store=second_store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
            )
            self.assertEqual(
                second_checkpoint.restore()["checkpointGeneration"],
                2,
            )
            second_owner = build_owner(
                second_checkpoint,
                ConversationIngressRecoveryJournal(
                    path=ingress_root / "main.json",
                    head_path=ingress_root / "main.head.json",
                ),
            )

            second_status = second_owner.activate_after_continuity_restore()

            self.assertTrue(second_status["ownerReady"])
            self.assertEqual(second_status["phases"]["completed"], 1)
            self.assertEqual(
                second_checkpoint.status()["checkpointGeneration"],
                2,
            )

    def test_build_recovers_terminal_marker_from_durable_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            continuity_root = root / "conversation_continuity"
            scope = "guild:1:text:2:user:3"
            store = SessionStateStore.create_empty()
            store.start_new_turn(scope, turn_id="prior-turn")
            store.append_history(
                scope,
                "prior user",
                "prior answer",
                system_prompt="system",
                max_history_items=10,
            )
            store.last_active_at[scope] = 100.0
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
                max_history_items=10,
                wall_time=lambda: 1000.0,
                monotonic=lambda: 100.0,
            )
            checkpoint.commit_completed_turn(scope, "prior-turn")

            ingress_root = root / "conversation_ingress"
            journal = ConversationIngressRecoveryJournal(
                path=ingress_root / "main.json",
                head_path=ingress_root / "main.head.json",
            )
            claim = journal.claim(
                surface="discord_text",
                scope=scope,
                source_delivery_id="delivery-2",
                accepted_text="delivered user",
                turn_id="delivered-turn",
            )
            memory_ref = unattributed_memory_receipt_ref()
            journal.mark_response_ready(
                claim["entryId"],
                assistant_text="delivered answer",
                memory_receipt_ref=memory_ref,
            )
            journal.mark_delivery_inflight(claim["entryId"])
            journal.mark_delivery_succeeded(claim["entryId"])
            journal.begin_terminal_commit(
                claim["entryId"],
                continuity_generation=2,
                assistant_text="delivered answer",
                memory_receipt_ref=memory_ref,
            )

            restored_store = SessionStateStore.create_empty()
            restored_checkpoint = SessionContinuityCheckpoint(
                store=restored_store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
                max_history_items=10,
                wall_time=lambda: 1001.0,
                monotonic=lambda: 500.0,
            )
            restored_checkpoint.restore()
            owner = build_main_conversation_ingress_composition(
                artifacts_root=root,
                enabled=True,
                session_continuity_checkpoint=restored_checkpoint,
                normal_ttl_sec=90.0,
                question_ttl_sec=300.0,
                log=lambda *_args: None,
                reset_guild_recovery_metadata=lambda _guild_id: None,
            )

            status = owner.activate_after_continuity_restore()

            self.assertTrue(status["ownerReady"])
            self.assertEqual(status["phases"]["completed"], 1)
            self.assertEqual(
                restored_checkpoint.status()["checkpointGeneration"],
                2,
            )
            self.assertEqual(
                restored_store.current_turn_id(scope),
                "delivered-turn",
            )
            history = restored_store.get_conversation_history(
                system_prompt="system",
                session_key=scope,
            )
            self.assertEqual(
                [row["content"] for row in history[-2:]],
                ["delivered user", "delivered answer"],
            )
            self.assertEqual(history[-1]["memoryReceiptRef"], memory_ref)

    def test_build_recovers_terminal_marker_from_fresh_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scope, memory_ref = self.terminal_journal(
                root,
                generation=1,
            )
            store = SessionStateStore.create_empty()
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "conversation_continuity" / "active.json",
                status_path=root / "conversation_continuity" / "status.json",
                system_prompt="system",
                wall_time=lambda: 1001.0,
                monotonic=lambda: 500.0,
            )
            self.assertEqual(checkpoint.restore()["state"], "missing")
            owner = build_main_conversation_ingress_composition(
                artifacts_root=root,
                enabled=True,
                session_continuity_checkpoint=checkpoint,
                normal_ttl_sec=90.0,
                question_ttl_sec=300.0,
                log=lambda *_args: None,
                reset_guild_recovery_metadata=lambda _guild_id: None,
            )

            status = owner.activate_after_continuity_restore()

            self.assertTrue(status["ownerReady"])
            self.assertEqual(status["phases"]["completed"], 1)
            self.assertEqual(checkpoint.status()["checkpointGeneration"], 1)
            history = store.get_conversation_history(
                system_prompt="system",
                session_key=scope,
            )
            self.assertEqual(
                [row["content"] for row in history[-2:]],
                ["delivered user", "delivered answer"],
            )
            self.assertEqual(history[-1]["memoryReceiptRef"], memory_ref)

    def test_build_recovers_terminal_marker_from_empty_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            continuity_root = root / "conversation_continuity"
            initial = SessionContinuityCheckpoint(
                store=SessionStateStore.create_empty(),
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
                wall_time=lambda: 1000.0,
                monotonic=lambda: 100.0,
            )
            self.assertEqual(initial.flush(force=True)["state"], "empty")
            scope, _memory_ref = self.terminal_journal(root, generation=2)
            store = SessionStateStore.create_empty()
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=continuity_root / "active.json",
                status_path=continuity_root / "status.json",
                system_prompt="system",
                wall_time=lambda: 1001.0,
                monotonic=lambda: 500.0,
            )
            self.assertEqual(checkpoint.restore()["state"], "missing")
            self.assertEqual(
                checkpoint.status()["checkpointHeadState"],
                "empty",
            )
            owner = build_main_conversation_ingress_composition(
                artifacts_root=root,
                enabled=True,
                session_continuity_checkpoint=checkpoint,
                normal_ttl_sec=90.0,
                question_ttl_sec=300.0,
                log=lambda *_args: None,
                reset_guild_recovery_metadata=lambda _guild_id: None,
            )

            status = owner.activate_after_continuity_restore()

            self.assertTrue(status["ownerReady"])
            self.assertEqual(status["phases"]["completed"], 1)
            self.assertEqual(checkpoint.status()["checkpointGeneration"], 2)
            self.assertEqual(store.current_turn_id(scope), "delivered-turn")


if __name__ == "__main__":
    unittest.main()
