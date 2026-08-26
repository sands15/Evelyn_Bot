from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .llm_warmup_runtime import LlmWarmupRuntimeDeps
from .main_inference_contract import current_main_llm_backend_epoch
from .startup_audio_runtime import (
    OpusStartupRuntimeDeps,
    SttWarmupRuntimeDeps,
    ensure_opus_loaded_from_runtime,
)


DepsFactory = Callable[[], Any]
CONTAINER_RESTART_EXIT_CODE = 75
DEFAULT_TERMINAL_EXIT_DEADLINE_SEC = 20.0
DEFAULT_CONTAINER_RESTART_MIN_UPTIME_SEC = 10.0
DEFAULT_RESTART_LAUNCHER_GRACE_SEC = 1.0


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
    main_llm_backend_epoch: Callable[[], str | None] = (
        current_main_llm_backend_epoch
    )


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
    ensure_minecraft_world_lease_started: Callable[
        [],
        Awaitable[Any],
    ]
    shutdown_minecraft_world_lease: Callable[
        [str],
        Awaitable[Any],
    ]
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
    terminal_exit_deadline_sec: float = (
        DEFAULT_TERMINAL_EXIT_DEADLINE_SEC
    )
    monotonic: Callable[[], float] = time.monotonic
    container_restart_min_uptime_sec: float = (
        DEFAULT_CONTAINER_RESTART_MIN_UPTIME_SEC
    )
    container_restart_enabled: bool = False


@dataclass(frozen=True)
class RuntimeLifecycleCompositionDeps:
    startup: RuntimeStartupCompositionDeps
    process: RuntimeProcessCompositionDeps


class RuntimeLifecycleComposition:
    """Owns startup single-flight state and process lifecycle orchestration."""

    def __init__(self, deps: RuntimeLifecycleCompositionDeps) -> None:
        self.deps = deps
        self._startup_components_ready = False
        self._startup_main_epoch: str | None = None
        self._startup_main_warmup_evidence: Any = None
        self.startup_components_task: Any = None
        self.voice_path_warmup_locks: dict[str, asyncio.Lock] = {}
        self.voice_path_warmup_done: dict[str, float] = {}
        self._terminal_request_lock = threading.Lock()
        self._terminal_request_claimed = False
        self._scheduled_terminal_watchdogs: list[
            threading.Timer
        ] = []
        self._process_started_at = float(
            self.deps.process.monotonic()
        )

    def startup_components_ready(self) -> bool:
        return bool(
            self._startup_components_ready
            and self._startup_main_epoch_is_bound()
        )

    def startup_main_warmup_evidence(self) -> Any:
        if not self._startup_main_epoch_is_bound():
            return None
        return self._startup_main_warmup_evidence

    def _startup_main_epoch_is_bound(self) -> bool:
        current = self.deps.startup.main_llm_backend_epoch()
        if current is None and self._startup_main_epoch is None:
            return True
        return bool(
            current
            and self._startup_main_epoch
            and current == self._startup_main_epoch
        )

    def _claim_terminal_request(self) -> bool:
        with self._terminal_request_lock:
            if self._terminal_request_claimed:
                return False
            self._terminal_request_claimed = True
            return True

    def _release_terminal_request(self) -> None:
        with self._terminal_request_lock:
            self._terminal_request_claimed = False

    def _arm_terminal_exit(
        self,
        fallback_exit_code: int,
        *,
        deadline_sec: float | None = None,
    ) -> tuple[threading.Timer, Callable[[int], Any]]:
        claimed = False
        claim_lock = threading.Lock()

        def run_once(exit_code: int = fallback_exit_code) -> Any:
            nonlocal claimed
            with claim_lock:
                if claimed:
                    return None
                claimed = True
            return self.deps.process.exit_process(int(exit_code))

        configured_deadline = (
            self.deps.process.terminal_exit_deadline_sec
            if deadline_sec is None
            else deadline_sec
        )
        deadline = max(
            0.0,
            float(configured_deadline),
        )
        watchdog = threading.Timer(deadline, run_once)
        watchdog.daemon = False
        try:
            watchdog.start()
        except BaseException:
            watchdog.cancel()
            raise
        return watchdog, run_once

    def _safe_terminal_log(self, message: str) -> None:
        try:
            self.deps.process.log(message)
        except Exception:
            pass

    @staticmethod
    async def _ignore_terminal_request() -> None:
        return None

    def _restart_admission_delay(self) -> float:
        deps = self.deps.process
        if not deps.container_restart_enabled:
            return 0.0
        minimum_uptime = max(
            0.0,
            float(deps.container_restart_min_uptime_sec),
        )
        try:
            elapsed = max(
                0.0,
                float(deps.monotonic())
                - self._process_started_at,
            )
        except Exception:
            return minimum_uptime
        return max(0.0, minimum_uptime - elapsed)

    async def _wait_for_restart_admission(
        self,
        delay: float,
    ) -> None:
        if delay <= 0.0:
            return
        sleeper = asyncio.create_task(
            self.deps.process.sleep(delay)
        )
        while True:
            try:
                await asyncio.shield(sleeper)
                return
            except asyncio.CancelledError:
                if sleeper.cancelled():
                    raise

    def _build_restart_launcher(
        self,
        *,
        exit_watchdog: threading.Timer,
        finish: Callable[[int], Any],
    ) -> Callable[[], Any]:
        deps = self.deps.process
        launch_lock = threading.Lock()
        launch_claimed = False

        def launch_and_exit() -> Any:
            nonlocal launch_claimed
            with launch_lock:
                if launch_claimed:
                    return None
                launch_claimed = True

            exit_code = CONTAINER_RESTART_EXIT_CODE
            result: Any = None
            try:
                restart_mode = deps.launch_runtime_restart_sequence(
                    deps.project_root,
                    local_only_mode=deps.local_only_mode,
                    discord_enabled=deps.discord_enabled,
                    control_page_port=deps.control_page_port,
                    fallback_target=deps.fallback_target,
                )
                if restart_mode in {"local", "discord"}:
                    exit_code = 0
                elif restart_mode != "container":
                    self._safe_terminal_log(
                        "[RESTART] launcher_mode_invalid"
                    )
            except Exception as exc:
                self._safe_terminal_log(
                    "[RESTART] launch_failed "
                    f"errorType={type(exc).__name__}"
                )
            finally:
                try:
                    result = finish(exit_code)
                finally:
                    exit_watchdog.cancel()
            return result

        return launch_and_exit

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
        include_llm: bool = False,
        include_tts: bool = True,
    ) -> None:
        deps = self.deps.startup
        lock_key = key or reason
        lock = self.voice_path_warmup_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            completed_at = (
                self.voice_path_warmup_done.get(key)
                if key is not None
                else None
            )
            if completed_at is not None and (
                not include_llm
                or lock_key != "startup"
                or self._startup_main_epoch_is_bound()
            ):
                return
            deps.log(f"[STARTUP] voice_path_warmup_begin reason={reason} key={lock_key}")
            if include_stt:
                if not deps.stt_service_url:
                    await deps.to_thread(deps.get_stt_model)
                await deps.to_thread(deps.warmup_stt_sync)
            if include_llm:
                epoch_before = deps.main_llm_backend_epoch()
                if epoch_before == "":
                    raise RuntimeError("main_llm_epoch_unavailable")
                warmup_evidence = await deps.warmup_llm()
                epoch_after = deps.main_llm_backend_epoch()
                if epoch_after == "":
                    raise RuntimeError("main_llm_epoch_unavailable")
                if (
                    epoch_before is not None
                    and epoch_after != epoch_before
                ):
                    raise RuntimeError("main_llm_epoch_changed")
                if lock_key == "startup":
                    self._startup_main_epoch = epoch_after
                    self._startup_main_warmup_evidence = (
                        warmup_evidence
                    )
            if include_tts:
                await deps.warmup_tts_server()
            self.voice_path_warmup_done[lock_key] = deps.monotonic()
            deps.log(f"[STARTUP] voice_path_warmup_done reason={reason} key={lock_key}")

    async def initialize_startup_components(self) -> None:
        deps = self.deps.startup
        self.deps.process.ensure_session_continuity_started()
        await self.deps.process.ensure_minecraft_world_lease_started()
        deps.log("[STARTUP] init_begin")
        await self.set_tts_presence(True)
        try:
            await deps.to_thread(self.ensure_opus_loaded)
            await self.warmup_voice_path(
                reason="startup",
                key="startup",
                include_llm=True,
            )
            deps.log("[STARTUP] init_done")
        finally:
            await self.set_tts_presence(False)

    async def ensure_startup_components_ready(self) -> None:
        if self._startup_components_ready:
            if self._startup_main_epoch_is_bound():
                return
            await self.warmup_voice_path(
                reason="main_epoch_changed",
                key="startup",
                include_stt=False,
                include_llm=True,
                include_tts=False,
            )
            if not self._startup_main_epoch_is_bound():
                raise RuntimeError("main_llm_epoch_changed")
            return
        current = self.startup_components_task
        if current is None or current.done():
            self.startup_components_task = self.deps.startup.create_task(
                self.initialize_startup_components()
            )
            current = self.startup_components_task
        await current
        if not self._startup_main_epoch_is_bound():
            self.voice_path_warmup_done.pop("startup", None)
            raise RuntimeError("main_llm_epoch_changed")
        self._startup_components_ready = True

    def restart_bot_process(self) -> Awaitable[None]:
        if not self._claim_terminal_request():
            return self._ignore_terminal_request()
        exit_watchdog: threading.Timer | None = None
        launcher_watchdog: threading.Timer | None = None
        try:
            admission_delay = self._restart_admission_delay()
            configured_deadline = max(
                0.0,
                float(
                    self.deps.process.terminal_exit_deadline_sec
                ),
            )
            base_deadline = max(
                configured_deadline,
                admission_delay,
            )
            launcher_grace = min(
                DEFAULT_RESTART_LAUNCHER_GRACE_SEC,
                base_deadline / 2.0,
            )
            hard_deadline = max(
                configured_deadline,
                admission_delay + launcher_grace,
            )
            exit_watchdog, finish = self._arm_terminal_exit(
                CONTAINER_RESTART_EXIT_CODE,
                deadline_sec=hard_deadline,
            )
            launch_and_exit = self._build_restart_launcher(
                exit_watchdog=exit_watchdog,
                finish=finish,
            )
            launcher_watchdog = threading.Timer(
                max(0.0, hard_deadline - launcher_grace),
                launch_and_exit,
            )
            launcher_watchdog.daemon = False
            launcher_watchdog.start()
        except BaseException:
            if launcher_watchdog is not None:
                launcher_watchdog.cancel()
            if exit_watchdog is not None:
                exit_watchdog.cancel()
            self._release_terminal_request()
            raise
        return self._restart_bot_process_owned(
            launcher_watchdog=launcher_watchdog,
            launch_and_exit=launch_and_exit,
            admission_delay=admission_delay,
        )

    async def _restart_bot_process_owned(
        self,
        *,
        launcher_watchdog: threading.Timer,
        launch_and_exit: Callable[[], Any],
        admission_delay: float,
    ) -> None:
        deps = self.deps.process
        try:
            deps.flush_session_continuity()
            await deps.shutdown_minecraft_world_lease(
                "process_restart"
            )
            await deps.sleep(1.0)
            deps.stop_control_page_background_tasks()
            deps.stop_vision_watch_task()
            deps.stop_local_mic_service()
        finally:
            admission_ready = False
            try:
                await self._wait_for_restart_admission(
                    admission_delay
                )
                admission_ready = True
            except Exception as exc:
                self._safe_terminal_log(
                    "[RESTART] admission_wait_failed "
                    f"errorType={type(exc).__name__}"
                )
            finally:
                if admission_ready:
                    launch_and_exit()
                    launcher_watchdog.cancel()

    def _schedule_terminal_shutdown(
        self,
        schedule: Callable[[], bool],
    ) -> bool:
        if not self._claim_terminal_request():
            return False
        try:
            watchdog, _finish = self._arm_terminal_exit(0)
        except BaseException:
            self._release_terminal_request()
            raise
        try:
            scheduled = bool(schedule())
        except BaseException:
            watchdog.cancel()
            self._release_terminal_request()
            raise
        if not scheduled:
            watchdog.cancel()
            self._release_terminal_request()
        else:
            self._scheduled_terminal_watchdogs.append(watchdog)
        return scheduled

    def schedule_evelyn_stack_shutdown(self, delay_ms: int = 3000) -> bool:
        deps = self.deps.process
        return self._schedule_terminal_shutdown(
            lambda: deps.schedule_stack_shutdown(
                deps.project_root,
                delay_ms=delay_ms,
            )
        )

    def schedule_evelyn_local_shutdown(self, delay_ms: int = 1500) -> bool:
        deps = self.deps.process
        return self._schedule_terminal_shutdown(
            lambda: deps.schedule_local_shutdown(
                deps.project_root,
                delay_ms=delay_ms,
            )
        )

    def shutdown_bot_process(self) -> Awaitable[None]:
        if not self._claim_terminal_request():
            return self._ignore_terminal_request()
        try:
            watchdog, finish = self._arm_terminal_exit(0)
        except BaseException:
            self._release_terminal_request()
            raise
        return self._shutdown_bot_process_owned(watchdog, finish)

    async def _shutdown_bot_process_owned(
        self,
        watchdog: threading.Timer,
        finish: Callable[[int], Any],
    ) -> None:
        deps = self.deps.process
        try:
            deps.flush_session_continuity()
            await deps.shutdown_minecraft_world_lease("shutdown")
            await deps.sleep(0.5)
            deps.stop_control_page_background_tasks()
            deps.stop_local_mic_service()
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
            watchdog.cancel()
            finish(0)

    async def run_local_only_mode(self) -> None:
        deps = self.deps.process
        deps.log("[LOCAL MODE] starting without Discord gateway")
        deps.mark_startup_component("discord_gateway", "done", "disabled by DISCORD_ENABLED=false")
        try:
            await deps.start_control_page_server()
        except Exception as exc:
            error_type = type(exc).__name__
            deps.mark_startup_component(
                "control_api", "failed", f"control_page_start_failed:{error_type}"
            )
            deps.log(
                "[CONTROL PAGE] start_fail "
                f"errorCode=control_page_start_failed errorType={error_type}"
            )
            raise RuntimeError("Control Page start failed") from None
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
