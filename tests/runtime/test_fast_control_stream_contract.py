from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


class FastControlStreamContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._original_local_voice_admission = fast_api.LOCAL_VOICE_ADMISSION
        fast_api.LOCAL_VOICE_ADMISSION = fast_api.LocalVoiceAdmissionManager()
        self._validation_context_patcher = patch.object(
            fast_api,
            "local_voice_validation_binding_is_current",
            side_effect=lambda binding: not binding,
        )
        self._validation_context_patcher.start()
        self._voice_turn_seq = 0
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
        self._validation_context_patcher.stop()
        fast_api.LOCAL_VOICE_ADMISSION = self._original_local_voice_admission

    def admitted_local_payload(self, text: str) -> dict[str, object]:
        self._voice_turn_seq += 1
        bridge_instance_id = "test-fast-stream-bridge"
        turn_id = f"test-fast-stream-turn-{self._voice_turn_seq}"
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

    async def post_stream(self, text: str) -> list[dict[str, object]]:
        client = TestClient(TestServer(fast_api.create_app()))
        await client.start_server()
        try:
            response = await client.post(
                "/api/control-page/chat-stream",
                json=self.admitted_local_payload(text),
            )
            self.assertEqual(response.status, 200)
            body = await response.text()
        finally:
            await client.close()
        return [json.loads(line) for line in body.splitlines() if line.strip()]

    async def test_minecraft_owner_mutation_requires_shared_token(
        self,
    ) -> None:
        class Owner:
            def __init__(self) -> None:
                self.calls = []

            def delegation_token(self):
                return "owner-secret"

            def status(self):
                return {
                    "state": "authorization_required",
                    "active": False,
                    "lease": None,
                }

            async def connect(self, guild_id, **kwargs):
                self.calls.append((guild_id, kwargs))
                return {
                    "connected": True,
                    "outcome_verified": True,
                }

        owner = Owner()
        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            owner,
        ):
            client = TestClient(
                TestServer(
                    fast_api.create_app(
                        enable_minecraft_world_lease_owner=False
                    )
                )
            )
            await client.start_server()
            try:
                unauthorized = await client.post(
                    "/internal/minecraft-world-lease/connect",
                    json={
                        "guildId": 7,
                        "issuerRef": "discord_user:1",
                        "source": "discord_command",
                    },
                )
                unauthorized_payload = await unauthorized.json()
                authorized = await client.post(
                    "/internal/minecraft-world-lease/connect",
                    headers={
                        fast_api.MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER:
                        "owner-secret"
                    },
                    json={
                        "guildId": 7,
                        "issuerRef": "discord_user:1",
                        "source": "discord_command",
                    },
                )
                authorized_payload = await authorized.json()
            finally:
                await client.close()

        self.assertEqual(unauthorized.status, 401)
        self.assertNotIn("leaseStatus", unauthorized_payload)
        self.assertEqual(authorized.status, 200)
        self.assertTrue(authorized_payload["ok"])
        self.assertNotIn(
            "owner-secret",
            json.dumps(authorized_payload),
        )
        self.assertEqual(len(owner.calls), 1)

    async def test_minecraft_owner_mutation_error_returns_lease_status(
        self,
    ) -> None:
        class Owner:
            @staticmethod
            def delegation_token():
                return "owner-secret"

            @staticmethod
            def status():
                return {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": False,
                    "statusReady": True,
                    "lease": None,
                    "lastErrorCode": (
                        "minecraft_world_lease_audit_unavailable"
                    ),
                }

            @staticmethod
            async def connect(_guild_id, **_kwargs):
                raise RuntimeError(
                    "minecraft_world_lease_audit_unavailable"
                )

        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            Owner(),
        ):
            client = TestClient(
                TestServer(
                    fast_api.create_app(
                        enable_minecraft_world_lease_owner=False
                    )
                )
            )
            await client.start_server()
            try:
                response = await client.post(
                    "/internal/minecraft-world-lease/connect",
                    headers={
                        fast_api.MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER:
                        "owner-secret"
                    },
                    json={
                        "guildId": 7,
                        "issuerRef": "discord_user:1",
                        "source": "discord_command",
                    },
                )
                payload = await response.json()
            finally:
                await client.close()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "minecraft_world_lease_audit_unavailable",
        )
        self.assertEqual(
            payload["leaseStatus"]["state"],
            "manual_intervention_required",
        )
        self.assertFalse(payload["leaseStatus"]["active"])
        self.assertNotIn("owner-secret", json.dumps(payload))

    async def test_minecraft_owner_status_write_failure_returns_503(
        self,
    ) -> None:
        class Owner:
            @staticmethod
            def delegation_token():
                return "owner-secret"

            @staticmethod
            def status():
                return {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": True,
                    "statusReady": False,
                    "lease": None,
                    "lastErrorCode": (
                        "minecraft_world_lease_status_write_failed"
                    ),
                }

            @staticmethod
            async def connect(_guild_id, **_kwargs):
                raise RuntimeError(
                    "minecraft_world_lease_status_write_failed"
                )

        owner = Owner()
        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            owner,
        ):
            redacted = fast_api.minecraft_world_lease_error_payload(
                RuntimeError("private C:\\path token=secret")
            )
            client = TestClient(
                TestServer(
                    fast_api.create_app(
                        enable_minecraft_world_lease_owner=False
                    )
                )
            )
            await client.start_server()
            try:
                response = await client.post(
                    "/internal/minecraft-world-lease/connect",
                    headers={
                        fast_api.MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER:
                        "owner-secret"
                    },
                    json={
                        "guildId": 7,
                        "issuerRef": "discord_user:1",
                        "source": "discord_command",
                    },
                )
                payload = await response.json()
            finally:
                await client.close()

        self.assertEqual(
            redacted["error"],
            "minecraft_world_lease_delegation_failed",
        )
        self.assertNotIn("private", json.dumps(redacted))
        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "minecraft_world_lease_status_write_failed",
        )
        self.assertFalse(payload["leaseStatus"]["statusReady"])

    async def test_minecraft_owner_lock_failure_returns_503(self) -> None:
        class Owner:
            @staticmethod
            def delegation_token():
                return "owner-secret"

            @staticmethod
            def status():
                return {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": True,
                    "statusReady": True,
                    "ownerClaimOwned": False,
                    "ownerLockHeld": False,
                    "lease": None,
                    "lastErrorCode": (
                        "minecraft_world_lease_owner_lock_unavailable"
                    ),
                }

            @staticmethod
            async def connect(_guild_id, **_kwargs):
                raise RuntimeError(
                    "minecraft_world_lease_owner_lock_unavailable"
                )

        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            Owner(),
        ):
            client = TestClient(
                TestServer(
                    fast_api.create_app(
                        enable_minecraft_world_lease_owner=False
                    )
                )
            )
            await client.start_server()
            try:
                response = await client.post(
                    "/internal/minecraft-world-lease/connect",
                    headers={
                        fast_api.MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER:
                        "owner-secret"
                    },
                    json={
                        "guildId": 7,
                        "issuerRef": "discord_user:1",
                        "source": "discord_command",
                    },
                )
                payload = await response.json()
            finally:
                await client.close()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload["error"],
            "minecraft_world_lease_owner_lock_unavailable",
        )
        self.assertFalse(payload["leaseStatus"]["ownerLockHeld"])

    async def test_minecraft_owner_infrastructure_failures_return_503(
        self,
    ) -> None:
        class Owner:
            def __init__(self, error: str) -> None:
                self.error = error

            @staticmethod
            def delegation_token():
                return "owner-secret"

            def status(self):
                return {
                    "schema": "minecraft_world_lease.status.v1",
                    "state": "manual_intervention_required",
                    "active": False,
                    "auditReady": True,
                    "statusReady": True,
                    "ownerClaimOwned": True,
                    "ownerLockHeld": True,
                    "lease": None,
                    "lastErrorCode": self.error,
                }

            async def connect(self, _guild_id, **_kwargs):
                raise RuntimeError(self.error)

        for error in (
            "minecraft_world_action_lock_busy",
            "minecraft_world_action_lock_unavailable",
            "minecraft_world_lease_owner_claim_failed",
            "minecraft_world_lease_owner_claim_write_failed",
        ):
            with self.subTest(error=error), patch.object(
                fast_api,
                "MINECRAFT_WORLD_LEASE_OWNER",
                Owner(error),
            ):
                client = TestClient(
                    TestServer(
                        fast_api.create_app(
                            enable_minecraft_world_lease_owner=False
                        )
                    )
                )
                await client.start_server()
                try:
                    response = await client.post(
                        "/internal/minecraft-world-lease/connect",
                        headers={
                            fast_api.MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER:
                            "owner-secret"
                        },
                        json={
                            "guildId": 7,
                            "issuerRef": "discord_user:1",
                            "source": "discord_command",
                        },
                    )
                    payload = await response.json()
                finally:
                    await client.close()

                self.assertEqual(response.status, 503)
                self.assertEqual(payload["error"], error)
                self.assertEqual(
                    payload["leaseStatus"]["lastErrorCode"],
                    error,
                )

    async def test_minecraft_owner_startup_failure_always_shuts_down(
        self,
    ) -> None:
        class Owner:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def initialize(self) -> None:
                self.calls.append("initialize")

            async def ensure_started(self) -> None:
                self.calls.append("ensure_started")
                raise RuntimeError("startup failed")

            async def shutdown(self, *, reason: str) -> None:
                self.calls.append(f"shutdown:{reason}")

        owner = Owner()
        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            owner,
        ):
            context = fast_api.minecraft_world_lease_owner_context(None)
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                await anext(context)

        self.assertEqual(
            owner.calls,
            ["initialize", "ensure_started", "shutdown:shutdown"],
        )

    async def test_minecraft_owner_initialize_failure_always_shuts_down(
        self,
    ) -> None:
        class Owner:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def initialize(self) -> None:
                self.calls.append("initialize")
                raise RuntimeError("initialize failed")

            async def ensure_started(self) -> None:
                self.calls.append("ensure_started")

            async def shutdown(self, *, reason: str) -> None:
                self.calls.append(f"shutdown:{reason}")

        owner = Owner()
        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            owner,
        ):
            context = fast_api.minecraft_world_lease_owner_context(None)
            with self.assertRaisesRegex(RuntimeError, "initialize failed"):
                await anext(context)

        self.assertEqual(
            owner.calls,
            ["initialize", "shutdown:shutdown"],
        )

    async def test_cancelled_minecraft_owner_startup_shields_shutdown(
        self,
    ) -> None:
        ensure_started = asyncio.Event()
        shutdown_started = asyncio.Event()
        allow_shutdown = asyncio.Event()
        shutdown_completed = asyncio.Event()

        class Owner:
            @staticmethod
            def initialize() -> None:
                return None

            @staticmethod
            async def ensure_started() -> None:
                ensure_started.set()
                await asyncio.Event().wait()

            @staticmethod
            async def shutdown(*, reason: str) -> None:
                if reason != "shutdown":
                    raise AssertionError(reason)
                shutdown_started.set()
                await allow_shutdown.wait()
                shutdown_completed.set()

        with patch.object(
            fast_api,
            "MINECRAFT_WORLD_LEASE_OWNER",
            Owner(),
        ):
            context = fast_api.minecraft_world_lease_owner_context(None)
            startup_task = asyncio.create_task(anext(context))
            await asyncio.wait_for(ensure_started.wait(), timeout=1.0)
            startup_task.cancel()
            await asyncio.wait_for(shutdown_started.wait(), timeout=1.0)
            self.assertFalse(startup_task.done())
            startup_task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(startup_task.done())
            allow_shutdown.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(startup_task, timeout=1.0)

        self.assertTrue(shutdown_completed.is_set())

    async def test_local_voice_stream_missing_token_has_no_turn_side_effects(
        self,
    ) -> None:
        client = TestClient(TestServer(fast_api.create_app()))
        await client.start_server()
        try:
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
                "iter_main_llm_deltas",
            ) as llm_stream, patch.object(
                fast_api,
                "commit_fast_control_turn",
            ) as continuity, patch.object(
                fast_api,
                "queue_local_bridge_speech",
            ) as queue_speech:
                response = await client.post(
                    "/api/control-page/chat-stream",
                    json={
                        "text": "주변 대화가 잘못 들어온 문장",
                        "source": "local_bridge",
                        "bridgeInstanceId": "test-fast-stream-bridge",
                        "turnId": "missing-token-turn",
                    },
                )
                payload = await response.json()
        finally:
            await client.close()

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
        llm_stream.assert_not_called()
        continuity.assert_not_called()
        queue_speech.assert_not_called()

    async def test_local_voice_stream_token_cannot_be_reused(self) -> None:
        request_payload = self.admitted_local_payload("한 번만 처리해줘")
        llm_calls = 0

        async def fake_iter(_text: str, *, source: str):
            nonlocal llm_calls
            self.assertEqual(source, "local_bridge")
            llm_calls += 1
            yield "한 번만 답했어."

        client = TestClient(TestServer(fast_api.create_app()))
        await client.start_server()
        try:
            with patch.object(
                fast_api,
                "execute_explicit_memory_confirmation",
                return_value=(False, "", None, ""),
            ), patch.object(
                fast_api,
                "plan_fast_tool_request_for_turn",
                new=AsyncMock(return_value=None),
            ), patch.object(
                fast_api,
                "resolve_pre_llm_reply",
                new=AsyncMock(return_value=None),
            ), patch.object(
                fast_api,
                "iter_main_llm_deltas",
                new=fake_iter,
            ):
                first = await client.post(
                    "/api/control-page/chat-stream",
                    json=request_payload,
                )
                first_body = await first.text()
                history_after_first = list(fast_api.CHAT_MESSAGES)
                second = await client.post(
                    "/api/control-page/chat-stream",
                    json=request_payload,
                )
                second_payload = await second.json()
        finally:
            await client.close()

        self.assertEqual(first.status, 200, first_body)
        self.assertEqual(second.status, 409)
        self.assertEqual(second_payload["reason"], "admission_token_reused")
        self.assertEqual(llm_calls, 1)
        self.assertEqual(fast_api.CHAT_MESSAGES, history_after_first)

    async def test_minecraft_owner_api_rejects_browser_origin(
        self,
    ) -> None:
        client = TestClient(
            TestServer(
                fast_api.create_app(
                    enable_minecraft_world_lease_owner=False
                )
            )
        )
        await client.start_server()
        try:
            response = await client.post(
                "/internal/minecraft-world-lease/connect",
                headers={
                    "Origin": "http://127.0.0.1:8799",
                    fast_api.MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER:
                    "any-value",
                },
                json={
                    "guildId": 0,
                    "issuerRef": "browser",
                    "source": "control_page",
                },
            )
            payload = await response.json()
        finally:
            await client.close()

        self.assertEqual(response.status, 403)
        self.assertEqual(
            payload["error"],
            "browser_origin_not_allowed",
        )

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

    async def test_stream_failure_emits_only_fixed_error_code(self) -> None:
        original_iter = fast_api.iter_main_llm_deltas
        recorded: list[tuple[str, str]] = []

        class ContinuityOwner:
            enabled = True

            @staticmethod
            def record_completed_turn(
                user_text: str,
                assistant_text: str,
                *,
                memory_receipt=None,
            ):
                recorded.append(
                    (user_text, assistant_text)
                )
                return durable_continuity_status(11)

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

        async def fail_iter(text: str, *, source: str):
            if False:
                yield ""
            raise RuntimeError(
                "Bearer stream-secret http://internal:9820 C:\\private"
            )

        fast_api.iter_main_llm_deltas = fail_iter
        try:
            with patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                ContinuityOwner(),
            ):
                events = await self.post_stream("실패 테스트")
        finally:
            fast_api.iter_main_llm_deltas = original_iter

        error = next(
            event
            for event in events
            if event["type"] == "error"
        )
        self.assertEqual(
            error["error"],
            "fast_control_stream_failed",
        )
        self.assertIn(
            "fast_control_stream_failed",
            error["message"],
        )
        self.assertTrue(error["continuity"]["durable"])
        self.assertEqual(
            error["continuity"]["generation"],
            11,
        )
        self.assertEqual(
            recorded,
            [("실패 테스트", error["message"])],
        )
        self.assertEqual(
            fast_api.CHAT_MESSAGES[-1]["text"],
            error["message"],
        )
        public_text = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("stream-secret", public_text)
        self.assertNotIn("internal:9820", public_text)
        self.assertNotIn("C:\\\\private", public_text)

    async def test_nonstream_integrity_failure_is_exact_no_store_503(
        self,
    ) -> None:
        async def fail_main_llm(*_args, **_kwargs):
            raise MemoryDeletionJournalIntegrityError(
                "PRIVATE_MUST_NOT_SURVIVE"
            )

        client = TestClient(TestServer(fast_api.create_app()))
        await client.start_server()
        try:
            with patch.object(
                fast_api,
                "plan_fast_tool_request_for_turn",
                new=AsyncMock(return_value=None),
            ), patch.object(
                fast_api,
                "resolve_pre_llm_reply",
                new=AsyncMock(return_value=None),
            ), patch.object(
                fast_api,
                "ask_main_llm",
                new=fail_main_llm,
            ), patch.object(
                fast_api,
                "should_queue_local_bridge_speech",
                return_value=False,
            ):
                response = await client.post(
                    "/api/control-page/chat",
                    json={
                        "text": "경계 일반 응답 테스트",
                        "source": "control_page",
                    },
                )
                payload = await response.json()
        finally:
            await client.close()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": (
                    "memory_deletion_journal_integrity_failed"
                ),
            },
        )
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn("PRIVATE_MUST_NOT_SURVIVE", str(payload))

    async def test_stream_integrity_failure_precedes_http_200_admission(
        self,
    ) -> None:
        async def fail_before_first_delta(*_args, **_kwargs):
            if False:
                yield ""
            raise MemoryDeletionJournalIntegrityError(
                "PRIVATE_STREAM_MUST_NOT_SURVIVE"
            )

        client = TestClient(TestServer(fast_api.create_app()))
        await client.start_server()
        try:
            with patch.object(
                fast_api,
                "plan_fast_tool_request_for_turn",
                new=AsyncMock(return_value=None),
            ), patch.object(
                fast_api,
                "resolve_pre_llm_reply",
                new=AsyncMock(return_value=None),
            ), patch.object(
                fast_api,
                "iter_main_llm_deltas",
                new=fail_before_first_delta,
            ):
                response = await client.post(
                    "/api/control-page/chat-stream",
                    json=self.admitted_local_payload(
                        "경계 스트림 응답 테스트"
                    ),
                )
                body = await response.text()
                payload = json.loads(body)
        finally:
            await client.close()

        self.assertEqual(response.status, 503)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error": (
                    "memory_deletion_journal_integrity_failed"
                ),
            },
        )
        self.assertNotIn('"type": "progress"', body)
        self.assertNotIn('"type": "error"', body)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn("PRIVATE_STREAM_MUST_NOT_SURVIVE", body)

    async def test_stream_planner_failure_uses_same_terminal_contract(
        self,
    ) -> None:
        private = (
            "Bearer stream-planner-secret "
            r"C:\Users\Admin\planner.json"
        )
        continuity = {
            "schema": "fast_control.delivery-continuity.v1",
            "enabled": True,
            "durable": True,
            "generation": 17,
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
        ) as commit_turn:
            events = await self.post_stream(
                "stream planner 실패"
            )

        error = next(
            event
            for event in events
            if event["type"] == "error"
        )
        self.assertEqual(
            error["error"],
            "fast_control_stream_failed",
        )
        self.assertEqual(error["continuity"], continuity)
        commit_turn.assert_called_once_with(
            "stream planner 실패",
            error["message"],
            memory_receipt=fast_api.not_used_memory_receipt_ref(),
        )
        self.assertNotIn(
            "stream-planner-secret",
            json.dumps(events, ensure_ascii=False),
        )

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

    async def test_explicit_memory_stream_returns_write_receipt_without_llm(self) -> None:
        receipt = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-234567890abcdef1",
            "sourceRef": (
                "turn:opaque-turn-" + ("c" * 64) + ":user"
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
        ), patch.object(
            fast_api,
            "plan_fast_tool_request_for_turn",
            new=AsyncMock(),
        ) as planner:
            events = await self.post_stream(
                "기억해줘: 나는 조용한 밤을 좋아해"
            )

        done = next(
            event for event in events if event["type"] == "done"
        )
        self.assertTrue(done["ok"])
        self.assertEqual(done["memoryWriteReceipt"], receipt)
        self.assertNotIn(
            "조용한 밤",
            json.dumps(receipt, ensure_ascii=False),
        )
        planner.assert_not_awaited()

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

    async def test_minecraft_execution_command_uses_central_lease_owner(
        self,
    ) -> None:
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
            events = await self.post_stream("마인크래프트에서 나무 캐줘")

        sentence = next(event for event in events if event["type"] == "sentence")
        done = next(event for event in events if event["type"] == "done")
        self.assertEqual(
            sentence["text"],
            "Minecraft world-action lease를 발급했고 게임 연결까지 확인했어.",
        )
        self.assertEqual(done["reply"], sentence["text"])
        self.assertIsNone(done["taskId"])
        connect.assert_awaited_once_with(
            0,
            issuer_ref="fast_control:local_bridge",
            source="control_page",
            goal="마인크래프트에서 나무 캐줘",
        )


if __name__ == "__main__":
    unittest.main()
