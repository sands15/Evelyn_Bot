import asyncio
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_writebehind import (  # noqa: E402
    BACKGROUND_MEMORY_TASKS,
    append_memory_writebehind_event,
    mark_memory_writer_status,
    memory_writebehind_task_key,
    run_registered_memory_thread,
    run_memory_writebehind_steps,
    should_replace_existing_memory_task,
)
from evelyn_core.guild_runtime_reset import (  # noqa: E402
    MEMORY_BACKGROUND_WORK_INFLIGHT,
    require_guild_runtime_reset_ready,
)


class MemoryWriteBehindTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_explicit_worker_blocks_reset_until_thread_finishes(
        self,
    ) -> None:
        started = threading.Event()
        release = threading.Event()

        def blocking_write() -> None:
            started.set()
            if not release.wait(2.0):
                raise TimeoutError("worker was not released")

        task = asyncio.create_task(
            run_registered_memory_thread(
                7,
                blocking_write,
                task_kind="explicit-confirmation",
            )
        )
        self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
        task.cancel()
        await asyncio.sleep(0)
        deps = SimpleNamespace(
            autonomy_engines={},
            autonomy_cognitive_refresh_tasks={},
            background_search_tasks={},
            background_memory_tasks=BACKGROUND_MEMORY_TASKS,
            background_memory_vault_tasks={},
        )

        cancelled = False
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                f"^{MEMORY_BACKGROUND_WORK_INFLIGHT}$",
            ):
                require_guild_runtime_reset_ready(7, deps=deps)
        finally:
            release.set()
            try:
                await task
            except asyncio.CancelledError:
                cancelled = True
        self.assertTrue(cancelled)
        self.assertEqual(BACKGROUND_MEMORY_TASKS, {})
        require_guild_runtime_reset_ready(7, deps=deps)

    def test_main_reset_uses_the_registered_memory_task_map(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertIn(
            "background_memory_tasks = BACKGROUND_MEMORY_TASKS",
            source,
        )

    async def test_run_steps_marks_completed(self) -> None:
        payload: dict = {}
        calls: list[str] = []

        async def first_step() -> None:
            calls.append("first")

        def second_step() -> None:
            calls.append("second")

        await run_memory_writebehind_steps(payload, [first_step, second_step])

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(payload["writebehind_status"], "completed")
        self.assertEqual(payload["writebehind_queued"], False)

    async def test_run_steps_writes_status_events(self) -> None:
        payload: dict = {"source": "voice", "session_key": "session-1"}
        with TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "writebehind.jsonl"

            async def step() -> None:
                return None

            await run_memory_writebehind_steps(payload, [step], event_path=event_path)
            rows = event_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(rows), 2)
        self.assertIn('"status": "running"', rows[0])
        self.assertIn('"status": "completed"', rows[1])
        self.assertIn('"session_key": "session-1"', rows[1])

    async def test_run_steps_marks_failure_without_raising(self) -> None:
        payload: dict = {}
        logs: list[str] = []
        private_error = "PRIVATE_MEMORY_WRITEBEHIND_CANARY"

        async def failing_step() -> None:
            raise RuntimeError(private_error)

        with TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "writebehind.jsonl"
            await run_memory_writebehind_steps(
                payload,
                [failing_step],
                event_path=event_path,
                log=logs.append,
            )
            persisted = event_path.read_text(encoding="utf-8")

        self.assertEqual(payload["writebehind_status"], "failed")
        self.assertEqual(
            payload["writebehind_error"],
            "memory_writebehind_failed",
        )
        self.assertEqual(payload["writebehind_error_type"], "RuntimeError")
        self.assertIn('"writebehind_error": "memory_writebehind_failed"', persisted)
        self.assertIn('"writebehind_error_type": "RuntimeError"', persisted)
        self.assertIn("errorType=RuntimeError", " ".join(logs))
        self.assertNotIn(private_error, repr(payload))
        self.assertNotIn(private_error, persisted)
        self.assertNotIn(private_error, " ".join(logs))

    def test_event_log_failure_is_content_free(self) -> None:
        payload: dict = {}
        logs: list[str] = []
        private_error = "PRIVATE_EVENT_LOG_CANARY"

        with patch(
            "evelyn_core.memory_writebehind.append_memory_writebehind_event",
            side_effect=OSError(private_error),
        ):
            mark_memory_writer_status(
                payload,
                "queued",
                event_path=Path("unused.jsonl"),
                log=logs.append,
            )

        self.assertEqual(
            payload["writebehind_event_error"],
            "memory_writebehind_event_log_failed",
        )
        self.assertEqual(
            payload["writebehind_event_error_type"],
            "OSError",
        )
        self.assertIn("errorType=OSError", " ".join(logs))
        self.assertNotIn(private_error, repr(payload))
        self.assertNotIn(private_error, " ".join(logs))

    async def test_cancelled_step_reraises_and_marks_cancelled(self) -> None:
        payload: dict = {}

        async def cancelled_step() -> None:
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await run_memory_writebehind_steps(payload, [cancelled_step])

        self.assertEqual(payload["writebehind_status"], "cancelled")

    def test_explicit_memory_uses_unique_key_and_does_not_replace_existing(self) -> None:
        decision = {"store_long_term_memory": True}

        self.assertEqual(should_replace_existing_memory_task(decision), False)
        self.assertEqual(memory_writebehind_task_key("session-1", decision, nonce=7), "session-1:explicit:7")

    def test_incidental_memory_reuses_key_and_can_replace_existing(self) -> None:
        decision = {"store_long_term_memory": False}

        self.assertEqual(should_replace_existing_memory_task(decision), True)
        self.assertEqual(memory_writebehind_task_key("session-1", decision, nonce=7), "session-1")

    def test_status_helper_records_queue_state(self) -> None:
        payload: dict = {}

        mark_memory_writer_status(payload, "queued", writebehind_mode="batch")

        self.assertEqual(payload["writebehind_status"], "queued")
        self.assertEqual(payload["writebehind_queued"], True)
        self.assertEqual(payload["writebehind_mode"], "batch")

    def test_append_event_tolerates_non_json_values(self) -> None:
        payload = {"source": "text", "session_key": object(), "store_long_term_memory": True}
        with TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "writebehind.jsonl"

            append_memory_writebehind_event(event_path, payload, "queued")
            line = event_path.read_text(encoding="utf-8")

        self.assertIn('"status": "queued"', line)
        self.assertIn('"store_long_term_memory": true', line)


if __name__ == "__main__":
    unittest.main()
