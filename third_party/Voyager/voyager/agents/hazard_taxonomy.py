from __future__ import annotations

from typing import Any

HOSTILE_ENTITY_NAMES = ("zombie", "skeleton", "creeper", "spider", "drowned", "witch", "enderman")

HAZARD_KEYWORDS = {
    "drowning": ("drown", "drowned", "water", "bubble"),
    "lava_fire": ("lava", "burn", "fire", "magma"),
    "fall": ("fell", "fall", "hit the ground", "cliff"),
    "starvation": ("starv", "hunger"),
    "hostile": ("slain", "shot", "blown up", "creeper", "skeleton", "zombie", "spider", "drowned", "witch", "enderman"),
}


def _combined_text(parts: list[str] | tuple[str, ...]) -> str:
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip()).lower()


def classify_hazard_text(text: str, *, has_hostiles: bool = False) -> str:
    combined = str(text or "").strip().lower()
    if any(token in combined for token in HAZARD_KEYWORDS["drowning"]):
        return "drowning"
    if any(token in combined for token in HAZARD_KEYWORDS["lava_fire"]):
        return "lava_fire"
    if any(token in combined for token in HAZARD_KEYWORDS["fall"]):
        return "fall"
    if any(token in combined for token in HAZARD_KEYWORDS["starvation"]):
        return "starvation"
    if has_hostiles or any(token in combined for token in HAZARD_KEYWORDS["hostile"]):
        return "hostile"
    return "general"


def classify_death_event(death_event: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(death_event, dict):
        return {
            "category": "general",
            "combined_text": "",
            "has_hostiles": False,
        }
    nearby_hostiles = death_event.get("nearby_hostiles") if isinstance(death_event.get("nearby_hostiles"), list) else []
    has_hostiles = bool(nearby_hostiles)
    combined = _combined_text([
        death_event.get("cause"),
        death_event.get("death_message"),
        death_event.get("likely_reason"),
        death_event.get("likely_killer"),
    ])
    return {
        "category": classify_hazard_text(combined, has_hostiles=has_hostiles),
        "combined_text": combined,
        "has_hostiles": has_hostiles,
    }
