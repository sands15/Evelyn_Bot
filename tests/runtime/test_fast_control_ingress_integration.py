from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import textwrap
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
from evelyn_core import conversation_ingress_recovery as ingress_recovery  # noqa: E402
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
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
            "schema": CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
            "entryId": "ingress-" + "1" * 64,
            "turnId": "journal-turn",
            "phase": "accepted",
            "durable": True,
            "shouldProcess": True,
            "journalGeneration": 1,
        }
        self.request_ids: list[str] = []
        self.accepted_texts: list[str] = []
        self.events: list[tuple[str, str]] = []
        self.replay_record: dict[str, object] | None = None

    def claim_ingress(self, *, request_id, accepted_text):
        self.request_ids.append(str(request_id))
        self.accepted_texts.append(str(accepted_text))
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

    async def test_local_voice_consumption_reuses_exact_atomic_claim(
        self,
    ) -> None:
        manager = fast_api.LocalVoiceAdmissionManager()
        bridge_id = "atomic-local-bridge"
        turn_id = "atomic-local-turn"
        issued = manager.issue(
            bridge_id,
            turn_id,
            "이블린, 원자 경계 질문",
            validation_binding={},
            validation_is_current=lambda binding: not binding,
        )
        payload = {
            "source": "local_bridge",
            "bridgeInstanceId": bridge_id,
            "turnId": turn_id,
            "text": issued["forwardText"],
            "admissionToken": issued["admissionToken"],
        }

        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
            )
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    manager,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    owner,
                    "claim_ingress",
                    wraps=owner.claim_ingress,
                ) as durable_claim,
            ):
                (
                    admitted_text,
                    preclaimed,
                    rejection,
                ) = fast_api.consume_local_voice_admission(
                    payload,
                    text=str(payload["text"]),
                    source="local_bridge",
                )
                claim, cached, ingress_rejection = (
                    fast_api._prepare_fast_control_ingress(
                        payload,
                        accepted_text=admitted_text,
                        source="local_bridge",
                        preclaimed=preclaimed,
                    )
                )
            recovery_records = owner.ingress.recovery_records()

        stable_key = json.dumps(
            [bridge_id, turn_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertIsNone(rejection)
        self.assertIsNone(cached)
        self.assertIsNone(ingress_rejection)
        self.assertEqual(durable_claim.call_count, 1)
        self.assertEqual(claim["_effectId"], stable_key)
        self.assertEqual(len(recovery_records), 1)
        self.assertEqual(
            recovery_records[0]["entryId"],
            claim["entryId"],
        )
        self.assertEqual(
            recovery_records[0]["acceptedText"],
            "원자 경계 질문",
        )
        self.assertEqual(
            preclaimed.text_hash,
            fast_api.final_text_sha256("원자 경계 질문"),
        )
        self.assertEqual(manager.public_status()["acceptedCount"], 1)

    async def test_local_voice_claim_failure_does_not_burn_token(
        self,
    ) -> None:
        class FlakyIngressOwner(_IngressOwner):
            def __init__(self) -> None:
                super().__init__()
                self.fail = True

            def claim_ingress(self, *, request_id, accepted_text):
                self.request_ids.append(str(request_id))
                self.accepted_texts.append(str(accepted_text))
                if self.fail:
                    raise ConversationIngressRecoveryError(
                        "conversation_ingress_recovery_write_failed"
                    )
                receipt = dict(self.claim)
                receipt.update(
                    {
                        "entryId": (
                            fast_api.conversation_ingress_entry_id(
                                surface=(
                                    fast_api.FAST_CONTROL_INGRESS_SURFACE
                                ),
                                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                                source_delivery_id=request_id,
                            )
                        ),
                        "textHash": fast_api.final_text_sha256(
                            accepted_text
                        ),
                        "phase": "accepted",
                        "disposition": "claimed",
                        "durable": True,
                        "shouldProcess": True,
                        "journalGeneration": 1,
                    }
                )
                return receipt

        manager = fast_api.LocalVoiceAdmissionManager()
        owner = FlakyIngressOwner()
        bridge_id = "atomic-retry-bridge"
        turn_id = "atomic-retry-turn"
        issued = manager.issue(
            bridge_id,
            turn_id,
            "이블린, claim 실패 뒤 재시도",
            validation_binding={},
            validation_is_current=lambda binding: not binding,
        )
        payload = {
            "source": "local_bridge",
            "bridgeInstanceId": bridge_id,
            "turnId": turn_id,
            "text": issued["forwardText"],
            "admissionToken": issued["admissionToken"],
        }

        with (
            patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ),
            patch.object(
                fast_api,
                "LOCAL_VOICE_ADMISSION",
                manager,
            ),
            patch.object(
                fast_api,
                "local_voice_validation_binding_is_current",
                side_effect=lambda binding: not binding,
            ),
        ):
            _, _, first_rejection = fast_api.consume_local_voice_admission(
                payload,
                text=str(payload["text"]),
                source="local_bridge",
            )
            self.assertEqual(first_rejection.status, 503)
            self.assertEqual(manager.public_status()["acceptedCount"], 0)
            self.assertFalse(manager.public_status()["active"])

            owner.fail = False
            admitted_text, preclaimed, second_rejection = (
                fast_api.consume_local_voice_admission(
                    payload,
                    text=str(payload["text"]),
                    source="local_bridge",
                )
            )
            claim, _, ingress_rejection = (
                fast_api._prepare_fast_control_ingress(
                    payload,
                    accepted_text=admitted_text,
                    source="local_bridge",
                    preclaimed=preclaimed,
                )
            )

        self.assertIsNone(second_rejection)
        self.assertIsNone(ingress_rejection)
        self.assertTrue(claim["shouldProcess"])
        self.assertEqual(len(owner.request_ids), 2)
        self.assertEqual(manager.public_status()["acceptedCount"], 1)

    async def test_real_journal_io_failure_is_content_free_and_retryable(
        self,
    ) -> None:
        manager = fast_api.LocalVoiceAdmissionManager()
        bridge_id = "raw-io-error-bridge"
        turn_id = "raw-io-error-turn"
        issued = manager.issue(
            bridge_id,
            turn_id,
            "이블린, 저장 실패 뒤 재시도",
            validation_binding={},
            validation_is_current=lambda binding: not binding,
        )
        payload = {
            "source": "local_bridge",
            "bridgeInstanceId": bridge_id,
            "turnId": turn_id,
            "text": issued["forwardText"],
            "admissionToken": issued["admissionToken"],
        }

        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
            )
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    manager,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    ingress_recovery,
                    "atomic_json_write",
                    side_effect=OSError("PRIVATE_PATH_CANARY"),
                ),
            ):
                _, _, first_rejection = (
                    fast_api.consume_local_voice_admission(
                        payload,
                        text=str(payload["text"]),
                        source="local_bridge",
                    )
                )

            self.assertEqual(first_rejection.status, 503)
            self.assertEqual(
                json.loads(first_rejection.text)["error"],
                "conversation_ingress_recovery_unavailable",
            )
            self.assertNotIn("PRIVATE_PATH_CANARY", first_rejection.text)
            self.assertEqual(manager.public_status()["acceptedCount"], 0)
            self.assertFalse(manager.public_status()["active"])

            recovered_owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
            )
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    recovered_owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    manager,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
            ):
                admitted_text, preclaimed, retry_rejection = (
                    fast_api.consume_local_voice_admission(
                        payload,
                        text=str(payload["text"]),
                        source="local_bridge",
                    )
                )
                claim, _, ingress_rejection = (
                    fast_api._prepare_fast_control_ingress(
                        payload,
                        accepted_text=admitted_text,
                        source="local_bridge",
                        preclaimed=preclaimed,
                    )
                )

        self.assertIsNone(retry_rejection)
        self.assertIsNone(ingress_rejection)
        self.assertTrue(claim["shouldProcess"])
        self.assertEqual(manager.public_status()["acceptedCount"], 1)

    async def test_process_exit_after_real_claim_recovers_exact_turn(
        self,
    ) -> None:
        bridge_id = "crash-window-bridge"
        turn_id = "crash-window-turn"
        forward_text = "claim 직후 종료되는 질문"
        request_id = json.dumps(
            [bridge_id, turn_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        child = textwrap.dedent(
            r"""
            import json
            import os
            import sys
            from pathlib import Path

            sys.path.insert(0, sys.argv[1])

            from evelyn_core.fast_control_continuity import (
                FastControlContinuityOwner,
            )
            from evelyn_core.local_voice_admission import (
                LocalVoiceAdmissionManager,
            )

            root = Path(sys.argv[2])
            bridge_id = sys.argv[3]
            turn_id = sys.argv[4]
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = LocalVoiceAdmissionManager()
            issued = manager.issue(
                bridge_id,
                turn_id,
                "이블린, claim 직후 종료되는 질문",
                validation_binding={},
                validation_is_current=lambda binding: not binding,
            )
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )

            def claim_then_exit(claim_request):
                owner.claim_ingress(
                    request_id=request_id,
                    accepted_text=claim_request.forward_text,
                )
                os._exit(91)

            manager.consume_with_durable_claim(
                issued["admissionToken"],
                bridge_id,
                turn_id,
                issued["forwardText"],
                durable_claim=claim_then_exit,
                validation_is_current=lambda binding: not binding,
            )
            raise SystemExit(92)
            """
        )

        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child,
                    str(RUNTIME_ROOT),
                    temporary,
                    bridge_id,
                    turn_id,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                completed.returncode,
                91,
                completed.stderr,
            )
            recovered_owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            records = recovered_owner.ingress.recovery_records()
            recovered_manager = fast_api.LocalVoiceAdmissionManager()
            recovered_issued = recovered_manager.issue(
                bridge_id,
                turn_id,
                f"이블린, {forward_text}",
                validation_binding={},
                validation_is_current=lambda binding: not binding,
            )
            recovered_payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": recovered_issued["forwardText"],
                "admissionToken": recovered_issued["admissionToken"],
            }
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    recovered_owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    recovered_manager,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
            ):
                (
                    duplicate_text,
                    duplicate_preclaim,
                    duplicate_admission_rejection,
                ) = fast_api.consume_local_voice_admission(
                    recovered_payload,
                    text=str(recovered_payload["text"]),
                    source="local_bridge",
                )
                duplicate_claim, _, duplicate_ingress_rejection = (
                    fast_api._prepare_fast_control_ingress(
                        recovered_payload,
                        accepted_text=duplicate_text,
                        source="local_bridge",
                        preclaimed=duplicate_preclaim,
                    )
                )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["phase"], "accepted")
        self.assertTrue(records[0]["recovered"])
        self.assertEqual(records[0]["sourceDeliveryId"], request_id)
        self.assertEqual(records[0]["acceptedText"], forward_text)
        self.assertIsNone(duplicate_admission_rejection)
        self.assertEqual(duplicate_ingress_rejection.status, 409)
        self.assertEqual(
            json.loads(duplicate_ingress_rejection.text)["error"],
            fast_api.FAST_CONTROL_INGRESS_PENDING_ERROR,
        )
        self.assertFalse(duplicate_claim["shouldProcess"])
        self.assertEqual(
            duplicate_claim["entryId"],
            records[0]["entryId"],
        )
        self.assertEqual(
            recovered_manager.public_status()["acceptedCount"],
            0,
        )
        self.assertFalse(recovered_manager.public_status()["active"])
        self.assertEqual(fast_api.CHAT_MESSAGES, [])
        self.assertEqual(recovered_owner.restored_chat_messages(), [])

    async def test_mismatched_entry_receipt_never_consumes_token(
        self,
    ) -> None:
        class BindingOwner(_IngressOwner):
            def __init__(self) -> None:
                super().__init__()
                self.return_wrong_entry = True

            def claim_ingress(self, *, request_id, accepted_text):
                self.request_ids.append(str(request_id))
                expected_entry_id = (
                    fast_api.conversation_ingress_entry_id(
                        surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                        scope=fast_api.FAST_CONTROL_SESSION_KEY,
                        source_delivery_id=request_id,
                    )
                )
                return {
                    "schema": (
                        "conversation.ingress-recovery-receipt.v1"
                    ),
                    "entryId": (
                        "ingress-" + "f" * 64
                        if self.return_wrong_entry
                        else expected_entry_id
                    ),
                    "turnId": "journal-binding-turn",
                    "phase": "accepted",
                    "disposition": "claimed",
                    "durable": True,
                    "shouldProcess": True,
                    "textHash": fast_api.final_text_sha256(
                        accepted_text
                    ),
                    "journalGeneration": 1,
                }

        manager = fast_api.LocalVoiceAdmissionManager()
        owner = BindingOwner()
        bridge_id = "binding-retry-bridge"
        turn_id = "binding-retry-turn"
        issued = manager.issue(
            bridge_id,
            turn_id,
            "이블린, exact receipt만 받아",
            validation_binding={},
            validation_is_current=lambda binding: not binding,
        )
        payload = {
            "source": "local_bridge",
            "bridgeInstanceId": bridge_id,
            "turnId": turn_id,
            "text": issued["forwardText"],
            "admissionToken": issued["admissionToken"],
        }

        with (
            patch.object(
                fast_api,
                "FAST_CONTROL_CONTINUITY_OWNER",
                owner,
            ),
            patch.object(
                fast_api,
                "LOCAL_VOICE_ADMISSION",
                manager,
            ),
            patch.object(
                fast_api,
                "local_voice_validation_binding_is_current",
                side_effect=lambda binding: not binding,
            ),
        ):
            _, _, rejection = fast_api.consume_local_voice_admission(
                payload,
                text=str(payload["text"]),
                source="local_bridge",
            )
            self.assertEqual(rejection.status, 503)
            self.assertEqual(manager.public_status()["acceptedCount"], 0)
            self.assertFalse(manager.public_status()["active"])

            owner.return_wrong_entry = False
            admitted_text, preclaimed, retry_rejection = (
                fast_api.consume_local_voice_admission(
                    payload,
                    text=str(payload["text"]),
                    source="local_bridge",
                )
            )
            claim, _, ingress_rejection = (
                fast_api._prepare_fast_control_ingress(
                    payload,
                    accepted_text=admitted_text,
                    source="local_bridge",
                    preclaimed=preclaimed,
                )
            )

        self.assertIsNone(retry_rejection)
        self.assertIsNone(ingress_rejection)
        self.assertEqual(len(owner.request_ids), 2)
        self.assertTrue(claim["shouldProcess"])
        self.assertEqual(manager.public_status()["acceptedCount"], 1)

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
