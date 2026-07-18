from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

REPO_ROOT=next(p for p in Path(__file__).resolve().parents if (p/"main.py").exists())
RUNTIME_ROOT=REPO_ROOT/"evelyn_core"/"runtime"
if str(RUNTIME_ROOT) not in sys.path: sys.path.insert(0,str(RUNTIME_ROOT))
from evelyn_core.memory_maintenance_composition import MemoryMaintenanceComposition, MemoryMaintenanceCompositionDeps


class MemoryMaintenanceCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self,**overrides):
        values=dict(memory_update=lambda:"memory-deps",memory_locks={},background_vault_tasks={},vault_last_maintenance_at={},
            attach_current_task=Mock(return_value="task"),detach_task=Mock(),run_long_term_memory_update=AsyncMock(),
            collect_memory_layers=Mock(),ask_summary_llm=AsyncMock(),is_context_size_error=Mock(),should_log_voice_timing=Mock(),
            memory_fact_limit=10,memory_loop_limit=5,raw_limit=100,run_vault_maintenance_once=Mock(return_value={}),
            create_scoped_task=Mock(),lock_factory=asyncio.Lock,sleep=AsyncMock(),to_thread=AsyncMock(),current_task=Mock(),
            monotonic=Mock(return_value=1000.0),getenv=Mock(return_value="900"),log=Mock())
        values.update(overrides); deps=MemoryMaintenanceCompositionDeps(**values)
        return MemoryMaintenanceComposition(deps),deps

    async def test_long_term_update_detaches_task(self):
        composition,deps=self.build(); await composition.update_long_term_memory(1,"u","a")
        deps.run_long_term_memory_update.assert_awaited_once(); deps.detach_task.assert_called_once_with(None,"task")

    def test_vault_interval_gate_skips_recent_run(self):
        composition,deps=self.build(vault_last_maintenance_at={1:500.0})
        composition.schedule_memory_vault_maintenance(1); deps.create_scoped_task.assert_not_called()

    def test_memory_update_uses_live_typed_deps(self):
        composition,_=self.build()
        with patch("evelyn_core.memory_maintenance_composition.schedule_memory_update_from_runtime",return_value={"scheduled":True}) as runtime:
            result=composition.schedule_memory_update(1,"u","a",source="voice")
        self.assertEqual(result,{"scheduled":True}); self.assertEqual(runtime.call_args.kwargs["deps"],"memory-deps")

    def test_main_uses_lazy_summary_binding(self):
        source=(REPO_ROOT/"main.py").read_text(encoding="utf-8")
        self.assertIn("memory_maintenance_composition = MemoryMaintenanceComposition(",source)
        self.assertIn("ask_summary_llm=lambda *args, **kwargs: ask_summary_llm(*args, **kwargs)",source)


if __name__=="__main__": unittest.main()
