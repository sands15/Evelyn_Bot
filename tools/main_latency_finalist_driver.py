from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from tools.main_latency_fixed_lab_adapter import (
    LabIdentityDiscoveryError,
    discover_owned_lab_identities,
    reconcile_owned_lab,
)
from tools.main_latency_lab_contract import (
    build_runner_plan,
    compile_runner_receipt,
    evaluate_runner_receipt,
    issue_host_restoration_proof,
)
from tools.main_latency_host_lifecycle import MainLatencyHostLifecycle
from tools.main_latency_finalist_verifier import verify_completed_artifact
from tools.main_latency_optimizer_loop import (
    FixedSubprocessRunnerTransport,
    RunnerTransportError,
    _OwnedLabCampaignLock,
)
from tools.optimize_main_latency import (
    MainLatencyConfig,
    bootstrap_ephemeral_fixed_coordinator,
    candidate_proposal,
    compile_candidate,
)


DOCKER_EXE = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
NVIDIA_SMI_EXE = Path(r"C:\Windows\System32\nvidia-smi.exe")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def exact_clean(value: Any) -> bool:
    return isinstance(value, dict) and (
        value.get("status") == "clean"
        and value.get("remainingProcesses") == 0
        and value.get("remainingGpuAllocations") == 0
        and value.get("remainingArtifacts") == 0
    )


def _discover_identities(
    baseline: MainLatencyConfig,
    state: dict[str, Any],
    persist: Any,
) -> Any:
    for attempt in range(1, 4):
        state["identityDiscoveryAttemptCount"] = attempt
        try:
            return discover_owned_lab_identities(baseline)
        except LabIdentityDiscoveryError as exc:
            state["identityDiscoveryLastCode"] = exc.code
            persist()
            if exc.code != "lab_gpu_idle_preflight_failed" or attempt == 3:
                raise
            time.sleep(15.0)


def _apply_recovered_completion(
    state: dict[str, Any],
    receipt: Any,
    evaluation: Any,
    timing_diagnostics: dict[str, Any],
    terminal_cleanup: Any,
    host_restoration_proof: Any,
    *,
    run_id: str,
    candidate_id: str,
) -> bool:
    receipt_dict = receipt.to_dict()
    evaluation_dict = evaluation.to_dict()
    state.update(
        {
            "preservedSignedReceipt": receipt_dict,
            "preservedTimingDiagnostics": dict(timing_diagnostics),
            "preservedEvaluation": evaluation_dict,
        }
    )
    cleanup = receipt.cleanup
    attestation = receipt.runner_attestation
    feedback = evaluation.promotion_feedback
    evidence = evaluation.promotion_evidence
    host_proof_id = getattr(host_restoration_proof, "proof_id", None)
    eligible = (
        exact_clean(cleanup.to_dict())
        and receipt.status == "completed"
        and receipt.run_id == cleanup.run_id == attestation.run_id == run_id
        and receipt.candidate_id
        == attestation.candidate_id
        == evaluation.candidate_id
        == getattr(feedback, "candidate_id", None)
        == getattr(evidence, "candidate_id", None)
        == candidate_id
        and receipt.receipt_id
        == attestation.receipt_id
        == evaluation.receipt_id
        == getattr(evidence, "receipt_id", None)
        and cleanup.proof_id
        == attestation.cleanup_proof_id
        == evaluation.cleanup_proof_id
        == getattr(evidence, "cleanup_proof_id", None)
        and evaluation.run_id == getattr(evidence, "run_id", None) == run_id
        and evaluation.evaluation_id == getattr(evidence, "evaluation_id", None)
        and evaluation.verdict == getattr(feedback, "verdict", None) == "eligible"
        and evaluation.code == "candidate_passed"
        and evaluation.gate == "passed"
        and getattr(host_restoration_proof, "status", None) == "clean"
        and getattr(host_restoration_proof, "run_id", None) == run_id
        and getattr(host_restoration_proof, "candidate_id", None) == candidate_id
        and getattr(host_restoration_proof, "receipt_id", None)
        == receipt.receipt_id
        and getattr(host_restoration_proof, "cleanup_proof_id", None)
        == cleanup.proof_id
        and evaluation.host_restoration_proof_id == host_proof_id
        and getattr(feedback, "codes", None) == ("candidate_passed",)
    )
    if not exact_clean(terminal_cleanup) or not eligible:
        state["status"] = "cleanup_required"
        return False
    state.update(
        {
            "status": "completed",
            "receipt": receipt_dict,
            "timingDiagnostics": dict(timing_diagnostics),
            "evaluation": evaluation_dict,
            "resultRecovery": "signed_receipt_after_transient_transport_cleanup",
        }
    )
    return True


PROGRESS_FIELDS = {
    "schema",
    "owner",
    "runId",
    "candidateId",
    "sequence",
    "phase",
    "abbaBlocksCompleted",
    "abbaBlocksTotal",
    "warmBaseline",
    "warmCandidate",
    "restartEligibleBaseline",
    "restartEligibleCandidate",
    "restartReadyTarget",
    "soakTurns",
    "soakTarget",
    "baselineWarmAnswerFirstPcmP50Ms",
    "baselineWarmAnswerFirstPcmP95Ms",
    "candidateWarmAnswerFirstPcmP50Ms",
    "candidateWarmAnswerFirstPcmP95Ms",
}
OWNED_DIR = re.compile(r"evelyn-latency-[0-9a-f]{8}-[A-Za-z0-9_-]+\Z")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    with temporary.open("wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_progress(run_id: str, candidate_id: str) -> dict[str, Any] | None:
    prefix = f"evelyn-latency-{run_id[7:15]}-"
    newest: dict[str, Any] | None = None
    try:
        entries = list(Path(tempfile.gettempdir()).iterdir())
    except OSError:
        return None
    for directory in entries:
        try:
            eligible = (
                directory.name.startswith(prefix)
                and OWNED_DIR.fullmatch(directory.name) is not None
                and not directory.is_symlink()
                and directory.is_dir()
            )
        except OSError:
            eligible = False
        if not eligible:
            continue
        marker = directory / ".evelyn-owned-lab.json"
        checkpoint = directory / "aggregate-checkpoint.json"
        try:
            marker_raw = marker.read_bytes()
            raw = checkpoint.read_bytes()
            marker_value = json.loads(marker_raw.decode("ascii"))
            value = json.loads(raw.decode("ascii"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            len(marker_raw) > 512
            or len(raw) > 4096
            or marker_value
            != {
                "schema": "evelyn.main-latency-owned-temp.v1",
                "owner": "evelyn-main-latency-lab-v1",
                "runId": run_id,
            }
            or type(value) is not dict
            or set(value) != PROGRESS_FIELDS
            or value.get("schema") != "evelyn.main-latency-progress.v1"
            or value.get("owner") != "evelyn-main-latency-lab-v1"
            or value.get("runId") != run_id
            or value.get("candidateId") != candidate_id
            or value.get("phase") not in {"warm", "soak", "measured"}
        ):
            continue
        integer_fields = PROGRESS_FIELDS - {
            "schema",
            "owner",
            "runId",
            "candidateId",
            "phase",
            "baselineWarmAnswerFirstPcmP50Ms",
            "baselineWarmAnswerFirstPcmP95Ms",
            "candidateWarmAnswerFirstPcmP50Ms",
            "candidateWarmAnswerFirstPcmP95Ms",
        }
        metric_fields = PROGRESS_FIELDS - integer_fields - {
            "schema", "owner", "runId", "candidateId", "phase"
        }
        if any(type(value.get(name)) is not int or value[name] < 0 for name in integer_fields):
            continue
        if any(
            isinstance(value.get(name), bool)
            or not isinstance(value.get(name), (int, float))
            or not math.isfinite(float(value[name]))
            or not 0 <= float(value[name]) <= 30_000
            for name in metric_fields
        ):
            continue
        if newest is None or value["sequence"] > newest["sequence"]:
            newest = value
    return newest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--attempt", type=int, default=2)
    args = parser.parse_args()
    artifact = Path(args.artifact).resolve()
    state: dict[str, Any] = {
        "schema": "evelyn.main-latency-finalist-validation-artifact.v1",
        "artifactPath": str(artifact),
        "attempt": args.attempt,
        "status": "starting",
        "startedAt": utc_now(),
        "priorAttemptArtifact": str(
            artifact.with_name(
                "main_latency_finalist_graph0-vs-graph1_sm120_swa1.attempt1-pacing-aborted.json"
            )
        ),
        "priorWorkloadRunArtifact": str(
            artifact.with_name(
                "main_latency_finalist_graph0-vs-graph1_sm120_swa1.attempt2-workload-complete-receipt-failed.json"
            )
        ),
        "priorPreflightArtifacts": [
            str(
                artifact.with_name(
                    "main_latency_finalist_graph0-vs-graph1_sm120_swa1.attempt2-preflight-blocked.json"
                )
            ),
            str(
                artifact.with_name(
                    "main_latency_finalist_graph0-vs-graph1_sm120_swa1.attempt2-preflight-blocked2.json"
                )
            ),
        ],
        "harnessOptimizations": [
            "warm_prime_and_resident_single_count2_launch",
            "twenty_macro_abba_blocks_with_five_fixed-history-residents_per_leg",
            "restart_ready_reuses_evenly_spaced_activation_primes",
            "bot_only_history_reset_between_resident_pairs",
            "numeric_only_atomic_progress_checkpoint",
            "signed_measurement_never_upgraded_by_later_unsigned_cleanup",
            "codex_independent_scheduled_execution",
            "exact_owned_runner_cleanup_proof",
            "coordinator_signed_host_restoration_proof",
            "docker_lifecycle_restored_before_final_evaluation",
            "three_sample_wddm_baseline_and_restoration_gate",
        ],
    }

    def persist() -> None:
        atomic_json(artifact, state)

    persist()
    exit_code = 1
    progress_stop = threading.Event()
    progress_thread: threading.Thread | None = None
    progress_artifact = artifact.with_name(artifact.stem + ".progress.json")
    authority_journal = artifact.with_name(artifact.stem + ".authority.sqlite3")
    state["authorityJournal"] = str(authority_journal)
    host_lifecycle = MainLatencyHostLifecycle(DOCKER_EXE, NVIDIA_SMI_EXE)
    host_lifecycle_attempted = False
    host_prepared = False
    host_observation = None
    host_restoration_proof = None
    terminal_cleanup = None
    plan = None
    candidate = None
    trust_root = None
    evaluator_capability = None
    lifecycle_capability = None
    measured_receipt = None
    measured_timing_diagnostics: dict[str, Any] = {}
    recovered_receipt = None
    recovered_timing_diagnostics: dict[str, Any] = {}
    recovered_evaluation = None
    with _OwnedLabCampaignLock():
        try:
            host_lifecycle_attempted = True
            host_lifecycle.prepare()
            host_prepared = True
            state["hostLifecyclePrepared"] = True
            persist()
            baseline = MainLatencyConfig(2048, 2048, 256, 8192, 0, 1)
            startup_cleanup_attempts: list[dict[str, Any]] = []
            startup_deadline = time.monotonic() + 900.0
            while True:
                startup_cleanup = reconcile_owned_lab()
                startup_cleanup_attempts.append(startup_cleanup)
                if exact_clean(startup_cleanup):
                    break
                if time.monotonic() >= startup_deadline:
                    state["status"] = "cleanup_required"
                    raise RuntimeError("owned_lab_cleanup_required")
                time.sleep(15.0)
            state["startupCleanup"] = startup_cleanup
            state["startupCleanupAttemptCount"] = len(startup_cleanup_attempts)
            host_lifecycle.verify_measurement_preflight()
            state["measurementPreflightVerified"] = True

            identities = _discover_identities(baseline, state, persist)
            trust_root, runner_capability, evaluator_capability, lifecycle_capability = (
                bootstrap_ephemeral_fixed_coordinator(
                    identities,
                    journal_path=authority_journal,
                )
            )
            candidate = compile_candidate(
                candidate_proposal(
                    identities,
                    baseline,
                    {"main.cudaGraph": 1},
                ),
                trust_root=trust_root,
            )
            plan = build_runner_plan(
                candidate,
                profile="finalist",
                attempt=args.attempt,
                trust_root=trust_root,
            )
            state.update(
                {
                    "status": "running",
                    "runId": plan.run_id,
                    "candidateId": candidate.candidate_id,
                    "plan": plan.to_dict(),
                    "runStartedAt": utc_now(),
                    "progressArtifact": str(progress_artifact),
                }
            )
            persist()

            def monitor_progress() -> None:
                last_sequence = -1
                while not progress_stop.wait(2.0):
                    progress = read_progress(plan.run_id, candidate.candidate_id)
                    if progress is not None and progress["sequence"] > last_sequence:
                        try:
                            atomic_json(
                                progress_artifact,
                                {
                                    "schema": "evelyn.main-latency-progress-artifact.v1",
                                    "observedAt": utc_now(),
                                    "progress": progress,
                                },
                            )
                        except OSError:
                            continue
                        else:
                            last_sequence = progress["sequence"]

            progress_thread = threading.Thread(
                target=monitor_progress,
                name="main-latency-progress-monitor",
                daemon=True,
            )
            progress_thread.start()
            print(
                json.dumps(
                    {
                        "event": "started",
                        "runId": plan.run_id,
                        "candidateId": candidate.candidate_id,
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )

            raw_receipt = FixedSubprocessRunnerTransport(runner_capability)(plan)
            timing_diagnostics = dict(
                getattr(raw_receipt, "timing_diagnostics", {})
            )
            receipt = compile_runner_receipt(
                plan,
                raw_receipt,
                trust_root=trust_root,
            )
            measured_receipt = receipt
            measured_timing_diagnostics = timing_diagnostics
            state.update(
                {
                    "status": "finalizing",
                    "preservedSignedReceipt": receipt.to_dict(),
                    "preservedTimingDiagnostics": dict(timing_diagnostics),
                }
            )
            persist()
        except RunnerTransportError as exc:
            state.update(
                {
                    "status": "runner_failed",
                    "errorCode": exc.code,
                    "diagnosticCode": exc.diagnostic_code,
                    "transportCleanup": dict(exc.cleanup) if exc.cleanup else None,
                }
            )
            if exc.partial_receipt is not None:
                try:
                    recovered_receipt = compile_runner_receipt(
                        plan,
                        dict(exc.partial_receipt),
                        trust_root=trust_root,
                    )
                except Exception:
                    state["errorCode"] = "runner_receipt_invalid"
                else:
                    recovered_timing_diagnostics = dict(
                        exc.partial_timing_diagnostics or {}
                    )
                    state["preservedSignedReceipt"] = recovered_receipt.to_dict()
                    state["preservedTimingDiagnostics"] = recovered_timing_diagnostics
        except Exception as exc:
            state.setdefault("errorCode", type(exc).__name__)
            state.setdefault("errorMessage", str(exc))
            if state.get("status") not in {"cleanup_required", "runner_failed"}:
                state["status"] = "failed"
        finally:
            if progress_thread is not None:
                progress = read_progress(plan.run_id, candidate.candidate_id)
                if progress is not None:
                    try:
                        atomic_json(
                            progress_artifact,
                            {
                                "schema": "evelyn.main-latency-progress-artifact.v1",
                                "observedAt": utc_now(),
                                "progress": progress,
                            },
                        )
                    except OSError:
                        pass
                progress_stop.set()
                progress_thread.join(timeout=5.0)
            if plan is not None and host_prepared:
                try:
                    terminal_cleanup = reconcile_owned_lab()
                    state["terminalCleanup"] = terminal_cleanup
                except Exception as exc:
                    state["terminalCleanupError"] = type(exc).__name__
            final_receipt = measured_receipt or recovered_receipt
            final_timing_diagnostics = (
                measured_timing_diagnostics
                if measured_receipt is not None
                else recovered_timing_diagnostics
            )
            if (
                exact_clean(terminal_cleanup)
                and final_receipt is not None
                and plan is not None
                and candidate is not None
                and trust_root is not None
                and evaluator_capability is not None
                and lifecycle_capability is not None
            ):
                try:
                    host_observation = dict(
                        host_lifecycle.finish_after_owned_cleanup()
                    )
                    state["hostRestorationObservation"] = host_observation
                    host_restoration_proof = issue_host_restoration_proof(
                        plan,
                        final_receipt,
                        host_observation,
                        trust_root=trust_root,
                        lifecycle_capability=lifecycle_capability,
                    )
                    state["hostRestorationProof"] = (
                        host_restoration_proof.to_dict()
                    )
                    evaluation = evaluate_runner_receipt(
                        plan,
                        final_receipt,
                        trust_root=trust_root,
                        evaluator_capability=evaluator_capability,
                        host_restoration_proof=host_restoration_proof,
                    )
                    if recovered_receipt is not None:
                        recovered_evaluation = evaluation
                        exit_code = (
                            0
                            if _apply_recovered_completion(
                                state,
                                recovered_receipt,
                                recovered_evaluation,
                                final_timing_diagnostics,
                                terminal_cleanup,
                                host_restoration_proof,
                                run_id=plan.run_id,
                                candidate_id=candidate.candidate_id,
                            )
                            else 1
                        )
                    else:
                        state.update(
                            {
                                "receipt": final_receipt.to_dict(),
                                "timingDiagnostics": dict(
                                    final_timing_diagnostics
                                ),
                                "evaluation": evaluation.to_dict(),
                            }
                        )
                        eligible = (
                            evaluation.verdict == "eligible"
                            and evaluation.code == "candidate_passed"
                            and evaluation.gate == "passed"
                            and evaluation.host_restoration_proof_id
                            == host_restoration_proof.proof_id
                        )
                        state["status"] = (
                            "completed" if eligible else evaluation.code
                        )
                        exit_code = 0 if eligible else 1
                except Exception as exc:
                    state["hostFinalizationError"] = getattr(
                        exc, "code", type(exc).__name__
                    )
                    state["status"] = "cleanup_required"
                    exit_code = 1
            else:
                if terminal_cleanup is not None and not exact_clean(terminal_cleanup):
                    state["status"] = "cleanup_required"
                exit_code = 1
            if host_lifecycle_attempted:
                restore = dict(host_lifecycle.best_effort_restore())
                state["hostRestore"] = restore
                if restore.get("status") != "clean":
                    state["status"] = "cleanup_required"
                    exit_code = 1
            state["endedAt"] = utc_now()
            state["wallClockMs"] = max(
                1,
                round(
                    (
                        datetime.fromisoformat(state["endedAt"])
                        - datetime.fromisoformat(state["startedAt"])
                    ).total_seconds()
                    * 1000
                ),
            )
            persist()
            if exit_code == 0:
                try:
                    state["offlineVerification"] = verify_completed_artifact(
                        artifact
                    )
                except Exception:
                    state["status"] = "offline_verification_failed"
                    state["errorCode"] = "offline_verification_failed"
                    exit_code = 1
                persist()
            print(
                json.dumps(
                    {
                        "event": "finished",
                        "status": state["status"],
                        "runId": state.get("runId"),
                        "artifact": str(artifact),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
