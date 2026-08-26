from __future__ import annotations

import copy
import contextlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER_PATH = (
    REPO_ROOT
    / "tools"
    / "main_latency_finalist_driver.py"
)
SPEC = importlib.util.spec_from_file_location("main_latency_finalist_driver", DRIVER_PATH)
assert SPEC is not None and SPEC.loader is not None
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


class SerializableNamespace(SimpleNamespace):
    def __init__(self, serialized: dict, **values: object) -> None:
        super().__init__(**values)
        self._serialized = serialized

    def to_dict(self) -> dict:
        return copy.deepcopy(self._serialized)


class MainLatencyFinalistDriverTests(unittest.TestCase):
    RUN_ID = "sha256:" + "1" * 64
    CANDIDATE_ID = "sha256:" + "2" * 64
    RECEIPT_ID = "sha256:" + "3" * 64
    CLEANUP_ID = "sha256:" + "4" * 64
    EVALUATION_ID = "sha256:" + "5" * 64
    HOST_PROOF_ID = "sha256:" + "6" * 64

    def recovered_objects(self, *, cleanup_status: str = "clean") -> tuple[object, object]:
        cleanup_counts = (0, 0, 0) if cleanup_status == "clean" else (0, 1, 0)
        cleanup_dict = {
            "runId": self.RUN_ID,
            "status": cleanup_status,
            "remainingProcesses": cleanup_counts[0],
            "remainingGpuAllocations": cleanup_counts[1],
            "remainingArtifacts": cleanup_counts[2],
            "proofId": self.CLEANUP_ID,
        }
        cleanup = SerializableNamespace(
            cleanup_dict,
            run_id=self.RUN_ID,
            status=cleanup_status,
            remaining_processes=cleanup_counts[0],
            remaining_gpu_allocations=cleanup_counts[1],
            remaining_artifacts=cleanup_counts[2],
            proof_id=self.CLEANUP_ID,
        )
        attestation = SimpleNamespace(
            run_id=self.RUN_ID,
            candidate_id=self.CANDIDATE_ID,
            receipt_id=self.RECEIPT_ID,
            cleanup_proof_id=self.CLEANUP_ID,
        )
        receipt_dict = {
            "runId": self.RUN_ID,
            "candidateId": self.CANDIDATE_ID,
            "receiptId": self.RECEIPT_ID,
            "status": "completed",
            "cleanup": cleanup_dict,
        }
        receipt = SerializableNamespace(
            receipt_dict,
            run_id=self.RUN_ID,
            candidate_id=self.CANDIDATE_ID,
            receipt_id=self.RECEIPT_ID,
            status="completed",
            cleanup=cleanup,
            runner_attestation=attestation,
        )
        feedback = SimpleNamespace(
            candidate_id=self.CANDIDATE_ID,
            verdict="eligible",
            codes=("candidate_passed",),
        )
        evidence = SimpleNamespace(
            run_id=self.RUN_ID,
            candidate_id=self.CANDIDATE_ID,
            receipt_id=self.RECEIPT_ID,
            cleanup_proof_id=self.CLEANUP_ID,
            evaluation_id=self.EVALUATION_ID,
        )
        evaluation_dict = {
            "runId": self.RUN_ID,
            "candidateId": self.CANDIDATE_ID,
            "receiptId": self.RECEIPT_ID,
            "cleanupProofId": self.CLEANUP_ID,
            "evaluationId": self.EVALUATION_ID,
            "verdict": "eligible",
            "code": "candidate_passed",
            "gate": "passed",
            "hostRestorationProofId": self.HOST_PROOF_ID,
        }
        evaluation = SerializableNamespace(
            evaluation_dict,
            run_id=self.RUN_ID,
            candidate_id=self.CANDIDATE_ID,
            receipt_id=self.RECEIPT_ID,
            cleanup_proof_id=self.CLEANUP_ID,
            evaluation_id=self.EVALUATION_ID,
            verdict="eligible",
            code="candidate_passed",
            gate="passed",
            host_restoration_proof_id=self.HOST_PROOF_ID,
            promotion_feedback=feedback,
            promotion_evidence=evidence,
        )
        return receipt, evaluation

    def host_proof(self) -> object:
        return SerializableNamespace(
            {
                "proofId": self.HOST_PROOF_ID,
                "status": "clean",
                "runId": self.RUN_ID,
                "candidateId": self.CANDIDATE_ID,
                "receiptId": self.RECEIPT_ID,
                "cleanupProofId": self.CLEANUP_ID,
            },
            proof_id=self.HOST_PROOF_ID,
            status="clean",
            run_id=self.RUN_ID,
            candidate_id=self.CANDIDATE_ID,
            receipt_id=self.RECEIPT_ID,
            cleanup_proof_id=self.CLEANUP_ID,
        )

    def test_identity_discovery_retries_only_exact_gpu_idle_failure(self) -> None:
        baseline = object()
        identities = object()
        state: dict[str, object] = {}
        persist = mock.Mock()
        gpu_idle = driver.LabIdentityDiscoveryError(
            "lab_gpu_idle_preflight_failed"
        )

        with (
            mock.patch.object(
                driver,
                "discover_owned_lab_identities",
                side_effect=(gpu_idle, identities),
            ) as discover,
            mock.patch.object(driver.time, "sleep") as sleep,
        ):
            self.assertIs(
                driver._discover_identities(baseline, state, persist),
                identities,
            )
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(state["identityDiscoveryAttemptCount"], 2)
        self.assertEqual(
            state["identityDiscoveryLastCode"],
            "lab_gpu_idle_preflight_failed",
        )
        persist.assert_called_once_with()
        sleep.assert_called_once_with(15.0)

        for code in ("lab_isolation_preflight_failed", "lab_identity_preflight_failed"):
            with self.subTest(code=code):
                state = {}
                persist.reset_mock()
                with (
                    mock.patch.object(
                        driver,
                        "discover_owned_lab_identities",
                        side_effect=driver.LabIdentityDiscoveryError(code),
                    ) as discover,
                    mock.patch.object(driver.time, "sleep") as sleep,
                ):
                    with self.assertRaisesRegex(
                        driver.LabIdentityDiscoveryError,
                        code,
                    ):
                        driver._discover_identities(baseline, state, persist)
                self.assertEqual(discover.call_count, 1)
                self.assertEqual(state["identityDiscoveryAttemptCount"], 1)
                persist.assert_called_once_with()
                sleep.assert_not_called()

        state = {}
        persist.reset_mock()
        with (
            mock.patch.object(
                driver,
                "discover_owned_lab_identities",
                side_effect=driver.LabIdentityDiscoveryError(
                    "lab_gpu_idle_preflight_failed"
                ),
            ) as discover,
            mock.patch.object(driver.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                driver.LabIdentityDiscoveryError,
                "lab_gpu_idle_preflight_failed",
            ):
                driver._discover_identities(baseline, state, persist)
        self.assertEqual(discover.call_count, 3)
        self.assertEqual(state["identityDiscoveryAttemptCount"], 3)
        self.assertEqual(persist.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @staticmethod
    def clean_terminal() -> dict:
        return {
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }

    def test_recovered_completion_requires_clean_eligible_exact_bindings(self) -> None:
        receipt, evaluation = self.recovered_objects()
        state = {"status": "runner_failed"}
        self.assertTrue(
            driver._apply_recovered_completion(
                state,
                receipt,
                evaluation,
                {"private": 1},
                self.clean_terminal(),
                self.host_proof(),
                run_id=self.RUN_ID,
                candidate_id=self.CANDIDATE_ID,
            )
        )
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["receipt"]["receiptId"], self.RECEIPT_ID)
        self.assertEqual(state["evaluation"]["cleanupProofId"], self.CLEANUP_ID)
        self.assertEqual(
            state["resultRecovery"],
            "signed_receipt_after_transient_transport_cleanup",
        )

        mutations = (
            ("run", lambda r, _e: setattr(r.runner_attestation, "run_id", "wrong")),
            ("receipt", lambda _r, e: setattr(e, "receipt_id", "wrong")),
            ("cleanup", lambda _r, e: setattr(e, "cleanup_proof_id", "wrong")),
            (
                "candidate",
                lambda _r, e: setattr(e.promotion_feedback, "candidate_id", "wrong"),
            ),
            ("verdict", lambda _r, e: setattr(e, "verdict", "inconclusive")),
            ("code", lambda _r, e: setattr(e, "code", "cleanup_required")),
        )
        for name, mutate in mutations:
            with self.subTest(binding=name):
                receipt, evaluation = self.recovered_objects()
                mutate(receipt, evaluation)
                state = {"status": "runner_failed"}
                self.assertFalse(
                    driver._apply_recovered_completion(
                        state,
                        receipt,
                        evaluation,
                        {},
                        self.clean_terminal(),
                        self.host_proof(),
                        run_id=self.RUN_ID,
                        candidate_id=self.CANDIDATE_ID,
                    )
                )
                self.assertEqual(state["status"], "cleanup_required")
                self.assertNotIn("receipt", state)
                self.assertNotIn("resultRecovery", state)

        receipt, evaluation = self.recovered_objects()
        dirty_terminal = self.clean_terminal()
        dirty_terminal.update(status="cleanup_required", remainingArtifacts=1)
        state = {"status": "runner_failed"}
        self.assertFalse(
            driver._apply_recovered_completion(
                state,
                receipt,
                evaluation,
                {},
                dirty_terminal,
                self.host_proof(),
                run_id=self.RUN_ID,
                candidate_id=self.CANDIDATE_ID,
            )
        )
        self.assertEqual(state["status"], "cleanup_required")
        self.assertNotIn("receipt", state)

    def test_dirty_signed_receipt_stays_cleanup_required_after_late_clean(self) -> None:
        receipt, evaluation = self.recovered_objects(cleanup_status="cleanup_required")
        evaluation.verdict = "inconclusive"
        evaluation.code = "cleanup_required"
        evaluation.gate = "cleanup"
        evaluation.promotion_feedback = None
        evaluation.promotion_evidence = None
        evaluation._serialized.update(
            verdict="inconclusive",
            code="cleanup_required",
            gate="cleanup",
        )
        state = {"status": "runner_failed"}

        self.assertFalse(
            driver._apply_recovered_completion(
                state,
                receipt,
                evaluation,
                {"private": 1},
                self.clean_terminal(),
                self.host_proof(),
                run_id=self.RUN_ID,
                candidate_id=self.CANDIDATE_ID,
            )
        )

        self.assertEqual(state["status"], "cleanup_required")
        self.assertEqual(state["preservedSignedReceipt"]["cleanup"]["status"], "cleanup_required")
        self.assertEqual(state["preservedEvaluation"]["verdict"], "inconclusive")
        self.assertNotIn("receipt", state)
        self.assertNotIn("evaluation", state)
        self.assertNotIn("resultRecovery", state)

    def test_main_orders_cleanup_restoration_proof_evaluation_and_final_check(self) -> None:
        events: list[str] = []
        receipt, evaluation = self.recovered_objects()
        proof = self.host_proof()
        plan = SerializableNamespace(
            {"runId": self.RUN_ID},
            run_id=self.RUN_ID,
        )
        candidate = SimpleNamespace(candidate_id=self.CANDIDATE_ID)

        class FakeLifecycle:
            final_status = "clean"
            finish_error = False

            def prepare(self) -> None:
                events.append("prepare")

            def verify_measurement_preflight(self) -> None:
                events.append("preflight")

            def finish_after_owned_cleanup(self) -> dict:
                events.append("finish_host")
                if self.finish_error:
                    raise RuntimeError("gpu_restore_timeout")
                return {
                    "schema": "evelyn.latency-host-restoration-observation.v1",
                    "status": "clean",
                }

            def best_effort_restore(self) -> dict:
                events.append("final_host_check")
                return {"status": self.final_status, "code": "verified"}

        lifecycle = FakeLifecycle()
        cleanup_calls = 0

        def reconcile() -> dict:
            nonlocal cleanup_calls
            cleanup_calls += 1
            events.append(
                "startup_cleanup" if cleanup_calls == 1 else "terminal_cleanup"
            )
            return self.clean_terminal()

        def transport(_capability: object):
            def run(_plan: object) -> dict:
                events.append("transport")
                return {}

            return run

        def issue(*_args: object, **_kwargs: object) -> object:
            events.append("issue_proof")
            return proof

        def evaluate(*_args: object, **_kwargs: object) -> object:
            events.append("evaluate")
            return evaluation

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "attempt5.json"
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["driver", "--attempt", "5", "--artifact", str(artifact)],
                ),
                mock.patch.object(driver, "_OwnedLabCampaignLock", side_effect=lambda: contextlib.nullcontext()),
                mock.patch.object(driver, "MainLatencyHostLifecycle", return_value=lifecycle),
                mock.patch.object(driver, "reconcile_owned_lab", side_effect=reconcile),
                mock.patch.object(driver, "discover_owned_lab_identities", return_value=object()),
                mock.patch.object(driver, "bootstrap_ephemeral_fixed_coordinator", return_value=(object(), object(), object(), object())),
                mock.patch.object(driver, "candidate_proposal", return_value={}),
                mock.patch.object(driver, "compile_candidate", return_value=candidate),
                mock.patch.object(driver, "build_runner_plan", return_value=plan),
                mock.patch.object(driver, "FixedSubprocessRunnerTransport", side_effect=transport),
                mock.patch.object(driver, "compile_runner_receipt", return_value=receipt),
                mock.patch.object(driver, "issue_host_restoration_proof", side_effect=issue),
                mock.patch.object(driver, "evaluate_runner_receipt", side_effect=evaluate),
                mock.patch.object(
                    driver,
                    "verify_completed_artifact",
                    return_value={"status": "verified"},
                ),
                mock.patch.object(driver, "read_progress", return_value=None),
                mock.patch("builtins.print"),
            ):
                exit_code = driver.main()
                state = json.loads(artifact.read_text(encoding="ascii"))
                clean_events = list(events)

                events.clear()
                cleanup_calls = 0
                lifecycle.final_status = "blocked"
                blocked_artifact = Path(directory) / "attempt5-blocked.json"
                sys.argv[-1] = str(blocked_artifact)
                blocked_exit_code = driver.main()
                blocked_state = json.loads(
                    blocked_artifact.read_text(encoding="ascii")
                )
                blocked_events = list(events)

                events.clear()
                cleanup_calls = 0
                lifecycle.final_status = "clean"
                lifecycle.finish_error = True
                failed_artifact = Path(directory) / "attempt5-host-failed.json"
                sys.argv[-1] = str(failed_artifact)
                failed_exit_code = driver.main()
                failed_state = json.loads(
                    failed_artifact.read_text(encoding="ascii")
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(
            clean_events,
            [
                "prepare",
                "startup_cleanup",
                "preflight",
                "transport",
                "terminal_cleanup",
                "finish_host",
                "issue_proof",
                "evaluate",
                "final_host_check",
            ],
        )
        self.assertEqual(
            state["evaluation"]["hostRestorationProofId"],
            self.HOST_PROOF_ID,
        )
        self.assertEqual(state["offlineVerification"]["status"], "verified")
        self.assertEqual(blocked_exit_code, 1)
        self.assertEqual(blocked_state["status"], "cleanup_required")
        self.assertNotIn("offlineVerification", blocked_state)
        self.assertEqual(blocked_events, clean_events)
        self.assertEqual(failed_exit_code, 1)
        self.assertEqual(failed_state["status"], "cleanup_required")
        self.assertEqual(
            failed_state["preservedSignedReceipt"]["receiptId"],
            self.RECEIPT_ID,
        )
        self.assertEqual(failed_state["preservedTimingDiagnostics"], {})
        self.assertNotIn("receipt", failed_state)
        self.assertNotIn("evaluation", failed_state)
        self.assertNotIn("offlineVerification", failed_state)
        self.assertEqual(
            events,
            [
                "prepare",
                "startup_cleanup",
                "preflight",
                "transport",
                "terminal_cleanup",
                "finish_host",
                "final_host_check",
            ],
        )

    def test_main_restores_after_prepare_partially_fails(self) -> None:
        events: list[str] = []

        class FailedLifecycle:
            def prepare(self) -> None:
                events.append("prepare")
                raise RuntimeError("partial_start_failed")

            def best_effort_restore(self) -> dict:
                events.append("restore")
                return {"status": "clean", "code": "restored"}

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "attempt5.json"
            with (
                mock.patch.object(sys, "argv", ["driver", "--attempt", "5", "--artifact", str(artifact)]),
                mock.patch.object(driver, "_OwnedLabCampaignLock", side_effect=lambda: contextlib.nullcontext()),
                mock.patch.object(driver, "MainLatencyHostLifecycle", return_value=FailedLifecycle()),
                mock.patch("builtins.print"),
            ):
                exit_code = driver.main()
            state = json.loads(artifact.read_text(encoding="ascii"))

        self.assertEqual(exit_code, 1)
        self.assertEqual(events, ["prepare", "restore"])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["hostRestore"]["status"], "clean")


if __name__ == "__main__":
    unittest.main()
