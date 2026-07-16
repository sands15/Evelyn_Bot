from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_stt_execution_runtime import (
    VoiceSttExecutionDeps,
    run_voice_stt_execution_from_runtime,
)


class VoiceSttExecutionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.metrics: dict[str, Any] = {"meta": {}}
        self.audio = np.ones(32, dtype=np.float32)
        self.partial_result = SimpleNamespace(
            partial_text="부분",
            committed_text="부분 확정",
            speculative_policy={"policy": {"route": "fast"}},
        )
        self.full_result = SimpleNamespace(text="최종 문장", stt_meta={"model": "fake"})
        self.partial_error: Exception | None = None
        self.full_error: Exception | None = None
        self.full_kwargs: dict[str, Any] = {}
        self.deps = self.build_deps()

    def build_deps(self) -> VoiceSttExecutionDeps:
        async def run_partial(_audio: Any, **kwargs: Any) -> Any:
            self.events.append(("partial", kwargs))
            if self.partial_error is not None:
                raise self.partial_error
            return self.partial_result

        async def run_full(_audio: Any, **kwargs: Any) -> Any:
            self.full_kwargs = kwargs
            self.events.append(("full", kwargs))
            if self.full_error is not None:
                raise self.full_error
            return self.full_result

        return VoiceSttExecutionDeps(
            run_partial_stt_flow=run_partial,
            run_full_stt_with_optional_rescore=run_full,
            build_partial_stt_window=lambda *_args, **_kwargs: None,
            get_partial_transcript=lambda *_args, **_kwargs: None,
            read_committed_text=lambda key: f"committed:{key}",
            run_blocking_stt_task=lambda *_args, **_kwargs: None,
            speculate_from_committed_stt=lambda *_args, **_kwargs: None,
            room_state_snapshot=lambda key: {"room": key},
            clean_text=lambda text: text.strip(),
            remember_speculative_policy=lambda key, value: self.events.append(("remember", (key, value))),
            transcribe_audio=lambda *_args, **_kwargs: None,
            choose_full_stt_candidate=lambda primary, rescore, **kwargs: self.events.append(
                ("choose", (primary, rescore, kwargs))
            )
            or "chosen",
            log_voice_stage=lambda _metrics, label, **kwargs: self.events.append(("stage", (label, kwargs))),
            mark_turn_stage=lambda _metrics, stage, **kwargs: self.events.append(("mark", (stage, kwargs))),
            save_voice_debug_audio=lambda *args, **kwargs: self.events.append(("debug", (args, kwargs))),
            print_fn=lambda *args, **_kwargs: self.events.append(("print", args)),
            full_stt_timeout_sec=12.0,
            voice_stt_max_new_tokens=96,
            rescore_enabled=True,
            rescore_extra_tokens=24,
            rescore_min_audio_sec=1.5,
            rescore_min_text_len=4,
            rescore_timeout_sec=6.0,
        )

    async def run_execution(self, *, deps: VoiceSttExecutionDeps | None = None):
        return await run_voice_stt_execution_from_runtime(
            member=SimpleNamespace(id=7, display_name="정훈"),
            guild_id=11,
            speaker_name="정훈",
            pcm_bytes=b"pcm",
            debug_meta={"source": "local_mic"},
            session_key="voice:11:7",
            room_session_key="room:11",
            audio16k=self.audio,
            stt_sampling_rate=16000,
            duration_sec=2.25,
            wake_probe="이블린",
            wake_detected=True,
            metrics=self.metrics,
            deps=deps or self.deps,
        )

    async def test_success_returns_partial_and_full_results(self) -> None:
        result = await self.run_execution()

        self.assertIsNotNone(result)
        self.assertEqual(result.text, "최종 문장")
        self.assertEqual(result.stt_meta, {"model": "fake"})
        self.assertEqual(result.partial_text, "부분")
        self.assertEqual(result.committed_partial_text, "부분 확정")
        self.assertEqual(self.metrics["meta"]["partial_stt_text"], "부분")
        self.assertEqual(self.metrics["meta"]["speculative_policy"], {"route": "fast"})
        self.assertTrue(any(kind == "remember" for kind, _payload in self.events))

    async def test_partial_failure_is_non_fatal(self) -> None:
        self.partial_error = RuntimeError("partial failed")

        result = await self.run_execution()

        self.assertIsNotNone(result)
        self.assertEqual(result.partial_text, "")
        printed = [args[0] for kind, args in self.events if kind == "print"]
        self.assertTrue(any(text.startswith("[STT PARTIAL]") for text in printed))

    async def test_full_failure_logs_and_stops(self) -> None:
        self.full_error = RuntimeError("full failed")

        result = await self.run_execution()

        self.assertIsNone(result)
        stages = [payload[0] for kind, payload in self.events if kind == "stage"]
        self.assertIn("본문 STT 실패", stages)

    async def test_empty_full_result_saves_debug_and_stops(self) -> None:
        self.full_result = SimpleNamespace(text="", stt_meta={"empty": True})

        result = await self.run_execution()

        self.assertIsNone(result)
        debug_payload = next(payload for kind, payload in self.events if kind == "debug")
        self.assertEqual(debug_payload[1]["final_text"], "[EMPTY STT]")
        self.assertEqual(debug_payload[1]["stt_meta"], {"empty": True})

    async def test_runtime_forwards_timeout_rescore_and_wake_candidate_context(self) -> None:
        await self.run_execution()

        partial_kwargs = next(payload for kind, payload in self.events if kind == "partial")
        self.assertEqual(partial_kwargs["timeout_sec"], 6.0)
        self.assertEqual(partial_kwargs["room_state"], {"room": "room:11"})
        self.assertEqual(self.full_kwargs["max_new_tokens"], 96)
        self.assertTrue(self.full_kwargs["rescore_enabled"])
        self.assertEqual(self.full_kwargs["rescore_timeout_sec"], 6.0)
        self.assertEqual(self.full_kwargs["choose_candidate"]("primary", "rescore"), "chosen")
        choose_payload = next(payload for kind, payload in self.events if kind == "choose")
        self.assertEqual(choose_payload[2]["wake_probe"], "이블린")

    async def test_speculative_policy_none_does_not_write_policy_meta(self) -> None:
        self.partial_result = SimpleNamespace(
            partial_text="부분",
            committed_text="부분",
            speculative_policy=None,
        )

        result = await self.run_execution()

        self.assertIsNotNone(result)
        self.assertNotIn("speculative_policy", self.metrics["meta"])
        self.assertFalse(any(kind == "remember" for kind, _payload in self.events))

    def test_main_delegates_partial_and_full_stt_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = composition_source.index("    async def process_member_audio_impl(")
        function_source = composition_source[start:]
        builder_source = source[
            source.index("def build_voice_member_audio_pipeline_deps(") : source.index("voice_io_composition =", source.index("def build_voice_member_audio_pipeline_deps("))
        ]

        self.assertIn("run_stt_execution=run_voice_stt_execution_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("run_partial_stt_flow(", function_source)
        self.assertNotIn("run_full_stt_with_optional_rescore(", function_source)
        self.assertNotIn("[EMPTY STT]", function_source)


if __name__ == "__main__":
    unittest.main()
