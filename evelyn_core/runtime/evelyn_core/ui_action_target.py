from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable

from .runtime_artifact_io import atomic_json_write
from .windows_accessibility import WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA


UI_ACTION_PREVIEW_SCHEMA = "ui_action.preview.v1"
UI_ACTION_TARGETS_SCHEMA = "ui_action.targets.v1"
UI_ACTION_EXECUTION_SCHEMA = "ui_action.execution.v1"
UI_ACTION_RESULT_SCHEMA = "ui_action.result.v1"
UI_ACTION_STATUS_SCHEMA = "ui_action.status.v1"
UI_ACTION_EVENT_SCHEMA = "ui_action.event.v1"
UI_ACTION_PREVIEW_TTL_SEC = 30.0
UI_ACTION_DISCOVERY_MAX_TARGETS = 24
UI_ACTION_TOKEN_RECORD_RETENTION_SEC = 300.0
UI_ACTION_OBSERVATION_MAX_AGE_SEC = 5.0
UI_ACTION_FUTURE_TOLERANCE_SEC = 2.0
UI_ACTION_ALLOWED_ACTIONS = frozenset({"invoke"})
UI_ACTION_ALLOWED_CONTROL_TYPES = frozenset({"Button"})
UI_ACTION_ALLOWED_POSTCONDITIONS = frozenset(
    {"target_absent", "target_disabled", "window_changed"}
)
_ELEMENT_ID_RE = re.compile(r"^[0-9a-f]{20}$")
_SAFE_CODE_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_EXECUTION_RESULT_KEYS = frozenset(
    {
        "schema",
        "ok",
        "errorCode",
        "completedAt",
        "executed",
        "action",
        "elementId",
        "windowDigest",
    }
)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bounded_text(value: Any, *, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit]


def _safe_code(value: Any, *, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate if _SAFE_CODE_RE.fullmatch(candidate) else fallback


def _digest(parts: list[str]) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _window_digest(title: str, class_name: str) -> str:
    return _digest([title, class_name])


def _normalize_bounds(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    bounds: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        number = _finite_number(value.get(key))
        if number is None:
            return None
        bounds[key] = round(number, 1)
    if bounds["width"] <= 0.0 or bounds["height"] <= 0.0:
        return None
    return bounds


def _normalize_element(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    element_id = str(value.get("elementId") or "")
    control_type = _bounded_text(value.get("controlType"), limit=40)
    name = _bounded_text(value.get("name"), limit=180)
    automation_id = _bounded_text(value.get("automationId"), limit=120)
    bounds = _normalize_bounds(value.get("bounds"))
    if (
        not _ELEMENT_ID_RE.fullmatch(element_id)
        or control_type not in UI_ACTION_ALLOWED_CONTROL_TYPES
        or not name
        or type(value.get("isEnabled")) is not bool
        or bounds is None
    ):
        return None
    return {
        "elementId": element_id,
        "name": name,
        "automationId": automation_id,
        "controlType": control_type,
        "isEnabled": bool(value["isEnabled"]),
        "bounds": bounds,
    }


def normalize_ui_action_observation(
    payload: Any,
    *,
    now: float,
) -> tuple[dict[str, Any] | None, str]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != WINDOWS_ACCESSIBILITY_OBSERVATION_SCHEMA
        or payload.get("attempted") is not True
        or payload.get("available") is not True
    ):
        return None, "ui_action_observation_unavailable"
    captured_at = _finite_number(payload.get("capturedAt"))
    if captured_at is None:
        return None, "ui_action_observation_timestamp_invalid"
    if captured_at > now + UI_ACTION_FUTURE_TOLERANCE_SEC:
        return None, "ui_action_observation_timestamp_invalid"
    if now - captured_at > UI_ACTION_OBSERVATION_MAX_AGE_SEC:
        return None, "ui_action_observation_stale"
    title = _bounded_text(payload.get("windowTitle"), limit=240)
    class_name = _bounded_text(payload.get("windowClass"), limit=80)
    if not title and not class_name:
        return None, "ui_action_foreground_identity_missing"
    raw_elements = payload.get("elements")
    if (
        not isinstance(raw_elements, list)
        or type(payload.get("truncated")) is not bool
    ):
        return None, "ui_action_observation_invalid"
    elements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_elements:
        element = _normalize_element(raw)
        if element is None:
            continue
        element_id = element["elementId"]
        if element_id in seen:
            return None, "ui_action_target_ambiguous"
        seen.add(element_id)
        elements.append(element)
    return {
        "capturedAt": captured_at,
        "windowTitle": title,
        "windowClass": class_name,
        "windowDigest": _window_digest(title, class_name),
        "truncated": payload["truncated"],
        "elements": elements,
    }, ""


def _target_fingerprint(
    observation: dict[str, Any],
    target: dict[str, Any],
) -> str:
    bounds = target["bounds"]
    return _digest(
        [
            observation["windowTitle"],
            observation["windowClass"],
            target["elementId"],
            target["controlType"],
            target["automationId"],
            target["name"],
            str(bounds["x"]),
            str(bounds["y"]),
            str(bounds["width"]),
            str(bounds["height"]),
        ]
    )


def _find_target(
    observation: dict[str, Any],
    element_id: str,
) -> tuple[dict[str, Any] | None, str]:
    matches = [
        item
        for item in observation["elements"]
        if item["elementId"] == element_id
    ]
    if not matches:
        return None, "ui_action_target_missing"
    if len(matches) != 1:
        return None, "ui_action_target_ambiguous"
    target = matches[0]
    if not target["isEnabled"]:
        return None, "ui_action_target_disabled"
    return target, ""


class UiActionTargetManager:
    """Process-owned confirmation and evidence gate for one UIA Button invoke."""

    def __init__(
        self,
        *,
        status_path: Path,
        events_dir: Path,
        now: Callable[[], float] = time.time,
        preview_ttl_sec: float = UI_ACTION_PREVIEW_TTL_SEC,
        process_nonce: str | None = None,
    ) -> None:
        self.status_path = Path(status_path)
        self.events_dir = Path(events_dir)
        self.now = now
        self.preview_ttl_sec = min(
            UI_ACTION_PREVIEW_TTL_SEC,
            max(1.0, float(preview_ttl_sec)),
        )
        self.process_nonce = str(process_nonce or secrets.token_hex(12))
        self._lock = threading.RLock()
        self._tokens: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._state = "authorization_required"
        self._discovery_count = 0
        self._preview_count = 0
        self._execution_count = 0
        self._verified_count = 0
        self._denied_count = 0
        self._audit_ready = self._append_event(
            event="process_started",
            reason_code="process_restart",
        )
        if not self._audit_ready:
            self._state = "authorization_audit_unavailable"
        self._write_status()

    def _event_path(self, timestamp: float) -> Path:
        day = time.strftime("%Y%m%d", time.localtime(timestamp))
        return self.events_dir / f"{day}.jsonl"

    def _append_event(
        self,
        *,
        event: str,
        operation_id: str = "",
        action: str = "",
        postcondition: str = "",
        target_digest: str = "",
        reason_code: str = "",
        executed: bool | None = None,
        verified: bool | None = None,
    ) -> bool:
        timestamp = self.now()
        record = {
            "schema": UI_ACTION_EVENT_SCHEMA,
            "eventId": secrets.token_hex(12),
            "at": timestamp,
            "event": _safe_code(event, fallback="invalid_event"),
            "processNonce": self.process_nonce,
            "operationId": _bounded_text(operation_id, limit=80),
            "action": action if action in UI_ACTION_ALLOWED_ACTIONS else "",
            "postcondition": (
                postcondition
                if postcondition in UI_ACTION_ALLOWED_POSTCONDITIONS
                else ""
            ),
            "targetDigest": (
                target_digest
                if re.fullmatch(r"[0-9a-f]{64}", target_digest)
                else ""
            ),
            "reasonCode": _safe_code(
                reason_code,
                fallback="ui_action_unknown",
            ),
            "executed": executed,
            "verified": verified,
        }
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
            with self._event_path(timestamp).open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True)
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            return True
        except OSError:
            return False

    def _status_payload(self) -> dict[str, Any]:
        return {
            "schema": UI_ACTION_STATUS_SCHEMA,
            "state": self._state,
            "updatedAt": self.now(),
            "processNonce": self.process_nonce,
            "auditReady": self._audit_ready,
            "activePreviewCount": sum(
                1
                for item in self._tokens.values()
                if not item.get("used")
                and float(item.get("expiresAt") or 0.0) > self.now()
            ),
            "pendingExecutionCount": len(self._pending),
            "discoveryCount": self._discovery_count,
            "previewCount": self._preview_count,
            "executionCount": self._execution_count,
            "verifiedCount": self._verified_count,
            "deniedCount": self._denied_count,
            "policy": {
                "allowedActions": sorted(UI_ACTION_ALLOWED_ACTIONS),
                "allowedControlTypes": sorted(
                    UI_ACTION_ALLOWED_CONTROL_TYPES
                ),
                "allowedPostconditions": sorted(
                    UI_ACTION_ALLOWED_POSTCONDITIONS
                ),
                "previewTtlSec": self.preview_ttl_sec,
                "observationMaxAgeSec": UI_ACTION_OBSERVATION_MAX_AGE_SEC,
                "restoredAfterRestart": False,
                "arbitraryCoordinates": False,
                "arbitraryCommands": False,
                "automaticRetry": False,
                "targetTextPersisted": False,
            },
        }

    def _write_status(self) -> None:
        try:
            atomic_json_write(self.status_path, self._status_payload())
        except OSError:
            self._audit_ready = False
            self._state = "authorization_audit_unavailable"

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._prune_tokens(self.now())
            self._write_status()
            return self._status_payload()

    def _prune_tokens(self, now: float) -> None:
        self._tokens = {
            token: item
            for token, item in self._tokens.items()
            if (
                (
                    not item.get("used")
                    and now
                    <= float(item.get("expiresAt") or 0.0)
                    + UI_ACTION_TOKEN_RECORD_RETENTION_SEC
                )
                or (
                    item.get("used")
                    and now
                    <= float(item.get("usedAt") or 0.0)
                    + UI_ACTION_TOKEN_RECORD_RETENTION_SEC
                )
            )
        }

    def _fail_closed_for_audit(self) -> None:
        self._audit_ready = False
        self._state = "authorization_audit_unavailable"
        self._tokens.clear()
        self._pending.clear()
        self._write_status()

    def _deny(self, error: str) -> dict[str, Any]:
        self._denied_count += 1
        if self._audit_ready and not self._append_event(
            event="action_denied",
            reason_code=error,
            executed=False,
            verified=False,
        ):
            self._fail_closed_for_audit()
            return {
                "ok": False,
                "error": "ui_action_authorization_audit_unavailable",
            }
        self._write_status()
        return {"ok": False, "error": error}

    def preview(
        self,
        *,
        observation: dict[str, Any],
        element_id: str,
        action: str,
        postcondition: str,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._audit_ready:
                return self._deny("ui_action_authorization_audit_unavailable")
            if action not in UI_ACTION_ALLOWED_ACTIONS:
                return self._deny("ui_action_not_allowed")
            if postcondition not in UI_ACTION_ALLOWED_POSTCONDITIONS:
                return self._deny("ui_action_postcondition_not_allowed")
            if not _ELEMENT_ID_RE.fullmatch(str(element_id or "")):
                return self._deny("ui_action_element_id_invalid")
            now = self.now()
            self._prune_tokens(now)
            normalized, error = normalize_ui_action_observation(
                observation,
                now=now,
            )
            if normalized is None:
                return self._deny(error)
            target, error = _find_target(normalized, element_id)
            if target is None:
                return self._deny(error)
            fingerprint = _target_fingerprint(normalized, target)
            token = secrets.token_urlsafe(32)
            expires_at = now + self.preview_ttl_sec
            self._tokens[token] = {
                "issuedAt": now,
                "expiresAt": expires_at,
                "used": False,
                "action": action,
                "postcondition": postcondition,
                "elementId": element_id,
                "windowDigest": normalized["windowDigest"],
                "targetDigest": fingerprint,
            }
            if not self._append_event(
                event="preview_issued",
                action=action,
                postcondition=postcondition,
                target_digest=fingerprint,
                reason_code="explicit_preview",
                executed=False,
                verified=False,
            ):
                self._fail_closed_for_audit()
                return {
                    "ok": False,
                    "error": "ui_action_authorization_audit_unavailable",
                }
            self._preview_count += 1
            self._state = "confirmation_required"
            self._write_status()
            return {
                "ok": True,
                "schema": UI_ACTION_PREVIEW_SCHEMA,
                "confirmToken": token,
                "expiresAt": expires_at,
                "requiresExplicitConfirmation": True,
                "action": action,
                "postcondition": postcondition,
                "target": {
                    "elementId": target["elementId"],
                    "name": target["name"],
                    "controlType": target["controlType"],
                    "windowTitle": normalized["windowTitle"],
                    "windowClass": normalized["windowClass"],
                },
                "policy": {
                    "reobserveBeforeExecute": True,
                    "verifyAfterExecute": True,
                    "automaticRetry": False,
                    "arbitraryCoordinates": False,
                },
            }

    def discover(
        self,
        *,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        """Return bounded, enabled Button choices without granting authority."""
        with self._lock:
            if not self._audit_ready:
                return self._deny("ui_action_authorization_audit_unavailable")
            normalized, error = normalize_ui_action_observation(
                observation,
                now=self.now(),
            )
            if normalized is None:
                return self._deny(error)
            eligible = [
                {
                    "elementId": item["elementId"],
                    "name": item["name"],
                    "controlType": item["controlType"],
                    "isEnabled": True,
                }
                for item in normalized["elements"]
                if item["isEnabled"]
            ]
            truncated = bool(
                normalized["truncated"]
                or len(eligible) > UI_ACTION_DISCOVERY_MAX_TARGETS
            )
            targets = eligible[:UI_ACTION_DISCOVERY_MAX_TARGETS]
            if not self._append_event(
                event="targets_observed",
                reason_code="explicit_target_discovery",
                executed=False,
                verified=False,
            ):
                self._fail_closed_for_audit()
                return {
                    "ok": False,
                    "error": "ui_action_authorization_audit_unavailable",
                }
            self._discovery_count += 1
            self._write_status()
            return {
                "ok": True,
                "schema": UI_ACTION_TARGETS_SCHEMA,
                "observedAt": normalized["capturedAt"],
                "window": {
                    "title": normalized["windowTitle"],
                    "className": normalized["windowClass"],
                },
                "targets": targets,
                "truncated": truncated,
                "policy": {
                    "action": "invoke",
                    "requiresPreview": True,
                    "requiresExplicitConfirmation": True,
                    "automaticRetry": False,
                },
            }

    def begin_apply(
        self,
        *,
        confirm_token: str,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            if not self._audit_ready:
                return self._deny("ui_action_authorization_audit_unavailable")
            token = str(confirm_token or "")
            token_state = self._tokens.get(token)
            if token_state is None:
                return self._deny("ui_action_confirm_token_invalid")
            if token_state.get("used"):
                return self._deny("ui_action_confirm_token_reused")
            now = self.now()
            token_state["used"] = True
            token_state["usedAt"] = now
            if now > float(token_state.get("expiresAt") or 0.0):
                return self._deny("ui_action_confirm_token_expired")
            normalized, error = normalize_ui_action_observation(
                observation,
                now=now,
            )
            if normalized is None:
                return self._deny(error)
            if normalized["windowDigest"] != token_state["windowDigest"]:
                return self._deny("ui_action_foreground_changed_since_preview")
            target, error = _find_target(
                normalized,
                str(token_state["elementId"]),
            )
            if target is None:
                return self._deny(error)
            fingerprint = _target_fingerprint(normalized, target)
            if fingerprint != token_state["targetDigest"]:
                return self._deny("ui_action_target_changed_since_preview")
            operation_id = f"ui-action-{secrets.token_hex(12)}"
            if not self._append_event(
                event="execution_started",
                operation_id=operation_id,
                action=str(token_state["action"]),
                postcondition=str(token_state["postcondition"]),
                target_digest=fingerprint,
                reason_code="explicit_confirmation",
                executed=False,
                verified=False,
            ):
                self._fail_closed_for_audit()
                return {
                    "ok": False,
                    "error": "ui_action_authorization_audit_unavailable",
                }
            self._pending[operation_id] = {
                **token_state,
                "operationId": operation_id,
            }
            self._state = "executing"
            self._write_status()
            return {
                "ok": True,
                "schema": UI_ACTION_EXECUTION_SCHEMA,
                "operationId": operation_id,
                "execution": {
                    "action": token_state["action"],
                    "elementId": token_state["elementId"],
                    "windowDigest": token_state["windowDigest"],
                },
            }

    def finish_apply(
        self,
        *,
        operation_id: str,
        execution_result: dict[str, Any],
        post_observation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(str(operation_id or ""), None)
            if pending is None:
                return self._deny("ui_action_execution_not_pending")
            self._execution_count += 1
            expected = {
                "action": pending["action"],
                "elementId": pending["elementId"],
                "windowDigest": pending["windowDigest"],
            }
            completed_at = (
                _finite_number(execution_result.get("completedAt"))
                if isinstance(execution_result, dict)
                else None
            )
            execution_error_code = (
                str(execution_result.get("errorCode") or "")
                if isinstance(execution_result, dict)
                else ""
            )
            now = self.now()
            contract_valid = bool(
                isinstance(execution_result, dict)
                and set(execution_result) == _EXECUTION_RESULT_KEYS
                and execution_result.get("schema")
                == "windows_ui_action.result.v1"
                and execution_result.get("action") == expected["action"]
                and execution_result.get("elementId")
                == expected["elementId"]
                and execution_result.get("windowDigest")
                == expected["windowDigest"]
                and type(execution_result.get("ok")) is bool
                and type(execution_result.get("executed")) is bool
                and execution_result.get("ok")
                is execution_result.get("executed")
                and completed_at is not None
                and completed_at <= now + UI_ACTION_FUTURE_TOLERANCE_SEC
                and now - completed_at
                <= UI_ACTION_OBSERVATION_MAX_AGE_SEC
                and (
                    (
                        execution_result.get("ok") is True
                        and not execution_error_code
                    )
                    or (
                        execution_result.get("ok") is False
                        and _SAFE_CODE_RE.fullmatch(execution_error_code)
                        is not None
                    )
                )
            )
            executed = bool(
                contract_valid
                and execution_result.get("ok")
                and execution_result.get("executed")
            )
            verified = False
            reason_code = "ui_action_executor_contract_invalid"
            if contract_valid and not executed:
                reason_code = _safe_code(
                    execution_error_code,
                    fallback="ui_action_execution_failed",
                )
            elif executed:
                normalized, observation_error = normalize_ui_action_observation(
                    post_observation,
                    now=self.now(),
                )
                if normalized is None:
                    reason_code = observation_error
                else:
                    matches = [
                        item
                        for item in normalized["elements"]
                        if item["elementId"] == pending["elementId"]
                    ]
                    postcondition = pending["postcondition"]
                    if postcondition == "target_absent":
                        verified = not matches
                    elif postcondition == "target_disabled":
                        verified = len(matches) == 1 and not matches[0]["isEnabled"]
                    elif postcondition == "window_changed":
                        verified = (
                            normalized["windowDigest"]
                            != pending["windowDigest"]
                        )
                    reason_code = (
                        "ui_action_outcome_verified"
                        if verified
                        else "ui_action_outcome_unverified"
                    )
            if not self._append_event(
                event="execution_finished",
                operation_id=operation_id,
                action=str(pending["action"]),
                postcondition=str(pending["postcondition"]),
                target_digest=str(pending["targetDigest"]),
                reason_code=reason_code,
                executed=executed,
                verified=verified,
            ):
                self._fail_closed_for_audit()
                return {
                    "ok": False,
                    "schema": UI_ACTION_RESULT_SCHEMA,
                    "state": "authorization_audit_unavailable",
                    "error": "ui_action_authorization_audit_unavailable",
                    "operationId": operation_id,
                    "action": pending["action"],
                    "postcondition": pending["postcondition"],
                    "executed": executed,
                    "verified": False,
                    "automaticRetry": False,
                }
            if verified:
                self._verified_count += 1
                self._state = "ready"
            elif executed:
                self._state = "outcome_unverified"
            else:
                self._state = "execution_failed"
            self._write_status()
            return {
                "ok": verified,
                "schema": UI_ACTION_RESULT_SCHEMA,
                "state": (
                    "verified"
                    if verified
                    else (
                        "outcome_unverified"
                        if executed
                        else "execution_failed"
                    )
                ),
                "error": "" if verified else reason_code,
                "operationId": operation_id,
                "action": pending["action"],
                "postcondition": pending["postcondition"],
                "executed": executed,
                "verified": verified,
                "automaticRetry": False,
            }


__all__ = [
    "UI_ACTION_ALLOWED_ACTIONS",
    "UI_ACTION_ALLOWED_CONTROL_TYPES",
    "UI_ACTION_ALLOWED_POSTCONDITIONS",
    "UI_ACTION_DISCOVERY_MAX_TARGETS",
    "UI_ACTION_EVENT_SCHEMA",
    "UI_ACTION_EXECUTION_SCHEMA",
    "UI_ACTION_FUTURE_TOLERANCE_SEC",
    "UI_ACTION_OBSERVATION_MAX_AGE_SEC",
    "UI_ACTION_PREVIEW_SCHEMA",
    "UI_ACTION_PREVIEW_TTL_SEC",
    "UI_ACTION_TOKEN_RECORD_RETENTION_SEC",
    "UI_ACTION_RESULT_SCHEMA",
    "UI_ACTION_STATUS_SCHEMA",
    "UI_ACTION_TARGETS_SCHEMA",
    "UiActionTargetManager",
    "normalize_ui_action_observation",
]
