from __future__ import annotations

import concurrent.futures
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
    LocalVoiceDurableIngressClaim,
    split_exact_leading_wake,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    CONVERSATION_INGRESS_RECOVERY_RECEIPT_SCHEMA,
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
        first = self.issue("이블린, 첫 질문", turn_id="turn-1")
        consumed = self.consume(first, turn_id="turn-1")
        followup = self.issue("두 번째 질문", turn_id="turn-2")

        self.assertTrue(consumed["admitted"])
        self.assertTrue(followup["admitted"])
        self.assertEqual(followup["mode"], "followup")
        self.assertTrue(self.manager.public_status()["active"])

        self.clock.advance(46)
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
            validation_is_current=self.current,
        )
        self.assertTrue(recovered.admission["admitted"])

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
