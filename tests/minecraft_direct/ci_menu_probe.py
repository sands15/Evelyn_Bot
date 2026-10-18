"""Real Java -> pinned oracle -> Python consumer, using owned in-memory fixtures."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

from .menu_property_oracle import check_java_output


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "ci_direct", ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py")
direct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(direct)


class JavaMenuGateTests(unittest.TestCase):
    def java(self, case):
        build = ROOT / ".tmp/ci-java"
        classpath = os.pathsep.join(map(str, (
            build, build / "gson.jar", ROOT / "adapters/minecraft-client/src/main/resources")))
        java = Path(os.environ["JAVA_HOME"]) / "bin/java.exe"
        return subprocess.run([str(java), "-XX:-UsePerfData", "-cp", classpath,
                               "dev.evelyn.minecraft." + case],
                              cwd=build, capture_output=True, text=True, timeout=30)

    def test_product_observations_match_independent_official_values(self):
        case = ("PropertyMutationProbe" if os.environ.get("EVELYN_CI_INJECT_WRONG_MENU") == "1"
                else "MenuPropertiesTest")
        checked = self.java(case)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        check_java_output(checked.stdout, direct._inventory_observation)

    def test_matching_wrong_product_and_old_expectation_are_rejected(self):
        checked = self.java("PropertyMutationProbe")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("official property oracle count mismatch", checked.stderr)
        self.assertNotIn("MenuPropertiesTest PASS", checked.stdout)
        self.assertNotIn("MENU_PROPERTY_OBSERVATIONS", checked.stdout)


if __name__ == "__main__":
    unittest.main()
