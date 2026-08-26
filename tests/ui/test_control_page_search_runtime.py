from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_search_runtime import (  # noqa: E402
    ControlPageSearchRuntimeDeps,
    answer_control_page_search_text_from_runtime,
)
from evelyn_core.control_page_text_runtime import (  # noqa: E402
    ControlPageTextRuntimeDeps,
    answer_control_page_text_from_runtime,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_POSITION_SCHEMA,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MemoryExposurePosition,
    capture_memory_exposure_position,
)
from evelyn_core.session_memory_state import (  # noqa: E402
    SessionStateStore,
    build_topic_id,
)
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from evelyn_core.session_turn_runtime import (  # noqa: E402
    SessionTurnRuntimeDeps,
    begin_user_text_turn_from_runtime,
)
from evelyn_core.turn_lifecycle import (  # noqa: E402
    TurnScope,
    TurnScopeRegistry,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


NOTE_A = "concept-0123456789abcdef"
NOTE_B = "concept-fedcba9876543210"


def memory_exposure() -> MemoryExposurePosition:
    return MemoryExposurePosition(
        deletion_position=MemoryDeletionPosition(
            schema=MEMORY_DELETION_POSITION_SCHEMA,
            root_digest="1" * 64,
            sequence=1,
            position_digest="2" * 64,
        ),
        memory_version=7,
        supplied_note_ids=(NOTE_A,),
    )


def session_runtime_deps(
    root: Path,
    store: SessionStateStore,
    *,
    turn_id: str,
) -> SessionTurnRuntimeDeps:
    return SessionTurnRuntimeDeps(
        session_state_store=store,
        system_prompt="system",
        memory_index_dir=root / "memory_index",
        active_conversation_awaiting_reply_sec=120.0,
        active_conversation_text_question_sec=120.0,
        active_conversation_text_sec=90.0,
        max_history_items=12,
        session_topic_ids=store.topic_ids,
        build_topic_id_fn=build_topic_id,
        new_turn_id_fn=lambda: turn_id,
    )


def _deps(**overrides) -> tuple[ControlPageSearchRuntimeDeps, dict[str, object]]:
    state: dict[str, object] = {
        "history": [],
        "active": [],
        "tts": [],
        "route": [],
        "search": [],
        "synthesis": [],
        "events": [],
        "commitTargets": [],
        "locks": {},
        "lockChecks": [],
        "turnId": "prior-turn",
        "turnStarts": [],
        "lifecycle": [],
        "currentScopes": {},
    }

    async def execute_search_then_answer_action(**kwargs):
        state["lockChecks"].append(
            ("search", get_session_lock(kwargs["session_key"]).locked())
        )
        state["search"].append(kwargs)
        return SimpleNamespace(answer_text="search answer")

    async def synthesize_tool_result_with_main_llm(**kwargs):
        state["lockChecks"].append(
            ("synthesis", get_session_lock(kwargs["session_key"]).locked())
        )
        state["synthesis"].append(kwargs)
        kwargs["metrics"]["meta"]["context_pipeline"] = {
            "memory_receipt": {
                "schema": "memory.context-receipt.v1",
                "state": "not_requested",
                "memoryVersion": 0,
                "contentFree": True,
            }
        }
        return "final answer"

    def get_session_lock(session_key: str) -> asyncio.Lock:
        locks = state["locks"]
        if session_key not in locks:
            locks[session_key] = asyncio.Lock()
        return locks[session_key]

    async def commit_session_continuity(*args):
        state["lockChecks"].append(
            ("commit", get_session_lock(args[0]).locked())
        )
        state["events"].append("commit")
        state["commitTargets"].append(args)
        return durable_continuity_status(4)

    def begin_user_text_turn(session_key, user_text, **kwargs):
        state["lockChecks"].append(
            ("begin", get_session_lock(session_key).locked())
        )
        turn_id = f"search-turn:{session_key}"
        state["turnId"] = turn_id
        state["turnStarts"].append(
            (session_key, user_text, kwargs, turn_id)
        )
        return SimpleNamespace(
            turn_id=turn_id,
            topic_id=f"topic:{user_text}",
            history=[
                {"role": "user", "content": "recent"},
                {"role": "user", "content": user_text},
            ],
        )

    def unexpected_history_read(**_kwargs):
        raise AssertionError("accepted turn snapshot must be reused")

    def schedule_local_control_tts(*args, **kwargs):
        state["lockChecks"].append(
            ("tts", get_session_lock(kwargs["session_key"]).locked())
        )
        state["events"].append("tts")
        state["tts"].append((args, kwargs))

    def replace_room_turn_scope(session_key, turn_scope):
        state["currentScopes"][session_key] = turn_scope
        state["lifecycle"].append(
            ("replace", session_key, turn_scope)
        )

    def get_room_turn_scope(session_key):
        return state["currentScopes"].get(session_key)

    def attach_current_task(turn_scope):
        state["lifecycle"].append(("attach", turn_scope))
        return "search-task"

    def detach_task(turn_scope, turn_task):
        state["lifecycle"].append(
            ("detach", turn_scope, turn_task)
        )

    def clear_room_turn_scope(session_key, turn_scope):
        if state["currentScopes"].get(session_key) is turn_scope:
            state["currentScopes"].pop(session_key, None)
        state["lifecycle"].append(
            ("clear", session_key, turn_scope)
        )

    deps = ControlPageSearchRuntimeDeps(
        control_page_effective_guild_id=lambda guild: int(getattr(guild, "id", 999) or 999),
        control_page_session_key=lambda guild_id: f"control:{guild_id}",
        get_conversation_history=unexpected_history_read,
        memory_index_dir=REPO_ROOT / "unused-memory-index",
        build_route_decision=lambda **kwargs: state["route"].append(kwargs) or SimpleNamespace(**kwargs),
        monotonic=lambda: 12.5,
        execute_search_then_answer_action=execute_search_then_answer_action,
        synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm,
        clean_text=lambda text: text.strip(),
        get_session_lock=get_session_lock,
        begin_user_text_turn=begin_user_text_turn,
        turn_scope_factory=TurnScope,
        replace_room_turn_scope=replace_room_turn_scope,
        get_room_turn_scope=get_room_turn_scope,
        attach_current_task=attach_current_task,
        append_history=lambda *args, **kwargs: (
            state["events"].append("history"),
            state["history"].append((args, kwargs)),
        )[-1],
        mark_session_active=lambda *args, **kwargs: (
            state["events"].append("active"),
            state["active"].append((args, kwargs)),
        )[-1],
        commit_session_continuity=commit_session_continuity,
        active_conversation_text_sec=30.0,
        build_topic_id=lambda *texts: "topic:" + "|".join(texts),
        schedule_local_control_tts=schedule_local_control_tts,
        format_display_text=lambda text, **_kwargs: f"display:{text}",
        fallback_answer_for=lambda text: f"fallback:{text}",
        detach_task=detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
        log=lambda *args, **kwargs: None,
    )
    if overrides:
        deps = ControlPageSearchRuntimeDeps(**{**deps.__dict__, **overrides})
    return deps, state


class ControlPageSearchRuntimeTests(unittest.TestCase):
    async def _run(self, deps: ControlPageSearchRuntimeDeps) -> str:
        return await answer_control_page_search_text_from_runtime(SimpleNamespace(id=7), "오늘 날씨", deps=deps)

    def test_search_answer_runs_search_synthesis_and_records_session_state(self) -> None:
        deps, state = _deps()

        reply = asyncio.run(self._run(deps))

        self.assertEqual(reply, "display:final answer")
        self.assertEqual(state["route"][0]["route"], "search_executor")
        self.assertEqual(state["route"][0]["needs_search"], True)
        self.assertEqual(state["search"][0]["session_key"], "control:7")
        self.assertEqual(state["synthesis"][0]["tool_result_text"], "search answer")
        self.assertEqual(state["synthesis"][0]["metrics"]["meta"]["selected_path"], "control_page_search_direct")
        self.assertTrue(
            state["synthesis"][0]["metrics"]["meta"][
                "accepted_user_turn_precommitted"
            ]
        )
        self.assertEqual(
            state["search"][0]["messages"],
            [
                {"role": "user", "content": "recent"},
                {"role": "user", "content": "오늘 날씨"},
            ],
        )
        self.assertEqual(state["history"][0][0], ("control:7", "오늘 날씨", "final answer"))
        self.assertEqual(
            state["history"][0][1]["complete_turn_id"],
            "search-turn:control:7",
        )
        self.assertEqual(state["active"][0][1]["topic_id"], "topic:오늘 날씨|search_executor|final answer")
        self.assertEqual(state["tts"][0][0], ("final answer",))
        self.assertEqual(state["turnStarts"][0][0:2], ("control:7", "오늘 날씨"))
        self.assertEqual(state["turnStarts"][0][3], "search-turn:control:7")
        self.assertNotEqual(state["turnStarts"][0][3], "prior-turn")
        self.assertEqual(
            state["lockChecks"],
            [
                ("begin", True),
                ("commit", True),
                ("search", False),
                ("synthesis", False),
                ("commit", True),
                ("tts", True),
            ],
        )
        self.assertEqual(state["tts"][0][1]["turn_id"], "search-turn:control:7")
        self.assertEqual(
            state["tts"][0][1]["turn_scope"].turn_id,
            "search-turn:control:7",
        )
        self.assertEqual(
            [event[0] for event in state["lifecycle"]],
            ["replace", "attach", "detach", "clear"],
        )
        self.assertEqual(
            state["events"],
            ["commit", "history", "active", "commit", "tts"],
        )
        self.assertEqual(
            state["commitTargets"],
            [
                ("control:7", "search-turn:control:7"),
                ("control:7", "search-turn:control:7"),
            ],
        )
        self.assertEqual(
            state["synthesis"][0]["metrics"]["meta"]["continuity_generation"],
            4,
        )
        self.assertEqual(
            state["history"][0][1]["memory_receipt"]["state"],
            "not_used",
        )

    def test_search_cancels_prior_normal_turn_before_stale_sinks(self) -> None:
        async def scenario() -> None:
            store = SessionStateStore.create_empty()
            session_key = "control:0"
            state_lock = asyncio.Lock()
            registry = TurnScopeRegistry()
            normal_started = asyncio.Event()
            release_normal = asyncio.Event()
            playback_started = asyncio.Event()
            release_playback = asyncio.Event()
            commits: list[tuple[str, str]] = []
            tts: list[tuple[str, str]] = []
            tts_tasks: list[asyncio.Task] = []

            def begin_turn(key, text, **kwargs):
                if kwargs.pop("precommit_user_only", False):
                    kwargs.pop("guild_id", None)
                    user_id = kwargs.pop("user_id", None)
                    turn_id = (
                        "normal-turn"
                        if text == "normal request"
                        else "search-turn"
                    )
                    topic_id = build_topic_id(
                        text,
                        store.topic_ids.get(key, ""),
                    )
                    store.begin_user_only_turn(
                        key,
                        text,
                        turn_id=turn_id,
                        system_prompt="system",
                        max_history_items=12,
                        user_id=user_id,
                        ttl_sec=30.0,
                        topic_id=topic_id,
                        active_conversation_awaiting_reply_sec=30.0,
                    )
                    return SimpleNamespace(
                        turn_id=turn_id,
                        topic_id=topic_id,
                        history=[
                            dict(message)
                            for message in store.get_conversation_history(
                                system_prompt="system",
                                session_key=key,
                            )
                        ],
                    )
                return store.begin_user_text_turn(
                    key,
                    text,
                    system_prompt="system",
                    active_conversation_awaiting_reply_sec=30.0,
                    max_history_items=12,
                    turn_id=(
                        "normal-turn"
                        if text == "normal request"
                        else "search-turn"
                    ),
                    **kwargs,
                )

            async def commit(key, turn_id):
                if store.current_turn_id(key) != turn_id:
                    raise RuntimeError("stale turn")
                commits.append((key, turn_id))
                return durable_continuity_status(len(commits))

            async def ask_normal(_text, **kwargs):
                kwargs["metrics"]["meta"]["context_pipeline"] = {
                    "memory_receipt": {
                        "schema": "memory.context-receipt.v1",
                        "state": "not_requested",
                        "memoryVersion": 0,
                        "contentFree": True,
                    }
                }
                normal_started.set()
                await release_normal.wait()
                return "normal answer"

            async def play_tts():
                playback_started.set()
                await release_playback.wait()

            def schedule_tts(answer, **kwargs):
                tts.append((answer, kwargs["turn_id"]))
                task = registry.create_scoped_task(
                    play_tts(),
                    turn_scope=kwargs["turn_scope"],
                )
                tts_tasks.append(task)
                return task

            normal_deps = ControlPageTextRuntimeDeps(
                memory_index_dir=REPO_ROOT / "unused-memory-index",
                effective_guild_id=lambda _guild: 0,
                session_key_for_guild=lambda _guild_id: session_key,
                get_session_lock=lambda _key: state_lock,
                begin_user_text_turn=begin_turn,
                turn_scope_factory=TurnScope,
                replace_room_turn_scope=registry.replace_room_scope,
                attach_current_task=registry.attach_current_task,
                monotonic=lambda: 1.0,
                resolve_pending_proactive_question_for_turn=(
                    lambda *_args, **_kwargs: {"resolved": True}
                ),
                ask_llm_streaming=ask_normal,
                clean_text=lambda text: text.strip(),
                strip_omnivoice_tags=lambda text: text,
                session_state_snapshot=store.snapshot,
                maybe_append_proactive_question=(
                    lambda answer, **_kwargs: (answer, False)
                ),
                finish_assistant_text_turn=lambda key, user, answer, **kwargs: (
                    store.finish_assistant_text_turn(
                        key,
                        user,
                        answer,
                        system_prompt="system",
                        max_history_items=12,
                        normal_ttl_sec=30.0,
                        question_ttl_sec=30.0,
                        **kwargs,
                    )
                ),
                commit_session_continuity=commit,
                log_voice_bottleneck_summary=lambda *_args, **_kwargs: None,
                schedule_local_control_tts=schedule_tts,
                format_display_text=lambda text, **_kwargs: text,
                fallback_answer_for=lambda _text: "fallback",
                detach_task=registry.detach_task,
                clear_room_turn_scope=registry.clear_room_scope,
                log=lambda *_args, **_kwargs: None,
            )

            async def execute_search(**_kwargs):
                return SimpleNamespace(answer_text="search answer")

            async def synthesize_search(**kwargs):
                kwargs["metrics"]["meta"]["context_pipeline"] = {
                    "memory_receipt": {
                        "schema": "memory.context-receipt.v1",
                        "state": "not_requested",
                        "memoryVersion": 0,
                        "contentFree": True,
                    }
                }
                return "search final answer"

            search_deps, _state = _deps(
                control_page_effective_guild_id=lambda _guild: 0,
                control_page_session_key=lambda _guild_id: session_key,
                get_conversation_history=lambda **kwargs: (
                    store.get_conversation_history(
                        system_prompt="system",
                        **kwargs,
                    )
                ),
                execute_search_then_answer_action=execute_search,
                synthesize_tool_result_with_main_llm=synthesize_search,
                get_session_lock=lambda _key: state_lock,
                begin_user_text_turn=begin_turn,
                turn_scope_factory=TurnScope,
                replace_room_turn_scope=registry.replace_room_scope,
                get_room_turn_scope=registry.get_room_scope,
                attach_current_task=registry.attach_current_task,
                append_history=lambda key, user, answer, **kwargs: (
                    store.append_history(
                        key,
                        user,
                        answer,
                        system_prompt="system",
                        max_history_items=12,
                        **kwargs,
                    )
                ),
                mark_session_active=lambda key, **kwargs: store.mark_active(
                    key,
                    active_conversation_awaiting_reply_sec=30.0,
                    **kwargs,
                ),
                commit_session_continuity=commit,
                build_topic_id=build_topic_id,
                schedule_local_control_tts=schedule_tts,
                format_display_text=lambda text, **_kwargs: text,
                detach_task=registry.detach_task,
                clear_room_turn_scope=registry.clear_room_scope,
            )

            normal_task = asyncio.create_task(
                answer_control_page_text_from_runtime(
                    None,
                    "normal request",
                    deps=normal_deps,
                )
            )
            await normal_started.wait()
            normal_scope = registry.get_room_scope(session_key)

            search_reply = await answer_control_page_search_text_from_runtime(
                None,
                "search request",
                deps=search_deps,
            )
            await playback_started.wait()
            search_scope = registry.get_room_scope(session_key)
            successor_scope = TurnScope("successor-turn")
            registry.replace_room_scope(session_key, successor_scope)
            release_normal.set()
            with self.assertRaises(asyncio.CancelledError):
                await normal_task
            with self.assertRaises(asyncio.CancelledError):
                await tts_tasks[0]

            history = [
                (message["role"], message["content"])
                for message in store.histories[session_key]
                if message["role"] != "system"
            ]
            self.assertEqual(search_reply, "search final answer")
            self.assertTrue(normal_scope.cancelled)
            self.assertTrue(search_scope.cancelled)
            self.assertIs(
                registry.get_room_scope(session_key),
                successor_scope,
            )
            self.assertEqual(store.current_turn_id(session_key), "search-turn")
            self.assertEqual(
                commits,
                [
                    (session_key, "normal-turn"),
                    (session_key, "search-turn"),
                    (session_key, "search-turn"),
                ],
            )
            self.assertEqual(tts, [("search final answer", "search-turn")])
            self.assertEqual(
                history,
                [
                    ("user", "normal request"),
                    ("user", "search request"),
                    ("assistant", "search final answer"),
                ],
            )

        asyncio.run(scenario())

    def test_successor_normal_cancels_inflight_search_before_sinks(self) -> None:
        async def scenario() -> None:
            session_key = "control:7"
            state_lock = asyncio.Lock()
            registry = TurnScopeRegistry()
            search_started = asyncio.Event()
            never_release_search = asyncio.Event()
            normal_history: list[tuple[object, ...]] = []
            normal_commits: list[tuple[object, ...]] = []
            normal_tts: list[tuple[object, ...]] = []

            async def blocked_search(**_kwargs):
                search_started.set()
                await never_release_search.wait()
                return SimpleNamespace(answer_text="stale search")

            search_deps, search_state = _deps(
                execute_search_then_answer_action=blocked_search,
                get_session_lock=lambda _key: state_lock,
                turn_scope_factory=TurnScope,
                replace_room_turn_scope=registry.replace_room_scope,
                get_room_turn_scope=registry.get_room_scope,
                attach_current_task=registry.attach_current_task,
                detach_task=registry.detach_task,
                clear_room_turn_scope=registry.clear_room_scope,
            )

            async def ask_normal(_text, **kwargs):
                kwargs["metrics"]["meta"]["context_pipeline"] = {
                    "memory_receipt": {
                        "schema": "memory.context-receipt.v1",
                        "state": "not_requested",
                        "memoryVersion": 0,
                        "contentFree": True,
                    }
                }
                return "successor answer"

            async def commit_normal(*args):
                normal_commits.append(args)
                return durable_continuity_status(1)

            normal_deps = ControlPageTextRuntimeDeps(
                memory_index_dir=REPO_ROOT / "unused-memory-index",
                effective_guild_id=lambda _guild: 7,
                session_key_for_guild=lambda _guild_id: session_key,
                get_session_lock=lambda _key: state_lock,
                begin_user_text_turn=lambda *_args, **_kwargs: (
                    SimpleNamespace(
                        turn_id="successor-turn",
                        topic_id="successor-topic",
                    )
                ),
                turn_scope_factory=TurnScope,
                replace_room_turn_scope=registry.replace_room_scope,
                attach_current_task=registry.attach_current_task,
                monotonic=lambda: 2.0,
                resolve_pending_proactive_question_for_turn=(
                    lambda *_args, **_kwargs: {"resolved": True}
                ),
                ask_llm_streaming=ask_normal,
                clean_text=lambda text: text.strip(),
                strip_omnivoice_tags=lambda text: text,
                session_state_snapshot=lambda _key: {},
                maybe_append_proactive_question=(
                    lambda answer, **_kwargs: (answer, False)
                ),
                finish_assistant_text_turn=lambda *args, **_kwargs: (
                    normal_history.append(args)
                ),
                commit_session_continuity=commit_normal,
                log_voice_bottleneck_summary=lambda *_args, **_kwargs: None,
                schedule_local_control_tts=lambda *args, **_kwargs: (
                    normal_tts.append(args)
                ),
                format_display_text=lambda text, **_kwargs: text,
                fallback_answer_for=lambda _text: "fallback",
                detach_task=registry.detach_task,
                clear_room_turn_scope=registry.clear_room_scope,
                log=lambda *_args, **_kwargs: None,
            )

            search_task = asyncio.create_task(self._run(search_deps))
            await search_started.wait()
            search_scope = registry.get_room_scope(session_key)
            try:
                successor_reply = await asyncio.wait_for(
                    answer_control_page_text_from_runtime(
                        None,
                        "successor request",
                        deps=normal_deps,
                    ),
                    timeout=1.0,
                )
            finally:
                if not search_task.done():
                    search_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await search_task

            self.assertEqual(successor_reply, "successor answer")
            self.assertTrue(search_scope.cancelled)
            self.assertEqual(search_state["history"], [])
            self.assertEqual(
                search_state["commitTargets"],
                [(session_key, f"search-turn:{session_key}")],
            )
            self.assertEqual(search_state["tts"], [])
            self.assertEqual(len(normal_history), 1)
            self.assertEqual(
                normal_commits,
                [
                    (session_key, "successor-turn"),
                    (session_key, "successor-turn"),
                ],
            )
            self.assertEqual(normal_tts, [("successor answer",)])

        asyncio.run(scenario())

    def test_search_answer_falls_back_to_action_result_when_synthesis_is_empty(self) -> None:
        async def synthesize_tool_result_with_main_llm(**kwargs):
            kwargs["metrics"]["meta"]["context_pipeline"] = {
                "memory_receipt": {
                    "schema": "memory.context-receipt.v1",
                    "state": "not_requested",
                    "memoryVersion": 0,
                    "contentFree": True,
                }
            }
            return "   "

        deps, _state = _deps(synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm)

        reply = asyncio.run(self._run(deps))

        self.assertEqual(reply, "display:search answer")

    def test_search_failure_restores_precommitted_unanswered_user(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = SessionStateStore.create_empty()
                checkpoint = SessionContinuityCheckpoint(
                    store=store,
                    checkpoint_path=root / "active.json",
                    status_path=root / "status.json",
                    system_prompt="system",
                )
                session_deps = session_runtime_deps(
                    root,
                    store,
                    turn_id="search-accepted",
                )
                state_lock = asyncio.Lock()

                async def fail_search(**_kwargs):
                    raise RuntimeError("search failed")

                deps, state = _deps(
                    get_session_lock=lambda _key: state_lock,
                    begin_user_text_turn=lambda *args, **kwargs: (
                        begin_user_text_turn_from_runtime(
                            *args,
                            **kwargs,
                            deps=session_deps,
                        )
                    ),
                    commit_session_continuity=(
                        checkpoint.commit_completed_turn_async
                    ),
                    execute_search_then_answer_action=fail_search,
                )

                with self.assertRaisesRegex(
                    RuntimeError,
                    "^search failed$",
                ):
                    await self._run(deps)

                restored_store = SessionStateStore.create_empty()
                restored = SessionContinuityCheckpoint(
                    store=restored_store,
                    checkpoint_path=root / "active.json",
                    status_path=root / "restored-status.json",
                    system_prompt="system",
                ).restore()

                self.assertEqual(restored["state"], "restored")
                self.assertEqual(
                    restored_store.histories["control:7"],
                    [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "오늘 날씨"},
                    ],
                )
                self.assertEqual(
                    restored_store.current_turn_id("control:7"),
                    "search-accepted",
                )
                self.assertEqual(state["tts"], [])

        asyncio.run(scenario())

    def test_cancelled_completion_commit_holds_lock_until_disk_drain(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = SessionStateStore.create_empty()
                checkpoint = SessionContinuityCheckpoint(
                    store=store,
                    checkpoint_path=root / "active.json",
                    status_path=root / "status.json",
                    system_prompt="system",
                )
                session_deps = session_runtime_deps(
                    root,
                    store,
                    turn_id="search-cancelled",
                )
                commit_started = threading.Event()
                release_commit = threading.Event()
                state_lock = asyncio.Lock()
                commit_count = 0

                def pause_physical_commit(_generation: int) -> None:
                    commit_started.set()
                    if not release_commit.wait(timeout=2.0):
                        raise TimeoutError("test_commit_release_timed_out")

                async def commit(
                    session_key: str,
                    turn_id: str,
                ) -> dict[str, object]:
                    nonlocal commit_count
                    commit_count += 1
                    before_commit = (
                        pause_physical_commit
                        if commit_count == 2
                        else None
                    )
                    return await checkpoint.commit_completed_turn_async(
                        session_key,
                        turn_id,
                        before_commit=before_commit,
                    )

                deps, state = _deps(
                    get_session_lock=lambda _key: state_lock,
                    begin_user_text_turn=lambda *args, **kwargs: (
                        begin_user_text_turn_from_runtime(
                            *args,
                            **kwargs,
                            deps=session_deps,
                        )
                    ),
                    append_history=lambda *args, **kwargs: (
                        store.append_history(
                            *args,
                            system_prompt="system",
                            max_history_items=12,
                            **kwargs,
                        )
                    ),
                    mark_session_active=lambda *args, **kwargs: (
                        store.mark_active(
                            *args,
                            active_conversation_awaiting_reply_sec=120.0,
                            **kwargs,
                        )
                    ),
                    commit_session_continuity=commit,
                )
                answer_task = asyncio.create_task(self._run(deps))
                successor_acquired = asyncio.Event()
                successor_task: asyncio.Task[None] | None = None
                try:
                    self.assertTrue(
                        await asyncio.to_thread(
                            commit_started.wait,
                            1.0,
                        )
                    )
                    answer_task.cancel()

                    async def acquire_successor_lock() -> None:
                        async with state_lock:
                            successor_acquired.set()

                    successor_task = asyncio.create_task(
                        acquire_successor_lock()
                    )
                    await asyncio.sleep(0)
                    self.assertFalse(answer_task.done())
                    self.assertFalse(successor_acquired.is_set())
                    self.assertEqual(state["tts"], [])

                    release_commit.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(answer_task, timeout=2.0)
                    await asyncio.wait_for(successor_task, timeout=1.0)

                    restored_store = SessionStateStore.create_empty()
                    restored = SessionContinuityCheckpoint(
                        store=restored_store,
                        checkpoint_path=root / "active.json",
                        status_path=root / "restored-status.json",
                        system_prompt="system",
                    ).restore()
                    self.assertEqual(restored["state"], "restored")
                    self.assertEqual(
                        [
                            (row["role"], row["content"])
                            for row in restored_store.histories["control:7"]
                        ],
                        [
                            ("system", "system"),
                            ("user", "오늘 날씨"),
                            ("assistant", "final answer"),
                        ],
                    )
                    self.assertEqual(state["tts"], [])
                finally:
                    release_commit.set()
                    answer_task.cancel()
                    pending = [answer_task]
                    if successor_task is not None:
                        pending.append(successor_task)
                    await asyncio.gather(
                        *pending,
                        return_exceptions=True,
                    )

        asyncio.run(scenario())

    def test_stale_scope_reaches_no_final_sink(self) -> None:
        deps, state = _deps(
            get_room_turn_scope=lambda _key: TurnScope("successor-turn")
        )

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self._run(deps))

        self.assertEqual(state["history"], [])
        self.assertEqual(state["active"], [])
        self.assertEqual(
            state["commitTargets"],
            [("control:7", "search-turn:control:7")],
        )
        self.assertEqual(state["tts"], [])

    def test_partial_completion_commit_fails_closed_before_tts(
        self,
    ) -> None:
        private = (
            "Bearer search-continuity-secret "
            "https://internal.example/private"
        )

        commit_count = 0

        async def partial_commit(*_args):
            nonlocal commit_count
            commit_count += 1
            if commit_count == 1:
                return durable_continuity_status(4)
            return {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            }

        deps, state = _deps(
            commit_session_continuity=partial_commit
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^conversation_continuity_commit_failed$",
        ):
            asyncio.run(self._run(deps))
        metrics = state["synthesis"][0]["metrics"]["meta"]

        self.assertEqual(
            metrics["continuity_commit"],
            "failed",
        )
        self.assertEqual(
            metrics["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn(private, str(metrics))
        self.assertEqual(state["tts"], [])

    def test_partial_precommit_reaches_no_search_or_sink(self) -> None:
        async def partial_commit(*_args):
            return {
                "state": "ready",
                "rollbackProtected": True,
            }

        deps, state = _deps(
            commit_session_continuity=partial_commit
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^conversation_continuity_commit_failed$",
        ):
            asyncio.run(self._run(deps))

        self.assertEqual(state["search"], [])
        self.assertEqual(state["synthesis"], [])
        self.assertEqual(state["history"], [])
        self.assertEqual(state["active"], [])
        self.assertEqual(state["tts"], [])
        self.assertEqual(state["lifecycle"], [])

    def test_bound_receipt_note_mismatch_reaches_no_sink(self) -> None:
        async def synthesize_with_mismatched_boundary(**kwargs):
            capture_memory_exposure_position(memory_exposure())
            kwargs["metrics"]["meta"]["context_pipeline"] = {
                "memory_receipt": {
                    "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
                    "state": "bound",
                    "memoryVersion": 7,
                    "suppliedNoteIds": [NOTE_B],
                    "suppliedNoteCount": 1,
                    "contentFree": True,
                }
            }
            return "private mismatched reply"

        deps, state = _deps(
            synthesize_tool_result_with_main_llm=(
                synthesize_with_mismatched_boundary
            )
        )

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            asyncio.run(self._run(deps))

        self.assertEqual(state["history"], [])
        self.assertEqual(state["active"], [])
        self.assertEqual(
            state["commitTargets"],
            [("control:7", "search-turn:control:7")],
        )
        self.assertEqual(state["tts"], [])
        self.assertEqual(state["events"], ["commit"])
        self.assertEqual(
            [event[0] for event in state["lifecycle"]],
            ["replace", "attach", "detach", "clear"],
        )


if __name__ == "__main__":
    unittest.main()
