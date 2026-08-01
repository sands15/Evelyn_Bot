from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_tts_stream_runtime import (  # noqa: E402
    LocalTtsStreamRuntimeDeps,
    stream_local_tts_sentences_from_runtime,
)


class FakeAsyncLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeScope:
    def __init__(self) -> None:
        self.transitions: list[tuple[object, str]] = []
        self.cancel_checks = 0

    def transition(self, state, *, reason: str) -> None:
        self.transitions.append((state, reason))

    def raise_if_cancelled(self) -> None:
        self.cancel_checks += 1


class FakePlaybackManager:
    def __init__(self, *, enabled: bool = True, error: Exception | None = None) -> None:
        self.enabled = enabled
        self.error = error
        self.played: list[object] = []
        self.calls: list[dict] = []
        self.stop_after_first = False

    async def play_source(self, source, **kwargs):
        self.played.append(source)
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        kwargs["on_first_playback"]()
        if self.stop_after_first:
            kwargs["metrics"].setdefault("meta", {})["qualified_tts_interrupt"] = True
        return True


class LocalTtsStreamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = FakePlaybackManager()
        self.created_sources: list[tuple[str, dict, object]] = []
        self.tasks: list[asyncio.Task] = []
        self.detached: list[tuple[object, object]] = []
        self.failures: list[tuple[tuple, dict]] = []
        self.stages: list[tuple[tuple, dict]] = []
        self.latencies: list[tuple] = []
        self.events: list[tuple[str, dict]] = []
        self.first_playbacks: list[tuple[tuple, dict]] = []
        self.cleaned: list[object] = []

    async def create_source(self, text: str, **kwargs):
        source = SimpleNamespace(text=text)
        self.created_sources.append((text, kwargs, source))
        return source

    def create_task(self, coro, *, turn_scope=None):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def prefetch_success(self, _sentence_queue, prepared_queue, *, synthesize_source, **_kwargs) -> None:
        source = await synthesize_source("[question-oh] 안녕", 2)
        callbacks = self.created_sources[-1][1]
        callbacks["on_request_start"]()
        callbacks["on_response_headers"]()
        callbacks["on_first_byte"]()
        callbacks["on_first_frame"]()
        callbacks["on_first_packet_sent"]()
        await prepared_queue.put((2, source))
        await prepared_queue.put(None)

    def build_deps(self, *, prefetch=None) -> LocalTtsStreamRuntimeDeps:
        return LocalTtsStreamRuntimeDeps(
            playback_manager=self.manager,
            attach_current_task=lambda scope: ("attached", scope),
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            tts_running_state="tts-running",
            clean_tts_text=lambda text: text.strip(),
            strip_omnivoice_tags=lambda text: text.replace("[question-oh]", ""),
            create_omnivoice_source=self.create_source,
            mark_turn_stage=lambda *args, **kwargs: self.stages.append((args, kwargs)),
            log_voice_latency=lambda *args: self.latencies.append(args),
            log_turn_event=lambda event, **payload: self.events.append((event, payload)),
            record_voice_pipeline_failure=lambda *args, **kwargs: self.failures.append((args, kwargs)),
            tts_lock=FakeAsyncLock(),
            tts_prefetch_chunks=2,
            create_turn_scoped_task=self.create_task,
            prefetch_tts_sources=prefetch or self.prefetch_success,
            omnivoice_timeout_sec=30.0,
            cleanup_prepared_tts_item=self.cleaned.append,
            mark_local_tts_first_playback=lambda *args, **kwargs: self.first_playbacks.append((args, kwargs)),
        )

    async def test_disabled_manager_returns_without_attaching_task(self) -> None:
        self.manager.enabled = False

        result = await stream_local_tts_sentences_from_runtime(asyncio.Queue(), deps=self.build_deps())

        self.assertEqual(result, 0)
        self.assertEqual(self.tasks, [])
        self.assertEqual(self.detached, [])

    async def test_streams_synthesized_sentence_and_records_stage_callbacks(self) -> None:
        scope = FakeScope()
        metrics = {"marks": {}}

        result = await stream_local_tts_sentences_from_runtime(
            asyncio.Queue(),
            deps=self.build_deps(),
            metrics=metrics,
            turn_id="turn-1",
            session_key="session-1",
            turn_scope=scope,
        )

        self.assertEqual(result, 1)
        self.assertEqual(self.created_sources[0][0], "안녕")
        self.assertEqual(self.created_sources[0][1]["chunk_index"], 2)
        self.assertEqual(self.created_sources[0][1]["trace_payload"]["output_mode"], "local_speaker")
        self.assertEqual(scope.transitions, [("tts-running", "local_speaker_stream_tts")])
        self.assertGreaterEqual(scope.cancel_checks, 2)
        self.assertEqual(self.manager.played, [self.created_sources[0][2]])
        self.assertEqual(self.manager.calls[0]["turn_id"], "turn-1")
        self.assertEqual(self.manager.calls[0]["session_key"], "session-1")
        self.assertIs(self.manager.calls[0]["metrics"], metrics)
        self.assertEqual(self.first_playbacks[0][1]["chunk_index"], 2)
        self.assertEqual(self.events[0], (
            "local_tts_first_packet_sent",
            {"turn_id": "turn-1", "chunk_index": 2, "session_key": "session-1"},
        ))
        self.assertEqual(self.detached, [(scope, ("attached", scope))])
        self.assertEqual(self.failures, [])

    async def test_qualified_stop_lease_prevents_next_prepared_sentence(self) -> None:
        self.manager.stop_after_first = True
        metrics = {"meta": {}}

        async def prefetch(_sentence_queue, prepared_queue, *, synthesize_source, **_kwargs) -> None:
            first = await synthesize_source("첫째", 1)
            second = await synthesize_source("둘째", 2)
            await prepared_queue.put((1, first))
            await prepared_queue.put((2, second))
            await prepared_queue.put(None)

        result = await stream_local_tts_sentences_from_runtime(
            asyncio.Queue(),
            deps=self.build_deps(prefetch=prefetch),
            metrics=metrics,
            turn_id="turn-source-1",
            session_key="session-source-1",
        )

        self.assertEqual(result, 1)
        self.assertEqual(self.manager.played, [self.created_sources[0][2]])
        self.assertIn((2, self.created_sources[1][2]), self.cleaned)

    async def test_playback_failure_is_recorded_and_leftovers_are_cleaned(self) -> None:
        self.manager.error = RuntimeError("speaker failed")

        async def prefetch(_sentence_queue, prepared_queue, *, synthesize_source, **_kwargs) -> None:
            first = await synthesize_source("첫째", 1)
            second = await synthesize_source("둘째", 2)
            await prepared_queue.put((1, first))
            await prepared_queue.put((2, second))
            await prepared_queue.put(None)

        with self.assertRaisesRegex(RuntimeError, "speaker failed"):
            await stream_local_tts_sentences_from_runtime(asyncio.Queue(), deps=self.build_deps(prefetch=prefetch))

        self.assertEqual(self.failures[0][0][0], "tts_playback_failed")
        self.assertEqual(self.failures[0][1]["stage"], "local_speaker_stream")
        self.assertIn((2, self.created_sources[1][2]), self.cleaned)
        self.assertEqual(len(self.detached), 1)

    async def test_prefetch_failure_callback_preserves_failure_stage(self) -> None:
        error = RuntimeError("synthesis failed")

        async def prefetch(_sentence_queue, prepared_queue, *, on_failure, **_kwargs) -> None:
            on_failure(error)
            await prepared_queue.put(error)

        with self.assertRaisesRegex(RuntimeError, "synthesis failed"):
            await stream_local_tts_sentences_from_runtime(
                asyncio.Queue(),
                deps=self.build_deps(prefetch=prefetch),
                turn_id="turn-2",
                session_key="session-2",
            )

        self.assertEqual(self.failures[0][0][:2], ("tts_request_failed", error))
        self.assertEqual(self.failures[0][1]["stage"], "local_speaker_stream_prefetch")
        self.assertEqual(self.detached[0][0], None)

    def test_main_delegates_local_stream_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def stream_local_tts_sentences(")
        end = source.index("    def split_first_response_and_followup(", start)
        function_source = source[start:end]

        self.assertIn("stream_local_tts_sentences_from_runtime(", function_source)
        self.assertNotIn("prefetch_tts_sources(", function_source)
        self.assertNotIn("async with tts_lock", function_source)


if __name__ == "__main__":
    unittest.main()
