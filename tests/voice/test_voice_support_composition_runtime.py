from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_support_composition_runtime import (
    VoiceSupportComposition,
    VoiceSupportCompositionDeps,
)


class FakeVoiceChannel:
    def __init__(self, *, channel_id: int = 22, name: str = "voice") -> None:
        self.id = channel_id
        self.name = name


class FakeVoiceClient:
    def __init__(self, channel: FakeVoiceChannel, *, healthy: bool = False) -> None:
        self.channel = channel
        self.healthy = healthy
        self.on_user_audio = None
        self.stop_calls = 0
        self.listen_calls = 0
        self.moves: list[FakeVoiceChannel] = []

    def is_internal_voice_reconnect_active(self) -> bool:
        return False

    def is_listener_healthy(self) -> bool:
        return self.healthy

    def stop_listening(self) -> None:
        self.stop_calls += 1

    def listen(self) -> None:
        self.listen_calls += 1

    async def move_to(self, channel: FakeVoiceChannel) -> None:
        self.moves.append(channel)
        self.channel = channel


class FakeGuild:
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

        result = await VoiceSupportComposition(self.build_deps()).ensure_listening_voice_client(
            guild,
            target_channel,
        )

        self.assertIs(result, voice_client)
        self.assertEqual(voice_client.moves, [target_channel])

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
