from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TurnState(str, Enum):
    CREATED = "created"
    RECEIVING_AUDIO = "receiving_audio"
    STT_RUNNING = "stt_running"
    ROUTING = "routing"
    CONTEXT_ASSEMBLING = "context_assembling"
    LLM_RUNNING = "llm_running"
    TTS_RUNNING = "tts_running"
    PLAYING = "playing"
    CANCELLED = "cancelled"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class TurnTransition:
    previous_state: TurnState
    state: TurnState
    reason: str | None
    at_monotonic: float


@dataclass
class TurnScope:
    turn_id: str
    cancelled: bool = False
    tasks: set[asyncio.Task] = field(default_factory=set)
    state: TurnState = TurnState.CREATED
    transition_log: list[TurnTransition] = field(default_factory=list)
    cancel_reason: str | None = None

    def transition(self, state: TurnState | str, *, reason: str | None = None) -> TurnTransition:
        next_state = TurnState(state)
        transition = TurnTransition(
            previous_state=self.state,
            state=next_state,
            reason=reason,
            at_monotonic=time.monotonic(),
        )
        self.state = next_state
        self.transition_log.append(transition)
        return transition

    def cancel(self, reason: str | None = None) -> None:
        self.cancelled = True
        self.cancel_reason = reason or self.cancel_reason or "cancelled"
        if self.state is not TurnState.CANCELLED:
            self.transition(TurnState.CANCELLED, reason=self.cancel_reason)
        for task in list(self.tasks):
            if task is not None and not task.done():
                task.cancel()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError()

    def is_current(self, expected_turn_id: str | None) -> bool:
        return bool(expected_turn_id) and self.turn_id == expected_turn_id and not self.cancelled

    def is_stale(self, expected_turn_id: str | None) -> bool:
        return not self.is_current(expected_turn_id)

    def register_task(self, task: asyncio.Task | None = None) -> asyncio.Task | None:
        task = task or asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        return task

    def unregister_task(self, task: asyncio.Task | None = None) -> None:
        task = task or asyncio.current_task()
        if task is not None:
            self.tasks.discard(task)

    def snapshot(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "cancelled": self.cancelled,
            "cancel_reason": self.cancel_reason,
            "state": self.state.value,
            "task_count": len(self.tasks),
            "transitions": [
                {
                    "previous_state": transition.previous_state.value,
                    "state": transition.state.value,
                    "reason": transition.reason,
                    "at_monotonic": transition.at_monotonic,
                }
                for transition in self.transition_log
            ],
        }
