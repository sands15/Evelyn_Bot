from __future__ import annotations

import re

from voyager.agents.food_signals import edible_food_total
from voyager.agents.inventory_planner import InventoryFirstPlanner, canonical_item_name
from voyager.agents.observation_utils import observe_payload, payload_inventory, payload_status
from voyager.agents.survival_signals import hostiles_nearby, is_night
from voyager.agents.task_contract_policy import (
    ACQUIRE_EDIBLE_FOOD_TASK_PATTERN,
    SMELT_INTO_TASK_PATTERN,
    SMELT_SHORTHAND_TASK_PATTERN,
    parse_smelt_result_task,
)

ORE_TASK_ITEM_MAP = {
    "coal_ore": "coal",
    "iron_ore": "raw_iron",
    "copper_ore": "raw_copper",
}
SURVIVAL_TASK_HINTS = ("shelter", "retreat", "safe", "food", "eat", "cook")
FOOD_SOURCE_BLOCK_NAMES = (
    "wheat",
    "carrots",
    "potatoes",
    "beetroots",
    "melon",
    "pumpkin",
    "sweet_berry_bush",
)
FOOD_SOURCE_ENTITY_NAMES = ("cow", "pig", "chicken", "sheep", "rabbit")
SHELTER_PLACEMENT_BLOCK_HINTS = (
    "planks",
    "cobblestone",
    "torch",
    "door",
    "fence",
    "wall",
    "slab",
    "stairs",
)
SURFACE_BLOCK_HINTS = {
    "grass_block",
    "dirt",
    "sand",
    "red_sand",
    "snow",
    "snow_block",
    "short_grass",
    "tall_grass",
    "wildflowers",
    "oak_log",
    "birch_log",
    "spruce_log",
    "jungle_log",
    "acacia_log",
    "dark_oak_log",
    "mangrove_log",
    "cherry_log",
    "oak_leaves",
    "birch_leaves",
    "spruce_leaves",
    "jungle_leaves",
    "acacia_leaves",
    "dark_oak_leaves",
    "mangrove_leaves",
    "cherry_leaves",
}
def _has_recent_shelter_placement(events):
    if not events:
        return False
    for event_type, event in events:
        if event_type != "onSave" or not isinstance(event, dict):
            continue
        save_name = str(event.get("onSave") or "").lower()
        if not save_name.endswith("_placed"):
            continue
        block_name = save_name[: -len("_placed")]
        if any(token in block_name for token in SHELTER_PLACEMENT_BLOCK_HINTS):
            return True
    return False


def _has_constructed_shelter_material_nearby(payload):
    voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
    return any(
        any(token in str(block).lower() for token in SHELTER_PLACEMENT_BLOCK_HINTS)
        for block in voxels
    )


def _surface_like(payload):
    voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
    nearby_blocks = payload.get("nearby_blocks") if isinstance(payload.get("nearby_blocks"), list) else []
    voxel_names = {str(block).lower() for block in [*voxels, *nearby_blocks]}
    return any(block_name in voxel_names for block_name in SURFACE_BLOCK_HINTS)


def _has_food_source_nearby(payload, inventory=None):
    voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
    voxel_names = {str(block).lower() for block in voxels}
    if any(block_name in voxel_names for block_name in FOOD_SOURCE_BLOCK_NAMES):
        return True
    if "farmland" in voxel_names and isinstance(inventory, dict):
        if any("seed" in str(item).lower() for item in inventory):
            return True

    status = payload_status(payload)
    entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
    return any(
        any(food_entity in str(entity_name).lower() for food_entity in FOOD_SOURCE_ENTITY_NAMES)
        for entity_name in entities.keys()
    )


class CriticOutcomePolicy:
    def _count_inventory_item(self, inventory, item_name):
        if not isinstance(inventory, dict):
            return 0
        value = inventory.get(item_name, 0)
        try:
            return int(value)
        except Exception:
            return 0

    def _last_observation(self, events=None):
        if not events:
            return {}
        payload = events[-1][1]
        return payload if isinstance(payload, dict) else {}

    def _result(self, success, reason_code, critique, source, evidence=None):
        return {
            "success": bool(success),
            "reason_code": str(reason_code or "unspecified").strip().lower(),
            "critique": str(critique or "").strip(),
            "source": source,
            "evidence": evidence if isinstance(evidence, dict) else {},
        }

    def _evaluate_chest_open_result(self, task_text, events=None):
        lowered_task = str(task_text or "").strip().lower()
        if "open" not in lowered_task or "chest" not in lowered_task:
            return None

        last_observation = self._last_observation(events)
        interaction = last_observation.get("voyagerContainerInteraction")
        if not isinstance(interaction, dict):
            interaction = {}

        kind = str(interaction.get("kind", "")).lower()
        if kind and kind != "chest":
            return self._result(False, "chest_interaction_kind_mismatch", f"Expected chest interaction but observed {kind}.", "outcome_policy")

        if bool(interaction.get("blockedAbove")):
            blocked_by = interaction.get("blockedBy") or "a solid block"
            return self._result(False, "chest_blocked_above", f"Chest is blocked above by {blocked_by}.", "outcome_policy")

        if bool(interaction.get("interacted")):
            return self._result(True, "chest_interaction_completed", "Chest helper completed a chest interaction in this step.", "outcome_policy")

        if bool(interaction.get("opened")) and not interaction.get("error"):
            return self._result(True, "chest_window_opened", "Chest interaction window opened successfully.", "outcome_policy")

        if interaction.get("error"):
            return self._result(False, "chest_interaction_error", f"Chest interaction failed: {interaction['error']}", "outcome_policy")

        window_result = last_observation.get("voyagerWindowResult")
        if isinstance(window_result, dict):
            label = str(window_result.get("label", "")).lower()
            status = str(window_result.get("status", "")).lower()
            if "chest" in label and status in {"opened", "closed", "success"}:
                return self._result(True, "chest_window_opened", "Chest interaction window opened successfully.", "outcome_policy")

        return None

    def _food_source_success_override(self, task_text, inventory, events=None):
        lowered_task = str(task_text or "").strip().lower()
        edible_contract_match = ACQUIRE_EDIBLE_FOOD_TASK_PATTERN.match(task_text)
        if "food source" not in lowered_task and "find food" not in lowered_task and not edible_contract_match:
            return None

        edible_total = edible_food_total(inventory)
        if edible_total > 0:
            return self._result(True, "food_already_in_inventory", "Inventory already contains edible food.", "outcome_policy")

        # Acquire-N-edible-food contracts require actual edible inventory.
        # Craftable wheat or nearby food sources are useful leads, but they do not
        # satisfy the contract until the inventory really holds edible food.
        if edible_contract_match:
            return None
        if self._count_inventory_item(inventory, "wheat") >= 3:
            return self._result(True, "bread_craftable_from_wheat", "Inventory has enough wheat to craft bread.", "outcome_policy")

        payload = observe_payload(events)
        search_execution = payload.get("searchExecution") if isinstance(payload.get("searchExecution"), dict) else {}
        if (
            str(search_execution.get("goalType") or "").lower() == "food"
            and str(search_execution.get("status") or "").lower() == "success"
            and str(search_execution.get("reason") or "").lower() in {"food_candidate_reached", "candidate_found"}
        ):
            return self._result(True, "food_candidate_reached", "Food search reached a food candidate.", "outcome_policy")

        if _has_food_source_nearby(payload, inventory):
            return self._result(True, "food_source_visible_nearby", "Nearby observation contains a viable food source or farmable food setup.", "outcome_policy")
        return None

    def _shelter_success_override(self, task_text, inventory, events=None):
        lowered_task = str(task_text or "").strip().lower()
        if "shelter" not in lowered_task:
            return None
        payload = observe_payload(events)
        status = payload_status(payload)
        entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
        health = status.get("health")
        if health is None or float(health) < 10:
            return None
        if hostiles_nearby(entities):
            return None
        if _has_recent_shelter_placement(events):
            return self._result(True, "recent_shelter_placement", "A shelter-related block was placed in this rollout and no immediate hostiles remain; count the temporary shelter as established.", "outcome_policy")
        if _has_constructed_shelter_material_nearby(payload):
            return self._result(True, "constructed_shelter_visible", "Nearby constructed shelter materials are present and no immediate hostiles remain; count the temporary shelter as established.", "outcome_policy")
        return None

    def _surface_position_success_override(self, task_text, events=None):
        if str(task_text or "").strip() != "Reach a surface position":
            return None
        payload = observe_payload(events)
        if _surface_like(payload):
            return self._result(True, "surface_position_visible", "Nearby observation looks surface-like after the recovery step.", "outcome_policy")
        return None

    def _inventory_success_override(self, task, inventory, events=None, allow_shelter_override=True):
        task_text = str(task or "").strip()
        if InventoryFirstPlanner().is_task_satisfied(task_text, inventory):
            return self._result(True, "inventory_or_capability_satisfied", "Inventory/capability planner already satisfies the task.", "outcome_policy")

        smelt_result_task = parse_smelt_result_task(task_text)
        if smelt_result_task is not None:
            amount = int(smelt_result_task["amount"])
            inventory_target = canonical_item_name(smelt_result_task["output_target"])
            current = self._count_inventory_item(inventory, inventory_target)
            if current >= amount:
                return self._result(True, "inventory_threshold_met", f"Inventory already satisfies the smelt-result task with {current} {inventory_target}.", "outcome_policy")

        match = re.fullmatch(r"(Obtain|Have|Craft|Mine)\s+(\d+)\s+([a-z0-9_ ]+)", task_text)
        if match:
            verb, amount_text, target = match.groups()
            amount = int(amount_text)
            inventory_target = target.strip().lower().replace(" ", "_")
            if verb == "Mine" and inventory_target in ORE_TASK_ITEM_MAP:
                inventory_target = ORE_TASK_ITEM_MAP[inventory_target]
            current = self._count_inventory_item(inventory, inventory_target)
            if current >= amount:
                return self._result(True, "inventory_threshold_met", f"Inventory already satisfies the task with {current} {inventory_target}.", "outcome_policy")
        smelt_match = SMELT_INTO_TASK_PATTERN.fullmatch(task_text)
        if smelt_match:
            amount_text, _input_target, output_target = smelt_match.groups()
            amount = int(amount_text)
            inventory_target = output_target.strip().lower().replace(" ", "_")
            current = self._count_inventory_item(inventory, inventory_target)
            if current >= amount:
                return self._result(True, "inventory_threshold_met", f"Inventory already satisfies the smelt task with {current} {inventory_target}.", "outcome_policy")
        smelt_shorthand_match = SMELT_SHORTHAND_TASK_PATTERN.fullmatch(task_text)
        if smelt_shorthand_match:
            amount_text, output_target = smelt_shorthand_match.groups()
            amount = int(amount_text)
            inventory_target = output_target.strip().lower().replace(" ", "_")
            current = self._count_inventory_item(inventory, inventory_target)
            if current >= amount:
                return self._result(True, "inventory_threshold_met", f"Inventory already satisfies the shorthand smelt task with {current} {inventory_target}.", "outcome_policy")
        food_source_override = self._food_source_success_override(task_text, inventory, events=events)
        if food_source_override is not None:
            return food_source_override
        surface_override = self._surface_position_success_override(task_text, events=events)
        if surface_override is not None:
            return surface_override
        if allow_shelter_override:
            shelter_override = self._shelter_success_override(task_text, inventory, events=events)
            if shelter_override is not None:
                return shelter_override
        return self._evaluate_chest_open_result(task_text, events=events)

    def _safety_failure_override(self, task, events=None):
        payload = observe_payload(events)
        status = payload_status(payload)
        task_text = str(task or "").strip().lower()
        if any(token in task_text for token in SURVIVAL_TASK_HINTS):
            return None
        health = status.get("health")
        hunger = status.get("food")
        entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
        hostile_nearby = hostiles_nearby(entities)
        if health is not None and float(health) <= 4:
            return self._result(False, "safety_override", "Safety override: ending health is critically low, so this step should not count as stable progress. Recover first.", "outcome_policy")
        if hunger is not None and float(hunger) <= 3:
            return self._result(False, "safety_override", "Safety override: ending hunger is critically low, so progression should pause for recovery.", "outcome_policy")
        if hostile_nearby and is_night(status) and health is not None and float(health) <= 8:
            return self._result(False, "safety_override", "Safety override: hostiles remain nearby at night while health is low; prioritize shelter or retreat before counting progress.", "outcome_policy")
        return None

    def evaluate_preflight(self, task, events=None):
        inventory = payload_inventory(observe_payload(events))
        return self._inventory_success_override(
            task,
            inventory,
            events=events,
            allow_shelter_override=False,
        )

    def evaluate_post_action(self, task, events=None):
        inventory = payload_inventory(observe_payload(events))
        override = self._inventory_success_override(task, inventory, events=events)
        if override is not None:
            return override
        return self._safety_failure_override(task, events=events)
