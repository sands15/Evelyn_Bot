from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class DiscordAppDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_dependency_composition_before_discord_app_root(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "discord_app_dependency_composition = DiscordAppDependencyComposition("
        )
        app_index = source.index("discord_app_composition = DiscordAppComposition(")

        self.assertLess(composition_index, app_index)
        for name in (
            "build_discord_text_message_handler_deps",
            "build_discord_command_session_runtime_deps",
        ):
            self.assertIn(f"discord_app_dependency_composition.{name}", source)

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT / "evelyn_core" / "discord_app_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "DiscordAppDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_discord_text_message_handler_deps",
            "build_discord_command_session_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_resolves_bot_user_at_builder_call_time(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "discord_app_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("bot_user=deps.bot_user()", source)
        self.assertIn("record_command_assistant_turn=deps.record_command_assistant_turn", source)


if __name__ == "__main__":
    unittest.main()
