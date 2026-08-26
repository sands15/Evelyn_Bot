from __future__ import annotations

import concurrent.futures
import copy
from hashlib import sha256
import json
import sys
import threading
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_voice_admission import (  # noqa: E402
    LocalVoiceAdmissionManager,
    LocalVoiceAdmissionTransactionError,
    LocalVoiceDurableIssuanceReservation,
    LocalVoiceDurableIngressClaim,
    LocalVoiceDurableReservationRevocation,
    split_exact_leading_wake,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
    CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class TokenFactory:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"opaque-local-voice-token-{self.value:08d}"


class LocalVoiceAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.tokens = TokenFactory()
        self.manager = LocalVoiceAdmissionManager(
            now=self.clock,
            token_factory=self.tokens,
            token_ttl_sec=10,
            followup_ttl_sec=45,
            replay_ttl_sec=120,
        )
        self.bridge_id = "bridge-a"
        self.capture_fence_digest = "a" * 64
        self.rotated_capture_fence_digest = "b" * 64

    @staticmethod
    def current(binding: dict) -> bool:
        return not binding or binding == {
            "sessionId": "validation-a",
            "stepId": "03-mood",
            "attempt": 1,
            "attemptId": "attempt-a",
        }

    def issue(self, text: str, *, turn_id: str = "turn-a", binding=None):
        return self.manager.issue(
            self.bridge_id,
            turn_id,
            text,
            validation_binding=binding,
            validation_is_current=self.current,
        )

    def consume(self, issued: dict, *, turn_id: str = "turn-a", text: str | None = None, binding=None):
        return self.manager.consume(
            issued.get("admissionToken"),
            self.bridge_id,
            turn_id,
            text if text is not None else issued.get("forwardText"),
            validation_binding=binding,
            validation_is_current=self.current,
        )

    @staticmethod
    def durable_claim_receipt(
        *,
        text_hash: str,
        generation: int = 1,
    ) -> dict:
        return {
            "schema": CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
            "entryId": "ingress-" + "1" * 64,
            "turnId": "journal-turn-a",
            "phase": "accepted",
            "disposition": "claimed",
            "durable": True,
            "shouldProcess": True,
            "textHash": text_hash,
            "journalGeneration": generation,
        }

    def durable_claim_for(
        self,
        request,
        *,
        claim_bridge_id: str | None = None,
        **overrides,
    ) -> LocalVoiceDurableIngressClaim:
        receipt = self.durable_claim_receipt(
            text_hash=request.forward_text_digest
        )
        receipt.update(overrides)
        return LocalVoiceDurableIngressClaim(
            schema=receipt["schema"],
            durable=receipt["durable"],
            bridge_instance_id=(
                claim_bridge_id or request.bridge_instance_id
            ),
            local_turn_id=request.turn_id,
            forward_text_digest=request.forward_text_digest,
            entry_id=receipt["entryId"],
            ingress_turn_id=receipt["turnId"],
            phase=receipt["phase"],
            disposition=receipt["disposition"],
            should_process=receipt["shouldProcess"],
            text_hash=receipt["textHash"],
            journal_generation=receipt["journalGeneration"],
        )

    def durable_reservation_for(
        self,
        request,
        **overrides,
    ) -> LocalVoiceDurableIssuanceReservation:
        values = {
            "schema": CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
            "durable": True,
            "bridge_instance_id": request.bridge_instance_id,
            "local_turn_id": request.turn_id,
            "forward_text_digest": request.forward_text_digest,
            "reservation_ref": request.reservation_ref,
            "entry_id": "ingress-" + "2" * 64,
            "ingress_turn_id": request.ingress_turn_id,
            "phase": "reserved",
            "disposition": "reserved",
            "should_process": False,
            "text_hash": request.forward_text_digest,
            "journal_generation": 1,
        }
        values.update(overrides)
        return LocalVoiceDurableIssuanceReservation(**values)

    def recovered_claim_for(
        self,
        request,
        **overrides,
    ) -> LocalVoiceDurableIngressClaim:
        values = {
            "schema": CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
            "durable": True,
            "bridge_instance_id": request.bridge_instance_id,
            "local_turn_id": request.turn_id,
            "forward_text_digest": request.forward_text_digest,
            "entry_id": "ingress-" + "2" * 64,
            "ingress_turn_id": request.ingress_turn_id,
            "phase": "accepted",
            "disposition": "claimed",
            "should_process": True,
            "text_hash": request.forward_text_digest,
            "journal_generation": 2,
            "reservation_ref": request.reservation_ref,
            "reservation_verified": True,
        }
        values.update(overrides)
        return LocalVoiceDurableIngressClaim(**values)

    def durable_revocation_for(
        self,
        requests,
        **overrides,
    ) -> LocalVoiceDurableReservationRevocation:
        values = {
            "schema": (
                CONVERSATION_INGRESS_RESERVATION_REVOCATION_RECEIPT_SCHEMA
            ),
            "durable": True,
            "bindings": tuple(
                (
                    "ingress-"
                    + sha256(request.ingress_turn_id.encode("utf-8")).hexdigest(),
                    request.ingress_turn_id,
                    request.forward_text_digest,
                    request.reservation_ref,
                )
                for request in requests
            ),
            "revoked_count": len(requests),
            "journal_generation": 2,
        }
        values.update(overrides)
        return LocalVoiceDurableReservationRevocation(**values)

    def test_exact_leading_wake_is_required_and_removed(self) -> None:
        admitted = self.issue("이블린, 지금 듣고 있어?")
        middle = self.manager.issue(
            self.bridge_id,
            "turn-middle",
            "야 이블린 지금 듣고 있어?",
            validation_is_current=self.current,
        )
        lookalike = self.manager.issue(
            self.bridge_id,
            "turn-lookalike",
            "이블린아 지금 듣고 있어?",
            validation_is_current=self.current,
        )

        self.assertTrue(admitted["admitted"])
        self.assertEqual(admitted["mode"], "wake_entry")
        self.assertEqual(admitted["forwardText"], "지금 듣고 있어?")
        self.assertEqual(middle["reason"], "wake_word_required")
        self.assertEqual(lookalike["reason"], "wake_word_required")

    def test_wake_only_turn_never_becomes_empty(self) -> None:
        self.assertEqual(split_exact_leading_wake(" 이블린! "), (True, "이블린"))
        issued = self.issue("이블린")
        self.assertEqual(issued["forwardText"], "이블린")

    def test_successful_consume_opens_bounded_followup(self) -> None:
        self.assertFalse(self.manager.active_for_bridge(self.bridge_id))
        first = self.issue("이블린, 첫 질문", turn_id="turn-1")
        consumed = self.consume(first, turn_id="turn-1")
        followup = self.issue("두 번째 질문", turn_id="turn-2")

        self.assertTrue(consumed["admitted"])
        self.assertTrue(followup["admitted"])
        self.assertEqual(followup["mode"], "followup")
        self.assertTrue(self.manager.public_status()["active"])
        self.assertTrue(self.manager.active_for_bridge(self.bridge_id))
        self.assertFalse(self.manager.active_for_bridge("bridge-other"))

        self.clock.advance(46)
        self.assertFalse(self.manager.active_for_bridge(self.bridge_id))
        expired = self.issue("세 번째 질문", turn_id="turn-3")
        self.assertEqual(expired["reason"], "wake_word_required")
        self.assertFalse(self.manager.public_status()["active"])

    def test_durable_claim_precedes_token_and_followup_commit(self) -> None:
        issued = self.issue("이블린, 원자적으로 처리해")
        observed: list[dict[str, object]] = []

        def durable_claim(request):
            observed.append(
                {
                    "text": request.forward_text,
                    "acceptedCount": self.manager.public_status()[
                        "acceptedCount"
                    ],
                    "active": self.manager.public_status()["active"],
                    "consumedTurnCount": len(
                        self.manager._consumed_turns  # noqa: SLF001
                    ),
                }
            )
            return self.durable_claim_for(request)

        transaction = self.manager.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-a",
            issued["forwardText"],
            durable_claim=durable_claim,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )

        self.assertTrue(transaction.admission["admitted"])
        claim = transaction.ingress_claim
        self.assertIsNotNone(claim)
        self.assertEqual(claim.bridge_instance_id, self.bridge_id)
        self.assertEqual(claim.local_turn_id, "turn-a")
        self.assertEqual(claim.phase, "accepted")
        self.assertEqual(claim.disposition, "claimed")
        self.assertTrue(claim.should_process)
        self.assertEqual(claim.text_hash, claim.forward_text_digest)
        self.assertEqual(
            observed,
            [
                {
                    "text": "원자적으로 처리해",
                    "acceptedCount": 0,
                    "active": False,
                    "consumedTurnCount": 0,
                }
            ],
        )
        self.assertEqual(self.manager.public_status()["acceptedCount"], 1)
        self.assertTrue(self.manager.public_status()["active"])
        self.assertEqual(
            self.consume(issued)["reason"],
            "admission_token_reused",
        )

    def test_durable_claim_failure_keeps_capability_retryable(self) -> None:
        issued = self.issue("이블린, 실패 뒤 다시 처리해")

        def fail_claim(_request):
            raise OSError("durable journal unavailable")

        with self.assertRaisesRegex(OSError, "journal unavailable"):
            self.manager.consume_with_durable_claim(
                issued["admissionToken"],
                self.bridge_id,
                "turn-a",
                issued["forwardText"],
                durable_claim=fail_claim,
                capture_fence_digest=self.capture_fence_digest,
                validation_is_current=self.current,
            )

        self.assertEqual(self.manager.public_status()["acceptedCount"], 0)
        self.assertFalse(self.manager.public_status()["active"])
        recovered = self.manager.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-a",
            issued["forwardText"],
            durable_claim=self.durable_claim_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )
        self.assertTrue(recovered.admission["admitted"])

    def test_issuance_reservation_precedes_state_and_failure_rolls_back(self) -> None:
        first = self.issue("이블린, 예약을 갱신해")
        first_digest = sha256(
            first["admissionToken"].encode("utf-8")
        ).hexdigest()

        def state():
            return copy.deepcopy(
                {
                    "bridge": self.manager._bridge_instance_id,  # noqa: SLF001
                    "active": self.manager._active_until,  # noqa: SLF001
                    "tokens": self.manager._tokens,  # noqa: SLF001
                    "pending": self.manager._pending_turn_tokens,  # noqa: SLF001
                    "terminal": self.manager._terminal_tokens,  # noqa: SLF001
                    "consumed": self.manager._consumed_turns,  # noqa: SLF001
                    "accepted": self.manager._accepted_count,  # noqa: SLF001
                    "rejected": self.manager._rejected_count,  # noqa: SLF001
                    "lastReason": self.manager._last_reason,  # noqa: SLF001
                    "lastMode": self.manager._last_mode,  # noqa: SLF001
                }
            )

        before = state()
        failed_requests = []

        def fail_reservation(request):
            failed_requests.append(request)
            self.assertIn(first_digest, self.manager._tokens)  # noqa: SLF001
            self.assertNotIn(
                request.token_digest,
                self.manager._tokens,  # noqa: SLF001
            )
            self.assertNotIn(
                first_digest,
                self.manager._terminal_tokens,  # noqa: SLF001
            )
            raise OSError("reservation journal unavailable")

        with self.assertRaisesRegex(OSError, "journal unavailable"):
            self.manager.issue_with_durable_reservation(
                self.bridge_id,
                "turn-a",
                "이블린, 예약을 갱신해",
                durable_reservation=fail_reservation,
                capture_fence_digest=self.capture_fence_digest,
                validation_is_current=self.current,
            )

        self.assertEqual(state(), before)
        retried_requests = []

        def reserve_retry(request):
            retried_requests.append(request)
            return self.durable_reservation_for(request)

        transaction = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-a",
            "이블린, 예약을 갱신해",
            durable_reservation=reserve_retry,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )

        self.assertTrue(transaction.admission["admitted"])
        self.assertIsNotNone(transaction.reservation)
        self.assertNotEqual(
            failed_requests[0].reservation_ref,
            retried_requests[0].reservation_ref,
        )
        self.assertEqual(
            self.consume(first)["reason"],
            "admission_token_superseded",
        )
        request_text = repr(retried_requests[0])
        self.assertNotIn("예약을 갱신해", request_text)
        self.assertNotIn(
            transaction.admission["admissionToken"],
            request_text,
        )

    def test_durable_mismatch_revokes_before_terminalizing(self) -> None:
        transaction = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-revoke",
            "이블린, 정확히 폐기해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )
        issued = transaction.admission
        token_digest = sha256(
            issued["admissionToken"].encode("utf-8")
        ).hexdigest()
        observed = []

        def revoke(requests):
            self.assertEqual(len(requests), 1)
            self.assertIn(token_digest, self.manager._tokens)  # noqa: SLF001
            self.assertNotIn(
                token_digest,
                self.manager._terminal_tokens,  # noqa: SLF001
            )
            observed.extend(requests)
            return self.durable_revocation_for(requests)

        rejected = self.manager.consume(
            issued["admissionToken"],
            self.bridge_id,
            "turn-revoke",
            "변조된 문장",
            validation_is_current=self.current,
            durable_revocation=revoke,
        )

        self.assertEqual(rejected["reason"], "admission_text_mismatch")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].token_digest, token_digest)
        self.assertFalse(self.manager.public_status()["revocationFenced"])
        self.assertNotIn(token_digest, self.manager._tokens)  # noqa: SLF001
        self.assertEqual(
            self.manager.consume(
                issued["admissionToken"],
                self.bridge_id,
                "turn-revoke",
                issued["forwardText"],
                validation_is_current=self.current,
            )["reason"],
            "admission_text_mismatch",
        )
        proof = repr(observed[0])
        self.assertNotIn("정확히 폐기해", proof)
        self.assertNotIn(issued["admissionToken"], proof)

    def test_capture_fence_rotation_revokes_before_durable_claim(self) -> None:
        reservations = []
        transaction = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-capture-rotation",
            "이블린, 이전 동의로 처리하지 마",
            durable_reservation=lambda request: (
                reservations.append(request)
                or self.durable_reservation_for(request)
            ),
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )
        claims = []
        revocations = []

        result = self.manager.consume_with_durable_claim(
            transaction.admission["admissionToken"],
            self.bridge_id,
            "turn-capture-rotation",
            transaction.admission["forwardText"],
            durable_claim=lambda request: claims.append(request),
            capture_fence_digest=self.rotated_capture_fence_digest,
            durable_revocation=lambda requests: (
                revocations.extend(requests)
                or self.durable_revocation_for(requests)
            ),
            validation_is_current=self.current,
        )

        self.assertEqual(
            result.admission["reason"],
            "voice_capture_consent_not_current",
        )
        self.assertIsNone(result.ingress_claim)
        self.assertEqual(claims, [])
        self.assertEqual(len(revocations), 1)
        self.assertEqual(
            revocations[0].capture_fence_digest,
            self.capture_fence_digest,
        )
        self.assertEqual(
            revocations[0].ingress_turn_id,
            reservations[0].ingress_turn_id,
        )
        self.assertEqual(
            revocations[0].reservation_ref,
            reservations[0].reservation_ref,
        )
        self.assertEqual(self.manager.public_status()["acceptedCount"], 0)

    def test_durable_apis_reject_invalid_capture_fence_digest(self) -> None:
        reservations = []
        for invalid in (None, "", "0" * 63, "A" * 64):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    LocalVoiceAdmissionTransactionError,
                    "local_voice_capture_fence_digest_invalid",
                ):
                    self.manager.issue_with_durable_reservation(
                        self.bridge_id,
                        "turn-invalid-capture-fence",
                        "이블린, 유효한 세대만 받아",
                        durable_reservation=lambda request: (
                            reservations.append(request)
                            or self.durable_reservation_for(request)
                        ),
                        capture_fence_digest=invalid,
                        validation_is_current=self.current,
                    )
        self.assertEqual(reservations, [])

        issued = self.issue(
            "이블린, 소비도 유효한 세대만 받아",
            turn_id="turn-invalid-capture-consume",
        )
        claims = []
        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_capture_fence_digest_invalid",
        ):
            self.manager.consume_with_durable_claim(
                issued["admissionToken"],
                self.bridge_id,
                "turn-invalid-capture-consume",
                issued["forwardText"],
                durable_claim=lambda request: claims.append(request),
                capture_fence_digest="not-a-digest",
                validation_is_current=self.current,
            )
        self.assertEqual(claims, [])
        self.assertTrue(
            self.consume(
                issued,
                turn_id="turn-invalid-capture-consume",
            )["admitted"]
        )

    def test_durable_reservation_requires_claim_before_valid_consume(self) -> None:
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-claim-required",
            "이블린, claim 뒤에 소비해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission
        token_digest = sha256(
            issued["admissionToken"].encode("utf-8")
        ).hexdigest()

        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_durable_claim_required",
        ):
            self.manager.consume(
                issued["admissionToken"],
                self.bridge_id,
                "turn-claim-required",
                issued["forwardText"],
                validation_is_current=self.current,
            )

        self.assertIn(token_digest, self.manager._tokens)  # noqa: SLF001
        self.assertFalse(self.manager.public_status()["revocationFenced"])
        accepted = self.manager.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-claim-required",
            issued["forwardText"],
            durable_claim=self.recovered_claim_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )
        self.assertTrue(accepted.admission["admitted"])

    def test_durable_reservation_requires_exact_verified_claim_receipt(self) -> None:
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-exact-claim",
            "이블린, 정확한 예약만 처리해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission

        invalid_claims = (
            lambda request: self.durable_claim_for(request),
            lambda request: self.recovered_claim_for(
                request,
                reservation_ref="reservation-" + "0" * 64,
            ),
            lambda request: self.recovered_claim_for(
                request,
                ingress_turn_id="local-voice-ingress-" + "0" * 64,
            ),
        )
        for invalid_claim in invalid_claims:
            with self.subTest(invalid_claim=invalid_claim):
                with self.assertRaises(LocalVoiceAdmissionTransactionError):
                    self.manager.consume_with_durable_claim(
                        issued["admissionToken"],
                        self.bridge_id,
                        "turn-exact-claim",
                        issued["forwardText"],
                        durable_claim=invalid_claim,
                        capture_fence_digest=self.capture_fence_digest,
                        validation_is_current=self.current,
                    )
                self.assertEqual(
                    self.manager.public_status()["acceptedCount"],
                    0,
                )

        accepted = self.manager.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-exact-claim",
            issued["forwardText"],
            durable_claim=self.recovered_claim_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )
        self.assertTrue(accepted.admission["admitted"])

    def test_invalid_revocation_receipt_keeps_token_and_sets_fence(self) -> None:
        cases = (
            (
                "untyped",
                lambda _requests: {"durable": True},
                "local_voice_reservation_revocation_invalid",
            ),
            (
                "wrong_binding",
                lambda requests: self.durable_revocation_for(
                    requests,
                    bindings=(
                        (
                            "ingress-" + "3" * 64,
                            requests[0].ingress_turn_id,
                            "0" * 64,
                            requests[0].reservation_ref,
                        ),
                    ),
                ),
                "local_voice_reservation_revocation_binding_mismatch",
            ),
        )
        for label, revoke, expected_code in cases:
            with self.subTest(label=label):
                manager = LocalVoiceAdmissionManager(
                    now=self.clock,
                    token_factory=TokenFactory(),
                    token_ttl_sec=10,
                    followup_ttl_sec=45,
                    replay_ttl_sec=120,
                )
                issued = manager.issue_with_durable_reservation(
                    self.bridge_id,
                    "turn-invalid-receipt",
                    "이블린, 영수증을 검사해",
                    durable_reservation=self.durable_reservation_for,
                    capture_fence_digest=self.capture_fence_digest,
                    validation_is_current=self.current,
                ).admission
                token_digest = sha256(
                    issued["admissionToken"].encode("utf-8")
                ).hexdigest()

                with self.assertRaisesRegex(
                    LocalVoiceAdmissionTransactionError,
                    expected_code,
                ):
                    manager.consume(
                        issued["admissionToken"],
                        self.bridge_id,
                        "turn-invalid-receipt",
                        "변조",
                        validation_is_current=self.current,
                        durable_revocation=revoke,
                    )

                self.assertIn(token_digest, manager._tokens)  # noqa: SLF001
                self.assertNotIn(
                    token_digest,
                    manager._terminal_tokens,  # noqa: SLF001
                )
                self.assertTrue(
                    manager.public_status()["revocationFenced"]
                )

    def test_failed_reset_fences_until_exact_atomic_batch_retry(self) -> None:
        issued = []
        for index in range(2):
            issued.append(
                self.manager.issue_with_durable_reservation(
                    self.bridge_id,
                    f"turn-reset-{index}",
                    f"이블린, 초기화 예약 {index}",
                    durable_reservation=self.durable_reservation_for,
                    capture_fence_digest=self.capture_fence_digest,
                    validation_is_current=self.current,
                ).admission
            )
        token_digests = {
            sha256(item["admissionToken"].encode("utf-8")).hexdigest()
            for item in issued
        }
        attempts = []

        def fail_reset(requests):
            attempts.append(requests)
            self.assertEqual(set(self.manager._tokens), token_digests)  # noqa: SLF001
            raise OSError("journal unavailable PRIVATE_CANARY")

        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_reservation_revocation_failed",
        ):
            self.manager.reset(
                "mic_disabled",
                durable_revocation=fail_reset,
            )

        self.assertEqual(set(self.manager._tokens), token_digests)  # noqa: SLF001
        self.assertTrue(self.manager.public_status()["revocationFenced"])
        self.assertFalse(self.manager.public_status()["active"])
        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_reservation_revocation_required",
        ):
            self.issue("이블린, 차단되어야 해", turn_id="turn-fenced")
        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_reservation_revocation_required",
        ):
            self.manager.consume(
                issued[0]["admissionToken"],
                self.bridge_id,
                "turn-reset-0",
                issued[0]["forwardText"],
                validation_is_current=self.current,
            )

        def retry_reset(requests):
            attempts.append(requests)
            self.assertEqual(requests, attempts[0])
            self.assertEqual(set(self.manager._tokens), token_digests)  # noqa: SLF001
            self.assertTrue(
                self.manager.public_status()["revocationFenced"]
            )
            return self.durable_revocation_for(requests)

        status = self.manager.reset(
            "mic_disabled",
            durable_revocation=retry_reset,
        )

        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(attempts[0]), 2)
        self.assertFalse(status["revocationFenced"])
        self.assertEqual(status["lastReason"], "mic_disabled")
        self.assertEqual(self.manager._tokens, {})  # noqa: SLF001
        self.assertNotIn("PRIVATE_CANARY", repr(attempts))

    def test_outer_revocation_failure_fences_fresh_manager_until_reset(self) -> None:
        recovered = LocalVoiceAdmissionManager(
            now=self.clock,
            token_factory=TokenFactory(),
        )

        fenced = recovered.require_durable_revocation()

        self.assertTrue(fenced["revocationFenced"])
        self.assertFalse(fenced["active"])
        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_reservation_revocation_required",
        ):
            recovered.issue(
                self.bridge_id,
                "turn-fenced-after-restart",
                "이블린, 아직 처리하지 마",
                validation_is_current=self.current,
            )

        reset = recovered.reset("scope_revocation_recovered")
        self.assertFalse(reset["revocationFenced"])
        issued = recovered.issue(
            self.bridge_id,
            "turn-after-scope-reset",
            "이블린, 이제 다시 처리해",
            validation_is_current=self.current,
        )
        self.assertTrue(issued["admitted"])

    def test_durable_same_turn_reissue_and_bridge_rotation_do_not_orphan(self) -> None:
        first = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-rotate",
            "이블린, 갱신하고 회전해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission
        unexpected_revocations = []
        second = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-rotate",
            "이블린, 갱신하고 회전해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            durable_revocation=lambda requests: (
                unexpected_revocations.extend(requests)
                or self.durable_revocation_for(requests)
            ),
            validation_is_current=self.current,
        ).admission
        first_digest = sha256(
            first["admissionToken"].encode("utf-8")
        ).hexdigest()
        second_digest = sha256(
            second["admissionToken"].encode("utf-8")
        ).hexdigest()

        self.assertEqual(unexpected_revocations, [])
        self.assertNotIn(first_digest, self.manager._tokens)  # noqa: SLF001
        self.assertIn(second_digest, self.manager._tokens)  # noqa: SLF001

        observed = []

        def revoke_for_rotation(requests):
            observed.extend(requests)
            self.assertIn(second_digest, self.manager._tokens)  # noqa: SLF001
            self.assertEqual(
                self.manager._bridge_instance_id,  # noqa: SLF001
                self.bridge_id,
            )
            return self.durable_revocation_for(requests)

        status = self.manager.observe_bridge_instance(
            "bridge-b",
            durable_revocation=revoke_for_rotation,
        )

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0].token_digest, second_digest)
        self.assertEqual(self.manager._tokens, {})  # noqa: SLF001
        self.assertEqual(status["lastReason"], "bridge_instance_rotated")

    def test_live_token_capacity_rejects_instead_of_evicting(self) -> None:
        for index in range(256):
            transaction = self.manager.issue_with_durable_reservation(
                self.bridge_id,
                f"turn-capacity-{index}",
                "이블린, 용량 경계",
                durable_reservation=self.durable_reservation_for,
                capture_fence_digest=self.capture_fence_digest,
                validation_is_current=self.current,
            )
            self.assertTrue(transaction.admission["admitted"])

        rejected = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-capacity-overflow",
            "이블린, 용량 초과",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )

        self.assertEqual(
            rejected.admission["reason"],
            "admission_token_capacity_exhausted",
        )
        self.assertEqual(len(self.manager._tokens), 256)  # noqa: SLF001

    def test_restart_recovers_exact_wake_followup_and_validation_reservations(self) -> None:
        binding = {
            "sessionId": "validation-a",
            "stepId": "03-mood",
            "attempt": 1,
            "attemptId": "attempt-a",
        }
        cases = (
            ("wake_entry", "이블린, 재시작 뒤에도 처리해", None),
            ("followup", "후속 질문도 처리해", None),
            ("validation", "한 문장으로 오늘 기분을 말해줘", binding),
        )
        for index, (expected_mode, text, case_binding) in enumerate(
            cases,
            start=1,
        ):
            with self.subTest(mode=expected_mode):
                issuer = LocalVoiceAdmissionManager(
                    now=self.clock,
                    token_factory=self.tokens,
                    token_ttl_sec=10,
                    followup_ttl_sec=45,
                    replay_ttl_sec=120,
                )
                if expected_mode == "followup":
                    wake = issuer.issue(
                        self.bridge_id,
                        f"wake-{index}",
                        "이블린, 먼저 열어",
                        validation_is_current=self.current,
                    )
                    issuer.consume(
                        wake["admissionToken"],
                        self.bridge_id,
                        f"wake-{index}",
                        wake["forwardText"],
                        validation_is_current=self.current,
                    )
                reservation_requests = []

                def reserve(request):
                    reservation_requests.append(request)
                    return self.durable_reservation_for(request)

                issued = issuer.issue_with_durable_reservation(
                    self.bridge_id,
                    f"restart-{index}",
                    text,
                    durable_reservation=reserve,
                    capture_fence_digest=self.capture_fence_digest,
                    validation_binding=case_binding,
                    validation_is_current=self.current,
                ).admission
                self.assertEqual(issued["mode"], expected_mode)

                recovered = LocalVoiceAdmissionManager(
                    now=self.clock,
                    token_factory=self.tokens,
                    token_ttl_sec=10,
                    followup_ttl_sec=45,
                    replay_ttl_sec=120,
                )
                claim_requests = []

                def claim(request):
                    claim_requests.append(request)
                    self.assertEqual(
                        request.reservation_ref,
                        reservation_requests[0].reservation_ref,
                    )
                    self.assertEqual(
                        request.ingress_turn_id,
                        reservation_requests[0].ingress_turn_id,
                    )
                    return self.recovered_claim_for(
                        request,
                        _validation_lease_held=bool(case_binding),
                    )

                transaction = recovered.consume_with_durable_claim(
                    issued["admissionToken"],
                    self.bridge_id,
                    f"restart-{index}",
                    issued["forwardText"],
                    durable_claim=claim,
                    capture_fence_digest=self.capture_fence_digest,
                    admission_mode=issued["mode"],
                    validation_binding=case_binding,
                    validation_is_current=self.current,
                    durable_recovery_is_current=lambda: True,
                )

                self.assertTrue(transaction.admission["admitted"])
                self.assertEqual(len(claim_requests), 1)
                self.assertEqual(
                    recovered.public_status()["active"],
                    expected_mode != "validation",
                )

    def test_restart_recovery_cannot_claim_old_capture_fence(self) -> None:
        reservations = []
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-stale-capture-recovery",
            "이블린, 이전 동의 예약을 복구하지 마",
            durable_reservation=lambda request: (
                reservations.append(request)
                or self.durable_reservation_for(request)
            ),
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission
        recovered = LocalVoiceAdmissionManager(now=self.clock)
        attempted_claims = []

        def reject_stale_reservation(request):
            attempted_claims.append(request)
            raise LocalVoiceAdmissionTransactionError(
                "local_voice_ingress_claim_binding_mismatch"
            )

        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_ingress_claim_binding_mismatch",
        ):
            recovered.consume_with_durable_claim(
                issued["admissionToken"],
                self.bridge_id,
                "turn-stale-capture-recovery",
                issued["forwardText"],
                durable_claim=reject_stale_reservation,
                capture_fence_digest=self.rotated_capture_fence_digest,
                admission_mode=issued["mode"],
                validation_is_current=self.current,
                durable_recovery_is_current=lambda: True,
            )

        self.assertEqual(len(attempted_claims), 1)
        self.assertEqual(
            attempted_claims[0].capture_fence_digest,
            self.rotated_capture_fence_digest,
        )
        self.assertNotEqual(
            attempted_claims[0].ingress_turn_id,
            reservations[0].ingress_turn_id,
        )
        self.assertNotEqual(
            attempted_claims[0].reservation_ref,
            reservations[0].reservation_ref,
        )
        self.assertEqual(recovered.public_status()["acceptedCount"], 0)
        self.assertEqual(len(recovered._consumed_turns), 0)  # noqa: SLF001

        accepted = recovered.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-stale-capture-recovery",
            issued["forwardText"],
            durable_claim=self.recovered_claim_for,
            capture_fence_digest=self.capture_fence_digest,
            admission_mode=issued["mode"],
            validation_is_current=self.current,
            durable_recovery_is_current=lambda: True,
        )
        self.assertTrue(accepted.admission["admitted"])

    def test_unknown_recovery_rejects_malformed_mode_binding_and_token(self) -> None:
        reservation_requests = []
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-recovery",
            "이블린, 정확히 복구해",
            durable_reservation=lambda request: (
                reservation_requests.append(request)
                or self.durable_reservation_for(request)
            ),
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission

        def attempt(token, mode, binding=None):
            recovered = LocalVoiceAdmissionManager(now=self.clock)
            claims = []

            def claim(request):
                claims.append(request)
                return self.recovered_claim_for(request)

            result = recovered.consume_with_durable_claim(
                token,
                self.bridge_id,
                "turn-recovery",
                issued["forwardText"],
                durable_claim=claim,
                capture_fence_digest=self.capture_fence_digest,
                admission_mode=mode,
                validation_binding=binding,
                validation_is_current=self.current,
                durable_recovery_is_current=lambda: True,
            )
            return result, claims

        malformed, malformed_claims = attempt("short", "wake_entry")
        bad_mode, bad_mode_claims = attempt(
            issued["admissionToken"],
            "WAKE_ENTRY",
        )
        bad_binding, bad_binding_claims = attempt(
            issued["admissionToken"],
            "wake_entry",
            {
                "sessionId": "validation-a",
                "stepId": "03-mood",
                "attempt": 1,
                "attemptId": "attempt-a",
            },
        )
        self.assertEqual(malformed.admission["reason"], "admission_token_invalid")
        self.assertEqual(bad_mode.admission["reason"], "admission_mode_invalid")
        self.assertEqual(
            bad_binding.admission["reason"],
            "admission_validation_mismatch",
        )
        self.assertEqual(
            malformed_claims + bad_mode_claims + bad_binding_claims,
            [],
        )

        recovered = LocalVoiceAdmissionManager(now=self.clock)

        def stale_reservation_claim(request):
            return self.recovered_claim_for(
                request,
                reservation_ref=reservation_requests[0].reservation_ref,
                ingress_turn_id=reservation_requests[0].ingress_turn_id,
            )

        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_ingress_claim_binding_mismatch",
        ):
            recovered.consume_with_durable_claim(
                "opaque-but-different-token-00000001",
                self.bridge_id,
                "turn-recovery",
                issued["forwardText"],
                durable_claim=stale_reservation_claim,
                capture_fence_digest=self.capture_fence_digest,
                admission_mode="wake_entry",
                validation_is_current=self.current,
                durable_recovery_is_current=lambda: True,
            )
        self.assertEqual(recovered.public_status()["acceptedCount"], 0)

    def test_validation_recovery_requires_attempt_lease_proof(self) -> None:
        binding = {
            "sessionId": "validation-a",
            "stepId": "03-mood",
            "attempt": 1,
            "attemptId": "attempt-a",
        }
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-validation-lease",
            "한 문장으로 오늘 기분을 말해줘",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_binding=binding,
            validation_is_current=self.current,
        ).admission
        recovered = LocalVoiceAdmissionManager(now=self.clock)
        arguments = {
            "token": issued["admissionToken"],
            "bridge_instance_id": self.bridge_id,
            "turn_id": "turn-validation-lease",
            "text": issued["forwardText"],
            "admission_mode": "validation",
            "validation_binding": binding,
            "validation_is_current": self.current,
            "durable_recovery_is_current": lambda: True,
            "capture_fence_digest": self.capture_fence_digest,
        }

        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_validation_attempt_lease_required",
        ):
            recovered.consume_with_durable_claim(
                durable_claim=self.recovered_claim_for,
                **arguments,
            )
        self.assertEqual(recovered.public_status()["acceptedCount"], 0)
        admitted = recovered.consume_with_durable_claim(
            durable_claim=lambda request: self.recovered_claim_for(
                request,
                _validation_lease_held=True,
            ),
            **arguments,
        )
        self.assertTrue(admitted.admission["admitted"])

    def test_generic_claim_cannot_authorize_unknown_token_recovery(self) -> None:
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-generic",
            "이블린, 예약으로만 복구해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission
        recovered = LocalVoiceAdmissionManager(now=self.clock)

        with self.assertRaisesRegex(
            LocalVoiceAdmissionTransactionError,
            "local_voice_ingress_reservation_unverified",
        ):
            recovered.consume_with_durable_claim(
                issued["admissionToken"],
                self.bridge_id,
                "turn-generic",
                issued["forwardText"],
                durable_claim=self.durable_claim_for,
                capture_fence_digest=self.capture_fence_digest,
                admission_mode=issued["mode"],
                validation_is_current=self.current,
                durable_recovery_is_current=lambda: True,
            )

        self.assertEqual(recovered.public_status()["acceptedCount"], 0)
        self.assertEqual(len(recovered._consumed_turns), 0)  # noqa: SLF001
        retried = recovered.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-generic",
            issued["forwardText"],
            durable_claim=self.recovered_claim_for,
            capture_fence_digest=self.capture_fence_digest,
            admission_mode=issued["mode"],
            validation_is_current=self.current,
            durable_recovery_is_current=lambda: True,
        )
        self.assertTrue(retried.admission["admitted"])

    def test_recovery_context_must_be_current_before_any_durable_claim(self) -> None:
        issued = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-context",
            "이블린, 현재 브리지에서만 복구해",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission

        for recovery_current in (
            lambda: False,
            lambda: (_ for _ in ()).throw(OSError("heartbeat unreadable")),
        ):
            with self.subTest(recovery_current=recovery_current):
                recovered = LocalVoiceAdmissionManager(now=self.clock)
                claims = []
                before = recovered.public_status()
                transaction = recovered.consume_with_durable_claim(
                    issued["admissionToken"],
                    self.bridge_id,
                    "turn-context",
                    issued["forwardText"],
                    durable_claim=lambda request: (
                        claims.append(request)
                        or self.recovered_claim_for(request)
                    ),
                    capture_fence_digest=self.capture_fence_digest,
                    admission_mode=issued["mode"],
                    validation_is_current=self.current,
                    durable_recovery_is_current=recovery_current,
                )
                self.assertEqual(
                    transaction.admission["reason"],
                    "admission_recovery_context_stale",
                )
                self.assertEqual(claims, [])
                self.assertEqual(recovered.public_status(), before)
                self.assertEqual(
                    len(recovered._consumed_turns),  # noqa: SLF001
                    0,
                )

    def test_superseded_and_terminal_tokens_never_enter_recovery(self) -> None:
        first = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-terminal",
            "이블린, 마지막 토큰만 써",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission
        second = self.manager.issue_with_durable_reservation(
            self.bridge_id,
            "turn-terminal",
            "이블린, 마지막 토큰만 써",
            durable_reservation=self.durable_reservation_for,
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        ).admission
        claims = []

        superseded = self.manager.consume_with_durable_claim(
            first["admissionToken"],
            self.bridge_id,
            "turn-terminal",
            first["forwardText"],
            durable_claim=lambda request: (
                claims.append(request)
                or self.recovered_claim_for(request)
            ),
            capture_fence_digest=self.capture_fence_digest,
            admission_mode=first["mode"],
            validation_is_current=self.current,
            durable_recovery_is_current=lambda: True,
        )
        self.assertEqual(
            superseded.admission["reason"],
            "admission_token_superseded",
        )

        mismatch = self.manager.consume(
            second["admissionToken"],
            self.bridge_id,
            "turn-terminal",
            "변조된 문장",
            validation_is_current=self.current,
            durable_revocation=self.durable_revocation_for,
        )
        self.assertEqual(mismatch["reason"], "admission_text_mismatch")
        terminal = self.manager.consume_with_durable_claim(
            second["admissionToken"],
            self.bridge_id,
            "turn-terminal",
            second["forwardText"],
            durable_claim=lambda request: (
                claims.append(request)
                or self.recovered_claim_for(request)
            ),
            capture_fence_digest=self.capture_fence_digest,
            admission_mode=second["mode"],
            validation_is_current=self.current,
            durable_recovery_is_current=lambda: True,
        )
        self.assertEqual(
            terminal.admission["reason"],
            "admission_text_mismatch",
        )
        self.assertEqual(claims, [])

    def test_durable_duplicate_does_not_open_followup_or_count(self) -> None:
        issued = self.issue("이블린, 이미 기록된 질문")

        transaction = self.manager.consume_with_durable_claim(
            issued["admissionToken"],
            self.bridge_id,
            "turn-a",
            issued["forwardText"],
            durable_claim=lambda request: self.durable_claim_for(
                request,
                shouldProcess=False,
                disposition="pending",
            ),
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )

        self.assertFalse(transaction.admission["admitted"])
        self.assertTrue(transaction.admission["suppressed"])
        self.assertEqual(
            transaction.admission["reason"],
            "admission_ingress_duplicate",
        )
        self.assertFalse(transaction.ingress_claim.should_process)
        self.assertEqual(self.manager.public_status()["acceptedCount"], 0)
        self.assertFalse(self.manager.public_status()["active"])
        self.assertEqual(len(self.manager._consumed_turns), 1)  # noqa: SLF001
        self.assertEqual(
            self.consume(issued)["reason"],
            "admission_token_reused",
        )
        self.assertEqual(
            self.manager.issue(
                self.bridge_id,
                "turn-a",
                "이블린, 이미 기록된 질문",
                validation_is_current=self.current,
            )["reason"],
            "local_voice_turn_already_consumed",
        )
        self.assertEqual(
            self.manager.issue(
                self.bridge_id,
                "turn-next",
                "호출어 없는 후속 질문",
                validation_is_current=self.current,
            )["reason"],
            "wake_word_required",
        )

    def test_durable_followup_duplicate_does_not_change_lease(self) -> None:
        wake = self.issue("이블린, 첫 질문", turn_id="turn-wake")
        self.consume(wake, turn_id="turn-wake")
        original_active_until = self.manager._active_until  # noqa: SLF001
        self.clock.advance(10)
        duplicate = self.issue("중복 후속 질문", turn_id="turn-duplicate")

        transaction = self.manager.consume_with_durable_claim(
            duplicate["admissionToken"],
            self.bridge_id,
            "turn-duplicate",
            duplicate["forwardText"],
            durable_claim=lambda request: self.durable_claim_for(
                request,
                shouldProcess=False,
                disposition="pending",
            ),
            capture_fence_digest=self.capture_fence_digest,
            validation_is_current=self.current,
        )

        self.assertTrue(transaction.admission["suppressed"])
        self.assertEqual(
            self.manager._active_until,  # noqa: SLF001
            original_active_until,
        )
        self.assertEqual(self.manager.public_status()["acceptedCount"], 1)
        self.clock.advance(36)
        self.assertEqual(
            self.manager.issue(
                self.bridge_id,
                "turn-after-original-expiry",
                "호출어 없는 질문",
                validation_is_current=self.current,
            )["reason"],
            "wake_word_required",
        )

    def test_invalid_claim_receipt_never_consumes_capability(self) -> None:
        issued = self.issue("이블린, 잘못된 claim은 거부해")

        invalid_claims = (
            lambda request: self.durable_claim_for(
                request,
                durable=False,
            ),
            lambda request: self.durable_claim_for(
                request,
                textHash="0" * 64,
            ),
            lambda request: self.durable_claim_for(
                request,
                phase="completed",
                disposition="claimed",
                shouldProcess=True,
            ),
            lambda request: self.durable_claim_for(
                request,
                claim_bridge_id="wrong-bridge",
            ),
        )
        for invalid_claim in invalid_claims:
            with self.subTest(invalid_claim=invalid_claim):
                with self.assertRaises(
                    LocalVoiceAdmissionTransactionError
                ):
                    self.manager.consume_with_durable_claim(
                        issued["admissionToken"],
                        self.bridge_id,
                        "turn-a",
                        issued["forwardText"],
                        durable_claim=invalid_claim,
                        capture_fence_digest=self.capture_fence_digest,
                        validation_is_current=self.current,
                    )
                self.assertEqual(
                    self.manager.public_status()["acceptedCount"],
                    0,
                )
                self.assertFalse(self.manager.public_status()["active"])

        self.assertTrue(self.consume(issued)["admitted"])

    def test_concurrent_atomic_consume_claims_durably_once(self) -> None:
        issued = self.issue("이블린, 동시 원자 소비")
        claim_count = 0
        count_lock = threading.Lock()

        def durable_claim(request):
            nonlocal claim_count
            with count_lock:
                claim_count += 1
            return self.durable_claim_for(request)

        def consume_once(_index: int):
            return self.manager.consume_with_durable_claim(
                issued["admissionToken"],
                self.bridge_id,
                "turn-a",
                issued["forwardText"],
                durable_claim=durable_claim,
                capture_fence_digest=self.capture_fence_digest,
                validation_is_current=self.current,
            )

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            transactions = list(executor.map(consume_once, range(2)))

        self.assertEqual(claim_count, 1)
        self.assertEqual(
            sum(
                transaction.admission.get("admitted") is True
                for transaction in transactions
            ),
            1,
        )
        self.assertEqual(
            sum(
                transaction.admission.get("reason")
                == "admission_token_reused"
                for transaction in transactions
            ),
            1,
        )

    def test_high_impact_followup_requires_a_fresh_wake(self) -> None:
        first = self.issue("이블린, 준비됐어?", turn_id="turn-1")
        self.assertTrue(self.consume(first, turn_id="turn-1")["admitted"])

        denied = self.issue("/shutdown", turn_id="turn-2")
        allowed = self.issue("이블린 /shutdown", turn_id="turn-3")

        self.assertEqual(denied["reason"], "fresh_wake_required")
        self.assertTrue(allowed["admitted"])
        self.assertEqual(allowed["forwardText"], "/shutdown")

    def test_expired_reused_and_mismatched_tokens_fail_closed(self) -> None:
        expired = self.issue("이블린, 만료", turn_id="turn-expired")
        self.clock.advance(11)
        expired_result = self.consume(expired, turn_id="turn-expired")
        self.assertEqual(expired_result["reason"], "admission_token_expired")

        mismatched = self.issue("이블린, 원문", turn_id="turn-mismatch")
        mismatch_result = self.consume(
            mismatched,
            turn_id="turn-mismatch",
            text="변조된 문장",
        )
        retry_after_mismatch = self.consume(mismatched, turn_id="turn-mismatch")
        self.assertEqual(mismatch_result["reason"], "admission_text_mismatch")
        self.assertEqual(retry_after_mismatch["reason"], "admission_text_mismatch")

        valid = self.issue("이블린, 한 번만", turn_id="turn-once")
        self.assertTrue(self.consume(valid, turn_id="turn-once")["admitted"])
        reused = self.consume(valid, turn_id="turn-once")
        self.assertEqual(reused["reason"], "admission_token_reused")

    def test_unconsumed_same_turn_reissue_revokes_old_token_without_double_count(self) -> None:
        first = self.issue("이블린, 토큰 갱신", turn_id="turn-refresh")
        self.clock.advance(6)
        second = self.issue("이블린, 토큰 갱신", turn_id="turn-refresh")

        self.assertNotEqual(first["admissionToken"], second["admissionToken"])
        old = self.consume(first, turn_id="turn-refresh")
        new = self.consume(second, turn_id="turn-refresh")
        self.assertEqual(old["reason"], "admission_token_superseded")
        self.assertTrue(new["admitted"])
        self.assertEqual(self.manager.public_status()["acceptedCount"], 1)
        replay_issue = self.issue("이블린, 토큰 갱신", turn_id="turn-refresh")
        self.assertEqual(replay_issue["reason"], "local_voice_turn_already_consumed")

    def test_bridge_rotation_and_mic_reset_revoke_session_and_tokens(self) -> None:
        first = self.issue("이블린, 시작", turn_id="turn-1")
        self.assertTrue(self.consume(first, turn_id="turn-1")["admitted"])
        pending = self.issue("후속 질문", turn_id="turn-2")

        self.manager.observe_bridge_instance("bridge-b")
        old_pending = self.consume(pending, turn_id="turn-2")
        no_wake_new_bridge = self.manager.issue(
            "bridge-b",
            "turn-3",
            "주변 대화",
            validation_is_current=self.current,
        )
        self.assertEqual(old_pending["reason"], "bridge_instance_rotated")
        self.assertEqual(no_wake_new_bridge["reason"], "wake_word_required")

        fresh = self.manager.issue(
            "bridge-b",
            "turn-4",
            "이블린, 다시 시작",
            validation_is_current=self.current,
        )
        self.manager.reset("mic_disabled")
        reset_result = self.manager.consume(
            fresh["admissionToken"],
            "bridge-b",
            "turn-4",
            fresh["forwardText"],
            validation_is_current=self.current,
        )
        self.assertEqual(reset_result["reason"], "mic_disabled")

    def test_validation_binding_is_rechecked_when_token_is_consumed(self) -> None:
        binding = {
            "sessionId": "validation-a",
            "stepId": "03-mood",
            "attempt": 1,
            "attemptId": "attempt-a",
        }
        issued = self.issue("한 문장으로 오늘 기분을 말해줘", binding=binding)
        self.assertTrue(issued["admitted"])
        self.assertEqual(issued["mode"], "validation")

        stale = self.manager.consume(
            issued["admissionToken"],
            self.bridge_id,
            "turn-a",
            issued["forwardText"],
            validation_binding=binding,
            validation_is_current=lambda _binding: False,
        )
        self.assertEqual(stale["reason"], "validation_attempt_stale")

    def test_validation_bypass_never_opens_an_ordinary_followup_lease(self) -> None:
        wake = self.issue(
            "이블린, 먼저 일반 대화를 열어",
            turn_id="turn-before-validation",
        )
        self.assertTrue(
            self.consume(wake, turn_id="turn-before-validation")["admitted"]
        )
        self.assertTrue(self.manager.public_status()["active"])

        binding = {
            "sessionId": "validation-a",
            "stepId": "03-mood",
            "attempt": 1,
            "attemptId": "attempt-a",
        }
        issued = self.issue(
            "한 문장으로 오늘 기분을 말해줘",
            turn_id="turn-validation",
            binding=binding,
        )
        consumed = self.consume(
            issued,
            turn_id="turn-validation",
            binding=binding,
        )

        self.assertTrue(consumed["admitted"])
        self.assertFalse(self.manager.public_status()["active"])
        unbound = self.issue("주변 대화", turn_id="turn-after-validation")
        self.assertEqual(unbound["reason"], "wake_word_required")

    def test_all_high_impact_followups_require_a_fresh_wake(self) -> None:
        first = self.issue("이블린, 준비됐어?", turn_id="turn-open")
        self.assertTrue(self.consume(first, turn_id="turn-open")["admitted"])

        cases = (
            "/restart",
            "/mic off",
            "/mic on",
            "/minecraft start",
            "/minecraft disconnect",
            "/minecraft goal 다이아몬드 찾아줘",
        )
        for index, command in enumerate(cases, start=1):
            with self.subTest(command=command):
                denied = self.issue(command, turn_id=f"turn-impact-{index}")
                allowed = self.issue(
                    f"이블린, {command}",
                    turn_id=f"turn-impact-wake-{index}",
                )
                self.assertEqual(denied["reason"], "fresh_wake_required")
                self.assertTrue(allowed["admitted"])

    def test_concurrent_double_consume_accepts_exactly_one(self) -> None:
        issued = self.issue("이블린, 동시 소비")

        def consume_once() -> dict:
            return self.consume(issued)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _item: consume_once(), range(2)))

        self.assertEqual(sum(result.get("admitted") is True for result in results), 1)
        self.assertEqual(sum(result.get("reason") == "admission_token_reused" for result in results), 1)

    def test_public_and_internal_state_do_not_retain_transcript_or_raw_token(self) -> None:
        private_transcript = "이블린, PRIVATE_TRANSCRIPT_CANARY"
        issued = self.issue(private_transcript)
        raw_token = issued["admissionToken"]
        status_text = json.dumps(self.manager.public_status(), ensure_ascii=False)
        internal_text = repr(self.manager.__dict__)

        self.assertNotIn("PRIVATE_TRANSCRIPT_CANARY", status_text)
        self.assertNotIn("PRIVATE_TRANSCRIPT_CANARY", internal_text)
        self.assertNotIn(raw_token, status_text)
        self.assertNotIn(raw_token, internal_text)
        self.assertEqual(self.manager.public_status()["contentFree"], True)

    def test_replay_ledger_capacity_fails_closed_without_evicting_live_turns(
        self,
    ) -> None:
        for index in range(512):
            self.manager._consumed_turns[(  # noqa: SLF001
                self.bridge_id,
                f"prior-turn-{index}",
            )] = self.clock.value + 120
        issued = self.issue("이블린, capacity test", turn_id="new-turn")
        consumed = self.consume(issued, turn_id="new-turn")

        self.assertFalse(consumed["admitted"])
        self.assertEqual(
            consumed["reason"],
            "admission_replay_capacity_exhausted",
        )
        self.assertEqual(len(self.manager._consumed_turns), 512)  # noqa: SLF001

    def test_replay_ttl_cannot_be_shorter_than_followup_ttl(self) -> None:
        manager = LocalVoiceAdmissionManager(
            now=self.clock,
            token_factory=self.tokens,
            token_ttl_sec=10,
            followup_ttl_sec=300,
            replay_ttl_sec=120,
        )

        self.assertEqual(manager.replay_ttl_sec, 300)


if __name__ == "__main__":
    unittest.main()
