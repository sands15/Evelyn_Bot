from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceTurnDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_turn_composition_before_fast_path_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_turn_dependency_composition = VoiceTurnDependencyComposition("
        )
        fast_path_index = source.index("fast_path_policy_composition = FastPathPolicyComposition(")
        self.assertLess(composition_index, fast_path_index)
        for name in (
            "build_voice_barge_in_continuity_runtime_deps",
            "build_voice_reply_side_effect_deps",
            "build_voice_reply_gate_runtime_deps",
            "build_voice_ingress_runtime_deps",
            "build_voice_ingress_entrypoint_deps",
        ):
            self.assertIn(f"voice_turn_dependency_composition.{name}", source)

    def test_composition_keeps_five_builder_signatures(self) -> None:
        module = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "voice_turn_dependency_composition.py").read_text(
                encoding="utf-8"
            )
        )
        cls = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "VoiceTurnDependencyComposition"
        )
        functions = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
        for name in (
            "build_voice_barge_in_continuity_runtime_deps",
            "build_voice_reply_side_effect_deps",
            "build_voice_reply_gate_runtime_deps",
            "build_voice_ingress_runtime_deps",
            "build_voice_ingress_entrypoint_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_turn_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_turn_dependency_composition.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("schedule_memory_update=deps.schedule_memory_update", source)
        self.assertIn("process_member_audio=deps.process_member_audio", source)
        self.assertIn("schedule_voice_utterance_item=deps.schedule_voice_utterance_item", source)


if __name__ == "__main__":
    unittest.main()
