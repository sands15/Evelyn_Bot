import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_writebehind import (  # noqa: E402
    append_memory_writebehind_event,
    mark_memory_writer_status,
    memory_writebehind_task_key,
    run_memory_writebehind_steps,
    should_replace_existing_memory_task,
)


class MemoryWriteBehindTests(unittest.IsolatedAsyncioTestCase):
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

        async def failing_step() -> None:
            raise RuntimeError("write failed")

        await run_memory_writebehind_steps(payload, [failing_step])

        self.assertEqual(payload["writebehind_status"], "failed")
        self.assertIn("write failed", payload["writebehind_error"])

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
