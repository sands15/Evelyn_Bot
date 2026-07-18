from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceTtsControlDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_tts_control_composition_before_voice_io_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_tts_control_dependency_composition = VoiceTtsControlDependencyComposition("
        )
        voice_io_index = source.index("voice_io_composition = VoiceIoComposition(")

        self.assertLess(composition_index, voice_io_index)
        for name in (
            "build_tts_interrupt_runtime_deps",
            "build_cached_tts_runtime_deps",
            "build_voice_tts_interrupt_gate_deps",
        ):
            self.assertIn(f"voice_tts_control_dependency_composition.{name}", source)

    def test_composition_keeps_three_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_tts_control_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceTtsControlDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_tts_interrupt_runtime_deps",
            "build_cached_tts_runtime_deps",
            "build_voice_tts_interrupt_gate_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_control_and_cache_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_tts_control_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("speaker_verification_applies=deps.speaker_verification_applies", source)
        self.assertIn("resolve_cached_tts_audio_path=deps.resolve_cached_tts_audio_path", source)
        self.assertIn("should_interrupt_tts=deps.should_interrupt_tts", source)


if __name__ == "__main__":
    unittest.main()
