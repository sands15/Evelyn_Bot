from __future__ import annotations

from typing import Any


CONTINUITY_STATUS_SCHEMA = "conversation_continuity.status.v1"
CONTINUITY_COMMIT_METRICS_SCHEMA = (
    "conversation_continuity.commit-metrics.v1"
)
CONTINUITY_COMMIT_RECEIPT_SCHEMA = (
    "conversation_continuity.commit-receipt.v1"
)
CONTINUITY_COMMIT_FAILED = (
    "conversation_continuity_commit_failed"
)


class ConversationContinuityCommitError(RuntimeError):
    pass


def _exact_nonnegative_int(value: object) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def require_durable_continuity_receipt(
    value: object,
) -> dict[str, Any]:
    try:
        if not isinstance(value, dict):
            raise ValueError(CONTINUITY_COMMIT_FAILED)
        generation = _exact_nonnegative_int(
            value.get("checkpointGeneration")
        )
        persisted_count = _exact_nonnegative_int(
            value.get("persistedSessionCount")
        )
        metrics = value.get("completedTurnCommit")
        if (
            value.get("schema") != CONTINUITY_STATUS_SCHEMA
            or value.get("state") != "ready"
            or value.get("rollbackProtected") is not True
            or value.get("checkpointIntegrity") != "verified"
            or value.get("checkpointHeadState") != "current"
            or generation is None
            or generation < 1
            or persisted_count is None
            or persisted_count < 1
            or not isinstance(metrics, dict)
            or metrics.get("schema")
            != CONTINUITY_COMMIT_METRICS_SCHEMA
            or metrics.get("lastSucceeded") is not True
        ):
            raise ValueError(CONTINUITY_COMMIT_FAILED)
        attempt_count = _exact_nonnegative_int(
            metrics.get("attemptCount")
        )
        success_count = _exact_nonnegative_int(
            metrics.get("successCount")
        )
        failure_count = _exact_nonnegative_int(
            metrics.get("failureCount")
        )
        sample_count = _exact_nonnegative_int(
            metrics.get("sampleCount")
        )
        if (
            attempt_count is None
            or success_count is None
            or failure_count is None
            or sample_count is None
            or attempt_count < 1
            or success_count < 1
            or success_count + failure_count != attempt_count
            or sample_count < 1
            or sample_count > success_count
        ):
            raise ValueError(CONTINUITY_COMMIT_FAILED)
    except Exception:
        raise ConversationContinuityCommitError(
            CONTINUITY_COMMIT_FAILED
        ) from None
    return {
        "schema": CONTINUITY_COMMIT_RECEIPT_SCHEMA,
        "durable": True,
        "generation": generation,
        "persistedSessionCount": persisted_count,
    }


__all__ = [
    "CONTINUITY_COMMIT_FAILED",
    "CONTINUITY_COMMIT_RECEIPT_SCHEMA",
    "ConversationContinuityCommitError",
    "require_durable_continuity_receipt",
]
