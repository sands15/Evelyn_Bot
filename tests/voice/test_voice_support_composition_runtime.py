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

    def is_internal_voice_reconnect_active(self) -> bool:
        return False

    def is_connected(self) -> bool:
        return self.connected

    def is_listener_healthy(self) -> bool:
        return self.healthy

    def stop_listening(self) -> None:
        self.stop_calls += 1
        self.events.append("stop")
        self.healthy = False

    def listen(self) -> None:
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
