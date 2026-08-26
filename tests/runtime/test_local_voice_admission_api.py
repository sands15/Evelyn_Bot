from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession
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


def _bridge_stopped_status_payload(now: float) -> dict[str, object]:
    payload = _bridge_status_payload(now, include_capture_fence=False)
    payload.update(
        {
            "micEnabled": False,
            "micControlDesiredEnabled": False,
            "micCaptureStopped": True,
            "mic": {
                "enabled": False,
                "captureReady": False,
                "captureActive": False,
                "captureStopped": True,
            },
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
        self._main_warmup_patch = patch.object(
            fast_api,
            "fast_main_llm_warmup_ready",
            return_value=True,
        )
        self._main_warmup_patch.start()
        self.reporter_token = "r" * 48
        self.internal_token = "i" * 48
        self.lease_token = "l" * 48
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
        self._lease_token_patch = patch.object(
            fast_api,
            "VOICE_INPUT_LEASE_AUTH_TOKEN",
            self.lease_token,
        )
        self._reporter_token_patch.start()
        self._internal_token_patch.start()
        self._lease_token_patch.start()
        self._lease_tmp = tempfile.TemporaryDirectory()
        self._original_lease_manager = fast_api.VOICE_INPUT_LEASE_MANAGER
        self._original_discord_status_path = (
            fast_api.DISCORD_RUNTIME_STATUS_PATH
        )
        fast_api.VOICE_INPUT_LEASE_MANAGER = fast_api.VoiceInputLeaseManager(
            state_path=Path(self._lease_tmp.name) / "owner.json"
        )
        fast_api.DISCORD_RUNTIME_STATUS_PATH = (
            Path(self._lease_tmp.name) / "discord-status.json"
        )
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
        self._lease_token_patch.stop()
        self._validation_context.stop()
        self._unsafe_ingress_patch.stop()
        self._main_warmup_patch.stop()
        fast_api.LOCAL_VOICE_ADMISSION = self._original_manager
        fast_api.VOICE_INPUT_LEASE_MANAGER = self._original_lease_manager
        fast_api.DISCORD_RUNTIME_STATUS_PATH = (
            self._original_discord_status_path
        )
        self._lease_tmp.cleanup()

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
            self.assertTrue(
                fast_api.local_voice_capture_fence_is_current(
                    "a" * 32,
                    now=now,
                )
            )
            self.assertEqual(
                fast_api.local_voice_capture_fence_digest_if_current(
                    "a" * 32,
                    now=now,
                    require_capture_active=True,
                ),
                "",
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

    async def test_discord_lease_blocks_local_mic_before_control_publish(self) -> None:
        status = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=_bridge_stopped_status_payload(time.time()),
        )
        self.assertEqual(status.status, 200, await status.text())
        discord = await self.client.post(
            "/internal/voice-input-lease",
            headers={
                fast_api.VOICE_INPUT_LEASE_AUTH_HEADER: self.lease_token,
            },
            json={
                "action": "acquire",
                "source": "discord_voice",
                "instanceId": "b" * 32,
            },
        )
        self.assertEqual(discord.status, 200, await discord.text())
        request_before = dict(fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST)

        mic = await self.client.post(
            "/api/local-bridge/mic",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: self.internal_token,
            },
            json={
                "enabled": True,
                "source": "control_page",
                "purpose": "voice_capture_consent",
                "enableFence": dict(
                    fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE
                ),
            },
        )
        payload = await mic.json()

        self.assertEqual(mic.status, 409)
        self.assertEqual(payload["error"], "voice_input_lease_conflict")
        self.assertEqual(
            fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST,
            request_before,
        )

    async def test_voice_input_lease_endpoint_requires_dedicated_token(self) -> None:
        response = await self.client.post(
            "/internal/voice-input-lease",
            headers={
                fast_api.VOICE_INPUT_LEASE_AUTH_HEADER: self.internal_token,
            },
            json={
                "action": "acquire",
                "source": "discord_voice",
                "instanceId": "b" * 32,
            },
        )
        payload = await response.json()

        self.assertEqual(response.status, 403)
        self.assertEqual(payload["error"], "voice_input_lease_unauthorized")

    async def test_voice_input_lease_wait_does_not_block_event_loop(self) -> None:
        started = threading.Event()
        release = threading.Event()
        timestamps: dict[str, float] = {}
        ticks: list[float] = []
        original_acquire = fast_api.VOICE_INPUT_LEASE_MANAGER.acquire

        def blocked_acquire(*args, **kwargs):
            timestamps["started"] = time.monotonic()
            started.set()
            release.wait(timeout=1.0)
            timestamps["released"] = time.monotonic()
            return original_acquire(*args, **kwargs)

        async def ticker() -> None:
            while "released" not in timestamps:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        try:
            with patch.object(
                fast_api.VOICE_INPUT_LEASE_MANAGER,
                "acquire",
                side_effect=blocked_acquire,
            ):
                request_task = asyncio.create_task(
                    self.client.post(
                        "/internal/voice-input-lease",
                        headers={
                            fast_api.VOICE_INPUT_LEASE_AUTH_HEADER: (
                                self.lease_token
                            ),
                        },
                        json={
                            "action": "acquire",
                            "source": "discord_voice",
                            "instanceId": "b" * 32,
                        },
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(started.wait, 2.0)
                )
                await asyncio.sleep(0.1)
                release.set()
                response = await asyncio.wait_for(request_task, timeout=2.0)
        finally:
            release.set()
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass

        self.assertEqual(response.status, 200, await response.text())
        self.assertTrue(
            any(
                timestamps["started"] < tick < timestamps["released"]
                for tick in ticks
            )
        )

    async def test_voice_input_lease_io_drains_before_cancellation(self) -> None:
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def blocked_io() -> str:
            started.set()
            release.wait(timeout=2.0)
            completed.set()
            return "done"

        task = asyncio.create_task(
            fast_api._run_voice_input_lease_io(blocked_io)
        )
        self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
        task.cancel()
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        release.set()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(completed.is_set())

    async def test_old_off_heartbeat_cannot_invalidate_local_on_handover(
        self,
    ) -> None:
        initial_status = _bridge_stopped_status_payload(time.time())
        initial = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=initial_status,
        )
        self.assertEqual(initial.status, 200, await initial.text())
        fast_api.VOICE_INPUT_LEASE_MANAGER.acquire(
            "local_mic",
            "a" * 32,
            observations={
                "local_mic": fast_api.VoiceInputObservation(
                    "inactive",
                    "a" * 32,
                ),
                "discord_voice": fast_api.VoiceInputObservation("inactive"),
            },
        )
        action_id = "e" * 32
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
            {
                "revision": 7,
                "actionId": action_id,
                "enabled": False,
                "requestedAt": time.time(),
                "source": "control_page",
                "purpose": "",
                "bridgeInstanceDigest": fast_api.hashlib.sha256(
                    ("a" * 32).encode("utf-8")
                ).hexdigest(),
            }
        )
        old_off_heartbeat = _bridge_stopped_status_payload(time.time())
        old_off_heartbeat.update(
            {
                "statusSeq": 2,
                "startedAt": fast_api.LOCAL_BRIDGE_STATUS["startedAt"],
                "micControlRevision": 7,
                "micControlActionId": action_id,
                "micControlState": "applied",
            }
        )
        local_reacquired = threading.Event()
        allow_local_return = threading.Event()
        old_off_released = threading.Event()
        manager = fast_api.VOICE_INPUT_LEASE_MANAGER
        original_acquire = manager.acquire
        original_release_if_inactive = manager.release_if_inactive

        def delayed_local_acquire(source, instance_id, *, observations):
            receipt = original_acquire(
                source,
                instance_id,
                observations=observations,
            )
            if source == "local_mic":
                local_reacquired.set()
                if not allow_local_return.wait(timeout=5.0):
                    raise TimeoutError("local_acquire_test_gate_timeout")
            return receipt

        def observed_release_if_inactive(*args, **kwargs):
            try:
                return original_release_if_inactive(*args, **kwargs)
            finally:
                old_off_released.set()

        async def applied_enable(request: dict) -> dict:
            return {
                "applied": True,
                "request": dict(request),
                "localBridge": fast_api.local_bridge_status_snapshot(),
            }

        local_task: asyncio.Task | None = None
        heartbeat_task: asyncio.Task | None = None
        discord_payload: dict | None = None
        concurrent_client = ClientSession()
        try:
            with patch.object(
                manager,
                "acquire",
                side_effect=delayed_local_acquire,
            ), patch.object(
                manager,
                "release_if_inactive",
                side_effect=observed_release_if_inactive,
            ), patch.object(
                fast_api,
                "wait_for_local_bridge_mic_control",
                side_effect=applied_enable,
            ):
                local_task = asyncio.create_task(
                    self.client.post(
                        "/api/local-bridge/mic",
                        headers={
                            fast_api.EVELYN_INTERNAL_CONTROL_HEADER: (
                                self.internal_token
                            ),
                        },
                        json={
                            "enabled": True,
                            "source": "control_page",
                            "purpose": "voice_capture_consent",
                            "enableFence": dict(
                                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE
                            ),
                        },
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(local_reacquired.wait, 2.0)
                )
                heartbeat_task = asyncio.create_task(
                    concurrent_client.post(
                        self.client.make_url("/api/local-bridge/status"),
                        headers={
                            fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                                self.reporter_token
                            ),
                        },
                        json=old_off_heartbeat,
                    )
                )
                released_before_local_return = await asyncio.to_thread(
                    old_off_released.wait,
                    1.0,
                )
                if (
                    released_before_local_return
                    and manager.public_status()["state"] == "unowned"
                ):
                    discord = await concurrent_client.post(
                        self.client.make_url("/internal/voice-input-lease"),
                        headers={
                            fast_api.VOICE_INPUT_LEASE_AUTH_HEADER: (
                                self.lease_token
                            ),
                        },
                        json={
                            "action": "acquire",
                            "source": "discord_voice",
                            "instanceId": "b" * 32,
                        },
                    )
                    discord_payload = await discord.json()
                    self.assertEqual(
                        discord.status,
                        200,
                        discord_payload,
                    )
                allow_local_return.set()
                local = await asyncio.wait_for(local_task, timeout=3.0)
                heartbeat = await asyncio.wait_for(
                    heartbeat_task,
                    timeout=3.0,
                )
        finally:
            allow_local_return.set()
            pending = [
                task
                for task in (local_task, heartbeat_task)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await concurrent_client.close()

        local_payload = await local.json()
        self.assertEqual(heartbeat.status, 200, await heartbeat.text())
        lease_status = manager.public_status()
        self.assertFalse(
            released_before_local_return,
            {
                "local": local_payload,
                "discord": discord_payload,
                "lease": lease_status,
            },
        )
        self.assertFalse(
            fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.get("enabled") is True
            and lease_status
            != {"state": "owned", "source": "local_mic"},
            {
                "local": local_payload,
                "discord": discord_payload,
                "lease": lease_status,
            },
        )

    async def test_chat_mic_off_cannot_be_overwritten_by_stale_local_on(
        self,
    ) -> None:
        initial = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=_bridge_stopped_status_payload(time.time()),
        )
        self.assertEqual(initial.status, 200, await initial.text())
        original_matches = fast_api._mic_enable_fence_matches
        worker_checks = 0
        event_loop_thread = threading.current_thread()
        final_on_check_started = threading.Event()
        allow_final_on_check = threading.Event()

        def delayed_matches(value: object) -> bool:
            nonlocal worker_checks
            matched = original_matches(value)
            if threading.current_thread() is not event_loop_thread:
                worker_checks += 1
                if worker_checks == 2:
                    final_on_check_started.set()
                    if not allow_final_on_check.wait(timeout=5.0):
                        raise TimeoutError("local_on_check_test_gate_timeout")
            return matched

        async def applied(request: dict) -> dict:
            return {
                "applied": True,
                "request": dict(request),
                "localBridge": fast_api.local_bridge_status_snapshot(),
            }

        on_task: asyncio.Task | None = None
        off_task: asyncio.Task | None = None
        try:
            with patch.object(
                fast_api,
                "_mic_enable_fence_matches",
                side_effect=delayed_matches,
            ), patch.object(
                fast_api,
                "wait_for_local_bridge_mic_control",
                side_effect=applied,
            ):
                on_task = asyncio.create_task(
                    self.client.post(
                        "/api/local-bridge/mic",
                        headers={
                            fast_api.EVELYN_INTERNAL_CONTROL_HEADER: (
                                self.internal_token
                            ),
                        },
                        json={
                            "enabled": True,
                            "source": "control_page",
                            "purpose": "voice_capture_consent",
                            "enableFence": dict(
                                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE
                            ),
                        },
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(
                        final_on_check_started.wait,
                        2.0,
                    )
                )
                off_task = asyncio.create_task(
                    fast_api.execute_local_bridge_mic_control(
                        False,
                        source="chat",
                    )
                )
                await asyncio.sleep(0.05)
                self.assertFalse(off_task.done())
                allow_final_on_check.set()
                on_response, off_reply = await asyncio.gather(
                    on_task,
                    off_task,
                )
        finally:
            allow_final_on_check.set()
            pending = [
                task
                for task in (on_task, off_task)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        self.assertEqual(on_response.status, 200, await on_response.text())
        self.assertEqual(off_reply, "마이크 입력을 껐어.")
        self.assertIs(
            fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["enabled"],
            False,
        )

    async def test_overlapping_heartbeats_do_not_mix_status_epochs(self) -> None:
        initial = _bridge_stopped_status_payload(time.time())
        accepted = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=initial,
        )
        self.assertEqual(accepted.status, 200, await accepted.text())
        manager = fast_api.VOICE_INPUT_LEASE_MANAGER
        manager.acquire(
            "local_mic",
            "a" * 32,
            observations={
                "local_mic": fast_api.VoiceInputObservation(
                    "inactive",
                    "a" * 32,
                ),
                "discord_voice": fast_api.VoiceInputObservation("inactive"),
            },
        )
        action_id = "f" * 32
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
            {
                "revision": 9,
                "actionId": action_id,
                "enabled": False,
                "requestedAt": time.time(),
                "source": "control_page",
                "purpose": "",
                "bridgeInstanceDigest": fast_api.hashlib.sha256(
                    ("a" * 32).encode("utf-8")
                ).hexdigest(),
            }
        )
        started_at = fast_api.LOCAL_BRIDGE_STATUS["startedAt"]

        def heartbeat(status_seq: int) -> dict[str, object]:
            payload = _bridge_stopped_status_payload(time.time())
            payload.update(
                {
                    "statusSeq": status_seq,
                    "startedAt": started_at,
                    "micControlRevision": 9,
                    "micControlActionId": action_id,
                    "micControlState": "applied",
                }
            )
            return payload

        release_started = threading.Event()
        allow_release = threading.Event()
        original_release = manager.release_if_inactive

        def delayed_release(*args, **kwargs):
            release_started.set()
            if not allow_release.wait(timeout=3.0):
                raise TimeoutError("heartbeat_release_test_gate_timeout")
            return original_release(*args, **kwargs)

        concurrent_client = ClientSession()
        first_task: asyncio.Task | None = None
        second_task: asyncio.Task | None = None
        try:
            with patch.object(
                manager,
                "release_if_inactive",
                side_effect=delayed_release,
            ):
                first_task = asyncio.create_task(
                    self.client.post(
                        "/api/local-bridge/status",
                        headers={
                            fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                                self.reporter_token
                            ),
                        },
                        json=heartbeat(2),
                    )
                )
                self.assertTrue(
                    await asyncio.to_thread(release_started.wait, 2.0)
                )
                second_task = asyncio.create_task(
                    concurrent_client.post(
                        self.client.make_url("/api/local-bridge/status"),
                        headers={
                            fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                                self.reporter_token
                            ),
                        },
                        json=heartbeat(3),
                    )
                )
                await asyncio.sleep(0.1)
                self.assertFalse(second_task.done())
                self.assertEqual(
                    fast_api.LOCAL_BRIDGE_STATUS["statusSeq"],
                    2,
                )
                allow_release.set()
                first = await asyncio.wait_for(first_task, timeout=3.0)
                second = await asyncio.wait_for(second_task, timeout=3.0)
        finally:
            allow_release.set()
            pending = [
                task
                for task in (first_task, second_task)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await concurrent_client.close()

        first_payload = await first.json()
        second_payload = await second.json()
        self.assertEqual(first.status, 200, first_payload)
        self.assertEqual(second.status, 200, second_payload)
        self.assertEqual(first_payload["localBridge"]["statusSeq"], 2)
        self.assertEqual(second_payload["localBridge"]["statusSeq"], 3)
        self.assertEqual(fast_api.LOCAL_BRIDGE_STATUS["statusSeq"], 3)

    async def test_internal_verified_stop_callback_retires_exact_owner(self) -> None:
        observations = {
            "local_mic": fast_api.VoiceInputObservation(
                "inactive",
                "a" * 32,
            ),
            "discord_voice": fast_api.VoiceInputObservation(
                "inactive",
                "b" * 32,
            ),
        }
        fast_api.VOICE_INPUT_LEASE_MANAGER.acquire(
            "discord_voice",
            "b" * 32,
            observations=observations,
        )

        prepared = await self.client.post(
            "/internal/voice-input-lease/retirement/prepare",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: (
                    self.internal_token
                ),
            },
            json={"source": "discord_voice"},
        )
        prepared_payload = await prepared.json()
        self.assertEqual(prepared.status, 200, prepared_payload)
        self.assertTrue(prepared_payload["required"])
        self.assertNotIn("instanceId", prepared_payload)
        self.assertNotIn("leaseId", prepared_payload)

        completed = await self.client.post(
            "/internal/voice-input-lease/retirement/complete",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: (
                    self.internal_token
                ),
            },
            json={
                "claimId": prepared_payload["claimId"],
                "hostInstanceId": "host-" + "c" * 32,
                "requestId": "request-verified-stop",
            },
        )
        completed_payload = await completed.json()

        self.assertEqual(completed.status, 200, completed_payload)
        self.assertTrue(completed_payload["retired"])
        self.assertEqual(
            fast_api.VOICE_INPUT_LEASE_MANAGER.public_status()["state"],
            "unowned",
        )

    async def test_retirement_callback_preserves_successor_generation(self) -> None:
        old_observations = {
            "local_mic": fast_api.VoiceInputObservation(
                "inactive",
                "a" * 32,
            ),
            "discord_voice": fast_api.VoiceInputObservation(
                "inactive",
                "b" * 32,
            ),
        }
        old = fast_api.VOICE_INPUT_LEASE_MANAGER.acquire(
            "discord_voice",
            "b" * 32,
            observations=old_observations,
        )
        prepared = await self.client.post(
            "/internal/voice-input-lease/retirement/prepare",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: (
                    self.internal_token
                ),
            },
            json={"source": "discord_voice"},
        )
        claim = await prepared.json()
        fast_api.VOICE_INPUT_LEASE_MANAGER.release(
            "discord_voice",
            "b" * 32,
            old["leaseId"],
        )
        successor_observations = {
            **old_observations,
            "discord_voice": fast_api.VoiceInputObservation(
                "inactive",
                "c" * 32,
            ),
        }
        successor = fast_api.VOICE_INPUT_LEASE_MANAGER.acquire(
            "discord_voice",
            "c" * 32,
            observations=successor_observations,
        )

        completed = await self.client.post(
            "/internal/voice-input-lease/retirement/complete",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: (
                    self.internal_token
                ),
            },
            json={
                "claimId": claim["claimId"],
                "hostInstanceId": "host-" + "d" * 32,
                "requestId": "request-replacement-race",
            },
        )
        payload = await completed.json()

        self.assertEqual(completed.status, 409, payload)
        self.assertEqual(
            payload["error"],
            "voice_input_lease_retirement_stale",
        )
        current = fast_api.VOICE_INPUT_LEASE_MANAGER.acquire(
            "discord_voice",
            "c" * 32,
            observations=successor_observations,
        )
        self.assertEqual(current["leaseId"], successor["leaseId"])

    async def test_retirement_callback_rejects_voice_lease_token(self) -> None:
        response = await self.client.post(
            "/internal/voice-input-lease/retirement/prepare",
            headers={
                fast_api.EVELYN_INTERNAL_CONTROL_HEADER: self.lease_token,
            },
            json={"source": "discord_voice"},
        )
        payload = await response.json()

        self.assertEqual(response.status, 403)
        self.assertEqual(
            payload["error"],
            "voice_input_lease_retirement_unauthorized",
        )

    async def test_failed_local_enable_terminalizes_request_before_handover(self) -> None:
        status = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=_bridge_stopped_status_payload(time.time()),
        )
        self.assertEqual(status.status, 200, await status.text())

        async def failed_enable(request: dict) -> dict:
            now = time.time()
            fast_api.LOCAL_BRIDGE_STATUS.update(
                {
                    "heartbeatAt": now,
                    "updatedAt": now,
                    "micControlRevision": request["revision"],
                    "micControlActionId": request["actionId"],
                    "micControlState": "failed",
                    "micControlDesiredEnabled": True,
                    "micControlError": "mic_control_failed",
                    "micEnabled": False,
                    "micCaptureStopped": True,
                    "mic": {
                        "enabled": False,
                        "captureReady": False,
                        "captureActive": False,
                        "captureStopped": True,
                    },
                }
            )
            return {
                "applied": False,
                "request": dict(request),
                "localBridge": fast_api.local_bridge_status_snapshot(
                    now=now
                ),
                "error": "mic_control_failed",
            }

        discord_observations = iter(
            (
                fast_api.VoiceInputObservation(
                    "inactive",
                    "b" * 32,
                ),
                fast_api.VoiceInputObservation("unknown"),
            )
        )
        with patch.object(
            fast_api,
            "wait_for_local_bridge_mic_control",
            side_effect=failed_enable,
        ), patch.object(
            fast_api,
            "_discord_voice_input_observation",
            side_effect=lambda **_kwargs: next(discord_observations),
        ):
            mic = await self.client.post(
                "/api/local-bridge/mic",
                headers={
                    fast_api.EVELYN_INTERNAL_CONTROL_HEADER: self.internal_token,
                },
                json={
                    "enabled": True,
                    "source": "control_page",
                    "purpose": "voice_capture_consent",
                    "enableFence": dict(
                        fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE
                    ),
                },
            )
        payload = await mic.json()

        self.assertEqual(mic.status, 202, payload)
        self.assertEqual(payload["error"], "mic_control_failed")
        current = fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
        self.assertIs(current["enabled"], False)
        self.assertEqual(current["purpose"], "")
        self.assertEqual(current["revision"], payload["request"]["revision"])
        self.assertEqual(current["actionId"], payload["request"]["actionId"])
        self.assertEqual(
            fast_api._local_mic_input_observation().state,
            "inactive",
        )
        self.assertEqual(
            fast_api.VOICE_INPUT_LEASE_MANAGER.public_status()["source"],
            "local_mic",
        )

        retry_status = _bridge_stopped_status_payload(time.time())
        retry_status.update(
            {
                "statusSeq": 2,
                "startedAt": fast_api.LOCAL_BRIDGE_STATUS["startedAt"],
                "micControlRevision": current["revision"],
                "micControlActionId": current["actionId"],
                "micControlState": "failed",
                "micControlDesiredEnabled": True,
                "micControlError": "mic_control_failed",
            }
        )
        heartbeat = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=retry_status,
        )
        self.assertEqual(heartbeat.status, 200, await heartbeat.text())
        self.assertEqual(
            fast_api.VOICE_INPUT_LEASE_MANAGER.public_status()["state"],
            "unowned",
        )

        discord = await self.client.post(
            "/internal/voice-input-lease",
            headers={
                fast_api.VOICE_INPUT_LEASE_AUTH_HEADER: self.lease_token,
            },
            json={
                "action": "acquire",
                "source": "discord_voice",
                "instanceId": "b" * 32,
            },
        )
        discord_payload = await discord.json()
        self.assertEqual(discord.status, 200, discord_payload)
        self.assertEqual(discord_payload["source"], "discord_voice")

    async def test_local_off_ack_releases_owner_only_after_physical_stop(self) -> None:
        fast_api.VOICE_INPUT_LEASE_MANAGER.acquire(
            "local_mic",
            "a" * 32,
            observations={
                "local_mic": fast_api.VoiceInputObservation(
                    "inactive",
                    "a" * 32,
                ),
                "discord_voice": fast_api.VoiceInputObservation("inactive"),
            },
        )
        action_id = "e" * 32
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
            {
                "revision": 7,
                "actionId": action_id,
                "enabled": False,
                "requestedAt": time.time(),
                "source": "control_page",
                "purpose": "",
                "bridgeInstanceDigest": fast_api.hashlib.sha256(
                    ("a" * 32).encode("utf-8")
                ).hexdigest(),
            }
        )
        bridge = _bridge_stopped_status_payload(time.time())
        bridge.update(
            {
                "micControlRevision": 7,
                "micControlActionId": action_id,
                "micControlState": "applied",
            }
        )

        response = await self.client.post(
            "/api/local-bridge/status",
            headers={
                fast_api.LOCAL_BRIDGE_STATUS_AUTH_HEADER: self.reporter_token,
            },
            json=bridge,
        )

        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(
            fast_api.VOICE_INPUT_LEASE_MANAGER.public_status()["state"],
            "unowned",
        )

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
