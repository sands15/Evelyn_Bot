from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.search_followup_runtime import (  # noqa: E402
    SearchFollowupRuntimeDeps,
    build_search_query_from_runtime,
    deliver_proactive_followup_from_runtime,
    recover_search_followups_from_runtime,
    run_search_followup_from_runtime,
    schedule_search_followup_from_runtime,
)
from evelyn_core.search_followup_recovery import (  # noqa: E402
    SearchFollowupRecoveryJournal,
)
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


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
        send_discord_text=lambda *args, **kwargs: None,
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
        log=lambda *args, **kwargs: None,
    )


class SearchFollowupRuntimeTests(unittest.TestCase):
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
                        "continuity_status": lambda: {
                            "checkpointGeneration": 5,
                            "rollbackProtected": True,
                        },
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
                        "continuity_status": lambda: {
                            "checkpointGeneration": 5,
                            "rollbackProtected": True,
                        },
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

        async def send(*_args, **_kwargs):
            events.append("send")

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
                events.append("send")

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
                ["history", "commit", "memory", "send"],
            )
            self.assertEqual(recovery.pending(), [])
            self.assertIn("errorType=OSError", str(logs))
            self.assertNotIn("PRIVATE_MEMORY_CANARY", str(logs))

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

        async def send(*_args, **_kwargs):
            return None

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
                            result=sent.append(text),
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

                class Channel:
                    async def send(self, text, **_kwargs):
                        sent.append(text)

                class Bot:
                    user = SimpleNamespace(id=99)

                    @staticmethod
                    def get_channel(_channel_id):
                        return Channel()

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
                        "send_discord_text": (
                            lambda _channel, text, **_kwargs: asyncio.sleep(
                                0,
                                result=sent.append(text),
                            )
                        ),
                        "format_display_text": lambda text, **_kwargs: text,
                        "create_turn_scoped_task": (
                            lambda coro, **_kwargs: asyncio.create_task(coro)
                        ),
                        "commit_session_continuity": (
                            checkpoint.commit_completed_turn_async
                        ),
                        "search_followup_recovery": restarted_recovery,
                        "continuity_status": checkpoint.status,
                    }
                )

                recovered = await recover_search_followups_from_runtime(
                    deps=deps
                )
                self.assertEqual(recovered["resumed"], 1)
                await deps.background_search_tasks[session_key]

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
                self.assertEqual(sent, ["RECOVERED_RESULT"])
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
                        ("assistant", "RECOVERED_RESULT"),
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

    def test_successor_same_query_replaces_prior_turn_search(self) -> None:
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
                            result=sent.append(text),
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
            with self.assertRaises(asyncio.CancelledError):
                await first_task
            releases[1].set()
            await second_task

            self.assertEqual(search_calls, 2)
            self.assertEqual(sent, ["RESULT"])

        asyncio.run(scenario())

    def test_restart_resumes_search_from_verified_continuity(self) -> None:
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
                            result=sent.append(text),
                        ),
                        "format_display_text": lambda text, **_kwargs: text,
                        "create_turn_scoped_task": lambda coro, **_kwargs: asyncio.create_task(
                            coro
                        ),
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: {
                            "state": "restored",
                            "checkpointGeneration": 4,
                            "rollbackProtected": True,
                        },
                    }
                )

                result = await recover_search_followups_from_runtime(
                    deps=deps
                )
                self.assertEqual(result["resumed"], 1)
                await deps.background_search_tasks["guild:7:text:8:user:9"]
                self.assertEqual(sent, ["검색 결과 답변"])
                self.assertEqual(journal.pending(), [])

        asyncio.run(scenario())

    def test_attempted_text_delivery_is_verified_before_resend(self) -> None:
        async def scenario(
            *,
            existing: bool,
            preparing: bool = False,
        ) -> tuple[dict[str, int], list[str]]:
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
                            if existing:
                                yield SimpleNamespace(
                                    author=SimpleNamespace(id=99),
                                    content="검색 결과 답변",
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
                            result=sent.append(text),
                        ),
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: {
                            "checkpointGeneration": 5,
                            "rollbackProtected": True,
                        },
                    }
                )
                result = await recover_search_followups_from_runtime(
                    deps=deps
                )
                self.assertEqual(journal.pending(), [])
                return result, sent

        verified, verified_sent = asyncio.run(scenario(existing=True))
        redelivered, redelivered_sent = asyncio.run(
            scenario(existing=False)
        )
        prepared, prepared_sent = asyncio.run(
            scenario(existing=False, preparing=True)
        )
        self.assertEqual(verified["verified"], 1)
        self.assertEqual(verified_sent, [])
        self.assertEqual(redelivered["redelivered"], 1)
        self.assertEqual(redelivered_sent, ["검색 결과 답변"])
        self.assertEqual(prepared["redelivered"], 1)
        self.assertEqual(prepared_sent, ["검색 결과 답변"])

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
                            result=sent.append(text),
                        ),
                        "search_followup_recovery": journal,
                        "continuity_status": lambda: {
                            "checkpointGeneration": 5,
                            "rollbackProtected": True,
                        },
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
                            if False:
                                yield None

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
                        "continuity_status": lambda: {
                            "checkpointGeneration": 5,
                            "rollbackProtected": True,
                        },
                    }
                )
                first = asyncio.create_task(
                    recover_search_followups_from_runtime(deps=deps)
                )
                await send_started.wait()
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                second = asyncio.create_task(
                    recover_search_followups_from_runtime(deps=deps)
                )
                await asyncio.sleep(0)
                release_send.set()
                recovered = await second

                self.assertEqual(recovered["redelivered"], 1)
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
                            result=sent.append(text),
                        ),
                        "commit_session_continuity": commit,
                        "search_followup_recovery": journal,
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
