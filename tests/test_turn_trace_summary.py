import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.turn_trace import TURN_SUMMARY_KEYS, build_turn_summary_payload  # noqa: E402


class TurnTraceSummaryTests(unittest.TestCase):
    def test_summary_payload_keeps_stable_keys_and_nulls(self) -> None:
        payload = build_turn_summary_payload(
            {
                "started_at": 10.0,
                "marks": {
                    "t_main_done": 123.456,
                    "t_tts_first_audio": 88.8,
                },
                "meta": {
                    "turn_id": "turn-1",
                    "source": "voice",
                    "context_pipeline": {
                        "route": "main_direct",
                        "policy": {
                            "needs_main_llm": True,
                            "needs_memory": False,
                            "needs_runtime_state": True,
                            "needs_search": False,
                            "needs_tts": True,
                            "priority": "latency",
                            "response_mode": "short",
                        },
                        "message_count": 4,
                        "sections": ["runtime"],
                        "section_chars": {"runtime": 120},
                    },
                    "minecraft_snapshot_age_ms": 1234.4,
                    "minecraft_snapshot_freshness": "fresh",
                },
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=456.789,
            p95_summary={"stt_ms_p95": 11, "search_followup_queued_count": 2},
        )

        self.assertEqual(tuple(payload.keys()), TURN_SUMMARY_KEYS)
        self.assertEqual(payload["summary_schema"], "turn_summary.v1")
        self.assertEqual(payload["turn_id"], "turn-1")
        self.assertEqual(payload["route"], "main_direct")
        self.assertEqual(payload["needs_main_llm"], True)
        self.assertEqual(payload["needs_memory"], False)
        self.assertEqual(payload["needs_search"], False)
        self.assertEqual(payload["needs_tts"], True)
        self.assertEqual(payload["route_priority"], "latency")
        self.assertEqual(payload["response_mode"], "short")
        self.assertEqual(payload["minecraft_snapshot_age_ms"], 1234.4)
        self.assertEqual(payload["minecraft_snapshot_freshness"], "fresh")
        self.assertEqual(payload["context_tokens_estimate"], 30)
        self.assertEqual(payload["llm_ms"], 123.5)
        self.assertEqual(payload["tts_first_audio_ms"], 88.8)
        self.assertIsNone(payload["playback_completed"])
        self.assertIsNone(payload["error"])

    def test_error_is_serialized_without_raising(self) -> None:
        payload = build_turn_summary_payload(
            {"meta": {"turn_id": "turn-err"}, "marks": {}},
            label="text_turn",
            event_name="text_turn_summary",
            total_ms=None,
            error_layer="text_turn",
            error=ValueError("bad"),
        )

        self.assertEqual(payload["error_layer"], "text_turn")
        self.assertIn("ValueError", payload["error"])
        self.assertIsNone(payload["total_ms"])

    def test_playback_completed_is_explicit_in_summary(self) -> None:
        payload = build_turn_summary_payload(
            {
                "marks": {"t_playback_first_packet": 42.0},
                "meta": {
                    "turn_id": "turn-playback",
                    "playback_cancelled": False,
                },
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=100.0,
        )

        self.assertEqual(payload["playback_started"], True)
        self.assertEqual(payload["playback_completed"], True)
        self.assertEqual(payload["playback_cancelled"], False)

    def test_explicit_playback_completed_false_is_preserved(self) -> None:
        payload = build_turn_summary_payload(
            {
                "marks": {"t_playback_first_packet": 42.0},
                "meta": {
                    "turn_id": "turn-playback",
                    "playback_completed": False,
                    "playback_cancelled": True,
                },
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=100.0,
        )

        self.assertEqual(payload["playback_started"], True)
        self.assertEqual(payload["playback_completed"], False)
        self.assertEqual(payload["playback_cancelled"], True)


if __name__ == "__main__":
    unittest.main()
