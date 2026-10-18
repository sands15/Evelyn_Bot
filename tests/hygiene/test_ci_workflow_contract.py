from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from tools.audit_locked_dependencies import OLD_TRANSFORMERS_DEFERRED


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "verify-evelyn.yml"


class CiWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)

    def test_full_locked_regression_and_real_process_smoke_are_required(self) -> None:
        self.assertIn("fetch-depth: 0", self.source)
        self.assertIn("cache-dependency-path: requirements.lock", self.source)
        self.assertIn("-r requirements.lock", self.source)
        self.assertIn('EVELYN_RUN_REAL_MAIN_INTEGRATION: "1"', self.source)
        self.assertIn("python -m unittest discover -s tests -t .", self.source)

    def test_dependency_audits_run_on_changes_and_weekly(self) -> None:
        # PyYAML's YAML 1.1 loader treats an unquoted `on` key as True.
        events = self.workflow.get("on", self.workflow.get(True))
        self.assertIn("push", events)
        self.assertIn("pull_request", events)
        self.assertIn({"cron": "17 3 * * 1"}, events["schedule"])
        audit_steps = [step for step in self.workflow["jobs"]["regression"]["steps"]
                       if step.get("name") == "Audit locked Python dependencies"]
        self.assertEqual(1, len(audit_steps))
        audit = audit_steps[0]
        self.assertNotIn("if", audit)
        self.assertFalse(audit.get("continue-on-error", False))
        self.assertIn("pip-audit==2.10.1", audit["run"])
        self.assertIn("python tools/audit_locked_dependencies.py",
                      [line.strip() for line in audit["run"].splitlines()])
        self.assertNotIn("--ignore-vuln", self.source)
        self.assertIn("npm audit --audit-level=high", self.source)

    def test_known_python_exceptions_are_explicit_and_documented(self) -> None:
        expected_deferred = {
            "PYSEC-2025-217",
            "PYSEC-2026-2290",
            "PYSEC-2026-2288",
            "PYSEC-2026-2289",
        }
        self.assertEqual(expected_deferred, OLD_TRANSFORMERS_DEFERRED)
        active_risks = (REPO_ROOT / "docs" / "ACTIVE_RISKS.md").read_text(encoding="utf-8")
        for vulnerability_id in expected_deferred:
            self.assertIn(f"`{vulnerability_id}`", active_risks)
        self.assertIn("Qwen-ASR 0.0.6", active_risks)


if __name__ == "__main__":
    unittest.main()
