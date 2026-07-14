from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

from evelyn_core.text import is_similar

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.stt_text_runtime import (  # noqa: E402
    SttTextRuntimeDeps,
    build_stt_text_runtime_deps,
    build_partial_stt_window_from_runtime,
    choose_full_stt_candidate_from_runtime,
    commit_stable_transcript_from_runtime,
    detect_wake_word_sync_from_runtime,
    get_partial_transcript_from_runtime,
    score_stt_candidate_from_runtime,
)


def _clean_text(value: str) -> str:
    return value.strip()


def _contains_wake_word(value: str) -> bool:
    return "wake" in value.lower()


def _normalize_voice_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if not ch.isspace())


def _looks_like_brief_filler_text(value: str) -> bool:
    return value.strip().lower() in {"uh", "um", "음"}


def _looks_like_repetitive_noise_text(value: str) -> bool:
    return "ㅋㅋㅋ" in value


class SttTextRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_partial_stt_text: dict[str, str] = {}
        self.session_committed_stt_text: dict[str, str] = {}
        self.partial_stt_cache: dict[str, dict[str, object]] = {}
        self.deps: SttTextRuntimeDeps = build_stt_text_runtime_deps(
            clean_text=_clean_text,
            normalize_voice_text=_normalize_voice_text,
            contains_wake_word=_contains_wake_word,
            looks_like_brief_filler_text=_looks_like_brief_filler_text,
            looks_like_repetitive_noise_text=_looks_like_repetitive_noise_text,
            is_similar=is_similar,
            session_partial_stt_text=self.session_partial_stt_text,
            session_committed_stt_text=self.session_committed_stt_text,
            partial_stt_cache=self.partial_stt_cache,
        )

    def test_build_partial_stt_window_keeps_recent_tail(self) -> None:
        audio = np.arange(1_000, dtype=np.float32)
        result = build_partial_stt_window_from_runtime(audio16k=audio, sampling_rate=100)
        self.assertEqual(len(result), 150)
        self.assertEqual(result[0], 850.0)

    def test_get_partial_transcript_uses_cache(self) -> None:
        calls = 0

        def transcribe(audio16k: np.ndarray, **_kwargs: object) -> str:
            nonlocal calls
            calls += 1
            return "partial text"

        audio = np.ones(1_000, dtype=np.float32)
        first, committed_first = get_partial_transcript_from_runtime(
            "session-1",
            audio,
            sampling_rate=100,
            max_new_tokens=64,
            transcribe_audio16k_sync=transcribe,
            deps=self.deps,
        )
        second, committed_second = get_partial_transcript_from_runtime(
            "session-1",
            audio,
            sampling_rate=100,
            max_new_tokens=64,
            transcribe_audio16k_sync=transcribe,
            deps=self.deps,
        )
        self.assertEqual(first, "partial text")
        self.assertEqual(calls, 1)
        self.assertEqual(second, "partial text")
        self.assertEqual(committed_first, committed_second)

    def test_commit_stable_transcript_without_session_key(self) -> None:
        result = commit_stable_transcript_from_runtime(
            None,
            new_partial_text="  hello  ",
            deps=self.deps,
        )
        self.assertEqual(result, "hello")
        self.assertFalse(self.session_partial_stt_text)
        self.assertFalse(self.session_committed_stt_text)

    def test_score_stt_candidate_boosted_by_wake_word(self) -> None:
        no_wake = score_stt_candidate_from_runtime("일반 텍스트", deps=self.deps)
        with_wake = score_stt_candidate_from_runtime("일반 텍스트 wake", wake_probe="wake", deps=self.deps)
        self.assertGreater(with_wake, no_wake)

    def test_choose_full_stt_candidate_prefers_primary_when_similar(self) -> None:
        chosen, meta = choose_full_stt_candidate_from_runtime("안녕하세요", "안녕", wake_probe="", deps=self.deps)
        self.assertEqual(chosen, "안녕하세요")
        self.assertEqual(meta["selected"], "primary")

    def test_detect_wake_word_sync_exact_match(self) -> None:
        def transcribe(audio: np.ndarray, **_kwargs: object) -> str:
            if audio.size > 120:
                return "wake wake"
            return "noise"

        result = detect_wake_word_sync_from_runtime(
            np.ones(240, dtype=np.float32),
            sampling_rate=16000,
            wake_audio_sec=0.08,
            wake_confirm_audio_sec=0.08,
            wake_max_tokens=12,
            wake_confirm_max_tokens=12,
            transcribe_audio16k_sync=transcribe,
            apply_stt_post_corrections=lambda text, wake_detected=False: text,
            strip_leading_voice_fillers=lambda text: text,
            extract_leading_wake_alias=lambda text: "wake" if "wake" in text else None,
            fuzzy_leading_wake_alias=lambda text: "wake" if "wak" in text else None,
            looks_like_gibberish_probe=lambda text: False,
            slice_audio_window=lambda audio, sec, sampling_rate: audio,
        )
        self.assertTrue(result["wake_detected"])
        self.assertEqual(result["wake_match_mode"], "exact")

    def test_detect_wake_word_sync_fuzzy_miss(self) -> None:
        def transcribe(audio: np.ndarray, **_kwargs: object) -> str:
            if audio.size > 120:
                return "wake"
            return "noise"

        result = detect_wake_word_sync_from_runtime(
            np.ones(240, dtype=np.float32),
            sampling_rate=16000,
            wake_audio_sec=0.08,
            wake_confirm_audio_sec=0.08,
            wake_max_tokens=12,
            wake_confirm_max_tokens=12,
            transcribe_audio16k_sync=transcribe,
            apply_stt_post_corrections=lambda text, wake_detected=False: text,
            strip_leading_voice_fillers=lambda text: text,
            extract_leading_wake_alias=lambda text: None,
            fuzzy_leading_wake_alias=lambda text: None,
            looks_like_gibberish_probe=lambda text: False,
            slice_audio_window=lambda audio, sec, sampling_rate: audio,
        )
        self.assertFalse(result["wake_detected"])
        self.assertEqual(result["wake_match_mode"], "rejected")
        self.assertEqual(result["wake_reject_reason"], "probe_miss")


if __name__ == "__main__":
    unittest.main()
