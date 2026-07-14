from __future__ import annotations

from typing import Any, Mapping, MutableMapping


def clear_room_owner(
    room_session_key: str | None,
    *,
    room_owner_user_ids: MutableMapping[str, int],
    room_owner_until: MutableMapping[str, float],
) -> None:
    if not room_session_key:
        return
    room_owner_user_ids.pop(room_session_key, None)
    room_owner_until.pop(room_session_key, None)


def room_state_snapshot(
    room_session_key: str | None,
    *,
    room_owner_user_ids: MutableMapping[str, int],
    room_owner_until: MutableMapping[str, float],
    room_reply_in_progress: Mapping[str, bool],
    active_speaker_user_id: int | None,
    now_monotonic: float,
) -> dict[str, Any]:
    if not room_session_key:
        return {}
    owner_until = float(room_owner_until.get(room_session_key, 0.0) or 0.0)
    if owner_until <= now_monotonic and not room_reply_in_progress.get(room_session_key, False):
        clear_room_owner(
            room_session_key,
            room_owner_user_ids=room_owner_user_ids,
            room_owner_until=room_owner_until,
        )
        owner_until = 0.0
    return {
        "owner_user_id": room_owner_user_ids.get(room_session_key),
        "owner_until": owner_until,
        "reply_in_progress": bool(room_reply_in_progress.get(room_session_key, False)),
        "active_speaker_user_id": active_speaker_user_id,
    }


def is_room_owner_active(
    room_session_key: str | None,
    user_id: int | None,
    *,
    room_owner_user_ids: MutableMapping[str, int],
    room_owner_until: MutableMapping[str, float],
    room_reply_in_progress: Mapping[str, bool],
    active_speaker_user_id: int | None,
    now_monotonic: float,
) -> bool:
    if not room_session_key or user_id is None:
        return False
    state = room_state_snapshot(
        room_session_key,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        active_speaker_user_id=active_speaker_user_id,
        now_monotonic=now_monotonic,
    )
    return state.get("owner_user_id") == user_id and float(state.get("owner_until") or 0.0) > now_monotonic


def set_room_owner(
    room_session_key: str | None,
    user_id: int | None,
    *,
    ttl_sec: float,
    reason: str,
    room_owner_user_ids: MutableMapping[str, int],
    room_owner_until: MutableMapping[str, float],
    log_event: Any,
    now_monotonic: float,
    session_key: str | None = None,
    turn_id: str | None = None,
    segment_id: int | None = None,
) -> None:
    if not room_session_key or user_id is None:
        return
    previous_owner = room_owner_user_ids.get(room_session_key)
    room_owner_user_ids[room_session_key] = user_id
    room_owner_until[room_session_key] = now_monotonic + max(0.0, ttl_sec)
    log_event(
        "room_owner_update",
        room_session_key=room_session_key,
        previous_owner_user_id=previous_owner,
        owner_user_id=user_id,
        owner_until=round(room_owner_until[room_session_key], 3),
        reason=reason,
        session_key=session_key,
        turn_id=turn_id,
        segment_id=segment_id,
    )


def set_room_reply_in_progress(
    room_session_key: str | None,
    value: bool,
    *,
    room_reply_in_progress: MutableMapping[str, bool],
    room_owner_user_ids: Mapping[str, int],
    log_event: Any,
    owner_user_id: int | None = None,
) -> None:
    if not room_session_key:
        return
    room_reply_in_progress[room_session_key] = value
    log_event(
        "room_reply_state",
        room_session_key=room_session_key,
        reply_in_progress=value,
        owner_user_id=owner_user_id if owner_user_id is not None else room_owner_user_ids.get(room_session_key),
    )
