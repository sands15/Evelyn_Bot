from __future__ import annotations

import re

from voyager.agents.food_signals import edible_food_total
from voyager.agents.observation_utils import (
    observe_payload,
    payload_inventory,
    payload_list,
    payload_status,
)
from voyager.agents.survival_signals import hostiles_nearby, is_night

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

MOVE_AWAY_TASK_PATTERN = re.compile(
    r"^Move\s+(\d+)\s+blocks\s+away\s+from\s+current\s+position$",
    re.IGNORECASE,
)
ACQUIRE_EDIBLE_FOOD_TASK_PATTERN = re.compile(
    r"^Acquire\s+(\d+)\s+edible\s+food\s+items?$",
    re.IGNORECASE,
)
PLACE_COUNT_TASK_PATTERN = re.compile(r"^Place\s+(\d+)\s+([a-z0-9_ ]+)$", re.IGNORECASE)
PLACE_ARTICLE_TASK_PATTERN = re.compile(
    r"^Place\s+(?:a|an)\s+([a-z0-9_ ]+)$",
    re.IGNORECASE,
)
SIMPLE_AMOUNT_TASK_PATTERN = re.compile(
    r"^(Obtain|Have|Craft|Mine)\s+(\d+)\s+([a-z0-9_ ]+)$",
    re.IGNORECASE,
)
SMELT_INTO_TASK_PATTERN = re.compile(
    r"^Smelt\s+(\d+)\s+([a-z0-9_ ]+)\s+into\s+([a-z0-9_ ]+)$",
    re.IGNORECASE,
)
HAVE_INTO_TASK_PATTERN = re.compile(
    r"^Have\s+(\d+)\s+([a-z0-9_ ]+)\s+into\s+([a-z0-9_ ]+)$",
    re.IGNORECASE,
)
SMELT_FUEL_SUFFIX_PATTERN = re.compile(
    r"\s+(?:using|with)\s+[a-z0-9_ ]+$",
    re.IGNORECASE,
)
SMELT_SHORTHAND_TASK_PATTERN = re.compile(
    r"^Smelt\s+(\d+)\s+([a-z0-9_ ]+)$",
    re.IGNORECASE,
)

EXACT_CONTRACT_TASKS = {
    "Reach a surface position",
    "Establish a lit temporary shelter",
}

SPECIAL_ALLOWED_PREFIXES = (
    "Deposit useless items into the chest at",
)


def _surface_like_payload(payload):
    nearby_blocks = set(payload_list(payload, "voxels"))
    nearby_blocks.update(payload_list(payload, "nearby_blocks"))
    return any(str(block).lower() in SURFACE_BLOCK_HINTS for block in nearby_blocks)
def parse_smelt_result_task(task_text):
    text = str(task_text or "").strip()
    normalized_text = SMELT_FUEL_SUFFIX_PATTERN.sub("", text).strip()
    for pattern, style in (
        (SMELT_INTO_TASK_PATTERN, "smelt_into"),
        (HAVE_INTO_TASK_PATTERN, "have_into"),
    ):
        match = pattern.fullmatch(normalized_text)
        if not match:
            continue
        amount_text, input_target, output_target = match.groups()
        return {
            "amount": int(amount_text),
            "input_target": str(input_target or "").strip().lower().replace(" ", "_"),
            "output_target": str(output_target or "").strip().lower().replace(" ", "_"),
            "style": style,
        }
    return None


def canonicalize_smelt_result_task(task_text):
    parsed = parse_smelt_result_task(task_text)
    if parsed is None:
        return None
    verb = "Smelt" if parsed["style"] == "smelt_into" else "Have"
    return f"{verb} {parsed['amount']} {parsed['input_target']} into {parsed['output_target']}"


def _status_number(status, key, default=0.0):
    try:
        return float((status or {}).get(key, default))
    except Exception:
        return float(default)


class TaskContractPolicy:
    def _rewrite_unknown_task(self, task_text, payload):
        lowered = str(task_text or "").strip().lower()
        status = payload_status(payload)
        inventory = payload_inventory(payload)
        entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
        health = _status_number(status, "health", default=20.0)
        hunger = _status_number(status, "food", default=20.0)
        hostile_nearby = hostiles_nearby(entities)

        rewrite_rules = [
            (
                lambda: any(token in lowered for token in ("food", "eat", "cook", "animal", "crop"))
                or (hunger <= 8 and edible_food_total(inventory) <= 0),
                "Acquire 1 edible food item",
                "Task contract rewrite: convert the non-verifiable task into a directly checkable food recovery contract.",
            ),
            (
                lambda: any(token in lowered for token in ("shelter", "safe", "hostile", "night"))
                or hostile_nearby
                or health <= 10
                or is_night(status),
                "Establish a lit temporary shelter",
                "Task contract rewrite: convert the non-verifiable task into a directly checkable shelter stabilization contract.",
            ),
            (
                lambda: any(token in lowered for token in ("retreat", "surface", "recover")),
                "Reach a surface position",
                "Task contract rewrite: convert the non-verifiable recovery task into a directly checkable surface recovery contract.",
            ),
            (
                lambda: any(token in lowered for token in ("move", "travel", "explore")) and _surface_like_payload(payload),
                "Move 24 blocks away from current position",
                "Task contract rewrite: convert the non-verifiable travel task into a directly checkable reposition contract.",
            ),
            (
                lambda: any(token in lowered for token in ("move", "travel", "explore")),
                "Reach a surface position",
                "Task contract rewrite: convert the non-verifiable travel task into a directly checkable recovery contract.",
            ),
            (
                lambda: any(token in lowered for token in ("wood", "log", "tree")),
                "Obtain 8 wood logs",
                "Task contract rewrite: convert the non-verifiable resource task into a directly checkable wood acquisition contract.",
            ),
        ]
        for should_apply, rewrite_task, rewrite_context in rewrite_rules:
            if should_apply():
                return rewrite_task, rewrite_context
        return (
            "Obtain 8 wood logs",
            "Task contract rewrite: convert the non-verifiable task into a verifier-known bootstrap resource contract.",
        )

    def _append_contract(self, context, contract_line):
        base = str(context or "").strip()
        contract = str(contract_line or "").strip()
        if not contract:
            return base
        if base:
            return base + "\n\nContract: " + contract
        return "Contract: " + contract

    def normalize_task_choice(self, task, context, *, events=None):
        task_text = str(task or "").strip()
        if not task_text:
            return task_text, context
        payload = observe_payload(events)
        inventory = payload_inventory(payload)

        canonical_smelt_task = canonicalize_smelt_result_task(task_text)
        if canonical_smelt_task is not None and canonical_smelt_task != task_text:
            return canonical_smelt_task, context

        place_article_match = PLACE_ARTICLE_TASK_PATTERN.match(task_text)
        if place_article_match:
            item_name = place_article_match.group(1).strip()
            return (
                f"Place 1 {item_name}",
                self._append_contract(
                    context,
                    f"Success means the bot places {item_name} in the world during this step.",
                ),
            )

        if task_text == "Retreat to a safe position":
            if _surface_like_payload(payload):
                return (
                    "Move 24 blocks away from current position",
                    self._append_contract(
                        context,
                        "Success means the bot finishes at least 24 blocks away from the starting checkpoint for this step.",
                    ),
                )
            return (
                "Reach a surface position",
                self._append_contract(
                    context,
                    "Success means the bot exits the underground domain and ends in a surface-like area with open terrain cues such as grass, logs, leaves, or other surface blocks.",
                ),
            )

        if task_text == "Find food source":
            food_target = max(1, 1 - edible_food_total(inventory))
            return (
                f"Acquire {food_target} edible food item",
                self._append_contract(
                    context,
                    "Success means the inventory gains at least one directly edible food item or already contains one.",
                ),
            )

        if task_text == "Build a temporary shelter":
            return (
                "Establish a lit temporary shelter",
                self._append_contract(
                    context,
                    "Success means the bot places shelter-related blocks or lighting and ends without immediate nearby hostile pressure.",
                ),
            )

        smelt_shorthand_match = SMELT_SHORTHAND_TASK_PATTERN.match(task_text)
        if smelt_shorthand_match:
            amount_text, output_target = smelt_shorthand_match.groups()
            normalized_target = output_target.strip()
            return (
                f"Have {amount_text} {normalized_target}",
                self._append_contract(
                    context,
                    f"Interpret shorthand smelt task as an inventory-result contract: success means the inventory reaches at least {amount_text} {normalized_target}.",
                ),
            )

        return task_text, context

    def contract_state(self, task):
        task_text = str(task or "").strip()
        if not task_text:
            return "empty"
        if task_text in EXACT_CONTRACT_TASKS:
            return "deterministic"
        if any(task_text.startswith(prefix) for prefix in SPECIAL_ALLOWED_PREFIXES):
            return "special"
        if MOVE_AWAY_TASK_PATTERN.match(task_text):
            return "deterministic"
        if ACQUIRE_EDIBLE_FOOD_TASK_PATTERN.match(task_text):
            return "deterministic"
        if PLACE_COUNT_TASK_PATTERN.match(task_text):
            return "deterministic"
        if SIMPLE_AMOUNT_TASK_PATTERN.match(task_text):
            return "deterministic"
        if parse_smelt_result_task(task_text) is not None:
            return "deterministic"
        if SMELT_SHORTHAND_TASK_PATTERN.match(task_text):
            return "deterministic"
        return "unknown"

    def enforce_task_choice(self, task, context, *, events=None):
        original_task = str(task or "").strip()
        normalized_task, normalized_context = self.normalize_task_choice(
            task,
            context,
            events=events,
        )
        state = self.contract_state(normalized_task)
        decision = {
            "input_task": original_task,
            "normalized_task": normalized_task,
            "final_task": normalized_task,
            "state": state,
            "lowered": normalized_task != original_task,
            "fallback_applied": False,
        }
        if state != "unknown":
            return normalized_task, normalized_context, decision

        payload = observe_payload(events)
        fallback_task, fallback_context = self._rewrite_unknown_task(normalized_task, payload)
        final_context = str(normalized_context or "").strip()
        if fallback_context:
            final_context = f"{final_context}\n\n{fallback_context}".strip()
        final_context = self._append_contract(
            final_context,
            f"Original non-contract task '{normalized_task}' was rewritten to a verifier-known task.",
        )
        decision.update(
            final_task=fallback_task,
            state="fallback",
            fallback_applied=True,
        )
        return fallback_task, final_context, decision
