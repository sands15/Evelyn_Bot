from __future__ import annotations

import asyncio
import hashlib
import os
import runpy
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_mic import (  # noqa: E402
    LocalMicCaptureService,
    mono16k_float_to_discord_pcm,
    normalize_sounddevice_identifier,
    parse_user_id_set,
    resolve_local_mic_target,
    serialize_local_mic_target,
    should_route_discord_user_to_local_mic,
)
from evelyn_core.local_io_bridge import LocalIoBridge, iter_pcm_aligned_chunks  # noqa: E402
from evelyn_core import local_io_bridge  # noqa: E402
from runtime_lifecycle import resolve_restart_launcher  # noqa: E402


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
            "issuedMonotonic": time.monotonic(),
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


def mic_control_response(
    bridge: LocalIoBridge,
    *,
    revision: int,
    enabled: bool,
    action_id: str | None = None,
) -> dict[str, dict[str, object]]:
    return {
        "micControlRequest": {
            "revision": revision,
            "enabled": enabled,
            "actionId": action_id or f"{revision:032x}",
            "bridgeInstanceDigest": hashlib.sha256(
                bridge.bridge_instance_id.encode("utf-8")
            ).hexdigest(),
        }
    }


def fresh_host_lease(*, owner: str = "a" * 64, lease: str = "b" * 64) -> dict:
    return {
        "authorized": True,
        "reason": "",
        "ownerDigest": owner,
        "leaseDigest": lease,
        "heartbeatAt": time.time(),
        "expiresAt": time.time() + 60,
        "checkedAt": time.time(),
        "fenceDigest": "c" * 64,
    }


class LocalMicRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        lease_patch = patch.object(
            LocalIoBridge,
            "_inspect_voice_capture_host_lease",
            autospec=True,
            side_effect=lambda _bridge: fresh_host_lease(),
        )
        lease_patch.start()
        self.addCleanup(lease_patch.stop)

    def test_bridge_never_boots_capture_from_ambient_configuration(self):
        bridge = LocalIoBridge()

        self.assertFalse(bridge.mic_enabled)
        self.assertTrue(bridge.mic_capture_stopped)

    def test_bridge_children_do_not_inherit_parent_credentials(self):
        credentials = {
            "LOCAL_BRIDGE_STATUS_AUTH_TOKEN": "reporter-secret",
            "EVELYN_INTERNAL_CONTROL_TOKEN": "internal-secret",
            "EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN": "capture-secret",
            "DISCORD_BOT_TOKEN": "discord-secret",
            "OPENAI_API_KEY": "model-secret",
        }
        with patch.dict(os.environ, credentials, clear=False):
            child_env = LocalIoBridge._credential_scoped_child_environment()
            minecraft_env = LocalIoBridge()._minecraft_launcher_environment()

        for name in credentials:
            self.assertNotIn(name, child_env)
        self.assertEqual(
            minecraft_env["DISCORD_BOT_TOKEN"],
            "local-only-disabled",
        )
        self.assertNotIn("LOCAL_BRIDGE_STATUS_AUTH_TOKEN", minecraft_env)
        self.assertNotIn("EVELYN_INTERNAL_CONTROL_TOKEN", minecraft_env)
        self.assertNotIn("EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN", minecraft_env)
        self.assertNotIn("OPENAI_API_KEY", minecraft_env)

    def test_parse_user_id_set_ignores_invalid_tokens(self) -> None:
        parsed = parse_user_id_set("441943340624248843, nope; 405351496012791808")
        self.assertEqual(parsed, {441943340624248843, 405351496012791808})

    def test_normalize_sounddevice_identifier_accepts_numeric_string_index(self) -> None:
        self.assertEqual(normalize_sounddevice_identifier("18"), 18)
        self.assertEqual(normalize_sounddevice_identifier(" fifine Microphone "), "fifine Microphone")
        self.assertIsNone(normalize_sounddevice_identifier(" "))

    def test_should_route_only_when_capture_ready(self) -> None:
        self.assertTrue(
            should_route_discord_user_to_local_mic(
                441943340624248843,
                preferred_user_ids={441943340624248843},
                capture_ready=True,
            )
        )
        self.assertFalse(
            should_route_discord_user_to_local_mic(
                441943340624248843,
                preferred_user_ids={441943340624248843},
                capture_ready=False,
            )
        )
        self.assertFalse(
            should_route_discord_user_to_local_mic(
                405351496012791808,
                preferred_user_ids={441943340624248843},
                capture_ready=True,
            )
        )

    def test_resolve_local_mic_target_uses_member_in_active_voice_channel(self) -> None:
        target_member = SimpleNamespace(id=441943340624248843, bot=False)
        other_member = SimpleNamespace(id=405351496012791808, bot=False)
        channel = SimpleNamespace(id=99, members=[other_member, target_member])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})

        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.guild_id, 7)
        self.assertEqual(target.voice_channel_id, 99)
        self.assertIs(target.member, target_member)

    def test_resolve_local_mic_target_returns_none_without_match(self) -> None:
        channel = SimpleNamespace(id=99, members=[SimpleNamespace(id=1, bot=False)])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})

        self.assertIsNone(target)

    def test_serialize_local_mic_target_returns_json_safe_snapshot(self) -> None:
        member = SimpleNamespace(id=441943340624248843, name="JH", display_name="정훈", bot=False)
        channel = SimpleNamespace(id=99, members=[member])
        guild = SimpleNamespace(id=7, voice_client=SimpleNamespace(channel=channel))

        target = resolve_local_mic_target(guilds=[guild], preferred_user_ids={441943340624248843})
        snapshot = serialize_local_mic_target(target)

        self.assertEqual(
            snapshot,
            {
                "guildId": 7,
                "voiceChannelId": 99,
                "memberId": 441943340624248843,
                "memberName": "정훈",
            },
        )

    def test_mono16k_float_to_discord_pcm_matches_discord_shape(self) -> None:
        audio = np.full(16000, 0.1, dtype=np.float32)

        pcm_bytes = mono16k_float_to_discord_pcm(audio, sampling_rate=16000)

        self.assertEqual(len(pcm_bytes), 48000 * 2 * 2)

    def test_local_io_bridge_aligns_streamed_pcm_chunks(self) -> None:
        chunks = list(iter_pcm_aligned_chunks([b"abc", b"defg", b"h"]))

        self.assertEqual(chunks, [b"ab", b"cdef", b"gh"])
        self.assertTrue(all(len(chunk) % 2 == 0 for chunk in chunks))

    def test_local_io_bridge_shutdown_starts_script_and_exits_once(self) -> None:
        bridge = LocalIoBridge()
        bridge.admission_active = True

        with (
            patch.object(bridge, "_start_shutdown_script") as start_shutdown,
            patch.object(bridge, "_schedule_bridge_exit") as schedule_exit,
        ):
            bridge._handle_control_response({"shutdown": {"requested": True}})
            bridge._handle_control_response({"shutdown": {"requested": True}})

        self.assertTrue(bridge.shutdown_started)
        self.assertFalse(bridge.admission_active)
        self.assertEqual(bridge.admission_epoch, 1)
        start_shutdown.assert_called_once_with()
        schedule_exit.assert_called_once_with()

    def test_local_io_bridge_restart_starts_script_and_exits_once(self) -> None:
        bridge = LocalIoBridge()
        bridge.admission_active = True

        with (
            patch.object(bridge, "_start_restart_script") as start_restart,
            patch.object(bridge, "_schedule_bridge_exit") as schedule_exit,
        ):
            bridge._handle_control_response({"restart": {"requested": True}})
            bridge._handle_control_response({"restart": {"requested": True}})

        self.assertTrue(bridge.restart_started)
        self.assertFalse(bridge.admission_active)
        self.assertEqual(bridge.admission_epoch, 1)
        start_restart.assert_called_once_with()
        schedule_exit.assert_called_once_with()

    def test_local_io_bridge_restart_uses_supervisor_exit_code(self) -> None:
        async def scenario(restart: bool):
            bridge = LocalIoBridge()
            bridge.restart_started = restart
            with (
                patch.object(asyncio, "sleep", new=AsyncMock()),
                patch.object(local_io_bridge.os, "_exit") as process_exit,
            ):
                await bridge._exit_after_shutdown_delay()
            return process_exit

        restart_exit = asyncio.run(scenario(True))
        shutdown_exit = asyncio.run(scenario(False))

        restart_exit.assert_called_once_with(
            local_io_bridge.LOCAL_BRIDGE_RESTART_EXIT_CODE
        )
        shutdown_exit.assert_called_once_with(0)

    def test_local_io_bridge_lifecycle_response_preempts_mic_enable(self) -> None:
        async def scenario(lifecycle: str):
            bridge = LocalIoBridge()
            bridge.mic_enabled = False
            bridge.mic_capture_stopped = True
            bridge._start_mic = AsyncMock(  # type: ignore[method-assign]
                side_effect=AssertionError("lifecycle response must preempt mic start")
            )
            start_method_name = (
                "_start_restart_script"
                if lifecycle == "restart"
                else "_start_shutdown_script"
            )
            with (
                patch.object(bridge, start_method_name) as start_lifecycle,
                patch.object(bridge, "_schedule_bridge_exit") as schedule_exit,
            ):
                response = mic_control_response(
                    bridge,
                    revision=18,
                    enabled=True,
                )
                response[lifecycle] = {"requested": True}
                bridge._handle_control_response(response)
                await asyncio.sleep(0)
            return bridge, start_lifecycle, schedule_exit

        for lifecycle in ("restart", "shutdown"):
            with self.subTest(lifecycle=lifecycle):
                bridge, start_lifecycle, schedule_exit = asyncio.run(
                    scenario(lifecycle)
                )

                self.assertIs(getattr(bridge, f"{lifecycle}_started"), True)
                bridge._start_mic.assert_not_awaited()  # type: ignore[union-attr]
                self.assertEqual(bridge.mic_control_tasks, set())
                self.assertEqual(bridge.mic_control_pending_revision, 0)
                self.assertEqual(bridge.mic_control_request_revision, 0)
                self.assertNotEqual(bridge.mic_control_state, "applied")
                self.assertFalse(bridge.mic_enabled)
                self.assertTrue(bridge.mic_capture_stopped)
                start_lifecycle.assert_called_once_with()
                schedule_exit.assert_called_once_with()

    def test_local_io_bridge_enable_lifecycle_race_stops_capture_and_fails(self) -> None:
        class StartedService:
            capture_ready = True
            capture_stopped = False

            def __init__(self) -> None:
                self.stop_calls = 0

            def stop(self) -> bool:
                self.stop_calls += 1
                self.capture_ready = False
                self.capture_stopped = True
                return True

        async def scenario(lifecycle: str):
            bridge = LocalIoBridge()
            bridge.mic_enabled = False
            bridge.mic_capture_stopped = True
            service = StartedService()
            start_entered = asyncio.Event()
            release_start = asyncio.Event()

            async def start_mic() -> None:
                bridge.service = service  # type: ignore[assignment]
                bridge.mic_capture_stopped = False
                bridge.ready = True
                start_entered.set()
                await release_start.wait()

            bridge._start_mic = AsyncMock(  # type: ignore[method-assign]
                side_effect=start_mic
            )
            apply_task = asyncio.create_task(
                bridge._apply_mic_control_request(
                    revision=19,
                    enabled=True,
                    action_id=f"{19:032x}",
                )
            )
            await start_entered.wait()
            setattr(bridge, f"{lifecycle}_started", True)
            release_start.set()
            await apply_task
            return bridge, service

        for lifecycle in ("restart", "shutdown"):
            with self.subTest(lifecycle=lifecycle):
                bridge, service = asyncio.run(scenario(lifecycle))

                bridge._start_mic.assert_awaited_once()  # type: ignore[union-attr]
                self.assertEqual(service.stop_calls, 1)
                self.assertIsNone(bridge.service)
                self.assertFalse(bridge.mic_enabled)
                self.assertTrue(bridge.mic_capture_stopped)
                self.assertFalse(bridge.ready)
                self.assertEqual(bridge.mic_control_request_revision, 19)
                self.assertEqual(bridge.mic_control_action_id, f"{19:032x}")
                self.assertEqual(bridge.mic_control_state, "failed")
                self.assertEqual(bridge.mic_control_error, "mic_control_failed")
                self.assertIn("local_bridge_lifecycle_stopping", bridge.last_error)

    def test_local_io_bridge_enqueues_control_page_speak_requests(self) -> None:
        bridge = LocalIoBridge()

        bridge._handle_control_response(
            {
                "speakRequests": [
                    {"id": "speak-1", "text": " hello from page ", "source": "control_page"},
                    {"id": "empty", "text": " "},
                ]
            }
        )

        self.assertEqual(bridge.speak_request_queue.qsize(), 1)
        self.assertEqual(bridge.speak_request_queue.get_nowait()["text"], "hello from page")

    def test_local_io_bridge_help_reply_is_not_sent_to_tts(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = True
            bridge.mic_capture_stopped = False
            bridge._post_status = AsyncMock()  # type: ignore[method-assign]
            bridge._transcribe = AsyncMock(return_value="/help")  # type: ignore[method-assign]
            bridge._chat = AsyncMock(  # type: ignore[method-assign]
                return_value=local_io_bridge.LocalChatReply(
                    text="사용 가능한 명령 목록",
                    memory_handoff=local_io_bridge.LocalMemoryHandoff(
                        state="not_used",
                        position=None,
                    ),
                )
            )
            bridge._chat_stream_and_speak = AsyncMock(  # type: ignore[method-assign]
                side_effect=AssertionError("help must not enter the TTS stream")
            )
            bridge._speak = AsyncMock(  # type: ignore[method-assign]
                side_effect=AssertionError("help must not enter one-shot TTS")
            )
            install_admission_grant(bridge)
            await bridge._handle_segment(b"pcm", {"source": "test"})
            return bridge

        bridge = asyncio.run(scenario())

        bridge._chat.assert_awaited_once_with("/help", grant=ANY)  # type: ignore[union-attr]
        bridge._chat_stream_and_speak.assert_not_awaited()  # type: ignore[union-attr]
        bridge._speak.assert_not_awaited()  # type: ignore[union-attr]
        self.assertEqual(bridge.last_latency["ttsMs"], 0.0)

    def test_local_io_bridge_tts_warmup_retries_until_ready(self) -> None:
        bridge = LocalIoBridge()
        bridge.session = object()
        bridge._drain_tts_payload = AsyncMock(side_effect=[RuntimeError("server disconnected"), 1234])  # type: ignore[method-assign]
        bridge._post_status = AsyncMock()  # type: ignore[method-assign]

        with (
            patch("evelyn_core.local_io_bridge.LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC", 0),
            patch("evelyn_core.local_io_bridge.LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS", 2),
            patch("evelyn_core.local_io_bridge.LOCAL_BRIDGE_TTS_WARMUP_RETRY_DELAY_SEC", 0),
            patch("evelyn_core.local_io_bridge.asyncio.sleep", new=AsyncMock()),
        ):
            asyncio.run(bridge._warmup_tts_after_delay())

        self.assertTrue(bridge.tts_warmup_done)
        self.assertEqual(bridge.tts_warmup_error, "")
        self.assertEqual(bridge._drain_tts_payload.await_count, 2)
        bridge._post_status.assert_awaited_once()

    def test_local_io_bridge_applies_mic_enable_request_once(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = False

            async def start_mic() -> None:
                bridge.service = SimpleNamespace(
                    capture_ready=True,
                    capture_stopped=False,
                )
                bridge.mic_capture_stopped = False
                bridge.ready = True
                bridge.last_error = ""

            bridge._start_mic = AsyncMock(side_effect=start_mic)  # type: ignore[method-assign]
            response = mic_control_response(
                bridge,
                revision=17,
                enabled=True,
            )
            bridge._handle_control_response(response)
            bridge._handle_control_response(response)
            await asyncio.gather(*list(bridge.mic_control_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertTrue(bridge.mic_enabled)
        self.assertTrue(bridge.ready)
        self.assertEqual(bridge.mic_control_request_revision, 17)
        self.assertEqual(bridge.mic_control_action_id, f"{17:032x}")
        self.assertEqual(bridge.mic_control_pending_revision, 0)
        self.assertEqual(bridge.mic_control_pending_action_id, "")
        self.assertEqual(bridge.mic_control_state, "applied")
        self.assertFalse(bridge.mic_capture_stopped)
        bridge._start_mic.assert_awaited_once()  # type: ignore[union-attr]

    def test_local_io_bridge_rejects_missing_or_malformed_mic_action_id(self) -> None:
        async def scenario(*, enabled: bool, action_id_state: str) -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = not enabled
            bridge.mic_capture_stopped = enabled
            bridge._start_mic = AsyncMock()  # type: ignore[method-assign]
            bridge._stop_mic = AsyncMock()  # type: ignore[method-assign]
            response = mic_control_response(
                bridge,
                revision=20,
                enabled=enabled,
            )
            request = response["micControlRequest"]
            if action_id_state == "missing":
                request.pop("actionId")
            else:
                request["actionId"] = "not-a-valid-action-id"
            bridge._handle_control_response(response)
            await asyncio.sleep(0)
            return bridge

        for enabled in (True, False):
            for action_id_state in ("missing", "malformed"):
                with self.subTest(
                    enabled=enabled,
                    action_id_state=action_id_state,
                ):
                    bridge = asyncio.run(
                        scenario(
                            enabled=enabled,
                            action_id_state=action_id_state,
                        )
                    )

                    bridge._start_mic.assert_not_awaited()  # type: ignore[union-attr]
                    bridge._stop_mic.assert_not_awaited()  # type: ignore[union-attr]
                    self.assertEqual(bridge.mic_control_tasks, set())
                    self.assertEqual(bridge.mic_control_request_revision, 0)
                    self.assertEqual(bridge.mic_control_pending_revision, 0)
                    self.assertEqual(bridge.mic_control_action_id, "")
                    self.assertEqual(bridge.mic_control_pending_action_id, "")
                    self.assertEqual(bridge.mic_control_state, "idle")
                    self.assertIs(bridge.mic_enabled, not enabled)
                    self.assertIs(bridge.mic_capture_stopped, enabled)

    def test_local_io_bridge_applies_mic_disable_and_discards_queued_audio(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = True
            bridge.admission_active = True
            bridge.queue.put_nowait((b"pcm", {"source": "test"}))
            bridge.priority_queue.put_nowait((b"priority", {"source": "test"}))
            bridge.barge_in_queue.put_nowait((b"barge", {"source": "test"}))
            bridge._handle_control_response(
                mic_control_response(
                    bridge,
                    revision=23,
                    enabled=False,
                )
            )
            await asyncio.gather(*list(bridge.mic_control_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertFalse(bridge.mic_enabled)
        self.assertFalse(bridge.admission_active)
        self.assertEqual(bridge.admission_epoch, 1)
        self.assertTrue(bridge.ready)
        self.assertEqual(bridge.queue.qsize(), 0)
        self.assertEqual(bridge.priority_queue.qsize(), 0)
        self.assertEqual(bridge.barge_in_queue.qsize(), 0)
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 3)
        self.assertEqual(bridge.mic_control_request_revision, 23)
        self.assertEqual(bridge.mic_control_action_id, f"{23:032x}")
        self.assertEqual(bridge.mic_control_pending_revision, 0)
        self.assertEqual(bridge.mic_control_pending_action_id, "")
        self.assertEqual(bridge.mic_control_state, "applied")
        self.assertTrue(bridge.mic_capture_stopped)

    def test_local_io_bridge_failed_stop_does_not_publish_false_success(self) -> None:
        class FailingStopService:
            capture_stopped = False

            def stop(self) -> bool:
                raise RuntimeError("local_mic_stop_timeout")

        async def scenario() -> tuple[LocalIoBridge, FailingStopService]:
            bridge = LocalIoBridge()
            service = FailingStopService()
            bridge.service = service  # type: ignore[assignment]
            bridge.mic_enabled = True
            bridge.mic_capture_stopped = False
            bridge.ready = True
            bridge._handle_control_response(
                mic_control_response(
                    bridge,
                    revision=24,
                    enabled=False,
                )
            )
            await asyncio.gather(*list(bridge.mic_control_tasks))
            return bridge, service

        bridge, service = asyncio.run(scenario())

        self.assertEqual(bridge.mic_control_request_revision, 24)
        self.assertEqual(bridge.mic_control_action_id, f"{24:032x}")
        self.assertEqual(bridge.mic_control_state, "failed")
        self.assertEqual(bridge.mic_control_error, "mic_control_failed")
        self.assertTrue(bridge.mic_enabled)
        self.assertIs(bridge.service, service)
        self.assertFalse(bridge.mic_capture_stopped)
        self.assertFalse(bridge.ready)
        self.assertIn("local_mic_stop_timeout", bridge.last_error)

    def test_local_io_bridge_enable_retry_retires_not_ready_service_before_replacement(
        self,
    ) -> None:
        class ExistingService:
            capture_ready = False
            capture_stopped = False

            def __init__(self, first_stop_outcome: str) -> None:
                self.first_stop_outcome = first_stop_outcome
                self.stop_calls = 0

            def stop(self) -> bool:
                self.stop_calls += 1
                if self.stop_calls == 1:
                    if self.first_stop_outcome == "timeout":
                        raise RuntimeError("local_mic_stop_timeout")
                    return False
                self.capture_stopped = True
                return True

        class ReplacementService:
            capture_ready = False
            capture_stopped = True
            last_error = None

            def __init__(self) -> None:
                self.start_calls = 0

            def start(self) -> bool:
                self.start_calls += 1
                self.capture_ready = True
                self.capture_stopped = False
                return True

        async def scenario(first_stop_outcome: str):
            bridge = LocalIoBridge()
            existing = ExistingService(first_stop_outcome)
            replacement = ReplacementService()
            bridge.service = existing  # type: ignore[assignment]
            bridge.mic_enabled = True
            bridge.mic_capture_stopped = False
            bridge.ready = False

            with patch(
                "evelyn_core.local_io_bridge.LocalMicCaptureService",
                return_value=replacement,
            ) as capture_factory:
                await bridge._apply_mic_control_request(
                    revision=25,
                    enabled=True,
                    action_id=f"{25:032x}",
                )
                first_result = {
                    "state": bridge.mic_control_state,
                    "error": bridge.mic_control_error,
                    "service": bridge.service,
                    "factoryCalls": capture_factory.call_count,
                }
                await bridge._apply_mic_control_request(
                    revision=26,
                    enabled=True,
                    action_id=f"{26:032x}",
                )

            return bridge, existing, replacement, capture_factory, first_result

        for first_stop_outcome in ("timeout", "false"):
            with self.subTest(first_stop_outcome=first_stop_outcome):
                bridge, existing, replacement, capture_factory, first_result = (
                    asyncio.run(scenario(first_stop_outcome))
                )

                self.assertEqual(first_result["state"], "failed")
                self.assertEqual(first_result["error"], "mic_control_failed")
                self.assertIs(first_result["service"], existing)
                self.assertEqual(first_result["factoryCalls"], 0)
                self.assertEqual(existing.stop_calls, 2)
                capture_factory.assert_called_once()
                self.assertIs(bridge.service, replacement)
                self.assertEqual(replacement.start_calls, 1)
                self.assertEqual(bridge.mic_control_request_revision, 26)
                self.assertEqual(bridge.mic_control_action_id, f"{26:032x}")
                self.assertEqual(bridge.mic_control_state, "applied")
                self.assertEqual(bridge.mic_control_error, "")
                self.assertTrue(bridge.mic_enabled)
                self.assertTrue(bridge.ready)
                self.assertFalse(bridge.mic_capture_stopped)

    def test_local_io_bridge_stop_rejects_capture_service_final_flush(self) -> None:
        class FlushOnStopService:
            capture_ready = False
            capture_stopped = True
            last_error = None

            def __init__(self, *, on_segment, **_kwargs) -> None:
                self.on_segment = on_segment

            def start(self) -> bool:
                self.capture_ready = True
                self.capture_stopped = False
                return True

            def stop(self) -> bool:
                self.on_segment(
                    b"final-flush",
                    {"source": "local_mic", "finalFlush": True},
                )
                self.capture_ready = False
                self.capture_stopped = True
                return True

        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge.mic_enabled = True
            bridge.admission_active = True
            with patch(
                "evelyn_core.local_io_bridge.LocalMicCaptureService",
                FlushOnStopService,
            ):
                await bridge._start_mic()
                await bridge._apply_mic_control_request(
                    revision=27,
                    enabled=False,
                    action_id=f"{27:032x}",
                )
                await asyncio.sleep(0)
            return bridge

        bridge = asyncio.run(scenario())

        self.assertEqual(bridge.mic_control_state, "applied")
        self.assertFalse(bridge.mic_enabled)
        self.assertTrue(bridge.mic_capture_stopped)
        self.assertEqual(bridge.queue.qsize(), 0)
        self.assertEqual(bridge.priority_queue.qsize(), 0)
        self.assertEqual(bridge.barge_in_queue.qsize(), 0)
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 1)

    def test_local_io_bridge_rejects_minecraft_world_action_without_lease_owner(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()  # type: ignore[method-assign]
            bridge._launch_minecraft_stack = AsyncMock(  # type: ignore[method-assign]
                return_value={"alreadyReady": False, "launcherExitCode": 0}
            )
            bridge._activate_minecraft_command = AsyncMock(  # type: ignore[method-assign]
                return_value={
                    "commandApplied": True,
                    "connected": True,
                    "connectionState": "connected",
                }
            )
            response = {
                "minecraftCommandRequest": {
                    "revision": 31,
                    "command": "마인크래프트에서 나무 캐줘",
                    "action": "goal",
                }
            }
            bridge._handle_control_response(response)
            bridge._handle_control_response(response)
            await asyncio.gather(*list(bridge.minecraft_command_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertEqual(bridge.minecraft_command_request_revision, 31)
        self.assertEqual(bridge.minecraft_command_state, "failed")
        self.assertEqual(
            bridge.minecraft_command_error,
            "RuntimeError('minecraft_world_authorization_required')",
        )
        self.assertFalse(bridge.minecraft_command_result["commandApplied"])
        self.assertFalse(bridge.minecraft_command_result["connected"])
        bridge._launch_minecraft_stack.assert_not_awaited()  # type: ignore[union-attr]
        bridge._activate_minecraft_command.assert_not_awaited()  # type: ignore[union-attr]
        self.assertEqual(bridge._post_status.await_count, 2)  # type: ignore[union-attr]

    def test_local_io_bridge_ignores_launcher_for_rejected_world_action(self) -> None:
        async def scenario() -> LocalIoBridge:
            bridge = LocalIoBridge()
            bridge._post_status = AsyncMock()  # type: ignore[method-assign]
            bridge._launch_minecraft_stack = AsyncMock(  # type: ignore[method-assign]
                side_effect=RuntimeError("compose failed")
            )
            bridge._handle_control_response(
                {
                    "minecraftCommandRequest": {
                        "revision": 32,
                        "command": "마인크래프트 시작해",
                        "action": "start",
                    }
                }
            )
            await asyncio.gather(*list(bridge.minecraft_command_tasks))
            return bridge

        bridge = asyncio.run(scenario())

        self.assertEqual(bridge.minecraft_command_request_revision, 32)
        self.assertEqual(bridge.minecraft_command_state, "failed")
        self.assertIn(
            "minecraft_world_authorization_required",
            bridge.minecraft_command_error,
        )
        self.assertFalse(bridge.minecraft_command_result["commandApplied"])
        bridge._launch_minecraft_stack.assert_not_awaited()  # type: ignore[union-attr]

    def test_local_io_bridge_reports_dynamic_mic_state_and_control_revision(self) -> None:
        bridge_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_io_bridge.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"micEnabled": self.mic_enabled', bridge_source)
        self.assertIn('"micControlRevision": self.mic_control_request_revision', bridge_source)
        self.assertIn('"micControlActionId": self.mic_control_action_id', bridge_source)
        self.assertIn('"statusSeq": status_seq', bridge_source)
        self.assertIn("if not self.mic_enabled:", bridge_source)

    def test_local_io_bridge_status_reports_sequence_and_mic_action_ids(self) -> None:
        class FakeResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return None

            async def json(self, *, content_type=None) -> dict:
                _ = content_type
                return {}

        class RecordingSession:
            def __init__(self) -> None:
                self.posts: list[tuple[str, dict]] = []

            def post(self, url: str, **kwargs):
                self.posts.append((url, kwargs))
                return FakeResponse()

        async def scenario():
            bridge = LocalIoBridge()
            session = RecordingSession()
            bridge.session = session  # type: ignore[assignment]
            first_applied_action_id = "a" * 32
            pending_action_id = "b" * 32
            bridge.mic_control_request_revision = 30
            bridge.mic_control_action_id = first_applied_action_id
            bridge.mic_control_pending_revision = 31
            bridge.mic_control_pending_action_id = pending_action_id
            bridge.mic_control_state = "applying"
            bridge.mic_control_desired_enabled = False

            with (
                patch.object(bridge, "_refresh_output_readiness"),
                patch(
                    "evelyn_core.local_io_bridge.atomic_json_write"
                ) as write_status,
                patch(
                    "evelyn_core.local_io_bridge.emit_silence_liveness_event"
                ),
                patch.object(
                    local_io_bridge,
                    "LOCAL_BRIDGE_STATUS_AUTH_TOKEN",
                    "reporter-test-token",
                ),
                patch.object(
                    local_io_bridge.aiohttp,
                    "ClientTimeout",
                    return_value=object(),
                    create=True,
                ),
            ):
                await bridge._post_status()
                bridge.mic_control_request_revision = 31
                bridge.mic_control_action_id = pending_action_id
                bridge.mic_control_pending_revision = 0
                bridge.mic_control_pending_action_id = ""
                bridge.mic_control_state = "applied"
                await bridge._post_status()

            payloads = [call.args[1] for call in write_status.call_args_list]
            return bridge, session, payloads

        bridge, session, payloads = asyncio.run(scenario())

        self.assertEqual(bridge.status_seq, 2)
        self.assertEqual([payload["statusSeq"] for payload in payloads], [1, 2])
        self.assertEqual(payloads[0]["micControlRevision"], 30)
        self.assertEqual(payloads[0]["micControlActionId"], "a" * 32)
        self.assertEqual(payloads[0]["micControlPendingRevision"], 31)
        self.assertEqual(payloads[0]["micControlPendingActionId"], "b" * 32)
        self.assertEqual(payloads[1]["micControlRevision"], 31)
        self.assertEqual(payloads[1]["micControlActionId"], "b" * 32)
        self.assertEqual(payloads[1]["micControlPendingRevision"], 0)
        self.assertEqual(payloads[1]["micControlPendingActionId"], "")
        self.assertEqual(len(session.posts), 2)
        for index, (_url, kwargs) in enumerate(session.posts):
            self.assertEqual(kwargs["json"]["statusSeq"], index + 1)
            self.assertEqual(
                kwargs["headers"],
                {
                    local_io_bridge.LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                        "reporter-test-token"
                    )
                },
            )

    def test_local_mic_short_segments_are_reported_as_rejected(self) -> None:
        captured: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: captured.append((pcm, meta)),
            sample_rate=16000,
            min_voiced_ms=200,
        )
        audio = np.full(16000 // 20, 0.1, dtype=np.float32)
        service._capture_active = True
        service._current_blocks = [audio]
        service._voiced_samples = audio.size
        service._total_samples = audio.size

        service._flush_active_segment(force=False)

        self.assertEqual(captured, [])
        self.assertEqual(service.rejected_segment_count, 1)
        self.assertEqual(service.last_rejected_reason, "too_short")
        self.assertEqual((service.last_segment_filter or {}).get("reason"), "too_short")

    def test_local_mic_stop_timeout_preserves_thread_until_later_success(self) -> None:
        class FakeThread:
            def __init__(self) -> None:
                self.alive = True
                self.join_timeouts: list[float] = []

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout: float) -> None:
                self.join_timeouts.append(timeout)

        service = LocalMicCaptureService(on_segment=lambda _pcm, _meta: None)
        thread = FakeThread()
        service._thread = thread  # type: ignore[assignment]
        service._capture_ready = True

        with self.assertRaisesRegex(RuntimeError, "local_mic_stop_timeout"):
            service.stop(join_timeout_sec=0.01)

        self.assertTrue(service._stop_event.is_set())
        self.assertIs(service._thread, thread)
        self.assertTrue(service.thread_alive)
        self.assertFalse(service.capture_stopped)
        self.assertEqual(service.last_error, "local_mic_stop_timeout")
        self.assertEqual(thread.join_timeouts, [0.2])

        thread.alive = False

        self.assertTrue(service.stop(join_timeout_sec=0.01))
        self.assertIsNone(service._thread)
        self.assertFalse(service.thread_alive)
        self.assertFalse(service.capture_ready)
        self.assertTrue(service.capture_stopped)

    def test_local_mic_voice_filter_rejects_silence(self) -> None:
        captured: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: captured.append((pcm, meta)),
            sample_rate=16000,
            min_voiced_ms=200,
            vad_filter_enabled=True,
            env_noise_filter_enabled=True,
            waveform_filter_enabled=True,
        )
        audio = np.zeros(16000 // 2, dtype=np.float32)
        service._capture_active = True
        service._current_blocks = [audio]
        service._voiced_samples = audio.size
        service._total_samples = audio.size

        with patch("evelyn_core.local_mic.is_probably_silent", return_value=True):
            service._flush_active_segment(force=False)

        self.assertEqual(captured, [])
        self.assertEqual(service.rejected_segment_count, 1)
        self.assertEqual(service.last_rejected_reason, "vad_silent")

    def test_local_mic_voice_filter_allows_speech_like_audio(self) -> None:
        captured: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: captured.append((pcm, meta)),
            sample_rate=16000,
            min_voiced_ms=200,
            vad_filter_enabled=True,
            env_noise_filter_enabled=True,
            waveform_filter_enabled=True,
        )
        t = np.arange(16000 // 2, dtype=np.float32) / 16000.0
        audio = (0.08 * np.sin(2.0 * np.pi * 440.0 * t)).astype(np.float32)
        service._capture_active = True
        service._current_blocks = [audio]
        service._voiced_samples = audio.size
        service._total_samples = audio.size

        with patch("evelyn_core.local_mic.is_probably_silent", return_value=False):
            service._flush_active_segment(force=False)

        self.assertEqual(len(captured), 1)
        _, meta = captured[0]
        self.assertEqual(meta["source"], "local_mic")
        self.assertFalse(meta["voice_filter"]["rejected"])

    def test_local_mic_uses_dynamic_max_silence_provider(self) -> None:
        flush_calls: list[bool] = []
        service = LocalMicCaptureService(
            on_segment=lambda _pcm, _meta: None,
            sample_rate=16000,
            block_ms=100,
            max_silence_ms=500,
            max_silence_ms_provider=lambda: 200,
        )
        service._capture_active = True
        service._trailing_silence = 1
        service._flush_active_segment = lambda *, force: flush_calls.append(force)  # type: ignore[method-assign]

        service._consume_block(np.zeros(1600, dtype=np.float32), {})

        self.assertEqual(flush_calls, [False])
        self.assertEqual(service.last_effective_max_silence_ms, 200)

    def test_start_local_disables_local_mic_by_default(self) -> None:
        script = (REPO_ROOT / "evelyn_core" / "start_local.bat").read_text(encoding="utf-8")

        self.assertIn('if "%LOCAL_MIC_ENABLED%"=="" set "LOCAL_MIC_ENABLED=false"', script)
        self.assertIn('if "%LOCAL_MIC_START_THRESHOLD%"=="" set "LOCAL_MIC_START_THRESHOLD=0.002"', script)
        self.assertIn('if "%LOCAL_MIC_CONTINUE_THRESHOLD%"=="" set "LOCAL_MIC_CONTINUE_THRESHOLD=0.001"', script)
        self.assertIn('if "%LOCAL_MIC_MIN_VOICED_MS%"=="" set "LOCAL_MIC_MIN_VOICED_MS=280"', script)
        self.assertIn('if "%LOCAL_MIC_WAVEFORM_FILTER_ENABLED%"=="" set "LOCAL_MIC_WAVEFORM_FILTER_ENABLED=true"', script)
        self.assertNotIn("OMNIVOICE_SPEED", script)
        self.assertNotIn("TTS_CHUNK_TAIL_SILENCE_MS", script)
        self.assertNotIn("LOCAL_TTS_TAIL_SILENCE_MS", script)
        self.assertNotIn("TTS_FIRST_CHUNK_MIN_CHARS", script)
        self.assertNotIn("TTS_NEXT_CHUNK_MIN_CHARS", script)
        self.assertNotIn('set "LOCAL_MIC_ENABLED=true"', script)

    def test_runtime_config_defaults_local_mic_to_disabled_when_unset(self) -> None:
        isolated_env = dict(os.environ)
        isolated_env.pop("LOCAL_MIC_ENABLED", None)
        config_path = RUNTIME_ROOT / "evelyn_core" / "config.py"

        with (
            patch.dict(os.environ, isolated_env, clear=True),
            patch.dict(sys.modules, {"winreg": None}),
        ):
            config_namespace = runpy.run_path(
                str(config_path),
                run_name="__voice_config_default_test__",
            )

        self.assertIs(config_namespace["LOCAL_MIC_ENABLED"], False)

    def test_local_restart_defaults_local_mic_to_disabled_when_unset(self) -> None:
        isolated_env = dict(os.environ)
        isolated_env.pop("LOCAL_MIC_ENABLED", None)

        with patch.dict(os.environ, isolated_env, clear=True):
            launcher, env_overrides, mode = resolve_restart_launcher(
                REPO_ROOT,
                local_restart=True,
                control_page_port=8799,
            )

        self.assertEqual(launcher, REPO_ROOT / "evelyn_core" / "start_local.bat")
        self.assertEqual(mode, "local")
        self.assertEqual(env_overrides["LOCAL_MIC_ENABLED"], "false")

    def test_background_local_mode_uses_docker_core_and_host_supervised_bridge(self) -> None:
        script = (REPO_ROOT / "evelyn_core" / "runtime" / "launchers" / "start_local_background.ps1").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.fast-control.yml").read_text(
            encoding="utf-8"
        )
        bridge_source = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_io_bridge.py").read_text(encoding="utf-8")
        supervisor_source = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "host_supervisor.py").read_text(encoding="utf-8")
        start_env = (REPO_ROOT / "evelyn_core" / "start_env.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn("function Invoke-DockerCommandWithRuntimeChannelTokens", script)
        self.assertIn("Invoke-DockerCommandWithRuntimeChannelTokens -Arguments (", script)
        self.assertIn("'--profile', 'llm'", script)
        self.assertIn("'--profile', 'tts'", script)
        self.assertIn("'--profile', 'stt'", script)
        self.assertNotIn("'--profile', 'voyager'", script)
        self.assertIn("EVELYN_DOCKER_BUILD", script)
        self.assertIn("@('up', '-d', '--no-build')", script)
        self.assertIn("'stop', 'discord_bot'", script)
        self.assertIn("EVELYN_LOCAL_KEEP_DISCORD_BOT", script)
        self.assertIn("evelyn_core.host_supervisor", script)
        self.assertNotIn("-m evelyn_core.local_io_bridge", script)
        self.assertIn('"evelyn_core.local_io_bridge"', supervisor_source)
        self.assertIn("HostVisionBridge", bridge_source)
        self.assertIn('name="local-bridge-host-vision"', bridge_source)
        self.assertIn('"hostVision":', bridge_source)
        self.assertIn("--project-root '$projectRoot'", script)
        self.assertIn("LOCAL_BRIDGE_BOT_API_BASE", script)
        self.assertIn("function New-SecureRuntimeToken", script)
        self.assertIn("LOCAL_BRIDGE_STATUS_AUTH_TOKEN", script)
        self.assertIn("EVELYN_INTERNAL_CONTROL_TOKEN", script)
        self.assertIn("EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN", script)
        self.assertIn(
            "Remove-Item Env:EVELYN_INTERNAL_CONTROL_TOKEN",
            script,
        )
        self.assertIn("Stop-PreviousHostSupervisorGeneration", script)
        self.assertIn("credential generation rotation", script)
        self.assertIn("LOCAL_BRIDGE_STATUS_AUTH_HEADER", bridge_source)
        compose_environment_keys = [
            line.strip().split(":", 1)[0]
            for line in compose.splitlines()
            if line.startswith("      ") and ":" in line
        ]
        self.assertEqual(
            compose_environment_keys.count("LOCAL_BRIDGE_STATUS_AUTH_TOKEN"),
            1,
        )
        self.assertEqual(
            compose_environment_keys.count("EVELYN_INTERNAL_CONTROL_TOKEN"),
            2,
        )
        self.assertEqual(
            compose_environment_keys.count(
                "EVELYN_VOICE_CAPTURE_HOST_AUTH_TOKEN"
            ),
            1,
        )
        self.assertLess(
            script.index("Remove-Item Env:EVELYN_INTERNAL_CONTROL_TOKEN"),
            script.index("-m evelyn_core.host_supervisor"),
        )
        self.assertIn("LOCAL_MIC_START_THRESHOLD = '0.002'", script)
        self.assertIn("LOCAL_MIC_CONTINUE_THRESHOLD = '0.001'", script)
        self.assertIn("LOCAL_MIC_MIN_VOICED_MS = '280'", script)
        self.assertIn("LOCAL_MIC_WAVEFORM_FILTER_ENABLED = 'true'", script)
        self.assertIn("LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC = '0.7'", script)
        self.assertNotIn("py -3 main.py", script)
        self.assertIn("LOCAL_BRIDGE_STREAMING_TTS_ENABLED", bridge_source)
        self.assertIn(
            '"LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED",\n    "false",',
            bridge_source,
        )
        self.assertIn("if not self.mic_enabled:", bridge_source)
        self.assertIn('"micEnabled": self.mic_enabled', bridge_source)
        self.assertIn('"micControlRevision": self.mic_control_request_revision', bridge_source)
        self.assertIn("LOCAL_BRIDGE_TTS_WARMUP_ENABLED = 'true'", script)
        self.assertIn("LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC = '0.5'", script)
        self.assertIn("LOCAL_BRIDGE_TTS_WARMUP_TEXT", bridge_source)
        self.assertIn("tts_warmup_done", bridge_source)
        self.assertIn("/api/control-page/chat-stream", bridge_source)
        self.assertIn("async def _local_voice_chat_payload", bridge_source)
        self.assertIn('"turnId": str(grant.get("turnId") or "")', bridge_source)
        self.assertIn('"admissionToken": str(grant.get("admissionToken") or "")', bridge_source)
        self.assertIn('if event_type == "progress":', bridge_source)
        self.assertIn('await submit_tts_command({"type": "commit"})', bridge_source)
        self.assertIn("for command in buffered_tts_commands:", bridge_source)
        self.assertIn('"progressCount": progress_count', bridge_source)
        self.assertIn('"firstProgressMs": round(first_progress_ms, 1)', bridge_source)
        self.assertIn("_play_streaming_pcm_response", bridge_source)
        self.assertIn("tts_played_streaming", bridge_source)
        self.assertIn('"num_step": OMNIVOICE_NUM_STEP', bridge_source)
        self.assertIn('"stream_strategy": OMNIVOICE_STREAM_STRATEGY', bridge_source)
        self.assertIn('"stream_first_block_steps": OMNIVOICE_STREAM_FIRST_BLOCK_STEPS', bridge_source)
        config_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "config.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'OMNIVOICE_STREAM_STRATEGY = os.getenv("OMNIVOICE_STREAM_STRATEGY", "sentence")',
            config_source,
        )
        self.assertIn(
            'OMNIVOICE_STREAM_FOLLOWUP_STRATEGY = os.getenv("OMNIVOICE_STREAM_FOLLOWUP_STRATEGY", "sentence")',
            config_source,
        )
        self.assertIn(
            'set "OMNIVOICE_STREAM_STRATEGY=sentence"',
            start_env,
        )
        self.assertIn(
            'set "OMNIVOICE_STREAM_FOLLOWUP_STRATEGY=sentence"',
            start_env,
        )
        self.assertNotIn("blockwise_capped_first", start_env)
        self.assertIn('"mic": mic_stats', bridge_source)

    def test_start_local_has_lightweight_vision_profile(self) -> None:
        script = (REPO_ROOT / "evelyn_core" / "start_local.bat").read_text(encoding="utf-8")

        self.assertIn('if /I "%~1"=="--lightweight" set "LOCAL_PROFILE=lightweight"', script)
        self.assertIn('if "%VISION_LOAD_OCR%"=="" set "VISION_LOAD_OCR=false"', script)
        self.assertIn('if "%VISION_WATCH_RUN_OCR%"=="" set "VISION_WATCH_RUN_OCR=false"', script)
        self.assertIn('if "%VISION_OCR_LAZY_LOAD%"=="" set "VISION_OCR_LAZY_LOAD=true"', script)
        self.assertIn('if "%VISION_OCR_UNLOAD_AFTER_REQUEST%"=="" set "VISION_OCR_UNLOAD_AFTER_REQUEST=true"', script)
        self.assertIn("skip Falcon-OCR startup load", script)
        self.assertLess(
            script.index('if /I "%LOCAL_PROFILE%"=="lightweight" ('),
            script.index('call "%~dp0start_env.bat"'),
        )

    def test_main_routes_local_only_mic_without_discord_target(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        local_mic_segment_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_mic_segment_runtime.py"
        ).read_text(encoding="utf-8")
        control_page_tools = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_tools.py"
        ).read_text(encoding="utf-8")
        control_page_state = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state.py"
        ).read_text(encoding="utf-8")
        discord_tts_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "discord_tts_stream_runtime.py"
        ).read_text(encoding="utf-8")
        voice_io_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        voice_runtime_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_runtime_composition_runtime.py"
        ).read_text(encoding="utf-8")
        runtime_lifecycle_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "runtime_lifecycle_composition.py"
        ).read_text(encoding="utf-8")
        control_page_status_tool_composition = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "control_page_status_tool_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("if target is None and deps.local_only_mode", local_mic_segment_runtime)
        self.assertIn("local_only_mode=LOCAL_ONLY_MODE", main_py)
        self.assertIn("local_control_voice_member=self.local_control_voice_member", voice_runtime_composition)
        self.assertIn("handle_local_mic_segment_from_runtime(", voice_runtime_composition)
        self.assertIn("should_drop_discord_audio_for_local_mic = (", main_py)
        self.assertIn(
            "ensure_local_mic_service_started=lambda: ensure_local_mic_service_started()",
            main_py,
        )
        self.assertIn(
            "await deps.ensure_local_mic_service_started()",
            runtime_lifecycle_composition,
        )
        self.assertIn("deps.is_local_speaker_voice_client(vc)", discord_tts_runtime)
        self.assertIn("ask_llm_and_speak_local_from_runtime(", voice_io_composition)
        self.assertIn('"/voice": "voice.status"', control_page_tools)
        self.assertIn('"/voice status": "voice.status"', control_page_tools)
        self.assertIn(
            "control_page_status_tool_composition.build_control_page_tool_runtime_deps",
            main_py,
        )
        self.assertIn(
            "execute_control_page_voice_tool=execute_control_page_voice_tool",
            control_page_status_tool_composition,
        )
        self.assertIn('if tool_name == "voice.status":', control_page_state)

    def test_local_speaker_uses_streaming_sentence_tts_with_full_answer_fallback(self) -> None:
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        voice_delivery_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_delivery_runtime.py"
        ).read_text(encoding="utf-8")
        local_tts_stream_runtime = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "local_tts_stream_runtime.py"
        ).read_text(encoding="utf-8")
        voice_io_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        delivery_entry_composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "delivery_entry_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def start_streaming_local_voice_delivery(", delivery_entry_composition)
        self.assertIn(
            "start_streaming_local_voice_delivery = (\n"
            "    delivery_entry_composition.start_streaming_local_voice_delivery\n"
            ")",
            main_py,
        )
        self.assertIn("async def stream_local_tts_sentences(", voice_io_composition)
        self.assertIn('"delivery_mode"] = "llm_sentence_stream"', voice_delivery_runtime)
        self.assertIn("on_sentence=fanout.on_chunk", voice_delivery_runtime)
        self.assertIn("stream_local_tts_sentences_from_runtime(", voice_io_composition)
        self.assertIn("prefetch_tts_sources(", local_tts_stream_runtime)
        self.assertIn("on_first_playback=", local_tts_stream_runtime)
        self.assertIn('"local_first_playback_logged"', voice_delivery_runtime)
        self.assertIn('"local_tts_first_playback"', delivery_entry_composition)
        self.assertIn("omnivoice_num_step=OMNIVOICE_NUM_STEP", main_py)
        self.assertIn("await deps.speak_answer_local(", voice_delivery_runtime)
        self.assertIn('metrics.setdefault("meta", {})["local_streaming_tts_fallback_used"] = True', voice_delivery_runtime)


class VoiceCaptureWatchdogTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def blocked(reason: str = "voice_capture_consent_heartbeat_stale") -> dict:
        return {
            "authorized": False,
            "reason": reason,
            "ownerDigest": "",
            "leaseDigest": "",
            "heartbeatAt": None,
            "expiresAt": None,
            "checkedAt": time.time(),
            "fenceDigest": "",
        }

    @staticmethod
    def active_bridge(*, stop_fails: bool = False) -> tuple[LocalIoBridge, object]:
        class Service:
            capture_ready = True
            capture_stopped = False

            def stop(self) -> bool:
                if stop_fails:
                    raise RuntimeError("private stop failure")
                self.capture_ready = False
                self.capture_stopped = True
                return True

        bridge = LocalIoBridge()
        service = Service()
        bridge.service = service  # type: ignore[assignment]
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        bridge.voice_capture_consent_binding = ("a" * 64, "b" * 64)
        return bridge, service

    async def test_stale_owner_stops_capture_and_discards_all_pending_audio(self):
        bridge, service = self.active_bridge()
        bridge.voice_capture_fence_digest = "c" * 64
        bridge.queue.put_nowait((b"normal", {}))
        bridge.priority_queue.put_nowait((b"priority", {}))
        bridge.barge_in_queue.put_nowait((b"barge", {}))
        bridge._inspect_voice_capture_host_lease = Mock(  # type: ignore[method-assign]
            return_value=self.blocked()
        )

        await bridge._enforce_voice_capture_watchdog()

        self.assertFalse(bridge.mic_enabled)
        self.assertTrue(bridge.mic_capture_stopped)
        self.assertIsNone(bridge.service)
        self.assertTrue(service.capture_stopped)  # type: ignore[attr-defined]
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 3)
        self.assertEqual(bridge.voice_capture_watchdog_state, "blocked")
        self.assertEqual(bridge.voice_capture_fence_digest, "")
        self.assertIsNotNone(bridge.voice_capture_watchdog_last_stopped_at)
        self.assertEqual(
            bridge.voice_capture_watchdog_last_stopped_at,
            bridge.voice_capture_watchdog_checked_at,
        )

    async def test_stop_failure_never_publishes_false_capture_stopped(self):
        bridge, service = self.active_bridge(stop_fails=True)
        bridge.voice_capture_fence_digest = "c" * 64
        bridge._schedule_watchdog_fail_safe_exit = Mock()  # type: ignore[method-assign]
        bridge._inspect_voice_capture_host_lease = Mock(  # type: ignore[method-assign]
            return_value=self.blocked()
        )

        await bridge._enforce_voice_capture_watchdog()

        self.assertTrue(bridge.mic_enabled)
        self.assertIs(bridge.service, service)
        self.assertFalse(bridge.mic_capture_stopped)
        self.assertEqual(bridge.voice_capture_watchdog_state, "stop_failed")
        self.assertEqual(bridge.voice_capture_fence_digest, "")
        self.assertEqual(
            bridge.mic_control_error,
            "voice_capture_watchdog_stop_failed",
        )
        self.assertIsNone(bridge.voice_capture_watchdog_last_stopped_at)
        bridge._schedule_watchdog_fail_safe_exit.assert_called_once_with()  # type: ignore[union-attr]

    async def test_authorized_watchdog_records_only_stable_fence_digest(self):
        bridge, _service = self.active_bridge()
        lease = fresh_host_lease()
        bridge._inspect_voice_capture_host_lease = Mock(  # type: ignore[method-assign]
            return_value=lease
        )

        await bridge._enforce_voice_capture_watchdog()

        self.assertEqual(
            bridge.voice_capture_fence_digest,
            lease["fenceDigest"],
        )
        watchdog = bridge._voice_capture_watchdog_status()
        self.assertNotIn("fenceDigest", watchdog)
        self.assertNotIn("ownerDigest", watchdog)
        self.assertNotIn("leaseDigest", watchdog)

    async def test_mic_off_clears_fence_before_stop_failure(self):
        bridge, _service = self.active_bridge(stop_fails=True)
        bridge.voice_capture_fence_digest = "c" * 64

        await bridge._apply_mic_control_request(
            revision=40,
            enabled=False,
            action_id=f"{40:032x}",
        )

        self.assertEqual(bridge.voice_capture_fence_digest, "")
        self.assertEqual(bridge.mic_control_state, "failed")

    async def test_stop_failure_fail_safe_forces_process_exit(self):
        with patch.object(local_io_bridge.os, "_exit") as exit_process:
            LocalIoBridge._schedule_watchdog_fail_safe_exit()
            await asyncio.sleep(0)

        exit_process.assert_called_once_with(
            local_io_bridge.VOICE_CAPTURE_FAIL_SAFE_EXIT_CODE
        )

    async def test_on_is_rejected_before_capture_when_owner_heartbeat_is_missing(self):
        bridge = LocalIoBridge()
        bridge._inspect_voice_capture_host_lease = Mock(  # type: ignore[method-assign]
            return_value=self.blocked("voice_capture_consent_heartbeat_missing")
        )
        bridge._start_mic = AsyncMock()  # type: ignore[method-assign]

        await bridge._apply_mic_control_request(
            revision=41,
            enabled=True,
            action_id=f"{41:032x}",
        )

        bridge._start_mic.assert_not_awaited()  # type: ignore[union-attr]
        self.assertFalse(bridge.mic_enabled)
        self.assertEqual(bridge.mic_control_state, "failed")

    async def test_heartbeat_expiring_during_start_is_stopped_before_success(self):
        bridge = LocalIoBridge()
        service = SimpleNamespace(capture_ready=True, capture_stopped=False)

        def stop() -> bool:
            service.capture_ready = False
            service.capture_stopped = True
            return True

        service.stop = stop

        async def start() -> None:
            bridge.service = service  # type: ignore[assignment]
            bridge.ready = True
            bridge.mic_capture_stopped = False

        bridge._start_mic = AsyncMock(side_effect=start)  # type: ignore[method-assign]
        bridge._inspect_voice_capture_host_lease = Mock(  # type: ignore[method-assign]
            side_effect=[fresh_host_lease(), self.blocked()]
        )

        await bridge._apply_mic_control_request(
            revision=42,
            enabled=True,
            action_id=f"{42:032x}",
        )

        self.assertFalse(bridge.mic_enabled)
        self.assertTrue(bridge.mic_capture_stopped)
        self.assertIsNone(bridge.service)
        self.assertEqual(bridge.mic_control_state, "failed")
        self.assertIsNotNone(bridge.voice_capture_watchdog_last_stopped_at)


if __name__ == "__main__":
    unittest.main()
