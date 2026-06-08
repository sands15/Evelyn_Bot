from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.skills import delivery, search  # noqa: E402
from evelyn_core.skills.registry import skill_registry  # noqa: E402


class ControlPageSearchRouteTests(unittest.TestCase):
    def test_control_page_can_use_search_and_delivery_skills(self) -> None:
        self.assertIn("control_page", search.sources)
        self.assertIn("control_page", delivery.sources)

        self.assertTrue(skill_registry.find_by_route("search_executor", source="control_page"))
        self.assertTrue(skill_registry.find_by_route("delivery", source="control_page"))


if __name__ == "__main__":
    unittest.main()
