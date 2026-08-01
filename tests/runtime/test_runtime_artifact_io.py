from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import runtime_artifact_io as artifact_io  # noqa: E402
from evelyn_core.runtime_artifact_io import (  # noqa: E402
    DurableCommitError,
    atomic_json_write,
    atomic_text_write,
)


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

    def test_atomic_text_write_syncs_and_retries_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "memory.md"
            synced: list[int] = []
            replace_calls = 0

            def replace(source, destination) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 1:
                    raise PermissionError("temporary reader lock")
                Path(source).replace(destination)

            atomic_text_write(
                target,
                "line one\nline two\n",
                replace=replace,
                sync=synced.append,
                sleep=lambda _: None,
                durable=True,
            )

            self.assertEqual(replace_calls, 2)
            self.assertEqual(len(synced), 1)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "line one\nline two\n",
            )
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_durable_order_is_file_sync_replace_directory_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "ordered.json"
            events: list[str] = []

            def sync(_file_descriptor: int) -> None:
                events.append("file_sync")

            def replace(source, destination) -> None:
                events.append("replace")
                Path(source).replace(destination)

            def directory_sync(directory: Path) -> None:
                self.assertEqual(directory, target.parent)
                self.assertTrue(target.exists())
                events.append("directory_sync")

            atomic_json_write(
                target,
                {"ordered": True},
                replace=replace,
                sync=sync,
                directory_sync=directory_sync,
                durable=True,
            )

            self.assertEqual(
                events,
                ["file_sync", "replace", "directory_sync"],
            )

    def test_directory_sync_failure_is_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "committed.json"

            def replace(source, destination) -> None:
                Path(source).replace(destination)

            def directory_sync(_directory: Path) -> None:
                raise OSError("directory sync failed")

            with self.assertRaises(DurableCommitError) as raised:
                atomic_json_write(
                    target,
                    {"committed": True},
                    replace=replace,
                    sync=lambda _descriptor: None,
                    directory_sync=directory_sync,
                    durable=True,
                )

            self.assertEqual(
                str(raised.exception),
                "durable_parent_sync_failed",
            )
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"committed": True},
            )
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_non_durable_write_skips_directory_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "non-durable.json"
            directory_sync_calls: list[Path] = []

            atomic_json_write(
                target,
                {"durable": False},
                directory_sync=directory_sync_calls.append,
                durable=False,
            )

            self.assertEqual(directory_sync_calls, [])

    @unittest.skipIf(os.name == "nt", "POSIX directory fsync contract")
    def test_posix_default_durable_write_syncs_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "posix.json"
            synced_directories: list[Path] = []

            with patch.object(
                artifact_io,
                "_sync_parent_directory",
                side_effect=synced_directories.append,
            ):
                atomic_json_write(
                    target,
                    {"durable": True},
                    durable=True,
                )

            self.assertEqual(synced_directories, [target.parent])

    @unittest.skipUnless(os.name == "nt", "Windows write-through contract")
    def test_windows_default_durable_write_uses_write_through_replace(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "windows.json"
            replacements: list[tuple[Path, Path]] = []

            def write_through_replace(source: Path, destination: Path) -> None:
                replacements.append((source, destination))
                source.replace(destination)

            with patch.object(
                artifact_io,
                "_windows_write_through_replace",
                side_effect=write_through_replace,
            ):
                atomic_json_write(
                    target,
                    {"durable": True},
                    durable=True,
                )

            self.assertEqual(len(replacements), 1)
            self.assertEqual(replacements[0][1], target)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"durable": True},
            )

    @unittest.skipUnless(os.name == "nt", "Windows write-through contract")
    def test_windows_write_through_permission_error_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "windows-retry.json"
            replace_calls = 0
            delays: list[float] = []

            def write_through_replace(source: Path, destination: Path) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 1:
                    raise PermissionError("temporary Windows reader lock")
                source.replace(destination)

            with patch.object(
                artifact_io,
                "_windows_write_through_replace",
                side_effect=write_through_replace,
            ):
                atomic_json_write(
                    target,
                    {"durable": True},
                    sleep=delays.append,
                    retry_delay_sec=0.01,
                    durable=True,
                )

            self.assertEqual(replace_calls, 2)
            self.assertEqual(delays, [0.01])
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"durable": True},
            )


if __name__ == "__main__":
    unittest.main()
