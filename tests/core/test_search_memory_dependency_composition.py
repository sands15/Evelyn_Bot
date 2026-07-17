from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class SearchMemoryDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_composition_before_memory_maintenance_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "search_memory_dependency_composition = SearchMemoryDependencyComposition("
        )
        maintenance_index = source.index("memory_maintenance_composition = MemoryMaintenanceComposition(")
        self.assertLess(composition_index, maintenance_index)
        for name in (
            "build_memory_update_runtime_deps",
            "build_search_answer_runtime_deps",
            "build_search_followup_runtime_deps",
        ):
            self.assertIn(f"search_memory_dependency_composition.{name}", source)

    def test_composition_keeps_three_builder_signatures(self) -> None:
        module = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "search_memory_dependency_composition.py").read_text(
                encoding="utf-8"
            )
        )
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "SearchMemoryDependencyComposition")
        functions = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
        for name in (
            "build_memory_update_runtime_deps",
            "build_search_answer_runtime_deps",
            "build_search_followup_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_memory_and_search_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "search_memory_dependency_composition.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("run_memory_writebehind_steps=deps.run_memory_writebehind_steps", source)
        self.assertIn("sanitize_model_output=deps.sanitize_model_output", source)
        self.assertIn("record_search_followup_queued=deps.record_search_followup_queued", source)


if __name__ == "__main__":
    unittest.main()
