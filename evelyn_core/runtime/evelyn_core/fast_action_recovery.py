from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .runtime_artifact_io import atomic_json_write
from .text import clean_text


FAST_ACTION_RECOVERY_SCHEMA = (
    "fast_control.action-recovery.v1"
)
FAST_ACTION_RECOVERY_NOTICE = (
    "재시작 전에 시작한 작업의 최종 결과를 확인할 수 없었어. "
    "중복 실행을 피하려고 자동으로 다시 시도하지 않았어."
)
DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS = 40
DEFAULT_FAST_ACTION_RECOVERY_MAX_BYTES = 128 * 1024
_ACTION_ID_PATTERN = re.compile(r"^fast-action-[1-9][0-9]{0,11}$")
_ACTION_STATES = frozenset(
    {"running", "terminal_committing"}
)


def _finite_nonnegative(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("fast_action_timestamp_invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "fast_action_timestamp_invalid"
        ) from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError("fast_action_timestamp_invalid")
    return parsed


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("fast_action_generation_invalid")
    if value < 0:
        raise ValueError("fast_action_generation_invalid")
    return value


def _action_id(value: Any) -> str:
    candidate = clean_text(value)
    if not _ACTION_ID_PATTERN.fullmatch(candidate):
        raise ValueError("fast_action_id_invalid")
    return candidate


class FastActionRecoveryJournal:
    """Durably mark background work without persisting prompts or replies."""

    def __init__(
        self,
        *,
        path: Path,
        enabled: bool,
        wall_time: Callable[[], float] = time.time,
        max_actions: int = (
            DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS
        ),
    ) -> None:
        self.path = Path(path)
        self.enabled = bool(enabled)
        self.wall_time = wall_time
        self.max_actions = max(
            1,
            min(
                DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS,
                int(max_actions),
            ),
        )
        self._lock = threading.RLock()
        self._actions: dict[str, dict[str, Any]] = {}
        self._last_recovery_at = 0.0
        self._last_recovery_count = 0
        self._last_error_code = ""
        self._load_state = (
            "disabled" if not self.enabled else "ready"
        )
        if self.enabled:
            self._load()

    def _empty_payload(self) -> dict[str, Any]:
        return {
            "schema": FAST_ACTION_RECOVERY_SCHEMA,
            "updatedAt": max(
                0.0,
                float(self.wall_time()),
            ),
            "actions": [],
            "lastRecoveryAt": self._last_recovery_at,
            "lastRecoveryCount": (
                self._last_recovery_count
            ),
            "lastErrorCode": self._last_error_code,
            "policy": {
                "contentFree": True,
                "rawText": False,
                "automaticRetry": False,
                "maxActions": self.max_actions,
            },
        }

    def _payload(self) -> dict[str, Any]:
        payload = self._empty_payload()
        payload["actions"] = [
            dict(entry)
            for entry in self._actions.values()
        ]
        return payload

    def _validated_payload(
        self,
        payload: Any,
    ) -> tuple[
        dict[str, dict[str, Any]],
        float,
        int,
        str,
    ]:
        if not isinstance(payload, dict):
            raise ValueError("fast_action_journal_invalid")
        expected_keys = {
            "schema",
            "updatedAt",
            "actions",
            "lastRecoveryAt",
            "lastRecoveryCount",
            "lastErrorCode",
            "policy",
        }
        policy = payload.get("policy")
        actions = payload.get("actions")
        if (
            set(payload) != expected_keys
            or payload.get("schema")
            != FAST_ACTION_RECOVERY_SCHEMA
            or not isinstance(policy, dict)
            or set(policy)
            != {
                "contentFree",
                "rawText",
                "automaticRetry",
                "maxActions",
            }
            or policy.get("contentFree") is not True
            or policy.get("rawText") is not False
            or policy.get("automaticRetry") is not False
            or policy.get("maxActions") != self.max_actions
            or not isinstance(actions, list)
            or len(actions) > self.max_actions
        ):
            raise ValueError("fast_action_journal_invalid")
        _finite_nonnegative(payload.get("updatedAt"))
        last_recovery_at = _finite_nonnegative(
            payload.get("lastRecoveryAt")
        )
        last_recovery_count = _nonnegative_int(
            payload.get("lastRecoveryCount")
        )
        last_error_code = clean_text(
            payload.get("lastErrorCode")
        )
        if last_error_code not in {
            "",
            "fast_action_recovery_interrupted",
            "fast_action_recovery_journal_corrupt",
            "fast_action_recovery_write_failed",
        }:
            raise ValueError("fast_action_error_code_invalid")
        validated: dict[str, dict[str, Any]] = {}
        for raw_entry in actions:
            if (
                not isinstance(raw_entry, dict)
                or set(raw_entry)
                != {
                    "actionId",
                    "state",
                    "startedAt",
                    "expectedGeneration",
                }
            ):
                raise ValueError("fast_action_entry_invalid")
            action_id = _action_id(
                raw_entry.get("actionId")
            )
            state = clean_text(raw_entry.get("state"))
            started_at = _finite_nonnegative(
                raw_entry.get("startedAt")
            )
            expected_generation = _nonnegative_int(
                raw_entry.get("expectedGeneration")
            )
            if (
                state not in _ACTION_STATES
                or (
                    state == "running"
                    and expected_generation != 0
                )
                or (
                    state == "terminal_committing"
                    and expected_generation < 1
                )
                or action_id in validated
            ):
                raise ValueError("fast_action_entry_invalid")
            validated[action_id] = {
                "actionId": action_id,
                "state": state,
                "startedAt": started_at,
                "expectedGeneration": expected_generation,
            }
        return (
            validated,
            last_recovery_at,
            last_recovery_count,
            last_error_code,
        )

    def _load(self) -> None:
        if not self.path.exists() and not self.path.is_symlink():
            return
        try:
            if (
                self.path.is_symlink()
                or not self.path.is_file()
                or self.path.stat().st_size
                > DEFAULT_FAST_ACTION_RECOVERY_MAX_BYTES
            ):
                raise ValueError(
                    "fast_action_journal_invalid"
                )
            payload = json.loads(
                self.path.read_text(encoding="utf-8")
            )
            (
                self._actions,
                self._last_recovery_at,
                self._last_recovery_count,
                self._last_error_code,
            ) = self._validated_payload(payload)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            self._actions = {}
            self._load_state = "corrupt"
            self._last_error_code = (
                "fast_action_recovery_journal_corrupt"
            )

    def _write(self) -> None:
        try:
            atomic_json_write(
                self.path,
                self._payload(),
                durable=True,
            )
            self._load_state = "ready"
        except Exception:
            self._load_state = "error"
            self._last_error_code = (
                "fast_action_recovery_write_failed"
            )
            raise

    def begin(self, action_id: str) -> dict[str, Any]:
        if not self.enabled:
            return self.public_status()
        validated_id = _action_id(action_id)
        with self._lock:
            if self._load_state in {"corrupt", "error"}:
                raise RuntimeError(
                    "fast_action_recovery_unavailable"
                )
            if validated_id in self._actions:
                raise ValueError("fast_action_already_exists")
            if len(self._actions) >= self.max_actions:
                raise RuntimeError(
                    "fast_action_recovery_capacity"
                )
            self._actions[validated_id] = {
                "actionId": validated_id,
                "state": "running",
                "startedAt": max(
                    0.0,
                    float(self.wall_time()),
                ),
                "expectedGeneration": 0,
            }
            try:
                self._write()
            except Exception:
                self._actions.pop(validated_id, None)
                raise
            return self.public_status()

    def prepare_terminal(
        self,
        action_id: str,
        *,
        expected_generation: int,
    ) -> dict[str, Any]:
        if not self.enabled:
            return self.public_status()
        validated_id = _action_id(action_id)
        generation = _nonnegative_int(
            expected_generation
        )
        if generation < 1:
            raise ValueError("fast_action_generation_invalid")
        with self._lock:
            entry = self._actions.get(validated_id)
            if entry is None:
                raise KeyError("fast_action_recovery_missing")
            previous = dict(entry)
            entry["state"] = "terminal_committing"
            entry["expectedGeneration"] = generation
            try:
                self._write()
            except Exception:
                self._actions[validated_id] = previous
                raise
            return self.public_status()

    def finish(self, action_id: str) -> dict[str, Any]:
        if not self.enabled:
            return self.public_status()
        validated_id = _action_id(action_id)
        with self._lock:
            previous = self._actions.pop(
                validated_id,
                None,
            )
            if previous is None:
                return self.public_status()
            try:
                self._write()
            except Exception:
                self._actions[validated_id] = previous
                raise
            return self.public_status()

    def mark_interrupted(
        self,
        action_id: str,
    ) -> dict[str, Any]:
        if not self.enabled:
            return self.public_status()
        validated_id = _action_id(action_id)
        with self._lock:
            entry = self._actions.get(validated_id)
            if entry is None:
                return self.public_status()
            previous = dict(entry)
            entry["state"] = "running"
            entry["expectedGeneration"] = 0
            try:
                self._write()
            except Exception:
                self._actions[validated_id] = previous
                raise
            return self.public_status()

    def continuity_commit_allowed(self) -> bool:
        with self._lock:
            return bool(
                not self.enabled
                or self._load_state == "ready"
            )

    def recovery_decision(
        self,
        *,
        continuity_generation: int,
        continuity_ready: bool = True,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {
                "state": "disabled",
                "pendingCount": 0,
                "noticeRequired": False,
                "reasonCode": "",
            }
        try:
            generation = _nonnegative_int(
                continuity_generation
            )
        except ValueError:
            generation = 0
        with self._lock:
            if self._load_state == "corrupt":
                return {
                    "state": "recovery_required",
                    "pendingCount": 1,
                    "noticeRequired": True,
                    "reasonCode": (
                        "fast_action_recovery_journal_corrupt"
                    ),
                }
            if self._load_state == "error":
                return {
                    "state": "unavailable",
                    "pendingCount": len(self._actions),
                    "noticeRequired": False,
                    "reasonCode": (
                        "fast_action_recovery_write_failed"
                    ),
                }
            if self._actions and continuity_ready is not True:
                return {
                    "state": "recovery_required",
                    "pendingCount": len(self._actions),
                    "noticeRequired": True,
                    "reasonCode": (
                        "fast_action_recovery_interrupted"
                    ),
                }
            unresolved = [
                entry
                for entry in self._actions.values()
                if (
                    entry["state"] == "running"
                    or int(entry["expectedGeneration"])
                    > generation
                )
            ]
            if unresolved:
                return {
                    "state": "recovery_required",
                    "pendingCount": len(unresolved),
                    "noticeRequired": True,
                    "reasonCode": (
                        "fast_action_recovery_interrupted"
                    ),
                }
            if self._actions:
                return {
                    "state": "delivery_verified",
                    "pendingCount": len(self._actions),
                    "noticeRequired": False,
                    "reasonCode": "",
                }
            return {
                "state": "idle",
                "pendingCount": 0,
                "noticeRequired": False,
                "reasonCode": "",
            }

    def acknowledge_recovery(
        self,
        *,
        recovered_count: int,
        error_code: str = "",
    ) -> dict[str, Any]:
        if not self.enabled:
            return self.public_status()
        count = max(0, int(recovered_count))
        code = clean_text(error_code)
        if code not in {
            "",
            "fast_action_recovery_interrupted",
            "fast_action_recovery_journal_corrupt",
        }:
            raise ValueError(
                "fast_action_recovery_code_invalid"
            )
        with self._lock:
            previous = (
                dict(self._actions),
                self._last_recovery_at,
                self._last_recovery_count,
                self._last_error_code,
                self._load_state,
            )
            self._actions = {}
            self._last_recovery_at = max(
                0.0,
                float(self.wall_time()),
            )
            self._last_recovery_count = count
            self._last_error_code = code
            self._load_state = "ready"
            try:
                self._write()
            except Exception:
                (
                    self._actions,
                    self._last_recovery_at,
                    self._last_recovery_count,
                    self._last_error_code,
                    self._load_state,
                ) = previous
                raise
            return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            if not self.enabled:
                state = "disabled"
            elif self._load_state in {"corrupt", "error"}:
                state = self._load_state
            elif self._actions:
                state = "pending"
            elif self._last_recovery_count:
                state = "recovered"
            else:
                state = "idle"
            return {
                "schema": FAST_ACTION_RECOVERY_SCHEMA,
                "enabled": self.enabled,
                "state": state,
                "pendingCount": len(self._actions),
                "lastRecoveryAt": (
                    self._last_recovery_at
                    if self._last_recovery_at > 0.0
                    else None
                ),
                "lastRecoveryCount": (
                    self._last_recovery_count
                ),
                "lastErrorCode": self._last_error_code,
                "policy": {
                    "contentFree": True,
                    "rawText": False,
                    "automaticRetry": False,
                    "maxActions": self.max_actions,
                },
            }


__all__ = [
    "DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS",
    "FAST_ACTION_RECOVERY_NOTICE",
    "FAST_ACTION_RECOVERY_SCHEMA",
    "FastActionRecoveryJournal",
]
