from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import stat
import sys
import wave
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import numpy as np


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
for import_root in (REPO_ROOT, RUNTIME_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import discord  # noqa: E402

from evelyn_core.audio import (  # noqa: E402
    compute_waveform_activity_stats,
    prepare_stt_audio,
)
from evelyn_core.discord_session_policy import (  # noqa: E402
    is_transport_corrupted_audio_policy,
)
from evelyn_core.paths import get_runtime_artifacts_root  # noqa: E402
from evelyn_core.voice_ingress_runtime import (  # noqa: E402
    voice_listener_binding_is_current,
)
from evelyn_core.voice_input_lease import (  # noqa: E402
    acquire_discord_voice_input_lease,
    discord_voice_input_instance_id,
    release_discord_voice_input_lease,
)


DISCORD_TOKEN_ENV = "DISCORD_BOT_TOKEN"
INPUT_RATE = 48_000
INPUT_CHANNELS = 2
OUTPUT_RATE = 16_000
SAMPLE_WIDTH = 2
DEFAULT_CLIP_COUNT = 10
MAX_CLIP_COUNT = 10
DEFAULT_TTL_SEC = 30 * 60
MAX_TTL_SEC = 30 * 60
MAX_CLIP_SEC = 30.0
GUIDED_MIN_CLIP_SEC = 1.0
GUIDED_MAX_CLIP_SEC = 10.0
GUIDED_MIN_VOICED_MS = 220.0
GUIDED_MIN_LONGEST_VOICED_MS = 120.0
MAX_INPUT_BYTES = int(MAX_CLIP_SEC * INPUT_RATE * INPUT_CHANNELS * SAMPLE_WIDTH)
MAX_TOKEN_BYTES = 512
LEASE_RELEASE_TIMEOUT_SEC = 15.0
LEASE_RELEASE_POLL_SEC = 0.05
GATEWAY_CLOSE_TIMEOUT_SEC = 10.0
STATUS_SEND_TIMEOUT_SEC = 10.0
_CAPTURE_FILE_RE = re.compile(r"(?:clip-\d{4}\.wav|\.clip-\d{4}\.wav\.part)\Z")

DOMAIN_PHRASES = (
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
)
_STATUS_PREFIX = "[Evelyn Discord 음성 캡처]"
_STATUS_FAILURE = (
    f"{_STATUS_PREFIX} 실패했습니다. 이번 결과는 corpus에 반영하지 않습니다."
)


class CaptureFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise CaptureFailure("invalid_arguments")


@dataclass(frozen=True)
class CaptureConfig:
    channel_id: int
    output_dir: Path
    clip_count: int = DEFAULT_CLIP_COUNT
    ttl_sec: float = DEFAULT_TTL_SEC
    status_channel_id: int | None = None


@dataclass(frozen=True)
class CaptureResult:
    ok: bool
    code: str
    saved_count: int


def _positive_id(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid") from None
    if parsed <= 0 or parsed >= 2**64:
        raise argparse.ArgumentTypeError("invalid")
    return parsed


def _clip_count(value: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid") from None
    if not 1 <= parsed <= MAX_CLIP_COUNT:
        raise argparse.ArgumentTypeError("invalid")
    return parsed


def _ttl_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid") from None
    if not 0.0 < parsed <= MAX_TTL_SEC:
        raise argparse.ArgumentTypeError("invalid")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Capture a bounded private Discord voice corpus with guided prompts.",
    )
    parser.add_argument("--channel-id", required=True, type=_positive_id)
    parser.add_argument("--status-channel-id", type=_positive_id)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--count",
        type=_clip_count,
        default=DEFAULT_CLIP_COUNT,
    )
    parser.add_argument(
        "--ttl-seconds",
        type=_ttl_seconds,
        default=float(DEFAULT_TTL_SEC),
    )
    parser.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read the Discord bot token from the first stdin line.",
    )
    return parser


def parse_config(argv: Sequence[str] | None = None) -> tuple[CaptureConfig, bool]:
    args = build_parser().parse_args(argv)
    status_channel_id = (
        None if args.status_channel_id is None else int(args.status_channel_id)
    )
    if status_channel_id is not None and int(args.count) != len(DOMAIN_PHRASES):
        raise CaptureFailure("invalid_arguments")
    return (
        CaptureConfig(
            channel_id=int(args.channel_id),
            output_dir=Path(args.output_dir),
            clip_count=int(args.count),
            ttl_sec=float(args.ttl_seconds),
            status_channel_id=status_channel_id,
        ),
        bool(args.token_stdin),
    )


def guided_prompt_message(index: int) -> str:
    if not 0 <= int(index) < len(DOMAIN_PHRASES):
        raise CaptureFailure("guided_prompt_invalid")
    number = int(index) + 1
    return (
        f"{_STATUS_PREFIX} {number}/{len(DOMAIN_PHRASES)}\n"
        "아래 문장을 한 번만 말해 주세요. 저장 완료가 뜬 뒤 다음 문장으로 "
        f"넘어가세요.\n{DOMAIN_PHRASES[index]}"
    )


def guided_retry_message(index: int, reason: str) -> str:
    reasons = {
        "duration": "발화 길이가 범위를 벗어났습니다.",
        "activity": "충분한 음성이 감지되지 않았습니다.",
        "transport": "음성 전송이 불완전했습니다.",
        "duplicate": "직전과 같은 오디오가 감지됐습니다.",
        "invalid": "오디오 형식을 확인하지 못했습니다.",
    }
    if not 0 <= int(index) < len(DOMAIN_PHRASES):
        raise CaptureFailure("guided_prompt_invalid")
    if reason not in reasons:
        reason = "invalid"
    return (
        f"{_STATUS_PREFIX} {int(index) + 1}/{len(DOMAIN_PHRASES)} 저장 안 됨: "
        f"{reasons[reason]}\n같은 문장을 다시 말해 주세요.\n"
        f"{DOMAIN_PHRASES[index]}"
    )


def guided_saved_message(saved_count: int) -> str:
    saved = int(saved_count)
    if not 1 <= saved <= len(DOMAIN_PHRASES):
        raise CaptureFailure("guided_prompt_invalid")
    if saved == len(DOMAIN_PHRASES):
        return (
            f"{_STATUS_PREFIX} {saved}/{len(DOMAIN_PHRASES)} 수집됨. "
            "사후 STT 모델 진단과 안전 정리를 진행합니다."
        )
    return (
        f"{_STATUS_PREFIX} {saved}/{len(DOMAIN_PHRASES)} 저장 완료.\n\n"
        f"{guided_prompt_message(saved)}"
    )


def status_channel_matches_voice_channel(status_channel: Any, voice_channel: Any) -> bool:
    try:
        return bool(
            isinstance(status_channel, discord.abc.Messageable)
            and int(status_channel.guild.id) == int(voice_channel.guild.id)
        )
    except (AttributeError, TypeError, ValueError):
        return False


def read_discord_token(*, from_stdin: bool, stdin: TextIO) -> str:
    if from_stdin:
        os.environ.pop(DISCORD_TOKEN_ENV, None)
        raw = stdin.readline(MAX_TOKEN_BYTES + 2)
        if len(raw.encode("utf-8", errors="ignore")) > MAX_TOKEN_BYTES + 1:
            raw = ""
            raise CaptureFailure("discord_token_invalid")
        token = raw.rstrip("\r\n")
        raw = ""
    else:
        token = os.environ.pop(DISCORD_TOKEN_ENV, "").strip()
    if (
        not token
        or len(token.encode("utf-8")) > MAX_TOKEN_BYTES
        or any(character.isspace() for character in token)
    ):
        token = ""
        raise CaptureFailure("discord_token_missing")
    return token


def _prepare_private_output_dir_owned(path: Path) -> tuple[Path, bool]:
    candidate = Path(path)
    if candidate.is_symlink():
        raise CaptureFailure("output_dir_unsafe")
    created = False
    if candidate.exists():
        if not candidate.is_dir() or next(candidate.iterdir(), None) is not None:
            raise CaptureFailure("output_dir_not_empty")
    else:
        candidate.mkdir(mode=0o700, parents=False, exist_ok=False)
        created = True
    try:
        resolved = candidate.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_dir():
            raise CaptureFailure("output_dir_unsafe")
        resolved.chmod(0o700)
    except BaseException as exc:
        if created:
            with contextlib.suppress(OSError):
                candidate.rmdir()
        if isinstance(exc, CaptureFailure):
            raise
        raise CaptureFailure("output_dir_private_mode_failed") from exc
    return resolved, created


def prepare_private_output_dir(path: Path) -> Path:
    resolved, _created = _prepare_private_output_dir_owned(path)
    return resolved


def cleanup_failed_output_dir(path: Path, *, remove_directory: bool) -> None:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_dir():
        raise CaptureFailure("output_cleanup_failed")
    try:
        entries = tuple(candidate.iterdir())
        for entry in entries:
            if (
                not _CAPTURE_FILE_RE.fullmatch(entry.name)
                or entry.is_symlink()
                or not entry.is_file()
            ):
                raise CaptureFailure("output_cleanup_failed")
        for entry in entries:
            entry.unlink()
        if next(candidate.iterdir(), None) is not None:
            raise CaptureFailure("output_cleanup_failed")
        if remove_directory:
            candidate.rmdir()
            if candidate.exists():
                raise CaptureFailure("output_cleanup_failed")
    except CaptureFailure:
        raise
    except OSError as exc:
        raise CaptureFailure("output_cleanup_failed") from exc


def pcm48_stereo_to_pcm16_mono(pcm_bytes: bytes) -> bytes:
    if not pcm_bytes or len(pcm_bytes) % (SAMPLE_WIDTH * INPUT_CHANNELS):
        raise CaptureFailure("clip_pcm_invalid")
    if len(pcm_bytes) > MAX_INPUT_BYTES:
        raise CaptureFailure("clip_duration_invalid")
    try:
        audio16k = np.asarray(prepare_stt_audio(pcm_bytes), dtype=np.float32)
    except Exception as exc:
        raise CaptureFailure("clip_pcm_invalid") from exc
    if audio16k.ndim != 1 or audio16k.size <= 0:
        raise CaptureFailure("clip_duration_invalid")
    if not bool(np.all(np.isfinite(audio16k))):
        raise CaptureFailure("clip_pcm_invalid")
    return (np.clip(audio16k, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def participant_ids_excluding_self(
    channel: Any,
    *,
    bot_user_id: int,
) -> frozenset[int]:
    voice_states = getattr(channel, "voice_states", None)
    if not isinstance(voice_states, Mapping):
        raise CaptureFailure("participant_guard_unavailable")
    participant_ids: set[int] = set()
    for raw_id in voice_states:
        try:
            participant_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise CaptureFailure("participant_guard_unavailable")
        if participant_id <= 0:
            raise CaptureFailure("participant_guard_unavailable")
        if participant_id != int(bot_user_id):
            participant_ids.add(participant_id)
    return frozenset(participant_ids)


class CorpusCapture:
    def __init__(
        self,
        *,
        output_dir: Path,
        exact_count: int,
        guided: bool = False,
    ) -> None:
        if not 1 <= int(exact_count) <= MAX_CLIP_COUNT:
            raise CaptureFailure("clip_count_invalid")
        if guided and int(exact_count) != len(DOMAIN_PHRASES):
            raise CaptureFailure("clip_count_invalid")
        self.output_dir = Path(output_dir)
        self.exact_count = int(exact_count)
        self.guided = bool(guided)
        self.armed = not self.guided
        self.armed_at: float | None = None
        self.status_callback: Callable[[str], Awaitable[None]] | None = None
        self.owner_id: int | None = None
        self.bot_user_id: int | None = None
        self.listener_binding: tuple[object, int, int] | None = None
        self.saved_count = 0
        self.rejected_count = 0
        self.hashes: set[str] = set()
        self.done = asyncio.Event()
        self.error_code = ""
        self._lock = asyncio.Lock()

    @property
    def completed(self) -> bool:
        return self.saved_count == self.exact_count and not self.error_code

    def fail(self, code: str) -> None:
        if self.done.is_set() or self.error_code:
            return
        self.error_code = code
        self.done.set()

    async def arm_guided(
        self,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> None:
        if not self.guided or self.armed or self.done.is_set():
            raise CaptureFailure("guided_state_invalid")
        await status_callback(guided_prompt_message(0))
        self.status_callback = status_callback
        self.armed_at = asyncio.get_running_loop().time()
        self.armed = True

    async def _notify(self, message: str) -> None:
        callback = self.status_callback
        if not self.guided or callback is None:
            raise CaptureFailure("guided_state_invalid")
        await callback(message)

    async def _notify_and_rearm(self, message: str) -> None:
        await self._notify(message)
        self.armed_at = asyncio.get_running_loop().time()
        self.armed = True

    @staticmethod
    def _guided_utterance_started_at(
        debug_meta: Any,
    ) -> float | None:
        if not isinstance(debug_meta, Mapping):
            return None
        try:
            utterance_started_at = float(
                debug_meta.get("_utterance_started_at_monotonic")
            )
        except (TypeError, ValueError):
            return None
        return utterance_started_at if math.isfinite(utterance_started_at) else None

    async def notify_failure(
        self,
        status_callback: Callable[[str], Awaitable[None]],
    ) -> None:
        async with self._lock:
            if not self.guided or not self.error_code:
                return
            if self.error_code == "status_send_failed":
                return
            with contextlib.suppress(Exception):
                await status_callback(_STATUS_FAILURE)

    @staticmethod
    def _guided_shape_rejection(pcm16_mono: bytes) -> str:
        duration = len(pcm16_mono) / float(OUTPUT_RATE * SAMPLE_WIDTH)
        if not GUIDED_MIN_CLIP_SEC <= duration <= GUIDED_MAX_CLIP_SEC:
            return "duration"
        try:
            audio = np.frombuffer(pcm16_mono, dtype="<i2").astype(np.float32)
            audio /= 32768.0
            activity = compute_waveform_activity_stats(
                audio,
                sampling_rate=OUTPUT_RATE,
            )
            voiced_ms = float(activity.get("voiced_ms") or 0.0)
            longest_voiced_ms = float(activity.get("longest_voiced_ms") or 0.0)
        except Exception:
            return "invalid"
        if (
            not math.isfinite(voiced_ms)
            or not math.isfinite(longest_voiced_ms)
            or voiced_ms < GUIDED_MIN_VOICED_MS
            or longest_voiced_ms < GUIDED_MIN_LONGEST_VOICED_MS
        ):
            return "activity"
        return ""

    def lock_owner(self, channel: Any, *, bot_user_id: int) -> None:
        if int(bot_user_id) <= 0:
            raise CaptureFailure("participant_guard_unavailable")
        participants = participant_ids_excluding_self(
            channel,
            bot_user_id=int(bot_user_id),
        )
        if len(participants) != 1:
            raise CaptureFailure("participant_guard_failed")
        self.bot_user_id = int(bot_user_id)
        self.owner_id = next(iter(participants))

    def assert_owner(self, channel: Any, member: Any | None = None) -> None:
        if self.owner_id is None or self.bot_user_id is None:
            raise CaptureFailure("participant_guard_unlocked")
        participants = participant_ids_excluding_self(
            channel,
            bot_user_id=self.bot_user_id,
        )
        if participants != frozenset({self.owner_id}):
            raise CaptureFailure("participant_guard_changed")
        if member is not None and (
            getattr(member, "bot", None) is not False
            or int(getattr(member, "id", 0) or 0) != self.owner_id
        ):
            raise CaptureFailure("participant_guard_changed")

    def bind_listener(
        self,
        binding: Any,
        *,
        expected_channel_id: int,
    ) -> None:
        if (
            not isinstance(binding, tuple)
            or len(binding) != 3
            or binding[0] is None
            or not isinstance(binding[1], int)
            or isinstance(binding[1], bool)
            or int(binding[2] or 0) != int(expected_channel_id)
        ):
            raise CaptureFailure("listener_binding_invalid")
        self.listener_binding = (binding[0], int(binding[1]), int(binding[2]))

    def assert_listener_binding(self, member: Any, debug_meta: Any) -> None:
        expected = self.listener_binding
        if expected is None or not isinstance(debug_meta, Mapping):
            raise CaptureFailure("listener_binding_invalid")
        observed = debug_meta.get("_voice_listener_binding")
        if (
            not isinstance(observed, tuple)
            or len(observed) != 3
            or observed[0] is not expected[0]
            or observed[1] != expected[1]
            or observed[2] != expected[2]
        ):
            raise CaptureFailure("listener_binding_stale")
        if not voice_listener_binding_is_current(member, observed):
            raise CaptureFailure("listener_binding_stale")

    def _store(self, pcm16_mono: bytes) -> bool:
        duration = len(pcm16_mono) / float(OUTPUT_RATE * SAMPLE_WIDTH)
        if not 0.0 < duration <= MAX_CLIP_SEC:
            self.rejected_count += 1
            return False
        digest = hashlib.sha256(pcm16_mono).hexdigest()
        if digest in self.hashes:
            self.rejected_count += 1
            return False
        index = self.saved_count + 1
        final_path = self.output_dir / f"clip-{index:04d}.wav"
        temporary_path = self.output_dir / f".clip-{index:04d}.wav.part"
        if final_path.exists() or temporary_path.exists():
            raise CaptureFailure("output_dir_not_empty")
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as handle:
                with wave.open(handle, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(SAMPLE_WIDTH)
                    wav.setframerate(OUTPUT_RATE)
                    wav.writeframes(pcm16_mono)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, final_path)
            final_path.chmod(0o600)
        except Exception as exc:
            with contextlib.suppress(OSError):
                temporary_path.unlink()
            raise CaptureFailure("clip_write_failed") from exc
        self.hashes.add(digest)
        self.saved_count = index
        if self.saved_count == self.exact_count and not self.guided:
            self.done.set()
        return True

    async def accept_completed_pcm(
        self,
        *,
        channel: Any,
        member: Any,
        pcm_bytes: bytes,
        debug_meta: dict[str, Any] | None = None,
    ) -> bool:
        async with self._lock:
            if self.done.is_set():
                return False
            if self.guided and not self.armed:
                self.rejected_count += 1
                return False
            try:
                self.assert_owner(channel, member)
                self.assert_listener_binding(member, debug_meta)
                if self.guided:
                    utterance_started_at = self._guided_utterance_started_at(debug_meta)
                    if utterance_started_at is None or self.armed_at is None:
                        self.fail("guided_timing_invalid")
                        return False
                    if utterance_started_at < self.armed_at:
                        self.rejected_count += 1
                        return False
                if self.guided:
                    self.armed = False
                if is_transport_corrupted_audio_policy(dict(debug_meta)):
                    self.rejected_count += 1
                    if self.guided:
                        await self._notify_and_rearm(
                            guided_retry_message(self.saved_count, "transport")
                        )
                    return False
                pcm16_mono = pcm48_stereo_to_pcm16_mono(pcm_bytes)
                if self.guided:
                    rejection = self._guided_shape_rejection(pcm16_mono)
                    if rejection:
                        self.rejected_count += 1
                        await self._notify_and_rearm(
                            guided_retry_message(self.saved_count, rejection)
                        )
                        return False
                    digest = hashlib.sha256(pcm16_mono).hexdigest()
                    if digest in self.hashes:
                        self.rejected_count += 1
                        await self._notify_and_rearm(
                            guided_retry_message(self.saved_count, "duplicate")
                        )
                        return False
                stored = self._store(pcm16_mono)
                if not stored:
                    if self.guided:
                        await self._notify_and_rearm(
                            guided_retry_message(self.saved_count, "invalid")
                        )
                    return False
                if self.guided:
                    message = guided_saved_message(self.saved_count)
                    if self.saved_count == self.exact_count:
                        await self._notify(message)
                        self.done.set()
                    else:
                        await self._notify_and_rearm(message)
                return True
            except CaptureFailure as exc:
                if exc.code.startswith(("participant_guard", "listener_binding")):
                    self.fail(exc.code)
                    return False
                if exc.code in {"clip_pcm_invalid", "clip_duration_invalid"}:
                    self.rejected_count += 1
                    if self.guided:
                        try:
                            await self._notify_and_rearm(
                                guided_retry_message(self.saved_count, "invalid")
                            )
                        except Exception:
                            self.fail("status_send_failed")
                    return False
                self.fail(exc.code)
                return False
            except Exception:
                self.fail("status_send_failed" if self.guided else "capture_failed")
                return False


def _voice_lease_owner_is_exactly_released() -> bool:
    owner_path = get_runtime_artifacts_root() / "voice_input_lease" / "owner.json"
    expected_fields = {
        "schema",
        "state",
        "source",
        "instanceId",
        "leaseId",
        "lastReleasedSource",
        "lastReleasedInstanceId",
        "lastReleasedLeaseId",
        "updatedAt",
    }
    try:
        metadata = owner_path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= 65_536
        ):
            return False
        payload = json.loads(owner_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    updated_at = payload.get("updatedAt") if isinstance(payload, dict) else None
    return bool(
        isinstance(payload, dict)
        and set(payload) == expected_fields
        and payload.get("schema") == "voice_input_lease.owner.v1"
        and payload.get("state") == "unowned"
        and payload.get("source") == ""
        and payload.get("instanceId") == ""
        and payload.get("leaseId") == ""
        and payload.get("lastReleasedSource") == "discord_voice"
        and payload.get("lastReleasedInstanceId")
        == discord_voice_input_instance_id()
        and re.fullmatch(
            r"[0-9a-f]{32}",
            str(payload.get("lastReleasedLeaseId") or ""),
        )
        and type(updated_at) in {int, float}
        and math.isfinite(float(updated_at))
    )


async def wait_for_voice_lease_release_confirmation(
    *,
    timeout_sec: float = LEASE_RELEASE_TIMEOUT_SEC,
    poll_sec: float = LEASE_RELEASE_POLL_SEC,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.01, float(timeout_sec))
    while True:
        if _voice_lease_owner_is_exactly_released():
            return
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise CaptureFailure("voice_lease_release_unconfirmed")
        await asyncio.sleep(min(max(0.001, float(poll_sec)), remaining))


class VoiceLeaseBinding:
    def __init__(
        self,
        *,
        acquire: Callable[[], Awaitable[str]] = acquire_discord_voice_input_lease,
        release: Callable[[str], Awaitable[None]] = release_discord_voice_input_lease,
        confirm_release: Callable[[], Awaitable[None]] = (
            wait_for_voice_lease_release_confirmation
        ),
    ) -> None:
        self.acquire = acquire
        self.release = release
        self.confirm_release = confirm_release
        self.pending_token = ""
        self.bound = False
        self.release_confirmed = asyncio.Event()

    async def acquire_for_capture(self) -> None:
        if self.pending_token or self.bound:
            raise CaptureFailure("voice_lease_state_invalid")
        token = str(await self.acquire() or "").strip()
        if not token:
            raise CaptureFailure("voice_lease_acquire_failed")
        self.pending_token = token

    def bind_and_listen(
        self,
        voice_client: Any,
        callback: Callable[..., Awaitable[Any]],
    ) -> None:
        if not self.pending_token or self.bound:
            raise CaptureFailure("voice_lease_state_invalid")

        async def release_and_confirm(token: str) -> None:
            await self.release(token)
            await self.confirm_release()
            self.release_confirmed.set()

        voice_client.on_user_audio = callback
        voice_client.bind_voice_input_lease(
            self.pending_token,
            release_and_confirm,
        )
        self.pending_token = ""
        self.bound = True
        voice_client.listen()

    async def stop_and_release(self, voice_client: Any | None) -> None:
        if self.pending_token:
            token = self.pending_token
            self.pending_token = ""
            await self.release(token)
            await self.confirm_release()
            self.release_confirmed.set()
            return
        if not self.bound:
            return
        if voice_client is None:
            raise CaptureFailure("voice_lease_release_unconfirmed")
        voice_client.stop_listening()
        drain = getattr(voice_client, "_drain_voice_input_lease_releases", None)
        if callable(drain):
            await drain()
        try:
            await asyncio.wait_for(
                self.release_confirmed.wait(),
                timeout=LEASE_RELEASE_TIMEOUT_SEC,
            )
        except TimeoutError as exc:
            raise CaptureFailure("voice_lease_release_unconfirmed") from exc


async def shutdown_capture(
    *,
    voice_client: Any | None,
    lease_binding: VoiceLeaseBinding,
    close_bot: Callable[[], Awaitable[None]],
) -> None:
    failure: BaseException | None = None
    try:
        await lease_binding.stop_and_release(voice_client)
    except BaseException as exc:
        failure = exc
    if voice_client is not None:
        try:
            await voice_client.disconnect(force=True)
        except BaseException as exc:
            failure = failure or exc
    try:
        await close_bot()
    except BaseException as exc:
        failure = failure or exc
    if failure is not None:
        raise CaptureFailure("capture_cleanup_failed") from failure


class CaptureDiscordClient(discord.Client):
    def __init__(
        self,
        *,
        config: CaptureConfig,
        capture: CorpusCapture,
        lease_binding: VoiceLeaseBinding,
    ) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.capture_config = config
        self.capture = capture
        self.lease_binding = lease_binding
        self.capture_channel: Any | None = None
        self.status_channel: Any | None = None
        self.capture_voice_client: Any | None = None
        self._setup_started = False
        self._shutdown_started = False

    async def on_ready(self) -> None:
        if self._setup_started or self.capture.done.is_set():
            return
        self._setup_started = True
        try:
            from evelyn_voice import EvelynVoiceClient

            channel = self.get_channel(self.capture_config.channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                raise CaptureFailure("voice_channel_unavailable")
            status_channel_id = self.capture_config.status_channel_id
            if status_channel_id is not None:
                status_channel = self.get_channel(status_channel_id)
                if not status_channel_matches_voice_channel(status_channel, channel):
                    raise CaptureFailure("status_channel_unavailable")
                self.status_channel = status_channel
            bot_user_id = int(getattr(self.user, "id", 0) or 0)
            if bot_user_id <= 0:
                raise CaptureFailure("participant_guard_unavailable")
            self.capture_channel = channel
            self.capture.lock_owner(channel, bot_user_id=bot_user_id)
            await self.lease_binding.acquire_for_capture()
            voice_client = await channel.connect(
                cls=EvelynVoiceClient,
                timeout=30.0,
                reconnect=False,
            )
            if not isinstance(voice_client, EvelynVoiceClient):
                raise CaptureFailure("voice_client_invalid")
            self.capture_voice_client = voice_client
            self.capture.assert_owner(channel)
            self.capture.bind_listener(
                voice_client.listener_binding(),
                expected_channel_id=self.capture_config.channel_id,
            )
            voice_client.set_listener_failure_callback(
                lambda _client, _generation: self.capture.fail(
                    "voice_listener_failed"
                )
            )

            async def on_completed_pcm(
                member: Any,
                pcm_bytes: bytes,
                debug_meta: dict[str, Any] | None = None,
            ) -> None:
                await self.capture.accept_completed_pcm(
                    channel=channel,
                    member=member,
                    pcm_bytes=pcm_bytes,
                    debug_meta=debug_meta,
                )

            self.lease_binding.bind_and_listen(
                voice_client,
                on_completed_pcm,
            )
            if self.capture.guided:
                await self.capture.arm_guided(self.send_status)
        except CaptureFailure as exc:
            self.capture.fail(exc.code)
        except Exception:
            self.capture.fail("capture_setup_failed")

    async def send_status(self, message: str) -> None:
        if (
            self.status_channel is None
            or not isinstance(message, str)
            or not 0 < len(message) <= 2_000
        ):
            raise CaptureFailure("status_send_failed")
        try:
            await asyncio.wait_for(
                self.status_channel.send(
                    message,
                    allowed_mentions=discord.AllowedMentions.none(),
                ),
                timeout=STATUS_SEND_TIMEOUT_SEC,
            )
        except Exception as exc:
            raise CaptureFailure("status_send_failed") from exc

    async def notify_failure(self) -> None:
        if self.status_channel is None:
            return
        await self.capture.notify_failure(self.send_status)

    async def on_voice_state_update(
        self,
        member: Any,
        before: Any,
        after: Any,
    ) -> None:
        del member, before, after
        if self.capture.done.is_set() or self.capture_channel is None:
            return
        try:
            self.capture.assert_owner(self.capture_channel)
        except CaptureFailure as exc:
            self.capture.fail(exc.code)

    async def shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        await shutdown_capture(
            voice_client=self.capture_voice_client,
            lease_binding=self.lease_binding,
            close_bot=lambda: discord.Client.close(self),
        )


async def _run_capture(config: CaptureConfig, discord_token: str) -> CaptureResult:
    capture = CorpusCapture(
        output_dir=config.output_dir,
        exact_count=config.clip_count,
        guided=config.status_channel_id is not None,
    )
    lease_binding = VoiceLeaseBinding()
    client = CaptureDiscordClient(
        config=config,
        capture=capture,
        lease_binding=lease_binding,
    )
    gateway_task: asyncio.Task[Any] | None = None
    done_task: asyncio.Task[Any] | None = None
    cleanup_ok = False

    async def live() -> None:
        nonlocal discord_token, gateway_task, done_task
        try:
            try:
                await client.login(discord_token)
            except discord.LoginFailure as exc:
                raise CaptureFailure("discord_auth_failed") from exc
        finally:
            discord_token = ""
        gateway_task = asyncio.create_task(client.connect(reconnect=False))
        done_task = asyncio.create_task(capture.done.wait())
        completed, _pending = await asyncio.wait(
            {gateway_task, done_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if gateway_task in completed and not capture.done.is_set():
            capture.fail("discord_gateway_stopped")
        await capture.done.wait()
        if not done_task.done():
            done_task.cancel()
        await asyncio.gather(done_task, return_exceptions=True)

    try:
        await asyncio.wait_for(live(), timeout=config.ttl_sec)
    except TimeoutError:
        capture.fail("capture_ttl_expired")
    except CaptureFailure as exc:
        capture.fail(exc.code)
    except Exception:
        capture.fail("capture_failed")
    finally:
        discord_token = ""
        if config.status_channel_id is not None and not capture.completed:
            await client.notify_failure()
        try:
            await shutdown_capture(
                voice_client=client.capture_voice_client,
                lease_binding=lease_binding,
                close_bot=lambda: discord.Client.close(client),
            )
            cleanup_ok = True
        except Exception:
            capture.fail("capture_cleanup_failed")
        if gateway_task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(gateway_task),
                    timeout=GATEWAY_CLOSE_TIMEOUT_SEC,
                )
            except Exception:
                gateway_task.cancel()
                await asyncio.gather(gateway_task, return_exceptions=True)
        if done_task is not None and not done_task.done():
            done_task.cancel()
            await asyncio.gather(done_task, return_exceptions=True)

    success = bool(
        capture.completed
        and cleanup_ok
        and lease_binding.release_confirmed.is_set()
    )
    return CaptureResult(
        ok=success,
        code="completed" if success else capture.error_code or "capture_failed",
        saved_count=capture.saved_count,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    runner: Callable[[CaptureConfig, str], Awaitable[CaptureResult]] = _run_capture,
) -> int:
    token = ""
    private_output: Path | None = None
    remove_output_directory = False
    result: CaptureResult | None = None
    failure_code = ""
    try:
        config, token_stdin = parse_config(argv)
        token = read_discord_token(
            from_stdin=token_stdin,
            stdin=stdin or sys.stdin,
        )
        private_output, remove_output_directory = _prepare_private_output_dir_owned(
            config.output_dir
        )
        config = CaptureConfig(
            channel_id=config.channel_id,
            output_dir=private_output,
            clip_count=config.clip_count,
            ttl_sec=config.ttl_sec,
            status_channel_id=config.status_channel_id,
        )
        with open(os.devnull, "w", encoding="utf-8") as discard:
            with contextlib.redirect_stdout(discard), contextlib.redirect_stderr(discard):
                result = asyncio.run(runner(config, token))
        token = ""
    except CaptureFailure as exc:
        failure_code = exc.code
    except (KeyboardInterrupt, asyncio.CancelledError):
        failure_code = "capture_failed"
    except Exception:
        failure_code = "capture_failed"
    finally:
        token = ""

    if private_output is not None and (result is None or not result.ok):
        try:
            cleanup_failed_output_dir(
                private_output,
                remove_directory=remove_output_directory,
            )
        except CaptureFailure:
            failure_code = "output_cleanup_failed"

    if failure_code:
        print(f"capture_failed code={failure_code}", file=sys.stderr)
        return 2
    if result is not None and result.ok:
        print(f"capture_complete clips={result.saved_count}")
        return 0
    print(
        f"capture_failed code={result.code if result is not None else 'capture_failed'}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
