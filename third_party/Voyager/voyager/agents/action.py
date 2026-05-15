import os
import re
import time

import voyager.utils as U
from javascript import require
from langchain.chat_models import ChatOpenAI
from langchain.prompts import SystemMessagePromptTemplate
from langchain.schema import AIMessage, HumanMessage, SystemMessage

from voyager.agents.codex_gateway_llm import CodexGatewayLLM
from voyager.prompts import load_prompt
from voyager.control_primitives_context import load_control_primitives_context
from voyager.utils.console import safe_print as print


def _payload_status(payload):
    status = payload.get("status") if isinstance(payload, dict) else None
    return status if isinstance(status, dict) else {}


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


HOSTILE_ENTITY_NAMES = ("zombie", "skeleton", "creeper", "spider", "drowned", "witch", "enderman")
FOOD_HINT_TOKENS = ("beef", "pork", "mutton", "chicken", "fish", "salmon", "cod", "bread", "carrot", "potato", "melon", "apple")


def _hostiles_nearby(entities):
    return any(
        any(hostile in str(name).lower() for hostile in HOSTILE_ENTITY_NAMES)
        for name in (entities or {}).keys()
    )


def _inventory_has_food(inventory):
    return any(
        int(count or 0) > 0
        for name, count in (inventory or {}).items()
        if any(token in str(name) for token in FOOD_HINT_TOKENS)
    )


def _is_night(time_of_day):
    return str(time_of_day or "").strip().lower() in {"night", "midnight", "sunset", "sunrise"}


def _extract_fenced_code_blocks(text):
    source = str(text or "")
    labeled_pattern = re.compile(r"```(?:javascript|js)\s*(.*?)```", re.IGNORECASE | re.DOTALL)
    labeled_blocks = [block.strip() for block in labeled_pattern.findall(source) if str(block).strip()]
    if labeled_blocks:
        return "\n\n".join(labeled_blocks)

    generic_pattern = re.compile(r"```(?:[A-Za-z0-9_+-]+)?\s*(.*?)```", re.DOTALL)
    generic_blocks = [block.strip() for block in generic_pattern.findall(source) if str(block).strip()]
    if generic_blocks:
        js_like_blocks = [
            block
            for block in generic_blocks
            if "async function" in block or "await " in block or "function " in block
        ]
        return "\n\n".join(js_like_blocks or generic_blocks)

    if "async function" in source or "function " in source:
        return source.strip()
    return ""


def _fallback_extract_functions(code):
    functions = []
    code = str(code or "")
    cursor = 0
    async_pattern = re.compile(r"(?:^|\n)\s*(async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{", re.MULTILINE)
    while True:
        match = async_pattern.search(code, cursor)
        if not match:
            break
        is_async = bool(match.group(1))
        name = match.group(2)
        params_text = match.group(3).strip()
        params = [part.strip() for part in params_text.split(",") if part.strip()]
        brace_start = code.find("{", match.start())
        if brace_start < 0:
            break
        depth = 0
        end_index = None
        for idx in range(brace_start, len(code)):
            char = code[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end_index = idx + 1
                    break
        if end_index is None:
            break
        body = code[match.start():end_index].strip()
        functions.append({
            "name": name,
            "type": "AsyncFunctionDeclaration" if is_async else "FunctionDeclaration",
            "body": body,
            "params": params,
        })
        cursor = end_index
    return functions


def _split_top_level_js_args(raw_args):
    args = []
    current = []
    paren = 0
    bracket = 0
    brace = 0
    quote = None
    escape = False
    for ch in str(raw_args or ""):
        if quote:
            current.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
            continue
        if ch in ('\"', "'", "`"):
            quote = ch
            current.append(ch)
            continue
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren = max(0, paren - 1)
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket = max(0, bracket - 1)
        elif ch == "{":
            brace += 1
        elif ch == "}":
            brace = max(0, brace - 1)
        elif ch == "," and paren == 0 and bracket == 0 and brace == 0:
            args.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        args.append(tail)
    return args


def _extract_explore_timeouts(program_code):
    timeouts = []
    code = str(program_code or "")
    needle = "exploreUntil("
    start = 0
    while True:
        idx = code.find(needle, start)
        if idx == -1:
            break
        open_paren = code.find("(", idx)
        if open_paren == -1:
            break
        depth = 0
        quote = None
        escape = False
        close_paren = None
        for pos in range(open_paren, len(code)):
            ch = code[pos]
            if quote:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote:
                    quote = None
                continue
            if ch in ('\"', "'", "`"):
                quote = ch
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_paren = pos
                    break
        if close_paren is None:
            break
        args = _split_top_level_js_args(code[open_paren + 1 : close_paren])
        timeout_value = None
        if len(args) >= 3:
            timeout_raw = args[2].strip()
            if timeout_raw.isdigit():
                timeout_value = int(timeout_raw)
        timeouts.append(timeout_value)
        start = close_paren + 1
    return timeouts


class ActionAgent:
    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        request_timout=120,
        ckpt_dir="ckpt",
        resume=False,
        chat_log=True,
        execution_error=True,
        llm_url=None,
    ):
        self.ckpt_dir = ckpt_dir
        self.chat_log = chat_log
        self.execution_error = execution_error
        U.f_mkdir(f"{ckpt_dir}/action")
        if resume:
            print(f"\033[32mLoading Action Agent from {ckpt_dir}/action\033[0m")
            self.chest_memory = U.load_json(f"{ckpt_dir}/action/chest_memory.json")
        else:
            self.chest_memory = {}
        if model_name == "codex-gateway":
            self.llm = CodexGatewayLLM(
                url=os.getenv("VOYAGER_CODEX_GATEWAY_URL", "http://127.0.0.1:8787/codex/action"),
                model=os.getenv("VOYAGER_CODEX_MODEL", "gpt-5.5"),
                timeout_sec=request_timout,
            )
        else:
            llm_kwargs = {
                "model_name": model_name,
                "temperature": temperature,
                "request_timeout": request_timout,
            }
            if llm_url:
                llm_kwargs["openai_api_base"] = llm_url.removesuffix("/chat/completions")
            self.llm = ChatOpenAI(**llm_kwargs)

    def update_chest_memory(self, chests):
        for position, chest in chests.items():
            if position in self.chest_memory:
                if isinstance(chest, dict):
                    self.chest_memory[position] = chest
                if chest == "Invalid":
                    print(
                        f"\033[32mAction Agent removing chest {position}: {chest}\033[0m"
                    )
                    self.chest_memory.pop(position)
            else:
                if chest != "Invalid":
                    print(f"\033[32mAction Agent saving chest {position}: {chest}\033[0m")
                    self.chest_memory[position] = chest
        U.dump_json(self.chest_memory, f"{self.ckpt_dir}/action/chest_memory.json")

    def render_chest_observation(self):
        chests = []
        for chest_position, chest in self.chest_memory.items():
            if isinstance(chest, dict) and len(chest) > 0:
                chests.append(f"{chest_position}: {chest}")
        for chest_position, chest in self.chest_memory.items():
            if isinstance(chest, dict) and len(chest) == 0:
                chests.append(f"{chest_position}: Empty")
        for chest_position, chest in self.chest_memory.items():
            if isinstance(chest, str):
                assert chest == "Unknown"
                chests.append(f"{chest_position}: Unknown items inside")
        assert len(chests) == len(self.chest_memory)
        if chests:
            chests = "\n".join(chests)
            return f"Chests:\n{chests}\n\n"
        else:
            return f"Chests: None\n\n"

    def render_system_message(self, skills=[]):
        system_template = load_prompt("action_template")
        # FIXME: Hardcoded control_primitives
        base_skills = [
            "search",
            "exploreUntil",
            "mineBlock",
            "craftItem",
            "placeItem",
            "smeltItem",
            "killMob",
        ]
        if not self.llm.model_name == "gpt-3.5-turbo":
            base_skills += [
                "useChest",
                "mineflayer",
            ]
        programs = "\n\n".join(load_control_primitives_context(base_skills) + skills)
        response_format = load_prompt("action_response_format")
        system_message_prompt = SystemMessagePromptTemplate.from_template(
            system_template
        )
        system_message = system_message_prompt.format(
            programs=programs, response_format=response_format
        )
        assert isinstance(system_message, SystemMessage)
        return system_message

    def _validate_program_code(self, program_code):
        errors = []
        code = str(program_code or "")
        uses_search_helper = any(
            marker in code
            for marker in [
                "await searchAndHarvest(",
                "await searchAndCollectFood(",
                "await searchForOre(",
                "await recoverToSurface(",
                "await searchAndMove(",
                "await searchAndAct(",
            ]
        )
        explore_calls = code.count("await exploreUntil(")
        if explore_calls > 2:
            errors.append("Use at most two short exploreUntil probes in one function.")
        if explore_calls and not uses_search_helper:
            nearby_markers = [
                "bot.findBlock(",
                "bot.findBlocks(",
                "bot.nearestEntity(",
                "nearestEntity(",
            ]
            nearby_searches = sum(code.count(marker) for marker in nearby_markers)
            first_probe = code.find("await exploreUntil(")
            search_positions = [
                pos
                for pos in [code.find(marker) for marker in nearby_markers]
                if pos != -1
            ]
            first_nearby_search = min(search_positions) if search_positions else -1
            if nearby_searches == 0 or (first_nearby_search != -1 and first_nearby_search > first_probe):
                errors.append("Do a nearby 32-block search before any exploreUntil probe.")
            timeouts = _extract_explore_timeouts(code)
            if any(timeout is None for timeout in timeouts):
                errors.append("exploreUntil must use an explicit numeric maxTime so local search stays bounded.")
            if any(timeout is not None and (timeout < 10 or timeout > 20) for timeout in timeouts):
                errors.append("Each exploreUntil maxTime must be between 10 and 20 seconds.")
            if "LOCAL_SEARCH_EXHAUSTED" not in code:
                errors.append("If local probes fail, throw a concise LOCAL_SEARCH_EXHAUSTED error so the higher-level planner can change direction, biome, or prerequisites.")
        if any(log_name in code for log_name in ["oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"]):
            if explore_calls and not any(marker in code for marker in ["searchAndHarvest(", "recoverToSurface("]):
                errors.append("Wood search should prefer searchAndHarvest(...) and recoverToSurface(...) instead of hand-written wandering.")
        if any(food_marker in code for food_marker in ["cow", "pig", "chicken", "sheep", "rabbit", "wheat", "carrots", "potatoes", "beetroots"]):
            if "searchAndCollectFood(" not in code and "recoverToSurface(" not in code and explore_calls:
                errors.append("Food search should prefer searchAndCollectFood(...) and recoverToSurface(...) over custom exploreUntil loops.")
        if any(ore_marker in code for ore_marker in ["iron_ore", "coal_ore", "copper_ore", "gold_ore", "diamond_ore", "raw_iron", "raw_gold"]):
            if "searchForOre(" not in code and explore_calls:
                errors.append("Ore search should prefer searchForOre(...) over custom exploreUntil cave wandering.")
        return errors

    def render_human_message(
        self, *, events, code="", task="", context="", critique=""
    ):
        chat_messages = []
        error_messages = []
        # FIXME: damage_messages is not used
        damage_messages = []
        assert events[-1][0] == "observe", "Last event must be observe"
        biome = None
        time_of_day = None
        voxels = []
        entities = {}
        health = None
        hunger = None
        position = None
        equipment = None
        inventory_used = 0
        inventory = {}
        for i, (event_type, event) in enumerate(events):
            if event_type == "onChat":
                chat_messages.append(event["onChat"])
            elif event_type == "onError":
                error_messages.append(event["onError"])
            elif event_type == "onDamage":
                damage_messages.append(event["onDamage"])
            elif event_type == "observe":
                payload = event if isinstance(event, dict) else {}
                status = _payload_status(payload)
                biome = status.get("biome")
                time_of_day = status.get("timeOfDay")
                voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
                entities = status.get("entities") if isinstance(status.get("entities"), dict) else {}
                health = status.get("health")
                hunger = status.get("food")
                position = status.get("position") if isinstance(status.get("position"), dict) else None
                equipment = status.get("equipment")
                inventory_used = _safe_int(status.get("inventoryUsed") or 0)
                inventory = payload.get("inventory") if isinstance(payload.get("inventory"), dict) else {}
                assert i == len(events) - 1, "observe must be the last event"

        observation = ""

        if code:
            observation += f"Code from the last round:\n{code}\n\n"
        else:
            observation += f"Code from the last round: No code in the first round\n\n"

        if self.execution_error:
            if error_messages:
                error = "\n".join(error_messages)
                observation += f"Execution error:\n{error}\n\n"
            else:
                observation += f"Execution error: No error\n\n"

        if self.chat_log:
            if chat_messages:
                chat_log = "\n".join(chat_messages)
                observation += f"Chat log: {chat_log}\n\n"
            else:
                observation += f"Chat log: None\n\n"

        observation += f"Biome: {biome}\n\n"

        observation += f"Time: {time_of_day}\n\n"

        if voxels:
            observation += f"Nearby blocks: {', '.join(voxels)}\n\n"
        else:
            observation += f"Nearby blocks: None\n\n"

        if entities:
            nearby_entities = [
                k for k, v in sorted(entities.items(), key=lambda x: x[1])
            ]
            observation += f"Nearby entities (nearest to farthest): {', '.join(nearby_entities)}\n\n"
        else:
            observation += f"Nearby entities (nearest to farthest): None\n\n"

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

        if not (
            task == "Place and deposit useless items into a chest"
            or task.startswith("Deposit useless items into the chest at")
        ):
            observation += self.render_chest_observation()

        safety_lines = []
        hostile_nearby = _hostiles_nearby(entities)
        has_food = _inventory_has_food(inventory)
        if health is not None and health <= 12:
            safety_lines.append("Health is limited. Avoid combat, avoid fall risk, and prefer the nearest safe progress only.")
        if hostile_nearby:
            safety_lines.append("Hostile mobs are nearby. Disengage instead of fighting unless the task is explicitly survival-critical.")
        if hunger is not None and hunger <= 8 and not has_food:
            safety_lines.append("Hunger is low and no edible food is in inventory. Prefer nearby food or shelter over longer travel.")
        if _is_night(time_of_day):
            safety_lines.append("It is night or a dangerous transition period. Keep exploration short and local; shelter is better than long surface travel.")
        if any(token in str(task or "").lower() for token in ["wood", "log", "planks", "pickaxe", "axe", "food", "shelter", "coal", "torch", "iron"]):
            safety_lines.append("For bootstrap tasks, stop once the minimum safe progress is achieved instead of overextending.")
        if safety_lines:
            observation += "Safety policy:\n- " + "\n- ".join(safety_lines) + "\n\n"

        observation += f"Task: {task}\n\n"

        if context:
            observation += f"Context: {context}\n\n"
        else:
            observation += f"Context: None\n\n"

        if critique:
            observation += f"Critique: {critique}\n\n"
        else:
            observation += f"Critique: None\n\n"

        return HumanMessage(content=observation)

    def process_ai_message(self, message):
        assert isinstance(message, AIMessage)

        retry = 3
        error = None
        while retry > 0:
            try:
                babel = require("@babel/core")
                babel_generator_module = require("@babel/generator")
                babel_generator = getattr(babel_generator_module, "default", None) or babel_generator_module

                code = _extract_fenced_code_blocks(message.content)
                functions = []
                try:
                    parsed = babel.parse(code)
                    assert len(list(parsed.program.body)) > 0, "No functions found"
                    for i, node in enumerate(parsed.program.body):
                        if node.type != "FunctionDeclaration":
                            continue
                        node_type = (
                            "AsyncFunctionDeclaration"
                            if node["async"]
                            else "FunctionDeclaration"
                        )
                        functions.append(
                            {
                                "name": node.id.name,
                                "type": node_type,
                                "body": babel_generator(node).code,
                                "params": [getattr(param, "name", None) for param in list(node["params"])],
                            }
                        )
                except Exception:
                    functions = []
                if not functions:
                    functions = _fallback_extract_functions(code)
                # find the last async function
                main_function = None
                for function in reversed(functions):
                    if function["type"] == "AsyncFunctionDeclaration":
                        main_function = function
                        break
                assert (
                    main_function is not None
                ), "No async function found. Your main function must be async."
                assert (
                    len(main_function["params"]) == 1
                    and main_function["params"][0] == "bot"
                ), f"Main function {main_function['name']} must take a single argument named 'bot'"
                program_code = "\n\n".join(function["body"] for function in functions)
                validation_errors = self._validate_program_code(program_code)
                assert not validation_errors, " ".join(validation_errors)
                exec_code = f"await {main_function['name']}(bot);"
                return {
                    "program_code": program_code,
                    "program_name": main_function["name"],
                    "exec_code": exec_code,
                }
            except Exception as e:
                retry -= 1
                error = e
                time.sleep(1)
        raw_preview = message.content[:1200] if isinstance(message.content, str) else str(message.content)
        return (
            f"Error parsing action response (before program execution): {error}\n"
            f"Raw response preview:\n{raw_preview}"
        )

    def summarize_chatlog(self, events):
        def filter_item(message: str):
            craft_pattern = r"I cannot make \w+ because I need: (.*)"
            craft_pattern2 = (
                r"I cannot make \w+ because there is no crafting table nearby"
            )
            mine_pattern = r"I need at least a (.*) to mine \w+!"
            if re.match(craft_pattern, message):
                return re.match(craft_pattern, message).groups()[0]
            elif re.match(craft_pattern2, message):
                return "a nearby crafting table"
            elif re.match(mine_pattern, message):
                return re.match(mine_pattern, message).groups()[0]
            else:
                return ""

        chatlog = set()
        for event_type, event in events:
            if event_type == "onChat":
                item = filter_item(event["onChat"])
                if item:
                    chatlog.add(item)
        return "I also need " + ", ".join(chatlog) + "." if chatlog else ""
