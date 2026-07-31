from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .discord_session_policy import DiscordRoomSessionPolicy
from .session_turn_runtime import (
    append_history_from_runtime,
    begin_user_text_turn_from_runtime,
    build_topic_id_from_runtime,
    current_turn_id_from_runtime,
    finish_assistant_text_turn_from_runtime,
    get_conversation_history_from_runtime,
    increment_session_bad_audio_from_runtime,
    is_session_active_for_user_from_runtime,
    mark_session_active_from_runtime,
    new_conversation_history_from_runtime,
    new_turn_id_from_runtime,
    next_segment_id_from_runtime,
    persona_state_hint_for_turn_from_runtime,
    recent_assistant_reply_summary_from_runtime,
    remember_session_followup_target_from_runtime,
    reset_session_bad_audio_from_runtime,
    session_state_snapshot_from_runtime,
    start_new_turn_from_runtime,
    trim_history_from_runtime,
    update_session_state_from_runtime,
)


@dataclass(frozen=True)
class ConversationSessionCompositionDeps:
    session: Callable[[], Any]
    room_owner_user_ids: MutableMapping[str, int]
    room_owner_until: MutableMapping[str, float]
    room_reply_in_progress: MutableMapping[str, bool]
    room_speaker_activity_store: Any
    monotonic: Callable[[], float]
    log_event: Callable[..., Any]


class ConversationSessionComposition:
    """Owns session-turn and Discord room-state adapters."""

    def __init__(self, deps: ConversationSessionCompositionDeps) -> None:
        self.deps = deps

    def new_conversation_history(self) -> list[dict]:
        return new_conversation_history_from_runtime(self.deps.session())

    def remember_session_followup_target(
        self,
        session_key: str,
        *,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        remember_session_followup_target_from_runtime(
            session_key,
            channel_id=channel_id,
            message_id=message_id,
            deps=self.deps.session(),
        )

    def build_topic_id(self, *texts: str) -> str:
        return build_topic_id_from_runtime(*texts, deps=self.deps.session())

    def new_turn_id(self) -> str:
        return new_turn_id_from_runtime(self.deps.session())

    def current_turn_id(self, session_key: str | None) -> str | None:
        return current_turn_id_from_runtime(session_key, deps=self.deps.session())

    def next_segment_id(self, session_key: str | None) -> int:
        return next_segment_id_from_runtime(session_key, deps=self.deps.session())

    def start_new_turn(self, session_key: str | None, *, turn_id: str | None = None) -> str:
        return start_new_turn_from_runtime(
            session_key,
            turn_id=turn_id,
            deps=self.deps.session(),
        )

    def begin_user_text_turn(
        self,
        session_key: str,
        user_text: str,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
    ) -> Any:
        return begin_user_text_turn_from_runtime(
            session_key,
            user_text,
            guild_id=guild_id,
            user_id=user_id,
            deps=self.deps.session(),
        )

    def finish_assistant_text_turn(
        self,
        session_key: str,
        user_text: str,
        answer_text: str,
        *,
        guild_id: int | None = None,
        user_id: int | None = None,
        awaiting_user_reply: bool,
        topic_id: str | None = None,
    ) -> Any:
        return finish_assistant_text_turn_from_runtime(
            session_key,
            user_text,
            answer_text,
            awaiting_user_reply=awaiting_user_reply,
            topic_id=topic_id,
            guild_id=guild_id,
            user_id=user_id,
            deps=self.deps.session(),
        )

    def session_state_snapshot(self, session_key: str | None) -> dict:
        return session_state_snapshot_from_runtime(session_key, deps=self.deps.session())

    def discord_room_session_policy(self) -> DiscordRoomSessionPolicy:
        deps = self.deps
        return DiscordRoomSessionPolicy(
            room_owner_user_ids=deps.room_owner_user_ids,
            room_owner_until=deps.room_owner_until,
            room_reply_in_progress=deps.room_reply_in_progress,
            log_event=deps.log_event,
            now_monotonic=deps.monotonic,
            pick_active_speaker=self.pick_active_speaker,
        )

    def clear_room_owner(self, room_session_key: str | None) -> None:
        self.discord_room_session_policy().clear_owner(room_session_key)

    def room_state_snapshot(self, room_session_key: str | None) -> dict:
        return self.discord_room_session_policy().snapshot(room_session_key)

    def prune_room_speaker_stats(
        self,
        room_session_key: str | None,
        *,
        now: float | None = None,
    ) -> dict[int, dict[str, float]]:
        return self.deps.room_speaker_activity_store.prune(room_session_key, now=now)

    def update_room_speaker_activity(
        self,
        room_session_key: str | None,
        user_id: int | None,
        *,
        voiced_ms: float,
        raw_seconds: float,
        rms: float,
        wake_detected: bool = False,
    ) -> dict[str, float]:
        return self.deps.room_speaker_activity_store.update(
            room_session_key,
            user_id,
            voiced_ms=voiced_ms,
            raw_seconds=raw_seconds,
            rms=rms,
            wake_detected=wake_detected,
        )

    def pick_active_speaker(self, room_session_key: str | None) -> int | None:
        return self.deps.room_speaker_activity_store.pick_active_speaker(room_session_key)

    def is_room_owner_active(
        self,
        room_session_key: str | None,
        user_id: int | None,
    ) -> bool:
        return self.discord_room_session_policy().is_owner_active(room_session_key, user_id)

    def set_room_owner(
        self,
        room_session_key: str | None,
        user_id: int | None,
        *,
        ttl_sec: float,
        reason: str,
        session_key: str | None = None,
        turn_id: str | None = None,
        segment_id: int | None = None,
    ) -> None:
        self.discord_room_session_policy().set_owner(
            room_session_key,
            user_id,
            ttl_sec=ttl_sec,
            reason=reason,
            session_key=session_key,
            turn_id=turn_id,
            segment_id=segment_id,
        )

    def set_room_reply_in_progress(
        self,
        room_session_key: str | None,
        value: bool,
        *,
        owner_user_id: int | None = None,
    ) -> None:
        self.discord_room_session_policy().set_reply_in_progress(
            room_session_key,
            value,
            owner_user_id=owner_user_id,
        )

    def increment_session_bad_audio(self, session_key: str | None) -> int:
        return increment_session_bad_audio_from_runtime(session_key, deps=self.deps.session())

    def reset_session_bad_audio(self, session_key: str | None) -> None:
        reset_session_bad_audio_from_runtime(session_key, deps=self.deps.session())

    def update_session_state(
        self,
        session_key: str | None,
        *,
        user_id: int | None = None,
        speaker: str | None = None,
        ttl_sec: float | None = None,
        awaiting_user_reply: bool | None = None,
        topic_id: str | None = None,
        answer_text: str | None = None,
        user_text: str | None = None,
    ) -> None:
        update_session_state_from_runtime(
            session_key,
            user_id=user_id,
            speaker=speaker,
            ttl_sec=ttl_sec,
            awaiting_user_reply=awaiting_user_reply,
            topic_id=topic_id,
            answer_text=answer_text,
            user_text=user_text,
            deps=self.deps.session(),
        )

    def mark_session_active(
        self,
        session_key: str,
        *,
        user_id: int | None = None,
        ttl_sec: float = 90.0,
        speaker: str = "assistant",
        awaiting_user_reply: bool | None = None,
        topic_id: str | None = None,
        answer_text: str | None = None,
        user_text: str | None = None,
    ) -> None:
        mark_session_active_from_runtime(
            session_key,
            user_id=user_id,
            speaker=speaker,
            ttl_sec=ttl_sec,
            awaiting_user_reply=awaiting_user_reply,
            topic_id=topic_id,
            answer_text=answer_text,
            user_text=user_text,
            deps=self.deps.session(),
        )

    def is_session_active_for_user(
        self,
        session_key: str,
        user_id: int | None = None,
    ) -> bool:
        return is_session_active_for_user_from_runtime(
            session_key,
            user_id=user_id,
            deps=self.deps.session(),
        )

    def get_conversation_history(
        self,
        *,
        session_key: str | None = None,
        guild_id: int | None = None,
    ) -> list[dict]:
        return get_conversation_history_from_runtime(
            session_key=session_key,
            guild_id=guild_id,
            deps=self.deps.session(),
        )

    def trim_history(
        self,
        *,
        session_key: str | None = None,
        guild_id: int | None = None,
    ) -> None:
        trim_history_from_runtime(
            session_key=session_key,
            guild_id=guild_id,
            deps=self.deps.session(),
        )

    def append_history(
        self,
        session_key: str | None,
        user_text: str,
        answer: str | None,
        *,
        guild_id: int | None = None,
    ) -> None:
        append_history_from_runtime(
            session_key,
            user_text,
            answer,
            guild_id=guild_id,
            deps=self.deps.session(),
        )

    def recent_assistant_reply_summary(
        self,
        *,
        session_key: str | None = None,
        guild_id: int | None = None,
        limit: int = 1,
    ) -> str:
        return recent_assistant_reply_summary_from_runtime(
            session_key=session_key,
            guild_id=guild_id,
            limit=limit,
            deps=self.deps.session(),
        )

    def persona_state_hint_for_turn(
        self,
        user_text: str,
        *,
        session_key: str | None = None,
        guild_id: int | None = None,
    ) -> str:
        return persona_state_hint_for_turn_from_runtime(
            user_text,
            session_key=session_key,
            guild_id=guild_id,
            deps=self.deps.session(),
        )
