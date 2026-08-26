from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_asr_stream import AsrStreamSession, AsrStreamStatus  # noqa: E402


class VoiceAsrStreamTests(unittest.TestCase):
    def test_first_partial_is_entirely_volatile_and_non_authoritative(self) -> None:
        session = AsrStreamSession()

        result = session.apply(revision=1, text=" 이블린   오늘 날 ", is_final=False)

        self.assertEqual(result.stable_prefix, "")
        self.assertEqual(result.volatile_suffix, "이블린 오늘 날")
        self.assertFalse(result.authoritative)

    def test_consecutive_revisions_commit_only_before_last_korean_word(self) -> None:
        session = AsrStreamSession()
        session.apply(revision=1, text="이블린 오늘 날", is_final=False)

        second = session.apply(revision=2, text="이블린 오늘 날씨", is_final=False)
        third = session.apply(revision=3, text="이블린 오늘 날씨 알려줘", is_final=False)
        fourth = session.apply(revision=4, text="이블린 오늘 날씨 알려줘", is_final=False)

        self.assertEqual(second.stable_prefix, "이블린 오늘 ")
        self.assertEqual(second.volatile_suffix, "날씨")
        self.assertEqual(third.stable_prefix, "이블린 오늘 ")
        self.assertEqual(fourth.stable_prefix, "이블린 오늘 날씨 ")

    def test_unspaced_korean_uses_character_holdback(self) -> None:
        session = AsrStreamSession(holdback_chars=2)
        session.apply(revision=1, text="이블린날씨", is_final=False)

        result = session.apply(revision=2, text="이블린날씨알려줘", is_final=False)

        self.assertEqual(result.stable_prefix, "이블린")
        self.assertEqual(result.volatile_suffix, "날씨알려줘")

    def test_stable_prefix_never_shrinks_when_a_partial_revises_it(self) -> None:
        session = AsrStreamSession()
        session.apply(revision=1, text="이블린 오늘 날", is_final=False)
        session.apply(revision=2, text="이블린 오늘 날씨", is_final=False)

        result = session.apply(revision=3, text="이블린 내일 날씨", is_final=False)

        self.assertEqual(result.stable_prefix, "이블린 오늘 ")
        self.assertEqual(result.volatile_suffix, "이블린 내일 날씨")
        self.assertTrue(result.conflicts_with_stable_prefix)
        self.assertFalse(result.authoritative)

    def test_final_conflict_is_reported_and_not_authoritative(self) -> None:
        session = AsrStreamSession()
        session.apply(revision=1, text="이블린 오늘 날", is_final=False)
        session.apply(revision=2, text="이블린 오늘 날씨", is_final=False)

        result = session.apply(revision=3, text="이블린 내일 날씨", is_final=True)

        self.assertTrue(result.is_final)
        self.assertTrue(result.conflicts_with_stable_prefix)
        self.assertFalse(result.authoritative)
        self.assertEqual(session.status, AsrStreamStatus.FINISHED)
        with self.assertRaisesRegex(RuntimeError, "asr_stream_not_active"):
            session.apply(revision=4, text="stale", is_final=False)

    def test_consistent_non_empty_final_is_authoritative(self) -> None:
        session = AsrStreamSession()
        session.apply(revision=1, text="이블린 오늘 날", is_final=False)
        session.apply(revision=2, text="이블린 오늘 날씨", is_final=False)

        result = session.apply(revision=3, text="이블린 오늘 날씨 알려줘", is_final=True)

        self.assertTrue(result.authoritative)
        self.assertEqual(result.stable_prefix + result.volatile_suffix, result.text)

    def test_revisions_are_monotonic_and_cancel_is_terminal(self) -> None:
        session = AsrStreamSession()
        session.apply(revision=1, text="partial", is_final=False)
        with self.assertRaisesRegex(ValueError, "asr_revision_not_monotonic"):
            session.apply(revision=1, text="duplicate", is_final=False)
        with self.assertRaisesRegex(ValueError, "asr_revision_not_monotonic"):
            session.apply(revision=3, text="skipped", is_final=False)

        session.cancel()
        self.assertEqual(session.status, AsrStreamStatus.CANCELLED)
        with self.assertRaisesRegex(RuntimeError, "asr_stream_not_active"):
            session.apply(revision=2, text="late", is_final=True)


if __name__ == "__main__":
    unittest.main()
