from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.observability_metrics import (  # noqa: E402
    ModelCallMetricsStore,
    average,
    average_or_none,
    p95_or_none,
    percentile_p95,
    rate_or_none,
    summarize_question_metrics_payload,
    summarize_turn_path_metrics_payload,
    summarize_voice_p95_metrics,
)


class ObservabilityMetricsTests(unittest.TestCase):
    def test_basic_metric_helpers(self) -> None:
        self.assertEqual(percentile_p95([]), 0.0)
        self.assertEqual(percentile_p95([1, 2, 3, 4, 5]), 5.0)
        self.assertEqual(average([1, 2, 3]), 2.0)
        self.assertIsNone(average_or_none([]))
        self.assertEqual(average_or_none([1, 2]), 1.5)
        self.assertIsNone(p95_or_none([]))
        self.assertEqual(p95_or_none([10, 20]), 20.0)
        self.assertIsNone(rate_or_none(1, 0))
        self.assertEqual(rate_or_none(2, 4), 0.5)

    def test_turn_path_metrics_are_sorted_and_capped(self) -> None:
        metrics = {
            "b": {
                "turn_type": "voice",
                "selected_path": "local",
                "count": 1,
                "total_ms": [10, 20],
                "stt_ms": [3],
                "main_first_ms": [4],
                "tts_first_ms": [5],
                "playback_ms": [6],
            },
            "a": {
                "turn_type": "text",
                "selected_path": "main",
                "count": 3,
                "total_ms": [1],
            },
        }

        rows = summarize_turn_path_metrics_payload(metrics)

        self.assertEqual(rows[0]["turnType"], "text")
        self.assertEqual(rows[1]["turnType"], "voice")
        self.assertEqual(rows[1]["totalMsP95"], 20.0)

    def test_question_metrics_summary_rates(self) -> None:
        summary = summarize_question_metrics_payload(
            {
                "turn_count": 4,
                "added_count": 2,
                "removed_count": 1,
                "cooldown_hit_count": 1,
                "final_question_count": 3,
                "ask_modes": {"clarify": 2},
            }
        )

        self.assertEqual(summary["turnCount"], 4)
        self.assertEqual(summary["questionAddedRate"], 0.5)
        self.assertEqual(summary["questionCooldownHitRate"], 0.25)
        self.assertEqual(summary["askModeDistribution"], {"clarify": 2})

    def test_voice_p95_summary_collects_stage_metrics(self) -> None:
        summary = summarize_voice_p95_metrics(
            {
                "1": {"t_stt_done": 10.0, "route_ready": 20.0, "t_main_first_token": 30.0, "t_tts_first_audio": 40.0},
                "2": {"t_stt_done": 15.0, "route_ready": 25.0, "t_main_first_token": 35.0, "t_tts_first_audio": 45.0},
            },
            search_followup_queued_count=2,
            cancelled_stale_turn_count=1,
        )

        self.assertEqual(summary["stt_ms_p95"], 15.0)
        self.assertEqual(summary["router_ms_p95"], 25.0)
        self.assertEqual(summary["main_first_token_ms_p95"], 35.0)
        self.assertEqual(summary["tts_first_audio_ms_p95"], 45.0)
        self.assertEqual(summary["search_followup_queued_count"], 2)
        self.assertEqual(summary["cancelled_stale_turn_count"], 1)

    def test_model_call_store_records_turn_path_and_summarizes_calls(self) -> None:
        store = ModelCallMetricsStore({}, {}, {"voice_turn_summary"}, Path("missing"), lambda _text: None)

        store.record_turn_path_summary(
            {"turn_type": "voice", "selected_path": "main"},
            {"t_stt_done": 10.0, "t_main_first_token": 20.0, "t_tts_first_audio": 30.0, "t_playback_first_packet": 40.0},
            100.0,
        )
        store.record_model_call(
            model_role="router",
            purpose="route",
            hot_path=True,
            success=True,
            latency_ms=12.0,
        )
        store.record_model_call(
            model_role="main",
            purpose="main_response",
            hot_path=True,
            success=True,
            latency_ms=50.0,
            first_token_ms=15.0,
        )

        turn_paths = store.summarize_turn_paths()
        summary = store.summarize_model_calls()

        self.assertEqual(turn_paths[0]["turnType"], "voice")
        self.assertEqual(summary["turnSummaryCount"], 1)
        self.assertEqual(summary["modelCallCount"], 2)
        self.assertEqual(summary["routerRouteCallCount"], 1)
        self.assertEqual(summary["mainFirstTokenP95Ms"], 15.0)

    def test_model_call_store_replays_jsonl_trace(self) -> None:
        printed: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_dir = Path(tmpdir)
            trace_path = trace_dir / "20260619.jsonl"
            rows = [
                {"event": "voice_turn_summary", "ts": 1.0},
                {"event": "model_call", "ts": 2.0, "model_role": "router", "purpose": "route", "hot_path": True, "success": True, "latency_ms": 22.0},
                {"event": "text_turn_summary", "ts": 3.0},
            ]
            trace_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            store = ModelCallMetricsStore({}, {}, {"voice_turn_summary", "text_turn_summary"}, trace_dir, printed.append)

            result = store.replay_model_calls_from_turn_trace()
            summary = store.summarize_model_calls()

        self.assertEqual(result, {"files": 1, "model_calls": 1, "turn_summaries": 1})
        self.assertEqual(summary["turnSummaryCount"], 1)
        self.assertEqual(summary["routerRouteCallCount"], 1)
        self.assertEqual(printed, [])


if __name__ == "__main__":
    unittest.main()
