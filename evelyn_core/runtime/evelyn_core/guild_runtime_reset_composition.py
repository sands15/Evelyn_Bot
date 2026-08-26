from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .guild_runtime_reset import (
    GuildRuntimeResetDeps,
    build_guild_runtime_reset_deps as build_guild_runtime_reset_deps_from_runtime,
    require_guild_runtime_reset_ready,
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
    partial_stt_cache: MutableMapping[str, Any]
    session_speculative_policies: MutableMapping[str, Any]
    session_bad_audio_counts: MutableMapping[str, Any]
    room_owner_user_ids: MutableMapping[str, Any]
    room_owner_until: MutableMapping[str, Any]
    room_reply_in_progress: MutableMapping[str, Any]
    room_last_voice_reply_at: MutableMapping[str, Any]
    voice_ingress_epochs: MutableMapping[int, int]
    turn_scope_registry: Any
    session_locks: MutableMapping[str, Any]
    background_search_tasks: MutableMapping[str, Any]
    clear_tts_playback_tracking: Callable[..., Any]
    tts_playback_tracker: Any
    memory_locks: MutableMapping[int, Any]
    background_memory_tasks: MutableMapping[str, Any]
    background_memory_vault_tasks: MutableMapping[int, Any]
    cognitive_locks: MutableMapping[int, Any]
    background_cognitive_tasks: MutableMapping[str, Any]
    autonomy_last_cognitive_refresh_at: MutableMapping[int, Any]
    autonomy_cognitive_refresh_tasks: MutableMapping[int, Any]
    autonomy_engines: MutableMapping[int, Any]
    reset_session_continuity_guild: Callable[[int, Callable[[], Any]], Any]
    reset_conversation_ingress_guild: Callable[..., Any]
    complete_conversation_ingress_guild_reset: Callable[[int], Any]


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
            partial_stt_cache=deps.partial_stt_cache,
            session_speculative_policies=deps.session_speculative_policies,
            session_bad_audio_counts=deps.session_bad_audio_counts,
            room_owner_user_ids=deps.room_owner_user_ids,
            room_owner_until=deps.room_owner_until,
            room_reply_in_progress=deps.room_reply_in_progress,
            room_last_voice_reply_at=deps.room_last_voice_reply_at,
            voice_ingress_epochs=deps.voice_ingress_epochs,
            turn_scope_registry=deps.turn_scope_registry,
            session_locks=deps.session_locks,
            background_search_tasks=deps.background_search_tasks,
            clear_tts_playback_tracking=deps.clear_tts_playback_tracking,
            tts_playback_tracker=deps.tts_playback_tracker,
            memory_locks=deps.memory_locks,
            background_memory_tasks=deps.background_memory_tasks,
            background_memory_vault_tasks=deps.background_memory_vault_tasks,
            cognitive_locks=deps.cognitive_locks,
            background_cognitive_tasks=deps.background_cognitive_tasks,
            autonomy_last_cognitive_refresh_at=deps.autonomy_last_cognitive_refresh_at,
            autonomy_cognitive_refresh_tasks=deps.autonomy_cognitive_refresh_tasks,
            autonomy_engines=deps.autonomy_engines,
        )

    def reset_guild_runtime_state(self, guild_id: int) -> None:
        runtime_deps = self.build_guild_runtime_reset_deps()
        require_guild_runtime_reset_ready(
            guild_id,
            deps=runtime_deps,
        )
        def reset_revoked_state() -> None:
            self.deps.reset_conversation_ingress_guild(
                guild_id,
                lambda: reset_guild_runtime_state_from_runtime(
                    guild_id,
                    deps=runtime_deps,
                ),
            )

        try:
            result = self.deps.reset_session_continuity_guild(
                guild_id,
                reset_revoked_state,
            )
        except Exception as exc:
            raise RuntimeError(
                "memory_guild_reset_durability_failed"
            ) from exc
        if isinstance(result, dict) and result.get("state") == "error":
            raise RuntimeError(
                "memory_guild_reset_durability_failed"
            )
        try:
            self.deps.complete_conversation_ingress_guild_reset(guild_id)
        except Exception as exc:
            raise RuntimeError(
                "memory_guild_reset_durability_failed"
            ) from exc


__all__ = ["GuildRuntimeResetComposition", "GuildRuntimeResetCompositionDeps"]
