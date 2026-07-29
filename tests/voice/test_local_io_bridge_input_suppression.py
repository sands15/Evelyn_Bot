from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_io_bridge import LocalIoBridge  # noqa: E402


class LocalIoBridgeInputSuppressionTests(unittest.IsolatedAsyncioTestCase):
    def test_speaking_input_is_not_discarded_but_post_playback_cooldown_is(self) -> None:
        bridge = LocalIoBridge()

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

        self.assertEqual(bridge._discard_pending_mic_segments(), 2)
        self.assertTrue(bridge.queue.empty())
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 2)
        self.assertEqual(bridge._discard_pending_mic_segments(), 0)
        self.assertEqual(bridge.discarded_pending_mic_segment_count, 2)

    async def test_handle_segment_drops_cooldown_audio_before_stt(self) -> None:
        bridge = LocalIoBridge()
        bridge.mic_input_suppressed_until = time.monotonic() + 0.7

        async def unexpected_transcribe(_pcm_bytes: bytes) -> str:
            raise AssertionError("suppressed audio reached STT")

        bridge._transcribe = unexpected_transcribe  # type: ignore[method-assign]
        await bridge._handle_segment(b"echo", {"source": "test"})

        self.assertEqual(bridge.segment_count, 0)
        self.assertEqual(bridge.transcript_count, 0)
        self.assertEqual(bridge.suppressed_mic_segment_count, 1)
        self.assertEqual(bridge.last_error, "")


if __name__ == "__main__":
    unittest.main()
