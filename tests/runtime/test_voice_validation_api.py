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
from evelyn_core.voice_validation import (  # noqa: E402
    SUITE_ID,
    VoiceValidationManager,
    active_validation_context,
)


READY_CAPABILITIES = {
    "voiceLocal": {"state": "ready", "ready": True, "blockers": []},
    "voiceDiscord": {"state": "ready", "ready": True, "blockers": []},
}
READY_HEALTH = {
    "capabilities": READY_CAPABILITIES,
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
            new=AsyncMock(return_value=READY_HEALTH),
        )
        self.raw_health_patch = patch.object(
            control_page_server.CONTROL_PAGE_RUNTIME_HEALTH_CACHE,
            "get",
            new=AsyncMock(return_value=READY_HEALTH),
        )
        self.mic_control = AsyncMock(side_effect=self.mic_control_result)
        self.mic_patch = patch.object(
            control_page_server,
            "request_local_bridge_mic_control",
            new=self.mic_control,
        )
        self.manager_patch.start()
        self.consent_manager_patch.start()
        self.health_mock = self.health_patch.start()
        self.raw_health_mock = self.raw_health_patch.start()
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
        self.raw_health_patch.stop()
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

    def start_manager_session(self):
        result = self.manager.start(
            suite=SUITE_ID,
            surfaces=("local",),
            capabilities=READY_CAPABILITIES,
        )
        self.assertTrue(result["ok"], result)
        return result["session"]

    def record_current(self, event, **payload):
        snapshot = self.manager.snapshot()
        step = snapshot["currentStep"]
        context = active_validation_context(
            surface=step["surface"],
            root=Path(self.temp_dir.name),
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

    def record_valid_current_normal_step(self):
        step = self.manager.snapshot()["currentStep"]
        self.record_current("stt_final", transcript=step["prompt"])
        self.record_current("turn_accepted")
        self.record_current("reply_started")
        self.record_current("reply_final")
        self.record_current("playback_started")
        self.record_current("playback_completed")

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
        self.raw_health_mock.assert_awaited_once_with(force=True)
        self.health_mock.assert_not_awaited()
        self.assertEqual(started["session"]["schema"], "voice_validation.session.v1")
        self.assertEqual(started["session"]["state"], "running")
        self.assertEqual(
            started["session"]["discordTarget"],
            {"guildId": "7", "channelId": "9"},
        )

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

    async def test_discord_start_requires_exactly_one_connected_listening_target(self):
        cases = (
            ([], "discord_target_unavailable"),
            (
                [
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
                ],
                "ambiguous_discord_target",
            ),
        )
        for rows, expected_error in cases:
            with self.subTest(error=expected_error):
                self.raw_health_mock.return_value = {
                    "capabilities": READY_CAPABILITIES,
                    "services": [
                        {
                            "id": "discord_bot",
                            "checks": [
                                {
                                    "kind": "artifact_json",
                                    "ok": True,
                                    "payload": {"voiceConnections": rows},
                                }
                            ],
                        }
                    ],
                }
                response = await self.client.post(
                    "/api/control-page/voice-validation/start",
                    headers=self.headers(),
                    json={"suite": "voice-p0.v1", "surfaces": ["discord"]},
                )
                payload = await response.json()

                self.assertEqual(response.status, 409)
                self.assertEqual(payload["error"], expected_error)
                self.assertEqual(self.manager.snapshot()["state"], "idle")

    async def test_unsupported_suite_precedes_discord_target_resolution(self):
        self.raw_health_mock.return_value = {
            "capabilities": READY_CAPABILITIES,
            "services": [],
        }

        response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.invalid", "surfaces": ["discord"]},
        )
        payload = await response.json()

        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"], "unsupported_suite")
        self.assertEqual(self.manager.snapshot()["state"], "idle")

    async def test_active_session_precedes_missing_discord_target(self):
        first = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["local"]},
        )
        self.assertEqual(first.status, 201)
        self.raw_health_mock.return_value = {
            "capabilities": READY_CAPABILITIES,
            "services": [],
        }

        response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["discord"]},
        )
        payload = await response.json()

        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"], "validation_session_active")

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

    async def test_confirm_requires_json_boolean_heard_value(self):
        invalid_values = ("true", "false", 1, 0, [], {}, None)
        with patch.object(self.manager, "confirm", wraps=self.manager.confirm) as confirm:
            for heard in invalid_values:
                with self.subTest(heard=heard):
                    response = await self.client.post(
                        "/api/control-page/voice-validation/confirm",
                        headers=self.headers(),
                        json={
                            "sessionId": "session-1",
                            "stepId": "01-wake",
                            "attempt": 1,
                            "heard": heard,
                        },
                    )
                    payload = await response.json()

                    self.assertEqual(response.status, 400)
                    self.assertEqual(payload["error"], "heard_boolean_required")
            self.assertEqual(confirm.call_count, 0)

            response = await self.client.post(
                "/api/control-page/voice-validation/confirm",
                headers=self.headers(),
                json={
                    "sessionId": "session-1",
                    "stepId": "01-wake",
                    "attempt": 1,
                    "heard": True,
                },
            )

            self.assertEqual(response.status, 409)
            self.assertEqual(confirm.call_count, 1)

    async def test_confirm_v1_omission_is_accepted_only_on_first_attempt(self):
        session = self.start_manager_session()
        step = session["currentStep"]
        self.record_valid_current_normal_step()

        explicit_null = await self.client.post(
            "/api/control-page/voice-validation/confirm",
            headers=self.headers(),
            json={
                "sessionId": session["sessionId"],
                "stepId": step["id"],
                "attempt": None,
                "heard": True,
            },
        )
        explicit_null_payload = await explicit_null.json()
        self.assertEqual(explicit_null.status, 409)
        self.assertEqual(
            explicit_null_payload["error"],
            "validation_attempt_revision_mismatch",
        )

        compatible = await self.client.post(
            "/api/control-page/voice-validation/confirm",
            headers=self.headers(),
            json={
                "sessionId": session["sessionId"],
                "stepId": step["id"],
                "heard": True,
            },
        )
        self.assertEqual(compatible.status, 200)
        self.assertTrue((await compatible.json())["ok"])

    async def test_confirm_omission_after_retry_requires_current_attempt(self):
        session = self.start_manager_session()
        step = session["currentStep"]
        self.record_current("stt_final", transcript="완전히 다른 말")

        retried = await self.client.post(
            "/api/control-page/voice-validation/retry",
            headers=self.headers(),
            json={
                "sessionId": session["sessionId"],
                "stepId": step["id"],
                "attempt": step["attempt"],
            },
        )
        retried_payload = await retried.json()
        self.assertEqual(retried.status, 200)
        current = retried_payload["session"]["currentStep"]
        self.assertEqual(current["attempt"], 2)
        self.record_valid_current_normal_step()

        omitted = await self.client.post(
            "/api/control-page/voice-validation/confirm",
            headers=self.headers(),
            json={
                "sessionId": session["sessionId"],
                "stepId": current["id"],
                "heard": True,
            },
        )
        omitted_payload = await omitted.json()
        self.assertEqual(omitted.status, 409)
        self.assertEqual(
            omitted_payload["error"],
            "validation_attempt_revision_mismatch",
        )

        accepted = await self.client.post(
            "/api/control-page/voice-validation/confirm",
            headers=self.headers(),
            json={
                "sessionId": session["sessionId"],
                "stepId": current["id"],
                "attempt": current["attempt"],
                "heard": True,
            },
        )
        self.assertEqual(accepted.status, 200)
        self.assertTrue((await accepted.json())["ok"])

    async def test_expiry_discovered_by_retry_revokes_local_capture_consent(self):
        now = [2_000.0]
        self.manager.now = lambda: now[0]
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
            json={"suite": "voice-p0.v1", "surfaces": ["local"]},
        )
        started = await started_response.json()
        now[0] += 1800

        response = await self.client.post(
            "/api/control-page/voice-validation/retry",
            headers=self.headers(),
            json={
                "sessionId": started["session"]["sessionId"],
                "stepId": started["session"]["currentStep"]["id"],
            },
        )
        payload = await response.json()

        self.assertEqual(response.status, 409)
        self.assertEqual(payload["session"]["failureCode"], "session_expired")
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertIn(False, [call.args[0] for call in self.mic_control.await_args_list])

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
