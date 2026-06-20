from __future__ import annotations

import re
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


def format_minecraft_state_summary(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict) or not state:
        return ""
    parts: list[str] = []
    runtime_snapshot = state.get("runtime_snapshot") if isinstance(state.get("runtime_snapshot"), dict) else {}
    if runtime_snapshot:
        freshness = clean_text(str(runtime_snapshot.get("freshness") or ""))
        age_sec = runtime_snapshot.get("age_sec")
        if freshness:
            parts.append(f"snapshot={freshness}")
        if age_sec is not None:
            parts.append(f"snapshot_age={age_sec}s")
        snapshot_error = clean_text(str(runtime_snapshot.get("last_error") or ""))
        if snapshot_error:
            parts.append(f"snapshot_error={snapshot_error[:120]}")
    voyager_running = state.get("minecraft_autonomy")
    voyager_connected = state.get("voyager_connected")
    if voyager_running is not None:
        parts.append(f"Voyager={'on' if bool(voyager_running) else 'off'}")
    if voyager_connected is not None:
        parts.append(f"연결={'on' if bool(voyager_connected) else 'off'}")
    objective_goal = clean_text(str(state.get("objective_goal") or state.get("goal") or ""))
    objective_stage = clean_text(str(state.get("objective_stage") or state.get("stage") or ""))
    objective_task = clean_text(str(state.get("objective_task") or state.get("current_task") or ""))
    if objective_goal:
        parts.append(f"Voyager목표={objective_goal}")
    if objective_stage:
        parts.append(f"Voyager단계={objective_stage}")
    if objective_task:
        parts.append(f"Voyager작업={objective_task}")
    voyager_evaluation = state.get("voyager_evaluation") if isinstance(state.get("voyager_evaluation"), dict) else {}
    unique_item_count = voyager_evaluation.get("unique_item_count") if voyager_evaluation else state.get("voyager_unique_item_count")
    tech_tree_highest = clean_text(str((voyager_evaluation.get("tech_tree") or {}).get("highest_unlocked") if voyager_evaluation else state.get("voyager_tech_tree_highest") or ""))
    travel_distance_blocks = voyager_evaluation.get("travel_distance_blocks") if voyager_evaluation else state.get("voyager_travel_distance_blocks")
    skill_library_size = ((voyager_evaluation.get("skill_library") or {}).get("size") if voyager_evaluation else state.get("voyager_skill_library_size"))
    if unique_item_count is not None:
        parts.append(f"유니크아이템={unique_item_count}")
    if tech_tree_highest:
        parts.append(f"테크={tech_tree_highest}")
    if travel_distance_blocks is not None:
        parts.append(f"이동거리={travel_distance_blocks}b")
    if skill_library_size is not None:
        parts.append(f"스킬라이브러리={skill_library_size}")
    position = state.get("position") or state.get("position_block")
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        z = position.get("z")
        if x is not None and y is not None and z is not None:
            parts.append(f"위치=({x},{y},{z})")
    health = state.get("health")
    hunger = state.get("hunger")
    if health is not None:
        parts.append(f"체력={health}")
    if hunger is not None:
        parts.append(f"허기={hunger}")
    hostiles = state.get("hostiles_nearby")
    if hostiles is not None:
        parts.append(f"근처 적대몹={hostiles}")
    inventory = state.get("inventory") or {}
    if isinstance(inventory, dict) and inventory:
        top_items = []
        for name, count in sorted(inventory.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))[:6]:
            top_items.append(f"{name}x{count}")
        if top_items:
            parts.append("인벤토리=" + ", ".join(top_items))
    nearby_blocks = state.get("nearby_blocks") or []
    if nearby_blocks:
        parts.append("주변블록=" + ", ".join(str(value) for value in list(nearby_blocks)[:6]))
    nearby_entities = state.get("nearby_entities") or []
    if nearby_entities:
        parts.append("주변엔티티=" + ", ".join(str(value) for value in list(nearby_entities)[:6]))
    active_environment = clean_text(str(state.get("active_environment") or ""))
    if active_environment:
        parts.append(f"활성환경={active_environment}")
    return clean_text(" / ".join(parts))


def merge_voyager_status_into_state(status: dict[str, Any] | None, observed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(status, dict) and not isinstance(observed, dict):
        return None
    merged: dict[str, Any] = dict(observed or {})
    if isinstance(status, dict):
        merged["minecraft_autonomy"] = bool(status.get("running"))
        merged["voyager_connected"] = bool(status.get("connected"))
        goal = clean_text(str(status.get("goal") or ""))
        stage = clean_text(str(status.get("stage") or ""))
        current_task = clean_text(str(status.get("current_task") or ""))
        current_task_stage = clean_text(str(status.get("current_task_stage") or ""))
        last_action = clean_text(str(status.get("last_action") or ""))
        last_progress_message = clean_text(str(status.get("last_progress_message") or ""))
        last_error = clean_text(str(status.get("last_error") or ""))
        if goal:
            merged["objective_goal"] = goal
        if stage:
            merged["objective_stage"] = stage
        if current_task:
            merged["objective_task"] = current_task
        if current_task_stage:
            merged["objective_task_stage"] = current_task_stage
        if last_action:
            merged["objective_last_action"] = last_action
        if last_progress_message:
            merged["objective_progress"] = last_progress_message
        if last_error and not merged.get("last_error"):
            merged["last_error"] = last_error
        current_execution = status.get("autonomy_current_execution")
        if isinstance(current_execution, dict):
            execution_desc = clean_text(str(current_execution.get("description") or current_execution.get("action") or ""))
            execution_stage = clean_text(str(current_execution.get("stage") or ""))
            if execution_desc and not merged.get("objective_task"):
                merged["objective_task"] = execution_desc
            if execution_stage and not merged.get("objective_task_stage"):
                merged["objective_task_stage"] = execution_stage
        voyager_evaluation = status.get("voyager_evaluation") if isinstance(status.get("voyager_evaluation"), dict) else None
        if isinstance(voyager_evaluation, dict):
            merged["voyager_evaluation"] = voyager_evaluation
            unique_item_count = voyager_evaluation.get("unique_item_count")
            if unique_item_count is not None:
                merged["voyager_unique_item_count"] = unique_item_count
            travel_distance_blocks = voyager_evaluation.get("travel_distance_blocks")
            if travel_distance_blocks is not None:
                merged["voyager_travel_distance_blocks"] = travel_distance_blocks
            tech_tree = voyager_evaluation.get("tech_tree") if isinstance(voyager_evaluation.get("tech_tree"), dict) else {}
            tech_tree_highest = clean_text(str(tech_tree.get("highest_unlocked") or ""))
            if tech_tree_highest:
                merged["voyager_tech_tree_highest"] = tech_tree_highest
            skill_library = voyager_evaluation.get("skill_library") if isinstance(voyager_evaluation.get("skill_library"), dict) else {}
            skill_library_size = skill_library.get("size")
            if skill_library_size is not None:
                merged["voyager_skill_library_size"] = skill_library_size
    return merged if merged else None


def format_position_short(position: Any) -> str:
    if isinstance(position, dict):
        x = position.get("x")
        y = position.get("y")
        z = position.get("z")
        if all(isinstance(value, (int, float)) for value in (x, y, z)):
            return f"{x:.1f}, {y:.1f}, {z:.1f}"
    if isinstance(position, (list, tuple)) and len(position) >= 3 and all(isinstance(value, (int, float)) for value in position[:3]):
        return f"{float(position[0]):.1f}, {float(position[1]):.1f}, {float(position[2]):.1f}"
    cleaned = clean_text(str(position or ""))
    return cleaned or "unknown"


def normalize_inventory_top_entries(inventory: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    entries: list[tuple[str, int]] = []
    if isinstance(inventory, dict):
        for name, count in inventory.items():
            cleaned_name = clean_text(str(name or ""))
            if not cleaned_name:
                continue
            try:
                count_int = int(count)
            except (TypeError, ValueError):
                continue
            if count_int > 0:
                entries.append((cleaned_name, count_int))
    elif isinstance(inventory, list):
        for row in inventory:
            if not isinstance(row, dict):
                continue
            cleaned_name = clean_text(str(row.get("name") or row.get("item") or ""))
            if not cleaned_name:
                continue
            try:
                count_int = int(row.get("count") or row.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
            if count_int > 0:
                entries.append((cleaned_name, count_int))
    entries.sort(key=lambda item: (-item[1], item[0]))
    return [{"name": name, "count": count} for name, count in entries[:limit]]


def summarize_inventory_top(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "인벤토리 정보 없음"
    return ", ".join(f"{row['name']} x{row['count']}" for row in entries)


def normalize_minecraft_item_name(value: Any) -> str:
    cleaned = clean_text(str(value or "")).strip().lower().replace("minecraft:", "").replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", cleaned)


def humanize_minecraft_item_name(value: Any) -> str:
    item_name = normalize_minecraft_item_name(value)
    if not item_name:
        return "Unknown"
    return item_name.replace("_", " ")


def build_inventory_slot_templates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    armor_labels = ["helmet", "chestplate", "leggings", "boots"]
    for slot_index in range(5, 9):
        rows.append(
            {
                "slot": slot_index,
                "section": "armor",
                "sectionIndex": slot_index - 5,
                "label": armor_labels[slot_index - 5],
                "selected": False,
                "item": None,
                "count": 0,
                "displayName": "",
            }
        )
    for slot_index in range(9, 36):
        rows.append(
            {
                "slot": slot_index,
                "section": "main",
                "sectionIndex": slot_index - 9,
                "label": str(slot_index - 8),
                "selected": False,
                "item": None,
                "count": 0,
                "displayName": "",
            }
        )
    for slot_index in range(36, 45):
        rows.append(
            {
                "slot": slot_index,
                "section": "hotbar",
                "sectionIndex": slot_index - 36,
                "label": str(slot_index - 35),
                "selected": False,
                "item": None,
                "count": 0,
                "displayName": "",
            }
        )
    rows.append(
        {
            "slot": 45,
            "section": "offhand",
            "sectionIndex": 0,
            "label": "offhand",
            "selected": False,
            "item": None,
            "count": 0,
            "displayName": "",
        }
    )
    return rows


def normalize_inventory_slot_entries(raw_slots: Any, *, inventory: Any = None) -> list[dict[str, Any]]:
    templates = build_inventory_slot_templates()
    slot_map = {int(row["slot"]): dict(row) for row in templates}
    filled = False
    if isinstance(raw_slots, list):
        for row in raw_slots:
            if not isinstance(row, dict):
                continue
            try:
                slot_index = int(row.get("slot"))
            except (TypeError, ValueError):
                continue
            target = slot_map.get(slot_index)
            if target is None:
                continue
            item_name = normalize_minecraft_item_name(row.get("item") or row.get("name"))
            try:
                count = int(row.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            display_name = clean_text(str(row.get("displayName") or row.get("display_name") or "")) or humanize_minecraft_item_name(item_name)
            target.update(
                {
                    "item": item_name or None,
                    "count": count if count > 0 else 0,
                    "displayName": display_name if item_name else "",
                    "selected": bool(row.get("selected")),
                }
            )
            filled = True
    if not filled:
        fallback_entries = normalize_inventory_top_entries(inventory, limit=36)
        fallback_slots = [
            slot_map[int(row["slot"])]
            for row in templates
            if row["section"] in {"main", "hotbar"} and int(row["slot"]) in slot_map
        ]
        for target, source in zip(fallback_slots, fallback_entries):
            item_name = normalize_minecraft_item_name(source.get("name"))
            target.update(
                {
                    "item": item_name or None,
                    "count": int(source.get("count") or 0),
                    "displayName": humanize_minecraft_item_name(item_name) if item_name else "",
                    "selected": False,
                }
            )
    return sorted(slot_map.values(), key=lambda row: int(row["slot"]))


def normalize_inventory_used_slots(value: Any, slots: list[dict[str, Any]]) -> int:
    try:
        normalized = int(value)
        if normalized >= 0:
            return normalized
    except (TypeError, ValueError):
        pass
    return sum(1 for row in slots if row.get("section") in {"main", "hotbar"} and row.get("item"))


def extract_minecraft_recent_activity(status: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(status, dict):
        return []
    rows: list[dict[str, str]] = []
    completed = status.get("completed_tasks") if isinstance(status.get("completed_tasks"), list) else []
    failed = status.get("failed_tasks") if isinstance(status.get("failed_tasks"), list) else []
    for task in completed[-3:]:
        label = clean_text(str(task or ""))
        if label:
            rows.append({"kind": "completed", "label": label, "detail": "완료"})
    for item in failed[-3:]:
        if not isinstance(item, dict):
            continue
        label = clean_text(str(item.get("task") or ""))
        detail = clean_text(str(item.get("reason") or item.get("evidence") or "실패"))
        if label:
            rows.append({"kind": "failed", "label": label, "detail": detail or "실패"})
    return rows[-6:]


def _dedupe_activity_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (str(row.get("kind") or ""), str(row.get("label") or ""), str(row.get("detail") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def extract_minecraft_recent_activity_live(status: dict[str, Any] | None, *, base_limit: int = 2) -> list[dict[str, str]]:
    base_rows = extract_minecraft_recent_activity(status)[-max(0, int(base_limit)):]
    if not isinstance(status, dict):
        return base_rows
    rows: list[dict[str, str]] = []
    current_task = clean_text(str(status.get("current_task") or ""))
    current_stage = clean_text(str(status.get("current_task_stage") or status.get("display_stage") or status.get("last_phase") or ""))
    last_progress_message = clean_text(str(status.get("last_progress_message") or ""))
    progress_messages = status.get("progress_messages") if isinstance(status.get("progress_messages"), list) else []
    stability = status.get("stability_signals") if isinstance(status.get("stability_signals"), dict) else {}
    phase_age_seconds = stability.get("phase_age_seconds")
    task_bookkeeping = status.get("current_task_bookkeeping") if isinstance(status.get("current_task_bookkeeping"), dict) else {}
    rollout_iteration = task_bookkeeping.get("rollout_iteration")
    max_rollout_iterations = task_bookkeeping.get("max_rollout_iterations")
    program_name = clean_text(str(task_bookkeeping.get("program_name") or ""))
    verification_state = clean_text(str(task_bookkeeping.get("verification_state") or ""))
    last_search_metrics = status.get("last_search_metrics") if isinstance(status.get("last_search_metrics"), dict) else {}
    world_effect = status.get("last_world_effect_verification") if isinstance(status.get("last_world_effect_verification"), dict) else {}
    critic_result = status.get("last_critic_result") if isinstance(status.get("last_critic_result"), dict) else {}
    if current_task:
        detail_parts: list[str] = []
        if current_stage:
            detail_parts.append(current_stage)
        if isinstance(phase_age_seconds, (int, float)):
            detail_parts.append(f"{max(0.0, float(phase_age_seconds)):.0f}s")
        rows.append({
            "kind": "live",
            "label": current_task,
            "detail": " / ".join(part for part in detail_parts if part) or "running",
        })
    if isinstance(rollout_iteration, int) and isinstance(max_rollout_iterations, int) and max_rollout_iterations > 0:
        rollout_label = f"rollout {rollout_iteration + 1}/{max_rollout_iterations}"
        rollout_detail = verification_state or program_name or "task session"
        rows.append({"kind": "live", "label": rollout_label, "detail": rollout_detail})
    if last_progress_message:
        rows.append({"kind": "live", "label": last_progress_message, "detail": "progress"})
    else:
        for message in progress_messages[-2:]:
            clean = clean_text(str(message or ""))
            if clean:
                rows.append({"kind": "live", "label": clean, "detail": "progress"})
    if last_search_metrics:
        helper = clean_text(str(last_search_metrics.get("helper") or ""))
        goal_type = clean_text(str(last_search_metrics.get("goal_type") or ""))
        completion_reason = clean_text(str(last_search_metrics.get("completion_reason") or last_search_metrics.get("failure_reason") or ""))
        search_label = " ".join(part for part in [helper, goal_type] if part) or "search helper"
        search_detail = completion_reason or "active"
        rows.append({"kind": "live", "label": search_label, "detail": search_detail})
    if world_effect:
        summary = clean_text(str(world_effect.get("summary") or ""))
        reason_code = clean_text(str(world_effect.get("reason_code") or ""))
        outcome = clean_text(str(world_effect.get("outcome") or ""))
        if summary:
            rows.append({
                "kind": "failed" if outcome == "fail" else "live",
                "label": summary,
                "detail": reason_code or outcome or "world effect",
            })
    elif critic_result:
        critique = clean_text(str(critic_result.get("critique") or ""))
        reason_code = clean_text(str(critic_result.get("reason_code") or ""))
        if critique:
            rows.append({"kind": "failed", "label": critique, "detail": reason_code or "critic"})
    rows.extend(base_rows)
    return _dedupe_activity_rows(rows)[:6]
