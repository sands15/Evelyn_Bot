from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api
from evelyn_core.fast_action_runtime import (
    FastActionCancelledError,
    FastActionExecutionError,
)
from evelyn_core.main_llm_runtime import (
    TASK_LOOP_INVALID_RESULT,
    TASK_LOOP_VERIFIED_MUTATION_OUTCOME,
)
from evelyn_core.task_loop_runtime import TaskLoopResult


class FastTaskLoopIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.handlers = list(fast_control_api.BACKGROUND_ACTION_HANDLERS)
        fast_control_api.ACTION_COORDINATOR.clear()

    def tearDown(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS[:] = self.handlers
        fast_control_api.BACKGROUND_ACTION_TASKS_BY_ID.clear()
        fast_control_api.BACKGROUND_ACTION_CANCEL_INTENTS.clear()
        fast_control_api.TASK_APPROVAL_CLAIMS.clear()
        fast_control_api.ACTION_COORDINATOR.clear()

    def test_builtin_registration_adds_task_handler_even_if_minecraft_exists(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS[:] = [
            {
                "kind": "minecraft_runtime",
                "matcher": lambda _text: False,
                "runner": lambda _text, _source: None,
                "startReply": "starting",
            }
        ]

        fast_control_api.register_builtin_background_action_handlers()
        fast_control_api.register_builtin_background_action_handlers()

        kinds = [handler["kind"] for handler in fast_control_api.BACKGROUND_ACTION_HANDLERS]
        self.assertEqual(kinds.count("minecraft_runtime"), 1)
        self.assertEqual(kinds.count("iterative_task"), 1)

    def test_control_page_catalog_exposes_start_and_exact_cancel(self) -> None:
        commands = {
            item["command"]
            for item in fast_control_api.build_default_commands()
        }
        self.assertIn("/작업 <목표>", commands)
        self.assertIn("/작업취소 <task-id>", commands)

    async def test_completed_read_only_task_uses_typed_receipt_without_main_finalizer(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="completed",
            code="task_completed",
            summary="verified",
            step_count=1,
            model_call_count=2,
            observations=(
                {
                    "step": 1,
                    "tool": "runtime_status",
                    "verified": True,
                    "outcome": "success",
                    "code": "runtime_status_collected",
                    "summary": "status",
                    "evidence": json.dumps(
                        {
                            "schema": "runtime_health.public.v1",
                            "ok": False,
                            "coreState": "down",
                            "overallState": "down",
                        },
                        separators=(",", ":"),
                    ),
                },
            ),
        )
        loop = AsyncMock(return_value=loop_result)
        finalizer = AsyncMock(return_value="최종 결과")

        with (
            patch.object(fast_control_api, "run_default_task_loop", loop),
            patch.object(fast_control_api, "synthesize_tool_evidence_reply", finalizer),
        ):
            reply = await handler["runner"](
                "/작업 런타임 상태를 확인해줘",
                "control_page",
            )

        loop.assert_awaited_once_with(
            "런타임 상태를 확인해줘",
            source="control_page",
        )
        finalizer.assert_not_awaited()
        self.assertIn("overallState=down", reply)
        self.assertIn("coreState=down", reply)

    async def test_completed_workspace_mutation_skips_overclaiming_main_finalizer(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        content = "X"
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        loop_result = TaskLoopResult(
            task_id="task-fast-mutation",
            status="completed",
            code="task_completed",
            summary="모든 버그를 고쳤고 전체 테스트가 통과했어.",
            step_count=2,
            model_call_count=3,
            observations=(
                {
                    "step": 1,
                    "tool": "workspace_edit",
                    "verified": True,
                    "outcome": "success",
                    "code": "workspace_edit_completed",
                    "summary": "applied",
                    "evidence": json.dumps(
                        {"path": "docs/file.md", "sha256": sha256},
                        separators=(",", ":"),
                    ),
                },
                {
                    "step": 2,
                    "tool": "workspace_read",
                    "verified": True,
                    "outcome": "success",
                    "code": "workspace_read_completed",
                    "summary": "read",
                    "evidence": json.dumps(
                        {
                            "path": "docs/file.md",
                            "sha256": sha256,
                            "bytes": 1,
                            "offset": 0,
                            "length": 1,
                            "nextOffset": 1,
                            "eof": True,
                            "content": content,
                            "truncated": False,
                        },
                        separators=(",", ":"),
                    ),
                },
            ),
        )
        finalizer = AsyncMock(
            return_value="모든 버그를 고쳤고 전체 테스트가 통과했어."
        )

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                finalizer,
            ),
        ):
            reply = await handler["runner"](
                "/작업 파일을 수정해줘",
                "control_page",
            )

        self.assertEqual(reply, TASK_LOOP_VERIFIED_MUTATION_OUTCOME)
        finalizer.assert_not_awaited()
        self.assertNotIn("모든 버그", reply)
        self.assertNotIn("전체 테스트가 통과", reply)

    async def test_completed_mutation_finalizer_failure_never_exposes_worker_claim(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="completed",
            code="task_completed",
            summary="모든 버그와 전체 테스트를 완전히 해결했어.",
            step_count=5,
            model_call_count=6,
            observations=(
                {
                    "tool": "workspace_edit",
                    "code": "workspace_edit_completed",
                    "verified": True,
                    "outcome": "success",
                },
                {
                    "tool": "workspace_test",
                    "code": "workspace_test_passed",
                    "verified": True,
                    "outcome": "success",
                },
                {
                    "tool": "workspace_read",
                    "code": "workspace_read_completed",
                    "verified": True,
                    "outcome": "success",
                },
            ),
        )

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                AsyncMock(side_effect=RuntimeError("main unavailable")),
            ),
        ):
            with self.assertRaises(FastActionExecutionError) as raised:
                await handler["runner"](
                    "/작업 테스트를 고쳐줘",
                    "control_page",
                )

        self.assertEqual(str(raised.exception), "task_result_invalid")
        self.assertEqual(raised.exception.reply, TASK_LOOP_INVALID_RESULT)
        self.assertNotIn("모든 버그", raised.exception.reply)
        self.assertNotIn("전체 테스트", raised.exception.reply)

    async def test_completed_mutation_empty_finalizer_never_exposes_worker_claim(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="completed",
            code="task_completed",
            summary="모든 버그와 전체 테스트를 완전히 해결했어.",
            step_count=5,
            model_call_count=6,
            observations=(
                {
                    "tool": "workspace_edit",
                    "code": "workspace_edit_completed",
                    "verified": True,
                    "outcome": "success",
                },
                {
                    "tool": "workspace_test",
                    "code": "workspace_test_passed",
                    "verified": True,
                    "outcome": "success",
                },
                {
                    "tool": "workspace_read",
                    "code": "workspace_read_completed",
                    "verified": True,
                    "outcome": "success",
                },
            ),
        )

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                AsyncMock(return_value="   "),
            ),
        ):
            with self.assertRaises(FastActionExecutionError) as raised:
                await handler["runner"](
                    "/작업 테스트를 고쳐줘",
                    "control_page",
                )

        self.assertEqual(str(raised.exception), "task_result_invalid")
        self.assertEqual(raised.exception.reply, TASK_LOOP_INVALID_RESULT)
        self.assertNotIn("모든 버그", raised.exception.reply)
        self.assertNotIn("전체 테스트", raised.exception.reply)

    async def test_failed_candidate_history_does_not_trigger_mutation_fallback(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="completed",
            code="task_completed",
            summary="raw worker claim",
            step_count=5,
            model_call_count=5,
            observations=(
                {
                    "tool": "workspace_edit",
                    "code": "workspace_edit_staged",
                    "verified": True,
                    "outcome": "success",
                },
                {
                    "tool": "workspace_test",
                    "code": "workspace_test_failed",
                    "verified": True,
                    "outcome": "failed",
                },
                {
                    "tool": "workspace_read",
                    "code": "workspace_read_completed",
                    "verified": True,
                    "outcome": "success",
                },
            ),
        )

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                AsyncMock(side_effect=RuntimeError("main unavailable")),
            ),
        ):
            with self.assertRaises(FastActionExecutionError) as raised:
                await handler["runner"](
                    "/작업 런타임 상태를 확인해줘",
                    "control_page",
                )

        self.assertEqual(str(raised.exception), "task_result_invalid")
        self.assertEqual(raised.exception.reply, TASK_LOOP_INVALID_RESULT)
        self.assertNotIn("승인된 변경 적용", raised.exception.reply)
        self.assertNotIn("raw worker claim", raised.exception.reply)

    async def test_noncompleted_task_raises_typed_failure_without_main(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="uncertain",
            code="workspace_behavior_outcome_unverified",
            summary="승인된 diff와 SHA는 확인했지만 행동적 목표 해결은 증명되지 않았어.",
            step_count=1,
            model_call_count=1,
        )
        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                AsyncMock(),
            ) as finalizer,
        ):
            with self.assertRaises(FastActionExecutionError) as raised:
                await handler["runner"]("/작업 파일을 고쳐줘", "control_page")

        finalizer.assert_not_awaited()
        self.assertEqual(
            str(raised.exception),
            "workspace_behavior_outcome_unverified",
        )
        self.assertEqual(
            raised.exception.reply,
            "승인된 diff와 SHA는 확인했지만 행동적 목표 해결은 증명되지 않았어.",
        )

    async def test_user_input_task_never_exposes_worker_completion_claim(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        raw_worker_claim = "작업을 모두 완료했어."
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="awaiting_approval",
            code="task_user_input_required",
            summary=raw_worker_claim,
            step_count=0,
            model_call_count=1,
        )

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                AsyncMock(),
            ) as finalizer,
        ):
            with self.assertRaises(FastActionExecutionError) as raised:
                await handler["runner"]("/작업 확인해줘", "control_page")

        self.assertEqual(str(raised.exception), "task_user_input_required")
        self.assertEqual(
            raised.exception.reply,
            "작업을 계속하려면 추가 입력이 필요해.",
        )
        self.assertNotIn(raw_worker_claim, raised.exception.reply)
        finalizer.assert_not_awaited()

    async def test_cancelled_task_raises_typed_cancel_without_main(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        handler = next(
            item
            for item in fast_control_api.BACKGROUND_ACTION_HANDLERS
            if item["kind"] == "iterative_task"
        )
        loop_result = TaskLoopResult(
            task_id="task-fast",
            status="cancelled",
            code="task_approval_cancelled",
            summary="사용자가 파일 변경 승인을 취소했어.",
            step_count=0,
            model_call_count=1,
        )
        finalizer = AsyncMock()

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                AsyncMock(return_value=loop_result),
            ),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                finalizer,
            ),
        ):
            with self.assertRaises(FastActionCancelledError) as raised:
                await handler["runner"]("/작업 파일을 고쳐줘", "control_page")

        finalizer.assert_not_awaited()
        self.assertEqual(str(raised.exception), "task_approval_cancelled")
        self.assertEqual(
            raised.exception.reply,
            "사용자가 파일 변경 승인을 취소했어.",
        )

    async def test_noncompleted_loop_is_failed_not_completed_by_launcher(self) -> None:
        task = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일을 고쳐줘",
            start_reply="시작",
        )

        async def runner(_text: str, _source: str) -> str:
            raise FastActionExecutionError(
                "task_outcome_unverified",
                reply="자동 재시도하지 않았어.",
            )

        with (
            patch.object(fast_control_api, "current_memory_exposure_position", return_value=None),
            patch.object(fast_control_api, "append_chat_message"),
            patch.object(fast_control_api, "queue_local_bridge_speech"),
            patch.object(
                fast_control_api.FAST_ACTION_RECOVERY_JOURNAL,
                "mark_interrupted",
            ),
        ):
            background = fast_control_api.launch_background_action(task, runner)
            await background

        recorded = fast_control_api.ACTION_COORDINATOR.get(task.task_id)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.status, "failed")
        self.assertEqual(recorded.error, "task_outcome_unverified")

    async def test_cancelled_loop_keeps_cancelled_fast_action_terminal(self) -> None:
        task = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일을 고쳐줘",
            start_reply="시작",
        )

        async def runner(_text: str, _source: str) -> str:
            raise FastActionCancelledError(
                "task_approval_cancelled",
                reply="사용자가 파일 변경 승인을 취소했어.",
            )

        with (
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(fast_control_api, "append_chat_message") as append,
            patch.object(
                fast_control_api,
                "commit_fast_control_action_followup",
            ) as commit,
            patch.object(fast_control_api, "queue_local_bridge_speech"),
            patch.object(
                fast_control_api.FAST_ACTION_RECOVERY_JOURNAL,
                "mark_interrupted",
            ) as interrupted,
        ):
            background = fast_control_api.launch_background_action(task, runner)
            await background

        recorded = fast_control_api.ACTION_COORDINATOR.get(task.task_id)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.status, "cancelled")
        self.assertEqual(recorded.error, "")
        self.assertEqual(
            recorded.final_reply,
            "사용자가 파일 변경 승인을 취소했어.",
        )
        self.assertEqual(
            fast_control_api.ACTION_COORDINATOR.internal_snapshot()["events"][-1]["type"],
            "cancelled",
        )
        append.assert_called_once()
        self.assertEqual(append.call_args.kwargs["task_status"], "cancelled")
        commit.assert_called_once()
        self.assertEqual(commit.call_args.args, (
            task.task_id,
            "사용자가 파일 변경 승인을 취소했어.",
        ))
        self.assertEqual(
            commit.call_args.kwargs["memory_receipt"]["state"],
            "not_used",
        )
        interrupted.assert_not_called()

    async def test_launched_task_binds_fast_id_and_stays_running_while_loop_waits(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        prepared = fast_control_api.prepare_registered_background_action(
            "/작업 런타임 상태를 확인해줘",
            source="control_page",
        )
        self.assertIsNotNone(prepared)
        task, runner = prepared
        entered = asyncio.Event()
        release = asyncio.Event()
        captured = {}

        async def loop(goal, **kwargs):
            captured.update(kwargs)
            entered.set()
            await release.wait()
            return TaskLoopResult(
                task_id=kwargs["task_id"],
                status="completed",
                code="task_completed",
                summary="verified",
                step_count=1,
                model_call_count=2,
                observations=(
                    {
                        "step": 1,
                        "tool": "runtime_status",
                        "verified": True,
                        "outcome": "success",
                        "code": "runtime_status_collected",
                        "summary": "status",
                        "evidence": json.dumps(
                            {
                                "schema": "runtime_health.public.v1",
                                "ok": True,
                                "coreState": "up",
                                "overallState": "up",
                            },
                            separators=(",", ":"),
                        ),
                    },
                ),
            )

        with (
            patch.object(fast_control_api, "run_default_task_loop", new=loop),
            patch.object(
                fast_control_api,
                "synthesize_tool_evidence_reply",
                new=AsyncMock(return_value="완료"),
            ),
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(fast_control_api, "append_chat_message"),
            patch.object(fast_control_api, "commit_fast_control_action_followup"),
            patch.object(fast_control_api, "queue_local_bridge_speech"),
        ):
            background = fast_control_api.launch_background_action(task, runner)
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            self.assertEqual(task.status, "running")
            self.assertFalse(background.done())
            self.assertEqual(captured["task_id"], task.task_id)
            self.assertIs(
                captured["request_approval"].__self__,
                fast_control_api.TASK_APPROVAL_MANAGER,
            )
            release.set()
            await background

        self.assertEqual(task.status, "completed")

    async def test_launched_invalid_completed_receipt_finishes_failed(self) -> None:
        fast_control_api.BACKGROUND_ACTION_HANDLERS.clear()
        fast_control_api.register_builtin_background_action_handlers()
        prepared = fast_control_api.prepare_registered_background_action(
            "/작업 런타임 상태를 확인해줘",
            source="control_page",
        )
        self.assertIsNotNone(prepared)
        task, runner = prepared
        invalid = TaskLoopResult(
            task_id=task.task_id,
            status="completed",
            code="task_completed",
            summary="forged completed result",
            step_count=1,
            model_call_count=2,
            observations=(),
        )

        with (
            patch.object(
                fast_control_api,
                "run_default_task_loop",
                new=AsyncMock(return_value=invalid),
            ),
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(fast_control_api, "append_chat_message"),
            patch.object(fast_control_api, "commit_fast_control_action_followup"),
            patch.object(fast_control_api, "queue_local_bridge_speech"),
            patch.object(
                fast_control_api.FAST_ACTION_RECOVERY_JOURNAL,
                "mark_interrupted",
            ),
        ):
            await fast_control_api.launch_background_action(task, runner)

        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error, "task_result_invalid")
        self.assertEqual(task.final_reply, TASK_LOOP_INVALID_RESULT)

    async def test_task_command_bypasses_generic_unknown_slash_reply(self) -> None:
        self.assertIsNone(
            await fast_control_api.resolve_pre_llm_reply(
                "/작업 테스트를 고쳐줘",
                source="control_page",
            )
        )
        self.assertTrue(
            fast_control_api.should_skip_fast_tool_planner(
                "작업: 테스트를 고쳐줘"
            )
        )

    async def test_task_cancel_targets_exact_background_task(self) -> None:
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        fast_control_api.BACKGROUND_ACTION_TASKS_BY_ID["fast-action-77"] = task

        reply = await fast_control_api.resolve_pre_llm_reply(
            "/작업취소 fast-action-77",
            source="control_page",
        )

        self.assertIn("중단을 요청", reply)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(
            fast_control_api.cancel_background_action("fast-action-78")
        )

    async def test_direct_task_cancel_records_cancelled_terminal(self) -> None:
        task = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일을 고쳐줘",
            start_reply="시작",
        )
        entered = asyncio.Event()

        async def runner(_text: str, _source: str) -> str:
            entered.set()
            await asyncio.Event().wait()
            return "unreachable"

        with (
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(fast_control_api, "append_chat_message") as append,
            patch.object(
                fast_control_api,
                "commit_fast_control_action_followup",
            ) as commit,
            patch.object(fast_control_api, "queue_local_bridge_speech"),
            patch.object(
                fast_control_api.FAST_ACTION_RECOVERY_JOURNAL,
                "mark_interrupted",
            ) as interrupted,
        ):
            background = fast_control_api.launch_background_action(task, runner)
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            self.assertTrue(
                fast_control_api.cancel_background_action(task.task_id)
            )
            with self.assertRaises(asyncio.CancelledError):
                await background

        recorded = fast_control_api.ACTION_COORDINATOR.get(task.task_id)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.status, "cancelled")
        self.assertEqual(recorded.error, "")
        self.assertEqual(recorded.final_reply, "작업 취소를 확인했어.")
        append.assert_called_once()
        self.assertEqual(append.call_args.kwargs["task_status"], "cancelled")
        commit.assert_called_once()
        interrupted.assert_not_called()

    async def test_cancel_intent_cannot_become_completed_if_runner_swallows_cancel(self) -> None:
        task = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일을 고쳐줘",
            start_reply="시작",
        )
        entered = asyncio.Event()

        async def runner(_text: str, _source: str) -> str:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "취소를 삼키고 완료"

        with (
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(fast_control_api, "append_chat_message"),
            patch.object(
                fast_control_api,
                "commit_fast_control_action_followup",
            ),
            patch.object(fast_control_api, "queue_local_bridge_speech"),
        ):
            background = fast_control_api.launch_background_action(task, runner)
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            self.assertTrue(
                fast_control_api.cancel_background_action(task.task_id)
            )
            await background

        recorded = fast_control_api.ACTION_COORDINATOR.get(task.task_id)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.status, "failed")
        self.assertEqual(
            recorded.error,
            "background_action_cancel_outcome_unverified",
        )
        self.assertIn("완료 여부를 확인할 수 없어", recorded.final_reply)

    async def test_immediate_direct_cancel_cannot_leave_task_running(self) -> None:
        task = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일을 고쳐줘",
            start_reply="시작",
        )
        runner = AsyncMock(return_value="unreachable")

        with (
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(fast_control_api, "append_chat_message"),
            patch.object(
                fast_control_api,
                "commit_fast_control_action_followup",
            ),
            patch.object(fast_control_api, "queue_local_bridge_speech"),
        ):
            background = fast_control_api.launch_background_action(task, runner)
            self.assertTrue(
                fast_control_api.cancel_background_action(task.task_id)
            )
            with self.assertRaises(asyncio.CancelledError):
                await background
            await asyncio.sleep(0)

        recorded = fast_control_api.ACTION_COORDINATOR.get(task.task_id)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.status, "cancelled")
        self.assertEqual(recorded.error, "")
        runner.assert_not_awaited()

    async def test_immediate_external_cancel_cannot_leave_task_running(self) -> None:
        task = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일을 고쳐줘",
            start_reply="시작",
        )
        runner = AsyncMock(return_value="unreachable")

        with (
            patch.object(
                fast_control_api,
                "current_memory_exposure_position",
                return_value=None,
            ),
            patch.object(
                fast_control_api.FAST_ACTION_RECOVERY_JOURNAL,
                "mark_interrupted",
            ) as interrupted,
        ):
            background = fast_control_api.launch_background_action(task, runner)
            background.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await background
            await asyncio.sleep(0)

        recorded = fast_control_api.ACTION_COORDINATOR.get(task.task_id)
        self.assertIsNotNone(recorded)
        self.assertEqual(recorded.status, "failed")
        self.assertEqual(recorded.error, "background_action_cancelled")
        interrupted.assert_called_once_with(task.task_id)
        runner.assert_not_awaited()

    async def test_task_cancel_does_not_interrupt_approval_transition(self) -> None:
        for approval_state, expected_text in (
            ("claimed", "강제 중단하지 않았어"),
            ("resuming", "강제 중단하지 않았어"),
            ("cancelling", "이미 취소 결과를 확인 중"),
        ):
            with self.subTest(approval_state=approval_state):
                blocker = asyncio.Event()
                background = asyncio.create_task(blocker.wait())
                fast_control_api.BACKGROUND_ACTION_TASKS_BY_ID[
                    "fast-action-77"
                ] = background
                manager = MagicMock()
                manager.public_snapshot.return_value = {
                    "schema": "task_approval.public.v1",
                    "state": approval_state,
                    "taskId": "fast-action-77",
                    "approvalId": "approval-0123456789abcdef",
                    "step": 2,
                    "maxSteps": 5,
                    "tool": "workspace_edit",
                    "effect": "UTF-8 파일 1개 create/replace",
                    "expiresAt": 2_000.0,
                }

                with patch.object(
                    fast_control_api,
                    "TASK_APPROVAL_MANAGER",
                    manager,
                ):
                    reply = await fast_control_api.resolve_pre_llm_reply(
                        "/작업취소 fast-action-77",
                        source="control_page",
                    )

                self.assertIn(expected_text, reply)
                self.assertFalse(background.done())
                manager.cancel.assert_not_called()
                blocker.set()
                await background
                fast_control_api.BACKGROUND_ACTION_TASKS_BY_ID.clear()

    async def test_task_cancel_respects_hidden_inflight_approval_barrier(self) -> None:
        blocker = asyncio.Event()
        background = asyncio.create_task(blocker.wait())
        fast_control_api.BACKGROUND_ACTION_TASKS_BY_ID[
            "fast-action-77"
        ] = background
        manager = MagicMock()
        manager.public_snapshot.return_value = {}
        manager.task_cancel_barrier.return_value = "claimed"

        with patch.object(
            fast_control_api,
            "TASK_APPROVAL_MANAGER",
            manager,
        ):
            reply = await fast_control_api.resolve_pre_llm_reply(
                "/작업취소 fast-action-77",
                source="control_page",
            )

        self.assertIn("강제 중단하지 않았어", reply)
        self.assertFalse(background.done())
        manager.task_cancel_barrier.assert_called_once_with("fast-action-77")
        blocker.set()
        await background

    async def test_resuming_effect_barrier_blocks_cancel_until_terminal_cleanup(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        manager = MagicMock()
        manager.public_snapshot.return_value = {}
        manager.task_cancel_barrier.return_value = "resuming"
        manager.release_task_cancel_barrier.return_value = True
        task_record = fast_control_api.ACTION_COORDINATOR.start(
            kind="iterative_task",
            source="control_page",
            user_text="/작업 파일 수정",
            start_reply="작업을 시작했어.",
        )

        async def runner(_text: str, _source: str) -> str:
            entered.set()
            await release.wait()
            return "검증된 작업을 완료했어."

        with (
            patch.object(fast_control_api, "TASK_APPROVAL_MANAGER", manager),
            patch.object(fast_control_api, "append_chat_message"),
            patch.object(fast_control_api, "commit_fast_control_action_followup"),
            patch.object(fast_control_api, "queue_local_bridge_speech"),
        ):
            background = fast_control_api.launch_background_action(
                task_record,
                runner,
            )
            await entered.wait()

            cancel_state = fast_control_api._request_background_action_cancel(
                task_record.task_id
            )

            self.assertEqual(cancel_state, "approval_in_flight")
            self.assertFalse(background.done())
            self.assertFalse(background.cancelled())
            manager.release_task_cancel_barrier.assert_not_called()

            release.set()
            await background
            await asyncio.sleep(0)

        manager.release_task_cancel_barrier.assert_called_once_with(
            task_record.task_id
        )
        self.assertEqual(task_record.status, "completed")

    async def test_task_cancel_wakes_pending_approval_without_cancelling_coroutine(self) -> None:
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        fast_control_api.BACKGROUND_ACTION_TASKS_BY_ID["fast-action-77"] = task
        claim = MagicMock(claim_id="claim-0123456789abcdef")
        manager = MagicMock()
        manager.public_snapshot.return_value = {
            "schema": "task_approval.public.v1",
            "state": "awaiting_approval",
            "taskId": "fast-action-77",
            "approvalId": "approval-0123456789abcdef",
            "step": 2,
            "maxSteps": 5,
            "tool": "workspace_edit",
            "effect": "UTF-8 파일 1개 create/replace",
            "expiresAt": 2_000.0,
        }
        manager.cancel.return_value = claim
        fast_control_api.TASK_APPROVAL_CLAIMS[claim.claim_id] = claim

        with patch.object(fast_control_api, "TASK_APPROVAL_MANAGER", manager):
            self.assertTrue(
                fast_control_api.cancel_background_action("fast-action-77")
            )

        manager.cancel.assert_called_once_with(
            "fast-action-77",
            "approval-0123456789abcdef",
        )
        self.assertFalse(task.done())
        self.assertNotIn(claim.claim_id, fast_control_api.TASK_APPROVAL_CLAIMS)
        blocker.set()
        await task


if __name__ == "__main__":
    unittest.main()
