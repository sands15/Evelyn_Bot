from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageSnapshotDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_snapshot_composition_before_runtime_services(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        snapshot_index = source.index(
            "control_page_snapshot_dependency_composition = ControlPageSnapshotDependencyComposition("
        )
        services_index = source.index(
            "control_page_runtime_services_dependency_composition = ("
        )

        self.assertLess(snapshot_index, services_index)
        self.assertIn(
            "control_page=lambda: control_page_composition",
            source[snapshot_index:services_index],
        )
        for name in (
            "build_control_page_minecraft_live_snapshot_runtime_deps",
            "build_control_page_minecraft_snapshot_runtime_deps",
            "build_control_page_background_tasks_runtime_deps",
        ):
            self.assertIn(f"control_page_snapshot_dependency_composition.{name}", source)

    def test_composition_keeps_three_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "control_page_snapshot_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ControlPageSnapshotDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_control_page_minecraft_live_snapshot_runtime_deps",
            "build_control_page_minecraft_snapshot_runtime_deps",
            "build_control_page_background_tasks_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_snapshot_normalization_contracts(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "control_page_snapshot_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn(
            "merge_voyager_status_into_state=merge_voyager_status_into_state", source
        )
        self.assertIn("select_control_page_guild=control_page.select_guild", source)


if __name__ == "__main__":
    unittest.main()
