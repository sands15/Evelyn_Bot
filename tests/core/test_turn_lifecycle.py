import asyncio
import contextlib
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.turn_lifecycle import TurnScope, TurnState  # noqa: E402


class TurnLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_scope_keeps_existing_task_cancel_contract(self) -> None:
        async def wait_forever() -> None:
            await asyncio.Event().wait()

        scope = TurnScope("turn-1")
        task = asyncio.create_task(wait_forever())
        scope.register_task(task)

        scope.cancel(reason="replaced_by_new_turn")
        await asyncio.sleep(0)
        with contextlib.suppress(asyncio.CancelledError):
            await task

        self.assertTrue(scope.cancelled)
        self.assertEqual(scope.cancel_reason, "replaced_by_new_turn")
        self.assertEqual(scope.state, TurnState.CANCELLED)
        self.assertTrue(task.cancelled())

    async def test_turn_scope_tracks_transitions_and_snapshot(self) -> None:
        scope = TurnScope("turn-1")

        scope.transition(TurnState.ROUTING, reason="route")
        scope.transition("llm_running", reason="main_llm")

        snapshot = scope.snapshot()

        self.assertEqual(scope.state, TurnState.LLM_RUNNING)
        self.assertEqual([item.state for item in scope.transition_log], [TurnState.ROUTING, TurnState.LLM_RUNNING])
        self.assertEqual(snapshot["state"], "llm_running")
        self.assertEqual(snapshot["transitions"][0]["reason"], "route")

    async def test_turn_scope_stale_check_uses_turn_id_and_cancel_state(self) -> None:
        scope = TurnScope("turn-1")

        self.assertTrue(scope.is_current("turn-1"))
        self.assertFalse(scope.is_stale("turn-1"))
        self.assertFalse(scope.is_current("turn-2"))
        self.assertTrue(scope.is_stale("turn-2"))

        scope.cancel(reason="barge_in")

        self.assertFalse(scope.is_current("turn-1"))
        self.assertTrue(scope.is_stale("turn-1"))


if __name__ == "__main__":
    unittest.main()
