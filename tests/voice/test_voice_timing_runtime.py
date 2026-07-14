from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_timing_runtime import (  # noqa: E402
    VoiceTimingRuntimeDeps,
    log_voice_bottleneck_summary_from_runtime,
    log_voice_latency_from_runtime,
    log_voice_stage_from_runtime,
    should_log_voice_timing_from_runtime,
)


class VoiceTimingRuntimeTests(unittest.TestCase):
    def build_deps(
        self,
        *,
        now: float = 11.0,
        threshold: float = 500.0,
        bottleneck_logs: bool = False,
        stages: list[tuple[str | None, str, float]] | None = None,
        path_summaries: list[tuple[dict[str, Any], dict[str, Any], float]] | None = None,
        events: list[tuple[str, dict[str, Any]]] | None = None,
        logs: list[str] | None = None,
    ) -> VoiceTimingRuntimeDeps:
        stages = stages if stages is not None else []
        path_summaries = path_summaries if path_summaries is not None else []
        events = events if events is not None else []
        logs = logs if logs is not None else []

        p95_summary = {
            "stt_ms_p95": 10.0,
            "router_ms_p95": 20.0,
            "main_first_token_ms_p95": 30.0,
            "tts_first_audio_ms_p95": 40.0,
            "search_followup_queued_count": 2,
            "cancelled_stale_turn_count": 1,
        }

        return VoiceTimingRuntimeDeps(
            monotonic=lambda: now,
            voice_timing_log_threshold_ms=threshold,
            voice_bottleneck_logs=bottleneck_logs,
            record_turn_stage=lambda turn_id, key, elapsed_ms: stages.append((turn_id, key, elapsed_ms)),
            record_turn_path_summary=lambda meta, marks, total_ms: path_summaries.append((meta, marks, total_ms)),
            summarize_p95_metrics=lambda: p95_summary,
            build_turn_summary_payload=lambda metrics, **kwargs: {"metrics": metrics, **kwargs},
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            log=lambda message: logs.append(message),
        )

    def test_should_log_voice_timing_uses_threshold(self) -> None:
        deps = self.build_deps(threshold=250.0)

        self.assertFalse(should_log_voice_timing_from_runtime(249.0, deps=deps))
        self.assertTrue(should_log_voice_timing_from_runtime(250.0, deps=deps))

    def test_log_voice_latency_records_aliases_once(self) -> None:
        stages: list[tuple[str | None, str, float]] = []
        metrics = {"started_at": 10.0, "meta": {"turn_id": "turn-1"}}

        log_voice_latency_from_runtime(
            metrics,
            "tts_first_byte_logged",
            "TTS first byte",
            deps=self.build_deps(now=10.25, stages=stages),
        )
        log_voice_latency_from_runtime(
            metrics,
            "tts_first_byte_logged",
            "TTS first byte",
            deps=self.build_deps(now=10.5, stages=stages),
        )

        self.assertTrue(metrics["tts_first_byte_logged"])
        self.assertEqual(metrics["marks"]["tts_first_byte_logged"], 250.0)
        self.assertEqual(metrics["marks"]["t_tts_first_byte"], 250.0)
        self.assertEqual(metrics["marks"]["t_tts_first_audio"], 250.0)
        self.assertEqual(
            stages,
            [
                ("turn-1", "tts_first_byte_logged", 250.0),
                ("turn-1", "t_tts_first_byte", 250.0),
                ("turn-1", "t_tts_first_audio", 250.0),
            ],
        )

    def test_log_voice_stage_records_stage_alias_and_optional_log(self) -> None:
        stages: list[tuple[str | None, str, float]] = []
        logs: list[str] = []
        metrics = {"started_at": 10.0, "meta": {"turn_id": "turn-1"}}

        log_voice_stage_from_runtime(
            metrics,
            "STT done",
            deps=self.build_deps(now=10.6, stages=stages, logs=logs),
            extra="chars=4",
            key="stt_done",
        )

        self.assertAlmostEqual(metrics["marks"]["stt_done"], 600.0)
        self.assertAlmostEqual(metrics["marks"]["t_stt_done"], 600.0)
        self.assertEqual(stages[1][0:2], ("turn-1", "t_stt_done"))
        self.assertAlmostEqual(stages[1][2], 600.0)
        self.assertIn("extra=chars=4", logs[0])

    def test_bottleneck_summary_records_path_and_event(self) -> None:
        path_summaries: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        events: list[tuple[str, dict[str, Any]]] = []
        logs: list[str] = []
        metrics = {
            "started_at": 10.0,
            "marks": {"stt_done": 120.0, "llm_done": 300.0},
            "meta": {"turn_id": "turn-1", "turn_type": "voice", "selected_path": "main"},
        }

        log_voice_bottleneck_summary_from_runtime(
            metrics,
            deps=self.build_deps(
                now=10.7,
                bottleneck_logs=True,
                path_summaries=path_summaries,
                events=events,
                logs=logs,
            ),
            label="voice_done",
            extra="ok",
        )

        self.assertEqual(path_summaries[0][0], metrics["meta"])
        self.assertAlmostEqual(path_summaries[0][2], 700.0)
        self.assertEqual(events[0][0], "turn_summary")
        self.assertEqual(events[0][1]["label"], "voice_done")
        self.assertAlmostEqual(events[0][1]["total_ms"], 700.0)
        self.assertIn("label=voice_done", logs[0])


if __name__ == "__main__":
    unittest.main()
