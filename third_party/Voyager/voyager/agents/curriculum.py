from __future__ import annotations

import os
import random
import re
import time
import warnings

import voyager.utils as U
from voyager.prompts import load_prompt
from voyager.utils.console import safe_print as print
from voyager.utils.json_utils import fix_and_parse_json
from voyager.agents.inventory_planner import (
    InventoryFirstPlanner,
    InventoryState,
    capability_satisfies_item as planner_capability_satisfies_item,
    canonical_item_name as planner_canonical_item_name,
    count_capability_at_least as planner_count_capability_at_least,
    has_armor_at_least as planner_has_armor_at_least,
    has_tool_at_least as planner_has_tool_at_least,
    material_tier as planner_material_tier,
    split_tiered_item as planner_split_tiered_item,
)
from voyager.agents.curriculum_recovery_policy import CurriculumRecoveryPolicy
from voyager.agents.curriculum_fallback_policy import CurriculumFallbackPolicy
from voyager.agents.curriculum_failure_policy import CurriculumFailurePolicy
from voyager.agents.curriculum_reason_policy import CurriculumReasonPolicy
from voyager.agents.curriculum_context_policy import CurriculumContextPolicy
from voyager.agents.curriculum_qa_cache import CurriculumQACache
from voyager.agents.objective_templates import OBJECTIVE_TEMPLATES, infer_objective_template
from voyager.agents.task_contract_policy import TaskContractPolicy
from voyager.agents.observation_utils import (
    observe_payload,
    payload_dict,
    payload_inventory,
    payload_list,
    payload_status,
    safe_int,
)
from voyager.agents.progression_policy import EarlyGameProgressionPolicy
from voyager.agents.survival_signals import inventory_has_food, is_night
from langchain.chat_models import ChatOpenAI

ORE_TASK_ITEM_MAP = {
    "coal_ore": "coal",
    "iron_ore": "raw_iron",
    "copper_ore": "raw_copper",
}
CRAFT_TASK_PATTERN = re.compile(r"^Craft\s+(\d+)\s+(.+)$", re.IGNORECASE)
WOOD_VARIANT_PREFIXES = (
    "oak",
    "spruce",
    "birch",
    "jungle",
    "acacia",
    "dark_oak",
    "mangrove",
    "cherry",
    "bamboo",
    "crimson",
    "warped",
)
TASK_ITEM_PATTERN = re.compile(r"^(?:Obtain|Mine|Craft|Smelt|Kill|Cook|Eat)\s+\d+\s+(.+)$", re.IGNORECASE)
OBTAIN_TASK_PATTERN = re.compile(r"^Obtain\s+(\d+)\s+([a-z0-9_]+)$", re.IGNORECASE)
SMELT_RAW_IRON_TASK_PATTERN = re.compile(r"^Smelt\s+(\d+)\s+raw_iron\s+into\s+iron_ingots?$", re.IGNORECASE)
LOCAL_SEARCH_EXHAUSTED_REASON = "LOCAL_SEARCH_EXHAUSTED"
SEARCH_FAILURE_REASONS = {
    LOCAL_SEARCH_EXHAUSTED_REASON,
    "surface_recovery_stalled",
    "surface_recovery_timeout",
    "surface_recovery_exhausted",
    "wood_scout_stalled",
    "wood_scout_timeout",
    "wood_scout_exhausted",
    "food_scout_stalled",
    "food_scout_timeout",
    "food_scout_exhausted",
    "ore_scout_stalled",
    "ore_scout_timeout",
    "ore_scout_exhausted",
}
ACTION_GENERATION_FAILURE_REASON = "action_generation_failed"
RESET_ONLY_LOOP_FAILURE_REASON = "reset_only_loop"
REPEAT_BLOCK_FAILURE_REASONS = SEARCH_FAILURE_REASONS.union({
    ACTION_GENERATION_FAILURE_REASON,
    RESET_ONLY_LOOP_FAILURE_REASON,
    "action_parse_failed",
    "max_retries_exhausted",
})
LOCAL_SEARCH_FAILURE_SNIPPETS = (
    "local_search_exhausted",
    "max exploration time reached",
    "not nearby",
    "current biome/terrain is inefficient",
    "could not find",
)
TASK_KEYWORD_STOPWORDS = {
    "obtain",
    "mine",
    "craft",
    "smelt",
    "kill",
    "cook",
    "eat",
    "equip",
    "into",
    "from",
    "the",
    "a",
    "an",
    "at",
    "of",
    "and",
    "useless",
    "item",
    "items",
    "chest",
}
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


def _inv_count(inventory, item_name):
    return int(inventory.get(item_name) or 0)


def _count_planks(inventory):
    return sum(
        int(count or 0)
        for name, count in inventory.items()
        if isinstance(name, str) and name.endswith("_planks")
    )


def _count_logs(inventory):
    return sum(
        int(count or 0)
        for name, count in inventory.items()
        if isinstance(name, str) and (name.endswith("_log") or name.endswith("_stem"))
    )


def _count_generic_stone(inventory):
    return sum(
        _inv_count(inventory, name)
        for name in ["cobblestone", "cobbled_deepslate", "blackstone"]
    )


def _split_tiered_item(item_name):
    return planner_split_tiered_item(item_name)


def _material_tier(kind, material):
    return planner_material_tier(kind, material)


def _count_capability_at_least(inventory, kind, slot, minimum_material):
    return planner_count_capability_at_least(inventory, kind, slot, minimum_material)


def _has_tool_at_least(inventory, tool_type, minimum_material):
    return planner_has_tool_at_least(inventory, tool_type, minimum_material)


def _has_armor_at_least(inventory, armor_slot, minimum_material):
    return planner_has_armor_at_least(inventory, armor_slot, minimum_material)


def _capability_satisfies_item(inventory, item_name, quantity=1):
    return planner_capability_satisfies_item(inventory, item_name, quantity)


def _has_named_planks(inventory, prefix, needed):
    candidates = [f"{prefix}_planks"]
    if prefix == "wood":
        return _count_planks(inventory) >= needed
    return sum(_inv_count(inventory, name) for name in candidates) >= needed


def _position_key(position):
    if not isinstance(position, dict):
        return None
    try:
        return tuple(round(float(position.get(axis)), 1) for axis in ("x", "y", "z"))
    except (TypeError, ValueError):
        return None


def _can_craft_tool(inventory, material, sticks_needed, units_needed):
    if _inv_count(inventory, "stick") < sticks_needed:
        return False
    if material == "wooden":
        return _count_planks(inventory) >= units_needed
    if material == "golden":
        return _inv_count(inventory, "gold_ingot") >= units_needed
    if material == "stone":
        return _count_generic_stone(inventory) >= units_needed
    if material == "iron":
        return _inv_count(inventory, "iron_ingot") >= units_needed
    if material == "diamond":
        return _inv_count(inventory, "diamond") >= units_needed
    if material == "netherite":
        return False
    return True


def _recipe_gate(item_name, quantity, inventory):
    if quantity <= 0:
        return True
    if item_name.endswith("_planks"):
        # 1 log -> 4 planks
        return _count_logs(inventory) * 4 >= quantity
    if item_name == "stick":
        # 2 planks -> 4 sticks
        required_planks = ((quantity + 3) // 4) * 2
        return _count_planks(inventory) >= required_planks
    if item_name == "crafting_table":
        return _count_planks(inventory) >= 4 * quantity
    if item_name == "chest":
        return _count_planks(inventory) >= 8 * quantity
    if item_name == "furnace":
        return _count_generic_stone(inventory) >= 8 * quantity
    if item_name == "shield":
        return _count_planks(inventory) >= 6 * quantity and _inv_count(inventory, "iron_ingot") >= quantity
    if item_name.endswith("_pickaxe"):
        material = item_name[: -len("_pickaxe")]
        return _can_craft_tool(inventory, material, 2 * quantity, 3 * quantity)
    if item_name.endswith("_axe"):
        material = item_name[: -len("_axe")]
        return _can_craft_tool(inventory, material, 2 * quantity, 3 * quantity)
    if item_name.endswith("_sword"):
        material = item_name[: -len("_sword")]
        return _can_craft_tool(inventory, material, 1 * quantity, 2 * quantity)
    if item_name.endswith("_shovel"):
        material = item_name[: -len("_shovel")]
        return _can_craft_tool(inventory, material, 2 * quantity, 1 * quantity)
    if item_name.endswith("_hoe"):
        material = item_name[: -len("_hoe")]
        return _can_craft_tool(inventory, material, 2 * quantity, 2 * quantity)
    if item_name.endswith("_slab"):
        base_name = item_name[: -len("_slab")]
        crafts_needed = (quantity + 5) // 6
        if base_name in ("stone", "cobblestone", "cobbled_deepslate", "blackstone"):
            if base_name == "stone":
                return _inv_count(inventory, "stone") >= 3 * crafts_needed
            return _inv_count(inventory, base_name) >= 3 * crafts_needed
        for prefix in WOOD_VARIANT_PREFIXES:
            if base_name == prefix:
                return _has_named_planks(inventory, prefix, 3 * crafts_needed)
    return True


def _task_keywords(task):
    normalized = str(task or "").strip().lower().replace("_", " ")
    match = TASK_ITEM_PATTERN.match(str(task or "").strip())
    if match:
        normalized = match.group(1).strip().lower().replace("_", " ")
    keywords = set()
    for token in re.findall(r"[a-z0-9]+", normalized):
        if token.isdigit() or token in TASK_KEYWORD_STOPWORDS:
            continue
        if token.endswith("ies") and len(token) > 4:
            token = token[:-3] + "y"
        elif token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
            token = token[:-1]
        keywords.add(token)
    return keywords


def _task_text(task):
    return str(task or "").strip().lower().replace("_", " ")


def _status_number(status, key, default=0):
    try:
        value = status.get(key) if isinstance(status, dict) else default
        return float(value)
    except Exception:
        return float(default)


def _has_any(inventory, *names):
    return any(_inv_count(inventory, name) > 0 for name in names)


def _event_age_seconds(event):
    if not isinstance(event, dict):
        return None
    recorded_at = event.get("recorded_at") or event.get("respawn_observed_at")
    if not isinstance(recorded_at, str) or not recorded_at:
        return None
    try:
        iso_text = recorded_at.replace("Z", "+00:00")
        return max(0.0, time.time() - __import__("datetime").datetime.fromisoformat(iso_text).timestamp())
    except Exception:
        return None


from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.schema import HumanMessage, SystemMessage
from langchain.vectorstores import Chroma


class CurriculumAgent:
    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        qa_model_name="gpt-3.5-turbo",
        qa_temperature=0,
        request_timout=120,
        ckpt_dir="ckpt",
        resume=False,
        mode="auto",
        warm_up=None,
        core_inventory_items: str | None = None,
        llm_url=None,
        qa_llm_url=None,
    ):
        llm_kwargs = {
            "model_name": model_name,
            "temperature": temperature,
            "request_timeout": request_timout,
        }
        if llm_url:
            llm_kwargs["openai_api_base"] = llm_url.removesuffix("/chat/completions")
        self.llm = ChatOpenAI(**llm_kwargs)
        qa_llm_kwargs = {
            "model_name": qa_model_name,
            "temperature": qa_temperature,
            "request_timeout": request_timout,
        }
        if qa_llm_url:
            qa_llm_kwargs["openai_api_base"] = qa_llm_url.removesuffix("/chat/completions")
        self.qa_llm = ChatOpenAI(**qa_llm_kwargs)
        assert mode in [
            "auto",
            "manual",
        ], f"mode {mode} not supported"
        self.mode = mode
        self.ckpt_dir = ckpt_dir
        self.reason_policy = CurriculumReasonPolicy(
            action_generation_failure_reason=ACTION_GENERATION_FAILURE_REASON,
            reset_only_loop_failure_reason=RESET_ONLY_LOOP_FAILURE_REASON,
            search_failure_reasons=SEARCH_FAILURE_REASONS,
            local_search_failure_snippets=LOCAL_SEARCH_FAILURE_SNIPPETS,
            local_search_exhausted_reason=LOCAL_SEARCH_EXHAUSTED_REASON,
        )
        self.context_policy = CurriculumContextPolicy()
        self.task_contract_policy = TaskContractPolicy()
        self.last_task_contract_decision = None
        U.f_mkdir(f"{ckpt_dir}/curriculum/vectordb")
        if resume:
            print(f"\033[35mLoading Curriculum Agent from {ckpt_dir}/curriculum\033[0m")
            completed_tasks_path = f"{ckpt_dir}/curriculum/completed_tasks.json"
            failed_tasks_path = f"{ckpt_dir}/curriculum/failed_tasks.json"
            qa_cache_path = f"{ckpt_dir}/curriculum/qa_cache.json"
            self.completed_tasks = (
                U.load_json(completed_tasks_path) if os.path.exists(completed_tasks_path) else []
            )
            raw_failed_tasks = (
                U.load_json(failed_tasks_path) if os.path.exists(failed_tasks_path) else []
            )
            self.failed_tasks = self._normalize_failed_task_records(raw_failed_tasks)
            self.qa_cache = (
                U.load_json(qa_cache_path) if os.path.exists(qa_cache_path) else {}
            )
        else:
            self.completed_tasks = []
            self.failed_tasks = []
            self.qa_cache = {}
        self.speculative_next_task = None
        self.last_speculative_decision = None
        self.last_inventory_plan = None
        self.last_completed_task = None
        self.current_objective_template = OBJECTIVE_TEMPLATES["progression"]
        self.active_plan_state = None
        self.progression_policy = EarlyGameProgressionPolicy(
            get_completed_tasks=lambda: self.completed_tasks,
            get_nearby_progression_candidates=self._nearby_progression_candidates,
        )
        self.recovery_policy = CurriculumRecoveryPolicy(
            count_logs=_count_logs,
            count_planks=_count_planks,
            has_tool_at_least=_has_tool_at_least,
            status_number=_status_number,
            event_age_seconds=_event_age_seconds,
        )
        self.fallback_policy = CurriculumFallbackPolicy(
            normalize_task=self.normalize_task,
            is_repeatable_state_task=self._is_repeatable_state_task,
            task_inventory_satisfied=self._task_inventory_satisfied,
            predict_task_from_inventory=self._predict_task_from_inventory,
            nearby_progression_candidates=self._nearby_progression_candidates,
            recovery_fallback_task=self.recovery_policy.fallback_recovery_task,
            count_logs=_count_logs,
            count_planks=_count_planks,
        )
        self.failure_policy = CurriculumFailurePolicy(
            normalize_task=self.normalize_task,
            task_keywords=_task_keywords,
            current_position_key=self._current_position_key,
            search_failure_reasons=SEARCH_FAILURE_REASONS,
            repeat_block_failure_reasons=REPEAT_BLOCK_FAILURE_REASONS,
        )
        # vectordb for qa cache
        self.qa_cache_questions_vectordb = Chroma(
            collection_name="qa_cache_questions_vectordb",
            embedding_function=OpenAIEmbeddings(),
            persist_directory=f"{ckpt_dir}/curriculum/vectordb",
        )
        qa_count = self.qa_cache_questions_vectordb._collection.count()
        if qa_count != len(self.qa_cache):
            warnings.warn(
                "Curriculum Agent qa cache vectordb is out of sync; rebuilding from qa_cache.json.",
                RuntimeWarning,
            )
            existing = self.qa_cache_questions_vectordb._collection.get()
            existing_ids = existing.get("ids", []) if isinstance(existing, dict) else []
            if existing_ids:
                self.qa_cache_questions_vectordb._collection.delete(ids=existing_ids)
            if self.qa_cache:
                self.qa_cache_questions_vectordb.add_texts(texts=list(self.qa_cache.keys()))
            self.qa_cache_questions_vectordb.persist()
        self.qa_cache_helper = CurriculumQACache(
            qa_cache=self.qa_cache,
            vectordb=self.qa_cache_questions_vectordb,
            cache_path=f"{ckpt_dir}/curriculum/qa_cache.json",
        )
        # if warm up not defined, initialize it as a dict, else, initialize all the missing value as a default value
        if not warm_up:
            warm_up = self.default_warmup
        self.warm_up = {}
        if "optional_inventory_items" in warm_up:
            assert core_inventory_items is not None
            self._core_inv_items_regex = re.compile(core_inventory_items)
            self.warm_up["optional_inventory_items"] = warm_up[
                "optional_inventory_items"
            ]
        else:
            self.warm_up["optional_inventory_items"] = 0
        for key in self.curriculum_observations:
            self.warm_up[key] = warm_up.get(key, self.default_warmup[key])
        self.warm_up["nearby_blocks"] = 0
        self.warm_up["inventory"] = 0
        self.warm_up["completed_tasks"] = 0
        self.warm_up["failed_tasks"] = 0

    @property
    def default_warmup(self):
        return {
            "context": 15,
            "biome": 10,
            "time": 15,
            "nearby_blocks": 0,
            "other_blocks": 10,
            "nearby_entities": 5,
            "health": 15,
            "hunger": 15,
            "position": 0,
            "equipment": 0,
            "inventory": 0,
            "optional_inventory_items": 7,
            "chests": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
        }

    @property
    def curriculum_observations(self):
        return [
            "context",
            "biome",
            "time",
            "nearby_blocks",
            "other_blocks",
            "nearby_entities",
            "health",
            "hunger",
            "position",
            "equipment",
            "inventory",
            "chests",
            "completed_tasks",
            "failed_tasks",
        ]

    @property
    def progress(self):
        return len(self.completed_tasks)

    def render_system_message(self):
        system_message = SystemMessage(content=load_prompt("curriculum"))
        assert isinstance(system_message, SystemMessage)
        return system_message

    def _normalize_failed_task_records(self, raw_failed_tasks):
        records = []
        if not isinstance(raw_failed_tasks, list):
            return records
        for entry in raw_failed_tasks:
            if isinstance(entry, str):
                records.append(
                    {
                        "task": entry,
                        "reason": "Unknown",
                        "evidence": "",
                        "repeat_count": 1,
                        "last_seen_at": None,
                        "last_error_type": None,
                        "last_program_name": None,
                    }
                )
            elif isinstance(entry, dict):
                task = str(entry.get("task") or "").strip()
                if not task:
                    continue
                raw_reason = str(entry.get("reason") or "Unknown").strip() or "Unknown"
                raw_evidence = str(entry.get("evidence") or "").strip()
                normalized_reason, normalized_evidence = self._canonicalize_failure_reason(
                    raw_reason,
                    raw_evidence,
                    {
                        "completion_reason": entry.get("completion_reason"),
                    },
                )
                records.append(
                    {
                        "task": task,
                        "reason": normalized_reason,
                        "evidence": normalized_evidence,
                        "repeat_count": max(1, int(entry.get("repeat_count") or 1)),
                        "last_seen_at": entry.get("last_seen_at"),
                        "last_error_type": entry.get("last_error_type"),
                        "last_program_name": entry.get("last_program_name"),
                    }
                )
        deduped = []
        by_task = {}
        for record in records:
            task = record["task"]
            existing = by_task.get(task)
            if existing is None:
                by_task[task] = record
                deduped.append(record)
                continue
            existing["repeat_count"] = max(
                existing.get("repeat_count", 1), record.get("repeat_count", 1)
            )
            if record.get("reason") and record.get("reason") != "Unknown":
                existing["reason"] = record["reason"]
            if record.get("evidence"):
                existing["evidence"] = record["evidence"]
            if record.get("last_seen_at"):
                existing["last_seen_at"] = record["last_seen_at"]
            if record.get("last_error_type"):
                existing["last_error_type"] = record["last_error_type"]
            if record.get("last_program_name"):
                existing["last_program_name"] = record["last_program_name"]
        return deduped

    def _summarize_failed_tasks(self):
        if not self.failed_tasks:
            return "None"
        summaries = []
        for record in self.failed_tasks:
            task = record.get("task", "Unknown task")
            reason = record.get("reason") or "Unknown"
            repeat_count = record.get("repeat_count") or 1
            evidence = record.get("evidence") or ""
            summary = f"{task} [reason={reason}; repeats={repeat_count}"
            if evidence:
                summary += f"; evidence={evidence[:140]}"
            summary += "]"
            summaries.append(summary)
        return "; ".join(summaries)

    def summarize_failed_tasks(self):
        return self._summarize_failed_tasks()

    def _extract_failure_reason(self, info):
        return self.reason_policy.extract_failure_reason(info)

    def _canonicalize_failure_reason(self, reason, evidence, info=None):
        return self.reason_policy.canonicalize_failure_reason(reason, evidence, info)

    def _looks_like_non_runtime_failure_instruction(self, reason_text, evidence_text):
        return self.reason_policy.looks_like_non_runtime_failure_instruction(reason_text, evidence_text)

    def _looks_like_prompt_or_conversation_dump(self, reason_text, evidence_text):
        return self.reason_policy.looks_like_prompt_or_conversation_dump(reason_text, evidence_text)

    def _recent_local_search_failure(self, task):
        return self.failure_policy.recent_local_search_failure(task, self.failed_tasks)

    def _current_position_key(self, events):
        event = observe_payload(events)
        status = payload_status(event)
        return _position_key(status.get("position"))

    def _recent_blocking_failure(self, task, events):
        return self.failure_policy.recent_blocking_failure(task, events, self.failed_tasks)

    def _nearby_progression_candidates(self, events):
        event = observe_payload(events)
        voxels = set(payload_list(event, "voxels"))
        voxels.update(payload_list(event, "nearby_blocks"))
        inventory, _, _ = self._extract_live_inventory_state(events)
        completed_tasks = self._completed_task_names()
        status = payload_status(event)
        health = _status_number(status, "health", default=20)
        hunger = _status_number(status, "food", default=20)
        if health < 16 or hunger < 16:
            return []
        candidates = []

        def add_candidate(task, context):
            normalized = self.normalize_task(task)
            if normalized in completed_tasks:
                return
            if not self._is_repeatable_state_task(task) and self._task_inventory_satisfied(task, inventory):
                return
            candidates.append((task, context))

        if "coal_ore" in voxels:
            add_candidate(
                "Mine 4 coal_ore",
                "Food search is currently blocked but health and hunger are stable. Mine nearby visible coal to make useful progress without long travel.",
            )
        if "iron_ore" in voxels and _has_tool_at_least(inventory, "pickaxe", "stone"):
            add_candidate(
                "Mine 2 iron_ore",
                "Food search is currently blocked but health and hunger are stable. Mine nearby visible iron with the available pickaxe instead of resetting in place.",
            )
        if "grass_block" in voxels and _has_any(inventory, "wheat_seeds", "stone_hoe", "dirt"):
            add_candidate(
                "Move 24 blocks away from current position",
                "Food search has stalled from this checkpoint. Reposition safely before attempting a new food route or nearby progression task.",
            )
        return candidates

    def _completed_task_names(self):
        return {
            self.normalize_task(task)
            for task in self.completed_tasks
            if str(task or "").strip()
        }

    def _is_repeatable_state_task(self, task):
        task_text = str(task or "").strip().lower()
        return any(
            token in task_text
            for token in (
                "temporary shelter",
                "retreat to a safe position",
                "safe position",
                "reach a surface position",
                "move 24 blocks away from current position",
                "establish a lit temporary shelter",
                "find food source",
                "acquire 1 edible food item",
                "eat food",
                "cook food",
            )
        )

    def _task_inventory_satisfied(self, task, inventory):
        return InventoryFirstPlanner().is_task_satisfied(self.normalize_task(task), inventory)

    def _fallback_after_local_search_failure(self, events, failed_record=None, blocked_tasks=None):
        event = observe_payload(events)
        voxels = payload_list(event, "voxels")
        inventory, _, _ = self._extract_live_inventory_state(events)
        return self.fallback_policy.fallback_after_local_search_failure(
            events=events,
            voxels=voxels,
            inventory=inventory,
            failed_record=failed_record,
            blocked_tasks=blocked_tasks,
        )

    def _inventory_first_task(self, events, inventory, *, allow_optional=False):
        state = self._planner_state(events, inventory)
        planner = InventoryFirstPlanner(completed_tasks=self._completed_task_names())
        planned = planner.choose_next(
            state,
            previous_task="inventory_first",
            allow_optional=allow_optional,
            objective=self.current_objective_template.id,
        )
        candidates = []
        if planned:
            candidates.append((planned.task, planned.context))

        selected = self.fallback_policy.select_unblocked_task(
            candidates,
            blocked_tasks=self._completed_task_names(),
            inventory=inventory,
        )
        if selected is not None:
            task, context = selected
            chain = [planned.as_dict()] if planned else []
            self._start_or_refresh_active_plan(
                task=task,
                context=context,
                capability=planned.capability if planned else None,
                reason=planned.reason if planned else None,
                source="inventory_first",
                chain=chain,
            )
            self._set_last_inventory_plan(
                phase="selected",
                source="inventory_first",
                selected_task=task,
                selected_context=context,
                reason=planned.reason if planned else None,
                chain=chain,
                objective_template=self.current_objective_template.as_dict(),
                capability=planned.capability if planned else None,
                active_plan=self.active_plan_state,
            )
            print(
                f"\033[35mInventory-first selected task '{task}' from live inventory before curriculum LLM.\033[0m"
            )
            return task, context
        if planned:
            self._set_last_inventory_plan(
                phase="blocked",
                source="inventory_first",
                selected_task=planned.task,
                selected_context=planned.context,
                reason=planned.reason,
                chain=[planned.as_dict()],
                active_plan=self.active_plan_state,
            )
        else:
            self._set_last_inventory_plan(
                phase="no_match",
                source="inventory_first",
                selected_task=None,
                selected_context=None,
                reason="no_deterministic_inventory_rule_matched",
                chain=[],
                objective_template=self.current_objective_template.as_dict(),
                active_plan=self.active_plan_state,
            )
        return None

    def set_objective_template(self, goal_text=None, current_task=None):
        previous = getattr(self, "current_objective_template", None)
        self.current_objective_template = infer_objective_template(goal_text, current_task)
        if previous and getattr(previous, "id", None) != self.current_objective_template.id:
            self.active_plan_state = None
        return self.current_objective_template

    def _make_plan_node(self, *, task, context, capability=None, reason=None, source=None, status="ready"):
        return {
            "task": self.normalize_task(task),
            "context": context,
            "capability": capability,
            "reason": reason,
            "source": source,
            "status": status,
            "created_at": time.time(),
        }

    def _record_active_plan_transition(self, plan, transition, **extra):
        if not isinstance(plan, dict):
            return
        history = plan.get("transition_history") if isinstance(plan.get("transition_history"), list) else []
        entry = {
            "transition": transition,
            "recorded_at": time.time(),
        }
        entry.update(extra)
        history.append(entry)
        plan["transition_history"] = history[-20:]
        plan["last_transition"] = transition
        plan["updated_at"] = time.time()

    def _start_or_refresh_active_plan(self, *, task, context, capability=None, reason=None, source=None, chain=None):
        node = self._make_plan_node(
            task=task,
            context=context,
            capability=capability,
            reason=reason,
            source=source,
            status="ready",
        )
        previous = self.active_plan_state if isinstance(self.active_plan_state, dict) else None
        previous_plan_id = previous.get("plan_id") if previous else None
        nodes = []
        if isinstance(chain, list) and chain:
            for entry in chain:
                if not isinstance(entry, dict):
                    continue
                entry_task = self.normalize_task(entry.get("task"))
                if not entry_task:
                    continue
                nodes.append(
                    {
                        "task": entry_task,
                        "context": entry.get("context"),
                        "capability": entry.get("capability"),
                        "reason": entry.get("reason"),
                        "source": entry.get("source") or source,
                        "status": "ready" if entry_task == node["task"] else "pending",
                        "created_at": time.time(),
                    }
                )
        if not nodes:
            nodes = [dict(node)]
        self.active_plan_state = {
            "plan_id": previous_plan_id or f"objective-plan-{int(time.time() * 1000)}",
            "objective_template": self.current_objective_template.as_dict(),
            "source": source,
            "status": "active",
            "current_node": dict(node),
            "nodes": nodes,
            "pending_next_node": None,
            "last_transition": "selected",
            "transition_history": [],
            "updated_at": time.time(),
        }
        self._record_active_plan_transition(
            self.active_plan_state,
            "selected",
            task=node.get("task"),
            source=source,
            capability=capability,
        )
        return self.active_plan_state

    def _queue_pending_plan_node(self, *, trigger_task, next_task, context, reason=None, expected_minimums=None):
        if not isinstance(self.active_plan_state, dict):
            return None
        current_node = self.active_plan_state.get("current_node") if isinstance(self.active_plan_state.get("current_node"), dict) else None
        if not current_node or self.normalize_task(current_node.get("task")) != self.normalize_task(trigger_task):
            return None
        pending = self._make_plan_node(
            task=next_task,
            context=context,
            capability=None,
            reason=reason,
            source="speculative_successor",
            status="pending",
        )
        pending["trigger_task"] = self.normalize_task(trigger_task)
        pending["expected_minimums"] = dict(expected_minimums or {})
        self.active_plan_state["pending_next_node"] = pending
        self._record_active_plan_transition(
            self.active_plan_state,
            "pending_successor_prepared",
            trigger_task=self.normalize_task(trigger_task),
            next_task=pending.get("task"),
        )
        return pending

    def _consume_active_plan_task(self, events):
        plan = self.active_plan_state
        if not isinstance(plan, dict):
            return None
        current_node = plan.get("current_node") if isinstance(plan.get("current_node"), dict) else None
        if not current_node:
            self.active_plan_state = None
            return None
        task = self.normalize_task(current_node.get("task"))
        context = current_node.get("context") or ""
        if not task:
            self.active_plan_state = None
            return None
        if self.normalize_task(task) in self._completed_task_names():
            plan["status"] = "completed"
            self._record_active_plan_transition(plan, "completed_already_owned", task=task)
            return None
        inventory, _, _ = self._extract_live_inventory_state(events)
        if not self._is_repeatable_state_task(task) and self._task_inventory_satisfied(task, inventory):
            plan["status"] = "satisfied_without_execution"
            self._record_active_plan_transition(plan, "inventory_satisfied", task=task)
            return None
        if self._recent_blocking_failure(task, events):
            plan["status"] = "blocked"
            self._record_active_plan_transition(plan, "recent_blocking_failure", task=task)
            return None
        self._record_active_plan_transition(plan, "reused_current_node", task=task)
        return task, context

    def _apply_task_result_to_active_plan(self, info):
        plan = self.active_plan_state
        if not isinstance(plan, dict):
            return
        task = self.normalize_task(info.get("task"))
        current_node = plan.get("current_node") if isinstance(plan.get("current_node"), dict) else None
        if not current_node or self.normalize_task(current_node.get("task")) != task:
            return
        current_node["status"] = "completed" if info.get("success") else "failed"
        current_node["updated_at"] = time.time()
        if info.get("success"):
            pending = plan.get("pending_next_node") if isinstance(plan.get("pending_next_node"), dict) else None
            if pending and self.normalize_task(pending.get("trigger_task")) == task:
                promoted = dict(pending)
                promoted["status"] = "ready"
                promoted["promoted_at"] = time.time()
                plan["current_node"] = promoted
                nodes = plan.get("nodes") if isinstance(plan.get("nodes"), list) else []
                if all(self.normalize_task(node.get("task")) != self.normalize_task(promoted.get("task")) for node in nodes if isinstance(node, dict)):
                    nodes.append(dict(promoted))
                plan["nodes"] = nodes
                plan["pending_next_node"] = None
                self._record_active_plan_transition(
                    plan,
                    "advanced_to_next_node",
                    task=promoted.get("task"),
                    trigger_task=task,
                )
            else:
                plan["status"] = "completed"
                self._record_active_plan_transition(plan, "completed_without_successor", task=task)
        else:
            plan["status"] = "failed"
            plan["pending_next_node"] = None
            self._record_active_plan_transition(plan, "current_node_failed", task=task)

    def _planner_state(self, events, inventory=None):
        event = observe_payload(events)
        nearby_blocks = set(payload_list(event, "voxels"))
        nearby_blocks.update(payload_list(event, "nearby_blocks"))
        return InventoryState.from_observation(
            inventory=inventory if inventory is not None else payload_inventory(event),
            status=payload_status(event),
            nearby_blocks=nearby_blocks,
        )

    def _set_last_inventory_plan(
        self,
        *,
        phase,
        source,
        selected_task=None,
        selected_context=None,
        reason=None,
        chain=None,
        **extra,
    ):
        payload = {
            "phase": phase,
            "source": source,
            "selected_task": selected_task,
            "selected_context": selected_context,
            "reason": reason,
            "chain": chain or [],
            "created_at": time.time(),
        }
        payload.update(extra)
        self.last_inventory_plan = payload

    def _record_failed_task(self, info):
        task = str(info.get("task") or "").strip()
        if not task:
            return
        reason, evidence = self._extract_failure_reason(info)
        position_key = _position_key(info.get("last_position"))
        for record in self.failed_tasks:
            if record.get("task") == task:
                previous_position_key = record.get("last_position_key")
                record["repeat_count"] = int(record.get("repeat_count") or 1) + 1
                record["reason"] = reason or record.get("reason") or "Unknown"
                if evidence:
                    record["evidence"] = evidence
                record["last_seen_at"] = int(time.time())
                record["last_error_type"] = info.get("error_type")
                record["last_program_name"] = info.get("program_name")
                if position_key is not None:
                    record["last_position"] = info.get("last_position")
                    record["last_position_key"] = list(position_key)
                    if previous_position_key == list(position_key):
                        record["same_position_repeat_count"] = int(record.get("same_position_repeat_count") or 1) + 1
                    else:
                        record["same_position_repeat_count"] = 1
                return
        record = {
            "task": task,
            "reason": reason,
            "evidence": evidence,
            "repeat_count": 1,
            "last_seen_at": int(time.time()),
            "last_error_type": info.get("error_type"),
            "last_program_name": info.get("program_name"),
        }
        if position_key is not None:
            record["last_position"] = info.get("last_position")
            record["last_position_key"] = list(position_key)
            record["same_position_repeat_count"] = 1
        self.failed_tasks.append(record)

    def render_observation(self, *, events, chest_observation):
        assert events[-1][0] == "observe", "Last event must be observe"
        event = observe_payload(events)
        status = payload_status(event)
        biome = status.get("biome")
        time_of_day = status.get("timeOfDay")
        voxels = payload_list(event, "voxels")
        block_records = payload_list(event, "blockRecords")
        entities = payload_dict(status, "entities")
        health = status.get("health")
        hunger = status.get("food")
        position = status.get("position") if isinstance(status.get("position"), dict) else None
        equipment = status.get("equipment")
        inventory_used = safe_int(status.get("inventoryUsed") or 0)
        inventory = payload_inventory(event)

        if not any(
            "dirt" in block
            or "log" in block
            or "grass" in block
            or "sand" in block
            or "snow" in block
            for block in voxels
        ):
            biome = "underground"

        other_blocks = ", ".join(
            list(
                set(block_records).difference(set(voxels).union(set(inventory.keys())))
            )
        )

        other_blocks = other_blocks if other_blocks else "None"

        nearby_entities = (
            ", ".join([k for k, v in sorted(entities.items(), key=lambda x: x[1])])
            if entities
            else "None"
        )

        completed_tasks = (
            ", ".join(self.completed_tasks) if self.completed_tasks else "None"
        )
        failed_tasks = self._summarize_failed_tasks()

        # Always expose the full live inventory to the curriculum agent.
        # Hiding "optional" items made task selection blind to already-owned
        # resources like lapis, smelted ingots, and ores gathered before the
        # warm-up threshold.
        observation = {
            "context": "",
            "biome": f"Biome: {biome}\n\n",
            "time": f"Time: {time_of_day}\n\n",
            "nearby_blocks": f"Nearby blocks: {', '.join(voxels) if voxels else 'None'}\n\n",
            "other_blocks": f"Other blocks that are recently seen: {other_blocks}\n\n",
            "nearby_entities": f"Nearby entities: {nearby_entities}\n\n",
            "health": f"Health: {health:.1f}/20\n\n" if health is not None else "Health: Unknown\n\n",
            "hunger": f"Hunger: {hunger:.1f}/20\n\n" if hunger is not None else "Hunger: Unknown\n\n",
            "position": (
                f"Position: x={position['x']:.1f}, y={position['y']:.1f}, z={position['z']:.1f}\n\n"
                if position and all(position.get(axis) is not None for axis in ("x", "y", "z"))
                else "Position: Unknown\n\n"
            ),
            "equipment": f"Equipment: {equipment}\n\n",
            "inventory": f"Inventory ({inventory_used or 0}/36): {inventory if inventory else 'Empty'}\n\n",
            "chests": chest_observation,
            "completed_tasks": f"Completed tasks so far: {completed_tasks}\n\n",
            "failed_tasks": f"Failed tasks that are too hard: {failed_tasks}\n\n",
        }
        return observation

    def render_human_message(self, *, events, chest_observation):
        content = ""
        observation = self.render_observation(
            events=events, chest_observation=chest_observation
        )
        if self.progress >= self.warm_up["context"]:
            questions, answers = self.run_qa(
                events=events, chest_observation=chest_observation
            )
            i = 1
            for question, answer in zip(questions, answers):
                if "Answer: Unknown" in answer or "language model" in answer:
                    continue
                observation["context"] += f"Question {i}: {question}\n"
                observation["context"] += f"{answer}\n\n"
                i += 1
                if i > 5:
                    break

        for key in self.curriculum_observations:
            if self.progress >= self.warm_up[key]:
                if self.warm_up[key] != 0:
                    should_include = random.random() < 0.8
                else:
                    should_include = True
                if should_include:
                    content += observation[key]

        print(f"\033[35m****Curriculum Agent human message****\n{content}\033[0m")
        return HumanMessage(content=content)

    def _finalize_task_choice(self, task, context, events):
        finalized_task, finalized_context, decision = self.task_contract_policy.enforce_task_choice(
            task,
            context,
            events=events,
        )
        self.last_task_contract_decision = decision
        if str(finalized_task or "").strip() != str(task or "").strip():
            print(
                f"\033[35mTask contract lowered '{task}' to '{finalized_task}'.\033[0m"
            )
        if isinstance(decision, dict) and decision.get("fallback_applied"):
            print(
                f"\033[35mTask contract rejected non-verifiable task '{decision.get('normalized_task')}' and replaced it with '{finalized_task}'.\033[0m"
            )
        return finalized_task, finalized_context

    def propose_next_task(self, *, events, chest_observation, max_retries=5):
        latest_event = observe_payload(events)
        latest_status = payload_status(latest_event)
        latest_inventory = payload_inventory(latest_event)
        if self.progress == 0 and self.mode == "auto":
            bootstrap_stage = self.progression_policy.infer_stage(events)
            if bootstrap_stage <= 0:
                task = "Mine 1 wood log"
                context = (
                    "You can mine one of oak, birch, spruce, jungle, acacia, dark oak, or mangrove logs. "
                    "If no log is already nearby, prefer searchAndHarvest(bot, { goalType: \"wood\", quantity: 1, maxSearchBudgetSec: 24 }) "
                    "and recoverToSurface(...) when the current area is underground or unsuitable. "
                    "Do not hand-write long exploreUntil wandering loops for wood search."
                )
                return self._finalize_task_choice(task, context, events)
            bootstrap_inventory_first = self._inventory_first_task(
                events,
                latest_inventory,
                allow_optional=True,
            )
            if bootstrap_inventory_first:
                return self._finalize_task_choice(
                    bootstrap_inventory_first[0],
                    bootstrap_inventory_first[1],
                    events,
                )

        # hard code task when inventory is almost full
        inventory_used = safe_int(latest_status.get("inventoryUsed") or 0)
        if inventory_used >= 33:
            if chest_observation != "Chests: None\n\n":
                chests = chest_observation[8:-2].split("\n")
                for chest in chests:
                    content = chest.split(":")[1]
                    if content == " Unknown items inside" or content == " Empty":
                        position = chest.split(":")[0]
                        task = f"Deposit useless items into the chest at {position}"
                        context = (
                            f"Your inventory have {inventory_used} occupied slots before depositing. "
                            "After depositing, your inventory should only have 20 occupied slots. "
                            "You should deposit useless items such as andesite, dirt, cobblestone, etc. "
                            "Also, you can deposit low-level tools, "
                            "For example, if you have a stone pickaxe, you can deposit a wooden pickaxe. "
                            "Make sure the list of useless items are in your inventory "
                            "(do not list items already in the chest), "
                            "You can use bot.inventoryUsed() to check how many inventory slots are used."
                        )
                        return self._finalize_task_choice(task, context, events)
            if "chest" in latest_inventory:
                task = "Place 1 chest"
                context = (
                    f"You have a chest in inventory, place it around you. "
                    f"If chests is not None, or nearby blocks contains chest, this task is success."
                )
            else:
                task = "Craft 1 chest"
                context = "Craft 1 chest with 8 planks of any kind of wood."
            return self._finalize_task_choice(task, context, events)

        inventory_first = self._inventory_first_task(events, latest_inventory)
        if inventory_first:
            return self._finalize_task_choice(inventory_first[0], inventory_first[1], events)

        active_plan_task = self._consume_active_plan_task(events)
        if active_plan_task:
            return self._finalize_task_choice(active_plan_task[0], active_plan_task[1], events)

        speculative = self.consume_speculative_next_task(events=events)
        if speculative:
            return self._finalize_task_choice(speculative[0], speculative[1], events)

        messages = [
            self.render_system_message(),
            self.render_human_message(
                events=events, chest_observation=chest_observation
            ),
        ]

        if self.mode == "auto":
            task, context = self.propose_next_ai_task(messages=messages, events=events, max_retries=max_retries)
            return self._finalize_task_choice(task, context, events)
        elif self.mode == "manual":
            task, context = self.propose_next_manual_task()
            return self._finalize_task_choice(task, context, events)
        else:
            raise ValueError(f"Invalid curriculum agent mode: {self.mode}")

    def _task_success_delta(self, task, inventory):
        raw_task = str(task or "").strip()
        mine_match = re.fullmatch(r"Mine\s+(\d+)\s+([a-z0-9_ ]+)", raw_task, re.IGNORECASE)
        if mine_match:
            amount = int(mine_match.group(1))
            target = mine_match.group(2).strip().lower().replace(" ", "_")
            mined_item = ORE_TASK_ITEM_MAP.get(target)
            if mined_item:
                predicted = dict(inventory or {})
                before = _inv_count(predicted, mined_item)
                predicted[mined_item] = before + amount
                return predicted, {mined_item: before + amount}

        task = self.normalize_task(task)
        predicted = dict(inventory or {})
        expected_minimums = {}
        match = OBTAIN_TASK_PATTERN.match(task)
        if match:
            amount = int(match.group(1))
            item_name = match.group(2)
            before = _inv_count(predicted, item_name)
            predicted[item_name] = max(before, amount)
            expected_minimums[item_name] = max(before + 1, amount)
            return predicted, expected_minimums

        craft_match = CRAFT_TASK_PATTERN.match(task)
        if craft_match:
            amount = int(craft_match.group(1))
            item_name = planner_canonical_item_name(craft_match.group(2))
            before = _inv_count(predicted, item_name)
            simulated = InventoryFirstPlanner().recipe_catalog.simulate_craft(item_name, amount, predicted)
            if simulated:
                predicted, crafted_amount = simulated
                expected_minimums[item_name] = before + min(amount, crafted_amount)
                return predicted, expected_minimums

            predicted[item_name] = before + amount
            expected_minimums[item_name] = before + amount
            if item_name == "furnace":
                predicted["cobblestone"] = max(0, _inv_count(predicted, "cobblestone") - 8 * amount)
            elif item_name == "crafting_table":
                self._consume_planks(predicted, 4 * amount)
            elif item_name == "stick":
                self._consume_planks(predicted, 2 * max(1, (amount + 3) // 4))
            elif item_name == "torch":
                crafts = max(1, (amount + 3) // 4)
                self._consume_fuel(predicted, crafts)
                predicted["stick"] = max(0, _inv_count(predicted, "stick") - crafts)
            elif item_name.endswith("_pickaxe"):
                material = item_name[: -len("_pickaxe")]
                self._consume_tool_material(predicted, material, 3 * amount)
                predicted["stick"] = max(0, _inv_count(predicted, "stick") - 2 * amount)
            elif item_name.endswith("_axe"):
                material = item_name[: -len("_axe")]
                self._consume_tool_material(predicted, material, 3 * amount)
                predicted["stick"] = max(0, _inv_count(predicted, "stick") - 2 * amount)
            elif item_name.endswith("_sword"):
                material = item_name[: -len("_sword")]
                self._consume_tool_material(predicted, material, 2 * amount)
                predicted["stick"] = max(0, _inv_count(predicted, "stick") - amount)
            elif item_name.endswith("_shovel"):
                material = item_name[: -len("_shovel")]
                self._consume_tool_material(predicted, material, amount)
                predicted["stick"] = max(0, _inv_count(predicted, "stick") - 2 * amount)
            elif item_name.endswith("_hoe"):
                material = item_name[: -len("_hoe")]
                self._consume_tool_material(predicted, material, 2 * amount)
                predicted["stick"] = max(0, _inv_count(predicted, "stick") - 2 * amount)
            return predicted, expected_minimums

        smelt_match = SMELT_RAW_IRON_TASK_PATTERN.match(task)
        if smelt_match:
            amount = int(smelt_match.group(1))
            before = _inv_count(predicted, "iron_ingot")
            predicted["iron_ingot"] = before + amount
            predicted["raw_iron"] = max(0, _inv_count(predicted, "raw_iron") - amount)
            self._consume_fuel(predicted, max(1, (amount + 7) // 8))
            expected_minimums["iron_ingot"] = before + amount
            return predicted, expected_minimums

        return predicted, expected_minimums

    def _consume_planks(self, inventory, amount):
        remaining = max(0, int(amount or 0))
        for name in sorted(list(inventory.keys())):
            if remaining <= 0:
                break
            if not (isinstance(name, str) and name.endswith("_planks")):
                continue
            take = min(_inv_count(inventory, name), remaining)
            inventory[name] = max(0, _inv_count(inventory, name) - take)
            remaining -= take

    def _consume_tool_material(self, inventory, material, amount):
        if material == "stone":
            remaining = max(0, int(amount or 0))
            for name in ("cobblestone", "cobbled_deepslate", "blackstone"):
                if remaining <= 0:
                    break
                take = min(_inv_count(inventory, name), remaining)
                inventory[name] = max(0, _inv_count(inventory, name) - take)
                remaining -= take
        elif material == "iron":
            inventory["iron_ingot"] = max(0, _inv_count(inventory, "iron_ingot") - int(amount or 0))
        elif material == "wooden":
            self._consume_planks(inventory, amount)

    def _consume_fuel(self, inventory, amount):
        remaining = max(0, int(amount or 0))
        for name in ("coal", "charcoal"):
            if remaining <= 0:
                break
            take = min(_inv_count(inventory, name), remaining)
            inventory[name] = max(0, _inv_count(inventory, name) - take)
            remaining -= take

    def _predict_task_from_inventory(self, inventory, events, previous_task):
        state = self._planner_state(events, inventory)
        planned = InventoryFirstPlanner(
            completed_tasks=self._completed_task_names()
        ).choose_next(
            state,
            previous_task=previous_task,
            allow_optional=True,
            objective=self.current_objective_template.id,
        )
        if not planned:
            return None
        return (
            planned.task,
            planned.context.replace("Inventory-first:", "Speculative successor:"),
            planned.reason,
        )

    def prepare_speculative_next_task(self, task, events):
        event = observe_payload(events)
        inventory = payload_inventory(event)
        predicted_inventory, expected_minimums = self._task_success_delta(task, inventory)
        predicted = self._predict_task_from_inventory(predicted_inventory, events, task)
        if not predicted:
            self.speculative_next_task = None
            self.last_speculative_decision = {
                "phase": "not_prepared",
                "trigger_task": self.normalize_task(task),
                "reason": "no_rule_matched",
                "created_at": time.time(),
            }
            return None
        next_task, context, reason = predicted
        next_task = self.normalize_task(next_task)
        self.speculative_next_task = {
            "trigger_task": self.normalize_task(task),
            "next_task": next_task,
            "context": context,
            "reason": reason,
            "expected_minimums": expected_minimums,
            "created_at": time.time(),
        }
        self._queue_pending_plan_node(
            trigger_task=task,
            next_task=next_task,
            context=context,
            reason=reason,
            expected_minimums=expected_minimums,
        )
        self.last_speculative_decision = {
            "phase": "prepared",
            **self.speculative_next_task,
            "active_plan": self.active_plan_state,
        }
        print(
            f"\033[35mPrepared speculative next task after '{task}': '{next_task}' ({reason}).\033[0m"
        )
        return self.speculative_next_task

    def consume_speculative_next_task(self, events):
        pending = self.speculative_next_task
        if not isinstance(pending, dict):
            return None
        self.speculative_next_task = None
        trigger_task = self.normalize_task(pending.get("trigger_task"))
        next_task = self.normalize_task(pending.get("next_task"))
        decision = {
            "phase": "discarded",
            "trigger_task": trigger_task,
            "next_task": next_task,
            "reason": pending.get("reason"),
            "created_at": pending.get("created_at"),
            "consumed_at": time.time(),
        }
        if not next_task:
            decision["discard_reason"] = "missing_next_task"
            self.last_speculative_decision = decision
            return None
        if time.time() - float(pending.get("created_at") or 0) > 900:
            decision["discard_reason"] = "expired"
            self.last_speculative_decision = decision
            return None
        last_completed_task = self.last_completed_task
        if not last_completed_task and self.completed_tasks:
            last_completed_task = self.completed_tasks[-1]
        if trigger_task and self.normalize_task(last_completed_task) != trigger_task:
            decision["discard_reason"] = "trigger_not_last_completed"
            decision["last_completed_task"] = last_completed_task
            self.last_speculative_decision = decision
            return None
        inventory, _, _ = self._extract_live_inventory_state(events)
        for item_name, minimum in (pending.get("expected_minimums") or {}).items():
            if _inv_count(inventory, item_name) < int(minimum or 0):
                decision["discard_reason"] = f"expected_{item_name}_below_{minimum}"
                self.last_speculative_decision = decision
                return None
        if (
            next_task in self._completed_task_names()
            and not self._is_repeatable_state_task(next_task)
            and self._task_inventory_satisfied(next_task, inventory)
        ):
            decision["discard_reason"] = "next_task_already_completed"
            self.last_speculative_decision = decision
            return None
        context = str(pending.get("context") or self.get_task_context(next_task))
        task, context = self._guard_task_with_live_inventory(next_task, context, events)
        blocking_failure = self._recent_blocking_failure(task, events)
        if blocking_failure:
            decision["discard_reason"] = "next_task_blocked"
            decision["blocking_failure"] = blocking_failure.get("task")
            self.last_speculative_decision = decision
            return None
        decision.update({
            "phase": "accepted",
            "next_task": task,
            "context": context,
        })
        self.last_speculative_decision = decision
        print(
            f"\033[35mAccepted speculative next task after '{trigger_task}': '{task}'.\033[0m"
        )
        return task, context

    def propose_next_ai_task(self, *, messages, events, max_retries=5):
        if max_retries == 0:
            raise RuntimeError("Max retries reached, failed to propose ai task.")
        curriculum = self.llm(messages).content
        print(f"\033[31m****Curriculum Agent ai message****\n{curriculum}\033[0m")
        try:
            response = self.parse_ai_message(curriculum)
            assert "next_task" in response
            task = response["next_task"]
            normalized_task = self.normalize_task(task)
            completed_tasks = self._completed_task_names()
            live_inventory, _, _ = self._extract_live_inventory_state(events)
            if not self._is_repeatable_state_task(normalized_task) and self._task_inventory_satisfied(normalized_task, live_inventory):
                if max_retries <= 1:
                    blocked_tasks = set(completed_tasks)
                    blocked_tasks.add(normalized_task)
                    fallback_task, fallback_context = self._fallback_after_local_search_failure(
                        events,
                        blocked_tasks=blocked_tasks,
                    )
                    print(
                        f"\033[35mReplacing inventory-satisfied task '{task}' with fallback '{fallback_task}'.\033[0m"
                    )
                    return fallback_task, fallback_context
                retry_messages = list(messages) + [
                    HumanMessage(
                        content=(
                            f"Do not repeat '{task}'. It is already satisfied by the current live inventory. "
                            "Choose a different single next task that advances survival, progression, or a new nearby novelty."
                        )
                    )
                ]
                return self.propose_next_ai_task(
                    messages=retry_messages,
                    events=events,
                    max_retries=max_retries - 1,
                )
            repeated_local_failure = self._recent_local_search_failure(task)
            if repeated_local_failure:
                if max_retries <= 1:
                    blocked_tasks = set(completed_tasks)
                    blocked_tasks.add(task)
                    blocked_tasks.add(repeated_local_failure.get("task"))
                    fallback_task, fallback_context = self._fallback_after_local_search_failure(
                        events,
                        repeated_local_failure,
                        blocked_tasks=blocked_tasks,
                    )
                    print(
                        f"\033[35mReplacing repeated search-failed task '{task}' with fallback '{fallback_task}'.\033[0m"
                    )
                    return fallback_task, fallback_context
                retry_messages = list(messages) + [
                    HumanMessage(
                        content=(
                            f"Do not immediately repeat '{task}'. The earlier task '{repeated_local_failure.get('task')}' already failed with {repeated_local_failure.get('reason')}. "
                            "Choose a different single next task that either changes travel context/biome, restores a safer search domain, gathers prerequisites for travel/survival, or targets a different nearby novelty."
                        )
                    )
                ]
                return self.propose_next_ai_task(
                    messages=retry_messages,
                    events=events,
                    max_retries=max_retries - 1,
                )
            repeated_blocking_failure = self._recent_blocking_failure(task, events)
            if repeated_blocking_failure:
                blocked_tasks = set(completed_tasks)
                blocked_tasks.add(task)
                blocked_tasks.add(repeated_blocking_failure.get("task"))
                fallback_task, fallback_context = self._fallback_after_local_search_failure(
                    events,
                    repeated_blocking_failure,
                    blocked_tasks=blocked_tasks,
                )
                print(
                    f"\033[35mReplacing repeatedly blocked task '{task}' with fallback '{fallback_task}'.\033[0m"
                )
                return fallback_task, fallback_context
            context = self.get_task_context(task) + self.context_policy.search_policy_context(
                task,
                local_search_exhausted_reason=LOCAL_SEARCH_EXHAUSTED_REASON,
            )
            task, context = self._guard_task_with_live_inventory(task, context, events)
            guarded_blocking_failure = self._recent_blocking_failure(task, events)
            if guarded_blocking_failure:
                blocked_tasks = set(completed_tasks)
                blocked_tasks.add(task)
                blocked_tasks.add(guarded_blocking_failure.get("task"))
                fallback_task, fallback_context = self._fallback_after_local_search_failure(
                    events,
                    guarded_blocking_failure,
                    blocked_tasks=blocked_tasks,
                )
                print(
                    f"\033[35mReplacing guardrail-selected blocked task '{task}' with fallback '{fallback_task}'.\033[0m"
                )
                return fallback_task, fallback_context
            return task, context
        except Exception as e:
            print(
                f"\033[35mError parsing curriculum response: {e}. Trying again!\033[0m"
            )
            return self.propose_next_ai_task(
                messages=messages,
                events=events,
                max_retries=max_retries - 1,
            )

    def normalize_task(self, task):
        normalized = str(task or "").strip()
        match = re.fullmatch(r"Mine\s+(\d+)\s+([a-z0-9_ ]+)", normalized)
        if match:
            amount = match.group(1)
            target = match.group(2).strip().lower().replace(" ", "_")
            item_name = ORE_TASK_ITEM_MAP.get(target)
            if item_name:
                return f"Obtain {amount} {item_name}"
        return normalized

    def _extract_live_inventory_state(self, events):
        event = observe_payload(events)
        inventory = payload_inventory(event)
        status = payload_status(event)
        inventory_used = safe_int(status.get("inventoryUsed") or 0)
        last_death_event = event.get("lastDeathEvent") if isinstance(event.get("lastDeathEvent"), dict) else None
        return inventory, inventory_used, last_death_event

    def _craft_task_feasible(self, task, inventory):
        match = CRAFT_TASK_PATTERN.match(str(task or "").strip())
        if not match:
            return True
        quantity = int(match.group(1))
        item_name = match.group(2).strip().lower().replace(" ", "_")
        return InventoryFirstPlanner().can_craft(item_name, quantity, inventory)

    def _is_bootstrap_or_survival_task(self, task):
        return self.progression_policy.is_bootstrap_or_survival_task(task)

    def _infer_early_game_stage(self, events):
        return self.progression_policy.infer_stage(events)

    def _survival_override_task(self, events):
        return self.progression_policy.survival_override(events)

    def _early_game_guard_task(self, task, context, events):
        decision = self.progression_policy.guard_task(task, context, events)
        if not isinstance(decision, dict):
            return task, context
        if bool(decision.get("changed")):
            if decision.get("kind") == "survival_override":
                print(f"\033[35mEarly-game survival override replaced '{task}' with '{decision.get('task')}'.\033[0m")
            else:
                print(
                    f"\033[35mEarly-game guardrail replaced '{task}' with '{decision.get('task')}' at stage {decision.get('stage')}.\033[0m"
                )
        return decision.get("task", task), decision.get("context", context)

    def _guard_task_with_live_inventory(self, task, context, events):
        inventory, inventory_used, last_death_event = self._extract_live_inventory_state(events)
        status = payload_status(observe_payload(events))
        sparse_inventory = inventory_used <= 2 or sum(int(v or 0) for v in inventory.values()) <= 4
        if self.recovery_policy.should_force_post_death_recovery(last_death_event, inventory, status):
            recovery_task, recovery_context = self.recovery_policy.post_death_recovery_task(inventory, status, last_death_event)
            print(
                f"\033[35mPost-death recovery replaced '{task}' with '{recovery_task}'.\033[0m"
            )
            return recovery_task, recovery_context
        if sparse_inventory and not self._is_bootstrap_or_survival_task(task):
            fallback_task, fallback_context = self.recovery_policy.fallback_recovery_task(inventory)
            print(
                f"\033[35mSparse-inventory recovery replaced '{task}' with '{fallback_task}' based on live inventory {inventory}.\033[0m"
            )
            task, context = fallback_task, fallback_context
        if not self._craft_task_feasible(task, inventory):
            craft_match = CRAFT_TASK_PATTERN.match(str(task or "").strip())
            prerequisite = None
            prerequisite_chain = []
            if craft_match:
                planner = InventoryFirstPlanner()
                craft_item = craft_match.group(2)
                craft_quantity = int(craft_match.group(1))
                prerequisite_chain = [
                    step.as_dict()
                    for step in planner.prerequisite_chain_for_craft(
                        craft_item,
                        craft_quantity,
                        inventory,
                    )
                ]
                prerequisite = planner.prerequisite_for_craft(craft_item, craft_quantity, inventory)
            if prerequisite:
                self._set_last_inventory_plan(
                    phase="prerequisite",
                    source="recipe_guard",
                    selected_task=prerequisite.task,
                    selected_context=prerequisite.context,
                    reason=prerequisite.reason,
                    chain=prerequisite_chain,
                    blocked_task=task,
                    blocked_context=context,
                )
                print(
                    f"\033[35mRecipe planner replaced infeasible craft task '{task}' with prerequisite '{prerequisite.task}'.\033[0m"
                )
                task, context = prerequisite.task, prerequisite.context
            elif sparse_inventory or last_death_event:
                fallback_task, fallback_context = self.recovery_policy.fallback_recovery_task(inventory)
                print(
                    f"\033[35mOverriding infeasible craft task '{task}' with '{fallback_task}' based on live inventory {inventory}.\033[0m"
                )
                task, context = fallback_task, fallback_context
        return self._early_game_guard_task(task, context, events)

    def parse_ai_message(self, message):
        task = ""
        for line in message.split("\n"):
            if line.startswith("Task:"):
                task = line[5:].replace(".", "").strip()
        assert task, "Task not found in Curriculum Agent response"
        return {"next_task": self.normalize_task(task)}

    def propose_next_manual_task(self):
        confirmed = False
        task, context = "", ""
        while not confirmed:
            task = input("Enter task: ")
            context = input("Enter context: ")
            print(f"Task: {task}\nContext: {context}")
            confirmed = input("Confirm? (y/n)").lower() in ["y", ""]
        return task, context

    def update_exploration_progress(self, info):
        task = info["task"]
        self._apply_task_result_to_active_plan(info)
        if task.startswith("Deposit useless items into the chest at"):
            # No need to record the deposit task
            return
        if info["success"]:
            print(f"\033[35mCompleted task {task}.\033[0m")
            self.last_completed_task = task
            self.completed_tasks.append(task)
        else:
            pending = self.speculative_next_task
            if isinstance(pending, dict) and self.normalize_task(pending.get("trigger_task")) == self.normalize_task(task):
                self.last_speculative_decision = {
                    "phase": "discarded",
                    "trigger_task": self.normalize_task(task),
                    "next_task": pending.get("next_task"),
                    "reason": pending.get("reason"),
                    "discard_reason": "trigger_task_failed",
                    "consumed_at": time.time(),
                }
                self.speculative_next_task = None
            print(
                f"\033[35mFailed to complete task {task}. Skipping to next task.\033[0m"
            )
            self._record_failed_task(info)

        # clean up tasks and dump to disk
        self.clean_up_tasks()

    def clean_up_tasks(self):
        updated_completed_tasks = []
        updated_failed_tasks = list(self.failed_tasks)
        # dedup but keep order
        for task in self.completed_tasks:
            if task not in updated_completed_tasks:
                updated_completed_tasks.append(task)

        # remove completed tasks from failed tasks
        completed_task_names = set(updated_completed_tasks)
        updated_failed_tasks = [
            record
            for record in updated_failed_tasks
            if record.get("task") not in completed_task_names
        ]

        self.completed_tasks = updated_completed_tasks
        self.failed_tasks = updated_failed_tasks

        # dump to json
        U.dump_json(
            self.completed_tasks, f"{self.ckpt_dir}/curriculum/completed_tasks.json"
        )
        U.dump_json(self.failed_tasks, f"{self.ckpt_dir}/curriculum/failed_tasks.json")

    def decompose_task(self, task, events):
        messages = [
            SystemMessage(
                content=load_prompt("curriculum_task_decomposition"),
            ),
            self.render_human_message(events=events, chest_observation=""),
            HumanMessage(content=f"Final task: {task}"),
        ]
        print(
            f"\033[31m****Curriculum Agent task decomposition****\nFinal task: {task}\033[0m"
        )
        response = self.llm(messages).content
        print(f"\033[31m****Curriculum Agent task decomposition****\n{response}\033[0m")
        return fix_and_parse_json(response)

    def run_qa(self, *, events, chest_observation):
        questions_new, _ = self.run_qa_step1_ask_questions(
            events=events, chest_observation=chest_observation
        )
        questions = []
        answers = []
        seen_questions = set()
        for raw_question in questions_new:
            question = self.context_policy.normalize_qa_question(raw_question)
            if not question or question in seen_questions:
                continue
            seen_questions.add(question)
            cached = self.qa_cache_helper.get_exact_answer(question)
            if cached is not None:
                question_cached, answer_cached = cached
                questions.append(question_cached)
                answers.append(answer_cached)
                continue
            cached = self.qa_cache_helper.get_similar_answer(question, max_score=0.05)
            if cached is not None:
                question_cached, answer_cached = cached
                questions.append(question_cached)
                answers.append(answer_cached)
                continue
            answer = self.run_qa_step2_answer_questions(question=question)
            cached = self.qa_cache_helper.get_exact_answer(question)
            if cached is not None:
                question_cached, answer_cached = cached
                questions.append(question_cached)
                answers.append(answer_cached)
                continue
            self.qa_cache_helper.store_answer(question, answer)
            questions.append(question)
            answers.append(answer)
        assert len(questions) == len(answers)
        return questions, answers

    def get_task_context(self, task):
        # if include ore in question, gpt will try to use tool with skill touch enhancement to mine
        question = self.context_policy.task_question(task)
        cached = self.qa_cache_helper.get_exact_answer(question)
        if cached is not None:
            _, answer = cached
        else:
            answer = self.run_qa_step2_answer_questions(question=question)
            self.qa_cache_helper.store_answer(question, answer)
        context = f"Question: {question}\n{answer}"
        return context

    def render_system_message_qa_step1_ask_questions(self):
        return SystemMessage(content=load_prompt("curriculum_qa_step1_ask_questions"))

    def render_human_message_qa_step1_ask_questions(self, *, events, chest_observation):
        observation = self.render_observation(
            events=events, chest_observation=chest_observation
        )
        content = ""
        for key in self.curriculum_observations:
            content += observation[key]
        return HumanMessage(content=content)

    def run_qa_step1_ask_questions(self, *, events, chest_observation):
        biome = self.context_policy.biome_label(events[-1][1]["status"].get("biome"))
        questions = self.context_policy.seed_questions_for_biome(biome)
        concepts = [biome, biome, biome]
        messages = [
            self.render_system_message_qa_step1_ask_questions(),
            self.render_human_message_qa_step1_ask_questions(
                events=events, chest_observation=chest_observation
            ),
        ]
        qa_response = self.qa_llm(messages).content
        try:
            # Regex pattern to extract question and concept pairs
            pattern = r"Question \d+: (.+)\nConcept \d+: (.+)"
            # Extracting all question and concept pairs from the text
            pairs = re.findall(pattern, qa_response)
            # Storing each question and concept in separate lists
            questions_new = []
            concepts_new = []
            for pair in pairs:
                question = self.context_policy.normalize_qa_question(pair[0])
                concept = str(pair[1] or "").strip() or biome
                if not question:
                    continue
                questions_new.append(question)
                concepts_new.append(concept)
            assert len(questions_new) == len(concepts_new)
            questions.extend(questions_new)
            concepts.extend(concepts_new)
        except Exception as e:
            print(
                f"\033[35mError parsing curriculum response for "
                f"QA step 1 ask questions: {e}.\033[0m"
            )
        return questions, concepts

    def render_system_message_qa_step2_answer_questions(self):
        return SystemMessage(
            content=load_prompt("curriculum_qa_step2_answer_questions")
        )

    def render_human_message_qa_step2_answer_questions(self, question):
        content = f"Question: {question}"
        return HumanMessage(content=content)

    def run_qa_step2_answer_questions(self, question):
        messages = [
            self.render_system_message_qa_step2_answer_questions(),
            self.render_human_message_qa_step2_answer_questions(question=question),
        ]
        print(f"\033[35mCurriculum Agent Question: {question}\033[0m")
        qa_answer = self.qa_llm(messages).content
        safe_answer = str(qa_answer).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        print(f"\033[31mCurriculum Agent {safe_answer}\033[0m")
        return qa_answer
