from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

import aiohttp

from .host_vision_contract import (
    HOST_VISION_MAX_OBSERVATION_CHARS,
    HOST_VISION_MAX_REQUEST_BYTES,
    HOST_VISION_MAX_REQUEST_TTL_SEC,
    HOST_VISION_MAX_USER_TEXT_CHARS,
    HOST_VISION_REQUEST_ID_RE,
    HOST_VISION_REQUEST_KEYS,
    HOST_VISION_REQUEST_SCHEMA,
    HOST_VISION_RESPONSE_SCHEMA,
    HOST_VISION_RESPONSE_TTL_SEC,
    HOST_VISION_SCREENSHOT_RETENTION_SEC,
    HOST_VISION_STATUS_SCHEMA,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .text import clean_text
from .vision_quality import build_vision_quality
from .vision_request_composition import VisionRequestComposition, VisionRequestCompositionDeps
from .vision_runtime import VisionEvidence, vision_evidence_from_metrics
from .vision_watch import vision_watch_scene_is_unreliable
from .windows_native_ocr import WindowsNativeOcr
from .windows_foreground_context import read_windows_foreground_window


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class HostVisionBridge:
    """Owns the narrow shared-folder capability for Windows screen observation."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        artifacts_root: Path | None = None,
        composition: VisionRequestComposition | None = None,
        capture_enabled: bool | None = None,
        poll_interval_sec: float = 0.2,
        status_interval_sec: float = 1.0,
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session
        self.root = Path(artifacts_root or get_runtime_artifacts_root()) / "host_vision"
        self.requests_dir = self.root / "requests"
        self.processing_dir = self.root / "processing"
        self.responses_dir = self.root / "responses"
        self.screenshots_dir = self.root / "screenshots"
        self.status_path = self.root / "status.json"
        self.capture_enabled = (
            _env_bool("HOST_VISION_CAPTURE_ENABLED", True)
            if capture_enabled is None
            else bool(capture_enabled)
        )
        self.poll_interval_sec = max(0.05, float(poll_interval_sec))
        self.status_interval_sec = max(0.2, float(status_interval_sec))
        self.now = now
        self.monotonic = monotonic
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.rejected_count = 0
        self.expired_count = 0
        self.last_request_id = ""
        self.last_error_code = ""
        self.last_latency_ms: float | None = None
        self.last_evidence = VisionEvidence()
        self.last_screenshot_deleted: bool | None = None
        self._last_cleanup_at = 0.0
        self._last_status_at = 0.0
        self.composition = composition or self._build_default_composition()

    def _build_default_composition(self) -> VisionRequestComposition:
        service_url = os.getenv("VISION_SERVICE_URL", "http://127.0.0.1:8891").rstrip("/")
        analyze_timeout = max(5.0, float(os.getenv("VISION_ANALYZE_TIMEOUT_SEC", "120")))

        async def get_session() -> aiohttp.ClientSession:
            return self.session

        async def get_foreground_window() -> dict[str, Any]:
            return await asyncio.to_thread(read_windows_foreground_window)

        windows_ocr = WindowsNativeOcr(
            screenshot_root=self.screenshots_dir,
        )
        return VisionRequestComposition(
            VisionRequestCompositionDeps(
                screenshot_dir=self.screenshots_dir,
                capture_all_screens=_env_bool("VISION_CAPTURE_ALL_SCREENS", False),
                delete_request_images=True,
                auto_capture_enabled=self.capture_enabled,
                analyze_timeout_sec=analyze_timeout,
                service_url=service_url,
                build_vision_quality=build_vision_quality,
                vision_watch_scene_is_unreliable=vision_watch_scene_is_unreliable,
                get_http_session=get_session,
                client_timeout_factory=aiohttp.ClientTimeout,
                clean_text=clean_text,
                to_thread=asyncio.to_thread,
                monotonic=self.monotonic,
                local_ocr_provider=windows_ocr.recognize,
                local_window_provider=get_foreground_window,
            )
        )

    def _ensure_directories(self) -> None:
        for path in (
            self.requests_dir,
            self.processing_dir,
            self.responses_dir,
            self.screenshots_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        try:
            queue_depth = len(list(self.requests_dir.glob("*.json")))
        except OSError:
            queue_depth = 0
        return {
            "schema": HOST_VISION_STATUS_SCHEMA,
            "heartbeatAt": self.now(),
            "state": "running" if self.running else "stopped",
            "captureEnabled": self.capture_enabled,
            "queueDepth": queue_depth,
            "processedCount": self.processed_count,
            "failedCount": self.failed_count,
            "rejectedCount": self.rejected_count,
            "expiredCount": self.expired_count,
            "lastRequestId": self.last_request_id,
            "lastErrorCode": self.last_error_code,
            "lastLatencyMs": self.last_latency_ms,
            "lastEvidence": self.last_evidence.to_dict(),
            "lastScreenshotDeleted": self.last_screenshot_deleted,
        }

    async def _write_status(self) -> None:
        try:
            await asyncio.to_thread(atomic_json_write, self.status_path, self.snapshot())
        except Exception:
            return
        self._last_status_at = self.now()

    async def run(self) -> None:
        self._ensure_directories()
        self.running = True
        await self._cleanup_stale(force=True)
        await self._write_status()
        try:
            while True:
                await self.process_pending(limit=1)
                now = self.now()
                if now - self._last_cleanup_at >= 5.0:
                    await self._cleanup_stale(force=True)
                if now - self._last_status_at >= self.status_interval_sec:
                    await self._write_status()
                await asyncio.sleep(self.poll_interval_sec)
        finally:
            self.running = False
            with contextlib.suppress(Exception):
                await self._write_status()

    async def process_pending(self, *, limit: int = 1) -> int:
        self._ensure_directories()
        try:
            candidates = sorted(
                self.requests_dir.glob("*.json"),
                key=lambda path: path.stat().st_mtime,
            )
        except OSError:
            return 0
        processed = 0
        for request_path in candidates[: max(1, int(limit))]:
            request_id = request_path.stem
            if not HOST_VISION_REQUEST_ID_RE.fullmatch(request_id):
                self.rejected_count += 1
                self._unlink_quietly(request_path)
                continue
            claimed_path = self.processing_dir / request_path.name
            try:
                os.replace(request_path, claimed_path)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            await self._process_claimed(claimed_path, request_id=request_id)
            processed += 1
        return processed

    def _read_request(self, path: Path, *, request_id: str) -> tuple[dict[str, Any] | None, str]:
        try:
            if path.stat().st_size > HOST_VISION_MAX_REQUEST_BYTES:
                return None, "request_too_large"
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None, "invalid_json"
        if not isinstance(payload, dict) or set(payload) != HOST_VISION_REQUEST_KEYS:
            return None, "invalid_request"
        if payload.get("schema") != HOST_VISION_REQUEST_SCHEMA:
            return None, "invalid_schema"
        if payload.get("requestId") != request_id:
            return None, "request_id_mismatch"
        created_at = payload.get("createdAt")
        expires_at = payload.get("expiresAt")
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
        ):
            return None, "invalid_expiry"
        now = self.now()
        if float(created_at) > now + 5.0:
            return None, "invalid_expiry"
        if float(expires_at) <= now:
            return None, "request_expired"
        if float(expires_at) - float(created_at) > HOST_VISION_MAX_REQUEST_TTL_SEC:
            return None, "invalid_expiry"
        user_text = payload.get("userText")
        if not isinstance(user_text, str):
            return None, "invalid_user_text"
        user_text = clean_text(user_text)
        if not user_text or len(user_text) > HOST_VISION_MAX_USER_TEXT_CHARS:
            return None, "invalid_user_text"
        if type(payload.get("runOcr")) is not bool:
            return None, "invalid_run_ocr"
        return {
            **payload,
            "createdAt": float(created_at),
            "expiresAt": float(expires_at),
            "userText": user_text,
        }, ""

    async def _process_claimed(self, path: Path, *, request_id: str) -> None:
        request, error_code = self._read_request(path, request_id=request_id)
        self.last_request_id = request_id
        started = self.monotonic()
        try:
            if request is None:
                self.rejected_count += 1
                if error_code == "request_expired":
                    self.expired_count += 1
                await self._write_failure_response(request_id, error_code)
                return

            metrics: dict[str, Any] = {"meta": {}, "marks": {}}
            try:
                observation = await self.composition.build_live_vision_context(
                    request["userText"],
                    metrics=metrics,
                    run_ocr=bool(request["runOcr"]),
                )
            except Exception:
                observation = (
                    "Local screen vision failed before a usable observation was produced. "
                    "Do not claim the screen was analyzed."
                )
                evidence = VisionEvidence(
                    state="failed",
                    reason_code="host_vision_runtime_error",
                )
            else:
                evidence = vision_evidence_from_metrics(metrics)

            observation = clean_text(observation)[:HOST_VISION_MAX_OBSERVATION_CHARS]
            if evidence.state == "observed" and not observation:
                evidence = VisionEvidence(
                    state="unreliable",
                    reason_code="empty_observation",
                )
            error_code = "" if evidence.evidence_available else evidence.reason_code
            latency_ms = max(0.0, (self.monotonic() - started) * 1000.0)
            meta = metrics.get("meta") if isinstance(metrics.get("meta"), dict) else {}
            response = {
                "schema": HOST_VISION_RESPONSE_SCHEMA,
                "requestId": request_id,
                "createdAt": self.now(),
                "expiresAt": self.now() + HOST_VISION_RESPONSE_TTL_SEC,
                "observation": observation,
                "evidence": evidence.to_dict(),
                "errorCode": clean_text(error_code)[:80],
                "latencyMs": round(latency_ms, 1),
                "screenshotDeleted": bool(meta.get("vision_capture_deleted")),
                "sceneChars": max(0, int(meta.get("vision_scene_chars") or 0)),
                "ocrChars": max(0, int(meta.get("vision_ocr_chars") or 0)),
            }
            await asyncio.to_thread(
                atomic_json_write,
                self.responses_dir / f"{request_id}.json",
                response,
            )
            self.processed_count += 1
            if not evidence.evidence_available:
                self.failed_count += 1
            self.last_evidence = evidence
            self.last_error_code = response["errorCode"]
            self.last_latency_ms = response["latencyMs"]
            self.last_screenshot_deleted = response["screenshotDeleted"]
        finally:
            self._unlink_quietly(path)
            await self._write_status()

    async def _write_failure_response(self, request_id: str, error_code: str) -> None:
        evidence = VisionEvidence(state="failed", reason_code=error_code or "invalid_request")
        response = {
            "schema": HOST_VISION_RESPONSE_SCHEMA,
            "requestId": request_id,
            "createdAt": self.now(),
            "expiresAt": self.now() + HOST_VISION_RESPONSE_TTL_SEC,
            "observation": (
                "Local screen vision request was rejected before capture. "
                "Do not claim the screen was analyzed."
            ),
            "evidence": evidence.to_dict(),
            "errorCode": clean_text(error_code or "invalid_request")[:80],
            "latencyMs": 0.0,
            "screenshotDeleted": True,
            "sceneChars": 0,
            "ocrChars": 0,
        }
        await asyncio.to_thread(
            atomic_json_write,
            self.responses_dir / f"{request_id}.json",
            response,
        )
        self.failed_count += 1
        self.last_evidence = evidence
        self.last_error_code = response["errorCode"]
        self.last_latency_ms = 0.0
        self.last_screenshot_deleted = True

    async def _cleanup_stale(self, *, force: bool = False) -> None:
        if not force and self.now() - self._last_cleanup_at < 5.0:
            return
        now = self.now()
        for directory, max_age in (
            (self.requests_dir, HOST_VISION_MAX_REQUEST_TTL_SEC),
            (self.processing_dir, HOST_VISION_MAX_REQUEST_TTL_SEC),
            (self.responses_dir, HOST_VISION_RESPONSE_TTL_SEC),
            (self.screenshots_dir, HOST_VISION_SCREENSHOT_RETENTION_SEC),
        ):
            try:
                candidates = list(directory.iterdir())
            except OSError:
                continue
            for path in candidates:
                try:
                    if not path.is_file() or now - path.stat().st_mtime <= max_age:
                        continue
                except OSError:
                    continue
                self._unlink_quietly(path)
        self._last_cleanup_at = now

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        with contextlib.suppress(FileNotFoundError, PermissionError, OSError):
            path.unlink()


__all__ = [
    "HostVisionBridge",
]
