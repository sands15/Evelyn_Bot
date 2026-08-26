from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_ingress_runtime import (  # noqa: E402
    VoiceIngressEntrypointDeps,
    VoiceIngressRuntimeDeps,
    advance_voice_ingress_epoch,
    current_voice_ingress_epoch,
    flush_voice_utterance_buffer_from_runtime,
    process_member_audio_from_runtime,
    schedule_voice_utterance_item_from_runtime,
    set_voice_transition_pending,
    voice_ingress_epoch_is_current,
    voice_ingress_worker_from_runtime,
    voice_utterance_buffer_key,
)
from evelyn_core.voice_utterance import UtteranceAssemblyConfig, discord_pcm_seconds  # noqa: E402
from evelyn_core.voice_validation import SUITE_ID, VoiceValidationManager  # noqa: E402
from evelyn_core.turn_lifecycle import TurnScope, TurnScopeRegistry  # noqa: E402
from evelyn_core.local_control_voice_runtime import (  # noqa: E402
    build_local_control_voice_member_from_runtime,
)


@dataclass(frozen=True)
class FakeEnqueueResult:
    accepted: bool
    dropped_oldest_item: dict[str, Any] | None = None


@dataclass(frozen=True)
class FakeDequeuePlan:
    should_drop_stale: bool = False
    queue_wait_ms: float = 0.0
    max_age_ms: float = 8000.0


@dataclass
class IngressHarness:
    config: UtteranceAssemblyConfig = field(
        default_factory=lambda: UtteranceAssemblyConfig(enabled=True, commit_wait_sec=999.0, pad_ms=100, max_audio_sec=60.0)
    )
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=8))
    buffers: dict[str, dict[str, Any]] = field(default_factory=dict)
    flush_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    counters: list[str] = field(default_factory=list)
    processed: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    epochs: dict[int, int] = field(default_factory=lambda: {7: 0})

    def deps(self) -> VoiceIngressRuntimeDeps:
        async def process_member_audio(**kwargs: Any) -> None:
            self.processed.append(dict(kwargs))

        def enqueue_voice_ingress_item(queue: asyncio.Queue, item: dict[str, Any], **_kwargs: Any) -> FakeEnqueueResult:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                return FakeEnqueueResult(False)
            return FakeEnqueueResult(True)

        return VoiceIngressRuntimeDeps(
            voice_ingress_queue=self.queue,
            voice_utterance_buffers=self.buffers,
            voice_utterance_flush_tasks=self.flush_tasks,
            voice_utterance_assembly_config=self.config,
            voice_ingress_max_age_sec=8.0,
            voice_ingress_drop_oldest_on_full=True,
            voice_ingress_queue_max=8,
            evaluate_voice_ingress_dequeue=lambda *_args, **_kwargs: FakeDequeuePlan(),
            apply_voice_ingress_dequeue_debug_meta=lambda *_args, **_kwargs: None,
            enqueue_voice_ingress_item=enqueue_voice_ingress_item,
            increment_voice_pipeline_counter=lambda name: self.counters.append(name),
            voice_ingress_epoch_is_current=lambda guild_id, epoch: (
                voice_ingress_epoch_is_current(
                    self.epochs,
                    guild_id if guild_id is not None else 7,
                    epoch if epoch is not None else 0,
                )
            ),
            process_member_audio=process_member_audio,
            create_task=asyncio.create_task,
            log=lambda message: self.logs.append(str(message)),
            monotonic=lambda: 100.0,
        )


class VoiceIngressRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def test_disabled_utterance_assembly_enqueues_immediately(self) -> None:
        harness = IngressHarness(config=UtteranceAssemblyConfig(enabled=False))
        item = {"session_key": "s1", "pcm_bytes": b"abc", "debug_meta": {}}

        await schedule_voice_utterance_item_from_runtime(item, deps=harness.deps())

        queued = await harness.queue.get()
        self.assertEqual(queued["pcm_bytes"], b"abc")
        self.assertEqual(queued["enqueued_at"], 100.0)
        self.assertEqual(queued["debug_meta"]["voice_queue_depth_at_enqueue"], 0)
        self.assertEqual(harness.buffers, {})

    async def test_validation_attempts_use_distinct_utterance_buffers(self) -> None:
        base = {
            "session_key": "s1",
            "debug_meta": {
                "validation_session_id": "validation-1",
                "validation_step_id": "02-listening",
                "validation_attempt_id": "attempt-1",
            },
        }
        retried = {
            **base,
            "debug_meta": {
                **base["debug_meta"],
                "validation_attempt_id": "attempt-2",
            },
        }

        self.assertNotEqual(
            voice_utterance_buffer_key(base),
            voice_utterance_buffer_key(retried),
        )

    async def test_worker_drops_item_from_rotated_validation_attempt(self) -> None:
        harness = IngressHarness()
        item = {
            "session_key": "s1",
            "pcm_bytes": b"private-pcm",
            "debug_meta": {
                "validation_session_id": "validation-1",
                "validation_step_id": "01-wake",
                "validation_attempt_id": "stale-attempt",
            },
        }
        await harness.queue.put(item)

        with patch(
            "evelyn_core.voice_ingress_runtime.validation_attempt_binding_is_current",
            return_value=False,
        ) as guard:
            worker = asyncio.create_task(
                voice_ingress_worker_from_runtime(deps=harness.deps())
            )
            await asyncio.wait_for(harness.queue.join(), timeout=1.0)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

        guard.assert_called_once_with(
            item["debug_meta"],
            surface="discord",
            reject_unbound_when_active=True,
        )

        self.assertEqual(harness.processed, [])
        self.assertIn("validation_attempt_stale_drop_count", harness.counters)
        self.assertIn(
            "[VOICE QUEUE DROP] reason=validation_attempt_stale",
            harness.logs,
        )

    async def test_reset_epoch_drops_old_queue_item_but_keeps_fresh_and_other_guild(
        self,
    ) -> None:
        harness = IngressHarness(epochs={7: 1, 8: 4})

        def member(guild_id: int) -> Any:
            return SimpleNamespace(
                id=42,
                guild=SimpleNamespace(
                    id=guild_id,
                    voice_client=SimpleNamespace(),
                ),
            )

        await harness.queue.put(
            {
                "session_key": "guild:7:voice:9:user:42",
                "member": member(7),
                "pcm_bytes": b"private-pre-reset-pcm",
                "debug_meta": {},
                "voice_ingress_epoch": 0,
            }
        )
        await harness.queue.put(
            {
                "session_key": "guild:7:voice:9:user:42",
                "member": member(7),
                "pcm_bytes": b"fresh-pcm",
                "debug_meta": {},
                "voice_ingress_epoch": 1,
            }
        )
        await harness.queue.put(
            {
                "session_key": "guild:8:voice:10:user:42",
                "member": member(8),
                "pcm_bytes": b"other-guild-pcm",
                "debug_meta": {},
                "voice_ingress_epoch": 4,
            }
        )

        worker = asyncio.create_task(
            voice_ingress_worker_from_runtime(deps=harness.deps())
        )
        await asyncio.wait_for(harness.queue.join(), timeout=1.0)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        self.assertEqual(
            [item["pcm_bytes"] for item in harness.processed],
            [b"fresh-pcm", b"other-guild-pcm"],
        )
        self.assertEqual(
            harness.counters.count("voice_ingress_epoch_stale_drop_count"),
            1,
        )
        self.assertNotIn("private-pre-reset-pcm", repr(harness.logs))

    async def test_guild_reset_block_drops_fresh_worker_item_only_for_target(
        self,
    ) -> None:
        harness = IngressHarness(epochs={0: 0, 7: 1, 8: 4})
        guild_is_open = lambda guild_id: guild_id != 7

        def member(guild_id: int) -> Any:
            return SimpleNamespace(
                id=42,
                guild=SimpleNamespace(
                    id=guild_id,
                    voice_client=SimpleNamespace(),
                ),
            )

        for guild_id, epoch, pcm in (
            (7, 1, b"blocked-pcm"),
            (8, 4, b"other-pcm"),
            (0, 0, b"local-pcm"),
        ):
            await harness.queue.put(
                {
                    "session_key": f"guild:{guild_id}:voice:user:42",
                    "member": member(guild_id),
                    "pcm_bytes": pcm,
                    "debug_meta": {},
                    "voice_ingress_epoch": epoch,
                }
            )

        deps = replace(
            harness.deps(),
            voice_ingress_epoch_is_current=lambda guild_id, epoch: (
                voice_ingress_epoch_is_current(
                    harness.epochs,
                    guild_id,
                    epoch,
                    guild_is_open=guild_is_open,
                )
            ),
        )
        worker = asyncio.create_task(
            voice_ingress_worker_from_runtime(deps=deps)
        )
        await asyncio.wait_for(harness.queue.join(), timeout=1.0)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        self.assertEqual(
            [item["pcm_bytes"] for item in harness.processed],
            [b"other-pcm", b"local-pcm"],
        )

    async def test_reset_epoch_separates_old_buffer_from_fresh_audio(self) -> None:
        harness = IngressHarness(epochs={7: 0})
        member = SimpleNamespace(
            id=42,
            guild=SimpleNamespace(id=7, voice_client=SimpleNamespace()),
        )
        base = {
            "session_key": "guild:7:voice:9:user:42",
            "member": member,
            "debug_meta": {},
        }
        await schedule_voice_utterance_item_from_runtime(
            {
                **base,
                "segment_id": 1,
                "pcm_bytes": b"old-pcm",
                "voice_ingress_epoch": 0,
            },
            deps=harness.deps(),
        )

        advance_voice_ingress_epoch(harness.epochs, 7)
        await schedule_voice_utterance_item_from_runtime(
            {
                **base,
                "segment_id": 2,
                "pcm_bytes": b"fresh-pcm",
                "voice_ingress_epoch": 1,
            },
            deps=harness.deps(),
        )
        await flush_voice_utterance_buffer_from_runtime(
            base["session_key"],
            deps=harness.deps(),
        )

        queued = await harness.queue.get()
        self.assertEqual(queued["pcm_bytes"], b"fresh-pcm")
        self.assertEqual(queued["voice_ingress_epoch"], 1)
        self.assertEqual(
            harness.counters.count("voice_ingress_epoch_stale_drop_count"),
            1,
        )

    async def test_worker_logs_only_exception_type_and_continues(self) -> None:
        private_detail = "PRIVATE_VOICE_WORKER_ERROR_7f19"
        harness = IngressHarness()
        processed: list[str] = []
        await harness.queue.put({"session_key": "fail", "debug_meta": {}})
        await harness.queue.put({"session_key": "next", "debug_meta": {}})

        async def process_member_audio(**item: Any) -> None:
            if item["session_key"] == "fail":
                raise RuntimeError(private_detail)
            processed.append(item["session_key"])

        deps = replace(
            harness.deps(),
            process_member_audio=process_member_audio,
        )
        worker = asyncio.create_task(voice_ingress_worker_from_runtime(deps=deps))
        await asyncio.wait_for(harness.queue.join(), timeout=1.0)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        self.assertEqual(processed, ["next"])
        self.assertIn(
            "[VOICE WORKER] 실패: errorType=RuntimeError",
            harness.logs,
        )
        self.assertNotIn(private_detail, repr(harness.logs))

    async def test_accepted_turn_handoff_allows_next_ingress_to_cancel_delivery(self) -> None:
        harness = IngressHarness()
        registry = TurnScopeRegistry()
        started = asyncio.Event()
        cancelled: list[str] = []
        processed: list[str] = []
        for session_key in ("guild-a", "guild-b"):
            await harness.queue.put(
                {
                    "session_key": session_key,
                    "debug_meta": {},
                }
            )

        async def process_member_audio(**item: Any) -> None:
            session_key = item["session_key"]
            release_ingress_worker = item.get(
                "release_ingress_worker",
                lambda: None,
            )
            scope = TurnScope(f"turn-{session_key}")
            registry.replace_room_scope("guild:1:voice:9", scope)
            task = scope.register_task()
            try:
                if session_key == "guild-a":
                    started.set()
                    release_ingress_worker()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.append(session_key)
                        raise
                processed.append(session_key)
                release_ingress_worker()
            finally:
                scope.unregister_task(task)

        worker = asyncio.create_task(
            voice_ingress_worker_from_runtime(
                deps=replace(
                    harness.deps(),
                    process_member_audio=process_member_audio,
                )
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await asyncio.wait_for(harness.queue.join(), timeout=1.0)

        self.assertFalse(worker.done())
        self.assertEqual(processed, ["guild-b"])
        self.assertEqual(cancelled, ["guild-a"])
        self.assertEqual(registry.cancelled_stale_turn_count, 1)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def test_worker_drops_unbound_item_queued_before_validation_during_silence(self) -> None:
        harness = IngressHarness()
        await harness.queue.put(
            {
                "session_key": "s-before-validation",
                "pcm_bytes": b"private-pcm-before-validation",
                "debug_meta": {},
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"EVELYN_RUNTIME_ARTIFACTS_DIR": temp_dir},
        ):
            manager = VoiceValidationManager(root=Path(temp_dir))
            started = manager.start(
                suite=SUITE_ID,
                surfaces=("discord",),
                capabilities={
                    "voiceDiscord": {
                        "state": "ready",
                        "ready": True,
                        "blockers": [],
                    }
                },
                discord_target={"guildId": "7", "channelId": "9"},
            )
            self.assertTrue(started["ok"], started)
            assert manager._session is not None
            manager._session["_stepIndex"] = 10
            manager._sync_current_step()
            manager._persist()
            self.assertEqual(manager.snapshot()["currentStep"]["kind"], "silence")

            worker = asyncio.create_task(
                voice_ingress_worker_from_runtime(deps=harness.deps())
            )
            await asyncio.wait_for(harness.queue.join(), timeout=1.0)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

        self.assertEqual(harness.processed, [])
        self.assertIn("validation_attempt_stale_drop_count", harness.counters)
        self.assertIn(
            "[VOICE QUEUE DROP] reason=validation_attempt_stale",
            harness.logs,
        )

    async def test_flush_merges_segments_and_enqueues_base_item(self) -> None:
        harness = IngressHarness()
        left = b"\x01\x00" * (4800 * 2)
        right = b"\x02\x00" * (4800 * 2)

        await schedule_voice_utterance_item_from_runtime(
            {"session_key": "s1", "segment_id": 1, "pcm_bytes": left, "debug_meta": {"source": "test"}},
            deps=harness.deps(),
        )
        await schedule_voice_utterance_item_from_runtime(
            {"session_key": "s1", "segment_id": 2, "pcm_bytes": right, "ingress_during_reply": True},
            deps=harness.deps(),
        )
        await flush_voice_utterance_buffer_from_runtime("s1", deps=harness.deps())

        queued = await harness.queue.get()
        self.assertGreater(discord_pcm_seconds(queued["pcm_bytes"]), discord_pcm_seconds(left) + discord_pcm_seconds(right))
        self.assertTrue(queued["ingress_during_reply"])
        self.assertEqual(queued["debug_meta"]["assembled_segment_ids"], [1, 2])
        self.assertTrue(queued["debug_meta"]["assembled_utterance"])
        self.assertEqual(queued["debug_meta"]["utterance_assembly"]["segment_count"], 2)
        self.assertIn("utterance_assembly_flush_count", harness.counters)
        self.assertIn("utterance_assembly_merge_count", harness.counters)
        self.assertTrue(any("VOICE UTTERANCE MERGE" in message for message in harness.logs))
        self.assertNotIn("s1", harness.buffers)

    async def test_channel_move_drops_buffered_and_queued_old_listener_audio(self) -> None:
        harness = IngressHarness()
        source_client = SimpleNamespace(
            _listener_generation=4,
            channel=SimpleNamespace(id=9),
        )
        member = SimpleNamespace(
            id=42,
            guild=SimpleNamespace(id=7, voice_client=source_client),
        )
        stale_item = {
            "session_key": "voice:7:42",
            "member": member,
            "pcm_bytes": b"old-channel-pcm",
            "debug_meta": {},
            "voice_listener_binding": (source_client, 4, 9),
        }

        await schedule_voice_utterance_item_from_runtime(
            stale_item,
            deps=harness.deps(),
        )
        source_client._listener_generation = 5
        source_client.channel = SimpleNamespace(id=10)
        await flush_voice_utterance_buffer_from_runtime(
            "voice:7:42",
            deps=harness.deps(),
        )

        self.assertTrue(harness.queue.empty())
        await harness.queue.put(dict(stale_item))
        await harness.queue.put(
            {
                "session_key": "voice:7:42",
                "member": member,
                "pcm_bytes": b"unbound-old-channel-pcm",
                "debug_meta": {},
            }
        )
        worker = asyncio.create_task(
            voice_ingress_worker_from_runtime(deps=harness.deps())
        )
        await asyncio.wait_for(harness.queue.join(), timeout=1.0)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        self.assertEqual(harness.processed, [])
        self.assertEqual(
            harness.counters.count("listener_generation_stale_drop_count"),
            3,
        )

    async def test_process_member_audio_builds_ingress_item(self) -> None:
        scheduled: list[dict[str, Any]] = []
        calls: list[str] = []
        voice_client = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            _listener_generation=3,
        )
        member = SimpleNamespace(
            id=42,
            bot=False,
            guild=SimpleNamespace(id=7, voice_client=voice_client),
        )
        listener_binding = (voice_client, 3, 9)

        async def ensure_startup_components_ready() -> None:
            calls.append("startup")

        async def admit_search_followup_recovery() -> bool:
            calls.append("recovery")
            return True

        async def schedule_voice_utterance_item(item: dict[str, Any]) -> None:
            scheduled.append(item)

        def build_voice_ingress_item(**kwargs: Any) -> dict[str, Any]:
            return dict(kwargs)

        deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=ensure_startup_components_ready,
            normalize_voice_debug_meta=lambda meta: {"source": "discord_voice", **(meta or {})},
            voice_ingress_source=lambda meta: str(meta.get("source") or ""),
            should_drop_discord_audio_for_local_mic=lambda *_args, **_kwargs: False,
            ensure_voice_worker_started=lambda: calls.append("worker"),
            build_voice_ingress_context=lambda **_kwargs: SimpleNamespace(
                room_session_key="room-session",
                session_key="session-1",
                room_key="room-key",
                person_key="person-key",
                session_memory_key="session-memory",
            ),
            next_segment_id=lambda session_key: 5,
            new_turn_id=lambda: "turn-1",
            room_state_snapshot=lambda room_session_key: {"reply_in_progress": True, "owner_user_id": 99},
            validation_context_provider=lambda **kwargs: {
                "sessionId": "validation-1",
                "stepId": "03-interrupt",
                "attempt": 2,
                "attemptId": "attempt-private-2",
                "discordTarget": {"guildId": "7", "channelId": "9"},
                "preferInterrupt": kwargs["prefer_interrupt"],
            },
            capture_voice_ingress_epoch=lambda guild_id: 5,
            build_voice_ingress_item=build_voice_ingress_item,
            voice_ingress_queue_depth=lambda: 3,
            schedule_voice_utterance_item=schedule_voice_utterance_item,
            monotonic=lambda: 123.0,
            admit_search_followup_recovery=admit_search_followup_recovery,
        )

        await process_member_audio_from_runtime(
            member,
            b"pcm",
            {
                "unstable": True,
                "_voice_listener_binding": listener_binding,
            },
            deps=deps,
        )

        self.assertEqual(calls, ["startup", "recovery", "worker"])
        self.assertEqual(len(scheduled), 1)
        item = scheduled[0]
        self.assertIs(item["member"], member)
        self.assertEqual(item["pcm_bytes"], b"pcm")
        self.assertEqual(item["debug_meta"]["source"], "discord_voice")
        self.assertTrue(item["debug_meta"]["unstable"])
        self.assertNotIn("_voice_listener_binding", item["debug_meta"])
        self.assertIs(item["voice_listener_binding"], listener_binding)
        self.assertEqual(item["debug_meta"]["validation_session_id"], "validation-1")
        self.assertEqual(item["debug_meta"]["validation_step_id"], "03-interrupt")
        self.assertEqual(item["debug_meta"]["validation_attempt"], 2)
        self.assertEqual(item["debug_meta"]["validation_attempt_id"], "attempt-private-2")
        self.assertEqual(item["session_key"], "session-1")
        self.assertEqual(item["room_session_key"], "room-session")
        self.assertEqual(item["turn_id"], "turn-1")
        self.assertEqual(item["segment_id"], 5)
        self.assertTrue(item["ingress_during_reply"])
        self.assertEqual(item["owner_user_id_on_ingress"], 99)
        self.assertEqual(item["voice_ingress_epoch"], 5)
        self.assertEqual(item["queue_depth_at_enqueue"], 3)
        self.assertEqual(item["enqueued_at"], 123.0)

        scheduled.clear()
        voice_client._evelyn_voice_move_pending = True
        await process_member_audio_from_runtime(
            member,
            b"pcm",
            {"_voice_listener_binding": listener_binding},
            deps=deps,
        )
        self.assertEqual(scheduled, [])
        del voice_client._evelyn_voice_move_pending

        set_voice_transition_pending(member.guild.id, True)
        try:
            await process_member_audio_from_runtime(
                member,
                b"pcm",
                {"_voice_listener_binding": listener_binding},
                deps=deps,
            )
            self.assertEqual(scheduled, [])
        finally:
            set_voice_transition_pending(member.guild.id, False)

        for guild_id, channel_id in ((8, 9), (7, 10)):
            with self.subTest(guild_id=guild_id, channel_id=channel_id):
                scheduled.clear()
                mismatched_member = SimpleNamespace(
                    id=42,
                    bot=False,
                    guild=SimpleNamespace(
                        id=guild_id,
                        voice_client=SimpleNamespace(
                            channel=SimpleNamespace(id=channel_id)
                        ),
                    ),
                )
                await process_member_audio_from_runtime(
                    mismatched_member,
                    b"pcm",
                    {"validation_attempt_id": "untrusted"},
                    deps=deps,
                )
                self.assertEqual(len(scheduled), 1)
                mismatched_meta = scheduled[0]["debug_meta"]
                self.assertNotIn("validation_session_id", mismatched_meta)
                self.assertNotIn("validation_step_id", mismatched_meta)
                self.assertNotIn("validation_attempt_id", mismatched_meta)

    async def test_process_member_audio_fails_closed_before_turn_when_recovery_pending(
        self,
    ) -> None:
        scheduled: list[dict[str, Any]] = []
        voice_client = SimpleNamespace(channel=SimpleNamespace(id=9))
        member = SimpleNamespace(
            id=42,
            bot=False,
            guild=SimpleNamespace(id=7, voice_client=voice_client),
        )

        async def recovery_pending() -> bool:
            return False

        deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=lambda: asyncio.sleep(0),
            normalize_voice_debug_meta=lambda meta: dict(meta or {}),
            voice_ingress_source=lambda _meta: "discord_voice",
            should_drop_discord_audio_for_local_mic=lambda *_args, **_kwargs: False,
            ensure_voice_worker_started=lambda: self.fail(
                "recovery-pending audio must not start the worker"
            ),
            build_voice_ingress_context=lambda **_kwargs: self.fail(
                "recovery-pending audio must not build context"
            ),
            next_segment_id=lambda _session_key: self.fail(
                "recovery-pending audio must not allocate a segment"
            ),
            new_turn_id=lambda: self.fail(
                "recovery-pending audio must not allocate a turn"
            ),
            room_state_snapshot=lambda _room_session_key: {},
            validation_context_provider=lambda **_kwargs: None,
            capture_voice_ingress_epoch=lambda _guild_id: 0,
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=lambda item: scheduled.append(item),
            admit_search_followup_recovery=recovery_pending,
        )

        await process_member_audio_from_runtime(member, b"pcm", {}, deps=deps)

        self.assertEqual(scheduled, [])

    async def test_reset_during_startup_wait_drops_pre_reset_audio(self) -> None:
        epochs = {7: 0}
        startup_waiting = asyncio.Event()
        resume_startup = asyncio.Event()
        scheduled: list[dict[str, Any]] = []
        worker_start_count = 0
        member = SimpleNamespace(
            id=42,
            bot=False,
            guild=SimpleNamespace(
                id=7,
                voice_client=SimpleNamespace(channel=SimpleNamespace(id=9)),
            ),
        )

        async def ensure_startup_components_ready() -> None:
            startup_waiting.set()
            await resume_startup.wait()

        def ensure_voice_worker_started() -> None:
            nonlocal worker_start_count
            worker_start_count += 1

        deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=ensure_startup_components_ready,
            normalize_voice_debug_meta=lambda meta: dict(meta or {}),
            voice_ingress_source=lambda _meta: "discord_voice",
            should_drop_discord_audio_for_local_mic=lambda *_args, **_kwargs: False,
            ensure_voice_worker_started=ensure_voice_worker_started,
            build_voice_ingress_context=lambda **_kwargs: self.fail(
                "stale audio must stop before context construction"
            ),
            next_segment_id=lambda _session_key: 1,
            new_turn_id=lambda: "turn-1",
            room_state_snapshot=lambda _room_session_key: {},
            validation_context_provider=lambda **_kwargs: None,
            capture_voice_ingress_epoch=lambda guild_id: current_voice_ingress_epoch(
                epochs,
                guild_id,
            ),
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=lambda item: scheduled.append(item),
        )

        task = asyncio.create_task(
            process_member_audio_from_runtime(
                member,
                b"private-pre-reset-pcm",
                {},
                deps=deps,
            )
        )
        await asyncio.wait_for(startup_waiting.wait(), timeout=1.0)
        advance_voice_ingress_epoch(epochs, 7)
        resume_startup.set()
        await asyncio.wait_for(task, timeout=1.0)

        self.assertEqual(scheduled, [])
        self.assertEqual(worker_start_count, 0)

    async def test_guild_reset_block_rejects_new_target_voice_entry(self) -> None:
        epochs = {0: 0, 7: 1, 8: 4}
        guild_is_open = lambda guild_id: guild_id != 7
        scheduled: list[dict[str, Any]] = []

        self.assertEqual(
            current_voice_ingress_epoch(
                epochs,
                0,
                guild_is_open=lambda _guild_id: False,
            ),
            0,
        )
        self.assertTrue(
            voice_ingress_epoch_is_current(
                epochs,
                0,
                0,
                guild_is_open=lambda _guild_id: False,
            )
        )

        def member(guild_id: int) -> Any:
            return SimpleNamespace(
                id=42,
                bot=False,
                guild=SimpleNamespace(
                    id=guild_id,
                    voice_client=SimpleNamespace(
                        channel=SimpleNamespace(id=9)
                    ),
                ),
            )

        deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=lambda: asyncio.sleep(0),
            normalize_voice_debug_meta=lambda meta: dict(meta or {}),
            voice_ingress_source=lambda _meta: "discord_voice",
            should_drop_discord_audio_for_local_mic=(
                lambda *_args, **_kwargs: False
            ),
            ensure_voice_worker_started=lambda: None,
            build_voice_ingress_context=lambda **kwargs: SimpleNamespace(
                room_session_key=f"guild:{kwargs['guild_id']}:voice:room",
                session_key=f"guild:{kwargs['guild_id']}:voice:user:42",
                room_key="voice:9",
                person_key="user:42",
                session_memory_key=(
                    f"guild:{kwargs['guild_id']}:voice:user:42"
                ),
            ),
            next_segment_id=lambda _session_key: 1,
            new_turn_id=lambda: "turn-voice",
            room_state_snapshot=lambda _room_session_key: {},
            validation_context_provider=lambda **_kwargs: None,
            capture_voice_ingress_epoch=lambda guild_id: (
                current_voice_ingress_epoch(
                    epochs,
                    guild_id,
                    guild_is_open=guild_is_open,
                )
            ),
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=(
                lambda item: asyncio.sleep(
                    0,
                    result=scheduled.append(item),
                )
            ),
        )

        await process_member_audio_from_runtime(
            member(7),
            b"blocked-pcm",
            {},
            deps=deps,
        )
        await process_member_audio_from_runtime(
            member(8),
            b"other-pcm",
            {},
            deps=deps,
        )

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0]["member"].guild.id, 8)

    async def test_default_local_control_guild_zero_captures_epoch_and_schedules(
        self,
    ) -> None:
        epochs: dict[int, int] = {}
        scheduled: list[dict[str, Any]] = []
        member = build_local_control_voice_member_from_runtime(
            local_control_guild_id=0,
            local_control_guild_name="local",
            local_mic_discord_user_ids=set(),
            local_mic_user_name="local-user",
        )

        async def schedule_voice_utterance_item(item: dict[str, Any]) -> None:
            scheduled.append(item)

        deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=lambda: asyncio.sleep(0),
            normalize_voice_debug_meta=lambda meta: dict(meta or {}),
            voice_ingress_source=lambda _meta: "local_mic",
            should_drop_discord_audio_for_local_mic=lambda *_args, **_kwargs: False,
            ensure_voice_worker_started=lambda: None,
            build_voice_ingress_context=lambda **_kwargs: SimpleNamespace(
                room_session_key="guild:0:local:room",
                session_key="guild:0:local:user:0",
                room_key=None,
                person_key=None,
                session_memory_key=None,
            ),
            next_segment_id=lambda _session_key: 1,
            new_turn_id=lambda: "turn-local",
            room_state_snapshot=lambda _room_session_key: {},
            validation_context_provider=lambda **_kwargs: None,
            capture_voice_ingress_epoch=lambda guild_id: current_voice_ingress_epoch(
                epochs,
                guild_id,
            ),
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=schedule_voice_utterance_item,
        )

        await process_member_audio_from_runtime(
            member,
            b"local-pcm",
            {"source": "local_mic"},
            deps=deps,
        )

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0]["voice_ingress_epoch"], 0)
        self.assertEqual(scheduled[0]["member"].guild.id, 0)

    async def test_process_member_audio_respects_local_mic_suppression(self) -> None:
        scheduled: list[dict[str, Any]] = []
        member = SimpleNamespace(
            id=42,
            bot=False,
            guild=SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=SimpleNamespace(id=9))),
        )

        async def ensure_startup_components_ready() -> None:
            return None

        async def schedule_voice_utterance_item(item: dict[str, Any]) -> None:
            scheduled.append(item)

        deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=ensure_startup_components_ready,
            normalize_voice_debug_meta=lambda meta: dict(meta or {}),
            voice_ingress_source=lambda _meta: "discord_voice",
            should_drop_discord_audio_for_local_mic=lambda *_args, **_kwargs: True,
            ensure_voice_worker_started=lambda: (_ for _ in ()).throw(AssertionError("unexpected")),
            build_voice_ingress_context=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            next_segment_id=lambda _session_key: 0,
            new_turn_id=lambda: "turn",
            room_state_snapshot=lambda _room_session_key: {},
            validation_context_provider=lambda **_kwargs: None,
            capture_voice_ingress_epoch=lambda _guild_id: 0,
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=schedule_voice_utterance_item,
        )

        await process_member_audio_from_runtime(member, b"pcm", {}, deps=deps)

        self.assertEqual(scheduled, [])


if __name__ == "__main__":
    unittest.main()
