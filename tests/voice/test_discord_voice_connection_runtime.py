from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_voice_connection_runtime import (  # noqa: E402
    DiscordVoiceConnectionRuntimeDeps,
    connect_evelyn_voice_client_from_runtime,
    wait_for_internal_voice_reconnect_from_runtime,
)


class FakeVoiceClient:
    def __init__(
        self,
        *,
        channel=None,
        reconnect_active: bool = False,
        reconnect_result: bool = False,
        listener_healthy: bool = False,
        disconnect_error: Exception | None = None,
    ) -> None:
        self.channel = channel
        self.reconnect_active = reconnect_active
        self.reconnect_result = reconnect_result
        self.listener_healthy = listener_healthy
        self.disconnect_error = disconnect_error
        self.disconnect_replacement = None
        self.connected = True
        self.listen_calls = 0
        self.disconnect_calls: list[bool] = []
        self.cleanup_calls = 0
        self.wait_timeouts: list[float] = []
        self.on_user_audio = None

    def is_internal_voice_reconnect_active(self) -> bool:
        return self.reconnect_active

    async def wait_for_internal_voice_reconnect(self, *, timeout: float) -> bool:
        self.wait_timeouts.append(timeout)
        return self.reconnect_result

    def is_connected(self) -> bool:
        return self.connected

    def is_listener_healthy(self) -> bool:
        return self.listener_healthy

    def listen(self) -> None:
        self.listen_calls += 1

    async def disconnect(self, *, force: bool) -> None:
        self.disconnect_calls.append(force)
        if self.disconnect_replacement is not None and self.channel is not None:
            self.channel.guild.voice_client = self.disconnect_replacement
        if self.disconnect_error is not None:
            raise self.disconnect_error
        if self.channel is not None and self.channel.guild.voice_client is self:
            self.channel.guild.voice_client = None

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        if self.channel is not None:
            self.channel.guild.voice_client = None


class FakeGuild:
    def __init__(self, guild_id: int = 77) -> None:
        self.id = guild_id
        self.voice_client = None
        self.change_calls: list[dict] = []

    async def change_voice_state(self, **kwargs) -> None:
        self.change_calls.append(kwargs)


class FakeChannel:
    def __init__(self, guild: FakeGuild, *, name: str = "general") -> None:
        self.guild = guild
        self.name = name
        self.connect_results: list[object] = []
        self.connect_calls: list[dict] = []
        self.reject_when_registered = False

    async def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        if self.reject_when_registered and self.guild.voice_client is not None:
            raise RuntimeError("already connected")
        result = self.connect_results.pop(0)
        if isinstance(result, Exception):
            raise result
        self.guild.voice_client = result
        return result


class DiscordVoiceConnectionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.locks: dict[int, object] = {}
        self.logs: list[str] = []
        self.sleeps: list[float] = []
        self.process_member_audio = lambda *_args, **_kwargs: None

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    def build_deps(self) -> DiscordVoiceConnectionRuntimeDeps:
        return DiscordVoiceConnectionRuntimeDeps(
            voice_client_type=FakeVoiceClient,
            voice_connect_locks=self.locks,
            voice_connect_timeout=3.0,
            voice_connect_retries=2,
            voice_connect_retry_delay_sec=0.25,
            process_member_audio=self.process_member_audio,
            sleep=self.sleep,
            log=self.logs.append,
        )

    async def test_wait_reuses_active_internal_reconnect(self) -> None:
        guild = FakeGuild()
        channel = FakeChannel(guild)
        vc = FakeVoiceClient(channel=channel, reconnect_active=True, reconnect_result=True)
        guild.voice_client = vc

        result = await wait_for_internal_voice_reconnect_from_runtime(
            channel,
            deps=self.build_deps(),
        )

        self.assertIs(result, vc)
        self.assertEqual(vc.wait_timeouts, [5.0])

    async def test_connect_sets_audio_callback_and_arms_listener(self) -> None:
        guild = FakeGuild()
        channel = FakeChannel(guild)
        vc = FakeVoiceClient(channel=channel)
        channel.connect_results = [vc]

        result = await connect_evelyn_voice_client_from_runtime(channel, deps=self.build_deps())

        self.assertIs(result, vc)
        self.assertIs(vc.on_user_audio, self.process_member_audio)
        self.assertEqual(vc.listen_calls, 1)
        self.assertEqual(channel.connect_calls, [{
            "cls": FakeVoiceClient,
            "timeout": 3.0,
            "reconnect": False,
        }])
        self.assertIn(77, self.locks)

    async def test_connect_serializes_stale_cleanup_and_reuses_replacement(self) -> None:
        guild = FakeGuild()
        channel = FakeChannel(guild)
        channel.reject_when_registered = True
        stale = FakeVoiceClient(channel=channel)
        stale.connected = False
        guild.voice_client = stale
        replacement = FakeVoiceClient(channel=channel, listener_healthy=True)
        channel.connect_results = [replacement]
        deps = self.build_deps()

        first, second = await asyncio.gather(
            connect_evelyn_voice_client_from_runtime(channel, deps=deps),
            connect_evelyn_voice_client_from_runtime(channel, deps=deps),
        )

        self.assertIs(first, replacement)
        self.assertIs(second, replacement)
        self.assertEqual(len(channel.connect_calls), 1)
        self.assertEqual(stale.disconnect_calls, [True])
        self.assertEqual(replacement.disconnect_calls, [])

    async def test_connect_cleans_registry_when_stale_disconnect_fails(self) -> None:
        guild = FakeGuild()
        channel = FakeChannel(guild)
        channel.reject_when_registered = True
        stale = FakeVoiceClient(
            channel=channel,
            disconnect_error=RuntimeError("stale disconnect failed"),
        )
        stale.connected = False
        guild.voice_client = stale
        replacement = FakeVoiceClient(channel=channel, listener_healthy=True)
        channel.connect_results = [replacement]

        result = await connect_evelyn_voice_client_from_runtime(
            channel,
            deps=self.build_deps(),
        )

        self.assertIs(result, replacement)
        self.assertEqual(stale.disconnect_calls, [True])
        self.assertEqual(stale.cleanup_calls, 1)
        self.assertEqual(len(channel.connect_calls), 1)

    async def test_connect_reuses_replacement_installed_during_stale_disconnect(self) -> None:
        guild = FakeGuild()
        channel = FakeChannel(guild)
        channel.reject_when_registered = True
        stale = FakeVoiceClient(
            channel=channel,
            disconnect_error=RuntimeError("stale disconnect failed"),
        )
        stale.connected = False
        replacement = FakeVoiceClient(channel=channel, listener_healthy=True)
        stale.disconnect_replacement = replacement
        guild.voice_client = stale

        result = await connect_evelyn_voice_client_from_runtime(
            channel,
            deps=self.build_deps(),
        )

        self.assertIs(result, replacement)
        self.assertEqual(stale.disconnect_calls, [True])
        self.assertEqual(stale.cleanup_calls, 0)
        self.assertEqual(replacement.disconnect_calls, [])
        self.assertEqual(channel.connect_calls, [])

    async def test_failed_attempt_cleans_stale_state_and_retries(self) -> None:
        guild = FakeGuild()
        channel = FakeChannel(guild)
        stale = FakeVoiceClient(channel=channel)
        stale.connected = False
        guild.voice_client = stale
        connected = FakeVoiceClient(channel=channel, listener_healthy=True)
        channel.connect_results = [RuntimeError("first failed"), connected]

        result = await connect_evelyn_voice_client_from_runtime(channel, deps=self.build_deps())

        self.assertIs(result, connected)
        self.assertEqual(stale.disconnect_calls, [True])
        self.assertEqual(guild.change_calls, [{
            "channel": None,
            "self_deaf": False,
            "self_mute": False,
        }])
        self.assertEqual(self.sleeps, [0.25])
        self.assertEqual(len(channel.connect_calls), 2)

    def test_main_delegates_voice_connect_and_reconnect_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "voice_support_composition_runtime.py"
        ).read_text(encoding="utf-8")
        wait_start = source.index("    async def wait_for_internal_voice_reconnect(")
        connect_start = source.index("    async def connect_evelyn_voice_client(", wait_start)
        ensure_start = source.index("    async def ensure_listening_voice_client(", connect_start)

        wait_source = source[wait_start:connect_start]
        connect_source = source[connect_start:ensure_start]
        self.assertIn("wait_for_internal_voice_reconnect_from_runtime(", wait_source)
        self.assertIn("connect_evelyn_voice_client_from_runtime(", connect_source)
        self.assertNotIn("target_channel.connect(", connect_source)
        self.assertNotIn("VOICE_CONNECT_RETRIES", connect_source)


if __name__ == "__main__":
    unittest.main()
