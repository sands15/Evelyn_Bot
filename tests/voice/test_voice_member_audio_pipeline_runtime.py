from __future__ import annotations

import asyncio
import sys
import unittest
from contextlib import asynccontextmanager
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
from evelyn_core.main_inference_contract import (  # noqa: E402
    MAIN_FOREGROUND_RESERVATION_ID_HEADER,
    MainForegroundReservation,
    MainForegroundReservationRejected,
    MainRequestKind,
    admitted_main_request,
    main_admission_headers,
)
from evelyn_core.voice_ingress_runtime import (  # noqa: E402
    advance_voice_ingress_epoch,
    voice_ingress_epoch_is_current,
)


class VoiceMemberAudioPipelineRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.metrics: dict[str, Any] = {"meta": {}}
        self.epochs = {11: 0}
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
            voice_ingress_epoch_is_current=lambda guild_id, epoch: (
                voice_ingress_epoch_is_current(self.epochs, guild_id, epoch)
            ),
        )

    async def run_pipeline(
        self,
        *,
        deps: VoiceMemberAudioPipelineDeps | None = None,
        debug_meta: dict[str, Any] | None = None,
        member: Any | None = None,
        voice_listener_binding: Any = None,
        release_ingress_worker: Any = None,
        voice_ingress_epoch: int = 0,
    ) -> None:
        await process_member_audio_pipeline_from_runtime(
            member
            or SimpleNamespace(id=7, display_name="정훈", guild=self.guild),
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
            voice_ingress_epoch=voice_ingress_epoch,
            voice_listener_binding=voice_listener_binding,
            release_ingress_worker=release_ingress_worker,
            deps=deps or self.deps,
        )

    def stage_names(self) -> list[str]:
        return [name for name, _payload in self.events]

    async def admit_fake_realtime_main(self) -> dict[str, str]:
        captured_headers: dict[str, str] = {}

        @asynccontextmanager
        async def request_context():
            captured_headers.update(
                main_admission_headers(MainRequestKind.REALTIME)
            )
            self.events.append(("main", dict(captured_headers)))
            yield SimpleNamespace()

        with (
            patch(
                "evelyn_core.main_inference_contract.main_admission_client_mode",
                return_value="gateway",
            ),
            patch(
                "evelyn_core.main_inference_contract._gateway_admission_lease",
                return_value=SimpleNamespace(),
            ),
        ):
            async with admitted_main_request(
                request_context,
                kind=MainRequestKind.REALTIME,
            ):
                pass
        return captured_headers

    async def test_happy_path_runs_all_stages_in_order(self) -> None:
        await self.run_pipeline()

        self.assertEqual(
            self.stage_names(),
            ["ingress", "wake", "interrupt", "stt", "finalize", "session", "dispatch"],
        )

    async def test_completed_batch_runs_once_and_authoritative_final_is_reused(self) -> None:
        calls = 0

        async def transcribe_completed_audio(_audio: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            self.events.append(("batch", kwargs))
            return {"text": "이블린 오늘 날씨"}

        await self.run_pipeline(
            deps=replace(
                self.deps,
                transcribe_completed_audio=transcribe_completed_audio,
            )
        )

        self.assertEqual(calls, 1)
        self.assertEqual(
            self.stage_names(),
            ["ingress", "batch", "wake", "interrupt", "stt", "finalize", "session", "dispatch"],
        )
        wake = next(payload for name, payload in self.events if name == "wake")
        stt = next(payload for name, payload in self.events if name == "stt")
        self.assertIs(wake["stream_result"], stt["stream_result"])
        self.assertEqual(stt["stream_result"].final_text, "이블린 오늘 날씨")
        self.assertEqual(stt["stream_result"].partial_text, "")
        self.assertEqual(stt["stream_result"].committed_text, "")
        self.assertEqual(
            self.metrics["meta"]["asr_completed_batch"],
            {"authoritative": True, "callCount": 1, "fallbackReason": None},
        )
        self.assertNotIn("이블린", repr(self.metrics))

    async def test_reset_epoch_change_while_batch_stt_waits_stops_pipeline(
        self,
    ) -> None:
        batch_started = asyncio.Event()
        release_batch = asyncio.Event()

        async def transcribe_completed_audio(_audio: Any, **kwargs: Any) -> Any:
            self.events.append(("batch", kwargs))
            batch_started.set()
            await release_batch.wait()
            return {"text": "이블린 계속해"}

        task = asyncio.create_task(
            self.run_pipeline(
                deps=replace(
                    self.deps,
                    transcribe_completed_audio=transcribe_completed_audio,
                ),
                voice_ingress_epoch=0,
            )
        )
        await asyncio.wait_for(batch_started.wait(), timeout=1.0)
        advance_voice_ingress_epoch(self.epochs, 11)
        release_batch.set()
        await asyncio.wait_for(task, timeout=1.0)

        self.assertEqual(self.stage_names(), ["ingress", "batch"])

    async def test_empty_completed_batch_stops_without_a_second_stt_call(self) -> None:
        calls = 0

        async def transcribe_completed_audio(_audio: Any, **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            self.events.append(("batch", None))
            return {"text": ""}

        await self.run_pipeline(
            deps=replace(
                self.deps,
                transcribe_completed_audio=transcribe_completed_audio,
            )
        )

        self.assertEqual(calls, 1)
        self.assertEqual(self.stage_names(), ["ingress", "batch"])
        self.assertEqual(
            self.metrics["meta"]["asr_completed_batch"]["fallbackReason"],
            "empty_final",
        )

    async def test_malformed_completed_batch_stops_without_legacy_stt(self) -> None:
        calls = 0

        async def transcribe_completed_audio(_audio: Any, **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            self.events.append(("batch", None))
            return {"text": 7}

        await self.run_pipeline(
            deps=replace(
                self.deps,
                transcribe_completed_audio=transcribe_completed_audio,
            )
        )

        self.assertEqual(calls, 1)
        self.assertEqual(self.stage_names(), ["ingress", "batch"])
        self.assertEqual(
            self.metrics["meta"]["asr_completed_batch"]["fallbackReason"],
            "batch_response_invalid",
        )

    async def test_completed_batch_error_fails_closed_after_one_call(self) -> None:
        calls = 0

        async def transcribe_completed_audio(_audio: Any, **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            self.events.append(("batch", None))
            raise RuntimeError("private detail")

        await self.run_pipeline(
            deps=replace(
                self.deps,
                transcribe_completed_audio=transcribe_completed_audio,
            )
        )

        self.assertEqual(calls, 1)
        self.assertEqual(self.stage_names(), ["ingress", "batch"])
        self.assertEqual(
            self.metrics["meta"]["asr_completed_batch"],
            {
                "authoritative": False,
                "callCount": 1,
                "fallbackReason": "batch_error",
                "errorType": "RuntimeError",
            },
        )
        self.assertNotIn("private detail", repr(self.metrics))

    async def test_stale_listener_after_batch_never_reaches_wake_or_admission(self) -> None:
        source_client = SimpleNamespace(
            _listener_generation=8,
            channel=SimpleNamespace(id=22),
        )
        member = SimpleNamespace(
            id=7,
            display_name="정훈",
            guild=SimpleNamespace(id=11, voice_client=source_client),
        )

        async def transcribe_completed_audio(_audio: Any, **_kwargs: Any) -> Any:
            self.events.append(("batch", None))
            source_client._listener_generation = 9
            return {"text": "이블린 오늘 날씨"}

        await self.run_pipeline(
            deps=replace(
                self.deps,
                transcribe_completed_audio=transcribe_completed_audio,
            ),
            member=member,
            voice_listener_binding=(source_client, 8, 22),
        )

        self.assertEqual(self.stage_names(), ["ingress", "batch"])

    async def test_wake_miss_still_uses_only_the_one_completed_batch(self) -> None:
        calls = 0

        async def transcribe_completed_audio(_audio: Any, **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            self.events.append(("batch", None))
            return {"text": "오늘 날씨"}

        async def no_wake(**kwargs: Any) -> None:
            self.events.append(("wake", kwargs))
            return None

        await self.run_pipeline(
            deps=replace(
                self.deps,
                transcribe_completed_audio=transcribe_completed_audio,
                run_wake_probe=no_wake,
            )
        )

        self.assertEqual(calls, 1)
        self.assertEqual(self.stage_names(), ["ingress", "batch", "wake"])

    async def test_owner_followup_reserves_before_stt_and_unused_ticket_is_cancelled(self) -> None:
        reservation = MainForegroundReservation(
            reservation_id="a" * 32,
            capture_generation=3,
            backend_epoch="epoch-1",
            ttl_ms=900,
        )

        async def reserve(generation: int, **_kwargs: Any) -> Any:
            self.events.append(("reserve", generation))
            return reservation

        async def cancel(value: Any, **_kwargs: Any) -> None:
            self.events.append(("cancel", value.capture_generation))

        async def batch(_audio: Any, **_kwargs: Any) -> Any:
            self.events.append(("batch", None))
            return {"text": "후속 질문"}

        async def dispatch(**kwargs: Any) -> None:
            headers = await self.admit_fake_realtime_main()
            self.events.append(("dispatch", kwargs))
            self.assertEqual(
                headers[MAIN_FOREGROUND_RESERVATION_ID_HEADER],
                reservation.reservation_id,
            )

        deps = replace(
            self.deps,
            build_wake_probe_deps=lambda: SimpleNamespace(
                is_room_owner_active=lambda _room, member_id: member_id == 7,
                is_session_active_for_user=lambda _session, member_id: member_id == 7,
            ),
            transcribe_completed_audio=batch,
            reserve_main_foreground=reserve,
            cancel_main_foreground=cancel,
            dispatch_voice_reply=dispatch,
        )
        await self.run_pipeline(deps=deps)

        stages = self.stage_names()
        self.assertLess(stages.index("reserve"), stages.index("batch"))
        self.assertEqual(stages[-2:], ["dispatch", "cancel"])
        self.assertEqual(
            [payload for name, payload in self.events if name == "reserve"],
            [3],
        )

    async def test_owner_followup_slow_stt_reissues_stale_ticket_before_main(self) -> None:
        clock = [10.0]
        reservations = [
            MainForegroundReservation(
                reservation_id="c" * 32,
                capture_generation=3,
                backend_epoch="epoch-slow",
                ttl_ms=900,
            ),
            MainForegroundReservation(
                reservation_id="d" * 32,
                capture_generation=3,
                backend_epoch="epoch-slow",
                ttl_ms=900,
            ),
        ]
        reserve_count = 0

        async def reserve(generation: int, **_kwargs: Any) -> Any:
            nonlocal reserve_count
            value = reservations[reserve_count]
            reserve_count += 1
            self.events.append(("reserve", value.reservation_id))
            self.assertEqual(generation, 3)
            return value

        async def cancel(value: Any, **_kwargs: Any) -> None:
            self.events.append(("cancel", value.reservation_id))

        async def batch(_audio: Any, **_kwargs: Any) -> Any:
            self.events.append(("batch", None))
            clock[0] = 10.75
            return {"text": "느린 후속 질문"}

        async def dispatch(**kwargs: Any) -> None:
            headers = await self.admit_fake_realtime_main()
            self.events.append(("dispatch", kwargs))
            self.assertEqual(
                headers[MAIN_FOREGROUND_RESERVATION_ID_HEADER],
                reservations[1].reservation_id,
            )

        deps = replace(
            self.deps,
            build_wake_probe_deps=lambda: SimpleNamespace(
                is_room_owner_active=lambda _room, _member: True,
                is_session_active_for_user=lambda _session, _member: True,
            ),
            transcribe_completed_audio=batch,
            reserve_main_foreground=reserve,
            cancel_main_foreground=cancel,
            dispatch_voice_reply=dispatch,
        )
        with patch(
            "evelyn_core.voice_member_audio_pipeline_runtime._main_foreground_monotonic",
            side_effect=lambda: clock[0],
        ):
            await self.run_pipeline(deps=deps)

        self.assertEqual(reserve_count, 2)
        self.assertEqual(
            [payload for name, payload in self.events if name == "cancel"],
            [reservations[0].reservation_id, reservations[1].reservation_id],
        )
        self.assertNotEqual(
            self.metrics["meta"].get("main_foreground_reservation", {}).get(
                "state"
            ),
            "rejected",
        )

    async def test_deferred_foreground_owner_fails_closed_for_sibling_main(self) -> None:
        reservation = MainForegroundReservation(
            reservation_id="9" * 32,
            capture_generation=3,
            backend_epoch="epoch-owner",
            ttl_ms=900,
        )

        async def reserve(_generation: int, **_kwargs: Any) -> Any:
            return reservation

        async def cancel(_value: Any, **_kwargs: Any) -> None:
            return None

        async def dispatch(**_kwargs: Any) -> None:
            owner_ready = asyncio.Event()
            release_owner = asyncio.Event()

            @asynccontextmanager
            async def owner_request_context():
                self.assertEqual(
                    main_admission_headers(MainRequestKind.REALTIME)[
                        MAIN_FOREGROUND_RESERVATION_ID_HEADER
                    ],
                    reservation.reservation_id,
                )
                yield SimpleNamespace()

            @asynccontextmanager
            async def sibling_request_context():
                raise AssertionError("sibling request must fail before HTTP")
                yield SimpleNamespace()

            async def owner() -> None:
                async with admitted_main_request(
                    owner_request_context,
                    kind=MainRequestKind.REALTIME,
                ):
                    owner_ready.set()
                    await release_owner.wait()

            async def sibling() -> None:
                await owner_ready.wait()
                try:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "main_llm_pre_admission_already_claimed",
                    ):
                        async with admitted_main_request(
                            sibling_request_context,
                            kind=MainRequestKind.REALTIME,
                        ):
                            pass
                finally:
                    release_owner.set()

            with (
                patch(
                    "evelyn_core.main_inference_contract.main_admission_client_mode",
                    return_value="gateway",
                ),
                patch(
                    "evelyn_core.main_inference_contract._gateway_admission_lease",
                    return_value=SimpleNamespace(),
                ),
            ):
                await asyncio.gather(
                    asyncio.create_task(owner()),
                    asyncio.create_task(sibling()),
                )

        await self.run_pipeline(
            deps=replace(
                self.deps,
                build_wake_probe_deps=lambda: SimpleNamespace(
                    is_room_owner_active=lambda _room, _member: True,
                    is_session_active_for_user=lambda _session, _member: True,
                ),
                reserve_main_foreground=reserve,
                cancel_main_foreground=cancel,
                dispatch_voice_reply=dispatch,
            )
        )

    async def test_owner_followup_typed_reservation_rejection_uses_plain_turn(self) -> None:
        async def reserve(generation: int, **_kwargs: Any) -> Any:
            self.events.append(("reserve", generation))
            raise MainForegroundReservationRejected("conflict")

        async def dispatch(**kwargs: Any) -> None:
            headers = await self.admit_fake_realtime_main()
            self.assertNotIn(MAIN_FOREGROUND_RESERVATION_ID_HEADER, headers)
            self.events.append(("dispatch", kwargs))

        await self.run_pipeline(
            deps=replace(
                self.deps,
                build_wake_probe_deps=lambda: SimpleNamespace(
                    is_room_owner_active=lambda _room, _member: True,
                    is_session_active_for_user=lambda _session, _member: True,
                ),
                reserve_main_foreground=reserve,
                dispatch_voice_reply=dispatch,
            )
        )

        self.assertEqual(
            self.stage_names(),
            ["ingress", "reserve", "wake", "interrupt", "stt", "finalize", "session", "main", "dispatch"],
        )
        self.assertEqual(
            self.metrics["meta"]["main_foreground_reservation"]["state"],
            "rejected",
        )

    async def test_owner_followup_network_reservation_error_fails_closed(self) -> None:
        async def reserve(generation: int, **_kwargs: Any) -> Any:
            self.events.append(("reserve", generation))
            raise ConnectionError("private gateway detail")

        with self.assertRaises(ConnectionError):
            await self.run_pipeline(
                deps=replace(
                    self.deps,
                    build_wake_probe_deps=lambda: SimpleNamespace(
                        is_room_owner_active=lambda _room, _member: True,
                        is_session_active_for_user=lambda _session, _member: True,
                    ),
                    reserve_main_foreground=reserve,
                )
            )

        self.assertEqual(self.stage_names(), ["ingress", "reserve"])

    async def test_initial_wake_reserves_only_after_session_acceptance(self) -> None:
        reservation = MainForegroundReservation(
            reservation_id="b" * 32,
            capture_generation=3,
            backend_epoch="epoch-1",
            ttl_ms=900,
        )

        async def reserve(generation: int, **_kwargs: Any) -> Any:
            self.events.append(("reserve", generation))
            return reservation

        async def cancel(_value: Any, **_kwargs: Any) -> None:
            self.events.append(("cancel", None))

        async def no_wake(**kwargs: Any) -> None:
            self.events.append(("wake", kwargs))
            return None

        await self.run_pipeline(
            deps=replace(
                self.deps,
                reserve_main_foreground=reserve,
                cancel_main_foreground=cancel,
                run_wake_probe=no_wake,
            )
        )
        self.assertEqual(self.stage_names(), ["ingress", "wake"])

        self.events.clear()
        await self.run_pipeline(
            deps=replace(
                self.deps,
                reserve_main_foreground=reserve,
                cancel_main_foreground=cancel,
            )
        )
        stages = self.stage_names()
        self.assertGreater(stages.index("reserve"), stages.index("session"))
        self.assertEqual(stages[-2:], ["dispatch", "cancel"])

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
        release_ingress_worker = object()
        await self.run_pipeline(release_ingress_worker=release_ingress_worker)

        wake = next(payload for name, payload in self.events if name == "wake")
        interrupt = next(payload for name, payload in self.events if name == "interrupt")
        stt = next(payload for name, payload in self.events if name == "stt")
        finalize = next(payload for name, payload in self.events if name == "finalize")
        session = next(payload for name, payload in self.events if name == "session")
        dispatch = next(payload for name, payload in self.events if name == "dispatch")
        self.assertTrue(wake["source_is_current"]())
        self.assertTrue(interrupt["source_is_current"]())
        self.assertEqual(interrupt["active_speaker_user_id"], 7)
        self.assertEqual(stt["wake_probe"], "이블린")
        self.assertEqual(finalize["wake_match_mode"], "exact")
        self.assertIs(session["transcript_result"], self.finalization.transcript_result)
        self.assertEqual(dispatch["reply_deps"], ("reply-deps", self.guild))
        self.assertEqual(dispatch["deps"], "dispatch-deps")
        self.assertIs(dispatch["release_ingress_worker"], release_ingress_worker)

    def test_main_process_impl_is_a_thin_pipeline_wrapper(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_io_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("    async def process_member_audio_impl(")
        function_source = source[start:]

        self.assertIn("process_member_audio_pipeline_from_runtime(", function_source)
        self.assertIn("release_ingress_worker=release_ingress_worker", function_source)
        self.assertNotIn("prepare_voice_audio_ingress_from_runtime(", function_source)
        self.assertNotIn("run_voice_wake_probe_from_runtime(", function_source)
        self.assertNotIn("dispatch_voice_reply_from_runtime(", function_source)

    def test_main_discord_stt_uses_one_completed_batch_and_no_stream_session(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        client_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "stt_client.py"
        ).read_text(encoding="utf-8")
        start = client_source.index("async def transcribe_completed_audio16k_via_service(")
        end = client_source.index("\ndef start_stt_stream_via_service(", start)
        function_source = client_source[start:end]

        self.assertEqual(function_source.count("transcribe_audio16k_via_service,"), 1)
        self.assertNotIn("transcribe_audio16k_sync", function_source)
        self.assertNotIn("start_stt_stream_via_service", function_source)
        self.assertNotIn("push_stt_stream_chunk_via_service", function_source)
        self.assertNotIn("finish_stt_stream_via_service", function_source)
        self.assertIn(") if STT_STREAMING_ENABLED else None,", source)
        self.assertIn("reserve_main_foreground=partial(", source)
        self.assertNotIn("async def transcribe_discord_audio16k_once(", source)


if __name__ == "__main__":
    unittest.main()
