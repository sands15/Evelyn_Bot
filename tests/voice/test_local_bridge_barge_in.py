from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_bridge_barge_in import (  # noqa: E402
    SingleOwnerPlaybackController,
    evaluate_local_barge_in,
    local_barge_source_binding_matches,
)
from evelyn_core.local_io_bridge import (  # noqa: E402
    LocalChatStreamFailure,
    LocalIoBridge,
)
from evelyn_core import local_io_bridge  # noqa: E402
from evelyn_core.voice_validation import SUITE_ID, VoiceValidationManager  # noqa: E402


def install_admission_grant(bridge: LocalIoBridge) -> AsyncMock:
    async def issue(
        text: str,
        *,
        turn_id: str,
        validation=None,
        expected_epoch=None,
    ):
        return {
            "bridgeInstanceId": bridge.bridge_instance_id,
            "turnId": turn_id,
            "originalText": text,
            "forwardText": text,
            "admissionToken": "a" * 32,
            "validation": dict(validation or {}),
            "mode": "wake_entry",
            "issuedMonotonic": local_io_bridge.time.monotonic(),
            "epoch": (
                bridge.admission_epoch
                if expected_epoch is None
                else expected_epoch
            ),
            "_botDispatched": False,
        }

    admission = AsyncMock(side_effect=issue)
    bridge._request_voice_admission = admission  # type: ignore[method-assign]
    return admission


class LocalBridgeBargeInTests(unittest.TestCase):
    def strong_meta(self):
        return {
            "duration_sec": 0.8,
            "voice_filter": {
                "vadSilent": False,
                "environmentNoise": False,
                "weakWaveform": False,
                "bodyRms": 0.025,
                "rms": 0.02,
            },
        }

    def test_strong_voice_segment_uses_shared_tts_interrupt_rules(self):
        decision = evaluate_local_barge_in(self.strong_meta(), body_rms_min=0.01)
        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "qualified_user_audio")
        self.assertTrue(decision.interrupt_meta.active_speaker_match)

    def test_weak_or_echo_segment_is_rejected(self):
        meta = self.strong_meta()
        meta["duration_sec"] = 0.2
        meta["voice_filter"]["weakWaveform"] = True
        meta["voice_filter"]["bodyRms"] = 0.001
        decision = evaluate_local_barge_in(meta, body_rms_min=0.01)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "weak_or_echo_input")

    def test_enabled_speaker_verification_rejection_blocks_interrupt(self):
        verification = SimpleNamespace(
            matched=False,
            to_dict=lambda: {"status": "rejected", "score": 0.2},
        )
        decision = evaluate_local_barge_in(
            self.strong_meta(),
            body_rms_min=0.01,
            speaker_verification=verification,
            speaker_verification_required=True,
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "speaker_verification_rejected")

    def test_required_speaker_verification_only_accepts_verified_match(self):
        verified = SimpleNamespace(
            matched=True,
            to_dict=lambda: {"status": "verified", "score": 0.9},
        )

        decision = evaluate_local_barge_in(
            self.strong_meta(),
            body_rms_min=0.01,
            speaker_verification=verified,
            speaker_verification_required=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.reason, "qualified_user_audio")

    def test_required_speaker_verification_fails_closed_for_no_result(self):
        decision = evaluate_local_barge_in(
            self.strong_meta(),
            body_rms_min=0.01,
            speaker_verification=None,
            speaker_verification_required=True,
        )

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "speaker_verification_unverified")

    def test_required_speaker_verification_fails_closed_for_unverified_statuses(self):
        for status in ("too_short", "not_enrolled", "unavailable", "error"):
            with self.subTest(status=status):
                verification = SimpleNamespace(
                    matched=None,
                    to_dict=lambda status=status: {"status": status},
                )
                decision = evaluate_local_barge_in(
                    self.strong_meta(),
                    body_rms_min=0.01,
                    speaker_verification=verification,
                    speaker_verification_required=True,
                )

                self.assertFalse(decision.accepted)
                self.assertEqual(
                    decision.reason,
                    "speaker_verification_unverified",
                )

    def test_disabled_or_non_local_speaker_verification_is_not_required(self):
        cases = (
            (False, "local_mic"),
            (True, "discord"),
            (True, ""),
        )
        for enabled, apply_to in cases:
            with (
                self.subTest(enabled=enabled, apply_to=apply_to),
                patch.object(
                    local_io_bridge,
                    "SPEAKER_VERIFICATION_ENABLED",
                    enabled,
                ),
                patch.object(
                    local_io_bridge,
                    "SPEAKER_VERIFICATION_APPLY_TO",
                    apply_to,
                ),
            ):
                self.assertFalse(
                    LocalIoBridge._speaker_verification_required_for_barge_in()
                )

    def test_local_speaker_verification_configuration_is_required(self):
        with (
            patch.object(
                local_io_bridge,
                "SPEAKER_VERIFICATION_ENABLED",
                True,
            ),
            patch.object(
                local_io_bridge,
                "SPEAKER_VERIFICATION_APPLY_TO",
                "local_mic",
            ),
        ):
            self.assertTrue(
                LocalIoBridge._speaker_verification_required_for_barge_in()
            )

    def test_worker_fails_closed_when_required_verifier_is_unavailable(self):
        async def runner() -> tuple[list[str], list[str], int]:
            bridge = LocalIoBridge()
            cancelled: list[str] = []
            self.assertTrue(
                bridge.playback_controller.claim(
                    "turn-a",
                    lambda: cancelled.append("turn-a"),
                )
            )
            bridge.active_turn_id = "turn-a"
            bridge._verify_barge_in_speaker = AsyncMock(return_value=None)
            bridge._emit_validation = Mock()
            await bridge.barge_in_queue.put((b"pcm", self.strong_meta()))
            with (
                patch.object(
                    local_io_bridge,
                    "SPEAKER_VERIFICATION_ENABLED",
                    True,
                ),
                patch.object(
                    local_io_bridge,
                    "SPEAKER_VERIFICATION_APPLY_TO",
                    "local_mic",
                ),
            ):
                worker = asyncio.create_task(bridge._barge_in_worker())
                await bridge.barge_in_queue.join()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            reasons = [
                str(call.kwargs.get("reason") or "")
                for call in bridge._emit_validation.call_args_list
                if call.args and call.args[0] == "barge_in_rejected"
            ]
            return cancelled, reasons, bridge.priority_queue.qsize()

        cancelled, reasons, priority_size = asyncio.run(runner())

        self.assertEqual(cancelled, [])
        self.assertEqual(reasons, ["speaker_verification_unverified"])
        self.assertEqual(priority_size, 0)

    def test_playback_controller_keeps_one_owner_until_release(self):
        cancelled = []
        controller = SingleOwnerPlaybackController()

        self.assertTrue(controller.claim("turn-1", lambda: cancelled.append("turn-1")))
        self.assertFalse(controller.claim("turn-2", lambda: cancelled.append("turn-2")))
        self.assertTrue(controller.request_cancel())
        self.assertFalse(controller.request_cancel())
        self.assertEqual(cancelled, ["turn-1"])
        self.assertFalse(controller.release("turn-2"))
        self.assertTrue(controller.release("turn-1"))
        self.assertTrue(controller.claim("turn-2", lambda: cancelled.append("turn-2")))

    def test_delayed_barge_from_owner_a_cannot_cancel_owner_b(self):
        cancelled = []
        controller = SingleOwnerPlaybackController()
        self.assertTrue(controller.claim("turn-a", lambda: cancelled.append("turn-a")))
        token_a = controller.owner_token
        self.assertTrue(controller.release("turn-a"))
        self.assertTrue(controller.claim("turn-b", lambda: cancelled.append("turn-b")))

        self.assertFalse(
            controller.request_cancel(
                expected_owner_id="turn-a",
                expected_owner_token=token_a,
            )
        )
        self.assertEqual(cancelled, [])
        self.assertEqual(controller.owner_id, "turn-b")
        self.assertFalse(controller.cancel_requested)

    def test_source_binding_rejects_turn_validation_and_owner_generation_change(self):
        controller = SingleOwnerPlaybackController()
        self.assertTrue(controller.claim("turn-a", lambda: None))
        token_a = controller.owner_token
        source_meta = {
            "_bargeSource": {
                "turnId": "turn-a",
                "ownerId": "turn-a",
                "ownerToken": token_a,
                "validationSessionId": "validation-1",
                "validationStepId": "07-source",
                "validationAttempt": 1,
                "validationAttemptId": "attempt-a",
            }
        }
        validation = {
            "sessionId": "validation-1",
            "stepId": "07-source",
            "attempt": 1,
            "attemptId": "attempt-a",
        }
        self.assertTrue(
            local_barge_source_binding_matches(
                source_meta,
                active_turn_id="turn-a",
                active_validation=validation,
                active_owner_id=controller.owner_id,
                active_owner_token=controller.owner_token,
            )
        )
        source_meta["_bargeSource"]["interruptPairingValid"] = False
        self.assertFalse(
            local_barge_source_binding_matches(
                source_meta,
                active_turn_id="turn-a",
                active_validation=validation,
                active_owner_id=controller.owner_id,
                active_owner_token=controller.owner_token,
            )
        )
        source_meta["_bargeSource"]["interruptPairingValid"] = True

        self.assertTrue(controller.release("turn-a"))
        self.assertTrue(controller.claim("turn-b", lambda: None))
        self.assertFalse(
            local_barge_source_binding_matches(
                source_meta,
                active_turn_id="turn-b",
                active_validation=validation,
                active_owner_id=controller.owner_id,
                active_owner_token=controller.owner_token,
            )
        )

    def test_worker_rejects_delayed_a_segment_without_cancelling_b(self):
        async def runner() -> tuple[list[str], list[str], int]:
            bridge = LocalIoBridge()
            cancelled: list[str] = []
            self.assertTrue(
                bridge.playback_controller.claim(
                    "turn-a",
                    lambda: cancelled.append("turn-a"),
                )
            )
            token_a = bridge.playback_controller.owner_token
            self.assertTrue(bridge.playback_controller.release("turn-a"))
            self.assertTrue(
                bridge.playback_controller.claim(
                    "turn-b",
                    lambda: cancelled.append("turn-b"),
                )
            )
            bridge.active_turn_id = "turn-b"
            bridge.active_validation = None
            bridge._verify_barge_in_speaker = AsyncMock(
                return_value=SimpleNamespace(
                    matched=True,
                    to_dict=lambda: {"status": "verified", "score": 0.9},
                )
            )
            bridge._emit_validation = Mock()
            meta = self.strong_meta()
            meta["_bargeSource"] = {
                "turnId": "turn-a",
                "ownerId": "turn-a",
                "ownerToken": token_a,
                "validationSessionId": None,
                "validationStepId": None,
                "validationAttempt": None,
                "validationAttemptId": None,
            }
            await bridge.barge_in_queue.put((b"pcm", meta))
            worker = asyncio.create_task(bridge._barge_in_worker())
            await bridge.barge_in_queue.join()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            reasons = [
                str(call.kwargs.get("reason") or "")
                for call in bridge._emit_validation.call_args_list
                if call.args and call.args[0] == "barge_in_rejected"
            ]
            return cancelled, reasons, bridge.priority_queue.qsize()

        cancelled, rejection_reasons, priority_size = asyncio.run(runner())

        self.assertEqual(cancelled, [])
        self.assertIn("barge_in_stale_source", rejection_reasons)
        self.assertEqual(priority_size, 0)

    def test_worker_rejects_rotated_validation_attempt_before_cancelling(self):
        async def runner() -> tuple[list[str], list[str], int]:
            bridge = LocalIoBridge()
            cancelled: list[str] = []
            self.assertTrue(
                bridge.playback_controller.claim(
                    "turn-a",
                    lambda: cancelled.append("turn-a"),
                )
            )
            bridge.active_turn_id = "turn-a"
            bridge.active_validation = {
                "sessionId": "validation-1",
                "stepId": "07-barge-source",
                "attempt": 2,
                "attemptId": "current-source-attempt",
            }
            bridge._verify_barge_in_speaker = AsyncMock(
                return_value=SimpleNamespace(
                    matched=True,
                    to_dict=lambda: {"status": "verified", "score": 0.9},
                )
            )
            bridge._emit_validation = Mock()
            meta = self.strong_meta()
            meta.update(
                {
                    "validationSessionId": "validation-1",
                    "validationStepId": "08-barge-interrupt",
                    "validationAttempt": 1,
                    "validationAttemptId": "stale-interrupt-attempt",
                    "_bargeSource": {
                        "turnId": "turn-a",
                        "ownerId": "turn-a",
                        "ownerToken": bridge.playback_controller.owner_token,
                        "validationSessionId": "validation-1",
                        "validationStepId": "07-barge-source",
                        "validationAttempt": 1,
                        "validationAttemptId": "stale-source-attempt",
                        "interruptPairingValid": True,
                    },
                }
            )
            await bridge.barge_in_queue.put((b"pcm", meta))
            with patch.object(
                local_io_bridge,
                "validation_attempt_binding_is_current",
                return_value=False,
            ):
                worker = asyncio.create_task(bridge._barge_in_worker())
                await bridge.barge_in_queue.join()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            reasons = [
                str(call.kwargs.get("reason") or "")
                for call in bridge._emit_validation.call_args_list
                if call.args and call.args[0] == "barge_in_rejected"
            ]
            return cancelled, reasons, bridge.priority_queue.qsize()

        cancelled, rejection_reasons, priority_size = asyncio.run(runner())

        self.assertEqual(cancelled, [])
        self.assertIn("validation_attempt_stale", rejection_reasons)
        self.assertEqual(priority_size, 0)

    def test_mic_callback_freezes_source_a_before_deferred_enqueue_runs(self):
        class FakeMicCaptureService:
            instance = None

            def __init__(self, *, on_segment, **_kwargs) -> None:
                self.on_segment = on_segment
                self.capture_ready = True
                self.last_error = ""
                type(self).instance = self

            def start(self) -> bool:
                return True

        async def runner() -> dict:
            bridge = LocalIoBridge()
            bridge.mic_enabled = True
            interrupt_context = {
                "current": {
                    "sessionId": "validation-a",
                    "stepId": "08-interrupt-a",
                    "attempt": 1,
                    "attemptId": "interrupt-attempt-a",
                }
            }
            with (
                patch.object(
                    local_io_bridge,
                    "LocalMicCaptureService",
                    FakeMicCaptureService,
                ),
                patch.object(
                    local_io_bridge,
                    "active_validation_context",
                    side_effect=lambda **_kwargs: interrupt_context["current"],
                ),
            ):
                await bridge._start_mic()
                self.assertTrue(
                    bridge.playback_controller.claim("turn-a", lambda: None)
                )
                bridge.active_turn_id = "turn-a"
                bridge.active_validation = {
                    "sessionId": "validation-a",
                    "stepId": "07-a",
                    "attempt": 1,
                    "attemptId": "attempt-a",
                }
                bridge.speaking = True
                with bridge._barge_source_lock:
                    bridge._barge_source_snapshot = {
                        "turnId": "turn-a",
                        "ownerId": "turn-a",
                        "ownerToken": bridge.playback_controller.owner_token,
                        "validationSessionId": "validation-a",
                        "validationStepId": "07-a",
                        "validationAttempt": 1,
                        "validationAttemptId": "attempt-a",
                    }
                assert FakeMicCaptureService.instance is not None
                FakeMicCaptureService.instance.on_segment(b"pcm", self.strong_meta())

                interrupt_context["current"] = {
                    "sessionId": "validation-b",
                    "stepId": "08-interrupt-b",
                    "attempt": 1,
                    "attemptId": "interrupt-attempt-b",
                }
                self.assertTrue(bridge.playback_controller.release("turn-a"))
                self.assertTrue(
                    bridge.playback_controller.claim("turn-b", lambda: None)
                )
                bridge.active_turn_id = "turn-b"
                bridge.active_validation = None
                await asyncio.sleep(0)
                _pcm, queued_meta = bridge.barge_in_queue.get_nowait()
                return queued_meta

        queued_meta = asyncio.run(runner())
        source = dict(queued_meta["_bargeSource"])

        self.assertEqual(source["turnId"], "turn-a")
        self.assertEqual(source["ownerId"], "turn-a")
        self.assertEqual(source["validationSessionId"], "validation-a")
        self.assertEqual(source["validationAttemptId"], "attempt-a")
        self.assertTrue(source["interruptPairingValid"])
        self.assertEqual(queued_meta["validationSessionId"], "validation-a")
        self.assertEqual(queued_meta["validationStepId"], "08-interrupt-a")
        self.assertEqual(queued_meta["validationAttemptId"], "interrupt-attempt-a")

    def test_barge_worker_records_causal_interrupt_without_synthetic_final(self):
        source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "local_io_bridge.py"
        ).read_text(encoding="utf-8")
        worker_start = source.index("    async def _barge_in_worker(self) -> None:")
        worker_end = source.index("    def _discard_pending_mic_segments", worker_start)
        worker = source[worker_start:worker_end]

        self.assertNotIn("self._mark_reply_final_once()", worker)
        self.assertIn('"tts_interrupt"', worker)
        self.assertIn("sourceTurnId=original_turn_id", worker)
        self.assertIn("qualified=True", worker)
        self.assertIn("barge_in_stale_source", worker)
        self.assertIn("expected_owner_token=source_owner_token", worker)
        self.assertNotIn("self.active_turn_task.cancel()", worker)

    def test_stream_failure_after_playback_started_never_falls_back_to_duplicate_audio(self):
        async def runner() -> tuple[LocalIoBridge, list[str]]:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()
            bridge._transcribe = AsyncMock(return_value="hello")

            async def fail_after_audio(_text: str, *, grant: dict) -> dict:
                grant["_botDispatched"] = True
                bridge.playback_started_for_turn = True
                raise RuntimeError("stream failed after first audio")

            bridge._chat_stream_and_speak = AsyncMock(side_effect=fail_after_audio)
            bridge._chat = AsyncMock(return_value="duplicate fallback")
            bridge._speak = AsyncMock()
            bridge._emit_validation = Mock()
            install_admission_grant(bridge)
            with (
                patch.object(local_io_bridge, "LOCAL_BRIDGE_STREAMING_TTS_ENABLED", True),
                patch.object(local_io_bridge, "LOCAL_BRIDGE_TTS_ENABLED", True),
            ):
                await bridge._handle_segment(b"pcm", {"turnId": "turn-a"})
            events = [call.args[0] for call in bridge._emit_validation.call_args_list]
            return bridge, events

        bridge, events = asyncio.run(runner())

        bridge._chat.assert_not_awaited()
        bridge._speak.assert_not_awaited()
        self.assertIn("playback_failed", events)
        self.assertIn("error", events)
        self.assertLess(events.index("playback_failed"), events.index("error"))

    def test_stream_failure_after_bot_dispatch_never_falls_back_without_pcm(self):
        async def runner() -> tuple[LocalIoBridge, list[str]]:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()
            bridge._transcribe = AsyncMock(return_value="hello")

            async def fail_after_dispatch(_text: str, *, grant: dict) -> dict:
                grant["_botDispatched"] = True
                raise LocalChatStreamFailure(bot_dispatched=True)

            bridge._chat_stream_and_speak = AsyncMock(side_effect=fail_after_dispatch)
            bridge._chat = AsyncMock(return_value="duplicate fallback")
            bridge._speak = AsyncMock()
            bridge._emit_validation = Mock()
            install_admission_grant(bridge)
            with (
                patch.object(local_io_bridge, "LOCAL_BRIDGE_STREAMING_TTS_ENABLED", True),
                patch.object(local_io_bridge, "LOCAL_BRIDGE_TTS_ENABLED", True),
            ):
                await bridge._handle_segment(b"pcm", {"turnId": "turn-a"})
            events = [call.args[0] for call in bridge._emit_validation.call_args_list]
            return bridge, events

        bridge, events = asyncio.run(runner())

        self.assertFalse(bridge.playback_started_for_turn)
        bridge._chat.assert_not_awaited()
        bridge._speak.assert_not_awaited()
        self.assertIn("playback_failed", events)
        self.assertIn("error", events)

    def test_stream_failure_before_bot_dispatch_falls_back_exactly_once(self):
        async def runner() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()
            bridge._transcribe = AsyncMock(return_value="hello")

            async def fail_before_dispatch(_text: str, *, grant: dict) -> dict:
                self.assertFalse(grant["_botDispatched"])
                raise LocalChatStreamFailure(bot_dispatched=False)

            bridge._chat_stream_and_speak = AsyncMock(
                side_effect=fail_before_dispatch
            )
            bridge._chat = AsyncMock(return_value="single fallback")
            bridge._speak = AsyncMock()
            bridge._emit_validation = Mock()
            install_admission_grant(bridge)
            with (
                patch.object(local_io_bridge, "LOCAL_BRIDGE_STREAMING_TTS_ENABLED", True),
                patch.object(local_io_bridge, "LOCAL_BRIDGE_TTS_ENABLED", True),
            ):
                await bridge._handle_segment(b"pcm", {"turnId": "turn-a"})
            return bridge

        bridge = asyncio.run(runner())

        bridge._chat_stream_and_speak.assert_awaited_once()
        bridge._chat.assert_awaited_once()
        bridge._speak.assert_awaited_once_with("single fallback")
        self.assertEqual(bridge.last_error, "")

    def test_unbound_segment_captured_before_validation_is_dropped_before_stt(self):
        async def runner() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()
            bridge._transcribe = AsyncMock(return_value="must not be transcribed")
            bridge._emit_validation = Mock()
            await bridge._handle_segment(
                b"pcm-captured-before-validation",
                {"turnId": "turn-before-validation"},
            )
            return bridge

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"EVELYN_RUNTIME_ARTIFACTS_DIR": temp_dir},
        ):
            manager = VoiceValidationManager(root=Path(temp_dir))
            started = manager.start(
                suite=SUITE_ID,
                surfaces=("local",),
                capabilities={
                    "voiceLocal": {
                        "state": "ready",
                        "ready": True,
                        "blockers": [],
                    }
                },
            )
            self.assertTrue(started["ok"], started)
            bridge = asyncio.run(runner())

        bridge._transcribe.assert_not_awaited()
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 1)
        self.assertEqual(
            [call.args[0] for call in bridge._emit_validation.call_args_list],
            ["capture"],
        )

    def test_unbound_local_output_is_blocked_if_validation_starts_before_write(self):
        class CountingStream:
            writes = 0

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

            def write(self, _payload: bytes) -> None:
                type(self).writes += 1

        class FakeSoundDevice:
            @staticmethod
            def RawOutputStream(**_kwargs):
                return CountingStream()

        class OneChunkContent:
            def __init__(self) -> None:
                self.sent = False

            def iter_chunked(self, _size: int):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return b"\x01\x02"

        async def runner() -> LocalIoBridge:
            bridge = LocalIoBridge()
            response = SimpleNamespace(content=OneChunkContent())
            with patch.object(local_io_bridge, "sd", FakeSoundDevice()):
                with self.assertRaisesRegex(RuntimeError, "validation_attempt_stale"):
                    await bridge._play_streaming_pcm_response(
                        response,
                        started_at=local_io_bridge.time.perf_counter(),
                    )
            return bridge

        CountingStream.writes = 0
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"EVELYN_RUNTIME_ARTIFACTS_DIR": temp_dir},
        ):
            manager = VoiceValidationManager(root=Path(temp_dir))
            started = manager.start(
                suite=SUITE_ID,
                surfaces=("local",),
                capabilities={
                    "voiceLocal": {
                        "state": "ready",
                        "ready": True,
                        "blockers": [],
                    }
                },
            )
            self.assertTrue(started["ok"], started)
            bridge = asyncio.run(runner())

        self.assertEqual(CountingStream.writes, 0)
        self.assertFalse(bridge.playback_started_for_turn)

    def test_segment_status_omits_private_validation_attempt_binding(self):
        async def runner() -> tuple[dict, dict]:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()
            bridge._transcribe = AsyncMock(return_value="")
            bridge._emit_validation = Mock()
            meta = {
                "turnId": "turn-a",
                "validationSessionId": "validation-a",
                "validationStepId": "01-local",
                "validationAttempt": 1,
                "validationAttemptId": "private-attempt-camel",
                "validation_attempt_id": "private-attempt-snake",
                "_admissionEpoch": bridge.admission_epoch,
                "_bargeSource": {"ownerToken": "private-owner"},
            }

            await bridge._handle_segment(b"pcm", meta)

            extra = bridge._post_status.await_args_list[0].kwargs["extra"]
            return dict(extra["lastSegmentMeta"]), meta

        public_meta, private_meta = asyncio.run(runner())

        self.assertEqual(public_meta["turnId"], "turn-a")
        self.assertEqual(public_meta["validationSessionId"], "validation-a")
        self.assertNotIn("validationAttemptId", public_meta)
        self.assertNotIn("validation_attempt_id", public_meta)
        self.assertNotIn("_admissionEpoch", public_meta)
        self.assertNotIn("_bargeSource", public_meta)
        self.assertEqual(private_meta["validationAttemptId"], "private-attempt-camel")

    def test_delta_teardown_holds_owner_until_receiver_has_fully_exited(self):
        class FakeStream:
            def __init__(self) -> None:
                self.abort_count = 0

            def abort(self) -> None:
                self.abort_count += 1

        class FakeWebSocket:
            def __init__(self) -> None:
                self.closed = False

            async def close(self) -> None:
                self.closed = True

        async def runner() -> tuple[bool, bool, int, bool]:
            bridge = LocalIoBridge()
            self.assertTrue(bridge.playback_controller.claim("turn-a", lambda: None))
            cleanup_started = asyncio.Event()
            allow_receiver_exit = asyncio.Event()

            async def receiver_body() -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_started.set()
                    await allow_receiver_exit.wait()

            receiver = asyncio.create_task(receiver_body())
            await asyncio.sleep(0)
            stream = FakeStream()
            websocket = FakeWebSocket()
            teardown = asyncio.create_task(
                bridge._teardown_delta_playback(
                    playback_owner="turn-a",
                    websocket=websocket,
                    receiver=receiver,
                    output_stream=stream,
                )
            )
            await cleanup_started.wait()
            claimed_while_receiver_exiting = bridge.playback_controller.claim(
                "turn-b",
                lambda: None,
            )
            allow_receiver_exit.set()
            await teardown
            claimed_after_receiver_exit = bridge.playback_controller.claim(
                "turn-b",
                lambda: None,
            )
            return (
                claimed_while_receiver_exiting,
                claimed_after_receiver_exit,
                stream.abort_count,
                websocket.closed,
            )

        claimed_during, claimed_after, abort_count, websocket_closed = asyncio.run(
            runner()
        )

        self.assertFalse(claimed_during)
        self.assertTrue(claimed_after)
        self.assertEqual(abort_count, 1)
        self.assertTrue(websocket_closed)

    def test_delta_parent_cancellation_waits_for_receiver_before_owner_release(self):
        async def runner() -> tuple[bool, bool, bool, bool, bool]:
            write_started = threading.Event()
            abort_called = threading.Event()
            allow_write_exit = threading.Event()
            write_exited = threading.Event()

            class FakeRawOutputStream:
                def __init__(self, **_kwargs) -> None:
                    self.aborted = False
                    self.exited = False

                def __enter__(self):
                    return self

                def __exit__(self, _exc_type, _exc, _tb) -> None:
                    self.exited = True

                def abort(self) -> None:
                    self.aborted = True
                    abort_called.set()

                def write(self, _payload: bytes) -> None:
                    write_started.set()
                    allow_write_exit.wait(timeout=2.0)
                    write_exited.set()

            class FakeSoundDevice:
                def __init__(self) -> None:
                    self.stream: FakeRawOutputStream | None = None

                def RawOutputStream(self, **kwargs):
                    self.stream = FakeRawOutputStream(**kwargs)
                    return self.stream

            class FakeWebSocket:
                def __init__(self) -> None:
                    self.closed = False
                    self.sent_audio = False

                async def receive_json(self, **_kwargs):
                    return {"type": "ready"}

                async def send_json(self, _payload) -> None:
                    return None

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if not self.sent_audio:
                        self.sent_audio = True
                        return SimpleNamespace(
                            type=local_io_bridge.aiohttp.WSMsgType.BINARY,
                            data=b"\x01\x02",
                        )
                    await asyncio.Event().wait()
                    raise StopAsyncIteration

                async def close(self) -> None:
                    self.closed = True

            class BlockingContent:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    await asyncio.Event().wait()
                    raise StopAsyncIteration

            class FakeResponse:
                status = 200

                def __init__(self) -> None:
                    self.content = BlockingContent()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                    return None

            class FakeSession:
                def __init__(self) -> None:
                    self.websocket = FakeWebSocket()

                async def ws_connect(self, *_args, **_kwargs):
                    return self.websocket

                def post(self, *_args, **_kwargs):
                    return FakeResponse()

            bridge = LocalIoBridge()
            session = FakeSession()
            sound_device = FakeSoundDevice()
            bridge.session = session
            bridge.active_turn_id = "turn-a"
            bridge._post_status = AsyncMock()
            grant = {
                "bridgeInstanceId": bridge.bridge_instance_id,
                "turnId": "turn-a",
                "originalText": "hello",
                "forwardText": "hello",
                "admissionToken": "a" * 32,
                "validation": {},
                "mode": "wake_entry",
                "issuedMonotonic": local_io_bridge.time.monotonic(),
                "epoch": bridge.admission_epoch,
                "_botDispatched": False,
            }
            with patch.object(local_io_bridge, "sd", sound_device):
                parent = asyncio.create_task(
                    bridge._chat_delta_stream_and_speak(
                        "hello",
                        grant=grant,
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(write_started.wait, 1.0)
                )
                self.assertTrue(bridge.playback_started_for_turn)
                parent.cancel()
                self.assertTrue(
                    await asyncio.to_thread(abort_called.wait, 1.0)
                )
                claimed_during_cleanup = bridge.playback_controller.claim(
                    "turn-b",
                    lambda: None,
                )
                self.assertFalse(parent.done())
                self.assertFalse(write_exited.is_set())
                allow_write_exit.set()
                with self.assertRaises(asyncio.CancelledError):
                    await parent
                claimed_after_cleanup = bridge.playback_controller.claim(
                    "turn-b",
                    lambda: None,
                )

            assert sound_device.stream is not None
            return (
                claimed_during_cleanup,
                claimed_after_cleanup,
                sound_device.stream.aborted and sound_device.stream.exited,
                session.websocket.closed,
                write_exited.is_set(),
            )

        (
            claimed_during,
            claimed_after,
            stream_stopped,
            websocket_closed,
            worker_exited,
        ) = asyncio.run(runner())

        self.assertFalse(claimed_during)
        self.assertTrue(claimed_after)
        self.assertTrue(stream_stopped)
        self.assertTrue(websocket_closed)
        self.assertTrue(worker_exited)

    def test_stream_write_attempt_marks_playback_before_partial_write_failure(self):
        class FailingStream:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

            def write(self, _payload: bytes) -> None:
                raise RuntimeError("partial output then device failure")

        class FakeSoundDevice:
            @staticmethod
            def RawOutputStream(**_kwargs):
                return FailingStream()

        class OneChunkContent:
            def __init__(self) -> None:
                self.sent = False

            def iter_chunked(self, _size: int):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return b"\x01\x02"

        async def runner() -> LocalIoBridge:
            bridge = LocalIoBridge()
            response = SimpleNamespace(content=OneChunkContent())
            with patch.object(local_io_bridge, "sd", FakeSoundDevice()):
                with self.assertRaisesRegex(RuntimeError, "partial output"):
                    await bridge._play_streaming_pcm_response(
                        response,
                        started_at=local_io_bridge.time.perf_counter(),
                    )
            return bridge

        bridge = asyncio.run(runner())

        self.assertTrue(bridge.playback_started_for_turn)

    def test_rotated_validation_attempt_blocks_local_device_write(self):
        class CountingStream:
            writes = 0

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb) -> None:
                return None

            def write(self, _payload: bytes) -> None:
                type(self).writes += 1

        class FakeSoundDevice:
            @staticmethod
            def RawOutputStream(**_kwargs):
                return CountingStream()

        class OneChunkContent:
            def __init__(self) -> None:
                self.sent = False

            def iter_chunked(self, _size: int):
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return b"\x01\x02"

        async def runner() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.active_validation = {
                "sessionId": "validation-1",
                "stepId": "01-wake",
                "attemptId": "stale-attempt",
            }
            response = SimpleNamespace(content=OneChunkContent())
            with (
                patch.object(local_io_bridge, "sd", FakeSoundDevice()),
                patch.object(
                    local_io_bridge,
                    "validation_attempt_binding_is_current",
                    return_value=False,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "validation_attempt_stale"):
                    await bridge._play_streaming_pcm_response(
                        response,
                        started_at=local_io_bridge.time.perf_counter(),
                    )
            return bridge

        CountingStream.writes = 0
        bridge = asyncio.run(runner())

        self.assertEqual(CountingStream.writes, 0)
        self.assertFalse(bridge.playback_started_for_turn)

    def test_local_final_is_marked_only_from_actual_reply_completion(self):
        source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "local_io_bridge.py"
        ).read_text(encoding="utf-8")
        delta_start = source.index("    async def _chat_delta_stream_and_speak")
        sentence_start = source.index("    async def _chat_sentence_stream_and_speak")
        speak_start = source.index("    async def _speak(", sentence_start)

        self.assertIn(
            "if final_reply:\n                            self._mark_reply_final_once()",
            source[delta_start:sentence_start],
        )
        self.assertIn(
            "if final_reply:\n                        self._mark_reply_final_once()",
            source[sentence_start:speak_start],
        )


if __name__ == "__main__":
    unittest.main()
