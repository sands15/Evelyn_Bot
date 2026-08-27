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
from unittest.mock import AsyncMock, Mock, patch

import discord
import numpy as np


from tools.discord_voice_corpus_capture import (
    DEFAULT_CLIP_COUNT,
    DEFAULT_TTL_SEC,
    DISCORD_TOKEN_ENV,
    DOMAIN_PHRASES,
    CaptureConfig,
    CaptureDiscordClient,
    CaptureFailure,
    CaptureResult,
    CorpusCapture,
    VoiceLeaseBinding,
    _run_capture,
    build_parser,
    guided_prompt_message,
    guided_retry_message,
    guided_saved_message,
    main,
    parse_config,
    prepare_private_output_dir,
    read_discord_token,
    shutdown_capture,
    status_channel_matches_voice_channel,
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
            self.assertEqual(
                [path.name for path in output.iterdir()],
                ["clip-0001.wav"],
            )
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

    async def test_diagnostic_instability_does_not_override_completed_pcm_contract(self) -> None:
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
            unstable_meta["reasons"] = ["dave_warmup_skips=1"]

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
                        debug_meta=unstable_meta,
                    )
                )
            prepare.assert_called_once_with(pcm)
            self.assertEqual(capture.rejected_count, 0)
            with wave.open(str(output / "clip-0001.wav"), "rb") as wav:
                self.assertEqual(wav.readframes(1_600), (canonical * 32767.0).astype("<i2").tobytes())

    async def test_explicit_transport_corruption_is_rejected(self) -> None:
        owner = FakeMember(10)
        bot = FakeMember(11, bot=True)
        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            channel = FakeChannel([owner, bot])
            capture = CorpusCapture(output_dir=output, exact_count=1)
            capture.lock_owner(channel, bot_user_id=bot.id)
            debug_meta = bind_capture_listener(capture, channel=channel)
            debug_meta.update(
                {
                    "unstable": True,
                    "reasons": [
                        "opus_fail=4",
                        "plc=2",
                        "fec=2",
                        "front_burst_detected",
                        "heavy_trim_ms=220",
                        "burst_trim_ms=140",
                    ],
                }
            )

            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=stereo_pcm(frames=4_800, value=700),
                    debug_meta=debug_meta,
                )
            )
            self.assertEqual(capture.rejected_count, 1)
            self.assertEqual(list(output.iterdir()), [])


class GuidedCorpusCaptureTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _timed_meta(
        capture: CorpusCapture,
        channel: FakeChannel,
        *,
        total_ms: float = 0.0,
    ) -> dict[str, object]:
        metadata = bind_capture_listener(capture, channel=channel)
        metadata["timing"] = {"utterance_total_ms": total_ms}
        return metadata

    @staticmethod
    def _arm_started_meta(
        capture: CorpusCapture,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        metadata["_utterance_started_at_monotonic"] = float(
            capture.armed_at or 0.0
        ) + 0.001
        return metadata

    async def test_prompt_shape_retry_and_saved_index_are_serial(self) -> None:
        messages: list[str] = []

        async def status(message: str) -> None:
            messages.append(message)

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(21)
            channel = FakeChannel([owner])
            capture = CorpusCapture(
                output_dir=output,
                exact_count=len(DOMAIN_PHRASES),
                guided=True,
            )
            capture.lock_owner(channel, bot_user_id=999)
            metadata = self._timed_meta(capture, channel)
            pcm = stereo_pcm(frames=57_600, value=1_200)

            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=pcm,
                    debug_meta=metadata,
                )
            )
            self.assertEqual(messages, [])
            await capture.arm_guided(status)
            self.assertEqual(messages, [guided_prompt_message(0)])

            with patch(
                "tools.discord_voice_corpus_capture.compute_waveform_activity_stats",
                return_value={"voiced_ms": 900.0, "longest_voiced_ms": 400.0},
            ):
                for invalid_pcm in (
                    stereo_pcm(frames=24_000, value=1_200),
                    stereo_pcm(frames=504_000, value=1_200),
                ):
                    self.assertFalse(
                        await capture.accept_completed_pcm(
                            channel=channel,
                            member=owner,
                            pcm_bytes=invalid_pcm,
                            debug_meta=self._arm_started_meta(capture, metadata),
                        )
                    )

            with patch(
                "tools.discord_voice_corpus_capture.compute_waveform_activity_stats",
                return_value={"voiced_ms": 40.0, "longest_voiced_ms": 20.0},
            ):
                self.assertFalse(
                    await capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=pcm,
                        debug_meta=self._arm_started_meta(capture, metadata),
                    )
                )

            with patch(
                "tools.discord_voice_corpus_capture.compute_waveform_activity_stats",
                return_value={"voiced_ms": 900.0, "longest_voiced_ms": 400.0},
            ):
                first_start = float(capture.armed_at or 0.0) + 0.001
                metadata["_utterance_started_at_monotonic"] = first_start
                results = await asyncio.gather(
                    capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=pcm,
                        debug_meta=metadata,
                    ),
                    capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=stereo_pcm(frames=57_600, value=1_300),
                        debug_meta=metadata,
                    ),
                )

            self.assertEqual(results, [True, False])
            self.assertEqual(capture.saved_count, 1)
            self.assertEqual([path.name for path in output.iterdir()], ["clip-0001.wav"])
            self.assertIn(DOMAIN_PHRASES[1], messages[-1])
            self.assertEqual(capture.rejected_count, 5)

    async def test_missing_guided_start_timing_is_terminal(self) -> None:
        messages: list[str] = []

        async def status(message: str) -> None:
            messages.append(message)

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(23)
            channel = FakeChannel([owner])
            capture = CorpusCapture(
                output_dir=output,
                exact_count=len(DOMAIN_PHRASES),
                guided=True,
            )
            capture.lock_owner(channel, bot_user_id=999)
            metadata = self._timed_meta(capture, channel)
            await capture.arm_guided(status)
            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=stereo_pcm(frames=57_600, value=1_200),
                    debug_meta=metadata,
                )
            )
            self.assertEqual(capture.error_code, "guided_timing_invalid")
            self.assertTrue(capture.done.is_set())
            self.assertEqual(list(output.iterdir()), [])

    async def test_terminal_failure_status_follows_inflight_saved_status(self) -> None:
        messages: list[str] = []
        saved_status_started = asyncio.Event()
        release_saved_status = asyncio.Event()

        async def status(message: str) -> None:
            if message == guided_saved_message(1):
                saved_status_started.set()
                await asyncio.wait_for(release_saved_status.wait(), timeout=2.0)
            messages.append(message)

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(24)
            channel = FakeChannel([owner])
            capture = CorpusCapture(
                output_dir=output,
                exact_count=len(DOMAIN_PHRASES),
                guided=True,
            )
            capture.lock_owner(channel, bot_user_id=999)
            metadata = self._timed_meta(capture, channel)
            await capture.arm_guided(status)
            self._arm_started_meta(capture, metadata)
            with patch(
                "tools.discord_voice_corpus_capture.compute_waveform_activity_stats",
                return_value={"voiced_ms": 900.0, "longest_voiced_ms": 400.0},
            ):
                accept_task = asyncio.create_task(
                    capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=stereo_pcm(frames=57_600, value=1_200),
                        debug_meta=metadata,
                    )
                )
                await asyncio.wait_for(saved_status_started.wait(), timeout=2.0)
                capture.fail("capture_ttl_expired")
                failure_task = asyncio.create_task(capture.notify_failure(status))
                await asyncio.sleep(0)
                self.assertFalse(failure_task.done())
                release_saved_status.set()
                await asyncio.gather(accept_task, failure_task)

            self.assertIn("저장 완료", messages[-2])
            self.assertIn("실패", messages[-1])
            self.assertEqual(capture.error_code, "capture_ttl_expired")

    async def test_pre_prompt_utterance_and_status_failure_fail_closed(self) -> None:
        messages: list[str] = []

        async def status(message: str) -> None:
            messages.append(message)
            if len(messages) > 1:
                raise RuntimeError("fixed test send failure")

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            owner = FakeMember(22)
            channel = FakeChannel([owner])
            capture = CorpusCapture(
                output_dir=output,
                exact_count=len(DOMAIN_PHRASES),
                guided=True,
            )
            capture.lock_owner(channel, bot_user_id=999)
            metadata = self._timed_meta(capture, channel)
            await capture.arm_guided(status)
            pcm = stereo_pcm(frames=57_600, value=1_200)

            metadata["_utterance_started_at_monotonic"] = float(
                capture.armed_at or 0.0
            ) - 1.0

            self.assertFalse(
                await capture.accept_completed_pcm(
                    channel=channel,
                    member=owner,
                    pcm_bytes=pcm,
                    debug_meta=metadata,
                )
            )
            self.assertTrue(capture.armed)
            with patch(
                "tools.discord_voice_corpus_capture.compute_waveform_activity_stats",
                return_value={"voiced_ms": 900.0, "longest_voiced_ms": 400.0},
            ):
                self.assertFalse(
                    await capture.accept_completed_pcm(
                        channel=channel,
                        member=owner,
                        pcm_bytes=pcm,
                        debug_meta=self._arm_started_meta(capture, metadata),
                    )
                )
            self.assertEqual(capture.error_code, "status_send_failed")
            self.assertTrue(capture.done.is_set())
            self.assertEqual(capture.saved_count, 1)

    def test_fixed_domain_messages_are_bounded_and_content_safe(self) -> None:
        self.assertEqual(
            DOMAIN_PHRASES,
            (
                "이블린, 다이아몬드 곡괭이를 찾아줘",
                "이블린, 참나무 원목을 열두 개 모아줘",
                "이블린, 제작대에서 빵 세 개를 만들어줘",
                "이블린, 크리퍼와 스켈레톤을 피해줘",
                "이블린, Control Page 상태를 확인해줘",
                "이블린, Discord 음성 연결을 다시 확인해줘",
                "이블린, Main LLM과 Qwen ASR 상태를 알려줘",
                "이블린, GPU 일 번의 VRAM을 확인해줘",
                "이블린, 마인크래프트 Voyager 상태만 보여줘",
                "이블린, 오후 세 시 이십오 분에 열두 개를 세어줘",
            ),
        )
        self.assertEqual(len(set(DOMAIN_PHRASES)), 10)
        messages = [guided_prompt_message(index) for index in range(10)]
        messages += [guided_retry_message(index, "transport") for index in range(10)]
        messages += [guided_saved_message(index) for index in range(1, 11)]
        rendered = "\n".join(messages)
        for phrase in DOMAIN_PHRASES:
            self.assertIn(phrase, rendered)
        for canary in (
            "PRIVATE_TRANSCRIPT_SENTINEL",
            "123456789012345678",
            "DISCORD_TOKEN_PRIVATE_SENTINEL",
            "runtime_artifacts/private-capture",
        ):
            self.assertNotIn(canary, rendered)
        self.assertTrue(all(0 < len(message) <= 2_000 for message in messages))
        self.assertNotIn("성공", guided_saved_message(10))

    def test_status_channel_must_be_messageable_and_in_same_guild(self) -> None:
        status = Mock(spec=discord.TextChannel)
        status.guild = SimpleNamespace(id=77)
        voice = SimpleNamespace(guild=SimpleNamespace(id=77))
        self.assertTrue(status_channel_matches_voice_channel(status, voice))
        status.guild = SimpleNamespace(id=78)
        self.assertFalse(status_channel_matches_voice_channel(status, voice))
        self.assertFalse(
            status_channel_matches_voice_channel(SimpleNamespace(), voice)
        )

    def test_real_guided_shape_boundaries(self) -> None:
        def sine_pcm(seconds: float) -> bytes:
            samples = np.arange(int(16_000 * seconds), dtype=np.float32)
            audio = np.sin((2.0 * np.pi * 220.0 * samples) / 16_000.0) * 0.2
            return (audio * 32767.0).astype("<i2").tobytes()

        self.assertEqual(CorpusCapture._guided_shape_rejection(sine_pcm(0.99)), "duration")
        self.assertEqual(CorpusCapture._guided_shape_rejection(sine_pcm(1.0)), "")
        self.assertEqual(CorpusCapture._guided_shape_rejection(sine_pcm(10.0)), "")
        self.assertEqual(CorpusCapture._guided_shape_rejection(sine_pcm(10.01)), "duration")
        silence = np.zeros(32_000, dtype="<i2").tobytes()
        self.assertEqual(CorpusCapture._guided_shape_rejection(silence), "activity")

    async def test_status_send_disables_mentions(self) -> None:
        sent: list[tuple[str, object]] = []

        class StatusChannel:
            async def send(self, message: str, *, allowed_mentions: object) -> None:
                sent.append((message, allowed_mentions))

        with tempfile.TemporaryDirectory() as root:
            capture = CorpusCapture(
                output_dir=Path(root),
                exact_count=len(DOMAIN_PHRASES),
                guided=True,
            )
            client = CaptureDiscordClient(
                config=CaptureConfig(
                    channel_id=1,
                    output_dir=Path(root),
                    status_channel_id=2,
                ),
                capture=capture,
                lease_binding=VoiceLeaseBinding(),
            )
            client.status_channel = StatusChannel()
            await client.send_status(guided_prompt_message(0))
            await client.close()

        self.assertEqual(sent[0][0], guided_prompt_message(0))
        mentions = sent[0][1]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.roles)


class CaptureAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_login_rejection_has_exact_failure_code(self) -> None:
        class RejectingClient:
            def __init__(self, **_kwargs: object) -> None:
                self.capture_voice_client = None

            async def login(self, _token: str) -> None:
                raise discord.LoginFailure("rejected test credential")

        with tempfile.TemporaryDirectory() as root:
            output = prepare_private_output_dir(Path(root) / "capture")
            config = CaptureConfig(
                channel_id=12345678901234567,
                output_dir=output,
                clip_count=1,
                ttl_sec=60,
            )
            with (
                patch(
                    "tools.discord_voice_corpus_capture.CaptureDiscordClient",
                    RejectingClient,
                ),
                patch(
                    "tools.discord_voice_corpus_capture.shutdown_capture",
                    new=AsyncMock(),
                ),
            ):
                result = await _run_capture(config, "dummy-rejected-token")

        self.assertFalse(result.ok)
        self.assertEqual(result.code, "discord_auth_failed")
        self.assertEqual(result.saved_count, 0)


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
        self.assertIn("--status-channel-id", option_strings)
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
        guided, _ = parse_config(
            [
                "--channel-id",
                "12345678901234567",
                "--status-channel-id",
                "22345678901234567",
                "--output-dir",
                "private-stage",
            ]
        )
        self.assertEqual(guided.status_channel_id, 22345678901234567)
        for argv in (
            ["--channel-id", "1", "--output-dir", "x", "--count", "11"],
            ["--channel-id", "1", "--output-dir", "x", "--ttl-seconds", "1800.1"],
            [
                "--channel-id",
                "1",
                "--status-channel-id",
                "2",
                "--output-dir",
                "x",
                "--count",
                "1",
            ],
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

    def test_auth_rejection_emits_one_exact_public_code(self) -> None:
        async def rejected_runner(
            _config: CaptureConfig,
            _token: str,
        ) -> CaptureResult:
            return CaptureResult(
                ok=False,
                code="discord_auth_failed",
                saved_count=0,
            )

        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "private"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "--channel-id",
                        "1",
                        "--output-dir",
                        str(output),
                        "--token-stdin",
                    ],
                    stdin=io.StringIO("dummy-rejected-token\n"),
                    runner=rejected_runner,
                )
            self.assertFalse(output.exists())

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "capture_failed code=discord_auth_failed\n",
        )

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
