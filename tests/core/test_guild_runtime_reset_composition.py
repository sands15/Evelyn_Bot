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
from evelyn_core.guild_runtime_reset import (  # noqa: E402
    AUTONOMY_COGNITIVE_REFRESH_INFLIGHT,
    AUTONOMY_RUNTIME_ACTIVE,
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
        self.assertIn("self.deps.reset_session_continuity_guild(", source)

    def test_reset_is_wrapped_by_durable_continuity_revocation(self) -> None:
        events: list[str] = []

        def reset_continuity_guild(guild_id, reset_runtime_state):
            events.append(f"revoke:{guild_id}")
            reset_runtime_state()
            events.append("flush")
            return {"state": "ready"}

        composition = GuildRuntimeResetComposition(
            SimpleNamespace(
                reset_session_continuity_guild=reset_continuity_guild,
                reset_search_followup_recovery_guild=lambda guild_id: events.append(
                    f"search:{guild_id}"
                ),
            )
        )
        composition.build_guild_runtime_reset_deps = Mock(
            return_value=SimpleNamespace(
                autonomy_cognitive_refresh_tasks={},
                autonomy_engines={},
            )
        )

        with patch(
            "evelyn_core.guild_runtime_reset_composition."
            "reset_guild_runtime_state_from_runtime",
            side_effect=lambda *args, **kwargs: events.append("reset"),
        ):
            composition.reset_guild_runtime_state(7)

        self.assertEqual(
            events,
            ["revoke:7", "reset", "flush", "search:7"],
        )

    def test_live_refresh_fails_before_continuity_revocation(self) -> None:
        events: list[str] = []
        refresh_task = Mock()
        refresh_task.done.return_value = False
        composition = GuildRuntimeResetComposition(
            SimpleNamespace(
                reset_session_continuity_guild=(
                    lambda *_args: events.append("revoke")
                ),
            )
        )
        composition.build_guild_runtime_reset_deps = Mock(
            return_value=SimpleNamespace(
                autonomy_cognitive_refresh_tasks={7: refresh_task},
                autonomy_engines={},
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            f"^{AUTONOMY_COGNITIVE_REFRESH_INFLIGHT}$",
        ):
            composition.reset_guild_runtime_state(7)

        self.assertEqual(events, [])
        refresh_task.cancel.assert_not_called()

    def test_live_autonomy_fails_before_continuity_revocation(self) -> None:
        events: list[str] = []
        autonomy_task = Mock()
        autonomy_task.done.return_value = False
        composition = GuildRuntimeResetComposition(
            SimpleNamespace(
                reset_session_continuity_guild=(
                    lambda *_args: events.append("revoke")
                ),
            )
        )
        composition.build_guild_runtime_reset_deps = Mock(
            return_value=SimpleNamespace(
                autonomy_cognitive_refresh_tasks={},
                autonomy_engines={
                    7: SimpleNamespace(
                        _task=autonomy_task,
                        state=SimpleNamespace(
                            enabled=True,
                            status="running",
                        ),
                    )
                },
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            f"^{AUTONOMY_RUNTIME_ACTIVE}$",
        ):
            composition.reset_guild_runtime_state(7)

        self.assertEqual(events, [])
        autonomy_task.cancel.assert_not_called()

    def test_continuity_persistence_failure_fails_the_reset_command(self) -> None:
        autonomy_task = Mock()
        autonomy_task.done.return_value = True
        runtime_state = SimpleNamespace(
            enabled=False,
            status="idle",
            current_goal=SimpleNamespace(summary="must remain"),
        )
        composition = GuildRuntimeResetComposition(
            SimpleNamespace(
                reset_session_continuity_guild=lambda guild_id, callback: {
                    "state": "error",
                    "lastErrorCode": "continuity_reset_not_durable",
                }
            )
        )
        composition.build_guild_runtime_reset_deps = Mock(
            return_value=SimpleNamespace(
                autonomy_cognitive_refresh_tasks={},
                autonomy_engines={
                    7: SimpleNamespace(
                        _task=autonomy_task,
                        state=runtime_state,
                    )
                },
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "continuity_reset_not_durable",
        ):
            composition.reset_guild_runtime_state(7)

        self.assertEqual(
            runtime_state.current_goal.summary,
            "must remain",
        )


if __name__ == "__main__":
    unittest.main()
