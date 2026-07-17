from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))


class ControlPageStateCompositionBoundaryTests(unittest.TestCase):
    def test_main_uses_explicit_binding(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_state_composition.py"
        ).read_text(encoding="utf-8")
        self.assertIn("control_page_state_composition = ControlPageStateComposition(", source)
        self.assertIn("build_control_page_state = control_page_state_composition.build_control_page_state", source)
        self.assertIn("build_control_page_state_from_runtime(", composition)
        self.assertIn("inflight_llm_requests=deps.inflight_llm_requests()", composition)


if __name__ == "__main__":
    unittest.main()
