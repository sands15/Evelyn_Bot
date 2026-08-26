from __future__ import annotations

import json
import subprocess
import sys
import unittest
from collections import deque
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_host_lifecycle as lifecycle  # noqa: E402


DOCKER = Path("C:/fixed/docker.exe")
NVIDIA_SMI = Path("C:/fixed/nvidia-smi.exe")
VERSION = (str(DOCKER), "version", "--format", "{{.Server.Version}}")
STATUS = (str(DOCKER), "desktop", "status")
PS = (str(DOCKER), "ps", "-q", "--no-trunc")
START = (str(DOCKER), "desktop", "start")
STOP = (str(DOCKER), "desktop", "stop")
GPU = (
    str(NVIDIA_SMI),
    "--id=0",
    "--query-gpu=driver_model.current,utilization.gpu,memory.free,memory.total",
    "--format=csv,noheader,nounits",
)


def completed(argv: tuple[str, ...], returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, "")


def gpu(free_mib: float = 30_500.0, utilization: float = 1.0) -> str:
    return f"WDDM, {utilization}, {free_mib}, 32607\n"


def docker_off() -> list[tuple[tuple[str, ...], int, str]]:
    return [
        (VERSION, 1, ""),
        (
            STATUS,
            1,
            "Could not retrieve status. Is Docker Desktop running? "
            "You can start Docker Desktop by running 'docker desktop start'.\n",
        ),
    ]


class ScriptedRunner:
    def __init__(
        self,
        steps: list[tuple[tuple[str, ...], int, str]],
    ) -> None:
        self.steps = deque(steps)
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, argv: tuple[str, ...], _timeout_s: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if not self.steps:
            raise AssertionError(f"unexpected command: {argv!r}")
        expected, returncode, stdout = self.steps.popleft()
        if argv != expected:
            raise AssertionError(f"expected {expected!r}, got {argv!r}")
        return completed(argv, returncode, stdout)

    def assert_finished(self, test: unittest.TestCase) -> None:
        test.assertEqual(list(self.steps), [])


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def guard(
    runner: ScriptedRunner,
    clock: FakeClock | None = None,
) -> lifecycle.MainLatencyHostLifecycle:
    clock = clock or FakeClock()
    return lifecycle.MainLatencyHostLifecycle(
        DOCKER,
        NVIDIA_SMI,
        command_runner=runner,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
        docker_transition_timeout_s=3.0,
        gpu_restore_timeout_s=3.0,
        poll_interval_s=1.0,
    )


class MainLatencyHostLifecycleTests(unittest.TestCase):
    def test_initially_off_starts_then_stops_before_gpu_proof(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                *((GPU, 0, gpu()) for _ in range(3)),
                (START, 0, ""),
                (VERSION, 0, "29.6.1\n"),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
                (STOP, 0, ""),
                *docker_off(),
                *((GPU, 0, gpu(30_300.0, 2.0)) for _ in range(3)),
                *docker_off(),
            ]
        )
        subject = guard(runner)

        subject.prepare()
        evidence = subject.finish_after_owned_cleanup()

        self.assertEqual(
            set(evidence),
            {
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
            },
        )
        self.assertEqual(evidence["schema"], lifecycle.EVIDENCE_SCHEMA)
        self.assertEqual(evidence["status"], "clean")
        self.assertEqual(evidence["dockerInitialState"], "stopped")
        self.assertEqual(evidence["dockerFinalState"], "stopped")
        self.assertTrue(evidence["dockerStartedByRun"])
        self.assertEqual(evidence["globalRunningContainers"], 0)
        self.assertEqual(evidence["stableObservations"], 3)
        stop_index = runner.calls.index(STOP)
        first_post_gpu = next(
            index
            for index, command in enumerate(runner.calls)
            if index > stop_index and command == GPU
        )
        self.assertLess(stop_index, first_post_gpu)
        runner.assert_finished(self)

    def test_initially_on_never_starts_stops_or_restarts(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu(30_400.0)) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
            ]
        )
        subject = guard(runner)

        subject.prepare()
        evidence = subject.finish_after_owned_cleanup()

        self.assertEqual(evidence["dockerInitialState"], "running")
        self.assertEqual(evidence["dockerFinalState"], "running")
        self.assertFalse(evidence["dockerStartedByRun"])
        self.assertEqual(evidence["globalRunningContainers"], 0)
        self.assertNotIn(START, runner.calls)
        self.assertNotIn(STOP, runner.calls)
        self.assertEqual(runner.calls.count(PS), 1)
        runner.assert_finished(self)

    def test_initially_on_nonempty_docker_fails_without_mutation(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, "0123456789ab\n"),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_not_empty"
        ):
            subject.finish_after_owned_cleanup()

        self.assertNotIn(START, runner.calls)
        self.assertNotIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_initially_off_refuses_to_stop_with_any_running_container(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                *((GPU, 0, gpu()) for _ in range(3)),
                (START, 0, ""),
                (VERSION, 0, "29.6.1\n"),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, "0123456789ab\n"),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_not_empty"
        ):
            subject.finish_after_owned_cleanup()

        self.assertNotIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_post_gpu_proof_requires_three_consecutive_matches(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (GPU, 0, gpu(24_000.0)),
                *((GPU, 0, gpu(30_300.0)) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        evidence = subject.finish_after_owned_cleanup()

        self.assertEqual(evidence["stableObservations"], 3)
        self.assertEqual(runner.calls.count(GPU), 7)
        runner.assert_finished(self)

    def test_post_gpu_proof_accepts_unrelated_wddm_change_above_free_floor(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu(26_300.0)) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        evidence = subject.finish_after_owned_cleanup()

        self.assertEqual(evidence["baselineFreeMiB"], 30_500.0)
        self.assertEqual(evidence["postFreeMinMiB"], 26_300.0)
        self.assertGreaterEqual(
            evidence["postFreeMinMiB"] / evidence["totalMiB"],
            lifecycle.GPU_MIN_FREE_RATIO,
        )
        runner.assert_finished(self)

    def test_post_gpu_proof_rejects_free_below_floor(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu(24_000.0)) for _ in range(4)),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "gpu_restore_timeout"
        ):
            subject.finish_after_owned_cleanup()

        runner.assert_finished(self)

    def test_initially_on_engine_loss_fails_without_restart(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                *docker_off(),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_state_changed"
        ):
            subject.finish_after_owned_cleanup()

        self.assertNotIn(START, runner.calls[1:])
        self.assertNotIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_unstable_pre_gpu_samples_fail_before_docker_mutation(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                *((GPU, 0, gpu(utilization=11.0)) for _ in range(4)),
            ]
        )
        subject = guard(runner)

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "gpu_baseline_unstable"
        ):
            subject.prepare()

        self.assertNotIn(START, runner.calls)
        runner.assert_finished(self)

    def test_transient_gpu_noise_waits_for_three_sample_baseline(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                (GPU, 0, gpu(utilization=11.0)),
                (GPU, 0, gpu(utilization=8.0)),
                (GPU, 0, gpu(utilization=9.0)),
                (GPU, 0, gpu(utilization=8.0)),
            ]
        )
        subject = guard(runner)

        subject.prepare()

        self.assertEqual(runner.calls.count(GPU), 4)
        self.assertNotIn(START, runner.calls)
        runner.assert_finished(self)

    def test_measurement_preflight_rejects_nonempty_reconciled_docker(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, "0123456789ab\n"),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_not_empty"
        ):
            subject.verify_measurement_preflight()

        self.assertNotIn(START, runner.calls)
        self.assertNotIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_exit_one_with_running_desktop_is_unknown_not_off(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 1, ""),
                (STATUS, 0, "Docker Desktop is running\n"),
            ]
        )
        subject = guard(runner)

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_state_probe_failed"
        ):
            subject.prepare()

        self.assertNotIn(START, runner.calls)
        runner.assert_finished(self)

    def test_unknown_docker_probe_failure_is_not_treated_as_off(self) -> None:
        runner = ScriptedRunner([(VERSION, 2, "")])
        subject = guard(runner)

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_state_probe_failed"
        ):
            subject.prepare()

        self.assertNotIn(START, runner.calls)
        runner.assert_finished(self)

    def test_restore_after_failed_baseline_does_not_wait_or_mutate(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                (GPU, 0, "malformed\n"),
                *docker_off(),
            ]
        )
        subject = guard(runner)
        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "gpu_telemetry_failed"
        ):
            subject.prepare()

        restored = subject.best_effort_restore()

        self.assertEqual(restored["status"], "blocked")
        self.assertEqual(restored["code"], "gpu_baseline_unavailable")
        self.assertEqual(restored["dockerFinalState"], "off")
        self.assertNotIn(START, runner.calls)
        self.assertNotIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_best_effort_restore_stops_only_guard_started_empty_docker(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                *((GPU, 0, gpu()) for _ in range(3)),
                (START, 0, ""),
                (VERSION, 0, "29.6.1\n"),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
                (STOP, 0, ""),
                *docker_off(),
                *((GPU, 0, gpu(30_300.0)) for _ in range(3)),
                *docker_off(),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        restored = subject.best_effort_restore()

        self.assertEqual(restored["status"], "clean")
        self.assertEqual(restored["code"], "restored")
        self.assertEqual(restored["dockerFinalState"], "off")
        self.assertTrue(restored["gpuRestored"])
        runner.assert_finished(self)

    def test_partial_start_probe_failure_is_still_restored(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                *((GPU, 0, gpu()) for _ in range(3)),
                (START, 0, ""),
                (VERSION, 2, ""),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
                (STOP, 0, ""),
                *docker_off(),
                *((GPU, 0, gpu(30_300.0)) for _ in range(3)),
                *docker_off(),
            ]
        )
        subject = guard(runner)

        with self.assertRaisesRegex(
            lifecycle.HostLifecycleError, "docker_state_probe_failed"
        ):
            subject.prepare()
        restored = subject.best_effort_restore()

        self.assertEqual(restored["status"], "clean")
        self.assertIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_best_effort_rechecks_after_clean_proof(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu(30_400.0)) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu(30_400.0)) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
            ]
        )
        subject = guard(runner)
        subject.prepare()
        subject.finish_after_owned_cleanup()

        restored = subject.best_effort_restore()

        self.assertEqual(restored["status"], "clean")
        self.assertEqual(runner.calls.count(PS), 2)
        self.assertEqual(runner.calls.count(GPU), 9)
        runner.assert_finished(self)

    def test_best_effort_restore_never_stops_nonempty_docker(self) -> None:
        runner = ScriptedRunner(
            [
                *docker_off(),
                *((GPU, 0, gpu()) for _ in range(3)),
                (START, 0, ""),
                (VERSION, 0, "29.6.1\n"),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, "0123456789ab\n"),
                (VERSION, 0, "29.6.1\n"),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        restored = subject.best_effort_restore()

        self.assertEqual(restored["status"], "blocked")
        self.assertEqual(restored["code"], "docker_not_empty")
        self.assertEqual(restored["dockerFinalState"], "on")
        self.assertNotIn(STOP, runner.calls)
        runner.assert_finished(self)

    def test_evidence_is_bounded_content_free_json(self) -> None:
        runner = ScriptedRunner(
            [
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                *((GPU, 0, gpu()) for _ in range(3)),
                (VERSION, 0, "29.6.1\n"),
                (PS, 0, ""),
            ]
        )
        subject = guard(runner)
        subject.prepare()

        encoded = json.dumps(
            subject.finish_after_owned_cleanup(),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertLess(len(encoded), 1024)
        self.assertNotIn("C:/", encoded)
        self.assertNotIn("29.6.1", encoded)
        runner.assert_finished(self)


if __name__ == "__main__":
    unittest.main()
