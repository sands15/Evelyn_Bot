from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.json_llm_request_runtime import (  # noqa: E402
    JsonLlmRequestRuntimeDeps,
    ask_json_llm_from_runtime,
)
from evelyn_core import memory_deletion_journal as deletion_journal  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


class FakeResponse:
    def __init__(self, *, status: int = 200, data: dict | None = None, text: str = "") -> None:
        self.status = status
        self.data = data or {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self) -> dict:
        return self.data

    async def text(self) -> str:
        return self._text


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.posts: list[dict] = []

    def post(self, url: str, *, json: dict, timeout):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return self.response


class JsonLlmRequestRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = FakeSession(FakeResponse(data={"choices": [{"message": {"content": '{"ok": true}'}}]}))
        self.traces: list[dict] = []
        self.extracted: list[str] = []

    def build_deps(self, *, role: str = "summary", label: str = "요약 LLM") -> JsonLlmRequestRuntimeDeps:
        return JsonLlmRequestRuntimeDeps(
            model_name=f"{role}-model",
            endpoint=f"http://{role}/v1/chat",
            model_role=role,
            error_label=label,
            get_http_session=lambda: asyncio.sleep(0, result=self.session),
            client_timeout_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            monotonic=lambda: 10.0,
            clean_text=lambda text: text.strip(),
            extract_json_object=self.extract_json,
            record_model_call_trace=lambda **kwargs: self.traces.append(kwargs),
        )

    def extract_json(self, text: str) -> dict:
        self.extracted.append(text)
        return {"parsed": text}

    async def ask(self, deps: JsonLlmRequestRuntimeDeps | None = None) -> dict:
        return await ask_json_llm_from_runtime(
            [{"role": "user", "content": "route"}],
            deps=deps or self.build_deps(),
            max_tokens=123,
            timeout_seconds=4.5,
            purpose="test-purpose",
            hot_path=True,
            turn_id="turn-1",
            session_key="session-1",
            source="voice",
            guild_id=7,
        )

    async def test_content_json_is_parsed_and_success_is_traced(self) -> None:
        result = await self.ask()

        self.assertEqual(result, {"parsed": '{"ok": true}'})
        self.assertEqual(self.session.posts[0]["json"]["model"], "summary-model")
        self.assertEqual(self.session.posts[0]["json"]["max_tokens"], 123)
        self.assertEqual(self.session.posts[0]["timeout"].total, 4.5)
        self.assertEqual(self.traces[0]["model_role"], "summary")
        self.assertEqual(self.traces[0]["turn_id"], "turn-1")
        self.assertTrue(self.traces[0]["success"])

    async def test_reasoning_content_is_used_when_content_is_empty(self) -> None:
        self.session = FakeSession(FakeResponse(data={
            "choices": [{"message": {"content": "", "reasoning_content": ' {"route": "main"} '}}]
        }))

        result = await self.ask(self.build_deps(role="router", label="router LLM"))

        self.assertEqual(result, {"parsed": '{"route": "main"}'})
        self.assertEqual(self.extracted, ['{"route": "main"}'])
        self.assertEqual(self.traces[0]["model_role"], "router")

    async def test_empty_choices_returns_empty_dict_and_traces_success(self) -> None:
        self.session = FakeSession(FakeResponse(data={"choices": []}))

        result = await self.ask()

        self.assertEqual(result, {})
        self.assertEqual(self.extracted, [])
        self.assertTrue(self.traces[0]["success"])

    async def test_http_error_preserves_role_specific_message_and_does_not_trace(self) -> None:
        self.session = FakeSession(FakeResponse(status=503, text="not ready"))

        with self.assertRaisesRegex(RuntimeError, "router LLM 서버 오류: 503"):
            await self.ask(self.build_deps(role="router", label="router LLM"))

        self.assertEqual(self.traces, [])

    async def test_stale_required_memory_boundary_fails_before_post_factory(self) -> None:
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            index_dir = Path(tmp) / "memory_index"
            position = deletion_journal.memory_deletion_journal_position(index_dir)
            deletion_journal.append_memory_deletion_tombstone(
                index_dir,
                {
                    "schema": deletion_journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
                    "noteId": "concept-0123456789abcdef",
                    "noteType": "concept",
                    "sourceType": "conversation",
                    "reason": "privacy_request",
                    "deletedAt": "2026-08-01T00:00:00Z",
                },
            )

            with self.assertRaises(
                deletion_journal.MemoryDeletionJournalIntegrityError
            ) as raised:
                await ask_json_llm_from_runtime(
                    [{"role": "system", "content": "PRIVATE deleted memory canary"}],
                    deps=self.build_deps(),
                    max_tokens=123,
                    timeout_seconds=4.5,
                    purpose="memory_summary",
                    hot_path=False,
                    memory_deletion_position=position,
                    memory_boundary_required=True,
                    memory_deletion_index_dir=index_dir,
                )

        self.assertEqual(
            str(raised.exception),
            deletion_journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
        )
        self.assertEqual(self.session.posts, [])

    def test_main_summary_and_router_wrappers_delegate_to_common_runtime(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "llm_route_composition_runtime.py"
        ).read_text(encoding="utf-8")
        summary_start = source.index("async def ask_summary_llm(")
        router_start = source.index("async def ask_router_llm(", summary_start)
        route_builder = source.index("async def classify_llm_route(", router_start)

        self.assertIn("ask_json_llm_from_runtime(", source[summary_start:router_start])
        self.assertIn("ask_json_llm_from_runtime(", source[router_start:route_builder])
        self.assertNotIn("session.post(", source[summary_start:route_builder])


if __name__ == "__main__":
    unittest.main()
