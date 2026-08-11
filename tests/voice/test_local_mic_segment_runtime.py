from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_mic_segment_runtime import (  # noqa: E402
    LocalMicDiscordSuppressionRuntimeDeps,
    LocalMicSegmentRuntimeDeps,
    LocalMicServiceRuntimeDeps,
    ensure_local_mic_service_started_from_runtime,
    handle_local_mic_segment_from_runtime,
    local_mic_effective_max_silence_ms_from_runtime,
    should_drop_discord_audio_for_local_mic_from_runtime,
    stop_local_mic_service_from_runtime,
)
from evelyn_core.voice_ingress_runtime import (  # noqa: E402
    VoiceIngressEntrypointDeps,
    process_member_audio_from_runtime,
)


class LocalMicSegmentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_stop_local_mic_service_stops_service_and_clears_capture_ready(self) -> None:
        state: dict[str, Any] = {"capture_ready": True}
        calls: list[str] = []

        class FakeService:
            def stop(self) -> None:
                calls.append("stop")

        next_service = stop_local_mic_service_from_runtime(
            current_service=FakeService(),
            local_mic_runtime_state=state,
        )

        self.assertIsNone(next_service)
        self.assertFalse(state["capture_ready"])
        self.assertEqual(calls, ["stop"])

    def test_stop_local_mic_service_marks_not_ready_when_service_is_missing(self) -> None:
        state: dict[str, Any] = {"capture_ready": True}

        next_service = stop_local_mic_service_from_runtime(
            current_service=None,
            local_mic_runtime_state=state,
        )

        self.assertIsNone(next_service)
        self.assertFalse(state["capture_ready"])

    def test_effective_max_silence_shortens_while_local_tts_is_active(self) -> None:
        self.assertEqual(
            local_mic_effective_max_silence_ms_from_runtime(
                local_tts_playback_snapshot=lambda: {"active": True},
                tts_active_max_silence_ms=350,
                default_max_silence_ms=650,
            ),
            350,
        )
        self.assertEqual(
            local_mic_effective_max_silence_ms_from_runtime(
                local_tts_playback_snapshot=lambda: {"active": False},
                tts_active_max_silence_ms=350,
                default_max_silence_ms=650,
            ),
            650,
        )

    async def test_ensure_local_mic_service_starts_and_returns_ready_service(self) -> None:
        state: dict[str, Any] = {}
        calls: list[tuple[str, Any]] = []

        class FakeLoop:
            def call_soon_threadsafe(self, callback, coro):
                calls.append(("dispatch", callback))
                callback(coro)

        class FakeService:
            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                self.capture_ready = True
                self.last_error = None

            def start(self) -> bool:
                calls.append(("start", self.kwargs["sample_rate"]))
                self.kwargs["on_segment"](b"pcm", {"source": "test"})
                return True

        async def handle_segment(pcm_bytes: bytes, meta: dict[str, Any]) -> None:
            calls.append(("segment", (pcm_bytes, meta)))

        created_tasks: list[Any] = []
        deps = LocalMicServiceRuntimeDeps(
            local_mic_runtime_state=state,
            local_mic_enabled=True,
            local_only_mode=True,
            discord_user_ids=lambda: {42},
            service_factory=FakeService,
            get_running_loop=lambda: FakeLoop(),
            create_task=lambda coro: created_tasks.append(coro),
            handle_local_mic_segment=handle_segment,
            max_silence_ms_provider=lambda: 650,
            sample_rate=16000,
            block_ms=30,
            start_threshold=0.1,
            continue_threshold=0.05,
            start_consecutive=2,
            min_voiced_ms=280,
            max_silence_ms=650,
            preroll_ms=180,
            max_segment_sec=12.0,
            device=None,
            queue_max=32,
            vad_filter_enabled=True,
            env_noise_filter_enabled=True,
            waveform_filter_enabled=True,
            log=lambda message: calls.append(("log", message)),
        )

        service = await ensure_local_mic_service_started_from_runtime(current_service=None, deps=deps)

        self.assertIsInstance(service, FakeService)
        self.assertTrue(state["capture_ready"])
        self.assertIsNone(state["last_error"])
        self.assertEqual(calls[0], ("start", 16000))
        self.assertEqual(calls[1][0], "dispatch")
        self.assertEqual(calls[2][0], "log")
        self.assertEqual(len(created_tasks), 1)
        created_tasks[0].close()

    async def test_ensure_local_mic_service_keeps_current_service_when_disabled_or_missing_user_ids(self) -> None:
        current_service = object()
        state: dict[str, Any] = {}
        base = dict(
            local_mic_runtime_state=state,
            service_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            get_running_loop=lambda: None,
            create_task=lambda _coro: None,
            handle_local_mic_segment=lambda _pcm, _meta: (_ for _ in ()).throw(AssertionError("unexpected")),
            max_silence_ms_provider=lambda: 650,
            sample_rate=16000,
            block_ms=30,
            start_threshold=0.1,
            continue_threshold=0.05,
            start_consecutive=2,
            min_voiced_ms=280,
            max_silence_ms=650,
            preroll_ms=180,
            max_segment_sec=12.0,
            device=None,
            queue_max=32,
            vad_filter_enabled=True,
            env_noise_filter_enabled=True,
            waveform_filter_enabled=True,
        )

        disabled = await ensure_local_mic_service_started_from_runtime(
            current_service=current_service,
            deps=LocalMicServiceRuntimeDeps(
                **base,
                local_mic_enabled=False,
                local_only_mode=True,
                discord_user_ids=lambda: {42},
            ),
        )
        missing_ids = await ensure_local_mic_service_started_from_runtime(
            current_service=current_service,
            deps=LocalMicServiceRuntimeDeps(
                **base,
                local_mic_enabled=True,
                local_only_mode=False,
                discord_user_ids=lambda: set(),
            ),
        )

        self.assertIs(disabled, current_service)
        self.assertIs(missing_ids, current_service)
        self.assertFalse(state["capture_ready"])
        self.assertEqual(state["last_error"], "no_local_mic_user_ids")

    def test_discord_suppression_updates_state_for_recent_local_mic(self) -> None:
        state: dict[str, Any] = {"input_mode": "auto", "last_segment_at": 95.0}
        deps = LocalMicDiscordSuppressionRuntimeDeps(
            local_mic_runtime_state=state,
            local_mic_capture_ready=lambda: True,
            preferred_user_ids=lambda: {42},
            normalize_voice_input_mode=lambda value: str(value or "auto"),
            should_route_discord_user_to_local_mic=lambda user_id, *, preferred_user_ids, capture_ready: (
                bool(capture_ready) and user_id in preferred_user_ids
            ),
            suppress_after_segment_sec=10.0,
            time=lambda: 100.0,
        )

        self.assertTrue(should_drop_discord_audio_for_local_mic_from_runtime(42, source="discord_voice", deps=deps))
        self.assertEqual(state["input_mode"], "auto")
        self.assertTrue(state["capture_ready"])
        self.assertTrue(state["discord_suppression_active"])

    def test_discord_suppression_never_suppresses_local_mic_source(self) -> None:
        state: dict[str, Any] = {"input_mode": "local", "last_segment_at": 100.0}
        deps = LocalMicDiscordSuppressionRuntimeDeps(
            local_mic_runtime_state=state,
            local_mic_capture_ready=lambda: True,
            preferred_user_ids=lambda: {42},
            normalize_voice_input_mode=lambda value: str(value or "auto"),
            should_route_discord_user_to_local_mic=lambda user_id, *, preferred_user_ids, capture_ready: True,
            suppress_after_segment_sec=10.0,
            time=lambda: 100.0,
        )

        self.assertFalse(should_drop_discord_audio_for_local_mic_from_runtime(42, source="local_mic", deps=deps))
        self.assertFalse(state["discord_suppression_active"])

    async def test_routes_to_local_control_member_when_local_only_has_no_discord_target(self) -> None:
        state: dict[str, Any] = {"input_mode": "auto", "segment_count": 2}
        calls: list[tuple[str, Any]] = []
        member = SimpleNamespace(id=11)

        async def process_member_audio(target_member: Any, pcm_bytes: bytes, debug_meta: dict[str, Any]) -> None:
            calls.append(("process", (target_member, pcm_bytes, debug_meta)))

        deps = LocalMicSegmentRuntimeDeps(
            local_mic_runtime_state=state,
            normalize_voice_input_mode=lambda value: str(value),
            resolve_local_mic_target=lambda **_kwargs: None,
            guilds=lambda: [],
            preferred_user_ids=lambda: set(),
            local_only_mode=True,
            local_control_voice_member=lambda: member,
            process_member_audio=process_member_audio,
            log=lambda message: calls.append(("log", message)),
            time=lambda: 123.0,
        )

        await handle_local_mic_segment_from_runtime(
            b"pcm",
            {"duration_sec": 1.2, "voice_filter": {"ok": True}},
            deps=deps,
        )

        self.assertEqual(state["segment_count"], 3)
        self.assertEqual(state["last_segment_at"], 123.0)
        self.assertEqual(state["last_segment_duration_sec"], 1.2)
        self.assertEqual(state["last_filter"], {"ok": True})
        self.assertIsNone(state["last_error"])
        self.assertEqual(calls[0][0], "log")
        self.assertEqual(calls[1][0], "process")
        processed = calls[1][1]
        self.assertIs(processed[0], member)
        self.assertEqual(processed[1], b"pcm")
        self.assertTrue(processed[2]["routed_local_control"])
        self.assertEqual(processed[2]["routed_discord_user_id"], 11)
        self.assertEqual(processed[2]["source"], "local_mic")

    async def test_routes_to_discord_target_when_present(self) -> None:
        state: dict[str, Any] = {"input_mode": "auto"}
        scheduled: list[dict[str, Any]] = []
        voice_client = SimpleNamespace(
            channel=SimpleNamespace(id=9),
            _listener_generation=3,
        )
        listener_binding = (voice_client, 3, 9)
        voice_client.listener_binding = lambda: listener_binding
        member = SimpleNamespace(
            id=42,
            bot=False,
            guild=SimpleNamespace(id=7, voice_client=voice_client),
        )
        target = SimpleNamespace(member=member)

        async def schedule_voice_utterance_item(item: dict[str, Any]) -> None:
            scheduled.append(item)

        async def ensure_startup_components_ready() -> None:
            return None

        ingress_deps = VoiceIngressEntrypointDeps(
            ensure_startup_components_ready=ensure_startup_components_ready,
            normalize_voice_debug_meta=lambda meta: dict(meta or {}),
            voice_ingress_source=lambda meta: str(meta.get("source") or ""),
            should_drop_discord_audio_for_local_mic=lambda *_args, **_kwargs: False,
            ensure_voice_worker_started=lambda: None,
            build_voice_ingress_context=lambda **_kwargs: SimpleNamespace(
                room_session_key="room-session",
                session_key="session-1",
                room_key="room-key",
                person_key="person-key",
                session_memory_key="session-memory",
            ),
            next_segment_id=lambda _session_key: 1,
            new_turn_id=lambda: "turn-1",
            room_state_snapshot=lambda _room_session_key: {},
            validation_context_provider=lambda **_kwargs: None,
            build_voice_ingress_item=lambda **kwargs: dict(kwargs),
            voice_ingress_queue_depth=lambda: 0,
            schedule_voice_utterance_item=schedule_voice_utterance_item,
            monotonic=lambda: 123.0,
        )

        async def process_member_audio(
            target_member: Any,
            pcm_bytes: bytes,
            debug_meta: dict[str, Any],
        ) -> None:
            await process_member_audio_from_runtime(
                target_member,
                pcm_bytes,
                debug_meta,
                deps=ingress_deps,
            )

        deps = LocalMicSegmentRuntimeDeps(
            local_mic_runtime_state=state,
            normalize_voice_input_mode=lambda value: str(value),
            resolve_local_mic_target=lambda **_kwargs: target,
            guilds=lambda: ["guild"],
            preferred_user_ids=lambda: {42},
            local_only_mode=False,
            local_control_voice_member=lambda: (_ for _ in ()).throw(AssertionError("unexpected")),
            process_member_audio=process_member_audio,
        )

        await handle_local_mic_segment_from_runtime(b"pcm", {}, deps=deps)

        self.assertIsNone(state["last_error"])
        self.assertEqual(len(scheduled), 1)
        self.assertIs(scheduled[0]["member"], member)
        self.assertEqual(scheduled[0]["pcm_bytes"], b"pcm")
        self.assertEqual(scheduled[0]["debug_meta"]["source"], "local_mic")
        self.assertNotIn("_voice_listener_binding", scheduled[0]["debug_meta"])
        self.assertIs(scheduled[0]["voice_listener_binding"], listener_binding)

    async def test_discord_input_mode_skips_segment(self) -> None:
        state: dict[str, Any] = {"input_mode": "discord", "segment_count": 0}
        deps = LocalMicSegmentRuntimeDeps(
            local_mic_runtime_state=state,
            normalize_voice_input_mode=lambda value: str(value),
            resolve_local_mic_target=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            guilds=lambda: [],
            preferred_user_ids=lambda: set(),
            local_only_mode=True,
            local_control_voice_member=lambda: (_ for _ in ()).throw(AssertionError("unexpected")),
            process_member_audio=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
        )

        await handle_local_mic_segment_from_runtime(b"pcm", {}, deps=deps)

        self.assertEqual(state["segment_count"], 0)


if __name__ == "__main__":
    unittest.main()
