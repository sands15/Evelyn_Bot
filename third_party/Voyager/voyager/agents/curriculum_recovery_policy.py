from __future__ import annotations

from typing import Callable

from voyager.agents.hazard_taxonomy import classify_death_event
from voyager.agents.inventory_planner import InventoryFirstPlanner, InventoryState
from voyager.agents.survival_signals import inventory_has_food, is_night


class CurriculumRecoveryPolicy:
    def __init__(
        self,
        *,
        count_logs: Callable[[dict], int],
        count_planks: Callable[[dict], int],
        has_tool_at_least: Callable[[dict, str, str], bool],
        status_number: Callable[[dict, str, float], float],
        event_age_seconds: Callable[[dict], float | None],
    ):
        self._count_logs = count_logs
        self._count_planks = count_planks
        self._has_tool_at_least = has_tool_at_least
        self._status_number = status_number
        self._event_age_seconds = event_age_seconds

    def fallback_recovery_task(self, inventory):
        planned = InventoryFirstPlanner().fallback_recovery(
            InventoryState.from_observation(inventory=inventory)
        )
        return planned.task, planned.context

    def death_specific_recovery_task(self, last_death_event):
        if not isinstance(last_death_event, dict):
            return None
        hazard = classify_death_event(last_death_event)
        category = str(hazard.get("category") or "general")
        if category == "drowning":
            return (
                "Reach a surface position",
                "Death-derived countermeasure: the last death was water-related. Use recoverToSurface-style behavior immediately, move onto solid ground, avoid deep water routes, surface as soon as submerged, and do not keep working underwater unless the task explicitly requires it.",
            )
        if category == "lava_fire":
            return (
                "Move 24 blocks away from current position",
                "Death-derived countermeasure: the last death was lava or fire related. Back away from exposed lava, avoid mining directly over voids or lava pockets, and secure footing before resuming resource collection.",
            )
        if category == "fall":
            return (
                "Move 24 blocks away from current position",
                "Death-derived countermeasure: the last death was fall-related. Favor flat routes, descend one block at a time, and avoid sprinting near drops until health and terrain are stable.",
            )
        if category == "starvation":
            return (
                "Acquire 1 edible food item",
                "Death-derived countermeasure: the last death was hunger related. Secure edible food before travel, mining, or combat, and keep a safety reserve instead of consuming the last item too late.",
            )
        if category == "hostile":
            return (
                "Establish a lit temporary shelter",
                "Death-derived countermeasure: the last death involved hostile pressure. Re-establish shelter, avoid open combat, and only re-engage after recovering health, food, and a safer position.",
            )
        return None

    def post_death_recovery_task(self, inventory, status, last_death_event=None):
        specific = self.death_specific_recovery_task(last_death_event)
        if specific:
            return specific
        health = self._status_number(status, "health", default=20)
        hunger = self._status_number(status, "food", default=20)
        if health <= 12 or is_night(status):
            return (
                "Establish a lit temporary shelter",
                "Recent death recovery: immediately rebuild a safe position, prefer recoverToSurface-style movement before broader search, avoid combat, and only resume progression after stabilizing health and exposure.",
            )
        if hunger <= 8 and not inventory_has_food(inventory):
            return (
                "Acquire 1 edible food item",
                "Recent death recovery: secure nearby edible food before longer travel or mining, and use surface-oriented food search rather than underground wandering.",
            )
        return self.fallback_recovery_task(inventory)

    def should_force_post_death_recovery(self, last_death_event, inventory, status):
        if not isinstance(last_death_event, dict):
            return False
        age_seconds = self._event_age_seconds(last_death_event)
        if age_seconds is not None and age_seconds > 180:
            return False
        health = self._status_number(status, "health", default=20)
        hunger = self._status_number(status, "food", default=20)
        if health <= 16 or hunger <= 12:
            return True
        if not inventory_has_food(inventory):
            return True
        if not self._has_tool_at_least(inventory, "pickaxe", "wooden"):
            return True
        if self._count_logs(inventory) + self._count_planks(inventory) <= 4:
            return True
        return False
