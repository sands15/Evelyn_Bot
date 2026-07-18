from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceResponseDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_response_composition_before_llm_route_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_response_dependency_composition = VoiceResponseDependencyComposition("
        )
        route_index = source.index("llm_route_composition = LlmRouteComposition(")

        self.assertLess(composition_index, route_index)
        for name in (
            "build_voice_response_runtime_deps",
            "build_main_llm_runtime_deps",
            "build_ask_llm_once_runtime_deps",
            "build_voice_stream_chunk_deps",
        ):
            self.assertIn(f"voice_response_dependency_composition.{name}", source)

    def test_composition_keeps_four_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_response_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceResponseDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_voice_response_runtime_deps",
            "build_main_llm_runtime_deps",
            "build_ask_llm_once_runtime_deps",
            "build_voice_stream_chunk_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_response_and_stream_contracts(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_response_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("prepare_route_context=deps.prepare_route_context", source)
        self.assertIn("execute_main_llm_once=deps.execute_main_llm_once", source)
        self.assertIn("tts_first_chunk_min_chars=deps.tts_first_chunk_min_chars", source)


if __name__ == "__main__":
    unittest.main()
