from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

REPO_ROOT=next(p for p in Path(__file__).resolve().parents if (p/"main.py").exists())
RUNTIME_ROOT=REPO_ROOT/"evelyn_core"/"runtime"
if str(RUNTIME_ROOT) not in sys.path: sys.path.insert(0,str(RUNTIME_ROOT))
from evelyn_core.memory_maintenance_composition import MemoryMaintenanceComposition, MemoryMaintenanceCompositionDeps
from evelyn_core.guild_runtime_reset import MEMORY_BACKGROUND_WORK_INFLIGHT, require_guild_runtime_reset_ready
from evelyn_core.turn_lifecycle import TurnScope, TurnScopeRegistry


class MemoryMaintenanceCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self,**overrides):
        values=dict(memory_update=lambda:"memory-deps",memory_locks={},background_vault_tasks={},vault_last_maintenance_at={},
            attach_current_task=Mock(return_value="task"),detach_task=Mock(),run_long_term_memory_update=AsyncMock(),
            collect_memory_layers=Mock(),ask_summary_llm=AsyncMock(),is_context_size_error=Mock(),should_log_voice_timing=Mock(),
            memory_fact_limit=10,memory_loop_limit=5,raw_limit=100,run_vault_maintenance_once=Mock(return_value={}),
            create_scoped_task=Mock(),lock_factory=asyncio.Lock,sleep=AsyncMock(),to_thread=AsyncMock(),current_task=asyncio.current_task,
            monotonic=Mock(return_value=1000.0),getenv=Mock(return_value="900"),log=Mock())
        values.update(overrides); deps=MemoryMaintenanceCompositionDeps(**values)
        return MemoryMaintenanceComposition(deps),deps

    async def test_long_term_update_detaches_task(self):
        composition,deps=self.build(); await composition.update_long_term_memory(1,"u","a",source_turn_id="turn-1")
        deps.run_long_term_memory_update.assert_awaited_once()
        self.assertEqual(deps.run_long_term_memory_update.await_args.kwargs["source_turn_id"],"turn-1")
        deps.detach_task.assert_called_once_with(None,"task")

    async def test_long_term_update_rejects_failed_result_and_detaches_task(self):
        composition,deps=self.build(
            run_long_term_memory_update=AsyncMock(return_value={"ok":False})
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "^long_term_memory_update_failed$",
        ):
            await composition.update_long_term_memory(1,"u","a")

        deps.detach_task.assert_called_once_with(None,"task")

    def test_vault_interval_gate_skips_recent_run(self):
        composition,deps=self.build(vault_last_maintenance_at={1:500.0})
        composition.schedule_memory_vault_maintenance(1); deps.create_scoped_task.assert_not_called()

    def test_memory_update_uses_live_typed_deps(self):
        composition,_=self.build()
        with patch("evelyn_core.memory_maintenance_composition.schedule_memory_update_from_runtime",return_value={"scheduled":True}) as runtime:
            result=composition.schedule_memory_update(1,"u","a",source="voice")
        self.assertEqual(result,{"scheduled":True}); self.assertEqual(runtime.call_args.kwargs["deps"],"memory-deps")

    async def test_pending_recomposition_uses_short_retry_gate(self):
        clock = Mock(return_value=1000.0)
        create_task = Mock(
            side_effect=lambda coro, turn_scope=None: asyncio.create_task(
                coro
            )
        )
        getenv = Mock(
            side_effect=lambda key, default: {
                "MEMORY_VAULT_MAINTENANCE_INTERVAL_SEC": "900",
                "MEMORY_DERIVATION_RETRY_INTERVAL_SEC": "60",
            }.get(key, default)
        )
        to_thread = AsyncMock(
            return_value={
                "derivation_recomposition": {
                    "status": "skipped_sub_llm_unavailable",
                    "pendingNoteIds": [
                        "private-derived-note-id"
                    ],
                }
            }
        )
        log = Mock()
        composition, deps = self.build(
            monotonic=clock,
            create_scoped_task=create_task,
            getenv=getenv,
            to_thread=to_thread,
            log=log,
        )

        composition.schedule_memory_vault_maintenance(1)
        await deps.background_vault_tasks[1]
        self.assertNotIn(1, deps.background_vault_tasks)

        self.assertEqual(
            deps.vault_last_maintenance_at[1],
            160.0,
        )
        logged = " ".join(
            str(call.args[0])
            for call in log.call_args_list
            if call.args
        )
        self.assertIn("count=1", logged)
        self.assertIn("retrySec=60.0", logged)
        self.assertNotIn("private-derived-note-id", logged)

        clock.return_value = 1059.0
        composition.schedule_memory_vault_maintenance(1)
        self.assertEqual(create_task.call_count, 1)

        clock.return_value = 1060.0
        composition.schedule_memory_vault_maintenance(1)
        self.assertEqual(create_task.call_count, 2)
        await deps.background_vault_tasks[1]

    async def test_clear_recomposition_keeps_normal_interval(self):
        clock = Mock(return_value=1000.0)
        create_task = Mock(
            side_effect=lambda coro, turn_scope=None: asyncio.create_task(
                coro
            )
        )
        composition, deps = self.build(
            monotonic=clock,
            create_scoped_task=create_task,
            to_thread=AsyncMock(
                return_value={
                    "derivation_recomposition": {
                        "status": "clear",
                        "pendingNoteIds": [],
                    }
                }
            ),
        )

        composition.schedule_memory_vault_maintenance(1)
        await deps.background_vault_tasks[1]
        self.assertNotIn(1, deps.background_vault_tasks)

        self.assertEqual(
            deps.vault_last_maintenance_at[1],
            1000.0,
        )

    async def test_vault_maintenance_failure_logs_only_exception_type(self):
        private_error = "PRIVATE_VAULT_MAINTENANCE C:/secret/memory-token"
        create_task = Mock(
            side_effect=lambda coro, turn_scope=None: asyncio.create_task(coro)
        )
        log = Mock()
        composition, deps = self.build(
            create_scoped_task=create_task,
            to_thread=AsyncMock(side_effect=RuntimeError(private_error)),
            log=log,
        )

        composition.schedule_memory_vault_maintenance(1)
        await deps.background_vault_tasks[1]

        log.assert_called_once_with(
            "[MEMORY VAULT] maintenance failed guild=1 errorType=RuntimeError"
        )
        self.assertNotIn(private_error, repr(log.call_args_list))
        self.assertNotIn(1, deps.background_vault_tasks)

    async def test_cancelled_vault_task_tracks_real_worker_and_blocks_every_guild_reset(self):
        worker_started = threading.Event()
        release_worker = threading.Event()

        def blocking_maintenance(_guild_id):
            worker_started.set()
            if not release_worker.wait(2.0):
                raise TimeoutError("vault worker was not released")
            return {}

        registry = TurnScopeRegistry()
        turn_scope = TurnScope("turn-1")
        composition, deps = self.build(
            run_vault_maintenance_once=blocking_maintenance,
            create_scoped_task=registry.create_scoped_task,
            to_thread=asyncio.to_thread,
        )
        reset_deps = SimpleNamespace(
            autonomy_engines={},
            autonomy_cognitive_refresh_tasks={},
            background_search_tasks={},
            background_memory_tasks={},
            background_memory_vault_tasks=deps.background_vault_tasks,
        )

        composition.schedule_memory_vault_maintenance(
            1,
            turn_scope=turn_scope,
        )
        maintenance_task = deps.background_vault_tasks[1]
        self.assertTrue(
            await asyncio.wait_for(
                asyncio.to_thread(worker_started.wait, 1.0),
                timeout=2.0,
            )
        )

        turn_scope.cancel("replaced")
        await asyncio.sleep(0)

        self.assertFalse(maintenance_task.done())
        self.assertIs(deps.background_vault_tasks[1], maintenance_task)
        maintenance_task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(maintenance_task.done())
        self.assertIs(deps.background_vault_tasks[1], maintenance_task)
        for guild_id in (1, 2):
            with self.assertRaisesRegex(
                RuntimeError,
                f"^{MEMORY_BACKGROUND_WORK_INFLIGHT}$",
            ):
                require_guild_runtime_reset_ready(
                    guild_id,
                    deps=reset_deps,
                )

        release_worker.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(maintenance_task, timeout=2.0)

        self.assertNotIn(1, deps.background_vault_tasks)
        for guild_id in (1, 2):
            require_guild_runtime_reset_ready(
                guild_id,
                deps=reset_deps,
            )

    def test_main_uses_lazy_summary_binding(self):
        source=(REPO_ROOT/"main.py").read_text(encoding="utf-8")
        self.assertIn("memory_maintenance_composition = MemoryMaintenanceComposition(",source)
        self.assertIn("ask_summary_llm=lambda *args, **kwargs: ask_summary_llm(*args, **kwargs)",source)


if __name__=="__main__": unittest.main()
