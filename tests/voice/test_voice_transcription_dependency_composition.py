from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceTranscriptionDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_transcription_composition_before_session_gate(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_transcription_dependency_composition = VoiceTranscriptionDependencyComposition("
        )
        session_gate_index = source.index(
            "voice_member_pipeline_dependency_composition = VoiceMemberPipelineDependencyComposition("
        )

        self.assertLess(composition_index, session_gate_index)
        self.assertIn(
            "voice_transcription_dependency_composition.build_voice_stt_execution_deps",
            source,
        )
        self.assertIn(
            "voice_transcription_dependency_composition.build_voice_transcript_finalize_deps",
            source,
        )

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_transcription_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceTranscriptionDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_voice_stt_execution_deps",
            "build_voice_transcript_finalize_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_stt_and_transcript_policies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "voice_transcription_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("run_full_stt_with_optional_rescore=run_full_stt_with_optional_rescore", source)
        self.assertIn("build_final_transcript_flow=build_final_transcript_flow", source)


if __name__ == "__main__":
    unittest.main()
