from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402


class FastControlApiToolTests(unittest.TestCase):
    def setUp(self) -> None:
        fast_api.CONTROL_PAGE_UI_COMMANDS.clear()
        fast_api.CONTROL_PAGE_UI_COMMAND_SEQ = 0
        fast_api.LOCAL_BRIDGE_SPEAK_QUEUE.clear()
        fast_api.LOCAL_BRIDGE_SPEAK_SEQ = 0
        fast_api.LOCAL_BRIDGE_STATUS.clear()
        fast_api.LOCAL_BRIDGE_STATUS.update({"enabled": False, "ready": False, "mode": "windows_io_bridge"})
        fast_api.SHUTDOWN_REQUEST.update({"requested": False, "requestedAt": None, "source": "", "reason": ""})
        fast_api.RESTART_REQUEST.update({"requested": False, "requestedAt": None, "source": "", "reason": ""})

    def test_memory_panel_slash_command_routes_without_main_llm(self) -> None:
        self.assertEqual(fast_api.detect_memory_panel_action("/memory"), "toggle")

    def test_natural_memory_panel_open_command_routes_without_main_llm(self) -> None:
        self.assertEqual(fast_api.detect_memory_panel_action("메모리 패널 열어줘"), "open")

    def test_memory_panel_action_adds_frontend_command_state(self) -> None:
        reply = fast_api.execute_memory_panel_action("open")
        state = fast_api.build_control_page_panel_state()

        self.assertIn("메모리 패널", reply)
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["commands"][0]["panel"], "memory")
        self.assertEqual(state["commands"][0]["action"], "open")

    def test_default_commands_expose_memory_panel_command(self) -> None:
        commands = {item["command"] for item in fast_api.build_default_commands()}
        self.assertIn("/memory", commands)
        self.assertIn("/restart", commands)

    def test_visible_text_strips_internal_answer_tag(self) -> None:
        self.assertEqual(fast_api.visible_text("[\ub2f5\ubcc0] \uc548\ub155"), "\uc548\ub155")

    def test_pop_speakable_chunks_returns_completed_sentence_only(self) -> None:
        chunks, remainder = fast_api.pop_speakable_chunks("\uccab \ubb38\uc7a5. \ub458\uc9f8")

        self.assertEqual(chunks, ["\uccab \ubb38\uc7a5."])
        self.assertEqual(remainder, "\ub458\uc9f8")

    def test_pop_speakable_chunks_flushes_tail_when_forced(self) -> None:
        chunks, remainder = fast_api.pop_speakable_chunks("\uc9e7\uc740 \ub2f5\ubcc0", force=True)

        self.assertEqual(chunks, ["\uc9e7\uc740 \ub2f5\ubcc0"])
        self.assertEqual(remainder, "")

    def test_local_bridge_snapshot_marks_stale_ready_false(self) -> None:
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "lastError": "",
                "updatedAt": 100.0,
            }
        )

        snapshot = fast_api.local_bridge_status_snapshot(now=100.0 + fast_api.LOCAL_BRIDGE_STALE_AFTER_SEC + 1.0)

        self.assertTrue(snapshot["stale"])
        self.assertFalse(snapshot["ready"])
        self.assertIn("local_bridge_stale", snapshot["lastError"])

    def test_local_bridge_speech_queue_requires_ready_bridge_and_drains(self) -> None:
        self.assertIsNone(fast_api.queue_local_bridge_speech("hello", source="test"))

        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "lastError": "",
                "updatedAt": fast_api.time.time(),
            }
        )

        queued = fast_api.queue_local_bridge_speech(" hello ", source="test")
        drained = fast_api.drain_local_bridge_speak_requests()

        self.assertIsNotNone(queued)
        self.assertEqual(drained[0]["text"], "hello")
        self.assertEqual(drained[0]["source"], "test")
        self.assertEqual(fast_api.drain_local_bridge_speak_requests(), [])

    def test_main_llm_stream_queues_speakable_chunks_before_final_reply(self) -> None:
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "lastError": "",
                "updatedAt": fast_api.time.time(),
            }
        )
        original_iter = fast_api.iter_main_llm_deltas

        async def fake_iter_main_llm_deltas(text: str, *, source: str):
            yield "First sentence. "
            yield "Second sentence."

        fast_api.iter_main_llm_deltas = fake_iter_main_llm_deltas
        try:
            reply, queued_count = asyncio.run(
                fast_api.ask_main_llm_and_queue_speech("hello", source="control_page")
            )
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        queued = fast_api.drain_local_bridge_speak_requests()

        self.assertEqual(reply, "First sentence. Second sentence.")
        self.assertEqual(queued_count, 2)
        self.assertEqual([item["text"] for item in queued], ["First sentence.", "Second sentence."])

    def test_fast_main_prompt_keeps_evelyn_persona_contract(self) -> None:
        self.assertIn("너는 Evelyn", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("한국어로 친구처럼 짧게 반말", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("비서/상담원 말투", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("generic remote text-only chatbot", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("반드시 한국어 반말", fast_api.FAST_MAIN_LLM_USER_PREFIX)
        self.assertIn("무엇을 도와드릴까요", fast_api.FAST_MAIN_LLM_USER_PREFIX)

    def test_shutdown_request_is_reported_to_local_bridge_status(self) -> None:
        result = fast_api.request_local_shutdown(source="test", reason="unit")

        self.assertTrue(result["ok"])
        self.assertTrue(fast_api.SHUTDOWN_REQUEST["requested"])
        self.assertEqual(fast_api.SHUTDOWN_REQUEST["source"], "test")

    def test_restart_request_is_reported_to_local_bridge_status(self) -> None:
        result = fast_api.request_local_restart(source="test", reason="unit")
        state = fast_api.build_control_state({"services": []})

        self.assertTrue(result["ok"])
        self.assertTrue(fast_api.RESTART_REQUEST["requested"])
        self.assertEqual(fast_api.RESTART_REQUEST["source"], "test")
        self.assertTrue(state["restart"]["requested"])

    def test_control_state_exposes_control_plane_roles(self) -> None:
        state = fast_api.build_control_state(
            {
                "legacyServices": {"botReady": True},
                "services": [{"id": "bot_api", "state": "up", "ready": True}],
            }
        )

        control_plane = state["runtime"]["controlPlane"]
        self.assertEqual(control_plane["controlPage"]["role"], "Control-Page")
        self.assertEqual(control_plane["botApi"]["role"], "Bot API")
        self.assertEqual(control_plane["botApi"]["port"], fast_api.PORT)
        self.assertIn("Bot API", state["statusText"])

    def test_state_handler_returns_expected_control_page_schema(self) -> None:
        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "legacyServices": {
                    "botReady": True,
                    "mainReady": True,
                    "routerReady": True,
                    "subReady": True,
                    "ttsReady": True,
                    "sttReady": True,
                },
                "services": [{"id": "bot_api", "state": "up", "ready": True}],
                "summary": "all ready",
            }

        original_collect = fast_api.collect_runtime_health
        fast_api.collect_runtime_health = fake_collect_runtime_health
        try:
            response = asyncio.run(fast_api.state_handler(object()))
        finally:
            fast_api.collect_runtime_health = original_collect

        payload = fast_api.json.loads(response.text or "{}")
        self.assertTrue(payload["ok"])
        self.assertIn("generatedAt", payload)
        self.assertIn("voice", payload)
        self.assertIn("restart", payload)
        self.assertIn("shutdown", payload)
        self.assertIn("runtime", payload)
        self.assertIn("controlPlane", payload["runtime"])
        self.assertIn("services", payload["runtime"])
        self.assertTrue(payload["runtime"]["services"]["botReady"])
        self.assertTrue(payload["runtime"]["services"]["chatReady"])
        self.assertTrue(payload["runtime"]["services"]["voiceReady"])
        self.assertEqual(payload["runtime"]["controlPlane"]["controlPage"]["port"], fast_api.PUBLIC_CONTROL_PORT)
        self.assertEqual(payload["runtime"]["controlPlane"]["botApi"]["port"], fast_api.PORT)

    def test_control_state_preserves_local_bridge_tts_warmup_status(self) -> None:
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "lastError": "",
                "updatedAt": fast_api.time.time(),
                "ttsWarmup": {"enabled": True, "done": True, "error": "", "ms": 512.3},
            }
        )

        state = fast_api.build_control_state(
            {
                "legacyServices": {"botReady": True, "ttsReady": True, "sttReady": True},
                "services": [{"id": "bot_api", "state": "up", "ready": True}],
            }
        )

        bridge = state["voice"]["localBridge"]
        self.assertEqual(state["voice"]["outputMode"], "windows_local_bridge")
        self.assertFalse(bridge["stale"])
        self.assertTrue(bridge["ttsWarmup"]["enabled"])
        self.assertTrue(bridge["ttsWarmup"]["done"])
        self.assertEqual(bridge["ttsWarmup"]["ms"], 512.3)

    def test_control_state_promotes_local_bridge_voice_activity(self) -> None:
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "speaking": True,
                "lastError": "",
                "updatedAt": fast_api.time.time(),
                "mic": {"captureActive": True},
            }
        )

        state = fast_api.build_control_state(
            {
                "legacyServices": {"botReady": True, "ttsReady": True, "sttReady": True},
                "services": [{"id": "bot_api", "state": "up", "ready": True}],
            }
        )

        self.assertTrue(state["voice"]["speaking"])
        self.assertTrue(state["voice"]["listening"])
        self.assertEqual(state["voice"]["ttsTargetName"], "로컬 스피커")
        self.assertEqual(state["ui"]["submode"], "voice-speaking")

    def test_local_bridge_status_post_drains_speak_requests_once(self) -> None:
        class _Request:
            method = "POST"

            async def json(self):
                return {
                    "enabled": True,
                    "ready": True,
                    "lastError": "",
                    "ttsWarmup": {"enabled": True, "done": True, "error": "", "ms": 400.0},
                }

        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "lastError": "",
                "updatedAt": fast_api.time.time(),
            }
        )
        queued = fast_api.queue_local_bridge_speech("hello bridge", source="unit")
        self.assertIsNotNone(queued)

        first = asyncio.run(fast_api.local_bridge_status_handler(_Request()))
        second = asyncio.run(fast_api.local_bridge_status_handler(_Request()))

        first_payload = fast_api.json.loads(first.text or "{}")
        second_payload = fast_api.json.loads(second.text or "{}")
        self.assertEqual([item["text"] for item in first_payload["speakRequests"]], ["hello bridge"])
        self.assertEqual(second_payload["speakRequests"], [])
        self.assertTrue(first_payload["localBridge"]["ttsWarmup"]["done"])

    def test_local_bridge_status_get_does_not_drain_speak_requests(self) -> None:
        class _GetRequest:
            method = "GET"

        class _PostRequest:
            method = "POST"

            async def json(self):
                return {"enabled": True, "ready": True, "lastError": ""}

        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "lastError": "",
                "updatedAt": fast_api.time.time(),
            }
        )
        queued = fast_api.queue_local_bridge_speech("do not drain on get", source="unit")
        self.assertIsNotNone(queued)

        get_response = asyncio.run(fast_api.local_bridge_status_handler(_GetRequest()))
        get_payload = fast_api.json.loads(get_response.text or "{}")
        self.assertEqual(get_payload["speakRequests"], [])
        self.assertEqual(len(fast_api.LOCAL_BRIDGE_SPEAK_QUEUE), 1)

        post_response = asyncio.run(fast_api.local_bridge_status_handler(_PostRequest()))
        post_payload = fast_api.json.loads(post_response.text or "{}")
        self.assertEqual([item["text"] for item in post_payload["speakRequests"]], ["do not drain on get"])
        self.assertEqual(fast_api.LOCAL_BRIDGE_SPEAK_QUEUE, [])

    def test_boot_progress_marks_components_ready_at_one_hundred_percent(self) -> None:
        health = {
            "services": [
                {"id": service_id, "state": "up", "ready": True}
                for service_id, _label in fast_api.BOOT_STEPS
            ]
        }

        progress = fast_api.build_boot_progress(health)

        self.assertEqual(progress["percent"], 100)
        self.assertTrue(progress["ready"])
        self.assertTrue(progress["componentsReady"])


if __name__ == "__main__":
    unittest.main()
