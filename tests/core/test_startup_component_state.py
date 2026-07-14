from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.startup_component_state import (  # noqa: E402
    StartupComponentRuntimeDeps,
    mark_startup_component_from_runtime,
    mark_startup_component_state,
    startup_component_done_from_runtime,
    startup_component_done_from_state,
)


class StartupComponentStateTests(unittest.TestCase):
    def test_mark_startup_component_sanitizes_status_and_detail(self) -> None:
        state: dict[str, dict[str, Any]] = {}

        mark_startup_component_state(
            state,
            "tts",
            "  done  ",
            "  ready\n",
            now=lambda: 123.0,
        )

        self.assertEqual(
            state["tts"],
            {
                "status": "done",
                "detail": "ready",
                "updatedAt": 123.0,
            },
        )

    def test_mark_startup_component_defaults_blank_status_to_pending(self) -> None:
        state: dict[str, dict[str, Any]] = {}

        mark_startup_component_state(state, "main", "", now=lambda: 456.0)

        self.assertEqual(state["main"]["status"], "pending")
        self.assertEqual(state["main"]["updatedAt"], 456.0)

    def test_startup_component_done_requires_done_status(self) -> None:
        state = {
            "done_key": {"status": "done"},
            "failed_key": {"status": "failed"},
        }

        self.assertTrue(startup_component_done_from_state(state, "done_key"))
        self.assertFalse(startup_component_done_from_state(state, "failed_key"))
        self.assertFalse(startup_component_done_from_state(state, "missing"))

    def test_runtime_wrapper_uses_dependency_state(self) -> None:
        state: dict[str, dict[str, Any]] = {}
        deps = StartupComponentRuntimeDeps(state, now=lambda: 1.23)
        mark_startup_component_from_runtime("tts", "done", "ok", deps=deps)

        self.assertEqual(state["tts"]["status"], "done")
        self.assertEqual(state["tts"]["detail"], "ok")

    def test_runtime_wrapper_done_reads_dependency_state(self) -> None:
        state = {
            "done_key": {"status": "done"},
        }
        deps = StartupComponentRuntimeDeps(state, now=lambda: 1.23)

        self.assertTrue(startup_component_done_from_runtime("done_key", deps=deps))
        self.assertFalse(startup_component_done_from_runtime("missing", deps=deps))


if __name__ == "__main__":
    unittest.main()
