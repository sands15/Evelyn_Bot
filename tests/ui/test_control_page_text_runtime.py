from __future__ import annotations

import asyncio
import sys
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


class FakeScope:
    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id


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

    async def ask_streaming(self, _text: str, **kwargs) -> str:
        if self.ask_error is not None:
            raise self.ask_error
        if self.black_frame:
            kwargs["metrics"]["meta"]["vision_capture_error"] = "DXGI black frame"
        return "[question-oh] 원본 답변"

    def maybe_append(self, *args, **kwargs) -> tuple[str, bool]:
        self.append_calls.append((args, kwargs))
        return f"{args[0]} 추가 질문?", self.append_proactive

    def build_deps(self) -> ControlPageTextRuntimeDeps:
        return ControlPageTextRuntimeDeps(
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
            log_voice_bottleneck_summary=lambda metrics, **kwargs: self.summaries.append((metrics, kwargs)),
            schedule_local_control_tts=lambda *args, **kwargs: self.scheduled.append((args, kwargs)),
            format_display_text=lambda text, **_kwargs: f" display:{text} ",
            fallback_answer_for=lambda _text: "fallback",
            detach_task=lambda scope, task: self.detached.append((scope, task)),
            clear_room_turn_scope=lambda key, scope: self.cleared.append((key, scope)),
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
        self.assertEqual(self.scheduled[0][0], ("원본 답변 추가 질문?",))
        self.assertEqual(self.scheduled[0][1]["turn_id"], "turn-1")
        self.assertEqual(self.summaries[0][1]["event_name"], "text_turn_summary")
        self.assertEqual(self.detached[0][1], "attached-task")
        self.assertEqual(self.cleared[0][0], "control:7")

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
