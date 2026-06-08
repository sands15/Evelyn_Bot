import asyncio
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "turn_replay"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_writebehind import run_memory_writebehind_steps  # noqa: E402
from evelyn_core.tts_playback import TtsPlaybackManager  # noqa: E402
from evelyn_core.turn_lifecycle import TurnScope  # noqa: E402


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class TurnReplayGoldenTests(unittest.IsolatedAsyncioTestCase):
    async def test_barge_in_cancel_fixture(self) -> None:
        fixture = load_fixture("barge_in_cancel.json")
        old_scope = TurnScope(fixture["active_turn_id"])
        new_scope = TurnScope(fixture["incoming_turn_id"])

        old_scope.cancel(reason=fixture["cancel_reason"])

        self.assertEqual(new_scope.turn_id, fixture["incoming_turn_id"])
        self.assertEqual(old_scope.state.value, fixture["expected_old_state"])
        self.assertEqual(old_scope.cancel_reason, fixture["cancel_reason"])

    async def test_late_llm_chunk_fixture(self) -> None:
        fixture = load_fixture("late_llm_chunk.json")
        scope = TurnScope(fixture["scope_turn_id"])

        is_stale = scope.is_stale(fixture["current_turn_id"])

        self.assertEqual(is_stale, fixture["expected_stale"])

    async def test_tts_cancel_fixture(self) -> None:
        class FakeVc:
            def __init__(self) -> None:
                self.stopped = False

            def is_playing(self) -> bool:
                return True

            def is_paused(self) -> bool:
                return False

            def stop(self) -> None:
                self.stopped = True

        class FakeSource:
            def __init__(self) -> None:
                self.finished = False

            def finish(self) -> None:
                self.finished = True

        fixture = load_fixture("tts_cancel.json")
        manager = TtsPlaybackManager()
        vc = FakeVc()
        source = FakeSource()
        manager.start(
            guild_id=fixture["guild_id"],
            vc=vc,
            playback_source=source,
            turn_id=fixture["turn_id"],
        )

        cancelled = await manager.cancel_turn(fixture["turn_id"], now=123.0)

        self.assertEqual(cancelled, fixture["expected_cancelled"])
        self.assertTrue(vc.stopped)
        self.assertTrue(source.finished)
        self.assertFalse(manager.is_active(fixture["guild_id"]))

    async def test_memory_writebehind_failure_fixture(self) -> None:
        fixture = load_fixture("memory_writebehind_failure.json")
        payload = {"source": fixture["source"], "session_key": fixture["session_key"]}

        async def failing_step() -> None:
            await asyncio.sleep(0)
            raise RuntimeError(fixture["error"])

        await run_memory_writebehind_steps(payload, [failing_step])

        self.assertEqual(payload["writebehind_status"], fixture["expected_status"])
        self.assertIn(fixture["error"], payload["writebehind_error"])


if __name__ == "__main__":
    unittest.main()
