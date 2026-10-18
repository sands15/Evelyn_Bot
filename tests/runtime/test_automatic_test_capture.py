"""Existing unittest entrypoints must retain failures without an opt-in runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROBE = '''
import io, os, unittest
import tests
class Probe(unittest.TestCase):
    def test_probe(self):
        outcome = os.environ.get("PROBE_OUTCOME", "pass")
        if outcome == "error": raise PermissionError(13, "private sentinel")
        if outcome == "skip": self.skipTest("private sentinel")
        if outcome == "interrupt": raise KeyboardInterrupt()
        if outcome == "subtests":
            for i in range(2):
                with self.subTest(secret="private sentinel", i=i):
                    raise OSError(5, "private sentinel")
class Result(unittest.TextTestResult):
    marker = True
runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=Result, failfast=True)
result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Probe))
assert result.marker and result.failfast
raise SystemExit(not result.wasSuccessful())
'''


class AutomaticCaptureTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="automatic-capture-", dir=ROOT / ".tmp")
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "receipts"

    def invoke(self, args=None, outcome="pass", code=PROBE):
        env = dict(os.environ, EVELYN_TEST_ATTEMPT_ROOT=str(self.output), PROBE_OUTCOME=outcome)
        return subprocess.run([sys.executable, "-B", *(args or ["-c", code])], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=40)

    def records(self):
        return sorted(self.output.glob("*/result.json"))

    def test_ci_keeps_redacted_receipts_under_distinct_rerun_names(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/verify-evelyn.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["regression"]["steps"]
        run = next(step for step in steps if step.get("id") == "regression_tests")
        self.assertIn("python -m unittest discover", run["run"])
        self.assertEqual(run["env"]["EVELYN_TEST_ATTEMPT_ROOT"],
                         "runtime_artifacts/validation/test_attempts")
        upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")
                      and step.get("with", {}).get("name", "").startswith("test-attempts-"))
        self.assertEqual(upload["if"], "always() && steps.regression_tests.outcome != 'skipped'")
        self.assertIn("github.run_id", upload["with"]["name"])
        self.assertIn("github.run_attempt", upload["with"]["name"])
        self.assertFalse(upload["with"]["overwrite"])
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(set(upload["with"]["path"].splitlines()), {
            "runtime_artifacts/validation/test_attempts/*/result.json",
            "runtime_artifacts/validation/test_attempts/*/events.jsonl"})

    def test_posix_runner_temp_uses_an_available_step_context(self):
        import yaml
        workflow = yaml.safe_load((ROOT / ".github/workflows/verify-evelyn.yml").read_text(encoding="utf-8"))
        job = workflow["jobs"]["posix"]
        # GitHub permits runner in step env, but not in job-level env.
        for value in job["env"].values():
            self.assertNotRegex(str(value), r"\$\{\{[^}]*\brunner\.")
        step = next(step for step in job["steps"] if step.get("id") == "posix_test")
        self.assertEqual(step["env"]["TMPDIR"], "${{ runner.temp }}")
        upload = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["if"], "always() && steps.posix_test.outcome != 'skipped'")
        self.assertIn("github.run_attempt", upload["with"]["name"])
        self.assertFalse(upload["with"]["overwrite"])
        self.assertEqual(upload["with"]["if-no-files-found"], "error")

    def test_existing_named_and_ci_discovery_commands_save_evidence(self):
        for args in (["-m", "unittest", "tests.runtime.test_minecraft_menu_oracle"],
                     ["-m", "unittest", "discover", "-s", "tests", "-t", ".",
                      "-p", "test_minecraft_menu_oracle.py"],
                     ["-m", "unittest", "discover", "-s", "tests",
                      "-p", "test_minecraft_menu_oracle.py"]):
            before = set(self.records())
            completed = self.invoke(args)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            added = set(self.records()) - before
            self.assertEqual(len(added), 1, "Existing command bypassed durable evidence")
            record = json.loads(added.pop().read_bytes())
            self.assertEqual((record["status"], record["tests"]), ("PASS", 5))
            self.assertIn("tests/runtime/test_minecraft_menu_oracle.py", record["sources"])

    def test_existing_runner_failure_then_success_preserves_first_bytes(self):
        self.assertEqual(self.invoke(outcome="error").returncode, 1)
        self.assertEqual(len(self.records()), 1, "Original failure was not captured")
        first = self.records()[0]
        original = first.read_bytes()
        events = first.with_name("events.jsonl").read_bytes()
        self.assertEqual(self.invoke().returncode, 0)
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(original).digest())
        self.assertEqual(first.with_name("events.jsonl").read_bytes(), events)
        issue = json.loads(original)["issues"][0]
        self.assertEqual((issue["exception"], issue["errno"]), ("PermissionError", 13))
        self.assertNotIn(b"private sentinel", original + events)

    @unittest.skipUnless(os.name == "nt", "Windows launcher boundary")
    def test_existing_isolated_launcher_command_saves_evidence(self):
        completed = self.invoke(["-I", "tests/runtime/test_shutdown_scripts.py",
                                 "ShutdownScriptContractTests.test_local_stop_script_has_safe_contract"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(self.records()), 1)
        record = json.loads(self.records()[0].read_bytes())
        self.assertEqual((record["status"], record["tests"]), ("PASS", 1))

    def test_skipped_native_success_is_not_recorded_as_verified_pass(self):
        self.assertEqual(self.invoke(outcome="skip").returncode, 0)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(json.loads(self.records()[0].read_bytes())["status"], "INCOMPLETE")

    def test_interrupt_retains_abort_before_exit(self):
        self.assertNotEqual(self.invoke(outcome="interrupt").returncode, 0)
        self.assertEqual(len(self.records()), 1)
        record = json.loads(self.records()[0].read_bytes())
        self.assertEqual(record["status"], "ABORTED")
        self.assertEqual(record["issues"][0]["exception"], "KeyboardInterrupt")

    def test_storage_failure_blocks_success(self):
        self.output.write_text("owned synthetic collision", encoding="utf-8")
        self.assertNotEqual(self.invoke().returncode, 0, "Evidence storage failure was ignored")

    def test_installer_is_idempotent_and_subtest_details_are_redacted(self):
        code = PROBE.replace("import tests", "import tests\nfrom tools.run_test_attempt import install_unittest_capture\ninstall_unittest_capture()\ninstall_unittest_capture()")
        code = code.replace("failfast=True", "failfast=False").replace("result.marker and result.failfast", "result.marker and not result.failfast")
        completed = self.invoke(outcome="subtests", code=code)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(len(self.records()), 1)
        record = json.loads(self.records()[0].read_bytes())
        self.assertEqual((record["tests"], record["errors"], len(record["issues"])), (1, 2, 2))
        self.assertNotIn("private sentinel", self.records()[0].read_text())

    def test_nested_runner_keeps_inner_failure_even_when_outer_accepts_it(self):
        for nested_runner in ("unittest.TextTestRunner(stream=io.StringIO())", "runner"):
            code = PROBE.replace('if outcome == "error":', '''if outcome == "nested":
            class Inner(unittest.TestCase):
                def runTest(inner): inner.fail("private sentinel")
            result = NESTED_RUNNER.run(unittest.TestSuite([Inner()]))
            self.assertFalse(result.wasSuccessful())
        if outcome == "error":''').replace("NESTED_RUNNER", nested_runner)
            before = set(self.records())
            self.assertEqual(self.invoke(outcome="nested", code=code).returncode, 0)
            records = [json.loads(path.read_bytes()) for path in set(self.records()) - before]
            self.assertEqual(sorted(record["status"] for record in records), ["FAIL", "PASS"])
            failed = next(record for record in records if record["status"] == "FAIL")
            self.assertEqual(failed["issues"][0]["exception"], "AssertionError")

    def test_custom_result_failure_cannot_be_recorded_as_pass(self):
        code = PROBE.replace("marker = True", "marker = True\n    def wasSuccessful(self): return False")
        self.assertEqual(self.invoke(code=code).returncode, 1)
        self.assertEqual(json.loads(self.records()[0].read_bytes())["status"], "FAIL")

    def test_custom_success_cannot_hide_a_captured_error(self):
        code = PROBE.replace("marker = True", "marker = True\n    def wasSuccessful(self): return True")
        code = code.replace("failfast=True", "failfast=False").replace("result.marker and result.failfast", "result.marker and not result.failfast")
        self.assertNotEqual(self.invoke(outcome="error", code=code).returncode, 0)
        record = json.loads(self.records()[0].read_bytes())
        self.assertEqual((record["status"], record["errors"]), ("FAIL", 1))

    def test_early_stop_cannot_bless_unexecuted_selected_test(self):
        code = PROBE.replace("marker = True", "marker = True\n    def stopTest(self, test):\n        super().stopTest(test)\n        self.stop()")
        code = code.replace("unittest.defaultTestLoader.loadTestsFromTestCase(Probe)",
                            'unittest.TestSuite([Probe("test_probe"), Probe("test_probe")])')
        self.assertNotEqual(self.invoke(code=code).returncode, 0)
        record = json.loads(self.records()[0].read_bytes())
        self.assertEqual((record["status"], record["tests"], record["expected_tests"]),
                         ("INCOMPLETE", 1, 2))

    def test_result_lifecycle_errors_are_captured_before_callbacks_are_restored(self):
        for callback in ("startTestRun", "stopTestRun"):
            code = PROBE.replace("marker = True", f'''marker = True
    def {callback}(self):
        import sys
        try: raise OSError(5, "private sentinel")
        except OSError: self.addError(Probe("test_probe"), sys.exc_info())''')
            before = set(self.records())
            self.assertEqual(self.invoke(code=code).returncode, 1)
            path, = set(self.records()) - before
            record = json.loads(path.read_bytes())
            self.assertEqual((record["status"], record["errors"]), ("FAIL", 1))
            self.assertEqual(record["issues"][0]["errno"], 5)


if __name__ == "__main__":
    unittest.main()
