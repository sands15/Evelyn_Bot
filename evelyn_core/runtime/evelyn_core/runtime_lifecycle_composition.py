from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .llm_warmup_runtime import LlmWarmupRuntimeDeps
from .startup_audio_runtime import (
    OpusStartupRuntimeDeps,
    SttWarmupRuntimeDeps,
    ensure_opus_loaded_from_runtime,
)


DepsFactory = Callable[[], Any]


@dataclass(frozen=True)
class RuntimeStartupCompositionDeps:
    opus: DepsFactory
    stt_warmup: DepsFactory
    llm_warmup: DepsFactory
    bot_user: Callable[[], Any]
    change_presence: Callable[..., Awaitable[Any]]
    game_factory: Callable[..., Any]
    to_thread: Callable[..., Awaitable[Any]]
    create_task: Callable[[Awaitable[Any]], Any]
    stt_service_url: str
    get_stt_model: Callable[[], Any]
    warmup_stt_sync: Callable[[], Any]
    warmup_llm: Callable[[], Awaitable[Any]]
    warmup_tts_server: Callable[[], Awaitable[Any]]
    monotonic: Callable[[], float]
    log: Callable[..., Any]


@dataclass(frozen=True)
class RuntimeProcessCompositionDeps:
    project_root: Path
    local_only_mode: bool
    discord_enabled: bool
    control_page_port: int
    fallback_target: Path
    sleep: Callable[[float], Awaitable[Any]]
    ensure_session_continuity_started: Callable[[], Any]
    flush_session_continuity: Callable[[], Any]
    stop_control_page_background_tasks: Callable[[], Any]
    stop_vision_watch_task: Callable[[], Any]
    stop_local_mic_service: Callable[[], Any]
    launch_runtime_restart_sequence: Callable[..., str]
    exit_process: Callable[[int], Any]
    schedule_stack_shutdown: Callable[..., bool]
    schedule_local_shutdown: Callable[..., bool]
    bot_guilds: Callable[[], list[Any]]
    mark_startup_component: Callable[..., None]
    start_control_page_server: Callable[[], Awaitable[Any]]
    ensure_local_mic_service_started: Callable[[], Awaitable[Any]]
    ensure_vision_watch_started: Callable[[], Any]
    ensure_control_page_background_tasks_started: Callable[[], Awaitable[Any]]
    control_page_local_url: Callable[[], str]
    wait_forever: Callable[[], Awaitable[Any]]
    log: Callable[..., Any]


@dataclass(frozen=True)
class RuntimeLifecycleCompositionDeps:
    startup: RuntimeStartupCompositionDeps
    process: RuntimeProcessCompositionDeps


class RuntimeLifecycleComposition:
    """Owns startup single-flight state and process lifecycle orchestration."""

    def __init__(self, deps: RuntimeLifecycleCompositionDeps) -> None:
        self.deps = deps
        self._startup_components_ready = False
        self.startup_components_task: Any = None
        self.voice_path_warmup_locks: dict[str, asyncio.Lock] = {}
        self.voice_path_warmup_done: dict[str, float] = {}

    def startup_components_ready(self) -> bool:
        return self._startup_components_ready

    async def set_tts_presence(self, is_warming_up: bool) -> None:
        deps = self.deps.startup
        if deps.bot_user() is None:
            return
        try:
            if is_warming_up:
                await deps.change_presence(activity=deps.game_factory(name="봇 준비중..."))
            else:
                await deps.change_presence(activity=None)
        except Exception as exc:
            deps.log("Presence 변경 실패:", repr(exc))

    def build_opus_startup_runtime_deps(self) -> OpusStartupRuntimeDeps:
        return self.deps.startup.opus()

    def ensure_opus_loaded(self) -> None:
        ensure_opus_loaded_from_runtime(deps=self.build_opus_startup_runtime_deps())

    def build_stt_warmup_runtime_deps(self) -> SttWarmupRuntimeDeps:
        return self.deps.startup.stt_warmup()

    def build_llm_warmup_runtime_deps(self) -> LlmWarmupRuntimeDeps:
        return self.deps.startup.llm_warmup()

    async def warmup_voice_path(
        self,
        *,
        reason: str,
        key: str | None = None,
        include_stt: bool = True,
        include_llm: bool = True,
        include_tts: bool = True,
    ) -> None:
        deps = self.deps.startup
        lock_key = key or reason
        lock = self.voice_path_warmup_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            if key is not None and self.voice_path_warmup_done.get(key):
                return
            deps.log(f"[STARTUP] voice_path_warmup_begin reason={reason} key={lock_key}")
            if include_stt:
                if not deps.stt_service_url:
                    await deps.to_thread(deps.get_stt_model)
                await deps.to_thread(deps.warmup_stt_sync)
            if include_llm:
                await deps.warmup_llm()
            if include_tts:
                await deps.warmup_tts_server()
            self.voice_path_warmup_done[lock_key] = deps.monotonic()
            deps.log(f"[STARTUP] voice_path_warmup_done reason={reason} key={lock_key}")

    async def initialize_startup_components(self) -> None:
        deps = self.deps.startup
        self.deps.process.ensure_session_continuity_started()
        deps.log("[STARTUP] init_begin")
        await self.set_tts_presence(True)
        try:
            await deps.to_thread(self.ensure_opus_loaded)
            await self.warmup_voice_path(reason="startup", key="startup")
            deps.log("[STARTUP] init_done")
        finally:
            await self.set_tts_presence(False)

    async def ensure_startup_components_ready(self) -> None:
        if self._startup_components_ready:
            return
        current = self.startup_components_task
        if current is None or current.done():
            self.startup_components_task = self.deps.startup.create_task(
                self.initialize_startup_components()
            )
            current = self.startup_components_task
        await current
        self._startup_components_ready = True

    async def restart_bot_process(self) -> None:
        deps = self.deps.process
        deps.flush_session_continuity()
        await deps.sleep(1.0)
        deps.stop_control_page_background_tasks()
        deps.stop_vision_watch_task()
        deps.stop_local_mic_service()
        deps.launch_runtime_restart_sequence(
            deps.project_root,
            local_only_mode=deps.local_only_mode,
            discord_enabled=deps.discord_enabled,
            control_page_port=deps.control_page_port,
            fallback_target=deps.fallback_target,
        )
        deps.exit_process(0)

    def schedule_evelyn_stack_shutdown(self, delay_ms: int = 3000) -> bool:
        deps = self.deps.process
        return deps.schedule_stack_shutdown(deps.project_root, delay_ms=delay_ms)

    def schedule_evelyn_local_shutdown(self, delay_ms: int = 1500) -> bool:
        deps = self.deps.process
        return deps.schedule_local_shutdown(deps.project_root, delay_ms=delay_ms)

    async def shutdown_bot_process(self) -> None:
        deps = self.deps.process
        deps.flush_session_continuity()
        await deps.sleep(0.5)
        deps.stop_control_page_background_tasks()
        deps.stop_local_mic_service()
        try:
            for guild in deps.bot_guilds():
                voice_client = guild.voice_client
                if voice_client is None:
                    continue
                try:
                    if hasattr(voice_client, "stop_listening"):
                        voice_client.stop_listening()
                except Exception:
                    pass
                try:
                    await voice_client.disconnect(force=True)
                except Exception:
                    pass
        finally:
            deps.exit_process(0)

    async def run_local_only_mode(self) -> None:
        deps = self.deps.process
        deps.log("[LOCAL MODE] starting without Discord gateway")
        deps.mark_startup_component("discord_gateway", "done", "disabled by DISCORD_ENABLED=false")
        try:
            await deps.start_control_page_server()
        except Exception as exc:
            deps.mark_startup_component("control_api", "failed", repr(exc))
            deps.log(f"[CONTROL PAGE] start_fail err={exc!r}")
            raise
        try:
            await self.ensure_startup_components_ready()
            await deps.ensure_local_mic_service_started()
            deps.ensure_vision_watch_started()
        except Exception as exc:
            deps.log(f"[STARTUP] local_init_fail err={exc!r}")
        try:
            await deps.ensure_control_page_background_tasks_started()
        except Exception as exc:
            deps.log(f"[CONTROL PAGE] bg_tasks_fail err={exc!r}")
        deps.log(f"[LOCAL MODE] ready url={deps.control_page_local_url()}")
        await deps.wait_forever()
