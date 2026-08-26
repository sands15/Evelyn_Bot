from __future__ import annotations

import copy
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_external_runner as external_runner  # noqa: E402
import main_latency_fixed_lab_adapter as fixed_lab  # noqa: E402
import main_latency_lab_contract as lab  # noqa: E402
import main_latency_lab_harness as harness  # noqa: E402
import main_latency_owned_lab_worker as worker  # noqa: E402
import optimize_main_latency as optimizer  # noqa: E402
import post_stt_latency_benchmark as benchmark  # noqa: E402


class FixedMainLatencyLabAdapterTests(unittest.TestCase):
    def test_bot_image_context_includes_fixed_checks(self) -> None:
        dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("!tests/", dockerignore.splitlines())
        self.assertIn("!tests/**", dockerignore.splitlines())
        requirements = (
            REPO_ROOT / "docker" / "requirements.bot-api.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("discord.py>=2.7", requirements.splitlines())

    def setUp(self) -> None:
        identities = optimizer.IdentitySet(
            *(f"sha256:{index:064x}" for index in range(1, 7))
        )
        (
            self.root,
            self.runner_capability,
            self.evaluator_capability,
            _,
        ) = optimizer.bootstrap_ephemeral_fixed_coordinator(identities)
        baseline = optimizer.MainLatencyConfig(2048, 1024, 256, 8192, 1, 0)
        candidate = optimizer.compile_candidate(
            optimizer.candidate_proposal(
                identities,
                baseline,
                {"main.cacheReuse": 128},
            ),
            trust_root=self.root,
        )
        self.plan = lab.build_runner_plan(
            candidate,
            profile="screening",
            attempt=1,
            trust_root=self.root,
        )

    @staticmethod
    def _metrics(candidate: bool) -> dict[str, float]:
        shift = -100.0 if candidate else 0.0
        return {
            "postSttMainWriteP95Ms": 90.0,
            "rawFirstTokenP95Ms": 300.0,
            "rawToSafeSpeechP95Ms": 60.0,
            "safePrefixCommitP95Ms": 430.0,
            "ttsFirstPcmP95Ms": 200.0,
            "firstSentenceCommitP50Ms": 610.0 + shift,
            "firstSentenceCommitP95Ms": 720.0 + shift,
            "warmAnswerFirstPcmP50Ms": 650.0 + shift,
            "warmAnswerFirstPcmP95Ms": 800.0 + shift,
            "warmAnswerFirstPcmP99Ms": 950.0 + shift,
            "restartReadyAnswerFirstPcmP95Ms": 1050.0 + shift,
            "restartStartupToReadyP95Ms": 55_000.0 if candidate else 60_000.0,
            "gpuMinFreeMiB": 8192.0,
        }

    @staticmethod
    def _timing_diagnostics(plan: lab.RunnerPlan) -> dict:
        total = plan.spec.warm_per_condition
        activations = plan.spec.abba_blocks * 2

        def metrics(count: int, shift: float) -> dict:
            return {
                "promptEvalMs": {"sampleCount": count, "p50": 4.0 + shift, "p95": 6.0 + shift},
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

        def condition(shift: float) -> dict:
            return {
                "afterActivation": metrics(activations, shift),
                "resident": metrics(total, shift),
            }

        return {
            "schema": fixed_lab.PRIVATE_TIMING_SCHEMA,
            "baseline": condition(0.0),
            "candidate": condition(1.0),
        }

    def _receipt(self, plan: lab.RunnerPlan) -> dict:
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
            "baselineMetrics": self._metrics(False),
            "candidateMetrics": self._metrics(True),
            "statistics": {
                "schema": lab.STATISTICS_SCHEMA,
                "method": "paired-bootstrap-abba-v1",
                "bootstrapReplicates": 2000,
                "confidenceLevel": 0.95,
                "warmAnswerFirstPcmP95DeltaCiLowMs": -120.0,
                "warmAnswerFirstPcmP95DeltaCiHighMs": -80.0,
                "warmAnswerFirstPcmP95EffectSize": -0.8,
            },
            "checks": {key: 0 for key in lab.CHECK_FIELDS},
            "equivalence": {
                "comparisons": spec.warm_per_condition,
                "matches": spec.warm_per_condition,
            },
            "resources": {
                "runtimeMs": 1000,
                "artifactBytes": 0,
                "peakHostRamMiB": 1024,
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

    def test_installed_adapter_reaches_measurement_worker_and_external_signer(self) -> None:
        calls: list[str] = []

        def owned_worker(mode: str, plan: lab.RunnerPlan) -> dict:
            calls.append(mode)
            if mode == "preflight":
                return {"ready": True, "code": "ready"}
            if mode == "run":
                return {
                    "receipt": self._receipt(plan),
                    "timingDiagnostics": self._timing_diagnostics(plan),
                }
            if mode == "cleanup":
                return {"cleanup": self._receipt(plan)["cleanup"]}
            raise AssertionError("unexpected worker mode")

        installed = fixed_lab.OwnedDockerLabAdapter(owned_worker)
        one_run = self.runner_capability._issue_one_run(self.plan.run_id)
        request = {
            "schema": external_runner.REQUEST_SCHEMA,
            "plan": self.plan.to_dict(),
            "runnerCapability": one_run._export_once(),
        }
        with patch.object(fixed_lab, "FIXED_LAB_ADAPTER", installed):
            raw = external_runner._runner_receipt(copy.deepcopy(request))
        receipt = lab.compile_runner_receipt(self.plan, raw, trust_root=self.root)
        self.assertEqual(calls, ["preflight", "run", "cleanup"])
        self.assertNotIn("timingDiagnostics", raw)
        self.assertEqual(raw["schema"], lab.RUNNER_RECEIPT_SCHEMA)
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.cleanup.status, "clean")
        self.assertNotEqual(receipt.receipt_id, receipt.cleanup.proof_id)

    def test_external_signer_returns_only_allowlisted_invalid_measurement_code(self) -> None:
        calls: list[str] = []

        def owned_worker(mode: str, plan: lab.RunnerPlan) -> dict:
            calls.append(mode)
            payload = self._receipt(plan)
            if mode == "preflight":
                return {"ready": True, "code": "ready"}
            if mode == "run":
                payload["statistics"]["warmAnswerFirstPcmP95EffectSize"] = 0.8
                return {
                    "receipt": payload,
                    "timingDiagnostics": self._timing_diagnostics(plan),
                }
            if mode == "cleanup":
                return {"cleanup": payload["cleanup"]}
            raise AssertionError("unexpected worker mode")

        installed = fixed_lab.OwnedDockerLabAdapter(owned_worker)
        one_run = self.runner_capability._issue_one_run(self.plan.run_id)
        request = {
            "schema": external_runner.REQUEST_SCHEMA,
            "plan": self.plan.to_dict(),
            "runnerCapability": one_run._export_once(),
        }
        with patch.object(fixed_lab, "FIXED_LAB_ADAPTER", installed):
            with self.assertRaises(optimizer.ContractError) as raised:
                external_runner._runner_receipt(copy.deepcopy(request))
        self.assertEqual(calls, ["preflight", "run", "cleanup"])
        self.assertEqual(str(raised.exception), "runner_statistics_invalid")
        self.assertEqual(
            external_runner._diagnostic_response(raised.exception),
            {
                "schema": external_runner.DIAGNOSTIC_SCHEMA,
                "code": "runner_statistics_invalid",
            },
        )
        private = ValueError("private/path/content")
        rendered = external_runner._diagnostic_response(private)
        self.assertEqual(rendered["code"], "external_runner_contract_failed")
        self.assertNotIn(str(private), json.dumps(rendered))

    def test_private_timing_envelope_rejects_content_and_count_smuggling(self) -> None:
        valid = self._timing_diagnostics(self.plan)
        self.assertEqual(
            fixed_lab.normalize_private_timing_diagnostics(valid), valid
        )
        for mutate in (
            lambda value: value["candidate"]["resident"].update(
                {"promptText": "private prompt"}
            ),
            lambda value: value["candidate"]["resident"]["queueMs"].update(
                {"p95": "C:/private/audio.wav"}
            ),
            lambda value: value["candidate"]["resident"]["promptEvalMs"].update(
                {"sampleCount": 29}
            ),
            lambda value: value["candidate"]["resident"]["queueMs"].update(
                {"sampleCount": 29}
            ),
        ):
            malformed = copy.deepcopy(valid)
            mutate(malformed)
            with self.assertRaisesRegex(ValueError, "private_timing_invalid"):
                fixed_lab.normalize_private_timing_diagnostics(malformed)

    def test_private_timing_allows_distinct_activation_and_resident_cohorts(self) -> None:
        plan = lab.build_runner_plan(
            self.plan.candidate,
            profile="finalist",
            attempt=1,
            trust_root=self.root,
        )
        valid = self._timing_diagnostics(plan)
        normalized = fixed_lab.normalize_private_timing_diagnostics(valid)
        self.assertEqual(
            normalized["baseline"]["afterActivation"]["promptEvalMs"]["sampleCount"],
            40,
        )
        self.assertEqual(
            normalized["baseline"]["resident"]["promptEvalMs"]["sampleCount"],
            200,
        )
        for summary in valid["candidate"]["afterActivation"].values():
            summary["sampleCount"] = 39
        with self.assertRaisesRegex(ValueError, "private_timing_invalid"):
            fixed_lab.normalize_private_timing_diagnostics(valid)

    def test_external_signer_preserves_completed_samples_when_cleanup_is_dirty(self) -> None:
        dirty = self._receipt(self.plan)["cleanup"]
        dirty.update(
            {
                "status": "cleanup_required",
                "remainingGpuAllocations": 1,
            }
        )

        def owned_worker(mode: str, plan: lab.RunnerPlan) -> dict:
            if mode == "preflight":
                return {"ready": True, "code": "ready"}
            if mode == "run":
                return {
                    "receipt": self._receipt(plan),
                    "timingDiagnostics": self._timing_diagnostics(plan),
                }
            if mode == "cleanup":
                return {"cleanup": dirty}
            raise AssertionError("unexpected worker mode")

        request = {
            "schema": external_runner.REQUEST_SCHEMA,
            "plan": self.plan.to_dict(),
            "runnerCapability": self.runner_capability._issue_one_run(
                self.plan.run_id
            )._export_once(),
        }
        with patch.object(
            fixed_lab,
            "FIXED_LAB_ADAPTER",
            fixed_lab.OwnedDockerLabAdapter(owned_worker),
        ):
            raw, timing = external_runner._runner_measurement(request)
        receipt = lab.compile_runner_receipt(self.plan, raw, trust_root=self.root)
        decision = lab.evaluate_runner_receipt(
            self.plan,
            receipt,
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
        )
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.samples.warm_candidate, 30)
        self.assertEqual(receipt.cleanup.status, "cleanup_required")
        self.assertTrue(timing)
        self.assertEqual((decision.verdict, decision.code), ("inconclusive", "cleanup_required"))

    def test_progress_checkpoint_is_atomic_numeric_only_and_replaceable(self) -> None:
        sample = {"answerFirstPcmMs": 250.0}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker._write_progress_checkpoint(
                root,
                self.plan.to_dict(),
                sequence=1,
                phase="warm",
                completed_blocks=1,
                baseline_warm=[sample],
                candidate_warm=[{"answerFirstPcmMs": 200.0}],
                restart_eligible_baseline=2,
                restart_eligible_candidate=2,
                soak_turns=0,
            )
            worker._write_progress_checkpoint(
                root,
                self.plan.to_dict(),
                sequence=2,
                phase="soak",
                completed_blocks=15,
                baseline_warm=[sample] * 30,
                candidate_warm=[{"answerFirstPcmMs": 200.0}] * 30,
                restart_eligible_baseline=30,
                restart_eligible_candidate=30,
                soak_turns=25,
            )
            raw = (root / worker.PROGRESS_CHECKPOINT).read_text(encoding="ascii")
            checkpoint = json.loads(raw)
            self.assertEqual(checkpoint["sequence"], 2)
            self.assertEqual(checkpoint["warmBaseline"], 30)
            self.assertEqual(checkpoint["candidateWarmAnswerFirstPcmP95Ms"], 200.0)
            self.assertFalse((root / f"{worker.PROGRESS_CHECKPOINT}.tmp").exists())
            self.assertNotIn("prompt", raw.casefold())
            self.assertNotIn("fingerprint", raw.casefold())
            self.assertNotIn(str(root).casefold(), raw.casefold())

    def test_bot_only_reset_preserves_main_epoch_and_uses_no_dependencies(self) -> None:
        state = (
            "00000000-0000-0000-0000-000000000001",
            "1" * 64,
            "2" * 64,
            "3" * 64,
        )
        with (
            patch.object(worker, "_activation_state", side_effect=(state, state)),
            patch.object(worker, "_run_command", return_value="") as run,
        ):
            worker._reset_bot(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                config_dir=Path("/fixed/config"),
                compose_env={},
                deadline=worker.time.monotonic() + 60,
            )
        command = tuple(map(str, run.call_args.args[0]))
        self.assertIn("--no-deps", command)
        self.assertIn("--force-recreate", command)
        self.assertEqual(command[-1], "bot_api_lab")

    def test_restart_samples_are_evenly_spaced_and_endpoint_bound(self) -> None:
        indexes = worker._evenly_spaced_indexes(40, 30)
        self.assertEqual((indexes[0], indexes[-1]), (0, 39))
        self.assertEqual(len(indexes), 30)
        self.assertEqual(len(set(indexes)), 30)

    def test_worker_statistics_effect_uses_the_p95_point_estimate_sign(self) -> None:
        baseline_values = (
            588.28, 605.59, 951.91, 742.08, 875.15, 548.90, 797.16, 586.60,
            939.20, 903.98, 1183.38, 825.98, 600.98, 715.93, 626.51, 768.22,
            877.20, 904.55, 719.95, 718.80, 798.30, 830.44, 783.05, 919.27,
            1010.34, 876.70, 1060.98, 1153.10, 607.12, 907.84,
        )
        candidate_values = (
            955.65, 550.71, 755.98, 563.50, 944.32, 934.10, 655.90, 890.20,
            903.96, 769.09, 532.61, 645.18, 664.72, 1037.52, 704.50, 1061.34,
            713.28, 874.54, 994.45, 701.86, 770.31, 906.80, 1052.75, 830.82,
            935.23, 736.37, 1170.12, 815.19, 800.11, 991.36,
        )

        def blocks(values: tuple[float, ...]):
            return [
                (
                    {"answerFirstPcmMs": values[index]},
                    {"answerFirstPcmMs": values[index + 1]},
                )
                for index in range(0, len(values), 2)
            ]

        evidence = worker._statistics(
            blocks(baseline_values),
            blocks(candidate_values),
            run_id=f"sha256:{1:064x}",
        )
        point = worker._nearest(candidate_values, 0.95) - worker._nearest(
            baseline_values, 0.95
        )
        self.assertLess(point, 0)
        self.assertLess(evidence["warmAnswerFirstPcmP95EffectSize"], 0)
        self.assertLessEqual(
            evidence["warmAnswerFirstPcmP95DeltaCiLowMs"], point
        )
        self.assertGreaterEqual(
            evidence["warmAnswerFirstPcmP95DeltaCiHighMs"], point
        )

    def test_run_failure_returns_exact_cleanup_proof_and_inconclusive_receipt(self) -> None:
        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": self.plan.run_id,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }

        def failing_worker(mode: str, _plan: lab.RunnerPlan) -> dict:
            if mode == "run":
                raise RuntimeError("content must not escape")
            if mode == "cleanup":
                return {"cleanup": clean}
            return {"ready": True, "code": "ready"}

        unsigned = fixed_lab.OwnedDockerLabAdapter(failing_worker).run(self.plan)
        signed = lab.issue_runner_receipt(
            self.plan,
            unsigned,
            trust_root=self.root,
            runner_capability=self.runner_capability,
        )
        decision = lab.evaluate_runner_receipt(
            self.plan,
            signed,
            trust_root=self.root,
            evaluator_capability=self.evaluator_capability,
        )
        self.assertEqual(signed.status, "runner_failed")
        self.assertEqual(signed.cleanup.status, "clean")
        self.assertEqual((decision.verdict, decision.code), ("inconclusive", "runner_failed"))
        self.assertNotIn("content must not escape", json.dumps(signed.to_dict()))

    def test_unknown_cleanup_cannot_be_claimed_clean(self) -> None:
        installed = fixed_lab.OwnedDockerLabAdapter(
            lambda _mode, _plan: (_ for _ in ()).throw(RuntimeError("failed"))
        )
        unsigned = installed.run(self.plan)
        self.assertEqual(unsigned["cleanup"]["status"], "cleanup_required")
        self.assertGreater(unsigned["cleanup"]["remainingProcesses"], 0)

    def test_public_cleanup_uses_fixed_worker_and_fails_closed(self) -> None:
        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": self.plan.run_id,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        with patch.object(
            fixed_lab,
            "_invoke_worker",
            return_value={"cleanup": clean},
        ) as invoke:
            self.assertEqual(fixed_lab.cleanup_owned_lab(self.plan), clean)
        self.assertEqual(invoke.call_args.args[0], "cleanup")
        self.assertEqual(invoke.call_args.args[1]["plan"], self.plan.to_dict())

        with patch.object(
            fixed_lab,
            "_invoke_worker",
            side_effect=RuntimeError("transport killed"),
        ):
            unknown = fixed_lab.cleanup_owned_lab(self.plan)
        self.assertEqual(unknown["status"], "cleanup_required")
        self.assertGreater(unknown["remainingProcesses"], 0)

    def test_global_reconcile_uses_fixed_mode_and_fails_closed(self) -> None:
        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": fixed_lab.GLOBAL_RECONCILE_RUN_ID,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        with patch.object(
            fixed_lab,
            "_invoke_reconcile_worker",
            return_value={"cleanup": clean},
        ) as invoke:
            self.assertEqual(fixed_lab.reconcile_owned_lab(), clean)
        invoke.assert_called_once_with()

        with patch.object(
            fixed_lab,
            "_invoke_reconcile_worker",
            side_effect=RuntimeError("killed"),
        ):
            unknown = fixed_lab.reconcile_owned_lab()
        self.assertEqual(unknown["runId"], fixed_lab.GLOBAL_RECONCILE_RUN_ID)
        self.assertEqual(unknown["status"], "cleanup_required")

        with patch.object(fixed_lab, "_invoke_worker", return_value={}) as invoke:
            fixed_lab._invoke_reconcile_worker()
        self.assertEqual(
            invoke.call_args.args,
            (
                "reconcile",
                {
                    "schema": fixed_lab.LAB_WORKER_REQUEST_SCHEMA,
                    "mode": "reconcile",
                },
            ),
        )
        self.assertEqual(invoke.call_args.kwargs["timeout_s"], 180.0)

    def test_worker_cleanup_targets_only_exact_owner_and_run_labels(self) -> None:
        calls: list[tuple[str, ...]] = []

        def no_resources(command, **_kwargs):
            rendered = tuple(map(str, command))
            calls.append(rendered)
            return ""

        with patch.object(worker, "_run_command", side_effect=no_resources), patch.object(
            worker.time, "sleep", return_value=None
        ), patch.object(worker, "_owned_temp_paths", return_value=([], 0)), patch.object(
            worker, "_gpu_telemetry", return_value=("TCC", 0.0, 32000.0, 32607.0)
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )
        self.assertEqual(cleanup["status"], "clean")
        docker_calls = [
            command for command in calls if command[0] == str(Path("/fixed/docker"))
        ]
        self.assertEqual(len(docker_calls), 12)
        for command in docker_calls:
            self.assertIn(f"label=ai.evelyn.owner={lab.LAB_OWNER}", command)
            self.assertIn(f"label=ai.evelyn.run-id={self.plan.run_id}", command)
            self.assertNotIn("down", command)
        self.assertEqual(
            len(
                [
                    command
                    for command in calls
                    if command[0] == str(Path("/fixed/nvidia-smi"))
                ]
            ),
            0,
        )

    def test_cleanup_removes_hard_kill_temp_and_waits_out_delayed_daemon_create(self) -> None:
        container_id = "a" * 64
        container_scans = iter(("", "", container_id, "", "", ""))
        calls: list[tuple[str, ...]] = []

        def delayed_resource(command, **_kwargs):
            rendered = tuple(map(str, command))
            calls.append(rendered)
            if any("query-compute-apps=pid,used_gpu_memory" in part for part in rendered):
                return ""
            if rendered[1:3] == ("ps", "-aq"):
                return next(container_scans)
            return ""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = f"evelyn-latency-{self.plan.run_id[7:15]}-"
            current = root / f"{prefix}current"
            orphan = root / f"{prefix}hardkill"
            current.mkdir()
            orphan.mkdir()
            worker._write_owned_temp_marker(orphan, self.plan.run_id)
            with patch.object(worker.tempfile, "gettempdir", return_value=str(root)), patch.object(
                worker, "_run_command", side_effect=delayed_resource
            ), patch.object(
                worker, "_gpu_telemetry", return_value=("TCC", 0.0, 32000.0, 32607.0)
            ), patch.object(worker.time, "sleep", return_value=None):
                cleanup = worker._cleanup(
                    self.plan.to_dict(),
                    docker=Path("/fixed/docker"),
                    nvidia_smi=Path("/fixed/nvidia-smi"),
                    config_dir=current,
                )
            self.assertEqual(cleanup["status"], "clean")
            self.assertFalse(orphan.exists())
            self.assertTrue(current.exists())
        self.assertTrue(any(command[1:3] == ("rm", "-f") for command in calls))

    def test_temp_reconciliation_requires_exact_owner_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = f"evelyn-latency-{self.plan.run_id[7:15]}-"
            current = root / f"{prefix}current"
            valid = root / f"{prefix}valid"
            unowned = root / f"{prefix}unowned"
            for path in (current, valid, unowned):
                path.mkdir()
            worker._write_owned_temp_marker(valid, self.plan.run_id)
            with patch.object(worker.tempfile, "gettempdir", return_value=str(root)):
                removable, observed = worker._owned_temp_paths(
                    self.plan.run_id,
                    current_config_dir=current,
                )
            self.assertEqual(observed, 2)
            self.assertEqual(removable, [valid])

    def test_owned_temp_is_published_only_after_durable_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(worker.tempfile, "gettempdir", return_value=str(root)):
                published = worker._create_owned_temp_dir(self.plan.run_id)

            self.assertRegex(published.name, worker.OWNED_TEMP_NAME)
            self.assertTrue((published / worker.OWNED_TEMP_MARKER).is_file())

            with (
                patch.object(worker.tempfile, "gettempdir", return_value=str(root)),
                patch.object(worker.os, "replace", side_effect=OSError("fault")),
            ):
                with self.assertRaisesRegex(worker.LabFailure, "isolation_preflight"):
                    worker._create_owned_temp_dir(self.plan.run_id)

            active = [
                path
                for path in root.iterdir()
                if worker.OWNED_TEMP_NAME.fullmatch(path.name)
            ]
            self.assertEqual(active, [published])
            self.assertFalse(
                any(path.name.startswith("evelyn-latency-staging-") for path in root.iterdir())
            )

    def test_cleanup_uses_owned_gpu_observation_and_global_scope_has_no_run_filter(self) -> None:
        calls: list[tuple[str, ...]] = []

        def foreign_gpu(command, **_kwargs):
            rendered = tuple(map(str, command))
            calls.append(rendered)
            if any("query-compute-apps=pid,used_gpu_memory" in part for part in rendered):
                return "321, 1024\n"
            return ""

        with patch.object(worker, "_run_command", side_effect=foreign_gpu), patch.object(
            worker, "_owned_temp_paths", return_value=([], 0)
        ), patch.object(
            worker, "_gpu_telemetry", return_value=("TCC", 0.0, 32000.0, 32607.0)
        ), patch.object(worker.time, "sleep", return_value=None):
            cleanup = worker._cleanup(
                {"runId": fixed_lab.GLOBAL_RECONCILE_RUN_ID},
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
                all_runs=True,
            )
        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)
        docker_calls = [
            command for command in calls if command[0] == str(Path("/fixed/docker"))
        ]
        self.assertTrue(docker_calls)
        for command in docker_calls:
            self.assertIn(f"label=ai.evelyn.owner={lab.LAB_OWNER}", command)
            self.assertFalse(any("label=ai.evelyn.run-id=" in part for part in command))

        container_id = "b" * 64
        all_scans = iter((container_id, "", ""))
        running_scans = iter((container_id, "", ""))

        def owned_gpu(command, **_kwargs):
            rendered = tuple(map(str, command))
            if any("query-compute-apps=pid,used_gpu_memory" in part for part in rendered):
                return "321, 1024\n"
            if rendered[1:3] == ("ps", "-aq"):
                return next(all_scans)
            if rendered[1:3] == ("ps", "-q"):
                return next(running_scans)
            if rendered[1:3] == ("top", container_id):
                return "PID\n321\n"
            return ""

        with patch.object(worker, "_run_command", side_effect=owned_gpu), patch.object(
            worker, "_gpu_telemetry", return_value=("TCC", 0.0, 8000.0, 32607.0)
        ), patch.object(worker, "_owned_temp_paths", return_value=([], 0)), patch.object(
            worker.time, "sleep", return_value=None
        ), patch.object(worker, "CLEANUP_MAX_ROUNDS", 3):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )
        self.assertEqual(cleanup["status"], "cleanup_required")
        self.assertEqual(cleanup["remainingProcesses"], 0)
        self.assertEqual(cleanup["remainingGpuAllocations"], 1)

    def test_worker_command_allowlist_has_no_argument_smuggling(self) -> None:
        allowed = (sys.executable, "-I", str(fixed_lab.LAB_WORKER_SCRIPT))
        self.assertTrue(fixed_lab.is_fixed_lab_worker_command(sys.executable, allowed))
        self.assertFalse(
            fixed_lab.is_fixed_lab_worker_command(
                sys.executable,
                (*allowed, "--candidate", "4096"),
            )
        )
        external_runner._deny_process_creation("subprocess.Popen", (sys.executable, allowed))
        with self.assertRaisesRegex(RuntimeError, "child_process_forbidden"):
            external_runner._deny_process_creation("subprocess.Popen", (sys.executable, (*allowed, "x")))

        import_probe = subprocess.run(
            allowed,
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            timeout=10,
        )
        self.assertEqual(import_probe.returncode, 2)
        self.assertEqual(import_probe.stderr, b"")

    def test_external_runner_audit_accepts_only_exact_windows_command_line(self) -> None:
        allowed = (sys.executable, "-I", str(fixed_lab.LAB_WORKER_SCRIPT))
        command_line = subprocess.list2cmdline(allowed)
        self.assertTrue(fixed_lab.is_fixed_lab_worker_command(None, command_line))
        external_runner._deny_process_creation(
            "subprocess.Popen",
            (None, command_line, None, None),
        )
        self.assertFalse(
            fixed_lab.is_fixed_lab_worker_command(None, f"{command_line} --extra")
        )
        with self.assertRaisesRegex(RuntimeError, "child_process_forbidden"):
            external_runner._deny_process_creation(
                "subprocess.Popen",
                (None, f"{command_line} --extra", None, None),
            )

    def test_worker_audit_accepts_windows_none_executable_only_for_exact_argv(self) -> None:
        allowed = (str(Path(sys.executable).resolve()), "--version")
        worker._AUTHORIZED_COMMAND = allowed
        try:
            command_line = subprocess.list2cmdline(allowed)
            worker._audit("subprocess.Popen", (None, command_line, None, None))
            with self.assertRaisesRegex(RuntimeError, "child_process_forbidden"):
                worker._audit(
                    "subprocess.Popen",
                    (None, f"{command_line} --extra", None, None),
                )
        finally:
            worker._AUTHORIZED_COMMAND = None

    def test_worker_minimal_environment_keeps_windows_nvml_system_path(self) -> None:
        with patch.dict(os.environ, {"PROGRAMFILES": r"C:\Program Files"}, clear=False):
            child_env = worker._minimal_child_env(Path(r"C:\fixed\config"))
        self.assertEqual(child_env["PROGRAMFILES"], r"C:\Program Files")

    def test_wddm_idle_uses_bounded_telemetry_instead_of_gui_process_rows(self) -> None:
        readings = iter(
            (
                "WDDM, 1, 28000, 32607\n",
                "WDDM, 3, 27900, 32607\n",
                "WDDM, 10, 27800, 32607\n",
            )
        )
        with patch.object(worker, "_run_command", side_effect=lambda *_args, **_kwargs: next(readings)), patch.object(
            worker.time, "sleep", return_value=None
        ):
            self.assertTrue(worker._gpu_idle(Path("/fixed/nvidia-smi"), Path("/fixed/config")))

        readings = iter(
            (
                "WDDM, 1, 28000, 32607\n",
                "WDDM, 11, 27900, 32607\n",
                "WDDM, 1, 27800, 32607\n",
            )
        )
        with patch.object(worker, "_run_command", side_effect=lambda *_args, **_kwargs: next(readings)), patch.object(
            worker.time, "sleep", return_value=None
        ):
            self.assertFalse(worker._gpu_idle(Path("/fixed/nvidia-smi"), Path("/fixed/config")))

    def test_identity_probe_reports_gpu_idle_failure_exactly(self) -> None:
        plan = self.plan.to_dict()
        with (
            patch.object(worker, "_run_command", side_effect=("29.6.1", "{}", "", "", "")),
            patch.object(worker, "_image_metadata", return_value=[]),
            patch.object(worker, "_pinned_image_env", return_value={}),
            patch.object(worker, "_base_compose_env", return_value={}),
            patch.object(worker, "_validate_compose"),
            patch.object(worker, "_production_absent", return_value=True),
            patch.object(worker, "_running_gpu0_container_ids", return_value=set()),
            patch.object(worker, "_gpu_idle", return_value=False),
        ):
            with self.assertRaisesRegex(
                worker.LabFailure,
                "lab_gpu_idle_preflight_failed",
            ):
                worker._identity_probe_state(
                    plan,
                    docker=Path("/fixed/docker"),
                    nvidia_smi=Path("/fixed/nvidia-smi"),
                    config_dir=Path("/fixed/config"),
                    paths={},
                )

    def test_windows_lx_symlink_decoder_accepts_only_safe_relative_targets(self) -> None:
        target = b"libggml.so.0"
        payload = struct.pack(
            "<IHHI",
            worker.WINDOWS_LX_SYMLINK_TAG,
            4 + len(target),
            0,
            worker.WINDOWS_LX_SYMLINK_KIND,
        ) + target
        self.assertEqual(
            worker._decode_windows_lx_symlink(payload).as_posix(),
            "libggml.so.0",
        )
        escaped = payload[:-len(target)] + b"../outside.so"
        escaped = escaped[:4] + struct.pack("<H", len(escaped) - 8) + escaped[6:]
        with self.assertRaisesRegex(worker.LabFailure, "isolation_preflight"):
            worker._decode_windows_lx_symlink(escaped)

    def test_gpu0_inventory_reads_only_device_requests_and_exact_gpu1_bit(self) -> None:
        ids = [character * 64 for character in "abc"]
        requests = {
            ids[0]: ([{"Count": -1, "DeviceIDs": None, "Capabilities": [["gpu"]]}], ""),
            ids[1]: ([{"Count": -1, "DeviceIDs": None, "Capabilities": [["gpu"]]}], "1"),
            ids[2]: ([{"Count": 1, "DeviceIDs": ["0"], "Capabilities": [["gpu"]]}], "1"),
        }
        commands: list[tuple[str, ...]] = []

        def command_result(command, **_kwargs):
            rendered = tuple(map(str, command))
            commands.append(rendered)
            if rendered[1:4] == ("ps", "--no-trunc", "-q"):
                return "\n".join(ids)
            device_requests, marker = requests[rendered[-1]]
            return json.dumps(device_requests) + "\n" + marker

        with patch.object(worker, "_run_command", side_effect=command_result):
            self.assertEqual(
                worker._running_gpu0_container_ids(
                    Path("/fixed/docker"), Path("/fixed/config")
                ),
                {ids[0], ids[2]},
            )
        inspect_formats = [
            command[command.index("--format") + 1]
            for command in commands
            if "--format" in command
        ]
        self.assertTrue(inspect_formats)
        self.assertTrue(all("json .Config.Env" not in value for value in inspect_formats))

    def test_wddm_boundary_requires_exact_services_and_gpu0_owners(self) -> None:
        ids = [character * 64 for character in "abcd"]

        def command_result(command, **_kwargs):
            rendered = tuple(map(str, command))
            if "top" in rendered or any("query-compute-apps" in value for value in rendered):
                raise AssertionError("wddm_pid_path_used")
            if rendered[1:4] == ("ps", "--no-trunc", "-q"):
                return "\n".join(ids)
            if "main_llm_gateway_lab" in rendered:
                return "\n".join(ids)
            if "main_llm_lab" in rendered:
                return ids[0]
            if "tts_lab" in rendered:
                return ids[2]
            return ""

        with patch.object(worker, "_run_command", side_effect=command_result), patch.object(
            worker, "_gpu_telemetry", return_value=("WDDM", 25.0, 8000.0, 32607.0)
        ), patch.object(
            worker, "_running_gpu0_container_ids", return_value={ids[0], ids[2]}
        ):
            self.assertEqual(
                worker._gpu_boundary_observation(
                    self.plan.to_dict(),
                    Path("/fixed/docker"),
                    Path("/fixed/nvidia-smi"),
                    Path("/fixed/config"),
                    {},
                ),
                (25.0, 8000.0),
            )
        with patch.object(worker, "_run_command", side_effect=command_result), patch.object(
            worker, "_gpu_telemetry", return_value=("WDDM", 25.0, 8000.0, 32607.0)
        ), patch.object(
            worker, "_running_gpu0_container_ids", return_value={ids[0], ids[2], "e" * 64}
        ):
            with self.assertRaisesRegex(worker.LabFailure, "environment_drift"):
                worker._gpu_boundary_observation(
                    self.plan.to_dict(),
                    Path("/fixed/docker"),
                    Path("/fixed/nvidia-smi"),
                    Path("/fixed/config"),
                    {},
                )

    def test_wddm_cleanup_counts_only_exact_owned_gpu0_containers(self) -> None:
        container_id = "a" * 64
        container_scans = iter((container_id, "", "", "", ""))
        running_scans = iter((container_id, "", "", "", ""))

        def command_result(command, **_kwargs):
            rendered = tuple(map(str, command))
            if rendered[1:3] == ("ps", "-aq"):
                return next(container_scans)
            if rendered[1:3] == ("ps", "-q"):
                return next(running_scans)
            return ""

        telemetry = iter(
            (
                ("WDDM", 90.0, 8000.0, 32607.0),
                ("WDDM", 90.0, 8000.0, 32607.0),
                ("WDDM", 1.0, 28000.0, 32607.0),
                ("WDDM", 2.0, 27900.0, 32607.0),
                ("WDDM", 1.0, 27800.0, 32607.0),
            )
        )
        gpu_owners = iter(({container_id}, set(), set(), set(), set()))
        with patch.object(worker, "_run_command", side_effect=command_result), patch.object(
            worker, "_gpu_telemetry", side_effect=lambda *_args: next(telemetry)
        ), patch.object(
            worker,
            "_cleanup_gpu_baseline",
            return_value=("wddm", 28000.0, 32607.0),
        ), patch.object(
            worker, "_running_gpu0_container_ids", side_effect=lambda *_args: next(gpu_owners)
        ), patch.object(
            worker, "_cleanup_container_pids", side_effect=AssertionError("wddm_pid_path_used")
        ), patch.object(
            worker, "_owned_temp_paths", return_value=([], 0)
        ), patch.object(worker.time, "sleep", return_value=None), patch.object(
            worker, "CLEANUP_MAX_ROUNDS", 5
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )
        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)

    def test_wddm_cleanup_keeps_exact_owned_gpu0_container_dirty(self) -> None:
        container_id = "a" * 64

        def owned_resources(command, **_kwargs):
            rendered = tuple(map(str, command))
            if rendered[1:3] in {("ps", "-aq"), ("ps", "-q")}:
                return container_id
            return ""

        with (
            patch.object(worker, "_run_command", side_effect=owned_resources),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(
                worker,
                "_cleanup_gpu_baseline",
                return_value=("wddm", 31000.0, 32607.0),
            ),
            patch.object(
                worker,
                "_gpu_telemetry",
                return_value=("WDDM", 1.0, 26000.0, 32607.0),
            ),
            patch.object(
                worker, "_running_gpu0_container_ids", return_value={container_id}
            ),
            patch.object(worker.time, "sleep", return_value=None),
            patch.object(worker, "CLEANUP_MAX_ROUNDS", 3),
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )

        self.assertEqual(cleanup["status"], "cleanup_required")
        self.assertEqual(cleanup["remainingProcesses"], 1)
        self.assertEqual(cleanup["remainingGpuAllocations"], 1)

    def test_wddm_cleanup_ignores_global_gpu_memory_after_owned_resources_are_gone(self) -> None:
        def no_resources(_command, **_kwargs):
            return ""

        with (
            patch.object(worker, "_run_command", side_effect=no_resources),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(
                worker,
                "_cleanup_gpu_baseline",
                return_value=("wddm", 31000.0, 32607.0),
            ),
            patch.object(
                worker,
                "_gpu_telemetry",
                return_value=("WDDM", 1.0, 26000.0, 32607.0),
            ),
            patch.object(worker.time, "sleep", return_value=None),
            patch.object(worker, "CLEANUP_MAX_ROUNDS", 3),
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )

        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)

    def test_wddm_cleanup_finishes_an_exact_owned_zero_streak(self) -> None:
        telemetry = iter(
            [
                *(('WDDM', 90.0, 28000.0, 32607.0) for _ in range(6)),
                ('WDDM', 1.0, 28000.0, 32607.0),
                ('WDDM', 1.0, 28000.0, 32607.0),
                ('WDDM', 1.0, 28000.0, 32607.0),
            ]
        )
        with (
            patch.object(worker, "_run_command", return_value=""),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(
                worker,
                "_cleanup_gpu_baseline",
                return_value=("wddm", 28000.0, 32607.0),
            ),
            patch.object(worker, "_gpu_telemetry", side_effect=telemetry),
            patch.object(worker.time, "sleep", return_value=None),
            patch.object(worker, "CLEANUP_MAX_ROUNDS", 8),
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )

        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)

    def test_wddm_cleanup_global_utilization_spike_is_not_an_owned_allocation(self) -> None:
        telemetry = iter(
            [
                *(('WDDM', 90.0, 28000.0, 32607.0) for _ in range(6)),
                ('WDDM', 1.0, 28000.0, 32607.0),
                ('WDDM', 1.0, 28000.0, 32607.0),
                ('WDDM', 90.0, 28000.0, 32607.0),
            ]
        )
        with (
            patch.object(worker, "_run_command", return_value=""),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(
                worker,
                "_cleanup_gpu_baseline",
                return_value=("wddm", 28000.0, 32607.0),
            ),
            patch.object(worker, "_gpu_telemetry", side_effect=telemetry),
            patch.object(worker.time, "sleep", return_value=None),
            patch.object(worker, "CLEANUP_MAX_ROUNDS", 8),
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )

        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)

    def test_wddm_cleanup_does_not_wait_for_global_gpu_memory_release(self) -> None:
        telemetry = iter(
            [
                *(('WDDM', 1.0, 27000.0, 32607.0) for _ in range(20)),
                ('WDDM', 1.0, 28000.0, 32607.0),
                ('WDDM', 1.0, 28000.0, 32607.0),
                ('WDDM', 1.0, 28000.0, 32607.0),
            ]
        )
        with (
            patch.object(worker, "_run_command", return_value=""),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(
                worker,
                "_cleanup_gpu_baseline",
                return_value=("wddm", 28000.0, 32607.0),
            ),
            patch.object(worker, "_gpu_telemetry", side_effect=telemetry),
            patch.object(worker.time, "sleep", return_value=None),
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )

        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)

    def test_wddm_cleanup_without_baseline_uses_exact_owned_zero_streak(self) -> None:
        with (
            patch.object(worker, "_run_command", return_value=""),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(worker, "_cleanup_gpu_baseline", return_value=None),
            patch.object(
                worker,
                "_gpu_telemetry",
                return_value=("WDDM", 0.0, 32000.0, 32607.0),
            ),
            patch.object(worker.time, "sleep", return_value=None),
            patch.object(worker, "CLEANUP_MAX_ROUNDS", 3),
        ):
            cleanup = worker._cleanup(
                self.plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
            )

        self.assertEqual(cleanup["status"], "clean")
        self.assertEqual(cleanup["remainingGpuAllocations"], 0)

    def test_wddm_cleanup_rejects_driver_mode_flip(self) -> None:
        telemetry = iter(
            (
                ("WDDM", 0.0, 31000.0, 32607.0),
                ("TCC", 0.0, 31000.0, 32607.0),
            )
        )
        with (
            patch.object(worker, "_run_command", return_value=""),
            patch.object(worker, "_owned_temp_paths", return_value=([], 0)),
            patch.object(
                worker,
                "_cleanup_gpu_baseline",
                return_value=("wddm", 31000.0, 32607.0),
            ),
            patch.object(worker, "_gpu_telemetry", side_effect=telemetry),
            patch.object(worker.time, "sleep", return_value=None),
            patch.object(worker, "CLEANUP_MAX_ROUNDS", 3),
        ):
            with self.assertRaisesRegex(worker.LabFailure, "runner_failed"):
                worker._cleanup(
                    self.plan.to_dict(),
                    docker=Path("/fixed/docker"),
                    nvidia_smi=Path("/fixed/nvidia-smi"),
                    config_dir=Path("/fixed/config"),
                )

    def test_cleanup_retry_copies_prior_run_gpu_baseline_to_current_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefix = f"evelyn-latency-{self.plan.run_id[7:15]}-"
            current = root / f"{prefix}current"
            prior = root / f"{prefix}prior"
            current.mkdir()
            prior.mkdir()
            worker._write_owned_temp_marker(current, self.plan.run_id)
            worker._write_owned_temp_marker(prior, self.plan.run_id)
            worker._write_gpu_baseline_marker(
                prior,
                self.plan.run_id,
                ("wddm", 31000.0, 32607.0),
            )

            with patch.object(worker.tempfile, "gettempdir", return_value=str(root)):
                baseline = worker._cleanup_gpu_baseline(
                    self.plan.run_id,
                    current_config_dir=current,
                    current_run_id=self.plan.run_id,
                )

            self.assertEqual(baseline, ("wddm", 31000.0, 32607.0))
            self.assertIsNotNone(
                worker._read_gpu_baseline_marker(current, self.plan.run_id)
            )

    def test_global_cleanup_bootstraps_after_prebaseline_temp_orphan_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current"
            orphan = root / "orphan"
            current.mkdir()
            orphan.mkdir()
            paths = iter(
                (
                    ([orphan], 1),
                    ([], 0),
                    ([], 0),
                    ([], 0),
                )
            )
            with (
                patch.object(worker, "_cleanup_gpu_baseline", return_value=None),
                patch.object(worker, "_owned_temp_paths", side_effect=lambda *_args, **_kwargs: next(paths)),
                patch.object(worker, "_run_command", return_value=""),
                patch.object(
                    worker,
                    "_gpu_telemetry",
                    return_value=("WDDM", 1.0, 31000.0, 32607.0),
                ),
                patch.object(worker.time, "sleep", return_value=None),
                patch.object(worker, "CLEANUP_MAX_ROUNDS", 4),
            ):
                cleanup = worker._cleanup(
                    {"runId": fixed_lab.GLOBAL_RECONCILE_RUN_ID},
                    docker=Path("/fixed/docker"),
                    nvidia_smi=Path("/fixed/nvidia-smi"),
                    config_dir=current,
                    all_runs=True,
                )

            self.assertEqual(cleanup["status"], "clean")
            self.assertFalse(orphan.exists())
            self.assertIsNotNone(
                worker._read_gpu_baseline_marker(current, None)
            )

    def test_owned_dir_is_deleted_only_after_verified_clean_cleanup(self) -> None:
        clean = {
            "cleanup": {
                "status": "clean",
                "remainingProcesses": 0,
                "remainingGpuAllocations": 0,
                "remainingArtifacts": 0,
            }
        }
        dirty = copy.deepcopy(clean)
        dirty["cleanup"].update(
            {"status": "cleanup_required", "remainingGpuAllocations": 1}
        )
        self.assertTrue(worker._result_has_verified_clean_cleanup(clean))
        self.assertFalse(worker._result_has_verified_clean_cleanup(dirty))
        self.assertFalse(worker._result_has_verified_clean_cleanup({"error": "telemetry"}))

    def test_harness_gpu_snapshot_pins_gpu_zero_for_both_queries(self) -> None:
        class Completed:
            returncode = 0

            def __init__(self, stdout: str) -> None:
                self.stdout = stdout

        with patch.object(
            harness.subprocess,
            "run",
            side_effect=(Completed("8000\n"), Completed("123\n")),
        ) as run:
            harness._gpu_snapshot(b"k" * 32)
        self.assertEqual(len(run.call_args_list), 2)
        self.assertTrue(
            all("--id=0" in call.args[0] for call in run.call_args_list)
        )

    def test_harness_preserves_only_bounded_numeric_main_timing_diagnostics(self) -> None:
        marker_names = (
            "request_received",
            "turn_accepted",
            "ingress_committed",
            "route_done",
            "context_done",
            "prompt_compiled",
            "main_admission_requested",
            "main_request_written",
            "main_headers_received",
            "raw_first_token",
            "safe_first_delta",
            "speech_prefix_committed",
        )
        markers = {name: float(index * 10) for index, name in enumerate(marker_names)}
        sample = {
            "latencyTrace": {
                "schema": benchmark.VOICE_LATENCY_TRACE_SCHEMA,
                "markers_ms": markers,
                "durations_ms": {
                    "ingress_committed_to_route_done_ms": 10.0,
                    "route_done_to_context_done_ms": 10.0,
                    "main_request_written_to_raw_first_token_ms": 20.0,
                    "raw_first_token_to_speech_prefix_committed_ms": 20.0,
                },
            },
            "llmTimingMetrics": {
                "promptTokensProcessed": 20,
                "promptTokensCached": 180,
                "promptTokensTotal": 200,
                "promptCacheHitRatio": 0.9,
                "promptEvalMs": 4.5,
                "queueMs": 1.2,
            },
            "replyFingerprint": "reply",
            "ttsInputFingerprint": "reply",
            "replyChars": 4,
            "ttsInputChars": 4,
            "eventCounts": {"sentence": 1},
            "ttsFirstPcmMs": 80.0,
            "firstSentenceMs": 130.0,
            "postSttFirstPcmMs": 210.0,
        }

        normalized = harness._normalize_sample(sample, (9000.0, "a"), (8900.0, "a"))

        self.assertEqual(normalized["llmPromptEvalMs"], 4.5)
        self.assertEqual(normalized["llmPromptCacheHitRatio"], 0.9)
        self.assertEqual(normalized["llmPromptTokensProcessed"], 20)
        self.assertEqual(normalized["llmQueueMs"], 1.2)
        self.assertEqual(normalized["routeStageMs"], 10.0)
        self.assertEqual(normalized["contextStageMs"], 10.0)
        sample["llmTimingMetrics"]["privatePath"] = "C:/private/audio.wav"
        with self.assertRaisesRegex(RuntimeError, "diagnostics_invalid"):
            harness._normalize_sample(sample, (9000.0, "a"), (8900.0, "a"))

    def test_worker_transport_always_closes_its_process_tree(self) -> None:
        response = json.dumps(
            {
                "schema": fixed_lab.LAB_WORKER_RESPONSE_SCHEMA,
                "mode": "cleanup",
                "result": {"ok": True},
            },
            separators=(",", ":"),
        ).encode("ascii")

        class Child:
            pid = 123
            returncode = 0
            _handle = 456

            def communicate(self, **_kwargs):
                return response, b""

        child = Child()
        with (
            patch.object(fixed_lab.subprocess, "Popen", return_value=child) as popen,
            patch.object(fixed_lab, "_assign_windows_kill_job", return_value="job"),
            patch.object(fixed_lab, "_kill_worker_tree") as kill_tree,
        ):
            result = fixed_lab._invoke_worker(
                "cleanup",
                {"schema": "fixed", "mode": "cleanup"},
                timeout_s=1.0,
            )
        self.assertEqual(result, {"ok": True})
        kill_tree.assert_called_once_with(child, "job")
        self.assertEqual(
            popen.call_args.kwargs["start_new_session"],
            os.name != "nt",
        )

    def test_run_worker_reserves_full_cleanup_envelope(self) -> None:
        with patch.object(fixed_lab, "_invoke_worker", return_value={}) as invoke:
            fixed_lab._invoke_owned_lab_worker("run", self.plan)
        self.assertEqual(
            invoke.call_args.kwargs["timeout_s"],
            (self.plan.spec.max_runtime_ms / 1000.0) + 300.0,
        )

    def test_content_free_identity_discovery_returns_only_six_fixed_hashes(self) -> None:
        expected = self.root.pinned_identities.to_dict()
        with patch.object(
            fixed_lab,
            "_invoke_identity_worker",
            return_value={"identities": expected},
        ) as invoke:
            discovered = fixed_lab.discover_owned_lab_identities(
                self.plan.candidate.baseline_config
            )
        self.assertEqual(discovered.to_dict(), expected)
        invoke.assert_called_once_with(self.plan.candidate.baseline_config)

        with patch.object(
            fixed_lab,
            "_invoke_identity_worker",
            return_value={"errorCode": "lab_identity_preflight_failed"},
        ):
            with self.assertRaisesRegex(
                fixed_lab.LabIdentityDiscoveryError,
                "lab_identity_preflight_failed",
            ):
                fixed_lab.discover_owned_lab_identities(
                    self.plan.candidate.baseline_config
                )

    def test_worker_discovery_reuses_read_only_identity_probe(self) -> None:
        expected = self.root.pinned_identities.to_dict()
        baseline = self.plan.candidate.baseline_config.to_dict()
        paths = {"llama": Path("/fixed/llama")}
        with patch.object(worker, "_identity_probe", return_value=expected) as probe:
            result = worker._discover(
                baseline,
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
                paths=paths,
            )
        self.assertEqual(result, expected)
        pseudo_plan = probe.call_args.args[0]
        self.assertEqual(pseudo_plan["baselineConfig"], baseline)
        self.assertEqual(
            pseudo_plan["network"],
            "owned_internal_only_external_egress_disabled",
        )
        self.assertRegex(pseudo_plan["runId"], r"^sha256:[0-9a-f]{64}$")

    def test_worker_executes_warmed_abba_restart_ready_cases_and_candidate_soak(self) -> None:
        plan = lab.build_runner_plan(
            self.plan.candidate,
            profile="finalist",
            attempt=1,
            trust_root=self.root,
        )
        calls: list[tuple[str, str, int]] = []
        sample = {
            "postSttMainWriteMs": 10.0,
            "rawFirstTokenMs": 100.0,
            "rawToSafeSpeechMs": 20.0,
            "safePrefixCommitMs": 140.0,
            "ttsFirstPcmMs": 80.0,
            "firstSentenceCommitMs": 180.0,
            "answerFirstPcmMs": 260.0,
            "replyFingerprint": "hmac:reply",
            "ttsInputFingerprint": "hmac:speech",
            "replyChars": 10,
            "ttsInputChars": 8,
            "sentenceEvents": 1,
            "errorEvents": 0,
            "staleSpeech": 0,
            "unsafePrefix": 0,
            "orderViolation": 0,
            "externalInterference": 0,
            "safetyFailure": 0,
            "qualityFailure": 0,
            "gpuFreeMiB": 8192.0,
            "llmPromptEvalMs": 4.0,
            "llmPromptCacheHitRatio": 0.9,
            "llmPromptTokensProcessed": 20,
            "llmPromptTokensCached": 180,
            "llmPromptTokensTotal": 200,
            "llmQueueMs": 1.0,
            "routeStageMs": 3.0,
            "contextStageMs": 8.0,
        }

        restart_readiness: list[float] = []
        def batch(_plan, *, condition, phase, count, **kwargs):
            calls.append((condition, phase, count))
            self.assertNotIn("startup_epoch", kwargs)
            rows = [dict(sample) for _ in range(count)]
            if phase == "warm":
                rows[0].update(
                    {
                        "rawFirstTokenMs": 900.0,
                        "safePrefixCommitMs": 1000.0,
                        "answerFirstPcmMs": 1200.0,
                        "llmPromptEvalMs": 40.0,
                    }
                )
            return rows, 2, 0, None

        clean = {
            "schema": lab.CLEANUP_SCHEMA,
            "runId": plan.run_id,
            "owner": lab.LAB_OWNER,
            "status": "clean",
            "remainingProcesses": 0,
            "remainingGpuAllocations": 0,
            "remainingArtifacts": 0,
        }
        paths = {
            "llama": Path("/fixed/llama"),
            "main_build": Path("/fixed/llama/build-sm120-v1"),
            "profiles": Path("/fixed/profiles"),
            "hub": Path("/fixed/hub"),
        }
        image_env = {
            "LAB_MAIN_LLM_IMAGE": f"sha256:{101:064x}",
            "LAB_BOT_API_IMAGE": f"sha256:{102:064x}",
            "LAB_TTS_IMAGE": f"sha256:{103:064x}",
        }
        activations: list[tuple[bool, str]] = []

        def activate(_plan, *, config, initial, **_kwargs):
            condition = (
                "candidate"
                if dict(config) == plan.candidate.candidate_config.to_dict()
                else "baseline"
            )
            activations.append(
                (
                    initial,
                    condition,
                )
            )
            readiness_out = _kwargs.get("readiness_ms_out")
            if readiness_out is not None:
                value = 55_000.0 if condition == "candidate" else 60_000.0
                readiness_out.append(value)
                restart_readiness.append(value)
            return {}

        private_diagnostics: dict = {}
        with (
            patch.object(
                worker,
                "_identity_probe_state",
                return_value=(plan.candidate.identities.to_dict(), image_env),
            ),
            patch.object(
                worker,
                "_run_source_checks",
                return_value={"focusedTestFailures": 0, "privacyTestFailures": 0},
            ),
            patch.object(worker, "_activate", side_effect=activate),
            patch.object(worker, "_reset_bot") as reset_bot,
            patch.object(worker, "_harness_batch", side_effect=batch),
            patch.object(worker, "_write_progress_checkpoint") as checkpoint,
            patch.object(worker, "_statistics", wraps=worker._statistics) as statistics_call,
            patch.object(worker, "_tts_harness_warmup") as tts_warmup,
            patch.object(worker, "_container_observation", return_value=(100, 0)),
            patch.object(worker, "_gpu_boundary_observation", return_value=(25.0, 8000.0)),
            patch.object(worker, "_image_metadata", return_value=[{}, {}, {}]),
            patch.object(
                worker,
                "_actual_identities",
                return_value=plan.candidate.identities.to_dict(),
            ),
            patch.object(worker, "_production_absent", return_value=True),
            patch.object(worker, "_owned_artifact_bytes", return_value=17),
            patch.object(worker, "_epoch_artifact_bytes", return_value=11),
            patch.object(worker, "_cleanup", return_value=clean),
        ):
            receipt = worker._run_lab(
                plan.to_dict(),
                docker=Path("/fixed/docker"),
                nvidia_smi=Path("/fixed/nvidia-smi"),
                config_dir=Path("/fixed/config"),
                paths=paths,
                _private_diagnostics_out=private_diagnostics,
            )
        self.assertEqual(receipt["status"], "completed")
        tts_warmup.assert_called_once()
        self.assertEqual(
            [calls[index][0] for index in (0, 5, 10, 15)],
            [
                "baseline",
                "candidate",
                "candidate",
                "baseline",
            ],
        )
        self.assertTrue(all(call[1:] == ("warm", 2) for call in calls[:400]))
        self.assertEqual(len(restart_readiness), 80)
        self.assertTrue(all(value > 0 for value in restart_readiness))
        self.assertEqual(calls[400:], [("candidate", "soak", 25)] * 40)
        self.assertEqual(len(activations), 82)
        self.assertEqual(activations[0], (True, "baseline"))
        self.assertEqual(
            activations[1:81],
            [
                (False, "baseline"),
                (False, "candidate"),
                (False, "candidate"),
                (False, "baseline"),
            ]
            * 20,
        )
        self.assertEqual(activations[-1], (False, "candidate"))
        self.assertEqual(reset_bot.call_count, 320)
        self.assertEqual(checkpoint.call_count, 61)
        baseline_blocks, candidate_blocks = statistics_call.call_args.args
        self.assertEqual(len(baseline_blocks), 20)
        self.assertEqual(len(candidate_blocks), 20)
        self.assertTrue(all(len(block) == 10 for block in baseline_blocks))
        self.assertTrue(all(len(block) == 10 for block in candidate_blocks))
        self.assertEqual(receipt["resources"]["artifactBytes"], 28)
        self.assertEqual(
            receipt["baselineMetrics"]["restartStartupToReadyP95Ms"],
            60_000.0,
        )
        self.assertEqual(
            receipt["candidateMetrics"]["restartStartupToReadyP95Ms"],
            55_000.0,
        )
        self.assertEqual(receipt["baselineMetrics"]["rawFirstTokenP95Ms"], 100.0)
        self.assertEqual(receipt["candidateMetrics"]["rawFirstTokenP95Ms"], 100.0)
        self.assertEqual(
            receipt["baselineMetrics"]["warmAnswerFirstPcmP95Ms"], 260.0
        )
        self.assertEqual(
            receipt["samples"],
            {
                "warmBaseline": 200,
                "warmCandidate": 200,
                "restartReadyBaseline": 30,
                "restartReadyCandidate": 30,
                "soakTurns": 1000,
                "abbaBlocks": 20,
            },
        )
        self.assertEqual(
            set(private_diagnostics["baseline"]),
            {"afterActivation", "resident"},
        )
        self.assertEqual(
            set(private_diagnostics["candidate"]),
            {"afterActivation", "resident"},
        )
        self.assertEqual(
            private_diagnostics["baseline"]["afterActivation"]
            ["promptEvalMs"]["sampleCount"],
            40,
        )
        for condition in ("baseline", "candidate"):
            for cohort in ("afterActivation", "resident"):
                expected = 40 if cohort == "afterActivation" else 200
                self.assertEqual(
                    private_diagnostics[condition][cohort]
                    ["promptTokensCached"]["sampleCount"],
                    expected,
                )
                self.assertEqual(
                    private_diagnostics[condition][cohort]
                    ["promptTokensTotal"]["sampleCount"],
                    expected,
                )
        self.assertEqual(
            private_diagnostics["baseline"]["resident"]
            ["promptEvalMs"]["sampleCount"],
            200,
        )
        self.assertEqual(
            private_diagnostics["candidate"]["afterActivation"]
            ["promptEvalMs"]["sampleCount"],
            40,
        )
        self.assertEqual(
            private_diagnostics["candidate"]["resident"]
            ["promptEvalMs"]["sampleCount"],
            200,
        )
        self.assertEqual(
            private_diagnostics["candidate"]["afterActivation"]
            ["rawFirstTokenMs"]["p95"],
            900.0,
        )
        self.assertEqual(
            private_diagnostics["candidate"]["resident"]
            ["answerFirstPcmMs"]["p95"],
            260.0,
        )

    def test_plan_reconstruction_rejects_smuggling_and_run_id_changes(self) -> None:
        self.assertEqual(worker._validate_plan_dict(self.plan.to_dict()), self.plan.to_dict())
        for mutate in (
            lambda value: value.update({"network": "default"}),
            lambda value: value["bounds"].update({"maxConcurrentRequests": 2}),
            lambda value: value.update({"runId": f"sha256:{999:064x}"}),
        ):
            raw = copy.deepcopy(self.plan.to_dict())
            mutate(raw)
            with self.assertRaisesRegex(ValueError, "runner_plan_invalid"):
                worker._validate_plan_dict(raw)

    def test_operator_paths_are_absolute_disjoint_and_contained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            llama = root / "llama"
            server = root / "omnivoice-server"
            profiles = root / "profiles"
            candidate_build = llama / "build-sm120-v1"
            outside_build = root / "outside-build"
            (llama / "build/bin").mkdir(parents=True)
            (candidate_build / "bin").mkdir(parents=True)
            (outside_build / "bin").mkdir(parents=True)
            (llama / worker.MODEL_RELATIVE).parent.mkdir(parents=True)
            (llama / "build/bin/llama-server").write_bytes(b"default-server")
            (candidate_build / "bin/llama-server").write_bytes(b"candidate-server")
            (outside_build / "bin/llama-server").write_bytes(b"outside-server")
            (llama / worker.MODEL_RELATIVE).write_bytes(b"model")
            (server / "hub").mkdir(parents=True)
            (profiles / "evelyn").mkdir(parents=True)
            (profiles / "evelyn/ref.wav").write_bytes(b"wav")
            env = {
                "EVELYN_LLAMA_CPP_DIR": str(llama.resolve()),
                "EVELYN_OMNIVOICE_SERVER_DIR": str(server.resolve()),
                "EVELYN_OMNIVOICE_PROFILES_DIR": str(profiles.resolve()),
            }
            with patch.dict(os.environ, env, clear=True):
                default_paths = worker._operator_paths()
            env["EVELYN_MAIN_LLM_BUILD_DIR"] = str(candidate_build.resolve())
            with patch.dict(os.environ, env, clear=True):
                paths = worker._operator_paths()
            self.assertEqual(paths["model"], (llama / worker.MODEL_RELATIVE).resolve())
            self.assertEqual(paths["main_build"], candidate_build.resolve())
            self.assertEqual(paths["server"], (candidate_build / "bin/llama-server").resolve())
            self.assertNotEqual(
                worker._server_build_identity(default_paths["main_build"]),
                worker._server_build_identity(paths["main_build"]),
            )
            first_profile = worker._profile_tree_hash(paths["profiles"])
            (profiles / "evelyn/ref.wav").write_bytes(b"changed-wav")
            self.assertNotEqual(first_profile, worker._profile_tree_hash(paths["profiles"]))

            env["EVELYN_MAIN_LLM_BUILD_DIR"] = str(outside_build.resolve())
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(worker.LabFailure, "isolation_preflight"):
                    worker._operator_paths()
            env["EVELYN_MAIN_LLM_BUILD_DIR"] = str(candidate_build.resolve())
            env["EVELYN_OMNIVOICE_PROFILES_DIR"] = str(server.resolve())
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(worker.LabFailure, "isolation_preflight"):
                    worker._operator_paths()

    def test_adapter_forwards_only_the_explicit_main_build_selector(self) -> None:
        selector = r"C:\fixed\llama.cpp\build-sm120-v1"
        with patch.dict(
            os.environ,
            {"EVELYN_MAIN_LLM_BUILD_DIR": selector},
            clear=True,
        ):
            env = fixed_lab._worker_env()
        self.assertEqual(env["EVELYN_MAIN_LLM_BUILD_DIR"], selector)

    def test_resource_evidence_is_measured_without_clamping(self) -> None:
        container_id = "a" * 64

        def command_result(command, **_kwargs):
            rendered = tuple(map(str, command))
            if "inspect" in rendered:
                return '{"OOMKilled":false,"Running":true}\n'
            if "exec" in rendered:
                return str(40 * 1024 * 1024 * 1024)
            return container_id

        with patch.object(worker, "_run_command", side_effect=command_result):
            ram_mib, oom_count = worker._container_observation(
                self.plan.to_dict(),
                Path("/fixed/docker"),
                Path("/fixed/config"),
                {},
            )
        self.assertEqual((ram_mib, oom_count), (40 * 1024, 0))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one").write_bytes(b"123")
            (root / "nested").mkdir()
            (root / "nested/two").write_bytes(b"4567")
            self.assertEqual(worker._owned_artifact_bytes(root, 1024), 7)

    def test_cache_proof_and_foreign_gpu_pid_observations_fail_closed(self) -> None:
        ready_state = {
            "runtime": {
                "services": {
                    "mainReady": True,
                    "mainWarmupReady": True,
                    "sourceAligned": True,
                },
                "mainWarmup": {
                    "status": "done",
                    "ready": True,
                    "cacheProof": True,
                    "promptAbiRequired": True,
                    "promptAbiExact": True,
                    "promptAbiProductionMatch": True,
                    "promptAbiProductionMatch": True,
                },
            }
        }
        with patch.object(harness.benchmark, "_json_request", return_value=(200, ready_state)):
            self.assertIs(harness._cache_proof_ready("http://fixed/state"), True)
        ready_state["runtime"]["mainWarmup"]["cacheProof"] = False
        with patch.object(harness.benchmark, "_json_request", return_value=(200, ready_state)):
            self.assertIs(harness._cache_proof_ready("http://fixed/state"), False)
        ready_state["runtime"]["mainWarmup"]["cacheProof"] = True
        ready_state["runtime"]["mainWarmup"]["promptAbiExact"] = False
        self.assertIs(harness._lab_state_ready(ready_state), False)
        ready_state["runtime"]["mainWarmup"]["promptAbiExact"] = True
        ready_state["runtime"]["mainWarmup"]["promptAbiProductionMatch"] = False
        self.assertIs(harness._lab_state_ready(ready_state), False)

        ids = [character * 64 for character in "abcd"]

        def foreign_process(command, **_kwargs):
            rendered = tuple(map(str, command))
            if "top" in rendered:
                return "PID\n101\n"
            if "compose" in rendered and "main_llm_lab" in rendered:
                return ids[0]
            if any("query-compute-apps" in value for value in rendered):
                return "999, 1024\n"
            return "\n".join(ids)

        with patch.object(worker, "_run_command", side_effect=foreign_process), patch.object(
            worker, "_gpu_telemetry", return_value=("TCC", 25.0, 8000.0, 32607.0)
        ):
            with self.assertRaisesRegex(worker.LabFailure, "environment_drift"):
                worker._gpu_boundary_observation(
                    self.plan.to_dict(),
                    Path("/fixed/docker"),
                    Path("/fixed/nvidia-smi"),
                    Path("/fixed/config"),
                    {},
                )

    def test_restart_ready_harness_does_not_measure_container_launch_delay(self) -> None:
        env = {
            "LAB_CONDITION": "baseline",
            "LAB_PHASE": "restart_ready",
            "LAB_EQUIVALENCE_KEY_HEX": "ab" * 32,
            "LAB_SAMPLE_COUNT": "1",
            "LAB_CHAT_URL": "http://bot_api_lab:8798/api/control-page/chat-stream",
            "LAB_STATE_URL": "http://bot_api_lab:8798/api/control-page/state",
            "LAB_TTS_URL": "http://tts_lab:8880/v1/audio/speech",
        }
        normalized = {"answerFirstPcmMs": 100.0}
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(harness, "_has_external_default_route", return_value=False),
            patch.object(
                harness.benchmark,
                "wait_until_ready",
                return_value={"startupToReadyMs": None},
            ) as wait_ready,
            patch.object(harness, "_cache_proof_ready", return_value=True),
            patch.object(harness, "_gpu_snapshot", return_value=(8192.0, "pid-proof")),
            patch.object(harness.benchmark, "run_once", return_value={}),
            patch.object(harness, "_normalize_sample", return_value=normalized),
        ):
            result = harness.run()
        self.assertIsNone(result["startupToReadyMs"])
        self.assertEqual(result["cacheProofFailures"], 0)
        self.assertEqual(result["samples"], [normalized])
        self.assertIsNone(wait_ready.call_args.kwargs["startup_epoch"])
        self.assertIs(wait_ready.call_args.kwargs["state_ready"], harness._lab_state_ready)

    def test_compose_preflight_requires_exact_flashinfer_scratch_mount(self) -> None:
        revision = "a" * 40
        image_env = {
            "LAB_MAIN_LLM_IMAGE": f"sha256:{101:064x}",
            "LAB_BOT_API_IMAGE": f"sha256:{102:064x}",
            "LAB_TTS_IMAGE": f"sha256:{103:064x}",
            "LAB_BOT_SOURCE_REVISION": revision,
            "LAB_MODEL_IDENTITY": "d" * 64,
            "LAB_SERVER_IDENTITY": "e" * 64,
            "LAB_LLAMA_CPP_DIR": "/fixed/llama",
            "LAB_MAIN_LLM_BUILD_DIR": "/fixed/llama/build-sm120-v1",
            "LAB_OMNIVOICE_PROFILES_DIR": "/fixed/profiles",
            "LAB_OMNIVOICE_HUB_DIR": "/fixed/hub",
            "LAB_TOOLS_DIR": "/fixed/tools",
        }
        run_id = "sha256:" + "b" * 64
        services = {}
        for name in worker.EXPECTED_SERVICES:
            image = (
                image_env["LAB_MAIN_LLM_IMAGE"]
                if name == "main_llm_lab"
                else image_env["LAB_TTS_IMAGE"]
                if name == "tts_lab"
                else image_env["LAB_BOT_API_IMAGE"]
            )
            services[name] = {
                "image": image,
                "pull_policy": "never",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "labels": {
                    "ai.evelyn.owner": worker.OWNER,
                    "ai.evelyn.run-id": run_id,
                },
                "networks": {"lab_internal": None},
                "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m"],
                "volumes": [],
            }
        services["tts_lab"]["tmpfs"].append(
            "/home/ubuntu/.cache/flashinfer:rw,noexec,nosuid,nodev,size=64m"
        )
        services["bot_api_lab"]["environment"] = {
            "EVELYN_EXPECTED_SOURCE_REVISION": revision,
            "MAIN_LLM_PORT": "9819",
            "FAST_CONTROL_CONTINUITY_ENABLED": "true",
            "CROSS_SURFACE_CONTINUITY_ENABLED": "false",
        }
        services["bot_api_lab"]["healthcheck"] = {
            "test": ["CMD", "python", "-c", worker.BOT_STRICT_HEALTHCHECK_SOURCE]
        }
        services["main_llm_lab"]["volumes"] = [
            {
                "type": "bind",
                "source": image_env["LAB_LLAMA_CPP_DIR"],
                "target": "/llama",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": image_env["LAB_MAIN_LLM_BUILD_DIR"],
                "target": "/llama/build",
                "read_only": True,
            },
            {
                "type": "volume",
                "source": "main_llm_epoch_lab",
                "target": "/main-llm-epoch",
            },
        ]
        for name in ("main_llm_gateway_lab", "bot_api_lab"):
            services[name]["volumes"] = [
                {
                    "type": "volume",
                    "source": "main_llm_epoch_lab",
                    "target": "/main-llm-epoch",
                    "read_only": True,
                }
            ]
        services["tts_lab"]["volumes"] = [
            {
                "type": "bind",
                "source": image_env["LAB_OMNIVOICE_PROFILES_DIR"],
                "target": "/home/ubuntu/app/profiles",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": image_env["LAB_OMNIVOICE_HUB_DIR"],
                "target": "/home/ubuntu/.cache/huggingface/hub",
                "read_only": True,
            },
        ]
        services["lab_harness"]["volumes"] = [
            {
                "type": "bind",
                "source": image_env["LAB_TOOLS_DIR"],
                "target": "/lab/tools",
                "read_only": True,
            }
        ]
        services["main_llm_lab"]["environment"] = {
            "MAIN_LLM_MODEL_IDENTITY": image_env["LAB_MODEL_IDENTITY"],
            "MAIN_LLM_SERVER_IDENTITY": image_env["LAB_SERVER_IDENTITY"],
        }
        services["lab_harness"]["environment"] = {
            "LAB_CHAT_URL": "http://bot_api_lab:8798/api/control-page/chat-stream",
            "LAB_STATE_URL": "http://bot_api_lab:8798/api/control-page/state",
            "LAB_TTS_URL": "http://tts_lab:8880/v1/audio/speech",
            "LAB_MAIN_DIRECT_URL": "http://main_llm_gateway_lab:9819/v1/chat/completions",
            "LAB_EXECUTION_MODE": "e2e",
            "LAB_CONDITION": "baseline",
            "LAB_PHASE": "warm",
            "LAB_SAMPLE_COUNT": "1",
            "LAB_EQUIVALENCE_KEY_HEX": "0" * 64,
        }
        labels = {
            "ai.evelyn.owner": worker.OWNER,
            "ai.evelyn.run-id": run_id,
        }
        config = {
            "services": services,
            "networks": {
                "lab_internal": {
                    "name": f"{worker._project_name(run_id)}_lab_internal",
                    "ipam": {},
                    "internal": True,
                    "labels": labels,
                }
            },
            "volumes": {
                "main_llm_epoch_lab": {
                    "name": f"{worker._project_name(run_id)}_main_llm_epoch_lab",
                    "labels": labels,
                }
            },
        }
        worker._validate_compose(config, run_id, image_env)
        mutations = (
            lambda value: value["networks"]["lab_internal"]["labels"].update(
                {"ai.evelyn.owner": "foreign"}
            ),
            lambda value: value["volumes"]["main_llm_epoch_lab"]["labels"].pop(
                "ai.evelyn.run-id"
            ),
            lambda value: value["networks"]["lab_internal"].update(
                {"external": True}
            ),
            lambda value: value["volumes"]["main_llm_epoch_lab"].update(
                {"external": True}
            ),
            lambda value: value["volumes"].update(
                {"unexpected": {"labels": labels}}
            ),
            lambda value: value["services"]["bot_api_lab"]["volumes"].append(
                {"type": "volume", "source": "unexpected"}
            ),
            lambda value: value["services"]["bot_api_lab"]["volumes"][0].update(
                {"read_only": False}
            ),
            lambda value: value["services"]["main_llm_lab"]["volumes"][1].update(
                {"source": "/fixed/llama/build"}
            ),
            lambda value: value["services"]["lab_privacy_checks"]["volumes"].append(
                {
                    "type": "bind",
                    "source": "C:/",
                    "target": "/host",
                    "read_only": True,
                }
            ),
            lambda value: value["services"]["bot_api_lab"]["healthcheck"].update(
                {
                    "test": [
                        "CMD",
                        "python",
                        "-c",
                        "raise SystemExit(0) # /api/control-page/state mainReady sourceAligned mainWarmup cacheProof promptAbiProductionMatch promptAbiExact",
                    ]
                }
            ),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                invalid = copy.deepcopy(config)
                mutate(invalid)
                with self.assertRaisesRegex(worker.LabFailure, "isolation_preflight"):
                    worker._validate_compose(invalid, run_id, image_env)
        services["tts_lab"]["tmpfs"].pop()
        with self.assertRaisesRegex(worker.LabFailure, "isolation_preflight"):
            worker._validate_compose(config, run_id, image_env)

    def test_pinned_bot_image_requires_valid_embedded_source_revision(self) -> None:
        revision = "c" * 40
        images = [
            {"Id": f"sha256:{index:064x}", "Config": {"Env": []}}
            for index in range(1, 4)
        ]
        images[1]["Config"]["Env"] = [f"EVELYN_IMAGE_SOURCE_REVISION={revision}"]
        self.assertEqual(
            worker._pinned_image_env(images)["LAB_BOT_SOURCE_REVISION"],
            revision,
        )
        images[1]["Config"]["Env"] = ["EVELYN_IMAGE_SOURCE_REVISION=unversioned"]
        with self.assertRaisesRegex(worker.LabFailure, "identity_preflight"):
            worker._pinned_image_env(images)

    def test_compose_is_owned_internal_only_and_has_no_external_effect_services(self) -> None:
        text = (REPO_ROOT / "docker-compose.main-latency-lab.yml").read_text(encoding="utf-8")
        self.assertIn("internal: true", text)
        self.assertNotIn("ports:", text)
        self.assertNotIn("extra_hosts:", text)
        self.assertNotIn("/var/run/docker.sock", text)
        self.assertIn('DISCORD_ENABLED: "false"', text)
        self.assertIn('MINECRAFT_ENABLED: "false"', text)
        self.assertIn("read_only: true", text)
        self.assertIn("EVELYN_EXPECTED_SOURCE_REVISION:", text)
        self.assertIn('MAIN_LLM_PORT: "9819"', text)
        self.assertIn('FAST_CONTROL_CONTINUITY_ENABLED: "true"', text)
        self.assertIn('CROSS_SURFACE_CONTINUITY_ENABLED: "false"', text)
        self.assertIn("http://127.0.0.1:8798/api/control-page/state", text)
        self.assertIn('LAB_CONDITION: "${LAB_CONDITION:-baseline}"', text)
        self.assertNotIn("LAB_STARTUP_EPOCH", text)
        self.assertIn(
            "LAB_MAIN_DIRECT_URL: http://main_llm_gateway_lab:9819/v1/chat/completions",
            text,
        )
        self.assertIn('LAB_EXECUTION_MODE: "${LAB_EXECUTION_MODE:-e2e}"', text)
        self.assertIn(
            "/home/ubuntu/.cache/flashinfer:rw,noexec,nosuid,nodev,size=64m",
            text,
        )
        self.assertIn("MAIN_LLM_SERVER_IDENTITY_FILE", text)
        self.assertIn("LAB_MAIN_LLM_BUILD_DIR", text)
        self.assertIn("target: /llama/build", text)
        self.assertIn("MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE", text)
        self.assertIn("runtime_argv=(", text)
        self.assertNotIn("ldd \"$${server_path}\"", text)
        self.assertIn("MAIN_LLM_MODEL_IDENTITY:", text)
        self.assertIn("MAIN_LLM_SERVER_IDENTITY:", text)
        self.assertIn("MAIN_LLM_CUDA_GRAPHS_ENABLED:", text)
        self.assertIn("MAIN_LLM_SWA_FULL_ENABLED:", text)
        self.assertIn("1) swa_full_args=(--swa-full) ;;", text)
        self.assertIn("0) swa_full_args=() ;;", text)
        self.assertIn(
            "0) export GGML_CUDA_DISABLE_GRAPHS=1; graph_disable_state=present ;;",
            text,
        )
        self.assertIn(
            "1) unset GGML_CUDA_DISABLE_GRAPHS; graph_disable_state=absent ;;",
            text,
        )
        self.assertIn(
            "GGML_CUDA_DISABLE_GRAPHS=$${graph_disable_state}",
            text,
        )
        self.assertIn("GGML_CUDA_GRAPH_OPT=$${GGML_CUDA_GRAPH_OPT}", text)
        baseline = self.plan.candidate.baseline_config.to_dict()
        changed = dict(baseline)
        changed["main.batch"] = 1024
        self.assertNotEqual(
            worker._runtime_identity(baseline),
            worker._runtime_identity(changed),
        )
        graph_disabled = dict(baseline)
        graph_disabled["main.cudaGraph"] = 0
        self.assertNotEqual(
            worker._runtime_identity(baseline),
            worker._runtime_identity(graph_disabled),
        )
        swa_full = dict(baseline)
        swa_full["main.swaFull"] = 1
        self.assertNotIn("--swa-full", worker._runtime_argv(baseline))
        self.assertIn("--swa-full", worker._runtime_argv(swa_full))
        self.assertNotEqual(
            worker._runtime_identity(baseline),
            worker._runtime_identity(swa_full),
        )


if __name__ == "__main__":
    unittest.main()
