from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_io_bridge import LocalIoBridge  # noqa: E402
from evelyn_core import local_io_bridge  # noqa: E402


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


class LocalIoBridgeInputSuppressionTests(unittest.IsolatedAsyncioTestCase):
    def test_speaking_input_is_not_discarded_but_post_playback_cooldown_is(self) -> None:
        bridge = LocalIoBridge()
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False

        self.assertFalse(bridge._mic_input_is_suppressed())
        bridge.speaking = True
        self.assertFalse(bridge._mic_input_is_suppressed())
        bridge.speaking = False
        bridge.mic_input_suppressed_until = time.monotonic() + 0.7
        self.assertTrue(bridge._mic_input_is_suppressed())
        bridge.mic_input_suppressed_until = time.monotonic() - 0.01
        self.assertFalse(bridge._mic_input_is_suppressed())

    def test_discard_pending_mic_segments_drains_queue_and_tracks_count(self) -> None:
        bridge = LocalIoBridge()
        bridge.queue.put_nowait((b"one", {"source": "test"}))
        bridge.queue.put_nowait((b"two", {"source": "test"}))
        bridge.priority_queue.put_nowait((b"priority", {"source": "test"}))
        bridge.barge_in_queue.put_nowait((b"barge", {"source": "test"}))

        self.assertEqual(bridge._discard_pending_mic_segments(), 4)
        self.assertTrue(bridge.queue.empty())
        self.assertTrue(bridge.priority_queue.empty())
        self.assertTrue(bridge.barge_in_queue.empty())
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 4)
        self.assertEqual(bridge._discard_pending_mic_segments(), 0)
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 4)

    async def test_already_queued_segment_survives_post_playback_cooldown(self) -> None:
        bridge = LocalIoBridge()
        bridge.mic_enabled = True
        bridge.mic_capture_stopped = False
        bridge.mic_input_suppressed_until = time.monotonic() + 0.7
        bridge._post_status = AsyncMock()  # type: ignore[method-assign]
        bridge._transcribe = AsyncMock(return_value="/help")  # type: ignore[method-assign]
        bridge._chat = AsyncMock(  # type: ignore[method-assign]
            return_value=local_io_bridge.LocalChatReply(
                text="ok",
                memory_handoff=local_io_bridge.LocalMemoryHandoff(
                    state="not_used",
                    position=None,
                ),
            )
        )
        admission = install_admission_grant(bridge)

        await bridge._handle_segment(b"user speech", {"source": "test"})

        admission.assert_awaited_once()
        self.assertEqual(bridge.segment_count, 1)
        self.assertEqual(bridge.transcript_count, 1)
        self.assertEqual(bridge.suppressed_mic_segment_count, 0)
        self.assertEqual(bridge.last_error, "")

    async def test_tts_cleanup_preserves_queue_and_clone_fallback_keeps_one_owner(self) -> None:
        class FakeResponse:
            def __init__(self, status: int) -> None:
                self.status = status

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def text(self) -> str:
                return "tts failed"

        class FakeSession:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            def post(self, _url, *, json, timeout):
                del timeout
                self.payloads.append(dict(json))
                return FakeResponse(503 if len(self.payloads) == 1 else 200)

        bridge = LocalIoBridge()
        session = FakeSession()
        bridge.session = session  # type: ignore[assignment]
        bridge.queue.put_nowait((b"next user turn", {"source": "test"}))
        bridge._post_status = AsyncMock()  # type: ignore[method-assign]
        bridge._play_streaming_pcm_response = AsyncMock(  # type: ignore[method-assign]
            return_value=(1024, 1024, 12.5)
        )

        await bridge._speak_with_payload({"input": "hello", "voice": "clone:evelyn"})

        self.assertEqual(
            [payload["voice"] for payload in session.payloads],
            ["clone:evelyn", "auto"],
        )
        self.assertEqual(bridge.last_tts_playback["voice"], "auto")
        self.assertEqual(bridge.queue.qsize(), 1)
        self.assertFalse(bridge.speaking)
        self.assertEqual(bridge.playback_controller.owner_id, "")

    async def test_clone_fallback_requires_zero_audio_and_no_playback(self) -> None:
        class FakeResponse:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        class FakeSession:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            def post(self, _url, *, json, timeout):
                del timeout
                self.payloads.append(dict(json))
                return FakeResponse()

        bridge = LocalIoBridge()
        session = FakeSession()
        bridge.session = session  # type: ignore[assignment]
        bridge._post_status = AsyncMock()  # type: ignore[method-assign]
        bridge._play_streaming_pcm_response = AsyncMock(  # type: ignore[method-assign]
            side_effect=[(0, 0, None), (1024, 1024, 12.5)]
        )

        await bridge._speak_with_payload(
            {"input": "hello", "voice": "clone:evelyn"}
        )

        self.assertEqual(
            [payload["voice"] for payload in session.payloads],
            ["clone:evelyn", "auto"],
        )
        self.assertEqual(
            bridge._play_streaming_pcm_response.await_count,  # type: ignore[union-attr]
            2,
        )
        self.assertEqual(bridge.last_tts_playback["voice"], "auto")
        self.assertEqual(bridge.play_count, 1)
        self.assertEqual(bridge.playback_controller.owner_id, "")

        session.payloads.clear()
        bridge._play_streaming_pcm_response = AsyncMock(  # type: ignore[method-assign]
            return_value=(1024, 512, 12.5)
        )

        await bridge._speak_with_payload(
            {"input": "hello", "voice": "clone:evelyn"}
        )

        self.assertEqual(
            [payload["voice"] for payload in session.payloads],
            ["clone:evelyn"],
        )
        self.assertEqual(bridge.last_tts_playback["voice"], "clone:evelyn")


if __name__ == "__main__":
    unittest.main()
