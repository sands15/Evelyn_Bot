from __future__ import annotations

import asyncio
import contextlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Awaitable, Callable
import uuid

from .host_ui_action_contract import (
    HOST_UI_ACTION_CONFIRM_TOKEN_RE,
    HOST_UI_ACTION_ELEMENT_ID_RE,
    HOST_UI_ACTION_MAX_RESPONSE_AGE_SEC,
    HOST_UI_ACTION_MAX_RESPONSE_BYTES,
    HOST_UI_ACTION_REQUEST_SCHEMA,
    HOST_UI_ACTION_REQUEST_TTL_SEC,
    HOST_UI_ACTION_RESPONSE_KEYS,
    HOST_UI_ACTION_RESPONSE_SCHEMA,
    HOST_UI_ACTION_RESPONSE_TTL_SEC,
)
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .ui_action_target import (
    UI_ACTION_ALLOWED_ACTIONS,
    UI_ACTION_ALLOWED_POSTCONDITIONS,
)


HOST_UI_ACTION_DEFAULT_TIMEOUT_SEC = 10.0
_UI_ACTION_PREVIEW_KEYS = frozenset(
    {
        "ok",
        "schema",
        "confirmToken",
        "expiresAt",
        "requiresExplicitConfirmation",
        "action",
        "postcondition",
        "target",
        "policy",
    }
)
_UI_ACTION_TARGET_KEYS = frozenset(
    {
        "elementId",
        "name",
        "controlType",
        "windowTitle",
        "windowClass",
    }
)
_UI_ACTION_PREVIEW_POLICY_KEYS = frozenset(
    {
        "reobserveBeforeExecute",
        "verifyAfterExecute",
        "automaticRetry",
        "arbitraryCoordinates",
    }
)
_UI_ACTION_RESULT_KEYS = frozenset(
    {
        "ok",
        "schema",
        "state",
        "error",
        "operationId",
        "action",
        "postcondition",
        "executed",
        "verified",
        "automaticRetry",
    }
)
_UI_ACTION_OPERATION_ID_RE = re.compile(r"^ui-action-[0-9a-f]{24}$")
_UI_ACTION_ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{1,80}$")


def _failed(operation: str, error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "error": str(error or "host_ui_action_failed")[:80],
        "preview": {},
        "result": {},
    }


def _read_response(
    path: Path,
    *,
    request_id: str,
    operation: str,
    now: float,
) -> dict[str, Any]:
    try:
        if path.stat().st_size > HOST_UI_ACTION_MAX_RESPONSE_BYTES:
            return _failed(operation, "ui_action_response_too_large")
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return _failed(operation, "ui_action_invalid_response_json")
    if not isinstance(payload, dict) or set(payload) != HOST_UI_ACTION_RESPONSE_KEYS:
        return _failed(operation, "ui_action_invalid_response")
    if (
        payload.get("schema") != HOST_UI_ACTION_RESPONSE_SCHEMA
        or payload.get("requestId") != request_id
        or payload.get("operation") != operation
        or type(payload.get("ok")) is not bool
        or not isinstance(payload.get("preview"), dict)
        or not isinstance(payload.get("result"), dict)
    ):
        return _failed(operation, "ui_action_invalid_response")
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
        return _failed(operation, "ui_action_invalid_response_lifetime")
    created_at = float(created_at)
    expires_at = float(expires_at)
    if (
        created_at > now + 2.0
        or expires_at <= created_at
        or expires_at - created_at
        > HOST_UI_ACTION_RESPONSE_TTL_SEC + 0.001
    ):
        return _failed(operation, "ui_action_invalid_response_lifetime")
    if now - created_at > HOST_UI_ACTION_MAX_RESPONSE_AGE_SEC:
        return _failed(operation, "ui_action_response_stale")
    if expires_at <= now:
        return _failed(operation, "ui_action_response_expired")
    error_code = str(payload.get("errorCode") or "")
    if error_code and not _UI_ACTION_ERROR_CODE_RE.fullmatch(error_code):
        return _failed(operation, "ui_action_invalid_response")
    if payload["ok"] and error_code:
        return _failed(operation, "ui_action_contradictory_response")
    if not payload["ok"] and not error_code:
        return _failed(operation, "ui_action_contradictory_response")
    if operation == "preview":
        if payload["result"]:
            return _failed(operation, "ui_action_contradictory_response")
        if not payload["ok"]:
            if payload["preview"]:
                return _failed(
                    operation,
                    "ui_action_contradictory_response",
                )
            return {
                "ok": False,
                "operation": operation,
                "error": error_code,
                "preview": {},
                "result": {},
            }
        preview = payload["preview"]
        target = preview.get("target")
        policy = preview.get("policy")
        preview_expires_at = preview.get("expiresAt")
        if (
            set(preview) != _UI_ACTION_PREVIEW_KEYS
            or preview.get("ok") is not True
            or preview.get("schema") != "ui_action.preview.v1"
            or not HOST_UI_ACTION_CONFIRM_TOKEN_RE.fullmatch(
                str(preview.get("confirmToken") or "")
            )
            or isinstance(preview_expires_at, bool)
            or not isinstance(preview_expires_at, (int, float))
            or not math.isfinite(float(preview_expires_at))
            or float(preview_expires_at) <= now
            or float(preview_expires_at) - now > 32.0
            or preview.get("requiresExplicitConfirmation") is not True
            or preview.get("action") not in UI_ACTION_ALLOWED_ACTIONS
            or preview.get("postcondition")
            not in UI_ACTION_ALLOWED_POSTCONDITIONS
            or not isinstance(target, dict)
            or set(target) != _UI_ACTION_TARGET_KEYS
            or not HOST_UI_ACTION_ELEMENT_ID_RE.fullmatch(
                str(target.get("elementId") or "")
            )
            or not str(target.get("name") or "")
            or len(str(target.get("name") or "")) > 180
            or len(str(target.get("windowTitle") or "")) > 240
            or len(str(target.get("windowClass") or "")) > 80
            or (
                not str(target.get("windowTitle") or "")
                and not str(target.get("windowClass") or "")
            )
            or target.get("controlType") != "Button"
            or not isinstance(policy, dict)
            or set(policy) != _UI_ACTION_PREVIEW_POLICY_KEYS
            or policy.get("reobserveBeforeExecute") is not True
            or policy.get("verifyAfterExecute") is not True
            or policy.get("automaticRetry") is not False
            or policy.get("arbitraryCoordinates") is not False
        ):
            return _failed(operation, "ui_action_invalid_preview_contract")
    if operation == "apply":
        if payload["preview"]:
            return _failed(operation, "ui_action_contradictory_response")
        result = payload["result"]
        if not payload["ok"] and not result:
            return {
                "ok": False,
                "operation": operation,
                "error": error_code,
                "preview": {},
                "result": {},
            }
        result_error = str(result.get("error") or "")
        if (
            set(result) != _UI_ACTION_RESULT_KEYS
            or type(result.get("ok")) is not bool
            or result.get("schema") != "ui_action.result.v1"
            or result.get("state")
            not in {
                "verified",
                "outcome_unverified",
                "execution_failed",
                "authorization_audit_unavailable",
            }
            or (
                result_error
                and not _UI_ACTION_ERROR_CODE_RE.fullmatch(result_error)
            )
            or not _UI_ACTION_OPERATION_ID_RE.fullmatch(
                str(result.get("operationId") or "")
            )
            or result.get("action") not in UI_ACTION_ALLOWED_ACTIONS
            or result.get("postcondition")
            not in UI_ACTION_ALLOWED_POSTCONDITIONS
            or type(result.get("executed")) is not bool
            or type(result.get("verified")) is not bool
            or result.get("automaticRetry") is not False
        ):
            return _failed(operation, "ui_action_invalid_result_contract")
        result_state = result["state"]
        state_valid = bool(
            (
                result_state == "verified"
                and result["ok"] is True
                and result["executed"] is True
                and result["verified"] is True
                and not result_error
            )
            or (
                result_state == "outcome_unverified"
                and result["ok"] is False
                and result["executed"] is True
                and result["verified"] is False
                and bool(result_error)
            )
            or (
                result_state == "execution_failed"
                and result["ok"] is False
                and result["executed"] is False
                and result["verified"] is False
                and bool(result_error)
            )
            or (
                result_state == "authorization_audit_unavailable"
                and result["ok"] is False
                and result["verified"] is False
                and result_error
                == "ui_action_authorization_audit_unavailable"
            )
        )
        if (
            not state_valid
            or result["ok"] is not payload["ok"]
            or (
                payload["ok"]
                and error_code
            )
            or (
                not payload["ok"]
                and result_error != error_code
            )
        ):
            return _failed(operation, "ui_action_contradictory_response")
    return {
        "ok": bool(payload["ok"]),
        "operation": operation,
        "error": error_code,
        "preview": dict(payload["preview"]),
        "result": dict(payload["result"]),
    }


async def _request(
    *,
    operation: str,
    action: str = "",
    element_id: str = "",
    postcondition: str = "",
    confirm_token: str = "",
    artifacts_root: Path | None = None,
    timeout_sec: float = HOST_UI_ACTION_DEFAULT_TIMEOUT_SEC,
    poll_interval_sec: float = 0.1,
    now: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> dict[str, Any]:
    if operation == "preview":
        if (
            action not in UI_ACTION_ALLOWED_ACTIONS
            or not HOST_UI_ACTION_ELEMENT_ID_RE.fullmatch(element_id)
            or postcondition not in UI_ACTION_ALLOWED_POSTCONDITIONS
            or confirm_token
        ):
            return _failed(operation, "ui_action_invalid_preview_request")
    elif operation == "apply":
        if (
            action
            or element_id
            or postcondition
            or not HOST_UI_ACTION_CONFIRM_TOKEN_RE.fullmatch(confirm_token)
        ):
            return _failed(operation, "ui_action_invalid_apply_request")
    else:
        return _failed(operation, "ui_action_invalid_operation")
    root = (
        Path(artifacts_root or get_runtime_artifacts_root())
        / "host_ui_action"
    )
    requests_dir = root / "requests"
    responses_dir = root / "responses"
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    request_path = requests_dir / f"{request_id}.json"
    response_path = responses_dir / f"{request_id}.json"
    created_at = now()
    payload = {
        "schema": HOST_UI_ACTION_REQUEST_SCHEMA,
        "requestId": request_id,
        "createdAt": created_at,
        "expiresAt": created_at + HOST_UI_ACTION_REQUEST_TTL_SEC,
        "operation": operation,
        "action": action,
        "elementId": element_id,
        "postcondition": postcondition,
        "confirmToken": confirm_token,
    }
    try:
        await asyncio.to_thread(atomic_json_write, request_path, payload)
    except Exception:
        return _failed(operation, "ui_action_request_write_failed")
    deadline = monotonic() + max(0.05, float(timeout_sec))
    try:
        while monotonic() < deadline:
            if response_path.exists():
                return await asyncio.to_thread(
                    _read_response,
                    response_path,
                    request_id=request_id,
                    operation=operation,
                    now=now(),
                )
            await sleep(max(0.01, float(poll_interval_sec)))
        return _failed(operation, "host_ui_action_timeout")
    finally:
        for path in (request_path, response_path):
            with contextlib.suppress(
                FileNotFoundError,
                PermissionError,
                OSError,
            ):
                path.unlink()


async def preview_host_ui_action(
    *,
    element_id: str,
    action: str,
    postcondition: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return await _request(
        operation="preview",
        action=action,
        element_id=element_id,
        postcondition=postcondition,
        **kwargs,
    )


async def apply_host_ui_action(
    *,
    confirm_token: str,
    **kwargs: Any,
) -> dict[str, Any]:
    return await _request(
        operation="apply",
        confirm_token=confirm_token,
        **kwargs,
    )


__all__ = [
    "HOST_UI_ACTION_DEFAULT_TIMEOUT_SEC",
    "apply_host_ui_action",
    "preview_host_ui_action",
]
