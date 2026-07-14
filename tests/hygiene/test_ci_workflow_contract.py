from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "verify-evelyn.yml"


class CiWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")

    def test_full_locked_regression_and_real_process_smoke_are_required(self) -> None:
        self.assertIn("cache-dependency-path: requirements.lock", self.source)
        self.assertIn("-r requirements.lock", self.source)
        self.assertIn('EVELYN_RUN_REAL_MAIN_INTEGRATION: "1"', self.source)
        self.assertIn("python -m unittest discover -s tests -t .", self.source)

    def test_dependency_audits_run_on_changes_and_weekly(self) -> None:
        self.assertIn('cron: "17 3 * * 1"', self.source)
        self.assertIn("pip-audit==2.10.1", self.source)
        self.assertIn("python -m pip_audit -r requirements.lock", self.source)
        self.assertIn("npm audit --audit-level=high", self.source)

    def test_known_python_exceptions_are_explicit_and_documented(self) -> None:
        for vulnerability_id in (
            "PYSEC-2025-217",
            "PYSEC-2026-2290",
            "PYSEC-2026-2288",
            "PYSEC-2026-2289",
        ):
            self.assertIn(f"--ignore-vuln {vulnerability_id}", self.source)
        active_risks = (REPO_ROOT / "docs" / "ACTIVE_RISKS.md").read_text(encoding="utf-8")
        self.assertIn("재검토일: 2026-07-22", active_risks)


if __name__ == "__main__":
    unittest.main()
