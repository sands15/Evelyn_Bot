from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.voyager.navigation_matrix import (
    CELL_LABEL,
    CELLS,
    DEFAULT_WORKERS,
    FIXTURE_RELATIVE,
    MAX_WORKERS,
    OWNER_VALUE,
    PRODUCTION_CONTAINER,
    PRODUCTION_PORT,
    SCRIPT_REPO_ROOT,
    MatrixSafetyError,
    _cell_report,
    _empty_evidence,
    _fixture_commands,
    _prepare_server_directory,
    _remove_owned_container,
    _write_cell_profile,
    cleanup_plan,
    docker_run_command,
    dry_run_manifest,
    preflight_run,
    run_matrix,
    validate_cells,
)


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _passing_cell(cell):
    evidence = _empty_evidence()
    evidence.update({
        "connected_fresh": True,
        "goal_manager_off": True,
        "path_updates": 2,
        "nonempty_path_updates": 2,
        "verified_goal_reached": 1,
        "walked_cm": 50,
        "logs_mined": 3,
        "wooden_pickaxes_crafted": 1,
        "pickaxe_inventory": True,
    })
    if cell.id == "blocked_batch_fallback":
        evidence["no_path_updates"] = 1
    return _cell_report(
        cell,
        evidence,
        infrastructure_valid=True,
        setup_verified=True,
        cleanup_verified=True,
        error_code=None,
    )


class NavigationMatrixTests(unittest.TestCase):
    def test_fixture_uses_valid_1_21_11_spawn_commands(self) -> None:
        commands = _fixture_commands(CELLS[0])
        self.assertIn("gamerule respawn_radius 0", commands)
        self.assertIn("setworldspawn 0 100 0", commands)
        self.assertNotIn("gamerule spawn_radius 0", commands)
        self.assertNotIn("setworldspawn 0 100 0 0", commands)

    def test_fixed_cells_are_unique_and_default_parallelism_is_bounded(self) -> None:
        validate_cells()
        self.assertEqual(
            [(cell.id, cell.port) for cell in CELLS],
            [
                ("direct_flat", 25575),
                ("detour_wall", 25576),
                ("stair_up", 25577),
                ("blocked_batch_fallback", 25578),
            ],
        )
        self.assertEqual([cell.username for cell in CELLS], [
            "EvelynNav01", "EvelynNav02", "EvelynNav03", "EvelynNav04",
        ])
        self.assertEqual(len({cell.username for cell in CELLS}), 4)
        self.assertEqual(len({cell.container_name for cell in CELLS}), 4)
        self.assertTrue(all(len(cell.username) <= 16 for cell in CELLS))
        self.assertEqual(DEFAULT_WORKERS, 2)
        self.assertEqual(MAX_WORKERS, 4)
        manifest = dry_run_manifest(SCRIPT_REPO_ROOT)
        self.assertEqual(manifest["defaultWorkers"], 2)
        self.assertEqual(manifest["maxWorkers"], 4)
        self.assertTrue(all(cell["serverAuth"] == "offline" for cell in manifest["cells"]))

    def test_server_and_bot_use_unique_offline_profiles_without_auth_mounts(self) -> None:
        seen_commands = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for cell in CELLS:
                cell_root = root / cell.id
                bot_root = cell_root / "bot"
                bot_root.mkdir(parents=True)
                _write_cell_profile(bot_root, cell)
                _prepare_server_directory(cell_root / "server", cell)
                properties = (cell_root / "server/server.properties").read_text(encoding="utf-8")
                profile = json.loads((bot_root / "profile.json").read_text(encoding="utf-8"))
                command = docker_run_command(
                    SCRIPT_REPO_ROOT,
                    SCRIPT_REPO_ROOT / FIXTURE_RELATIVE / "cells" / cell.id,
                    cell,
                    "run123",
                )
                joined = "\n".join(command)
                settings = json.loads(next(
                    value.removeprefix("SETTINGS_JSON=")
                    for value in command
                    if value.startswith("SETTINGS_JSON=")
                ))

                self.assertIn("online-mode=false", properties)
                self.assertIn("enforce-secure-profile=false", properties)
                self.assertIn(f"server-port={cell.port}", properties)
                self.assertEqual(profile["name"], cell.username)
                self.assertIn(cell.username, profile["conversing"])
                self.assertEqual(settings["auth"], "offline")
                self.assertEqual(settings["port"], cell.port)
                self.assertEqual(settings["profiles"], ["/app/runtime_artifacts/profile.json"])
                self.assertIn(f"--name\n{cell.container_name}", joined)
                self.assertIn(f"evelyn.validation.owner={OWNER_VALUE}", command)
                self.assertIn(f"{CELL_LABEL}={cell.id}", command)
                self.assertIn(f"MINEFLAYER_AUTH=offline", command)
                self.assertIn(f"MINECRAFT_USERNAME={cell.username}", command)
                self.assertNotIn("microsoft", joined.lower())
                self.assertNotIn("auth-seed", joined)
                self.assertNotIn("/app/bot_profiles", joined)
                self.assertNotIn("MINEFLAYER_PROFILES_FOLDER", joined)
                self.assertNotIn("cp -R", command[-1])
                seen_commands.append(command)
        self.assertEqual(len(seen_commands), 4)

    def test_preflight_checks_every_port_image_production_and_container_collision(self) -> None:
        commands = []
        probed = []

        def runner(command, **_kwargs):
            command = tuple(command)
            commands.append(command)
            if command[:3] == ("docker", "inspect", "--format"):
                return _result(returncode=1)
            if command[:2] == ("docker", "inspect"):
                return _result(returncode=1)
            return _result()

        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / "server.jar"
            java = Path(directory) / "java.exe"
            jar.touch()
            java.touch()
            preflight_run(
                SCRIPT_REPO_ROOT,
                SCRIPT_REPO_ROOT / FIXTURE_RELATIVE,
                jar,
                str(java),
                command_runner=runner,
                port_probe=lambda port: probed.append(port) or False,
                artifact_exists=lambda _path: False,
            )

        self.assertEqual(probed, [PRODUCTION_PORT, *(cell.port for cell in CELLS)])
        self.assertIn(("docker", "info"), commands)
        self.assertTrue(any(command[:3] == ("docker", "image", "inspect") for command in commands))
        self.assertTrue(any(command[-1] == PRODUCTION_CONTAINER for command in commands))
        for cell in CELLS:
            self.assertIn(("docker", "inspect", cell.container_name), commands)

        with self.assertRaisesRegex(MatrixSafetyError, "root_must_not_exist"):
            preflight_run(
                SCRIPT_REPO_ROOT,
                SCRIPT_REPO_ROOT / FIXTURE_RELATIVE,
                Path("missing.jar"),
                "missing-java",
                artifact_exists=lambda _path: True,
            )

    def test_run_matrix_uses_two_workers_by_default_and_rejects_more_than_four(self) -> None:
        active = 0
        peak = 0
        lock = threading.Lock()
        first_pair = threading.Event()

        def cell_runner(cell, *_args, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    first_pair.set()
            self.assertTrue(first_pair.wait(1))
            time.sleep(0.02)
            with lock:
                active -= 1
            return _passing_cell(cell)

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "matrix"
            report = run_matrix(
                SCRIPT_REPO_ROOT,
                artifact,
                Path("server.jar"),
                "java",
                preflight=lambda *_args, **_kwargs: None,
                cell_runner=cell_runner,
                port_probe=lambda _port: False,
                production_checker=lambda _runner: True,
                containers_absent_checker=lambda _runner: True,
            )
        self.assertEqual(peak, 2)
        self.assertEqual(report["workers"], 2)
        self.assertTrue(report["passed"])

        with self.assertRaisesRegex(MatrixSafetyError, "workers_out_of_range"):
            run_matrix(
                SCRIPT_REPO_ROOT,
                Path("unused"),
                Path("server.jar"),
                "java",
                workers=5,
            )

    def test_fallback_requires_recovery_and_global_cleanup_is_a_hard_gate(self) -> None:
        fallback = CELLS[-1]
        evidence = _empty_evidence()
        evidence.update({
            "connected_fresh": True,
            "goal_manager_off": True,
            "path_updates": 1,
            "nonempty_path_updates": 1,
            "verified_goal_reached": 1,
            "walked_cm": 1,
            "logs_mined": 1,
            "wooden_pickaxes_crafted": 1,
            "pickaxe_inventory": True,
        })
        without_recovery = _cell_report(
            fallback,
            evidence,
            infrastructure_valid=True,
            setup_verified=True,
            cleanup_verified=True,
            error_code=None,
        )
        self.assertFalse(without_recovery["acceptance"]["fallback_recovery_observed"])
        self.assertFalse(without_recovery["passed"])
        evidence["partial_updates"] = 10
        self.assertFalse(_cell_report(
            fallback,
            evidence,
            infrastructure_valid=True,
            setup_verified=True,
            cleanup_verified=True,
            error_code=None,
        )["acceptance"]["fallback_recovery_observed"])
        evidence["stuck_resets"] = 1
        self.assertTrue(_cell_report(
            fallback,
            evidence,
            infrastructure_valid=True,
            setup_verified=True,
            cleanup_verified=True,
            error_code=None,
        )["passed"])

        with tempfile.TemporaryDirectory() as directory:
            report = run_matrix(
                SCRIPT_REPO_ROOT,
                Path(directory) / "matrix",
                Path("server.jar"),
                "java",
                preflight=lambda *_args, **_kwargs: None,
                cell_runner=lambda cell, *_args, **_kwargs: _passing_cell(cell),
                port_probe=lambda _port: False,
                production_checker=lambda _runner: True,
                containers_absent_checker=lambda _runner: False,
            )
        self.assertFalse(report["cleanupVerified"])
        self.assertFalse(report["passed"])

    def test_owned_cleanup_requires_exact_cell_label_before_removal(self) -> None:
        cell = CELLS[0]
        container_id = "a" * 64
        removed = []

        def runner(command, **_kwargs):
            if command[1:3] == ["inspect", "--format"]:
                return _result(stdout=(
                    f"{container_id}|/{cell.container_name}|{OWNER_VALUE}|run123|wrong-cell\n"
                ))
            if command[1:3] == ["rm", "--force"]:
                removed.append(command[-1])
                return _result()
            self.fail(f"unexpected command: {command}")

        with self.assertRaisesRegex(MatrixSafetyError, "ownership_lost"):
            _remove_owned_container(container_id, cell, "run123", runner)
        self.assertEqual(removed, [])

    def test_cleanup_plan_contains_only_fixed_nonproduction_targets(self) -> None:
        plan = cleanup_plan(SCRIPT_REPO_ROOT)
        self.assertEqual(plan["ports"], [cell.port for cell in CELLS])
        self.assertEqual(plan["container_names"], [cell.container_name for cell in CELLS])
        self.assertNotIn(PRODUCTION_CONTAINER, plan["container_names"])
        self.assertFalse(plan["wildcards"])
        with self.assertRaisesRegex(MatrixSafetyError, "artifact_root_not_exact"):
            cleanup_plan(SCRIPT_REPO_ROOT, artifact_root=SCRIPT_REPO_ROOT / "other")


if __name__ == "__main__":
    unittest.main()
