from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import local_io_bridge  # noqa: E402
from evelyn_core.local_io_bridge import LocalIoBridge  # noqa: E402
from evelyn_core.local_mic import LocalMicCaptureService  # noqa: E402


class LocalMicStreamingCallbacksTests(unittest.TestCase):
    def test_capture_emits_canonical_pcm16_start_chunks_and_accepted_end(self) -> None:
        events: list[tuple] = []
        segments: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: segments.append((pcm, meta)),
            on_speech_start=lambda generation: events.append(("start", generation)),
            on_audio_chunk=lambda generation, pcm: events.append(("chunk", generation, pcm)),
            on_speech_end=lambda generation, accepted: events.append(("end", generation, accepted)),
            sample_rate=16000,
            min_voiced_ms=80,
            stream_chunk_ms=50,
            vad_filter_enabled=False,
            env_noise_filter_enabled=False,
            waveform_filter_enabled=False,
        )
        block = np.full(1600, 0.1, dtype=np.float32)
        service._pre_roll.append((block, 0.1))

        service._begin_capture(meta={})
        service._flush_active_segment(force=False)

        self.assertEqual(events[0], ("start", 1))
        self.assertEqual(events[-1], ("end", 1, True))
        chunks = [event[2] for event in events if event[0] == "chunk"]
        self.assertEqual(len(chunks), 2)
        self.assertEqual(len(b"".join(chunks)), block.size * 2)
        self.assertTrue(all(len(chunk) % 2 == 0 for chunk in chunks))
        self.assertEqual(segments[0][1]["_asrCaptureGeneration"], 1)

    def test_rejected_capture_ends_stream_without_sending_pending_audio(self) -> None:
        events: list[tuple] = []
        segments: list[tuple[bytes, dict]] = []
        service = LocalMicCaptureService(
            on_segment=lambda pcm, meta: segments.append((pcm, meta)),
            on_speech_start=lambda generation: events.append(("start", generation)),
            on_audio_chunk=lambda generation, pcm: events.append(("chunk", generation, pcm)),
            on_speech_end=lambda generation, accepted: events.append(("end", generation, accepted)),
            sample_rate=16000,
            min_voiced_ms=200,
            stream_chunk_ms=500,
            vad_filter_enabled=False,
            env_noise_filter_enabled=False,
            waveform_filter_enabled=False,
        )
        block = np.full(1600, 0.1, dtype=np.float32)
        service._pre_roll.append((block, 0.1))

        service._begin_capture(meta={})
        service._flush_active_segment(force=False)

        self.assertEqual(events, [("start", 1), ("end", 1, False)])
        self.assertEqual(segments, [])


class LocalBridgeStreamingAsrTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bridge = LocalIoBridge()
        self.bridge.mic_enabled = True
        self.bridge.mic_capture_stopped = False
        self.key = (self.bridge.admission_epoch, 1)

    async def asyncTearDown(self) -> None:
        await self.bridge._shutdown_local_asr_stream_worker()

    async def _run_stream(
        self,
        *,
        partials: list[str],
        final: str,
    ) -> tuple[str, AsyncMock]:
        revisions = iter(
            [
                {"revision": index, "text": text, "isFinal": False}
                for index, text in enumerate(partials, start=1)
            ]
        )
        batch = AsyncMock(return_value="batch final")
        self.bridge._transcribe = batch  # type: ignore[method-assign]
        with (
            patch.object(
                local_io_bridge,
                "start_stt_stream_via_service",
                return_value={
                    "streamId": "opaque-stream",
                    "samplingRate": 16000,
                    "decoderProfile": "realtime-ko",
                    "nextSequence": 0,
                },
            ),
            patch.object(
                local_io_bridge,
                "push_stt_stream_chunk_via_service",
                side_effect=lambda *_args, **_kwargs: next(revisions),
            ),
            patch.object(
                local_io_bridge,
                "finish_stt_stream_via_service",
                return_value={
                    "revision": len(partials) + 1,
                    "text": final,
                    "isFinal": True,
                },
            ),
            patch.object(local_io_bridge, "cancel_stt_stream_via_service"),
        ):
            self.bridge._start_local_asr_capture(self.key)
            for _ in partials:
                self.bridge._push_local_asr_audio(self.key, b"\x00\x00" * 160)
            self.bridge._finish_local_asr_capture(self.key, accepted=True)
            await asyncio.wait_for(self.bridge._local_asr_stream_queue.join(), timeout=1.0)
            result = await self.bridge._transcribe_stream_or_batch(
                b"complete segment",
                {"_asrStreamKey": self.key},
            )
        return result, batch

    async def test_authoritative_stream_final_skips_batch(self) -> None:
        result, batch = await self._run_stream(
            partials=["이블린 오늘 날", "이블린 오늘 날씨"],
            final="이블린 오늘 날씨 알려줘",
        )

        self.assertEqual(result, "이블린 오늘 날씨 알려줘")
        batch.assert_not_awaited()

    async def test_local_mic_callbacks_feed_worker_and_bind_final_to_segment(self) -> None:
        class FakeCaptureService:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.capture_ready = True
                self.capture_stopped = False
                self.last_error = ""

            def start(self) -> bool:
                self.kwargs["on_speech_start"](7)
                self.kwargs["on_audio_chunk"](7, b"\x00\x00" * 160)
                self.kwargs["on_speech_end"](7, True)
                self.kwargs["on_segment"](
                    b"complete segment",
                    {
                        "source": "local_mic",
                        "_asrCaptureGeneration": 7,
                    },
                )
                return True

        batch = AsyncMock(return_value="batch final")
        self.bridge._transcribe = batch  # type: ignore[method-assign]
        with (
            patch.object(local_io_bridge, "LocalMicCaptureService", FakeCaptureService),
            patch.object(
                local_io_bridge,
                "start_stt_stream_via_service",
                return_value={
                    "streamId": "opaque-stream",
                    "samplingRate": 16000,
                    "decoderProfile": "realtime-ko",
                    "nextSequence": 0,
                },
            ),
            patch.object(
                local_io_bridge,
                "push_stt_stream_chunk_via_service",
                return_value={"revision": 1, "text": "이블린 날씨", "isFinal": False},
            ),
            patch.object(
                local_io_bridge,
                "finish_stt_stream_via_service",
                return_value={"revision": 2, "text": "이블린 날씨 알려줘", "isFinal": True},
            ),
            patch.object(local_io_bridge, "cancel_stt_stream_via_service"),
        ):
            await self.bridge._start_mic()
            await asyncio.sleep(0)
            await asyncio.wait_for(self.bridge._local_asr_stream_queue.join(), timeout=1.0)
            pcm_bytes, meta = self.bridge.queue.get_nowait()
            self.bridge.queue.task_done()
            result = await self.bridge._transcribe_stream_or_batch(pcm_bytes, meta)

        self.assertEqual(meta["_asrStreamKey"], (self.bridge.admission_epoch, 7))
        self.assertNotIn("_asrCaptureGeneration", meta)
        self.assertEqual(result, "이블린 날씨 알려줘")
        batch.assert_not_awaited()

    async def test_final_conflict_uses_exactly_one_batch_fallback(self) -> None:
        result, batch = await self._run_stream(
            partials=["이블린 오늘 날", "이블린 오늘 날씨"],
            final="이블린 내일 날씨 알려줘",
        )

        self.assertEqual(result, "batch final")
        batch.assert_awaited_once_with(b"complete segment")

    async def test_invalid_stream_response_cancels_and_uses_one_batch(self) -> None:
        batch = AsyncMock(return_value="batch final")
        self.bridge._transcribe = batch  # type: ignore[method-assign]
        with (
            patch.object(
                local_io_bridge,
                "start_stt_stream_via_service",
                return_value={
                    "streamId": "opaque-stream",
                    "samplingRate": 16000,
                    "decoderProfile": "realtime-ko",
                    "nextSequence": 0,
                },
            ),
            patch.object(
                local_io_bridge,
                "push_stt_stream_chunk_via_service",
                return_value={"revision": 1, "text": "private partial", "isFinal": True},
            ),
            patch.object(local_io_bridge, "finish_stt_stream_via_service") as finish,
            patch.object(local_io_bridge, "cancel_stt_stream_via_service") as cancel,
        ):
            self.bridge._start_local_asr_capture(self.key)
            self.bridge._push_local_asr_audio(self.key, b"\x00\x00" * 160)
            self.bridge._finish_local_asr_capture(self.key, accepted=True)
            await asyncio.wait_for(self.bridge._local_asr_stream_queue.join(), timeout=1.0)
            result = await self.bridge._transcribe_stream_or_batch(
                b"complete segment",
                {"_asrStreamKey": self.key},
            )

        self.assertEqual(result, "batch final")
        finish.assert_not_called()
        cancel.assert_called_once()
        batch.assert_awaited_once_with(b"complete segment")

    async def test_invalid_start_contract_cancels_returned_stream_id(self) -> None:
        with (
            patch.object(
                local_io_bridge,
                "start_stt_stream_via_service",
                return_value={
                    "streamId": "invalid-contract",
                    "samplingRate": 8000,
                    "decoderProfile": "realtime-ko",
                    "nextSequence": 0,
                },
            ),
            patch.object(
                local_io_bridge,
                "cancel_stt_stream_via_service",
            ) as cancel,
        ):
            self.bridge._start_local_asr_capture(self.key)
            await asyncio.wait_for(
                self.bridge._local_asr_stream_queue.join(),
                timeout=1.0,
            )

        cancel.assert_called_once_with(
            service_url=local_io_bridge.STT_SERVICE_URL,
            stream_id="invalid-contract",
            timeout_sec=5.0,
        )

    async def test_shutdown_during_remote_start_cancels_returned_stream(self) -> None:
        start_entered = threading.Event()
        allow_start_return = threading.Event()

        def blocked_start(*_args, **_kwargs):
            start_entered.set()
            if not allow_start_return.wait(timeout=1.0):
                raise TimeoutError("test start was not released")
            return {
                "streamId": "start-inflight",
                "samplingRate": 16000,
                "decoderProfile": "realtime-ko",
                "nextSequence": 0,
            }

        with (
            patch.object(
                local_io_bridge,
                "start_stt_stream_via_service",
                side_effect=blocked_start,
            ),
            patch.object(
                local_io_bridge,
                "cancel_stt_stream_via_service",
            ) as cancel,
        ):
            self.bridge._start_local_asr_capture(self.key)
            self.assertTrue(await asyncio.to_thread(start_entered.wait, 1.0))
            shutdown = asyncio.create_task(
                self.bridge._shutdown_local_asr_stream_worker()
            )
            await asyncio.sleep(0)
            allow_start_return.set()
            await shutdown

        cancel.assert_called_once_with(
            service_url=local_io_bridge.STT_SERVICE_URL,
            stream_id="start-inflight",
            timeout_sec=3.0,
        )

    async def test_shutdown_drains_physical_chunk_before_remote_cancel(self) -> None:
        push_entered = threading.Event()
        allow_push_return = threading.Event()
        push_returned = threading.Event()
        cancel_before_push_returned: list[bool] = []

        def blocked_push(*_args, **_kwargs):
            push_entered.set()
            if not allow_push_return.wait(timeout=2.0):
                raise TimeoutError("test push was not released")
            push_returned.set()
            return {"revision": 1, "text": "이블린", "isFinal": False}

        def cancel_stream(*_args, **_kwargs):
            cancel_before_push_returned.append(not push_returned.is_set())
            return {"cancelled": True}

        shutdown: asyncio.Task[None] | None = None
        try:
            with (
                patch.object(
                    local_io_bridge,
                    "start_stt_stream_via_service",
                    return_value={
                        "streamId": "chunk-inflight",
                        "samplingRate": 16000,
                        "decoderProfile": "realtime-ko",
                        "nextSequence": 0,
                    },
                ),
                patch.object(
                    local_io_bridge,
                    "push_stt_stream_chunk_via_service",
                    side_effect=blocked_push,
                ),
                patch.object(
                    local_io_bridge,
                    "cancel_stt_stream_via_service",
                    side_effect=cancel_stream,
                ),
            ):
                self.bridge._start_local_asr_capture(self.key)
                self.bridge._push_local_asr_audio(
                    self.key,
                    b"\x00\x00" * 160,
                )
                self.assertTrue(
                    await asyncio.to_thread(push_entered.wait, 1.0)
                )
                shutdown = asyncio.create_task(
                    self.bridge._shutdown_local_asr_stream_worker()
                )
                done, _pending = await asyncio.wait(
                    {shutdown},
                    timeout=0.1,
                )
                self.assertEqual(done, set())

                allow_push_return.set()
                await asyncio.wait_for(shutdown, timeout=2.0)
        finally:
            allow_push_return.set()
            if shutdown is not None:
                await asyncio.gather(shutdown, return_exceptions=True)

        self.assertEqual(cancel_before_push_returned, [False])

    async def test_repeated_run_cancellation_keeps_stt_cleanup_owned(self) -> None:
        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class IdleBridge:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def run(self) -> None:
                await asyncio.Event().wait()

        push_entered = threading.Event()
        allow_push_return = threading.Event()
        push_returned = threading.Event()
        stop_entered = asyncio.Event()
        allow_stop_return = asyncio.Event()
        run_started = asyncio.Event()
        cancel_before_push_returned: list[bool] = []

        def blocked_push(*_args, **_kwargs):
            push_entered.set()
            if not allow_push_return.wait(timeout=2.0):
                raise TimeoutError("test push was not released")
            push_returned.set()
            return {"revision": 1, "text": "이블린", "isFinal": False}

        def cancel_stream(*_args, **_kwargs):
            cancel_before_push_returned.append(not push_returned.is_set())
            return {"cancelled": True}

        async def stop_mic_service(*, reason: str) -> None:
            self.assertEqual(reason, "bridge_stopping")
            stop_entered.set()
            await allow_stop_return.wait()

        async def post_status(*_args, **_kwargs) -> None:
            run_started.set()

        self.bridge.service = object()  # type: ignore[assignment]
        run_task: asyncio.Task[None] | None = None
        try:
            with (
                patch.object(
                    local_io_bridge.aiohttp,
                    "ClientSession",
                    return_value=FakeSession(),
                ),
                patch.object(local_io_bridge, "HostVisionBridge", IdleBridge),
                patch.object(local_io_bridge, "HostUiActionBridge", IdleBridge),
                patch.object(self.bridge, "_start_mic", AsyncMock()),
                patch.object(
                    self.bridge,
                    "_stop_mic_service",
                    side_effect=stop_mic_service,
                ),
                patch.object(
                    self.bridge,
                    "_post_status",
                    side_effect=post_status,
                ),
                patch.object(self.bridge, "_ensure_tts_warmup"),
                patch.object(
                    local_io_bridge,
                    "start_stt_stream_via_service",
                    return_value={
                        "streamId": "run-shutdown-inflight",
                        "samplingRate": 16000,
                        "decoderProfile": "realtime-ko",
                        "nextSequence": 0,
                    },
                ),
                patch.object(
                    local_io_bridge,
                    "push_stt_stream_chunk_via_service",
                    side_effect=blocked_push,
                ),
                patch.object(
                    local_io_bridge,
                    "cancel_stt_stream_via_service",
                    side_effect=cancel_stream,
                ),
            ):
                self.bridge._start_local_asr_capture(self.key)
                self.bridge._push_local_asr_audio(
                    self.key,
                    b"\x00\x00" * 160,
                )
                self.assertTrue(
                    await asyncio.to_thread(push_entered.wait, 1.0)
                )
                run_task = asyncio.create_task(self.bridge.run())
                await asyncio.wait_for(run_started.wait(), timeout=1.0)

                run_task.cancel()
                await asyncio.wait_for(stop_entered.wait(), timeout=1.0)
                run_task.cancel()
                done, _pending = await asyncio.wait(
                    {run_task},
                    timeout=0.1,
                )
                self.assertEqual(done, set())

                allow_stop_return.set()
                for _ in range(100):
                    if self.bridge._local_asr_stream_shutdown:
                        break
                    await asyncio.sleep(0)
                self.assertTrue(self.bridge._local_asr_stream_shutdown)
                run_task.cancel()
                done, _pending = await asyncio.wait(
                    {run_task},
                    timeout=0.1,
                )
                self.assertEqual(done, set())

                allow_push_return.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(run_task, timeout=2.0)
        finally:
            allow_stop_return.set()
            allow_push_return.set()
            if run_task is not None:
                await asyncio.gather(run_task, return_exceptions=True)
            await self.bridge._shutdown_local_asr_stream_worker()
            remaining_tasks = tuple(
                task
                for task in (
                    self.bridge.host_vision_task,
                    self.bridge.host_ui_action_task,
                    self.bridge.barge_worker_task,
                )
                if task is not None
            )
            for task in remaining_tasks:
                task.cancel()
            await asyncio.gather(*remaining_tasks, return_exceptions=True)

        self.assertEqual(cancel_before_push_returned, [False])
        self.assertIsNone(self.bridge._local_asr_stream_task)

    async def test_stale_epoch_never_starts_remote_stream_or_batch(self) -> None:
        batch = AsyncMock(return_value="batch final")
        self.bridge._transcribe = batch  # type: ignore[method-assign]
        with patch.object(local_io_bridge, "start_stt_stream_via_service") as start:
            self.bridge._start_local_asr_capture(self.key)
            self.bridge._invalidate_local_voice_admission("test_stale")
            await asyncio.wait_for(self.bridge._local_asr_stream_queue.join(), timeout=1.0)
            result = await self.bridge._transcribe_stream_or_batch(
                b"complete segment",
                {"_asrStreamKey": self.key},
            )

        self.assertEqual(result, "")
        start.assert_not_called()
        batch.assert_not_awaited()

    async def test_stream_event_backlog_is_bounded_and_falls_back_once(self) -> None:
        self.bridge._local_asr_stream_queue = asyncio.Queue(maxsize=1)
        self.bridge._ensure_local_asr_stream_worker = lambda: None  # type: ignore[method-assign]
        batch = AsyncMock(return_value="batch final")
        self.bridge._transcribe = batch  # type: ignore[method-assign]

        self.bridge._start_local_asr_capture(self.key)
        future = self.bridge._local_asr_stream_futures[self.key]
        self.bridge._push_local_asr_audio(self.key, b"\x00\x00" * 160)
        result = await self.bridge._transcribe_stream_or_batch(
            b"complete segment",
            {"_asrStreamKey": self.key},
        )

        self.assertEqual(self.bridge._local_asr_stream_queue.qsize(), 1)
        self.assertTrue(future.done())
        self.assertIsNone(future.result())
        self.assertEqual(result, "batch final")
        batch.assert_awaited_once_with(b"complete segment")
        self.bridge._local_asr_stream_queue.get_nowait()
        self.bridge._local_asr_stream_queue.task_done()

    async def test_rejected_barge_in_releases_stream_future(self) -> None:
        future = asyncio.get_running_loop().create_future()
        self.bridge._local_asr_stream_futures[self.key] = future
        worker = asyncio.create_task(self.bridge._barge_in_worker())
        await self.bridge.barge_in_queue.put(
            (
                b"complete segment",
                {
                    "_admissionEpoch": self.bridge.admission_epoch + 1,
                    "_asrStreamKey": self.key,
                },
            )
        )

        await asyncio.wait_for(self.bridge.barge_in_queue.join(), timeout=1.0)
        worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker

        self.assertTrue(future.done())
        self.assertIsNone(future.result())
        self.assertNotIn(self.key, self.bridge._local_asr_stream_futures)


if __name__ == "__main__":
    unittest.main()
