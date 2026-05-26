from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from voyager.agents.food_signals import inventory_has_edible_food
from voyager.agents.objective_templates import OBJECTIVE_TEMPLATES
from voyager.agents.task_contract_policy import parse_smelt_result_task


"""
Ideal target shape for Minecraft task selection:

1. Normalize live observation into an InventoryState.
2. Derive capabilities from inventory, not literal item names.
   Example: diamond_pickaxe satisfies every task that needs a stone or iron pickaxe.
3. Resolve deterministic progression with a recipe/capability planner before any LLM.
4. Ask the LLM only when deterministic state does not imply a clear next task
   or when exploration/novelty selection is genuinely open-ended.

This module is the start of that boundary. CurriculumAgent should call this planner
instead of accumulating one-off task exceptions in curriculum.py.
"""


TOOL_MATERIAL_TIERS = {
    "wooden": 1,
    "golden": 1,
    "stone": 2,
    "iron": 3,
    "diamond": 4,
    "netherite": 5,
}
ARMOR_MATERIAL_TIERS = {
    "leather": 1,
    "golden": 1,
    "chainmail": 2,
    "iron": 3,
    "diamond": 4,
    "netherite": 5,
    "turtle": 2,
}
TOOL_SUFFIXES = ("pickaxe", "axe", "shovel", "hoe", "sword")
ARMOR_SUFFIXES = ("helmet", "chestplate", "leggings", "boots")
@dataclass(frozen=True)
class PlannedTask:
    task: str
    context: str
    reason: str
    objective: str | None = None
    capability: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {
            "task": self.task,
            "context": self.context,
            "reason": self.reason,
        }
        if self.objective:
            payload["objective"] = self.objective
        if self.capability:
            payload["capability"] = self.capability
        return payload


@dataclass(frozen=True)
class RecipeIngredient:
    options: tuple[str, ...]
    count: int = 1


@dataclass(frozen=True)
class Recipe:
    output: str
    count: int
    ingredients: tuple[RecipeIngredient, ...]
    needs_table: bool = False


@dataclass(frozen=True)
class InventoryState:
    inventory: dict[str, int]
    health: float = 20.0
    hunger: float = 20.0
    nearby_blocks: frozenset[str] = frozenset()

    @classmethod
    def from_observation(
        cls,
        *,
        inventory: dict[str, Any] | None,
        status: dict[str, Any] | None = None,
        nearby_blocks: list[str] | set[str] | tuple[str, ...] | None = None,
    ) -> "InventoryState":
        normalized_inventory: dict[str, int] = {}
        for name, count in (inventory or {}).items():
            canonical = canonical_item_name(name)
            normalized_inventory[canonical] = normalized_inventory.get(canonical, 0) + _safe_int(count)
        return cls(
            inventory=normalized_inventory,
            health=_status_number(status, "health", 20.0),
            hunger=_status_number(status, "food", 20.0),
            nearby_blocks=frozenset(str(block) for block in (nearby_blocks or []) if block),
        )

    def count(self, item_name: str) -> int:
        return int(self.inventory.get(canonical_item_name(item_name)) or 0)

    def has_any(self, *item_names: str) -> bool:
        return any(self.count(item_name) > 0 for item_name in item_names)

    @property
    def planks(self) -> int:
        return sum(count for name, count in self.inventory.items() if name.endswith("_planks"))

    @property
    def logs(self) -> int:
        return sum(
            count
            for name, count in self.inventory.items()
            if name.endswith("_log") or name.endswith("_stem")
        )

    @property
    def generic_stone(self) -> int:
        return sum(self.count(name) for name in ("cobblestone", "cobbled_deepslate", "blackstone"))

    @property
    def has_food(self) -> bool:
        return inventory_has_edible_food(self.inventory)

    def has_tool_at_least(self, tool_type: str, minimum_material: str) -> bool:
        return count_capability_at_least(self.inventory, "tool", tool_type, minimum_material) > 0

    def has_armor_at_least(self, armor_slot: str, minimum_material: str) -> bool:
        return count_capability_at_least(self.inventory, "armor", armor_slot, minimum_material) > 0


class RecipeCatalog:
    def __init__(self, recipes_by_output: dict[str, tuple[Recipe, ...]]):
        self.recipes_by_output = recipes_by_output

    @classmethod
    @lru_cache(maxsize=8)
    def load_default(cls, version: str = "1.21.1") -> "RecipeCatalog":
        data_dir = _find_minecraft_data_dir(version)
        if data_dir is None:
            return cls({})
        items_path = data_dir / "items.json"
        recipes_path = data_dir / "recipes.json"
        try:
            items = json.loads(items_path.read_text(encoding="utf-8"))
            raw_recipes = json.loads(recipes_path.read_text(encoding="utf-8"))
        except Exception:
            return cls({})

        id_to_name = {
            int(item["id"]): str(item["name"])
            for item in items
            if isinstance(item, dict) and "id" in item and "name" in item
        }
        recipes_by_output: dict[str, list[Recipe]] = {}
        for output_id, recipe_entries in raw_recipes.items():
            output_name = id_to_name.get(_safe_int(output_id, -1))
            if not output_name or not isinstance(recipe_entries, list):
                continue
            for entry in recipe_entries:
                recipe = cls._parse_recipe(output_name, entry, id_to_name)
                if recipe:
                    recipes_by_output.setdefault(output_name, []).append(recipe)
        return cls({name: tuple(recipes) for name, recipes in recipes_by_output.items()})

    @staticmethod
    def _parse_recipe(output_name: str, entry: dict[str, Any], id_to_name: dict[int, str]) -> Recipe | None:
        if not isinstance(entry, dict):
            return None
        result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
        count = max(1, _safe_int(result.get("count"), 1))
        ingredient_slots: list[Any] = []
        if isinstance(entry.get("inShape"), list):
            shape = entry["inShape"]
            needs_table = len(shape) > 2 or any(isinstance(row, list) and len(row) > 2 for row in shape)
            for row in shape:
                if isinstance(row, list):
                    ingredient_slots.extend(row)
        elif isinstance(entry.get("ingredients"), list):
            needs_table = False
            ingredient_slots.extend(entry["ingredients"])
        else:
            return None

        grouped: dict[tuple[str, ...], int] = {}
        for slot in ingredient_slots:
            options = _recipe_slot_options(slot, id_to_name)
            if not options:
                continue
            grouped[options] = grouped.get(options, 0) + 1
        ingredients = tuple(
            RecipeIngredient(options=options, count=count)
            for options, count in sorted(grouped.items(), key=lambda item: item[0])
        )
        if not ingredients:
            return None
        return Recipe(output=output_name, count=count, ingredients=ingredients, needs_table=needs_table)

    def recipes_for(self, item_name: str) -> tuple[Recipe, ...]:
        return self.recipes_by_output.get(canonical_item_name(item_name), ())

    def can_craft(self, item_name: str, quantity: int, inventory: dict[str, Any]) -> bool:
        item_name = canonical_item_name(item_name)
        needed = max(1, int(quantity or 1))
        if _safe_int(inventory.get(item_name), 0) >= needed:
            return True
        for recipe in self.recipes_for(item_name):
            crafts = _ceil_div(needed, recipe.count)
            if recipe.needs_table and _safe_int(inventory.get("crafting_table"), 0) <= 0 and item_name != "crafting_table":
                continue
            if self._ingredients_available(recipe.ingredients, crafts, inventory):
                return True
        return False

    def missing_ingredients(self, item_name: str, quantity: int, inventory: dict[str, Any]) -> dict[str, int]:
        item_name = canonical_item_name(item_name)
        needed = max(1, int(quantity or 1))
        best_missing: dict[str, int] | None = None
        for recipe in self.recipes_for(item_name):
            crafts = _ceil_div(needed, recipe.count)
            missing = self._missing_for_ingredients(recipe.ingredients, crafts, inventory)
            if recipe.needs_table and _safe_int(inventory.get("crafting_table"), 0) <= 0 and item_name != "crafting_table":
                missing["crafting_table"] = max(1, missing.get("crafting_table", 0))
            if best_missing is None or self._missing_score(missing, inventory) < self._missing_score(best_missing, inventory):
                best_missing = missing
        return best_missing or {}

    def _ingredients_available(self, ingredients: tuple[RecipeIngredient, ...], crafts: int, inventory: dict[str, Any]) -> bool:
        available = {str(k): _safe_int(v) for k, v in (inventory or {}).items()}
        for ingredient in ingredients:
            required = ingredient.count * crafts
            if not self._consume_options(ingredient.options, required, available):
                return False
        return True

    def _missing_for_ingredients(self, ingredients: tuple[RecipeIngredient, ...], crafts: int, inventory: dict[str, Any]) -> dict[str, int]:
        available = {str(k): _safe_int(v) for k, v in (inventory or {}).items()}
        missing: dict[str, int] = {}
        for ingredient in ingredients:
            required = ingredient.count * crafts
            missing_count = self._consume_options_with_missing(ingredient.options, required, available)
            if missing_count <= 0:
                continue
            best_option = max(ingredient.options, key=lambda option: available.get(option, 0))
            missing[best_option] = missing.get(best_option, 0) + missing_count
        return missing

    @staticmethod
    def _consume_options(options: tuple[str, ...], required: int, available: dict[str, int]) -> bool:
        remaining = max(0, int(required or 0))
        for option in sorted(options, key=lambda item: available.get(item, 0), reverse=True):
            if remaining <= 0:
                break
            used = min(remaining, max(0, available.get(option, 0)))
            if used:
                available[option] = available.get(option, 0) - used
                remaining -= used
        return remaining <= 0

    @staticmethod
    def _consume_options_with_missing(options: tuple[str, ...], required: int, available: dict[str, int]) -> int:
        remaining = max(0, int(required or 0))
        for option in sorted(options, key=lambda item: available.get(item, 0), reverse=True):
            if remaining <= 0:
                break
            used = min(remaining, max(0, available.get(option, 0)))
            if used:
                available[option] = available.get(option, 0) - used
                remaining -= used
        return remaining

    def craft_output_count(self, item_name: str) -> int:
        recipes = self.recipes_for(item_name)
        if not recipes:
            return 1
        return max(1, max(recipe.count for recipe in recipes))

    def normalized_craft_quantity(self, item_name: str, quantity: int) -> int:
        output_count = self.craft_output_count(item_name)
        return _ceil_div(max(1, int(quantity or 1)), output_count) * output_count

    def simulate_craft(
        self,
        item_name: str,
        quantity: int,
        inventory: dict[str, Any],
    ) -> tuple[dict[str, int], int] | None:
        item_name = canonical_item_name(item_name)
        needed = max(1, int(quantity or 1))
        for recipe in self.recipes_for(item_name):
            crafts = _ceil_div(needed, recipe.count)
            if recipe.needs_table and _safe_int((inventory or {}).get("crafting_table"), 0) <= 0 and item_name != "crafting_table":
                continue
            simulated = {str(k): _safe_int(v) for k, v in (inventory or {}).items()}
            craftable = True
            for ingredient in recipe.ingredients:
                required = ingredient.count * crafts
                if not self._consume_options(ingredient.options, required, simulated):
                    craftable = False
                    break
            if not craftable:
                continue
            crafted = recipe.count * crafts
            simulated[item_name] = _safe_int(simulated.get(item_name), 0) + crafted
            return simulated, crafted
        return None

    def _missing_score(self, missing: dict[str, int], inventory: dict[str, Any]) -> tuple[int, int, str]:
        total = sum(missing.values())
        non_craftable = sum(
            amount
            for item, amount in missing.items()
            if not self.can_craft(item, amount, inventory)
        )
        return total, non_craftable, ",".join(sorted(missing))


class InventoryFirstPlanner:
    def __init__(self, completed_tasks: set[str] | None = None, recipe_catalog: RecipeCatalog | None = None):
        self.completed_tasks = completed_tasks or set()
        self.recipe_catalog = recipe_catalog or RecipeCatalog.load_default()

    def choose_next(
        self,
        state: InventoryState,
        *,
        previous_task: str = "",
        allow_optional: bool = False,
        objective: str = "progression",
    ) -> PlannedTask | None:
        objective_id = str(objective or "progression").strip().lower() or "progression"
        objective_template = OBJECTIVE_TEMPLATES.get(
            objective_id,
            OBJECTIVE_TEMPLATES["progression"],
        )
        common = self._common_guard_task(state)
        if common:
            return common
        for capability in objective_template.target_capabilities:
            planned = self._plan_for_capability(
                capability,
                state,
                previous_task=previous_task,
                allow_optional=allow_optional,
                objective=objective_id,
            )
            if planned:
                return planned
        return self._optional_objective_task(
            state,
            previous_task=previous_task,
            allow_optional=allow_optional,
            objective=objective_id,
        )

    def _common_guard_task(self, state: InventoryState) -> PlannedTask | None:
        if state.health <= 10:
            return PlannedTask(
                "Establish a lit temporary shelter",
                "Inventory-first: health is unsafe. Stabilize before continuing progression.",
                "low_health_guard",
                objective="progression",
                capability="shelter_stability",
            )
        if state.hunger <= 10 and not state.has_food:
            return PlannedTask(
                "Acquire 1 edible food item",
                "Inventory-first: hunger is low and no edible food is available. Secure food before more progression.",
                "low_hunger_guard",
                objective="progression",
                capability="food_security",
            )
        return None

    def _craft_or_prerequisite_task(
        self,
        item_name: str,
        quantity: int,
        inventory: dict[str, Any],
        *,
        context_prefix: str,
        reason: str,
        objective: str,
        capability: str,
    ) -> PlannedTask | None:
        item = canonical_item_name(item_name)
        if self.can_craft(item, quantity, inventory):
            craft_quantity = self.recipe_catalog.normalized_craft_quantity(item, quantity)
            return PlannedTask(
                f"Craft {craft_quantity} {task_item_name(item, craft_quantity)}",
                f"{context_prefix} Craft {task_item_name(item, craft_quantity)} now.",
                reason,
                objective=objective,
                capability=capability,
            )
        prerequisite = self.first_prerequisite_for_craft(item, quantity, inventory)
        if prerequisite is not None:
            return PlannedTask(
                prerequisite.task,
                prerequisite.context,
                prerequisite.reason,
                objective=objective,
                capability=capability,
            )
        return None

    def _plan_for_capability(
        self,
        capability: str,
        state: InventoryState,
        *,
        previous_task: str = "",
        allow_optional: bool = False,
        objective: str = "progression",
    ) -> PlannedTask | None:
        if capability == "food_security":
            return self._plan_food_security_capability(state, objective=objective)
        if capability == "iron_pickaxe":
            return self._plan_iron_pickaxe_capability(state, objective=objective)
        if capability == "diamond_pickaxe":
            return self._plan_diamond_pickaxe_capability(state, objective=objective)
        if capability == "diamond_armor":
            return self._plan_diamond_armor_capability(state)
        if capability == "light_reserve":
            return self._plan_light_reserve_capability(
                state,
                previous_task=previous_task,
                allow_optional=allow_optional,
                objective=objective,
            )
        if capability == "storage_access":
            return self._plan_storage_access_capability(state, objective=objective)
        if capability == "local_crafting_access":
            return self._plan_local_crafting_access_capability(state, objective=objective)
        return None

    def _plan_food_security_capability(
        self,
        state: InventoryState,
        *,
        objective: str,
    ) -> PlannedTask | None:
        if state.has_food:
            return None
        return PlannedTask(
            "Acquire 1 edible food item",
            "Objective progression: secure a minimum edible food reserve before deeper progression.",
            "food_security_missing",
            objective=objective,
            capability="food_security",
        )

    def _plan_iron_pickaxe_capability(
        self,
        state: InventoryState,
        *,
        objective: str,
    ) -> PlannedTask | None:
        if state.has_tool_at_least("pickaxe", "iron"):
            return None
        raw_iron = state.count("raw_iron")
        iron_ingot = state.count("iron_ingot")

        if iron_ingot >= 3:
            if state.count("stick") >= 2:
                return PlannedTask(
                    "Craft 1 iron_pickaxe",
                    "Inventory-first: enough iron ingots and sticks are available, so upgrade the pickaxe before more mining.",
                    "iron_pickaxe_ready",
                    objective=objective,
                    capability="iron_pickaxe",
                )
            prerequisite = self.first_prerequisite_for_craft("iron_pickaxe", 1, state.inventory)
            if prerequisite:
                return PlannedTask(
                    prerequisite.task,
                    prerequisite.context,
                    prerequisite.reason,
                    objective=objective,
                    capability="iron_pickaxe",
                )

        if raw_iron > 0:
            if not state.has_any("furnace") and state.generic_stone >= 8:
                return PlannedTask(
                    "Craft 1 furnace",
                    "Inventory-first: raw iron is available but an iron pickaxe upgrade still needs a furnace path.",
                    "raw_iron_needs_furnace",
                    objective=objective,
                    capability="local_crafting_access",
                )
            if state.has_any("furnace") and not state.has_any("coal", "charcoal"):
                if "coal_ore" in state.nearby_blocks and state.has_tool_at_least("pickaxe", "stone"):
                    return PlannedTask(
                        "Mine 4 coal_ore",
                        "Inventory-first: raw iron can unlock the iron pickaxe upgrade, but fuel is missing. Mine nearby coal for smelting.",
                        "raw_iron_needs_fuel",
                        objective=objective,
                        capability="smelting_fuel",
                    )
                return PlannedTask(
                    "Obtain 4 coal",
                    "Inventory-first: raw iron can unlock the iron pickaxe upgrade, but fuel is missing. Secure a small coal reserve for smelting.",
                    "raw_iron_needs_fuel",
                    objective=objective,
                    capability="smelting_fuel",
                )
            if state.has_any("furnace") and state.has_any("coal", "charcoal"):
                return PlannedTask(
                    f"Smelt {raw_iron} raw_iron into iron_ingots",
                    "Inventory-first: raw iron, furnace, and fuel are available, and smelting is still needed for the iron pickaxe upgrade.",
                    "raw_iron_ready_to_smelt",
                    objective=objective,
                    capability="iron_pickaxe",
                )
        return None

    def _plan_diamond_pickaxe_capability(
        self,
        state: InventoryState,
        *,
        objective: str,
    ) -> PlannedTask | None:
        if state.has_tool_at_least("pickaxe", "diamond"):
            return None
        if not state.has_tool_at_least("pickaxe", "iron"):
            return self._plan_iron_pickaxe_capability(state, objective=objective)
        if state.count("diamond") < 3:
            return PlannedTask(
                "Obtain 3 diamond",
                "Objective progression: secure enough diamonds for the next mining tier upgrade.",
                "diamond_pickaxe_missing_material",
                objective=objective,
                capability="diamond_pickaxe",
            )
        planned = self._craft_or_prerequisite_task(
            "diamond_pickaxe",
            1,
            state.inventory,
            context_prefix="Objective progression: enough diamonds are available for the next mining tier.",
            reason="diamond_pickaxe_ready",
            objective=objective,
            capability="diamond_pickaxe",
        )
        if planned:
            return planned
        return None

    def _plan_diamond_armor_capability(self, state: InventoryState) -> PlannedTask | None:
        progression_plan = self._plan_diamond_pickaxe_capability(
            state,
            objective="armor_progression",
        )
        if progression_plan is not None:
            return progression_plan
        armor_sequence = [
            ("chestplate", "diamond_chestplate"),
            ("leggings", "diamond_leggings"),
            ("helmet", "diamond_helmet"),
            ("boots", "diamond_boots"),
        ]
        for slot, item_name in armor_sequence:
            if state.has_armor_at_least(slot, "diamond"):
                continue
            planned = self._craft_or_prerequisite_task(
                item_name,
                1,
                state.inventory,
                context_prefix=f"Objective armor progression: craft the missing diamond {slot}.",
                reason=f"diamond_{slot}_missing",
                objective="armor_progression",
                capability="diamond_armor",
            )
            if planned is not None:
                return planned
            diamond_cost = {
                "diamond_chestplate": 8,
                "diamond_leggings": 7,
                "diamond_helmet": 5,
                "diamond_boots": 4,
            }[item_name]
            return PlannedTask(
                f"Obtain {diamond_cost} diamond",
                f"Objective armor progression: gather enough diamonds to craft the missing {item_name}.",
                f"{item_name}_missing_material",
                objective="armor_progression",
                capability="diamond_armor",
            )
        return None

    def _plan_light_reserve_capability(
        self,
        state: InventoryState,
        *,
        previous_task: str = "",
        allow_optional: bool = False,
        objective: str,
    ) -> PlannedTask | None:
        if state.count("torch") >= 8:
            return None
        if (
            allow_optional
            and state.count("coal") >= 1
            and state.count("stick") >= 1
            and "torch" not in str(previous_task or "").lower()
        ):
            return PlannedTask(
                "Craft 4 torches",
                "Inventory-first: coal and sticks are available, so prepare light before more underground movement.",
                "coal_ready_for_torches",
                objective=objective,
                capability="light_reserve",
            )
        planned = self._craft_or_prerequisite_task(
            "torch",
            4,
            state.inventory,
            context_prefix="Objective progression: restore a minimum torch reserve for safer exploration.",
            reason="light_reserve_missing",
            objective=objective,
            capability="light_reserve",
        )
        if planned:
            return planned
        return None

    def _plan_storage_access_capability(
        self,
        state: InventoryState,
        *,
        objective: str,
    ) -> PlannedTask | None:
        if "chest" in state.nearby_blocks:
            return None
        if state.has_any("chest"):
            return PlannedTask(
                "Place 1 chest",
                "Objective base establishment: place a chest to anchor a minimal local base.",
                "base_storage_placement_missing",
                objective=objective,
                capability="storage_access",
            )
        planned = self._craft_or_prerequisite_task(
            "chest",
            1,
            state.inventory,
            context_prefix="Objective base establishment: create local storage access.",
            reason="base_storage_missing",
            objective=objective,
            capability="storage_access",
        )
        if planned:
            return planned
        return None

    def _plan_local_crafting_access_capability(
        self,
        state: InventoryState,
        *,
        objective: str,
    ) -> PlannedTask | None:
        if state.has_any("crafting_table") or "crafting_table" in state.nearby_blocks:
            return None
        planned = self._craft_or_prerequisite_task(
            "crafting_table",
            1,
            state.inventory,
            context_prefix="Objective base establishment: maintain local crafting access.",
            reason="local_crafting_access_missing",
            objective=objective,
            capability="local_crafting_access",
        )
        if planned:
            return planned
        return None

    def _optional_objective_task(
        self,
        state: InventoryState,
        *,
        previous_task: str = "",
        allow_optional: bool = False,
        objective: str = "progression",
    ) -> PlannedTask | None:
        if objective == "base_establishment":
            return None
        if (
            allow_optional
            and state.count("coal") >= 1
            and state.count("stick") >= 1
            and "torch" not in str(previous_task or "").lower()
        ):
            return PlannedTask(
                "Craft 4 torches",
                "Inventory-first: coal and sticks are available, so prepare light before more underground movement.",
                "coal_ready_for_torches",
                objective=objective,
                capability="light_reserve",
            )
        return None

    def is_task_satisfied(self, task: str, inventory: dict[str, Any]) -> bool:
        state = InventoryState.from_observation(inventory=inventory)
        normalized = str(task or "").strip()
        smelt_result_match = _match_smelt_inventory_result(normalized)
        if smelt_result_match:
            amount, output_item_name = smelt_result_match
            return state.count(output_item_name) >= amount
        obtain_match = _match_simple_amount_task(normalized, "Obtain")
        if obtain_match:
            amount, item_name = obtain_match
            if item_name in {"wood_log", "wood_logs", "log", "logs"}:
                return state.logs >= amount
            if item_name in {"wood_plank", "wood_planks", "plank", "planks"}:
                return state.planks >= amount
            if item_name in {"stone", "cobblestone", "cobbled_deepslate", "blackstone"}:
                return state.generic_stone >= amount
            return state.count(item_name) >= amount
        craft_match = _match_simple_amount_task(normalized, "Craft")
        if craft_match:
            amount, item_name = craft_match
            if item_name in {"wood_plank", "wood_planks", "plank", "planks"}:
                return state.planks >= amount
            return state.count(item_name) >= amount or capability_satisfies_item(state.inventory, item_name, amount)
        return False

    def can_craft(self, item_name: str, quantity: int, inventory: dict[str, Any]) -> bool:
        item = canonical_item_name(item_name)
        if capability_satisfies_item(inventory, item, quantity):
            return True
        return self.recipe_catalog.can_craft(item, quantity, inventory)

    def missing_for_craft(self, item_name: str, quantity: int, inventory: dict[str, Any]) -> dict[str, int]:
        return self.recipe_catalog.missing_ingredients(
            canonical_item_name(item_name),
            quantity,
            inventory,
        )

    def prerequisite_chain_for_craft(
        self,
        item_name: str,
        quantity: int,
        inventory: dict[str, Any],
        *,
        max_depth: int = 5,
        _seen: frozenset[str] = frozenset(),
    ) -> list[PlannedTask]:
        item = canonical_item_name(item_name)
        craft_quantity = self.recipe_catalog.normalized_craft_quantity(item, quantity)
        if self.can_craft(item, quantity, inventory):
            return [
                PlannedTask(
                    f"Craft {craft_quantity} {task_item_name(item, craft_quantity)}",
                    f"Recipe planner: craft {task_item_name(item, craft_quantity)} from currently available ingredients.",
                    "craft_target",
                )
            ]
        if max_depth <= 0 or item in _seen:
            return []
        missing = self.missing_for_craft(item, quantity, inventory)
        if not missing:
            return []

        prereq, amount = self._select_prerequisite(missing, inventory)
        if prereq == "stick" and amount < 4:
            amount = 4
        prereq = canonical_item_name(prereq)
        prereq_amount = self.recipe_catalog.normalized_craft_quantity(prereq, amount)
        display_prereq = task_item_name(prereq, amount)
        if self.can_craft(prereq, amount, inventory):
            return [
                PlannedTask(
                    f"Craft {prereq_amount} {task_item_name(prereq, prereq_amount)}",
                    f"Recipe planner: craft {task_item_name(prereq, prereq_amount)} first because it is required for Craft {craft_quantity} {task_item_name(item, craft_quantity)}.",
                    "craft_prerequisite",
                ),
                PlannedTask(
                    f"Craft {craft_quantity} {task_item_name(item, craft_quantity)}",
                    f"Recipe planner: then craft {task_item_name(item, craft_quantity)}.",
                    "craft_target",
                ),
            ]
        nested = self.prerequisite_chain_for_craft(
            prereq,
            prereq_amount,
            inventory,
            max_depth=max_depth - 1,
            _seen=_seen.union({item}),
        )
        if nested:
            chain = list(nested)
            prereq_task = f"Craft {prereq_amount} {task_item_name(prereq, prereq_amount)}"
            if not chain or chain[-1].task != prereq_task:
                chain.append(
                    PlannedTask(
                        prereq_task,
                        f"Recipe planner: after prerequisites, craft {task_item_name(prereq, prereq_amount)} for Craft {craft_quantity} {task_item_name(item, craft_quantity)}.",
                        "craft_prerequisite",
                    )
                )
            chain.append(
                PlannedTask(
                    f"Craft {craft_quantity} {task_item_name(item, craft_quantity)}",
                    f"Recipe planner: then craft {task_item_name(item, craft_quantity)}.",
                    "craft_target",
                )
            )
            return chain
        return [
            PlannedTask(
                f"{gather_verb_for_item(prereq)} {amount} {display_prereq}",
                f"Recipe planner: gather {display_prereq} first because it is required for Craft {craft_quantity} {task_item_name(item, craft_quantity)}.",
                "gather_prerequisite",
            )
        ]

    def first_prerequisite_for_craft(self, item_name: str, quantity: int, inventory: dict[str, Any]) -> PlannedTask | None:
        chain = self.prerequisite_chain_for_craft(item_name, quantity, inventory)
        if not chain:
            return None
        first = chain[0]
        if first.reason == "craft_target":
            return None
        return first

    def prerequisite_for_craft(self, item_name: str, quantity: int, inventory: dict[str, Any]) -> PlannedTask | None:
        return self.first_prerequisite_for_craft(item_name, quantity, inventory)

    def _select_prerequisite(self, missing: dict[str, int], inventory: dict[str, Any]) -> tuple[str, int]:
        for preferred in ("crafting_table", "stick", "furnace"):
            if missing.get(preferred, 0) > 0:
                return preferred, missing[preferred]
        craftable = [
            (item, amount)
            for item, amount in missing.items()
            if self.can_craft(item, amount, inventory)
        ]
        if craftable:
            return sorted(craftable, key=lambda item: item[0])[0]
        return sorted(missing.items(), key=lambda item: (-item[1], item[0]))[0]

    def fallback_recovery(self, state: InventoryState) -> PlannedTask:
        if state.logs < 8:
            return PlannedTask(
                "Obtain 8 wood logs",
                "Recovery planner: collect wood before broader progression.",
                "recover_logs",
            )
        if state.planks < 8:
            return PlannedTask(
                "Craft 8 wood planks",
                "Recovery planner: convert logs into planks before broader progression.",
                "recover_planks",
            )
        if not state.has_any("crafting_table"):
            return PlannedTask(
                "Craft 1 crafting_table",
                "Recovery planner: rebuild the basic crafting setup.",
                "recover_crafting_table",
            )
        if state.count("stick") < 4:
            return PlannedTask(
                "Craft 4 sticks",
                "Recovery planner: rebuild a small stick reserve.",
                "recover_sticks",
            )
        if not state.has_tool_at_least("pickaxe", "wooden"):
            return PlannedTask(
                "Craft 1 wooden_pickaxe",
                "Recovery planner: restore minimum pickaxe capability.",
                "recover_wooden_pickaxe_capability",
            )
        if state.generic_stone < 6:
            return PlannedTask(
                "Mine 6 cobblestone",
                "Recovery planner: secure the first stone batch.",
                "recover_stone",
            )
        if not state.has_tool_at_least("pickaxe", "stone"):
            return PlannedTask(
                "Craft 1 stone_pickaxe",
                "Recovery planner: restore stone-or-better pickaxe capability.",
                "recover_stone_pickaxe_capability",
            )
        if not state.has_tool_at_least("axe", "stone"):
            return PlannedTask(
                "Craft 1 stone_axe",
                "Recovery planner: restore stone-or-better axe capability.",
                "recover_stone_axe_capability",
            )
        if not state.has_food:
            return PlannedTask(
                "Acquire 1 edible food item",
                "Recovery planner: stabilize food before returning to progression.",
                "recover_food",
            )
        return PlannedTask(
            "Move 24 blocks away from current position",
            "Recovery planner: no deterministic inventory prerequisite is missing, so reposition safely.",
            "recover_position",
        )


def split_tiered_item(item_name: str):
    item = str(item_name or "").strip().lower().replace(" ", "_")
    for suffix in TOOL_SUFFIXES:
        marker = f"_{suffix}"
        if item.endswith(marker):
            return "tool", suffix, item[: -len(marker)]
    for suffix in ARMOR_SUFFIXES:
        marker = f"_{suffix}"
        if item.endswith(marker):
            return "armor", suffix, item[: -len(marker)]
    return None, None, None


def material_tier(kind: str, material: str) -> int:
    tiers = TOOL_MATERIAL_TIERS if kind == "tool" else ARMOR_MATERIAL_TIERS
    return int(tiers.get(str(material or "").lower()) or 0)


def count_capability_at_least(inventory: dict[str, Any], kind: str, slot: str, minimum_material: str) -> int:
    minimum_tier = material_tier(kind, minimum_material)
    if minimum_tier <= 0:
        return 0
    count = 0
    for name, amount in (inventory or {}).items():
        item_kind, item_slot, material = split_tiered_item(name)
        if item_kind != kind or item_slot != slot:
            continue
        if material_tier(kind, material) >= minimum_tier:
            count += _safe_int(amount)
    return count


def has_tool_at_least(inventory: dict[str, Any], tool_type: str, minimum_material: str) -> bool:
    return count_capability_at_least(inventory, "tool", tool_type, minimum_material) > 0


def has_armor_at_least(inventory: dict[str, Any], armor_slot: str, minimum_material: str) -> bool:
    return count_capability_at_least(inventory, "armor", armor_slot, minimum_material) > 0


def capability_satisfies_item(inventory: dict[str, Any], item_name: str, quantity: int = 1) -> bool:
    kind, slot, material = split_tiered_item(canonical_item_name(item_name))
    if not kind:
        return False
    return count_capability_at_least(inventory, kind, slot, material) >= max(1, int(quantity or 1))


def canonical_item_name(item_name: str) -> str:
    item = str(item_name or "").strip().lower().replace(" ", "_")
    aliases = {
        "sticks": "stick",
        "torches": "torch",
        "wood_planks": "oak_planks",
        "planks": "oak_planks",
        "iron_ingots": "iron_ingot",
        "copper_ingots": "copper_ingot",
    }
    return aliases.get(item, item)


def task_item_name(item_name: str, quantity: int = 1) -> str:
    item = canonical_item_name(item_name)
    if item == "stick" and int(quantity or 1) != 1:
        return "sticks"
    if item == "iron_ingot" and int(quantity or 1) != 1:
        return "iron_ingots"
    return item


def gather_verb_for_item(item_name: str) -> str:
    item = canonical_item_name(item_name)
    if item in {"cobblestone", "cobbled_deepslate", "stone", "blackstone"}:
        return "Mine"
    if item.endswith("_ore"):
        return "Mine"
    if item in {"coal", "raw_iron", "raw_copper", "diamond", "redstone", "lapis_lazuli"}:
        return "Obtain"
    return "Obtain"


def _find_minecraft_data_dir(version: str) -> Path | None:
    candidates = []
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "node_modules" / "minecraft-data" / "minecraft-data" / "data" / "pc" / version)
    candidates.extend([
        Path("C:/Evelyn/node_modules/minecraft-data/minecraft-data/data/pc") / version,
        Path("C:/Evelyn/third_party/Voyager/voyager/env/mineflayer/node_modules/minecraft-data/minecraft-data/data/pc") / version,
    ])
    for candidate in candidates:
        if (candidate / "items.json").exists() and (candidate / "recipes.json").exists():
            return candidate
    return None


def _recipe_slot_options(slot: Any, id_to_name: dict[int, str]) -> tuple[str, ...]:
    if slot is None:
        return ()
    if isinstance(slot, int):
        name = id_to_name.get(slot)
        return (name,) if name else ()
    if isinstance(slot, list):
        names = []
        for value in slot:
            if isinstance(value, int) and value in id_to_name:
                names.append(id_to_name[value])
        return tuple(sorted(set(names)))
    return ()


def _ceil_div(value: int, divisor: int) -> int:
    return (int(value) + int(divisor) - 1) // int(divisor)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _status_number(status: dict[str, Any] | None, key: str, default: float) -> float:
    try:
        value = status.get(key) if isinstance(status, dict) else default
        return float(value)
    except Exception:
        return float(default)


def _match_simple_amount_task(task: str, verb: str) -> tuple[int, str] | None:
    prefix = f"{verb} "
    if not task.lower().startswith(prefix.lower()):
        return None
    parts = task[len(prefix):].strip().split(" ", 1)
    if len(parts) != 2:
        return None
    try:
        amount = int(parts[0])
    except ValueError:
        return None
    return amount, canonical_item_name(parts[1])


def _match_smelt_inventory_result(task: str) -> tuple[int, str] | None:
    parsed = parse_smelt_result_task(task)
    if not isinstance(parsed, dict):
        return None
    return int(parsed["amount"]), canonical_item_name(parsed["output_target"])
