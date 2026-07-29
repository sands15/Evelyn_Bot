from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402


class FastControlStreamContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        fast_api.clear_background_action_handlers()
        fast_api.MEMORY_RECALL_PROGRESS_LAST_TEXT = None
        fast_api.LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.update(
            {
                "revision": 0,
                "command": "",
                "action": "",
                "requestedAt": None,
                "source": "",
            }
        )

    async def asyncTearDown(self) -> None:
        pending = list(fast_api.BACKGROUND_ACTION_TASKS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        fast_api.clear_background_action_handlers()

    async def post_stream(self, text: str) -> list[dict[str, object]]:
        client = TestClient(TestServer(fast_api.create_app()))
        await client.start_server()
        try:
            response = await client.post(
                "/api/control-page/chat-stream",
                json={"text": text, "source": "local_bridge"},
            )
            self.assertEqual(response.status, 200)
            body = await response.text()
        finally:
            await client.close()
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    async def test_stream_suppresses_unbacked_progress_sentences(self) -> None:
        original_iter = fast_api.iter_main_llm_deltas

        async def fake_iter(text: str, *, source: str):
            yield "확인해볼게. "
            yield "잠시만 기다려줘."

        fast_api.iter_main_llm_deltas = fake_iter
        try:
            events = await self.post_stream("설정 확인해줘")
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        sentences = [event["text"] for event in events if event["type"] == "sentence"]
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(sentences, [fast_api.enforce_action_reply_contract("확인해볼게.")])
        self.assertNotIn("확인해볼게", str(done["reply"]))
        self.assertNotIn("기다려줘", str(done["reply"]))

    async def test_stream_keeps_verified_result_and_drops_preface(self) -> None:
        original_iter = fast_api.iter_main_llm_deltas

        async def fake_iter(text: str, *, source: str):
            yield "확인해볼게. "
            yield "마이크 입력은 꺼져 있어."

        fast_api.iter_main_llm_deltas = fake_iter
        try:
            events = await self.post_stream("설정 결과를 말해줘")
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        sentences = [event["text"] for event in events if event["type"] == "sentence"]
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(sentences, ["마이크 입력은 꺼져 있어."])
        self.assertEqual(done["reply"], "마이크 입력은 꺼져 있어.")
        deltas = "".join(str(event["text"]) for event in events if event["type"] == "delta")
        self.assertNotIn("확인해볼게", deltas)
        self.assertEqual(deltas, "마이크 입력은 꺼져 있어.")

    async def test_stream_emits_safe_word_deltas_before_sentence_completion(self) -> None:
        original_iter = fast_api.iter_main_llm_deltas

        async def fake_iter(text: str, *, source: str):
            yield "마이크 "
            yield "입력은 "
            yield "꺼져 "
            yield "있어."

        fast_api.iter_main_llm_deltas = fake_iter
        try:
            events = await self.post_stream("상태를 말해줘")
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        deltas = [str(event["text"]) for event in events if event["type"] == "delta"]
        self.assertEqual(deltas, ["마이크 ", "입력은 ", "꺼져 ", "있어."])
        done = next(event for event in events if event["type"] == "done")
        self.assertIsNotNone(done["firstDeltaMs"])

    async def test_memory_recall_progress_is_non_terminal_and_final_reply_continues(self) -> None:
        original_iter = fast_api.iter_main_llm_deltas

        async def fake_iter(text: str, *, source: str):
            yield "기억을 "
            yield "찾았어. "
            yield "그때 일 처리 중이라고 했어."

        fast_api.iter_main_llm_deltas = fake_iter
        try:
            events = await self.post_stream("전에 뭐 하고 있다고 했는지 기억해서 말해줘")
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        progress = [event for event in events if event["type"] == "progress"]
        self.assertEqual(len(progress), 1)
        self.assertIn(progress[0]["text"], fast_api.MEMORY_RECALL_PROGRESS_TEXTS)
        self.assertEqual(progress[0]["stage"], "memory_recall")
        self.assertTrue(progress[0]["requiresContinuation"])
        self.assertFalse(progress[0]["terminal"])

        progress_index = events.index(progress[0])
        done_index = next(index for index, event in enumerate(events) if event["type"] == "done")
        self.assertLess(progress_index, done_index)
        self.assertTrue(
            any(
                event["type"] in {"delta", "sentence"}
                for event in events[progress_index + 1 : done_index]
            )
        )

        done = events[done_index]
        self.assertEqual(done["reply"], "기억을 찾았어. 그때 일 처리 중이라고 했어.")
        self.assertNotEqual(done["reply"], progress[0]["text"])
        self.assertIsNotNone(done["firstProgressMs"])
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["text"], done["reply"])

    async def test_memory_recall_progress_variants_are_random_without_immediate_repeat(self) -> None:
        candidate_sets: list[tuple[str, ...]] = []

        def choose_first(candidates: tuple[str, ...]) -> str:
            candidate_sets.append(tuple(candidates))
            return candidates[0]

        with patch.object(fast_api.random, "choice", side_effect=choose_first) as random_choice:
            selected = [
                fast_api.next_memory_recall_progress_text()
                for _ in range(len(fast_api.MEMORY_RECALL_PROGRESS_TEXTS) * 2)
            ]

        self.assertEqual(random_choice.call_count, len(selected))
        self.assertTrue(all(text in fast_api.MEMORY_RECALL_PROGRESS_TEXTS for text in selected))
        self.assertTrue(all(left != right for left, right in zip(selected, selected[1:])))
        for previous, candidates in zip([None, *selected[:-1]], candidate_sets):
            if previous is not None:
                self.assertNotIn(previous, candidates)

    async def test_non_memory_stream_does_not_emit_progress(self) -> None:
        original_iter = fast_api.iter_main_llm_deltas

        async def fake_iter(text: str, *, source: str):
            yield "바로 답했어."

        fast_api.iter_main_llm_deltas = fake_iter
        try:
            events = await self.post_stream("짧게 답해줘")
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        self.assertFalse(any(event["type"] == "progress" for event in events))
        done = next(event for event in events if event["type"] == "done")
        self.assertIsNone(done["firstProgressMs"])

    async def test_stream_allows_start_reply_only_after_task_id_exists(self) -> None:
        async def runner(user_text: str, source: str) -> str:
            await asyncio.sleep(0)
            return "긴 작업을 완료했어."

        fast_api.register_background_action_handler(
            kind="unit",
            matcher=lambda text: text == "긴 작업",
            runner=runner,
            start_reply="긴 작업을 시작할게.",
        )

        events = await self.post_stream("긴 작업")
        pending = list(fast_api.BACKGROUND_ACTION_TASKS)
        if pending:
            await asyncio.gather(*pending)

        sentence = next(event for event in events if event["type"] == "sentence")
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(sentence["text"], "긴 작업을 시작할게.")
        self.assertEqual(done["taskId"], "fast-action-1")
        self.assertEqual(fast_api.ACTION_COORDINATOR.get("fast-action-1").status, "completed")
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["text"], "긴 작업을 완료했어.")

    async def test_research_request_starts_real_task_and_publishes_followup(self) -> None:
        original_runner = fast_api.execute_web_research_plan

        async def fake_runner(plan, user_text: str, source: str) -> str:
            self.assertEqual(plan.tool_name, "research_compare")
            self.assertIn("STT", plan.query)
            self.assertEqual(source, "local_bridge")
            await asyncio.sleep(0)
            return "STT 교체 후보를 비교했고, 우선 검증할 모델은 Qwen3-ASR과 faster-whisper야."

        fast_api.execute_web_research_plan = fake_runner
        try:
            events = await self.post_stream("S T T 모델 교체 후보를 알아봐줘")
            pending = list(fast_api.BACKGROUND_ACTION_TASKS)
            if pending:
                await asyncio.gather(*pending)
        finally:
            fast_api.execute_web_research_plan = original_runner

        sentence = next(event for event in events if event["type"] == "sentence")
        done = next(event for event in events if event["type"] == "done")
        self.assertIn(sentence["text"], fast_api.RESEARCH_PROGRESS_TEXTS)
        self.assertEqual(done["taskId"], "fast-action-1")
        self.assertEqual(done["taskStatus"], "running")
        self.assertEqual(fast_api.ACTION_COORDINATOR.get("fast-action-1").status, "completed")
        self.assertIn("STT 교체 후보를 비교했고", fast_api.CHAT_MESSAGES[-1]["text"])

    async def test_followup_research_uses_previous_topic(self) -> None:
        original_runner = fast_api.execute_web_research_plan
        captured = {}

        async def fake_runner(plan, user_text: str, source: str) -> str:
            captured["query"] = plan.query
            return "후속 검색을 완료했어."

        fast_api.append_chat_message("user", "정훈", "로컬 STT 모델을 교체하고 싶어", source="local_bridge")
        fast_api.append_chat_message("assistant", "Evelyn", "현재 모델 상태는 확인할 수 있어.", source="test")
        fast_api.execute_web_research_plan = fake_runner
        try:
            events = await self.post_stream("아니, 그거 찾아보라고")
            pending = list(fast_api.BACKGROUND_ACTION_TASKS)
            if pending:
                await asyncio.gather(*pending)
        finally:
            fast_api.execute_web_research_plan = original_runner

        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(done["taskId"], "fast-action-1")
        self.assertIn("로컬 STT 모델", captured["query"])

    async def test_minecraft_execution_command_lazy_starts_and_publishes_followup(self) -> None:
        original_runner = fast_api.execute_local_bridge_minecraft_command

        async def fake_runner(command: str, source: str) -> str:
            self.assertEqual(command, "마인크래프트에서 나무 캐줘")
            self.assertEqual(source, "local_bridge")
            await asyncio.sleep(0)
            return "마인크래프트 모델과 서비스를 준비했고, 게임 접속과 명령 전달까지 확인했어."

        fast_api.execute_local_bridge_minecraft_command = fake_runner
        try:
            events = await self.post_stream("마인크래프트에서 나무 캐줘")
            pending = list(fast_api.BACKGROUND_ACTION_TASKS)
            if pending:
                await asyncio.gather(*pending)
        finally:
            fast_api.execute_local_bridge_minecraft_command = original_runner

        sentence = next(event for event in events if event["type"] == "sentence")
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(
            sentence["text"],
            "마인크래프트 쪽을 준비할게. 끝나면 바로 알려줄게.",
        )
        self.assertEqual(done["taskId"], "fast-action-1")
        self.assertEqual(done["taskStatus"], "running")
        task = fast_api.ACTION_COORDINATOR.get("fast-action-1")
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "completed")
        self.assertIn("명령 전달까지 확인했어", fast_api.CHAT_MESSAGES[-1]["text"])


if __name__ == "__main__":
    unittest.main()
