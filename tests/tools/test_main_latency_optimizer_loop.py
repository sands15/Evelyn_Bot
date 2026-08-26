from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import threading
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_external_runner as external_runner  # noqa: E402
import main_latency_fixed_lab_adapter as fixed_lab  # noqa: E402
import main_latency_lab_contract as lab  # noqa: E402
import main_latency_optimizer_loop as loop  # noqa: E402
import optimize_main_latency as optimizer  # noqa: E402


class MainLatencyOptimizerLoopTests(unittest.TestCase):
    @staticmethod
    def identities() -> dict[str, str]:
        return {
            key: f"sha256:{index:064x}"
            for index, key in enumerate(optimizer.IDENTITY_KEYS, start=1)
        }

    @staticmethod
    def baseline() -> optimizer.MainLatencyConfig:
        return optimizer.MainLatencyConfig.from_mapping(
            {
                "main.batch": 2048,
                "main.ubatch": 1024,
                "main.cacheReuse": 256,
                "main.cacheRamMiB": 8192,
                "main.cudaGraph": 1,
                "main.swaFull": 0,
            }
        )

    def setUp(self) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        (
            self.root,
            self.runner_capability,
            self.evaluator_capability,
            self.lifecycle_capability,
        ) = optimizer._bootstrap_test_coordinator(identities)

    @staticmethod
    def _metrics(*, candidate: bool) -> dict[str, float]:
        if candidate:
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

    def _unsigned_receipt(
        self,
        plan: lab.RunnerPlan,
        *,
        status: str = "completed",
        safety_violations: int = 0,
    ) -> dict:
        spec = plan.spec
        checks = {key: 0 for key in lab.CHECK_FIELDS}
        checks["safetyFailures"] = safety_violations
        return {
            "schema": lab.RUNNER_RECEIPT_SCHEMA,
            "runId": plan.run_id,
            "candidateId": plan.candidate.candidate_id,
            "identities": plan.candidate.identities.to_dict(),
            "baselineConfig": plan.candidate.baseline_config.to_dict(),
            "candidateConfig": plan.candidate.candidate_config.to_dict(),
            "status": status,
            "samples": {
                "warmBaseline": spec.warm_per_condition,
                "warmCandidate": spec.warm_per_condition,
                "restartReadyBaseline": spec.restart_ready_per_condition,
                "restartReadyCandidate": spec.restart_ready_per_condition,
                "soakTurns": spec.soak_turns,
                "abbaBlocks": spec.abba_blocks,
            },
            "baselineMetrics": self._metrics(candidate=False),
            "candidateMetrics": self._metrics(candidate=True),
            "statistics": {
                "schema": lab.STATISTICS_SCHEMA,
                "method": "paired-bootstrap-abba-v1",
                "bootstrapReplicates": 2000,
                "confidenceLevel": 0.95,
                "warmAnswerFirstPcmP95DeltaCiLowMs": -230.0,
                "warmAnswerFirstPcmP95DeltaCiHighMs": -170.0,
                "warmAnswerFirstPcmP95EffectSize": -0.8,
            },
            "checks": checks,
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

    def _signed(self, plan: lab.RunnerPlan, **changes: object) -> dict:
        return lab.issue_runner_receipt(
            plan,
            self._unsigned_receipt(plan, **changes),
            trust_root=self.root,
            runner_capability=self.runner_capability,
        ).to_dict()

    def _host_restoration_proof(
        self,
        plan: lab.RunnerPlan,
        receipt: lab.RunnerReceipt,
    ) -> lab.HostRestorationProof:
        return lab.issue_host_restoration_proof(
            plan,
            receipt,
            {
                "schema": lab.HOST_RESTORATION_OBSERVATION_SCHEMA,
                "status": "clean",
                "dockerInitialState": "stopped",
                "dockerFinalState": "stopped",
                "dockerStartedByRun": True,
                "driverModel": "wddm",
                "baselineFreeMiB": 30_000.0,
                "postFreeMinMiB": 29_900.0,
                "totalMiB": 32_607.0,
                "maxUtilizationPct": 1.0,
                "stableObservations": 3,
                "globalRunningContainers": 0,
            },
            trust_root=self.root,
            lifecycle_capability=self.lifecycle_capability,
        )

    @staticmethod
    def _proposal(cache_reuse: int) -> dict[str, int]:
        return {"main.cacheReuse": cache_reuse}

    @staticmethod
    def _host_lifecycle(calls: list[str] | None = None) -> object:
        events = calls if calls is not None else []

        class FakeHostLifecycle:
            def prepare(self) -> None:
                events.append("prepare")

            def verify_measurement_preflight(self) -> None:
                events.append("preflight")

            def finish_after_owned_cleanup(self) -> dict:
                events.append("finish")
                return {
                    "schema": lab.HOST_RESTORATION_OBSERVATION_SCHEMA,
                    "status": "clean",
                    "dockerInitialState": "stopped",
                    "dockerFinalState": "stopped",
                    "dockerStartedByRun": True,
                    "driverModel": "wddm",
                    "baselineFreeMiB": 30_000.0,
                    "postFreeMinMiB": 29_900.0,
                    "totalMiB": 32_607.0,
                    "maxUtilizationPct": 1.0,
                    "stableObservations": 3,
                    "globalRunningContainers": 0,
                }

            def best_effort_restore(self) -> dict:
                events.append("restore")
                return {"status": "clean", "code": "verified"}

        return FakeHostLifecycle()

    @staticmethod
    def _private_timing_diagnostics(total: int = 30) -> dict:
        def metrics(count: int, prompt_eval: float) -> dict:
            return {
                "promptEvalMs": {"sampleCount": count, "p50": prompt_eval, "p95": prompt_eval + 2.0},
                "promptCacheHitRatio": {"sampleCount": count, "p50": 0.9, "p95": 0.95},
                "promptTokensProcessed": {"sampleCount": count, "p50": 20.0, "p95": 24.0},
                "promptTokensCached": {"sampleCount": count, "p50": 180.0, "p95": 180.0},
                "promptTokensTotal": {"sampleCount": count, "p50": 200.0, "p95": 204.0},
                "queueMs": {"sampleCount": count, "p50": 1.0, "p95": 2.0},
                "routeMs": {"sampleCount": count, "p50": 3.0, "p95": 5.0},
                "contextMs": {"sampleCount": count, "p50": 8.0, "p95": 10.0},
                "rawFirstTokenMs": {"sampleCount": count, "p50": 260.0, "p95": 300.0},
                "safePrefixCommitMs": {"sampleCount": count, "p50": 400.0, "p95": 430.0},
                "answerFirstPcmMs": {"sampleCount": count, "p50": 650.0, "p95": 800.0},
            }

        def condition(prompt_eval: float) -> dict:
            return {
                "afterActivation": metrics(total, prompt_eval + 1.0),
                "resident": metrics(total, prompt_eval - 1.0),
            }

        return {
            "schema": fixed_lab.PRIVATE_TIMING_SCHEMA,
            "baseline": condition(6.0),
            "candidate": condition(4.0),
        }

    def test_feedback_is_returned_to_proposer_and_eligible_stops_at_approval(self) -> None:
        contexts: list[loop.ProposerContext] = []

        def proposer(context: loop.ProposerContext) -> dict[str, int]:
            contexts.append(context)
            return self._proposal(64)

        def runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            return self._signed(
                plan,
                safety_violations=1 if plan.attempt == 1 else 0,
            )

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=proposer,
            runner=runner,
            host_restoration_prover=self._host_restoration_proof,
        )

        self.assertEqual(result.state, optimizer.LatencyState.AWAITING_APPROVAL)
        self.assertEqual(result.stop_reason, "eligible")
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].attempt, 2)
        self.assertEqual(contexts[0].feedback.verdict, "rejected")
        self.assertEqual(contexts[0].feedback.code, "safety_failed")
        proposer_context = contexts[0].to_dict()
        self.assertEqual(proposer_context["schema"], loop.LOOP_CONTEXT_SCHEMA)
        self.assertEqual(
            proposer_context["lastCandidate"]["candidateConfig"]["main.ubatch"],
            2048,
        )
        self.assertEqual(
            proposer_context["lastCandidate"]["changes"],
            [{"key": "main.ubatch", "from": 1024, "to": 2048}],
        )
        self.assertEqual(
            proposer_context["lastCandidate"]["stageDeltas"],
            {
                "postSttMainWriteP95DeltaMs": -30.0,
                "rawFirstTokenP95DeltaMs": -100.0,
                "rawToSafeSpeechP95DeltaMs": -30.0,
                "safePrefixCommitP95DeltaMs": -120.0,
                "ttsFirstPcmP95DeltaMs": -40.0,
            },
        )
        self.assertEqual(
            proposer_context["lastCandidate"]["checkCounts"]["safetyFailures"],
            1,
        )
        self.assertIsNone(proposer_context["bestFrontierConfig"])
        self.assertEqual(proposer_context["bestFrontierChanges"], [])
        self.assertIsNotNone(result.approval_context)
        self.assertTrue(result.to_dict()["awaitingApproval"])
        self.assertNotIn("signature", json.dumps(result.to_dict()).lower())
        self.assertNotIn("content", json.dumps(result.to_dict()).lower())

    def test_finalist_without_host_restoration_provider_fails_closed(self) -> None:
        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: self._proposal(128),
            runner=lambda plan, _cancel: self._signed(plan),
        )

        self.assertEqual(result.state, optimizer.LatencyState.FAILED)
        self.assertEqual(result.stop_reason, "evaluation_inconclusive")
        self.assertEqual(
            [(item.profile, item.code) for item in result.feedback],
            [
                ("screening", "screening_passed"),
                ("finalist", "host_restoration_required"),
            ],
        )
        self.assertIsNone(result.approval_context)

    def test_proposer_context_preserves_best_frontier_without_changing_public_v1(self) -> None:
        contexts: list[loop.ProposerContext] = []

        def proposer(context: loop.ProposerContext) -> dict[str, int] | None:
            contexts.append(context)
            return None

        def runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            payload = self._unsigned_receipt(plan)
            candidate_metrics = dict(payload["baselineMetrics"])
            candidate_metrics.update(
                {
                    "firstSentenceCommitP50Ms": 640.0,
                    "firstSentenceCommitP95Ms": 790.0,
                    "warmAnswerFirstPcmP50Ms": 690.0,
                    "warmAnswerFirstPcmP95Ms": 890.0,
                    "warmAnswerFirstPcmP99Ms": 1040.0,
                }
            )
            payload["candidateMetrics"] = candidate_metrics
            payload["statistics"].update(
                {
                    "warmAnswerFirstPcmP95DeltaCiLowMs": -20.0,
                    "warmAnswerFirstPcmP95DeltaCiHighMs": 0.0,
                    "warmAnswerFirstPcmP95EffectSize": -0.1,
                }
            )
            return lab.issue_runner_receipt(
                plan,
                payload,
                trust_root=self.root,
                runner_capability=self.runner_capability,
            ).to_dict()

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=proposer,
            runner=runner,
        )

        self.assertEqual(result.stop_reason, "proposer_stopped")
        self.assertEqual(len(contexts), 1)
        rendered = contexts[0].to_dict()
        self.assertEqual(contexts[0].attempt, 2)
        self.assertEqual(contexts[0].feedback.code, "frontier_improved")
        self.assertEqual(
            rendered["bestFrontierConfig"]["main.ubatch"], 2048
        )
        self.assertEqual(
            rendered["bestFrontierChanges"],
            [{"key": "main.ubatch", "from": 1024, "to": 2048}],
        )
        self.assertEqual(
            rendered["lastCandidate"]["candidateConfig"],
            rendered["bestFrontierConfig"],
        )
        self.assertEqual(
            rendered["lastCandidate"]["stageDeltas"],
            {
                "postSttMainWriteP95DeltaMs": 0.0,
                "rawFirstTokenP95DeltaMs": 0.0,
                "rawToSafeSpeechP95DeltaMs": 0.0,
                "safePrefixCommitP95DeltaMs": 0.0,
                "ttsFirstPcmP95DeltaMs": 0.0,
            },
        )
        public_feedback = result.to_dict()["feedback"][0]
        self.assertEqual(public_feedback["schema"], loop.LOOP_FEEDBACK_SCHEMA)
        for private_field in (
            "candidateConfig",
            "changes",
            "stageDeltas",
            "checkCounts",
            "bestFrontierConfig",
        ):
            self.assertNotIn(private_field, public_feedback)

    def test_operator_report_adds_only_verified_aggregate_measurements(self) -> None:
        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: self._proposal(128),
            runner=lambda plan, _cancel: self._signed(plan),
            host_restoration_prover=self._host_restoration_proof,
        )

        legacy = result.to_dict()
        self.assertEqual(
            set(legacy),
            {
                "schema",
                "state",
                "stopReason",
                "attemptCount",
                "attemptedCandidateIds",
                "feedback",
                "fallbackCount",
                "transportCleanup",
                "awaitingApproval",
            },
        )
        self.assertNotIn("measurements", legacy)
        self.assertNotIn("diagnostics", legacy)

        report = result.operator_report_dict()
        self.assertEqual(
            set(report), {"schema", "result", "measurements", "diagnostics"}
        )
        self.assertEqual(report["schema"], loop.OPERATOR_REPORT_SCHEMA)
        self.assertEqual(report["result"], legacy)
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(
            [item["profile"] for item in report["measurements"]],
            ["screening", "finalist"],
        )
        for measurement in report["measurements"]:
            self.assertEqual(
                measurement["baselineMetrics"], self._metrics(candidate=False)
            )
            self.assertEqual(
                measurement["candidateMetrics"], self._metrics(candidate=True)
            )
            self.assertEqual(measurement["baselineConfig"], self.baseline().to_dict())
            self.assertEqual(measurement["status"], "completed")

        serialized = json.dumps(report, sort_keys=True).lower()
        for forbidden in (
            "prompt",
            "audio",
            "content",
            "signature",
            "identities",
            "attestation",
            "fingerprint",
            "exception",
            "message",
            "traceback",
            "path",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_private_timing_reaches_operator_report_and_proposer_context_only(self) -> None:
        contexts: list[loop.ProposerContext] = []
        timing = self._private_timing_diagnostics()

        def proposer(context: loop.ProposerContext) -> None:
            contexts.append(context)
            return None

        def runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            receipt = self._signed(plan, safety_violations=1)
            cleanup = {
                "schema": lab.CLEANUP_SCHEMA,
                "runId": plan.run_id,
                "owner": lab.LAB_OWNER,
                "status": "clean",
                "remainingProcesses": 0,
                "remainingGpuAllocations": 0,
                "remainingArtifacts": 0,
            }
            return loop._decode_external_runner_output(
                json.dumps(
                    {
                        "schema": external_runner.PRIVATE_RESULT_SCHEMA,
                        "receipt": receipt,
                        "timingDiagnostics": timing,
                    },
                    separators=(",", ":"),
                ).encode("ascii"),
                0,
                cleanup,
            )

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=proposer,
            runner=runner,
        )

        self.assertEqual(result.stop_reason, "proposer_stopped")
        self.assertEqual(
            contexts[0].to_dict()["lastCandidate"]["timingDiagnostics"], timing
        )
        self.assertEqual(
            result.operator_report_dict()["measurements"][0]["timingDiagnostics"],
            timing,
        )
        self.assertNotIn("timingDiagnostics", result.to_dict()["feedback"][0])
        self.assertEqual(
            set(result.to_dict()),
            {
                "schema",
                "state",
                "stopReason",
                "attemptCount",
                "attemptedCandidateIds",
                "feedback",
                "fallbackCount",
                "transportCleanup",
                "awaitingApproval",
            },
        )

    def test_proposer_context_contains_no_coordinator_capabilities(self) -> None:
        seen: list[dict] = []

        def proposer(context: loop.ProposerContext) -> None:
            rendered = context.to_dict()
            seen.append(rendered)
            serialized = json.dumps(rendered, sort_keys=True)
            self.assertNotIn("capability", serialized.lower())
            self.assertNotIn("signature", serialized.lower())
            self.assertNotIn("authority", serialized.lower())
            self.assertFalse(hasattr(context, "trust_root"))
            return None

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=proposer,
            runner=lambda plan, _cancel: self._signed(
                plan,
                safety_violations=1,
            ),
        )
        self.assertEqual(result.stop_reason, "proposer_stopped")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["attempt"], 2)
        self.assertEqual(seen[0]["schema"], "evelyn.latency-loop-context.v2")
        self.assertEqual(
            seen[0]["lastCandidate"]["changes"],
            [{"key": "main.ubatch", "from": 1024, "to": 2048}],
        )
        self.assertIsNone(seen[0]["bestFrontierConfig"])
        self.assertEqual(seen[0]["bestFrontierChanges"], [])

    def test_fixed_localhost_proposer_sends_only_sanitized_context(self) -> None:
        captured: dict[str, object] = {}

        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            @staticmethod
            def geturl() -> str:
                return loop.LOCAL_PROPOSER_URL

            @staticmethod
            def read(_limit: int) -> bytes:
                return json.dumps(
                    {
                        "choices": [
                            {"message": {"content": '{"main.cacheReuse":128}'}}
                        ]
                    }
                ).encode("utf-8")

        class Opener:
            @staticmethod
            def open(request: object, *, timeout: float) -> Response:
                captured["request"] = request
                captured["timeout"] = timeout
                return Response()

        context = loop.ProposerContext(
            1,
            self.baseline().to_dict(),
            {key: tuple(values) for key, values in optimizer.CONFIG_DOMAINS.items()},
            (),
            None,
        )
        with patch.object(loop.urllib_request, "build_opener", return_value=Opener()):
            proposal = loop.FixedLocalhostProposer(timeout_s=3)(context)
        self.assertEqual(proposal, self._proposal(128))
        request = captured["request"]
        self.assertEqual(request.full_url, loop.LOCAL_PROPOSER_URL)
        serialized = request.data.decode("ascii")
        for forbidden in (
            "authority",
            "capability",
            "signature",
            "transcript",
            "audio",
            "reply",
            "path",
            "identity",
        ):
            self.assertNotIn(forbidden, serialized.lower())
        self.assertIn("configDomains", serialized)
        body = json.loads(serialized)
        proposer_input = json.loads(body["messages"][1]["content"])
        self.assertEqual(proposer_input["schema"], "evelyn.latency-loop-context.v2")
        self.assertIsNone(proposer_input["lastCandidate"])
        self.assertIn(
            "only keys whose values differ from baselineconfig",
            body["messages"][0]["content"].lower(),
        )

        Response.read = staticmethod(
            lambda _limit: b'{"choices":[{"message":{"content":"not-json"}}]}'
        )
        with patch.object(loop.urllib_request, "build_opener", return_value=Opener()):
            self.assertEqual(loop.FixedLocalhostProposer()(context), {})

    def test_first_attempt_uses_deterministic_seed_without_calling_proposer(self) -> None:
        proposer_calls: list[loop.ProposerContext] = []
        plans: list[lab.RunnerPlan] = []

        def proposer(context: loop.ProposerContext) -> dict[str, int]:
            proposer_calls.append(context)
            return {"main.cacheRamMiB": 12288}

        def runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            plans.append(plan)
            return self._signed(plan, status="runner_failed")

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=proposer,
            runner=runner,
        )

        self.assertEqual(result.stop_reason, "evaluation_inconclusive")
        self.assertEqual(result.fallback_count, 0)
        self.assertEqual(proposer_calls, [])
        self.assertEqual(len(plans), 1)
        self.assertEqual(
            [(change.key, change.value) for change in plans[0].candidate.changes],
            [("main.ubatch", 2048)],
        )

    def test_malformed_proposal_uses_latency_first_fallback(self) -> None:
        plans: list[lab.RunnerPlan] = []

        def runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            plans.append(plan)
            if plan.attempt == 1:
                return self._signed(plan, safety_violations=1)
            return self._signed(plan, status="runner_failed")

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: {},
            runner=runner,
        )

        self.assertEqual(result.stop_reason, "evaluation_inconclusive")
        self.assertEqual(result.fallback_count, 1)
        self.assertEqual(len(plans), 2)
        self.assertEqual(
            [
                [(change.key, change.value) for change in plan.candidate.changes]
                for plan in plans
            ],
            [
                [("main.ubatch", 2048)],
                [("main.cacheReuse", 128)],
            ],
        )

    def test_malformed_timeout_and_cancel_fail_closed(self) -> None:
        cases = (
            (
                lambda _plan, _cancel: {},
                "runner_receipt_invalid",
                optimizer.LatencyState.FAILED,
            ),
            (
                lambda _plan, _cancel: (_ for _ in ()).throw(
                    loop.RunnerTransportError("runner_timeout")
                ),
                "runner_timeout",
                optimizer.LatencyState.CLEANUP_REQUIRED,
            ),
        )
        for runner, reason, expected_state in cases:
            with self.subTest(reason=reason):
                root, _, evaluator, _ = optimizer._bootstrap_test_coordinator(
                    optimizer.IdentitySet.from_mapping(self.identities())
                )
                result = loop.run_optimizer_loop(
                    baseline_config=self.baseline(),
                    trust_root=root,
                    evaluator_capability=evaluator,
                    proposer=lambda _context: self._proposal(128),
                    runner=runner,
                )
                self.assertEqual(result.state, expected_state)
                self.assertEqual(result.stop_reason, reason)

        cancelled = threading.Event()
        cancelled.set()
        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: self._proposal(128),
            runner=lambda _plan, _cancel: {},
            cancel_event=cancelled,
        )
        self.assertEqual(result.stop_reason, "cancelled")

    def test_external_runner_diagnostic_is_operator_only_and_allowlisted(self) -> None:
        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": "unused",
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }

        def failed_runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            bound_cleanup = dict(clean)
            bound_cleanup["runId"] = plan.run_id
            raise loop.RunnerTransportError(
                "runner_failed",
                bound_cleanup,
                diagnostic_code="runner_statistics_invalid",
            )

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: self._proposal(128),
            runner=failed_runner,
        )

        self.assertEqual(result.stop_reason, "runner_failed")
        self.assertEqual(result.feedback, ())
        self.assertNotIn("diagnostics", result.to_dict())
        report = result.operator_report_dict()
        self.assertEqual(report["measurements"], [])
        self.assertEqual(
            report["diagnostics"],
            [
                {
                    "candidateId": result.attempted_candidate_ids[0],
                    "runId": result.operator_diagnostics[0].run_id,
                    "attempt": 1,
                    "profile": "screening",
                    "layer": "external_runner",
                    "code": "runner_statistics_invalid",
                }
            ],
        )
        with self.assertRaisesRegex(ValueError, "diagnostic_code_invalid"):
            loop.RunnerTransportError(
                "runner_failed", diagnostic_code="private/path/content"
            )

    def test_external_runner_diagnostic_envelope_is_strict(self) -> None:
        cleanup = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": f"sha256:{1:064x}",
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        encoded = json.dumps(
            {
                "schema": external_runner.DIAGNOSTIC_SCHEMA,
                "code": "runner_metrics_invalid",
            },
            separators=(",", ":"),
        ).encode("ascii")
        with self.assertRaises(loop.RunnerTransportError) as raised:
            loop._decode_external_runner_output(encoded, 2, cleanup)
        self.assertEqual(raised.exception.code, "runner_failed")
        self.assertEqual(raised.exception.diagnostic_code, "runner_metrics_invalid")

        unknown = encoded.replace(b"runner_metrics_invalid", b"private_path_exposure")
        with self.assertRaises(loop.RunnerTransportError) as raised:
            loop._decode_external_runner_output(unknown, 2, cleanup)
        self.assertIsNone(raised.exception.diagnostic_code)

        completed_without_private_timing = json.dumps(
            {
                "schema": external_runner.PRIVATE_RESULT_SCHEMA,
                "receipt": {"status": "completed"},
                "timingDiagnostics": {},
            },
            separators=(",", ":"),
        ).encode("ascii")
        with self.assertRaises(loop.RunnerTransportError) as raised:
            loop._decode_external_runner_output(
                completed_without_private_timing, 0, cleanup
            )
        self.assertEqual(raised.exception.code, "runner_malformed")

    def test_loop_is_bounded_to_at_most_twelve_unique_candidates(self) -> None:
        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: self._proposal(128),
            runner=lambda plan, _cancel: self._signed(plan, safety_violations=1),
        )
        self.assertIn(result.stop_reason, {"max_attempts", "candidate_space_exhausted"})
        self.assertLessEqual(result.attempt_count, optimizer.MAX_CANDIDATES)
        self.assertEqual(
            len(set(result.attempted_candidate_ids)),
            result.attempt_count,
        )

    def test_runner_receipt_replay_is_blocked_across_loop_instances(self) -> None:
        signed_receipts: list[dict] = []

        def runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            signed = self._signed(
                plan,
                status="runner_failed",
                safety_violations=len(signed_receipts) % 2,
            )
            signed_receipts.append(signed)
            return copy.deepcopy(signed)

        arguments = {
            "baseline_config": self.baseline(),
            "trust_root": self.root,
            "evaluator_capability": self.evaluator_capability,
            "proposer": lambda _context: self._proposal(128),
            "runner": runner,
        }
        first = loop.run_optimizer_loop(**arguments)
        second = loop.run_optimizer_loop(**arguments)
        self.assertEqual(first.stop_reason, "evaluation_inconclusive")
        self.assertEqual(second.stop_reason, "runner_replay")
        self.assertEqual(signed_receipts[0]["runId"], signed_receipts[1]["runId"])
        self.assertNotEqual(
            signed_receipts[0]["receiptId"],
            signed_receipts[1]["receiptId"],
        )

        synthetic_id = f"sha256:{999:064x}"
        self.assertTrue(self.root._consume_once("runner", synthetic_id))
        self.assertTrue(self.root._consume_once("lifecycle", synthetic_id))
        self.assertFalse(self.root._consume_once("runner", synthetic_id))
        self.assertFalse(self.root._consume_once("lifecycle", synthetic_id))

    def test_fixed_child_gets_one_run_capability_and_installed_adapter_fails_closed(self) -> None:
        candidate = optimizer.compile_candidate(
            optimizer.candidate_proposal(
                self.root.pinned_identities,
                self.baseline(),
                self._proposal(128),
            ),
            trust_root=self.root,
        )
        plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.root,
        )
        transport = loop.FixedSubprocessRunnerTransport(self.runner_capability)
        self.assertNotIn("secret", repr(transport).lower())
        self.assertIsInstance(
            fixed_lab.get_fixed_lab_adapter(), fixed_lab.OwnedDockerLabAdapter
        )

        self.runner_capability._issue_one_run(plan.run_id)
        with self.assertRaisesRegex(ValueError, "runner_capability_consumed"):
            self.runner_capability._issue_one_run(plan.run_id)

        export_plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=2,
            trust_root=self.root,
        )
        one_run = self.runner_capability._issue_one_run(export_plan.run_id)
        envelope = one_run._export_once()
        self.assertEqual(envelope["runId"], export_plan.run_id)
        self.assertRegex(envelope["secret"], r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(ValueError, "runner_capability_consumed"):
            one_run._export_once()

        signing_plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=3,
            trust_root=self.root,
        )
        signing_capability = self.runner_capability._issue_one_run(
            signing_plan.run_id
        )
        signed = lab.issue_runner_receipt(
            signing_plan,
            self._unsigned_receipt(signing_plan),
            trust_root=self.root,
            runner_capability=signing_capability,
        )
        self.assertEqual(
            lab.compile_runner_receipt(
                signing_plan,
                signed.to_dict(),
                trust_root=self.root,
            ).run_id,
            signing_plan.run_id,
        )
        with self.assertRaisesRegex(ValueError, "runner_capability_consumed"):
            lab.issue_runner_receipt(
                signing_plan,
                self._unsigned_receipt(signing_plan),
                trust_root=self.root,
                runner_capability=signing_capability,
            )
        other_plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=4,
            trust_root=self.root,
        )
        foreign_capability_plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=5,
            trust_root=self.root,
        )
        with self.assertRaisesRegex(ValueError, "runner_capability_invalid"):
            lab.issue_runner_receipt(
                other_plan,
                self._unsigned_receipt(other_plan),
                trust_root=self.root,
                runner_capability=self.runner_capability._issue_one_run(
                    foreign_capability_plan.run_id
                ),
            )
        with self.assertRaisesRegex(RuntimeError, "child_process_forbidden"):
            external_runner._deny_process_creation("subprocess.Popen", ())

    def test_fixed_runner_output_overflow_kills_child_promptly(self) -> None:
        candidate = optimizer.compile_candidate(
            optimizer.candidate_proposal(
                self.root.pinned_identities,
                self.baseline(),
                self._proposal(128),
            ),
            trust_root=self.root,
        )
        plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.root,
        )
        killed = threading.Event()

        class Input:
            @staticmethod
            def write(value: bytes) -> int:
                return len(value)

            @staticmethod
            def close() -> None:
                return None

        class Output:
            @staticmethod
            def read(_size: int) -> bytes:
                return b"" if killed.is_set() else b"x" * 8192

            @staticmethod
            def close() -> None:
                return None

        class Process:
            stdin = Input()
            stdout = Output()
            returncode = None

            @staticmethod
            def poll() -> int | None:
                return -9 if killed.is_set() else None

            @staticmethod
            def kill() -> None:
                killed.set()

            @staticmethod
            def wait(timeout: float) -> int:
                if timeout <= 0:
                    raise AssertionError("wait must be bounded")
                killed.set()
                return -9

        class ProcessOwner:
            @staticmethod
            def assign(_process: object) -> bool:
                return True

            @staticmethod
            def terminate_tree(process: object) -> None:
                process.kill()

            @staticmethod
            def close() -> None:
                return None

        transport = loop.FixedSubprocessRunnerTransport(self.runner_capability)
        clean_cleanup = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": plan.run_id,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        with patch.object(loop.subprocess, "Popen", return_value=Process()), patch.object(
            loop,
            "_RunnerProcessTreeOwner",
            return_value=ProcessOwner(),
        ), patch.object(
            loop,
            "_cleanup_runner_resources",
            return_value=clean_cleanup,
        ) as cleanup_runner:
            with self.assertRaises(loop.RunnerTransportError) as raised:
                transport(plan)
        self.assertEqual(raised.exception.code, "runner_output_too_large")
        self.assertEqual(raised.exception.cleanup["status"], "clean")
        cleanup_runner.assert_called_once_with(plan)
        self.assertTrue(killed.is_set())

    def test_transport_cleanup_receipt_is_preserved_on_failure(self) -> None:
        def cancelled_runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            raise loop.RunnerTransportError(
                "runner_cancelled",
                {
                    "schema": lab.CLEANUP_SCHEMA,
                    "runId": plan.run_id,
                    "owner": lab.LAB_OWNER,
                    "status": "cleanup_required",
                    "remainingProcesses": 1,
                    "remainingGpuAllocations": 1,
                    "remainingArtifacts": 1,
                },
            )

        result = loop.run_optimizer_loop(
            baseline_config=self.baseline(),
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
            proposer=lambda _context: self._proposal(128),
            runner=cancelled_runner,
        )

        self.assertEqual(result.stop_reason, "runner_cancelled")
        self.assertEqual(result.state, optimizer.LatencyState.CLEANUP_REQUIRED)
        self.assertEqual(
            result.to_dict()["transportCleanup"]["status"],
            "cleanup_required",
        )

    def test_transport_preserves_decoded_receipt_across_dirty_post_cleanup(self) -> None:
        candidate = optimizer.compile_candidate(
            optimizer.candidate_proposal(
                self.root.pinned_identities,
                self.baseline(),
                self._proposal(128),
            ),
            trust_root=self.root,
        )
        plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.root,
        )

        class Input:
            def write(self, value: bytes) -> int:
                return len(value)

            def close(self) -> None:
                return None

        class Output:
            def __init__(self) -> None:
                self.done = False

            def read(self, _size: int) -> bytes:
                if self.done:
                    return b""
                self.done = True
                return b"result"

            def close(self) -> None:
                return None

        class Process:
            stdin = Input()
            stdout = Output()
            returncode = 0

            def poll(self) -> int:
                return 0

        class ProcessOwner:
            def assign(self, _process: object) -> bool:
                return True

            def terminate_tree(self, _process: object) -> None:
                return None

            def close(self) -> None:
                return None

        dirty = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": plan.run_id,
            "owner": lab.LAB_OWNER,
            "status": "cleanup_required",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 1,
            "remainingArtifacts": 0,
        }
        decoded = {"schema": lab.RUNNER_RECEIPT_SCHEMA, "status": "completed"}
        transport = loop.FixedSubprocessRunnerTransport(self.runner_capability)
        with (
            patch.object(loop.subprocess, "Popen", return_value=Process()),
            patch.object(loop, "_RunnerProcessTreeOwner", return_value=ProcessOwner()),
            patch.object(loop, "_cleanup_runner_resources", return_value=dirty),
            patch.object(loop, "_decode_external_runner_output", return_value=decoded),
        ):
            with self.assertRaises(loop.RunnerTransportError) as raised:
                transport(plan)
        self.assertEqual(raised.exception.cleanup["status"], "cleanup_required")
        self.assertEqual(dict(raised.exception.partial_receipt), decoded)

    def test_runner_transport_reserves_preflight_and_cleanup_budget(self) -> None:
        self.assertGreaterEqual(loop.RUNNER_TRANSPORT_OVERHEAD_SEC, 900.0)

    def test_runner_environment_only_forwards_fixed_nonsecret_paths(self) -> None:
        configured = {
            "SYSTEMROOT": r"C:\Windows",
            "USERPROFILE": r"C:\Users\tester",
            "EVELYN_LLAMA_CPP_DIR": r"D:\models\llama.cpp",
            "EVELYN_MAIN_LLM_BUILD_DIR": r"D:\models\llama.cpp\build-sm120-v1",
            "EVELYN_OMNIVOICE_SERVER_DIR": r"D:\models\omnivoice",
            "EVELYN_OMNIVOICE_PROFILES_DIR": r"D:\profiles",
            "PATH": r"C:\untrusted",
            "HTTPS_PROXY": "http://proxy.invalid",
            "API_TOKEN": "secret",
            "DOCKER_HOST": "tcp://remote.invalid:2375",
        }
        with patch.dict(loop.os.environ, configured, clear=True):
            forwarded = loop._minimal_runner_env()
        self.assertEqual(forwarded["USERPROFILE"], configured["USERPROFILE"])
        self.assertEqual(
            forwarded["EVELYN_LLAMA_CPP_DIR"],
            configured["EVELYN_LLAMA_CPP_DIR"],
        )
        self.assertEqual(
            forwarded["EVELYN_MAIN_LLM_BUILD_DIR"],
            configured["EVELYN_MAIN_LLM_BUILD_DIR"],
        )
        for forbidden in ("PATH", "HTTPS_PROXY", "API_TOKEN", "DOCKER_HOST"):
            self.assertNotIn(forbidden, forwarded)

    def test_fixed_source_wiring_runs_llm_to_external_runner_and_fails_closed(self) -> None:
        class Response:
            status = 200

            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            @staticmethod
            def geturl() -> str:
                return loop.LOCAL_PROPOSER_URL

            @staticmethod
            def read(_limit: int) -> bytes:
                return b'{"choices":[{"message":{"content":"{\\"main.cacheReuse\\":128}"}}]}'

        class Opener:
            @staticmethod
            def open(_request: object, *, timeout: float) -> Response:
                if timeout <= 0:
                    raise AssertionError("timeout must be bounded")
                return Response()

        def unavailable_runner(plan: lab.RunnerPlan, _cancel: object) -> dict:
            return lab.issue_runner_receipt(
                plan,
                fixed_lab._failure_receipt(
                    plan,
                    status="lab_isolation_preflight_failed",
                    cleanup={
                        "schema": lab.CLEANUP_SCHEMA,
                        "runId": plan.run_id,
                        "owner": lab.LAB_OWNER,
                        "status": "clean",
                        "remainingProcesses": 0,
                        "remainingGpuAllocations": 0,
                        "remainingArtifacts": 0,
                    },
                ),
                trust_root=self.root,
                runner_capability=self.runner_capability,
            ).to_dict()

        with patch.object(loop.urllib_request, "build_opener", return_value=Opener()), patch.object(
            loop.FixedSubprocessRunnerTransport,
            "__call__",
            side_effect=unavailable_runner,
        ):
            result = loop.run_fixed_local_optimizer_loop(
                baseline_config=self.baseline(),
                trust_root=self.root,
                runner_capability=self.runner_capability,
                evaluator_capability=self.evaluator_capability,
                proposer_timeout_s=2,
            )
        self.assertEqual(result.state, optimizer.LatencyState.FAILED)
        self.assertEqual(result.stop_reason, "evaluation_inconclusive")
        self.assertEqual(result.feedback[-1].code, "lab_isolation_preflight_failed")

    def test_owned_lab_entrypoint_discovers_and_bootstraps_ephemeral_authority(self) -> None:
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        coordinator = optimizer.bootstrap_ephemeral_fixed_coordinator(identities)
        sentinel = loop.LoopResult(
            state=optimizer.LatencyState.IDLE,
            stop_reason="proposer_stopped",
            attempted_candidate_ids=(),
            feedback=(),
            fallback_count=0,
        )
        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": fixed_lab.GLOBAL_RECONCILE_RUN_ID,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        host_plan = object()
        host_receipt = object()

        def run_with_host_proof(**kwargs: object) -> loop.LoopResult:
            provider = kwargs["host_restoration_prover"]
            self.assertTrue(callable(provider))
            provider(host_plan, host_receipt)
            return sentinel

        with patch.object(
            fixed_lab,
            "discover_owned_lab_identities",
            return_value=identities,
        ) as discover, patch.object(
            fixed_lab,
            "reconcile_owned_lab",
            side_effect=(clean, clean),
        ) as reconcile, patch.object(
            loop,
            "bootstrap_ephemeral_fixed_coordinator",
            return_value=coordinator,
        ) as bootstrap, patch.object(
            loop,
            "run_fixed_local_optimizer_loop",
            side_effect=run_with_host_proof,
        ) as run, patch.object(
            loop,
            "issue_host_restoration_proof",
            return_value=object(),
        ) as issue:
            lifecycle_calls: list[str] = []
            lifecycle = self._host_lifecycle(lifecycle_calls)
            result = loop.run_owned_lab_optimizer_loop(
                baseline_config=self.baseline(),
                proposer_timeout_s=2,
                host_lifecycle=lifecycle,
            )

        self.assertIs(result, sentinel)
        self.assertEqual(reconcile.call_count, 2)
        discover.assert_called_once_with(self.baseline())
        bootstrap.assert_called_once_with(identities)
        run.assert_called_once()
        run_kwargs = run.call_args.kwargs
        self.assertEqual(run_kwargs["baseline_config"], self.baseline())
        self.assertIs(run_kwargs["trust_root"], coordinator[0])
        self.assertIs(run_kwargs["runner_capability"], coordinator[1])
        self.assertIs(run_kwargs["evaluator_capability"], coordinator[2])
        self.assertTrue(callable(run_kwargs["host_restoration_prover"]))
        self.assertIsNone(run_kwargs["cancel_event"])
        self.assertEqual(run_kwargs["proposer_timeout_s"], 2)
        self.assertEqual(
            lifecycle_calls,
            ["prepare", "preflight", "finish", "restore"],
        )
        issue.assert_called_once()
        self.assertIs(issue.call_args.args[0], host_plan)
        self.assertIs(issue.call_args.args[1], host_receipt)

    def test_legacy_and_operator_cli_outputs_are_separate_single_json_schemas(self) -> None:
        sentinel = loop.LoopResult(
            state=optimizer.LatencyState.IDLE,
            stop_reason="proposer_stopped",
            attempted_candidate_ids=(),
            feedback=(),
            fallback_count=0,
        )
        outputs: list[tuple[int, list[str], dict]] = []
        for argument in ("--run-owned-lab", "--run-owned-lab-report"):
            stdout = io.StringIO()
            with patch.object(
                loop, "run_owned_lab_optimizer_loop", return_value=sentinel
            ), patch.object(sys, "argv", ["main_latency_optimizer_loop.py", argument]), redirect_stdout(stdout):
                status = loop.main()
            lines = stdout.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            outputs.append((status, lines, json.loads(lines[0])))

        self.assertEqual(outputs[0][0], outputs[1][0])
        self.assertEqual(outputs[0][2], sentinel.to_dict())
        self.assertEqual(outputs[0][2]["schema"], loop.LOOP_RESULT_SCHEMA)
        self.assertEqual(outputs[1][2], sentinel.operator_report_dict())
        self.assertEqual(outputs[1][2]["schema"], loop.OPERATOR_REPORT_SCHEMA)

    def test_campaign_lock_has_windows_and_posix_nonblocking_backends(self) -> None:
        posix_calls: list[tuple[int, int]] = []
        fake_fcntl = types.SimpleNamespace(
            LOCK_EX=1,
            LOCK_NB=2,
            LOCK_UN=4,
            flock=lambda descriptor, operation: posix_calls.append(
                (descriptor, operation)
            ),
        )
        windows_calls: list[tuple[int, int, int]] = []
        fake_msvcrt = types.SimpleNamespace(
            LK_NBLCK=10,
            LK_UNLCK=11,
            locking=lambda descriptor, operation, count: windows_calls.append(
                (descriptor, operation, count)
            ),
        )
        with patch.object(loop.os, "lseek", return_value=0), patch.dict(
            sys.modules,
            {"fcntl": fake_fcntl, "msvcrt": fake_msvcrt},
        ):
            loop._lock_campaign_file(7, "posix")
            loop._unlock_campaign_file(7, "posix")
            loop._lock_campaign_file(8, "nt")
            loop._unlock_campaign_file(8, "nt")
        self.assertEqual(posix_calls, [(7, 3), (7, 4)])
        self.assertEqual(windows_calls, [(8, 10, 1), (8, 11, 1)])

    def test_campaign_lock_excludes_a_second_process_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            loop,
            "OWNED_LAB_CAMPAIGN_LOCK",
            Path(directory) / "campaign.lock",
        ):
            with loop._OwnedLabCampaignLock():
                with self.assertRaisesRegex(RuntimeError, "campaign_locked"):
                    with loop._OwnedLabCampaignLock():
                        self.fail("second campaign lock must not be acquired")
            with loop._OwnedLabCampaignLock():
                pass

    def test_owned_lab_startup_and_terminal_reconciliation_fail_closed(self) -> None:
        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": fixed_lab.GLOBAL_RECONCILE_RUN_ID,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        dirty = dict(clean)
        dirty.update(status="cleanup_required", remainingArtifacts=1)
        with patch.object(
            fixed_lab,
            "reconcile_owned_lab",
            side_effect=(dirty, dirty),
        ), patch.object(
            fixed_lab,
            "discover_owned_lab_identities",
        ) as discover:
            with self.assertRaisesRegex(RuntimeError, "cleanup_required"):
                loop.run_owned_lab_optimizer_loop(
                    baseline_config=self.baseline(),
                    host_lifecycle=self._host_lifecycle(),
                )
        discover.assert_not_called()

        base_result = loop.LoopResult(
            state=optimizer.LatencyState.IDLE,
            stop_reason="proposer_stopped",
            attempted_candidate_ids=(),
            feedback=(),
            fallback_count=0,
        )
        identities = optimizer.IdentitySet.from_mapping(self.identities())
        coordinator = optimizer.bootstrap_ephemeral_fixed_coordinator(identities)
        with patch.object(
            fixed_lab,
            "reconcile_owned_lab",
            side_effect=(clean, dirty),
        ), patch.object(
            fixed_lab,
            "discover_owned_lab_identities",
            return_value=identities,
        ), patch.object(
            loop,
            "bootstrap_ephemeral_fixed_coordinator",
            return_value=coordinator,
        ), patch.object(
            loop,
            "run_fixed_local_optimizer_loop",
            return_value=base_result,
        ):
            result = loop.run_owned_lab_optimizer_loop(
                baseline_config=self.baseline(),
                host_lifecycle=self._host_lifecycle(),
            )
        self.assertEqual(result.state, optimizer.LatencyState.CLEANUP_REQUIRED)
        self.assertEqual(result.transport_cleanup["remainingArtifacts"], 1)

    def test_fixed_lab_adapter_has_exact_content_free_preflight_failures(self) -> None:
        candidate = optimizer.compile_candidate(
            optimizer.candidate_proposal(
                self.root.pinned_identities,
                self.baseline(),
                self._proposal(128),
            ),
            trust_root=self.root,
        )
        plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.root,
        )
        adapter = fixed_lab.OwnedDockerLabAdapter(
            lambda mode, _plan: (
                {"ready": False, "code": "lab_identity_preflight_failed"}
                if mode == "preflight"
                else (_ for _ in ()).throw(RuntimeError("unavailable"))
            )
        )
        preflight = adapter.preflight(plan)
        self.assertEqual(
            preflight.to_dict(),
            {
                "schema": fixed_lab.LAB_PREFLIGHT_SCHEMA,
                "adapterContract": fixed_lab.LAB_ADAPTER_CONTRACT_ID,
                "ready": False,
                "code": "lab_identity_preflight_failed",
            },
        )
        self.assertEqual(adapter.run(plan)["cleanup"]["status"], "cleanup_required")
        for code in lab.LAB_PREFLIGHT_FAILURE_CODES:
            with self.subTest(code=code):
                payload = self._unsigned_receipt(plan, status=code)
                receipt = lab.issue_runner_receipt(
                    plan,
                    payload,
                    trust_root=self.root,
                    runner_capability=self.runner_capability,
                )
                decision = lab.evaluate_runner_receipt(
                    plan,
                    receipt,
                    trust_root=self.root,
                    evaluator_capability=self.evaluator_capability,
                )
                self.assertEqual(decision.verdict, "inconclusive")
                self.assertEqual(decision.code, code)
                self.assertEqual(decision.gate, "reliability")
        with self.assertRaisesRegex(ValueError, "lab_preflight_invalid"):
            fixed_lab.LabPreflight(True, "lab_adapter_not_installed")


if __name__ == "__main__":
    unittest.main()
