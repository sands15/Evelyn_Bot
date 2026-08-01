from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.instance_lock_runtime import (  # noqa: E402
    InstanceLockManager,
    build_instance_lock_runtime_deps,
)


class LocalBridgeSingleInstanceTests(unittest.TestCase):
    def test_process_lifetime_os_lock_rejects_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "local_bridge" / "instance.lock"
            first = InstanceLockManager(build_instance_lock_runtime_deps(lock_path))
            second = InstanceLockManager(build_instance_lock_runtime_deps(lock_path))
            first.acquire(wait_sec=0.0)
            try:
                with self.assertRaises(RuntimeError):
                    second.acquire(wait_sec=0.0)
            finally:
                first.release()

            second.acquire(wait_sec=0.0)
            second.release()

    def test_bridge_main_holds_lock_around_entire_async_runtime(self):
        source = (
            RUNTIME_ROOT / "evelyn_core" / "local_io_bridge.py"
        ).read_text(encoding="utf-8")
        main_source = source[source.index("def main()") :]

        acquire_index = main_source.index("instance_lock.acquire(wait_sec=0.0)")
        run_index = main_source.index("asyncio.run(LocalIoBridge().run())")
        release_index = main_source.index("instance_lock.release()")
        self.assertLess(acquire_index, run_index)
        self.assertLess(run_index, release_index)
        self.assertIn("local_bridge_instance_lock_held", main_source)
        self.assertIn("raise SystemExit(main())", main_source)


if __name__ == "__main__":
    unittest.main()
