from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.stt_streaming_runtime import transcribe_complete_audio_stream  # noqa: E402


class SttStreamingRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_stream_returns_only_consistent_final_as_authoritative(self) -> None:
        responses = iter(
            (
                {"revision": 1, "text": "", "isFinal": False},
                {"revision": 2, "text": "이블린 날", "isFinal": False},
                {"revision": 3, "text": "이블린 날씨", "isFinal": False},
            )
        )
        push = Mock(side_effect=lambda *_args, **_kwargs: next(responses))
        finish = Mock(return_value={"revision": 4, "text": "이블린 날씨 알려줘", "isFinal": True})

        result = await transcribe_complete_audio_stream(
            np.full(24000, 0.25, dtype=np.float32),
            sampling_rate=16000,
            service_url="http://stt:8892",
            timeout_sec=5.0,
            chunk_samples=8000,
            start_stream=Mock(
                return_value={
                    "streamId": "s1",
                    "samplingRate": 16000,
                    "decoderProfile": "realtime-ko",
                    "nextSequence": 0,
                }
            ),
            push_chunk=push,
            finish_stream=finish,
            cancel_stream=Mock(),
        )

        self.assertTrue(result.authoritative)
        self.assertEqual(result.final_text, "이블린 날씨 알려줘")
        self.assertEqual(result.partial_text, "이블린 날씨")
        self.assertEqual(result.revision_count, 4)
        self.assertEqual(push.call_count, 3)

    async def test_conflicting_final_requests_batch_fallback(self) -> None:
        responses = iter(
            (
                {"revision": 1, "text": "이블린 오늘 날", "isFinal": False},
                {"revision": 2, "text": "이블린 오늘 날씨", "isFinal": False},
            )
        )
        result = await transcribe_complete_audio_stream(
            np.full(16000, 0.25, dtype=np.float32),
            sampling_rate=16000,
            service_url="http://stt:8892",
            timeout_sec=5.0,
            chunk_samples=8000,
            start_stream=Mock(
                return_value={
                    "streamId": "s1",
                    "samplingRate": 16000,
                    "decoderProfile": "realtime-ko",
                    "nextSequence": 0,
                }
            ),
            push_chunk=Mock(side_effect=lambda *_args, **_kwargs: next(responses)),
            finish_stream=Mock(return_value={"revision": 3, "text": "이블린 내일 날씨", "isFinal": True}),
            cancel_stream=Mock(),
        )

        self.assertFalse(result.authoritative)
        self.assertEqual(result.fallback_reason, "stable_prefix_conflict")

    async def test_failure_cancels_the_open_stream(self) -> None:
        cancel = Mock(return_value={"cancelled": True})
        with self.assertRaisesRegex(RuntimeError, "decode_failed"):
            await transcribe_complete_audio_stream(
                np.full(8000, 0.25, dtype=np.float32),
                sampling_rate=16000,
                service_url="http://stt:8892",
                timeout_sec=5.0,
                start_stream=Mock(
                    return_value={
                        "streamId": "s1",
                        "samplingRate": 16000,
                        "decoderProfile": "realtime-ko",
                        "nextSequence": 0,
                    }
                ),
                push_chunk=Mock(side_effect=RuntimeError("decode_failed")),
                finish_stream=Mock(),
                cancel_stream=cancel,
            )

        cancel.assert_called_once()

    async def test_cancellation_also_releases_the_remote_stream(self) -> None:
        cancel = Mock(return_value={"cancelled": True})

        def cancel_task(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await transcribe_complete_audio_stream(
                np.full(8000, 0.25, dtype=np.float32),
                sampling_rate=16000,
                service_url="http://stt:8892",
                timeout_sec=5.0,
                start_stream=Mock(
                    return_value={
                        "streamId": "s1",
                        "samplingRate": 16000,
                        "decoderProfile": "realtime-ko",
                        "nextSequence": 0,
                    }
                ),
                push_chunk=cancel_task,
                finish_stream=Mock(),
                cancel_stream=cancel,
            )

        cancel.assert_called_once()

    async def test_cancellation_while_starting_releases_returned_stream(self) -> None:
        start_entered = threading.Event()
        allow_start_return = threading.Event()
        cancel = Mock(return_value={"cancelled": True})

        def blocked_start(*_args: object, **_kwargs: object) -> dict[str, object]:
            start_entered.set()
            if not allow_start_return.wait(timeout=1.0):
                raise TimeoutError("test start was not released")
            return {
                "streamId": "start-inflight",
                "samplingRate": 16000,
                "decoderProfile": "realtime-ko",
                "nextSequence": 0,
            }

        task = asyncio.create_task(
            transcribe_complete_audio_stream(
                np.full(8000, 0.25, dtype=np.float32),
                sampling_rate=16000,
                service_url="http://stt:8892",
                timeout_sec=5.0,
                start_stream=blocked_start,
                push_chunk=Mock(),
                finish_stream=Mock(),
                cancel_stream=cancel,
            )
        )
        self.assertTrue(await asyncio.to_thread(start_entered.wait, 1.0))
        task.cancel()
        allow_start_return.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

        cancel.assert_called_once_with(
            service_url="http://stt:8892",
            stream_id="start-inflight",
            timeout_sec=3.0,
        )

    async def test_mismatched_final_flag_is_never_authoritative(self) -> None:
        cancel = Mock(return_value={"cancelled": True})
        with self.assertRaisesRegex(RuntimeError, "stt_stream_response_invalid"):
            await transcribe_complete_audio_stream(
                np.full(8000, 0.25, dtype=np.float32),
                sampling_rate=16000,
                service_url="http://stt:8892",
                timeout_sec=5.0,
                start_stream=Mock(
                    return_value={
                        "streamId": "s1",
                        "samplingRate": 16000,
                        "decoderProfile": "realtime-ko",
                        "nextSequence": 0,
                    }
                ),
                push_chunk=Mock(
                    return_value={"revision": 1, "text": "이블린 날씨", "isFinal": False}
                ),
                finish_stream=Mock(
                    return_value={"revision": 2, "text": "이블린 날씨", "isFinal": False}
                ),
                cancel_stream=cancel,
            )

        cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
