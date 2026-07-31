from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .continuity_authenticity import (
    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD,
    CONTINUITY_AUTH_ARTIFACT_FAST_ACTION_HEAD,
    ContinuityAuthenticity,
    ContinuityAuthenticityError,
)
from .runtime_artifact_io import atomic_json_write
from .text import clean_text


FAST_ACTION_RECOVERY_LEGACY_SCHEMA = (
    "fast_control.action-recovery.v1"
)
FAST_ACTION_RECOVERY_V2_SCHEMA = (
    "fast_control.action-recovery.v2"
)
FAST_ACTION_RECOVERY_SCHEMA = (
    "fast_control.action-recovery.v3"
)
FAST_ACTION_RECOVERY_HEAD_SCHEMA = (
    "fast_control.action-recovery-head.v1"
)
FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA = (
    "fast_control.action-recovery-head.v2"
)
FAST_ACTION_RECOVERY_CHAIN_GENESIS = "0" * 64
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


def _valid_sha256(value: Any) -> str:
    candidate = clean_text(value).lower()
    if re.fullmatch(r"[0-9a-f]{64}", candidate):
        return candidate
    return ""


def _journal_hash(payload: dict[str, Any]) -> str:
    material = {
        key: value
        for key, value in payload.items()
        if key != "journalHash"
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _legacy_journal_hash(raw_text: str) -> str:
    return hashlib.sha256(
        raw_text.encode("utf-8")
    ).hexdigest()


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
        head_path: Path | None = None,
        enabled: bool,
        wall_time: Callable[[], float] = time.time,
        max_actions: int = (
            DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS
        ),
        authenticity: ContinuityAuthenticity | None = None,
    ) -> None:
        self.path = Path(path)
        self.head_path = Path(
            head_path
            if head_path is not None
            else self.path.with_name(
                f"{self.path.stem}.head.json"
            )
        )
        self.enabled = bool(enabled)
        self.wall_time = wall_time
        self.max_actions = max(
            1,
            min(
                DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS,
                int(max_actions),
            ),
        )
        self.authenticity = (
            authenticity or ContinuityAuthenticity()
        )
        self._lock = threading.RLock()
        self._actions: dict[str, dict[str, Any]] = {}
        self._last_recovery_at = 0.0
        self._last_recovery_count = 0
        self._last_error_code = ""
        self._generation = 0
        self._journal_hash = (
            FAST_ACTION_RECOVERY_CHAIN_GENESIS
        )
        self._integrity = (
            "disabled" if not self.enabled else "uninitialized"
        )
        self._head_state = (
            "disabled" if not self.enabled else "missing"
        )
        self._head_authenticity = (
            "disabled" if not self.enabled else "missing"
        )
        self._anchor_state = (
            "disabled"
            if not self.enabled
            else (
                "missing"
                if self.authenticity.external_anchor_configured
                else "unconfigured"
            )
        )
        self._auth_error_code = ""
        self._load_state = (
            "disabled" if not self.enabled else "ready"
        )
        if self.enabled:
            self._load()

    def _now(self) -> float:
        return _finite_nonnegative(self.wall_time())

    def _empty_payload(
        self,
        *,
        generation: int,
        previous_hash: str,
    ) -> dict[str, Any]:
        return {
            "schema": FAST_ACTION_RECOVERY_SCHEMA,
            "generation": generation,
            "previousHash": previous_hash,
            "journalHash": "",
            "updatedAt": self._now(),
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

    def _payload(
        self,
        *,
        generation: int,
        previous_hash: str,
    ) -> dict[str, Any]:
        payload = self._empty_payload(
            generation=generation,
            previous_hash=previous_hash,
        )
        payload["actions"] = [
            {
                "actionId": entry["actionId"],
                "state": entry["state"],
                "startedAt": entry["startedAt"],
                "expectedGeneration": entry[
                    "expectedGeneration"
                ],
                "startedGeneration": (
                    entry["startedGeneration"]
                    if isinstance(
                        entry.get("startedGeneration"),
                        int,
                    )
                    else 0
                ),
            }
            for entry in self._actions.values()
        ]
        payload["journalHash"] = _journal_hash(payload)
        return payload

    def _validated_payload(
        self,
        payload: Any,
    ) -> tuple[
        dict[str, dict[str, Any]],
        float,
        int,
        str,
        int,
        str,
        str,
    ]:
        if not isinstance(payload, dict):
            raise ValueError("fast_action_journal_invalid")
        base_keys = {
            "schema",
            "updatedAt",
            "actions",
            "lastRecoveryAt",
            "lastRecoveryCount",
            "lastErrorCode",
            "policy",
        }
        schema = clean_text(payload.get("schema"))
        expected_keys = set(base_keys)
        if schema in {
            FAST_ACTION_RECOVERY_SCHEMA,
            FAST_ACTION_RECOVERY_V2_SCHEMA,
        }:
            expected_keys.update(
                {
                    "generation",
                    "previousHash",
                    "journalHash",
                }
            )
        policy = payload.get("policy")
        actions = payload.get("actions")
        if (
            set(payload) != expected_keys
            or schema
            not in {
                FAST_ACTION_RECOVERY_SCHEMA,
                FAST_ACTION_RECOVERY_LEGACY_SCHEMA,
                FAST_ACTION_RECOVERY_V2_SCHEMA,
            }
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
        generation = 0
        previous_hash = (
            FAST_ACTION_RECOVERY_CHAIN_GENESIS
        )
        journal_hash = ""
        if schema in {
            FAST_ACTION_RECOVERY_SCHEMA,
            FAST_ACTION_RECOVERY_V2_SCHEMA,
        }:
            generation = _nonnegative_int(
                payload.get("generation")
            )
            previous_hash = _valid_sha256(
                payload.get("previousHash")
            )
            journal_hash = _valid_sha256(
                payload.get("journalHash")
            )
            if (
                generation < 1
                or not previous_hash
                or not journal_hash
                or journal_hash != _journal_hash(payload)
            ):
                raise ValueError(
                    "fast_action_journal_integrity_failed"
                )
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
            expected_entry_keys = {
                "actionId",
                "state",
                "startedAt",
                "expectedGeneration",
            }
            if schema == FAST_ACTION_RECOVERY_SCHEMA:
                expected_entry_keys.add(
                    "startedGeneration"
                )
            if (
                not isinstance(raw_entry, dict)
                or set(raw_entry) != expected_entry_keys
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
            started_generation = (
                _nonnegative_int(
                    raw_entry.get("startedGeneration")
                )
                if schema == FAST_ACTION_RECOVERY_SCHEMA
                else None
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
                "startedGeneration": started_generation,
            }
        return (
            validated,
            last_recovery_at,
            last_recovery_count,
            last_error_code,
            generation,
            previous_hash,
            journal_hash,
        )

    def _load_head(self) -> dict[str, Any] | None:
        path = self.head_path
        if not path.exists() and not path.is_symlink():
            return None
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size
            > DEFAULT_FAST_ACTION_RECOVERY_MAX_BYTES
        ):
            raise ValueError("fast_action_head_invalid")
        payload = json.loads(
            path.read_text(encoding="utf-8")
        )
        schema = (
            clean_text(payload.get("schema"))
            if isinstance(payload, dict)
            else ""
        )
        base_keys = {
            "schema",
            "generation",
            "journalHash",
            "updatedAt",
            "contentFree",
        }
        expected_keys = set(base_keys)
        if schema == FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA:
            expected_keys.update(
                {
                    "authAlgorithm",
                    "authScope",
                    "authKeyId",
                    "authTag",
                }
            )
        if (
            not isinstance(payload, dict)
            or set(payload) != expected_keys
            or schema
            not in {
                FAST_ACTION_RECOVERY_HEAD_SCHEMA,
                FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA,
            }
            or payload.get("contentFree") is not True
        ):
            raise ValueError("fast_action_head_invalid")
        generation = _nonnegative_int(
            payload.get("generation")
        )
        journal_hash = _valid_sha256(
            payload.get("journalHash")
        )
        updated_at = _finite_nonnegative(
            payload.get("updatedAt")
        )
        if not journal_hash:
            raise ValueError("fast_action_head_invalid")
        if schema == FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA:
            self.authenticity.verify_scoped_artifact(
                payload,
                artifact_scope=(
                    CONTINUITY_AUTH_ARTIFACT_FAST_ACTION_HEAD
                ),
            )
            auth_state = "verified"
        elif self.authenticity.configured:
            if not self.authenticity.allow_unsigned_bootstrap:
                raise ContinuityAuthenticityError(
                    "continuity_auth_bootstrap_required"
                )
            auth_state = "bootstrap_required"
        else:
            auth_state = "unconfigured"
        return {
            "generation": generation,
            "journalHash": journal_hash,
            "updatedAt": updated_at,
            "authenticity": auth_state,
        }

    def _write_head(
        self,
        *,
        generation: int,
        journal_hash: str,
    ) -> None:
        payload = {
            "schema": (
                FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA
                if self.authenticity.configured
                else FAST_ACTION_RECOVERY_HEAD_SCHEMA
            ),
            "generation": generation,
            "journalHash": journal_hash,
            "updatedAt": self._now(),
            "contentFree": True,
        }
        payload = self.authenticity.sign_scoped_artifact(
            payload,
            artifact_scope=(
                CONTINUITY_AUTH_ARTIFACT_FAST_ACTION_HEAD
            ),
        )
        atomic_json_write(
            self.head_path,
            payload,
            durable=True,
        )
        self._head_authenticity = (
            "verified"
            if self.authenticity.configured
            else "unconfigured"
        )

    def _adopt_head_anchor(
        self,
        head: dict[str, Any] | None,
    ) -> None:
        if head is None:
            return
        self._generation = int(head["generation"])
        self._journal_hash = str(head["journalHash"])
        self._head_state = "orphaned"
        self._head_authenticity = str(
            head.get("authenticity") or "missing"
        )

    def _mark_load_failure(
        self,
        *,
        state: str,
        head: dict[str, Any] | None,
    ) -> None:
        self._actions = {}
        self._adopt_head_anchor(head)
        self._load_state = state
        self._integrity = "failed"
        self._last_error_code = (
            "fast_action_recovery_write_failed"
            if state == "error"
            else "fast_action_recovery_journal_corrupt"
        )

    def _mark_auth_failure(
        self,
        exc: ContinuityAuthenticityError,
        *,
        head: dict[str, Any] | None,
    ) -> None:
        self._actions = {}
        self._adopt_head_anchor(head)
        self._load_state = "auth_error"
        self._integrity = "failed"
        self._auth_error_code = exc.code
        self._head_authenticity = {
            "continuity_auth_bootstrap_required": (
                "bootstrap_required"
            ),
            "continuity_auth_key_required": "key_required",
        }.get(exc.code, "failed")
        if exc.code.startswith("continuity_anchor_"):
            self._anchor_state = {
                "continuity_anchor_bootstrap_required": (
                    "bootstrap_required"
                ),
                "continuity_anchor_replay_detected": (
                    "replay_detected"
                ),
            }.get(exc.code, "failed")

    def _load(self) -> None:
        head: dict[str, Any] | None = None
        try:
            head = self._load_head()
            path_missing = (
                not self.path.exists()
                and not self.path.is_symlink()
            )
            if path_missing:
                if head is not None:
                    raise ValueError(
                        "fast_action_journal_missing_after_head"
                    )
                self.authenticity.reconcile_external_anchor(
                    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD,
                    generation=0,
                    artifact_hash=(
                        FAST_ACTION_RECOVERY_CHAIN_GENESIS
                    ),
                    updated_at=self._now(),
                )
                self._anchor_state = (
                    "verified"
                    if self.authenticity.external_anchor_configured
                    else "unconfigured"
                )
                self._write()
                return
            if (
                self.path.is_symlink()
                or not self.path.is_file()
                or self.path.stat().st_size
                > DEFAULT_FAST_ACTION_RECOVERY_MAX_BYTES
            ):
                raise ValueError(
                    "fast_action_journal_invalid"
                )
            raw_text = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            (
                actions,
                last_recovery_at,
                last_recovery_count,
                last_error_code,
                generation,
                previous_hash,
                journal_hash,
            ) = self._validated_payload(payload)
            schema = clean_text(payload.get("schema"))
            if schema == FAST_ACTION_RECOVERY_LEGACY_SCHEMA:
                journal_hash = _legacy_journal_hash(raw_text)
                if head is None:
                    if (
                        self.authenticity.configured
                        and not self.authenticity.allow_unsigned_bootstrap
                    ):
                        raise ContinuityAuthenticityError(
                            "continuity_auth_bootstrap_required"
                        )
                    self._write_head(
                        generation=0,
                        journal_hash=journal_hash,
                    )
                elif (
                    int(head["generation"]) != 0
                    or str(head["journalHash"])
                    != journal_hash
                ):
                    raise ValueError(
                        "fast_action_legacy_head_mismatch"
                    )
                elif head.get("authenticity") == "bootstrap_required":
                    self._write_head(
                        generation=0,
                        journal_hash=journal_hash,
                    )
                generation = 0
                integrity = "legacy_anchored"
            else:
                if head is None:
                    if (
                        self.authenticity.configured
                        and not self.authenticity.allow_unsigned_bootstrap
                    ):
                        raise ContinuityAuthenticityError(
                            "continuity_auth_bootstrap_required"
                        )
                    if (
                        generation != 1
                        or previous_hash
                        != FAST_ACTION_RECOVERY_CHAIN_GENESIS
                    ):
                        raise ValueError(
                            "fast_action_head_missing"
                        )
                    self._write_head(
                        generation=generation,
                        journal_hash=journal_hash,
                    )
                elif (
                    generation == int(head["generation"])
                    and journal_hash
                    == str(head["journalHash"])
                ):
                    if head.get("authenticity") == "bootstrap_required":
                        self._write_head(
                            generation=generation,
                            journal_hash=journal_hash,
                        )
                elif (
                    generation
                    == int(head["generation"]) + 1
                    and previous_hash
                    == str(head["journalHash"])
                ):
                    self._write_head(
                        generation=generation,
                        journal_hash=journal_hash,
                    )
                else:
                    raise ValueError(
                        "fast_action_rollback_or_head_mismatch"
                    )
                integrity = "verified"
            self.authenticity.reconcile_external_anchor(
                CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD,
                generation=generation,
                artifact_hash=journal_hash,
                previous_hash=previous_hash,
                updated_at=self._now(),
            )
            self._anchor_state = (
                "verified"
                if self.authenticity.external_anchor_configured
                else "unconfigured"
            )
            self._actions = actions
            self._last_recovery_at = last_recovery_at
            self._last_recovery_count = last_recovery_count
            self._last_error_code = last_error_code
            self._generation = generation
            self._journal_hash = journal_hash
            self._integrity = integrity
            self._head_state = "current"
            self._load_state = "ready"
            self._auth_error_code = ""
            if self._head_authenticity == "missing":
                self._head_authenticity = (
                    str(head.get("authenticity") or "missing")
                    if head is not None
                    else (
                        "verified"
                        if self.authenticity.configured
                        else "unconfigured"
                    )
                )
        except ContinuityAuthenticityError as exc:
            self._mark_auth_failure(exc, head=head)
        except (
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            self._mark_load_failure(
                state="corrupt",
                head=head,
            )
        except OSError:
            self._mark_load_failure(
                state="error",
                head=head,
            )

    @staticmethod
    def _write_target_allowed(path: Path) -> bool:
        return bool(
            not path.is_symlink()
            and (
                not path.exists()
                or path.is_file()
            )
        )

    def _write(self) -> None:
        generation = self._generation + 1
        payload = self._payload(
            generation=generation,
            previous_hash=self._journal_hash,
        )
        journal_hash = str(payload["journalHash"])
        try:
            if not self._write_target_allowed(
                self.path
            ) or not self._write_target_allowed(
                self.head_path
            ):
                raise OSError(
                    "fast_action_recovery_target_rejected"
                )
            atomic_json_write(
                self.path,
                payload,
                durable=True,
            )
            self._write_head(
                generation=generation,
                journal_hash=journal_hash,
            )
            self.authenticity.commit_external_anchor(
                CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD,
                previous_generation=self._generation,
                previous_hash=self._journal_hash,
                generation=generation,
                artifact_hash=journal_hash,
                updated_at=self._now(),
            )
            self._generation = generation
            self._journal_hash = journal_hash
            self._integrity = "verified"
            self._head_state = "current"
            self._load_state = "ready"
            self._auth_error_code = ""
            self._anchor_state = (
                "verified"
                if self.authenticity.external_anchor_configured
                else "unconfigured"
            )
        except ContinuityAuthenticityError as exc:
            self._load_state = "auth_error"
            self._integrity = "failed"
            self._head_state = "write_failed"
            self._auth_error_code = exc.code
            self._last_error_code = exc.code
            self._anchor_state = {
                "continuity_anchor_bootstrap_required": (
                    "bootstrap_required"
                ),
                "continuity_anchor_replay_detected": (
                    "replay_detected"
                ),
            }.get(exc.code, "failed")
            raise
        except Exception:
            self._load_state = "error"
            self._integrity = "failed"
            self._head_state = "write_failed"
            self._last_error_code = (
                "fast_action_recovery_write_failed"
            )
            raise

    def begin(
        self,
        action_id: str,
        *,
        continuity_generation: int = 0,
    ) -> dict[str, Any]:
        if not self.enabled:
            return self.public_status()
        validated_id = _action_id(action_id)
        started_generation = _nonnegative_int(
            continuity_generation
        )
        with self._lock:
            if self._load_state in {
                "auth_error",
                "corrupt",
                "error",
            }:
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
                "startedAt": self._now(),
                "expectedGeneration": 0,
                "startedGeneration": started_generation,
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
                or (
                    self._load_state == "ready"
                    and self._head_state == "current"
                    and self._integrity
                    in {"legacy_anchored", "verified"}
                    and (
                        not self.authenticity.configured
                        or self._head_authenticity == "verified"
                    )
                    and (
                        not self.authenticity.external_anchor_configured
                        or self._anchor_state == "verified"
                    )
                )
            )

    def restored_notice_matches(
        self,
        *,
        continuity_generation: int,
    ) -> bool:
        try:
            generation = _nonnegative_int(
                continuity_generation
            )
        except ValueError:
            return False
        with self._lock:
            if (
                self._load_state != "ready"
                or not self._actions
            ):
                return False
            for entry in self._actions.values():
                started_generation = entry.get(
                    "startedGeneration"
                )
                if (
                    isinstance(started_generation, bool)
                    or not isinstance(started_generation, int)
                    or generation <= started_generation
                ):
                    return False
            return True

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
            if self._load_state == "auth_error":
                return {
                    "state": "unavailable",
                    "pendingCount": 0,
                    "noticeRequired": False,
                    "reasonCode": self._auth_error_code,
                }
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
            if self._load_state == "auth_error":
                raise RuntimeError(
                    "fast_action_recovery_auth_unavailable"
                )
            previous = (
                dict(self._actions),
                self._last_recovery_at,
                self._last_recovery_count,
                self._last_error_code,
                self._load_state,
            )
            self._actions = {}
            self._last_recovery_at = self._now()
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
                    _previous_load_state,
                ) = previous
                self._load_state = "error"
                self._integrity = "failed"
                self._head_state = "write_failed"
                self._last_error_code = (
                    "fast_action_recovery_write_failed"
                )
                raise
            return self.public_status()

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            if not self.enabled:
                state = "disabled"
            elif self._load_state in {
                "auth_error",
                "corrupt",
                "error",
            }:
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
                "integrity": self._integrity,
                "generation": self._generation,
                "headState": self._head_state,
                "headAuthenticity": self._head_authenticity,
                "anchorState": self._anchor_state,
                "keyedAuthenticity": (
                    self.authenticity.configured
                ),
                "externalAnchorConfigured": (
                    self.authenticity.external_anchor_configured
                ),
                "externalReplayProtected": bool(
                    self.authenticity.external_anchor_configured
                    and self._load_state == "ready"
                    and self._anchor_state == "verified"
                ),
                "tamperEvident": bool(
                    self.authenticity.configured
                    and self._load_state == "ready"
                    and self._head_state == "current"
                    and self._head_authenticity == "verified"
                    and self._integrity
                    in {"legacy_anchored", "verified"}
                    and (
                        not self.authenticity.external_anchor_configured
                        or self._anchor_state == "verified"
                    )
                ),
                "rollbackProtected": bool(
                    self.enabled
                    and self._load_state == "ready"
                    and self._head_state == "current"
                    and self._integrity
                    in {"legacy_anchored", "verified"}
                    and (
                        not self.authenticity.configured
                        or self._head_authenticity == "verified"
                    )
                    and (
                        not self.authenticity.external_anchor_configured
                        or self._anchor_state == "verified"
                    )
                ),
                "noticeCorrelationReady": bool(
                    not self._actions
                    or all(
                        isinstance(
                            entry.get("startedGeneration"),
                            int,
                        )
                        and not isinstance(
                            entry.get("startedGeneration"),
                            bool,
                        )
                        for entry in self._actions.values()
                    )
                ),
                "pendingCount": len(self._actions),
                "lastRecoveryAt": (
                    self._last_recovery_at
                    if self._last_recovery_at > 0.0
                    else None
                ),
                "lastRecoveryCount": (
                    self._last_recovery_count
                ),
                "lastErrorCode": (
                    self._auth_error_code
                    or self._last_error_code
                ),
                "policy": {
                    "contentFree": True,
                    "rawText": False,
                    "automaticRetry": False,
                    "maxActions": self.max_actions,
                },
            }


__all__ = [
    "DEFAULT_FAST_ACTION_RECOVERY_MAX_ACTIONS",
    "FAST_ACTION_RECOVERY_CHAIN_GENESIS",
    "FAST_ACTION_RECOVERY_AUTHENTICATED_HEAD_SCHEMA",
    "FAST_ACTION_RECOVERY_HEAD_SCHEMA",
    "FAST_ACTION_RECOVERY_LEGACY_SCHEMA",
    "FAST_ACTION_RECOVERY_NOTICE",
    "FAST_ACTION_RECOVERY_SCHEMA",
    "FAST_ACTION_RECOVERY_V2_SCHEMA",
    "FastActionRecoveryJournal",
]
