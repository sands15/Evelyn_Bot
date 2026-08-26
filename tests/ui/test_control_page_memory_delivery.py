from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core.control_page_memory_http import (  # noqa: E402
    control_page_memory_guarded_json_response,
)
from evelyn_core.control_page_search_runtime import (  # noqa: E402
    ControlPageSearchRuntimeDeps,
    answer_control_page_search_text_from_runtime,
)
from evelyn_core.control_page_state import (  # noqa: E402
    ControlPageChatLogStore,
    handle_control_page_chat_request,
)
from evelyn_core.control_page_text_runtime import (  # noqa: E402
    ControlPageTextRuntimeDeps,
    answer_control_page_text_from_runtime,
)
from evelyn_core.control_page_tool_runtime import (  # noqa: E402
    recent_control_page_history_for_router_from_runtime,
)
from evelyn_core.control_page_ui_runtime import (  # noqa: E402
    ControlPageUiRuntimeDeps,
    get_control_page_chat_log_from_runtime,
)
from evelyn_core.conversation_memory_exposure import (  # noqa: E402
    filter_conversation_history_for_memory_exposure,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
    capture_conversation_memory_receipt_ref,
    not_used_memory_receipt_ref,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MEMORY_INDEX_DB_NAME,
    current_memory_exposure_position,
    reset_memory_exposure_position,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


NOTE_ID = "concept-0123456789abcdef"
PRIVATE_CANARY = "CONTROL_PAGE_PRIVATE_MEMORY_CANARY"


async def async_noop(*_args, **_kwargs):
    return {}


def bound_receipt(version: int) -> dict[str, object]:
    return {
        "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
        "state": "bound",
        "memoryVersion": version,
        "suppliedNoteIds": [NOTE_ID],
        "suppliedNoteCount": 1,
        "contentFree": True,
    }


class ControlPageMemoryDeliveryTests(unittest.IsolatedAsyncioTestCase):
    @contextmanager
    def unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    @staticmethod
    def write_memory_version(index_dir: Path, version: int) -> None:
        index_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(index_dir / MEMORY_INDEX_DB_NAME))
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata "
                "(key TEXT PRIMARY KEY, value NOT NULL)"
            )
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("memory_version", str(version)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def ui_deps(
        store: ControlPageChatLogStore,
        index_dir: Path,
    ) -> ControlPageUiRuntimeDeps:
        return ControlPageUiRuntimeDeps(
            memory_index_dir=index_dir,
            control_page_host="127.0.0.1",
            control_page_port=8799,
            local_control_guild_id=0,
            local_control_guild_name="Local",
            control_page_welcome_fallback="welcome",
            clean_text=lambda value: value.strip(),
            sanitize_control_page_welcome_text_payload=(
                lambda text, fallback: text or fallback
            ),
            control_page_ui_command_store=object(),
            control_page_chat_log_store=store,
        )

    async def test_public_projection_is_fail_closed_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self.write_memory_version(index_dir, 3)
            store = ControlPageChatLogStore(limit=20)
            store.append(0, "user", "User", "question")
            store.append(0, "assistant", "Evelyn", "missing receipt")
            store.append(
                0,
                "assistant",
                "Evelyn",
                "static reply",
                not_used_memory_receipt_ref(memory_version=3),
            )
            store.append(
                0,
                "assistant",
                "Evelyn",
                PRIVATE_CANARY,
                bound_receipt(3),
            )
            deps = self.ui_deps(store, index_dir)

            with self.unconfigured_authenticity():
                reset_memory_exposure_position()
                current = get_control_page_chat_log_from_runtime(0, deps)

            self.assertEqual(
                [row["text"] for row in current],
                ["question", "static reply", PRIVATE_CANARY],
            )
            encoded = json.dumps(current, ensure_ascii=False)
            self.assertNotIn("memoryReceipt", encoded)
            self.assertNotIn("_memoryReceiptRef", encoded)
            self.assertNotIn(NOTE_ID, encoded)
            self.assertEqual(
                current_memory_exposure_position().supplied_note_ids,
                (NOTE_ID,),
            )

            self.write_memory_version(index_dir, 4)
            with self.unconfigured_authenticity():
                reset_memory_exposure_position()
                stale = get_control_page_chat_log_from_runtime(0, deps)

            stale_text = json.dumps(stale, ensure_ascii=False)
            self.assertNotIn(PRIVATE_CANARY, stale_text)
            self.assertNotIn("missing receipt", stale_text)
            self.assertNotIn(NOTE_ID, stale_text)
            self.assertIsNone(current_memory_exposure_position())

    async def test_unattributed_reply_is_rejected_before_public_log(self) -> None:
        log_rows: list[tuple[object, ...]] = []

        async def unattributed_input(_guild, _text: str) -> str:
            return PRIVATE_CANARY

        async def noop(*_args, **_kwargs):
            return {}

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            await handle_control_page_chat_request(
                {"text": "question"},
                discord_enabled=False,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: log_rows.append(args),
                handle_input=unattributed_input,
                ensure_minecraft_snapshot=noop,
                refresh_runtime_services=noop,
                build_state=noop,
            )

        self.assertEqual(
            [row[1] for row in log_rows],
            ["user"],
        )
        self.assertNotIn(PRIVATE_CANARY, str(log_rows))

    async def test_text_unattributed_reply_reaches_no_assistant_sink(
        self,
    ) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        memory_index_dir = Path(temporary) / "memory_index"
        ui_rows: list[tuple[object, ...]] = []
        persisted: list[tuple[object, ...]] = []
        tts: list[tuple[object, ...]] = []
        commits: list[tuple[object, ...]] = []
        state_lock = asyncio.Lock()

        async def ask_without_receipt(_text: str, **_kwargs) -> str:
            return PRIVATE_CANARY

        async def commit(*args):
            commits.append(args)
            return durable_continuity_status(len(commits))

        async def handle_input(guild, text: str) -> str:
            return await answer_control_page_text_from_runtime(
                guild,
                text,
                deps=ControlPageTextRuntimeDeps(
                    memory_index_dir=memory_index_dir,
                    effective_guild_id=lambda _guild: 0,
                    session_key_for_guild=lambda _guild_id: "control:0",
                    get_session_lock=lambda _key: state_lock,
                    begin_user_text_turn=lambda *_args, **_kwargs: (
                        SimpleNamespace(turn_id="turn-1", topic_id="topic-1")
                    ),
                    turn_scope_factory=lambda turn_id: SimpleNamespace(
                        turn_id=turn_id
                    ),
                    replace_room_turn_scope=lambda *_args: None,
                    attach_current_task=lambda _scope: "task",
                    monotonic=lambda: 1.0,
                    resolve_pending_proactive_question_for_turn=(
                        lambda *_args, **_kwargs: {"resolved": True}
                    ),
                    ask_llm_streaming=ask_without_receipt,
                    clean_text=lambda value: value.strip(),
                    strip_omnivoice_tags=lambda value: value,
                    session_state_snapshot=lambda _key: {},
                    maybe_append_proactive_question=(
                        lambda answer, **_kwargs: (answer, False)
                    ),
                    finish_assistant_text_turn=lambda *args, **_kwargs: (
                        persisted.append((*args, _kwargs))
                    ),
                    commit_session_continuity=commit,
                    log_voice_bottleneck_summary=lambda *_args, **_kwargs: None,
                    schedule_local_control_tts=lambda *args, **_kwargs: (
                        tts.append((*args, _kwargs))
                    ),
                    format_display_text=lambda value, **_kwargs: value,
                    fallback_answer_for=lambda _text: "fallback",
                    detach_task=lambda *_args: None,
                    clear_room_turn_scope=lambda *_args: None,
                    log=lambda *_args: None,
                ),
            )

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            await handle_control_page_chat_request(
                {"text": "question"},
                discord_enabled=False,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: ui_rows.append(args),
                handle_input=handle_input,
                ensure_minecraft_snapshot=async_noop,
                refresh_runtime_services=async_noop,
                build_state=async_noop,
            )

        self.assertEqual([row[1] for row in ui_rows], ["user"])
        self.assertEqual(persisted, [])
        self.assertEqual(commits, [("control:0", "turn-1")])
        self.assertEqual(tts, [])
        self.assertNotIn(PRIVATE_CANARY, str(ui_rows))

    async def test_search_unattributed_reply_reaches_no_assistant_sink(
        self,
    ) -> None:
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        memory_index_dir = Path(temporary) / "memory_index"
        ui_rows: list[tuple[object, ...]] = []
        persisted: list[tuple[object, ...]] = []
        active: list[tuple[object, ...]] = []
        tts: list[tuple[object, ...]] = []
        commits: list[tuple[object, ...]] = []
        state_lock = asyncio.Lock()

        async def execute_search(**_kwargs):
            return SimpleNamespace(answer_text=PRIVATE_CANARY)

        async def synthesize_without_receipt(**_kwargs):
            return PRIVATE_CANARY

        async def commit(*args):
            commits.append(args)
            return durable_continuity_status(len(commits))

        search_deps = ControlPageSearchRuntimeDeps(
            control_page_effective_guild_id=lambda _guild: 0,
            control_page_session_key=lambda _guild_id: "control:0",
            get_conversation_history=lambda **_kwargs: [],
            memory_index_dir=memory_index_dir,
            build_route_decision=lambda **kwargs: SimpleNamespace(**kwargs),
            monotonic=lambda: 1.0,
            execute_search_then_answer_action=execute_search,
            synthesize_tool_result_with_main_llm=synthesize_without_receipt,
            clean_text=lambda value: value.strip(),
            get_session_lock=lambda _key: state_lock,
            begin_user_text_turn=lambda *_args, **_kwargs: (
                SimpleNamespace(
                    turn_id="turn-1",
                    history=[
                        {"role": "user", "content": "question"}
                    ],
                )
            ),
            turn_scope_factory=lambda turn_id: SimpleNamespace(
                turn_id=turn_id
            ),
            replace_room_turn_scope=lambda *_args: None,
            get_room_turn_scope=lambda _key: None,
            attach_current_task=lambda _scope: "task",
            append_history=lambda *args, **kwargs: persisted.append(
                (*args, kwargs)
            ),
            mark_session_active=lambda *args, **kwargs: active.append(
                (*args, kwargs)
            ),
            commit_session_continuity=commit,
            active_conversation_text_sec=30.0,
            build_topic_id=lambda *_args: "topic",
            schedule_local_control_tts=lambda *args, **kwargs: tts.append(
                (*args, kwargs)
            ),
            format_display_text=lambda value, **_kwargs: value,
            fallback_answer_for=lambda _text: "fallback",
            detach_task=lambda *_args: None,
            clear_room_turn_scope=lambda *_args: None,
            log=lambda *_args, **_kwargs: None,
        )

        async def handle_input(guild, text: str) -> str:
            return await answer_control_page_search_text_from_runtime(
                guild,
                text,
                deps=search_deps,
            )

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            await handle_control_page_chat_request(
                {"text": "question"},
                discord_enabled=False,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: ui_rows.append(args),
                handle_input=handle_input,
                ensure_minecraft_snapshot=async_noop,
                refresh_runtime_services=async_noop,
                build_state=async_noop,
            )

        self.assertEqual([row[1] for row in ui_rows], ["user"])
        self.assertEqual(persisted, [])
        self.assertEqual(active, [])
        self.assertEqual(commits, [("control:0", "turn-1")])
        self.assertEqual(tts, [])
        self.assertNotIn(PRIVATE_CANARY, str(ui_rows))

    async def test_parallel_chat_receipts_are_task_local(self) -> None:
        rows: list[tuple[object, ...]] = []
        both_started = asyncio.Event()
        started = 0

        async def handle_input(_guild, text: str) -> str:
            nonlocal started
            memory_version = 11 if text == "first" else 22
            capture_conversation_memory_receipt_ref(
                not_used_memory_receipt_ref(
                    memory_version=memory_version
                )
            )
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            await asyncio.sleep(0)
            return f"reply:{text}"

        await asyncio.gather(
            handle_control_page_chat_request(
                {"text": "first"},
                discord_enabled=False,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: rows.append(args),
                handle_input=handle_input,
                ensure_minecraft_snapshot=async_noop,
                refresh_runtime_services=async_noop,
                build_state=async_noop,
            ),
            handle_control_page_chat_request(
                {"text": "second"},
                discord_enabled=False,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: rows.append(args),
                handle_input=handle_input,
                ensure_minecraft_snapshot=async_noop,
                refresh_runtime_services=async_noop,
                build_state=async_noop,
            ),
        )

        assistant_receipts = {
            str(row[3]): row[4]
            for row in rows
            if row[1] == "assistant"
        }
        self.assertEqual(
            assistant_receipts["reply:first"]["memoryVersion"],
            11,
        )
        self.assertEqual(
            assistant_receipts["reply:second"]["memoryVersion"],
            22,
        )

    async def test_guard_failure_before_prepare_is_exact_content_free_503(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self.write_memory_version(index_dir, 8)
            with self.unconfigured_authenticity():
                outcome = filter_conversation_history_for_memory_exposure(
                    [
                        {
                            "role": "assistant",
                            "text": PRIVATE_CANARY,
                            "_memoryReceiptRef": bound_receipt(8),
                        }
                    ],
                    memory_index_dir=index_dir,
                )
            position = outcome.memory_exposure_position
            self.assertIsNotNone(position)

            async def handler(_request: web.Request) -> web.StreamResponse:
                response = control_page_memory_guarded_json_response(
                    {"ok": True, "reply": PRIVATE_CANARY},
                    expected_position=position,
                    memory_index_dir=index_dir,
                )
                self.write_memory_version(index_dir, 9)
                return response

            app = web.Application()
            app.router.add_get("/state", handler)
            client = TestClient(TestServer(app))
            with self.unconfigured_authenticity():
                await client.start_server()
                try:
                    response = await client.get("/state")
                    payload = await response.json()
                finally:
                    await client.close()

            self.assertEqual(response.status, 503)
            self.assertEqual(
                payload,
                {
                    "ok": False,
                    "error": "memory_deletion_journal_integrity_failed",
                },
            )
            self.assertNotIn(PRIVATE_CANARY, json.dumps(payload))
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    async def test_tool_router_history_drops_stale_assistant_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            self.write_memory_version(index_dir, 2)
            deps = SimpleNamespace(
                memory_index_dir=index_dir,
                system_prompt="system",
                clean_text=lambda value: value.strip(),
                get_conversation_history=lambda **_kwargs: [
                    {"role": "user", "content": "current question"},
                    {
                        "role": "assistant",
                        "content": PRIVATE_CANARY,
                        "memoryReceiptRef": bound_receipt(2),
                    },
                ],
            )

            with self.unconfigured_authenticity():
                reset_memory_exposure_position()
                current = (
                    recent_control_page_history_for_router_from_runtime(
                        session_key="control:0",
                        guild_id=0,
                        deps=deps,
                    )
                )
                self.write_memory_version(index_dir, 3)
                reset_memory_exposure_position()
                stale = (
                    recent_control_page_history_for_router_from_runtime(
                        session_key="control:0",
                        guild_id=0,
                        deps=deps,
                    )
                )

            self.assertIn(PRIVATE_CANARY, current)
            self.assertNotIn(NOTE_ID, current)
            self.assertNotIn(PRIVATE_CANARY, stale)
            self.assertEqual(stale, "user: current question")


if __name__ == "__main__":
    unittest.main()
