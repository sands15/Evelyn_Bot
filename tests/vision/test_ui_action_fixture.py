from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
FIXTURE = (
    REPO_ROOT
    / "evelyn_core"
    / "runtime"
    / "launchers"
    / "show_ui_action_test_fixture.ps1"
)


class UiActionFixtureTests(unittest.TestCase):
    def test_fixture_is_reversible_and_has_one_named_invoke_target(self) -> None:
        source = FIXTURE.read_text(encoding="utf-8")

        self.assertIn("System.Windows.Forms", source)
        self.assertIn("Evelyn UI Action Test Fixture", source)
        self.assertIn("evelynSafeInvokeButton", source)
        self.assertIn("Evelyn Safe Invoke Test", source)
        self.assertIn("$invokeButton.Enabled = $false", source)
        self.assertIn("$invokeButton.Enabled = $true", source)
        self.assertIn("expectedPostcondition = 'target_disabled'", source)
        self.assertIn("reversible = $true", source)
        self.assertIn("storesTargetText = $false", source)
        self.assertEqual(
            source.count("[System.Windows.Forms.Button]::new()"),
            1,
        )
        self.assertIn("[System.Windows.Forms.LinkLabel]::new()", source)
        self.assertNotIn("TopMost", source)

    def test_fixture_cannot_execute_the_production_action_boundary(self) -> None:
        source = FIXTURE.read_text(encoding="utf-8")

        for forbidden in (
            "InvokePattern",
            "Invoke-Expression",
            "Start-Process",
            "SendKeys",
            "SetCursorPos",
            "mouse_event",
            "/api/control-page/ui-action/apply",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_powershell_parses(self) -> None:
        powershell = shutil.which("powershell")
        if not powershell:
            self.skipTest("Windows PowerShell is not installed")
        command = (
            "[void][scriptblock]::Create("
            f"(Get-Content -Raw -LiteralPath '{str(FIXTURE).replace(chr(39), chr(39) * 2)}')"
            "); 'ok'"
        )
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
