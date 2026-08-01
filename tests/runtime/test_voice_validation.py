from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import voice_validation as voice_validation_module  # noqa: E402
from evelyn_core.voice_validation import (  # noqa: E402
    MAX_ATTEMPTS,
    SUITE_ID,
    VoiceValidationManager,
    active_validation_context,
    emit_transcript_validation_event,
    emit_voice_validation_event,
    observe_turn_trace_for_voice_validation,
    resolve_discord_validation_target,
    sanitize_validation_event,
    transcript_match,
    validation_attempt_binding_is_current,
    validation_transcript_admission_status,
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
            discord_target=(
                {"guildId": "7", "channelId": "9"}
                if "discord" in surfaces
                else None
            ),
        )
        self.assertTrue(result["ok"], result)
        return result["session"]

    def test_transcript_admission_status_is_read_only_and_content_free(self) -> None:
        self.start()
        context = active_validation_context(
            surface="local",
            root=self.root,
            now=self.clock,
        )
        self.assertIsNotNone(context)
        binding = {
            "sessionId": context["sessionId"],
            "stepId": context["stepId"],
            "attempt": context["attempt"],
            "attemptId": context["attemptId"],
        }
        active_path = self.root / "voice_validation" / "active.json"
        before = active_path.read_bytes()

        matched = validation_transcript_admission_status(
            "local",
            "이블린",
            binding,
            root=self.root,
            now=self.clock,
        )
        mismatched = validation_transcript_admission_status(
            "local",
            "주변 사람의 unrelated private sentence",
            binding,
            root=self.root,
            now=self.clock,
        )

        self.assertTrue(matched["current"])
        self.assertTrue(matched["matched"])
        self.assertEqual(matched["reason"], "validation_transcript_matched")
        self.assertTrue(mismatched["current"])
        self.assertFalse(mismatched["matched"])
        self.assertEqual(mismatched["reason"], "validation_transcript_mismatch")
        self.assertEqual(active_path.read_bytes(), before)
        public_text = json.dumps([matched, mismatched], ensure_ascii=False)
        self.assertNotIn("unrelated private sentence", public_text)
        self.assertNotIn(context["attemptId"], public_text)

    def test_attempt_rotation_uses_one_canonical_snapshot(self) -> None:
        self.start()
        active_path = self.root / "voice_validation" / "active.json"
        old_active = json.loads(active_path.read_text(encoding="utf-8"))
        old_step = old_active["currentStep"]
        old_internal_step = next(
            step
            for step in old_active["_steps"]
            if step["id"] == old_step["id"]
        )
        old_binding = {
            "sessionId": old_active["sessionId"],
            "stepId": old_step["id"],
            "attempt": old_internal_step["attempt"],
            "attemptId": old_internal_step["_attemptId"],
        }
        self.record("stt_final", transcript=old_internal_step["prompt"])
        self.record("turn_accepted", eventId="accepted-a")
        self.record("turn_accepted", eventId="accepted-b")
        failed = self.manager.snapshot()
        retried = self.manager.retry(
            session_id=failed["sessionId"],
            step_id=failed["currentStep"]["id"],
            attempt=failed["currentStep"]["attempt"],
        )
        self.assertTrue(retried["ok"], retried)
        new_active = json.loads(active_path.read_text(encoding="utf-8"))

        with patch.object(
            voice_validation_module,
            "_safe_json_read",
            side_effect=[deepcopy(new_active), deepcopy(old_active)],
        ) as read_active:
            current = validation_attempt_binding_is_current(
                old_binding,
                surface="local",
                root=self.root,
                now=self.clock,
                reject_unbound_when_active=True,
            )

        self.assertFalse(current)
        self.assertEqual(read_active.call_count, 1)

        with patch.object(
            voice_validation_module,
            "_safe_json_read",
            side_effect=[deepcopy(new_active), deepcopy(old_active)],
        ) as read_active:
            transcript = validation_transcript_admission_status(
                "local",
                old_internal_step["prompt"],
                old_binding,
                root=self.root,
                now=self.clock,
            )

        self.assertFalse(transcript["current"])
        self.assertFalse(transcript["matched"])
        self.assertEqual(read_active.call_count, 1)

    def record(self, event: str, **payload):
        session = self.manager.snapshot()
        step = session["currentStep"]
        context = active_validation_context(
            surface=step["surface"],
            root=self.root,
            prefer_interrupt=False,
            now=self.clock,
        )
        self.assertIsNotNone(context)
        result = self.manager.record_event(
            {
                "event": event,
                "surface": step["surface"],
                "stepId": step["id"],
                "attemptId": context["attemptId"],
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
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started", latencyMs=1200)
        self.record("playback_completed")
        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
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

    def test_attempt_binding_guard_rejects_rotation_partial_and_abort(self) -> None:
        session = self.start()
        context = active_validation_context(
            surface="local",
            root=self.root,
            now=self.clock,
        )
        binding = {
            "validation_session_id": context["sessionId"],
            "validation_step_id": context["stepId"],
            "validation_attempt_id": context["attemptId"],
        }
        self.assertTrue(
            validation_attempt_binding_is_current(
                binding,
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )
        self.assertTrue(
            validation_attempt_binding_is_current(
                {},
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )
        self.assertFalse(
            validation_attempt_binding_is_current(
                {**binding, "validation_attempt_id": "stale-attempt"},
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )
        self.assertFalse(
            validation_attempt_binding_is_current(
                {"validation_session_id": context["sessionId"]},
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )

        aborted = self.manager.abort(session_id=session["sessionId"])
        self.assertTrue(aborted["ok"], aborted)
        self.assertFalse(
            validation_attempt_binding_is_current(
                binding,
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )

    def test_attempt_binding_guard_can_reject_unbound_work_during_active_surface(
        self,
    ) -> None:
        session = self.start()

        self.assertTrue(
            validation_attempt_binding_is_current(
                {},
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )
        self.assertFalse(
            validation_attempt_binding_is_current(
                {},
                surface="local",
                root=self.root,
                now=self.clock,
                reject_unbound_when_active=True,
            )
        )
        self.assertTrue(
            validation_attempt_binding_is_current(
                None,
                surface="discord",
                root=self.root,
                now=self.clock,
                reject_unbound_when_active=True,
            )
        )

        self.manager.abort(session_id=session["sessionId"])
        self.assertTrue(
            validation_attempt_binding_is_current(
                {},
                surface="local",
                root=self.root,
                now=self.clock,
                reject_unbound_when_active=True,
            )
        )

    def test_wrong_schema_active_file_is_not_an_active_validation_context(
        self,
    ) -> None:
        self.start()
        active_path = self.root / "voice_validation" / "active.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["schema"] = "voice_validation.session.v0"
        active_path.write_text(json.dumps(active), encoding="utf-8")

        self.assertIsNone(
            active_validation_context(
                surface="local",
                root=self.root,
                now=self.clock,
            )
        )
        self.assertTrue(
            validation_attempt_binding_is_current(
                {},
                surface="local",
                root=self.root,
                now=self.clock,
                reject_unbound_when_active=True,
            )
        )

    def test_discord_target_resolution_and_session_binding_fail_closed(self) -> None:
        unavailable = resolve_discord_validation_target(
            {
                "services": [
                    {
                        "id": "discord_bot",
                        "checks": [
                            {
                                "kind": "artifact_json",
                                "ok": True,
                                "payload": {
                                    "voiceConnections": [
                                        {
                                            "guildId": 7,
                                            "channelId": 9,
                                            "connected": True,
                                            "listening": False,
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                ]
            }
        )
        ambiguous = resolve_discord_validation_target(
            {
                "services": [
                    {
                        "id": "discord_bot",
                        "checks": [
                            {
                                "kind": "artifact_json",
                                "ok": True,
                                "payload": {
                                    "voiceConnections": [
                                        {
                                            "guildId": 7,
                                            "channelId": 9,
                                            "connected": True,
                                            "listening": True,
                                        },
                                        {
                                            "guildId": 8,
                                            "channelId": 10,
                                            "connected": True,
                                            "listening": True,
                                        },
                                    ]
                                },
                            }
                        ],
                    }
                ]
            }
        )
        self.assertEqual(unavailable["error"], "discord_target_unavailable")
        self.assertEqual(ambiguous["error"], "ambiguous_discord_target")
        stale = resolve_discord_validation_target(
            {
                "cache": {
                    "stale": False,
                    "lastRefreshError": "runtime_health_refresh_failed",
                },
                "services": [
                    {
                        "id": "discord_bot",
                        "checks": [
                            {
                                "kind": "artifact_json",
                                "ok": True,
                                "payload": {
                                    "voiceConnections": [
                                        {
                                            "guildId": 7,
                                            "channelId": 9,
                                            "connected": True,
                                            "listening": True,
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        )
        self.assertEqual(stale["error"], "discord_target_unavailable")

        missing = self.manager.start(
            suite=SUITE_ID,
            surfaces=("discord",),
            capabilities=READY_CAPABILITIES,
        )
        self.assertEqual(missing, {"ok": False, "error": "discord_target_unavailable"})

        session = self.start(surfaces=("discord",))
        self.assertEqual(
            session["discordTarget"],
            {"guildId": "7", "channelId": "9"},
        )
        context = active_validation_context(
            surface="discord",
            root=self.root,
            now=self.clock,
        )
        self.assertEqual(context["discordTarget"], session["discordTarget"])

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

    def test_legacy_running_session_without_attempt_binding_fails_retryably(self) -> None:
        started = self.start()
        active_path = self.root / "voice_validation" / "active.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        for step in active["_steps"]:
            step.pop("_attemptId", None)
        active_path.write_text(json.dumps(active), encoding="utf-8")

        recovered = VoiceValidationManager(root=self.root, now=self.clock)
        failed = recovered.snapshot()

        self.assertEqual(failed["state"], "running")
        self.assertEqual(failed["currentStep"]["status"], "failed")
        self.assertIn(
            "attempt_binding_migration_required",
            failed["currentStep"]["errors"],
        )

        retried = recovered.retry(
            session_id=started["sessionId"],
            step_id=failed["currentStep"]["id"],
            attempt=failed["currentStep"]["attempt"],
        )
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["session"]["currentStep"]["status"], "pending")
        self.assertEqual(retried["session"]["currentStep"]["attempt"], 2)

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
            attempt=session["currentStep"]["attempt"],
        )
        self.assertTrue(retried["ok"])
        self.assertEqual(retried["session"]["currentStep"]["status"], "pending")

    def test_duplicate_reply_started_fails_attempt(self) -> None:
        self.start()
        self.record("reply_started", eventId="reply-started-1")
        self.record("reply_started", eventId="reply-started-2")

        session = self.manager.snapshot()
        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertEqual(session["lastFailureCode"], "duplicate_turn_or_playback")

    def test_explicit_event_id_is_deduplicated_per_attempt_binding(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("turn_accepted", eventId="shared-event-id")
        self.record("turn_accepted", eventId="attempt-1-duplicate")
        failed = self.manager.snapshot()["currentStep"]
        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=failed["attempt"],
        )
        self.assertTrue(retried["ok"], retried)

        self.record("turn_accepted", eventId="shared-event-id")

        current = self.manager.snapshot()["currentStep"]
        self.assertEqual(current["events"]["turn_accepted"], 1)

    def test_completed_reply_without_reply_started_cannot_pass(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        confirmed = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
            heard=True,
        )
        self.assertEqual(confirmed["session"]["currentStep"]["id"], step["id"])

        self.clock.advance(3)
        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["status"], "failed")
        self.assertEqual(
            snapshot["lastFailureCode"],
            "orphan_or_incomplete_playback",
        )

    def test_retry_rejects_late_event_from_prior_attempt_after_restart(self) -> None:
        session = self.start()
        old_context = active_validation_context(
            surface="local",
            root=self.root,
            now=self.clock,
        )
        self.assertIsNotNone(old_context)
        self.record("reply_started", eventId="attempt-1-start-1")
        self.record("reply_started", eventId="attempt-1-start-2")
        failed = self.manager.snapshot()
        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=failed["currentStep"]["id"],
            attempt=failed["currentStep"]["attempt"],
        )
        self.assertTrue(retried["ok"], retried)
        new_context = active_validation_context(
            surface="local",
            root=self.root,
            now=self.clock,
        )
        self.assertNotEqual(old_context["attemptId"], new_context["attemptId"])

        events_path = (
            self.root
            / "voice_validation"
            / "events"
            / f"{session['sessionId']}.jsonl"
        )
        stale = {
            "event": "turn_accepted",
            "eventId": "late-attempt-1-accepted",
            "at": self.clock(),
            "surface": "local",
            "sessionId": session["sessionId"],
            "stepId": failed["currentStep"]["id"],
            "attempt": old_context["attempt"],
            "attemptId": old_context["attemptId"],
            "turnId": "turn-attempt-1",
        }
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stale) + "\n")

        recovered = VoiceValidationManager(root=self.root, now=self.clock)
        current = recovered.snapshot()["currentStep"]
        self.assertEqual(current["attempt"], 2)
        self.assertEqual(current["events"], {})
        self.assertNotIn("attemptId", current)

    def test_delayed_retry_and_confirm_cannot_cross_attempt_revision(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("reply_started", eventId="attempt-1-start-1")
        self.record("reply_started", eventId="attempt-1-start-2")
        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
        )
        self.assertTrue(retried["ok"], retried)

        delayed_retry = self.manager.retry(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
        )
        self.assertFalse(delayed_retry["ok"])
        self.assertEqual(
            delayed_retry["error"],
            "validation_attempt_revision_mismatch",
        )

        stale_event = self.manager.record_event(
            {
                "event": "turn_accepted",
                "surface": "local",
                "stepId": step["id"],
                "attemptId": active_validation_context(
                    surface="local",
                    root=self.root,
                    now=self.clock,
                )["attemptId"] + "-stale",
            }
        )
        self.assertFalse(stale_event["ok"])
        self.assertEqual(
            stale_event["error"],
            "validation_attempt_binding_mismatch",
        )

        current = self.manager.snapshot()["currentStep"]
        self.record("stt_final", transcript=current["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        delayed_confirm = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=current["id"],
            attempt=step["attempt"],
            heard=True,
        )
        self.assertFalse(delayed_confirm["ok"])
        self.assertEqual(
            delayed_confirm["error"],
            "validation_attempt_revision_mismatch",
        )
        accepted_confirm = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=current["id"],
            attempt=current["attempt"],
            heard=True,
        )
        self.assertTrue(accepted_confirm["ok"], accepted_confirm)

    def test_confirm_v1_omission_is_only_compatible_with_first_attempt(self) -> None:
        session = self.start()
        first = session["currentStep"]
        self.record("stt_final", transcript=first["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")

        explicit_null = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=first["id"],
            attempt=None,
            heard=True,
        )
        self.assertFalse(explicit_null["ok"])
        self.assertEqual(
            explicit_null["error"],
            "validation_attempt_revision_mismatch",
        )
        compatible = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=first["id"],
            heard=True,
        )
        self.assertTrue(compatible["ok"], compatible)

        second = self.manager.snapshot()["currentStep"]
        self.record("stt_final", transcript="완전히 다른 말")
        failed = self.manager.snapshot()["currentStep"]
        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=second["id"],
            attempt=failed["attempt"],
        )
        self.assertTrue(retried["ok"], retried)
        current = retried["session"]["currentStep"]
        self.assertEqual(current["attempt"], 2)

        self.record("stt_final", transcript=current["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        omitted_after_retry = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=current["id"],
            heard=True,
        )
        self.assertFalse(omitted_after_retry["ok"])
        self.assertEqual(
            omitted_after_retry["error"],
            "validation_attempt_revision_mismatch",
        )
        explicit_current = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=current["id"],
            attempt=current["attempt"],
            heard=True,
        )
        self.assertTrue(explicit_current["ok"], explicit_current)

    def test_confirm_manager_rejects_non_boolean_heard_without_mutation(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")

        for invalid in ("true", "false", 1, 0, None, [], {}):
            with self.subTest(heard=invalid):
                result = self.manager.confirm(
                    session_id=session["sessionId"],
                    step_id=step["id"],
                    attempt=step["attempt"],
                    heard=invalid,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"], "heard_boolean_required")
                current = self.manager.snapshot()["currentStep"]
                self.assertEqual(current["id"], step["id"])
                self.assertFalse(current["heard"])

        accepted = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
            heard=True,
        )
        self.assertTrue(accepted["ok"], accepted)

    def test_attempt_revision_rejects_fractional_bool_and_nonfinite_values(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("reply_started", eventId="attempt-1-start-1")
        self.record("reply_started", eventId="attempt-1-start-2")

        for invalid in (1.9, True, float("nan"), float("inf")):
            with self.subTest(attempt=invalid):
                result = self.manager.retry(
                    session_id=session["sessionId"],
                    step_id=step["id"],
                    attempt=invalid,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(
                    result["error"],
                    "validation_attempt_revision_mismatch",
                )

    def test_barge_source_retry_rotates_and_clears_paired_interrupt_attempt(self) -> None:
        session = self.start()
        for _ in range(6):
            self.complete_normal_step()
        source = self.manager.snapshot()["currentStep"]
        paired_before = active_validation_context(
            surface="local",
            root=self.root,
            prefer_interrupt=True,
            now=self.clock,
        )
        paired_event = self.manager.record_event(
            {
                "event": "barge_in_accepted",
                "surface": "local",
                "stepId": source["interruptStepId"],
                "attemptId": paired_before["attemptId"],
            }
        )
        self.assertTrue(paired_event["ok"], paired_event)
        self.record("reply_started", eventId="source-attempt-start-1")
        self.record("reply_started", eventId="source-attempt-start-2")

        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=source["id"],
            attempt=source["attempt"],
        )
        self.assertTrue(retried["ok"], retried)
        paired_after = active_validation_context(
            surface="local",
            root=self.root,
            prefer_interrupt=True,
            now=self.clock,
        )
        self.assertEqual(paired_before["attempt"], 1)
        self.assertEqual(paired_after["attempt"], 1)
        self.assertNotEqual(
            paired_before["attemptId"],
            paired_after["attemptId"],
        )
        active = json.loads(
            (self.root / "voice_validation" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        paired_step = next(
            step
            for step in active["_steps"]
            if step["id"] == source["interruptStepId"]
            and step["surface"] == "local"
        )
        self.assertEqual(paired_step["events"], {})
        self.assertEqual(paired_step["errors"], [])
        self.assertFalse(paired_step["heard"])

    def test_barge_interrupt_retry_rewinds_and_rotates_full_pair(self) -> None:
        session = self.start()
        for _ in range(6):
            self.complete_normal_step()
        source = self.manager.snapshot()["currentStep"]
        self.record("stt_final", transcript=source["prompt"])
        self.record("turn_accepted", turnId="turn-source-1")
        self.record(
            "tts_interrupt",
            turnId="turn-source-1",
            sourceTurnId="turn-source-1",
            qualified=True,
        )
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_cancelled")
        interrupt = self.manager.snapshot()["currentStep"]
        self.assertEqual(interrupt["kind"], "barge_interrupt")
        private_before = json.loads(
            (self.root / "voice_validation" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        source_before = next(
            step
            for step in private_before["_steps"]
            if step["surface"] == "local" and step["id"] == source["id"]
        )
        interrupt_before = active_validation_context(
            surface="local",
            root=self.root,
            prefer_interrupt=True,
            now=self.clock,
        )
        self.record("reply_started", eventId="interrupt-start-1")
        self.record("reply_started", eventId="interrupt-start-2")
        failed = self.manager.snapshot()["currentStep"]

        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=failed["id"],
            attempt=failed["attempt"],
        )

        self.assertTrue(retried["ok"], retried)
        rewound = retried["session"]["currentStep"]
        self.assertEqual(rewound["id"], source["id"])
        self.assertEqual(rewound["attempt"], source["attempt"] + 1)
        self.assertEqual(rewound["events"], {})
        source_after = active_validation_context(
            surface="local",
            root=self.root,
            prefer_interrupt=False,
            now=self.clock,
        )
        interrupt_after = active_validation_context(
            surface="local",
            root=self.root,
            prefer_interrupt=True,
            now=self.clock,
        )
        self.assertNotEqual(source_before["_attemptId"], source_after["attemptId"])
        self.assertNotEqual(
            interrupt_before["attemptId"],
            interrupt_after["attemptId"],
        )
        self.assertEqual(interrupt_after["attempt"], interrupt["attempt"] + 1)

    def test_failed_paired_interrupt_blocks_source_advance_and_suite_pass(self) -> None:
        self.start()
        for _ in range(6):
            self.complete_normal_step()
        source = self.manager.snapshot()["currentStep"]
        interrupt_context = active_validation_context(
            surface="local",
            root=self.root,
            prefer_interrupt=True,
            now=self.clock,
        )
        failed_interrupt = self.manager.record_event(
            {
                "event": "error",
                "surface": "local",
                "stepId": interrupt_context["stepId"],
                "attemptId": interrupt_context["attemptId"],
                "errorCode": "interrupt_pipeline_failed",
            }
        )
        self.assertTrue(failed_interrupt["ok"], failed_interrupt)

        source_turn_id = "turn-source-with-failed-pair"
        self.record("stt_final", transcript=source["prompt"])
        self.record("turn_accepted", turnId=source_turn_id)
        self.record(
            "tts_interrupt",
            turnId=source_turn_id,
            sourceTurnId=source_turn_id,
            qualified=True,
        )
        self.record("reply_started")
        self.record("playback_started")
        self.record("playback_cancelled")

        blocked = self.manager.snapshot()
        self.assertEqual(blocked["state"], "running")
        self.assertEqual(blocked["currentStep"]["id"], interrupt_context["stepId"])
        self.assertEqual(blocked["currentStep"]["status"], "failed")
        self.assertEqual(blocked["summary"]["stepsPassed"], 7)

    def test_stt_mismatch_fails_attempt_immediately(self) -> None:
        self.start()

        self.record("stt_final", transcript="완전히 다른 말")

        session = self.manager.snapshot()
        self.assertEqual(session["currentStep"]["status"], "failed")
        self.assertEqual(session["lastFailureCode"], "stt_mismatch")

    def test_normal_step_with_extra_interrupt_evidence_cannot_pass(self) -> None:
        session = self.start()
        step = session["currentStep"]
        turn_id = "turn-normal-with-interrupt"
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted", turnId=turn_id)
        self.record(
            "tts_interrupt",
            turnId=turn_id,
            sourceTurnId=turn_id,
            qualified=True,
        )
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        confirmed = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
            heard=True,
        )
        self.assertTrue(confirmed["ok"], confirmed)
        self.assertEqual(confirmed["session"]["currentStep"]["status"], "pending")

        self.clock.advance(3)
        failed = self.manager.snapshot()
        self.assertEqual(failed["currentStep"]["status"], "failed")
        self.assertEqual(
            failed["lastFailureCode"],
            "orphan_or_incomplete_playback",
        )

    def test_conflicting_playback_terminal_events_fail_attempt(self) -> None:
        self.start()
        self.record("stt_final", transcript="이블린")
        self.record("turn_accepted")
        self.record("reply_started")
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

    def test_retry_requires_failure_and_is_limited_to_three_attempts(self) -> None:
        session = self.start()
        step_id = session["currentStep"]["id"]
        pending = self.manager.retry(
            session_id=session["sessionId"],
            step_id=step_id,
            attempt=1,
        )
        self.assertFalse(pending["ok"])
        self.assertEqual(pending["error"], "validation_step_not_failed")
        for expected_attempt in range(2, MAX_ATTEMPTS + 1):
            self.record("stt_final", transcript="완전히 다른 말")
            failed = self.manager.snapshot()["currentStep"]
            result = self.manager.retry(
                session_id=session["sessionId"],
                step_id=step_id,
                attempt=failed["attempt"],
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["session"]["attempt"], expected_attempt)

        self.record("stt_final", transcript="완전히 다른 말")
        exhausted = self.manager.snapshot()
        self.assertEqual(exhausted["state"], "failed")
        self.assertEqual(exhausted["currentStep"]["attempt"], MAX_ATTEMPTS)

    def test_retry_rejects_a_non_current_step(self) -> None:
        session = self.start()
        completed_step_id = session["currentStep"]["id"]
        self.complete_normal_step()

        result = self.manager.retry(
            session_id=session["sessionId"],
            step_id=completed_step_id,
            attempt=1,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_step_not_current")
        self.assertEqual(self.manager.snapshot()["currentStep"]["id"], "02-listening")

    def test_retry_does_not_reuse_prior_barge_interrupt_provenance(self) -> None:
        session = self.start()
        for _ in range(6):
            self.complete_normal_step()
        step = self.manager.snapshot()["currentStep"]
        self.record("turn_accepted", turnId="turn-old")
        self.record(
            "tts_interrupt",
            turnId="turn-old",
            sourceTurnId="turn-old",
            qualified=True,
        )
        self.record("reply_started", eventId="old-reply-1")
        self.record("reply_started", eventId="old-reply-2")
        failed = self.manager.snapshot()
        self.assertEqual(failed["currentStep"]["status"], "failed")

        retried = self.manager.retry(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=failed["currentStep"]["attempt"],
        )
        self.assertTrue(retried["ok"], retried)
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted", turnId="turn-new")
        self.record("reply_started")
        self.record("playback_started")
        self.record("playback_cancelled")
        self.clock.advance(3)

        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["status"], "failed")
        self.assertEqual(
            snapshot["lastFailureCode"],
            "orphan_or_incomplete_cancelled_playback",
        )

    def test_barge_interrupt_provenance_survives_manager_restart(self) -> None:
        self.start()
        for _ in range(6):
            self.complete_normal_step()
        step = self.manager.snapshot()["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted", turnId="turn-source-restart")
        self.record(
            "tts_interrupt",
            turnId="turn-source-restart",
            sourceTurnId="turn-source-restart",
            qualified=True,
        )

        recovered = VoiceValidationManager(root=self.root, now=self.clock)
        recovered_context = active_validation_context(
            surface="local",
            root=self.root,
            now=self.clock,
        )
        for event in ("reply_started", "playback_started", "playback_cancelled"):
            result = recovered.record_event(
                {
                    "event": event,
                    "surface": "local",
                    "stepId": step["id"],
                    "attemptId": recovered_context["attemptId"],
                }
            )
            self.assertTrue(result["ok"], result)

        self.assertEqual(
            recovered.snapshot()["currentStep"]["id"],
            "08-barge-interrupt",
        )

    def test_heard_confirmation_requires_completed_playback(self) -> None:
        session = self.start()
        step = session["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")

        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
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
                self.manager.retry(
                    session_id=session["sessionId"],
                    step_id=step_id,
                    attempt=snapshot["currentStep"]["attempt"],
                )
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
                attempt=session["currentStep"]["attempt"],
                heard=True,
            ),
            "retry": lambda manager, session: manager.retry(
                session_id=session["sessionId"],
                step_id=session["currentStep"]["id"],
                attempt=session["currentStep"]["attempt"],
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
        current_context = active_validation_context(
            surface="local",
            root=self.root,
            now=self.clock,
        )
        self.assertIsNotNone(current_context)
        self.assertEqual(current_context["kind"], "normal")
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
        active["_stepIndex"] = 6
        active["_surfaceIndex"] = 0
        active["attempt"] = active["_steps"][6]["attempt"]
        active["currentStep"] = {
            key: deepcopy(active["_steps"][6].get(key))
            for key in (
                "id",
                "kind",
                "prompt",
                "silenceSec",
                "silenceStartedAt",
                "surface",
                "status",
                "attempt",
                "events",
                "errors",
                "latencyMs",
                "match",
                "heard",
            )
        }
        active["currentStep"]["interruptStepId"] = "08-barge-interrupt"
        active["currentStep"]["interruptPrompt"] = active["_steps"][7]["prompt"]
        active_path.write_text(json.dumps(active), encoding="utf-8")

        source_context = active_validation_context(
            surface="local",
            root=paired_root,
            now=self.clock,
        )
        interrupt_context = active_validation_context(
            surface="local",
            root=paired_root,
            prefer_interrupt=True,
            now=self.clock,
        )
        self.assertEqual(source_context["kind"], "barge_source")
        self.assertEqual(interrupt_context["kind"], "barge_interrupt")

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

    def test_matched_discord_transcript_binds_private_source_guild_and_turn(self) -> None:
        session = self.start(surfaces=("discord",))
        step = session["currentStep"]
        context = active_validation_context(
            surface="discord",
            root=self.root,
            now=self.clock,
        )

        with patch("evelyn_core.voice_validation.time.time", new=self.clock):
            emitted = emit_transcript_validation_event(
                "discord",
                step["prompt"],
                root=self.root,
                session_id=session["sessionId"],
                step_id=step["id"],
                attempt_id=context["attemptId"],
                guildId=11,
                turnId="turn-source-1",
            )
        bound = active_validation_context(
            surface="discord",
            root=self.root,
            now=self.clock,
        )

        self.assertIsNotNone(emitted)
        self.assertEqual(bound["guildId"], "11")
        self.assertEqual(bound["turnId"], "turn-source-1")

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

    def test_corrupt_running_session_structure_fails_closed_on_load(self) -> None:
        session = self.start()
        active_path = self.root / "voice_validation" / "active.json"
        pristine = json.loads(active_path.read_text(encoding="utf-8"))
        corruptions = (
            lambda active: active.pop("_steps", None),
            lambda active: active.update({"currentStep": {}}),
            lambda active: active.update({"_stepIndex": 999}),
            lambda active: active["currentStep"].update({"id": "missing-step"}),
        )
        for index, corrupt in enumerate(corruptions):
            with self.subTest(index=index):
                active = deepcopy(pristine)
                corrupt(active)
                active_path.write_text(json.dumps(active), encoding="utf-8")

                self.assertIsNone(
                    active_validation_context(
                        surface="local",
                        root=self.root,
                        now=self.clock,
                    )
                )

                recovered = VoiceValidationManager(root=self.root, now=self.clock)
                snapshot = recovered.snapshot()

                self.assertEqual(snapshot["sessionId"], session["sessionId"])
                self.assertEqual(snapshot["state"], "failed")
                self.assertEqual(snapshot["failureCode"], "session_invalid")

    def test_recovery_rejects_noncanonical_suite_surface_and_step_contract(self) -> None:
        session = self.start()
        active_path = self.root / "voice_validation" / "active.json"
        pristine = json.loads(active_path.read_text(encoding="utf-8"))

        def remove_step(active):
            active["_steps"].pop()

        def change_kind(active):
            active["_steps"][0]["kind"] = "silence"

        def change_order(active):
            active["_steps"][0], active["_steps"][1] = (
                active["_steps"][1],
                active["_steps"][0],
            )

        def change_suite(active):
            active["suite"] = "voice-p0.tampered"

        def change_surfaces(active):
            active["surfaces"] = ["discord"]

        def change_attempt_token(active):
            active["_steps"][0]["_attemptId"] = "not-a-generated-token"

        for corrupt in (
            remove_step,
            change_kind,
            change_order,
            change_suite,
            change_surfaces,
            change_attempt_token,
        ):
            with self.subTest(corruption=corrupt.__name__):
                active = deepcopy(pristine)
                corrupt(active)
                active_path.write_text(json.dumps(active), encoding="utf-8")

                recovered = VoiceValidationManager(root=self.root, now=self.clock)
                snapshot = recovered.snapshot()

                self.assertEqual(snapshot["sessionId"], session["sessionId"])
                self.assertEqual(snapshot["state"], "failed")
                self.assertEqual(snapshot["failureCode"], "session_invalid")
                self.assertNotEqual(snapshot["state"], "passed")

    def test_recovery_rejects_terminal_pass_without_complete_evidence(self) -> None:
        session = self.start()
        active_path = self.root / "voice_validation" / "active.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["state"] = "passed"
        active["completedAt"] = self.clock()
        active_path.write_text(json.dumps(active), encoding="utf-8")

        recovered = VoiceValidationManager(root=self.root, now=self.clock)
        snapshot = recovered.snapshot()

        self.assertEqual(snapshot["sessionId"], session["sessionId"])
        self.assertEqual(snapshot["state"], "failed")
        self.assertEqual(snapshot["failureCode"], "session_invalid")

    def test_safe_uuid_session_id_remains_recoverable(self) -> None:
        self.start()
        active_path = self.root / "voice_validation" / "active.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        safe_uuid = str(uuid.uuid4())
        active["sessionId"] = safe_uuid
        active_path.write_text(json.dumps(active), encoding="utf-8")

        recovered = VoiceValidationManager(root=self.root, now=self.clock)

        self.assertEqual(recovered.snapshot()["sessionId"], safe_uuid)
        self.assertEqual(recovered.snapshot()["state"], "running")

    def test_unsafe_session_ids_never_escape_validation_artifact_root(self) -> None:
        unsafe_ids = (
            "../../escaped",
            r"..\..\escaped",
            "nested/escaped",
            str((self.root / "absolute-escaped").resolve()),
        )
        for index, unsafe_id in enumerate(unsafe_ids):
            with self.subTest(session_id=unsafe_id):
                case_root = self.root / f"unsafe-{index}"
                manager = VoiceValidationManager(root=case_root, now=self.clock)
                started = manager.start(
                    suite=SUITE_ID,
                    surfaces=("local",),
                    capabilities=READY_CAPABILITIES,
                )["session"]
                active_path = case_root / "voice_validation" / "active.json"
                active = json.loads(active_path.read_text(encoding="utf-8"))
                active["sessionId"] = unsafe_id
                active_path.write_text(json.dumps(active), encoding="utf-8")
                outside_event = case_root / "escaped.jsonl"
                outside_report = case_root / "escaped.json"
                outside_event.write_text("event-sentinel", encoding="utf-8")
                outside_report.write_text("report-sentinel", encoding="utf-8")

                recovered = VoiceValidationManager(root=case_root, now=self.clock)
                self.assertEqual(recovered.snapshot()["state"], "idle")
                self.assertIsNone(
                    active_validation_context(
                        surface="local",
                        root=case_root,
                        now=self.clock,
                    )
                )
                self.assertIsNone(
                    emit_voice_validation_event(
                        "local",
                        "turn_accepted",
                        root=case_root,
                        session_id=unsafe_id,
                        step_id=started["currentStep"]["id"],
                        now=self.clock,
                    )
                )

                manager._session = active
                self.assertIsNone(manager._events_path())
                aborted = manager.abort(session_id=unsafe_id)
                self.assertTrue(aborted["ok"], aborted)
                self.assertEqual(
                    outside_event.read_text(encoding="utf-8"),
                    "event-sentinel",
                )
                self.assertEqual(
                    outside_report.read_text(encoding="utf-8"),
                    "report-sentinel",
                )

    def test_validation_directory_symlink_outside_artifacts_is_rejected(self) -> None:
        session = self.start()
        active = json.loads(
            (self.root / "voice_validation" / "active.json").read_text(
                encoding="utf-8"
            )
        )
        artifacts_root = self.root / "artifacts-boundary"
        outside_store = self.root / "outside-validation-store"
        artifacts_root.mkdir()
        outside_store.mkdir()
        (outside_store / "active.json").write_text(
            json.dumps(active),
            encoding="utf-8",
        )
        try:
            (artifacts_root / "voice_validation").symlink_to(
                outside_store,
                target_is_directory=True,
            )
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {type(error).__name__}")

        self.assertIsNone(
            active_validation_context(
                surface="local",
                root=artifacts_root,
                now=self.clock,
            )
        )
        self.assertIsNone(
            emit_voice_validation_event(
                "local",
                "turn_accepted",
                root=artifacts_root,
                session_id=session["sessionId"],
                step_id=session["currentStep"]["id"],
                now=self.clock,
            )
        )
        self.assertFalse((outside_store / "events").exists())
        self.assertEqual(
            VoiceValidationManager(root=artifacts_root, now=self.clock).snapshot()[
                "state"
            ],
            "idle",
        )

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
        self.record("reply_started", eventId="reply-started-third")
        self.record("reply_final", eventId="reply-third")
        self.record("turn_accepted", eventId="accepted-fourth")
        self.record("stt_final", transcript=step["prompt"], eventId="stt-last")
        result = self.manager.confirm(
            session_id=session["sessionId"],
            step_id=step["id"],
            attempt=step["attempt"],
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

    def test_terminal_grace_requires_stt_but_not_heard_confirmation(self) -> None:
        self.start()
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        self.clock.advance(3)

        missing_stt = self.manager.snapshot()
        self.assertEqual(missing_stt["currentStep"]["status"], "failed")
        self.assertEqual(
            missing_stt["lastFailureCode"],
            "orphan_or_incomplete_playback",
        )

        retry = self.manager.retry(
            session_id=missing_stt["sessionId"],
            step_id=missing_stt["currentStep"]["id"],
            attempt=missing_stt["currentStep"]["attempt"],
        )
        self.assertTrue(retry["ok"], retry)
        step = retry["session"]["currentStep"]
        self.record("stt_final", transcript=step["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        self.clock.advance(3)

        awaiting_heard = self.manager.snapshot()
        self.assertEqual(awaiting_heard["currentStep"]["status"], "pending")
        self.assertFalse(awaiting_heard["currentStep"]["heard"])

    def test_barge_interrupt_terminal_requires_acceptance_and_continuity(self) -> None:
        self.start()
        for _ in range(6):
            self.complete_normal_step()
        source = self.manager.snapshot()["currentStep"]
        source_turn_id = "turn-source"
        self.record("stt_final", transcript=source["prompt"])
        self.record("turn_accepted", turnId=source_turn_id)
        self.record("tts_interrupt", qualified=True, sourceTurnId=source_turn_id)
        self.record("reply_started")
        self.record("playback_started")
        self.record("playback_cancelled")

        interrupt = self.manager.snapshot()["currentStep"]
        self.assertEqual(interrupt["kind"], "barge_interrupt")
        self.record("stt_final", transcript=interrupt["prompt"])
        self.record("turn_accepted")
        self.record("reply_started")
        self.record("reply_final")
        self.record("playback_started")
        self.record("playback_completed")
        self.clock.advance(3)

        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["status"], "failed")
        self.assertEqual(
            snapshot["lastFailureCode"],
            "orphan_or_incomplete_playback",
        )

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

    def test_silence_step_fails_on_reply_started_activity(self) -> None:
        session = self.start()
        while self.manager.snapshot()["currentStep"]["kind"] != "silence":
            snapshot = self.manager.snapshot()
            step = snapshot["currentStep"]
            if step["kind"] == "normal":
                self.complete_normal_step()
            elif step["kind"] == "barge_source":
                source_turn_id = f"turn-{step['id']}"
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted", turnId=source_turn_id)
                self.record(
                    "tts_interrupt",
                    turnId=source_turn_id,
                    sourceTurnId=source_turn_id,
                    qualified=True,
                )
                self.record("reply_started")
                self.record("reply_final")
                self.record("playback_started")
                self.record("playback_cancelled")
            else:
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted")
                self.record("barge_in_accepted")
                self.record("reply_started")
                self.record("reply_final")
                self.record("playback_started")
                self.record("playback_completed")
                self.record("barge_in_continuity")
                self.manager.confirm(
                    session_id=session["sessionId"],
                    step_id=step["id"],
                    attempt=step["attempt"],
                    heard=True,
                )

        self.record("reply_started")

        snapshot = self.manager.snapshot()
        self.assertEqual(snapshot["currentStep"]["status"], "failed")
        self.assertEqual(snapshot["lastFailureCode"], "silence_activity_detected")

    def test_discord_turn_summary_maps_playback_latency_and_outcome_events(self) -> None:
        payload = {
            "validation_session_id": "validation-1",
            "validation_step_id": "02-listening",
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-1",
            "turn_accepted": True,
            "reply_started": True,
            "reply_final": True,
            "playback_started": True,
            "playback_completed": True,
            "playback_cancelled": False,
            "playback_failed": False,
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
                "reply_started",
                "reply_final",
                "playback_started",
                "playback_completed",
            ],
        )
        playback_call = emit.call_args_list[3]
        self.assertEqual(playback_call.kwargs["latencyMs"], 1234.5)

    def test_discord_silence_ignores_only_proven_nonaccepted_filter_drops(self) -> None:
        context = {
            "sessionId": "validation-1",
            "stepId": "11-silence",
            "surface": "discord",
            "kind": "silence",
            "attemptId": "attempt-private-1",
        }
        base = {
            "validation_session_id": "validation-1",
            "validation_step_id": "11-silence",
            "validation_attempt_id": "attempt-private-1",
            "turn_id": "turn-1",
            "turn_accepted": False,
        }
        cases = (
            ({**base, "drop_reason": "vad_ignore"}, []),
            ({**base, "drop_reason": "too_short_total"}, []),
            ({**base, "drop_reason": "wake_probe_error"}, ["error"]),
            (
                {**base, "drop_reason": "vad_ignore", "turn_accepted": True},
                ["error"],
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), patch(
                "evelyn_core.voice_validation.active_validation_context",
                return_value=context,
            ), patch(
                "evelyn_core.voice_validation.emit_voice_validation_event"
            ) as emit:
                observe_turn_trace_for_voice_validation(
                    "voice_drop_summary",
                    payload,
                )
            self.assertEqual(
                [call.args[1] for call in emit.call_args_list],
                expected,
            )

    def test_discord_intentional_barge_source_cancellation_is_success_evidence(self) -> None:
        session = self.start(surfaces=("discord",))
        for _ in range(6):
            self.complete_normal_step()
        step = self.manager.snapshot()["currentStep"]
        self.assertEqual(step["kind"], "barge_source")
        self.record("stt_final", transcript=step["prompt"])
        payload = {
            "validation_session_id": session["sessionId"],
            "validation_step_id": step["id"],
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-1",
            "turn_accepted": True,
            "qualified_tts_interrupt": True,
            "reply_started": True,
            "reply_final": False,
            "playback_started": True,
            "playback_completed": False,
            "playback_cancelled": True,
            "playback_failed": False,
            "error": "cancelled",
        }
        with patch(
            "evelyn_core.voice_validation.active_validation_context",
            return_value={
                "sessionId": session["sessionId"],
                "stepId": step["id"],
                "surface": "discord",
                "kind": "barge_source",
                "attemptId": "attempt-private-1",
            },
        ), patch("evelyn_core.voice_validation.emit_voice_validation_event") as emit:
            observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

        event_names = [call.args[1] for call in emit.call_args_list]
        self.assertEqual(
            event_names,
            [
                "turn_accepted",
                "tts_interrupt",
                "reply_started",
                "playback_started",
                "playback_cancelled",
            ],
        )
        for call in emit.call_args_list:
            current_binding = active_validation_context(
                surface="discord",
                root=self.root,
                now=self.clock,
            )
            result = self.manager.record_event(
                {
                    "event": call.args[1],
                    "surface": "discord",
                    "stepId": step["id"],
                    "attemptId": current_binding["attemptId"],
                    **{
                        key: value
                        for key, value in call.kwargs.items()
                        if key not in {"session_id", "step_id"}
                    },
                }
            )
            self.assertTrue(result["ok"], result)
        self.assertEqual(self.manager.snapshot()["currentStep"]["id"], "08-barge-interrupt")

    def test_discord_barge_source_full_answer_then_cancel_preserves_both_reply_events(
        self,
    ) -> None:
        payload = {
            "validation_session_id": "validation-1",
            "validation_step_id": "07-barge-source",
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-1",
            "turn_accepted": True,
            "qualified_tts_interrupt": True,
            "reply_started": True,
            "reply_final": True,
            "playback_started": True,
            "playback_completed": False,
            "playback_cancelled": True,
            "playback_failed": False,
            "error": "cancelled",
        }
        with patch(
            "evelyn_core.voice_validation.active_validation_context",
            return_value={
                "sessionId": "validation-1",
                "stepId": "07-barge-source",
                "surface": "discord",
                "kind": "barge_source",
                "attemptId": "attempt-private-1",
            },
        ), patch("evelyn_core.voice_validation.emit_voice_validation_event") as emit:
            observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

        self.assertEqual(
            [call.args[1] for call in emit.call_args_list],
            [
                "turn_accepted",
                "tts_interrupt",
                "reply_started",
                "reply_final",
                "playback_started",
                "playback_cancelled",
            ],
        )

    def test_discord_unrelated_cancellation_emits_no_positive_evidence(self) -> None:
        payload = {
            "validation_session_id": "validation-1",
            "validation_step_id": "02-listening",
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-1",
            "turn_accepted": True,
            "reply_started": True,
            "reply_final": False,
            "playback_started": True,
            "playback_completed": False,
            "playback_cancelled": True,
            "playback_failed": False,
            "error": "cancelled",
        }
        with patch(
            "evelyn_core.voice_validation.active_validation_context",
            return_value={
                "sessionId": "validation-1",
                "stepId": "02-listening",
                "surface": "discord",
                "kind": "normal",
                "attemptId": "attempt-private-1",
            },
        ), patch("evelyn_core.voice_validation.emit_voice_validation_event") as emit:
            observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

        self.assertEqual([call.args[1] for call in emit.call_args_list], ["error"])
        self.assertEqual(emit.call_args.kwargs["errorCode"], "cancelled")

    def test_discord_barge_source_cancellation_without_causal_interrupt_fails(
        self,
    ) -> None:
        payload = {
            "validation_session_id": "validation-1",
            "validation_step_id": "07-barge-source",
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-source-1",
            "turn_accepted": True,
            "qualified_tts_interrupt": False,
            "reply_started": True,
            "reply_final": False,
            "playback_started": True,
            "playback_completed": False,
            "playback_cancelled": True,
            "playback_failed": False,
            "error": "cancelled",
        }
        with patch(
            "evelyn_core.voice_validation.active_validation_context",
            return_value={
                "sessionId": "validation-1",
                "stepId": "07-barge-source",
                "surface": "discord",
                "kind": "barge_source",
                "attemptId": "attempt-private-1",
            },
        ), patch("evelyn_core.voice_validation.emit_voice_validation_event") as emit:
            observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

        self.assertEqual([call.args[1] for call in emit.call_args_list], ["error"])
        self.assertEqual(emit.call_args.kwargs["errorCode"], "cancelled")

    def test_tts_interrupt_trace_requires_qualified_source_provenance(self) -> None:
        source_context = {
            "sessionId": "validation-1",
            "stepId": "07-barge-source",
            "surface": "discord",
            "kind": "barge_source",
            "attemptId": "attempt-source-1",
            "guildId": "11",
            "turnId": "turn-source-1",
        }
        interrupt_context = {
            "sessionId": "validation-1",
            "stepId": "08-barge-interrupt",
            "surface": "discord",
            "kind": "barge_interrupt",
            "attemptId": "attempt-interrupt-1",
        }
        cases = (
            ({"reason": "qualified_user_audio", "qualified": True}, []),
            (
                {
                    "reason": "interrupt",
                    "qualified": False,
                    "source_turn_id": "turn-source-1",
                },
                [],
            ),
            (
                {
                    "guild_id": 11,
                    "reason": "qualified_user_audio",
                    "qualified": True,
                    "source_turn_id": "turn-source-1",
                    "validation_session_id": "validation-1",
                    "validation_step_id": "07-barge-source",
                    "validation_attempt_id": "attempt-source-1",
                },
                ["barge_in_accepted"],
            ),
            (
                {
                    "guild_id": 99,
                    "reason": "qualified_user_audio",
                    "qualified": True,
                    "source_turn_id": "turn-source-1",
                    "validation_session_id": "validation-1",
                    "validation_step_id": "07-barge-source",
                    "validation_attempt_id": "attempt-source-1",
                },
                [],
            ),
            (
                {
                    "guild_id": 11,
                    "reason": "qualified_user_audio",
                    "qualified": True,
                    "source_turn_id": "turn-source-1",
                    "validation_session_id": "other-session",
                    "validation_step_id": "07-barge-source",
                    "validation_attempt_id": "attempt-source-1",
                },
                [],
            ),
            (
                {
                    "guild_id": 11,
                    "reason": "qualified_user_audio",
                    "qualified": True,
                    "source_turn_id": "turn-source-1",
                    "validation_session_id": "validation-1",
                    "validation_step_id": "07-barge-source",
                    "validation_attempt_id": "stale-attempt",
                },
                [],
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload), patch(
                "evelyn_core.voice_validation.active_validation_context",
                side_effect=lambda **kwargs: (
                    interrupt_context
                    if kwargs.get("prefer_interrupt")
                    else source_context
                ),
            ), patch(
                "evelyn_core.voice_validation.emit_voice_validation_event"
            ) as emit:
                observe_turn_trace_for_voice_validation("tts_interrupt", payload)

            self.assertEqual(
                [call.args[1] for call in emit.call_args_list],
                expected,
            )

    def test_discord_empty_reply_and_playback_failure_fail_without_hanging(self) -> None:
        base = {
            "validation_session_id": "validation-1",
            "validation_step_id": "02-listening",
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-1",
            "turn_accepted": True,
            "reply_started": False,
            "reply_final": False,
            "playback_started": False,
            "playback_completed": False,
            "playback_cancelled": False,
            "playback_failed": False,
        }
        cases = (
            (base, "error", "voice_delivery_empty"),
            (
                {**base, "reply_final": True},
                "playback_failed",
                "tts_playback_failed",
            ),
            (
                {**base, "reply_final": True, "playback_failed": True},
                "playback_failed",
                "tts_playback_failed",
            ),
        )
        for payload, expected_event, expected_code in cases:
            with self.subTest(event=expected_event), patch(
                "evelyn_core.voice_validation.emit_voice_validation_event"
            ) as emit:
                observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

            self.assertEqual(
                [call.args[1] for call in emit.call_args_list],
                [expected_event],
            )
            self.assertEqual(emit.call_args.kwargs["errorCode"], expected_code)

    def test_discord_summary_requires_typed_match_and_acceptance_proof(self) -> None:
        success = {
            "validation_session_id": "validation-1",
            "validation_step_id": "02-listening",
            "validation_attempt_id": "attempt-private-1",
            "validation_transcript_match": True,
            "turn_id": "turn-1",
            "turn_accepted": True,
            "reply_started": True,
            "reply_final": True,
            "playback_started": True,
            "playback_completed": True,
            "playback_cancelled": False,
            "playback_failed": False,
        }
        cases = (
            (
                {**success, "validation_transcript_match": False},
                "validation_transcript_not_matched",
            ),
            (
                {**success, "turn_accepted": False},
                "voice_turn_acceptance_unproven",
            ),
        )
        for payload, expected_code in cases:
            with self.subTest(error=expected_code), patch(
                "evelyn_core.voice_validation.emit_voice_validation_event"
            ) as emit:
                observe_turn_trace_for_voice_validation("voice_turn_summary", payload)

            self.assertEqual([call.args[1] for call in emit.call_args_list], ["error"])
            self.assertEqual(emit.call_args.kwargs["errorCode"], expected_code)

    def test_full_local_suite_passes_and_report_contains_no_prompt_or_transcript(self) -> None:
        session = self.start()
        while self.manager.snapshot()["state"] == "running":
            snapshot = self.manager.snapshot()
            step = snapshot["currentStep"]
            if step["kind"] == "normal":
                self.complete_normal_step()
            elif step["kind"] == "barge_source":
                source_turn_id = f"turn-{step['id']}"
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted", turnId=source_turn_id)
                self.record(
                    "tts_interrupt",
                    turnId=source_turn_id,
                    sourceTurnId=source_turn_id,
                    qualified=True,
                )
                self.record("reply_started")
                self.record("reply_final")
                self.record("playback_started", latencyMs=1500)
                self.record("playback_cancelled")
            elif step["kind"] == "barge_interrupt":
                self.record("stt_final", transcript=step["prompt"])
                self.record("turn_accepted")
                self.record("barge_in_accepted")
                self.record("reply_started")
                self.record("reply_final")
                self.record("playback_started", latencyMs=1400)
                self.record("playback_completed")
                self.record("barge_in_continuity", status="success")
                self.manager.confirm(
                    session_id=snapshot["sessionId"],
                    step_id=step["id"],
                    attempt=step["attempt"],
                    heard=True,
                )
            elif step["kind"] == "silence":
                self.clock.advance(16)
                self.manager.snapshot()
            else:
                self.fail(f"unknown step kind: {step['kind']}")

        final = self.manager.snapshot()
        self.assertEqual(final["state"], "passed")
        recovered = VoiceValidationManager(root=self.root, now=self.clock)
        self.assertEqual(recovered.snapshot()["state"], "passed")
        report_path = self.root / "voice_validation" / "reports" / f"{session['sessionId']}.json"
        report_text = report_path.read_text(encoding="utf-8")
        report = json.loads(report_text)
        self.assertFalse(report["privacy"]["rawAudioStored"])
        self.assertFalse(report["privacy"]["transcriptStored"])
        barge_reports = [
            step for step in report["steps"] if step["kind"] == "barge_source"
        ]
        self.assertTrue(barge_reports)
        self.assertTrue(all(step["reply"]["started"] == 1 for step in barge_reports))
        self.assertTrue(all(step["reply"]["final"] == 1 for step in barge_reports))
        self.assertTrue(
            all(step["interrupt"]["qualifiedTts"] == 1 for step in barge_reports)
        )
        self.assertTrue(
            all(step["interrupt"]["sourceTurnMatched"] for step in barge_reports)
        )
        self.assertNotIn("prompt", report_text)
        self.assertNotIn('"transcript":', report_text.lower())
        self.assertNotIn("이블린", report_text)


if __name__ == "__main__":
    unittest.main()
