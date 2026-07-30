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
    mark_turn_stage_from_runtime,
    new_turn_metrics_from_runtime,
    p95_or_none,
    percentile_p95,
    rate_or_none,
    record_context_pipeline_benchmark_from_runtime,
    record_model_call_trace_from_runtime,
    register_drop_reason_from_runtime,
    record_turn_stage_metric,
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

    def test_record_turn_stage_metric_ignores_missing_ids_and_records_float(self) -> None:
        metrics: dict[str, dict[str, float]] = {}

        record_turn_stage_metric(metrics, None, "stage", 1.0)
        record_turn_stage_metric(metrics, "turn-1", "", 1.0)
        record_turn_stage_metric(metrics, "turn-1", "stage", 12)

        self.assertEqual(metrics, {"turn-1": {"stage": 12.0}})

    def test_mark_turn_stage_records_mark_metric_and_event_payload(self) -> None:
        metrics = {
            "started_at": 10.0,
            "meta": {
                "turn_id": "turn-1",
                "segment_id": 2,
                "chunk_index": 3,
                "session_key": "session-1",
                "room_session_key": "room-1",
                "guild_id": 7,
                "user_id": 8,
                "owner_user_id": 9,
                "source": "voice",
            },
        }
        recorded_stages: list[tuple[str | None, str, float]] = []
        events: list[tuple[str, dict]] = []

        mark_turn_stage_from_runtime(
            metrics,
            "tts_first_byte",
            monotonic=lambda: 10.1234,
            record_turn_stage=lambda turn_id, stage, elapsed_ms: recorded_stages.append((turn_id, stage, elapsed_ms)),
            merge_log_event_payload=lambda *, explicit, extra=None: {**explicit, **(extra or {})},
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            event_name="tts_first_byte",
            chunk_index=99,
            detail="ok",
        )

        self.assertAlmostEqual(metrics["marks"]["tts_first_byte"], 123.4)
        self.assertEqual(recorded_stages[0][0], "turn-1")
        self.assertEqual(recorded_stages[0][1], "tts_first_byte")
        self.assertAlmostEqual(recorded_stages[0][2], 123.4)
        self.assertEqual(events[0][0], "tts_first_byte")
        self.assertEqual(events[0][1]["turn_id"], "turn-1")
        self.assertEqual(events[0][1]["session_key"], "session-1")
        self.assertEqual(events[0][1]["room_session_key"], "room-1")
        self.assertEqual(events[0][1]["chunk_index"], 99)
        self.assertEqual(events[0][1]["detail"], "ok")
        self.assertAlmostEqual(events[0][1]["elapsed_ms"], 123.4)

    def test_new_turn_metrics_records_ingress_event(self) -> None:
        events: list[tuple[str, dict]] = []

        metrics = new_turn_metrics_from_runtime(
            source="voice",
            monotonic=lambda: 123.0,
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            session_key="session-1",
            room_session_key="room-1",
            guild_id=7,
            user_id=8,
            owner_user_id=9,
            topic_id="topic",
            turn_id="turn-1",
            segment_id=3,
            chunk_index=4,
        )

        self.assertEqual(metrics["started_at"], 123.0)
        self.assertEqual(metrics["marks"], {"t_ingress": 0.0})
        self.assertEqual(metrics["meta"]["source"], "voice")
        self.assertEqual(metrics["meta"]["turn_id"], "turn-1")
        self.assertEqual(events[0][0], "turn_ingress")
        self.assertEqual(events[0][1]["room_session_key"], "room-1")
        self.assertEqual(events[0][1]["chunk_index"], 4)

    def test_register_drop_reason_records_contract_and_turn_drop_event(self) -> None:
        metrics = {
            "meta": {
                "turn_id": "turn-1",
                "segment_id": 2,
                "session_key": "session-meta",
                "room_session_key": "room-meta",
                "owner_user_id": 7,
                "source": "voice",
                "voice_queue_wait_ms": 12.5,
                "topic_id": "topic",
                "reply_gate_blocked_by": "cooldown",
                "voice_segment_contract": {"id": "segment"},
            }
        }
        events: list[tuple[str, dict]] = []

        register_drop_reason_from_runtime(
            metrics,
            "too_short",
            build_rejected_voice_turn=lambda **kwargs: {"rejected": kwargs},
            merge_log_event_payload=lambda *, explicit, extra=None: {**(extra or {}), **explicit},
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            session_key="session-extra",
            owner_user_id=9,
            text="detail",
        )

        meta = metrics["meta"]
        self.assertEqual(meta["drop_reason"], "too_short")
        rejected = meta["rejected_turn_contract"]["rejected"]
        self.assertEqual(rejected["segment"], {"id": "segment"})
        self.assertEqual(rejected["ingress_source"], "voice")
        self.assertEqual(rejected["queue_wait_ms"], 12.5)
        self.assertEqual(rejected["owner_user_id"], 9)
        self.assertEqual(rejected["detail_text"], "detail")
        self.assertEqual(events[0][0], "turn_drop")
        self.assertEqual(events[0][1]["session_key"], "session-extra")
        self.assertEqual(events[0][1]["room_session_key"], "room-meta")
        self.assertEqual(events[0][1]["reason"], "too_short")

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

    def test_record_model_call_trace_records_metric_and_trace_payload(self) -> None:
        metrics = {"meta": {"turn_id": "turn-meta", "session_key": "session-meta", "source": "voice", "guild_id": 7}}
        recorded_metrics: list[dict] = []
        events: list[tuple[str, dict]] = []

        record_model_call_trace_from_runtime(
            model_role=" main ",
            purpose=" answer ",
            hot_path=True,
            started_at=10.0,
            success=False,
            monotonic=lambda: 10.1234,
            record_model_call_metric=lambda **kwargs: recorded_metrics.append(kwargs),
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            metrics=metrics,
            first_token_ms=12.345,
            error=ValueError("bad"),
            model_name=" model ",
            endpoint=" http://llm ",
        )

        self.assertEqual(recorded_metrics[0]["model_role"], " main ")
        self.assertAlmostEqual(recorded_metrics[0]["latency_ms"], 123.4)
        self.assertEqual(events[0][0], "model_call")
        payload = events[0][1]
        self.assertEqual(payload["model_role"], "main")
        self.assertEqual(payload["purpose"], "answer")
        self.assertFalse(payload["success"])
        self.assertEqual(payload["latency_ms"], 123.4)
        self.assertEqual(payload["first_token_ms"], 12.3)
        self.assertEqual(payload["model_name"], "model")
        self.assertEqual(payload["endpoint"], "http://llm")
        self.assertEqual(payload["turn_id"], "turn-meta")
        self.assertEqual(payload["session_key"], "session-meta")
        self.assertEqual(payload["source"], "voice")
        self.assertEqual(payload["guild_id"], 7)
        self.assertIn("ValueError", payload["error"])

    def test_record_context_pipeline_benchmark_writes_jsonl_when_context_meta_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            log_path = Path("logs/context.jsonl")
            record_context_pipeline_benchmark_from_runtime(
                metrics={
                    "meta": {
                        "turn_id": "turn-1",
                        "route": "fallback",
                        "context_pipeline": {
                            "route": "main",
                            "policy": "full",
                            "sections": ["memory", "vision"],
                            "section_chars": {"memory": 10},
                            "minecraft_context": True,
                            "vision_requested": True,
                            "vision_evidence_available": True,
                            "vision_evidence_state": "observed",
                            "vision_scene_available": True,
                            "vision_ocr_available": False,
                            "vision_actionable": False,
                        },
                    },
                    "marks": {"route_ready": 12.0, "vision_ready": 34.0, "ignored": 99.0},
                },
                user_text=" hello ",
                answer=" answer ",
                source=" voice ",
                guild_id=7,
                session_key="session-1",
                now=lambda: 123.4567,
                benchmark_log_path=log_path,
                project_root=project_root,
                log=lambda _message: None,
            )

            rows = (project_root / log_path).read_text(encoding="utf-8").splitlines()

        record = json.loads(rows[0])
        self.assertEqual(record["ts"], 123.457)
        self.assertEqual(record["source"], "voice")
        self.assertEqual(record["turn_id"], "turn-1")
        self.assertEqual(record["route"], "main")
        self.assertTrue(record["minecraft_context"])
        self.assertTrue(record["vision_context"])
        self.assertTrue(record["vision_requested"])
        self.assertTrue(record["vision_evidence_available"])
        self.assertEqual(record["vision_evidence_state"], "observed")
        self.assertTrue(record["vision_scene_available"])
        self.assertFalse(record["vision_ocr_available"])
        self.assertFalse(record["vision_actionable"])
        self.assertEqual(record["marks"], {"route_ready": 12.0, "vision_ready": 34.0})
        self.assertEqual(record["user_text_len"], 5)
        self.assertEqual(record["answer_len"], 6)

    def test_record_context_pipeline_benchmark_skips_without_context_meta(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            record_context_pipeline_benchmark_from_runtime(
                metrics={"meta": {}},
                user_text="hello",
                answer="answer",
                source="voice",
                guild_id=None,
                session_key=None,
                now=lambda: 1.0,
                benchmark_log_path=Path("logs/context.jsonl"),
                project_root=project_root,
                log=lambda _message: None,
            )

            self.assertFalse((project_root / "logs" / "context.jsonl").exists())

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
