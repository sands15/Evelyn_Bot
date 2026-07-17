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

from evelyn_core.voice_session_gate_runtime import (
    VoiceSessionGateDeps,
    run_voice_session_gate_from_runtime,
)


class VoiceSessionGateRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.metrics: dict[str, Any] = {"meta": {}}
        self.short_candidate = False
        self.ignore_short = False
        self.wake_decision = SimpleNamespace(accepted=True, wake_alias=None, reject_reason=None)
        self.transcript = SimpleNamespace(
            final_text="이블린 오늘 날씨 알려줘",
            committed_text="이블린 오늘 날씨 알려줘",
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="이블린",
            probe_text="이블린",
            confirm_text="이블린",
            reject_reason=None,
        )
        self.deps = self.build_deps()

    def build_deps(self) -> VoiceSessionGateDeps:
        return VoiceSessionGateDeps(
            is_short_followup_candidate=lambda *_args, **_kwargs: self.short_candidate,
            should_ignore_short_transcription=lambda *_args, **_kwargs: self.ignore_short,
            decide_final_wake_veto=lambda **kwargs: self.events.append(("veto", kwargs)) or self.wake_decision,
            extract_leading_wake_alias=lambda text: "이블린" if text.startswith("이블린") else None,
            register_drop_reason=lambda _metrics, reason, **kwargs: self.events.append(("drop", (reason, kwargs))),
            save_voice_debug_audio=lambda *args, **kwargs: self.events.append(("debug", (args, kwargs))),
            log_voice_stage=lambda _metrics, label, **kwargs: self.events.append(("stage", (label, kwargs))),
            log_voice_bottleneck_summary=lambda _metrics, **kwargs: self.events.append(("bottleneck", kwargs)),
            print_fn=lambda *args, **_kwargs: self.events.append(("print", args)),
        )

    def run_gate(self):
        return run_voice_session_gate_from_runtime(
            member=SimpleNamespace(id=7, display_name="정훈"),
            transcript_result=self.transcript,
            text=self.transcript.final_text,
            pcm_bytes=b"pcm",
            audio16k=b"audio",
            debug_meta={"source": "local_mic"},
            stt_meta={"model": "fake"},
            guild_id=11,
            speaker_name="정훈",
            session_key="voice:11:7",
            room_session_key="room:11",
            owner_user_id=7,
            owner_followup_active=False,
            wake_probe="이블린",
            wake_confirm="이블린",
            wake_detected=True,
            wake_alias="기존별칭",
            metrics=self.metrics,
            deps=self.deps,
        )

    def debug_stage_labels(self) -> list[str]:
        return [payload[1]["stage_label"] for kind, payload in self.events if kind == "debug"]

    def test_accepted_transcript_saves_final_debug_and_passes(self) -> None:
        result = self.run_gate()

        self.assertIsNotNone(result)
        self.assertEqual(result.wake_alias, "기존별칭")
        self.assertFalse(result.short_followup_candidate)
        self.assertEqual(self.debug_stage_labels(), ["final"])
        self.assertTrue(any(kind == "veto" for kind, _payload in self.events))

    def test_short_followup_candidate_is_marked_but_continues(self) -> None:
        self.short_candidate = True
        self.ignore_short = True

        result = self.run_gate()

        self.assertIsNotNone(result)
        self.assertTrue(result.short_followup_candidate)
        self.assertTrue(self.metrics["meta"]["short_followup_candidate"])
        self.assertEqual(self.debug_stage_labels(), ["drop", "final"])
        debug_payload = next(payload for kind, payload in self.events if kind == "debug")
        self.assertTrue(debug_payload[1]["final_text"].startswith("[SHORT FOLLOWUP CANDIDATE]"))

    def test_short_noise_stops_before_final_wake_veto(self) -> None:
        self.ignore_short = True

        result = self.run_gate()

        self.assertIsNone(result)
        self.assertEqual(self.debug_stage_labels(), ["drop"])
        self.assertFalse(any(kind == "veto" for kind, _payload in self.events))
        stages = [payload[0] for kind, payload in self.events if kind == "stage"]
        self.assertIn("짧은 STT 무시", stages)

    def test_full_text_veto_registers_drop_and_stops(self) -> None:
        self.wake_decision = SimpleNamespace(
            accepted=False,
            wake_alias=None,
            reject_reason="full_text_veto",
        )

        result = self.run_gate()

        self.assertIsNone(result)
        drops = [payload[0] for kind, payload in self.events if kind == "drop"]
        self.assertEqual(drops, ["full_text_veto"])
        self.assertEqual(self.debug_stage_labels(), ["drop"])
        self.assertTrue(any(kind == "bottleneck" for kind, _payload in self.events))

    def test_veto_reason_defaults_to_full_text_veto_in_stage_log(self) -> None:
        self.wake_decision = SimpleNamespace(accepted=False, wake_alias=None, reject_reason=None)

        self.run_gate()

        stage_payload = next(payload for kind, payload in self.events if kind == "stage")
        self.assertIn("wake_reject_reason=full_text_veto", stage_payload[1]["extra"])

    def test_final_wake_alias_replaces_probe_alias(self) -> None:
        self.wake_decision = SimpleNamespace(accepted=True, wake_alias="이블린", reject_reason=None)

        result = self.run_gate()

        self.assertIsNotNone(result)
        self.assertEqual(result.wake_alias, "이블린")

    def test_main_delegates_short_and_final_wake_gate_to_runtime_module(self) -> None:
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

        self.assertIn("run_session_gate=run_voice_session_gate_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("should_ignore_short_transcription(", function_source)
        self.assertNotIn("decide_final_wake_veto(", function_source)
        self.assertNotIn("[SHORT FOLLOWUP CANDIDATE]", function_source)


if __name__ == "__main__":
    unittest.main()
