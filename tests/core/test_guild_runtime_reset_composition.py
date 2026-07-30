from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.guild_runtime_reset_composition import (  # noqa: E402
    GuildRuntimeResetComposition,
)


class GuildRuntimeResetCompositionTests(unittest.TestCase):
    def test_main_binds_guild_reset_composition_before_continuity_tracker(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "guild_runtime_reset_composition = GuildRuntimeResetComposition("
        )
        continuity_index = source.index(
            "voice_barge_in_continuity_tracker = VoiceBargeInContinuityTracker("
        )

        self.assertLess(composition_index, continuity_index)
        self.assertIn(
            "reset_guild_runtime_state = guild_runtime_reset_composition.reset_guild_runtime_state",
            source,
        )
        self.assertNotIn("reset_guild_runtime_state_from_runtime", source)

    def test_composition_keeps_public_builder_and_reset_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "guild_runtime_reset_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "GuildRuntimeResetComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }

        self.assertEqual(
            [arg.arg for arg in functions["build_guild_runtime_reset_deps"].args.args],
            ["self"],
        )
        self.assertEqual(
            [arg.arg for arg in functions["reset_guild_runtime_state"].args.args],
            ["self", "guild_id"],
        )

    def test_composition_uses_explicit_dependencies(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "guild_runtime_reset_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("build_guild_runtime_reset_deps_from_runtime(", source)
        self.assertIn("self.deps.flush_session_continuity()", source)

    def test_reset_flushes_continuity_after_runtime_state_is_cleared(self) -> None:
        events: list[str] = []
        composition = GuildRuntimeResetComposition(
            SimpleNamespace(
                flush_session_continuity=lambda: events.append("flush")
            )
        )
        composition.build_guild_runtime_reset_deps = Mock(return_value=object())

        with patch(
            "evelyn_core.guild_runtime_reset_composition."
            "reset_guild_runtime_state_from_runtime",
            side_effect=lambda *args, **kwargs: events.append("reset"),
        ):
            composition.reset_guild_runtime_state(7)

        self.assertEqual(events, ["reset", "flush"])


if __name__ == "__main__":
    unittest.main()
