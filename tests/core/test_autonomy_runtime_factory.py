from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy_runtime_factory import (
    AutonomyRuntimeFactoryDeps,
    get_or_create_autonomy_engine_from_runtime,
)
from evelyn_core.autonomy import AutonomyExecutionContext, AutonomyPlan
from evelyn_core import memory_deletion_journal as journal
from evelyn_core.conversation_memory_receipt import (
    memory_receipt_ref_from_receipt,
)
from evelyn_core.conversation_ingress_composition import (
    ConversationIngressComposition,
    ConversationIngressCompositionDeps,
)
from evelyn_core.conversation_ingress_recovery import (
    ConversationIngressRecoveryJournal,
)
from evelyn_core.memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
)
from evelyn_core.memory_integrity_authenticity import (
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
)
from evelyn_core.discord_runtime_status import DiscordRuntimeStatus
from evelyn_core.self_model import EvelynSelfState
from evelyn_core.session_continuity import SessionContinuityCheckpoint
from evelyn_core.session_memory_state import SessionStateStore
from tests.continuity_test_support import (
    durable_continuity_status,
)


class FakeGuild:
    def __init__(
        self,
        channels: dict[int, Any] | None = None,
        threads: dict[int, Any] | None = None,
    ) -> None:
        self.channels = channels or {}
        self.threads = threads or {}

    def get_channel(self, channel_id: int) -> Any:
        return self.channels.get(channel_id)

    def get_thread(self, thread_id: int) -> Any:
        return self.threads.get(thread_id)


def bound_receipt(note_id: str) -> dict[str, Any]:
    return memory_receipt_ref_from_receipt(
        {
            "schema": "memory.context-receipt.v1",
            "state": "provided",
            "groundingState": "attributed",
            "memoryVersion": 0,
            "suppliedNoteIds": [note_id],
            "suppliedNoteCount": 1,
            "contentFree": True,
        }
    )


class AutonomyRuntimeFactoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.memory_index_dir = (
            Path(self.temp_dir.name) / "memory_index"
        )
        self.events: list[tuple[str, Any]] = []
        self.engines: dict[int, Any] = {}
        self.channels = {10: SimpleNamespace(id=10, name="observe", send=object())}
        self.guild = FakeGuild(self.channels)
        self.history = [{"role": "user", "content": "최근 질문"}]
        self.refresh_tasks: dict[int, Any] = {}
        self.last_refresh: dict[int, float] = {}
        self.last_ping: dict[int, float] = {}
        self.target_session_key = "guild:11:text:10:user:42"
        self.followup_targets: dict[str, Any] = {
            self.target_session_key: {
                "channel_id": 10,
                "message_id": 1,
            }
        }
        self.last_active = {self.target_session_key: 1.0}
        self.session_locks: dict[str, asyncio.Lock] = {}
        self.reply_slot_locks: dict[str, asyncio.Lock] = {}
        self.reply_slot_admission_locks: dict[str, asyncio.Lock] = {}
        self.cognitive_reads: list[dict[str, Any]] = []
        self.clock_values = iter([100.0, 100.25, 200.0, 200.5])
        self.deps = self.build_deps()

    def build_deps(self) -> AutonomyRuntimeFactoryDeps:
        async def send(channel: Any, text: str) -> None:
            self.events.append(("send", (channel, text)))

        async def update_cognitive(*args: Any, **kwargs: Any) -> dict[str, Any]:
            session_key = kwargs["session_key"]
            self.assertTrue(self.session_locks[session_key].locked())
            self.events.append(("refresh", (args, kwargs)))
            return {"updated_at": "now", "action": "reply", "confidence": 0.9}

        async def commit_session_continuity(
            *args: Any,
        ) -> dict[str, Any]:
            self.events.append(("commit", args))
            return durable_continuity_status(9)

        def record_action_outcome(
            guild_id: int,
            action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            self.events.append(
                ("outcome", (guild_id, action, result))
            )
            return {
                "recorded": True,
                "verified": True,
                "authorizationCurrent": True,
            }

        return AutonomyRuntimeFactoryDeps(
            autonomy_engines=self.engines,
            get_guild=lambda _guild_id: self.guild,
            get_observe_channel_ids=lambda _guild_id: [10],
            get_command_only_channel_ids=lambda _guild_id: [20],
            session_followup_targets=self.followup_targets,
            session_last_active_at=self.last_active,
            is_session_active_for_user=lambda _key, _user_id: True,
            session_locks=self.session_locks,
            reply_slot_locks=self.reply_slot_locks,
            reply_slot_admission_locks=(
                self.reply_slot_admission_locks
            ),
            clean_text=lambda text: text.strip(),
            send_discord_text=send,
            question_cooldown_hit=lambda _key: False,
            evaluate_proactive_question_gate=lambda **_kwargs: SimpleNamespace(allowed=False),
            proactive_question_scope_candidates=lambda **_kwargs: [],
            select_question_to_ask=lambda *_args, **_kwargs: None,
            get_conversation_history=lambda **_kwargs: list(self.history),
            memory_index_dir=self.memory_index_dir,
            pick_recent_user_text=lambda history: history[-1]["content"] if history else "",
            localtime=lambda: SimpleNamespace(tm_hour=12),
            monotonic=lambda: next(self.clock_values),
            autonomy_last_cognitive_refresh_at=self.last_refresh,
            autonomy_cognitive_refresh_tasks=self.refresh_tasks,
            read_cached_cognitive_state=(
                lambda _guild_id, **kwargs: self.cognitive_reads.append(
                    kwargs
                )
                or {"action": "idle"}
            ),
            read_vision_watch_state=lambda: {"enabled": False},
            local_tts_snapshot=lambda: {"active": False},
            serialize_local_mic_runtime_state=lambda: {"ready": True},
            get_active_session_count=lambda: 2,
            get_inflight_llm_requests=lambda: 1,
            last_autonomy_ping_at=self.last_ping,
            answer_promises_search=lambda _text: False,
            start_new_turn=lambda session_key: (
                f"autonomy-turn:{session_key}"
            ),
            append_history=lambda *args, **kwargs: self.events.append(("history", (args, kwargs))),
            schedule_memory_update=lambda *args, **kwargs: self.events.append(("memory", (args, kwargs))),
            mark_session_active=lambda *args, **kwargs: self.events.append(("session", (args, kwargs))),
            build_topic_id=lambda *parts: ":".join(parts),
            mark_self_state_assistant_output=lambda **kwargs: self.events.append(("self_state", kwargs)),
            select_and_mark_proactive_question=lambda **_kwargs: None,
            update_cognitive_state=update_cognitive,
            autonomy_cognitive_stale_sec=60.0,
            autonomy_cognitive_min_interval_sec=10.0,
            autonomy_cognitive_force_refresh_sec=120.0,
            vision_watch_interval_sec=5.0,
            active_conversation_text_question_sec=30.0,
            active_conversation_text_sec=15.0,
            autonomy_poll_interval_sec=4.0,
            get_authorized_actions=lambda _guild_id: [
                "assistant:idle",
            ],
            authorize_action=lambda _guild_id, action: {
                "allowed": action
                in {
                    "assistant:idle",
                    "assistant:maybe_ping_user",
                    "assistant:send_followup",
                },
                "code": (
                    "authorized"
                    if action
                    in {
                        "assistant:idle",
                        "assistant:maybe_ping_user",
                        "assistant:send_followup",
                    }
                    else "authorization_scope_denied"
                ),
                "grantId": "grant-1",
            },
            record_action_outcome=record_action_outcome,
            commit_session_continuity=commit_session_continuity,
            log=lambda *args, **kwargs: None,
        )

    def create_engine(self):
        return get_or_create_autonomy_engine_from_runtime(11, deps=self.deps)

    def default_executor(self):
        return self.create_engine().executor.default_executor

    def real_ingress_owner(self) -> ConversationIngressComposition:
        root = Path(self.temp_dir.name) / "autonomy_ingress"
        owner = ConversationIngressComposition(
            ConversationIngressCompositionDeps(
                journal_factory=lambda: ConversationIngressRecoveryJournal(
                    path=root / "main.json",
                    head_path=root / "main.head.json",
                ),
                log=lambda *_args: None,
                active_guild_revocation_ids=lambda: (),
                reset_session_continuity_guild=(
                    lambda _guild_id, callback: callback()
                ),
                reset_guild_persistent_memory=lambda _guild_id: None,
            )
        )
        self.assertTrue(
            owner.activate_after_continuity_restore()["ownerReady"]
        )
        return owner

    def test_factory_caches_engine_and_preserves_poll_interval(self) -> None:
        first = self.create_engine()
        second = self.create_engine()

        self.assertIs(first, second)
        self.assertIs(self.engines[11], first)
        self.assertEqual(first.poll_interval_sec, 4.0)

    def test_factory_injects_minecraft_route_without_eager_effect(
        self,
    ) -> None:
        route_executor = object()
        build_calls: list[int] = []
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "build_minecraft_executor": (
                    lambda guild_id: build_calls.append(guild_id)
                    or route_executor
                ),
            }
        )

        engine = self.create_engine()

        self.assertEqual(build_calls, [11])
        self.assertIs(
            engine.executor.executors["minecraft"],
            route_executor,
        )
        self.assertEqual(engine.executor.enabled_domains, set())
        self.assertFalse(engine._executor_connected)

    async def test_notify_cleans_text_and_uses_preferred_channel(self) -> None:
        engine = self.create_engine()

        await engine.notify("  알림  ")

        self.assertEqual(
            [kind for kind, _payload in self.events],
            [
                "send",
                "history",
                "session",
                "commit",
                "memory",
                "self_state",
                "outcome",
            ],
        )
        history_args, _history_kwargs = next(
            payload
            for kind, payload in self.events
            if kind == "history"
        )
        self.assertEqual(
            history_args[:3],
            (self.target_session_key, "[autonomy:error]", "알림"),
        )
        _guild_id, action, audit_result = self.events[-1][1]
        self.assertEqual(action, "assistant:send_followup")
        self.assertEqual(
            audit_result["_authorization_grant_id"],
            "grant-1",
        )
        self.assertTrue(audit_result["_action_run_id"])

    async def test_notify_fails_closed_without_current_followup_authorization(
        self,
    ) -> None:
        authorizations: list[tuple[int, str, str]] = []

        def deny(
            guild_id: int,
            action: str,
            *,
            action_run_id: str = "",
        ) -> dict[str, Any]:
            authorizations.append(
                (guild_id, action, action_run_id)
            )
            return {
                "allowed": False,
                "code": "authorization_required",
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "authorize_action": deny,
            }
        )
        engine = self.create_engine()

        await engine.notify(
            "must not send",
            action_run_id="notify-denied-1",
        )

        self.assertEqual(
            authorizations,
            [
                (
                    11,
                    "assistant:send_followup",
                    "notify-denied-1",
                )
            ],
        )
        self.assertEqual(self.events, [])
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )
        self.assertEqual(self.last_ping, {})

    async def test_notify_rechecks_exact_grant_after_journal_prepare(
        self,
    ) -> None:
        owner = self.real_ingress_owner()
        authorizations: list[tuple[int, str, str]] = []
        outcomes: list[tuple[int, str, dict[str, Any]]] = []

        def authorize(
            guild_id: int,
            action: str,
            *,
            action_run_id: str = "",
        ) -> dict[str, Any]:
            authorizations.append(
                (guild_id, action, action_run_id)
            )
            if len(authorizations) == 1:
                return {
                    "allowed": True,
                    "code": "authorized",
                    "grantId": "grant-original",
                    "actionRunId": action_run_id,
                }
            return {
                "allowed": False,
                "code": "authorization_required",
                "grantId": "",
                "actionRunId": action_run_id,
            }

        def record_outcome(
            guild_id: int,
            action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            outcomes.append((guild_id, action, dict(result)))
            return {
                "recorded": True,
                "verified": False,
                "authorizationCurrent": False,
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": owner,
                "authorize_action": authorize,
                "record_action_outcome": record_outcome,
            }
        )
        engine = self.create_engine()

        with self.assertRaisesRegex(
            RuntimeError,
            "outcome_unverified",
        ):
            await engine.notify(
                "must not send",
                action_run_id="notify-revoked-1",
            )

        self.assertEqual(
            authorizations,
            [
                (
                    11,
                    "assistant:send_followup",
                    "notify-revoked-1",
                ),
                (
                    11,
                    "assistant:send_followup",
                    "notify-revoked-1",
                ),
            ],
        )
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(
            outcomes[0][2]["reason"],
            "authorization_required",
        )
        self.assertEqual(
            outcomes[0][2]["_authorization_grant_id"],
            "grant-original",
        )
        self.assertEqual(
            outcomes[0][2]["_action_run_id"],
            "notify-revoked-1",
        )
        self.assertEqual(owner.public_status()["entryCount"], 0)
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )
        self.assertFalse(
            any(kind == "send" for kind, _payload in self.events)
        )
        self.assertEqual(self.last_ping, {})

    async def test_notify_error_is_durable_once_in_exact_session_after_restart(
        self,
    ) -> None:
        system_prompt = "system"
        store = SessionStateStore.create_empty()
        store.append_history(
            self.target_session_key,
            "SEARCH_PENDING?",
            None,
            system_prompt=system_prompt,
            max_history_items=12,
        )
        store.update_session_state(
            self.target_session_key,
            user_id=42,
            speaker="user",
            ttl_sec=300.0,
            active_conversation_awaiting_reply_sec=300.0,
            now_monotonic=100.0,
        )
        store.remember_followup_target(
            self.target_session_key,
            channel_id=10,
            message_id=1,
        )
        checkpoint_path = Path(self.temp_dir.name) / "notify-active.json"
        checkpoint = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=checkpoint_path,
            status_path=Path(self.temp_dir.name) / "notify-status.json",
            system_prompt=system_prompt,
            wall_time=lambda: 1000.0,
            monotonic=lambda: 100.0,
        )

        async def commit(
            session_key: str,
            turn_id: str,
        ) -> dict[str, Any]:
            return await checkpoint.commit_completed_turn_async(
                session_key,
                turn_id,
            )

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "session_followup_targets": store.followup_targets,
                "session_last_active_at": store.last_active_at,
                "is_session_active_for_user": (
                    lambda key, user_id: store.is_active_for_user(
                        key,
                        user_id,
                        now_monotonic=100.0,
                    )
                ),
                "get_conversation_history": (
                    lambda **kwargs: store.get_conversation_history(
                        system_prompt=system_prompt,
                        **kwargs,
                    )
                ),
                "start_new_turn": (
                    lambda session_key: store.start_new_turn(
                        session_key,
                        now_monotonic=100.0,
                    )
                ),
                "append_history": (
                    lambda *args, **kwargs: store.append_history(
                        *args,
                        system_prompt=system_prompt,
                        max_history_items=12,
                        **kwargs,
                    )
                ),
                "mark_session_active": (
                    lambda session_key, **kwargs: store.mark_active(
                        session_key,
                        active_conversation_awaiting_reply_sec=300.0,
                        now_monotonic=100.0,
                        **kwargs,
                    )
                ),
                "commit_session_continuity": commit,
                "answer_promises_search": (
                    lambda text: text == "SEARCH_PENDING?"
                ),
                "monotonic": lambda: 100.0,
            }
        )
        engine = self.create_engine()

        await engine.notify("  FAILURE_NOTICE  ")

        live_history = store.get_conversation_history(
            system_prompt=system_prompt,
            session_key=self.target_session_key,
        )
        self.assertEqual(
            [(row["role"], row["content"]) for row in live_history[-2:]],
            [
                ("user", "[autonomy:error]"),
                ("assistant", "FAILURE_NOTICE"),
            ],
        )
        self.assertTrue(
            (await engine.executor.default_executor.observe())[
                "search_pending"
            ]
        )
        self.assertFalse(
            self.deps.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )

        restored_store = SessionStateStore.create_empty()
        restored = SessionContinuityCheckpoint(
            store=restored_store,
            checkpoint_path=checkpoint_path,
            status_path=(
                Path(self.temp_dir.name) / "notify-restored-status.json"
            ),
            system_prompt=system_prompt,
            wall_time=lambda: 1001.0,
            monotonic=lambda: 500.0,
        ).restore()
        restored_history = restored_store.get_conversation_history(
            system_prompt=system_prompt,
            session_key=self.target_session_key,
        )

        self.assertEqual(restored["state"], "restored")
        self.assertEqual(
            sum(
                row["role"] == "user"
                and row["content"] == "[autonomy:error]"
                for row in restored_history
            ),
            1,
        )
        self.assertEqual(
            sum(
                row["role"] == "assistant"
                and row["content"] == "FAILURE_NOTICE"
                for row in restored_history
            ),
            1,
        )
        self.assertEqual(
            restored_history[-1]["memoryReceiptRef"]["state"],
            "not_used",
        )
        self.assertNotIn("guild:11:default", restored_store.histories)

    async def test_observe_builds_runtime_snapshot(self) -> None:
        observation = await self.default_executor().observe()

        self.assertTrue(observation["connected"])
        self.assertEqual(observation["active_sessions"], 2)
        self.assertEqual(observation["inflight_llm_requests"], 1)
        self.assertEqual(observation["observe_channel_ids"], [10])
        self.assertEqual(observation["command_only_channel_ids"], [20])
        self.assertFalse(observation["quiet_hours"])
        self.assertEqual(
            self.cognitive_reads[-1],
            {
                "room_key": "text:10",
                "person_key": "user:42",
                "session_memory_key": (
                    f"{self.target_session_key}:user:42"
                ),
            },
        )

    async def test_all_history_consumers_drop_unreceipted_assistant_text(
        self,
    ) -> None:
        private = "AUTONOMY_HISTORY_CANARY"
        selected_user_texts: list[str] = []
        self.history = [
            {"role": "user", "content": "SAFE_USER_TEXT"},
            {"role": "assistant", "content": private},
        ]
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "select_and_mark_proactive_question": (
                    lambda **kwargs: selected_user_texts.append(
                        kwargs["user_text"]
                    )
                    or None
                ),
            }
        )
        executor = self.default_executor()

        observation = await executor.observe()
        summary = await executor.summarize_fn()
        recent = await executor.summarize_recent_context_fn()
        ping = await executor.maybe_ping_user_fn("확인")
        refresh = await executor.refresh_cognitive_state_fn()

        combined = str(
            {
                "observation": observation,
                "summary": summary,
                "recent": recent,
                "ping": ping,
                "refresh": refresh,
                "events": self.events,
            }
        )
        self.assertNotIn(private, combined)
        self.assertNotIn("summary", summary)
        self.assertNotIn("summary", recent)
        self.assertNotIn("count", recent)
        self.assertNotIn("text", refresh)
        self.assertEqual(selected_user_texts, ["SAFE_USER_TEXT"])
        refresh_event = next(
            payload
            for kind, payload in self.events
            if kind == "refresh"
        )
        self.assertEqual(refresh_event[0][1], "SAFE_USER_TEXT")

    @contextmanager
    def unconfigured_memory_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    async def test_delete_after_observe_blocks_cycle_before_action(
        self,
    ) -> None:
        note_id = "concept-0123456789abcdef"
        self.history = [
            {"role": "user", "content": "SAFE_USER_TEXT"},
            {
                "role": "assistant",
                "content": "BOUND_HISTORY_CANARY",
                "memoryReceiptRef": bound_receipt(note_id),
            },
        ]
        engine = self.create_engine()
        original_observe = engine.observe

        async def observe_then_delete() -> dict[str, Any]:
            observation = await original_observe()
            journal.append_memory_deletion_tombstone(
                self.memory_index_dir,
                {
                    "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                    "noteId": note_id,
                    "noteType": "concept",
                    "sourceType": "conversation",
                    "reason": "privacy_request",
                    "deletedAt": "2026-08-08T00:00:00Z",
                },
            )
            return observation

        engine.observe = observe_then_delete
        engine._run_cycle_with_observation = AsyncMock()

        with self.unconfigured_memory_authenticity():
            with self.assertRaises(
                MemoryDeletionJournalIntegrityError
            ):
                await engine.run_cycle()

        engine._run_cycle_with_observation.assert_not_awaited()
        self.assertEqual(self.events, [])

    async def test_cycle_does_not_return_or_persist_bound_history_text(
        self,
    ) -> None:
        private = "BOUND_HISTORY_RETURN_CANARY"
        self.history = [
            {"role": "user", "content": "SAFE_USER_TEXT"},
            {
                "role": "assistant",
                "content": private,
                "memoryReceiptRef": bound_receipt(
                    "concept-fedcba9876543210"
                ),
            },
        ]
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "get_active_session_count": lambda: 0,
            }
        )
        engine = self.create_engine()
        executed_plans: list[Any] = []

        async def execute(plan: Any) -> dict[str, Any]:
            executed_plans.append(plan)
            return {
                "status": "failed",
                "reason": "autonomy_executor_execute_failed",
            }

        with (
            self.unconfigured_memory_authenticity(),
            patch(
                "evelyn_core.autonomy.update_self_state_from_observation",
                return_value=EvelynSelfState(),
            ),
            patch.object(engine, "persist_state"),
            patch.object(
                engine,
                "execute_next_step",
                side_effect=execute,
            ),
        ):
            cycle = await engine.run_cycle()

        self.assertEqual(len(executed_plans), 1)
        self.assertIsNotNone(executed_plans[0])
        self.assertNotIn(private, str(cycle))
        self.assertNotIn(private, str(engine.state))
        self.assertNotIn("latest_user_text", cycle.observation)
        self.assertNotIn("recent_visible", cycle.observation)
        self.assertEqual(cycle.needs, [])
        self.assertIsNone(cycle.selected_goal)
        self.assertIsNone(cycle.planned)
        self.assertIsNone(cycle.step_result)
        self.assertIsNone(engine.state.current_goal)
        self.assertIsNone(engine.state.current_plan)
        self.assertEqual(engine.state.last_step_result, {})
        self.assertEqual(engine.state.failure_count, 1)
        self.assertEqual(
            engine.state.last_error,
            "autonomy_executor_execute_failed",
        )

    async def test_bound_history_does_not_reset_minecraft_plan_cursor(
        self,
    ) -> None:
        self.history = [
            {"role": "user", "content": "SAFE_USER_TEXT"},
            {
                "role": "assistant",
                "content": "BOUND_CURRENT_TEXT",
                "memoryReceiptRef": bound_receipt(
                    "concept-c123456789abcdef"
                ),
            },
        ]
        engine = self.create_engine()
        default_observe = engine.observe

        async def observe_minecraft() -> dict[str, Any]:
            observation = await default_observe()
            observation["active_environment"] = "minecraft"
            observation["environments"] = {
                "minecraft": {
                    "health": 20,
                    "hunger": 20,
                    "inventory": {},
                }
            }
            return observation

        plans: list[Any] = []

        async def advance(plan: Any) -> dict[str, Any]:
            plans.append(plan)
            plan.cursor += 1
            return {
                "status": "ok",
                "verified": True,
                "evidence_code": "no_side_effect_required",
            }

        engine.observe = observe_minecraft

        with (
            self.unconfigured_memory_authenticity(),
            patch(
                "evelyn_core.autonomy.update_self_state_from_observation",
                return_value=EvelynSelfState(),
            ),
            patch.object(engine, "persist_state"),
            patch.object(
                engine,
                "execute_next_step",
                side_effect=advance,
            ),
        ):
            first = await engine.run_cycle()
            second = await engine.run_cycle()

        self.assertEqual(len(plans), 2)
        self.assertIs(plans[0], plans[1])
        self.assertEqual(plans[1].cursor, 2)
        self.assertIs(first.planned, second.planned)
        self.assertIs(engine.state.current_plan, second.planned)
        self.assertEqual(second.selected_goal.kind, "progress")

    async def test_send_followup_records_history_memory_session_and_ping(self) -> None:
        result = await self.default_executor().send_followup_fn(
            "후속 답변",
            awaiting_user_reply=True,
            user_text="사용자 질문",
        )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["verified"])
        self.assertEqual(
            result["evidence_code"],
            "discord_send_completed",
        )
        self.assertTrue(result["continuityDurable"])
        self.assertEqual(result["continuityGeneration"], 9)
        self.assertEqual(self.last_ping[11], 100.0)
        kinds = [kind for kind, _payload in self.events]
        self.assertEqual(
            kinds,
            ["send", "history", "session", "commit", "memory", "self_state"],
        )
        history_payload = next(
            payload
            for kind, payload in self.events
            if kind == "history"
        )
        self.assertEqual(
            history_payload[1]["memory_receipt"]["state"],
            "not_used",
        )
        session_payload = next(payload for kind, payload in self.events if kind == "session")
        self.assertEqual(session_payload[1]["ttl_sec"], 30.0)
        self.assertTrue(session_payload[1]["awaiting_user_reply"])
        commit_payload = next(
            payload
            for kind, payload in self.events
            if kind == "commit"
        )
        self.assertEqual(
            commit_payload,
            (
                self.target_session_key,
                f"autonomy-turn:{self.target_session_key}",
            ),
        )

    async def test_journaled_followup_orders_delivery_before_exact_commit(self) -> None:
        lifecycle: list[tuple[str, Any]] = []

        class Ingress:
            def guild_epoch(self, guild_id: int) -> int:
                return 4

            def claim_discord_autonomy(self, **kwargs: Any) -> dict[str, Any]:
                lifecycle.append(("claim", kwargs))
                return {
                    "entryId": "entry-1",
                    "turnId": "journal-turn-1",
                    "guildEpoch": 4,
                    "shouldProcess": True,
                }

            def bind_response(self, entry_id: str, **kwargs: Any) -> dict[str, Any]:
                lifecycle.append(("bind", (entry_id, kwargs)))
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, entry_id: str, **kwargs: Any) -> None:
                lifecycle.append(("inflight", (entry_id, kwargs)))

            def mark_delivery_succeeded(self, entry_id: str, **kwargs: Any) -> None:
                lifecycle.append(("succeeded", (entry_id, kwargs)))

            def begin_terminal_commit(self, entry_id: str, **kwargs: Any) -> None:
                lifecycle.append(("terminal", (entry_id, kwargs)))

            def complete(self, entry_id: str, **kwargs: Any) -> None:
                lifecycle.append(("complete", (entry_id, kwargs)))

        async def send(channel: Any, text: str) -> None:
            lifecycle.append(("send", (channel, text)))

        async def commit(
            session_key: str,
            turn_id: str,
            *,
            before_commit=None,
        ) -> dict[str, Any]:
            lifecycle.append(("commit", (session_key, turn_id)))
            before_commit(8)
            return durable_continuity_status(8)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "send_discord_text": send,
                "start_new_turn": (
                    lambda session_key, *, turn_id=None: (
                        lifecycle.append(("start", (session_key, turn_id)))
                        or turn_id
                    )
                ),
                "append_history": (
                    lambda *args, **kwargs: lifecycle.append(
                        ("history", (args, kwargs))
                    )
                ),
                "mark_session_active": (
                    lambda *args, **kwargs: lifecycle.append(
                        ("active", (args, kwargs))
                    )
                ),
                "commit_session_continuity": commit,
                "schedule_memory_update": (
                    lambda *args, **kwargs: lifecycle.append(
                        ("memory", (args, kwargs))
                    )
                ),
                "mark_self_state_assistant_output": (
                    lambda **kwargs: lifecycle.append(
                        ("self_state", kwargs)
                    )
                ),
            }
        )
        context = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-followup-1",
            authorization_grant_id="grant-1",
        )

        result = await self.default_executor().execute_step(
            {"action": "send_followup", "text": "journaled"},
            context=context,
        )

        self.assertTrue(result["continuityDurable"])
        claim = next(payload for name, payload in lifecycle if name == "claim")
        self.assertEqual(
            claim["source_delivery_id"],
            "autonomy:followup:run-followup-1",
        )
        names = [name for name, _payload in lifecycle]
        self.assertLess(names.index("claim"), names.index("bind"))
        self.assertLess(names.index("bind"), names.index("inflight"))
        self.assertLess(names.index("inflight"), names.index("send"))
        self.assertLess(names.index("send"), names.index("succeeded"))
        self.assertLess(names.index("succeeded"), names.index("start"))
        self.assertLess(names.index("active"), names.index("terminal"))
        self.assertLess(names.index("terminal"), names.index("complete"))
        self.assertLess(names.index("complete"), names.index("memory"))

    async def test_continuity_pending_advances_cursor_without_resend_or_memory(
        self,
    ) -> None:
        sends = 0
        phase = "missing"
        source_ids: list[str] = []

        class Ingress:
            def guild_epoch(self, _guild_id: int) -> int:
                return 2

            def claim_discord_autonomy(self, **kwargs: Any) -> dict[str, Any]:
                nonlocal phase
                source_ids.append(kwargs["source_delivery_id"])
                if phase == "missing":
                    phase = "accepted"
                    should_process = True
                else:
                    should_process = False
                return {
                    "entryId": "entry-pending",
                    "turnId": "turn-pending",
                    "guildEpoch": 2,
                    "phase": phase,
                    "shouldProcess": should_process,
                }

            def bind_response(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                nonlocal phase
                phase = "response_ready"
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args: Any, **_kwargs: Any) -> None:
                nonlocal phase
                phase = "delivery_inflight"

            def mark_delivery_succeeded(self, *_args: Any, **_kwargs: Any) -> None:
                nonlocal phase
                phase = "delivery_succeeded"

        async def send(_channel: Any, _text: str) -> None:
            nonlocal sends
            sends += 1

        async def partial_commit(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"state": "ready"}

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "send_discord_text": send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": partial_commit,
                "authorize_action": (
                    lambda _guild_id, _action, **_kwargs: {
                        "allowed": True,
                        "code": "authorized",
                        "grantId": "grant-1",
                    }
                ),
                "record_action_outcome": (
                    lambda *_args, **_kwargs: {
                        "recorded": True,
                        "authorizationCurrent": True,
                        "verified": True,
                    }
                ),
            }
        )
        engine = self.create_engine()
        engine.state.allowed_actions = ["assistant:send_followup"]
        plan = AutonomyPlan(
            goal_kind="followup",
            summary="deliver once",
            steps=[
                {
                    "domain": "assistant",
                    "action": "send_followup",
                    "text": "pending answer",
                }
            ],
        )

        with patch(
            "evelyn_core.autonomy.secrets.token_hex",
            return_value="run-pending-1",
        ):
            result = await engine.execute_next_step(plan)
            done = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["reason"],
            "sent_but_continuity_pending",
        )
        self.assertTrue(result["verified"])
        self.assertFalse(result["continuityDurable"])
        self.assertEqual(result["evidence_code"], "discord_send_completed")
        self.assertEqual(plan.cursor, 1)
        self.assertEqual(done["reason"], "plan_complete")
        self.assertEqual(sends, 1)
        self.assertEqual(
            source_ids,
            ["autonomy:followup:run-pending-1"],
        )
        self.assertFalse(
            any(
                kind in {"memory", "self_state"}
                for kind, _payload in self.events
            )
        )

    async def test_definitive_discord_rejection_discards_claim_for_safe_retry(
        self,
    ) -> None:
        owner = self.real_ingress_owner()
        attempts = 0

        class DiscordRejected(RuntimeError):
            status = 400

        async def send(_channel: Any, _text: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise DiscordRejected("rejected before acceptance")

        async def commit(
            _session_key: str,
            _turn_id: str,
            *,
            before_commit=None,
        ) -> dict[str, Any]:
            before_commit(1)
            return durable_continuity_status(1)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": owner,
                "send_discord_text": send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": commit,
            }
        )
        context = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-definitive-1",
            authorization_grant_id="grant-1",
        )
        executor = self.default_executor()

        with self.assertRaisesRegex(
            DiscordRejected,
            "rejected before acceptance",
        ):
            await executor.send_followup_fn(
                "retry-safe",
                context=context,
            )

        self.assertEqual(owner.public_status()["entryCount"], 0)
        self.assertEqual(self.events, [])
        self.assertEqual(self.last_ping, {})

        result = await executor.send_followup_fn(
            "retry-safe",
            context=context,
        )
        duplicate = await executor.send_followup_fn(
            "retry-safe",
            context=context,
        )

        self.assertTrue(result["continuityDurable"])
        self.assertTrue(duplicate["continuityDurable"])
        self.assertEqual(attempts, 2)
        self.assertEqual(owner.public_status()["phases"]["completed"], 1)
        self.assertEqual(
            [kind for kind, _payload in self.events].count("history"),
            1,
        )
        self.assertEqual(
            [kind for kind, _payload in self.events].count("memory"),
            1,
        )

    async def test_ambiguous_discord_failure_blocks_successor_without_projection(
        self,
    ) -> None:
        owner = self.real_ingress_owner()
        attempts = 0

        async def timeout_send(_channel: Any, _text: str) -> None:
            nonlocal attempts
            attempts += 1
            raise TimeoutError("acceptance unknown")

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": owner,
                "send_discord_text": timeout_send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
            }
        )
        executor = self.default_executor()
        first = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-ambiguous-1",
            authorization_grant_id="grant-1",
        )
        successor = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-successor-1",
            authorization_grant_id="grant-1",
        )

        with self.assertRaisesRegex(TimeoutError, "acceptance unknown"):
            await executor.send_followup_fn(
                "unknown delivery",
                context=first,
            )
        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_ingress_reconciliation_required",
        ):
            await executor.send_followup_fn(
                "must block",
                context=successor,
            )

        status = owner.public_status()
        self.assertEqual(attempts, 1)
        self.assertEqual(status["phases"]["delivery_ambiguous"], 1)
        self.assertEqual(self.events, [])
        self.assertEqual(self.last_ping, {})

    async def test_followup_final_jit_blocks_revoked_expired_replaced_or_error(
        self,
    ) -> None:
        cases = (
            (
                "revoked",
                {
                    "allowed": False,
                    "code": "authorization_required",
                },
                "authorization_required",
            ),
            (
                "expired",
                {
                    "allowed": False,
                    "code": "authorization_required",
                },
                "authorization_required",
            ),
            (
                "replaced",
                {
                    "allowed": True,
                    "code": "authorized",
                    "grantId": "grant-replacement",
                },
                "authorization_changed_during_action",
            ),
            (
                "inspection_error",
                None,
                "authorization_audit_unavailable",
            ),
        )
        for case, final_decision, expected_reason in cases:
            with self.subTest(case=case):
                self.events.clear()
                self.engines.clear()
                self.reply_slot_locks.clear()
                self.reply_slot_admission_locks.clear()
                self.last_ping.clear()
                owner = self.real_ingress_owner()
                authorization_calls: list[
                    tuple[int, str, str]
                ] = []
                outcomes: list[
                    tuple[int, str, dict[str, Any]]
                ] = []
                run_id = f"run-jit-{case}"

                def authorize(
                    guild_id: int,
                    action: str,
                    *,
                    action_run_id: str = "",
                ) -> dict[str, Any]:
                    authorization_calls.append(
                        (guild_id, action, action_run_id)
                    )
                    if len(authorization_calls) <= 2:
                        return {
                            "allowed": True,
                            "code": "authorized",
                            "grantId": "grant-original",
                            "actionRunId": action_run_id,
                        }
                    if final_decision is None:
                        raise OSError("authorization store unavailable")
                    return {
                        **final_decision,
                        "actionRunId": action_run_id,
                    }

                def record_outcome(
                    guild_id: int,
                    action: str,
                    result: dict[str, Any],
                ) -> dict[str, bool]:
                    outcomes.append(
                        (guild_id, action, dict(result))
                    )
                    return {
                        "recorded": True,
                        "verified": False,
                        "authorizationCurrent": False,
                    }

                self.deps = AutonomyRuntimeFactoryDeps(
                    **{
                        **self.deps.__dict__,
                        "conversation_ingress": owner,
                        "authorize_action": authorize,
                        "record_action_outcome": record_outcome,
                    }
                )
                engine = self.create_engine()
                engine.state.enabled = True
                engine.state.status = "running"
                engine.state.allowed_actions = [
                    "assistant:send_followup"
                ]
                plan = AutonomyPlan(
                    goal_kind="followup",
                    summary="must remain authorized",
                    steps=[
                        {
                            "domain": "assistant",
                            "action": "send_followup",
                            "text": "must not send",
                        }
                    ],
                )

                with patch(
                    "evelyn_core.autonomy.secrets.token_hex",
                    return_value=run_id,
                ):
                    result = await engine.execute_next_step(plan)

                self.assertEqual(result["status"], "unverified")
                self.assertEqual(result["reason"], expected_reason)
                self.assertEqual(plan.cursor, 0)
                self.assertFalse(engine.state.enabled)
                self.assertEqual(
                    authorization_calls,
                    [
                        (
                            11,
                            "assistant:send_followup",
                            run_id,
                        )
                    ]
                    * 3,
                )
                self.assertEqual(len(outcomes), 1)
                self.assertEqual(
                    outcomes[0][2]["_authorization_grant_id"],
                    "grant-original",
                )
                self.assertEqual(
                    outcomes[0][2]["_action_run_id"],
                    run_id,
                )
                self.assertEqual(owner.public_status()["entryCount"], 0)
                self.assertFalse(
                    self.reply_slot_locks[
                        "guild:11:reply:text:10"
                    ].locked()
                )
                self.assertFalse(
                    any(
                        kind
                        in {
                            "send",
                            "history",
                            "session",
                            "commit",
                            "memory",
                        }
                        for kind, _payload in self.events
                    )
                )
                self.assertEqual(self.last_ping, {})

    async def test_ping_marks_question_then_rechecks_before_physical_send(
        self,
    ) -> None:
        owner = self.real_ingress_owner()
        lifecycle: list[str] = []
        for method_name in (
            "claim_discord_autonomy",
            "bind_response",
            "mark_delivery_inflight",
            "mark_delivery_ambiguous",
            "discard_ambiguous",
        ):
            original = getattr(owner, method_name)

            def traced(
                *args: Any,
                _method_name: str = method_name,
                _original: Any = original,
                **kwargs: Any,
            ) -> Any:
                lifecycle.append(_method_name)
                return _original(*args, **kwargs)

            setattr(owner, method_name, traced)

        authorization_calls: list[tuple[int, str, str]] = []
        outcomes: list[tuple[int, str, dict[str, Any]]] = []
        run_id = "run-ping-final-jit"

        def authorize(
            guild_id: int,
            action: str,
            *,
            action_run_id: str = "",
        ) -> dict[str, Any]:
            authorization_calls.append(
                (guild_id, action, action_run_id)
            )
            lifecycle.append(
                f"authorize:{len(authorization_calls)}"
            )
            if len(authorization_calls) <= 2:
                return {
                    "allowed": True,
                    "code": "authorized",
                    "grantId": "grant-original",
                    "actionRunId": action_run_id,
                }
            return {
                "allowed": False,
                "code": "authorization_required",
                "actionRunId": action_run_id,
            }

        def select_question(**_kwargs: Any) -> dict[str, str]:
            lifecycle.append("question_marked")
            return {"ask_text": "must not send"}

        def record_outcome(
            guild_id: int,
            action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            lifecycle.append("outcome")
            outcomes.append((guild_id, action, dict(result)))
            return {
                "recorded": True,
                "verified": False,
                "authorizationCurrent": False,
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": owner,
                "authorize_action": authorize,
                "record_action_outcome": record_outcome,
                "select_and_mark_proactive_question": select_question,
            }
        )
        engine = self.create_engine()
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = [
            "assistant:maybe_ping_user"
        ]
        plan = AutonomyPlan(
            goal_kind="ping",
            summary="ask only while authorized",
            steps=[
                {
                    "domain": "assistant",
                    "action": "maybe_ping_user",
                    "text": "ignored",
                }
            ],
        )

        with patch(
            "evelyn_core.autonomy.secrets.token_hex",
            return_value=run_id,
        ):
            result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["reason"], "authorization_required")
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(
            authorization_calls,
            [
                (11, "assistant:maybe_ping_user", run_id),
            ]
            * 3,
        )
        self.assertEqual(
            lifecycle,
            [
                "authorize:1",
                "authorize:2",
                "question_marked",
                "claim_discord_autonomy",
                "bind_response",
                "mark_delivery_inflight",
                "authorize:3",
                "mark_delivery_ambiguous",
                "discard_ambiguous",
                "outcome",
            ],
        )
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(
            outcomes[0][2]["_authorization_grant_id"],
            "grant-original",
        )
        self.assertEqual(
            outcomes[0][2]["_action_run_id"],
            run_id,
        )
        self.assertEqual(owner.public_status()["entryCount"], 0)
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )
        self.assertFalse(
            any(kind == "send" for kind, _payload in self.events)
        )
        self.assertEqual(self.last_ping, {})

    async def test_ping_and_notify_use_action_run_delivery_ids(self) -> None:
        source_ids: list[str] = []
        next_entry = 0

        class Ingress:
            def guild_epoch(self, _guild_id: int) -> int:
                return 1

            def claim_discord_autonomy(self, **kwargs: Any) -> dict[str, Any]:
                nonlocal next_entry
                next_entry += 1
                source_ids.append(kwargs["source_delivery_id"])
                return {
                    "entryId": f"entry-{next_entry}",
                    "turnId": f"turn-{next_entry}",
                    "guildEpoch": 1,
                    "shouldProcess": True,
                }

            def bind_response(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def mark_delivery_succeeded(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def begin_terminal_commit(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def complete(self, *_args: Any, **_kwargs: Any) -> None:
                return None

        async def commit(
            _session_key: str,
            _turn_id: str,
            *,
            before_commit=None,
        ) -> dict[str, Any]:
            before_commit(next_entry)
            return durable_continuity_status(next_entry)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": commit,
                "select_and_mark_proactive_question": (
                    lambda **_kwargs: {"ask_text": "질문"}
                ),
            }
        )
        engine = self.create_engine()
        ping_context = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:maybe_ping_user",
            action_run_id="run-ping-1",
            authorization_grant_id="grant-1",
        )

        ping = await engine.executor.default_executor.execute_step(
            {"action": "maybe_ping_user", "text": "ignored"},
            context=ping_context,
        )
        await engine.notify(
            "notify",
            action_run_id="run-notify-1",
        )

        self.assertTrue(ping["continuityDurable"])
        self.assertEqual(
            source_ids,
            [
                "autonomy:ping:run-ping-1",
                "autonomy:notify:run-notify-1",
            ],
        )
        outcomes = [
            payload
            for kind, payload in self.events
            if kind == "outcome"
        ]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0][:2], (11, "assistant:send_followup"))
        self.assertEqual(
            outcomes[0][2]["_action_run_id"],
            "run-notify-1",
        )
        self.assertEqual(
            outcomes[0][2]["_authorization_grant_id"],
            "grant-1",
        )

    async def test_successor_waits_for_prior_exact_receipt_then_commits_once(
        self,
    ) -> None:
        commit_entered = asyncio.Event()
        release_first_commit = asyncio.Event()
        source_ids: list[str] = []
        turn_ids: list[str] = []
        sends: list[str] = []
        completed: list[tuple[str, int]] = []
        commit_count = 0

        class Ingress:
            def guild_epoch(self, _guild_id: int) -> int:
                return 1

            def claim_discord_autonomy(self, **kwargs: Any) -> dict[str, Any]:
                source_ids.append(kwargs["source_delivery_id"])
                ordinal = len(source_ids)
                return {
                    "entryId": f"entry-{ordinal}",
                    "turnId": f"turn-{ordinal}",
                    "guildEpoch": 1,
                    "shouldProcess": True,
                }

            def bind_response(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def mark_delivery_succeeded(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def begin_terminal_commit(
                self,
                entry_id: str,
                **kwargs: Any,
            ) -> None:
                completed.append(
                    (entry_id, kwargs["continuity_generation"])
                )

            def complete(self, *_args: Any, **_kwargs: Any) -> None:
                return None

        async def send(_channel: Any, text: str) -> None:
            sends.append(text)

        async def commit(
            _session_key: str,
            _turn_id: str,
            *,
            before_commit=None,
        ) -> dict[str, Any]:
            nonlocal commit_count
            commit_count += 1
            generation = commit_count
            if generation == 1:
                commit_entered.set()
                await release_first_commit.wait()
            before_commit(generation)
            return durable_continuity_status(generation)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "send_discord_text": send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: (
                        turn_ids.append(turn_id) or turn_id
                    )
                ),
                "commit_session_continuity": commit,
            }
        )
        executor = self.default_executor()

        def context(run_id: str) -> AutonomyExecutionContext:
            return AutonomyExecutionContext(
                guild_id=11,
                action_key="assistant:send_followup",
                action_run_id=run_id,
                authorization_grant_id="grant-1",
            )

        first_task = asyncio.create_task(
            executor.send_followup_fn(
                "first",
                context=context("run-first"),
            )
        )
        await asyncio.wait_for(commit_entered.wait(), timeout=1.0)

        racing = await executor.send_followup_fn(
            "racing",
            context=context("run-racing"),
        )
        release_first_commit.set()
        first = await asyncio.wait_for(first_task, timeout=1.0)
        successor = await executor.send_followup_fn(
            "successor",
            context=context("run-successor"),
        )

        self.assertEqual(racing["status"], "blocked")
        self.assertEqual(racing["reason"], "followup_reply_slot_busy")
        self.assertEqual(first["continuityGeneration"], 1)
        self.assertEqual(successor["continuityGeneration"], 2)
        self.assertEqual(sends, ["first", "successor"])
        self.assertEqual(
            source_ids,
            [
                "autonomy:followup:run-first",
                "autonomy:followup:run-successor",
            ],
        )
        self.assertEqual(turn_ids, ["turn-1", "turn-2"])
        self.assertEqual(completed, [("entry-1", 1), ("entry-2", 2)])
        history = [
            payload
            for kind, payload in self.events
            if kind == "history"
        ]
        self.assertEqual(len(history), 2)
        self.assertEqual(
            [(row[0][1], row[0][2]) for row in history],
            [("[autonomy]", "first"), ("[autonomy]", "successor")],
        )

    async def test_terminal_complete_failure_returns_pending_without_resend(
        self,
    ) -> None:
        phase = "missing"
        sends = 0

        class Ingress:
            def guild_epoch(self, _guild_id: int) -> int:
                return 1

            def claim_discord_autonomy(self, **_kwargs: Any) -> dict[str, Any]:
                nonlocal phase
                should_process = phase == "missing"
                if should_process:
                    phase = "accepted"
                return {
                    "entryId": "entry-terminal",
                    "turnId": "turn-terminal",
                    "guildEpoch": 1,
                    "phase": phase,
                    "shouldProcess": should_process,
                }

            def bind_response(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                nonlocal phase
                phase = "response_ready"
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args: Any, **_kwargs: Any) -> None:
                nonlocal phase
                phase = "delivery_inflight"

            def mark_delivery_succeeded(self, *_args: Any, **_kwargs: Any) -> None:
                nonlocal phase
                phase = "delivery_succeeded"

            def begin_terminal_commit(self, *_args: Any, **_kwargs: Any) -> None:
                nonlocal phase
                phase = "terminal_committing"

            def complete(self, *_args: Any, **_kwargs: Any) -> None:
                raise RuntimeError("complete receipt failed")

        async def send(_channel: Any, _text: str) -> None:
            nonlocal sends
            sends += 1

        async def commit(
            _session_key: str,
            _turn_id: str,
            *,
            before_commit=None,
        ) -> dict[str, Any]:
            before_commit(4)
            return durable_continuity_status(4)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "send_discord_text": send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": commit,
            }
        )
        context = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-terminal",
            authorization_grant_id="grant-1",
        )
        executor = self.default_executor()

        first = await executor.send_followup_fn(
            "delivered",
            context=context,
        )
        duplicate = await executor.send_followup_fn(
            "delivered",
            context=context,
        )

        for result in (first, duplicate):
            self.assertEqual(result["status"], "ok")
            self.assertEqual(
                result["reason"],
                "sent_but_continuity_pending",
            )
            self.assertTrue(result["verified"])
            self.assertFalse(result["continuityDurable"])
        self.assertEqual(phase, "terminal_committing")
        self.assertEqual(sends, 1)
        self.assertEqual(
            [kind for kind, _payload in self.events].count("history"),
            1,
        )
        self.assertFalse(
            any(
                kind in {"memory", "self_state"}
                for kind, _payload in self.events
            )
        )

    async def test_physical_success_with_receipt_failure_is_pending_not_failed(
        self,
    ) -> None:
        sends = 0

        class Ingress:
            def guild_epoch(self, _guild_id: int) -> int:
                return 1

            def claim_discord_autonomy(self, **_kwargs: Any) -> dict[str, Any]:
                return {
                    "entryId": "entry-receipt",
                    "turnId": "turn-receipt",
                    "guildEpoch": 1,
                    "shouldProcess": True,
                }

            def bind_response(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def mark_delivery_succeeded(self, *_args: Any, **_kwargs: Any) -> None:
                raise OSError("receipt unavailable")

        async def send(_channel: Any, _text: str) -> None:
            nonlocal sends
            sends += 1

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "send_discord_text": send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
            }
        )
        context = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-receipt-fail",
            authorization_grant_id="grant-1",
        )

        result = await self.default_executor().send_followup_fn(
            "physically delivered",
            context=context,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["reason"],
            "sent_but_continuity_pending",
        )
        self.assertTrue(result["verified"])
        self.assertFalse(result["continuityDurable"])
        self.assertEqual(sends, 1)
        self.assertFalse(
            any(
                kind in {"history", "session", "commit", "memory", "self_state"}
                for kind, _payload in self.events
            )
        )

    async def test_followup_stays_bound_to_observed_recipient_and_checkpoint(
        self,
    ) -> None:
        system_prompt = "system"
        store = SessionStateStore.create_empty()
        thread = SimpleNamespace(
            id=20,
            parent=self.channels[10],
            send=object(),
        )
        self.guild.threads[20] = thread
        observed_session = "guild:11:text:20:thread:20:user:42"
        newer_session = "guild:11:text:20:thread:20:user:43"
        store.append_history(
            observed_session,
            "OBSERVED_PENDING?",
            None,
            system_prompt=system_prompt,
            max_history_items=12,
        )
        store.update_session_state(
            observed_session,
            user_id=42,
            speaker="user",
            ttl_sec=300.0,
            active_conversation_awaiting_reply_sec=300.0,
            now_monotonic=100.0,
        )
        store.remember_followup_target(
            observed_session,
            channel_id=20,
            message_id=100,
        )
        clock = SimpleNamespace(wall=1000.0, monotonic=100.0)
        checkpoint = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=Path(self.temp_dir.name) / "active.json",
            status_path=Path(self.temp_dir.name) / "continuity-status.json",
            system_prompt=system_prompt,
            wall_time=lambda: clock.wall,
            monotonic=lambda: clock.monotonic,
        )
        reply_locks: dict[str, asyncio.Lock] = {}
        reply_admission_locks: dict[str, asyncio.Lock] = {}
        session_locks: dict[str, asyncio.Lock] = {}
        commits: list[tuple[str, str]] = []

        async def commit(session_key: str, turn_id: str) -> dict[str, Any]:
            commits.append((session_key, turn_id))
            return await checkpoint.commit_completed_turn_async(
                session_key,
                turn_id,
            )

        self.followup_targets = store.followup_targets
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "session_followup_targets": store.followup_targets,
                "session_last_active_at": store.last_active_at,
                "is_session_active_for_user": (
                    lambda key, user_id: store.is_active_for_user(
                        key,
                        user_id,
                        now_monotonic=clock.monotonic,
                    )
                ),
                "session_locks": session_locks,
                "reply_slot_locks": reply_locks,
                "reply_slot_admission_locks": reply_admission_locks,
                "get_conversation_history": (
                    lambda **kwargs: store.get_conversation_history(
                        system_prompt=system_prompt,
                        **kwargs,
                    )
                ),
                "start_new_turn": (
                    lambda session_key: store.start_new_turn(
                        session_key,
                        now_monotonic=clock.monotonic,
                    )
                ),
                "append_history": (
                    lambda *args, **kwargs: store.append_history(
                        *args,
                        system_prompt=system_prompt,
                        max_history_items=12,
                        **kwargs,
                    )
                ),
                "mark_session_active": (
                    lambda session_key, **kwargs: store.mark_active(
                        session_key,
                        active_conversation_awaiting_reply_sec=300.0,
                        now_monotonic=clock.monotonic,
                        **kwargs,
                    )
                ),
                "commit_session_continuity": commit,
                "monotonic": lambda: clock.monotonic,
            }
        )
        executor = self.default_executor()

        observation = await executor.observe()
        self.assertEqual(observation["latest_user_text"], "OBSERVED_PENDING?")

        clock.monotonic = 200.0
        store.append_history(
            newer_session,
            "NEWER_USER_TEXT",
            None,
            system_prompt=system_prompt,
            max_history_items=12,
        )
        store.update_session_state(
            newer_session,
            user_id=43,
            speaker="user",
            ttl_sec=300.0,
            active_conversation_awaiting_reply_sec=300.0,
            now_monotonic=clock.monotonic,
        )
        store.remember_followup_target(
            newer_session,
            channel_id=20,
            message_id=200,
        )

        result = await executor.send_followup_fn(
            "BOUND_FOLLOWUP",
            awaiting_user_reply=True,
            user_text="OBSERVED_PENDING?",
        )

        self.assertTrue(result["continuityDurable"])
        self.assertEqual(commits[0][0], observed_session)
        self.assertEqual(store.active_user_ids[observed_session], 42)
        self.assertNotIn("BOUND_FOLLOWUP", str(store.histories[newer_session]))
        self.assertNotIn("guild:11:default", store.histories)
        send_call = next(
            payload
            for kind, payload in self.events
            if kind == "send"
        )
        self.assertIs(send_call[0], thread)
        memory_call = next(
            payload
            for kind, payload in self.events
            if kind == "memory"
        )
        self.assertEqual(memory_call[1]["room_key"], "text:20")
        self.assertEqual(memory_call[1]["person_key"], "user:42")
        self.assertEqual(
            memory_call[1]["session_memory_key"],
            f"{observed_session}:user:42",
        )

        clock.wall = 1001.0
        clock.monotonic = 500.0
        restored_store = SessionStateStore.create_empty()
        restored = SessionContinuityCheckpoint(
            store=restored_store,
            checkpoint_path=Path(self.temp_dir.name) / "active.json",
            status_path=Path(self.temp_dir.name) / "restored-status.json",
            system_prompt=system_prompt,
            wall_time=lambda: clock.wall,
            monotonic=lambda: clock.monotonic,
        ).restore()
        restored_history = restored_store.get_conversation_history(
            system_prompt=system_prompt,
            session_key=observed_session,
        )

        self.assertEqual(restored["state"], "restored")
        self.assertEqual(
            [
                (row["role"], row["content"])
                for row in restored_history[-2:]
            ],
            [
                ("user", "OBSERVED_PENDING?"),
                ("assistant", "BOUND_FOLLOWUP"),
            ],
        )
        self.assertEqual(
            restored_history[-1]["memoryReceiptRef"]["state"],
            "not_used",
        )
        self.assertNotIn("guild:11:default", restored_store.histories)

    async def test_same_recipient_successor_invalidates_observed_target(
        self,
    ) -> None:
        executor = self.default_executor()
        await executor.observe()
        self.last_active[self.target_session_key] = 2.0
        self.followup_targets[self.target_session_key]["message_id"] = 2

        stale = await executor.send_followup_fn("STALE_FOLLOWUP")
        stale_refresh = await executor.refresh_cognitive_state_fn()

        self.assertEqual(
            stale,
            {"status": "blocked", "reason": "no_followup_channel"},
        )
        self.assertEqual(
            stale_refresh,
            {"status": "blocked", "reason": "no_followup_channel"},
        )
        self.assertEqual(self.events, [])
        await executor.observe()
        current = await executor.send_followup_fn("CURRENT_FOLLOWUP")
        self.assertEqual(current["status"], "ok")
        self.assertEqual(
            len([kind for kind, _ in self.events if kind == "send"]),
            1,
        )

    async def test_expired_recipient_is_not_observed_or_sent(self) -> None:
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "is_session_active_for_user": (
                    lambda _key, _user_id: False
                ),
            }
        )
        executor = self.default_executor()

        observation = await executor.observe()
        result = await executor.send_followup_fn("STALE_FOLLOWUP")

        self.assertEqual(observation["known_followup_channels"], 0)
        self.assertEqual(
            result,
            {"status": "blocked", "reason": "no_followup_channel"},
        )
        self.assertEqual(self.events, [])

    async def test_configured_observe_channel_is_a_recipient_boundary(
        self,
    ) -> None:
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "get_observe_channel_ids": lambda _guild_id: [99],
            }
        )
        executor = self.default_executor()

        observation = await executor.observe()
        result = await executor.send_followup_fn("OUTSIDE_POLICY")

        self.assertFalse(observation["connected"])
        self.assertEqual(observation["known_followup_channels"], 0)
        self.assertEqual(
            result,
            {"status": "blocked", "reason": "no_followup_channel"},
        )
        self.assertEqual(self.events, [])

    async def test_newest_active_recipient_wins_inside_configured_channels(
        self,
    ) -> None:
        newer_session = "guild:11:text:20:user:43"
        newer_channel = SimpleNamespace(id=20, name="newer", send=object())
        self.channels[20] = newer_channel
        self.followup_targets[newer_session] = {
            "channel_id": 20,
            "message_id": 2,
        }
        self.last_active[self.target_session_key] = 100.0
        self.last_active[newer_session] = 200.0
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "get_observe_channel_ids": lambda _guild_id: [10, 20],
            }
        )

        result = await self.default_executor().send_followup_fn("NEWEST")

        self.assertEqual(result["status"], "ok")
        send_call = next(
            payload for kind, payload in self.events if kind == "send"
        )
        self.assertIs(send_call[0], newer_channel)
        history_call = next(
            payload for kind, payload in self.events if kind == "history"
        )
        self.assertEqual(history_call[0][0], newer_session)
        memory_call = next(
            payload for kind, payload in self.events if kind == "memory"
        )
        self.assertEqual(memory_call[1]["room_key"], "text:20")
        self.assertEqual(memory_call[1]["person_key"], "user:43")

    async def test_delivered_followup_is_not_retried_after_optional_finalize_failure(
        self,
    ) -> None:
        private = "PRIVATE_AUTONOMY_MEMORY_PATH"
        observer_private = "PRIVATE_AUTONOMY_OBSERVER_PATH"
        log_private = "PRIVATE_AUTONOMY_LOG_PATH"
        logs: list[str] = []
        runtime_status = DiscordRuntimeStatus(
            gateway_ready=lambda: True,
            bot_guilds=lambda: [],
            voice_client_type=object,
            status_path=Path(self.temp_dir.name) / "discord-status.json",
        )
        self.history = [
            {"role": "user", "content": "SEARCH_PENDING?"},
        ]
        def fail_memory_update(*args: Any, **kwargs: Any) -> None:
            self.events.append(("memory", (args, kwargs)))
            raise OSError(private)

        def authorize(_guild_id: int, action: str, **_kwargs: Any) -> dict[str, Any]:
            return {
                "allowed": action
                in {"assistant:send_followup", "assistant:idle"},
                "code": "authorized",
                "grantId": "grant-1",
            }

        def record_then_fail(code: str, exc: BaseException) -> None:
            runtime_status.record_error(code, exc)
            raise OSError(observer_private)

        def log_then_fail(message: str) -> None:
            logs.append(message)
            raise RuntimeError(log_private)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "answer_promises_search": (
                    lambda text: text == "SEARCH_PENDING?"
                ),
                "get_active_session_count": lambda: 0,
                "get_inflight_llm_requests": lambda: 0,
                "monotonic": time.monotonic,
                "schedule_memory_update": fail_memory_update,
                "authorize_action": authorize,
                "record_runtime_error": record_then_fail,
                "record_action_outcome": (
                    lambda *_args, **_kwargs: {
                        "recorded": True,
                        "authorizationCurrent": True,
                        "verified": True,
                    }
                ),
                "log": log_then_fail,
            }
        )
        engine = self.create_engine()
        engine.persist_state = lambda: None
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = [
            "assistant:send_followup",
            "assistant:idle",
        ]

        first = await engine.run_cycle()
        second = await engine.run_cycle()

        sent_texts = [
            payload[1]
            for kind, payload in self.events
            if kind == "send"
        ]
        self.assertEqual(
            sent_texts.count(
                "아까 이어서 실제로 찾아본 결과를 정리해볼게."
            ),
            1,
        )
        self.assertFalse(
            any(text.startswith("[자율봇] 오류") for text in sent_texts)
        )
        self.assertEqual(first.step_result["status"], "ok")
        self.assertEqual(first.step_result["reason"], "sent_followup")
        self.assertTrue(first.step_result["continuityDurable"])
        self.assertEqual(first.planned.cursor, 1)
        self.assertEqual(second.selected_goal.kind, "idle")
        self.assertEqual(
            [
                kind
                for kind, _payload in self.events
                if kind in {"history", "session", "commit", "memory"}
            ],
            ["history", "session", "commit", "memory"],
        )
        error_snapshot = runtime_status.runtime_errors.snapshot()
        self.assertEqual(error_snapshot["errorCount"], 1)
        self.assertEqual(
            error_snapshot["lastErrorCode"],
            "autonomy_followup_finalize_failed",
        )
        self.assertEqual(error_snapshot["lastErrorType"], "OSError")
        self.assertEqual(len(logs), 1)
        self.assertIn("errorType=OSError", logs[0])
        combined = str(
            {
                "first": first,
                "second": second,
                "errors": error_snapshot,
                "logs": logs,
            }
        )
        self.assertNotIn(private, combined)
        self.assertNotIn(observer_private, combined)
        self.assertNotIn(log_private, combined)

    async def test_post_effect_integrity_is_audited_and_persisted_before_raise(
        self,
    ) -> None:
        private = "PRIVATE_POST_EFFECT_INTEGRITY"
        owner = self.real_ingress_owner()
        self.history = [
            {"role": "user", "content": "SEARCH_PENDING?"},
        ]
        authorization_calls: list[tuple[int, str, str]] = []
        outcomes: list[tuple[int, str, dict[str, Any]]] = []
        persisted: list[dict[str, Any]] = []

        def authorize(
            guild_id: int,
            action: str,
            *,
            action_run_id: str = "",
        ) -> dict[str, Any]:
            authorization_calls.append(
                (guild_id, action, action_run_id)
            )
            return {
                "allowed": True,
                "code": "authorized",
                "grantId": "grant-original",
                "actionRunId": action_run_id,
            }

        def record_outcome(
            guild_id: int,
            action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            self.events.append(("outcome", None))
            outcomes.append((guild_id, action, dict(result)))
            return {
                "recorded": True,
                "verified": True,
                "authorizationCurrent": True,
            }

        async def commit(
            *args: Any,
            before_commit=None,
        ) -> dict[str, Any]:
            self.events.append(("commit", args))
            before_commit(17)
            return durable_continuity_status(17)

        def fail_memory_update(
            *args: Any,
            **kwargs: Any,
        ) -> None:
            self.events.append(("memory", (args, kwargs)))
            raise MemoryDeletionJournalIntegrityError(private)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": owner,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": commit,
                "schedule_memory_update": fail_memory_update,
                "answer_promises_search": (
                    lambda text: text == "SEARCH_PENDING?"
                ),
                "get_active_session_count": lambda: 0,
                "get_inflight_llm_requests": lambda: 0,
                "monotonic": time.monotonic,
                "authorize_action": authorize,
                "record_action_outcome": record_outcome,
            }
        )
        engine = self.create_engine()
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = [
            "assistant:send_followup",
            "assistant:idle",
        ]

        def persist() -> None:
            persisted.append(
                {
                    "enabled": engine.state.enabled,
                    "status": engine.state.status,
                    "lastStepResult": dict(
                        engine.state.last_step_result
                    ),
                    "planCursor": (
                        engine.state.current_plan.cursor
                        if engine.state.current_plan is not None
                        else None
                    ),
                    "lastPing": self.last_ping.get(11),
                }
            )

        engine.persist_state = persist
        run_id = "run-post-effect-integrity"

        with (
            patch(
                "evelyn_core.autonomy.secrets.token_hex",
                return_value=run_id,
            ),
            self.assertRaises(MemoryDeletionJournalIntegrityError),
        ):
            await engine.run_cycle()

        self.assertEqual(
            [kind for kind, _payload in self.events],
            [
                "send",
                "history",
                "session",
                "commit",
                "memory",
                "outcome",
            ],
        )
        self.assertEqual(
            authorization_calls,
            [(11, "assistant:send_followup", run_id)] * 4,
        )
        self.assertEqual(len(outcomes), 1)
        audit_result = outcomes[0][2]
        self.assertEqual(
            audit_result["_authorization_grant_id"],
            "grant-original",
        )
        self.assertEqual(audit_result["_action_run_id"], run_id)
        self.assertTrue(
            audit_result["_post_effect_integrity_failure"]
        )
        self.assertEqual(len(persisted), 1)
        self.assertTrue(persisted[0]["enabled"])
        self.assertEqual(persisted[0]["status"], "running")
        self.assertEqual(
            persisted[0]["lastStepResult"]["reason"],
            "sent_followup",
        )
        self.assertEqual(persisted[0]["planCursor"], 1)
        self.assertNotIn(
            "_post_effect_integrity_failure",
            persisted[0]["lastStepResult"],
        )
        self.assertIsNotNone(persisted[0]["lastPing"])
        ingress_status = owner.public_status()
        self.assertEqual(ingress_status["entryCount"], 1)
        self.assertEqual(ingress_status["phases"]["completed"], 1)
        self.assertEqual(
            sum(
                count
                for phase, count in ingress_status["phases"].items()
                if phase != "completed"
            ),
            0,
        )
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )
        self.assertNotIn(private, str(outcomes))
        self.assertNotIn(private, str(persisted))

    async def test_post_effect_integrity_flag_survives_authorization_change(
        self,
    ) -> None:
        calls = 0
        outcomes: list[dict[str, Any]] = []

        def authorize(
            _guild_id: int,
            _action: str,
            *,
            action_run_id: str = "",
        ) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {
                "allowed": True,
                "code": "authorized",
                "grantId": (
                    "grant-original"
                    if calls == 1
                    else "grant-replacement"
                ),
                "actionRunId": action_run_id,
            }

        def record_outcome(
            _guild_id: int,
            _action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            outcomes.append(dict(result))
            return {
                "recorded": True,
                "verified": False,
                "authorizationCurrent": False,
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "authorize_action": authorize,
                "record_action_outcome": record_outcome,
            }
        )
        engine = self.create_engine()
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = [
            "assistant:send_followup"
        ]

        async def delivered_with_integrity_signal(
            _step: dict[str, Any],
            *,
            context: AutonomyExecutionContext,
        ) -> dict[str, Any]:
            self.assertEqual(
                context.authorization_grant_id,
                "grant-original",
            )
            return {
                "status": "ok",
                "reason": "sent_followup",
                "verified": True,
                "evidence_code": "discord_send_completed",
                "_post_effect_integrity_failure": True,
            }

        engine._execute_step_with_context = (
            delivered_with_integrity_signal
        )
        plan = AutonomyPlan(
            goal_kind="followup",
            summary="delivered once",
            steps=[
                {
                    "domain": "assistant",
                    "action": "send_followup",
                    "text": "fixed",
                }
            ],
        )

        with patch(
            "evelyn_core.autonomy.secrets.token_hex",
            return_value="run-integrity-auth-change",
        ):
            result = await engine.execute_next_step(plan)

        self.assertEqual(result["status"], "unverified")
        self.assertEqual(
            result["reason"],
            "authorization_changed_during_action",
        )
        self.assertTrue(result["_post_effect_integrity_failure"])
        self.assertEqual(plan.cursor, 0)
        self.assertFalse(engine.state.enabled)
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(
            outcomes[0]["_post_effect_integrity_failure"]
        )
        self.assertEqual(
            outcomes[0]["_authorization_grant_id"],
            "grant-original",
        )
        self.assertEqual(
            outcomes[0]["_action_run_id"],
            "run-integrity-auth-change",
        )

    async def test_notify_audits_post_effect_integrity_before_raise(
        self,
    ) -> None:
        private = "PRIVATE_NOTIFY_POST_EFFECT_INTEGRITY"
        owner = self.real_ingress_owner()
        authorization_calls: list[tuple[int, str, str]] = []
        outcomes: list[tuple[int, str, dict[str, Any]]] = []

        def authorize(
            guild_id: int,
            action: str,
            *,
            action_run_id: str = "",
        ) -> dict[str, Any]:
            authorization_calls.append(
                (guild_id, action, action_run_id)
            )
            return {
                "allowed": True,
                "code": "authorized",
                "grantId": "grant-notify",
                "actionRunId": action_run_id,
            }

        def record_outcome(
            guild_id: int,
            action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            self.events.append(("outcome", None))
            outcomes.append((guild_id, action, dict(result)))
            return {
                "recorded": True,
                "verified": True,
                "authorizationCurrent": True,
            }

        async def commit(
            *args: Any,
            before_commit=None,
        ) -> dict[str, Any]:
            self.events.append(("commit", args))
            before_commit(23)
            return durable_continuity_status(23)

        def fail_memory_update(
            *args: Any,
            **kwargs: Any,
        ) -> None:
            self.events.append(("memory", (args, kwargs)))
            raise MemoryDeletionJournalIntegrityError(private)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": owner,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": commit,
                "schedule_memory_update": fail_memory_update,
                "authorize_action": authorize,
                "record_action_outcome": record_outcome,
            }
        )
        engine = self.create_engine()
        run_id = "run-notify-post-effect-integrity"

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            await engine.notify(
                "fixed safe notice",
                action_run_id=run_id,
            )

        self.assertEqual(
            [kind for kind, _payload in self.events],
            [
                "send",
                "history",
                "session",
                "commit",
                "memory",
                "outcome",
            ],
        )
        self.assertEqual(
            authorization_calls,
            [(11, "assistant:send_followup", run_id)] * 2,
        )
        self.assertEqual(len(outcomes), 1)
        audit_result = outcomes[0][2]
        self.assertEqual(
            audit_result["_authorization_grant_id"],
            "grant-notify",
        )
        self.assertEqual(audit_result["_action_run_id"], run_id)
        self.assertTrue(
            audit_result["_post_effect_integrity_failure"]
        )
        ingress_status = owner.public_status()
        self.assertEqual(ingress_status["entryCount"], 1)
        self.assertEqual(ingress_status["phases"]["completed"], 1)
        self.assertEqual(
            sum(
                count
                for phase, count in ingress_status["phases"].items()
                if phase != "completed"
            ),
            0,
        )
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )
        self.assertIn(11, self.last_ping)
        self.assertNotIn(private, str(outcomes))

    async def test_busy_followup_slot_does_not_exhaust_retry_budget(
        self,
    ) -> None:
        self.history = [
            {"role": "user", "content": "SEARCH_PENDING?"},
        ]

        def authorize(
            _guild_id: int,
            action: str,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "allowed": action
                in {"assistant:send_followup", "assistant:idle"},
                "code": "authorized",
                "grantId": "grant-1",
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "answer_promises_search": (
                    lambda text: text == "SEARCH_PENDING?"
                ),
                "get_active_session_count": lambda: 0,
                "get_inflight_llm_requests": lambda: 0,
                "monotonic": time.monotonic,
                "authorize_action": authorize,
                "record_action_outcome": (
                    lambda *_args, **_kwargs: {
                        "recorded": True,
                        "authorizationCurrent": True,
                        "verified": True,
                    }
                ),
            }
        )
        engine = self.create_engine()
        engine.persist_state = lambda: None
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = [
            "assistant:send_followup",
            "assistant:idle",
        ]
        reply_lock = self.reply_slot_locks.setdefault(
            "guild:11:reply:text:10",
            asyncio.Lock(),
        )
        await reply_lock.acquire()

        first = await engine.run_cycle()
        second = await engine.run_cycle()

        self.assertEqual(
            first.step_result["reason"],
            "followup_reply_slot_busy",
        )
        self.assertEqual(
            second.step_result["reason"],
            "followup_reply_slot_busy",
        )
        self.assertEqual(engine._blocked_counts, {})
        self.assertFalse(any(kind == "send" for kind, _ in self.events))

        reply_lock.release()
        third = await engine.run_cycle()

        self.assertEqual(third.step_result["status"], "ok")
        self.assertEqual(
            len([kind for kind, _ in self.events if kind == "send"]),
            1,
        )

    async def test_send_followup_rejects_partial_commit_status(
        self,
    ) -> None:
        private = (
            "Bearer autonomy-continuity-secret "
            "https://internal.example/private"
        )

        async def partial_commit(*_args: Any) -> dict[str, Any]:
            self.events.append(("commit", None))
            return {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "commit_session_continuity": partial_commit,
            }
        )

        result = await self.default_executor().send_followup_fn(
            "후속 답변"
        )

        self.assertTrue(result["verified"])
        self.assertFalse(result["continuityDurable"])
        self.assertEqual(result["continuityGeneration"], 0)
        self.assertNotIn(private, str(result))

    async def test_send_failure_records_type_only_runtime_error(self) -> None:
        private = "PRIVATE_AUTONOMY_SEND_PATH"
        runtime_status = DiscordRuntimeStatus(
            gateway_ready=lambda: True,
            bot_guilds=lambda: [],
            voice_client_type=object,
            status_path=Path(self.temp_dir.name) / "send-status.json",
        )

        async def fail_send(_channel: Any, _text: str) -> None:
            raise OSError(private)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "send_discord_text": fail_send,
                "record_runtime_error": runtime_status.record_error,
            }
        )

        with self.assertRaisesRegex(OSError, private):
            await self.default_executor().send_followup_fn("후속 답변")

        snapshot = runtime_status.runtime_errors.snapshot()
        self.assertEqual(snapshot["errorCount"], 1)
        self.assertEqual(
            snapshot["lastErrorCode"],
            "autonomy_followup_send_failed",
        )
        self.assertEqual(snapshot["lastErrorType"], "OSError")
        self.assertNotIn(private, str(snapshot))
        self.assertFalse(self.reply_slot_locks[
            "guild:11:reply:text:10"
        ].locked())
        self.assertFalse(any(
            kind in {"history", "session", "commit", "memory"}
            for kind, _payload in self.events
        ))

    async def test_cycle_error_notify_send_failure_preserves_original_error_without_recursion(
        self,
    ) -> None:
        send_calls = 0
        observed: list[tuple[str, str]] = []

        async def fail_send(_channel: Any, _text: str) -> None:
            nonlocal send_calls
            send_calls += 1
            raise OSError("PRIVATE_NOTIFY_SEND_FAILURE")

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "send_discord_text": fail_send,
                "record_runtime_error": (
                    lambda code, exc: observed.append(
                        (code, type(exc).__name__)
                    )
                ),
            }
        )
        engine = self.create_engine()
        engine.state.enabled = True

        async def fail_cycle() -> None:
            engine.state.enabled = False
            raise RuntimeError("PRIVATE_ORIGINAL_CYCLE_FAILURE")

        engine.run_cycle = fail_cycle
        with (
            patch.object(engine, "persist_state"),
            patch(
                "evelyn_core.autonomy.asyncio.sleep",
                return_value=None,
            ),
        ):
            await engine._run_loop()

        self.assertEqual(engine.state.last_error, "autonomy_cycle_failed")
        self.assertEqual(send_calls, 1)
        self.assertEqual(
            observed,
            [("autonomy_followup_send_failed", "OSError")],
        )
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )
        self.assertFalse(
            any(
                kind in {"history", "session", "commit", "memory"}
                for kind, _payload in self.events
            )
        )

    async def test_cycle_error_notify_commit_failure_sends_once_without_recursion(
        self,
    ) -> None:
        observed: list[tuple[str, str]] = []

        async def fail_commit(*args: Any) -> dict[str, Any]:
            self.events.append(("commit", args))
            raise OSError("PRIVATE_NOTIFY_COMMIT_FAILURE")

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "commit_session_continuity": fail_commit,
                "record_runtime_error": (
                    lambda code, exc: observed.append(
                        (code, type(exc).__name__)
                    )
                ),
            }
        )
        engine = self.create_engine()
        engine.state.enabled = True

        async def fail_cycle() -> None:
            engine.state.enabled = False
            raise RuntimeError("PRIVATE_ORIGINAL_CYCLE_FAILURE")

        engine.run_cycle = fail_cycle
        with (
            patch.object(engine, "persist_state"),
            patch(
                "evelyn_core.autonomy.asyncio.sleep",
                return_value=None,
            ),
        ):
            await engine._run_loop()

        self.assertEqual(engine.state.last_error, "autonomy_cycle_failed")
        self.assertEqual(
            len([kind for kind, _payload in self.events if kind == "send"]),
            1,
        )
        self.assertEqual(
            len([kind for kind, _payload in self.events if kind == "commit"]),
            1,
        )
        self.assertEqual(
            observed,
            [("autonomy_followup_finalize_failed", "OSError")],
        )
        self.assertFalse(
            self.reply_slot_locks[
                "guild:11:reply:text:10"
            ].locked()
        )

    async def test_pre_send_failure_releases_followup_slot(self) -> None:
        private = "PRIVATE_AUTONOMY_PREPARE_PATH"
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "build_topic_id": (
                    lambda *_args: (_ for _ in ()).throw(
                        OSError(private)
                    )
                ),
            }
        )

        with self.assertRaisesRegex(OSError, private):
            await self.default_executor().send_followup_fn("후속 답변")

        self.assertFalse(self.reply_slot_locks[
            "guild:11:reply:text:10"
        ].locked())
        self.assertEqual(self.events, [])

    async def test_currentness_failure_releases_followup_slot(self) -> None:
        private = "PRIVATE_AUTONOMY_CURRENTNESS_PATH"
        calls = 0

        def channels(_guild_id: int) -> list[int]:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise OSError(private)
            return [10]

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "get_observe_channel_ids": channels,
            }
        )

        with self.assertRaisesRegex(OSError, private):
            await self.default_executor().send_followup_fn("후속 답변")

        self.assertFalse(self.reply_slot_locks[
            "guild:11:reply:text:10"
        ].locked())
        self.assertEqual(self.events, [])

    async def test_send_cancellation_is_unobserved_and_releases_slot(
        self,
    ) -> None:
        observed: list[tuple[str, BaseException]] = []

        async def cancel_send(_channel: Any, _text: str) -> None:
            raise asyncio.CancelledError()

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "send_discord_text": cancel_send,
                "record_runtime_error": (
                    lambda code, exc: observed.append((code, exc))
                ),
            }
        )

        with self.assertRaises(asyncio.CancelledError):
            await self.default_executor().send_followup_fn("후속 답변")

        self.assertEqual(observed, [])
        self.assertFalse(self.reply_slot_locks[
            "guild:11:reply:text:10"
        ].locked())
        self.assertEqual(self.events, [])

    async def test_repeated_cancellation_drains_physical_send_with_claim_locked(
        self,
    ) -> None:
        send_entered = asyncio.Event()
        release_send = asyncio.Event()
        events: list[str] = []

        class Ingress:
            def guild_epoch(self, _guild_id: int) -> int:
                return 1

            def claim_discord_autonomy(self, **_kwargs: Any) -> dict[str, Any]:
                events.append("claim")
                return {
                    "entryId": "entry-cancel",
                    "turnId": "turn-cancel",
                    "guildEpoch": 1,
                    "shouldProcess": True,
                }

            def bind_response(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
                events.append("bind")
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args: Any, **_kwargs: Any) -> None:
                events.append("inflight")

            def mark_delivery_succeeded(self, *_args: Any, **_kwargs: Any) -> None:
                events.append("succeeded")

            def begin_terminal_commit(self, *_args: Any, **_kwargs: Any) -> None:
                events.append("terminal")

            def complete(self, *_args: Any, **_kwargs: Any) -> None:
                events.append("complete")

        async def delayed_send(_channel: Any, _text: str) -> None:
            events.append("send_entered")
            send_entered.set()
            await release_send.wait()
            events.append("send_returned")

        async def commit(*_args: Any, before_commit=None) -> dict[str, Any]:
            before_commit(3)
            return durable_continuity_status(3)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "conversation_ingress": Ingress(),
                "send_discord_text": delayed_send,
                "start_new_turn": (
                    lambda _session_key, *, turn_id=None: turn_id
                ),
                "commit_session_continuity": commit,
            }
        )
        context = AutonomyExecutionContext(
            guild_id=11,
            action_key="assistant:send_followup",
            action_run_id="run-cancel-1",
            authorization_grant_id="grant-1",
        )
        task = asyncio.create_task(
            self.default_executor().send_followup_fn(
                "drain me",
                context=context,
            )
        )
        await asyncio.wait_for(send_entered.wait(), timeout=1.0)
        reply_lock = self.reply_slot_locks[
            "guild:11:reply:text:10"
        ]

        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertTrue(reply_lock.locked())
        self.assertNotIn("succeeded", events)

        release_send.set()
        result = await asyncio.wait_for(task, timeout=1.0)

        self.assertTrue(result["_post_effect_cancellation"])
        self.assertFalse(reply_lock.locked())
        self.assertEqual(events.count("send_entered"), 1)
        self.assertLess(events.index("send_returned"), events.index("succeeded"))
        self.assertLess(events.index("succeeded"), events.index("complete"))

    async def test_post_send_cancellation_drains_continuity_audits_once_and_prevents_retry(
        self,
    ) -> None:
        self.history = [
            {"role": "user", "content": "SEARCH_PENDING?"},
        ]
        commit_entered = asyncio.Event()
        release_commit = asyncio.Event()

        async def commit(*args: Any) -> dict[str, Any]:
            self.events.append(("commit", args))
            commit_entered.set()
            await release_commit.wait()
            return durable_continuity_status(12)

        def authorize(
            _guild_id: int,
            action: str,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            return {
                "allowed": action
                in {"assistant:send_followup", "assistant:idle"},
                "code": "authorized",
                "grantId": "grant-1",
            }

        def record_outcome(
            guild_id: int,
            action: str,
            result: dict[str, Any],
        ) -> dict[str, bool]:
            self.events.append(
                ("outcome", (guild_id, action, dict(result)))
            )
            return {
                "recorded": True,
                "authorizationCurrent": True,
                "verified": True,
            }

        def append_history(
            session_key: str,
            user_text: str,
            answer: str,
            **kwargs: Any,
        ) -> None:
            self.events.append(
                (
                    "history",
                    ((session_key, user_text, answer), kwargs),
                )
            )
            self.history.extend(
                (
                    {"role": "user", "content": user_text},
                    {
                        "role": "assistant",
                        "content": answer,
                        "memoryReceiptRef": kwargs.get(
                            "memory_receipt"
                        ),
                    },
                )
            )

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "answer_promises_search": (
                    lambda text: text == "SEARCH_PENDING?"
                ),
                "get_active_session_count": lambda: 0,
                "get_inflight_llm_requests": lambda: 0,
                "monotonic": time.monotonic,
                "authorize_action": authorize,
                "record_action_outcome": record_outcome,
                "commit_session_continuity": commit,
                "append_history": append_history,
            }
        )
        engine = self.create_engine()
        persisted: list[tuple[int, str]] = []
        engine.persist_state = lambda: persisted.append(
            (
                (
                    engine.state.current_plan.cursor
                    if engine.state.current_plan is not None
                    else -1
                ),
                str(engine.state.last_step_result.get("reason") or ""),
            )
        )
        engine.state.enabled = True
        engine.state.status = "running"
        engine.state.allowed_actions = [
            "assistant:send_followup",
            "assistant:idle",
        ]

        cycle = asyncio.create_task(engine.run_cycle())
        await asyncio.wait_for(commit_entered.wait(), timeout=1.0)
        cycle.cancel()
        await asyncio.sleep(0)

        try:
            self.assertFalse(cycle.done())
        finally:
            release_commit.set()
        with self.assertRaises(asyncio.CancelledError):
            await cycle

        self.assertIsNotNone(engine.state.current_plan)
        self.assertEqual(engine.state.current_plan.cursor, 1)
        self.assertEqual(persisted[-1], (1, "sent_followup"))
        self.assertEqual(
            len([kind for kind, _payload in self.events if kind == "send"]),
            1,
        )

    async def test_invalid_proactive_marker_releases_slot(self) -> None:
        private = "PRIVATE_AUTONOMY_MARKER_PATH"

        class InvalidMarker:
            def __bool__(self) -> bool:
                raise OSError(private)

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "select_and_mark_proactive_question": (
                    lambda **_kwargs: InvalidMarker()
                ),
            }
        )

        with self.assertRaisesRegex(OSError, private):
            await self.default_executor().maybe_ping_user_fn("확인")

        self.assertFalse(self.reply_slot_locks[
            "guild:11:reply:text:10"
        ].locked())
        self.assertEqual(self.events, [])

    async def test_send_followup_blocks_without_exact_recipient(self) -> None:
        self.followup_targets.clear()

        result = await self.default_executor().send_followup_fn("후속 답변")

        self.assertEqual(result, {"status": "blocked", "reason": "no_followup_channel"})

    async def test_voice_target_is_not_used_for_text_followup(self) -> None:
        self.followup_targets.clear()
        self.last_active.clear()
        voice_key = "guild:11:voice:10:user:42"
        self.followup_targets[voice_key] = {
            "channel_id": 10,
            "message_id": 1,
        }
        self.last_active[voice_key] = 1.0
        executor = self.default_executor()

        observation = await executor.observe()
        result = await executor.send_followup_fn("VOICE_TARGET")

        self.assertEqual(observation["known_followup_channels"], 0)
        self.assertEqual(
            result,
            {"status": "blocked", "reason": "no_followup_channel"},
        )
        self.assertEqual(self.events, [])

    async def test_refresh_cognitive_state_tracks_and_clears_task(self) -> None:
        result = await self.default_executor().refresh_cognitive_state_fn()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reason"], "router_refreshed")
        self.assertTrue(result["verified"])
        self.assertEqual(
            result["evidence_code"],
            "cognitive_state_updated",
        )
        self.assertEqual(result["elapsed_ms"], 250.0)
        self.assertEqual(self.last_refresh[11], 100.0)
        self.assertNotIn(11, self.refresh_tasks)
        refresh_event = next(
            payload
            for kind, payload in self.events
            if kind == "refresh"
        )
        self.assertEqual(
            refresh_event[1]["session_key"],
            self.target_session_key,
        )
        self.assertEqual(refresh_event[1]["room_key"], "text:10")
        self.assertEqual(refresh_event[1]["person_key"], "user:42")
        self.assertEqual(
            refresh_event[1]["session_memory_key"],
            f"{self.target_session_key}:user:42",
        )

    async def test_refresh_cognitive_state_does_not_overtake_text_reply(
        self,
    ) -> None:
        reply_lock = self.reply_slot_locks.setdefault(
            "guild:11:reply:text:10",
            asyncio.Lock(),
        )
        await reply_lock.acquire()

        result = await self.default_executor().refresh_cognitive_state_fn()

        self.assertEqual(
            result,
            {
                "status": "blocked",
                "reason": "followup_reply_slot_busy",
            },
        )
        self.assertEqual(self.events, [])
        self.assertNotIn(11, self.refresh_tasks)
        reply_lock.release()

    async def test_refresh_hands_reply_slot_to_next_text_turn(
        self,
    ) -> None:
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()
        normal_entered_state = asyncio.Event()
        release_normal = asyncio.Event()

        async def update_cognitive(
            *_args: Any,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            refresh_started.set()
            await release_refresh.wait()
            return {
                "updated_at": "now",
                "action": "reply",
                "confidence": 0.9,
            }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "update_cognitive_state": update_cognitive,
            }
        )
        refresh_task = asyncio.create_task(
            self.default_executor().refresh_cognitive_state_fn()
        )
        await refresh_started.wait()
        reply_lock = self.reply_slot_locks[
            "guild:11:reply:text:10"
        ]
        state_lock = self.session_locks[self.target_session_key]
        self.assertFalse(reply_lock.locked())
        self.assertTrue(state_lock.locked())

        async def next_text_turn() -> None:
            await reply_lock.acquire()
            try:
                async with state_lock:
                    normal_entered_state.set()
                    await release_normal.wait()
            finally:
                if reply_lock.locked():
                    reply_lock.release()

        normal_task = asyncio.create_task(next_text_turn())
        await asyncio.sleep(0)
        self.assertTrue(reply_lock.locked())
        self.assertFalse(normal_entered_state.is_set())
        release_refresh.set()
        await normal_entered_state.wait()
        result = await refresh_task
        self.assertEqual(result["status"], "ok")
        self.assertTrue(reply_lock.locked())
        release_normal.set()
        await normal_task
        self.assertFalse(reply_lock.locked())

    async def test_bound_refresh_reuses_current_task_deletion_lease(
        self,
    ) -> None:
        note_id = "concept-b123456789abcdef"
        self.history = [
            {"role": "user", "content": "SAFE_USER_TEXT"},
            {
                "role": "assistant",
                "content": "BOUND_CURRENT_TEXT",
                "memoryReceiptRef": bound_receipt(note_id),
            },
        ]

        async def update_cognitive(
            *_args: Any,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            self.assertIs(
                self.refresh_tasks[11],
                asyncio.current_task(),
            )
            exposure = current_memory_exposure_position()
            self.assertIsNotNone(exposure)
            with memory_exposure_guard(
                expected_position=exposure,
                required=True,
                index_dir=self.memory_index_dir,
            ):
                return {
                    "updated_at": "now",
                    "action": "reply",
                    "confidence": 0.9,
                }

        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "update_cognitive_state": update_cognitive,
            }
        )

        with self.unconfigured_memory_authenticity():
            result = await self.default_executor().refresh_cognitive_state_fn()

        self.assertEqual(result["status"], "ok")
        self.assertNotIn(11, self.refresh_tasks)

    async def test_maybe_ping_respects_recent_ping_cooldown(self) -> None:
        self.last_ping[11] = 50.0

        result = await self.default_executor().maybe_ping_user_fn("확인")

        self.assertEqual(result, {"status": "blocked", "reason": "ping_cooldown"})

    async def test_memory_bound_autonomy_followup_keeps_compact_receipt(
        self,
    ) -> None:
        note_id = "concept-a123456789abcdef"
        self.history = [
            {"role": "user", "content": "SAFE_USER_TEXT"},
            {
                "role": "assistant",
                "content": "BOUND_CURRENT_TEXT",
                "memoryReceiptRef": bound_receipt(note_id),
            },
        ]
        marks: list[dict[str, Any]] = []
        self.deps = AutonomyRuntimeFactoryDeps(
            **{
                **self.deps.__dict__,
                "select_and_mark_proactive_question": (
                    lambda **kwargs: marks.append(kwargs)
                    or {"ask_text": "후속 확인"}
                ),
            }
        )
        reply_lock = self.reply_slot_locks.setdefault(
            "guild:11:reply:text:10",
            asyncio.Lock(),
        )
        await reply_lock.acquire()

        with self.unconfigured_memory_authenticity():
            blocked = await self.default_executor().maybe_ping_user_fn(
                "확인"
            )
            self.assertEqual(
                blocked,
                {
                    "status": "blocked",
                    "reason": "followup_reply_slot_busy",
                },
            )
            self.assertEqual(marks, [])
            reply_lock.release()
            result = await self.default_executor().maybe_ping_user_fn(
                "확인"
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(marks), 1)
        history_payload = next(
            payload
            for kind, payload in self.events
            if kind == "history"
        )
        self.assertEqual(history_payload[0][1], "[autonomy]")
        receipt = history_payload[1]["memory_receipt"]
        self.assertEqual(receipt["state"], "bound")
        self.assertEqual(receipt["suppliedNoteIds"], [note_id])

    def test_main_delegates_autonomy_factory_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_source = (
            RUNTIME_ROOT / "evelyn_core" / "autonomy_runtime_composition.py"
        ).read_text(encoding="utf-8")
        start = source.index(
            "get_or_create_autonomy_engine = autonomy_runtime_composition.get_or_create_autonomy_engine"
        )
        end = source.index(
            "guild_runtime_reset_composition = GuildRuntimeResetComposition(", start
        )
        function_source = source[start:end]

        self.assertIn("autonomy_runtime_composition.get_or_create_autonomy_engine", function_source)
        self.assertIn("get_or_create_autonomy_engine_from_runtime(", composition_source)
        self.assertNotIn("async def _default_observe", composition_source)
        self.assertNotIn("AutonomyEngine(", composition_source)


if __name__ == "__main__":
    unittest.main()
