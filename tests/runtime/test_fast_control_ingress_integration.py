from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import nullcontext, suppress
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

CAPTURE_FENCE_DIGEST = "a" * 64

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
    MEMORY_DELETION_JOURNAL_BUSY_ERROR,
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
)
from evelyn_core.voice_validation import (  # noqa: E402
    SUITE_ID,
    VoiceValidationManager,
    active_validation_context,
    emit_voice_validation_event,
    validation_attempt_binding_is_current,
    validation_transcript_admission_status,
)
from evelyn_core.voice_validation_attempt_lease import (  # noqa: E402
    acquire_attempt_lease,
)
from evelyn_core.voice_capture_consent import (  # noqa: E402
    VoiceCaptureConsentManager,
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

    def claim_reserved_ingress(
        self,
        *,
        request_id,
        accepted_text,
        turn_id,
        reservation_ref,
    ):
        receipt = dict(
            self.claim_ingress(
                request_id=request_id,
                accepted_text=accepted_text,
            )
        )
        receipt.update(
            {
                "turnId": str(turn_id),
                "phase": "accepted",
                "disposition": "claimed",
                "durable": True,
                "shouldProcess": True,
                "textHash": fast_api.final_text_sha256(accepted_text),
                "journalGeneration": int(
                    receipt.get("journalGeneration") or 1
                ),
            }
        )
        return receipt

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


def _durably_issue_local_voice(
    manager,
    owner,
    *,
    bridge_id: str,
    turn_id: str,
    text: str,
):
    request_id = json.dumps(
        [bridge_id, turn_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    def reserve(request):
        receipt = owner.reserve_ingress(
            request_id=request_id,
            text_hash=request.forward_text_digest,
            turn_id=request.ingress_turn_id,
            reservation_ref=request.reservation_ref,
            ttl_sec=request.ttl_sec,
        )
        return fast_api.LocalVoiceDurableIssuanceReservation(
            schema=str(receipt.get("schema") or ""),
            durable=receipt.get("durable") is True,
            bridge_instance_id=request.bridge_instance_id,
            local_turn_id=request.turn_id,
            forward_text_digest=request.forward_text_digest,
            reservation_ref=request.reservation_ref,
            entry_id=str(receipt.get("entryId") or ""),
            ingress_turn_id=str(receipt.get("turnId") or ""),
            phase=str(receipt.get("phase") or ""),
            disposition=str(receipt.get("disposition") or ""),
            should_process=receipt.get("shouldProcess") is True,
            text_hash=str(receipt.get("textHash") or ""),
            journal_generation=int(receipt.get("journalGeneration") or 0),
        )

    return manager.issue_with_durable_reservation(
        bridge_id,
        turn_id,
        text,
        durable_reservation=reserve,
        capture_fence_digest=CAPTURE_FENCE_DIGEST,
        validation_binding={},
        validation_is_current=lambda binding: not binding,
    ).admission


def _start_local_validation_turn(
    root: Path,
    *,
    bridge_id: str,
    turn_id: str,
) -> SimpleNamespace:
    manager = VoiceValidationManager(root=root)
    started = manager.start(
        suite=SUITE_ID,
        surfaces=("local",),
        capabilities={
            "voiceLocal": {
                "state": "ready",
                "ready": True,
                "blockers": [],
            },
            "voiceDiscord": {
                "state": "ready",
                "ready": True,
                "blockers": [],
            },
        },
    )
    session = started["session"]
    context = active_validation_context(surface="local", root=root)
    return SimpleNamespace(
        root=root,
        manager=manager,
        session=session,
        binding={
            "sessionId": context["sessionId"],
            "stepId": context["stepId"],
            "attempt": context["attempt"],
            "attemptId": context["attemptId"],
        },
        prompt=session["currentStep"]["prompt"],
        bridge_id=bridge_id,
        turn_id=turn_id,
        owner=fast_api.FastControlContinuityOwner(
            artifacts_root=root,
            enabled=True,
            log=lambda *_args, **_kwargs: None,
        ),
        admission=fast_api.LocalVoiceAdmissionManager(),
    )


def _validation_runtime_patches(
    turn: SimpleNamespace,
    forbidden_hooks: tuple[Mock, ...],
    **overrides,
):
    replacements = {
        "FAST_CONTROL_CONTINUITY_OWNER": turn.owner,
        "LOCAL_VOICE_ADMISSION": turn.admission,
        "acquire_attempt_lease": Mock(
            side_effect=lambda value: acquire_attempt_lease(
                value,
                root=turn.root,
            )
        ),
        "local_voice_validation_binding_is_current": Mock(
            side_effect=lambda value: validation_attempt_binding_is_current(
                value,
                surface="local",
                root=turn.root,
                reject_unbound_when_active=True,
            )
        ),
        "validation_transcript_admission_status": Mock(
            side_effect=lambda surface, transcript, value: (
                validation_transcript_admission_status(
                    surface,
                    transcript,
                    value,
                    root=turn.root,
                )
            )
        ),
        "emit_voice_validation_event": Mock(
            side_effect=lambda surface, event, **payload: (
                emit_voice_validation_event(
                    surface,
                    event,
                    root=turn.root,
                    **payload,
                )
            )
        ),
        "execute_explicit_memory_confirmation": forbidden_hooks[0],
        "plan_fast_tool_request_for_turn": forbidden_hooks[1],
        "resolve_pre_llm_reply": forbidden_hooks[2],
        "prepare_tool_plan_background_action": forbidden_hooks[3],
        "prepare_registered_background_action": forbidden_hooks[4],
        "launch_background_action": forbidden_hooks[5],
        "append_chat_message": Mock(),
        "memory_exposure_guard": Mock(
            side_effect=lambda *_args, **_kwargs: nullcontext()
        ),
    }
    replacements.update(overrides)
    return patch.multiple(fast_api, **replacements)


async def _issue_local_validation_turn(turn: SimpleNamespace):
    response = await fast_api.local_voice_admission_handler(
        _JsonRequest(
            {
                "bridgeInstanceId": turn.bridge_id,
                "turnId": turn.turn_id,
                "text": turn.prompt,
                "validation": turn.binding,
            }
        )
    )
    return response, json.loads(response.text)


def _validation_chat_payload(
    turn: SimpleNamespace,
    issued: dict[str, object],
) -> dict[str, object]:
    return {
        "source": "local_bridge",
        "bridgeInstanceId": turn.bridge_id,
        "turnId": turn.turn_id,
        "text": issued["forwardText"],
        "admissionToken": issued["admissionToken"],
        "admissionMode": issued["mode"],
        "validation": turn.binding,
    }


class FastControlIngressIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fast_api.CHAT_MESSAGES.clear()
        self._previous_capture_fence_digest = (
            fast_api.LOCAL_BRIDGE_STATUS.get("voiceCaptureFenceDigest")
        )
        fast_api.LOCAL_BRIDGE_STATUS["voiceCaptureFenceDigest"] = (
            CAPTURE_FENCE_DIGEST
        )
        self._capture_fence_patch = patch.object(
            fast_api,
            "local_voice_capture_fence_is_current",
            return_value=True,
        )
        self._capture_fence_patch.start()
        self._capture_claim_lease_patch = patch.object(
            fast_api,
            "_acquire_local_voice_capture_claim_lease",
            side_effect=lambda: nullcontext(),
        )
        self._capture_claim_lease_patch.start()

    async def asyncTearDown(self) -> None:
        self._capture_claim_lease_patch.stop()
        self._capture_fence_patch.stop()
        if self._previous_capture_fence_digest is None:
            fast_api.LOCAL_BRIDGE_STATUS.pop(
                "voiceCaptureFenceDigest",
                None,
            )
        else:
            fast_api.LOCAL_BRIDGE_STATUS["voiceCaptureFenceDigest"] = (
                self._previous_capture_fence_digest
            )
        fast_api.CHAT_MESSAGES.clear()

    async def test_client_retry_lease_has_server_safety_margin(self) -> None:
        self.assertEqual(DEFAULT_INGRESS_MAX_AGE_SEC, 15 * 60)
        self.assertLess(14 * 60, DEFAULT_INGRESS_MAX_AGE_SEC)

    async def test_unexpected_consume_exception_releases_validation_lease(
        self,
    ) -> None:
        lease = Mock()
        with (
            patch.object(
                fast_api,
                "_acquire_local_voice_validation_lease",
                return_value=(lease, None),
            ),
            patch.object(
                fast_api,
                "_consume_local_voice_admission_with_lease",
                side_effect=ValueError("unexpected consume failure"),
            ),
            self.assertRaisesRegex(
                ValueError,
                "unexpected consume failure",
            ),
        ):
            fast_api.consume_local_voice_admission(
                {"validation": {"attemptId": "attempt-a"}},
                text="검증 문장",
                source="local_bridge",
            )
        lease.release.assert_called_once_with()

    async def test_validation_chat_cancellation_releases_lease_once(
        self,
    ) -> None:
        lease = Mock()

        def consume(payload, **_kwargs):
            payload[fast_api._LOCAL_VOICE_VALIDATION_LEASE_KEY] = lease
            return "검증 문장", None, None

        with (
            patch.object(
                fast_api,
                "consume_local_voice_admission",
                side_effect=consume,
            ),
            patch.object(
                fast_api,
                "_prepare_fast_control_ingress",
                return_value=(None, None, None),
            ),
            patch.object(
                fast_api,
                "should_queue_local_bridge_speech",
                return_value=True,
            ),
            patch.object(
                fast_api,
                "ask_main_llm_and_queue_speech",
                new=AsyncMock(side_effect=asyncio.CancelledError()),
            ),
            self.assertRaises(asyncio.CancelledError),
        ):
            await fast_api.chat_handler(
                _JsonRequest(
                    {
                        "text": "검증 문장",
                        "source": "local_bridge",
                    }
                )
            )

        lease.release.assert_called_once_with()

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
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
            )
            issued = _durably_issue_local_voice(
                manager,
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, 원자 경계 질문",
            )
            payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
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
                patch.object(
                    owner,
                    "claim_reserved_ingress",
                    wraps=owner.claim_reserved_ingress,
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

    async def test_issue_stops_before_reservation_without_capture_fence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
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
                    fast_api,
                    "local_voice_capture_fence_is_current",
                    return_value=False,
                ),
                patch.object(
                    owner,
                    "reserve_ingress",
                    wraps=owner.reserve_ingress,
                ) as reserve,
            ):
                response = await fast_api.local_voice_admission_handler(
                    _JsonRequest(
                        {
                            "bridgeInstanceId": "closed-fence-bridge",
                            "turnId": "closed-fence-turn",
                            "text": "이블린, 동의 없는 발급 금지",
                        }
                    )
                )
        payload = json.loads(response.text)
        self.assertEqual(response.status, 409, payload)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_not_current",
        )
        self.assertNotIn("admissionToken", payload)
        reserve.assert_not_called()

    async def test_issue_rechecks_capture_fence_after_reservation(
        self,
    ) -> None:
        bridge_id = "reserve-race-bridge"
        turn_id = "reserve-race-turn"
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
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
                    fast_api,
                    "local_voice_capture_fence_is_current",
                    side_effect=(True, True, False),
                ),
                patch.object(
                    owner,
                    "revoke_reserved_ingress_batch",
                    wraps=owner.revoke_reserved_ingress_batch,
                ) as revoke,
            ):
                response = await fast_api.local_voice_admission_handler(
                    _JsonRequest(
                        {
                            "bridgeInstanceId": bridge_id,
                            "turnId": turn_id,
                            "text": "이블린, 예약 직후 동의 철회",
                        }
                    )
                )
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=request_id,
            )
            self.assertIsNone(owner.ingress.record_for(entry_id))

        payload = json.loads(response.text)
        self.assertEqual(response.status, 409, payload)
        self.assertEqual(
            payload["error"],
            "voice_capture_consent_not_current",
        )
        self.assertNotIn("admissionToken", payload)
        self.assertEqual(revoke.call_count, 1)
        self.assertEqual(manager.public_status()["acceptedCount"], 0)

    async def test_consume_rechecks_capture_fence_before_durable_claim(
        self,
    ) -> None:
        bridge_id = "claim-race-bridge"
        turn_id = "claim-race-turn"
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
            issued = _durably_issue_local_voice(
                manager,
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, claim 직전 동의 철회",
            )
            request_payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
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
                patch.object(
                    fast_api,
                    "local_voice_capture_fence_is_current",
                    side_effect=(True, False),
                ),
                patch.object(
                    owner,
                    "claim_reserved_ingress",
                    wraps=owner.claim_reserved_ingress,
                ) as claim,
            ):
                _, _, rejection = fast_api.consume_local_voice_admission(
                    request_payload,
                    text=str(request_payload["text"]),
                    source="local_bridge",
                )
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=request_id,
            )
            self.assertIsNone(owner.ingress.record_for(entry_id))

        self.assertIsNotNone(rejection)
        self.assertEqual(rejection.status, 409)
        self.assertEqual(
            json.loads(rejection.text)["error"],
            "voice_capture_consent_not_current",
        )
        claim.assert_not_called()
        self.assertEqual(manager.public_status()["acceptedCount"], 0)
        self.assertFalse(manager.public_status()["active"])

    async def test_old_fence_reservation_never_recovers_under_new_fence(
        self,
    ) -> None:
        bridge_id = "fence-generation-bridge"
        turn_id = "fence-generation-turn"
        rotated_digest = "b" * 64
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            issued = _durably_issue_local_voice(
                fast_api.LocalVoiceAdmissionManager(),
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, 이전 동의 토큰을 막아",
            )
            recovered = fast_api.LocalVoiceAdmissionManager()
            payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
            }
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=request_id,
            )
            fast_api.LOCAL_BRIDGE_STATUS["voiceCaptureFenceDigest"] = (
                rotated_digest
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
                    recovered,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    fast_api,
                    "local_voice_capture_fence_is_current",
                    return_value=True,
                ),
            ):
                _, _, mismatch = fast_api.consume_local_voice_admission(
                    payload,
                    text=str(payload["text"]),
                    source="local_bridge",
                )

            self.assertIsNotNone(mismatch)
            self.assertEqual(mismatch.status, 409)
            reserved = owner.ingress.record_for(entry_id)
            self.assertIsNotNone(reserved)
            self.assertEqual(reserved["phase"], "reserved")
            self.assertEqual(reserved["acceptedText"], "")

            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    recovered,
                ),
            ):
                error_code, status = (
                    fast_api._revoke_local_voice_for_capture_fence()
                )
            self.assertEqual((error_code, status), (
                "voice_capture_consent_not_current",
                409,
            ))
            self.assertIsNone(owner.ingress.record_for(entry_id))

            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    recovered,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    fast_api,
                    "local_voice_capture_fence_is_current",
                    return_value=True,
                ),
            ):
                _, _, after_reenable = (
                    fast_api.consume_local_voice_admission(
                        payload,
                        text=str(payload["text"]),
                        source="local_bridge",
                    )
                )
            self.assertIsNotNone(after_reenable)
            self.assertNotEqual(after_reenable.status, 200)
            self.assertIsNone(owner.ingress.record_for(entry_id))

    async def test_capture_claim_lease_failures_are_fixed_503_before_text_claim(
        self,
    ) -> None:
        bridge_id = "claim-lease-bridge"
        turn_id = "claim-lease-turn"
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
            issued = _durably_issue_local_voice(
                manager,
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, lease 실패 전에는 저장하지 마",
            )
            payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
            }
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=json.dumps(
                    [bridge_id, turn_id],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            for code in (
                "local_voice_capture_claim_inflight",
                "local_voice_capture_claim_lease_unavailable",
            ):
                with self.subTest(code=code):
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
                            fast_api,
                            "_acquire_local_voice_capture_claim_lease",
                            side_effect=(
                                fast_api.LocalVoiceAdmissionTransactionError(
                                    code
                                )
                            ),
                        ),
                        patch.object(
                            owner,
                            "claim_reserved_ingress",
                            wraps=owner.claim_reserved_ingress,
                        ) as claim,
                    ):
                        _, _, rejection = (
                            fast_api.consume_local_voice_admission(
                                payload,
                                text=str(payload["text"]),
                                source="local_bridge",
                            )
                        )
                    self.assertIsNotNone(rejection)
                    self.assertEqual(rejection.status, 503)
                    self.assertEqual(
                        json.loads(rejection.text)["error"],
                        code,
                    )
                    claim.assert_not_called()
                    record = owner.ingress.record_for(entry_id)
                    self.assertIsNotNone(record)
                    self.assertEqual(record["phase"], "reserved")
                    self.assertEqual(record["acceptedText"], "")

    async def test_consent_revoke_waits_for_linearized_text_claim(self) -> None:
        bridge_id = "claim-revoke-linearization-bridge"
        turn_id = "claim-revoke-linearization-turn"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            consent = VoiceCaptureConsentManager(
                root=root,
                auth_token="test-voice-capture-auth-token-0123456789",
            )
            consent.begin_revoke(reason="initial_recovery")
            consent.finish_revoke(applied=True)
            preview = consent.preview()
            applying = consent.begin_apply(
                confirm_token=preview["confirmToken"]
            )
            consent.finish_apply(
                lease_id=applying["leaseId"],
                applied=True,
                capture_ready=True,
            )

            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            admission = fast_api.LocalVoiceAdmissionManager()
            issued = _durably_issue_local_voice(
                admission,
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, 철회와 claim 순서를 지켜",
            )
            payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
            }
            original_claim = owner.claim_reserved_ingress
            revoke_started = threading.Event()
            revoke_completed = threading.Event()
            revoke_result = []
            revoke_threads = []

            def revoke() -> None:
                revoke_started.set()
                revoke_result.append(
                    consent.begin_revoke(reason="user_revoked")
                )
                revoke_completed.set()

            def claim_while_revoke_waits(**kwargs):
                worker = threading.Thread(target=revoke)
                revoke_threads.append(worker)
                worker.start()
                self.assertTrue(revoke_started.wait(timeout=2))
                self.assertFalse(revoke_completed.wait(timeout=0.1))
                return original_claim(**kwargs)

            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    admission,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    fast_api,
                    "_acquire_local_voice_capture_claim_lease",
                    side_effect=lambda: (
                        fast_api.acquire_voice_capture_consent_claim_lease(
                            root=root
                        )
                    ),
                ),
                patch.object(
                    owner,
                    "claim_reserved_ingress",
                    side_effect=claim_while_revoke_waits,
                ),
            ):
                admitted_text, claim, rejection = (
                    fast_api.consume_local_voice_admission(
                        payload,
                        text=str(payload["text"]),
                        source="local_bridge",
                    )
                )

            for worker in revoke_threads:
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
            self.assertIsNone(rejection)
            self.assertEqual(admitted_text, issued["forwardText"])
            self.assertIsNotNone(claim)
            self.assertTrue(claim.should_process)
            self.assertTrue(revoke_completed.is_set())
            self.assertTrue(revoke_result[0]["controlRequired"])
            self.assertEqual(consent.status()["state"], "revoking")
            records = owner.ingress.recovery_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["phase"], "accepted")
            self.assertEqual(
                records[0]["acceptedText"],
                issued["forwardText"],
            )
    async def test_issuance_reservation_survives_manager_restart_content_free(
        self,
    ) -> None:
        bridge_id = "issuance-crash-bridge"
        turn_id = "issuance-crash-turn"
        original_text = "이블린, PRIVATE_ISSUANCE_CANARY"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            issuer = fast_api.LocalVoiceAdmissionManager()
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    issuer,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
            ):
                response = await fast_api.local_voice_admission_handler(
                    _JsonRequest(
                        {
                            "bridgeInstanceId": bridge_id,
                            "turnId": turn_id,
                            "text": original_text,
                        }
                    )
                )
            issued = json.loads(response.text)
            token = issued["admissionToken"]
            self.assertEqual(response.status, 200)
            self.assertEqual(owner.ingress.recovery_records(), [])
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=request_id,
            )
            first_record = owner.ingress.record_for(entry_id)
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*.json")
            )
            self.assertNotIn(original_text, persisted)
            self.assertNotIn("PRIVATE_ISSUANCE_CANARY", persisted)
            self.assertNotIn(token, persisted)

            reissuer = fast_api.LocalVoiceAdmissionManager()
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    reissuer,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
            ):
                reissued_response = (
                    await fast_api.local_voice_admission_handler(
                        _JsonRequest(
                            {
                                "bridgeInstanceId": bridge_id,
                                "turnId": turn_id,
                                "text": original_text,
                            }
                        )
                    )
                )
            reissued = json.loads(reissued_response.text)
            second_record = owner.ingress.record_for(entry_id)
            self.assertEqual(reissued_response.status, 200, reissued)
            self.assertNotEqual(
                reissued["admissionToken"],
                token,
            )
            self.assertEqual(first_record["turnId"], second_record["turnId"])
            self.assertNotEqual(
                first_record["deliveryRef"],
                second_record["deliveryRef"],
            )
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*.json")
            )
            self.assertNotIn(reissued["admissionToken"], persisted)
            self.assertNotIn("PRIVATE_ISSUANCE_CANARY", persisted)

            recovered = fast_api.LocalVoiceAdmissionManager()
            payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": reissued["forwardText"],
                "admissionToken": reissued["admissionToken"],
                "admissionMode": reissued["mode"],
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
                    recovered,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    fast_api,
                    "local_voice_recovery_context_is_current",
                    return_value=True,
                ),
            ):
                stale_payload = {
                    **payload,
                    "admissionToken": token,
                }
                _, _, stale_rejection = (
                    fast_api.consume_local_voice_admission(
                        stale_payload,
                        text=str(stale_payload["text"]),
                        source="local_bridge",
                    )
                )
                admitted_text, claim, rejection = (
                    fast_api.consume_local_voice_admission(
                        payload,
                        text=str(payload["text"]),
                        source="local_bridge",
                    )
                )
                _, _, replay_rejection = (
                    fast_api.consume_local_voice_admission(
                        payload,
                        text=str(payload["text"]),
                        source="local_bridge",
                    )
                )

            self.assertEqual(stale_rejection.status, 409)
            self.assertEqual(
                json.loads(stale_rejection.text)["error"],
                "local_voice_turn_binding_mismatch",
            )
            self.assertIsNone(rejection)
            self.assertEqual(admitted_text, "PRIVATE_ISSUANCE_CANARY")
            self.assertIsNotNone(claim)
            self.assertTrue(claim.reservation_verified)
            self.assertTrue(claim.should_process)
            self.assertEqual(replay_rejection.status, 409)
            records = owner.ingress.recovery_records()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["acceptedText"], admitted_text)
            self.assertEqual(recovered.public_status()["acceptedCount"], 1)

    async def test_mismatch_revocation_prevents_old_token_restart_recovery(
        self,
    ) -> None:
        bridge_id = "revocation-bridge"
        turn_id = "revocation-turn"
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
            issued = _durably_issue_local_voice(
                manager,
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, 원래 질문",
            )
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=request_id,
            )
            mismatched = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": "바뀐 질문",
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
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
                _, _, mismatch_rejection = (
                    fast_api.consume_local_voice_admission(
                        mismatched,
                        text=str(mismatched["text"]),
                        source="local_bridge",
                    )
                )

            self.assertEqual(mismatch_rejection.status, 409)
            self.assertEqual(
                json.loads(mismatch_rejection.text)["reason"],
                "admission_text_mismatch",
            )
            self.assertIsNone(owner.ingress.record_for(entry_id))

            recovered = fast_api.LocalVoiceAdmissionManager()
            exact = {**mismatched, "text": issued["forwardText"]}
            with (
                patch.object(
                    fast_api,
                    "FAST_CONTROL_CONTINUITY_OWNER",
                    owner,
                ),
                patch.object(
                    fast_api,
                    "LOCAL_VOICE_ADMISSION",
                    recovered,
                ),
                patch.object(
                    fast_api,
                    "local_voice_validation_binding_is_current",
                    side_effect=lambda binding: not binding,
                ),
                patch.object(
                    fast_api,
                    "local_voice_recovery_context_is_current",
                    return_value=True,
                ),
            ):
                _, _, restart_rejection = (
                    fast_api.consume_local_voice_admission(
                        exact,
                        text=str(exact["text"]),
                        source="local_bridge",
                    )
                )

        self.assertEqual(restart_rejection.status, 503)
        self.assertEqual(
            json.loads(restart_rejection.text)["error"],
            "conversation_ingress_recovery_unavailable",
        )

    async def test_mic_off_revokes_all_reservations_in_one_journal_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
            entries = []
            for index in (1, 2):
                _durably_issue_local_voice(
                    manager,
                    owner,
                    bridge_id="mic-off-bridge",
                    turn_id=f"mic-off-turn-{index}",
                    text=f"이블린, 질문 {index}",
                )
                request_id = json.dumps(
                    ["mic-off-bridge", f"mic-off-turn-{index}"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                entries.append(
                    fast_api.conversation_ingress_entry_id(
                        surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                        scope=fast_api.FAST_CONTROL_SESSION_KEY,
                        source_delivery_id=request_id,
                    )
                )
            generation = owner.ingress.public_status()["generation"]
            original_request = dict(
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
            )
            original_fence = dict(fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE)
            try:
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
                ):
                    request = fast_api.request_local_bridge_mic_control(
                        False,
                        source="unit",
                    )
            finally:
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.clear()
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
                    original_request
                )
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.clear()
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.update(
                    original_fence
                )

        self.assertFalse(request["enabled"])
        self.assertEqual(
            owner.ingress.public_status()["generation"],
            generation + 1,
        )
        self.assertTrue(
            all(owner.ingress.record_for(entry_id) is None for entry_id in entries)
        )
        self.assertFalse(manager.public_status()["revocationFenced"])

    async def test_mic_off_purges_reservation_after_manager_restart(self) -> None:
        bridge_id = "mic-off-restart-bridge"
        turn_id = "mic-off-restart-turn"
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            _durably_issue_local_voice(
                fast_api.LocalVoiceAdmissionManager(),
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, 재시작 뒤 OFF 예약 제거",
            )
            entry_id = fast_api.conversation_ingress_entry_id(
                surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                scope=fast_api.FAST_CONTROL_SESSION_KEY,
                source_delivery_id=json.dumps(
                    [bridge_id, turn_id],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            self.assertIsNotNone(owner.ingress.record_for(entry_id))
            recovered = fast_api.LocalVoiceAdmissionManager()
            original_request = dict(
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
            )
            original_fence = dict(fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE)
            try:
                with (
                    patch.object(
                        fast_api,
                        "FAST_CONTROL_CONTINUITY_OWNER",
                        owner,
                    ),
                    patch.object(
                        fast_api,
                        "LOCAL_VOICE_ADMISSION",
                        recovered,
                    ),
                ):
                    request = fast_api.request_local_bridge_mic_control(
                        False,
                        source="unit",
                    )
            finally:
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.clear()
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
                    original_request
                )
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.clear()
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.update(
                    original_fence
                )

            self.assertFalse(request["enabled"])
            self.assertIsNone(owner.ingress.record_for(entry_id))
            self.assertFalse(recovered.public_status()["revocationFenced"])

    async def test_mic_off_is_published_when_revocation_write_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = fast_api.LocalVoiceAdmissionManager()
            _durably_issue_local_voice(
                manager,
                owner,
                bridge_id="failed-revoke-bridge",
                turn_id="failed-revoke-turn",
                text="이블린, 철회 실패 경계",
            )
            original_request = dict(
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
            )
            original_fence = dict(fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE)
            try:
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
                        owner,
                        "revoke_reserved_ingress_batch",
                        side_effect=OSError("simulated durable write failure"),
                    ),
                ):
                    with self.assertRaises(
                        fast_api.LocalVoiceAdmissionTransactionError
                    ):
                        fast_api.request_local_bridge_mic_control(
                            False,
                            source="unit",
                        )
                    published = dict(
                        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
                    )
            finally:
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.clear()
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
                    original_request
                )
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.clear()
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.update(
                    original_fence
                )

        self.assertFalse(published["enabled"])
        self.assertTrue(manager.public_status()["revocationFenced"])

    async def test_restart_orphan_purge_failure_fences_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            _durably_issue_local_voice(
                fast_api.LocalVoiceAdmissionManager(),
                owner,
                bridge_id="orphan-purge-bridge",
                turn_id="orphan-purge-turn",
                text="이블린, orphan purge 실패",
            )
            recovered = fast_api.LocalVoiceAdmissionManager()
            original_request = dict(
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
            )
            original_fence = dict(fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE)
            try:
                with (
                    patch.object(
                        fast_api,
                        "FAST_CONTROL_CONTINUITY_OWNER",
                        owner,
                    ),
                    patch.object(
                        fast_api,
                        "LOCAL_VOICE_ADMISSION",
                        recovered,
                    ),
                    patch.object(
                        owner,
                        "revoke_reserved_local_voice_ingress",
                        side_effect=OSError("scope purge failed"),
                    ),
                ):
                    with self.assertRaises(
                        fast_api.LocalVoiceAdmissionTransactionError
                    ):
                        fast_api.request_local_bridge_mic_control(
                            False,
                            source="unit",
                        )
                    published = dict(
                        fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST
                    )
            finally:
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.clear()
                fast_api.LOCAL_BRIDGE_MIC_CONTROL_REQUEST.update(
                    original_request
                )
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.clear()
                fast_api.LOCAL_BRIDGE_MIC_ENABLE_FENCE.update(
                    original_fence
                )

        self.assertFalse(published["enabled"])
        self.assertTrue(recovered.public_status()["revocationFenced"])

    async def test_validation_chat_handler_skips_side_effect_hooks_until_terminal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-chat-bridge",
                turn_id="validation-chat-turn",
            )
            forbidden_hooks = (
                Mock(side_effect=AssertionError("memory confirmation ran")),
                AsyncMock(side_effect=AssertionError("tool planner ran")),
                AsyncMock(side_effect=AssertionError("pre-LLM hook ran")),
                Mock(side_effect=AssertionError("tool background hook ran")),
                Mock(side_effect=AssertionError("background hook ran")),
                Mock(side_effect=AssertionError("background action launched")),
            )
            validation_llm = AsyncMock(return_value="검증 응답")
            chat_append = Mock(
                side_effect=AssertionError("validation chat was persisted")
            )
            issued_response = None
            response = None
            try:
                with _validation_runtime_patches(
                    turn,
                    forbidden_hooks,
                    should_queue_local_bridge_speech=Mock(return_value=False),
                    ask_main_llm=validation_llm,
                    append_chat_message=chat_append,
                    cached_fast_runtime_health=AsyncMock(
                        side_effect=AssertionError(
                            "validation runtime provider ran"
                        )
                    ),
                    build_control_state=Mock(return_value={}),
                    commit_fast_control_turn=Mock(
                        return_value={"durable": True}
                    ),
                ):
                    issued_response, issued = (
                        await _issue_local_validation_turn(turn)
                    )
                    self.assertEqual(issued_response.status, 200, issued)
                    self.assertEqual(
                        turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        ).get("error"),
                        "validation_attempt_inflight",
                    )
                    issued_response._run_after_write()

                    response = await fast_api.chat_handler(
                        _JsonRequest(_validation_chat_payload(turn, issued))
                    )
                    self.assertIsInstance(
                        response,
                        fast_api.MemoryGuardedJsonResponse,
                    )
                    self.assertEqual(json.loads(response.text)["reply"], "검증 응답")
                    self.assertEqual(
                        turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        ).get("error"),
                        "validation_attempt_inflight",
                    )
                    for hook in forbidden_hooks:
                        hook.assert_not_called()
                    validation_llm.assert_awaited_once_with(
                        issued["forwardText"],
                        source="local_bridge",
                        isolated_validation=True,
                    )
                    chat_append.assert_not_called()

                    response._run_before_write()
                    response._run_after_write()
                    entry_id = fast_api.conversation_ingress_entry_id(
                        surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                        scope=fast_api.FAST_CONTROL_SESSION_KEY,
                        source_delivery_id=json.dumps(
                            [turn.bridge_id, turn.turn_id],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                    record = turn.owner.ingress_record(entry_id)
                    self.assertEqual(record["phase"], "completed")
                    self.assertNotEqual(
                        record["assistantText"],
                        "검증 응답",
                    )
                    self.assertEqual(turn.owner.restored_chat_messages(), [])
                    self.assertEqual(fast_api.CHAT_MESSAGES, [])
                    self.assertEqual(
                        turn.manager.snapshot()["currentStep"]["events"][
                            "turn_accepted"
                        ],
                        1,
                    )
                    aborted = turn.manager.abort(
                        session_id=turn.session["sessionId"]
                    )
                    self.assertTrue(aborted["ok"], aborted)
                    self.assertEqual(aborted["session"]["state"], "aborted")
                    for hook in forbidden_hooks:
                        hook.assert_not_called()
            finally:
                if issued_response is not None:
                    issued_response._run_after_terminal()
                if response is not None:
                    response._run_after_terminal()

    async def test_validation_event_write_failure_terminalizes_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-event-failure-bridge",
                turn_id="validation-event-failure-turn",
            )
            forbidden_hooks = tuple(Mock() for _ in range(6))
            validation_llm = AsyncMock(return_value="검증 응답")
            event_writer = Mock(
                side_effect=OSError("PRIVATE_VALIDATION_EVENT_CANARY")
            )
            issued_response = None
            response = None
            retry_response = None
            try:
                with (
                    _validation_runtime_patches(
                        turn,
                        forbidden_hooks,
                        emit_voice_validation_event=event_writer,
                        should_queue_local_bridge_speech=Mock(
                            return_value=False
                        ),
                        ask_main_llm=validation_llm,
                        cached_fast_runtime_health=AsyncMock(
                            side_effect=AssertionError(
                                "validation runtime provider ran"
                            )
                        ),
                        append_chat_message=Mock(
                            side_effect=AssertionError(
                                "validation chat was persisted"
                            )
                        ),
                        build_control_state=Mock(return_value={}),
                        commit_fast_control_turn=Mock(
                            return_value={"durable": True}
                        ),
                    ),
                    patch.object(
                        turn.owner,
                        "claim_reserved_ingress",
                        wraps=turn.owner.claim_reserved_ingress,
                    ) as durable_claim,
                    patch("builtins.print") as safe_log,
                ):
                    issued_response, issued = (
                        await _issue_local_validation_turn(turn)
                    )
                    self.assertEqual(issued_response.status, 200, issued)
                    issued_response._run_after_write()
                    chat_payload = _validation_chat_payload(turn, issued)

                    response = await fast_api.chat_handler(
                        _JsonRequest(chat_payload)
                    )
                    result = json.loads(response.text)
                    self.assertEqual(response.status, 200, result)
                    self.assertEqual(result["reply"], "검증 응답")
                    self.assertNotIn(
                        "PRIVATE_VALIDATION_EVENT_CANARY",
                        response.text,
                    )
                    response._run_before_write()
                    response._run_after_write()

                    retry_response = await fast_api.chat_handler(
                        _JsonRequest(chat_payload)
                    )
                    retry = json.loads(retry_response.text)
                    self.assertEqual(retry_response.status, 409, retry)
                    self.assertEqual(retry["reason"], "admission_token_reused")
                    self.assertNotIn(
                        "PRIVATE_VALIDATION_EVENT_CANARY",
                        retry_response.text,
                    )
                    retry_response._run_after_terminal()

                    entry_id = fast_api.conversation_ingress_entry_id(
                        surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                        scope=fast_api.FAST_CONTROL_SESSION_KEY,
                        source_delivery_id=json.dumps(
                            [turn.bridge_id, turn.turn_id],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                    record = turn.owner.ingress_record(entry_id)
                    self.assertEqual(record["phase"], "completed")
                    self.assertEqual(record["acceptedText"], issued["forwardText"])
                    self.assertEqual(durable_claim.call_count, 1)
                    validation_llm.assert_awaited_once()
                    self.assertEqual(
                        turn.admission.public_status()["acceptedCount"],
                        1,
                    )
                    self.assertEqual(
                        turn.manager.snapshot()["currentStep"]["events"].get(
                            "turn_accepted",
                            0,
                        ),
                        0,
                    )
                    log_text = " ".join(
                        str(value)
                        for call in safe_log.call_args_list
                        for value in call.args
                    )
                    self.assertIn("errorType=OSError", log_text)
                    self.assertNotIn(
                        "PRIVATE_VALIDATION_EVENT_CANARY",
                        log_text,
                    )
            finally:
                if issued_response is not None:
                    issued_response._run_after_terminal()
                if response is not None:
                    response._run_after_terminal()
                if retry_response is not None:
                    retry_response._run_after_terminal()

    async def test_validation_rejection_holds_lease_until_real_http_eof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-reject-bridge",
                turn_id="validation-reject-turn",
            )
            forbidden_hooks = tuple(Mock() for _ in range(6))
            eof_entered = asyncio.Event()
            allow_eof = asyncio.Event()
            original_write_eof = (
                fast_api.MemoryGuardedJsonResponse.write_eof
            )

            async def blocked_write_eof(response, data=b""):
                if response.status == 409:
                    eof_entered.set()
                    await allow_eof.wait()
                await original_write_eof(response, data)

            app = web.Application()
            app.router.add_post("/chat", fast_api.chat_handler)
            client = TestClient(TestServer(app))
            issued_response = None
            request_task = None
            response = None
            await client.start_server()
            try:
                with (
                    _validation_runtime_patches(turn, forbidden_hooks),
                    patch.object(
                        fast_api.MemoryGuardedJsonResponse,
                        "write_eof",
                        new=blocked_write_eof,
                    ),
                ):
                    issued_response, issued = (
                        await _issue_local_validation_turn(turn)
                    )
                    self.assertEqual(issued_response.status, 200, issued)
                    issued_response._run_after_write()
                    rejected_payload = _validation_chat_payload(turn, issued)
                    rejected_payload["text"] = "검증 문장 불일치"
                    request_task = asyncio.create_task(
                        client.post("/chat", json=rejected_payload)
                    )
                    await asyncio.wait_for(eof_entered.wait(), timeout=2)

                    self.assertEqual(
                        turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        ).get("error"),
                        "validation_attempt_inflight",
                    )

                    allow_eof.set()
                    response = await asyncio.wait_for(request_task, timeout=2)
                    payload = await asyncio.wait_for(response.json(), timeout=2)
                    self.assertEqual(response.status, 409)
                    self.assertFalse(payload["admitted"])
                    aborted = turn.manager.abort(
                        session_id=turn.session["sessionId"]
                    )
                    self.assertTrue(aborted["ok"], aborted)
            finally:
                allow_eof.set()
                if response is not None:
                    with suppress(Exception):
                        await response.read()
                if request_task is not None:
                    with suppress(Exception):
                        await request_task
                if issued_response is not None:
                    issued_response._run_after_terminal()
                await client.close()

    async def test_validation_issue_fence_rejection_holds_lease_until_eof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-issue-fence-bridge",
                turn_id="validation-issue-fence-turn",
            )
            eof_entered = asyncio.Event()
            allow_eof = asyncio.Event()
            original_write_eof = (
                fast_api.MemoryGuardedJsonResponse.write_eof
            )

            async def blocked_write_eof(response, data=b""):
                if response.status == 409:
                    eof_entered.set()
                    await allow_eof.wait()
                await original_write_eof(response, data)

            app = web.Application()
            app.router.add_post(
                "/admission",
                fast_api.local_voice_admission_handler,
            )
            client = TestClient(TestServer(app))
            request_task = None
            response = None
            await client.start_server()
            try:
                with (
                    _validation_runtime_patches(
                        turn,
                        tuple(Mock() for _ in range(6)),
                    ),
                    patch.object(
                        fast_api,
                        "local_voice_capture_fence_is_current",
                        return_value=False,
                    ),
                    patch.object(
                        fast_api.MemoryGuardedJsonResponse,
                        "write_eof",
                        new=blocked_write_eof,
                    ),
                ):
                    request_task = asyncio.create_task(
                        client.post(
                            "/admission",
                            json={
                                "bridgeInstanceId": turn.bridge_id,
                                "turnId": turn.turn_id,
                                "text": turn.prompt,
                                "validation": turn.binding,
                            },
                        )
                    )
                    await asyncio.wait_for(eof_entered.wait(), timeout=2)
                    self.assertEqual(
                        turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        ).get("error"),
                        "validation_attempt_inflight",
                    )

                    allow_eof.set()
                    response = await asyncio.wait_for(request_task, timeout=2)
                    payload = await asyncio.wait_for(
                        response.json(),
                        timeout=2,
                    )
                    self.assertEqual(response.status, 409, payload)
                    self.assertEqual(
                        payload["error"],
                        "voice_capture_consent_not_current",
                    )
                    aborted = turn.manager.abort(
                        session_id=turn.session["sessionId"]
                    )
                    self.assertTrue(aborted["ok"], aborted)
            finally:
                allow_eof.set()
                if response is not None:
                    with suppress(Exception):
                        await response.read()
                if request_task is not None:
                    with suppress(Exception):
                        await request_task
                await client.close()

    async def test_validation_consume_fence_503_holds_lease_until_eof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-consume-fence-bridge",
                turn_id="validation-consume-fence-turn",
            )
            forbidden_hooks = tuple(Mock() for _ in range(6))
            eof_entered = asyncio.Event()
            allow_eof = asyncio.Event()
            original_write_eof = (
                fast_api.MemoryGuardedJsonResponse.write_eof
            )

            async def blocked_write_eof(response, data=b""):
                if response.status == 503:
                    eof_entered.set()
                    await allow_eof.wait()
                await original_write_eof(response, data)

            app = web.Application()
            app.router.add_post("/chat", fast_api.chat_handler)
            client = TestClient(TestServer(app))
            issued_response = None
            request_task = None
            response = None
            await client.start_server()
            try:
                with _validation_runtime_patches(turn, forbidden_hooks):
                    issued_response, issued = (
                        await _issue_local_validation_turn(turn)
                    )
                    self.assertEqual(issued_response.status, 200, issued)
                    issued_response._run_after_write()

                    with (
                        patch.object(
                            fast_api,
                            "local_voice_capture_fence_is_current",
                            return_value=False,
                        ),
                        patch.object(
                            fast_api,
                            "_reset_local_voice_admission",
                            side_effect=OSError("simulated revoke failure"),
                        ),
                        patch.object(
                            fast_api.MemoryGuardedJsonResponse,
                            "write_eof",
                            new=blocked_write_eof,
                        ),
                    ):
                        request_task = asyncio.create_task(
                            client.post(
                                "/chat",
                                json=_validation_chat_payload(turn, issued),
                            )
                        )
                        await asyncio.wait_for(
                            eof_entered.wait(),
                            timeout=2,
                        )
                        self.assertEqual(
                            turn.manager.abort(
                                session_id=turn.session["sessionId"]
                            ).get("error"),
                            "validation_attempt_inflight",
                        )

                        allow_eof.set()
                        response = await asyncio.wait_for(
                            request_task,
                            timeout=2,
                        )
                        payload = await asyncio.wait_for(
                            response.json(),
                            timeout=2,
                        )
                        self.assertEqual(response.status, 503, payload)
                        self.assertEqual(
                            payload["error"],
                            "local_voice_reservation_revocation_failed",
                        )
                        aborted = turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        )
                        self.assertTrue(aborted["ok"], aborted)
            finally:
                allow_eof.set()
                if response is not None:
                    with suppress(Exception):
                        await response.read()
                if request_task is not None:
                    with suppress(Exception):
                        await request_task
                if issued_response is not None:
                    issued_response._run_after_terminal()
                await client.close()

    async def test_validation_json_success_holds_lease_until_real_http_eof(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-json-bridge",
                turn_id="validation-json-turn",
            )
            forbidden_hooks = tuple(Mock() for _ in range(6))
            eof_entered = asyncio.Event()
            allow_eof = asyncio.Event()
            original_write_eof = (
                fast_api.MemoryGuardedJsonResponse.write_eof
            )

            async def blocked_write_eof(response, data=b""):
                if response.status == 200:
                    eof_entered.set()
                    await allow_eof.wait()
                await original_write_eof(response, data)

            app = web.Application()
            app.router.add_post("/chat", fast_api.chat_handler)
            client = TestClient(TestServer(app))
            issued_response = None
            request_task = None
            response = None
            await client.start_server()
            try:
                with (
                    _validation_runtime_patches(
                        turn,
                        forbidden_hooks,
                        should_queue_local_bridge_speech=Mock(
                            return_value=False
                        ),
                        ask_main_llm=AsyncMock(return_value="검증 응답"),
                        cached_fast_runtime_health=AsyncMock(
                            side_effect=AssertionError(
                                "validation runtime provider ran"
                            )
                        ),
                        append_chat_message=Mock(
                            side_effect=AssertionError(
                                "validation chat was persisted"
                            )
                        ),
                    ),
                    patch.object(
                        fast_api.MemoryGuardedJsonResponse,
                        "write_eof",
                        new=blocked_write_eof,
                    ),
                ):
                    issued_response, issued = (
                        await _issue_local_validation_turn(turn)
                    )
                    issued_response._run_after_write()
                    request_task = asyncio.create_task(
                        client.post(
                            "/chat",
                            json=_validation_chat_payload(turn, issued),
                        )
                    )
                    await asyncio.wait_for(eof_entered.wait(), timeout=2)
                    self.assertEqual(
                        turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        ).get("error"),
                        "validation_attempt_inflight",
                    )

                    allow_eof.set()
                    response = await asyncio.wait_for(request_task, timeout=2)
                    result = await asyncio.wait_for(response.json(), timeout=2)
                    self.assertEqual(response.status, 200, result)
                    self.assertEqual(result["reply"], "검증 응답")
                    aborted = turn.manager.abort(
                        session_id=turn.session["sessionId"]
                    )
                    self.assertTrue(aborted["ok"], aborted)
            finally:
                allow_eof.set()
                if response is not None:
                    with suppress(Exception):
                        await response.read()
                if request_task is not None:
                    with suppress(Exception):
                        await request_task
                if issued_response is not None:
                    issued_response._run_after_terminal()
                await client.close()

    async def test_validation_partial_stream_holds_lease_and_skips_side_effects(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            turn = _start_local_validation_turn(
                Path(temporary),
                bridge_id="validation-stream-bridge",
                turn_id="validation-stream-turn",
            )
            partial_sent = asyncio.Event()
            fail_stream = asyncio.Event()

            async def broken_stream():
                yield "부분 검증 응답"
                partial_sent.set()
                await fail_stream.wait()
                raise RuntimeError("validation stream failed after first delta")

            forbidden_hooks = (
                Mock(side_effect=AssertionError("memory confirmation ran")),
                AsyncMock(side_effect=AssertionError("tool planner ran")),
                AsyncMock(side_effect=AssertionError("pre-LLM hook ran")),
                Mock(side_effect=AssertionError("tool background hook ran")),
                Mock(side_effect=AssertionError("background hook ran")),
                Mock(side_effect=AssertionError("background action launched")),
            )
            commit_turn = Mock(
                side_effect=AssertionError("partial stream committed")
            )
            app = web.Application()
            app.router.add_post("/chat", fast_api.chat_stream_handler)
            client = TestClient(TestServer(app))
            issued_response = None
            request_task = None
            response = None
            body = ""
            await client.start_server()
            try:
                with _validation_runtime_patches(
                    turn,
                    forbidden_hooks,
                    iter_main_llm_deltas=Mock(
                        side_effect=lambda *_args, **_kwargs: broken_stream()
                    ),
                    should_emit_memory_recall_progress=Mock(return_value=False),
                    commit_fast_control_turn=commit_turn,
                ):
                    try:
                        issued_response, issued = (
                            await _issue_local_validation_turn(turn)
                        )
                        self.assertEqual(issued_response.status, 200, issued)
                        issued_response._run_after_write()
                        request_task = asyncio.create_task(
                            client.post(
                                "/chat",
                                json=_validation_chat_payload(turn, issued),
                            )
                        )
                        await asyncio.wait_for(partial_sent.wait(), timeout=2)
                        response = await asyncio.wait_for(
                            asyncio.shield(request_task),
                            timeout=2,
                        )
                        self.assertEqual(
                            turn.manager.abort(
                                session_id=turn.session["sessionId"]
                            ).get("error"),
                            "validation_attempt_inflight",
                        )
                        for hook in forbidden_hooks:
                            hook.assert_not_called()

                        fail_stream.set()
                        body = await asyncio.wait_for(response.text(), timeout=2)
                        entry_id = fast_api.conversation_ingress_entry_id(
                            surface=fast_api.FAST_CONTROL_INGRESS_SURFACE,
                            scope=fast_api.FAST_CONTROL_SESSION_KEY,
                            source_delivery_id=json.dumps(
                                [turn.bridge_id, turn.turn_id],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        )
                        self.assertEqual(
                            turn.owner.ingress_record(entry_id)["phase"],
                            "completed",
                        )
                        self.assertEqual(
                            turn.owner.restored_chat_messages(),
                            [],
                        )
                        aborted = turn.manager.abort(
                            session_id=turn.session["sessionId"]
                        )
                        self.assertTrue(aborted["ok"], aborted)
                        self.assertEqual(aborted["session"]["state"], "aborted")
                        for hook in forbidden_hooks:
                            hook.assert_not_called()
                        commit_turn.assert_not_called()
                    finally:
                        fail_stream.set()
                        if response is not None:
                            with suppress(Exception):
                                await asyncio.wait_for(response.read(), timeout=2)
                        if request_task is not None:
                            with suppress(Exception):
                                await asyncio.wait_for(request_task, timeout=2)
            finally:
                if issued_response is not None:
                    issued_response._run_after_terminal()
                await client.close()

            events = [
                json.loads(line)
                for line in body.splitlines()
                if line.strip()
            ]
            self.assertTrue(any(event.get("type") == "delta" for event in events))
            self.assertFalse(
                any(event.get("type") in {"done", "error"} for event in events)
            )

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
        with tempfile.TemporaryDirectory() as temporary:
            owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
            )
            issued = _durably_issue_local_voice(
                manager,
                owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text="이블린, 저장 실패 뒤 재시도",
            )
            payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
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
            token = sys.argv[5]
            forward_text = sys.argv[6]
            admission_mode = sys.argv[7]
            capture_fence_digest = sys.argv[8]
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            manager = LocalVoiceAdmissionManager()
            request_id = json.dumps(
                [bridge_id, turn_id],
                ensure_ascii=False,
                separators=(",", ":"),
            )

            def claim_then_exit(claim_request):
                owner.claim_reserved_ingress(
                    request_id=request_id,
                    accepted_text=claim_request.forward_text,
                    turn_id=claim_request.ingress_turn_id,
                    reservation_ref=claim_request.reservation_ref,
                )
                os._exit(91)

            manager.consume_with_durable_claim(
                token,
                bridge_id,
                turn_id,
                forward_text,
                durable_claim=claim_then_exit,
                capture_fence_digest=capture_fence_digest,
                admission_mode=admission_mode,
                validation_binding={},
                validation_is_current=lambda binding: not binding,
                durable_recovery_is_current=lambda: True,
            )
            raise SystemExit(92)
            """
        )

        with tempfile.TemporaryDirectory() as temporary:
            issuing_owner = fast_api.FastControlContinuityOwner(
                artifacts_root=Path(temporary),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            issued = _durably_issue_local_voice(
                fast_api.LocalVoiceAdmissionManager(),
                issuing_owner,
                bridge_id=bridge_id,
                turn_id=turn_id,
                text=f"이블린, {forward_text}",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child,
                    str(RUNTIME_ROOT),
                    temporary,
                    bridge_id,
                    turn_id,
                    issued["admissionToken"],
                    issued["forwardText"],
                    issued["mode"],
                    CAPTURE_FENCE_DIGEST,
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
            recovered_payload = {
                "source": "local_bridge",
                "bridgeInstanceId": bridge_id,
                "turnId": turn_id,
                "text": issued["forwardText"],
                "admissionToken": issued["admissionToken"],
                "admissionMode": issued["mode"],
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
                patch.object(
                    fast_api,
                    "local_voice_recovery_context_is_current",
                    return_value=True,
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
                "memory_deletion_journal_read_guard",
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

    async def test_completed_retry_keeps_journal_busy_retryable(self) -> None:
        private_canary = "private replay detail"
        owner = _IngressOwner(
            {
                "entryId": "ingress-" + "4" * 64,
                "turnId": "journal-turn-4",
                "phase": "completed",
                "shouldProcess": False,
            }
        )
        owner.replay_record = {
            "entryId": owner.claim["entryId"],
            "phase": "completed",
            "assistantText": private_canary,
            "memoryReceiptRef": not_used_memory_receipt_ref(),
        }

        with patch.object(
            fast_api,
            "FAST_CONTROL_CONTINUITY_OWNER",
            owner,
        ), patch.object(
            fast_api,
            "memory_deletion_journal_read_guard",
            side_effect=MemoryDeletionJournalBusyError(
                private_canary
            ),
        ):
            with self.assertRaises(
                MemoryDeletionJournalBusyError
            ) as raised:
                fast_api._prepare_fast_control_ingress(
                    {"requestId": "browser-request-busy"},
                    accepted_text="질문",
                    source="control_page",
                )

        self.assertIs(
            type(raised.exception),
            MemoryDeletionJournalBusyError,
        )
        self.assertEqual(
            str(raised.exception),
            MEMORY_DELETION_JOURNAL_BUSY_ERROR,
        )
        self.assertNotIn(private_canary, str(raised.exception))

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
            after_terminal=lambda: calls.append("terminal"),
        )
        response._run_before_write()
        response._run_after_write_failure(
            "conversation_ingress_delivery_disconnected"
        )
        response._run_after_write()
        self.assertEqual(
            calls,
            [
                "before",
                "conversation_ingress_delivery_disconnected",
                "terminal",
            ],
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
