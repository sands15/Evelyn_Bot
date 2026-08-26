"""Run one operator-only Main-latency diagnostic in the fixed owned lab."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_fixed_lab_adapter as adapter
import main_latency_campaign_lock as campaign_lock
import main_latency_owned_lab_worker as worker
import optimize_main_latency as optimizer


DEFAULT_CONFIG = optimizer.MainLatencyConfig(2048, 2048, 256, 8192, 1, 0)
_RESULT_FIELDS = {
    "schema",
    "status",
    "config",
    "e2e",
    "backendObservation",
    "observations",
    "cleanup",
}
_COHORTS = ("firstAfterWarmup", "resident", "afterIdle")
_PHASES = ("cold", "capture", "resident", "afterIdle")
_CONTROLS = ("graphsOff", "graphsOn")
_EXPECTED_E2E_COUNTS = {
    "firstAfterWarmup": 1,
    "resident": worker.SHORT_DIAGNOSTIC_RESIDENT_SAMPLES,
    "afterIdle": 1,
}
_STATUS = frozenset(
    {
        "completed",
        "invariant_failed",
        "worker_failed",
        "runner_failed",
        "candidate_failed",
        "environment_drift",
        "timed_out",
        "cleanup_required",
        "lab_identity_preflight_failed",
        "lab_isolation_preflight_failed",
        "owned_lab_campaign_locked",
    }
)
_SUMMARY_BOUNDS = {
    "promptEvalMs": 30_000.0,
    "promptCacheHitRatio": 1.0,
    "promptTokensProcessed": 1_000_000.0,
    "promptTokensCached": 1_000_000.0,
    "promptTokensTotal": 1_000_000.0,
    "queueMs": 30_000.0,
    "routeMs": 30_000.0,
    "contextMs": 30_000.0,
    "firstTokenMs": 30_000.0,
    "safePrefixCommitMs": 30_000.0,
    "ttsFirstPcmMs": 30_000.0,
    "answerFirstPcmMs": 30_000.0,
    "predictedTokens": 1_000_000.0,
    "predictedMs": 30_000.0,
    "predictedTokensPerSec": 1_000_000.0,
}
_ORDERED_SAMPLE_BOUNDS = {
    "promptEvalMs": (30_000.0, False),
    "promptCacheHitRatio": (1.0, False),
    "promptTokensProcessed": (1_000_000.0, True),
    "promptTokensCached": (1_000_000.0, True),
    "promptTokensTotal": (1_000_000.0, True),
    "firstTokenMs": (30_000.0, False),
    "safePrefixCommitMs": (30_000.0, False),
    "ttsFirstPcmMs": (30_000.0, False),
    "answerFirstPcmMs": (30_000.0, False),
}
_REQUIRED_SUMMARY = frozenset(
    {
        "promptEvalMs",
        "promptCacheHitRatio",
        "promptTokensProcessed",
        "promptTokensCached",
        "promptTokensTotal",
        "firstTokenMs",
        "safePrefixCommitMs",
        "ttsFirstPcmMs",
        "answerFirstPcmMs",
    }
)
_DIRECT_BOUNDS = {
    "firstTokenMs": 30_000.0,
    "promptEvalMs": 30_000.0,
    "promptCacheHitRatio": 1.0,
    "promptTokensProcessed": 1_000_000.0,
    "promptTokensCached": 1_000_000.0,
    "promptTokensTotal": 1_000_000.0,
}
_INVARIANTS = {
    "payloadExact",
    "promptTotalsExact",
    "residentKvExactAcrossIdle",
    "controlsComparable",
}


def _request(mode: str, config: optimizer.MainLatencyConfig) -> dict[str, Any]:
    return {
        "schema": adapter.LAB_WORKER_REQUEST_SCHEMA,
        "mode": mode,
        "config": config.to_dict(),
    }


def _unknown_cleanup() -> dict[str, Any]:
    return {
        "status": "cleanup_required",
        "remainingProcesses": 1,
        "remainingGpuAllocations": 1,
        "remainingArtifacts": 1,
    }


def _terminal_cleanup(config: optimizer.MainLatencyConfig) -> dict[str, Any]:
    try:
        result = adapter._invoke_worker(
            "short_diagnostic_cleanup",
            _request("short_diagnostic_cleanup", config),
            timeout_s=180.0,
        )
        cleanup = result.get("cleanup")
        if isinstance(cleanup, dict):
            return _cleanup(worker._public_cleanup(cleanup))
    except (RuntimeError, TypeError, ValueError):
        pass
    return _unknown_cleanup()


def _reconcile_cleanup() -> dict[str, Any]:
    try:
        return _cleanup(worker._public_cleanup(adapter.reconcile_owned_lab()))
    except (RuntimeError, TypeError, ValueError):
        return _unknown_cleanup()


def _number(value: Any, maximum: float, *, integral: bool = False) -> float | int:
    if type(value) not in (int, float) or (integral and type(value) is not int):
        raise ValueError("short_diagnostic_malformed")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("short_diagnostic_malformed") from exc
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise ValueError("short_diagnostic_malformed")
    if integral:
        return int(number)
    return number


def _summary(value: Any, expected_count: int) -> dict[str, Any]:
    if expected_count == 0:
        if value != {}:
            raise ValueError("short_diagnostic_malformed")
        return {}
    if (
        type(value) is not dict
        or not _REQUIRED_SUMMARY.issubset(value)
        or not set(value).issubset(_SUMMARY_BOUNDS)
    ):
        raise ValueError("short_diagnostic_malformed")
    result: dict[str, Any] = {}
    for name, raw in value.items():
        if type(raw) is not dict or set(raw) != {"sampleCount", "p50", "p95"}:
            raise ValueError("short_diagnostic_malformed")
        if type(raw.get("sampleCount")) is not int or raw["sampleCount"] != expected_count:
            raise ValueError("short_diagnostic_malformed")
        p50 = _number(raw.get("p50"), _SUMMARY_BOUNDS[name])
        p95 = _number(raw.get("p95"), _SUMMARY_BOUNDS[name])
        if p50 > p95:
            raise ValueError("short_diagnostic_malformed")
        if name == "promptTokensTotal" and p50 < 1:
            raise ValueError("short_diagnostic_malformed")
        result[name] = {"sampleCount": expected_count, "p50": p50, "p95": p95}
    return result


def _ordered_samples(value: Any, expected_count: int) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) != expected_count:
        raise ValueError("short_diagnostic_malformed")
    fields = {"ordinal", *_ORDERED_SAMPLE_BOUNDS}
    result: list[dict[str, Any]] = []
    for ordinal, raw in enumerate(value, 1):
        if type(raw) is not dict or set(raw) != fields or raw.get("ordinal") != ordinal:
            raise ValueError("short_diagnostic_malformed")
        sample = {
            name: _number(raw[name], maximum, integral=integral)
            for name, (maximum, integral) in _ORDERED_SAMPLE_BOUNDS.items()
        }
        if (
            sample["promptTokensTotal"] < 1
            or sample["promptTokensProcessed"] + sample["promptTokensCached"]
            != sample["promptTokensTotal"]
            or not math.isclose(
                sample["promptCacheHitRatio"],
                sample["promptTokensCached"] / sample["promptTokensTotal"],
                rel_tol=0.0,
                abs_tol=0.000051,
            )
        ):
            raise ValueError("short_diagnostic_malformed")
        result.append({"ordinal": ordinal, **sample})
    return result


def _ordered_samples_match_summary(
    samples: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]
) -> bool:
    if not samples:
        return summary == {}
    for name in _ORDERED_SAMPLE_BOUNDS:
        values = sorted(float(sample[name]) for sample in samples)
        p50 = (
            values[len(values) // 2]
            if len(values) % 2
            else (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2.0
        )
        p95 = values[max(0, math.ceil(len(values) * 0.95) - 1)]
        raw = summary.get(name)
        if not isinstance(raw, Mapping) or not (
            math.isclose(float(raw["p50"]), p50, rel_tol=0.0, abs_tol=1e-9)
            and math.isclose(float(raw["p95"]), p95, rel_tol=0.0, abs_tol=1e-9)
        ):
            return False
    return True


def _direct_sample(value: Any, expected_count: int) -> dict[str, Any]:
    if expected_count == 0:
        if value != {}:
            raise ValueError("short_diagnostic_malformed")
        return {}
    if type(value) is not dict or set(value) != set(_DIRECT_BOUNDS):
        raise ValueError("short_diagnostic_malformed")
    result = {
        name: _number(
            value[name],
            maximum,
            integral=name.startswith("promptTokens"),
        )
        for name, maximum in _DIRECT_BOUNDS.items()
    }
    if (
        result["promptTokensTotal"] < 1
        or
        result["promptTokensProcessed"] + result["promptTokensCached"]
        != result["promptTokensTotal"]
        or not math.isclose(
            result["promptCacheHitRatio"],
            result["promptTokensCached"] / result["promptTokensTotal"],
            rel_tol=0.0,
            abs_tol=0.000051,
        )
    ):
        raise ValueError("short_diagnostic_malformed")
    return result


def _cleanup(value: Any) -> dict[str, Any]:
    fields = {
        "status",
        "remainingProcesses",
        "remainingGpuAllocations",
        "remainingArtifacts",
    }
    if type(value) is not dict or set(value) != fields:
        raise ValueError("short_diagnostic_malformed")
    status = value.get("status")
    if type(status) is not str or status not in {"clean", "cleanup_required"}:
        raise ValueError("short_diagnostic_malformed")
    counts = (
        value.get("remainingProcesses"),
        value.get("remainingGpuAllocations"),
        value.get("remainingArtifacts"),
    )
    if any(type(count) is not int or not 0 <= count <= 64 for count in counts) or (
        status == "clean"
    ) != (sum(counts) == 0):
        raise ValueError("short_diagnostic_malformed")
    return {
        "status": status,
        "remainingProcesses": _number(counts[0], 8, integral=True),
        "remainingGpuAllocations": _number(counts[1], 4, integral=True),
        "remainingArtifacts": _number(counts[2], 64, integral=True),
    }


def normalize_result(value: Any) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value) != _RESULT_FIELDS
        or type(value.get("schema")) is not str
        or value.get("schema") != worker.SHORT_DIAGNOSTIC_SCHEMA
        or type(value.get("status")) is not str
        or value.get("status") not in _STATUS
    ):
        raise ValueError("short_diagnostic_malformed")
    raw_config = value.get("config")
    if type(raw_config) is not dict or any(
        type(config_value) is not int for config_value in raw_config.values()
    ):
        raise ValueError("short_diagnostic_malformed")
    config = optimizer.MainLatencyConfig.from_mapping(raw_config).to_dict()
    raw_e2e = value.get("e2e")
    if (
        type(raw_e2e) is not dict
        or set(raw_e2e)
        != {"causal", "idleSeconds", "samples", "measurements", "orderedSamples"}
        or raw_e2e.get("causal") is not False
    ):
        raise ValueError("short_diagnostic_malformed")
    if float(_number(raw_e2e.get("idleSeconds"), 60.0)) <= 11.0:
        raise ValueError("short_diagnostic_malformed")
    raw_counts = raw_e2e.get("samples")
    raw_measurements = raw_e2e.get("measurements")
    raw_ordered_samples = raw_e2e.get("orderedSamples")
    if (
        type(raw_counts) is not dict
        or set(raw_counts) != set(_COHORTS)
        or type(raw_measurements) is not dict
        or set(raw_measurements) != set(_COHORTS)
        or type(raw_ordered_samples) is not dict
        or set(raw_ordered_samples) != set(_COHORTS)
    ):
        raise ValueError("short_diagnostic_malformed")
    counts: dict[str, int] = {}
    measurements: dict[str, Any] = {}
    ordered_samples: dict[str, Any] = {}
    for cohort in _COHORTS:
        count = _number(
            raw_counts[cohort], _EXPECTED_E2E_COUNTS[cohort], integral=True
        )
        counts[cohort] = int(count)
        measurements[cohort] = _summary(raw_measurements[cohort], int(count))
        ordered_samples[cohort] = _ordered_samples(
            raw_ordered_samples[cohort], int(count)
        )
        if not _ordered_samples_match_summary(
            ordered_samples[cohort], measurements[cohort]
        ):
            raise ValueError("short_diagnostic_malformed")
    zero_e2e_counts = {cohort: 0 for cohort in _COHORTS}
    if counts not in (zero_e2e_counts, _EXPECTED_E2E_COUNTS):
        raise ValueError("short_diagnostic_malformed")
    if counts == _EXPECTED_E2E_COUNTS and len(
        {frozenset(measurements[cohort]) for cohort in _COHORTS}
    ) != 1:
        raise ValueError("short_diagnostic_malformed")

    raw_backend = value.get("backendObservation")
    backend_fields = {
        "causal",
        "idleSeconds",
        "samplesPerControl",
        "graphsOff",
        "graphsOn",
        "invariants",
    }
    if (
        type(raw_backend) is not dict
        or set(raw_backend) != backend_fields
        or raw_backend.get("causal") is not False
    ):
        raise ValueError("short_diagnostic_malformed")
    if float(_number(raw_backend.get("idleSeconds"), 60.0)) <= 11.0:
        raise ValueError("short_diagnostic_malformed")
    phase_counts = raw_backend.get("samplesPerControl")
    if type(phase_counts) is not dict or set(phase_counts) != set(_PHASES):
        raise ValueError("short_diagnostic_malformed")
    normalized_phase_counts = {
        phase: int(_number(phase_counts[phase], 1, integral=True))
        for phase in _PHASES
    }
    zero_phase_counts = {phase: 0 for phase in _PHASES}
    full_phase_counts = {phase: 1 for phase in _PHASES}
    if normalized_phase_counts not in (zero_phase_counts, full_phase_counts):
        raise ValueError("short_diagnostic_malformed")
    controls: dict[str, Any] = {}
    for control in _CONTROLS:
        raw_control = raw_backend.get(control)
        if type(raw_control) is not dict or set(raw_control) != set(_PHASES):
            raise ValueError("short_diagnostic_malformed")
        controls[control] = {
            phase: _direct_sample(raw_control[phase], normalized_phase_counts[phase])
            for phase in _PHASES
        }
    raw_invariants = raw_backend.get("invariants")
    if (
        type(raw_invariants) is not dict
        or set(raw_invariants) != _INVARIANTS
        or any(type(raw_invariants[name]) is not bool for name in _INVARIANTS)
    ):
        raise ValueError("short_diagnostic_malformed")
    invariants = {name: raw_invariants[name] for name in _INVARIANTS}
    if normalized_phase_counts == zero_phase_counts:
        if any(invariants.values()):
            raise ValueError("short_diagnostic_malformed")
    else:
        direct_rows = [
            controls[control][phase]
            for control in _CONTROLS
            for phase in _PHASES
        ]
        derived = {
            "promptTotalsExact": len(
                {int(sample["promptTokensTotal"]) for sample in direct_rows}
            )
            == 1,
            "residentKvExactAcrossIdle": all(
                tuple(
                    int(controls[control][phase][name])
                    for name in (
                        "promptTokensProcessed",
                        "promptTokensCached",
                        "promptTokensTotal",
                    )
                )
                == tuple(
                    int(controls[control]["resident"][name])
                    for name in (
                        "promptTokensProcessed",
                        "promptTokensCached",
                        "promptTokensTotal",
                    )
                )
                for control in _CONTROLS
                for phase in ("resident", "afterIdle")
            ),
            "controlsComparable": all(
                tuple(
                    int(controls[control][phase][name])
                    for name in (
                        "promptTokensProcessed",
                        "promptTokensCached",
                        "promptTokensTotal",
                    )
                )
                == tuple(
                    int(controls["graphsOff"][phase][name])
                    for name in (
                        "promptTokensProcessed",
                        "promptTokensCached",
                        "promptTokensTotal",
                    )
                )
                for control in _CONTROLS
                for phase in _PHASES
            ),
        }
        if any(invariants[name] is not expected for name, expected in derived.items()):
            raise ValueError("short_diagnostic_malformed")
    raw_observations = value.get("observations")
    observation_fields = {
        "cacheProofChecks",
        "cacheProofFailures",
        "gpuMinFreeMiB",
        "gpuMaxUtilization",
        "peakHostRamMiB",
        "sampleValidityFailures",
        "runtimeMs",
    }
    if type(raw_observations) is not dict or set(raw_observations) != observation_fields:
        raise ValueError("short_diagnostic_malformed")
    observations = {
        "cacheProofChecks": _number(
            raw_observations["cacheProofChecks"], 10_000, integral=True
        ),
        "cacheProofFailures": _number(
            raw_observations["cacheProofFailures"], 10_000, integral=True
        ),
        "gpuMinFreeMiB": _number(raw_observations["gpuMinFreeMiB"], 1_000_000),
        "gpuMaxUtilization": _number(raw_observations["gpuMaxUtilization"], 100),
        "peakHostRamMiB": _number(
            raw_observations["peakHostRamMiB"], 1_000_000, integral=True
        ),
        "sampleValidityFailures": _number(
            raw_observations["sampleValidityFailures"], 10_000, integral=True
        ),
        "runtimeMs": _number(
            raw_observations["runtimeMs"],
            (worker.SHORT_DIAGNOSTIC_MAX_RUNTIME_S + 300.0) * 1000.0,
            integral=True,
        ),
    }
    if observations["cacheProofFailures"] > observations["cacheProofChecks"]:
        raise ValueError("short_diagnostic_malformed")
    cleanup = _cleanup(value.get("cleanup"))
    if value["status"] == "completed" and (
        counts != _EXPECTED_E2E_COUNTS
        or any(count != 1 for count in normalized_phase_counts.values())
        or not all(invariants.values())
        or observations["cacheProofFailures"] != 0
        or observations["sampleValidityFailures"] != 0
        or cleanup["status"] != "clean"
    ):
        raise ValueError("short_diagnostic_malformed")
    return {
        "schema": worker.SHORT_DIAGNOSTIC_SCHEMA,
        "status": value["status"],
        "config": config,
        "e2e": {
            "causal": False,
            "idleSeconds": float(raw_e2e["idleSeconds"]),
            "samples": counts,
            "measurements": measurements,
            "orderedSamples": ordered_samples,
        },
        "backendObservation": {
            "causal": False,
            "idleSeconds": float(raw_backend["idleSeconds"]),
            "samplesPerControl": normalized_phase_counts,
            **controls,
            "invariants": invariants,
        },
        "observations": observations,
        "cleanup": cleanup,
    }


def _failure_result(
    config: optimizer.MainLatencyConfig,
    cleanup: Mapping[str, Any],
    *,
    status: str = "worker_failed",
) -> dict[str, Any]:
    return {
        "schema": worker.SHORT_DIAGNOSTIC_SCHEMA,
        "status": status,
        "config": config.to_dict(),
        "e2e": {
            "causal": False,
            "idleSeconds": worker.SHORT_DIAGNOSTIC_IDLE_SECONDS,
            "samples": {cohort: 0 for cohort in _COHORTS},
            "measurements": {cohort: {} for cohort in _COHORTS},
            "orderedSamples": {cohort: [] for cohort in _COHORTS},
        },
        "backendObservation": {
            "causal": False,
            "idleSeconds": worker.SHORT_DIAGNOSTIC_IDLE_SECONDS,
            "samplesPerControl": {phase: 0 for phase in _PHASES},
            "graphsOff": {phase: {} for phase in _PHASES},
            "graphsOn": {phase: {} for phase in _PHASES},
            "invariants": {name: False for name in _INVARIANTS},
        },
        "observations": {
            "cacheProofChecks": 0,
            "cacheProofFailures": 0,
            "gpuMinFreeMiB": 0.0,
            "gpuMaxUtilization": 0.0,
            "peakHostRamMiB": 0,
            "sampleValidityFailures": 0,
            "runtimeMs": 0,
        },
        "cleanup": dict(cleanup),
    }


def _run_locked(config: optimizer.MainLatencyConfig) -> dict[str, Any]:
    try:
        result = normalize_result(dict(
            adapter._invoke_worker(
                "short_diagnostic",
                _request("short_diagnostic", config),
                timeout_s=worker.SHORT_DIAGNOSTIC_MAX_RUNTIME_S + 300.0,
            )
        ))
    except (RuntimeError, TypeError, ValueError):
        return normalize_result(_failure_result(config, _terminal_cleanup(config)))

    cleanup = result.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("status") != "clean":
        result["cleanup"] = _terminal_cleanup(config)
        if (
            result["cleanup"].get("status") == "clean"
            and result["status"] == "cleanup_required"
        ):
            result["status"] = "completed"
        elif (
            result["cleanup"].get("status") != "clean"
            and result["status"] == "completed"
        ):
            result["status"] = "cleanup_required"
    return normalize_result(result)


def run(config: optimizer.MainLatencyConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    try:
        with campaign_lock.OwnedLabCampaignLock():
            startup_cleanup = _reconcile_cleanup()
            if startup_cleanup.get("status") != "clean":
                return normalize_result(
                    _failure_result(
                        config,
                        startup_cleanup,
                        status="cleanup_required",
                    )
                )
            try:
                result = _run_locked(config)
            finally:
                terminal_cleanup = _reconcile_cleanup()
            result["cleanup"] = terminal_cleanup
            if (
                terminal_cleanup.get("status") != "clean"
                and result["status"] == "completed"
            ):
                result["status"] = "cleanup_required"
            return normalize_result(result)
    except RuntimeError as exc:
        if str(exc) != "owned_lab_campaign_locked":
            raise
        return normalize_result(
            _failure_result(
                config,
                _unknown_cleanup(),
                status="owned_lab_campaign_locked",
            )
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the fixed one-config Main-latency short diagnostic."
    )
    parser.add_argument("--swa-full", type=int, choices=(0, 1), default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(
        optimizer.MainLatencyConfig(
            DEFAULT_CONFIG.batch,
            DEFAULT_CONFIG.ubatch,
            DEFAULT_CONFIG.cache_reuse,
            DEFAULT_CONFIG.cache_ram_mib,
            DEFAULT_CONFIG.cuda_graph,
            args.swa_full,
        )
    )
    sys.stdout.write(
        json.dumps(
            result,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return int(
        result.get("status") != "completed"
        or not isinstance(result.get("cleanup"), Mapping)
        or result["cleanup"].get("status") != "clean"
    )


if __name__ == "__main__":
    raise SystemExit(main())
