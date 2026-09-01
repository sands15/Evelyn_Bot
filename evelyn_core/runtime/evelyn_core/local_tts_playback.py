from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import DISCORD_FRAME_BYTES, DISCORD_PCM_CHANNELS, DISCORD_PCM_RATE, LOCAL_TTS_TAIL_SILENCE_MS
from .observability_metrics import mark_voice_latency_stage
from .text import clean_text
from .voice_validation import validation_attempt_binding_is_current

try:
    import sounddevice as sd
except Exception:
    sd = None


def normalize_output_device(device: str | int | None) -> str | int | None:
    if device is None:
        return None
    if isinstance(device, int):
        return device
    value = clean_text(str(device)).strip()
    if not value or value.lower() in {"default", "auto"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def local_tts_tail_silence_bytes(ms: int | float = LOCAL_TTS_TAIL_SILENCE_MS) -> bytes:
    duration_ms = max(0.0, float(ms))
    if duration_ms <= 0.0:
        return b""
    stereo_bytes_per_second = DISCORD_PCM_RATE * DISCORD_PCM_CHANNELS * 2
    byte_count = int(stereo_bytes_per_second * (duration_ms / 1000.0))
    if byte_count <= 0:
        return b""
    frame_aligned = ((byte_count + DISCORD_FRAME_BYTES - 1) // DISCORD_FRAME_BYTES) * DISCORD_FRAME_BYTES
    return b"\x00" * frame_aligned


@dataclass(slots=True)
class LocalTtsPlaybackSnapshot:
    enabled: bool
    active: bool
    device: str
    play_count: int
    played_bytes: int
    last_error: str
    last_started_at: float | None
    last_finished_at: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active": self.active,
            "device": self.device,
            "playCount": self.play_count,
            "playedBytes": self.played_bytes,
            "lastError": self.last_error,
            "lastStartedAt": self.last_started_at,
            "lastFinishedAt": self.last_finished_at,
        }


@dataclass(frozen=True, slots=True)
class LocalTtsSourceContext:
    source_turn_id: str | None
    source_session_key: str | None
    output_mode: str
    validation_session_id: str | None
    validation_step_id: str | None
    validation_attempt_id: str | None


@dataclass(slots=True)
class _ActiveLocalTtsBinding:
    token: object
    generation: int
    source: Any
    turn_id: str | None
    session_key: str | None
    metrics: dict[str, Any] | None
    target: Any | None = None
    stream: Any | None = None
    worker: asyncio.Task[int] | None = None
    stop_requested: bool = False
    stop_acceptance_token: object | None = None
    playback_started: bool = False
    worker_terminal: bool = False
    qualified_interrupt_committed: bool = False
    cleanup_succeeded: bool | None = None


class LocalTtsPlaybackManager:
    def __init__(
        self,
        *,
        enabled: bool,
        device: str | int | None = None,
        log: Callable[[str], None] | None = None,
        stop_wait_timeout_sec: float = 1.0,
        target_is_current: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.device = normalize_output_device(device)
        self._log = log or (lambda _message: None)
        self._lock = asyncio.Lock()
        self.active = False
        self.play_count = 0
        self.played_bytes = 0
        self.last_error = ""
        self.last_started_at: float | None = None
        self.last_finished_at: float | None = None
        self._state_lock = threading.Lock()
        self._current_source: Any | None = None
        self._current_stream: Any | None = None
        self._active_binding: _ActiveLocalTtsBinding | None = None
        self._generation = 0
        self._stop_wait_timeout_sec = max(0.01, float(stop_wait_timeout_sec))
        self._target_is_current = target_is_current

    def _playback_target_is_current(
        self,
        *,
        turn_id: str | None,
        session_key: str | None,
        target: Any | None,
    ) -> bool:
        callback = self._target_is_current
        if callback is None:
            return True
        snapshot = {
            "target": target,
            "turn_id": turn_id,
            "session_key": session_key,
        }
        try:
            return callback(snapshot) is True
        except Exception:
            return False

    def snapshot(self) -> dict[str, Any]:
        return LocalTtsPlaybackSnapshot(
            enabled=self.enabled,
            active=self.active,
            device=str(self.device if self.device is not None else "default"),
            play_count=self.play_count,
            played_bytes=self.played_bytes,
            last_error=self.last_error,
            last_started_at=self.last_started_at,
            last_finished_at=self.last_finished_at,
        ).to_dict()

    @staticmethod
    def _source_context(binding: _ActiveLocalTtsBinding) -> LocalTtsSourceContext:
        meta = (
            binding.metrics.get("meta", {})
            if isinstance(binding.metrics, dict)
            and isinstance(binding.metrics.get("meta"), dict)
            else {}
        )

        def optional_text(value: Any) -> str | None:
            cleaned = str(value or "").strip()
            return cleaned or None

        return LocalTtsSourceContext(
            source_turn_id=optional_text(binding.turn_id),
            source_session_key=optional_text(binding.session_key),
            output_mode="local_speaker",
            validation_session_id=optional_text(meta.get("validation_session_id")),
            validation_step_id=optional_text(meta.get("validation_step_id")),
            validation_attempt_id=optional_text(meta.get("validation_attempt_id")),
        )

    def active_source_context(self) -> LocalTtsSourceContext | None:
        with self._state_lock:
            binding = self._active_binding
            return self._source_context(binding) if binding is not None else None

    @staticmethod
    def _target_snapshot(binding: _ActiveLocalTtsBinding) -> dict[str, Any]:
        return {
            "generation": binding.generation,
            "target": binding.target,
            "turn_id": binding.turn_id,
            "session_key": binding.session_key,
        }

    def active_target_snapshot(self) -> dict[str, Any] | None:
        with self._state_lock:
            binding = self._active_binding
            return self._target_snapshot(binding) if binding is not None else None

    def request_stop(
        self,
        *,
        reason: str = "interrupt",
    ) -> LocalTtsSourceContext | None:
        with self._state_lock:
            binding = self._active_binding
        if binding is None:
            return None
        context, controls_ok, _stop_acceptance_token = self._request_stop_for_binding(
            binding,
            reason=reason,
        )
        # A synchronous caller cannot prove that the exact playback worker has
        # terminated. Qualified evidence therefore uses request_stop_and_wait().
        if reason == "qualified_user_audio":
            return None
        return context if controls_ok else None

    async def request_stop_and_wait(
        self,
        *,
        reason: str = "interrupt",
        expected_generation: int | None = None,
        target_predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout_sec: float | None = None,
    ) -> LocalTtsSourceContext | None:
        """Stop one exact binding and return evidence only after clean teardown.

        The bounded wait is deliberately separate from request_stop(): the
        interrupt gate needs proof that the device worker finished, while sync
        cancellation callers must never block the event loop or a playback
        worker on itself.
        """
        with self._state_lock:
            binding = self._active_binding
            generation = binding.generation if binding is not None else -1
            worker = binding.worker if binding is not None else None
            playback_started = bool(binding and binding.playback_started)
            worker_terminal = bool(binding and binding.worker_terminal)
            target_snapshot = (
                self._target_snapshot(binding) if binding is not None else None
            )
        if binding is None or worker_terminal:
            return None
        if expected_generation is not None and generation != expected_generation:
            return None
        if target_predicate is not None:
            try:
                if target_snapshot is None or target_predicate(target_snapshot) is not True:
                    return None
            except Exception:
                return None

        validation_current = self._validation_attempt_is_current(binding)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(
            0.01,
            self._stop_wait_timeout_sec if timeout_sec is None else float(timeout_sec),
        )
        stop_result = await self._request_stop_for_binding_bounded(
            binding,
            reason=reason,
            deadline=deadline,
        )
        if stop_result is None:
            self._log(f"[LOCAL TTS] stop_timeout reason={reason}")
            return None
        context, controls_ok, stop_acceptance_token = stop_result
        if (
            reason == "qualified_user_audio"
            and (
                not playback_started
                or not validation_current
                or context is None
                or worker is None
                or not controls_ok
                or stop_acceptance_token is None
            )
        ):
            return None
        if context is None or worker is None:
            return None

        remaining = deadline - loop.time()
        if remaining <= 0:
            self._log(f"[LOCAL TTS] stop_timeout reason={reason}")
            return None
        try:
            await asyncio.wait_for(
                asyncio.shield(worker),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            self._log(f"[LOCAL TTS] stop_timeout reason={reason}")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(
                f"[LOCAL TTS] stop_failed reason={reason} "
                f"errorType={type(exc).__name__}"
            )
            return None

        with self._state_lock:
            stale_generation = self._generation != generation
        if stale_generation or not self._validation_attempt_is_current(binding):
            self._clear_binding_refs(binding)
            return None
        result = context if controls_ok else None
        if reason == "qualified_user_audio":
            if not self._commit_qualified_interrupt(
                binding,
                stop_acceptance_token=stop_acceptance_token,
            ):
                self._clear_binding_refs(binding)
                return None
            result = context
        return result if self._clear_binding_refs(binding) else None

    async def cleanup_matching_source(
        self,
        target_predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout_sec: float | None = None,
    ) -> tuple[int, int]:
        snapshot = self.active_target_snapshot()
        if snapshot is None or target_predicate(snapshot) is not True:
            return 0, 0
        generation = int(snapshot["generation"])
        stopped = await self.request_stop_and_wait(
            reason="privacy_purge",
            expected_generation=generation,
            target_predicate=target_predicate,
            timeout_sec=timeout_sec,
        )
        current = self.active_target_snapshot()
        remaining = int(
            current is not None and target_predicate(current) is True
        )
        return int(stopped is not None and remaining == 0), remaining

    async def _request_stop_for_binding_bounded(
        self,
        binding: _ActiveLocalTtsBinding,
        *,
        reason: str,
        deadline: float,
    ) -> tuple[LocalTtsSourceContext | None, bool, object | None] | None:
        """Run potentially blocking driver controls off-loop within one budget."""
        result: list[tuple[LocalTtsSourceContext | None, bool, object | None]] = []
        finished = threading.Event()

        def control_worker() -> None:
            try:
                result.append(self._request_stop_for_binding(binding, reason=reason))
            finally:
                finished.set()

        threading.Thread(
            target=control_worker,
            name=f"local-tts-stop-{id(binding.token)}",
            daemon=True,
        ).start()
        loop = asyncio.get_running_loop()
        while not finished.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(0.01, remaining))
        return result[0] if result else (None, False, None)

    def _request_stop_for_binding(
        self,
        binding: _ActiveLocalTtsBinding,
        *,
        reason: str,
    ) -> tuple[LocalTtsSourceContext | None, bool, object | None]:
        with self._state_lock:
            if (
                self._active_binding is not binding
                or binding.worker_terminal
                or binding.stop_requested
            ):
                return None, False, None
            binding.stop_requested = True
            stop_acceptance_token = object()
            binding.stop_acceptance_token = stop_acceptance_token
            source = binding.source
            stream = binding.stream
            context = self._source_context(binding)

        controls_ok = True
        if source is not None:
            finish = getattr(source, "finish", None)
            cleanup = getattr(source, "cleanup", None)
            try:
                if finish is not None:
                    stopped = finish()
                elif cleanup is not None:
                    stopped = cleanup()
                else:
                    stopped = None
                if stopped is False:
                    controls_ok = False
                    self._log(
                        f"[LOCAL TTS] source_stop_failed reason={reason} "
                        "errorType=ExplicitFalse"
                    )
            except Exception as exc:
                controls_ok = False
                self._log(
                    f"[LOCAL TTS] source_stop_failed reason={reason} "
                    f"errorType={type(exc).__name__}"
                )

        if stream is not None:
            for method_name in ("abort", "stop"):
                method = getattr(stream, method_name, None)
                if method is None:
                    continue
                try:
                    stopped = method()
                    if stopped is False:
                        controls_ok = False
                        self._log(
                            f"[LOCAL TTS] stream_stop_failed method={method_name} "
                            f"reason={reason} errorType=ExplicitFalse"
                        )
                except Exception as exc:
                    controls_ok = False
                    self._log(
                        f"[LOCAL TTS] stream_stop_failed method={method_name} "
                        f"reason={reason} errorType={type(exc).__name__}"
                    )
                break

        self._log(f"[LOCAL TTS] stop_requested reason={reason}")
        return context, controls_ok, stop_acceptance_token

    async def play_source(
        self,
        source: Any,
        *,
        cleanup_source: bool = True,
        on_first_playback: Callable[[], None] | None = None,
        turn_id: str | None = None,
        session_key: str | None = None,
        metrics: dict[str, Any] | None = None,
        target: Any | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        if not self._playback_target_is_current(
            turn_id=turn_id,
            session_key=session_key,
            target=target,
        ):
            self._cleanup_source(source)
            return False
        if sd is None:
            self.last_error = "sounddevice import failed"
            self._log(f"[LOCAL TTS] unavailable err={self.last_error}")
            if cleanup_source:
                self._cleanup_source(source)
            return False

        async with self._lock:
            if not self._playback_target_is_current(
                turn_id=turn_id,
                session_key=session_key,
                target=target,
            ):
                self._cleanup_source(source)
                return False
            with self._state_lock:
                stopped_by_turn_lease = bool(
                    isinstance(metrics, dict)
                    and isinstance(metrics.get("meta"), dict)
                    and metrics["meta"].get("qualified_tts_interrupt") is True
                )
            if stopped_by_turn_lease:
                if cleanup_source:
                    self._cleanup_source(source)
                return False
            with self._state_lock:
                self._generation += 1
                binding = _ActiveLocalTtsBinding(
                    token=object(),
                    generation=self._generation,
                    source=source,
                    turn_id=turn_id,
                    session_key=session_key,
                    metrics=metrics,
                    target=target,
                )
                self._active_binding = binding
                self._current_source = source
                self._current_stream = None
                self.active = True
                self.last_error = ""
                self.last_started_at = time.time()
            self._log(f"[LOCAL TTS] start device={self.device if self.device is not None else 'default'}")
            worker = asyncio.create_task(
                asyncio.to_thread(
                    self._play_source_sync,
                    source,
                    binding=binding,
                    on_first_playback=on_first_playback,
                ),
                name=f"local-tts-playback-{id(binding.token)}",
            )
            with self._state_lock:
                if self._active_binding is binding:
                    binding.worker = worker
            try:
                played = await asyncio.shield(worker)
                if played > 0:
                    self.played_bytes += played
                    self.play_count += 1
                    self._log(f"[LOCAL TTS] finished bytes={played} play_count={self.play_count}")
                else:
                    self._log("[LOCAL TTS] no_audio")
                return played > 0
            except asyncio.CancelledError:
                self._request_stop_for_binding(
                    binding,
                    reason="playback_task_cancelled",
                )
                await self._await_worker_termination(worker)
                raise
            except Exception as exc:
                error_type = type(exc).__name__
                self.last_error = f"playback_failed:{error_type}"
                self._log(
                    f"[LOCAL TTS] playback_failed errorType={error_type}"
                )
                return False
            finally:
                cleanup_succeeded = False
                if cleanup_source:
                    cleanup_succeeded = self._cleanup_source(source)
                with self._state_lock:
                    binding.cleanup_succeeded = cleanup_succeeded
                    if self._active_binding is binding:
                        self.last_finished_at = time.time()
                        self.active = False
                        self._active_binding = None
                        self._current_source = None
                        self._current_stream = None
                    binding.source = None
                    binding.stream = None
                    binding.worker = None
                    binding.target = None

    @staticmethod
    async def _await_worker_termination(worker: asyncio.Task[int]) -> None:
        """Do not release the playback binding while its thread still owns audio."""
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A repeated parent cancellation must not let the thread outlive
                # the playback lock and binding that protect the output device.
                continue
            except Exception:
                break
        with contextlib.suppress(asyncio.CancelledError, Exception):
            worker.result()

    def _play_source_sync(
        self,
        source: Any,
        *,
        binding: _ActiveLocalTtsBinding,
        on_first_playback: Callable[[], None] | None = None,
    ) -> int:
        try:
            return self._play_source_sync_inner(
                source,
                binding=binding,
                on_first_playback=on_first_playback,
            )
        finally:
            with self._state_lock:
                binding.worker_terminal = True

    def _play_source_sync_inner(
        self,
        source: Any,
        *,
        binding: _ActiveLocalTtsBinding,
        on_first_playback: Callable[[], None] | None = None,
    ) -> int:
        if self._is_stop_requested(binding):
            return 0
        first_chunk = source.read()
        source_error = getattr(source, "error", None)
        if source_error is not None:
            raise source_error
        if not first_chunk:
            return 0
        if not self._validation_attempt_is_current(binding):
            self._mark_terminal_no_fallback(
                binding,
                reason="validation_attempt_stale",
            )
            return 0

        played = 0
        with sd.RawOutputStream(
            samplerate=DISCORD_PCM_RATE,
            channels=DISCORD_PCM_CHANNELS,
            dtype="int16",
            device=self.device,
            blocksize=max(1, DISCORD_FRAME_BYTES // (DISCORD_PCM_CHANNELS * 2)),
        ) as stream:
            with self._state_lock:
                if self._active_binding is binding:
                    binding.stream = stream
                    self._current_stream = stream
            if self._is_stop_requested(binding):
                return played
            if not self._validation_attempt_is_current(binding):
                self._mark_terminal_no_fallback(
                    binding,
                    reason="validation_attempt_stale",
                )
                return played
            self._mark_first_playback_attempt(binding)
            stream.write(first_chunk)
            with self._state_lock:
                if self._active_binding is binding:
                    binding.playback_started = True
            mark_voice_latency_stage(
                binding.metrics,
                "playback_first_write",
            )
            if on_first_playback is not None:
                try:
                    on_first_playback()
                except Exception:
                    pass
            played += len(first_chunk)
            while True:
                if self._is_stop_requested(binding):
                    break
                chunk = source.read()
                if not chunk:
                    break
                if self._is_stop_requested(binding):
                    break
                stream.write(chunk)
                played += len(chunk)
            source_error = getattr(source, "error", None)
            if source_error is not None:
                raise source_error
            tail_silence = local_tts_tail_silence_bytes()
            if played > 0 and tail_silence and not self._is_stop_requested(binding):
                stream.write(tail_silence)
                played += len(tail_silence)
        return played

    @staticmethod
    def _mark_first_playback_attempt(binding: _ActiveLocalTtsBinding) -> None:
        """Lease the turn before a device write can partially emit and fail."""
        metrics = binding.metrics
        if not isinstance(metrics, dict):
            return
        meta = metrics.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            metrics["meta"] = meta
        meta["local_tts_playback_attempted"] = True

    def _commit_qualified_interrupt(
        self,
        binding: _ActiveLocalTtsBinding,
        *,
        stop_acceptance_token: object,
    ) -> bool:
        with self._state_lock:
            if (
                binding.qualified_interrupt_committed
                or not binding.playback_started
                or not binding.worker_terminal
                or binding.stop_acceptance_token is not stop_acceptance_token
                or self._generation != binding.generation
            ):
                return False
            metrics = binding.metrics
            if not isinstance(metrics, dict):
                return False
            meta = metrics.get("meta")
            if not isinstance(meta, dict):
                meta = {}
                metrics["meta"] = meta
            if meta.get("qualified_tts_interrupt") is True:
                return False
            meta["qualified_tts_interrupt"] = True
            binding.qualified_interrupt_committed = True
            return True

    @staticmethod
    def _validation_attempt_is_current(binding: _ActiveLocalTtsBinding) -> bool:
        metrics = binding.metrics
        meta = metrics.get("meta") if isinstance(metrics, dict) else None
        try:
            return validation_attempt_binding_is_current(
                meta,
                surface="local",
                reject_unbound_when_active=True,
            )
        except Exception:
            return False

    @staticmethod
    def _mark_terminal_no_fallback(
        binding: _ActiveLocalTtsBinding,
        *,
        reason: str,
    ) -> None:
        metrics = binding.metrics
        if not isinstance(metrics, dict):
            return
        meta = metrics.get("meta")
        if not isinstance(meta, dict):
            meta = {}
            metrics["meta"] = meta
        meta["local_tts_playback_terminal_no_fallback"] = True
        meta["local_tts_playback_rejected_reason"] = reason

    def _is_stop_requested(self, binding: _ActiveLocalTtsBinding) -> bool:
        with self._state_lock:
            return self._active_binding is not binding or binding.stop_requested

    def _clear_binding_refs(self, binding: _ActiveLocalTtsBinding) -> bool:
        cleanup_ok = binding.cleanup_succeeded
        if binding.source is not None:
            cleanup_ok = self._cleanup_source(binding.source)
            binding.cleanup_succeeded = cleanup_ok
        if cleanup_ok is None:
            cleanup_ok = False
        with self._state_lock:
            if self._active_binding is binding:
                self.last_finished_at = time.time()
                self.active = False
                self._active_binding = None
                self._current_source = None
                self._current_stream = None
            binding.source = None
            binding.stream = None
            binding.worker = None
            binding.target = None
            binding.turn_id = None
            binding.session_key = None
            binding.metrics = None
        return cleanup_ok is True

    @staticmethod
    def _cleanup_source(source: Any) -> bool:
        cleanup = getattr(source, "cleanup", None)
        if cleanup is not None:
            try:
                cleanup()
            except Exception:
                return False
        return True
