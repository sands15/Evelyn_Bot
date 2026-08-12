from __future__ import annotations

import asyncio
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy_runtime_composition import (  # noqa: E402
    AutonomyRuntimeComposition,
    MinecraftAutonomyRouteComposition,
    MinecraftAutonomyRouteCompositionDeps,
)


class AutonomyRuntimeCompositionTests(unittest.TestCase):
    def test_main_binds_autonomy_runtime_composition_before_guild_reset(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_index = source.index(
            "autonomy_runtime_composition = AutonomyRuntimeComposition("
        )
        reset_index = source.index(
            "guild_runtime_reset_composition = GuildRuntimeResetComposition("
        )

        self.assertLess(composition_index, reset_index)
        self.assertIn(
            "get_or_create_autonomy_engine = autonomy_runtime_composition.get_or_create_autonomy_engine",
            source,
        )
        self.assertNotIn("get_or_create_autonomy_engine_from_runtime", source)
        self.assertIn(
            "record_runtime_error=lambda code, exc: discord_runtime_status.record_error(code, exc)",
            source,
        )

    def test_composition_keeps_public_builder_and_engine_signatures(self) -> None:
        module = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "autonomy_runtime_composition.py").read_text(
                encoding="utf-8"
            )
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "AutonomyRuntimeComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }

        self.assertEqual(
            [arg.arg for arg in functions["build_autonomy_runtime_factory_deps"].args.args],
            ["self"],
        )
        self.assertEqual(
            [arg.arg for arg in functions["get_or_create_autonomy_engine"].args.args],
            ["self", "guild_id"],
        )

    def test_composition_does_not_reach_into_main_globals(self) -> None:
        source = (
            RUNTIME_ROOT / "evelyn_core" / "autonomy_runtime_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn("AutonomyRuntimeFactoryDeps(", source)
        self.assertIn(
            "record_runtime_error=deps.record_runtime_error",
            source,
        )

    def test_minecraft_route_composition_requires_a_router(self) -> None:
        create_engine = Mock()
        composition = MinecraftAutonomyRouteComposition(
            MinecraftAutonomyRouteCompositionDeps(
                create_engine=create_engine,
                get_router=lambda _guild_id: None,
            )
        )

        self.assertFalse(asyncio.run(composition.enable(7)))
        self.assertFalse(asyncio.run(composition.disable(7)))
        self.assertFalse(composition.is_enabled(7))
        create_engine.assert_called_once_with(7)

    def test_minecraft_route_composition_controls_exact_domain(self) -> None:
        router = Mock()
        router.enable_domain = AsyncMock(return_value=True)
        router.disable_domain = AsyncMock(return_value=True)
        router.is_domain_enabled.return_value = True
        create_engine = Mock()
        composition = MinecraftAutonomyRouteComposition(
            MinecraftAutonomyRouteCompositionDeps(
                create_engine=create_engine,
                get_router=lambda guild_id: router if guild_id == 9 else None,
            )
        )

        self.assertTrue(asyncio.run(composition.enable(9)))
        self.assertTrue(asyncio.run(composition.disable(9)))
        self.assertTrue(composition.is_enabled(9))
        router.enable_domain.assert_awaited_once_with("minecraft")
        router.disable_domain.assert_awaited_once_with("minecraft")
        router.is_domain_enabled.assert_called_once_with("minecraft")


if __name__ == "__main__":
    unittest.main()
