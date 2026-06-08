import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_orchestration import (  # noqa: E402
    VoiceTurnOrchestrator,
    VoiceTurnOrchestratorDeps,
    VoiceTurnRequest,
)
from evelyn_core.voice_pipeline import (  # noqa: E402
    DeliveryPlan,
    build_answer_payload_from_text,
    build_delivery_plan,
    build_route_decision,
)


def split_test_tts_chunks(text: str, *, force: bool = False) -> tuple[list[str], str]:
    return [text] if text else [], ""


class VoiceTurnOrchestratorTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIn("route exploded", metrics["meta"]["error"])

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
