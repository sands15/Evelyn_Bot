from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())


class SourceHygieneTests(unittest.TestCase):
    def test_primary_source_files_do_not_start_with_utf8_bom(self) -> None:
        for relative_path in (
            "main.py",
            "docs/index.html",
            "docs/assets/evelyn-live2d.js",
            "tests/runtime/test_shutdown_scripts.py",
        ):
            with self.subTest(path=relative_path):
                data = (REPO_ROOT / relative_path).read_bytes()
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), f"{relative_path} starts with a UTF-8 BOM")


if __name__ == "__main__":
    unittest.main()
