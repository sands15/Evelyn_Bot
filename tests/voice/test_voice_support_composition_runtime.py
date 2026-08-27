from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from collections import deque
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_support_composition_runtime import (
    VoiceSupportComposition,
    VoiceSupportCompositionDeps,
)
from evelyn_core.voice_ingress_runtime import (
    voice_listener_binding_is_current,
    voice_transition_is_pending,
)


class FakeVoiceChannel:
    def __init__(self, *, channel_id: int = 22, name: str = "voice") -> None:
        self.id = channel_id
        self.name = name


class FakeVoiceClient:
    def __init__(
        self,
        channel: FakeVoiceChannel,
        *,
        healthy: bool = False,
        connected: bool = True,
    ) -> None:
        self.channel = channel
        self.healthy = healthy
        self.connected = connected
        self.on_user_audio = None
        self.stop_calls = 0
        self.listen_calls = 0
        self.moves: list[FakeVoiceChannel] = []
        self.events: list[str] = []
        self.lease_token = "existing" if healthy else ""
        self.lease_release = None
        self._listener_generation = 0
        self.listener_failure_callback = None

    def set_listener_failure_callback(self, callback) -> None:
        self.listener_failure_callback = callback

    def bind_voice_input_lease(self, token, release) -> None:
        if self.lease_token:
            raise RuntimeError("voice_input_lease_already_bound")
        self.lease_token = token
        self.lease_release = release

    def has_voice_input_lease(self) -> bool:
        return bool(self.lease_token)

    def is_internal_voice_reconnect_active(self) -> bool:
        return False

    def is_connected(self) -> bool:
        return self.connected

    def is_listener_healthy(self) -> bool:
        return self.healthy

    def stop_listening(self) -> None:
        self._listener_generation += 1
        self.stop_calls += 1
        self.events.append("stop")
        self.healthy = False
        token, release = self.lease_token, self.lease_release
        self.lease_token = ""
        self.lease_release = None
        if token and release is not None:
            asyncio.create_task(release(token))

    def listen(self) -> None:
        if not self.lease_token:
            raise RuntimeError("voice_input_lease_required")
        self.listen_calls += 1
        self.events.append("listen")
        self.healthy = True

    async def move_to(self, channel: FakeVoiceChannel) -> None:
        self.events.append("move")
        self.moves.append(channel)
        self.channel = channel

    async def disconnect(self, *, force: bool = False) -> None:
        self.events.append("disconnect")
        self.connected = False


class FakeGuild:
    __slots__ = ("id", "voice_client", "channel")

    def __init__(self, voice_client=None, *, guild_id: int = 11, channel=None) -> None:
        self.id = guild_id
        self.voice_client = voice_client
        self.channel = channel

    def get_channel(self, channel_id: int):
        if self.channel is not None and self.channel.id == channel_id:
            return self.channel
        return None


class VoiceSupportCompositionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(self, **overrides) -> VoiceSupportCompositionDeps:
        callback = object()
        lease_sequence = 0

        async def acquire_voice_input_lease() -> str:
            nonlocal lease_sequence
            lease_sequence += 1
            return f"lease-{lease_sequence}"

        async def release_voice_input_lease(_token: str) -> None:
            return None

        values = dict(
            continuity=lambda: "continuity-deps",
            stt_warmup=lambda: "stt-warmup-deps",
            tts_warmup=lambda: "tts-warmup-deps",
            timing=lambda: "timing-deps",
            omnivoice_source=lambda: "omnivoice-deps",
            stt_transcription=lambda: "stt-transcription-deps",
            stt_text=lambda: "stt-text-deps",
            voice_connection=lambda: "voice-connection-deps",
            set_tts_warmup_started=Mock(),
            partial_stt_max_new_tokens=96,
            clean_text=lambda text: text.strip(),
            wake_audio_sec=1.0,
            wake_confirm_audio_sec=2.0,
            wake_max_tokens=16,
            wake_confirm_max_tokens=32,
            apply_stt_post_corrections=lambda text: text,
            strip_leading_voice_fillers=lambda text: text,
            extract_leading_wake_alias=lambda _text: None,
            fuzzy_leading_wake_alias=lambda _text: None,
            looks_like_gibberish_probe=lambda _text: False,
            slice_audio_window=lambda audio, *_args, **_kwargs: audio,
            ensure_startup_components_ready=AsyncMock(),
            voice_client_type=FakeVoiceClient,
            process_member_audio=lambda: callback,
            cancel_voice_turns_for_guild=Mock(return_value=0),
            stop_active_tts_playback=AsyncMock(return_value=False),
            is_tts_playback_active=Mock(return_value=False),
            warmup_voice_path=AsyncMock(),
            save_last_voice_channel_state=Mock(),
            load_last_voice_channel_state=Mock(return_value={}),
            increment_voice_pipeline_counter=Mock(),
            voice_pipeline_state={},
            voice_rejoin_on_ready=True,
            get_guild=Mock(return_value=None),
            voice_channel_type=FakeVoiceChannel,
            now=Mock(return_value=123.0),
            log=Mock(),
            acquire_voice_input_lease=acquire_voice_input_lease,
            release_voice_input_lease=release_voice_input_lease,
        )
        values.update(overrides)
        return VoiceSupportCompositionDeps(**values)

    async def test_tts_warmup_sets_started_before_runtime_call(self) -> None:
        events: list[object] = []
        deps = self.build_deps(set_tts_warmup_started=lambda value: events.append(("started", value)))

        async def runtime(*, deps):
            events.append(("runtime", deps))

        with patch(
            "evelyn_core.voice_support_composition_runtime.warmup_tts_server_from_runtime",
            runtime,
        ):
            await VoiceSupportComposition(deps).warmup_tts_server()

        self.assertEqual(events, [("started", True), ("runtime", "tts-warmup-deps")])

    def test_transcription_uses_typed_dependency_factory(self) -> None:
        runtime = Mock(return_value="result")
        composition = VoiceSupportComposition(self.build_deps())

        with patch(
            "evelyn_core.voice_support_composition_runtime.transcribe_audio16k_from_runtime",
            runtime,
        ):
            result = composition.transcribe_audio16k_sync(
                "audio",
                77,
                sampling_rate=8000,
                stage="wake",
            )

        self.assertEqual(result, "result")
        runtime.assert_called_once_with(
            "audio",
            77,
            deps="stt-transcription-deps",
            sampling_rate=8000,
            stage="wake",
        )

    def test_validation_bound_transcription_propagates_privacy_flag(self) -> None:
        runtime = Mock(return_value="raw validation transcript")
        composition = VoiceSupportComposition(self.build_deps())

        with patch(
            "evelyn_core.voice_support_composition_runtime.transcribe_audio16k_from_runtime",
            runtime,
        ):
            result = composition.transcribe_audio16k_sync(
                "audio",
                77,
                sampling_rate=16000,
                stage="full",
                validation_bound=True,
            )

        self.assertEqual(result, "raw validation transcript")
        runtime.assert_called_once_with(
            "audio",
            77,
            deps="stt-transcription-deps",
            sampling_rate=16000,
            stage="full",
            validation_bound=True,
        )

    async def test_existing_voice_client_is_rearmed_warmed_and_persisted(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        callback = object()
        ensure_ready = AsyncMock()
        warmup = AsyncMock()
        save = Mock()
        deps = self.build_deps(
            ensure_startup_components_ready=ensure_ready,
            process_member_audio=lambda: callback,
            warmup_voice_path=warmup,
            save_last_voice_channel_state=save,
        )

        result = await VoiceSupportComposition(deps).ensure_listening_voice_client(guild, channel)

        self.assertIs(result, voice_client)
        ensure_ready.assert_awaited_once_with()
        self.assertIs(voice_client.on_user_audio, callback)
        self.assertEqual((voice_client.stop_calls, voice_client.listen_calls), (1, 1))
        warmup.assert_awaited_once_with(reason="voice_connect", key="voice:11:22")
        save.assert_called_once_with(
            guild,
            channel,
            reason="ensure_listening",
            manual_disconnect=False,
        )

    async def test_voice_client_moves_to_requested_channel(self) -> None:
        old_channel = FakeVoiceChannel(channel_id=1, name="old")
        target_channel = FakeVoiceChannel(channel_id=2, name="new")
        voice_client = FakeVoiceClient(old_channel, healthy=True)
        guild = FakeGuild(voice_client)

        def cancel_voice_turns(guild_id: int) -> int:
            self.assertEqual(guild_id, 11)
            voice_client.events.append("cancel_turns")
            return 1

        async def stop_playback(guild_id: int, *, reason: str) -> bool:
            self.assertEqual((guild_id, reason), (11, "voice_channel_move"))
            voice_client.events.append("cancel_playback")
            return True

        result = await VoiceSupportComposition(
            self.build_deps(
                cancel_voice_turns_for_guild=cancel_voice_turns,
                stop_active_tts_playback=stop_playback,
            )
        ).ensure_listening_voice_client(
            guild,
            target_channel,
        )

        self.assertIs(result, voice_client)
        self.assertEqual(voice_client.moves, [target_channel])
        self.assertFalse(hasattr(voice_client, "_evelyn_voice_move_pending"))
        self.assertEqual(
            voice_client.events,
            ["stop", "cancel_turns", "cancel_playback", "move", "listen"],
        )

    async def test_voice_client_move_stops_when_playback_remains_active(self) -> None:
        old_channel = FakeVoiceChannel(channel_id=1, name="old")
        target_channel = FakeVoiceChannel(channel_id=2, name="new")
        voice_client = FakeVoiceClient(old_channel, healthy=True)
        guild = FakeGuild(voice_client)

        async def stop_playback(_guild_id: int, *, reason: str) -> bool:
            self.assertEqual(reason, "voice_channel_move")
            voice_client.events.append("cancel_playback")
            return False

        with self.assertRaisesRegex(
            RuntimeError,
            "^voice_channel_move_playback_stop_failed$",
        ):
            await VoiceSupportComposition(
                self.build_deps(
                    cancel_voice_turns_for_guild=lambda _guild_id: voice_client.events.append(
                        "cancel_turns"
                    ),
                    stop_active_tts_playback=stop_playback,
                    is_tts_playback_active=lambda _guild_id: True,
                )
            ).ensure_listening_voice_client(guild, target_channel)

        self.assertEqual(
            voice_client.events,
            ["stop", "cancel_turns", "cancel_playback", "disconnect"],
        )
        self.assertEqual(voice_client.moves, [])

    async def test_external_channel_move_forces_cleanup_without_second_move(self) -> None:
        target_channel = FakeVoiceChannel(channel_id=2, name="new")
        voice_client = FakeVoiceClient(target_channel, healthy=True)
        guild = FakeGuild(voice_client)

        def cancel_voice_turns(_guild_id: int) -> int:
            voice_client.events.append("cancel_turns")
            return 1

        async def stop_playback(_guild_id: int, *, reason: str) -> bool:
            self.assertEqual(reason, "voice_channel_move")
            voice_client.events.append("cancel_playback")
            return True

        result = await VoiceSupportComposition(
            self.build_deps(
                cancel_voice_turns_for_guild=cancel_voice_turns,
                stop_active_tts_playback=stop_playback,
            )
        ).ensure_listening_voice_client(
            guild,
            target_channel,
            force_listener_reset=True,
        )

        self.assertIs(result, voice_client)
        self.assertEqual(voice_client.moves, [])
        self.assertEqual(
            voice_client.events,
            ["stop", "cancel_turns", "cancel_playback", "listen"],
        )

        missing_client_composition = VoiceSupportComposition(self.build_deps())
        missing_client_composition.connect_evelyn_voice_client = AsyncMock()
        self.assertIsNone(
            await missing_client_composition.ensure_listening_voice_client(
                FakeGuild(None),
                target_channel,
                force_listener_reset=True,
            )
        )
        missing_client_composition.connect_evelyn_voice_client.assert_not_awaited()

        observed_client = FakeVoiceClient(target_channel, healthy=True)
        replacement_client = FakeVoiceClient(target_channel, healthy=True)
        replacement_guild = FakeGuild(replacement_client)
        replacement_deps = self.build_deps()
        self.assertIsNone(
            await VoiceSupportComposition(replacement_deps).ensure_listening_voice_client(
                replacement_guild,
                target_channel,
                force_listener_reset=True,
                expected_voice_client=observed_client,
            )
        )
        self.assertEqual((replacement_client.stop_calls, replacement_client.listen_calls), (0, 0))
        replacement_deps.cancel_voice_turns_for_guild.assert_not_called()
        replacement_deps.stop_active_tts_playback.assert_not_awaited()
        replacement_deps.save_last_voice_channel_state.assert_not_called()

    async def test_external_channel_move_does_not_restore_stale_target_after_cleanup(self) -> None:
        target_channel = FakeVoiceChannel(channel_id=2, name="observed")
        newer_channel = FakeVoiceChannel(channel_id=3, name="newer")
        voice_client = FakeVoiceClient(target_channel, healthy=True)
        guild = FakeGuild(voice_client)
        save = Mock()

        async def stop_playback(_guild_id: int, *, reason: str) -> bool:
            self.assertEqual(reason, "voice_channel_move")
            voice_client.events.append("cancel_playback")
            voice_client.channel = newer_channel
            return True

        result = await VoiceSupportComposition(
            self.build_deps(
                cancel_voice_turns_for_guild=lambda _guild_id: voice_client.events.append(
                    "cancel_turns"
                ),
                stop_active_tts_playback=stop_playback,
                save_last_voice_channel_state=save,
            )
        ).ensure_listening_voice_client(
            guild,
            target_channel,
            force_listener_reset=True,
        )

        self.assertIsNone(result)
        self.assertEqual(
            voice_client.events,
            ["stop", "cancel_turns", "cancel_playback"],
        )
        self.assertEqual(voice_client.moves, [])
        self.assertEqual(voice_client.listen_calls, 0)
        save.assert_not_called()

    async def test_new_connection_defers_listener_arm_to_composition(self) -> None:
        target_channel = FakeVoiceChannel(channel_id=2, name="target")
        voice_client = FakeVoiceClient(target_channel, healthy=False)
        voice_client._listener_generation = 7
        guild = FakeGuild(None)
        member = SimpleNamespace(guild=guild)
        save = Mock()
        arm_listener_values = []

        async def connect_runtime(
            _target_channel: FakeVoiceChannel,
            *,
            deps,
            arm_listener: bool = True,
        ) -> FakeVoiceClient:
            del deps
            arm_listener_values.append(arm_listener)
            guild.voice_client = voice_client
            self.assertTrue(voice_transition_is_pending(guild.id))
            self.assertFalse(
                voice_listener_binding_is_current(
                    member,
                    (voice_client, 7, target_channel.id),
                )
            )
            if arm_listener:
                voice_client.listen()
            return voice_client

        composition = VoiceSupportComposition(
            self.build_deps(save_last_voice_channel_state=save)
        )
        with patch(
            "evelyn_core.voice_support_composition_runtime.connect_evelyn_voice_client_from_runtime",
            side_effect=connect_runtime,
        ):
            result = await composition.ensure_listening_voice_client(guild, target_channel)

        self.assertIs(result, voice_client)
        self.assertEqual(arm_listener_values, [False])
        self.assertEqual(voice_client.listen_calls, 1)
        self.assertFalse(hasattr(voice_client, "_evelyn_voice_move_pending"))
        self.assertFalse(voice_transition_is_pending(guild.id))
        save.assert_called_once()

    async def test_channel_event_cleanup_invalidates_prior_warmup_result(self) -> None:
        target_channel = FakeVoiceChannel(channel_id=2, name="target")
        voice_client = FakeVoiceClient(target_channel, healthy=True)
        guild = FakeGuild(voice_client)
        save = Mock()
        warmup_started = asyncio.Event()
        release_warmup = asyncio.Event()
        stop_playback_started = asyncio.Event()
        release_stop_playback = asyncio.Event()
        active_playback = True

        async def warmup(*, reason: str, key: str) -> None:
            self.assertEqual(reason, "voice_connect")
            if key == "voice:11:2":
                warmup_started.set()
                await release_warmup.wait()

        async def stop_playback(_guild_id: int, *, reason: str) -> bool:
            nonlocal active_playback
            self.assertEqual(reason, "voice_channel_move")
            stop_playback_started.set()
            await release_stop_playback.wait()
            active_playback = False
            return True

        deps = self.build_deps(
            warmup_voice_path=warmup,
            stop_active_tts_playback=stop_playback,
            is_tts_playback_active=lambda _guild_id: active_playback,
            save_last_voice_channel_state=save,
        )
        composition = VoiceSupportComposition(deps)
        prior = asyncio.create_task(
            composition.ensure_listening_voice_client(guild, target_channel)
        )
        await asyncio.wait_for(warmup_started.wait(), timeout=1.0)
        event = asyncio.create_task(
            composition.ensure_listening_voice_client(
                guild,
                target_channel,
                force_listener_reset=True,
                expected_voice_client=voice_client,
            )
        )
        try:
            await asyncio.wait_for(stop_playback_started.wait(), timeout=1.0)
            self.assertEqual(deps.cancel_voice_turns_for_guild.call_count, 1)
            release_warmup.set()
            prior_result = await asyncio.wait_for(prior, timeout=1.0)
            self.assertIsNone(prior_result)
            self.assertFalse(voice_client.is_listener_healthy())
            self.assertTrue(hasattr(voice_client, "_evelyn_voice_move_pending"))
            self.assertFalse(event.done())
            release_stop_playback.set()
            event_result = await asyncio.wait_for(event, timeout=1.0)
        finally:
            release_warmup.set()
            release_stop_playback.set()
            await asyncio.gather(prior, event, return_exceptions=True)

        self.assertIs(event_result, voice_client)
        self.assertFalse(active_playback)
        self.assertIs(voice_client.channel, target_channel)
        self.assertEqual(voice_client.moves, [])
        self.assertEqual(save.call_count, 2)

    async def test_voice_client_move_verifies_target_channel(self) -> None:
        old_channel = FakeVoiceChannel(channel_id=1, name="old")
        target_channel = FakeVoiceChannel(channel_id=2, name="new")
        voice_client = FakeVoiceClient(old_channel, healthy=True)
        guild = FakeGuild(voice_client)
        save = Mock()

        async def stalled_move(channel: FakeVoiceChannel) -> None:
            voice_client.events.append("move")
            voice_client.moves.append(channel)

        voice_client.move_to = stalled_move
        with self.assertRaisesRegex(RuntimeError, "^voice_channel_move_failed$"):
            await VoiceSupportComposition(
                self.build_deps(save_last_voice_channel_state=save)
            ).ensure_listening_voice_client(guild, target_channel)

        self.assertEqual(
            voice_client.events,
            ["stop", "move", "disconnect"],
        )
        self.assertEqual(voice_client.listen_calls, 0)
        self.assertFalse(hasattr(voice_client, "_evelyn_voice_move_pending"))
        save.assert_not_called()

    async def test_concurrent_channel_moves_are_serialized(self) -> None:
        old_channel = FakeVoiceChannel(channel_id=1, name="old")
        first_target = FakeVoiceChannel(channel_id=2, name="first")
        second_target = FakeVoiceChannel(channel_id=3, name="second")
        voice_client = FakeVoiceClient(old_channel, healthy=True)
        voice_client._listener_generation = 1
        guild = FakeGuild(voice_client)
        member = SimpleNamespace(guild=guild)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        original_move = voice_client.move_to
        move_count = 0

        async def ordered_move(channel: FakeVoiceChannel) -> None:
            nonlocal move_count
            move_count += 1
            if move_count == 1:
                voice_client.events.append("move")
                voice_client.moves.append(channel)
                first_started.set()
                await release_first.wait()
                voice_client.channel = channel
                return
            await original_move(channel)

        voice_client.move_to = ordered_move
        deps = self.build_deps()
        composition = VoiceSupportComposition(deps)
        first = asyncio.create_task(
            composition.ensure_listening_voice_client(guild, first_target)
        )
        await asyncio.wait_for(first_started.wait(), timeout=1.0)
        second = asyncio.create_task(
            composition.ensure_listening_voice_client(guild, second_target)
        )
        await asyncio.sleep(0)
        stale_event = asyncio.create_task(
            composition.ensure_listening_voice_client(
                guild,
                first_target,
                expected_voice_client=voice_client,
            )
        )
        await asyncio.sleep(0)

        self.assertEqual(voice_client.moves, [first_target])
        self.assertFalse(second.done())
        self.assertFalse(
            voice_listener_binding_is_current(
                member,
                (voice_client, 1, old_channel.id),
            )
        )

        release_first.set()
        first_result, second_result, stale_result = await asyncio.gather(
            first,
            second,
            stale_event,
        )
        self.assertIs(first_result, voice_client)
        self.assertIs(second_result, voice_client)
        self.assertIsNone(stale_result)
        self.assertEqual(voice_client.moves, [first_target, second_target])
        self.assertIs(voice_client.channel, second_target)
        self.assertEqual((voice_client.stop_calls, voice_client.listen_calls), (2, 2))
        self.assertEqual(deps.cancel_voice_turns_for_guild.call_count, 2)
        self.assertEqual(deps.stop_active_tts_playback.await_count, 2)
        self.assertEqual(deps.save_last_voice_channel_state.call_count, 2)

    async def test_stop_listening_cancels_delayed_map_retry(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = lambda *_args, **_kwargs: b""
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")
            state_module = importlib.import_module("evelyn_voice.state")

        voice_client = object.__new__(client_module.EvelynVoiceClient)
        with self.assertRaisesRegex(
            RuntimeError,
            "^voice_input_lease_required$",
        ):
            voice_client.listen()
        voice_client.runtime = state_module.VoiceRuntimeState()
        voice_client.channel = SimpleNamespace(members=[])
        voice_client.connected_at = None
        voice_client.dave_inner_fail_log_count = 0

        now = asyncio.get_running_loop().time()
        packet = {"sequence": 7, "payload": b"x", "received_at": now}
        voice_client.pending_ssrc_packets = {42: deque([packet])}
        voice_client.pending_inner_packets = {}
        voice_client.pending_inner_log_times = {}
        voice_client.unknown_ssrc_log_times = {}
        voice_client.utterance_states = {}
        voice_client.opus_decoders = {}
        voice_client.opus_decoder_stats = {}
        voice_client.reorder_states = {}
        voice_client.media_queue = asyncio.Queue(maxsize=2000)
        voice_client.utterance_queue = asyncio.Queue(maxsize=32)
        voice_client._receive_task = None
        voice_client._decrypt_task = None
        voice_client._utterance_task = None
        voice_client._utterance_processing_tasks = set()
        voice_client._listener_generation = 0
        voice_client.sink = None

        self.assertEqual(voice_client.listener_binding(), (voice_client, 0, None))
        client_source = (REPO_ROOT / "evelyn_voice" / "client.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'voice_debug_meta["_voice_listener_binding"] = self.listener_binding()',
            client_source,
        )

        item = {"idx": 1, "ssrc": 42, "packets": [packet], "queued_at": now}
        created_tasks: list[asyncio.Task] = []
        real_create_task = asyncio.create_task

        def capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        with (
            patch.object(client_module, "VOICE_UNKNOWN_SSRC_RETRY_MS", 1.0),
            patch.object(client_module, "VOICE_MAP_RETRY_MS", 1.0),
            patch.object(client_module.asyncio, "create_task", capture_task),
        ):
            await voice_client._process_utterance_packets(item)
            retry_task = created_tasks[-1]
            voice_client.media_queue.put_nowait({"generation": "old"})
            voice_client.utterance_queue.put_nowait({"generation": "old"})
            voice_client.stop_listening()
            await asyncio.gather(retry_task, return_exceptions=True)

        self.assertTrue(voice_client.media_queue.empty())
        self.assertTrue(voice_client.utterance_queue.empty())
        self.assertEqual(voice_client._listener_generation, 1)

        allow_capture_cleanup = asyncio.Event()
        capture_stopped = asyncio.Event()
        lease_released = asyncio.Event()

        async def capture_loop() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await allow_capture_cleanup.wait()
                capture_stopped.set()

        async def release_lease(_token: str) -> None:
            self.assertTrue(capture_stopped.is_set())
            lease_released.set()

        capture_task = asyncio.create_task(capture_loop())
        await asyncio.sleep(0)
        voice_client._receive_task = capture_task
        voice_client._voice_input_lease_token = "listener-token"
        voice_client._voice_input_lease_release = release_lease
        voice_client._voice_input_lease_release_tasks = set()

        voice_client.stop_listening()
        await asyncio.sleep(0)
        self.assertFalse(lease_released.is_set())
        allow_capture_cleanup.set()
        await asyncio.wait_for(lease_released.wait(), timeout=1.0)

    def test_discord_rtp_padding_is_stripped_after_outer_decrypt(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = Mock()
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")

        voice_client = object.__new__(client_module.EvelynVoiceClient)
        voice_client.runtime = SimpleNamespace(
            voice_mode="aead_xchacha20_poly1305_rtpsize",
            voice_secret_key=b"k" * 32,
            dave_protocol_version=1,
            bind_dave_ssrc=Mock(),
        )
        voice_client.dave = SimpleNamespace(protocol_version=1)
        voice_client._connection = SimpleNamespace(
            dave_protocol_version=0,
            dave_session=None,
        )
        voice_client._sync_dave_from_base()
        self.assertEqual(voice_client.runtime.dave_protocol_version, 0)
        self.assertEqual(voice_client.dave.protocol_version, 0)
        packet = (
            bytes.fromhex("b07800010000000100000001bede0001")
            + b"ciphertext"
            + b"nonce"
        )

        decrypt = Mock(return_value=b"ext!opus\x00\x00\x03")
        with patch.object(
            client_module,
            "crypto_aead_xchacha20poly1305_ietf_decrypt",
            decrypt,
        ):
            decrypted = voice_client._decrypt_standard_voice_packet(packet)

        self.assertIsNotNone(decrypted)
        payload, info = decrypted
        self.assertTrue(info["padding"])
        self.assertEqual(payload, b"opus")
        self.assertEqual(decrypt.call_args.args[1], packet[:16])
        self.assertIsNone(client_module._parse_rtp_header(b"\x70" + packet[1:]))

        for invalid_plaintext in (b"ext!opus\x00", b"ext!\x01", b"ext!\x09"):
            with (
                self.subTest(plaintext=invalid_plaintext),
                patch.object(
                    client_module,
                    "crypto_aead_xchacha20poly1305_ietf_decrypt",
                    return_value=invalid_plaintext,
                ),
            ):
                self.assertIsNone(
                    voice_client._decrypt_standard_voice_packet(packet)
                )

        voice_client._try_dave_inner_decrypt = Mock(
            return_value=(None, 9, "cryptor_pending")
        )
        voice_client._queue_pending_inner_packet = Mock()
        with patch.object(
            client_module,
            "parse_dave_payload",
            return_value=SimpleNamespace(ranges_count=0),
        ):
            pending = voice_client._resolve_dave_audio_payload(
                user_id=9,
                ssrc=7,
                outer_plain=b"dave-ciphertext",
                packet_meta={"sequence": 1},
                dave_required=True,
            )

        self.assertEqual(pending, (None, 9, "deferred_cryptor_pending"))
        voice_client._queue_pending_inner_packet.assert_called_once()

        voice_client._try_dave_inner_decrypt = Mock(
            return_value=(b"opus", 9, "ok")
        )
        with patch.object(
            client_module,
            "parse_dave_payload",
            return_value=SimpleNamespace(ranges_count=0),
        ):
            transitioned = voice_client._resolve_dave_audio_payload(
                user_id=9,
                ssrc=7,
                outer_plain=b"prior-epoch-dave-frame",
                packet_meta={"sequence": 2},
                dave_required=False,
            )

        self.assertEqual(transitioned, (b"opus", 9, "inner_ok"))
        voice_client._try_dave_inner_decrypt.assert_called_once()

    async def test_receive_uses_unpadded_outer_payload_for_endpointing(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = Mock()
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")

        raw_packet = bytes.fromhex("a07800010000000100000001") + (b"x" * 80)
        info = client_module._parse_rtp_header(raw_packet)
        self.assertIsNotNone(info)

        class OnePacketTransport:
            def __init__(self) -> None:
                self.sent = False

            async def recv_packet(self) -> bytes:
                if not self.sent:
                    self.sent = True
                    return raw_packet
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        voice_client = object.__new__(client_module.EvelynVoiceClient)
        voice_client.runtime = SimpleNamespace(receive_ready=asyncio.Event())
        voice_client.udp_transport = OnePacketTransport()
        voice_client.media_queue = asyncio.Queue(maxsize=4)
        voice_client.pending_ssrc_packets = {}
        voice_client.media_packet_count = 0
        voice_client._prune_pending_ssrc_packets = Mock()
        voice_client._decrypt_standard_voice_packet = Mock(
            return_value=(b"\xf8\xff\xfe", info)
        )

        receive_task = asyncio.create_task(voice_client._receive_loop())
        queued = await asyncio.wait_for(voice_client.media_queue.get(), timeout=1.0)
        receive_task.cancel()
        await receive_task

        self.assertEqual(queued["payload"], b"\xf8\xff\xfe")
        self.assertEqual(queued["outer_plain"], b"\xf8\xff\xfe")
        self.assertGreater(len(queued["raw_packet"]), 60)
        voice_client._decrypt_standard_voice_packet.assert_called_once_with(raw_packet)

        voice_client.utterance_states = {}
        voice_client.preroll_packet_limit = 5
        voice_client.decrypt_packet_count = 0
        speech = {**queued, "payload": b"s", "sequence": 1}
        silence = {**queued, "sequence": 2}
        voice_client._route_packet_to_utterance_state(speech, now=1.0)
        voice_client._route_packet_to_utterance_state(silence, now=1.1)

        state = voice_client.utterance_states[queued["ssrc"]]
        self.assertTrue(state["in_utterance"])
        self.assertEqual(state["last_voice_like_at"], 1.0)

        voice_client.end_silence_sec = 0.01
        voice_client.utterance_count = 0
        voice_client.utterance_queue = asyncio.Queue(maxsize=1)
        voice_client._flush_ready_reordered_packets = Mock()
        decrypt_task = asyncio.create_task(voice_client._decrypt_loop())
        ended = await asyncio.wait_for(
            voice_client.utterance_queue.get(),
            timeout=1.0,
        )
        decrypt_task.cancel()
        await decrypt_task

        self.assertEqual(
            [packet["sequence"] for packet in ended["packets"]],
            [1, 2],
        )

        now = asyncio.get_running_loop().time()
        voice_client.pending_inner_packets = {
            7: [
                {
                    "packet": {"sequence": 3},
                    "payload": b"still-encrypted",
                    "user_id": 9,
                    "queued_at": now,
                    "attempts": 0,
                    "ranges_count": 0,
                }
            ]
        }
        voice_client._try_dave_inner_decrypt = Mock(
            return_value=(b"still-encrypted", 9, "passthrough_not_ready")
        )
        voice_client._log_pending_inner_event = Mock()

        recovered = voice_client._drain_pending_inner_packets(ssrc=7, user_id=9)

        self.assertEqual(recovered, [])
        self.assertEqual(len(voice_client.pending_inner_packets[7]), 1)

        voice_client.runtime = SimpleNamespace(
            get_preferred_user_id=lambda _ssrc: 9,
            current_speaking_user_id=9,
            pending_user_ids=[],
            dave_ssrc_to_user_id={},
            voice_secret_key=b"k" * 32,
            voice_mode="aead_xchacha20_poly1305_rtpsize",
            dave_protocol_version=1,
            bind_dave_ssrc=Mock(),
        )
        voice_client.dave = SimpleNamespace(ready=True)
        voice_client._sync_dave_from_base = Mock()
        voice_client._ordered_unique_packets = Mock(
            side_effect=lambda packets: list(packets)
        )
        voice_client.opus_decoder_stats = {}
        voice_client.pending_inner_packets = {}
        voice_client.utterance_queue = asyncio.Queue(maxsize=1)
        voice_client._utterance_processing_tasks = set()
        outer_info = {"header_len": 12, "unencrypted_header_len": 12}
        speech_packet = {
            "raw_packet": b"raw-speech",
            "outer_plain": b"dave-speech",
            "outer_info": outer_info,
            "ssrc": 7,
            "sequence": 1,
            "timestamp": 960,
            "payload": b"dave-speech",
        }
        silence_packet = {
            **speech_packet,
            "raw_packet": b"raw-silence",
            "outer_plain": b"\xf8\xff\xfe",
            "sequence": 2,
            "timestamp": 1920,
            "payload": b"\xf8\xff\xfe",
        }

        def resolve_pending_speech(**kwargs):
            if kwargs["outer_plain"] != b"\xf8\xff\xfe":
                voice_client.pending_inner_packets[7] = [{"pending": True}]
                return None, 9, "deferred_cryptor_pending"
            return b"\xf8\xff\xfe", 9, "inner_silence"

        voice_client._resolve_dave_audio_payload = Mock(
            side_effect=resolve_pending_speech
        )
        voice_client._drain_pending_inner_packets = Mock(return_value=[])
        await voice_client._process_utterance_packets(
            {
                "idx": 1,
                "ssrc": 7,
                "packets": [speech_packet, silence_packet],
                "body_packets": [speech_packet, silence_packet],
                "queued_at": now,
            }
        )
        retry_item = await asyncio.wait_for(
            voice_client.utterance_queue.get(),
            timeout=1.0,
        )

        self.assertEqual(retry_item["dave_retry"], 1)
        self.assertEqual(
            [packet["opus_packet"] for packet in retry_item["normalized_packets"]],
            [b"\xf8\xff\xfe"],
        )

    async def test_cancelled_release_drain_waits_for_capture_stop_and_release(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")

        voice_client = object.__new__(client_module.EvelynVoiceClient)
        allow_capture_cleanup = asyncio.Event()
        capture_stopped = asyncio.Event()
        lease_released = asyncio.Event()

        async def capture_loop() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await allow_capture_cleanup.wait()
                capture_stopped.set()

        async def release_lease(token: str) -> None:
            self.assertEqual(token, "listener-token")
            self.assertTrue(capture_stopped.is_set())
            lease_released.set()

        capture_task = asyncio.create_task(capture_loop())
        await asyncio.sleep(0)
        voice_client._voice_input_lease_token = "listener-token"
        voice_client._voice_input_lease_release = release_lease
        voice_client._voice_input_lease_release_tasks = set()

        capture_task.cancel()
        voice_client._release_voice_input_lease_after_stop((capture_task,))
        drain_task = asyncio.create_task(
            voice_client._drain_voice_input_lease_releases()
        )
        await asyncio.sleep(0)
        drain_task.cancel()
        await asyncio.sleep(0)
        drain_task.cancel()
        await asyncio.sleep(0)

        self.assertFalse(lease_released.is_set())
        self.assertFalse(next(iter(voice_client._voice_input_lease_release_tasks)).cancelled())

        allow_capture_cleanup.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(drain_task, timeout=1.0)
        self.assertTrue(lease_released.is_set())
        self.assertEqual(voice_client._voice_input_lease_release_tasks, set())

    async def test_actual_listener_terminal_notifies_once_but_explicit_or_stale_stop_does_not(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")
            state_module = importlib.import_module("evelyn_voice.state")

        class GatedUdp:
            def __init__(self, *, fail: bool) -> None:
                self.fail = fail
                self.ready = asyncio.Event()

            async def recv_packet(self) -> bytes:
                await self.ready.wait()
                if self.fail:
                    raise OSError("private udp failure")
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        def make_client(udp: GatedUdp):
            client = object.__new__(client_module.EvelynVoiceClient)
            client.runtime = state_module.VoiceRuntimeState()
            client.channel = SimpleNamespace(id=22, name="voice", members=[])
            client.udp_transport = udp
            client.media_queue = asyncio.Queue(maxsize=8)
            client.utterance_queue = asyncio.Queue(maxsize=4)
            client._receive_task = None
            client._decrypt_task = None
            client._utterance_task = None
            client._utterance_processing_tasks = set()
            client._listener_generation = 0
            client.sink = None
            client.utterance_states = {}
            client.pending_ssrc_packets = {}
            client.pending_inner_packets = {}
            client.pending_inner_log_times = {}
            client.unknown_ssrc_log_times = {}
            client.opus_decoders = {}
            client.opus_decoder_stats = {}
            client.reorder_states = {}
            client._voice_input_lease_token = "listener-token"
            client._voice_input_lease_release = None
            client._voice_input_lease_release_tasks = set()
            return client

        failed_udp = GatedUdp(fail=True)
        failed = make_client(failed_udp)
        failed_released = asyncio.Event()
        failed_notifications: list[tuple[object, int]] = []

        async def release_failed(_token: str) -> None:
            failed_released.set()

        failed._voice_input_lease_release = release_failed
        failed.set_listener_failure_callback(
            lambda client, generation: failed_notifications.append(
                (client, generation)
            )
        )
        failed.listen()
        failed_udp.ready.set()

        await asyncio.wait_for(failed_released.wait(), timeout=1.0)
        self.assertEqual(failed_notifications, [(failed, 1)])
        self.assertFalse(failed.has_voice_input_lease())
        self.assertIsNone(failed._receive_task)
        self.assertIsNone(failed._decrypt_task)
        self.assertIsNone(failed._utterance_task)

        explicit_udp = GatedUdp(fail=False)
        explicit = make_client(explicit_udp)
        explicit_released = asyncio.Event()
        explicit_notifications: list[tuple[object, int]] = []

        async def release_explicit(_token: str) -> None:
            explicit_released.set()

        explicit._voice_input_lease_release = release_explicit
        explicit.set_listener_failure_callback(
            lambda client, generation: explicit_notifications.append(
                (client, generation)
            )
        )
        explicit.listen()
        await asyncio.sleep(0)
        explicit.stop_listening()

        await asyncio.wait_for(explicit_released.wait(), timeout=1.0)
        await asyncio.sleep(0)
        self.assertEqual(explicit_notifications, [])

        stale_udp = GatedUdp(fail=True)
        stale = make_client(stale_udp)
        stale_released = asyncio.Event()
        stale_notifications: list[tuple[object, int]] = []

        async def release_stale(_token: str) -> None:
            stale_released.set()

        stale._voice_input_lease_release = release_stale
        stale.set_listener_failure_callback(
            lambda client, generation: stale_notifications.append(
                (client, generation)
            )
        )
        stale.listen()
        stale_receive = stale._receive_task
        stale._listener_generation += 1
        stale_udp.ready.set()
        await asyncio.gather(stale_receive, return_exceptions=True)
        await asyncio.sleep(0)

        self.assertEqual(stale_notifications, [])
        self.assertTrue(stale.has_voice_input_lease())
        stale.stop_listening()
        await asyncio.wait_for(stale_released.wait(), timeout=1.0)

    async def test_actual_nonresume_same_socket_reconnect_resets_session_listener(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")
        VoiceConnectionState = client_module.VoiceConnectionState
        ConnectionFlowState = VoiceConnectionState.state.fget.__globals__[
            "ConnectionFlowState"
        ]

        class FakeWebSocket:
            def __init__(self) -> None:
                self.closed = False
                self.messages: list[object] = []
                self._connection = SimpleNamespace(dave_session=object())

            async def received_message(self, message) -> None:
                self.messages.append(message)

            async def received_binary_message(self, _message) -> None:
                return None

            async def close(self) -> None:
                self.closed = True

        runtime = client_module.VoiceRuntimeState()
        gateway = client_module.VoiceGateway(
            runtime,
            SimpleNamespace(ready=False, status="idle"),
        )
        old_ws = FakeWebSocket()
        new_ws = FakeWebSocket()
        gateway.bind_ws(old_ws)
        reconnect_flags: list[bool] = []
        old_socket = object()
        release = AsyncMock()
        listener_rearms: list[tuple[object, int]] = []
        voice_client = object.__new__(client_module.EvelynVoiceClient)
        voice_client.gateway = gateway
        voice_client.runtime = runtime
        voice_client.channel = SimpleNamespace(id=22)
        voice_client.client = SimpleNamespace(user=SimpleNamespace(id=1))
        voice_client.udp_transport = SimpleNamespace(sock=old_socket)
        voice_client._listener_generation = 4
        voice_client._receive_task = None
        voice_client._decrypt_task = None
        voice_client._utterance_task = None
        voice_client._utterance_processing_tasks = set()
        voice_client.media_queue = asyncio.Queue(maxsize=8)
        voice_client.utterance_queue = asyncio.Queue(maxsize=4)
        voice_client.sink = None
        voice_client.utterance_states = {}
        voice_client.pending_ssrc_packets = {}
        voice_client.pending_inner_packets = {}
        voice_client.pending_inner_log_times = {}
        voice_client.unknown_ssrc_log_times = {}
        voice_client.opus_decoders = {}
        voice_client.opus_decoder_stats = {}
        voice_client.reorder_states = {}
        voice_client._voice_input_lease_token = "old-listener"
        voice_client._voice_input_lease_release = release
        voice_client._voice_input_lease_release_tasks = set()
        voice_client._listener_failure_callback = (
            lambda client, generation: listener_rearms.append(
                (client, generation)
            )
        )
        voice_client._sync_dave_from_base = Mock()
        voice_client._set_internal_voice_reconnect_active = (
            reconnect_flags.append
        )
        connection = object.__new__(
            client_module.EvelynVoiceConnectionState
        )
        connection.voice_client = voice_client
        voice_client._connection = connection
        connection.timeout = 1.0
        connection._state = ConnectionFlowState.got_voice_server_update
        connection.ws = old_ws
        connection.socket = old_socket
        connection.endpoint = "voice.example"
        connection.session_id = "fresh-session"
        connection.token = "fresh-token"
        connection.mode = "aead_xchacha20_poly1305_rtpsize"
        connection.secret_key = [1] * 32
        runtime.udp_ready.set()
        runtime.bind_dave_ssrc(99, 555)
        voice_client.media_queue.put_nowait({"old": True})
        voice_client.utterance_queue.put_nowait({"old": True})
        voice_client.utterance_states[555] = {"old": True}
        voice_client.pending_ssrc_packets[555] = deque([{"old": True}])
        voice_client.pending_inner_packets[555] = [{"old": True}]
        voice_client.opus_decoders[555] = object()
        voice_client.reorder_states[555] = {"old": True}

        self.assertIsNone(
            voice_client.prepare_base_udp_transport_change(
                old_socket,
                resume=True,
            )
        )
        self.assertEqual(runtime.get_preferred_user_id(555), 99)
        self.assertTrue(voice_client.has_voice_input_lease())

        async def wait_for_state(*_states, timeout: float) -> None:
            self.assertEqual(timeout, 1.0)

        async def connect_websocket(_state, resume: bool):
            self.assertFalse(resume)
            return new_ws

        async def handshake_websocket(_state) -> None:
            connection.secret_key = [2] * 32
            await connection.ws.received_message(
                {
                    "op": 12,
                    "d": {"user_id": "42", "audio_ssrc": 555},
                }
            )

        connection._wait_for_state = wait_for_state
        with (
            patch.object(
                VoiceConnectionState,
                "_connect_websocket",
                connect_websocket,
            ),
            patch.object(
                VoiceConnectionState,
                "_handshake_websocket",
                handshake_websocket,
            ),
        ):
            result = await connection._potential_reconnect()

        self.assertTrue(result)
        self.assertIs(connection.ws, new_ws)
        self.assertIs(gateway.ws, new_ws)
        self.assertTrue(getattr(new_ws, "_evelyn_gateway_hooked", False))
        self.assertTrue(old_ws.closed)
        self.assertEqual(runtime.get_preferred_user_id(555), 42)
        self.assertEqual(runtime.dave_ssrc_to_user_id, {})
        self.assertEqual(runtime.voice_secret_key, bytes([2] * 32))
        self.assertEqual(runtime.session_id, "fresh-session")
        self.assertTrue(runtime.udp_ready.is_set())
        self.assertTrue(voice_client.media_queue.empty())
        self.assertTrue(voice_client.utterance_queue.empty())
        self.assertEqual(voice_client.utterance_states, {})
        self.assertEqual(voice_client.pending_ssrc_packets, {})
        self.assertEqual(voice_client.pending_inner_packets, {})
        self.assertEqual(voice_client.opus_decoders, {})
        self.assertEqual(voice_client.reorder_states, {})
        self.assertEqual(reconnect_flags, [True, False])
        self.assertEqual(listener_rearms, [(voice_client, 5)])
        self.assertFalse(voice_client.has_voice_input_lease())
        await voice_client._drain_voice_input_lease_releases()
        release.assert_awaited_once_with("old-listener")

        await old_ws.received_message(
            {"op": 12, "d": {"user_id": "99", "audio_ssrc": 555}}
        )

        self.assertEqual(runtime.get_preferred_user_id(555), 42)

    async def test_actual_orphan_cleanup_preserves_ready_replacement(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")
        from discord.state import ConnectionState

        class Registry:
            max_messages = None

            def _get_voice_client(self, guild_id):
                return self._voice_clients.get(guild_id)

            def _remove_voice_client(self, guild_id):
                self._voice_clients.pop(guild_id, None)

        registry = Registry()
        channel = SimpleNamespace(
            name="voice",
            _get_voice_client_key=lambda: (7, "guild_id"),
        )
        orphan = object.__new__(client_module.EvelynVoiceClient)
        orphan.channel = channel
        orphan.client = SimpleNamespace(_connection=registry)
        orphan._listener_generation = 0
        capture_tasks = tuple(
            asyncio.create_task(asyncio.Event().wait())
            for _ in range(4)
        )
        orphan._receive_task = capture_tasks[0]
        orphan._decrypt_task = capture_tasks[1]
        orphan._utterance_task = capture_tasks[2]
        orphan._utterance_processing_tasks = {capture_tasks[3]}
        orphan.media_queue = asyncio.Queue(maxsize=8)
        orphan.utterance_queue = asyncio.Queue(maxsize=4)
        sink = Mock()
        orphan.sink = sink
        orphan.utterance_states = {1: {}}
        orphan.pending_ssrc_packets = {1: deque()}
        orphan.pending_inner_packets = {1: []}
        orphan.pending_inner_log_times = {1: 1.0}
        orphan.unknown_ssrc_log_times = {1: 1.0}
        orphan.opus_decoders = {1: object()}
        orphan.opus_decoder_stats = {1: {}}
        orphan.reorder_states = {1: {}}
        release = AsyncMock()
        orphan._voice_input_lease_token = "orphan-listener"
        orphan._voice_input_lease_release = release
        orphan._voice_input_lease_release_tasks = set()
        registry._voice_clients = {7: orphan}

        ConnectionState.clear(registry, views=False)
        replacement = object()
        registry._voice_clients[7] = replacement
        orphan.cleanup()
        await orphan._drain_voice_input_lease_releases()

        self.assertIs(registry._voice_clients[7], replacement)
        self.assertTrue(all(task.cancelled() for task in capture_tasks))
        release.assert_awaited_once_with("orphan-listener")
        sink.cleanup.assert_called_once_with()
        self.assertFalse(orphan.has_voice_input_lease())

        current = object.__new__(client_module.EvelynVoiceClient)
        current.channel = channel
        current.client = SimpleNamespace(_connection=registry)
        registry._voice_clients[7] = current
        current.cleanup()
        self.assertNotIn(7, registry._voice_clients)

    async def test_actual_connect_cancellation_drains_base_and_preserves_replacement(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")

        class Registry:
            def __init__(self) -> None:
                self._voice_clients = {}

            def _get_voice_client(self, guild_id):
                return self._voice_clients.get(guild_id)

            def _remove_voice_client(self, guild_id):
                self._voice_clients.pop(guild_id, None)

        registry = Registry()
        channel = SimpleNamespace(
            id=9,
            name="voice",
            _get_voice_client_key=lambda: (7, "guild_id"),
        )
        voice_client = object.__new__(client_module.EvelynVoiceClient)
        voice_client.channel = channel
        voice_client.client = SimpleNamespace(
            user=SimpleNamespace(id=42),
            _connection=registry,
        )
        voice_client.runtime = SimpleNamespace(
            dave_protocol_version=None,
            dave_ready=False,
            dave_status="",
        )
        voice_client.dave = SimpleNamespace(
            protocol_version=1,
            ready=False,
            status="new",
            init_session=Mock(),
            reset=Mock(),
        )
        cleanup_started = asyncio.Event()

        async def blocking_gateway_close() -> None:
            cleanup_started.set()
            await asyncio.Event().wait()

        voice_client.gateway = SimpleNamespace(close=blocking_gateway_close)
        voice_client.udp_transport = None
        voice_client.stop_listening = Mock()
        voice_client._voice_input_lease_release_tasks = set()
        registry._voice_clients[7] = voice_client

        base_started = asyncio.Event()
        base_tasks: list[asyncio.Task] = []

        async def blocking_base_connect(_self, **_kwargs) -> None:
            base_tasks.append(asyncio.current_task())
            base_started.set()
            await asyncio.Event().wait()

        replacement = object()
        with patch.object(
            client_module.discord.VoiceClient,
            "connect",
            blocking_base_connect,
        ):
            outer = asyncio.create_task(
                voice_client.connect(timeout=0.05, reconnect=True)
            )
            await base_started.wait()
            outer.cancel("caller-cancel")
            try:
                await asyncio.wait_for(cleanup_started.wait(), timeout=0.3)
                cleanup_observed = True
                registry._voice_clients[7] = replacement
                outer.cancel("later-cancel")
            except asyncio.TimeoutError:
                cleanup_observed = False

            cancellation_args: tuple = ()
            try:
                await asyncio.wait_for(outer, timeout=1.0)
            except asyncio.CancelledError as exc:
                cancellation_args = exc.args

        base_done_before_manual_cleanup = bool(base_tasks and base_tasks[0].done())
        registered_after_cancel = registry._voice_clients.get(7)

        for task in base_tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if registry._voice_clients.get(7) is voice_client:
            voice_client.cleanup()

        self.assertTrue(cleanup_observed)
        self.assertTrue(base_done_before_manual_cleanup)
        self.assertIs(registered_after_cancel, replacement)
        self.assertEqual(cancellation_args, ("caller-cancel",))

    async def test_connect_cancel_after_base_success_disconnects_base_and_preserves_replacement(
        self,
    ) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")

        class Registry:
            def __init__(self) -> None:
                self._voice_clients = {}

            def _get_voice_client(self, guild_id):
                return self._voice_clients.get(guild_id)

            def _remove_voice_client(self, guild_id):
                self._voice_clients.pop(guild_id, None)

        registry = Registry()
        channel = SimpleNamespace(
            id=9,
            name="voice",
            _get_voice_client_key=lambda: (7, "guild_id"),
        )
        voice_client = object.__new__(client_module.EvelynVoiceClient)
        voice_client.channel = channel
        voice_client.client = SimpleNamespace(
            user=SimpleNamespace(id=42),
            _connection=registry,
        )
        voice_client.runtime = SimpleNamespace(
            dave_protocol_version=None,
            dave_ready=False,
            dave_status="",
        )
        voice_client.dave = SimpleNamespace(
            protocol_version=1,
            ready=False,
            status="new",
            init_session=Mock(),
            reset=Mock(),
        )
        voice_client.gateway = SimpleNamespace(close=AsyncMock())
        voice_client.udp_transport = None
        voice_client.stop_listening = Mock()
        voice_client._voice_input_lease_release_tasks = set()
        registry._voice_clients[7] = voice_client

        custom_setup_started = asyncio.Event()

        async def block_custom_setup() -> None:
            custom_setup_started.set()
            await asyncio.Event().wait()

        voice_client._finish_connect_after_base = block_custom_setup
        base_disconnect_calls: list[bool] = []

        async def completed_base_connect(_self, **_kwargs) -> None:
            return None

        async def exact_base_disconnect(_self, *, force: bool = False) -> None:
            base_disconnect_calls.append(force)
            _self.cleanup()

        replacement = object()
        with (
            patch.object(
                client_module.discord.VoiceClient,
                "connect",
                completed_base_connect,
            ),
            patch.object(
                client_module.discord.VoiceClient,
                "disconnect",
                exact_base_disconnect,
            ),
        ):
            outer = asyncio.create_task(
                voice_client.connect(timeout=0.1, reconnect=True)
            )
            await custom_setup_started.wait()
            registry._voice_clients[7] = replacement
            outer.cancel("cancel-during-custom-setup")
            cancellation_args: tuple = ()
            try:
                await asyncio.wait_for(outer, timeout=1.0)
            except asyncio.CancelledError as exc:
                cancellation_args = exc.args

        self.assertEqual(base_disconnect_calls, [True])
        self.assertIs(registry._voice_clients[7], replacement)
        self.assertEqual(cancellation_args, ("cancel-during-custom-setup",))

    async def test_packet_send_receipt_requires_base_success_and_current_source(
        self,
    ) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")

        voice_client = object.__new__(client_module.EvelynVoiceClient)
        current = SimpleNamespace(mark_packet_sent=Mock())
        voice_client._player = SimpleNamespace(source=current)
        voice_client.sequence = 0
        voice_client.timestamp = 0
        voice_client.encoder = SimpleNamespace(
            SAMPLES_PER_FRAME=960,
            encode=Mock(return_value=b"encoded"),
        )
        voice_client._get_voice_packet = Mock(return_value=b"packet")
        connection = object.__new__(
            client_module.EvelynVoiceConnectionState
        )
        connection.voice_client = voice_client
        connection.socket = SimpleNamespace(sendall=Mock())
        voice_client._connection = connection

        voice_client.send_audio_packet(b"pcm", encode=True)

        voice_client.encoder.encode.assert_called_once_with(b"pcm", 960)
        connection.socket.sendall.assert_called_once_with(b"packet")
        current.mark_packet_sent.assert_called_once_with(b"pcm")

        failed = SimpleNamespace(mark_packet_sent=Mock())
        voice_client._player = SimpleNamespace(source=failed)
        connection.socket.sendall.reset_mock(side_effect=True)
        connection.socket.sendall.side_effect = OSError(
            "udp send failed"
        )

        voice_client.send_audio_packet(b"pcm", encode=True)

        failed.mark_packet_sent.assert_not_called()
        connection.socket.sendall.side_effect = None
        voice_client.send_audio_packet(b"pcm", encode=True)
        failed.mark_packet_sent.assert_called_once_with(b"pcm")

        stale = SimpleNamespace(mark_packet_sent=Mock())
        replacement = SimpleNamespace(mark_packet_sent=Mock())
        voice_client._player = SimpleNamespace(source=stale)

        def replace_during_send(_packet: bytes) -> None:
            voice_client._player = SimpleNamespace(source=replacement)

        connection.socket.sendall.side_effect = replace_during_send
        voice_client.send_audio_packet(b"pcm", encode=True)

        stale.mark_packet_sent.assert_not_called()
        replacement.mark_packet_sent.assert_not_called()

        from evelyn_core.tts_playback import (
            TtsPlaybackManager,
            TtsSourcePlaybackRequest,
        )

        metrics: dict = {"meta": {}}
        manager = TtsPlaybackManager()
        voice_client.channel = SimpleNamespace(guild=SimpleNamespace(id=123))
        voice_client._player = None
        playing = False

        def play(source, *, after) -> None:
            nonlocal playing
            playing = True
            voice_client._player = SimpleNamespace(source=source)
            try:
                voice_client.send_audio_packet(source.read(), encode=True)
            except Exception as exc:
                playing = False
                after(exc)
                return
            playing = False
            after(None)

        def stop() -> None:
            nonlocal playing
            playing = False
            voice_client._player = None

        voice_client.play = play
        voice_client.stop = stop
        voice_client.is_playing = lambda: playing
        voice_client.is_paused = lambda: False
        connection.socket.sendall.side_effect = OSError(
            "udp send failed"
        )
        audible_source = SimpleNamespace(
            error=None,
            read=lambda: b"nonzero-pcm",
            is_opus=lambda: False,
            finish=lambda: None,
        )

        self.assertFalse(
            await manager.play_source_once(
                TtsSourcePlaybackRequest(
                    voice_client,
                    audible_source,
                    guild_id=123,
                    turn_id="turn-udp-failure",
                    metrics=metrics,
                )
            )
        )

        self.assertFalse(
            await manager.cancel_guild(
                123,
                reason="qualified_user_audio",
            )
        )
        self.assertIs(metrics["meta"]["playback_started"], False)
        self.assertIs(metrics["meta"]["playback_completed"], False)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    async def test_actual_client_refreshes_only_when_base_udp_socket_changed(self) -> None:
        davey = ModuleType("davey")
        davey.DAVE_PROTOCOL_VERSION = 1
        davey.DaveSession = object
        davey.MediaType = SimpleNamespace(audio="audio")
        nacl = ModuleType("nacl")
        bindings = ModuleType("nacl.bindings")
        bindings.crypto_aead_xchacha20poly1305_ietf_decrypt = (
            lambda *_args, **_kwargs: b""
        )
        nacl.bindings = bindings

        with patch.dict(
            sys.modules,
            {"davey": davey, "nacl": nacl, "nacl.bindings": bindings},
        ):
            client_module = importlib.import_module("evelyn_voice.client")
            state_module = importlib.import_module("evelyn_voice.state")

        class FakeTransport:
            def __init__(self, sock) -> None:
                self.sock = sock
                self._closed = False
                self.opened = False

            async def open(self) -> None:
                self.opened = True

            async def close(self) -> None:
                self._closed = True

        old_socket = object()
        current_socket = object()
        old_transport = FakeTransport(old_socket)
        voice_client = object.__new__(client_module.EvelynVoiceClient)
        voice_client.runtime = state_module.VoiceRuntimeState()
        voice_client.udp_transport = old_transport
        voice_client._find_base_udp_socket = lambda: current_socket

        with patch.object(client_module, "VoiceUDPTransport", FakeTransport):
            self.assertTrue(
                await voice_client.refresh_udp_transport_from_base()
            )
            self.assertFalse(
                await voice_client.refresh_udp_transport_from_base()
            )

        self.assertTrue(old_transport._closed)
        self.assertIs(voice_client.udp_transport.sock, current_socket)
        self.assertTrue(voice_client.udp_transport.opened)
        self.assertTrue(voice_client.runtime.udp_ready.is_set())

        stale_rearm = Mock()
        voice_client._listener_generation = 9
        voice_client._listener_failure_callback = stale_rearm
        self.assertTrue(
            voice_client.rearm_listener_after_base_udp_change(8)
        )
        stale_rearm.assert_not_called()

    async def test_restore_last_channel_updates_success_state(self) -> None:
        channel = FakeVoiceChannel()
        guild = FakeGuild(guild_id=11, channel=channel)
        counters = Mock()
        save = Mock()
        state = {"guild_id": 11, "channel_id": 22, "manual_disconnect": False}
        pipeline_state: dict[str, object] = {}
        deps = self.build_deps(
            load_last_voice_channel_state=Mock(return_value=state),
            get_guild=Mock(return_value=guild),
            increment_voice_pipeline_counter=counters,
            voice_pipeline_state=pipeline_state,
            save_last_voice_channel_state=save,
        )
        composition = VoiceSupportComposition(deps)
        composition.ensure_listening_voice_client = AsyncMock(return_value=FakeVoiceClient(channel, healthy=True))

        result = await composition.restore_last_voice_channel()

        self.assertEqual(result, (True, "voice"))
        self.assertEqual(
            [call.args[0] for call in counters.call_args_list],
            ["voice_rejoin_attempts", "voice_rejoin_success"],
        )
        self.assertEqual(pipeline_state["last_voice_rejoin_at"], 123.0)
        self.assertIsNone(pipeline_state["last_voice_rejoin_error"])
        save.assert_called_once_with(
            guild,
            channel,
            reason="restore_last_voice_channel",
            manual_disconnect=False,
        )

    async def test_failed_internal_reconnect_replaces_stale_client(self) -> None:
        channel = FakeVoiceChannel()
        stale_voice_client = FakeVoiceClient(channel, connected=False)
        stale_voice_client._listener_generation = 9
        stale_voice_client.is_internal_voice_reconnect_active = Mock(return_value=True)
        replacement = FakeVoiceClient(channel, connected=True)
        guild = FakeGuild(stale_voice_client)
        member = SimpleNamespace(guild=guild)
        warmup = AsyncMock()
        save = Mock()
        deps = self.build_deps(
            warmup_voice_path=warmup,
            save_last_voice_channel_state=save,
        )
        composition = VoiceSupportComposition(deps)

        async def wait_for_reconnect(_channel: FakeVoiceChannel) -> None:
            self.assertTrue(voice_transition_is_pending(guild.id))
            self.assertFalse(
                voice_listener_binding_is_current(
                    member,
                    (stale_voice_client, 9, channel.id),
                )
            )
            return None

        composition.wait_for_internal_voice_reconnect = AsyncMock(
            side_effect=wait_for_reconnect
        )

        async def connect(_channel: FakeVoiceChannel) -> FakeVoiceClient:
            guild.voice_client = replacement
            return replacement

        composition.connect_evelyn_voice_client = AsyncMock(side_effect=connect)

        result = await composition.ensure_listening_voice_client(guild, channel)

        self.assertIs(result, replacement)
        composition.wait_for_internal_voice_reconnect.assert_awaited_once_with(channel)
        composition.connect_evelyn_voice_client.assert_awaited_once_with(channel)
        self.assertEqual(replacement.listen_calls, 1)
        self.assertIsNotNone(replacement.on_user_audio)
        self.assertFalse(voice_transition_is_pending(guild.id))
        warmup.assert_awaited_once_with(reason="voice_connect", key="voice:11:22")
        save.assert_called_once_with(
            guild,
            channel,
            reason="ensure_listening",
            manual_disconnect=False,
        )

    async def test_listener_rearm_refreshes_base_udp_transport_before_listen(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)

        async def refresh_udp_transport() -> bool:
            voice_client.events.append("refresh_udp")
            return True

        voice_client.refresh_udp_transport_from_base = refresh_udp_transport

        result = await VoiceSupportComposition(
            self.build_deps()
        ).ensure_listening_voice_client(guild, channel)

        self.assertIs(result, voice_client)
        self.assertEqual(
            voice_client.events,
            ["stop", "refresh_udp", "listen"],
        )

    async def test_unexpected_listener_failure_uses_bounded_exact_client_rearm(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        acquire = AsyncMock(
            side_effect=[
                "initial-listener",
                RuntimeError("private transient lease failure"),
                "rearmed-listener",
            ]
        )
        log = Mock()
        composition = VoiceSupportComposition(
            self.build_deps(
                acquire_voice_input_lease=acquire,
                log=log,
            )
        )

        self.assertIs(
            await composition.ensure_listening_voice_client(guild, channel),
            voice_client,
        )
        rearm = AsyncMock(wraps=composition.ensure_listening_voice_client)
        composition.ensure_listening_voice_client = rearm
        callback = voice_client.listener_failure_callback
        self.assertIsNotNone(callback)

        voice_client.stop_listening()
        callback(
            voice_client,
            voice_client._listener_generation,
        )
        tasks = tuple(composition._listener_rearm_tasks.values())
        with patch(
            "evelyn_core.voice_support_composition_runtime."
            "VOICE_LISTENER_REARM_DELAY_SEC",
            0.0,
        ):
            await asyncio.gather(*tasks)

        self.assertEqual(acquire.await_count, 3)
        self.assertEqual(rearm.await_count, 2)
        for awaited in rearm.await_args_list:
            self.assertEqual(awaited.args, (guild, channel))
            self.assertEqual(
                awaited.kwargs,
                {
                    "force_listener_reset": True,
                    "expected_voice_client": voice_client,
                },
            )
        self.assertTrue(voice_client.is_listener_healthy())
        self.assertEqual(voice_client.listen_calls, 2)
        log.assert_any_call(
            "[VOICE LISTENER REARM OK] guild=11 channel=22"
        )

    async def test_stale_listener_failure_rearm_does_not_replace_newer_generation(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        acquire = AsyncMock(
            side_effect=[
                "initial-listener",
                AssertionError("stale generation acquired a lease"),
            ]
        )
        composition = VoiceSupportComposition(
            self.build_deps(acquire_voice_input_lease=acquire)
        )

        await composition.ensure_listening_voice_client(guild, channel)
        callback = voice_client.listener_failure_callback
        voice_client.stop_listening()
        failed_generation = voice_client._listener_generation
        callback(voice_client, failed_generation)
        voice_client.stop_listening()

        await asyncio.gather(*tuple(composition._listener_rearm_tasks.values()))

        acquire.assert_awaited_once_with()
        self.assertFalse(voice_client.is_listener_healthy())

    async def test_listener_failure_rearm_stops_after_three_transient_failures(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        acquire = AsyncMock(
            side_effect=[
                "initial-listener",
                RuntimeError("private failure one"),
                RuntimeError("private failure two"),
                RuntimeError("private failure three"),
                AssertionError("unbounded listener rearm"),
            ]
        )
        log = Mock()
        composition = VoiceSupportComposition(
            self.build_deps(
                acquire_voice_input_lease=acquire,
                log=log,
            )
        )

        await composition.ensure_listening_voice_client(guild, channel)
        voice_client.stop_listening()
        voice_client.listener_failure_callback(
            voice_client,
            voice_client._listener_generation,
        )
        with patch(
            "evelyn_core.voice_support_composition_runtime."
            "VOICE_LISTENER_REARM_DELAY_SEC",
            0.0,
        ):
            await asyncio.gather(
                *tuple(composition._listener_rearm_tasks.values())
            )

        self.assertEqual(acquire.await_count, 4)
        log.assert_any_call(
            "[VOICE LISTENER REARM FAIL] guild=11 channel=22 "
            "errorType=RuntimeError"
        )
        self.assertNotIn("private failure", str(log.call_args_list))

    async def test_immediate_terminal_failures_share_one_three_attempt_rearm_budget(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        acquire = AsyncMock(
            side_effect=[f"listener-{index}" for index in range(20)]
        )
        composition = VoiceSupportComposition(
            self.build_deps(acquire_voice_input_lease=acquire)
        )
        real_listen = voice_client.listen
        remaining_failures = 8

        def fail_current_listener() -> None:
            nonlocal remaining_failures
            if not voice_client.is_listener_healthy() or remaining_failures <= 0:
                return
            remaining_failures -= 1
            voice_client.stop_listening()
            voice_client.listener_failure_callback(
                voice_client,
                voice_client._listener_generation,
            )

        def listen_then_fail() -> None:
            real_listen()
            asyncio.get_running_loop().call_soon(fail_current_listener)

        voice_client.listen = listen_then_fail

        await composition.ensure_listening_voice_client(guild, channel)
        for _ in range(50):
            await asyncio.sleep(0)
            if not composition._listener_rearm_tasks:
                break

        self.assertEqual(voice_client.listen_calls, 4)
        self.assertEqual(acquire.await_count, 4)
        self.assertEqual(remaining_failures, 4)
        self.assertFalse(voice_client.is_listener_healthy())

    async def test_cancelled_listener_rearm_releases_transition_lease(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        acquired = iter(("initial-listener", "rearm-transition"))
        release = AsyncMock()
        transition_started = asyncio.Event()

        async def acquire() -> str:
            return next(acquired)

        async def block_rearm_transition(
            guild_id: int,
            *,
            reason: str,
        ) -> None:
            self.assertEqual((guild_id, reason), (11, "voice_channel_move"))
            transition_started.set()
            await asyncio.Event().wait()

        composition = VoiceSupportComposition(
            self.build_deps(
                acquire_voice_input_lease=acquire,
                release_voice_input_lease=release,
                stop_active_tts_playback=block_rearm_transition,
            )
        )
        await composition.ensure_listening_voice_client(guild, channel)
        voice_client.stop_listening()
        voice_client.listener_failure_callback(
            voice_client,
            voice_client._listener_generation,
        )
        task = next(iter(composition._listener_rearm_tasks.values()))

        await transition_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        release.assert_any_await("rearm-transition")
        self.assertFalse(composition._listener_rearm_tasks)
        self.assertFalse(voice_transition_is_pending(guild.id))
        self.assertFalse(voice_client.has_voice_input_lease())

    async def test_non_force_health_check_resets_listener_rearm_budget(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        composition = VoiceSupportComposition(self.build_deps())
        rearm_key = (guild.id, channel.id, id(voice_client))
        composition._listener_rearm_attempts[rearm_key] = 3
        composition._listener_rearm_generations[rearm_key] = (
            voice_client._listener_generation
        )

        self.assertIs(
            await composition.ensure_listening_voice_client(guild, channel),
            voice_client,
        )
        self.assertNotIn(rearm_key, composition._listener_rearm_attempts)
        self.assertNotIn(rearm_key, composition._listener_rearm_generations)

        voice_client.stop_listening()
        voice_client.listener_failure_callback(
            voice_client,
            voice_client._listener_generation,
        )
        await asyncio.gather(
            *tuple(composition._listener_rearm_tasks.values())
        )

        self.assertTrue(voice_client.is_listener_healthy())
        self.assertNotIn(rearm_key, composition._listener_rearm_attempts)
        self.assertNotIn(rearm_key, composition._listener_rearm_generations)

    async def test_listener_never_starts_when_input_lease_is_denied(self) -> None:
        channel = FakeVoiceChannel()
        voice_client = FakeVoiceClient(channel, healthy=False)
        guild = FakeGuild(voice_client)
        acquire = AsyncMock(side_effect=RuntimeError("voice_input_lease_conflict"))

        with self.assertRaisesRegex(RuntimeError, "voice_input_lease_conflict"):
            await VoiceSupportComposition(
                self.build_deps(acquire_voice_input_lease=acquire)
            ).ensure_listening_voice_client(guild, channel)

        acquire.assert_awaited_once_with()
        self.assertEqual(voice_client.listen_calls, 0)
        self.assertEqual(voice_client.stop_calls, 0)

    async def test_channel_move_holds_lease_across_stop_and_rearm(self) -> None:
        old_channel = FakeVoiceChannel(channel_id=1, name="old")
        target_channel = FakeVoiceChannel(channel_id=2, name="new")
        voice_client = FakeVoiceClient(old_channel, healthy=True)
        guild = FakeGuild(voice_client)
        active_tokens = {"old-listener"}
        events: list[str] = []
        voice_client.lease_token = "old-listener"

        async def release(token: str) -> None:
            events.append(f"release:{token}")
            active_tokens.discard(token)
            if not active_tokens:
                events.append("server-release")

        async def acquire() -> str:
            events.append("acquire:transition")
            active_tokens.add("transition")
            return "transition"

        voice_client.lease_release = release
        result = await VoiceSupportComposition(
            self.build_deps(
                acquire_voice_input_lease=acquire,
                release_voice_input_lease=release,
            )
        ).ensure_listening_voice_client(guild, target_channel)
        await asyncio.sleep(0)

        self.assertIs(result, voice_client)
        self.assertEqual(events[0], "acquire:transition")
        self.assertIn("release:old-listener", events)
        self.assertNotIn("server-release", events)
        self.assertEqual(active_tokens, {"transition"})
        self.assertTrue(voice_client.has_voice_input_lease())

    async def test_disconnected_same_channel_client_is_replaced(self) -> None:
        channel = FakeVoiceChannel()
        stale_voice_client = FakeVoiceClient(channel, connected=False)
        replacement = FakeVoiceClient(channel, connected=True, healthy=True)
        guild = FakeGuild(stale_voice_client)
        composition = VoiceSupportComposition(self.build_deps())
        async def connect(_channel: FakeVoiceChannel) -> FakeVoiceClient:
            guild.voice_client = replacement
            return replacement

        composition.connect_evelyn_voice_client = AsyncMock(side_effect=connect)

        result = await composition.ensure_listening_voice_client(guild, channel)

        self.assertIs(result, replacement)
        composition.connect_evelyn_voice_client.assert_awaited_once_with(channel)

    async def test_restore_failure_exposes_only_code_and_type(self) -> None:
        channel = FakeVoiceChannel()
        guild = FakeGuild(guild_id=11, channel=channel)
        pipeline_state: dict[str, object] = {}
        log = Mock()
        deps = self.build_deps(
            load_last_voice_channel_state=Mock(
                return_value={
                    "guild_id": 11,
                    "channel_id": 22,
                    "manual_disconnect": False,
                }
            ),
            get_guild=Mock(return_value=guild),
            voice_pipeline_state=pipeline_state,
            log=log,
        )
        composition = VoiceSupportComposition(deps)
        composition.ensure_listening_voice_client = AsyncMock(
            side_effect=RuntimeError(
                "Bearer private-token C:\\private\\voice.wav"
            )
        )

        result = await composition.restore_last_voice_channel()

        self.assertEqual(result, (False, "voice_rearm_failed"))
        self.assertEqual(
            pipeline_state["last_voice_rejoin_error"],
            "voice_rearm_failed",
        )
        self.assertEqual(
            pipeline_state["last_voice_rejoin_error_type"],
            "RuntimeError",
        )
        self.assertNotIn("private-token", str(log.call_args_list))
        self.assertNotIn("voice.wav", str(log.call_args_list))

    async def test_restore_respects_disabled_and_manual_disconnect_gates(self) -> None:
        disabled = VoiceSupportComposition(self.build_deps(voice_rejoin_on_ready=False))
        self.assertEqual(await disabled.restore_last_voice_channel(), (False, "rejoin_disabled"))

        manual = VoiceSupportComposition(
            self.build_deps(
                load_last_voice_channel_state=Mock(return_value={"manual_disconnect": True}),
            )
        )
        self.assertEqual(await manual.restore_last_voice_channel(), (False, "manual_disconnect"))

    def test_main_uses_explicit_support_composition_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_support_composition_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertIn("voice_support_composition = VoiceSupportComposition(", source)
        self.assertIn("create_omnivoice_source = voice_support_composition.create_omnivoice_source", source)
        self.assertIn("restore_last_voice_channel = voice_support_composition.restore_last_voice_channel", source)
        self.assertLess(
            source.index("voice_support_composition = VoiceSupportComposition("),
            source.index("control_page_composition = ControlPageComposition("),
        )
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
