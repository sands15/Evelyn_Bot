from __future__ import annotations

import unittest
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_audio_ingress_runtime import (
    VoiceAudioIngressDeps,
    prepare_voice_audio_ingress_from_runtime,
)


class VoiceAudioIngressRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.audio = np.full(20, 0.1, dtype=np.float32)
        self.pipeline_state: dict[str, Any] = {}
        self.member = SimpleNamespace(
            id=7,
            bot=False,
            display_name="정훈",
            guild=SimpleNamespace(id=11),
        )
        self.deps = self.build_deps()

    def build_deps(self) -> VoiceAudioIngressDeps:
        def save_debug(*args: Any, **kwargs: Any) -> None:
            self.events.append(("debug", (args, kwargs)))

        def new_metrics(**kwargs: Any) -> dict[str, Any]:
            self.events.append(("metrics", kwargs))
            return {"meta": {}, "marks": {}}

        def log_stage(_metrics: dict[str, Any], label: str, **kwargs: Any) -> None:
            self.events.append(("stage", (label, kwargs)))

        def register_drop(_metrics: dict[str, Any], reason: str, **kwargs: Any) -> None:
            self.events.append(("drop", (reason, kwargs)))

        def log_bottleneck(_metrics: dict[str, Any], **kwargs: Any) -> None:
            self.events.append(("bottleneck", kwargs))

        def update_speaker(*args: Any, **kwargs: Any) -> None:
            self.events.append(("speaker", (args, kwargs)))

        return VoiceAudioIngressDeps(
            voice_pipeline_state=self.pipeline_state,
            prepare_stt_audio=lambda _pcm: self.audio.copy(),
            save_voice_debug_audio=save_debug,
            room_state_snapshot=lambda _key: {"owner_user_id": 7},
            session_topic_ids={},
            build_topic_id=lambda text: f"topic:{text}",
            new_turn_metrics=new_metrics,
            log_voice_stage=log_stage,
            register_drop_reason=register_drop,
            log_voice_bottleneck_summary=log_bottleneck,
            downmix_int16_stereo_to_mono_float=lambda _pcm: self.audio.copy(),
            apply_light_denoise=lambda audio, **_kwargs: audio,
            is_transport_corrupted_audio=lambda _meta: False,
            build_voice_segment=lambda **kwargs: kwargs,
            compute_waveform_activity_stats=lambda _audio, **_kwargs: {
                "voiced_ms": 500.0,
                "longest_voiced_ms": 300.0,
                "body_rms": 0.1,
                "body_peak": 0.2,
            },
            estimate_voice_like_probability=lambda **_kwargs: 0.9,
            update_room_speaker_activity=update_speaker,
            increment_session_bad_audio=lambda _key: 1,
            is_tail_fragment_candidate=lambda **_kwargs: False,
            is_probably_silent=lambda _audio, **_kwargs: False,
            print_fn=lambda *args, **_kwargs: self.events.append(("print", args)),
            stt_use_raw_48k=False,
            rate=10,
            channels=1,
            target_rate=10,
            voice_min_total_sec=0.25,
            tail_fragment_max_raw_sec=1.0,
            vad_enabled=True,
            voice_waveform_min_voiced_ms=200.0,
            voice_waveform_min_run_ms=120.0,
            voice_waveform_body_rms_min=0.05,
            voice_waveform_body_peak_min=0.08,
            time_fn=lambda: 123.5,
        )

    def run_ingress(
        self,
        *,
        deps: VoiceAudioIngressDeps | None = None,
        pcm_bytes: bytes = b"x" * 40,
        debug_meta: dict[str, Any] | None = None,
        ingress_during_reply: bool = False,
        owner_user_id_on_ingress: int | None = None,
    ):
        return prepare_voice_audio_ingress_from_runtime(
            self.member,
            pcm_bytes,
            debug_meta,
            session_key="voice:11:7",
            room_session_key="room:11",
            turn_id="turn-1",
            segment_id=3,
            ingress_during_reply=ingress_during_reply,
            owner_user_id_on_ingress=owner_user_id_on_ingress,
            deps=deps or self.deps,
        )

    def drop_reasons(self) -> list[str]:
        return [payload[0] for kind, payload in self.events if kind == "drop"]

    def test_happy_path_returns_explicit_audio_contract(self) -> None:
        result = self.run_ingress(
            debug_meta={
                "queue_wait_ms": "12.5",
                "source": "local_mic",
                "validation_session_id": "validation-1",
                "validation_step_id": "03-interrupt",
                "validation_attempt": 2,
                "validation_attempt_id": "attempt-private-2",
            }
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.guild_id, 11)
        self.assertEqual(result.speaker_name, "정훈")
        self.assertEqual(result.owner_user_id, 7)
        self.assertEqual(result.raw_seconds, 2.0)
        self.assertEqual(result.duration_sec, 2.0)
        self.assertEqual(result.stt_sampling_rate, 10)
        self.assertEqual(result.wake_sampling_rate, 10)
        self.assertEqual(result.voiced_ms, 500.0)
        self.assertEqual(result.voice_like_prob, 0.9)
        self.assertEqual(result.metrics["meta"]["voice_queue_wait_ms"], 12.5)
        self.assertEqual(result.metrics["meta"]["ingress_source"], "local_mic")
        self.assertEqual(result.metrics["meta"]["validation_session_id"], "validation-1")
        self.assertEqual(result.metrics["meta"]["validation_step_id"], "03-interrupt")
        self.assertEqual(result.metrics["meta"]["validation_attempt"], 2)
        self.assertEqual(result.metrics["meta"]["validation_attempt_id"], "attempt-private-2")
        self.assertEqual(self.pipeline_state["last_voice_segment_at"], 123.5)
        self.assertEqual(self.drop_reasons(), [])

    def test_other_speaker_is_dropped_before_audio_processing(self) -> None:
        result = self.run_ingress(ingress_during_reply=True, owner_user_id_on_ingress=99)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["other_speaker_during_reply"])
        stages = [payload[0] for kind, payload in self.events if kind == "stage"]
        self.assertIn("다른 화자 중복 진입 차단", stages)

    def test_empty_audio_is_rejected(self) -> None:
        deps = replace(self.deps, prepare_stt_audio=lambda _pcm: np.array([], dtype=np.float32))

        result = self.run_ingress(deps=deps)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["empty_audio"])

    def test_short_total_audio_is_rejected_and_preserved_for_debug(self) -> None:
        result = self.run_ingress(pcm_bytes=b"x" * 4)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["too_short_total"])
        debug_payloads = [payload for kind, payload in self.events if kind == "debug"]
        self.assertEqual(debug_payloads[-1][1]["final_text"], "[SHORT AUDIO IGNORE]")

    def test_transport_corruption_is_rejected_after_waveform_measurement(self) -> None:
        deps = replace(self.deps, is_transport_corrupted_audio=lambda _meta: True)

        result = self.run_ingress(deps=deps, pcm_bytes=b"x" * 24)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["transport_corrupted"])

    def test_vad_silence_is_rejected_without_waveform_override(self) -> None:
        deps = replace(
            self.deps,
            is_probably_silent=lambda _audio, **_kwargs: True,
            compute_waveform_activity_stats=lambda _audio, **_kwargs: {
                "voiced_ms": 20.0,
                "longest_voiced_ms": 10.0,
                "body_rms": 0.001,
                "body_peak": 0.002,
            },
        )

        result = self.run_ingress(deps=deps)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["vad_ignore"])

    def test_vad_can_be_overridden_by_strong_waveform_evidence(self) -> None:
        deps = replace(self.deps, is_probably_silent=lambda _audio, **_kwargs: True)

        result = self.run_ingress(deps=deps)

        self.assertIsNotNone(result)
        stages = [payload[0] for kind, payload in self.events if kind == "stage"]
        self.assertIn("VAD override", stages)

    def test_main_delegates_ingress_filtering_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        function_source = composition_source[composition_source.index("    async def process_member_audio_impl(") :]
        builder_source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "voice_member_pipeline_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("prepare_audio_ingress=prepare_voice_audio_ingress_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("compute_waveform_activity_stats(", function_source)
        self.assertNotIn("if VAD_ENABLED and is_probably_silent", function_source)


if __name__ == "__main__":
    unittest.main()
