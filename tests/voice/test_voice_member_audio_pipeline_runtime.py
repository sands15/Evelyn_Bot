from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_member_audio_pipeline_runtime import (
    VoiceMemberAudioPipelineDeps,
    process_member_audio_pipeline_from_runtime,
)


class VoiceMemberAudioPipelineRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.metrics: dict[str, Any] = {"meta": {}}
        self.guild = SimpleNamespace(id=11, voice_client="voice-client")
        self.ingress = SimpleNamespace(
            guild=self.guild,
            guild_id=11,
            speaker_name="정훈",
            owner_user_id=7,
            metrics=self.metrics,
            audio16k=SimpleNamespace(size=32),
            audio_for_wake=SimpleNamespace(size=16),
            stt_sampling_rate=16000,
            wake_sampling_rate=16000,
            raw_seconds=1.5,
            duration_sec=1.2,
            voice_segment=SimpleNamespace(duration_sec=1.2),
            voiced_ms=820.0,
            body_rms=0.08,
            voice_like_prob=0.9,
        )
        self.wake = SimpleNamespace(
            owner_followup_active=False,
            active_speaker_user_id=7,
            wake_probe="이블린",
            wake_confirm="이블린",
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="이블린",
            wake_reject_reason=None,
        )
        self.stt = SimpleNamespace(
            text="오늘 날씨 알려줘",
            stt_meta={"model": "fake"},
            partial_text="오늘 날씨",
        )
        self.finalization = SimpleNamespace(
            text="오늘 날씨 알려줘",
            transcript_result=SimpleNamespace(final_text="오늘 날씨 알려줘"),
        )
        self.session_gate = SimpleNamespace(wake_alias="이블린")
        self.deps = self.build_deps()

    def build_deps(self) -> VoiceMemberAudioPipelineDeps:
        def prepare(*args: Any, **kwargs: Any) -> Any:
            self.events.append(("ingress", (args, kwargs)))
            return self.ingress

        async def wake(**kwargs: Any) -> Any:
            self.events.append(("wake", kwargs))
            return self.wake

        async def interrupt(**kwargs: Any) -> Any:
            self.events.append(("interrupt", kwargs))
            return SimpleNamespace(qualified_tts_interrupt=True)

        async def stt(**kwargs: Any) -> Any:
            self.events.append(("stt", kwargs))
            return self.stt

        def finalize(**kwargs: Any) -> Any:
            self.events.append(("finalize", kwargs))
            return self.finalization

        def session(**kwargs: Any) -> Any:
            self.events.append(("session", kwargs))
            return self.session_gate

        async def dispatch(**kwargs: Any) -> None:
            self.events.append(("dispatch", kwargs))

        return VoiceMemberAudioPipelineDeps(
            prepare_audio_ingress=prepare,
            build_audio_ingress_deps=lambda: "ingress-deps",
            run_wake_probe=wake,
            build_wake_probe_deps=lambda: "wake-deps",
            run_tts_interrupt_gate=interrupt,
            build_tts_interrupt_gate_deps=lambda: "interrupt-deps",
            run_stt_execution=stt,
            build_stt_execution_deps=lambda: "stt-deps",
            finalize_transcript=finalize,
            build_transcript_finalize_deps=lambda: "finalize-deps",
            run_session_gate=session,
            build_session_gate_deps=lambda: "session-deps",
            dispatch_voice_reply=dispatch,
            build_transcript_reply_deps=lambda guild: ("reply-deps", guild),
            build_reply_dispatch_deps=lambda: "dispatch-deps",
        )

    async def run_pipeline(
        self,
        *,
        deps: VoiceMemberAudioPipelineDeps | None = None,
        debug_meta: dict[str, Any] | None = None,
        member: Any | None = None,
        voice_listener_binding: Any = None,
    ) -> None:
        await process_member_audio_pipeline_from_runtime(
            member or SimpleNamespace(id=7, display_name="정훈"),
            b"pcm",
            debug_meta or {"source": "local_mic"},
            session_key="voice:11:7",
            room_session_key="room:11",
            room_key="room-memory",
            person_key="person-memory",
            session_memory_key="session-memory",
            turn_id="turn-1",
            segment_id=3,
            ingress_during_reply=True,
            owner_user_id_on_ingress=7,
            voice_listener_binding=voice_listener_binding,
            deps=deps or self.deps,
        )

    def stage_names(self) -> list[str]:
        return [name for name, _payload in self.events]

    async def test_happy_path_runs_all_stages_in_order(self) -> None:
        await self.run_pipeline()

        self.assertEqual(
            self.stage_names(),
            ["ingress", "wake", "interrupt", "stt", "finalize", "session", "dispatch"],
        )

    async def test_ingress_none_stops_pipeline(self) -> None:
        await self.run_pipeline(deps=replace(self.deps, prepare_audio_ingress=lambda *_args, **_kwargs: None))

        self.assertEqual(self.stage_names(), [])

    async def test_wake_none_stops_before_interrupt(self) -> None:
        async def no_wake(**kwargs: Any) -> None:
            self.events.append(("wake", kwargs))
            return None

        await self.run_pipeline(deps=replace(self.deps, run_wake_probe=no_wake))

        self.assertEqual(self.stage_names(), ["ingress", "wake"])

    async def test_retry_rotation_during_wake_stops_before_interrupt_side_effect(self) -> None:
        validation_meta = {
            "source": "discord_voice",
            "validation_session_id": "validation-1",
            "validation_step_id": "01-wake",
            "validation_attempt_id": "attempt-1",
        }
        with patch(
            "evelyn_core.voice_member_audio_pipeline_runtime.validation_attempt_binding_is_current",
            side_effect=(True, True, False),
        ) as guard:
            await self.run_pipeline(debug_meta=validation_meta)

        self.assertEqual(self.stage_names(), ["ingress", "wake"])
        self.assertEqual(guard.call_count, 3)
        for call in guard.call_args_list:
            self.assertEqual(call.kwargs["surface"], "discord")
            self.assertIs(call.kwargs["reject_unbound_when_active"], True)

    async def test_interrupt_none_stops_before_stt(self) -> None:
        async def rejected(**kwargs: Any) -> None:
            self.events.append(("interrupt", kwargs))
            return None

        await self.run_pipeline(deps=replace(self.deps, run_tts_interrupt_gate=rejected))

        self.assertEqual(self.stage_names(), ["ingress", "wake", "interrupt"])

    async def test_stt_none_stops_before_finalization(self) -> None:
        async def no_stt(**kwargs: Any) -> None:
            self.events.append(("stt", kwargs))
            return None

        await self.run_pipeline(deps=replace(self.deps, run_stt_execution=no_stt))

        self.assertEqual(self.stage_names(), ["ingress", "wake", "interrupt", "stt"])

    async def test_channel_move_during_stt_stops_before_reply_dispatch(self) -> None:
        source_client = SimpleNamespace(
            _listener_generation=8,
            channel=SimpleNamespace(id=22),
        )
        member = SimpleNamespace(
            id=7,
            display_name="정훈",
            guild=SimpleNamespace(id=11, voice_client=source_client),
        )

        async def move_during_stt(**kwargs: Any) -> Any:
            self.events.append(("stt", kwargs))
            source_client._listener_generation = 9
            source_client.channel = SimpleNamespace(id=23)
            return self.stt

        await self.run_pipeline(
            deps=replace(self.deps, run_stt_execution=move_during_stt),
            member=member,
            voice_listener_binding=(source_client, 8, 22),
        )

        self.assertEqual(
            self.stage_names(),
            ["ingress", "wake", "interrupt", "stt"],
        )

    async def test_session_none_stops_before_dispatch(self) -> None:
        def rejected(**kwargs: Any) -> None:
            self.events.append(("session", kwargs))
            return None

        await self.run_pipeline(deps=replace(self.deps, run_session_gate=rejected))

        self.assertEqual(
            self.stage_names(),
            ["ingress", "wake", "interrupt", "stt", "finalize", "session"],
        )

    async def test_wake_and_stt_results_are_forwarded_to_later_stages(self) -> None:
        await self.run_pipeline()

        interrupt = next(payload for name, payload in self.events if name == "interrupt")
        stt = next(payload for name, payload in self.events if name == "stt")
        finalize = next(payload for name, payload in self.events if name == "finalize")
        session = next(payload for name, payload in self.events if name == "session")
        dispatch = next(payload for name, payload in self.events if name == "dispatch")
        self.assertEqual(interrupt["active_speaker_user_id"], 7)
        self.assertEqual(stt["wake_probe"], "이블린")
        self.assertEqual(finalize["wake_match_mode"], "exact")
        self.assertIs(session["transcript_result"], self.finalization.transcript_result)
        self.assertEqual(dispatch["reply_deps"], ("reply-deps", self.guild))
        self.assertEqual(dispatch["deps"], "dispatch-deps")

    def test_main_process_impl_is_a_thin_pipeline_wrapper(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def process_member_audio_impl(")
        function_source = source[start:]

        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertNotIn("prepare_voice_audio_ingress_from_runtime(", function_source)
        self.assertNotIn("run_voice_wake_probe_from_runtime(", function_source)
        self.assertNotIn("dispatch_voice_reply_from_runtime(", function_source)


if __name__ == "__main__":
    unittest.main()
