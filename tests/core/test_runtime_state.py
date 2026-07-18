from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_state import (  # noqa: E402
    AsyncWorkerStarter,
    LazyResourceProvider,
    RuntimeCounter,
    RuntimeValue,
)


class RuntimeStateTests(unittest.TestCase):
    def test_runtime_value_gets_and_sets_typed_value(self) -> None:
        state = RuntimeValue[str | None](None)
        state.set("ready")
        self.assertEqual(state.get(), "ready")

    def test_runtime_counter_increments_and_floors_at_zero(self) -> None:
        counter = RuntimeCounter()
        counter.increment()
        counter.increment()
        counter.decrement()
        counter.decrement()
        counter.decrement()
        self.assertEqual(counter.get(), 0)

    def test_lazy_resource_provider_builds_once(self) -> None:
        calls: list[str] = []

        class Resource:
            pass

        provider = LazyResourceProvider(
            lambda: calls.append("build") or Resource(),
            Resource,
        )
        self.assertIs(provider(), provider())
        self.assertEqual(calls, ["build"])

    def test_async_worker_starter_reuses_live_task_and_restarts_done_task(self) -> None:
        events: list[str] = []

        class Task:
            def __init__(self) -> None:
                self.finished = False

            def done(self) -> bool:
                return self.finished

        tasks: list[Task] = []

        def create_task(_awaitable: object) -> Task:
            task = Task()
            tasks.append(task)
            return task

        starter = AsyncWorkerStarter(
            before_start=lambda: events.append("before"),
            worker=lambda: object(),  # type: ignore[arg-type,return-value]
            create_task=create_task,  # type: ignore[arg-type]
        )
        starter.ensure_started()
        starter.ensure_started()
        tasks[0].finished = True
        starter.ensure_started()

        self.assertEqual(len(tasks), 2)
        self.assertEqual(events, ["before", "before", "before"])

    def test_main_binds_voice_worker_starter(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("def ensure_voice_worker_started(", source)
        self.assertIn(
            "ensure_voice_worker_started = voice_worker_starter.ensure_started",
            source,
        )

    def test_main_uses_state_objects_instead_of_global_setter_functions(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        for name in (
            "record_search_followup_queued",
            "_set_tts_warmup_started",
            "_set_control_page_runtime_services_refresh_task",
            "_set_control_page_runtime_services_lock",
            "_set_control_page_minecraft_snapshot_refresh_task",
            "_set_control_page_minecraft_snapshot_lock",
            "_set_control_page_minecraft_snapshot_poll_task",
            "_set_control_page_runner",
            "_set_control_page_site",
            "_set_control_page_start_lock",
            "increment_inflight_llm_requests",
            "decrement_inflight_llm_requests",
        ):
            self.assertNotIn(f"def {name}(", source)


if __name__ == "__main__":
    unittest.main()
