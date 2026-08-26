#!/usr/bin/env python3
"""Immutable PID-1 adapter for advisory candidate-bound unittest runs."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile


PROTOCOL = "evelyn-workspace-test-runner-v1"
CANARY_SENTINEL = "evelyn-workspace-sandbox-canary-v2"
_CHILD_TIMEOUT_SEC = 18.0
_CHILD_PROGRAM = r"""
import os
import sys
import unittest

sys.path.insert(0, os.getcwd())
sys.path.insert(0, "/workspace/evelyn_core/runtime")
program = unittest.main(
    module=None,
    argv=["unittest", *sys.argv[1:]],
    exit=False,
    buffer=True,
)
result = program.result
raw_tests_run = max(int(result.testsRun), 0)
tests_run = min(raw_tests_run, 63)
executed = max(0, raw_tests_run - len(result.skipped))
if executed <= 0:
    os._exit(64)
if result.wasSuccessful() and executed > 0:
    os._exit(64 + min(executed, 63))
if tests_run > 0:
    os._exit(192 + tests_run)
os._exit(64)
"""


def _run_targets(targets: list[str], *, cwd: pathlib.Path) -> tuple[str, int]:
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _CHILD_PROGRAM, *targets],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_CHILD_TIMEOUT_SEC,
            check=False,
            close_fds=True,
            env={
                "HOME": "/tmp",
                "PATH": os.environ.get("PATH", ""),
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NO_COLOR": "1",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return "invalid", 0
    code = int(completed.returncode)
    if 65 <= code <= 127:
        return "passed", code - 64
    if 193 <= code <= 255:
        return "failed", code - 192
    return "invalid", 0


def _write_test(root: pathlib.Path, name: str, source: str) -> str:
    target = root / name
    target.write_text(source, encoding="utf-8")
    return name


def _canary() -> bool:
    try:
        status = pathlib.Path("/proc/self/status").read_text(encoding="utf-8")
        isolation_ok = bool(
            os.getuid() == 65534
            and "CapEff:\t0000000000000000" in status
            and "NoNewPrivs:\t1" in status
            and set(os.listdir("/sys/class/net")) <= {"lo"}
            and os.listdir("/app") == []
        )
        with tempfile.TemporaryDirectory(prefix="evelyn-runner-canary-") as value:
            root = pathlib.Path(value)
            zero = _write_test(
                root,
                "test_zero.py",
                "import unittest\nclass Zero(unittest.TestCase):\n    pass\n",
            )
            passed = _write_test(
                root,
                "test_pass.py",
                "import unittest\nclass Pass(unittest.TestCase):\n"
                "    def test_pass(self): self.assertTrue(True)\n",
            )
            skipped = _write_test(
                root,
                "test_skipped.py",
                "import unittest\nclass Skip(unittest.TestCase):\n"
                "    @unittest.skip('canary')\n"
                "    def test_skip(self): pass\n",
            )
            abrupt = _write_test(root, "test_abrupt.py", "import os\nos._exit(0)\n")
            checks = (
                _run_targets([zero], cwd=root) == ("invalid", 0),
                _run_targets([passed], cwd=root) == ("passed", 1),
                _run_targets([skipped], cwd=root) == ("invalid", 0),
                _run_targets([abrupt], cwd=root) == ("invalid", 0),
            )
        return isolation_ok and all(checks)
    except (OSError, ValueError):
        return False


def main(argv: list[str]) -> int:
    if argv == ["canary", "--protocol", PROTOCOL]:
        if _canary():
            print(CANARY_SENTINEL, flush=True)
            return 0
        return 2
    if len(argv) < 5 or argv[:3] != ["python-unittest", "--protocol", PROTOCOL]:
        return 2
    if argv[3] != "--" or any(not target for target in argv[4:]):
        return 2
    state, tests_run = _run_targets(argv[4:], cwd=pathlib.Path("/workspace"))
    if state == "passed":
        print(f"{PROTOCOL}:passed:{tests_run}", flush=True)
        return 0
    if state == "failed":
        print(f"{PROTOCOL}:failed:{tests_run}", flush=True)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
