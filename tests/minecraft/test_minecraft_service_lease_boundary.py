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
        for guarded_source in (
            start_source,
            goal_source,
            runtime_source,
        ):
            self.assertIn(
                "owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH",
                guarded_source,
            )
        self.assertIn(
            'WORLD_LEASE_OWNER_CLAIM_PATH = (',
            full_source,
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
        full_source = path.read_text(encoding="utf-8")

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
        for guarded_source in (
            start_source,
            goal_source,
            poller_source,
        ):
            self.assertIn(
                "owner_claim_path=WORLD_LEASE_OWNER_CLAIM_PATH",
                guarded_source,
            )
        self.assertIn(
            'WORLD_LEASE_OWNER_CLAIM_PATH = (',
            full_source,
        )

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
            "_restart_bot_process_owned",
        )
        restart_launcher_source = function_source(
            path,
            "_build_restart_launcher",
        )
        shutdown_source = function_source(
            path,
            "_shutdown_bot_process_owned",
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
                "launch_and_exit()"
            ),
        )
        self.assertIn(
            "launch_runtime_restart_sequence",
            restart_launcher_source,
        )
        self.assertLess(
            shutdown_source.index(
                "shutdown_minecraft_world_lease"
            ),
            shutdown_source.index("finish(0)"),
        )

    def test_split_runtime_uses_bot_api_as_single_owner(self) -> None:
        fast_api_path = CORE_ROOT / "fast_control_api.py"
        fast_api_source = fast_api_path.read_text(encoding="utf-8")
        create_app_source = function_source(
            fast_api_path,
            "create_app",
        )
        main_source = (REPO_ROOT / "main.py").read_text(
            encoding="utf-8"
        )
        compose_source = (
            REPO_ROOT / "docker-compose.fast-control.yml"
        ).read_text(encoding="utf-8")
        discord_section = compose_source.split(
            "  discord_bot:\n",
            1,
        )[1]

        self.assertIn(
            '"/internal/minecraft-world-lease"',
            create_app_source,
        )
        self.assertIn(
            '"/internal/minecraft-world-lease/{action}"',
            create_app_source,
        )
        self.assertIn(
            "minecraft_world_lease_delegation_authorized",
            fast_api_source,
        )
        self.assertIn(
            "execute_minecraft_world_lease_delegation",
            fast_api_source,
        )
        self.assertIn(
            "MinecraftWorldLeaseRemote(",
            main_source,
        )
        self.assertIn(
            "if MINECRAFT_WORLD_LEASE_OWNER_URL",
            main_source,
        )
        self.assertIn(
            'MINECRAFT_WORLD_LEASE_OWNER_URL: "http://bot_api:8798"',
            discord_section,
        )

    def test_internal_owner_api_has_no_arbitrary_execution_fields(
        self,
    ) -> None:
        delegation_source = (
            CORE_ROOT
            / "minecraft_world_lease_delegation.py"
        ).read_text(encoding="utf-8")

        for action in ('"connect"', '"disconnect"', '"goal"'):
            self.assertIn(action, delegation_source)
        for forbidden in (
            "subprocess",
            "shell=True",
            'payload.get("argv")',
            'payload.get("command")',
            'payload.get("cwd")',
        ):
            self.assertNotIn(forbidden, delegation_source)


if __name__ == "__main__":
    unittest.main()
