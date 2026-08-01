from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_stt_flow import (
    apply_fuzzy_wake_near_miss,
    apply_strict_wake_confirm_policy,
    interpret_wake_probe_result,
)
from evelyn_core.voice_wake_probe_runtime import (
    VoiceWakeProbeDeps,
    run_voice_wake_probe_from_runtime,
)


class VoiceWakeProbeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.member = SimpleNamespace(id=7, display_name="정훈")
        self.audio = np.ones(32, dtype=np.float32)
        self.wake_result: dict[str, Any] = {
            "wake_detected": True,
            "wake_probe_text": "이블린",
            "wake_confirm_text": "이블린",
            "wake_match_mode": "exact",
            "wake_alias": "이블린",
        }
        self.deps = self.build_deps()

    def build_deps(self) -> VoiceWakeProbeDeps:
        async def run_blocking(task, **kwargs: Any) -> Any:
            self.events.append(("blocking", kwargs))
            return task()

        def log_stage(_metrics: dict[str, Any], label: str, **kwargs: Any) -> None:
            self.events.append(("stage", (label, kwargs)))

        def register_drop(_metrics: dict[str, Any], reason: str, **kwargs: Any) -> None:
            self.events.append(("drop", (reason, kwargs)))

        def log_bottleneck(_metrics: dict[str, Any], **kwargs: Any) -> None:
            self.events.append(("bottleneck", kwargs))

        def save_debug(*args: Any, **kwargs: Any) -> None:
            self.events.append(("debug", (args, kwargs)))

        return VoiceWakeProbeDeps(
            is_room_owner_active=lambda _room_key, _user_id: False,
            is_session_active_for_user=lambda _session_key, _user_id: False,
            pick_active_speaker=lambda _room_key: 7,
            log_voice_stage=log_stage,
            run_blocking_stt_task=run_blocking,
            detect_wake_word_sync=lambda _audio, **_kwargs: self.wake_result,
            interpret_wake_probe_result=interpret_wake_probe_result,
            clean_text=lambda text: text.strip(),
            apply_stt_post_corrections=lambda text, **_kwargs: text.strip(),
            should_require_confirm_exact_for_wake=lambda _meta: False,
            apply_strict_wake_confirm_policy=apply_strict_wake_confirm_policy,
            apply_fuzzy_wake_near_miss=apply_fuzzy_wake_near_miss,
            fuzzy_leading_wake_alias=lambda _text: None,
            register_drop_reason=register_drop,
            log_voice_bottleneck_summary=log_bottleneck,
            is_likely_environment_noise=lambda _audio, **_kwargs: False,
            looks_like_brief_filler_text=lambda _text: False,
            looks_like_repetitive_noise_text=lambda _text: False,
            compute_voice_band_metrics=lambda _audio, **_kwargs: (0.2, 0.3, 0.04),
            save_voice_debug_audio=save_debug,
            increment_session_bad_audio=lambda _session_key: 4,
            should_skip_full_stt_after_wake_probe=lambda **_kwargs: False,
            print_fn=lambda *args, **_kwargs: self.events.append(("print", args)),
            wake_stt_timeout_sec=4.0,
            voice_no_wake_max_continue_sec=3.0,
        )

    async def run_probe(
        self,
        *,
        deps: VoiceWakeProbeDeps | None = None,
        raw_seconds: float = 4.0,
        debug_meta: dict[str, Any] | None = None,
    ):
        return await run_voice_wake_probe_from_runtime(
            member=self.member,
            pcm_bytes=b"pcm",
            debug_meta=debug_meta,
            session_key="voice:11:7",
            room_session_key="room:11",
            owner_user_id=7,
            guild_id=11,
            speaker_name="정훈",
            audio16k=self.audio,
            audio_for_wake=self.audio,
            wake_sampling_rate=16000,
            raw_seconds=raw_seconds,
            duration_sec=1.25,
            metrics={"meta": {}, "marks": {}},
            deps=deps or self.deps,
        )

    def drop_reasons(self) -> list[str]:
        return [payload[0] for kind, payload in self.events if kind == "drop"]

    async def test_owner_followup_skips_probe_and_preserves_active_speaker(self) -> None:
        deps = replace(
            self.deps,
            is_room_owner_active=lambda _room_key, _user_id: True,
            is_session_active_for_user=lambda _session_key, _user_id: True,
            pick_active_speaker=lambda _room_key: 23,
        )

        result = await self.run_probe(deps=deps)

        self.assertIsNotNone(result)
        self.assertTrue(result.owner_followup_active)
        self.assertEqual(result.active_speaker_user_id, 23)
        self.assertEqual(result.wake_match_mode, "owner_followup_active")
        self.assertFalse(any(kind == "blocking" for kind, _payload in self.events))

    async def test_exact_wake_returns_detected_result(self) -> None:
        result = await self.run_probe()

        self.assertIsNotNone(result)
        self.assertTrue(result.wake_detected)
        self.assertEqual(result.wake_match_mode, "exact")
        self.assertEqual(result.wake_alias, "이블린")
        self.assertEqual(self.drop_reasons(), [])

    async def test_probe_error_registers_drop_and_stops(self) -> None:
        async def fail_probe(_task, **_kwargs: Any) -> Any:
            raise RuntimeError("wake failed")

        result = await self.run_probe(deps=replace(self.deps, run_blocking_stt_task=fail_probe))

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["wake_probe_error"])
        self.assertTrue(any(kind == "bottleneck" for kind, _payload in self.events))

    async def test_confirm_miss_is_a_hard_drop(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "이블린",
            "wake_confirm_text": "",
            "wake_reject_reason": "confirm_miss",
        }

        result = await self.run_probe()

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["confirm_miss"])

    async def test_strict_confirm_rejects_non_exact_wake(self) -> None:
        self.wake_result["wake_match_mode"] = "fuzzy"
        deps = replace(self.deps, should_require_confirm_exact_for_wake=lambda _meta: True)

        result = await self.run_probe(deps=deps, debug_meta={"source": "discord"})

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["unstable_audio"])

    async def test_fuzzy_near_miss_is_promoted(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "이블린아",
            "wake_confirm_text": "",
            "wake_reject_reason": "wake_ignore",
        }
        deps = replace(
            self.deps,
            fuzzy_leading_wake_alias=lambda text: "이블린" if text == "이블린아" else None,
        )

        result = await self.run_probe(deps=deps)

        self.assertIsNotNone(result)
        self.assertTrue(result.wake_detected)
        self.assertEqual(result.wake_match_mode, "fuzzy")
        self.assertEqual(result.wake_alias, "이블린")

    async def test_short_environment_noise_saves_debug_and_stops(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "바람",
            "wake_reject_reason": "wake_ignore",
        }
        deps = replace(self.deps, is_likely_environment_noise=lambda _audio, **_kwargs: True)

        result = await self.run_probe(deps=deps, raw_seconds=2.0)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["env_ignore"])
        debug_payload = next(payload for kind, payload in self.events if kind == "debug")
        self.assertEqual(debug_payload[1]["final_text"], "[ENV IGNORE]")

    async def test_short_filler_stops(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "음",
            "wake_reject_reason": "wake_ignore",
        }
        deps = replace(self.deps, looks_like_brief_filler_text=lambda _text: True)

        result = await self.run_probe(deps=deps, raw_seconds=2.0)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["filler_ignore"])

    async def test_repetitive_noise_stops_even_when_segment_is_long(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "아아아아",
            "wake_reject_reason": "wake_ignore",
        }
        deps = replace(self.deps, looks_like_repetitive_noise_text=lambda _text: True)

        result = await self.run_probe(deps=deps, raw_seconds=8.0)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["noise_text_ignore"])

    async def test_low_signal_stops_without_bottleneck_summary(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "",
            "wake_reject_reason": "wake_ignore",
        }
        deps = replace(self.deps, should_skip_full_stt_after_wake_probe=lambda **_kwargs: True)

        result = await self.run_probe(deps=deps)

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["wake_probe_low_signal"])
        self.assertFalse(any(kind == "bottleneck" for kind, _payload in self.events))

    async def test_non_hard_no_wake_result_continues_to_full_stt(self) -> None:
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": "오늘 날씨",
            "wake_reject_reason": "wake_ignore",
        }

        result = await self.run_probe(raw_seconds=8.0)

        self.assertIsNotNone(result)
        self.assertFalse(result.wake_detected)
        self.assertEqual(result.wake_reject_reason, "wake_ignore")

    async def test_validation_bound_probe_is_redacted_from_observability_logs(self) -> None:
        secret = "VOICE_PRIVACY_SENTINEL_WAKE_PROBE_4d38"
        detect_calls: list[dict[str, Any]] = []
        self.wake_result = {
            "wake_detected": False,
            "wake_probe_text": secret,
            "wake_confirm_text": secret,
            "wake_alias": secret,
            "wake_reject_reason": "confirm_miss",
        }
        deps = replace(
            self.deps,
            detect_wake_word_sync=lambda _audio, **kwargs: (
                detect_calls.append(kwargs) or self.wake_result
            ),
        )

        result = await self.run_probe(
            deps=deps,
            debug_meta={
                "validation_session_id": "validation-private",
                "validation_step_id": "02-listening",
                "validation_attempt": 3,
                "validation_attempt_id": "attempt-private-3",
            }
        )

        self.assertIsNone(result)
        self.assertIs(detect_calls[0]["validation_bound"], True)
        for kind in ("print", "stage", "drop"):
            rendered = repr(
                [payload for event_kind, payload in self.events if event_kind == kind]
            )
            self.assertNotIn(secret, rendered, kind)
            self.assertIn("<validation-text chars=", rendered, kind)

    def test_main_delegates_wake_filtering_to_runtime_module(self) -> None:
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

        self.assertIn("run_wake_probe=run_voice_wake_probe_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("detect_wake_word_sync(", function_source)
        self.assertNotIn("is_likely_environment_noise(", function_source)
        self.assertNotIn("apply_fuzzy_wake_near_miss(", function_source)


if __name__ == "__main__":
    unittest.main()
