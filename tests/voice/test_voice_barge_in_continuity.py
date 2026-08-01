from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.text import clean_text  # noqa: E402
from evelyn_core.voice_barge_in_continuity import (  # noqa: E402
    VOICE_BARGE_IN_REASON_CODE,
    VoiceBargeInContinuityTracker,
)


class VoiceBargeInContinuityTests(unittest.TestCase):
    def make_tracker(self, events: list[tuple[tuple, dict]] | None = None) -> VoiceBargeInContinuityTracker:
        event_log = events if events is not None else []
        return VoiceBargeInContinuityTracker(
            target_count=2,
            history_limit=2,
            clean_text=clean_text,
            event_logger=lambda *args, **kwargs: event_log.append((args, kwargs)),
        )

    def test_records_success_streak_and_target_reached(self) -> None:
        events: list[tuple[tuple, dict]] = []
        tracker = self.make_tracker(events)

        first_metrics = {
            "meta": {
                "turn_id": "turn-1",
                "session_key": "session-1",
                "guild_id": 1,
                "validation_session_id": "validation-1",
                "validation_step_id": "08-barge-interrupt",
                "validation_attempt_id": "attempt-private-1",
                "validation_transcript_match": True,
            }
        }
        tracker.start_probe(first_metrics, source="discord_voice")
        tracker.mark_probe(first_metrics, success=True, reason="finalize_complete", queued_sentence_count=1)

        first = tracker.snapshot()
        self.assertEqual(first["attemptCount"], 1)
        self.assertEqual(first["successCount"], 1)
        self.assertEqual(first["currentSuccessStreak"], 1)
        self.assertFalse(first["targetReached"])
        self.assertFalse(first_metrics["meta"]["barge_in_probe_active"])

        second_metrics = {"meta": {"turn_id": "turn-2", "session_key": "session-1", "guild_id": 1}}
        tracker.start_probe(second_metrics, source="local_tts")
        tracker.mark_probe(second_metrics, success=True, reason="finalize_complete", queued_sentence_count=2)

        second = tracker.snapshot()
        self.assertEqual(second["attemptCount"], 2)
        self.assertEqual(second["successCount"], 2)
        self.assertEqual(second["currentSuccessStreak"], 2)
        self.assertTrue(second["targetReached"])
        self.assertEqual(second["targetReachedTurnId"], "turn-2")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1][1]["target_count"], 2)
        self.assertEqual(
            events[0][1]["validation_session_id"],
            "validation-1",
        )
        self.assertEqual(
            events[0][1]["validation_step_id"],
            "08-barge-interrupt",
        )
        self.assertEqual(events[0][1]["validation_attempt_id"], "attempt-private-1")

    def test_failure_classification_resets_success_streak(self) -> None:
        tracker = self.make_tracker()
        metrics = {"meta": {"turn_id": "turn-1"}}

        tracker.start_probe(metrics, source="discord_voice")
        tracker.mark_probe(metrics, success=False, reason="reconnect timeout", queued_sentence_count=0)

        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["attemptCount"], 1)
        self.assertEqual(snapshot["failureCount"], 1)
        self.assertEqual(snapshot["currentSuccessStreak"], 0)
        self.assertEqual(snapshot["currentFailureStreak"], 1)
        self.assertEqual(snapshot["lastReasonCode"], VOICE_BARGE_IN_REASON_CODE["TIMEOUT"])

    def test_success_without_queued_sentence_is_false_trigger(self) -> None:
        tracker = self.make_tracker()
        metrics = {"meta": {"turn_id": "turn-1"}}

        tracker.start_probe(metrics, source="discord_voice")
        tracker.mark_probe(metrics, success=True, reason="finalize_empty_answer", queued_sentence_count=0)

        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["successCount"], 1)
        self.assertEqual(snapshot["lastReasonCode"], VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"])
        self.assertEqual(snapshot["lastReasonLabel"], "오탐")

    def test_reset_clears_counters_and_formatting_uses_history_limit(self) -> None:
        tracker = self.make_tracker()
        metrics = {"meta": {"turn_id": "turn-1"}}
        tracker.start_probe(metrics, source="discord_voice")
        tracker.mark_probe(metrics, success=True, reason="finalize_complete", queued_sentence_count=1)

        detail_before = tracker.format_detail_lines(tracker.snapshot(), command_status=lambda value: "yes" if value else "no", now=0)
        self.assertIn("최근 연속기록(2회)", "\n".join(detail_before))
        self.assertIn("event=finish", "\n".join(detail_before))

        tracker.reset(reason="unit")
        reset = tracker.snapshot()
        self.assertEqual(reset["attemptCount"], 0)
        self.assertEqual(reset["successCount"], 0)
        self.assertEqual(reset["lastReason"], "reset:unit")
        self.assertEqual(reset["lastReasonLabel"], "리셋")
        self.assertEqual(reset["recentAttempts"], [])


if __name__ == "__main__":
    unittest.main()
