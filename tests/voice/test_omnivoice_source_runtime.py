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

from evelyn_core.omnivoice_request_runtime import OmniVoiceRequestRuntimeDeps  # noqa: E402
from evelyn_core.observability_metrics import VoiceLatencyTrace  # noqa: E402
from evelyn_core.omnivoice_source_runtime import (  # noqa: E402
    OmniVoiceSourceRuntimeDeps,
    create_omnivoice_source_from_runtime,
)


class FakeContent:
    def __init__(self, chunks: list[bytes], *, cancel: bool = False) -> None:
        self.chunks = chunks
        self.cancel = cancel

    async def iter_chunked(self, _size: int):
        if self.cancel:
            raise asyncio.CancelledError
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status: int, chunks: list[bytes] | None = None, text: str = "", *, cancel: bool = False) -> None:
        self.status = status
        self.content = FakeContent(chunks or [], cancel=cancel)
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def text(self) -> str:
        return self._text


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts: list[dict] = []

    def post(self, url: str, *, json: dict, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


class FakeSource:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.pcm: list[bytes] = []
        self.finished = False
        self.cleaned = False
        self.error: BaseException | None = None

    def feed_pcm24_mono(self, chunk: bytes) -> None:
        self.pcm.append(chunk)

    def finish(self) -> None:
        self.finished = True

    def cleanup(self) -> None:
        self.cleaned = True

    def fail(self, error: BaseException) -> None:
        self.error = error


class OmniVoiceSourceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = FakeSession([FakeResponse(200, [b"pcm-a", b"pcm-b"])])
        self.tasks: list[asyncio.Task] = []
        self.events: list[tuple[str, dict]] = []
        self.failures: list[tuple[tuple, dict]] = []
        self.logs: list[str] = []
        self.clock = 10.0

    def monotonic(self) -> float:
        self.clock += 0.05
        return self.clock

    @staticmethod
    def merge_payload(*, explicit: dict, extra: dict | None = None) -> dict:
        merged = dict(extra or {})
        for key in explicit:
            merged.pop(key, None)
        merged.update(explicit)
        return merged

    def request_deps(self) -> OmniVoiceRequestRuntimeDeps:
        return OmniVoiceRequestRuntimeDeps(
            request_id_suffix=lambda: "suffix",
            tts_synth_request_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            tts_synth_result_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            omnivoice_model="omni",
            omnivoice_pcm_rate=24000,
            omnivoice_stream=True,
            omnivoice_num_step=16,
            omnivoice_speed=1.0,
            omnivoice_language="ko",
        )

    def create_task(self, coro, *, turn_scope=None):
        task = asyncio.create_task(coro)
        task.turn_scope = turn_scope
        self.tasks.append(task)
        return task

    def build_deps(self, *, voice: str = "auto") -> OmniVoiceSourceRuntimeDeps:
        return OmniVoiceSourceRuntimeDeps(
            clean_tts_text=lambda text: text.strip(),
            merge_log_event_payload=self.merge_payload,
            source_factory=FakeSource,
            get_http_session=lambda: asyncio.sleep(0, result=self.session),
            client_timeout_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            omnivoice_timeout_sec=30.0,
            omnivoice_server_url="http://tts",
            omnivoice_voice=voice,
            request_runtime_deps_factory=self.request_deps,
            monotonic=self.monotonic,
            log_turn_event=lambda event, **payload: self.events.append((event, payload)),
            record_voice_pipeline_failure=lambda *args, **kwargs: self.failures.append((args, kwargs)),
            create_turn_scoped_task=self.create_task,
            log=self.logs.append,
        )

    async def test_rejects_empty_text_before_starting_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "TTS 텍스트가 비어"):
            await create_omnivoice_source_from_runtime("   ", deps=self.build_deps())

        self.assertEqual(self.tasks, [])
        self.assertEqual(self.session.posts, [])

    async def test_streams_pcm_finishes_source_and_emits_trace_callbacks(self) -> None:
        callbacks: list[str] = []
        turn_scope = object()
        latency_trace = VoiceLatencyTrace()

        source = await create_omnivoice_source_from_runtime(
            " 안녕 ",
            deps=self.build_deps(),
            on_task_started=lambda: callbacks.append("task"),
            on_request_start=lambda: callbacks.append("request"),
            on_response_headers=lambda: callbacks.append("headers"),
            on_first_byte=lambda: callbacks.append("first_byte"),
            on_first_frame=lambda: callbacks.append("first_frame"),
            on_first_packet_sent=lambda: callbacks.append("first_packet"),
            turn_id="turn-1",
            chunk_index=3,
            session_key="session-1",
            turn_scope=turn_scope,
            trace_payload={"turn_id": "wrong", "source_type": "test"},
            latency_trace=latency_trace,
        )
        await self.tasks[0]

        self.assertEqual(source.pcm, [b"pcm-a", b"pcm-b"])
        self.assertTrue(source.finished)
        self.assertIsNone(source.error)
        self.assertEqual(callbacks, ["task", "request", "headers", "first_byte"])
        self.assertIs(self.tasks[0].turn_scope, turn_scope)
        self.assertEqual(source.kwargs["trace_payload"]["turn_id"], "turn-1")
        self.assertEqual(source.kwargs["trace_payload"]["source_type"], "test")
        self.assertEqual(self.session.posts[0]["url"], "http://tts/v1/audio/speech")
        self.assertEqual(self.session.posts[0]["json"]["input"], "안녕")
        self.assertEqual(self.session.posts[0]["json"]["turn_id"], "turn-1")
        self.assertEqual(self.session.posts[0]["json"]["session_key"], "session-1")
        self.assertEqual([event for event, _payload in self.events], [
            "playback_task_started",
            "tts_request_started",
            "tts_first_pcm_received",
        ])
        self.assertEqual(self.failures, [])
        self.assertEqual(
            set(latency_trace.public_summary()["markers_ms"]),
            {"tts_requested", "tts_started", "tts_first_pcm"},
        )

    async def test_empty_audio_never_marks_first_pcm(self) -> None:
        self.session = FakeSession([FakeResponse(200, [])])
        latency_trace = VoiceLatencyTrace()

        source = await create_omnivoice_source_from_runtime(
            "안녕",
            deps=self.build_deps(),
            latency_trace=latency_trace,
        )
        await self.tasks[0]

        self.assertIsNotNone(source.error)
        self.assertEqual(
            set(latency_trace.public_summary()["markers_ms"]),
            {"tts_requested", "tts_started"},
        )

    async def test_clone_http_failure_retries_auto(self) -> None:
        self.session = FakeSession([
            FakeResponse(404, text="clone missing"),
            FakeResponse(200, [b"fallback-pcm"]),
        ])

        source = await create_omnivoice_source_from_runtime("안녕", deps=self.build_deps(voice="clone:evelyn"))
        await self.tasks[0]

        self.assertEqual([post["json"]["voice"] for post in self.session.posts], ["clone:evelyn", "auto"])
        self.assertEqual(source.pcm, [b"fallback-pcm"])
        self.assertTrue(source.finished)
        self.assertIn("clone voice 실패", self.logs[0])

    async def test_final_http_failure_marks_source_failed(self) -> None:
        self.session = FakeSession([FakeResponse(503, text="not ready")])

        source = await create_omnivoice_source_from_runtime("안녕", deps=self.build_deps())
        await self.tasks[0]

        self.assertIsInstance(source.error, RuntimeError)
        self.assertFalse(source.finished)
        self.assertEqual(self.failures[0][0][0], "tts_request_failed")
        self.assertEqual(self.failures[0][1]["turn_id"], None)

    async def test_cancelled_producer_records_failure_and_cleans_source(self) -> None:
        self.session = FakeSession([FakeResponse(200, cancel=True)])

        source = await create_omnivoice_source_from_runtime("안녕", deps=self.build_deps())
        with self.assertRaises(asyncio.CancelledError):
            await self.tasks[0]

        self.assertTrue(source.cleaned)
        self.assertFalse(source.finished)
        self.assertEqual(self.failures[0][0][:2], ("tts_producer_cancelled", "cancelled"))

    def test_main_delegates_source_creation_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_support_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def create_omnivoice_source(")
        end = source.index("    def transcribe_audio16k_sync(", start)
        function_source = source[start:end]

        self.assertIn("create_omnivoice_source_from_runtime(", function_source)
        self.assertNotIn("session.post(", function_source)
        self.assertNotIn("async def producer", function_source)


if __name__ == "__main__":
    unittest.main()
