from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.tts_interrupt_runtime import (  # noqa: E402
    TtsInterruptRuntimeDeps,
    VoiceTtsInterruptGateDeps,
    run_voice_tts_interrupt_gate_from_runtime,
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
        self.cancelled: list[tuple[int | None, str]] = []

    def get(self, guild_id: int | None) -> dict[str, str]:
        return {
            "turn_id": "turn-source-1",
            "session_key": "session-source-1",
        }

    def source_context(self, guild_id: int | None) -> dict[str, str]:
        return {
            "source_turn_id": "turn-source-1",
            "source_session_key": "session-source-1",
            "output_mode": "discord_voice",
            "validation_session_id": "validation-1",
            "validation_step_id": "07-barge-source",
            "validation_attempt_id": "attempt-private-1",
        }

    async def cancel_guild(
        self,
        guild_id: int | None,
        *,
        reason: str = "interrupt",
    ) -> bool:
        self.cancelled.append((guild_id, reason))
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
        self.assertEqual(
            events,
            [
                (
                    "tts_interrupt",
                    {
                        "guild_id": 7,
                        "reason": "qualified_user_audio",
                        "qualified": True,
                        "source_turn_id": "turn-source-1",
                        "source_session_key": "session-source-1",
                        "output_mode": "discord_voice",
                        "validation_session_id": "validation-1",
                        "validation_step_id": "07-barge-source",
                        "validation_attempt_id": "attempt-private-1",
                    },
                )
            ],
        )

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


class FakeGateLocalPlaybackManager:
    def __init__(self, *, active: bool = False, stopped: bool = True) -> None:
        self.active = active
        self.stopped = stopped
        self.stop_reasons: list[str] = []

    def snapshot(self) -> dict[str, bool]:
        return {"active": self.active}

    async def request_stop_and_wait(self, *, reason: str) -> Any:
        self.stop_reasons.append(reason)
        if not self.stopped:
            return None
        return SimpleNamespace(
            source_turn_id="turn-source-local",
            source_session_key="session-source-local",
            output_mode="local_speaker",
            validation_session_id="validation-local",
            validation_step_id="07-local-barge-source",
            validation_attempt_id="attempt-local-1",
        )


class FakeGatePlaybackManager:
    def __init__(self, reasons: list[str | None] | None = None) -> None:
        self.reasons = list(reasons or [None])
        self.calls: list[tuple[int, float]] = []

    def input_suppression_reason(self, *, guild_id: int, post_tts_ignore_sec: float) -> str | None:
        self.calls.append((guild_id, post_tts_ignore_sec))
        if len(self.reasons) > 1:
            return self.reasons.pop(0)
        return self.reasons[0]


class VoiceTtsInterruptGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.qualified = False
        self.discord_stop_result = True
        self.verification = SimpleNamespace(
            matched=True,
            score=0.93,
            to_dict=lambda: {"matched": True, "score": 0.93},
        )
        self.local_manager = FakeGateLocalPlaybackManager()
        self.playback_manager = FakeGatePlaybackManager()
        self.deps = self.build_gate_deps()

    def build_gate_deps(self) -> VoiceTtsInterruptGateDeps:
        def should_interrupt(meta: Any) -> bool:
            self.events.append(("interrupt_meta", meta))
            return self.qualified

        async def verify(_audio: Any, **kwargs: Any) -> Any:
            self.events.append(("verify", kwargs))
            return self.verification

        async def stop_discord(guild_id: int, **kwargs: Any) -> bool:
            self.events.append(("stop_discord", (guild_id, kwargs)))
            return self.discord_stop_result

        async def sleep(seconds: float) -> None:
            self.events.append(("sleep", seconds))

        return VoiceTtsInterruptGateDeps(
            should_interrupt_tts=should_interrupt,
            local_tts_playback_manager=self.local_manager,
            tts_playback_manager=self.playback_manager,
            verify_speaker_for_tts_interrupt=verify,
            speaker_verification_allows_tts_interrupt=lambda result: result.matched is not False,
            stop_active_tts_playback=stop_discord,
            register_drop_reason=lambda _metrics, reason, **kwargs: self.events.append(("drop", (reason, kwargs))),
            log_voice_stage=lambda _metrics, label, **kwargs: self.events.append(("stage", (label, kwargs))),
            log_voice_bottleneck_summary=lambda _metrics, **kwargs: self.events.append(("bottleneck", kwargs)),
            start_voice_barge_in_continuity_probe=lambda _metrics, **kwargs: self.events.append(("continuity", kwargs)),
            log_turn_event=lambda event, **kwargs: self.events.append(("turn", (event, kwargs))),
            sleep=sleep,
            monotonic=lambda: 123.5,
            local_only_mode=True,
            post_tts_ignore_sec=0.7,
            tts_interrupt_debounce_sec=0.12,
            voice_waveform_body_rms_min=0.05,
        )

    async def run_gate(self, *, deps: VoiceTtsInterruptGateDeps | None = None):
        return await run_voice_tts_interrupt_gate_from_runtime(
            member=SimpleNamespace(id=7, display_name="정훈"),
            guild_id=11,
            session_key="voice:11:7",
            room_session_key="room:11",
            owner_user_id=7,
            active_speaker_user_id=7,
            wake_probe="이블린",
            wake_detected=True,
            voice_like_prob=0.8,
            duration_sec=1.2,
            body_rms=0.08,
            audio16k=b"audio",
            stt_sampling_rate=16000,
            metrics=getattr(self, "metrics", {"meta": {"ingress_source": "local_mic"}}),
            deps=deps or self.deps,
        )

    def drop_reasons(self) -> list[str]:
        return [payload[0] for kind, payload in self.events if kind == "drop"]

    async def test_clear_input_passes_gate_and_builds_interrupt_meta(self) -> None:
        result = await self.run_gate()

        self.assertIsNotNone(result)
        self.assertFalse(result.qualified_tts_interrupt)
        meta = next(payload for kind, payload in self.events if kind == "interrupt_meta")
        self.assertTrue(meta.active_speaker_match)
        self.assertTrue(meta.rms_ok)
        self.assertTrue(meta.voice_like)

    async def test_weak_input_during_local_tts_is_dropped(self) -> None:
        self.local_manager.active = True

        result = await self.run_gate()

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["local_tts_active_input_suppressed"])

    async def test_qualified_local_input_verifies_and_stops_playback(self) -> None:
        self.qualified = True
        self.local_manager.active = True
        self.metrics = {"meta": {"ingress_source": "local_mic"}}

        result = await self.run_gate()

        self.assertIsNotNone(result)
        self.assertTrue(result.local_tts_interrupted)
        self.assertEqual(self.local_manager.stop_reasons, ["qualified_user_audio"])
        self.assertTrue(self.metrics["meta"]["local_tts_interrupted_by_user_audio"])
        self.assertEqual(self.metrics["meta"]["tts_interrupted_at"], 123.5)
        verify_payload = next(payload for kind, payload in self.events if kind == "verify")
        self.assertEqual(verify_payload["source"], "local_mic")
        interrupt_event = next(payload for kind, payload in self.events if kind == "turn")
        self.assertEqual(interrupt_event[0], "tts_interrupt")
        self.assertIs(interrupt_event[1]["qualified"], True)
        self.assertEqual(interrupt_event[1]["source_turn_id"], "turn-source-local")
        self.assertEqual(interrupt_event[1]["source_session_key"], "session-source-local")
        self.assertEqual(interrupt_event[1]["validation_session_id"], "validation-local")
        self.assertEqual(interrupt_event[1]["validation_step_id"], "07-local-barge-source")
        self.assertEqual(interrupt_event[1]["validation_attempt_id"], "attempt-local-1")

    async def test_qualified_local_stop_failure_emits_no_interrupt_evidence(self) -> None:
        self.qualified = True
        self.local_manager.active = True
        self.local_manager.stopped = False
        self.metrics = {"meta": {"ingress_source": "local_mic"}}

        result = await self.run_gate()

        self.assertIsNotNone(result)
        self.assertFalse(result.local_tts_interrupted)
        self.assertEqual(self.local_manager.stop_reasons, ["qualified_user_audio"])
        self.assertFalse(self.metrics["meta"]["local_tts_interrupted_by_user_audio"])
        self.assertNotIn("tts_interrupted_at", self.metrics["meta"])
        self.assertFalse(any(kind == "turn" for kind, _payload in self.events))
        self.assertFalse(any(kind == "continuity" for kind, _payload in self.events))

    async def test_speaker_verification_rejection_stops_gate(self) -> None:
        self.qualified = True
        self.local_manager.active = True
        self.verification = SimpleNamespace(
            matched=False,
            score=0.2,
            to_dict=lambda: {"matched": False, "score": 0.2},
        )

        result = await self.run_gate()

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["speaker_verification_rejected"])
        self.assertEqual(self.local_manager.stop_reasons, [])

    async def test_qualified_discord_input_debounces_and_stops_playback(self) -> None:
        self.qualified = True
        self.playback_manager.reasons = ["bot_is_speaking", "bot_is_speaking"]
        self.metrics = {"meta": {"ingress_source": "discord_voice"}}

        result = await self.run_gate()

        self.assertIsNotNone(result)
        self.assertTrue(result.discord_tts_interrupted)
        self.assertTrue(self.metrics["meta"]["tts_interrupted_by_user_audio"])
        self.assertIn(("sleep", 0.12), self.events)
        self.assertTrue(any(kind == "stop_discord" for kind, _payload in self.events))

    async def test_retry_rotation_during_debounce_prevents_stale_interrupt(self) -> None:
        self.qualified = True
        self.playback_manager.reasons = ["bot_is_speaking", "bot_is_speaking"]
        self.metrics = {
            "meta": {
                "ingress_source": "discord_voice",
                "validation_session_id": "validation-1",
                "validation_step_id": "08-barge-interrupt",
                "validation_attempt_id": "attempt-1",
            }
        }

        with patch(
            "evelyn_core.tts_interrupt_runtime.validation_attempt_binding_is_current",
            side_effect=(True, True, False),
        ) as guard:
            result = await self.run_gate()

        self.assertIsNone(result)
        self.assertIn(("sleep", 0.12), self.events)
        self.assertFalse(any(kind == "stop_discord" for kind, _payload in self.events))
        self.assertEqual(guard.call_count, 3)
        for call in guard.call_args_list:
            self.assertEqual(call.kwargs["surface"], "discord")
            self.assertIs(call.kwargs["reject_unbound_when_active"], True)

    async def test_post_tts_suppression_after_debounce_stops_gate(self) -> None:
        self.qualified = True
        self.playback_manager.reasons = ["bot_is_speaking", "post_tts_ignore"]

        result = await self.run_gate()

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["post_tts_ignore"])
        self.assertFalse(any(kind == "stop_discord" for kind, _payload in self.events))

    async def test_weak_input_during_post_tts_window_is_dropped(self) -> None:
        self.playback_manager.reasons = ["post_tts_ignore"]

        result = await self.run_gate()

        self.assertIsNone(result)
        self.assertEqual(self.drop_reasons(), ["post_tts_ignore"])

    def test_main_delegates_tts_interrupt_gate_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        composition_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = composition_source.index("    async def process_member_audio_impl(")
        function_source = composition_source[start:]
        builder_source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "voice_member_pipeline_dependency_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("run_tts_interrupt_gate=run_voice_tts_interrupt_gate_from_runtime", builder_source)
        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("TtsInterruptMeta(", function_source)
        self.assertNotIn("input_suppression_reason(", function_source)
        self.assertNotIn("verify_speaker_for_tts_interrupt(", function_source)


if __name__ == "__main__":
    unittest.main()
