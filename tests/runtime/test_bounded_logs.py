import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.bounded_logs import append_bounded_log, rotate_log_if_needed  # noqa: E402
import evelyn_core.upstream_voyager_runner as upstream_runner  # noqa: E402
import evelyn_core.voyager_service as voyager_service  # noqa: E402


class BoundedLogsTests(unittest.TestCase):
    def test_rotate_log_shifts_backups_and_bounds_count(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "service.log"
            path.write_text("current", encoding="utf-8")
            (Path(tmp) / "service.log.1").write_text("first", encoding="utf-8")
            (Path(tmp) / "service.log.2").write_text("second", encoding="utf-8")

            result = rotate_log_if_needed(path, max_bytes=3, backup_count=2)

            self.assertTrue(result.rotated)
            self.assertFalse(path.exists())
            self.assertEqual((Path(tmp) / "service.log.1").read_text(encoding="utf-8"), "current")
            self.assertEqual((Path(tmp) / "service.log.2").read_text(encoding="utf-8"), "first")
            self.assertFalse((Path(tmp) / "service.log.3").exists())

    def test_append_bounded_log_rotates_before_size_limit_is_crossed(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "errors.log"
            path.write_text("12345678", encoding="utf-8")

            result = append_bounded_log(path, "abcd", max_bytes=10, backup_count=1)

            self.assertTrue(result.rotated)
            self.assertEqual(path.read_text(encoding="utf-8"), "abcd")
            self.assertEqual((Path(tmp) / "errors.log.1").read_text(encoding="utf-8"), "12345678")

    def test_no_rotation_below_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "errors.log"
            path.write_text("12", encoding="utf-8")

            result = append_bounded_log(path, "34", max_bytes=10, backup_count=1)

            self.assertFalse(result.rotated)
            self.assertEqual(path.read_text(encoding="utf-8"), "1234")

    def test_redirected_voyager_service_status_is_rate_limited(self) -> None:
        stream = StringIO()
        voyager_service._FILE_STATUS_LAST_EMIT_AT = 0.0
        with patch.object(voyager_service.sys, "stdout", stream), patch.object(
            voyager_service.time,
            "monotonic",
            side_effect=[100.0, 105.0, 131.0],
        ):
            voyager_service._write_status_line("first")
            voyager_service._write_status_line("suppressed")
            voyager_service._write_status_line("third")

        self.assertEqual(stream.getvalue(), "first\nthird\n")

    def test_redirected_upstream_status_is_rate_limited(self) -> None:
        stream = StringIO()
        upstream_runner._RUNNER_FILE_STATUS_LAST_EMIT_AT = 0.0
        with patch.object(upstream_runner.sys, "stdout", stream), patch.object(
            upstream_runner.time,
            "monotonic",
            side_effect=[100.0, 105.0, 131.0],
        ):
            upstream_runner._write_status_line("first")
            upstream_runner._write_status_line("suppressed")
            upstream_runner._write_status_line("third")

        self.assertEqual(stream.getvalue(), "first\nthird\n")


if __name__ == "__main__":
    unittest.main()
