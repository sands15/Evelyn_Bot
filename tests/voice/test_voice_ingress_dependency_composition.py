from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceIngressDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_ingress_composition_before_member_pipeline_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_ingress_dependency_composition = VoiceIngressDependencyComposition("
        )
        pipeline_index = source.index(
            "voice_member_pipeline_dependency_composition = VoiceMemberPipelineDependencyComposition("
        )

        self.assertLess(composition_index, pipeline_index)
        self.assertIn(
            "voice_ingress_dependency_composition.build_voice_audio_ingress_runtime_deps",
            source,
        )
        self.assertIn(
            "voice_ingress_dependency_composition.build_voice_wake_probe_runtime_deps",
            source,
        )

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_ingress_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceIngressDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_voice_audio_ingress_runtime_deps",
            "build_voice_wake_probe_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_pure_audio_and_wake_policies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_ingress_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("prepare_stt_audio=prepare_stt_audio", source)
        self.assertIn("interpret_wake_probe_result=interpret_wake_probe_result", source)


if __name__ == "__main__":
    unittest.main()
