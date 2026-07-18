from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .runtime_status_context import (
    RuntimeStatusContextDeps,
    RuntimeStatusContextState,
    build_runtime_status_context_from_runtime,
)
from .voice_pipeline import RouteDecision
from .voice_response_runtime import (
    MainResponseGuidanceRuntimeDeps,
    build_main_response_guidance_from_runtime,
)


@dataclass(frozen=True)
class ResponseContextCompositionDeps:
    runtime_status_enabled: bool
    runtime_status_refresh_sec: float
    control_page_host: str
    control_page_port: int
    llm_server_url: str
    router_llm_url: str
    summary_llm_url: str
    omnivoice_server_url: str
    minecraft_autonomy_service_port: int
    voyager_action_backend: str
    voyager_codex_gateway_port: int
    get_control_page_runtime_services: Callable[[], Awaitable[dict]]
    is_control_api_ready_from_runtime_services: Callable[[dict], bool]
    probe_runtime_tcp_service: Callable[..., Awaitable[tuple[str, bool]]]
    load_runtime_gpu_status: Callable[[], tuple[str, bool]]
    load_runtime_recent_errors: Callable[[], list[str]]
    now: Callable[[], float]
    clean_text: Callable[[str], str]
    apply_ask_gating: Callable[..., dict[str, Any]]
    persona_state_hint_for_turn: Callable[..., str]
    recent_assistant_reply_summary: Callable[..., str]
    build_tool_awareness_context: Callable[..., str]
    skill_registry: Any
    format_minecraft_state_summary: Callable[[dict[str, Any] | None], str]
    question_feature_enabled: bool


class ResponseContextComposition:
    """Owns runtime-status caching and the final response-guidance context."""

    def __init__(self, deps: ResponseContextCompositionDeps) -> None:
        self.deps = deps
        self.runtime_status_state = RuntimeStatusContextState()

    def build_runtime_status_context_deps(self) -> RuntimeStatusContextDeps:
        deps = self.deps
        return RuntimeStatusContextDeps(
            enabled=deps.runtime_status_enabled,
            refresh_sec=deps.runtime_status_refresh_sec,
            control_page_host=deps.control_page_host,
            control_page_port=deps.control_page_port,
            llm_server_url=deps.llm_server_url,
            router_llm_url=deps.router_llm_url,
            summary_llm_url=deps.summary_llm_url,
            omnivoice_server_url=deps.omnivoice_server_url,
            minecraft_autonomy_service_port=deps.minecraft_autonomy_service_port,
            voyager_action_backend=deps.voyager_action_backend,
            voyager_codex_gateway_port=deps.voyager_codex_gateway_port,
            get_control_page_runtime_services=deps.get_control_page_runtime_services,
            is_control_api_ready_from_runtime_services=deps.is_control_api_ready_from_runtime_services,
            probe_runtime_tcp_service=deps.probe_runtime_tcp_service,
            load_runtime_gpu_status=deps.load_runtime_gpu_status,
            load_runtime_recent_errors=deps.load_runtime_recent_errors,
            now=deps.now,
        )

    async def build_runtime_status_context(self, *, force: bool = False) -> str:
        return await build_runtime_status_context_from_runtime(
            deps=self.build_runtime_status_context_deps(),
            state=self.runtime_status_state,
            force=force,
        )

    def skill_route_available(self, route_name: str, *, source: str) -> bool:
        try:
            return bool(self.deps.skill_registry.find_by_route(route_name, source=source))
        except Exception:
            return False

    def build_main_response_guidance(
        self,
        cognitive_state: dict | None = None,
        *,
        source: str = "text",
        user_text: str = "",
        session_key: str | None = None,
        guild_id: int | None = None,
        minecraft_state: dict[str, Any] | None = None,
        runtime_status_context: str | None = None,
        route_decision: RouteDecision | None = None,
    ) -> str:
        return build_main_response_guidance_from_runtime(
            cognitive_state,
            source=source,
            user_text=user_text,
            session_key=session_key,
            guild_id=guild_id,
            minecraft_state=minecraft_state,
            runtime_status_context=runtime_status_context,
            route_decision=route_decision,
            deps=self.build_main_response_guidance_runtime_deps(),
        )

    def build_main_response_guidance_runtime_deps(self) -> MainResponseGuidanceRuntimeDeps:
        deps = self.deps
        return MainResponseGuidanceRuntimeDeps(
            clean_text=deps.clean_text,
            apply_ask_gating=deps.apply_ask_gating,
            persona_state_hint_for_turn=deps.persona_state_hint_for_turn,
            recent_assistant_reply_summary=deps.recent_assistant_reply_summary,
            build_tool_awareness_context=deps.build_tool_awareness_context,
            route_available=self.skill_route_available,
            format_minecraft_state_summary=deps.format_minecraft_state_summary,
            question_feature_enabled=deps.question_feature_enabled,
        )


__all__ = ["ResponseContextComposition", "ResponseContextCompositionDeps"]
