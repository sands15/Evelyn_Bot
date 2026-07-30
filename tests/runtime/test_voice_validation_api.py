from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core.control_page_http import CONTROL_PAGE_CSRF_HEADER  # noqa: E402
from evelyn_core.voice_capture_consent import VoiceCaptureConsentManager  # noqa: E402
from evelyn_core.voice_validation import VoiceValidationManager  # noqa: E402


READY_CAPABILITIES = {
    "voiceLocal": {"state": "ready", "ready": True, "blockers": []},
    "voiceDiscord": {"state": "ready", "ready": True, "blockers": []},
}


class VoiceValidationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = VoiceValidationManager(root=Path(self.temp_dir.name))
        self.consent_manager = VoiceCaptureConsentManager(
            root=Path(self.temp_dir.name),
            owner_nonce="test-control-page",
        )
        self.manager_patch = patch.object(
            control_page_server,
            "get_voice_validation_manager",
            return_value=self.manager,
        )
        self.consent_manager_patch = patch.object(
            control_page_server,
            "get_voice_capture_consent_manager",
            return_value=self.consent_manager,
        )
        self.health_patch = patch.object(
            control_page_server,
            "cached_runtime_health",
            new=AsyncMock(return_value={"capabilities": READY_CAPABILITIES}),
        )
        self.mic_control = AsyncMock(side_effect=self.mic_control_result)
        self.mic_patch = patch.object(
            control_page_server,
            "request_local_bridge_mic_control",
            new=self.mic_control,
        )
        self.manager_patch.start()
        self.consent_manager_patch.start()
        self.health_patch.start()
        self.mic_patch.start()
        self.client = TestClient(TestServer(control_page_server.create_app()))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        session_response = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        self.csrf = (await session_response.json())["csrfToken"]

    async def asyncTearDown(self):
        await self.client.close()
        self.mic_patch.stop()
        self.health_patch.stop()
        self.consent_manager_patch.stop()
        self.manager_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def mic_control_result(enabled, *, source):
        del source
        return {
            "ok": True,
            "applied": True,
            "localBridge": {
                "ready": True,
                "micEnabled": bool(enabled),
                "mic": {
                    "enabled": bool(enabled),
                    "captureReady": bool(enabled),
                },
            },
        }

    def headers(self):
        return {
            "Origin": self.origin,
            CONTROL_PAGE_CSRF_HEADER: self.csrf,
        }

    async def test_start_get_and_abort_follow_public_session_contract(self):
        preview = self.consent_manager.preview()
        pending = self.consent_manager.begin_apply(
            confirm_token=preview["confirmToken"]
        )
        self.consent_manager.finish_apply(
            lease_id=pending["leaseId"],
            applied=True,
            capture_ready=True,
        )
        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["local", "discord"]},
        )
        started = await started_response.json()
        self.assertEqual(started_response.status, 201)
        self.assertEqual(started["session"]["schema"], "voice_validation.session.v1")
        self.assertEqual(started["session"]["state"], "running")

        state_response = await self.client.get(
            "/api/control-page/voice-validation",
            headers={"Origin": self.origin},
        )
        state = await state_response.json()
        self.assertEqual(state["session"]["sessionId"], started["session"]["sessionId"])

        aborted_response = await self.client.post(
            "/api/control-page/voice-validation/abort",
            headers=self.headers(),
            json={"sessionId": started["session"]["sessionId"]},
        )
        aborted = await aborted_response.json()
        self.assertEqual(aborted_response.status, 200)
        self.assertEqual(aborted["session"]["state"], "aborted")
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertIn(
            False,
            [call.args[0] for call in self.mic_control.await_args_list],
        )

    async def test_every_mutating_route_requires_csrf(self):
        for suffix in ("start", "confirm", "retry", "abort"):
            with self.subTest(route=suffix):
                response = await self.client.post(
                    f"/api/control-page/voice-validation/{suffix}",
                    headers={"Origin": self.origin},
                    json={},
                )
                self.assertEqual(response.status, 403)
                self.assertEqual((await response.json())["error"], "csrf_token_required")
        for suffix in ("preview", "apply", "revoke"):
            with self.subTest(route=f"voice-capture-consent/{suffix}"):
                response = await self.client.post(
                    f"/api/control-page/voice-capture-consent/{suffix}",
                    headers={"Origin": self.origin},
                    json={},
                )
                self.assertEqual(response.status, 403)
                self.assertEqual(
                    (await response.json())["error"],
                    "csrf_token_required",
                )
        for suffix in ("targets", "preview", "apply"):
            with self.subTest(route=f"ui-action/{suffix}"):
                response = await self.client.post(
                    f"/api/control-page/ui-action/{suffix}",
                    headers={"Origin": self.origin},
                    json={},
                )
                self.assertEqual(response.status, 403)
                self.assertEqual(
                    (await response.json())["error"],
                    "csrf_token_required",
                )

    async def test_preflight_options_is_non_mutating(self):
        response = await self.client.options(
            "/api/control-page/voice-validation/start",
            headers={"Origin": self.origin},
        )
        self.assertEqual(response.status, 204)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])

    async def test_ui_action_proxy_preserves_fail_closed_status(self):
        for suffix, payload in (
            ("targets", {}),
            (
                "preview",
                {
                    "elementId": "a" * 20,
                    "action": "invoke",
                    "postcondition": "target_absent",
                },
            ),
            (
                "apply",
                {
                    "confirmToken": "t" * 43,
                    "userConfirmed": True,
                },
            ),
        ):
            with self.subTest(route=suffix), patch.object(
                control_page_server,
                "proxy_json",
                new=AsyncMock(
                    return_value=control_page_server.json_response(
                        {
                            "ok": False,
                            "error": "ui_action_foreground_changed_since_preview",
                        },
                        status=409,
                    )
                ),
            ):
                response = await self.client.post(
                    f"/api/control-page/ui-action/{suffix}",
                    headers=self.headers(),
                    json=payload,
                )
                self.assertEqual(response.status, 409)
                self.assertEqual(
                    (await response.json())["error"],
                    "ui_action_foreground_changed_since_preview",
                )

    async def test_consent_preview_apply_and_revoke_use_bridge_ack(self):
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={"scope": "voice_validation_local"},
        )
        preview = await preview_response.json()
        self.assertEqual(preview_response.status, 200)
        self.assertFalse(preview["privacy"]["storesAudio"])
        self.assertFalse(preview["privacy"]["storesTranscript"])

        applied_response = await self.client.post(
            "/api/control-page/voice-capture-consent/apply",
            headers=self.headers(),
            json={
                "scope": "voice_validation_local",
                "confirmToken": preview["confirmToken"],
            },
        )
        applied = await applied_response.json()
        self.assertEqual(applied_response.status, 200)
        self.assertTrue(applied["consent"]["active"])
        self.assertTrue(applied["localBridge"]["mic"]["captureReady"])

        revoked_response = await self.client.post(
            "/api/control-page/voice-capture-consent/revoke",
            headers=self.headers(),
            json={},
        )
        revoked = await revoked_response.json()
        self.assertEqual(revoked_response.status, 200)
        self.assertEqual(revoked["consent"]["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_grant_resumes_local_preflight_and_binds_lease(self):
        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["local"]},
        )
        started = await started_response.json()
        self.assertEqual(started["session"]["state"], "preflight")

        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={"scope": "voice_validation_local"},
        )
        preview = await preview_response.json()
        applied_response = await self.client.post(
            "/api/control-page/voice-capture-consent/apply",
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        applied = await applied_response.json()

        self.assertEqual(applied_response.status, 200)
        self.assertEqual(applied["validationSession"]["state"], "running")
        self.assertEqual(
            applied["consent"]["validationSessionId"],
            started["session"]["sessionId"],
        )

    async def test_failed_capture_readiness_is_turned_back_off(self):
        self.mic_control.side_effect = [
            {
                "ok": True,
                "applied": True,
                "localBridge": {
                    "ready": True,
                    "micEnabled": True,
                    "mic": {"captureReady": False},
                },
            },
            {
                "ok": True,
                "applied": True,
                "localBridge": {
                    "ready": True,
                    "micEnabled": False,
                    "mic": {"captureReady": False},
                },
            },
        ]
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()
        response = await self.client.post(
            "/api/control-page/voice-capture-consent/apply",
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        payload = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(payload["consent"]["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_expired_unbound_consent_is_fail_closed_on_reconcile(self):
        now = [2_000.0]
        self.consent_manager.now = lambda: now[0]
        self.consent_manager.armed_ttl_sec = 1.0
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()
        applied_response = await self.client.post(
            "/api/control-page/voice-capture-consent/apply",
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        self.assertEqual(applied_response.status, 200)

        now[0] += 2.0
        status_response = await self.client.get(
            "/api/control-page/voice-capture-consent",
            headers={"Origin": self.origin},
        )
        status = await status_response.json()

        self.assertEqual(status_response.status, 200)
        self.assertEqual(status["consent"]["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_state_write_failure_after_enable_still_requests_mic_off(self):
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()
        with patch.object(
            self.consent_manager,
            "finish_apply",
            side_effect=OSError("read-only consent store"),
        ):
            response = await self.client.post(
                "/api/control-page/voice-capture-consent/apply",
                headers=self.headers(),
                json={"confirmToken": preview["confirmToken"]},
            )
        payload = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_state_write_failed",
        )
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )


if __name__ == "__main__":
    unittest.main()
