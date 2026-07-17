from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageUiDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_ui_composition_before_live_snapshot_builder(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "control_page_ui_dependency_composition = ControlPageUiDependencyComposition("
        )
        snapshot_index = source.index(
            "def build_control_page_minecraft_live_snapshot_runtime_deps("
        )

        self.assertLess(composition_index, snapshot_index)
        for name in (
            "build_control_page_ui_runtime_deps",
            "build_control_page_guild_selection_runtime_deps",
            "build_control_page_welcome_runtime_deps",
        ):
            self.assertIn(f"control_page_ui_dependency_composition.{name}", source)

    def test_composition_keeps_three_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "control_page_ui_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ControlPageUiDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_control_page_ui_runtime_deps",
            "build_control_page_guild_selection_runtime_deps",
            "build_control_page_welcome_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_ui_and_welcome_pure_contracts(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "control_page_ui_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("build_main_llm_payload=build_main_llm_payload", source)
        self.assertIn(
            "sanitize_control_page_welcome_text_payload=sanitize_control_page_welcome_text_payload",
            source,
        )


if __name__ == "__main__":
    unittest.main()
