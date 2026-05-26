from __future__ import annotations

from typing import Callable

from voyager.agents.survival_signals import inventory_has_food


class CurriculumFallbackPolicy:
    def __init__(
        self,
        *,
        normalize_task: Callable[[str], str],
        is_repeatable_state_task: Callable[[str], bool],
        task_inventory_satisfied: Callable[[str, dict], bool],
        predict_task_from_inventory: Callable[[dict, object, str], tuple[str, str, str] | None],
        nearby_progression_candidates: Callable[[object], list[tuple[str, str]]],
        recovery_fallback_task: Callable[[dict], tuple[str, str]],
        count_logs: Callable[[dict], int],
        count_planks: Callable[[dict], int],
    ):
        self._normalize_task = normalize_task
        self._is_repeatable_state_task = is_repeatable_state_task
        self._task_inventory_satisfied = task_inventory_satisfied
        self._predict_task_from_inventory = predict_task_from_inventory
        self._nearby_progression_candidates = nearby_progression_candidates
        self._recovery_fallback_task = recovery_fallback_task
        self._count_logs = count_logs
        self._count_planks = count_planks

    def select_unblocked_task(self, candidates, *, blocked_tasks=None, inventory=None):
        blocked = {
            self._normalize_task(task)
            for task in (blocked_tasks or [])
            if str(task or "").strip()
        }
        for task, context in candidates:
            normalized_task = self._normalize_task(task)
            if normalized_task in blocked and not self._is_repeatable_state_task(normalized_task):
                if inventory is None or self._task_inventory_satisfied(normalized_task, inventory):
                    continue
            return normalized_task, context
        return None

    def fallback_after_local_search_failure(self, *, events, voxels, inventory, failed_record=None, blocked_tasks=None):
        failed_reason = str((failed_record or {}).get("reason") or "")
        candidates = []
        predicted = self._predict_task_from_inventory(inventory, events, "fallback")
        if predicted:
            next_task, next_context, _ = predicted
            candidates.append((next_task, next_context))
        candidates.extend(self._nearby_progression_candidates(events))
        if failed_reason.startswith("surface_recovery_"):
            candidates.append((
                "Reach a surface position",
                "Recent recovery search could not find a clean surface route. Re-stabilize locally, avoid hazards, and let the next task choose a different recovery direction or safer travel context.",
            ))
        if failed_reason.startswith("food_scout_") and inventory_has_food(inventory):
            candidates.append((
                "Acquire 1 edible food item",
                "Recent food scouting was inefficient. Turn nearby or already-owned food resources into directly edible survival value before attempting a broader search again.",
            ))
        if self._count_logs(inventory) and self._count_planks(inventory) < 8:
            candidates.append((
                "Craft 8 wood planks",
                "The previous search was inefficient. Use this turn to strengthen travel and crafting prerequisites instead of repeating a long local search.",
            ))
        if any("log" in block for block in (voxels or [])):
            candidates.append((
                "Obtain 8 wood logs",
                "The previous target was not nearby or the scout path stalled. Gather reusable travel resources from the current area, then let the next task choose a new direction or biome.",
            ))
        if failed_reason.startswith("ore_scout_"):
            candidates.append((
                "Move 24 blocks away from current position",
                "Recent ore scouting failed to find a productive underground route. Try local prerequisite recovery first; if nothing better applies, reposition and let the next task choose a different ore direction, elevation, or prerequisite.",
            ))
        candidates.append(self._recovery_fallback_task(inventory))
        selected = self.select_unblocked_task(candidates, blocked_tasks=blocked_tasks, inventory=inventory)
        if selected is not None:
            return selected
        return (
            "Move 24 blocks away from current position",
            "Fallback recovery exhausted repeated or already-completed tasks. Re-stabilize locally and force the next curriculum step to choose a different direction, prerequisite, or novelty.",
        )
