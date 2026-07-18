from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_session_policy_runtime import (  # noqa: E402
    DiscordSessionPolicyRuntimeDeps,
    is_tail_fragment_candidate_from_runtime,
    is_short_followup_candidate_from_runtime,
    is_transport_corrupted_audio_from_runtime,
    should_ignore_short_transcription_from_runtime,
    should_skip_full_stt_after_wake_probe_from_runtime,
    should_require_confirm_exact_for_wake_from_runtime,
)


class DiscordSessionPolicyRuntimeTests(unittest.TestCase):
    def test_tail_fragment_candidate_runtime_reads_session_state_and_thresholds(self) -> None:
        seen: list[tuple] = []

        deps = DiscordSessionPolicyRuntimeDeps(
            session_last_turn_accepted_at_get=lambda session_key: seen.append(("get", session_key)) or 3.0,
            monotonic_fn=lambda: 10.0,
            should_require_confirm_exact_for_wake_payload=lambda debug_meta: True,
            is_transport_corrupted_audio_payload=lambda debug_meta: True,
            no_wake_max_continue_sec=1.2,
            clean_text=lambda text: (text or "").strip(),
            looks_like_brief_filler_text=lambda text: text == "...",
            looks_like_repetitive_noise_text=lambda text: "noise" in text,
            tail_fragment_window_sec=7.0,
            tail_fragment_max_raw_sec=1.2,
            tail_fragment_max_voiced_ms=180.0,
            tail_fragment_max_longest_ms=260.0,
            normalize_voice_text=lambda text: (text or "").strip(),
            normalized_wake_words=lambda: {"좋아요", "ok"},
            min_audio_sec=0.15,
            min_transcribed_len=5,
            wake_short_text_keep_len=3,
        )

        self.assertTrue(
            is_tail_fragment_candidate_from_runtime(
                session_key="guild:1:text:2",
                raw_seconds=0.5,
                voiced_ms=120.0,
                longest_voiced_ms=80.0,
                unstable=False,
                deps=deps,
            ),
            "tail fragment should qualify under injected thresholds",
        )
        self.assertIn(("get", "guild:1:text:2"), seen)

        self.assertFalse(
            is_tail_fragment_candidate_from_runtime(
                session_key=None,
                raw_seconds=0.5,
                voiced_ms=120.0,
                longest_voiced_ms=80.0,
                unstable=False,
                deps=deps,
            )
        )

    def test_boolean_policy_wrappers_delegate_payloads(self) -> None:
        calls: list[tuple[str, dict] | tuple[str, None]] = []

        def wake_policy(debug_meta):
            calls.append(("wake", tuple(sorted((debug_meta or {}).items()))))
            return bool(debug_meta and "wake" in str(debug_meta.get("reasons", [])))

        def transport_policy(debug_meta):
            calls.append(("transport", tuple(sorted((debug_meta or {}).items()))))
            return bool(debug_meta and debug_meta.get("transport"))

        deps = DiscordSessionPolicyRuntimeDeps(
            session_last_turn_accepted_at_get=lambda _session_key: 0.0,
            monotonic_fn=lambda: 0.0,
            should_require_confirm_exact_for_wake_payload=wake_policy,
            is_transport_corrupted_audio_payload=transport_policy,
            no_wake_max_continue_sec=2.0,
            clean_text=lambda text: (text or "").strip(),
            looks_like_brief_filler_text=lambda text: text in {"...", "네"},
            looks_like_repetitive_noise_text=lambda text: text == "noise",
            tail_fragment_window_sec=1.0,
            tail_fragment_max_raw_sec=1.0,
            tail_fragment_max_voiced_ms=1.0,
            tail_fragment_max_longest_ms=1.0,
            normalize_voice_text=lambda text: (text or "").strip(),
            normalized_wake_words=lambda: {"start", "hey"},
            min_audio_sec=0.15,
            min_transcribed_len=6,
            wake_short_text_keep_len=2,
        )

        self.assertTrue(should_require_confirm_exact_for_wake_from_runtime(debug_meta={"reasons": ["wake"]}, deps=deps))
        self.assertFalse(should_require_confirm_exact_for_wake_from_runtime(debug_meta={"x": 1}, deps=deps))
        self.assertTrue(is_transport_corrupted_audio_from_runtime(debug_meta={"transport": True}, deps=deps))
        self.assertFalse(is_transport_corrupted_audio_from_runtime(debug_meta={"transport": False}, deps=deps))
        self.assertIn(("wake", (("reasons", ["wake"]),)), calls)
        self.assertIn(("transport", (("transport", True),)), calls)
        self.assertIn(("transport", (("transport", False),)), calls)

    def test_skip_full_stt_after_wake_probe_delegates_to_policy_via_deps(self) -> None:
        calls: list[tuple[str, object]] = []

        def clean_text(text: str) -> str:
            calls.append(("clean_text", text))
            return (text or "").strip()

        def brief_filler(text: str) -> bool:
            calls.append(("brief", text))
            return text in {"...", "음"}

        def repetitive_noise(text: str) -> bool:
            calls.append(("noise", text))
            return text == "!!"

        deps = DiscordSessionPolicyRuntimeDeps(
            session_last_turn_accepted_at_get=lambda _session_key: 0.0,
            monotonic_fn=lambda: 0.0,
            should_require_confirm_exact_for_wake_payload=lambda debug_meta: False,
            is_transport_corrupted_audio_payload=lambda debug_meta: False,
            no_wake_max_continue_sec=2.0,
            clean_text=clean_text,
            looks_like_brief_filler_text=brief_filler,
            looks_like_repetitive_noise_text=repetitive_noise,
            tail_fragment_window_sec=0.0,
            tail_fragment_max_raw_sec=0.0,
            tail_fragment_max_voiced_ms=0.0,
            tail_fragment_max_longest_ms=0.0,
            normalize_voice_text=lambda text: (text or "").strip(),
            normalized_wake_words=lambda: {"start", "ok"},
            min_audio_sec=0.15,
            min_transcribed_len=6,
            wake_short_text_keep_len=2,
        )

        self.assertTrue(
            should_skip_full_stt_after_wake_probe_from_runtime(
                wake_detected=False,
                wake_probe="",
                duration_sec=1.0,
                deps=deps,
            )
        )
        self.assertFalse(
            should_skip_full_stt_after_wake_probe_from_runtime(
                wake_detected=True,
                wake_probe="hello",
                duration_sec=1.0,
                deps=deps,
            )
        )
        self.assertTrue(
            should_skip_full_stt_after_wake_probe_from_runtime(
                wake_detected=False,
                wake_probe="...",
                duration_sec=1.5,
                deps=deps,
            )
        )
        self.assertTrue(
            should_skip_full_stt_after_wake_probe_from_runtime(
                wake_detected=False,
                wake_probe="!!",
            duration_sec=1.5,
                deps=deps,
            )
        )

        self.assertIn(("clean_text", ""), calls)
        self.assertIn(("brief", "..."), calls)
        self.assertIn(("noise", "!!"), calls)

    def test_short_transcription_and_followup_candidate_delegates_via_deps(self) -> None:
        calls: list[tuple[str, object]] = []

        def normalize_voice_text(text: str) -> str:
            calls.append(("normalize", text))
            return (text or "").strip()

        def normalized_wake_words() -> set[str]:
            calls.append(("wake_words", "fetch"))
            return {"ok", "start"}

        deps = DiscordSessionPolicyRuntimeDeps(
            session_last_turn_accepted_at_get=lambda _session_key: 0.0,
            monotonic_fn=lambda: 0.0,
            should_require_confirm_exact_for_wake_payload=lambda debug_meta: False,
            is_transport_corrupted_audio_payload=lambda debug_meta: False,
            no_wake_max_continue_sec=2.0,
            clean_text=lambda text: (text or "").strip(),
            looks_like_brief_filler_text=lambda text: text == "...",
            looks_like_repetitive_noise_text=lambda text: text == "noise",
            tail_fragment_window_sec=1.0,
            tail_fragment_max_raw_sec=1.0,
            tail_fragment_max_voiced_ms=1.0,
            tail_fragment_max_longest_ms=1.0,
            normalize_voice_text=normalize_voice_text,
            normalized_wake_words=normalized_wake_words,
            min_audio_sec=0.25,
            min_transcribed_len=6,
            wake_short_text_keep_len=3,
        )

        self.assertTrue(
            should_ignore_short_transcription_from_runtime(
                text=" hmm",
                audio_sec=0.1,
                wake_detected=False,
                deps=deps,
            )
        )
        self.assertFalse(
            should_ignore_short_transcription_from_runtime(
                text="ok",
                audio_sec=0.05,
                wake_detected=True,
                deps=deps,
            )
        )
        self.assertTrue(
            is_short_followup_candidate_from_runtime(
                text="아 ㅇㅋ",
                audio_sec=0.3,
                wake_detected=False,
                owner_followup_active=True,
                deps=deps,
            )
        )
        self.assertFalse(
            is_short_followup_candidate_from_runtime(
                text="요약",
                audio_sec=0.3,
                wake_detected=False,
                owner_followup_active=False,
                deps=deps,
            )
        )
        self.assertIn(("normalize", " hmm"), calls)
        self.assertIn(("wake_words", "fetch"), calls)


if __name__ == "__main__":
    unittest.main()
