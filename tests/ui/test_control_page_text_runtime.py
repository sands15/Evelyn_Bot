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

from evelyn_core.control_page_text_runtime import (  # noqa: E402
    ControlPageTextRuntimeDeps,
    answer_control_page_text_from_runtime,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
    current_conversation_memory_receipt_ref,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MEMORY_DELETION_POSITION_SCHEMA,
    MemoryDeletionJournalIntegrityError,
    MemoryDeletionPosition,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MemoryExposurePosition,
    capture_memory_exposure_position,
    reset_memory_exposure_position,
)
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from evelyn_core.session_memory_state import (  # noqa: E402
    SessionStateStore,
    build_topic_id,
)
from evelyn_core.session_turn_runtime import (  # noqa: E402
    SessionTurnRuntimeDeps,
    begin_user_text_turn_from_runtime,
    finish_assistant_text_turn_from_runtime,
)
from evelyn_core.turn_lifecycle import TurnScope, TurnScopeRegistry  # noqa: E402
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


class FakeScope:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id


NOTE_ID = "concept-0123456789abcdef"


def memory_exposure(version: int) -> MemoryExposurePosition:
    return MemoryExposurePosition(
        deletion_position=MemoryDeletionPosition(
            schema=MEMORY_DELETION_POSITION_SCHEMA,
            root_digest="1" * 64,
            sequence=1,
            position_digest="2" * 64,
        ),
        memory_version=version,
        supplied_note_ids=(NOTE_ID,),
    )


class ControlPageTextRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.lock = asyncio.Lock()
        self.proactive_resolved = False
        self.append_proactive = True
        self.black_frame = False
        self.ask_error: Exception | None = None
        self.replaced: list[tuple[str, FakeScope]] = []
        self.finished: list[tuple[tuple, dict]] = []
        self.summaries: list[tuple[dict, dict]] = []
        self.scheduled: list[tuple[tuple, dict]] = []
        self.detached: list[tuple[FakeScope, object]] = []
        self.cleared: list[tuple[str, FakeScope]] = []
        self.append_calls: list[tuple[tuple, dict]] = []
        self.commits: list[str] = []
        self.commit_targets: list[tuple[object, ...]] = []

    async def ask_streaming(self, _text: str, **kwargs) -> str:
        if self.ask_error is not None:
            raise self.ask_error
        kwargs["metrics"]["meta"]["context_pipeline"] = {
            "memory_receipt": {
                "schema": "memory.context-receipt.v1",
                "state": "not_requested",
                "memoryVersion": 0,
                "contentFree": True,
            }
        }
        if self.black_frame:
            kwargs["metrics"]["meta"]["vision_capture_error"] = "DXGI black frame"
        return "[question-oh] 원본 답변"

    def maybe_append(self, *args, **kwargs) -> tuple[str, bool]:
        self.append_calls.append((args, kwargs))
        return f"{args[0]} 추가 질문?", self.append_proactive

    def build_deps(self) -> ControlPageTextRuntimeDeps:
        async def commit_session_continuity(*args):
            self.commits.append("commit")
            self.commit_targets.append(args)
            return durable_continuity_status(6)

        return ControlPageTextRuntimeDeps(
            memory_index_dir=Path("unused-memory-index"),
            effective_guild_id=lambda guild: guild.id if guild is not None else 0,
            session_key_for_guild=lambda guild_id: f"control:{guild_id}",
            get_session_lock=lambda _key: self.lock,
            begin_user_text_turn=lambda *_args, **_kwargs: SimpleNamespace(turn_id="turn-1", topic_id="topic-1"),
            turn_scope_factory=FakeScope,
            replace_room_turn_scope=lambda key, scope: self.replaced.append((key, scope)),
            attach_current_task=lambda _scope: "attached-task",
            monotonic=lambda: 10.0,
            resolve_pending_proactive_question_for_turn=lambda *_args, **_kwargs: {
                "resolved": self.proactive_resolved
            },
            ask_llm_streaming=self.ask_streaming,
            clean_text=lambda text: text.strip(),
            strip_omnivoice_tags=lambda text: text.replace("[question-oh]", "").strip(),
            session_state_snapshot=lambda _key: {"awaiting_user_reply": False},
            maybe_append_proactive_question=self.maybe_append,
            finish_assistant_text_turn=lambda *args, **kwargs: self.finished.append((args, kwargs)),
            commit_session_continuity=commit_session_continuity,
            log_voice_bottleneck_summary=lambda metrics, **kwargs: self.summaries.append((metrics, kwargs)),
            schedule_local_control_tts=lambda *args, **kwargs: self.scheduled.append((args, kwargs)),
            format_display_text=lambda text, **_kwargs: f" display:{text} ",
            fallback_answer_for=lambda _text: "fallback",
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            clear_room_turn_scope=lambda key, scope: self.cleared.append((key, scope)),
            log=lambda *_args: None,
        )

    async def test_answer_records_turn_appends_question_and_schedules_local_tts(self) -> None:
        result = await answer_control_page_text_from_runtime(
            SimpleNamespace(id=7),
            "질문",
            deps=self.build_deps(),
        )

        self.assertEqual(result, "display:원본 답변 추가 질문?")
        self.assertEqual(self.replaced[0][0], "control:7")
        self.assertEqual(self.finished[0][0][:3], ("control:7", "질문", "원본 답변 추가 질문?"))
        self.assertTrue(self.finished[0][1]["awaiting_user_reply"])
        self.assertEqual(self.commits, ["commit", "commit"])
        self.assertEqual(
            self.commit_targets,
            [
                ("control:7", "turn-1"),
                ("control:7", "turn-1"),
            ],
        )
        self.assertEqual(
            self.finished[0][1]["complete_turn_id"],
            "turn-1",
        )
        self.assertEqual(self.scheduled[0][0], ("원본 답변 추가 질문?",))
        self.assertEqual(self.scheduled[0][1]["turn_id"], "turn-1")
        self.assertEqual(self.summaries[0][1]["event_name"], "text_turn_summary")
        self.assertEqual(
            self.finished[0][1]["memory_receipt"]["state"],
            "not_used",
        )
        self.assertEqual(
            current_conversation_memory_receipt_ref()["state"],
            "not_used",
        )
        self.assertEqual(self.detached[0][1], "attached-task")
        self.assertEqual(self.cleared[0][0], "control:7")

    async def test_tts_owns_scope_until_done_and_cannot_clear_successor(self) -> None:
        registry = TurnScopeRegistry()
        playback_started = asyncio.Event()
        release_playback = asyncio.Event()
        tts_tasks: list[asyncio.Task] = []

        async def play_tts() -> None:
            playback_started.set()
            await release_playback.wait()

        def schedule_tts(*_args, **kwargs):
            task = registry.create_scoped_task(
                play_tts(),
                turn_scope=kwargs["turn_scope"],
            )
            tts_tasks.append(task)
            return task

        deps = self.build_deps()
        deps = ControlPageTextRuntimeDeps(
            **{
                **deps.__dict__,
                "turn_scope_factory": TurnScope,
                "replace_room_turn_scope": registry.replace_room_scope,
                "attach_current_task": registry.attach_current_task,
                "schedule_local_control_tts": schedule_tts,
                "detach_task": registry.detach_task,
                "clear_room_turn_scope": registry.clear_room_scope,
            }
        )

        await answer_control_page_text_from_runtime(
            None,
            "질문",
            deps=deps,
        )
        await playback_started.wait()
        tts_task = tts_tasks[0]

        async def cleanup() -> None:
            release_playback.set()
            if not tts_task.done():
                tts_task.cancel()
            await asyncio.gather(tts_task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        scope = registry.get_room_scope("control:0")
        self.assertIsNotNone(scope)
        self.assertIn(tts_task, scope.tasks)

        successor = TurnScope("successor-turn")
        registry.replace_room_scope("control:0", successor)
        with self.assertRaises(asyncio.CancelledError):
            await tts_task
        await asyncio.sleep(0)

        self.assertTrue(scope.cancelled)
        self.assertIs(
            registry.get_room_scope("control:0"),
            successor,
        )

    async def test_resolved_proactive_question_skips_append(self) -> None:
        self.proactive_resolved = True

        result = await answer_control_page_text_from_runtime(None, "질문", deps=self.build_deps())

        self.assertEqual(result, "display:원본 답변")
        self.assertEqual(self.append_calls, [])
        self.assertFalse(self.finished[0][1]["awaiting_user_reply"])

    async def test_black_frame_error_replaces_model_answer(self) -> None:
        self.black_frame = True
        self.proactive_resolved = True

        result = await answer_control_page_text_from_runtime(None, "화면 보여?", deps=self.build_deps())

        self.assertIn("검은 프레임", result)
        self.assertIn("Windows 캡처 세션", self.finished[0][0][2])
        self.assertNotIn("원본 답변", result)

    async def test_partial_commit_status_is_not_marked_durable(
        self,
    ) -> None:
        private = (
            "Bearer control-continuity-secret "
            r"C:\Users\Admin\checkpoint.json"
        )

        commit_count = 0

        async def partial_commit(*_args):
            nonlocal commit_count
            commit_count += 1
            if commit_count == 1:
                return durable_continuity_status(5)
            return {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            }

        deps = self.build_deps()
        deps = ControlPageTextRuntimeDeps(
            **{
                **deps.__dict__,
                "commit_session_continuity": partial_commit,
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^conversation_continuity_commit_failed$",
        ):
            await answer_control_page_text_from_runtime(
                None,
                "질문",
                deps=deps,
            )
        metrics = self.summaries[0][0]["meta"]

        self.assertEqual(
            metrics["continuity_commit"],
            "failed",
        )
        self.assertEqual(
            metrics["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn(private, str(self.summaries))
        self.assertEqual(self.scheduled, [])

    async def test_failure_logs_error_summary_and_always_clears_scope(self) -> None:
        self.ask_error = RuntimeError("LLM failed")

        with self.assertRaisesRegex(RuntimeError, "LLM failed"):
            await answer_control_page_text_from_runtime(None, "질문", deps=self.build_deps())

        metrics, summary = self.summaries[0]
        self.assertEqual(metrics["meta"]["error_layer"], "control_page_text")
        self.assertEqual(metrics["meta"]["error"], "control_page_text_aborted_before_summary")
        self.assertEqual(summary["extra"], "control_page=true error=true")
        self.assertEqual(len(self.detached), 1)
        self.assertEqual(len(self.cleared), 1)
        self.assertEqual(self.commits, ["commit"])

    async def test_llm_failure_restores_precommitted_unanswered_user_turn(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SessionStateStore.create_empty()
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
            )
            session_deps = SessionTurnRuntimeDeps(
                session_state_store=store,
                system_prompt="system",
                memory_index_dir=root / "memory_index",
                active_conversation_awaiting_reply_sec=120.0,
                active_conversation_text_question_sec=120.0,
                active_conversation_text_sec=90.0,
                max_history_items=12,
                session_topic_ids=store.topic_ids,
                build_topic_id_fn=build_topic_id,
                new_turn_id_fn=lambda: "turn-accepted",
            )
            registry = TurnScopeRegistry()
            lock = asyncio.Lock()

            async def fail_llm(*_args: Any, **_kwargs: Any) -> str:
                raise RuntimeError("LLM failed")

            deps = ControlPageTextRuntimeDeps(
                memory_index_dir=root / "memory_index",
                effective_guild_id=lambda _guild: 0,
                session_key_for_guild=lambda _guild_id: "control:0",
                get_session_lock=lambda _key: lock,
                begin_user_text_turn=lambda *args, **kwargs: (
                    begin_user_text_turn_from_runtime(
                        *args,
                        **kwargs,
                        deps=session_deps,
                    )
                ),
                turn_scope_factory=TurnScope,
                replace_room_turn_scope=registry.replace_room_scope,
                attach_current_task=registry.attach_current_task,
                monotonic=lambda: 10.0,
                resolve_pending_proactive_question_for_turn=(
                    lambda *_args, **_kwargs: {"resolved": False}
                ),
                ask_llm_streaming=fail_llm,
                clean_text=lambda text: text.strip(),
                strip_omnivoice_tags=lambda text: text,
                session_state_snapshot=store.snapshot,
                maybe_append_proactive_question=(
                    lambda answer, **_kwargs: (answer, False)
                ),
                finish_assistant_text_turn=lambda *args, **kwargs: (
                    finish_assistant_text_turn_from_runtime(
                        *args,
                        **kwargs,
                        deps=session_deps,
                    )
                ),
                commit_session_continuity=(
                    checkpoint.commit_completed_turn_async
                ),
                log_voice_bottleneck_summary=(
                    lambda *_args, **_kwargs: None
                ),
                schedule_local_control_tts=(
                    lambda *_args, **_kwargs: None
                ),
                format_display_text=lambda text, **_kwargs: text,
                fallback_answer_for=lambda _text: "fallback",
                detach_task=registry.detach_task,
                clear_room_turn_scope=registry.clear_room_scope,
                log=lambda *_args, **_kwargs: None,
            )

            with self.assertRaisesRegex(RuntimeError, "LLM failed"):
                await answer_control_page_text_from_runtime(
                    None,
                    "잊지 말고 이어가 줘",
                    deps=deps,
                )

            restored_store = SessionStateStore.create_empty()
            restored = SessionContinuityCheckpoint(
                store=restored_store,
                checkpoint_path=root / "active.json",
                status_path=root / "restored-status.json",
                system_prompt="system",
            ).restore()

            self.assertEqual(restored["state"], "restored")
            self.assertEqual(
                restored_store.histories["control:0"],
                [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "잊지 말고 이어가 줘"},
                ],
            )
            self.assertEqual(
                restored_store.current_turn_id("control:0"),
                "turn-accepted",
            )

            finish_assistant_text_turn_from_runtime(
                "control:0",
                "잊지 말고 이어가 줘",
                "다시 이어갈게",
                awaiting_user_reply=False,
                topic_id=store.topic_ids["control:0"],
                complete_turn_id="turn-accepted",
                deps=session_deps,
            )
            await checkpoint.commit_completed_turn_async(
                "control:0",
                "turn-accepted",
            )
            completed_store = SessionStateStore.create_empty()
            completed = SessionContinuityCheckpoint(
                store=completed_store,
                checkpoint_path=root / "active.json",
                status_path=root / "completed-status.json",
                system_prompt="system",
            ).restore()

            self.assertEqual(completed["state"], "restored")
            self.assertEqual(
                [
                    (row["role"], row["content"])
                    for row in completed_store.histories["control:0"]
                ],
                [
                    ("system", "system"),
                    ("user", "잊지 말고 이어가 줘"),
                    ("assistant", "다시 이어갈게"),
                ],
            )

    async def test_cancelled_precommit_holds_state_lock_until_disk_commit_drains(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SessionStateStore.create_empty()
            checkpoint = SessionContinuityCheckpoint(
                store=store,
                checkpoint_path=root / "active.json",
                status_path=root / "status.json",
                system_prompt="system",
            )
            session_deps = SessionTurnRuntimeDeps(
                session_state_store=store,
                system_prompt="system",
                memory_index_dir=root / "memory_index",
                active_conversation_awaiting_reply_sec=120.0,
                active_conversation_text_question_sec=120.0,
                active_conversation_text_sec=90.0,
                max_history_items=12,
                session_topic_ids=store.topic_ids,
                build_topic_id_fn=build_topic_id,
                new_turn_id_fn=lambda: "turn-cancelled",
            )
            commit_started = threading.Event()
            release_commit = threading.Event()
            state_lock = asyncio.Lock()

            def pause_physical_commit(_generation: int) -> None:
                commit_started.set()
                if not release_commit.wait(timeout=2.0):
                    raise TimeoutError("test_commit_release_timed_out")

            async def slow_commit(
                session_key: str,
                turn_id: str,
            ) -> dict[str, Any]:
                return await checkpoint.commit_completed_turn_async(
                    session_key,
                    turn_id,
                    before_commit=pause_physical_commit,
                )

            base_deps = self.build_deps()
            deps = ControlPageTextRuntimeDeps(
                **{
                    **base_deps.__dict__,
                    "get_session_lock": lambda _key: state_lock,
                    "begin_user_text_turn": lambda *args, **kwargs: (
                        begin_user_text_turn_from_runtime(
                            *args,
                            **kwargs,
                            deps=session_deps,
                        )
                    ),
                    "session_state_snapshot": store.snapshot,
                    "finish_assistant_text_turn": (
                        lambda *args, **kwargs: (
                            finish_assistant_text_turn_from_runtime(
                                *args,
                                **kwargs,
                                deps=session_deps,
                            )
                        )
                    ),
                    "commit_session_continuity": slow_commit,
                }
            )
            answer_task = asyncio.create_task(
                answer_control_page_text_from_runtime(
                    None,
                    "취소돼도 기억해 줘",
                    deps=deps,
                )
            )
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
                    restored_store.histories["control:0"][-1],
                    {
                        "role": "user",
                        "content": "취소돼도 기억해 줘",
                    },
                )
            finally:
                release_commit.set()
                answer_task.cancel()
                pending = [answer_task]
                if successor_task is not None:
                    pending.append(successor_task)
                await asyncio.gather(*pending, return_exceptions=True)

    async def test_bound_receipt_version_mismatch_reaches_no_sink(
        self,
    ) -> None:
        async def ask_with_mismatched_boundary(
            _text: str,
            **kwargs,
        ) -> str:
            capture_memory_exposure_position(memory_exposure(7))
            kwargs["metrics"]["meta"]["context_pipeline"] = {
                "memory_receipt": {
                    "schema": CONVERSATION_MEMORY_RECEIPT_REF_SCHEMA,
                    "state": "bound",
                    "memoryVersion": 8,
                    "suppliedNoteIds": [NOTE_ID],
                    "suppliedNoteCount": 1,
                    "contentFree": True,
                }
            }
            return "private mismatched reply"

        reset_memory_exposure_position()
        deps = self.build_deps()
        deps = ControlPageTextRuntimeDeps(
            **{
                **deps.__dict__,
                "ask_llm_streaming": ask_with_mismatched_boundary,
            }
        )

        with self.assertRaises(MemoryDeletionJournalIntegrityError):
            await answer_control_page_text_from_runtime(
                None,
                "질문",
                deps=deps,
            )

        self.assertEqual(self.finished, [])
        self.assertEqual(self.commits, ["commit"])
        self.assertEqual(self.scheduled, [])
        self.assertEqual(len(self.detached), 1)
        self.assertEqual(len(self.cleared), 1)

    def test_main_delegates_control_page_answer_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def answer_text(")
        end = source.index("async def handle_input(", start)
        function_source = source[start:end]

        self.assertIn("answer_control_page_text_from_runtime(", function_source)
        self.assertNotIn("ask_llm_streaming(", function_source)
        self.assertNotIn("finish_assistant_text_turn(", function_source)


if __name__ == "__main__":
    unittest.main()
