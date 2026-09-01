from __future__ import annotations

import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from .runtime_artifact_io import atomic_json_write


MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA = (
    "mindcraft_world_effect.status.v1"
)
MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA = (
    "mindcraft_world_effect.event.v1"
)
MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA = (
    "mindcraft_world_effect.binding.v1"
)
MINDCRAFT_WORLD_EFFECT_TELEMETRY_SCHEMA = (
    "mindcraft.postcondition-candidate.v1"
)
MINDCRAFT_WORLD_EFFECT_OBSERVATION_SCHEMA = (
    "mindcraft_world_effect.observation.v1"
)
MINDCRAFT_WORLD_EFFECT_ARCHIVE_EVENT_SCHEMA = (
    "conversation.archive.minecraft-result.v1"
)
MINDCRAFT_LIFECYCLE_ARCHIVE_EVENT_SCHEMA = (
    "conversation.archive.minecraft-lifecycle-result.v1"
)
_LIFECYCLE_OUTCOMES = {
    "connect": "minecraft_connected",
    "goal": "minecraft_goal_confirmed",
    "disconnect": "minecraft_stopped",
}

DEFAULT_TELEMETRY_MAX_AGE_SEC = 5.0
DEFAULT_STATUS_MAX_AGE_SEC = 15.0
DEFAULT_CLOCK_SKEW_SEC = 1.0

_ID_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9:_\-.]{0,127}\Z",
    re.ASCII,
)
_STATUS_STATES = frozenset(
    {
        "initializing",
        "idle",
        "arming",
        "armed",
        "verifying",
        "verified",
        "rejecting",
        "rejected",
        "disarming",
        "manual_intervention_required",
    }
)
_EVENTS = frozenset(
    {
        "process_started",
        "binding_armed",
        "telemetry_rejected",
        "effect_verified",
        "binding_disarmed",
        "audit_failed",
        "status_failed",
    }
)
_BINDING_KEYS = frozenset(
    {
        "schema",
        "goalRunId",
        "actionRunId",
        "actionKey",
        "contractCode",
        "leaseId",
        "leaseProcessNonce",
        "producerNonce",
        "candidateSequence",
        "contentFree",
    }
)
_CANDIDATE_KEYS = frozenset(
    {
        "schema",
        "goalRunId",
        "actionRunId",
        "actionKey",
        "contractCode",
        "leaseId",
        "leaseProcessNonce",
        "producerNonce",
        "candidateSequence",
        "executionSequence",
        "observedAt",
        "evidenceCode",
        "postconditionCode",
        "beforeSatisfied",
        "afterSatisfied",
        "autonomous",
        "relevant",
        "actionSucceeded",
        "worldChanged",
        "goalProgress",
        "predicateCompleted",
        "completionDelta",
        "blockedDelta",
        "contentFree",
    }
)
_IDENTITY_KEYS = (
    "goalRunId",
    "actionRunId",
    "actionKey",
    "contractCode",
    "leaseId",
    "leaseProcessNonce",
    "producerNonce",
)
MINDCRAFT_WORLD_EFFECT_EVIDENCE_CODE = (
    "mindcraft_explicit_postcondition_candidate"
)
_WORLD_EFFECT_CONTRACTS: dict[str, tuple[str, str]] = {
    "mindcraft_food_recovery.v1": (
        "minecraft:find_food_source",
        "food_reserve_ready",
    ),
}
_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "goal",
        "rawgoal",
        "goaltext",
        "command",
        "rawcommand",
        "result",
        "rawresult",
        "inventory",
        "position",
        "coordinates",
        "target",
        "predicate",
        "chat",
        "transcript",
        "rawtranscript",
    }
)
_SAFE_SENSITIVE_KEYS = frozenset(
    {"goalrunid", "goalprogress", "predicatecompleted"}
)

GuardCheck = Callable[[dict[str, Any]], tuple[bool, str]]
ArchiveSink = Callable[[dict[str, Any]], tuple[bool, str]]
ArchiveReadiness = Callable[[], tuple[bool, str]]


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = _normalized_key(key)
            if normalized not in _SAFE_SENSITIVE_KEYS and any(
                token in normalized for token in _FORBIDDEN_KEY_TOKENS
            ):
                return True
            if _contains_forbidden_key(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ValueError(code)
    return value


def _sequence(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("mindcraft_world_effect_sequence_invalid")
    return value


def _timestamp(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("mindcraft_world_effect_timestamp_invalid")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "mindcraft_world_effect_timestamp_invalid"
        ) from exc
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError("mindcraft_world_effect_timestamp_invalid")
    return parsed


def _boolean(value: Any, code: str) -> bool:
    if type(value) is not bool:
        raise ValueError(code)
    return value


def _error_code(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if _ID_PATTERN.fullmatch(text) else fallback


def _freshness_error(
    observed_at: float,
    *,
    now: float,
    max_age_sec: float,
    clock_skew_sec: float,
) -> str:
    if observed_at > now + clock_skew_sec:
        return "mindcraft_world_effect_telemetry_clock_invalid"
    if now - observed_at > max_age_sec:
        return "mindcraft_world_effect_telemetry_stale"
    return ""


def _validate_binding(
    value: Any,
    *,
    now: float,
    max_age_sec: float,
    clock_skew_sec: float,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != _BINDING_KEYS
        or value.get("schema") != MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA
        or value.get("contentFree") is not True
        or _contains_forbidden_key(value)
    ):
        raise ValueError("mindcraft_world_effect_binding_invalid")
    binding = {
        "schema": MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA,
        **{
            key: _identifier(
                value.get(key),
                f"mindcraft_world_effect_{_normalized_key(key)}_invalid",
            )
            for key in _IDENTITY_KEYS
        },
        "candidateSequence": _sequence(value.get("candidateSequence")),
        "contentFree": True,
    }
    if binding["candidateSequence"] < 1:
        raise ValueError("mindcraft_world_effect_sequence_invalid")
    contract = _WORLD_EFFECT_CONTRACTS.get(binding["contractCode"])
    if contract is None or contract[0] != binding["actionKey"]:
        raise ValueError("mindcraft_world_effect_contract_invalid")
    return binding


def _validate_candidate(
    value: Any,
    *,
    now: float,
    max_age_sec: float,
    clock_skew_sec: float,
) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != _CANDIDATE_KEYS
        or value.get("schema") != MINDCRAFT_WORLD_EFFECT_TELEMETRY_SCHEMA
        or value.get("contentFree") is not True
        or _contains_forbidden_key(value)
    ):
        raise ValueError("mindcraft_world_effect_candidate_invalid")
    candidate = {
        "schema": MINDCRAFT_WORLD_EFFECT_TELEMETRY_SCHEMA,
        **{
            key: _identifier(
                value.get(key),
                f"mindcraft_world_effect_{_normalized_key(key)}_invalid",
            )
            for key in _IDENTITY_KEYS
        },
        "candidateSequence": _sequence(value.get("candidateSequence")),
        "executionSequence": _sequence(value.get("executionSequence")),
        "observedAt": _timestamp(value.get("observedAt")),
        "evidenceCode": _identifier(
            value.get("evidenceCode"),
            "mindcraft_world_effect_evidencecode_invalid",
        ),
        "postconditionCode": _identifier(
            value.get("postconditionCode"),
            "mindcraft_world_effect_postconditioncode_invalid",
        ),
        "beforeSatisfied": _boolean(
            value.get("beforeSatisfied"),
            "mindcraft_world_effect_beforesatisfied_invalid",
        ),
        "afterSatisfied": _boolean(
            value.get("afterSatisfied"),
            "mindcraft_world_effect_aftersatisfied_invalid",
        ),
        "autonomous": _boolean(
            value.get("autonomous"),
            "mindcraft_world_effect_autonomous_invalid",
        ),
        "relevant": _boolean(
            value.get("relevant"),
            "mindcraft_world_effect_relevant_invalid",
        ),
        "actionSucceeded": _boolean(
            value.get("actionSucceeded"),
            "mindcraft_world_effect_actionsucceeded_invalid",
        ),
        "worldChanged": _boolean(
            value.get("worldChanged"),
            "mindcraft_world_effect_worldchanged_invalid",
        ),
        "goalProgress": _boolean(
            value.get("goalProgress"),
            "mindcraft_world_effect_goalprogress_invalid",
        ),
        "predicateCompleted": _boolean(
            value.get("predicateCompleted"),
            "mindcraft_world_effect_predicatecompleted_invalid",
        ),
        "completionDelta": _sequence(value.get("completionDelta")),
        "blockedDelta": _sequence(value.get("blockedDelta")),
        "contentFree": True,
    }
    freshness = _freshness_error(
        candidate["observedAt"],
        now=now,
        max_age_sec=max_age_sec,
        clock_skew_sec=clock_skew_sec,
    )
    if freshness:
        raise ValueError(freshness)
    return candidate


def _archive_event(
    *,
    binding: Mapping[str, Any],
    candidate: Mapping[str, Any],
    context: Any,
) -> dict[str, Any]:
    if not isinstance(context, dict):
        raise ValueError("mindcraft_world_effect_archive_context_invalid")
    guild_id = context.get("guildId")
    if isinstance(guild_id, bool) or not isinstance(guild_id, int) or guild_id <= 0:
        raise ValueError("mindcraft_world_effect_archive_context_invalid")
    for key in _IDENTITY_KEYS[:-1]:
        if key not in context or not hmac.compare_digest(
            str(context[key]), str(binding[key])
        ):
            raise ValueError("mindcraft_world_effect_archive_context_invalid")
    authorization_grant_id = str(context.get("authorizationGrantId") or "")
    if not _ID_PATTERN.fullmatch(authorization_grant_id):
        raise ValueError("mindcraft_world_effect_archive_context_invalid")
    action_run_id = str(binding["actionRunId"])
    execution_sequence = int(candidate["executionSequence"])
    parents = [authorization_grant_id]
    command_parents = context.get("parentRecordIds")
    if command_parents is not None:
        if not isinstance(command_parents, list) or len(command_parents) != 1:
            raise ValueError("mindcraft_world_effect_archive_context_invalid")
        command_parent = str(command_parents[0] or "")
        if not _ID_PATTERN.fullmatch(command_parent):
            raise ValueError("mindcraft_world_effect_archive_context_invalid")
        if command_parent not in parents:
            parents.append(command_parent)
    return {
        "schema": MINDCRAFT_WORLD_EFFECT_ARCHIVE_EVENT_SCHEMA,
        "eventType": "minecraft_result",
        "mode": "discord_shared",
        "surface": "minecraft",
        "recordType": "minecraft_result",
        "guildId": str(guild_id),
        "parentRecordIds": parents,
        "goalRunId": str(binding["goalRunId"]),
        "actionRunId": action_run_id,
        "actionKey": str(binding["actionKey"]),
        "contractCode": str(binding["contractCode"]),
        "candidateSequence": int(candidate["candidateSequence"]),
        "executionSequence": execution_sequence,
        "observedAt": float(candidate["observedAt"]),
        "evidenceCode": str(candidate["evidenceCode"]),
        "postconditionCode": str(candidate["postconditionCode"]),
        "verified": True,
        "succeeded": True,
        "worldChanged": True,
        "goalProgress": True,
        "idempotencyKey": (
            f"minecraft-result:{action_run_id}:{execution_sequence}"
        ),
        "contentFree": True,
    }


def _lifecycle_archive_event(
    *,
    guild_id: int,
    parent_record_ids: tuple[str, ...],
    operation: str,
    outcome_code: str,
    observed_at: float,
) -> dict[str, Any]:
    if isinstance(guild_id, bool) or not isinstance(guild_id, int) or guild_id <= 0:
        raise ValueError("mindcraft_lifecycle_archive_context_invalid")
    if (
        len(parent_record_ids) != 1
        or not _ID_PATTERN.fullmatch(parent_record_ids[0])
        or _LIFECYCLE_OUTCOMES.get(operation) != outcome_code
        or not math.isfinite(observed_at)
        or observed_at < 0
    ):
        raise ValueError("mindcraft_lifecycle_archive_context_invalid")
    parent = parent_record_ids[0]
    return {
        "schema": MINDCRAFT_LIFECYCLE_ARCHIVE_EVENT_SCHEMA,
        "eventType": "minecraft_result",
        "mode": "discord_shared",
        "surface": "minecraft",
        "recordType": "minecraft_result",
        "guildId": str(guild_id),
        "parentRecordIds": [parent],
        "operation": operation,
        "outcomeCode": outcome_code,
        "observedAt": observed_at,
        "verified": True,
        "succeeded": True,
        "idempotencyKey": (
            f"minecraft-lifecycle:{operation}:{parent}:{int(observed_at * 1_000_000)}"
        ),
        "contentFree": True,
    }


def _policy(*, telemetry_max_age_sec: float) -> dict[str, Any]:
    return {
        "contentFree": True,
        "restoredAfterRestart": False,
        "strictBinding": True,
        "strictSequence": True,
        "falseToTrueRequired": True,
        "guardedLeaseRequired": True,
        "functionalReadinessRequired": True,
        "telemetryMaxAgeSec": telemetry_max_age_sec,
        "eventFsync": True,
        "durableStatus": True,
        "sensitivePayloadStored": False,
        "recursiveKeyDenylist": True,
    }


def _empty_evidence() -> dict[str, Any]:
    return {
        "verified": False,
        "candidateSequence": None,
        "executionSequence": None,
        "observedAt": None,
        "evidenceCode": "",
        "postconditionCode": "",
        "autonomous": False,
        "relevant": False,
        "succeeded": False,
        "worldChanged": False,
        "goalProgress": False,
        "predicateCompleted": False,
        "beforeSatisfied": False,
        "afterSatisfied": False,
    }


def validate_mindcraft_world_effect_status(
    value: Any,
    *,
    now: float | None = None,
    max_age_sec: float = DEFAULT_STATUS_MAX_AGE_SEC,
) -> tuple[dict[str, Any] | None, str]:
    """Strictly validate the persisted content-free observer projection."""

    expected_keys = {
        "schema",
        "state",
        "updatedAt",
        "processNonce",
        "auditReady",
        "statusReady",
        "armed",
        "binding",
        "evidence",
        "lastCandidateSequence",
        "lastErrorCode",
        "policy",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema") != MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA
        or value.get("state") not in _STATUS_STATES
        or _contains_forbidden_key(value)
        or type(value.get("auditReady")) is not bool
        or type(value.get("statusReady")) is not bool
        or type(value.get("armed")) is not bool
        or not isinstance(value.get("binding"), dict)
        or not isinstance(value.get("evidence"), dict)
        or not isinstance(value.get("policy"), dict)
    ):
        return None, "mindcraft_world_effect_status_invalid"
    try:
        updated_at = _timestamp(value.get("updatedAt"))
        _identifier(
            value.get("processNonce"),
            "mindcraft_world_effect_status_invalid",
        )
        last_sequence = value.get("lastCandidateSequence")
        if last_sequence is not None:
            _sequence(last_sequence)
        last_error = str(value.get("lastErrorCode") or "")
        if last_error and not _ID_PATTERN.fullmatch(last_error):
            raise ValueError("mindcraft_world_effect_status_invalid")
    except ValueError:
        return None, "mindcraft_world_effect_status_invalid"
    policy = value["policy"]
    try:
        telemetry_max_age_sec = _timestamp(
            policy.get("telemetryMaxAgeSec")
        )
    except ValueError:
        return None, "mindcraft_world_effect_status_invalid"
    if telemetry_max_age_sec < 0.1:
        return None, "mindcraft_world_effect_status_invalid"
    expected_policy = _policy(
        telemetry_max_age_sec=telemetry_max_age_sec
    )
    if set(policy) != set(expected_policy) or policy != expected_policy:
        return None, "mindcraft_world_effect_status_invalid"
    binding = value["binding"]
    if binding and (
        set(binding) != {
            *_IDENTITY_KEYS,
            "expectedCandidateSequence",
        }
        or any(not _ID_PATTERN.fullmatch(str(binding.get(key) or "")) for key in _IDENTITY_KEYS)
    ):
        return None, "mindcraft_world_effect_status_invalid"
    if binding:
        try:
            if _sequence(binding.get("expectedCandidateSequence")) < 1:
                raise ValueError("mindcraft_world_effect_status_invalid")
        except ValueError:
            return None, "mindcraft_world_effect_status_invalid"
    evidence = value["evidence"]
    if set(evidence) != set(_empty_evidence()):
        return None, "mindcraft_world_effect_status_invalid"
    if any(
        type(evidence.get(key)) is not bool
        for key in (
            "verified",
            "autonomous",
            "relevant",
            "succeeded",
            "worldChanged",
            "goalProgress",
            "predicateCompleted",
            "beforeSatisfied",
            "afterSatisfied",
        )
    ):
        return None, "mindcraft_world_effect_status_invalid"
    try:
        if evidence.get("candidateSequence") is not None:
            _sequence(evidence.get("candidateSequence"))
        if evidence.get("executionSequence") is not None:
            _sequence(evidence.get("executionSequence"))
        if evidence.get("observedAt") is not None:
            _timestamp(evidence.get("observedAt"))
        for key in ("evidenceCode", "postconditionCode"):
            code = str(evidence.get(key) or "")
            if code and not _ID_PATTERN.fullmatch(code):
                raise ValueError("mindcraft_world_effect_status_invalid")
    except ValueError:
        return None, "mindcraft_world_effect_status_invalid"
    if value.get("armed") is not (value.get("state") == "armed"):
        return None, "mindcraft_world_effect_status_invalid"
    if value.get("armed") and not binding:
        return None, "mindcraft_world_effect_status_invalid"
    current = time.time() if now is None else float(now)
    if updated_at > current + DEFAULT_CLOCK_SKEW_SEC:
        return None, "mindcraft_world_effect_status_clock_invalid"
    if current - updated_at > max(1.0, float(max_age_sec)):
        return None, "mindcraft_world_effect_status_stale"
    if value.get("auditReady") is not True or value.get("statusReady") is not True:
        return None, "mindcraft_world_effect_observer_unavailable"
    return deepcopy(value), ""


def load_mindcraft_world_effect_status(
    path: Path,
    *,
    now: float | None = None,
    max_age_sec: float = DEFAULT_STATUS_MAX_AGE_SEC,
) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "mindcraft_world_effect_status_missing"
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None, "mindcraft_world_effect_status_invalid"
    return validate_mindcraft_world_effect_status(
        payload,
        now=now,
        max_age_sec=max_age_sec,
    )


class MindcraftWorldEffectProjector:
    """Durably project an exact, content-free Mindcraft world-effect edge."""

    def __init__(
        self,
        *,
        status_path: Path,
        events_dir: Path,
        validate_guarded_lease: GuardCheck,
        validate_readiness: GuardCheck,
        now: Callable[[], float] = time.time,
        telemetry_max_age_sec: float = DEFAULT_TELEMETRY_MAX_AGE_SEC,
        clock_skew_sec: float = DEFAULT_CLOCK_SKEW_SEC,
        archive_verified_effect: ArchiveSink | None = None,
        validate_archive_ready: ArchiveReadiness | None = None,
        archive_required: bool = False,
    ) -> None:
        self.status_path = Path(status_path)
        self.events_dir = Path(events_dir)
        self.validate_guarded_lease = validate_guarded_lease
        self.validate_readiness = validate_readiness
        self.now = now
        self.telemetry_max_age_sec = max(0.1, float(telemetry_max_age_sec))
        self.clock_skew_sec = max(0.0, float(clock_skew_sec))
        if archive_verified_effect is not None and not callable(
            archive_verified_effect
        ):
            raise TypeError("archive_verified_effect must be callable")
        self.archive_verified_effect = archive_verified_effect
        if validate_archive_ready is not None and not callable(
            validate_archive_ready
        ):
            raise TypeError("validate_archive_ready must be callable")
        self.validate_archive_ready = validate_archive_ready
        self.archive_required = bool(archive_required)
        self.process_nonce = secrets.token_hex(16)
        self._lock = threading.RLock()
        self._state = "initializing"
        self._audit_ready = False
        self._status_ready = False
        self._binding: dict[str, Any] | None = None
        self._evidence = _empty_evidence()
        self._last_sequence: int | None = None
        self._last_error_code = ""
        self._archive_faulted = False
        self._seen_bindings: set[tuple[str, ...]] = set()
        self.initialize()

    def configure_archive(
        self,
        callback: ArchiveSink | None,
        *,
        validate_ready: ArchiveReadiness | None = None,
        required: bool,
    ) -> None:
        """Configure the sole archive projection before admitting an action."""

        if callback is not None and not callable(callback):
            raise TypeError("archive callback must be callable")
        if validate_ready is not None and not callable(validate_ready):
            raise TypeError("archive readiness callback must be callable")
        with self._lock:
            if self._binding is not None:
                raise RuntimeError(
                    "mindcraft_world_effect_archive_config_busy"
                )
            self.archive_verified_effect = callback
            self.validate_archive_ready = validate_ready
            self.archive_required = bool(required)

    def archive_ready(self) -> bool:
        with self._lock:
            if self._archive_faulted:
                return False
            if not self.archive_required:
                return True
            if (
                self.archive_verified_effect is None
                or self.validate_archive_ready is None
            ):
                return False
            try:
                response = self.validate_archive_ready()
            except Exception:
                return False
            return bool(
                isinstance(response, tuple)
                and len(response) == 2
                and type(response[0]) is bool
                and isinstance(response[1], str)
                and response[0]
            )

    def archive_verified_lifecycle(
        self,
        *,
        guild_id: int,
        parent_record_ids: tuple[str, ...],
        operation: str,
        outcome_code: str,
    ) -> tuple[bool, str]:
        with self._lock:
            callback = self.archive_verified_effect
            if callback is None or not self.archive_ready():
                return False, "mindcraft_world_effect_archive_unavailable"
            try:
                event = _lifecycle_archive_event(
                    guild_id=guild_id,
                    parent_record_ids=parent_record_ids,
                    operation=operation,
                    outcome_code=outcome_code,
                    observed_at=self.now(),
                )
                response = callback(deepcopy(event))
            except Exception:
                response = None
            if (
                isinstance(response, tuple)
                and len(response) == 2
                and response[0] is True
                and isinstance(response[1], str)
            ):
                return True, ""
            self._archive_faulted = True
            self._last_error_code = (
                "mindcraft_world_effect_archive_unavailable"
            )
            self._write_status()
            return False, self._last_error_code

    def _binding_projection(self) -> dict[str, Any]:
        if self._binding is None:
            return {}
        return {
            **{key: self._binding[key] for key in _IDENTITY_KEYS},
            "expectedCandidateSequence": self._binding[
                "candidateSequence"
            ],
        }

    def _status_payload(self) -> dict[str, Any]:
        return {
            "schema": MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA,
            "state": self._state,
            "updatedAt": self.now(),
            "processNonce": self.process_nonce,
            "auditReady": self._audit_ready,
            "statusReady": self._status_ready,
            "armed": self._state == "armed" and self._binding is not None,
            "binding": self._binding_projection(),
            "evidence": deepcopy(self._evidence),
            "lastCandidateSequence": self._last_sequence,
            "lastErrorCode": self._last_error_code,
            "policy": _policy(
                telemetry_max_age_sec=self.telemetry_max_age_sec
            ),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_payload()

    def _write_status(self) -> bool:
        try:
            atomic_json_write(
                self.status_path,
                self._status_payload(),
                durable=True,
            )
            self._status_ready = True
            return True
        except (OSError, TypeError, ValueError):
            self._status_ready = False
            self._binding = None
            self._state = "manual_intervention_required"
            self._last_error_code = (
                "mindcraft_world_effect_status_write_failed"
            )
            return False

    def _append_event(
        self,
        event: str,
        *,
        binding: Mapping[str, Any] | None = None,
        candidate_sequence: int | None = None,
        execution_sequence: int | None = None,
        error_code: str = "",
        evidence_code: str = "",
        postcondition_code: str = "",
        flags: Mapping[str, bool] | None = None,
    ) -> bool:
        if event not in _EVENTS:
            return False
        source = binding or self._binding or {}
        record: dict[str, Any] = {
            "schema": MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA,
            "eventId": secrets.token_hex(12),
            "at": self.now(),
            "event": event,
            "processNonce": self.process_nonce,
            **{key: str(source.get(key) or "") for key in _IDENTITY_KEYS},
            "candidateSequence": candidate_sequence,
            "executionSequence": execution_sequence,
            "errorCode": _error_code(error_code, ""),
            "evidenceCode": _error_code(evidence_code, ""),
            "postconditionCode": _error_code(postcondition_code, ""),
            "autonomous": bool((flags or {}).get("autonomous")),
            "relevant": bool((flags or {}).get("relevant")),
            "succeeded": bool((flags or {}).get("succeeded")),
            "worldChanged": bool((flags or {}).get("worldChanged")),
            "goalProgress": bool((flags or {}).get("goalProgress")),
            "contentFree": True,
        }
        if _contains_forbidden_key(record):
            return False
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
            event_path = self.events_dir / time.strftime(
                "%Y%m%d.jsonl",
                time.gmtime(self.now()),
            )
            with event_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            self._audit_ready = True
            return True
        except (OSError, ValueError):
            self._audit_ready = False
            self._binding = None
            self._state = "manual_intervention_required"
            self._last_error_code = (
                "mindcraft_world_effect_audit_unavailable"
            )
            return False

    def _commit_phase(self, state: str) -> bool:
        self._state = state
        self._status_ready = False
        return self._write_status()

    def initialize(self) -> dict[str, Any]:
        with self._lock:
            self.process_nonce = secrets.token_hex(16)
            self._state = "initializing"
            self._audit_ready = False
            self._status_ready = False
            self._binding = None
            self._evidence = _empty_evidence()
            self._last_sequence = None
            self._last_error_code = ""
            self._archive_faulted = False
            self._seen_bindings.clear()
            # The first durable write fences any armed state from a prior
            # process before this process publishes an audit event.
            if not self._write_status():
                return self._operation(False, "mindcraft_world_effect_status_write_failed")
            if not self._append_event("process_started"):
                self._write_status()
                return self._operation(False, "mindcraft_world_effect_audit_unavailable")
            self._state = "idle"
            self._last_error_code = ""
            if not self._write_status():
                return self._operation(False, "mindcraft_world_effect_status_write_failed")
            return self._operation(True, "initialized")

    def _operation(
        self,
        ok: bool,
        code: str,
        *,
        accepted: bool = False,
        verified: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema": MINDCRAFT_WORLD_EFFECT_OBSERVATION_SCHEMA,
            "ok": bool(ok),
            "accepted": bool(accepted),
            "verified": bool(verified),
            "code": _error_code(code, "mindcraft_world_effect_failed"),
            "status": self._status_payload(),
        }

    def _guards(self, binding: dict[str, Any]) -> str:
        for callback, fallback in (
            (
                self.validate_guarded_lease,
                "mindcraft_world_effect_guarded_lease_unavailable",
            ),
            (
                self.validate_readiness,
                "mindcraft_world_effect_readiness_unavailable",
            ),
        ):
            try:
                response = callback(deepcopy(binding))
            except Exception:
                return fallback
            if (
                not isinstance(response, tuple)
                or len(response) != 2
                or type(response[0]) is not bool
            ):
                return fallback
            if not response[0]:
                return _error_code(response[1], fallback)
        return ""

    def active_guard_error(self) -> str:
        with self._lock:
            if self._binding is None or self._state != "armed":
                return "mindcraft_world_effect_not_armed"
            return self._guards(self._binding)

    @staticmethod
    def _binding_key(binding: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(binding[key]) for key in _IDENTITY_KEYS)

    def arm(self, binding: Any) -> dict[str, Any]:
        with self._lock:
            if not self._audit_ready or not self._status_ready:
                return self._operation(
                    False,
                    "mindcraft_world_effect_observer_unavailable",
                )
            if not self.archive_ready():
                return self._operation(
                    False,
                    "mindcraft_world_effect_archive_unavailable",
                )
            if self._binding is not None or self._state in {
                "armed",
                "arming",
                "verifying",
            }:
                return self._operation(
                    False,
                    "mindcraft_world_effect_already_armed",
                )
            try:
                validated = _validate_binding(
                    binding,
                    now=self.now(),
                    max_age_sec=self.telemetry_max_age_sec,
                    clock_skew_sec=self.clock_skew_sec,
                )
            except ValueError as exc:
                return self._operation(False, str(exc))
            binding_key = self._binding_key(validated)
            if binding_key in self._seen_bindings:
                return self._operation(
                    False,
                    "mindcraft_world_effect_binding_reused",
                )
            guard_error = self._guards(validated)
            if guard_error:
                return self._operation(False, guard_error)
            self._binding = validated
            self._evidence = _empty_evidence()
            self._last_sequence = validated["candidateSequence"] - 1
            self._last_error_code = ""
            if not self._commit_phase("arming"):
                return self._operation(
                    False,
                    "mindcraft_world_effect_status_write_failed",
                )
            if not self._append_event(
                "binding_armed",
                binding=validated,
                candidate_sequence=validated["candidateSequence"] - 1,
            ):
                self._write_status()
                return self._operation(
                    False,
                    "mindcraft_world_effect_audit_unavailable",
                )
            self._binding = validated
            self._state = "armed"
            self._seen_bindings.add(binding_key)
            if not self._write_status():
                return self._operation(
                    False,
                    "mindcraft_world_effect_status_write_failed",
                )
            return self._operation(True, "armed", accepted=True)

    def _reject_candidate(
        self,
        code: str,
        *,
        candidate: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        binding = self._binding
        candidate_sequence = (
            candidate.get("candidateSequence")
            if isinstance(candidate, Mapping)
            and isinstance(candidate.get("candidateSequence"), int)
            and not isinstance(candidate.get("candidateSequence"), bool)
            else None
        )
        execution_sequence = (
            candidate.get("executionSequence")
            if isinstance(candidate, Mapping)
            and isinstance(candidate.get("executionSequence"), int)
            and not isinstance(candidate.get("executionSequence"), bool)
            else None
        )
        self._last_error_code = _error_code(
            code,
            "mindcraft_world_effect_candidate_rejected",
        )
        if binding is not None:
            self._commit_phase("rejecting")
        if not self._append_event(
            "telemetry_rejected",
            binding=binding or candidate,
            candidate_sequence=candidate_sequence,
            execution_sequence=execution_sequence,
            error_code=self._last_error_code,
        ):
            self._write_status()
            return self._operation(
                False,
                "mindcraft_world_effect_audit_unavailable",
            )
        self._binding = None
        self._state = "rejected"
        if not self._write_status():
            return self._operation(
                False,
                "mindcraft_world_effect_status_write_failed",
            )
        return self._operation(False, self._last_error_code)

    def _project_verified_effect(
        self,
        *,
        binding: Mapping[str, Any],
        candidate: Mapping[str, Any],
        archive_context: Any,
    ) -> str:
        callback = self.archive_verified_effect
        if callback is None:
            return (
                "mindcraft_world_effect_archive_unavailable"
                if self.archive_required
                else ""
            )
        try:
            event = _archive_event(
                binding=binding,
                candidate=candidate,
                context=archive_context,
            )
            response = callback(deepcopy(event))
        except Exception:
            return "mindcraft_world_effect_archive_unavailable"
        if (
            not isinstance(response, tuple)
            or len(response) != 2
            or type(response[0]) is not bool
            or not isinstance(response[1], str)
        ):
            return "mindcraft_world_effect_archive_unavailable"
        if response[0]:
            return ""
        return _error_code(
            response[1],
            "mindcraft_world_effect_archive_unavailable",
        )

    def _archive_failure(
        self,
        code: str,
        *,
        binding: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._archive_faulted = True
        self._last_error_code = _error_code(
            code,
            "mindcraft_world_effect_archive_unavailable",
        )
        if not self._append_event(
            "audit_failed",
            binding=binding,
            candidate_sequence=int(candidate["candidateSequence"]),
            execution_sequence=int(candidate["executionSequence"]),
            error_code=self._last_error_code,
        ):
            self._write_status()
            return self._operation(
                False,
                "mindcraft_world_effect_audit_unavailable",
            )
        self._binding = None
        self._state = "manual_intervention_required"
        if not self._write_status():
            return self._operation(
                False,
                "mindcraft_world_effect_status_write_failed",
            )
        return self._operation(False, self._last_error_code)

    def observe(
        self,
        candidate: Any,
        *,
        archive_context: Any = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._binding is None or self._state != "armed":
                return self._operation(
                    False,
                    "mindcraft_world_effect_not_armed",
                )
            try:
                validated = _validate_candidate(
                    candidate,
                    now=self.now(),
                    max_age_sec=self.telemetry_max_age_sec,
                    clock_skew_sec=self.clock_skew_sec,
                )
            except ValueError as exc:
                return self._reject_candidate(str(exc))
            for key in _IDENTITY_KEYS:
                if not hmac.compare_digest(
                    str(validated[key]),
                    str(self._binding[key]),
                ):
                    return self._reject_candidate(
                        f"mindcraft_world_effect_{_normalized_key(key)}_mismatch",
                        candidate=validated,
                    )
            if (
                self._last_sequence is None
                or validated["candidateSequence"] <= self._last_sequence
            ):
                return self._reject_candidate(
                    "mindcraft_world_effect_sequence_out_of_order",
                    candidate=validated,
                )
            if validated["candidateSequence"] != self._last_sequence + 1:
                return self._reject_candidate(
                    "mindcraft_world_effect_sequence_gap",
                    candidate=validated,
                )
            guard_error = self._guards(self._binding)
            if guard_error:
                return self._reject_candidate(
                    guard_error,
                    candidate=validated,
                )
            contract = _WORLD_EFFECT_CONTRACTS.get(
                validated["contractCode"]
            )
            required_true = (
                "autonomous",
                "relevant",
                "actionSucceeded",
                "worldChanged",
                "goalProgress",
                "predicateCompleted",
                "afterSatisfied",
            )
            transition_valid = bool(
                validated["beforeSatisfied"] is False
                and all(validated[key] is True for key in required_true)
                and validated["completionDelta"] == 1
                and validated["blockedDelta"] == 0
                and validated["candidateSequence"] >= 1
                and validated["executionSequence"] >= 1
                and validated["evidenceCode"]
                == MINDCRAFT_WORLD_EFFECT_EVIDENCE_CODE
                and contract is not None
                and contract[0] == validated["actionKey"]
                and contract[1] == validated["postconditionCode"]
            )
            if not transition_valid:
                return self._reject_candidate(
                    "mindcraft_world_effect_transition_unproven",
                    candidate=validated,
                )
            self._last_sequence = validated["candidateSequence"]
            self._last_error_code = ""
            if not self._commit_phase("verifying"):
                return self._operation(
                    False,
                    "mindcraft_world_effect_status_write_failed",
                )
            archive_error = self._project_verified_effect(
                binding=self._binding,
                candidate=validated,
                archive_context=archive_context,
            )
            if archive_error:
                return self._archive_failure(
                    archive_error,
                    binding=self._binding,
                    candidate=validated,
                )
            if not self._append_event(
                "effect_verified",
                binding=self._binding,
                candidate_sequence=validated["candidateSequence"],
                execution_sequence=validated["executionSequence"],
                evidence_code=validated["evidenceCode"],
                postcondition_code=validated["postconditionCode"],
                flags={
                    "autonomous": validated["autonomous"],
                    "relevant": validated["relevant"],
                    "succeeded": validated["actionSucceeded"],
                    "worldChanged": validated["worldChanged"],
                    "goalProgress": validated["goalProgress"],
                },
            ):
                self._write_status()
                return self._operation(
                    False,
                    "mindcraft_world_effect_audit_unavailable",
                )
            self._evidence = {
                "verified": True,
                "candidateSequence": validated["candidateSequence"],
                "executionSequence": validated["executionSequence"],
                "observedAt": validated["observedAt"],
                "evidenceCode": validated["evidenceCode"],
                "postconditionCode": validated["postconditionCode"],
                "autonomous": True,
                "relevant": True,
                "succeeded": True,
                "worldChanged": True,
                "goalProgress": True,
                "predicateCompleted": True,
                "beforeSatisfied": False,
                "afterSatisfied": True,
            }
            self._binding = None
            self._state = "verified"
            if not self._write_status():
                return self._operation(
                    False,
                    "mindcraft_world_effect_status_write_failed",
                )
            return self._operation(
                True,
                "effect_verified",
                accepted=True,
                verified=True,
            )

    def disarm(self, reason: str = "manual_disarm") -> dict[str, Any]:
        with self._lock:
            if self._binding is None:
                return self._operation(
                    False,
                    "mindcraft_world_effect_not_armed",
                )
            safe_reason = _error_code(
                reason,
                "mindcraft_world_effect_manual_disarm",
            )
            binding = self._binding
            if not self._commit_phase("disarming"):
                return self._operation(
                    False,
                    "mindcraft_world_effect_status_write_failed",
                )
            if not self._append_event(
                "binding_disarmed",
                binding=binding,
                candidate_sequence=self._last_sequence,
                error_code=safe_reason,
            ):
                self._write_status()
                return self._operation(
                    False,
                    "mindcraft_world_effect_audit_unavailable",
                )
            self._binding = None
            self._state = "idle"
            self._last_error_code = safe_reason
            if not self._write_status():
                return self._operation(
                    False,
                    "mindcraft_world_effect_status_write_failed",
                )
            return self._operation(True, "disarmed", accepted=True)


__all__ = [
    "DEFAULT_STATUS_MAX_AGE_SEC",
    "DEFAULT_TELEMETRY_MAX_AGE_SEC",
    "MINDCRAFT_WORLD_EFFECT_BINDING_SCHEMA",
    "MINDCRAFT_WORLD_EFFECT_ARCHIVE_EVENT_SCHEMA",
    "MINDCRAFT_WORLD_EFFECT_EVENT_SCHEMA",
    "MINDCRAFT_WORLD_EFFECT_OBSERVATION_SCHEMA",
    "MINDCRAFT_WORLD_EFFECT_STATUS_SCHEMA",
    "MINDCRAFT_WORLD_EFFECT_TELEMETRY_SCHEMA",
    "ArchiveReadiness",
    "ArchiveSink",
    "MindcraftWorldEffectProjector",
    "load_mindcraft_world_effect_status",
    "validate_mindcraft_world_effect_status",
]
