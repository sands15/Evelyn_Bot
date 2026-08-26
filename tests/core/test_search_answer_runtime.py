from __future__ import annotations

import json
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
    @staticmethod
    def decoded_evidence(rendered: str) -> dict:
        encoded = rendered.split("evidencePreviewHex=", 1)[1].rstrip(".")
        return json.loads(bytes.fromhex(encoded).decode("utf-8"))

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

        self.assertIn("결과를 받지 못했어", result)
        self.assertEqual(self.session.calls, [])

    async def test_renders_typed_external_cards_without_model_request(self) -> None:
        results = [{"title": " 제목 ", "snippet": " 내용 "}]

        result = await answer_from_search_results_from_runtime("질문", results, deps=self.build_deps())

        evidence = self.decoded_evidence(result)
        self.assertEqual(evidence["cards"][0]["title"], "제목")
        self.assertEqual(evidence["cards"][0]["excerpt"], "내용")
        self.assertIn("외부 인용 데이터", result)
        self.assertEqual(self.session.calls, [])
        self.assertEqual(self.build_calls, [])

    async def test_deterministic_renderer_does_not_expose_memory_or_history(self) -> None:
        result = await answer_from_search_results_from_runtime(
            "질문",
            [{"title": "제목", "snippet": "내용"}],
            deps=self.build_deps(),
        )

        self.assertNotIn(str(self.memory_index_dir), result)
        self.assertEqual(self.session.calls, [])

    async def test_model_http_failure_is_irrelevant_to_deterministic_renderer(self) -> None:
        self.response = FakeResponse(status=500, text="x" * 400)
        self.session = FakeSession(self.response)

        result = await answer_from_search_results_from_runtime(
            "질문",
            [{"snippet": "내용"}],
            deps=self.build_deps(),
        )

        self.assertEqual(
            self.decoded_evidence(result)["cards"][0]["excerpt"],
            "내용",
        )
        self.assertEqual(self.session.calls, [])

    async def test_snippet_urls_are_omitted_from_user_cards(self) -> None:
        self.response = FakeResponse(data={"choices": []})
        self.session = FakeSession(self.response)

        result = await answer_from_search_results_from_runtime(
            "질문",
            [{"snippet": "첫 내용 https://source"}],
            deps=self.build_deps(),
        )

        self.assertEqual(
            self.decoded_evidence(result)["cards"][0]["excerpt"],
            "첫 내용",
        )
        self.assertNotIn("https://source", result)

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
