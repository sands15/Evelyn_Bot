from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.guild_runtime_reset import (  # noqa: E402
    SEARCH_BACKGROUND_WORK_INFLIGHT,
    require_guild_runtime_reset_ready,
)
from evelyn_core.search_followup_runtime import (  # noqa: E402
    SearchFollowupRuntimeDeps,
    _channel_contains_followup,
    build_search_query_from_runtime,
    deliver_proactive_followup_from_runtime,
    recover_search_followups_from_runtime,
    run_search_followup_from_runtime,
    schedule_search_followup_from_runtime,
)
from evelyn_core.search_followup_recovery import (  # noqa: E402
    SearchFollowupRecoveryJournal,
)
from evelyn_core import search_followup_recovery as recovery_module  # noqa: E402
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


def delivery_receipt(text: str, message_id: int = 7001):
    return SimpleNamespace(
        message=SimpleNamespace(id=message_id, content=text)
    )


def record_delivery(
    delivered: list[str],
    text: str,
    message_id: int = 7001,
):
    delivered.append(text)
    return delivery_receipt(text, message_id)


def build_deps(
    *,
    memory_index_dir: Path | None = None,
    get_conversation_history_result=None,
    compact_summary: str = "메모 요약",
    history_calls: list[dict[str, int | str | None]] | None = None,
    summary_path_calls: list[int | None] | None = None,
    summary_read_calls: list[object] | None = None,
) -> SearchFollowupRuntimeDeps:
    history_calls = [] if history_calls is None else history_calls
    summary_path_calls = [] if summary_path_calls is None else summary_path_calls
    summary_read_calls = [] if summary_read_calls is None else summary_read_calls

    def _get_conversation_history(*, session_key: str | None, guild_id: int | None):
        history_calls.append({"session_key": session_key, "guild_id": guild_id})
        return get_conversation_history_result or []

    def _memory_summary_path(guild_id: int):
        summary_path_calls.append(guild_id)
        return f"summary:{guild_id}"

    def _read_text_file(path):
        summary_read_calls.append(path)
        return "raw summary"

    def _compact_working_summary(text: str) -> str:
        return f"compact::{text}::{compact_summary}"

    async def _commit_session_continuity(*_args):
        return durable_continuity_status(1)

    return SearchFollowupRuntimeDeps(
        memory_index_dir=(
            memory_index_dir
            if memory_index_dir is not None
            else Path(tempfile.gettempdir())
            / "evelyn-search-followup-runtime-tests-memory-index"
        ),
        bot=object(),
        discord_object_factory=lambda **kwargs: object(),
        session_followup_targets={},
        background_search_tasks={},
        inflight_search_tasks={},
        session_locks={},
        reply_slot_locks={},
        apply_runtime_mode=lambda runtime_mode="normal": {"skip_search_followup": False},
        parse_response_action_tag=lambda text: (None, text),
        answer_promises_search=lambda text: False,
        build_search_query=lambda *args, **kwargs: "",
        runtime_session_key=lambda *args, **kwargs: None,
        remember_session_followup_target=lambda *args, **kwargs: None,
        get_conversation_history=_get_conversation_history,
        memory_summary_path=_memory_summary_path,
        read_text_file=_read_text_file,
        compact_working_summary=_compact_working_summary,
        search_duckduckgo=lambda *args, **kwargs: [],
        answer_from_search_results=lambda *args, **kwargs: "",
        resolve_open_question_rows=lambda *args, **kwargs: 0,
        write_json_file=lambda *args, **kwargs: None,
        cognitive_state_path=lambda *args, **kwargs: None,
        send_discord_text=(
            lambda _channel, text, **_kwargs: asyncio.sleep(
                0,
                result=delivery_receipt(text),
            )
        ),
        format_display_text=lambda *args, **kwargs: "",
        speak_answer=lambda *args, **kwargs: None,
        current_turn_id=lambda *args, **kwargs: "turn",
        start_new_turn=lambda *args, **kwargs: "search-turn",
        append_history=lambda *args, **kwargs: None,
        mark_session_active=lambda *args, **kwargs: None,
        build_topic_id=lambda *args, **kwargs: "search-topic",
        active_conversation_text_sec=90.0,
        schedule_memory_update=lambda *args, **kwargs: None,
        create_turn_scoped_task=lambda *args, **kwargs: None,
        attach_current_task=lambda *args, **kwargs: None,
        detach_task=lambda *args, **kwargs: None,
        record_search_followup_queued=lambda: None,
        commit_session_continuity=_commit_session_continuity,
        continuity_status=lambda: durable_continuity_status(1),
        log=lambda *args, **kwargs: None,
    )


class SearchFollowupRuntimeTests(unittest.TestCase):
    def test_guild_reset_block_stops_followup_before_durable_or_send(self) -> None:
        gate_calls = 0
        side_effects: list[str] = []

        def guild_is_open(_guild_id: int) -> bool:
            nonlocal gate_calls
            gate_calls += 1
            return gate_calls == 1

        async def send_discord_text(*_args, **_kwargs) -> None:
            side_effects.append("send")

        async def commit_continuity(*_args, **_kwargs):
            side_effects.append("commit")
            return durable_continuity_status(1)

        deps = replace(
            build_deps(),
            bot=SimpleNamespace(
                get_channel=lambda _channel_id: SimpleNamespace()
            ),
            guild_is_open=guild_is_open,
            send_discord_text=send_discord_text,
            commit_session_continuity=commit_continuity,
            append_history=lambda *_args, **_kwargs: side_effects.append(
                "history"
            ),
            format_display_text=lambda text, **_kwargs: text,
            current_turn_id=lambda _session_key: "turn-source",
        )

        delivered = asyncio.run(
            deliver_proactive_followup_from_runtime(
                7,
                "query",
                "answer",
                deps=deps,
                session_key="guild:7:text:2:user:3",
                room_key="text:2",
                person_key="user:3",
                session_memory_key="guild:7:text:2:user:3",
                channel_id=2,
                source="text",
                source_turn_id="turn-source",
            )
        )

        self.assertFalse(delivered)
        self.assertEqual(side_effects, [])

    def test_voice_recovery_fails_closed_without_delivery_owner(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "검색 질의"},
                    {
                        "role": "assistant",
                        "content": "검색 결과 답변",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                intent_id = journal.begin(
                    guild_id=7,
                    session_key="session-1",
                    source="voice",
                    turn_id="turn-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=None,
                    reply_to_message_id=None,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                journal.begin_delivery_prepare(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    delivery_turn_id="turn",
                )
                journal.mark_delivery_ready(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    continuity_generation=5,
                )
                spoken: list[str] = []

                class Voice:
                    def is_connected(self):
                        return True

                voice = Voice()

                class Bot:
                    def get_guild(self, _guild_id):
                        return SimpleNamespace(voice_client=voice)

                async def speak(_voice, text, **_kwargs):
                    spoken.append(text)

                base = build_deps(
                    get_conversation_history_result=history
                )
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **base.__dict__,
                        "bot": Bot(),
                        "get_conversation_history": lambda **_kwargs: history,
                        "format_display_text": lambda text, **_kwargs: text,
                        "speak_answer": speak,
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(5)
                        ),
                    }
                )

                recovered = await recover_search_followups_from_runtime(
                    deps=deps
                )
                self.assertEqual(recovered["uncertain"], 1)
                self.assertEqual(
                    journal.pending()[0]["phase"],
                    "delivery_uncertain",
                )
                self.assertEqual(spoken, [])

        asyncio.run(scenario())

    def test_voice_recovery_keeps_unplayed_delivery_uncertain(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "검색 질의"},
                    {
                        "role": "assistant",
                        "content": "검색 결과 답변",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                intent_id = journal.begin(
                    guild_id=7,
                    session_key="session-1",
                    source="voice",
                    turn_id="turn-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=None,
                    reply_to_message_id=None,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                journal.begin_delivery_prepare(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    delivery_turn_id="turn",
                )
                journal.mark_delivery_ready(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    continuity_generation=5,
                )
                playback_metrics: list[dict] = []

                class Voice:
                    @staticmethod
                    def is_connected() -> bool:
                        return True

                class Bot:
                    @staticmethod
                    def get_guild(_guild_id):
                        return SimpleNamespace(voice_client=Voice())

                async def speak(_voice, _text, **kwargs):
                    metrics = kwargs["metrics"]
                    playback_metrics.append(metrics)
                    metrics.setdefault("meta", {})["playback_completed"] = False

                base = build_deps(
                    get_conversation_history_result=history
                )
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **base.__dict__,
                        "bot": Bot(),
                        "get_conversation_history": lambda **_kwargs: history,
                        "format_display_text": lambda text, **_kwargs: text,
                        "speak_answer": speak,
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(5)
                        ),
                    }
                )

                recovered = await recover_search_followups_from_runtime(
                    deps=deps
                )

                self.assertEqual(recovered["redelivered"], 0)
                self.assertEqual(recovered["uncertain"], 1)
                self.assertEqual(playback_metrics, [])
                self.assertEqual(
                    journal.pending()[0]["phase"],
                    "delivery_uncertain",
                )

        asyncio.run(scenario())

    def test_build_search_query_uses_provided_messages_without_history_lookup(self) -> None:
        history_calls: list[dict[str, int | str | None]] = []
        summary_path_calls: list[int | None] = []
        summary_read_calls: list[object] = []
        deps = build_deps(
            history_calls=history_calls,
            summary_path_calls=summary_path_calls,
            summary_read_calls=summary_read_calls,
        )
        query = build_search_query_from_runtime(
            None,
            "이건 긴 검색 질의입니다",
            messages=[{"role": "user", "content": "과거 문장"}, {"role": "assistant", "content": "답변"}],
            deps=deps,
        )

        self.assertEqual(query, "이건 긴 검색 질의입니다")
        self.assertEqual(history_calls, [])
        self.assertEqual(summary_path_calls, [])
        self.assertEqual(summary_read_calls, [])

    def test_build_search_query_does_not_read_history_or_summary_when_messages_missing(self) -> None:
        history_calls: list[dict[str, int | str | None]] = []
        summary_path_calls: list[int | None] = []
        summary_read_calls: list[object] = []
        deps = build_deps(
            get_conversation_history_result=[{"role": "user", "content": "오픈AI"}],
            compact_summary="요약 텍스트",
            history_calls=history_calls,
            summary_path_calls=summary_path_calls,
            summary_read_calls=summary_read_calls,
        )
        query = build_search_query_from_runtime(
            42,
            "짧음",
            session_key="session-42",
            deps=deps,
        )

        self.assertEqual(query, "짧음")
        self.assertEqual(history_calls, [])
        self.assertEqual(summary_path_calls, [])
        self.assertEqual(summary_read_calls, [])

    def test_delivered_text_gets_a_dedicated_durable_turn(self) -> None:
        events: list[str] = []

        class Channel:
            async def send(self, *_args, **_kwargs):
                return None

        class Voice:
            def is_connected(self):
                return True

        class Bot:
            def get_guild(self, _guild_id):
                return type("Guild", (), {"voice_client": Voice()})()

            def get_channel(self, _channel_id):
                return Channel()

        async def send(_channel, text, **_kwargs):
            events.append("send")
            return delivery_receipt(text)

        commit_targets: list[tuple[object, ...]] = []

        async def commit(*args):
            events.append("commit")
            commit_targets.append(args)
            return durable_continuity_status(2)

        async def speak(*_args, **_kwargs):
            events.append("voice")
            raise RuntimeError("optional voice failed")

        deps = build_deps()
        deps = SearchFollowupRuntimeDeps(
            **{
                **deps.__dict__,
                "bot": Bot(),
                "send_discord_text": send,
                "append_history": lambda *_args, **_kwargs: events.append(
                    "history"
                ),
                "schedule_memory_update": lambda *_args, **_kwargs: events.append(
                    "memory"
                ),
                "commit_session_continuity": commit,
                "speak_answer": speak,
            }
        )

        asyncio.run(
            deliver_proactive_followup_from_runtime(
                1,
                "query",
                "answer",
                deps=deps,
                session_key="guild:1:text:20:thread:20:user:3",
                room_key=None,
                person_key=None,
                session_memory_key=None,
                channel_id=20,
                source="search",
                source_turn_id="turn",
            )
        )

        self.assertEqual(
            events,
            ["send", "history", "commit", "memory"],
        )
        self.assertEqual(
            commit_targets,
            [("guild:1:text:20:thread:20:user:3", "search-turn")],
        )
        self.assertIn(
            "guild:1:reply:text:20:thread:20",
            deps.reply_slot_locks,
        )

    def test_cancelled_commit_holds_reply_and_session_locks_until_drain(
        self,
    ) -> None:
        async def scenario() -> None:
            commit_started = threading.Event()
            release_commit = threading.Event()
            memory: list[str] = []

            class Bot:
                @staticmethod
                def get_channel(_channel_id):
                    return SimpleNamespace(send=object())

            async def commit(*_args):
                commit_started.set()
                if not await asyncio.to_thread(
                    release_commit.wait,
                    2.0,
                ):
                    raise TimeoutError("test_commit_release_timed_out")
                return durable_continuity_status(2)

            deps = replace(
                build_deps(),
                bot=Bot(),
                send_discord_text=(
                    lambda _channel, text, **_kwargs: asyncio.sleep(
                        0,
                        result=delivery_receipt(text),
                    )
                ),
                append_history=lambda *_args, **_kwargs: None,
                commit_session_continuity=commit,
                schedule_memory_update=lambda *_args, **_kwargs: memory.append(
                    "memory"
                ),
            )
            answer_task = asyncio.create_task(
                deliver_proactive_followup_from_runtime(
                    1,
                    "query",
                    "answer",
                    deps=deps,
                    session_key=(
                        "guild:1:text:20:thread:20:user:3"
                    ),
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=20,
                    source="search",
                    source_turn_id="turn",
                )
            )
            successor_acquired = asyncio.Event()
            successor_task: asyncio.Task[None] | None = None
            reply_key = "guild:1:reply:text:20:thread:20"
            session_key = "guild:1:text:20:thread:20:user:3"
            try:
                self.assertTrue(
                    await asyncio.to_thread(
                        commit_started.wait,
                        1.0,
                    )
                )
                answer_task.cancel()

                async def acquire_successor() -> None:
                    async with deps.reply_slot_locks[reply_key]:
                        async with deps.session_locks[session_key]:
                            successor_acquired.set()

                successor_task = asyncio.create_task(acquire_successor())
                await asyncio.sleep(0)
                self.assertFalse(answer_task.done())
                self.assertTrue(deps.reply_slot_locks[reply_key].locked())
                self.assertTrue(deps.session_locks[session_key].locked())
                self.assertFalse(successor_acquired.is_set())
                self.assertEqual(memory, [])

                release_commit.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(answer_task, timeout=2.0)
                await asyncio.wait_for(successor_task, timeout=1.0)
                self.assertEqual(memory, [])
            finally:
                release_commit.set()
                answer_task.cancel()
                pending = [answer_task]
                if successor_task is not None:
                    pending.append(successor_task)
                await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run(scenario())

    def test_cancelled_send_drains_physical_child_before_unlock(
        self,
    ) -> None:
        async def run_case(*, recoverable: bool) -> None:
            with tempfile.TemporaryDirectory() as temporary:
                send_started = asyncio.Event()
                release_send = asyncio.Event()
                physical_done = asyncio.Event()
                effects: list[str] = []
                physical_tasks: list[asyncio.Task] = []
                recovery = (
                    SearchFollowupRecoveryJournal(
                        path=Path(temporary) / "recovery.json"
                    )
                    if recoverable
                    else None
                )
                intent_id = (
                    recovery.begin(
                        guild_id=1,
                        session_key="guild:1:text:20:thread:20:user:3",
                        source="text",
                        turn_id="turn",
                        room_key="text:20",
                        person_key="user:3",
                        session_memory_key=(
                            "guild:1:text:20:thread:20:user:3"
                        ),
                        channel_id=20,
                        reply_to_message_id=100,
                        request_user_text="검색해줘",
                        request_answer_text="찾아보고 알려줄게",
                        query="query",
                        continuity_generation=1,
                    )
                    if recovery is not None
                    else None
                )

                class Bot:
                    @staticmethod
                    def get_channel(_channel_id):
                        return SimpleNamespace(send=object())

                async def send(_channel, text, **_kwargs):
                    async def physical_send():
                        send_started.set()
                        await release_send.wait()
                        physical_done.set()
                        return delivery_receipt(text)

                    child = asyncio.create_task(physical_send())
                    physical_tasks.append(child)
                    return await asyncio.shield(child)

                deps = replace(
                    build_deps(),
                    bot=Bot(),
                    send_discord_text=send,
                    append_history=lambda *_args, **_kwargs: effects.append(
                        "history"
                    ),
                    commit_session_continuity=lambda *_args: (
                        asyncio.sleep(
                            0,
                            result=durable_continuity_status(2),
                        )
                    ),
                    schedule_memory_update=lambda *_args, **_kwargs: (
                        effects.append("memory")
                    ),
                    search_followup_recovery=recovery,
                )
                answer_task = asyncio.create_task(
                    deliver_proactive_followup_from_runtime(
                        1,
                        "query",
                        "answer",
                        deps=deps,
                        session_key=(
                            "guild:1:text:20:thread:20:user:3"
                        ),
                        room_key="text:20",
                        person_key="user:3",
                        session_memory_key=(
                            "guild:1:text:20:thread:20:user:3"
                        ),
                        channel_id=20,
                        reply_to_message_id=100,
                        source="search-followup-text",
                        source_turn_id="turn",
                        recovery_intent_id=intent_id,
                    )
                )
                successor_acquired = asyncio.Event()
                successor_task: asyncio.Task[None] | None = None
                reply_key = "guild:1:reply:text:20:thread:20"
                session_key = "guild:1:text:20:thread:20:user:3"
                try:
                    await asyncio.wait_for(
                        send_started.wait(),
                        timeout=1.0,
                    )
                    answer_task.cancel("first-cancel")
                    await asyncio.sleep(0)
                    answer_task.cancel("second-cancel")

                    async def acquire_successor() -> None:
                        async with deps.reply_slot_locks[reply_key]:
                            async with deps.session_locks[session_key]:
                                successor_acquired.set()

                    successor_task = asyncio.create_task(
                        acquire_successor()
                    )
                    await asyncio.sleep(0)
                    self.assertFalse(answer_task.done())
                    self.assertTrue(
                        deps.reply_slot_locks[reply_key].locked()
                    )
                    self.assertTrue(
                        deps.session_locks[session_key].locked()
                    )
                    self.assertFalse(successor_acquired.is_set())
                    self.assertFalse(physical_done.is_set())
                    self.assertEqual(effects, [])

                    release_send.set()
                    try:
                        await asyncio.wait_for(
                            answer_task,
                            timeout=2.0,
                        )
                    except asyncio.CancelledError as exc:
                        self.assertEqual(
                            exc.args,
                            ("first-cancel",),
                        )
                    else:
                        self.fail("send owner did not propagate cancellation")
                    self.assertTrue(physical_done.is_set())
                    await asyncio.wait_for(
                        successor_task,
                        timeout=1.0,
                    )
                    if recovery is not None:
                        self.assertEqual(
                            recovery.pending()[0]["phase"],
                            "delivery_uncertain",
                        )
                finally:
                    release_send.set()
                    answer_task.cancel()
                    pending = [answer_task, *physical_tasks]
                    if successor_task is not None:
                        pending.append(successor_task)
                    await asyncio.gather(
                        *pending,
                        return_exceptions=True,
                    )

        async def scenario() -> None:
            await run_case(recoverable=False)
            await run_case(recoverable=True)

        asyncio.run(scenario())

    def test_durable_search_delivery_survives_optional_memory_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            events: list[str] = []
            logs: list[str] = []
            recovery = SearchFollowupRecoveryJournal(
                path=Path(temporary) / "search-followups.json",
            )
            intent_id = recovery.begin(
                guild_id=1,
                session_key="guild:1:text:10:user:3",
                source="text",
                turn_id="source-turn",
                room_key="text:10",
                person_key="user:3",
                session_memory_key="guild:1:text:10:user:3:user:3",
                channel_id=10,
                reply_to_message_id=100,
                request_user_text="검색해줘",
                request_answer_text="찾아보고 알려줄게",
                query="검색 질의",
                continuity_generation=1,
            )

            class Bot:
                @staticmethod
                def get_channel(_channel_id):
                    return SimpleNamespace(send=object())

            async def commit(*_args):
                events.append("commit")
                return durable_continuity_status(2)

            async def send(*_args, **_kwargs):
                self.assertEqual(events, [])
                events.append("send")
                return delivery_receipt("검색 결과")

            def fail_memory(*_args, **_kwargs):
                events.append("memory")
                raise OSError("PRIVATE_MEMORY_CANARY")

            base = build_deps()
            deps = SearchFollowupRuntimeDeps(
                **{
                    **base.__dict__,
                    "bot": Bot(),
                    "current_turn_id": lambda _key: "source-turn",
                    "append_history": lambda *_args, **_kwargs: events.append(
                        "history"
                    ),
                    "commit_session_continuity": commit,
                    "schedule_memory_update": fail_memory,
                    "send_discord_text": send,
                    "format_display_text": lambda text, **_kwargs: text,
                    "search_followup_recovery": recovery,
                    "log": lambda message: logs.append(str(message)),
                }
            )

            delivered = asyncio.run(
                deliver_proactive_followup_from_runtime(
                    1,
                    "검색 질의",
                    "검색 결과",
                    deps=deps,
                    session_key="guild:1:text:10:user:3",
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key=(
                        "guild:1:text:10:user:3:user:3"
                    ),
                    channel_id=10,
                    reply_to_message_id=100,
                    source="search-followup-text",
                    source_turn_id="source-turn",
                    recovery_intent_id=intent_id,
                )
            )

            self.assertTrue(delivered)
            self.assertEqual(
                events,
                ["send", "history", "commit", "memory"],
            )
            self.assertEqual(recovery.pending(), [])
            self.assertIn("errorType=OSError", str(logs))
            self.assertNotIn("PRIVATE_MEMORY_CANARY", str(logs))

    def test_failed_or_ambiguous_send_has_no_canonical_projection(
        self,
    ) -> None:
        class DefinitiveSendFailure(RuntimeError):
            status = 403

        for send_error in (
            DefinitiveSendFailure("definitive"),
            TimeoutError("ambiguous"),
        ):
            with self.subTest(error_type=type(send_error).__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    events: list[str] = []
                    recovery = SearchFollowupRecoveryJournal(
                        path=Path(temporary) / "search-followups.json",
                    )
                    intent_id = recovery.begin(
                        guild_id=1,
                        session_key="guild:1:text:10:user:3",
                        source="text",
                        turn_id="source-turn",
                        room_key="text:10",
                        person_key="user:3",
                        session_memory_key="guild:1:text:10:user:3",
                        channel_id=10,
                        reply_to_message_id=100,
                        request_user_text="검색해줘",
                        request_answer_text="찾아보고 알려줄게",
                        query="검색 질의",
                        continuity_generation=1,
                    )

                    class Bot:
                        @staticmethod
                        def get_channel(_channel_id):
                            return SimpleNamespace(send=object())

                    async def send(*_args, **_kwargs):
                        events.append("send")
                        raise send_error

                    async def commit(*_args):
                        events.append("commit")
                        return durable_continuity_status(2)

                    deps = replace(
                        build_deps(),
                        bot=Bot(),
                        current_turn_id=lambda _key: "source-turn",
                        append_history=lambda *_args, **_kwargs: events.append(
                            "history"
                        ),
                        mark_session_active=lambda *_args, **_kwargs: events.append(
                            "active"
                        ),
                        commit_session_continuity=commit,
                        schedule_memory_update=lambda *_args, **_kwargs: events.append(
                            "memory"
                        ),
                        resolve_open_question_rows=lambda *_args, **_kwargs: events.append(
                            "question"
                        )
                        or 0,
                        write_json_file=lambda *_args, **_kwargs: events.append(
                            "cognitive"
                        ),
                        send_discord_text=send,
                        format_display_text=lambda text, **_kwargs: text,
                        search_followup_recovery=recovery,
                    )

                    with self.assertRaises(type(send_error)):
                        asyncio.run(
                            deliver_proactive_followup_from_runtime(
                                1,
                                "검색 질의",
                                "검색 결과",
                                deps=deps,
                                session_key="guild:1:text:10:user:3",
                                room_key="text:10",
                                person_key="user:3",
                                session_memory_key=(
                                    "guild:1:text:10:user:3"
                                ),
                                channel_id=10,
                                reply_to_message_id=100,
                                source="search-followup-text",
                                source_turn_id="source-turn",
                                completed_state={"action": "answer"},
                                recovery_intent_id=intent_id,
                            )
                        )

                    self.assertEqual(events, ["send"])
                    self.assertEqual(
                        recovery.pending()[0]["phase"],
                        "delivery_uncertain",
                    )

    def test_unanchored_send_requires_exact_receipt_before_projection(
        self,
    ) -> None:
        events: list[str] = []

        class Bot:
            @staticmethod
            def get_channel(_channel_id):
                return SimpleNamespace(send=object())

        deps = replace(
            build_deps(),
            bot=Bot(),
            send_discord_text=lambda *_args, **_kwargs: asyncio.sleep(
                0,
                result=events.append("send"),
            ),
            append_history=lambda *_args, **_kwargs: events.append(
                "history"
            ),
            mark_session_active=lambda *_args, **_kwargs: events.append(
                "active"
            ),
            commit_session_continuity=lambda *_args: asyncio.sleep(
                0,
                result=events.append("commit")
                or durable_continuity_status(2),
            ),
            schedule_memory_update=lambda *_args, **_kwargs: events.append(
                "memory"
            ),
            format_display_text=lambda text, **_kwargs: text,
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^search_followup_delivery_receipt_invalid$",
        ):
            asyncio.run(
                deliver_proactive_followup_from_runtime(
                    1,
                    "query",
                    "answer",
                    deps=deps,
                    session_key="guild:1:text:10:user:3",
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key="guild:1:text:10:user:3",
                    channel_id=10,
                    source="search-followup-text",
                    source_turn_id="turn",
                )
            )
        self.assertEqual(events, ["send"])

    def test_unowned_voice_followup_is_not_delivered_or_committed(self) -> None:
        events: list[str] = []
        playback_metrics: list[dict] = []

        class Bot:
            @staticmethod
            def get_channel(_channel_id):
                return SimpleNamespace(send=object())

        async def speak(_voice, _text, **kwargs):
            metrics = kwargs["metrics"]
            playback_metrics.append(metrics)
            metrics.setdefault("meta", {})["playback_completed"] = False

        async def commit(*_args):
            events.append("commit")
            return durable_continuity_status(1)

        base = build_deps()
        deps = SearchFollowupRuntimeDeps(
            **{
                **base.__dict__,
                "bot": Bot(),
                "speak_answer": speak,
                "append_history": lambda *_args, **_kwargs: events.append(
                    "history"
                ),
                "schedule_memory_update": lambda *_args, **_kwargs: events.append(
                    "memory"
                ),
                "commit_session_continuity": commit,
            }
        )

        delivered = asyncio.run(
            deliver_proactive_followup_from_runtime(
                1,
                "query",
                "answer",
                deps=deps,
                session_key="guild:1:text:10:user:3",
                room_key=None,
                person_key=None,
                session_memory_key=None,
                channel_id=10,
                source="search-followup-voice",
                source_turn_id="turn",
            )
        )

        self.assertFalse(delivered)
        self.assertEqual(playback_metrics, [])
        self.assertEqual(events, [])

    def test_partial_commit_status_logs_failure_and_keeps_turn(
        self,
    ) -> None:
        events: list[str] = []
        logs: list[tuple] = []
        private = (
            "Bearer followup-continuity-secret "
            "https://internal.example/private"
        )

        class Channel:
            async def send(self, *_args, **_kwargs):
                return None

        class Bot:
            def get_guild(self, _guild_id):
                return type(
                    "Guild",
                    (),
                    {"voice_client": None},
                )()

            def get_channel(self, _channel_id):
                return Channel()

        async def partial_commit(*_args):
            events.append("commit")
            return {
                "state": "ready",
                "privateMessage": private,
            }

        async def send(_channel, text, **_kwargs):
            return delivery_receipt(text)

        deps = build_deps()
        deps = SearchFollowupRuntimeDeps(
            **{
                **deps.__dict__,
                "bot": Bot(),
                "send_discord_text": send,
                "append_history": (
                    lambda *_args, **_kwargs: events.append(
                        "history"
                    )
                ),
                "schedule_memory_update": (
                    lambda *_args, **_kwargs: events.append(
                        "memory"
                    )
                ),
                "commit_session_continuity": partial_commit,
                "log": (
                    lambda *args, **_kwargs: logs.append(args)
                ),
            }
        )

        with self.assertRaises(Exception):
            asyncio.run(
                deliver_proactive_followup_from_runtime(
                    1,
                    "query",
                    "answer",
                    deps=deps,
                    session_key="guild:1:text:10:user:3",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=10,
                    source="search",
                    source_turn_id="turn",
                )
            )

        self.assertEqual(events, ["history", "commit"])
        rendered = str(logs)
        self.assertIn(
            "ConversationContinuityCommitError",
            rendered,
        )
        self.assertNotIn("followup-continuity-secret", rendered)

    def test_direct_commit_failure_restores_exact_source_pair(
        self,
    ) -> None:
        async def scenario(root: Path) -> None:
            session_key = "guild:1:text:10:user:3"
            store = SessionStateStore.create_empty()
            store.start_new_turn(
                session_key,
                turn_id="source-turn",
            )
            store.append_history(
                session_key,
                "SOURCE_QUESTION",
                "SOURCE_PROMISE",
                system_prompt="system",
                max_history_items=12,
                memory_receipt=not_used_memory_receipt_ref(),
            )
            store.mark_active(
                session_key,
                user_id=3,
                speaker="assistant",
                active_conversation_awaiting_reply_sec=300.0,
            )
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
            )
            checkpoint.commit_completed_turn(
                session_key,
                "source-turn",
            )
            sent: list[str] = []

            def append_history(key, user, assistant, **kwargs):
                store.append_history(
                    key,
                    user,
                    assistant,
                    system_prompt="system",
                    max_history_items=12,
                    guild_id=kwargs.get("guild_id"),
                    memory_receipt=kwargs.get("memory_receipt"),
                )

            def mark_active(key, **kwargs):
                store.mark_active(
                    key,
                    active_conversation_awaiting_reply_sec=300.0,
                    **kwargs,
                )

            async def partial_commit(*_args):
                return {"state": "ready"}

            deps = replace(
                build_deps(),
                bot=SimpleNamespace(
                    get_channel=lambda _channel_id: SimpleNamespace(
                        send=object()
                    )
                ),
                current_turn_id=store.current_turn_id,
                start_new_turn=store.start_new_turn,
                append_history=append_history,
                mark_session_active=mark_active,
                send_discord_text=lambda _channel, text, **_kwargs: (
                    asyncio.sleep(
                        0,
                        result=record_delivery(sent, text),
                    )
                ),
                format_display_text=lambda text, **_kwargs: text,
                commit_session_continuity=partial_commit,
            )
            with self.assertRaises(Exception):
                await deliver_proactive_followup_from_runtime(
                    1,
                    "DELIVERY_QUERY",
                    "DELIVERY_ANSWER",
                    deps=deps,
                    session_key=session_key,
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key=session_key,
                    channel_id=10,
                    source="search",
                    source_turn_id="source-turn",
                )
            self.assertEqual(sent, ["DELIVERY_ANSWER"])

            restored = SessionStateStore.create_empty()
            status = SessionContinuityCheckpoint(
                store=restored,
                checkpoint_path=root / "active.json",
                status_path=root / "restored-status.json",
                system_prompt="system",
            ).restore()
            history = restored.get_conversation_history(
                system_prompt="system",
                session_key=session_key,
            )
            self.assertEqual(status["state"], "restored")
            self.assertEqual(
                restored.current_turn_id(session_key),
                "source-turn",
            )
            self.assertEqual(
                [
                    (row["role"], row["content"])
                    for row in history[-2:]
                ],
                [
                    ("user", "SOURCE_QUESTION"),
                    ("assistant", "SOURCE_PROMISE"),
                ],
            )

        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(scenario(Path(temporary)))

    def test_successor_commit_adopts_succeeded_delivery_pair(
        self,
    ) -> None:
        async def scenario(root: Path) -> None:
            session_key = "guild:1:text:10:user:3"
            store = SessionStateStore.create_empty()
            store.start_new_turn(session_key, turn_id="source-turn")
            store.append_history(
                session_key,
                "SOURCE_QUESTION",
                "SOURCE_PROMISE",
                system_prompt="system",
                max_history_items=12,
                memory_receipt=not_used_memory_receipt_ref(),
            )
            store.mark_active(
                session_key,
                user_id=3,
                speaker="assistant",
                active_conversation_awaiting_reply_sec=300.0,
            )
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
            )
            source_status = checkpoint.commit_completed_turn(
                session_key,
                "source-turn",
            )
            recovery = SearchFollowupRecoveryJournal(
                path=root / "search-followups.json"
            )
            intent_id = recovery.begin(
                guild_id=1,
                session_key=session_key,
                source="text",
                turn_id="source-turn",
                room_key="text:10",
                person_key="user:3",
                session_memory_key=session_key,
                channel_id=10,
                reply_to_message_id=100,
                request_user_text="SOURCE_QUESTION",
                request_answer_text="SOURCE_PROMISE",
                query="DELIVERY_QUERY",
                continuity_generation=int(
                    source_status["checkpointGeneration"]
                ),
            )
            sends: list[str] = []

            def append_history(key, user, assistant, **kwargs):
                store.append_history(
                    key,
                    user,
                    assistant,
                    system_prompt="system",
                    max_history_items=12,
                    guild_id=kwargs.get("guild_id"),
                    memory_receipt=kwargs.get("memory_receipt"),
                )

            def mark_active(key, **kwargs):
                store.mark_active(
                    key,
                    active_conversation_awaiting_reply_sec=300.0,
                    **kwargs,
                )

            async def fail_delivery_commit(*_args):
                raise OSError("delivery checkpoint failed")

            async def send(_channel, text, **_kwargs):
                sends.append(text)
                return delivery_receipt(text, 8001)

            base = build_deps(memory_index_dir=root / "memory-index")
            deps = replace(
                base,
                bot=SimpleNamespace(
                    get_channel=lambda _channel_id: SimpleNamespace(
                        send=object()
                    )
                ),
                current_turn_id=store.current_turn_id,
                start_new_turn=store.start_new_turn,
                append_history=append_history,
                mark_session_active=mark_active,
                send_discord_text=send,
                format_display_text=lambda text, **_kwargs: text,
                commit_session_continuity=fail_delivery_commit,
                search_followup_recovery=recovery,
            )
            with self.assertRaises(OSError):
                await deliver_proactive_followup_from_runtime(
                    1,
                    "DELIVERY_QUERY",
                    "DELIVERY_ANSWER",
                    deps=deps,
                    session_key=session_key,
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key=session_key,
                    channel_id=10,
                    reply_to_message_id=100,
                    source="search-followup-text",
                    source_turn_id="source-turn",
                    recovery_intent_id=intent_id,
                )
            self.assertEqual(
                recovery.pending()[0]["phase"],
                "delivery_succeeded",
            )

            store.start_new_turn(
                session_key,
                turn_id="successor-turn",
            )
            store.append_history(
                session_key,
                "SUCCESSOR_QUESTION",
                "SUCCESSOR_ANSWER",
                system_prompt="system",
                max_history_items=12,
                memory_receipt=not_used_memory_receipt_ref(),
            )
            store.mark_active(
                session_key,
                user_id=3,
                speaker="assistant",
                active_conversation_awaiting_reply_sec=300.0,
            )
            checkpoint.commit_completed_turn(
                session_key,
                "successor-turn",
            )
            successor_generation = int(
                checkpoint.status()["checkpointGeneration"]
            )

            projections: list[str] = []
            recovery_deps = replace(
                deps,
                get_conversation_history=(
                    lambda **kwargs: store.get_conversation_history(
                        system_prompt="system",
                        **kwargs,
                    )
                ),
                build_search_query=lambda *_args, **_kwargs: (
                    "DELIVERY_QUERY"
                ),
                continuity_status=checkpoint.status,
                commit_session_continuity=(
                    checkpoint.commit_completed_turn_async
                ),
                send_discord_text=(
                    lambda *_args, **_kwargs: asyncio.sleep(
                        0,
                        result=sends.append("UNEXPECTED_RESEND"),
                    )
                ),
                schedule_memory_update=(
                    lambda *_args, **_kwargs: projections.append(
                        "memory"
                    )
                ),
                resolve_open_question_rows=(
                    lambda *_args, **_kwargs: projections.append(
                        "question"
                    )
                    or 0
                ),
                write_json_file=(
                    lambda *_args, **_kwargs: projections.append(
                        "cognitive"
                    )
                ),
            )
            recovered = await recover_search_followups_from_runtime(
                deps=recovery_deps
            )
            self.assertEqual(recovered["verified"], 1)
            self.assertEqual(recovery.pending(), [])
            self.assertEqual(sends, ["DELIVERY_ANSWER"])
            self.assertEqual(
                checkpoint.status()["checkpointGeneration"],
                successor_generation,
            )
            self.assertEqual(projections.count("memory"), 1)
            self.assertEqual(projections.count("question"), 4)
            self.assertEqual(projections.count("cognitive"), 4)

            await recover_search_followups_from_runtime(
                deps=recovery_deps
            )
            self.assertEqual(projections.count("memory"), 1)
            self.assertEqual(
                store.current_turn_id(session_key),
                "successor-turn",
            )

        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(scenario(Path(temporary)))

    def test_pre_delivery_generation_blocks_stale_other_session_adoption(
        self,
    ) -> None:
        async def scenario(root: Path) -> None:
            session_key = "guild:1:text:10:user:3"
            other_key = "guild:2:text:20:user:4"
            store = SessionStateStore.create_empty()

            def append_turn(
                key: str,
                turn_id: str,
                user: str,
                assistant: str,
                user_id: int,
            ) -> None:
                store.start_new_turn(key, turn_id=turn_id)
                store.append_history(
                    key,
                    user,
                    assistant,
                    system_prompt="system",
                    max_history_items=12,
                    memory_receipt=not_used_memory_receipt_ref(),
                )
                store.mark_active(
                    key,
                    user_id=user_id,
                    speaker="assistant",
                    active_conversation_awaiting_reply_sec=300.0,
                )

            append_turn(
                session_key,
                "source-turn",
                "SOURCE_QUESTION",
                "SOURCE_PROMISE",
                3,
            )
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
            )
            source_status = checkpoint.commit_completed_turn(
                session_key,
                "source-turn",
            )
            append_turn(
                other_key,
                "other-turn",
                "OTHER_QUESTION",
                "OTHER_ANSWER",
                4,
            )
            checkpoint.commit_completed_turn(other_key, "other-turn")
            pre_delivery_generation = int(
                checkpoint.status()["checkpointGeneration"]
            )
            recovery = SearchFollowupRecoveryJournal(
                path=root / "search-followups.json"
            )
            intent_id = recovery.begin(
                guild_id=1,
                session_key=session_key,
                source="text",
                turn_id="source-turn",
                room_key="text:10",
                person_key="user:3",
                session_memory_key=session_key,
                channel_id=10,
                reply_to_message_id=100,
                request_user_text="SOURCE_QUESTION",
                request_answer_text="SOURCE_PROMISE",
                query="DELIVERY_QUERY",
                continuity_generation=int(
                    source_status["checkpointGeneration"]
                ),
            )

            def append_history(key, user, assistant, **kwargs):
                store.append_history(
                    key,
                    user,
                    assistant,
                    system_prompt="system",
                    max_history_items=12,
                    guild_id=kwargs.get("guild_id"),
                    memory_receipt=kwargs.get("memory_receipt"),
                )

            def mark_active(key, **kwargs):
                store.mark_active(
                    key,
                    active_conversation_awaiting_reply_sec=300.0,
                    **kwargs,
                )

            async def fail_delivery_commit(*_args):
                raise OSError("delivery checkpoint failed")

            base = build_deps(memory_index_dir=root / "memory-index")
            deps = replace(
                base,
                bot=SimpleNamespace(
                    get_channel=lambda _channel_id: SimpleNamespace(
                        send=object()
                    )
                ),
                current_turn_id=store.current_turn_id,
                start_new_turn=store.start_new_turn,
                append_history=append_history,
                mark_session_active=mark_active,
                send_discord_text=(
                    lambda _channel, text, **_kwargs: asyncio.sleep(
                        0,
                        result=delivery_receipt(text, 8001),
                    )
                ),
                format_display_text=lambda text, **_kwargs: text,
                commit_session_continuity=fail_delivery_commit,
                search_followup_recovery=recovery,
                continuity_status=checkpoint.status,
            )
            with self.assertRaises(OSError):
                await deliver_proactive_followup_from_runtime(
                    1,
                    "DELIVERY_QUERY",
                    "DELIVERY_ANSWER",
                    deps=deps,
                    session_key=session_key,
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key=session_key,
                    channel_id=10,
                    reply_to_message_id=100,
                    source="search-followup-text",
                    source_turn_id="source-turn",
                    recovery_intent_id=intent_id,
                )
            self.assertEqual(
                recovery.pending()[0]["deliveryGeneration"],
                pre_delivery_generation,
            )

            recovery_commits: list[str] = []

            async def recovery_commit(*args):
                recovery_commits.append(str(args[1]))
                return await checkpoint.commit_completed_turn_async(*args)

            recovery_deps = replace(
                deps,
                get_conversation_history=(
                    lambda **kwargs: store.get_conversation_history(
                        system_prompt="system",
                        **kwargs,
                    )
                ),
                build_search_query=lambda *_args, **_kwargs: (
                    "DELIVERY_QUERY"
                ),
                commit_session_continuity=recovery_commit,
                schedule_memory_update=lambda *_args, **_kwargs: None,
            )
            recovered = await recover_search_followups_from_runtime(
                deps=recovery_deps
            )
            restored = SessionStateStore.create_empty()
            SessionContinuityCheckpoint(
                store=restored,
                checkpoint_path=root / "active.json",
                status_path=root / "restored-status.json",
                system_prompt="system",
            ).restore()
            restored_history = restored.get_conversation_history(
                system_prompt="system",
                session_key=session_key,
                guild_id=1,
            )

            self.assertEqual(recovered["verified"], 1)
            self.assertEqual(len(recovery_commits), 1)
            self.assertTrue(
                any(
                    row.get("content") == "DELIVERY_ANSWER"
                    for row in restored_history
                )
            )
            self.assertEqual(recovery.pending(), [])

        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(scenario(Path(temporary)))

    def test_untrusted_delivery_anchor_or_duplicate_pair_fails_closed(
        self,
    ) -> None:
        async def run_case(*, duplicate_pair: bool) -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                recovery = SearchFollowupRecoveryJournal(
                    path=root / "search-followups.json"
                )
                intent_id = recovery.begin(
                    guild_id=1,
                    session_key="guild:1:text:10:user:3",
                    source="text",
                    turn_id="source-turn",
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key="guild:1:text:10:user:3",
                    channel_id=10,
                    reply_to_message_id=100,
                    request_user_text="SOURCE_QUESTION",
                    request_answer_text="SOURCE_PROMISE",
                    query="DELIVERY_QUERY",
                    continuity_generation=4,
                )
                recovery.begin_delivery_prepare(
                    intent_id,
                    answer="DELIVERY_ANSWER",
                    display_text="DELIVERY_ANSWER",
                    delivery_turn_id="delivery-turn",
                )
                if duplicate_pair:
                    recovery.mark_delivery_baseline(
                        intent_id,
                        continuity_generation=4,
                    )
                    recovery.mark_delivery_attempted(intent_id)
                    recovery.mark_delivery_succeeded(
                        intent_id,
                        delivery_message_id=8001,
                    )
                else:
                    recovery.mark_delivery_attempted(intent_id)
                    entry = recovery._entries[intent_id]
                    entry.update(
                        {
                            "phase": "delivery_succeeded",
                            "deliveryMessageId": 8001,
                            "deliveryGeneration": 0,
                        }
                    )
                    recovery._write()

                history = [
                    {"role": "user", "content": "SOURCE_QUESTION"},
                    {
                        "role": "assistant",
                        "content": "SOURCE_PROMISE",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "DELIVERY_QUERY"},
                    {
                        "role": "assistant",
                        "content": "DELIVERY_ANSWER",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                if duplicate_pair:
                    history.extend(history[-2:])
                effects: list[str] = []
                deps = replace(
                    build_deps(),
                    get_conversation_history=lambda **_kwargs: history,
                    current_turn_id=lambda _key: "delivery-turn",
                    build_search_query=lambda *_args, **_kwargs: (
                        "DELIVERY_QUERY"
                    ),
                    format_display_text=lambda text, **_kwargs: text,
                    search_followup_recovery=recovery,
                    continuity_status=lambda: durable_continuity_status(5),
                    commit_session_continuity=(
                        lambda *_args: effects.append("commit")
                    ),
                    schedule_memory_update=(
                        lambda *_args, **_kwargs: effects.append("memory")
                    ),
                    resolve_open_question_rows=(
                        lambda *_args, **_kwargs: effects.append("question")
                        or 0
                    ),
                    write_json_file=(
                        lambda *_args, **_kwargs: effects.append(
                            "cognitive"
                        )
                    ),
                )

                result = await recover_search_followups_from_runtime(
                    deps=deps
                )

                self.assertEqual(result["uncertain"], 1)
                self.assertEqual(effects, [])
                self.assertEqual(
                    recovery.pending()[0]["phase"],
                    "delivery_succeeded",
                )

        asyncio.run(run_case(duplicate_pair=False))
        asyncio.run(run_case(duplicate_pair=True))

    def test_complete_write_precedes_optional_projection_on_restart(
        self,
    ) -> None:
        async def scenario(root: Path) -> None:
            session_key = "guild:1:text:10:user:3"
            store = SessionStateStore.create_empty()
            store.start_new_turn(session_key, turn_id="source-turn")
            store.append_history(
                session_key,
                "SOURCE_QUESTION",
                "SOURCE_PROMISE",
                system_prompt="system",
                max_history_items=12,
                memory_receipt=not_used_memory_receipt_ref(),
            )
            store.mark_active(
                session_key,
                user_id=3,
                speaker="assistant",
                active_conversation_awaiting_reply_sec=300.0,
            )
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
            )
            source_status = checkpoint.commit_completed_turn(
                session_key,
                "source-turn",
            )
            recovery = SearchFollowupRecoveryJournal(
                path=root / "search-followups.json"
            )
            intent_id = recovery.begin(
                guild_id=1,
                session_key=session_key,
                source="text",
                turn_id="source-turn",
                room_key="text:10",
                person_key="user:3",
                session_memory_key=session_key,
                channel_id=10,
                reply_to_message_id=100,
                request_user_text="SOURCE_QUESTION",
                request_answer_text="SOURCE_PROMISE",
                query="DELIVERY_QUERY",
                continuity_generation=int(
                    source_status["checkpointGeneration"]
                ),
            )
            effects: list[str] = []

            def append_history(key, user, assistant, **kwargs):
                store.append_history(
                    key,
                    user,
                    assistant,
                    system_prompt="system",
                    max_history_items=12,
                    guild_id=kwargs.get("guild_id"),
                    memory_receipt=kwargs.get("memory_receipt"),
                )

            def mark_active(key, **kwargs):
                store.mark_active(
                    key,
                    active_conversation_awaiting_reply_sec=300.0,
                    **kwargs,
                )

            deps = replace(
                build_deps(memory_index_dir=root / "memory-index"),
                bot=SimpleNamespace(
                    get_channel=lambda _channel_id: SimpleNamespace(
                        send=object()
                    )
                ),
                current_turn_id=store.current_turn_id,
                start_new_turn=store.start_new_turn,
                append_history=append_history,
                mark_session_active=mark_active,
                send_discord_text=(
                    lambda _channel, text, **_kwargs: asyncio.sleep(
                        0,
                        result=delivery_receipt(text, 8001),
                    )
                ),
                format_display_text=lambda text, **_kwargs: text,
                commit_session_continuity=(
                    checkpoint.commit_completed_turn_async
                ),
                continuity_status=checkpoint.status,
                search_followup_recovery=recovery,
                schedule_memory_update=(
                    lambda *_args, **_kwargs: effects.append("memory")
                ),
                resolve_open_question_rows=(
                    lambda *_args, **_kwargs: effects.append("question")
                    or 0
                ),
                write_json_file=(
                    lambda *_args, **_kwargs: effects.append("cognitive")
                ),
            )
            real_atomic_json_write = recovery_module.atomic_json_write

            def fail_complete(path, payload, **kwargs):
                if Path(path) == recovery.path and not payload["entries"]:
                    raise OSError("simulated complete write failure")
                return real_atomic_json_write(path, payload, **kwargs)

            with patch.object(
                recovery_module,
                "atomic_json_write",
                side_effect=fail_complete,
            ):
                with self.assertRaises(OSError):
                    await deliver_proactive_followup_from_runtime(
                        1,
                        "DELIVERY_QUERY",
                        "DELIVERY_ANSWER",
                        deps=deps,
                        session_key=session_key,
                        room_key="text:10",
                        person_key="user:3",
                        session_memory_key=session_key,
                        channel_id=10,
                        reply_to_message_id=100,
                        source="search-followup-text",
                        source_turn_id="source-turn",
                        recovery_intent_id=intent_id,
                    )
            self.assertEqual(effects, [])

            restarted = SearchFollowupRecoveryJournal(
                path=recovery.path
            )
            recovery_deps = replace(
                deps,
                search_followup_recovery=restarted,
                get_conversation_history=(
                    lambda **kwargs: store.get_conversation_history(
                        system_prompt="system",
                        **kwargs,
                    )
                ),
                build_search_query=lambda *_args, **_kwargs: (
                    "DELIVERY_QUERY"
                ),
            )
            first = await recover_search_followups_from_runtime(
                deps=recovery_deps
            )
            second = await recover_search_followups_from_runtime(
                deps=recovery_deps
            )

            self.assertEqual(first["verified"], 1)
            self.assertEqual(second["pending"], 0)
            self.assertEqual(effects.count("memory"), 1)
            self.assertEqual(effects.count("question"), 4)
            self.assertEqual(effects.count("cognitive"), 4)
            self.assertEqual(restarted.pending(), [])

        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(scenario(Path(temporary)))

    def test_search_result_is_bound_to_its_source_turn_and_own_commit(
        self,
    ) -> None:
        async def run_case(root: Path, *, supersede: bool):
            system_prompt = "system"
            session_key = "guild:1:text:10:user:3"
            store = SessionStateStore.create_empty()
            store.start_new_turn(session_key, turn_id="source-turn")
            store.append_history(
                session_key,
                "ORIGINAL_SEARCH_REQUEST",
                "찾아보고 알려줄게",
                system_prompt=system_prompt,
                max_history_items=12,
                memory_receipt=not_used_memory_receipt_ref(),
            )
            store.mark_active(
                session_key,
                user_id=3,
                speaker="assistant",
                active_conversation_awaiting_reply_sec=300.0,
            )
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt=system_prompt,
            )
            recovery = SearchFollowupRecoveryJournal(
                path=root / "search-followups.json",
            )
            source_status = checkpoint.commit_completed_turn(
                session_key,
                "source-turn",
            )
            search_started = asyncio.Event()
            release_search = asyncio.Event()
            sent: list[str] = []
            memory_events: list[str] = []
            optional_events: list[str] = []

            class Channel:
                async def send(self, text, **_kwargs):
                    sent.append(text)

            class Bot:
                @staticmethod
                def get_channel(_channel_id):
                    return Channel()

            async def search(_query):
                search_started.set()
                await release_search.wait()
                return [{"title": "result"}]

            async def answer(_query, _rows):
                return "SEARCH_RESULT_CANARY"

            def append_history(key, user, assistant, **kwargs):
                store.append_history(
                    key,
                    user,
                    assistant,
                    system_prompt=system_prompt,
                    max_history_items=12,
                    guild_id=kwargs.get("guild_id"),
                    memory_receipt=kwargs.get("memory_receipt"),
                )

            def mark_active(key, **kwargs):
                store.mark_active(
                    key,
                    active_conversation_awaiting_reply_sec=300.0,
                    **kwargs,
                )

            base = build_deps()
            deps = SearchFollowupRuntimeDeps(
                **{
                    **base.__dict__,
                    "bot": Bot(),
                    "runtime_session_key": lambda **kwargs: kwargs.get(
                        "session_key"
                    ),
                    "remember_session_followup_target": (
                        lambda key, **kwargs: store.remember_followup_target(
                            key,
                            **kwargs,
                        )
                    ),
                    "build_search_query": lambda *_args, **_kwargs: (
                        "SEARCH_QUERY_CANARY"
                    ),
                    "search_duckduckgo": search,
                    "answer_from_search_results": answer,
                    "current_turn_id": store.current_turn_id,
                    "start_new_turn": store.start_new_turn,
                    "append_history": append_history,
                    "mark_session_active": mark_active,
                    "send_discord_text": (
                        lambda _channel, text, **_kwargs: asyncio.sleep(
                            0,
                            result=record_delivery(sent, text),
                        )
                    ),
                    "format_display_text": lambda text, **_kwargs: text,
                    "schedule_memory_update": (
                        lambda *_args, **_kwargs: memory_events.append(
                            "memory"
                        )
                    ),
                    "resolve_open_question_rows": (
                        lambda *_args, **_kwargs: optional_events.append(
                            "resolve"
                        )
                        or 0
                    ),
                    "write_json_file": (
                        lambda *_args, **_kwargs: optional_events.append(
                            "cognitive"
                        )
                    ),
                    "create_turn_scoped_task": (
                        lambda coro, **_kwargs: asyncio.create_task(coro)
                    ),
                    "commit_session_continuity": (
                        checkpoint.commit_completed_turn_async
                    ),
                    "search_followup_recovery": recovery,
                }
            )
            schedule_search_followup_from_runtime(
                1,
                session_key,
                "ORIGINAL_SEARCH_REQUEST",
                "찾아보고 알려줄게",
                deps=deps,
                channel_id=10,
                reply_to_message_id=100,
                source="search-followup-text",
                force=True,
                continuity_generation=int(
                    source_status["checkpointGeneration"]
                ),
            )
            task = deps.background_search_tasks[session_key]
            await search_started.wait()
            if supersede:
                store.start_new_turn(session_key, turn_id="successor-turn")
                store.append_history(
                    session_key,
                    "SUCCESSOR_QUESTION",
                    None,
                    system_prompt=system_prompt,
                    max_history_items=12,
                )
                store.mark_active(
                    session_key,
                    user_id=3,
                    speaker="user",
                    active_conversation_awaiting_reply_sec=300.0,
                )
            live_before_delivery = [
                dict(row) for row in store.histories[session_key]
            ]
            release_search.set()
            await task

            restored_store = SessionStateStore.create_empty()
            restore_status = SessionContinuityCheckpoint(
                store=restored_store,
                checkpoint_path=root / "active.json",
                status_path=root / "restored-status.json",
                system_prompt=system_prompt,
            ).restore()
            return {
                "session_key": session_key,
                "store": store,
                "live_before_delivery": live_before_delivery,
                "sent": sent,
                "memory_events": memory_events,
                "optional_events": optional_events,
                "restored_store": restored_store,
                "restore_status": restore_status,
                "pending_recovery": recovery.pending(),
            }

        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                superseded = await run_case(
                    root / "superseded",
                    supersede=True,
                )
                self.assertEqual(superseded["sent"], [])
                self.assertEqual(superseded["memory_events"], [])
                self.assertEqual(superseded["optional_events"], [])
                self.assertEqual(
                    superseded["pending_recovery"][0]["lastErrorCode"],
                    "search_followup_source_turn_superseded",
                )
                self.assertEqual(
                    superseded["store"].histories[
                        superseded["session_key"]
                    ],
                    superseded["live_before_delivery"],
                )
                self.assertEqual(
                    superseded["restored_store"].current_turn_id(
                        superseded["session_key"]
                    ),
                    "source-turn",
                )
                self.assertNotIn(
                    "SEARCH_RESULT_CANARY",
                    str(superseded["restored_store"].histories),
                )

                current = await run_case(
                    root / "current",
                    supersede=False,
                )
                restored_store = current["restored_store"]
                restored_history = restored_store.get_conversation_history(
                    system_prompt="system",
                    session_key=current["session_key"],
                )
                self.assertEqual(current["sent"], ["SEARCH_RESULT_CANARY"])
                self.assertEqual(current["memory_events"], ["memory"])
                self.assertEqual(current["pending_recovery"], [])
                self.assertEqual(
                    current["optional_events"],
                    [
                        "resolve",
                        "resolve",
                        "resolve",
                        "resolve",
                        "cognitive",
                        "cognitive",
                        "cognitive",
                        "cognitive",
                    ],
                )
                self.assertEqual(current["restore_status"]["state"], "restored")
                self.assertNotEqual(
                    restored_store.current_turn_id(current["session_key"]),
                    "source-turn",
                )
                self.assertEqual(
                    [
                        (row["role"], row["content"])
                        for row in restored_history[-2:]
                    ],
                    [
                        ("user", "SEARCH_QUERY_CANARY"),
                        ("assistant", "SEARCH_RESULT_CANARY"),
                    ],
                )
                self.assertEqual(
                    restored_history[-1]["memoryReceiptRef"]["state"],
                    "not_used",
                )
                self.assertEqual(
                    restored_store.last_speaker[current["session_key"]],
                    "assistant",
                )
                self.assertEqual(
                    restored_store.active_user_ids[current["session_key"]],
                    3,
                )

        asyncio.run(scenario())

    def test_prepare_crash_keeps_source_anchor_and_recovers_new_delivery_turn(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                system_prompt = "system"
                session_key = "guild:1:text:10:user:3"
                store = SessionStateStore.create_empty()
                store.start_new_turn(session_key, turn_id="source-turn")
                store.append_history(
                    session_key,
                    "ORIGINAL_SEARCH_REQUEST",
                    "찾아보고 알려줄게",
                    system_prompt=system_prompt,
                    max_history_items=12,
                    memory_receipt=not_used_memory_receipt_ref(),
                )
                store.mark_active(
                    session_key,
                    user_id=3,
                    speaker="assistant",
                    active_conversation_awaiting_reply_sec=300.0,
                )
                checkpoint = SessionContinuityCheckpoint(
                    store=store,
                    checkpoint_path=root / "active.json",
                    status_path=root / "status.json",
                    system_prompt=system_prompt,
                )
                source_status = checkpoint.commit_completed_turn(
                    session_key,
                    "source-turn",
                )
                recovery = SearchFollowupRecoveryJournal(
                    path=root / "search-followups.json",
                )
                intent_id = recovery.begin(
                    guild_id=1,
                    session_key=session_key,
                    source="text",
                    turn_id="source-turn",
                    room_key="text:10",
                    person_key="user:3",
                    session_memory_key=f"{session_key}:user:3",
                    channel_id=10,
                    reply_to_message_id=100,
                    request_user_text="ORIGINAL_SEARCH_REQUEST",
                    request_answer_text="찾아보고 알려줄게",
                    query="SEARCH_QUERY_CANARY",
                    continuity_generation=int(
                        source_status["checkpointGeneration"]
                    ),
                )
                recovery.begin_delivery_prepare(
                    intent_id,
                    answer="ABANDONED_RESULT",
                    display_text="ABANDONED_RESULT",
                    delivery_turn_id="abandoned-delivery-turn",
                )
                restarted_recovery = SearchFollowupRecoveryJournal(
                    path=root / "search-followups.json",
                )
                pending = restarted_recovery.pending()[0]
                self.assertEqual(pending["turnId"], "source-turn")
                self.assertEqual(
                    pending["deliveryTurnId"],
                    "abandoned-delivery-turn",
                )
                self.assertEqual(store.current_turn_id(session_key), "source-turn")
                sent: list[str] = []
                delivery_events: list[str] = []

                class Channel:
                    async def send(self, text, **_kwargs):
                        sent.append(text)

                class Bot:
                    user = SimpleNamespace(id=99)

                    @staticmethod
                    def get_channel(_channel_id):
                        return Channel()

                def append_history(key, user, assistant, **kwargs):
                    delivery_events.append("history")
                    store.append_history(
                        key,
                        user,
                        assistant,
                        system_prompt=system_prompt,
                        max_history_items=12,
                        guild_id=kwargs.get("guild_id"),
                        memory_receipt=kwargs.get("memory_receipt"),
                    )

                def mark_active(key, **kwargs):
                    store.mark_active(
                        key,
                        active_conversation_awaiting_reply_sec=300.0,
                        **kwargs,
                    )

                async def send_followup(_channel, text, **_kwargs):
                    delivery_events.append("send")
                    return record_delivery(sent, text)

                async def commit_followup(*args, **kwargs):
                    delivery_events.append("commit")
                    return await checkpoint.commit_completed_turn_async(
                        *args,
                        **kwargs,
                    )

                base = build_deps()
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **base.__dict__,
                        "bot": Bot(),
                        "runtime_session_key": lambda **kwargs: kwargs.get(
                            "session_key"
                        ),
                        "get_conversation_history": (
                            lambda **kwargs: store.get_conversation_history(
                                system_prompt=system_prompt,
                                **kwargs,
                            )
                        ),
                        "build_search_query": lambda *_args, **_kwargs: (
                            "SEARCH_QUERY_CANARY"
                        ),
                        "search_duckduckgo": (
                            lambda _query: asyncio.sleep(
                                0,
                                result=[{"title": "result"}],
                            )
                        ),
                        "answer_from_search_results": (
                            lambda *_args: asyncio.sleep(
                                0,
                                result="RECOVERED_RESULT",
                            )
                        ),
                        "current_turn_id": store.current_turn_id,
                        "start_new_turn": store.start_new_turn,
                        "append_history": append_history,
                        "mark_session_active": mark_active,
                        "send_discord_text": send_followup,
                        "format_display_text": lambda text, **_kwargs: text,
                        "create_turn_scoped_task": (
                            lambda coro, **_kwargs: asyncio.create_task(coro)
                        ),
                        "commit_session_continuity": commit_followup,
                        "search_followup_recovery": restarted_recovery,
                        "continuity_status": checkpoint.status,
                    }
                )

                recovered = await recover_search_followups_from_runtime(
                    deps=deps
                )
                self.assertEqual(recovered["redelivered"], 1)

                restored_store = SessionStateStore.create_empty()
                restore_status = SessionContinuityCheckpoint(
                    store=restored_store,
                    checkpoint_path=root / "active.json",
                    status_path=root / "restored-status.json",
                    system_prompt=system_prompt,
                ).restore()
                restored_history = restored_store.get_conversation_history(
                    system_prompt=system_prompt,
                    session_key=session_key,
                )
                self.assertEqual(sent, ["ABANDONED_RESULT"])
                self.assertEqual(
                    delivery_events[:3],
                    ["send", "history", "commit"],
                )
                self.assertEqual(restarted_recovery.pending(), [])
                self.assertEqual(restore_status["state"], "restored")
                self.assertNotEqual(
                    restored_store.current_turn_id(session_key),
                    "source-turn",
                )
                self.assertEqual(
                    [
                        (row["role"], row["content"])
                        for row in restored_history[-2:]
                    ],
                    [
                        ("user", "SEARCH_QUERY_CANARY"),
                        ("assistant", "ABANDONED_RESULT"),
                    ],
                )

        asyncio.run(scenario())

    def test_scheduler_persists_only_after_durable_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = SearchFollowupRecoveryJournal(
                path=Path(temporary) / "active.json"
            )
            created: list[object] = []

            def create_task(coro, **_kwargs):
                coro.close()
                created.append(coro)
                return SimpleNamespace(done=lambda: False, cancel=lambda: None)

            deps = build_deps(
                get_conversation_history_result=[
                    {"role": "user", "content": "검색해줘"},
                    {"role": "assistant", "content": "찾아보고 알려줄게"},
                ]
            )
            deps = SearchFollowupRuntimeDeps(
                **{
                    **deps.__dict__,
                    "answer_promises_search": lambda _text: True,
                    "build_search_query": lambda *_args, **_kwargs: "검색 질의",
                    "runtime_session_key": lambda **_kwargs: "guild:7:text:8:user:9",
                    "current_turn_id": lambda _key: "turn-1",
                    "create_turn_scoped_task": create_task,
                    "search_followup_recovery": journal,
                }
            )

            schedule_search_followup_from_runtime(
                7,
                "guild:7:text:8:user:9",
                "검색해줘",
                "찾아보고 알려줄게",
                deps=deps,
                channel_id=8,
                source="search-followup-text",
                continuity_generation=4,
            )

            self.assertEqual(len(created), 1)
            self.assertEqual(journal.pending()[0]["continuityGeneration"], 4)
            raw = journal.path.read_text(encoding="utf-8")
            self.assertNotIn("검색해줘", raw)
            self.assertNotIn("검색 질의", raw)

    def test_configured_recovery_admission_failure_does_not_schedule(
        self,
    ) -> None:
        for admission in ("failed", "none", "missing"):
            with self.subTest(admission=admission):
                created: list[object] = []
                queued: list[str] = []

                def create_task(coro, **_kwargs):
                    coro.close()
                    created.append(coro)
                    return SimpleNamespace(done=lambda: False)

                recovery = SimpleNamespace(
                    begin=(
                        lambda **_kwargs: (
                            (_ for _ in ()).throw(
                                OSError("journal unavailable")
                            )
                        )
                        if admission == "failed"
                        else None
                    )
                )
                deps = replace(
                    build_deps(),
                    answer_promises_search=lambda _text: True,
                    build_search_query=lambda *_args, **_kwargs: (
                        "검색 질의"
                    ),
                    runtime_session_key=lambda **_kwargs: (
                        "guild:7:text:8:user:9"
                    ),
                    current_turn_id=lambda _key: "turn-1",
                    create_turn_scoped_task=create_task,
                    record_search_followup_queued=lambda: queued.append(
                        "queued"
                    ),
                    search_followup_recovery=recovery,
                )
                schedule_search_followup_from_runtime(
                    7,
                    "guild:7:text:8:user:9",
                    "검색해줘",
                    "찾아보고 알려줄게",
                    deps=deps,
                    channel_id=8,
                    source="search-followup-text",
                    continuity_generation=(
                        4 if admission != "missing" else None
                    ),
                )
                self.assertEqual(created, [])
                self.assertEqual(queued, [])
                self.assertEqual(deps.background_search_tasks, {})

    def test_voice_source_does_not_schedule_without_delivery_owner(self) -> None:
        created: list[object] = []

        def create_task(coro, **_kwargs):
            coro.close()
            created.append(coro)
            return SimpleNamespace(done=lambda: False, cancel=lambda: None)

        base = build_deps()
        deps = SearchFollowupRuntimeDeps(
            **{
                **base.__dict__,
                "answer_promises_search": lambda _text: True,
                "build_search_query": lambda *_args, **_kwargs: "검색 질의",
                "runtime_session_key": lambda **_kwargs: (
                    "guild:7:voice:8:user:9"
                ),
                "create_turn_scoped_task": create_task,
            }
        )

        schedule_search_followup_from_runtime(
            7,
            "guild:7:voice:8:user:9",
            "검색해줘",
            "찾아보고 알려줄게",
            deps=deps,
            channel_id=None,
            source="search-followup-voice",
            continuity_generation=4,
        )

        self.assertEqual(created, [])
        self.assertEqual(deps.background_search_tasks, {})
        self.assertEqual(deps.inflight_search_tasks, {})

    def test_successor_same_query_drains_prior_search_before_reset(self) -> None:
        async def scenario() -> None:
            session_key = "guild:7:text:8:user:9"
            current_turn = "turn-a"
            first_started = asyncio.Event()
            second_started = asyncio.Event()
            releases = [asyncio.Event(), asyncio.Event()]
            search_calls = 0
            sent: list[str] = []

            async def search(_query):
                nonlocal search_calls
                index = search_calls
                search_calls += 1
                (first_started if index == 0 else second_started).set()
                await releases[index].wait()
                return [{"title": "result"}]

            class Bot:
                @staticmethod
                def get_channel(_channel_id):
                    return SimpleNamespace(send=object())

            base = build_deps()
            deps = SearchFollowupRuntimeDeps(
                **{
                    **base.__dict__,
                    "bot": Bot(),
                    "answer_promises_search": lambda _text: True,
                    "build_search_query": lambda *_args, **_kwargs: (
                        "SAME_QUERY"
                    ),
                    "runtime_session_key": lambda **_kwargs: session_key,
                    "current_turn_id": lambda _key: current_turn,
                    "search_duckduckgo": search,
                    "answer_from_search_results": (
                        lambda *_args: asyncio.sleep(
                            0,
                            result="RESULT",
                        )
                    ),
                    "send_discord_text": (
                        lambda _channel, text, **_kwargs: asyncio.sleep(
                            0,
                            result=record_delivery(sent, text),
                        )
                    ),
                    "format_display_text": lambda text, **_kwargs: text,
                    "create_turn_scoped_task": (
                        lambda coro, **_kwargs: asyncio.create_task(coro)
                    ),
                }
            )
            schedule_search_followup_from_runtime(
                7,
                session_key,
                "질문",
                "찾아볼게",
                deps=deps,
                channel_id=8,
                source="search-followup-text",
                force=True,
            )
            first_task = deps.background_search_tasks[session_key]
            await first_started.wait()
            current_turn = "turn-b"
            schedule_search_followup_from_runtime(
                7,
                session_key,
                "질문",
                "다시 찾아볼게",
                deps=deps,
                channel_id=8,
                source="search-followup-text",
                force=True,
            )
            second_task = deps.background_search_tasks[session_key]
            self.assertIsNot(first_task, second_task)
            await asyncio.wait_for(second_started.wait(), timeout=1.0)
            draining = [
                task
                for key, task in deps.background_search_tasks.items()
                if ":search-drain:" in key
            ]
            self.assertEqual(draining, [first_task])
            reset_deps = SimpleNamespace(
                autonomy_engines={},
                autonomy_cognitive_refresh_tasks={},
                background_search_tasks=deps.background_search_tasks,
                background_memory_tasks={},
                background_memory_vault_tasks={},
            )
            with self.assertRaisesRegex(
                RuntimeError,
                f"^{SEARCH_BACKGROUND_WORK_INFLIGHT}$",
            ):
                require_guild_runtime_reset_ready(7, deps=reset_deps)
            releases[0].set()
            releases[1].set()
            await asyncio.gather(first_task, second_task)
            require_guild_runtime_reset_ready(7, deps=reset_deps)

            self.assertEqual(search_calls, 2)
            self.assertEqual(sent, ["RESULT"])
            self.assertEqual(deps.background_search_tasks, {})

        asyncio.run(scenario())

    def test_restart_resumes_search_and_drains_different_query_owner(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                sent: list[str] = []
                prior_started = asyncio.Event()
                release_prior = asyncio.Event()

                async def prior_search() -> None:
                    prior_started.set()
                    await release_prior.wait()

                prior_task = asyncio.create_task(prior_search())
                await prior_started.wait()

                class Channel:
                    async def send(self, text, **_kwargs):
                        sent.append(text)

                    def history(self, **_kwargs):
                        async def rows():
                            if False:
                                yield None

                        return rows()

                class Bot:
                    user = SimpleNamespace(id=99)

                    def get_channel(self, _channel_id):
                        return Channel()

                    async def fetch_channel(self, _channel_id):
                        return Channel()

                    def get_guild(self, _guild_id):
                        return SimpleNamespace(voice_client=None)

                intent_id = journal.begin(
                    guild_id=7,
                    session_key="guild:7:text:8:user:9",
                    source="text",
                    turn_id="turn-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                self.assertIsNotNone(intent_id)

                async def commit(*_args):
                    return durable_continuity_status(5)

                deps = build_deps(
                    get_conversation_history_result=history
                )
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **deps.__dict__,
                        "bot": Bot(),
                        "build_search_query": lambda *_args, **_kwargs: "검색 질의",
                        "search_duckduckgo": lambda _query: asyncio.sleep(
                            0,
                            result=[{"title": "결과"}],
                        ),
                        "answer_from_search_results": lambda *_args: asyncio.sleep(
                            0,
                            result="검색 결과 답변",
                        ),
                        "current_turn_id": lambda _key: "turn-1",
                        "get_conversation_history": lambda **_kwargs: history,
                        "append_history": lambda _session, user, answer, **_kwargs: history.extend(
                            [
                                {"role": "user", "content": user},
                                {"role": "assistant", "content": answer},
                            ]
                        ),
                        "commit_session_continuity": commit,
                        "send_discord_text": lambda _channel, text, **_kwargs: asyncio.sleep(
                            0,
                            result=record_delivery(sent, text),
                        ),
                        "format_display_text": lambda text, **_kwargs: text,
                        "create_turn_scoped_task": lambda coro, **_kwargs: asyncio.create_task(
                            coro
                        ),
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(4)
                        ),
                    }
                )
                session_key = "guild:7:text:8:user:9"
                deps.background_search_tasks[session_key] = prior_task
                deps.inflight_search_tasks[
                    f"{session_key}:different-query"
                ] = prior_task

                result = await recover_search_followups_from_runtime(
                    deps=deps
                )
                self.assertEqual(result["resumed"], 1)
                resumed_task = deps.background_search_tasks[session_key]
                self.assertIsNot(resumed_task, prior_task)
                self.assertIn(
                    prior_task,
                    [
                        task
                        for key, task in deps.background_search_tasks.items()
                        if ":search-drain:" in key
                    ],
                )
                await resumed_task
                self.assertEqual(sent, ["검색 결과 답변"])
                self.assertEqual(journal.pending(), [])
                reset_deps = SimpleNamespace(
                    autonomy_engines={},
                    autonomy_cognitive_refresh_tasks={},
                    background_search_tasks=deps.background_search_tasks,
                    background_memory_tasks={},
                    background_memory_vault_tasks={},
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    f"^{SEARCH_BACKGROUND_WORK_INFLIGHT}$",
                ):
                    require_guild_runtime_reset_ready(7, deps=reset_deps)
                release_prior.set()
                await prior_task
                require_guild_runtime_reset_ready(7, deps=reset_deps)
                self.assertTrue(
                    all(
                        task.done()
                        for task in deps.background_search_tasks.values()
                    )
                )

        asyncio.run(scenario())

    def test_guild_block_during_recovery_stops_and_delivery_stays_owned(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                session_key = "guild:7:text:8:user:9"
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "검색 질의"},
                    {
                        "role": "assistant",
                        "content": "검색 결과 답변",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                intent_id = journal.begin(
                    guild_id=7,
                    session_key=session_key,
                    source="text",
                    turn_id="source-turn",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                journal.begin_delivery_prepare(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    delivery_turn_id="delivery-turn",
                )
                journal.mark_delivery_ready(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    continuity_generation=5,
                )
                gate = {"open": True, "block_on_channel": True}
                sent: list[str] = []
                send_started = asyncio.Event()
                release_send = asyncio.Event()
                reset_state = {"succeeded": False, "staleSends": 0}

                class Channel:
                    async def send(self, *_args, **_kwargs):
                        return None

                class Bot:
                    user = SimpleNamespace(id=99)

                    @staticmethod
                    def get_channel(_channel_id):
                        if gate["block_on_channel"]:
                            gate["open"] = False
                        return Channel()

                async def send(_channel, text, **_kwargs):
                    send_started.set()
                    await release_send.wait()
                    if reset_state["succeeded"]:
                        reset_state["staleSends"] += 1
                    sent.append(text)
                    return delivery_receipt(text)

                deps = replace(
                    build_deps(get_conversation_history_result=history),
                    bot=Bot(),
                    get_conversation_history=lambda **_kwargs: history,
                    current_turn_id=lambda _key: "delivery-turn",
                    format_display_text=lambda text, **_kwargs: text,
                    send_discord_text=send,
                    search_followup_recovery=journal,
                    continuity_status=lambda: (
                        durable_continuity_status(5)
                    ),
                    guild_is_open=lambda _guild_id: gate["open"],
                )

                blocked = await recover_search_followups_from_runtime(deps=deps)
                self.assertEqual(blocked["redelivered"], 0)
                self.assertEqual(sent, [])
                self.assertEqual(journal.pending()[0]["phase"], "delivery_ready")

                gate.update(open=True, block_on_channel=False)
                recovery_task = asyncio.create_task(
                    recover_search_followups_from_runtime(deps=deps)
                )
                await asyncio.wait_for(send_started.wait(), timeout=1.0)
                owned = [
                    task
                    for key, task in deps.background_search_tasks.items()
                    if key.startswith("guild:7:")
                ]
                self.assertEqual(len(owned), 1)
                self.assertIs(owned[0], recovery_task)
                reset_deps = SimpleNamespace(
                    autonomy_engines={},
                    autonomy_cognitive_refresh_tasks={},
                    background_search_tasks=deps.background_search_tasks,
                    background_memory_tasks={},
                    background_memory_vault_tasks={},
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    f"^{SEARCH_BACKGROUND_WORK_INFLIGHT}$",
                ):
                    require_guild_runtime_reset_ready(7, deps=reset_deps)
                self.assertFalse(reset_state["succeeded"])
                release_send.set()
                recovered = await recovery_task
                require_guild_runtime_reset_ready(7, deps=reset_deps)
                reset_state["succeeded"] = True
                self.assertEqual(recovered["redelivered"], 1)
                self.assertEqual(sent, ["검색 결과 답변"])
                self.assertEqual(reset_state["staleSends"], 0)
                self.assertEqual(journal.pending(), [])
                self.assertEqual(deps.background_search_tasks, {})

        asyncio.run(scenario())

    def test_attempted_text_delivery_is_verified_before_resend(self) -> None:
        async def scenario(
            *,
            references: tuple[object | None, ...] = (),
            preparing: bool = False,
        ) -> tuple[dict[str, int], list[str], list[dict]]:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "검색 질의"},
                    {
                        "role": "assistant",
                        "content": "검색 결과 답변",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                intent_id = journal.begin(
                    guild_id=7,
                    session_key="guild:7:text:8:user:9",
                    source="text",
                    turn_id="turn-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                journal.begin_delivery_prepare(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    delivery_turn_id="turn",
                )
                if not preparing:
                    journal.mark_delivery_ready(
                        intent_id,
                        answer="검색 결과 답변",
                        display_text="검색 결과 답변",
                        continuity_generation=5,
                    )
                    journal.mark_delivery_attempted(intent_id)
                sent: list[str] = []

                class Channel:
                    async def send(self, text, **_kwargs):
                        sent.append(text)

                    def history(self, **_kwargs):
                        async def rows():
                            for index, reference in enumerate(references):
                                yield SimpleNamespace(
                                    id=7001 + index,
                                    author=SimpleNamespace(id=99),
                                    content="검색 결과 답변",
                                    reference=reference,
                                )

                        return rows()

                class Bot:
                    user = SimpleNamespace(id=99)

                    def get_channel(self, _channel_id):
                        return Channel()

                    def get_guild(self, _guild_id):
                        return SimpleNamespace(voice_client=None)

                deps = build_deps(
                    get_conversation_history_result=history
                )
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **deps.__dict__,
                        "bot": Bot(),
                        "get_conversation_history": lambda **_kwargs: history,
                        "format_display_text": lambda text, **_kwargs: text,
                        "send_discord_text": lambda _channel, text, **_kwargs: asyncio.sleep(
                            0,
                            result=record_delivery(sent, text),
                        ),
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(5)
                        ),
                    }
                )
                result = await recover_search_followups_from_runtime(
                    deps=deps
                )
                return result, sent, journal.pending()

        verified, verified_sent, verified_pending = asyncio.run(
            scenario(references=(SimpleNamespace(message_id=10),))
        )
        redelivered, redelivered_sent, redelivered_pending = asyncio.run(
            scenario()
        )
        prepared, prepared_sent, prepared_pending = asyncio.run(
            scenario(preparing=True)
        )
        wrong, wrong_sent, wrong_pending = asyncio.run(
            scenario(references=(SimpleNamespace(message_id=11),))
        )
        reference_less, reference_less_sent, reference_less_pending = (
            asyncio.run(scenario(references=(None,)))
        )
        duplicate, duplicate_sent, duplicate_pending = asyncio.run(
            scenario(
                references=(
                    SimpleNamespace(message_id=10),
                    SimpleNamespace(message_id=10),
                )
            )
        )
        self.assertEqual(verified["verified"], 1)
        self.assertEqual(verified_sent, [])
        self.assertEqual(verified_pending, [])
        self.assertEqual(redelivered["redelivered"], 1)
        self.assertEqual(redelivered_sent, ["검색 결과 답변"])
        self.assertEqual(redelivered_pending, [])
        self.assertEqual(prepared["redelivered"], 1)
        self.assertEqual(prepared_sent, ["검색 결과 답변"])
        self.assertEqual(prepared_pending, [])
        for result, sent, pending in (
            (wrong, wrong_sent, wrong_pending),
            (reference_less, reference_less_sent, reference_less_pending),
            (duplicate, duplicate_sent, duplicate_pending),
        ):
            self.assertEqual(result["uncertain"], 1)
            self.assertEqual(sent, [])
            self.assertEqual(pending[0]["phase"], "delivery_uncertain")

    def test_delivery_history_requires_one_exact_source_reply(self) -> None:
        async def probe(messages) -> int | bool | None:
            class Channel:
                def history(self, **_kwargs):
                    async def rows():
                        for message in messages:
                            yield message

                    return rows()

            return await _channel_contains_followup(
                Channel(),
                "검색 결과 답변",
                bot_user_id=99,
                after_message_id=10,
                discord_object_factory=lambda **kwargs: SimpleNamespace(
                    **kwargs
                ),
            )

        def bot_message(message_id: int, *, reference=None):
            return SimpleNamespace(
                id=message_id,
                author=SimpleNamespace(id=99),
                content="검색 결과 답변",
                reference=reference,
            )

        async def scenario() -> None:
            production_reference = SimpleNamespace(message_id=10)
            fixture_reference = SimpleNamespace(
                resolved=SimpleNamespace(id=10)
            )
            exact = bot_message(7001, reference=production_reference)

            self.assertIs(await probe([]), False)
            self.assertEqual(await probe([exact]), 7001)
            self.assertEqual(
                await probe(
                    [bot_message(7002, reference=fixture_reference)]
                ),
                7002,
            )
            self.assertEqual(
                await probe([bot_message(7003, reference={"id": 10})]),
                7003,
            )
            self.assertIsNone(
                await probe(
                    [
                        exact,
                        bot_message(7002, reference=production_reference),
                    ]
                )
            )
            self.assertIsNone(
                await probe(
                    [
                        bot_message(
                            7003,
                            reference=SimpleNamespace(message_id=11),
                        )
                    ]
                )
            )
            self.assertIsNone(await probe([bot_message(7004)]))
            self.assertIsNone(
                await probe(
                    [
                        bot_message(
                            7005,
                            reference=SimpleNamespace(
                                message_id=10,
                                resolved=SimpleNamespace(id=11),
                            ),
                        )
                    ]
                )
            )
            self.assertIsNone(
                await probe([exact, bot_message(7006)])
            )

        asyncio.run(scenario())

    def test_failure_logs_do_not_expose_query_or_exception_text(self) -> None:
        private_query = "Bearer private-search-query"
        private_error = "https://private.example/token"
        logs: list[tuple[object, ...]] = []

        class Bot:
            def get_guild(self, _guild_id):
                return SimpleNamespace(voice_client=None)

        async def fail_search(_query):
            raise RuntimeError(private_error)

        async def no_wait(_seconds):
            return None

        deps = build_deps()
        deps = SearchFollowupRuntimeDeps(
            **{
                **deps.__dict__,
                "bot": Bot(),
                "search_duckduckgo": fail_search,
                "sleep": no_wait,
                "commit_session_continuity": lambda: asyncio.sleep(
                    0,
                    result=durable_continuity_status(2),
                ),
                "log": lambda *args: logs.append(args),
            }
        )

        asyncio.run(
            run_search_followup_from_runtime(
                7,
                private_query,
                deps=deps,
                session_key="guild:7:text:8:user:9",
                room_key=None,
                person_key=None,
                session_memory_key=None,
                channel_id=None,
                source="search-followup-text",
                source_turn_id="turn",
            )
        )

        rendered = str(logs)
        self.assertNotIn(private_query, rendered)
        self.assertNotIn(private_error, rendered)
        self.assertIn("RuntimeError", rendered)

    def test_inconclusive_delivery_history_fails_closed(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "검색 질의"},
                    {
                        "role": "assistant",
                        "content": "검색 결과 답변",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                intent_id = journal.begin(
                    guild_id=7,
                    session_key="guild:7:text:8:user:9",
                    source="text",
                    turn_id="turn-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                journal.begin_delivery_prepare(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    delivery_turn_id="turn",
                )
                journal.mark_delivery_ready(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    continuity_generation=5,
                )
                journal.mark_delivery_attempted(intent_id)
                sent: list[str] = []

                class Channel:
                    def history(self, **_kwargs):
                        async def rows():
                            for index in range(50):
                                yield SimpleNamespace(
                                    author=SimpleNamespace(id=99),
                                    content=f"other-{index}",
                                )

                        return rows()

                    async def send(self, text, **_kwargs):
                        sent.append(text)

                class Bot:
                    user = SimpleNamespace(id=99)

                    def get_channel(self, _channel_id):
                        return Channel()

                    def get_guild(self, _guild_id):
                        return SimpleNamespace(voice_client=None)

                deps = build_deps()
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **deps.__dict__,
                        "bot": Bot(),
                        "get_conversation_history": lambda **_kwargs: history,
                        "format_display_text": lambda text, **_kwargs: text,
                        "send_discord_text": lambda _channel, text, **_kwargs: asyncio.sleep(
                            0,
                            result=record_delivery(sent, text),
                        ),
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(5)
                        ),
                    }
                )

                result = await recover_search_followups_from_runtime(
                    deps=deps
                )

                self.assertEqual(result["uncertain"], 1)
                self.assertEqual(sent, [])
                self.assertEqual(
                    journal.pending()[0]["phase"],
                    "delivery_uncertain",
                )

        asyncio.run(scenario())

    def test_cancelled_recovery_releases_claim_for_next_attempt(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                journal = SearchFollowupRecoveryJournal(
                    path=root / "active.json"
                )
                session_key = "guild:7:text:8:user:9"
                history = [
                    {"role": "user", "content": "검색해줘"},
                    {
                        "role": "assistant",
                        "content": "찾아보고 알려줄게",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                    {"role": "user", "content": "검색 질의"},
                    {
                        "role": "assistant",
                        "content": "검색 결과 답변",
                        "memoryReceiptRef": not_used_memory_receipt_ref(),
                    },
                ]
                intent_id = journal.begin(
                    guild_id=7,
                    session_key=session_key,
                    source="text",
                    turn_id="source-turn",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                journal.begin_delivery_prepare(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    delivery_turn_id="delivery-turn",
                )
                journal.mark_delivery_ready(
                    intent_id,
                    answer="검색 결과 답변",
                    display_text="검색 결과 답변",
                    continuity_generation=5,
                )
                send_started = asyncio.Event()
                release_send = asyncio.Event()
                sent: list[str] = []

                class Channel:
                    async def send(self, *_args, **_kwargs):
                        return None

                    def history(self, **_kwargs):
                        async def rows():
                            if sent:
                                yield SimpleNamespace(
                                    id=7001,
                                    author=SimpleNamespace(id=99),
                                    content="검색 결과 답변",
                                    reference=SimpleNamespace(message_id=10),
                                )

                        return rows()

                class Bot:
                    user = SimpleNamespace(id=99)

                    @staticmethod
                    def get_channel(_channel_id):
                        return Channel()

                async def send(_channel, text, **_kwargs):
                    send_started.set()
                    await release_send.wait()
                    sent.append(text)
                    return delivery_receipt(text)

                base = build_deps(get_conversation_history_result=history)
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **base.__dict__,
                        "bot": Bot(),
                        "get_conversation_history": lambda **_kwargs: history,
                        "current_turn_id": lambda _key: "delivery-turn",
                        "format_display_text": lambda text, **_kwargs: text,
                        "send_discord_text": send,
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(5)
                        ),
                    }
                )
                first = asyncio.create_task(
                    recover_search_followups_from_runtime(deps=deps)
                )
                await send_started.wait()
                first.cancel()
                await asyncio.sleep(0)
                self.assertFalse(first.done())
                release_send.set()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                recovered = await recover_search_followups_from_runtime(
                    deps=deps
                )

                self.assertEqual(recovered["verified"], 1)
                self.assertEqual(sent, ["검색 결과 답변"])
                self.assertEqual(journal.pending(), [])

        asyncio.run(scenario())

    def test_retry_budget_survives_restart(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                journal = SearchFollowupRecoveryJournal(
                    path=Path(temporary) / "active.json"
                )
                intent_id = journal.begin(
                    guild_id=7,
                    session_key="guild:7:text:8:user:9",
                    source="text",
                    turn_id="turn-1",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    request_user_text="검색해줘",
                    request_answer_text="찾아보고 알려줄게",
                    query="검색 질의",
                    continuity_generation=4,
                )
                for _ in range(3):
                    journal.record_attempt_failure(
                        intent_id,
                        error_code="search_followup_execution_failed",
                    )
                search_calls = 0
                sent: list[str] = []

                class Channel:
                    async def send(self, text, **_kwargs):
                        sent.append(text)

                class Bot:
                    def get_channel(self, _channel_id):
                        return Channel()

                    def get_guild(self, _guild_id):
                        return SimpleNamespace(voice_client=None)

                async def search(_query):
                    nonlocal search_calls
                    search_calls += 1
                    return []

                async def commit(*_args):
                    return durable_continuity_status(5)

                deps = build_deps()
                deps = SearchFollowupRuntimeDeps(
                    **{
                        **deps.__dict__,
                        "bot": Bot(),
                        "search_duckduckgo": search,
                        "current_turn_id": lambda _key: "turn-1",
                        "format_display_text": lambda text, **_kwargs: text,
                        "send_discord_text": lambda _channel, text, **_kwargs: asyncio.sleep(
                            0,
                            result=record_delivery(sent, text),
                        ),
                        "commit_session_continuity": commit,
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: (
                            durable_continuity_status(4)
                        ),
                    }
                )

                await run_search_followup_from_runtime(
                    7,
                    "검색 질의",
                    deps=deps,
                    session_key="guild:7:text:8:user:9",
                    room_key=None,
                    person_key=None,
                    session_memory_key=None,
                    channel_id=8,
                    reply_to_message_id=10,
                    source="search-followup-recovery-text",
                    source_turn_id="turn-1",
                    recovery_intent_id=intent_id,
                )

                self.assertEqual(search_calls, 0)
                self.assertEqual(len(sent), 1)
                self.assertIn("세 번", sent[0])
                self.assertEqual(journal.pending(), [])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
