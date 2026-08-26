"""Fixed subprocess boundary for the owned Main-latency lab.

Only the fixed worker may create the isolated Docker lab. The candidate plan
is transferred over stdin, never interpolated into a command line.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

if __package__:
    from tools.main_latency_lab_contract import (
        CHECK_FIELDS,
        CLEANUP_SCHEMA,
        LAB_OWNER,
        LAB_PREFLIGHT_FAILURE_CODES,
        METRIC_FIELDS,
        RUNNER_RECEIPT_SCHEMA,
        SAMPLE_FIELDS,
        STATISTICS_SCHEMA,
        RunnerPlan,
    )
    from tools.optimize_main_latency import IdentitySet, MainLatencyConfig
else:
    from main_latency_lab_contract import (
        CHECK_FIELDS,
        CLEANUP_SCHEMA,
        LAB_OWNER,
        LAB_PREFLIGHT_FAILURE_CODES,
        METRIC_FIELDS,
        RUNNER_RECEIPT_SCHEMA,
        SAMPLE_FIELDS,
        STATISTICS_SCHEMA,
        RunnerPlan,
    )
    from optimize_main_latency import IdentitySet, MainLatencyConfig


LAB_ADAPTER_CONTRACT_ID = "main-latency-fixed-lab-adapter-v2"
LAB_PREFLIGHT_SCHEMA = "evelyn.latency-lab-preflight.v1"
LAB_WORKER_REQUEST_SCHEMA = "evelyn.main-latency-owned-lab-request.v1"
LAB_WORKER_RESPONSE_SCHEMA = "evelyn.main-latency-owned-lab-response.v1"
LAB_WORKER_SCRIPT = Path(__file__).resolve().with_name("main_latency_owned_lab_worker.py")
MAX_WORKER_OUTPUT_BYTES = 1_048_576
PRIVATE_TIMING_SCHEMA = "evelyn.main-latency-private-timing.v3"
PRIVATE_TIMING_CONDITIONS = ("baseline", "candidate")
PRIVATE_TIMING_COHORTS = ("afterActivation", "resident")
PRIVATE_TIMING_METRIC_BOUNDS = {
    "promptEvalMs": 30_000.0,
    "promptCacheHitRatio": 1.0,
    "promptTokensProcessed": 1_000_000.0,
    "promptTokensCached": 1_000_000.0,
    "promptTokensTotal": 1_000_000.0,
    "queueMs": 30_000.0,
    "routeMs": 30_000.0,
    "contextMs": 30_000.0,
    "rawFirstTokenMs": 30_000.0,
    "safePrefixCommitMs": 30_000.0,
    "answerFirstPcmMs": 30_000.0,
}
PRIVATE_TIMING_REQUIRED_METRICS = frozenset(
    {
        "promptEvalMs",
        "promptCacheHitRatio",
        "promptTokensProcessed",
        "promptTokensCached",
        "promptTokensTotal",
        "rawFirstTokenMs",
        "safePrefixCommitMs",
        "answerFirstPcmMs",
    }
)
GLOBAL_RECONCILE_RUN_ID = (
    "sha256:"
    + hashlib.sha256(b"evelyn-main-latency-global-reconcile-v1").hexdigest()
)


class LabAdapterUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in LAB_PREFLIGHT_FAILURE_CODES:
            raise ValueError("lab_preflight_code_invalid")
        self.code = code
        super().__init__(code)


class LabIdentityDiscoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        if code not in LAB_PREFLIGHT_FAILURE_CODES:
            code = "lab_isolation_preflight_failed"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LabPreflight:
    ready: bool
    code: str

    def __post_init__(self) -> None:
        invalid_code = (
            self.code != "ready"
            if self.ready
            else self.code not in LAB_PREFLIGHT_FAILURE_CODES
        )
        if type(self.ready) is not bool or invalid_code:
            raise ValueError("lab_preflight_invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LAB_PREFLIGHT_SCHEMA,
            "adapterContract": LAB_ADAPTER_CONTRACT_ID,
            "ready": self.ready,
            "code": self.code,
        }


class FixedLabAdapter(Protocol):
    def preflight(self, plan: RunnerPlan) -> LabPreflight: ...

    def run(self, plan: RunnerPlan) -> Mapping[str, Any]: ...

    def cleanup(self, plan: RunnerPlan) -> Mapping[str, Any]: ...


WorkerCall = Callable[[str, RunnerPlan], Mapping[str, Any]]


def is_fixed_lab_worker_command(executable: Any, argv: Any) -> bool:
    """Return true only for the one child command allowed by the runner hook."""

    expected = (str(sys.executable), "-I", str(LAB_WORKER_SCRIPT))
    expected_executable = Path(sys.executable).resolve()
    if isinstance(argv, (str, bytes)):
        try:
            actual_executable = (
                None
                if executable is None
                else Path(os.fsdecode(executable)).resolve()
            )
            command_line = os.fsdecode(argv)
        except (OSError, TypeError, ValueError):
            return False
        return (
            actual_executable in {None, expected_executable}
            and command_line == subprocess.list2cmdline(expected)
        )
    if not isinstance(executable, (str, bytes, os.PathLike)) or not isinstance(argv, (list, tuple)):
        return False
    try:
        actual_executable = Path(os.fsdecode(executable)).resolve()
        actual_argv = tuple(os.fsdecode(item) for item in argv)
    except (OSError, TypeError, ValueError):
        return False
    return actual_executable == expected_executable and actual_argv == expected


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("lab_worker_duplicate_key")
        result[key] = value
    return result


def normalize_private_timing_diagnostics(value: Any) -> dict[str, Any]:
    """Validate and copy the numeric-only operator/proposer timing envelope."""

    if value == {}:
        return {}
    if (
        type(value) is not dict
        or set(value) != {"schema", *PRIVATE_TIMING_CONDITIONS}
        or value.get("schema") != PRIVATE_TIMING_SCHEMA
    ):
        raise ValueError("lab_private_timing_invalid")
    normalized: dict[str, Any] = {"schema": PRIVATE_TIMING_SCHEMA}
    for condition in PRIVATE_TIMING_CONDITIONS:
        cohorts = value.get(condition)
        if type(cohorts) is not dict or set(cohorts) != set(PRIVATE_TIMING_COHORTS):
            raise ValueError("lab_private_timing_invalid")
        normalized_cohorts: dict[str, Any] = {}
        for cohort in PRIVATE_TIMING_COHORTS:
            metrics = cohorts.get(cohort)
            if (
                type(metrics) is not dict
                or not PRIVATE_TIMING_REQUIRED_METRICS.issubset(metrics)
                or not set(metrics).issubset(PRIVATE_TIMING_METRIC_BOUNDS)
            ):
                raise ValueError("lab_private_timing_invalid")
            normalized_metrics: dict[str, Any] = {}
            for name, summary in metrics.items():
                if type(summary) is not dict or set(summary) != {"sampleCount", "p50", "p95"}:
                    raise ValueError("lab_private_timing_invalid")
                count = summary.get("sampleCount")
                p50 = summary.get("p50")
                p95 = summary.get("p95")
                maximum = PRIVATE_TIMING_METRIC_BOUNDS[name]
                if (
                    type(count) is not int
                    or not 1 <= count <= 1000
                    or isinstance(p50, bool)
                    or isinstance(p95, bool)
                    or not isinstance(p50, (int, float))
                    or not isinstance(p95, (int, float))
                ):
                    raise ValueError("lab_private_timing_invalid")
                try:
                    p50_number = float(p50)
                    p95_number = float(p95)
                except (OverflowError, ValueError):
                    raise ValueError("lab_private_timing_invalid") from None
                if (
                    not math.isfinite(p50_number)
                    or not math.isfinite(p95_number)
                    or not 0 <= p50_number <= p95_number <= maximum
                ):
                    raise ValueError("lab_private_timing_invalid")
                normalized_metrics[name] = {
                    "sampleCount": count,
                    "p50": p50_number,
                    "p95": p95_number,
                }
            required_counts = {
                normalized_metrics[name]["sampleCount"]
                for name in PRIVATE_TIMING_REQUIRED_METRICS
            }
            if len(required_counts) != 1 or any(
                summary["sampleCount"] != next(iter(required_counts))
                for summary in normalized_metrics.values()
            ):
                raise ValueError("lab_private_timing_invalid")
            normalized_cohorts[cohort] = normalized_metrics
        if set(normalized_cohorts["afterActivation"]) != set(
            normalized_cohorts["resident"]
        ):
            raise ValueError("lab_private_timing_invalid")
        normalized[condition] = normalized_cohorts
    if any(
        set(normalized["baseline"][cohort])
        != set(normalized["candidate"][cohort])
        or any(
            normalized["baseline"][cohort][name]["sampleCount"]
            != normalized["candidate"][cohort][name]["sampleCount"]
            for name in normalized["baseline"][cohort]
        )
        for cohort in PRIVATE_TIMING_COHORTS
    ):
        raise ValueError("lab_private_timing_invalid")
    return normalized


def _worker_env() -> dict[str, str]:
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


def _assign_windows_kill_job(process: subprocess.Popen[bytes]) -> Any:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class IoCounters(ctypes.Structure):
        _fields_ = tuple((name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        ))

    class ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError("lab_worker_job_unavailable")
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel32.CloseHandle(job)
        raise OSError("lab_worker_job_unavailable")
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(int(process._handle))):
        kernel32.CloseHandle(job)
        raise OSError("lab_worker_job_unavailable")
    return job


def _kill_worker_tree(process: subprocess.Popen[bytes], windows_job: Any) -> None:
    if os.name == "nt":
        if windows_job:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle(windows_job)
        elif process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            if process.poll() is None:
                process.kill()
    try:
        process.wait(timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _invoke_worker(
    mode: str,
    request: Mapping[str, Any],
    *,
    timeout_s: float,
) -> Mapping[str, Any]:
    encoded = json.dumps(
        request,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process: subprocess.Popen[bytes] | None = None
    windows_job: Any = None
    output = b""
    worker_failed = False
    try:
        process = subprocess.Popen(
            (sys.executable, "-I", str(LAB_WORKER_SCRIPT)),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=_worker_env(),
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        windows_job = _assign_windows_kill_job(process)
        output, _ = process.communicate(input=encoded, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired):
        worker_failed = True
    finally:
        if process is not None:
            try:
                _kill_worker_tree(process, windows_job)
            except (OSError, RuntimeError, ValueError):
                worker_failed = True
    if worker_failed or process is None:
        raise RuntimeError("lab_worker_unavailable") from None
    if process.returncode != 0 or not output or len(output) > MAX_WORKER_OUTPUT_BYTES:
        raise RuntimeError("lab_worker_failed")
    try:
        parsed = json.loads(output.decode("ascii"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RuntimeError("lab_worker_malformed") from None
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"schema", "mode", "result"}
        or parsed["schema"] != LAB_WORKER_RESPONSE_SCHEMA
        or parsed["mode"] != mode
        or not isinstance(parsed["result"], dict)
    ):
        raise RuntimeError("lab_worker_malformed")
    return parsed["result"]


def _invoke_owned_lab_worker(mode: str, plan: RunnerPlan) -> Mapping[str, Any]:
    if mode not in {"preflight", "run", "cleanup"}:
        raise ValueError("lab_worker_mode_invalid")
    return _invoke_worker(
        mode,
        {
            "schema": LAB_WORKER_REQUEST_SCHEMA,
            "mode": mode,
            "plan": plan.to_dict(),
        },
        timeout_s=(
            180.0 if mode != "run" else (plan.spec.max_runtime_ms / 1000.0) + 300.0
        ),
    )


def _invoke_reconcile_worker() -> Mapping[str, Any]:
    return _invoke_worker(
        "reconcile",
        {
            "schema": LAB_WORKER_REQUEST_SCHEMA,
            "mode": "reconcile",
        },
        timeout_s=180.0,
    )


def _invoke_identity_worker(baseline: MainLatencyConfig) -> Mapping[str, Any]:
    return _invoke_worker(
        "discover",
        {
            "schema": LAB_WORKER_REQUEST_SCHEMA,
            "mode": "discover",
            "baselineConfig": baseline.to_dict(),
        },
        timeout_s=180.0,
    )


def _cleanup_from_run_id(value: Any, run_id: str) -> dict[str, Any]:
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
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != CLEANUP_SCHEMA
        or value.get("runId") != run_id
        or value.get("owner") != LAB_OWNER
        or value.get("status") not in {"clean", "cleanup_required"}
        or any(
            type(value.get(key)) is not int or not 0 <= value[key] <= maximum
            for key, maximum in (
                ("remainingProcesses", 8),
                ("remainingGpuAllocations", 4),
                ("remainingArtifacts", 64),
            )
        )
        or (
            value.get("status") == "clean"
            and any(value[key] for key in fields if key.startswith("remaining"))
        )
    ):
        raise ValueError("lab_cleanup_invalid")
    return dict(value)


def _cleanup_from(value: Any, plan: RunnerPlan) -> dict[str, Any]:
    return _cleanup_from_run_id(value, plan.run_id)


def _unknown_cleanup_for_run_id(run_id: str) -> dict[str, Any]:
    return {
        "schema": CLEANUP_SCHEMA,
        "runId": run_id,
        "owner": LAB_OWNER,
        "status": "cleanup_required",
        "remainingProcesses": 1,
        "remainingGpuAllocations": 1,
        "remainingArtifacts": 1,
    }


def _unknown_cleanup(plan: RunnerPlan) -> dict[str, Any]:
    return _unknown_cleanup_for_run_id(plan.run_id)


def _failure_receipt(
    plan: RunnerPlan,
    *,
    status: str,
    cleanup: Mapping[str, Any],
) -> dict[str, Any]:
    if status not in {
        "runner_failed",
        "timed_out",
        "cancelled",
        "ambiguous",
        "environment_drift",
    } | set(LAB_PREFLIGHT_FAILURE_CODES):
        raise ValueError("lab_failure_status_invalid")
    return {
        "schema": RUNNER_RECEIPT_SCHEMA,
        "runId": plan.run_id,
        "candidateId": plan.candidate.candidate_id,
        "identities": plan.candidate.identities.to_dict(),
        "baselineConfig": plan.candidate.baseline_config.to_dict(),
        "candidateConfig": plan.candidate.candidate_config.to_dict(),
        "status": status,
        "samples": {key: 0 for key in SAMPLE_FIELDS},
        "baselineMetrics": {key: 0.0 for key in METRIC_FIELDS},
        "candidateMetrics": {key: 0.0 for key in METRIC_FIELDS},
        "statistics": {
            "schema": STATISTICS_SCHEMA,
            "method": "paired-bootstrap-abba-v1",
            "bootstrapReplicates": 1,
            "confidenceLevel": 0.95,
            "warmAnswerFirstPcmP95DeltaCiLowMs": 0.0,
            "warmAnswerFirstPcmP95DeltaCiHighMs": 0.0,
            "warmAnswerFirstPcmP95EffectSize": 0.0,
        },
        "checks": {key: 1 if key == "errorCount" else 0 for key in CHECK_FIELDS},
        "equivalence": {"comparisons": 0, "matches": 0},
        "resources": {
            "runtimeMs": 0,
            "artifactBytes": 0,
            "peakHostRamMiB": 0,
            "maxConcurrentRequests": 0,
        },
        "cleanup": dict(cleanup),
    }


class OwnedDockerLabAdapter:
    """Installed adapter for the fixed, externally-egress-disabled Docker lab."""

    __slots__ = ("_worker",)

    def __init__(self, worker: WorkerCall = _invoke_owned_lab_worker) -> None:
        if not callable(worker):
            raise TypeError("lab_worker_invalid")
        self._worker = worker

    def preflight(self, plan: RunnerPlan) -> LabPreflight:
        if not isinstance(plan, RunnerPlan):
            raise TypeError("runner_plan_invalid")
        try:
            result = self._worker("preflight", plan)
            if set(result) != {"ready", "code"}:
                raise ValueError("lab_preflight_invalid")
            return LabPreflight(result["ready"], result["code"])
        except (RuntimeError, TypeError, ValueError):
            return LabPreflight(False, "lab_isolation_preflight_failed")

    def run(self, plan: RunnerPlan) -> Mapping[str, Any]:
        receipt, _ = self.run_with_diagnostics(plan, require_diagnostics=False)
        return receipt

    def run_with_diagnostics(
        self,
        plan: RunnerPlan,
        *,
        require_diagnostics: bool = True,
    ) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        if not isinstance(plan, RunnerPlan):
            raise TypeError("runner_plan_invalid")
        try:
            result = self._worker("run", plan)
            expected = {"receipt", "timingDiagnostics"}
            if (
                type(result) is not dict
                or type(result.get("receipt")) is not dict
                or set(result) not in ({"receipt"}, expected)
                or (require_diagnostics and set(result) != expected)
            ):
                raise ValueError("lab_receipt_invalid")
            diagnostics = normalize_private_timing_diagnostics(
                result.get("timingDiagnostics", {})
            )
            if (
                require_diagnostics
                and result["receipt"].get("status") == "completed"
                and not diagnostics
            ):
                raise ValueError("lab_private_timing_invalid")
            return result["receipt"], diagnostics
        except (RuntimeError, TypeError, ValueError):
            cleanup = _unknown_cleanup(plan)
            try:
                cleanup_result = self._worker("cleanup", plan)
                if set(cleanup_result) == {"cleanup"}:
                    cleanup = _cleanup_from(cleanup_result["cleanup"], plan)
            except (RuntimeError, TypeError, ValueError):
                pass
            return (
                _failure_receipt(plan, status="runner_failed", cleanup=cleanup),
                {},
            )

    def cleanup(self, plan: RunnerPlan) -> Mapping[str, Any]:
        if not isinstance(plan, RunnerPlan):
            raise TypeError("runner_plan_invalid")
        try:
            result = self._worker("cleanup", plan)
            if set(result) != {"cleanup"}:
                raise ValueError("lab_cleanup_invalid")
            return _cleanup_from(result["cleanup"], plan)
        except (RuntimeError, TypeError, ValueError):
            return _unknown_cleanup(plan)


FIXED_LAB_ADAPTER: FixedLabAdapter = OwnedDockerLabAdapter()


def get_fixed_lab_adapter() -> FixedLabAdapter:
    return FIXED_LAB_ADAPTER


def cleanup_owned_lab(plan: RunnerPlan) -> dict[str, Any]:
    """Best-effort exact-owner cleanup for a killed fixed-lab transport."""

    if not isinstance(plan, RunnerPlan):
        raise TypeError("runner_plan_invalid")
    return dict(OwnedDockerLabAdapter().cleanup(plan))


def reconcile_owned_lab() -> dict[str, Any]:
    """Reconcile every prior owned-lab run before releasing the campaign fence."""

    try:
        result = _invoke_reconcile_worker()
        if set(result) != {"cleanup"}:
            raise ValueError("lab_cleanup_invalid")
        return _cleanup_from_run_id(result["cleanup"], GLOBAL_RECONCILE_RUN_ID)
    except (RuntimeError, TypeError, ValueError):
        return _unknown_cleanup_for_run_id(GLOBAL_RECONCILE_RUN_ID)


def discover_owned_lab_identities(
    baseline_config: MainLatencyConfig,
) -> IdentitySet:
    """Read the six fixed lab identities without creating Docker resources."""

    if not isinstance(baseline_config, MainLatencyConfig):
        raise TypeError("lab_baseline_config_invalid")
    baseline = MainLatencyConfig.from_mapping(baseline_config.to_dict())
    try:
        result = _invoke_identity_worker(baseline)
        if set(result) == {"errorCode"}:
            raise LabIdentityDiscoveryError(result["errorCode"])
        if set(result) != {"identities"}:
            raise ValueError("lab_identity_discovery_invalid")
        return IdentitySet.from_mapping(result["identities"])
    except LabIdentityDiscoveryError:
        raise
    except (RuntimeError, TypeError, ValueError):
        raise LabIdentityDiscoveryError("lab_isolation_preflight_failed") from None
