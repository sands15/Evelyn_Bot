from __future__ import annotations

EDIBLE_FOOD_ITEMS = (
    "apple",
    "baked_potato",
    "beef",
    "beetroot",
    "beetroot_soup",
    "bread",
    "carrot",
    "chicken",
    "cod",
    "cookie",
    "cooked_beef",
    "cooked_chicken",
    "cooked_cod",
    "cooked_mutton",
    "cooked_porkchop",
    "cooked_rabbit",
    "cooked_salmon",
    "dried_kelp",
    "glow_berries",
    "golden_carrot",
    "melon_slice",
    "mushroom_stew",
    "mutton",
    "porkchop",
    "potato",
    "pumpkin_pie",
    "rabbit",
    "rabbit_stew",
    "salmon",
    "sweet_berries",
)

ITEM_NAME_ALIASES = {
    "sticks": "stick",
    "torches": "torch",
    "wood_planks": "oak_planks",
    "planks": "oak_planks",
    "iron_ingots": "iron_ingot",
    "copper_ingots": "copper_ingot",
}


def canonical_food_item_name(item_name: str) -> str:
    item = str(item_name or "").strip().lower().replace(" ", "_")
    return ITEM_NAME_ALIASES.get(item, item)


def edible_food_total(inventory) -> int:
    if not isinstance(inventory, dict):
        return 0
    total = 0
    for name, count in inventory.items():
        canonical = canonical_food_item_name(name)
        if canonical not in EDIBLE_FOOD_ITEMS:
            continue
        try:
            total += int(count or 0)
        except Exception:
            continue
    return total


def inventory_has_edible_food(inventory) -> bool:
    return edible_food_total(inventory) > 0
