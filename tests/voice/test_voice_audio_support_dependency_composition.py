from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceAudioSupportDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_audio_support_composition_before_voice_support_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_audio_support_dependency_composition = VoiceAudioSupportDependencyComposition("
        )
        support_index = source.index("voice_support_composition = VoiceSupportComposition(")

        self.assertLess(composition_index, support_index)
        for name in (
            "build_tts_warmup_runtime_deps",
            "build_voice_timing_runtime_deps",
            "build_omnivoice_request_runtime_deps",
            "build_omnivoice_source_runtime_deps",
        ):
            self.assertIn(f"voice_audio_support_dependency_composition.{name}", source)

    def test_composition_keeps_four_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_audio_support_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceAudioSupportDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_tts_warmup_runtime_deps",
            "build_voice_timing_runtime_deps",
            "build_omnivoice_request_runtime_deps",
            "build_omnivoice_source_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_request_to_source_factory_chain(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "voice_audio_support_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn(
            "request_runtime_deps_factory=self.build_omnivoice_request_runtime_deps",
            source,
        )
        self.assertIn("build_voice_timing_runtime_deps(", source)


if __name__ == "__main__":
    unittest.main()
