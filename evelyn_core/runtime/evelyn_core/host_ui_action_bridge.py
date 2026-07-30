from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable

from .host_ui_action_contract import (
    HOST_UI_ACTION_CONFIRM_TOKEN_RE,
    HOST_UI_ACTION_ELEMENT_ID_RE,
    HOST_UI_ACTION_MAX_REQUEST_BYTES,
    HOST_UI_ACTION_REQUEST_ID_RE,
    HOST_UI_ACTION_REQUEST_KEYS,
    HOST_UI_ACTION_REQUEST_SCHEMA,
    HOST_UI_ACTION_REQUEST_TTL_SEC,
    HOST_UI_ACTION_RESPONSE_SCHEMA,
    HOST_UI_ACTION_RESPONSE_TTL_SEC,
    HOST_UI_ACTION_STATUS_SCHEMA,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .ui_action_target import (
    UI_ACTION_ALLOWED_ACTIONS,
    UI_ACTION_ALLOWED_POSTCONDITIONS,
    UiActionTargetManager,
)
from .windows_accessibility import WindowsAccessibility
from .windows_accessibility_invoke import WindowsAccessibilityInvoker


class HostUiActionBridge:
    """Own the Windows-only, confirmation-bound UIA action queue."""

    def __init__(
        self,
        *,
        artifacts_root: Path | None = None,
        accessibility: Any | None = None,
        invoker: Any | None = None,
        manager: UiActionTargetManager | None = None,
        poll_interval_sec: float = 0.2,
        status_interval_sec: float = 1.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.root = (
            Path(artifacts_root or get_runtime_artifacts_root())
            / "host_ui_action"
        )
        self.requests_dir = self.root / "requests"
        self.processing_dir = self.root / "processing"
        self.responses_dir = self.root / "responses"
        self.status_path = self.root / "status.json"
        self.now = now
        self.poll_interval_sec = max(0.05, float(poll_interval_sec))
        self.status_interval_sec = max(0.2, float(status_interval_sec))
        self.accessibility = accessibility or WindowsAccessibility(now=now)
        self.invoker = invoker or WindowsAccessibilityInvoker(now=now)
        self.manager = manager or UiActionTargetManager(
            status_path=self.root / "authorization.json",
            events_dir=self.root / "events",
            now=now,
        )
        self.running = False
        self.processed_count = 0
        self.failed_count = 0
        self.rejected_count = 0
        self.last_operation = ""
        self.last_error_code = ""
        self._last_cleanup_at = 0.0
        self._last_status_at = 0.0

    def _ensure_directories(self) -> None:
        for path in (
            self.requests_dir,
            self.processing_dir,
            self.responses_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        try:
            queue_depth = len(list(self.requests_dir.glob("*.json")))
        except OSError:
            queue_depth = 0
        authorization = self.manager.status()
        return {
            "schema": HOST_UI_ACTION_STATUS_SCHEMA,
            "heartbeatAt": self.now(),
            "state": "running" if self.running else "stopped",
            "queueDepth": queue_depth,
            "processedCount": self.processed_count,
            "failedCount": self.failed_count,
            "rejectedCount": self.rejected_count,
            "lastOperation": self.last_operation,
            "lastErrorCode": self.last_error_code,
            "authorizationState": authorization.get("state"),
            "auditReady": bool(authorization.get("auditReady")),
            "allowedActions": sorted(UI_ACTION_ALLOWED_ACTIONS),
            "automaticRetry": False,
            "arbitraryCoordinates": False,
            "targetTextPersisted": False,
        }

    async def _write_status(self) -> None:
        try:
            await asyncio.to_thread(
                atomic_json_write,
                self.status_path,
                self.snapshot(),
            )
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

    def _read_request(
        self,
        path: Path,
        *,
        request_id: str,
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            if path.stat().st_size > HOST_UI_ACTION_MAX_REQUEST_BYTES:
                return None, "ui_action_request_too_large"
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None, "ui_action_invalid_json"
        if not isinstance(payload, dict) or set(payload) != HOST_UI_ACTION_REQUEST_KEYS:
            return None, "ui_action_invalid_request"
        if payload.get("schema") != HOST_UI_ACTION_REQUEST_SCHEMA:
            return None, "ui_action_invalid_schema"
        if payload.get("requestId") != request_id:
            return None, "ui_action_request_id_mismatch"
        created_at = payload.get("createdAt")
        expires_at = payload.get("expiresAt")
        if (
            isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not math.isfinite(float(created_at))
            or not math.isfinite(float(expires_at))
        ):
            return None, "ui_action_invalid_expiry"
        now = self.now()
        created_at = float(created_at)
        expires_at = float(expires_at)
        if (
            created_at > now + 2.0
            or expires_at <= now
            or expires_at <= created_at
            or expires_at - created_at
            > HOST_UI_ACTION_REQUEST_TTL_SEC + 0.001
        ):
            return (
                None,
                (
                    "ui_action_request_expired"
                    if expires_at <= now
                    else "ui_action_invalid_expiry"
                ),
            )
        operation = str(payload.get("operation") or "")
        action = str(payload.get("action") or "")
        element_id = str(payload.get("elementId") or "")
        postcondition = str(payload.get("postcondition") or "")
        confirm_token = str(payload.get("confirmToken") or "")
        if operation == "discover":
            if action or element_id or postcondition or confirm_token:
                return None, "ui_action_invalid_discover_request"
        elif operation == "preview":
            if (
                action not in UI_ACTION_ALLOWED_ACTIONS
                or not HOST_UI_ACTION_ELEMENT_ID_RE.fullmatch(element_id)
                or postcondition not in UI_ACTION_ALLOWED_POSTCONDITIONS
                or confirm_token
            ):
                return None, "ui_action_invalid_preview_request"
        elif operation == "apply":
            if (
                action
                or element_id
                or postcondition
                or not HOST_UI_ACTION_CONFIRM_TOKEN_RE.fullmatch(
                    confirm_token
                )
            ):
                return None, "ui_action_invalid_apply_request"
        else:
            return None, "ui_action_invalid_operation"
        return {
            **payload,
            "createdAt": created_at,
            "expiresAt": expires_at,
            "operation": operation,
            "action": action,
            "elementId": element_id,
            "postcondition": postcondition,
            "confirmToken": confirm_token,
        }, ""

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
            if not HOST_UI_ACTION_REQUEST_ID_RE.fullmatch(request_id):
                self.rejected_count += 1
                self._unlink_quietly(request_path)
                continue
            claimed = self.processing_dir / request_path.name
            try:
                os.replace(request_path, claimed)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            await self._process_claimed(claimed, request_id=request_id)
            processed += 1
        return processed

    async def _process_claimed(
        self,
        path: Path,
        *,
        request_id: str,
    ) -> None:
        request, error_code = self._read_request(path, request_id=request_id)
        try:
            if request is None:
                self.rejected_count += 1
                await self._write_response(
                    request_id=request_id,
                    operation="",
                    ok=False,
                    error_code=error_code,
                )
                return
            operation = request["operation"]
            self.last_operation = operation
            if operation == "discover":
                try:
                    current = await self.accessibility.read()
                except Exception:
                    outcome = {
                        "ok": False,
                        "error": "ui_action_observation_unavailable",
                    }
                else:
                    outcome = self.manager.discover(observation=current)
                await self._write_response(
                    request_id=request_id,
                    operation=operation,
                    ok=bool(outcome.get("ok")),
                    error_code=str(outcome.get("error") or ""),
                    targets=outcome if outcome.get("ok") else {},
                )
                return
            if operation == "preview":
                try:
                    current = await self.accessibility.read()
                except Exception:
                    outcome = {
                        "ok": False,
                        "error": "ui_action_observation_unavailable",
                    }
                else:
                    outcome = self.manager.preview(
                        observation=current,
                        element_id=request["elementId"],
                        action=request["action"],
                        postcondition=request["postcondition"],
                    )
                await self._write_response(
                    request_id=request_id,
                    operation=operation,
                    ok=bool(outcome.get("ok")),
                    error_code=str(outcome.get("error") or ""),
                    preview=outcome if outcome.get("ok") else {},
                )
                return

            try:
                current = await self.accessibility.read()
            except Exception:
                begin = {
                    "ok": False,
                    "error": "ui_action_observation_unavailable",
                }
            else:
                begin = self.manager.begin_apply(
                    confirm_token=request["confirmToken"],
                    observation=current,
                )
            if not begin.get("ok"):
                await self._write_response(
                    request_id=request_id,
                    operation=operation,
                    ok=False,
                    error_code=str(begin.get("error") or ""),
                )
                return
            execution = dict(begin["execution"])
            try:
                execution_result = await self.invoker.invoke(**execution)
            except Exception:
                execution_result = {
                    "schema": "windows_ui_action.result.v1",
                    "ok": False,
                    "errorCode": "windows_ui_action_failed",
                    "completedAt": self.now(),
                    "executed": False,
                    **execution,
                }
            post_observation: dict[str, Any] | None = None
            if execution_result.get("executed"):
                try:
                    post_observation = await self.accessibility.read()
                except Exception:
                    post_observation = None
            result = self.manager.finish_apply(
                operation_id=begin["operationId"],
                execution_result=execution_result,
                post_observation=post_observation,
            )
            await self._write_response(
                request_id=request_id,
                operation=operation,
                ok=bool(result.get("ok")),
                error_code=str(result.get("error") or ""),
                result=result,
            )
        finally:
            self._unlink_quietly(path)
            await self._write_status()

    async def _write_response(
        self,
        *,
        request_id: str,
        operation: str,
        ok: bool,
        error_code: str,
        targets: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        created_at = self.now()
        response = {
            "schema": HOST_UI_ACTION_RESPONSE_SCHEMA,
            "requestId": request_id,
            "createdAt": created_at,
            "expiresAt": created_at + HOST_UI_ACTION_RESPONSE_TTL_SEC,
            "ok": bool(ok),
            "operation": (
                operation
                if operation in {"discover", "preview", "apply"}
                else ""
            ),
            "errorCode": str(error_code or "")[:80],
            "targets": dict(targets or {}),
            "preview": dict(preview or {}),
            "result": dict(result or {}),
        }
        await asyncio.to_thread(
            atomic_json_write,
            self.responses_dir / f"{request_id}.json",
            response,
        )
        self.processed_count += 1
        if not ok:
            self.failed_count += 1
        self.last_error_code = response["errorCode"]

    async def _cleanup_stale(self, *, force: bool = False) -> None:
        if not force and self.now() - self._last_cleanup_at < 5.0:
            return
        now = self.now()
        for directory, max_age in (
            (self.requests_dir, HOST_UI_ACTION_REQUEST_TTL_SEC),
            (self.processing_dir, HOST_UI_ACTION_REQUEST_TTL_SEC),
            (self.responses_dir, HOST_UI_ACTION_RESPONSE_TTL_SEC),
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


__all__ = ["HostUiActionBridge"]
