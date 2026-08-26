from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core.delivery_entry_composition import (  # noqa: E402
    _run_delivery_under_memory_exposure,
)
from evelyn_core.memory_exposure import (  # noqa: E402
    MemoryExposurePosition,
    capture_memory_exposure_position,
    current_memory_exposure_position,
    memory_exposure_guard,
    reset_memory_exposure_position,
)
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)
from evelyn_core.tts_playback import (  # noqa: E402
    LazyStreamingVoiceDelivery,
    TTSQueueSink,
)
from evelyn_core.turn_lifecycle import TurnScope, TurnScopeRegistry  # noqa: E402


class StreamingTtsMemoryHandoffTests(
    unittest.IsolatedAsyncioTestCase
):
    def tearDown(self) -> None:
        reset_memory_exposure_position()

    @contextmanager
    def unconfigured_authenticity(self):
        with patch.dict(
            os.environ,
            {
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        ):
            yield

    async def test_playback_lease_starts_only_after_producer_handoff(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index_dir = Path(temporary) / "memory_index"
            with self.unconfigured_authenticity():
                position = MemoryExposurePosition(
                    deletion_position=(
                        journal.memory_deletion_journal_position(
                            index_dir
                        )
                    ),
                    memory_version=0,
                    supplied_note_ids=(
                        "concept-0123456789abcdef",
                    ),
                )
                capture_memory_exposure_position(position)
                sentence_queue: asyncio.Queue[str | None] = (
                    asyncio.Queue()
                )
                received: list[str] = []

                async def playback() -> None:
                    while True:
                        item = await sentence_queue.get()
                        if item is None:
                            return
                        received.append(item)

                delivery = LazyStreamingVoiceDelivery(
                    sentence_queue,
                    TTSQueueSink(sentence_queue),
                    lambda: asyncio.create_task(
                        _run_delivery_under_memory_exposure(
                            playback(),
                            position=position,
                            memory_index_dir=index_dir,
                        )
                    ),
                    metrics={},
                    eager_start_allowed=lambda: (
                        current_memory_exposure_position() is None
                    ),
                )

                with memory_exposure_guard(
                    expected_position=position,
                    required=True,
                    index_dir=index_dir,
                ):
                    await delivery.on_chunk("안전한 문장")
                    await asyncio.sleep(0)
                    self.assertIsNone(delivery.playback_task)

                await delivery.close("안전한 문장")
                self.assertIsNotNone(delivery.playback_task)
                self.assertEqual(await delivery.finalize(), 1)
                self.assertEqual(received, ["안전한 문장"])

    async def test_non_memory_playback_starts_before_close_and_aborts_exact_task(
        self,
    ) -> None:
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        received: list[str] = []
        cancelled = asyncio.Event()
        starts = 0

        async def playback() -> None:
            try:
                while True:
                    item = await sentence_queue.get()
                    if item is None:
                        return
                    received.append(item)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        def start_playback() -> asyncio.Task:
            nonlocal starts
            starts += 1
            return asyncio.create_task(playback())

        delivery = LazyStreamingVoiceDelivery(
            sentence_queue,
            TTSQueueSink(sentence_queue),
            start_playback,
            metrics={},
            eager_start_allowed=lambda: True,
        )

        await delivery.on_chunk("첫 문장")
        await delivery.on_chunk("둘째 문장")
        await asyncio.sleep(0)

        self.assertIsNotNone(delivery.playback_task)
        self.assertEqual(received, ["첫 문장", "둘째 문장"])
        self.assertEqual(starts, 1)

        task = delivery.playback_task
        await delivery.abort()

        self.assertIs(delivery.playback_task, task)
        self.assertTrue(task.done())
        self.assertTrue(cancelled.is_set())

    async def test_replaced_turn_cancels_eager_task_before_pending_synthesis(
        self,
    ) -> None:
        registry = TurnScopeRegistry()
        stale_scope = TurnScope("stale-turn")
        registry.replace_room_scope("room", stale_scope)
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        synthesized: list[str] = []

        async def playback() -> None:
            started.set()
            try:
                await release.wait()
                item = await sentence_queue.get()
                if item is not None:
                    synthesized.append(item)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        delivery = LazyStreamingVoiceDelivery(
            sentence_queue,
            TTSQueueSink(sentence_queue),
            lambda: registry.create_scoped_task(
                playback(),
                turn_scope=stale_scope,
            ),
            metrics={},
            eager_start_allowed=lambda: True,
        )
        await delivery.on_chunk("stale pending sentence")
        await asyncio.wait_for(started.wait(), timeout=1)

        registry.replace_room_scope("room", TurnScope("current-turn"))
        await asyncio.wait_for(cancelled.wait(), timeout=1)

        self.assertTrue(delivery.playback_task.cancelled())
        self.assertEqual(synthesized, [])


if __name__ == "__main__":
    unittest.main()
