from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .guild_runtime_reset import (
    GuildRuntimeResetDeps,
    build_guild_runtime_reset_deps as build_guild_runtime_reset_deps_from_runtime,
    reset_guild_runtime_state_from_runtime,
)


@dataclass(frozen=True)
class GuildRuntimeResetCompositionDeps:
    session_histories: MutableMapping[str, Any]
    session_followup_targets: MutableMapping[str, Any]
    active_session_until: MutableMapping[str, Any]
    active_session_user_ids: MutableMapping[str, Any]
    session_last_active_at: MutableMapping[str, Any]
    session_awaiting_user_reply: MutableMapping[str, Any]
    session_last_speaker: MutableMapping[str, Any]
    session_topic_ids: MutableMapping[str, Any]
    session_turn_ids: MutableMapping[str, Any]
    session_segment_counters: MutableMapping[str, Any]
    session_last_turn_accepted_at: MutableMapping[str, Any]
    session_last_stt_text: MutableMapping[str, Any]
    room_last_voice_utterance_for_merge: MutableMapping[str, Any]
    session_partial_stt_text: MutableMapping[str, Any]
    session_committed_stt_text: MutableMapping[str, Any]
    session_bad_audio_counts: MutableMapping[str, Any]
    room_owner_user_ids: MutableMapping[str, Any]
    room_owner_until: MutableMapping[str, Any]
    room_reply_in_progress: MutableMapping[str, Any]
    room_last_voice_reply_at: MutableMapping[str, Any]
    turn_scope_registry: Any
    session_locks: MutableMapping[str, Any]
    background_search_tasks: MutableMapping[str, Any]
    clear_tts_playback_tracking: Callable[..., Any]
    tts_playback_tracker: Any
    memory_locks: MutableMapping[int, Any]
    cognitive_locks: MutableMapping[int, Any]
    background_cognitive_tasks: MutableMapping[str, Any]
    autonomy_last_cognitive_refresh_at: MutableMapping[int, Any]
    autonomy_cognitive_refresh_tasks: MutableMapping[int, Any]


class GuildRuntimeResetComposition:
    """Owns the mutable state boundary cleared by a guild reset."""

    def __init__(self, deps: GuildRuntimeResetCompositionDeps) -> None:
        self.deps = deps

    def build_guild_runtime_reset_deps(self) -> GuildRuntimeResetDeps:
        deps = self.deps
        return build_guild_runtime_reset_deps_from_runtime(
            session_histories=deps.session_histories,
            session_followup_targets=deps.session_followup_targets,
            active_session_until=deps.active_session_until,
            active_session_user_ids=deps.active_session_user_ids,
            session_last_active_at=deps.session_last_active_at,
            session_awaiting_user_reply=deps.session_awaiting_user_reply,
            session_last_speaker=deps.session_last_speaker,
            session_topic_ids=deps.session_topic_ids,
            session_turn_ids=deps.session_turn_ids,
            session_segment_counters=deps.session_segment_counters,
            session_last_turn_accepted_at=deps.session_last_turn_accepted_at,
            session_last_stt_text=deps.session_last_stt_text,
            room_last_voice_utterance_for_merge=deps.room_last_voice_utterance_for_merge,
            session_partial_stt_text=deps.session_partial_stt_text,
            session_committed_stt_text=deps.session_committed_stt_text,
            session_bad_audio_counts=deps.session_bad_audio_counts,
            room_owner_user_ids=deps.room_owner_user_ids,
            room_owner_until=deps.room_owner_until,
            room_reply_in_progress=deps.room_reply_in_progress,
            room_last_voice_reply_at=deps.room_last_voice_reply_at,
            turn_scope_registry=deps.turn_scope_registry,
            session_locks=deps.session_locks,
            background_search_tasks=deps.background_search_tasks,
            clear_tts_playback_tracking=deps.clear_tts_playback_tracking,
            tts_playback_tracker=deps.tts_playback_tracker,
            memory_locks=deps.memory_locks,
            cognitive_locks=deps.cognitive_locks,
            background_cognitive_tasks=deps.background_cognitive_tasks,
            autonomy_last_cognitive_refresh_at=deps.autonomy_last_cognitive_refresh_at,
            autonomy_cognitive_refresh_tasks=deps.autonomy_cognitive_refresh_tasks,
        )

    def reset_guild_runtime_state(self, guild_id: int) -> None:
        reset_guild_runtime_state_from_runtime(
            guild_id,
            deps=self.build_guild_runtime_reset_deps(),
        )


__all__ = ["GuildRuntimeResetComposition", "GuildRuntimeResetCompositionDeps"]
