from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
CHECKER = REPO_ROOT / "tools" / "check_docker_runtime.ps1"


class DockerRuntimeCheckerTests(unittest.TestCase):
    def test_native_failures_cannot_be_reported_as_success(self) -> None:
        source = CHECKER.read_text(encoding="utf-8").replace("\r\n", "\n")

        for command_and_check in (
            '$dockerVersion = docker version --format "{{.Server.Os}}/{{.Server.Arch}}" 2>$null\n'
            '    if ($LASTEXITCODE -ne 0) {\n'
            '        throw "docker_version_failed"',
            '$null = docker compose @composeArgs config 2>$null\n'
            '    if ($LASTEXITCODE -ne 0) {\n'
            '        throw "docker_compose_config_failed"',
            '$composeStatus = docker compose @composeArgs ps 2>$null\n'
            '    if ($LASTEXITCODE -ne 0) {\n'
            '        throw "docker_compose_ps_failed"',
            '$gpuStatus = nvidia-smi --query-gpu=index,name,memory.used,memory.free '
            '--format=csv,noheader 2>$null\n'
            '    if ($LASTEXITCODE -ne 0) {\n'
            '        throw "nvidia_smi_failed"',
        ):
            self.assertIn(command_and_check, source)

        self.assertIn('exit 1', source)
        self.assertNotIn('2>&1', source)
        self.assertNotIn('$($_.Exception.Message)', source)
        self.assertNotIn('Compose file not found: $ComposeFile', source)
        self.assertNotIn('Compose file found: $ComposeFile', source)


if __name__ == "__main__":
    unittest.main()
