from __future__ import annotations

import asyncio
import json
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressRecoveryError,
    DEFAULT_INGRESS_MAX_AGE_SEC,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)


class _JsonRequest:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def json(self) -> dict[str, object]:
        return dict(self.payload)


class _IngressOwner:
    enabled = True

    def __init__(self, claim: dict[str, object] | None = None) -> None:
        self.claim = claim or {
            "entryId": "ingress-" + "1" * 64,
            "turnId": "journal-turn",
            "phase": "accepted",
            "shouldProcess": True,
        }
        self.request_ids: list[str] = []
        self.events: list[tuple[str, str]] = []
        self.replay_record: dict[str, object] | None = None

    def claim_ingress(self, *, request_id, accepted_text):
        self.request_ids.append(str(request_id))
        return dict(self.claim)

    def ingress_record(self, entry_id, *, replay=False):
        self.events.append(("replay" if replay else "record", str(entry_id)))
        return dict(self.replay_record) if self.replay_record else None

    def mark_ingress_delivery_inflight(
        self, entry_id, *, delivery_ref="", streaming=False
    ):
        self.events.append(("inflight", str(entry_id)))
        return {}

    def mark_ingress_delivery_ambiguous(self, entry_id, *, error_code):
        self.events.append(("ambiguous", str(error_code)))
        return {}

    def mark_ingress_delivery_succeeded(self, entry_id, *, delivery_ref=""):
        self.events.append(("succeeded", str(entry_id)))
        return {}

    def bind_ingress_response(self, entry_id, **_kwargs):
        self.events.append(("bound", str(entry_id)))
        return {}


class FastControlIngressIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fast_api.CHAT_MESSAGES.clear()

    async def asyncTearDown(self) -> None:
        fast_api.CHAT_MESSAGES.clear()

    async def test_client_retry_lease_has_server_safety_margin(self) -> None:
        self.assertEqual(DEFAULT_INGRESS_MAX_AGE_SEC, 15 * 60)
        self.assertLess(14 * 60, DEFAULT_INGRESS_MAX_AGE_SEC)

    async def test_disabled_and_unavailable_owner_fail_closed(self) -> None:
        disabled = SimpleNamespace(enabled=False)
        with patch.object(
            fast_api,
            "FAST_CONTROL_CONTINUITY_OWNER",
            disabled,
        ):
            _, _, rejection = fast_api._prepare_fast_control_ingress(
                {"requestId": "request-disabled"},
                accepted_text="질문",
                source="control_page",
            )
        self.assertEqual(rejection.status, 503)
        self.assertEqual(
            json.loads(rejection.text)["error"],
            "conversation_ingress_recovery_unavailable",
        )

        class _UnavailableOwner:
            enabled = True

            @staticmethod
            def claim_ingress(**_kwargs):
                raise ConversationIngressRecoveryError(
                    "conversation_ingress_recovery_pending"
                )

        with patch.object(
            fast_api,
            "FAST_CONTROL_CONTINUITY_OWNER",
            _UnavailableOwner(),
        ):
            _, _, rejection = fast_api._prepare_fast_control_ingress(
                {"requestId": "request-unavailable"},
                accepted_text="질문",
                source="control_page",
            )
        self.assertEqual(rejection.status, 503)

    async def test_stable_source_effect_key_and_voice_redelivery_suppression(
        self,
    ) -> None:
        owner = _IngressOwner()
        with patch.object(
            fast_api,
            "FAST_CONTROL_CONTINUITY_OWNER",
            owner,
        ):
            claim, _, rejection = fast_api._prepare_fast_control_ingress(
                {
                    "bridgeInstanceId": "bridge-a",
                    "turnId": "turn-a",
                },
                accepted_text="질문",
                source="local_bridge",
            )
        stable_key = '["bridge-a","turn-a"]'
        self.assertIsNone(rejection)
        self.assertEqual(owner.request_ids, [stable_key])
        self.assertEqual(claim["_effectId"], stable_key)
        self.assertNotEqual(claim["_effectId"], claim["turnId"])

        completed = _IngressOwner(
            {
                "entryId": "ingress-" + "2" * 64,
                "turnId": "journal-turn-2",
                "phase": "completed",
                "shouldProcess": False,
            }
        )
        with patch.object(
            fast_api,
            "FAST_CONTROL_CONTINUITY_OWNER",
            completed,
        ):
            _, cached, rejection = fast_api._prepare_fast_control_ingress(
                {"requestId": "voice-request"},
                accepted_text="질문",
                source="voice",
            )
        self.assertIsNone(cached)
        self.assertEqual(rejection.status, 409)
        self.assertEqual(
            json.loads(rejection.text)["error"],
            "conversation_ingress_completed_redelivery_suppressed",
        )
        self.assertEqual(completed.events, [])

    async def test_only_control_page_completed_retry_uses_guarded_cache(self) -> None:
        owner = _IngressOwner(
            {
                "entryId": "ingress-" + "3" * 64,
                "turnId": "journal-turn-3",
                "phase": "completed",
                "shouldProcess": False,
            }
        )
        owner.replay_record = {
            "entryId": owner.claim["entryId"],
            "phase": "completed",
            "assistantText": "캐시 답변",
            "memoryReceiptRef": not_used_memory_receipt_ref(),
        }
        with (
            patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ),
            patch.object(
                fast_api,
                "memory_deletion_journal_guard",
                side_effect=lambda *_args, **_kwargs: nullcontext(None),
            ),
            patch.object(
                fast_api,
                "capture_memory_deletion_outbound_position",
            ),
            patch.object(
                fast_api,
                "memory_exposure_position_from_receipt",
                return_value=None,
            ),
            patch.object(
                fast_api,
                "memory_exposure_guard",
                side_effect=lambda *_args, **_kwargs: nullcontext(),
            ),
        ):
            _, cached, rejection = fast_api._prepare_fast_control_ingress(
                {"requestId": "browser-request"},
                accepted_text="질문",
                source="control_page",
            )
        self.assertIsNone(rejection)
        self.assertEqual(cached[0]["assistantText"], "캐시 답변")
        self.assertIn(("replay", owner.claim["entryId"]), owner.events)

    async def test_nonstream_integrity_error_does_not_enter_stream_path(self) -> None:
        owner = _IngressOwner()
        with (
            patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ),
            patch.object(
                fast_api,
                "execute_explicit_memory_confirmation",
                side_effect=MemoryDeletionJournalIntegrityError(),
            ),
        ):
            with self.assertRaises(MemoryDeletionJournalIntegrityError):
                await fast_api.chat_handler(
                    _JsonRequest(
                        {
                            "text": "질문",
                            "source": "control_page",
                            "requestId": "nonstream-request",
                        }
                    )
                )

    async def test_handler_uses_source_id_not_journal_turn_for_effect(self) -> None:
        owner = _IngressOwner()
        action_ids: list[str] = []

        def memory_command(_text, *, action_id):
            action_ids.append(str(action_id))
            return True, "확인", None, ""

        with (
            patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ),
            patch.object(
                fast_api,
                "execute_explicit_memory_confirmation",
                side_effect=memory_command,
            ),
            patch.object(
                fast_api,
                "_finalize_fast_chat_response",
                new=AsyncMock(return_value=web.Response(status=204)),
            ),
        ):
            response = await fast_api.chat_handler(
                _JsonRequest(
                    {
                        "text": "기억해줘",
                        "source": "control_page",
                        "requestId": "stable-effect-id",
                    }
                )
            )
        self.assertEqual(response.status, 204)
        self.assertEqual(action_ids, ["stable-effect-id"])
        self.assertNotEqual(action_ids[0], owner.claim["turnId"])

    async def test_partial_stream_failure_is_ambiguous_without_second_reply(
        self,
    ) -> None:
        owner = _IngressOwner()

        async def broken_stream():
            yield "부분 응답"
            raise RuntimeError("generation failed after first delta")

        app = web.Application()
        app.router.add_post("/chat", fast_api.chat_stream_handler)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "execute_explicit_memory_confirmation",
                    return_value=(False, "", None, ""),
                ),
                patch.object(
                    fast_api,
                    "plan_fast_tool_request_for_turn",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    fast_api,
                    "resolve_pre_llm_reply",
                    new=AsyncMock(return_value=None),
                ),
                patch.object(
                    fast_api,
                    "prepare_tool_plan_background_action",
                    return_value=None,
                ),
                patch.object(
                    fast_api,
                    "prepare_registered_background_action",
                    return_value=None,
                ),
                patch.object(
                    fast_api,
                    "iter_main_llm_deltas",
                    side_effect=lambda *_args, **_kwargs: broken_stream(),
                ),
                patch.object(
                    fast_api,
                    "should_emit_memory_recall_progress",
                    return_value=False,
                ),
                patch.object(
                    fast_api,
                    "commit_fast_control_turn",
                    new=Mock(side_effect=AssertionError("must not commit")),
                ),
            ):
                response = await client.post(
                    "/chat",
                    json={
                        "text": "스트림 질문",
                        "source": "control_page",
                        "requestId": "stream-request",
                    },
                )
                body = await response.text()
        finally:
            await client.close()

        events = [
            json.loads(line)
            for line in body.splitlines()
            if line.strip()
        ]
        self.assertTrue(any(event.get("type") == "delta" for event in events))
        self.assertFalse(any(event.get("type") == "error" for event in events))
        self.assertFalse(any(event.get("type") == "done" for event in events))
        self.assertIn(("inflight", owner.claim["entryId"]), owner.events)
        self.assertIn(
            ("ambiguous", "conversation_ingress_delivery_ambiguous"),
            owner.events,
        )
        self.assertFalse(any(name == "succeeded" for name, _ in owner.events))
        self.assertFalse(any(name == "bound" for name, _ in owner.events))

    async def test_delivery_callbacks_separate_success_from_failure(self) -> None:
        calls: list[str] = []
        response = fast_api.MemoryGuardedJsonResponse(
            {"ok": True},
            expected_position=None,
            before_write=lambda: calls.append("before"),
            after_write=lambda: calls.append("success"),
            after_write_failure=lambda code: calls.append(code),
        )
        response._run_before_write()
        response._run_after_write_failure(
            "conversation_ingress_delivery_disconnected"
        )
        response._run_after_write()
        self.assertEqual(
            calls,
            ["before", "conversation_ingress_delivery_disconnected"],
        )

    async def test_recovered_context_is_bounded_deduplicated_and_private(
        self,
    ) -> None:
        owner = SimpleNamespace(
            recovered_ingress_context_messages=lambda **_kwargs: [
                {
                    "role": "user",
                    "content": "기존 질문",
                    "_ingressRecoveryEntryId": "entry-1",
                },
                {
                    "role": "user",
                    "content": "기존 질문",
                    "_ingressRecoveryEntryId": "entry-1",
                },
                {
                    "role": "user",
                    "content": "현재 질문",
                    "_ingressRecoveryEntryId": "entry-2",
                },
                {
                    "role": "assistant",
                    "content": "검증되지 않은 답변",
                    "_ingressRecoveryEntryId": "entry-3",
                },
                {
                    "role": "user",
                    "content": "복구된 미완료 질문",
                    "_ingressRecoveryEntryId": "entry-4",
                },
            ]
        )
        fast_api.CHAT_MESSAGES[:] = [
            {"role": "user", "text": "기존 질문"},
            {
                "role": "assistant",
                "text": "기존 답변",
                "memoryReceiptRef": not_used_memory_receipt_ref(),
            },
        ]

        def filter_history(messages, **_kwargs):
            return SimpleNamespace(
                messages=list(messages),
                memory_exposure_position=None,
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )

        with (
            patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ),
            patch.object(
                fast_api.CROSS_SURFACE_CONTINUITY_BRIDGE,
                "merge_for_fast",
                side_effect=lambda messages, **_kwargs: list(messages),
            ),
            patch.object(
                fast_api,
                "filter_conversation_history_for_memory_exposure",
                side_effect=filter_history,
            ),
        ):
            context = fast_api.recent_chat_messages_for_planner(
                "현재 질문",
                limit=8,
            )

        self.assertEqual(
            sum(item["content"] == "기존 질문" for item in context),
            1,
        )
        self.assertEqual(
            sum(item["content"] == "복구된 미완료 질문" for item in context),
            1,
        )
        self.assertNotIn("현재 질문", [item["content"] for item in context])
        self.assertNotIn(
            "검증되지 않은 답변",
            [item["content"] for item in context],
        )
        self.assertTrue(all(set(item) == {"role", "content"} for item in context))


if __name__ == "__main__":
    unittest.main()
