from __future__ import annotations

import time
from typing import Any

from .text import clean_text


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "connected", "active", "running"}
    return bool(value)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _position_text(position: Any, fallback: Any = "") -> str:
    fallback_text = clean_text(str(fallback or ""))
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        z = position.get("z")
        try:
            if x is not None and y is not None and z is not None:
                return f"{float(x):.1f}, {float(y):.1f}, {float(z):.1f}"
        except (TypeError, ValueError):
            return fallback_text
    if isinstance(position, (list, tuple)) and len(position) >= 3:
        try:
            return f"{float(position[0]):.1f}, {float(position[1]):.1f}, {float(position[2]):.1f}"
        except (TypeError, ValueError):
            return fallback_text
    return fallback_text


def build_minecraft_runtime_snapshot(
    state: dict[str, Any] | None,
    *,
    source: str = "unknown",
    now: float | None = None,
    observed_at: float | None = None,
    stale_after_sec: float = 15.0,
    expired_after_sec: float | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    state = state if isinstance(state, dict) else {}
    current_time = time.time() if now is None else now
    observed_time = _float_or_none(observed_at)
    age_sec = max(0.0, current_time - observed_time) if observed_time is not None else None
    stale = bool(age_sec is None or age_sec > max(0.0, stale_after_sec))
    expired = bool(expired_after_sec is not None and age_sec is not None and age_sec > max(0.0, expired_after_sec))

    error_text = clean_text(str(last_error or state.get("last_error") or ""))
    running = _as_bool(state.get("minecraft_autonomy") or state.get("running"))
    connected = _as_bool(state.get("voyager_connected") or state.get("connected"))
    active = _as_bool(state.get("active") or connected or state.get("position") or state.get("position_text"))
    absent = not any((running, connected, active, state.get("goal"), state.get("objective_goal"), error_text))
    if error_text:
        freshness = "error"
    elif absent:
        freshness = "absent"
    elif expired:
        freshness = "expired"
    elif stale:
        freshness = "stale"
    else:
        freshness = "fresh"

    position = state.get("position") or state.get("position_block")
    return {
        "snapshot_schema": "minecraft_runtime_snapshot.v1",
        "source": clean_text(source) or "unknown",
        "observed_at": observed_time,
        "age_sec": round(age_sec, 3) if age_sec is not None else None,
        "stale_after_sec": float(stale_after_sec),
        "expired_after_sec": float(expired_after_sec) if expired_after_sec is not None else None,
        "freshness": freshness,
        "stale": stale,
        "expired": expired,
        "running": running,
        "connected": connected,
        "active": active,
        "goal": clean_text(str(state.get("goal") or state.get("objective_goal") or "")) or None,
        "stage": clean_text(str(state.get("stage") or state.get("objective_stage") or "")) or None,
        "current_task": clean_text(str(state.get("current_task") or state.get("objective_task") or "")) or None,
        "current_task_stage": clean_text(str(state.get("current_task_stage") or state.get("objective_task_stage") or "")) or None,
        "position_text": _position_text(position, state.get("position_text")) or None,
        "dimension": clean_text(str(state.get("dimension") or state.get("active_environment") or "")) or None,
        "health": state.get("health"),
        "hunger": state.get("hunger"),
        "inventory_summary": clean_text(str(state.get("inventory_summary") or "")) or None,
        "last_error": error_text or None,
    }


def attach_minecraft_runtime_snapshot(
    state: dict[str, Any] | None,
    *,
    source: str = "unknown",
    now: float | None = None,
    observed_at: float | None = None,
    stale_after_sec: float = 15.0,
    expired_after_sec: float | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    merged = dict(state or {})
    snapshot = build_minecraft_runtime_snapshot(
        merged,
        source=source,
        now=now,
        observed_at=observed_at,
        stale_after_sec=stale_after_sec,
        expired_after_sec=expired_after_sec,
        last_error=last_error,
    )
    merged["runtime_snapshot"] = snapshot
    merged["snapshot_age_sec"] = snapshot["age_sec"]
    merged["snapshot_stale"] = bool(snapshot["stale"])
    merged["snapshot_expired"] = bool(snapshot["expired"])
    merged["snapshot_freshness"] = snapshot["freshness"]
    if snapshot.get("position_text") and not merged.get("position_text"):
        merged["position_text"] = snapshot["position_text"]
    return merged


def minecraft_runtime_status_fields(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state if isinstance(state, dict) else {}
    snapshot = state.get("runtime_snapshot") if isinstance(state.get("runtime_snapshot"), dict) else {}
    return {
        "snapshotFreshness": snapshot.get("freshness") or state.get("snapshot_freshness"),
        "snapshotAgeSec": snapshot.get("age_sec", state.get("snapshot_age_sec")),
        "snapshotStale": bool(snapshot.get("stale", state.get("snapshot_stale", False))),
        "snapshotExpired": bool(snapshot.get("expired", state.get("snapshot_expired", False))),
        "runtimeSnapshot": dict(snapshot),
    }
