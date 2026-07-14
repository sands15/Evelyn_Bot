from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.llm_warmup_runtime import LlmWarmupRuntimeDeps, warmup_llm_from_runtime  # noqa: E402


class FakeContent:
    def __init__(self, rows: list[bytes]) -> None:
        self.rows = rows

    async def __aiter__(self):
        for row in self.rows:
            yield row


class FakeResponse:
    def __init__(self, status: int, *, text: str = "", rows: list[bytes] | None = None) -> None:
        self.status = status
        self._text = text
        self.content = FakeContent(rows or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def text(self) -> str:
        return self._text


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], timeout: Any) -> FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return self.response


class LlmWarmupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        session: FakeSession,
        *,
        marks: list[tuple[str, str, str]] | None = None,
        logs: list[str] | None = None,
    ) -> LlmWarmupRuntimeDeps:
        marks = marks if marks is not None else []
        logs = logs if logs is not None else []

        def mark(key: str, status: str, detail: str = "") -> None:
            marks.append((key, status, detail))

        return LlmWarmupRuntimeDeps(
            get_http_session=lambda: self.async_value(session),
            client_timeout=lambda **kwargs: kwargs,
            mark_startup_component=mark,
            llm_server_url="http://llm/v1/chat/completions",
            model_name="model",
            main_llm_chat_content_format="chat",
            voice_llm_max_tokens=128,
            main_llm_stop_tokens=("</s>",),
            build_chat_messages=lambda messages, **kwargs: [{"messages": messages, "format": kwargs["content_format"]}],
            decode_sse_stream_line=lambda raw: {"delta_text": "응"} if raw == b"delta" else {"done": True},
            log=lambda message: logs.append(str(message)),
        )

    async def async_value(self, value: Any) -> Any:
        return value

    async def test_marks_done_on_first_delta_text(self) -> None:
        marks: list[tuple[str, str, str]] = []
        logs: list[str] = []
        session = FakeSession(FakeResponse(200, rows=[b"done", b"delta"]))

        await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks, logs=logs))

        self.assertEqual(session.posts[0]["url"], "http://llm/v1/chat/completions")
        self.assertEqual(session.posts[0]["json"]["model"], "model")
        self.assertEqual(session.posts[0]["json"]["max_tokens"], 8)
        self.assertEqual(session.posts[0]["json"]["stop"], ["</s>"])
        self.assertEqual(marks[0], ("main_warmup", "running", "Main LLM warmup request"))
        self.assertEqual(marks[-1], ("main_warmup", "done", ""))
        self.assertIn("[STARTUP] llm_warmup_done", logs)

    async def test_marks_done_when_stream_has_no_delta(self) -> None:
        marks: list[tuple[str, str, str]] = []
        logs: list[str] = []
        session = FakeSession(FakeResponse(200, rows=[b"done"]))

        await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks, logs=logs))

        self.assertEqual(marks[-1], ("main_warmup", "done", "no streamed chunk"))
        self.assertIn("[STARTUP] llm_warmup_done_no_chunk", logs)

    async def test_marks_failed_and_raises_on_http_error(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(500, text="boom"))

        with self.assertRaisesRegex(RuntimeError, "LLM warmup failed"):
            await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(marks[-1], ("main_warmup", "failed", "500: boom"))


if __name__ == "__main__":
    unittest.main()
