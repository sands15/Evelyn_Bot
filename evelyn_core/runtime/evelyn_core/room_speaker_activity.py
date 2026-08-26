from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session_memory_state import SessionStateStore


def _parse_voice_user_session_key(session_key: str) -> tuple[str, int] | None:
    parts = str(session_key or "").split(":")
    if (
        len(parts) != 6
        or parts[0] != "guild"
        or parts[2] != "voice"
        or parts[4] != "user"
    ):
        return None
    numeric_parts = (parts[1], parts[3], parts[5])
    if any(
        not value.isascii()
        or not value.isdigit()
        or int(value) <= 0
        or str(int(value)) != value
        for value in numeric_parts
    ):
        return None
    guild_id, channel_id, user_id = map(int, numeric_parts)
    return f"guild:{guild_id}:voice:{channel_id}", user_id


@dataclass(slots=True)
class RoomSpeakerActivityStore:
    recent_speaker_stats: dict[str, dict[int, dict[str, float]]]
    room_owner_user_ids: dict[str, int]
    room_owner_until: dict[str, float]

    @classmethod
    def create_empty(cls) -> "RoomSpeakerActivityStore":
        return cls(recent_speaker_stats={}, room_owner_user_ids={}, room_owner_until={})

    def restore_owners_from_sessions(
        self,
        sessions: "SessionStateStore",
        *,
        now: float | None = None,
    ) -> int:
        now_mono = time.monotonic() if now is None else float(now)
        if not math.isfinite(now_mono):
            raise ValueError("invalid_restore_time")
        candidates: dict[str, list[tuple[float, int, float, bool]]] = {}
        blocked_rooms: set[str] = set()
        for session_key, raw_last_active_at in sessions.last_active_at.items():
            parsed = _parse_voice_user_session_key(session_key)
            if parsed is None:
                continue
            room_session_key, key_user_id = parsed
            try:
                last_active_at = float(raw_last_active_at)
            except (TypeError, ValueError):
                blocked_rooms.add(room_session_key)
                continue
            if (
                not math.isfinite(last_active_at)
                or last_active_at > now_mono
            ):
                blocked_rooms.add(room_session_key)
                continue
            try:
                active_until = float(sessions.active_until.get(session_key, 0.0))
            except (TypeError, ValueError):
                active_until = float("nan")
            remembered_user_id = sessions.active_user_ids.get(session_key)
            candidates.setdefault(room_session_key, []).append(
                (
                    last_active_at,
                    key_user_id,
                    active_until,
                    type(remembered_user_id) is int
                    and remembered_user_id == key_user_id,
                )
            )

        self.room_owner_user_ids.clear()
        self.room_owner_until.clear()
        restored = 0
        for room_session_key, room_candidates in candidates.items():
            if room_session_key in blocked_rooms:
                continue
            newest_at = max(candidate[0] for candidate in room_candidates)
            newest = [
                candidate
                for candidate in room_candidates
                if candidate[0] == newest_at
            ]
            if len(newest) != 1:
                continue
            _, user_id, active_until, user_bound = newest[0]
            if (
                not user_bound
                or not math.isfinite(active_until)
                or active_until <= now_mono
            ):
                continue
            self.room_owner_user_ids[room_session_key] = user_id
            self.room_owner_until[room_session_key] = active_until
            restored += 1
        return restored

    def prune(self, room_session_key: str | None, *, now: float | None = None) -> dict[int, dict[str, float]]:
        if not room_session_key:
            return {}
        now_mono = time.monotonic() if now is None else float(now)
        stats = self.recent_speaker_stats.get(room_session_key, {})
        keep: dict[int, dict[str, float]] = {}
        for user_id, data in stats.items():
            last_packet_at = float(data.get("last_packet_at") or 0.0)
            if now_mono - last_packet_at <= 2.5:
                keep[int(user_id)] = data
        if keep:
            self.recent_speaker_stats[room_session_key] = keep
        else:
            self.recent_speaker_stats.pop(room_session_key, None)
        return keep

    def update(
        self,
        room_session_key: str | None,
        user_id: int | None,
        *,
        voiced_ms: float,
        raw_seconds: float,
        rms: float,
        wake_detected: bool = False,
        now: float | None = None,
    ) -> dict[str, float]:
        if not room_session_key or user_id is None:
            return {}
        now_mono = time.monotonic() if now is None else float(now)
        stats = self.prune(room_session_key, now=now_mono)
        entry = stats.setdefault(int(user_id), {})
        entry["last_packet_at"] = now_mono
        entry["recent_voiced_ms"] = max(float(entry.get("recent_voiced_ms") or 0.0) * 0.55, float(voiced_ms))
        entry["recent_raw_ms"] = max(float(entry.get("recent_raw_ms") or 0.0) * 0.55, float(raw_seconds) * 1000.0)
        entry["body_rms"] = max(float(entry.get("body_rms") or 0.0) * 0.6, float(rms))
        if wake_detected:
            entry["wake_priority"] = now_mono
        else:
            entry["wake_priority"] = float(entry.get("wake_priority") or 0.0)
        self.recent_speaker_stats[room_session_key] = stats
        return entry

    def pick_active_speaker(self, room_session_key: str | None, *, now: float | None = None) -> int | None:
        if not room_session_key:
            return None
        now_mono = time.monotonic() if now is None else float(now)
        stats = self.prune(room_session_key, now=now_mono)
        if not stats:
            return None

        owner_user_id = self.room_owner_user_ids.get(room_session_key)
        owner_until = float(self.room_owner_until.get(room_session_key, 0.0) or 0.0)
        owner_active = owner_user_id is not None and owner_until > now_mono
        if owner_active:
            owner_stats = stats.get(int(owner_user_id))
            if owner_stats and now_mono - float(owner_stats.get("last_packet_at") or 0.0) <= 0.5:
                return int(owner_user_id)

        scored: list[tuple[tuple[float, float, float, float], int]] = []
        for user_id, data in stats.items():
            scored.append((
                (
                    float(data.get("wake_priority") or 0.0),
                    float(data.get("recent_voiced_ms") or 0.0),
                    float(data.get("body_rms") or 0.0),
                    float(data.get("last_packet_at") or 0.0),
                ),
                int(user_id),
            ))
        scored.sort(reverse=True)
        return scored[0][1] if scored else None


__all__ = ["RoomSpeakerActivityStore"]
