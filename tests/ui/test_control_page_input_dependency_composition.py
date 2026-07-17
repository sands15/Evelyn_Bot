from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageInputDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_input_composition_before_state_composition(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        input_index = source.index(
            "control_page_input_dependency_composition = ControlPageInputDependencyComposition("
        )
        state_index = source.index(
            "control_page_state_composition = ControlPageStateComposition("
        )

        self.assertLess(input_index, state_index)
        self.assertIn("control_page=lambda: control_page_composition", source[input_index:state_index])
        self.assertIn(
            "control_page_input_dependency_composition.build_control_page_input_runtime_deps",
            source,
        )

    def test_composition_keeps_builder_signature(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "control_page_input_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ControlPageInputDependencyComposition"
        )
        function = next(
            node
            for node in cls.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "build_control_page_input_runtime_deps"
        )

        self.assertEqual([arg.arg for arg in function.args.args], ["self"])

    def test_composition_owns_all_input_routing_policies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "control_page_input_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("cheap_control_page_tool_decision=cheap_control_page_tool_decision", source)
        self.assertIn("should_force_search_query=should_force_search_query", source)
        self.assertIn("answer_control_page_text=control_page.answer_text", source)


if __name__ == "__main__":
    unittest.main()
