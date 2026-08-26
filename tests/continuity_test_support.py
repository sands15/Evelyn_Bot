from __future__ import annotations

from typing import Any


def durable_continuity_status(
    generation: int = 1,
    *,
    persisted_session_count: int = 1,
) -> dict[str, Any]:
    return {
        "schema": "conversation_continuity.status.v1",
        "state": "ready",
        "rollbackProtected": True,
        "checkpointIntegrity": "verified",
        "checkpointHeadState": "current",
        "checkpointGeneration": generation,
        "persistedSessionCount": persisted_session_count,
        "restoredSessionCount": 0,
        "keyedAuthenticity": False,
        "externalAnchorConfigured": False,
        "completedTurnCommit": {
            "schema": (
                "conversation_continuity.commit-metrics.v1"
            ),
            "attemptCount": 1,
            "successCount": 1,
            "failureCount": 0,
            "sampleCount": 1,
            "lastSucceeded": True,
            "lastTargetVerified": True,
        },
    }


__all__ = ["durable_continuity_status"]
