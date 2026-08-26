from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import secrets
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .autonomy_authorization import (
    ASSISTANT_AUTONOMY_ACTIONS,
    MINECRAFT_AUTONOMY_ACTIONS,
)
from .autonomy_failure_contract import (
    AUTONOMY_CYCLE_FAILED,
    AUTONOMY_EXECUTOR_EXECUTE_FAILED,
    autonomy_failure_payload,
    autonomy_last_error,
    sanitize_autonomy_observation,
    sanitize_autonomy_step_result,
)
from .autonomy_outcome_evidence import autonomy_outcome_verified
from .memory import cognitive_state_path, read_json_file, write_json_file
from .memory_deletion_journal import MemoryDeletionJournalIntegrityError
from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
    reset_memory_exposure_position,
)
from .minecraft_threat import has_interrupting_threat, has_survival_threat, highest_threat_score, threat_count, threat_distance
from .paths import get_repo_root
from .self_model import update_self_state_from_observation
from .text import clean_text


MC_DEBUG_LOG_PATH = get_repo_root() / "minecraft_debug.log"


def _mc_log(prefix: str, payload: Any) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    except Exception:
        body = repr(payload)
    file_line = f"{stamp} {prefix} {body}"
    try:
        with MC_DEBUG_LOG_PATH.open("a", encoding="utf-8") as fp:
            fp.write(file_line + "\n")
    except Exception:
        pass


def assistant_proactive_impulse_text(impulse: str, fallback: str = "") -> str:
    text = {
        "check_softly": "정훈, 뭔가 상태가 조금 이상해 보여서 살짝 확인해봤어.",
        "comment_on_screen_change": "정훈, 화면이 바뀐 것 같아. 필요하면 내가 보고 같이 정리해줄게.",
        "ask_light_question": "정훈, 지금 화면 쪽으로 뭔가 이어서 볼까?",
        "suggest_next_step": "정훈, 방금 흐름에서 다음에 확인할 걸 같이 잡아볼까?",
    }.get(clean_text(impulse), clean_text(fallback))
    return text or "정훈, 필요하면 바로 이어서 볼게."


@dataclass(frozen=True)
class AutonomyExecutionContext:
    guild_id: int
    action_key: str
    action_run_id: str
    authorization_grant_id: str


class AutonomyExecutor(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def observe(self) -> dict[str, Any]: ...
    async def execute_step(
        self,
        step: dict[str, Any],
        *,
        context: AutonomyExecutionContext | None = None,
    ) -> dict[str, Any]: ...


@dataclass
class AutonomyNeed:
    kind: str
    priority: float
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutonomyGoal:
    kind: str
    summary: str
    priority: float
    status: str = "pending"
    source_need: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutonomyPlan:
    goal_kind: str
    summary: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    cursor: int = 0
    updated_at: float = field(default_factory=time.time)


@dataclass
class AutonomyRuntimeState:
    enabled: bool = False
    status: str = "idle"
    safety_mode: str = "constrained"
    allowed_actions: list[str] = field(default_factory=list)
    last_observation: dict[str, Any] = field(default_factory=dict)
    current_goal: AutonomyGoal | None = None
    current_plan: AutonomyPlan | None = None
    last_step_result: dict[str, Any] = field(default_factory=dict)
    last_router_refresh_result: dict[str, Any] = field(default_factory=dict)
    drive_state: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    failure_count: int = 0
    updated_at: float = field(default_factory=time.time)


@dataclass
class AutonomyCycleResult:
    observation: dict[str, Any]
    needs: list[AutonomyNeed]
    selected_goal: AutonomyGoal | None
    planned: AutonomyPlan | None
    step_result: dict[str, Any] | None
    state: AutonomyRuntimeState


class AutonomyEngine:
    @staticmethod
    def default_allowed_actions() -> list[str]:
        return [
            *ASSISTANT_AUTONOMY_ACTIONS,
            *MINECRAFT_AUTONOMY_ACTIONS,
        ]

    def __init__(
        self,
        *,
        guild_id: int,
        executor: AutonomyExecutor,
        notify: Callable[..., Awaitable[None]] | None = None,
        poll_interval_sec: float = 4.0,
        get_authorized_actions: Callable[[int], list[str]] | None = None,
        authorize_action: Callable[..., dict[str, Any]] | None = None,
        record_action_outcome: Callable[
            [int, str, dict[str, Any]],
            dict[str, bool] | bool | None,
        ]
        | None = None,
        memory_index_dir: Path | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.executor = executor
        self.notify = notify
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self.get_authorized_actions = get_authorized_actions
        self.authorize_action = authorize_action
        self.record_action_outcome = record_action_outcome
        self.memory_index_dir = (
            Path(memory_index_dir)
            if memory_index_dir is not None
            else None
        )
        self.state = AutonomyRuntimeState()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._executor_connected = False
        self._blocked_counts: dict[str, int] = defaultdict(int)
        self._failure_notification_attempted = False
        self._failure_notification_action_run_id = ""

    async def _notify_failure_once(self, code: str) -> None:
        if self._failure_notification_attempted:
            return
        self._failure_notification_attempted = True
        self._failure_notification_action_run_id = secrets.token_hex(12)
        if self.notify is None:
            return
        text = f"[자율봇] 오류: {code}"
        try:
            inspect.signature(self.notify).bind(
                text,
                action_run_id=self._failure_notification_action_run_id,
            )
        except (TypeError, ValueError):
            await self.notify(text)
            return
        await self.notify(
            text,
            action_run_id=self._failure_notification_action_run_id,
        )

    def _reset_failure_notification_episode(self) -> None:
        self._failure_notification_attempted = False
        self._failure_notification_action_run_id = ""

    @property
    def memory_scope_key(self) -> str:
        return "autonomy"

    def load_persisted_state(self) -> None:
        saved = read_json_file(cognitive_state_path(self.guild_id, scope_type="system", scope_key=self.memory_scope_key))
        runtime = saved.get("autonomy_runtime") if isinstance(saved, dict) else None
        self.state = AutonomyRuntimeState()
        self._blocked_counts.clear()
        if not isinstance(runtime, dict):
            self.persist_state()
            return
        was_enabled = runtime.get("enabled") is True
        safety_mode = runtime.get("safety_mode")
        if safety_mode not in {"constrained", "normal"}:
            safety_mode = "constrained"
        failure_count = runtime.get("failure_count")
        if (
            type(failure_count) is not int
            or not 0 <= failure_count <= 1_000_000
        ):
            failure_count = 0
        self.state = AutonomyRuntimeState(
            enabled=False,
            status=(
                "authorization_required"
                if was_enabled
                else "idle"
            ),
            safety_mode=safety_mode,
            allowed_actions=[],
            # History-derived runtime caches are not receipt-bound.
            last_observation={},
            current_goal=None,
            current_plan=None,
            last_step_result={},
            last_router_refresh_result={},
            drive_state={},
            last_error=autonomy_last_error(
                runtime.get("last_error")
            ),
            failure_count=failure_count,
            updated_at=time.time(),
        )
        self.persist_state()

    def persist_state(self) -> None:
        path = cognitive_state_path(self.guild_id, scope_type="system", scope_key=self.memory_scope_key)
        payload = read_json_file(path)
        self.state.last_observation = sanitize_autonomy_observation(
            self.state.last_observation
        )
        self.state.last_step_result = sanitize_autonomy_step_result(
            self.state.last_step_result
        )
        self.state.last_router_refresh_result = (
            sanitize_autonomy_step_result(
                self.state.last_router_refresh_result
            )
        )
        self.state.last_error = autonomy_last_error(
            self.state.last_error
        )
        payload["autonomy_runtime"] = {
            "enabled": self.state.enabled,
            "status": self.state.status,
            "safety_mode": self.state.safety_mode,
            "allowed_actions": list(self.state.allowed_actions),
            "last_observation": self.state.last_observation,
            "current_goal": asdict(self.state.current_goal) if self.state.current_goal else None,
            "current_plan": asdict(self.state.current_plan) if self.state.current_plan else None,
            "last_step_result": self.state.last_step_result,
            "last_router_refresh_result": self.state.last_router_refresh_result,
            "drive_state": self.state.drive_state,
            "last_error": self.state.last_error,
            "failure_count": self.state.failure_count,
            "updated_at": self.state.updated_at,
        }
        write_json_file(path, payload)

    async def start(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> bool:
        async with self._lock:
            if self._task is not None and not self._task.done():
                if self.state.enabled:
                    return True
                task = self._task
                self._task = None
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
            if not self.state.enabled and self._executor_connected:
                await self._disconnect_executor_once()
            self.load_persisted_state()
            authorized_actions = self._current_authorized_actions()
            if not authorized_actions:
                self.state.enabled = False
                self.state.status = "authorization_required"
                self.state.allowed_actions = []
                self.state.updated_at = time.time()
                self.persist_state()
                raise PermissionError("autonomy_authorization_required")
            await self._connect_executor_once()
            try:
                start_is_current = (
                    is_current is None or bool(is_current())
                )
            except Exception:
                start_is_current = False
            if not start_is_current:
                await self._disconnect_executor_once()
                return False
            authorized_actions = self._current_authorized_actions()
            if not authorized_actions:
                await self._disconnect_executor_once()
                self.state.enabled = False
                self.state.status = "authorization_required"
                self.state.allowed_actions = []
                self.state.updated_at = time.time()
                self.persist_state()
                raise PermissionError("autonomy_authorization_required")
            self.state.enabled = True
            self.state.status = "running"
            self.state.allowed_actions = list(dict.fromkeys(authorized_actions))
            self.state.updated_at = time.time()
            self.persist_state()
            self._task = asyncio.create_task(self._run_loop())
            return True

    def _current_authorized_actions(self) -> list[str]:
        authorized_actions = (
            self.get_authorized_actions(self.guild_id)
            if self.get_authorized_actions is not None
            else []
        )
        supported = set(self.default_allowed_actions())
        return [
            action
            for action in authorized_actions
            if action in supported
        ]

    async def stop(self) -> None:
        async with self._lock:
            self.state.enabled = False
            self.state.status = "stopping"
            self.state.allowed_actions = []
            self.state.updated_at = time.time()
            self.persist_state()
            task = self._task
            self._task = None
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    current_task = asyncio.current_task()
                    if current_task is not None and current_task.cancelling():
                        raise
            await self._disconnect_executor_once()
            self.state.status = "idle"
            self.state.updated_at = time.time()
            self.persist_state()

    async def _connect_executor_once(self) -> None:
        if self._executor_connected:
            return
        await self.executor.connect()
        self._executor_connected = True

    async def _disconnect_executor_once(self) -> None:
        if not self._executor_connected:
            return
        await self.executor.disconnect()
        self._executor_connected = False

    async def _run_loop(self) -> None:
        try:
            while self.state.enabled:
                try:
                    cycle_result = await self.run_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.state.last_error = AUTONOMY_CYCLE_FAILED
                    self.state.failure_count += 1
                    self.state.status = "error"
                    self.state.updated_at = time.time()
                    self.persist_state()
                    with contextlib.suppress(Exception):
                        await self._notify_failure_once(
                            AUTONOMY_CYCLE_FAILED
                        )
                    await asyncio.sleep(self.poll_interval_sec)
                    continue
                if cycle_result.state.status != "error":
                    self._reset_failure_notification_episode()
                await asyncio.sleep(self.next_poll_delay(cycle_result))
        finally:
            if not self.state.enabled:
                await self._disconnect_executor_once()

    def next_poll_delay(self, cycle_result: AutonomyCycleResult) -> float:
        observation = cycle_result.observation if isinstance(cycle_result.observation, dict) else {}
        active_environment = clean_text(str(observation.get("active_environment") or observation.get("environment") or "assistant")) or "assistant"
        step_result = cycle_result.step_result if isinstance(cycle_result.step_result, dict) else {}
        step_reason = clean_text(str(step_result.get("reason", "")))
        if active_environment == "minecraft":
            if cycle_result.selected_goal and cycle_result.selected_goal.kind == "survive":
                return 0.5
            if step_reason in {"hostile_interrupt", "hazard_interrupt", "low_health_interrupt", "plan_complete"}:
                return 0.5
            return min(self.poll_interval_sec, 1.0)
        return self.poll_interval_sec

    def should_continue_plan(self, goal: AutonomyGoal | None) -> bool:
        plan = self.state.current_plan
        current_goal = self.state.current_goal
        if goal is None or plan is None or current_goal is None:
            return False
        if plan.cursor >= len(plan.steps):
            return False
        if plan.goal_kind != goal.kind or current_goal.kind != goal.kind:
            return False
        current_domain = clean_text(str(current_goal.metadata.get("domain", "assistant"))) or "assistant"
        next_domain = clean_text(str(goal.metadata.get("domain", "assistant"))) or "assistant"
        if current_domain != next_domain:
            return False
        if goal.kind == "progress":
            current_phase = clean_text(str(current_goal.metadata.get("progress_phase", "")))
            next_phase = clean_text(str(goal.metadata.get("progress_phase", "")))
            return current_phase == next_phase
        return True

    async def run_cycle(self) -> AutonomyCycleResult:
        reset_memory_exposure_position()
        try:
            observation = await self.observe()
            exposure = current_memory_exposure_position()
            if exposure is None:
                return await self._run_cycle_with_observation(
                    observation
                )
            if self.memory_index_dir is None:
                raise MemoryDeletionJournalIntegrityError()
            with memory_exposure_guard(
                expected_position=exposure,
                required=True,
                index_dir=self.memory_index_dir,
            ):
                return await self._run_cycle_with_observation(
                    observation
                )
        finally:
            reset_memory_exposure_position()

    async def _run_cycle_with_observation(
        self,
        observation: dict[str, Any],
    ) -> AutonomyCycleResult:
        self_state = update_self_state_from_observation(observation)
        self.state.drive_state = asdict(self_state)
        observation["self_state"] = dict(self.state.drive_state)
        needs = self.derive_needs(observation)
        selected_goal = self.select_goal(needs)
        planned = self.state.current_plan if self.should_continue_plan(selected_goal) else self.plan_goal(selected_goal, observation)
        active_environment = clean_text(
            str(
                observation.get("active_environment")
                or observation.get("environment")
                or "assistant"
            )
        ) or "assistant"
        if active_environment == "minecraft":
            if selected_goal is not None:
                _mc_log("[MC GOAL]", asdict(selected_goal))
            if planned is not None:
                _mc_log("[MC PLAN]", asdict(planned))
        step_result = await self.execute_next_step(planned)
        post_effect_cancellation = bool(
            isinstance(step_result, dict)
            and step_result.get("_post_effect_cancellation") is True
        )
        post_effect_integrity_failure = bool(
            isinstance(step_result, dict)
            and step_result.get("_post_effect_integrity_failure") is True
        )
        memory_bound = current_memory_exposure_position() is not None
        history_derived = memory_bound and active_environment != "minecraft"
        if isinstance(step_result, dict) and not history_derived:
            step = step_result.get("step") if isinstance(step_result.get("step"), dict) else None
            action_key = self._action_key(step) if step else ""
            interrupt_reason = clean_text(str(step_result.get("reason", "")))
            if step_result.get("status") == "blocked" and action_key:
                if interrupt_reason in {"followup_reply_slot_busy", "hazard_interrupt", "hostile_interrupt", "low_health_interrupt", "no_followup_channel", "shield_not_in_inventory", "no_food_in_inventory", "no_food_source_detected"}:
                    self._blocked_counts.pop(action_key, None)
                else:
                    self._blocked_counts[action_key] += 1
            elif action_key and step_result.get("status") in {"ok", "done", "completed"}:
                self._blocked_counts.pop(action_key, None)
        if self.should_replan(step_result):
            planned = self.replan_goal(selected_goal, observation, step_result)
        safe_observation = sanitize_autonomy_observation(observation)
        safe_step_result = (
            {}
            if history_derived
            else sanitize_autonomy_step_result(step_result)
        )
        self.state.last_observation = safe_observation
        self.state.current_goal = None if history_derived else selected_goal
        self.state.current_plan = None if history_derived else planned
        self.state.last_step_result = safe_step_result
        if history_derived:
            self.state.last_router_refresh_result = {}
        authorization_blocked = bool(
            isinstance(step_result, dict)
            and step_result.get("reason")
            in {
                "authorization_required",
                "authorization_scope_denied",
                "authorization_action_unsupported",
                "authorization_audit_unavailable",
                "authorization_changed_during_action",
            }
        )
        executor_failed = bool(
            isinstance(step_result, dict)
            and step_result.get("reason")
            == AUTONOMY_EXECUTOR_EXECUTE_FAILED
        )
        if executor_failed:
            self.state.last_error = AUTONOMY_EXECUTOR_EXECUTE_FAILED
            self.state.failure_count += 1
        self.state.status = (
            "authorization_required"
            if authorization_blocked
            else (
                "error"
                if executor_failed
                else ("running" if self.state.enabled else "idle")
            )
        )
        self.state.updated_at = time.time()
        self.persist_state()
        if executor_failed:
            with contextlib.suppress(Exception):
                await self._notify_failure_once(
                    AUTONOMY_EXECUTOR_EXECUTE_FAILED
                )
        if post_effect_integrity_failure:
            raise MemoryDeletionJournalIntegrityError()
        if post_effect_cancellation:
            raise asyncio.CancelledError()
        return AutonomyCycleResult(
            observation=safe_observation,
            needs=[] if history_derived else needs,
            selected_goal=None if history_derived else selected_goal,
            planned=None if history_derived else planned,
            step_result=None if history_derived else safe_step_result,
            state=self.state,
        )

    async def observe(self) -> dict[str, Any]:
        observed = await self.executor.observe()
        return observed if isinstance(observed, dict) else {"raw": observed}

    def _action_key(self, step: dict[str, Any] | None) -> str:
        if not isinstance(step, dict):
            return ""
        domain = clean_text(str(step.get("domain", "assistant"))) or "assistant"
        action = clean_text(str(step.get("action", "")))
        return f"{domain}:{action}" if action else ""

    def _minecraft_inventory_count(self, inventory: dict[str, Any], predicate: Callable[[str], bool]) -> int:
        total = 0
        for key, value in inventory.items():
            if isinstance(key, str) and predicate(key):
                try:
                    total += int(value or 0)
                except Exception:
                    continue
        return total

    def _minecraft_food_item_count(self, inventory: dict[str, Any]) -> int:
        food_keywords = ("apple", "bread", "beef", "porkchop", "mutton", "chicken", "rabbit", "cod", "salmon", "potato", "carrot", "berry", "melon", "cookie", "stew")
        return self._minecraft_inventory_count(inventory, lambda name: any(keyword in name for keyword in food_keywords))

    def _minecraft_progress_phase(self, minecraft_obs: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        inventory = minecraft_obs.get("inventory") if isinstance(minecraft_obs.get("inventory"), dict) else {}
        hunger = float(minecraft_obs.get("hunger", 20) or 20)
        health = float(minecraft_obs.get("health", 20) or 20)
        log_count = self._minecraft_inventory_count(inventory, lambda name: name.endswith("_log"))
        plank_count = self._minecraft_inventory_count(inventory, lambda name: name.endswith("_planks"))
        stick_count = int(inventory.get("stick", 0) or 0)
        stone_materials = self._minecraft_inventory_count(inventory, lambda name: name in {"cobblestone", "cobbled_deepslate", "blackstone", "stone", "deepslate"})
        coal_count = self._minecraft_inventory_count(inventory, lambda name: name in {"coal", "charcoal"})
        torch_count = int(inventory.get("torch", 0) or 0)
        furnace_count = int(inventory.get("furnace", 0) or 0)
        food_count = self._minecraft_food_item_count(inventory)
        raw_food_count = self._minecraft_inventory_count(inventory, lambda name: name in {"raw_beef", "raw_porkchop", "raw_mutton", "raw_chicken", "raw_rabbit", "raw_cod", "raw_salmon", "potato"})
        metadata = {
            **inventory,
            "domain": "minecraft",
            "health": health,
            "hunger": hunger,
            "log_count": log_count,
            "plank_count": plank_count,
            "stick_count": stick_count,
            "stone_materials": stone_materials,
            "coal_count": coal_count,
            "torch_count": torch_count,
            "furnace_count": furnace_count,
            "food_count": food_count,
            "raw_food_count": raw_food_count,
        }
        if not inventory.get("wooden_pickaxe") or not inventory.get("wooden_axe"):
            return "wood_tools", "나무 도구를 만든다", metadata
        if not inventory.get("stone_pickaxe") or not inventory.get("stone_axe"):
            return "stone_tools", "돌 도구로 업그레이드한다", metadata
        if hunger <= 16 and food_count <= 0:
            return "food_buffer", "먹을 것을 확보한다", metadata
        if torch_count < 8 and coal_count <= 0:
            return "coal_torches", "석탄과 횃불을 확보한다", metadata
        if furnace_count <= 0:
            return "furnace", "화로를 확보한다", metadata
        if raw_food_count > 0 and food_count <= 0:
            return "cook_food", "음식을 익혀서 비축한다", metadata
        return "free_gather", "기본 자원을 축적한다", metadata

    def derive_needs(self, observation: dict[str, Any]) -> list[AutonomyNeed]:
        needs: list[AutonomyNeed] = []
        active_environment = clean_text(str(observation.get("active_environment") or observation.get("environment") or "assistant")) or "assistant"
        if active_environment == "minecraft":
            minecraft_obs = observation.get("environments", {}).get("minecraft", observation)
            hunger = float(minecraft_obs.get("hunger", 20) or 20)
            health = float(minecraft_obs.get("health", 20) or 20)
            inventory = minecraft_obs.get("inventory") if isinstance(minecraft_obs.get("inventory"), dict) else {}
            hostiles_nearby = threat_count(minecraft_obs)
            immediate_hazards = minecraft_obs.get("immediate_hazards") if isinstance(minecraft_obs.get("immediate_hazards"), list) else []
            nearest_hostile = minecraft_obs.get("nearest_hostile") if isinstance(minecraft_obs.get("nearest_hostile"), dict) else {}
            threat_score = highest_threat_score(minecraft_obs)
            if immediate_hazards:
                needs.append(AutonomyNeed("survive", 1.0, detail="즉시 위험 회피 필요", metadata={"hazards": immediate_hazards, "hostiles_nearby": hostiles_nearby, "nearest_hostile": nearest_hostile, "highest_threat_score": threat_score, "health": health, "hunger": hunger, "domain": "minecraft"}))
            elif health <= 12 or has_survival_threat(minecraft_obs):
                priority = 1.0 if has_interrupting_threat(minecraft_obs) or health <= 12 else 0.72
                needs.append(AutonomyNeed("survive", priority, detail="위협 회피 또는 회복 필요", metadata={"hostiles_nearby": hostiles_nearby, "nearest_hostile": nearest_hostile, "highest_threat_score": threat_score, "health": health, "hunger": hunger, "domain": "minecraft"}))
            if hunger <= 10:
                needs.append(AutonomyNeed("eat", 0.9, detail="배고픔 관리 필요", metadata={"hunger": hunger, "domain": "minecraft"}))
            progress_phase, progress_summary, progress_metadata = self._minecraft_progress_phase(minecraft_obs)
            if progress_phase != "free_gather":
                phase_priority = {
                    "wood_tools": 0.72,
                    "stone_tools": 0.64,
                    "food_buffer": 0.60,
                    "coal_torches": 0.54,
                    "furnace": 0.52,
                    "cook_food": 0.50,
                }.get(progress_phase, 0.48)
                needs.append(AutonomyNeed("progress", phase_priority, detail=progress_summary, metadata={**progress_metadata, "progress_phase": progress_phase, "progress_summary": progress_summary}))
            if not needs:
                needs.append(AutonomyNeed("gather", 0.4, detail="기본 자원 축적", metadata={**inventory, "domain": "minecraft"}))
        else:
            active_sessions = int(observation.get("active_sessions", 0) or 0)
            known_followup_channels = int(observation.get("known_followup_channels", 0) or 0)
            inflight_requests = int(observation.get("inflight_llm_requests", 0) or 0)
            recent_context_items = int(observation.get("recent_context_items", 0) or 0)
            last_autonomy_ping_sec = float(
                observation.get("last_autonomy_ping_sec", 999999)
            )
            repeated_blocked = bool(observation.get("repeated_blocked_action", False))
            quiet_hours = bool(observation.get("quiet_hours", False))
            unresolved_items = int(observation.get("unresolved_items", 0) or 0)
            search_pending = bool(observation.get("search_pending", False))
            cognitive_stale_sec = float(observation.get("cognitive_stale_sec", 999999) or 999999)
            cognitive_refresh_needed = bool(observation.get("cognitive_refresh_needed", False))
            router_refresh_inflight = bool(observation.get("router_refresh_inflight", False))
            self_drive = observation.get("self_state") if isinstance(observation.get("self_state"), dict) else {}
            impulse = clean_text(str(self_drive.get("last_impulse", "stay_silent"))) or "stay_silent"
            gate_reason = clean_text(str(self_drive.get("last_gate_reason", "")))
            queued_question_available = bool(observation.get("queued_proactive_question_available", False))
            if inflight_requests >= 2:
                needs.append(AutonomyNeed("check_status", 0.42, detail="런타임 혼잡 상태를 점검할 수 있음", metadata={"domain": "assistant"}))
            if cognitive_refresh_needed and not router_refresh_inflight and active_sessions > 0 and recent_context_items > 0:
                priority = 0.46 if cognitive_stale_sec >= 30 else 0.38
                needs.append(AutonomyNeed("refresh_cognitive", priority, detail="최근 문맥 기준 router/cognitive 재평가 필요", metadata={"domain": "assistant"}))
            if active_sessions > 0 and recent_context_items > 0 and not repeated_blocked:
                needs.append(AutonomyNeed("summarize", 0.35, detail="최근 문맥을 짧게 요약할 수 있음", metadata={"domain": "assistant"}))
            if search_pending and known_followup_channels > 0 and inflight_requests == 0 and last_autonomy_ping_sec >= 900:
                needs.append(AutonomyNeed("maintain", 0.34, detail="검색 후속 응답이 필요함", metadata={"domain": "assistant", "text": "아까 이어서 실제로 찾아본 결과를 정리해볼게."}))
            if unresolved_items > 0 and known_followup_channels > 0 and not quiet_hours and last_autonomy_ping_sec >= 900:
                needs.append(AutonomyNeed("maintain", 0.28, detail="미해결 문맥 후속이 필요함", metadata={"domain": "assistant", "text": "아직 덜 끝난 문맥이 있어서 이어서 챙겨볼게."}))
            if queued_question_available and known_followup_channels > 0 and active_sessions > 0 and impulse != "stay_silent" and gate_reason not in {"quiet_hours", "answer_inflight", "proactive_cooldown", "hourly_limit"}:
                impulse_text = {
                    "check_softly": "정훈, 아까 뭔가 걸린 것 같아서 살짝 보고 있었어. 지금은 괜찮아?",
                    "comment_on_screen_change": "정훈, 화면에 뭔가 바뀐 것 같아. 내가 제대로 봐줄까?",
                    "ask_light_question": "정훈, 지금 뭐 하고 있어? 그냥 조금 궁금해졌어.",
                    "suggest_next_step": "정훈, 아까 하던 거 이어서 잡아볼까?",
                }.get(impulse, "정훈, 필요하면 바로 불러줘. 나 여기 있어.")
                impulse_text = assistant_proactive_impulse_text(impulse, impulse_text)
                needs.append(AutonomyNeed("ping", 0.18, detail="self-model proactive impulse", metadata={"domain": "assistant", "text": impulse_text, "impulse": impulse, "gate_reason": gate_reason, "drive": self_drive}))
            if queued_question_available and known_followup_channels > 0 and inflight_requests == 0 and active_sessions > 0 and last_autonomy_ping_sec > 1800 and not quiet_hours:
                needs.append(AutonomyNeed("ping", 0.20, detail="필요하면 사용자에게 짧게 핑할 수 있음", metadata={"domain": "assistant"}))
            if not needs:
                needs.append(AutonomyNeed("idle", 0.10, detail="대기", metadata={"domain": "assistant"}))
        needs.sort(key=lambda item: item.priority, reverse=True)
        return needs

    def select_goal(self, needs: list[AutonomyNeed]) -> AutonomyGoal | None:
        if not needs:
            return None
        top = needs[0]
        summary_map = {
            "survive": "위협을 피하고 안전 상태를 회복한다",
            "eat": "먹을 것을 확보하거나 섭취한다",
            "tooling": "기본 도구를 확보한다",
            "progress": clean_text(str(top.metadata.get("progress_summary", ""))) or "초반 생존 단계를 진행한다",
            "gather": "기본 자원을 수집한다",
            "check_status": "현재 런타임 상태를 점검한다",
            "refresh_cognitive": "최근 문맥 기준으로 router/cognitive 상태를 새로 계산한다",
            "summarize": "현재 상태를 짧게 요약한다",
            "maintain": "낮은 위험도의 후속 메시지를 보낸다",
            "ping": "필요할 때 사용자에게 짧게 알린다",
            "idle": "대기한다",
        }
        metadata = dict(top.metadata)
        metadata.setdefault("domain", metadata.get("domain") or "assistant")
        return AutonomyGoal(
            kind=top.kind,
            summary=summary_map.get(top.kind, top.detail or top.kind),
            priority=top.priority,
            source_need=top.kind,
            metadata=metadata,
        )

    def plan_goal(self, goal: AutonomyGoal | None, observation: dict[str, Any]) -> AutonomyPlan | None:
        if goal is None:
            return None
        domain = clean_text(str(goal.metadata.get("domain", "assistant"))) or "assistant"
        if domain == "minecraft":
            minecraft_obs = observation.get("environments", {}).get("minecraft", observation)
            inventory = minecraft_obs.get("inventory") if isinstance(minecraft_obs.get("inventory"), dict) else {}
            immediate_hazards = minecraft_obs.get("immediate_hazards") if isinstance(minecraft_obs.get("immediate_hazards"), list) else []
            hostiles_nearby = threat_count(minecraft_obs)
            health = float(minecraft_obs.get("health", 20) or 20)
            hunger = float(minecraft_obs.get("hunger", 20) or 20)
            nearest_hostile = minecraft_obs.get("nearest_hostile") if isinstance(minecraft_obs.get("nearest_hostile"), dict) else {}
            hostile_distance = threat_distance(minecraft_obs)
            threat_score = highest_threat_score(minecraft_obs)
            has_shield = bool(inventory.get("shield"))
            if goal.kind == "survive":
                steps = []
                food_keywords = ("apple", "bread", "beef", "porkchop", "mutton", "chicken", "rabbit", "cod", "salmon", "potato", "carrot", "berry", "melon", "cookie")
                has_food_inventory = any(isinstance(name, str) and any(keyword in name for keyword in food_keywords) and int(count or 0) > 0 for name, count in inventory.items())
                if has_shield and (threat_score >= 50 or health < 16):
                    steps.append({"domain": domain, "action": "equip_shield", "reason": "hostile_or_low_health", "highest_threat_score": threat_score})
                if immediate_hazards:
                    steps.append({"domain": domain, "action": "avoid_hazard", "reason": "hostile_or_low_health"})
                can_counterattack = not immediate_hazards and threat_score >= 65 and hostile_distance <= 4.5 and health >= 14 and hunger >= 12
                if can_counterattack:
                    steps.append({"domain": domain, "action": "melee_attack", "maxDistance": 5, "reason": "hostile_close_and_stable"})
                if has_interrupting_threat(minecraft_obs) or health < 12:
                    steps.append({"domain": domain, "action": "retreat", "reason": "hostile_or_low_health", "highest_threat_score": threat_score})
                if has_food_inventory:
                    steps.extend([
                        {"domain": domain, "action": "eat_if_low", "minHealth": 16, "minHunger": 16, "reason": "hostile_or_low_health"},
                        {"domain": domain, "action": "heal_or_regroup"},
                    ])
                else:
                    steps.extend([
                        {"domain": domain, "action": "find_food_source", "reason": "low_health_no_food"},
                        {"domain": domain, "action": "consume_food", "reason": "after_food_source"},
                        {"domain": domain, "action": "heal_or_regroup"},
                    ])
            elif goal.kind == "eat":
                steps = []
                if self._minecraft_food_item_count(inventory) <= 0:
                    steps.append({"domain": domain, "action": "find_food_source"})
                steps.append({"domain": domain, "action": "consume_food"})
            elif goal.kind == "tooling":
                tool_stage = clean_text(str(goal.metadata.get("tool_stage", "wood"))) or "wood"
                if tool_stage == "stone":
                    stone_materials = 0
                    current_planks = 0
                    try:
                        stone_materials = int(sum(v for k, v in inventory.items() if isinstance(k, str) and k in {"cobblestone", "cobbled_deepslate", "blackstone", "stone", "deepslate"}))
                        current_planks = int(sum(v for k, v in inventory.items() if isinstance(k, str) and k.endswith("_planks")))
                    except Exception:
                        stone_materials = 0
                        current_planks = 0
                    steps = []
                    if stone_materials < 6:
                        steps.append({"domain": domain, "action": "gather_basic_resources", "targets": ["stone", "food"], "stoneTarget": 6})
                    if current_planks < 2:
                        steps.append({"domain": domain, "action": "gather_logs", "count": 1})
                    steps.append({"domain": domain, "action": "craft_stone_tools"})
                else:
                    current_logs = 0
                    current_planks = 0
                    try:
                        current_logs = int(sum(v for k, v in inventory.items() if isinstance(k, str) and k.endswith("_log")))
                        current_planks = int(sum(v for k, v in inventory.items() if isinstance(k, str) and k.endswith("_planks")))
                    except Exception:
                        current_logs = 0
                        current_planks = 0
                    log_equivalent = current_logs + (current_planks // 4)
                    target_logs = max(0, 2 - log_equivalent)
                    steps = []
                    if target_logs > 0:
                        steps.append({"domain": domain, "action": "gather_logs", "count": target_logs})
                    steps.append({"domain": domain, "action": "craft_basic_tools"})
            elif goal.kind == "progress":
                progress_phase = clean_text(str(goal.metadata.get("progress_phase", "free_gather"))) or "free_gather"
                log_count = int(goal.metadata.get("log_count", 0) or 0)
                plank_count = int(goal.metadata.get("plank_count", 0) or 0)
                stone_materials = int(goal.metadata.get("stone_materials", 0) or 0)
                coal_count = int(goal.metadata.get("coal_count", 0) or 0)
                torch_count = int(goal.metadata.get("torch_count", 0) or 0)
                furnace_count = int(goal.metadata.get("furnace_count", 0) or 0)
                steps = []
                if progress_phase == "wood_tools":
                    log_equivalent = log_count + (plank_count // 4)
                    target_logs = max(0, 2 - log_equivalent)
                    if target_logs > 0:
                        steps.append({"domain": domain, "action": "gather_logs", "count": target_logs})
                    steps.append({"domain": domain, "action": "craft_basic_tools"})
                elif progress_phase == "stone_tools":
                    if stone_materials < 6:
                        steps.append({"domain": domain, "action": "gather_basic_resources", "targets": ["stone", "food"], "stoneTarget": 6})
                    if plank_count < 2:
                        steps.append({"domain": domain, "action": "gather_logs", "count": 1})
                    steps.append({"domain": domain, "action": "craft_stone_tools"})
                elif progress_phase == "food_buffer":
                    steps.extend([
                        {"domain": domain, "action": "find_food_source"},
                        {"domain": domain, "action": "consume_food"},
                    ])
                elif progress_phase == "coal_torches":
                    if coal_count <= 0:
                        steps.append({"domain": domain, "action": "mine_coal", "maxDistance": 48})
                    if torch_count < 8:
                        steps.append({"domain": domain, "action": "craft_torch"})
                elif progress_phase == "furnace":
                    if stone_materials < 8:
                        steps.append({"domain": domain, "action": "gather_basic_resources", "targets": ["stone", "food"], "stoneTarget": 8})
                    if furnace_count <= 0:
                        steps.append({"domain": domain, "action": "craft_furnace"})
                elif progress_phase == "cook_food":
                    if furnace_count <= 0:
                        steps.append({"domain": domain, "action": "craft_furnace"})
                    steps.append({"domain": domain, "action": "cook_food"})
                else:
                    steps.append({"domain": domain, "action": "gather_basic_resources", "targets": ["stone", "food"]})
            else:
                steps = [
                    {"domain": domain, "action": "gather_basic_resources", "targets": ["stone", "food"]},
                ]
        else:
            if goal.kind == "check_status":
                steps = [
                    {"domain": domain, "action": "check_status"},
                ]
            elif goal.kind == "refresh_cognitive":
                steps = [
                    {"domain": domain, "action": "refresh_cognitive_state"},
                ]
            elif goal.kind == "summarize":
                steps = [
                    {"domain": domain, "action": "summarize_recent_context"},
                    {"domain": domain, "action": "summarize_notifications"},
                ]
            elif goal.kind == "maintain":
                followup_text = clean_text(str(goal.metadata.get("text", "")))
                steps = (
                    [{"domain": domain, "action": "send_followup", "text": followup_text}]
                    if followup_text
                    else [{"domain": domain, "action": "idle"}]
                )
            elif goal.kind == "ping":
                steps = [
                    {"domain": domain, "action": "maybe_ping_user", "text": "필요한 게 있으면 바로 불러줘. 지금 대기 중이야."},
                ]
            else:
                steps = [
                    {"domain": domain, "action": "idle"},
                ]
        if goal.kind == "ping" and steps:
            ping_text = clean_text(str(goal.metadata.get("text", "")))
            if ping_text:
                steps[0]["text"] = ping_text
        if self.state.enabled:
            allowed_steps = []
            for step in steps:
                if self._action_key(step) not in self.state.allowed_actions:
                    break
                allowed_steps.append(step)
            steps = allowed_steps
            if not steps:
                return None
        return AutonomyPlan(goal_kind=goal.kind, summary=goal.summary, steps=steps, cursor=0)

    def should_replan(self, step_result: dict[str, Any] | None) -> bool:
        if not isinstance(step_result, dict):
            return False
        if "replan" in step_result:
            return bool(step_result.get("replan"))
        if step_result.get("status") in {
            "failed",
            "blocked",
            "unverified",
        }:
            return True
        return False

    def replan_goal(self, goal: AutonomyGoal | None, observation: dict[str, Any], step_result: dict[str, Any] | None) -> AutonomyPlan | None:
        if goal is None:
            return None
        fallback_reason = clean_text(
            str(
                sanitize_autonomy_step_result(step_result).get(
                    "reason",
                    "",
                )
            )
        ) or "replan"
        plan = self.plan_goal(goal, observation)
        if plan is not None:
            plan.summary = f"{goal.summary} ({fallback_reason} 재계획)"
        return plan

    def is_action_allowed(self, step: dict[str, Any]) -> bool:
        return bool(self.action_authorization_decision(step).get("allowed"))

    def action_authorization_decision(
        self,
        step: dict[str, Any],
        *,
        action_run_id: str = "",
    ) -> dict[str, Any]:
        domain = clean_text(str(step.get("domain", "assistant"))) or "assistant"
        action = clean_text(str(step.get("action", "")))
        if not action:
            return {
                "allowed": False,
                "code": "authorization_action_unsupported",
            }
        token = f"{domain}:{action}"
        if token not in self.state.allowed_actions:
            return {
                "allowed": False,
                "code": "authorization_scope_denied",
                "action": token,
            }
        if self.authorize_action is None:
            return {
                "allowed": False,
                "code": "authorization_required",
                "action": token,
            }
        decision = self._call_authorize_action(
            token,
            action_run_id=action_run_id,
        )
        if not isinstance(decision, dict):
            return {
                "allowed": False,
                "code": "authorization_required",
                "action": token,
            }
        return decision

    def _call_authorize_action(
        self,
        action_key: str,
        *,
        action_run_id: str = "",
    ) -> dict[str, Any]:
        if self.authorize_action is None:
            return {}
        if not action_run_id:
            return self.authorize_action(self.guild_id, action_key)
        try:
            inspect.signature(self.authorize_action).bind(
                self.guild_id,
                action_key,
                action_run_id=action_run_id,
            )
        except (TypeError, ValueError):
            return self.authorize_action(self.guild_id, action_key)
        return self.authorize_action(
            self.guild_id,
            action_key,
            action_run_id=action_run_id,
        )

    def _record_action_result(
        self,
        action_key: str,
        result: dict[str, Any],
        *,
        authorization_grant_id: str = "",
        action_run_id: str = "",
        plan: AutonomyPlan | None = None,
    ) -> dict[str, Any]:
        if self.record_action_outcome is not None and action_key:
            audit_result = dict(result)
            audit_result["_authorization_grant_id"] = (
                authorization_grant_id
            )
            audit_result["_action_run_id"] = action_run_id
            try:
                recorded = self.record_action_outcome(
                    self.guild_id,
                    action_key,
                    audit_result,
                )
            except Exception:
                recorded = False
            failure_reason = ""
            if not (
                recorded is True
                or (
                    isinstance(recorded, dict)
                    and recorded.get("recorded") is True
                )
            ):
                failure_reason = "authorization_audit_unavailable"
            elif (
                isinstance(recorded, dict)
                and authorization_grant_id
                and recorded.get("authorizationCurrent") is not True
            ):
                failure_reason = str(result.get("reason") or "")
                if failure_reason not in {
                    "authorization_required",
                    "authorization_scope_denied",
                    "authorization_action_unsupported",
                    "authorization_audit_unavailable",
                    "authorization_changed_during_action",
                }:
                    failure_reason = "authorization_changed_during_action"
            elif (
                plan is not None
                and isinstance(recorded, dict)
                and recorded.get("verified") is not True
            ):
                failure_reason = "outcome_unverified"
            if failure_reason:
                self.state.enabled = False
                self.state.status = "authorization_required"
                self.state.allowed_actions = []
                return {
                    "status": "unverified",
                    "reason": failure_reason,
                    "reportedStatus": result.get("status"),
                    "step": result.get("step"),
                    "verified": False,
                    **(
                        {"_post_effect_cancellation": True}
                        if result.get("_post_effect_cancellation") is True
                        else {}
                    ),
                    **(
                        {"_post_effect_integrity_failure": True}
                        if result.get("_post_effect_integrity_failure")
                        is True
                        else {}
                    ),
                }
        if plan is not None:
            plan.cursor += 1
            plan.updated_at = time.time()
        return result

    def _authorization_remains_current(
        self,
        action_key: str,
        grant_id: str,
        *,
        action_run_id: str = "",
    ) -> tuple[bool, str]:
        if not self.authorize_action or not grant_id:
            return False, "authorization_required"
        decision = self._call_authorize_action(
            action_key,
            action_run_id=action_run_id,
        )
        if not isinstance(decision, dict) or not decision.get("allowed"):
            return False, str(
                (decision or {}).get("code")
                or "authorization_required"
            )
        if str(decision.get("grantId") or "") != grant_id:
            return False, "authorization_changed_during_action"
        return True, "authorized"

    async def _execute_step_with_context(
        self,
        step: dict[str, Any],
        *,
        context: AutonomyExecutionContext,
    ) -> dict[str, Any]:
        execute_step = self.executor.execute_step
        try:
            inspect.signature(execute_step).bind(
                step,
                context=context,
            )
        except (TypeError, ValueError):
            return await execute_step(step)
        return await execute_step(step, context=context)

    async def execute_next_step(self, plan: AutonomyPlan | None) -> dict[str, Any] | None:
        if plan is None:
            return None
        if plan.cursor >= len(plan.steps):
            return {"status": "done", "reason": "plan_complete"}
        step = plan.steps[plan.cursor]
        action_key = self._action_key(step)
        action_run_id = secrets.token_hex(12)
        authorization = self.action_authorization_decision(
            step,
            action_run_id=action_run_id,
        )
        authorization_grant_id = str(
            authorization.get("grantId") or ""
        )
        if not authorization.get("allowed"):
            self.state.enabled = False
            self.state.status = "authorization_required"
            self.state.allowed_actions = []
            return self._record_action_result(
                action_key,
                {
                    "status": "blocked",
                    "reason": str(
                        authorization.get("code")
                        or "authorization_required"
                    ),
                    "step": step,
                    "verified": False,
                },
                action_run_id=action_run_id,
            )
        if action_key and self._blocked_counts.get(action_key, 0) >= 2:
            return self._record_action_result(
                action_key,
                {
                    "status": "blocked",
                    "reason": "retry_budget_exhausted",
                    "step": step,
                    "skipped": False,
                    "replan": True,
                    "verified": False,
                },
                authorization_grant_id=authorization_grant_id,
                action_run_id=action_run_id,
            )
        try:
            result = await self._execute_step_with_context(
                step,
                context=AutonomyExecutionContext(
                    guild_id=self.guild_id,
                    action_key=action_key,
                    action_run_id=action_run_id,
                    authorization_grant_id=(
                        authorization_grant_id
                    ),
                ),
            )
        except asyncio.CancelledError:
            raise
        except MemoryDeletionJournalIntegrityError:
            raise
        except Exception:
            return self._record_action_result(
                action_key,
                {
                    "status": "failed",
                    "reason": AUTONOMY_EXECUTOR_EXECUTE_FAILED,
                    "step": step,
                    "verified": False,
                    "failure": autonomy_failure_payload(
                        code=AUTONOMY_EXECUTOR_EXECUTE_FAILED,
                        phase="execute",
                        action=action_key,
                    ),
                },
                authorization_grant_id=authorization_grant_id,
                action_run_id=action_run_id,
            )
        if isinstance(result, dict):
            result.setdefault("step", step)
            verified_outcome = autonomy_outcome_verified(
                action_key,
                result,
            )
            if verified_outcome:
                (
                    authorization_current,
                    authorization_code,
                ) = self._authorization_remains_current(
                    action_key,
                    authorization_grant_id,
                    action_run_id=action_run_id,
                )
                if not authorization_current:
                    self.state.enabled = False
                    self.state.status = "authorization_required"
                    self.state.allowed_actions = []
                    return self._record_action_result(
                        action_key,
                        {
                            "status": "unverified",
                            "reason": authorization_code,
                            "reportedStatus": result.get("status"),
                            "step": step,
                            "verified": False,
                            **(
                                {"_post_effect_cancellation": True}
                                if result.get("_post_effect_cancellation")
                                is True
                                else {}
                            ),
                            **(
                                {
                                    "_post_effect_integrity_failure": (
                                        True
                                    )
                                }
                                if result.get(
                                    "_post_effect_integrity_failure"
                                )
                                is True
                                else {}
                            ),
                        },
                        authorization_grant_id=(
                            authorization_grant_id
                        ),
                        action_run_id=action_run_id,
                    )
            if step.get("action") == "refresh_cognitive_state":
                self.state.last_router_refresh_result = (
                    sanitize_autonomy_step_result(result)
                )
            if (
                verified_outcome
                and step.get("action") == "equip_shield"
                and result.get("reason") == "shield_not_in_inventory"
            ):
                return self._record_action_result(
                    action_key,
                    {
                        "status": "ok",
                        "reason": "shield_skipped",
                        "step": step,
                        "replan": False,
                        "verified": True,
                        "skipped": True,
                        "evidence_code": "inventory_absence_verified",
                    },
                    authorization_grant_id=authorization_grant_id,
                    action_run_id=action_run_id,
                    plan=plan,
                )
            if (
                verified_outcome
                and step.get("action") == "avoid_hazard"
                and result.get("reason") == "no_immediate_hazard"
            ):
                return self._record_action_result(
                    action_key,
                    {
                        "status": "ok",
                        "reason": "hazard_step_skipped",
                        "step": step,
                        "replan": False,
                        "verified": True,
                        "skipped": True,
                        "evidence_code": "hazard_absence_verified",
                    },
                    authorization_grant_id=authorization_grant_id,
                    action_run_id=action_run_id,
                    plan=plan,
                )
            if (
                verified_outcome
                and step.get("action") == "retreat"
                and result.get("reason") == "no_hostile_nearby"
            ):
                return self._record_action_result(
                    action_key,
                    {
                        "status": "ok",
                        "reason": "retreat_step_skipped",
                        "step": step,
                        "replan": False,
                        "verified": True,
                        "skipped": True,
                        "evidence_code": "hostile_absence_verified",
                    },
                    authorization_grant_id=authorization_grant_id,
                    action_run_id=action_run_id,
                    plan=plan,
                )
            if (
                verified_outcome
                and step.get("action") == "melee_attack"
                and result.get("reason")
                in {
                    "target_entity_not_found",
                    "target_entity_unreachable",
                }
            ):
                return self._record_action_result(
                    action_key,
                    {
                        "status": "ok",
                        "reason": "attack_step_skipped",
                        "step": step,
                        "replan": False,
                        "verified": True,
                        "skipped": True,
                        "evidence_code": "target_absence_verified",
                    },
                    authorization_grant_id=authorization_grant_id,
                    action_run_id=action_run_id,
                    plan=plan,
                )
            if (
                verified_outcome
                and step.get("action")
                in {
                    "eat_if_low",
                    "consume_food",
                    "heal_or_regroup",
                }
                and result.get("reason") == "no_food_in_inventory"
            ):
                return self._record_action_result(
                    action_key,
                    {
                        "status": "ok",
                        "reason": "food_step_skipped",
                        "step": step,
                        "replan": False,
                        "verified": True,
                        "skipped": True,
                        "evidence_code": "food_absence_verified",
                    },
                    authorization_grant_id=authorization_grant_id,
                    action_run_id=action_run_id,
                    plan=plan,
                )
            if result.get("status") in {"ok", "done", "completed"}:
                if not verified_outcome:
                    return self._record_action_result(
                        action_key,
                        {
                            "status": "unverified",
                            "reason": "outcome_unverified",
                            "reportedStatus": result.get("status"),
                            "step": step,
                            "verified": False,
                        },
                        authorization_grant_id=(
                            authorization_grant_id
                        ),
                        action_run_id=action_run_id,
                    )
            return self._record_action_result(
                action_key,
                result,
                authorization_grant_id=authorization_grant_id,
                action_run_id=action_run_id,
                plan=(
                    plan
                    if result.get("status") in {"ok", "done", "completed"}
                    and verified_outcome
                    else None
                ),
            )
        return self._record_action_result(
            action_key,
            {
                "status": "unknown",
                "reason": "executor_result_invalid",
                "step": step,
                "verified": False,
            },
            authorization_grant_id=authorization_grant_id,
            action_run_id=action_run_id,
        )
