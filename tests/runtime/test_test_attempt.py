from __future__ import annotations

import importlib
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]


def cli_probe():
    class PassingProbe(unittest.TestCase):
        def runTest(self):
            self.assertTrue(True)
    return unittest.TestSuite([PassingProbe()])


class TestAttemptTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / "tools/run_test_attempt.py").is_file(),
                        "The durable attempt runner is not implemented")
        self.runner = importlib.import_module("tools.run_test_attempt")
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="test-attempt-", dir=ROOT / ".tmp")
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)

    def suite(self, outcome="pass"):
        class Probe(unittest.TestCase):
            def test_probe(self):
                if outcome == "error":
                    raise PermissionError(13, "private sentinel", "C:/private/person.txt")
                if outcome == "failure":
                    self.fail("private sentinel")
                if outcome == "skip":
                    self.skipTest("private sentinel")
                if outcome == "subtests":
                    for i in range(2):
                        with self.subTest(secret="private sentinel", number=i):
                            raise OSError(5, "private sentinel")
        return unittest.defaultTestLoader.loadTestsFromTestCase(Probe)

    def run_probe(self, outcome="pass", attempt_id="first", **kwargs):
        return self.runner.run_attempt(self.suite(outcome), self.output,
                                       attempt_id=attempt_id, **kwargs)

    def test_failure_then_success_keeps_original_bytes_and_details(self):
        failed_code, failed_path = self.run_probe("error")
        original = failed_path.read_bytes()
        success_code, success_path = self.run_probe(attempt_id="second")
        self.assertEqual((failed_code, success_code), (1, 0))
        self.assertNotEqual(failed_path, success_path)
        self.assertEqual(failed_path.read_bytes(), original)
        failure = json.loads(original)
        self.assertEqual(failure["status"], "FAIL")
        self.assertEqual(failure["issues"][0]["exception"], "PermissionError")
        self.assertEqual(failure["issues"][0]["errno"], 13)
        self.assertTrue(any(f["file"] == "tests/runtime/test_test_attempt.py"
                            for f in failure["issues"][0]["frames"]))
        self.assertNotIn("private sentinel", original.decode())
        self.assertNotIn("C:/private", original.decode())
        self.assertEqual(json.loads(success_path.read_bytes())["status"], "PASS")

    def test_duplicate_id_never_overwrites_or_executes_suite(self):
        _, path = self.run_probe()
        original = path.read_bytes()
        with patch.object(unittest.TestSuite, "run", side_effect=AssertionError("must not run")):
            with self.assertRaises(FileExistsError):
                self.run_probe("error")
        self.assertEqual(path.read_bytes(), original)

    def test_persistence_failure_is_not_success_and_keeps_failed_attempt(self):
        _, failed_path = self.run_probe("error")
        original = failed_path.read_bytes()
        with patch.object(self.runner.os, "fsync", side_effect=OSError(28, "disk full")):
            with self.assertRaises(OSError):
                self.run_probe(attempt_id="second")
        self.assertEqual(failed_path.read_bytes(), original)
        self.assertFalse((self.output / "second/result.json").exists())

    def test_subtest_errors_remain_separate_without_parameter_values(self):
        code, path = self.run_probe("subtests")
        record = json.loads(path.read_bytes())
        self.assertEqual(code, 1)
        self.assertEqual(record["tests"], 1)
        self.assertEqual(record["errors"], 2)
        self.assertEqual(len(record["issues"]), 2)
        self.assertEqual([i["ordinal"] for i in record["issues"]], [1, 2])
        self.assertNotIn("private sentinel", path.read_text())

    def test_assertion_and_skipped_checks_cannot_be_pass(self):
        for outcome in ("failure", "skip"):
            code, path = self.run_probe(outcome, attempt_id=outcome)
            self.assertNotEqual(code, 0)
            self.assertNotEqual(json.loads(path.read_bytes())["status"], "PASS")

    def test_zero_tests_and_expected_count_mismatch_cannot_be_pass(self):
        code, path = self.runner.run_attempt(unittest.TestSuite(), self.output, attempt_id="empty")
        self.assertNotEqual(code, 0)
        self.assertNotEqual(json.loads(path.read_bytes())["status"], "PASS")
        code, path = self.run_probe(expected_tests=2, attempt_id="missing")
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(path.read_bytes())["expected_tests"], 2)

    def test_traversal_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_probe(attempt_id="../outside")

    def test_source_identity_is_saved_without_absolute_path(self):
        code, path = self.run_probe(sources=[ROOT / "tools/run_test_attempt.py"])
        record = json.loads(path.read_bytes())
        self.assertEqual(code, 0)
        self.assertEqual(len(record["sources"]["tools/run_test_attempt.py"]), 64)
        self.assertNotIn(str(ROOT), path.read_text())

    def test_cli_returns_nonzero_on_persistence_error_and_duplicate(self):
        args = ["tests.runtime.test_test_attempt.cli_probe", "--output-dir", str(self.output),
                "--attempt-id", "cli", "--expected-tests", "1"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.runner.main(args), 0)
            self.assertEqual(self.runner.main(args), 2)
            args[4] = "write-failure"
            with patch.object(self.runner.os, "fsync", side_effect=OSError(28, "private sentinel")):
                self.assertEqual(self.runner.main(args), 2)
        self.assertFalse((self.output / "write-failure/result.json").exists())

    def test_changed_source_during_run_is_not_blessed_as_same_revision(self):
        source = self.output / "input.py"
        source.write_text("initial", encoding="utf-8")
        class MutatingProbe(unittest.TestCase):
            def runTest(self):
                source.write_text("changed", encoding="utf-8")
        code, path = self.runner.run_attempt(unittest.TestSuite([MutatingProbe()]), self.output,
                                             attempt_id="changed", sources=[source])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(path.read_bytes())["status"], "INPUT_CHANGED")

    def test_failure_then_interrupt_and_retry_keep_first_failure(self):
        class InterruptedSuite(unittest.TestSuite):
            def run(inner, result, debug=False):
                self.suite("failure").run(result)
                raise KeyboardInterrupt()
        code, path = self.runner.run_attempt(InterruptedSuite(), self.output, attempt_id="interrupted")
        self.assertEqual(code, 130)
        original = path.read_bytes()
        record = json.loads(original)
        self.assertEqual(record["status"], "ABORTED")
        self.assertEqual(record["issues"][0]["exception"], "AssertionError")
        self.assertEqual(record["issues"][-1]["exception"], "KeyboardInterrupt")
        self.assertEqual(self.run_probe(attempt_id="retry")[0], 0)
        self.assertEqual(path.read_bytes(), original)

    def test_issue_is_persisted_before_later_suite_code_runs(self):
        class InspectingSuite(unittest.TestSuite):
            def run(inner, result, debug=False):
                self.suite("error").run(result)
                rows = (self.output / "immediate/events.jsonl").read_text(encoding="utf-8").splitlines()
                self.assertEqual(json.loads(rows[-1])["exception"], "PermissionError")
                raise RuntimeError("private sentinel")
        code, path = self.runner.run_attempt(InspectingSuite(), self.output, attempt_id="immediate")
        self.assertEqual(code, 1)
        record = json.loads(path.read_bytes())
        self.assertEqual(record["status"], "ABORTED")
        self.assertEqual(record["issues"][-1]["exception"], "RuntimeError")
        self.assertNotIn("private sentinel", path.read_text())


if __name__ == "__main__":
    unittest.main()
