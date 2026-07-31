from __future__ import annotations

from typing import Any

from .autonomy_authorization import SUPPORTED_AUTONOMY_ACTIONS


AUTONOMY_FAILURE_SCHEMA = "autonomy.failure.v1"
AUTONOMY_CYCLE_FAILED = "autonomy_cycle_failed"
AUTONOMY_EXECUTOR_OBSERVE_FAILED = "autonomy_executor_observe_failed"
AUTONOMY_EXECUTOR_EXECUTE_FAILED = "autonomy_executor_execute_failed"
AUTONOMY_FAILURE_CODES = frozenset(
    {
        AUTONOMY_CYCLE_FAILED,
        AUTONOMY_EXECUTOR_OBSERVE_FAILED,
        AUTONOMY_EXECUTOR_EXECUTE_FAILED,
    }
)
AUTONOMY_FAILURE_PHASES = frozenset({"cycle", "observe", "execute"})
AUTONOMY_FAILURE_DOMAINS = frozenset(
    action.partition(":")[0]
    for action in SUPPORTED_AUTONOMY_ACTIONS
)


def autonomy_failure_code(
    value: object,
    *,
    fallback: str = AUTONOMY_CYCLE_FAILED,
) -> str:
    safe_fallback = str(fallback or "").strip()
    if safe_fallback not in AUTONOMY_FAILURE_CODES:
        safe_fallback = AUTONOMY_CYCLE_FAILED
    candidate = str(value or "").strip()
    return (
        candidate
        if candidate in AUTONOMY_FAILURE_CODES
        else safe_fallback
    )


def autonomy_last_error(value: object) -> str:
    candidate = str(value or "").strip()
    return autonomy_failure_code(candidate) if candidate else ""


def autonomy_failure_payload(
    *,
    code: object,
    phase: object,
    domain: object = "",
    action: object = "",
) -> dict[str, Any]:
    safe_phase = str(phase or "").strip()
    if safe_phase not in AUTONOMY_FAILURE_PHASES:
        safe_phase = "cycle"
    safe_action = str(action or "").strip()
    if safe_action not in SUPPORTED_AUTONOMY_ACTIONS:
        safe_action = ""
    safe_domain = str(domain or "").strip()
    if safe_action:
        safe_domain = safe_action.partition(":")[0]
    elif safe_domain not in AUTONOMY_FAILURE_DOMAINS:
        safe_domain = "unknown"
    payload: dict[str, Any] = {
        "schema": AUTONOMY_FAILURE_SCHEMA,
        "code": autonomy_failure_code(code),
        "phase": safe_phase,
        "domain": safe_domain,
        "verified": False,
    }
    if safe_action:
        payload["action"] = safe_action
    return payload


def sanitize_autonomy_executor_errors(
    value: object,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, dict[str, Any]] = {}
    for raw_domain, raw_failure in value.items():
        domain = str(raw_domain or "").strip()
        safe_domain = (
            domain
            if domain in AUTONOMY_FAILURE_DOMAINS
            else "unknown"
        )
        sanitized[safe_domain] = autonomy_failure_payload(
            code=AUTONOMY_EXECUTOR_OBSERVE_FAILED,
            phase="observe",
            domain=safe_domain,
        )
    return sanitized


def sanitize_autonomy_observation(
    value: object,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized = dict(value)
    if "executor_errors" not in sanitized:
        return sanitized
    executor_errors = sanitize_autonomy_executor_errors(
        sanitized.get("executor_errors")
    )
    if executor_errors:
        sanitized["executor_errors"] = executor_errors
    else:
        sanitized.pop("executor_errors", None)
    return sanitized


__all__ = [
    "AUTONOMY_CYCLE_FAILED",
    "AUTONOMY_EXECUTOR_EXECUTE_FAILED",
    "AUTONOMY_EXECUTOR_OBSERVE_FAILED",
    "AUTONOMY_FAILURE_CODES",
    "AUTONOMY_FAILURE_DOMAINS",
    "AUTONOMY_FAILURE_SCHEMA",
    "autonomy_failure_code",
    "autonomy_failure_payload",
    "autonomy_last_error",
    "sanitize_autonomy_executor_errors",
    "sanitize_autonomy_observation",
]
