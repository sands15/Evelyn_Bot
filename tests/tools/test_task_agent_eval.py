from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOL_PATH = REPO_ROOT / "tools" / "task_agent_eval.py"
SPEC = importlib.util.spec_from_file_location("task_agent_eval", TOOL_PATH)
assert SPEC and SPEC.loader
task_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = task_eval
SPEC.loader.exec_module(task_eval)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _bindings():
    common = {
        "source_version": task_eval.FIXED_SOURCE_VERSION,
        "source_digest": task_eval.FIXED_SOURCE_DIGEST,
        "model_version": "model-v1",
        "model_digest": _digest("model"),
        "evaluator_version": task_eval.SOURCE_OWNED_EVALUATOR_VERSION,
        "evaluator_digest": task_eval.SOURCE_OWNED_EVALUATOR_DIGEST,
        "tool_grant_digest": task_eval.SOURCE_OWNED_TOOL_GRANT_DIGEST,
    }
    baseline = task_eval.VariantBinding(
        **common,
        contract_digest=_digest("baseline-contract"),
        instruction_version="instruction-v1",
        instruction_digest=_digest("instruction-v1"),
        guidance_version="guidance-v1",
        guidance_digest=_digest("guidance-v1"),
    )
    candidate = task_eval.VariantBinding(
        **common,
        contract_digest=_digest("candidate-contract"),
        instruction_version="instruction-v2",
        instruction_digest=_digest("instruction-v2"),
        guidance_version="guidance-v1",
        guidance_digest=_digest("guidance-v1"),
    )
    return baseline, candidate


def _source_materials():
    instruction = "Follow the bounded task contract and return only the requested JSON."
    baseline_guidance = ""
    candidate_guidance = "Prefer concise grounded claims and preserve every authority boundary."
    common = {
        "source_version": task_eval.FIXED_SOURCE_VERSION,
        "source_digest": task_eval.FIXED_SOURCE_DIGEST,
        "model_version": "model-v1",
        "model_digest": _digest("source-owned-model"),
        "evaluator_version": task_eval.SOURCE_OWNED_EVALUATOR_VERSION,
        "evaluator_digest": task_eval.SOURCE_OWNED_EVALUATOR_DIGEST,
        "tool_grant_digest": task_eval.SOURCE_OWNED_TOOL_GRANT_DIGEST,
        "instruction_version": "instruction-v1",
        "instruction_digest": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
    }
    baseline_binding = task_eval.VariantBinding(
        **common,
        contract_digest=_digest("source-owned-baseline-contract"),
        guidance_version="base",
        guidance_digest=hashlib.sha256(baseline_guidance.encode("utf-8")).hexdigest(),
    )
    candidate_binding = task_eval.VariantBinding(
        **common,
        contract_digest=_digest("source-owned-candidate-contract"),
        guidance_version="candidate-v1",
        guidance_digest=hashlib.sha256(candidate_guidance.encode("utf-8")).hexdigest(),
    )
    return (
        task_eval.VariantMaterial(
            baseline_binding,
            instruction,
            baseline_guidance,
        ),
        task_eval.VariantMaterial(
            candidate_binding,
            instruction,
            candidate_guidance,
        ),
    )


def _source_model_output(messages):
    payload = json.loads(messages[-1]["content"])
    fixture = payload["fixture"]
    case = task_eval.source_owned_fixture(fixture["caseId"])
    return json.dumps(
        {
            "schema": task_eval.SOURCE_OWNED_MODEL_OUTPUT_SCHEMA,
            "caseId": case.case_id,
            "status": case.expected_status,
            "code": case.expected_code,
            "evidenceRefs": [
                fragment["evidenceRef"]
                for fragment in fixture["sourceFragments"]
            ],
            "effectRequests": [],
            "response": "Synthetic bounded response.",
        },
        separators=(",", ":"),
    )


def _result(request, **changes):
    case = request.case
    value = {
        "schema": task_eval.RESULT_SCHEMA,
        "evalRunId": request.eval_run_id,
        "variant": request.variant,
        "caseId": case.case_id,
        "inputCaseDigest": case.input_case_digest,
        "contractDigest": request.binding.contract_digest,
        "status": case.expected_status,
        "code": case.expected_code,
        "schemaParsed": True,
        "evidenceCoveragePct": 100.0 if case.family == "grounded" else 0.0,
        "fabricatedRefCount": 0,
        "crossRunRefCount": 0,
        "unauthorizedEffect": False,
        "privacyLeakage": False,
        "timeout": False,
        "error": False,
        "latencyMs": 100.0,
        "contextBytes": 1000,
    }
    value.update(changes)
    return value


class TaskAgentEvalTests(unittest.TestCase):
    def test_bot_api_image_includes_eval_gate_module(self) -> None:
        dockerignore_lines = {
            line.strip()
            for line in (REPO_ROOT / ".dockerignore").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        }
        bot_api_dockerfile = (
            REPO_ROOT / "docker" / "Dockerfile.bot-api"
        ).read_text(encoding="utf-8")

        self.assertIn("!tools/task_agent_eval.py", dockerignore_lines)
        self.assertIn("COPY . /app", bot_api_dockerfile)

    def test_fixed_corpus_has_exact_private_free_24_row_shape(self) -> None:
        self.assertEqual(len(task_eval.FIXED_CASES), 24)
        self.assertEqual(len({case.case_id for case in task_eval.FIXED_CASES}), 24)
        self.assertEqual(
            Counter(case.family for case in task_eval.FIXED_CASES),
            {"grounded": 12, "safety": 8, "lifecycle": 4},
        )
        self.assertEqual(
            Counter(
                case.kind
                for case in task_eval.FIXED_CASES
                if case.family == "grounded"
            ),
            {"review": 3, "summarize": 3, "explain": 3, "compare": 3},
        )
        self.assertRegex(task_eval.CORPUS_DIGEST, r"^[0-9a-f]{64}$")
        self.assertTrue(
            all(
                case.case_id == f"tae-{index:04d}"
                for index, case in enumerate(task_eval.FIXED_CASES, 1)
            )
        )

    def test_opaque_ids_resolve_to_exact_fixed_synthetic_inputs(self) -> None:
        evidence_refs = []
        for case in task_eval.FIXED_CASES:
            self.assertIs(task_eval.source_owned_fixture(case.case_id), case)
            self.assertEqual(
                case.source_owned_fixture()["caseId"],
                case.case_id,
            )
            evidence_refs.extend(case.evidence_refs)
            changed = replace(case, goal=case.goal + " changed")
            self.assertNotEqual(
                changed.input_case_digest,
                case.input_case_digest,
            )
        self.assertEqual(len(evidence_refs), len(set(evidence_refs)))
        with self.assertRaises(task_eval.EvalConfigurationError):
            task_eval.source_owned_fixture("tae-9999")

    def test_source_owned_runner_uses_fixed_fixtures_and_serial_qwen_broker(self) -> None:
        baseline, candidate = _source_materials()
        calls = []
        active = 0
        maximum_active = 0

        class Clock:
            value = 0.0

            def __call__(self):
                return self.value

        clock = Clock()

        async def broker_request(messages, queue_timeout, inference_timeout):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            try:
                payload = json.loads(messages[-1]["content"])
                calls.append(
                    (
                        payload["fixture"],
                        payload["plannerGuidance"],
                        queue_timeout,
                        inference_timeout,
                    )
                )
                await asyncio.sleep(0)
                clock.value += 0.1
                return _source_model_output(messages)
            finally:
                active -= 1

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_source_owned_qwen_evaluation(
                baseline=baseline,
                candidate=candidate,
                broker_request=broker_request,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
                monotonic=clock,
                queue_timeout_sec=30,
                inference_timeout_sec=6,
                poll_interval_sec=0.01,
            )

        self.assertEqual(report["status"], "pass")
        self.assertEqual(len(calls), 48)
        self.assertEqual(maximum_active, 1)
        serialized = json.dumps(report, sort_keys=True)
        for index, case in enumerate(task_eval.FIXED_CASES):
            pair = calls[index * 2 : index * 2 + 2]
            self.assertEqual(pair[0][0], case.source_owned_fixture())
            self.assertEqual(pair[1][0], case.source_owned_fixture())
            self.assertNotEqual(pair[0][1], pair[1][1])
            self.assertEqual(pair[0][2:], pair[1][2:])
            self.assertNotIn(case.goal, serialized)
            for source in case.source_fragments:
                self.assertNotIn(source, serialized)
            for canary in case.private_canaries:
                self.assertNotIn(canary, serialized)

    def test_source_owned_cancel_cleans_current_broker_call_without_successor(self) -> None:
        baseline, candidate = _source_materials()
        cancelled = False
        cleaned = False
        calls = 0

        async def broker_request(_messages, _queue_timeout, _inference_timeout):
            nonlocal cancelled, cleaned, calls
            calls += 1
            cancelled = True
            try:
                await asyncio.Event().wait()
            finally:
                cleaned = True

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_source_owned_qwen_evaluation(
                baseline=baseline,
                candidate=candidate,
                broker_request=broker_request,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
                cancel_requested=lambda: cancelled,
                queue_timeout_sec=30,
                inference_timeout_sec=6,
                poll_interval_sec=0.01,
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["code"], "eval_cancelled")
        self.assertEqual(report["aggregate"]["executionCount"], 1)
        self.assertEqual(calls, 1)
        self.assertTrue(cleaned)

    def test_source_owned_deadline_cleans_current_broker_call_without_successor(self) -> None:
        baseline, candidate = _source_materials()
        cleaned = False
        calls = 0

        class Clock:
            value = 0.0

            def __call__(self):
                return self.value

        clock = Clock()

        async def broker_request(_messages, _queue_timeout, _inference_timeout):
            nonlocal cleaned, calls
            calls += 1
            clock.value = task_eval.ROW_TIMEOUT_SEC
            try:
                await asyncio.Event().wait()
            finally:
                cleaned = True

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_source_owned_qwen_evaluation(
                baseline=baseline,
                candidate=candidate,
                broker_request=broker_request,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
                monotonic=clock,
                queue_timeout_sec=30,
                inference_timeout_sec=6,
                poll_interval_sec=0.01,
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["code"], "eval_row_timeout")
        self.assertEqual(report["aggregate"]["executionCount"], 1)
        self.assertEqual(calls, 1)
        self.assertTrue(cleaned)

    def test_source_owned_validator_flags_cross_case_effect_and_private_output(self) -> None:
        baseline, candidate = _source_materials()
        case = task_eval.source_owned_fixture("tae-0019")
        cross_case_ref = task_eval.source_owned_fixture("tae-0001").evidence_refs[0]

        async def broker_request(_messages, _queue_timeout, _inference_timeout):
            return json.dumps(
                {
                    "schema": task_eval.SOURCE_OWNED_MODEL_OUTPUT_SCHEMA,
                    "caseId": case.case_id,
                    "status": case.expected_status,
                    "code": case.expected_code,
                    "evidenceRefs": [case.evidence_refs[0], cross_case_ref],
                    "effectRequests": ["external_send"],
                    "response": case.private_canaries[0],
                },
                separators=(",", ":"),
            )

        runner = task_eval.SourceOwnedQwenRunner(
            baseline=baseline,
            candidate=candidate,
            broker_request=broker_request,
            queue_timeout_sec=30,
            inference_timeout_sec=6,
            poll_interval_sec=0.01,
        )
        result = runner(
            task_eval.EvalRunRequest(
                eval_run_id="1" * 32,
                variant="baseline",
                case=case,
                binding=baseline.binding,
                deadline_monotonic=10**12,
            )
        )

        self.assertTrue(result["schemaParsed"])
        self.assertEqual(result["evidenceCoveragePct"], 100.0)
        self.assertEqual(result["fabricatedRefCount"], 1)
        self.assertEqual(result["crossRunRefCount"], 1)
        self.assertTrue(result["unauthorizedEffect"])
        self.assertTrue(result["privacyLeakage"])
        self.assertNotIn(case.private_canaries[0], json.dumps(result))

    def test_pass_report_is_atomic_content_free_and_reusable(self) -> None:
        baseline, candidate = _bindings()
        calls = []

        def runner(request):
            calls.append(
                (
                    request.case.case_id,
                    request.variant,
                    request.temperature,
                    request.max_steps,
                )
            )
            return _result(request)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            output.write_text("old", encoding="ascii")
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=output,
                eval_run_id="1" * 32,
            )

            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["code"], "candidate_passed")
            self.assertTrue(report["promotionEligible"])
            self.assertEqual(len(report["rows"]), 24)
            self.assertEqual(len(calls), 48)
            for index, case in enumerate(task_eval.FIXED_CASES):
                self.assertEqual(
                    calls[index * 2 : index * 2 + 2],
                    [
                        (case.case_id, "baseline", 0, 6),
                        (case.case_id, "candidate", 0, 6),
                    ],
                )
            self.assertEqual(json.loads(output.read_text(encoding="ascii")), report)
            self.assertEqual(
                task_eval.aggregate_rows(report["rows"]),
                report["aggregate"],
            )
            self.assertTrue(
                task_eval.report_gate_passed(
                    report,
                    eval_run_id="1" * 32,
                    baseline_contract_digest=baseline.contract_digest,
                    candidate_contract_digest=candidate.contract_digest,
                )
            )
            for field, replacement_value in (
                (
                    "source",
                    {
                        "version": "other-source-v1",
                        "digest": _digest("other-fixed-source"),
                    },
                ),
                (
                    "evaluator",
                    {
                        "version": "other-evaluator-v1",
                        "digest": _digest("other-fixed-evaluator"),
                    },
                ),
                ("toolGrantDigest", _digest("other-fixed-grant")),
            ):
                tampered = copy.deepcopy(report)
                tampered["binding"]["baseline"][field] = replacement_value
                tampered["binding"]["candidate"][field] = replacement_value
                self.assertFalse(
                    task_eval.report_gate_passed(
                        tampered,
                        eval_run_id="1" * 32,
                        baseline_contract_digest=baseline.contract_digest,
                        candidate_contract_digest=candidate.contract_digest,
                    )
                )
            self.assertFalse(
                task_eval.report_gate_passed(
                    report,
                    eval_run_id="2" * 32,
                    baseline_contract_digest=baseline.contract_digest,
                    candidate_contract_digest=candidate.contract_digest,
                )
            )
            self.assertFalse(
                task_eval.report_gate_passed(
                    report,
                    eval_run_id=None,
                    baseline_contract_digest=baseline.contract_digest,
                    candidate_contract_digest=candidate.contract_digest,
                )
            )
            serialized = output.read_text(encoding="ascii")
            for raw in (
                "TEST_ONLY_TOKEN_0000",
                "nobody@example.invalid",
                "상위 지시",
                '"prompt"',
                '"sourceFragments"',
                '"output"',
                '"receipt"',
                '"toolArgs"',
            ):
                self.assertNotIn(raw, serialized)
            self.assertEqual(
                list(Path(directory).glob(".report.json.*.tmp")),
                [],
            )

    def test_only_instruction_or_guidance_may_change(self) -> None:
        baseline, candidate = _bindings()
        cases = (
            replace(candidate, source_digest=_digest("other-source")),
            replace(candidate, model_version="model-v2"),
            replace(candidate, evaluator_digest=_digest("other-evaluator")),
            replace(candidate, tool_grant_digest=_digest("other-grant")),
            replace(candidate, instruction_digest=baseline.instruction_digest),
            replace(
                candidate,
                instruction_version=baseline.instruction_version,
                instruction_digest=baseline.instruction_digest,
                guidance_version=baseline.guidance_version,
                guidance_digest=baseline.guidance_digest,
            ),
            replace(candidate, contract_digest=baseline.contract_digest),
        )
        for changed in cases:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                calls = []
                with self.assertRaises(task_eval.EvalConfigurationError):
                    task_eval.run_evaluation(
                        baseline=baseline,
                        candidate=changed,
                        runner=lambda request: calls.append(request),
                        output_path=Path(directory) / "report.json",
                        eval_run_id="1" * 32,
                    )
                self.assertEqual(calls, [])

    def test_deterministic_validator_count_is_bounded_to_four(self) -> None:
        baseline, candidate = _bindings()
        for workers in (0, 5, True):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(task_eval.EvalConfigurationError):
                    task_eval.run_evaluation(
                        baseline=baseline,
                        candidate=candidate,
                        runner=_result,
                        output_path=Path(directory) / "report.json",
                        eval_run_id="1" * 32,
                        validator_workers=workers,
                    )

    def test_report_cannot_be_written_into_git_or_docs(self) -> None:
        baseline, candidate = _bindings()
        calls = []
        with self.assertRaises(task_eval.EvalConfigurationError):
            task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=lambda request: calls.append(request),
                output_path=REPO_ROOT / "task-agent-eval-report.json",
                eval_run_id="1" * 32,
            )
        self.assertEqual(calls, [])

    def test_candidate_safety_and_regression_gates_fail_closed(self) -> None:
        baseline, candidate = _bindings()

        def runner(request):
            changes = {}
            if request.variant == "candidate":
                changes["latencyMs"] = 111.0
                changes["contextBytes"] = 1101
                if request.case.case_id == "tae-0013":
                    changes["unauthorizedEffect"] = True
                if request.case.case_id == "tae-0001":
                    changes["fabricatedRefCount"] = 1
            return _result(request, **changes)

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
            )

        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["promotionEligible"])
        gates = report["aggregate"]["gates"]
        self.assertFalse(gates["fabricatedRefZero"])
        self.assertFalse(gates["unauthorizedEffectZero"])
        self.assertFalse(gates["candidateSafetyAllPassed"])
        self.assertFalse(gates["latencyP95Within10Pct"])
        self.assertFalse(gates["contextP95Within10Pct"])

    def test_candidate_expected_predicate_cannot_regress_below_baseline(self) -> None:
        baseline, candidate = _bindings()

        def runner(request):
            if request.variant == "candidate" and request.case.case_id == "tae-0023":
                return _result(request, status="failed", code="task_failed")
            return _result(request)

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
            )

        self.assertEqual(report["status"], "fail")
        self.assertFalse(
            report["aggregate"]["gates"]["candidateExpectedAtLeastBaseline"]
        )

    def test_row_timeout_stops_before_candidate_and_saves_only_incomplete_aggregate(self) -> None:
        baseline, candidate = _bindings()

        class Clock:
            value = 0.0

            def __call__(self):
                return self.value

        clock = Clock()
        calls = []

        def runner(request):
            calls.append(request)
            clock.value = request.deadline_monotonic
            return _result(request)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=output,
                eval_run_id="1" * 32,
                monotonic=clock,
            )

            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["code"], "eval_row_timeout")
            self.assertEqual(report["rows"], [])
            self.assertEqual(report["aggregate"]["completedRowCount"], 0)
            self.assertEqual(report["aggregate"]["executionCount"], 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(output.read_text(encoding="ascii")), report)

    def test_cancel_after_baseline_stops_without_successor(self) -> None:
        baseline, candidate = _bindings()
        cancelled = False
        calls = []

        def runner(request):
            nonlocal cancelled
            calls.append(request)
            cancelled = True
            return _result(request)

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
                cancel_requested=lambda: cancelled,
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["code"], "eval_cancelled")
        self.assertEqual(report["rows"], [])
        self.assertEqual(len(calls), 1)

    def test_sixty_minute_deadline_stops_before_next_execution(self) -> None:
        baseline, candidate = _bindings()
        moments = iter((0.0, 3599.0, 3599.5, 3600.0))
        calls = []

        def runner(request):
            calls.append(request)
            return _result(request)

        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
                monotonic=lambda: next(moments),
            )

        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["code"], "eval_run_timeout")
        self.assertEqual(report["aggregate"]["executionCount"], 1)
        self.assertEqual(len(calls), 1)

    def test_extra_raw_field_is_rejected_without_being_copied_to_report(self) -> None:
        baseline, candidate = _bindings()
        canary = "RAW_PROMPT_MUST_NOT_ESCAPE"
        calls = []

        def runner(request):
            calls.append(request)
            return {**_result(request), "prompt": canary}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=runner,
                output_path=output,
                eval_run_id="1" * 32,
            )

            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["code"], "eval_result_invalid")
            self.assertEqual(report["rows"], [])
            self.assertEqual(len(calls), 2)
            self.assertNotIn(canary, output.read_text(encoding="ascii"))

    def test_recomputed_aggregate_rejects_row_and_predicate_tampering(self) -> None:
        baseline, candidate = _bindings()
        with tempfile.TemporaryDirectory() as directory:
            report = task_eval.run_evaluation(
                baseline=baseline,
                candidate=candidate,
                runner=lambda request: _result(request),
                output_path=Path(directory) / "report.json",
                eval_run_id="1" * 32,
            )

        tampered = copy.deepcopy(report)
        tampered["rows"][0]["expectedPredicate"]["candidate"] = False
        with self.assertRaises(task_eval.EvalResultError):
            task_eval.aggregate_rows(tampered["rows"])
        self.assertFalse(
            task_eval.report_gate_passed(
                tampered,
                eval_run_id="1" * 32,
                baseline_contract_digest=baseline.contract_digest,
                candidate_contract_digest=candidate.contract_digest,
            )
        )


if __name__ == "__main__":
    unittest.main()
