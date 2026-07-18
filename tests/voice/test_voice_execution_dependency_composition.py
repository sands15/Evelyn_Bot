from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_execution_dependency_composition import (
    VoiceExecutionDependencyComposition,
    VoiceExecutionDependencyCompositionDeps,
)


class VoiceExecutionDependencyCompositionBoundaryTests(unittest.TestCase):
    def test_public_types_are_importable(self) -> None:
        self.assertTrue(VoiceExecutionDependencyComposition)
        self.assertTrue(VoiceExecutionDependencyCompositionDeps)

    def test_main_uses_explicit_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "voice_execution_dependency_composition.py"
        ).read_text(encoding="utf-8")
        self.assertIn("voice_execution_dependency_composition = VoiceExecutionDependencyComposition(", source)
        self.assertIn("build_voice_route_execution_deps = (", source)
        self.assertIn("build_voice_main_llm_streaming_deps = (", source)
        self.assertIn("return VoiceRouteExecutionDeps(", composition)
        self.assertIn("build_voice_main_llm_streaming_deps_from_runtime(", composition)


if __name__ == "__main__":
    unittest.main()
