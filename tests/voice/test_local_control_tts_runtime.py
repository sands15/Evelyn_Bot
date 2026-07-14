from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_control_tts_runtime import (  # noqa: E402
    LocalControlTtsRuntimeDeps,
    schedule_local_control_tts_from_runtime,
)


class LocalControlTtsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_local_control_tts_creates_task_and_logs_summary(self) -> None:
        calls: list[tuple[str, Any]] = []

        async def speak_answer_local(answer: str, **kwargs: Any) -> bool:
            calls.append(("speak", (answer, kwargs["turn_id"], kwargs["session_key"], kwargs["metrics"]["started_at"])))
            return True

        deps = LocalControlTtsRuntimeDeps(
            local_only_mode=True,
            local_tts_enabled=lambda: True,
            speak_answer_local=speak_answer_local,
            create_turn_scoped_task=lambda coro, **kwargs: asyncio.create_task(coro, name=str(kwargs.get("turn_scope"))),
            log_voice_bottleneck_summary=lambda metrics, **kwargs: calls.append(
                ("summary", (metrics["meta"]["turn_type"], kwargs["extra"], kwargs["event_name"]))
            ),
            monotonic=lambda: 123.0,
        )

        task = schedule_local_control_tts_from_runtime(
            "hello",
            turn_id="turn-1",
            session_key="session-1",
            turn_scope="scope-1",
            deps=deps,
        )

        self.assertIsNotNone(task)
        await task
        self.assertEqual(calls[0], ("speak", ("hello", "turn-1", "session-1", 123.0)))
        self.assertEqual(calls[1], ("summary", ("control_page_local_tts", "control_page=true playback=ok", "local_tts_summary")))

    async def test_schedule_local_control_tts_returns_none_when_disabled(self) -> None:
        deps = LocalControlTtsRuntimeDeps(
            local_only_mode=False,
            local_tts_enabled=lambda: True,
            speak_answer_local=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            create_turn_scoped_task=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            log_voice_bottleneck_summary=lambda *_args, **_kwargs: None,
        )

        self.assertIsNone(schedule_local_control_tts_from_runtime("hello", deps=deps))


if __name__ == "__main__":
    unittest.main()
