import re

from voyager.prompts import load_prompt
from voyager.utils.json_utils import fix_and_parse_json
from voyager.utils.console import safe_print as print
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

ORE_TASK_ITEM_MAP = {
    "coal_ore": "coal",
    "iron_ore": "raw_iron",
    "copper_ore": "raw_copper",
}
HOSTILE_ENTITY_NAMES = ("zombie", "skeleton", "creeper", "spider", "drowned", "witch", "enderman")
SURVIVAL_TASK_HINTS = ("shelter", "retreat", "safe", "food", "eat", "cook")


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


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _hostiles_nearby(entities):
    return any(
        any(hostile in str(name).lower() for hostile in HOSTILE_ENTITY_NAMES)
        for name in (entities or {}).keys()
    )


def _is_night(status):
    return str((status or {}).get("timeOfDay") or "").strip().lower() in {"night", "midnight", "sunset", "sunrise"}


def _has_shelter_material_nearby(payload):
    voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
    return any(
        any(token in str(block).lower() for token in ["planks", "log", "cobblestone", "dirt"])
        for block in voxels
    )


class CriticAgent:
    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        request_timout=120,
        mode="auto",
        llm_url=None,
    ):
        llm_kwargs = {
            "model_name": model_name,
            "temperature": temperature,
            "request_timeout": request_timout,
        }
        if llm_url:
            llm_kwargs["openai_api_base"] = llm_url.removesuffix("/chat/completions")
        self.llm = ChatOpenAI(**llm_kwargs)
        assert mode in ["auto", "manual"]
        self.mode = mode

    def render_system_message(self):
        system_message = SystemMessage(content=load_prompt("critic"))
        return system_message

    def render_human_message(self, *, events, task, context, chest_observation):
        assert events[-1][0] == "observe", "Last event must be observe"
        payload = _observe_payload(events)
        status = _payload_status(payload)
        biome = status.get("biome")
        time_of_day = status.get("timeOfDay")
        voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
        health = status.get("health")
        hunger = status.get("food")
        position = status.get("position") if isinstance(status.get("position"), dict) else None
        equipment = status.get("equipment")
        inventory_used = _safe_int(status.get("inventoryUsed") or 0)
        inventory = _payload_inventory(payload)

        for i, (event_type, event) in enumerate(events):
            if event_type == "onError":
                print(f"\033[31mCritic Agent: Error occurs {event['onError']}\033[0m")
                return None

        observation = ""

        observation += f"Biome: {biome}\n\n"

        observation += f"Time: {time_of_day}\n\n"

        if voxels:
            observation += f"Nearby blocks: {', '.join(voxels)}\n\n"
        else:
            observation += f"Nearby blocks: None\n\n"

        observation += f"Health: {health:.1f}/20\n\n" if health is not None else "Health: Unknown\n\n"
        observation += f"Hunger: {hunger:.1f}/20\n\n" if hunger is not None else "Hunger: Unknown\n\n"

        if position and all(position.get(axis) is not None for axis in ("x", "y", "z")):
            observation += f"Position: x={position['x']:.1f}, y={position['y']:.1f}, z={position['z']:.1f}\n\n"
        else:
            observation += "Position: Unknown\n\n"

        observation += f"Equipment: {equipment}\n\n"

        if inventory:
            observation += f"Inventory ({inventory_used or 0}/36): {inventory}\n\n"
        else:
            observation += f"Inventory ({inventory_used or 0}/36): Empty\n\n"

        observation += chest_observation

        observation += f"Task: {task}\n\n"

        if context:
            observation += f"Context: {context}\n\n"
        else:
            observation += f"Context: None\n\n"

        print(f"\033[31m****Critic Agent human message****\n{observation}\033[0m")
        return HumanMessage(content=observation)

    def human_check_task_success(self):
        confirmed = False
        success = False
        critique = ""
        while not confirmed:
            success = input("Success? (y/n)")
            success = success.lower() == "y"
            critique = input("Enter your critique:")
            print(f"Success: {success}\nCritique: {critique}")
            confirmed = input("Confirm? (y/n)") in ["y", ""]
        return success, critique

    def _count_inventory_item(self, inventory, item_name):
        if not isinstance(inventory, dict):
            return 0
        value = inventory.get(item_name, 0)
        try:
            return int(value)
        except Exception:
            return 0

    def _last_observation(self, events=None):
        if not events:
            return {}
        payload = events[-1][1]
        return payload if isinstance(payload, dict) else {}

    def _evaluate_chest_open_result(self, task_text, events=None):
        lowered_task = str(task_text or "").strip().lower()
        if "open" not in lowered_task or "chest" not in lowered_task:
            return None

        last_observation = self._last_observation(events)
        interaction = last_observation.get("voyagerContainerInteraction")
        if not isinstance(interaction, dict):
            interaction = {}

        kind = str(interaction.get("kind", "")).lower()
        if kind and kind != "chest":
            return False, f"Expected chest interaction but observed {kind}."

        if bool(interaction.get("blockedAbove")):
            blocked_by = interaction.get("blockedBy") or "a solid block"
            return False, f"Chest is blocked above by {blocked_by}."

        if bool(interaction.get("interacted")):
            return True, "Chest helper completed a chest interaction in this step."

        if bool(interaction.get("opened")) and not interaction.get("error"):
            return True, "Chest interaction window opened successfully."

        if interaction.get("error"):
            return False, f"Chest interaction failed: {interaction['error']}"

        window_result = last_observation.get("voyagerWindowResult")
        if isinstance(window_result, dict):
            label = str(window_result.get("label", "")).lower()
            status = str(window_result.get("status", "")).lower()
            if "chest" in label and status in {"opened", "closed", "success"}:
                return True, "Chest interaction window opened successfully."

        return None

    def _inventory_success_override(self, task, inventory, events=None):
        task_text = str(task or "").strip()
        match = re.fullmatch(r"(Obtain|Have|Craft|Smelt|Mine)\s+(\d+)\s+([a-z0-9_]+)", task_text)
        if match:
            verb, amount_text, target = match.groups()
            amount = int(amount_text)
            inventory_target = target
            if verb == "Mine" and target in ORE_TASK_ITEM_MAP:
                inventory_target = ORE_TASK_ITEM_MAP[target]
            current = self._count_inventory_item(inventory, inventory_target)
            if current >= amount:
                return True, f"Inventory already satisfies the task with {current} {inventory_target}."
        shelter_override = self._shelter_success_override(task_text, inventory, events=events)
        if shelter_override is not None:
            return shelter_override
        return self._evaluate_chest_open_result(task_text, events=events)

    def preflight_task_success(self, task, events=None):
        inventory = _payload_inventory(_observe_payload(events))
        return self._inventory_success_override(task, inventory, events=events)

    def _shelter_success_override(self, task_text, inventory, events=None):
        lowered_task = str(task_text or "").strip().lower()
        if "shelter" not in lowered_task:
            return None
        payload = _observe_payload(events)
        status = _payload_status(payload)
        entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
        health = status.get("health")
        if health is None or float(health) < 10:
            return None
        if _hostiles_nearby(entities):
            return None
        if _has_shelter_material_nearby(payload):
            return True, "Nearby placed shelter materials are present and no immediate hostiles remain; count the temporary shelter as established."
        return None

    def _safety_failure_override(self, task, events=None):
        payload = _observe_payload(events)
        status = _payload_status(payload)
        task_text = str(task or "").strip().lower()
        if any(token in task_text for token in SURVIVAL_TASK_HINTS):
            return None
        health = status.get("health")
        hunger = status.get("food")
        entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
        hostile_nearby = _hostiles_nearby(entities)
        if health is not None and float(health) <= 4:
            return False, "Safety override: ending health is critically low, so this step should not count as stable progress. Recover first."
        if hunger is not None and float(hunger) <= 3:
            return False, "Safety override: ending hunger is critically low, so progression should pause for recovery."
        if hostile_nearby and _is_night(status) and health is not None and float(health) <= 8:
            return False, "Safety override: hostiles remain nearby at night while health is low; prioritize shelter or retreat before counting progress."
        return None

    def ai_check_task_success(self, messages, max_retries=5):
        if max_retries == 0:
            print(
                "\033[31mFailed to parse Critic Agent response. Consider updating your prompt.\033[0m"
            )
            return False, ""

        if messages[1] is None:
            return False, ""

        critic = self.llm(messages).content
        print(f"\033[31m****Critic Agent ai message****\n{critic}\033[0m")
        try:
            response = fix_and_parse_json(critic)
            assert response["success"] in [True, False]
            if "critique" not in response:
                response["critique"] = ""
            return response["success"], response["critique"]
        except Exception as e:
            print(f"\033[31mError parsing critic response: {e} Trying again!\033[0m")
            return self.ai_check_task_success(
                messages=messages,
                max_retries=max_retries - 1,
            )

    def check_task_success(
        self, *, events, task, context, chest_observation, max_retries=5
    ):
        inventory = _payload_inventory(_observe_payload(events))
        override = self._inventory_success_override(task, inventory, events=events)
        if override is not None:
            return override
        safety_override = self._safety_failure_override(task, events=events)
        if safety_override is not None:
            return safety_override

        human_message = self.render_human_message(
            events=events,
            task=task,
            context=context,
            chest_observation=chest_observation,
        )

        messages = [
            self.render_system_message(),
            human_message,
        ]

        if self.mode == "manual":
            return self.human_check_task_success()
        elif self.mode == "auto":
            return self.ai_check_task_success(
                messages=messages, max_retries=max_retries
            )
        else:
            raise ValueError(f"Invalid critic agent mode: {self.mode}")
