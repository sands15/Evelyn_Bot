from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.stt_task_runtime import run_blocking_stt_task_from_runtime  # noqa: E402


class FakeAsyncLock:
    def __init__(self, *, locked: bool = False) -> None:
        self._locked = locked

    def locked(self) -> bool:
        return self._locked

    async def acquire(self) -> None:
        self._locked = True

    def release(self) -> None:
        self._locked = False


class SttTaskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_blocking_function_under_lock(self) -> None:
        lock = FakeAsyncLock()

        async def fake_wait_for(value: Any, *, timeout: float) -> Any:
            self.assertEqual(timeout, 0.5)
            return await value

        async def fake_to_thread(func: Any) -> Any:
            return func()

        result = await run_blocking_stt_task_from_runtime(
            lambda: "ok",
            stage="full",
            timeout_sec=0.1,
            get_stt_cooldown_until=lambda: 0.0,
            set_stt_cooldown_until=lambda _value: None,
            stt_cooldown_after_timeout_sec=2.0,
            monotonic=lambda: 10.0,
            get_stt_inference_lock=lambda: lock,
            increment_voice_pipeline_counter=lambda _name: None,
            record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            wait_for=fake_wait_for,
            to_thread=fake_to_thread,
        )

        self.assertEqual(result, "ok")

    async def test_rejects_while_cooling_down(self) -> None:
        counters: list[str] = []

        with self.assertRaisesRegex(TimeoutError, "stt_cooldown:wake:2.50s"):
            await run_blocking_stt_task_from_runtime(
                lambda: "unused",
                stage="wake",
                timeout_sec=1.0,
                get_stt_cooldown_until=lambda: 12.5,
                set_stt_cooldown_until=lambda _value: None,
                stt_cooldown_after_timeout_sec=2.0,
                monotonic=lambda: 10.0,
                get_stt_inference_lock=lambda: FakeAsyncLock(),
                increment_voice_pipeline_counter=lambda name: counters.append(name),
                record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(counters, ["stt_busy_drop_count"])

    async def test_rejects_when_lock_is_busy(self) -> None:
        counters: list[str] = []

        with self.assertRaisesRegex(RuntimeError, "stt_busy:full"):
            await run_blocking_stt_task_from_runtime(
                lambda: "unused",
                stage="full",
                timeout_sec=1.0,
                get_stt_cooldown_until=lambda: 0.0,
                set_stt_cooldown_until=lambda _value: None,
                stt_cooldown_after_timeout_sec=2.0,
                monotonic=lambda: 10.0,
                get_stt_inference_lock=lambda: FakeAsyncLock(locked=True),
                increment_voice_pipeline_counter=lambda name: counters.append(name),
                record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            )

        self.assertEqual(counters, ["stt_busy_drop_count"])

    async def test_timeout_sets_cooldown_records_counter_and_failure(self) -> None:
        counters: list[str] = []
        cooldowns: list[float] = []
        failures: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def fake_wait_for(_value: Any, *, timeout: float) -> Any:
            raise asyncio.TimeoutError()

        async def fake_to_thread(func: Any) -> Any:
            return func()

        with self.assertRaises(asyncio.TimeoutError):
            await run_blocking_stt_task_from_runtime(
                lambda: "unused",
                stage="full",
                timeout_sec=1.25,
                metrics={"meta": {"turn_id": "turn-1"}},
                get_stt_cooldown_until=lambda: 0.0,
                set_stt_cooldown_until=lambda value: cooldowns.append(value),
                stt_cooldown_after_timeout_sec=2.0,
                monotonic=lambda: 10.0,
                get_stt_inference_lock=lambda: FakeAsyncLock(),
                increment_voice_pipeline_counter=lambda name: counters.append(name),
                record_voice_pipeline_failure=lambda *args, **kwargs: failures.append((args, kwargs)),
                wait_for=fake_wait_for,
                to_thread=fake_to_thread,
            )

        self.assertEqual(cooldowns, [12.0])
        self.assertEqual(counters, ["stt_timeout_count"])
        self.assertEqual(failures[0][0][0], "stt_timeout")
        self.assertIn("full timed out after 1.2s", failures[0][0][1])
        self.assertEqual(failures[0][1]["stage"], "full")

    async def test_timeout_keeps_inference_busy_until_thread_exits(self) -> None:
        lock = asyncio.Lock()
        started = threading.Event()
        release = threading.Event()

        def blocked_inference() -> str:
            started.set()
            release.wait(timeout=2.0)
            return "late"

        try:
            with self.assertRaises(asyncio.TimeoutError):
                await run_blocking_stt_task_from_runtime(
                    blocked_inference,
                    stage="full",
                    timeout_sec=0.01,
                    get_stt_cooldown_until=lambda: 0.0,
                    set_stt_cooldown_until=lambda _value: None,
                    stt_cooldown_after_timeout_sec=0.0,
                    monotonic=lambda: 10.0,
                    get_stt_inference_lock=lambda: lock,
                    increment_voice_pipeline_counter=lambda _name: None,
                    record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
                )
            self.assertTrue(started.is_set())
            self.assertTrue(lock.locked())
            with self.assertRaisesRegex(RuntimeError, "stt_busy:wake"):
                await run_blocking_stt_task_from_runtime(
                    lambda: "must-not-run",
                    stage="wake",
                    timeout_sec=0.01,
                    get_stt_cooldown_until=lambda: 0.0,
                    set_stt_cooldown_until=lambda _value: None,
                    stt_cooldown_after_timeout_sec=0.0,
                    monotonic=lambda: 10.0,
                    get_stt_inference_lock=lambda: lock,
                    increment_voice_pipeline_counter=lambda _name: None,
                    record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
                )
        finally:
            release.set()
            for _ in range(100):
                if not lock.locked():
                    break
                await asyncio.sleep(0.01)

        self.assertFalse(lock.locked())

    async def test_cancel_keeps_inference_busy_until_thread_exits(self) -> None:
        lock = asyncio.Lock()
        started = threading.Event()
        release = threading.Event()

        def blocked_inference() -> str:
            started.set()
            release.wait(timeout=2.0)
            return "late"

        task = asyncio.create_task(
            run_blocking_stt_task_from_runtime(
                blocked_inference,
                stage="full",
                timeout_sec=5.0,
                get_stt_cooldown_until=lambda: 0.0,
                set_stt_cooldown_until=lambda _value: None,
                stt_cooldown_after_timeout_sec=0.0,
                monotonic=lambda: 10.0,
                get_stt_inference_lock=lambda: lock,
                increment_voice_pipeline_counter=lambda _name: None,
                record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            )
        )
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1.0))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(lock.locked())
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for _ in range(100):
                if not lock.locked():
                    break
                await asyncio.sleep(0.01)

        self.assertFalse(lock.locked())


if __name__ == "__main__":
    unittest.main()
