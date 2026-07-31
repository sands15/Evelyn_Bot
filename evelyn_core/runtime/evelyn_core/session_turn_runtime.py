from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

from .session_memory_state import SessionStateStore, new_conversation_history as create_empty_conversation_history


@dataclass(frozen=True)
class SessionTurnRuntimeDeps:
    session_state_store: SessionStateStore
    system_prompt: str
    active_conversation_awaiting_reply_sec: float
    active_conversation_text_question_sec: float
    active_conversation_text_sec: float
    max_history_items: int
    session_topic_ids: MutableMapping[str, str]
    build_topic_id_fn: Callable[..., str]
    new_turn_id_fn: Callable[[], str]


def new_conversation_history_from_runtime(deps: SessionTurnRuntimeDeps) -> list[dict]:
    return create_empty_conversation_history(deps.system_prompt)


def remember_session_followup_target_from_runtime(
    session_key: str,
    *,
    channel_id: int | None = None,
    message_id: int | None = None,
    deps: SessionTurnRuntimeDeps,
) -> None:
    deps.session_state_store.remember_followup_target(
        session_key,
        channel_id=channel_id,
        message_id=message_id,
    )


def build_topic_id_from_runtime(*texts: str, deps: SessionTurnRuntimeDeps) -> str:
    return deps.build_topic_id_fn(*texts)


def new_turn_id_from_runtime(deps: SessionTurnRuntimeDeps) -> str:
    return deps.new_turn_id_fn()


def current_turn_id_from_runtime(session_key: str | None, deps: SessionTurnRuntimeDeps) -> str | None:
    return deps.session_state_store.current_turn_id(session_key)


def next_segment_id_from_runtime(session_key: str | None, deps: SessionTurnRuntimeDeps) -> int:
    return deps.session_state_store.next_segment_id(session_key)


def start_new_turn_from_runtime(
    session_key: str | None,
    *,
    turn_id: str | None = None,
    deps: SessionTurnRuntimeDeps,
) -> str:
    return deps.session_state_store.start_new_turn(session_key, turn_id=turn_id)


def begin_user_text_turn_from_runtime(
    session_key: str,
    user_text: str,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
    deps: SessionTurnRuntimeDeps,
) -> Any:
    return deps.session_state_store.begin_user_text_turn(
        session_key,
        user_text,
        system_prompt=deps.system_prompt,
        active_conversation_awaiting_reply_sec=deps.active_conversation_text_question_sec,
        max_history_items=deps.max_history_items,
        guild_id=guild_id,
        user_id=user_id,
        previous_topic_id=deps.session_topic_ids.get(session_key, ""),
    )


def finish_assistant_text_turn_from_runtime(
    session_key: str,
    user_text: str,
    answer_text: str,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
    awaiting_user_reply: bool,
    topic_id: str | None = None,
    deps: SessionTurnRuntimeDeps,
) -> Any:
    return deps.session_state_store.finish_assistant_text_turn(
        session_key,
        user_text,
        answer_text,
        system_prompt=deps.system_prompt,
        max_history_items=deps.max_history_items,
        guild_id=guild_id,
        user_id=user_id,
        awaiting_user_reply=awaiting_user_reply,
        normal_ttl_sec=deps.active_conversation_text_sec,
        question_ttl_sec=deps.active_conversation_text_question_sec,
        topic_id=topic_id,
    )


def session_state_snapshot_from_runtime(session_key: str | None, deps: SessionTurnRuntimeDeps) -> dict:
    return deps.session_state_store.snapshot(session_key)


def update_session_state_from_runtime(
    session_key: str | None,
    *,
    user_id: int | None = None,
    speaker: str | None = None,
    ttl_sec: float | None = None,
    awaiting_user_reply: bool | None = None,
    topic_id: str | None = None,
    answer_text: str | None = None,
    user_text: str | None = None,
    deps: SessionTurnRuntimeDeps,
) -> None:
    return deps.session_state_store.update_session_state(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
        active_conversation_awaiting_reply_sec=deps.active_conversation_awaiting_reply_sec,
    )


def mark_session_active_from_runtime(
    session_key: str,
    *,
    user_id: int | None = None,
    ttl_sec: float = 90.0,
    speaker: str = "assistant",
    awaiting_user_reply: bool | None = None,
    topic_id: str | None = None,
    answer_text: str | None = None,
    user_text: str | None = None,
    deps: SessionTurnRuntimeDeps,
) -> None:
    return deps.session_state_store.mark_active(
        session_key,
        user_id=user_id,
        ttl_sec=ttl_sec,
        speaker=speaker,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
        active_conversation_awaiting_reply_sec=deps.active_conversation_awaiting_reply_sec,
    )


def is_session_active_for_user_from_runtime(
    session_key: str,
    user_id: int | None = None,
    *,
    deps: SessionTurnRuntimeDeps,
) -> bool:
    return deps.session_state_store.is_active_for_user(session_key, user_id)


def get_conversation_history_from_runtime(
    *,
    session_key: str | None = None,
    guild_id: int | None = None,
    deps: SessionTurnRuntimeDeps,
) -> list[dict]:
    return deps.session_state_store.get_conversation_history(
        system_prompt=deps.system_prompt,
        session_key=session_key,
        guild_id=guild_id,
    )


def trim_history_from_runtime(
    *,
    session_key: str | None = None,
    guild_id: int | None = None,
    deps: SessionTurnRuntimeDeps,
) -> None:
    deps.session_state_store.trim_history(
        system_prompt=deps.system_prompt,
        max_history_items=deps.max_history_items,
        session_key=session_key,
        guild_id=guild_id,
    )


def append_history_from_runtime(
    session_key: str | None,
    user_text: str,
    answer: str | None,
    *,
    guild_id: int | None = None,
    deps: SessionTurnRuntimeDeps,
) -> None:
    deps.session_state_store.append_history(
        session_key,
        user_text,
        answer,
        system_prompt=deps.system_prompt,
        max_history_items=deps.max_history_items,
        guild_id=guild_id,
    )


def recent_assistant_reply_summary_from_runtime(
    *,
    session_key: str | None = None,
    guild_id: int | None = None,
    limit: int = 1,
    deps: SessionTurnRuntimeDeps,
) -> str:
    return deps.session_state_store.recent_assistant_reply_summary(
        system_prompt=deps.system_prompt,
        session_key=session_key,
        guild_id=guild_id,
        limit=limit,
    )


def persona_state_hint_for_turn_from_runtime(
    user_text: str,
    *,
    session_key: str | None = None,
    guild_id: int | None = None,
    deps: SessionTurnRuntimeDeps,
) -> str:
    return deps.session_state_store.persona_state_hint_for_turn(
        user_text,
        system_prompt=deps.system_prompt,
        session_key=session_key,
        guild_id=guild_id,
    )


def increment_session_bad_audio_from_runtime(session_key: str | None, deps: SessionTurnRuntimeDeps) -> int:
    return deps.session_state_store.increment_bad_audio(session_key)


def reset_session_bad_audio_from_runtime(session_key: str | None, deps: SessionTurnRuntimeDeps) -> None:
    deps.session_state_store.reset_bad_audio(session_key)


__all__ = [
    "SessionTurnRuntimeDeps",
    "append_history_from_runtime",
    "begin_user_text_turn_from_runtime",
    "build_topic_id_from_runtime",
    "current_turn_id_from_runtime",
    "finish_assistant_text_turn_from_runtime",
    "get_conversation_history_from_runtime",
    "increment_session_bad_audio_from_runtime",
    "is_session_active_for_user_from_runtime",
    "mark_session_active_from_runtime",
    "new_conversation_history_from_runtime",
    "new_turn_id_from_runtime",
    "next_segment_id_from_runtime",
    "persona_state_hint_for_turn_from_runtime",
    "remember_session_followup_target_from_runtime",
    "recent_assistant_reply_summary_from_runtime",
    "reset_session_bad_audio_from_runtime",
    "session_state_snapshot_from_runtime",
    "start_new_turn_from_runtime",
    "trim_history_from_runtime",
    "update_session_state_from_runtime",
    "append_history_from_runtime",
]
