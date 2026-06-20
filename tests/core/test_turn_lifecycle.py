import asyncio
import contextlib
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.turn_lifecycle import TurnScope, TurnScopeRegistry, TurnState  # noqa: E402


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

    async def test_registry_replaces_scope_and_counts_stale_cancel(self) -> None:
        registry = TurnScopeRegistry()
        old_scope = TurnScope("old")
        new_scope = TurnScope("new")

        self.assertIsNone(registry.replace_room_scope("room-1", old_scope))
        replaced = registry.replace_room_scope("room-1", new_scope)

        self.assertIs(replaced, old_scope)
        self.assertTrue(old_scope.cancelled)
        self.assertEqual(old_scope.cancel_reason, "replaced_by_new_turn")
        self.assertIs(registry.get_room_scope("room-1"), new_scope)
        self.assertEqual(registry.cancelled_stale_turn_count, 1)

    async def test_registry_scoped_task_unregisters_on_completion(self) -> None:
        registry = TurnScopeRegistry()
        scope = TurnScope("turn-1")

        async def finish_soon() -> str:
            await asyncio.sleep(0)
            return "done"

        task = registry.create_scoped_task(finish_soon(), turn_scope=scope)
        self.assertIn(task, scope.tasks)

        result = await task
        await asyncio.sleep(0)

        self.assertEqual(result, "done")
        self.assertNotIn(task, scope.tasks)

    async def test_registry_cancel_matching_prefix_cancels_and_removes(self) -> None:
        registry = TurnScopeRegistry()
        matching = TurnScope("turn-1")
        other = TurnScope("turn-2")
        registry.replace_room_scope("guild:1:voice", matching)
        registry.replace_room_scope("guild:2:voice", other)

        cancelled = registry.cancel_matching_prefix("guild:1:")

        self.assertEqual(cancelled, 1)
        self.assertTrue(matching.cancelled)
        self.assertIsNone(registry.get_room_scope("guild:1:voice"))
        self.assertIs(registry.get_room_scope("guild:2:voice"), other)


if __name__ == "__main__":
    unittest.main()
