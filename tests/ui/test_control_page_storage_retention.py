from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
HTML = REPO_ROOT / "docs" / "index.html"
JS = REPO_ROOT / "docs" / "assets" / "evelyn-storage-retention.js"
CSS = REPO_ROOT / "docs" / "assets" / "evelyn-storage-retention.css"
SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"


class ControlPageStorageRetentionTests(unittest.TestCase):
    def test_mount_and_assets_are_declared(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="storageRetentionMount"', html)
        self.assertIn('id="storageRetentionRefreshButton"', html)
        self.assertIn("evelyn-storage-retention.js", html)
        self.assertIn("evelyn-storage-retention.css", html)

    def test_ui_is_read_only_and_states_the_no_delete_policy(self) -> None:
        source = JS.read_text(encoding="utf-8")
        self.assertIn("/api/control-page/storage-retention", source)
        self.assertIn("자동 삭제 꺼짐 · 보고만 수행", source)
        self.assertNotIn('method: "POST"', source)
        self.assertNotIn("/storage-retention/apply", source)

    def test_server_registers_only_the_read_route(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        self.assertIn(
            'add_get("/api/control-page/storage-retention", storage_retention_handler)',
            source,
        )
        self.assertNotIn('add_post("/api/control-page/storage-retention', source)

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
