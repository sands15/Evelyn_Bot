from __future__ import annotations

from voyager.agents.food_signals import inventory_has_edible_food

HOSTILE_ENTITY_NAMES = ("zombie", "skeleton", "creeper", "spider", "drowned", "witch", "enderman")


def hostiles_nearby(entities):
    return any(
        any(hostile in str(name).lower() for hostile in HOSTILE_ENTITY_NAMES)
        for name in (entities or {}).keys()
    )


def inventory_has_food(inventory):
    return inventory_has_edible_food(inventory)


def is_night(time_of_day_or_status):
    if isinstance(time_of_day_or_status, dict):
        time_of_day_or_status = time_of_day_or_status.get("timeOfDay")
    return str(time_of_day_or_status or "").strip().lower() in {"night", "midnight", "sunset", "sunrise"}
