from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.fast_path_policy_composition import (
    FastPathPolicyComposition,
    FastPathPolicyCompositionDeps,
)


class FastPathPolicyCompositionTests(unittest.TestCase):
    def build(self) -> FastPathPolicyComposition:
        return FastPathPolicyComposition(
            FastPathPolicyCompositionDeps(
                clean_text=lambda value: value.strip(),
                normalize_voice_text=lambda value: value,
                should_force_search_query=lambda _value: False,
            )
        )

    def test_build_runtime_deps_preserves_routing_vocabulary(self) -> None:
        deps = self.build().build_runtime_deps()
        self.assertEqual(deps.control_page_light_request_max_chars, 180)
        self.assertIn("계속", deps.fast_path_continue_markers)
        self.assertIn("검색 없이", deps.fast_path_negated_search_markers)
        self.assertIn("찾아봐", deps.fast_path_search_route_markers)

    def test_main_uses_explicit_binding(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("fast_path_policy_composition = FastPathPolicyComposition(", source)
        self.assertIn(
            "build_fast_path_policy_runtime_deps = fast_path_policy_composition.build_runtime_deps",
            source,
        )


if __name__ == "__main__":
    unittest.main()
