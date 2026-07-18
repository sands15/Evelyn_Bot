from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class DiscordTtsDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_discord_tts_composition_before_voice_io_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "discord_tts_dependency_composition = DiscordTtsDependencyComposition("
        )
        voice_io_index = source.index("voice_io_composition = VoiceIoComposition(")

        self.assertLess(composition_index, voice_io_index)
        for name in (
            "build_discord_tts_single_runtime_deps",
            "build_discord_tts_stream_runtime_deps",
        ):
            self.assertIn(f"discord_tts_dependency_composition.{name}", source)

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT / "evelyn_core" / "discord_tts_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "DiscordTtsDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_discord_tts_single_runtime_deps",
            "build_discord_tts_stream_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_has_explicit_single_and_stream_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "discord_tts_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("play_cached_answer_audio=deps.play_cached_answer_audio", source)
        self.assertIn("streaming_playback_request_factory=deps.streaming_playback_request_factory", source)


if __name__ == "__main__":
    unittest.main()
