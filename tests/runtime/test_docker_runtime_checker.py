from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
CHECKER = REPO_ROOT / "tools" / "check_docker_runtime.ps1"


class DockerRuntimeCheckerTests(unittest.TestCase):
    def test_native_failures_cannot_be_reported_as_success(self) -> None:
        source = CHECKER.read_text(encoding="utf-8")

        self.assertGreaterEqual(
            source.count("if ($LASTEXITCODE -ne 0)"),
            4,
        )
        for code in (
            "docker_version_failed",
            "docker_compose_config_failed",
            "docker_compose_ps_failed",
            "nvidia_smi_failed",
        ):
            self.assertIn(code, source)
        self.assertNotIn(
            "Docker daemon is not available ($($_.Exception.Message))",
            source,
        )


if __name__ == "__main__":
    unittest.main()
