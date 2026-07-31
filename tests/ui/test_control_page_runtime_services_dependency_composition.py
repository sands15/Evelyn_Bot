from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageRuntimeServicesDependencyCompositionTests(unittest.TestCase):
    def test_main_binds_runtime_services_composition_to_runtime_state(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn(
            "set_refresh_task=control_page_runtime_services_refresh_task_state.set",
            source,
        )
        self.assertNotIn(
            "def _set_control_page_runtime_services_refresh_task(", source
        )
        for name in (
            "build_control_page_runtime_services_runtime_deps",
            "build_control_page_runtime_services_probe_runtime_deps",
        ):
            self.assertIn(
                f"control_page_runtime_services_dependency_composition.{name}", source
            )
        self.assertIn(
            "voyager_alive_probe=lambda: "
            "get_minecraft_client().is_functionally_ready(",
            source,
        )
        self.assertNotIn(
            "voyager_alive_probe=lambda: "
            "get_minecraft_client().is_service_alive(",
            source,
        )

    def test_composition_keeps_both_builder_signatures(self) -> None:
        module = ast.parse(
            (
                RUNTIME_ROOT
                / "evelyn_core"
                / "control_page_runtime_services_dependency_composition.py"
            ).read_text(encoding="utf-8")
        )
        cls = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ControlPageRuntimeServicesDependencyComposition"
        )
        functions = {
            node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)
        }
        for name in (
            "build_control_page_runtime_services_runtime_deps",
            "build_control_page_runtime_services_probe_runtime_deps",
        ):
            self.assertEqual([arg.arg for arg in functions[name].args.args], ["self"])

    def test_composition_owns_probe_and_error_payload_contracts(self) -> None:
        source = (
            RUNTIME_ROOT
            / "evelyn_core"
            / "control_page_runtime_services_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("globals()", source)
        self.assertNotIn("import main", source)
        self.assertIn(
            "probe_runtime_services_once=probe_control_page_runtime_services", source
        )
        self.assertIn(
            "build_runtime_services_error_payload=build_control_page_runtime_services_error_payload",
            source,
        )


if __name__ == "__main__":
    unittest.main()
