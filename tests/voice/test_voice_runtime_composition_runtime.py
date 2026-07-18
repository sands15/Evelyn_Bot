from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_runtime_composition_runtime import (  # noqa: E402
    LocalMicCompositionDeps,
    VoiceDebugCompositionDeps,
    VoicePipelineCompositionDeps,
    VoiceRuntimeComposition,
    VoiceRuntimeCompositionDeps,
)


def make_pipeline_deps(root: Path, **overrides) -> VoicePipelineCompositionDeps:
    values = dict(
        project_root=root,
        last_channel_state_file="state/last_voice.json",
        summarize_p95_metrics=lambda: {"stt_ms_p95": 10.0},
        merge_log_event_payload=lambda *, explicit, extra=None: {**explicit, **(extra or {})},
        log_turn_event=Mock(),
        local_only_mode=False,
        local_tts_enabled=lambda: False,
        local_tts_snapshot=lambda: {"enabled": False},
        voice_ingress_queue_depth=lambda: 2,
        voice_ingress_queue_max=16,
        live_recent_sec=15.0,
        utterance_assembly_enabled=lambda: True,
        utterance_pending_count=lambda: 3,
        utterance_commit_wait_sec=lambda: 0.2,
        barge_in_continuity=lambda: {"active": True},
        summarize_turn_path_metrics=lambda: {},
        stt_cooldown_after_timeout_sec=1.0,
        monotonic=lambda: 50.0,
        time=lambda: 100.0,
        log=Mock(),
    )
    values.update(overrides)
    return VoicePipelineCompositionDeps(**values)


def make_debug_deps(root: Path, **overrides) -> VoiceDebugCompositionDeps:
    values = dict(
        project_root=root,
        configured_dir="debug_audio",
        max_files_per_guild=20,
        max_age_days=7.0,
        max_total_bytes_per_guild=1024 * 1024,
        preserve_newest=2,
        raw_channels=2,
        raw_rate=48000,
        stt_rate=16000,
        enabled=True,
        queue_max=8,
        create_task=Mock(),
        to_thread=AsyncMock(),
        log=Mock(),
    )
    values.update(overrides)
    return VoiceDebugCompositionDeps(**values)


def make_local_mic_deps(**overrides) -> LocalMicCompositionDeps:
    async def process_member_audio(_member, _pcm_bytes, _debug_meta):
        return None

    values = dict(
        enabled=True,
        input_mode="auto",
        discord_user_ids=lambda: {42},
        local_control_guild_id=1,
        local_control_guild_name="Local",
        local_mic_user_name="정훈",
        normalize_voice_input_mode=lambda mode: "local" if mode == "local_mic" else str(mode or "auto"),
        resolve_local_mic_target=lambda **_kwargs: None,
        should_route_discord_user_to_local_mic=lambda user_id, **kwargs: user_id in kwargs["preferred_user_ids"],
        guilds=lambda: [],
        process_member_audio=lambda: process_member_audio,
        local_only_mode=False,
        service_factory=Mock(),
        get_running_loop=asyncio.get_running_loop,
        create_task=asyncio.create_task,
        local_tts_playback_snapshot=lambda: {"active": False},
        tts_active_max_silence_ms=900,
        max_silence_ms=500,
        discord_suppress_after_segment_sec=1.5,
        sample_rate=16000,
        block_ms=20,
        start_threshold=0.02,
        continue_threshold=0.01,
        start_consecutive=2,
        min_voiced_ms=120,
        preroll_ms=100,
        max_segment_sec=12.0,
        device=None,
        queue_max=8,
        vad_filter_enabled=True,
        env_noise_filter_enabled=True,
        waveform_filter_enabled=True,
        time=lambda: 100.0,
        log=Mock(),
    )
    values.update(overrides)
    return LocalMicCompositionDeps(**values)


def make_composition(root: Path, *, pipeline=None, debug=None, local_mic=None) -> VoiceRuntimeComposition:
    return VoiceRuntimeComposition(
        VoiceRuntimeCompositionDeps(
            pipeline=pipeline or make_pipeline_deps(root),
            debug=debug or make_debug_deps(root),
            local_mic=local_mic or make_local_mic_deps(),
        )
    )


class VoiceRuntimeCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_compositions_own_independent_pipeline_debug_and_local_mic_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = make_composition(root)
            second = make_composition(root)

        first.increment_voice_pipeline_counter("queue_full_drop_count", 2)
        first.voice_debug_counts[7] = 3
        first.set_voice_input_mode("local_mic")

        self.assertEqual(first.voice_pipeline_counters["queue_full_drop_count"], 2)
        self.assertEqual(second.voice_pipeline_counters["queue_full_drop_count"], 0)
        self.assertEqual(second.voice_debug_counts, {})
        self.assertEqual(first.local_mic_runtime_state["input_mode"], "local")
        self.assertEqual(second.local_mic_runtime_state["input_mode"], "auto")

    async def test_pipeline_failure_and_snapshot_use_owned_live_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events = Mock()
            composition = make_composition(
                root,
                pipeline=make_pipeline_deps(
                    root,
                    log_turn_event=events,
                    local_only_mode=True,
                    local_tts_enabled=lambda: True,
                ),
            )

            composition.voice_pipeline_state["last_voice_segment_at"] = 99.0
            composition.record_voice_pipeline_failure("tts_request_failed", RuntimeError("boom"))
            snapshot = composition.build_voice_pipeline_snapshot()

        self.assertEqual(composition.voice_pipeline_counters["tts_request_failed_count"], 1)
        self.assertEqual(composition.voice_pipeline_state["last_failure"]["kind"], "tts_request_failed")
        self.assertEqual(snapshot["outputMode"], "local_speaker")
        self.assertEqual(snapshot["queueDepth"], 2)
        self.assertEqual(snapshot["utteranceAssemblyPendingCount"], 3)
        self.assertTrue(snapshot["bargeInContinuity"]["active"])
        events.assert_called_once()

    async def test_debug_enqueue_uses_owned_queue_and_worker_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            composition = make_composition(Path(temp_dir))
            audio = np.zeros(4, dtype=np.float32)

            with patch(
                "evelyn_core.voice_runtime_composition_runtime.enqueue_voice_debug_audio_from_runtime"
            ) as enqueue:
                composition.save_voice_debug_audio(7, "user", b"pcm", audio, stage_label="final")

        kwargs = enqueue.call_args.kwargs
        self.assertIs(kwargs["queue"], composition.debug_write_queue)
        self.assertIs(kwargs["ensure_worker_started"].__self__, composition)
        self.assertEqual(kwargs["stage_label"], "final")

    async def test_stt_task_adapter_uses_owned_lock_cooldown_and_counters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            composition = make_composition(Path(temp_dir))

            with patch(
                "evelyn_core.voice_runtime_composition_runtime.run_blocking_stt_task_from_runtime",
                new=AsyncMock(return_value="transcript"),
            ) as run_stt:
                result = await composition.run_blocking_stt_task(
                    lambda: "audio",
                    stage="full_stt",
                    timeout_sec=3.0,
                )

        kwargs = run_stt.await_args.kwargs
        kwargs["set_stt_cooldown_until"](55.0)
        kwargs["increment_voice_pipeline_counter"]("stt_timeout_count")
        self.assertEqual(result, "transcript")
        self.assertIs(kwargs["get_stt_inference_lock"](), composition.get_stt_inference_lock())
        self.assertEqual(composition.stt_cooldown_until, 55.0)
        self.assertEqual(composition.voice_pipeline_counters["stt_timeout_count"], 1)

    async def test_local_mic_suppression_wrapper_and_local_only_route_are_connected(self) -> None:
        process_member_audio = AsyncMock()
        member = SimpleNamespace(id=42)
        with tempfile.TemporaryDirectory() as temp_dir:
            composition = make_composition(
                Path(temp_dir),
                local_mic=make_local_mic_deps(
                    input_mode="local",
                    local_only_mode=True,
                    process_member_audio=lambda: process_member_audio,
                ),
            )
            composition.local_mic_service = SimpleNamespace(capture_ready=True)

            suppressed = composition.should_drop_discord_audio_for_local_mic(
                42,
                source="discord_voice",
            )
            await composition.handle_local_mic_segment(b"pcm", {"duration_sec": 0.4})

        self.assertTrue(suppressed)
        process_member_audio.assert_awaited_once()
        routed_member, pcm_bytes, routed_meta = process_member_audio.await_args.args
        self.assertEqual(routed_member.id, member.id)
        self.assertEqual(pcm_bytes, b"pcm")
        self.assertTrue(routed_meta["routed_local_control"])

    async def test_local_mic_stop_clears_owned_service_and_capture_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            composition = make_composition(Path(temp_dir))
            service = SimpleNamespace(stop=Mock(), capture_ready=True)
            composition.local_mic_service = service
            composition.local_mic_runtime_state["capture_ready"] = True

            composition.stop_local_mic_service()

        service.stop.assert_called_once_with()
        self.assertIsNone(composition.local_mic_service)
        self.assertFalse(composition.local_mic_runtime_state["capture_ready"])

    async def test_main_uses_explicit_voice_runtime_composition_bindings(self) -> None:
        main_source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_runtime_composition_runtime.py"
        ).read_text(encoding="utf-8")

        self.assertIn("voice_runtime_composition = VoiceRuntimeComposition(", main_source)
        self.assertIn("voice_pipeline_state = voice_runtime_composition.voice_pipeline_state", main_source)
        self.assertIn("save_voice_debug_audio = voice_runtime_composition.save_voice_debug_audio", main_source)
        self.assertIn(
            "ensure_local_mic_service_started = voice_runtime_composition.ensure_local_mic_service_started",
            main_source,
        )
        self.assertLess(
            main_source.index("voice_runtime_composition = VoiceRuntimeComposition("),
            main_source.index("voice_support_composition = VoiceSupportComposition("),
        )
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
