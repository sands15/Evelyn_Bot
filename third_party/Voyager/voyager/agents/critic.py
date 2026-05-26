import re

from voyager.agents.outcome_policy import CriticOutcomePolicy
from voyager.agents.observation_utils import observe_payload, payload_inventory, payload_status, safe_int
from voyager.prompts import load_prompt
from voyager.utils.json_utils import fix_and_parse_json
from voyager.utils.console import safe_print as print
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage


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
        self.last_result = None
        self.outcome_policy = CriticOutcomePolicy()

    def _build_result(self, outcome, reason_code, critique="", evidence=None, source="critic"):
        normalized_outcome = str(outcome or "unknown").strip().lower()
        if normalized_outcome not in {"success", "partial", "fail", "unknown"}:
            normalized_outcome = "unknown"
        result = {
            "outcome": normalized_outcome,
            "success": normalized_outcome == "success",
            "reason_code": str(reason_code or "unspecified").strip().lower(),
            "critique": str(critique or "").strip(),
            "source": source,
            "evidence": evidence if isinstance(evidence, dict) else {},
        }
        self.last_result = result
        return result

    def _policy_result_to_result(self, value, outcome=None):
        if not isinstance(value, dict):
            return None
        success = bool(value.get("success"))
        return self._build_result(
            outcome or ("success" if success else "fail"),
            value.get("reason_code"),
            critique=value.get("critique"),
            evidence=value.get("evidence"),
            source=value.get("source") or "outcome_policy",
        )

    def _result_tuple(self, result):
        if not isinstance(result, dict):
            return False, ""
        return bool(result.get("success")), str(result.get("critique") or "")

    def render_system_message(self):
        system_message = SystemMessage(content=load_prompt("critic"))
        return system_message

    def render_human_message(self, *, events, task, context, chest_observation):
        assert events[-1][0] == "observe", "Last event must be observe"
        payload = observe_payload(events)
        status = payload_status(payload)
        biome = status.get("biome")
        time_of_day = status.get("timeOfDay")
        voxels = payload.get("voxels") if isinstance(payload.get("voxels"), list) else []
        health = status.get("health")
        hunger = status.get("food")
        position = status.get("position") if isinstance(status.get("position"), dict) else None
        equipment = status.get("equipment")
        inventory_used = safe_int(status.get("inventoryUsed") or 0)
        inventory = payload_inventory(payload)

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
        return self._build_result(
            "success" if success else "fail",
            "manual_confirmation" if success else "manual_rejection",
            critique=critique,
            source="manual",
        )

    def preflight_task_success(self, task, events=None):
        result = self._policy_result_to_result(
            self.outcome_policy.evaluate_preflight(task, events=events)
        )
        return self._result_tuple(result) if result is not None else None

    def ai_check_task_success(self, messages, max_retries=5):
        if max_retries == 0:
            print(
                "\033[31mFailed to parse Critic Agent response. Consider updating your prompt.\033[0m"
            )
            return self._build_result(
                "unknown",
                "critic_parse_exhausted",
                critique="Failed to parse critic response after retries.",
                source="critic_llm",
            )

        if messages[1] is None:
            return self._build_result(
                "unknown",
                "critic_human_message_missing",
                critique="Critic human message was missing.",
                source="critic_llm",
            )

        critic = self.llm(messages).content
        print(f"\033[31m****Critic Agent ai message****\n{critic}\033[0m")
        try:
            response = fix_and_parse_json(critic)
            assert response["success"] in [True, False]
            if "critique" not in response:
                response["critique"] = ""
            return self._build_result(
                "success" if response["success"] else "fail",
                "critic_llm_success" if response["success"] else "critic_llm_failure",
                critique=response["critique"],
                source="critic_llm",
                evidence={"raw_response": response},
            )
        except Exception as e:
            print(f"\033[31mError parsing critic response: {e} Trying again!\033[0m")
            return self.ai_check_task_success(
                messages=messages,
                max_retries=max_retries - 1,
            )

    def check_task_success_result(
        self, *, events, task, context, chest_observation, max_retries=5
    ):
        override = self._policy_result_to_result(
            self.outcome_policy.evaluate_post_action(task, events=events)
        )
        if override is not None:
            return override

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

    def check_task_success(
        self, *, events, task, context, chest_observation, max_retries=5
    ):
        result = self.check_task_success_result(
            events=events,
            task=task,
            context=context,
            chest_observation=chest_observation,
            max_retries=max_retries,
        )
        return self._result_tuple(result)
