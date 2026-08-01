from __future__ import annotations

import sys
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
        self._validation_context.stop()
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
            json={
                "bridgeInstanceId": "bridge-a",
                "micEnabled": True,
                "ready": True,
            },
        )
        payload = await status.json()
        public_text = fast_api.json.dumps(payload, ensure_ascii=False)

        self.assertEqual(status.status, 200)
        self.assertNotIn("bridgeInstanceId", payload["localBridge"])
        self.assertNotIn(token, public_text)
        self.assertNotIn("PRIVATE_STATUS_CANARY", public_text)
        self.assertTrue(payload["voiceAdmission"]["contentFree"])

    async def test_mic_off_revokes_an_unconsumed_capability(self) -> None:
        issued = await self.issue("이블린, 아직 처리하지 마")
        capability = await issued.json()
        mic_off = await self.client.post(
            "/api/local-bridge/mic",
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
