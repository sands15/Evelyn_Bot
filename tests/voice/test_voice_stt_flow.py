import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_stt_flow import (  # noqa: E402
    WakeSttResult,
    apply_fuzzy_wake_near_miss,
    apply_strict_wake_confirm_policy,
    build_final_transcript_flow,
    decide_final_wake_veto,
    get_matching_speculative_policy_from_runtime,
    interpret_wake_probe_result,
    remember_speculative_policy_from_runtime,
    run_full_stt_with_optional_rescore,
    run_partial_stt_flow,
    speculate_from_committed_stt_from_runtime,
)


class FakeAudio:
    def __init__(self, size: int) -> None:
        self.size = size


class VoiceSttFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_wake_stt_result_normalizes_mapping(self) -> None:
        result = WakeSttResult.from_mapping(
            {
                "wake_detected": True,
                "wake_probe_text": "Evelyn",
                "wake_confirm_text": "Evelyn",
                "wake_match_mode": "",
                "wake_alias": " evelyn ",
                "wake_reject_reason": "",
            },
            clean_text=lambda text: str(text).strip(),
        )

        self.assertTrue(result.wake_detected)
        self.assertEqual(result.probe_text, "Evelyn")
        self.assertEqual(result.confirm_text, "Evelyn")
        self.assertEqual(result.wake_match_mode, "exact")
        self.assertEqual(result.wake_alias, "evelyn")
        self.assertIsNone(result.wake_reject_reason)

    def test_interpret_wake_probe_result_applies_post_corrections(self) -> None:
        result = interpret_wake_probe_result(
            {
                "wake_detected": True,
                "wake_probe_text": " evelin ",
                "wake_confirm_text": " evelyn ",
                "wake_match_mode": "fuzzy",
                "wake_alias": "evelyn",
            },
            clean_text=lambda text: str(text).strip(),
            apply_post_corrections=lambda text, **_kwargs: str(text).strip().replace("evelin", "evelyn"),
        )

        self.assertTrue(result.wake_detected)
        self.assertEqual(result.probe_text, "evelyn")
        self.assertEqual(result.confirm_text, "evelyn")
        self.assertEqual(result.wake_match_mode, "fuzzy")
        self.assertEqual(result.wake_alias, "evelyn")

    def test_strict_wake_confirm_policy_rejects_non_exact_match(self) -> None:
        wake = interpret_wake_probe_result(
            {
                "wake_detected": True,
                "wake_probe_text": "evelyn",
                "wake_confirm_text": "evelyn",
                "wake_match_mode": "fuzzy",
                "wake_alias": "evelyn",
            },
            clean_text=lambda text: str(text).strip(),
            apply_post_corrections=lambda text, **_kwargs: str(text),
        )

        result = apply_strict_wake_confirm_policy(wake, strict_confirm_required=True)

        self.assertFalse(result.wake_detected)
        self.assertEqual(result.wake_match_mode, "rejected")
        self.assertEqual(result.wake_reject_reason, "unstable_audio")

    def test_fuzzy_wake_near_miss_promotes_rejected_probe(self) -> None:
        wake = interpret_wake_probe_result(
            {
                "wake_detected": False,
                "wake_probe_text": "evlyn",
                "wake_confirm_text": "",
                "wake_match_mode": "rejected",
                "wake_reject_reason": "probe_miss",
            },
            clean_text=lambda text: str(text).strip(),
            apply_post_corrections=lambda text, **_kwargs: str(text),
        )

        result = apply_fuzzy_wake_near_miss(
            wake,
            fuzzy_leading_wake_alias=lambda text: "evelyn" if text == "evlyn" else None,
        )

        self.assertTrue(result.wake_detected)
        self.assertEqual(result.wake_match_mode, "fuzzy")
        self.assertEqual(result.wake_alias, "evelyn")
        self.assertIsNone(result.wake_reject_reason)
        self.assertTrue(result.near_miss)

    def test_speculate_from_committed_stt_requires_speaker_and_policy(self) -> None:
        result = speculate_from_committed_stt_from_runtime(
            "  continue the work  ",
            {"owner_user_id": 7},
            clean_text=lambda text: str(text).strip(),
            fast_path_policy=lambda text, source, state: {"text": text, "source": source, "owner": state["owner_user_id"]},
            monotonic=lambda: 123.0,
        )

        self.assertEqual(result["text"], "continue the work")
        self.assertEqual(result["policy"]["source"], "voice")
        self.assertEqual(result["prepared_at"], 123.0)
        self.assertIsNone(
            speculate_from_committed_stt_from_runtime(
                "short",
                {"owner_user_id": 7},
                clean_text=lambda text: str(text).strip(),
                fast_path_policy=lambda *_args: {"ok": True},
                monotonic=lambda: 1.0,
            )
        )
        self.assertIsNone(
            speculate_from_committed_stt_from_runtime(
                "continue the work",
                {},
                clean_text=lambda text: str(text).strip(),
                fast_path_policy=lambda *_args: {"ok": True},
                monotonic=lambda: 1.0,
            )
        )

    def test_speculative_policy_store_matches_and_expires(self) -> None:
        store: dict[str, dict[str, Any]] = {}
        speculative = {"text": "continue the work", "prepared_at": 10.0}

        remember_speculative_policy_from_runtime(store, "session-1", speculative)
        matched = get_matching_speculative_policy_from_runtime(
            store,
            "session-1",
            "continue the work please",
            clean_text=lambda text: str(text).strip(),
            is_similar=lambda _left, _right: False,
            monotonic=lambda: 12.0,
        )
        expired = get_matching_speculative_policy_from_runtime(
            store,
            "session-1",
            "continue the work please",
            clean_text=lambda text: str(text).strip(),
            is_similar=lambda _left, _right: False,
            monotonic=lambda: 31.0,
        )

        self.assertIs(matched, speculative)
        self.assertIsNone(expired)
        self.assertNotIn("session-1", store)

    def test_final_wake_veto_allows_owner_followup_without_alias(self) -> None:
        result = decide_final_wake_veto(
            final_text="continue that",
            owner_followup_active=True,
            extract_leading_wake_alias=lambda _text: None,
        )

        self.assertTrue(result.accepted)
        self.assertIsNone(result.wake_alias)
        self.assertIsNone(result.reject_reason)

    def test_final_wake_veto_rejects_non_followup_without_alias(self) -> None:
        result = decide_final_wake_veto(
            final_text="continue that",
            owner_followup_active=False,
            extract_leading_wake_alias=lambda _text: None,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reject_reason, "full_text_veto")

    def test_final_wake_veto_accepts_non_followup_with_alias(self) -> None:
        result = decide_final_wake_veto(
            final_text="evelyn continue that",
            owner_followup_active=False,
            extract_leading_wake_alias=lambda text: "evelyn" if text.startswith("evelyn") else None,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.wake_alias, "evelyn")

    def test_build_final_transcript_flow_corrects_commits_and_speculates(self) -> None:
        partial_store: dict[str | None, str] = {}
        committed_calls: list[tuple[str | None, str]] = []

        def commit(session_key: str | None, *, new_partial_text: str) -> str:
            committed_calls.append((session_key, new_partial_text))
            return f"committed:{new_partial_text}"

        def build_transcript(**kwargs: Any) -> dict[str, Any]:
            return dict(kwargs)

        result = build_final_transcript_flow(
            text="evelin hello",
            partial_text=" partial ",
            session_key="session-1",
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="evelyn",
            wake_probe="evelyn",
            wake_confirm="evelyn",
            wake_reject_reason=None,
            speaker_user_id=10,
            duration_sec=1.5,
            room_state={"mode": "voice"},
            apply_post_corrections=lambda text, **_kwargs: str(text).replace("evelin", "evelyn"),
            clean_text=lambda text: str(text).strip(),
            set_partial_text=lambda key, value: partial_store.__setitem__(key, value),
            commit_stable_transcript=commit,
            build_transcript_result=build_transcript,
            speculate_from_committed_stt=lambda text, state: {"text": text, "state": state},
        )

        self.assertEqual(result.corrected_text, "evelyn hello")
        self.assertTrue(result.was_corrected)
        self.assertEqual(partial_store["session-1"], "partial")
        self.assertEqual(committed_calls, [("session-1", "evelyn hello")])
        self.assertEqual(result.committed_text, "committed:evelyn hello")
        self.assertEqual(result.transcript_result["final_text"], "evelyn hello")
        self.assertEqual(result.transcript_result["partial_text"], "partial")
        self.assertEqual(result.speculative_policy["text"], "committed:evelyn hello")

    async def test_full_stt_flow_skips_rescore_when_audio_too_short(self) -> None:
        calls: list[tuple[str, float]] = []

        async def run_blocking(func: Any, *, stage: str, timeout_sec: float, metrics: dict | None = None) -> str:
            calls.append((stage, timeout_sec))
            return func()

        result = await run_full_stt_with_optional_rescore(
            "audio",
            sampling_rate=16000,
            duration_sec=0.5,
            wake_probe="",
            max_new_tokens=32,
            full_timeout_sec=12.0,
            rescore_enabled=True,
            rescore_extra_tokens=16,
            rescore_min_audio_sec=1.0,
            rescore_min_text_len=4,
            rescore_timeout_sec=3.0,
            run_blocking_stt_task=run_blocking,
            transcribe_audio=lambda _audio, _tokens, **_kwargs: "hello",
            choose_candidate=lambda primary, _rescore: (primary, {"selected": "primary"}),
            clean_text=lambda text: str(text).strip(),
        )

        self.assertEqual(result.text, "hello")
        self.assertEqual(result.stt_meta["selected"], "primary")
        self.assertEqual(result.stt_meta["skipped_reason"], "audio_too_short")
        self.assertEqual(calls, [("full", 12.0)])

    async def test_full_stt_flow_runs_rescore_and_uses_candidate(self) -> None:
        stages: list[str] = []

        async def run_blocking(func: Any, *, stage: str, timeout_sec: float, metrics: dict | None = None) -> str:
            stages.append(stage)
            return func()

        def transcribe(_audio: Any, max_new_tokens: int, **kwargs: Any) -> str:
            return "primary" if kwargs["stage"] == "full" else "better answer"

        result = await run_full_stt_with_optional_rescore(
            "audio",
            sampling_rate=16000,
            duration_sec=2.0,
            wake_probe="",
            max_new_tokens=32,
            full_timeout_sec=12.0,
            rescore_enabled=True,
            rescore_extra_tokens=16,
            rescore_min_audio_sec=1.0,
            rescore_min_text_len=4,
            rescore_timeout_sec=3.0,
            run_blocking_stt_task=run_blocking,
            transcribe_audio=transcribe,
            choose_candidate=lambda _primary, rescore: (
                rescore,
                {"selected": "rescore", "primary_score": 1.0, "rescore_score": 3.0},
            ),
            clean_text=lambda text: str(text).strip(),
        )

        self.assertEqual(result.text, "better answer")
        self.assertEqual(result.stt_meta["selected"], "rescore")
        self.assertEqual(stages, ["full", "full-rescore"])

    async def test_partial_stt_flow_skips_when_audio_too_short(self) -> None:
        metrics: dict[str, Any] = {}

        async def run_blocking(_func: Any, *, stage: str, timeout_sec: float, metrics: dict | None = None) -> tuple[str, str]:
            raise AssertionError("partial STT should not run")

        result = await run_partial_stt_flow(
            FakeAudio(1),
            sampling_rate=16000,
            session_key="guild:user",
            timeout_sec=3.0,
            build_partial_stt_window=lambda _audio, **_kwargs: FakeAudio(10),
            get_partial_transcript=lambda *_args, **_kwargs: ("partial", "committed"),
            read_committed_text=lambda _key: " committed ",
            run_blocking_stt_task=run_blocking,
            speculate_from_committed_stt=lambda _text, _state: None,
            room_state={"room": "state"},
            clean_text=lambda text: str(text).strip(),
            metrics=metrics,
        )

        self.assertEqual(result.partial_text, "")
        self.assertEqual(result.committed_text, "committed")
        self.assertEqual(result.skipped_reason, "insufficient_audio")
        self.assertEqual(metrics["meta"]["partial_stt_skip_reason"], "insufficient_audio")

    async def test_partial_stt_flow_runs_and_returns_speculative_policy(self) -> None:
        stages: list[tuple[str, float]] = []

        async def run_blocking(func: Any, *, stage: str, timeout_sec: float, metrics: dict | None = None) -> tuple[str, str]:
            stages.append((stage, timeout_sec))
            return func()

        result = await run_partial_stt_flow(
            FakeAudio(20000),
            sampling_rate=16000,
            session_key="guild:user",
            timeout_sec=4.0,
            build_partial_stt_window=lambda _audio, **_kwargs: FakeAudio(16000),
            get_partial_transcript=lambda *_args, **_kwargs: ("partial", "committed"),
            read_committed_text=lambda _key: "",
            run_blocking_stt_task=run_blocking,
            speculate_from_committed_stt=lambda text, state: {
                "policy": {"route": "main_direct"},
                "text": text,
                "state": state,
            },
            room_state={"mode": "voice"},
            clean_text=lambda text: str(text).strip(),
        )

        self.assertEqual(result.partial_text, "partial")
        self.assertEqual(result.committed_text, "committed")
        self.assertEqual(result.speculative_policy["policy"], {"route": "main_direct"})
        self.assertEqual(result.speculative_policy["text"], "committed")
        self.assertEqual(stages, [("partial", 4.0)])


if __name__ == "__main__":
    unittest.main()
