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

from evelyn_core.storage_retention_report import (  # noqa: E402
    STORAGE_RETENTION_REPORT_SCHEMA,
    StorageRetentionReporter,
    build_storage_retention_report,
    read_storage_retention_report,
    write_storage_retention_report,
)


def write_file(path: Path, text: str, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


class StorageRetentionReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_root = Path(self.temp_dir.name)
        self.artifacts_root = self.project_root / "runtime_artifacts"
        self.now = 2_000_000.0

    def test_report_aggregates_candidates_without_paths_or_deletion(self) -> None:
        old_runtime_log = self.artifacts_root / "logs" / "old.log"
        new_runtime_log = self.artifacts_root / "logs" / "new.log"
        old_host_log = self.project_root / "logs" / "old.log"
        new_host_log = self.project_root / "logs" / "new.log"
        write_file(old_runtime_log, "old", mtime=self.now - 20 * 86400)
        write_file(new_runtime_log, "new", mtime=self.now)
        write_file(old_host_log, "old", mtime=self.now - 20 * 86400)
        write_file(new_host_log, "new", mtime=self.now)

        report = build_storage_retention_report(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            now=self.now,
        )

        self.assertEqual(report["schema"], STORAGE_RETENTION_REPORT_SCHEMA)
        self.assertEqual(report["state"], "attention")
        self.assertEqual(report["summary"]["candidateCount"], 2)
        self.assertTrue(report["dryRun"])
        self.assertFalse(report["automaticDeletion"])
        self.assertTrue(old_runtime_log.exists())
        self.assertTrue(old_host_log.exists())
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(str(self.project_root), serialized)
        self.assertNotIn("old.log", serialized)

    def test_missing_scopes_are_reported_as_absent(self) -> None:
        report = build_storage_retention_report(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            now=self.now,
        )

        self.assertEqual(report["state"], "clear")
        self.assertEqual(report["scopes"]["runtimeArtifacts"]["state"], "absent")
        self.assertEqual(report["scopes"]["hostLogs"]["state"], "absent")
        self.assertEqual(report["scopes"]["voiceDebug"]["state"], "absent")

    def test_scope_failure_does_not_discard_other_results(self) -> None:
        import evelyn_core.storage_retention_report as report_module

        original = report_module._cleanup_scope

        def fail_host_logs(scope_id, root, *, rules, now):
            if scope_id == "hostLogs":
                raise PermissionError("secret path")
            return original(scope_id, root, rules=rules, now=now)

        with patch.object(report_module, "_cleanup_scope", side_effect=fail_host_logs):
            report = build_storage_retention_report(
                project_root=self.project_root,
                artifacts_root=self.artifacts_root,
                now=self.now,
            )

        self.assertEqual(report["state"], "error")
        self.assertEqual(report["summary"]["errorCount"], 1)
        self.assertEqual(report["scopes"]["hostLogs"]["error"], "scan_failed")
        self.assertEqual(report["warnings"][0]["detail"], "PermissionError")
        self.assertNotIn("secret path", json.dumps(report))

    def test_reader_reports_missing_corrupt_and_stale_states(self) -> None:
        missing = read_storage_retention_report(
            artifacts_root=self.artifacts_root,
            now=self.now,
        )
        self.assertTrue(missing["ok"])
        self.assertFalse(missing["available"])
        self.assertFalse(missing["policy"]["applyApiAvailable"])

        report_path = self.artifacts_root / "retention" / "status.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{", encoding="utf-8")
        corrupt = read_storage_retention_report(
            artifacts_root=self.artifacts_root,
            now=self.now,
        )
        self.assertFalse(corrupt["ok"])
        self.assertEqual(corrupt["error"], "storage_retention_report_invalid")

        report = build_storage_retention_report(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            now=self.now - 1000,
        )
        report["root"] = str(self.project_root)
        write_storage_retention_report(report, artifacts_root=self.artifacts_root)
        stale = read_storage_retention_report(
            artifacts_root=self.artifacts_root,
            now=self.now,
            stale_after_sec=120,
        )
        self.assertTrue(stale["available"])
        self.assertTrue(stale["stale"])
        self.assertNotIn("root", stale["report"])
        self.assertNotIn(str(self.project_root), json.dumps(stale))

    def test_reporter_run_once_writes_only_a_dry_run_report(self) -> None:
        reporter = StorageRetentionReporter(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            interval_sec=60,
            now=lambda: self.now,
        )

        report = reporter.run_once()

        status = reporter.status()
        self.assertEqual(status["state"], report["state"])
        self.assertTrue(status["dryRun"])
        self.assertFalse(status["automaticDeletion"])
        stored = json.loads(
            (self.artifacts_root / "retention" / "status.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(stored["dryRun"])
        self.assertFalse(stored["automaticDeletion"])


if __name__ == "__main__":
    unittest.main()
