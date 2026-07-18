from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageSearchTextDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_search_text_composition_before_input_builder(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "control_page_search_text_dependency_composition = ControlPageSearchTextDependencyComposition("
        )
        input_index = source.index(
            "control_page_input_dependency_composition = ControlPageInputDependencyComposition("
        )

        self.assertLess(composition_index, input_index)
        self.assertIn(
            "control_page_search_text_dependency_composition.build_control_page_search_runtime_deps",
            source,
        )
        self.assertIn(
            "control_page_search_text_dependency_composition.build_control_page_text_runtime_deps",
            source,
        )

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "control_page_search_text_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ControlPageSearchTextDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_control_page_search_runtime_deps",
            "build_control_page_text_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_lock_and_turn_scope_contracts(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "control_page_search_text_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("return self.deps.session_locks.setdefault", source)
        self.assertIn("turn_scope_factory=TurnScope", source)


if __name__ == "__main__":
    unittest.main()
