from __future__ import annotations

from typing import Any, Callable

from voyager.agents.inventory_planner import has_tool_at_least as planner_has_tool_at_least
from voyager.agents.observation_utils import observe_payload, payload_dict, payload_inventory, payload_status
from voyager.agents.survival_signals import hostiles_nearby, inventory_has_food, is_night

SURVIVAL_TASK_HINTS = (
    "shelter",
    "retreat",
    "safe",
    "food",
    "eat",
    "cook",
    "coal",
    "torch",
    "iron",
    "wood",
    "log",
    "planks",
    "stick",
    "crafting table",
    "crafting_table",
    "wooden pickaxe",
    "stone pickaxe",
    "stone axe",
    "cobblestone",
)


def _task_text(task):
    return str(task or "").strip().lower()


def _status_number(status, key, default=0):
    try:
        return float(status.get(key, default))
    except Exception:
        return float(default)


def _inv_count(inventory, item_name):
    return int((inventory or {}).get(item_name) or 0)


def _count_planks(inventory):
    return sum(
        int(count or 0)
        for name, count in (inventory or {}).items()
        if isinstance(name, str) and name.endswith("_planks")
    )


def _count_logs(inventory):
    return sum(
        int(count or 0)
        for name, count in (inventory or {}).items()
        if isinstance(name, str) and (name.endswith("_log") or name.endswith("_stem"))
    )


def _count_generic_stone(inventory):
    return sum(
        _inv_count(inventory, name)
        for name in ["cobblestone", "cobbled_deepslate", "blackstone"]
    )


def _has_any(inventory, *names):
    return any(_inv_count(inventory, name) > 0 for name in names)


def _has_tool_at_least(inventory, tool_type, minimum_material):
    return planner_has_tool_at_least(inventory or {}, tool_type, minimum_material)


def _has_iron_capability(inventory):
    return any(
        _has_tool_at_least(inventory, tool_type, "iron")
        for tool_type in ("pickaxe", "axe", "sword", "shovel", "hoe")
    )


def _has_iron_progress_material(inventory):
    return _has_any(inventory, "raw_iron", "iron_ingot", "iron_ore")


class EarlyGameProgressionPolicy:
    def __init__(
        self,
        *,
        get_completed_tasks: Callable[[], list[str]] | None = None,
        get_nearby_progression_candidates: Callable[[Any], list[tuple[str, str]]] | None = None,
    ):
        self._get_completed_tasks = get_completed_tasks or (lambda: [])
        self._get_nearby_progression_candidates = get_nearby_progression_candidates or (lambda events: [])

    def is_bootstrap_or_survival_task(self, task):
        task_text = _task_text(task)
        return any(token in task_text for token in SURVIVAL_TASK_HINTS)

    def _stage3_prerequisite(self, inventory, events):
        has_food = inventory_has_food(inventory)
        has_iron_capability = _has_iron_capability(inventory)

        if not has_food:
            nearby_candidates = self._get_nearby_progression_candidates(events) or []
            for next_task, next_context in nearby_candidates:
                if "food" in _task_text(next_task):
                    return (
                        next_task,
                        next_context + " Return to broader progression after stabilizing food.",
                    )
            return (
                "Acquire 1 edible food item",
                "Stability stage prerequisite: secure renewable or nearby edible food before deeper progression.",
            )

        if has_iron_capability:
            return None

        if _inv_count(inventory, "iron_ingot") >= 3:
            if _inv_count(inventory, "stick") >= 2:
                return (
                    "Craft 1 iron_pickaxe",
                    "Stability stage prerequisite: enough iron and sticks are available, so upgrade to an iron pickaxe before more mining.",
                )
            return (
                "Craft 2 stick",
                "Stability stage prerequisite: prepare sticks so the available iron can become an iron pickaxe.",
            )

        if _inv_count(inventory, "raw_iron") > 0 or _inv_count(inventory, "iron_ore") > 0:
            if not _has_any(inventory, "furnace") and _count_generic_stone(inventory) >= 8:
                return (
                    "Craft 1 furnace",
                    "Stability stage prerequisite: raw iron is available but a furnace is missing.",
                )
            if _has_any(inventory, "furnace") and not _has_any(inventory, "coal", "charcoal"):
                nearby_candidates = self._get_nearby_progression_candidates(events) or []
                for next_task, next_context in nearby_candidates:
                    if "coal" in _task_text(next_task):
                        return (
                            next_task,
                            next_context + " Use the fuel for iron smelting afterward.",
                        )
                return (
                    "Obtain 4 coal",
                    "Stability stage prerequisite: raw iron is ready but fuel is missing for smelting.",
                )
            if _has_any(inventory, "furnace") and _has_any(inventory, "coal", "charcoal"):
                raw_iron_total = max(_inv_count(inventory, "raw_iron"), _inv_count(inventory, "iron_ore"))
                return (
                    f"Smelt {raw_iron_total} raw_iron into iron_ingots",
                    "Stability stage prerequisite: convert the available raw iron into usable iron ingots.",
                )
            return (
                "Craft 1 furnace",
                "Stability stage prerequisite: raw iron exists, so prepare a furnace path before unrelated tasks.",
            )

        return (
            "Obtain 8 raw_iron",
            "Stability stage prerequisite: food is secured but the first iron upgrade path has not started yet.",
        )

    def infer_stage(self, events):
        payload = observe_payload(events)
        inventory = payload_inventory(payload)
        logs = _count_logs(inventory)
        planks = _count_planks(inventory)
        sticks = _inv_count(inventory, "stick")
        stone = _count_generic_stone(inventory)
        has_wooden_pickaxe = _has_tool_at_least(inventory, "pickaxe", "wooden")
        has_stone_pickaxe = _has_tool_at_least(inventory, "pickaxe", "stone")
        has_stone_axe = _has_tool_at_least(inventory, "axe", "stone")
        has_food = inventory_has_food(inventory)
        has_iron_progress = _has_iron_progress_material(inventory) or _has_iron_capability(inventory)
        has_beyond_wood_progress = has_stone_pickaxe or has_stone_axe or stone >= 6 or has_food or has_iron_progress
        if not has_wooden_pickaxe and not has_beyond_wood_progress:
            if logs <= 0 and planks <= 0 and sticks <= 0:
                return 0
            return 1
        if not (has_stone_pickaxe and has_stone_axe):
            return 2
        if self._stage3_prerequisite(inventory, events) is not None:
            return 3
        return 4

    def survival_override(self, events):
        payload = observe_payload(events)
        status = payload_status(payload)
        inventory = payload_inventory(payload)
        entities = payload_dict(status, "entities")
        health = _status_number(status, "health", default=20)
        hunger = _status_number(status, "food", default=20)
        hostile_nearby = hostiles_nearby(entities)
        has_food = inventory_has_food(inventory)
        completed_tasks = self._get_completed_tasks() or []
        recent_shelter_success = bool(
            completed_tasks
            and completed_tasks[-1] in {"Build a temporary shelter", "Establish a lit temporary shelter"}
        )
        if recent_shelter_success and not hostile_nearby and health >= 16:
            if hunger <= 12 and not has_food:
                return (
                    "Acquire 1 edible food item",
                    "Shelter exit override: safety is restored, so leave shelter mode and secure nearby food before resuming broader progression.",
                )
            return None
        if health <= 6 or (health <= 10 and hostile_nearby):
            return (
                "Establish a lit temporary shelter",
                "Low health override: immediately get to safety, block exposure, and avoid combat before resuming progression.",
            )
        if hunger <= 8 and not has_food:
            return (
                "Acquire 1 edible food item",
                "Low hunger override: prioritize obtaining nearby edible food before any ore processing or exploration. Keep the search local and safe.",
            )
        if is_night(status) and (hostile_nearby or health <= 10):
            return (
                "Establish a lit temporary shelter",
                "Night danger override: secure a safe shelter before other progression tasks and avoid long surface travel.",
            )
        if hostile_nearby and health <= 14:
            return (
                "Move 24 blocks away from current position",
                "Hostile danger override: disengage and move to a safe position before continuing task progression.",
            )
        return None

    def guard_task(self, task, context, events):
        stage = self.infer_stage(events)
        task_text = _task_text(task)
        payload = observe_payload(events)
        inventory = payload_inventory(payload)
        stage3_prerequisite = self._stage3_prerequisite(inventory, events) if stage == 3 else None

        survival_override = self.survival_override(events)
        if survival_override:
            next_task, next_context = survival_override
            return {
                "task": next_task,
                "context": next_context,
                "stage": stage,
                "changed": True,
                "kind": "survival_override",
            }

        def replace(next_task, next_context):
            return {
                "task": next_task,
                "context": next_context,
                "stage": stage,
                "changed": next_task != task or next_context != context,
                "kind": "guardrail",
            }

        is_smelt = task_text.startswith("smelt ") or " smelt " in task_text
        mentions_copper = "copper" in task_text
        mentions_iron = "iron" in task_text
        mentions_logs = "log" in task_text or "wood" in task_text
        mentions_stone = "stone" in task_text or "cobblestone" in task_text or "deepslate" in task_text or "blackstone" in task_text
        mentions_food = "food" in task_text or "eat" in task_text or "cook" in task_text or "animal" in task_text or "beef" in task_text or "pork" in task_text or "chicken" in task_text or "mutton" in task_text
        mentions_basic_crafting = any(
            token in task_text
            for token in ("stick", "planks", "crafting table", "crafting_table", "furnace")
        )

        if mentions_copper and stage < 4:
            if stage <= 1:
                return replace(
                    "Craft 1 wooden_pickaxe",
                    "Copper processing is blocked during early bootstrap. First secure a crafting table and wooden pickaxe.",
                )
            if stage == 2:
                return replace(
                    "Craft 1 stone_pickaxe",
                    "Copper processing is blocked until stone tools are ready. Upgrade tools first.",
                )
            return replace(
                "Acquire 1 edible food item",
                "Copper processing is optional in early progression. Stabilize food and iron progression before processing copper.",
            )

        if is_smelt and stage < 4:
            if mentions_iron and stage == 3 and _has_any(inventory, "furnace") and (_has_any(inventory, "coal", "charcoal") or _has_any(inventory, "raw_iron", "iron_ore")):
                return {"task": task, "context": context, "stage": stage, "changed": False, "kind": "none"}
            if mentions_iron and stage == 3:
                if not _has_any(inventory, "furnace") and _count_generic_stone(inventory) >= 8:
                    return replace(
                        "Craft 1 furnace",
                        "Iron smelting is the right next progression, but a furnace is missing. Craft the furnace locally from available stone before returning to smelting.",
                    )
                if not _has_any(inventory, "coal", "charcoal"):
                    nearby_candidates = self._get_nearby_progression_candidates(events) or []
                    for next_task, next_context in nearby_candidates:
                        if "coal" in _task_text(next_task):
                            return replace(
                                next_task,
                                next_context + " Use the fuel for iron smelting afterward.",
                            )
                    return replace(
                        "Obtain 4 coal",
                        "Iron smelting is blocked by missing fuel. Secure a small coal reserve before returning to raw iron processing.",
                    )
            if stage <= 1:
                return replace(
                    "Obtain 8 wood logs",
                    "Smelting is blocked during early bootstrap. Gather wood and basic crafting materials first.",
                )
            if stage == 2:
                return replace(
                    "Craft 1 stone_axe",
                    "Smelting is blocked until stone tool progression is finished.",
                )
            return replace(
                "Acquire 1 edible food item",
                "Smelting is blocked until survival and iron progression are stable.",
            )

        if "wooden axe" in task_text and stage < 3:
            return replace(
                "Craft 1 wooden_pickaxe",
                "Do not branch into a wooden axe during early bootstrap; prioritize pickaxe and stone unlock first.",
            )

        if stage == 0:
            if not mentions_logs:
                return replace(
                    "Mine 1 wood log",
                    "Early bootstrap stage: first obtain wood before considering other tasks.",
                )
        elif stage == 1:
            if not any(token in task_text for token in ["wooden pickaxe", "crafting table", "crafting_table", "planks", "sticks", "wood log", "wood logs"]):
                if _count_planks(inventory) < 4:
                    return replace(
                        "Craft 8 wood planks",
                        "Basic tool bootstrap stage: convert wood into planks before exploring broader tasks.",
                    )
                return replace(
                    "Craft 1 wooden_pickaxe",
                    "Basic tool bootstrap stage: finish wooden pickaxe before any side goals.",
                )
        elif stage == 2:
            if not (mentions_stone or mentions_basic_crafting or "stone pickaxe" in task_text or "stone axe" in task_text):
                if _count_generic_stone(inventory) < 6:
                    return replace(
                        "Mine 6 cobblestone",
                        "Stone unlock stage: secure the first stone batch before optional tasks.",
                    )
                if not _has_tool_at_least(inventory, "pickaxe", "stone"):
                    return replace(
                        "Craft 1 stone_pickaxe",
                        "Stone unlock stage: craft a stone pickaxe before optional tasks.",
                    )
                return replace(
                    "Craft 1 stone_axe",
                    "Stone unlock stage: craft a stone axe before optional tasks.",
                )
        elif stage == 3:
            if stage3_prerequisite is not None:
                prerequisite_task, prerequisite_context = stage3_prerequisite
                prerequisite_text = _task_text(prerequisite_task)
                prerequisite_mentions_iron = "iron" in prerequisite_text or "coal" in prerequisite_text or "furnace" in prerequisite_text
                requested_matches_prerequisite = (
                    task_text == prerequisite_text
                    or (mentions_food and "food" in prerequisite_text)
                    or (mentions_iron and prerequisite_mentions_iron)
                    or (is_smelt and "smelt " in prerequisite_text)
                    or (mentions_basic_crafting and any(token in prerequisite_text for token in ("stick", "furnace", "craft")))
                )
                if not requested_matches_prerequisite:
                    return replace(prerequisite_task, prerequisite_context)
            allowed = (
                mentions_food
                or mentions_iron
                or is_smelt
                or mentions_basic_crafting
                or "furnace" in task_text
                or "torch" in task_text
                or "coal" in task_text
                or "shelter" in task_text
            )
            if not allowed:
                return replace(
                    stage3_prerequisite[0] if stage3_prerequisite is not None else "Acquire 1 edible food item",
                    stage3_prerequisite[1] if stage3_prerequisite is not None else "Stability stage: prioritize food, fuel, shelter, and first iron progression before unrelated tasks.",
                )

        return {
            "task": task,
            "context": context,
            "stage": stage,
            "changed": False,
            "kind": "none",
        }
