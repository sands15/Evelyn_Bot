from __future__ import annotations

import re

LOG_BLOCK_NAMES = [
    "oak_log",
    "spruce_log",
    "birch_log",
    "jungle_log",
    "acacia_log",
    "dark_oak_log",
    "mangrove_log",
    "cherry_log",
]
FOOD_ANIMALS = ["cow", "pig", "chicken", "sheep", "rabbit"]
FOOD_CROPS = ["wheat", "carrots", "potatoes", "beetroots"]
ORE_MARKERS = ["iron_ore", "coal_ore", "copper_ore", "gold_ore", "diamond_ore", "raw_iron", "raw_gold"]
SEARCH_HELPER_MARKERS = [
    "await searchAndHarvest(",
    "await searchAndCollectFood(",
    "await searchForOre(",
    "await recoverToSurface(",
    "await searchAndMove(",
    "await searchAndAct(",
]
NEARBY_SEARCH_MARKERS = [
    "bot.findBlock(",
    "bot.findBlocks(",
    "bot.nearestEntity(",
    "nearestEntity(",
]


class ActionValidatorPolicy:
    def validate_program_code(self, program_code, *, extract_explore_timeouts):
        errors = []
        code = str(program_code or "")
        if re.search(r"bot\.food\s*!={1,2}\s*undefined", code) or re.search(
            r"typeof\s+bot\.food\s*!={1,2}\s*['\"]undefined['\"]",
            code,
        ):
            errors.append("Do not use bot.food existence as proof of edible inventory. bot.food is the hunger meter, not an inventory-food check.")
        uses_search_helper = any(marker in code for marker in SEARCH_HELPER_MARKERS)
        explore_calls = code.count("await exploreUntil(")
        if explore_calls > 2:
            errors.append("Use at most two short exploreUntil probes in one function.")
        if explore_calls and not uses_search_helper:
            nearby_searches = sum(code.count(marker) for marker in NEARBY_SEARCH_MARKERS)
            first_probe = code.find("await exploreUntil(")
            search_positions = [
                pos
                for pos in [code.find(marker) for marker in NEARBY_SEARCH_MARKERS]
                if pos != -1
            ]
            first_nearby_search = min(search_positions) if search_positions else -1
            if nearby_searches == 0 or (first_nearby_search != -1 and first_nearby_search > first_probe):
                errors.append("Do a nearby 32-block search before any exploreUntil probe.")
            timeouts = extract_explore_timeouts(code)
            if any(timeout is None for timeout in timeouts):
                errors.append("exploreUntil must use an explicit numeric maxTime so local search stays bounded.")
            if any(timeout is not None and (timeout < 10 or timeout > 20) for timeout in timeouts):
                errors.append("Each exploreUntil maxTime must be between 10 and 20 seconds.")
            if "LOCAL_SEARCH_EXHAUSTED" not in code:
                errors.append("If local probes fail, throw a concise LOCAL_SEARCH_EXHAUSTED error so the higher-level planner can change direction, biome, or prerequisites.")
        if any(log_name in code for log_name in LOG_BLOCK_NAMES):
            if explore_calls and not any(marker in code for marker in ["searchAndHarvest(", "recoverToSurface("]):
                errors.append("Wood search should prefer searchAndHarvest(...) and recoverToSurface(...) instead of hand-written wandering.")
        direct_crop_harvest = (
            any(crop_name in code for crop_name in FOOD_CROPS)
            and ("collectBlock.collect" in code or "mineBlock(" in code)
            and not any(animal_name in code for animal_name in FOOD_ANIMALS)
        )
        if any(food_marker in code for food_marker in [*FOOD_ANIMALS, *FOOD_CROPS]):
            if "searchAndCollectFood(" not in code and "recoverToSurface(" not in code and explore_calls:
                if not direct_crop_harvest:
                    errors.append("Food search should prefer searchAndCollectFood(...) and recoverToSurface(...) over custom exploreUntil loops.")
        if any(ore_marker in code for ore_marker in ORE_MARKERS):
            if "searchForOre(" not in code and explore_calls:
                errors.append("Ore search should prefer searchForOre(...) over custom exploreUntil cave wandering.")
        return errors
