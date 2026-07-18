from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.cached_tts_runtime import (  # noqa: E402
    CachedTtsRuntimeDeps,
    cached_audio_path_for_answer_from_runtime,
    play_cached_answer_audio_from_runtime,
)


class FakePlaybackManager:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def play_source_once(self, request: Any) -> bool:
        self.requests.append(request)
        return True


class CachedTtsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        *,
        resolved_path: Path | None,
        events: list[tuple[str, dict[str, Any]]] | None = None,
        latency: list[tuple[dict | None, str, str]] | None = None,
        playback_manager: FakePlaybackManager | None = None,
    ) -> CachedTtsRuntimeDeps:
        events = events if events is not None else []
        latency = latency if latency is not None else []
        playback_manager = playback_manager if playback_manager is not None else FakePlaybackManager()

        def resolve_path(answer: str, **kwargs: Any) -> Path | None:
            self.assertEqual(answer, "안녕")
            self.assertTrue(kwargs["enabled"])
            self.assertEqual(kwargs["canned_text"], "안녕")
            self.assertEqual(kwargs["canned_audio_path"], "cached.wav")
            return resolved_path

        return CachedTtsRuntimeDeps(
            resolve_cached_tts_audio_path=resolve_path,
            cached_audio_enabled=True,
            canned_wake_reply_text="안녕",
            canned_wake_reply_audio="cached.wav",
            project_root=Path("C:/Evelyn"),
            cached_wave_audio_source_factory=lambda path, **kwargs: SimpleNamespace(path=path, **kwargs),
            tts_source_playback_request_factory=lambda *args, **kwargs: {"args": args, "kwargs": kwargs},
            tts_playback_manager=playback_manager,
            clean_text=lambda value: f"clean:{value}",
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            log_voice_latency=lambda metrics, key, label: latency.append((metrics, key, label)),
        )

    def test_cached_audio_path_delegates_to_resolver(self) -> None:
        path = Path("C:/Evelyn/cached.wav")

        self.assertEqual(
            cached_audio_path_for_answer_from_runtime("안녕", deps=self.build_deps(resolved_path=path)),
            path,
        )

    async def test_play_cached_answer_audio_returns_false_when_no_cache_path(self) -> None:
        playback = FakePlaybackManager()

        self.assertFalse(
            await play_cached_answer_audio_from_runtime(
                SimpleNamespace(guild=SimpleNamespace(id=7)),
                "안녕",
                deps=self.build_deps(resolved_path=None, playback_manager=playback),
            )
        )
        self.assertEqual(playback.requests, [])

    async def test_play_cached_answer_audio_logs_and_plays_cached_source(self) -> None:
        events: list[tuple[str, dict[str, Any]]] = []
        latency: list[tuple[dict | None, str, str]] = []
        playback = FakePlaybackManager()
        metrics: dict[str, Any] = {}

        ok = await play_cached_answer_audio_from_runtime(
            SimpleNamespace(guild=SimpleNamespace(id=7)),
            "안녕",
            deps=self.build_deps(
                resolved_path=Path("C:/Evelyn/cached.wav"),
                events=events,
                latency=latency,
                playback_manager=playback,
            ),
            turn_id="turn-1",
            session_key="session-1",
            metrics=metrics,
        )

        self.assertTrue(ok)
        self.assertEqual(events[0][0], "cached_audio_playback_selected")
        self.assertEqual(events[0][1]["answer"], "clean:안녕")
        self.assertEqual(len(playback.requests), 1)
        request = playback.requests[0]
        source = request["args"][1]
        self.assertEqual(source.path, Path("C:/Evelyn/cached.wav"))
        self.assertEqual(request["kwargs"]["guild_id"], 7)
        self.assertTrue(request["kwargs"]["cleanup_source"])

        source.on_first_packet_sent()
        self.assertEqual(events[-1][0], "first_packet_sent")
        self.assertEqual(latency[-1], (metrics, "first_packet_sent_logged", "캐시 오디오 첫 패킷 송신 시간"))


if __name__ == "__main__":
    unittest.main()
