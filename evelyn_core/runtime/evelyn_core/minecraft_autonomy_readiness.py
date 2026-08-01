from __future__ import annotations

from typing import Any


MINECRAFT_AUTONOMY_READINESS_SCHEMA = (
    "minecraft_autonomy.readiness.v1"
)
MINDCRAFT_TASK_CONTRACT_SCHEMA = "mindcraft.task-contract.v1"
MINECRAFT_READINESS_DEPENDENCIES = (
    "worldLeaseAuthorized",
    "runnerAlive",
    "telemetryFresh",
    "minecraftConnected",
    "taskContractReady",
    "effectObserverReady",
    "autonomyActive",
)
MINECRAFT_READINESS_BLOCKERS = {
    "worldLeaseAuthorized": "world_lease_unauthorized",
    "runnerAlive": "runner_not_alive",
    "telemetryFresh": "telemetry_stale",
    "minecraftConnected": "minecraft_not_connected",
    "taskContractReady": "task_contract_unavailable",
    "effectObserverReady": "effect_observer_unavailable",
    "autonomyActive": "autonomy_not_active",
}


def expected_readiness_state(
    dependencies: dict[str, bool],
) -> str:
    if all(dependencies.values()):
        return "ready"
    if (
        not dependencies["worldLeaseAuthorized"]
        or not dependencies["taskContractReady"]
        or not dependencies["effectObserverReady"]
        or not dependencies["autonomyActive"]
    ):
        return "blocked"
    return "starting"


def validate_minecraft_autonomy_readiness(
    status_payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    raw = status_payload.get("functional_readiness")
    if raw is None:
        raw = status_payload.get("functionalReadiness")
    if raw is None:
        return None, "missing"
    if (
        not isinstance(raw, dict)
        or raw.get("schema")
        != MINECRAFT_AUTONOMY_READINESS_SCHEMA
    ):
        return None, "invalid"
    raw_dependencies = raw.get("dependencies")
    if not isinstance(raw_dependencies, dict):
        return None, "invalid"
    dependencies: dict[str, bool] = {}
    for name in MINECRAFT_READINESS_DEPENDENCIES:
        value = raw_dependencies.get(name)
        if not isinstance(value, bool):
            return None, "invalid"
        dependencies[name] = value
    ready = all(dependencies.values())
    blockers = [
        MINECRAFT_READINESS_BLOCKERS[name]
        for name in MINECRAFT_READINESS_DEPENDENCIES
        if not dependencies[name]
    ]
    expected_state = expected_readiness_state(dependencies)
    raw_blockers = raw.get("blockers")
    task_contract = raw.get("taskContract")
    if (
        raw.get("ready") is not ready
        or str(raw.get("state") or "").strip().lower()
        != expected_state
        or not isinstance(raw_blockers, list)
        or raw_blockers != blockers
        or raw.get("contentFree") is not True
        or not isinstance(task_contract, dict)
        or (
            dependencies["taskContractReady"]
            and (
                task_contract.get("schema")
                != MINDCRAFT_TASK_CONTRACT_SCHEMA
                or str(
                    task_contract.get("goalManagerMode") or ""
                ).strip().lower()
                != "gated"
                or task_contract.get("commandGate")
                != "evelyn_goal_manager"
                or task_contract.get("effectVerification")
                != "explicit_postcondition"
            )
        )
        or (
            dependencies["autonomyActive"]
            and str(
                task_contract.get("autonomyState") or ""
            ).strip().lower()
            != "active"
        )
    ):
        return None, "invalid"
    if str(status_payload.get("runtime") or "").lower() == "mindcraft":
        top_level_dependencies = {
            "worldLeaseAuthorized": status_payload.get(
                "world_lease_authorized"
            ),
            "runnerAlive": status_payload.get("running"),
            "telemetryFresh": status_payload.get(
                "telemetry_fresh"
            ),
            "minecraftConnected": status_payload.get(
                "minecraft_connected"
            ),
        }
        if any(
            not isinstance(value, bool)
            or value is not dependencies[name]
            for name, value in top_level_dependencies.items()
        ):
            return None, "invalid"
    return (
        {
            "schema": MINECRAFT_AUTONOMY_READINESS_SCHEMA,
            "state": expected_state,
            "ready": ready,
            "blockers": blockers,
            "dependencies": dependencies,
            "contentFree": True,
        },
        "valid",
    )


__all__ = [
    "MINECRAFT_AUTONOMY_READINESS_SCHEMA",
    "MINECRAFT_READINESS_BLOCKERS",
    "MINECRAFT_READINESS_DEPENDENCIES",
    "MINDCRAFT_TASK_CONTRACT_SCHEMA",
    "expected_readiness_state",
    "validate_minecraft_autonomy_readiness",
]
