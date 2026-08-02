from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import local_io_bridge  # noqa: E402
from evelyn_core.local_io_bridge import LocalIoBridge  # noqa: E402


def admission_status(*, rejected_count: int = 0) -> dict:
    return {
        "schema": "local_voice.admission.status.v1",
        "active": False,
        "mode": "inactive",
        "acceptedCount": 0,
        "rejectedCount": rejected_count,
        "lastReason": "wake_required",
        "contentFree": True,
    }


class LocalVoiceAdmissionBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_wake_rejection_is_a_silent_pre_chat_drop(self) -> None:
        class AdmissionResponse:
            status = 409

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

            async def json(self, *, content_type=None):
                del content_type
                return {
                    "ok": False,
                    "admitted": False,
                    "error": "local_voice_wake_required",
                    "reason": "wake_required",
                    "admission": admission_status(rejected_count=1),
                }

        class AdmissionSession:
            def __init__(self) -> None:
                self.requests: list[tuple[str, dict]] = []

            def post(self, url, *, json, timeout):
                del timeout
                self.requests.append((str(url), dict(json)))
                return AdmissionResponse()

        bridge = LocalIoBridge()
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        session = AdmissionSession()
        bridge.session = session  # type: ignore[assignment]
        bridge._post_status = AsyncMock()  # type: ignore[method-assign]
        bridge._transcribe = AsyncMock(return_value="주변에서 나눈 대화")  # type: ignore[method-assign]
        bridge._chat = AsyncMock()  # type: ignore[method-assign]
        bridge._chat_stream_and_speak = AsyncMock()  # type: ignore[method-assign]
        bridge._speak = AsyncMock()  # type: ignore[method-assign]
        bridge._emit_validation = Mock()  # type: ignore[method-assign]

        await bridge._handle_segment(b"ambient speech", {"turnId": "turn-no-wake"})

        self.assertEqual(len(session.requests), 1)
        request_url, request_payload = session.requests[0]
        self.assertTrue(request_url.endswith("/api/local-voice/admission"))
        self.assertEqual(request_payload["bridgeInstanceId"], bridge.bridge_instance_id)
        self.assertEqual(request_payload["turnId"], "turn-no-wake")
        self.assertNotIn("admissionToken", request_payload)
        bridge._chat.assert_not_awaited()
        bridge._chat_stream_and_speak.assert_not_awaited()
        bridge._speak.assert_not_awaited()
        self.assertEqual(bridge.transcript_count, 0)
        self.assertEqual(bridge.last_error, "")
        self.assertEqual(bridge.admission_rejected_count, 1)
        self.assertEqual(bridge.admission_last_reason, "wake_required")
        emitted = [call.args[0] for call in bridge._emit_validation.call_args_list]
        self.assertEqual(emitted, ["capture"])

    async def test_chat_payload_contains_only_the_frozen_admission_capability(self) -> None:
        bridge = LocalIoBridge()
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        bridge.active_turn_id = "turn-admitted"
        grant = {
            "bridgeInstanceId": bridge.bridge_instance_id,
            "turnId": "turn-admitted",
            "originalText": "이블린 안녕",
            "forwardText": "안녕",
            "admissionToken": "private-admission-token-1234567890",
            "validation": {
                "sessionId": "session-a",
                "stepId": "step-a",
                "attempt": 1,
                "attemptId": "attempt-a",
            },
            "mode": "validation",
            "issuedMonotonic": time.monotonic(),
            "epoch": bridge.admission_epoch,
            "_botDispatched": False,
        }

        payload = await bridge._local_voice_chat_payload("안녕", grant)

        self.assertEqual(
            payload,
            {
                "text": "안녕",
                "source": "local_bridge",
                "turnId": "turn-admitted",
                "bridgeInstanceId": bridge.bridge_instance_id,
                "admissionToken": "private-admission-token-1234567890",
                "validation": {
                    "sessionId": "session-a",
                    "stepId": "step-a",
                    "attempt": 1,
                    "attemptId": "attempt-a",
                },
            },
        )
        self.assertNotIn("originalText", payload)
        self.assertNotIn("epoch", payload)

    async def test_mic_off_during_stt_cannot_issue_a_new_epoch_token(self) -> None:
        bridge = LocalIoBridge()
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        captured_epoch = bridge.admission_epoch
        stt_started = asyncio.Event()
        release_stt = asyncio.Event()

        async def transcribe(_pcm: bytes) -> str:
            stt_started.set()
            await release_stt.wait()
            return "이블린, 종료 뒤 실행되면 안 돼"

        bridge._post_status = AsyncMock()  # type: ignore[method-assign]
        bridge._transcribe = AsyncMock(side_effect=transcribe)  # type: ignore[method-assign]
        bridge._request_voice_admission = AsyncMock()  # type: ignore[method-assign]
        bridge._chat = AsyncMock()  # type: ignore[method-assign]
        bridge._chat_stream_and_speak = AsyncMock()  # type: ignore[method-assign]

        with patch.object(
            local_io_bridge,
            "validation_attempt_binding_is_current",
            return_value=True,
        ):
            turn = asyncio.create_task(
                bridge._handle_segment(
                    b"pcm",
                    {
                        "turnId": "turn-inflight",
                        "_admissionEpoch": captured_epoch,
                    },
                )
            )
            await asyncio.wait_for(stt_started.wait(), timeout=1.0)
            await bridge._stop_mic()
            release_stt.set()
            await turn

        bridge._request_voice_admission.assert_not_awaited()  # type: ignore[union-attr]
        bridge._chat.assert_not_awaited()  # type: ignore[union-attr]
        bridge._chat_stream_and_speak.assert_not_awaited()  # type: ignore[union-attr]
        self.assertEqual(bridge.admission_epoch, captured_epoch + 1)
        self.assertEqual(bridge.transcript_count, 0)

    async def test_mic_off_during_barge_verification_cannot_cancel_or_requeue(
        self,
    ) -> None:
        bridge = LocalIoBridge()
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        captured_epoch = bridge.admission_epoch
        verification_started = asyncio.Event()
        release_verification = asyncio.Event()

        async def verify(_pcm: bytes):
            verification_started.set()
            await release_verification.wait()
            return None

        bridge._verify_barge_in_speaker = AsyncMock(  # type: ignore[method-assign]
            side_effect=verify
        )
        bridge._emit_validation = Mock()  # type: ignore[method-assign]
        bridge.playback_controller.request_cancel = Mock(  # type: ignore[method-assign]
            return_value=True
        )
        bridge.barge_in_queue.put_nowait(
            (
                b"barge",
                {
                    "turnId": "turn-barge-inflight",
                    "_admissionEpoch": captured_epoch,
                },
            )
        )
        worker = asyncio.create_task(bridge._barge_in_worker())
        try:
            await asyncio.wait_for(verification_started.wait(), timeout=1.0)
            await bridge._stop_mic()
            release_verification.set()
            await asyncio.wait_for(bridge.barge_in_queue.join(), timeout=1.0)
        finally:
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

        bridge.playback_controller.request_cancel.assert_not_called()  # type: ignore[union-attr]
        self.assertTrue(bridge.priority_queue.empty())
        emitted = [call.args[0] for call in bridge._emit_validation.call_args_list]
        self.assertEqual(emitted, ["barge_in_rejected"])


if __name__ == "__main__":
    unittest.main()
