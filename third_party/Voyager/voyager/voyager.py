import copy
import json
import os
import time
from datetime import datetime
from typing import Dict

BOOTSTRAP_SKILL_SKIP_TOKENS = (
    "wood log",
    "oak_log",
    "jungle_log",
    "birch_log",
    "spruce_log",
    "planks",
    "crafting_table",
    "wooden_pickaxe",
    "stone_pickaxe",
    "stone_axe",
    "cobblestone",
    "temporary shelter",
    "shelter",
    "food source",
    "retreat",
)

import voyager.utils as U
from .env import VoyagerEnv
from voyager.utils.console import safe_print as print

from .agents import ActionAgent
from .agents import CriticAgent
from .agents import CurriculumAgent
from .agents import SkillManager


# TODO: remove event memory
class Voyager:
    def __init__(
        self,
        mc_port: int = None,
        azure_login: Dict[str, str] = None,
        server_port: int = 3000,
        openai_api_key: str = None,
        env_wait_ticks: int = 20,
        env_request_timeout: int = 600,
        max_iterations: int = 160,
        reset_placed_if_failed: bool = False,
        action_agent_model_name: str = "gpt-4",
        action_agent_temperature: float = 0,
        action_agent_task_max_retries: int = 4,
        action_agent_show_chat_log: bool = True,
        action_agent_show_execution_error: bool = True,
        action_agent_llm_url: str = None,
        curriculum_agent_model_name: str = "gpt-4",
        curriculum_agent_temperature: float = 0,
        curriculum_agent_qa_model_name: str = "gpt-3.5-turbo",
        curriculum_agent_qa_temperature: float = 0,
        curriculum_agent_warm_up: Dict[str, int] = None,
        curriculum_agent_core_inventory_items: str = r".*_log|.*_planks|stick|crafting_table|furnace"
        r"|cobblestone|dirt|coal|.*_pickaxe|.*_sword|.*_axe",
        curriculum_agent_mode: str = "auto",
        curriculum_agent_llm_url: str = None,
        curriculum_agent_qa_llm_url: str = None,
        critic_agent_model_name: str = "gpt-4",
        critic_agent_temperature: float = 0,
        critic_agent_mode: str = "auto",
        critic_agent_llm_url: str = None,
        skill_manager_model_name: str = "gpt-3.5-turbo",
        skill_manager_temperature: float = 0,
        skill_manager_retrieval_top_k: int = 5,
        skill_manager_llm_url: str = None,
        openai_api_request_timeout: int = 240,
        ckpt_dir: str = "ckpt",
        skill_library_dir: str = None,
        resume: bool = False,
    ):
        """
        The main class for Voyager.
        Action agent is the iterative prompting mechanism in paper.
        Curriculum agent is the automatic curriculum in paper.
        Critic agent is the self-verification in paper.
        Skill manager is the skill library in paper.
        :param mc_port: minecraft in-game port
        :param azure_login: minecraft login config
        :param server_port: mineflayer port
        :param openai_api_key: openai api key
        :param env_wait_ticks: how many ticks at the end each step will wait, if you found some chat log missing,
        you should increase this value
        :param env_request_timeout: how many seconds to wait for each step, if the code execution exceeds this time,
        python side will terminate the connection and need to be resumed
        :param reset_placed_if_failed: whether to reset placed blocks if failed, useful for building task
        :param action_agent_model_name: action agent model name
        :param action_agent_temperature: action agent temperature
        :param action_agent_task_max_retries: how many times to retry if failed
        :param curriculum_agent_model_name: curriculum agent model name
        :param curriculum_agent_temperature: curriculum agent temperature
        :param curriculum_agent_qa_model_name: curriculum agent qa model name
        :param curriculum_agent_qa_temperature: curriculum agent qa temperature
        :param curriculum_agent_warm_up: info will show in curriculum human message
        if completed task larger than the value in dict, available keys are:
        {
            "context": int,
            "biome": int,
            "time": int,
            "other_blocks": int,
            "nearby_entities": int,
            "health": int,
            "hunger": int,
            "position": int,
            "equipment": int,
            "chests": int,
            "optional_inventory_items": int,
        }
        :param curriculum_agent_core_inventory_items: only show these items in inventory before optional_inventory_items
        reached in warm up
        :param curriculum_agent_mode: "auto" for automatic curriculum, "manual" for human curriculum
        :param critic_agent_model_name: critic agent model name
        :param critic_agent_temperature: critic agent temperature
        :param critic_agent_mode: "auto" for automatic critic ,"manual" for human critic
        :param skill_manager_model_name: skill manager model name
        :param skill_manager_temperature: skill manager temperature
        :param skill_manager_retrieval_top_k: how many skills to retrieve for each task
        :param openai_api_request_timeout: how many seconds to wait for openai api
        :param ckpt_dir: checkpoint dir
        :param skill_library_dir: skill library dir
        :param resume: whether to resume from checkpoint
        """
        # init env
        self.env = VoyagerEnv(
            mc_port=mc_port,
            azure_login=azure_login,
            server_port=server_port,
            request_timeout=env_request_timeout,
        )
        self.env_wait_ticks = env_wait_ticks
        self.reset_placed_if_failed = reset_placed_if_failed
        self.max_iterations = max_iterations

        # set openai api key
        os.environ["OPENAI_API_KEY"] = openai_api_key

        # init agents
        self.action_agent = ActionAgent(
            model_name=action_agent_model_name,
            temperature=action_agent_temperature,
            request_timout=openai_api_request_timeout,
            ckpt_dir=ckpt_dir,
            resume=resume,
            chat_log=action_agent_show_chat_log,
            execution_error=action_agent_show_execution_error,
            llm_url=action_agent_llm_url,
        )
        self.action_agent_task_max_retries = action_agent_task_max_retries
        self.curriculum_agent = CurriculumAgent(
            model_name=curriculum_agent_model_name,
            temperature=curriculum_agent_temperature,
            qa_model_name=curriculum_agent_qa_model_name,
            qa_temperature=curriculum_agent_qa_temperature,
            request_timout=openai_api_request_timeout,
            ckpt_dir=ckpt_dir,
            resume=resume,
            mode=curriculum_agent_mode,
            warm_up=curriculum_agent_warm_up,
            core_inventory_items=curriculum_agent_core_inventory_items,
            llm_url=curriculum_agent_llm_url,
            qa_llm_url=curriculum_agent_qa_llm_url,
        )
        self.critic_agent = CriticAgent(
            model_name=critic_agent_model_name,
            temperature=critic_agent_temperature,
            request_timout=openai_api_request_timeout,
            mode=critic_agent_mode,
            llm_url=critic_agent_llm_url,
        )
        self.skill_manager = SkillManager(
            model_name=skill_manager_model_name,
            temperature=skill_manager_temperature,
            retrieval_top_k=skill_manager_retrieval_top_k,
            request_timout=openai_api_request_timeout,
            ckpt_dir=skill_library_dir if skill_library_dir else ckpt_dir,
            resume=True if resume or skill_library_dir else False,
            llm_url=skill_manager_llm_url,
        )
        self.recorder = U.EventRecorder(ckpt_dir=ckpt_dir, resume=resume)
        self.resume = resume

        # init variables for rollout
        self.action_agent_rollout_num_iter = -1
        self.task = None
        self.context = ""
        self.messages = None
        self.conversations = []
        self.last_events = None
        self.last_phase = "initialized"
        self.last_phase_at = time.time()
        self.last_action_program_name = None
        self.last_action_code = None
        self.last_ai_response_preview = None
        self.last_env_event_count = None
        self.last_critique = None
        self.last_human_message_payload = None
        self.last_rollout_info = None
        self.last_task_result = None
        self.last_task_result_at = None
        self.last_completion_reason = None
        self.last_success = None
        self.current_rollout_started_at = None
        self.pending_countermeasure = None
        self.last_search_metrics = None

    def _set_phase(self, phase, **updates):
        self.last_phase = phase
        self.last_phase_at = time.time()
        for key, value in updates.items():
            setattr(self, key, value)

    def _merge_telemetry_into_events(self, events, telemetry):
        if not events or not telemetry:
            return events
        merged_events = copy.deepcopy(events)
        for index in range(len(merged_events) - 1, -1, -1):
            event_type, event = merged_events[index]
            if event_type != "observe" or not isinstance(event, dict):
                continue
            status = event.get("status") if isinstance(event.get("status"), dict) else {}
            telemetry_status = telemetry.get("status") if isinstance(telemetry.get("status"), dict) else {}
            if telemetry_status:
                status.update(telemetry_status)
                event["status"] = status
            if isinstance(telemetry.get("inventory"), dict):
                event["inventory"] = telemetry.get("inventory")
            if isinstance(telemetry.get("connectionState"), str):
                event["connectionState"] = telemetry.get("connectionState")
            if isinstance(telemetry.get("connectionNote"), str):
                event["connectionNote"] = telemetry.get("connectionNote")
            if isinstance(telemetry.get("lastDeathEvent"), dict):
                event["lastDeathEvent"] = telemetry.get("lastDeathEvent")
            if isinstance(telemetry.get("deathEventLogPath"), str):
                event["deathEventLogPath"] = telemetry.get("deathEventLogPath")
            if isinstance(telemetry.get("recordedAt"), str):
                event["telemetryRecordedAt"] = telemetry.get("recordedAt")
            break
        return merged_events

    def _event_status(self, event):
        return event.get("status") if isinstance(event.get("status"), dict) else {}

    def _event_position(self, event):
        status = self._event_status(event)
        position = status.get("position")
        if not isinstance(position, dict):
            return None
        if not all(position.get(axis) is not None for axis in ("x", "y", "z")):
            return None
        return position

    def _latest_event_payload(self, events=None):
        source_events = events if events is not None else self.last_events
        if not isinstance(source_events, list) or not source_events:
            return {}
        payload = source_events[-1][1]
        return payload if isinstance(payload, dict) else {}

    def _parse_event_timestamp(self, raw_value):
        if not isinstance(raw_value, str) or not raw_value:
            return None
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def _death_event_during_current_rollout(self, events=None):
        if self.current_rollout_started_at is None:
            return None
        payload = self._latest_event_payload(events)
        death_event = payload.get("lastDeathEvent") if isinstance(payload.get("lastDeathEvent"), dict) else None
        if not isinstance(death_event, dict):
            return None
        event_ts = self._parse_event_timestamp(death_event.get("respawn_observed_at"))
        if event_ts is None:
            event_ts = self._parse_event_timestamp(death_event.get("recorded_at"))
        if event_ts is None or event_ts < self.current_rollout_started_at:
            return None
        return death_event

    def _build_recovery_reset_options(self):
        options = {
            "mode": "hard",
            "wait_ticks": self.env_wait_ticks,
        }
        payload = self._latest_event_payload()
        if not payload:
            return options
        inventory = payload.get("inventory")
        if isinstance(inventory, dict):
            options["inventory"] = inventory
        status = self._event_status(payload)
        equipment = status.get("equipment")
        if isinstance(equipment, list):
            options["equipment"] = equipment
        position = self._event_position(payload)
        if position:
            options["position"] = position
        return options

    def _synthesize_death_countermeasure(self, death_event, task=None):
        if not isinstance(death_event, dict):
            return None
        cause = str(death_event.get("cause") or "").strip()
        death_message = str(death_event.get("death_message") or "").strip()
        likely_reason = str(death_event.get("likely_reason") or "").strip()
        likely_killer = str(death_event.get("likely_killer") or "").strip()
        combined = " ".join(part for part in [cause, death_message, likely_reason, likely_killer] if part).lower()
        category = "general"
        guidance = [
            "Recover in place instead of rushing back into the same hazard.",
            "Take the shortest safe action that restores stability before continuing the task.",
        ]
        skill_hint = "Prefer safer, shorter actions until stability is restored."
        if any(token in combined for token in ["drown", "drowned", "water", "bubble"]):
            category = "drowning"
            guidance = [
                "Treat water as an active hazard until back on solid ground.",
                "Surface immediately when submerged and abort underwater work if air is limited.",
                "Prefer shoreline or shallow-water routes over direct deep-water travel.",
                "Use recoverToSurface-style behavior before resuming normal search or gathering.",
            ]
            skill_hint = "Add a water-safety guard: leave water quickly, avoid long underwater pathing, prefer dry access routes, and recover to the surface before resuming search."
        elif any(token in combined for token in ["lava", "burn", "fire", "magma"]):
            category = "lava_fire"
            guidance = [
                "Back away from exposed lava or fire before mining or fighting.",
                "Do not dig or bridge directly over suspected lava pockets without confirming footing.",
                "Choose routes with solid escape space instead of tight ledges.",
            ]
            skill_hint = "Add a lava-safety guard: keep distance from lava, avoid unstable ledges, and secure the floor before continuing."
        elif any(token in combined for token in ["fell", "fall", "hit the ground", "cliff"]):
            category = "fall"
            guidance = [
                "Assume the terrain is unsafe until a flat recovery route is found.",
                "Descend one block at a time and avoid sprinting near drops.",
                "Stabilize on level ground before reattempting gathering or travel.",
            ]
            skill_hint = "Add a fall-safety guard: favor flat movement, slow descents, and avoid cliff-edge pathing."
        elif any(token in combined for token in ["starv", "hunger"]):
            category = "starvation"
            guidance = [
                "Secure food before any optional travel, mining, or combat.",
                "Keep a reserve instead of consuming the last food item too late.",
            ]
            skill_hint = "Add a food-safety guard: do not start long tasks without edible food or an immediate food plan."
        elif any(token in combined for token in ["slain", "shot", "blown up", "creeper", "skeleton", "zombie", "spider", "drowned", "witch", "enderman"]):
            category = "hostile"
            guidance = [
                "Disengage from hostiles unless combat is the actual task.",
                "Use shelter, distance, or terrain cover before resuming work.",
                "Prefer local safe progress over chasing drops or distant targets.",
                "If the area is wrong for the current task, recover or reposition before searching again.",
            ]
            skill_hint = "Add a combat-safety guard: break line of sight, avoid open fights, and return only after stabilizing health, position, and search domain."
        summary = f"Death-derived countermeasure ({category})"
        if task:
            summary += f" for task '{task}'"
        summary += ": " + " ".join(guidance)
        return {
            "category": category,
            "summary": summary,
            "guidance": guidance,
            "skill_hint": skill_hint,
            "death_event": copy.deepcopy(death_event),
            "created_at": time.time(),
        }

    def _active_countermeasure(self):
        countermeasure = self.pending_countermeasure
        if not isinstance(countermeasure, dict):
            return None
        created_at = countermeasure.get("created_at")
        if isinstance(created_at, (int, float)) and (time.time() - float(created_at)) > 900:
            self.pending_countermeasure = None
            return None
        return countermeasure

    def _extract_search_failure_reason(self, completion_reason, critique):
        known_reasons = [
            "LOCAL_SEARCH_EXHAUSTED",
            "surface_recovery_stalled",
            "surface_recovery_timeout",
            "surface_recovery_exhausted",
            "surface_not_found",
            "wood_scout_stalled",
            "wood_scout_timeout",
            "wood_scout_exhausted",
            "food_scout_stalled",
            "food_scout_timeout",
            "food_scout_exhausted",
            "ore_scout_stalled",
            "ore_scout_timeout",
            "ore_scout_exhausted",
            "search_budget_exhausted",
        ]
        if completion_reason in known_reasons:
            return completion_reason
        critique_text = str(critique or "")
        for reason in known_reasons:
            if reason in critique_text:
                return reason
        return None

    def _build_search_metrics(self, parsed_result, completion_reason, critique, success, events=None):
        code = ""
        if isinstance(parsed_result, dict):
            code = str(parsed_result.get("program_code") or "") + "\n" + str(parsed_result.get("exec_code") or "")
        payload = self._latest_event_payload(events)
        search_execution = payload.get("searchExecution") if isinstance(payload.get("searchExecution"), dict) else None
        helper = None
        goal_type = None
        helper_markers = [
            ("searchAndHarvest(", "searchAndHarvest"),
            ("searchAndCollectFood(", "searchAndCollectFood"),
            ("searchForOre(", "searchForOre"),
            ("recoverToSurface(", "recoverToSurface"),
            ("searchAndMove(", "searchAndMove"),
            ("searchAndAct(", "searchAndAct"),
        ]
        for marker, helper_name in helper_markers:
            if marker in code:
                helper = helper_name
                break
        if "goalType: \"wood\"" in code or "goalType: 'wood'" in code:
            goal_type = "wood"
        elif "goalType: \"food\"" in code or "goalType: 'food'" in code:
            goal_type = "food"
        elif "goalType: \"recovery\"" in code or "goalType: 'recovery'" in code:
            goal_type = "recovery"
        elif "goalType: \"ore\"" in code or "goalType: 'ore'" in code:
            goal_type = "ore"
        elif helper == "searchAndHarvest":
            goal_type = "wood"
        elif helper == "searchAndCollectFood":
            goal_type = "food"
        elif helper == "searchForOre":
            goal_type = "ore"
        elif helper == "recoverToSurface":
            goal_type = "recovery"
        if isinstance(search_execution, dict):
            helper = search_execution.get("helper") or helper
            goal_type = search_execution.get("goalType") or goal_type
        failure_reason = self._extract_search_failure_reason(completion_reason, critique)
        if isinstance(search_execution, dict) and search_execution.get("reason") and not failure_reason:
            failure_reason = search_execution.get("reason")
        if success:
            failure_reason = None
        if not helper and not failure_reason and not search_execution:
            return None
        metrics = {
            "task": self.task,
            "helper": helper or "none",
            "goal_type": goal_type or "generic",
            "success": bool(success),
            "completion_reason": completion_reason,
            "failure_reason": failure_reason,
            "rollout_iteration": self.action_agent_rollout_num_iter,
            "recorded_at": time.time(),
        }
        if isinstance(parsed_result, dict):
            metrics["program_name"] = parsed_result.get("program_name")
        if isinstance(search_execution, dict):
            metrics["execution"] = copy.deepcopy(search_execution)
            metrics["execution_mode"] = search_execution.get("mode")
            metrics["execution_status"] = search_execution.get("status")
            metrics["attempted_targets"] = search_execution.get("attemptedTargets")
            metrics["search_profile_domain"] = search_execution.get("profileDomain")
            metrics["budget_spent_sec"] = search_execution.get("budgetSpentSec")
            metrics["stuck_events"] = search_execution.get("stuckEvents")
            metrics["replans"] = search_execution.get("replans")
            metrics["outcome_category"] = search_execution.get("outcomeCategory")
            metrics["failure_category"] = search_execution.get("failureCategory")
            metrics["countermeasure_applied"] = search_execution.get("countermeasureApplied")
            search_policy = search_execution.get("searchPolicy") if isinstance(search_execution.get("searchPolicy"), dict) else None
            if search_policy:
                metrics["search_policy"] = copy.deepcopy(search_policy)
                metrics["policy_radius_bonus"] = search_policy.get("radiusBonus")
                metrics["policy_time_budget_scale"] = search_policy.get("timeBudgetScale")
                metrics["policy_progress_timeout_scale"] = search_policy.get("progressTimeoutScale")
                metrics["policy_force_domain"] = search_policy.get("forceDomain")
                metrics["policy_consecutive_failures"] = search_policy.get("consecutiveFailures")
            progress_before = search_execution.get("progressBefore") if isinstance(search_execution.get("progressBefore"), dict) else None
            progress_after = search_execution.get("progressAfter") if isinstance(search_execution.get("progressAfter"), dict) else None
            metrics["progress_score_before"] = progress_before.get("score") if progress_before else None
            metrics["progress_score_after"] = progress_after.get("score") if progress_after else None
            metrics["progress_delta"] = search_execution.get("progressDelta")
        return metrics

    def _sync_search_policy_to_env(self):
        if not self.env:
            return None
        policy_payload = self.skill_manager.export_search_policy()
        if not policy_payload:
            return None
        script = "bot._voyagerSearchPolicy = " + json.dumps(policy_payload, ensure_ascii=False) + ";"
        events = self.env.step(script, programs=self.skill_manager.programs)
        if isinstance(events, list) and events:
            self.last_events = copy.deepcopy(events)
        return policy_payload

    def _augment_context_with_countermeasure(self, context):
        countermeasure = self._active_countermeasure()
        if not countermeasure:
            return context
        guidance = countermeasure.get("guidance") if isinstance(countermeasure.get("guidance"), list) else []
        summary = str(countermeasure.get("summary") or "").strip()
        lines = [line for line in guidance if isinstance(line, str) and line.strip()]
        addition = summary
        if lines:
            addition += "\n- " + "\n- ".join(lines)
        addition = addition.strip()
        if not addition:
            return context
        if context:
            return context + "\n\n" + addition
        return addition

    def refresh_live_state(self, refresh_messages=False):
        if not self.env:
            return None
        if not getattr(self.env, "has_reset", False):
            return None
        telemetry = self.env.telemetry()
        if not telemetry:
            return None
        if self.last_events:
            self.last_events = self._merge_telemetry_into_events(self.last_events, telemetry)
        if refresh_messages and self.messages and self.last_human_message_payload:
            payload = dict(self.last_human_message_payload)
            payload["events"] = self._merge_telemetry_into_events(payload.get("events"), telemetry)
            base_context = payload.get("context_base") if isinstance(payload.get("context_base"), str) else (payload.get("context") or "")
            payload["context"] = self._augment_context_with_countermeasure(base_context)
            render_payload = {
                "events": payload.get("events"),
                "code": payload.get("code", ""),
                "task": payload.get("task", ""),
                "context": payload.get("context", ""),
                "critique": payload.get("critique", ""),
            }
            human_message = self.action_agent.render_human_message(**render_payload)
            self.messages = [self.messages[0], human_message]
            self.last_human_message_payload = payload
        return telemetry

    def reset(self, task, context="", reset_env=True):
        self.action_agent_rollout_num_iter = 0
        self.task = task
        context = self._augment_context_with_countermeasure(context)
        self.context = context
        self.current_rollout_started_at = time.time()
        self._set_phase(
            "reset_start",
            last_action_program_name=None,
            last_action_code=None,
            last_ai_response_preview=None,
            last_env_event_count=None,
            last_critique=None,
            last_completion_reason=None,
            last_success=None,
        )
        self.last_rollout_info = None
        if reset_env:
            self.env.reset(
                options={
                    "mode": "soft",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
        difficulty = (
            "easy" if len(self.curriculum_agent.completed_tasks) > 15 else "peaceful"
        )
        # step to peek an observation
        events = self.env.step(
            "bot.chat(`/time set ${getNextTime()}`);\n"
            + f"bot.chat('/difficulty {difficulty}');"
        )
        self._set_phase("reset_observation_ready", last_env_event_count=len(events))
        synced_search_policy = self._sync_search_policy_to_env()
        if synced_search_policy:
            events = copy.deepcopy(self.last_events)
        skills = self.skill_manager.retrieve_skills(query=self.context)
        print(
            f"\033[33mRender Action Agent system message with {len(skills)} skills\033[0m"
        )
        system_message = self.action_agent.render_system_message(skills=skills)
        human_message = self.action_agent.render_human_message(
            events=events, code="", task=self.task, context=context, critique=""
        )
        self.last_events = copy.deepcopy(events)
        self.last_human_message_payload = {
            "events": copy.deepcopy(events),
            "code": "",
            "task": self.task,
            "context": context,
            "context_base": context,
            "critique": "",
        }
        self.messages = [system_message, human_message]
        self._set_phase("awaiting_action_llm")
        print(
            f"\033[32m****Action Agent human message****\n{human_message.content}\033[0m"
        )
        assert len(self.messages) == 2
        self.conversations = []
        return self.messages

    def close(self):
        self.env.close()

    def step(self):
        if self.action_agent_rollout_num_iter < 0:
            raise ValueError("Agent must be reset before stepping")
        success = False
        critique = ""
        self.refresh_live_state(refresh_messages=True)
        preflight_success = self.critic_agent.preflight_task_success(self.task, events=self.last_events)
        if preflight_success is not None and preflight_success[0] is True:
            success = True
            critique = preflight_success[1]
            self.pending_countermeasure = None
            info = {
                "task": self.task,
                "success": True,
                "conversations": self.conversations,
                "critique": "",
                "done": True,
                "rollout_iteration": self.action_agent_rollout_num_iter,
                "max_rollout_iterations": self.action_agent_task_max_retries,
                "completion_reason": "preflight_success",
            }
            self.last_rollout_info = copy.deepcopy(info)
            self.last_completion_reason = "preflight_success"
            self.last_success = True
            self.last_task_result = copy.deepcopy(info)
            self.last_task_result_at = time.time()
            self._set_phase(
                "task_rollout_finished",
                last_critique=critique,
                last_completion_reason="preflight_success",
                last_success=True,
            )
            return self.messages, 0, True, info
        self._set_phase("action_llm_request")
        ai_message = self.action_agent.llm(self.messages)
        self._set_phase(
            "action_llm_response",
            last_ai_response_preview=(ai_message.content[:1200] if isinstance(ai_message.content, str) else str(ai_message.content)),
        )
        print(f"\033[34m****Action Agent ai message****\n{ai_message.content}\033[0m")
        self.conversations.append(
            (self.messages[0].content, self.messages[1].content, ai_message.content)
        )
        parsed_result = self.action_agent.process_ai_message(message=ai_message)
        if isinstance(parsed_result, dict):
            code = parsed_result["program_code"] + "\n" + parsed_result["exec_code"]
            self._set_phase(
                "action_program_ready",
                last_action_program_name=parsed_result.get("program_name"),
                last_action_code=code[:4000],
            )
            interrupted_by_death = False
            try:
                events = self.env.step(
                    code,
                    programs=self.skill_manager.programs,
                )
            except RuntimeError as exc:
                if "interrupted by death event" not in str(exc).lower():
                    raise
                interrupted_by_death = True
                self._set_phase("death_interrupt_observed", last_critique=str(exc))
                events = self.env.step(
                    "",
                    programs=self.skill_manager.programs,
                )
            self._set_phase("minecraft_step_response", last_env_event_count=len(events))
            self.recorder.record(events, self.task)
            self.action_agent.update_chest_memory(events[-1][1]["nearbyChests"])
            death_event = self._death_event_during_current_rollout(events)
            if interrupted_by_death and death_event is None:
                payload = self._latest_event_payload(events)
                death_event = payload.get("lastDeathEvent") if isinstance(payload.get("lastDeathEvent"), dict) else None
            if death_event is not None:
                success = False
                countermeasure = self._synthesize_death_countermeasure(death_event, task=self.task)
                self.pending_countermeasure = countermeasure
                critique = (
                    f"Death recovery override: task rollout ended because the bot died during execution ({death_event.get('death_message') or death_event.get('cause') or 'unknown cause'})."
                )
                if countermeasure and countermeasure.get("guidance"):
                    critique += " Countermeasure: " + " ".join(
                        str(line).strip() for line in countermeasure.get("guidance") if str(line).strip()
                    )
                self.action_agent_rollout_num_iter = self.action_agent_task_max_retries - 1
                self._set_phase("death_recovery_required", last_critique=critique)
            elif success:
                self.pending_countermeasure = None
            else:
                self._set_phase("critic_check")
                success, critique = self.critic_agent.check_task_success(
                    events=events,
                    task=self.task,
                    context=self.context,
                    chest_observation=self.action_agent.render_chest_observation(),
                    max_retries=5,
                )
                if success:
                    self.pending_countermeasure = None
                self._set_phase("critic_result", last_critique=critique)

            if self.reset_placed_if_failed and not success:
                # revert all the placing event in the last step
                blocks = []
                positions = []
                for event_type, event in events:
                    if event_type == "onSave" and event["onSave"].endswith("_placed"):
                        block = event["onSave"].split("_placed")[0]
                        position = self._event_position(event)
                        if position:
                            blocks.append(block)
                            positions.append(position)
                if blocks:
                    new_events = self.env.step(
                        f"await givePlacedItemBack(bot, {U.json_dumps(blocks)}, {U.json_dumps(positions)})",
                        programs=self.skill_manager.programs,
                    )
                    events[-1][1]["inventory"] = new_events[-1][1]["inventory"]
                    events[-1][1]["voxels"] = new_events[-1][1]["voxels"]
            self._set_phase("skill_retrieval")
            new_skills = self.skill_manager.retrieve_skills(
                query=self.context
                + "\n\n"
                + self.action_agent.summarize_chatlog(events)
            )
            system_message = self.action_agent.render_system_message(skills=new_skills)
            human_message = self.action_agent.render_human_message(
                events=events,
                code=parsed_result["program_code"],
                task=self.task,
                context=self.context,
                critique=critique,
            )
            self.last_events = copy.deepcopy(events)
            self.last_human_message_payload = {
                "events": copy.deepcopy(events),
                "code": parsed_result["program_code"],
                "task": self.task,
                "context": self.context,
                "context_base": self.context,
                "critique": critique,
            }
            self.messages = [system_message, human_message]
            self._set_phase("action_step_complete")
        else:
            assert isinstance(parsed_result, str)
            self._set_phase("action_parse_failed", last_action_code=str(parsed_result)[:4000])
            self.recorder.record([], self.task)
            print(f"\033[34m{parsed_result} Trying again!\033[0m")
        assert len(self.messages) == 2
        self.action_agent_rollout_num_iter += 1
        done = (
            self.action_agent_rollout_num_iter >= self.action_agent_task_max_retries
            or success
        )
        death_event = self._death_event_during_current_rollout(self.last_events if isinstance(parsed_result, dict) else None)
        if success:
            completion_reason = "critic_success"
        elif death_event is not None:
            completion_reason = "death_recovery_required"
        elif self.action_agent_rollout_num_iter >= self.action_agent_task_max_retries:
            completion_reason = "max_retries_exhausted"
        elif not isinstance(parsed_result, dict):
            completion_reason = "action_parse_failed"
        else:
            completion_reason = "retrying"
        info = {
            "task": self.task,
            "success": success,
            "conversations": self.conversations,
            "critique": critique if not success else "",
            "done": done,
            "rollout_iteration": self.action_agent_rollout_num_iter,
            "max_rollout_iterations": self.action_agent_task_max_retries,
            "completion_reason": completion_reason,
        }
        active_countermeasure = self._active_countermeasure()
        if active_countermeasure:
            info["death_countermeasure"] = copy.deepcopy(active_countermeasure)
        search_metrics = self._build_search_metrics(parsed_result, completion_reason, critique, success, events if isinstance(parsed_result, dict) else None)
        self.last_search_metrics = copy.deepcopy(search_metrics)
        if search_metrics:
            search_policy_update = self.skill_manager.record_search_outcome(search_metrics)
            if search_policy_update:
                search_metrics["persisted_search_policy"] = copy.deepcopy(search_policy_update)
                info["search_policy_update"] = copy.deepcopy(search_policy_update)
            info["search_metrics"] = copy.deepcopy(search_metrics)
        if isinstance(parsed_result, dict):
            info["program_code"] = parsed_result.get("program_code")
            info["program_name"] = parsed_result.get("program_name")
        if success:
            assert (
                "program_code" in parsed_result and "program_name" in parsed_result
            ), "program and program_name must be returned when success"
        else:
            print(
                f"\033[32m****Action Agent human message****\n{self.messages[-1].content}\033[0m"
            )
        self.last_rollout_info = copy.deepcopy(info)
        self.last_completion_reason = completion_reason
        self.last_success = bool(success)
        if done:
            self.last_task_result = copy.deepcopy(info)
            self.last_task_result_at = time.time()
            self._set_phase(
                "task_rollout_finished",
                last_critique=critique,
                last_completion_reason=completion_reason,
                last_success=bool(success),
            )
        return self.messages, 0, done, info

    def rollout(self, *, task, context, reset_env=True):
        self.reset(task=task, context=context, reset_env=reset_env)
        while True:
            messages, reward, done, info = self.step()
            if done:
                break
        return messages, reward, done, info

    def _should_save_skill(self, info):
        if not isinstance(info, dict) or not info.get("success"):
            return False
        task_text = str(info.get("task") or "").strip().lower()
        if any(token in task_text for token in BOOTSTRAP_SKILL_SKIP_TOKENS):
            return False
        completion_reason = str(info.get("completion_reason") or "").strip().lower()
        if completion_reason and completion_reason != "critic_success":
            return False
        return bool(info.get("program_name") and info.get("program_code"))

    def learn(self, reset_env=True):
        if self.resume:
            # keep the inventory
            self.env.reset(
                options={
                    "mode": "soft",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
        else:
            # clear the inventory
            self.env.reset(
                options={
                    "mode": "hard",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
            self.resume = True
        self.last_events = self.env.step("")
        self._sync_search_policy_to_env()

        while True:
            if self.recorder.iteration > self.max_iterations:
                print("Iteration limit reached")
                break
            self.refresh_live_state(refresh_messages=False)
            task, context = self.curriculum_agent.propose_next_task(
                events=self.last_events,
                chest_observation=self.action_agent.render_chest_observation(),
                max_retries=5,
            )
            print(
                f"\033[35mStarting task {task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            try:
                messages, reward, done, info = self.rollout(
                    task=task,
                    context=context,
                    reset_env=reset_env,
                )
            except Exception as e:
                time.sleep(3)  # wait for mineflayer to exit
                info = {
                    "task": task,
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
                try:
                    self.last_events = self.env.reset(
                        options=self._build_recovery_reset_options()
                    )
                except Exception as reset_error:
                    info["reset_error"] = str(reset_error)
                    self.last_events = self.env.reset(
                        options={
                            "mode": "hard",
                            "wait_ticks": self.env_wait_ticks,
                        }
                    )
                # use red color background to print the error
                print("Your last round rollout terminated due to error:")
                print(f"\033[41m{e}\033[0m")

            self._set_phase("post_rollout_processing")
            try:
                self.skill_manager.record_skill_outcome(info)
            except Exception as skill_outcome_error:
                print(f"\033[33mSkill outcome recording failed: {skill_outcome_error}\033[0m")
            if self._should_save_skill(info):
                self._set_phase("skill_persistence")
                try:
                    self.skill_manager.add_new_skill(info)
                except Exception as skill_save_error:
                    print(f"\033[33mSkill save skipped after error: {skill_save_error}\033[0m")
            self._set_phase("curriculum_progress_update")
            self.curriculum_agent.update_exploration_progress(info)
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks) if self.curriculum_agent.completed_tasks else 'None'}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {self.curriculum_agent.summarize_failed_tasks()}\033[0m"
            )

        return {
            "completed_tasks": self.curriculum_agent.completed_tasks,
            "failed_tasks": self.curriculum_agent.failed_tasks,
            "skills": self.skill_manager.skills,
        }

    def decompose_task(self, task):
        if not self.last_events:
            self.last_events = self.env.reset(
                options={
                    "mode": "hard",
                    "wait_ticks": self.env_wait_ticks,
                }
            )
        self.refresh_live_state(refresh_messages=False)
        return self.curriculum_agent.decompose_task(task, self.last_events)

    def inference(self, task=None, sub_goals=[], reset_mode="hard", reset_env=True):
        if not task and not sub_goals:
            raise ValueError("Either task or sub_goals must be provided")
        self.env.reset(
            options={
                "mode": reset_mode,
                "wait_ticks": self.env_wait_ticks,
            }
        )
        self.curriculum_agent.completed_tasks = []
        self.curriculum_agent.failed_tasks = []
        self.last_events = self.env.step("")
        self._sync_search_policy_to_env()
        self.refresh_live_state(refresh_messages=False)
        if not sub_goals:
            sub_goals = self.curriculum_agent.decompose_task(task, self.last_events)
        task_results = []
        subgoal_index = 0
        while subgoal_index < len(sub_goals):
            next_task = sub_goals[subgoal_index]
            context = self.curriculum_agent.get_task_context(next_task)
            print(
                f"\033[35mStarting task {next_task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            try:
                messages, reward, done, info = self.rollout(
                    task=next_task,
                    context=context,
                    reset_env=reset_env,
                )
            except Exception as e:
                time.sleep(3)  # wait for mineflayer to exit
                info = {
                    "task": next_task,
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
                try:
                    if self.last_events:
                        self.last_events = self.env.reset(
                            options=self._build_recovery_reset_options()
                        )
                    else:
                        self.last_events = self.env.reset(
                            options={
                                "mode": "hard",
                                "wait_ticks": self.env_wait_ticks,
                            }
                        )
                except Exception as reset_error:
                    info["reset_error"] = str(reset_error)
                    self.last_events = self.env.reset(
                        options={
                            "mode": "hard",
                            "wait_ticks": self.env_wait_ticks,
                        }
                    )
                print("Your last round rollout terminated due to error:")
                print(f"\033[41m{e}\033[0m")
            self.curriculum_agent.update_exploration_progress(info)
            task_results.append(
                {
                    "task": next_task,
                    "success": bool(info.get("success")),
                    "error": info.get("error"),
                    "error_type": info.get("error_type"),
                    "program_name": info.get("program_name"),
                }
            )
            subgoal_index += 1
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks) if self.curriculum_agent.completed_tasks else 'None'}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {self.curriculum_agent.summarize_failed_tasks()}\033[0m"
            )
        return {
            "status": "completed",
            "goal": task,
            "sub_goals": sub_goals,
            "task_results": task_results,
            "completed_tasks": list(self.curriculum_agent.completed_tasks),
            "failed_tasks": list(self.curriculum_agent.failed_tasks),
            "progress_index": subgoal_index,
            "total_sub_goals": len(sub_goals),
            "success": len(self.curriculum_agent.completed_tasks) == len(sub_goals),
        }
