from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.llm_warmup_runtime import (  # noqa: E402
    LlmWarmupRuntimeDeps,
    warmup_llm_from_runtime,
)
from evelyn_core.main_inference_contract import compile_main_prompt  # noqa: E402
from evelyn_core.text import clean_text  # noqa: E402


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
        self.text_calls = 0
        self.content = FakeContent(rows or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def text(self) -> str:
        self.text_calls += 1
        return self._text


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.posts: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: Any,
    ) -> FakeResponse:
        self.posts.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class LlmWarmupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        session: FakeSession,
        *,
        marks: list[tuple[str, str, str]] | None = None,
        logs: list[str] | None = None,
        require_cache_proof: bool = True,
        require_exact_prompt_abi: bool = False,
        expected_prompt_abi_ids: tuple[str, ...] | None = None,
    ) -> LlmWarmupRuntimeDeps:
        marks = marks if marks is not None else []
        logs = logs if logs is not None else []

        def mark(key: str, status: str, detail: str = "") -> None:
            marks.append((key, status, detail))

        def decode(raw: bytes) -> dict[str, Any] | None:
            if raw == b"delta":
                return {"done": False, "delta_text": "응"}
            if raw == b"done" or raw == b"data: [DONE]":
                return {"done": True}
            line = raw.decode("utf-8", errors="ignore").strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                return None
            choices = payload.get("choices") if isinstance(payload, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices else {}
            delta = choice.get("delta") if isinstance(choice, dict) else {}
            return {
                "done": False,
                "delta_text": str((delta or {}).get("content") or ""),
            }

        return LlmWarmupRuntimeDeps(
            get_http_session=lambda: self.async_value(session),
            client_timeout=lambda **kwargs: kwargs,
            mark_startup_component=mark,
            llm_server_url="http://llm/v1/chat/completions",
            model_name="model",
            system_prompts=("stable system prompt",),
            main_llm_chat_content_format="chat",
            voice_llm_max_tokens=128,
            main_llm_stop_tokens=("</s>",),
            decode_sse_stream_line=decode,
            log=lambda message: logs.append(str(message)),
            require_cache_proof=require_cache_proof,
            require_exact_prompt_abi=require_exact_prompt_abi,
            expected_prompt_abi_ids=expected_prompt_abi_ids,
        )

    async def async_value(self, value: Any) -> Any:
        return value

    async def test_drains_terminal_and_varies_only_dynamic_system_suffix(self) -> None:
        marks: list[tuple[str, str, str]] = []
        logs: list[str] = []
        first_timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":240,"cache_n":0,"prompt_ms":30.5}}'
        )
        second_timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":12,"cache_n":228,"prompt_ms":2.5}}'
        )
        session = FakeSession(
            FakeResponse(200, rows=[b"delta", first_timing, b"done"]),
            FakeResponse(200, rows=[b"delta", second_timing, b"done"]),
        )

        evidence = await warmup_llm_from_runtime(
            deps=self.build_deps(session, marks=marks, logs=logs)
        )

        self.assertEqual(len(session.posts), 2)
        self.assertEqual(session.posts[0]["url"], "http://llm/v1/chat/completions")
        self.assertEqual(session.posts[0]["json"]["model"], "model")
        self.assertEqual(session.posts[0]["json"]["max_tokens"], 1)
        self.assertEqual(session.posts[0]["json"]["stop"], ["</s>"])
        self.assertEqual(
            session.posts[0]["json"]["stream_options"],
            {"include_usage": True},
        )
        self.assertIs(session.posts[0]["json"]["timings_per_token"], True)
        warmup_messages = session.posts[0]["json"]["messages"]
        first_prefix, first_dynamic = warmup_messages[0]["content"].split(
            "\n\n", 1
        )
        self.assertEqual(warmup_messages[0]["role"], "system")
        self.assertEqual(first_prefix, "stable system prompt")
        self.assertEqual(warmup_messages[1]["role"], "user")
        second_messages = session.posts[1]["json"]["messages"]
        second_prefix, second_dynamic = second_messages[0]["content"].split(
            "\n\n", 1
        )
        self.assertEqual(second_prefix, first_prefix)
        self.assertNotEqual(second_dynamic, first_dynamic)
        self.assertEqual(
            second_messages[1]["content"],
            warmup_messages[1]["content"],
        )
        self.assertEqual(marks[0], ("main_warmup", "running", "Main LLM warmup request"))
        self.assertEqual(marks[-1], ("main_warmup", "done", ""))
        self.assertIn("[STARTUP] llm_warmup_done", logs)
        self.assertEqual(evidence.schema, "evelyn.main-llm-warmup-evidence.v3")
        self.assertEqual(len(evidence.probes), 2)
        self.assertEqual(len(evidence.prompt_abi_ids), 1)
        self.assertIs(evidence.cache_reuse_proven, True)
        self.assertIs(evidence.exact_runtime_identity, False)
        self.assertIs(evidence.production_prompt_match, False)

    async def test_canonicalizes_multiline_prefix_and_matches_production_abi(self) -> None:
        raw_prompt = "stable\nsystem\tprompt"
        canonical_prompt = clean_text(raw_prompt)
        expected = compile_main_prompt(
            model_name="model",
            messages=[{"role": "system", "content": canonical_prompt}],
            final_user_text="",
            content_format="chat",
            stable_system_prefix=canonical_prompt,
        ).abi.prompt_abi_id
        timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":12,"cache_n":228,"prompt_ms":2.5}}'
        )
        session = FakeSession(
            FakeResponse(200, rows=[b"delta", timing, b"done"]),
            FakeResponse(200, rows=[b"delta", timing, b"done"]),
        )
        deps = self.build_deps(
            session,
            expected_prompt_abi_ids=(expected,),
        )
        deps = LlmWarmupRuntimeDeps(
            **{
                **deps.__dict__,
                "system_prompts": (raw_prompt,),
            }
        )

        evidence = await warmup_llm_from_runtime(deps=deps)

        self.assertEqual(
            session.posts[0]["json"]["messages"][0]["content"].split("\n\n", 1)[0],
            canonical_prompt,
        )
        self.assertEqual(evidence.prompt_abi_ids, (expected,))
        self.assertIs(evidence.production_prompt_match, True)

    async def test_prompt_abi_mismatch_fails_before_http(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(200, rows=[b"delta", b"done"]))

        with self.assertRaises(RuntimeError):
            await warmup_llm_from_runtime(
                deps=self.build_deps(
                    session,
                    marks=marks,
                    expected_prompt_abi_ids=("mismatch",),
                )
            )

        self.assertEqual(session.posts, [])
        self.assertEqual(
            marks[-1],
            ("main_warmup", "failed", "llm_warmup_prompt_abi_mismatch"),
        )

    async def test_exact_prompt_abi_requirement_fails_before_http(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(200, rows=[b"delta", b"done"]))
        unset_identity = {
            "MAIN_LLM_MODEL_SHA256": "",
            "MAIN_LLM_TOKENIZER_SHA256": "",
            "MAIN_LLM_CHAT_TEMPLATE_SHA256": "",
            "MAIN_LLM_SERVER_SHA256": "",
            "MAIN_LLM_SERVER_IDENTITY_FILE": "",
            "MAIN_LLM_RUNTIME_TEMPLATE_SHA256": "",
            "MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE": "",
            "MAIN_LLM_IDENTITY_FILE": "",
            "MAIN_LLM_PROMPT_ASSETS_EMBEDDED": "",
        }

        with patch.dict(os.environ, unset_identity, clear=False):
            with self.assertRaises(RuntimeError):
                await warmup_llm_from_runtime(
                    deps=self.build_deps(
                        session,
                        marks=marks,
                        require_cache_proof=False,
                        require_exact_prompt_abi=True,
                    )
                )

        self.assertEqual(session.posts, [])
        self.assertEqual(
            marks[-1],
            (
                "main_warmup",
                "failed",
                "llm_warmup_prompt_abi_unverified",
            ),
        )

    async def test_fails_closed_when_stream_has_no_delta(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(200, rows=[b"done"]))

        with self.assertRaises(RuntimeError) as raised:
            await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(str(raised.exception), "LLM warmup failed")
        self.assertEqual(
            marks[-1],
            ("main_warmup", "failed", "llm_warmup_stream_incomplete"),
        )

    async def test_fails_closed_when_stream_ends_before_done(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(200, rows=[b"delta"]))

        with self.assertRaises(RuntimeError) as raised:
            await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(str(raised.exception), "LLM warmup failed")
        self.assertEqual(
            marks[-1],
            ("main_warmup", "failed", "llm_warmup_stream_incomplete"),
        )

    async def test_fails_closed_on_malformed_data_without_exposing_it(self) -> None:
        private_row = b"data: PRIVATE_WARMUP_STREAM:/secret/model.json"
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(200, rows=[private_row, b"done"]))

        with self.assertRaises(RuntimeError) as raised:
            await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(str(raised.exception), "LLM warmup failed")
        self.assertEqual(
            marks[-1],
            ("main_warmup", "failed", "llm_warmup_stream_malformed"),
        )
        self.assertNotIn("PRIVATE_WARMUP_STREAM", repr(marks))
        self.assertNotIn("PRIVATE_WARMUP_STREAM", repr(raised.exception))

    async def test_returns_typed_cache_timing_proof(self) -> None:
        first_timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":240,"cache_n":0,"prompt_ms":30.5}}'
        )
        second_timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":12,"cache_n":228,"prompt_ms":2.5}}'
        )
        session = FakeSession(
            FakeResponse(200, rows=[b"delta", first_timing, b"done"]),
            FakeResponse(200, rows=[b"delta", second_timing, b"done"]),
        )

        evidence = await warmup_llm_from_runtime(
            deps=self.build_deps(session, require_cache_proof=True)
        )

        self.assertIs(evidence.cache_reuse_proven, True)
        self.assertEqual(evidence.probes[0].prompt_tokens_processed, 240)
        self.assertEqual(evidence.probes[1].prompt_tokens_cached, 228)
        self.assertEqual(evidence.probes[1].prompt_eval_ms, 2.5)
        self.assertEqual(evidence.probes[1].finish_reason, "length")

    async def test_required_cache_proof_fails_closed_when_timing_is_missing(self) -> None:
        marks: list[tuple[str, str, str]] = []
        session = FakeSession(FakeResponse(200, rows=[b"delta", b"done"]))

        with self.assertRaises(RuntimeError) as raised:
            await warmup_llm_from_runtime(
                deps=self.build_deps(
                    session,
                    marks=marks,
                    require_cache_proof=True,
                )
            )

        self.assertEqual(str(raised.exception), "LLM warmup failed")
        self.assertEqual(
            marks[-1],
            ("main_warmup", "failed", "llm_warmup_cache_proof_missing"),
        )

    async def test_required_cache_proof_rejects_trivial_cached_prefix(self) -> None:
        marks: list[tuple[str, str, str]] = []
        first_timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":240,"cache_n":0,"prompt_ms":30.5}}'
        )
        second_timing = (
            b'data: {"choices":[{"finish_reason":"length","delta":{}}],'
            b'"timings":{"prompt_n":239,"cache_n":1,"prompt_ms":30.0}}'
        )
        session = FakeSession(
            FakeResponse(200, rows=[b"delta", first_timing, b"done"]),
            FakeResponse(200, rows=[b"delta", second_timing, b"done"]),
        )

        with self.assertRaises(RuntimeError) as raised:
            await warmup_llm_from_runtime(
                deps=self.build_deps(session, marks=marks, require_cache_proof=True)
            )

        self.assertEqual(str(raised.exception), "LLM warmup failed")
        self.assertEqual(
            marks[-1],
            ("main_warmup", "failed", "llm_warmup_cache_proof_missing"),
        )

    async def test_marks_failed_and_raises_on_http_error(self) -> None:
        private_error = "PRIVATE_LLM_WARMUP_BODY:/synthetic/model-token.json"
        marks: list[tuple[str, str, str]] = []
        response = FakeResponse(500, text=private_error)
        session = FakeSession(response)

        with self.assertRaises(RuntimeError) as raised:
            await warmup_llm_from_runtime(deps=self.build_deps(session, marks=marks))

        self.assertEqual(str(raised.exception), "LLM warmup failed")
        self.assertEqual(marks[-1], ("main_warmup", "failed", "llm_warmup_failed"))
        self.assertEqual(response.text_calls, 0)
        self.assertNotIn(private_error, repr(marks))
        self.assertNotIn(private_error, repr(raised.exception))


if __name__ == "__main__":
    unittest.main()
