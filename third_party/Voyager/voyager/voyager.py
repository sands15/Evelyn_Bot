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
from .agents.hazard_taxonomy import classify_death_event
from .agents.world_effect_verifier import verify_task_effect


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
        self.current_speculative_next_task = None
        self.last_speculative_decision = None
        self.last_inventory_plan = None
        self.last_task_contract_decision = None
        self._trace_sequence = 0
        self.current_task_bookkeeping = None
        self.last_task_bookkeeping = None
        self.last_world_effect_verification = None
        self.last_critic_result = None
        self.last_recovery_boundary = None
        self.execution_session = None
        self.reset_audit_log = []

    def _next_trace_id(self, prefix):
        self._trace_sequence += 1
        return f"{prefix}-{int(time.time() * 1000)}-{self._trace_sequence}"

    def _observation_id_from_events(self, events=None):
        source_events = events if events is not None else self.last_events
        if not isinstance(source_events, list) or not source_events:
            return None
        payload = source_events[-1][1]
        if not isinstance(payload, dict):
            return None
        existing = payload.get("_observation_id")
        if isinstance(existing, str) and existing:
            return existing
        observation_id = self._next_trace_id("obs")
        payload["_observation_id"] = observation_id
        return observation_id

    def _start_task_bookkeeping(self, task, context="", observation_id_before=None):
        self.current_task_bookkeeping = {
            "task": task,
            "context": context,
            "goal_id": self._next_trace_id("goal"),
            "plan_id": self._next_trace_id("plan"),
            "step_id": self._next_trace_id("step"),
            "action_id": None,
            "effect_check_id": None,
            "observation_id_before": observation_id_before,
            "observation_id_after": None,
            "status": "pending",
            "done_reason": None,
            "success": None,
            "verification_state": "not_started",
            "rollout_iteration": self.action_agent_rollout_num_iter,
            "max_rollout_iterations": self.action_agent_task_max_retries,
            "action_path": None,
            "program_name": None,
            "updated_at": time.time(),
        }
        self.last_task_bookkeeping = copy.deepcopy(self.current_task_bookkeeping)

    def _set_task_bookkeeping(self, **updates):
        if not isinstance(self.current_task_bookkeeping, dict):
            self._start_task_bookkeeping(
                self.task or "",
                self.context or "",
                observation_id_before=self._observation_id_from_events(),
            )
        self.current_task_bookkeeping.update(updates)
        self.current_task_bookkeeping["updated_at"] = time.time()
        self.last_task_bookkeeping = copy.deepcopy(self.current_task_bookkeeping)

    def _bookkeeping_snapshot(self):
        if isinstance(self.current_task_bookkeeping, dict):
            return copy.deepcopy(self.current_task_bookkeeping)
        if isinstance(self.last_task_bookkeeping, dict):
            return copy.deepcopy(self.last_task_bookkeeping)
        return None

    def _classify_recovery_boundary(self, *, completion_reason=None, success=False, critique="", info=None):
        payload = info if isinstance(info, dict) else {}
        normalized_reason = str(completion_reason or payload.get("completion_reason") or "").strip().lower()
        world_effect = self.last_world_effect_verification if isinstance(self.last_world_effect_verification, dict) else {}
        critic_result = self.last_critic_result if isinstance(self.last_critic_result, dict) else {}
        active_countermeasure = self._active_countermeasure()
        boundary = {
            "healthy": False,
            "scope": "task",
            "domain": "unknown",
            "layer": "task_loop",
            "reason_code": normalized_reason or "unknown",
            "reason": str(critique or payload.get("critique") or payload.get("error") or "").strip(),
            "recommended_action": "inspect_task_state",
            "requires_runtime_restart": False,
            "requires_replan": False,
            "countermeasure": copy.deepcopy(active_countermeasure) if active_countermeasure else None,
        }
        if success or normalized_reason in {"critic_success", "world_effect_verified", "preflight_success"}:
            boundary.update(
                healthy=True,
                domain="healthy",
                layer="none",
                reason_code="success",
                reason="task completed successfully",
                recommended_action="continue",
            )
            return boundary
        if normalized_reason == "death_recovery_required":
            boundary.update(
                domain="world_danger",
                layer="minecraft_world",
                reason_code="death_interrupt",
                reason=str(critique or "Bot died during task execution."),
                recommended_action="run_world_recovery",
                requires_replan=True,
            )
            return boundary
        if normalized_reason == "runtime_exception":
            boundary.update(
                scope="runtime",
                domain="runner_runtime",
                layer="runner",
                reason_code="runtime_exception",
                reason=str(payload.get("error") or critique or "Runtime exception during rollout."),
                recommended_action="soft_reset_then_restart_runner_if_needed",
                requires_runtime_restart=True,
                requires_replan=True,
            )
            return boundary
        if normalized_reason == "action_generation_failed":
            boundary.update(
                domain="action_generation",
                layer="action_llm",
                reason_code="action_generation_failed",
                reason=str(payload.get("error") or critique or "Action generation backend failed."),
                recommended_action="repair_action_backend_or_retry_generation",
            )
            return boundary
        if normalized_reason == "action_parse_failed":
            boundary.update(
                domain="action_contract",
                layer="action_llm",
                reason_code="action_parse_failed",
                reason=str(critique or "Generated action output did not satisfy the execution contract."),
                recommended_action="repair_action_contract_or_retry_generation",
            )
            return boundary
        if isinstance(world_effect, dict) and str(world_effect.get("outcome") or "").lower() == "fail":
            boundary.update(
                domain="task_world_effect",
                layer="world_effect_verifier",
                reason_code=str(world_effect.get("reason_code") or normalized_reason or "world_effect_failed"),
                reason=str(world_effect.get("summary") or critique or "Expected world effect was not observed."),
                recommended_action="retry_or_replan_step",
                requires_replan=normalized_reason == "max_retries_exhausted",
            )
            return boundary
        if isinstance(critic_result, dict) and not bool(critic_result.get("success")):
            critic_reason = str(critic_result.get("reason_code") or normalized_reason or "critic_failure")
            critic_domain = "critic_judgment" if critic_reason.startswith("critic_") else "task_local_execution"
            critic_layer = "critic"
            recommended_action = "retry_with_better_state"
            requires_replan = False
            if critic_reason == "safety_override":
                critic_domain = "world_danger"
                critic_layer = "minecraft_world"
                recommended_action = "run_world_recovery"
                requires_replan = True
            elif normalized_reason == "max_retries_exhausted":
                recommended_action = "replan_task"
                requires_replan = True
            boundary.update(
                domain=critic_domain,
                layer=critic_layer,
                reason_code=critic_reason,
                reason=str(critic_result.get("critique") or critique or "Critic rejected the step result."),
                recommended_action=recommended_action,
                requires_replan=requires_replan,
            )
            return boundary
        if normalized_reason == "max_retries_exhausted":
            boundary.update(
                domain="retry_budget",
                layer="task_loop",
                reason_code="max_retries_exhausted",
                reason=str(critique or "Task exhausted its retry budget."),
                recommended_action="replan_task",
                requires_replan=True,
            )
            return boundary
        if normalized_reason == "retrying":
            boundary.update(
                domain="task_local_execution",
                layer="task_loop",
                reason_code="retrying",
                reason=str(critique or "Step did not complete yet and will retry."),
                recommended_action="retry_same_step",
            )
            return boundary
        return boundary

    def _apply_recovery_boundary(self, boundary):
        if not isinstance(boundary, dict):
            self.last_recovery_boundary = None
            return None
        self.last_recovery_boundary = copy.deepcopy(boundary)
        self._set_task_bookkeeping(
            recovery_scope=boundary.get("scope"),
            recovery_domain=boundary.get("domain"),
            recovery_layer=boundary.get("layer"),
            recovery_reason_code=boundary.get("reason_code"),
            recovery_action=boundary.get("recommended_action"),
        )
        return boundary

    def _set_phase(self, phase, **updates):
        self.last_phase = phase
        self.last_phase_at = time.time()
        for key, value in updates.items():
            setattr(self, key, value)

    def _ensure_execution_session(self, *, mode, bootstrap_reset):
        session = self.execution_session if isinstance(self.execution_session, dict) else None
        if session and session.get("mode") == mode:
            return session
        self.execution_session = {
            "id": self._next_trace_id("session"),
            "mode": mode,
            "bootstrap_reset": bootstrap_reset,
            "bootstrap_resets": 1 if bootstrap_reset else 0,
            "task_turnovers": 0,
            "started_at": time.time(),
            "last_task": None,
            "last_context": "",
            "reset_count": 0,
            "recovery_reset_count": 0,
            "normal_bootstrap_reset_count": 1 if bootstrap_reset else 0,
            "unexpected_reset_count": 0,
            "last_reset_cause": "session_bootstrap" if bootstrap_reset else None,
            "last_reset_mode": bootstrap_reset,
        }
        return self.execution_session

    def _record_reset_audit(self, *, cause, mode, allowed, detail=None):
        if not isinstance(getattr(self, "reset_audit_log", None), list):
            self.reset_audit_log = []
        entry = {
            "cause": cause,
            "mode": mode,
            "allowed": bool(allowed),
            "detail": detail,
            "phase": self.last_phase,
            "task": getattr(self, "task", None),
            "recorded_at": time.time(),
        }
        self.reset_audit_log.append(entry)
        self.reset_audit_log = self.reset_audit_log[-20:]
        if isinstance(self.execution_session, dict):
            self.execution_session["reset_count"] = int(self.execution_session.get("reset_count", 0) or 0) + 1
            self.execution_session["last_reset_cause"] = cause
            self.execution_session["last_reset_mode"] = mode
            if cause == "recovery":
                self.execution_session["recovery_reset_count"] = int(self.execution_session.get("recovery_reset_count", 0) or 0) + 1
            elif cause == "session_bootstrap":
                self.execution_session["normal_bootstrap_reset_count"] = int(self.execution_session.get("normal_bootstrap_reset_count", 0) or 0) + 1
            else:
                self.execution_session["unexpected_reset_count"] = int(self.execution_session.get("unexpected_reset_count", 0) or 0) + 1
            self.execution_session["updated_at"] = time.time()
        return entry

    def _guarded_env_reset(self, *, cause, mode, detail=None, inventory=None, equipment=None, position=None):
        allowed = cause in {"session_bootstrap", "recovery", "manual_reset"}
        audit = self._record_reset_audit(
            cause=cause,
            mode=mode,
            allowed=allowed,
            detail=detail,
        )
        if not allowed:
            self._set_phase("reset_policy_violation", last_critique=f"Unexpected env.reset cause: {cause}")
            raise RuntimeError(f"Reset policy violation: unexpected cause '{cause}'")
        options = {
            "mode": mode,
            "wait_ticks": self.env_wait_ticks,
        }
        if isinstance(inventory, dict):
            options["inventory"] = inventory
        if isinstance(equipment, list):
            options["equipment"] = equipment
        if isinstance(position, dict):
            options["position"] = position
        result = self.env.reset(options=options)
        latest = self.reset_audit_log[-1] if self.reset_audit_log else audit
        if isinstance(latest, dict):
            latest["result"] = "ok"
        return result

    def _activate_task(self, task, context="", *, reset_mode=None):
        self.action_agent_rollout_num_iter = 0
        if hasattr(self.curriculum_agent, "set_objective_template"):
            self.curriculum_agent.set_objective_template(task, task)
        if hasattr(self.curriculum_agent, "task_contract_policy"):
            enforced_task, enforced_context, decision = self.curriculum_agent.task_contract_policy.enforce_task_choice(
                task,
                context,
                events=self.last_events,
            )
            if isinstance(decision, dict):
                setattr(self.curriculum_agent, "last_task_contract_decision", copy.deepcopy(decision))
                self.last_task_contract_decision = copy.deepcopy(decision)
            if str(enforced_task or "").strip() != str(task or "").strip():
                print(
                    f"\033[35mRuntime task contract adjusted '{task}' to '{enforced_task}' before task activation.\033[0m"
                )
            task = enforced_task
            context = enforced_context
        self.task = task
        context = self._augment_context_with_countermeasure(context)
        self.context = context
        self.current_rollout_started_at = time.time()
        start_phase = "reset_start" if reset_mode else "task_session_start"
        observation_phase = "reset_observation_ready" if reset_mode else "task_observation_ready"
        self._set_phase(
            start_phase,
            last_action_program_name=None,
            last_action_code=None,
            last_ai_response_preview=None,
            last_env_event_count=None,
            last_critique=None,
            last_world_effect_verification=None,
            last_critic_result=None,
            last_recovery_boundary=None,
        )
        self.last_rollout_info = None
        if reset_mode:
            self._guarded_env_reset(
                cause="manual_reset",
                mode=reset_mode,
                detail="explicit reset() or rollout(reset_env=True) requested",
            )
        difficulty = (
            "easy" if len(self.curriculum_agent.completed_tasks) > 15 else "peaceful"
        )
        events = self.env.step(
            "bot.chat(\`/time set \${getNextTime()}\`);\n"
            + f"bot.chat('/difficulty {difficulty}');"
        )
        self._set_phase(observation_phase, last_env_event_count=len(events))
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
        observation_id = self._observation_id_from_events(events)
        self._start_task_bookkeeping(task=self.task, context=context, observation_id_before=observation_id)
        self._set_task_bookkeeping(status="running", verification_state="pending_action")
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
        if isinstance(self.execution_session, dict):
            self.execution_session["task_turnovers"] = int(self.execution_session.get("task_turnovers", 0) or 0) + 1
            self.execution_session["last_task"] = self.task
            self.execution_session["last_context"] = context
        return self.messages

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
        hazard = classify_death_event(death_event)
        category = str(hazard.get("category") or "general")
        guidance = [
            "Recover in place instead of rushing back into the same hazard.",
            "Take the shortest safe action that restores stability before continuing the task.",
        ]
        skill_hint = "Prefer safer, shorter actions until stability is restored."
        if category == "drowning":
            guidance = [
                "Treat water as an active hazard until back on solid ground.",
                "Surface immediately when submerged and abort underwater work if air is limited.",
                "Prefer shoreline or shallow-water routes over direct deep-water travel.",
                "Use recoverToSurface-style behavior before resuming normal search or gathering.",
            ]
            skill_hint = "Add a water-safety guard: leave water quickly, avoid long underwater pathing, prefer dry access routes, and recover to the surface before resuming search."
        elif category == "lava_fire":
            guidance = [
                "Back away from exposed lava or fire before mining or fighting.",
                "Do not dig or bridge directly over suspected lava pockets without confirming footing.",
                "Choose routes with solid escape space instead of tight ledges.",
            ]
            skill_hint = "Add a lava-safety guard: keep distance from lava, avoid unstable ledges, and secure the floor before continuing."
        elif category == "fall":
            guidance = [
                "Assume the terrain is unsafe until a flat recovery route is found.",
                "Descend one block at a time and avoid sprinting near drops.",
                "Stabilize on level ground before reattempting gathering or travel.",
            ]
            skill_hint = "Add a fall-safety guard: favor flat movement, slow descents, and avoid cliff-edge pathing."
        elif category == "starvation":
            guidance = [
                "Secure food before any optional travel, mining, or combat.",
                "Keep a reserve instead of consuming the last food item too late.",
            ]
            skill_hint = "Add a food-safety guard: do not start long tasks without edible food or an immediate food plan."
        elif category == "hostile":
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
            execution_status = str(search_execution.get("status") or "").lower()
            if execution_status == "success" and not success:
                failure_reason = completion_reason or "task_failed_after_search_success"
            else:
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
        return self._activate_task(
            task,
            context=context,
            reset_mode="soft" if reset_env else None,
        )

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
            self._set_task_bookkeeping(
                effect_check_id=self._next_trace_id("effect"),
                observation_id_after=self._observation_id_from_events(self.last_events),
                status="completed",
                done_reason="preflight_success",
                success=True,
                verification_state="preflight_success",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
            self._apply_recovery_boundary(
                self._classify_recovery_boundary(
                    completion_reason="preflight_success",
                    success=True,
                    critique=critique,
                    info=info,
                )
            )
            info["task_bookkeeping"] = self._bookkeeping_snapshot()
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
        try:
            ai_message = self.action_agent.llm(self.messages)
        except Exception as exc:
            latest_payload = self._latest_event_payload(self.last_events)
            info = {
                "task": self.task,
                "success": False,
                "conversations": self.conversations,
                "critique": str(exc),
                "done": True,
                "rollout_iteration": self.action_agent_rollout_num_iter,
                "max_rollout_iterations": self.action_agent_task_max_retries,
                "completion_reason": "action_generation_failed",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "last_position": self._event_position(latest_payload),
            }
            self._set_task_bookkeeping(
                status="failed",
                done_reason="action_generation_failed",
                success=False,
                verification_state="llm_request_failed",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
            self._apply_recovery_boundary(
                self._classify_recovery_boundary(
                    completion_reason="action_generation_failed",
                    success=False,
                    critique=str(exc),
                    info=info,
                )
            )
            info["task_bookkeeping"] = self._bookkeeping_snapshot()
            self.last_rollout_info = copy.deepcopy(info)
            self.last_completion_reason = "action_generation_failed"
            self.last_success = False
            self.last_task_result = copy.deepcopy(info)
            self.last_task_result_at = time.time()
            self._set_phase(
                "task_rollout_finished",
                last_critique=str(exc),
                last_completion_reason="action_generation_failed",
                last_success=False,
            )
            return self.messages, 0, True, info
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
            before_events = copy.deepcopy(self.last_events)
            code = parsed_result["program_code"] + "\n" + parsed_result["exec_code"]
            self._set_task_bookkeeping(
                action_id=self._next_trace_id("action"),
                status="running",
                verification_state="action_ready",
                action_path="llm_generated",
                program_name=parsed_result.get("program_name"),
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
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
            observation_id_after = self._observation_id_from_events(events)
            self._set_task_bookkeeping(
                observation_id_after=observation_id_after,
                verification_state="post_action_observation_ready",
            )
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
                self._set_task_bookkeeping(
                    effect_check_id=self._next_trace_id("effect"),
                    status="recovery_required",
                    done_reason="death_recovery_required",
                    success=False,
                    verification_state="death_interrupt",
                )
                self._set_phase("death_recovery_required", last_critique=critique)
            else:
                self._set_phase("effect_verifier_check")
                effect_verification = verify_task_effect(
                    self.task,
                    before_events=before_events,
                    after_events=events,
                )
                self.last_world_effect_verification = copy.deepcopy(effect_verification)
                self.last_critic_result = None
                verifier_outcome = str(effect_verification.get("outcome") or "").lower()
                if verifier_outcome == "success":
                    success = True
                    critique = str(effect_verification.get("summary") or "World-effect verifier confirmed task success.")
                    self.pending_countermeasure = None
                    self._set_task_bookkeeping(
                        effect_check_id=self._next_trace_id("effect"),
                        status="effect_verified",
                        done_reason="world_effect_verified",
                        success=True,
                        verification_state="world_effect_verified",
                    )
                    self._set_phase("effect_verifier_result", last_critique=critique)
                elif verifier_outcome == "fail":
                    success = False
                    critique = str(effect_verification.get("summary") or "World-effect verifier saw no intended world change.")
                    self.last_critic_result = {
                        "outcome": "skipped",
                        "success": False,
                        "reason_code": str(effect_verification.get("reason_code") or "world_effect_failed"),
                        "critique": critique,
                        "source": "world_effect_verifier",
                    }
                    self._set_task_bookkeeping(
                        effect_check_id=self._next_trace_id("effect"),
                        status="running",
                        success=False,
                        verification_state="world_effect_failed",
                    )
                    self._set_phase("effect_verifier_result", last_critique=critique)
                else:
                    self._set_task_bookkeeping(
                        effect_check_id=self._next_trace_id("effect"),
                        status="running",
                        success=False,
                        verification_state="world_effect_unknown_pending_critic",
                    )
                    self._set_phase("critic_check")
                    critic_result = self.critic_agent.check_task_success_result(
                        events=events,
                        task=self.task,
                        context=self.context,
                        chest_observation=self.action_agent.render_chest_observation(),
                        max_retries=5,
                    )
                    self.last_critic_result = copy.deepcopy(critic_result)
                    success = bool(critic_result.get("success"))
                    critique = str(critic_result.get("critique") or "")
                    if success:
                        self.pending_countermeasure = None
                        self._set_task_bookkeeping(
                            status="completed",
                            done_reason=str(critic_result.get("reason_code") or "critic_success"),
                            success=True,
                            verification_state="critic_success_after_verifier",
                        )
                    else:
                        self._set_task_bookkeeping(
                            status="running",
                            success=False,
                            verification_state=str(critic_result.get("reason_code") or "critic_failed_pending_retry"),
                        )
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
            self._set_task_bookkeeping(
                status="running",
                done_reason="action_parse_failed",
                success=False,
                verification_state="action_parse_failed",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
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
            if isinstance(self.last_world_effect_verification, dict) and str(self.last_world_effect_verification.get("outcome") or "").lower() == "success":
                completion_reason = "world_effect_verified"
            else:
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
            "last_position": self._event_position(self._latest_event_payload(self.last_events)),
        }
        if completion_reason == "critic_success":
            self._set_task_bookkeeping(
                status="completed",
                done_reason="critic_success",
                success=True,
                verification_state="critic_success_after_verifier",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
        elif completion_reason == "world_effect_verified":
            self._set_task_bookkeeping(
                status="effect_verified",
                done_reason="world_effect_verified",
                success=True,
                verification_state="world_effect_verified",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
        elif completion_reason == "death_recovery_required":
            self._set_task_bookkeeping(
                status="recovery_required",
                done_reason="death_recovery_required",
                success=False,
                verification_state="death_interrupt",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
        elif completion_reason == "max_retries_exhausted":
            self._set_task_bookkeeping(
                status="failed",
                done_reason="max_retries_exhausted",
                success=False,
                verification_state="retry_budget_exhausted",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
        elif completion_reason == "action_parse_failed":
            self._set_task_bookkeeping(
                status="running",
                done_reason="action_parse_failed",
                success=False,
                verification_state="action_parse_failed",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
        else:
            self._set_task_bookkeeping(
                status="running",
                done_reason="retrying",
                success=False,
                verification_state="retrying",
                rollout_iteration=self.action_agent_rollout_num_iter,
            )
        info["task_bookkeeping"] = self._bookkeeping_snapshot()
        if isinstance(self.last_world_effect_verification, dict):
            info["world_effect_verification"] = copy.deepcopy(self.last_world_effect_verification)
        if isinstance(self.last_critic_result, dict):
            info["critic_result"] = copy.deepcopy(self.last_critic_result)
        recovery_boundary = self._apply_recovery_boundary(
            self._classify_recovery_boundary(
                completion_reason=completion_reason,
                success=bool(success),
                critique=critique,
                info=info,
            )
        )
        if isinstance(recovery_boundary, dict):
            info["recovery_boundary"] = copy.deepcopy(recovery_boundary)
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
        self._activate_task(
            task,
            context=context,
            reset_mode="soft" if reset_env else None,
        )
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
        if completion_reason and completion_reason not in {"critic_success", "world_effect_verified"}:
            return False
        return bool(info.get("program_name") and info.get("program_code"))

    def learn(self, reset_env=True):
        if hasattr(self.curriculum_agent, "set_objective_template"):
            self.curriculum_agent.set_objective_template("progression", "")
        bootstrap_reset = "soft" if self.resume else "hard"
        self._set_phase("persistent_session_bootstrap")
        if self.resume:
            # keep the inventory
            self._guarded_env_reset(
                cause="session_bootstrap",
                mode="soft",
                detail="learn bootstrap resume inventory-preserving reset",
            )
        else:
            # clear the inventory
            self._guarded_env_reset(
                cause="session_bootstrap",
                mode="hard",
                detail="learn bootstrap fresh session reset",
            )
            self.resume = True
        self.last_events = self.env.step("")
        self._sync_search_policy_to_env()
        self._ensure_execution_session(mode="learn", bootstrap_reset=bootstrap_reset)
        self._set_phase("persistent_session_ready")

        while True:
            if self.recorder.iteration > self.max_iterations:
                print("Iteration limit reached")
                break
            self.refresh_live_state(refresh_messages=False)
            self._set_phase("task_selection")
            task, context = self.curriculum_agent.propose_next_task(
                events=self.last_events,
                chest_observation=self.action_agent.render_chest_observation(),
                max_retries=5,
            )
            self.last_inventory_plan = copy.deepcopy(
                getattr(self.curriculum_agent, "last_inventory_plan", None)
            )
            try:
                self.current_speculative_next_task = copy.deepcopy(
                    self.curriculum_agent.prepare_speculative_next_task(task, self.last_events)
                )
                self.last_speculative_decision = copy.deepcopy(
                    getattr(self.curriculum_agent, "last_speculative_decision", None)
                )
            except Exception as speculative_error:
                self.current_speculative_next_task = None
                self.last_speculative_decision = {
                    "phase": "error",
                    "trigger_task": task,
                    "error": str(speculative_error),
                    "created_at": time.time(),
                }
            print(
                f"\033[35mStarting task {task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            try:
                self._set_phase("task_session_turnover")
                messages, reward, done, info = self.rollout(
                    task=task,
                    context=context,
                    reset_env=False,
                )
            except Exception as e:
                time.sleep(3)  # wait for mineflayer to exit
                info = {
                    "task": task,
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "completion_reason": "action_generation_failed" if "codex/action" in str(e).lower() or "codex gateway" in str(e).lower() else "runtime_exception",
                    "last_position": self._event_position(self._latest_event_payload(self.last_events)),
                }
                try:
                    recovery_options = self._build_recovery_reset_options()
                    self.last_events = self._guarded_env_reset(
                        cause="recovery",
                        mode=str(recovery_options.get("mode") or "hard"),
                        detail="learn rollout exception recovery reset",
                        inventory=recovery_options.get("inventory"),
                        equipment=recovery_options.get("equipment"),
                        position=recovery_options.get("position"),
                    )
                except Exception as reset_error:
                    info["reset_error"] = str(reset_error)
                    self.last_events = self._guarded_env_reset(
                        cause="recovery",
                        mode="hard",
                        detail="learn rollout exception hard fallback reset",
                    )
                # use red color background to print the error
                print("Your last round rollout terminated due to error:")
                print(f"\033[41m{e}\033[0m")
                self.last_rollout_info = copy.deepcopy(info)
                self.last_task_result = copy.deepcopy(info)
                self.last_task_result_at = time.time()
                self.last_completion_reason = str(info.get("completion_reason") or "")
                self.last_success = False
                recovery_boundary = self._apply_recovery_boundary(
                    self._classify_recovery_boundary(
                        completion_reason=info.get("completion_reason"),
                        success=False,
                        critique=str(info.get("error") or ""),
                        info=info,
                    )
                )
                if isinstance(recovery_boundary, dict):
                    info["recovery_boundary"] = copy.deepcopy(recovery_boundary)

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
            self.current_speculative_next_task = copy.deepcopy(
                getattr(self.curriculum_agent, "speculative_next_task", None)
            )
            self.last_speculative_decision = copy.deepcopy(
                getattr(self.curriculum_agent, "last_speculative_decision", None)
            )
            self.last_inventory_plan = copy.deepcopy(
                getattr(self.curriculum_agent, "last_inventory_plan", None)
            )
            print(
                f"\033[35mCompleted tasks: {', '.join(self.curriculum_agent.completed_tasks) if self.curriculum_agent.completed_tasks else 'None'}\033[0m"
            )
            print(
                f"\033[35mFailed tasks: {self.curriculum_agent.summarize_failed_tasks()}\033[0m"
            )
            self._set_phase("objective_node_advanced")

        return {
            "completed_tasks": self.curriculum_agent.completed_tasks,
            "failed_tasks": self.curriculum_agent.failed_tasks,
            "skills": self.skill_manager.skills,
        }

    def decompose_task(self, task):
        if not self.last_events:
            self.last_events = self._guarded_env_reset(
                cause="session_bootstrap",
                mode="hard",
                detail="decompose_task bootstrap reset",
            )
        self.refresh_live_state(refresh_messages=False)
        return self.curriculum_agent.decompose_task(task, self.last_events)

    def inference(self, task=None, sub_goals=[], reset_mode="hard", reset_env=True):
        if not task and not sub_goals:
            raise ValueError("Either task or sub_goals must be provided")
        if hasattr(self.curriculum_agent, "set_objective_template"):
            objective_seed = task if task else " ".join(str(goal or "") for goal in sub_goals)
            self.curriculum_agent.set_objective_template(objective_seed, objective_seed)
        self._guarded_env_reset(
            cause="session_bootstrap",
            mode=reset_mode,
            detail="inference bootstrap reset",
        )
        self.curriculum_agent.completed_tasks = []
        self.curriculum_agent.failed_tasks = []
        self.last_events = self.env.step("")
        self._sync_search_policy_to_env()
        self._ensure_execution_session(mode="inference", bootstrap_reset=reset_mode if reset_env else None)
        self._set_phase("persistent_session_ready")
        self.refresh_live_state(refresh_messages=False)
        if not sub_goals:
            sub_goals = self.curriculum_agent.decompose_task(task, self.last_events)
        task_results = []
        subgoal_index = 0
        while subgoal_index < len(sub_goals):
            next_task = sub_goals[subgoal_index]
            context = self.curriculum_agent.get_task_context(next_task)
            self._set_phase("task_selection")
            print(
                f"\033[35mStarting task {next_task} for at most {self.action_agent_task_max_retries} times\033[0m"
            )
            try:
                self._set_phase("task_session_turnover")
                messages, reward, done, info = self.rollout(
                    task=next_task,
                    context=context,
                    reset_env=False,
                )
            except Exception as e:
                time.sleep(3)  # wait for mineflayer to exit
                info = {
                    "task": next_task,
                    "success": False,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "completion_reason": "action_generation_failed" if "codex/action" in str(e).lower() or "codex gateway" in str(e).lower() else "runtime_exception",
                    "last_position": self._event_position(self._latest_event_payload(self.last_events)),
                }
                try:
                    if self.last_events:
                        recovery_options = self._build_recovery_reset_options()
                        self.last_events = self._guarded_env_reset(
                            cause="recovery",
                            mode=str(recovery_options.get("mode") or "hard"),
                            detail="inference rollout exception recovery reset",
                            inventory=recovery_options.get("inventory"),
                            equipment=recovery_options.get("equipment"),
                            position=recovery_options.get("position"),
                        )
                    else:
                        self.last_events = self._guarded_env_reset(
                            cause="recovery",
                            mode="hard",
                            detail="inference rollout exception hard recovery reset",
                        )
                except Exception as reset_error:
                    info["reset_error"] = str(reset_error)
                    self.last_events = self._guarded_env_reset(
                        cause="recovery",
                        mode="hard",
                        detail="inference rollout exception hard fallback reset",
                    )
                print("Your last round rollout terminated due to error:")
                print(f"\033[41m{e}\033[0m")
                self.last_rollout_info = copy.deepcopy(info)
                self.last_task_result = copy.deepcopy(info)
                self.last_task_result_at = time.time()
                self.last_completion_reason = str(info.get("completion_reason") or "")
                self.last_success = False
                recovery_boundary = self._apply_recovery_boundary(
                    self._classify_recovery_boundary(
                        completion_reason=info.get("completion_reason"),
                        success=False,
                        critique=str(info.get("error") or ""),
                        info=info,
                    )
                )
                if isinstance(recovery_boundary, dict):
                    info["recovery_boundary"] = copy.deepcopy(recovery_boundary)
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
