from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.cognitive_refresh_composition import CognitiveRefreshComposition, CognitiveRefreshCompositionDeps


class CognitiveRefreshCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, **overrides):
        values=dict(state=lambda: "state-deps", background_tasks={}, runtime_session_key=lambda **_: "guild:1",
            create_scoped_task=Mock(), current_turn_id=Mock(return_value="turn-1"),
            monotonic=Mock(side_effect=[1.0, 1.25]), current_task=Mock(),
            log_turn_event=Mock(), log=Mock())
        values.update(overrides); deps=CognitiveRefreshCompositionDeps(**values)
        return CognitiveRefreshComposition(deps), deps

    async def test_update_delegates_with_typed_state(self):
        composition, _ = self.build()
        with patch("evelyn_core.cognitive_refresh_composition.update_cognitive_state_from_runtime", new=AsyncMock(return_value={"ok": True})) as runtime:
            result=await composition.update_cognitive_state(1,"hello",session_key="s")
        self.assertEqual(result,{"ok":True}); self.assertEqual(runtime.await_args.kwargs["deps"],"state-deps")

    def test_schedule_replaces_existing_task(self):
        old=Mock(); old.done.return_value=False; created=object()
        composition,deps=self.build(background_tasks={"memory":old}, create_scoped_task=Mock(return_value=created))
        composition.refresh_cognitive_state_in_background=Mock(return_value=object())
        composition.schedule_cognitive_refresh(1,"hello",reason="turn",session_memory_key="memory")
        old.cancel.assert_called_once_with(); self.assertIs(deps.background_tasks["memory"],created)

    def test_missing_guild_does_not_schedule(self):
        composition,deps=self.build(); composition.schedule_cognitive_refresh(None,"hello",reason="turn")
        deps.create_scoped_task.assert_not_called()

    async def test_background_failure_logs_only_exception_type(self):
        private_session = "PRIVATE_COGNITIVE_SESSION"
        private_error = "PRIVATE_COGNITIVE_EXCEPTION C:/secret/memory-token"
        current_task = asyncio.current_task()
        background_tasks = {private_session: current_task}
        log = Mock()
        composition, _ = self.build(
            background_tasks=background_tasks,
            current_task=lambda: current_task,
            log=log,
        )
        composition.update_cognitive_state = AsyncMock(
            side_effect=RuntimeError(private_error)
        )

        await composition.refresh_cognitive_state_in_background(
            1,
            "hello",
            reason="turn",
            session_memory_key=private_session,
        )

        log.assert_called_once_with(
            "[COGNITIVE] background refresh failed errorType=RuntimeError"
        )
        self.assertNotIn(private_session, repr(log.call_args_list))
        self.assertNotIn(private_error, repr(log.call_args_list))
        self.assertNotIn(private_session, background_tasks)

    def test_main_uses_explicit_bindings(self):
        source=(REPO_ROOT/"main.py").read_text(encoding="utf-8")
        self.assertIn("cognitive_refresh_composition = CognitiveRefreshComposition(",source)
        self.assertIn("schedule_cognitive_refresh = cognitive_refresh_composition.schedule_cognitive_refresh",source)


if __name__ == "__main__": unittest.main()
