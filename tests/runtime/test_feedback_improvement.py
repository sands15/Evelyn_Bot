from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_archive import ConversationArchive  # noqa: E402
from evelyn_core.feedback_improvement import (  # noqa: E402
    BASE_GUIDANCE_VERSION_ID,
    FEEDBACK_CANARY_AGGREGATE_SCHEMA,
    FEEDBACK_PRIVACY_REVIEW_SCHEMA,
    FeedbackAuthorizationError,
    FeedbackConflictError,
    FeedbackImprovementController,
)


BASE = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FeedbackImprovementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.primary = root / "c" / "conversation.sqlite3"
        self.replica = root / "d" / "conversation.sqlite3"
        self.clock = MutableClock(BASE)
        self.archive = ConversationArchive(
            primary_path=self.primary,
            replica_path=self.replica,
            anchor_path=root / "anchor" / "head.json",
            integrity_key=b"feedback-test-integrity-key-32-bytes-minimum",
            clock=self.clock,
        ).open()
        self.controller = FeedbackImprovementController(
            self.archive,
            clock=self.clock,
            evaluation_gate=lambda *_args, **_kwargs: True,
        )
        self.counter = 0

    def tearDown(self) -> None:
        self.archive.close()
        self.temporary.cleanup()

    def _local_task(self, suffix: str = "a") -> tuple[str, str]:
        self.counter += 1
        user = self.archive.append_record(
            mode="local_private",
            surface="local",
            record_type="user_text",
            body=f"local task {suffix}",
            started_at=self.clock.value,
            ended_at=self.clock.value,
            actor_external_id="control-page:local",
            owner_name="정훈",
            idempotency_key=f"user-{self.counter}",
        )
        task_id = f"task-{suffix}-{self.counter}"
        result = self.archive.append_derived_record(
            surface="local",
            record_type="task_result",
            body=f"task result {suffix}",
            started_at=self.clock.value,
            ended_at=self.clock.value,
            parent_ids=(user.record_id,),
            idempotency_key=f"result-{self.counter}",
        )
        return task_id, result.record_id

    def _discord_task(self) -> tuple[str, str]:
        self.counter += 1
        user = self.archive.append_record(
            mode="discord_shared",
            surface="discord",
            record_type="user_text",
            body="discord task",
            started_at=self.clock.value,
            ended_at=self.clock.value,
            actor_external_id="123456789012345678",
            owner_name="테스터",
            guild_id="guild-1",
            channel_id="channel-1",
            idempotency_key=f"discord-user-{self.counter}",
        )
        result = self.archive.append_derived_record(
            surface="discord",
            record_type="task_result",
            body="discord task result",
            started_at=self.clock.value,
            ended_at=self.clock.value,
            parent_ids=(user.record_id,),
            idempotency_key=f"discord-result-{self.counter}",
        )
        return f"discord-task-{self.counter}", result.record_id

    @staticmethod
    def _privacy_review() -> dict[str, object]:
        return {
            "schema": FEEDBACK_PRIVACY_REVIEW_SCHEMA,
            "reviewedByLocalOperator": True,
            "sourceIdentifiersAbsent": True,
            "privateDataAbsent": True,
            "quotesAbsent": True,
            "uniquePhrasesAbsent": True,
            "semanticParaphraseRiskAbsent": True,
            "styleFingerprintAbsent": True,
            "inferenceRiskAbsent": True,
            "privacyFixturePassed": True,
        }

    def _capture(self, suffix: str = "a"):
        task_id, source_id = self._local_task(suffix)
        return self.controller.capture_correction(
            task_id=task_id,
            source_record_id=source_id,
            category="answer_quality",
            correction=f"원문 교정 {suffix} UNIQUE_PRIVATE_PHRASE",
            identity_surface="local",
            actor_external_id="control-page:local",
            owner_name="정훈",
            surface="local",
            session_id="control-page-session",
            nonce=f"nonce-{suffix}-{self.counter}",
            session_current=True,
            admin_authorized=True,
        )

    def _generalize(self, workflow_id: str, suffix: str = "a"):
        return self.controller.generalize(
            workflow_id=workflow_id,
            guidance=f"답변 근거를 먼저 확인하고 불확실한 주장은 분리한다. 규칙 {suffix}",
            privacy_review=self._privacy_review(),
            admin_authorized=True,
        )

    def _eval_report(self, version_id: str) -> tuple[dict[str, object], str, str, str]:
        binding = self.controller.active_guidance()
        candidate = self.controller._version(version_id)[1]
        run_id = f"{self.counter + 1:032x}"
        baseline_contract = "a" * 64
        candidate_contract = "b" * 64
        report = {
            "owner": {"suiteVersion": "evelyn.task-agent-eval-suite.v1"},
            "binding": {
                "baseline": {
                    "guidance": {
                        "version": binding.version_id,
                        "digest": binding.guidance_digest,
                    }
                },
                "candidate": {
                    "guidance": {
                        "version": version_id,
                        "digest": candidate["guidanceDigest"],
                    }
                },
            },
            "aggregate": {
                "schema": "evelyn.task-agent-eval-aggregate.v1",
                "expectedRowCount": 24,
                "completedRowCount": 24,
                "passed": True,
            },
        }
        return report, run_id, baseline_contract, candidate_contract

    def _promote(self, workflow_id: str, version_id: str) -> None:
        report, run_id, baseline_contract, candidate_contract = self._eval_report(
            version_id
        )
        self.controller.record_evaluation(
            version_id=version_id,
            report=report,
            eval_run_id=run_id,
            baseline_contract_digest=baseline_contract,
            candidate_contract_digest=candidate_contract,
            admin_authorized=True,
        )
        approval = self.controller.action_binding(
            action="approve",
            version_id=version_id,
        )
        self.controller.grant_approval(
            version_id=version_id,
            approval_id=f"approval-{self.counter}",
            binding_digest=approval.binding_digest,
            expected_generation=approval.archive_generation,
            admin_authorized=True,
            step_up_consumed=True,
        )
        canary_run_id = f"canary-{self.counter}"
        self.controller.begin_canary(
            version_id=version_id,
            canary_run_id=canary_run_id,
            admin_authorized=True,
        )
        version = self.controller._version(version_id)[1]
        self.controller.record_canary(
            version_id=version_id,
            canary_run_id=canary_run_id,
            aggregate={
                "schema": FEEDBACK_CANARY_AGGREGATE_SCHEMA,
                "candidateVersionId": version_id,
                "guidanceDigest": version["guidanceDigest"],
                "contractVersion": "evelyn.task-work-contract.v1",
                "evaluatorVersion": "evelyn.task-agent-eval-suite.v1",
                "sampleCount": 10,
                "passedCount": 10,
                "unauthorizedEffectCount": 0,
                "privacyLeakageCount": 0,
                "structuralFailureCount": 0,
                "taskFailureCount": 0,
            },
            admin_authorized=True,
        )
        activation = self.controller.action_binding(
            action="activate",
            version_id=version_id,
        )
        result = self.controller.activate(
            version_id=version_id,
            binding_digest=activation.binding_digest,
            expected_generation=activation.archive_generation,
            admin_authorized=True,
        )
        self.assertEqual(result.state, "active")
        self.assertEqual(result.workflow_id, workflow_id)

    def _delete_feedback(self, workflow_id: str) -> None:
        preview = self.archive.preview_admin_deletion(
            authorized=True,
            record_ids=(workflow_id,),
            now=self.clock.value,
        )
        self.archive.apply_admin_deletion(
            authorized=True,
            preview_id=preview.preview_id,
            now=self.clock.value,
        )

    def test_full_pipeline_keeps_only_independent_active_version_after_source_delete(self) -> None:
        captured = self._capture()
        generalized = self._generalize(captured.workflow_id)
        assert generalized.version_id is not None
        self._promote(captured.workflow_id, generalized.version_id)

        self._delete_feedback(captured.workflow_id)

        deleted = self.controller.workflow(captured.workflow_id)
        self.assertEqual(deleted.state, "revoked")
        self.assertEqual(
            deleted.deletion_states,
            ("source_deleted", "purge_pending", "revoked"),
        )
        active = self.controller.active_guidance()
        self.assertEqual(active.version_id, generalized.version_id)
        self.assertNotIn("UNIQUE_PRIVATE_PHRASE", active.guidance)
        for path in (self.primary, self.replica):
            connection = sqlite3.connect(path)
            try:
                bodies = "\n".join(
                    str(row[0])
                    for row in connection.execute("SELECT body FROM records")
                )
            finally:
                connection.close()
            self.assertNotIn("UNIQUE_PRIVATE_PHRASE", bodies)
            self.assertIn("답변 근거를 먼저 확인", bodies)

    def test_discord_feedback_is_review_only_even_with_admin_flag(self) -> None:
        task_id, source_id = self._discord_task()
        result = self.controller.capture_correction(
            task_id=task_id,
            source_record_id=source_id,
            category="answer_quality",
            correction="이 부분을 고쳐줘",
            identity_surface="discord",
            actor_external_id="123456789012345678",
            owner_name="테스터",
            surface="discord",
            session_id="discord-session",
            nonce="discord-nonce",
            session_current=True,
            admin_authorized=True,
        )
        self.assertEqual(result.route, "review_only")
        self.assertFalse(result.actionable)
        with self.assertRaises(FeedbackAuthorizationError):
            self._generalize(result.workflow_id)

    def test_source_deletion_fences_late_evaluation(self) -> None:
        captured = self._capture("late")
        generalized = self._generalize(captured.workflow_id, "late")
        assert generalized.version_id is not None
        self._delete_feedback(captured.workflow_id)
        report, run_id, baseline, candidate = self._eval_report(
            generalized.version_id
        )
        with self.assertRaises(FeedbackConflictError):
            self.controller.record_evaluation(
                version_id=generalized.version_id,
                report=report,
                eval_run_id=run_id,
                baseline_contract_digest=baseline,
                candidate_contract_digest=candidate,
                admin_authorized=True,
            )

    def test_source_deleted_running_canary_is_terminally_aborted(self) -> None:
        captured = self._capture("canary-source-delete")
        generalized = self._generalize(
            captured.workflow_id,
            "canary-source-delete",
        )
        assert generalized.version_id is not None
        report, run_id, baseline, candidate = self._eval_report(
            generalized.version_id
        )
        self.controller.record_evaluation(
            version_id=generalized.version_id,
            report=report,
            eval_run_id=run_id,
            baseline_contract_digest=baseline,
            candidate_contract_digest=candidate,
            admin_authorized=True,
        )
        approval = self.controller.action_binding(
            action="approve",
            version_id=generalized.version_id,
        )
        self.controller.grant_approval(
            version_id=generalized.version_id,
            approval_id="approval-source-delete",
            binding_digest=approval.binding_digest,
            expected_generation=approval.archive_generation,
            admin_authorized=True,
            step_up_consumed=True,
        )
        self.controller.begin_canary(
            version_id=generalized.version_id,
            canary_run_id="canary-source-delete",
            admin_authorized=True,
        )
        self._delete_feedback(captured.workflow_id)

        aborted = self.controller.abort_interrupted_canary(
            canary_run_id="canary-source-delete",
            admin_authorized=True,
        )

        assert aborted is not None
        self.assertEqual(aborted["state"], "canary_failed")
        self.assertEqual(
            self.controller.active_guidance().version_id,
            BASE_GUIDANCE_VERSION_ID,
        )
        self.assertIsNone(
            self.controller.running_canary_pointer(
                local_admin=True,
                read_only=True,
                grounded_task=True,
            )
        )
        self.assertEqual(
            self.controller.workflow(captured.workflow_id).deletion_states,
            ("source_deleted", "purge_pending", "revoked"),
        )

    def test_source_deletion_during_evaluation_gate_cannot_commit_late_receipt(
        self,
    ) -> None:
        captured = self._capture("eval-race")
        generalized = self._generalize(captured.workflow_id, "eval-race")
        assert generalized.version_id is not None
        report, run_id, baseline, candidate = self._eval_report(
            generalized.version_id
        )

        def delete_during_gate(*_args, **_kwargs) -> bool:
            self._delete_feedback(captured.workflow_id)
            return True

        self.controller = FeedbackImprovementController(
            self.archive,
            clock=self.clock,
            evaluation_gate=delete_during_gate,
        )
        with self.assertRaisesRegex(
            FeedbackConflictError,
            "feedback_source_generation_stale",
        ):
            self.controller.record_evaluation(
                version_id=generalized.version_id,
                report=report,
                eval_run_id=run_id,
                baseline_contract_digest=baseline,
                candidate_contract_digest=candidate,
                admin_authorized=True,
            )
        self.assertFalse(
            self.archive.read_feedback_records_admin(
                authorized=True,
                record_types=("feedback_evaluation",),
            )
        )

    def test_source_deletion_during_generalization_cannot_create_orphan_version(
        self,
    ) -> None:
        captured = self._capture("generalize-race")
        append_system_record = self.archive.append_system_record

        def delete_then_append(**kwargs):
            self._delete_feedback(captured.workflow_id)
            return append_system_record(**kwargs)

        with patch.object(
            self.archive,
            "append_system_record",
            side_effect=delete_then_append,
        ):
            with self.assertRaisesRegex(
                FeedbackConflictError,
                "feedback_source_generation_stale",
            ):
                self._generalize(captured.workflow_id, "generalize-race")
        self.assertFalse(
            self.archive.read_feedback_records_admin(
                authorized=True,
                record_types=("feedback_independent_version",),
            )
        )

    def test_source_dependent_guidance_and_source_bound_activation_fail_closed(self) -> None:
        captured = self._capture("private")
        with self.assertRaises(FeedbackAuthorizationError):
            self.controller.generalize(
                workflow_id=captured.workflow_id,
                guidance=(
                    "원문 교정 private UNIQUE_PRIVATE_PHRASE 를 모든 작업에 사용한다"
                ),
                privacy_review=self._privacy_review(),
                admin_authorized=True,
            )
        with self.assertRaises(FeedbackAuthorizationError):
            self.controller.action_binding(
                action="approve",
                version_id="fgv-not-created",
            )

    def test_fixed_failure_allows_one_rollback_and_rejects_generic_failure(self) -> None:
        first = self._capture("first")
        first_version = self._generalize(first.workflow_id, "first").version_id
        assert first_version is not None
        self._promote(first.workflow_id, first_version)
        second = self._capture("second")
        second_version = self._generalize(second.workflow_id, "second").version_id
        assert second_version is not None
        self._promote(second.workflow_id, second_version)
        failure_task_id, failure_source_id = self._local_task("failure")
        generation = self.archive.generation
        with self.assertRaises(FeedbackAuthorizationError):
            self.controller.record_active_failure(
                version_id=second_version,
                failure_id="failure-network",
                task_id=failure_task_id,
                source_record_id=failure_source_id,
                contract_version="evelyn.task-work-contract.v1",
                evaluator_version="evelyn.task-agent-eval-suite.v1",
                failure_code="network_error",
                principal_id="a" * 32,
                ledger_generation=generation,
                authorized=True,
                ledger_integrity_current=True,
            )
        self.controller.record_active_failure(
            version_id=second_version,
            failure_id="failure-fixed",
            task_id=failure_task_id,
            source_record_id=failure_source_id,
            contract_version="evelyn.task-work-contract.v1",
            evaluator_version="evelyn.task-agent-eval-suite.v1",
            failure_code="grounding_regression",
            principal_id="a" * 32,
            ledger_generation=self.archive.generation,
            authorized=True,
            ledger_integrity_current=True,
        )
        binding = self.controller.action_binding(
            action="rollback",
            version_id=second_version,
            contract_version="evelyn.task-work-contract.v1",
            evaluator_version="evelyn.task-agent-eval-suite.v1",
        )
        rolled_back = self.controller.rollback(
            version_id=second_version,
            contract_version="evelyn.task-work-contract.v1",
            evaluator_version="evelyn.task-agent-eval-suite.v1",
            binding_digest=binding.binding_digest,
            expected_generation=binding.archive_generation,
            admin_authorized=True,
            step_up_consumed=True,
        )
        self.assertEqual(rolled_back["activeVersionId"], first_version)
        with self.assertRaises(FeedbackAuthorizationError):
            self.controller.action_binding(
                action="rollback",
                version_id=first_version,
                contract_version="evelyn.task-work-contract.v1",
                evaluator_version="evelyn.task-agent-eval-suite.v1",
            )

    def test_active_failure_is_deleted_with_its_exact_task_source(self) -> None:
        captured = self._capture("failure-delete")
        version_id = self._generalize(
            captured.workflow_id,
            "failure-delete",
        ).version_id
        assert version_id is not None
        self._promote(captured.workflow_id, version_id)
        task_id, source_id = self._local_task("failure-delete-source")
        self.controller.record_active_failure(
            version_id=version_id,
            failure_id="failure-delete-exact",
            task_id=task_id,
            source_record_id=source_id,
            contract_version="evelyn.task-work-contract.v1",
            evaluator_version="evelyn.task-agent-eval-suite.v1",
            failure_code="grounding_regression",
            principal_id="c" * 32,
            ledger_generation=self.archive.generation,
            authorized=True,
            ledger_integrity_current=True,
        )
        preview = self.archive.preview_admin_deletion(
            authorized=True,
            record_ids=(source_id,),
            now=self.clock.value,
        )
        self.archive.apply_admin_deletion(
            authorized=True,
            preview_id=preview.preview_id,
            now=self.clock.value,
        )
        self.assertFalse(
            self.archive.read_feedback_records_admin(
                authorized=True,
                record_types=("feedback_failure",),
            )
        )

    def test_old_evaluator_generation_is_not_a_rollback_candidate(self) -> None:
        captured = self._capture("old-evaluator")
        version_id = self._generalize(
            captured.workflow_id,
            "old-evaluator",
        ).version_id
        assert version_id is not None
        self._promote(captured.workflow_id, version_id)
        upgraded = FeedbackImprovementController(
            self.archive,
            clock=self.clock,
            evaluation_gate=lambda *_args, **_kwargs: True,
            task_evaluator_version="evelyn.task-agent-eval-suite.v2",
        )
        self.assertFalse(
            upgraded._version_verified_for_rollback(
                version_id,
                upgraded._records(),
            )
        )

    def test_revoking_ancestor_quarantines_descendant_and_fails_to_base(self) -> None:
        first = self._capture("ancestor")
        first_version = self._generalize(first.workflow_id, "ancestor").version_id
        assert first_version is not None
        self._promote(first.workflow_id, first_version)
        second = self._capture("descendant")
        second_version = self._generalize(second.workflow_id, "descendant").version_id
        assert second_version is not None
        self._promote(second.workflow_id, second_version)

        binding = self.controller.action_binding(
            action="revoke",
            version_id=first_version,
            reason="source_dependency_detected",
        )
        revoked = self.controller.revoke_version(
            binding_digest=binding.binding_digest,
            expected_generation=binding.archive_generation,
            version_id=first_version,
            reason="source_dependency_detected",
            admin_authorized=True,
            step_up_consumed=True,
        )

        self.assertEqual(set(revoked), {first_version, second_version})
        self.assertEqual(
            self.controller.active_guidance().version_id,
            BASE_GUIDANCE_VERSION_ID,
        )

    def test_failed_canary_is_terminal_and_revokes_candidate(self) -> None:
        captured = self._capture("canary-fail")
        version_id = self._generalize(
            captured.workflow_id, "canary-fail"
        ).version_id
        assert version_id is not None
        report, run_id, baseline, candidate = self._eval_report(version_id)
        self.controller.record_evaluation(
            version_id=version_id,
            report=report,
            eval_run_id=run_id,
            baseline_contract_digest=baseline,
            candidate_contract_digest=candidate,
            admin_authorized=True,
        )
        approval = self.controller.action_binding(
            action="approve", version_id=version_id
        )
        self.controller.grant_approval(
            version_id=version_id,
            approval_id="approval-canary-fail",
            binding_digest=approval.binding_digest,
            expected_generation=approval.archive_generation,
            admin_authorized=True,
            step_up_consumed=True,
        )
        self.controller.begin_canary(
            version_id=version_id,
            canary_run_id="canary-fail-run",
            admin_authorized=True,
        )
        version = self.controller._version(version_id)[1]
        result = self.controller.record_canary(
            version_id=version_id,
            canary_run_id="canary-fail-run",
            aggregate={
                "schema": FEEDBACK_CANARY_AGGREGATE_SCHEMA,
                "candidateVersionId": version_id,
                "guidanceDigest": version["guidanceDigest"],
                "contractVersion": "evelyn.task-work-contract.v1",
                "evaluatorVersion": "evelyn.task-agent-eval-suite.v1",
                "sampleCount": 10,
                "passedCount": 9,
                "unauthorizedEffectCount": 0,
                "privacyLeakageCount": 0,
                "structuralFailureCount": 1,
                "taskFailureCount": 0,
            },
            admin_authorized=True,
        )
        self.assertEqual(result.state, "revoked")
        self.assertIsNone(
            self.controller.running_canary_pointer(
                local_admin=True,
                read_only=True,
                grounded_task=True,
            )
        )
        with self.assertRaises(FeedbackAuthorizationError):
            self.controller.begin_canary(
                version_id=version_id,
                canary_run_id="canary-retry-forbidden",
                admin_authorized=True,
            )

    def test_candidate_parent_change_fences_evaluation(self) -> None:
        first = self._capture("parent-first")
        first_version = self._generalize(
            first.workflow_id, "parent-first"
        ).version_id
        assert first_version is not None
        second = self._capture("parent-stale")
        second_version = self._generalize(
            second.workflow_id, "parent-stale"
        ).version_id
        assert second_version is not None
        self._promote(first.workflow_id, first_version)
        report, run_id, baseline, candidate = self._eval_report(second_version)
        with self.assertRaises(FeedbackConflictError):
            self.controller.record_evaluation(
                version_id=second_version,
                report=report,
                eval_run_id=run_id,
                baseline_contract_digest=baseline,
                candidate_contract_digest=candidate,
                admin_authorized=True,
            )

    def test_retention_removes_source_bound_feedback_but_not_independent_version(self) -> None:
        captured = self._capture("retention")
        version_id = self._generalize(captured.workflow_id, "retention").version_id
        assert version_id is not None
        self.clock.value = BASE + timedelta(days=31)

        while self.archive.prune_expired(now=self.clock.value, batch_size=100):
            pass

        self.assertIsNone(
            self.archive.read_record_admin(
                authorized=True,
                record_id=captured.workflow_id,
            )
        )
        version = self.archive.read_record_admin(
            authorized=True,
            record_id=version_id,
        )
        self.assertIsNotNone(version)
        self.assertEqual(version.record_type, "feedback_independent_version")

    def test_feedback_ledger_reader_pages_past_five_thousand_records(self) -> None:
        first_page = tuple(
            SimpleNamespace(created_sequence=index) for index in range(1, 5001)
        )
        last_record = SimpleNamespace(created_sequence=5001)
        calls: list[int] = []

        class PagingArchive:
            generation = 7

            @staticmethod
            def read_feedback_records_admin(
                *,
                authorized: bool,
                limit: int,
                after_created_sequence: int,
            ):
                assert authorized is True
                assert limit == 5000
                calls.append(after_created_sequence)
                if after_created_sequence == 0:
                    return first_page
                if after_created_sequence == 5000:
                    return (last_record,)
                raise AssertionError("unexpected cursor")

        controller = FeedbackImprovementController(PagingArchive())
        records = controller._records()

        self.assertEqual(len(records), 5001)
        self.assertIs(records[-1], last_record)
        self.assertEqual(calls, [0, 5000])


if __name__ == "__main__":
    unittest.main()
