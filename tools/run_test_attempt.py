"""Run selected unittest checks with a new, non-overwriting evidence directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]


def _token(value: str) -> str:
    return value if re.fullmatch(r"[\w.<>-]{1,240}", value) else "redacted"


def _source(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return "<external>"


class AttemptResult(unittest.TestResult):
    def __init__(self, save_issue):
        super().__init__()
        self.issues: list[dict] = []
        self.save_issue = save_issue

    def _capture(self, test, err, kind):
        exception_type, exception, tb = err
        frames = []
        while tb is not None:
            frames.append({"file": _source(tb.tb_frame.f_code.co_filename),
                           "function": _token(tb.tb_frame.f_code.co_name), "line": tb.tb_lineno})
            tb = tb.tb_next
        issue = {"ordinal": len(self.issues) + 1, "kind": kind,
                 "test": _token(test.id()) if test is not None else "<suite>",
                 "exception": _token(exception_type.__name__),
                 "frames": frames}
        # Exception messages, subtest parameters, source text and locals can contain private data.
        for key in ("errno", "winerror"):
            value = getattr(exception, key, None)
            if type(value) is int:
                issue[key] = value
        self.issues.append(issue)
        self.save_issue(issue)

    def addError(self, test, err):
        self._capture(test, err, "error")
        super().addError(test, err)

    def addFailure(self, test, err):
        self._capture(test, err, "failure")
        super().addFailure(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._capture(test, err, "failure" if issubclass(err[0], test.failureException) else "error")
        super().addSubTest(test, subtest, err)


def run_attempt(suite, output_dir, *, attempt_id=None, environment="unspecified",
                expected_tests=None, sources=(), _execute=None, _native_skips=False):
    attempt_id = attempt_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", attempt_id):
        raise ValueError("invalid attempt id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", environment):
        raise ValueError("invalid environment label")
    if expected_tests is not None and (type(expected_tests) is not int or expected_tests < 1):
        raise ValueError("expected_tests must be positive")
    source_hashes = {}
    for source in sources:
        path = Path(source).resolve()
        relative = _source(path)
        if relative == "<external>":
            raise ValueError("source identity must be project-relative")
        source_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    attempt_dir = output_dir / attempt_id
    attempt_dir.mkdir()  # Exclusive reservation, before running even one test.
    started = datetime.now(timezone.utc).isoformat()
    before = time.monotonic()
    aborted = None
    with (attempt_dir / "events.jsonl").open("x", encoding="utf-8", newline="\n") as events:
        def save_event(event):
            events.write(json.dumps(event, ensure_ascii=True) + "\n")
            events.flush()
            os.fsync(events.fileno())
        save_event({"event": "started", "attempt_id": attempt_id, "started_at": started,
                    "environment": environment, "sources": source_hashes})
        result = AttemptResult(save_event)
        try:
            if _execute is None:
                suite.run(result)
            else:
                _execute(result)
        except BaseException as exc:
            aborted = exc
            result._capture(None, sys.exc_info(), "abort")
    final_hashes = {}
    for relative in source_hashes:
        try:
            final_hashes[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        except OSError:
            final_hashes[relative] = None
    inputs_unchanged = source_hashes == final_hashes
    count_matches = expected_tests is None or result.testsRun == expected_tests
    successful = result.wasSuccessful() and unittest.TestResult.wasSuccessful(result)
    passed = (successful and result.testsRun > 0 and count_matches
              and not result.skipped and not result.expectedFailures and inputs_unchanged
              and aborted is None and not result.shouldStop)
    incomplete = (_native_skips and successful and result.testsRun > 0
                  and count_matches and inputs_unchanged and aborted is None and not passed
                  and not result.shouldStop)
    stopped = _native_skips and (result.shouldStop or not count_matches) and successful
    record = {"schema": 1, "attempt_id": attempt_id, "started_at": started,
              "environment": environment, "python": sys.version.split()[0],
              "status": ("ABORTED" if aborted is not None else "INPUT_CHANGED"
                         if not inputs_unchanged else "PASS" if passed else
                         "INCOMPLETE" if incomplete or stopped else "FAIL"),
              "tests": result.testsRun,
              "expected_tests": expected_tests, "errors": len(result.errors),
              "failures": len(result.failures), "skipped": len(result.skipped),
              "expected_failures": len(result.expectedFailures),
              "unexpected_successes": len(result.unexpectedSuccesses),
              "seconds": round(time.monotonic() - before, 3),
              "issues": result.issues, "sources": source_hashes, "sources_after": final_hashes}
    pending = attempt_dir / "result.pending"
    final = attempt_dir / "result.json"
    with pending.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(final)
    return (130 if isinstance(aborted, KeyboardInterrupt) else 0 if passed or incomplete else 1), final


def _test_sources(suite):
    paths = {Path(__file__).resolve()}
    pending = [suite]
    while pending:
        test = pending.pop()
        if isinstance(test, unittest.TestSuite):
            pending.extend(test)
        elif test is not None:
            module = sys.modules.get(type(test).__module__)
            filename = getattr(module, "__file__", None)
            if filename and _source(filename) != "<external>" and Path(filename).is_file():
                paths.add(Path(filename).resolve())
    return sorted(paths)


def install_unittest_capture():
    """Attach to this repository's existing unittest paths, not the Python installation."""
    original_run = unittest.TextTestRunner.run
    if getattr(original_run, "_evelyn_capture", False):
        return

    def captured_run(runner, suite):
        actual_result = None

        def execute(recorder):
            original_make = runner._makeResult
            original_make = getattr(original_make, "_evelyn_original_factory", original_make)
            own_make = runner.__dict__.get("_makeResult")
            originals = {}

            def observed_result():
                nonlocal actual_result
                actual_result = result = original_make()
                originals.update({name: getattr(result, name) for name in
                                  ("addError", "addFailure", "addSubTest")})

                def error(test, err):
                    recorder._capture(test, err, "error")
                    originals["addError"](test, err)

                def failure(test, err):
                    recorder._capture(test, err, "failure")
                    originals["addFailure"](test, err)

                def subtest(test, child, err):
                    if err is not None:
                        recorder._capture(test, err, "failure" if
                                          issubclass(err[0], test.failureException) else "error")
                    originals["addSubTest"](test, child, err)

                result.addError, result.addFailure, result.addSubTest = error, failure, subtest
                return result

            observed_result._evelyn_original_factory = original_make
            runner._makeResult = observed_result
            try:
                original_run(runner, suite)
            finally:
                if own_make is None:
                    del runner._makeResult
                else:
                    runner._makeResult = own_make
                if actual_result is not None:
                    for name, method in originals.items():
                        setattr(actual_result, name, method)
                    for name in ("testsRun", "errors", "failures", "skipped", "shouldStop",
                                 "expectedFailures", "unexpectedSuccesses"):
                        setattr(recorder, name, getattr(actual_result, name))
                    recorder.wasSuccessful = actual_result.wasSuccessful

        output = os.environ.get("EVELYN_TEST_ATTEMPT_ROOT") or ROOT / "runtime_artifacts/validation/test_attempts"
        expected = suite.countTestCases() if hasattr(suite, "countTestCases") else None
        code, _ = run_attempt(suite, output, environment="unittest-auto",
                              sources=_test_sources(suite), _execute=execute,
                              expected_tests=expected or None, _native_skips=True)
        if code == 130:
            raise KeyboardInterrupt()
        # A test failure keeps unittest's result; evidence/abort failures cannot become exit 0.
        if actual_result is None or (code and actual_result.wasSuccessful()):
            raise RuntimeError("Verification incomplete; inspect the saved attempt receipt")
        return actual_result

    captured_run._evelyn_capture = True
    unittest.TextTestRunner.run = captured_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tests", nargs="+", help="Explicit unittest module/class/method names")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runtime_artifacts/validation/test_attempts")
    parser.add_argument("--attempt-id")
    parser.add_argument("--environment", default="unspecified")
    parser.add_argument("--expected-tests", type=int)
    parser.add_argument("--source", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    try:
        suite = unittest.TestLoader().loadTestsFromNames(args.tests)
        code, path = run_attempt(suite, args.output_dir, attempt_id=args.attempt_id,
                                 environment=args.environment, expected_tests=args.expected_tests,
                                 sources=args.source)
        record = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps({"attempt_id": record["attempt_id"], "status": record["status"],
                          "tests": record["tests"], "errors": record["errors"],
                          "failures": record["failures"], "skipped": record["skipped"],
                          "evidence_saved": True}))
        return code
    except Exception as exc:
        print(json.dumps({"status": "EVIDENCE_ERROR", "evidence_saved": False,
                          "exception": _token(type(exc).__name__)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
