from __future__ import annotations

import math
import re

from voyager.agents.food_signals import edible_food_total
from voyager.agents.inventory_planner import canonical_item_name
from voyager.agents.observation_utils import observe_payload, payload_inventory, payload_status, safe_int
from voyager.agents.survival_signals import hostiles_nearby
from voyager.agents.task_contract_policy import (
    ACQUIRE_EDIBLE_FOOD_TASK_PATTERN,
    MOVE_AWAY_TASK_PATTERN,
    PLACE_COUNT_TASK_PATTERN,
    SMELT_INTO_TASK_PATTERN,
    parse_smelt_result_task,
)

ORE_TASK_ITEM_MAP = {
    "coal_ore": "coal",
    "iron_ore": "raw_iron",
    "copper_ore": "raw_copper",
    "gold_ore": "raw_gold",
    "diamond_ore": "diamond",
    "lapis_ore": "lapis_lazuli",
    "redstone_ore": "redstone",
    "emerald_ore": "emerald",
}

PLACEABLE_HINTS = ("place", "build", "shelter", "chest", "torch", "furnace", "crafting table")
MOVE_HINTS = ("move", "go to", "travel", "reach", "walk", "approach")
EAT_HINTS = ("eat", "food", "hunger")
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


def _normalized_inventory(payload):
    normalized = {}
    for name, count in payload_inventory(payload).items():
        canonical = canonical_item_name(name)
        normalized[canonical] = normalized.get(canonical, 0) + safe_int(count)
    return normalized


def _position(payload):
    status = payload_status(payload)
    position = status.get("position")
    if not isinstance(position, dict):
        return None
    try:
        return (
            float(position.get("x")),
            float(position.get("y")),
            float(position.get("z")),
        )
    except Exception:
        return None


def _distance(before, after):
    if before is None or after is None:
        return None
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(before, after)))


def _surface_like(payload):
    if not isinstance(payload, dict):
        return False
    voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
    nearby_blocks = payload.get("nearby_blocks") if isinstance(payload.get("nearby_blocks"), list) else []
    blocks = {str(block).lower() for block in [*voxels, *nearby_blocks]}
    return any(block in SURFACE_BLOCK_HINTS for block in blocks)


def _inventory_delta(before_payload, after_payload, item_name):
    before = _normalized_inventory(before_payload)
    after = _normalized_inventory(after_payload)
    key = canonical_item_name(item_name)
    before_count = safe_int(before.get(key), 0)
    after_count = safe_int(after.get(key), 0)
    return {
        "item": key,
        "before": before_count,
        "after": after_count,
        "delta": after_count - before_count,
    }


def _parse_amount_item_task(task):
    task_text = str(task or "").strip()
    smelt_result_task = parse_smelt_result_task(task_text)
    if smelt_result_task is not None:
        return {
            "verb": "Have" if smelt_result_task["style"] == "have_into" else "Smelt",
            "amount": int(smelt_result_task["amount"]),
            "target": canonical_item_name(smelt_result_task["output_target"]),
        }
    match = re.fullmatch(r"(Obtain|Have|Craft|Mine)\s+(\d+)\s+([a-z0-9_ ]+)", task_text, re.IGNORECASE)
    if not match:
        return None
    verb, amount_text, raw_target = match.groups()
    target = canonical_item_name(raw_target.strip().lower().replace(" ", "_"))
    if verb.lower() == "mine" and target in ORE_TASK_ITEM_MAP:
        target = ORE_TASK_ITEM_MAP[target]
    return {
        "verb": verb.capitalize(),
        "amount": int(amount_text),
        "target": target,
    }


def _placed_block_success(events):
    if not isinstance(events, list):
        return None
    placed = []
    for event_type, event in events:
        if event_type != "onSave" or not isinstance(event, dict):
            continue
        name = str(event.get("onSave") or "")
        if name.endswith("_placed"):
            placed.append(name[: -len("_placed")])
    if placed:
        return {
            "outcome": "success",
            "reason_code": "placed_block_detected",
            "summary": f"Observed placed block events: {', '.join(placed[:4])}",
            "evidence": {"placed_blocks": placed[:8]},
        }
    return None


def verify_task_effect(task, before_events=None, after_events=None):
    before_payload = observe_payload(before_events)
    after_payload = observe_payload(after_events)
    task_text = str(task or "").strip()
    lowered = task_text.lower()

    edible_match = ACQUIRE_EDIBLE_FOOD_TASK_PATTERN.match(task_text)
    if edible_match:
        target_amount = int(edible_match.group(1))
        before_total = edible_food_total(payload_inventory(before_payload))
        after_total = edible_food_total(payload_inventory(after_payload))
        if after_total >= target_amount and after_total > before_total:
            return {
                "outcome": "success",
                "reason_code": "edible_food_gained",
                "summary": f"Edible food total increased from {before_total} to {after_total}.",
                "evidence": {"before_total": before_total, "after_total": after_total},
            }
        if after_total >= target_amount:
            return {
                "outcome": "success",
                "reason_code": "edible_food_available",
                "summary": f"Inventory already contains {after_total} edible food items.",
                "evidence": {"before_total": before_total, "after_total": after_total},
            }
        return {
            "outcome": "fail",
            "reason_code": "edible_food_missing",
            "summary": f"Edible food total stayed at {after_total}.",
            "evidence": {"before_total": before_total, "after_total": after_total},
        }

    if task_text == "Reach a surface position":
        moved = _distance(_position(before_payload), _position(after_payload))
        after_surface = _surface_like(after_payload)
        if after_surface and (moved is None or moved >= 2.0 or not _surface_like(before_payload)):
            return {
                "outcome": "success",
                "reason_code": "surface_position_reached",
                "summary": "Bot ended in a surface-like area after the step.",
                "evidence": {"distance": moved, "after_surface": after_surface},
            }
        return {
            "outcome": "fail",
            "reason_code": "surface_position_unreached",
            "summary": "Bot did not end in a clearly surface-like area.",
            "evidence": {"distance": moved, "after_surface": after_surface},
        }

    move_away_match = MOVE_AWAY_TASK_PATTERN.match(task_text)
    if move_away_match:
        required_distance = float(move_away_match.group(1))
        moved = _distance(_position(before_payload), _position(after_payload))
        if moved is None:
            return {
                "outcome": "unknown",
                "reason_code": "position_missing",
                "summary": "Missing before/after position data.",
                "evidence": {},
            }
        if moved >= required_distance:
            return {
                "outcome": "success",
                "reason_code": "move_distance_met",
                "summary": f"Bot moved {round(moved, 2)} blocks, meeting the {required_distance:g}-block requirement.",
                "evidence": {"distance": moved, "required_distance": required_distance},
            }
        return {
            "outcome": "fail",
            "reason_code": "move_distance_unmet",
            "summary": f"Bot moved only {round(moved, 2)} blocks, below the {required_distance:g}-block requirement.",
            "evidence": {"distance": moved, "required_distance": required_distance},
        }

    if task_text == "Establish a lit temporary shelter":
        placed_result = _placed_block_success(after_events)
        after_status = payload_status(after_payload)
        entities = after_status.get("entities") if isinstance(after_status.get("entities"), dict) else {}
        hostile_nearby = hostiles_nearby(entities)
        if placed_result is not None and not hostile_nearby:
            return {
                "outcome": "success",
                "reason_code": "shelter_blocks_placed",
                "summary": placed_result.get("summary") or "Shelter-related block placement was observed.",
                "evidence": placed_result.get("evidence") or {},
            }
        return {
            "outcome": "unknown",
            "reason_code": "shelter_effect_unclear",
            "summary": "Shelter placement or safety state was not yet clear enough for deterministic verification.",
            "evidence": {"hostile_nearby": hostile_nearby},
        }

    place_count_match = PLACE_COUNT_TASK_PATTERN.match(task_text)
    if place_count_match:
        required_count = int(place_count_match.group(1))
        placed_result = _placed_block_success(after_events)
        if placed_result is not None:
            placed_blocks = placed_result.get("evidence", {}).get("placed_blocks") or []
            if len(placed_blocks) >= required_count:
                return {
                    "outcome": "success",
                    "reason_code": "placed_block_count_met",
                    "summary": f"Observed {len(placed_blocks)} placed block event(s), meeting the placement requirement.",
                    "evidence": {"placed_blocks": placed_blocks[:8], "required_count": required_count},
                }
        return {
            "outcome": "unknown",
            "reason_code": "placed_block_unconfirmed",
            "summary": "The requested placement task did not yet have a clear placed-block signal.",
            "evidence": {"required_count": required_count},
        }

    parsed = _parse_amount_item_task(task_text)
    if parsed is not None:
        delta = _inventory_delta(before_payload, after_payload, parsed["target"])
        crossed_threshold = delta["before"] < parsed["amount"] <= delta["after"]
        if parsed["verb"] in {"Craft", "Smelt", "Mine", "Obtain"}:
            if delta["delta"] > 0 or crossed_threshold:
                return {
                    "outcome": "success",
                    "reason_code": "inventory_delta_positive",
                    "summary": f"{parsed['target']} changed from {delta['before']} to {delta['after']}.",
                    "evidence": delta,
                }
            return {
                "outcome": "fail",
                "reason_code": "no_inventory_gain",
                "summary": f"{parsed['target']} did not increase after the step.",
                "evidence": delta,
            }
        if parsed["verb"] == "Have":
            if delta["after"] >= parsed["amount"]:
                return {
                    "outcome": "success",
                    "reason_code": "inventory_threshold_met",
                    "summary": f"{parsed['target']} now satisfies the requested amount.",
                    "evidence": delta,
                }
            return {
                "outcome": "partial" if delta["delta"] > 0 else "fail",
                "reason_code": "inventory_threshold_unmet",
                "summary": f"{parsed['target']} is still below the requested amount.",
                "evidence": delta,
            }

    placed_result = _placed_block_success(after_events)
    if placed_result is not None and any(token in lowered for token in PLACEABLE_HINTS):
        return placed_result

    if any(token in lowered for token in MOVE_HINTS):
        moved = _distance(_position(before_payload), _position(after_payload))
        if moved is None:
            return {
                "outcome": "unknown",
                "reason_code": "position_missing",
                "summary": "Missing before/after position data.",
                "evidence": {},
            }
        if moved >= 2.0:
            return {
                "outcome": "success",
                "reason_code": "position_changed",
                "summary": f"Bot moved {round(moved, 2)} blocks.",
                "evidence": {"distance": moved},
            }
        return {
            "outcome": "fail",
            "reason_code": "position_unchanged",
            "summary": f"Bot moved only {round(moved, 2)} blocks.",
            "evidence": {"distance": moved},
        }

    if any(token in lowered for token in EAT_HINTS):
        before_status = payload_status(before_payload)
        after_status = payload_status(after_payload)
        before_food = before_status.get("food")
        after_food = after_status.get("food")
        if before_food is not None and after_food is not None and float(after_food) > float(before_food):
            return {
                "outcome": "success",
                "reason_code": "hunger_improved",
                "summary": f"Hunger increased from {before_food} to {after_food}.",
                "evidence": {"before_hunger": before_food, "after_hunger": after_food},
            }
        return {
            "outcome": "unknown",
            "reason_code": "no_direct_food_signal",
            "summary": "No direct hunger increase was observed.",
            "evidence": {"before_hunger": before_food, "after_hunger": after_food},
        }

    return {
        "outcome": "unknown",
        "reason_code": "no_rule_matched",
        "summary": "No deterministic world-effect rule matched this task yet.",
        "evidence": {},
    }
