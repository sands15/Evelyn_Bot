from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))
CORE_ROOT = RUNTIME_ROOT / "evelyn_core"


def function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in ast.walk(tree)
        if isinstance(
            item,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


class MinecraftServiceLeaseBoundaryTests(unittest.TestCase):
    def test_mindcraft_mutations_validate_proof_before_runtime(self) -> None:
        path = CORE_ROOT / "mindcraft_service.py"
        start_source = function_source(path, "start")
        goal_source = function_source(path, "set_goal")
        runtime_source = function_source(
            path,
            "reconcile_world_lease",
        )
        full_source = path.read_text(encoding="utf-8")

        self.assertLess(
            start_source.index("validate_world_lease_request"),
            start_source.index("STATE.start("),
        )
        self.assertLess(
            goal_source.index("validate_world_lease_request"),
            goal_source.index("STATE.restart_for_goal("),
        )
        self.assertIn("self.stop()", runtime_source)
        self.assertIn(
            "load_guarded_world_lease",
            runtime_source,
        )
        self.assertIn(
            "app.cleanup_ctx.append(_world_lease_guard_context)",
            full_source,
        )
        self.assertIn("self._manual_stop = True", full_source)

    def test_legacy_voyager_mutations_and_poller_share_guard(self) -> None:
        path = CORE_ROOT / "voyager_service.py"
        start_source = function_source(path, "start")
        goal_source = function_source(path, "set_goal")
        poller_source = function_source(path, "_status_poller")

        self.assertLess(
            start_source.index("validate_world_lease_request"),
            start_source.index("STATE.start_runner("),
        )
        self.assertLess(
            goal_source.index("validate_world_lease_request"),
            goal_source.index("STATE.persist_goal_override("),
        )
        self.assertIn("load_guarded_world_lease", poller_source)
        self.assertIn("STATE.stop_runner()", poller_source)

    def test_local_bridge_has_no_direct_world_mutation(self) -> None:
        path = CORE_ROOT / "local_io_bridge.py"
        activate_source = function_source(
            path,
            "_activate_minecraft_command",
        )
        apply_source = function_source(
            path,
            "_apply_minecraft_command_request",
        )

        self.assertIn(
            "minecraft_world_authorization_required",
            activate_source,
        )
        self.assertNotIn(".post(", activate_source)
        self.assertIn(
            "minecraft_world_authorization_required",
            apply_source,
        )
        self.assertNotIn(
            "_launch_minecraft_stack(",
            apply_source,
        )

    def test_legacy_start_helper_is_fail_closed(self) -> None:
        source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "launchers"
            / "start_voyager_task.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("world-action lease policy", source)
        self.assertNotIn("Invoke-RestMethod", source)

    def test_process_lifecycle_starts_and_revokes_owner(self) -> None:
        path = CORE_ROOT / "runtime_lifecycle_composition.py"
        startup_source = function_source(
            path,
            "initialize_startup_components",
        )
        restart_source = function_source(
            path,
            "restart_bot_process",
        )
        shutdown_source = function_source(
            path,
            "shutdown_bot_process",
        )

        self.assertIn(
            "ensure_minecraft_world_lease_started",
            startup_source,
        )
        self.assertLess(
            restart_source.index(
                "shutdown_minecraft_world_lease"
            ),
            restart_source.index(
                "launch_runtime_restart_sequence"
            ),
        )
        self.assertLess(
            shutdown_source.index(
                "shutdown_minecraft_world_lease"
            ),
            shutdown_source.index("exit_process"),
        )


if __name__ == "__main__":
    unittest.main()
