from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .conversation_archive_process_purge import (
    ConversationArchiveProcessPurgeFence,
    ConversationArchiveProcessPurgeRunner,
    conversation_archive_process_target_values,
    purge_exact_process_caches,
)
from .memory_confirmation_contract import memory_owner_scope


CountTuple = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ConversationArchiveProcessCompositionDeps:
    room_turn_scopes: MutableMapping[str, Any]
    background_memory_vault_tasks: Mapping[int, asyncio.Task]
    autonomy_cognitive_refresh_tasks: Mapping[int, asyncio.Task]
    session_continuity_checkpoint: Any
    conversation_ingress_composition: Any
    search_followup_recovery: Any
    reset_persona_state_for_deletion: Callable[..., CountTuple]
    autonomy_authorization_manager: Any
    autonomy_engines: Mapping[int, Any]
    cleanup_identity_review_artifacts: Callable[..., CountTuple]
    purge_feedback_targets: Callable[
        [Callable[[dict[str, Any]], bool]], CountTuple
    ]
    identity_review_export_dir: Path
    runtime_artifacts_root: Path
    session_speculative_policies: MutableMapping[str, Any]
    session_question_state: MutableMapping[str, Any]
    recent_skill_dispatches: MutableMapping[str, Any]
    recent_skill_dispatch_targets: MutableMapping[str, dict[str, Any]]
    cleanup_voice_ingress_targets: Callable[..., Awaitable[tuple[int, int]]]
    build_voice_ingress_runtime_deps: Callable[[], Any]
    partial_stt_cache: MutableMapping[str, Any]
    room_last_voice_utterance_for_merge: MutableMapping[str, Any]
    cleanup_tts_playback_targets: Callable[..., Awaitable[tuple[int, int]]]
    tts_playback_tracker: Any
    local_tts_playback_manager: Any


class ConversationArchiveProcessComposition:
    """Own process-local archive fences, exact purge callbacks, and task lineage."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.process_tasks: dict[asyncio.Task, dict[str, Any]] = {}
        self.voice_ingress_process_tasks: dict[asyncio.Task, dict[str, Any]] = {}
        self._deps: ConversationArchiveProcessCompositionDeps | None = None
        self._fence: ConversationArchiveProcessPurgeFence | None = None
        self._runner: ConversationArchiveProcessPurgeRunner | None = None

    def configure(
        self,
        *,
        deps: ConversationArchiveProcessCompositionDeps,
        lineage_handle: Callable[[str, str], str] | None,
    ) -> None:
        self._deps = deps
        if lineage_handle is None:
            self._fence = None
            self._runner = None
            return
        self._fence = ConversationArchiveProcessPurgeFence(
            lineage_handle=lineage_handle
        )
        self._runner = ConversationArchiveProcessPurgeRunner(
            fence=self._fence,
            owners={
                "autonomy_state": self._purge_autonomy,
                "continuity_checkpoint": self._purge_continuity,
                "feedback_state": self._purge_feedback_and_exports,
                "ingress_journal": self._purge_ingress,
                "outbound_retry": self._purge_outbound,
                "persona_state": self._purge_persona,
                "prompt_tool_cache": self._purge_prompt_tool_cache,
                "registered_exports": self._purge_feedback_and_exports,
                "stt_buffer": self._purge_stt,
                "tts_buffer": self._purge_tts,
            },
        )

    @staticmethod
    def _target_lineage(
        *,
        guild_id: Any,
        turn_id: Any = None,
        session_key: Any = None,
        session_memory_key: Any = None,
        person_key: Any = None,
    ) -> dict[str, tuple[str, ...]]:
        lineage: dict[str, tuple[str, ...]] = {}
        normalized_turn = str(turn_id or "").strip()
        if normalized_turn:
            lineage["turn"] = (normalized_turn,)
            lineage["memory_evidence"] = (
                f"turn:{normalized_turn}:user",
                f"turn:{normalized_turn}:assistant",
            )
        sessions = tuple(
            dict.fromkeys(
                value
                for candidate in (session_key, session_memory_key)
                if (value := str(candidate or "").strip())
            )
        )
        if sessions:
            lineage["session"] = sessions
        normalized_person = str(person_key or "").strip()
        if normalized_person:
            try:
                owner_guild_id = (
                    None
                    if normalized_person == "control-page:local"
                    else int(guild_id)
                )
                lineage["memory_owner"] = (
                    memory_owner_scope(
                        guild_id=owner_guild_id,
                        person_key=normalized_person,
                    ),
                )
            except (TypeError, ValueError, OverflowError):
                return {}
        return lineage

    def target_is_current(
        self,
        *,
        guild_id: Any,
        turn_id: Any = None,
        session_key: Any = None,
        session_memory_key: Any = None,
        person_key: Any = None,
    ) -> bool:
        if not self.enabled:
            return True
        lineage = self._target_lineage(
            guild_id=guild_id,
            turn_id=turn_id,
            session_key=session_key,
            session_memory_key=session_memory_key,
            person_key=person_key,
        )
        return bool(
            self._fence is not None
            and lineage
            and self._fence.target_is_current(lineage)
        )

    def ingress_target_is_current(
        self,
        *,
        turn_id: Any,
        scope: Any,
        session_key: Any,
        surface: Any,
        source_delivery_id: Any,
    ) -> bool:
        del scope, surface, source_delivery_id
        return self.target_is_current(
            guild_id=0,
            turn_id=turn_id,
            session_key=session_key,
        )

    def search_followup_target_is_current(
        self,
        *,
        turn_id: Any,
        delivery_turn_id: Any,
        session_key: Any,
        session_memory_key: Any,
    ) -> bool:
        turn_ids = tuple(
            dict.fromkeys(
                value
                for candidate in (turn_id, delivery_turn_id)
                if (value := str(candidate or "").strip())
            )
        )
        if not turn_ids:
            return self.target_is_current(
                guild_id=0,
                session_key=session_key,
                session_memory_key=session_memory_key,
            )
        return all(
            self.target_is_current(
                guild_id=0,
                turn_id=current_turn_id,
                session_key=session_key,
                session_memory_key=session_memory_key,
            )
            for current_turn_id in turn_ids
        )

    def voice_target_is_current(self, item: dict[str, Any]) -> bool:
        member = item.get("member")
        guild_id = getattr(getattr(member, "guild", None), "id", None)
        person_key = item.get("person_key")
        if not person_key:
            user_id = getattr(member, "id", None)
            person_key = None if user_id is None else f"user:{int(user_id)}"
        return self.target_is_current(
            guild_id=guild_id,
            turn_id=item.get("turn_id"),
            session_key=item.get("session_key"),
            session_memory_key=item.get("session_memory_key"),
            person_key=person_key,
        )

    def tts_target_is_current(self, target: dict[str, Any]) -> bool:
        values = conversation_archive_process_target_values(target)
        return self.target_is_current(
            guild_id=values.get("guild_id", 0),
            turn_id=values.get("turn_id"),
            session_key=values.get("session_key"),
            session_memory_key=values.get("session_memory_key"),
            person_key=values.get("person_key"),
        )

    def _work_matches(
        self,
        work_order: dict[str, Any],
        kind: str,
        raw_value: Any,
    ) -> bool:
        value = str(raw_value or "").strip()
        return bool(
            self._fence is not None
            and value
            and self._fence.matches(work_order, {kind: (value,)})
        )

    def _work_matches_target(
        self,
        work_order: dict[str, Any],
        target: dict[str, Any],
    ) -> bool:
        values = conversation_archive_process_target_values(target)
        lineage = self._target_lineage(
            guild_id=values.get("guild_id"),
            turn_id=values.get("turn_id"),
            session_key=values.get("session_key"),
            session_memory_key=values.get("session_memory_key"),
            person_key=values.get("person_key"),
        )
        return bool(
            self._fence is not None
            and lineage
            and self._fence.matches(work_order, lineage)
        )

    @staticmethod
    def _work_time_matches(
        work_order: dict[str, Any],
        raw_timestamp: Any,
    ) -> bool:
        if work_order.get("scopeAll") is True:
            return True
        try:
            timestamp = float(raw_timestamp)
        except (TypeError, ValueError, OverflowError):
            return True
        if not math.isfinite(timestamp):
            return True

        def parse(value: Any) -> float | None:
            if value is None:
                return None
            try:
                return datetime.fromisoformat(
                    str(value).replace("Z", "+00:00")
                ).astimezone(timezone.utc).timestamp()
            except (TypeError, ValueError, OverflowError):
                return None

        started_at = parse(work_order.get("startedAt"))
        ended_at = parse(work_order.get("endedAt"))
        return bool(
            started_at is not None
            and ended_at is not None
            and started_at <= timestamp < ended_at
        )

    def _lineage_mapping_matches(
        self,
        work_order: dict[str, Any],
        lineage: dict[str, Any],
    ) -> bool:
        allowed = {
            "turn",
            "session",
            "memory_owner",
            "memory_note",
            "memory_evidence",
        }
        if not lineage or any(kind not in allowed for kind in lineage):
            raise ValueError("archive_process_purge_lineage_invalid")
        normalized: dict[str, tuple[str, ...]] = {}
        for kind, raw_values in lineage.items():
            values = (
                raw_values
                if isinstance(raw_values, (list, tuple))
                else (raw_values,)
            )
            cleaned = tuple(str(value or "").strip() for value in values)
            if not cleaned or any(not value for value in cleaned):
                raise ValueError("archive_process_purge_lineage_invalid")
            normalized[kind] = cleaned
        return bool(
            self._fence is not None
            and self._fence.matches(work_order, normalized)
        )

    @staticmethod
    def _count_tuple(value: Any) -> CountTuple:
        if isinstance(value, dict):
            values = (
                value.get("removedCount"),
                value.get("remainingCopies"),
                value.get("manualReviewCount"),
            )
        else:
            values = tuple(value) if isinstance(value, tuple) else ()
        if len(values) != 3 or any(
            type(item) is not int or item < 0 for item in values
        ):
            return (0, 1, 1)
        return values  # type: ignore[return-value]

    @staticmethod
    def _sum_counts(*values: CountTuple) -> CountTuple:
        return tuple(  # type: ignore[return-value]
            sum(value[index] for value in values) for index in range(3)
        )

    async def _cancel_target_work(
        self,
        work_order: dict[str, Any],
        *,
        timeout_sec: float = 2.0,
    ) -> CountTuple:
        deps = self._deps
        if deps is None:
            return (0, 1, 1)
        tasks: set[asyncio.Task] = set()
        for registry in (self.process_tasks, self.voice_ingress_process_tasks):
            tasks.update(
                task
                for task, target in tuple(registry.items())
                if self._work_matches_target(work_order, target)
            )
        for room_key, scope in tuple(deps.room_turn_scopes.items()):
            if not self._work_matches(
                work_order, "turn", getattr(scope, "turn_id", None)
            ):
                continue
            tasks.update(getattr(scope, "tasks", ()))
            scope.cancel(reason="conversation_archive_privacy_purge")
            if deps.room_turn_scopes.get(room_key) is scope:
                deps.room_turn_scopes.pop(room_key, None)

        guild_id = work_order.get("guildId")
        selected_guild_ids = None if guild_id is None else {int(guild_id)}
        for registry in (
            deps.background_memory_vault_tasks,
            deps.autonomy_cognitive_refresh_tasks,
        ):
            tasks.update(
                task
                for key, task in tuple(registry.items())
                if selected_guild_ids is None or int(key) in selected_guild_ids
            )

        current = asyncio.current_task()
        if current in tasks:
            return (0, 1, 1)
        tasks = {task for task in tasks if isinstance(task, asyncio.Task)}
        for task in tasks:
            if not task.done():
                task.cancel()
        if not tasks:
            return (0, 0, 0)
        done, pending = await asyncio.wait(
            tasks,
            timeout=max(0.0, float(timeout_sec)),
        )
        if done:
            await asyncio.gather(*done, return_exceptions=True)
        return (len(done), len(pending), 0)

    def _exact_selectors(
        self,
        work_order: dict[str, Any],
    ) -> tuple[Callable[[str], bool], Callable[[str], bool], bool]:
        return (
            lambda value: self._work_matches(work_order, "turn", value),
            lambda value: self._work_matches(work_order, "session", value),
            work_order.get("scopeAll") is True,
        )

    def _purge_continuity(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None
        match_turn, match_session, full_user_delete = self._exact_selectors(
            work_order
        )
        return self._count_tuple(
            self._deps.session_continuity_checkpoint.purge_exact_lineage(
                match_turn=match_turn,
                match_session=match_session,
                full_user_delete=full_user_delete,
            )
        )

    def _purge_ingress(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None
        match_turn, match_session, full_user_delete = self._exact_selectors(
            work_order
        )
        return self._count_tuple(
            self._deps.conversation_ingress_composition.purge_exact_lineage(
                match_turn=match_turn,
                match_session=match_session,
                full_user_delete=full_user_delete,
            )
        )

    def _purge_outbound(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None
        match_turn, match_session, full_user_delete = self._exact_selectors(
            work_order
        )
        return self._count_tuple(
            self._deps.search_followup_recovery.purge_exact_lineage(
                match_turn=match_turn,
                match_session=match_session,
                full_user_delete=full_user_delete,
            )
        )

    async def _purge_persona(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None
        stopped = await self._cancel_target_work(work_order)
        if stopped[1] or stopped[2]:
            return stopped
        reset = self._deps.reset_persona_state_for_deletion(has_targets=True)
        return self._sum_counts(stopped, reset)

    async def _purge_autonomy(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None

        def authorization_target(row: dict[str, Any]) -> bool | None:
            timestamp = row.get("issuedAt", row.get("at"))
            if not self._work_time_matches(work_order, timestamp):
                return False
            issuer = str(row.get("issuerRef") or "").strip()
            row_guild = row.get("guildId")
            if issuer.startswith("discord_user:") and issuer[13:].isdecimal():
                try:
                    owner_scope = memory_owner_scope(
                        guild_id=int(row_guild),
                        person_key=f"user:{int(issuer[13:])}",
                    )
                except (TypeError, ValueError, OverflowError):
                    return None
                return self._work_matches(
                    work_order, "memory_owner", owner_scope
                )
            if issuer == "control-page:local":
                owner_scope = memory_owner_scope(
                    guild_id=None,
                    person_key="control-page:local",
                )
                return self._work_matches(
                    work_order, "memory_owner", owner_scope
                )
            if row_guild is None:
                return False
            target_guild = work_order.get("guildId")
            return (
                None
                if target_guild is None or str(row_guild) == str(target_guild)
                else False
            )

        authorization = (
            self._deps.autonomy_authorization_manager.cleanup_exact_targets(
                authorization_target
            )
        )
        removed, remaining, manual = authorization
        guild_id = work_order.get("guildId")
        engines = tuple(
            engine
            for key, engine in tuple(self._deps.autonomy_engines.items())
            if guild_id is None or int(key) == int(guild_id)
        )
        for engine in engines:
            result = await engine.cleanup_history_state(timeout_sec=2.0)
            removed += result[0]
            remaining += result[1]
            manual += result[2]
        return (removed, remaining, manual)

    def _purge_feedback_and_exports(
        self,
        work_order: dict[str, Any],
    ) -> CountTuple:
        assert self._deps is not None
        identity_artifacts = self._deps.cleanup_identity_review_artifacts(
            time_predicate=lambda row: self._work_time_matches(
                work_order, row.get("recorded_at")
            ),
            lineage_predicate=lambda lineage: self._lineage_mapping_matches(
                work_order, lineage
            ),
            registered_export_dirs=(self._deps.identity_review_export_dir,),
            allowed_export_root=self._deps.runtime_artifacts_root,
        )
        feedback_targets = self._deps.purge_feedback_targets(
            lambda target: self._work_matches_target(work_order, target)
        )
        return self._sum_counts(
            self._count_tuple(identity_artifacts),
            self._count_tuple(feedback_targets),
        )

    def _purge_prompt_tool_cache(
        self,
        work_order: dict[str, Any],
    ) -> CountTuple:
        assert self._deps is not None
        return purge_exact_process_caches(
            session_caches=(
                self._deps.session_speculative_policies,
                self._deps.session_question_state,
            ),
            targeted_cache=self._deps.recent_skill_dispatches,
            target_metadata=self._deps.recent_skill_dispatch_targets,
            session_matches=lambda session_key: self._work_matches(
                work_order, "session", session_key
            ),
            target_matches=lambda target: self._work_matches_target(
                work_order, target
            ),
            unattributed_session_keys=("global",),
        )

    async def _purge_stt(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None
        predicate = lambda item: self._work_matches_target(work_order, item)
        removed, remaining = await self._deps.cleanup_voice_ingress_targets(
            predicate,
            deps=self._deps.build_voice_ingress_runtime_deps(),
            timeout_sec=2.0,
        )
        for key in tuple(self._deps.partial_stt_cache):
            if self._work_matches(work_order, "session", key):
                self._deps.partial_stt_cache.pop(key, None)
                removed += 1
        merge_records = self._deps.room_last_voice_utterance_for_merge
        for key, record in tuple(merge_records.items()):
            if self._work_matches(
                work_order, "turn", getattr(record, "turn_id", None)
            ) or self._work_matches(
                work_order, "session", getattr(record, "session_key", None)
            ):
                merge_records.pop(key, None)
                removed += 1
        remaining += sum(
            self._work_matches(work_order, "session", key)
            for key in self._deps.partial_stt_cache
        )
        remaining += sum(
            self._work_matches(
                work_order, "turn", getattr(record, "turn_id", None)
            )
            or self._work_matches(
                work_order, "session", getattr(record, "session_key", None)
            )
            for record in merge_records.values()
        )
        return (removed, remaining, 0)

    async def _purge_tts(self, work_order: dict[str, Any]) -> CountTuple:
        assert self._deps is not None
        predicate = lambda target: self._work_matches_target(work_order, target)
        discord_removed, discord_remaining = (
            await self._deps.cleanup_tts_playback_targets(
                predicate,
                tracker=self._deps.tts_playback_tracker,
                cleanup_timeout_sec=2.0,
            )
        )
        local_removed, local_remaining = (
            await self._deps.local_tts_playback_manager.cleanup_matching_source(
                predicate,
                timeout_sec=2.0,
            )
        )
        return (
            discord_removed + local_removed,
            discord_remaining + local_remaining,
            0,
        )

    async def purge(self, work_order: dict[str, Any]) -> tuple[str, ...]:
        if self._runner is None or self._fence is None:
            return ()
        try:
            self._fence.freeze(work_order)
        except Exception:
            return ()
        stopped = await self._cancel_target_work(work_order)
        if stopped[1] or stopped[2]:
            return ()
        return await self._runner.purge(work_order)

    def complete(self, work_order: dict[str, Any]) -> None:
        if self._runner is None:
            raise RuntimeError("conversation_archive_process_purge_unavailable")
        self._runner.release_completed(work_order)


__all__ = [
    "ConversationArchiveProcessComposition",
    "ConversationArchiveProcessCompositionDeps",
]
