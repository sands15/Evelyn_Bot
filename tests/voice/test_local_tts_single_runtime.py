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
    LocalTtsSingleRuntimeDeps,
    speak_answer_local_from_runtime,
)


class FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeScope:
    def __init__(self) -> None:
        self.transitions: list[tuple[object, str]] = []

    def transition(self, state, *, reason: str) -> None:
        self.transitions.append((state, reason))


class FakeSource:
    def __init__(self, callbacks: dict) -> None:
        self.callbacks = callbacks
        self.ready_timeouts: list[float] = []

    async def wait_until_ready(self, *, timeout: float) -> None:
        self.ready_timeouts.append(timeout)


class FakePlaybackManager:
    def __init__(self) -> None:
        self.enabled = True
        self.error: BaseException | None = None
        self.calls: list[tuple[object, bool]] = []

    async def play_source(self, source, *, cleanup_source: bool, on_first_playback):
        self.calls.append((source, cleanup_source))
        if self.error is not None:
            raise self.error
        on_first_playback()
        return True


class LocalTtsSingleRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = FakePlaybackManager()
        self.created: list[tuple[str, dict, FakeSource]] = []
        self.detached: list[tuple[object, object]] = []
        self.stages: list[tuple[tuple, dict]] = []
        self.latencies: list[tuple] = []
        self.events: list[tuple[str, dict]] = []
        self.first_playbacks: list[dict] = []
        self.failures: list[tuple[tuple, dict]] = []

    async def create_source(self, text: str, **kwargs):
        source = FakeSource(kwargs)
        self.created.append((text, kwargs, source))
        return source

    def build_deps(self, *, clean_text=lambda text: text.strip()) -> LocalTtsSingleRuntimeDeps:
        return LocalTtsSingleRuntimeDeps(
            playback_manager=self.manager,
            clean_tts_text=clean_text,
            strip_omnivoice_tags=lambda text: text.replace("[question-oh]", "").strip(),
            attach_current_task=lambda scope: ("attached", scope),
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            tts_running_state="tts-running",
            tts_lock=FakeLock(),
            create_omnivoice_source=self.create_source,
            mark_turn_stage=lambda *args, **kwargs: self.stages.append((args, kwargs)),
            log_voice_latency=lambda *args: self.latencies.append(args),
            log_turn_event=lambda event, **payload: self.events.append((event, payload)),
            mark_local_tts_first_playback=lambda *_args, **kwargs: self.first_playbacks.append(kwargs),
            record_voice_pipeline_failure=lambda *args, **kwargs: self.failures.append((args, kwargs)),
            omnivoice_timeout_sec=30.0,
        )

    async def test_disabled_manager_returns_without_attaching(self) -> None:
        self.manager.enabled = False

        result = await speak_answer_local_from_runtime("안녕", deps=self.build_deps())

        self.assertFalse(result)
        self.assertEqual(self.created, [])
        self.assertEqual(self.detached, [])

    async def test_empty_cleaned_text_returns_without_attaching(self) -> None:
        result = await speak_answer_local_from_runtime(
            "[question-oh]",
            deps=self.build_deps(clean_text=lambda text: text.replace("[question-oh]", "").strip()),
        )

        self.assertFalse(result)
        self.assertEqual(self.created, [])
        self.assertEqual(self.detached, [])

    async def test_plays_ready_source_and_runs_trace_callbacks(self) -> None:
        scope = FakeScope()

        result = await speak_answer_local_from_runtime(
            "[question-oh] 안녕",
            deps=self.build_deps(),
            turn_id="turn-1",
            session_key="session-1",
            turn_scope=scope,
            metrics={"marks": {}},
        )
        callbacks = self.created[0][1]
        callbacks["on_request_start"]()
        callbacks["on_response_headers"]()
        callbacks["on_first_byte"]()
        callbacks["on_first_frame"]()
        callbacks["on_first_packet_sent"]()

        self.assertTrue(result)
        self.assertEqual(self.created[0][0], "안녕")
        self.assertEqual(self.created[0][2].ready_timeouts, [30.0])
        self.assertTrue(self.manager.calls[0][1])
        self.assertEqual(scope.transitions, [("tts-running", "local_speaker_tts")])
        self.assertEqual(self.first_playbacks[0]["chunk_index"], 1)
        self.assertEqual(self.events[0][0], "local_tts_first_packet_sent")
        self.assertEqual(self.detached, [(scope, ("attached", scope))])

    async def test_playback_failure_returns_false_records_stage_and_detaches(self) -> None:
        self.manager.error = RuntimeError("speaker failed")

        result = await speak_answer_local_from_runtime("안녕", deps=self.build_deps(), turn_id="turn-2")

        self.assertFalse(result)
        self.assertEqual(self.failures[0][0][0], "tts_playback_failed")
        self.assertEqual(self.failures[0][1]["stage"], "local_speaker")
        self.assertEqual(len(self.detached), 1)

    async def test_cancelled_playback_propagates_and_detaches_without_failure_record(self) -> None:
        self.manager.error = asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await speak_answer_local_from_runtime("안녕", deps=self.build_deps())

        self.assertEqual(self.failures, [])
        self.assertEqual(len(self.detached), 1)

    def test_main_delegates_local_single_playback_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("async def speak_answer_local(")
        end = source.index("def _cleanup_prepared_tts_item(", start)
        function_source = source[start:end]

        self.assertIn("speak_answer_local_from_runtime(", function_source)
        self.assertNotIn("create_omnivoice_source(", function_source)
        self.assertNotIn("play_source(", function_source)


if __name__ == "__main__":
    unittest.main()
