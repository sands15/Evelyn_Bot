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
from evelyn_core import memory_deletion_journal as journal
from evelyn_core.conversation_memory_receipt import (
    memory_receipt_ref_from_receipt,
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
                "allowed": action == "assistant:idle",
                "code": (
                    "authorized"
                    if action == "assistant:idle"
                    else "authorization_scope_denied"
                ),
            },
            record_action_outcome=lambda guild_id, action, result: (
                self.events.append(
                    ("outcome", (guild_id, action, result))
                )
            ),
            commit_session_continuity=commit_session_continuity,
            log=lambda *args, **kwargs: None,
        )

    def create_engine(self):
        return get_or_create_autonomy_engine_from_runtime(11, deps=self.deps)

    def default_executor(self):
        return self.create_engine().executor.default_executor

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

        self.assertEqual(self.events, [("send", (self.channels[10], "알림"))])

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
