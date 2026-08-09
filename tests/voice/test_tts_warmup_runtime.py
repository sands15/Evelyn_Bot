from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.tts_warmup_runtime import TtsWarmupRuntimeDeps, warmup_tts_server_from_runtime  # noqa: E402


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status: int, text: str = "", chunks: list[bytes] | None = None) -> None:
        self.status = status
        self._text = text
        self.text_calls = 0
        self.content = FakeContent(chunks or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def text(self) -> str:
        self.text_calls += 1
        return self._text


class FakeSession:
    def __init__(self, *, health: FakeResponse, post: FakeResponse | None = None) -> None:
        self.health = health
        self.post_response = post
        self.posts: list[dict[str, Any]] = []

    def get(self, url: str, *, timeout: Any) -> FakeResponse:
        self.health.url = url
        self.health.timeout = timeout
        return self.health

    def post(self, url: str, *, json: dict[str, Any], timeout: Any) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if self.post_response is None:
            raise AssertionError("unexpected post")
        return self.post_response


class TtsWarmupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        session: FakeSession,
        *,
        generate: str = "false",
        marks: list[tuple[str, str, str]] | None = None,
        logs: list[str] | None = None,
    ) -> TtsWarmupRuntimeDeps:
        marks = marks if marks is not None else []
        logs = logs if logs is not None else []

        def mark(key: str, status: str, detail: str = "") -> None:
            marks.append((key, status, detail))

        return TtsWarmupRuntimeDeps(
            get_http_session=lambda: self.async_value(session),
            client_timeout=lambda **kwargs: kwargs,
            mark_startup_component=mark,
            startup_component_done=lambda key: any(item[0] == key and item[1] == "done" for item in marks),
            omnivoice_server_url="http://tts",
            omnivoice_model="model",
            omnivoice_voice="voice",
            omnivoice_language="ko",
            getenv=lambda _key, _default: generate,
            log=lambda message: logs.append(str(message)),
        )

    async def async_value(self, value: Any) -> Any:
        return value

    async def test_health_check_only_marks_done_without_generation(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(health=FakeResponse(200))

        await warmup_tts_server_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(marks[0], ("tts_warmup", "running", "OmniVoice health check"))
        self.assertEqual(marks[-1], ("tts_warmup", "done", "health check only"))
        self.assertEqual(session.posts, [])

    async def test_generation_posts_warmup_payload_and_marks_done_on_first_chunk(self) -> None:
        marks: list[tuple[str, str, str]] = []
        logs: list[str] = []
        session = FakeSession(health=FakeResponse(200), post=FakeResponse(200, chunks=[b"pcm"]))

        await warmup_tts_server_from_runtime(
            deps=self.build_deps(session, generate="true", marks=marks, logs=logs)
        )

        self.assertEqual(session.posts[0]["url"], "http://tts/v1/audio/speech")
        self.assertEqual(session.posts[0]["json"]["voice"], "voice")
        self.assertEqual(session.posts[0]["json"]["language"], "ko")
        self.assertIn(("tts_warmup", "done", ""), marks)
        self.assertIn("OmniVoice TTS 워밍업 완료", logs)

    async def test_health_failure_marks_failed_and_raises(self) -> None:
        private_error = "PRIVATE_TTS_HEALTH_BODY:/synthetic/server-token.json"
        marks: list[tuple[str, str, str]] = []
        response = FakeResponse(503, text=private_error)
        session = FakeSession(health=response)

        with self.assertRaises(RuntimeError) as raised:
            await warmup_tts_server_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(str(raised.exception), "OmniVoice health check failed")
        self.assertEqual(marks[-1], ("tts_warmup", "failed", "tts_warmup_failed"))
        self.assertEqual(response.text_calls, 0)
        self.assertNotIn(private_error, repr(marks))
        self.assertNotIn(private_error, repr(raised.exception))

    async def test_generation_failure_does_not_read_response_body(self) -> None:
        private_error = "PRIVATE_TTS_GENERATE_BODY:/synthetic/voice-token.json"
        marks: list[tuple[str, str, str]] = []
        response = FakeResponse(500, text=private_error)
        session = FakeSession(health=FakeResponse(200), post=response)

        with self.assertRaises(RuntimeError) as raised:
            await warmup_tts_server_from_runtime(
                deps=self.build_deps(session, generate="true", marks=marks)
            )

        self.assertEqual(str(raised.exception), "OmniVoice warmup failed")
        self.assertEqual(marks[-1], ("tts_warmup", "failed", "tts_warmup_failed"))
        self.assertEqual(response.text_calls, 0)
        self.assertNotIn(private_error, repr(marks))
        self.assertNotIn(private_error, repr(raised.exception))


if __name__ == "__main__":
    unittest.main()
