from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class RoomSpeakerActivityStore:
    recent_speaker_stats: dict[str, dict[int, dict[str, float]]]
    room_owner_user_ids: dict[str, int]
    room_owner_until: dict[str, float]

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
