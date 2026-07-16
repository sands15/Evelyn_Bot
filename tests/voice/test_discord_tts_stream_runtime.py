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

from evelyn_core.discord_tts_stream_runtime import (  # noqa: E402
    DiscordTtsStreamRuntimeDeps,
    stream_tts_sentences_from_runtime,
)


class FakeLock:
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
    def __init__(self) -> None:
        self.request = None
        self.error: Exception | None = None
        self.trigger_failures = False

    async def stream_sentences(self, request) -> None:
        self.request = request
        if self.error is not None:
            raise self.error
        source = await request.synthesize_source("안녕", 2)
        callbacks = source.callbacks
        callbacks["on_request_start"]()
        callbacks["on_response_headers"]()
        callbacks["on_first_byte"]()
        callbacks["on_first_frame"]()
        callbacks["on_first_packet_sent"]()
        request.check_cancelled()
        if self.trigger_failures:
            request.on_prefetch_failure(RuntimeError("prefetch"))
            request.on_prepared_failure(RuntimeError("prepared"))


class DiscordTtsStreamRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = FakePlaybackManager()
        self.created: list[tuple[str, dict, object]] = []
        self.detached: list[tuple[object, object]] = []
        self.stages: list[tuple[tuple, dict]] = []
        self.latencies: list[tuple] = []
        self.events: list[tuple[str, dict]] = []
        self.failures: list[tuple[tuple, dict]] = []

    async def create_source(self, text: str, **kwargs):
        source = SimpleNamespace(text=text, callbacks=kwargs)
        self.created.append((text, kwargs, source))
        return source

    def build_deps(self) -> DiscordTtsStreamRuntimeDeps:
        return DiscordTtsStreamRuntimeDeps(
            attach_current_task=lambda scope: ("attached", scope),
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            tts_running_state="tts-running",
            create_omnivoice_source=self.create_source,
            mark_turn_stage=lambda *args, **kwargs: self.stages.append((args, kwargs)),
            log_voice_latency=lambda *args: self.latencies.append(args),
            log_turn_event=lambda event, **payload: self.events.append((event, payload)),
            record_voice_pipeline_failure=lambda *args, **kwargs: self.failures.append((args, kwargs)),
            tts_lock=FakeLock(),
            playback_manager=self.manager,
            streaming_playback_request_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            omnivoice_timeout_sec=30.0,
            tts_prefetch_chunks=2,
            playback_start_lookahead_chunks=1,
            playback_start_lookahead_timeout_ms=250,
            create_turn_scoped_task=lambda coro, **_kwargs: asyncio.create_task(coro),
            log=lambda _message: None,
        )

    async def test_stream_builds_request_runs_callbacks_and_detaches_scope(self) -> None:
        scope = FakeScope()
        vc = SimpleNamespace(guild=SimpleNamespace(id=77))
        metrics = {"marks": {}}

        await stream_tts_sentences_from_runtime(
            vc,
            asyncio.Queue(),
            deps=self.build_deps(),
            metrics=metrics,
            turn_id="turn-1",
            session_key="session-1",
            turn_scope=scope,
        )

        request = self.manager.request
        self.assertEqual(request.guild_id, 77)
        self.assertEqual(request.turn_id, "turn-1")
        self.assertEqual(request.prefetch_chunks, 2)
        self.assertEqual(request.lookahead_timeout_ms, 250)
        self.assertEqual(self.created[0][0], "안녕")
        self.assertEqual(self.created[0][1]["chunk_index"], 2)
        self.assertEqual(scope.transitions, [("tts-running", "stream_tts_sentences")])
        self.assertEqual(scope.cancel_checks, 1)
        self.assertEqual(self.events[0], (
            "first_packet_sent",
            {"turn_id": "turn-1", "chunk_index": 2, "session_key": "session-1"},
        ))
        self.assertEqual(self.detached, [(scope, ("attached", scope))])

    async def test_prefetch_and_prepared_failures_keep_distinct_stages(self) -> None:
        self.manager.trigger_failures = True

        await stream_tts_sentences_from_runtime(
            SimpleNamespace(guild=None),
            asyncio.Queue(),
            deps=self.build_deps(),
        )

        self.assertEqual([kwargs["stage"] for _args, kwargs in self.failures], ["prefetch", "prepared_exception"])
        self.assertTrue(all(args[0] == "tts_playback_failed" for args, _kwargs in self.failures))

    async def test_playback_exception_still_detaches_task(self) -> None:
        self.manager.error = RuntimeError("playback failed")
        scope = FakeScope()

        with self.assertRaisesRegex(RuntimeError, "playback failed"):
            await stream_tts_sentences_from_runtime(
                SimpleNamespace(guild=None),
                asyncio.Queue(),
                deps=self.build_deps(),
                turn_scope=scope,
            )

        self.assertEqual(len(self.detached), 1)
        self.assertIs(self.detached[0][0], scope)

    def test_main_delegates_discord_tts_stream_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def stream_tts_sentences(")
        end = source.index("    async def speak_answer_local(", start)
        function_source = source[start:end]

        self.assertIn("stream_tts_sentences_from_runtime(", function_source)
        self.assertNotIn("TtsStreamingPlaybackRequest(", function_source)
        self.assertNotIn("async def synthesize_source", function_source)


if __name__ == "__main__":
    unittest.main()
