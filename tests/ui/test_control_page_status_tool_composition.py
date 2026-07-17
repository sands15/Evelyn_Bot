from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageStatusToolCompositionTests(unittest.TestCase):
    def test_main_binds_status_tool_composition_before_control_page_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        status_tool_index = source.index(
            "control_page_status_tool_composition = ControlPageStatusToolComposition("
        )
        control_page_index = source.index(
            "control_page_composition = ControlPageComposition("
        )
        status_tool_source = source[status_tool_index:control_page_index]

        self.assertLess(status_tool_index, control_page_index)
        self.assertIn("control_page=lambda: control_page_composition", source)
        self.assertIn("max_history_items=MAX_HISTORY_ITEMS", status_tool_source)
        self.assertNotIn("max_history_items=MAX_HISTORY,", status_tool_source)
        self.assertIn(
            "control_page_status_tool_composition.build_control_page_status_runtime_deps",
            source,
        )
        self.assertIn(
            "control_page_status_tool_composition.build_control_page_tool_runtime_deps",
            source,
        )

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "control_page_status_tool_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ControlPageStatusToolComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }

        for name in (
            "build_control_page_status_runtime_deps",
            "build_control_page_tool_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_payload_and_tool_contracts_without_main_globals(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "control_page_status_tool_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("ControlPageStatusRuntimeDeps(", source)
        self.assertIn("ControlPageToolRuntimeDeps(", source)
        self.assertIn('"schedule_bot_shutdown": deps.schedule_bot_shutdown', source)


if __name__ == "__main__":
    unittest.main()
