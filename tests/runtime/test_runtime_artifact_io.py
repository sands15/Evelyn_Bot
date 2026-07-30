from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402


class RuntimeArtifactIoTests(unittest.TestCase):
    def test_permission_error_is_retried_without_rewriting_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "status.json"
            replace_calls = 0
            delays: list[float] = []

            def replace(source, destination) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls < 3:
                    raise PermissionError("reader holds delete sharing")
                Path(source).replace(destination)

            atomic_json_write(
                target,
                {"ready": True},
                replace=replace,
                sleep=delays.append,
                attempts=4,
                retry_delay_sec=0.01,
            )

            self.assertEqual(replace_calls, 3)
            self.assertEqual(delays, [0.01, 0.02])
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ready": True})
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_non_permission_io_error_is_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "status.json"

            def replace(_source, _destination) -> None:
                raise OSError("disk unavailable")

            with self.assertRaisesRegex(OSError, "disk unavailable"):
                atomic_json_write(target, {}, replace=replace)
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_durable_write_syncs_temporary_file_before_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "checkpoint.json"
            synced_file_descriptors: list[int] = []
            replaced_after_sync: list[bool] = []

            def sync(file_descriptor: int) -> None:
                synced_file_descriptors.append(file_descriptor)

            def replace(source, destination) -> None:
                replaced_after_sync.append(bool(synced_file_descriptors))
                Path(source).replace(destination)

            atomic_json_write(
                target,
                {"durable": True},
                replace=replace,
                sync=sync,
                durable=True,
            )

            self.assertEqual(len(synced_file_descriptors), 1)
            self.assertEqual(replaced_after_sync, [True])
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"durable": True},
            )

    def test_durable_sync_failure_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "checkpoint.json"

            def sync(_file_descriptor: int) -> None:
                raise OSError("sync failed")

            with self.assertRaisesRegex(OSError, "sync failed"):
                atomic_json_write(
                    target,
                    {"durable": True},
                    sync=sync,
                    durable=True,
                )

            self.assertFalse(target.exists())
            self.assertEqual(
                list(target.parent.glob(".*.tmp")),
                [],
            )


if __name__ == "__main__":
    unittest.main()
