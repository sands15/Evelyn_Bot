from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceInputSupportDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_input_support_composition_before_voice_support_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_input_support_dependency_composition = VoiceInputSupportDependencyComposition("
        )
        support_index = source.index("voice_support_composition = VoiceSupportComposition(")

        self.assertLess(composition_index, support_index)
        for name in (
            "build_stt_text_runtime_deps",
            "build_stt_transcription_runtime_deps",
            "build_discord_voice_connection_runtime_deps",
        ):
            self.assertIn(f"voice_input_support_dependency_composition.{name}", source)

    def test_composition_keeps_three_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_input_support_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceInputSupportDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_stt_text_runtime_deps",
            "build_stt_transcription_runtime_deps",
            "build_discord_voice_connection_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_stt_text_factory_without_global_lookup(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "voice_input_support_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("return build_stt_text_runtime_deps(", source)
        self.assertIn("transcribe_via_service=deps.transcribe_via_service", source)
        self.assertIn("process_member_audio=deps.process_member_audio", source)


if __name__ == "__main__":
    unittest.main()
