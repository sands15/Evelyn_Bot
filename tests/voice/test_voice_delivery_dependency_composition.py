from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceDeliveryDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_delivery_composition_before_llm_route_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        delivery_index = source.index(
            "voice_delivery_dependency_composition = VoiceDeliveryDependencyComposition("
        )
        route_index = source.index("llm_route_composition = LlmRouteComposition(")

        self.assertLess(delivery_index, route_index)
        for name in (
            "build_voice_turn_entry_runtime_deps",
            "build_voice_delivery_runtime_deps",
            "build_discord_text_reply_runtime_deps",
        ):
            self.assertIn(f"voice_delivery_dependency_composition.{name}", source)

    def test_composition_keeps_three_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_delivery_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceDeliveryDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_voice_turn_entry_runtime_deps",
            "build_voice_delivery_runtime_deps",
            "build_discord_text_reply_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_answer_delivery_and_tts_split_policies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_delivery_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("build_answer_payload_from_text=build_answer_payload_from_text", source)
        self.assertIn("build_delivery_plan=build_delivery_plan", source)
        self.assertIn("split_tts_sentences=split_tts_sentences", source)


if __name__ == "__main__":
    unittest.main()
