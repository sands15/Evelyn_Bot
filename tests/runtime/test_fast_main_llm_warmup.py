from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import web


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402


class _Content:
    def __init__(self, rows: list[bytes]) -> None:
        self._rows = rows

    async def __aiter__(self):
        for row in self._rows:
            yield row


class _Response:
    def __init__(self, status: int, rows: list[bytes] | None = None) -> None:
        self.status = status
        self.content = _Content(rows or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.posts: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: Any,
    ) -> _Response:
        self.posts.append(
            {"url": url, "json": json, "headers": headers, "timeout": timeout}
        )
        return self._responses.pop(0)


class _SessionProvider:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __call__(self) -> _Session:
        return self._session


class _Request:
    def __init__(
        self,
        payload: dict[str, Any],
        app: web.Application,
    ) -> None:
        self._payload = payload
        self.app = app
        self.headers: dict[str, str] = {}

    async def json(self) -> dict[str, Any]:
        return dict(self._payload)


def _ready_health() -> dict[str, Any]:
    return {
        "ok": True,
        "fullyHealthy": True,
        "legacyServices": {
            "botReady": True,
            "mainReady": True,
            "routerReady": True,
            "subReady": True,
            "ttsReady": True,
            "sttReady": True,
        },
        "services": [
            {"id": service_id, "state": "up", "ready": True}
            for service_id, _label in fast_api.BOOT_STEPS
        ],
    }


def _ready_source_identity() -> dict[str, Any]:
    return {
        "schema": "runtime_source_identity.v1",
        "role": "development",
        "mode": "development",
        "state": "development",
        "ready": True,
        "aligned": True,
        "verified": False,
        "imageSourceRevision": None,
        "expectedSourceRevision": None,
        "reasonCode": "development_source_identity",
    }


class FastMainLlmWarmupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_context_does_not_wait_for_warmup(self) -> None:
        app = web.Application()
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = (
            fast_api.new_fast_main_llm_warmup_state()
        )
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def blocked_warmup(_app: web.Application) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        context = fast_api.fast_main_llm_warmup_context(app)
        with patch.object(
            fast_api,
            "warm_fast_main_llm_until_ready",
            new=blocked_warmup,
        ):
            await context.__anext__()
            await started.wait()
            with patch.object(
                fast_api,
                "runtime_source_identity",
                return_value=_ready_source_identity(),
            ):
                response = await fast_api.health_handler(
                    _Request({}, app)
                )
            self.assertEqual(response.status, 200)
            await context.aclose()

        self.assertIs(stopped.is_set(), True)

    async def test_retries_exact_fast_prefix_with_prompt_cache(self) -> None:
        app = web.Application()
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = (
            fast_api.new_fast_main_llm_warmup_state()
        )
        session = _Session(
            [
                _Response(503),
                _Response(
                    200,
                    [
                        b'data: {"choices":[{"delta":{"content":"ok"}}]}',
                        (
                            b'data: {"choices":[{"finish_reason":"length",'
                            b'"delta":{}}],"timings":{"prompt_n":240,'
                            b'"cache_n":0,"prompt_ms":30.5}}'
                        ),
                        b"data: [DONE]",
                    ],
                ),
                _Response(
                    200,
                    [
                        b'data: {"choices":[{"delta":{"content":"ok"}}]}',
                        (
                            b'data: {"choices":[{"finish_reason":"length",'
                            b'"delta":{}}],"timings":{"prompt_n":12,'
                            b'"cache_n":228,"prompt_ms":2.5}}'
                        ),
                        b"data: [DONE]",
                    ],
                ),
            ]
        )
        retry_sleep = AsyncMock()

        with (
            patch.object(
                fast_api,
                "FAST_MAIN_LLM_HTTP_SESSION",
                _SessionProvider(session),
            ),
            patch.object(fast_api.asyncio, "sleep", retry_sleep),
        ):
            await fast_api.warm_fast_main_llm_until_ready(app)

        self.assertEqual(len(session.posts), 3)
        self.assertEqual(retry_sleep.await_count, 1)
        for post in session.posts:
            payload = post["json"]
            self.assertIs(payload["cache_prompt"], True)
            self.assertEqual(payload["messages"][0]["role"], "system")
            self.assertTrue(
                payload["messages"][0]["content"].startswith(
                    f"{fast_api.clean_text(fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)}\n\n"
                )
            )
        self.assertNotEqual(
            session.posts[1]["json"]["messages"][0]["content"],
            session.posts[2]["json"]["messages"][0]["content"],
        )
        self.assertEqual(
            session.posts[1]["json"]["messages"][1]["content"],
            session.posts[2]["json"]["messages"][1]["content"],
        )
        state = app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY]
        self.assertEqual(state["attempts"], 2)
        self.assertEqual(state["status"], "done")
        self.assertIs(state["ready"], True)
        self.assertIs(state["cacheProof"], True)
        self.assertEqual(state["probeCount"], 2)
        self.assertEqual(state["minCacheHitRatio"], 0.95)
        self.assertEqual(state["maxPromptEvalMs"], 30.5)
        self.assertEqual(len(state["promptAbiIds"]), 1)
        self.assertIs(state["promptAbiProductionMatch"], True)

    def test_managed_cache_proof_without_production_prompt_match_stays_closed(self) -> None:
        app = web.Application()
        state = fast_api.new_fast_main_llm_warmup_state()
        state.update(
            {
                "status": "done",
                "ready": True,
                "cacheProof": True,
                "promptAbiExact": True,
                "verifiedAtMonotonic": fast_api.time.monotonic(),
            }
        )
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = state

        self.assertIs(
            fast_api.fast_main_llm_warmup_ready(_Request({}, app)),
            False,
        )

    async def test_pending_warmup_only_closes_local_chat_and_voice(self) -> None:
        app = web.Application()
        pending = fast_api.new_fast_main_llm_warmup_state()
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = pending

        with patch.object(
            fast_api,
            "runtime_source_identity",
            return_value=_ready_source_identity(),
        ):
            state = fast_api.build_control_state(
                _ready_health(),
                main_llm_warmup=pending,
            )
            health_response = await fast_api.health_handler(
                _Request({}, app)
            )

        self.assertIs(state["ok"], True)
        self.assertIs(state["runtime"]["services"]["botReady"], True)
        self.assertIs(state["runtime"]["services"]["mainReady"], True)
        self.assertIs(
            state["runtime"]["services"]["mainWarmupReady"],
            False,
        )
        self.assertIs(state["runtime"]["services"]["chatReady"], False)
        self.assertIs(state["runtime"]["services"]["voiceReady"], False)
        self.assertIs(state["chat"]["inputEnabled"], False)
        self.assertEqual(health_response.status, 200)
        self.assertIs(json.loads(health_response.text or "{}")["ok"], True)

        chat_response = await fast_api.chat_handler(
            _Request({"text": "hello"}, app)
        )
        stream_response = await fast_api._chat_stream_handler(
            _Request({"text": "hello"}, app)
        )
        voice_response = await fast_api.local_voice_admission_handler(
            _Request({"text": "hello"}, app)
        )

        for response in (chat_response, stream_response):
            payload = json.loads(response.text or "{}")
            self.assertEqual(response.status, 503)
            self.assertEqual(payload["error"], "main_llm_warmup_pending")
        voice_payload = json.loads(voice_response.text or "{}")
        self.assertEqual(voice_response.status, 503)
        self.assertEqual(
            voice_payload["reason"],
            "main_llm_warmup_pending",
        )

    async def test_managed_ready_flag_without_cache_proof_stays_closed(self) -> None:
        app = web.Application()
        state = fast_api.new_fast_main_llm_warmup_state()
        state.update({"status": "done", "ready": True, "cacheProof": False})
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = state

        self.assertIs(fast_api.fast_main_llm_warmup_ready(_Request({}, app)), False)

        with patch.object(
            fast_api,
            "runtime_source_identity",
            return_value=_ready_source_identity(),
        ):
            control_state = fast_api.build_control_state(
                _ready_health(),
                main_llm_warmup=state,
            )
        self.assertIs(
            control_state["runtime"]["services"]["mainWarmupReady"],
            False,
        )
        self.assertIs(control_state["chat"]["inputEnabled"], False)

    async def test_backend_epoch_change_invalidates_ready_cache_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            epoch_path = Path(directory) / "epoch"
            epoch_path.write_text("epoch-one", encoding="ascii")
            app = web.Application()
            state = fast_api.new_fast_main_llm_warmup_state()
            state.update(
                {
                    "status": "done",
                    "ready": True,
                    "cacheProof": True,
                    "promptAbiProductionMatch": True,
                    "backendEpoch": "epoch-one",
                    "verifiedAtMonotonic": fast_api.time.monotonic(),
                }
            )
            app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = state

            with patch.object(fast_api, "MAIN_LLM_EPOCH_FILE", epoch_path):
                current = fast_api.public_fast_main_llm_warmup_state(app)
                epoch_path.write_text("epoch-two", encoding="ascii")
                stale = fast_api.public_fast_main_llm_warmup_state(app)

        self.assertIs(current["ready"], True)
        self.assertIs(current["cacheProof"], True)
        self.assertIs(current["backendEpochBound"], True)
        self.assertEqual(stale["status"], "stale")
        self.assertIs(stale["ready"], False)
        self.assertIs(stale["cacheProof"], False)
        self.assertIs(stale["backendEpochBound"], False)

    def test_proof_does_not_expire_but_clock_rollback_closes_readiness(self) -> None:
        app = web.Application()
        state = fast_api.new_fast_main_llm_warmup_state()
        state.update(
            {
                "status": "done",
                "ready": True,
                "cacheProof": True,
                "promptAbiExact": True,
                "promptAbiProductionMatch": True,
                "verifiedAtMonotonic": 100.0,
            }
        )
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = state

        with patch.object(fast_api.time, "monotonic", return_value=1_000_000.0):
            current = fast_api.public_fast_main_llm_warmup_state(app)
        with patch.object(fast_api.time, "monotonic", return_value=99.0):
            rollback = fast_api.public_fast_main_llm_warmup_state(app)

        self.assertIs(current["ready"], True)
        self.assertIs(current["proofFresh"], True)
        self.assertEqual(rollback["status"], "stale")
        self.assertEqual(
            rollback["detail"],
            "main_llm_warmup_proof_invalid",
        )
        self.assertIs(rollback["ready"], False)
        self.assertIs(rollback["cacheProof"], False)
        self.assertIs(rollback["promptAbiExact"], False)
        self.assertIs(rollback["promptAbiProductionMatch"], False)
        self.assertIs(rollback["proofFresh"], False)

    async def test_supervisor_rewarms_only_after_epoch_change(self) -> None:
        app = web.Application()
        state = fast_api.new_fast_main_llm_warmup_state()
        app[fast_api.FAST_MAIN_LLM_WARMUP_STATE_KEY] = state
        original_sleep = asyncio.sleep
        same_epoch_observed = asyncio.Event()
        allow_epoch_change = asyncio.Event()
        second_warmup_started = asyncio.Event()
        epoch = {"value": "epoch-one"}
        polls = 0
        calls = 0

        async def fake_warmup(_app: web.Application) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                state.update(
                    {
                        "status": "done",
                        "ready": True,
                        "cacheProof": True,
                        "promptAbiProductionMatch": True,
                        "backendEpoch": "epoch-one",
                        "verifiedAtMonotonic": fast_api.time.monotonic(),
                    }
                )
                return
            second_warmup_started.set()
            await asyncio.Event().wait()

        async def fake_sleep(_delay: float) -> None:
            nonlocal polls
            polls += 1
            if polls == 1:
                epoch["value"] = ""
            elif polls == 2:
                epoch["value"] = "epoch-one"
                same_epoch_observed.set()
                await allow_epoch_change.wait()
            else:
                epoch["value"] = "epoch-two"
            await original_sleep(0)

        with (
            patch.object(
                fast_api,
                "warm_fast_main_llm_until_ready",
                new=fake_warmup,
            ),
            patch.object(fast_api, "MAIN_LLM_EPOCH_FILE", Path("configured")),
            patch.object(
                fast_api,
                "current_main_llm_backend_epoch",
                side_effect=lambda: epoch["value"],
            ),
            patch.object(fast_api.asyncio, "sleep", new=fake_sleep),
        ):
            task = asyncio.create_task(
                fast_api.supervise_fast_main_llm_warmup(app)
            )
            try:
                await asyncio.wait_for(same_epoch_observed.wait(), 1.0)
                self.assertEqual(calls, 1)
                allow_epoch_change.set()
                await asyncio.wait_for(second_warmup_started.wait(), 1.0)
                self.assertEqual(calls, 2)
                self.assertEqual(
                    state["detail"],
                    "main_llm_epoch_changed",
                )
                self.assertIs(state["ready"], False)
                self.assertIs(state["cacheProof"], False)
                self.assertIsNone(state["verifiedAtMonotonic"])
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task


if __name__ == "__main__":
    unittest.main()
