from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .autonomy import AutonomyExecutor
from .autonomy_failure_contract import (
    AUTONOMY_EXECUTOR_OBSERVE_FAILED,
    AUTONOMY_FAILURE_DOMAINS,
    autonomy_failure_payload,
)
from .autonomy_outcome_evidence import autonomy_outcome_verified
from .text import clean_text


class DefaultAutonomyExecutor:
    def __init__(
        self,
        *,
        observe_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        send_followup_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        summarize_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        check_status_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        summarize_recent_context_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        maybe_ping_user_fn: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        refresh_cognitive_state_fn: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.observe_fn = observe_fn
        self.send_followup_fn = send_followup_fn
        self.summarize_fn = summarize_fn
        self.check_status_fn = check_status_fn
        self.summarize_recent_context_fn = summarize_recent_context_fn
        self.maybe_ping_user_fn = maybe_ping_user_fn
        self.refresh_cognitive_state_fn = refresh_cognitive_state_fn

    @staticmethod
    def _finalize_callback_result(
        result: dict[str, Any],
        *,
        action: str,
    ) -> dict[str, Any]:
        result.setdefault("handled_by", "default")
        result.setdefault("action", action)
        status = str(result.get("status") or "").strip().lower()
        verified = autonomy_outcome_verified(
            f"assistant:{action}",
            result,
        )
        result["verified"] = verified
        if status in {"ok", "done", "completed"} and not verified:
            result["reason"] = "outcome_evidence_missing"
        return result

    @staticmethod
    def _unavailable(action: str) -> dict[str, Any]:
        return {
            "status": "blocked",
            "handled_by": "default",
            "action": action,
            "reason": "executor_callback_unavailable",
            "verified": False,
        }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def observe(self) -> dict[str, Any]:
        base = {
            "environment": "assistant",
            "available_capabilities": ["notify", "summarize", "idle", "check_status", "refresh_cognitive_state", "summarize_recent_context", "maybe_ping_user"],
            "risk_level": "low",
        }
        if self.observe_fn is None:
            return base
        observed = await self.observe_fn()
        if isinstance(observed, dict):
            base.update(observed)
        return base

    async def execute_step(self, step: dict[str, Any]) -> dict[str, Any]:
        action = str(step.get("action", "idle"))
        if action == "send_followup":
            text = clean_text(str(step.get("text", "")))
            if self.send_followup_fn is not None and text:
                result = await self.send_followup_fn(text)
                if isinstance(result, dict):
                    return self._finalize_callback_result(
                        result,
                        action=action,
                    )
            return self._unavailable(action)
        if action == "summarize_notifications":
            if self.summarize_fn is not None:
                result = await self.summarize_fn()
                if isinstance(result, dict):
                    return self._finalize_callback_result(
                        result,
                        action=action,
                    )
            return self._unavailable(action)
        if action == "check_status":
            if self.check_status_fn is not None:
                result = await self.check_status_fn()
                if isinstance(result, dict):
                    return self._finalize_callback_result(
                        result,
                        action=action,
                    )
            return self._unavailable(action)
        if action == "refresh_cognitive_state":
            if self.refresh_cognitive_state_fn is not None:
                result = await self.refresh_cognitive_state_fn()
                if isinstance(result, dict):
                    return self._finalize_callback_result(
                        result,
                        action=action,
                    )
            return self._unavailable(action)
        if action == "summarize_recent_context":
            if self.summarize_recent_context_fn is not None:
                result = await self.summarize_recent_context_fn()
                if isinstance(result, dict):
                    return self._finalize_callback_result(
                        result,
                        action=action,
                    )
            return self._unavailable(action)
        if action == "maybe_ping_user":
            text = clean_text(str(step.get("text", ""))) or "지금 확인이 필요해 보여."
            if self.maybe_ping_user_fn is not None:
                result = await self.maybe_ping_user_fn(text)
                if isinstance(result, dict):
                    return self._finalize_callback_result(
                        result,
                        action=action,
                    )
            return self._unavailable(action)
        if action == "idle":
            return {
                "status": "ok",
                "handled_by": "default",
                "action": action,
                "reason": "idle_ok",
                "verified": True,
                "evidence_code": "no_side_effect_required",
            }
        return {
            "status": "blocked",
            "handled_by": "default",
            "action": action,
            "reason": "unsupported_default_action",
            "verified": False,
        }


class RoutedAutonomyExecutor:
    def __init__(self, *, default_executor: AutonomyExecutor, executors: dict[str, AutonomyExecutor]) -> None:
        self.default_executor = default_executor
        self.executors = executors
        self.active_environment = "assistant"
        self.enabled_domains: set[str] = set()

    def list_enabled_domains(self) -> list[str]:
        return sorted(
            domain
            for domain in self.enabled_domains
            if domain in AUTONOMY_FAILURE_DOMAINS
        )

    def is_domain_enabled(self, domain: str) -> bool:
        return clean_text(domain) in self.enabled_domains

    async def enable_domain(self, domain: str) -> bool:
        normalized = clean_text(domain)
        executor = self.executors.get(normalized)
        if (
            normalized not in AUTONOMY_FAILURE_DOMAINS
            or executor is None
        ):
            return False
        await executor.connect()
        self.enabled_domains.add(normalized)
        return True

    async def disable_domain(self, domain: str) -> bool:
        normalized = clean_text(domain)
        executor = self.executors.get(normalized)
        if not normalized or executor is None:
            return False
        await executor.disconnect()
        self.enabled_domains.discard(normalized)
        if self.active_environment == normalized:
            self.active_environment = "assistant"
        return True

    async def connect(self) -> None:
        await self.default_executor.connect()

    async def disconnect(self) -> None:
        for name, executor in self.executors.items():
            if normalized := clean_text(name):
                self.enabled_domains.discard(normalized)
            await executor.disconnect()
        self.active_environment = "assistant"
        await self.default_executor.disconnect()

    async def observe(self) -> dict[str, Any]:
        observed: dict[str, Any] = {}
        default_obs = await self.default_executor.observe()
        if isinstance(default_obs, dict):
            observed.update(default_obs)

        active_environment = "assistant"
        for name in self.list_enabled_domains():
            executor = self.executors.get(name)
            if executor is None:
                continue
            try:
                env_obs = await executor.observe()
            except Exception:
                failure = autonomy_failure_payload(
                    code=AUTONOMY_EXECUTOR_OBSERVE_FAILED,
                    phase="observe",
                    domain=name,
                )
                observed.setdefault("executor_errors", {})[
                    failure["domain"]
                ] = failure
                continue
            if not isinstance(env_obs, dict):
                continue
            observed.setdefault("environments", {})[name] = env_obs
            if env_obs.get("connected") or env_obs.get("active"):
                active_environment = name

        self.active_environment = active_environment
        observed["active_environment"] = self.active_environment
        observed["enabled_domains"] = self.list_enabled_domains()
        return observed

    async def execute_step(self, step: dict[str, Any]) -> dict[str, Any]:
        domain = clean_text(str(step.get("domain") or self.active_environment or "assistant")) or "assistant"
        if domain in self.executors and domain not in self.enabled_domains:
            return {"status": "blocked", "domain": domain, "reason": "executor_disabled"}
        executor = self.executors.get(domain, self.default_executor)
        result = await executor.execute_step(step)
        if isinstance(result, dict):
            result.setdefault("domain", domain)
        return result


def get_routed_autonomy_executor_from_runtime(
    guild_id: int | None,
    *,
    autonomy_engines: dict[int, Any],
    executor_type: type,
) -> Any | None:
    if guild_id is None:
        return None
    engine = autonomy_engines.get(guild_id)
    if engine is None:
        return None
    executor = getattr(engine, "executor", None)
    return executor if isinstance(executor, executor_type) else None


@dataclass(frozen=True)
class ResolveRouteExecutorRuntimeDeps:
    get_autonomy_engine: Callable[[int], Any]
    create_autonomy_engine: Callable[[int], Any]


def resolve_route_executor_from_runtime(
    guild_id: int | None,
    route_name: str,
    *,
    deps: ResolveRouteExecutorRuntimeDeps,
) -> Any:
    if guild_id is None:
        return None
    engine = deps.get_autonomy_engine(guild_id)
    if engine is None:
        if route_name != "minecraft":
            return None
        engine = deps.create_autonomy_engine(guild_id)
    return getattr(engine.executor, "executors", {}).get(route_name)
