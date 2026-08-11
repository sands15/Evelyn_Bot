from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402


def _bridge_status_payload(
    now: float,
    *,
    include_capture_fence: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "local_io_bridge.status.v1",
        "statusSeq": 1,
        "heartbeatAt": now,
        "pid": 4242,
        "bridgeInstanceId": "a" * 32,
        "startedAt": now - 1.0,
        "enabled": True,
        "micEnabled": True,
        "ready": True,
        "micControlRevision": 0,
        "micControlActionId": "",
        "micControlPendingRevision": 0,
        "micControlPendingActionId": "",
        "micControlState": "idle",
        "micControlDesiredEnabled": True,
        "micControlError": "",
        "micCaptureStopped": False,
        "mic": {
            "enabled": True,
            "captureReady": True,
            "captureActive": True,
            "captureStopped": False,
        },
    }
    if include_capture_fence:
        payload.update(
            {
                "voiceCaptureWatchdog": {
                    "schema": fast_api.WATCHDOG_STATUS_SCHEMA,
                    "state": "authorized",
                    "reason": "",
                    "checkedAt": now,
                    "captureStopped": False,
                    "stoppedAt": None,
                    "contentFree": True,
                },
                "voiceCaptureFenceDigest": "d" * 64,
            }
        )
    return payload


class LocalVoiceAdmissionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._original_manager = fast_api.LOCAL_VOICE_ADMISSION
        fast_api.LOCAL_VOICE_ADMISSION = fast_api.LocalVoiceAdmissionManager()
        self._validation_context = patch.object(
            fast_api,
            "local_voice_validation_binding_is_current",
            side_effect=lambda binding: not binding,
        )
        self._validation_context.start()
        self._unsafe_ingress_patch = patch.object(
            fast_api.FAST_CONTROL_CONTINUITY_OWNER,
            "_test_only_allow_unsafe_ingress",
            True,
            create=True,
        )
        self._unsafe_ingress_patch.start()
        self.reporter_token = "r" * 48
        self.internal_token = "i" * 48
        self._reporter_token_patch = patch.object(
            fast_api,
            "LOCAL_BRIDGE_STATUS_AUTH_TOKEN",
            self.reporter_token,
        )
        self._internal_token_patch = patch.object(
            fast_api,
            "EVELYN_INTERNAL_CONTROL_TOKEN",
            self.internal_token,
        )
        self._reporter_token_patch.start()
        self._internal_token_patch.start()
        self._original_bridge_status = dict(fast_api.LOCAL_BRIDGE_STATUS)
        self._original_mic_request = dict(
            fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
        )
        self._original_enable_fence = dict(
            fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE
        )
        fast_api.LOCAL_BRIDGE_STATUS.clear()
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": False,
                "ready": False,
                "mode": "windows_io_bridge",
            }
        )
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.clear()
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
            {
                "revision": 0,
                "actionId": "",
                "enabled": None,
                "requestedAt": None,
                "source": "",
                "purpose": "",
                "bridgeInstanceDigest": "",
            }
        )
        self.client = TestClient(
            TestServer(
                fast_api.create_app(
                    enable_minecraft_world_lease_owner=False,
                )
            )
        )
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        fast_api.LOCAL_BRIDGE_STATUS.clear()
        fast_api.LOCAL_BRIDGE_STATUS.update(self._original_bridge_status)
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.clear()
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
            self._original_mic_request
        )
        fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.clear()
        fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.update(
            self._original_enable_fence
        )
        self._internal_token_patch.stop()
        self._reporter_token_patch.stop()
        self._validation_context.stop()
        self._unsafe_ingress_patch.stop()
        fast_api.LOCAL_VOICE_ADMISSION = self._original_manager

    async def issue(
        self,
        text: str,
        *,
        turn_id: str = "turn-a",
        validation: dict | None = None,
    ):
        payload = {
            "bridgeInstanceId": "bridge-a",
            "turnId": turn_id,
            "text": text,
        }
        if validation is not None:
            payload["validation"] = validation
        return await self.client.post(
            "/api/local-voice/admission",
            json=payload,
        )

    async def test_issue_requires_exact_wake_and_never_caches_result(self) -> None:
        denied = await self.issue("주변 대화")
        denied_payload = await denied.json()
        admitted = await self.issue(
            "이블린, 지금 듣고 있어?",
            turn_id="turn-b",
        )
        admitted_payload = await admitted.json()

        self.assertEqual(denied.status, 409)
        self.assertEqual(denied_payload["reason"], "wake_word_required")
        self.assertEqual(denied.headers["Cache-Control"], "no-store")
        self.assertEqual(admitted.status, 200)
        self.assertEqual(admitted.headers["Cache-Control"], "no-store")
        self.assertTrue(admitted_payload["admitted"])
        self.assertEqual(admitted_payload["forwardText"], "지금 듣고 있어?")
        self.assertGreaterEqual(len(admitted_payload["admissionToken"]), 24)

    async def test_browser_origin_cannot_call_internal_admission_route(self) -> None:
        response = await self.client.post(
            "/api/local-voice/admission",
            headers={"Origin": "http://127.0.0.1:8799"},
            json={
                "bridgeInstanceId": "browser-forged-bridge",
                "turnId": "browser-forged-turn",
                "text": "이블린, 브라우저 우회",
            },
        )
        payload = await response.json()

        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"], "browser_origin_not_allowed")

    async def test_validation_mismatch_never_issues_a_capability(self) -> None:
        binding = {
            "sessionId": "session-a",
            "stepId": "step-a",
            "attempt": 1,
            "attemptId": "attempt-a",
        }
        with patch.object(
            fast_api,
            "validation_transcript_admission_status",
            return_value={
                "current": True,
                "matched": False,
                "reason": "validation_transcript_mismatch",
                "contentFree": True,
            },
        ):
            response = await self.issue(
                "검증과 무관한 문장",
                validation=binding,
            )
        payload = await response.json()

        self.assertEqual(response.status, 409)
        self.assertEqual(payload["reason"], "validation_transcript_mismatch")
        self.assertNotIn("admissionToken", payload)
        self.assertNotIn(
            "검증과 무관한 문장",
            fast_api.json.dumps(payload, ensure_ascii=False),
        )

    async def test_public_status_omits_bridge_id_token_and_text(self) -> None:
        issued = await self.issue("이블린, PRIVATE_STATUS_CANARY")
        issued_payload = await issued.json()
        token = issued_payload["admissionToken"]

        status = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json={
                "schema": "local_io_bridge.status.v1",
                "statusSeq": 1,
                "heartbeatAt": time.time(),
                "pid": 4242,
                "bridgeInstanceId": "a" * 32,
                "startedAt": time.time() - 1.0,
                "enabled": True,
                "micEnabled": True,
                "ready": True,
                "micControlRevision": 0,
                "micControlActionId": "",
                "micControlPendingRevision": 0,
                "micControlPendingActionId": "",
                "micControlState": "idle",
                "micControlDesiredEnabled": True,
                "micControlError": "",
                "micCaptureStopped": False,
                "mic": {
                    "enabled": True,
                    "captureReady": True,
                    "captureActive": True,
                    "captureStopped": False,
                },
            },
        )
        payload = await status.json()
        public_text = fast_api.json.dumps(payload, ensure_ascii=False)

        self.assertEqual(status.status, 200)
        self.assertNotIn("bridgeInstanceId", payload["localBridge"])
        self.assertNotIn(token, public_text)
        self.assertNotIn("PRIVATE_STATUS_CANARY", public_text)
        self.assertTrue(payload["voiceAdmission"]["contentFree"])

    async def test_capture_fence_status_is_private_and_fail_closed(self) -> None:
        now = time.time()
        status = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=_bridge_status_payload(now),
        )
        response_payload = await status.json()

        self.assertEqual(status.status, 200, response_payload)
        self.assertNotIn(
            "voiceCaptureFenceDigest",
            response_payload["localBridge"],
        )
        self.assertEqual(
            response_payload["localBridge"]["voiceCaptureWatchdog"][
                "state"
            ],
            "authorized",
        )
        with patch.object(
            fast_api,
            "voice_capture_consent_fence_matches",
            return_value=True,
        ) as matcher:
            self.assertTrue(
                fast_api.local_voice_capture_fence_is_current(
                    "a" * 32,
                    now=now,
                )
            )
        matcher.assert_called_once_with(
            fast_api.VOICE_CAPTURE_HOST_LEASE_PATH,
            fast_api.VOICE_CAPTURE_CONSENT_STATE_PATH,
            expected_digest="d" * 64,
            now=matcher.call_args.kwargs["now"],
        )
        fast_api.LOCAL_BRIDGE_STATUS["mic"]["captureActive"] = False
        with patch.object(
            fast_api,
            "voice_capture_consent_fence_matches",
            return_value=True,
        ):
            self.assertFalse(
                fast_api.local_voice_capture_fence_is_current(
                    "a" * 32,
                    now=now,
                )
            )
            self.assertEqual(
                fast_api.local_voice_capture_fence_digest_if_current(
                    "a" * 32,
                    now=now,
                    require_capture_active=False,
                ),
                "d" * 64,
            )
        fast_api.LOCAL_BRIDGE_STATUS["mic"]["captureActive"] = True
        with patch.object(
            fast_api,
            "voice_capture_consent_fence_matches",
            return_value=False,
        ):
            self.assertFalse(
                fast_api.local_voice_capture_fence_is_current(
                    "a" * 32,
                    now=now,
                )
            )
            self.assertEqual(
                fast_api.local_voice_capture_fence_digest_if_current(
                    "a" * 32,
                    now=now,
                    require_capture_active=False,
                ),
                "",
            )
        self.assertFalse(
            fast_api.local_voice_capture_fence_is_current(
                "a" * 32,
                now=now + fast_api.HOST_LEASE_STALE_SEC + 0.01,
            )
        )
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["enabled"] = False
        self.assertFalse(
            fast_api.local_voice_capture_fence_is_current(
                "a" * 32,
                now=now,
            )
        )

    async def test_capture_fence_status_pair_is_exact(self) -> None:
        now = time.time()
        missing = fast_api._normalize_local_bridge_status(
            _bridge_status_payload(now, include_capture_fence=False),
            now=now,
        )
        self.assertIsNotNone(missing)
        self.assertNotIn("voiceCaptureFenceDigest", missing)

        one_sided = _bridge_status_payload(now)
        one_sided.pop("voiceCaptureFenceDigest")
        self.assertIsNone(
            fast_api._normalize_local_bridge_status(one_sided, now=now)
        )

        mismatched_stop = _bridge_status_payload(now)
        mismatched_stop["voiceCaptureWatchdog"]["captureStopped"] = True
        self.assertIsNone(
            fast_api._normalize_local_bridge_status(
                mismatched_stop,
                now=now,
            )
        )

    async def test_mic_off_revokes_an_unconsumed_capability(self) -> None:
        issued = await self.issue("이블린, 아직 처리하지 마")
        capability = await issued.json()
        mic_off = await self.client.post(
            "/api/local-bridge/mic",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: self.internal_token,
            },
            json={"enabled": False, "source": "control_page"},
        )
        self.assertEqual(mic_off.status, 202)

        consumed = await self.client.post(
            "/api/control-page/chat",
            json={
                "text": capability["forwardText"],
                "source": "local_bridge",
                "bridgeInstanceId": "bridge-a",
                "turnId": "turn-a",
                "admissionToken": capability["admissionToken"],
            },
        )
        payload = await consumed.json()

        self.assertEqual(consumed.status, 409)
        self.assertEqual(payload["reason"], "mic_disabled")

    async def test_conflicting_validation_fields_invalidate_before_history(
        self,
    ) -> None:
        issued = await self.issue("이블린, 한 번만 처리해")
        capability = await issued.json()
        history_before = list(fast_api.CHAT_MESSAGES)

        response = await self.client.post(
            "/api/control-page/chat",
            json={
                "text": capability["forwardText"],
                "source": "local_bridge",
                "bridgeInstanceId": "bridge-a",
                "turnId": "turn-a",
                "admissionToken": capability["admissionToken"],
                "validation": {},
                "validationBinding": {
                    "sessionId": "conflicting-session",
                    "stepId": "conflicting-step",
                    "attempt": 1,
                    "attemptId": "conflicting-attempt",
                },
            },
        )
        payload = await response.json()

        self.assertEqual(response.status, 409)
        self.assertEqual(payload["reason"], "admission_validation_mismatch")
        self.assertEqual(fast_api.CHAT_MESSAGES, history_before)

    async def test_stale_validation_token_is_rejected_before_history(self) -> None:
        binding = {
            "sessionId": "session-old",
            "stepId": "step-old",
            "attempt": 1,
            "attemptId": "attempt-old",
        }
        capability = fast_api.LOCAL_VOICE_ADMISSION.issue(
            "bridge-a",
            "turn-stale",
            "검증 문장",
            validation_binding=binding,
            validation_is_current=lambda _binding: True,
        )
        self.assertTrue(capability["admitted"], capability)
        history_before = list(fast_api.CHAT_MESSAGES)

        with patch.object(
            fast_api,
            "local_voice_validation_binding_is_current",
            return_value=False,
        ):
            response = await self.client.post(
                "/api/control-page/chat",
                json={
                    "text": capability["forwardText"],
                    "source": "local_bridge",
                    "bridgeInstanceId": "bridge-a",
                    "turnId": "turn-stale",
                    "admissionToken": capability["admissionToken"],
                    "validation": binding,
                },
            )
        payload = await response.json()

        self.assertEqual(response.status, 409)
        self.assertEqual(payload["reason"], "validation_attempt_stale")
        self.assertEqual(fast_api.CHAT_MESSAGES, history_before)


if __name__ == "__main__":
    unittest.main()
