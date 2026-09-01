from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, MutableMapping

from .cognitive_state_runtime import update_cognitive_state_from_runtime


@dataclass(frozen=True)
class CognitiveRefreshCompositionDeps:
    state: Callable[[], Any]
    background_tasks: dict[str, Any]
    runtime_session_key: Callable[..., str | None]
    create_scoped_task: Callable[..., Any]
    current_turn_id: Callable[[str | None], str | None]
    monotonic: Callable[[], float]
    current_task: Callable[[], Any]
    log_turn_event: Callable[..., Any]
    log: Callable[..., Any]
    archive_target_is_current: Callable[..., bool] | None = None
    archive_task_targets: MutableMapping[
        asyncio.Task, dict[str, Any]
    ] | None = None


class CognitiveRefreshComposition:
    """Owns cognitive refresh execution and per-session background tasks."""

    def __init__(self, deps: CognitiveRefreshCompositionDeps) -> None:
        self.deps = deps

    @staticmethod
    def _archive_target(
        *,
        guild_id: int,
        turn_scope: Any,
        session_key: str | None,
        session_memory_key: str | None,
        person_key: str | None,
    ) -> dict[str, Any]:
        return {
            "guild_id": int(guild_id),
            "turn_id": getattr(turn_scope, "turn_id", None),
            "session_key": session_key,
            "session_memory_key": session_memory_key,
            "person_key": person_key,
        }

    def _archive_target_current(self, target: dict[str, Any]) -> bool:
        callback = getattr(self.deps, "archive_target_is_current", None)
        if callback is None:
            return True
        try:
            return callback(**target) is True
        except Exception:
            return False

    def _track_archive_task(
        self,
        task: asyncio.Task,
        target: dict[str, Any],
    ) -> None:
        registry = getattr(self.deps, "archive_task_targets", None)
        if registry is None:
            return
        registry[task] = dict(target)

        def release(completed: asyncio.Task) -> None:
            if registry.get(completed) == target:
                registry.pop(completed, None)

        task.add_done_callback(release)

    async def update_cognitive_state(
        self,
        guild_id: int,
        user_text: str,
        *,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        turn_scope: Any | None = None,
    ) -> dict:
        return await update_cognitive_state_from_runtime(
            guild_id,
            user_text,
            deps=self.deps.state(),
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            turn_scope=turn_scope,
        )

    async def refresh_cognitive_state_in_background(
        self,
        guild_id: int,
        user_text: str,
        *,
        reason: str,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        turn_scope: Any | None = None,
    ) -> None:
        deps = self.deps
        archive_target = self._archive_target(
            guild_id=guild_id,
            turn_scope=turn_scope,
            session_key=session_key,
            session_memory_key=session_memory_key,
            person_key=person_key,
        )
        task_key = session_memory_key or deps.runtime_session_key(guild_id=guild_id)
        started_at = deps.monotonic()
        try:
            if not self._archive_target_current(archive_target):
                return
            await self.update_cognitive_state(
                guild_id,
                user_text,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source=source,
                turn_scope=turn_scope,
            )
            deps.log_turn_event(
                "cognitive_background_done",
                session_key=session_key,
                turn_id=deps.current_turn_id(session_key),
                cognitive_background_ms=round((deps.monotonic() - started_at) * 1000.0, 1),
                reason=reason,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            deps.log(
                f"[COGNITIVE] background refresh failed errorType={type(exc).__name__}"
            )
        finally:
            task = deps.background_tasks.get(task_key)
            if task is deps.current_task():
                deps.background_tasks.pop(task_key, None)

    def schedule_cognitive_refresh(
        self,
        guild_id: int | None,
        user_text: str,
        *,
        reason: str,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        turn_scope: Any | None = None,
    ) -> None:
        if guild_id is None:
            return
        deps = self.deps
        task_key = session_memory_key or deps.runtime_session_key(guild_id=guild_id)
        if task_key is None:
            return
        archive_target = self._archive_target(
            guild_id=guild_id,
            turn_scope=turn_scope,
            session_key=session_key,
            session_memory_key=session_memory_key,
            person_key=person_key,
        )
        if not self._archive_target_current(archive_target):
            return
        existing = deps.background_tasks.get(task_key)
        if existing is not None and not existing.done():
            existing.cancel()
        task = deps.create_scoped_task(
            self.refresh_cognitive_state_in_background(
                guild_id,
                user_text,
                reason=reason,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source=source,
                turn_scope=turn_scope,
            ),
            turn_scope=turn_scope,
        )
        deps.background_tasks[task_key] = task
        self._track_archive_task(task, archive_target)
