import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.context_pipeline import ContextPolicy  # noqa: E402
from evelyn_core.turn_budget import build_turn_execution_budget  # noqa: E402


class TurnExecutionBudgetTests(unittest.TestCase):
    def test_budget_carries_route_policy_timeout_and_fallback(self) -> None:
        budget = build_turn_execution_budget(
            router_timeout_sec=8,
            context_timeout_sec=7,
            memory_timeout_sec=6,
            fallback_route="main_direct",
            router_enabled=True,
            context_policy=ContextPolicy(priority="accuracy"),
        )

        self.assertEqual(budget.router_timeout_sec, 8)
        self.assertEqual(budget.context_timeout_sec, 7)
        self.assertEqual(budget.memory_timeout_sec, 6)
        self.assertEqual(budget.fallback_route, "main_direct")
        self.assertEqual(budget.priority, "accuracy")
        self.assertTrue(budget.router_enabled)

    def test_budget_drops_memory_timeout_when_policy_skips_memory(self) -> None:
        budget = build_turn_execution_budget(
            router_timeout_sec=8,
            context_timeout_sec=7,
            memory_timeout_sec=6,
            fallback_route="main_direct",
            router_enabled=False,
            context_policy=ContextPolicy(needs_memory=False, needs_runtime_state=False),
            fallback_reason="fast_path",
        )

        self.assertEqual(budget.memory_timeout_sec, 0)
        self.assertFalse(budget.needs_memory)
        self.assertFalse(budget.needs_runtime_state)
        self.assertFalse(budget.router_enabled)
        self.assertEqual(budget.fallback_reason, "fast_path")


if __name__ == "__main__":
    unittest.main()
