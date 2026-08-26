from __future__ import annotations

import base64
import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
CHECKER = REPO_ROOT / "tools" / "check_docker_runtime.ps1"
LAUNCHER = (
    REPO_ROOT
    / "evelyn_core"
    / "runtime"
    / "launchers"
    / "start_local_background.ps1"
)


class DockerRuntimeCheckerTests(unittest.TestCase):
    def test_windows_powershell_json_numeric_contract(self) -> None:
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("powershell.exe is unavailable")
        script = r'''
$ProgressPreference = 'SilentlyContinue'

function Test-HealthNumbers {
    param($health)
    $buckets = @($health.flashinfer_cuda_graph_buckets)
    return (
        ($health.flashinfer_jit_disabled -is [bool]) -and
        $health.flashinfer_jit_disabled -eq $true -and
        (($health.max_concurrent -is [int]) -or
            ($health.max_concurrent -is [long])) -and
        $health.max_concurrent -eq 1 -and
        (($health.num_step -is [int]) -or
            ($health.num_step -is [long])) -and
        $health.num_step -eq 12 -and
        $buckets.Count -eq 3 -and
        (($buckets[0] -is [decimal]) -or ($buckets[0] -is [double])) -and
        $buckets[0] -eq 2.0 -and
        (($buckets[1] -is [decimal]) -or ($buckets[1] -is [double])) -and
        $buckets[1] -eq 4.0 -and
        (($buckets[2] -is [decimal]) -or ($buckets[2] -is [double])) -and
        $buckets[2] -eq 8.0
    )
}

$valid = '{"flashinfer_jit_disabled":true,"max_concurrent":1,"num_step":12,"flashinfer_cuda_graph_buckets":[2.0,4.0,8.0]}' | ConvertFrom-Json
if (-not (Test-HealthNumbers $valid)) { exit 10 }

$stringValues = '{"flashinfer_jit_disabled":"true","max_concurrent":"1","num_step":"12","flashinfer_cuda_graph_buckets":["2.0","4.0","8.0"]}' | ConvertFrom-Json
if (Test-HealthNumbers $stringValues) { exit 11 }

$booleanNumbers = '{"flashinfer_jit_disabled":true,"max_concurrent":true,"num_step":true,"flashinfer_cuda_graph_buckets":[true,true,true]}' | ConvertFrom-Json
if (Test-HealthNumbers $booleanNumbers) { exit 12 }
'''
        encoded = base64.b64encode(script.encode("utf-16le")).decode(
            "ascii"
        )

        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-EncodedCommand",
                encoded,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )

    def test_tts_health_requires_exact_flashinfer_runtime(self) -> None:
        checker = CHECKER.read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        )
        checker_contract = checker.split(
            'Invoke-RequiredHttp "TTS"',
            1,
        )[1].split('Invoke-RequiredHttp "STT"', 1)[0]
        launcher = LAUNCHER.read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        )
        launcher_contract = launcher.split(
            "function Wait-HttpReady",
            1,
        )[1].split("function New-EncodedCommand", 1)[0]

        expected_strings = {
            "runtime_revision": "omnivoice-0.1.5",
            "flashinfer_revision": "28bc0889d92110491d726a9c79f26a895db5a074",
            "inference_backend": "flashinfer_cuda_graph",
            "flashinfer_python_version": "0.6.15.post1",
            "flashinfer_jit_cache_version": "0.6.15.post1+cu129",
            "torch_version": "2.8.0+cu129",
            "torch_cuda_version": "12.9",
        }
        for field, value in expected_strings.items():
            with self.subTest(field=field, script="checker"):
                self.assertIn(
                    f'$json.{field} -ceq "{value}"',
                    checker_contract,
                )
            with self.subTest(field=field, script="launcher"):
                self.assertIn(
                    f"[string]$health.{field} -ceq '{value}'",
                    launcher_contract,
                )

        for field, value in {
            "status": "healthy",
            "model_id": "k2-fsa/OmniVoice",
            "model_revision": "c5fdb5ccb189668d56333f77ba2629f4cd7535f4",
        }.items():
            self.assertIn(
                f'$json.{field} -eq "{value}"',
                checker_contract,
            )
            self.assertIn(
                f"[string]$health.{field} -eq '{value}'",
                launcher_contract,
            )

        for variable, contract in (
            ("json", checker_contract),
            ("health", launcher_contract),
        ):
            with self.subTest(script=variable):
                self.assertIn(
                    f"(${variable}.flashinfer_jit_disabled -is [bool])",
                    contract,
                )
                self.assertIn(
                    f"${variable}.flashinfer_jit_disabled -eq $true",
                    contract,
                )
                self.assertIn(
                    f"((${variable}.max_concurrent -is [int]) -or",
                    contract,
                )
                self.assertIn(
                    f"(${variable}.max_concurrent -is [long]))",
                    contract,
                )
                self.assertIn(
                    f"${variable}.max_concurrent -eq 1",
                    contract,
                )
                self.assertIn(
                    f"((${variable}.num_step -is [int]) -or",
                    contract,
                )
                self.assertIn(
                    f"(${variable}.num_step -is [long]))",
                    contract,
                )
                self.assertIn(
                    f"${variable}.num_step -eq 12",
                    contract,
                )

        self.assertIn(
            "$flashinferCudaGraphBuckets.Count -eq 3",
            checker_contract,
        )
        self.assertIn(
            "$flashinferCudaGraphBuckets = @($json.flashinfer_cuda_graph_buckets)",
            checker_contract,
        )
        self.assertIn(
            "$flashinferCudaGraphBuckets.Count -eq 3",
            launcher_contract,
        )
        self.assertIn(
            "$flashinferCudaGraphBuckets = @(\n"
            "                $health.flashinfer_cuda_graph_buckets\n"
            "            )",
            launcher_contract,
        )
        for index, value in enumerate((2.0, 4.0, 8.0)):
            expected_decimal = (
                f"(($flashinferCudaGraphBuckets[{index}] -is [decimal]) -or"
            )
            expected_double = (
                f"($flashinferCudaGraphBuckets[{index}] -is [double]))"
            )
            expected_value = (
                f"$flashinferCudaGraphBuckets[{index}] -eq {value:.1f}"
            )
            self.assertIn(expected_decimal, checker_contract)
            self.assertIn(expected_decimal, launcher_contract)
            self.assertIn(expected_double, checker_contract)
            self.assertIn(expected_double, launcher_contract)
            self.assertIn(expected_value, checker_contract)
            self.assertIn(expected_value, launcher_contract)

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
