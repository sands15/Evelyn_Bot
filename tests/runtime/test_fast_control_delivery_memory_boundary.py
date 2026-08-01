from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
sys.modules.setdefault("numpy", types.SimpleNamespace(ndarray=object))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core.conversation_memory_exposure import (  # noqa: E402
    ConversationMemoryHistoryOutcome,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
    MEMORY_DELETION_POSITION_SCHEMA,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MemoryExposurePosition,
    reset_memory_exposure_position,
)


NOTE_ID = "concept-0123456789abcdef"
STALE_CANARY = "stale-memory-must-never-cross-http-boundary"
SUCCESS_REPLY = "single durable terminal reply"


class FastControlDeliveryMemoryBoundaryTests(
    unittest.IsolatedAsyncioTestCase
):
    async def asyncSetUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.bot_memory = self.root / "bot_memory"
        self.runtime_artifacts = self.root / "runtime_artifacts"
        self.bot_memory.mkdir()
        self.runtime_artifacts.mkdir()
        self._environment = patch.dict(
            os.environ,
            {
                "BOT_MEMORY_DIR": str(self.bot_memory),
                "EVELYN_RUNTIME_ARTIFACTS_DIR": str(
                    self.runtime_artifacts
                ),
            },
        )
        self._environment.start()
        self._memory_root = patch.object(
            fast_api,
            "MEMORY_ROOT",
            self.bot_memory,
        )
        self._memory_root.start()
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        fast_api.clear_background_action_handlers()
        reset_memory_exposure_position()
        fast_api.FAST_MEMORY_EXPOSURE_POSITION.set(None)

    async def asyncTearDown(self) -> None:
        pending = list(fast_api.BACKGROUND_ACTION_TASKS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        fast_api.clear_background_action_handlers()
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        reset_memory_exposure_position()
        fast_api.FAST_MEMORY_EXPOSURE_POSITION.set(None)
        self._memory_root.stop()
        self._environment.stop()
        self._temporary.cleanup()

    @staticmethod
    def exposure_position() -> MemoryExposurePosition:
        return MemoryExposurePosition(
            deletion_position=MemoryDeletionPosition(
                schema=MEMORY_DELETION_POSITION_SCHEMA,
                root_digest="a" * 64,
                sequence=7,
                position_digest="b" * 64,
            ),
            memory_version=3,
            supplied_note_ids=(NOTE_ID,),
        )

    @staticmethod
    def filtered_outcome(
        rows: object,
        position: MemoryExposurePosition,
    ) -> ConversationMemoryHistoryOutcome:
        messages = tuple(
            dict(row)
            for row in rows
            if isinstance(row, dict)
        )
        return ConversationMemoryHistoryOutcome(
            messages=messages,
            memory_receipt_ref=not_used_memory_receipt_ref(),
            memory_exposure_position=(position if messages else None),
        )

    async def test_prepare_integrity_failure_becomes_exact_no_store_503(
        self,
    ) -> None:
        position = self.exposure_position()

        @contextmanager
        def fail_bound_guard(
            *,
            expected_position=None,
            required=False,
            **_kwargs,
        ):
            if required or expected_position is not None:
                raise MemoryDeletionJournalIntegrityError()
            yield

        async def handler(_request: web.Request) -> web.StreamResponse:
            return fast_api.memory_guarded_json_response(
                {
                    "ok": True,
                    "reply": STALE_CANARY,
                },
                expected_position=position,
            )

        app = web.Application()
        app.router.add_get("/guarded", handler)
        client = TestClient(TestServer(app))
        with patch.object(
            fast_api,
            "memory_exposure_guard",
            new=fail_bound_guard,
        ):
            await client.start_server()
            try:
                response = await client.get("/guarded")
                body = await response.text()
            finally:
                await client.close()

        self.assertEqual(response.status, 503)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(
            json.loads(body),
            {
                "ok": False,
                "error": MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR,
            },
        )
        self.assertNotIn(STALE_CANARY, body)
        self.assertNotIn(NOTE_ID, body)

    async def test_done_transport_failure_does_not_duplicate_terminal_turn(
        self,
    ) -> None:
        original_write_stream_event = fast_api.write_stream_event
        done_failures = 0

        async def fail_first_done(response, payload):
            nonlocal done_failures
            if payload.get("type") == "done" and done_failures == 0:
                done_failures += 1
                raise ConnectionError("synthetic done transport failure")
            await original_write_stream_event(response, payload)

        continuity = Mock(
            return_value={
                "schema": "fast_control.delivery-continuity.v1",
                "enabled": True,
                "durable": True,
                "generation": 1,
                "persistedSessionCount": 1,
                "error": "",
            }
        )
        app = fast_api.create_app(
            enable_minecraft_world_lease_owner=False
        )
        client = TestClient(TestServer(app))
        with patch.object(
            fast_api,
            "plan_fast_tool_request_for_turn",
            new=AsyncMock(return_value=None),
        ), patch.object(
            fast_api,
            "resolve_pre_llm_reply",
            new=AsyncMock(return_value=SUCCESS_REPLY),
        ), patch.object(
            fast_api,
            "should_queue_local_bridge_speech",
            return_value=False,
        ), patch.object(
            fast_api,
            "commit_fast_control_turn",
            continuity,
        ), patch.object(
            fast_api,
            "write_stream_event",
            new=fail_first_done,
        ):
            await client.start_server()
            try:
                response = await client.post(
                    "/api/control-page/chat-stream",
                    json={
                        "text": "make exactly one terminal reply",
                        "source": "control_page",
                    },
                )
                await response.read()
            finally:
                await client.close()

        assistant_rows = [
            row
            for row in fast_api.CHAT_MESSAGES
            if row.get("role") == "assistant"
        ]
        self.assertEqual(done_failures, 1)
        self.assertEqual(
            [row.get("text") for row in assistant_rows],
            [SUCCESS_REPLY],
        )
        self.assertEqual(continuity.call_count, 1)

    async def test_local_stream_emits_handoff_before_reply_and_on_done(
        self,
    ) -> None:
        continuity = {
            "schema": "fast_control.delivery-continuity.v1",
            "enabled": True,
            "durable": True,
            "generation": 1,
            "persistedSessionCount": 1,
            "error": "",
        }
        app = fast_api.create_app(
            enable_minecraft_world_lease_owner=False
        )
        client = TestClient(TestServer(app))
        with patch.object(
            fast_api,
            "consume_local_voice_admission",
            return_value=("local handoff", None),
        ), patch.object(
            fast_api,
            "plan_fast_tool_request_for_turn",
            new=AsyncMock(return_value=None),
        ), patch.object(
            fast_api,
            "resolve_pre_llm_reply",
            new=AsyncMock(return_value="safe local reply"),
        ), patch.object(
            fast_api,
            "commit_fast_control_turn",
            return_value=continuity,
        ):
            await client.start_server()
            try:
                response = await client.post(
                    "/api/control-page/chat-stream",
                    json={
                        "text": "local handoff",
                        "source": "local_bridge",
                    },
                )
                events = [
                    json.loads(line)
                    for line in (await response.text()).splitlines()
                    if line.strip()
                ]
            finally:
                await client.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(events[0]["type"], "memory_boundary")
        self.assertEqual(events[0]["memoryState"], "not_used")
        self.assertIsNone(events[0]["memoryBoundary"])
        done = next(row for row in events if row["type"] == "done")
        self.assertEqual(done["memoryState"], "not_used")
        self.assertIsNone(done["memoryBoundary"])
        self.assertLess(
            next(i for i, row in enumerate(events) if row["type"] == "memory_boundary"),
            next(i for i, row in enumerate(events) if row["type"] == "sentence"),
        )

    async def test_state_response_guards_projection_exposure(self) -> None:
        position = self.exposure_position()
        fast_api.append_chat_message(
            "assistant",
            "Evelyn",
            "memory-derived state reply",
            memory_receipt={
                "state": "bound",
                "memoryVersion": position.memory_version,
                "suppliedNoteIds": [NOTE_ID],
                "suppliedNoteCount": 1,
                "contentFree": True,
            },
        )

        def filter_rows(rows, *, memory_index_dir):
            self.assertEqual(memory_index_dir, self.bot_memory / "memory_index")
            return self.filtered_outcome(rows, position)

        with patch.object(
            fast_api,
            "filter_conversation_history_for_memory_exposure",
            side_effect=filter_rows,
        ), patch.object(
            fast_api,
            "cached_fast_runtime_health",
            new=AsyncMock(return_value={"services": []}),
        ):
            response = await fast_api.state_handler(object())

        self.assertIsInstance(
            response,
            fast_api.MemoryGuardedJsonResponse,
        )
        self.assertEqual(
            response._memory_expected_position,
            position,
        )

    async def test_action_events_response_guards_projection_exposure(
        self,
    ) -> None:
        position = self.exposure_position()
        task = fast_api.ACTION_COORDINATOR.start(
            kind="runtime_investigation",
            source="control_page",
            user_text="inspect",
            start_reply="started",
        )
        fast_api.ACTION_COORDINATOR.complete(
            task.task_id,
            "memory-derived action reply",
            memory_receipt={
                "state": "bound",
                "memoryVersion": position.memory_version,
                "suppliedNoteIds": [NOTE_ID],
                "suppliedNoteCount": 1,
                "contentFree": True,
            },
        )

        def filter_rows(rows, *, memory_index_dir):
            self.assertEqual(memory_index_dir, self.bot_memory / "memory_index")
            return self.filtered_outcome(rows, position)

        with patch.object(
            fast_api,
            "filter_conversation_history_for_memory_exposure",
            side_effect=filter_rows,
        ):
            response = await fast_api.action_events_handler(
                SimpleNamespace(query={"after": "0"})
            )

        self.assertIsInstance(
            response,
            fast_api.MemoryGuardedJsonResponse,
        )
        self.assertEqual(
            response._memory_expected_position,
            position,
        )


if __name__ == "__main__":
    unittest.main()
