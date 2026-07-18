from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class LocalTtsDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_local_tts_composition_before_delivery_entry_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "local_tts_dependency_composition = LocalTtsDependencyComposition("
        )
        delivery_index = source.index("delivery_entry_composition = DeliveryEntryComposition(")

        self.assertLess(composition_index, delivery_index)
        for name in (
            "build_local_tts_single_runtime_deps",
            "build_local_tts_stream_runtime_deps",
        ):
            self.assertIn(f"local_tts_dependency_composition.{name}", source)

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT / "evelyn_core" / "local_tts_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "LocalTtsDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_local_tts_single_runtime_deps",
            "build_local_tts_stream_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_single_and_stream_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "local_tts_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("clean_tts_text=deps.clean_tts_text", source)
        self.assertIn("prefetch_tts_sources=deps.prefetch_tts_sources", source)
        self.assertIn("cleanup_prepared_tts_item=deps.cleanup_prepared_tts_item", source)


if __name__ == "__main__":
    unittest.main()
