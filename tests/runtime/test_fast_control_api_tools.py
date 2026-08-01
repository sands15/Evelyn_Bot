from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
sys.modules.setdefault("numpy", SimpleNamespace(ndarray=object))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core import explicit_memory_confirmation as explicit_memory  # noqa: E402
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


class FastControlApiToolTests(unittest.TestCase):
    def setUp(self) -> None:
        original_local_voice_admission = fast_api.LOCAL_VOICE_ADMISSION
        fast_api.LOCAL_VOICE_ADMISSION = fast_api.LocalVoiceAdmissionManager()
        self.addCleanup(
            setattr,
            fast_api,
            "LOCAL_VOICE_ADMISSION",
            original_local_voice_admission,
        )
        validation_context_patcher = patch.object(
            fast_api,
            "local_voice_validation_binding_is_current",
            side_effect=lambda binding: not binding,
        )
        validation_context_patcher.start()
        self.addCleanup(validation_context_patcher.stop)
        self._voice_turn_seq = 0
        fast_api.FAST_RUNTIME_HEALTH_CACHE.clear()
        fast_api.CHAT_MESSAGES.clear()
        fast_api.ACTION_COORDINATOR.clear()
        fast_api.clear_background_action_handlers()
        fast_api.CONTROL_PAGE_UI_COMMANDS.clear()
        fast_api.CONTROL_PAGE_UI_COMMAND_SEQ = 0
        fast_api.LOCAL_BRIDGE_SPEAK_QUEUE.clear()
        fast_api.LOCAL_BRIDGE_SPEAK_SEQ = 0
        fast_api.LOCAL_BRIDGE_STATUS.clear()
        fast_api.LOCAL_BRIDGE_STATUS.update({"enabled": False, "ready": False, "mode": "windows_io_bridge"})
        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
            {"revision": 0, "enabled": None, "requestedAt": None, "source": ""}
        )
        fast_api.LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST.update(
            {
                "revision": 0,
                "command": "",
                "action": "",
                "requestedAt": None,
                "source": "",
            }
        )
        fast_api.SHUTDOWN_REQUEST.update({"requested": False, "requestedAt": None, "source": "", "reason": ""})
        fast_api.RESTART_REQUEST.update({"requested": False, "requestedAt": None, "source": "", "reason": ""})

    def admitted_local_payload(self, text: str) -> dict[str, object]:
        self._voice_turn_seq += 1
        bridge_instance_id = "test-fast-api-tools-bridge"
        turn_id = f"test-fast-api-tools-turn-{self._voice_turn_seq}"
        fast_api.LOCAL_VOICE_ADMISSION.observe_bridge_instance(
            bridge_instance_id
        )
        issued = fast_api.LOCAL_VOICE_ADMISSION.issue(
            bridge_instance_id,
            turn_id,
            f"이블린 {text}",
            validation_binding={},
            validation_is_current=lambda binding: not binding,
        )
        self.assertTrue(issued.get("admitted"), issued)
        return {
            "text": issued["forwardText"],
            "source": "local_bridge",
            "bridgeInstanceId": bridge_instance_id,
            "turnId": turn_id,
            "admissionToken": issued["admissionToken"],
        }

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
        self.assertEqual(
            commands,
            {
                "/help",
                "/status",
                "/remember <fact>",
                "/memory",
                "/obsidian",
                "/voice status",
                "/mic status",
                "/mic on",
                "/mic off",
                "/minecraft connect",
                "/minecraft status",
                "/inventory",
                "/voyager stats",
                "/minecraft disconnect",
                "/minecraft goal <goal>",
                "/autonomy status",
                "/repair preview",
                "/repair start",
                "/restart",
                "/shutdown",
            },
        )

    def test_visible_text_strips_internal_answer_tag(self) -> None:
        self.assertEqual(fast_api.visible_text("[\ub2f5\ubcc0] \uc548\ub155"), "\uc548\ub155")

    def test_restored_chat_messages_feed_next_llm_request(
        self,
    ) -> None:
        fast_api.CHAT_MESSAGES.extend(
            [
                {
                    "role": "user",
                    "author": "정훈",
                    "text": "재시작 전 질문",
                    "source": (
                        "fast_control_continuity_restore"
                    ),
                },
                {
                    "role": "assistant",
                    "author": "Evelyn",
                    "text": "재시작 전 답변",
                    "source": (
                        "fast_control_continuity_restore"
                    ),
                    "memoryReceiptRef": (
                        fast_api.not_used_memory_receipt_ref()
                    ),
                },
            ]
        )
        built = SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "새 질문"},
            ],
            context=SimpleNamespace(
                required_evidence_failure_reply="",
                grounded_evidence_reply="",
                memory_receipt={
                    "schema": "memory.context-receipt.v1",
                    "state": "provided",
                    "groundingState": "attributed",
                    "suppliedNoteIds": ["note-restored"],
                    "contentFree": True,
                },
            ),
        )

        with patch.object(
            fast_api,
            "build_fast_main_llm_request",
            new=AsyncMock(return_value=built),
        ) as build_request:
            async def build_and_read_receipt():
                await fast_api.build_main_llm_request_payload(
                    "새 질문",
                    source="control_page",
                )
                return fast_api.current_fast_memory_context_receipt()

            memory_receipt = asyncio.run(build_and_read_receipt())

        self.assertEqual(
            build_request.await_args.kwargs[
                "recent_messages"
            ],
            [
                {
                    "role": "user",
                    "content": "재시작 전 질문",
                },
                {
                    "role": "assistant",
                    "content": "재시작 전 답변",
                },
            ],
        )
        self.assertEqual(
            memory_receipt["suppliedNoteIds"],
            ["note-restored"],
        )

    def test_verified_cross_surface_context_feeds_planner_and_llm(
        self,
    ) -> None:
        class Bridge:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def merge_for_fast(
                self,
                messages,
                *,
                current_user_text,
            ):
                self.calls.append(current_user_text)
                return [
                    *messages,
                    {
                        "role": "user",
                        "content": "디스코드에서 한 질문",
                    },
                    {
                        "role": "assistant",
                        "content": "디스코드에서 한 답",
                        "memoryReceiptRef": (
                            fast_api.not_used_memory_receipt_ref()
                        ),
                    },
                ]

        bridge = Bridge()
        built = SimpleNamespace(
            messages=[
                {"role": "system", "content": "system"},
                {"role": "user", "content": "새 질문"},
            ],
            context=SimpleNamespace(
                required_evidence_failure_reply="",
                grounded_evidence_reply="",
            ),
        )
        with (
            patch.object(
                fast_api,
                "CROSS_SURFACE_CONTINUITY_BRIDGE",
                bridge,
            ),
            patch.object(
                fast_api,
                "build_fast_main_llm_request",
                new=AsyncMock(return_value=built),
            ) as build_request,
        ):
            planner_context = (
                fast_api.recent_chat_messages_for_planner(
                    "새 질문",
                )
            )
            asyncio.run(
                fast_api.build_main_llm_request_payload(
                    "새 질문",
                    source="control_page",
                )
            )

        expected = [
            {
                "role": "user",
                "content": "디스코드에서 한 질문",
            },
            {
                "role": "assistant",
                "content": "디스코드에서 한 답",
            },
        ]
        self.assertEqual(planner_context, expected)
        self.assertEqual(
            build_request.await_args.kwargs[
                "recent_messages"
            ],
            expected,
        )
        self.assertEqual(bridge.calls, ["새 질문", "새 질문"])

    def test_web_capability_question_bypasses_main_llm(self) -> None:
        reply = asyncio.run(
            fast_api.resolve_pre_llm_reply("웹검색 권한 없어?", source="local_bridge")
        )

        self.assertIn("웹 검색 도구는 연결돼", reply or "")

    def test_pop_speakable_chunks_returns_completed_sentence_only(self) -> None:
        chunks, remainder = fast_api.pop_speakable_chunks("\uccab \ubb38\uc7a5. \ub458\uc9f8")

        self.assertEqual(chunks, ["\uccab \ubb38\uc7a5."])
        self.assertEqual(remainder, "\ub458\uc9f8")

    def test_pop_speakable_chunks_flushes_tail_when_forced(self) -> None:
        chunks, remainder = fast_api.pop_speakable_chunks("\uc9e7\uc740 \ub2f5\ubcc0", force=True)

        self.assertEqual(chunks, ["\uc9e7\uc740 \ub2f5\ubcc0"])
        self.assertEqual(remainder, "")

    def test_datetime_question_bypasses_main_llm(self) -> None:
        reply = asyncio.run(
            fast_api.resolve_pre_llm_reply("\uc9c0\uae08 \uba87\uc2dc\uc57c?", source="control_page")
        )

        self.assertIsNotNone(reply)
        self.assertIn("\uc9c0\uae08\uc740", reply or "")

    def test_internal_minecraft_status_probe_is_quiet_only_for_reads(
        self,
    ) -> None:
        request = AsyncMock(return_value=({}, ""))

        with patch.object(
            fast_api,
            "request_minecraft_control_service",
            new=request,
        ):
            asyncio.run(
                fast_api._request_minecraft_world_runtime(
                    "GET",
                    "/status",
                    None,
                )
            )
            asyncio.run(
                fast_api._request_minecraft_world_runtime(
                    "POST",
                    "/start",
                    {},
                )
            )

        self.assertFalse(
            request.await_args_list[0].kwargs["log_failure"]
        )
        self.assertTrue(
            request.await_args_list[1].kwargs["log_failure"]
        )

    def test_grounded_exact_reply_bypasses_main_llm_stream(self) -> None:
        request = SimpleNamespace(
            context=SimpleNamespace(
                required_evidence_failure_reply="",
                grounded_evidence_reply="Minecraft 26.2 - 싱글플레이",
            ),
            messages=[],
        )

        async def collect() -> list[str]:
            with patch.object(
                fast_api,
                "build_fast_main_llm_request",
                new=AsyncMock(return_value=request),
            ), patch.object(
                fast_api,
                "ClientSession",
                side_effect=AssertionError("main LLM must not be called"),
            ):
                return [
                    delta
                    async for delta in fast_api.iter_main_llm_deltas(
                        "현재 Windows 화면의 창 제목만 정확히 말해줘.",
                        source="control_page",
                    )
                ]

        self.assertEqual(
            asyncio.run(collect()),
            ["Minecraft 26.2 - 싱글플레이"],
        )

    def test_help_reply_is_built_from_the_fast_command_registry(self) -> None:
        reply = asyncio.run(fast_api.resolve_pre_llm_reply("/help", source="control_page"))

        self.assertIn("/minecraft status", reply or "")
        self.assertIn("/shutdown", reply or "")
        self.assertNotIn("/voice continuity", reply or "")

    def test_explicit_memory_confirmation_bypasses_planner_and_returns_content_free_receipt(self) -> None:
        class _Request:
            async def json(self):
                return {
                    "text": "/remember 나는 산책을 좋아해",
                    "source": "control_page",
                    "requestId": "control-request-123",
                }

        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {},
                "services": [],
            }

        receipt = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-34567890abcdef12",
            "sourceRef": (
                "turn:opaque-turn-" + ("d" * 64) + ":user"
            ),
            "confirmedAt": "2026-07-31T00:00:00+00:00",
            "contentFree": True,
        }
        with patch.object(
            fast_api,
            "execute_explicit_memory_confirmation",
            return_value=(
                True,
                "지금 요청을 근거로 새 기억에 저장했어.",
                receipt,
                "",
            ),
        ) as execute, patch.object(
            fast_api,
            "plan_fast_tool_request_for_turn",
            new=AsyncMock(),
        ) as planner, patch.object(
            fast_api,
            "collect_runtime_health",
            new=AsyncMock(side_effect=fake_collect_runtime_health),
        ):
            response = asyncio.run(fast_api.chat_handler(_Request()))

        payload = fast_api.json.loads(response.text or "{}")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["memoryWriteReceipt"], receipt)
        self.assertNotIn("산책", fast_api.json.dumps(receipt, ensure_ascii=False))
        self.assertEqual(
            fast_api.CHAT_MESSAGES[-1]["memoryWriteReceipt"],
            receipt,
        )
        execute.assert_called_once_with(
            "/remember 나는 산책을 좋아해",
            action_id="control-request-123",
        )
        planner.assert_not_awaited()

    def test_fast_confirmation_projects_request_id_from_receipt(
        self,
    ) -> None:
        private_request_id = "private-natural-language-secret"

        class _Request:
            async def json(self):
                return {
                    "text": "/remember 공개 영수증 경계",
                    "source": "control_page",
                    "requestId": private_request_id,
                }

        async def fake_collect_runtime_health(
            *,
            manifest,
            probe_runner,
        ):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {},
                "services": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            real_store = (
                explicit_memory.store_explicit_memory_confirmation
            )

            def store_in_temp(
                fact: str,
                **kwargs: object,
            ):
                return real_store(
                    fact,
                    root=root,
                    **kwargs,
                )

            with patch.object(
                explicit_memory,
                "store_explicit_memory_confirmation",
                side_effect=store_in_temp,
            ), patch.object(
                fast_api,
                "plan_fast_tool_request_for_turn",
                new=AsyncMock(),
            ) as planner, patch.object(
                fast_api,
                "collect_runtime_health",
                new=AsyncMock(
                    side_effect=fake_collect_runtime_health
                ),
            ):
                response = asyncio.run(
                    fast_api.chat_handler(_Request())
                )

        payload = fast_api.json.loads(response.text or "{}")
        receipt = payload["memoryWriteReceipt"]
        serialized_receipt = fast_api.json.dumps(
            receipt,
            ensure_ascii=False,
        )
        self.assertTrue(payload["ok"])
        self.assertNotIn(private_request_id, serialized_receipt)
        self.assertRegex(
            receipt["sourceRef"],
            r"^turn:opaque-turn-[0-9a-f]{64}:user$",
        )
        self.assertNotIn(
            private_request_id,
            fast_api.json.dumps(
                fast_api.CHAT_MESSAGES[-1].get(
                    "memoryWriteReceipt"
                ),
                ensure_ascii=False,
            ),
        )
        planner.assert_not_awaited()

    def test_help_chat_response_is_visible_but_not_queued_for_tts(self) -> None:
        class _Request:
            async def json(self):
                return {"text": "/help", "source": "control_page"}

        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {
                    "botReady": True,
                    "mainReady": True,
                    "routerReady": True,
                    "subReady": True,
                    "ttsReady": True,
                    "sttReady": True,
                },
                "services": [],
            }

        with patch.object(
            fast_api,
            "collect_runtime_health",
            new=AsyncMock(side_effect=fake_collect_runtime_health),
        ):
            response = asyncio.run(fast_api.chat_handler(_Request()))

        payload = fast_api.json.loads(response.text or "{}")
        self.assertTrue(payload["suppressTts"])
        self.assertIn("/shutdown", payload["reply"])
        self.assertEqual(payload["memoryReceipt"]["state"], "not_requested")
        self.assertTrue(payload["memoryReceipt"]["contentFree"])
        public_message = payload["state"]["chat"]["messages"][-1]
        self.assertNotIn(
            "memoryReceipt",
            public_message,
        )
        self.assertNotIn(
            "memoryReceiptRef",
            public_message,
        )
        self.assertEqual(fast_api.LOCAL_BRIDGE_SPEAK_QUEUE, [])

    def test_natural_runtime_commands_create_scoped_requests(self) -> None:
        shutdown_reply = asyncio.run(
            fast_api.resolve_pre_llm_reply("이블린 셧다운해", source="test")
        )
        self.assertTrue(fast_api.SHUTDOWN_REQUEST["requested"])
        self.assertIn("종료 요청", shutdown_reply or "")

        fast_api.SHUTDOWN_REQUEST.update(
            {"requested": False, "requestedAt": None, "source": "", "reason": ""}
        )
        restart_reply = asyncio.run(
            fast_api.resolve_pre_llm_reply("프로젝트 재시작해줘", source="test")
        )
        self.assertTrue(fast_api.RESTART_REQUEST["requested"])
        self.assertIn("재시작 요청", restart_reply or "")

    def test_scoped_component_shutdown_does_not_stop_the_evelyn_runtime(self) -> None:
        with patch.object(
            fast_api.MINECRAFT_WORLD_LEASE_OWNER,
            "disconnect",
            new=AsyncMock(
                return_value={
                    "running": False,
                    "connected": False,
                }
            ),
        ):
            reply = asyncio.run(
                fast_api.resolve_pre_llm_reply("마인크래프트 종료해", source="test")
            )

        self.assertEqual(reply, "마인크래프트 에이전트 연결을 중지했어.")
        self.assertFalse(fast_api.SHUTDOWN_REQUEST["requested"])

    def test_unknown_slash_command_never_falls_through_to_main_llm(self) -> None:
        reply = asyncio.run(
            fast_api.resolve_pre_llm_reply("/not-a-real-command", source="control_page")
        )

        self.assertIn("지원하지 않는 명령", reply or "")

    def test_deterministic_commands_skip_router_tool_planning(self) -> None:
        self.assertTrue(fast_api.should_skip_fast_tool_planner("/shutdown"))
        self.assertTrue(fast_api.should_skip_fast_tool_planner("이블린 재시작해"))
        self.assertTrue(fast_api.should_skip_fast_tool_planner("마인크래프트 꺼져 있어?"))
        self.assertTrue(fast_api.should_skip_fast_tool_planner("지금 몇 시야?"))
        self.assertFalse(fast_api.should_skip_fast_tool_planner("서울 날씨 검색해줘"))

    def test_unsupported_legacy_voice_command_reports_truthfully(self) -> None:
        reply = asyncio.run(
            fast_api.resolve_pre_llm_reply("/voice input auto", source="control_page")
        )

        self.assertIn("지원하지 않아", reply or "")
        self.assertNotIn("전환했어", reply or "")

    def test_runtime_status_treats_deferred_minecraft_as_standby(self) -> None:
        health = {
            "legacyServices": {
                "botReady": True,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "sttReady": True,
                "voyagerReady": False,
            },
            "summary": "Voyager is not responding.",
        }

        reply = fast_api.render_fast_runtime_status(health)

        self.assertIn("핵심 서비스는 모두 정상", reply)
        self.assertIn("마인크래프트 서비스는 명령을 받기 전까지 대기", reply)
        self.assertNotIn("not responding", reply)

    def test_minecraft_status_and_inventory_use_verified_service_payloads(self) -> None:
        status = {
            "running": True,
            "connected": True,
            "connection_state": "connected",
            "goal": "나무 캐기",
            "stage": "gather",
            "blocked_command_count": 2,
        }
        inventory = {"inventory": {"oak_log": 5, "stone": 12}}

        self.assertIn("게임 접속은 확인됐어", fast_api.render_minecraft_status(status))
        self.assertIn("진행 단계는 gather", fast_api.render_minecraft_status(status, detailed=True))
        inventory_reply = fast_api.render_minecraft_inventory(inventory)
        self.assertIn("oak_log 5개", inventory_reply)
        self.assertIn("stone 12개", inventory_reply)

    def test_minecraft_disconnect_requires_service_result_before_success(self) -> None:
        with patch.object(
            fast_api.MINECRAFT_WORLD_LEASE_OWNER,
            "disconnect",
            new=AsyncMock(return_value={"running": False}),
        ) as disconnect:
            reply = asyncio.run(fast_api.execute_minecraft_control_command("disconnect"))

        disconnect.assert_awaited_once_with(0)
        self.assertEqual(reply, "마인크래프트 에이전트 연결을 중지했어.")

    def test_minecraft_http_failure_is_not_reported_as_already_stopped(self) -> None:
        with patch.object(
            fast_api.MINECRAFT_WORLD_LEASE_OWNER,
            "disconnect",
            new=AsyncMock(
                side_effect=RuntimeError("minecraft_stop_unverified")
            ),
        ):
            reply = asyncio.run(fast_api.execute_minecraft_control_command("disconnect"))

        self.assertIn("실패했어", reply)
        self.assertNotIn("이미 종료", reply)

    def test_minecraft_timeout_is_standby_only_before_lazy_start(self) -> None:
        fast_api.LOCAL_BRIDGE_STATUS.update(
            {"minecraftCommandRevision": 0, "minecraftCommandState": "idle"}
        )
        self.assertTrue(fast_api.minecraft_service_is_offline("TimeoutError()"))

        fast_api.LOCAL_BRIDGE_STATUS.update(
            {"minecraftCommandRevision": 4, "minecraftCommandState": "ready"}
        )
        self.assertFalse(fast_api.minecraft_service_is_offline("TimeoutError()"))

    def test_fast_control_minecraft_start_grants_central_lease(self) -> None:
        connect = AsyncMock(
            return_value={
                "connected": True,
                "outcome_verified": True,
                "outcome_code": "minecraft_connected",
            }
        )
        with patch.object(
            fast_api.MINECRAFT_WORLD_LEASE_OWNER,
            "connect",
            new=connect,
        ):
            reply = asyncio.run(
                fast_api.resolve_pre_llm_reply(
                    "마인크래프트 시작해",
                    source="control_page",
                )
            )

        self.assertIn("lease를 발급했고", reply)
        connect.assert_awaited_once_with(
            0,
            issuer_ref="fast_control:control_page",
            source="control_page",
        )
        fast_api.register_builtin_background_action_handlers()
        self.assertEqual(fast_api.BACKGROUND_ACTION_HANDLERS, [])

        with self.assertRaisesRegex(
            fast_api.FastActionExecutionError,
            "minecraft_world_authorization_required",
        ):
            asyncio.run(
                fast_api.execute_local_bridge_minecraft_command(
                    "마인크래프트 시작해",
                    "control_page",
                )
            )

    def test_fast_control_goal_uses_existing_local_lease(self) -> None:
        set_goal = AsyncMock(
            return_value={
                "goal": "diamond",
                "outcome_verified": True,
                "outcome_code": "minecraft_goal_confirmed",
            }
        )
        with (
            patch.object(
                fast_api.MINECRAFT_WORLD_LEASE_OWNER,
                "status",
                return_value={
                    "active": True,
                    "lease": {"guildId": 0},
                },
            ),
            patch.object(
                fast_api.MINECRAFT_WORLD_LEASE_OWNER,
                "set_goal",
                new=set_goal,
            ),
        ):
            reply = asyncio.run(
                fast_api.resolve_pre_llm_reply(
                    "/minecraft goal diamond",
                    source="control_page",
                )
            )

        self.assertIn("목표 변경을 실제 runtime 응답으로 확인", reply)
        set_goal.assert_awaited_once_with(0, "diamond")

    def test_fast_control_cannot_replace_another_owner(self) -> None:
        with patch.object(
            fast_api.MINECRAFT_WORLD_LEASE_OWNER,
            "connect",
            new=AsyncMock(
                side_effect=RuntimeError(
                    "minecraft_world_lease_owner_mismatch"
                )
            ),
        ):
            reply = asyncio.run(
                fast_api.resolve_pre_llm_reply(
                    "마인크래프트 시작해",
                    source="control_page",
                )
            )

        self.assertIn("다른 대화 공간", reply)

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

    def test_minecraft_command_request_waits_for_real_bridge_completion(self) -> None:
        async def scenario():
            fast_api.LOCAL_BRIDGE_STATUS.update(
                {
                    "enabled": True,
                    "ready": True,
                    "updatedAt": fast_api.time.time(),
                }
            )
            request = fast_api.request_local_bridge_minecraft_command(
                "마인크래프트에서 나무 캐줘",
                source="local_bridge",
            )

            async def acknowledge() -> None:
                await asyncio.sleep(0)
                fast_api.LOCAL_BRIDGE_STATUS.update(
                    {
                        "minecraftCommandRevision": request["revision"],
                        "minecraftCommandState": "ready",
                        "minecraftCommandError": "",
                        "minecraftCommandResult": {
                            "commandApplied": True,
                            "connected": True,
                        },
                        "updatedAt": fast_api.time.time(),
                    }
                )

            outcome, _ = await asyncio.gather(
                fast_api.wait_for_local_bridge_minecraft_command(
                    request,
                    timeout_sec=1,
                ),
                acknowledge(),
            )
            return request, outcome

        request, outcome = asyncio.run(scenario())

        self.assertEqual(request["action"], "goal")
        self.assertTrue(outcome["applied"])
        self.assertTrue(outcome["result"]["commandApplied"])
        self.assertTrue(outcome["result"]["connected"])

    def test_minecraft_command_request_cannot_be_overwritten_while_pending(self) -> None:
        first = fast_api.request_local_bridge_minecraft_command(
            "마인크래프트 시작해",
            source="control_page",
        )

        with self.assertRaisesRegex(RuntimeError, "minecraft_command_already_pending"):
            fast_api.request_local_bridge_minecraft_command(
                "마인크래프트에서 나무 캐줘",
                source="control_page",
            )

        fast_api.clear_local_bridge_minecraft_command_request(first["revision"])
        second = fast_api.request_local_bridge_minecraft_command(
            "마인크래프트에서 나무 캐줘",
            source="control_page",
        )
        self.assertGreater(second["revision"], first["revision"])

    def test_terminal_minecraft_ack_clears_request_to_prevent_replay(self) -> None:
        command = fast_api.request_local_bridge_minecraft_command(
            "마인크래프트 시작해",
            source="control_page",
        )

        class _Request:
            method = "POST"

            async def json(self):
                return {
                    "enabled": True,
                    "ready": True,
                    "minecraftCommandRevision": command["revision"],
                    "minecraftCommandState": "ready",
                    "minecraftCommandResult": {
                        "commandApplied": True,
                        "connected": False,
                    },
                }

        asyncio.run(fast_api.local_bridge_status_handler(_Request()))

        self.assertEqual(fast_api.LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST["command"], "")
        self.assertEqual(fast_api.LOCAL_BRIDGE_MINECRAFT_COMMAND_REQUEST["action"], "")

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

    def test_main_llm_speech_queue_never_queues_unbacked_progress_claim(self) -> None:
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
            yield "확인해볼게. "
            yield "마이크 입력은 꺼져 있어."

        fast_api.iter_main_llm_deltas = fake_iter_main_llm_deltas
        try:
            reply, queued_count = asyncio.run(
                fast_api.ask_main_llm_and_queue_speech("hello", source="control_page")
            )
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        queued = fast_api.drain_local_bridge_speak_requests()

        self.assertEqual(reply, "마이크 입력은 꺼져 있어.")
        self.assertEqual(queued_count, 1)
        self.assertEqual([item["text"] for item in queued], ["마이크 입력은 꺼져 있어."])

    def test_fast_main_prompt_keeps_evelyn_persona_contract(self) -> None:
        self.assertIn("너는 Evelyn", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("한국어로 친구처럼 짧게 반말", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("비서/상담원 말투", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("generic remote text-only chatbot", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("내부 제어 태그를 출력하지 않는다", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertNotIn("맨 앞에 [찾기]", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("반드시 한국어 반말", fast_api.FAST_MAIN_LLM_USER_PREFIX)
        self.assertIn("무엇을 도와드릴까요", fast_api.FAST_MAIN_LLM_USER_PREFIX)
        self.assertIn(fast_api.FAST_MAIN_LLM_USER_PREFIX, fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("active_action_id", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)
        self.assertIn("확인해볼게", fast_api.FAST_MAIN_LLM_SYSTEM_PROMPT)

    def test_local_voice_nonstream_missing_token_has_no_turn_side_effects(
        self,
    ) -> None:
        class _Request:
            async def json(self):
                return {
                    "text": "주변 대화가 잘못 들어온 문장",
                    "source": "local_bridge",
                    "bridgeInstanceId": "test-fast-api-tools-bridge",
                    "turnId": "missing-token-turn",
                }

        before_actions = fast_api.ACTION_COORDINATOR.snapshot()
        with patch.object(
            fast_api,
            "reset_fast_memory_context_receipt",
        ) as reset_receipt, patch.object(
            fast_api,
            "append_chat_message",
        ) as append_message, patch.object(
            fast_api,
            "execute_explicit_memory_confirmation",
        ) as memory_write, patch.object(
            fast_api,
            "plan_fast_tool_request_for_turn",
            new=AsyncMock(),
        ) as planner, patch.object(
            fast_api,
            "resolve_pre_llm_reply",
            new=AsyncMock(),
        ) as pre_llm, patch.object(
            fast_api,
            "ask_main_llm",
            new=AsyncMock(),
        ) as main_llm, patch.object(
            fast_api,
            "commit_fast_control_turn",
        ) as continuity, patch.object(
            fast_api,
            "queue_local_bridge_speech",
        ) as queue_speech:
            response = asyncio.run(fast_api.chat_handler(_Request()))

        payload = fast_api.json.loads(response.text or "{}")
        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"], "local_voice_wake_required")
        self.assertEqual(payload["reason"], "admission_token_missing")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(fast_api.CHAT_MESSAGES, [])
        self.assertEqual(fast_api.ACTION_COORDINATOR.snapshot(), before_actions)
        reset_receipt.assert_not_called()
        append_message.assert_not_called()
        memory_write.assert_not_called()
        planner.assert_not_awaited()
        pre_llm.assert_not_awaited()
        main_llm.assert_not_awaited()
        continuity.assert_not_called()
        queue_speech.assert_not_called()

    def test_natural_mic_status_executes_before_main_llm(self) -> None:
        request_payload = self.admitted_local_payload(
            "마이크 입력이 되고 있어?"
        )

        class _Request:
            async def json(self):
                return dict(request_payload)

        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {"mainReady": True, "routerReady": True, "ttsReady": True, "sttReady": True},
                "services": [],
            }

        async def forbidden_main_llm(*args, **kwargs):
            raise AssertionError("main LLM must not run for a local mic status request")

        fast_api.LOCAL_BRIDGE_STATUS.update(
            {
                "enabled": True,
                "ready": True,
                "micEnabled": False,
                "mic": {"enabled": False},
                "lastError": "",
                "updatedAt": fast_api.time.time(),
            }
        )
        original_collect = fast_api.collect_runtime_health
        original_ask = fast_api.ask_main_llm
        fast_api.collect_runtime_health = fake_collect_runtime_health
        fast_api.ask_main_llm = forbidden_main_llm
        try:
            response = asyncio.run(fast_api.chat_handler(_Request()))
        finally:
            fast_api.collect_runtime_health = original_collect
            fast_api.ask_main_llm = original_ask

        payload = fast_api.json.loads(response.text or "{}")
        self.assertEqual(payload["reply"], "마이크 입력은 꺼져 있어.")
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["text"], "마이크 입력은 꺼져 있어.")

    def test_mic_enable_waits_for_bridge_ack_and_capture_ready(self) -> None:
        async def scenario() -> str:
            control = asyncio.create_task(
                fast_api.execute_local_bridge_mic_control(True, source="unit")
            )
            await asyncio.sleep(0)
            revision = int(fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["revision"])
            fast_api.LOCAL_BRIDGE_STATUS.update(
                {
                    "enabled": True,
                    "ready": True,
                    "micEnabled": True,
                    "micControlRevision": revision,
                    "mic": {"enabled": True, "captureReady": True},
                    "lastError": "",
                    "updatedAt": fast_api.time.time(),
                }
            )
            return await control

        reply = asyncio.run(scenario())

        self.assertEqual(reply, "마이크 입력을 켰어.")
        self.assertTrue(fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["enabled"])
        self.assertEqual(fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["source"], "unit")

    def test_mic_disable_reports_bridge_stop_failure_instead_of_false_success(self) -> None:
        async def scenario() -> str:
            control = asyncio.create_task(
                fast_api.execute_local_bridge_mic_control(False, source="unit")
            )
            await asyncio.sleep(0)
            revision = int(fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["revision"])
            fast_api.LOCAL_BRIDGE_STATUS.update(
                {
                    "enabled": True,
                    "ready": True,
                    "micEnabled": False,
                    "micControlRevision": revision,
                    "mic": {"enabled": False},
                    "lastError": "mic_control_failed: RuntimeError('stop failed')",
                    "updatedAt": fast_api.time.time(),
                }
            )
            return await control

        reply = asyncio.run(scenario())

        self.assertIn("캡처 종료 중 오류", reply)
        self.assertNotEqual(reply, "마이크 입력을 껐어.")

    def test_mic_command_runs_before_main_llm_and_uses_actual_ack(self) -> None:
        request_payload = self.admitted_local_payload("/mic on")

        class _Request:
            async def json(self):
                return dict(request_payload)

        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {"mainReady": True, "routerReady": True, "ttsReady": True, "sttReady": True},
                "services": [],
            }

        async def forbidden_main_llm(*args, **kwargs):
            raise AssertionError("main LLM must not run for a microphone control command")

        async def scenario():
            original_collect = fast_api.collect_runtime_health
            original_ask = fast_api.ask_main_llm
            fast_api.collect_runtime_health = fake_collect_runtime_health
            fast_api.ask_main_llm = forbidden_main_llm
            try:
                request_task = asyncio.create_task(fast_api.chat_handler(_Request()))
                await asyncio.sleep(0)
                revision = int(fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST["revision"])
                fast_api.LOCAL_BRIDGE_STATUS.update(
                    {
                        "enabled": True,
                        "ready": True,
                        "micEnabled": True,
                        "micControlRevision": revision,
                        "mic": {"enabled": True, "captureReady": True},
                        "lastError": "",
                        "updatedAt": fast_api.time.time(),
                    }
                )
                return await request_task
            finally:
                fast_api.collect_runtime_health = original_collect
                fast_api.ask_main_llm = original_ask

        response = asyncio.run(scenario())
        payload = fast_api.json.loads(response.text or "{}")

        self.assertEqual(payload["reply"], "마이크 입력을 켰어.")
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["text"], "마이크 입력을 켰어.")

    def test_chat_handler_blocks_future_claim_without_task_id(self) -> None:
        request_payload = self.admitted_local_payload("설정 확인해줘")

        class _Request:
            async def json(self):
                return dict(request_payload)

        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {"mainReady": True, "routerReady": True, "ttsReady": True, "sttReady": True},
                "services": [],
            }

        async def fake_main_llm(*args, **kwargs):
            return "확인해볼게. 잠시만 기다려줘."

        original_collect = fast_api.collect_runtime_health
        original_ask = fast_api.ask_main_llm
        fast_api.collect_runtime_health = fake_collect_runtime_health
        fast_api.ask_main_llm = fake_main_llm
        try:
            response = asyncio.run(fast_api.chat_handler(_Request()))
        finally:
            fast_api.collect_runtime_health = original_collect
            fast_api.ask_main_llm = original_ask

        payload = fast_api.json.loads(response.text or "{}")
        self.assertIn("실제 작업이 시작되지 않았어", payload["reply"])
        self.assertNotIn("확인해볼게", payload["reply"])

    def test_chat_handler_failure_uses_fixed_public_error(self) -> None:
        class _Request:
            async def json(self):
                return {
                    "text": "실패 테스트",
                    "source": "control_page",
                }

        async def fake_collect_runtime_health(
            *,
            manifest,
            probe_runner,
        ):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {},
                "services": [],
            }

        async def fail_main_llm(*args, **kwargs):
            raise RuntimeError(
                "Bearer api-secret http://internal:9820 C:\\private"
            )

        class ContinuityOwner:
            enabled = True

            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []
                self.receipts: list[dict[str, object]] = []

            def record_completed_turn(
                self,
                user_text: str,
                assistant_text: str,
                *,
                memory_receipt=None,
            ):
                self.calls.append(
                    (user_text, assistant_text)
                )
                self.receipts.append(memory_receipt)
                return durable_continuity_status(7)

            @staticmethod
            def status():
                return {
                    "schema": (
                        "fast_control.continuity-status.v1"
                    ),
                    "enabled": True,
                    "state": "ready",
                    "policy": {"contentFree": True},
                }

        owner = ContinuityOwner()
        original_collect = fast_api.collect_runtime_health
        original_ask = fast_api.ask_main_llm
        fast_api.collect_runtime_health = fake_collect_runtime_health
        fast_api.ask_main_llm = fail_main_llm
        try:
            with patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ):
                response = asyncio.run(
                    fast_api.chat_handler(_Request())
                )
        finally:
            fast_api.collect_runtime_health = original_collect
            fast_api.ask_main_llm = original_ask

        payload = fast_api.json.loads(response.text or "{}")
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"],
            "fast_control_chat_failed",
        )
        self.assertIn(
            "fast_control_chat_failed",
            payload["reply"],
        )
        public_text = fast_api.json.dumps(
            payload,
            ensure_ascii=False,
        )
        self.assertNotIn("api-secret", public_text)
        self.assertNotIn("internal:9820", public_text)
        self.assertNotIn("C:\\\\private", public_text)
        self.assertTrue(payload["continuity"]["durable"])
        self.assertEqual(
            payload["continuity"]["generation"],
            7,
        )
        self.assertEqual(
            owner.calls,
            [("실패 테스트", payload["reply"])],
        )
        self.assertEqual(owner.receipts[0]["state"], "not_used")

    def test_chat_handler_planner_failure_uses_same_durable_boundary(
        self,
    ) -> None:
        class _Request:
            async def json(self):
                return {
                    "text": "계획 실패 테스트",
                    "source": "control_page",
                }

        private = (
            "Bearer planner-secret "
            r"C:\Users\Admin\planner.json"
        )
        continuity = {
            "schema": "fast_control.delivery-continuity.v1",
            "enabled": True,
            "durable": True,
            "generation": 13,
            "persistedSessionCount": 1,
            "error": "",
        }
        with patch.object(
            fast_api,
            "plan_fast_tool_request_for_turn",
            new=AsyncMock(
                side_effect=RuntimeError(private)
            ),
        ), patch.object(
            fast_api,
            "commit_fast_control_turn",
            return_value=continuity,
        ) as commit_turn, patch.object(
            fast_api,
            "cached_fast_runtime_health",
            new=AsyncMock(return_value={
                "ok": True,
                "overallState": "up",
                "legacyServices": {},
                "services": [],
            }),
        ):
            response = asyncio.run(
                fast_api.chat_handler(_Request())
            )

        payload = fast_api.json.loads(response.text or "{}")
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"],
            "fast_control_chat_failed",
        )
        self.assertEqual(payload["continuity"], continuity)
        commit_turn.assert_called_once()
        turn_args = commit_turn.call_args
        self.assertEqual(
            turn_args.args,
            ("계획 실패 테스트", payload["reply"]),
        )
        self.assertEqual(
            turn_args.kwargs["memory_receipt"]["state"],
            "not_used",
        )
        self.assertNotIn(private, str(payload))

    def test_fast_control_rejects_partial_continuity_status(
        self,
    ) -> None:
        private = (
            "Bearer fast-control-continuity-secret "
            r"C:\Users\Admin\checkpoint.json"
        )

        class PartialOwner:
            enabled = True

            @staticmethod
            def record_completed_turn(
                _user_text: str,
                _assistant_text: str,
            ):
                return {
                    "state": "ready",
                    "rollbackProtected": True,
                    "privateMessage": private,
                }

        output = StringIO()
        with patch.object(
            fast_api,
            "FAST_CONTROL_CONTINUITY_OWNER",
            PartialOwner(),
        ), redirect_stdout(output):
            result = fast_api.commit_fast_control_turn(
                "질문",
                "답변",
            )

        rendered = fast_api.json.dumps(
            result,
            ensure_ascii=False,
        )
        self.assertFalse(result["durable"])
        self.assertEqual(
            result["error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn(
            "fast-control-continuity-secret",
            rendered + output.getvalue(),
        )
        self.assertNotIn(
            "checkpoint.json",
            rendered + output.getvalue(),
        )

    def test_registered_background_action_adds_followup_chat_and_events(self) -> None:
        async def runner(user_text: str, source: str) -> str:
            await asyncio.sleep(0)
            return f"완료: {user_text} ({source})"

        async def scenario():
            fast_api.register_background_action_handler(
                kind="unit",
                matcher=lambda text: text == "긴 작업",
                runner=runner,
                start_reply="긴 작업을 시작할게.",
            )
            prepared = fast_api.prepare_registered_background_action("긴 작업", source="control_page")
            self.assertIsNotNone(prepared)
            task, task_runner = prepared
            fast_api.append_chat_message(
                "assistant",
                "Evelyn",
                task.start_reply,
                source="unit",
                task_id=task.task_id,
                task_status=task.status,
            )
            background = fast_api.launch_background_action(task, task_runner)
            await background
            return task

        with patch.object(
            fast_api,
            "commit_fast_control_action_followup",
        ) as commit_followup:
            task = asyncio.run(scenario())
        snapshot = fast_api.ACTION_COORDINATOR.snapshot()

        self.assertEqual(snapshot["tasks"][0]["status"], "completed")
        self.assertEqual([event["type"] for event in snapshot["events"]], ["started", "completed"])
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["taskId"], task.task_id)
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["taskStatus"], "completed")
        self.assertIn("완료: 긴 작업", fast_api.CHAT_MESSAGES[-1]["text"])
        commit_followup.assert_called_once()
        followup_args = commit_followup.call_args
        self.assertEqual(
            followup_args.args,
            (
                task.task_id,
                fast_api.CHAT_MESSAGES[-1]["text"],
            ),
        )
        self.assertEqual(
            followup_args.kwargs["memory_receipt"]["state"],
            "not_used",
        )

    def test_background_action_failure_publishes_specific_followup_reply(self) -> None:
        async def runner(user_text: str, source: str) -> str:
            raise fast_api.FastActionExecutionError(
                "local_bridge_not_ready",
                reply="로컬 브리지가 준비되지 않아서 마인크래프트 서비스를 자동으로 시작하지 못했어.",
            )

        async def scenario():
            fast_api.register_background_action_handler(
                kind="minecraft_lazy_start",
                matcher=lambda text: True,
                runner=runner,
                start_reply="마인크래프트 쪽을 준비할게.",
            )
            prepared = fast_api.prepare_registered_background_action(
                "마인크래프트 시작해",
                source="control_page",
            )
            self.assertIsNotNone(prepared)
            task, task_runner = prepared
            await fast_api.launch_background_action(task, task_runner)
            return task

        task = asyncio.run(scenario())

        self.assertEqual(task.status, "failed")
        self.assertEqual(task.error, "local_bridge_not_ready")
        self.assertIn("로컬 브리지가 준비되지 않아서", task.final_reply)
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["taskStatus"], "failed")

    def test_unknown_background_failure_is_redacted_from_snapshot(self) -> None:
        async def runner(user_text: str, source: str) -> str:
            raise RuntimeError(
                "Bearer task-secret http://internal:9820 C:\\private"
            )

        async def scenario():
            fast_api.register_background_action_handler(
                kind="unit",
                matcher=lambda text: True,
                runner=runner,
                start_reply="작업을 시작할게.",
            )
            prepared = fast_api.prepare_registered_background_action(
                "실패 테스트",
                source="control_page",
            )
            self.assertIsNotNone(prepared)
            task, task_runner = prepared
            await fast_api.launch_background_action(
                task,
                task_runner,
            )
            return task

        task = asyncio.run(scenario())
        snapshot_text = fast_api.json.dumps(
            fast_api.ACTION_COORDINATOR.snapshot(),
            ensure_ascii=False,
        )

        self.assertEqual(task.error, "background_action_failed")
        self.assertIn("background_action_failed", task.final_reply)
        self.assertNotIn("task-secret", snapshot_text)
        self.assertNotIn("internal:9820", snapshot_text)
        self.assertNotIn("C:\\\\private", snapshot_text)

    def test_chat_handler_returns_real_task_id_before_background_followup(self) -> None:
        request_payload = self.admitted_local_payload("긴 작업")

        class _Request:
            async def json(self):
                return dict(request_payload)

        async def runner(user_text: str, source: str) -> str:
            await asyncio.sleep(0)
            return "긴 작업을 완료했어."

        async def fake_collect_runtime_health(*, manifest, probe_runner):
            return {
                "ok": True,
                "overallState": "up",
                "legacyServices": {"mainReady": True, "routerReady": True, "ttsReady": True, "sttReady": True},
                "services": [],
            }

        async def scenario():
            fast_api.register_background_action_handler(
                kind="unit",
                matcher=lambda text: text == "긴 작업",
                runner=runner,
                start_reply="긴 작업을 시작할게.",
            )
            original_collect = fast_api.collect_runtime_health
            fast_api.collect_runtime_health = fake_collect_runtime_health
            try:
                response = await fast_api.chat_handler(_Request())
                self.assertFalse(fast_api.BACKGROUND_ACTION_TASKS)
                response._run_after_write()
                await asyncio.gather(*list(fast_api.BACKGROUND_ACTION_TASKS))
            finally:
                fast_api.collect_runtime_health = original_collect
            return fast_api.json.loads(response.text or "{}")

        payload = asyncio.run(scenario())

        self.assertEqual(payload["reply"], "긴 작업을 시작할게.")
        self.assertEqual(payload["task"]["id"], "fast-action-1")
        self.assertIn(payload["task"]["status"], {"running", "completed"})
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["text"], "긴 작업을 완료했어.")
        self.assertEqual(fast_api.CHAT_MESSAGES[-1]["taskStatus"], "completed")

    def test_action_events_handler_returns_events_after_cursor(self) -> None:
        class _Request:
            query = {"after": "1"}

        task = fast_api.ACTION_COORDINATOR.start(
            kind="unit",
            source="test",
            user_text="task",
            start_reply="started",
        )
        fast_api.ACTION_COORDINATOR.complete(task.task_id, "done")

        response = asyncio.run(fast_api.action_events_handler(_Request()))
        payload = fast_api.json.loads(response.text or "{}")

        self.assertTrue(payload["ok"])
        self.assertEqual([event["type"] for event in payload["events"]], ["completed"])
        self.assertEqual(payload["activeCount"], 0)

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
        cross_surface = state["runtime"][
            "crossSurfaceContinuity"
        ]
        self.assertEqual(
            cross_surface["schema"],
            "cross_surface_continuity.status.v1",
        )
        self.assertTrue(
            cross_surface["policy"]["contentFree"]
        )
        self.assertEqual(
            cross_surface["lastMerge"]["schema"],
            "cross_surface_continuity.merge.v1",
        )
        self.assertTrue(
            cross_surface["lastMerge"]["policy"][
                "contentFree"
            ]
        )
        serialized = fast_api.json.dumps(
            cross_surface,
            ensure_ascii=False,
        )
        self.assertNotIn("guildId", serialized)
        self.assertNotIn("userId", serialized)

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
        self.assertIn("actions", payload)
        self.assertEqual(payload["actions"]["activeCount"], 0)
        recovery = payload["actions"]["recovery"]
        self.assertEqual(
            recovery["schema"],
            fast_api.FAST_ACTION_RECOVERY_SCHEMA,
        )
        self.assertEqual(
            set(recovery["policy"]),
            {
                "contentFree",
                "rawText",
                "automaticRetry",
                "maxActions",
            },
        )
        self.assertTrue(recovery["policy"]["contentFree"])
        self.assertFalse(recovery["policy"]["rawText"])
        self.assertFalse(
            recovery["policy"]["automaticRetry"]
        )
        recovery_text = fast_api.json.dumps(
            recovery,
            ensure_ascii=False,
        )
        self.assertNotIn("사용자 질문", recovery_text)
        self.assertNotIn("최종 답변", recovery_text)
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

    def test_state_handler_reuses_snapshot_and_refreshes_in_background(
        self,
    ) -> None:
        clock = [100.0]
        calls = 0

        async def collect() -> dict:
            nonlocal calls
            calls += 1
            return {
                "ok": True,
                "fullyHealthy": True,
                "overallState": "up",
                "summary": "ready",
                "revision": calls,
                "legacyServices": {
                    "botReady": True,
                    "mainReady": True,
                    "routerReady": True,
                    "subReady": True,
                    "ttsReady": True,
                    "sttReady": True,
                },
                "services": [
                    {
                        "id": "bot_api",
                        "state": "up",
                        "ready": True,
                        "checks": [
                            {
                                "kind": "artifact_json",
                                "ok": True,
                                "reason": "ok",
                                "target": "/app/runtime_artifacts/private.json",
                                "payload": {"pid": 42},
                            }
                        ],
                    }
                ],
            }

        cache = fast_api.RuntimeHealthSnapshotCache(
            collector=collect,
            refresh_after_sec=2.0,
            max_stale_sec=6.0,
            monotonic=lambda: clock[0],
        )

        async def exercise() -> tuple[dict, dict, dict, dict]:
            first = await fast_api.state_handler(object())
            second = await fast_api.state_handler(object())
            clock[0] += 2.1
            refreshing = await fast_api.state_handler(object())
            await asyncio.sleep(0)
            refreshed = await fast_api.state_handler(object())
            return tuple(
                fast_api.json.loads(response.text or "{}")
                for response in (
                    first,
                    second,
                    refreshing,
                    refreshed,
                )
            )

        with patch.object(
            fast_api,
            "FAST_RUNTIME_HEALTH_CACHE",
            cache,
        ):
            first, second, refreshing, refreshed = asyncio.run(
                exercise()
            )

        self.assertEqual(calls, 2)
        self.assertEqual(
            first["runtime"]["serviceHealth"]["revision"],
            1,
        )
        public_health = first["runtime"]["serviceHealth"]
        self.assertEqual(
            public_health["schema"],
            "runtime_health.public.v1",
        )
        public_check = public_health["services"][0]["checks"][0]
        self.assertNotIn("target", public_check)
        self.assertNotIn("payload", public_check)
        self.assertEqual(
            second["runtime"]["serviceHealth"]["revision"],
            1,
        )
        self.assertTrue(
            refreshing["runtime"]["controlPlane"]["healthCache"][
                "refreshing"
            ]
        )
        self.assertEqual(
            refreshed["runtime"]["serviceHealth"]["revision"],
            2,
        )

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
        self.assertEqual(progress["phase"], "core services ready")
        self.assertTrue(progress["ready"])
        self.assertTrue(progress["componentsReady"])

    def test_control_state_separates_core_readiness_from_optional_health(self) -> None:
        state = fast_api.build_control_state(
            {
                "ok": True,
                "fullyHealthy": False,
                "overallState": "degraded",
                "optionalDegraded": True,
                "legacyServices": {
                    "botReady": True,
                    "mainReady": True,
                    "routerReady": True,
                    "subReady": True,
                    "ttsReady": True,
                    "sttReady": True,
                    "voyagerReady": False,
                    "voyagerHttpReady": True,
                    "voyagerRuntimeReady": False,
                },
                "services": [
                    {"id": service_id, "state": "up", "ready": True}
                    for service_id, _label in fast_api.BOOT_STEPS
                ],
                "summary": "Voyager runtime boundary needs recovery.",
            }
        )

        self.assertTrue(state["ok"])
        self.assertTrue(state["runtime"]["services"]["coreReady"])
        self.assertFalse(state["runtime"]["services"]["fullReady"])
        self.assertTrue(state["runtime"]["services"]["optionalDegraded"])
        self.assertTrue(state["runtime"]["services"]["voyagerHttpReady"])
        self.assertFalse(state["runtime"]["services"]["voyagerRuntimeReady"])


if __name__ == "__main__":
    unittest.main()
