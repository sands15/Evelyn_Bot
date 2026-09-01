from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import runtime_lifecycle as runtime_lifecycle_module

from evelyn_core.control_page_composition_runtime import (
    ControlPageComposition,
    ControlPageCompositionDeps,
)
from evelyn_core.runtime_lifecycle_composition import (
    RuntimeLifecycleComposition,
    RuntimeLifecycleCompositionDeps,
    RuntimeProcessCompositionDeps,
    RuntimeStartupCompositionDeps,
)


async def _record_async(
    events: list[object],
    event: object,
) -> None:
    events.append(event)


class FakeVoiceClient:
    def __init__(self, events: list[object], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail

    def stop_listening(self) -> None:
        self.events.append("stop_listening")
        if self.fail:
            raise RuntimeError("stop failed")

    async def disconnect(self, *, force: bool) -> None:
        self.events.append(("disconnect", force))
        if self.fail:
            raise RuntimeError("disconnect failed")


class RuntimeLifecycleCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build_startup_deps(self, **overrides) -> RuntimeStartupCompositionDeps:
        async def to_thread(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        values = dict(
            opus=lambda: "opus-deps",
            stt_warmup=lambda: "stt-deps",
            llm_warmup=lambda: "llm-deps",
            bot_user=Mock(return_value=object()),
            change_presence=AsyncMock(),
            game_factory=lambda **kwargs: kwargs,
            to_thread=to_thread,
            create_task=asyncio.create_task,
            stt_service_url="",
            get_stt_model=Mock(),
            warmup_stt_sync=Mock(),
            warmup_llm=AsyncMock(),
            warmup_tts_server=AsyncMock(),
            monotonic=Mock(return_value=123.0),
            log=Mock(),
        )
        values.update(overrides)
        return RuntimeStartupCompositionDeps(**values)

    def build_process_deps(self, **overrides) -> RuntimeProcessCompositionDeps:
        values = dict(
            project_root=REPO_ROOT,
            local_only_mode=True,
            discord_enabled=False,
            control_page_port=8799,
            fallback_target=REPO_ROOT / "evelyn_core" / "start.bat",
            sleep=AsyncMock(),
            ensure_session_continuity_started=Mock(),
            flush_session_continuity=Mock(),
            ensure_minecraft_world_lease_started=AsyncMock(),
            shutdown_minecraft_world_lease=AsyncMock(),
            stop_control_page_background_tasks=Mock(),
            stop_vision_watch_task=Mock(),
            stop_local_mic_service=Mock(),
            launch_runtime_restart_sequence=Mock(return_value="local"),
            exit_process=Mock(),
            schedule_stack_shutdown=Mock(return_value=True),
            schedule_local_shutdown=Mock(return_value=True),
            bot_guilds=Mock(return_value=[]),
            mark_startup_component=Mock(),
            start_control_page_server=AsyncMock(),
            ensure_local_mic_service_started=AsyncMock(),
            ensure_vision_watch_started=Mock(),
            ensure_control_page_background_tasks_started=AsyncMock(),
            control_page_local_url=Mock(return_value="http://127.0.0.1:8799"),
            wait_forever=AsyncMock(),
            log=Mock(),
        )
        values.update(overrides)
        return RuntimeProcessCompositionDeps(**values)

    def build_composition(self, *, startup=None, process=None) -> RuntimeLifecycleComposition:
        return RuntimeLifecycleComposition(
            RuntimeLifecycleCompositionDeps(
                startup=startup or self.build_startup_deps(),
                process=process or self.build_process_deps(),
            )
        )

    async def test_startup_ready_is_single_flight_and_live(self) -> None:
        composition = self.build_composition()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def initialize() -> None:
            entered.set()
            await release.wait()

        composition.initialize_startup_components = AsyncMock(side_effect=initialize)
        first = asyncio.create_task(composition.ensure_startup_components_ready())
        await entered.wait()
        second = asyncio.create_task(composition.ensure_startup_components_ready())
        await asyncio.sleep(0)

        self.assertFalse(composition.startup_components_ready())
        composition.initialize_startup_components.assert_called_once_with()
        self.assertIsNotNone(composition.startup_components_task)

        release.set()
        await asyncio.gather(first, second)

        self.assertTrue(composition.startup_components_ready())
        composition.initialize_startup_components.assert_awaited_once_with()

    async def test_voice_warmup_key_deduplicates_completed_work(self) -> None:
        startup = self.build_startup_deps()
        composition = self.build_composition(startup=startup)

        await composition.warmup_voice_path(reason="voice_connect", key="voice:1:2")
        await composition.warmup_voice_path(reason="voice_connect", key="voice:1:2")

        startup.get_stt_model.assert_called_once_with()
        startup.warmup_stt_sync.assert_called_once_with()
        startup.warmup_llm.assert_not_awaited()
        startup.warmup_tts_server.assert_awaited_once_with()
        self.assertEqual(composition.voice_path_warmup_done["voice:1:2"], 123.0)

    async def test_main_backend_epoch_change_invalidates_startup_ready(self) -> None:
        epoch = {"value": "epoch-one"}
        startup = self.build_startup_deps(
            main_llm_backend_epoch=lambda: epoch["value"],
        )
        composition = self.build_composition(startup=startup)
        composition.ensure_opus_loaded = Mock()
        composition.set_tts_presence = AsyncMock()

        await composition.ensure_startup_components_ready()
        self.assertTrue(composition.startup_components_ready())
        self.assertIsNotNone(
            composition.startup_main_warmup_evidence()
        )

        epoch["value"] = "epoch-two"
        self.assertFalse(composition.startup_components_ready())
        self.assertIsNone(
            composition.startup_main_warmup_evidence()
        )
        await composition.ensure_startup_components_ready()

        self.assertTrue(composition.startup_components_ready())
        self.assertEqual(startup.warmup_llm.await_count, 2)

    async def test_epoch_change_during_main_warmup_fails_closed(self) -> None:
        startup = self.build_startup_deps(
            main_llm_backend_epoch=Mock(
                side_effect=["epoch-one", "epoch-two"]
            ),
        )
        composition = self.build_composition(startup=startup)

        with self.assertRaisesRegex(RuntimeError, "main_llm_epoch_changed"):
            await composition.warmup_voice_path(
                reason="startup",
                key="startup",
                include_stt=False,
                include_llm=True,
                include_tts=False,
            )

        self.assertNotIn("startup", composition.voice_path_warmup_done)

    async def test_stable_epoch_warmup_proof_does_not_expire(self) -> None:
        clock = {"value": 100.0}
        startup = self.build_startup_deps(
            monotonic=lambda: clock["value"],
        )
        composition = self.build_composition(startup=startup)
        composition.ensure_opus_loaded = Mock()
        composition.set_tts_presence = AsyncMock()

        await composition.ensure_startup_components_ready()
        self.assertTrue(composition.startup_components_ready())

        clock["value"] = 1_000_000.0
        self.assertTrue(composition.startup_components_ready())
        await composition.ensure_startup_components_ready()

        self.assertTrue(composition.startup_components_ready())
        self.assertEqual(startup.warmup_llm.await_count, 1)
        startup.warmup_stt_sync.assert_called_once_with()
        startup.warmup_tts_server.assert_awaited_once_with()

    async def test_concurrent_epoch_change_rewarms_once(self) -> None:
        epoch = {"value": "epoch-one"}
        startup = self.build_startup_deps(
            main_llm_backend_epoch=lambda: epoch["value"],
        )
        composition = self.build_composition(startup=startup)
        composition.ensure_opus_loaded = Mock()
        composition.set_tts_presence = AsyncMock()

        await composition.ensure_startup_components_ready()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def rewarm() -> object:
            entered.set()
            await release.wait()
            return object()

        startup.warmup_llm.side_effect = rewarm
        epoch["value"] = "epoch-two"
        first = asyncio.create_task(
            composition.ensure_startup_components_ready()
        )
        await entered.wait()
        second = asyncio.create_task(
            composition.ensure_startup_components_ready()
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)

        self.assertEqual(startup.warmup_llm.await_count, 2)
        startup.warmup_stt_sync.assert_called_once_with()
        startup.warmup_tts_server.assert_awaited_once_with()
        self.assertTrue(composition.startup_components_ready())

    def test_core_warmup_owns_only_the_core_prompt_abi(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        wiring = source.split("llm_warmup=lambda: LlmWarmupRuntimeDeps(", 1)[1].split(
            "bot_user=lambda: bot.user",
            1,
        )[0]
        self.assertIn("system_prompts=(SYSTEM_PROMPT,)", wiring)
        self.assertIn("expected_prompt_abi_ids=(", wiring)
        self.assertIn("compile_main_prompt(", wiring)
        self.assertIn("model_name=MODEL_NAME", wiring)
        self.assertIn("clean_text(SYSTEM_PROMPT)", wiring)
        self.assertIn("content_format=MAIN_LLM_CHAT_CONTENT_FORMAT", wiring)
        self.assertIn("stable_system_prefix=clean_text(SYSTEM_PROMPT)", wiring)
        self.assertIn(").abi.prompt_abi_id", wiring)
        self.assertNotIn("FAST_MAIN_LLM_USER_PREFIX", wiring)

    async def test_initialize_restores_presence_when_warmup_fails(self) -> None:
        composition = self.build_composition()
        composition.set_tts_presence = AsyncMock()
        composition.ensure_opus_loaded = Mock()
        composition.warmup_voice_path = AsyncMock(side_effect=RuntimeError("warmup failed"))

        with self.assertRaisesRegex(RuntimeError, "warmup failed"):
            await composition.initialize_startup_components()

        self.assertEqual(
            [call.args for call in composition.set_tts_presence.await_args_list],
            [(True,), (False,)],
        )
        composition.deps.process.ensure_session_continuity_started.assert_called_once_with()
        (
            composition.deps.process
            .ensure_minecraft_world_lease_started
            .assert_awaited_once_with()
        )

    async def test_restart_stops_services_launches_and_exits_in_order(self) -> None:
        events: list[object] = []

        async def sleep(delay: float) -> None:
            events.append(("sleep", delay))

        process = self.build_process_deps(
            sleep=sleep,
            flush_session_continuity=lambda: events.append(
                "flush_continuity"
            ),
            shutdown_minecraft_world_lease=(
                lambda reason: _record_async(
                    events,
                    ("stop_minecraft", reason),
                )
            ),
            stop_control_page_background_tasks=lambda: events.append("stop_control"),
            stop_vision_watch_task=lambda: events.append("stop_vision"),
            stop_local_mic_service=lambda: events.append("stop_mic"),
            launch_runtime_restart_sequence=lambda *args, **kwargs: (
                events.append(("launch", args, kwargs)) or "local"
            ),
            exit_process=lambda code: events.append(("exit", code)),
        )

        await self.build_composition(process=process).restart_bot_process()

        self.assertEqual(
            [event[0] if isinstance(event, tuple) else event for event in events],
            [
                "flush_continuity",
                "stop_minecraft",
                "sleep",
                "stop_control",
                "stop_vision",
                "stop_mic",
                "launch",
                "exit",
            ],
        )
        launch = events[6]
        self.assertEqual(launch[1], (REPO_ROOT,))
        self.assertEqual(
            launch[2],
            {
                "local_only_mode": True,
                "discord_enabled": False,
                "control_page_port": 8799,
                "fallback_target": REPO_ROOT / "evelyn_core" / "start.bat",
            },
        )
        self.assertEqual(events[-1], ("exit", 0))

    def test_missing_primary_launcher_runs_batch_fallback_with_cmd(self) -> None:
        missing_primary = REPO_ROOT / "missing-primary.bat"
        fallback_batch = REPO_ROOT / "evelyn_core" / "start.bat"

        with patch.object(
            runtime_lifecycle_module.subprocess,
            "Popen",
        ) as popen:
            runtime_lifecycle_module.launch_restart_process(
                missing_primary,
                REPO_ROOT,
                {},
                fallback_target=fallback_batch,
            )

        command = popen.call_args.args[0]
        self.assertEqual(
            command,
            ["cmd.exe", "/c", str(fallback_batch)],
        )
        self.assertNotEqual(command[0], sys.executable)

    def test_missing_primary_launcher_runs_python_fallback_with_python(
        self,
    ) -> None:
        missing_primary = REPO_ROOT / "missing-primary.bat"
        fallback_python = REPO_ROOT / "main.py"

        with patch.object(
            runtime_lifecycle_module.subprocess,
            "Popen",
        ) as popen:
            runtime_lifecycle_module.launch_restart_process(
                missing_primary,
                REPO_ROOT,
                {},
                fallback_target=fallback_python,
            )

        self.assertEqual(
            popen.call_args.args[0],
            [sys.executable, str(fallback_python)],
        )

    def test_missing_primary_and_fallback_fail_before_process_spawn(
        self,
    ) -> None:
        missing_primary = REPO_ROOT / "missing-primary.bat"
        missing_fallback = REPO_ROOT / "missing-fallback.py"

        with patch.object(
            runtime_lifecycle_module.subprocess,
            "Popen",
        ) as popen:
            with self.assertRaises(FileNotFoundError):
                runtime_lifecycle_module.launch_restart_process(
                    missing_primary,
                    REPO_ROOT,
                    {},
                    fallback_target=missing_fallback,
                )

        popen.assert_not_called()

    def test_main_wires_mode_preserving_python_restart_fallback(self) -> None:
        main_source = (REPO_ROOT / "main.py").read_text(
            encoding="utf-8",
        )

        self.assertIn(
            'fallback_target=PROJECT_ROOT / "main.py"',
            main_source,
        )
        self.assertNotIn(
            'fallback_target=PROJECT_ROOT / "evelyn_core" / "start.bat"',
            main_source,
        )
        self.assertIn(
            "container_restart_enabled=runtime_uses_container_restart()",
            main_source,
        )

    def test_container_restart_delegates_to_docker_without_windows_launcher(
        self,
    ) -> None:
        with (
            patch.dict(
                runtime_lifecycle_module.os.environ,
                {"EVELYN_RUNTIME_ROLE": "discord_bot"},
                clear=False,
            ),
            patch.object(
                runtime_lifecycle_module.subprocess,
                "Popen",
            ) as popen,
        ):
            mode = runtime_lifecycle_module.launch_runtime_restart_sequence(
                REPO_ROOT,
                local_only_mode=False,
                discord_enabled=True,
                control_page_port=8799,
                fallback_target=REPO_ROOT / "evelyn_core" / "start.bat",
            )

        self.assertEqual(mode, "container")
        popen.assert_not_called()

    async def test_container_restart_uses_failure_exit_for_docker_relaunch(
        self,
    ) -> None:
        process = self.build_process_deps(
            local_only_mode=False,
            discord_enabled=True,
            container_restart_enabled=True,
            launch_runtime_restart_sequence=Mock(
                return_value="container"
            ),
        )

        await self.build_composition(process=process).restart_bot_process()

        process.exit_process.assert_called_once_with(75)

    async def test_container_restart_waits_for_docker_policy_admission(
        self,
    ) -> None:
        monotonic = Mock(side_effect=[100.0, 102.0])
        sleep = AsyncMock()
        process = self.build_process_deps(
            local_only_mode=False,
            discord_enabled=True,
            container_restart_enabled=True,
            launch_runtime_restart_sequence=Mock(
                return_value="container"
            ),
            monotonic=monotonic,
            container_restart_min_uptime_sec=10.0,
            sleep=sleep,
        )

        await self.build_composition(process=process).restart_bot_process()

        self.assertEqual(
            [call.args for call in sleep.await_args_list],
            [(1.0,), (8.0,)],
        )
        process.exit_process.assert_called_once_with(75)

    def test_restart_request_arms_watchdog_before_work_is_scheduled(
        self,
    ) -> None:
        exit_called = threading.Event()
        exit_codes: list[int] = []

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            exit_process=exit_process,
            terminal_exit_deadline_sec=0.05,
            container_restart_min_uptime_sec=0.0,
        )
        composition = self.build_composition(process=process)

        work = composition.restart_bot_process()
        try:
            self.assertTrue(exit_called.wait(timeout=1.0))
        finally:
            work.close()

        process.launch_runtime_restart_sequence.assert_called_once()
        self.assertEqual(exit_codes, [0])

    def test_restart_watchdogs_keep_process_alive_until_terminal_exit(
        self,
    ) -> None:
        created_timers: list[threading.Timer] = []
        real_timer = threading.Timer

        def capture_timer(*args, **kwargs) -> threading.Timer:
            timer = real_timer(*args, **kwargs)
            created_timers.append(timer)
            return timer

        process = self.build_process_deps(
            terminal_exit_deadline_sec=10.0,
        )
        composition = self.build_composition(process=process)
        with patch.object(
            threading,
            "Timer",
            side_effect=capture_timer,
        ):
            work = composition.restart_bot_process()

        try:
            self.assertEqual(len(created_timers), 2)
            self.assertTrue(
                all(not timer.daemon for timer in created_timers)
            )
        finally:
            for timer in created_timers:
                timer.cancel()
            work.close()

    async def test_container_admission_cannot_be_bypassed_by_task_cancel(
        self,
    ) -> None:
        admission_entered = asyncio.Event()
        release_admission = asyncio.Event()
        exit_codes: list[int] = []

        async def sleep(delay: float) -> None:
            if delay == 1.0:
                return
            admission_entered.set()
            await release_admission.wait()

        process = self.build_process_deps(
            local_only_mode=False,
            discord_enabled=True,
            container_restart_enabled=True,
            launch_runtime_restart_sequence=Mock(
                return_value="container"
            ),
            monotonic=Mock(side_effect=[100.0, 100.0]),
            container_restart_min_uptime_sec=10.0,
            sleep=sleep,
            exit_process=exit_codes.append,
        )
        task = asyncio.create_task(
            self.build_composition(
                process=process
            ).restart_bot_process()
        )
        await admission_entered.wait()

        task.cancel()
        await asyncio.sleep(0)

        self.assertEqual(exit_codes, [])
        process.launch_runtime_restart_sequence.assert_not_called()

        release_admission.set()
        await task

        process.launch_runtime_restart_sequence.assert_called_once()
        self.assertEqual(exit_codes, [75])

    async def test_cancelled_admission_sleeper_defers_to_soft_launcher_timer(
        self,
    ) -> None:
        admission_child_cancelled = asyncio.Event()
        exit_called = threading.Event()
        exit_codes: list[int] = []

        async def sleep(delay: float) -> None:
            if delay == 1.0:
                return
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            admission_child_cancelled.set()
            await asyncio.sleep(0)

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            local_only_mode=False,
            discord_enabled=True,
            container_restart_enabled=True,
            launch_runtime_restart_sequence=Mock(
                return_value="container"
            ),
            monotonic=Mock(side_effect=[100.0, 100.0]),
            container_restart_min_uptime_sec=0.1,
            terminal_exit_deadline_sec=0.05,
            sleep=sleep,
            exit_process=exit_process,
        )
        task = asyncio.create_task(
            self.build_composition(
                process=process
            ).restart_bot_process()
        )
        await admission_child_cancelled.wait()
        await asyncio.sleep(0)

        self.assertEqual(exit_codes, [])
        process.launch_runtime_restart_sequence.assert_not_called()

        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(exit_codes, [])
        process.launch_runtime_restart_sequence.assert_not_called()
        self.assertTrue(
            await asyncio.to_thread(exit_called.wait, 1.0)
        )

        process.launch_runtime_restart_sequence.assert_called_once()
        self.assertEqual(exit_codes, [75])

    async def test_concurrent_restart_requests_share_one_terminal_owner(
        self,
    ) -> None:
        cleanup_entered = asyncio.Event()
        release_cleanup = asyncio.Event()

        async def shutdown_lease(_reason: str) -> None:
            cleanup_entered.set()
            await release_cleanup.wait()

        process = self.build_process_deps(
            shutdown_minecraft_world_lease=shutdown_lease,
        )
        composition = self.build_composition(process=process)
        first = asyncio.create_task(composition.restart_bot_process())
        await cleanup_entered.wait()
        second = asyncio.create_task(composition.restart_bot_process())
        await asyncio.sleep(0)
        release_cleanup.set()
        await asyncio.gather(first, second)

        process.flush_session_continuity.assert_called_once_with()
        process.launch_runtime_restart_sequence.assert_called_once()
        process.exit_process.assert_called_once_with(0)

    async def test_restart_and_shutdown_share_first_terminal_owner(
        self,
    ) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)
        restart_work = composition.restart_bot_process()

        await composition.shutdown_bot_process()
        await restart_work

        process.flush_session_continuity.assert_called_once_with()
        process.launch_runtime_restart_sequence.assert_called_once_with(
            REPO_ROOT,
            local_only_mode=True,
            discord_enabled=False,
            control_page_port=8799,
            fallback_target=REPO_ROOT / "evelyn_core" / "start.bat",
        )
        process.exit_process.assert_called_once_with(0)

    async def test_shutdown_and_restart_share_first_terminal_owner(
        self,
    ) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)
        shutdown_work = composition.shutdown_bot_process()

        await composition.restart_bot_process()
        await shutdown_work

        process.flush_session_continuity.assert_called_once_with()
        process.launch_runtime_restart_sequence.assert_not_called()
        process.exit_process.assert_called_once_with(0)

    async def test_shutdown_watchdog_arm_failure_releases_terminal_claim(
        self,
    ) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)

        with patch.object(
            threading.Timer,
            "start",
            side_effect=RuntimeError("timer unavailable"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "timer unavailable",
            ):
                composition.shutdown_bot_process()

        await composition.shutdown_bot_process()

        process.flush_session_continuity.assert_called_once_with()
        process.exit_process.assert_called_once_with(0)

    async def test_shutdown_watchdog_spawn_then_arm_failure_is_cancelled(
        self,
    ) -> None:
        real_start = threading.Timer.start
        timers: list[threading.Timer] = []
        exit_codes: list[int] = []

        def start_then_raise(timer: threading.Timer) -> None:
            timers.append(timer)
            real_start(timer)
            raise KeyboardInterrupt("timer arm interrupted")

        process = self.build_process_deps(
            exit_process=exit_codes.append,
            terminal_exit_deadline_sec=0.02,
        )
        composition = self.build_composition(process=process)

        with patch.object(
            threading.Timer,
            "start",
            new=start_then_raise,
        ):
            with self.assertRaisesRegex(
                KeyboardInterrupt,
                "timer arm interrupted",
            ):
                composition.shutdown_bot_process()

        for timer in timers:
            timer.join(timeout=1.0)
            self.assertFalse(timer.is_alive())
        self.assertEqual(exit_codes, [])

        await composition.shutdown_bot_process()

        self.assertEqual(exit_codes, [0])

    async def test_restart_launcher_spawn_then_arm_failure_is_cancelled(
        self,
    ) -> None:
        real_start = threading.Timer.start
        timers: list[threading.Timer] = []
        exit_codes: list[int] = []

        def fail_second_start(timer: threading.Timer) -> None:
            timers.append(timer)
            real_start(timer)
            if len(timers) == 2:
                raise KeyboardInterrupt("launcher arm interrupted")

        process = self.build_process_deps(
            exit_process=exit_codes.append,
            terminal_exit_deadline_sec=0.05,
        )
        composition = self.build_composition(process=process)

        with patch.object(
            threading.Timer,
            "start",
            new=fail_second_start,
        ):
            with self.assertRaisesRegex(
                KeyboardInterrupt,
                "launcher arm interrupted",
            ):
                composition.restart_bot_process()

        for timer in timers:
            timer.join(timeout=1.0)
            self.assertFalse(timer.is_alive())
        process.launch_runtime_restart_sequence.assert_not_called()
        self.assertEqual(exit_codes, [])

        await composition.shutdown_bot_process()

        self.assertEqual(exit_codes, [0])

    async def test_restart_setup_failure_releases_terminal_claim(
        self,
    ) -> None:
        process = self.build_process_deps(
            container_restart_enabled=True,
            container_restart_min_uptime_sec="invalid",
        )
        composition = self.build_composition(process=process)

        with self.assertRaises(ValueError):
            composition.restart_bot_process()

        await composition.shutdown_bot_process()

        process.flush_session_continuity.assert_called_once_with()
        process.launch_runtime_restart_sequence.assert_not_called()
        process.exit_process.assert_called_once_with(0)

    async def test_shutdown_attempts_all_voice_cleanup_and_always_exits(self) -> None:
        events: list[object] = []
        guilds = [
            SimpleNamespace(voice_client=FakeVoiceClient(events, fail=True)),
            SimpleNamespace(voice_client=FakeVoiceClient(events)),
            SimpleNamespace(voice_client=None),
        ]
        process = self.build_process_deps(
            flush_session_continuity=lambda: events.append("flush_continuity"),
            shutdown_minecraft_world_lease=(
                lambda reason: _record_async(
                    events,
                    ("stop_minecraft", reason),
                )
            ),
            bot_guilds=lambda: guilds,
            exit_process=lambda code: events.append(("exit", code)),
        )

        await self.build_composition(process=process).shutdown_bot_process()

        self.assertEqual(events.count("stop_listening"), 2)
        self.assertEqual(events.count(("disconnect", True)), 2)
        self.assertEqual(events[0], "flush_continuity")
        self.assertEqual(
            events[1],
            ("stop_minecraft", "shutdown"),
        )
        self.assertEqual(events[-1], ("exit", 0))
        process.stop_control_page_background_tasks.assert_called_once_with()
        process.stop_local_mic_service.assert_called_once_with()

    def test_shutdown_terminal_callback_survives_permanently_stalled_continuity_flush(
        self,
    ) -> None:
        flush_entered = threading.Event()
        release_flush = threading.Event()
        exit_called = threading.Event()
        exit_codes: list[int] = []
        worker_errors: list[BaseException] = []

        def stalled_flush() -> None:
            flush_entered.set()
            release_flush.wait()

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            flush_session_continuity=stalled_flush,
            exit_process=exit_process,
            terminal_exit_deadline_sec=0.05,
        )
        composition = self.build_composition(process=process)

        def run_shutdown() -> None:
            try:
                asyncio.run(composition.shutdown_bot_process())
            except BaseException as exc:
                worker_errors.append(exc)

        worker = threading.Thread(target=run_shutdown, daemon=True)
        worker.start()
        try:
            self.assertTrue(flush_entered.wait(timeout=1.0))
            terminal_called_before_release = exit_called.wait(timeout=1.0)
        finally:
            release_flush.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertTrue(terminal_called_before_release)
        self.assertEqual(exit_codes, [0])

    def test_restart_watchdog_delegates_to_docker_before_stalled_flush_returns(
        self,
    ) -> None:
        flush_entered = threading.Event()
        release_flush = threading.Event()
        exit_called = threading.Event()
        exit_codes: list[int] = []
        worker_errors: list[BaseException] = []
        launcher = Mock(return_value="container")

        def stalled_flush() -> None:
            flush_entered.set()
            release_flush.wait()

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            flush_session_continuity=stalled_flush,
            launch_runtime_restart_sequence=launcher,
            exit_process=exit_process,
            terminal_exit_deadline_sec=0.05,
            container_restart_min_uptime_sec=0.0,
        )
        composition = self.build_composition(process=process)

        def run_restart() -> None:
            try:
                asyncio.run(composition.restart_bot_process())
            except BaseException as exc:
                worker_errors.append(exc)

        worker = threading.Thread(target=run_restart, daemon=True)
        worker.start()
        try:
            self.assertTrue(flush_entered.wait(timeout=1.0))
            launcher_called_before_release = launcher.called or (
                exit_called.wait(timeout=1.0) and launcher.called
            )
            terminal_called_before_release = exit_called.wait(timeout=1.0)
        finally:
            release_flush.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertTrue(launcher_called_before_release)
        self.assertTrue(terminal_called_before_release)
        launcher.assert_called_once()
        self.assertEqual(exit_codes, [75])

    def test_restart_watchdog_exits_even_when_launcher_itself_stalls(
        self,
    ) -> None:
        launcher_entered = threading.Event()
        release_launcher = threading.Event()
        exit_called = threading.Event()
        exit_codes: list[int] = []
        worker_errors: list[BaseException] = []

        def stalled_launcher(*_args, **_kwargs) -> str:
            launcher_entered.set()
            release_launcher.wait()
            return "container"

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            launch_runtime_restart_sequence=stalled_launcher,
            exit_process=exit_process,
            terminal_exit_deadline_sec=0.05,
            container_restart_min_uptime_sec=0.0,
        )
        composition = self.build_composition(process=process)

        def run_restart() -> None:
            try:
                asyncio.run(composition.restart_bot_process())
            except BaseException as exc:
                worker_errors.append(exc)

        worker = threading.Thread(target=run_restart, daemon=True)
        worker.start()
        try:
            self.assertTrue(launcher_entered.wait(timeout=1.0))
            terminal_called_before_release = exit_called.wait(timeout=1.0)
        finally:
            release_launcher.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertTrue(terminal_called_before_release)
        self.assertEqual(exit_codes, [75])

    async def test_restart_launcher_failure_still_forces_failure_exit(
        self,
    ) -> None:
        private_error = "PRIVATE launcher token"
        process = self.build_process_deps(
            launch_runtime_restart_sequence=Mock(
                side_effect=RuntimeError(private_error)
            ),
        )

        await self.build_composition(process=process).restart_bot_process()

        process.exit_process.assert_called_once_with(75)
        process.log.assert_called_once_with(
            "[RESTART] launch_failed errorType=RuntimeError"
        )
        self.assertNotIn(private_error, repr(process.log.call_args_list))

    async def test_restart_launcher_and_logger_failure_still_exits_once(
        self,
    ) -> None:
        private_error = "PRIVATE launcher token"
        logged: list[str] = []

        def broken_log(message: str) -> None:
            logged.append(message)
            raise BrokenPipeError("PRIVATE log sink")

        process = self.build_process_deps(
            launch_runtime_restart_sequence=Mock(
                side_effect=RuntimeError(private_error)
            ),
            log=broken_log,
        )

        await self.build_composition(process=process).restart_bot_process()

        process.exit_process.assert_called_once_with(75)
        self.assertEqual(
            logged,
            ["[RESTART] launch_failed errorType=RuntimeError"],
        )
        self.assertNotIn(private_error, repr(logged))

    async def test_unknown_restart_mode_fails_closed(self) -> None:
        process = self.build_process_deps(
            launch_runtime_restart_sequence=Mock(
                return_value="unexpected"
            ),
        )

        await self.build_composition(process=process).restart_bot_process()

        process.exit_process.assert_called_once_with(75)
        process.log.assert_called_once_with(
            "[RESTART] launcher_mode_invalid"
        )

    async def test_local_only_mode_starts_services_and_waits(self) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)
        composition.ensure_startup_components_ready = AsyncMock()

        await composition.run_local_only_mode()

        process.mark_startup_component.assert_called_once_with(
            "discord_gateway", "done", "disabled by DISCORD_ENABLED=false"
        )
        process.start_control_page_server.assert_awaited_once_with()
        composition.ensure_startup_components_ready.assert_awaited_once_with()
        process.ensure_local_mic_service_started.assert_awaited_once_with()
        process.ensure_vision_watch_started.assert_called_once_with()
        process.ensure_control_page_background_tasks_started.assert_awaited_once_with()
        process.wait_forever.assert_awaited_once_with()
        process.log.assert_any_call("[LOCAL MODE] ready url=http://127.0.0.1:8799")

    async def test_local_only_archive_starts_generation_then_purge_and_stops_worker(
        self,
    ) -> None:
        events: list[str] = []
        worker_started = asyncio.Event()

        async def begin_generation() -> str:
            events.append("generation")
            return "archive-generation-1"

        async def purge_worker() -> None:
            events.append("purge-started")
            worker_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("purge-stopped")

        async def wait_forever() -> None:
            await worker_started.wait()
            events.append("wait")

        process = self.build_process_deps(
            begin_conversation_archive_generation=begin_generation,
            run_conversation_archive_purge_worker=purge_worker,
            wait_forever=wait_forever,
        )
        composition = self.build_composition(process=process)
        composition.ensure_startup_components_ready = AsyncMock()

        await composition.run_local_only_mode()

        self.assertLess(events.index("generation"), events.index("purge-started"))
        self.assertLess(events.index("purge-started"), events.index("wait"))
        self.assertEqual(events[-1], "purge-stopped")

    async def test_local_only_archive_invalid_generation_fails_closed(
        self,
    ) -> None:
        begin_generation = AsyncMock(return_value="")
        purge_worker = AsyncMock()
        process = self.build_process_deps(
            begin_conversation_archive_generation=begin_generation,
            run_conversation_archive_purge_worker=purge_worker,
        )
        composition = self.build_composition(process=process)
        composition.ensure_startup_components_ready = AsyncMock()

        with self.assertRaisesRegex(
            RuntimeError,
            "^conversation_archive_startup_failed$",
        ):
            await composition.run_local_only_mode()

        begin_generation.assert_awaited_once_with()
        purge_worker.assert_not_called()
        process.wait_forever.assert_not_awaited()
        process.log.assert_any_call(
            "[STARTUP] conversation_archive_fail errorType=RuntimeError"
        )

    async def test_local_only_control_page_failure_is_marked_and_raised(self) -> None:
        private_error = "PRIVATE_CONTROL_PAGE_START C:\\private\\token"
        process = self.build_process_deps(
            start_control_page_server=AsyncMock(side_effect=RuntimeError(private_error)),
        )

        with self.assertRaisesRegex(RuntimeError, "^Control Page start failed$") as raised:
            await self.build_composition(process=process).run_local_only_mode()

        process.mark_startup_component.assert_any_call(
            "control_api", "failed", "control_page_start_failed:RuntimeError"
        )
        process.log.assert_any_call(
            "[CONTROL PAGE] start_fail "
            "errorCode=control_page_start_failed errorType=RuntimeError"
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn(private_error, repr(process.mark_startup_component.call_args_list))
        self.assertNotIn(private_error, repr(process.log.call_args_list))
        process.wait_forever.assert_not_awaited()

    def test_shutdown_schedulers_preserve_project_root_and_delay(self) -> None:
        stack_process = self.build_process_deps()
        local_process = self.build_process_deps()
        stack_composition = self.build_composition(process=stack_process)
        local_composition = self.build_composition(process=local_process)

        self.assertTrue(
            stack_composition.schedule_evelyn_stack_shutdown(delay_ms=4000)
        )
        self.assertTrue(
            local_composition.schedule_evelyn_local_shutdown(delay_ms=2000)
        )

        stack_process.schedule_stack_shutdown.assert_called_once_with(
            REPO_ROOT,
            delay_ms=4000,
        )
        local_process.schedule_local_shutdown.assert_called_once_with(
            REPO_ROOT,
            delay_ms=2000,
        )
        for watchdog in (
            stack_composition._scheduled_terminal_watchdogs
            + local_composition._scheduled_terminal_watchdogs
        ):
            watchdog.cancel()

    async def test_successful_scheduled_shutdown_blocks_async_terminal_owners(
        self,
    ) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)

        self.assertTrue(composition.schedule_evelyn_stack_shutdown())
        await composition.restart_bot_process()
        await composition.shutdown_bot_process()

        process.flush_session_continuity.assert_not_called()
        process.launch_runtime_restart_sequence.assert_not_called()
        process.exit_process.assert_not_called()
        for watchdog in composition._scheduled_terminal_watchdogs:
            watchdog.cancel()

    def test_successful_scheduled_shutdown_has_fail_stop_watchdog(
        self,
    ) -> None:
        exit_called = threading.Event()
        exit_codes: list[int] = []

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            exit_process=exit_process,
            terminal_exit_deadline_sec=0.05,
        )
        composition = self.build_composition(process=process)

        self.assertTrue(composition.schedule_evelyn_stack_shutdown())
        self.assertTrue(exit_called.wait(timeout=1.0))

        self.assertEqual(exit_codes, [0])

    def test_stalled_shutdown_scheduler_is_already_watchdog_bounded(
        self,
    ) -> None:
        schedule_entered = threading.Event()
        release_schedule = threading.Event()
        exit_called = threading.Event()
        exit_codes: list[int] = []
        results: list[bool] = []

        def stalled_schedule(*_args, **_kwargs) -> bool:
            schedule_entered.set()
            release_schedule.wait()
            return True

        def exit_process(code: int) -> None:
            exit_codes.append(code)
            exit_called.set()

        process = self.build_process_deps(
            schedule_stack_shutdown=stalled_schedule,
            exit_process=exit_process,
            terminal_exit_deadline_sec=0.05,
        )
        composition = self.build_composition(process=process)
        worker = threading.Thread(
            target=lambda: results.append(
                composition.schedule_evelyn_stack_shutdown()
            ),
            daemon=True,
        )
        worker.start()
        try:
            self.assertTrue(schedule_entered.wait(timeout=1.0))
            terminal_called_before_release = exit_called.wait(
                timeout=1.0
            )
        finally:
            release_schedule.set()
            worker.join(timeout=1.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(terminal_called_before_release)
        self.assertEqual(exit_codes, [0])
        self.assertEqual(results, [True])

    async def test_async_terminal_owner_blocks_scheduled_shutdown(self) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)
        restart_work = composition.restart_bot_process()

        self.assertFalse(composition.schedule_evelyn_stack_shutdown())
        await restart_work

        process.schedule_stack_shutdown.assert_not_called()
        process.launch_runtime_restart_sequence.assert_called_once()
        process.exit_process.assert_called_once_with(0)

    async def test_failed_scheduled_shutdown_releases_terminal_claim(self) -> None:
        process = self.build_process_deps(
            schedule_stack_shutdown=Mock(return_value=False),
        )
        composition = self.build_composition(process=process)

        self.assertFalse(composition.schedule_evelyn_stack_shutdown())
        await composition.restart_bot_process()

        process.launch_runtime_restart_sequence.assert_called_once()
        process.exit_process.assert_called_once_with(0)

    async def test_scheduler_watchdog_arm_failure_releases_terminal_claim(
        self,
    ) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)

        with patch.object(
            threading.Timer,
            "start",
            side_effect=RuntimeError("timer unavailable"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "timer unavailable",
            ):
                composition.schedule_evelyn_stack_shutdown()

        await composition.shutdown_bot_process()

        process.schedule_stack_shutdown.assert_not_called()
        process.flush_session_continuity.assert_called_once_with()
        process.exit_process.assert_called_once_with(0)

    def test_control_page_reads_live_startup_ready_state(self) -> None:
        ready = False
        deps = ControlPageCompositionDeps(
            ui=Mock(),
            guild_selection=Mock(),
            welcome=Mock(),
            minecraft_live_snapshot=Mock(),
            minecraft_snapshot=Mock(),
            background_tasks=Mock(),
            runtime_services=Mock(),
            status=Mock(),
            tool=Mock(),
            search=Mock(),
            text=Mock(),
            input=Mock(),
            server_start=Mock(),
            build_voice_continuity_snapshot=Mock(),
            cheap_tool_decision=Mock(),
            welcome_locks={},
            startup_component_state={},
            startup_steps=(),
            startup_components_ready=lambda: ready,
            discord_enabled=False,
            discord_ready=Mock(return_value=False),
            control_api_available=Mock(return_value=True),
            now=Mock(return_value=0.0),
        )
        composition = ControlPageComposition(deps)

        with patch(
            "evelyn_core.control_page_composition_runtime.build_control_page_boot_progress_payload",
            return_value={},
        ) as build_progress:
            composition.build_boot_progress({}, guild_available=False)
            ready = True
            composition.build_boot_progress({}, guild_available=False)

        self.assertEqual(
            [call.kwargs["startup_components_ready"] for call in build_progress.call_args_list],
            [False, True],
        )

    def test_main_uses_explicit_lifecycle_composition_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "runtime_lifecycle_composition.py"
        ).read_text(encoding="utf-8")

        self.assertIn("runtime_lifecycle_composition = RuntimeLifecycleComposition(", source)
        self.assertIn(
            "ensure_startup_components_ready = "
            "runtime_lifecycle_composition.ensure_startup_components_ready",
            source,
        )
        self.assertIn(
            "mark_startup_component=lambda *args, **kwargs: mark_startup_component(*args, **kwargs)",
            source,
        )
        self.assertIn(
            "lambda: conversation_archive_gate.begin_generation()",
            source,
        )
        self.assertIn(
            "lambda: conversation_archive.run_purge_owner_loop()",
            source,
        )
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
