from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_orchestration import (  # noqa: E402
    VoiceTurnOrchestrator,
    VoiceTurnOrchestratorDeps,
    VoiceTurnRequest,
)
from evelyn_core.turn_lifecycle import TurnScope  # noqa: E402
from evelyn_core.voice_pipeline import RouteDecision  # noqa: E402


class VoiceTurnCancellationSeamTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _orchestrator(
        *,
        cancel_at: str | None,
        scope: TurnScope,
        events: list[str],
    ) -> VoiceTurnOrchestrator:
        decision = RouteDecision(
            action="answer",
            route="main_direct",
            source="voice",
            prompt_text="계속해 줘",
        )

        async def prepare_route_context(
            *_args: Any,
            **_kwargs: Any,
        ) -> tuple[list[dict[str, str]], dict[str, str], RouteDecision, None, bool]:
            events.append("route_context")
            if cancel_at == "route_context":
                scope.cancel()
            return (
                [{"role": "system", "content": "base"}],
                {"action": "answer"},
                decision,
                None,
                False,
            )

        async def short_circuit(**_kwargs: Any) -> tuple[str | None, None]:
            events.append("short_circuit")
            if cancel_at == "short_circuit":
                scope.cancel()
                return "stale short reply", None
            return None, None

        async def registered_route(**_kwargs: Any) -> str | None:
            events.append("registered_route")
            if cancel_at == "registered_route":
                scope.cancel()
                return "stale route evidence"
            return None

        async def main_llm(**_kwargs: Any) -> str:
            events.append("main_llm")
            if cancel_at == "main_llm":
                scope.cancel()
            return "current reply"

        async def unexpected_delivery(*_args: Any, **_kwargs: Any) -> None:
            events.append("delivery")

        return VoiceTurnOrchestrator(
            VoiceTurnOrchestratorDeps(
                prepare_route_context=prepare_route_context,
                maybe_handle_short_circuit_route=short_circuit,
                maybe_execute_registered_route=registered_route,
                run_main_llm_turn=main_llm,
                emit_delivery_plan_chunks=unexpected_delivery,
                build_answer_payload_from_text=lambda text, **_kwargs: text,
                build_delivery_plan=lambda *_args, **_kwargs: None,
                split_tts_sentences=lambda text: [text],
            )
        )

    async def test_cancelled_owner_stops_at_each_awaited_boundary(self) -> None:
        expected_events = {
            "route_context": ["route_context"],
            "short_circuit": ["route_context", "short_circuit"],
            "registered_route": [
                "route_context",
                "short_circuit",
                "registered_route",
            ],
            "main_llm": [
                "route_context",
                "short_circuit",
                "registered_route",
                "main_llm",
            ],
        }
        for boundary, expected in expected_events.items():
            with self.subTest(boundary=boundary):
                scope = TurnScope(turn_id=f"turn:{boundary}")
                events: list[str] = []
                orchestrator = self._orchestrator(
                    cancel_at=boundary,
                    scope=scope,
                    events=events,
                )

                with self.assertRaises(asyncio.CancelledError):
                    await orchestrator.execute(
                        VoiceTurnRequest(
                            user_text="계속해 줘",
                            source="voice",
                            turn_scope=scope,
                        )
                    )

                self.assertEqual(events, expected)

    async def test_current_owner_still_completes_normally(self) -> None:
        scope = TurnScope(turn_id="turn:current")
        events: list[str] = []
        result = await self._orchestrator(
            cancel_at=None,
            scope=scope,
            events=events,
        ).execute(
            VoiceTurnRequest(
                user_text="계속해 줘",
                source="voice",
                turn_scope=scope,
            )
        )

        self.assertEqual(result.answer_text, "current reply")
        self.assertEqual(result.handled_by, "main_llm")
        self.assertEqual(
            events,
            [
                "route_context",
                "short_circuit",
                "registered_route",
                "main_llm",
            ],
        )


if __name__ == "__main__":
    unittest.main()
