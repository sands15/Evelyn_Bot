from __future__ import annotations

import copy
import hashlib
import io
import json
import pickle
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_lab_contract as lab  # noqa: E402
import main_latency_finalist_verifier as finalist_verifier  # noqa: E402
import optimize_main_latency as optimizer  # noqa: E402


class FixedExternalRuntimeObserver:
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self._source = Path(self._temporary.name) / "runtime-observation.json"
        self._worker = Path(__file__).with_name("fixed_runtime_observer_worker.py").resolve()
        self.worker_identity = self._digest(self._worker.read_bytes())
        self.source_identity = self._digest(
            json.dumps(
                {
                    "schema": "evelyn.test-runtime-observer-source.v1",
                    "path": str(self._source.resolve()),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        fixed_argv = [
            str(Path(sys.executable).resolve()),
            "-I",
            "-u",
            str(self._worker),
            "--source",
            str(self._source.resolve()),
            "--worker-identity",
            self.worker_identity,
            "--source-identity",
            self.source_identity,
        ]
        self.argv_identity = self._digest(
            json.dumps(fixed_argv, separators=(",", ":")).encode("utf-8")
        )
        self._authority: tuple[str, str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _digest(value: bytes) -> str:
        return f"sha256:{hashlib.sha256(value).hexdigest()}"

    def bind(self, authority_id: str, identity_digest: str) -> bool:
        if self._authority is not None:
            return self._authority == (authority_id, identity_digest)
        command = [
            sys.executable,
            "-I",
            "-u",
            str(self._worker),
            "--source",
            str(self._source),
            "--authority",
            authority_id,
            "--identity",
            identity_digest,
            "--worker-identity",
            self.worker_identity,
            "--argv-identity",
            self.argv_identity,
            "--source-identity",
            self.source_identity,
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="ascii",
            bufsize=1,
            creationflags=creation_flags,
        )
        self._authority = (authority_id, identity_digest)
        return True

    def set_source(self, kind: str, facts: dict[str, object]) -> None:
        self._source.write_text(
            json.dumps(
                {
                    "schema": "evelyn.test-runtime-observer-source.v1",
                    "kind": kind,
                    "facts": facts,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _rpc(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("observer_not_bound")
        with self._lock:
            process.stdin.write(
                json.dumps(
                    {"op": operation, "payload": payload},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise RuntimeError("observer_request_failed")
        return response

    def observe(self, request: dict[str, object]) -> dict[str, object]:
        receipt = self._rpc("observe", request).get("receipt")
        if not isinstance(receipt, dict):
            raise RuntimeError("observer_receipt_invalid")
        return receipt

    def verify(self, receipt: dict[str, object]) -> bool:
        return self._rpc("verify", receipt).get("valid") is True

    def close(self) -> None:
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        self._temporary.cleanup()


class MainLatencyLabContractTests(unittest.TestCase):
    @staticmethod
    def identities() -> dict[str, str]:
        return {
            key: f"sha256:{index:064x}"
            for index, key in enumerate(optimizer.IDENTITY_KEYS, start=1)
        }

    def setUp(self) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        self.observer = FixedExternalRuntimeObserver()
        self.addCleanup(self.observer.close)
        (
            self.trust_root,
            self.runner_capability,
            self.evaluator_capability,
            self.lifecycle_capability,
        ) = optimizer._bootstrap_test_coordinator(
            identities,
            observer_adapter=self.observer,
        )

    @staticmethod
    def baseline_config() -> dict[str, int]:
        return {
            "main.batch": 2048,
            "main.ubatch": 1024,
            "main.cacheReuse": 256,
            "main.cacheRamMiB": 8192,
            "main.cudaGraph": 1,
            "main.swaFull": 0,
        }

    def candidate(self, *, identity_suffix: str = "") -> optimizer.CandidateManifest:
        identities = self.identities()
        if identity_suffix:
            identities["harness"] = f"sha256:{99:064x}"
        trust_root = self.trust_root
        if identity_suffix:
            pinned = optimizer.IdentitySet.from_mapping(identities)
            trust_root, _, _, _ = optimizer._bootstrap_test_coordinator(pinned)
        return optimizer.compile_candidate(
            {
                "schema": optimizer.PROPOSAL_SCHEMA,
                "identities": identities,
                "baselineConfig": self.baseline_config(),
                "changes": [{"key": "main.cacheReuse", "value": 128}],
            },
            trust_root=trust_root,
        )

    def plan(
        self,
        profile: str = "screening",
        *,
        trust_root: optimizer.CoordinatorTrustRoot | None = None,
        attempt: int = 1,
    ) -> lab.RunnerPlan:
        return lab.build_runner_plan(
            self.candidate(),
            profile=profile,
            attempt=attempt,
            trust_root=trust_root or self.trust_root,
        )

    @staticmethod
    def baseline_metrics() -> dict[str, float]:
        return {
            "postSttMainWriteP95Ms": 120.0,
            "rawFirstTokenP95Ms": 400.0,
            "rawToSafeSpeechP95Ms": 90.0,
            "safePrefixCommitP95Ms": 550.0,
            "ttsFirstPcmP95Ms": 240.0,
            "firstSentenceCommitP50Ms": 650.0,
            "firstSentenceCommitP95Ms": 800.0,
            "warmAnswerFirstPcmP50Ms": 700.0,
            "warmAnswerFirstPcmP95Ms": 900.0,
            "warmAnswerFirstPcmP99Ms": 1050.0,
            "restartReadyAnswerFirstPcmP95Ms": 1100.0,
            "restartStartupToReadyP95Ms": 60_000.0,
            "gpuMinFreeMiB": 8192.0,
        }

    @staticmethod
    def candidate_metrics() -> dict[str, float]:
        return {
            "postSttMainWriteP95Ms": 90.0,
            "rawFirstTokenP95Ms": 300.0,
            "rawToSafeSpeechP95Ms": 60.0,
            "safePrefixCommitP95Ms": 430.0,
            "ttsFirstPcmP95Ms": 200.0,
            "firstSentenceCommitP50Ms": 510.0,
            "firstSentenceCommitP95Ms": 620.0,
            "warmAnswerFirstPcmP50Ms": 550.0,
            "warmAnswerFirstPcmP95Ms": 700.0,
            "warmAnswerFirstPcmP99Ms": 850.0,
            "restartReadyAnswerFirstPcmP95Ms": 1000.0,
            "restartStartupToReadyP95Ms": 55_000.0,
            "gpuMinFreeMiB": 8192.0,
        }

    def receipt_payload(self, plan: lab.RunnerPlan) -> dict:
        spec = plan.spec
        return {
            "schema": lab.RUNNER_RECEIPT_SCHEMA,
            "runId": plan.run_id,
            "candidateId": plan.candidate.candidate_id,
            "identities": plan.candidate.identities.to_dict(),
            "baselineConfig": plan.candidate.baseline_config.to_dict(),
            "candidateConfig": plan.candidate.candidate_config.to_dict(),
            "status": "completed",
            "samples": {
                "warmBaseline": spec.warm_per_condition,
                "warmCandidate": spec.warm_per_condition,
                "restartReadyBaseline": spec.restart_ready_per_condition,
                "restartReadyCandidate": spec.restart_ready_per_condition,
                "soakTurns": spec.soak_turns,
                "abbaBlocks": spec.abba_blocks,
            },
            "baselineMetrics": self.baseline_metrics(),
            "candidateMetrics": self.candidate_metrics(),
            "statistics": {
                "schema": lab.STATISTICS_SCHEMA,
                "method": "paired-bootstrap-abba-v1",
                "bootstrapReplicates": 2000,
                "confidenceLevel": 0.95,
                "warmAnswerFirstPcmP95DeltaCiLowMs": -230.0,
                "warmAnswerFirstPcmP95DeltaCiHighMs": -170.0,
                "warmAnswerFirstPcmP95EffectSize": -0.8,
            },
            "checks": {key: 0 for key in lab.CHECK_FIELDS},
            "equivalence": {
                "comparisons": spec.warm_per_condition,
                "matches": spec.warm_per_condition,
            },
            "resources": {
                "runtimeMs": min(120_000, spec.max_runtime_ms),
                "artifactBytes": 4096,
                "peakHostRamMiB": 8192,
                "maxConcurrentRequests": 1,
            },
            "cleanup": {
                "schema": lab.CLEANUP_SCHEMA,
                "runId": plan.run_id,
                "owner": lab.LAB_OWNER,
                "status": "clean",
                "remainingProcesses": 0,
                "remainingGpuAllocations": 0,
                "remainingArtifacts": 0,
            },
        }

    def compile_receipt(
        self,
        plan: lab.RunnerPlan,
        payload: dict | None = None,
        *,
        sync_statistics: bool = True,
    ) -> lab.RunnerReceipt:
        unsigned = self.receipt_payload(plan) if payload is None else copy.deepcopy(payload)
        if sync_statistics:
            delta = (
                unsigned["candidateMetrics"]["warmAnswerFirstPcmP95Ms"]
                - unsigned["baselineMetrics"]["warmAnswerFirstPcmP95Ms"]
            )
            unsigned["statistics"]["warmAnswerFirstPcmP95DeltaCiLowMs"] = delta - 30.0
            unsigned["statistics"]["warmAnswerFirstPcmP95DeltaCiHighMs"] = delta + 30.0
            unsigned["statistics"]["warmAnswerFirstPcmP95EffectSize"] = (
                -0.8 if delta < 0 else 0.8 if delta > 0 else 0.0
            )
        return lab.issue_runner_receipt(
            plan,
            unsigned,
            trust_root=self.trust_root,
            runner_capability=self.runner_capability,
        )

    def evaluate(
        self,
        plan: lab.RunnerPlan,
        receipt: lab.RunnerReceipt,
        *,
        trust_root: optimizer.CoordinatorTrustRoot | None = None,
        evaluator_capability: optimizer.EvaluatorCapability | None = None,
    ) -> lab.EvaluationDecision:
        host_proof = (
            lab.issue_host_restoration_proof(
                plan,
                receipt,
                self.host_restoration_observation(),
                trust_root=trust_root or self.trust_root,
                lifecycle_capability=self.lifecycle_capability,
            )
            if plan.profile == "finalist" and trust_root is None
            else None
        )
        return lab.evaluate_runner_receipt(
            plan,
            receipt,
            trust_root=trust_root or self.trust_root,
            evaluator_capability=evaluator_capability or self.evaluator_capability,
            host_restoration_proof=host_proof,
        )

    @staticmethod
    def host_restoration_observation(
        *, status: str = "clean"
    ) -> dict[str, object]:
        clean = status == "clean"
        return {
            "schema": lab.HOST_RESTORATION_OBSERVATION_SCHEMA,
            "status": status,
            "dockerInitialState": "stopped",
            "dockerFinalState": "stopped" if clean else "running",
            "dockerStartedByRun": True,
            "driverModel": "wddm",
            "baselineFreeMiB": 32000.0,
            "postFreeMinMiB": 31900.0 if clean else 20000.0,
            "totalMiB": 32607.0,
            "maxUtilizationPct": 2.0 if clean else 90.0,
            "stableObservations": 3 if clean else 0,
            "globalRunningContainers": 0 if clean else 1,
        }

    def test_completed_artifact_reopens_journal_and_verifies_in_fresh_process(self) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "attempt5.json"
            journal = Path(directory) / "attempt5.authority.sqlite3"
            root, runner_capability, evaluator_capability, lifecycle_capability = (
                optimizer.bootstrap_ephemeral_fixed_coordinator(
                    identities,
                    journal_path=journal,
                )
            )
            candidate = optimizer.compile_candidate(
                optimizer.candidate_proposal(
                    identities,
                    optimizer.MainLatencyConfig.from_mapping(
                        self.baseline_config()
                    ),
                    {"main.cacheReuse": 128},
                ),
                trust_root=root,
            )
            plan = lab.build_runner_plan(
                candidate,
                profile="finalist",
                attempt=5,
                trust_root=root,
            )
            receipt = lab.issue_runner_receipt(
                plan,
                self.receipt_payload(plan),
                trust_root=root,
                runner_capability=runner_capability,
            )
            host_proof = lab.issue_host_restoration_proof(
                plan,
                receipt,
                self.host_restoration_observation(),
                trust_root=root,
                lifecycle_capability=lifecycle_capability,
            )
            evaluation = lab.evaluate_runner_receipt(
                plan,
                receipt,
                trust_root=root,
                evaluator_capability=evaluator_capability,
                host_restoration_proof=host_proof,
            )
            state = {
                "schema": finalist_verifier.ARTIFACT_SCHEMA,
                "status": "completed",
                "authorityJournal": str(journal),
                "runId": plan.run_id,
                "candidateId": candidate.candidate_id,
                "plan": plan.to_dict(),
                "receipt": receipt.to_dict(),
                "hostRestorationProof": host_proof.to_dict(),
                "evaluation": evaluation.to_dict(),
            }
            artifact.write_text(
                json.dumps(state, sort_keys=True, separators=(",", ":")),
                encoding="ascii",
            )
            root.close()

            verified = finalist_verifier.verify_completed_artifact(artifact)
            process = subprocess.run(
                [sys.executable, str(Path(finalist_verifier.__file__)), str(artifact)],
                capture_output=True,
                text=True,
                encoding="ascii",
                check=False,
            )

            self.assertEqual(verified["status"], "verified")
            self.assertEqual(verified["hostRestorationProofId"], host_proof.proof_id)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["evaluationId"], evaluation.evaluation_id)

            state["evaluation"]["evaluationId"] = "sha256:" + "0" * 64
            artifact.write_text(
                json.dumps(state, sort_keys=True, separators=(",", ":")),
                encoding="ascii",
            )
            with self.assertRaises(finalist_verifier.FinalistVerificationError):
                finalist_verifier.verify_completed_artifact(artifact)

    def test_plan_is_deterministic_identity_bound_and_has_only_fixed_execution_policy(self) -> None:
        candidate = self.candidate()
        first = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.trust_root,
        )
        second = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.trust_root,
        )

        self.assertEqual(first.run_id, second.run_id)
        self.assertRegex(first.run_id, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first.spec.warm_per_condition, 30)
        self.assertEqual(first.spec.restart_ready_per_condition, 5)
        self.assertEqual(first.spec.abba_blocks, 15)
        self.assertEqual(first.spec.max_restart_startup_ms, 900_000)
        rendered = first.to_dict()
        self.assertEqual(rendered["schema"], "evelyn.latency-runner-plan.v2")
        self.assertEqual(rendered["runnerContract"], "main-latency-fixed-runner-v3")
        serialized_plan = json.dumps(rendered, sort_keys=True)
        self.assertEqual(rendered["order"], "ABBA")
        self.assertEqual(rendered["samples"]["restartReadyPerCondition"], 5)
        self.assertNotIn("coldPerCondition", rendered["samples"])
        self.assertEqual(
            rendered["network"],
            "owned_internal_only_external_egress_disabled",
        )
        self.assertEqual(rendered["lifecycle"], "external_fixed_coordinator_only")
        self.assertNotIn("command", rendered)
        self.assertNotIn("port", rendered)
        self.assertNotIn("mount", rendered)
        self.assertNotIn("hmac", serialized_plan.lower())
        self.assertNotIn("_key", serialized_plan.lower())
        self.assertIn("authorityId", rendered)
        self.assertFalse(hasattr(first, "authority"))
        self.assertFalse(hasattr(first, "_authority"))
        self.assertNotIn("Capability", repr(asdict(first)))
        for secret in (
            self.trust_root,
            self.runner_capability,
            self.evaluator_capability,
            self.lifecycle_capability,
        ):
            with self.subTest(secret=type(secret).__name__):
                with self.assertRaises(TypeError):
                    copy.copy(secret)
                with self.assertRaises(TypeError):
                    copy.deepcopy(secret)
                with self.assertRaises(TypeError):
                    pickle.dumps(secret)

        changed_attempt = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=2,
            trust_root=self.trust_root,
        )
        finalist = lab.build_runner_plan(
            candidate,
            profile="finalist",
            attempt=1,
            trust_root=self.trust_root,
        )
        rotated_root, _, _, _ = optimizer._bootstrap_test_coordinator(
            optimizer.IdentitySet.from_mapping(self.identities())
        )
        rotated_authority = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=rotated_root,
        )
        self.assertNotEqual(first.run_id, changed_attempt.run_id)
        self.assertNotEqual(first.run_id, finalist.run_id)
        self.assertNotEqual(first.run_id, rotated_authority.run_id)
        self.assertEqual(finalist.spec.warm_per_condition, 200)
        self.assertEqual(finalist.spec.restart_ready_per_condition, 30)
        self.assertEqual(finalist.spec.soak_turns, 1000)
        self.assertEqual(finalist.spec.abba_blocks, 20)

        invalid_candidate = replace(candidate, candidate_id=f"sha256:{0:064x}")
        for name, call, code in (
            (
                "forged-candidate",
                lambda: lab.build_runner_plan(
                    invalid_candidate,
                    profile="screening",
                    attempt=1,
                    trust_root=self.trust_root,
                ),
                "runner_candidate_invalid",
            ),
            (
                "profile",
                lambda: lab.build_runner_plan(
                    candidate,
                    profile="custom",
                    attempt=1,
                    trust_root=self.trust_root,
                ),
                "runner_profile_invalid",
            ),
            (
                "bool-attempt",
                lambda: lab.build_runner_plan(
                    candidate,
                    profile="screening",
                    attempt=True,
                    trust_root=self.trust_root,
                ),
                "runner_attempt_invalid",
            ),
            (
                "missing-authority",
                lambda: lab.build_runner_plan(
                    candidate,
                    profile="screening",
                    attempt=1,
                    trust_root=None,
                ),
                "coordinator_trust_root_invalid",
            ),
        ):
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                call()
            self.assertEqual(raised.exception.code, code)

        with self.assertRaises(TypeError):
            lab.build_runner_plan(
                candidate,
                profile="screening",
                attempt=1,
                trust_root=self.trust_root,
                command="arbitrary shell",
            )

        foreign_candidate = self.candidate(identity_suffix="changed")
        with self.assertRaises(optimizer.ContractError) as unpinned:
            lab.build_runner_plan(
                foreign_candidate,
                profile="screening",
                attempt=1,
                trust_root=self.trust_root,
            )
        self.assertEqual(unpinned.exception.code, "runner_candidate_invalid")

        forged_plan = replace(first, authority_id=f"sha256:{0:064x}")
        with self.assertRaises(optimizer.ContractError) as forged_authority:
            lab.issue_runner_receipt(
                forged_plan,
                self.receipt_payload(first),
                trust_root=self.trust_root,
                runner_capability=self.runner_capability,
            )
        self.assertEqual(forged_authority.exception.code, "runner_plan_invalid")

    def test_receipt_is_strictly_bound_bounded_and_content_free(self) -> None:
        plan = self.plan()
        payload = self.receipt_payload(plan)
        receipt = self.compile_receipt(plan, payload)
        rendered = json.dumps(receipt.to_dict(), sort_keys=True)

        self.assertRegex(receipt.receipt_id, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(receipt.candidate_id, plan.candidate.candidate_id)
        self.assertEqual(
            lab.compile_runner_receipt(
                plan,
                receipt.to_dict(),
                trust_root=self.trust_root,
            ).to_dict(),
            receipt.to_dict(),
        )
        nonadditive_percentiles = copy.deepcopy(payload)
        nonadditive_percentiles["candidateMetrics"].update(
            {
                "safePrefixCommitP95Ms": 430.0,
                "ttsFirstPcmP95Ms": 200.0,
                "warmAnswerFirstPcmP50Ms": 450.0,
                "warmAnswerFirstPcmP95Ms": 500.0,
                "warmAnswerFirstPcmP99Ms": 550.0,
            }
        )
        self.assertIsInstance(
            self.compile_receipt(plan, nonadditive_percentiles),
            lab.RunnerReceipt,
        )
        for forbidden in ("prompt", "reply", "audio", "path", "command", "port", "mount"):
            self.assertNotIn(forbidden, rendered.lower())

        invalid_payloads: list[tuple[str, dict, str]] = []
        root_extra = copy.deepcopy(payload)
        root_extra["path"] = "private"
        invalid_payloads.append(("root-extra", root_extra, "runner_receipt_fields_invalid"))
        wrong_run = copy.deepcopy(payload)
        wrong_run["runId"] = f"sha256:{0:064x}"
        invalid_payloads.append(("run-binding", wrong_run, "runner_receipt_binding_invalid"))
        wrong_candidate = copy.deepcopy(payload)
        wrong_candidate["candidateId"] = f"sha256:{1:064x}"
        invalid_payloads.append(
            ("candidate-binding", wrong_candidate, "runner_receipt_binding_invalid")
        )
        wrong_identity = copy.deepcopy(payload)
        wrong_identity["identities"]["model"] += "-changed"
        invalid_payloads.append(
            ("identity-binding", wrong_identity, "identity_invalid")
        )
        wrong_config = copy.deepcopy(payload)
        wrong_config["candidateConfig"]["main.cacheReuse"] = 64
        invalid_payloads.append(("config-binding", wrong_config, "runner_receipt_binding_invalid"))
        bool_sample = copy.deepcopy(payload)
        bool_sample["samples"]["warmCandidate"] = True
        invalid_payloads.append(("bool-sample", bool_sample, "runner_samples_invalid"))
        too_many_samples = copy.deepcopy(payload)
        too_many_samples["samples"]["warmCandidate"] += 1
        invalid_payloads.append(
            ("sample-bound", too_many_samples, "runner_samples_invalid")
        )
        too_long = copy.deepcopy(payload)
        too_long["resources"]["runtimeMs"] = plan.spec.max_runtime_ms + 1
        invalid_payloads.append(("runtime-bound", too_long, "runner_bounds_exceeded"))
        too_large = copy.deepcopy(payload)
        too_large["resources"]["artifactBytes"] = plan.spec.max_artifact_bytes + 1
        invalid_payloads.append(("artifact-bound", too_large, "runner_bounds_exceeded"))
        nan_metric = copy.deepcopy(payload)
        nan_metric["candidateMetrics"]["warmAnswerFirstPcmP95Ms"] = float("nan")
        invalid_payloads.append(("nan", nan_metric, "runner_metrics_invalid"))
        old_cold_name = copy.deepcopy(payload)
        old_value = old_cold_name["candidateMetrics"].pop(
            "restartReadyAnswerFirstPcmP95Ms"
        )
        old_cold_name["candidateMetrics"]["coldAnswerFirstPcmP95Ms"] = old_value
        invalid_payloads.append(
            ("old-cold-metric-name", old_cold_name, "runner_metrics_invalid")
        )
        old_cold_samples = copy.deepcopy(payload)
        old_baseline_count = old_cold_samples["samples"].pop(
            "restartReadyBaseline"
        )
        old_candidate_count = old_cold_samples["samples"].pop(
            "restartReadyCandidate"
        )
        old_cold_samples["samples"].update(
            {
                "coldBaseline": old_baseline_count,
                "coldCandidate": old_candidate_count,
            }
        )
        invalid_payloads.append(
            ("old-cold-sample-names", old_cold_samples, "runner_samples_invalid")
        )
        startup_too_long = copy.deepcopy(payload)
        startup_too_long["candidateMetrics"]["restartStartupToReadyP95Ms"] = (
            plan.spec.max_restart_startup_ms + 1
        )
        invalid_payloads.append(
            ("restart-startup-bound", startup_too_long, "runner_metrics_invalid")
        )
        bad_percentiles = copy.deepcopy(payload)
        bad_percentiles["candidateMetrics"]["warmAnswerFirstPcmP50Ms"] = 800.0
        invalid_payloads.append(("percentile", bad_percentiles, "runner_metrics_invalid"))
        impossible_timeline = copy.deepcopy(payload)
        impossible_timeline["candidateMetrics"].update(
            {
                "safePrefixCommitP95Ms": 430.0,
                "warmAnswerFirstPcmP50Ms": 100.0,
                "warmAnswerFirstPcmP95Ms": 200.0,
                "warmAnswerFirstPcmP99Ms": 300.0,
            }
        )
        invalid_payloads.append(
            ("impossible-timeline", impossible_timeline, "runner_metrics_invalid")
        )

        for name, invalid, code in invalid_payloads:
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                self.compile_receipt(plan, invalid)
            self.assertEqual(raised.exception.code, code)

        with self.assertRaises(optimizer.ContractError) as unsigned:
            lab.compile_runner_receipt(
                plan,
                payload,
                trust_root=self.trust_root,
            )
        self.assertEqual(unsigned.exception.code, "runner_receipt_fields_invalid")

        signed_invalid: list[tuple[str, dict, str]] = []
        missing_receipt_id = receipt.to_dict()
        del missing_receipt_id["receiptId"]
        signed_invalid.append(
            ("missing-receipt-id", missing_receipt_id, "runner_receipt_fields_invalid")
        )
        missing_signature = receipt.to_dict()
        del missing_signature["signature"]
        signed_invalid.append(
            ("missing-signature", missing_signature, "runner_receipt_fields_invalid")
        )
        missing_cleanup_signature = receipt.to_dict()
        del missing_cleanup_signature["cleanup"]["signature"]
        signed_invalid.append(
            ("missing-cleanup-signature", missing_cleanup_signature, "cleanup_proof_invalid")
        )
        missing_runner_attestation = receipt.to_dict()
        del missing_runner_attestation["runnerAttestation"]
        signed_invalid.append(
            (
                "missing-runner-attestation",
                missing_runner_attestation,
                "runner_receipt_fields_invalid",
            )
        )
        forged_receipt_id = receipt.to_dict()
        forged_receipt_id["receiptId"] = f"sha256:{0:064x}"
        signed_invalid.append(
            ("receipt-id", forged_receipt_id, "runner_receipt_auth_invalid")
        )
        forged_signature = receipt.to_dict()
        forged_signature["signature"] = f"hmac-sha256:{0:064x}"
        signed_invalid.append(
            ("receipt-signature", forged_signature, "runner_receipt_auth_invalid")
        )
        forged_cleanup_id = receipt.to_dict()
        forged_cleanup_id["cleanup"]["proofId"] = f"sha256:{0:064x}"
        signed_invalid.append(
            ("cleanup-id", forged_cleanup_id, "cleanup_proof_invalid")
        )
        forged_cleanup_signature = receipt.to_dict()
        forged_cleanup_signature["cleanup"]["signature"] = f"hmac-sha256:{0:064x}"
        signed_invalid.append(
            ("cleanup-signature", forged_cleanup_signature, "cleanup_proof_invalid")
        )
        forged_attestation = receipt.to_dict()
        forged_attestation["runnerAttestation"]["signature"] = f"hmac-sha256:{0:064x}"
        signed_invalid.append(
            ("runner-attestation", forged_attestation, "runner_attestation_invalid")
        )

        for name, invalid, code in signed_invalid:
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                lab.compile_runner_receipt(
                    plan,
                    invalid,
                    trust_root=self.trust_root,
                )
            self.assertEqual(raised.exception.code, code)

        with self.assertRaises(TypeError):
            lab.issue_runner_receipt(plan, payload)
        (
            foreign_root,
            foreign_runner,
            foreign_evaluator,
            _,
        ) = optimizer._bootstrap_test_coordinator(
            optimizer.IdentitySet.from_mapping(self.identities())
        )
        with self.assertRaises(optimizer.ContractError) as foreign_capability:
            lab.issue_runner_receipt(
                plan,
                payload,
                trust_root=self.trust_root,
                runner_capability=foreign_runner,
            )
        self.assertEqual(
            foreign_capability.exception.code,
            "runner_capability_invalid",
        )
        self.assertNotEqual(foreign_root.authority_id, plan.authority_id)
        with self.assertRaises(optimizer.ContractError) as foreign_evaluator_error:
            self.evaluate(
                plan,
                receipt,
                evaluator_capability=foreign_evaluator,
            )
        self.assertEqual(
            foreign_evaluator_error.exception.code,
            "evaluator_capability_invalid",
        )
        with self.assertRaises(TypeError):
            lab.evaluate_runner_receipt(
                plan,
                receipt,
                trust_root=self.trust_root,
            )

    def test_cleanup_proof_is_mandatory_and_blocks_evaluation(self) -> None:
        plan = self.plan()
        payload = self.receipt_payload(plan)
        payload["cleanup"].update(
            {"status": "cleanup_required", "remainingProcesses": 1}
        )
        receipt = self.compile_receipt(plan, payload)
        decision = self.evaluate(plan, receipt)

        self.assertEqual(decision.verdict, "inconclusive")
        self.assertEqual(decision.code, "cleanup_required")
        self.assertEqual(decision.gate, "cleanup")
        self.assertIsNone(decision.promotion_feedback)

        clean_with_leak = self.receipt_payload(plan)
        clean_with_leak["cleanup"]["remainingArtifacts"] = 1
        with self.assertRaises(optimizer.ContractError) as raised:
            self.compile_receipt(plan, clean_with_leak)
        self.assertEqual(raised.exception.code, "cleanup_proof_invalid")

        missing = self.receipt_payload(plan)
        del missing["cleanup"]
        with self.assertRaises(optimizer.ContractError):
            self.compile_receipt(plan, missing)

        valid = self.compile_receipt(plan)
        forged = replace(valid, receipt_id=f"sha256:{0:064x}")
        with self.assertRaises(optimizer.ContractError) as forged_error:
            self.evaluate(plan, forged)
        self.assertEqual(forged_error.exception.code, "runner_receipt_invalid")

    def test_eligible_decision_produces_same_candidate_promotion_feedback(self) -> None:
        plan = self.plan("finalist")
        receipt = self.compile_receipt(plan)
        first = self.evaluate(plan, receipt)
        second = self.evaluate(plan, receipt)

        self.assertEqual(first.verdict, "eligible")
        self.assertEqual(first.code, "candidate_passed")
        self.assertEqual(first.evaluation_id, second.evaluation_id)
        self.assertEqual(first.identities, plan.candidate.identities)
        self.assertIsNotNone(first.promotion_feedback)
        self.assertIsNotNone(first.promotion_evidence)
        self.assertRegex(first.host_restoration_proof_id or "", r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            first.to_dict()["hostRestorationProofId"],
            first.host_restoration_proof_id,
        )
        self.assertEqual(first.promotion_feedback.candidate_id, plan.candidate.candidate_id)
        self.assertNotEqual(
            first.promotion_feedback.metrics["firstSentenceP50DeltaMs"],
            first.promotion_feedback.metrics["postSttFirstPcmP50DeltaMs"],
        )
        self.assertNotEqual(
            first.promotion_feedback.metrics["firstSentenceP95DeltaMs"],
            first.promotion_feedback.metrics["postSttFirstPcmP95DeltaMs"],
        )
        self.assertEqual(
            first.promotion_feedback.metrics["postSttFirstPcmP99DeltaMs"],
            -200.0,
        )
        self.assertEqual(
            first.promotion_feedback.metrics["restartReadyFirstPcmP95DeltaMs"],
            -100.0,
        )
        self.assertEqual(
            first.promotion_feedback.metrics["restartStartupToReadyP95DeltaMs"],
            -5_000.0,
        )
        current_evaluation = {
            "expected_run_id": plan.run_id,
            "expected_receipt_id": receipt.receipt_id,
            "expected_cleanup_proof_id": receipt.cleanup.proof_id,
            "expected_evaluation_id": first.evaluation_id,
            "expected_attempt": plan.attempt,
        }
        self.assertEqual(
            optimizer.validate_state_transition(
                "feedback_ready",
                "awaiting_approval",
                candidate_id=plan.candidate.candidate_id,
                feedback=first.promotion_feedback,
                promotion_evidence=first.promotion_evidence,
                trust_root=self.trust_root,
                **current_evaluation,
            ),
            optimizer.LatencyState.AWAITING_APPROVAL,
        )

        direct_feedback = optimizer.compile_feedback(first.promotion_feedback.to_dict())
        with self.assertRaises(optimizer.ContractError) as direct:
            optimizer.validate_state_transition(
                "feedback_ready",
                "awaiting_approval",
                candidate_id=plan.candidate.candidate_id,
                feedback=direct_feedback,
                **current_evaluation,
            )
        self.assertEqual(direct.exception.code, "state_transition_evidence_invalid")

        forged_evidence = replace(
            first.promotion_evidence,
            _signature=f"hmac-sha256:{0:064x}",
        )
        with self.assertRaises(optimizer.ContractError) as forged:
            optimizer.validate_state_transition(
                "feedback_ready",
                "awaiting_approval",
                candidate_id=plan.candidate.candidate_id,
                feedback=first.promotion_feedback,
                promotion_evidence=forged_evidence,
                trust_root=self.trust_root,
                **current_evaluation,
            )
        self.assertEqual(forged.exception.code, "state_transition_evidence_invalid")

        with self.assertRaises(optimizer.ContractError) as wrong_authority:
            optimizer.validate_state_transition(
                "feedback_ready",
                "awaiting_approval",
                candidate_id=plan.candidate.candidate_id,
                feedback=first.promotion_feedback,
                promotion_evidence=first.promotion_evidence,
                trust_root=optimizer._bootstrap_test_coordinator(
                    optimizer.IdentitySet.from_mapping(self.identities())
                )[0],
                **current_evaluation,
            )
        self.assertEqual(
            wrong_authority.exception.code,
            "state_transition_evidence_invalid",
        )
        with self.assertRaises(TypeError):
            first.deltas["firstSentenceP95DeltaMs"] = 0.0

        rendered = json.dumps(first.to_dict(), sort_keys=True)
        self.assertNotIn("prompt", rendered.lower())
        self.assertNotIn("audio", rendered.lower())
        self.assertNotIn("signature", rendered.lower())
        self.assertNotIn("hmac", rendered.lower())

        approval = optimizer.issue_approval_receipt(
            self.lifecycle_capability,
            self.trust_root,
            first.promotion_evidence,
        )
        lifecycle_id = lambda value: f"sha256:{value:064x}"
        deployment_observation = self._request_deployment_observation(
            plan,
            approval,
            {
                "previous_runtime_identity": lifecycle_id(100),
                "deployment_identity": lifecycle_id(101),
                "runtime_identity": lifecycle_id(102),
                "backend_epoch": lifecycle_id(103),
                "observation_window_id": lifecycle_id(109),
                "health_receipt_id": lifecycle_id(104),
                "healthy": True,
                "error_receipt_id": lifecycle_id(105),
                "error_count": 0,
                "sample_receipt_id": lifecycle_id(106),
                "sample_count": 10,
                "rollback_checkpoint_id": lifecycle_id(107),
                "cleanup_proof_id": lifecycle_id(108),
                "remaining_processes": 0,
                "remaining_artifacts": 0,
                "rollback_ready": True,
            },
        )
        deployment_evidence = optimizer.issue_canary_deployment_evidence(
            self.evaluator_capability,
            self.trust_root,
            approval,
            plan.candidate,
            deployment_observation,
        )
        canary = optimizer.issue_canary_receipt(
            self.lifecycle_capability,
            self.trust_root,
            approval,
            deployment_evidence,
        )
        soak_observation = self._request_soak_observation(
            canary,
            {
                "candidate_config": plan.candidate.candidate_config,
                "deployment_identity": lifecycle_id(101),
                "runtime_identity": lifecycle_id(102),
                "backend_epoch": lifecycle_id(103),
                "observation_window_id": lifecycle_id(208),
                "soak_receipt_id": lifecycle_id(201),
                "evaluation_receipt_id": lifecycle_id(202),
                "health_receipt_id": lifecycle_id(203),
                "healthy": True,
                "error_receipt_id": lifecycle_id(204),
                "error_count": 0,
                "sample_receipt_id": lifecycle_id(205),
                "sample_count": 1000,
                "rollback_checkpoint_id": lifecycle_id(107),
                "cleanup_proof_id": lifecycle_id(206),
                "remaining_processes": 0,
                "remaining_artifacts": 0,
                "rollback_ready_receipt_id": lifecycle_id(207),
                "rollback_ready": True,
            },
        )
        soak_evidence = optimizer.issue_soak_evaluation_evidence(
            self.evaluator_capability,
            self.trust_root,
            canary,
            soak_observation,
        )
        acceptance = optimizer.issue_acceptance_receipt(
            self.lifecycle_capability,
            self.trust_root,
            canary,
            soak_evidence,
        )
        lifecycle_context = {
            "candidate_id": plan.candidate.candidate_id,
            "trust_root": self.trust_root,
            "expected_run_id": plan.run_id,
            "expected_evaluation_id": first.evaluation_id,
        }
        self.assertEqual(
            optimizer.validate_state_transition(
                "awaiting_approval",
                "staged",
                lifecycle_receipt=approval,
                **lifecycle_context,
            ),
            optimizer.LatencyState.STAGED,
        )
        with self.assertRaises(optimizer.ContractError) as replay:
            optimizer.validate_state_transition(
                "awaiting_approval",
                "staged",
                lifecycle_receipt=approval,
                **lifecycle_context,
            )
        self.assertEqual(replay.exception.code, "state_transition_evidence_invalid")

        with self.assertRaises(optimizer.ContractError) as wrong_type:
            optimizer.validate_state_transition(
                "staged",
                "canary",
                lifecycle_receipt=approval,
                expected_previous_receipt_id=approval.receipt_id,
                **lifecycle_context,
            )
        self.assertEqual(wrong_type.exception.code, "state_transition_evidence_invalid")
        self.assertEqual(
            optimizer.validate_state_transition(
                "staged",
                "canary",
                lifecycle_receipt=canary,
                expected_previous_receipt_id=approval.receipt_id,
                **lifecycle_context,
            ),
            optimizer.LatencyState.CANARY,
        )
        with self.assertRaises(optimizer.ContractError) as canary_replay:
            optimizer.validate_state_transition(
                "staged",
                "canary",
                lifecycle_receipt=canary,
                expected_previous_receipt_id=approval.receipt_id,
                **lifecycle_context,
            )
        self.assertEqual(
            canary_replay.exception.code,
            "state_transition_evidence_invalid",
        )
        self.assertEqual(
            optimizer.validate_state_transition(
                "canary",
                "accepted",
                lifecycle_receipt=acceptance,
                expected_previous_receipt_id=canary.receipt_id,
                **lifecycle_context,
            ),
            optimizer.LatencyState.ACCEPTED,
        )
        with self.assertRaises(optimizer.ContractError) as acceptance_replay:
            optimizer.validate_state_transition(
                "canary",
                "accepted",
                lifecycle_receipt=acceptance,
                expected_previous_receipt_id=canary.receipt_id,
                **lifecycle_context,
            )
        self.assertEqual(
            acceptance_replay.exception.code,
            "state_transition_evidence_invalid",
        )
        cleanup_observation = self._request_rollback_observation(
            canary,
            {
                "candidate_config": plan.candidate.candidate_config,
                "deployed_runtime_identity": deployment_evidence.runtime_identity,
                "deployed_backend_epoch": deployment_evidence.backend_epoch,
                "observation_window_id": lifecycle_id(5004),
                "failure_receipt_id": lifecycle_id(5000),
                "rollback_checkpoint_id": deployment_evidence.rollback_checkpoint_id,
                "restored_runtime_identity": deployment_evidence.previous_runtime_identity,
                "cleanup_proof_id": lifecycle_id(5001),
                "remaining_processes": 0,
                "remaining_artifacts": 0,
                "health_receipt_id": lifecycle_id(5002),
                "healthy": True,
                "error_receipt_id": lifecycle_id(5003),
                "error_count": 0,
            },
        )
        cleanup = optimizer.issue_rollback_cleanup_evidence(
            self.evaluator_capability,
            self.trust_root,
            canary,
            cleanup_observation,
        )
        rollback = optimizer.issue_rollback_receipt(
            self.lifecycle_capability,
            self.trust_root,
            canary,
            cleanup,
        )
        with self.assertRaises(optimizer.ContractError) as branch_replay:
            optimizer.validate_state_transition(
                "canary",
                "rolled_back",
                lifecycle_receipt=rollback,
                expected_previous_receipt_id=canary.receipt_id,
                **lifecycle_context,
            )
        self.assertEqual(
            branch_replay.exception.code,
            "state_transition_evidence_invalid",
        )
        forged_approval = replace(
            approval,
            identity_digest=f"sha256:{0:064x}",
        )
        with self.assertRaises(optimizer.ContractError) as identity_bound:
            optimizer.validate_state_transition(
                "awaiting_approval",
                "staged",
                lifecycle_receipt=forged_approval,
                **lifecycle_context,
            )
        self.assertEqual(
            identity_bound.exception.code,
            "state_transition_evidence_invalid",
        )

    def test_finalist_promotion_requires_bound_host_restoration_proof(self) -> None:
        plan = self.plan("finalist")
        receipt = self.compile_receipt(plan)
        missing = lab.evaluate_runner_receipt(
            plan,
            receipt,
            trust_root=self.trust_root,
            evaluator_capability=self.evaluator_capability,
        )
        self.assertEqual(
            (missing.verdict, missing.code, missing.gate),
            ("inconclusive", "host_restoration_required", "cleanup"),
        )
        self.assertIsNone(missing.promotion_evidence)

        dirty_proof = lab.issue_host_restoration_proof(
            plan,
            receipt,
            self.host_restoration_observation(status="restoration_required"),
            trust_root=self.trust_root,
            lifecycle_capability=self.lifecycle_capability,
        )
        dirty = lab.evaluate_runner_receipt(
            plan,
            receipt,
            trust_root=self.trust_root,
            evaluator_capability=self.evaluator_capability,
            host_restoration_proof=dirty_proof,
        )
        self.assertEqual(dirty.code, "host_restoration_required")
        self.assertEqual(dirty.host_restoration_proof_id, dirty_proof.proof_id)
        self.assertIsNone(dirty.promotion_feedback)

        clean_proof = lab.issue_host_restoration_proof(
            plan,
            receipt,
            self.host_restoration_observation(),
            trust_root=self.trust_root,
            lifecycle_capability=self.lifecycle_capability,
        )
        compiled = lab.compile_host_restoration_proof(
            plan,
            receipt,
            clean_proof.to_dict(),
            trust_root=self.trust_root,
        )
        self.assertEqual(compiled.to_dict(), clean_proof.to_dict())
        eligible = lab.evaluate_runner_receipt(
            plan,
            receipt,
            trust_root=self.trust_root,
            evaluator_capability=self.evaluator_capability,
            host_restoration_proof=compiled,
        )
        self.assertEqual((eligible.verdict, eligible.code), ("eligible", "candidate_passed"))
        self.assertEqual(eligible.host_restoration_proof_id, clean_proof.proof_id)

        with self.assertRaises(optimizer.ContractError) as mismatched:
            lab.evaluate_runner_receipt(
                plan,
                receipt,
                trust_root=self.trust_root,
                evaluator_capability=self.evaluator_capability,
                host_restoration_proof=replace(
                    clean_proof, run_id=f"sha256:{0:064x}"
                ),
            )
        self.assertEqual(mismatched.exception.code, "host_restoration_proof_invalid")

    @staticmethod
    def _lifecycle_id(value: int) -> str:
        return f"sha256:{value:064x}"

    def _request_deployment_observation(
        self,
        plan: lab.RunnerPlan,
        approval: optimizer.ApprovalReceipt,
        values: dict[str, object],
    ) -> optimizer.CanaryDeploymentObservation:
        self.observer.set_source(
            "canary_deployment",
            {
                "candidateConfig": plan.candidate.candidate_config.to_dict(),
                "previousRuntimeIdentity": values["previous_runtime_identity"],
                "deploymentIdentity": values["deployment_identity"],
                "runtimeIdentity": values["runtime_identity"],
                "backendEpoch": values["backend_epoch"],
                "observationWindowId": values["observation_window_id"],
                "healthReceiptId": values["health_receipt_id"],
                "healthy": values["healthy"],
                "errorReceiptId": values["error_receipt_id"],
                "errorCount": values["error_count"],
                "sampleReceiptId": values["sample_receipt_id"],
                "sampleCount": values["sample_count"],
                "rollbackCheckpointId": values["rollback_checkpoint_id"],
                "cleanupProofId": values["cleanup_proof_id"],
                "remainingProcesses": values["remaining_processes"],
                "remainingArtifacts": values["remaining_artifacts"],
                "rollbackReady": values["rollback_ready"],
            },
        )
        return optimizer.request_canary_deployment_observation(
            self.trust_root,
            approval,
            plan.candidate,
        )

    def _request_soak_observation(
        self,
        canary: optimizer.CanaryReceipt,
        values: dict[str, object],
    ) -> optimizer.SoakEvaluationObservation:
        candidate_config = values["candidate_config"]
        assert isinstance(candidate_config, optimizer.MainLatencyConfig)
        self.observer.set_source(
            "soak_evaluation",
            {
                "candidateConfig": candidate_config.to_dict(),
                "deploymentIdentity": values["deployment_identity"],
                "runtimeIdentity": values["runtime_identity"],
                "backendEpoch": values["backend_epoch"],
                "observationWindowId": values["observation_window_id"],
                "soakReceiptId": values["soak_receipt_id"],
                "evaluationReceiptId": values["evaluation_receipt_id"],
                "healthReceiptId": values["health_receipt_id"],
                "healthy": values["healthy"],
                "errorReceiptId": values["error_receipt_id"],
                "errorCount": values["error_count"],
                "sampleReceiptId": values["sample_receipt_id"],
                "sampleCount": values["sample_count"],
                "rollbackCheckpointId": values["rollback_checkpoint_id"],
                "cleanupProofId": values["cleanup_proof_id"],
                "remainingProcesses": values["remaining_processes"],
                "remainingArtifacts": values["remaining_artifacts"],
                "rollbackReadyReceiptId": values["rollback_ready_receipt_id"],
                "rollbackReady": values["rollback_ready"],
            },
        )
        return optimizer.request_soak_evaluation_observation(self.trust_root, canary)

    def _request_rollback_observation(
        self,
        canary: optimizer.CanaryReceipt,
        values: dict[str, object],
    ) -> optimizer.RollbackCleanupObservation:
        candidate_config = values["candidate_config"]
        assert isinstance(candidate_config, optimizer.MainLatencyConfig)
        self.observer.set_source(
            "rollback_cleanup",
            {
                "candidateConfig": candidate_config.to_dict(),
                "deployedRuntimeIdentity": values["deployed_runtime_identity"],
                "deployedBackendEpoch": values["deployed_backend_epoch"],
                "observationWindowId": values["observation_window_id"],
                "failureReceiptId": values["failure_receipt_id"],
                "rollbackCheckpointId": values["rollback_checkpoint_id"],
                "restoredRuntimeIdentity": values["restored_runtime_identity"],
                "cleanupProofId": values["cleanup_proof_id"],
                "remainingProcesses": values["remaining_processes"],
                "remainingArtifacts": values["remaining_artifacts"],
                "healthReceiptId": values["health_receipt_id"],
                "healthy": values["healthy"],
                "errorReceiptId": values["error_receipt_id"],
                "errorCount": values["error_count"],
            },
        )
        return optimizer.request_rollback_cleanup_observation(self.trust_root, canary)

    def _lifecycle_seed(
        self,
        *,
        attempt: int = 1,
    ) -> tuple[lab.RunnerPlan, lab.EvaluationDecision, optimizer.ApprovalReceipt]:
        plan = self.plan("finalist", attempt=attempt)
        decision = self.evaluate(plan, self.compile_receipt(plan))
        approval = optimizer.issue_approval_receipt(
            self.lifecycle_capability,
            self.trust_root,
            decision.promotion_evidence,
        )
        return plan, decision, approval

    def _deployment_evidence(
        self,
        plan: lab.RunnerPlan,
        approval: optimizer.ApprovalReceipt,
        *,
        base: int = 1000,
        **overrides: object,
    ) -> optimizer.CanaryDeploymentEvidence:
        values: dict[str, object] = {
            "previous_runtime_identity": self._lifecycle_id(base),
            "deployment_identity": self._lifecycle_id(base + 1),
            "runtime_identity": self._lifecycle_id(base + 2),
            "backend_epoch": self._lifecycle_id(base + 3),
            "observation_window_id": self._lifecycle_id(base + 9),
            "health_receipt_id": self._lifecycle_id(base + 4),
            "healthy": True,
            "error_receipt_id": self._lifecycle_id(base + 5),
            "error_count": 0,
            "sample_receipt_id": self._lifecycle_id(base + 6),
            "sample_count": 10,
            "rollback_checkpoint_id": self._lifecycle_id(base + 7),
            "cleanup_proof_id": self._lifecycle_id(base + 8),
            "remaining_processes": 0,
            "remaining_artifacts": 0,
            "rollback_ready": True,
        }
        values.update(overrides)
        observation = self._request_deployment_observation(plan, approval, values)
        return optimizer.issue_canary_deployment_evidence(
            self.evaluator_capability,
            self.trust_root,
            approval,
            plan.candidate,
            observation,
        )

    def _canary(
        self,
        plan: lab.RunnerPlan,
        approval: optimizer.ApprovalReceipt,
        *,
        base: int = 1000,
    ) -> tuple[optimizer.CanaryDeploymentEvidence, optimizer.CanaryReceipt]:
        evidence = self._deployment_evidence(plan, approval, base=base)
        return evidence, optimizer.issue_canary_receipt(
            self.lifecycle_capability,
            self.trust_root,
            approval,
            evidence,
        )

    def _soak_evidence(
        self,
        plan: lab.RunnerPlan,
        canary: optimizer.CanaryReceipt,
        *,
        base: int = 2000,
        **overrides: object,
    ) -> optimizer.SoakEvaluationEvidence:
        deployment = canary.deployment_evidence
        values: dict[str, object] = {
            "candidate_config": plan.candidate.candidate_config,
            "deployment_identity": deployment.deployment_identity,
            "runtime_identity": deployment.runtime_identity,
            "backend_epoch": deployment.backend_epoch,
            "observation_window_id": self._lifecycle_id(base + 7),
            "soak_receipt_id": self._lifecycle_id(base),
            "evaluation_receipt_id": self._lifecycle_id(base + 1),
            "health_receipt_id": self._lifecycle_id(base + 2),
            "healthy": True,
            "error_receipt_id": self._lifecycle_id(base + 3),
            "error_count": 0,
            "sample_receipt_id": self._lifecycle_id(base + 4),
            "sample_count": 1000,
            "rollback_checkpoint_id": deployment.rollback_checkpoint_id,
            "cleanup_proof_id": self._lifecycle_id(base + 5),
            "remaining_processes": 0,
            "remaining_artifacts": 0,
            "rollback_ready_receipt_id": self._lifecycle_id(base + 6),
            "rollback_ready": True,
        }
        values.update(overrides)
        observation = self._request_soak_observation(canary, values)
        return optimizer.issue_soak_evaluation_evidence(
            self.evaluator_capability,
            self.trust_root,
            canary,
            observation,
        )

    def test_canary_requires_new_deployment_and_health_evidence(self) -> None:
        plan, decision, approval = self._lifecycle_seed()
        previous_runtime = self._lifecycle_id(1000)
        invalid_cases = {
            "same-runtime": {"runtime_identity": previous_runtime},
            "same-deployment": {"deployment_identity": previous_runtime},
            "unhealthy": {"healthy": False},
            "errors": {"error_count": 1},
            "no-samples": {"sample_count": 0},
            "process-leak": {"remaining_processes": 1},
            "artifact-leak": {"remaining_artifacts": 1},
            "no-rollback-checkpoint": {"rollback_ready": False},
        }
        for name, overrides in invalid_cases.items():
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                self._deployment_evidence(plan, approval, **overrides)
            self.assertEqual(raised.exception.code, "canary_deployment_evidence_invalid")

        evidence, canary = self._canary(plan, approval)
        forged_evidence = replace(
            evidence,
            _signature=f"hmac-sha256:{0:064x}",
        )
        with self.assertRaises(optimizer.ContractError) as forged:
            optimizer.issue_canary_receipt(
                self.lifecycle_capability,
                self.trust_root,
                approval,
                forged_evidence,
            )
        self.assertEqual(forged.exception.code, "canary_receipt_invalid")

        _, _, later_approval = self._lifecycle_seed(attempt=2)
        with self.assertRaises(optimizer.ContractError) as stale:
            optimizer.issue_canary_receipt(
                self.lifecycle_capability,
                self.trust_root,
                later_approval,
                evidence,
            )
        self.assertEqual(stale.exception.code, "canary_receipt_invalid")

        context = {
            "candidate_id": plan.candidate.candidate_id,
            "trust_root": self.trust_root,
            "expected_run_id": plan.run_id,
            "expected_evaluation_id": decision.evaluation_id,
            "expected_previous_receipt_id": approval.receipt_id,
        }
        forged_receipt = replace(canary, deployment_evidence=forged_evidence)
        with self.assertRaises(optimizer.ContractError) as forged_transition:
            optimizer.validate_state_transition(
                "staged",
                "canary",
                lifecycle_receipt=forged_receipt,
                **context,
            )
        self.assertEqual(forged_transition.exception.code, "state_transition_evidence_invalid")
        self.assertEqual(
            optimizer.validate_state_transition(
                "staged",
                "canary",
                lifecycle_receipt=canary,
                **context,
            ),
            optimizer.LatencyState.CANARY,
        )
        with self.assertRaises(optimizer.ContractError) as replay:
            optimizer.validate_state_transition(
                "staged",
                "canary",
                lifecycle_receipt=canary,
                **context,
            )
        self.assertEqual(replay.exception.code, "state_transition_evidence_invalid")

    def test_runtime_observer_receipt_is_required_fresh_and_authority_bound(self) -> None:
        plan, _, approval = self._lifecycle_seed()
        evidence = self._deployment_evidence(plan, approval)
        observation = evidence.observation_receipt

        with self.assertRaises(optimizer.ContractError) as replay:
            optimizer.issue_canary_deployment_evidence(
                self.evaluator_capability,
                self.trust_root,
                approval,
                plan.candidate,
                observation,
            )
        self.assertEqual(replay.exception.code, "canary_deployment_evidence_invalid")

        tampered = replace(observation, healthy=False)
        with self.assertRaises(optimizer.ContractError) as forged:
            optimizer.issue_canary_deployment_evidence(
                self.evaluator_capability,
                self.trust_root,
                approval,
                plan.candidate,
                tampered,
            )
        self.assertEqual(forged.exception.code, "canary_deployment_evidence_invalid")

        _, _, later_approval = self._lifecycle_seed(attempt=2)
        with self.assertRaises(optimizer.ContractError) as stale:
            optimizer.issue_canary_deployment_evidence(
                self.evaluator_capability,
                self.trust_root,
                later_approval,
                plan.candidate,
                observation,
            )
        self.assertEqual(stale.exception.code, "canary_deployment_evidence_invalid")

        unconsumed = self._request_deployment_observation(
            plan,
            later_approval,
            {
                "previous_runtime_identity": evidence.previous_runtime_identity,
                "deployment_identity": evidence.deployment_identity,
                "runtime_identity": evidence.runtime_identity,
                "backend_epoch": evidence.backend_epoch,
                "observation_window_id": evidence.observation_window_id,
                "health_receipt_id": evidence.health_receipt_id,
                "healthy": evidence.healthy,
                "error_receipt_id": evidence.error_receipt_id,
                "error_count": evidence.error_count,
                "sample_receipt_id": evidence.sample_receipt_id,
                "sample_count": evidence.sample_count,
                "rollback_checkpoint_id": evidence.rollback_checkpoint_id,
                "cleanup_proof_id": evidence.cleanup_proof_id,
                "remaining_processes": evidence.remaining_processes,
                "remaining_artifacts": evidence.remaining_artifacts,
                "rollback_ready": evidence.rollback_ready,
            },
        )
        evaluator_only_forgery = optimizer._sign_lifecycle_evidence(
            self.evaluator_capability,
            replace(
                evidence,
                evidence_id="",
                predecessor_id=later_approval.receipt_id,
                _signature="",
                observation_receipt=unconsumed,
            ),
        )
        with self.assertRaises(optimizer.ContractError) as not_consumed:
            optimizer.issue_canary_receipt(
                self.lifecycle_capability,
                self.trust_root,
                later_approval,
                evaluator_only_forgery,
            )
        self.assertEqual(not_consumed.exception.code, "canary_receipt_invalid")

        self.assertFalse(hasattr(optimizer, "RuntimeObserverCapability"))
        self.assertFalse(hasattr(optimizer, "bootstrap_runtime_observer_capability"))
        self.assertFalse(hasattr(optimizer, "issue_canary_deployment_observation"))
        for capability in (
            self.runner_capability,
            self.evaluator_capability,
            self.lifecycle_capability,
        ):
            self.assertFalse(hasattr(capability, "observe"))

        for field in (
            "observer_worker_identity",
            "observer_argv_identity",
            "observer_source_identity",
        ):
            with self.subTest(binding=field), self.assertRaises(
                optimizer.ContractError
            ) as foreign:
                optimizer.issue_canary_deployment_evidence(
                    self.evaluator_capability,
                    self.trust_root,
                    approval,
                    plan.candidate,
                    replace(observation, **{field: self._lifecycle_id(9990)}),
                )
            self.assertEqual(
                foreign.exception.code,
                "canary_deployment_evidence_invalid",
            )

        with self.assertRaises(TypeError):
            optimizer.request_canary_deployment_observation(
                self.trust_root,
                approval,
                plan.candidate,
                healthy=True,
            )

        with self.assertRaises(TypeError):
            optimizer.issue_canary_deployment_evidence(
                self.evaluator_capability,
                self.trust_root,
                approval,
                plan.candidate,
                previous_runtime_identity=observation.previous_runtime_identity,
            )

        ephemeral_authority = self.trust_root.authority_id
        self.trust_root.close()
        with self.assertRaises(optimizer.ContractError) as closed:
            optimizer.request_canary_deployment_observation(
                self.trust_root,
                approval,
                plan.candidate,
            )
        self.assertEqual(closed.exception.code, "coordinator_trust_root_invalid")
        fresh_root, _, _, _ = optimizer._bootstrap_test_coordinator(
            optimizer.IdentitySet.from_mapping(self.identities())
        )
        self.assertNotEqual(fresh_root.authority_id, ephemeral_authority)
        with self.assertRaises(optimizer.ContractError) as unavailable:
            fresh_root._request_runtime_observation({})
        self.assertEqual(unavailable.exception.code, "runtime_observer_unavailable")
        with self.assertRaises(TypeError):
            optimizer.bootstrap_ephemeral_fixed_coordinator(
                optimizer.IdentitySet.from_mapping(self.identities()),
                observer_adapter=self.observer,
            )

    def test_post_approval_states_have_no_unreceipted_terminal_exit(self) -> None:
        for source, target in (
            ("awaiting_approval", "rolled_back"),
            ("awaiting_approval", "failed"),
            ("staged", "rolled_back"),
            ("staged", "cleanup_required"),
            ("canary", "failed"),
            ("canary", "cleanup_required"),
        ):
            with self.subTest(source=source, target=target), self.assertRaises(
                optimizer.ContractError
            ) as blocked:
                optimizer.validate_state_transition(source, target)
            self.assertEqual(blocked.exception.code, "state_transition_invalid")

    def test_acceptance_requires_soak_and_exact_runtime_identity(self) -> None:
        plan, decision, approval = self._lifecycle_seed()
        deployment, canary = self._canary(plan, approval)
        invalid_cases = {
            "runtime-drift": {"runtime_identity": self._lifecycle_id(9001)},
            "epoch-drift": {"backend_epoch": self._lifecycle_id(9002)},
            "config-drift": {"candidate_config": plan.candidate.baseline_config},
            "not-separate": {"soak_receipt_id": deployment.sample_receipt_id},
            "stale-samples": {"sample_count": deployment.sample_count},
            "errors": {"error_count": 1},
            "cleanup-required": {"remaining_artifacts": 1},
            "cleanup-relabel": {"cleanup_proof_id": deployment.cleanup_proof_id},
            "rollback-unready": {"rollback_ready": False},
        }
        for name, overrides in invalid_cases.items():
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                self._soak_evidence(plan, canary, **overrides)
            self.assertEqual(raised.exception.code, "soak_evaluation_evidence_invalid")

        soak = self._soak_evidence(plan, canary)
        forged_soak = replace(soak, _signature=f"hmac-sha256:{0:064x}")
        with self.assertRaises(optimizer.ContractError) as forged:
            optimizer.issue_acceptance_receipt(
                self.lifecycle_capability,
                self.trust_root,
                canary,
                forged_soak,
            )
        self.assertEqual(forged.exception.code, "acceptance_receipt_invalid")

        later_plan, _, later_approval = self._lifecycle_seed(attempt=2)
        _, later_canary = self._canary(later_plan, later_approval, base=3000)
        with self.assertRaises(optimizer.ContractError) as stale:
            optimizer.issue_acceptance_receipt(
                self.lifecycle_capability,
                self.trust_root,
                later_canary,
                soak,
            )
        self.assertEqual(stale.exception.code, "acceptance_receipt_invalid")

        acceptance = optimizer.issue_acceptance_receipt(
            self.lifecycle_capability,
            self.trust_root,
            canary,
            soak,
        )
        context = {
            "candidate_id": plan.candidate.candidate_id,
            "trust_root": self.trust_root,
            "expected_run_id": plan.run_id,
            "expected_evaluation_id": decision.evaluation_id,
            "expected_previous_receipt_id": canary.receipt_id,
        }
        with self.assertRaises(optimizer.ContractError) as stale_predecessor:
            optimizer.validate_state_transition(
                "canary",
                "accepted",
                lifecycle_receipt=acceptance,
                **{**context, "expected_previous_receipt_id": later_canary.receipt_id},
            )
        self.assertEqual(
            stale_predecessor.exception.code,
            "state_transition_evidence_invalid",
        )
        self.assertEqual(
            optimizer.validate_state_transition(
                "canary",
                "accepted",
                lifecycle_receipt=acceptance,
                **context,
            ),
            optimizer.LatencyState.ACCEPTED,
        )
        with self.assertRaises(optimizer.ContractError) as replay:
            optimizer.validate_state_transition(
                "canary",
                "accepted",
                lifecycle_receipt=acceptance,
                **context,
            )
        self.assertEqual(replay.exception.code, "state_transition_evidence_invalid")

    def test_failed_canary_rollback_requires_verified_cleanup(self) -> None:
        plan, decision, approval = self._lifecycle_seed()
        deployment, canary = self._canary(plan, approval)
        context = {
            "candidate_id": plan.candidate.candidate_id,
            "trust_root": self.trust_root,
            "expected_run_id": plan.run_id,
            "expected_evaluation_id": decision.evaluation_id,
            "expected_previous_receipt_id": canary.receipt_id,
        }
        with self.assertRaises(optimizer.ContractError) as missing:
            optimizer.validate_state_transition("canary", "rolled_back", **context)
        self.assertEqual(missing.exception.code, "state_transition_evidence_invalid")

        rollback_values = {
            "candidate_config": plan.candidate.candidate_config,
            "deployed_runtime_identity": deployment.runtime_identity,
            "deployed_backend_epoch": deployment.backend_epoch,
            "observation_window_id": self._lifecycle_id(4004),
            "failure_receipt_id": self._lifecycle_id(4000),
            "rollback_checkpoint_id": deployment.rollback_checkpoint_id,
            "restored_runtime_identity": deployment.previous_runtime_identity,
            "cleanup_proof_id": self._lifecycle_id(4001),
            "remaining_processes": 0,
            "remaining_artifacts": 0,
            "health_receipt_id": self._lifecycle_id(4002),
            "healthy": True,
            "error_receipt_id": self._lifecycle_id(4003),
            "error_count": 0,
        }

        def issue_cleanup(values: dict[str, object]) -> optimizer.RollbackCleanupEvidence:
            observation = self._request_rollback_observation(canary, values)
            return optimizer.issue_rollback_cleanup_evidence(
                self.evaluator_capability,
                self.trust_root,
                canary,
                observation,
            )

        for name, overrides in {
            "process-leak": {"remaining_processes": 1},
            "artifact-leak": {"remaining_artifacts": 1},
            "unhealthy-restore": {"healthy": False},
            "restore-errors": {"error_count": 1},
            "wrong-runtime": {"restored_runtime_identity": deployment.runtime_identity},
        }.items():
            with self.subTest(case=name), self.assertRaises(optimizer.ContractError) as raised:
                issue_cleanup({**rollback_values, **overrides})
            self.assertEqual(raised.exception.code, "rollback_cleanup_evidence_invalid")

        cleanup = issue_cleanup(rollback_values)
        rollback = optimizer.issue_rollback_receipt(
            self.lifecycle_capability,
            self.trust_root,
            canary,
            cleanup,
        )
        forged = replace(cleanup, _signature=f"hmac-sha256:{0:064x}")
        with self.assertRaises(optimizer.ContractError) as forged_receipt:
            optimizer.issue_rollback_receipt(
                self.lifecycle_capability,
                self.trust_root,
                canary,
                forged,
            )
        self.assertEqual(forged_receipt.exception.code, "rollback_receipt_invalid")
        self.assertEqual(
            optimizer.validate_state_transition(
                "canary",
                "rolled_back",
                lifecycle_receipt=rollback,
                **context,
            ),
            optimizer.LatencyState.ROLLED_BACK,
        )
        with self.assertRaises(optimizer.ContractError) as replay:
            optimizer.validate_state_transition(
                "canary",
                "rolled_back",
                lifecycle_receipt=rollback,
                **context,
            )
        self.assertEqual(replay.exception.code, "state_transition_evidence_invalid")

    def test_durable_lifecycle_journal_rejects_replay_forks_and_rolls_back_faults(
        self,
    ) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "lifecycle.sqlite3"
            self.observer = FixedExternalRuntimeObserver()
            self.addCleanup(self.observer.close)
            (
                self.trust_root,
                self.runner_capability,
                self.evaluator_capability,
                self.lifecycle_capability,
            ) = optimizer._bootstrap_test_coordinator(
                identities,
                journal_path=journal,
                observer_adapter=self.observer,
            )
            plan = self.plan("finalist")
            runner_receipt = self.compile_receipt(plan)
            decision = self.evaluate(plan, runner_receipt)
            approval = optimizer.issue_approval_receipt(
                self.lifecycle_capability,
                self.trust_root,
                decision.promotion_evidence,
            )
            evaluation_context = {
                "candidate_id": plan.candidate.candidate_id,
                "feedback": decision.promotion_feedback,
                "promotion_evidence": decision.promotion_evidence,
                "trust_root": self.trust_root,
                "expected_run_id": plan.run_id,
                "expected_receipt_id": runner_receipt.receipt_id,
                "expected_cleanup_proof_id": runner_receipt.cleanup.proof_id,
                "expected_evaluation_id": decision.evaluation_id,
                "expected_attempt": plan.attempt,
            }
            self.assertEqual(
                optimizer.validate_state_transition(
                    "feedback_ready",
                    "awaiting_approval",
                    **evaluation_context,
                ),
                optimizer.LatencyState.AWAITING_APPROVAL,
            )
            lifecycle_context = {
                "candidate_id": plan.candidate.candidate_id,
                "trust_root": self.trust_root,
                "expected_run_id": plan.run_id,
                "expected_evaluation_id": decision.evaluation_id,
            }
            self.assertEqual(
                optimizer.validate_state_transition(
                    "awaiting_approval",
                    "staged",
                    lifecycle_receipt=approval,
                    **lifecycle_context,
                ),
                optimizer.LatencyState.STAGED,
            )
            with self.assertRaises(optimizer.ContractError) as unreceipted_staged_exit:
                optimizer.validate_state_transition("staged", "cleanup_required")
            self.assertEqual(
                unreceipted_staged_exit.exception.code,
                "state_transition_invalid",
            )

            deployment = self._deployment_evidence(plan, approval, base=5000)
            self.trust_root.close()
            (
                self.trust_root,
                self.runner_capability,
                self.evaluator_capability,
                self.lifecycle_capability,
            ) = optimizer._bootstrap_test_coordinator(
                identities,
                journal_path=journal,
                observer_adapter=self.observer,
            )
            lifecycle_context["trust_root"] = self.trust_root
            with self.assertRaises(optimizer.ContractError) as transition_replay:
                optimizer.validate_state_transition(
                    "awaiting_approval",
                    "staged",
                    lifecycle_receipt=approval,
                    **lifecycle_context,
                )
            self.assertEqual(
                transition_replay.exception.code,
                "state_transition_evidence_invalid",
            )
            with self.assertRaises(optimizer.ContractError) as observation_replay:
                optimizer.issue_canary_deployment_evidence(
                    self.evaluator_capability,
                    self.trust_root,
                    approval,
                    plan.candidate,
                    deployment.observation_receipt,
                )
            self.assertEqual(
                observation_replay.exception.code,
                "canary_deployment_evidence_invalid",
            )

            canary = optimizer.issue_canary_receipt(
                self.lifecycle_capability,
                self.trust_root,
                approval,
                deployment,
            )
            self.assertEqual(
                optimizer.validate_state_transition(
                    "staged",
                    "canary",
                    lifecycle_receipt=canary,
                    expected_previous_receipt_id=approval.receipt_id,
                    **lifecycle_context,
                ),
                optimizer.LatencyState.CANARY,
            )
            with self.assertRaises(optimizer.ContractError) as unreceipted_canary_exit:
                optimizer.validate_state_transition("canary", "failed")
            self.assertEqual(
                unreceipted_canary_exit.exception.code,
                "state_transition_invalid",
            )
            soak = self._soak_evidence(plan, canary, base=6000)
            acceptance = optimizer.issue_acceptance_receipt(
                self.lifecycle_capability,
                self.trust_root,
                canary,
                soak,
            )
            rollback_observation = self._request_rollback_observation(
                canary,
                {
                    "candidate_config": plan.candidate.candidate_config,
                    "deployed_runtime_identity": deployment.runtime_identity,
                    "deployed_backend_epoch": deployment.backend_epoch,
                    "observation_window_id": self._lifecycle_id(7000),
                    "failure_receipt_id": self._lifecycle_id(7001),
                    "rollback_checkpoint_id": deployment.rollback_checkpoint_id,
                    "restored_runtime_identity": deployment.previous_runtime_identity,
                    "cleanup_proof_id": self._lifecycle_id(7002),
                    "remaining_processes": 0,
                    "remaining_artifacts": 0,
                    "health_receipt_id": self._lifecycle_id(7003),
                    "healthy": True,
                    "error_receipt_id": self._lifecycle_id(7004),
                    "error_count": 0,
                },
            )
            cleanup = optimizer.issue_rollback_cleanup_evidence(
                self.evaluator_capability,
                self.trust_root,
                canary,
                rollback_observation,
            )
            rollback = optimizer.issue_rollback_receipt(
                self.lifecycle_capability,
                self.trust_root,
                canary,
                cleanup,
            )

            self.trust_root.close()
            (
                self.trust_root,
                self.runner_capability,
                self.evaluator_capability,
                self.lifecycle_capability,
            ) = optimizer._bootstrap_test_coordinator(
                identities,
                journal_path=journal,
                observer_adapter=self.observer,
            )
            lifecycle_context["trust_root"] = self.trust_root

            self.assertFalse(
                self.trust_root._consume_lifecycle_transition(
                    self._lifecycle_id(7999),
                    canary.receipt_id,
                    candidate_id=plan.candidate.candidate_id,
                    run_id=plan.run_id,
                    evaluation_id=decision.evaluation_id,
                    source_state="canary",
                    target_state="invalid",
                )
            )
            self.assertEqual(
                optimizer.validate_state_transition(
                    "canary",
                    "accepted",
                    lifecycle_receipt=acceptance,
                    expected_previous_receipt_id=canary.receipt_id,
                    **lifecycle_context,
                ),
                optimizer.LatencyState.ACCEPTED,
            )
            self.trust_root.close()
            reopened_root, _, _, _ = optimizer._bootstrap_test_coordinator(
                identities,
                journal_path=journal,
                observer_adapter=self.observer,
            )
            lifecycle_context["trust_root"] = reopened_root
            with self.assertRaises(optimizer.ContractError) as forked_branch:
                optimizer.validate_state_transition(
                    "canary",
                    "rolled_back",
                    lifecycle_receipt=rollback,
                    expected_previous_receipt_id=canary.receipt_id,
                    **lifecycle_context,
                )
            self.assertEqual(
                forked_branch.exception.code,
                "state_transition_evidence_invalid",
            )
            reopened_root.close()

    def test_promotion_rejects_stale_evidence_from_an_earlier_attempt(self) -> None:
        candidate = self.candidate()
        first_plan = lab.build_runner_plan(
            candidate,
            profile="finalist",
            attempt=1,
            trust_root=self.trust_root,
        )
        first_receipt = self.compile_receipt(first_plan)
        first_decision = self.evaluate(first_plan, first_receipt)
        current_plan = lab.build_runner_plan(
            candidate,
            profile="finalist",
            attempt=2,
            trust_root=self.trust_root,
        )
        current_receipt = self.compile_receipt(current_plan)
        current_decision = self.evaluate(current_plan, current_receipt)
        current_evaluation = {
            "expected_run_id": current_plan.run_id,
            "expected_receipt_id": current_receipt.receipt_id,
            "expected_cleanup_proof_id": current_receipt.cleanup.proof_id,
            "expected_evaluation_id": current_decision.evaluation_id,
            "expected_attempt": current_plan.attempt,
        }

        with self.assertRaises(optimizer.ContractError) as stale:
            optimizer.validate_state_transition(
                "feedback_ready",
                "awaiting_approval",
                candidate_id=candidate.candidate_id,
                feedback=first_decision.promotion_feedback,
                promotion_evidence=first_decision.promotion_evidence,
                trust_root=self.trust_root,
                **current_evaluation,
            )
        self.assertEqual(stale.exception.code, "state_transition_evidence_invalid")

        self.assertEqual(
            optimizer.validate_state_transition(
                "feedback_ready",
                "awaiting_approval",
                candidate_id=candidate.candidate_id,
                feedback=current_decision.promotion_feedback,
                promotion_evidence=current_decision.promotion_evidence,
                trust_root=self.trust_root,
                **current_evaluation,
            ),
            optimizer.LatencyState.AWAITING_APPROVAL,
        )

    def test_hard_gates_fail_closed_before_latency_can_compensate(self) -> None:
        plan = self.plan()
        cases: list[tuple[str, dict, str, str, str]] = []

        incomplete = self.receipt_payload(plan)
        incomplete["samples"]["warmCandidate"] -= 1
        cases.append(("incomplete", incomplete, "inconclusive", "insufficient_samples", "reliability"))

        no_execution = self.receipt_payload(plan)
        no_execution["resources"]["maxConcurrentRequests"] = 0
        cases.append(("no-execution", no_execution, "inconclusive", "insufficient_samples", "reliability"))

        interference = self.receipt_payload(plan)
        interference["checks"]["externalInterferenceSamples"] = 1
        cases.append(("interference", interference, "inconclusive", "environment_drift", "reliability"))

        reliability = self.receipt_payload(plan)
        reliability["checks"]["oomCount"] = 1
        reliability["candidateMetrics"].update(
            {
                "postSttMainWriteP95Ms": 10.0,
                "rawFirstTokenP95Ms": 20.0,
                "rawToSafeSpeechP95Ms": 10.0,
                "safePrefixCommitP95Ms": 40.0,
                "ttsFirstPcmP95Ms": 20.0,
                "warmAnswerFirstPcmP50Ms": 50.0,
                "warmAnswerFirstPcmP95Ms": 100.0,
                "warmAnswerFirstPcmP99Ms": 150.0,
                "restartReadyAnswerFirstPcmP95Ms": 100.0,
                "restartStartupToReadyP95Ms": 1_000.0,
            }
        )
        cases.append(("reliability", reliability, "rejected", "reliability_failed", "reliability"))

        safety = self.receipt_payload(plan)
        safety["checks"]["safetyFailures"] = 1
        cases.append(("safety", safety, "rejected", "safety_failed", "safety"))

        unsafe_prefix = self.receipt_payload(plan)
        unsafe_prefix["checks"]["unsafePrefixCount"] = 1
        cases.append(("unsafe-prefix", unsafe_prefix, "rejected", "safety_failed", "safety"))

        quality = self.receipt_payload(plan)
        quality["checks"]["qualityFailures"] = 1
        cases.append(("quality", quality, "rejected", "quality_regressed", "quality"))

        mismatch = self.receipt_payload(plan)
        mismatch["equivalence"]["matches"] -= 1
        cases.append(("equivalence", mismatch, "inconclusive", "quality_review_required", "quality"))

        resource = self.receipt_payload(plan)
        resource["candidateMetrics"]["gpuMinFreeMiB"] = 4095.0
        cases.append(("resource", resource, "rejected", "resource_failed", "resource"))

        latency = self.receipt_payload(plan)
        latency["candidateMetrics"].update(
            {
                "warmAnswerFirstPcmP50Ms": 735.0,
                "warmAnswerFirstPcmP95Ms": 800.0,
            }
        )
        cases.append(("latency", latency, "rejected", "latency_regressed", "latency"))

        p99_regression = self.receipt_payload(plan)
        p99_regression["candidateMetrics"]["warmAnswerFirstPcmP99Ms"] = 1200.0
        cases.append(
            (
                "p99-regression",
                p99_regression,
                "rejected",
                "latency_regressed",
                "latency",
            )
        )

        restart_startup_regression = self.receipt_payload(plan)
        restart_startup_regression["candidateMetrics"][
            "restartStartupToReadyP95Ms"
        ] = 70_000.0
        cases.append(
            (
                "restart-startup-regression",
                restart_startup_regression,
                "rejected",
                "latency_regressed",
                "latency",
            )
        )

        runner_failed = self.receipt_payload(plan)
        runner_failed["status"] = "runner_failed"
        cases.append(("runner", runner_failed, "inconclusive", "runner_failed", "reliability"))

        ambiguous = self.receipt_payload(plan)
        ambiguous["status"] = "ambiguous"
        cases.append(
            (
                "ambiguous",
                ambiguous,
                "inconclusive",
                "runner_outcome_ambiguous",
                "reliability",
            )
        )

        candidate_failed = self.receipt_payload(plan)
        candidate_failed["status"] = "candidate_failed"
        cases.append(("candidate", candidate_failed, "rejected", "candidate_failed", "reliability"))

        for name, payload, verdict, code, gate in cases:
            with self.subTest(case=name):
                decision = self.evaluate(plan, self.compile_receipt(plan, payload))
                self.assertEqual((decision.verdict, decision.code, decision.gate), (verdict, code, gate))
                self.assertIsNone(decision.promotion_feedback)

    def test_slo_path_frontier_and_no_improvement_are_distinct(self) -> None:
        screening_plan = self.plan("screening")
        screening_decision = self.evaluate(
            screening_plan,
            self.compile_receipt(screening_plan),
        )
        self.assertEqual(
            (screening_decision.verdict, screening_decision.code),
            ("frontier", "screening_passed"),
        )
        self.assertIsNone(screening_decision.promotion_feedback)
        self.assertIsNone(screening_decision.promotion_evidence)

        plan = self.plan("finalist")

        slo = self.receipt_payload(plan)
        slo["baselineMetrics"].update(
            {
                "postSttMainWriteP95Ms": 90.0,
                "rawFirstTokenP95Ms": 300.0,
                "rawToSafeSpeechP95Ms": 60.0,
                "safePrefixCommitP95Ms": 430.0,
                "ttsFirstPcmP95Ms": 200.0,
                "warmAnswerFirstPcmP50Ms": 590.0,
                "warmAnswerFirstPcmP95Ms": 760.0,
                "warmAnswerFirstPcmP99Ms": 920.0,
            }
        )
        slo["candidateMetrics"].update(
            {
                "postSttMainWriteP95Ms": 95.0,
                "rawFirstTokenP95Ms": 305.0,
                "rawToSafeSpeechP95Ms": 65.0,
                "safePrefixCommitP95Ms": 440.0,
                "ttsFirstPcmP95Ms": 210.0,
                "warmAnswerFirstPcmP50Ms": 595.0,
                "warmAnswerFirstPcmP95Ms": 745.0,
                "warmAnswerFirstPcmP99Ms": 890.0,
            }
        )
        slo["statistics"].update(
            {
                "warmAnswerFirstPcmP95DeltaCiLowMs": -25.0,
                "warmAnswerFirstPcmP95DeltaCiHighMs": -5.0,
                "warmAnswerFirstPcmP95EffectSize": -0.3,
            }
        )
        slo_decision = self.evaluate(
            plan,
            self.compile_receipt(plan, slo, sync_statistics=False),
        )
        self.assertEqual(slo_decision.verdict, "eligible")

        insufficient_statistics = self.receipt_payload(plan)
        insufficient_statistics["statistics"]["bootstrapReplicates"] = 1000
        insufficient_decision = self.evaluate(
            plan,
            self.compile_receipt(plan, insufficient_statistics),
        )
        self.assertEqual(
            (
                insufficient_decision.verdict,
                insufficient_decision.code,
                insufficient_decision.gate,
            ),
            ("frontier", "statistical_evidence_insufficient", "statistics"),
        )

        inconsistent_statistics = self.receipt_payload(plan)
        inconsistent_statistics["statistics"].update(
            {
                "warmAnswerFirstPcmP95DeltaCiLowMs": -10.0,
                "warmAnswerFirstPcmP95DeltaCiHighMs": 10.0,
                "warmAnswerFirstPcmP95EffectSize": -0.8,
            }
        )
        with self.assertRaises(optimizer.ContractError) as inconsistent:
            self.compile_receipt(
                plan,
                inconsistent_statistics,
                sync_statistics=False,
            )
        self.assertEqual(inconsistent.exception.code, "runner_statistics_invalid")

    def test_finalist_rejects_component_regression_despite_end_to_end_gain(self) -> None:
        plan = self.plan("finalist")
        payload = self.receipt_payload(plan)
        payload["baselineMetrics"].update(
            {
                "postSttMainWriteP95Ms": 120.0,
                "ttsFirstPcmP95Ms": 100.0,
                "firstSentenceCommitP50Ms": 500.0,
                "firstSentenceCommitP95Ms": 600.0,
                "warmAnswerFirstPcmP50Ms": 700.0,
                "warmAnswerFirstPcmP95Ms": 900.0,
                "warmAnswerFirstPcmP99Ms": 1050.0,
            }
        )
        payload["candidateMetrics"].update(
            {
                "postSttMainWriteP95Ms": 500.0,
                "safePrefixCommitP95Ms": 520.0,
                "ttsFirstPcmP95Ms": 500.0,
                "firstSentenceCommitP50Ms": 540.0,
                "firstSentenceCommitP95Ms": 690.0,
                "warmAnswerFirstPcmP50Ms": 550.0,
                "warmAnswerFirstPcmP95Ms": 700.0,
                "warmAnswerFirstPcmP99Ms": 850.0,
            }
        )
        decision = self.evaluate(
            plan,
            self.compile_receipt(plan, payload),
        )
        self.assertEqual(
            (decision.verdict, decision.code, decision.gate),
            ("rejected", "latency_regressed", "latency"),
        )

        frontier = self.receipt_payload(plan)
        frontier["candidateMetrics"].update(
            {
                "postSttMainWriteP95Ms": 110.0,
                "rawFirstTokenP95Ms": 390.0,
                "rawToSafeSpeechP95Ms": 85.0,
                "safePrefixCommitP95Ms": 520.0,
                "ttsFirstPcmP95Ms": 230.0,
                "warmAnswerFirstPcmP50Ms": 690.0,
                "warmAnswerFirstPcmP95Ms": 880.0,
                "warmAnswerFirstPcmP99Ms": 1030.0,
                "restartReadyAnswerFirstPcmP95Ms": 1080.0,
            }
        )
        frontier_decision = self.evaluate(
            plan, self.compile_receipt(plan, frontier)
        )
        self.assertEqual(
            (frontier_decision.verdict, frontier_decision.code),
            ("frontier", "frontier_improved"),
        )

        no_improvement = copy.deepcopy(frontier)
        no_improvement["candidateMetrics"].update(
            {
                "warmAnswerFirstPcmP50Ms": 700.0,
                "warmAnswerFirstPcmP95Ms": 905.0,
                "warmAnswerFirstPcmP99Ms": 1060.0,
            }
        )
        no_improvement_decision = self.evaluate(
            plan, self.compile_receipt(plan, no_improvement)
        )
        self.assertEqual(
            (no_improvement_decision.verdict, no_improvement_decision.code),
            ("rejected", "no_material_improvement"),
        )

    def test_finalist_profile_and_self_test(self) -> None:
        self.assertEqual(self.plan("screening").spec.max_runtime_ms, 3_600_000)
        plan = self.plan("finalist")
        self.assertEqual(plan.spec.max_runtime_ms, 14_400_000)
        receipt = self.compile_receipt(plan)
        self.assertEqual(receipt.samples.warm_candidate, 200)
        self.assertEqual(receipt.samples.restart_ready_candidate, 30)
        self.assertEqual(receipt.samples.soak_turns, 1000)
        self.assertEqual(self.evaluate(plan, receipt).verdict, "eligible")
        lab.self_test()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(lab.main(["--self-test"]), 0)

        private_path = r"\\server\share\private.json"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(lab.main(["--input", private_path]), 2)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"ok": False, "code": "arguments_invalid"},
        )
        self.assertNotIn(private_path, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
