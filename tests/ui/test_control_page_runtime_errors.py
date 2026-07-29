from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
HTML = REPO_ROOT / "docs" / "index.html"
JS = REPO_ROOT / "docs" / "assets" / "evelyn-runtime-errors.js"
CSS = REPO_ROOT / "docs" / "assets" / "evelyn-runtime-errors.css"
SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"


class ControlPageRuntimeErrorsTests(unittest.TestCase):
    def test_mount_and_assets_are_declared(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="runtimeErrorsMount"', html)
        self.assertIn('id="runtimeErrorsRefreshButton"', html)
        self.assertIn("evelyn-runtime-errors.js", html)
        self.assertIn("evelyn-runtime-errors.css", html)

    def test_ui_is_read_only_and_does_not_render_raw_messages(self) -> None:
        source = JS.read_text(encoding="utf-8")
        self.assertIn("/api/control-page/runtime-errors", source)
        self.assertIn("메시지·스택·파일 경로는 수집하지 않습니다.", source)
        self.assertNotIn('method: "POST"', source)
        self.assertNotIn("lastError\"", source)

    def test_server_registers_only_the_read_route(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn(
            'add_get("/api/control-page/runtime-errors", runtime_errors_handler)',
            source,
        )
        self.assertNotIn('add_post("/api/control-page/runtime-errors', source)

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
