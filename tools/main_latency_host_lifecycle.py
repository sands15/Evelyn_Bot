"""Bounded host lifecycle guard for the Main latency owned lab.

The runner proves exact-owned resource cleanup.  This guard separately restores
Docker Desktop and proves bounded host-wide GPU0 idle without attributing
unrelated WDDM allocations to the runner.
"""

from __future__ import annotations

import copy
import math
import os
import re
import statistics
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EVIDENCE_SCHEMA = "evelyn.latency-host-restoration-observation.v1"
RESTORE_SCHEMA = "evelyn.main-latency-host-lifecycle-restore.v1"
GPU_OBSERVATIONS = 3
GPU_MAX_UTILIZATION = 10.0
GPU_MIN_FREE_RATIO = 0.75
GPU_FREE_TOLERANCE_MIB = 256.0
GPU_TOTAL_TOLERANCE_MIB = 1.0
COMMAND_OUTPUT_LIMIT = 4096

_DOCKER_VERSION_ARGS = ("version", "--format", "{{.Server.Version}}")
_DOCKER_DESKTOP_STATUS_ARGS = ("desktop", "status")
_DOCKER_PS_ARGS = ("ps", "-q", "--no-trunc")
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}\Z", re.ASCII)
_GPU_QUERY_ARGS = (
    "--id=0",
    "--query-gpu=driver_model.current,utilization.gpu,memory.free,memory.total",
    "--format=csv,noheader,nounits",
)


class HostLifecycleError(RuntimeError):
    """A fixed, content-free host lifecycle failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _GpuObservation:
    driver_model: str
    utilization: float
    free_mib: float
    total_mib: float


@dataclass(frozen=True)
class _GpuBaseline:
    driver_model: str
    free_mib: float
    total_mib: float


CommandRunner = Callable[
    [tuple[str, ...], float], subprocess.CompletedProcess[str]
]


def _default_command_runner(
    argv: tuple[str, ...], timeout_s: float
) -> subprocess.CompletedProcess[str]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="strict",
        timeout=timeout_s,
        check=False,
        shell=False,
        close_fds=True,
        creationflags=creationflags,
    )


class MainLatencyHostLifecycle:
    """Restore Docker Desktop and GPU0 without changing user-owned workloads."""

    __slots__ = (
        "_docker",
        "_nvidia_smi",
        "_run_command",
        "_monotonic",
        "_sleep",
        "_command_timeout_s",
        "_docker_transition_timeout_s",
        "_gpu_restore_timeout_s",
        "_poll_interval_s",
        "_initial_docker_running",
        "_baseline",
        "_prepared",
        "_start_attempted",
        "_started_by_guard",
        "_stop_attempted",
        "_stopped_by_guard",
        "_final_evidence",
    )

    def __init__(
        self,
        docker_executable: str | Path,
        nvidia_smi_executable: str | Path,
        *,
        command_runner: CommandRunner = _default_command_runner,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        command_timeout_s: float = 30.0,
        docker_transition_timeout_s: float = 180.0,
        gpu_restore_timeout_s: float = 180.0,
        poll_interval_s: float = 1.0,
    ) -> None:
        docker = Path(docker_executable)
        nvidia_smi = Path(nvidia_smi_executable)
        numbers = (
            command_timeout_s,
            docker_transition_timeout_s,
            gpu_restore_timeout_s,
            poll_interval_s,
        )
        if (
            not docker.is_absolute()
            or not nvidia_smi.is_absolute()
            or not callable(command_runner)
            or not callable(monotonic)
            or not callable(sleeper)
            or any(not math.isfinite(value) or value <= 0 for value in numbers)
        ):
            raise ValueError("host_lifecycle_config_invalid")
        self._docker = str(docker)
        self._nvidia_smi = str(nvidia_smi)
        self._run_command = command_runner
        self._monotonic = monotonic
        self._sleep = sleeper
        self._command_timeout_s = float(command_timeout_s)
        self._docker_transition_timeout_s = float(docker_transition_timeout_s)
        self._gpu_restore_timeout_s = float(gpu_restore_timeout_s)
        self._poll_interval_s = float(poll_interval_s)
        self._initial_docker_running: bool | None = None
        self._baseline: _GpuBaseline | None = None
        self._prepared = False
        self._start_attempted = False
        self._started_by_guard = False
        self._stop_attempted = False
        self._stopped_by_guard = False
        self._final_evidence: dict[str, Any] | None = None

    def _command(
        self, executable: str, *args: str
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run_command(
                (executable, *args), self._command_timeout_s
            )
        except HostLifecycleError:
            raise
        except Exception:
            raise HostLifecycleError("host_command_failed") from None
        if (
            not isinstance(result, subprocess.CompletedProcess)
            or type(result.returncode) is not int
            or not isinstance(result.stdout, str)
            or not isinstance(result.stderr, str)
            or len(result.stdout.encode("utf-8"))
            + len(result.stderr.encode("utf-8"))
            > COMMAND_OUTPUT_LIMIT
        ):
            raise HostLifecycleError("host_command_failed")
        return result

    def _docker_state(self) -> str:
        result = self._command(self._docker, *_DOCKER_VERSION_ARGS)
        if result.returncode == 0:
            if not result.stdout.strip() or len(result.stdout.splitlines()) != 1:
                raise HostLifecycleError("docker_state_probe_failed")
            return "running"
        if result.returncode != 1:
            raise HostLifecycleError("docker_state_probe_failed")
        status = self._command(self._docker, *_DOCKER_DESKTOP_STATUS_ARGS)
        rendered = " ".join(
            f"{status.stdout}\n{status.stderr}".casefold().split()
        )
        stopped = status.returncode == 1 and (
            (
                "could not retrieve status" in rendered
                and "docker desktop" in rendered
            )
            or ("docker desktop" in rendered and "not running" in rendered)
        )
        if stopped:
            return "stopped"
        if status.returncode == 0 and rendered:
            if "stopping" in rendered:
                return "stopping"
            if "starting" in rendered:
                return "starting"
            if "running" in rendered:
                return "engine_unavailable"
        raise HostLifecycleError("docker_state_probe_failed")

    def _docker_running(self) -> bool:
        state = self._docker_state()
        if state not in {"running", "stopped"}:
            raise HostLifecycleError("docker_state_probe_failed")
        return state == "running"

    def probe_docker_state(self) -> str:
        """Return only a stable running/stopped state; ambiguity raises."""

        state = self._docker_state()
        if state not in {"running", "stopped"}:
            raise HostLifecycleError("docker_state_probe_failed")
        return state

    def _wait_for_docker(self, expected_running: bool) -> None:
        deadline = self._monotonic() + self._docker_transition_timeout_s
        while True:
            state = self._docker_state()
            if state == ("running" if expected_running else "stopped"):
                return
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise HostLifecycleError(
                    "docker_start_timeout"
                    if expected_running
                    else "docker_stop_timeout"
                )
            self._sleep(min(self._poll_interval_s, remaining))

    def _gpu_observation(self) -> _GpuObservation:
        result = self._command(self._nvidia_smi, *_GPU_QUERY_ARGS)
        raw = result.stdout.strip()
        if result.returncode != 0 or not raw or len(raw.splitlines()) != 1:
            raise HostLifecycleError("gpu_telemetry_failed")
        fields = [field.strip() for field in raw.split(",")]
        if len(fields) != 4 or not fields[0]:
            raise HostLifecycleError("gpu_telemetry_failed")
        try:
            utilization, free_mib, total_mib = map(float, fields[1:])
        except ValueError:
            raise HostLifecycleError("gpu_telemetry_failed") from None
        if (
            not all(math.isfinite(value) for value in (utilization, free_mib, total_mib))
            or not 0 <= utilization <= 100
            or total_mib <= 0
            or not 0 <= free_mib <= total_mib
        ):
            raise HostLifecycleError("gpu_telemetry_failed")
        return _GpuObservation(
            fields[0].casefold(), utilization, free_mib, total_mib
        )

    def _capture_baseline(self) -> _GpuBaseline:
        deadline = self._monotonic() + self._gpu_restore_timeout_s
        consecutive: list[_GpuObservation] = []
        while True:
            value = self._gpu_observation()
            candidate = [*consecutive, value]
            models = {item.driver_model for item in candidate}
            frees = [item.free_mib for item in candidate]
            totals = [item.total_mib for item in candidate]
            if (
                value.utilization <= GPU_MAX_UTILIZATION
                and len(models) == 1
                and max(frees) - min(frees) <= GPU_FREE_TOLERANCE_MIB
                and max(totals) - min(totals) <= GPU_TOTAL_TOLERANCE_MIB
            ):
                consecutive = candidate[-GPU_OBSERVATIONS:]
                if len(consecutive) == GPU_OBSERVATIONS:
                    return _GpuBaseline(
                        consecutive[0].driver_model,
                        min(item.free_mib for item in consecutive),
                        statistics.median(item.total_mib for item in consecutive),
                    )
            else:
                consecutive = (
                    [value]
                    if value.utilization <= GPU_MAX_UTILIZATION
                    else []
                )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise HostLifecycleError("gpu_baseline_unstable")
            self._sleep(min(self._poll_interval_s, remaining))

    def _matches_baseline(self, value: _GpuObservation) -> bool:
        baseline = self._baseline
        if baseline is None:
            return False
        return (
            value.driver_model == baseline.driver_model
            and abs(value.total_mib - baseline.total_mib)
            <= GPU_TOTAL_TOLERANCE_MIB
            and value.utilization <= GPU_MAX_UTILIZATION
            and value.free_mib / value.total_mib >= GPU_MIN_FREE_RATIO
        )

    def _wait_for_gpu_restore(self) -> tuple[_GpuObservation, ...]:
        deadline = self._monotonic() + self._gpu_restore_timeout_s
        consecutive: list[_GpuObservation] = []
        while True:
            value = self._gpu_observation()
            if self._matches_baseline(value):
                consecutive.append(value)
                if len(consecutive) == GPU_OBSERVATIONS:
                    return tuple(consecutive)
            else:
                consecutive.clear()
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise HostLifecycleError("gpu_restore_timeout")
            self._sleep(min(self._poll_interval_s, remaining))

    def _global_running_container_count(self) -> int:
        result = self._command(self._docker, *_DOCKER_PS_ARGS)
        if result.returncode != 0:
            raise HostLifecycleError("docker_ps_failed")
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if any(_CONTAINER_ID.fullmatch(value) is None for value in values):
            raise HostLifecycleError("docker_ps_failed")
        return len(values)

    def _require_global_docker_ps_empty(self) -> None:
        if self._global_running_container_count() != 0:
            raise HostLifecycleError("docker_not_empty")

    def _start_docker(self) -> None:
        self._start_attempted = True
        result = self._command(self._docker, "desktop", "start")
        if result.returncode != 0:
            raise HostLifecycleError("docker_start_failed")
        self._wait_for_docker(True)
        self._started_by_guard = True

    def _stop_docker(self) -> None:
        self._stop_attempted = True
        result = self._command(self._docker, "desktop", "stop")
        if result.returncode != 0:
            raise HostLifecycleError("docker_stop_failed")
        self._wait_for_docker(False)
        self._stopped_by_guard = True

    def prepare(self) -> None:
        """Capture the original host state, then make Docker available."""

        if self._prepared or self._initial_docker_running is not None:
            raise HostLifecycleError("host_lifecycle_already_prepared")
        self._initial_docker_running = self._docker_running()
        self._baseline = self._capture_baseline()
        self._prepared = True
        if not self._initial_docker_running:
            self._start_docker()

    def verify_measurement_preflight(self) -> None:
        """Fail before measurement unless the reconciled Docker lab is empty."""

        if not self._prepared:
            raise HostLifecycleError("host_lifecycle_not_prepared")
        if not self._docker_running():
            raise HostLifecycleError("docker_state_changed")
        self._require_global_docker_ps_empty()

    def _clean_evidence(
        self,
        post: tuple[_GpuObservation, ...],
        *,
        global_running_containers: int,
    ) -> dict[str, Any]:
        baseline = self._baseline
        assert baseline is not None
        state = "running" if self._initial_docker_running else "stopped"
        return {
            "schema": EVIDENCE_SCHEMA,
            "status": "clean",
            "dockerInitialState": state,
            "dockerFinalState": state,
            "dockerStartedByRun": self._started_by_guard,
            "driverModel": baseline.driver_model,
            "baselineFreeMiB": round(baseline.free_mib, 3),
            "postFreeMinMiB": round(min(value.free_mib for value in post), 3),
            "totalMiB": round(baseline.total_mib, 3),
            "maxUtilizationPct": round(
                max(value.utilization for value in post), 3
            ),
            "stableObservations": len(post),
            "globalRunningContainers": global_running_containers,
        }

    def finish_after_owned_cleanup(self) -> Mapping[str, Any]:
        """Restore the original Docker state and prove three clean GPU samples."""

        if not self._prepared or self._initial_docker_running is None:
            raise HostLifecycleError("host_lifecycle_not_prepared")
        if self._final_evidence is not None:
            return copy.deepcopy(self._final_evidence)
        global_running_containers: int
        if self._initial_docker_running:
            if not self._docker_running():
                raise HostLifecycleError("docker_state_changed")
        else:
            if not self._docker_running():
                raise HostLifecycleError("docker_state_changed")
            global_running_containers = self._global_running_container_count()
            if global_running_containers:
                raise HostLifecycleError("docker_not_empty")
            self._stop_docker()
        post = self._wait_for_gpu_restore()
        if self._docker_running() is not self._initial_docker_running:
            raise HostLifecycleError("docker_state_changed")
        if self._initial_docker_running:
            global_running_containers = self._global_running_container_count()
            if global_running_containers:
                raise HostLifecycleError("docker_not_empty")
        self._final_evidence = self._clean_evidence(
            post, global_running_containers=global_running_containers
        )
        return copy.deepcopy(self._final_evidence)

    def _restore_evidence(
        self,
        *,
        status: str,
        code: str,
        final_running: bool | None,
        gpu_restored: bool,
    ) -> dict[str, Any]:
        initial = self._initial_docker_running
        return {
            "schema": RESTORE_SCHEMA,
            "status": status,
            "code": code,
            "dockerInitialState": (
                "unknown" if initial is None else ("on" if initial else "off")
            ),
            "dockerFinalState": (
                "unknown"
                if final_running is None
                else ("on" if final_running else "off")
            ),
            "startAttempted": self._start_attempted,
            "stopAttempted": self._stop_attempted,
            "gpuRestored": gpu_restored,
        }

    def best_effort_restore(self) -> Mapping[str, Any]:
        """Never raise; undo only a Docker start this guard could have caused."""

        if self._initial_docker_running is None:
            return self._restore_evidence(
                status="blocked",
                code="initial_state_unknown",
                final_running=None,
                gpu_restored=False,
            )
        if self._baseline is None:
            try:
                final_running: bool | None = self._docker_running()
            except HostLifecycleError:
                final_running = None
            return self._restore_evidence(
                status="blocked",
                code="gpu_baseline_unavailable",
                final_running=final_running,
                gpu_restored=False,
            )
        try:
            state = self._docker_state()
            if self._initial_docker_running:
                if state != "running":
                    raise HostLifecycleError("docker_state_changed")
            elif state != "stopped":
                if not self._start_attempted:
                    raise HostLifecycleError("docker_state_changed")
                if state != "running":
                    self._wait_for_docker(True)
                self._require_global_docker_ps_empty()
                self._stop_docker()
            post = self._wait_for_gpu_restore()
            running = self._docker_running()
            if running is not self._initial_docker_running:
                raise HostLifecycleError("docker_state_changed")
            if running:
                self._require_global_docker_ps_empty()
            return self._restore_evidence(
                status="clean",
                code="restored",
                final_running=running,
                gpu_restored=len(post) == GPU_OBSERVATIONS,
            )
        except HostLifecycleError as exc:
            try:
                final_running: bool | None = self._docker_running()
            except HostLifecycleError:
                final_running = None
            return self._restore_evidence(
                status="blocked",
                code=exc.code,
                final_running=final_running,
                gpu_restored=False,
            )
