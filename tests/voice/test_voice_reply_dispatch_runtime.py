from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_reply_dispatch_runtime import (
    VoiceReplyDispatchDeps,
    dispatch_voice_reply_from_runtime,
)


class VoiceReplyDispatchRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[Any, Any]] = []
        self.room_state = {"reply_in_progress": True}
        self.reply_deps = SimpleNamespace(marker="reply-deps")

        async def process_voice_reply(*, context: Any, deps: Any) -> None:
            self.calls.append((context, deps))

        self.deps = VoiceReplyDispatchDeps(
            room_state_snapshot=lambda _key: self.room_state,
            session_topic_ids={"voice:11:7": "topic-1"},
            monotonic=lambda: 123.5,
            process_voice_reply=process_voice_reply,
            active_conversation_awaiting_reply_sec=20.0,
            active_conversation_voice_sec=15.0,
            canned_wake_reply="네, 정훈님.",
        )

    async def dispatch(
        self,
        *,
        metrics: dict[str, Any] | None = None,
        member: Any | None = None,
        reply_deps: Any | None = None,
        voice_listener_binding: Any = None,
        deps: VoiceReplyDispatchDeps | None = None,
        release_ingress_worker: Any = None,
    ) -> Any:
        metrics = metrics if metrics is not None else {"meta": {}}
        await dispatch_voice_reply_from_runtime(
            guild_id=11,
            transcript=SimpleNamespace(final_text="안녕"),
            voice_segment=SimpleNamespace(duration_sec=1.2),
            session_key="voice:11:7",
            room_session_key="room:11",
            owner_user_id=7,
            source_turn_id="turn-1",
            segment_id=3,
            voiced_ms=820.0,
            raw_seconds=1.5,
            rms=0.08,
            wake_detected=True,
            metrics=metrics,
            member=member or SimpleNamespace(id=7, display_name="정훈"),
            room_key="room-memory",
            person_key="person-memory",
            session_memory_key="session-memory",
            voice_listener_binding=voice_listener_binding,
            release_ingress_worker=release_ingress_worker,
            reply_deps=reply_deps or self.reply_deps,
            deps=deps or self.deps,
        )
        return self.calls[-1][0] if self.calls else None

    async def test_dispatch_builds_context_and_preserves_reply_dependencies(self) -> None:
        release_ingress_worker = object()
        context = await self.dispatch(
            release_ingress_worker=release_ingress_worker,
        )

        self.assertIs(self.calls[0][1], self.reply_deps)
        self.assertEqual(context.guild_id, 11)
        self.assertEqual(context.session_key, "voice:11:7")
        self.assertEqual(context.source_turn_id, "turn-1")
        self.assertEqual(context.segment_id, 3)
        self.assertEqual(context.voiced_ms, 820.0)
        self.assertEqual(context.raw_seconds, 1.5)
        self.assertEqual(context.rms, 0.08)
        self.assertTrue(context.wake_detected)
        self.assertIs(context.release_ingress_worker, release_ingress_worker)

    async def test_dispatch_derives_room_topic_and_timing_state(self) -> None:
        context = await self.dispatch()

        self.assertTrue(context.reply_in_progress)
        self.assertEqual(context.session_topic_seed, "topic-1")
        self.assertEqual(context.now_monotonic, 123.5)
        self.assertEqual(context.active_conversation_awaiting_reply_sec, 20.0)
        self.assertEqual(context.active_conversation_voice_sec, 15.0)
        self.assertEqual(context.canned_wake_reply, "네, 정훈님.")

    async def test_dispatch_defaults_ingress_and_queue_metadata(self) -> None:
        context = await self.dispatch(metrics={})

        self.assertEqual(context.ingress_source, "discord_voice")
        self.assertEqual(context.queue_wait_ms, 0.0)

    async def test_dispatch_normalizes_runtime_metadata(self) -> None:
        context = await self.dispatch(
            metrics={"meta": {"ingress_source": "local_mic", "voice_queue_wait_ms": "12.5"}}
        )

        self.assertEqual(context.ingress_source, "local_mic")
        self.assertEqual(context.queue_wait_ms, 12.5)

    async def test_dispatch_preserves_memory_keys_and_member(self) -> None:
        context = await self.dispatch()

        self.assertEqual(context.room_key, "room-memory")
        self.assertEqual(context.person_key, "person-memory")
        self.assertEqual(context.session_memory_key, "session-memory")
        self.assertEqual(context.member.display_name, "정훈")

    async def test_channel_move_hides_replacement_client_during_delivery(self) -> None:
        source_client = SimpleNamespace(
            _listener_generation=2,
            channel=SimpleNamespace(id=22),
        )
        member = SimpleNamespace(
            id=7,
            display_name="정훈",
            guild=SimpleNamespace(id=11, voice_client=source_client),
        )
        replacement_client = object()
        observed_clients: list[Any] = []
        reply_deps = SimpleNamespace(
            marker="reply-deps",
            get_voice_client=lambda: replacement_client,
        )

        async def process_voice_reply(*, context: Any, deps: Any) -> None:
            source_client._listener_generation = 3
            source_client.channel = SimpleNamespace(id=23)
            member.guild.voice_client = replacement_client
            observed_clients.append(deps.get_voice_client())
            self.calls.append((context, deps))

        await self.dispatch(
            member=member,
            reply_deps=reply_deps,
            voice_listener_binding=(source_client, 2, 22),
            deps=replace(self.deps, process_voice_reply=process_voice_reply),
        )

        self.assertEqual(observed_clients, [None])

    def test_main_delegates_reply_context_dispatch_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = composition_source.index("    async def process_member_audio_impl(")
        function_source = composition_source[start:]
        builder_source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "voice_member_pipeline_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("dispatch_voice_reply=dispatch_voice_reply_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("VoiceTranscriptReplyContext(", function_source)
        self.assertNotIn("VoiceTranscriptReplyDeps(", function_source)
        self.assertNotIn("process_voice_reply_from_transcript_context(", function_source)


if __name__ == "__main__":
    unittest.main()
