from __future__ import annotations

from typing import Any

from .autonomy_authorization import SUPPORTED_AUTONOMY_ACTIONS
from .autonomy_outcome_evidence import (
    AUTONOMY_ACTION_EVIDENCE_CODES,
    autonomy_outcome_verified,
)


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
_AUTONOMY_RESULT_STATUSES = frozenset(
    {
        "ok",
        "done",
        "completed",
        "blocked",
        "failed",
        "unknown",
        "unverified",
    }
)
_AUTONOMY_RESULT_REASONS = frozenset(
    {
        AUTONOMY_CYCLE_FAILED,
        AUTONOMY_EXECUTOR_EXECUTE_FAILED,
        "action_not_allowed",
        "attack_step_skipped",
        "authorization_action_unsupported",
        "authorization_audit_unavailable",
        "authorization_changed_during_action",
        "authorization_required",
        "authorization_scope_denied",
        "executor_callback_unavailable",
        "executor_disabled",
        "executor_result_invalid",
        "explicit_postcondition_verified",
        "food_step_skipped",
        "followup_reply_slot_busy",
        "hazard_interrupt",
        "hazard_step_skipped",
        "hostile_interrupt",
        "idle_ok",
        "low_health_interrupt",
        "no_followup_channel",
        "no_queued_proactive_question",
        "no_recent_user_text",
        "outcome_evidence_missing",
        "outcome_unverified",
        "ping_cooldown",
        "plan_complete",
        "proactive_gate_completed",
        "recent_context_summarized",
        "retreat_step_skipped",
        "retry_budget_exhausted",
        "retry_suppressed",
        "router_refresh_inflight",
        "router_refresh_task_unavailable",
        "router_refreshed",
        "sent_followup",
        "shield_skipped",
        "status_checked",
        "summary_ready",
        "target_absence_verified",
        "unsupported_default_action",
    }
)
_AUTONOMY_RESULT_EVIDENCE_CODES = frozenset(
    code
    for codes in AUTONOMY_ACTION_EVIDENCE_CODES.values()
    for code in codes
)
_AUTONOMY_RESULT_BOOL_FIELDS = frozenset(
    {
        "skipped",
        "replan",
        "continuityDurable",
        "connected",
    }
)
_AUTONOMY_RESULT_NUMBER_FIELDS = frozenset(
    {
        "count",
        "active_sessions",
        "inflight_llm_requests",
        "known_followup_channels",
        "continuityGeneration",
        "elapsed_ms",
        "confidence",
    }
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
    for history_field in (
        "latest_user_text",
        "recent_visible",
        "recent_context_items",
        "unresolved_items",
        "search_pending",
        "cognitive_refresh_needed",
    ):
        sanitized.pop(history_field, None)
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


def sanitize_autonomy_step_result(
    value: object,
) -> dict[str, Any]:
    """Project an executor result to content-free durable fields."""

    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, Any] = {}
    status = value.get("status")
    if isinstance(status, str) and status in _AUTONOMY_RESULT_STATUSES:
        sanitized["status"] = status
    reason = value.get("reason")
    if isinstance(reason, str) and reason in _AUTONOMY_RESULT_REASONS:
        sanitized["reason"] = reason
    evidence_code = value.get("evidence_code")
    if (
        isinstance(evidence_code, str)
        and evidence_code in _AUTONOMY_RESULT_EVIDENCE_CODES
    ):
        sanitized["evidence_code"] = evidence_code
    action_key = ""
    step = value.get("step")
    if isinstance(step, dict):
        domain = step.get("domain")
        action = step.get("action")
        if isinstance(domain, str) and isinstance(action, str):
            domain = domain.strip()
            action = action.strip()
            candidate = f"{domain}:{action}"
            if candidate in SUPPORTED_AUTONOMY_ACTIONS:
                action_key = candidate
                sanitized["step"] = {
                    "domain": domain,
                    "action": action,
                }
    if "verified" in value:
        sanitized["verified"] = autonomy_outcome_verified(
            action_key,
            {
                "status": sanitized.get("status"),
                "evidence_code": sanitized.get("evidence_code"),
                "verified": value.get("verified"),
            },
        )
    for key in _AUTONOMY_RESULT_BOOL_FIELDS:
        candidate = value.get(key)
        if type(candidate) is bool:
            sanitized[key] = candidate
    for key in _AUTONOMY_RESULT_NUMBER_FIELDS:
        candidate = value.get(key)
        if type(candidate) in {int, float}:
            sanitized[key] = candidate
    failure = value.get("failure")
    if isinstance(failure, dict):
        sanitized["failure"] = autonomy_failure_payload(
            code=failure.get("code"),
            phase=failure.get("phase"),
            domain=failure.get("domain"),
            action=failure.get("action"),
        )
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
    "sanitize_autonomy_step_result",
]
