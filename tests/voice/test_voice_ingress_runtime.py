from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_ingress_runtime import (  # noqa: E402
    VoiceIngressEntrypointDeps,
    VoiceIngressRuntimeDeps,
    flush_voice_utterance_buffer_from_runtime,
    process_member_audio_from_runtime,
    schedule_voice_utterance_item_from_runtime,
)
from evelyn_core.voice_utterance import UtteranceAssemblyConfig, discord_pcm_seconds  # noqa: E402


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

    async def test_process_member_audio_builds_ingress_item(self) -> None:
        scheduled: list[dict[str, Any]] = []
        calls: list[str] = []
        member = SimpleNamespace(
            id=42,
            bot=False,
            guild=SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=SimpleNamespace(id=9))),
        )

        async def ensure_startup_components_ready() -> None:
            calls.append("startup")

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
            build_voice_ingress_item=build_voice_ingress_item,
            voice_ingress_queue_depth=lambda: 3,
            schedule_voice_utterance_item=schedule_voice_utterance_item,
            monotonic=lambda: 123.0,
        )

        await process_member_audio_from_runtime(member, b"pcm", {"unstable": True}, deps=deps)

        self.assertEqual(calls, ["startup", "worker"])
        self.assertEqual(len(scheduled), 1)
        item = scheduled[0]
        self.assertIs(item["member"], member)
        self.assertEqual(item["pcm_bytes"], b"pcm")
        self.assertEqual(item["debug_meta"]["source"], "discord_voice")
        self.assertTrue(item["debug_meta"]["unstable"])
        self.assertEqual(item["session_key"], "session-1")
        self.assertEqual(item["room_session_key"], "room-session")
        self.assertEqual(item["turn_id"], "turn-1")
        self.assertEqual(item["segment_id"], 5)
        self.assertTrue(item["ingress_during_reply"])
        self.assertEqual(item["owner_user_id_on_ingress"], 99)
        self.assertEqual(item["queue_depth_at_enqueue"], 3)
        self.assertEqual(item["enqueued_at"], 123.0)

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
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=schedule_voice_utterance_item,
        )

        await process_member_audio_from_runtime(member, b"pcm", {}, deps=deps)

        self.assertEqual(scheduled, [])


if __name__ == "__main__":
    unittest.main()
