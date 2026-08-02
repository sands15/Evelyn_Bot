from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
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
            auth_token="voice-capture-test-auth-token-0123456789",
        )
        recovery = self.consent_manager.begin_revoke(reason="test_setup")
        self.assertTrue(recovery["controlRequired"], recovery)
        seeded = self.consent_manager.finish_revoke(applied=True)
        self.assertTrue(seeded["ok"], seeded)
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
        self.artifacts_patch = patch.object(
            control_page_server,
            "get_runtime_artifacts_root",
            return_value=Path(self.temp_dir.name),
        )
        self.manager_patch.start()
        self.consent_manager_patch.start()
        self.health_mock = self.health_patch.start()
        self.raw_health_mock = self.raw_health_patch.start()
        self.mic_patch.start()
        self.artifacts_patch.start()
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
        self.artifacts_patch.stop()
        self.mic_patch.stop()
        self.raw_health_patch.stop()
        self.health_patch.stop()
        self.consent_manager_patch.stop()
        self.manager_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def mic_control_result(enabled, *, source):
        revision = 101 if enabled else 102
        action_id = "cd" * 16
        bridge_digest = "ab" * 32
        capture_stopped = not bool(enabled)
        return {
            "ok": True,
            "applied": True,
            "httpStatus": 200,
            "request": {
                "revision": revision,
                "actionId": action_id,
                "enabled": bool(enabled),
                "source": source,
                "bridgeInstanceDigest": bridge_digest,
            },
            "ack": {
                "schema": "local_io_bridge.mic-control-ack.v1",
                "actionId": action_id,
                "requestRevision": revision,
                "observedRevision": revision,
                "enabled": bool(enabled),
                "bridgeInstanceDigest": bridge_digest,
                "state": "applied",
                "captureStopped": capture_stopped,
            },
            "localBridge": {
                "enabled": True,
                "ready": True,
                "micEnabled": bool(enabled),
                "micControlRevision": revision,
                "micControlActionId": action_id,
                "micControlPendingRevision": 0,
                "micControlPendingActionId": "",
                "micControlState": "applied",
                "micControlDesiredEnabled": bool(enabled),
                "micControlError": "",
                "micCaptureStopped": capture_stopped,
                "stale": False,
                "lastError": "",
                "mic": {
                    "enabled": bool(enabled),
                    "captureReady": bool(enabled),
                    "captureActive": bool(enabled),
                    "captureStopped": capture_stopped,
                },
            },
        }

    @classmethod
    def mic_control_without_exact_ack(cls, enabled, *, source):
        result = cls.mic_control_result(enabled, source=source)
        result.pop("ack")
        return result

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

    async def test_preview_from_idle_cannot_enable_during_discord_session(self):
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()
        self.assertEqual(preview_response.status, 200)

        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["discord"]},
        )
        self.assertEqual(started_response.status, 201)

        applied_response = await self.client.post(
            "/api/control-page/voice-capture-consent/apply",
            headers=self.headers(),
            json={"confirmToken": preview["confirmToken"]},
        )
        applied = await applied_response.json()

        self.assertEqual(applied_response.status, 409)
        self.assertEqual(
            applied["error"],
            "voice_capture_confirm_token_stale",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.mic_control.assert_not_awaited()

    async def test_discord_start_revokes_capture_applied_while_idle(self):
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

        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["discord"]},
        )

        self.assertEqual(started_response.status, 201)
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_validation_mutation_exceptions_force_capture_off(self):
        session = self.start_manager_session()
        cases = (
            (
                "confirm",
                "/api/control-page/voice-validation/confirm",
                {
                    "sessionId": session["sessionId"],
                    "stepId": session["currentStep"]["id"],
                    "heard": True,
                },
            ),
            (
                "retry",
                "/api/control-page/voice-validation/retry",
                {
                    "sessionId": session["sessionId"],
                    "stepId": session["currentStep"]["id"],
                },
            ),
            (
                "abort",
                "/api/control-page/voice-validation/abort",
                {"sessionId": session["sessionId"]},
            ),
        )
        for method_name, path, payload in cases:
            with self.subTest(operation=method_name):
                binding = control_page_server._voice_capture_validation_binding(
                    self.manager.snapshot()
                )
                preview = self.consent_manager.preview(
                    validation_binding=binding
                )
                pending = self.consent_manager.begin_apply(
                    confirm_token=preview["confirmToken"],
                    validation_binding=binding,
                )
                self.consent_manager.finish_apply(
                    lease_id=pending["leaseId"],
                    applied=True,
                    capture_ready=True,
                )
                self.consent_manager.bind_validation_session(
                    session["sessionId"]
                )
                self.mic_control.reset_mock()

                with patch.object(
                    self.manager,
                    method_name,
                    side_effect=OSError("private report write failure"),
                ):
                    response = await self.client.post(
                        path,
                        headers=self.headers(),
                        json=payload,
                    )
                body = await response.json()

                self.assertEqual(response.status, 503)
                self.assertEqual(
                    body["error"],
                    "voice_validation_mutation_failed",
                )
                self.assertEqual(
                    self.consent_manager.status()["state"],
                    "inactive",
                )
                self.assertEqual(
                    [call.args[0] for call in self.mic_control.await_args_list],
                    [False],
                )

    async def test_validation_mutation_snapshot_failure_forces_capture_off(self):
        session = self.start_manager_session()
        binding = control_page_server._voice_capture_validation_binding(session)
        preview = self.consent_manager.preview(validation_binding=binding)
        pending = self.consent_manager.begin_apply(
            confirm_token=preview["confirmToken"],
            validation_binding=binding,
        )
        self.consent_manager.finish_apply(
            lease_id=pending["leaseId"],
            applied=True,
            capture_ready=True,
        )
        self.consent_manager.bind_validation_session(session["sessionId"])
        self.mic_control.reset_mock()

        with (
            patch.object(
                self.manager,
                "confirm",
                return_value={
                    "ok": False,
                    "error": "validation_session_not_found",
                },
            ),
            patch.object(
                self.manager,
                "snapshot",
                side_effect=OSError("private report write failure"),
            ),
        ):
            response = await self.client.post(
                "/api/control-page/voice-validation/confirm",
                headers=self.headers(),
                json={
                    "sessionId": session["sessionId"],
                    "stepId": session["currentStep"]["id"],
                    "heard": True,
                },
            )
        body = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(body["error"], "voice_validation_mutation_failed")
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [False],
        )

    async def test_off_without_exact_ack_stays_revoking_until_reconcile(self):
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

        self.mic_control.side_effect = [
            self.mic_control_without_exact_ack(False, source="test"),
            self.mic_control_result(False, source="test"),
        ]
        revoked_response = await self.client.post(
            "/api/control-page/voice-capture-consent/revoke",
            headers=self.headers(),
            json={},
        )
        revoked = await revoked_response.json()

        self.assertEqual(revoked_response.status, 503)
        self.assertFalse(revoked["localBridge"]["micEnabled"])
        self.assertFalse(revoked["controlApplied"])
        self.assertEqual(self.consent_manager.status()["state"], "revoking")

        reconciled_response = await self.client.get(
            "/api/control-page/voice-capture-consent",
            headers={"Origin": self.origin},
        )
        reconciled = await reconciled_response.json()

        self.assertEqual(reconciled_response.status, 200)
        self.assertEqual(reconciled["consent"]["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False, False],
        )

    async def test_malformed_bridge_payload_stays_revoking_until_retry(self):
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

        self.mic_control.side_effect = [
            {
                "ok": False,
                "applied": False,
                "error": "mic_control_ack_invalid",
                "localBridge": "malformed-bridge-payload",
            },
            self.mic_control_result(False, source="test"),
        ]
        revoked_response = await self.client.post(
            "/api/control-page/voice-capture-consent/revoke",
            headers=self.headers(),
            json={},
        )
        revoked = await revoked_response.json()

        self.assertEqual(revoked_response.status, 503)
        self.assertEqual(revoked["localBridge"], {})
        self.assertEqual(self.consent_manager.status()["state"], "revoking")

        reconciled_response = await self.client.get(
            "/api/control-page/voice-capture-consent",
            headers={"Origin": self.origin},
        )
        reconciled = await reconciled_response.json()

        self.assertEqual(reconciled_response.status, 200)
        self.assertEqual(reconciled["consent"]["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False, False],
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
        not_ready = self.mic_control_result(True, source="test")
        not_ready["localBridge"]["mic"]["captureReady"] = False
        self.mic_control.side_effect = [
            not_ready,
            self.mic_control_result(False, source="test"),
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

    async def test_state_write_failure_retries_invalid_first_off_on_reconcile(self):
        self.mic_control.side_effect = [
            self.mic_control_result(True, source="test"),
            self.mic_control_without_exact_ack(False, source="test"),
            self.mic_control_result(False, source="test"),
        ]
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
        self.assertEqual(self.consent_manager.status()["state"], "revoking")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

        reconciled_response = await self.client.get(
            "/api/control-page/voice-capture-consent",
            headers={"Origin": self.origin},
        )
        reconciled = await reconciled_response.json()

        self.assertEqual(reconciled_response.status, 200)
        self.assertEqual(reconciled["consent"]["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False, False],
        )

    async def test_cancelled_mic_on_wait_cleans_up_with_exact_off_ack(self):
        on_waiting = asyncio.Event()

        async def wait_for_control(enabled, *, source):
            if enabled:
                on_waiting.set()
                await asyncio.Future()
            return self.mic_control_result(enabled, source=source)

        self.mic_control.side_effect = wait_for_control
        preview = self.consent_manager.preview()

        class _Request:
            app = self.client.server.app

            async def json(self):
                return {"confirmToken": preview["confirmToken"]}

        apply_task = asyncio.create_task(
            control_page_server.voice_capture_consent_apply_handler(_Request())
        )
        await on_waiting.wait()
        apply_task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await apply_task

        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_terminal_session_during_apply_cleans_up_capture(self):
        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["local"]},
        )
        started = await started_response.json()
        self.assertEqual(started["session"]["state"], "preflight")

        on_waiting = asyncio.Event()
        release_on = asyncio.Event()

        async def wait_for_control(enabled, *, source):
            if enabled:
                on_waiting.set()
                await release_on.wait()
            return self.mic_control_result(enabled, source=source)

        self.mic_control.side_effect = wait_for_control
        preview = self.consent_manager.preview(
            validation_binding=control_page_server._voice_capture_validation_binding(
                started["session"]
            )
        )
        apply_task = asyncio.create_task(
            self.client.post(
                "/api/control-page/voice-capture-consent/apply",
                headers=self.headers(),
                json={"confirmToken": preview["confirmToken"]},
            )
        )
        await on_waiting.wait()

        aborted = self.manager.abort(
            session_id=str(started["session"]["sessionId"])
        )
        self.assertTrue(aborted["ok"], aborted)
        self.assertEqual(aborted["session"]["state"], "aborted")
        release_on.set()

        response = await apply_task
        payload = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_activation_failed",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_terminal_snapshot_after_apply_bind_cleans_up_capture(self):
        running = self.start_manager_session()
        terminal = {**running, "state": "failed"}
        preview = self.consent_manager.preview(
            validation_binding=control_page_server._voice_capture_validation_binding(
                running
            )
        )

        with patch.object(
            self.manager,
            "snapshot",
            side_effect=[running, running, terminal],
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
            "voice_capture_consent_activation_failed",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_validation_get_reconciles_a_terminalizing_snapshot(self):
        running = self.start_manager_session()
        preview = self.consent_manager.preview()
        started = self.consent_manager.begin_apply(
            confirm_token=preview["confirmToken"]
        )
        activated = self.consent_manager.finish_apply(
            lease_id=started["leaseId"],
            applied=True,
            capture_ready=True,
        )
        self.assertTrue(activated["ok"], activated)
        bound = self.consent_manager.bind_validation_session(
            str(running["sessionId"])
        )
        self.assertTrue(bound["ok"], bound)
        terminal = {**running, "state": "failed"}

        with patch.object(self.manager, "snapshot", return_value=terminal):
            response = await self.client.get(
                "/api/control-page/voice-validation",
                headers={"Origin": self.origin},
            )
        payload = await response.json()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["session"]["state"], "failed")
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [False],
        )

    async def test_validation_get_reports_incomplete_terminal_cleanup(self):
        running = self.start_manager_session()
        preview = self.consent_manager.preview(
            validation_binding=control_page_server._voice_capture_validation_binding(
                running
            )
        )
        started = self.consent_manager.begin_apply(
            confirm_token=preview["confirmToken"],
            validation_binding=control_page_server._voice_capture_validation_binding(
                running
            ),
        )
        activated = self.consent_manager.finish_apply(
            lease_id=started["leaseId"],
            applied=True,
            capture_ready=True,
        )
        self.assertTrue(activated["ok"], activated)
        bound = self.consent_manager.bind_validation_session(
            str(running["sessionId"])
        )
        self.assertTrue(bound["ok"], bound)
        terminal = {**running, "state": "failed"}
        self.mic_control.side_effect = self.mic_control_without_exact_ack

        with patch.object(self.manager, "snapshot", return_value=terminal):
            response = await self.client.get(
                "/api/control-page/voice-validation",
                headers={"Origin": self.origin},
            )
        payload = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_cleanup_failed",
        )
        self.assertEqual(payload["session"]["state"], "failed")
        self.assertEqual(self.consent_manager.status()["state"], "revoking")
        self.assertFalse(payload["cleanup"]["controlApplied"])

    async def test_abort_reports_incomplete_capture_cleanup(self):
        running = self.start_manager_session()
        binding = control_page_server._voice_capture_validation_binding(running)
        preview = self.consent_manager.preview(validation_binding=binding)
        started = self.consent_manager.begin_apply(
            confirm_token=preview["confirmToken"],
            validation_binding=binding,
        )
        activated = self.consent_manager.finish_apply(
            lease_id=started["leaseId"],
            applied=True,
            capture_ready=True,
        )
        self.assertTrue(activated["ok"], activated)
        bound = self.consent_manager.bind_validation_session(
            str(running["sessionId"])
        )
        self.assertTrue(bound["ok"], bound)
        self.mic_control.side_effect = self.mic_control_without_exact_ack

        response = await self.client.post(
            "/api/control-page/voice-validation/abort",
            headers=self.headers(),
            json={"sessionId": running["sessionId"]},
        )
        payload = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_cleanup_failed",
        )
        self.assertEqual(payload["session"]["state"], "aborted")
        self.assertEqual(self.consent_manager.status()["state"], "revoking")

    async def test_local_start_terminal_snapshot_after_bind_cleans_up(self):
        preview = self.consent_manager.preview()
        started = self.consent_manager.begin_apply(
            confirm_token=preview["confirmToken"]
        )
        activated = self.consent_manager.finish_apply(
            lease_id=started["leaseId"],
            applied=True,
            capture_ready=True,
        )
        self.assertTrue(activated["ok"], activated)
        real_snapshot = self.manager.snapshot

        def terminalize_post_bind(*args, **kwargs):
            session = real_snapshot(*args, **kwargs)
            if session.get("state") == "running":
                return {**session, "state": "failed"}
            return session

        with patch.object(
            self.manager,
            "snapshot",
            side_effect=terminalize_post_bind,
        ):
            response = await self.client.post(
                "/api/control-page/voice-validation/start",
                headers=self.headers(),
                json={"suite": "voice-p0.v1", "surfaces": ["local"]},
            )
        payload = await response.json()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "voice_capture_validation_changed_during_bind",
        )
        self.assertEqual(payload["session"]["state"], "failed")
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [False],
        )

    async def test_post_activation_health_failure_cleans_up_capture(self):
        self.health_mock.side_effect = RuntimeError("health unavailable")
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
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_activation_failed",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_post_activation_resume_failure_cleans_up_capture(self):
        started_response = await self.client.post(
            "/api/control-page/voice-validation/start",
            headers=self.headers(),
            json={"suite": "voice-p0.v1", "surfaces": ["local"]},
        )
        self.assertEqual(
            (await started_response.json())["session"]["state"],
            "preflight",
        )
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()

        with patch.object(
            self.manager,
            "resume_after_preflight",
            side_effect=RuntimeError("resume failed"),
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
            "voice_capture_consent_activation_failed",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_post_activation_bind_failure_cleans_up_capture(self):
        running = self.start_manager_session()
        self.assertEqual(running["state"], "running")
        preview_response = await self.client.post(
            "/api/control-page/voice-capture-consent/preview",
            headers=self.headers(),
            json={},
        )
        preview = await preview_response.json()

        with patch.object(
            self.consent_manager,
            "bind_validation_session",
            side_effect=RuntimeError("bind failed"),
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
            "voice_capture_consent_activation_failed",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [True, False],
        )

    async def test_host_lease_publish_failure_blocks_mic_on_and_forces_off(self):
        preview = self.consent_manager.preview()

        class _Request:
            app = self.client.server.app

            async def json(self):
                return {"confirmToken": preview["confirmToken"]}

        with patch.object(
            self.consent_manager,
            "publish_host_lease",
            side_effect=OSError("private heartbeat write failure"),
        ):
            response = await control_page_server.voice_capture_consent_apply_handler(
                _Request()
            )
        payload = json.loads(response.text)

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_heartbeat_write_failed",
        )
        self.assertEqual(self.consent_manager.status()["state"], "inactive")
        self.assertEqual(
            [call.args[0] for call in self.mic_control.await_args_list],
            [False],
        )


class VoiceCaptureMicTransportTests(unittest.IsolatedAsyncioTestCase):
    internal_token = "transport-test-internal-token-1234567890"

    async def call_transport(
        self,
        *,
        enabled: bool,
        source: str,
        get_status: int = 200,
        get_payload=None,
        get_raw_text: str | None = None,
    ) -> tuple[dict, list[dict]]:
        calls: list[dict] = []
        fence = {
            "schema": "local_io_bridge.mic-enable-fence.v1",
            "epoch": "ef" * 16,
            "disableGeneration": 7,
        }

        async def mic_handler(request: web.Request) -> web.StreamResponse:
            call = {
                "method": request.method,
                "internalToken": request.headers.get(
                    control_page_server.EVELYN_INTERNAL_CONTROL_HEADER
                ),
            }
            if request.method == "POST":
                call["body"] = await request.json()
            calls.append(call)
            if request.method == "GET":
                if get_raw_text is not None:
                    return web.Response(
                        text=get_raw_text,
                        status=get_status,
                        content_type="application/json",
                    )
                payload = (
                    get_payload
                    if get_payload is not None
                    else {"ok": True, "enableFence": fence}
                )
                return web.json_response(payload, status=get_status)
            return web.json_response(
                {"ok": True, "applied": True},
                status=200,
            )

        app = web.Application()
        app.router.add_route("*", "/api/local-bridge/mic", mic_handler)
        server = TestServer(app)
        await server.start_server()
        try:
            bot_api_base = str(server.make_url("/")).rstrip("/")
            with (
                patch.object(
                    control_page_server,
                    "BOT_API_BASE",
                    bot_api_base,
                ),
                patch.object(
                    control_page_server,
                    "EVELYN_INTERNAL_CONTROL_TOKEN",
                    self.internal_token,
                ),
            ):
                result = await control_page_server.request_local_bridge_mic_control(
                    enabled,
                    source=source,
                )
        finally:
            await server.close()
        return result, calls

    async def test_on_gets_fence_and_posts_exact_internal_contract(self):
        source = "voice_capture_consent:transport-test"

        result, calls = await self.call_transport(
            enabled=True,
            source=source,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([call["method"] for call in calls], ["GET", "POST"])
        self.assertEqual(
            [call["internalToken"] for call in calls],
            [self.internal_token, self.internal_token],
        )
        self.assertEqual(
            calls[1]["body"],
            {
                "enabled": True,
                "source": source,
                "purpose": "voice_capture_consent",
                "enableFence": {
                    "schema": "local_io_bridge.mic-enable-fence.v1",
                    "epoch": "ef" * 16,
                    "disableGeneration": 7,
                },
            },
        )

    async def test_off_posts_internal_contract_without_fence_get(self):
        source = "voice_capture_consent:transport-off"

        result, calls = await self.call_transport(
            enabled=False,
            source=source,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([call["method"] for call in calls], ["POST"])
        self.assertEqual(calls[0]["internalToken"], self.internal_token)
        self.assertEqual(
            calls[0]["body"],
            {
                "enabled": False,
                "source": source,
            },
        )

    async def test_on_does_not_post_when_fence_get_is_forbidden(self):
        result, calls = await self.call_transport(
            enabled=True,
            source="voice_capture_consent:fence-forbidden",
            get_status=403,
            get_payload={"ok": False, "error": "mic_control_unauthorized"},
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["httpStatus"], 403)
        self.assertEqual([call["method"] for call in calls], ["GET"])
        self.assertEqual(calls[0]["internalToken"], self.internal_token)

    async def test_on_does_not_post_when_fence_response_is_corrupt(self):
        result, calls = await self.call_transport(
            enabled=True,
            source="voice_capture_consent:fence-corrupt",
            get_raw_text="{not-valid-json",
        )

        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["error"], "mic_enable_fence_unavailable")
        self.assertEqual([call["method"] for call in calls], ["GET"])
        self.assertEqual(calls[0]["internalToken"], self.internal_token)


class VoiceCaptureOwnerContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_or_unavailable_owner_aborts_before_consent(self):
        for error, expected in (
            (
                control_page_server.MinecraftOwnerLockBusy(
                    "minecraft_owner_lock_busy"
                ),
                "voice_capture_owner_conflict",
            ),
            (
                control_page_server.MinecraftOwnerLockUnavailable(
                    "minecraft_owner_lock_unavailable"
                ),
                "voice_capture_owner_lock_unavailable",
            ),
        ):
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as temp_dir:
                    artifact_root = Path(temp_dir)
                    consent_root = artifact_root / "voice_capture_consent"
                    consent_root.mkdir(parents=True)
                    state_path = consent_root / "state.json"
                    heartbeat_path = consent_root / "heartbeat.json"
                    state_path.write_bytes(b"state-sentinel")
                    heartbeat_path.write_bytes(b"heartbeat-sentinel")

                    consent_entered = False
                    owner_lock = Mock()
                    owner_lock.acquire.side_effect = error
                    manager_getter = Mock()
                    mic_control = AsyncMock()

                    async def forbidden_consent(_app):
                        nonlocal consent_entered
                        consent_entered = True
                        yield

                    with (
                        patch.object(
                            control_page_server,
                            "get_runtime_artifacts_root",
                            return_value=artifact_root,
                        ),
                        patch.object(
                            control_page_server,
                            "MinecraftOwnerLock",
                            return_value=owner_lock,
                        ),
                        patch.object(
                            control_page_server,
                            "_voice_capture_consent_context",
                            new=forbidden_consent,
                        ),
                        patch.object(
                            control_page_server,
                            "get_voice_capture_consent_manager",
                            new=manager_getter,
                        ),
                        patch.object(
                            control_page_server,
                            "request_local_bridge_mic_control",
                            new=mic_control,
                        ),
                    ):
                        runner = web.AppRunner(
                            control_page_server.create_app()
                        )
                        try:
                            with self.assertRaises(RuntimeError) as raised:
                                await runner.setup()
                        finally:
                            await runner.cleanup()

                    self.assertEqual(str(raised.exception), expected)
                    self.assertFalse(consent_entered)
                    manager_getter.assert_not_called()
                    mic_control.assert_not_awaited()
                    owner_lock.release.assert_not_called()
                    self.assertEqual(
                        state_path.read_bytes(),
                        b"state-sentinel",
                    )
                    self.assertEqual(
                        heartbeat_path.read_bytes(),
                        b"heartbeat-sentinel",
                    )

    async def test_owner_lock_wraps_consent_cleanup_and_releases_for_successor(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir)
            lock_path = (
                artifact_root
                / "voice_capture_consent"
                / "owner_claim.lock"
            )
            events: list[str] = []

            async def observed_consent(_app):
                events.append("consent_started")
                try:
                    yield
                finally:
                    events.append("consent_cleanup_started")
                    contender = control_page_server.MinecraftOwnerLock(
                        lock_path
                    )
                    with self.assertRaises(
                        control_page_server.MinecraftOwnerLockBusy
                    ):
                        contender.acquire()
                    events.append("owner_still_held")

            configured = control_page_server.create_app()
            self.assertIs(
                configured.cleanup_ctx[0],
                control_page_server._voice_capture_owner_context,
            )
            self.assertIs(
                configured.cleanup_ctx[1],
                control_page_server._voice_capture_consent_context,
            )

            app = web.Application()
            app.cleanup_ctx.append(
                control_page_server._voice_capture_owner_context
            )
            app.cleanup_ctx.append(observed_consent)
            runner = web.AppRunner(app)
            with patch.object(
                control_page_server,
                "get_runtime_artifacts_root",
                return_value=artifact_root,
            ):
                await runner.setup()
                self.assertEqual(events, ["consent_started"])
                await runner.cleanup()

            self.assertEqual(
                events,
                [
                    "consent_started",
                    "consent_cleanup_started",
                    "owner_still_held",
                ],
            )
            successor = control_page_server.MinecraftOwnerLock(lock_path)
            successor.acquire()
            self.assertTrue(successor.acquired)
            successor.release()

    async def test_process_crash_releases_owner_without_loser_side_effects(
        self,
    ):
        worker_code = """
import asyncio
import os
import sys
from pathlib import Path

from evelyn_core import control_page_server

control_page_server.get_runtime_artifacts_root = lambda: Path(sys.argv[1])

async def main():
    context = control_page_server._voice_capture_owner_context({})
    await anext(context)
    print("READY", flush=True)
    sys.stdin.readline()
    os._exit(78)

asyncio.run(main())
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir)
            consent_root = artifact_root / "voice_capture_consent"
            consent_root.mkdir(parents=True)
            state_path = consent_root / "state.json"
            heartbeat_path = consent_root / "heartbeat.json"
            state_path.write_bytes(b"state-sentinel")
            heartbeat_path.write_bytes(b"heartbeat-sentinel")
            environment = os.environ.copy()
            python_path = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = os.pathsep.join(
                part
                for part in (str(RUNTIME_ROOT), python_path)
                if part
            )
            process = subprocess.Popen(
                [sys.executable, "-c", worker_code, str(artifact_root)],
                cwd=str(REPO_ROOT),
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertIsNotNone(process.stdout)
                ready = await asyncio.wait_for(
                    asyncio.to_thread(process.stdout.readline),
                    timeout=10.0,
                )
                if ready.strip() != "READY":
                    process.kill()
                    stdout, stderr = process.communicate(timeout=10)
                    self.fail(
                        "owner worker did not become ready: "
                        f"stdout={ready + stdout!r} stderr={stderr!r}"
                    )

                with patch.object(
                    control_page_server,
                    "get_runtime_artifacts_root",
                    return_value=artifact_root,
                ):
                    loser = (
                        control_page_server._voice_capture_owner_context({})
                    )
                    with self.assertRaises(RuntimeError) as raised:
                        await anext(loser)
                    self.assertEqual(
                        str(raised.exception),
                        "voice_capture_owner_conflict",
                    )
                    self.assertEqual(
                        state_path.read_bytes(),
                        b"state-sentinel",
                    )
                    self.assertEqual(
                        heartbeat_path.read_bytes(),
                        b"heartbeat-sentinel",
                    )

                    self.assertIsNotNone(process.stdin)
                    process.stdin.write("\n")
                    process.stdin.flush()
                    return_code = await asyncio.to_thread(
                        process.wait,
                        timeout=10,
                    )
                    self.assertEqual(return_code, 78)

                    successor = (
                        control_page_server._voice_capture_owner_context({})
                    )
                    await anext(successor)
                    await successor.aclose()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                for stream in (
                    process.stdin,
                    process.stdout,
                    process.stderr,
                ):
                    if stream is not None:
                        stream.close()

    async def test_cancelled_lease_publish_waits_for_worker_completion(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def publish_host_lease():
            started.set()
            if not release.wait(timeout=5):
                raise TimeoutError("publish worker was not released")
            finished.set()
            return {"ok": True}

        manager = Mock(publish_host_lease=publish_host_lease)
        publishing = asyncio.create_task(
            control_page_server._publish_voice_capture_host_lease(manager)
        )
        try:
            self.assertTrue(
                await asyncio.to_thread(started.wait, 2),
                "publish worker did not start",
            )
            publishing.cancel()
            await asyncio.sleep(0)
            self.assertFalse(publishing.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await publishing
            self.assertTrue(finished.is_set())
        finally:
            release.set()
            if not publishing.done():
                publishing.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await publishing

    async def test_cancelled_cleanup_holds_owner_until_drain_and_revoke(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            artifact_root = Path(temp_dir)
            lock_path = (
                artifact_root
                / "voice_capture_consent"
                / "owner_claim.lock"
            )
            publish_started = threading.Event()
            publish_release = threading.Event()
            shutdown_started = asyncio.Event()
            revoked = asyncio.Event()
            publish_calls = 0

            def publish_host_lease():
                nonlocal publish_calls
                publish_calls += 1
                if publish_calls == 1:
                    return {"ok": True}
                publish_started.set()
                if not publish_release.wait(timeout=5):
                    raise TimeoutError("heartbeat publisher was not released")
                return {"ok": True}

            manager = Mock(
                status=Mock(return_value={"captureMayBeActive": True}),
                publish_host_lease=Mock(side_effect=publish_host_lease),
            )

            async def revoke(_app, *, reason):
                self.assertEqual(reason, "control_page_shutdown")
                revoked.set()
                return {"ok": True}

            original_shutdown = (
                control_page_server._shutdown_voice_capture_consent
            )

            async def observed_shutdown(app, tasks):
                shutdown_started.set()
                await original_shutdown(app, tasks)

            app = web.Application()
            app[control_page_server.VOICE_CAPTURE_CONSENT_LOCK_KEY] = (
                asyncio.Lock()
            )
            app.cleanup_ctx.append(
                control_page_server._voice_capture_owner_context
            )
            app.cleanup_ctx.append(
                control_page_server._voice_capture_consent_context
            )
            runner = web.AppRunner(app)
            cleanup: asyncio.Task[None] | None = None
            try:
                with (
                    patch.object(
                        control_page_server,
                        "get_runtime_artifacts_root",
                        return_value=artifact_root,
                    ),
                    patch.object(
                        control_page_server,
                        "get_voice_capture_consent_manager",
                        return_value=manager,
                    ),
                    patch.object(
                        control_page_server,
                        "_reconcile_voice_capture_consent",
                        new=AsyncMock(return_value={"ok": True}),
                    ),
                    patch.object(
                        control_page_server,
                        "_revoke_voice_capture_consent",
                        new=revoke,
                    ),
                    patch.object(
                        control_page_server,
                        "_shutdown_voice_capture_consent",
                        new=observed_shutdown,
                    ),
                ):
                    await runner.setup()
                    self.assertTrue(
                        await asyncio.to_thread(publish_started.wait, 2),
                        "heartbeat publisher did not start",
                    )
                    cleanup = asyncio.create_task(runner.cleanup())
                    await asyncio.wait_for(
                        shutdown_started.wait(),
                        timeout=2.0,
                    )
                    cleanup.cancel()
                    await asyncio.sleep(0)

                    self.assertFalse(cleanup.done())
                    self.assertFalse(revoked.is_set())
                    contender = control_page_server.MinecraftOwnerLock(
                        lock_path
                    )
                    with self.assertRaises(
                        control_page_server.MinecraftOwnerLockBusy
                    ):
                        contender.acquire()

                    publish_release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await cleanup
                    self.assertTrue(revoked.is_set())

                successor = control_page_server.MinecraftOwnerLock(lock_path)
                successor.acquire()
                self.assertTrue(successor.acquired)
                successor.release()
            finally:
                publish_release.set()
                if cleanup is not None and not cleanup.done():
                    cleanup.cancel()
                    try:
                        await cleanup
                    except asyncio.CancelledError:
                        pass


class VoiceCaptureConsentContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_retries_after_recovery_attempt_raises(self):
        app = {
            control_page_server.VOICE_CAPTURE_CONSENT_LOCK_KEY: asyncio.Lock(),
        }
        recovery_retried = asyncio.Event()
        reconcile_calls = 0
        recovery_calls = 0
        manager = Mock(publish_host_lease=Mock(return_value={}))

        async def reconcile(_app, **_kwargs):
            nonlocal reconcile_calls
            reconcile_calls += 1
            if reconcile_calls == 1:
                return {"ok": True}
            raise RuntimeError("reconcile failed")

        async def recover(_app, **_kwargs):
            nonlocal recovery_calls
            recovery_calls += 1
            if recovery_calls == 1:
                raise RuntimeError("recovery failed")
            recovery_retried.set()
            return {"ok": False}

        with (
            patch.object(
                control_page_server,
                "_reconcile_voice_capture_consent",
                side_effect=reconcile,
            ),
            patch.object(
                control_page_server,
                "get_voice_capture_consent_manager",
                return_value=manager,
            ),
            patch.object(
                control_page_server,
                "_force_voice_capture_recovery",
                side_effect=recover,
            ),
            patch.object(
                control_page_server,
                "_revoke_voice_capture_consent",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch.object(
                control_page_server,
                "VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC",
                0.001,
            ),
        ):
            context = control_page_server._voice_capture_consent_context(app)
            await anext(context)
            try:
                await asyncio.wait_for(recovery_retried.wait(), timeout=1.0)
            finally:
                await context.aclose()

        self.assertGreaterEqual(reconcile_calls, 3)
        self.assertGreaterEqual(recovery_calls, 2)

    async def test_startup_recovery_failure_still_starts_monitor(self):
        app = {
            control_page_server.VOICE_CAPTURE_CONSENT_LOCK_KEY: asyncio.Lock(),
        }
        monitor_ran = asyncio.Event()
        reconcile_calls = 0
        manager = Mock(publish_host_lease=Mock(return_value={}))

        async def reconcile(_app, **_kwargs):
            nonlocal reconcile_calls
            reconcile_calls += 1
            if reconcile_calls == 1:
                raise RuntimeError("startup reconcile failed")
            monitor_ran.set()
            return {"ok": True}

        with (
            patch.object(
                control_page_server,
                "_reconcile_voice_capture_consent",
                side_effect=reconcile,
            ),
            patch.object(
                control_page_server,
                "get_voice_capture_consent_manager",
                return_value=manager,
            ),
            patch.object(
                control_page_server,
                "_force_voice_capture_recovery",
                new=AsyncMock(side_effect=RuntimeError("startup recovery failed")),
            ),
            patch.object(
                control_page_server,
                "_revoke_voice_capture_consent",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch.object(
                control_page_server,
                "VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC",
                0.001,
            ),
        ):
            context = control_page_server._voice_capture_consent_context(app)
            await anext(context)
            try:
                await asyncio.wait_for(monitor_ran.wait(), timeout=1.0)
            finally:
                await context.aclose()

        self.assertGreaterEqual(reconcile_calls, 2)

    async def test_owner_heartbeat_continues_while_consent_lock_is_held(self):
        lock = asyncio.Lock()
        app = {control_page_server.VOICE_CAPTURE_CONSENT_LOCK_KEY: lock}
        heartbeat_published = asyncio.Event()
        loop = asyncio.get_running_loop()
        publish_calls = 0

        def publish_host_lease():
            nonlocal publish_calls
            publish_calls += 1
            if publish_calls >= 2:
                loop.call_soon_threadsafe(heartbeat_published.set)
            return {}

        manager = Mock(
            status=Mock(return_value={"captureMayBeActive": True}),
            publish_host_lease=Mock(side_effect=publish_host_lease),
        )

        async def reconcile(_app, **_kwargs):
            async with lock:
                return {"ok": True}

        with (
            patch.object(
                control_page_server,
                "get_voice_capture_consent_manager",
                return_value=manager,
            ),
            patch.object(
                control_page_server,
                "_reconcile_voice_capture_consent",
                side_effect=reconcile,
            ),
            patch.object(
                control_page_server,
                "_revoke_voice_capture_consent",
                new=AsyncMock(return_value={"ok": True}),
            ),
            patch.object(
                control_page_server,
                "VOICE_CAPTURE_CONSENT_MONITOR_INTERVAL_SEC",
                0.005,
            ),
        ):
            context = control_page_server._voice_capture_consent_context(app)
            await anext(context)
            await lock.acquire()
            try:
                await asyncio.wait_for(heartbeat_published.wait(), timeout=1.0)
            finally:
                lock.release()
                await context.aclose()

        self.assertGreaterEqual(manager.publish_host_lease.call_count, 2)


if __name__ == "__main__":
    unittest.main()
