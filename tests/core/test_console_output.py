from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.console_output import ConsoleOutputFilter  # noqa: E402


class ConsoleOutputFilterTests(unittest.TestCase):
    def test_disabled_filter_forwards_all_output(self) -> None:
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        output = ConsoleOutputFilter(
            enabled=False,
            output=lambda *args, **kwargs: calls.append((args, kwargs)) or "sent",
            allowed_prefixes=("[KEEP]",),
        )

        self.assertEqual(output("hidden", sep="|"), "sent")
        self.assertEqual(calls, [(('hidden',), {"sep": "|"})])

    def test_enabled_filter_only_forwards_allowed_prefixes(self) -> None:
        lines: list[str] = []
        output = ConsoleOutputFilter(
            enabled=True,
            output=lambda *args, **_kwargs: lines.append(" ".join(map(str, args))),
            allowed_prefixes=("[KEEP]", "🎤 ["),
        )

        self.assertIsNone(output("drop", 1))
        output("  [KEEP]", "message")
        output("🎤 [voice]", "message")
        self.assertEqual(lines, ["  [KEEP] message", "🎤 [voice] message"])

    def test_main_has_no_top_level_function_definitions(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        functions = [
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]

        self.assertEqual(functions, [])
        self.assertNotIn("def print(", source)
        self.assertIn("print = ConsoleOutputFilter(", source)


if __name__ == "__main__":
    unittest.main()
