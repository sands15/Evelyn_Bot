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

from evelyn_core.tts_interrupt_runtime import (  # noqa: E402
    TtsInterruptRuntimeDeps,
    speaker_verification_allows_tts_interrupt_from_runtime,
    stop_active_tts_playback_from_runtime,
    verify_speaker_for_tts_interrupt_from_runtime,
)


class FakeSpeakerResult:
    def __init__(self, matched: bool | None, *, threshold: float = 0.7, detail: str = "") -> None:
        self.matched = matched
        self.threshold = threshold
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"matched": self.matched, "threshold": self.threshold, "detail": self.detail}


class FakePlaybackManager:
    def __init__(self, stopped: bool) -> None:
        self.stopped = stopped
        self.cancelled: list[int | None] = []

    async def cancel_guild(self, guild_id: int | None) -> bool:
        self.cancelled.append(guild_id)
        return self.stopped


class TtsInterruptRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def build_deps(
        self,
        *,
        stopped: bool = True,
        applies: bool = True,
        events: list[tuple[str, dict[str, Any]]] | None = None,
        verifier: Any | None = None,
    ) -> TtsInterruptRuntimeDeps:
        events = events if events is not None else []
        verifier = verifier if verifier is not None else SimpleNamespace(verify=lambda audio, **_kwargs: audio)

        async def to_thread(func, *args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return TtsInterruptRuntimeDeps(
            tts_playback_manager=FakePlaybackManager(stopped),
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            speaker_verification_applies=lambda **_kwargs: applies,
            speaker_verification_result_factory=lambda _status, **kwargs: FakeSpeakerResult(
                None,
                threshold=kwargs["threshold"],
                detail=kwargs["detail"],
            ),
            speaker_verifier=verifier,
            speaker_verification_apply_to="all",
            speaker_verification_threshold=0.7,
            to_thread=to_thread,
        )

    async def test_stop_active_tts_playback_logs_only_when_cancelled(self) -> None:
        events: list[tuple[str, dict[str, Any]]] = []

        self.assertTrue(
            await stop_active_tts_playback_from_runtime(
                7,
                deps=self.build_deps(stopped=True, events=events),
                reason="qualified_user_audio",
            )
        )
        self.assertEqual(events, [("tts_interrupt", {"guild_id": 7, "reason": "qualified_user_audio"})])

        events.clear()
        self.assertFalse(await stop_active_tts_playback_from_runtime(7, deps=self.build_deps(stopped=False, events=events)))
        self.assertEqual(events, [])

    async def test_verify_speaker_skips_when_policy_does_not_apply(self) -> None:
        metrics: dict[str, Any] = {}

        result = await verify_speaker_for_tts_interrupt_from_runtime(
            b"audio",
            deps=self.build_deps(applies=False),
            sampling_rate=16000,
            source="local_mic",
            metrics=metrics,
        )

        self.assertIsNone(result.matched)
        self.assertEqual(result.detail, "source=local_mic")
        self.assertEqual(metrics["meta"]["speaker_verification"]["detail"], "source=local_mic")

    async def test_verify_speaker_runs_verifier_when_policy_applies(self) -> None:
        verifier = SimpleNamespace(verify=lambda audio, **_kwargs: FakeSpeakerResult(True, detail=f"verified:{audio!r}"))

        result = await verify_speaker_for_tts_interrupt_from_runtime(
            b"audio",
            deps=self.build_deps(applies=True, verifier=verifier),
            sampling_rate=16000,
            source="discord_voice",
            metrics={},
        )

        self.assertTrue(result.matched)
        self.assertEqual(result.detail, "verified:b'audio'")

    def test_speaker_verification_allows_unless_explicit_false(self) -> None:
        self.assertTrue(speaker_verification_allows_tts_interrupt_from_runtime(FakeSpeakerResult(True)))
        self.assertTrue(speaker_verification_allows_tts_interrupt_from_runtime(FakeSpeakerResult(None)))
        self.assertFalse(speaker_verification_allows_tts_interrupt_from_runtime(FakeSpeakerResult(False)))


if __name__ == "__main__":
    unittest.main()
