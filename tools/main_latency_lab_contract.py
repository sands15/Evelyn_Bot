from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence

if __package__:
    from tools.optimize_main_latency import (
        CANDIDATE_ID_PATTERN,
        MAIN_LATENCY_EVALUATOR_ID,
        PROPOSAL_SCHEMA,
        CandidateFeedback,
        CandidateManifest,
        CoordinatorTrustRoot,
        ContractError,
        EvaluatorCapability,
        IdentitySet,
        LifecycleCapability,
        MainLatencyConfig,
        PromotionEvidence,
        RunnerCapability,
        RunnerRunCapability,
        _bootstrap_test_coordinator,
        _capability_matches,
        _issue_promotion_evidence,
        _validated_trust_root,
        compile_candidate,
        compile_feedback,
    )
else:
    from optimize_main_latency import (
        CANDIDATE_ID_PATTERN,
        MAIN_LATENCY_EVALUATOR_ID,
        PROPOSAL_SCHEMA,
        CandidateFeedback,
        CandidateManifest,
        CoordinatorTrustRoot,
        ContractError,
        EvaluatorCapability,
        IdentitySet,
        LifecycleCapability,
        MainLatencyConfig,
        PromotionEvidence,
        RunnerCapability,
        RunnerRunCapability,
        _bootstrap_test_coordinator,
        _capability_matches,
        _issue_promotion_evidence,
        _validated_trust_root,
        compile_candidate,
        compile_feedback,
    )


RUNNER_PLAN_SCHEMA = "evelyn.latency-runner-plan.v2"
RUNNER_PLAN_ID_SCHEMA = "evelyn.latency-runner-plan-id.v2"
RUNNER_RECEIPT_SCHEMA = "evelyn.latency-runner-receipt.v3"
RUNNER_RECEIPT_ID_SCHEMA = "evelyn.latency-runner-receipt-id.v3"
RUNNER_RECEIPT_SIGNATURE_SCHEMA = "evelyn.latency-runner-receipt-signature.v3"
RUNNER_ATTESTATION_SCHEMA = "evelyn.latency-external-runner-attestation.v3"
RUNNER_ATTESTATION_ID_SCHEMA = "evelyn.latency-external-runner-attestation-id.v3"
RUNNER_ATTESTATION_SIGNATURE_SCHEMA = "evelyn.latency-external-runner-attestation-signature.v3"
CLEANUP_SCHEMA = "evelyn.latency-owned-cleanup-proof.v2"
CLEANUP_ID_SCHEMA = "evelyn.latency-owned-cleanup-proof-id.v2"
CLEANUP_SIGNATURE_SCHEMA = "evelyn.latency-owned-cleanup-proof-signature.v2"
HOST_RESTORATION_OBSERVATION_SCHEMA = "evelyn.latency-host-restoration-observation.v1"
HOST_RESTORATION_PROOF_SCHEMA = "evelyn.latency-host-restoration-proof.v1"
HOST_RESTORATION_PROOF_ID_SCHEMA = "evelyn.latency-host-restoration-proof-id.v1"
HOST_RESTORATION_SIGNATURE_SCHEMA = "evelyn.latency-host-restoration-proof-signature.v1"
EVALUATION_SCHEMA = "evelyn.latency-evaluation.v3"
EVALUATION_ID_SCHEMA = "evelyn.latency-evaluation-id.v3"
STATISTICS_SCHEMA = "evelyn.latency-paired-statistics.v1"
RUNNER_CONTRACT_ID = "main-latency-fixed-runner-v3"
EVALUATOR_CONTRACT_ID = MAIN_LATENCY_EVALUATOR_ID
LAB_OWNER = "evelyn-main-latency-lab-v1"
HASH_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)

SAMPLE_FIELDS = (
    "warmBaseline",
    "warmCandidate",
    "restartReadyBaseline",
    "restartReadyCandidate",
    "soakTurns",
    "abbaBlocks",
)
METRIC_FIELDS = (
    "postSttMainWriteP95Ms",
    "rawFirstTokenP95Ms",
    "rawToSafeSpeechP95Ms",
    "safePrefixCommitP95Ms",
    "ttsFirstPcmP95Ms",
    "firstSentenceCommitP50Ms",
    "firstSentenceCommitP95Ms",
    "warmAnswerFirstPcmP50Ms",
    "warmAnswerFirstPcmP95Ms",
    "warmAnswerFirstPcmP99Ms",
    "restartReadyAnswerFirstPcmP95Ms",
    "restartStartupToReadyP95Ms",
    "gpuMinFreeMiB",
)
STATISTIC_FIELDS = (
    "schema",
    "method",
    "bootstrapReplicates",
    "confidenceLevel",
    "warmAnswerFirstPcmP95DeltaCiLowMs",
    "warmAnswerFirstPcmP95DeltaCiHighMs",
    "warmAnswerFirstPcmP95EffectSize",
)
CHECK_FIELDS = (
    "focusedTestFailures",
    "privacyTestFailures",
    "errorCount",
    "oomCount",
    "malformedStreamCount",
    "staleSpeechCount",
    "duplicateSpeechCount",
    "unsafePrefixCount",
    "cacheProofFailures",
    "orderViolations",
    "externalInterferenceSamples",
    "safetyFailures",
    "qualityFailures",
)
LAB_PREFLIGHT_FAILURE_CODES = frozenset(
    {
        "lab_adapter_not_installed",
        "lab_identity_preflight_failed",
        "lab_isolation_preflight_failed",
        "lab_gpu_idle_preflight_failed",
        "lab_baseline_preflight_failed",
        "lab_benchmark_preflight_failed",
    }
)
RUN_STATUSES = frozenset(
    {
        "completed",
        "candidate_failed",
        "runner_failed",
        "environment_drift",
        "timed_out",
        "cancelled",
        "ambiguous",
    }
) | LAB_PREFLIGHT_FAILURE_CODES


def _exact(value: Any, fields: Sequence[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ContractError(code)
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _content_id(schema: str, value: Any) -> str:
    payload = {"schema": schema, "value": value}
    return f"sha256:{hashlib.sha256(_canonical_bytes(payload)).hexdigest()}"


def _bounded_int(value: Any, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContractError(code)
    return value


def _bounded_number(value: Any, minimum: float, maximum: float, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(code)
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ContractError(code) from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ContractError(code)
    return 0.0 if number == 0 else number


@dataclass(frozen=True)
class RunProfile:
    warm_per_condition: int
    restart_ready_per_condition: int
    soak_turns: int
    abba_blocks: int
    max_runtime_ms: int
    max_sample_ms: int
    max_restart_startup_ms: int
    max_host_ram_mib: int
    max_artifact_bytes: int
    max_concurrent_requests: int
    min_gpu_free_mib: int

    def samples_dict(self) -> dict[str, int]:
        return {
            "warmPerCondition": self.warm_per_condition,
            "restartReadyPerCondition": self.restart_ready_per_condition,
            "soakTurns": self.soak_turns,
            "abbaBlocks": self.abba_blocks,
        }

    def bounds_dict(self) -> dict[str, int]:
        return {
            "maxRuntimeMs": self.max_runtime_ms,
            "maxSampleMs": self.max_sample_ms,
            "maxRestartStartupMs": self.max_restart_startup_ms,
            "maxHostRamMiB": self.max_host_ram_mib,
            "maxArtifactBytes": self.max_artifact_bytes,
            "maxConcurrentRequests": self.max_concurrent_requests,
            "minGpuFreeMiB": self.min_gpu_free_mib,
        }


RUN_PROFILES: Mapping[str, RunProfile] = MappingProxyType(
    {
        "screening": RunProfile(30, 5, 0, 15, 3_600_000, 30_000, 900_000, 32_768, 1_048_576, 1, 4096),
        "finalist": RunProfile(200, 30, 1000, 20, 14_400_000, 30_000, 900_000, 32_768, 1_048_576, 1, 4096),
    }
)


def _validated_candidate(
    value: Any,
    trust_root: CoordinatorTrustRoot,
) -> CandidateManifest:
    if not isinstance(value, CandidateManifest):
        raise ContractError("runner_candidate_invalid")
    try:
        proposal = {
            "schema": PROPOSAL_SCHEMA,
            "identities": value.identities.to_dict(),
            "baselineConfig": value.baseline_config.to_dict(),
            "changes": [
                {"key": change.key, "value": change.value} for change in value.changes
            ],
        }
        canonical = compile_candidate(proposal, trust_root=trust_root)
        if canonical.to_dict() != value.to_dict():
            raise ContractError("runner_candidate_invalid")
    except (AttributeError, TypeError, ContractError):
        raise ContractError("runner_candidate_invalid") from None
    return canonical


def _plan_payload(
    candidate: CandidateManifest,
    profile: str,
    attempt: int,
    authority_id: str,
) -> dict[str, Any]:
    spec = RUN_PROFILES[profile]
    return {
        "schema": RUNNER_PLAN_SCHEMA,
        "runnerContract": RUNNER_CONTRACT_ID,
        "evaluatorContract": EVALUATOR_CONTRACT_ID,
        "authorityId": authority_id,
        "candidateId": candidate.candidate_id,
        "identities": candidate.identities.to_dict(),
        "baselineConfig": candidate.baseline_config.to_dict(),
        "candidateConfig": candidate.candidate_config.to_dict(),
        "changes": [change.to_dict() for change in candidate.changes],
        "profile": profile,
        "attempt": attempt,
        "workload": "post_stt_latency_v3",
        "order": "ABBA",
        "isolation": "owned_lab",
        "network": "owned_internal_only_external_egress_disabled",
        "filesystem": "owned_ephemeral_content_free_only",
        "lifecycle": "external_fixed_coordinator_only",
        "samples": spec.samples_dict(),
        "bounds": spec.bounds_dict(),
    }


@dataclass(frozen=True)
class RunnerPlan:
    run_id: str
    authority_id: str
    candidate: CandidateManifest
    profile: str
    attempt: int

    @property
    def spec(self) -> RunProfile:
        return RUN_PROFILES[self.profile]

    def to_dict(self) -> dict[str, Any]:
        result = _plan_payload(
            self.candidate,
            self.profile,
            self.attempt,
            self.authority_id,
        )
        result["runId"] = self.run_id
        return result


def build_runner_plan(
    candidate: CandidateManifest,
    *,
    profile: str,
    attempt: int,
    trust_root: CoordinatorTrustRoot,
) -> RunnerPlan:
    trust_root = _validated_trust_root(trust_root)
    canonical = _validated_candidate(candidate, trust_root)
    if not isinstance(profile, str) or profile not in RUN_PROFILES:
        raise ContractError("runner_profile_invalid")
    attempt = _bounded_int(attempt, 1, 12, "runner_attempt_invalid")
    payload = _plan_payload(canonical, profile, attempt, trust_root.authority_id)
    run_id = _content_id(RUNNER_PLAN_ID_SCHEMA, payload)
    return RunnerPlan(run_id, trust_root.authority_id, canonical, profile, attempt)


def _validated_plan(
    value: Any,
    trust_root: CoordinatorTrustRoot,
) -> RunnerPlan:
    if not isinstance(value, RunnerPlan):
        raise ContractError("runner_plan_invalid")
    try:
        canonical = build_runner_plan(
            value.candidate,
            profile=value.profile,
            attempt=value.attempt,
            trust_root=trust_root,
        )
        if canonical.to_dict() != value.to_dict():
            raise ContractError("runner_plan_invalid")
    except (AttributeError, TypeError, ContractError):
        raise ContractError("runner_plan_invalid") from None
    return canonical


@dataclass(frozen=True)
class SampleCounts:
    warm_baseline: int
    warm_candidate: int
    restart_ready_baseline: int
    restart_ready_candidate: int
    soak_turns: int
    abba_blocks: int

    def to_dict(self) -> dict[str, int]:
        return dict(zip(SAMPLE_FIELDS, (
            self.warm_baseline,
            self.warm_candidate,
            self.restart_ready_baseline,
            self.restart_ready_candidate,
            self.soak_turns,
            self.abba_blocks,
        )))


def _compile_samples(raw: Any, spec: RunProfile) -> SampleCounts:
    values = _exact(raw, SAMPLE_FIELDS, "runner_samples_invalid")
    maxima = (
        spec.warm_per_condition,
        spec.warm_per_condition,
        spec.restart_ready_per_condition,
        spec.restart_ready_per_condition,
        spec.soak_turns,
        spec.abba_blocks,
    )
    parsed = tuple(
        _bounded_int(values[key], 0, maximum, "runner_samples_invalid")
        for key, maximum in zip(SAMPLE_FIELDS, maxima)
    )
    return SampleCounts(*parsed)


@dataclass(frozen=True)
class MetricSnapshot:
    post_stt_main_write_p95_ms: float
    raw_first_token_p95_ms: float
    raw_to_safe_speech_p95_ms: float
    safe_prefix_commit_p95_ms: float
    tts_first_pcm_p95_ms: float
    first_sentence_commit_p50_ms: float
    first_sentence_commit_p95_ms: float
    warm_answer_first_pcm_p50_ms: float
    warm_answer_first_pcm_p95_ms: float
    warm_answer_first_pcm_p99_ms: float
    restart_ready_answer_first_pcm_p95_ms: float
    restart_startup_to_ready_p95_ms: float
    gpu_min_free_mib: float

    def to_dict(self) -> dict[str, float]:
        return dict(zip(METRIC_FIELDS, (
            self.post_stt_main_write_p95_ms,
            self.raw_first_token_p95_ms,
            self.raw_to_safe_speech_p95_ms,
            self.safe_prefix_commit_p95_ms,
            self.tts_first_pcm_p95_ms,
            self.first_sentence_commit_p50_ms,
            self.first_sentence_commit_p95_ms,
            self.warm_answer_first_pcm_p50_ms,
            self.warm_answer_first_pcm_p95_ms,
            self.warm_answer_first_pcm_p99_ms,
            self.restart_ready_answer_first_pcm_p95_ms,
            self.restart_startup_to_ready_p95_ms,
            self.gpu_min_free_mib,
        )))

    def durations_are_positive(self) -> bool:
        values = tuple(self.to_dict().values())[:-1]
        return all(value > 0 for value in values)


def _compile_metrics(raw: Any, spec: RunProfile) -> MetricSnapshot:
    values = _exact(raw, METRIC_FIELDS, "runner_metrics_invalid")
    parsed = [
        _bounded_number(
            values[key],
            0,
            (
                spec.max_restart_startup_ms
                if key == "restartStartupToReadyP95Ms"
                else spec.max_sample_ms
            ),
            "runner_metrics_invalid",
        )
        for key in METRIC_FIELDS[:-1]
    ]
    parsed.append(
        _bounded_number(values[METRIC_FIELDS[-1]], 0, 131_072, "runner_metrics_invalid")
    )
    metrics = MetricSnapshot(*parsed)
    if not (
        metrics.first_sentence_commit_p50_ms
        <= metrics.first_sentence_commit_p95_ms
        and
        metrics.warm_answer_first_pcm_p50_ms
        <= metrics.warm_answer_first_pcm_p95_ms
        <= metrics.warm_answer_first_pcm_p99_ms
    ):
        raise ContractError("runner_metrics_invalid")
    if (
        metrics.safe_prefix_commit_p95_ms
        < max(
            metrics.post_stt_main_write_p95_ms,
            metrics.raw_first_token_p95_ms,
            metrics.raw_to_safe_speech_p95_ms,
        )
        or metrics.warm_answer_first_pcm_p95_ms
        < max(metrics.safe_prefix_commit_p95_ms, metrics.tts_first_pcm_p95_ms)
    ):
        raise ContractError("runner_metrics_invalid")
    return metrics


@dataclass(frozen=True)
class StatisticalEvidence:
    bootstrap_replicates: int
    confidence_level: float
    warm_answer_first_pcm_p95_delta_ci_low_ms: float
    warm_answer_first_pcm_p95_delta_ci_high_ms: float
    warm_answer_first_pcm_p95_effect_size: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STATISTICS_SCHEMA,
            "method": "paired-bootstrap-abba-v1",
            "bootstrapReplicates": self.bootstrap_replicates,
            "confidenceLevel": self.confidence_level,
            "warmAnswerFirstPcmP95DeltaCiLowMs": (
                self.warm_answer_first_pcm_p95_delta_ci_low_ms
            ),
            "warmAnswerFirstPcmP95DeltaCiHighMs": (
                self.warm_answer_first_pcm_p95_delta_ci_high_ms
            ),
            "warmAnswerFirstPcmP95EffectSize": self.warm_answer_first_pcm_p95_effect_size,
        }


def _compile_statistics(
    raw: Any,
    baseline: MetricSnapshot,
    candidate: MetricSnapshot,
) -> StatisticalEvidence:
    values = _exact(raw, STATISTIC_FIELDS, "runner_statistics_invalid")
    if (
        values["schema"] != STATISTICS_SCHEMA
        or values["method"] != "paired-bootstrap-abba-v1"
    ):
        raise ContractError("runner_statistics_invalid")
    evidence = StatisticalEvidence(
        _bounded_int(
            values["bootstrapReplicates"],
            1,
            100_000,
            "runner_statistics_invalid",
        ),
        _bounded_number(
            values["confidenceLevel"],
            0.5,
            0.999,
            "runner_statistics_invalid",
        ),
        _bounded_number(
            values["warmAnswerFirstPcmP95DeltaCiLowMs"],
            -30_000,
            30_000,
            "runner_statistics_invalid",
        ),
        _bounded_number(
            values["warmAnswerFirstPcmP95DeltaCiHighMs"],
            -30_000,
            30_000,
            "runner_statistics_invalid",
        ),
        _bounded_number(
            values["warmAnswerFirstPcmP95EffectSize"],
            -100,
            100,
            "runner_statistics_invalid",
        ),
    )
    point_delta = (
        candidate.warm_answer_first_pcm_p95_ms
        - baseline.warm_answer_first_pcm_p95_ms
    )
    if not (
        evidence.warm_answer_first_pcm_p95_delta_ci_low_ms
        <= point_delta
        <= evidence.warm_answer_first_pcm_p95_delta_ci_high_ms
    ):
        raise ContractError("runner_statistics_invalid")
    if (
        point_delta < 0 <= evidence.warm_answer_first_pcm_p95_effect_size
        or point_delta > 0 >= evidence.warm_answer_first_pcm_p95_effect_size
    ):
        raise ContractError("runner_statistics_invalid")
    return evidence


@dataclass(frozen=True)
class CheckCounts:
    focused_test_failures: int
    privacy_test_failures: int
    error_count: int
    oom_count: int
    malformed_stream_count: int
    stale_speech_count: int
    duplicate_speech_count: int
    unsafe_prefix_count: int
    cache_proof_failures: int
    order_violations: int
    external_interference_samples: int
    safety_failures: int
    quality_failures: int

    def to_dict(self) -> dict[str, int]:
        return dict(zip(CHECK_FIELDS, (
            self.focused_test_failures,
            self.privacy_test_failures,
            self.error_count,
            self.oom_count,
            self.malformed_stream_count,
            self.stale_speech_count,
            self.duplicate_speech_count,
            self.unsafe_prefix_count,
            self.cache_proof_failures,
            self.order_violations,
            self.external_interference_samples,
            self.safety_failures,
            self.quality_failures,
        )))


def _compile_checks(raw: Any) -> CheckCounts:
    values = _exact(raw, CHECK_FIELDS, "runner_checks_invalid")
    parsed = tuple(
        _bounded_int(values[key], 0, 10_000, "runner_checks_invalid")
        for key in CHECK_FIELDS
    )
    return CheckCounts(*parsed)


@dataclass(frozen=True)
class EquivalenceCounts:
    comparisons: int
    matches: int

    def to_dict(self) -> dict[str, int]:
        return {"comparisons": self.comparisons, "matches": self.matches}


def _compile_equivalence(raw: Any, spec: RunProfile) -> EquivalenceCounts:
    values = _exact(raw, ("comparisons", "matches"), "runner_equivalence_invalid")
    comparisons = _bounded_int(
        values["comparisons"], 0, spec.warm_per_condition, "runner_equivalence_invalid"
    )
    matches = _bounded_int(values["matches"], 0, comparisons, "runner_equivalence_invalid")
    return EquivalenceCounts(comparisons, matches)


@dataclass(frozen=True)
class ResourceUsage:
    runtime_ms: int
    artifact_bytes: int
    peak_host_ram_mib: int
    max_concurrent_requests: int

    def to_dict(self) -> dict[str, int]:
        return {
            "runtimeMs": self.runtime_ms,
            "artifactBytes": self.artifact_bytes,
            "peakHostRamMiB": self.peak_host_ram_mib,
            "maxConcurrentRequests": self.max_concurrent_requests,
        }


def _compile_resources(raw: Any, spec: RunProfile) -> ResourceUsage:
    fields = ("runtimeMs", "artifactBytes", "peakHostRamMiB", "maxConcurrentRequests")
    values = _exact(raw, fields, "runner_resources_invalid")
    return ResourceUsage(
        _bounded_int(values["runtimeMs"], 0, spec.max_runtime_ms, "runner_bounds_exceeded"),
        _bounded_int(
            values["artifactBytes"], 0, spec.max_artifact_bytes, "runner_bounds_exceeded"
        ),
        _bounded_int(
            values["peakHostRamMiB"], 0, spec.max_host_ram_mib, "runner_bounds_exceeded"
        ),
        _bounded_int(
            values["maxConcurrentRequests"],
            0,
            spec.max_concurrent_requests,
            "runner_bounds_exceeded",
        ),
    )


@dataclass(frozen=True)
class CleanupProof:
    proof_id: str
    _signature: str = field(repr=False)
    run_id: str
    status: str
    remaining_processes: int
    remaining_gpu_allocations: int
    remaining_artifacts: int

    def input_dict(self) -> dict[str, Any]:
        return {
            "schema": CLEANUP_SCHEMA,
            "runId": self.run_id,
            "owner": LAB_OWNER,
            "status": self.status,
            "remainingProcesses": self.remaining_processes,
            "remainingGpuAllocations": self.remaining_gpu_allocations,
            "remainingArtifacts": self.remaining_artifacts,
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.input_dict()
        result["proofId"] = self.proof_id
        result["signature"] = self._signature
        return result


@dataclass(frozen=True)
class HostRestorationProof:
    proof_id: str
    authority_id: str
    identity_digest: str
    run_id: str
    candidate_id: str
    receipt_id: str
    cleanup_proof_id: str
    status: str
    docker_initial_state: str
    docker_final_state: str
    docker_started_by_run: bool
    driver_model: str
    baseline_free_mib: float
    post_free_min_mib: float
    total_mib: float
    max_utilization_pct: float
    stable_observations: int
    global_running_containers: int
    _signature: str = field(repr=False)

    def input_dict(self) -> dict[str, Any]:
        return {
            "schema": HOST_RESTORATION_PROOF_SCHEMA,
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "runId": self.run_id,
            "candidateId": self.candidate_id,
            "receiptId": self.receipt_id,
            "cleanupProofId": self.cleanup_proof_id,
            "status": self.status,
            "dockerInitialState": self.docker_initial_state,
            "dockerFinalState": self.docker_final_state,
            "dockerStartedByRun": self.docker_started_by_run,
            "driverModel": self.driver_model,
            "baselineFreeMiB": self.baseline_free_mib,
            "postFreeMinMiB": self.post_free_min_mib,
            "totalMiB": self.total_mib,
            "maxUtilizationPct": self.max_utilization_pct,
            "stableObservations": self.stable_observations,
            "globalRunningContainers": self.global_running_containers,
        }

    def signature_payload(self) -> dict[str, Any]:
        return {"proofId": self.proof_id, "hostRestoration": self.input_dict()}

    def to_dict(self) -> dict[str, Any]:
        result = self.input_dict()
        result["proofId"] = self.proof_id
        result["signature"] = self._signature
        return result


_HOST_RESTORATION_OBSERVATION_FIELDS = (
    "schema",
    "status",
    "dockerInitialState",
    "dockerFinalState",
    "dockerStartedByRun",
    "driverModel",
    "baselineFreeMiB",
    "postFreeMinMiB",
    "totalMiB",
    "maxUtilizationPct",
    "stableObservations",
    "globalRunningContainers",
)


def _normalize_host_restoration_observation(raw: Any) -> dict[str, Any]:
    values = _exact(
        raw,
        _HOST_RESTORATION_OBSERVATION_FIELDS,
        "host_restoration_observation_invalid",
    )
    status = values["status"]
    initial = values["dockerInitialState"]
    final = values["dockerFinalState"]
    started_by_run = values["dockerStartedByRun"]
    model = values["driverModel"]
    if (
        values["schema"] != HOST_RESTORATION_OBSERVATION_SCHEMA
        or status not in {"clean", "restoration_required"}
        or initial not in {"running", "stopped", "unknown"}
        or final not in {"running", "stopped", "unknown"}
        or type(started_by_run) is not bool
        or not isinstance(model, str)
        or model.casefold() not in {"wddm", "tcc"}
    ):
        raise ContractError("host_restoration_observation_invalid")
    baseline_free = _bounded_number(
        values["baselineFreeMiB"], 0.0, 1_048_576.0, "host_restoration_observation_invalid"
    )
    post_free = _bounded_number(
        values["postFreeMinMiB"], 0.0, 1_048_576.0, "host_restoration_observation_invalid"
    )
    total = _bounded_number(
        values["totalMiB"], 1.0, 1_048_576.0, "host_restoration_observation_invalid"
    )
    max_utilization = _bounded_number(
        values["maxUtilizationPct"], 0.0, 100.0, "host_restoration_observation_invalid"
    )
    stable = _bounded_int(
        values["stableObservations"], 0, 64, "host_restoration_observation_invalid"
    )
    running = _bounded_int(
        values["globalRunningContainers"], 0, 64, "host_restoration_observation_invalid"
    )
    normalized = {
        "schema": HOST_RESTORATION_OBSERVATION_SCHEMA,
        "status": status,
        "dockerInitialState": initial,
        "dockerFinalState": final,
        "dockerStartedByRun": started_by_run,
        "driverModel": model.casefold(),
        "baselineFreeMiB": baseline_free,
        "postFreeMinMiB": post_free,
        "totalMiB": total,
        "maxUtilizationPct": max_utilization,
        "stableObservations": stable,
        "globalRunningContainers": running,
    }
    clean = (
        status == "clean"
        and initial in {"running", "stopped"}
        and final == initial
        and started_by_run == (initial == "stopped")
        and baseline_free <= total
        and post_free <= total
        and post_free >= baseline_free - 256.0
        and max_utilization <= 10.0
        and stable >= 3
        and running == 0
    )
    if status == "clean" and not clean:
        raise ContractError("host_restoration_observation_invalid")
    return normalized


def _host_restoration_from(
    plan: RunnerPlan,
    receipt: RunnerReceipt,
    observation: Mapping[str, Any],
    *,
    trust_root: CoordinatorTrustRoot,
    proof_id: str = "",
    signature: str = "",
) -> HostRestorationProof:
    return HostRestorationProof(
        proof_id,
        trust_root.authority_id,
        trust_root.identity_digest,
        plan.run_id,
        receipt.candidate_id,
        receipt.receipt_id,
        receipt.cleanup.proof_id,
        observation["status"],
        observation["dockerInitialState"],
        observation["dockerFinalState"],
        observation["dockerStartedByRun"],
        observation["driverModel"],
        observation["baselineFreeMiB"],
        observation["postFreeMinMiB"],
        observation["totalMiB"],
        observation["maxUtilizationPct"],
        observation["stableObservations"],
        observation["globalRunningContainers"],
        signature,
    )


def issue_host_restoration_proof(
    plan: RunnerPlan,
    receipt: RunnerReceipt,
    observation: Any,
    *,
    trust_root: CoordinatorTrustRoot,
    lifecycle_capability: LifecycleCapability,
) -> HostRestorationProof:
    trust_root = _validated_trust_root(trust_root)
    plan = _validated_plan(plan, trust_root)
    receipt = _validated_receipt(plan, receipt, trust_root)
    if not _capability_matches(
        trust_root,
        lifecycle_capability,
        LifecycleCapability,
        "_verify_lifecycle",
    ):
        raise ContractError("lifecycle_capability_invalid")
    normalized = _normalize_host_restoration_observation(observation)
    pending = _host_restoration_from(
        plan, receipt, normalized, trust_root=trust_root
    )
    proof_id = _content_id(HOST_RESTORATION_PROOF_ID_SCHEMA, pending.input_dict())
    signed = replace(pending, proof_id=proof_id)
    return replace(
        signed,
        _signature=lifecycle_capability._sign_for(
            HOST_RESTORATION_SIGNATURE_SCHEMA,
            signed.signature_payload(),
        ),
    )


def compile_host_restoration_proof(
    plan: RunnerPlan,
    receipt: RunnerReceipt,
    raw: Any,
    *,
    trust_root: CoordinatorTrustRoot,
) -> HostRestorationProof:
    trust_root = _validated_trust_root(trust_root)
    plan = _validated_plan(plan, trust_root)
    receipt = _validated_receipt(plan, receipt, trust_root)
    required = {
        "authorityId",
        "identityDigest",
        "runId",
        "candidateId",
        "receiptId",
        "cleanupProofId",
        "proofId",
        "signature",
        *(_HOST_RESTORATION_OBSERVATION_FIELDS),
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ContractError("host_restoration_proof_invalid")
    if (
        raw["schema"] != HOST_RESTORATION_PROOF_SCHEMA
        or raw["authorityId"] != trust_root.authority_id
        or raw["identityDigest"] != trust_root.identity_digest
        or raw["runId"] != plan.run_id
        or raw["candidateId"] != receipt.candidate_id
        or raw["receiptId"] != receipt.receipt_id
        or raw["cleanupProofId"] != receipt.cleanup.proof_id
    ):
        raise ContractError("host_restoration_proof_invalid")
    observation = dict(raw)
    observation["schema"] = HOST_RESTORATION_OBSERVATION_SCHEMA
    for key in required - set(_HOST_RESTORATION_OBSERVATION_FIELDS):
        observation.pop(key, None)
    try:
        normalized = _normalize_host_restoration_observation(observation)
    except ContractError:
        raise ContractError("host_restoration_proof_invalid") from None
    pending = _host_restoration_from(
        plan, receipt, normalized, trust_root=trust_root
    )
    expected_id = _content_id(
        HOST_RESTORATION_PROOF_ID_SCHEMA, pending.input_dict()
    )
    candidate = replace(
        pending,
        proof_id=expected_id,
        _signature=raw["signature"] if isinstance(raw["signature"], str) else "",
    )
    if (
        raw["proofId"] != expected_id
        or not trust_root._verify_lifecycle(
            HOST_RESTORATION_SIGNATURE_SCHEMA,
            candidate.signature_payload(),
            candidate._signature,
        )
    ):
        raise ContractError("host_restoration_proof_invalid")
    return candidate


def _validated_host_restoration_proof(
    plan: RunnerPlan,
    receipt: RunnerReceipt,
    value: Any,
    trust_root: CoordinatorTrustRoot,
) -> HostRestorationProof:
    if not isinstance(value, HostRestorationProof):
        raise ContractError("host_restoration_proof_invalid")
    canonical = compile_host_restoration_proof(
        plan,
        receipt,
        value.to_dict(),
        trust_root=trust_root,
    )
    if canonical.to_dict() != value.to_dict():
        raise ContractError("host_restoration_proof_invalid")
    return canonical


@dataclass(frozen=True)
class RunnerAttestation:
    attestation_id: str
    authority_id: str
    identity_digest: str
    run_id: str
    candidate_id: str
    receipt_id: str
    cleanup_proof_id: str
    _signature: str = field(repr=False)

    def input_dict(self) -> dict[str, str]:
        return {
            "schema": RUNNER_ATTESTATION_SCHEMA,
            "runnerContract": RUNNER_CONTRACT_ID,
            "authorityId": self.authority_id,
            "identityDigest": self.identity_digest,
            "runId": self.run_id,
            "candidateId": self.candidate_id,
            "receiptId": self.receipt_id,
            "cleanupProofId": self.cleanup_proof_id,
            "provenance": "external-fixed-runner-v3",
        }

    def signature_payload(self) -> dict[str, Any]:
        return {
            "attestationId": self.attestation_id,
            "attestation": self.input_dict(),
        }

    def to_dict(self) -> dict[str, str]:
        result = self.input_dict()
        result["attestationId"] = self.attestation_id
        result["signature"] = self._signature
        return result


def _compile_cleanup(
    raw: Any,
    run_id: str,
    *,
    trust_root: CoordinatorTrustRoot,
    runner_capability: RunnerCapability | RunnerRunCapability | None,
    require_signature: bool,
) -> CleanupProof:
    fields = (
        "schema",
        "runId",
        "owner",
        "status",
        "remainingProcesses",
        "remainingGpuAllocations",
        "remainingArtifacts",
    )
    required_fields = set(fields) | ({"proofId", "signature"} if require_signature else set())
    if not isinstance(raw, dict) or set(raw) != required_fields:
        raise ContractError("cleanup_proof_invalid")
    values = raw
    if (
        values["schema"] != CLEANUP_SCHEMA
        or values["runId"] != run_id
        or values["owner"] != LAB_OWNER
        or not isinstance(values["status"], str)
        or values["status"] not in {"clean", "cleanup_required"}
    ):
        raise ContractError("cleanup_proof_invalid")
    counts = (
        _bounded_int(values["remainingProcesses"], 0, 8, "cleanup_proof_invalid"),
        _bounded_int(values["remainingGpuAllocations"], 0, 4, "cleanup_proof_invalid"),
        _bounded_int(values["remainingArtifacts"], 0, 64, "cleanup_proof_invalid"),
    )
    if values["status"] == "clean" and any(counts):
        raise ContractError("cleanup_proof_invalid")
    normalized = {
        "schema": CLEANUP_SCHEMA,
        "runId": run_id,
        "owner": LAB_OWNER,
        "status": values["status"],
        "remainingProcesses": counts[0],
        "remainingGpuAllocations": counts[1],
        "remainingArtifacts": counts[2],
    }
    proof_id = _content_id(CLEANUP_ID_SCHEMA, normalized)
    signature_payload = {"proofId": proof_id, "cleanup": normalized}
    if require_signature:
        if (
            values["proofId"] != proof_id
            or not trust_root._verify_runner(
                CLEANUP_SIGNATURE_SCHEMA,
                signature_payload,
                values["signature"],
            )
        ):
            raise ContractError("cleanup_proof_invalid")
        signature = values["signature"]
    else:
        if not isinstance(runner_capability, (RunnerCapability, RunnerRunCapability)):
            raise ContractError("runner_capability_invalid")
        signature = runner_capability._sign_for(
            CLEANUP_SIGNATURE_SCHEMA,
            signature_payload,
        )
    return CleanupProof(proof_id, signature, run_id, values["status"], *counts)


@dataclass(frozen=True)
class RunnerReceipt:
    receipt_id: str
    _signature: str = field(repr=False)
    run_id: str
    candidate_id: str
    identities: IdentitySet
    baseline_config: MainLatencyConfig
    candidate_config: MainLatencyConfig
    status: str
    samples: SampleCounts
    baseline_metrics: MetricSnapshot
    candidate_metrics: MetricSnapshot
    statistics: StatisticalEvidence
    checks: CheckCounts
    equivalence: EquivalenceCounts
    resources: ResourceUsage
    cleanup: CleanupProof
    runner_attestation: RunnerAttestation

    def input_dict(self) -> dict[str, Any]:
        return {
            "schema": RUNNER_RECEIPT_SCHEMA,
            "runId": self.run_id,
            "candidateId": self.candidate_id,
            "identities": self.identities.to_dict(),
            "baselineConfig": self.baseline_config.to_dict(),
            "candidateConfig": self.candidate_config.to_dict(),
            "status": self.status,
            "samples": self.samples.to_dict(),
            "baselineMetrics": self.baseline_metrics.to_dict(),
            "candidateMetrics": self.candidate_metrics.to_dict(),
            "statistics": self.statistics.to_dict(),
            "checks": self.checks.to_dict(),
            "equivalence": self.equivalence.to_dict(),
            "resources": self.resources.to_dict(),
            "cleanup": self.cleanup.input_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        result = self.input_dict()
        result["receiptId"] = self.receipt_id
        result["signature"] = self._signature
        result["cleanup"] = self.cleanup.to_dict()
        result["runnerAttestation"] = self.runner_attestation.to_dict()
        return result


def _compile_runner_attestation(
    raw: Any,
    *,
    plan: RunnerPlan,
    trust_root: CoordinatorTrustRoot,
    runner_capability: RunnerCapability | RunnerRunCapability | None,
    receipt_id: str,
    cleanup_proof_id: str,
    require_signature: bool,
) -> RunnerAttestation:
    attestation = RunnerAttestation(
        "",
        trust_root.authority_id,
        trust_root.identity_digest,
        plan.run_id,
        plan.candidate.candidate_id,
        receipt_id,
        cleanup_proof_id,
        "",
    )
    attestation_id = _content_id(RUNNER_ATTESTATION_ID_SCHEMA, attestation.input_dict())
    attestation = replace(attestation, attestation_id=attestation_id)
    if require_signature:
        expected_fields = set(attestation.input_dict()) | {"attestationId", "signature"}
        if (
            not isinstance(raw, dict)
            or set(raw) != expected_fields
            or any(raw[key] != value for key, value in attestation.input_dict().items())
            or raw["attestationId"] != attestation_id
            or not trust_root._verify_runner(
                RUNNER_ATTESTATION_SIGNATURE_SCHEMA,
                attestation.signature_payload(),
                raw["signature"],
            )
        ):
            raise ContractError("runner_attestation_invalid")
        return replace(attestation, _signature=raw["signature"])
    if not isinstance(runner_capability, (RunnerCapability, RunnerRunCapability)):
        raise ContractError("runner_capability_invalid")
    return replace(
        attestation,
        _signature=runner_capability._sign_for(
            RUNNER_ATTESTATION_SIGNATURE_SCHEMA,
            attestation.signature_payload(),
        ),
    )


def _compile_runner_receipt(
    plan: RunnerPlan,
    raw: Any,
    *,
    trust_root: CoordinatorTrustRoot,
    runner_capability: RunnerCapability | RunnerRunCapability | None,
    require_signature: bool,
) -> RunnerReceipt:
    trust_root = _validated_trust_root(trust_root)
    plan = _validated_plan(plan, trust_root)
    if not require_signature:
        master_valid = _capability_matches(
            trust_root,
            runner_capability,
            RunnerCapability,
            "_verify_runner",
        )
        run_valid = False
        if isinstance(runner_capability, RunnerRunCapability):
            challenge = {
                "authorityId": trust_root.authority_id,
                "identityDigest": trust_root.identity_digest,
                "receipt": {"runId": plan.run_id},
            }
            run_valid = (
                runner_capability.authority_id == trust_root.authority_id
                and runner_capability.identity_digest == trust_root.identity_digest
                and runner_capability.run_id == plan.run_id
                and trust_root._verify_runner(
                    "evelyn.runner-one-run-challenge.v1",
                    challenge,
                    runner_capability._sign_for(
                        "evelyn.runner-one-run-challenge.v1",
                        challenge,
                    ),
                )
            )
        if not (master_valid or run_valid):
            raise ContractError("runner_capability_invalid")
        if isinstance(runner_capability, RunnerRunCapability):
            runner_capability._claim_receipt(plan.run_id)
    fields = (
        "schema",
        "runId",
        "candidateId",
        "identities",
        "baselineConfig",
        "candidateConfig",
        "status",
        "samples",
        "baselineMetrics",
        "candidateMetrics",
        "statistics",
        "checks",
        "equivalence",
        "resources",
        "cleanup",
    )
    required_fields = set(fields) | (
        {"receiptId", "signature", "runnerAttestation"}
        if require_signature
        else set()
    )
    if not isinstance(raw, dict) or set(raw) != required_fields:
        raise ContractError("runner_receipt_fields_invalid")
    values = raw
    if values["schema"] != RUNNER_RECEIPT_SCHEMA:
        raise ContractError("runner_receipt_schema_invalid")
    if values["runId"] != plan.run_id or values["candidateId"] != plan.candidate.candidate_id:
        raise ContractError("runner_receipt_binding_invalid")
    if (
        not isinstance(values["candidateId"], str)
        or CANDIDATE_ID_PATTERN.fullmatch(values["candidateId"]) is None
        or HASH_ID_PATTERN.fullmatch(values["runId"]) is None
    ):
        raise ContractError("runner_receipt_binding_invalid")
    identities = IdentitySet.from_mapping(values["identities"])
    baseline = MainLatencyConfig.from_mapping(values["baselineConfig"])
    candidate = MainLatencyConfig.from_mapping(values["candidateConfig"])
    if (
        identities != plan.candidate.identities
        or baseline != plan.candidate.baseline_config
        or candidate != plan.candidate.candidate_config
    ):
        raise ContractError("runner_receipt_binding_invalid")
    status = values["status"]
    if not isinstance(status, str) or status not in RUN_STATUSES:
        raise ContractError("runner_status_invalid")
    samples = _compile_samples(values["samples"], plan.spec)
    baseline_metrics = _compile_metrics(values["baselineMetrics"], plan.spec)
    candidate_metrics = _compile_metrics(values["candidateMetrics"], plan.spec)
    statistics = _compile_statistics(
        values["statistics"],
        baseline_metrics,
        candidate_metrics,
    )
    checks = _compile_checks(values["checks"])
    equivalence = _compile_equivalence(values["equivalence"], plan.spec)
    resources = _compile_resources(values["resources"], plan.spec)
    cleanup = _compile_cleanup(
        values["cleanup"],
        plan.run_id,
        trust_root=trust_root,
        runner_capability=runner_capability,
        require_signature=require_signature,
    )
    pending_attestation = RunnerAttestation(
        "",
        trust_root.authority_id,
        trust_root.identity_digest,
        plan.run_id,
        plan.candidate.candidate_id,
        "",
        cleanup.proof_id,
        "",
    )
    receipt = RunnerReceipt(
        "",
        "",
        plan.run_id,
        plan.candidate.candidate_id,
        identities,
        baseline,
        candidate,
        status,
        samples,
        baseline_metrics,
        candidate_metrics,
        statistics,
        checks,
        equivalence,
        resources,
        cleanup,
        pending_attestation,
    )
    receipt_id = _content_id(RUNNER_RECEIPT_ID_SCHEMA, receipt.input_dict())
    signature_payload = {
        "receiptId": receipt_id,
        "cleanupProofId": cleanup.proof_id,
        "receipt": receipt.input_dict(),
    }
    if require_signature:
        if (
            values["receiptId"] != receipt_id
            or not trust_root._verify_runner(
                RUNNER_RECEIPT_SIGNATURE_SCHEMA,
                signature_payload,
                values["signature"],
            )
        ):
            raise ContractError("runner_receipt_auth_invalid")
        signature = values["signature"]
    else:
        signature = runner_capability._sign_for(
            RUNNER_RECEIPT_SIGNATURE_SCHEMA,
            signature_payload,
        )
    attestation = _compile_runner_attestation(
        values.get("runnerAttestation"),
        plan=plan,
        trust_root=trust_root,
        runner_capability=runner_capability,
        receipt_id=receipt_id,
        cleanup_proof_id=cleanup.proof_id,
        require_signature=require_signature,
    )
    return replace(
        receipt,
        receipt_id=receipt_id,
        _signature=signature,
        runner_attestation=attestation,
    )


def issue_runner_receipt(
    plan: RunnerPlan,
    raw: Any,
    *,
    trust_root: CoordinatorTrustRoot,
    runner_capability: RunnerCapability | RunnerRunCapability,
) -> RunnerReceipt:
    return _compile_runner_receipt(
        plan,
        raw,
        trust_root=trust_root,
        runner_capability=runner_capability,
        require_signature=False,
    )


def compile_runner_receipt(
    plan: RunnerPlan,
    raw: Any,
    *,
    trust_root: CoordinatorTrustRoot,
) -> RunnerReceipt:
    return _compile_runner_receipt(
        plan,
        raw,
        trust_root=trust_root,
        runner_capability=None,
        require_signature=True,
    )


def _validated_receipt(
    plan: RunnerPlan,
    value: Any,
    trust_root: CoordinatorTrustRoot,
) -> RunnerReceipt:
    if not isinstance(value, RunnerReceipt):
        raise ContractError("runner_receipt_invalid")
    try:
        canonical = compile_runner_receipt(
            plan,
            value.to_dict(),
            trust_root=trust_root,
        )
        if canonical.to_dict() != value.to_dict():
            raise ContractError("runner_receipt_invalid")
    except (AttributeError, TypeError, ContractError):
        raise ContractError("runner_receipt_invalid") from None
    return canonical


@dataclass(frozen=True)
class EvaluationDecision:
    evaluation_id: str
    run_id: str
    receipt_id: str
    candidate_id: str
    identities: IdentitySet
    verdict: str
    code: str
    gate: str
    deltas: Mapping[str, float]
    cleanup_proof_id: str
    host_restoration_proof_id: str | None
    promotion_feedback: CandidateFeedback | None
    promotion_evidence: PromotionEvidence | None = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EVALUATION_SCHEMA,
            "evaluationId": self.evaluation_id,
            "evaluatorContract": EVALUATOR_CONTRACT_ID,
            "runId": self.run_id,
            "receiptId": self.receipt_id,
            "candidateId": self.candidate_id,
            "identities": self.identities.to_dict(),
            "verdict": self.verdict,
            "code": self.code,
            "gate": self.gate,
            "deltas": dict(self.deltas),
            "cleanupProofId": self.cleanup_proof_id,
            "hostRestorationProofId": self.host_restoration_proof_id,
            "promotionFeedback": (
                self.promotion_feedback.to_dict() if self.promotion_feedback else None
            ),
        }


def _deltas(receipt: RunnerReceipt) -> Mapping[str, float]:
    baseline = receipt.baseline_metrics
    candidate = receipt.candidate_metrics
    return MappingProxyType(
        {
            "firstSentenceP50DeltaMs": (
                candidate.first_sentence_commit_p50_ms
                - baseline.first_sentence_commit_p50_ms
            ),
            "firstSentenceP95DeltaMs": (
                candidate.first_sentence_commit_p95_ms
                - baseline.first_sentence_commit_p95_ms
            ),
            "postSttFirstPcmP50DeltaMs": (
                candidate.warm_answer_first_pcm_p50_ms
                - baseline.warm_answer_first_pcm_p50_ms
            ),
            "postSttFirstPcmP95DeltaMs": (
                candidate.warm_answer_first_pcm_p95_ms
                - baseline.warm_answer_first_pcm_p95_ms
            ),
            "postSttFirstPcmP99DeltaMs": (
                candidate.warm_answer_first_pcm_p99_ms
                - baseline.warm_answer_first_pcm_p99_ms
            ),
            "restartReadyFirstPcmP95DeltaMs": (
                candidate.restart_ready_answer_first_pcm_p95_ms
                - baseline.restart_ready_answer_first_pcm_p95_ms
            ),
            "restartStartupToReadyP95DeltaMs": (
                candidate.restart_startup_to_ready_p95_ms
                - baseline.restart_startup_to_ready_p95_ms
            ),
            "gpuMinFreeDeltaMiB": candidate.gpu_min_free_mib - baseline.gpu_min_free_mib,
        }
    )


def _decision(
    plan: RunnerPlan,
    receipt: RunnerReceipt,
    *,
    verdict: str,
    code: str,
    gate: str,
    evaluator_capability: EvaluatorCapability | None = None,
    host_restoration_proof: HostRestorationProof | None = None,
) -> EvaluationDecision:
    deltas = _deltas(receipt)
    promotion_feedback = None
    if verdict == "eligible":
        if not isinstance(evaluator_capability, EvaluatorCapability):
            raise ContractError("evaluator_capability_invalid")
        promotion_feedback = compile_feedback(
            {
                "schema": "evelyn.latency-feedback.v1",
                "candidateId": receipt.candidate_id,
                "attempt": plan.attempt,
                "verdict": "eligible",
                "codes": ["candidate_passed"],
                "metrics": dict(deltas),
            }
        )
    payload = {
        "schema": EVALUATION_SCHEMA,
        "evaluatorContract": EVALUATOR_CONTRACT_ID,
        "runId": receipt.run_id,
        "receiptId": receipt.receipt_id,
        "candidateId": receipt.candidate_id,
        "identities": receipt.identities.to_dict(),
        "verdict": verdict,
        "code": code,
        "gate": gate,
        "deltas": dict(deltas),
        "cleanupProofId": receipt.cleanup.proof_id,
        "hostRestorationProofId": (
            host_restoration_proof.proof_id if host_restoration_proof else None
        ),
        "promotionFeedback": promotion_feedback.to_dict() if promotion_feedback else None,
    }
    evaluation_id = _content_id(EVALUATION_ID_SCHEMA, payload)
    promotion_evidence = None
    if promotion_feedback is not None:
        promotion_evidence = _issue_promotion_evidence(
            evaluator_capability,
            feedback=promotion_feedback,
            run_id=receipt.run_id,
            receipt_id=receipt.receipt_id,
            cleanup_proof_id=receipt.cleanup.proof_id,
            evaluation_id=evaluation_id,
        )
    return EvaluationDecision(
        evaluation_id,
        receipt.run_id,
        receipt.receipt_id,
        receipt.candidate_id,
        receipt.identities,
        verdict,
        code,
        gate,
        deltas,
        receipt.cleanup.proof_id,
        host_restoration_proof.proof_id if host_restoration_proof else None,
        promotion_feedback,
        promotion_evidence,
    )


def _samples_complete(samples: SampleCounts, spec: RunProfile) -> bool:
    return samples == SampleCounts(
        spec.warm_per_condition,
        spec.warm_per_condition,
        spec.restart_ready_per_condition,
        spec.restart_ready_per_condition,
        spec.soak_turns,
        spec.abba_blocks,
    )


def _slo_met(metrics: MetricSnapshot) -> bool:
    return (
        metrics.post_stt_main_write_p95_ms <= 100
        and metrics.raw_first_token_p95_ms <= 350
        and metrics.raw_to_safe_speech_p95_ms <= 80
        and metrics.safe_prefix_commit_p95_ms <= 500
        and metrics.tts_first_pcm_p95_ms <= 220
        and metrics.warm_answer_first_pcm_p50_ms <= 600
        and metrics.warm_answer_first_pcm_p95_ms <= 750
        and metrics.warm_answer_first_pcm_p99_ms <= 900
        and metrics.restart_ready_answer_first_pcm_p95_ms <= 1200
    )


def _latency_regressed(baseline: MetricSnapshot, candidate: MetricSnapshot) -> bool:
    return (
        candidate.post_stt_main_write_p95_ms
        > baseline.post_stt_main_write_p95_ms * 1.10
        or candidate.tts_first_pcm_p95_ms
        > baseline.tts_first_pcm_p95_ms * 1.10
        or candidate.first_sentence_commit_p50_ms
        > baseline.first_sentence_commit_p50_ms * 1.10
        or candidate.first_sentence_commit_p95_ms
        > baseline.first_sentence_commit_p95_ms * 1.10
        or candidate.warm_answer_first_pcm_p50_ms
        > baseline.warm_answer_first_pcm_p50_ms * 1.02
        or candidate.warm_answer_first_pcm_p99_ms
        > baseline.warm_answer_first_pcm_p99_ms * 1.10
        or candidate.restart_ready_answer_first_pcm_p95_ms
        > baseline.restart_ready_answer_first_pcm_p95_ms * 1.10
        or candidate.restart_startup_to_ready_p95_ms
        > baseline.restart_startup_to_ready_p95_ms * 1.10
        or candidate.raw_first_token_p95_ms > baseline.raw_first_token_p95_ms * 1.10
        or candidate.raw_to_safe_speech_p95_ms
        > baseline.raw_to_safe_speech_p95_ms * 1.10
        or candidate.safe_prefix_commit_p95_ms
        > baseline.safe_prefix_commit_p95_ms * 1.10
    )


def evaluate_runner_receipt(
    plan: RunnerPlan,
    receipt: RunnerReceipt,
    *,
    trust_root: CoordinatorTrustRoot,
    evaluator_capability: EvaluatorCapability,
    host_restoration_proof: HostRestorationProof | None = None,
) -> EvaluationDecision:
    trust_root = _validated_trust_root(trust_root)
    plan = _validated_plan(plan, trust_root)
    if not _capability_matches(
        trust_root,
        evaluator_capability,
        EvaluatorCapability,
        "_verify_evaluator",
    ):
        raise ContractError("evaluator_capability_invalid")
    receipt = _validated_receipt(plan, receipt, trust_root)
    if host_restoration_proof is not None:
        host_restoration_proof = _validated_host_restoration_proof(
            plan,
            receipt,
            host_restoration_proof,
            trust_root,
        )
    if receipt.cleanup.status != "clean":
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code="cleanup_required",
            gate="cleanup",
        )
    if receipt.status == "environment_drift":
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code="environment_drift",
            gate="reliability",
        )
    if receipt.status in {"runner_failed", "timed_out", "cancelled", "ambiguous"}:
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code={
                "runner_failed": "runner_failed",
                "timed_out": "runner_timed_out",
                "cancelled": "runner_cancelled",
                "ambiguous": "runner_outcome_ambiguous",
            }[receipt.status],
            gate="reliability",
        )
    if receipt.status in LAB_PREFLIGHT_FAILURE_CODES:
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code=receipt.status,
            gate="reliability",
        )
    if receipt.status == "candidate_failed":
        return _decision(
            plan,
            receipt,
            verdict="rejected",
            code="candidate_failed",
            gate="reliability",
        )
    if (
        not _samples_complete(receipt.samples, plan.spec)
        or not receipt.baseline_metrics.durations_are_positive()
        or not receipt.candidate_metrics.durations_are_positive()
        or receipt.resources.runtime_ms == 0
        or receipt.resources.peak_host_ram_mib == 0
        or receipt.resources.max_concurrent_requests != 1
    ):
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code="insufficient_samples",
            gate="reliability",
        )
    checks = receipt.checks
    if checks.external_interference_samples:
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code="environment_drift",
            gate="reliability",
        )
    if checks.order_violations:
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code="insufficient_samples",
            gate="reliability",
        )
    reliability_values = (
        checks.focused_test_failures,
        checks.privacy_test_failures,
        checks.error_count,
        checks.oom_count,
        checks.malformed_stream_count,
        checks.stale_speech_count,
        checks.duplicate_speech_count,
        checks.cache_proof_failures,
    )
    if any(reliability_values):
        return _decision(
            plan,
            receipt,
            verdict="rejected",
            code="reliability_failed",
            gate="reliability",
        )
    if checks.unsafe_prefix_count or checks.safety_failures:
        return _decision(
            plan,
            receipt,
            verdict="rejected",
            code="safety_failed",
            gate="safety",
        )
    if checks.quality_failures:
        return _decision(
            plan,
            receipt,
            verdict="rejected",
            code="quality_regressed",
            gate="quality",
        )
    if (
        receipt.equivalence.comparisons != plan.spec.warm_per_condition
        or receipt.equivalence.matches != receipt.equivalence.comparisons
    ):
        return _decision(
            plan,
            receipt,
            verdict="inconclusive",
            code="quality_review_required",
            gate="quality",
        )
    if (
        receipt.baseline_metrics.gpu_min_free_mib < plan.spec.min_gpu_free_mib
        or receipt.candidate_metrics.gpu_min_free_mib < plan.spec.min_gpu_free_mib
    ):
        return _decision(
            plan,
            receipt,
            verdict="rejected",
            code="resource_failed",
            gate="resource",
        )
    baseline = receipt.baseline_metrics
    candidate = receipt.candidate_metrics
    if _latency_regressed(baseline, candidate):
        return _decision(
            plan,
            receipt,
            verdict="rejected",
            code="latency_regressed",
            gate="latency",
        )
    improved = (
        candidate.warm_answer_first_pcm_p95_ms
        <= baseline.warm_answer_first_pcm_p95_ms * 0.95
    )
    slo_met = _slo_met(candidate)
    slo_crossed = slo_met and not _slo_met(baseline)
    if slo_met and (improved or slo_crossed):
        if plan.profile != "finalist":
            return _decision(
                plan,
                receipt,
                verdict="frontier",
                code="screening_passed",
                gate="latency",
            )
        statistics = receipt.statistics
        if not (
            statistics.bootstrap_replicates >= 2_000
            and statistics.confidence_level >= 0.95
            and statistics.warm_answer_first_pcm_p95_delta_ci_high_ms < 0
            and statistics.warm_answer_first_pcm_p95_effect_size <= -0.2
        ):
            return _decision(
                plan,
                receipt,
                verdict="frontier",
                code="statistical_evidence_insufficient",
                gate="statistics",
            )
        if (
            host_restoration_proof is None
            or host_restoration_proof.status != "clean"
        ):
            return _decision(
                plan,
                receipt,
                verdict="inconclusive",
                code="host_restoration_required",
                gate="cleanup",
                host_restoration_proof=host_restoration_proof,
            )
        return _decision(
            plan,
            receipt,
            verdict="eligible",
            code="candidate_passed",
            gate="passed",
            evaluator_capability=evaluator_capability,
            host_restoration_proof=host_restoration_proof,
        )
    if candidate.warm_answer_first_pcm_p95_ms < baseline.warm_answer_first_pcm_p95_ms:
        return _decision(
            plan,
            receipt,
            verdict="frontier",
            code="frontier_improved",
            gate="latency",
        )
    return _decision(
        plan,
        receipt,
        verdict="rejected",
        code="no_material_improvement",
        gate="latency",
    )


def self_test() -> None:
    identities = IdentitySet(*(f"sha256:{index:064x}" for index in range(1, 7)))
    trust_root, _, _, _ = _bootstrap_test_coordinator(identities)
    baseline = MainLatencyConfig(2048, 1024, 256, 8192, 1, 0)
    candidate = compile_candidate(
        {
            "schema": PROPOSAL_SCHEMA,
            "identities": identities.to_dict(),
            "baselineConfig": baseline.to_dict(),
            "changes": [{"key": "main.cacheReuse", "value": 128}],
        },
        trust_root=trust_root,
    )
    plan = build_runner_plan(
        candidate,
        profile="screening",
        attempt=1,
        trust_root=trust_root,
    )
    assert HASH_ID_PATTERN.fullmatch(plan.run_id)
    assert plan.spec.warm_per_condition == 30
    assert (
        plan.to_dict()["network"]
        == "owned_internal_only_external_egress_disabled"
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(sys.argv[1:] if argv is None else argv)
    if args != ("--self-test",):
        print('{"ok":false,"code":"arguments_invalid"}', file=sys.stderr)
        return 2
    self_test()
    print("self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
