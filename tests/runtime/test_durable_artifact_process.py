from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.durable_artifact_process import (  # noqa: E402
    DurableArtifactProcess,
    DurableArtifactProcessError,
    DurableArtifactProcessTimeout,
)


FAULT_WORKER = REPO_ROOT / "tests" / "fixtures" / "durable_artifact_fault_worker.py"
PARENT_HARNESS = (
    REPO_ROOT / "tests" / "fixtures" / "durable_artifact_parent_harness.py"
)
WORKER_PYTHON = str(getattr(sys, "_base_executable", "") or sys.executable)


@contextmanager
def _process_exit_handle(pid: int) -> Iterator[Callable[[float], bool]]:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        wait_for_single = kernel32.WaitForSingleObject
        wait_for_single.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single.restype = wintypes.DWORD
        terminate_process = kernel32.TerminateProcess
        terminate_process.argtypes = (wintypes.HANDLE, wintypes.UINT)
        terminate_process.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = open_process(0x00100001, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        def wait_for_exit(timeout_sec: float) -> bool:
            result = wait_for_single(handle, max(0, round(timeout_sec * 1000)))
            if result == 0:
                return True
            if result == 258:
                return False
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            yield wait_for_exit
        finally:
            if not wait_for_exit(0.0):
                terminate_process(handle, 1)
                wait_for_exit(2.0)
            close_handle(handle)
        return

    if hasattr(os, "pidfd_open"):
        descriptor = os.pidfd_open(pid)
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)

        def wait_for_exit(timeout_sec: float) -> bool:
            return bool(poller.poll(max(0, round(timeout_sec * 1000))))

        try:
            yield wait_for_exit
        finally:
            if not wait_for_exit(0.0):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                wait_for_exit(2.0)
            os.close(descriptor)
        return

    if hasattr(select, "kqueue"):
        queue = select.kqueue()
        event = select.kevent(
            pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        queue.control([event], 0, 0)
        exited = False

        def wait_for_exit(timeout_sec: float) -> bool:
            nonlocal exited
            exited = exited or bool(queue.control(None, 1, max(0.0, timeout_sec)))
            return exited

        try:
            yield wait_for_exit
        finally:
            if not wait_for_exit(0.0):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                wait_for_exit(2.0)
            queue.close()
        return

    raise unittest.SkipTest("this POSIX platform has no process exit handle")


class DurableArtifactProcessTests(unittest.TestCase):
    def test_worker_environment_does_not_inherit_credentials(self) -> None:
        process = DurableArtifactProcess()
        self.addCleanup(process.close)
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "must-not-cross-boundary",
                "DISCORD_TOKEN": "must-not-cross-boundary",
            },
        ):
            worker_env = process._worker_env()

        self.assertNotIn("OPENAI_API_KEY", worker_env)
        self.assertNotIn("DISCORD_TOKEN", worker_env)
        self.assertEqual(worker_env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(worker_env["PYTHONIOENCODING"], "utf-8")

    def test_relative_path_is_rejected_before_worker_start(self) -> None:
        process = DurableArtifactProcess()
        self.addCleanup(process.close)

        with self.assertRaisesRegex(
            DurableArtifactProcessError,
            "durable_artifact_path_rejected",
        ):
            process.write_text(Path("relative.txt"), "private")

        self.assertIsNone(process.pid)

    def _fault_process(
        self,
        *,
        scenario: str,
        state_path: Path,
        target: Path,
    ) -> DurableArtifactProcess:
        return DurableArtifactProcess(
            deadline_sec=0.75,
            start_timeout_sec=1.0,
            command=(
                WORKER_PYTHON,
                "-u",
                str(FAULT_WORKER),
                "--scenario",
                scenario,
                "--state",
                str(state_path),
                "--target",
                str(target),
            ),
        )

    def test_real_worker_is_warm_and_replaced_after_hard_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "checkpoint.json"
            process = DurableArtifactProcess(
                deadline_sec=1.0,
                start_timeout_sec=1.0,
            )
            try:
                process.ensure_started()
                warm_pid = process.pid
                self.assertIsNotNone(warm_pid)
                process.write_json(target, {"generation": 1}, durable=True)
                self.assertEqual(process.pid, warm_pid)
                self.assertEqual(
                    json.loads(
                        process.read_text(
                            target,
                            maximum_bytes=1024,
                            missing_ok=False,
                        )
                    ),
                    {"generation": 1},
                )
                self.assertEqual(process.pid, warm_pid)

                killed = process._process
                self.assertIsNotNone(killed)
                killed.kill()
                killed.wait(timeout=1.0)

                self.assertIsNotNone(
                    process.read_text(
                        target,
                        maximum_bytes=1024,
                        missing_ok=False,
                    )
                )
                self.assertNotEqual(process.pid, warm_pid)
            finally:
                process.close()

    def test_real_worker_exits_after_parent_hard_death(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            worker_pid_path = Path(temp_dir) / "worker.pid"
            parent = subprocess.Popen(
                [WORKER_PYTHON, "-u", str(PARENT_HARNESS), str(worker_pid_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 8.0
                worker_pid: int | None = None
                while time.monotonic() < deadline and parent.poll() is None:
                    try:
                        worker_pid = int(worker_pid_path.read_text(encoding="utf-8"))
                        break
                    except (FileNotFoundError, ValueError):
                        time.sleep(0.02)
                self.assertIsNone(parent.poll())
                self.assertIsNotNone(worker_pid)
                assert worker_pid is not None

                with _process_exit_handle(worker_pid) as wait_for_worker_exit:
                    self.assertFalse(wait_for_worker_exit(0.0))
                    parent.kill()
                    parent.wait(timeout=3.0)
                    self.assertIsNotNone(parent.returncode)
                    self.assertTrue(
                        wait_for_worker_exit(5.0),
                        "durable artifact worker survived its hard-killed parent",
                    )
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=3.0)

    def test_pre_replace_stall_is_reaped_and_exact_temporary_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            target = directory / "checkpoint.json"
            state_path = directory / "fault-state.json"
            target.write_text("old", encoding="utf-8")
            process = self._fault_process(
                scenario="stall_before_replace_once",
                state_path=state_path,
                target=target,
            )
            try:
                process.ensure_started()
                stalled = process._process
                self.assertIsNotNone(stalled)
                with self.assertRaises(DurableArtifactProcessTimeout):
                    process.write_text(target, "new", durable=True)

                state = json.loads(state_path.read_text(encoding="utf-8"))
                temporary = Path(state["tempPath"])
                self.assertEqual(state["pid"], stalled.pid)
                self.assertEqual(state["phase"], "before_replace")
                self.assertIsNotNone(stalled.poll())
                self.assertNotEqual(process.pid, stalled.pid)
                self.assertFalse(temporary.exists())
                self.assertEqual(target.read_text(encoding="utf-8"), "old")
                time.sleep(0.5)
                self.assertEqual(target.read_text(encoding="utf-8"), "old")
                self.assertEqual(list(directory.glob(".*.tmp")), [])
            finally:
                process.close()

    def test_post_replace_stall_reconciles_from_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            target = directory / "checkpoint.json"
            state_path = directory / "fault-state.json"
            target.write_text("old", encoding="utf-8")
            process = self._fault_process(
                scenario="stall_after_replace_once",
                state_path=state_path,
                target=target,
            )
            try:
                process.ensure_started()
                stalled = process._process
                self.assertIsNotNone(stalled)
                process.write_text(target, "new", durable=True)

                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["pid"], stalled.pid)
                self.assertEqual(state["phase"], "after_replace")
                self.assertIsNotNone(stalled.poll())
                self.assertNotEqual(process.pid, stalled.pid)
                self.assertEqual(target.read_text(encoding="utf-8"), "new")
                self.assertEqual(list(directory.glob(".*.tmp")), [])
            finally:
                process.close()

    def test_stalled_bounded_read_is_reaped_and_safely_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            target = directory / "checkpoint.json"
            state_path = directory / "fault-state.json"
            target.write_text("stable", encoding="utf-8")
            process = self._fault_process(
                scenario="stall_read_once",
                state_path=state_path,
                target=target,
            )
            try:
                process.ensure_started()
                stalled = process._process
                self.assertIsNotNone(stalled)
                self.assertEqual(
                    process.read_text(
                        target,
                        maximum_bytes=1024,
                        missing_ok=False,
                    ),
                    "stable",
                )

                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(state["pid"], stalled.pid)
                self.assertEqual(state["phase"], "read")
                self.assertIsNotNone(stalled.poll())
                self.assertNotEqual(process.pid, stalled.pid)
            finally:
                process.close()


if __name__ == "__main__":
    unittest.main()
