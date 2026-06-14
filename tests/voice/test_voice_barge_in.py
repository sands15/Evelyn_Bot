from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.text import clean_text  # noqa: E402
from evelyn_core.voice_barge_in import (  # noqa: E402
    VoiceUtteranceMergeRecord,
    maybe_merge_barge_in_utterance,
    remember_voice_utterance_for_merge,
    resolve_barge_in_merge_window_sec,
)


class VoiceBargeInMergeTests(unittest.TestCase):
    def test_merges_previous_utterance_when_interrupt_is_within_window(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="first user line",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="second user line",
            current_turn_id="turn-b",
            interrupted_at=10.399,
            merge_window_sec=0.4,
            adaptive_window_enabled=False,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "first user line second user line")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["previous_turn_id"], "turn-a")
        self.assertEqual(records["room-1"].consumed_by_turn_id, "turn-b")

    def test_does_not_merge_when_interrupt_is_outside_fixed_window(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="first user line",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="second user line",
            current_turn_id="turn-b",
            interrupted_at=10.401,
            merge_window_sec=0.4,
            adaptive_window_enabled=False,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "second user line")
        self.assertIsNone(meta)

    def test_requires_same_room_and_user(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="first user line",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-2",
            session_key="session-1",
            user_id=42,
            current_text="second user line",
            current_turn_id="turn-b",
            interrupted_at=10.2,
            merge_window_sec=0.4,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "second user line")
        self.assertIsNone(meta)

    def test_requires_same_session(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="first user line",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-2",
            user_id=42,
            current_text="second user line",
            current_turn_id="turn-b",
            interrupted_at=10.2,
            merge_window_sec=0.4,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "second user line")
        self.assertIsNone(meta)

    def test_consumes_previous_utterance_only_once(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="first user line",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="second user line",
            current_turn_id="turn-b",
            interrupted_at=10.2,
            merge_window_sec=0.4,
            clean_text=clean_text,
        )
        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="third user line",
            current_turn_id="turn-c",
            interrupted_at=10.3,
            merge_window_sec=0.4,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "third user line")
        self.assertIsNone(meta)

    def test_adaptive_tts_interrupted_window_extends_default_window(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="please update the configuration value",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="to the faster setting",
            current_turn_id="turn-b",
            interrupted_at=10.8,
            merge_window_sec=0.4,
            tts_interrupted_window_sec=0.9,
            incomplete_window_sec=1.2,
            complete_question_window_sec=0.5,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "please update the configuration value to the faster setting")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["window_reason"], "tts_interrupted")
        self.assertEqual(meta["window_sec"], 0.9)

    def test_adaptive_incomplete_window_extends_for_short_fragment(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="but",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="make it adaptive",
            current_turn_id="turn-b",
            interrupted_at=11.1,
            merge_window_sec=0.4,
            tts_interrupted_window_sec=0.9,
            incomplete_window_sec=1.2,
            complete_question_window_sec=0.5,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "but make it adaptive")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["window_reason"], "incomplete_or_short")
        self.assertEqual(meta["window_sec"], 1.2)

    def test_adaptive_complete_question_keeps_narrow_window(self) -> None:
        records: dict[str, VoiceUtteranceMergeRecord] = {}
        remember_voice_utterance_for_merge(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            text="what time is it?",
            accepted_at=10.0,
            turn_id="turn-a",
            segment_id=1,
            clean_text=clean_text,
        )

        merged, meta = maybe_merge_barge_in_utterance(
            records,
            room_session_key="room-1",
            session_key="session-1",
            user_id=42,
            current_text="actually tomorrow",
            current_turn_id="turn-b",
            interrupted_at=10.6,
            merge_window_sec=0.4,
            tts_interrupted_window_sec=0.9,
            incomplete_window_sec=1.2,
            complete_question_window_sec=0.5,
            clean_text=clean_text,
        )

        self.assertEqual(merged, "actually tomorrow")
        self.assertIsNone(meta)

    def test_resolves_complete_question_before_short_fragment(self) -> None:
        window, reason = resolve_barge_in_merge_window_sec(
            "what time is it?",
            base_window_sec=0.4,
            tts_interrupted_window_sec=0.9,
            incomplete_window_sec=1.2,
            complete_question_window_sec=0.5,
            clean_text=clean_text,
        )

        self.assertEqual(window, 0.5)
        self.assertEqual(reason, "complete_question")


if __name__ == "__main__":
    unittest.main()
