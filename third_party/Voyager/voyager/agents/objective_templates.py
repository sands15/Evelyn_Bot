from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObjectiveTemplate:
    id: str
    label: str
    description: str
    target_capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "target_capabilities": list(self.target_capabilities),
        }


OBJECTIVE_TEMPLATES: dict[str, ObjectiveTemplate] = {
    "progression": ObjectiveTemplate(
        id="progression",
        label="Progression",
        description="Default early and mid-game capability progression.",
        target_capabilities=("food_security", "iron_pickaxe", "diamond_pickaxe"),
    ),
    "armor_progression": ObjectiveTemplate(
        id="armor_progression",
        label="Armor Progression",
        description="Upgrade toward durable combat and exploration armor, preferring diamond armor once the tool chain is ready.",
        target_capabilities=(
            "food_security",
            "iron_pickaxe",
            "diamond_pickaxe",
            "diamond_armor",
        ),
    ),
    "base_establishment": ObjectiveTemplate(
        id="base_establishment",
        label="Base Establishment",
        description="Stabilize survival, lighting, storage, and local crafting infrastructure before decorative expansion.",
        target_capabilities=(
            "food_security",
            "light_reserve",
            "storage_access",
            "local_crafting_access",
        ),
    ),
}


def infer_objective_template(goal_text: str | None = None, current_task: str | None = None) -> ObjectiveTemplate:
    combined = " ".join(
        part.strip().lower()
        for part in (str(goal_text or ""), str(current_task or ""))
        if str(part or "").strip()
    )
    if not combined:
        return OBJECTIVE_TEMPLATES["progression"]
    if any(token in combined for token in ("diamond armor", "diamond_armor", "chestplate", "leggings", "helmet", "boots", "armor progression")):
        return OBJECTIVE_TEMPLATES["armor_progression"]
    if any(token in combined for token in ("base", "shelter", "outpost", "storage", "home", "settle")):
        return OBJECTIVE_TEMPLATES["base_establishment"]
    return OBJECTIVE_TEMPLATES["progression"]
