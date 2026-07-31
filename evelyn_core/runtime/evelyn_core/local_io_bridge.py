from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
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
    LOCAL_MIC_ENABLED,
    LOCAL_MIC_MAX_SEGMENT_SEC,
    LOCAL_MIC_MIN_VOICED_MS,
    LOCAL_MIC_PREROLL_MS,
    LOCAL_MIC_QUEUE_MAX,
    LOCAL_MIC_SAMPLE_RATE,
    LOCAL_MIC_START_CONSECUTIVE,
    LOCAL_MIC_START_THRESHOLD,
    LOCAL_MIC_VAD_FILTER_ENABLED,
    LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
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
from .fast_action_runtime import detect_minecraft_runtime_command
from .host_vision_bridge import HostVisionBridge
from .host_ui_action_bridge import HostUiActionBridge
from .local_mic import LocalMicCaptureService
from .local_tts_playback import normalize_output_device
from .local_bridge_barge_in import (
    SingleOwnerPlaybackController,
    evaluate_local_barge_in,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .runtime_error_observability import RuntimeErrorCounter
from .text import clean_text, clean_tts_text, should_suppress_tts_for_command
from .voice_validation import (
    active_validation_context,
    emit_transcript_validation_event,
    emit_voice_validation_event,
)

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - host audio dependency
    sd = None


BOT_API_BASE = os.getenv("LOCAL_BRIDGE_BOT_API_BASE", "http://127.0.0.1:8798").rstrip("/")
STT_SERVICE_URL = os.getenv("STT_SERVICE_URL", "http://127.0.0.1:8892").rstrip("/")
OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880").rstrip("/")
LOCAL_TTS_OUTPUT_DEVICE = os.getenv("LOCAL_TTS_OUTPUT_DEVICE") or os.getenv("LOCAL_AUDIO_OUTPUT_DEVICE")
LOCAL_BRIDGE_TTS_ENABLED = os.getenv("LOCAL_BRIDGE_TTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_STREAMING_TTS_ENABLED = os.getenv("LOCAL_BRIDGE_STREAMING_TTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED = os.getenv(
    "LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_MIN_TEXT_CHARS = max(1, int(os.getenv("LOCAL_BRIDGE_MIN_TEXT_CHARS", "2")))
LOCAL_BRIDGE_STATUS_INTERVAL_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_STATUS_INTERVAL_SEC", "0.25")))
LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN = os.getenv("LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN", "true").strip().lower() in {"1", "true", "yes", "on"}
LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC = max(0.2, float(os.getenv("LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC", "1.5")))
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
TTS_SAMPLE_WIDTH_BYTES = 2
PROJECT_ROOT = Path(os.getenv("EVELYN_PROJECT_ROOT") or Path(__file__).resolve().parents[3])
STOP_SCRIPT = PROJECT_ROOT / "evelyn_core" / "runtime" / "launchers" / "stop_evelyn_local.ps1"
START_LOCAL_BAT = PROJECT_ROOT / "evelyn_core" / "start_local.bat"
START_VOYAGER_BAT = PROJECT_ROOT / "evelyn_core" / "start_voyager.bat"
MINECRAFT_SERVICE_BASE = os.getenv(
    "LOCAL_BRIDGE_MINECRAFT_SERVICE_BASE",
    "http://127.0.0.1:8765",
).rstrip("/")
MINECRAFT_MODEL_HEALTH_URL = os.getenv(
    "LOCAL_BRIDGE_MINECRAFT_MODEL_HEALTH_URL",
    "http://127.0.0.1:9823/health",
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


class LocalIoBridge:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=8)
        self.priority_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=4)
        self.barge_in_queue: asyncio.Queue[tuple[bytes, dict[str, Any]]] = asyncio.Queue(maxsize=4)
        self.session: aiohttp.ClientSession | None = None
        self.service: LocalMicCaptureService | None = None
        self.mic_enabled = bool(LOCAL_MIC_ENABLED)
        self.mic_control_request_revision = 0
        self.mic_control_pending_revision = 0
        self.mic_control_lock = asyncio.Lock()
        self.mic_control_tasks: set[asyncio.Task[Any]] = set()
        self.ready = False
        self.speaking = False
        self.mic_input_suppressed_until = 0.0
        self.suppressed_mic_segment_count = 0
        self.discarded_pending_mic_segment_count = 0
        self.segment_count = 0
        self.transcript_count = 0
        self.play_count = 0
        self.last_error = ""
        self.runtime_errors = RuntimeErrorCounter()
        self.last_latency: dict[str, Any] = {}
        self.last_tts_playback: dict[str, Any] = {}
        self.started_at = time.time()
        self.output_device = normalize_output_device(LOCAL_TTS_OUTPUT_DEVICE)
        self.shutdown_started = False
        self.restart_started = False
        self.speak_request_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self.speak_worker_task: asyncio.Task | None = None
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
        self.active_validation: dict[str, str] | None = None
        self.playback_started_for_turn = False
        self.playback_cancelled_for_turn = False
        self.reply_final_for_turn = False
        self.playback_controller = SingleOwnerPlaybackController()
        self._speaker_verifier: Any | None = None
        self._speaker_verifier_initialized = False

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

    async def _start_mic(self) -> None:
        if not self.mic_enabled:
            self.service = None
            self.ready = True
            self.last_error = ""
            print("[LOCAL BRIDGE] mic_disabled=true", flush=True)
            return
        if self.service is not None and self.service.capture_ready:
            self.ready = True
            self.last_error = ""
            return

        loop = asyncio.get_running_loop()

        def on_segment(pcm_bytes: bytes, meta: dict[str, Any]) -> None:
            def enqueue() -> None:
                segment_meta = dict(meta)
                segment_meta.setdefault("turnId", uuid.uuid4().hex)
                if self.speaking:
                    validation = active_validation_context(
                        surface="local",
                        prefer_interrupt=True,
                    )
                    if validation:
                        segment_meta["validationSessionId"] = validation["sessionId"]
                        segment_meta["validationStepId"] = validation["stepId"]
                    if self.barge_in_queue.full():
                        with contextlib.suppress(Exception):
                            self.barge_in_queue.get_nowait()
                            self.barge_in_queue.task_done()
                    self.barge_in_queue.put_nowait((pcm_bytes, segment_meta))
                    return
                if self._mic_input_is_suppressed():
                    self.suppressed_mic_segment_count += 1
                    return
                validation = active_validation_context(surface="local")
                if validation:
                    segment_meta["validationSessionId"] = validation["sessionId"]
                    segment_meta["validationStepId"] = validation["stepId"]
                if self.queue.full():
                    try:
                        self.queue.get_nowait()
                        self.queue.task_done()
                    except Exception:
                        pass
                self.queue.put_nowait((pcm_bytes, segment_meta))

            loop.call_soon_threadsafe(enqueue)

        self.service = LocalMicCaptureService(
            on_segment=on_segment,
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
        started = await asyncio.to_thread(self.service.start)
        self.ready = bool(started and self.service.capture_ready)
        self.last_error = "" if self.ready else (self.service.last_error or "local_mic_not_ready")
        print(f"[LOCAL BRIDGE] mic_ready={self.ready} device={LOCAL_MIC_DEVICE or 'default'} error={self.last_error or 'none'}", flush=True)

    async def _stop_mic(self) -> None:
        self.mic_enabled = False
        service = self.service
        self.service = None
        self._discard_pending_mic_segments()
        if service is not None:
            await asyncio.to_thread(service.stop)
        self.ready = True
        self.last_error = ""
        print("[LOCAL BRIDGE] mic_ready=false mic_disabled=true", flush=True)

    async def _apply_mic_control_request(self, *, revision: int, enabled: bool) -> None:
        async with self.mic_control_lock:
            if revision <= self.mic_control_request_revision:
                return
            try:
                if enabled:
                    self.mic_enabled = True
                    await self._start_mic()
                else:
                    await self._stop_mic()
            except Exception as exc:
                self.ready = False if enabled else True
                self.last_error = f"mic_control_failed: {exc!r}"
            finally:
                self.mic_control_request_revision = revision
            print(
                "[LOCAL BRIDGE] mic_control_applied "
                f"enabled={self.mic_enabled} ready={self.ready} revision={revision} "
                f"error={self.last_error or 'none'}",
                flush=True,
            )

    def _handle_mic_control_request(self, data: dict[str, Any]) -> None:
        request = data.get("micControlRequest") if isinstance(data, dict) else None
        if not isinstance(request, dict) or not isinstance(request.get("enabled"), bool):
            return
        try:
            revision = int(request.get("revision") or 0)
        except (TypeError, ValueError):
            return
        if revision <= max(self.mic_control_request_revision, self.mic_control_pending_revision):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.mic_control_pending_revision = revision
        task = loop.create_task(
            self._apply_mic_control_request(revision=revision, enabled=bool(request["enabled"])),
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
        env = os.environ.copy()
        env.setdefault("DISCORD_BOT_TOKEN", "local-only-disabled")
        secret_home = (
            get_runtime_artifacts_root()
            / "secrets"
            / "codex_device_home"
        )
        if (secret_home / "auth.json").is_file():
            env.setdefault("EVELYN_CODEX_CREDENTIALS_DIR", str(secret_home))
        return env

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
            try:
                raise RuntimeError(
                    "minecraft_world_authorization_required"
                )
            except Exception as exc:
                self.runtime_errors.record("minecraft_lazy_start_failed", exc)
                self.minecraft_command_state = "failed"
                self.minecraft_command_error = clean_text(repr(exc)) or "minecraft_lazy_start_failed"
                self.minecraft_command_result = {
                    "command": clean_text(command),
                    "action": action,
                    "commandApplied": False,
                    "connected": False,
                }
            finally:
                self.minecraft_command_pending_revision = max(
                    self.minecraft_command_pending_revision,
                    revision,
                )
                await self._post_status()
            print(
                "[LOCAL BRIDGE] minecraft_command_applied "
                f"revision={revision} state={self.minecraft_command_state} "
                f"connected={bool(self.minecraft_command_result.get('connected'))} "
                f"error={self.minecraft_command_error or 'none'}",
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
    ) -> dict[str, str] | None:
        source = dict(meta or {})
        session_id = str(source.get("validationSessionId") or "")
        step_id = str(source.get("validationStepId") or "")
        if session_id and step_id:
            return {"sessionId": session_id, "stepId": step_id, "surface": "local"}
        return self.active_validation

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
            turnId=str((meta or {}).get("turnId") or self.active_turn_id or ""),
            **payload,
        )

    def _mark_playback_started_once(self) -> None:
        if self.playback_started_for_turn:
            return
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
        if not self.playback_controller.claim(owner_id, cancel):
            raise RuntimeError("active_playback_owner_conflict")
        return owner_id

    def _mark_reply_final_once(self) -> None:
        if self.reply_final_for_turn:
            return
        self.reply_final_for_turn = True
        self._emit_validation("reply_final")

    def _speaker_verifier_for_barge_in(self) -> Any | None:
        if self._speaker_verifier_initialized:
            return self._speaker_verifier
        self._speaker_verifier_initialized = True
        apply_to = str(SPEAKER_VERIFICATION_APPLY_TO or "").lower()
        applies = apply_to in {"1", "true", "on", "all", "always", "local", "local_mic"}
        if not SPEAKER_VERIFICATION_ENABLED or not applies:
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
            try:
                verification = await self._verify_barge_in_speaker(pcm_bytes)
                decision = evaluate_local_barge_in(
                    meta,
                    body_rms_min=VOICE_WAVEFORM_BODY_RMS_MIN,
                    speaker_verification=verification,
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
                original_context = self.active_validation
                original_turn_id = self.active_turn_id
                self._mark_reply_final_once()
                controller_cancelled = self.playback_controller.request_cancel()
                if (
                    not controller_cancelled
                    and self.active_turn_task is not None
                    and not self.active_turn_task.done()
                ):
                    self.active_turn_task.cancel()
                if not self.playback_cancelled_for_turn:
                    self.playback_cancelled_for_turn = True
                    if original_context:
                        emit_voice_validation_event(
                            "local",
                            "playback_cancelled",
                            session_id=original_context.get("sessionId"),
                            step_id=original_context.get("stepId"),
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
                        self.priority_queue.get_nowait()
                        self.priority_queue.task_done()
                self.priority_queue.put_nowait((pcm_bytes, meta))
            finally:
                self.barge_in_queue.task_done()

    def _discard_pending_mic_segments(self) -> int:
        discarded = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.queue.task_done()
            discarded += 1
        self.discarded_pending_mic_segment_count += discarded
        return discarded

    async def _handle_segment(self, pcm_bytes: bytes, meta: dict[str, Any]) -> None:
        turn_started = time.perf_counter()
        self.active_turn_started_at = turn_started
        self.active_turn_id = str(meta.get("turnId") or uuid.uuid4().hex)
        meta["turnId"] = self.active_turn_id
        self.active_validation = self._validation_context_from_meta(meta)
        self.playback_started_for_turn = False
        self.playback_cancelled_for_turn = False
        self.reply_final_for_turn = False
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
        await self._post_status(extra={"lastSegmentMeta": meta})
        try:
            stage_started = time.perf_counter()
            text = await self._transcribe(pcm_bytes)
            stt_ms = (time.perf_counter() - stage_started) * 1000.0
            if len(text) < LOCAL_BRIDGE_MIN_TEXT_CHARS:
                return
            context = self._validation_context_from_meta(meta)
            if context:
                emit_transcript_validation_event(
                    "local",
                    text,
                    session_id=context.get("sessionId"),
                    step_id=context.get("stepId"),
                    turnId=self.active_turn_id,
                )
            self.transcript_count += 1
            self._emit_validation("turn_accepted", meta=meta)
            print(f"[LOCAL BRIDGE] transcript={text!r}", flush=True)
            if should_suppress_tts_for_command(text):
                stage_started = time.perf_counter()
                reply = await self._chat(text)
                chat_ms = (time.perf_counter() - stage_started) * 1000.0
                tts_ms = 0.0
            elif LOCAL_BRIDGE_STREAMING_TTS_ENABLED and LOCAL_BRIDGE_TTS_ENABLED:
                try:
                    stream_result = await self._chat_stream_and_speak(text)
                    reply = clean_text(stream_result.get("reply"))
                    chat_ms = stream_result.get("chatMs")
                    tts_ms = stream_result.get("ttsMs")
                except Exception as stream_exc:
                    self.runtime_errors.record("chat_stream_failed", stream_exc)
                    print(f"[LOCAL BRIDGE] chat_stream_failed fallback_to_full err={stream_exc!r}", flush=True)
                    stage_started = time.perf_counter()
                    reply = await self._chat(text)
                    chat_ms = (time.perf_counter() - stage_started) * 1000.0
                    if reply:
                        stage_started = time.perf_counter()
                        await self._speak(reply)
                        tts_ms = (time.perf_counter() - stage_started) * 1000.0
            else:
                stage_started = time.perf_counter()
                reply = await self._chat(text)
                chat_ms = (time.perf_counter() - stage_started) * 1000.0
                if reply and LOCAL_BRIDGE_TTS_ENABLED:
                    stage_started = time.perf_counter()
                    await self._speak(reply)
                    tts_ms = (time.perf_counter() - stage_started) * 1000.0
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
            print(f"[LOCAL BRIDGE] reply={reply!r}", flush=True)
        except asyncio.CancelledError:
            if self.playback_started_for_turn and not self.playback_cancelled_for_turn:
                self.playback_cancelled_for_turn = True
                self._emit_validation(
                    "playback_cancelled",
                    meta=meta,
                    reason="turn_cancelled",
                )
            raise
        except Exception as exc:
            self.runtime_errors.record("turn_pipeline_failed", exc)
            self.last_error = repr(exc)
            self._emit_validation(
                "error",
                meta=meta,
                errorCode=type(exc).__name__,
            )
            print(f"[LOCAL BRIDGE] segment_failed err={exc!r}", flush=True)
        finally:
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
            await self._post_status()

    async def _transcribe(self, pcm_bytes: bytes) -> str:
        audio16k = np.asarray(prepare_stt_audio(pcm_bytes), dtype=np.float32)
        payload = {
            "audio_f32_base64": base64.b64encode(audio16k.tobytes()).decode("ascii"),
            "sample_count": int(audio16k.size),
            "sampling_rate": TARGET_RATE,
            "stage": "local_bridge",
            "language": "Korean",
        }
        assert self.session is not None
        async with self.session.post(f"{STT_SERVICE_URL}/v1/stt/transcribe", json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(f"stt_failed {resp.status}: {data}")
            return clean_text(data.get("text"))

    async def _chat(self, text: str) -> str:
        assert self.session is not None
        payload = {"text": text, "source": "local_bridge"}
        async with self.session.post(f"{BOT_API_BASE}/api/control-page/chat", json=payload, timeout=aiohttp.ClientTimeout(total=150)) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or not data.get("ok"):
                raise RuntimeError(f"chat_failed {resp.status}: {data}")
            return clean_text(data.get("reply"))

    async def _chat_stream_and_speak(self, text: str) -> dict[str, Any]:
        if LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED and sd is not None:
            return await self._chat_delta_stream_and_speak(text)
        return await self._chat_sentence_stream_and_speak(text)

    async def _chat_delta_stream_and_speak(self, text: str) -> dict[str, Any]:
        assert self.session is not None
        payload = {"text": text, "source": "local_bridge"}
        started_at = time.perf_counter()
        sentence_count = 0
        first_sentence_ms: float | None = None
        first_delta_ms: float | None = None
        first_progress_ms: float | None = None
        progress_count = 0
        final_reply = ""
        chat_done_ms: float | None = None
        audio_bytes = 0
        played_bytes = 0
        first_playback_ms: float | None = None
        websocket: aiohttp.ClientWebSocketResponse | None = None
        receiver: asyncio.Task[None] | None = None
        playback_owner = self._claim_playback_owner()

        self.speaking = True
        await self._post_status()

        async def receive_tts_audio() -> None:
            nonlocal audio_bytes, played_bytes, first_playback_ms
            remainder = b""
            with sd.RawOutputStream(
                samplerate=TTS_PCM_RATE,
                channels=TTS_PCM_CHANNELS,
                dtype="int16",
                device=self.output_device,
            ) as stream:
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
                            await asyncio.to_thread(stream.write, playable)
                            if first_playback_ms is None:
                                first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                                self._mark_playback_started_once()
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
                    await asyncio.to_thread(stream.write, padded)
                    if first_playback_ms is None:
                        first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                        self._mark_playback_started_once()
                    played_bytes += len(padded)
                if played_bytes > 0:
                    await asyncio.to_thread(
                        stream.write,
                        b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18),
                    )

        try:
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
            receiver = asyncio.create_task(receive_tts_audio(), name="local-voxcpm-stream-receiver")

            saw_delta = False
            async with self.session.post(
                f"{BOT_API_BASE}/api/control-page/chat-stream",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status != 200:
                    detail = await resp.text()
                    raise RuntimeError(f"chat_stream_failed {resp.status}: {detail[:300]}")
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_type = clean_text(event.get("type"))
                    if event_type == "progress":
                        progress_text = clean_tts_text(event.get("text"))
                        if not progress_text:
                            continue
                        progress_count += 1
                        if first_progress_ms is None:
                            first_progress_ms = (time.perf_counter() - started_at) * 1000.0
                        await websocket.send_json({"type": "append", "text": progress_text})
                        await websocket.send_json({"type": "commit"})
                        continue
                    if event_type == "delta":
                        fragment = str(event.get("text") or "")
                        if not fragment:
                            continue
                        saw_delta = True
                        if first_delta_ms is None:
                            first_delta_ms = (time.perf_counter() - started_at) * 1000.0
                        await websocket.send_json({"type": "append", "text": fragment})
                        continue
                    if event_type == "sentence":
                        sentence = clean_tts_text(event.get("text"))
                        if not sentence:
                            continue
                        sentence_count += 1
                        if first_sentence_ms is None:
                            first_sentence_ms = (time.perf_counter() - started_at) * 1000.0
                        if not saw_delta:
                            await websocket.send_json({"type": "append", "text": sentence})
                        continue
                    if event_type == "done":
                        final_reply = clean_text(event.get("reply"))
                        chat_done_ms = (time.perf_counter() - started_at) * 1000.0
                        continue
                    if event_type == "error":
                        raise RuntimeError(clean_text(event.get("error")) or "chat_stream_failed")

            await websocket.send_json({"type": "flush"})
            await receiver
            receiver = None
            if audio_bytes <= 0 or played_bytes <= 0:
                raise RuntimeError(
                    f"voxcpm_stream_empty_audio audio_bytes={audio_bytes} played_bytes={played_bytes}"
                )

            self.play_count += 1
            self.last_error = ""
            self.last_tts_playback = {
                "voice": "clone:evelyn",
                "audioBytes": audio_bytes,
                "playedBytes": played_bytes,
                "firstPlaybackMs": round(first_playback_ms, 1) if first_playback_ms is not None else None,
                "inputStreaming": True,
            }
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
            if receiver is not None and not receiver.done():
                receiver.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receiver
            raise
        finally:
            if websocket is not None and not websocket.closed:
                await websocket.close()
            self.playback_controller.release(playback_owner)
            self.speaking = False
            self.mic_input_suppressed_until = time.monotonic() + LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC
            await self._post_status()

    async def _chat_sentence_stream_and_speak(self, text: str) -> dict[str, Any]:
        assert self.session is not None
        payload = {"text": text, "source": "local_bridge"}
        started_at = time.perf_counter()
        tts_ms = 0.0
        sentence_count = 0
        first_sentence_ms: float | None = None
        final_reply = ""
        async with self.session.post(
            f"{BOT_API_BASE}/api/control-page/chat-stream",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"chat_stream_failed {resp.status}: {detail[:300]}")
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = clean_text(event.get("type"))
                if event_type == "sentence":
                    sentence = clean_text(event.get("text"))
                    if not sentence:
                        continue
                    sentence_count += 1
                    if first_sentence_ms is None:
                        first_sentence_ms = (time.perf_counter() - started_at) * 1000.0
                    speak_started = time.perf_counter()
                    await self._speak(sentence)
                    tts_ms += (time.perf_counter() - speak_started) * 1000.0
                    continue
                if event_type == "done":
                    final_reply = clean_text(event.get("reply"))
                    continue
                if event_type == "error":
                    raise RuntimeError(clean_text(event.get("error")) or "chat_stream_failed")
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
            self.last_error = repr(exc)
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
                last_error = repr(exc)
                if attempt >= LOCAL_BRIDGE_TTS_WARMUP_ATTEMPTS:
                    break
                print(f"[LOCAL BRIDGE] tts_warmup_retry attempt={attempt} err={exc!r}", flush=True)
                await asyncio.sleep(LOCAL_BRIDGE_TTS_WARMUP_RETRY_DELAY_SEC)
        self.tts_warmup_error = last_error
        print(f"[LOCAL BRIDGE] tts_warmup_failed err={last_error}", flush=True)
        await self._post_status()

    async def _drain_tts_payload(self, payload: dict[str, Any]) -> int:
        assert self.session is not None
        async with self.session.post(
            f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=LOCAL_BRIDGE_TTS_WARMUP_TIMEOUT_SEC),
        ) as resp:
            if resp.status != 200:
                detail = await resp.text()
                raise RuntimeError(f"tts_warmup_failed {resp.status}: {detail[:300]}")
            audio_bytes = 0
            async for chunk in resp.content.iter_chunked(4096):
                audio_bytes += len(chunk)
            return audio_bytes

    async def _speak_with_payload(self, payload: dict[str, Any]) -> None:
        assert self.session is not None
        playback_owner = self._claim_playback_owner()
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
                        )
                    )
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
            self.playback_controller.release(playback_owner)
            self.speaking = False
            self.mic_input_suppressed_until = time.monotonic() + LOCAL_BRIDGE_TTS_INPUT_SUPPRESS_AFTER_SEC
            await self._post_status()

    async def _play_streaming_pcm_response(
        self,
        resp: aiohttp.ClientResponse,
        *,
        started_at: float,
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
            dtype="int16",
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
                    stream.write(playable)
                    if first_playback_ms is None:
                        first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                        self._mark_playback_started_once()
                    played_bytes += len(playable)
                remainder = data[aligned_len:]
            if remainder:
                padded = remainder + (b"\x00" * (TTS_SAMPLE_WIDTH_BYTES - len(remainder)))
                stream.write(padded)
                if first_playback_ms is None:
                    first_playback_ms = (time.perf_counter() - started_at) * 1000.0
                    self._mark_playback_started_once()
                played_bytes += len(padded)
            if played_bytes > 0:
                stream.write(b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18))
        return audio_bytes, played_bytes, first_playback_ms

    def _play_pcm(self, chunks: list[bytes]) -> int:
        if not chunks or sd is None:
            return 0
        played_bytes = 0
        with sd.RawOutputStream(
            samplerate=TTS_PCM_RATE,
            channels=TTS_PCM_CHANNELS,
            dtype="int16",
            device=self.output_device,
        ) as stream:
            for chunk in iter_pcm_aligned_chunks(chunks):
                if played_bytes == 0:
                    self._mark_playback_started_once()
                stream.write(chunk)
                played_bytes += len(chunk)
            stream.write(b"\x00" * int(TTS_PCM_RATE * TTS_PCM_CHANNELS * 2 * 0.18))
        return played_bytes

    async def _post_status(self, extra: dict[str, Any] | None = None) -> None:
        if self.session is None:
            return
        mic_stats: dict[str, Any] = {"enabled": self.mic_enabled}
        if self.service is not None:
            last_input_at = self.service.last_input_at
            suppress_remaining_sec = max(0.0, self.mic_input_suppressed_until - time.monotonic())
            mic_stats = {
                "enabled": True,
                "captureReady": self.service.capture_ready,
                "captureActive": bool(getattr(self.service, "_capture_active", False)),
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
        payload: dict[str, Any] = {
            "schema": "local_io_bridge.status.v1",
            "heartbeatAt": time.time(),
            "enabled": True,
            "ready": self.ready,
            "micEnabled": self.mic_enabled,
            "micControlRevision": self.mic_control_request_revision,
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
            "outputDevices": self._output_devices_snapshot(),
            "streamingTts": LOCAL_BRIDGE_STREAMING_TTS_ENABLED,
            "inputStreamingTts": LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED,
            "botApiBase": BOT_API_BASE,
            "sttUrl": STT_SERVICE_URL,
            "ttsUrl": OMNIVOICE_SERVER_URL,
            "mic": mic_stats,
            "lastLatency": dict(self.last_latency),
            "lastTtsPlayback": dict(self.last_tts_playback),
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
        try:
            await asyncio.to_thread(
                atomic_json_write,
                LOCAL_BRIDGE_STATUS_PATH,
                payload,
            )
        except Exception as exc:
            self.runtime_errors.record("heartbeat_write_failed", exc)
            self.last_error = f"heartbeat_write_failed: {type(exc).__name__}"
        else:
            if self.last_error.startswith("heartbeat_write_failed:"):
                self.last_error = ""
        try:
            async with self.session.post(f"{BOT_API_BASE}/api/local-bridge/status", json=payload, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                data = await resp.json(content_type=None)
                self._handle_control_response(data)
        except Exception:
            pass

    def _handle_control_response(self, data: dict[str, Any]) -> None:
        self._handle_mic_control_request(data)
        self._handle_output_device_request(data)
        self._handle_minecraft_command_request(data)

        restart = data.get("restart") if isinstance(data, dict) else None
        if isinstance(restart, dict) and restart.get("requested") and not self.restart_started:
            self.restart_started = True
            self.last_error = "restart_requested"
            self._start_restart_script()
            self._schedule_bridge_exit()
            return

        speak_requests = data.get("speakRequests") if isinstance(data, dict) else None
        if isinstance(speak_requests, list):
            for request in speak_requests:
                if not isinstance(request, dict):
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

        shutdown = data.get("shutdown") if isinstance(data, dict) else None
        if not isinstance(shutdown, dict) or not shutdown.get("requested") or self.shutdown_started:
            return
        self.shutdown_started = True
        self.last_error = "shutdown_requested"
        self._start_shutdown_script()
        self._schedule_bridge_exit()

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
        self.speak_worker_task = loop.create_task(self._speak_request_worker())

    async def _speak_request_worker(self) -> None:
        while True:
            try:
                request = self.speak_request_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                text = clean_text(request.get("text"))
                if text and LOCAL_BRIDGE_TTS_ENABLED:
                    while self.active_turn_task is not None and not self.active_turn_task.done():
                        await asyncio.sleep(0.05)
                    started = time.perf_counter()
                    await self._speak(text)
                    tts_playback = dict(self.last_tts_playback)
                    self.last_latency = {
                        **dict(self.last_latency),
                        "controlTtsMs": round((time.perf_counter() - started) * 1000.0, 1),
                        "controlTtsFirstPlaybackMs": tts_playback.get("firstPlaybackMs"),
                    }
                    await self._post_status()
            except Exception as exc:
                self.runtime_errors.record("control_tts_failed", exc)
                self.last_error = repr(exc)
                print(f"[LOCAL BRIDGE] control_tts_failed err={exc!r}", flush=True)
                await self._post_status()
            finally:
                self.speak_request_queue.task_done()

    def _schedule_bridge_exit(self) -> None:
        if not LOCAL_BRIDGE_EXIT_AFTER_SHUTDOWN:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            os._exit(0)
        loop.create_task(self._exit_after_shutdown_delay())

    async def _exit_after_shutdown_delay(self) -> None:
        await asyncio.sleep(LOCAL_BRIDGE_SHUTDOWN_EXIT_DELAY_SEC)
        if self.service is not None:
            try:
                await asyncio.to_thread(self.service.stop)
            except Exception:
                pass
        print("[LOCAL BRIDGE] exiting after shutdown request", flush=True)
        os._exit(0)

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
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            print(f"[LOCAL BRIDGE] shutdown script started: {STOP_SCRIPT}", flush=True)
        except Exception as exc:
            self.runtime_errors.record("shutdown_start_failed", exc)
            self.last_error = f"shutdown start failed: {exc!r}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)

    def _start_restart_script(self) -> None:
        if not STOP_SCRIPT.exists():
            self.last_error = f"restart stop helper not found: {STOP_SCRIPT}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)
            return
        if not START_LOCAL_BAT.exists():
            self.last_error = f"restart start helper not found: {START_LOCAL_BAT}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)
            return
        try:
            restart_script = (
                "$ErrorActionPreference = 'Continue'; "
                f"& '{STOP_SCRIPT}' -DelayMs 200; "
                "Start-Sleep -Seconds 2; "
                f"& '{START_LOCAL_BAT}' --background"
            )
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    restart_script,
                ],
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            print(f"[LOCAL BRIDGE] restart script started: {START_LOCAL_BAT}", flush=True)
        except Exception as exc:
            self.runtime_errors.record("restart_start_failed", exc)
            self.last_error = f"restart start failed: {exc!r}"
            print(f"[LOCAL BRIDGE] {self.last_error}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Evelyn Windows local microphone/speaker bridge against Docker core.")
    parser.add_argument("--project-root", default="", help="Project root marker used by launchers to detect an existing bridge process.")
    return parser.parse_args()


def main() -> None:
    parse_args()
    asyncio.run(LocalIoBridge().run())


if __name__ == "__main__":
    main()
