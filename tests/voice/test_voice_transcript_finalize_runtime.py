from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_transcript_finalize_runtime import (
    VoiceTranscriptFinalizeDeps,
    finalize_voice_transcript_from_runtime,
)


@dataclass(frozen=True)
class FakeTranscriptResult:
    final_text: str
    committed_text: str
    wake_detected: bool = True


class VoiceTranscriptFinalizeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.metrics: dict[str, Any] = {"meta": {}}
        self.transcript = FakeTranscriptResult("수정 문장", "수정 문장")
        self.final_flow = SimpleNamespace(
            corrected_text=" 수정 문장 ",
            committed_text="수정 문장",
            transcript_result=self.transcript,
            speculative_policy={"policy": {"route": "fast"}},
            was_corrected=True,
        )
        self.merge_result: tuple[str, dict[str, Any] | None] = (
            "앞 문장 수정 문장",
            {"delta_sec": 0.42},
        )
        self.build_kwargs: dict[str, Any] = {}
        self.merge_kwargs: dict[str, Any] = {}
        self.deps = self.build_deps()

    def build_deps(self) -> VoiceTranscriptFinalizeDeps:
        def build_final(**kwargs: Any) -> Any:
            self.build_kwargs = kwargs
            return self.final_flow

        def merge(_store: Any, **kwargs: Any) -> tuple[str, dict[str, Any] | None]:
            self.merge_kwargs = kwargs
            self.events.append(("merge", kwargs))
            return self.merge_result

        return VoiceTranscriptFinalizeDeps(
            build_final_transcript_flow=build_final,
            room_state_snapshot=lambda key: {"room": key},
            apply_stt_post_corrections=lambda text, **_kwargs: text,
            clean_text=lambda text: text.strip(),
            set_partial_text=lambda key, value: self.events.append(("partial", (key, value))),
            commit_stable_transcript=lambda *_args, **_kwargs: None,
            build_transcript_result=lambda *_args, **_kwargs: None,
            speculate_from_committed_stt=lambda *_args, **_kwargs: None,
            remember_speculative_policy=lambda key, value: self.events.append(("remember", (key, value))),
            room_last_voice_utterance_for_merge={"room:11": {"text": "앞 문장"}},
            maybe_merge_barge_in_utterance=merge,
            log_voice_stage=lambda _metrics, label, **kwargs: self.events.append(("stage", (label, kwargs))),
            print_fn=lambda *args, **_kwargs: self.events.append(("print", args)),
            merge_window_sec=2.0,
            tts_interrupted_window_sec=3.0,
            incomplete_window_sec=4.0,
            complete_question_window_sec=1.5,
            adaptive_window_enabled=True,
        )

    def finalize(self):
        return finalize_voice_transcript_from_runtime(
            member=SimpleNamespace(id=7, display_name="정훈"),
            text="원문",
            partial_text="부분",
            session_key="voice:11:7",
            room_session_key="room:11",
            turn_id="turn-1",
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="이블린",
            wake_probe="이블린",
            wake_confirm="이블린",
            wake_reject_reason=None,
            duration_sec=2.2,
            metrics=self.metrics,
            deps=self.deps,
        )

    def test_final_flow_arguments_and_speculative_policy_are_preserved(self) -> None:
        result = self.finalize()

        self.assertEqual(result.text, "수정 문장")
        self.assertEqual(result.committed_text, "수정 문장")
        self.assertIs(result.transcript_result, self.transcript)
        self.assertEqual(self.build_kwargs["room_state"], {"room": "room:11"})
        self.assertEqual(self.build_kwargs["speaker_user_id"], 7)
        self.assertTrue(any(kind == "remember" for kind, _payload in self.events))
        printed = [args[0] for kind, args in self.events if kind == "print"]
        self.assertTrue(any(text.startswith("[STT CORRECT]") for text in printed))

    def test_without_interrupt_flags_does_not_attempt_merge(self) -> None:
        self.finalize()

        self.assertFalse(any(kind == "merge" for kind, _payload in self.events))
        self.assertNotIn("barge_in_utterance_merge", self.metrics["meta"])

    def test_interrupted_turn_merges_and_replaces_transcript_text(self) -> None:
        self.metrics["meta"].update(
            {
                "tts_interrupted_by_user_audio": True,
                "tts_interrupted_at": "123.25",
            }
        )

        result = self.finalize()

        self.assertEqual(result.text, "앞 문장 수정 문장")
        self.assertEqual(result.committed_text, "앞 문장 수정 문장")
        self.assertEqual(result.transcript_result.final_text, "앞 문장 수정 문장")
        self.assertEqual(result.transcript_result.committed_text, "앞 문장 수정 문장")
        self.assertEqual(self.merge_kwargs["interrupted_at"], 123.25)
        self.assertEqual(self.merge_kwargs["current_turn_id"], "turn-1")
        self.assertEqual(self.metrics["meta"]["barge_in_utterance_merge"], {"delta_sec": 0.42})

    def test_invalid_interrupted_timestamp_is_forwarded_as_none(self) -> None:
        self.metrics["meta"].update(
            {
                "local_tts_interrupted_by_user_audio": True,
                "tts_interrupted_at": "invalid",
            }
        )

        self.finalize()

        self.assertIsNone(self.merge_kwargs["interrupted_at"])

    def test_merge_without_metadata_keeps_original_transcript(self) -> None:
        self.metrics["meta"]["tts_interrupted_by_user_audio"] = True
        self.merge_result = ("ignored merged text", None)

        result = self.finalize()

        self.assertIs(result.transcript_result, self.transcript)
        self.assertEqual(result.text, "수정 문장")
        self.assertNotIn("barge_in_utterance_merge", self.metrics["meta"])

    def test_no_speculative_policy_does_not_remember(self) -> None:
        self.final_flow.speculative_policy = None

        self.finalize()

        self.assertFalse(any(kind == "remember" for kind, _payload in self.events))

    def test_main_delegates_transcript_finalization_to_runtime_module(self) -> None:
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

        self.assertIn("finalize_transcript=finalize_voice_transcript_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("build_final_transcript_flow(", function_source)
        self.assertNotIn("maybe_merge_barge_in_utterance(", function_source)
        self.assertNotIn("[STT CORRECT]", function_source)


if __name__ == "__main__":
    unittest.main()
