from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .control_page_minecraft_snapshot_runtime import (
    ControlPageBackgroundTasksRuntimeDeps,
    ControlPageMinecraftSnapshotRuntimeDeps,
)
from .minecraft_live_state_runtime import ControlPageMinecraftLiveSnapshotRuntimeDeps
from .minecraft_runtime_snapshot import (
    attach_minecraft_runtime_snapshot,
    extract_minecraft_recent_activity_live,
    format_position_short,
    merge_voyager_status_into_state,
    normalize_inventory_slot_entries,
    normalize_inventory_top_entries,
    normalize_inventory_used_slots,
    summarize_inventory_top,
)
from .text import clean_text


@dataclass(frozen=True)
class ControlPageSnapshotDependencyCompositionDeps:
    control_page: Callable[[], Any]
    get_minecraft_client: Callable[[], Any]
    observe_live_minecraft_state: Callable[..., Any]
    now: Callable[[], float]
    stale_after_sec: float
    expired_after_sec: float
    cache: Any
    get_refresh_task: Callable[[], Any]
    set_refresh_task: Callable[[Any], None]
    get_lock: Callable[[], Any]
    set_lock: Callable[[Any], None]
    lock_factory: Callable[..., Any]
    create_task: Callable[..., Any]
    wait_for: Callable[..., Any]
    get_snapshot: Callable[..., Any]
    timeout_sec: float
    get_poll_task: Callable[[], Any]
    set_poll_task: Callable[[Any], None]
    get_runtime_services_refresh_task: Callable[[], Any]
    set_runtime_services_refresh_task: Callable[[Any], None]
    ensure_minecraft_snapshot: Callable[..., Any]
    sleep: Callable[..., Any]
    log: Callable[..., Any] = print


class ControlPageSnapshotDependencyComposition:
    """Builds live, cached, and background Minecraft snapshot contracts."""

    def __init__(self, deps: ControlPageSnapshotDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_control_page_minecraft_live_snapshot_runtime_deps(
        self,
    ) -> ControlPageMinecraftLiveSnapshotRuntimeDeps:
        deps = self.deps
        return ControlPageMinecraftLiveSnapshotRuntimeDeps(
            get_minecraft_client=deps.get_minecraft_client,
            observe_live_minecraft_state=deps.observe_live_minecraft_state,
            merge_voyager_status_into_state=merge_voyager_status_into_state,
            normalize_inventory_top_entries=normalize_inventory_top_entries,
            summarize_inventory_top=summarize_inventory_top,
            normalize_inventory_slot_entries=normalize_inventory_slot_entries,
            normalize_inventory_used_slots=normalize_inventory_used_slots,
            extract_recent_activity=extract_minecraft_recent_activity_live,
            format_position_short=format_position_short,
            attach_minecraft_runtime_snapshot=attach_minecraft_runtime_snapshot,
            clean_text=clean_text,
            now=deps.now,
            stale_after_sec=deps.stale_after_sec,
            expired_after_sec=deps.expired_after_sec,
        )

    def build_control_page_minecraft_snapshot_runtime_deps(
        self,
    ) -> ControlPageMinecraftSnapshotRuntimeDeps:
        deps = self.deps
        return ControlPageMinecraftSnapshotRuntimeDeps(
            cache=deps.cache,
            get_refresh_task=deps.get_refresh_task,
            set_refresh_task=deps.set_refresh_task,
            get_lock=deps.get_lock,
            set_lock=deps.set_lock,
            lock_factory=deps.lock_factory,
            create_task=deps.create_task,
            wait_for=deps.wait_for,
            get_snapshot=deps.get_snapshot,
            clean_text=clean_text,
            timeout_sec=deps.timeout_sec,
        )

    def build_control_page_background_tasks_runtime_deps(
        self,
    ) -> ControlPageBackgroundTasksRuntimeDeps:
        deps = self.deps
        control_page = deps.control_page()
        return ControlPageBackgroundTasksRuntimeDeps(
            get_poll_task=deps.get_poll_task,
            set_poll_task=deps.set_poll_task,
            get_snapshot_refresh_task=deps.get_refresh_task,
            set_snapshot_refresh_task=deps.set_refresh_task,
            get_runtime_services_refresh_task=deps.get_runtime_services_refresh_task,
            set_runtime_services_refresh_task=deps.set_runtime_services_refresh_task,
            create_task=deps.create_task,
            select_control_page_guild=control_page.select_guild,
            ensure_minecraft_snapshot=deps.ensure_minecraft_snapshot,
            sleep=deps.sleep,
            log=deps.log,
            refresh_interval_sec=deps.stale_after_sec,
        )


__all__ = [
    "ControlPageSnapshotDependencyComposition",
    "ControlPageSnapshotDependencyCompositionDeps",
]
