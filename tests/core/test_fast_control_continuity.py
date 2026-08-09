from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.continuity_commit_contract import (  # noqa: E402
    require_durable_continuity_receipt,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressBindingMismatch,
    ConversationIngressRecoveryError,
    final_text_sha256,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
)
from evelyn_core.fast_control_continuity import (  # noqa: E402
    FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
    FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF,
    FAST_CONTROL_LOCAL_PLAYBACK_DELIVERY_REF,
    FAST_CONTROL_SESSION_KEY,
    FastControlContinuityOwner,
)


NOTE_A = "concept-0123456789abcdef"
NOTE_B = "concept-fedcba9876543210"


def full_receipt(note_id: str, *, version: int) -> dict:
    return {
        "schema": "memory.context-receipt.v1",
        "state": "provided",
        "groundingState": "attributed",
        "memoryVersion": version,
        "suppliedNoteIds": [note_id],
        "suppliedNoteCount": 1,
        "contentFree": True,
    }


class FastControlContinuityTests(unittest.TestCase):
    @staticmethod
    def _without_restore_time(
        messages: list[dict],
    ) -> list[dict]:
        return [
            {key: value for key, value in message.items() if key != "at"}
            for message in messages
        ]

    @staticmethod
    def _deliver_ingress(
        owner: FastControlContinuityOwner,
        *,
        request_id: str = "request-1",
        user_text: str = "질문",
        assistant_text: str = "답변",
    ) -> dict:
        claim = owner.claim_ingress(
            request_id=request_id,
            accepted_text=user_text,
        )
        owner.bind_ingress_response(
            claim["entryId"],
            assistant_text=assistant_text,
            memory_receipt_ref=not_used_memory_receipt_ref(),
        )
        owner.mark_ingress_delivery_inflight(
            claim["entryId"],
            delivery_ref="test:http",
        )
        owner.mark_ingress_delivery_succeeded(
            claim["entryId"],
            delivery_ref="test:http",
        )
        return claim

    @staticmethod
    def _start_ephemeral_delivery(
        owner: FastControlContinuityOwner,
        *,
        request_id: str,
        user_text: str,
    ) -> dict:
        claim = owner.claim_ingress(
            request_id=request_id,
            accepted_text=user_text,
        )
        owner.mark_ingress_delivery_inflight(
            claim["entryId"],
            delivery_ref=FAST_CONTROL_EPHEMERAL_VALIDATION_DELIVERY_REF,
            streaming=True,
        )
        return claim

    def test_disabled_owner_never_creates_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=False,
            )

            self.assertEqual(owner.restored_chat_messages(), [])
            self.assertEqual(owner.status()["state"], "disabled")
            self.assertFalse(
                (root / "fast_control_continuity").exists()
            )

    def test_completed_turn_restores_after_owner_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            raw_status = first.record_completed_turn(
                "실패 전 질문",
                "고정 실패 응답",
            )
            receipt = require_durable_continuity_receipt(
                raw_status
            )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = second.restored_chat_messages()
            checkpoint_text = (
                root
                / "fast_control_continuity"
                / "active.json"
            ).read_text(encoding="utf-8")

        self.assertTrue(receipt["durable"])
        self.assertGreaterEqual(receipt["generation"], 1)
        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in restored
            ],
            [
                ("user", "실패 전 질문"),
                ("assistant", "고정 실패 응답"),
            ],
        )
        self.assertEqual(
            second.restore_status["state"],
            "restored",
        )
        self.assertNotIn(
            "fast-control short-lived conversation continuity",
            checkpoint_text,
        )

    def test_failed_ingress_restores_only_the_unanswered_user_turn(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            claim = owner.claim_ingress(
                request_id="local-playback-failed",
                accepted_text="실패해도 이 말은 이어가줘",
            )
            owner.bind_ingress_response(
                claim["entryId"],
                assistant_text="재생되지 않은 답변",
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )
            owner.mark_ingress_delivery_inflight(
                claim["entryId"],
                delivery_ref=FAST_CONTROL_LOCAL_PLAYBACK_DELIVERY_REF,
            )
            owner.mark_ingress_delivery_ambiguous(
                claim["entryId"],
                error_code="conversation_ingress_delivery_failed",
            )
            with patch.object(
                owner.ingress,
                "discard_ambiguous",
                side_effect=RuntimeError("simulated journal write failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "simulated journal write failure",
                ):
                    owner.discard_failed_ingress(
                        claim["entryId"],
                        assistant_hash=final_text_sha256(
                            "재생되지 않은 답변"
                        ).upper(),
                    )

            restored_owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = restored_owner.restored_chat_messages()
            unrelated = restored_owner.claim_ingress(
                request_id="local-playback-next",
                accepted_text="다음 요청",
            )

        self.assertEqual(
            [(item["role"], item["text"]) for item in restored],
            [("user", "실패해도 이 말은 이어가줘")],
        )
        self.assertIsNone(restored_owner.ingress_record(claim["entryId"]))
        self.assertTrue(unrelated["shouldProcess"])

    def test_failed_ingress_rejects_wrong_assistant_hash_before_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            claim = owner.claim_ingress(
                request_id="local-playback-wrong-hash",
                accepted_text="결합이 틀리면 저장하지 마",
            )
            owner.bind_ingress_response(
                claim["entryId"],
                assistant_text="결합된 답변",
                memory_receipt_ref=not_used_memory_receipt_ref(),
            )
            owner.mark_ingress_delivery_inflight(
                claim["entryId"],
                delivery_ref=FAST_CONTROL_LOCAL_PLAYBACK_DELIVERY_REF,
            )
            owner.mark_ingress_delivery_ambiguous(
                claim["entryId"],
                error_code="conversation_ingress_delivery_failed",
            )

            with self.assertRaises(ConversationIngressBindingMismatch):
                owner.discard_failed_ingress(
                    claim["entryId"],
                    assistant_hash="0" * 64,
                )

            self.assertEqual(owner.restored_chat_messages(), [])
            self.assertIsNotNone(owner.ingress_record(claim["entryId"]))

    def test_background_followup_preserves_exact_message_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            first.record_completed_turn(
                "긴 작업 해줘",
                "작업을 시작했어.",
            )
            first.record_assistant_followup(
                "작업을 완료했어."
            )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = second.restored_chat_messages()

        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in restored
            ],
            [
                ("user", "긴 작업 해줘"),
                ("assistant", "작업을 시작했어."),
                ("assistant", "작업을 완료했어."),
            ],
        )

    def test_receipts_restore_on_completed_turn_and_followup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            first.record_completed_turn(
                "기억 질문",
                "기억 답",
                memory_receipt=full_receipt(
                    NOTE_A,
                    version=4,
                ),
            )
            first.record_assistant_followup(
                "추가 기억 답",
                memory_receipt=full_receipt(
                    NOTE_B,
                    version=5,
                ),
            )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = second.restored_chat_messages()
            status = second.status()

        assistants = [
            item
            for item in restored
            if item["role"] == "assistant"
        ]
        self.assertEqual(
            assistants[0]["memoryReceiptRef"][
                "suppliedNoteIds"
            ],
            [NOTE_A],
        )
        self.assertEqual(
            assistants[1]["memoryReceiptRef"][
                "suppliedNoteIds"
            ],
            [NOTE_B],
        )
        rendered_status = json.dumps(
            status,
            ensure_ascii=False,
        )
        self.assertNotIn(NOTE_A, rendered_status)
        self.assertNotIn(NOTE_B, rendered_status)

    def test_status_is_content_free_and_exact(self) -> None:
        private_user = (
            "Bearer fast-control-user-secret "
            r"C:\Users\Admin\private.txt"
        )
        private_answer = (
            "https://internal.example/answer"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            owner.record_completed_turn(
                private_user,
                private_answer,
            )
            status = owner.status()

        rendered = json.dumps(status, ensure_ascii=False)
        self.assertEqual(
            status["schema"],
            FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
        )
        self.assertTrue(status["enabled"])
        self.assertTrue(status["durableReady"])
        self.assertEqual(status["messageCount"], 2)
        self.assertTrue(status["policy"]["contentFree"])
        self.assertNotIn("fast-control-user-secret", rendered)
        self.assertNotIn("internal.example", rendered)
        self.assertNotIn("Users", rendered)

    def test_corrupt_checkpoint_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            continuity_root = (
                root / "fast_control_continuity"
            )
            continuity_root.mkdir(parents=True)
            (continuity_root / "active.json").write_text(
                "{broken",
                encoding="utf-8",
            )

            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(owner.restored_chat_messages(), [])
        self.assertEqual(
            owner.restore_status["state"],
            "error",
        )
        self.assertEqual(
            owner.restore_status["lastErrorCode"],
            "conversation_continuity_restore_failed",
        )

    def test_history_is_bounded_to_configured_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owner = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                max_history_items=4,
                log=lambda *_args, **_kwargs: None,
            )
            for index in range(4):
                owner.record_completed_turn(
                    f"user-{index}",
                    f"answer-{index}",
                )

            restored = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                max_history_items=4,
                log=lambda *_args, **_kwargs: None,
            ).restored_chat_messages()

        self.assertEqual(
            [
                (item["role"], item["text"])
                for item in restored
            ],
            [
                ("user", "user-2"),
                ("assistant", "answer-2"),
                ("user", "user-3"),
                ("assistant", "answer-3"),
            ],
        )

    def test_ingress_bootstrap_is_rollback_protected_and_claim_is_stable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            journal_status = owner.ingress.public_status()
            first = owner.claim_ingress(
                request_id="stable-request",
                accepted_text="같은 질문",
            )
            duplicate = owner.claim_ingress(
                request_id="stable-request",
                accepted_text="같은 질문",
            )
            with self.assertRaises(ConversationIngressBindingMismatch):
                owner.claim_ingress(
                    request_id="stable-request",
                    accepted_text="다른 질문",
                )
            with self.assertRaisesRegex(
                ConversationIngressRecoveryError,
                "conversation_ingress_recovery_pending",
            ):
                owner.claim_ingress(
                    request_id="later-request",
                    accepted_text="후속 질문",
                )

        self.assertEqual(journal_status["generation"], 1)
        self.assertTrue(journal_status["rollbackProtected"])
        self.assertTrue(first["shouldProcess"])
        self.assertFalse(duplicate["shouldProcess"])
        self.assertEqual(first["entryId"], duplicate["entryId"])

    def test_reserved_ingress_is_not_recovery_work_and_promotes_exactly_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            first.reserve_ingress(
                request_id="local:turn-1",
                text_hash=final_text_sha256("첫 음성 입력"),
                turn_id="local-turn-1",
                reservation_ref="d" * 64,
                ttl_sec=10.0,
            )

            restored = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored.reserve_ingress(
                request_id="local:turn-2",
                text_hash=final_text_sha256("두 번째 음성 입력"),
                turn_id="local-turn-2",
                reservation_ref="e" * 64,
                ttl_sec=10.0,
            )
            context = restored.recovered_ingress_context_messages()
            projection = restored.ingress_recovery_projection()
            promoted = restored.claim_reserved_ingress(
                request_id="local:turn-1",
                accepted_text="첫 음성 입력",
                turn_id="local-turn-1",
                reservation_ref="d" * 64,
            )
            duplicate = restored.claim_reserved_ingress(
                request_id="local:turn-1",
                accepted_text="첫 음성 입력",
                turn_id="local-turn-1",
                reservation_ref="d" * 64,
            )
            with self.assertRaisesRegex(
                ConversationIngressRecoveryError,
                "conversation_ingress_recovery_pending",
            ):
                restored.claim_reserved_ingress(
                    request_id="local:turn-2",
                    accepted_text="두 번째 음성 입력",
                    turn_id="local-turn-2",
                    reservation_ref="e" * 64,
                )

        self.assertEqual(context, [])
        self.assertEqual(projection["pendingCount"], 0)
        self.assertTrue(promoted["shouldProcess"])
        self.assertFalse(duplicate["shouldProcess"])

    def test_reserved_ingress_batch_revocation_uses_exact_request_binding(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            reservations = [
                {
                    "request_id": f"local:turn-{index}",
                    "text_hash": final_text_sha256(f"voice-{index}"),
                    "turn_id": f"local-turn-{index}",
                    "reservation_ref": str(index) * 64,
                    "ttl_sec": 10.0,
                }
                for index in (1, 2)
            ]
            receipts = [
                owner.reserve_ingress(**reservation)
                for reservation in reservations
            ]

            revoked = owner.revoke_reserved_ingress_batch(reservations)

        self.assertEqual(revoked["revokedCount"], 2)
        self.assertTrue(revoked["durable"])
        self.assertTrue(
            all(owner.ingress_record(item["entryId"]) is None for item in receipts)
        )

    def test_local_voice_scope_revocation_is_durable_and_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            receipt = owner.reserve_ingress(
                request_id="local:orphan-turn",
                text_hash=final_text_sha256("저장하지 않을 예약 원문"),
                turn_id="local-orphan-turn",
                reservation_ref="a" * 64,
                ttl_sec=10.0,
            )
            generation = owner.ingress.public_status()["generation"]

            revoked = owner.revoke_reserved_local_voice_ingress()
            no_op = owner.revoke_reserved_local_voice_ingress()

        self.assertEqual(revoked["revokedCount"], 1)
        self.assertEqual(revoked["journalGeneration"], generation + 1)
        self.assertIsNone(owner.ingress_record(receipt["entryId"]))
        self.assertEqual(no_op["revokedCount"], 0)
        self.assertEqual(no_op["journalGeneration"], generation + 1)

    def test_ephemeral_ingress_advances_checkpoint_without_history_or_replay(
        self,
    ) -> None:
        validation_user = "검증 사용자 원문은 checkpoint에 남지 않는다"
        validation_assistant = "검증 답변 원문도 보존하지 않는다"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            first.record_completed_turn("일반 질문", "일반 답변")
            baseline_history = json.dumps(
                first.store.histories[FAST_CONTROL_SESSION_KEY],
                ensure_ascii=False,
                sort_keys=True,
            )
            baseline_messages = first.restored_chat_messages()
            baseline_generation = first.status()["generation"]
            claim = self._start_ephemeral_delivery(
                first,
                request_id="validation-request",
                user_text=validation_user,
            )

            completed = first.complete_ephemeral_ingress(
                claim["entryId"],
                assistant_text=validation_assistant,
                memory_receipt_ref=not_used_memory_receipt_ref(
                    memory_version=41
                ),
            )
            record = first.ingress_record(claim["entryId"])
            active_json = (
                root / "fast_control_continuity" / "active.json"
            ).read_text(encoding="utf-8")
            ingress_json = (
                root / "fast_control_continuity" / "ingress.json"
            ).read_text(encoding="utf-8")
            with self.assertRaises(ConversationIngressBindingMismatch):
                first.complete_ephemeral_ingress(
                    claim["entryId"],
                    assistant_text="다른 검증 답변",
                )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored_record = second.ingress_record(claim["entryId"])

        self.assertTrue(completed["ephemeral"])
        self.assertTrue(completed["rollbackProtected"])
        self.assertEqual(
            completed["ingressReceipt"]["phase"],
            "completed",
        )
        self.assertFalse(completed["ingressReceipt"]["replayable"])
        self.assertEqual(record["phase"], "completed")
        self.assertNotEqual(record["assistantText"], validation_assistant)
        self.assertNotIn(validation_assistant, record["assistantText"])
        self.assertTrue(
            record["assistantText"].endswith(
                final_text_sha256(validation_assistant)
            )
        )
        self.assertEqual(
            record["memoryReceiptRef"]["state"],
            "unattributed",
        )
        self.assertEqual(first.status()["generation"], baseline_generation + 1)
        self.assertEqual(
            json.dumps(
                first.store.histories[FAST_CONTROL_SESSION_KEY],
                ensure_ascii=False,
                sort_keys=True,
            ),
            baseline_history,
        )
        self.assertEqual(
            self._without_restore_time(second.restored_chat_messages()),
            self._without_restore_time(baseline_messages),
        )
        self.assertEqual(restored_record["phase"], "completed")
        self.assertEqual(second.ingress_recovery_projection()["pendingCount"], 0)
        self.assertNotIn(validation_user, active_json)
        self.assertNotIn(validation_assistant, active_json)
        self.assertNotIn(validation_assistant, ingress_json)

    def test_ephemeral_delivery_ref_recovers_crash_before_response_bind(
        self,
    ) -> None:
        validation_user = "bind 전에 중단된 검증 입력"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            first.record_completed_turn("기존 질문", "기존 답변")
            baseline_messages = first.restored_chat_messages()
            baseline_generation = first.status()["generation"]
            claim = self._start_ephemeral_delivery(
                first,
                request_id="validation-before-bind",
                user_text=validation_user,
            )
            inflight = first.ingress_record(claim["entryId"])

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            recovered = second.ingress_record(claim["entryId"])
            active_json = (
                root / "fast_control_continuity" / "active.json"
            ).read_text(encoding="utf-8")
            with self.assertRaisesRegex(
                ConversationIngressRecoveryError,
                "conversation_ingress_replay_unattributed",
            ):
                second.ingress_record(claim["entryId"], replay=True)

        self.assertEqual(inflight["phase"], "delivery_inflight")
        self.assertEqual(inflight["assistantText"], "")
        self.assertEqual(recovered["phase"], "completed")
        self.assertTrue(recovered["assistantText"].startswith("validation-"))
        self.assertNotIn(validation_user, recovered["assistantText"])
        self.assertEqual(
            recovered["memoryReceiptRef"]["state"],
            "unattributed",
        )
        self.assertFalse(recovered["replayable"])
        self.assertEqual(second.status()["generation"], baseline_generation + 1)
        self.assertEqual(
            self._without_restore_time(second.restored_chat_messages()),
            self._without_restore_time(baseline_messages),
        )
        self.assertEqual(
            second.ingress_recovery_status["reconciledCount"],
            1,
        )
        self.assertNotIn(validation_user, active_json)

    def test_ephemeral_completion_rejects_noncanonical_delivery_ref(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            generation = owner.status()["generation"]
            claim = owner.claim_ingress(
                request_id="not-validation-delivery",
                accepted_text="일반 입력",
            )
            owner.mark_ingress_delivery_inflight(
                claim["entryId"],
                delivery_ref="fast-control:http-ndjson",
                streaming=True,
            )

            with self.assertRaisesRegex(
                ConversationIngressBindingMismatch,
                "conversation_ingress_delivery_binding_mismatch",
            ):
                owner.complete_ephemeral_ingress(
                    claim["entryId"],
                    assistant_text="잘못 분류하면 안 되는 답변",
                )
            record = owner.ingress_record(claim["entryId"])

        self.assertEqual(record["phase"], "delivery_inflight")
        self.assertEqual(record["assistantText"], "")
        self.assertEqual(owner.status()["generation"], generation)

    def test_ephemeral_ingress_reconciles_both_terminal_crash_boundaries(
        self,
    ) -> None:
        for boundary in ("before_checkpoint", "after_checkpoint"):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                root = Path(temp_dir)
                first = FastControlContinuityOwner(
                    artifacts_root=root,
                    enabled=True,
                    log=lambda *_args, **_kwargs: None,
                )
                first.record_completed_turn("보존할 일반 질문", "보존할 일반 답변")
                baseline_messages = first.restored_chat_messages()
                claim = self._start_ephemeral_delivery(
                    first,
                    request_id=f"validation-{boundary}",
                    user_text=f"검증 입력 {boundary}",
                )
                target = (
                    first.checkpoint
                    if boundary == "before_checkpoint"
                    else first.ingress
                )
                method = (
                    "flush"
                    if boundary == "before_checkpoint"
                    else "complete"
                )
                with patch.object(
                    target,
                    method,
                    side_effect=OSError(f"crash {boundary}"),
                ):
                    with self.assertRaises(OSError):
                        first.complete_ephemeral_ingress(
                            claim["entryId"],
                            assistant_text=f"검증 답변 {boundary}",
                        )
                pending = first.ingress_record(claim["entryId"])

                second = FastControlContinuityOwner(
                    artifacts_root=root,
                    enabled=True,
                    log=lambda *_args, **_kwargs: None,
                )
                recovered = second.ingress_record(claim["entryId"])

                self.assertEqual(pending["phase"], "terminal_committing")
                self.assertEqual(recovered["phase"], "completed")
                self.assertEqual(
                    self._without_restore_time(
                        second.restored_chat_messages()
                    ),
                    self._without_restore_time(baseline_messages),
                )
                self.assertEqual(
                    second.ingress_recovery_status["reconciledCount"],
                    1,
                )

    def test_ingress_terminal_order_and_authoritative_turn_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            owner = FastControlContinuityOwner(
                artifacts_root=Path(temp_dir),
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            claim = self._deliver_ingress(owner)
            events: list[str] = []
            begin = owner.ingress.begin_terminal_commit
            commit = owner.checkpoint.commit_completed_turn
            complete = owner.ingress.complete

            def record_begin(*args, **kwargs):
                events.append("begin_terminal_commit")
                return begin(*args, **kwargs)

            def record_commit(*args, **kwargs):
                events.append("checkpoint_commit")
                return commit(*args, **kwargs)

            def record_complete(*args, **kwargs):
                events.append("complete")
                return complete(*args, **kwargs)

            with (
                patch.object(
                    owner.ingress,
                    "begin_terminal_commit",
                    side_effect=record_begin,
                ),
                patch.object(
                    owner.checkpoint,
                    "commit_completed_turn",
                    side_effect=record_commit,
                ),
                patch.object(
                    owner.ingress,
                    "complete",
                    side_effect=record_complete,
                ),
            ):
                owner.record_completed_turn(
                    "질문",
                    "답변",
                    memory_receipt=not_used_memory_receipt_ref(),
                    ingress_entry_id=claim["entryId"],
                )

            record = owner.ingress_record(claim["entryId"])
            later = owner.claim_ingress(
                request_id="request-2",
                accepted_text="후속 질문",
            )

        self.assertEqual(
            events,
            [
                "begin_terminal_commit",
                "checkpoint_commit",
                "complete",
            ],
        )
        self.assertEqual(record["phase"], "completed")
        self.assertEqual(
            owner.store.current_turn_id("fast-control:control-page:owner"),
            claim["turnId"],
        )
        self.assertTrue(later["shouldProcess"])

    def test_post_delivery_commit_gap_blocks_new_claim_and_reconciles_once(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            claim = self._deliver_ingress(first)
            with patch.object(
                first.ingress,
                "complete",
                side_effect=OSError("simulated crash after checkpoint"),
            ):
                with self.assertRaises(OSError):
                    first.record_completed_turn(
                        "질문",
                        "답변",
                        memory_receipt=not_used_memory_receipt_ref(),
                        ingress_entry_id=claim["entryId"],
                    )
            with self.assertRaisesRegex(
                ConversationIngressRecoveryError,
                "conversation_ingress_recovery_pending",
            ):
                first.claim_ingress(
                    request_id="too-early",
                    accepted_text="순서가 뒤집히면 안 돼",
                )

            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            restored = second.restored_chat_messages()
            recovered_record = second.ingress_record(claim["entryId"])

        self.assertEqual(recovered_record["phase"], "completed")
        self.assertEqual(
            [(item["role"], item["text"]) for item in restored],
            [("user", "질문"), ("assistant", "답변")],
        )

    def test_recovered_pending_context_is_private_user_only_and_blocks_order(
        self,
    ) -> None:
        private_text = "재시작 전 미완료 사용자 요청"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            claim = first.claim_ingress(
                request_id="private-source-delivery",
                accepted_text=private_text,
            )
            second = FastControlContinuityOwner(
                artifacts_root=root,
                enabled=True,
                log=lambda *_args, **_kwargs: None,
            )
            context = second.recovered_ingress_context_messages()
            rendered_status = json.dumps(
                second.status(),
                ensure_ascii=False,
            )
            with self.assertRaisesRegex(
                ConversationIngressRecoveryError,
                "conversation_ingress_recovery_pending",
            ):
                second.claim_ingress(
                    request_id="new-source-delivery",
                    accepted_text="새 요청",
                )

        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["role"], "user")
        self.assertEqual(context[0]["content"], private_text)
        self.assertTrue(context[0]["_ingressRecoveryUnanswered"])
        self.assertNotIn("assistant", [item["role"] for item in context])
        self.assertNotIn(private_text, rendered_status)
        self.assertNotIn("private-source-delivery", rendered_status)
        self.assertNotIn(claim["entryId"], rendered_status)
        self.assertNotIn(claim["turnId"], rendered_status)


if __name__ == "__main__":
    unittest.main()
