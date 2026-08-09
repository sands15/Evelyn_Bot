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

from evelyn_core.control_page_ui_runtime import (  # noqa: E402
    ControlPageWelcomeRuntimeDeps,
    generate_control_page_welcome_text_from_runtime,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, data: dict | None = None, text: str = "") -> None:
        self.status = status
        self.data = data or {}
        self._text = text
        self.text_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self) -> dict:
        return self.data

    async def text(self) -> str:
        self.text_calls += 1
        return self._text


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.posts: list[dict] = []

    def post(self, url: str, *, json: dict, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return self.response


class ControlPageWelcomeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = FakeSession(FakeResponse(data={"choices": [{"message": {"content": "어서 와"}}]}))
        self.traces: list[dict] = []
        self.logs: list[str] = []
        self.payloads: list[dict] = []

    def build_deps(self) -> ControlPageWelcomeRuntimeDeps:
        return ControlPageWelcomeRuntimeDeps(
            effective_guild_name=lambda guild: guild.name if guild is not None else "로컬",
            effective_guild_id=lambda guild: guild.id if guild is not None else 0,
            build_main_llm_payload=lambda **kwargs: self._payload(kwargs),
            model_name="main-model",
            main_llm_chat_content_format="string",
            main_llm_stop_tokens=("STOP",),
            get_http_session=lambda: asyncio.sleep(0, result=self.session),
            client_timeout_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            welcome_llm_timeout_sec=12.0,
            llm_server_url="http://llm/v1/chat",
            extract_main_llm_answer_from_choice=lambda choice, _user, **_kwargs: (
                choice["message"]["content"],
                "content",
                "stop",
            ),
            sanitize_model_output=lambda text: text,
            parse_response_action_tag=lambda _text: None,
            extract_answer_from_reasoning=lambda _reasoning, answer: answer,
            sanitize_welcome_text=lambda text: text.strip(),
            record_model_call_trace=lambda **kwargs: self.traces.append(kwargs),
            monotonic=lambda: 10.0,
            welcome_fallback="기본 환영",
            clean_text=lambda text: text.strip(),
            log=self.logs.append,
        )

    def _payload(self, kwargs: dict) -> dict:
        self.payloads.append(kwargs)
        return {"request": True, **kwargs}

    async def test_generates_sanitized_welcome_and_records_success(self) -> None:
        result = await generate_control_page_welcome_text_from_runtime(
            SimpleNamespace(id=7, name="테스트 공간"),
            deps=self.build_deps(),
        )

        self.assertEqual(result, "어서 와")
        self.assertIn("현재 공간 이름: 테스트 공간", self.payloads[0]["final_user_text"])
        self.assertEqual(self.payloads[0]["temperature"], 0.65)
        self.assertEqual(self.session.posts[0]["url"], "http://llm/v1/chat")
        self.assertEqual(self.session.posts[0]["timeout"].total, 12.0)
        self.assertTrue(self.traces[0]["success"])
        self.assertEqual(self.traces[0]["guild_id"], 7)

    async def test_http_failure_records_trace_and_returns_fallback(self) -> None:
        marker = "PRIVATE_WELCOME_BODY_CANARY:/synthetic/model-token.json"
        response = FakeResponse(status=503, text=marker)
        self.session = FakeSession(response)

        result = await generate_control_page_welcome_text_from_runtime(None, deps=self.build_deps())

        self.assertEqual(result, "기본 환영")
        self.assertEqual(response.text_calls, 0)
        self.assertFalse(self.traces[0]["success"])
        self.assertEqual(self.traces[0]["error"], "RuntimeError")
        self.assertEqual(
            self.logs,
            ["[CONTROL PAGE] welcome_generation_failed errorType=RuntimeError"],
        )
        self.assertNotIn(marker, repr(self.traces) + repr(self.logs))

    async def test_empty_choices_returns_fallback(self) -> None:
        self.session = FakeSession(FakeResponse(data={"choices": []}))

        result = await generate_control_page_welcome_text_from_runtime(None, deps=self.build_deps())

        self.assertEqual(result, "기본 환영")
        self.assertEqual(self.traces[0]["error"], "RuntimeError")

    def test_main_delegates_welcome_generation_to_ui_runtime(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def generate_welcome_text(")
        end = source.index("async def ensure_welcome_message(", start)
        function_source = source[start:end]

        self.assertIn("generate_control_page_welcome_text_from_runtime(", function_source)
        self.assertNotIn("session.post(", function_source)
        self.assertNotIn("record_model_call_trace(", function_source)


if __name__ == "__main__":
    unittest.main()
