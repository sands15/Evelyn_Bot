from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.minecraft_owner_lock as lock_module  # noqa: E402
from evelyn_core.minecraft_owner_lock import (  # noqa: E402
    MinecraftOwnerLock,
    MinecraftOwnerLockBusy,
    MinecraftOwnerLockUnavailable,
)


TRY_LOCK_WORKER = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    from evelyn_core.minecraft_owner_lock import (
        MinecraftOwnerLock,
        MinecraftOwnerLockBusy,
        MinecraftOwnerLockUnavailable,
    )

    lock = MinecraftOwnerLock(Path(sys.argv[1]))
    try:
        lock.acquire()
    except MinecraftOwnerLockBusy:
        raise SystemExit(23)
    except MinecraftOwnerLockUnavailable:
        raise SystemExit(24)
    lock.release()
    raise SystemExit(0)
    """
)

CRASH_HOLDER_WORKER = textwrap.dedent(
    """
    import os
    import sys
    from pathlib import Path

    from evelyn_core.minecraft_owner_lock import MinecraftOwnerLock

    lock = MinecraftOwnerLock(Path(sys.argv[1]))
    lock.acquire()
    os._exit(78)
    """
)


class FakeFcntl:
    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 4

    def __init__(self, acquire_error: OSError | None = None) -> None:
        self.acquire_error = acquire_error
        self.calls: list[int] = []

    def flock(self, _fileno: int, mode: int) -> None:
        self.calls.append(mode)
        if mode == self.LOCK_EX | self.LOCK_NB:
            if self.acquire_error is not None:
                raise self.acquire_error


class FakeMsvcrt:
    LK_NBLCK = 1
    LK_UNLCK = 2

    def __init__(self, acquire_error: OSError | None = None) -> None:
        self.acquire_error = acquire_error
        self.calls: list[tuple[int, int]] = []

    def locking(self, _fileno: int, mode: int, size: int) -> None:
        self.calls.append((mode, size))
        if mode == self.LK_NBLCK and self.acquire_error is not None:
            raise self.acquire_error


class MinecraftOwnerLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.path = self.root / "nested" / "owner_claim.lock"

    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return environment

    def run_worker(
        self,
        script: str,
        *,
        expected_returncode: int,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            cwd=REPO_ROOT,
            env=self.subprocess_environment(),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            expected_returncode,
            completed.stderr + completed.stdout,
        )
        return completed

    def test_default_backend_creates_one_byte_persistent_file(self) -> None:
        lock = MinecraftOwnerLock(self.path)
        self.addCleanup(lock.release)

        lock.acquire()
        lock.acquire()

        self.assertEqual(lock.path, self.path)
        self.assertTrue(lock.acquired)
        self.assertEqual(self.path.stat().st_size, 1)

        lock.release()

        self.assertFalse(lock.acquired)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_bytes(), b"\0")

    def test_blocking_acquire_waits_for_holder_release_then_acquires(
        self,
    ) -> None:
        holder = MinecraftOwnerLock(self.path)
        contender = MinecraftOwnerLock(self.path)
        self.addCleanup(holder.release)
        self.addCleanup(contender.release)
        holder.acquire()
        attempt_started = threading.Event()
        attempt_finished = threading.Event()
        errors: list[BaseException] = []

        def acquire_contender() -> None:
            attempt_started.set()
            try:
                contender.acquire_blocking()
            except BaseException as exc:
                errors.append(exc)
            finally:
                attempt_finished.set()

        worker = threading.Thread(
            target=acquire_contender,
            daemon=True,
        )
        worker.start()
        started_before_release = attempt_started.wait(timeout=2.0)
        finished_before_release = attempt_finished.wait(timeout=0.1)
        acquired_before_release = contender.acquired
        holder.release()
        finished_after_release = attempt_finished.wait(timeout=2.0)
        worker.join(timeout=2.0)

        self.assertTrue(started_before_release)
        self.assertFalse(finished_before_release)
        self.assertFalse(acquired_before_release)
        self.assertTrue(finished_after_release)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(contender.acquired)

    def test_blocking_acquire_without_backend_fails_closed_immediately(
        self,
    ) -> None:
        with (
            patch.object(lock_module, "_MSVCRT", None),
            patch.object(lock_module, "_FCNTL", None),
        ):
            lock = MinecraftOwnerLock(self.path)

        with self.assertRaisesRegex(
            MinecraftOwnerLockUnavailable,
            "^minecraft_owner_lock_unavailable$",
        ):
            lock.acquire_blocking()

        self.assertFalse(lock.acquired)
        self.assertFalse(self.path.exists())

    def test_windows_blocking_acquire_retries_beyond_lk_lock_budget(
        self,
    ) -> None:
        backend = FakeMsvcrt()
        busy_attempts = 25
        acquire_attempts = 0
        original_locking = backend.locking

        def locking(fileno: int, mode: int, size: int) -> None:
            nonlocal acquire_attempts
            if mode == backend.LK_NBLCK:
                acquire_attempts += 1
                if acquire_attempts <= busy_attempts:
                    raise PermissionError(errno.EACCES, "still busy")
            original_locking(fileno, mode, size)

        backend.locking = locking
        lock = MinecraftOwnerLock(
            self.path,
            msvcrt_module=backend,
        )
        self.addCleanup(lock.release)

        with patch.object(lock_module.time, "sleep") as sleep:
            lock.acquire_blocking()

        self.assertTrue(lock.acquired)
        self.assertEqual(acquire_attempts, busy_attempts + 1)
        self.assertEqual(sleep.call_count, busy_attempts)

    def test_existing_lock_file_is_normalized_to_one_byte(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"lock-metadata-must-not-grow")
        lock = MinecraftOwnerLock(self.path)
        self.addCleanup(lock.release)

        lock.acquire()

        self.assertEqual(self.path.stat().st_size, 1)
        lock.release()
        self.assertTrue(self.path.exists())

    def test_fcntl_backend_uses_nonblocking_exclusive_lock(self) -> None:
        backend = FakeFcntl()
        lock = MinecraftOwnerLock(
            self.path,
            fcntl_module=backend,
        )

        lock.acquire()
        lock.release()

        self.assertEqual(
            backend.calls,
            [backend.LOCK_EX | backend.LOCK_NB, backend.LOCK_UN],
        )

    def test_msvcrt_backend_locks_exactly_one_byte(self) -> None:
        backend = FakeMsvcrt()
        lock = MinecraftOwnerLock(
            self.path,
            msvcrt_module=backend,
        )

        lock.acquire()
        lock.release()

        self.assertEqual(
            backend.calls,
            [(backend.LK_NBLCK, 1), (backend.LK_UNLCK, 1)],
        )

    def test_known_contention_error_is_busy(self) -> None:
        backend = FakeFcntl(
            BlockingIOError(errno.EAGAIN, "already locked")
        )
        handles: list[object] = []

        def open_file(*args, **kwargs):
            handle = open(*args, **kwargs)
            handles.append(handle)
            return handle

        lock = MinecraftOwnerLock(
            self.path,
            fcntl_module=backend,
            open_file=open_file,
        )

        with self.assertRaisesRegex(
            MinecraftOwnerLockBusy,
            "^minecraft_owner_lock_busy$",
        ):
            lock.acquire()

        self.assertFalse(lock.acquired)
        self.assertTrue(handles[0].closed)

    def test_unknown_lock_error_is_unavailable(self) -> None:
        backend = FakeFcntl(OSError(errno.EIO, "device failure"))
        lock = MinecraftOwnerLock(
            self.path,
            fcntl_module=backend,
        )

        with self.assertRaisesRegex(
            MinecraftOwnerLockUnavailable,
            "^minecraft_owner_lock_unavailable$",
        ):
            lock.acquire()

        self.assertFalse(lock.acquired)

    def test_open_failure_is_unavailable_not_busy(self) -> None:
        backend = FakeFcntl()
        lock = MinecraftOwnerLock(
            self.path,
            fcntl_module=backend,
            open_file=Mock(side_effect=PermissionError("denied")),
        )

        with self.assertRaises(MinecraftOwnerLockUnavailable):
            lock.acquire()

        self.assertFalse(lock.acquired)
        self.assertEqual(backend.calls, [])

    def test_no_backend_fails_closed_without_creating_file(self) -> None:
        with (
            patch.object(lock_module, "_MSVCRT", None),
            patch.object(lock_module, "_FCNTL", None),
        ):
            lock = MinecraftOwnerLock(self.path)

        with self.assertRaisesRegex(
            MinecraftOwnerLockUnavailable,
            "^minecraft_owner_lock_unavailable$",
        ):
            lock.acquire()

        self.assertFalse(lock.acquired)
        self.assertFalse(self.path.exists())

    def test_symlink_is_rejected_before_open(self) -> None:
        open_file = Mock()
        lock = MinecraftOwnerLock(
            self.path,
            fcntl_module=FakeFcntl(),
            open_file=open_file,
        )

        with patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(
                MinecraftOwnerLockUnavailable,
                "^minecraft_owner_lock_unavailable$",
            ):
                lock.acquire()

        open_file.assert_not_called()
        self.assertFalse(lock.acquired)

    def test_real_symlink_is_rejected_when_platform_allows_it(self) -> None:
        target = self.root / "target.lock"
        target.write_bytes(b"private-target")
        self.path.parent.mkdir(parents=True)
        try:
            self.path.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        lock = MinecraftOwnerLock(self.path)

        with self.assertRaises(MinecraftOwnerLockUnavailable):
            lock.acquire()

        self.assertFalse(lock.acquired)
        self.assertEqual(target.read_bytes(), b"private-target")

    def test_destructor_release_is_safe_and_keeps_lock_file(self) -> None:
        lock = MinecraftOwnerLock(self.path)
        lock.acquire()

        lock.__del__()
        lock.__del__()

        self.assertFalse(lock.acquired)
        self.assertTrue(self.path.exists())

    def test_live_holder_blocks_other_process_then_release_allows_it(
        self,
    ) -> None:
        lock = MinecraftOwnerLock(self.path)
        self.addCleanup(lock.release)
        lock.acquire()

        self.run_worker(TRY_LOCK_WORKER, expected_returncode=23)

        self.assertTrue(lock.acquired)
        self.assertEqual(self.path.stat().st_size, 1)
        lock.release()

        self.run_worker(TRY_LOCK_WORKER, expected_returncode=0)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_bytes(), b"\0")

    def test_process_crash_releases_lock_without_deleting_file(self) -> None:
        self.run_worker(CRASH_HOLDER_WORKER, expected_returncode=78)

        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_bytes(), b"\0")
        replacement = MinecraftOwnerLock(self.path)
        self.addCleanup(replacement.release)

        replacement.acquire()

        self.assertTrue(replacement.acquired)
        replacement.release()
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
