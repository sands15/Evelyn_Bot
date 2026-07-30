from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
HTML = REPO_ROOT / "docs" / "index.html"
JS = REPO_ROOT / "docs" / "assets" / "evelyn-ui-action.js"
CSS = REPO_ROOT / "docs" / "assets" / "evelyn-ui-action.css"
SERVER = (
    REPO_ROOT
    / "evelyn_core"
    / "runtime"
    / "evelyn_core"
    / "control_page_server.py"
)


class ControlPageUiActionTests(unittest.TestCase):
    def test_mount_and_assets_are_declared(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="uiActionPreviewForm"', html)
        self.assertIn('id="uiActionMount"', html)
        self.assertIn("evelyn-ui-action.js", html)
        self.assertIn("evelyn-ui-action.css", html)

    def test_ui_requires_preview_and_explicit_confirmation(self) -> None:
        source = JS.read_text(encoding="utf-8")
        self.assertIn("/api/control-page/ui-action/preview", source)
        self.assertIn("/api/control-page/ui-action/apply", source)
        self.assertIn("/api/control-page/session", source)
        self.assertIn("X-Evelyn-CSRF-Token", source)
        self.assertIn("window.confirm", source)
        self.assertIn("userConfirmed: true", source)
        self.assertIn('action: "invoke"', source)
        self.assertIn("FOCUS_HANDOFF_DELAY_SEC = 5", source)
        self.assertIn("FOCUS_HANDOFF_MAX_LATE_MS = 2000", source)
        self.assertIn("deadlineAt: Date.now()", source)
        self.assertIn('setState("focus_handoff")', source)
        self.assertIn("uiActionHandoffCancel", source)
        self.assertIn('armFocusHandoff("preview"', source)
        self.assertIn('armFocusHandoff("apply"', source)
        self.assertNotIn("setTimeout(applyAction", source)
        self.assertNotIn("setTimeout(executeApplyRequest", source)
        self.assertNotIn("rawCommand", source)

        apply_source = source[
            source.index("function applyAction()") :
            source.index('form.addEventListener("submit"')
        ]
        self.assertLess(
            apply_source.index("window.confirm"),
            apply_source.index('armFocusHandoff("apply"'),
        )
        armed_source = source[
            source.index("function runArmedHandoff(") :
            source.index("function armFocusHandoff(")
        ]
        self.assertLess(
            armed_source.index("FOCUS_HANDOFF_MAX_LATE_MS"),
            armed_source.index("executeApplyRequest"),
        )

    def test_server_registers_ui_action_routes(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        for suffix in ("", "/preview", "/apply"):
            self.assertIn(
                f'"/api/control-page/ui-action{suffix}"',
                source,
            )

    def test_javascript_parses(self) -> None:
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
