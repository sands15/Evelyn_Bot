from __future__ import annotations

import os
import runpy
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
LEGACY_ROOT = REPO_ROOT / "external" / "codex_gateway"
LEGACY_MODULE = LEGACY_ROOT / "codex_gateway.py"
LEGACY_BATCH = LEGACY_ROOT / "run_codex_gateway.bat"


class LegacyCodexGatewayDisabledTests(unittest.TestCase):
    def test_python_gateway_fails_before_host_process_spawn(self) -> None:
        with patch("subprocess.run") as spawn:
            with self.assertRaisesRegex(
                RuntimeError,
                "legacy_host_native_codex_gateway_disabled",
            ):
                runpy.run_path(str(LEGACY_MODULE), run_name="legacy_codex_gateway")
        spawn.assert_not_called()

    def test_batch_is_fixed_failure_without_host_launcher(self) -> None:
        self.assertEqual(
            LEGACY_BATCH.read_text(encoding="utf-8").splitlines(),
            [
                "@echo off",
                ">&2 echo [Evelyn] Legacy host-native Codex gateway is disabled. Use evelyn_core\\start_codex_gateway.bat.",
                "exit /b 1",
            ],
        )

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher")
    def test_batch_exits_nonzero(self) -> None:
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", str(LEGACY_BATCH)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Legacy host-native Codex gateway is disabled", result.stderr)


if __name__ == "__main__":
    unittest.main()
