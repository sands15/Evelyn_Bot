from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

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
            launch_runtime_restart_sequence=Mock(return_value="launcher"),
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
        startup.warmup_llm.assert_awaited_once_with()
        startup.warmup_tts_server.assert_awaited_once_with()
        self.assertEqual(composition.voice_path_warmup_done["voice:1:2"], 123.0)

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
            launch_runtime_restart_sequence=lambda *args, **kwargs: events.append(
                ("launch", args, kwargs)
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

    async def test_local_only_control_page_failure_is_marked_and_raised(self) -> None:
        process = self.build_process_deps(
            start_control_page_server=AsyncMock(side_effect=RuntimeError("bind failed")),
        )

        with self.assertRaisesRegex(RuntimeError, "bind failed"):
            await self.build_composition(process=process).run_local_only_mode()

        process.mark_startup_component.assert_any_call(
            "control_api", "failed", "RuntimeError('bind failed')"
        )
        process.wait_forever.assert_not_awaited()

    def test_shutdown_schedulers_preserve_project_root_and_delay(self) -> None:
        process = self.build_process_deps()
        composition = self.build_composition(process=process)

        self.assertTrue(composition.schedule_evelyn_stack_shutdown(delay_ms=4000))
        self.assertTrue(composition.schedule_evelyn_local_shutdown(delay_ms=2000))

        process.schedule_stack_shutdown.assert_called_once_with(REPO_ROOT, delay_ms=4000)
        process.schedule_local_shutdown.assert_called_once_with(REPO_ROOT, delay_ms=2000)

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
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
