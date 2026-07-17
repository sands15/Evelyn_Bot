from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .control_page_runtime_probe import probe_control_page_runtime_services
from .control_page_runtime_services_runtime import (
    ControlPageRuntimeServicesProbeDeps,
    ControlPageRuntimeServicesRuntimeDeps,
    probe_control_page_runtime_services_once_from_runtime,
)
from .control_page_state import build_control_page_runtime_services_error_payload
from .text import clean_text


@dataclass(frozen=True)
class ControlPageRuntimeServicesDependencyCompositionDeps:
    cache: Any
    get_refresh_task: Callable[[], Any]
    set_refresh_task: Callable[[Any], None]
    get_lock: Callable[[], Any]
    set_lock: Callable[[Any], None]
    lock_factory: Callable[..., Any]
    create_task: Callable[..., Any]
    action_backend: str
    now: Callable[[], float]
    service_urls: Mapping[str, str]
    bot_api_host: str
    bot_api_port: int
    bot_api_state_path: str
    bot_api_probe_timeout_sec: float
    codex_gateway_port: int
    voyager_alive_probe: Callable[[], bool]


class ControlPageRuntimeServicesDependencyComposition:
    """Builds Control Page service-cache and live-probe dependency contracts."""

    def __init__(self, deps: ControlPageRuntimeServicesDependencyCompositionDeps) -> None:
        self.deps = deps

    def build_control_page_runtime_services_runtime_deps(
        self,
    ) -> ControlPageRuntimeServicesRuntimeDeps:
        deps = self.deps
        return ControlPageRuntimeServicesRuntimeDeps(
            cache=deps.cache,
            get_refresh_task=deps.get_refresh_task,
            set_refresh_task=deps.set_refresh_task,
            get_lock=deps.get_lock,
            set_lock=deps.set_lock,
            lock_factory=deps.lock_factory,
            create_task=deps.create_task,
            probe_runtime_services_once=lambda: probe_control_page_runtime_services_once_from_runtime(
                deps=self.build_control_page_runtime_services_probe_runtime_deps(),
            ),
            build_runtime_services_error_payload=build_control_page_runtime_services_error_payload,
            clean_text=clean_text,
            action_backend=deps.action_backend,
            now=deps.now,
        )

    def build_control_page_runtime_services_probe_runtime_deps(
        self,
    ) -> ControlPageRuntimeServicesProbeDeps:
        deps = self.deps
        return ControlPageRuntimeServicesProbeDeps(
            service_urls=dict(deps.service_urls),
            bot_api_host=deps.bot_api_host,
            bot_api_port=deps.bot_api_port,
            bot_api_state_path=deps.bot_api_state_path,
            bot_api_probe_timeout_sec=deps.bot_api_probe_timeout_sec,
            action_backend=deps.action_backend,
            codex_gateway_port=deps.codex_gateway_port,
            voyager_alive_probe=deps.voyager_alive_probe,
            probe_runtime_services_once=probe_control_page_runtime_services,
        )


__all__ = [
    "ControlPageRuntimeServicesDependencyComposition",
    "ControlPageRuntimeServicesDependencyCompositionDeps",
]
