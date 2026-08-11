import sys
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_orchestration import (  # noqa: E402
    VoiceTurnOrchestrator,
    VoiceTurnOrchestratorDeps,
    VoiceTurnRequest,
    accept_voice_reply_execution,
    prepare_and_execute_accepted_voice_reply,
    prepare_voice_reply_for_delivery,
    run_locked_voice_reply_delivery,
)
from evelyn_core.tts_playback import play_audio_source  # noqa: E402
from evelyn_core.session_continuity import (  # noqa: E402
    SessionContinuityCheckpoint,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402
from evelyn_core.voice_reply_side_effects import (  # noqa: E402
    checkpoint_accepted_voice_turn_from_runtime,
)
from evelyn_core.voice_pipeline import (  # noqa: E402
    DeliveryPlan,
    TranscriptResult,
    VoiceReplyRequest,
    VoiceSegment,
    build_answer_payload_from_text,
    build_delivery_plan,
    build_route_decision,
)


def split_test_tts_chunks(text: str, *, force: bool = False) -> tuple[list[str], str]:
    return [text] if text else [], ""


class VoiceTurnOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def accepted_voice_fixture() -> tuple[
        VoiceReplyRequest,
        VoiceSegment,
        TranscriptResult,
    ]:
        segment = VoiceSegment(
            guild_id=7,
            room_session_key="room-1",
            session_key="session-1",
            speaker_user_id=42,
            speaker_name="tester",
            audio16k=np.zeros(1600, dtype=np.float32),
            sampling_rate=16000,
            duration_sec=0.1,
            segment_id=1,
            owner_user_id=42,
        )
        transcript = TranscriptResult(
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="이블린",
            probe_text="계속해 줘",
            confirm_text="계속해 줘",
            reject_reason=None,
            partial_text="",
            committed_text="계속해 줘",
            final_text="계속해 줘",
            speaker_user_id=42,
            duration_sec=0.1,
        )
        reply = VoiceReplyRequest(
            transcript=transcript,
            segment=segment,
            gate_mode="wake_entry",
            raw_user_text="계속해 줘",
            prompt_user_text="계속해 줘",
            history_user_text="계속해 줘",
            wake_only_turn=False,
            turn_type="voice_request",
            selected_path="main_llm",
            reply_source="main_llm",
            topic_id="topic-1",
        )
        return reply, segment, transcript

    def test_accepted_voice_checkpoint_precedes_owner_and_scope(self) -> None:
        reply, segment, transcript = self.accepted_voice_fixture()
        events: list[str] = []
        metrics: dict[str, Any] = {"meta": {}}

        accepted = accept_voice_reply_execution(
            session_key="session-1",
            room_session_key="room-1",
            user_id=42,
            source_turn_id="turn-1",
            segment_id=1,
            gate_mode="wake_entry",
            reply_in_progress=False,
            voice_reply=reply,
            voice_segment=segment,
            transcript=transcript,
            ingress_source="discord_voice",
            queue_wait_ms=0.0,
            active_conversation_awaiting_reply_sec=120.0,
            active_conversation_voice_sec=30.0,
            metrics=metrics,
            start_new_turn=lambda *_args, **_kwargs: self.fail(
                "Discord voice precommit owns the new turn"
            ),
            update_session_state=lambda *_args, **_kwargs: self.fail(
                "Discord voice precommit owns the user state"
            ),
            checkpoint_accepted_voice_turn=lambda **_kwargs: events.append(
                "checkpoint"
            ),
            set_room_owner=lambda *_args, **_kwargs: events.append("owner"),
            session_partial_stt_text={},
            session_committed_stt_text={},
            partial_stt_cache={},
            owner_user_id=42,
            make_turn_scope=lambda _turn_id: (
                events.append("scope") or "scope"
            ),
            replace_room_turn_scope=lambda *_args, **_kwargs: None,
            attach_current_task=lambda _scope: "task",
            set_room_reply_in_progress=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(events, ["checkpoint", "owner", "scope"])
        self.assertEqual(accepted.accepted_turn_id, "turn-1")

    def test_accepted_voice_checkpoint_failure_starts_no_reply_state(self) -> None:
        reply, segment, transcript = self.accepted_voice_fixture()
        events: list[str] = []

        def fail_checkpoint(**_kwargs: Any) -> None:
            raise RuntimeError("conversation_continuity_commit_failed")

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_continuity_commit_failed",
        ):
            accept_voice_reply_execution(
                session_key="session-1",
                room_session_key="room-1",
                user_id=42,
                source_turn_id="turn-1",
                segment_id=1,
                gate_mode="wake_entry",
                reply_in_progress=False,
                voice_reply=reply,
                voice_segment=segment,
                transcript=transcript,
                ingress_source="discord_voice",
                queue_wait_ms=0.0,
                active_conversation_awaiting_reply_sec=120.0,
                active_conversation_voice_sec=30.0,
                metrics={"meta": {}},
                start_new_turn=lambda *_args, **_kwargs: None,
                update_session_state=lambda *_args, **_kwargs: None,
                checkpoint_accepted_voice_turn=fail_checkpoint,
                set_room_owner=lambda *_args, **_kwargs: events.append("owner"),
                session_partial_stt_text={},
                session_committed_stt_text={},
                partial_stt_cache={},
                owner_user_id=42,
                make_turn_scope=lambda _turn_id: events.append("scope"),
                replace_room_turn_scope=lambda *_args, **_kwargs: None,
                attach_current_task=lambda _scope: events.append("task"),
                set_room_reply_in_progress=lambda *_args, **_kwargs: events.append(
                    "reply"
                ),
            )

        self.assertEqual(events, [])

    def test_local_mic_keeps_existing_noncheckpointed_turn_path(self) -> None:
        reply, segment, transcript = self.accepted_voice_fixture()
        events: list[str] = []

        accepted = accept_voice_reply_execution(
            session_key="session-1",
            room_session_key="room-1",
            user_id=42,
            source_turn_id="turn-local",
            segment_id=1,
            gate_mode="wake_entry",
            reply_in_progress=False,
            voice_reply=reply,
            voice_segment=segment,
            transcript=transcript,
            ingress_source="local_mic",
            queue_wait_ms=0.0,
            active_conversation_awaiting_reply_sec=120.0,
            active_conversation_voice_sec=30.0,
            metrics={"meta": {}},
            start_new_turn=lambda *_args, **_kwargs: (
                events.append("start") or "turn-local"
            ),
            update_session_state=lambda *_args, **_kwargs: events.append(
                "update"
            ),
            checkpoint_accepted_voice_turn=lambda **_kwargs: self.fail(
                "local mic uses Fast ingress continuity"
            ),
            set_room_owner=lambda *_args, **_kwargs: events.append("owner"),
            session_partial_stt_text={},
            session_committed_stt_text={},
            partial_stt_cache={},
            owner_user_id=42,
            make_turn_scope=lambda _turn_id: (
                events.append("scope") or "scope"
            ),
            replace_room_turn_scope=lambda *_args, **_kwargs: None,
            attach_current_task=lambda _scope: "task",
            set_room_reply_in_progress=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(events, ["start", "update", "owner", "scope"])
        self.assertEqual(accepted.accepted_turn_id, "turn-local")

    async def test_cancel_after_precommit_preserves_control_flow_and_cleans_scope(
        self,
    ) -> None:
        reply, segment, transcript = self.accepted_voice_fixture()
        events: list[str] = []
        scopes: list[object] = []
        reply_states: list[bool] = []
        metrics: dict[str, Any] = {"meta": {}}
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        store = SessionStateStore.create_empty()
        manager = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=root / "active.json",
            status_path=root / "status.json",
            system_prompt="system",
        )

        def begin_user_only_turn(key: str, text: str, **kwargs: Any) -> str:
            return store.begin_user_only_turn(
                key,
                text,
                system_prompt="system",
                max_history_items=12,
                active_conversation_awaiting_reply_sec=120.0,
                **kwargs,
            )

        checkpoint_deps = SimpleNamespace(
            begin_user_only_turn=begin_user_only_turn,
            commit_session_continuity=manager.commit_completed_turn,
            log=lambda *_args, **_kwargs: None,
        )

        def checkpoint(**kwargs: Any) -> None:
            events.append("checkpoint")
            checkpoint_accepted_voice_turn_from_runtime(
                **kwargs,
                deps=checkpoint_deps,
            )

        def make_scope(_turn_id: str) -> object:
            scope = object()
            scopes.append(scope)
            return scope

        async def cancel_during_llm(*_args: Any, **_kwargs: Any) -> str:
            events.append("llm")
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await prepare_and_execute_accepted_voice_reply(
                session_key="session-1",
                room_session_key="room-1",
                user_id=42,
                source_turn_id="turn-1",
                segment_id=1,
                gate_mode="wake_entry",
                reply_in_progress=False,
                voice_reply=reply,
                voice_segment=segment,
                transcript=transcript,
                ingress_source="discord_voice",
                queue_wait_ms=0.0,
                metrics=metrics,
                active_conversation_awaiting_reply_sec=120.0,
                active_conversation_voice_sec=30.0,
                start_new_turn=lambda *_args, **_kwargs: self.fail(
                    "Discord voice precommit owns the new turn"
                ),
                update_session_state=lambda *_args, **_kwargs: self.fail(
                    "Discord voice precommit owns the user state"
                ),
                checkpoint_accepted_voice_turn=checkpoint,
                set_room_owner=lambda *_args, **_kwargs: events.append("owner"),
                session_partial_stt_text={},
                session_committed_stt_text={},
                partial_stt_cache={},
                owner_user_id=42,
                make_turn_scope=make_scope,
                replace_room_turn_scope=lambda *_args, **_kwargs: None,
                attach_current_task=lambda _scope: asyncio.current_task(),
                set_room_reply_in_progress=(
                    lambda _room, active, **_kwargs: reply_states.append(active)
                ),
                session_locks={},
                speaker_display_name="tester",
                visible_text=str,
                print_fn=lambda *_args, **_kwargs: None,
                get_voice_client=object,
                member=type(
                    "Member",
                    (),
                    {"id": 42, "display_name": "tester"},
                )(),
                canned_wake_reply="응",
                guild_id=7,
                room_key=None,
                person_key=None,
                session_memory_key=None,
                speak_answer=lambda *_args, **_kwargs: self.fail(
                    "single reply TTS must not run"
                ),
                ask_llm_and_speak_streaming=cancel_during_llm,
                record_voice_pipeline_failure=lambda *_args, **_kwargs: self.fail(
                    "cancellation must stay control flow"
                ),
                finalize_voice_reply_side_effects=lambda **_kwargs: self.fail(
                    "cancellation must not synthesize a reply"
                ),
                log_voice_stage=lambda *_args, **_kwargs: None,
                strip_omnivoice_tags=str,
                get_room_turn_scope=lambda _room: scopes[0],
                detach_task=lambda *_args, **_kwargs: events.append("detach"),
                clear_room_turn_scope=lambda *_args, **_kwargs: events.append(
                    "clear"
                ),
            )

        self.assertEqual(events[:3], ["checkpoint", "owner", "llm"])
        self.assertEqual(events[-2:], ["detach", "clear"])
        self.assertEqual(reply_states, [True, False])
        restored_store = SessionStateStore.create_empty()
        restore_status = SessionContinuityCheckpoint(
            store=restored_store,
            checkpoint_path=root / "active.json",
            status_path=root / "restored-status.json",
            system_prompt="system",
        ).restore()
        self.assertEqual(restore_status["state"], "restored")
        self.assertEqual(
            [row["role"] for row in restored_store.histories["session-1"]],
            ["system", "user"],
        )
        self.assertEqual(restored_store.turn_ids["session-1"], "turn-1")

    async def test_lost_playback_callback_releases_room_lock_and_finalizes_failure(
        self,
    ) -> None:
        class FakeVc:
            stop_count = 0

            def __init__(self) -> None:
                self.source = None

            def is_playing(self) -> bool:
                return False

            def is_paused(self) -> bool:
                return False

            def play(self, source: object, *, after: Any) -> None:
                self.source = source
                return None

            def stop(self) -> None:
                self.stop_count += 1

        vc = FakeVc()
        lock = asyncio.Lock()
        finalized: list[dict[str, Any]] = []
        failures: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        metrics: dict[str, Any] = {"meta": {}}
        reply = VoiceReplyRequest(
            transcript=None,
            segment=None,
            gate_mode="wake_entry",
            raw_user_text="이블린",
            prompt_user_text="이블린",
            history_user_text="이블린",
            wake_only_turn=True,
            turn_type="wake_only",
            selected_path="canned_wake_reply",
            reply_source="canned_wake_reply",
            topic_id="topic-1",
        )

        async def speak_answer(
            voice_client: Any,
            _answer: str,
            **_kwargs: Any,
        ) -> None:
            await play_audio_source(
                voice_client,
                object(),
                timeout_sec=0.01,
            )

        result = await asyncio.wait_for(
            run_locked_voice_reply_delivery(
                room_session_key="room-1",
                lock=lock,
                get_voice_client=lambda: vc,
                member=type(
                    "Member",
                    (),
                    {"id": 42, "display_name": "tester"},
                )(),
                voice_reply=reply,
                canned_wake_reply="응, 듣고 있어.",
                accepted_turn_id="turn-timeout",
                session_key="session-1",
                guild_id=7,
                room_key=None,
                person_key=None,
                session_memory_key=None,
                metrics=metrics,
                turn_scope="scope",
                segment_id=1,
                gate_mode="wake_entry",
                on_final_answer=None,
                speak_answer=speak_answer,
                ask_llm_and_speak_streaming=lambda *_args, **_kwargs: None,
                record_voice_pipeline_failure=(
                    lambda *args, **kwargs: failures.append((args, kwargs))
                ),
                finalize_voice_reply_side_effects=(
                    lambda **kwargs: finalized.append(kwargs)
                ),
                log_voice_stage=lambda *_args, **_kwargs: None,
                strip_omnivoice_tags=lambda text: text,
                report_waiting_on_lock=None,
                report_delivery_error=lambda _exc: None,
            ),
            timeout=0.5,
        )

        self.assertIsNone(result)
        self.assertEqual(vc.stop_count, 1)
        self.assertFalse(lock.locked())
        await asyncio.wait_for(lock.acquire(), timeout=0.1)
        lock.release()
        self.assertEqual(len(finalized), 1)
        self.assertFalse(finalized[0]["delivery_succeeded"])
        self.assertEqual(finalized[0]["failure_code"], "voice_delivery_failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0][0], "tts_playback_failed")

    async def test_missing_voice_connection_durably_routes_unanswered_turn(
        self,
    ) -> None:
        finalized: list[dict[str, Any]] = []
        failures: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        summaries: list[dict[str, Any]] = []
        metrics: dict[str, Any] = {"meta": {}}
        member = type(
            "Member",
            (),
            {"id": 42, "display_name": "tester"},
        )()
        reply = VoiceReplyRequest(
            transcript=None,
            segment=None,
            gate_mode="wake_entry",
            raw_user_text="이어가줘",
            prompt_user_text="이어가줘",
            history_user_text="이어가줘",
            wake_only_turn=False,
            turn_type="voice_request",
            selected_path="main_llm",
            reply_source="main_llm",
            topic_id="topic-1",
        )

        result = await run_locked_voice_reply_delivery(
            room_session_key="room-1",
            lock=__import__("asyncio").Lock(),
            get_voice_client=lambda: None,
            member=member,
            voice_reply=reply,
            canned_wake_reply="응",
            accepted_turn_id="turn-1",
            session_key="session-1",
            guild_id=7,
            room_key=None,
            person_key=None,
            session_memory_key=None,
            metrics=metrics,
            turn_scope="scope",
            segment_id=1,
            gate_mode="wake_entry",
            on_final_answer=None,
            speak_answer=lambda *_args, **_kwargs: self.fail(
                "TTS must not start without a voice connection"
            ),
            ask_llm_and_speak_streaming=(
                lambda *_args, **_kwargs: self.fail(
                    "LLM delivery must not start without a voice connection"
                )
            ),
            record_voice_pipeline_failure=(
                lambda *args, **kwargs: failures.append((args, kwargs))
            ),
            finalize_voice_reply_side_effects=(
                lambda **kwargs: finalized.append(kwargs)
            ),
            log_voice_stage=lambda *_args, **_kwargs: None,
            strip_omnivoice_tags=lambda text: text,
            report_waiting_on_lock=None,
            report_delivery_error=lambda _exc: None,
            log_voice_bottleneck_summary=lambda _metrics, **payload: summaries.append(
                payload
            ),
        )

        self.assertIsNone(result)
        self.assertEqual(len(finalized), 1)
        self.assertFalse(finalized[0]["delivery_succeeded"])
        self.assertEqual(
            finalized[0]["failure_code"],
            "voice_connection_unavailable",
        )
        self.assertEqual(
            finalized[0]["voice_reply"].history_user_text,
            "이어가줘",
        )
        self.assertEqual(
            metrics["meta"]["voice_delivery_failure_code"],
            "voice_connection_unavailable",
        )
        self.assertEqual(
            failures,
            [
                (
                    (
                        "voice_connection_unavailable",
                        "voice_connection_unavailable",
                        metrics,
                    ),
                    {"stage": "voice_connection"},
                )
            ],
        )
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["event_name"], "voice_turn_summary")

    def make_orchestrator(
        self,
        *,
        short_circuit_answer: str | None = None,
        skill_route_answer: str | None = None,
        skill_route_error: Exception | None = None,
        route_decision_kwargs: dict[str, Any] | None = None,
        events: list[Any] | None = None,
    ) -> VoiceTurnOrchestrator:
        recorded_events = events if events is not None else []
        route_decision = build_route_decision(
            action="answer",
            route="main_direct",
            source="text",
            prompt_text="hello",
            **(route_decision_kwargs or {}),
        )

        async def prepare_route_context(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], dict, Any, dict, bool]:
            recorded_events.append(("prepare_route_context", args, kwargs))
            return [{"role": "system", "content": "base"}], {"action": "answer"}, route_decision, {"action": "answer"}, False

        async def maybe_handle_short_circuit_route(**kwargs: Any) -> tuple[str | None, Any]:
            recorded_events.append(("short_circuit", kwargs))
            return short_circuit_answer, kwargs.get("on_first_chunk")

        async def maybe_execute_registered_route(**kwargs: Any) -> str | None:
            recorded_events.append(("skill_route", kwargs))
            if skill_route_error is not None:
                raise skill_route_error
            return skill_route_answer

        async def run_main_llm_turn(**kwargs: Any) -> str:
            recorded_events.append(("main_llm", kwargs))
            on_first_chunk = kwargs.get("on_first_chunk")
            if on_first_chunk is not None:
                on_first_chunk()
            return "main answer"

        async def emit_delivery_plan_chunks(delivery_plan: DeliveryPlan, **kwargs: Any) -> None:
            recorded_events.append(("delivery", delivery_plan, kwargs))
            on_sentence = kwargs.get("on_sentence")
            if on_sentence is not None:
                for chunk in delivery_plan.tts_chunks:
                    await on_sentence(chunk)

        return VoiceTurnOrchestrator(
            VoiceTurnOrchestratorDeps(
                prepare_route_context=prepare_route_context,
                maybe_handle_short_circuit_route=maybe_handle_short_circuit_route,
                maybe_execute_registered_route=maybe_execute_registered_route,
                run_main_llm_turn=run_main_llm_turn,
                emit_delivery_plan_chunks=emit_delivery_plan_chunks,
                build_answer_payload_from_text=build_answer_payload_from_text,
                build_delivery_plan=build_delivery_plan,
                split_tts_sentences=split_test_tts_chunks,
            )
        )

    async def test_short_circuit_stops_before_skill_and_main_llm(self) -> None:
        events: list[Any] = []
        orchestrator = self.make_orchestrator(short_circuit_answer="fast answer", events=events)

        result = await orchestrator.execute(VoiceTurnRequest(user_text="what time is it?"))

        self.assertEqual(result.answer_text, "fast answer")
        self.assertEqual(result.handled_by, "short_circuit")
        self.assertEqual([event[0] for event in events], ["prepare_route_context", "short_circuit"])

    async def test_skill_route_answer_is_delivered_without_main_llm(self) -> None:
        events: list[Any] = []
        first_chunk_calls = 0
        spoken_chunks: list[str] = []

        def on_first_chunk() -> None:
            nonlocal first_chunk_calls
            first_chunk_calls += 1

        async def on_sentence(chunk: str) -> None:
            spoken_chunks.append(chunk)

        orchestrator = self.make_orchestrator(skill_route_answer="skill answer", events=events)

        result = await orchestrator.execute(
            VoiceTurnRequest(
                user_text="run route",
                on_first_chunk=on_first_chunk,
                on_sentence=on_sentence,
            )
        )

        self.assertEqual(result.answer_text, "skill answer")
        self.assertEqual(result.handled_by, "skill_route")
        self.assertEqual(first_chunk_calls, 1)
        self.assertEqual(spoken_chunks, ["skill answer"])
        self.assertEqual([event[0] for event in events].count("delivery"), 1)
        self.assertNotIn("main_llm", [event[0] for event in events])

    async def test_main_llm_receives_request_and_route_context(self) -> None:
        events: list[Any] = []
        first_chunk_calls = 0

        def on_first_chunk() -> None:
            nonlocal first_chunk_calls
            first_chunk_calls += 1

        orchestrator = self.make_orchestrator(events=events)

        result = await orchestrator.execute(
            VoiceTurnRequest(user_text="hello", session_key="session-1", on_first_chunk=on_first_chunk)
        )

        self.assertEqual(result.answer_text, "main answer")
        self.assertEqual(result.handled_by, "main_llm")
        self.assertEqual(first_chunk_calls, 1)
        self.assertNotIn("delivery", [event[0] for event in events])
        main_event = [event for event in events if event[0] == "main_llm"][0]
        self.assertEqual(main_event[1]["request"].session_key, "session-1")
        self.assertEqual(main_event[1]["route_context"].route_decision.route, "main_direct")

    async def test_skill_route_receives_source_and_session_identity(self) -> None:
        events: list[Any] = []
        orchestrator = self.make_orchestrator(skill_route_answer="skill answer", events=events)

        await orchestrator.execute(
            VoiceTurnRequest(
                user_text="run route",
                guild_id=123,
                session_key="session-1",
                room_key="room-1",
                person_key="person-1",
                session_memory_key="memory-1",
                source="voice",
            )
        )

        skill_event = [event for event in events if event[0] == "skill_route"][0]
        self.assertEqual(skill_event[1]["source"], "voice")
        self.assertEqual(skill_event[1]["guild_id"], 123)
        self.assertEqual(skill_event[1]["session_key"], "session-1")
        self.assertEqual(skill_event[1]["room_key"], "room-1")
        self.assertEqual(skill_event[1]["person_key"], "person-1")
        self.assertEqual(skill_event[1]["session_memory_key"], "memory-1")

    async def test_skill_route_failure_marks_error_layer(self) -> None:
        metrics: dict[str, Any] = {}
        orchestrator = self.make_orchestrator(skill_route_error=RuntimeError("route exploded"))

        with self.assertRaises(RuntimeError):
            await orchestrator.execute(VoiceTurnRequest(user_text="run route", metrics=metrics))

        self.assertEqual(metrics["meta"]["error_layer"], "voice_turn_orchestrator.skill_route")
        self.assertEqual(metrics["meta"]["error"], "RuntimeError")
        self.assertNotIn("route exploded", str(metrics))

    def test_prepare_voice_reply_skips_tts_suppression_after_interrupt(self) -> None:
        captured_kwargs: dict[str, Any] = {}
        transcript = TranscriptResult(
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="evelyn",
            probe_text="evelyn",
            confirm_text="evelyn",
            reject_reason=None,
            partial_text="",
            committed_text="",
            final_text="evelyn stop",
            speaker_user_id=10,
            duration_sec=0.6,
        )
        segment = VoiceSegment(
            guild_id=123,
            room_session_key="room-1",
            session_key="session-1",
            speaker_user_id=10,
            speaker_name="tester",
            audio16k=np.zeros(1600, dtype=np.float32),
            sampling_rate=16000,
            duration_sec=0.6,
            segment_id=1,
            owner_user_id=None,
        )

        def should_reply_to_voice(*args: Any, **kwargs: Any) -> tuple[bool, str, str]:
            captured_kwargs.update(kwargs)
            return True, "ok", "wake_entry"

        def build_voice_reply_request(**kwargs: Any) -> VoiceReplyRequest:
            return VoiceReplyRequest(
                transcript=kwargs["transcript"],
                segment=kwargs["segment"],
                gate_mode=kwargs["gate_mode"],
                raw_user_text="evelyn stop",
                prompt_user_text="stop",
                history_user_text="stop",
                wake_only_turn=False,
                turn_type="normal",
                selected_path="main",
                reply_source="voice",
                topic_id="topic-1",
            )

        result = prepare_voice_reply_for_delivery(
            guild_id=123,
            transcript=transcript,
            voice_segment=segment,
            session_key="session-1",
            room_session_key="room-1",
            owner_user_id=None,
            active_speaker_user_id=10,
            metrics={"meta": {"tts_interrupted_by_user_audio": True}},
            session_topic_seed="",
            now_monotonic=1.0,
            should_reply_to_voice=should_reply_to_voice,
            register_drop_reason=lambda *args, **kwargs: None,
            log_voice_stage=lambda *args, **kwargs: None,
            log_voice_bottleneck_summary=lambda *args, **kwargs: None,
            reset_session_bad_audio=lambda _session_key: None,
            build_voice_reply_request=build_voice_reply_request,
            build_topic_id=lambda _seed: "topic-1",
            session_last_stt_text={},
            room_last_voice_reply_at={},
        )

        self.assertTrue(result.accepted)
        self.assertTrue(captured_kwargs["ignore_tts_suppression"])

    def test_validation_bound_reply_gate_redacts_transcript_observability(self) -> None:
        secret = "VOICE_PRIVACY_SENTINEL_REPLY_GATE_904a"
        transcript = TranscriptResult(
            wake_detected=True,
            wake_match_mode="exact",
            wake_alias="evelyn",
            probe_text="evelyn",
            confirm_text="evelyn",
            reject_reason=None,
            partial_text="",
            committed_text="",
            final_text=secret,
            speaker_user_id=10,
            duration_sec=0.6,
        )
        segment = VoiceSegment(
            guild_id=123,
            room_session_key="room-1",
            session_key="session-1",
            speaker_user_id=10,
            speaker_name="tester",
            audio16k=np.zeros(1600, dtype=np.float32),
            sampling_rate=16000,
            duration_sec=0.6,
            segment_id=1,
            owner_user_id=None,
        )
        metrics = {
            "meta": {
                "validation_session_id": "validation-private",
                "validation_step_id": "02-listening",
                "validation_attempt_id": "attempt-private",
            }
        }
        stage_events: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        gate_texts: list[str] = []

        def build_reply(**kwargs: Any) -> VoiceReplyRequest:
            return VoiceReplyRequest(
                transcript=kwargs["transcript"],
                segment=kwargs["segment"],
                gate_mode=kwargs["gate_mode"],
                raw_user_text=secret,
                prompt_user_text=secret,
                history_user_text=secret,
                wake_only_turn=False,
                turn_type="normal",
                selected_path="main",
                reply_source="voice",
                topic_id="topic-1",
            )

        result = prepare_voice_reply_for_delivery(
            guild_id=123,
            transcript=transcript,
            voice_segment=segment,
            session_key="session-1",
            room_session_key="room-1",
            owner_user_id=None,
            active_speaker_user_id=10,
            metrics=metrics,
            session_topic_seed="",
            now_monotonic=1.0,
            should_reply_to_voice=lambda _guild, text, **_kwargs: (
                gate_texts.append(text) or (True, "ok", "wake_entry")
            ),
            register_drop_reason=lambda *_args, **_kwargs: None,
            log_voice_stage=lambda *args, **kwargs: stage_events.append((args, kwargs)),
            log_voice_bottleneck_summary=lambda *_args, **_kwargs: None,
            reset_session_bad_audio=lambda _key: None,
            build_voice_reply_request=build_reply,
            build_topic_id=lambda _seed: "topic-1",
            session_last_stt_text={},
            room_last_voice_reply_at={},
        )

        self.assertTrue(result.accepted)
        self.assertEqual(gate_texts, [secret])
        rendered = repr(stage_events)
        self.assertNotIn(secret, rendered)
        self.assertIn("<validation-text chars=", rendered)

    def test_validation_bound_reply_drop_reason_receives_redacted_text(self) -> None:
        secret = "VOICE_PRIVACY_SENTINEL_REPLY_DROP_58b1"
        transcript = TranscriptResult(
            wake_detected=False,
            wake_match_mode="rejected",
            wake_alias=None,
            probe_text="",
            confirm_text="",
            reject_reason="wake_miss",
            partial_text="",
            committed_text="",
            final_text=secret,
            speaker_user_id=10,
            duration_sec=0.6,
        )
        segment = VoiceSegment(
            guild_id=123,
            room_session_key="room-1",
            session_key="session-1",
            speaker_user_id=10,
            speaker_name="tester",
            audio16k=np.zeros(1600, dtype=np.float32),
            sampling_rate=16000,
            duration_sec=0.6,
            segment_id=1,
            owner_user_id=None,
        )
        drops: list[dict[str, Any]] = []

        result = prepare_voice_reply_for_delivery(
            guild_id=123,
            transcript=transcript,
            voice_segment=segment,
            session_key="session-1",
            room_session_key="room-1",
            owner_user_id=None,
            active_speaker_user_id=10,
            metrics={"meta": {"validation_attempt_id": "attempt-private"}},
            session_topic_seed="",
            now_monotonic=1.0,
            should_reply_to_voice=lambda *_args, **_kwargs: (False, "wake_miss", "wake"),
            register_drop_reason=lambda *_args, **kwargs: drops.append(kwargs),
            log_voice_stage=lambda *_args, **_kwargs: None,
            log_voice_bottleneck_summary=lambda *_args, **_kwargs: None,
            reset_session_bad_audio=lambda _key: None,
            build_voice_reply_request=lambda **_kwargs: self.fail("reply must remain blocked"),
            build_topic_id=lambda _seed: "topic-1",
            session_last_stt_text={},
            room_last_voice_reply_at={},
        )

        self.assertFalse(result.accepted)
        self.assertNotIn(secret, repr(drops))
        self.assertIn("<validation-text chars=", drops[0]["text"])

    async def test_policy_no_main_llm_delivers_preface_without_main_llm(self) -> None:
        events: list[Any] = []
        first_chunk_calls = 0
        spoken_chunks: list[str] = []

        def on_first_chunk() -> None:
            nonlocal first_chunk_calls
            first_chunk_calls += 1

        async def on_sentence(chunk: str) -> None:
            spoken_chunks.append(chunk)

        orchestrator = self.make_orchestrator(
            route_decision_kwargs={
                "user_visible_preface": "policy answer",
                "needs_main_llm": False,
                "needs_tts": True,
            },
            events=events,
        )

        result = await orchestrator.execute(
            VoiceTurnRequest(user_text="fast", on_first_chunk=on_first_chunk, on_sentence=on_sentence)
        )

        self.assertEqual(result.answer_text, "policy answer")
        self.assertEqual(result.handled_by, "policy_no_main_llm")
        self.assertEqual(first_chunk_calls, 1)
        self.assertEqual(spoken_chunks, ["policy answer"])
        self.assertNotIn("main_llm", [event[0] for event in events])

    async def test_policy_no_main_llm_without_preface_falls_through_to_main_llm(self) -> None:
        events: list[Any] = []
        orchestrator = self.make_orchestrator(
            route_decision_kwargs={
                "needs_main_llm": False,
            },
            events=events,
        )

        result = await orchestrator.execute(VoiceTurnRequest(user_text="hello"))

        self.assertEqual(result.answer_text, "main answer")
        self.assertEqual(result.handled_by, "main_llm")
        self.assertIn("main_llm", [event[0] for event in events])
        self.assertNotIn("policy_no_main_llm", result.handled_by)

    async def test_user_echo_short_circuit_falls_through_to_main_llm(self) -> None:
        events: list[Any] = []
        orchestrator = self.make_orchestrator(short_circuit_answer="hello", events=events)

        result = await orchestrator.execute(VoiceTurnRequest(user_text="hello"))

        self.assertEqual(result.answer_text, "main answer")
        self.assertEqual(result.handled_by, "main_llm")
        self.assertEqual([event[0] for event in events], ["prepare_route_context", "short_circuit", "skill_route", "main_llm"])

    async def test_user_echo_skill_route_falls_through_to_main_llm(self) -> None:
        events: list[Any] = []
        orchestrator = self.make_orchestrator(skill_route_answer="hello", events=events)

        result = await orchestrator.execute(VoiceTurnRequest(user_text="hello"))

        self.assertEqual(result.answer_text, "main answer")
        self.assertEqual(result.handled_by, "main_llm")
        self.assertIn("main_llm", [event[0] for event in events])

    async def test_skill_route_respects_needs_tts_false(self) -> None:
        events: list[Any] = []
        spoken_chunks: list[str] = []

        async def on_sentence(chunk: str) -> None:
            spoken_chunks.append(chunk)

        orchestrator = self.make_orchestrator(
            skill_route_answer="silent skill answer",
            route_decision_kwargs={"needs_tts": False},
            events=events,
        )

        result = await orchestrator.execute(
            VoiceTurnRequest(user_text="run route", on_sentence=on_sentence)
        )

        self.assertEqual(result.answer_text, "silent skill answer")
        self.assertEqual(result.handled_by, "skill_route")
        self.assertEqual(spoken_chunks, [])
        delivery_event = [event for event in events if event[0] == "delivery"][0]
        self.assertEqual(delivery_event[1].should_play_voice, False)

    async def test_policy_no_main_llm_respects_needs_tts_false(self) -> None:
        events: list[Any] = []
        spoken_chunks: list[str] = []

        async def on_sentence(chunk: str) -> None:
            spoken_chunks.append(chunk)

        orchestrator = self.make_orchestrator(
            route_decision_kwargs={
                "user_visible_preface": "silent policy answer",
                "needs_main_llm": False,
                "needs_tts": False,
            },
            events=events,
        )

        result = await orchestrator.execute(
            VoiceTurnRequest(user_text="fast", on_sentence=on_sentence)
        )

        self.assertEqual(result.answer_text, "silent policy answer")
        self.assertEqual(result.handled_by, "policy_no_main_llm")
        self.assertEqual(spoken_chunks, [])
        delivery_event = [event for event in events if event[0] == "delivery"][0]
        self.assertEqual(delivery_event[1].should_play_voice, False)


if __name__ == "__main__":
    unittest.main()
