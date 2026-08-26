from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid
from typing import Any

import aiohttp
import numpy as np

from .audio import prepare_stt_audio
from .config import (
    LOCAL_MIC_BLOCK_MS,
    LOCAL_MIC_CONTINUE_THRESHOLD,
    LOCAL_MIC_DEVICE,
    LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
    LOCAL_MIC_MAX_SEGMENT_SEC,
    LOCAL_MIC_MIN_VOICED_MS,
    LOCAL_MIC_PREROLL_MS,
    LOCAL_MIC_QUEUE_MAX,
    LOCAL_MIC_SAMPLE_RATE,
    LOCAL_MIC_START_CONSECUTIVE,
    LOCAL_MIC_START_THRESHOLD,
    LOCAL_MIC_VAD_FILTER_ENABLED,
    LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
    MEMORY_ROOT,
    OMNIVOICE_LANGUAGE,
    OMNIVOICE_MODEL,
    OMNIVOICE_NUM_STEP,
    OMNIVOICE_SPEED,
    OMNIVOICE_STREAM_BLOCK_SIZE,
    OMNIVOICE_STREAM_BLOCK_STEPS,
    OMNIVOICE_STREAM_FIRST_BLOCK_STEPS,
    OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS,
    OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS,
    OMNIVOICE_STREAM_STRATEGY,
    OMNIVOICE_VOICE,
    TARGET_RATE,
    VOICE_WAVEFORM_BODY_RMS_MIN,
    SPEAKER_VERIFICATION_APPLY_TO,
    SPEAKER_VERIFICATION_CACHE_DIR,
    SPEAKER_VERIFICATION_DEVICE,
    SPEAKER_VERIFICATION_ENABLED,
    SPEAKER_VERIFICATION_ENROLL_DIR,
    SPEAKER_VERIFICATION_MAX_AUDIO_SEC,
    SPEAKER_VERIFICATION_MIN_AUDIO_SEC,
    SPEAKER_VERIFICATION_MODEL,
    SPEAKER_VERIFICATION_THRESHOLD,
)
from .conversation_ingress_recovery import final_text_sha256
from .host_supervisor_client import LOCAL_BRIDGE_RESTART_EXIT_CODE
from .fast_action_runtime import detect_minecraft_runtime_command
from .host_vision_bridge import HostVisionBridge
from .host_ui_action_bridge import HostUiActionBridge
from .instance_lock_runtime import (
    InstanceLockManager,
    build_instance_lock_runtime_deps,
)
from .local_mic import LocalMicCaptureService
from .local_tts_playback import normalize_output_device
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
)
from .memory_exposure import (
    MemoryExposurePosition,
    memory_exposure_guard,
    memory_exposure_position_from_dict,
)
from .local_bridge_barge_in import (
    SingleOwnerPlaybackController,
    evaluate_local_barge_in,
    local_barge_source_binding_matches,
)
from .local_voice_admission import split_exact_leading_wake
from .main_inference_contract import (
    MainForegroundReservation,
    main_capture_generation_from_wire,
    main_foreground_reservation_from_wire,
    main_foreground_reservation_to_wire,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .runtime_error_observability import RuntimeErrorCounter
from .stt_client import (
    cancel_stt_stream_via_service,
    finish_stt_stream_via_service,
    push_stt_stream_chunk_via_service,
    run_stt_client_operation_with_cancellation_drain,
    start_stt_stream_via_service,
    start_stt_stream_with_cleanup,
)
from .text import clean_text, clean_tts_text, should_suppress_tts_for_command
from .voice_asr_stream import AsrRevision, AsrStreamSession
from .voice_validation import (
    active_validation_context,
    emit_silence_liveness_event,
    emit_transcript_validation_event,
    emit_voice_validation_event,
    validation_attempt_binding_is_current,
)
from .voice_capture_consent import (
    BRIDGE_STATUS_AUTH_SCOPE,
    VOICE_CAPTURE_AUTH_ENV,
    WATCHDOG_STATUS_SCHEMA,
    inspect_voice_capture_host_lease,
    resolve_voice_capture_auth_token,
    sign_voice_capture_artifact,
)

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - host audio dependency
    sd = None


BOT_API_BASE = os.getenv("LOCAL_BRIDGE_BOT_API_BASE", "http://127.0.0.1:8798").rstrip("/")
LOCAL_BRIDGE_STATUS_AUTH_HEADER = "X-Evelyn-Local-Bridge-Token"
LOCAL_BRIDGE_STATUS_AUTH_TOKEN = os.getenv(
    "LOCAL_BRIDGE_STATUS_AUTH_TOKEN",
    "",
).strip()
VOICE_CAPTURE_HOST_AUTH_TOKEN = resolve_voice_capture_auth_token()
_CREDENTIAL_ENV_PATTERN = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIALS?|API_KEY|PRIVATE_KEY|ACCESS_KEY)(?:_|$)",
    re.IGNORECASE,
)
STT_SERVICE_URL = os.getenv("STT_SERVICE_URL", "http://127.0.0.1:8892").rstrip("/")
OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880").rstrip("/")
LOCAL_TTS_OUTPUT_DEVICE = os.getenv("LOCAL_TTS_OUTPUT_DEVICE") or os.getenv("LOCAL_AUDIO_OUTPUT_DEVICE")
LOCAL_BRIDGE_TTS_ENABLED = os.getenv("LOCAL_BRIDGE_TTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_STREAMING_TTS_ENABLED = os.getenv("LOCAL_BRIDGE_STREAMING_TTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_STT_STREAMING_ENABLED = os.getenv(
    "LOCAL_BRIDGE_STT_STREAMING_ENABLED",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_ASR_STREAM_QUEUE_MAX = 256
LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED = os.getenv(
    "LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

LOCAL_BRIDGE_MIN_TEXT_CHARS = max(1, int(os.getenv("LOCAL_BRIDGE_MIN_TEXT_CHARS", "2")))
LOCAL_BRIDGE_STATUS_INTERVAL_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_STATUS_INTERVAL_SEC", "0.25")))
LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN = os.getenv("LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC", "1.5")))
VOICE_CAPTURE_FAIL_SAFE_EXIT_CODE = 76
LOCAL_BRIDGE_TTS_WARMUP_ENABLED = os.getenv("LOCAL_BRIDGE_TTS_WARMUP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_TTS_WARMUP_TEXT = os.getenv("LOCAL_BRIDGE_TTS_WARMUP_TEXT", "\uc751.")
LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC = max(0.0, float(os.getenv("LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC", "0.5")))
LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC = max(1.0, float(os.getenv("LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC", "30")))
LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS = max(1, int(os.getenv("LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS", "6")))
LOCAL_BRIDGE_TTS_WARMUP_RETRY_DELAY_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_TTS_WARMUP_RETRY_DELAY_SEC", "2.0")))
LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC = max(
    0.0,
    float(os.getenv("LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC", "0.7")),
)
TTS_PCM_RATE = int(os.getenv("OMNIVOICE_PCM_RATE", "24000"))
TTS_PCM_CHANNELS = int(os.getenv("OMNIVOICE_PCM_CHANNELS", "1"))
TTS_PCM_DTYPE = "int16"
TTS_SAMPLE_WIDTH_BYTES = 2
LOCAL_OUTPUT_BACKEND_UNAVAILABLE = "local_output_backend_unavailable"
LOCAL_OUTPUT_DEVICE_UNAVAILABLE = "local_output_device_unavailable"
LOCAL_OUTPUT_FORMAT_UNSUPPORTED = "local_output_format_unsupported"
PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
STOP_SCRIPT = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
START_VOYAGER_BAT = PROJECT_ROOT / "evelyn_core" / "start_voyager.bat"
MINECRAFT_SERVICE_BASE = os.getenv(
    "LOCAL_BRIDGE_MINECRAFT_SERVICE_BASE",
    "http://127.0.0.1:8765",
).rstrip("/")
MINECRAFT_MODEL_HEALTH_URL = os.getenv(
    "LOCAL_BRIDGE_MINECRAFT_MODEL_HEALTH_URL",
    "http://127.0.0.1:8798/internal/mindcraft-llm/health",
)
MINECRAFT_GATEWAY_HEALTH_URL = os.getenv(
    "LOCAL_BRIDGE_MINECRAFT_GATEWAY_HEALTH_URL",
    "http://127.0.0.1:8787/health",
)
LOCAL_BRIDGE_MINECRAFT_START_TIMEOUT_SEC = max(
    30.0,
    float(os.getenv("LOCAL_BRIDGE_MINECRAFT_START_TIMEOUT_SEC", "300")),
)
LOCAL_BRIDGE_STATUS_PATH = get_runtime_artifacts_root() / "local_bridge" / "status.json"
LOCAL_BRIDGE_INSTANCE_LOCK_PATH = (
    get_runtime_artifacts_root() / "local_bridge" / "instance.lock"
)
VOICE_CAPTURE_HOST_LEASE_PATH = (
    get_runtime_artifacts_root()
    / "voice_capture_consent"
    / "owner_heartbeat.json"
)
LOCAL_VOICE_ADMISSION_REFRESH_AFTER_SEC = 5.0
LOCAL_MAIN_FOREGROUND_FRESHNESS_MARGIN_SEC = 0.2
LOCAL_VOICE_MAIN_FOREGROUND_SCHEMA = (
    "local_voice.main-foreground-reservation.v1"
)
LOCAL_VOICE_MAIN_FOREGROUND_PATH = (
    "/api/local-voice/main-foreground-reservation"
)
LOCAL_VOICE_BOT_CONNECT_MAX_RETRIES = 8
LOCAL_VOICE_BOT_CONNECT_RETRY_DELAY_SEC = 0.5
LOCAL_BRIDGE_DELIVERY_BINDING_SCHEMA = (
    "local_bridge.conversation-delivery-binding.v1"
)
LOCAL_BRIDGE_DELIVERY_ACK_SCHEMA = (
    "local_bridge.conversation-delivery-ack.v1"
)
LOCAL_BRIDGE_DELIVERY_ACK_RECEIPT_SCHEMA = (
    "local_bridge.conversation-delivery-ack-receipt.v1"
)
_LOCAL_BRIDGE_DELIVERY_HASH = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LOCAL_BRIDGE_DELIVERY_OUTCOMES = frozenset(
    {"played", "failed", "partial", "cancelled"}
)


class LocalVoiceAdmissionDrop(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = clean_text(reason).lower() or "local_voice_wake_required"
        super().__init__(self.reason)


class LocalChatStreamFailure(RuntimeError):
    def __init__(self, *, bot_dispatched: bool) -> None:
        self.bot_dispatched = bool(bot_dispatched)
        super().__init__(
            "chat_stream_failed_after_dispatch"
            if self.bot_dispatched
            else "chat_stream_failed_before_dispatch"
        )


@dataclass(frozen=True)
class LocalMemoryHandoff:
    state: str
    position: MemoryExposurePosition | None


@dataclass(frozen=True)
class LocalChatReply:
    text: str
    memory_handoff: LocalMemoryHandoff
    playback_ack: dict[str, Any] | None = None


@dataclass(frozen=True)
class _LocalAsrStreamEvent:
    kind: str
    key: tuple[int, int]
    pcm16: bytes = b""
    future: asyncio.Future[str | None] | None = None


@dataclass
class _LocalAsrRemoteStream:
    stream_id: str
    sequence: int
    revisions: AsrStreamSession
    future: asyncio.Future[str | None]


def parse_local_memory_handoff(payload: Any) -> LocalMemoryHandoff:
    """Parse the strict, content-free server-to-host lease handoff."""

    if not isinstance(payload, dict):
        raise MemoryDeletionJournalIntegrityError()
    if not {"memoryState", "memoryBoundary"}.issubset(payload):
        raise MemoryDeletionJournalIntegrityError()
    state = payload.get("memoryState")
    raw_boundary = payload.get("memoryBoundary")
    if state == "bound":
        if not isinstance(raw_boundary, dict):
            raise MemoryDeletionJournalIntegrityError()
        return LocalMemoryHandoff(
            state="bound",
            position=memory_exposure_position_from_dict(raw_boundary),
        )
    if state == "not_used" and raw_boundary is None:
        return LocalMemoryHandoff(state="not_used", position=None)
    raise MemoryDeletionJournalIntegrityError()


def parse_local_playback_ack_binding(
    payload: Any,
    *,
    bridge_instance_id: str,
    turn_id: str,
    assistant_text: str | None,
    allow_pending: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        raise MemoryDeletionJournalIntegrityError()
    ingress = payload.get("ingress")
    if ingress is None:
        return None
    if not isinstance(ingress, dict):
        raise MemoryDeletionJournalIntegrityError()
    binding = ingress.get("playbackAck")
    if binding is None:
        return None
    if not isinstance(binding, dict) or set(binding) != {
        "schema",
        "bridgeInstanceId",
        "turnId",
        "assistantHash",
        "required",
        "contentFree",
    }:
        raise MemoryDeletionJournalIntegrityError()
    assistant_hash = clean_text(binding.get("assistantHash"))
    if (
        binding.get("schema") != LOCAL_BRIDGE_DELIVERY_BINDING_SCHEMA
        or clean_text(binding.get("bridgeInstanceId"))
        != bridge_instance_id
        or clean_text(binding.get("turnId")) != turn_id
        or not (
            (allow_pending and not assistant_hash)
            or (
                assistant_text is not None
                and _LOCAL_BRIDGE_DELIVERY_HASH.fullmatch(assistant_hash)
                is not None
                and assistant_hash == final_text_sha256(assistant_text)
            )
        )
        or binding.get("required") is not True
        or binding.get("contentFree") is not True
    ):
        raise MemoryDeletionJournalIntegrityError()
    return dict(binding)


def voxcpm_stream_url(base_url: str = OMNIVOICE_SERVER_URL) -> str:
    if base_url.startswith("https://"):
        base_url = "wss://" + base_url[len("https://") :]
    elif base_url.startswith("http://"):
        base_url = "ws://" + base_url[len("http://") :]
    return base_url.rstrip("/") + "/v1/audio/speech/stream"


def iter_pcm_aligned_chunks(chunks: list[bytes], *, sample_width: int = TTS_SAMPLE_WIDTH_BYTES):
    remainder = b""
    for chunk in chunks:
        if not chunk:
            continue
        data = remainder + chunk
        aligned_len = len(data) - (len(data) % sample_width)
        if aligned_len > 0:
            yield data[:aligned_len]
        remainder = data[aligned_len:]


def _local_main_foreground_monotonic() -> float:
    return time.monotonic()


class LocalIoBridge:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=8)
        self.priority_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=4)
        self.barge_in_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=4)
        self._local_asr_stream_queue: asyncio.Queue[_LocalAsrStreamEvent] = asyncio.Queue(
            maxsize=LOCAL_BRIDGE_ASR_STREAM_QUEUE_MAX
        )
        self._local_asr_stream_task: asyncio.Task[None] | None = None
        self._local_asr_stream_shutdown = False
        self._local_asr_stream_futures: dict[
            tuple[int, int],
            asyncio.Future[str | None],
        ] = {}
        self.session: aiohttp.ClientSession | None = None
        self.service: LocalMicCaptureService | None = None
        # Capture is never activated from ambient process configuration. The
        # authenticated consent control path is the sole ON authority.
        self.mic_enabled = False
        self.mic_control_request_revision = 0
        self.mic_control_pending_revision = 0
        self.mic_control_action_id = ""
        self.mic_control_pending_action_id = ""
        self.mic_control_state = "idle"
        self.mic_control_desired_enabled = self.mic_enabled
        self.mic_control_error = ""
        self.mic_capture_stopped = not self.mic_enabled
        self.mic_control_lock = asyncio.Lock()
        self.status_lock = asyncio.Lock()
        self.mic_control_tasks: set[asyncio.Task[Any]] = set()
        self.voice_capture_consent_binding: tuple[str, str] | None = None
        self.voice_capture_watchdog_state = "blocked"
        self.voice_capture_watchdog_reason = (
            "voice_capture_consent_heartbeat_missing"
        )
        self.voice_capture_watchdog_checked_at = time.time()
        self.voice_capture_watchdog_last_stopped_at: float | None = None
        self.voice_capture_fence_digest = ""
        self.ready = False
        self.status_seq = 0
        self.speaking = False
        self.mic_input_suppressed_until = 0.0
        self.suppressed_mic_segment_count = 0
        self.discarded_pending_mic_segment_count = 0
        self.segment_count = 0
        self.main_foreground_capture_generation = 0
        self.transcript_count = 0
        self.play_count = 0
        self.last_error = ""
        self.runtime_errors = RuntimeErrorCounter()
        self.last_latency: dict[str, Any] = {}
        self.last_tts_playback: dict[str, Any] = {}
        self.pending_conversation_delivery_acks: list[dict[str, Any]] = []
        self.active_conversation_playback_ack: dict[str, Any] | None = None
        self.started_at = time.time()
        self.output_device = normalize_output_device(LOCAL_TTS_OUTPUT_DEVICE)
        self.output_ready = False
        self.output_error_code = LOCAL_OUTPUT_BACKEND_UNAVAILABLE
        self.shutdown_started = False
        self.restart_started = False
        self.speak_request_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self.speak_worker_task: asyncio.Task | None = None
        self.control_speech_generation = 0
        self.active_control_speech_generation = 0
        self.tts_warmup_task: asyncio.Task | None = None
        self.tts_warmup_done = False
        self.tts_warmup_error = ""
        self.tts_warmup_ms: float | None = None
        self.output_device_request_revision = 0
        self.minecraft_command_request_revision = 0
        self.minecraft_command_pending_revision = 0
        self.minecraft_command_state = "idle"
        self.minecraft_command_error = ""
        self.minecraft_command_result: dict[str, Any] = {}
        self.minecraft_command_lock = asyncio.Lock()
        self.minecraft_command_tasks: set[asyncio.Task[Any]] = set()
        self.active_turn_task: asyncio.Task[Any] | None = None
        self.barge_worker_task: asyncio.Task[Any] | None = None
        self.host_vision_bridge: HostVisionBridge | None = None
        self.host_vision_task: asyncio.Task[Any] | None = None
        self.host_ui_action_bridge: HostUiActionBridge | None = None
        self.host_ui_action_task: asyncio.Task[Any] | None = None
        self.active_turn_id = ""
        self.active_turn_started_at: float | None = None
        self.active_validation: dict[str, Any] | None = None
        self.playback_started_for_turn = False
        self.playback_cancelled_for_turn = False
        self.reply_started_for_turn = False
        self.reply_final_for_turn = False
        self.playback_controller = SingleOwnerPlaybackController()
        self._barge_source_lock = threading.Lock()
        self._barge_source_snapshot: dict[str, Any] | None = None
        self._last_released_barge_source: dict[str, Any] | None = None
        self._speaker_verifier: Any | None = None
        self._speaker_verifier_initialized = False
        self.bridge_instance_id = uuid.uuid4().hex
        self.admission_epoch = 0
        self.admission_active = False
        self.admission_mode = "inactive"
        self.admission_accepted_count = 0
        self.admission_rejected_count = 0
        self.admission_last_reason = "not_started"
        self.main_foreground_reservation_enabled = False

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.session = session
            await self._start_mic()
            self.host_vision_bridge = HostVisionBridge(session=session)
            self.host_vision_task = asyncio.create_task(
                self.host_vision_bridge.run(),
                name="local-bridge-host-vision",
            )
            self.host_ui_action_bridge = HostUiActionBridge()
            self.host_ui_action_task = asyncio.create_task(
                self.host_ui_action_bridge.run(),
                name="local-bridge-host-ui-action",
            )
            await self._post_status()
            self._ensure_tts_warmup()
            self.barge_worker_task = asyncio.create_task(
                self._barge_in_worker(),
                name="local-bridge-barge-in",
            )
            try:
                while True:
                    if self.active_turn_task is not None and self.active_turn_task.done():
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await self.active_turn_task
                        self.active_turn_task = None
                    if self.active_turn_task is None:
                        source_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]]
                        try:
                            item = self.priority_queue.get_nowait()
                            source_queue = self.priority_queue
                        except asyncio.QueueEmpty:
                            try:
                                item = await asyncio.wait_for(
                                    self.queue.get(),
                                    timeout=LOCAL_BRIDGE_STATUS_INTERVAL_SEC,
                                )
                                source_queue = self.queue
                            except asyncio.TimeoutError:
                                await self._post_status()
                                continue
                        pcm_bytes, meta = item
                        self.active_turn_task = asyncio.create_task(
                            self._process_queued_segment(
                                pcm_bytes,
                                meta,
                                source_queue=source_queue,
                            ),
                            name=f"local-bridge-turn-{meta.get('turnId') or 'unknown'}",
                        )
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(self.active_turn_task),
                            timeout=LOCAL_BRIDGE_STATUS_INTERVAL_SEC,
                        )
                    except asyncio.TimeoutError:
                        await self._post_status()
                    except asyncio.CancelledError:
                        current_task = asyncio.current_task()
                        if current_task is not None and current_task.cancelling():
                            raise
            finally:
                cleanup_task = asyncio.create_task(
                    self._cleanup_after_run(),
                    name="local-bridge-run-cleanup",
                )
                cancellation: asyncio.CancelledError | None = None
                while not cleanup_task.done():
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError as exc:
                        if cleanup_task.cancelled():
                            if cancellation is None:
                                raise
                            break
                        if cancellation is None:
                            cancellation = exc
                    except BaseException:
                        break
                if cancellation is not None:
                    if not cleanup_task.cancelled():
                        with contextlib.suppress(BaseException):
                            cleanup_task.result()
                    raise cancellation
                cleanup_task.result()

    async def _cleanup_after_run(self) -> None:
        if self.service is not None:
            with contextlib.suppress(Exception):
                await self._stop_mic_service(reason="bridge_stopping")
            # Capture callbacks are registered before the to_thread
            # completion, so let their loop callbacks observe the
            # invalidated epoch before the worker is retired.
            await asyncio.sleep(0)
        await self._shutdown_local_asr_stream_worker()
        if self.host_ui_action_task is not None:
            self.host_ui_action_task.cancel()
            with contextlib.suppress(
                asyncio.CancelledError,
                Exception,
            ):
                await self.host_ui_action_task
            self.host_ui_action_task = None
        if self.host_vision_task is not None:
            self.host_vision_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.host_vision_task
            self.host_vision_task = None

    async def _process_queued_segment(
        self,
        pcm_bytes: bytes,
        meta: dict[str, Any],
        *,
        source_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]],
    ) -> None:
        try:
            await self._handle_segment(pcm_bytes, meta)
        finally:
            source_queue.task_done()

    def _ensure_local_asr_stream_worker(self) -> None:
        if not LOCAL_BRIDGE_STT_STREAMING_ENABLED or self._local_asr_stream_shutdown:
            return
        if self._local_asr_stream_task is not None and not self._local_asr_stream_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._local_asr_stream_task = loop.create_task(
            self._local_asr_stream_worker(),
            name="local-bridge-asr-stream",
        )

    @staticmethod
    def _resolve_local_asr_future(
        future: asyncio.Future[str | None] | None,
        result: str | None,
    ) -> None:
        if future is not None and not future.done():
            future.set_result(result)

    def _queue_local_asr_event(self, event: _LocalAsrStreamEvent) -> bool:
        try:
            self._local_asr_stream_queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            if self._local_asr_stream_futures.get(event.key) is event.future:
                self._local_asr_stream_futures.pop(event.key, None)
            self._resolve_local_asr_future(event.future, None)
            self.runtime_errors.record("stt_transcribe_failed", RuntimeError)
            return False

    def _start_local_asr_capture(self, key: tuple[int, int]) -> None:
        if (
            not LOCAL_BRIDGE_STT_STREAMING_ENABLED
            or self._local_asr_stream_shutdown
            or not self._voice_admission_lifecycle_is_current(key[0])
        ):
            return
        previous = self._local_asr_stream_futures.pop(key, None)
        self._resolve_local_asr_future(previous, None)
        future = asyncio.get_running_loop().create_future()
        self._local_asr_stream_futures[key] = future
        self._ensure_local_asr_stream_worker()
        self._queue_local_asr_event(
            _LocalAsrStreamEvent("start", key, future=future)
        )

    def _push_local_asr_audio(self, key: tuple[int, int], pcm16: bytes) -> None:
        future = self._local_asr_stream_futures.get(key)
        if future is None:
            return
        if not self._voice_admission_lifecycle_is_current(key[0]):
            self._abandon_local_asr_stream(key)
            return
        self._queue_local_asr_event(
            _LocalAsrStreamEvent(
                "chunk",
                key,
                pcm16=bytes(pcm16),
                future=future,
            )
        )

    def _finish_local_asr_capture(self, key: tuple[int, int], *, accepted: bool) -> None:
        future = self._local_asr_stream_futures.get(key)
        if future is None:
            return
        current = self._voice_admission_lifecycle_is_current(key[0])
        kind = "finish" if accepted and current else "cancel"
        self._queue_local_asr_event(
            _LocalAsrStreamEvent(kind, key, future=future)
        )
        if kind == "cancel":
            self._local_asr_stream_futures.pop(key, None)
            self._resolve_local_asr_future(future, None)

    @staticmethod
    def _local_asr_key_from_meta(meta: dict[str, Any]) -> tuple[int, int] | None:
        value = meta.get("_asrStreamKey")
        if (
            not isinstance(value, tuple)
            or len(value) != 2
            or any(type(part) is not int for part in value)
        ):
            return None
        return value

    def _abandon_local_asr_stream(self, key: tuple[int, int]) -> None:
        future = self._local_asr_stream_futures.pop(key, None)
        self._resolve_local_asr_future(future, None)
        self._ensure_local_asr_stream_worker()
        if self._local_asr_stream_task is not None:
            self._queue_local_asr_event(
                _LocalAsrStreamEvent("cancel", key, future=future)
            )

    def _abandon_local_asr_meta(self, meta: dict[str, Any]) -> None:
        key = self._local_asr_key_from_meta(meta)
        if key is not None:
            self._abandon_local_asr_stream(key)

    def _abandon_all_local_asr_streams(self) -> None:
        for key in tuple(self._local_asr_stream_futures):
            self._abandon_local_asr_stream(key)

    @staticmethod
    def _apply_local_asr_response(
        state: _LocalAsrRemoteStream,
        payload: Any,
        *,
        expected_final: bool,
    ) -> AsrRevision:
        if (
            not isinstance(payload, dict)
            or type(payload.get("revision")) is not int
            or not isinstance(payload.get("text"), str)
            or payload.get("isFinal") is not expected_final
        ):
            raise RuntimeError("stt_stream_response_invalid")
        return state.revisions.apply(
            revision=payload["revision"],
            text=payload["text"],
            is_final=expected_final,
        )

    async def _cancel_local_asr_remote(self, state: _LocalAsrRemoteStream) -> None:
        with contextlib.suppress(Exception):
            await run_stt_client_operation_with_cancellation_drain(
                cancel_stt_stream_via_service,
                service_url=STT_SERVICE_URL,
                stream_id=state.stream_id,
                timeout_sec=5.0,
            )

    async def _local_asr_stream_worker(self) -> None:
        states: dict[tuple[int, int], _LocalAsrRemoteStream] = {}
        try:
            while True:
                event = await self._local_asr_stream_queue.get()
                try:
                    if event.kind == "start":
                        if (
                            event.future is None
                            or event.future.done()
                            or not self._voice_admission_lifecycle_is_current(event.key[0])
                        ):
                            self._resolve_local_asr_future(event.future, None)
                            continue
                        payload = await start_stt_stream_with_cleanup(
                            service_url=STT_SERVICE_URL,
                            timeout_sec=10.0,
                            language="Korean",
                            start_stream=start_stt_stream_via_service,
                            cancel_stream=cancel_stt_stream_via_service,
                        )
                        if not isinstance(payload, dict):
                            raise RuntimeError("stt_stream_start_invalid")
                        stream_id = payload.get("streamId")
                        if (
                            not isinstance(stream_id, str)
                            or not stream_id.strip()
                            or type(payload.get("samplingRate")) is not int
                            or payload["samplingRate"] != 16000
                            or payload.get("decoderProfile") != "realtime-ko"
                            or type(payload.get("nextSequence")) is not int
                            or payload["nextSequence"] != 0
                        ):
                            if isinstance(stream_id, str) and stream_id.strip():
                                with contextlib.suppress(Exception):
                                    await run_stt_client_operation_with_cancellation_drain(
                                        cancel_stt_stream_via_service,
                                        service_url=STT_SERVICE_URL,
                                        stream_id=stream_id,
                                        timeout_sec=5.0,
                                    )
                            raise RuntimeError("stt_stream_start_invalid")
                        state = _LocalAsrRemoteStream(
                            stream_id=stream_id,
                            sequence=0,
                            revisions=AsrStreamSession(),
                            future=event.future,
                        )
                        if (
                            event.future.done()
                            or not self._voice_admission_lifecycle_is_current(event.key[0])
                        ):
                            await self._cancel_local_asr_remote(state)
                            self._resolve_local_asr_future(event.future, None)
                        else:
                            states[event.key] = state
                        continue

                    state = states.get(event.key)
                    if state is None:
                        if event.kind in {"finish", "cancel"}:
                            self._resolve_local_asr_future(event.future, None)
                        continue
                    if (
                        event.kind == "cancel"
                        or state.future.done()
                        or not self._voice_admission_lifecycle_is_current(event.key[0])
                    ):
                        states.pop(event.key, None)
                        state.revisions.cancel()
                        await self._cancel_local_asr_remote(state)
                        self._resolve_local_asr_future(state.future, None)
                        continue
                    if event.kind == "chunk":
                        payload = await run_stt_client_operation_with_cancellation_drain(
                            push_stt_stream_chunk_via_service,
                            event.pcm16,
                            service_url=STT_SERVICE_URL,
                            stream_id=state.stream_id,
                            sequence=state.sequence,
                            timeout_sec=15.0,
                        )
                        self._apply_local_asr_response(
                            state,
                            payload,
                            expected_final=False,
                        )
                        state.sequence += 1
                        continue
                    if event.kind != "finish":
                        raise RuntimeError("stt_stream_event_invalid")
                    payload = await run_stt_client_operation_with_cancellation_drain(
                        finish_stt_stream_via_service,
                        service_url=STT_SERVICE_URL,
                        stream_id=state.stream_id,
                        timeout_sec=45.0,
                    )
                    final = self._apply_local_asr_response(
                        state,
                        payload,
                        expected_final=True,
                    )
                    states.pop(event.key, None)
                    self._resolve_local_asr_future(
                        state.future,
                        final.text if final.authoritative else None,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    state = states.pop(event.key, None)
                    if state is not None:
                        state.revisions.cancel()
                        await self._cancel_local_asr_remote(state)
                    self._resolve_local_asr_future(
                        state.future if state is not None else event.future,
                        None,
                    )
                    self.runtime_errors.record("stt_transcribe_failed", exc)
                finally:
                    self._local_asr_stream_queue.task_done()
        finally:
            for state in states.values():
                self._resolve_local_asr_future(state.future, None)
                await self._cancel_local_asr_remote(state)
            for future in self._local_asr_stream_futures.values():
                self._resolve_local_asr_future(future, None)
            self._local_asr_stream_futures.clear()

    async def _shutdown_local_asr_stream_worker(self) -> None:
        self._local_asr_stream_shutdown = True
        task = self._local_asr_stream_task
        self._local_asr_stream_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _start_mic(self) -> None:
        if not self.mic_enabled:
            await self._stop_mic_service(reason="mic_disabled")
            self.mic_capture_stopped = True
            self.ready = True
            self.last_error = ""
            print("[LOCAL BRIDGE] mic_disabled=true", flush=True)
            return
        if self.service is not None and self.service.capture_ready:
            self.mic_capture_stopped = False
            self.ready = True
            self.last_error = ""
            return
        if self.service is not None:
            # A timed-out start may still own a live capture thread. Never
            # overwrite that reference: retire and verify it before retrying.
            await self._stop_mic_service(reason="mic_restart")

        loop = asyncio.get_running_loop()
        capture_service_epoch = self.admission_epoch

        def capture_context() -> dict[str, Any] | None:
            with self._barge_source_lock:
                barge_source = (
                    dict(self._barge_source_snapshot)
                    if self._barge_source_snapshot is not None
                    else None
                )
            if barge_source is None:
                return None
            interrupt_validation = active_validation_context(
                surface="local",
                prefer_interrupt=True,
            )
            source_session_id = str(
                barge_source.get("validationSessionId") or ""
            )
            interrupt_session_id = str(
                (interrupt_validation or {}).get("sessionId") or ""
            )
            barge_source["interruptPairingValid"] = not bool(
                source_session_id
                and interrupt_session_id
                and source_session_id != interrupt_session_id
            )
            context: dict[str, Any] = {"_bargeSource": barge_source}
            if interrupt_validation:
                context["validationSessionId"] = interrupt_validation["sessionId"]
                context["validationStepId"] = interrupt_validation["stepId"]
                context["validationAttempt"] = interrupt_validation.get("attempt")
                context["validationAttemptId"] = interrupt_validation.get("attemptId")
            return context

        def on_speech_start(capture_generation: int) -> None:
            key = (capture_service_epoch, int(capture_generation))
            # LocalMic invokes start/chunks/end/segment on one capture thread;
            # thread-safe loop callbacks therefore preserve that exact order.
            loop.call_soon_threadsafe(self._start_local_asr_capture, key)

        def on_audio_chunk(capture_generation: int, pcm16: bytes) -> None:
            key = (capture_service_epoch, int(capture_generation))
            loop.call_soon_threadsafe(
                self._push_local_asr_audio,
                key,
                bytes(pcm16),
            )

        def on_speech_end(capture_generation: int, accepted: bool) -> None:
            key = (capture_service_epoch, int(capture_generation))

            def finish() -> None:
                self._finish_local_asr_capture(key, accepted=bool(accepted))

            loop.call_soon_threadsafe(finish)

        def on_segment(pcm_bytes: bytes, meta: dict[str, Any]) -> None:
            # Bind every callback to the service generation that captured it.
            # A final flush racing with OFF/restart must stay stale.
            captured_admission_epoch = capture_service_epoch
            barge_source = (
                dict(meta.get("_bargeSource") or {})
                if isinstance(meta.get("_bargeSource"), dict)
                else None
            )
            raw_capture_generation = meta.get("_asrCaptureGeneration")
            stream_key = (
                (captured_admission_epoch, raw_capture_generation)
                if type(raw_capture_generation) is int
                else None
            )

            def enqueue() -> None:
                if not self._voice_admission_lifecycle_is_current(
                    captured_admission_epoch
                ):
                    if stream_key is not None:
                        self._abandon_local_asr_stream(stream_key)
                    self.discarded_pending_mic_segment_count += 1
                    return
                segment_meta = dict(meta)
                segment_meta.pop("_asrCaptureGeneration", None)
                if type(raw_capture_generation) is int:
                    capture_generation = raw_capture_generation
                    self.main_foreground_capture_generation = max(
                        self.main_foreground_capture_generation,
                        capture_generation,
                    )
                else:
                    self.main_foreground_capture_generation += 1
                    capture_generation = (
                        self.main_foreground_capture_generation
                    )
                segment_meta["_mainForegroundCaptureGeneration"] = (
                    capture_generation
                )
                if stream_key is not None:
                    segment_meta["_asrStreamKey"] = stream_key
                segment_meta.setdefault("turnId", uuid.uuid4().hex)
                segment_meta["_admissionEpoch"] = captured_admission_epoch
                if barge_source is not None:
                    segment_meta["_bargeSource"] = dict(barge_source)
                    if self.barge_in_queue.full():
                        with contextlib.suppress(Exception):
                            _dropped_pcm, dropped_meta = self.barge_in_queue.get_nowait()
                            self._abandon_local_asr_meta(dropped_meta)
                            self.barge_in_queue.task_done()
                    self.barge_in_queue.put_nowait((pcm_bytes, segment_meta))
                    return
                if self._mic_input_is_suppressed():
                    self._abandon_local_asr_meta(segment_meta)
                    self.suppressed_mic_segment_count += 1
                    return
                validation = active_validation_context(surface="local")
                if validation:
                    segment_meta["validationSessionId"] = validation["sessionId"]
                    segment_meta["validationStepId"] = validation["stepId"]
                    segment_meta["validationAttempt"] = validation.get("attempt")
                    segment_meta["validationAttemptId"] = validation.get("attemptId")
                if self.queue.full():
                    try:
                        _dropped_pcm, dropped_meta = self.queue.get_nowait()
                        self._abandon_local_asr_meta(dropped_meta)
                        self.queue.task_done()
                    except Exception:
                        pass
                self.queue.put_nowait((pcm_bytes, segment_meta))

            loop.call_soon_threadsafe(enqueue)

        self.service = LocalMicCaptureService(
            on_segment=on_segment,
            on_speech_start=(
                on_speech_start if LOCAL_BRIDGE_STT_STREAMING_ENABLED else None
            ),
            on_audio_chunk=(
                on_audio_chunk if LOCAL_BRIDGE_STT_STREAMING_ENABLED else None
            ),
            on_speech_end=(
                on_speech_end if LOCAL_BRIDGE_STT_STREAMING_ENABLED else None
            ),
            capture_context_provider=capture_context,
            sample_rate=LOCAL_MIC_SAMPLE_RATE,
            block_ms=LOCAL_MIC_BLOCK_MS,
            start_threshold=LOCAL_MIC_START_THRESHOLD,
            continue_threshold=LOCAL_MIC_CONTINUE_THRESHOLD,
            start_consecutive=LOCAL_MIC_START_CONSECUTIVE,
            min_voiced_ms=LOCAL_MIC_MIN_VOICED_MS,
            max_silence_ms=int(os.getenv("LOCAL_MIC_MAX_SILENCE_MS", "500")),
            preroll_ms=LOCAL_MIC_PREROLL_MS,
            max_segment_sec=LOCAL_MIC_MAX_SEGMENT_SEC,
            device=LOCAL_MIC_DEVICE,
            queue_max=LOCAL_MIC_QUEUE_MAX,
            vad_filter_enabled=LOCAL_MIC_VAD_FILTER_ENABLED,
            env_noise_filter_enabled=LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
            waveform_filter_enabled=LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
        )
        start_task = asyncio.create_task(
            asyncio.to_thread(self.service.start),
            name="local-mic-capture-start",
        )
        cancellation: asyncio.CancelledError | None = None
        while not start_task.done():
            try:
                await asyncio.shield(start_task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except Exception:
                pass
        start_error: Exception | None = None
        try:
            started = start_task.result()
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            started = False
        except Exception as exc:
            start_error = exc
            started = False
        if cancellation is not None:
            raise cancellation
        if start_error is not None:
            raise start_error
        self.ready = bool(started and self.service.capture_ready)
        self.mic_capture_stopped = bool(self.service.capture_stopped)
        self.last_error = "" if self.ready else (self.service.last_error or "local_mic_not_ready")
        print(f"[LOCAL BRIDGE] mic_ready={self.ready} device={LOCAL_MIC_DEVICE or 'default'} error={self.last_error or 'none'}", flush=True)

    async def _stop_mic_service(self, *, reason: str) -> None:
        self.voice_capture_fence_digest = ""
        self._invalidate_local_voice_admission(reason)
        service = self.service
        self._discard_pending_mic_segments()
        if service is not None:
            stopped = await asyncio.to_thread(service.stop)
            if stopped is not True or not service.capture_stopped:
                raise RuntimeError("local_mic_stop_unverified")
        self.service = None
        self.mic_capture_stopped = True

    async def _stop_mic(self, *, reason: str = "mic_disabled") -> None:
        await self._stop_mic_service(reason=reason)
        self.mic_enabled = False
        self.voice_capture_consent_binding = None
        self.ready = True
        self.last_error = ""
        print("[LOCAL BRIDGE] mic_ready=false mic_disabled=true", flush=True)

    async def _rollback_failed_mic_enable(self) -> None:
        stop_task = asyncio.create_task(
            self._stop_mic(reason="mic_enable_failed"),
            name="local-mic-enable-rollback",
        )
        cancellation: asyncio.CancelledError | None = None
        while not stop_task.done():
            try:
                await asyncio.shield(stop_task)
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except Exception:
                pass

        stop_error: BaseException | None = None
        try:
            stop_task.result()
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except Exception as exc:
            stop_error = exc
        if stop_error is None and (
            self.mic_enabled
            or self.service is not None
            or not self.mic_capture_stopped
        ):
            stop_error = RuntimeError("local_mic_stop_unverified")
        if stop_error is not None:
            self.runtime_errors.record(
                "mic_control_failed",
                stop_error,
            )
            self._schedule_watchdog_fail_safe_exit()
        if cancellation is not None:
            raise cancellation

    def _capture_may_be_active(self) -> bool:
        return self.mic_enabled or self.service is not None or not self.mic_capture_stopped

    def _inspect_voice_capture_host_lease(self) -> dict[str, Any]:
        return inspect_voice_capture_host_lease(
            VOICE_CAPTURE_HOST_LEASE_PATH,
            auth_token=VOICE_CAPTURE_HOST_AUTH_TOKEN,
        )

    def _voice_capture_lease_rejection(
        self,
        lease: dict[str, Any],
        *,
        expected_binding: tuple[str, str] | None = None,
        require_pinned: bool = False,
        allow_explicit_reenable: bool = False,
    ) -> str:
        if lease.get("authorized") is not True:
            return str(lease.get("reason") or "voice_capture_consent_heartbeat_untrusted")
        if (
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(lease.get("fenceDigest") or ""),
            )
            is None
        ):
            return "voice_capture_consent_heartbeat_untrusted"
        observed = (
            str(lease.get("ownerDigest") or ""),
            str(lease.get("leaseDigest") or ""),
        )
        expected = expected_binding or self.voice_capture_consent_binding
        if (require_pinned and expected is None) or (
            expected is not None and observed != expected
        ):
            return "voice_capture_consent_lease_replaced"
        if (
            self.voice_capture_watchdog_last_stopped_at is not None
            and not allow_explicit_reenable
        ):
            return "voice_capture_watchdog_stopped"
        return ""

    def _record_voice_capture_watchdog(
        self,
        lease: dict[str, Any],
        *,
        reason: str,
        state: str | None = None,
        stopped: bool = False,
    ) -> None:
        self.voice_capture_watchdog_state = state or ("blocked" if reason else "authorized")
        self.voice_capture_watchdog_reason = reason
        self.voice_capture_watchdog_checked_at = float(lease.get("checkedAt") or time.time())
        self.voice_capture_fence_digest = (
            str(lease.get("fenceDigest") or "") if not reason else ""
        )
        latched_physical_stop = bool(
            reason
            and self.voice_capture_watchdog_last_stopped_at is not None
            and not self._capture_may_be_active()
        )
        if stopped or latched_physical_stop:
            self.voice_capture_watchdog_checked_at = time.time()
            self.voice_capture_watchdog_last_stopped_at = (
                self.voice_capture_watchdog_checked_at
            )

    def _voice_capture_watchdog_status(self) -> dict[str, Any]:
        return {
            "schema": WATCHDOG_STATUS_SCHEMA,
            "state": self.voice_capture_watchdog_state,
            "reason": self.voice_capture_watchdog_reason,
            "checkedAt": self.voice_capture_watchdog_checked_at,
            "captureStopped": self.mic_capture_stopped,
            "stoppedAt": self.voice_capture_watchdog_last_stopped_at,
            "contentFree": True,
        }

    async def _stop_mic_for_watchdog(
        self,
        lease: dict[str, Any],
        reason: str,
    ) -> None:
        self.mic_control_desired_enabled = False
        try:
            await self._stop_mic(reason=reason)
        except Exception as exc:
            self._record_voice_capture_watchdog(
                lease,
                reason=reason,
                state="stop_failed",
            )
            self.mic_control_state = "failed"
            self.mic_control_error = "voice_capture_watchdog_stop_failed"
            self.ready = False
            self.last_error = "voice_capture_watchdog_stop_failed"
            self.runtime_errors.record("voice_capture_watchdog_stop_failed", exc)
            self._schedule_watchdog_fail_safe_exit()
            return
        self._record_voice_capture_watchdog(lease, reason=reason, stopped=True)
        self.mic_control_state = "failed"
        self.mic_control_error = reason
        self.last_error = reason

    async def _enforce_voice_capture_watchdog(self) -> None:
        lease = await asyncio.to_thread(self._inspect_voice_capture_host_lease)
        reason = self._voice_capture_lease_rejection(
            lease,
            require_pinned=self._capture_may_be_active(),
        )
        self._record_voice_capture_watchdog(lease, reason=reason)
        if not reason or not self._capture_may_be_active():
            return
        async with self.mic_control_lock:
            lease = await asyncio.to_thread(self._inspect_voice_capture_host_lease)
            reason = self._voice_capture_lease_rejection(
                lease,
                require_pinned=self._capture_may_be_active(),
            )
            self._record_voice_capture_watchdog(lease, reason=reason)
            if not reason or not self._capture_may_be_active():
                return
            await self._stop_mic_for_watchdog(lease, reason)

    async def _apply_mic_control_request(
        self,
        *,
        revision: int,
        enabled: bool,
        action_id: str,
    ) -> None:
        async with self.mic_control_lock:
            if revision <= self.mic_control_request_revision:
                return
            self.mic_control_state = "applying"
            self.mic_control_desired_enabled = enabled
            self.mic_control_error = ""
            try:
                if enabled:
                    if self.restart_started or self.shutdown_started:
                        raise RuntimeError("local_bridge_lifecycle_stopping")
                    lease = await asyncio.to_thread(
                        self._inspect_voice_capture_host_lease
                    )
                    reason = self._voice_capture_lease_rejection(
                        lease,
                        allow_explicit_reenable=True,
                    )
                    if reason:
                        if self._capture_may_be_active():
                            await self._stop_mic_for_watchdog(lease, reason)
                        raise RuntimeError(reason)
                    binding = (
                        str(lease["ownerDigest"]),
                        str(lease["leaseDigest"]),
                    )
                    self.mic_enabled = True
                    await self._start_mic()
                    if self.restart_started or self.shutdown_started:
                        await self._stop_mic()
                        raise RuntimeError("local_bridge_lifecycle_stopping")
                    if (
                        self.service is None
                        or not self.service.capture_ready
                        or self.mic_capture_stopped
                    ):
                        raise RuntimeError("local_mic_start_unverified")
                    current = await asyncio.to_thread(
                        self._inspect_voice_capture_host_lease
                    )
                    reason = self._voice_capture_lease_rejection(
                        current,
                        expected_binding=binding,
                        allow_explicit_reenable=True,
                    )
                    if reason:
                        await self._stop_mic_for_watchdog(current, reason)
                        raise RuntimeError(reason)
                    self.voice_capture_watchdog_last_stopped_at = None
                    self.voice_capture_consent_binding = binding
                    self._record_voice_capture_watchdog(current, reason="")
                else:
                    await self._stop_mic()
                    if (
                        self.mic_enabled
                        or self.service is not None
                        or not self.mic_capture_stopped
                    ):
                        raise RuntimeError("local_mic_stop_unverified")
                self.mic_control_state = "applied"
                self.mic_control_error = ""
            except asyncio.CancelledError:
                try:
                    if enabled:
                        await self._rollback_failed_mic_enable()
                finally:
                    self.mic_control_state = "failed"
                    self.mic_control_desired_enabled = enabled
                    self.mic_control_error = "mic_control_cancelled"
                    self.ready = False
                    self.last_error = "mic_control_cancelled"
                raise
            except Exception as exc:
                try:
                    if enabled:
                        await self._rollback_failed_mic_enable()
                finally:
                    self.mic_control_state = "failed"
                    self.mic_control_desired_enabled = enabled
                    self.mic_control_error = "mic_control_failed"
                    self.ready = False
                    self.runtime_errors.record("mic_control_failed", exc)
                    if not enabled:
                        self.mic_capture_stopped = bool(
                            self.service is None
                            or self.service.capture_stopped
                        )
                    self.last_error = "mic_control_failed"
            finally:
                self.mic_control_request_revision = revision
                self.mic_control_action_id = action_id
                if self.mic_control_pending_action_id == action_id:
                    self.mic_control_pending_revision = 0
                    self.mic_control_pending_action_id = ""
            print(
                "[LOCAL BRIDGE] mic_control_applied "
                f"enabled={self.mic_enabled} ready={self.ready} revision={revision} "
                f"error={self.mic_control_error or 'none'}",
                flush=True,
            )

    def _handle_mic_control_request(self, data: dict[str, Any]) -> None:
        request = data.get("micControlRequest") if isinstance(data, dict) else None
        if not isinstance(request, dict) or not isinstance(request.get("enabled"), bool):
            return
        action_id = str(request.get("actionId") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", action_id):
            return
        target_digest = str(request.get("bridgeInstanceDigest") or "")
        expected_digest = hashlib.sha256(
            self.bridge_instance_id.encode("utf-8")
        ).hexdigest()
        if target_digest != expected_digest:
            return
        revision = request.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool):
            return
        if revision <= max(self.mic_control_request_revision, self.mic_control_pending_revision):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.mic_control_pending_revision = revision
        self.mic_control_pending_action_id = action_id
        task = loop.create_task(
            self._apply_mic_control_request(
                revision=revision,
                enabled=bool(request["enabled"]),
                action_id=action_id,
            ),
            name=f"local-mic-control-{revision}",
        )
        self.mic_control_tasks.add(task)
        task.add_done_callback(self.mic_control_tasks.discard)

    async def _http_health_ready(self, url: str) -> bool:
        if self.session is None:
            return False
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as response:
                if response.status != 200:
                    return False
                payload = await response.json(content_type=None)
                if isinstance(payload, dict) and payload.get("ok") is False:
                    return False
                return True
        except Exception:
            return False

    async def _minecraft_stack_ready(self) -> bool:
        checks = (
            MINECRAFT_MODEL_HEALTH_URL,
            MINECRAFT_GATEWAY_HEALTH_URL,
            f"{MINECRAFT_SERVICE_BASE}/health",
        )
        return all([await self._http_health_ready(url) for url in checks])

    def _minecraft_launcher_environment(self) -> dict[str, str]:
        env = self._credential_scoped_child_environment()
        env["DISCORD_BOT_TOKEN"] = "local-only-disabled"
        secret_home = (
            get_runtime_artifacts_root()
            / "secrets"
            / "codex_device_home"
        )
        if (secret_home / "auth.json").is_file():
            env.setdefault("EVELYN_CODEX_CREDENTIALS_DIR", str(secret_home))
        return env

    @staticmethod
    def _credential_scoped_child_environment() -> dict[str, str]:
        return {
            name: value
            for name, value in os.environ.items()
            if not _CREDENTIAL_ENV_PATTERN.search(name)
        }

    async def _launch_minecraft_stack(self) -> dict[str, Any]:
        if await self._minecraft_stack_ready():
            return {"alreadyReady": True, "launcherExitCode": 0}
        if not START_VOYAGER_BAT.exists():
            raise RuntimeError(f"minecraft launcher not found: {START_VOYAGER_BAT}")
        cmd_exe = os.environ.get("COMSPEC") or "cmd.exe"
        process = subprocess.Popen(
            [cmd_exe, "/d", "/c", "call", str(START_VOYAGER_BAT)],
            cwd=str(PROJECT_ROOT),
            env=self._minecraft_launcher_environment(),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        deadline = time.monotonic() + LOCAL_BRIDGE_MINECRAFT_START_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if await self._minecraft_stack_ready():
                return {
                    "alreadyReady": False,
                    "launcherExitCode": process.poll(),
                    "launcherPid": process.pid,
                }
            return_code = process.poll()
            if return_code is not None and return_code != 0:
                raise RuntimeError(f"minecraft launcher exited with code {return_code}")
            await asyncio.sleep(1.0)
        raise RuntimeError(
            f"minecraft services did not become ready within "
            f"{LOCAL_BRIDGE_MINECRAFT_START_TIMEOUT_SEC:.0f}s"
        )

    async def _activate_minecraft_command(self, command: str, action: str) -> dict[str, Any]:
        _ = command, action
        raise RuntimeError(
            "minecraft_world_authorization_required"
        )

    async def _apply_minecraft_command_request(
        self,
        *,
        revision: int,
        command: str,
        action: str,
    ) -> None:
        async with self.minecraft_command_lock:
            if revision <= self.minecraft_command_request_revision:
                return
            self.minecraft_command_request_revision = revision
            self.minecraft_command_state = "starting"
            self.minecraft_command_error = ""
            self.minecraft_command_result = {}
            await self._post_status()
            self.minecraft_command_state = "failed"
            self.minecraft_command_error = (
                "minecraft_world_authorization_required"
            )
            self.minecraft_command_result = {
                "command": clean_text(command),
                "action": action,
                "commandApplied": False,
                "connected": False,
            }
            self.minecraft_command_pending_revision = max(
                self.minecraft_command_pending_revision,
                revision,
            )
            await self._post_status()
            print(
                "[LOCAL BRIDGE] minecraft_command_applied "
                f"revision={revision} state={self.minecraft_command_state} "
                f"connected={bool(self.minecraft_command_result.get('connected'))} "
                f"errorCode={self.minecraft_command_error or 'none'}",
                flush=True,
            )

    def _handle_minecraft_command_request(self, data: dict[str, Any]) -> None:
        request = data.get("minecraftCommandRequest") if isinstance(data, dict) else None
        if not isinstance(request, dict):
            return
        command = clean_text(request.get("command"))
        action = clean_text(request.get("action")).lower()
        if not command or action not in {"start", "goal"}:
            return
        if detect_minecraft_runtime_command(command) != action:
            return
        try:
            revision = int(request.get("revision") or 0)
        except (TypeError, ValueError):
            return
        if revision <= max(
            self.minecraft_command_request_revision,
            self.minecraft_command_pending_revision,
        ):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.minecraft_command_pending_revision = revision
        task = loop.create_task(
            self._apply_minecraft_command_request(
                revision=revision,
                command=command,
                action=action,
            ),
            name=f"minecraft-lazy-start-{revision}",
        )
        self.minecraft_command_tasks.add(task)
        task.add_done_callback(self.minecraft_command_tasks.discard)

    def _mic_input_is_suppressed(self) -> bool:
        return time.monotonic() < self.mic_input_suppressed_until

    def _validation_context_from_meta(
        self,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if meta is None:
            return self.active_validation
        source = dict(meta or {})
        session_id = str(source.get("validationSessionId") or "")
        step_id = str(source.get("validationStepId") or "")
        attempt_id = str(source.get("validationAttemptId") or "")
        if session_id or step_id or attempt_id:
            if not (session_id and step_id and attempt_id):
                return None
            return {
                "sessionId": session_id,
                "stepId": step_id,
                "surface": "local",
                "attempt": source.get("validationAttempt"),
                "attemptId": attempt_id,
            }
        return None

    def _admission_validation_binding(
        self,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._validation_context_from_meta(meta)
        if not context:
            return {}
        return {
            "sessionId": str(context.get("sessionId") or ""),
            "stepId": str(context.get("stepId") or ""),
            "attempt": context.get("attempt"),
            "attemptId": str(context.get("attemptId") or ""),
        }

    def _apply_voice_admission_status(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        main_foreground = payload.get("mainForegroundReservation")
        if (
            isinstance(main_foreground, dict)
            and set(main_foreground) == {"schema", "enabled", "contentFree"}
            and main_foreground.get("schema")
            == LOCAL_VOICE_MAIN_FOREGROUND_SCHEMA
            and type(main_foreground.get("enabled")) is bool
            and main_foreground.get("contentFree") is True
        ):
            self.main_foreground_reservation_enabled = main_foreground[
                "enabled"
            ]
        status = payload.get("voiceAdmission")
        if not isinstance(status, dict):
            status = payload.get("admission")
        if (
            not isinstance(status, dict)
            or status.get("schema") != "local_voice.admission.status.v1"
            or status.get("contentFree") is not True
        ):
            return
        mode = clean_text(status.get("mode")).lower()
        reason = clean_text(status.get("lastReason")).lower()
        if mode not in {"inactive", "wake_entry", "followup", "validation"}:
            mode = "inactive"
        if not re.fullmatch(r"[a-z0-9_]{1,80}", reason):
            reason = "admission_status_invalid"
        try:
            accepted_count = max(0, int(status.get("acceptedCount") or 0))
            rejected_count = max(0, int(status.get("rejectedCount") or 0))
        except (TypeError, ValueError, OverflowError):
            return
        self.admission_active = bool(status.get("active"))
        self.admission_mode = mode if self.admission_active else "inactive"
        self.admission_accepted_count = accepted_count
        self.admission_rejected_count = rejected_count
        self.admission_last_reason = reason

    def _invalidate_local_voice_admission(self, reason: str) -> None:
        self.admission_epoch += 1
        self._abandon_all_local_asr_streams()
        self.admission_active = False
        self.admission_mode = "inactive"
        normalized = clean_text(reason).lower()
        self.admission_last_reason = (
            normalized
            if re.fullmatch(r"[a-z0-9_]{1,80}", normalized)
            else "admission_invalidated"
        )

    def _record_local_voice_admission_rejection(self, reason: str) -> None:
        normalized = clean_text(reason).lower()
        self.admission_rejected_count += 1
        self.admission_last_reason = (
            normalized
            if re.fullmatch(r"[a-z0-9_]{1,80}", normalized)
            else "local_voice_wake_required"
        )

    def _voice_admission_public_status(self) -> dict[str, Any]:
        return {
            "schema": "local_voice.admission.status.v1",
            "active": self.admission_active,
            "mode": self.admission_mode if self.admission_active else "inactive",
            "acceptedCount": self.admission_accepted_count,
            "rejectedCount": self.admission_rejected_count,
            "lastReason": self.admission_last_reason,
            "contentFree": True,
        }

    def _voice_admission_lifecycle_is_current(self, epoch: Any) -> bool:
        if isinstance(epoch, bool):
            return False
        try:
            normalized_epoch = int(epoch)
        except (TypeError, ValueError, OverflowError):
            return False
        return bool(
            normalized_epoch == self.admission_epoch
            and self.mic_enabled
            and not self.restart_started
            and not self.shutdown_started
        )

    async def _reserve_main_foreground_before_stt(
        self,
        capture_generation: int,
    ) -> MainForegroundReservation | None:
        if self.session is None:
            raise RuntimeError("main_foreground_reservation_unavailable")
        generation = main_capture_generation_from_wire(capture_generation)
        async with self.session.post(
            f"{BOT_API_BASE}{LOCAL_VOICE_MAIN_FOREGROUND_PATH}",
            json={
                "action": "reserve",
                "bridgeInstanceId": self.bridge_instance_id,
                "turnId": self.active_turn_id,
                "captureGeneration": generation,
            },
            headers={
                LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                    LOCAL_BRIDGE_STATUS_AUTH_TOKEN
                )
            },
            timeout=aiohttp.ClientTimeout(
                total=0.9,
                connect=0.25,
                sock_connect=0.25,
            ),
            allow_redirects=False,
        ) as response:
            data = await response.json(content_type=None)
            rejected = {
                "ok": False,
                "schema": LOCAL_VOICE_MAIN_FOREGROUND_SCHEMA,
                "error": "main_llm_foreground_reservation_rejected",
            }
            if response.status == 409 and data == rejected:
                return None
            if (
                response.status != 201
                or not isinstance(data, dict)
                or set(data) != {"ok", "schema", "reservation"}
                or data.get("ok") is not True
                or data.get("schema")
                != LOCAL_VOICE_MAIN_FOREGROUND_SCHEMA
            ):
                raise RuntimeError("main_foreground_reservation_unavailable")
            try:
                reservation = main_foreground_reservation_from_wire(
                    data.get("reservation")
                )
            except ValueError:
                raise RuntimeError(
                    "main_foreground_reservation_receipt_invalid"
                ) from None
            if reservation.capture_generation != generation:
                raise RuntimeError(
                    "main_foreground_reservation_receipt_invalid"
                )
            return reservation

    async def _cancel_main_foreground_reservation(
        self,
        reservation: MainForegroundReservation,
    ) -> None:
        if self.session is None:
            return
        try:
            async with self.session.post(
                f"{BOT_API_BASE}{LOCAL_VOICE_MAIN_FOREGROUND_PATH}",
                json={
                    "action": "cancel",
                    "bridgeInstanceId": self.bridge_instance_id,
                    "turnId": self.active_turn_id,
                    "reservation": main_foreground_reservation_to_wire(
                        reservation
                    ),
                },
                headers={
                    LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                        LOCAL_BRIDGE_STATUS_AUTH_TOKEN
                    )
                },
                timeout=aiohttp.ClientTimeout(
                    total=0.9,
                    connect=0.25,
                    sock_connect=0.25,
                ),
                allow_redirects=False,
            ) as response:
                await response.read()
        except Exception as exc:
            self.runtime_errors.record(
                "main_foreground_cancel_failed",
                exc,
            )

    async def _request_voice_admission(
        self,
        text: str,
        *,
        turn_id: str,
        validation: dict[str, Any] | None = None,
        expected_epoch: int | None = None,
    ) -> dict[str, Any] | None:
        if self.session is None:
            self._record_local_voice_admission_rejection(
                "admission_service_unavailable"
            )
            return None
        request_epoch = (
            self.admission_epoch
            if expected_epoch is None
            else expected_epoch
        )
        if not self._voice_admission_lifecycle_is_current(request_epoch):
            self._record_local_voice_admission_rejection(
                "admission_epoch_stale"
            )
            return None
        binding = dict(validation or {})
        payload: dict[str, Any] = {
            "bridgeInstanceId": self.bridge_instance_id,
            "turnId": turn_id,
            "text": text,
        }
        if binding:
            payload["validation"] = binding
        try:
            async with self.session.post(
                f"{BOT_API_BASE}/api/local-voice/admission",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=False,
            ) as response:
                data = await response.json(content_type=None)
                self._apply_voice_admission_status(data)
                if (
                    response.status != 200
                    or not isinstance(data, dict)
                    or data.get("admitted") is not True
                ):
                    if not isinstance(data, dict) or not isinstance(
                        data.get("admission"), dict
                    ):
                        self._record_local_voice_admission_rejection(
                            clean_text((data or {}).get("reason"))
                            if isinstance(data, dict)
                            else "local_voice_wake_required"
                        )
                    return None
        except Exception:
            self._record_local_voice_admission_rejection(
                "admission_service_unavailable"
            )
            return None
        if not self._voice_admission_lifecycle_is_current(request_epoch):
            self._record_local_voice_admission_rejection(
                "admission_epoch_stale"
            )
            return None
        forward_text = clean_text(data.get("forwardText"))
        token = str(data.get("admissionToken") or "")
        mode = clean_text(data.get("mode")).lower()
        if (
            not forward_text
            or len(token) < 24
            or mode not in {"wake_entry", "followup", "validation"}
        ):
            self._record_local_voice_admission_rejection(
                "admission_response_invalid"
            )
            return None
        return {
            "bridgeInstanceId": self.bridge_instance_id,
            "turnId": turn_id,
            "originalText": text,
            "forwardText": forward_text,
            "admissionToken": token,
            "validation": binding,
            "mode": mode,
            "issuedMonotonic": time.monotonic(),
            "epoch": request_epoch,
            "_botDispatched": False,
        }

    async def _ensure_fresh_voice_admission(
        self,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            grant_epoch = int(grant.get("epoch"))
        except (TypeError, ValueError, OverflowError):
            grant_epoch = -1
        if (
            not self._voice_admission_lifecycle_is_current(grant_epoch)
            or grant.get("bridgeInstanceId") != self.bridge_instance_id
            or grant.get("turnId") != self.active_turn_id
        ):
            raise LocalVoiceAdmissionDrop("admission_epoch_stale")
        issued_at = float(grant.get("issuedMonotonic") or 0.0)
        if time.monotonic() - issued_at < LOCAL_VOICE_ADMISSION_REFRESH_AFTER_SEC:
            return grant
        refreshed = await self._request_voice_admission(
            str(grant.get("originalText") or ""),
            turn_id=str(grant.get("turnId") or ""),
            validation=dict(grant.get("validation") or {}),
            expected_epoch=grant_epoch,
        )
        if refreshed is None:
            raise LocalVoiceAdmissionDrop("admission_refresh_rejected")
        if refreshed.get("forwardText") != grant.get("forwardText"):
            raise LocalVoiceAdmissionDrop("admission_refresh_mismatch")
        dispatched = bool(grant.get("_botDispatched"))
        grant.update(refreshed)
        grant["_botDispatched"] = dispatched
        return grant

    async def _ensure_fresh_main_foreground_reservation(
        self,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        previous = grant.get("mainForegroundReservation")
        if previous is None:
            return grant
        issued_at = grant.get("mainForegroundReservationIssuedMonotonic")
        stale = bool(
            not isinstance(issued_at, (int, float))
            or isinstance(issued_at, bool)
            or _local_main_foreground_monotonic() - float(issued_at)
            >= max(
                0.0,
                previous.ttl_ms / 1000.0
                - LOCAL_MAIN_FOREGROUND_FRESHNESS_MARGIN_SEC,
            )
        )
        if not stale:
            return grant
        generation = main_capture_generation_from_wire(
            grant.get("mainCaptureGeneration")
        )
        await self._cancel_main_foreground_reservation(previous)
        grant.pop("mainForegroundReservation", None)
        grant.pop("mainForegroundReservationIssuedMonotonic", None)
        refreshed_at = _local_main_foreground_monotonic()
        refreshed = await self._reserve_main_foreground_before_stt(
            generation
        )
        if refreshed is None:
            return grant
        if (
            refreshed.capture_generation != previous.capture_generation
            or refreshed.backend_epoch != previous.backend_epoch
        ):
            await self._cancel_main_foreground_reservation(refreshed)
            raise RuntimeError("main_foreground_reservation_refresh_mismatch")
        grant["mainForegroundReservation"] = refreshed
        grant["mainForegroundReservationIssuedMonotonic"] = refreshed_at
        return grant

    async def _local_voice_chat_payload(
        self,
        text: str,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        await self._ensure_fresh_voice_admission(grant)
        await self._ensure_fresh_main_foreground_reservation(grant)
        if clean_text(text) != clean_text(grant.get("forwardText")):
            raise LocalVoiceAdmissionDrop("admission_text_mismatch")
        payload: dict[str, Any] = {
            "text": clean_text(grant.get("forwardText")),
            "source": "local_bridge",
            "turnId": str(grant.get("turnId") or ""),
            "bridgeInstanceId": str(grant.get("bridgeInstanceId") or ""),
            "admissionToken": str(grant.get("admissionToken") or ""),
            "admissionMode": str(grant.get("mode") or ""),
        }
        if (
            self.main_foreground_reservation_enabled
            or "mainCaptureGeneration" in grant
        ):
            payload["mainCaptureGeneration"] = (
                main_capture_generation_from_wire(
                    grant.get("mainCaptureGeneration")
                )
            )
            attempted = grant.get("mainForegroundReservationAttempted")
            if type(attempted) is not bool:
                raise LocalVoiceAdmissionDrop(
                    "main_foreground_reservation_binding_invalid"
                )
            payload["mainForegroundReservationAttempted"] = attempted
        main_foreground_reservation = grant.get(
            "mainForegroundReservation"
        )
        if main_foreground_reservation is not None:
            payload["mainForegroundReservation"] = (
                main_foreground_reservation_to_wire(
                    main_foreground_reservation
                )
            )
        validation = dict(grant.get("validation") or {})
        if validation:
            payload["validation"] = validation
        return payload

    def _playback_ack_from_response(
        self,
        payload: Any,
        *,
        grant: dict[str, Any],
        assistant_text: str | None,
        allow_pending: bool = False,
    ) -> dict[str, Any] | None:
        if (
            clean_text(grant.get("mode")).lower() == "validation"
            or bool(grant.get("validation"))
            or self.active_validation is not None
        ):
            return None
        return parse_local_playback_ack_binding(
            payload,
            bridge_instance_id=self.bridge_instance_id,
            turn_id=clean_text(grant.get("turnId")),
            assistant_text=assistant_text,
            allow_pending=allow_pending,
        )

    def _remember_conversation_playback_ack(
        self,
        binding: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if binding is None:
            return self.active_conversation_playback_ack
        current = self.active_conversation_playback_ack
        if current is not None and (
            current["bridgeInstanceId"] != binding["bridgeInstanceId"]
            or current["turnId"] != binding["turnId"]
            or (
                current["assistantHash"]
                and current["assistantHash"] != binding["assistantHash"]
            )
        ):
            raise MemoryDeletionJournalIntegrityError()
        self.active_conversation_playback_ack = dict(binding)
        return self.active_conversation_playback_ack

    def _queue_conversation_delivery_ack(
        self,
        binding: dict[str, Any] | None,
        *,
        outcome: str,
    ) -> None:
        if binding is None:
            return
        normalized_outcome = clean_text(outcome).lower()
        if normalized_outcome not in _LOCAL_BRIDGE_DELIVERY_OUTCOMES:
            raise ValueError("invalid_conversation_delivery_outcome")
        if not binding.get("assistantHash") and normalized_outcome == "played":
            return
        ack = {
            "schema": LOCAL_BRIDGE_DELIVERY_ACK_SCHEMA,
            "bridgeInstanceId": binding["bridgeInstanceId"],
            "turnId": binding["turnId"],
            "assistantHash": binding["assistantHash"],
            "outcome": normalized_outcome,
            "contentFree": True,
        }
        for index, pending in enumerate(
            self.pending_conversation_delivery_acks
        ):
            if (
                pending["bridgeInstanceId"] == ack["bridgeInstanceId"]
                and pending["turnId"] == ack["turnId"]
                and pending["assistantHash"] == ack["assistantHash"]
            ):
                if normalized_outcome == "cancelled":
                    self.pending_conversation_delivery_acks[index] = ack
                return
        self.pending_conversation_delivery_acks.append(ack)

    async def _report_conversation_delivery(
        self,
        binding: dict[str, Any] | None,
        *,
        outcome: str,
    ) -> None:
        if binding is None:
            return
        self._queue_conversation_delivery_ack(binding, outcome=outcome)
        try:
            await self._post_status()
        except Exception:
            # The content-free ACK remains queued for the next heartbeat.
            return

    def _playback_outcome(self, *, completed: bool) -> str:
        if self.playback_cancelled_for_turn:
            return "cancelled"
        if completed:
            return "played"
        if self.playback_started_for_turn:
            return "partial"
        return "failed"

    def _consume_conversation_delivery_ack_receipt(
        self,
        data: dict[str, Any],
        *,
        sent_ack: dict[str, Any] | None,
    ) -> None:
        if sent_ack is None:
            return
        receipt = data.get("conversationDeliveryAckReceipt")
        if not isinstance(receipt, dict) or set(receipt) != {
            "schema",
            "accepted",
            "duplicate",
            "retryable",
            "errorCode",
            "contentFree",
        }:
            return
        accepted = receipt.get("accepted")
        duplicate = receipt.get("duplicate")
        retryable = receipt.get("retryable")
        error_code = receipt.get("errorCode")
        if (
            receipt.get("schema")
            != LOCAL_BRIDGE_DELIVERY_ACK_RECEIPT_SCHEMA
            or type(accepted) is not bool
            or type(duplicate) is not bool
            or type(retryable) is not bool
            or not isinstance(error_code, str)
            or receipt.get("contentFree") is not True
            or (accepted and (retryable or error_code))
            or (not accepted and (duplicate or not error_code))
        ):
            return
        if retryable:
            return
        if (
            self.pending_conversation_delivery_acks
            and self.pending_conversation_delivery_acks[0] == sent_ack
        ):
            self.pending_conversation_delivery_acks.pop(0)
            active = self.active_conversation_playback_ack
            if active is not None and all(
                active[field] == sent_ack[field]
                for field in (
                    "bridgeInstanceId",
                    "turnId",
                    "assistantHash",
                )
            ):
                self.active_conversation_playback_ack = None

    def _local_voice_connection_retry_allowed(
        self,
        grant: dict[str, Any],
    ) -> bool:
        try:
            issued_at = float(grant.get("issuedMonotonic"))
        except (TypeError, ValueError, OverflowError):
            return False
        age = time.monotonic() - issued_at
        return bool(
            0.0 <= age < LOCAL_VOICE_ADMISSION_REFRESH_AFTER_SEC
            and self._voice_admission_lifecycle_is_current(
                grant.get("epoch")
            )
        )

    @contextlib.asynccontextmanager
    async def _local_voice_bot_response(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        grant: dict[str, Any],
        timeout_sec: float,
    ):
        """Bound safe Bot restart retries before exposing a response."""

        assert self.session is not None
        connector_retries = 0
        stale_context_retried = False
        while True:
            async with contextlib.AsyncExitStack() as stack:
                try:
                    response = await stack.enter_async_context(
                        self.session.post(
                            f"{BOT_API_BASE}{path}",
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=timeout_sec),
                            allow_redirects=False,
                        )
                    )
                except aiohttp.ClientConnectorError:
                    if (
                        connector_retries
                        >= LOCAL_VOICE_BOT_CONNECT_MAX_RETRIES
                        or not self._local_voice_connection_retry_allowed(grant)
                    ):
                        raise
                    connector_retries += 1
                    await asyncio.sleep(
                        LOCAL_VOICE_BOT_CONNECT_RETRY_DELAY_SEC
                    )
                    if not self._local_voice_connection_retry_allowed(grant):
                        raise
                    continue

                if (
                    response.status == 409
                    and not stale_context_retried
                    and self._local_voice_connection_retry_allowed(grant)
                ):
                    try:
                        failure = await response.json(content_type=None)
                    except Exception:
                        failure = {}
                    if (
                        isinstance(failure, dict)
                        and failure.get("reason")
                        == "admission_recovery_context_stale"
                    ):
                        heartbeat_accepted = await self._post_status()
                        if (
                            heartbeat_accepted
                            and self._local_voice_connection_retry_allowed(grant)
                        ):
                            stale_context_retried = True
                            continue

                yield response
                return

    def _emit_validation(
        self,
        event: str,
        *,
        meta: dict[str, Any] | None = None,
        **payload: Any,
    ) -> None:
        context = self._validation_context_from_meta(meta)
        if not context:
            return
        emit_voice_validation_event(
            "local",
            event,
            session_id=context.get("sessionId"),
            step_id=context.get("stepId"),
            attempt_id=context.get("attemptId"),
            turnId=str((meta or {}).get("turnId") or self.active_turn_id or ""),
            **payload,
        )

    def _mark_playback_started_once(
        self,
        *,
        expected_owner_id: str,
        expected_owner_token: object | None,
    ) -> None:
        self._ensure_validation_attempt_current()
        with self._barge_source_lock:
            if (
                expected_owner_id
                and expected_owner_token is not None
                and self.playback_controller.owner_id == expected_owner_id
                and self.playback_controller.owner_token is expected_owner_token
            ):
                source_validation = (
                    dict(self.active_validation or {})
                    if expected_owner_id == self.active_turn_id
                    else {}
                )
                self._barge_source_snapshot = {
                    "turnId": (
                        self.active_turn_id
                        if expected_owner_id == self.active_turn_id
                        else expected_owner_id
                    ),
                    "ownerId": expected_owner_id,
                    "ownerToken": expected_owner_token,
                    "validationSessionId": source_validation.get("sessionId"),
                    "validationStepId": source_validation.get("stepId"),
                    "validationAttempt": source_validation.get("attempt"),
                    "validationAttemptId": source_validation.get("attemptId"),
                }
        if self.playback_started_for_turn:
            return
        self._mark_reply_started_once()
        self.playback_started_for_turn = True
        latency_ms = (
            (time.perf_counter() - self.active_turn_started_at) * 1000.0
            if self.active_turn_started_at is not None
            else None
        )
        self._emit_validation(
            "playback_started",
            latencyMs=round(latency_ms, 1) if latency_ms is not None else None,
        )

    def _ensure_validation_attempt_current(self) -> None:
        if not validation_attempt_binding_is_current(
            self.active_validation,
            surface="local",
            reject_unbound_when_active=True,
        ):
            raise RuntimeError("validation_attempt_stale")

    def _claim_playback_owner(self) -> str:
        active_task = self.active_turn_task
        if active_task is not None and not active_task.done():
            owner_id = self.active_turn_id
            cancel = active_task.cancel
        else:
            current_task = asyncio.current_task()
            if current_task is None:
                raise RuntimeError("playback_task_missing")
            owner_id = f"control-{id(current_task)}"
            cancel = current_task.cancel
        with self._barge_source_lock:
            previous_owner_token = self.playback_controller.owner_token
            if not self.playback_controller.claim(owner_id, cancel):
                raise RuntimeError("active_playback_owner_conflict")
            if self.playback_controller.owner_token is not previous_owner_token:
                self._barge_source_snapshot = None
                self._last_released_barge_source = None
        return owner_id

    def _release_playback_owner(self, owner_id: str) -> bool:
        with self._barge_source_lock:
            owner_token = self.playback_controller.owner_token
            released = self.playback_controller.release(owner_id)
            if not released:
                return False
            snapshot = self._barge_source_snapshot
            if (
                snapshot is not None
                and snapshot.get("ownerId") == owner_id
                and snapshot.get("ownerToken") is owner_token
            ):
                self._last_released_barge_source = dict(snapshot)
                self._barge_source_snapshot = None
        return True

    def _mark_reply_started_once(self) -> None:
        if self.reply_started_for_turn:
            return
        self.reply_started_for_turn = True
        self._emit_validation("reply_started")

    def _mark_reply_final_once(self) -> None:
        if self.reply_final_for_turn:
            return
        self._mark_reply_started_once()
        self.reply_final_for_turn = True
        self._emit_validation("reply_final")

    def _speaker_verifier_for_barge_in(self) -> Any | None:
        if self._speaker_verifier_initialized:
            return self._speaker_verifier
        self._speaker_verifier_initialized = True
        if not self._speaker_verification_required_for_barge_in():
            return None
        try:
            from .speaker_verification import SpeakerVerificationConfig, SpeakerVerifier

            self._speaker_verifier = SpeakerVerifier(
                SpeakerVerificationConfig(
                    enabled=True,
                    enroll_dir=SPEAKER_VERIFICATION_ENROLL_DIR,
                    threshold=SPEAKER_VERIFICATION_THRESHOLD,
                    min_audio_sec=SPEAKER_VERIFICATION_MIN_AUDIO_SEC,
                    max_audio_sec=SPEAKER_VERIFICATION_MAX_AUDIO_SEC,
                    model=SPEAKER_VERIFICATION_MODEL,
                    cache_dir=SPEAKER_VERIFICATION_CACHE_DIR,
                    device=SPEAKER_VERIFICATION_DEVICE,
                ),
                log=lambda message: print(message, flush=True),
            )
        except Exception as exc:
            self.runtime_errors.record("speaker_verifier_unavailable", exc)
            self.last_error = f"speaker_verifier_unavailable: {type(exc).__name__}"
            self._speaker_verifier = None
        return self._speaker_verifier

    @staticmethod
    def _speaker_verification_required_for_barge_in() -> bool:
        apply_to = str(SPEAKER_VERIFICATION_APPLY_TO or "").strip().lower()
        return bool(
            SPEAKER_VERIFICATION_ENABLED
            and apply_to
            in {"1", "true", "on", "all", "always", "local", "local_mic"}
        )

    async def _verify_barge_in_speaker(self, pcm_bytes: bytes) -> Any | None:
        verifier = self._speaker_verifier_for_barge_in()
        if verifier is None:
            return None
        try:
            audio16k = np.asarray(prepare_stt_audio(pcm_bytes), dtype=np.float32)
            return await asyncio.to_thread(
                verifier.verify,
                audio16k,
                sampling_rate=TARGET_RATE,
            )
        except Exception as exc:
            self.runtime_errors.record("speaker_verification_failed", exc)
            self.last_error = f"speaker_verification_failed: {type(exc).__name__}"
            return None

    async def _barge_in_worker(self) -> None:
        while True:
            pcm_bytes, meta = await self.barge_in_queue.get()
            handed_off = False
            try:
                segment_epoch = meta.get(
                    "_admissionEpoch",
                    self.admission_epoch,
                )
                if not self._voice_admission_lifecycle_is_current(
                    segment_epoch
                ):
                    continue
                verification_required = (
                    self._speaker_verification_required_for_barge_in()
                )
                verification = await self._verify_barge_in_speaker(pcm_bytes)
                if not self._voice_admission_lifecycle_is_current(
                    segment_epoch
                ):
                    self._emit_validation(
                        "barge_in_rejected",
                        meta=meta,
                        reason="admission_epoch_stale",
                    )
                    continue
                decision = evaluate_local_barge_in(
                    meta,
                    body_rms_min=VOICE_WAVEFORM_BODY_RMS_MIN,
                    speaker_verification=verification,
                    speaker_verification_required=verification_required,
                )
                decision_payload = {
                    "reason": decision.reason,
                    "vadProb": round(decision.interrupt_meta.vad_prob, 4),
                    "audioSec": round(decision.interrupt_meta.audio_sec, 3),
                    "rmsOk": decision.interrupt_meta.rms_ok,
                    "speakerVerification": decision.speaker_verification,
                }
                if not decision.accepted:
                    self._emit_validation(
                        "barge_in_rejected",
                        meta=meta,
                        **decision_payload,
                    )
                    continue
                source_binding = dict(meta.get("_bargeSource") or {})
                if not (
                    validation_attempt_binding_is_current(
                        meta,
                        surface="local",
                        reject_unbound_when_active=True,
                    )
                    and validation_attempt_binding_is_current(
                        source_binding,
                        surface="local",
                        reject_unbound_when_active=True,
                    )
                ):
                    self._emit_validation(
                        "barge_in_rejected",
                        meta=meta,
                        **{**decision_payload, "reason": "validation_attempt_stale"},
                    )
                    continue
                with self._barge_source_lock:
                    active_owner_id = self.playback_controller.owner_id
                    active_owner_token = self.playback_controller.owner_token
                    current_source = (
                        dict(self._barge_source_snapshot)
                        if self._barge_source_snapshot is not None
                        else None
                    )
                    released_source = (
                        dict(self._last_released_barge_source)
                        if self._last_released_barge_source is not None
                        else None
                    )
                owner_generation_matches = local_barge_source_binding_matches(
                    meta,
                    active_turn_id=self.active_turn_id,
                    active_validation=self.active_validation,
                    active_owner_id=active_owner_id,
                    active_owner_token=active_owner_token,
                )
                if owner_generation_matches and current_source is None:
                    self._emit_validation(
                        "barge_in_rejected",
                        meta=meta,
                        **{
                            **decision_payload,
                            "reason": "barge_in_playback_not_started",
                        },
                    )
                    continue
                source_matches_current_owner = bool(
                    owner_generation_matches
                    and current_source is not None
                    and source_binding.get("ownerToken")
                    is current_source.get("ownerToken")
                )
                if not source_matches_current_owner:
                    source_matches_released_owner = bool(
                        not self.active_validation
                        and not active_owner_id
                        and active_owner_token is None
                        and released_source
                        and local_barge_source_binding_matches(
                            meta,
                            active_turn_id=self.active_turn_id,
                            active_validation=None,
                            active_owner_id=str(
                                released_source.get("ownerId") or ""
                            ),
                            active_owner_token=released_source.get("ownerToken"),
                        )
                    )
                    if source_matches_released_owner:
                        meta["_requiresFreshWake"] = True
                        if self.priority_queue.full():
                            with contextlib.suppress(Exception):
                                _dropped_pcm, dropped_meta = self.priority_queue.get_nowait()
                                self._abandon_local_asr_meta(dropped_meta)
                                self.priority_queue.task_done()
                        self.priority_queue.put_nowait((pcm_bytes, meta))
                        handed_off = True
                        continue
                    self._emit_validation(
                        "barge_in_rejected",
                        meta=meta,
                        **{**decision_payload, "reason": "barge_in_stale_source"},
                    )
                    continue
                original_turn_id = str(source_binding.get("turnId") or "")
                source_owner_id = str(source_binding.get("ownerId") or "")
                source_owner_token = source_binding.get("ownerToken")
                original_context = {
                    "sessionId": source_binding.get("validationSessionId"),
                    "stepId": source_binding.get("validationStepId"),
                    "attempt": source_binding.get("validationAttempt"),
                    "attemptId": source_binding.get("validationAttemptId"),
                }
                if not original_context["sessionId"] or not original_context["stepId"]:
                    original_context = None
                controller_cancelled = self.playback_controller.request_cancel(
                    expected_owner_id=source_owner_id,
                    expected_owner_token=source_owner_token,
                )
                if not controller_cancelled:
                    self._emit_validation(
                        "barge_in_rejected",
                        meta=meta,
                        **{**decision_payload, "reason": "barge_in_stale_source"},
                    )
                    continue
                if original_context and original_turn_id:
                    emit_voice_validation_event(
                        "local",
                        "tts_interrupt",
                        session_id=original_context.get("sessionId"),
                        step_id=original_context.get("stepId"),
                        attempt_id=original_context.get("attemptId"),
                        turnId=original_turn_id,
                        sourceTurnId=original_turn_id,
                        qualified=True,
                        reason="qualified_user_audio",
                    )
                if not self.playback_cancelled_for_turn:
                    self.playback_cancelled_for_turn = True
                    if original_context:
                        emit_voice_validation_event(
                            "local",
                            "playback_cancelled",
                            session_id=original_context.get("sessionId"),
                            step_id=original_context.get("stepId"),
                            attempt_id=original_context.get("attemptId"),
                            turnId=original_turn_id,
                            reason=decision.reason,
                        )
                meta["bargeInAccepted"] = True
                meta["interruptedTurnId"] = original_turn_id
                self._emit_validation(
                    "barge_in_accepted",
                    meta=meta,
                    **decision_payload,
                )
                if self.priority_queue.full():
                    with contextlib.suppress(Exception):
                        _dropped_pcm, dropped_meta = self.priority_queue.get_nowait()
                        self._abandon_local_asr_meta(dropped_meta)
                        self.priority_queue.task_done()
                self.priority_queue.put_nowait((pcm_bytes, meta))
                handed_off = True
            finally:
                if not handed_off:
                    self._abandon_local_asr_meta(meta)
                self.barge_in_queue.task_done()

    def _discard_pending_mic_segments(self) -> int:
        discarded = 0
        for source_queue in (
            self.queue,
            self.priority_queue,
            self.barge_in_queue,
        ):
            while True:
                try:
                    _discarded_pcm, discarded_meta = source_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._abandon_local_asr_meta(discarded_meta)
                source_queue.task_done()
                discarded += 1
        self.discarded_pending_mic_segment_count += discarded
        return discarded

    async def _handle_segment(self, pcm_bytes: bytes, meta: dict[str, Any]) -> None:
        turn_admission_epoch = meta.get(
            "_admissionEpoch",
            self.admission_epoch,
        )
        if not self._voice_admission_lifecycle_is_current(
            turn_admission_epoch
        ):
            self._abandon_local_asr_meta(meta)
            self.discarded_pending_mic_segment_count += 1
            return
        turn_started = time.perf_counter()
        self.active_turn_started_at = turn_started
        self.active_turn_id = str(meta.get("turnId") or uuid.uuid4().hex)
        meta["turnId"] = self.active_turn_id
        raw_capture_generation = meta.get(
            "_mainForegroundCaptureGeneration"
        )
        if type(raw_capture_generation) is not int:
            self.main_foreground_capture_generation += 1
            raw_capture_generation = self.main_foreground_capture_generation
            meta["_mainForegroundCaptureGeneration"] = (
                raw_capture_generation
            )
        main_capture_generation = main_capture_generation_from_wire(
            raw_capture_generation
        )
        main_foreground_enabled = (
            self.main_foreground_reservation_enabled
        )
        main_foreground_attempted = False
        main_foreground_reservation: MainForegroundReservation | None = None
        main_foreground_issued_at: float | None = None
        main_foreground_grant: dict[str, Any] | None = None
        self.active_validation = self._validation_context_from_meta(meta)
        self.playback_started_for_turn = False
        self.playback_cancelled_for_turn = False
        self.reply_started_for_turn = False
        self.reply_final_for_turn = False
        self.active_conversation_playback_ack = None
        stt_ms: float | None = None
        chat_ms: float | None = None
        tts_ms: float | None = None
        self.segment_count += 1
        self._emit_validation(
            "capture",
            meta=meta,
            durationSec=meta.get("duration_sec"),
            bargeIn=bool(meta.get("bargeInAccepted")),
        )
        public_segment_meta = dict(meta)
        public_segment_meta.pop("_bargeSource", None)
        public_segment_meta.pop("_admissionEpoch", None)
        public_segment_meta.pop("_requiresFreshWake", None)
        public_segment_meta.pop("_asrStreamKey", None)
        public_segment_meta.pop("_asrCaptureGeneration", None)
        public_segment_meta.pop("_mainForegroundCaptureGeneration", None)
        public_segment_meta.pop("validationAttemptId", None)
        public_segment_meta.pop("validation_attempt_id", None)
        await self._post_status(extra={"lastSegmentMeta": public_segment_meta})
        if not self._voice_admission_lifecycle_is_current(
            turn_admission_epoch
        ):
            self._abandon_local_asr_meta(meta)
            self.discarded_pending_mic_segment_count += 1
            return
        if not validation_attempt_binding_is_current(
            meta,
            surface="local",
            reject_unbound_when_active=True,
        ):
            self._abandon_local_asr_meta(meta)
            self.discarded_pending_mic_segment_count += 1
            return
        try:
            if main_foreground_enabled and self.admission_active:
                main_foreground_attempted = True
                main_foreground_issued_at = (
                    _local_main_foreground_monotonic()
                )
                main_foreground_reservation = (
                    await self._reserve_main_foreground_before_stt(
                        main_capture_generation
                    )
                )
                if main_foreground_reservation is None:
                    main_foreground_issued_at = None
            stage_started = time.perf_counter()
            text = await self._transcribe_stream_or_batch(pcm_bytes, meta)
            stt_ms = (time.perf_counter() - stage_started) * 1000.0
            if not validation_attempt_binding_is_current(
                meta,
                surface="local",
                reject_unbound_when_active=True,
            ):
                self.discarded_pending_mic_segment_count += 1
                return
            if not self._voice_admission_lifecycle_is_current(
                turn_admission_epoch
            ):
                self.discarded_pending_mic_segment_count += 1
                return
            context = self._validation_context_from_meta(meta)
            if len(text) < LOCAL_BRIDGE_MIN_TEXT_CHARS:
                if context:
                    self._emit_validation(
                        "error",
                        meta=meta,
                        errorCode="stt_transcript_too_short",
                    )
                return
            transcript_text = text
            if meta.get("_requiresFreshWake"):
                fresh_wake, _ = split_exact_leading_wake(
                    transcript_text
                )
                if not fresh_wake:
                    self._record_local_voice_admission_rejection(
                        "fresh_wake_required"
                    )
                    return
            if context:
                emit_transcript_validation_event(
                    "local",
                    transcript_text,
                    session_id=context.get("sessionId"),
                    step_id=context.get("stepId"),
                    attempt_id=context.get("attemptId"),
                    turnId=self.active_turn_id,
                )
            grant = await self._request_voice_admission(
                transcript_text,
                turn_id=self.active_turn_id,
                validation=self._admission_validation_binding(meta),
                expected_epoch=int(turn_admission_epoch),
            )
            if grant is None:
                if context:
                    self._emit_validation(
                        "error",
                        meta=meta,
                        errorCode=(
                            self.admission_last_reason
                            or "local_voice_admission_rejected"
                        ),
                    )
                return
            main_foreground_grant = grant
            if main_foreground_enabled:
                grant["mainCaptureGeneration"] = main_capture_generation
                grant["mainForegroundReservationAttempted"] = (
                    main_foreground_attempted
                )
                if main_foreground_reservation is not None:
                    grant["mainForegroundReservation"] = (
                        main_foreground_reservation
                    )
                    grant["mainForegroundReservationIssuedMonotonic"] = (
                        main_foreground_issued_at
                    )
            text = clean_text(grant.get("forwardText"))
            if not text:
                self._record_local_voice_admission_rejection(
                    "admission_forward_text_invalid"
                )
                return
            self.transcript_count += 1
            print(f"[LOCAL BRIDGE] transcript_received chars={len(text)}", flush=True)
            if should_suppress_tts_for_command(text):
                stage_started = time.perf_counter()
                chat_result = await self._chat(text, grant=grant)
                reply = chat_result.text
                chat_ms = (time.perf_counter() - stage_started) * 1000.0
                if reply:
                    self._mark_reply_final_once()
                tts_ms = 0.0
            elif LOCAL_BRIDGE_STREAMING_TTS_ENABLED and LOCAL_BRIDGE_TTS_ENABLED:
                try:
                    stream_result = await self._chat_stream_and_speak(
                        text,
                        grant=grant,
                    )
                    reply = clean_text(stream_result.get("reply"))
                    chat_ms = stream_result.get("chatMs")
                    tts_ms = stream_result.get("ttsMs")
                except LocalVoiceAdmissionDrop:
                    raise
                except Exception as stream_exc:
                    self.runtime_errors.record("chat_stream_failed", stream_exc)
                    dispatched = not (
                        isinstance(stream_exc, LocalChatStreamFailure)
                        and not stream_exc.bot_dispatched
                    )
                    if dispatched:
                        self._emit_validation(
                            "playback_failed",
                            meta=meta,
                            errorCode="streaming_tts_failed_after_dispatch",
                        )
                        raise RuntimeError(
                            "streaming_tts_failed_after_dispatch"
                        ) from stream_exc
                    print(
                        "[LOCAL BRIDGE] chat_stream_failed_before_dispatch "
                        "fallback_to_full=true",
                        flush=True,
                    )
                    stage_started = time.perf_counter()
                    chat_result = await self._chat(text, grant=grant)
                    reply = chat_result.text
                    chat_ms = (time.perf_counter() - stage_started) * 1000.0
                    if reply:
                        self._mark_reply_final_once()
                        stage_started = time.perf_counter()
                        await self._speak_chat_reply(chat_result)
                        tts_ms = (time.perf_counter() - stage_started) * 1000.0
            else:
                stage_started = time.perf_counter()
                chat_result = await self._chat(text, grant=grant)
                reply = chat_result.text
                chat_ms = (time.perf_counter() - stage_started) * 1000.0
                if reply and LOCAL_BRIDGE_TTS_ENABLED:
                    self._mark_reply_final_once()
                    stage_started = time.perf_counter()
                    await self._speak_chat_reply(chat_result)
                    tts_ms = (time.perf_counter() - stage_started) * 1000.0
                elif reply:
                    await self._report_chat_reply_playback_failure(
                        chat_result
                    )
            if reply:
                self._mark_reply_final_once()
            if self.playback_started_for_turn and not self.playback_cancelled_for_turn:
                self._emit_validation("playback_completed", meta=meta)
                if meta.get("bargeInAccepted"):
                    self._emit_validation(
                        "barge_in_continuity",
                        meta=meta,
                        status="success",
                        reason="replacement_playback_completed",
                    )
            elif reply and LOCAL_BRIDGE_TTS_ENABLED and not should_suppress_tts_for_command(text):
                self._emit_validation(
                    "playback_failed",
                    meta=meta,
                    errorCode="playback_not_started",
                )
            print(f"[LOCAL BRIDGE] reply_ready chars={len(reply or '')}", flush=True)
        except LocalVoiceAdmissionDrop as exc:
            self._record_local_voice_admission_rejection(exc.reason)
            self._emit_validation(
                "error",
                meta=meta,
                errorCode=exc.reason,
            )
        except asyncio.CancelledError:
            if self.playback_started_for_turn and not self.playback_cancelled_for_turn:
                self.playback_cancelled_for_turn = True
                self._emit_validation(
                    "playback_cancelled",
                    meta=meta,
                    reason="turn_cancelled",
                )
            await self._report_conversation_delivery(
                self.active_conversation_playback_ack,
                outcome="cancelled",
            )
            raise
        except Exception as exc:
            await self._report_conversation_delivery(
                self.active_conversation_playback_ack,
                outcome=self._playback_outcome(completed=False),
            )
            self.runtime_errors.record("turn_pipeline_failed", exc)
            self.last_error = "turn_pipeline_failed"
            self._emit_validation(
                "error",
                meta=meta,
                errorCode=type(exc).__name__,
            )
            print(
                "[LOCAL BRIDGE] segment_failed "
                f"errorType={type(exc).__name__}",
                flush=True,
            )
        finally:
            terminal_main_foreground_reservation = (
                main_foreground_grant.get("mainForegroundReservation")
                if main_foreground_grant is not None
                else main_foreground_reservation
            )
            if terminal_main_foreground_reservation is not None:
                await self._cancel_main_foreground_reservation(
                    terminal_main_foreground_reservation
                )
            total_ms = (time.perf_counter() - turn_started) * 1000.0
            self.last_latency = {
                "sttMs": round(stt_ms, 1) if stt_ms is not None else None,
                "chatMs": round(chat_ms, 1) if chat_ms is not None else None,
                "ttsMs": round(tts_ms, 1) if tts_ms is not None else None,
                "totalMs": round(total_ms, 1),
            }
            print(
                "[LOCAL BRIDGE] turn_timing "
                f"stt_ms={self.last_latency['sttMs']} "
                f"chat_ms={self.last_latency['chatMs']} "
                f"tts_ms={self.last_latency['ttsMs']} "
                f"total_ms={self.last_latency['totalMs']}",
                flush=True,
            )
            try:
                await self._post_status()
            finally:
                self.active_conversation_playback_ack = None

    async def _transcribe_stream_or_batch(
        self,
        pcm_bytes: bytes,
        meta: dict[str, Any],
    ) -> str:
        key = self._local_asr_key_from_meta(meta)
        if key is not None and not self._voice_admission_lifecycle_is_current(key[0]):
            self._abandon_local_asr_stream(key)
            return ""
        future = self._local_asr_stream_futures.get(key) if key is not None else None
        if future is None:
            return await self._transcribe(pcm_bytes)

        streamed_text: str | None = None
        try:
            streamed_text = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=45.0,
            )
        except asyncio.TimeoutError as exc:
            self.runtime_errors.record("stt_timeout", exc)
            self._abandon_local_asr_stream(key)
        except asyncio.CancelledError:
            self._abandon_local_asr_stream(key)
            raise
        except Exception as exc:
            self.runtime_errors.record("stt_transcribe_failed", exc)
            self._abandon_local_asr_stream(key)
        finally:
            if self._local_asr_stream_futures.get(key) is future:
                self._local_asr_stream_futures.pop(key, None)

        final_text = clean_text(streamed_text)
        if final_text:
            return final_text
        if key is not None and not self._voice_admission_lifecycle_is_current(key[0]):
            return ""
        return await self._transcribe(pcm_bytes)

    async def _transcribe(self, pcm_bytes: bytes) -> str:
        audio16k = np.asarray(prepare_stt_audio(pcm_bytes), dtype=np.float32)
        payload = {
            "audio_f32_base64": base64.b64encode(audio16k.tobytes()).decode("ascii"),
            "sample_count": int(audio16k.size),
            "sampling_rate": TARGET_RATE,
            "stage": "local_bridge",
            "language": "Korean",
            "validation_bound": bool(self.active_validation),
        }
        assert self.session is not None
        async with self.session.post(f"{STT_SERVICE_URL}/v1/stt/transcribe", json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"stt_failed {resp.status}: {data}")
            return clean_text(data.get("text"))

    async def _chat(
        self,
        text: str,
        *,
        grant: dict[str, Any],
    ) -> LocalChatReply:
        assert self.session is not None
        payload = await self._local_voice_chat_payload(text, grant)
        grant["_botDispatched"] = True
        async with self._local_voice_bot_response(
            "/api/control-page/chat",
            payload=payload,
            grant=grant,
            timeout_sec=150,
        ) as resp:
            data = await resp.json(content_type=None)
            self._apply_voice_admission_status(data)
            if resp.status == 409 and isinstance(data, dict) and data.get("error") == "local_voice_wake_required":
                raise LocalVoiceAdmissionDrop(
                    clean_text(data.get("reason")) or "local_voice_wake_required"
                )
            if resp.status != 200 or not isinstance(data, dict) or not data.get("ok"):
                raise RuntimeError(f"chat_failed_{resp.status}")
            reply = clean_text(data.get("reply"))
            if not reply:
                raise RuntimeError("chat_reply_empty")
            return LocalChatReply(
                text=reply,
                memory_handoff=parse_local_memory_handoff(data),
                playback_ack=self._playback_ack_from_response(
                    data,
                    grant=grant,
                    assistant_text=reply,
                ),
            )

    async def _speak_chat_reply(self, result: LocalChatReply) -> None:
        self._remember_conversation_playback_ack(result.playback_ack)

        async def speak_and_report() -> None:
            play_count = self.play_count
            try:
                await self._speak(result.text)
            except asyncio.CancelledError:
                await self._report_conversation_delivery(
                    result.playback_ack,
                    outcome="cancelled",
                )
                raise
            except Exception:
                await self._report_conversation_delivery(
                    result.playback_ack,
                    outcome=self._playback_outcome(completed=False),
                )
                raise
            await self._report_conversation_delivery(
                result.playback_ack,
                outcome=self._playback_outcome(
                    completed=self.play_count == play_count + 1,
                ),
            )

        handoff = result.memory_handoff
        if handoff.state == "not_used":
            await speak_and_report()
            return
        if handoff.position is None:
            raise MemoryDeletionJournalIntegrityError()
        with memory_exposure_guard(
            expected_position=handoff.position,
            required=True,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        ):
            await speak_and_report()

    async def _report_chat_reply_playback_failure(
        self,
        result: LocalChatReply,
    ) -> None:
        if result.playback_ack is None:
            return
        self._remember_conversation_playback_ack(result.playback_ack)

        async def report() -> None:
            await self._report_conversation_delivery(
                result.playback_ack,
                outcome="failed",
            )

        handoff = result.memory_handoff
        if handoff.state == "not_used":
            await report()
            return
        if handoff.position is None:
            raise MemoryDeletionJournalIntegrityError()
        with memory_exposure_guard(
            expected_position=handoff.position,
            required=True,
            index_dir=Path(MEMORY_ROOT) / "memory_index",
        ):
            await report()

    async def _chat_stream_and_speak(
        self,
        text: str,
        *,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        grant["_botDispatched"] = False
        try:
            if LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED and sd is not None:
                return await self._chat_delta_stream_and_speak(
                    text,
                    grant=grant,
                )
            return await self._chat_sentence_stream_and_speak(
                text,
                grant=grant,
            )
        except asyncio.CancelledError:
            await self._report_conversation_delivery(
                self.active_conversation_playback_ack,
                outcome="cancelled",
            )
            raise
        except LocalVoiceAdmissionDrop:
            raise
        except LocalChatStreamFailure:
            raise
        except Exception as exc:
            await self._report_conversation_delivery(
                self.active_conversation_playback_ack,
                outcome=self._playback_outcome(completed=False),
            )
            raise LocalChatStreamFailure(
                bot_dispatched=bool(grant.get("_botDispatched")),
            ) from exc

    @staticmethod
    def _stop_delta_output_stream(stream: Any | None) -> None:
        if stream is None:
            return
        for method_name in ("abort", "stop"):
            method = getattr(stream, method_name, None)
            if not callable(method):
                continue
            try:
                method()
                break
            except Exception:
                continue

    @staticmethod
    async def _await_delta_task_shutdown(
        task: asyncio.Task[Any],
        *,
        cancel: bool,
    ) -> None:
        if cancel and not task.done():
            task.cancel()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Repeated parent cancellation cannot release the playback owner
                # while a receiver or websocket-close task is still running.
                continue
            except Exception:
                break
        with contextlib.suppress(asyncio.CancelledError, Exception):
            task.result()

    async def _write_delta_stream_chunk(self, stream: Any, payload: bytes) -> None:
        self._ensure_validation_attempt_current()
        write_task = asyncio.create_task(
            asyncio.to_thread(stream.write, payload),
            name="local-voxcpm-output-write",
        )
        try:
            await asyncio.shield(write_task)
        except asyncio.CancelledError:
            self._stop_delta_output_stream(stream)
            await self._await_delta_task_shutdown(write_task, cancel=False)
            raise

    async def _teardown_delta_playback(
        self,
        *,
        playback_owner: str,
        websocket: Any | None,
        receiver: asyncio.Task[Any] | None,
        output_stream: Any | None,
    ) -> None:
        self._stop_delta_output_stream(output_stream)
        if receiver is not None and not receiver.done():
            receiver.cancel()

        if websocket is not None and not bool(getattr(websocket, "closed", False)):
            with contextlib.suppress(Exception):
                close_task = asyncio.create_task(
                    websocket.close(),
                    name="local-voxcpm-websocket-close",
                )
                await self._await_delta_task_shutdown(close_task, cancel=False)

        if receiver is not None:
            await self._await_delta_task_shutdown(receiver, cancel=False)
        self._release_playback_owner(playback_owner)

    async def _chat_delta_stream_and_speak(
        self,
        text: str,
        *,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.session is not None
        started_at = time.perf_counter()
        sentence_count = 0
        first_sentence_ms: float | None = None
        first_delta_ms: float | None = None
        first_progress_ms: float | None = None
        progress_count = 0
        final_reply = ""
        memory_handoff: LocalMemoryHandoff | None = None
        playback_ack: dict[str, Any] | None = None
        buffered_tts_commands: list[dict[str, str]] = []
        done_seen = False
        chat_done_ms: float | None = None
        audio_bytes = 0
        played_bytes = 0
        first_playback_ms: float | None = None
        websocket: aiohttp.ClientWebSocketResponse | None = None
        receiver: asyncio.Task[None] | None = None
        active_output_stream: Any | None = None
        playback_owner = self._claim_playback_owner()
        playback_owner_token = self.playback_controller.owner_token
        if playback_owner_token is None:
            self._release_playback_owner(playback_owner)
            raise RuntimeError("playback_owner_token_missing")

        self.speaking = True

        async def receive_tts_audio() -> None:
            nonlocal audio_bytes, played_bytes, first_playback_ms, active_output_stream
            remainder = b""
            with sd.RawOutputStream(
                samplerate=TTS_PCM_RATE,
                channels=TTS_PCM_CHANNELS,
                dtype=TTS_PCM_DTYPE,
                device=self.output_device,
            ) as stream:
                active_output_stream = stream
                try:
                    assert websocket is not None
                    async for message in websocket:
                        if message.type == aiohttp.WSMsgType.BINARY:
                            chunk = bytes(message.data or b"")
                            if not chunk:
                                continue
                            audio_bytes += len(chunk)
                            data = remainder + chunk
                            aligned_len = len(data) - (len(data) % TTS_SAMPLE_WIDTH_BYTES)
                            if aligned_len > 0:
                                playable = data[:aligned_len]
                                await self._write_delta_stream_chunk(stream, playable)
                                if first_playback_ms is None:
                                    first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                                    self._mark_playback_started_once(
                                        expected_owner_id=playback_owner,
                                        expected_owner_token=playback_owner_token,
                                    )
                                played_bytes += len(playable)
                            remainder = data[aligned_len:]
                            continue
                        if message.type == aiohttp.WSMsgType.TEXT:
                            event = json.loads(str(message.data or "{}"))
                            event_type = clean_text(event.get("type"))
                            if event_type == "error":
                                raise RuntimeError(clean_text(event.get("error")) or "voxcpm_stream_failed")
                            if event_type in {"done", "canceled"}:
                                break
                            continue
                        if message.type in {
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            break
                    if remainder:
                        padded = remainder + (b"\x00" * (TTS_SAMPLE_WIDTH_BYTES - len(remainder)))
                        await self._write_delta_stream_chunk(stream, padded)
                        if first_playback_ms is None:
                            first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                            self._mark_playback_started_once(
                                expected_owner_id=playback_owner,
                                expected_owner_token=playback_owner_token,
                            )
                        played_bytes += len(padded)
                    if played_bytes > 0:
                        await self._write_delta_stream_chunk(
                            stream,
                            b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18),
                        )
                finally:
                    if active_output_stream is stream:
                        active_output_stream = None

        async def ensure_tts_receiver() -> asyncio.Task[None]:
            nonlocal receiver
            if receiver is None:
                receiver = asyncio.create_task(
                    receive_tts_audio(),
                    name="local-voxcpm-stream-receiver",
                )
            return receiver

        async def submit_tts_command(command: dict[str, str]) -> None:
            if memory_handoff is None:
                raise MemoryDeletionJournalIntegrityError()
            if memory_handoff.state == "bound":
                buffered_tts_commands.append(dict(command))
                return
            if websocket is None:
                raise RuntimeError("voxcpm_stream_unavailable")
            await ensure_tts_receiver()
            await websocket.send_json(command)

        async def finish_tts_playback(*, send_buffered: bool) -> None:
            nonlocal receiver
            try:
                receiver = await ensure_tts_receiver()
                if send_buffered:
                    for command in buffered_tts_commands:
                        await websocket.send_json(command)
                await websocket.send_json({"type": "flush"})
                await receiver
                if audio_bytes <= 0 or played_bytes <= 0:
                    raise RuntimeError(
                        "voxcpm_stream_empty_audio "
                        f"audio_bytes={audio_bytes} "
                        f"played_bytes={played_bytes}"
                    )
            except asyncio.CancelledError:
                await self._report_conversation_delivery(
                    playback_ack,
                    outcome="cancelled",
                )
                raise
            except Exception:
                await self._report_conversation_delivery(
                    playback_ack,
                    outcome=self._playback_outcome(completed=False),
                )
                raise

            self.play_count += 1
            self.last_error = ""
            self.last_tts_playback = {
                "voice": "clone:evelyn",
                "audioBytes": audio_bytes,
                "playedBytes": played_bytes,
                "firstPlaybackMs": (
                    round(first_playback_ms, 1)
                    if first_playback_ms is not None
                    else None
                ),
                "inputStreaming": True,
            }
            await self._report_conversation_delivery(
                playback_ack,
                outcome=self._playback_outcome(completed=True),
            )

        try:
            await self._post_status()
            websocket = await self.session.ws_connect(
                voxcpm_stream_url(),
                heartbeat=30.0,
                receive_timeout=180.0,
                max_msg_size=0,
            )
            ready = await websocket.receive_json(timeout=30.0)
            if clean_text(ready.get("type")) != "ready":
                raise RuntimeError(f"voxcpm_stream_not_ready: {ready}")
            await websocket.send_json({"type": "start"})

            payload = await self._local_voice_chat_payload(text, grant)
            grant["_botDispatched"] = True
            async with self._local_voice_bot_response(
                "/api/control-page/chat-stream",
                payload=payload,
                grant=grant,
                timeout_sec=180,
            ) as resp:
                if resp.status != 200:
                    try:
                        failure = await resp.json(content_type=None)
                    except Exception:
                        failure = {}
                    self._apply_voice_admission_status(failure)
                    if (
                        resp.status == 409
                        and isinstance(failure, dict)
                        and failure.get("error") == "local_voice_wake_required"
                    ):
                        raise LocalVoiceAdmissionDrop(
                            clean_text(failure.get("reason"))
                            or "local_voice_wake_required"
                        )
                    raise RuntimeError(f"chat_stream_failed_{resp.status}")
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        raise MemoryDeletionJournalIntegrityError() from None
                    if not isinstance(event, dict):
                        raise MemoryDeletionJournalIntegrityError()
                    event_type = clean_text(event.get("type"))
                    if event_type != "done":
                        pending_ack = self._playback_ack_from_response(
                            event,
                            grant=grant,
                            assistant_text=None,
                            allow_pending=True,
                        )
                        if pending_ack is not None:
                            playback_ack = (
                                self._remember_conversation_playback_ack(
                                    pending_ack
                                )
                            )
                    if event_type == "memory_boundary":
                        if memory_handoff is not None:
                            raise MemoryDeletionJournalIntegrityError()
                        memory_handoff = parse_local_memory_handoff(event)
                        continue
                    if memory_handoff is None:
                        raise MemoryDeletionJournalIntegrityError()
                    if event_type == "progress":
                        progress_text = clean_tts_text(event.get("text"))
                        if not progress_text:
                            continue
                        progress_count += 1
                        if first_progress_ms is None:
                            first_progress_ms = (time.perf_counter() - started_at) * 1000.0
                        await submit_tts_command(
                            {"type": "append", "text": progress_text}
                        )
                        await submit_tts_command({"type": "commit"})
                        continue
                    if event_type == "delta":
                        fragment = str(event.get("text") or "")
                        if not fragment:
                            continue
                        if first_delta_ms is None:
                            first_delta_ms = (time.perf_counter() - started_at) * 1000.0
                        continue
                    if event_type == "sentence":
                        sentence = clean_tts_text(event.get("text"))
                        if not sentence:
                            continue
                        sentence_count += 1
                        if first_sentence_ms is None:
                            first_sentence_ms = (time.perf_counter() - started_at) * 1000.0
                        await submit_tts_command(
                            {"type": "append", "text": sentence}
                        )
                        await submit_tts_command({"type": "commit"})
                        continue
                    if event_type == "done":
                        if done_seen:
                            raise MemoryDeletionJournalIntegrityError()
                        if parse_local_memory_handoff(event) != memory_handoff:
                            raise MemoryDeletionJournalIntegrityError()
                        done_seen = True
                        final_reply = clean_text(event.get("reply"))
                        final_playback_ack = self._playback_ack_from_response(
                            event,
                            grant=grant,
                            assistant_text=final_reply,
                        )
                        if (
                            playback_ack is not None
                            and final_playback_ack is None
                        ):
                            raise MemoryDeletionJournalIntegrityError()
                        playback_ack = (
                            self._remember_conversation_playback_ack(
                                final_playback_ack
                            )
                        )
                        if final_reply:
                            self._mark_reply_final_once()
                        chat_done_ms = (time.perf_counter() - started_at) * 1000.0
                        continue
                    if event_type == "error":
                        raise RuntimeError(clean_text(event.get("error")) or "chat_stream_failed")

            if memory_handoff is None or not done_seen:
                raise MemoryDeletionJournalIntegrityError()
            if memory_handoff.state == "bound":
                if memory_handoff.position is None:
                    raise MemoryDeletionJournalIntegrityError()
                with memory_exposure_guard(
                    expected_position=memory_handoff.position,
                    required=True,
                    index_dir=Path(MEMORY_ROOT) / "memory_index",
                ):
                    await finish_tts_playback(send_buffered=True)
            else:
                await finish_tts_playback(send_buffered=False)
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            print(
                "[LOCAL BRIDGE] delta_stream_reply "
                f"sentence_count={sentence_count} "
                f"progress_count={progress_count} "
                f"first_progress_ms={round(first_progress_ms, 1) if first_progress_ms is not None else None} "
                f"first_delta_ms={round(first_delta_ms, 1) if first_delta_ms is not None else None} "
                f"first_sentence_ms={round(first_sentence_ms, 1) if first_sentence_ms is not None else None} "
                f"first_playback_ms={self.last_tts_playback['firstPlaybackMs']} "
                f"audio_bytes={audio_bytes}",
                flush=True,
            )
            return {
                "reply": final_reply,
                "sentenceCount": sentence_count,
                "progressCount": progress_count,
                "firstProgressMs": round(first_progress_ms, 1) if first_progress_ms is not None else None,
                "firstSentenceMs": round(first_sentence_ms, 1) if first_sentence_ms is not None else None,
                "firstDeltaMs": round(first_delta_ms, 1) if first_delta_ms is not None else None,
                "chatMs": chat_done_ms if chat_done_ms is not None else elapsed_ms,
                "ttsMs": elapsed_ms,
            }
        except Exception:
            if websocket is not None and not websocket.closed:
                with contextlib.suppress(Exception):
                    await websocket.send_json({"type": "cancel"})
            raise
        finally:
            await self._teardown_delta_playback(
                playback_owner=playback_owner,
                websocket=websocket,
                receiver=receiver,
                output_stream=active_output_stream,
            )
            self.speaking = False
            self.mic_input_suppressed_until = time.monotonic() + LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC
            await self._post_status()

    async def _chat_sentence_stream_and_speak(
        self,
        text: str,
        *,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.session is not None
        started_at = time.perf_counter()
        tts_ms = 0.0
        sentence_count = 0
        first_sentence_ms: float | None = None
        final_reply = ""
        memory_handoff: LocalMemoryHandoff | None = None
        playback_ack: dict[str, Any] | None = None
        buffered_sentences: list[str] = []
        played_sentence_count = 0
        done_seen = False

        async def speak_sentence(sentence: str) -> None:
            nonlocal played_sentence_count, tts_ms
            play_count = self.play_count
            speak_started = time.perf_counter()
            await self._speak(sentence)
            tts_ms += (time.perf_counter() - speak_started) * 1000.0
            if self.play_count == play_count + 1:
                played_sentence_count += 1

        async def report_stream_outcome() -> None:
            await self._report_conversation_delivery(
                playback_ack,
                outcome=self._playback_outcome(
                    completed=(
                        sentence_count > 0
                        and played_sentence_count == sentence_count
                    ),
                ),
            )

        payload = await self._local_voice_chat_payload(text, grant)
        grant["_botDispatched"] = True
        async with self._local_voice_bot_response(
            "/api/control-page/chat-stream",
            payload=payload,
            grant=grant,
            timeout_sec=180,
        ) as resp:
            if resp.status != 200:
                try:
                    failure = await resp.json(content_type=None)
                except Exception:
                    failure = {}
                self._apply_voice_admission_status(failure)
                if (
                    resp.status == 409
                    and isinstance(failure, dict)
                    and failure.get("error") == "local_voice_wake_required"
                ):
                    raise LocalVoiceAdmissionDrop(
                        clean_text(failure.get("reason"))
                        or "local_voice_wake_required"
                    )
                raise RuntimeError(f"chat_stream_failed_{resp.status}")
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    raise MemoryDeletionJournalIntegrityError() from None
                if not isinstance(event, dict):
                    raise MemoryDeletionJournalIntegrityError()
                event_type = clean_text(event.get("type"))
                if event_type != "done":
                    pending_ack = self._playback_ack_from_response(
                        event,
                        grant=grant,
                        assistant_text=None,
                        allow_pending=True,
                    )
                    if pending_ack is not None:
                        playback_ack = (
                            self._remember_conversation_playback_ack(
                                pending_ack
                            )
                        )
                if event_type == "memory_boundary":
                    if memory_handoff is not None:
                        raise MemoryDeletionJournalIntegrityError()
                    memory_handoff = parse_local_memory_handoff(event)
                    continue
                if memory_handoff is None:
                    raise MemoryDeletionJournalIntegrityError()
                if event_type == "sentence":
                    sentence = clean_text(event.get("text"))
                    if not sentence:
                        continue
                    sentence_count += 1
                    if first_sentence_ms is None:
                        first_sentence_ms = (time.perf_counter() - started_at) * 1000.0
                    if memory_handoff.state == "bound":
                        buffered_sentences.append(sentence)
                    else:
                        await speak_sentence(sentence)
                    continue
                if event_type == "done":
                    if done_seen:
                        raise MemoryDeletionJournalIntegrityError()
                    if parse_local_memory_handoff(event) != memory_handoff:
                        raise MemoryDeletionJournalIntegrityError()
                    done_seen = True
                    final_reply = clean_text(event.get("reply"))
                    final_playback_ack = self._playback_ack_from_response(
                        event,
                        grant=grant,
                        assistant_text=final_reply,
                    )
                    if (
                        playback_ack is not None
                        and final_playback_ack is None
                    ):
                        raise MemoryDeletionJournalIntegrityError()
                    playback_ack = (
                        self._remember_conversation_playback_ack(
                            final_playback_ack
                        )
                    )
                    if final_reply:
                        self._mark_reply_final_once()
                    continue
                if event_type == "error":
                    raise RuntimeError(clean_text(event.get("error")) or "chat_stream_failed")
        if memory_handoff is None or not done_seen:
            raise MemoryDeletionJournalIntegrityError()
        if memory_handoff.state == "bound":
            if memory_handoff.position is None:
                raise MemoryDeletionJournalIntegrityError()
            with memory_exposure_guard(
                expected_position=memory_handoff.position,
                required=True,
                index_dir=Path(MEMORY_ROOT) / "memory_index",
            ):
                try:
                    for sentence in buffered_sentences:
                        await speak_sentence(sentence)
                except asyncio.CancelledError:
                    await self._report_conversation_delivery(
                        playback_ack,
                        outcome="cancelled",
                    )
                    raise
                except Exception:
                    await self._report_conversation_delivery(
                        playback_ack,
                        outcome=self._playback_outcome(completed=False),
                    )
                    raise
                await report_stream_outcome()
        else:
            await report_stream_outcome()
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        chat_ms = max(0.0, elapsed_ms - tts_ms)
        print(
            "[LOCAL BRIDGE] stream_reply "
            f"sentence_count={sentence_count} "
            f"first_sentence_ms={round(first_sentence_ms, 1) if first_sentence_ms is not None else None} "
            f"chat_ms={round(chat_ms, 1)} "
            f"tts_ms={round(tts_ms, 1)}",
            flush=True,
        )
        return {
            "reply": final_reply,
            "sentenceCount": sentence_count,
            "firstSentenceMs": round(first_sentence_ms, 1) if first_sentence_ms is not None else None,
            "chatMs": chat_ms,
            "ttsMs": tts_ms,
        }

    async def _speak(self, text: str) -> None:
        if sd is None:
            self.last_error = "sounddevice import failed"
            return
        tts_text = clean_tts_text(text)
        if not tts_text:
            return
        await self._speak_with_payload(self._build_tts_payload(tts_text))

    def _build_tts_payload(self, text: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": OMNIVOICE_MODEL,
            "input": text,
            "voice": OMNIVOICE_VOICE if OMNIVOICE_VOICE else "auto",
            "response_format": "pcm",
            "stream": True,
            "num_step": OMNIVOICE_NUM_STEP,
            "stream_strategy": OMNIVOICE_STREAM_STRATEGY,
            "stream_block_size": OMNIVOICE_STREAM_BLOCK_SIZE,
            "stream_first_block_steps": OMNIVOICE_STREAM_FIRST_BLOCK_STEPS,
            "stream_block_steps": OMNIVOICE_STREAM_BLOCK_STEPS,
            "stream_first_immediate_cap_ms": OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS,
            "stream_lookahead_crossfade_ms": OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS,
        }
        if OMNIVOICE_LANGUAGE:
            payload["language"] = OMNIVOICE_LANGUAGE
        if OMNIVOICE_SPEED > 0 and abs(OMNIVOICE_SPEED - 1.0) > 0.001:
            payload["speed"] = OMNIVOICE_SPEED
        return payload

    def _output_devices_snapshot(self) -> list[dict[str, Any]]:
        if sd is None:
            return []
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            default_output = None
            try:
                default_device = sd.default.device
                if isinstance(default_device, (list, tuple)) and len(default_device) >= 2:
                    default_output = int(default_device[1])
                elif isinstance(default_device, int):
                    default_output = int(default_device)
            except Exception:
                default_output = None
        except Exception as exc:
            self.runtime_errors.record("output_device_probe_failed", exc)
            self.last_error = "output_device_probe_failed"
            return []

        output_devices: list[dict[str, Any]] = []
        for index, info in enumerate(devices):
            try:
                if int(info.get("max_output_channels") or 0) <= 0:
                    continue
                hostapi_index = int(info.get("hostapi") or 0)
                hostapi_name = clean_text((hostapis[hostapi_index] or {}).get("name")) if 0 <= hostapi_index < len(hostapis) else ""
            except Exception:
                continue
            if hostapi_name.strip().upper() == "MME":
                continue
            name = clean_text(info.get("name"))
            if not name:
                continue
            output_devices.append(
                {
                    "id": str(index),
                    "name": name,
                    "api": hostapi_name or "Windows",
                    "label": f"{name} · {hostapi_name}" if hostapi_name else name,
                    "channels": int(info.get("max_output_channels") or 0),
                    "sampleRate": int(float(info.get("default_samplerate") or 0)),
                    "default": default_output == index,
                }
            )
        return self._dedupe_output_devices(output_devices)

    @staticmethod
    def _output_device_family_key(name: str) -> str:
        normalized = re.sub(r"\s+", " ", name.lower()).strip()
        parenthetical = re.findall(r"\(([^)]+)\)", normalized)
        if parenthetical:
            normalized = parenthetical[-1]
        normalized = re.sub(r"^\s*\d+\s*-\s*", "", normalized)
        normalized = re.sub(r"^(speakers?|headphones?|스피커|헤드폰)\s*", "", normalized)
        normalized = re.sub(r"[^a-z0-9가-힣]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip() or name.lower()

    @staticmethod
    def _output_device_api_rank(api: str) -> int:
        normalized = api.strip().lower()
        if "wasapi" in normalized:
            return 0
        if "directsound" in normalized:
            return 1
        if "wdm-ks" in normalized:
            return 2
        return 3

    def _dedupe_output_devices(self, devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_family: dict[str, dict[str, Any]] = {}
        for device in devices:
            key = self._output_device_family_key(str(device.get("name") or ""))
            current = by_family.get(key)
            if current is None:
                by_family[key] = device
                continue
            current_rank = self._output_device_api_rank(str(current.get("api") or ""))
            device_rank = self._output_device_api_rank(str(device.get("api") or ""))
            if device_rank < current_rank or (device_rank == current_rank and device.get("default") and not current.get("default")):
                by_family[key] = device
        return sorted(by_family.values(), key=lambda item: (0 if item.get("default") else 1, str(item.get("name") or "")))

    def _current_output_device_id(self) -> str:
        return str(self.output_device if self.output_device is not None else "default")

    def _refresh_output_readiness(self) -> None:
        """Validate the selected output format without opening or writing a stream."""

        if sd is None or not callable(getattr(sd, "check_output_settings", None)):
            self.output_ready = False
            self.output_error_code = LOCAL_OUTPUT_BACKEND_UNAVAILABLE
            return
        try:
            device_info = sd.query_devices(self.output_device, "output")
        except Exception:
            self.output_ready = False
            self.output_error_code = LOCAL_OUTPUT_DEVICE_UNAVAILABLE
            return
        try:
            max_channels = int(device_info.get("max_output_channels") or 0)
        except (AttributeError, TypeError, ValueError):
            self.output_ready = False
            self.output_error_code = LOCAL_OUTPUT_DEVICE_UNAVAILABLE
            return
        if max_channels < TTS_PCM_CHANNELS:
            self.output_ready = False
            self.output_error_code = LOCAL_OUTPUT_FORMAT_UNSUPPORTED
            return
        try:
            sd.check_output_settings(
                device=self.output_device,
                channels=TTS_PCM_CHANNELS,
                dtype=TTS_PCM_DTYPE,
                samplerate=TTS_PCM_RATE,
            )
        except Exception:
            self.output_ready = False
            self.output_error_code = LOCAL_OUTPUT_FORMAT_UNSUPPORTED
            return
        self.output_ready = True
        self.output_error_code = ""

    def _ensure_tts_warmup(self) -> None:
        if not LOCAL_BRIDGE_TTS_ENABLED or not LOCAL_BRIDGE_TTS_WARMUP_ENABLED:
            return
        if self.tts_warmup_task is not None and not self.tts_warmup_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.tts_warmup_task = loop.create_task(self._warmup_tts_after_delay())

    async def _warmup_tts_after_delay(self) -> None:
        await asyncio.sleep(LOCAL_BRIDGE_TTS_WARMUP_DELAY_SEC)
        text = clean_tts_text(LOCAL_BRIDGE_TTS_WARMUP_TEXT)
        if not text or self.session is None:
            return
        started = time.perf_counter()
        last_error = ""
        last_error_type = ""
        for attempt in range(1, LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS + 1):
            try:
                audio_bytes = await self._drain_tts_payload(self._build_tts_payload(text))
                if audio_bytes <= 0:
                    raise RuntimeError("tts_warmup_empty_audio")
                self.tts_warmup_ms = round((time.perf_counter() - started) * 1000.0, 1)
                self.tts_warmup_done = True
                self.tts_warmup_error = ""
                print(f"[LOCAL BRIDGE] tts_warmup_done attempt={attempt} bytes={audio_bytes} ms={self.tts_warmup_ms}", flush=True)
                await self._post_status()
                return
            except Exception as exc:
                self.runtime_errors.record("tts_warmup_attempt_failed", exc)
                last_error = "tts_warmup_failed"
                last_error_type = type(exc).__name__
                if attempt >= LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS:
                    break
                print(
                    "[LOCAL BRIDGE] tts_warmup_retry "
                    f"attempt={attempt} errorCode={last_error} "
                    f"errorType={last_error_type}",
                    flush=True,
                )
                await asyncio.sleep(LOCAL_BRIDGE_TTS_WARMUP_RETRY_DELAY_SEC)
        self.tts_warmup_error = last_error
        print(
            "[LOCAL BRIDGE] tts_warmup_failed "
            f"errorCode={last_error} errorType={last_error_type}",
            flush=True,
        )
        await self._post_status()

    async def _drain_tts_payload(self, payload: dict[str, Any]) -> int:
        assert self.session is not None
        async with self.session.post(
            f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError("tts_warmup_failed")
            audio_bytes = 0
            async for chunk in resp.content.iter_chunked(4096):
                audio_bytes += len(chunk)
            return audio_bytes

    async def _speak_with_payload(self, payload: dict[str, Any]) -> None:
        assert self.session is not None
        playback_owner = self._claim_playback_owner()
        playback_owner_token = self.playback_controller.owner_token
        if playback_owner_token is None:
            self._release_playback_owner(playback_owner)
            raise RuntimeError("playback_owner_token_missing")
        self.speaking = True
        await self._post_status()
        try:
            request_started = time.perf_counter()
            candidate_payloads = [dict(payload)]
            if str(payload.get("voice") or "").startswith("clone:"):
                fallback_payload = dict(payload)
                fallback_payload["voice"] = "auto"
                candidate_payloads.append(fallback_payload)
            active_payload = candidate_payloads[0]
            audio_bytes = 0
            played_bytes = 0
            first_playback_ms: float | None = None
            for index, candidate_payload in enumerate(candidate_payloads):
                active_payload = candidate_payload
                async with self.session.post(
                    f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                    json=candidate_payload,
                    timeout=aiohttp.ClientTimeout(total=180),
                ) as resp:
                    if resp.status != 200 and index + 1 < len(candidate_payloads):
                        continue
                    if resp.status != 200:
                        detail = await resp.text()
                        raise RuntimeError(f"tts_failed {resp.status}: {detail[:300]}")
                    audio_bytes, played_bytes, first_playback_ms = (
                        await self._play_streaming_pcm_response(
                            resp,
                            started_at=request_started,
                            playback_owner=playback_owner,
                            playback_owner_token=playback_owner_token,
                        )
                    )
                    if (
                        audio_bytes <= 0
                        and played_bytes <= 0
                        and first_playback_ms is None
                        and index + 1 < len(candidate_payloads)
                    ):
                        continue
                    break
            if audio_bytes <= 0:
                raise RuntimeError(
                    f"tts_empty_audio voice={active_payload.get('voice') or 'auto'}"
                )
            if played_bytes <= 0:
                raise RuntimeError(
                    "tts_playback_empty "
                    f"voice={active_payload.get('voice') or 'auto'} bytes={audio_bytes}"
                )
            self.play_count += 1
            self.last_error = ""
            self.last_tts_playback = {
                "voice": str(active_payload.get("voice") or "auto"),
                "audioBytes": audio_bytes,
                "playedBytes": played_bytes,
                "firstPlaybackMs": round(first_playback_ms, 1) if first_playback_ms is not None else None,
            }
            print(
                "[LOCAL BRIDGE] tts_played_streaming "
                f"bytes={audio_bytes} played_bytes={played_bytes} "
                f"first_playback_ms={self.last_tts_playback['firstPlaybackMs']}",
                flush=True,
            )
        finally:
            self._release_playback_owner(playback_owner)
            self.speaking = False
            self.mic_input_suppressed_until = time.monotonic() + LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC
            await self._post_status()

    async def _play_streaming_pcm_response(
        self,
        resp: aiohttp.ClientResponse,
        *,
        started_at: float,
        playback_owner: str,
        playback_owner_token: object,
    ) -> tuple[int, int, float | None]:
        if sd is None:
            return 0, 0, None
        audio_bytes = 0
        played_bytes = 0
        first_playback_ms: float | None = None
        remainder = b""
        with sd.RawOutputStream(
            samplerate=TTS_PCM_RATE,
            channels=TTS_PCM_CHANNELS,
            dtype=TTS_PCM_DTYPE,
            device=self.output_device,
        ) as stream:
            async for chunk in resp.content.iter_chunked(4096):
                if not chunk:
                    continue
                audio_bytes += len(chunk)
                data = remainder + chunk
                aligned_len = len(data) - (len(data) % TTS_SAMPLE_WIDTH_BYTES)
                if aligned_len > 0:
                    playable = data[:aligned_len]
                    await self._write_delta_stream_chunk(stream, playable)
                    if first_playback_ms is None:
                        first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                        self._mark_playback_started_once(
                            expected_owner_id=playback_owner,
                            expected_owner_token=playback_owner_token,
                        )
                    played_bytes += len(playable)
                remainder = data[aligned_len:]
            if remainder:
                padded = remainder + (b"\x00" * (TTS_SAMPLE_WIDTH_BYTES - len(remainder)))
                await self._write_delta_stream_chunk(stream, padded)
                if first_playback_ms is None:
                    first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                    self._mark_playback_started_once(
                        expected_owner_id=playback_owner,
                        expected_owner_token=playback_owner_token,
                    )
                played_bytes += len(padded)
            if played_bytes > 0:
                await self._write_delta_stream_chunk(
                    stream,
                    b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18),
                )
        return audio_bytes, played_bytes, first_playback_ms

    def _play_pcm(self, chunks: list[bytes]) -> int:
        if not chunks or sd is None:
            return 0
        played_bytes = 0
        playback_owner = self.playback_controller.owner_id
        playback_owner_token = self.playback_controller.owner_token
        with sd.RawOutputStream(
            samplerate=TTS_PCM_RATE,
            channels=TTS_PCM_CHANNELS,
            dtype=TTS_PCM_DTYPE,
            device=self.output_device,
        ) as stream:
            for chunk in iter_pcm_aligned_chunks(chunks):
                self._ensure_validation_attempt_current()
                stream.write(chunk)
                if played_bytes == 0:
                    self._mark_playback_started_once(
                        expected_owner_id=playback_owner,
                        expected_owner_token=playback_owner_token,
                    )
                played_bytes += len(chunk)
            self._ensure_validation_attempt_current()
            stream.write(b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18))
        return played_bytes

    async def _post_status(self, extra: dict[str, Any] | None = None) -> bool:
        async with self.status_lock:
            return await self._post_status_serialized(extra)

    async def _post_status_serialized(
        self,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        await self._enforce_voice_capture_watchdog()
        if self.session is None:
            return False
        self.status_seq += 1
        status_seq = self.status_seq
        self._refresh_output_readiness()
        mic_stats: dict[str, Any] = {"enabled": self.mic_enabled}
        if self.service is not None:
            self.mic_capture_stopped = bool(self.service.capture_stopped)
            if self.mic_enabled and not self.service.capture_ready:
                self.ready = False
                if self.mic_control_state == "applied":
                    self.mic_control_state = "failed"
                    self.mic_control_error = "mic_capture_lost"
                if not self.last_error:
                    self.last_error = str(
                        self.service.last_error or "local_mic_capture_lost"
                    )
            last_input_at = self.service.last_input_at
            suppress_remaining_sec = max(0.0, self.mic_input_suppressed_until - time.monotonic())
            mic_stats = {
                "enabled": True,
                "captureReady": self.service.capture_ready,
                "captureActive": bool(getattr(self.service, "_capture_active", False)),
                "captureStopped": self.service.capture_stopped,
                "inputBlockCount": self.service.input_block_count,
                "lastInputAgeSec": round(time.time() - last_input_at, 2) if last_input_at else None,
                "lastInputLevel": round(float(self.service.last_input_level), 6),
                "maxInputLevel": round(float(self.service.max_input_level), 6),
                "lastInputStatus": self.service.last_input_status,
                "rejectedSegmentCount": self.service.rejected_segment_count,
                "lastRejectedReason": self.service.last_rejected_reason,
                "lastSegmentFilter": self.service.last_segment_filter,
                "startThreshold": self.service.start_threshold,
                "continueThreshold": self.service.continue_threshold,
                "minVoicedMs": self.service.min_voiced_ms,
                "vadFilterEnabled": self.service.vad_filter_enabled,
                "envNoiseFilterEnabled": self.service.env_noise_filter_enabled,
                "waveformFilterEnabled": self.service.waveform_filter_enabled,
                "ttsInputSuppressed": self._mic_input_is_suppressed(),
                "ttsInputSuppressRemainingMs": round(suppress_remaining_sec * 1000.0),
                "suppressedSegmentCount": self.suppressed_mic_segment_count,
                "discardedPendingSegmentCount": self.discarded_pending_mic_segment_count,
            }
        else:
            mic_stats.update(
                {
                    "captureReady": False,
                    "captureActive": False,
                    "captureStopped": self.mic_capture_stopped,
                }
            )
        payload: dict[str, Any] = {
            "schema": "local_io_bridge.status.v1",
            "statusSeq": status_seq,
            "heartbeatAt": time.time(),
            "pid": os.getpid(),
            "bridgeInstanceId": self.bridge_instance_id,
            "enabled": True,
            "ready": self.ready,
            "micEnabled": self.mic_enabled,
            "micControlRevision": self.mic_control_request_revision,
            "micControlActionId": self.mic_control_action_id,
            "micControlPendingRevision": self.mic_control_pending_revision,
            "micControlPendingActionId": self.mic_control_pending_action_id,
            "micControlState": self.mic_control_state,
            "micControlDesiredEnabled": self.mic_control_desired_enabled,
            "micControlError": self.mic_control_error,
            "micCaptureStopped": self.mic_capture_stopped,
            "restartStarted": self.restart_started,
            "shutdownStarted": self.shutdown_started,
            "minecraftCommandRevision": self.minecraft_command_request_revision,
            "minecraftCommandState": self.minecraft_command_state,
            "minecraftCommandError": self.minecraft_command_error,
            "minecraftCommandResult": dict(self.minecraft_command_result),
            "speaking": self.speaking,
            "mode": "windows_io_bridge",
            "segmentCount": self.segment_count,
            "transcriptCount": self.transcript_count,
            "playCount": self.play_count,
            "activePlaybackOwner": self.playback_controller.owner_id or None,
            "playbackCancelRequested": self.playback_controller.cancel_requested,
            "lastError": (
                ""
                if self.last_error.startswith("heartbeat_write_failed:")
                else self.last_error
            ),
            "startedAt": self.started_at,
            "device": LOCAL_MIC_DEVICE or "default",
            "outputDevice": self._current_output_device_id(),
            "outputReady": self.output_ready,
            "outputErrorCode": self.output_error_code,
            "outputFormat": {
                "sampleRate": TTS_PCM_RATE,
                "channels": TTS_PCM_CHANNELS,
                "dtype": TTS_PCM_DTYPE,
            },
            "outputDevices": self._output_devices_snapshot(),
            "streamingTts": LOCAL_BRIDGE_STREAMING_TTS_ENABLED,
            "inputStreamingTts": LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED,
            "botApiBase": BOT_API_BASE,
            "sttUrl": STT_SERVICE_URL,
            "ttsUrl": OMNIVOICE_SERVER_URL,
            "mic": mic_stats,
            "lastLatency": dict(self.last_latency),
            "lastTtsPlayback": dict(self.last_tts_playback),
            "voiceAdmission": self._voice_admission_public_status(),
            "voiceCaptureWatchdog": self._voice_capture_watchdog_status(),
            "voiceCaptureFenceDigest": self.voice_capture_fence_digest,
            "ttsWarmup": {
                "enabled": LOCAL_BRIDGE_TTS_ENABLED and LOCAL_BRIDGE_TTS_WARMUP_ENABLED,
                "done": self.tts_warmup_done,
                "error": self.tts_warmup_error,
                "ms": self.tts_warmup_ms,
            },
            "hostVision": (
                self.host_vision_bridge.snapshot()
                if self.host_vision_bridge is not None
                else {
                    "schema": "host_vision.status.v1",
                    "state": "starting",
                    "captureEnabled": False,
                }
            ),
            "hostUiAction": (
                self.host_ui_action_bridge.snapshot()
                if self.host_ui_action_bridge is not None
                else {
                    "schema": "host_ui_action.status.v1",
                    "state": "starting",
                    "auditReady": False,
                }
            ),
            **self.runtime_errors.snapshot(),
        }
        if extra:
            payload.update(extra)
        payload.pop("conversationDeliveryAck", None)
        payload = sign_voice_capture_artifact(
            payload,
            auth_scope=BRIDGE_STATUS_AUTH_SCOPE,
            auth_token=VOICE_CAPTURE_HOST_AUTH_TOKEN,
        )
        try:
            write_task = asyncio.create_task(
                asyncio.to_thread(
                    atomic_json_write,
                    LOCAL_BRIDGE_STATUS_PATH,
                    payload,
                )
            )
            try:
                await asyncio.shield(write_task)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await write_task
                raise
        except Exception as exc:
            self.runtime_errors.record("heartbeat_write_failed", exc)
            self.last_error = f"heartbeat_write_failed: {type(exc).__name__}"
        else:
            if self.last_error.startswith("heartbeat_write_failed:"):
                self.last_error = ""
        try:
            await asyncio.to_thread(
                emit_silence_liveness_event,
                "local",
                root=LOCAL_BRIDGE_STATUS_PATH.parent.parent,
                heartbeat_at=payload["heartbeatAt"],
                bridge_ready=payload["ready"],
                mic_enabled=payload["micEnabled"],
                capture_ready=mic_stats.get("captureReady"),
            )
        except Exception as exc:
            self.runtime_errors.record("silence_liveness_emit_failed", exc)
        sent_delivery_ack = (
            dict(self.pending_conversation_delivery_acks[0])
            if self.pending_conversation_delivery_acks
            else None
        )
        http_payload = dict(payload)
        if sent_delivery_ack is not None:
            http_payload["conversationDeliveryAck"] = sent_delivery_ack
        try:
            async with self.session.post(
                f"{BOT_API_BASE}/api/local-bridge/status",
                json=http_payload,
                headers={
                    LOCAL_BRIDGE_STATUS_AUTH_HEADER: (
                        LOCAL_BRIDGE_STATUS_AUTH_TOKEN
                    )
                },
                timeout=aiohttp.ClientTimeout(total=2),
                allow_redirects=False,
            ) as resp:
                data = await resp.json(content_type=None)
                acknowledged = data.get("localBridge") if isinstance(data, dict) else None
                if not (
                    resp.status == 200
                    and isinstance(data, dict)
                    and data.get("ok") is True
                    and isinstance(acknowledged, dict)
                    and acknowledged.get("bridgeInstanceDigest")
                    == hashlib.sha256(
                        self.bridge_instance_id.encode("utf-8")
                    ).hexdigest()
                    and type(acknowledged.get("pid")) is int
                    and acknowledged.get("pid") == os.getpid()
                    and type(acknowledged.get("statusSeq")) is int
                    and acknowledged.get("statusSeq") == status_seq
                    and isinstance(acknowledged.get("startedAt"), (int, float))
                    and not isinstance(acknowledged.get("startedAt"), bool)
                    and float(acknowledged["startedAt"]) == self.started_at
                ):
                    return False
                self._consume_conversation_delivery_ack_receipt(
                    data,
                    sent_ack=sent_delivery_ack,
                )
                self._handle_control_response(data)
                return True
        except Exception:
            return False

    def _handle_control_response(self, data: dict[str, Any]) -> None:
        if self.restart_started or self.shutdown_started:
            return
        self._apply_voice_admission_status(data)

        restart = data.get("restart") if isinstance(data, dict) else None
        if isinstance(restart, dict) and restart.get("requested"):
            self._invalidate_local_voice_admission("restart_requested")
            self.restart_started = True
            self.last_error = "restart_requested"
            self._start_restart_script()
            self._schedule_bridge_exit()
            return

        shutdown = data.get("shutdown") if isinstance(data, dict) else None
        if isinstance(shutdown, dict) and shutdown.get("requested"):
            self._invalidate_local_voice_admission("shutdown_requested")
            self.shutdown_started = True
            self.last_error = "shutdown_requested"
            self._start_shutdown_script()
            self._schedule_bridge_exit()
            return

        self._handle_mic_control_request(data)
        self._handle_output_device_request(data)
        self._handle_minecraft_command_request(data)

        raw_speech_generation = (
            data.get("speakGeneration") if isinstance(data, dict) else None
        )
        speech_generation = (
            raw_speech_generation
            if isinstance(raw_speech_generation, int)
            and not isinstance(raw_speech_generation, bool)
            and raw_speech_generation >= 0
            else None
        )
        if (
            speech_generation is not None
            and speech_generation > self.control_speech_generation
        ):
            self.control_speech_generation = speech_generation
            while True:
                try:
                    self.speak_request_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    self.speak_request_queue.task_done()
            if (
                self.speak_worker_task is not None
                and not self.speak_worker_task.done()
                and self.active_control_speech_generation
                < speech_generation
            ):
                self.speak_worker_task.cancel()

        speak_requests = data.get("speakRequests") if isinstance(data, dict) else None
        if isinstance(speak_requests, list):
            for request in speak_requests:
                if not isinstance(request, dict):
                    continue
                request_generation = request.get("speechGeneration")
                request_turn_id = clean_text(request.get("speechTurnId"))
                prefix_index = request.get("prefixIndex")
                if (
                    speech_generation is None
                    or request_generation != speech_generation
                    or request_generation != self.control_speech_generation
                    or not request_turn_id
                    or not isinstance(prefix_index, int)
                    or isinstance(prefix_index, bool)
                    or prefix_index < 0
                ):
                    continue
                text = clean_text(request.get("text"))
                if not text:
                    continue
                try:
                    self.speak_request_queue.put_nowait({**request, "text": text})
                except asyncio.QueueFull:
                    with contextlib.suppress(Exception):
                        self.speak_request_queue.get_nowait()
                        self.speak_request_queue.task_done()
                    with contextlib.suppress(Exception):
                        self.speak_request_queue.put_nowait({**request, "text": text})
            self._ensure_speak_worker()

    def _handle_output_device_request(self, data: dict[str, Any]) -> None:
        request = data.get("outputDeviceRequest") if isinstance(data, dict) else None
        if not isinstance(request, dict):
            return
        try:
            revision = int(request.get("revision") or 0)
        except Exception:
            revision = 0
        if revision <= self.output_device_request_revision:
            return
        output_device = clean_text(request.get("outputDevice")) or "default"
        self.output_device = normalize_output_device(output_device)
        self.output_device_request_revision = revision
        self._refresh_output_readiness()
        print(
            "[LOCAL BRIDGE] output_device_selected "
            f"device={self.output_device if self.output_device is not None else 'default'} "
            f"revision={revision}",
            flush=True,
        )

    def _ensure_speak_worker(self) -> None:
        if self.speak_worker_task is not None and not self.speak_worker_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._speak_request_worker())
        self.speak_worker_task = task
        task.add_done_callback(self._on_speak_worker_done)

    def _on_speak_worker_done(self, task: asyncio.Task) -> None:
        if self.speak_worker_task is task:
            self.speak_worker_task = None
        if (
            not self.restart_started
            and not self.shutdown_started
            and not self.speak_request_queue.empty()
        ):
            self._ensure_speak_worker()

    async def _speak_request_worker(self) -> None:
        while True:
            try:
                request = self.speak_request_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                text = clean_text(request.get("text"))
                request_generation = request.get("speechGeneration")
                if (
                    text
                    and LOCAL_BRIDGE_TTS_ENABLED
                    and request_generation
                    == self.control_speech_generation
                ):
                    self.active_control_speech_generation = int(
                        request_generation
                    )
                    while self.active_turn_task is not None and not self.active_turn_task.done():
                        await asyncio.sleep(0.05)
                    if (
                        request_generation
                        != self.control_speech_generation
                    ):
                        continue
                    started = time.perf_counter()
                    raw_boundary = request.get("memoryBoundary")
                    if raw_boundary is None:
                        await self._speak(text)
                    else:
                        position = memory_exposure_position_from_dict(
                            raw_boundary
                        )
                        with memory_exposure_guard(
                            expected_position=position,
                            required=True,
                            index_dir=(
                                Path(MEMORY_ROOT) / "memory_index"
                            ),
                        ):
                            await self._speak(text)
                    tts_playback = dict(self.last_tts_playback)
                    self.last_latency = {
                        **dict(self.last_latency),
                        "controlTtsMs": round((time.perf_counter() - started) * 1000.0, 1),
                        "controlTtsFirstPlaybackMs": tts_playback.get("firstPlaybackMs"),
                    }
                    await self._post_status()
            except MemoryDeletionJournalIntegrityError:
                print(
                    "[LOCAL BRIDGE] "
                    "control_tts_memory_boundary_stale",
                    flush=True,
                )
                await self._post_status()
            except Exception as exc:
                self.runtime_errors.record("control_tts_failed", exc)
                self.last_error = "control_tts_failed"
                print(
                    "[LOCAL BRIDGE] control_tts_failed "
                    f"errorType={type(exc).__name__}",
                    flush=True,
                )
                await self._post_status()
            finally:
                if (
                    self.active_control_speech_generation
                    == request.get("speechGeneration")
                ):
                    self.active_control_speech_generation = 0
                self.speak_request_queue.task_done()

    def _schedule_bridge_exit(self) -> None:
        if not LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            os._exit(
                LOCAL_BRIDGE_RESTART_EXIT_CODE if self.restart_started else 0
            )
        loop.create_task(self._exit_after_shutdown_delay())

    @staticmethod
    def _schedule_watchdog_fail_safe_exit() -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            os._exit(VOICE_CAPTURE_FAIL_SAFE_EXIT_CODE)
        loop.call_soon(os._exit, VOICE_CAPTURE_FAIL_SAFE_EXIT_CODE)

    async def _exit_after_shutdown_delay(self) -> None:
        await asyncio.sleep(LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC)
        if self.service is not None:
            try:
                await asyncio.to_thread(self.service.stop)
            except Exception:
                pass
        exit_code = LOCAL_BRIDGE_RESTART_EXIT_CODE if self.restart_started else 0
        print(
            f"[LOCAL BRIDGE] exiting after lifecycle request code={exit_code}",
            flush=True,
        )
        os._exit(exit_code)

    def _start_shutdown_script(self) -> None:
        if not STOP_SCRIPT.exists():
            self.last_error = f"shutdown helper not found: {STOP_SCRIPT}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)
            return
        try:
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(STOP_SCRIPT),
                    "-DelayMs",
                    "200",
                ],
                cwd=str(PROJECT_ROOT),
                env=self._credential_scoped_child_environment(),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            print(f"[LOCAL BRIDGE] shutdown script started: {STOP_SCRIPT}", flush=True)
        except Exception as exc:
            self.runtime_errors.record("shutdown_start_failed", exc)
            self.last_error = f"shutdown start failed: {exc!r}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)

    def _start_restart_script(self) -> None:
        print("[LOCAL BRIDGE] restart delegated to Host Supervisor", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Evelyn Windows local microphone/speaker bridge against Docker core.")
    parser.add_argument("--project-root", default="", help="Project root marker used by launchers to detect an existing bridge process.")
    return parser.parse_args()


def main() -> int:
    parse_args()
    lock_deps = build_instance_lock_runtime_deps(
        LOCAL_BRIDGE_INSTANCE_LOCK_PATH
    )
    if lock_deps.msvcrt_module is None and lock_deps.fcntl_module is None:
        print(
            "[LOCAL BRIDGE] local_bridge_instance_lock_backend_unavailable",
            flush=True,
        )
        return 73
    instance_lock = InstanceLockManager(lock_deps)
    try:
        instance_lock.acquire(wait_sec=0.0)
    except RuntimeError:
        print(
            "[LOCAL BRIDGE] local_bridge_instance_lock_held",
            flush=True,
        )
        return 73
    try:
        asyncio.run(LocalIoBridge().run())
    finally:
        instance_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
