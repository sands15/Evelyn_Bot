from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(frozen=True)
class MinecraftLiveObservationRuntimeDeps:
    get_minecraft_client: Callable[[], Any]
    merge_voyager_status_into_state: Callable[[Any, Any], Any]
    attach_minecraft_runtime_snapshot: Callable[..., dict[str, Any]]
    clean_text: Callable[[str], str]
    now: Callable[[], float]
    stale_after_sec: float
    expired_after_sec: float


@dataclass(frozen=True)
class ControlPageMinecraftLiveSnapshotRuntimeDeps:
    get_minecraft_client: Callable[[], Any]
    observe_live_minecraft_state: Callable[[int | None], Awaitable[dict[str, Any] | None]]
    merge_voyager_status_into_state: Callable[[Any, Any], Any]
    normalize_inventory_top_entries: Callable[[Any], list[Any]]
    summarize_inventory_top: Callable[[Any], str]
    normalize_inventory_slot_entries: Callable[..., list[Any]]
    normalize_inventory_used_slots: Callable[[Any, Any], Any]
    extract_recent_activity: Callable[..., list[Any]]
    format_position_short: Callable[[Any], str]
    attach_minecraft_runtime_snapshot: Callable[..., dict[str, Any]]
    clean_text: Callable[[str], str]
    now: Callable[[], float]
    stale_after_sec: float
    expired_after_sec: float


def _redact_minecraft_snapshot_error(
    state: dict[str, Any],
    *,
    local_error_type: str = "",
) -> str:
    error_code = (
        f"minecraft_status_failed:{local_error_type}"
        if local_error_type
        else "minecraft_status_failed" if state.get("last_error") else ""
    )
    if error_code:
        state["last_error"] = error_code
    return error_code


async def observe_live_minecraft_state_from_runtime(
    guild_id: int | None,
    *,
    deps: MinecraftLiveObservationRuntimeDeps,
) -> dict[str, Any] | None:
    _ = guild_id
    client = deps.get_minecraft_client()
    try:
        status = await client.status()
    except Exception:
        status = None
    if isinstance(status, dict):
        observed = status.get("observation") if isinstance(status.get("observation"), dict) else None
        merged = deps.merge_voyager_status_into_state(status, observed)
        if isinstance(merged, dict):
            snapshot_error = _redact_minecraft_snapshot_error(merged)
            has_context = bool(
                status.get("running")
                or status.get("connected")
                or deps.clean_text(str(status.get("goal") or ""))
                or deps.clean_text(str(status.get("stage") or ""))
                or deps.clean_text(str(status.get("current_task") or ""))
                or (
                    isinstance(observed, dict)
                    and (observed.get("connected") or observed.get("active") or observed.get("position"))
                )
            )
            if has_context:
                return deps.attach_minecraft_runtime_snapshot(
                    merged,
                    source="live_status",
                    now=deps.now(),
                    observed_at=deps.now(),
                    stale_after_sec=deps.stale_after_sec,
                    expired_after_sec=deps.expired_after_sec,
                    last_error=snapshot_error or None,
                )
    try:
        observed = await client.observe(ensure_service=False)
    except Exception:
        return None
    if not isinstance(observed, dict):
        return None
    merged = (
        deps.merge_voyager_status_into_state(None, observed)
        if (observed.get("connected") or observed.get("active") or observed.get("position"))
        else None
    )
    if isinstance(merged, dict):
        snapshot_error = _redact_minecraft_snapshot_error(merged)
        return deps.attach_minecraft_runtime_snapshot(
            merged,
            source="live_observe",
            now=deps.now(),
            observed_at=deps.now(),
            stale_after_sec=deps.stale_after_sec,
            expired_after_sec=deps.expired_after_sec,
            last_error=snapshot_error or None,
        )
    return None


async def get_control_page_minecraft_snapshot_from_runtime(
    guild_id: int | None,
    *,
    deps: ControlPageMinecraftLiveSnapshotRuntimeDeps,
) -> dict[str, Any]:
    client = deps.get_minecraft_client()
    raw_status: dict[str, Any] = {}
    last_error_type = ""
    try:
        maybe_status = await client.status()
        if isinstance(maybe_status, dict):
            raw_status = maybe_status
    except Exception as exc:
        last_error_type = type(exc).__name__
    observation = raw_status.get("observation") if isinstance(raw_status.get("observation"), dict) else {}
    merged = deps.merge_voyager_status_into_state(raw_status, observation) or {}
    if not merged:
        merged = await deps.observe_live_minecraft_state(guild_id) or {}
    snapshot_error = _redact_minecraft_snapshot_error(
        merged,
        local_error_type=last_error_type,
    )
    merged["inventory_top"] = deps.normalize_inventory_top_entries(
        merged.get("inventory") or observation.get("inventory")
    )
    merged["inventory_summary"] = deps.summarize_inventory_top(merged["inventory_top"])
    merged["inventory_slots"] = deps.normalize_inventory_slot_entries(
        observation.get("inventory_slots") or observation.get("inventorySlots"),
        inventory=merged.get("inventory") or observation.get("inventory"),
    )
    merged["inventory_used"] = deps.normalize_inventory_used_slots(
        observation.get("inventory_used") or observation.get("inventoryUsed"),
        merged["inventory_slots"],
    )
    merged["recent_activity"] = deps.extract_recent_activity(raw_status, base_limit=2)
    merged["completed_count"] = len(raw_status.get("completed_tasks") or [])
    merged["failed_count"] = len(raw_status.get("failed_tasks") or [])
    merged["current_task"] = deps.clean_text(
        str(raw_status.get("current_task") or merged.get("objective_task") or "")
    )
    merged["current_task_stage"] = deps.clean_text(
        str(raw_status.get("current_task_stage") or merged.get("objective_task_stage") or "")
    )
    merged["goal"] = deps.clean_text(str(raw_status.get("goal") or merged.get("objective_goal") or ""))
    merged["stage"] = deps.clean_text(str(raw_status.get("stage") or merged.get("objective_stage") or ""))
    merged["progress"] = deps.clean_text(
        str(raw_status.get("last_progress_message") or merged.get("objective_progress") or "")
    )
    merged["position_text"] = deps.format_position_short(
        merged.get("position") or observation.get("position")
    )
    return deps.attach_minecraft_runtime_snapshot(
        merged,
        source="control_page_live",
        now=deps.now(),
        observed_at=deps.now(),
        stale_after_sec=deps.stale_after_sec,
        expired_after_sec=deps.expired_after_sec,
        last_error=snapshot_error or None,
    )


__all__ = [
    "ControlPageMinecraftLiveSnapshotRuntimeDeps",
    "MinecraftLiveObservationRuntimeDeps",
    "get_control_page_minecraft_snapshot_from_runtime",
    "observe_live_minecraft_state_from_runtime",
]
