from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_mic import (  # noqa: E402
    LocalMicCaptureService,
    mono16k_float_to_discord_pcm,
    normalize_sounddevice_identifier,
    parse_user_id_set,
    resolve_local_mic_target,
    serialize_local_mic_target,
    should_route_discord_user_to_local_mic,
)
from evelyn_core.local_io_bridge import LocalIoBridge, iter_pcm_aligned_chunks  # noqa: E402


class LocalMicRoutingTests(unittest.TestCase):
    def test_parse_user_id_set_ignores_invalid_tokens(self) -> None:
        parsed = parse_user_id_set("441943340624248843, nope; 405351496012791808")
        self.assertEqual(parsed, {441943340624248843, 405351496012791808})

    def test_normalize_sounddevice_identifier_accepts_numeric_string_index(self) -> None:
        self.assertEqual(normalize_sounddevice_identifier("18"), 18)
        self.assertEqual(normalize_sounddevice_identifier(" fifine Microphone "), "fifine Microphone")
        self.assertIsNone(normalize_sounddevice_identifier(" "))

    def test_should_route_only_when_capture_ready(self) -> None:
        self.assertTrue(
            should_route_discord_user_to_local_mic(
                441943340624248843,
                preferred_user_ids={441943340624248843},
                capture_ready=True,
            )
        )
        self.assertFalse(
            should_route_discord_user_to_local_mic(
                441943340624248843,
                preferred_user_ids={441943340624248843},
                capture_ready=False,
            )
        )
        self.assertFalse(
            should_route_discord_user_to_local_mic(
                405351496012791808,
                preferred_user_ids={441943340624248843},
                capture_ready=True,
            )
        )

    def test_resolve_local_mic_target_uses_member_in_active_voice_channel(self) -> None:
        target_member = SimpleNamespace(id=441943340624248843, bot=False)
        other_member = SimpleNamespace(id=405351496012791808, bot=False)
        channel = SimpleNamespace(id=99, members=[other_member, target_member])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.guild_id, 7)
        self.assertEqual(target.voice_channel_id, 99)
        self.assertIs(target.member, target_member)

    def test_resolve_local_mic_target_returns_none_without_match(self) -> None:
        channel = SimpleNamespace(id=99, members=[SimpleNamespace(id=1, bot=False)])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})

        self.assertIsNone(target)

    def test_serialize_local_mic_target_returns_json_safe_snapshot(self) -> None:
        member = SimpleNamespace(id=441943340624248843, name="JH", display_name="정훈", bot=False)
        channel = SimpleNamespace(id=99, members=[member])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})
        snapshot = serialize_local_mic_target(target)

        self.assertEqual(
            snapshot,
            {
                "guildId": 7,
                "voiceChannelId": 99,
                "memberId": 441943340624248843,
                "memberName": "정훈",
            },
        )

    def test_mono16k_float_to_discord_pcm_matches_discord_shape(self) -> None:
        audio = np.full(16000, 0.1, dtype=np.float32)

        pcm_bytes = mono16k_float_to_discord_pcm(audio, sampling_rate=16000)

        self.assertEqual(len(pcm_bytes), 48000 * 2 * 2)

    def test_local_io_bridge_aligns_streamed_pcm_chunks(self) -> None:
        chunks = list(iter_pcm_aligned_chunks([b"abc", b"defg", b"h"]))

        self.assertEqual(chunks, [b"ab", b"cdef", b"gh"])
        self.assertTrue(all(len(chunk) % 2 == 0 for chunk in chunks))

    def test_local_io_bridge_shutdown_starts_script_and_exits_once(self) -> None:
        bridge = LocalIoBridge()

        with (
            patch.object(bridge, "_start_shutdown_script") as start_shutdown,
            patch.object(bridge, "_schedule_bridge_exit") as schedule_exit,
        ):
            bridge._handle_control_response({"shutdown": {"requested": True}})
            bridge._handle_control_response({"shutdown": {"requested": True}})

        self.assertTrue(bridge.shutdown_started)
        start_shutdown.assert_called_once_with()
        schedule_exit.assert_called_once_with()

    def test_local_io_bridge_restart_starts_script_and_exits_once(self) -> None:
        bridge = LocalIoBridge()

        with (
            patch.object(bridge, "_start_restart_script") as start_restart,
            patch.object(bridge, "_schedule_bridge_exit") as schedule_exit,
        ):
            bridge._handle_control_response({"restart": {"requested": True}})
            bridge._handle_control_response({"restart": {"requested": True}})

        self.assertTrue(bridge.restart_started)
        start_restart.assert_called_once_with()
        schedule_exit.assert_called_once_with()

    def test_local_io_bridge_enqueues_control_page_speak_requests(self) -> None:
        bridge = LocalIoBridge()

        bridge._handle_control_response(
            {
                "speakRequests": [
                    {"id": "speak-1", "text": " hello from page ", "source": "control_page"},
                    {"id": "empty", "text": " "},
                ]
            }
        )

        self.assertEqual(bridge.speak_request_queue.qsize(), 1)
        self.assertEqual(bridge.speak_request_queue.get_nowait()["text"], "hello from page")

    def test_local_io_bridge_help_reply_is_not_sent_to_tts(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()  # type: ignore[method-assign]
            bridge._transcribe = AsyncMock(return_value="/help")  # type: ignore[method-assign]
            bridge._chat = AsyncMock(return_value="사용 가능한 명령 목록")  # type: ignore[method-assign]
            bridge._chat_stream_and_speak = AsyncMock(  # type: ignore[method-assign]
                side_effect=AssertionError("help must not enter the TTS stream")
            )
            bridge._speak = AsyncMock(  # type: ignore[method-assign]
                side_effect=AssertionError("help must not enter one-shot TTS")
            )
            await bridge._handle_segment(b"pcm", {"source": "test"})
            return bridge

        bridge = asyncio.run(scenario())

        bridge._chat.assert_awaited_once_with("/help")  # type: ignore[union-attr]
        bridge._chat_stream_and_speak.assert_not_awaited()  # type: ignore[union-attr]
        bridge._speak.assert_not_awaited()  # type: ignore[union-attr]
        self.assertEqual(bridge.last_latency["ttsMs"], 0.0)

    def test_local_io_bridge_tts_warmup_retries_until_ready(self) -> None:
        bridge = LocalIoBridge()
        bridge.session = object()
        bridge._drain_tts_payload = AsyncMock(side_effect=[RuntimeError("server disconnected"), 1234])  # type: ignore[method-assign]
        bridge._post_status = AsyncMock()  # type: ignore[method-assign]

        with (
            patch("evelyn_core.local_io_bridge.LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC", 0),
            patch("evelyn_core.local_io_bridge.LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS", 2),
            patch("evelyn_core.local_io_bridge.LOCAL_BRIDGE_TTS_WARMUP_RETRY_DELAY_SEC", 0),
            patch("evelyn_core.local_io_bridge.asyncio.sleep", new=AsyncMock()),
        ):
            asyncio.run(bridge._warmup_tts_after_delay())

        self.assertTrue(bridge.tts_warmup_done)
        self.assertEqual(bridge.tts_warmup_error, "")
        self.assertEqual(bridge._drain_tts_payload.await_count, 2)
        bridge._post_status.assert_awaited_once()

    def test_local_io_bridge_applies_mic_enable_request_once(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = False

            async def start_mic() -> None:
                bridge.ready = True
                bridge.last_error = ""

            bridge._start_mic = AsyncMock(side_effect=start_mic)  # type: ignore[method-assign]
            response = {"micControlRequest": {"revision": 17, "enabled": True}}
            bridge._handle_control_response(response)
            bridge._handle_control_response(response)
            await asyncio.gather(*list(bridge.mic_control_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertTrue(bridge.mic_enabled)
        self.assertTrue(bridge.ready)
        self.assertEqual(bridge.mic_control_request_revision, 17)
        bridge._start_mic.assert_awaited_once()  # type: ignore[union-attr]

    def test_local_io_bridge_applies_mic_disable_and_discards_queued_audio(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = True
            bridge.queue.put_nowait((b"pcm", {"source": "test"}))

            async def stop_mic() -> None:
                bridge.mic_enabled = False
                bridge._discard_pending_mic_segments()
                bridge.ready = True
                bridge.last_error = ""

            bridge._stop_mic = AsyncMock(side_effect=stop_mic)  # type: ignore[method-assign]
            bridge._handle_control_response(
                {"micControlRequest": {"revision": 23, "enabled": False}}
            )
            await asyncio.gather(*list(bridge.mic_control_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertFalse(bridge.mic_enabled)
        self.assertTrue(bridge.ready)
        self.assertEqual(bridge.queue.qsize(), 0)
        self.assertEqual(bridge.mic_control_request_revision, 23)
        bridge._stop_mic.assert_awaited_once()  # type: ignore[union-attr]

    def test_local_io_bridge_rejects_minecraft_world_action_without_lease_owner(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()  # type: ignore[method-assign]
            bridge._launch_minecraft_stack = AsyncMock(  # type: ignore[method-assign]
                return_value={"alreadyReady": False, "launcherExitCode": 0}
            )
            bridge._activate_minecraft_command = AsyncMock(  # type: ignore[method-assign]
                return_value={
                    "commandApplied": True,
                    "connected": True,
                    "connectionState": "connected",
                }
            )
            response = {
                "minecraftCommandRequest": {
                    "revision": 31,
                    "command": "마인크래프트에서 나무 캐줘",
                    "action": "goal",
                }
            }
            bridge._handle_control_response(response)
            bridge._handle_control_response(response)
            await asyncio.gather(*list(bridge.minecraft_command_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertEqual(bridge.minecraft_command_request_revision, 31)
        self.assertEqual(bridge.minecraft_command_state, "failed")
        self.assertEqual(
            bridge.minecraft_command_error,
            "RuntimeError('minecraft_world_authorization_required')",
        )
        self.assertFalse(bridge.minecraft_command_result["commandApplied"])
        self.assertFalse(bridge.minecraft_command_result["connected"])
        bridge._launch_minecraft_stack.assert_not_awaited()  # type: ignore[union-attr]
        bridge._activate_minecraft_command.assert_not_awaited()  # type: ignore[union-attr]
        self.assertEqual(bridge._post_status.await_count, 2)  # type: ignore[union-attr]

    def test_local_io_bridge_ignores_launcher_for_rejected_world_action(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()  # type: ignore[method-assign]
            bridge._launch_minecraft_stack = AsyncMock(  # type: ignore[method-assign]
                side_effect=RuntimeError("compose failed")
            )
            bridge._handle_control_response(
                {
                    "minecraftCommandRequest": {
                        "revision": 32,
                        "command": "마인크래프트 시작해",
                        "action": "start",
                    }
                }
            )
            await asyncio.gather(*list(bridge.minecraft_command_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertEqual(bridge.minecraft_command_request_revision, 32)
        self.assertEqual(bridge.minecraft_command_state, "failed")
        self.assertIn(
            "minecraft_world_authorization_required",
            bridge.minecraft_command_error,
        )
        self.assertFalse(bridge.minecraft_command_result["commandApplied"])
        bridge._launch_minecraft_stack.assert_not_awaited()  # type: ignore[union-attr]

    def test_local_io_bridge_reports_dynamic_mic_state_and_control_revision(self) -> None:
        bridge_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_io_bridge.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"micEnabled": self.mic_enabled', bridge_source)
        self.assertIn('"micControlRevision": self.mic_control_request_revision', bridge_source)
        self.assertIn("if not self.mic_enabled:", bridge_source)

    def test_local_mic_short_segments_are_reported_as_rejected(self) -> None:
        captured: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: captured.append((pcm, meta)),
            sample_rate=16000,
            min_voiced_ms=200,
        )
        audio = np.full(16000 // 20, 0.1, dtype=np.float32)
        service._capture_active = True
        service._current_blocks = [audio]
        service._voiced_samples = audio.size
        service._total_samples = audio.size

        service._flush_active_segment(force=False)

        self.assertEqual(captured, [])
        self.assertEqual(service.rejected_segment_count, 1)
        self.assertEqual(service.last_rejected_reason, "too_short")
        self.assertEqual((service.last_segment_filter or {}).get("reason"), "too_short")

    def test_local_mic_voice_filter_rejects_silence(self) -> None:
        captured: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: captured.append((pcm, meta)),
            sample_rate=16000,
            min_voiced_ms=200,
            vad_filter_enabled=True,
            env_noise_filter_enabled=True,
            waveform_filter_enabled=True,
        )
        audio = np.zeros(16000 // 2, dtype=np.float32)
        service._capture_active = True
        service._current_blocks = [audio]
        service._voiced_samples = audio.size
        service._total_samples = audio.size

        with patch("evelyn_core.local_mic.is_probably_silent", return_value=True):
            service._flush_active_segment(force=False)

        self.assertEqual(captured, [])
        self.assertEqual(service.rejected_segment_count, 1)
        self.assertEqual(service.last_rejected_reason, "vad_silent")

    def test_local_mic_voice_filter_allows_speech_like_audio(self) -> None:
        captured: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: captured.append((pcm, meta)),
            sample_rate=16000,
            min_voiced_ms=200,
            vad_filter_enabled=True,
            env_noise_filter_enabled=True,
            waveform_filter_enabled=True,
        )
        t = np.arange(16000 // 2, dtype=np.float32) / 16000.0
        audio = (0.08 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
        service._capture_active = True
        service._current_blocks = [audio]
        service._voiced_samples = audio.size
        service._total_samples = audio.size

        with patch("evelyn_core.local_mic.is_probably_silent", return_value=False):
            service._flush_active_segment(force=False)

        self.assertEqual(len(captured), 1)
        _, meta = captured[0]
        self.assertEqual(meta["source"], "local_mic")
        self.assertFalse(meta["voice_filter"]["rejected"])

    def test_local_mic_uses_dynamic_max_silence_provider(self) -> None:
        flush_calls: list[bool] = []
        service = LocalMicCaptureService(
            on_segment=lambda _pcm, _meta: None,
            sample_rate=16000,
            block_ms=100,
            max_silence_ms=500,
            max_silence_ms_provider=lambda: 200,
        )
        service._capture_active = True
        service._trailing_silence = 1
        service._flush_active_segment = lambda *, force: flush_calls.append(force)  # type: ignore[method-assign]

        service._consume_block(np.zeros(1600, dtype=np.float32), {})

        self.assertEqual(flush_calls, [False])
        self.assertEqual(service.last_effective_max_silence_ms, 200)

    def test_start_local_disables_local_mic_by_default(self) -> None:
        script = (REPO_ROOT / "evelyn_core" / "start_local.bat").read_text(encoding="utf-8")

        self.assertIn('if "%LOCAL_MIC_ENABLED%"=="" set "LOCAL_MIC_ENABLED=false"', script)
        self.assertIn('if "%LOCAL_MIC_START_THRESHOLD%"=="" set "LOCAL_MIC_START_THRESHOLD=0.002"', script)
        self.assertIn('if "%LOCAL_MIC_CONTINUE_THRESHOLD%"=="" set "LOCAL_MIC_CONTINUE_THRESHOLD=0.001"', script)
        self.assertIn('if "%LOCAL_MIC_MIN_VOICED_MS%"=="" set "LOCAL_MIC_MIN_VOICED_MS=280"', script)
        self.assertIn('if "%LOCAL_MIC_WAVEFORM_FILTER_ENABLED%"=="" set "LOCAL_MIC_WAVEFORM_FILTER_ENABLED=true"', script)
        self.assertNotIn("OMNIVOICE_SPEED", script)
        self.assertNotIn("TTS_CHUNK_TAIL_SILENCE_MS", script)
        self.assertNotIn("LOCAL_TTS_TAIL_SILENCE_MS", script)
        self.assertNotIn("TTS_FIRST_CHUNK_MIN_CHARS", script)
        self.assertNotIn("TTS_NEXT_CHUNK_MIN_CHARS", script)
        self.assertNotIn('set "LOCAL_MIC_ENABLED=true"', script)

    def test_background_local_mode_uses_docker_core_and_host_supervised_bridge(self) -> None:
        script = (REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_local_background.ps1").read_text(encoding="utf-8")
        bridge_source = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_io_bridge.py").read_text(encoding="utf-8")
        supervisor_source = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "host_supervisor.py").read_text(encoding="utf-8")

        self.assertIn("Invoke-DockerCommand -Arguments (@('compose') + $composeArgs)", script)
        self.assertIn("'--profile', 'llm'", script)
        self.assertIn("'--profile', 'tts'", script)
        self.assertIn("'--profile', 'stt'", script)
        self.assertNotIn("'--profile', 'voyager'", script)
        self.assertIn("EVELYN_DOCKER_BUILD", script)
        self.assertIn("@('up', '-d')", script)
        self.assertIn("'stop', 'discord_bot'", script)
        self.assertIn("EVELYN_LOCAL_KEEP_DISCORD_BOT", script)
        self.assertIn("evelyn_core.host_supervisor", script)
        self.assertNotIn("-m evelyn_core.local_io_bridge", script)
        self.assertIn('"evelyn_core.local_io_bridge"', supervisor_source)
        self.assertIn("HostVisionBridge", bridge_source)
        self.assertIn('name="local-bridge-host-vision"', bridge_source)
        self.assertIn('"hostVision":', bridge_source)
        self.assertIn("--project-root '$projectRoot'", script)
        self.assertIn("LOCAL_BRIDGE_BOT_API_BASE", script)
        self.assertIn("LOCAL_MIC_START_THRESHOLD = '0.002'", script)
        self.assertIn("LOCAL_MIC_CONTINUE_THRESHOLD = '0.001'", script)
        self.assertIn("LOCAL_MIC_MIN_VOICED_MS = '280'", script)
        self.assertIn("LOCAL_MIC_WAVEFORM_FILTER_ENABLED = 'true'", script)
        self.assertIn("LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC = '0.7'", script)
        self.assertNotIn("py -3 main.py", script)
        self.assertIn("LOCAL_BRIDGE_STREAMING_TTS_ENABLED", bridge_source)
        self.assertIn("if not self.mic_enabled:", bridge_source)
        self.assertIn('"micEnabled": self.mic_enabled', bridge_source)
        self.assertIn('"micControlRevision": self.mic_control_request_revision', bridge_source)
        self.assertIn("LOCAL_BRIDGE_TTS_WARMUP_ENABLED = 'true'", script)
        self.assertIn("LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC = '0.5'", script)
        self.assertIn("LOCAL_BRIDGE_TTS_WARMUP_TEXT", bridge_source)
        self.assertIn("tts_warmup_done", bridge_source)
        self.assertIn("/api/control-page/chat-stream", bridge_source)
        self.assertIn('"turnId": self.active_turn_id', bridge_source)
        self.assertIn('if event_type == "progress":', bridge_source)
        self.assertIn('await websocket.send_json({"type": "commit"})', bridge_source)
        self.assertIn('"progressCount": progress_count', bridge_source)
        self.assertIn('"firstProgressMs": round(first_progress_ms, 1)', bridge_source)
        self.assertIn("_play_streaming_pcm_response", bridge_source)
        self.assertIn("tts_played_streaming", bridge_source)
        self.assertIn('"num_step": OMNIVOICE_NUM_STEP', bridge_source)
        self.assertIn('"stream_strategy": OMNIVOICE_STREAM_STRATEGY', bridge_source)
        self.assertIn('"stream_first_block_steps": OMNIVOICE_STREAM_FIRST_BLOCK_STEPS', bridge_source)
        self.assertIn('"mic": mic_stats', bridge_source)

    def test_start_local_has_lightweight_vision_profile(self) -> None:
        script = (REPO_ROOT / "evelyn_core" / "start_local.bat").read_text(encoding="utf-8")

        self.assertIn('if /I "%~1"=="--lightweight" set "LOCAL_PROFILE=lightweight"', script)
        self.assertIn('if "%VISION_LOAD_OCR%"=="" set "VISION_LOAD_OCR=false"', script)
        self.assertIn('if "%VISION_WATCH_RUN_OCR%"=="" set "VISION_WATCH_RUN_OCR=false"', script)
        self.assertIn('if "%VISION_OCR_LAZY_LOAD%"=="" set "VISION_OCR_LAZY_LOAD=true"', script)
        self.assertIn('if "%VISION_OCR_UNLOAD_AFTER_REQUEST%"=="" set "VISION_OCR_UNLOAD_AFTER_REQUEST=true"', script)
        self.assertIn("skip Falcon-OCR startup load", script)
        self.assertLess(
            script.index('if /I "%LOCAL_PROFILE%"=="lightweight" ('),
            script.index('call "%~dp0start_env.bat"'),
        )

    def test_main_routes_local_only_mic_without_discord_target(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        local_mic_segment_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_mic_segment_runtime.py"
        ).read_text(encoding="utf-8")
        control_page_tools = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
        ).read_text(encoding="utf-8")
        control_page_state = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"
        ).read_text(encoding="utf-8")
        discord_tts_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "discord_tts_stream_runtime.py"
        ).read_text(encoding="utf-8")
        voice_io_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        voice_runtime_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_runtime_composition_runtime.py"
        ).read_text(encoding="utf-8")
        runtime_lifecycle_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "runtime_lifecycle_composition.py"
        ).read_text(encoding="utf-8")
        control_page_status_tool_composition = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "control_page_status_tool_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("if target is None and deps.local_only_mode", local_mic_segment_runtime)
        self.assertIn("local_only_mode=LOCAL_ONLY_MODE", main_py)
        self.assertIn("local_control_voice_member=self.local_control_voice_member", voice_runtime_composition)
        self.assertIn("handle_local_mic_segment_from_runtime(", voice_runtime_composition)
        self.assertIn("should_drop_discord_audio_for_local_mic = (", main_py)
        self.assertIn(
            "ensure_local_mic_service_started=lambda: ensure_local_mic_service_started()",
            main_py,
        )
        self.assertIn(
            "await deps.ensure_local_mic_service_started()",
            runtime_lifecycle_composition,
        )
        self.assertIn("deps.is_local_speaker_voice_client(vc)", discord_tts_runtime)
        self.assertIn("ask_llm_and_speak_local_from_runtime(", voice_io_composition)
        self.assertIn('"/voice": "voice.status"', control_page_tools)
        self.assertIn('"/voice status": "voice.status"', control_page_tools)
        self.assertIn(
            "control_page_status_tool_composition.build_control_page_tool_runtime_deps",
            main_py,
        )
        self.assertIn(
            "execute_control_page_voice_tool=execute_control_page_voice_tool",
            control_page_status_tool_composition,
        )
        self.assertIn('if tool_name == "voice.status":', control_page_state)

    def test_local_speaker_uses_streaming_sentence_tts_with_full_answer_fallback(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        voice_delivery_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_delivery_runtime.py"
        ).read_text(encoding="utf-8")
        local_tts_stream_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_tts_stream_runtime.py"
        ).read_text(encoding="utf-8")
        voice_io_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        delivery_entry_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "delivery_entry_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def start_streaming_local_voice_delivery(", delivery_entry_composition)
        self.assertIn(
            "start_streaming_local_voice_delivery = (\n"
            "    delivery_entry_composition.start_streaming_local_voice_delivery\n"
            ")",
            main_py,
        )
        self.assertIn("async def stream_local_tts_sentences(", voice_io_composition)
        self.assertIn('"delivery_mode"] = "llm_sentence_stream"', voice_delivery_runtime)
        self.assertIn("on_sentence=fanout.on_chunk", voice_delivery_runtime)
        self.assertIn("stream_local_tts_sentences_from_runtime(", voice_io_composition)
        self.assertIn("prefetch_tts_sources(", local_tts_stream_runtime)
        self.assertIn("on_first_playback=", local_tts_stream_runtime)
        self.assertIn('"local_first_playback_logged"', voice_delivery_runtime)
        self.assertIn('"local_tts_first_playback"', delivery_entry_composition)
        self.assertIn("omnivoice_num_step=OMNIVOICE_NUM_STEP", main_py)
        self.assertIn("await deps.speak_answer_local(", voice_delivery_runtime)
        self.assertIn('metrics.setdefault("meta", {})["local_streaming_tts_fallback_used"] = True', voice_delivery_runtime)


if __name__ == "__main__":
    unittest.main()
