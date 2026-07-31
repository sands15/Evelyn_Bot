from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_validation import (  # noqa: E402
    MAX_ATTEMPTS,
    SUITE_ID,
    VoiceValidationManager,
    emit_voice_validation_event,
    observe_turn_trace_for_voice_validation,
    sanitize_validation_event,
    transcript_match,
)


READY_CAPABILITIES = {
    "voiceLocal": {"state": "ready", "ready": True, "blockers": []},
    "voiceDiscord": {"state": "ready", "ready": True, "blockers": []},
}


class FakeClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class VoiceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.clock = FakeClock()
        self.manager = VoiceValidationManager(
            root=self.root,
            now=self.clock,
            ttl_sec=1800,
        )

    def start(self, surfaces=("local",)):
        result = self.manager.start(
            suite=SUITE_ID,
            surfaces=surfaces,
            capabilities=READY_CAPABILITIES,
        )
        self.assertTrue(result["ok"], result)
        return result["session"]

    def record(self, event: str, **payload):
        session = self.manager.snapshot()
        step = session["currentStep"]
        result = self.manager.record_event(
            {
                "event": event,
                "surface": step["surface"],
                "stepId": step["id"],
                **payload,
            }
        )
        self.assertTrue(result["ok"], result)
        return result

    def complete_normal_step(self) -> None:
        session = self.manager.snapshot()
        step = session["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted")
        self.record("reply_final")
        self.record("playback_started", latencyMs=1200)
        self.record("playback_completed")
        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            heard=True,
        )
        self.assertTrue(result["ok"], result)

    def test_transcript_normalization_keywords_and_similarity(self) -> None:
        keyword_match = transcript_match(
            "이블린 지금 듣고 있어",
            "이블린, 지금 듣고 있어?",
            keywords=("이블린", "듣고"),
        )
        similarity_match = transcript_match("음성 테스트 마지막 문장이야", "음성 테스트 마지막 문장이야")
        mismatch = transcript_match("완전히 다른 말", "이블린 지금 듣고 있어")

        self.assertTrue(keyword_match["matched"])
        self.assertTrue(similarity_match["matched"])
        self.assertFalse(mismatch["matched"])

    def test_event_sanitizer_never_persists_transcript_or_audio(self) -> None:
        event = sanitize_validation_event(
            {
                "event": "stt_final",
                "transcript": "비밀 원문",
                "Raw-Transcript": "다른 비밀 원문",
                "rawAudio": "AAAA",
                "audio_f32_base64": "BBBB",
                "meta": {"PCMBytes": "CCCC", "latencyMs": 12},
                "matched": True,
            }
        )
        self.assertNotIn("transcript", event)
        self.assertNotIn("rawAudio", event)
        self.assertNotIn("audio_f32_base64", event)
        self.assertNotIn("Raw-Transcript", event)
        self.assertNotIn("PCMBytes", event["meta"])
        self.assertEqual(event["meta"]["latencyMs"], 12)
        self.assertTrue(event["matched"])

    def test_fsm_recovers_active_session_after_manager_restart(self) -> None:
        started = self.start()
        self.complete_normal_step()

        recovered = VoiceValidationManager(root=self.root, now=self.clock)
        snapshot = recovered.snapshot()

        self.assertEqual(snapshot["sessionId"], started["sessionId"])
        self.assertEqual(snapshot["state"], "running")
        self.assertEqual(snapshot["currentStep"]["id"], "02-listening")

    def test_duplicate_turn_fails_attempt_and_allows_retry(self) -> None:
        self.start()
        self.record("stt_final", transcript="이블린")
        self.record("turn_accepted", eventId="accepted-1")
        self.record("turn_accepted", eventId="accepted-2")

        session = self.manager.snapshot()
        self.assertEqual(session["state"], "running")
        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertEqual(session["lastFailureCode"], "duplicate_turn_or_playback")
        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=session["currentStep"]["id"],
        )
        self.assertTrue(retried["ok"])
        self.assertEqual(retried["session"]["currentStep"]["status"], "pending")

    def test_stt_mismatch_fails_attempt_immediately(self) -> None:
        self.start()

        self.record("stt_final", transcript="완전히 다른 말")

        session = self.manager.snapshot()
        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertEqual(session["lastFailureCode"], "stt_mismatch")

    def test_conflicting_playback_terminal_events_fail_attempt(self) -> None:
        self.start()
        self.record("stt_final", transcript="이블린")
        self.record("turn_accepted")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        self.record("playback_cancelled")

        session = self.manager.snapshot()
        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertEqual(
            session["lastFailureCode"],
            "conflicting_playback_terminal_events",
        )

    def test_retry_is_limited_to_three_attempts(self) -> None:
        session = self.start()
        step_id = session["currentStep"]["id"]
        for expected_attempt in range(2, MAX_ATTEMPTS + 1):
            result = self.manager.retry(session_id=session["sessionId"], step_id=step_id)
            self.assertTrue(result["ok"])
            self.assertEqual(result["session"]["attempt"], expected_attempt)

        exhausted = self.manager.retry(session_id=session["sessionId"], step_id=step_id)
        self.assertFalse(exhausted["ok"])
        self.assertEqual(exhausted["error"], "attempt_budget_exhausted")

    def test_retry_rejects_a_non_current_step(self) -> None:
        session = self.start()
        completed_step_id = session["currentStep"]["id"]
        self.complete_normal_step()

        result = self.manager.retry(
            session_id=session["sessionId"],
            step_id=completed_step_id,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_step_not_current")
        self.assertEqual(self.manager.snapshot()["currentStep"]["id"], "02-listening")

    def test_heard_confirmation_requires_completed_playback(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted")
        self.record("reply_final")
        self.record("playback_started")

        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            heard=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "playback_not_completed")
        self.assertFalse(self.manager.snapshot()["currentStep"]["heard"])

    def test_events_for_non_active_steps_are_rejected(self) -> None:
        session = self.start()

        result = self.manager.record_event(
            {
                "event": "turn_accepted",
                "surface": "local",
                "stepId": "03-mood",
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_event_step_not_active")
        self.assertEqual(self.manager.snapshot()["currentStep"]["id"], "01-wake")

    def test_third_failed_attempt_ends_session(self) -> None:
        session = self.start()
        step_id = session["currentStep"]["id"]
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.record("turn_accepted", eventId=f"accepted-{attempt}-1")
            self.record("turn_accepted", eventId=f"accepted-{attempt}-2")
            snapshot = self.manager.snapshot()
            if attempt < MAX_ATTEMPTS:
                self.assertEqual(snapshot["state"], "running")
                self.manager.retry(session_id=session["sessionId"], step_id=step_id)
        final = self.manager.snapshot()
        self.assertEqual(final["state"], "failed")
        self.assertEqual(final["failureCode"], "duplicate_turn_or_playback")

    def test_session_expires_after_thirty_minutes(self) -> None:
        self.start()
        self.clock.advance(1801)
        session = self.manager.snapshot()
        self.assertEqual(session["state"], "failed")
        self.assertEqual(session["failureCode"], "session_expired")

    def test_expired_session_rejects_mutation_without_prior_snapshot(self) -> None:
        operations = {
            "confirm": lambda manager, session: manager.confirm(
                session_id=session["sessionId"],
                step_id=session["currentStep"]["id"],
                heard=True,
            ),
            "retry": lambda manager, session: manager.retry(
                session_id=session["sessionId"],
                step_id=session["currentStep"]["id"],
            ),
            "abort": lambda manager, session: manager.abort(
                session_id=session["sessionId"],
            ),
            "record_event": lambda manager, session: manager.record_event(
                {
                    "event": "turn_accepted",
                    "surface": "local",
                    "stepId": session["currentStep"]["id"],
                }
            ),
        }
        for name, operation in operations.items():
            with self.subTest(operation=name):
                clock = FakeClock()
                root = self.root / name
                manager = VoiceValidationManager(root=root, now=clock, ttl_sec=1800)
                started = manager.start(
                    suite=SUITE_ID,
                    surfaces=("local",),
                    capabilities=READY_CAPABILITIES,
                )["session"]
                clock.advance(1800)

                result = operation(manager, started)

                self.assertFalse(result["ok"])
                self.assertEqual(result["session"]["state"], "failed")
                self.assertEqual(result["session"]["failureCode"], "session_expired")
                report = (
                    root
                    / "voice_validation"
                    / "reports"
                    / f"{started['sessionId']}.json"
                )
                self.assertTrue(report.exists())

    def test_expired_preflight_cannot_be_resumed_without_prior_snapshot(self) -> None:
        blocked = {
            **READY_CAPABILITIES,
            "voiceLocal": {
                "state": "unavailable",
                "ready": False,
                "blockers": [{"code": "local_mic_capture_not_ready"}],
            },
        }
        session = self.manager.start(
            suite=SUITE_ID,
            surfaces=("local",),
            capabilities=blocked,
        )["session"]
        self.assertEqual(session["state"], "preflight")
        self.clock.advance(1800)

        result = self.manager.resume_after_preflight(capabilities=READY_CAPABILITIES)

        self.assertFalse(result["ok"])
        self.assertEqual(result["session"]["state"], "failed")
        self.assertEqual(result["session"]["failureCode"], "session_expired")

    def test_runtime_event_emission_rejects_expired_active_file(self) -> None:
        session = self.start()
        self.clock.advance(1800)

        with patch("evelyn_core.voice_validation.time.time", new=self.clock):
            emitted = emit_voice_validation_event(
                "local",
                "turn_accepted",
                root=self.root,
                session_id=session["sessionId"],
                step_id=session["currentStep"]["id"],
            )

        self.assertIsNone(emitted)
        events = self.root / "voice_validation" / "events" / f"{session['sessionId']}.jsonl"
        self.assertFalse(events.exists())

    def test_runtime_event_emission_rejects_explicit_foreign_session_and_step(self) -> None:
        session = self.start()

        for session_id, step_id in (
            ("foreign-session", session["currentStep"]["id"]),
            (session["sessionId"], "03-mood"),
        ):
            with self.subTest(session_id=session_id, step_id=step_id):
                emitted = emit_voice_validation_event(
                    "local",
                    "turn_accepted",
                    root=self.root,
                    session_id=session_id,
                    step_id=step_id,
                    now=self.clock,
                )
                self.assertIsNone(emitted)

        events = self.root / "voice_validation" / "events" / f"{session['sessionId']}.jsonl"
        self.assertFalse(events.exists())

    def test_runtime_event_emission_accepts_current_and_paired_interrupt_steps(self) -> None:
        session = self.start()
        current = emit_voice_validation_event(
            "local",
            "turn_accepted",
            root=self.root,
            session_id=session["sessionId"],
            step_id=session["currentStep"]["id"],
            now=self.clock,
        )
        self.assertIsNotNone(current)
        self.assertEqual(current["stepId"], "01-wake")

        paired_root = self.root / "paired-interrupt"
        paired_manager = VoiceValidationManager(root=paired_root, now=self.clock)
        paired_session = paired_manager.start(
            suite=SUITE_ID,
            surfaces=("local",),
            capabilities=READY_CAPABILITIES,
        )["session"]
        active_path = paired_root / "voice_validation" / "active.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["surface"] = "local"
        active["currentStep"] = {
            "id": "07-barge-source",
            "kind": "barge_source",
            "interruptStepId": "08-barge-interrupt",
        }
        active_path.write_text(json.dumps(active), encoding="utf-8")

        interrupt = emit_voice_validation_event(
            "local",
            "barge_in_accepted",
            root=paired_root,
            session_id=paired_session["sessionId"],
            step_id="08-barge-interrupt",
            now=self.clock,
        )

        self.assertIsNotNone(interrupt)
        self.assertEqual(interrupt["stepId"], "08-barge-interrupt")

    def test_invalid_persisted_expiry_fails_closed_without_crashing(self) -> None:
        session = self.start()
        active_path = self.root / "voice_validation" / "active.json"

        for invalid_expiry in (None, "not-a-number", "NaN", "Infinity", -1):
            with self.subTest(expires_at=invalid_expiry):
                active = json.loads(active_path.read_text(encoding="utf-8"))
                active["state"] = "running"
                active["expiresAt"] = invalid_expiry
                active_path.write_text(json.dumps(active), encoding="utf-8")

                recovered = VoiceValidationManager(root=self.root, now=self.clock)
                snapshot = recovered.snapshot()

                self.assertEqual(snapshot["sessionId"], session["sessionId"])
                self.assertEqual(snapshot["state"], "failed")
                self.assertEqual(snapshot["failureCode"], "session_expiry_invalid")

    def test_partial_jsonl_tail_is_ignored_until_complete(self) -> None:
        session = self.start()
        events_path = self.root / "voice_validation" / "events" / f"{session['sessionId']}.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text('{"event":"turn_accepted"', encoding="utf-8")

        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["events"].get("turn_accepted", 0), 0)

    def test_reordered_complete_event_is_tolerated_within_grace_window(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("playback_completed", eventId="completed-first")
        self.record("playback_started", eventId="started-second")
        self.record("reply_final", eventId="reply-third")
        self.record("turn_accepted", eventId="accepted-fourth")
        self.record("stt_final", transcript=step["prompt"], eventId="stt-last")
        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            heard=True,
        )
        self.assertEqual(result["session"]["currentStep"]["id"], "02-listening")

    def test_orphan_playback_fails_attempt_after_reorder_grace(self) -> None:
        self.start()
        self.record("playback_completed")
        self.clock.advance(3)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["status"], "failed")
        self.assertEqual(snapshot["lastFailureCode"], "orphan_or_incomplete_playback")

    def test_reports_are_limited_to_twenty_and_thirty_days(self) -> None:
        reports = self.root / "voice_validation" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        for index in range(22):
            path = reports / f"report-{index:02d}.json"
            path.write_text("{}", encoding="utf-8")
            timestamp = self.clock.value - index
            os.utime(path, (timestamp, timestamp))

        removed = self.manager.prune_reports()
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(list(reports.glob("*.json"))), 20)

        self.clock.advance(31 * 86400)
        self.manager.prune_reports()
        self.assertFalse(list(reports.glob("*.json")))

    def test_silence_step_fails_on_any_tts_playback_activity(self) -> None:
        session = self.start()
        while self.manager.snapshot()["currentStep"]["kind"] != "silence":
            snapshot = self.manager.snapshot()
            step = snapshot["currentStep"]
            if step["kind"] == "normal":
                self.complete_normal_step()
            elif step["kind"] == "barge_source":
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted")
                self.record("reply_final")
                self.record("playback_started")
                self.record("playback_cancelled")
            else:
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted")
                self.record("barge_in_accepted")
                self.record("reply_final")
                self.record("playback_started")
                self.record("playback_completed")
                self.record("barge_in_continuity")
                self.manager.confirm(
                    session_id=session["sessionId"],
                    step_id=step["id"],
                    heard=True,
                )

        self.record("playback_completed")

        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["status"], "failed")
        self.assertEqual(snapshot["lastFailureCode"], "silence_activity_detected")

    def test_discord_turn_summary_maps_playback_latency_and_outcome_events(self) -> None:
        payload = {
            "validation_session_id": "validation-1",
            "validation_step_id": "02-listening",
            "turn_id": "turn-1",
            "playback_started": True,
            "playback_completed": True,
            "playback_cancelled": False,
            "playback_first_packet_ms": 1234.5,
            "total_ms": 9876.5,
        }
        with patch(
            "evelyn_core.voice_validation.emit_voice_validation_event"
        ) as emit:
            observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

        event_names = [call.args[1] for call in emit.call_args_list]
        self.assertEqual(
            event_names,
            [
                "turn_accepted",
                "reply_final",
                "playback_started",
                "playback_completed",
            ],
        )
        playback_call = emit.call_args_list[2]
        self.assertEqual(playback_call.kwargs["latencyMs"], 1234.5)

    def test_full_local_suite_passes_and_report_contains_no_prompt_or_transcript(self) -> None:
        session = self.start()
        while self.manager.snapshot()["state"] == "running":
            snapshot = self.manager.snapshot()
            step = snapshot["currentStep"]
            if step["kind"] == "normal":
                self.complete_normal_step()
            elif step["kind"] == "barge_source":
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted")
                self.record("reply_final")
                self.record("playback_started", latencyMs=1500)
                self.record("playback_cancelled")
            elif step["kind"] == "barge_interrupt":
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted")
                self.record("barge_in_accepted")
                self.record("reply_final")
                self.record("playback_started", latencyMs=1400)
                self.record("playback_completed")
                self.record("barge_in_continuity", status="success")
                self.manager.confirm(
                    session_id=snapshot["sessionId"],
                    step_id=step["id"],
                    heard=True,
                )
            elif step["kind"] == "silence":
                self.clock.advance(16)
                self.manager.snapshot()
            else:
                self.fail(f"unknown step kind: {step['kind']}")

        final = self.manager.snapshot()
        self.assertEqual(final["state"], "passed")
        report_path = self.root / "voice_validation" / "reports" / f"{session['sessionId']}.json"
        report_text = report_path.read_text(encoding="utf-8")
        report = json.loads(report_text)
        self.assertFalse(report["privacy"]["rawAudioStored"])
        self.assertFalse(report["privacy"]["transcriptStored"])
        self.assertNotIn("prompt", report_text)
        self.assertNotIn('"transcript":', report_text.lower())
        self.assertNotIn("이블린", report_text)


if __name__ == "__main__":
    unittest.main()
