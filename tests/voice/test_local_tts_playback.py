from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import local_tts_playback  # noqa: E402
from evelyn_core.local_tts_playback import (  # noqa: E402
    LocalTtsPlaybackManager,
    local_tts_tail_silence_bytes,
    normalize_output_device,
)
from evelyn_core.observability_metrics import (  # noqa: E402
    VOICE_LATENCY_TRACE_METRICS_KEY,
    VoiceLatencyTrace,
)


class FakeSource:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.cleanup_called = False
        self.error = None

    def read(self) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def cleanup(self) -> None:
        self.cleanup_called = True


class StoppableSource(FakeSource):
    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self.finish_called = False

    def finish(self) -> None:
        self.finish_called = True
        self.chunks = []


class ObservingSource(FakeSource):
    def __init__(self, chunks: list[bytes]) -> None:
        super().__init__(chunks)
        self.read_count = 0
        self.writes_seen_before_second_read: list[bytes] = []

    def read(self) -> bytes:
        self.read_count += 1
        if self.read_count == 2:
            self.writes_seen_before_second_read = list(FakeRawOutputStream.writes)
        return super().read()


class BlockingSource(FakeSource):
    def __init__(self, *, release_on_finish: bool = True) -> None:
        super().__init__([b"first"])
        self.release_on_finish = release_on_finish
        self.read_count = 0
        self.second_read_started = threading.Event()
        self.release_read = threading.Event()
        self.worker_exited = threading.Event()
        self.finish_requested = threading.Event()
        self.finish_called = False

    def read(self) -> bytes:
        self.read_count += 1
        if self.read_count == 1:
            return b"first"
        self.second_read_started.set()
        self.release_read.wait(timeout=2.0)
        self.worker_exited.set()
        return b""

    def finish(self) -> None:
        self.finish_called = True
        self.finish_requested.set()
        if self.release_on_finish:
            self.release_read.set()


class PrePlaybackBlockingSource(FakeSource):
    def __init__(self) -> None:
        super().__init__([b"must-not-play"])
        self.read_started = threading.Event()
        self.release_read = threading.Event()
        self.finish_called = False

    def read(self) -> bytes:
        self.read_started.set()
        self.release_read.wait(timeout=2.0)
        return super().read()

    def finish(self) -> None:
        self.finish_called = True
        self.chunks = []
        self.release_read.set()


class FakeRawOutputStream:
    writes: list[bytes] = []
    abort_count = 0
    stop_count = 0
    active_count = 0
    max_active_count = 0

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self):
        type(self).active_count += 1
        type(self).max_active_count = max(
            type(self).max_active_count,
            type(self).active_count,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        type(self).active_count -= 1
        return None

    def write(self, chunk: bytes) -> None:
        self.writes.append(bytes(chunk))

    def abort(self) -> None:
        type(self).abort_count += 1

    def stop(self) -> None:
        type(self).stop_count += 1


class FakeSoundDevice:
    RawOutputStream = FakeRawOutputStream


class LocalTtsPlaybackTests(unittest.TestCase):
    def test_normalize_output_device(self) -> None:
        self.assertIsNone(normalize_output_device(None))
        self.assertIsNone(normalize_output_device("default"))
        self.assertEqual(normalize_output_device("3"), 3)
        self.assertEqual(normalize_output_device("Speakers"), "Speakers")

    def test_manager_writes_source_chunks_to_sounddevice(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            source = FakeSource([b"\x01\x02", b"\x03\x04"])
            ok = asyncio.run(manager.play_source(source))
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertTrue(source.cleanup_called)
        self.assertEqual(FakeRawOutputStream.writes, [b"\x01\x02", b"\x03\x04", local_tts_tail_silence_bytes()])
        snapshot = manager.snapshot()
        self.assertEqual(snapshot["playCount"], 1)
        self.assertEqual(snapshot["playedBytes"], 4 + len(local_tts_tail_silence_bytes()))

    def test_manager_streams_first_chunk_before_reading_entire_source(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            source = ObservingSource([b"first", b"second"])
            ok = asyncio.run(manager.play_source(source))
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertEqual(source.writes_seen_before_second_read, [b"first"])
        self.assertEqual(FakeRawOutputStream.writes[:2], [b"first", b"second"])

    def test_manager_leases_first_playback_attempt_before_first_write(self) -> None:
        trace = VoiceLatencyTrace()
        metrics = {
            "marks": {},
            "meta": {},
            VOICE_LATENCY_TRACE_METRICS_KEY: trace,
        }
        attempt_markers_at_write: list[bool] = []

        class AttemptObservingStream(FakeRawOutputStream):
            def write(self, chunk: bytes) -> None:
                attempt_markers_at_write.append(
                    metrics["meta"].get("local_tts_playback_attempted") is True
                )
                super().write(chunk)

        class AttemptObservingSoundDevice:
            RawOutputStream = AttemptObservingStream

        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        callback_writes: list[list[bytes]] = []
        local_tts_playback.sd = AttemptObservingSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            source = FakeSource([b"first", b"second"])
            ok = asyncio.run(
                manager.play_source(
                    source,
                    metrics=metrics,
                    on_first_playback=lambda: callback_writes.append(list(FakeRawOutputStream.writes)),
                )
            )
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertEqual(attempt_markers_at_write, [True, True, True])
        self.assertEqual(callback_writes, [[b"first"]])
        self.assertEqual(FakeRawOutputStream.writes[:2], [b"first", b"second"])
        self.assertIn(
            "playback_first_write",
            trace.public_summary()["markers_ms"],
        )

    def test_manager_leases_attempt_before_partial_write_failure(self) -> None:
        trace = VoiceLatencyTrace()
        metrics = {
            "marks": {},
            "meta": {},
            VOICE_LATENCY_TRACE_METRICS_KEY: trace,
        }
        attempt_markers_at_write: list[bool] = []

        class PartialWriteFailureStream(FakeRawOutputStream):
            def write(self, chunk: bytes) -> None:
                attempt_markers_at_write.append(
                    metrics["meta"].get("local_tts_playback_attempted") is True
                )
                super().write(chunk)
                raise OSError("device failed after accepting bytes")

        class PartialWriteFailureSoundDevice:
            RawOutputStream = PartialWriteFailureStream

        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        callback_count = 0

        def on_first_playback() -> None:
            nonlocal callback_count
            callback_count += 1

        local_tts_playback.sd = PartialWriteFailureSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            ok = asyncio.run(
                manager.play_source(
                    FakeSource([b"partial-audio"]),
                    metrics=metrics,
                    on_first_playback=on_first_playback,
                )
            )
        finally:
            local_tts_playback.sd = original_sd

        self.assertFalse(ok)
        self.assertEqual(FakeRawOutputStream.writes, [b"partial-audio"])
        self.assertEqual(attempt_markers_at_write, [True])
        self.assertEqual(callback_count, 0)
        self.assertIs(metrics["meta"]["local_tts_playback_attempted"], True)
        self.assertEqual(manager.snapshot()["playCount"], 0)
        self.assertNotIn(
            "playback_first_write",
            trace.public_summary()["markers_ms"],
        )

    def test_playback_failure_state_and_log_do_not_expose_exception_detail(self) -> None:
        privacy_sentinel = "VOICE_PRIVACY_SENTINEL_LOCAL_TTS_TOKEN_URL"

        class PrivateFailureStream(FakeRawOutputStream):
            def write(self, chunk: bytes) -> None:
                raise OSError(privacy_sentinel)

        class PrivateFailureSoundDevice:
            RawOutputStream = PrivateFailureStream

        original_sd = local_tts_playback.sd
        logs: list[str] = []
        local_tts_playback.sd = PrivateFailureSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, log=logs.append)
            ok = asyncio.run(manager.play_source(FakeSource([b"audio"])))
        finally:
            local_tts_playback.sd = original_sd

        self.assertFalse(ok)
        snapshot = manager.snapshot()
        public_output = f"{snapshot!r}\n{''.join(logs)}"
        self.assertNotIn(privacy_sentinel, public_output)
        self.assertEqual(snapshot["lastError"], "playback_failed:OSError")
        self.assertIn("playback_failed errorType=OSError", "\n".join(logs))

    def test_manager_revalidates_attempt_immediately_before_first_write(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        callback_count = 0
        metrics = {
            "marks": {},
            "meta": {
                "validation_session_id": "validation-1",
                "validation_step_id": "local-step-1",
                "validation_attempt_id": "attempt-1",
            },
        }

        def on_first_playback() -> None:
            nonlocal callback_count
            callback_count += 1

        local_tts_playback.sd = FakeSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True)
            with patch.object(
                local_tts_playback,
                "validation_attempt_binding_is_current",
                side_effect=[True, False],
            ) as validate:
                ok = asyncio.run(
                    manager.play_source(
                        FakeSource([b"must-not-play"]),
                        metrics=metrics,
                        on_first_playback=on_first_playback,
                    )
                )
        finally:
            local_tts_playback.sd = original_sd

        self.assertFalse(ok)
        self.assertEqual(callback_count, 0)
        self.assertEqual(FakeRawOutputStream.writes, [])
        self.assertEqual(validate.call_count, 2)
        for call in validate.call_args_list:
            self.assertEqual(call.kwargs["surface"], "local")
            self.assertIs(call.kwargs["reject_unbound_when_active"], True)
        self.assertIs(
            metrics["meta"]["local_tts_playback_terminal_no_fallback"],
            True,
        )
        self.assertEqual(
            metrics["meta"]["local_tts_playback_rejected_reason"],
            "validation_attempt_stale",
        )
        self.assertEqual(manager.snapshot()["playCount"], 0)

    def test_natural_completion_gap_rejects_false_qualified_interrupt(self) -> None:
        class CompletionGapManager(LocalTtsPlaybackManager):
            def __init__(self) -> None:
                super().__init__(enabled=True)
                self.worker_returned = threading.Event()

            def _play_source_sync(self, source, *, binding, on_first_playback=None):
                result = super()._play_source_sync(
                    source,
                    binding=binding,
                    on_first_playback=on_first_playback,
                )
                self.worker_returned.set()
                return result

        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = CompletionGapManager()
            source = BlockingSource(release_on_finish=False)
            play_task = asyncio.create_task(
                manager.play_source(source, metrics=metrics)
            )
            self.assertTrue(
                await asyncio.to_thread(source.second_read_started.wait, 1.0)
            )
            source.release_read.set()
            # Deliberately block the loop: the worker can return and set its
            # terminal marker, but play_source.finally cannot clear the binding.
            self.assertTrue(manager.worker_returned.wait(timeout=1.0))
            with manager._state_lock:
                binding = manager._active_binding
                self.assertIsNotNone(binding)
                self.assertTrue(binding.worker_terminal)
                self.assertFalse(binding.stop_requested)

            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            with manager._state_lock:
                self.assertIs(manager._active_binding, binding)
                self.assertFalse(binding.stop_requested)
                self.assertIsNone(binding.stop_acceptance_token)
            self.assertFalse(play_task.done())
            played = await play_task
            return receipt, played

        try:
            receipt, played = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNone(receipt)
        self.assertTrue(played)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    def test_request_stop_interrupts_active_local_playback(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        FakeRawOutputStream.abort_count = 0
        FakeRawOutputStream.stop_count = 0
        stop_results: list[object] = []
        local_tts_playback.sd = FakeSoundDevice()
        try:
            manager = LocalTtsPlaybackManager(enabled=True, device="default")
            source = StoppableSource([b"first", b"second"])
            ok = asyncio.run(
                manager.play_source(
                    source,
                    turn_id="turn-source-1",
                    session_key="session-source-1",
                    on_first_playback=lambda: stop_results.append(manager.request_stop(reason="test")),
                )
            )
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertEqual(stop_results[0].source_turn_id, "turn-source-1")
        self.assertEqual(stop_results[0].source_session_key, "session-source-1")
        self.assertTrue(source.finish_called)
        self.assertTrue(source.cleanup_called)
        self.assertEqual(FakeRawOutputStream.writes, [b"first"])
        self.assertEqual(FakeRawOutputStream.abort_count, 1)

    def test_qualified_stop_marks_only_bound_source_and_returns_content_free_context(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        source_metrics = {
            "meta": {
                "validation_session_id": "validation-1",
                "validation_step_id": "07-barge-source",
                "validation_attempt_id": "attempt-private-1",
                "private_text": "never expose this",
            }
        }
        unrelated_metrics = {"meta": {}}

        async def runner(manager: LocalTtsPlaybackManager, source: BlockingSource):
            play_task = asyncio.create_task(
                manager.play_source(
                    source,
                    turn_id="turn-source-1",
                    session_key="session-source-1",
                    metrics=source_metrics,
                )
            )
            started = await asyncio.to_thread(source.second_read_started.wait, 1.0)
            self.assertTrue(started)
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            ok = await play_task
            return ok, receipt

        try:
            manager = LocalTtsPlaybackManager(enabled=True)
            source = BlockingSource()
            with patch.object(
                local_tts_playback,
                "validation_attempt_binding_is_current",
                return_value=True,
            ) as validate:
                ok, receipt = asyncio.run(runner(manager, source))
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(ok)
        self.assertIs(source_metrics["meta"]["qualified_tts_interrupt"], True)
        self.assertNotIn("qualified_tts_interrupt", unrelated_metrics["meta"])
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.source_turn_id, "turn-source-1")
        self.assertEqual(receipt.source_session_key, "session-source-1")
        self.assertEqual(receipt.validation_session_id, "validation-1")
        self.assertEqual(receipt.validation_step_id, "07-barge-source")
        self.assertEqual(receipt.validation_attempt_id, "attempt-private-1")
        self.assertGreaterEqual(validate.call_count, 4)
        for call in validate.call_args_list:
            self.assertEqual(call.kwargs["surface"], "local")
            self.assertIs(call.kwargs["reject_unbound_when_active"], True)
        self.assertFalse(hasattr(receipt, "metrics"))
        self.assertNotIn("sourceTurn", manager.snapshot())

    def test_qualified_stop_lease_rejects_later_sentence_for_same_metrics(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner(manager: LocalTtsPlaybackManager):
            first = BlockingSource()
            first_task = asyncio.create_task(manager.play_source(first, metrics=metrics))
            started = await asyncio.to_thread(first.second_read_started.wait, 1.0)
            self.assertTrue(started)
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            first_ok = await first_task
            second = FakeSource([b"must-not-play"])
            second_ok = await manager.play_source(second, metrics=metrics)
            return first_ok, second, second_ok, receipt

        try:
            manager = LocalTtsPlaybackManager(enabled=True)
            first_ok, second, second_ok, receipt = asyncio.run(runner(manager))
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(first_ok)
        self.assertIsNotNone(receipt)
        self.assertFalse(second_ok)
        self.assertTrue(second.cleanup_called)
        self.assertNotIn(b"must-not-play", FakeRawOutputStream.writes)

    def test_qualified_stop_before_first_device_write_has_no_evidence(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(enabled=True)
            source = PrePlaybackBlockingSource()
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            started = await asyncio.to_thread(source.read_started.wait, 1.0)
            self.assertTrue(started)
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            played = await play_task
            return receipt, played, source

        try:
            receipt, played, source = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNone(receipt)
        self.assertFalse(played)
        self.assertTrue(source.finish_called)
        self.assertEqual(FakeRawOutputStream.writes, [])
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    def test_qualified_stop_abort_failure_rolls_back_evidence(self) -> None:
        class AbortFailureStream(FakeRawOutputStream):
            def abort(self) -> None:
                raise OSError("private-device-detail")

        class AbortFailureSoundDevice:
            RawOutputStream = AbortFailureStream

        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = AbortFailureSoundDevice()
        metrics = {"meta": {}}
        logs: list[str] = []

        async def runner():
            manager = LocalTtsPlaybackManager(enabled=True, log=logs.append)
            source = BlockingSource()
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            started = await asyncio.to_thread(source.second_read_started.wait, 1.0)
            self.assertTrue(started)
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            played = await play_task
            return receipt, played

        try:
            receipt, played = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNone(receipt)
        self.assertTrue(played)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])
        self.assertNotIn("private-device-detail", "\n".join(logs))
        self.assertIn("errorType=OSError", "\n".join(logs))

    def test_qualified_stop_close_failure_rolls_back_evidence(self) -> None:
        class CloseFailureStream(FakeRawOutputStream):
            def __exit__(self, exc_type, exc, tb) -> None:
                super().__exit__(exc_type, exc, tb)
                raise OSError("close failed")

        class CloseFailureSoundDevice:
            RawOutputStream = CloseFailureStream

        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = CloseFailureSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(enabled=True)
            source = BlockingSource()
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            started = await asyncio.to_thread(source.second_read_started.wait, 1.0)
            self.assertTrue(started)
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            played = await play_task
            return receipt, played

        try:
            receipt, played = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNone(receipt)
        self.assertFalse(played)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    def test_qualified_stop_timeout_has_no_evidence_and_does_not_hang(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(
                enabled=True,
                stop_wait_timeout_sec=0.02,
            )
            source = BlockingSource(release_on_finish=False)
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            started = await asyncio.to_thread(source.second_read_started.wait, 1.0)
            self.assertTrue(started)
            started_at = asyncio.get_running_loop().time()
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            elapsed = asyncio.get_running_loop().time() - started_at
            source.release_read.set()
            played = await play_task
            return receipt, played, elapsed

        try:
            receipt, played, elapsed = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNone(receipt)
        self.assertTrue(played)
        self.assertLess(elapsed, 0.5)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    def test_hanging_stop_control_is_bounded_and_has_no_evidence(self) -> None:
        class HangingFinishSource(BlockingSource):
            def __init__(self) -> None:
                super().__init__(release_on_finish=False)
                self.finish_started = threading.Event()
                self.release_finish = threading.Event()

            def finish(self) -> None:
                self.finish_called = True
                self.finish_started.set()
                self.release_finish.wait(timeout=2.0)
                self.release_read.set()

        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(
                enabled=True,
                stop_wait_timeout_sec=0.02,
            )
            source = HangingFinishSource()
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            self.assertTrue(
                await asyncio.to_thread(source.second_read_started.wait, 1.0)
            )
            started_at = asyncio.get_running_loop().time()
            receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            elapsed = asyncio.get_running_loop().time() - started_at
            self.assertTrue(source.finish_started.is_set())
            source.release_finish.set()
            played = await play_task
            return receipt, played, elapsed

        try:
            receipt, played, elapsed = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNone(receipt)
        self.assertTrue(played)
        self.assertLess(elapsed, 0.5)
        self.assertNotIn("qualified_tts_interrupt", metrics["meta"])

    def test_qualified_stop_commits_once_and_stale_binding_cannot_stop_replacement(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        first_metrics = {"meta": {}}
        second_metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(enabled=True)
            first = BlockingSource()
            first_task = asyncio.create_task(
                manager.play_source(first, metrics=first_metrics)
            )
            self.assertTrue(
                await asyncio.to_thread(first.second_read_started.wait, 1.0)
            )
            with manager._state_lock:
                stale_binding = manager._active_binding
            first_receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )
            await first_task
            duplicate_receipt = await manager.request_stop_and_wait(
                reason="qualified_user_audio"
            )

            second = BlockingSource()
            second_task = asyncio.create_task(
                manager.play_source(second, metrics=second_metrics)
            )
            self.assertTrue(
                await asyncio.to_thread(second.second_read_started.wait, 1.0)
            )
            stale_context, stale_ok, stale_token = manager._request_stop_for_binding(
                stale_binding,
                reason="qualified_user_audio",
            )
            with manager._state_lock:
                replacement_was_stopped = bool(
                    manager._active_binding and manager._active_binding.stop_requested
                )
            manager.request_stop(reason="test_cleanup")
            await second_task
            return (
                first_receipt,
                duplicate_receipt,
                stale_context,
                stale_ok,
                stale_token,
                replacement_was_stopped,
            )

        try:
            result = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        (
            first_receipt,
            duplicate_receipt,
            stale_context,
            stale_ok,
            stale_token,
            replacement_stopped,
        ) = result
        self.assertIsNotNone(first_receipt)
        self.assertIsNone(duplicate_receipt)
        self.assertIsNone(stale_context)
        self.assertFalse(stale_ok)
        self.assertIsNone(stale_token)
        self.assertFalse(replacement_stopped)
        self.assertIs(first_metrics["meta"]["qualified_tts_interrupt"], True)
        self.assertNotIn("qualified_tts_interrupt", second_metrics["meta"])

    def test_late_concurrent_timeout_cannot_erase_successful_interrupt_lease(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(
                enabled=True,
                stop_wait_timeout_sec=0.1,
            )
            source = BlockingSource()
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            self.assertTrue(
                await asyncio.to_thread(source.second_read_started.wait, 1.0)
            )
            original_bounded_stop = manager._request_stop_for_binding_bounded
            invocation_count = 0

            async def ordered_bounded_stop(binding, *, reason, deadline):
                nonlocal invocation_count
                invocation_count += 1
                if invocation_count == 1:
                    return await original_bounded_stop(
                        binding,
                        reason=reason,
                        deadline=deadline,
                    )
                await asyncio.sleep(0.12)
                return None

            manager._request_stop_for_binding_bounded = ordered_bounded_stop
            successful, late_timeout = await asyncio.gather(
                manager.request_stop_and_wait(reason="qualified_user_audio"),
                manager.request_stop_and_wait(reason="qualified_user_audio"),
            )
            await play_task
            return successful, late_timeout

        try:
            successful, late_timeout = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertIsNotNone(successful)
        self.assertIsNone(late_timeout)
        self.assertIs(metrics["meta"]["qualified_tts_interrupt"], True)

    def test_concurrent_interrupts_receive_one_preterminal_stop_acceptance(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        local_tts_playback.sd = FakeSoundDevice()
        metrics = {"meta": {}}

        async def runner():
            manager = LocalTtsPlaybackManager(enabled=True)
            source = BlockingSource()
            play_task = asyncio.create_task(manager.play_source(source, metrics=metrics))
            self.assertTrue(
                await asyncio.to_thread(source.second_read_started.wait, 1.0)
            )
            receipts = await asyncio.gather(
                manager.request_stop_and_wait(reason="qualified_user_audio"),
                manager.request_stop_and_wait(reason="qualified_user_audio"),
            )
            await play_task
            return receipts

        try:
            receipts = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertEqual(sum(receipt is not None for receipt in receipts), 1)
        self.assertIs(metrics["meta"]["qualified_tts_interrupt"], True)

    def test_cancellation_waits_for_exact_worker_before_releasing_playback_lock(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        FakeRawOutputStream.active_count = 0
        FakeRawOutputStream.max_active_count = 0
        local_tts_playback.sd = FakeSoundDevice()

        async def runner() -> tuple[BlockingSource, bool, dict]:
            manager = LocalTtsPlaybackManager(enabled=True)
            first = BlockingSource()
            first_task = asyncio.create_task(
                manager.play_source(first, turn_id="turn-a")
            )
            started = await asyncio.to_thread(
                first.second_read_started.wait,
                1.0,
            )
            self.assertTrue(started)
            first_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first_task
            second = FakeSource([b"second"])
            second_ok = await manager.play_source(second, turn_id="turn-b")
            return first, second_ok, manager.snapshot()

        try:
            first, second_ok, snapshot = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(first.finish_called)
        self.assertTrue(first.worker_exited.is_set())
        self.assertTrue(second_ok)
        self.assertEqual(FakeRawOutputStream.max_active_count, 1)
        self.assertFalse(snapshot["active"])

    def test_repeated_cancellation_cannot_release_binding_before_worker_exit(self) -> None:
        original_sd = local_tts_playback.sd
        FakeRawOutputStream.writes = []
        FakeRawOutputStream.active_count = 0
        FakeRawOutputStream.max_active_count = 0
        local_tts_playback.sd = FakeSoundDevice()

        async def runner() -> BlockingSource:
            manager = LocalTtsPlaybackManager(enabled=True)
            source = BlockingSource(release_on_finish=False)
            task = asyncio.create_task(
                manager.play_source(source, turn_id="turn-repeat-cancel")
            )
            started = await asyncio.to_thread(source.second_read_started.wait, 1.0)
            self.assertTrue(started)

            task.cancel()
            stop_requested = await asyncio.to_thread(source.finish_requested.wait, 1.0)
            self.assertTrue(stop_requested)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertTrue(manager.snapshot()["active"])

            source.release_read.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return source

        try:
            source = asyncio.run(runner())
        finally:
            local_tts_playback.sd = original_sd

        self.assertTrue(source.worker_exited.is_set())
        self.assertEqual(FakeRawOutputStream.active_count, 0)


if __name__ == "__main__":
    unittest.main()
