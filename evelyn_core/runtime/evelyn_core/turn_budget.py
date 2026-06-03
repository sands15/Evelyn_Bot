from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TurnExecutionBudget:
    router_timeout_sec: float
    context_timeout_sec: float
    memory_timeout_sec: float
    fallback_route: str
    fallback_reason: str
    router_enabled: bool
    needs_memory: bool
    needs_runtime_state: bool
    priority: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "router_timeout_sec": self.router_timeout_sec,
            "context_timeout_sec": self.context_timeout_sec,
            "memory_timeout_sec": self.memory_timeout_sec,
            "fallback_route": self.fallback_route,
            "fallback_reason": self.fallback_reason,
            "router_enabled": self.router_enabled,
            "needs_memory": self.needs_memory,
            "needs_runtime_state": self.needs_runtime_state,
            "priority": self.priority,
        }


def _policy_value(policy: Any, name: str, default: Any) -> Any:
    if policy is None:
        return default
    if isinstance(policy, dict):
        return policy.get(name, default)
    return getattr(policy, name, default)


def build_turn_execution_budget(
    *,
    router_timeout_sec: float,
    context_timeout_sec: float,
    memory_timeout_sec: float,
    fallback_route: str,
    router_enabled: bool,
    context_policy: Any = None,
    route_decision: Any = None,
    fallback_reason: str = "route_policy_fallback",
) -> TurnExecutionBudget:
    policy = context_policy if context_policy is not None else route_decision
    needs_memory = bool(_policy_value(policy, "needs_memory", True))
    needs_runtime_state = bool(_policy_value(policy, "needs_runtime_state", True))
    priority = str(_policy_value(policy, "priority", "latency") or "latency")
    return TurnExecutionBudget(
        router_timeout_sec=max(0.1, float(router_timeout_sec)),
        context_timeout_sec=max(0.1, float(context_timeout_sec)),
        memory_timeout_sec=max(0.0, float(memory_timeout_sec if needs_memory else 0.0)),
        fallback_route=str(fallback_route or "main_direct"),
        fallback_reason=str(fallback_reason or "route_policy_fallback"),
        router_enabled=bool(router_enabled),
        needs_memory=needs_memory,
        needs_runtime_state=needs_runtime_state,
        priority=priority,
    )
