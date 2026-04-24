from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from .memory import cognitive_state_path, read_json_file, write_json_file
from .text import clean_text


class AutonomyExecutor(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def observe(self) -> dict[str, Any]: ...
    async def execute_step(self, step: dict[str, Any]) -> dict[str, Any]: ...


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
    def __init__(
        self,
        *,
        guild_id: int,
        executor: AutonomyExecutor,
        notify: Callable[[str], Awaitable[None]] | None = None,
        poll_interval_sec: float = 4.0,
    ) -> None:
        self.guild_id = guild_id
        self.executor = executor
        self.notify = notify
        self.poll_interval_sec = max(1.0, float(poll_interval_sec))
        self.state = AutonomyRuntimeState(allowed_actions=["assistant:check_status", "assistant:summarize_notifications", "assistant:summarize_recent_context", "assistant:send_followup", "assistant:maybe_ping_user", "assistant:idle", "minecraft:retreat", "minecraft:heal_or_regroup", "minecraft:find_food_source", "minecraft:consume_food", "minecraft:gather_logs", "minecraft:craft_basic_tools", "minecraft:gather_basic_resources"])
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def memory_scope_key(self) -> str:
        return "autonomy"

    def load_persisted_state(self) -> None:
        saved = read_json_file(cognitive_state_path(self.guild_id, scope_type="system", scope_key=self.memory_scope_key))
        runtime = saved.get("autonomy_runtime") if isinstance(saved, dict) else None
        if not isinstance(runtime, dict):
            return
        current_goal = runtime.get("current_goal")
        current_plan = runtime.get("current_plan")
        self.state = AutonomyRuntimeState(
            enabled=bool(runtime.get("enabled", False)),
            status=clean_text(str(runtime.get("status", "idle"))) or "idle",
            safety_mode=clean_text(str(runtime.get("safety_mode", "constrained"))) or "constrained",
            allowed_actions=[clean_text(str(item)) for item in runtime.get("allowed_actions", []) if clean_text(str(item))],
            last_observation=runtime.get("last_observation") if isinstance(runtime.get("last_observation"), dict) else {},
            current_goal=AutonomyGoal(**current_goal) if isinstance(current_goal, dict) and current_goal.get("kind") else None,
            current_plan=AutonomyPlan(**current_plan) if isinstance(current_plan, dict) and current_plan.get("goal_kind") else None,
            last_step_result=runtime.get("last_step_result") if isinstance(runtime.get("last_step_result"), dict) else {},
            last_error=clean_text(str(runtime.get("last_error", ""))),
            failure_count=int(runtime.get("failure_count", 0) or 0),
            updated_at=float(runtime.get("updated_at", time.time()) or time.time()),
        )

    def persist_state(self) -> None:
        path = cognitive_state_path(self.guild_id, scope_type="system", scope_key=self.memory_scope_key)
        payload = read_json_file(path)
        payload["autonomy_runtime"] = {
            "enabled": self.state.enabled,
            "status": self.state.status,
            "safety_mode": self.state.safety_mode,
            "allowed_actions": list(self.state.allowed_actions),
            "last_observation": self.state.last_observation,
            "current_goal": asdict(self.state.current_goal) if self.state.current_goal else None,
            "current_plan": asdict(self.state.current_plan) if self.state.current_plan else None,
            "last_step_result": self.state.last_step_result,
            "last_error": self.state.last_error,
            "failure_count": self.state.failure_count,
            "updated_at": self.state.updated_at,
        }
        write_json_file(path, payload)

    async def start(self) -> None:
        async with self._lock:
            if self._task is not None and not self._task.done():
                return
            self.load_persisted_state()
            await self.executor.connect()
            self.state.enabled = True
            self.state.status = "running"
            self.state.updated_at = time.time()
            self.persist_state()
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        async with self._lock:
            self.state.enabled = False
            self.state.status = "stopping"
            self.state.updated_at = time.time()
            self.persist_state()
            task = self._task
            self._task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.executor.disconnect()
        self.state.status = "idle"
        self.state.updated_at = time.time()
        self.persist_state()

    async def _run_loop(self) -> None:
        while self.state.enabled:
            try:
                await self.run_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.last_error = clean_text(repr(exc))
                self.state.failure_count += 1
                self.state.status = "error"
                self.state.updated_at = time.time()
                self.persist_state()
                if self.notify is not None:
                    await self.notify(f"[자율봇] 오류: {self.state.last_error}")
            await asyncio.sleep(self.poll_interval_sec)

    async def run_cycle(self) -> AutonomyCycleResult:
        observation = await self.observe()
        needs = self.derive_needs(observation)
        selected_goal = self.select_goal(needs)
        planned = self.plan_goal(selected_goal, observation)
        step_result = await self.execute_next_step(planned)
        if self.should_replan(step_result):
            planned = self.replan_goal(selected_goal, observation, step_result)
        self.state.last_observation = observation
        self.state.current_goal = selected_goal
        self.state.current_plan = planned
        self.state.last_step_result = step_result or {}
        self.state.status = "running" if self.state.enabled else "idle"
        self.state.updated_at = time.time()
        self.persist_state()
        return AutonomyCycleResult(
            observation=observation,
            needs=needs,
            selected_goal=selected_goal,
            planned=planned,
            step_result=step_result,
            state=self.state,
        )

    async def observe(self) -> dict[str, Any]:
        observed = await self.executor.observe()
        return observed if isinstance(observed, dict) else {"raw": observed}

    def derive_needs(self, observation: dict[str, Any]) -> list[AutonomyNeed]:
        needs: list[AutonomyNeed] = []
        active_environment = clean_text(str(observation.get("active_environment") or observation.get("environment") or "assistant")) or "assistant"
        if active_environment == "minecraft":
            minecraft_obs = observation.get("environments", {}).get("minecraft", observation)
            hunger = float(minecraft_obs.get("hunger", 20) or 20)
            health = float(minecraft_obs.get("health", 20) or 20)
            inventory = minecraft_obs.get("inventory") if isinstance(minecraft_obs.get("inventory"), dict) else {}
            hostiles_nearby = int(minecraft_obs.get("hostiles_nearby", 0) or 0)
            if health <= 8 or hostiles_nearby > 0:
                needs.append(AutonomyNeed("survive", 1.0, detail="위협 회피 또는 회복 필요", metadata={"hostiles_nearby": hostiles_nearby, "health": health, "domain": "minecraft"}))
            if hunger <= 10:
                needs.append(AutonomyNeed("eat", 0.9, detail="배고픔 관리 필요", metadata={"hunger": hunger, "domain": "minecraft"}))
            if not inventory.get("wooden_pickaxe") and not inventory.get("stone_pickaxe"):
                needs.append(AutonomyNeed("tooling", 0.7, detail="기본 곡괭이 확보 필요", metadata={**inventory, "domain": "minecraft"}))
            if not needs:
                needs.append(AutonomyNeed("gather", 0.4, detail="기본 자원 축적", metadata={**inventory, "domain": "minecraft"}))
        else:
            active_sessions = int(observation.get("active_sessions", 0) or 0)
            known_followup_channels = int(observation.get("known_followup_channels", 0) or 0)
            inflight_requests = int(observation.get("inflight_llm_requests", 0) or 0)
            recent_context_items = int(observation.get("recent_context_items", 0) or 0)
            last_autonomy_ping_sec = float(observation.get("last_autonomy_ping_sec", 999999) or 999999)
            if inflight_requests >= 2:
                needs.append(AutonomyNeed("check_status", 0.42, detail="런타임 혼잡 상태를 점검할 수 있음", metadata={"domain": "assistant"}))
            if active_sessions > 0 and recent_context_items > 0:
                needs.append(AutonomyNeed("summarize", 0.35, detail="최근 문맥을 짧게 요약할 수 있음", metadata={"domain": "assistant"}))
            if known_followup_channels > 0 and last_autonomy_ping_sec > 900:
                needs.append(AutonomyNeed("maintain", 0.28, detail="오랜 침묵 뒤 저위험 후속 메시지를 보낼 수 있음", metadata={"domain": "assistant"}))
            if known_followup_channels > 0 and inflight_requests == 0 and active_sessions > 0:
                needs.append(AutonomyNeed("ping", 0.22, detail="필요하면 사용자에게 짧게 핑할 수 있음", metadata={"domain": "assistant"}))
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
            "gather": "기본 자원을 수집한다",
            "check_status": "현재 런타임 상태를 점검한다",
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
            if goal.kind == "survive":
                steps = [
                    {"domain": domain, "action": "retreat", "reason": "hostile_or_low_health"},
                    {"domain": domain, "action": "heal_or_regroup"},
                ]
            elif goal.kind == "eat":
                steps = [
                    {"domain": domain, "action": "find_food_source"},
                    {"domain": domain, "action": "consume_food"},
                ]
            elif goal.kind == "tooling":
                steps = [
                    {"domain": domain, "action": "gather_logs", "count": 4},
                    {"domain": domain, "action": "craft_basic_tools"},
                ]
            else:
                steps = [
                    {"domain": domain, "action": "gather_basic_resources", "targets": ["wood", "stone", "food"]},
                ]
        else:
            if goal.kind == "check_status":
                steps = [
                    {"domain": domain, "action": "check_status"},
                ]
            elif goal.kind == "summarize":
                steps = [
                    {"domain": domain, "action": "summarize_recent_context"},
                    {"domain": domain, "action": "summarize_notifications"},
                ]
            elif goal.kind == "maintain":
                steps = [
                    {"domain": domain, "action": "send_followup", "text": "지금은 저위험 자율 보조 모드로 상태를 점검하고 있어."},
                ]
            elif goal.kind == "ping":
                steps = [
                    {"domain": domain, "action": "maybe_ping_user", "text": "필요한 게 있으면 바로 불러줘. 지금 대기 중이야."},
                ]
            else:
                steps = [
                    {"domain": domain, "action": "idle"},
                ]
        return AutonomyPlan(goal_kind=goal.kind, summary=goal.summary, steps=steps, cursor=0)

    def should_replan(self, step_result: dict[str, Any] | None) -> bool:
        if not isinstance(step_result, dict):
            return False
        if step_result.get("status") in {"failed", "blocked"}:
            return True
        return bool(step_result.get("replan"))

    def replan_goal(self, goal: AutonomyGoal | None, observation: dict[str, Any], step_result: dict[str, Any] | None) -> AutonomyPlan | None:
        if goal is None:
            return None
        fallback_reason = clean_text(str((step_result or {}).get("reason", ""))) or "replan"
        plan = self.plan_goal(goal, observation)
        if plan is not None:
            plan.summary = f"{goal.summary} ({fallback_reason} 재계획)"
        return plan

    def is_action_allowed(self, step: dict[str, Any]) -> bool:
        domain = clean_text(str(step.get("domain", "assistant"))) or "assistant"
        action = clean_text(str(step.get("action", "")))
        if not action:
            return False
        token = f"{domain}:{action}"
        if not self.state.allowed_actions:
            return False
        return token in self.state.allowed_actions

    async def execute_next_step(self, plan: AutonomyPlan | None) -> dict[str, Any] | None:
        if plan is None:
            return None
        if plan.cursor >= len(plan.steps):
            return {"status": "done", "reason": "plan_complete"}
        step = plan.steps[plan.cursor]
        if not self.is_action_allowed(step):
            return {"status": "blocked", "reason": "action_not_allowed", "step": step}
        result = await self.executor.execute_step(step)
        if isinstance(result, dict) and result.get("status") in {"ok", "done", "completed"}:
            plan.cursor += 1
            plan.updated_at = time.time()
        return result if isinstance(result, dict) else {"status": "unknown", "raw": result}


import contextlib
