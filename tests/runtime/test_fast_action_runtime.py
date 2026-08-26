from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.fast_action_runtime import (  # noqa: E402
    FastActionCoordinator,
    FastActionExecutionError,
    SafeIncrementalSpeechFilter,
    UNBACKED_PROGRESS_FALLBACK,
    compact_local_bridge_context,
    detect_local_mic_command,
    detect_local_runtime_command,
    detect_minecraft_control_command,
    detect_minecraft_runtime_command,
    enforce_action_reply_contract,
    has_unbacked_progress_claim,
    is_local_mic_status_request,
    render_local_mic_status,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)


class FastActionRuntimeTests(unittest.TestCase):
    def test_incremental_speech_filter_releases_safe_words(self) -> None:
        stream_filter = SafeIncrementalSpeechFilter()
        output = []
        for fragment in ("마이크 ", "입력은 ", "꺼져 ", "있어."):
            output.extend(stream_filter.push(fragment))
        output.extend(stream_filter.finish())
        self.assertEqual("".join(output), "마이크 입력은 꺼져 있어.")

    def test_incremental_speech_filter_drops_split_future_claim(self) -> None:
        stream_filter = SafeIncrementalSpeechFilter()
        output = []
        for fragment in ("내가 ", "확인해 ", "볼게. ", "마이크 입력은 꺼져 있어."):
            output.extend(stream_filter.push(fragment))
        output.extend(stream_filter.finish())
        spoken = "".join(output)
        self.assertNotIn("확인해", spoken)
        self.assertNotIn("볼게", spoken)
        self.assertIn("마이크 입력은 꺼져 있어.", spoken)

    def test_incremental_filter_holds_no_space_late_unsafe_suffix(self) -> None:
        stream_filter = SafeIncrementalSpeechFilter()

        self.assertEqual(stream_filter.push("안전한 앞부분이야."), [])
        self.assertEqual(stream_filter.push("확인해볼게."), [])
        spoken = "".join(stream_filter.finish())

        self.assertEqual(
            spoken,
            enforce_action_reply_contract(
                "안전한 앞부분이야.확인해볼게."
            ),
        )
        self.assertEqual(spoken, UNBACKED_PROGRESS_FALLBACK)

    def test_detects_natural_local_mic_status_question(self) -> None:
        self.assertTrue(is_local_mic_status_request("마이크 입력이 되고 있어?"))
        self.assertTrue(is_local_mic_status_request("내 목소리 들어오고 있어?"))
        self.assertFalse(is_local_mic_status_request("내 목소리로 음성을 복제해"))

    def test_detects_mic_control_commands_without_confusing_status_questions(self) -> None:
        self.assertEqual(detect_local_mic_command("/mic on"), "on")
        self.assertEqual(detect_local_mic_command("/mic off"), "off")
        self.assertEqual(detect_local_mic_command("/mic status"), "status")
        self.assertEqual(detect_local_mic_command("마이크 입력 켜줘"), "on")
        self.assertEqual(detect_local_mic_command("마이크 이제 꺼줘"), "off")
        self.assertEqual(detect_local_mic_command("마이크 꺼져 있어?"), "status")

    def test_detects_only_explicit_minecraft_execution_commands(self) -> None:
        self.assertEqual(detect_minecraft_runtime_command("마인크래프트 시작해"), "start")
        self.assertEqual(detect_minecraft_runtime_command("마크 서버에 접속해줘"), "start")
        self.assertEqual(detect_minecraft_runtime_command("마인크래프트에서 나무 캐줘"), "goal")
        self.assertEqual(detect_minecraft_runtime_command("/minecraft connect"), "start")
        self.assertEqual(detect_minecraft_runtime_command("/minecraft goal diamond"), "goal")
        self.assertIsNone(detect_minecraft_runtime_command("/minecraft goal"))
        self.assertIsNone(detect_minecraft_runtime_command("마인크래프트 상태 알려줘"))
        self.assertIsNone(detect_minecraft_runtime_command("마인크래프트 종료해"))
        self.assertIsNone(detect_minecraft_runtime_command("마인크래프트 좋아해?"))
        self.assertIsNone(detect_minecraft_runtime_command("나무 캐는 법 알려줘"))
        self.assertIsNone(detect_minecraft_runtime_command("마인크래프트에서 나무 캐는 법 알려줘"))
        self.assertIsNone(detect_minecraft_runtime_command("마인크래프트 어떻게 시작해?"))
        self.assertEqual(detect_minecraft_runtime_command("마인크래프트 어떻게든 시작해줘"), "start")

    def test_detects_evelyn_wide_restart_and_shutdown_commands(self) -> None:
        for text in (
            "/shutdown",
            "/quit",
            "/exit",
            "shutdown",
            "셧다운해",
            "종료해",
            "프로젝트 종료해",
            "이블린 꺼줘",
        ):
            self.assertEqual(detect_local_runtime_command(text), "shutdown", text)
        for text in (
            "/restart",
            "/재시작",
            "restart",
            "재시작해",
            "이블린 재시작해",
            "프로젝트 재시작해줘",
        ):
            self.assertEqual(detect_local_runtime_command(text), "restart", text)

    def test_runtime_command_detector_rejects_questions_and_scoped_shutdowns(self) -> None:
        self.assertIsNone(detect_local_runtime_command("종료하면 어떻게 돼?"))
        self.assertIsNone(detect_local_runtime_command("재시작해야 해?"))
        self.assertIsNone(detect_local_runtime_command("마인크래프트 종료해"))
        self.assertIsNone(detect_local_runtime_command("마이크 꺼줘"))

    def test_detects_non_starting_minecraft_control_commands(self) -> None:
        self.assertEqual(detect_minecraft_control_command("/minecraft status"), "status")
        self.assertEqual(detect_minecraft_control_command("/inventory"), "inventory")
        self.assertEqual(detect_minecraft_control_command("/voyager stats"), "stats")
        self.assertEqual(detect_minecraft_control_command("/minecraft disconnect"), "disconnect")
        self.assertEqual(detect_minecraft_control_command("/autonomy status"), "autonomy_status")
        self.assertEqual(detect_minecraft_control_command("마크 인벤토리 보여줘"), "inventory")
        self.assertEqual(detect_minecraft_control_command("마인크래프트 종료해"), "disconnect")
        self.assertEqual(detect_minecraft_control_command("마인크래프트 꺼져 있어?"), "status")
        self.assertIsNone(detect_minecraft_control_command("마인크래프트 시작해"))

    def test_runtime_command_aliases_cover_korean_and_slash_forms(self) -> None:
        for command in (
            "/shutdown",
            "/quit",
            "/exit",
            "/종료",
            "/셧다운",
            "셧다운해",
            "종료해",
            "이블린 꺼줘",
            "프로젝트 꺼줘",
            "전체 꺼줘",
        ):
            with self.subTest(command=command):
                self.assertEqual(detect_local_runtime_command(command), "shutdown")

        for command in (
            "/restart",
            "/재시작",
            "재시작해",
            "이블린 다시 켜줘",
            "프로젝트 다시 켜줘",
        ):
            with self.subTest(command=command):
                self.assertEqual(detect_local_runtime_command(command), "restart")

    def test_runtime_questions_never_execute_a_lifecycle_command(self) -> None:
        for text in (
            "종료하면 어떻게 돼?",
            "종료해도 돼?",
            "재시작해야 해?",
            "셧다운 명령어가 뭐야?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(detect_local_runtime_command(text))

    def test_fast_action_execution_error_keeps_user_facing_failure_reply(self) -> None:
        error = FastActionExecutionError(
            "minecraft_launcher_failed",
            reply="마인크래프트 준비에 실패했어.",
        )

        self.assertEqual(str(error), "minecraft_launcher_failed")
        self.assertEqual(error.reply, "마인크래프트 준비에 실패했어.")

    def test_mic_status_uses_detailed_bridge_snapshot(self) -> None:
        snapshot = {
            "enabled": True,
            "ready": True,
            "micEnabled": False,
            "mic": {"enabled": False, "captureActive": False},
            "segmentCount": 0,
            "transcriptCount": 0,
        }

        self.assertEqual(render_local_mic_status(snapshot), "마이크 입력은 꺼져 있어.")
        context = compact_local_bridge_context(snapshot)
        self.assertIn("mic_enabled=false", context)
        self.assertIn("mic_capture_active=false", context)
        self.assertIn("mic_segment_count=0", context)
        self.assertIn("mic_transcript_count=0", context)

    def test_unbacked_progress_reply_is_blocked_without_task_id(self) -> None:
        reply = "음성 상태를 확인해볼게. 잠시만 기다려줘."

        self.assertTrue(has_unbacked_progress_claim(reply))
        self.assertEqual(enforce_action_reply_contract(reply), UNBACKED_PROGRESS_FALLBACK)

    def test_unbacked_progress_sentence_is_removed_when_result_exists(self) -> None:
        reply = "확인해볼게. 마이크 입력은 꺼져 있어."

        self.assertEqual(enforce_action_reply_contract(reply), "마이크 입력은 꺼져 있어.")

    def test_progress_reply_is_allowed_only_with_active_task_id(self) -> None:
        reply = "로그 전체 점검을 시작할게. 완료되면 이어서 알려줄게."

        self.assertEqual(
            enforce_action_reply_contract(reply, active_task_id="fast-action-1"),
            reply,
        )

    def test_action_coordinator_records_started_and_completed_events(self) -> None:
        ticks = iter((100.0, 101.0, 102.0, 103.0))
        coordinator = FastActionCoordinator(time_fn=lambda: next(ticks))

        task = coordinator.start(
            kind="unit",
            source="control_page",
            user_text="긴 작업",
            start_reply="작업을 시작했어.",
        )
        completed = coordinator.complete(task.task_id, "작업을 마쳤어.")
        snapshot = coordinator.snapshot()

        self.assertEqual(task.task_id, "fast-action-1")
        self.assertEqual(completed.status, "completed")
        self.assertEqual(snapshot["activeCount"], 0)
        self.assertEqual([event["type"] for event in snapshot["events"]], ["started", "completed"])
        self.assertEqual(coordinator.events_after(1)[0]["reply"], "작업을 마쳤어.")

    def test_history_cap_never_evicts_a_running_task_before_terminal_result(self) -> None:
        coordinator = FastActionCoordinator(history_limit=4, time_fn=lambda: 100.0)
        tasks = [
            coordinator.start(
                kind="unit",
                source="control_page",
                user_text=f"task {index}",
                start_reply="started",
            )
            for index in range(5)
        ]

        self.assertIs(coordinator.get(tasks[0].task_id), tasks[0])
        completed = coordinator.complete(tasks[0].task_id, "finished")

        self.assertEqual(completed.status, "completed")
        self.assertEqual(coordinator.snapshot()["activeCount"], 4)
        self.assertEqual(coordinator.internal_snapshot()["events"][-1]["type"], "completed")

    def test_action_coordinator_preserves_cancelled_terminal_and_receipt(self) -> None:
        coordinator = FastActionCoordinator(time_fn=lambda: 100.0)
        task = coordinator.start(
            kind="iterative_task",
            source="control_page",
            user_text="파일을 고쳐줘",
            start_reply="작업을 시작했어.",
        )

        cancelled = coordinator.cancel(
            task.task_id,
            "사용자가 파일 변경 승인을 취소했어.",
            memory_receipt=not_used_memory_receipt_ref(),
        )
        internal = coordinator.internal_snapshot()

        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(cancelled.error, "")
        self.assertEqual(internal["activeCount"], 0)
        self.assertEqual(internal["events"][-1]["type"], "cancelled")
        self.assertEqual(internal["events"][-1]["status"], "cancelled")
        self.assertEqual(
            internal["events"][-1]["_memoryReceiptRef"]["state"],
            "not_used",
        )


if __name__ == "__main__":
    unittest.main()
