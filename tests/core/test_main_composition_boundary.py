from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAIN_PATH = PROJECT_ROOT / "main.py"


class MainCompositionBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.lines = cls.source.splitlines()
        cls.tree = ast.parse(cls.source)

    def test_main_stays_within_documented_size_and_width(self) -> None:
        self.assertLessEqual(len(self.lines), 2_500)
        self.assertLessEqual(max(map(len, self.lines)), 158)

    def test_main_contains_no_function_or_global_state_declarations(self) -> None:
        forbidden = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Global, ast.Nonlocal)
        self.assertFalse([node for node in ast.walk(self.tree) if isinstance(node, forbidden)])

    def test_main_contains_no_replacement_character(self) -> None:
        self.assertNotIn("\ufffd", self.source)


if __name__ == "__main__":
    unittest.main()
