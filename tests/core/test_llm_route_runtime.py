from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.llm_route_runtime import (  # noqa: E402
    LlmRouteRuntimeDeps,
    classify_llm_route_from_runtime,
)


class LlmRouteRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.router_result = {
            "selected": "voice_context",
            "confidence": 0.8,
            "reason_brief": " memory needed ",
            "ask_mode": "soft_followup",
            "max_question_count": 1,
            "question_hint": "preference",
            "question_reason": "useful",
            "question_source": "router",
            "context_policy": {
                "intent": "question",
                "needs_main_llm": True,
                "needs_memory": True,
                "response_mode": "normal",
            },
        }
        self.router_calls: list[tuple[list[dict], dict]] = []
        self.question_calls: list[tuple[dict, str]] = []
        self.logs: list[str] = []
        self.load_calls: list[tuple[str, int]] = []
        self.fast_policy_result: dict | None = None

    async def ask_router(self, messages: list[dict], **kwargs):
        self.router_calls.append((messages, kwargs))
        if isinstance(self.router_result, BaseException):
            raise self.router_result
        return self.router_result

    def normalize_question(self, value: dict, *, default_source: str) -> dict:
        self.question_calls.append((value, default_source))
        return {
            "ask_mode": value.get("ask_mode") or "none",
            "max_question_count": int(value.get("max_question_count") or 0),
            "question_source": value.get("question_source") or default_source,
        }

    def build_deps(self, *, router_enabled: bool = True) -> LlmRouteRuntimeDeps:
        return LlmRouteRuntimeDeps(
            classify_llm_route_fallback=lambda _text, **_kwargs: "main_direct",
            fast_path_policy=lambda _text, _source, _state: self.fast_policy_result,
            session_state_snapshot=lambda key: {"session_key": key},
            load_working_summary=lambda guild_id: self._load("summary", guild_id, "summary text"),
            load_cognitive_state=lambda guild_id: self._load("state", guild_id, {"mood": "calm"}),
            normalize_cognitive_state=lambda value: {"normalized": value},
            load_recent_raw=lambda guild_id: self._load(
                "raw",
                guild_id,
                [{"id": index} for index in range(5)],
            ),
            load_recent_facts=lambda guild_id: self._load(
                "facts",
                guild_id,
                [{"fact": index} for index in range(5)],
            ),
            format_memory_rows_for_llm=lambda rows, **_kwargs: ",".join(str(row) for row in rows),
            compact_memory_text=lambda text, **_kwargs: text[:160],
            ask_router_llm=self.ask_router,
            current_turn_id=lambda key: f"turn:{key}",
            clean_text=lambda text: text.strip(),
            normalize_question_policy_mapping=self.normalize_question,
            router_route_timeout_sec=2.5,
            cognitive_timeout_sec=1.5,
            router_llm_enabled=router_enabled,
            router_route_max_tokens=321,
            log=self.logs.append,
        )

    def _load(self, kind: str, guild_id: int, value):
        self.load_calls.append((kind, guild_id))
        return value

    async def test_fast_path_skips_memory_and_router(self) -> None:
        self.fast_policy_result = {
            "route": "voice_context",
            "reason_brief": "cached policy",
            "needs_memory": True,
        }

        route, meta = await classify_llm_route_from_runtime(
            "계속해",
            deps=self.build_deps(),
            guild_id=11,
            session_key="session-1",
        )

        self.assertEqual(route, "sub_hint")
        self.assertEqual(meta["source"], "fast_path")
        self.assertEqual(meta["reason_brief"], "cached policy")
        self.assertEqual(meta["execution_budget"]["fallback_reason"], "fast_path")
        self.assertEqual(self.load_calls, [])
        self.assertEqual(self.router_calls, [])

    async def test_voice_without_context_marker_uses_fallback(self) -> None:
        route, meta = await classify_llm_route_from_runtime("안녕", deps=self.build_deps(), source="voice")

        self.assertEqual(route, "main_direct")
        self.assertEqual(meta["source"], "fallback")
        self.assertEqual(self.router_calls, [])

    async def test_disabled_router_uses_fallback_for_text(self) -> None:
        route, meta = await classify_llm_route_from_runtime("자세히 알려줘", deps=self.build_deps(router_enabled=False))

        self.assertEqual(route, "main_direct")
        self.assertEqual(meta["source"], "fallback")
        self.assertFalse(meta["execution_budget"]["router_enabled"])

    async def test_router_result_includes_question_and_context_policy(self) -> None:
        route, meta = await classify_llm_route_from_runtime(
            "이전 내용을 이어서 설명해줘",
            deps=self.build_deps(),
            guild_id=11,
            source="text",
            session_key="session-2",
        )

        self.assertEqual(route, "sub_hint")
        self.assertEqual(meta["source"], "router")
        self.assertEqual(meta["confidence"], 0.8)
        self.assertEqual(meta["reason_brief"], "memory needed")
        self.assertEqual(meta["ask_mode"], "soft_followup")
        self.assertEqual(meta["max_question_count"], 1)
        self.assertEqual(meta["context_policy"]["intent"], "question")
        self.assertTrue(meta["context_policy"]["needs_memory"])
        self.assertEqual(self.load_calls, [("summary", 11), ("state", 11), ("raw", 11), ("facts", 11)])
        messages, kwargs = self.router_calls[0]
        self.assertIn("summary text", messages[1]["content"])
        self.assertNotIn("{'id': 0}", messages[1]["content"])
        self.assertIn("{'id': 4}", messages[1]["content"])
        self.assertEqual(kwargs["max_tokens"], 321)
        self.assertEqual(kwargs["turn_id"], "turn:session-2")
        self.assertEqual(self.question_calls[0][1], "router")

    async def test_router_exception_returns_error_fallback(self) -> None:
        self.router_result = RuntimeError("router down")

        route, meta = await classify_llm_route_from_runtime("질문", deps=self.build_deps())

        self.assertEqual(route, "main_direct")
        self.assertEqual(meta["source"], "fallback")
        self.assertIn("router down", meta["error"])
        self.assertIn("route 실패", self.logs[0])

    async def test_invalid_router_result_returns_reasoned_fallback(self) -> None:
        self.router_result = ["not", "json"]

        route, meta = await classify_llm_route_from_runtime("질문", deps=self.build_deps())

        self.assertEqual(route, "main_direct")
        self.assertEqual(meta["reason_brief"], "invalid_router_json")
        self.assertEqual(self.question_calls, [])

    def test_main_delegates_route_classification_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("async def classify_llm_route_async(")
        end = source.index("def build_cognitive_state_runtime_deps(", start)
        function_source = source[start:end]

        self.assertIn("classify_llm_route_from_runtime(", function_source)
        self.assertNotIn("ask_router_llm(", function_source)
        self.assertNotIn("build_turn_execution_budget(", function_source)


if __name__ == "__main__":
    unittest.main()
