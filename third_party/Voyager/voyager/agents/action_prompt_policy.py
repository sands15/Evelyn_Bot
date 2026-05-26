from __future__ import annotations

from voyager.agents.survival_signals import hostiles_nearby, inventory_has_food, is_night

BOOTSTRAP_TASK_HINT_TOKENS = ("wood", "log", "planks", "pickaxe", "axe", "food", "shelter", "coal", "torch", "iron")


class ActionPromptPolicy:
    def build_safety_lines(
        self,
        *,
        task,
        time_of_day,
        entities,
        health,
        hunger,
        inventory,
    ):
        lines = []
        hostile_nearby = hostiles_nearby(entities)
        has_food = inventory_has_food(inventory)
        if health is not None and health <= 12:
            lines.append("Health is limited. Avoid combat, avoid fall risk, and prefer the nearest safe progress only.")
        if hostile_nearby:
            lines.append("Hostile mobs are nearby. Disengage instead of fighting unless the task is explicitly survival-critical.")
        if hunger is not None and hunger <= 8 and not has_food:
            lines.append("Hunger is low and no edible food is in inventory. Prefer nearby food or shelter over longer travel.")
        if is_night(time_of_day):
            lines.append("It is night or a dangerous transition period. Keep exploration short and local; shelter is better than long surface travel.")
        if any(token in str(task or "").lower() for token in BOOTSTRAP_TASK_HINT_TOKENS):
            lines.append("For bootstrap tasks, stop once the minimum safe progress is achieved instead of overextending.")
        lowered_task = str(task or "").lower()
        if "food" in lowered_task or "edible" in lowered_task:
            lines.append("Do not treat bot.food or hunger level as proof that edible food is already in inventory. Only actual inventory items count for food possession.")
        if "wood log" in lowered_task or "wood logs" in lowered_task or "_log" in lowered_task:
            lines.append("For wood-search tasks, prefer searchAndHarvest(..., { goalType: \"wood\" }) and recoverToSurface(...) over custom exploreUntil wandering.")
        return lines
