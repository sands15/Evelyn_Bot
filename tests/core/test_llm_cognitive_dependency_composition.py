from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class LlmCognitiveDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_composition_before_response_context_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "llm_cognitive_dependency_composition = LlmCognitiveDependencyComposition("
        )
        context_index = source.index("response_context_composition = ResponseContextComposition(")
        self.assertLess(composition_index, context_index)
        for name in (
            "build_cognitive_followup_runtime_deps",
            "build_summary_json_llm_runtime_deps",
            "build_router_json_llm_runtime_deps",
            "build_llm_route_runtime_deps",
            "build_cognitive_state_runtime_deps",
        ):
            self.assertIn(f"llm_cognitive_dependency_composition.{name}", source)

    def test_composition_keeps_five_public_builder_signatures(self) -> None:
        module = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "llm_cognitive_dependency_composition.py").read_text(
                encoding="utf-8"
            )
        )
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "LlmCognitiveDependencyComposition")
        functions = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
        for name in (
            "build_cognitive_followup_runtime_deps",
            "build_summary_json_llm_runtime_deps",
            "build_router_json_llm_runtime_deps",
            "build_llm_route_runtime_deps",
            "build_cognitive_state_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_shared_json_llm_factory(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "llm_cognitive_dependency_composition.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("def _build_json_llm_runtime_deps(", source)
        self.assertIn("ask_router_llm=deps.ask_router_llm", source)
        self.assertIn("build_cognitive_state_messages=deps.build_cognitive_state_messages", source)


if __name__ == "__main__":
    unittest.main()
