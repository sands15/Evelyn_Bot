from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import local_io_bridge  # noqa: E402
from evelyn_core.local_io_bridge import LocalIoBridge  # noqa: E402
from evelyn_core.voice_capture_consent import (  # noqa: E402
    BRIDGE_STATUS_AUTH_SCOPE,
    voice_capture_artifact_is_authentic,
)


class _Response:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def json(self, *, content_type=None):
        _ = content_type
        return {}


class _Session:
    def post(self, *_args, **_kwargs):
        return _Response()


class _SoundDevice:
    def __init__(
        self,
        *,
        max_output_channels: int = 2,
        missing_devices: set[object] | None = None,
        unsupported_devices: set[object] | None = None,
    ) -> None:
        self.max_output_channels = max_output_channels
        self.missing_devices = set(missing_devices or set())
        self.unsupported_devices = set(unsupported_devices or set())
        self.query_calls: list[tuple[object, object]] = []
        self.check_calls: list[dict[str, object]] = []
        self.raw_output_stream_calls = 0

    def query_devices(self, device=None, kind=None):
        self.query_calls.append((device, kind))
        if device in self.missing_devices:
            raise OSError("PRIVATE missing output device")
        return {"max_output_channels": self.max_output_channels}

    def check_output_settings(self, **kwargs):
        self.check_calls.append(dict(kwargs))
        if kwargs.get("device") in self.unsupported_devices:
            raise OSError("PRIVATE unsupported output format")

    def RawOutputStream(self, *_args, **_kwargs):  # noqa: N802
        self.raw_output_stream_calls += 1
        raise AssertionError("readiness probe must not open an output stream")


class LocalBridgeHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_status_reports_cannot_overwrite_newer_sequence(self) -> None:
        bridge = LocalIoBridge()
        bridge.session = _Session()  # type: ignore[assignment]
        release_first = asyncio.Event()
        write_order: list[int] = []

        with (
            patch.object(
                bridge,
                "_enforce_voice_capture_watchdog",
                new=AsyncMock(),
            ),
            patch.object(bridge, "_refresh_output_readiness"),
            patch.object(bridge, "_output_devices_snapshot", return_value=[]),
            patch("evelyn_core.local_io_bridge.atomic_json_write") as write_status,
            patch("evelyn_core.local_io_bridge.emit_silence_liveness_event"),
        ):
            async def controlled_to_thread(function, *args, **kwargs):
                if function is write_status:
                    sequence = args[1]["statusSeq"]
                    if sequence == 1:
                        await release_first.wait()
                    write_order.append(sequence)
                    return None
                return function(*args, **kwargs)

            with patch.object(
                local_io_bridge.asyncio,
                "to_thread",
                side_effect=controlled_to_thread,
            ):
                first = asyncio.create_task(bridge._post_status())
                await asyncio.sleep(0)
                second = asyncio.create_task(bridge._post_status())
                await asyncio.sleep(0)
                first.cancel()
                await asyncio.sleep(0)
                release_first.set()
                results = await asyncio.gather(
                    first,
                    second,
                    return_exceptions=True,
                )

        self.assertEqual(write_order, [1, 2])
        self.assertEqual(bridge.status_seq, 2)
        self.assertIsInstance(results[0], asyncio.CancelledError)

    async def test_successful_write_clears_transient_heartbeat_error(self) -> None:
        auth_token = "voice-capture-test-auth-token-0123456789"
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "local_bridge" / "status.json"
            bridge = LocalIoBridge()
            bridge.session = _Session()  # type: ignore[assignment]
            bridge.last_error = "heartbeat_write_failed: PermissionError"
            bridge.runtime_errors.record(
                "heartbeat_write_failed",
                PermissionError("C:\\private\\token"),
            )

            with (
                patch(
                    "evelyn_core.local_io_bridge.LOCAL_BRIDGE_STATUS_PATH",
                    status_path,
                ),
                patch.object(local_io_bridge, "sd", _SoundDevice()),
                patch.object(
                    local_io_bridge,
                    "VOICE_CAPTURE_HOST_AUTH_TOKEN",
                    auth_token,
                ),
                patch.object(bridge, "_output_devices_snapshot", return_value=[]),
            ):
                await bridge._post_status()

            payload = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["lastError"], "")
            self.assertEqual(bridge.last_error, "")
            self.assertEqual(payload["errorCount"], 1)
            self.assertEqual(payload["lastErrorCode"], "heartbeat_write_failed")
            self.assertTrue(
                voice_capture_artifact_is_authentic(
                    payload,
                    auth_scope=BRIDGE_STATUS_AUTH_SCOPE,
                    auth_token=auth_token,
                )
            )
            self.assertNotIn(auth_token, json.dumps(payload))
            watchdog = payload["voiceCaptureWatchdog"]
            self.assertEqual(
                set(watchdog),
                {
                    "schema",
                    "state",
                    "reason",
                    "checkedAt",
                    "captureStopped",
                    "stoppedAt",
                    "contentFree",
                },
            )
            self.assertEqual(watchdog["state"], "blocked")
            self.assertTrue(watchdog["captureStopped"])
            self.assertTrue(watchdog["contentFree"])
            self.assertNotIn("private", json.dumps(payload))
            self.assertNotIn("token", json.dumps(payload))
            self.assertNotIn("ownerDigest", json.dumps(watchdog))
            self.assertNotIn("leaseDigest", json.dumps(watchdog))

    async def test_status_projects_selected_output_format_without_opening_stream(self) -> None:
        sound_device = _SoundDevice()
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "local_bridge" / "status.json"
            bridge = LocalIoBridge()
            bridge.session = _Session()  # type: ignore[assignment]
            bridge.output_device = 7

            with (
                patch(
                    "evelyn_core.local_io_bridge.LOCAL_BRIDGE_STATUS_PATH",
                    status_path,
                ),
                patch.object(local_io_bridge, "sd", sound_device),
                patch.object(bridge, "_output_devices_snapshot", return_value=[]),
            ):
                await bridge._post_status()

            payload = json.loads(status_path.read_text(encoding="utf-8"))

        self.assertIs(payload["outputReady"], True)
        self.assertEqual(payload["outputErrorCode"], "")
        self.assertEqual(
            payload["outputFormat"],
            {
                "sampleRate": local_io_bridge.TTS_PCM_RATE,
                "channels": local_io_bridge.TTS_PCM_CHANNELS,
                "dtype": local_io_bridge.TTS_PCM_DTYPE,
            },
        )
        self.assertEqual(sound_device.query_calls, [(7, "output")])
        self.assertEqual(
            sound_device.check_calls,
            [
                {
                    "device": 7,
                    "channels": local_io_bridge.TTS_PCM_CHANNELS,
                    "dtype": local_io_bridge.TTS_PCM_DTYPE,
                    "samplerate": local_io_bridge.TTS_PCM_RATE,
                }
            ],
        )
        self.assertEqual(sound_device.raw_output_stream_calls, 0)

    async def test_unsupported_output_format_uses_fixed_content_free_error(self) -> None:
        sound_device = _SoundDevice(unsupported_devices={3})
        with tempfile.TemporaryDirectory() as temp_dir:
            status_path = Path(temp_dir) / "local_bridge" / "status.json"
            bridge = LocalIoBridge()
            bridge.session = _Session()  # type: ignore[assignment]
            bridge.output_device = 3

            with (
                patch(
                    "evelyn_core.local_io_bridge.LOCAL_BRIDGE_STATUS_PATH",
                    status_path,
                ),
                patch.object(local_io_bridge, "sd", sound_device),
                patch.object(bridge, "_output_devices_snapshot", return_value=[]),
            ):
                await bridge._post_status()

            payload = json.loads(status_path.read_text(encoding="utf-8"))
            serialized = json.dumps(payload, ensure_ascii=False)

        self.assertIs(payload["outputReady"], False)
        self.assertEqual(
            payload["outputErrorCode"],
            local_io_bridge.LOCAL_OUTPUT_FORMAT_UNSUPPORTED,
        )
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("unsupported output format", serialized)
        self.assertEqual(sound_device.raw_output_stream_calls, 0)

    def test_output_device_change_revalidates_selected_device_immediately(self) -> None:
        sound_device = _SoundDevice(unsupported_devices={4})
        bridge = LocalIoBridge()

        with patch.object(local_io_bridge, "sd", sound_device):
            bridge._refresh_output_readiness()
            self.assertTrue(bridge.output_ready)
            bridge._handle_output_device_request(
                {
                    "outputDeviceRequest": {
                        "revision": 1,
                        "outputDevice": "4",
                    }
                }
            )

        self.assertEqual(sound_device.query_calls, [(None, "output"), (4, "output")])
        self.assertEqual(
            [call["device"] for call in sound_device.check_calls],
            [None, 4],
        )
        self.assertFalse(bridge.output_ready)
        self.assertEqual(
            bridge.output_error_code,
            local_io_bridge.LOCAL_OUTPUT_FORMAT_UNSUPPORTED,
        )
        self.assertEqual(sound_device.raw_output_stream_calls, 0)

    def test_missing_selected_device_and_backend_have_fixed_codes(self) -> None:
        bridge = LocalIoBridge()
        bridge.output_device = 9
        with patch.object(
            local_io_bridge,
            "sd",
            _SoundDevice(missing_devices={9}),
        ):
            bridge._refresh_output_readiness()
        self.assertFalse(bridge.output_ready)
        self.assertEqual(
            bridge.output_error_code,
            local_io_bridge.LOCAL_OUTPUT_DEVICE_UNAVAILABLE,
        )

        with patch.object(local_io_bridge, "sd", None):
            bridge._refresh_output_readiness()
        self.assertFalse(bridge.output_ready)
        self.assertEqual(
            bridge.output_error_code,
            local_io_bridge.LOCAL_OUTPUT_BACKEND_UNAVAILABLE,
        )


if __name__ == "__main__":
    unittest.main()
