from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class VoiceMemberPipelineDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_member_pipeline_composition_before_voice_io(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "voice_member_pipeline_dependency_composition = VoiceMemberPipelineDependencyComposition("
        )
        voice_io_index = source.index("voice_io_composition = VoiceIoComposition(")

        self.assertLess(composition_index, voice_io_index)
        for name in (
            "build_voice_session_gate_deps",
            "build_voice_reply_dispatch_deps",
            "build_voice_transcript_reply_deps",
            "build_voice_member_audio_pipeline_deps",
        ):
            self.assertIn(f"voice_member_pipeline_dependency_composition.{name}", source)

    def test_composition_keeps_public_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "voice_member_pipeline_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "VoiceMemberPipelineDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        self.assertEqual(
            [arg.arg for arg in functions["build_voice_transcript_reply_deps"].args.args],
            ["self", "guild"],
        )
        for name in (
            "build_voice_session_gate_deps",
            "build_voice_reply_dispatch_deps",
            "build_voice_member_audio_pipeline_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_all_member_pipeline_stage_bindings(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "voice_member_pipeline_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        for binding in (
            "prepare_audio_ingress=prepare_voice_audio_ingress_from_runtime",
            "run_wake_probe=run_voice_wake_probe_from_runtime",
            "run_tts_interrupt_gate=run_voice_tts_interrupt_gate_from_runtime",
            "run_stt_execution=run_voice_stt_execution_from_runtime",
            "finalize_transcript=finalize_voice_transcript_from_runtime",
            "run_session_gate=run_voice_session_gate_from_runtime",
            "dispatch_voice_reply=dispatch_voice_reply_from_runtime",
        ):
            self.assertIn(binding, source)


if __name__ == "__main__":
    unittest.main()
