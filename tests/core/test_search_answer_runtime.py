from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.search_answer_runtime import (  # noqa: E402
    SearchAnswerRuntimeDeps,
    answer_from_search_results_from_runtime,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, data=None, text: str = "") -> None:
        self.status = status
        self.data = data or {}
        self.text_value = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self):
        return self.data

    async def text(self) -> str:
        return self.text_value


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class SearchAnswerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.memory_index_dir = (
            Path(self.temp_dir.name) / "memory_index"
        )
        self.response = FakeResponse(data={"choices": [{"message": {"content": " 정리 답변 "}}]})
        self.session = FakeSession(self.response)
        self.build_calls: list[tuple[list[dict], dict]] = []

    async def get_session(self):
        return self.session

    def build_messages(self, messages, **kwargs):
        self.build_calls.append((messages, kwargs))
        return messages

    def build_deps(self) -> SearchAnswerRuntimeDeps:
        return SearchAnswerRuntimeDeps(
            model_name="main-model",
            llm_server_url="http://llm/chat",
            memory_index_dir=self.memory_index_dir,
            chat_content_format="string",
            stop_tokens=("STOP",),
            get_http_session=self.get_session,
            build_chat_messages=self.build_messages,
            client_timeout_factory=lambda **kwargs: kwargs,
            clean_text=lambda text: str(text).strip(),
            sanitize_model_output=lambda text: str(text).strip(),
            strip_search_answer_sources=lambda text: str(text).replace("https://source", "").strip(),
        )

    async def test_empty_results_return_without_http_request(self) -> None:
        result = await answer_from_search_results_from_runtime("질문", [], deps=self.build_deps())

        self.assertIn("결과를 못 찾았어", result)
        self.assertEqual(self.session.calls, [])

    async def test_builds_summary_request_and_returns_sanitized_answer(self) -> None:
        results = [{"title": " 제목 ", "snippet": " 내용 "}]

        result = await answer_from_search_results_from_runtime("질문", results, deps=self.build_deps())

        self.assertEqual(result, "정리 답변")
        url, request = self.session.calls[0]
        self.assertEqual(url, "http://llm/chat")
        self.assertEqual(request["json"]["model"], "main-model")
        self.assertEqual(request["json"]["stop"], ["STOP"])
        self.assertEqual(request["timeout"], {"total": 45})
        self.assertIn("- 제목 | 내용", self.build_calls[0][0][1]["content"])
        self.assertEqual(self.build_calls[0][1]["content_format"], "string")

    async def test_request_uses_injected_memory_index_dir(self) -> None:
        observed: list[Path] = []

        @asynccontextmanager
        async def recording_request(
            request_factory,
            *args,
            memory_index_dir,
            **kwargs,
        ):
            observed.append(memory_index_dir)
            async with request_factory(*args, **kwargs) as response:
                yield response

        with patch(
            "evelyn_core.search_answer_runtime.memory_exposure_request",
            recording_request,
        ):
            await answer_from_search_results_from_runtime(
                "질문",
                [{"title": "제목", "snippet": "내용"}],
                deps=self.build_deps(),
            )

        self.assertEqual(observed, [self.memory_index_dir])

    async def test_http_error_includes_bounded_response_text(self) -> None:
        self.response = FakeResponse(status=500, text="x" * 400)
        self.session = FakeSession(self.response)

        with self.assertRaisesRegex(RuntimeError, "검색 정리 LLM 오류: 500"):
            await answer_from_search_results_from_runtime(
                "질문",
                [{"snippet": "내용"}],
                deps=self.build_deps(),
            )

    async def test_empty_choices_falls_back_to_first_snippet_and_strips_source(self) -> None:
        self.response = FakeResponse(data={"choices": []})
        self.session = FakeSession(self.response)

        result = await answer_from_search_results_from_runtime(
            "질문",
            [{"snippet": "첫 내용 https://source"}],
            deps=self.build_deps(),
        )

        self.assertEqual(result, "찾아보니까 첫 내용")

    def test_main_delegates_search_answer_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "llm_route_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def answer_from_search_results(")
        end = source.index("async def deliver_proactive_followup", start)
        function_source = source[start:end]

        self.assertIn("answer_from_search_results_from_runtime(", function_source)
        self.assertNotIn("session.post(", function_source)
        self.assertNotIn("build_chat_messages(", function_source)


if __name__ == "__main__":
    unittest.main()
