from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable


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
            if self.cancelled:
                task.cancel()
            else:
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


@dataclass(slots=True)
class TurnScopeRegistry:
    room_turn_scopes: dict[str, TurnScope] = field(default_factory=dict)
    cancelled_stale_turn_count: int = 0

    def replace_room_scope(self, room_id: str, new_scope: TurnScope, *, cancel_old: bool = True) -> TurnScope | None:
        old = self.room_turn_scopes.get(room_id)
        self.room_turn_scopes[room_id] = new_scope
        if cancel_old and old is not None and old is not new_scope:
            old.cancel(reason="replaced_by_new_turn")
            self.cancelled_stale_turn_count += 1
        return old

    def get_room_scope(self, room_id: str | None) -> TurnScope | None:
        if not room_id:
            return None
        return self.room_turn_scopes.get(room_id)

    def attach_current_task(self, turn_scope: TurnScope | None) -> asyncio.Task | None:
        if turn_scope is None:
            return None
        turn_scope.raise_if_cancelled()
        return turn_scope.register_task(asyncio.current_task())

    def detach_task(self, turn_scope: TurnScope | None, task: asyncio.Task | None) -> None:
        if turn_scope is None:
            return
        turn_scope.unregister_task(task)

    def create_scoped_task(self, coro: Awaitable[Any], turn_scope: TurnScope | None = None) -> asyncio.Task:
        task = asyncio.create_task(coro)
        if turn_scope is not None:
            turn_scope.register_task(task)
            task.add_done_callback(lambda done, scope=turn_scope: scope.unregister_task(done))
        return task

    def clear_room_scope(self, room_id: str | None, turn_scope: TurnScope | None = None) -> None:
        if not room_id:
            return
        current = self.room_turn_scopes.get(room_id)
        if current is None:
            return
        if turn_scope is not None and current is not turn_scope:
            return
        self.room_turn_scopes.pop(room_id, None)

    def cancel_matching_prefix(self, prefix: str) -> int:
        cancelled = 0
        for key, scope in list(self.room_turn_scopes.items()):
            if not key.startswith(prefix):
                continue
            if scope is not None:
                scope.cancel()
                cancelled += 1
            self.room_turn_scopes.pop(key, None)
        return cancelled


__all__ = [
    "TurnScope",
    "TurnScopeRegistry",
    "TurnState",
    "TurnTransition",
]
