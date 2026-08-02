from __future__ import annotations

import asyncio
import json
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


def connector_error() -> local_io_bridge.aiohttp.ClientConnectorError:
    key = Mock(host="127.0.0.1", port=8798, ssl=False)
    return local_io_bridge.aiohttp.ClientConnectorError(
        key,
        OSError(10061, "connection refused"),
    )


class _EnterFailure:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, _exc_type, _exc, _tb):
        return None


class _LinesThenError:
    def __init__(self, event: dict, error: BaseException) -> None:
        self.line = (json.dumps(event) + "\n").encode("utf-8")
        self.error = error
        self.sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.sent:
            self.sent = True
            return self.line
        raise self.error


class _BotResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: dict | None = None,
        content=None,
    ) -> None:
        self.status = status
        self.payload = dict(payload or {})
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return None

    async def json(self, *, content_type=None):
        del content_type
        return dict(self.payload)


class _SequenceSession:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict, bool]] = []

    def post(self, url, *, json, timeout, allow_redirects=True):
        del timeout
        self.requests.append(
            (str(url), dict(json), bool(allow_redirects))
        )
        return self.responses.pop(0)


class LocalVoiceAdmissionBridgeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def grant(
        bridge: LocalIoBridge,
        *,
        issued_monotonic: float | None = None,
    ) -> dict:
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        bridge.active_turn_id = "turn-retry"
        return {
            "bridgeInstanceId": bridge.bridge_instance_id,
            "turnId": "turn-retry",
            "originalText": "이블린 안녕",
            "forwardText": "안녕",
            "admissionToken": "private-admission-token-1234567890",
            "validation": {},
            "mode": "wake_entry",
            "issuedMonotonic": (
                time.monotonic()
                if issued_monotonic is None
                else issued_monotonic
            ),
            "epoch": bridge.admission_epoch,
            "_botDispatched": False,
        }

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
                self.requests: list[tuple[str, dict, bool]] = []

            def post(self, url, *, json, timeout, allow_redirects=True):
                del timeout
                self.requests.append(
                    (str(url), dict(json), bool(allow_redirects))
                )
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
        request_url, request_payload, allow_redirects = session.requests[0]
        self.assertTrue(request_url.endswith("/api/local-voice/admission"))
        self.assertFalse(allow_redirects)
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
                "admissionMode": "validation",
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

    async def test_chat_retries_multiple_pre_header_connector_failures(
        self,
    ) -> None:
        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        success = {
            "ok": True,
            "reply": "응",
            "memoryState": "not_used",
            "memoryBoundary": None,
            "admission": admission_status(),
        }
        session = _SequenceSession(
            _EnterFailure(connector_error()),
            _EnterFailure(connector_error()),
            _EnterFailure(connector_error()),
            _BotResponse(payload=success),
        )
        bridge.session = session  # type: ignore[assignment]
        retry_sleep = AsyncMock()

        with patch.object(local_io_bridge.asyncio, "sleep", retry_sleep):
            result = await bridge._chat("안녕", grant=grant)

        self.assertEqual(result.text, "응")
        self.assertEqual(len(session.requests), 4)
        self.assertTrue(
            all(request == session.requests[0] for request in session.requests)
        )
        self.assertFalse(session.requests[0][2])
        self.assertEqual(retry_sleep.await_count, 3)
        self.assertTrue(
            all(
                awaited.args
                == (local_io_bridge.LOCAL_VOICE_BOT_CONNECT_RETRY_DELAY_SEC,)
                for awaited in retry_sleep.await_args_list
            )
        )
        self.assertTrue(grant["_botDispatched"])

    async def test_chat_connector_retry_budget_is_bounded(self) -> None:
        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        retry_budget = local_io_bridge.LOCAL_VOICE_BOT_CONNECT_MAX_RETRIES
        session = _SequenceSession(
            *(
                _EnterFailure(connector_error())
                for _ in range(retry_budget + 1)
            ),
            _BotResponse(payload={"ok": True}),
        )
        bridge.session = session  # type: ignore[assignment]
        retry_sleep = AsyncMock()

        with patch.object(local_io_bridge.asyncio, "sleep", retry_sleep):
            with self.assertRaises(
                local_io_bridge.aiohttp.ClientConnectorError
            ):
                await bridge._chat("안녕", grant=grant)

        self.assertEqual(len(session.requests), retry_budget + 1)
        self.assertEqual(retry_sleep.await_count, retry_budget)

    async def test_chat_recovers_after_bot_restart_stale_context(self) -> None:
        events: list[str] = []

        class OrderedSession(_SequenceSession):
            def post(self, url, *, json, timeout, allow_redirects=True):
                events.append("request")
                return super().post(
                    url,
                    json=json,
                    timeout=timeout,
                    allow_redirects=allow_redirects,
                )

        async def post_status() -> bool:
            events.append("heartbeat")
            return True

        stale = {
            "ok": False,
            "admitted": False,
            "error": "local_voice_wake_required",
            "reason": "admission_recovery_context_stale",
            "admission": admission_status(rejected_count=1),
        }
        success = {
            "ok": True,
            "reply": "응",
            "memoryState": "not_used",
            "memoryBoundary": None,
            "admission": admission_status(),
        }
        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        session = OrderedSession(
            _EnterFailure(connector_error()),
            _BotResponse(status=409, payload=stale),
            _BotResponse(payload=success),
        )
        bridge.session = session  # type: ignore[assignment]
        bridge._post_status = AsyncMock(  # type: ignore[method-assign]
            side_effect=post_status
        )
        retry_sleep = AsyncMock()

        with patch.object(local_io_bridge.asyncio, "sleep", retry_sleep):
            result = await bridge._chat("안녕", grant=grant)

        self.assertEqual(result.text, "응")
        self.assertEqual(events, ["request", "request", "heartbeat", "request"])
        self.assertEqual(len(session.requests), 3)
        self.assertTrue(all(not request[2] for request in session.requests))
        retry_sleep.assert_awaited_once_with(
            local_io_bridge.LOCAL_VOICE_BOT_CONNECT_RETRY_DELAY_SEC
        )
        bridge._post_status.assert_awaited_once()  # type: ignore[union-attr]

    async def test_chat_stale_context_retry_is_limited_to_one(self) -> None:
        stale = {
            "ok": False,
            "admitted": False,
            "error": "local_voice_wake_required",
            "reason": "admission_recovery_context_stale",
            "admission": admission_status(rejected_count=1),
        }
        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        session = _SequenceSession(
            _BotResponse(status=409, payload=stale),
            _BotResponse(status=409, payload=stale),
            _BotResponse(payload={"ok": True}),
        )
        bridge.session = session  # type: ignore[assignment]
        bridge._post_status = AsyncMock(return_value=True)  # type: ignore[method-assign]

        with self.assertRaisesRegex(
            local_io_bridge.LocalVoiceAdmissionDrop,
            "admission_recovery_context_stale",
        ):
            await bridge._chat("안녕", grant=grant)

        self.assertEqual(len(session.requests), 2)
        bridge._post_status.assert_awaited_once()  # type: ignore[union-attr]

    async def test_chat_stale_context_requires_accepted_heartbeat(self) -> None:
        stale = {
            "ok": False,
            "admitted": False,
            "error": "local_voice_wake_required",
            "reason": "admission_recovery_context_stale",
            "admission": admission_status(rejected_count=1),
        }
        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        session = _SequenceSession(
            _BotResponse(status=409, payload=stale),
            _BotResponse(payload={"ok": True}),
        )
        bridge.session = session  # type: ignore[assignment]
        bridge._post_status = AsyncMock(return_value=False)  # type: ignore[method-assign]

        with self.assertRaisesRegex(
            local_io_bridge.LocalVoiceAdmissionDrop,
            "admission_recovery_context_stale",
        ):
            await bridge._chat("안녕", grant=grant)

        self.assertEqual(len(session.requests), 1)
        bridge._post_status.assert_awaited_once()  # type: ignore[union-attr]

    async def test_chat_only_retries_exact_stale_context_409(self) -> None:
        cases = (
            (409, "wake_required", local_io_bridge.LocalVoiceAdmissionDrop),
            (503, "admission_recovery_context_stale", RuntimeError),
        )
        for status, reason, error_type in cases:
            with self.subTest(status=status, reason=reason):
                bridge = LocalIoBridge()
                grant = self.grant(bridge)
                session = _SequenceSession(
                    _BotResponse(
                        status=status,
                        payload={
                            "ok": False,
                            "admitted": False,
                            "error": "local_voice_wake_required",
                            "reason": reason,
                            "admission": admission_status(rejected_count=1),
                        },
                    ),
                    _BotResponse(payload={"ok": True}),
                )
                bridge.session = session  # type: ignore[assignment]
                bridge._post_status = AsyncMock()  # type: ignore[method-assign]

                with self.assertRaises(error_type):
                    await bridge._chat("안녕", grant=grant)

                self.assertEqual(len(session.requests), 1)
                bridge._post_status.assert_not_awaited()  # type: ignore[union-attr]

    async def test_chat_stale_context_retry_expires_after_heartbeat(self) -> None:
        bridge = LocalIoBridge()
        grant = self.grant(bridge, issued_monotonic=99.0)
        session = _SequenceSession(
            _BotResponse(
                status=409,
                payload={
                    "ok": False,
                    "admitted": False,
                    "error": "local_voice_wake_required",
                    "reason": "admission_recovery_context_stale",
                    "admission": admission_status(rejected_count=1),
                },
            ),
            _BotResponse(payload={"ok": True}),
        )
        bridge.session = session  # type: ignore[assignment]
        bridge._post_status = AsyncMock(return_value=True)  # type: ignore[method-assign]

        with patch.object(
            local_io_bridge.time,
            "monotonic",
            side_effect=(100.0, 101.0, 106.0),
        ):
            with self.assertRaisesRegex(
                local_io_bridge.LocalVoiceAdmissionDrop,
                "admission_recovery_context_stale",
            ):
                await bridge._chat("안녕", grant=grant)

        self.assertEqual(len(session.requests), 1)
        bridge._post_status.assert_awaited_once()  # type: ignore[union-attr]

    async def test_chat_never_retries_other_failures_or_http_response(self) -> None:
        failures = (
            asyncio.TimeoutError(),
            local_io_bridge.aiohttp.ServerDisconnectedError("disconnected"),
        )
        for error in failures:
            with self.subTest(error=type(error).__name__):
                bridge = LocalIoBridge()
                grant = self.grant(bridge)
                session = _SequenceSession(
                    _EnterFailure(error),
                    _BotResponse(payload={"ok": True}),
                )
                bridge.session = session  # type: ignore[assignment]
                with self.assertRaises(type(error)):
                    await bridge._chat("안녕", grant=grant)
                self.assertEqual(len(session.requests), 1)

        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        session = _SequenceSession(
            _BotResponse(status=503, payload={"ok": False}),
            _BotResponse(payload={"ok": True}),
        )
        bridge.session = session  # type: ignore[assignment]
        with self.assertRaisesRegex(RuntimeError, "chat_failed_503"):
            await bridge._chat("안녕", grant=grant)
        self.assertEqual(len(session.requests), 1)

    async def test_chat_connector_retry_checks_window_around_sleep(self) -> None:
        cases = (
            ((100.0, 106.0), 0),
            ((100.0, 101.0, 106.0), 1),
        )
        for monotonic_values, expected_sleeps in cases:
            with self.subTest(expected_sleeps=expected_sleeps):
                bridge = LocalIoBridge()
                grant = self.grant(bridge, issued_monotonic=99.0)
                session = _SequenceSession(
                    _EnterFailure(connector_error()),
                    _BotResponse(payload={"ok": True}),
                )
                bridge.session = session  # type: ignore[assignment]
                retry_sleep = AsyncMock()

                with patch.object(
                    local_io_bridge.time,
                    "monotonic",
                    side_effect=monotonic_values,
                ), patch.object(
                    local_io_bridge.asyncio,
                    "sleep",
                    retry_sleep,
                ):
                    with self.assertRaises(
                        local_io_bridge.aiohttp.ClientConnectorError
                    ):
                        await bridge._chat("안녕", grant=grant)

                self.assertEqual(len(session.requests), 1)
                self.assertEqual(retry_sleep.await_count, expected_sleeps)

    async def test_chat_stream_never_retries_after_first_event(self) -> None:
        bridge = LocalIoBridge()
        grant = self.grant(bridge)
        stream_error = connector_error()
        response = _BotResponse(
            content=_LinesThenError(
                {
                    "type": "memory_boundary",
                    "memoryState": "not_used",
                    "memoryBoundary": None,
                },
                stream_error,
            )
        )
        session = _SequenceSession(
            response,
            _BotResponse(payload={"ok": True}),
        )
        bridge.session = session  # type: ignore[assignment]
        bridge._speak = AsyncMock()  # type: ignore[method-assign]

        with self.assertRaises(local_io_bridge.aiohttp.ClientConnectorError):
            await bridge._chat_sentence_stream_and_speak(
                "안녕",
                grant=grant,
            )

        self.assertEqual(len(session.requests), 1)
        bridge._speak.assert_not_awaited()  # type: ignore[union-attr]

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
