from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping


@dataclass(frozen=True)
class GuildRuntimeResetDeps:
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


def build_guild_runtime_reset_deps(
    *,
    session_histories: MutableMapping[str, Any],
    session_followup_targets: MutableMapping[str, Any],
    active_session_until: MutableMapping[str, Any],
    active_session_user_ids: MutableMapping[str, Any],
    session_last_active_at: MutableMapping[str, Any],
    session_awaiting_user_reply: MutableMapping[str, Any],
    session_last_speaker: MutableMapping[str, Any],
    session_topic_ids: MutableMapping[str, Any],
    session_turn_ids: MutableMapping[str, Any],
    session_segment_counters: MutableMapping[str, Any],
    session_last_turn_accepted_at: MutableMapping[str, Any],
    session_last_stt_text: MutableMapping[str, Any],
    room_last_voice_utterance_for_merge: MutableMapping[str, Any],
    session_partial_stt_text: MutableMapping[str, Any],
    session_committed_stt_text: MutableMapping[str, Any],
    session_bad_audio_counts: MutableMapping[str, Any],
    room_owner_user_ids: MutableMapping[str, Any],
    room_owner_until: MutableMapping[str, Any],
    room_reply_in_progress: MutableMapping[str, Any],
    room_last_voice_reply_at: MutableMapping[str, Any],
    turn_scope_registry: Any,
    session_locks: MutableMapping[str, Any],
    background_search_tasks: MutableMapping[str, Any],
    clear_tts_playback_tracking: Callable[..., Any],
    tts_playback_tracker: Any,
    memory_locks: MutableMapping[int, Any],
    cognitive_locks: MutableMapping[int, Any],
    background_cognitive_tasks: MutableMapping[str, Any],
    autonomy_last_cognitive_refresh_at: MutableMapping[int, Any],
    autonomy_cognitive_refresh_tasks: MutableMapping[int, Any],
) -> GuildRuntimeResetDeps:
    return GuildRuntimeResetDeps(
        session_histories=session_histories,
        session_followup_targets=session_followup_targets,
        active_session_until=active_session_until,
        active_session_user_ids=active_session_user_ids,
        session_last_active_at=session_last_active_at,
        session_awaiting_user_reply=session_awaiting_user_reply,
        session_last_speaker=session_last_speaker,
        session_topic_ids=session_topic_ids,
        session_turn_ids=session_turn_ids,
        session_segment_counters=session_segment_counters,
        session_last_turn_accepted_at=session_last_turn_accepted_at,
        session_last_stt_text=session_last_stt_text,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        session_bad_audio_counts=session_bad_audio_counts,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        room_last_voice_reply_at=room_last_voice_reply_at,
        turn_scope_registry=turn_scope_registry,
        session_locks=session_locks,
        background_search_tasks=background_search_tasks,
        clear_tts_playback_tracking=clear_tts_playback_tracking,
        tts_playback_tracker=tts_playback_tracker,
        memory_locks=memory_locks,
        cognitive_locks=cognitive_locks,
        background_cognitive_tasks=background_cognitive_tasks,
        autonomy_last_cognitive_refresh_at=autonomy_last_cognitive_refresh_at,
        autonomy_cognitive_refresh_tasks=autonomy_cognitive_refresh_tasks,
    )


def _cancel_task(task: Any) -> None:
    if task is not None and not task.done():
        task.cancel()


def _has_prefix(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and value.startswith(prefix)


def _drop_prefixed(mapping: MutableMapping[str, Any], prefix: str) -> None:
    for key in [key for key in mapping if _has_prefix(key, prefix)]:
        mapping.pop(key, None)


def reset_guild_runtime_state_from_runtime(guild_id: int, *, deps: GuildRuntimeResetDeps) -> None:
    prefix = f"guild:{guild_id}:"
    for mapping in (
        deps.session_histories,
        deps.session_followup_targets,
        deps.active_session_until,
        deps.active_session_user_ids,
        deps.session_last_active_at,
        deps.session_awaiting_user_reply,
        deps.session_last_speaker,
        deps.session_topic_ids,
        deps.session_turn_ids,
        deps.session_segment_counters,
        deps.session_last_turn_accepted_at,
        deps.session_last_stt_text,
        deps.session_partial_stt_text,
        deps.session_committed_stt_text,
        deps.session_bad_audio_counts,
    ):
        _drop_prefixed(mapping, prefix)
    for room_key, record in list(
        deps.room_last_voice_utterance_for_merge.items()
    ):
        if _has_prefix(getattr(record, "session_key", None), prefix):
            deps.room_last_voice_utterance_for_merge.pop(room_key, None)
    for mapping in (
        deps.room_owner_user_ids,
        deps.room_owner_until,
        deps.room_reply_in_progress,
        deps.room_last_voice_reply_at,
    ):
        _drop_prefixed(mapping, prefix)
    deps.turn_scope_registry.cancel_matching_prefix(prefix)
    _drop_prefixed(deps.session_locks, prefix)
    for key, task in list(deps.background_search_tasks.items()):
        if _has_prefix(key, prefix):
            _cancel_task(task)
            deps.background_search_tasks.pop(key, None)
    deps.clear_tts_playback_tracking(
        tracker=deps.tts_playback_tracker,
        guild_id=guild_id,
    )
    deps.memory_locks.pop(guild_id, None)
    deps.cognitive_locks.pop(guild_id, None)
    for key, task in list(deps.background_cognitive_tasks.items()):
        if _has_prefix(key, prefix):
            _cancel_task(task)
            deps.background_cognitive_tasks.pop(key, None)
    deps.autonomy_last_cognitive_refresh_at.pop(guild_id, None)
    refresh_task = deps.autonomy_cognitive_refresh_tasks.pop(guild_id, None)
    _cancel_task(refresh_task)
