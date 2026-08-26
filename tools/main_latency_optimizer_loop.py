from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

if __package__:
    from tools.main_latency_campaign_lock import (
        OWNED_LAB_CAMPAIGN_LOCK,
        OwnedLabCampaignLock as _SharedOwnedLabCampaignLock,
        lock_campaign_file as _lock_campaign_file,
        unlock_campaign_file as _unlock_campaign_file,
    )
    from tools.main_latency_external_runner import (
        DIAGNOSTIC_CODES as EXTERNAL_RUNNER_DIAGNOSTIC_CODES,
        DIAGNOSTIC_SCHEMA as EXTERNAL_RUNNER_DIAGNOSTIC_SCHEMA,
        PRIVATE_RESULT_SCHEMA as EXTERNAL_RUNNER_PRIVATE_RESULT_SCHEMA,
    )
    from tools.main_latency_lab_contract import (
        CLEANUP_SCHEMA,
        EvaluationDecision,
        LAB_OWNER,
        LAB_PREFLIGHT_FAILURE_CODES,
        RunnerPlan,
        RunnerReceipt,
        build_runner_plan,
        compile_runner_receipt,
        evaluate_runner_receipt,
        issue_host_restoration_proof,
    )
    from tools.main_latency_host_lifecycle import MainLatencyHostLifecycle
    from tools.optimize_main_latency import (
        CONFIG_DOMAINS,
        MAX_CANDIDATES,
        MAX_INPUT_BYTES,
        CandidateChange,
        CandidateFeedback,
        CoordinatorTrustRoot,
        EvaluatorCapability,
        LatencyState,
        MainLatencyConfig,
        PromotionEvidence,
        RunnerCapability,
        _capability_matches,
        _validated_trust_root,
        bootstrap_ephemeral_fixed_coordinator,
        candidate_proposal,
        compile_candidate,
        next_candidate,
        parse_json_bytes,
        validate_state_transition,
    )
else:
    from main_latency_campaign_lock import (
        OWNED_LAB_CAMPAIGN_LOCK,
        OwnedLabCampaignLock as _SharedOwnedLabCampaignLock,
        lock_campaign_file as _lock_campaign_file,
        unlock_campaign_file as _unlock_campaign_file,
    )
    from main_latency_external_runner import (
        DIAGNOSTIC_CODES as EXTERNAL_RUNNER_DIAGNOSTIC_CODES,
        DIAGNOSTIC_SCHEMA as EXTERNAL_RUNNER_DIAGNOSTIC_SCHEMA,
        PRIVATE_RESULT_SCHEMA as EXTERNAL_RUNNER_PRIVATE_RESULT_SCHEMA,
    )
    from main_latency_lab_contract import (
        CLEANUP_SCHEMA,
        EvaluationDecision,
        LAB_OWNER,
        LAB_PREFLIGHT_FAILURE_CODES,
        RunnerPlan,
        RunnerReceipt,
        build_runner_plan,
        compile_runner_receipt,
        evaluate_runner_receipt,
        issue_host_restoration_proof,
    )
    from main_latency_host_lifecycle import MainLatencyHostLifecycle
    from optimize_main_latency import (
        CONFIG_DOMAINS,
        MAX_CANDIDATES,
        MAX_INPUT_BYTES,
        CandidateChange,
        CandidateFeedback,
        CoordinatorTrustRoot,
        EvaluatorCapability,
        LatencyState,
        MainLatencyConfig,
        PromotionEvidence,
        RunnerCapability,
        _capability_matches,
        _validated_trust_root,
        bootstrap_ephemeral_fixed_coordinator,
        candidate_proposal,
        compile_candidate,
        next_candidate,
        parse_json_bytes,
        validate_state_transition,
    )


LOOP_CONTEXT_SCHEMA = "evelyn.latency-loop-context.v2"
LOOP_FEEDBACK_SCHEMA = "evelyn.latency-loop-feedback.v1"
LOOP_RESULT_SCHEMA = "evelyn.latency-loop-result.v1"
OPERATOR_REPORT_SCHEMA = "evelyn.latency-loop-operator-report.v1"
RUNNER_REQUEST_SCHEMA = "evelyn.latency-runner-request.v1"
FIXED_RUNNER_SCRIPT = Path(__file__).resolve().with_name("main_latency_external_runner.py")
DEFAULT_DOCKER_EXE = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
DEFAULT_NVIDIA_SMI_EXE = Path(r"C:\Windows\System32\nvidia-smi.exe")
RUNNER_OUTPUT_MAX_BYTES = MAX_INPUT_BYTES
# Separate budgets for read-only preflight, external process teardown, and a
# same-run cleanup worker. The candidate's measured maxRuntimeMs is added on top.
RUNNER_TRANSPORT_OVERHEAD_SEC = 900.0
LOCAL_PROPOSER_URL = "http://127.0.0.1:9821/v1/chat/completions"
LOCAL_PROPOSER_MODEL = "gemma-4-E4B-it-Q4_K_M-text-only"
LOCAL_PROPOSER_MAX_BYTES = 65_536
LOCAL_PROPOSER_SYSTEM = (
    "You are a bounded latency configuration proposer. Return exactly one JSON object "
    "whose keys and integer values come from configDomains, or JSON null when no useful "
    "candidate remains. Use lastCandidate timingDiagnostics, stage deltas, and "
    "bestFrontierConfig to distinguish cache, prompt-eval, queue, route, and context "
    "costs before choosing the next "
    "unattempted candidate. Return only keys whose values differ from baselineConfig; "
    "when extending a frontier, include its changed keys plus at most one new changed "
    "key. Never request tools, files, commands, policy changes, or prose."
)

STOP_REASONS = frozenset(
    {
        "eligible",
        "max_attempts",
        "candidate_space_exhausted",
        "proposer_stopped",
        "proposer_failed",
        "cancelled",
        "runner_unavailable",
        "runner_timeout",
        "runner_cancelled",
        "runner_failed",
        "runner_output_too_large",
        "runner_malformed",
        "runner_receipt_invalid",
        "runner_replay",
        "evaluator_failed",
        "evaluation_inconclusive",
        "host_restoration_failed",
    }
)
FEEDBACK_VERDICTS = frozenset({"rejected", "inconclusive", "frontier", "eligible"})
FEEDBACK_CODES = frozenset(
    {
        "cleanup_required",
        "host_restoration_required",
        "environment_drift",
        "runner_failed",
        "runner_timed_out",
        "runner_cancelled",
        "runner_outcome_ambiguous",
        "candidate_failed",
        "insufficient_samples",
        "reliability_failed",
        "safety_failed",
        "quality_regressed",
        "quality_review_required",
        "resource_failed",
        "latency_regressed",
        "screening_passed",
        "candidate_passed",
        "statistical_evidence_insufficient",
        "frontier_improved",
        "no_material_improvement",
    }
) | LAB_PREFLIGHT_FAILURE_CODES
FEEDBACK_GATES = frozenset(
    {"cleanup", "reliability", "safety", "quality", "resource", "latency", "statistics", "passed"}
)


class RunnerTransportError(RuntimeError):
    def __init__(
        self,
        code: str,
        cleanup: Mapping[str, Any] | None = None,
        diagnostic_code: str | None = None,
        partial_receipt: Mapping[str, Any] | None = None,
    ) -> None:
        if code not in STOP_REASONS or not code.startswith("runner_"):
            raise ValueError("runner_transport_code_invalid")
        if (
            diagnostic_code is not None
            and diagnostic_code not in EXTERNAL_RUNNER_DIAGNOSTIC_CODES
        ):
            raise ValueError("runner_diagnostic_code_invalid")
        self.code = code
        self.diagnostic_code = diagnostic_code
        self.cleanup = (
            MappingProxyType(dict(cleanup)) if cleanup is not None else None
        )
        if partial_receipt is not None and not isinstance(partial_receipt, Mapping):
            raise ValueError("runner_partial_receipt_invalid")
        self.partial_receipt = (
            MappingProxyType(dict(partial_receipt))
            if partial_receipt is not None
            else None
        )
        timing_diagnostics = getattr(partial_receipt, "timing_diagnostics", None)
        self.partial_timing_diagnostics = (
            MappingProxyType(dict(timing_diagnostics))
            if isinstance(timing_diagnostics, Mapping)
            else None
        )
        super().__init__(code)


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    def redirect_request(
        self,
        _req: Any,
        _fp: Any,
        _code: int,
        _msg: str,
        _headers: Any,
        _newurl: str,
    ) -> None:
        raise urllib_error.HTTPError(
            LOCAL_PROPOSER_URL,
            400,
            "redirect_forbidden",
            None,
            None,
        )


@dataclass(frozen=True)
class AggregateMeasurement:
    candidate_id: str
    run_id: str
    attempt: int
    profile: str
    status: str
    verdict: str
    code: str
    gate: str
    baseline_config: Mapping[str, int]
    candidate_config: Mapping[str, int]
    samples: Mapping[str, int]
    baseline_metrics: Mapping[str, float]
    candidate_metrics: Mapping[str, float]
    timing_diagnostics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "runId": self.run_id,
            "attempt": self.attempt,
            "profile": self.profile,
            "status": self.status,
            "verdict": self.verdict,
            "code": self.code,
            "gate": self.gate,
            "baselineConfig": dict(self.baseline_config),
            "candidateConfig": dict(self.candidate_config),
            "samples": dict(self.samples),
            "baselineMetrics": dict(self.baseline_metrics),
            "candidateMetrics": dict(self.candidate_metrics),
            "timingDiagnostics": json.loads(
                json.dumps(
                    dict(self.timing_diagnostics),
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        }


@dataclass(frozen=True)
class OperatorDiagnostic:
    candidate_id: str
    run_id: str
    attempt: int
    profile: str
    code: str

    def __post_init__(self) -> None:
        if self.code not in EXTERNAL_RUNNER_DIAGNOSTIC_CODES:
            raise ValueError("operator_diagnostic_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "runId": self.run_id,
            "attempt": self.attempt,
            "profile": self.profile,
            "layer": "external_runner",
            "code": self.code,
        }


@dataclass(frozen=True)
class _ProposerCandidateSummary:
    candidate_config: Mapping[str, int]
    changes: tuple[CandidateChange, ...]
    stage_deltas: Mapping[str, float]
    check_counts: Mapping[str, int]
    timing_diagnostics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateConfig": dict(self.candidate_config),
            "changes": [change.to_dict() for change in self.changes],
            "stageDeltas": dict(self.stage_deltas),
            "checkCounts": dict(self.check_counts),
            "timingDiagnostics": json.loads(
                json.dumps(
                    dict(self.timing_diagnostics),
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        }


@dataclass(frozen=True)
class LoopFeedback:
    candidate_id: str
    attempt: int
    profile: str
    verdict: str
    code: str
    gate: str
    metrics: Mapping[str, float]
    _measurement: AggregateMeasurement = field(repr=False, compare=False)
    _proposer_summary: _ProposerCandidateSummary = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LOOP_FEEDBACK_SCHEMA,
            "candidateId": self.candidate_id,
            "attempt": self.attempt,
            "profile": self.profile,
            "verdict": self.verdict,
            "code": self.code,
            "gate": self.gate,
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class ProposerContext:
    attempt: int
    baseline_config: Mapping[str, int]
    config_domains: Mapping[str, tuple[int, ...]]
    attempted_candidate_ids: tuple[str, ...]
    feedback: LoopFeedback | None
    best_frontier_config: MainLatencyConfig | None = None
    best_frontier_changes: tuple[CandidateChange, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LOOP_CONTEXT_SCHEMA,
            "attempt": self.attempt,
            "baselineConfig": dict(self.baseline_config),
            "configDomains": {
                key: list(values) for key, values in self.config_domains.items()
            },
            "attemptedCandidateIds": list(self.attempted_candidate_ids),
            "feedback": self.feedback.to_dict() if self.feedback else None,
            "lastCandidate": (
                self.feedback._proposer_summary.to_dict() if self.feedback else None
            ),
            "bestFrontierConfig": (
                self.best_frontier_config.to_dict()
                if self.best_frontier_config is not None
                else None
            ),
            "bestFrontierChanges": [
                change.to_dict() for change in self.best_frontier_changes
            ],
        }


class FixedLocalhostProposer:
    """Strict JSON proposer over the fixed loopback Sub-LLM endpoint."""

    __slots__ = ("timeout_s",)

    def __init__(self, timeout_s: float = 15.0) -> None:
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not 1.0 <= float(timeout_s) <= 30.0
        ):
            raise ValueError("proposer_timeout_invalid")
        self.timeout_s = float(timeout_s)

    def __call__(self, context: ProposerContext) -> dict[str, int] | None:
        if not isinstance(context, ProposerContext):
            raise TypeError("proposer_context_invalid")
        context_json = json.dumps(
            context.to_dict(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = {
            "model": LOCAL_PROPOSER_MODEL,
            "messages": [
                {"role": "system", "content": LOCAL_PROPOSER_SYSTEM},
                {"role": "user", "content": context_json},
            ],
            "temperature": 0,
            "max_tokens": 96,
            "stream": False,
            "cache_prompt": False,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
        if len(encoded) > LOCAL_PROPOSER_MAX_BYTES:
            raise RuntimeError("proposer_request_too_large")
        request = urllib_request.Request(
            LOCAL_PROPOSER_URL,
            data=encoded,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({}),
            _NoRedirect(),
        )
        try:
            with opener.open(request, timeout=self.timeout_s) as response:
                final_url = response.geturl()
                if final_url != LOCAL_PROPOSER_URL or response.status != 200:
                    raise RuntimeError("proposer_endpoint_invalid")
                raw = response.read(LOCAL_PROPOSER_MAX_BYTES + 1)
        except (OSError, urllib_error.URLError):
            raise RuntimeError("proposer_unavailable") from None
        if len(raw) > LOCAL_PROPOSER_MAX_BYTES:
            raise RuntimeError("proposer_response_too_large")
        try:
            body = parse_json_bytes(raw)
            choices = body["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (IndexError, KeyError, TypeError, ValueError):
            return {}
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_INPUT_BYTES:
            return {}
        try:
            proposal = parse_json_bytes(content.encode("utf-8"))
        except ValueError:
            return {}
        if proposal is None:
            return None
        if (
            type(proposal) is not dict
            or not proposal
            or not set(proposal).issubset(CONFIG_DOMAINS)
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in proposal.values()
            )
        ):
            return {}
        return proposal


class AwaitingApprovalContext:
    __slots__ = (
        "candidate_id",
        "run_id",
        "receipt_id",
        "cleanup_proof_id",
        "evaluation_id",
        "attempt",
        "feedback",
        "__promotion_evidence",
    )

    def __init__(
        self,
        *,
        candidate_id: str,
        run_id: str,
        receipt_id: str,
        cleanup_proof_id: str,
        evaluation_id: str,
        attempt: int,
        feedback: CandidateFeedback,
        promotion_evidence: PromotionEvidence,
    ) -> None:
        self.candidate_id = candidate_id
        self.run_id = run_id
        self.receipt_id = receipt_id
        self.cleanup_proof_id = cleanup_proof_id
        self.evaluation_id = evaluation_id
        self.attempt = attempt
        self.feedback = feedback
        self.__promotion_evidence = promotion_evidence

    @property
    def promotion_evidence(self) -> PromotionEvidence:
        return self.__promotion_evidence

    def __repr__(self) -> str:
        return "AwaitingApprovalContext(<redacted-evidence>)"

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("approval_context_not_serializable")


class LoopResult:
    __slots__ = (
        "state",
        "stop_reason",
        "attempt_count",
        "attempted_candidate_ids",
        "feedback",
        "fallback_count",
        "transport_cleanup",
        "__approval_context",
        "__operator_diagnostics",
    )

    def __init__(
        self,
        *,
        state: LatencyState,
        stop_reason: str,
        attempted_candidate_ids: tuple[str, ...],
        feedback: tuple[LoopFeedback, ...],
        fallback_count: int,
        transport_cleanup: Mapping[str, Any] | None = None,
        approval_context: AwaitingApprovalContext | None = None,
        operator_diagnostics: tuple[OperatorDiagnostic, ...] = (),
    ) -> None:
        if (
            stop_reason not in STOP_REASONS
            or any(
                not isinstance(item, OperatorDiagnostic)
                for item in operator_diagnostics
            )
        ):
            raise ValueError("loop_stop_reason_invalid")
        self.state = state
        self.stop_reason = stop_reason
        self.attempt_count = len(attempted_candidate_ids)
        self.attempted_candidate_ids = attempted_candidate_ids
        self.feedback = feedback
        self.fallback_count = fallback_count
        self.transport_cleanup = (
            MappingProxyType(dict(transport_cleanup))
            if transport_cleanup is not None
            else None
        )
        self.__approval_context = approval_context
        self.__operator_diagnostics = tuple(operator_diagnostics)

    @property
    def approval_context(self) -> AwaitingApprovalContext | None:
        return self.__approval_context

    @property
    def operator_diagnostics(self) -> tuple[OperatorDiagnostic, ...]:
        return self.__operator_diagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LOOP_RESULT_SCHEMA,
            "state": self.state.value,
            "stopReason": self.stop_reason,
            "attemptCount": self.attempt_count,
            "attemptedCandidateIds": list(self.attempted_candidate_ids),
            "feedback": [item.to_dict() for item in self.feedback],
            "fallbackCount": self.fallback_count,
            "transportCleanup": (
                dict(self.transport_cleanup)
                if self.transport_cleanup is not None
                else None
            ),
            "awaitingApproval": self.__approval_context is not None,
        }

    def operator_report_dict(self) -> dict[str, Any]:
        return {
            "schema": OPERATOR_REPORT_SCHEMA,
            "result": self.to_dict(),
            "measurements": [
                item._measurement.to_dict() for item in self.feedback
            ],
            "diagnostics": [
                item.to_dict() for item in self.__operator_diagnostics
            ],
        }

    def __repr__(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("loop_result_not_serializable")


def _minimal_runner_env() -> dict[str, str]:
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    for key in (
        "SYSTEMROOT",
        "WINDIR",
        "USERPROFILE",
        "PROGRAMFILES",
        "PROGRAMDATA",
        "EVELYN_LLAMA_CPP_DIR",
        "EVELYN_MAIN_LLM_BUILD_DIR",
        "EVELYN_OMNIVOICE_SERVER_DIR",
        "EVELYN_OMNIVOICE_PROFILES_DIR",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


class _RunnerProcessTreeOwner:
    """Own the fixed runner and every child it creates."""

    __slots__ = ("__windows_job",)

    def __init__(self) -> None:
        self.__windows_job: Any | None = None
        if os.name != "nt":
            return
        runtime_root = Path(__file__).resolve().parents[1] / "evelyn_core" / "runtime"
        if str(runtime_root) not in sys.path:
            sys.path.insert(0, str(runtime_root))
        from evelyn_core.windows_process_job import KillOnCloseProcessOwner

        self.__windows_job = KillOnCloseProcessOwner()

    def assign(self, process: subprocess.Popen[bytes]) -> bool:
        if os.name != "nt":
            return True
        if self.__windows_job is None:
            return False
        from evelyn_core.process_identity import process_birth_identity

        birth_identity = process_birth_identity(int(process.pid))
        return bool(
            birth_identity
            and self.__windows_job.assign(process, str(birth_identity))
        )

    def terminate_tree(self, process: subprocess.Popen[bytes]) -> None:
        if self.__windows_job is not None:
            self.__windows_job.close()
            return
        try:
            os.killpg(int(process.pid), signal.SIGKILL)
        except (AttributeError, OSError, ProcessLookupError):
            pass

    def close(self) -> None:
        if self.__windows_job is not None:
            self.__windows_job.close()


class _OwnedLabCampaignLock(_SharedOwnedLabCampaignLock):
    __slots__ = ()

    def __init__(self) -> None:
        super().__init__(OWNED_LAB_CAMPAIGN_LOCK)


def _kill_and_reap(
    process: subprocess.Popen[bytes],
    owner: _RunnerProcessTreeOwner,
) -> None:
    owner.terminate_tree(process)
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    owner.close()


def _decode_external_runner_output(
    output: bytes,
    returncode: int,
    cleanup: Mapping[str, Any],
) -> Mapping[str, Any]:
    try:
        parsed = parse_json_bytes(output)
    except ValueError:
        code = "runner_failed" if returncode != 0 else "runner_malformed"
        raise RunnerTransportError(code, cleanup) from None
    diagnostic_code = (
        parsed.get("code")
        if type(parsed) is dict
        and set(parsed) == {"schema", "code"}
        and parsed.get("schema") == EXTERNAL_RUNNER_DIAGNOSTIC_SCHEMA
        and parsed.get("code") in EXTERNAL_RUNNER_DIAGNOSTIC_CODES
        else None
    )
    if returncode != 0:
        raise RunnerTransportError(
            "runner_failed",
            cleanup,
            diagnostic_code=diagnostic_code,
        )
    if type(parsed) is not dict or diagnostic_code is not None:
        raise RunnerTransportError("runner_malformed", cleanup)
    if parsed.get("schema") != EXTERNAL_RUNNER_PRIVATE_RESULT_SCHEMA:
        return parsed
    if set(parsed) != {"schema", "receipt", "timingDiagnostics"} or type(
        parsed.get("receipt")
    ) is not dict:
        raise RunnerTransportError("runner_malformed", cleanup)
    try:
        if __package__:
            from tools.main_latency_fixed_lab_adapter import (
                normalize_private_timing_diagnostics,
            )
        else:
            from main_latency_fixed_lab_adapter import (
                normalize_private_timing_diagnostics,
            )

        timing_diagnostics = normalize_private_timing_diagnostics(
            parsed["timingDiagnostics"]
        )
    except (ImportError, TypeError, ValueError):
        raise RunnerTransportError("runner_malformed", cleanup) from None
    if (
        parsed["receipt"].get("status") == "completed"
    ) != bool(timing_diagnostics):
        raise RunnerTransportError("runner_malformed", cleanup)
    receipt = _PrivateRunnerReceipt(parsed["receipt"])
    receipt.timing_diagnostics = timing_diagnostics
    return receipt


class _PrivateRunnerReceipt(dict[str, Any]):
    """Exact public receipt keys plus process-local private timing metadata."""

    timing_diagnostics: Mapping[str, Any]


def _unknown_transport_cleanup(plan: RunnerPlan) -> dict[str, Any]:
    return {
        "schema": CLEANUP_SCHEMA,
        "runId": plan.run_id,
        "owner": LAB_OWNER,
        "status": "cleanup_required",
        "remainingProcesses": 1,
        "remainingGpuAllocations": 1,
        "remainingArtifacts": 1,
    }


def _normalize_transport_cleanup(
    plan: RunnerPlan,
    cleanup: Any,
) -> dict[str, Any]:
    fields = {
        "schema",
        "runId",
        "owner",
        "status",
        "remainingProcesses",
        "remainingGpuAllocations",
        "remainingArtifacts",
    }
    if (
        type(cleanup) is not dict
        or set(cleanup) != fields
        or cleanup.get("schema") != CLEANUP_SCHEMA
        or cleanup.get("runId") != plan.run_id
        or cleanup.get("owner") != LAB_OWNER
        or cleanup.get("status") not in {"clean", "cleanup_required"}
        or any(
            type(cleanup.get(key)) is not int
            or not 0 <= cleanup[key] <= maximum
            for key, maximum in (
                ("remainingProcesses", 8),
                ("remainingGpuAllocations", 4),
                ("remainingArtifacts", 64),
            )
        )
        or (
            cleanup.get("status") == "clean"
            and any(
                cleanup[key]
                for key in (
                    "remainingProcesses",
                    "remainingGpuAllocations",
                    "remainingArtifacts",
                )
            )
        )
    ):
        return _unknown_transport_cleanup(plan)
    return dict(cleanup)


def _cleanup_runner_resources(plan: RunnerPlan) -> dict[str, Any]:
    try:
        if __package__:
            from tools.main_latency_fixed_lab_adapter import cleanup_owned_lab
        else:
            from main_latency_fixed_lab_adapter import cleanup_owned_lab

        cleanup = cleanup_owned_lab(plan)
    except Exception:
        return _unknown_transport_cleanup(plan)
    return _normalize_transport_cleanup(plan, cleanup)


class FixedSubprocessRunnerTransport:
    """Fixed child transport with a run-bound capability sent only over stdin."""

    __slots__ = ("__runner_capability",)

    def __init__(self, runner_capability: RunnerCapability) -> None:
        if not isinstance(runner_capability, RunnerCapability):
            raise TypeError("runner_capability_invalid")
        self.__runner_capability = runner_capability

    def __repr__(self) -> str:
        return "FixedSubprocessRunnerTransport(<redacted-capability>)"

    def __reduce_ex__(self, _protocol: int) -> Any:
        raise TypeError("runner_transport_not_serializable")

    def __call__(
        self,
        plan: RunnerPlan,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not FIXED_RUNNER_SCRIPT.is_file():
            raise RunnerTransportError("runner_unavailable")
        one_run_capability = self.__runner_capability._issue_one_run(plan.run_id)
        request = {
            "schema": RUNNER_REQUEST_SCHEMA,
            "plan": plan.to_dict(),
            "runnerCapability": one_run_capability._export_once(),
        }
        encoded = json.dumps(
            request,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(encoded) > MAX_INPUT_BYTES:
            raise RunnerTransportError("runner_malformed")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process_owner = _RunnerProcessTreeOwner()
        except OSError:
            raise RunnerTransportError("runner_unavailable") from None
        try:
            process = subprocess.Popen(
                (sys.executable, "-I", str(FIXED_RUNNER_SCRIPT)),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                env=_minimal_runner_env(),
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except OSError:
            process_owner.close()
            raise RunnerTransportError("runner_unavailable") from None
        try:
            assigned = process_owner.assign(process)
        except (OSError, ValueError):
            assigned = False
        if not assigned:
            _kill_and_reap(process, process_owner)
            raise RunnerTransportError("runner_unavailable")
        output = bytearray()
        overflow = threading.Event()
        io_error = threading.Event()

        def write_input() -> None:
            stream = process.stdin
            try:
                assert stream is not None
                stream.write(encoded)
            except (BrokenPipeError, OSError, ValueError):
                io_error.set()
            finally:
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

        def read_output() -> None:
            stream = process.stdout
            try:
                assert stream is not None
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    remaining = RUNNER_OUTPUT_MAX_BYTES + 1 - len(output)
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    if len(output) > RUNNER_OUTPUT_MAX_BYTES or len(chunk) > remaining:
                        overflow.set()
            except (OSError, ValueError):
                io_error.set()
            finally:
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

        writer = threading.Thread(target=write_input, daemon=True)
        reader = threading.Thread(target=read_output, daemon=True)
        writer.start()
        reader.start()

        def fail_after_cleanup(code: str, *, terminate: bool) -> None:
            if terminate:
                _kill_and_reap(process, process_owner)
                writer.join(timeout=1)
                reader.join(timeout=1)
            cleanup = _cleanup_runner_resources(plan)
            raise RunnerTransportError(code, cleanup)

        deadline = (
            time.monotonic()
            + (plan.spec.max_runtime_ms / 1000)
            + RUNNER_TRANSPORT_OVERHEAD_SEC
        )
        while process.poll() is None:
            if overflow.is_set():
                fail_after_cleanup("runner_output_too_large", terminate=True)
            if cancel_event is not None and cancel_event.is_set():
                fail_after_cleanup("runner_cancelled", terminate=True)
            if time.monotonic() >= deadline:
                fail_after_cleanup("runner_timeout", terminate=True)
            time.sleep(0.01)
        writer.join(timeout=1)
        reader.join(timeout=1)
        if writer.is_alive() or reader.is_alive():
            fail_after_cleanup("runner_failed", terminate=True)
        process_owner.terminate_tree(process)
        process_owner.close()
        cleanup = _cleanup_runner_resources(plan)
        if overflow.is_set():
            raise RunnerTransportError("runner_output_too_large", cleanup)
        if io_error.is_set():
            raise RunnerTransportError("runner_failed", cleanup)
        decoded = _decode_external_runner_output(
            bytes(output),
            int(process.returncode),
            cleanup,
        )
        if cleanup["status"] != "clean":
            raise RunnerTransportError(
                "runner_failed",
                cleanup,
                partial_receipt=decoded,
            )
        return decoded


def _proposer_stage_deltas(receipt: RunnerReceipt) -> Mapping[str, float]:
    baseline = receipt.baseline_metrics
    candidate = receipt.candidate_metrics
    return MappingProxyType(
        {
            "postSttMainWriteP95DeltaMs": (
                candidate.post_stt_main_write_p95_ms
                - baseline.post_stt_main_write_p95_ms
            ),
            "rawFirstTokenP95DeltaMs": (
                candidate.raw_first_token_p95_ms - baseline.raw_first_token_p95_ms
            ),
            "rawToSafeSpeechP95DeltaMs": (
                candidate.raw_to_safe_speech_p95_ms
                - baseline.raw_to_safe_speech_p95_ms
            ),
            "safePrefixCommitP95DeltaMs": (
                candidate.safe_prefix_commit_p95_ms
                - baseline.safe_prefix_commit_p95_ms
            ),
            "ttsFirstPcmP95DeltaMs": (
                candidate.tts_first_pcm_p95_ms - baseline.tts_first_pcm_p95_ms
            ),
        }
    )


def _feedback_for(
    decision: EvaluationDecision,
    receipt: RunnerReceipt,
    *,
    attempt: int,
    profile: str,
    changes: tuple[CandidateChange, ...],
    timing_diagnostics: Mapping[str, Any],
) -> LoopFeedback:
    if (
        decision.verdict not in FEEDBACK_VERDICTS
        or decision.gate not in FEEDBACK_GATES
        or decision.code not in FEEDBACK_CODES
        or profile not in {"screening", "finalist"}
    ):
        raise ValueError("evaluation_decision_invalid")
    return LoopFeedback(
        decision.candidate_id,
        attempt,
        profile,
        decision.verdict,
        decision.code,
        decision.gate,
        MappingProxyType(dict(decision.deltas)),
        AggregateMeasurement(
            candidate_id=receipt.candidate_id,
            run_id=receipt.run_id,
            attempt=attempt,
            profile=profile,
            status=receipt.status,
            verdict=decision.verdict,
            code=decision.code,
            gate=decision.gate,
            baseline_config=MappingProxyType(receipt.baseline_config.to_dict()),
            candidate_config=MappingProxyType(receipt.candidate_config.to_dict()),
            samples=MappingProxyType(receipt.samples.to_dict()),
            baseline_metrics=MappingProxyType(receipt.baseline_metrics.to_dict()),
            candidate_metrics=MappingProxyType(receipt.candidate_metrics.to_dict()),
            timing_diagnostics=MappingProxyType(dict(timing_diagnostics)),
        ),
        _ProposerCandidateSummary(
            candidate_config=MappingProxyType(receipt.candidate_config.to_dict()),
            changes=tuple(changes),
            stage_deltas=_proposer_stage_deltas(receipt),
            check_counts=MappingProxyType(receipt.checks.to_dict()),
            timing_diagnostics=MappingProxyType(dict(timing_diagnostics)),
        ),
    )


def _result(
    *,
    state: LatencyState,
    reason: str,
    attempted: list[str],
    feedback: list[LoopFeedback],
    fallback_count: int,
    transport_cleanup: Mapping[str, Any] | None = None,
    approval_context: AwaitingApprovalContext | None = None,
    operator_diagnostics: tuple[OperatorDiagnostic, ...] = (),
) -> LoopResult:
    return LoopResult(
        state=state,
        stop_reason=reason,
        attempted_candidate_ids=tuple(attempted),
        feedback=tuple(feedback),
        fallback_count=fallback_count,
        transport_cleanup=transport_cleanup,
        approval_context=approval_context,
        operator_diagnostics=operator_diagnostics,
    )


def run_optimizer_loop(
    *,
    baseline_config: MainLatencyConfig,
    trust_root: CoordinatorTrustRoot,
    evaluator_capability: EvaluatorCapability,
    proposer: Any,
    runner: Any,
    host_restoration_prover: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> LoopResult:
    trust_root = _validated_trust_root(trust_root)
    baseline = MainLatencyConfig.from_mapping(baseline_config.to_dict())
    if not _capability_matches(
        trust_root,
        evaluator_capability,
        EvaluatorCapability,
        "_verify_evaluator",
    ):
        return _result(
            state=LatencyState.FAILED,
            reason="evaluator_failed",
            attempted=[],
            feedback=[],
            fallback_count=0,
        )
    if (
        not callable(proposer)
        or not callable(runner)
        or (
            host_restoration_prover is not None
            and not callable(host_restoration_prover)
        )
    ):
        raise TypeError("loop_adapter_invalid")
    attempted: list[str] = []
    feedback_items: list[LoopFeedback] = []
    last_feedback: LoopFeedback | None = None
    best_frontier_config: MainLatencyConfig | None = None
    best_frontier_changes: tuple[CandidateChange, ...] = ()
    best_frontier_p95_delta: float | None = None
    fallback_count = 0

    for attempt in range(1, MAX_CANDIDATES + 1):
        if cancel_event is not None and cancel_event.is_set():
            return _result(
                state=LatencyState.FAILED,
                reason="cancelled",
                attempted=attempted,
                feedback=feedback_items,
                fallback_count=fallback_count,
            )
        candidate = None
        if attempt == 1:
            try:
                candidate = next_candidate(
                    trust_root.pinned_identities,
                    baseline,
                    trust_root=trust_root,
                    attempted_candidate_ids=attempted,
                )
            except ValueError:
                candidate = None
        else:
            context = ProposerContext(
                attempt,
                MappingProxyType(baseline.to_dict()),
                MappingProxyType(
                    {key: tuple(values) for key, values in CONFIG_DOMAINS.items()}
                ),
                tuple(attempted),
                last_feedback,
                best_frontier_config,
                best_frontier_changes,
            )
            try:
                proposal = proposer(context)
            except Exception:
                return _result(
                    state=LatencyState.FAILED,
                    reason="proposer_failed",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            if proposal is None:
                return _result(
                    state=LatencyState.FEEDBACK_READY,
                    reason="proposer_stopped",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            if type(proposal) is dict:
                try:
                    candidate = compile_candidate(
                        candidate_proposal(
                            trust_root.pinned_identities,
                            baseline,
                            proposal,
                        ),
                        trust_root=trust_root,
                    )
                except ValueError:
                    candidate = None
            if candidate is None or candidate.candidate_id in attempted:
                fallback_count += 1
                try:
                    candidate = next_candidate(
                        trust_root.pinned_identities,
                        baseline,
                        trust_root=trust_root,
                        attempted_candidate_ids=attempted,
                    )
                except ValueError:
                    candidate = None
        if candidate is None:
            return _result(
                state=LatencyState.FEEDBACK_READY if attempted else LatencyState.IDLE,
                reason="candidate_space_exhausted",
                attempted=attempted,
                feedback=feedback_items,
                fallback_count=fallback_count,
            )
        attempted.append(candidate.candidate_id)

        decision = None
        final_plan = None
        final_receipt = None
        for profile in ("screening", "finalist"):
            plan = build_runner_plan(
                candidate,
                profile=profile,
                attempt=attempt,
                trust_root=trust_root,
            )
            try:
                raw_receipt = runner(plan, cancel_event)
                if not isinstance(raw_receipt, dict):
                    raise RunnerTransportError("runner_malformed")
                timing_diagnostics = (
                    raw_receipt.timing_diagnostics
                    if isinstance(raw_receipt, _PrivateRunnerReceipt)
                    else {}
                )
                receipt = compile_runner_receipt(
                    plan,
                    raw_receipt,
                    trust_root=trust_root,
                )
            except RunnerTransportError as exc:
                cleanup_verified = bool(
                    exc.cleanup is not None
                    and exc.cleanup.get("status") == "clean"
                )
                return _result(
                    state=(
                        LatencyState.FAILED
                        if cleanup_verified
                        else LatencyState.CLEANUP_REQUIRED
                    ),
                    reason=exc.code,
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                    transport_cleanup=exc.cleanup,
                    operator_diagnostics=(
                        (
                            OperatorDiagnostic(
                                candidate_id=candidate.candidate_id,
                                run_id=plan.run_id,
                                attempt=attempt,
                                profile=profile,
                                code=exc.diagnostic_code,
                            ),
                        )
                        if exc.diagnostic_code is not None
                        else ()
                    ),
                )
            except ValueError:
                return _result(
                    state=LatencyState.FAILED,
                    reason="runner_receipt_invalid",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            except Exception:
                return _result(
                    state=LatencyState.FAILED,
                    reason="runner_failed",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            if not trust_root._consume_once("runner", receipt.run_id):
                return _result(
                    state=LatencyState.FAILED,
                    reason="runner_replay",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            try:
                decision = evaluate_runner_receipt(
                    plan,
                    receipt,
                    trust_root=trust_root,
                    evaluator_capability=evaluator_capability,
                )
                if (
                    profile == "finalist"
                    and decision.verdict == "inconclusive"
                    and decision.code == "host_restoration_required"
                    and decision.gate == "cleanup"
                    and host_restoration_prover is not None
                ):
                    try:
                        host_restoration_proof = host_restoration_prover(plan, receipt)
                    except Exception:
                        return _result(
                            state=LatencyState.CLEANUP_REQUIRED,
                            reason="host_restoration_failed",
                            attempted=attempted,
                            feedback=feedback_items,
                            fallback_count=fallback_count,
                        )
                    decision = evaluate_runner_receipt(
                        plan,
                        receipt,
                        trust_root=trust_root,
                        evaluator_capability=evaluator_capability,
                        host_restoration_proof=host_restoration_proof,
                    )
                loop_feedback = _feedback_for(
                    decision,
                    receipt,
                    attempt=attempt,
                    profile=profile,
                    changes=tuple(candidate.changes),
                    timing_diagnostics=timing_diagnostics,
                )
            except (TypeError, ValueError):
                return _result(
                    state=LatencyState.FAILED,
                    reason="evaluator_failed",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            feedback_items.append(loop_feedback)
            last_feedback = loop_feedback
            if decision.verdict == "frontier":
                p95_delta = float(decision.deltas["postSttFirstPcmP95DeltaMs"])
                if (
                    best_frontier_p95_delta is None
                    or p95_delta < best_frontier_p95_delta
                ):
                    best_frontier_config = candidate.candidate_config
                    best_frontier_changes = tuple(candidate.changes)
                    best_frontier_p95_delta = p95_delta
            final_plan = plan
            final_receipt = receipt
            if profile == "screening" and not (
                decision.verdict == "frontier" and decision.code == "screening_passed"
            ):
                break

        assert decision is not None and final_plan is not None and final_receipt is not None
        if decision.verdict == "eligible":
            if decision.promotion_feedback is None or decision.promotion_evidence is None:
                return _result(
                    state=LatencyState.FAILED,
                    reason="evaluator_failed",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            try:
                state = validate_state_transition(
                    LatencyState.FEEDBACK_READY,
                    LatencyState.AWAITING_APPROVAL,
                    candidate_id=candidate.candidate_id,
                    feedback=decision.promotion_feedback,
                    promotion_evidence=decision.promotion_evidence,
                    trust_root=trust_root,
                    expected_run_id=final_plan.run_id,
                    expected_receipt_id=final_receipt.receipt_id,
                    expected_cleanup_proof_id=final_receipt.cleanup.proof_id,
                    expected_evaluation_id=decision.evaluation_id,
                    expected_attempt=attempt,
                )
            except ValueError:
                return _result(
                    state=LatencyState.FAILED,
                    reason="evaluator_failed",
                    attempted=attempted,
                    feedback=feedback_items,
                    fallback_count=fallback_count,
                )
            approval_context = AwaitingApprovalContext(
                candidate_id=candidate.candidate_id,
                run_id=final_plan.run_id,
                receipt_id=final_receipt.receipt_id,
                cleanup_proof_id=final_receipt.cleanup.proof_id,
                evaluation_id=decision.evaluation_id,
                attempt=attempt,
                feedback=decision.promotion_feedback,
                promotion_evidence=decision.promotion_evidence,
            )
            return _result(
                state=state,
                reason="eligible",
                attempted=attempted,
                feedback=feedback_items,
                fallback_count=fallback_count,
                approval_context=approval_context,
            )
        if decision.verdict == "inconclusive":
            return _result(
                state=LatencyState.FAILED,
                reason="evaluation_inconclusive",
                attempted=attempted,
                feedback=feedback_items,
                fallback_count=fallback_count,
            )

    return _result(
        state=LatencyState.FEEDBACK_READY,
        reason="max_attempts",
        attempted=attempted,
        feedback=feedback_items,
        fallback_count=fallback_count,
    )


def run_fixed_local_optimizer_loop(
    *,
    baseline_config: MainLatencyConfig,
    trust_root: CoordinatorTrustRoot,
    runner_capability: RunnerCapability,
    evaluator_capability: EvaluatorCapability,
    host_restoration_prover: Any | None = None,
    cancel_event: threading.Event | None = None,
    proposer_timeout_s: float = 15.0,
) -> LoopResult:
    """Wire the fixed loopback proposer to the fixed external runner boundary."""
    pinned_root = _validated_trust_root(trust_root)
    if not _capability_matches(
        pinned_root,
        runner_capability,
        RunnerCapability,
        "_verify_runner",
    ):
        raise ValueError("runner_capability_invalid")
    return run_optimizer_loop(
        baseline_config=baseline_config,
        trust_root=pinned_root,
        evaluator_capability=evaluator_capability,
        proposer=FixedLocalhostProposer(proposer_timeout_s),
        runner=FixedSubprocessRunnerTransport(runner_capability),
        host_restoration_prover=host_restoration_prover,
        cancel_event=cancel_event,
    )


def run_owned_lab_optimizer_loop(
    *,
    baseline_config: MainLatencyConfig | None = None,
    cancel_event: threading.Event | None = None,
    proposer_timeout_s: float = 15.0,
    host_lifecycle: Any | None = None,
) -> LoopResult:
    """Discover the fixed lab, mint ephemeral authority, and run the bounded loop."""

    baseline = baseline_config or MainLatencyConfig(2048, 1024, 256, 8192, 1, 0)
    baseline = MainLatencyConfig.from_mapping(baseline.to_dict())
    if __package__:
        from tools.main_latency_fixed_lab_adapter import (
            discover_owned_lab_identities,
            reconcile_owned_lab,
        )
    else:
        from main_latency_fixed_lab_adapter import (
            discover_owned_lab_identities,
            reconcile_owned_lab,
        )

    lifecycle = host_lifecycle or MainLatencyHostLifecycle(
        DEFAULT_DOCKER_EXE,
        DEFAULT_NVIDIA_SMI_EXE,
    )

    def exact_clean(cleanup: Mapping[str, Any] | None) -> bool:
        return bool(
            cleanup is not None
            and cleanup.get("status") == "clean"
            and cleanup.get("remainingProcesses") == 0
            and cleanup.get("remainingGpuAllocations") == 0
            and cleanup.get("remainingArtifacts") == 0
        )

    with _OwnedLabCampaignLock():
        result: LoopResult | None = None
        terminal_cleanup: Mapping[str, Any] | None = None
        host_restore: Mapping[str, Any] = {"status": "blocked"}
        host_attempted = False
        host_prepared = False
        try:
            host_attempted = True
            lifecycle.prepare()
            host_prepared = True
            startup_cleanup = reconcile_owned_lab()
            if not exact_clean(startup_cleanup):
                raise RuntimeError("owned_lab_cleanup_required")
            lifecycle.verify_measurement_preflight()
            identities = discover_owned_lab_identities(baseline)
            (
                trust_root,
                runner_capability,
                evaluator_capability,
                lifecycle_capability,
            ) = (
                bootstrap_ephemeral_fixed_coordinator(identities)
            )

            def prove_host_restoration(
                plan: RunnerPlan,
                receipt: RunnerReceipt,
            ) -> Any:
                nonlocal terminal_cleanup
                terminal_cleanup = reconcile_owned_lab()
                if not exact_clean(terminal_cleanup):
                    raise RuntimeError("owned_lab_cleanup_required")
                observation = lifecycle.finish_after_owned_cleanup()
                return issue_host_restoration_proof(
                    plan,
                    receipt,
                    observation,
                    trust_root=trust_root,
                    lifecycle_capability=lifecycle_capability,
                )

            result = run_fixed_local_optimizer_loop(
                baseline_config=baseline,
                trust_root=trust_root,
                runner_capability=runner_capability,
                evaluator_capability=evaluator_capability,
                host_restoration_prover=prove_host_restoration,
                cancel_event=cancel_event,
                proposer_timeout_s=proposer_timeout_s,
            )
        finally:
            if host_prepared and terminal_cleanup is None:
                terminal_cleanup = reconcile_owned_lab()
            if host_attempted:
                host_restore = lifecycle.best_effort_restore()
        assert result is not None
        if not exact_clean(terminal_cleanup) or host_restore.get("status") != "clean":
            return LoopResult(
                state=LatencyState.CLEANUP_REQUIRED,
                stop_reason=(
                    "runner_failed"
                    if not exact_clean(terminal_cleanup)
                    else "host_restoration_failed"
                ),
                attempted_candidate_ids=result.attempted_candidate_ids,
                feedback=result.feedback,
                fallback_count=result.fallback_count,
                transport_cleanup=terminal_cleanup,
                operator_diagnostics=result.operator_diagnostics,
            )
        if result.state is LatencyState.CLEANUP_REQUIRED:
            return LoopResult(
                state=LatencyState.FAILED,
                stop_reason=result.stop_reason,
                attempted_candidate_ids=result.attempted_candidate_ids,
                feedback=result.feedback,
                fallback_count=result.fallback_count,
                transport_cleanup=terminal_cleanup,
                operator_diagnostics=result.operator_diagnostics,
            )
        return result


def self_test() -> None:
    assert MAX_CANDIDATES == 12
    assert FixedSubprocessRunnerTransport.__slots__ == ("__runner_capability",)
    assert "eligible" in STOP_REASONS


def main() -> int:
    if sys.argv[1:] in (["--run-owned-lab"], ["--run-owned-lab-report"]):
        try:
            result = run_owned_lab_optimizer_loop()
        except Exception:
            print("owned-lab startup failed", file=sys.stderr)
            return 2
        payload = (
            result.operator_report_dict()
            if sys.argv[1:] == ["--run-owned-lab-report"]
            else result.to_dict()
        )
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if result.state is LatencyState.AWAITING_APPROVAL else 1
    if sys.argv[1:]:
        print(
            "usage: main_latency_optimizer_loop.py "
            "[--run-owned-lab|--run-owned-lab-report]",
            file=sys.stderr,
        )
        return 2
    self_test()
    print("self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
