"""Exercise the existing isolated command entrypoint, without a game or Java session."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests/minecraft_direct/test_java_transport.py"
SCRIPTS = [ROOT / "tests/minecraft_direct" / name for name in (
    "test_direct_adapter.py", "test_observer_adapter.py", "test_control_adapter.py",
    "test_advanced_adapter.py", "test_player_adapter.py", "test_java_transport.py")]


class MinecraftEntrypointTests(unittest.TestCase):
    def test_documented_isolated_command_loads_and_keeps_its_options(self):
        result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "--help"],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--java-home", result.stdout)
        self.assertIn("--gson", result.stdout)

    def test_existing_isolated_main_preserves_failure_then_success_automatically(self):
        driver = """
import runpy, sys, unittest
script, outcome = sys.argv[1:]
class Synthetic(unittest.TestCase):
    def test_selected(self):
        self.assertNotEqual(outcome, 'fail')
options = ['--java-home', 'unused', '--gson', 'unused'] if script.endswith('test_java_transport.py') else []
sys.argv = [script, *options, 'Synthetic.test_selected']
runpy.run_path(script, run_name='__main__', init_globals={'Synthetic': Synthetic})
"""
        (ROOT / ".tmp").mkdir(exist_ok=True)
        for script in SCRIPTS:
            with self.subTest(script=script.name), tempfile.TemporaryDirectory(prefix="minecraft-entry-", dir=ROOT / ".tmp") as temp:
                root = Path(temp)
                evidence = root / "attempts"
                env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
                env.update(TEMP=temp, TMP=temp, EVELYN_TEST_ATTEMPT_ROOT=str(evidence))
                command = [sys.executable, "-I", "-B", "-c", driver, str(script)]
                failed = subprocess.run([*command, "fail"], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(failed.returncode, 1, failed.stderr)
                receipts = list(evidence.glob("*/result.json"))
                self.assertEqual(len(receipts), 1, "existing command did not save its failed attempt")
                first = receipts[0]
                original = first.read_bytes()
                self.assertEqual(json.loads(original)["status"], "FAIL")
                succeeded = subprocess.run([*command, "pass"], cwd=root, env=env,
                                           capture_output=True, text=True, timeout=15)
                self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
                receipts = list(evidence.glob("*/result.json"))
                self.assertEqual(len(receipts), 2)
                self.assertEqual(first.read_bytes(), original)
                second = next(path for path in receipts if path != first)
                self.assertEqual(json.loads(second.read_bytes())["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
