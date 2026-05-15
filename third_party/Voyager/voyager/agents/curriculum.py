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
HOSTILE_ENTITY_NAMES = ("zombie", "skeleton", "creeper", "spider", "drowned", "witch", "enderman")
FOOD_HINT_TOKENS = ("beef", "pork", "mutton", "chicken", "fish", "salmon", "cod", "bread", "carrot", "potato", "melon", "apple")
SURVIVAL_TASK_HINTS = ("shelter", "retreat", "safe", "food", "eat", "cook", "coal", "torch", "iron", "wood", "log", "planks", "stick", "crafting table", "crafting_table", "wooden pickaxe", "stone pickaxe", "stone axe", "cobblestone")


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


def _has_named_planks(inventory, prefix, needed):
    candidates = [f"{prefix}_planks"]
    if prefix == "wood":
        return _count_planks(inventory) >= needed
    return sum(_inv_count(inventory, name) for name in candidates) >= needed


def _can_craft_tool(inventory, material, sticks_needed, units_needed):
    if _inv_count(inventory, "stick") < sticks_needed:
        return False
    if material == "wooden":
        return _count_planks(inventory) >= units_needed
    if material == "stone":
        return _count_generic_stone(inventory) >= units_needed
    if material == "iron":
        return _inv_count(inventory, "iron_ingot") >= units_needed
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


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


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


def _observe_payload(events):
    if not events:
        return {}
    try:
        event_type, payload = events[-1]
    except Exception:
        return {}
    if event_type != "observe" or not isinstance(payload, dict):
        return {}
    return payload


def _payload_status(payload):
    status = payload.get("status") if isinstance(payload, dict) else None
    return status if isinstance(status, dict) else {}


def _payload_inventory(payload):
    inventory = payload.get("inventory") if isinstance(payload, dict) else None
    return inventory if isinstance(inventory, dict) else {}


def _payload_list(payload, key):
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, list) else []


def _payload_dict(payload, key):
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else {}


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


def _is_night(status):
    raw = str((status or {}).get("timeOfDay") or "").strip().lower()
    return raw in {"night", "midnight", "sunset", "sunrise"}


def _inventory_has_food(inventory):
    return any(
        _inv_count(inventory, name) > 0
        for name in inventory.keys()
        if any(token in str(name) for token in FOOD_HINT_TOKENS)
    )


def _hostiles_nearby(entities):
    return any(
        any(hostile in str(name).lower() for hostile in HOSTILE_ENTITY_NAMES)
        for name in (entities or {}).keys()
    )


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
        if not isinstance(info, dict):
            return "Unknown", ""
        for key in ("error", "critique", "failure_reason", "reset_error"):
            value = info.get(key)
            if value:
                text = str(value).strip()
                if text:
                    reason = text.splitlines()[0][:160]
                    return self._canonicalize_failure_reason(reason, text[:300], info)
        return self._canonicalize_failure_reason("Unknown", "", info)

    def _canonicalize_failure_reason(self, reason, evidence, info=None):
        reason_text = str(reason or "Unknown").strip() or "Unknown"
        evidence_text = str(evidence or "").strip()
        combined = f"{reason_text}\n{evidence_text}".lower()
        completion_reason = ""
        if isinstance(info, dict):
            completion_reason = str(info.get("completion_reason") or "").strip()
        if reason_text in SEARCH_FAILURE_REASONS:
            return reason_text, evidence_text or reason_text
        if completion_reason in SEARCH_FAILURE_REASONS:
            return completion_reason, evidence_text or reason_text
        if any(snippet in combined for snippet in LOCAL_SEARCH_FAILURE_SNIPPETS):
            return LOCAL_SEARCH_EXHAUSTED_REASON, evidence_text or reason_text
        if reason_text == "Unknown" and completion_reason:
            return completion_reason, evidence_text
        if self._looks_like_prompt_or_conversation_dump(reason_text, evidence_text):
            fallback_reason = completion_reason or "action_or_critic_output_not_normalized"
            return fallback_reason, evidence_text[:300]
        return reason_text, evidence_text

    def _looks_like_prompt_or_conversation_dump(self, reason_text, evidence_text):
        combined = f"{reason_text}\n{evidence_text}".strip().lower()
        if not combined:
            return False
        suspicious_snippets = (
            "you are a helpful assistant that writes mineflayer javascript code",
            "here are some useful programs written with mineflayer apis",
            "completed tasks so far:",
            "failed tasks that are too hard:",
        )
        if any(snippet in combined for snippet in suspicious_snippets):
            return True
        if combined.startswith("(") and "mineflayer javascript code" in combined:
            return True
        if len(reason_text) >= 140 and any(token in reason_text.lower() for token in ("you are ", "inventory", "nearby blocks", "task:")):
            return True
        return False

    def _recent_local_search_failure(self, task):
        target_keywords = _task_keywords(task)
        if not target_keywords:
            return None
        best_record = None
        best_overlap = 0
        for record in self.failed_tasks:
            if record.get("reason") not in SEARCH_FAILURE_REASONS:
                continue
            overlap = len(target_keywords.intersection(_task_keywords(record.get("task"))))
            if overlap > best_overlap:
                best_overlap = overlap
                best_record = record
        return best_record if best_overlap > 0 else None

    def _search_policy_context(self, task):
        verb = str(task or "").strip().split(" ", 1)[0].lower()
        if verb not in {"obtain", "mine", "kill", "cook", "eat"}:
            return ""
        return (
            "\nOperational policy: prefer intent-level search helpers instead of ad-hoc wandering. "
            "For wood or food in the wrong domain, recover to the surface first. "
            "For nearby-search tasks, first check within 32 blocks, then use short bounded search only if needed. "
            f"If search stalls or exhausts candidates, stop with a concise reason such as {LOCAL_SEARCH_EXHAUSTED_REASON}, wood_scout_exhausted, food_scout_exhausted, or surface_recovery_exhausted so the next curriculum step can change direction, biome, or prerequisites."
        )

    def _fallback_after_local_search_failure(self, events, failed_record=None):
        event = _observe_payload(events)
        voxels = _payload_list(event, "voxels")
        inventory, _, _ = self._extract_live_inventory_state(events)
        failed_reason = str((failed_record or {}).get("reason") or "")
        if failed_reason.startswith("surface_recovery_"):
            return (
                "Retreat to a safe position",
                "Recent recovery search could not find a clean surface route. Re-stabilize locally, avoid hazards, and let the next task choose a different recovery direction or safer travel context.",
            )
        if failed_reason.startswith("food_scout_") and any(item in inventory for item in FOOD_HINT_TOKENS):
            return (
                "Cook food",
                "Recent food scouting was inefficient. Convert already-owned food resources into immediate survival value before attempting a broader search again.",
            )
        if failed_reason.startswith("ore_scout_"):
            return (
                "Retreat to a safe position",
                "Recent ore scouting failed to find a productive underground route. Re-stabilize, avoid burning more cave time immediately, and let the next task choose a different ore direction, elevation, or prerequisite.",
            )
        if _count_logs(inventory) and _count_planks(inventory) < 8:
            return (
                "Craft 8 wood planks",
                "The previous search was inefficient. Use this turn to strengthen travel and crafting prerequisites instead of repeating a long local search.",
            )
        if any("log" in block for block in voxels):
            return (
                "Obtain 8 wood logs",
                "The previous target was not nearby or the scout path stalled. Gather reusable travel resources from the current area, then let the next task choose a new direction or biome.",
            )
        return self._fallback_recovery_task(inventory)

    def _record_failed_task(self, info):
        task = str(info.get("task") or "").strip()
        if not task:
            return
        reason, evidence = self._extract_failure_reason(info)
        for record in self.failed_tasks:
            if record.get("task") == task:
                record["repeat_count"] = int(record.get("repeat_count") or 1) + 1
                record["reason"] = reason or record.get("reason") or "Unknown"
                if evidence:
                    record["evidence"] = evidence
                record["last_seen_at"] = int(time.time())
                record["last_error_type"] = info.get("error_type")
                record["last_program_name"] = info.get("program_name")
                return
        self.failed_tasks.append(
            {
                "task": task,
                "reason": reason,
                "evidence": evidence,
                "repeat_count": 1,
                "last_seen_at": int(time.time()),
                "last_error_type": info.get("error_type"),
                "last_program_name": info.get("program_name"),
            }
        )

    def render_observation(self, *, events, chest_observation):
        assert events[-1][0] == "observe", "Last event must be observe"
        event = _observe_payload(events)
        status = _payload_status(event)
        biome = status.get("biome")
        time_of_day = status.get("timeOfDay")
        voxels = _payload_list(event, "voxels")
        block_records = _payload_list(event, "blockRecords")
        entities = _payload_dict(status, "entities")
        health = status.get("health")
        hunger = status.get("food")
        position = status.get("position") if isinstance(status.get("position"), dict) else None
        equipment = status.get("equipment")
        inventory_used = _safe_int(status.get("inventoryUsed") or 0)
        inventory = _payload_inventory(event)

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

    def propose_next_task(self, *, events, chest_observation, max_retries=5):
        if self.progress == 0 and self.mode == "auto":
            task = "Mine 1 wood log"
            context = "You can mine one of oak, birch, spruce, jungle, acacia, dark oak, or mangrove logs."
            return task, context

        # hard code task when inventory is almost full
        latest_event = _observe_payload(events)
        latest_status = _payload_status(latest_event)
        latest_inventory = _payload_inventory(latest_event)
        inventory_used = _safe_int(latest_status.get("inventoryUsed") or 0)
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
                        return task, context
            if "chest" in latest_inventory:
                task = "Place a chest"
                context = (
                    f"You have a chest in inventory, place it around you. "
                    f"If chests is not None, or nearby blocks contains chest, this task is success."
                )
            else:
                task = "Craft 1 chest"
                context = "Craft 1 chest with 8 planks of any kind of wood."
            return task, context

        messages = [
            self.render_system_message(),
            self.render_human_message(
                events=events, chest_observation=chest_observation
            ),
        ]

        if self.mode == "auto":
            return self.propose_next_ai_task(messages=messages, events=events, max_retries=max_retries)
        elif self.mode == "manual":
            return self.propose_next_manual_task()
        else:
            raise ValueError(f"Invalid curriculum agent mode: {self.mode}")

    def propose_next_ai_task(self, *, messages, events, max_retries=5):
        if max_retries == 0:
            raise RuntimeError("Max retries reached, failed to propose ai task.")
        curriculum = self.llm(messages).content
        print(f"\033[31m****Curriculum Agent ai message****\n{curriculum}\033[0m")
        try:
            response = self.parse_ai_message(curriculum)
            assert "next_task" in response
            task = response["next_task"]
            completed_tasks = {
                self.normalize_task(completed_task)
                for completed_task in self.completed_tasks
                if str(completed_task or "").strip()
            }
            if task in completed_tasks:
                if max_retries <= 1:
                    fallback_task, fallback_context = self._fallback_after_local_search_failure(events)
                    print(
                        f"\033[35mReplacing already completed task '{task}' with fallback '{fallback_task}'.\033[0m"
                    )
                    return fallback_task, fallback_context
                retry_messages = list(messages) + [
                    HumanMessage(
                        content=(
                            f"Do not repeat '{task}'. It is already in completed_tasks. "
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
                    fallback_task, fallback_context = self._fallback_after_local_search_failure(events, repeated_local_failure)
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
            context = self.get_task_context(task) + self._search_policy_context(task)
            task, context = self._guard_task_with_live_inventory(task, context, events)
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
        match = re.fullmatch(r"Mine\s+(\d+)\s+([a-z0-9_]+)", normalized)
        if match:
            amount = match.group(1)
            target = match.group(2)
            item_name = ORE_TASK_ITEM_MAP.get(target)
            if item_name:
                return f"Obtain {amount} {item_name}"
        return normalized

    def _extract_live_inventory_state(self, events):
        event = _observe_payload(events)
        inventory = _payload_inventory(event)
        status = _payload_status(event)
        inventory_used = _safe_int(status.get("inventoryUsed") or 0)
        last_death_event = event.get("lastDeathEvent") if isinstance(event.get("lastDeathEvent"), dict) else None
        return inventory, inventory_used, last_death_event

    def _craft_task_feasible(self, task, inventory):
        match = CRAFT_TASK_PATTERN.match(str(task or "").strip())
        if not match:
            return True
        quantity = int(match.group(1))
        item_name = match.group(2).strip().lower().replace(" ", "_")
        return bool(_recipe_gate(item_name, quantity, inventory))

    def _fallback_recovery_task(self, inventory):
        if _count_logs(inventory) < 8:
            return (
                "Obtain 8 wood logs",
                "Your current inventory is too sparse for advanced crafting. Rebuild from the live inventory state: collect at least 8 wood logs first, then recover planks, sticks, and tools.",
            )
        if _count_planks(inventory) < 8:
            return (
                "Craft 8 wood planks",
                "Use your current logs to rebuild core crafting materials before attempting advanced items.",
            )
        if not _has_any(inventory, "crafting_table"):
            return (
                "Craft 1 crafting_table",
                "Rebuild your basic crafting setup from the live inventory state before attempting advanced items.",
            )
        if _inv_count(inventory, "stick") < 4:
            return (
                "Craft 4 sticks",
                "Rebuild a small stick reserve so tool recovery does not stall on the next step.",
            )
        if not _has_any(inventory, "wooden_pickaxe"):
            return (
                "Craft 1 wooden_pickaxe",
                "Finish the wooden pickaxe bootstrap before trying broader survival or progression tasks.",
            )
        if _count_generic_stone(inventory) < 6:
            return (
                "Mine 6 cobblestone",
                "Secure the first stone batch again so you can restore the basic stone tool loop.",
            )
        if not _has_any(inventory, "stone_pickaxe"):
            return (
                "Craft 1 stone_pickaxe",
                "Restore stone mining capability before attempting optional progression.",
            )
        if not _has_any(inventory, "stone_axe"):
            return (
                "Craft 1 stone_axe",
                "Restore the basic gathering toolset before attempting optional progression.",
            )
        if not _inventory_has_food(inventory):
            return (
                "Find food source",
                "Recovery mode: stabilize food before returning to progression so low-hunger deaths do not chain.",
            )
        return (
            "Craft 1 crafting_table",
            "Rebuild your basic crafting setup from the live inventory state before attempting advanced items.",
        )

    def _death_specific_recovery_task(self, last_death_event, inventory, status):
        if not isinstance(last_death_event, dict):
            return None
        combined = " ".join(
            str(last_death_event.get(key) or "")
            for key in ("cause", "death_message", "likely_reason", "likely_killer")
        ).lower()
        nearby_hostiles = last_death_event.get("nearby_hostiles") if isinstance(last_death_event.get("nearby_hostiles"), list) else []
        has_hostiles = bool(nearby_hostiles)
        if any(token in combined for token in ["drown", "drowned", "water", "bubble"]):
            return (
                "Retreat to a safe position",
                "Death-derived countermeasure: the last death was water-related. Use recoverToSurface-style behavior immediately, move onto solid ground, avoid deep water routes, surface as soon as submerged, and do not keep working underwater unless the task explicitly requires it.",
            )
        if any(token in combined for token in ["lava", "burn", "fire", "magma"]):
            return (
                "Retreat to a safe position",
                "Death-derived countermeasure: the last death was lava or fire related. Back away from exposed lava, avoid mining directly over voids or lava pockets, and secure footing before resuming resource collection.",
            )
        if any(token in combined for token in ["fell", "fall", "hit the ground", "cliff"]):
            return (
                "Retreat to a safe position",
                "Death-derived countermeasure: the last death was fall-related. Favor flat routes, descend one block at a time, and avoid sprinting near drops until health and terrain are stable.",
            )
        if any(token in combined for token in ["starv", "hunger"]):
            return (
                "Find food source",
                "Death-derived countermeasure: the last death was hunger related. Secure edible food before travel, mining, or combat, and keep a safety reserve instead of consuming the last item too late.",
            )
        if has_hostiles or any(token in combined for token in ["slain", "shot", "blown up", "creeper", "skeleton", "zombie", "spider", "drowned", "witch", "enderman"]):
            return (
                "Build a temporary shelter",
                "Death-derived countermeasure: the last death involved hostile pressure. Re-establish shelter, avoid open combat, and only re-engage after recovering health, food, and a safer position.",
            )
        return None

    def _post_death_recovery_task(self, inventory, status, last_death_event=None):
        specific = self._death_specific_recovery_task(last_death_event, inventory, status)
        if specific:
            return specific
        health = _status_number(status, "health", default=20)
        hunger = _status_number(status, "food", default=20)
        if health <= 12 or _is_night(status):
            return (
                "Build a temporary shelter",
                "Recent death recovery: immediately rebuild a safe position, prefer recoverToSurface-style movement before broader search, avoid combat, and only resume progression after stabilizing health and exposure.",
            )
        if hunger <= 8 and not _inventory_has_food(inventory):
            return (
                "Find food source",
                "Recent death recovery: secure nearby edible food before longer travel or mining, and use surface-oriented food search rather than underground wandering.",
            )
        return self._fallback_recovery_task(inventory)

    def _should_force_post_death_recovery(self, last_death_event, inventory, status):
        if not isinstance(last_death_event, dict):
            return False
        age_seconds = _event_age_seconds(last_death_event)
        if age_seconds is not None and age_seconds > 180:
            return False
        health = _status_number(status, "health", default=20)
        hunger = _status_number(status, "food", default=20)
        if health <= 16 or hunger <= 12:
            return True
        if not _inventory_has_food(inventory):
            return True
        if not _has_any(inventory, "wooden_pickaxe", "stone_pickaxe"):
            return True
        if _count_logs(inventory) + _count_planks(inventory) <= 4:
            return True
        return False

    def _is_bootstrap_or_survival_task(self, task):
        task_text = _task_text(task)
        return any(token in task_text for token in SURVIVAL_TASK_HINTS)

    def _infer_early_game_stage(self, events):
        payload = _observe_payload(events)
        status = _payload_status(payload)
        inventory = _payload_inventory(payload)
        logs = _count_logs(inventory)
        planks = _count_planks(inventory)
        sticks = _inv_count(inventory, "stick")
        stone = _count_generic_stone(inventory)
        has_table = _has_any(inventory, "crafting_table")
        has_wooden_pickaxe = _has_any(inventory, "wooden_pickaxe")
        has_stone_pickaxe = _has_any(inventory, "stone_pickaxe")
        has_stone_axe = _has_any(inventory, "stone_axe")
        has_food = _inventory_has_food(inventory)
        has_iron_progress = _has_any(inventory, "raw_iron", "iron_ingot", "iron_ore")
        if not has_wooden_pickaxe:
            if logs <= 0 and planks <= 0 and sticks <= 0:
                return 0
            return 1
        if not (has_stone_pickaxe and has_stone_axe):
            return 2
        if not has_food or not has_iron_progress:
            return 3
        return 4

    def _survival_override_task(self, events):
        payload = _observe_payload(events)
        status = _payload_status(payload)
        inventory = _payload_inventory(payload)
        entities = _payload_dict(status, "entities")
        health = _status_number(status, "health", default=20)
        hunger = _status_number(status, "food", default=20)
        hostile_nearby = _hostiles_nearby(entities)
        has_food = _inventory_has_food(inventory)
        recent_shelter_success = bool(self.completed_tasks and self.completed_tasks[-1] == "Build a temporary shelter")
        if recent_shelter_success and not hostile_nearby and health >= 16:
            if hunger <= 12 and not has_food:
                return (
                    "Find food source",
                    "Shelter exit override: safety is restored, so leave shelter mode and secure nearby food before resuming broader progression.",
                )
            return None
        if health <= 6 or (health <= 10 and hostile_nearby):
            return (
                "Build a temporary shelter",
                "Low health override: immediately get to safety, block exposure, and avoid combat before resuming progression.",
            )
        if hunger <= 8 and not has_food:
            return (
                "Find food source",
                "Low hunger override: prioritize obtaining nearby edible food before any ore processing or exploration. Keep the search local and safe.",
            )
        if _is_night(status) and (hostile_nearby or health <= 10):
            return (
                "Build a temporary shelter",
                "Night danger override: secure a safe shelter before other progression tasks and avoid long surface travel.",
            )
        if hostile_nearby and health <= 14:
            return (
                "Retreat to a safe position",
                "Hostile danger override: disengage and move to a safe position before continuing task progression.",
            )
        return None

    def _early_game_guard_task(self, task, context, events):
        stage = self._infer_early_game_stage(events)
        task_text = _task_text(task)
        payload = _observe_payload(events)
        inventory = _payload_inventory(payload)
        has_food = _inventory_has_food(inventory)
        has_iron_progress = _has_any(inventory, "raw_iron", "iron_ingot", "iron_ore")

        survival_override = self._survival_override_task(events)
        if survival_override:
            print(f"\033[35mEarly-game survival override replaced '{task}' with '{survival_override[0]}'.\033[0m")
            return survival_override

        def replace(next_task, next_context):
            print(
                f"\033[35mEarly-game guardrail replaced '{task}' with '{next_task}' at stage {stage}.\033[0m"
            )
            return next_task, next_context

        is_smelt = task_text.startswith("smelt ") or " smelt " in task_text
        mentions_copper = "copper" in task_text
        mentions_iron = "iron" in task_text
        mentions_logs = "log" in task_text or "wood" in task_text
        mentions_stone = "stone" in task_text or "cobblestone" in task_text or "deepslate" in task_text or "blackstone" in task_text
        mentions_food = "food" in task_text or "eat" in task_text or "cook" in task_text or "animal" in task_text or "beef" in task_text or "pork" in task_text or "chicken" in task_text or "mutton" in task_text

        if mentions_copper and stage < 4:
            if stage <= 1:
                return replace(
                    "Craft 1 wooden_pickaxe",
                    "Copper processing is blocked during early bootstrap. First secure a crafting table and wooden pickaxe.",
                )
            if stage == 2:
                return replace(
                    "Craft 1 stone_pickaxe",
                    "Copper processing is blocked until stone tools are ready. Upgrade tools first.",
                )
            return replace(
                "Find food source",
                "Copper processing is optional in early progression. Stabilize food and iron progression before processing copper.",
            )

        if is_smelt and stage < 4:
            if mentions_iron and stage == 3 and _has_any(inventory, "furnace") and (_has_any(inventory, "coal", "charcoal") or _has_any(inventory, "raw_iron", "iron_ore")):
                return task, context
            if stage <= 1:
                return replace(
                    "Obtain 8 wood logs",
                    "Smelting is blocked during early bootstrap. Gather wood and basic crafting materials first.",
                )
            if stage == 2:
                return replace(
                    "Craft 1 stone_axe",
                    "Smelting is blocked until stone tool progression is finished.",
                )
            return replace(
                "Find food source",
                "Smelting is blocked until survival and iron progression are stable.",
            )

        if "wooden axe" in task_text and stage < 3:
            return replace(
                "Craft 1 wooden_pickaxe",
                "Do not branch into a wooden axe during early bootstrap; prioritize pickaxe and stone unlock first.",
            )

        if stage == 0:
            if not mentions_logs:
                return replace(
                    "Mine 1 wood log",
                    "Early bootstrap stage: first obtain wood before considering other tasks.",
                )
        elif stage == 1:
            if not any(token in task_text for token in ["wooden pickaxe", "crafting table", "crafting_table", "planks", "sticks", "wood log", "wood logs"]):
                if _count_planks(inventory) < 4:
                    return replace(
                        "Craft 8 wood planks",
                        "Basic tool bootstrap stage: convert wood into planks before exploring broader tasks.",
                    )
                return replace(
                    "Craft 1 wooden_pickaxe",
                    "Basic tool bootstrap stage: finish wooden pickaxe before any side goals.",
                )
        elif stage == 2:
            if not (mentions_stone or "stone pickaxe" in task_text or "stone axe" in task_text):
                if _count_generic_stone(inventory) < 6:
                    return replace(
                        "Mine 6 cobblestone",
                        "Stone unlock stage: secure the first stone batch before optional tasks.",
                    )
                if not _has_any(inventory, "stone_pickaxe"):
                    return replace(
                        "Craft 1 stone_pickaxe",
                        "Stone unlock stage: craft a stone pickaxe before optional tasks.",
                    )
                return replace(
                    "Craft 1 stone_axe",
                    "Stone unlock stage: craft a stone axe before optional tasks.",
                )
        elif stage == 3:
            if not has_food and not mentions_food:
                return replace(
                    "Find food source",
                    "Stability stage: secure renewable or nearby edible food before optional progression so survival does not collapse.",
                )
            if has_food and not has_iron_progress and not (mentions_iron or is_smelt or "furnace" in task_text or "coal" in task_text or "torch" in task_text or "shelter" in task_text):
                return replace(
                    "Obtain 8 raw_iron",
                    "Stability stage: after food, move into first iron progression before unrelated novelty tasks.",
                )
            allowed = mentions_food or mentions_iron or is_smelt or "furnace" in task_text or "torch" in task_text or "coal" in task_text or "shelter" in task_text
            if not allowed:
                return replace(
                    "Find food source",
                    "Stability stage: prioritize food, fuel, shelter, and first iron progression before unrelated tasks.",
                )

        return task, context

    def _guard_task_with_live_inventory(self, task, context, events):
        inventory, inventory_used, last_death_event = self._extract_live_inventory_state(events)
        status = _payload_status(_observe_payload(events))
        sparse_inventory = inventory_used <= 2 or sum(int(v or 0) for v in inventory.values()) <= 4
        if self._should_force_post_death_recovery(last_death_event, inventory, status):
            recovery_task, recovery_context = self._post_death_recovery_task(inventory, status, last_death_event)
            print(
                f"\033[35mPost-death recovery replaced '{task}' with '{recovery_task}'.\033[0m"
            )
            return recovery_task, recovery_context
        if sparse_inventory and not self._is_bootstrap_or_survival_task(task):
            fallback_task, fallback_context = self._fallback_recovery_task(inventory)
            print(
                f"\033[35mSparse-inventory recovery replaced '{task}' with '{fallback_task}' based on live inventory {inventory}.\033[0m"
            )
            task, context = fallback_task, fallback_context
        if not self._craft_task_feasible(task, inventory):
            if sparse_inventory or last_death_event:
                fallback_task, fallback_context = self._fallback_recovery_task(inventory)
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
        if task.startswith("Deposit useless items into the chest at"):
            # No need to record the deposit task
            return
        if info["success"]:
            print(f"\033[35mCompleted task {task}.\033[0m")
            self.completed_tasks.append(task)
        else:
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
        for question in questions_new:
            if self.qa_cache_questions_vectordb._collection.count() > 0:
                docs_and_scores = (
                    self.qa_cache_questions_vectordb.similarity_search_with_score(
                        question, k=1
                    )
                )
                if docs_and_scores and docs_and_scores[0][1] < 0.05:
                    question_cached = docs_and_scores[0][0].page_content
                    assert question_cached in self.qa_cache
                    answer_cached = self.qa_cache[question_cached]
                    questions.append(question_cached)
                    answers.append(answer_cached)
                    continue
            answer = self.run_qa_step2_answer_questions(question=question)
            assert question not in self.qa_cache
            self.qa_cache[question] = answer
            self.qa_cache_questions_vectordb.add_texts(
                texts=[question],
            )
            U.dump_json(self.qa_cache, f"{self.ckpt_dir}/curriculum/qa_cache.json")
            self.qa_cache_questions_vectordb.persist()
            questions.append(question)
            answers.append(answer)
        assert len(questions_new) == len(questions) == len(answers)
        return questions, answers

    def get_task_context(self, task):
        # if include ore in question, gpt will try to use tool with skill touch enhancement to mine
        question = (
            f"How to {task.replace('_', ' ').replace(' ore', '').replace(' ores', '').replace('.', '').strip().lower()}"
            f" in Minecraft?"
        )
        if question in self.qa_cache:
            answer = self.qa_cache[question]
        else:
            answer = self.run_qa_step2_answer_questions(question=question)
            self.qa_cache[question] = answer
            self.qa_cache_questions_vectordb.add_texts(
                texts=[question],
            )
            U.dump_json(self.qa_cache, f"{self.ckpt_dir}/curriculum/qa_cache.json")
            self.qa_cache_questions_vectordb.persist()
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
        biome = events[-1][1]["status"]["biome"].replace("_", " ")
        questions = [
            f"What are the blocks that I can find in the {biome} in Minecraft?",
            f"What are the items that I can find in the {biome} in Minecraft?",
            f"What are the mobs that I can find in the {biome} in Minecraft?",
        ]
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
            questions_new = [pair[0] for pair in pairs]
            concepts_new = [pair[1] for pair in pairs]
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
