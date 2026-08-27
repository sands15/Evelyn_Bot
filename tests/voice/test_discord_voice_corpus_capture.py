from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


from tools.discord_voice_corpus_capture import (
    DEFAULT_CLIP_COUNT,
    DEFAULT_TTL_SEC,
    DISCORD_TOKEN_ENV,
    CaptureConfig,
    CaptureFailure,
    CaptureResult,
    CorpusCapture,
    VoiceLeaseBinding,
    build_parser,
    main,
    parse_config,
    prepare_private_output_dir,
    read_discord_token,
    shutdown_capture,
    wait_for_voice_lease_release_confirmation,
)


class FakeMember:
    def __init__(self, member_id: int, *, bot: bool = False) -> None:
        self.id = member_id
        self.bot = bot
        self.guild = None


class FakeChannel:
    def __init__(self, members: list[FakeMember]) -> None:
        self.id = 1234
        self.guild = SimpleNamespace(id=4321, voice_client=None)
        for member in members:
            member.guild = self.guild
        self.voice_states = {
            member.id: SimpleNamespace()
            for member in members
        }

    def add(self, member: FakeMember) -> None:
        member.guild = self.guild
        self.voice_states[member.id] = SimpleNamespace()


def stereo_pcm(*, frames: int, value: int) -> bytes:
    samples = array("h")
    for _ in range(frames):
        samples.extend((value, value // 2))
    return samples.tobytes()


def bind_capture_listener(
    capture: CorpusCapture,
    *,
    channel: FakeChannel,
    channel_id: int = 1234,
) -> dict[str, object]:
    channel.id = channel_id
    voice_client = SimpleNamespace(_listener_generation=7, channel=channel)
    channel.guild.voice_client = voice_client
    binding = (voice_client, 7, channel_id)
    capture.bind_listener(binding, expected_channel_id=channel_id)
    return {"_voice_listener_binding": binding, "unstable": False}


class CorpusCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_pcm_is_private_mono_pcm16_16k_without_metadata(self) -> None:
        private_canary = b"PRIVATE_TRANSCRIPT_USER_GUILD_CHANNEL_SENTINEL"
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(111111111111111111)
            channel = FakeChannel([owner])
            capture = CorpusCapture(output_dir=output, exact_count=1)
            capture.lock_owner(channel, bot_user_id=999)
            debug_meta = bind_capture_listener(capture, channel=channel)
            debug_meta.update(
                {
                    "transcript": private_canary.decode("ascii"),
                    "user_id": owner.id,
                }
            )

            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                saved = await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=stereo_pcm(frames=4_800, value=1_200),
                    debug_meta=debug_meta,
                )

            self.assertTrue(saved)
            self.assertTrue(capture.completed)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual([path.name for path in output.iterdir()], ["clip-0001.wav"])
            wav_path = output / "clip-0001.wav"
            with wave.open(str(wav_path), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertEqual(wav.getframerate(), 16_000)
                self.assertEqual(wav.getnframes(), 1_600)
            self.assertNotIn(private_canary, wav_path.read_bytes())

    async def test_duplicate_invalid_and_overlong_clips_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(1)
            channel = FakeChannel([owner])
            capture = CorpusCapture(output_dir=output, exact_count=2)
            capture.lock_owner(channel, bot_user_id=999)
            debug_meta = bind_capture_listener(capture, channel=channel)
            pcm = stereo_pcm(frames=4_800, value=900)

            self.assertTrue(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=pcm,
                    debug_meta=debug_meta,
                )
            )
            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=pcm,
                    debug_meta=debug_meta,
                )
            )
            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=b"",
                    debug_meta=debug_meta,
                )
            )
            self.assertFalse(capture._store(b"\0\0" * (16_000 * 30 + 1)))
            self.assertEqual(capture.saved_count, 1)
            self.assertEqual(capture.rejected_count, 3)
            self.assertFalse(capture.done.is_set())

    async def test_exact_count_stops_accepting_more_callbacks(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(2)
            channel = FakeChannel([owner])
            capture = CorpusCapture(output_dir=output, exact_count=2)
            capture.lock_owner(channel, bot_user_id=999)
            debug_meta = bind_capture_listener(capture, channel=channel)

            for value in (600, 800):
                self.assertTrue(
                    await capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=stereo_pcm(frames=4_800, value=value),
                        debug_meta=debug_meta,
                    )
                )
            self.assertTrue(capture.done.is_set())
            self.assertTrue(capture.completed)
            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=stereo_pcm(frames=4_800, value=1_000),
                    debug_meta=debug_meta,
                )
            )
            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["clip-0001.wav", "clip-0002.wav"],
            )

    async def test_owner_lock_requires_one_non_bot_and_fails_on_change(self) -> None:
        owner = FakeMember(3)
        bot = FakeMember(4, bot=True)
        intruder = FakeMember(5)
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            capture = CorpusCapture(output_dir=output, exact_count=1)

            with self.assertRaisesRegex(CaptureFailure, "participant_guard_failed"):
                capture.lock_owner(FakeChannel([bot]), bot_user_id=bot.id)
            with self.assertRaisesRegex(CaptureFailure, "participant_guard_failed"):
                capture.lock_owner(
                    FakeChannel([owner, intruder, bot]),
                    bot_user_id=bot.id,
                )

            channel = FakeChannel([owner, bot])
            capture.lock_owner(channel, bot_user_id=bot.id)
            debug_meta = bind_capture_listener(capture, channel=channel)
            channel.add(intruder)
            saved = await capture.accept_completed_pcm(
                channel=channel,
                member=owner,
                pcm_bytes=stereo_pcm(frames=4_800, value=700),
                debug_meta=debug_meta,
            )
            self.assertFalse(saved)
            self.assertEqual(capture.error_code, "participant_guard_changed")
            self.assertTrue(capture.done.is_set())
            self.assertEqual(list(output.iterdir()), [])

    async def test_callback_fails_closed_for_bot_owner_or_stale_binding(self) -> None:
        owner = FakeMember(6)
        bot = FakeMember(7, bot=True)
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            channel = FakeChannel([owner, bot])
            capture = CorpusCapture(output_dir=output, exact_count=1)
            capture.lock_owner(channel, bot_user_id=bot.id)
            debug_meta = bind_capture_listener(capture, channel=channel)

            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=FakeMember(owner.id, bot=True),
                    pcm_bytes=stereo_pcm(frames=4_800, value=700),
                    debug_meta=debug_meta,
                )
            )
            self.assertEqual(capture.error_code, "participant_guard_changed")

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            channel = FakeChannel([owner, bot])
            capture = CorpusCapture(output_dir=output, exact_count=1)
            capture.lock_owner(channel, bot_user_id=bot.id)
            debug_meta = bind_capture_listener(capture, channel=channel)
            stale_meta = dict(debug_meta)
            stale_meta["_voice_listener_binding"] = (object(), 7, 1234)

            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=stereo_pcm(frames=4_800, value=700),
                    debug_meta=stale_meta,
                )
            )
            self.assertEqual(capture.error_code, "listener_binding_stale")

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            channel = FakeChannel([owner, bot])
            capture = CorpusCapture(output_dir=output, exact_count=1)
            capture.lock_owner(channel, bot_user_id=bot.id)
            debug_meta = bind_capture_listener(capture, channel=channel)
            channel.guild.voice_client._listener_generation += 1

            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=stereo_pcm(frames=4_800, value=700),
                    debug_meta=debug_meta,
                )
            )
            self.assertEqual(capture.error_code, "listener_binding_stale")

    async def test_unstable_clip_is_rejected_and_stt_audio_path_is_reused(self) -> None:
        owner = FakeMember(8)
        bot = FakeMember(9, bot=True)
        pcm = stereo_pcm(frames=4_800, value=700)
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            channel = FakeChannel([owner, bot])
            capture = CorpusCapture(output_dir=output, exact_count=1)
            capture.lock_owner(channel, bot_user_id=bot.id)
            debug_meta = bind_capture_listener(capture, channel=channel)

            unstable_meta = dict(debug_meta)
            unstable_meta["unstable"] = True
            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=pcm,
                    debug_meta=unstable_meta,
                )
            )
            self.assertEqual(capture.rejected_count, 1)
            self.assertEqual(list(output.iterdir()), [])

            canonical = np.linspace(-0.5, 0.5, 1_600, dtype=np.float32)
            with patch(
                "tools.discord_voice_corpus_capture.prepare_stt_audio",
                return_value=canonical,
            ) as prepare:
                self.assertTrue(
                    await capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=pcm,
                        debug_meta=debug_meta,
                    )
                )
            prepare.assert_called_once_with(pcm)
            with wave.open(str(output / "clip-0001.wav"), "rb") as wav:
                self.assertEqual(wav.readframes(1_600), (canonical * 32767.0).astype("<i2").tobytes())


class FakeVoiceClient:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.release = None
        self.listener_token = ""
        self.release_tasks: set[asyncio.Task[None]] = set()
        self.on_user_audio = None

    def bind_voice_input_lease(self, token, release) -> None:
        self.events.append("bind")
        self.listener_token = token
        self.release = release

    def listen(self) -> None:
        self.events.append("listen")

    def stop_listening(self) -> None:
        self.events.append("stop")
        if self.release is None or not self.listener_token:
            return
        token = self.listener_token
        self.listener_token = ""
        task = asyncio.create_task(self.release(token))
        self.release_tasks.add(task)

    async def _drain_voice_input_lease_releases(self) -> None:
        if self.release_tasks:
            await asyncio.gather(*self.release_tasks)
            self.release_tasks.clear()

    async def disconnect(self, *, force: bool = False) -> None:
        self.events.append(f"disconnect:{force}")


class CaptureCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_return_alone_does_not_claim_confirmation(self) -> None:
        release_returned = asyncio.Event()
        canonical_unowned = asyncio.Event()

        async def acquire() -> str:
            return "listener-token-private"

        async def release(_token: str) -> None:
            release_returned.set()

        async def confirm_release() -> None:
            await canonical_unowned.wait()

        binding = VoiceLeaseBinding(
            acquire=acquire,
            release=release,
            confirm_release=confirm_release,
        )
        voice_client = FakeVoiceClient([])
        await binding.acquire_for_capture()
        binding.bind_and_listen(voice_client, lambda *_args, **_kwargs: None)
        shutdown = asyncio.create_task(
            shutdown_capture(
                voice_client=voice_client,
                lease_binding=binding,
                close_bot=lambda: asyncio.sleep(0),
            )
        )
        await release_returned.wait()
        self.assertFalse(binding.release_confirmed.is_set())
        self.assertFalse(shutdown.done())
        canonical_unowned.set()
        await shutdown
        self.assertTrue(binding.release_confirmed.is_set())

    async def test_exact_cleanup_stops_releases_disconnects_and_closes(self) -> None:
        events: list[str] = []
        secret_listener_token = "listener-token-private"

        async def acquire() -> str:
            events.append("acquire")
            return secret_listener_token

        async def release(token: str) -> None:
            self.assertEqual(token, secret_listener_token)
            events.append("release")

        async def close_bot() -> None:
            events.append("close")

        async def confirm_release() -> None:
            events.append("confirm")

        binding = VoiceLeaseBinding(
            acquire=acquire,
            release=release,
            confirm_release=confirm_release,
        )
        voice_client = FakeVoiceClient(events)
        await binding.acquire_for_capture()
        binding.bind_and_listen(voice_client, lambda *_args, **_kwargs: None)
        await shutdown_capture(
            voice_client=voice_client,
            lease_binding=binding,
            close_bot=close_bot,
        )

        self.assertTrue(binding.release_confirmed.is_set())
        self.assertEqual(
            events,
            [
                "acquire",
                "bind",
                "listen",
                "stop",
                "release",
                "confirm",
                "disconnect:True",
                "close",
            ],
        )

    async def test_unbound_acquired_lease_is_released_before_close(self) -> None:
        events: list[str] = []

        async def acquire() -> str:
            events.append("acquire")
            return "pending-private-token"

        async def release(_token: str) -> None:
            events.append("release")

        async def close_bot() -> None:
            events.append("close")

        async def confirm_release() -> None:
            events.append("confirm")

        binding = VoiceLeaseBinding(
            acquire=acquire,
            release=release,
            confirm_release=confirm_release,
        )
        await binding.acquire_for_capture()
        await shutdown_capture(
            voice_client=None,
            lease_binding=binding,
            close_bot=close_bot,
        )
        self.assertEqual(events, ["acquire", "release", "confirm", "close"])

    async def test_release_confirmation_waits_for_exact_canonical_unowned_state(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime_root = Path(root)
            owner_path = runtime_root / "voice_input_lease" / "owner.json"
            owner_path.parent.mkdir()
            instance_id = "b" * 32

            def write_owner(*, state: str) -> None:
                owned = state == "owned"
                owner_path.write_text(
                    json.dumps(
                        {
                            "schema": "voice_input_lease.owner.v1",
                            "state": state,
                            "source": "discord_voice" if owned else "",
                            "instanceId": instance_id if owned else "",
                            "leaseId": "d" * 32 if owned else "",
                            "lastReleasedSource": "" if owned else "discord_voice",
                            "lastReleasedInstanceId": "" if owned else instance_id,
                            "lastReleasedLeaseId": "" if owned else "d" * 32,
                            "updatedAt": 123.0,
                        }
                    ),
                    encoding="utf-8",
                )

            write_owner(state="owned")
            with (
                patch(
                    "tools.discord_voice_corpus_capture.get_runtime_artifacts_root",
                    return_value=runtime_root,
                ),
                patch(
                    "tools.discord_voice_corpus_capture.discord_voice_input_instance_id",
                    return_value=instance_id,
                ),
            ):
                confirmation = asyncio.create_task(
                    wait_for_voice_lease_release_confirmation(
                        timeout_sec=0.5,
                        poll_sec=0.001,
                    )
                )
                await asyncio.sleep(0.01)
                self.assertFalse(confirmation.done())
                write_owner(state="unowned")
                await confirmation

                write_owner(state="owned")
                with self.assertRaisesRegex(
                    CaptureFailure,
                    "voice_lease_release_unconfirmed",
                ):
                    await wait_for_voice_lease_release_confirmation(
                        timeout_sec=0.01,
                        poll_sec=0.001,
                    )


class CaptureCliContractTests(unittest.TestCase):
    def test_cli_bounds_count_ttl_and_has_no_token_value_argument(self) -> None:
        parser = build_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        self.assertIn("--token-stdin", option_strings)
        self.assertNotIn("--token", option_strings)
        config, token_stdin = parse_config(
            [
                "--channel-id",
                "12345678901234567",
                "--output-dir",
                "private-stage",
            ]
        )
        self.assertEqual(config.clip_count, DEFAULT_CLIP_COUNT)
        self.assertEqual(config.ttl_sec, DEFAULT_TTL_SEC)
        self.assertFalse(token_stdin)
        for argv in (
            ["--channel-id", "1", "--output-dir", "x", "--count", "11"],
            ["--channel-id", "1", "--output-dir", "x", "--ttl-seconds", "1800.1"],
        ):
            with self.assertRaisesRegex(CaptureFailure, "invalid_arguments"):
                parse_config(argv)

    def test_stdin_token_is_not_in_argv_output_or_files(self) -> None:
        secret = "DISCORD_TOKEN_PRIVATE_SENTINEL_123456789"
        seen: list[str] = []

        async def runner(config: CaptureConfig, token: str) -> CaptureResult:
            self.assertEqual(token, secret)
            seen.append(token)
            print(secret)
            print(secret, file=os.sys.stderr)
            self.assertEqual(list(config.output_dir.iterdir()), [])
            return CaptureResult(ok=True, code="completed", saved_count=10)

        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "private"
            argv = [
                "--channel-id",
                "12345678901234567",
                "--output-dir",
                str(output),
                "--token-stdin",
            ]
            self.assertNotIn(secret, " ".join(argv))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    argv,
                    stdin=io.StringIO(secret + "\nignored-second-line\n"),
                    runner=runner,
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(seen, [secret])
            self.assertNotIn(secret, stdout.getvalue())
            self.assertNotIn(secret, stderr.getvalue())
            self.assertEqual(stdout.getvalue(), "capture_complete clips=10\n")
            for path in output.rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode("utf-8"), path.read_bytes())

    def test_failure_and_cancel_remove_only_owned_capture_outputs(self) -> None:
        secret = "DISCORD_TOKEN_PRIVATE_SENTINEL_FAILURE"

        async def failed_runner(config: CaptureConfig, _token: str) -> CaptureResult:
            (config.output_dir / "clip-0001.wav").write_bytes(b"partial-success")
            (config.output_dir / ".clip-0002.wav.part").write_bytes(b"partial")
            return CaptureResult(ok=False, code="capture_ttl_expired", saved_count=1)

        async def cancelled_runner(config: CaptureConfig, _token: str) -> CaptureResult:
            (config.output_dir / ".clip-0001.wav.part").write_bytes(b"partial")
            raise asyncio.CancelledError

        for runner, expected_exit in ((failed_runner, 1), (cancelled_runner, 2)):
            with self.subTest(runner=runner.__name__):
                with tempfile.TemporaryDirectory() as root:
                    output = Path(root) / "created-by-tool"
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        exit_code = main(
                            [
                                "--channel-id",
                                "1",
                                "--output-dir",
                                str(output),
                                "--token-stdin",
                            ],
                            stdin=io.StringIO(secret + "\n"),
                            runner=runner,
                        )
                    self.assertEqual(exit_code, expected_exit)
                    self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "preexisting-empty"
            output.mkdir()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "--channel-id",
                        "1",
                        "--output-dir",
                        str(output),
                        "--token-stdin",
                    ],
                    stdin=io.StringIO(secret + "\n"),
                    runner=failed_runner,
                )
            self.assertEqual(exit_code, 1)
            self.assertTrue(output.is_dir())
            self.assertEqual(list(output.iterdir()), [])

    def test_environment_token_is_fallback_and_removed_from_process_env(self) -> None:
        secret = "DISCORD_ENV_PRIVATE_SENTINEL_123456789"
        with patch.dict(os.environ, {DISCORD_TOKEN_ENV: secret}, clear=False):
            loaded = read_discord_token(from_stdin=False, stdin=io.StringIO(""))
            self.assertEqual(loaded, secret)
            self.assertNotIn(DISCORD_TOKEN_ENV, os.environ)

    def test_stdin_token_does_not_fall_back_to_or_retain_environment(self) -> None:
        env_secret = "DISCORD_ENV_MUST_NOT_WIN_123456789"
        stdin_secret = "DISCORD_STDIN_MUST_WIN_123456789"
        with patch.dict(os.environ, {DISCORD_TOKEN_ENV: env_secret}, clear=False):
            loaded = read_discord_token(
                from_stdin=True,
                stdin=io.StringIO(stdin_secret + "\n"),
            )
            self.assertEqual(loaded, stdin_secret)
            self.assertNotIn(DISCORD_TOKEN_ENV, os.environ)

    def test_missing_token_fails_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "private"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.dict(os.environ, {}, clear=True):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exit_code = main(
                        ["--channel-id", "1", "--output-dir", str(output)],
                        stdin=io.StringIO(""),
                    )
            self.assertEqual(exit_code, 2)
            self.assertFalse(output.exists())
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                stderr.getvalue(),
                "capture_failed code=discord_token_missing\n",
            )

    def test_tool_imports_only_capture_and_official_lease_runtime(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "tools"
            / "discord_voice_corpus_capture.py"
        ).read_text(encoding="utf-8")
        self.assertIn("acquire_discord_voice_input_lease", source)
        self.assertIn("release_discord_voice_input_lease", source)
        for forbidden in (
            "evelyn_core.stt_client",
            "evelyn_core.fast_control_api",
            "main.py import",
            "OMNIVOICE_SERVER_URL",
            "LLM_SERVER_URL",
            "STT_SERVICE_URL",
        ):
            self.assertNotIn(forbidden, source)

    def test_docker_context_allowlists_only_the_capture_tool(self) -> None:
        dockerignore = (
            Path(__file__).resolve().parents[2] / ".dockerignore"
        ).read_text(encoding="utf-8")
        self.assertIn("!tools/discord_voice_corpus_capture.py", dockerignore.splitlines())
        self.assertNotIn("!tools/**", dockerignore.splitlines())


if __name__ == "__main__":
    unittest.main()
