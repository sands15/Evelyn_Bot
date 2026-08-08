from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_tts_stream_runtime import (  # noqa: E402
    DiscordTtsSingleRuntimeDeps,
    speak_answer_from_runtime,
)
import evelyn_core.discord_tts_stream_runtime as discord_tts_runtime  # noqa: E402
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalIntegrityError,
)


class FakeLock:
    def __init__(self) -> None:
        self.entered = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeScope:
    def __init__(self) -> None:
        self.transitions: list[tuple[object, str]] = []

    def transition(self, state, *, reason: str) -> None:
        self.transitions.append((state, reason))


class FakePlaybackManager:
    def __init__(self) -> None:
        self.request = None

    async def play_source_once(self, request) -> None:
        self.request = request


class DiscordTtsSingleRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.local_calls: list[tuple[tuple, dict]] = []
        self.cached_calls: list[tuple[tuple, dict]] = []
        self.source_calls: list[tuple[str, dict]] = []
        self.events: list[tuple[str, dict]] = []
        self.latencies: list[tuple] = []
        self.lock = FakeLock()
        self.manager = FakePlaybackManager()
        self.local = False
        self.local_played = True
        self.cached = False

    async def speak_local(self, *args, **kwargs) -> bool:
        self.local_calls.append((args, kwargs))
        return self.local_played

    async def play_cached(self, *args, **kwargs) -> bool:
        self.cached_calls.append((args, kwargs))
        return self.cached

    async def create_source(self, answer: str, **kwargs):
        self.source_calls.append((answer, kwargs))
        return SimpleNamespace(answer=answer)

    def build_deps(self) -> DiscordTtsSingleRuntimeDeps:
        return DiscordTtsSingleRuntimeDeps(
            memory_index_dir=Path("unused-memory-index"),
            is_local_speaker_voice_client=lambda _vc: self.local,
            speak_answer_local=self.speak_local,
            tts_running_state="tts-running",
            play_cached_answer_audio=self.play_cached,
            tts_lock=self.lock,
            create_omnivoice_source=self.create_source,
            log_turn_event=lambda event, **payload: self.events.append((event, payload)),
            log_voice_latency=lambda *args: self.latencies.append(args),
            playback_manager=self.manager,
            source_playback_request_factory=lambda vc, source, **kwargs: SimpleNamespace(
                vc=vc,
                source=source,
                **kwargs,
            ),
        )

    async def test_local_speaker_delegates_without_cache_or_discord_playback(self) -> None:
        self.local = True
        metrics = {"marks": {}}

        await speak_answer_from_runtime(
            object(),
            "안녕",
            deps=self.build_deps(),
            turn_id="turn-1",
            session_key="session-1",
            metrics=metrics,
        )

        self.assertEqual(self.local_calls[0][0], ("안녕",))
        self.assertEqual(self.local_calls[0][1]["turn_id"], "turn-1")
        self.assertEqual(self.cached_calls, [])
        self.assertIsNone(self.manager.request)
        self.assertIs(metrics["meta"]["playback_completed"], True)

    async def test_local_speaker_projects_failed_playback(self) -> None:
        self.local = True
        self.local_played = False
        metrics = {"meta": {}}

        await speak_answer_from_runtime(
            object(),
            "안녕",
            deps=self.build_deps(),
            metrics=metrics,
        )

        self.assertIs(metrics["meta"]["playback_completed"], False)

    async def test_local_speaker_projects_qualified_interrupt_as_incomplete(self) -> None:
        self.local = True
        metrics = {"meta": {"qualified_tts_interrupt": True}}

        await speak_answer_from_runtime(
            object(),
            "안녕",
            deps=self.build_deps(),
            metrics=metrics,
        )

        self.assertIs(metrics["meta"]["playback_completed"], False)

    async def test_local_speaker_blank_reply_preserves_empty_answer_classification(self) -> None:
        self.local = True
        self.local_played = False
        metrics = {"meta": {}}

        await speak_answer_from_runtime(
            object(),
            "   ",
            deps=self.build_deps(),
            metrics=metrics,
        )

        self.assertNotIn("playback_completed", metrics["meta"])

    async def test_cached_audio_short_circuits_source_creation(self) -> None:
        self.cached = True
        scope = FakeScope()

        await speak_answer_from_runtime(
            SimpleNamespace(guild=SimpleNamespace(id=77)),
            "캐시",
            deps=self.build_deps(),
            turn_scope=scope,
        )

        self.assertEqual(scope.transitions, [("tts-running", "speak_answer")])
        self.assertEqual(len(self.cached_calls), 1)
        self.assertEqual(self.source_calls, [])
        self.assertEqual(self.lock.entered, 0)

    async def test_builds_source_and_single_playback_request(self) -> None:
        scope = FakeScope()
        metrics = {"marks": {}}
        vc = SimpleNamespace(guild=SimpleNamespace(id=88))

        await speak_answer_from_runtime(
            vc,
            "새 음성",
            deps=self.build_deps(),
            turn_id="turn-2",
            session_key="session-2",
            turn_scope=scope,
            metrics=metrics,
        )

        request = self.manager.request
        self.assertEqual(request.guild_id, 88)
        self.assertEqual(request.turn_id, "turn-2")
        self.assertFalse(request.clear_registry_on_finish)
        self.assertEqual(self.source_calls[0][0], "새 음성")
        callback = self.source_calls[0][1]["on_first_packet_sent"]
        callback()
        self.assertEqual(self.events, [(
            "first_packet_sent",
            {"turn_id": "turn-2", "chunk_index": 1, "session_key": "session-2"},
        )])
        self.assertEqual(self.latencies[0][1:], ("first_packet_sent_logged", "첫 패킷 송신 시간"))

    async def test_stale_memory_boundary_blocks_before_tts(self) -> None:
        with patch.object(
            discord_tts_runtime,
            "current_memory_exposure_position",
            return_value=object(),
        ), patch.object(
            discord_tts_runtime,
            "memory_exposure_guard",
            side_effect=MemoryDeletionJournalIntegrityError(),
        ):
            with self.assertRaises(MemoryDeletionJournalIntegrityError):
                await speak_answer_from_runtime(
                    SimpleNamespace(guild=SimpleNamespace(id=88)),
                    "stale",
                    deps=self.build_deps(),
                )
        self.assertEqual(self.cached_calls, [])
        self.assertEqual(self.source_calls, [])
        self.assertIsNone(self.manager.request)

    def test_main_delegates_single_discord_tts_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def speak_answer(")
        end = source.index("    async def stream_tts_sentences(", start)
        function_source = source[start:end]

        self.assertIn("speak_answer_from_runtime(", function_source)
        self.assertNotIn("create_omnivoice_source(", function_source)
        self.assertNotIn("play_source_once(", function_source)


if __name__ == "__main__":
    unittest.main()
