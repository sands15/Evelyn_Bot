from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
HTML = REPO_ROOT / "docs" / "index.html"
JS = REPO_ROOT / "docs" / "assets" / "evelyn-autonomy-validation.js"
CSS = REPO_ROOT / "docs" / "assets" / "evelyn-validation-wizard.css"
SERVER = (
    REPO_ROOT
    / "evelyn_core"
    / "runtime"
    / "evelyn_core"
    / "control_page_server.py"
)


class ControlPageAutonomyValidationTests(unittest.TestCase):
    def test_mount_and_assets_are_declared(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="autonomyValidationPanel"', html)
        self.assertIn('id="autonomyValidationStartButton"', html)
        self.assertIn('id="autonomyValidationGuildId"', html)
        self.assertIn('id="autonomyValidationGuildId" type="text"', html)
        self.assertIn('id="autonomyValidationMount"', html)
        self.assertIn("evelyn-autonomy-validation.js", html)
        self.assertIn("evelyn-validation-wizard.css", html)

    def test_ui_uses_dry_observer_contract(self):
        source = JS.read_text(encoding="utf-8")
        self.assertIn('suite: "autonomy-p0.v1"', source)
        self.assertIn("dryRun: true", source)
        self.assertIn("userConfirmed: true", source)
        self.assertIn("window.confirm", source)
        for suffix in ("", "/start", "/confirm", "/retry", "/abort"):
            self.assertIn(
                f'"/api/control-page/autonomy-validation{suffix}"',
                source,
            )
        self.assertIn("automaticExecution=false", source)
        self.assertIn("requestQueueWrites=false", source)
        self.assertIn("actionRunId", source)
        self.assertIn("session.cleanupStep", source)
        self.assertNotIn("Number(guildInput.value)", source)

    def test_ui_has_no_execution_or_persistence_escape_hatch(self):
        source = JS.read_text(encoding="utf-8")
        for forbidden in (
            "/api/control-page/chat",
            "/api/control-page/runtime-repair/apply",
            "localStorage",
            "sessionStorage",
            "actionId",
            "argv",
            "cwd",
            "rawGoal",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("innerHTML", source)

    def test_server_registers_autonomy_validation_routes(self):
        source = SERVER.read_text(encoding="utf-8")
        for suffix in ("", "/start", "/confirm", "/retry", "/abort"):
            self.assertIn(
                f'"/api/control-page/autonomy-validation{suffix}"',
                source,
            )

    def test_javascript_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run(
            [node, "--check", str(JS)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
