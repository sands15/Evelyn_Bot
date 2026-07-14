from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.instance_lock_runtime import (  # noqa: E402
    InstanceLockRuntimeDeps,
    acquire_instance_lock_from_runtime,
    release_instance_lock_from_runtime,
)


class InstanceLockRuntimeTests(unittest.TestCase):
    def build_deps(
        self,
        lock_path: Path,
        *,
        lock_failures: int = 0,
        times: list[float] | None = None,
        sleeps: list[float] | None = None,
    ) -> InstanceLockRuntimeDeps:
        state = {"failures": lock_failures}
        times = times if times is not None else [0.0]
        sleeps = sleeps if sleeps is not None else []

        def locking(_fileno: int, mode: int, _size: int) -> None:
            if mode == fake_msvcrt.LK_NBLCK and state["failures"] > 0:
                state["failures"] -= 1
                raise OSError("locked")

        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=1,
            LK_UNLCK=2,
            locking=locking,
        )

        def monotonic() -> float:
            value = times[0]
            if len(times) > 1:
                times.pop(0)
            return value

        return InstanceLockRuntimeDeps(
            lock_path=lock_path,
            open_file=open,
            getpid=lambda: 12345,
            monotonic=monotonic,
            sleep=lambda delay: sleeps.append(delay),
            msvcrt_module=fake_msvcrt,
            fcntl_module=None,
        )

    def test_acquire_returns_current_handle_without_reopening(self) -> None:
        current = object()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = acquire_instance_lock_from_runtime(
                current,
                deps=self.build_deps(Path(temp_dir) / "lock"),
            )

        self.assertIs(result, current)

    def test_acquire_writes_pid_after_lock_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "nested" / "lock"
            handle = acquire_instance_lock_from_runtime(
                None,
                deps=self.build_deps(lock_path),
                wait_sec=0.0,
            )
            try:
                handle.seek(0)
                self.assertEqual(handle.read(), "12345")
            finally:
                handle.close()

    def test_acquire_retries_until_timeout_then_closes_handle(self) -> None:
        sleeps: list[float] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "lock"
            with self.assertRaisesRegex(RuntimeError, "Another Evelyn bot instance"):
                acquire_instance_lock_from_runtime(
                    None,
                    deps=self.build_deps(lock_path, lock_failures=2, times=[0.0, 0.1], sleeps=sleeps),
                    wait_sec=0.05,
                    poll_sec=0.01,
                )

        self.assertEqual(sleeps, [])

    def test_release_unlocks_and_closes_handle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "lock"
            handle = acquire_instance_lock_from_runtime(None, deps=self.build_deps(lock_path))

            release_instance_lock_from_runtime(handle, deps=self.build_deps(lock_path))

            self.assertTrue(handle.closed)


if __name__ == "__main__":
    unittest.main()
