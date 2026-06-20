from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy_observation_state import (  # noqa: E402
    build_autonomy_recent_context_payload,
    build_autonomy_status_payload,
    build_autonomy_summary_payload,
    build_default_autonomy_observation,
    pick_recent_user_text,
)


class AutonomyObservationStateTests(unittest.TestCase):
    def test_pick_recent_user_text_skips_autonomy_marker(self) -> None:
        history = [
            {"role": "user", "content": "첫 요청"},
            {"role": "assistant", "content": "응"},
            {"role": "user", "content": "[autonomy]"},
        ]

        self.assertEqual(pick_recent_user_text(history), "첫 요청")

    def test_autonomy_executor_payload_helpers_format_history_and_status(self) -> None:
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "첫 요청"},
            {"role": "assistant", "content": "첫 응답"},
            {"role": "user", "content": "둘째 요청"},
        ]

        summary = build_autonomy_summary_payload(history, active_sessions=2, inflight_llm_requests=1)
        empty_summary = build_autonomy_summary_payload([], active_sessions=3, inflight_llm_requests=4)
        status = build_autonomy_status_payload(
            connected=True,
            active_sessions=2,
            inflight_llm_requests=1,
            known_followup_channels=5,
        )
        recent = build_autonomy_recent_context_payload(history)

        self.assertEqual(summary["summary"], "첫 요청 | 첫 응답 | 둘째 요청")
        self.assertEqual(empty_summary["summary"], "active_sessions=3 inflight_llm=4")
        self.assertTrue(status["connected"])
        self.assertEqual(status["known_followup_channels"], 5)
        self.assertEqual(recent["count"], 3)
        self.assertIn("둘째 요청", recent["summary"])

    def test_build_observation_computes_autonomy_runtime_flags(self) -> None:
        history = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "이거 확인해줘?"},
            {"role": "assistant", "content": "찾아볼게"},
        ]

        observation = build_default_autonomy_observation(
            connected=True,
            known_followup_channels=2,
            inflight_llm_requests=0,
            active_sessions=1,
            history=history,
            last_autonomy_ping_at=90.0,
            observe_channel_ids=[10, 20],
            command_only_channel_ids=[30],
            observed_channels=[{"id": 10, "name": "main"}],
            quiet_hours=False,
            last_result={"reason": "retry_suppressed"},
            cached_cognitive={"updated_at": 1000.0},
            last_cognitive_refresh_at=50.0,
            router_refresh_inflight=False,
            autonomy_cognitive_stale_sec=100.0,
            autonomy_cognitive_min_interval_sec=10.0,
            autonomy_cognitive_force_refresh_sec=500.0,
            vision_watch={
                "captured_at": 1990.0,
                "analyzed_at": 1980.0,
                "changed": True,
                "scene_fingerprint": "abc",
            },
            vision_watch_interval_sec=20.0,
            local_tts_state={"active": True},
            local_mic_state={"lastInputAgeSec": 3.0},
            queued_proactive_question_available=True,
            answer_promises_search_fn=lambda text: "찾아" in text,
            now_mono=100.0,
            now_time=2000.0,
        )

        self.assertTrue(observation["connected"])
        self.assertEqual(observation["latest_user_text"], "이거 확인해줘?")
        self.assertEqual(observation["last_autonomy_ping_sec"], 10.0)
        self.assertTrue(observation["repeated_blocked_action"])
        self.assertTrue(observation["search_pending"])
        self.assertEqual(observation["unresolved_items"], 1)
        self.assertTrue(observation["cognitive_refresh_needed"])
        self.assertTrue(observation["local_tts_active"])
        self.assertTrue(observation["local_mic_recent"])
        self.assertTrue(observation["vision_change_recent"])
        self.assertEqual(observation["vision_fingerprint"], "abc")
        self.assertTrue(observation["vision_analysis_recent"])
        self.assertTrue(observation["queued_proactive_question_available"])

    def test_unreliable_or_old_vision_does_not_trigger_recent_change(self) -> None:
        observation = build_default_autonomy_observation(
            connected=False,
            known_followup_channels=0,
            inflight_llm_requests=1,
            active_sessions=0,
            history=[],
            last_autonomy_ping_at=0.0,
            observe_channel_ids=[],
            command_only_channel_ids=[],
            observed_channels=[],
            quiet_hours=True,
            last_result={},
            cached_cognitive=None,
            last_cognitive_refresh_at=0.0,
            router_refresh_inflight=True,
            autonomy_cognitive_stale_sec=100.0,
            autonomy_cognitive_min_interval_sec=10.0,
            autonomy_cognitive_force_refresh_sec=500.0,
            vision_watch={"captured_at": 1000.0, "changed": True, "analysis_error": "black frame"},
            vision_watch_interval_sec=20.0,
            local_tts_state={},
            local_mic_state={"lastInputAgeSec": 9.0},
            queued_proactive_question_available=False,
            answer_promises_search_fn=lambda _text: False,
            now_mono=100.0,
            now_time=2000.0,
        )

        self.assertFalse(observation["vision_change_recent"])
        self.assertTrue(observation["vision_unreliable"])
        self.assertFalse(observation["local_mic_recent"])
        self.assertFalse(observation["cognitive_refresh_needed"])


if __name__ == "__main__":
    unittest.main()
