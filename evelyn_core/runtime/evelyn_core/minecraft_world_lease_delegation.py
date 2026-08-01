from __future__ import annotations

import hmac
import math
import re
from typing import Any


MINECRAFT_WORLD_LEASE_DELEGATION_RESULT_SCHEMA = (
    "minecraft_world_lease.delegation_result.v1"
)
MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER = (
    "X-Evelyn-Minecraft-Lease-Token"
)
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,96}$")


def minecraft_world_lease_delegation_authorized(
    *,
    expected_token: str,
    presented_token: str,
) -> bool:
    expected = str(expected_token or "").strip()
    presented = str(presented_token or "").strip()
    return bool(
        expected
        and presented
        and hmac.compare_digest(expected, presented)
    )


def minecraft_world_lease_delegation_error_code(
    error: BaseException | str,
) -> str:
    value = (
        str(error)
        if isinstance(error, BaseException)
        else str(error or "")
    ).strip()
    if _SAFE_ERROR_CODE.fullmatch(value):
        return value
    return "minecraft_world_lease_delegation_failed"


def _guild_id(value: Any) -> int:
    try:
        guild_id = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "minecraft_world_guild_invalid"
        ) from exc
    if guild_id < 0:
        raise RuntimeError("minecraft_world_guild_invalid")
    return guild_id


def _ttl_sec(value: Any) -> float | None:
    if value is None:
        return None
    try:
        ttl = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "minecraft_world_ttl_invalid"
        ) from exc
    if not math.isfinite(ttl):
        raise RuntimeError("minecraft_world_ttl_invalid")
    return ttl


async def execute_minecraft_world_lease_delegation(
    owner: Any,
    *,
    action: str,
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("minecraft_world_payload_invalid")
    normalized_action = str(action or "").strip().lower()
    guild_id = _guild_id(payload.get("guildId"))
    if normalized_action == "connect":
        result = await owner.connect(
            guild_id,
            issuer_ref=str(payload.get("issuerRef") or ""),
            source=str(payload.get("source") or ""),
            goal=(
                str(payload.get("goal")).strip()
                if payload.get("goal") is not None
                else None
            ),
            ttl_sec=_ttl_sec(payload.get("ttlSec")),
        )
    elif normalized_action == "disconnect":
        result = await owner.disconnect(guild_id)
    elif normalized_action == "goal":
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            raise RuntimeError("minecraft_goal_missing")
        result = await owner.set_goal(guild_id, goal)
    elif normalized_action == "action":
        if set(payload) != {"guildId", "request"}:
            raise RuntimeError(
                "minecraft_action_delegation_fields_invalid"
            )
        request = payload.get("request")
        if not isinstance(request, dict):
            raise RuntimeError(
                "minecraft_action_request_invalid"
            )
        result = await owner.dispatch_action(
            guild_id,
            dict(request),
        )
    elif normalized_action == "action_status":
        if set(payload) != {
            "guildId",
            "goalRunId",
            "actionRunId",
            "actionKey",
            "contractCode",
        }:
            raise RuntimeError(
                "minecraft_action_status_fields_invalid"
            )
        result = await owner.action_status(
            guild_id,
            goal_run_id=str(payload.get("goalRunId") or ""),
            action_run_id=str(payload.get("actionRunId") or ""),
            action_key=str(payload.get("actionKey") or ""),
            contract_code=str(
                payload.get("contractCode") or ""
            ),
        )
    elif normalized_action == "cancel_action":
        if set(payload) != {"guildId", "actionRunId"}:
            raise RuntimeError(
                "minecraft_action_cancel_fields_invalid"
            )
        result = await owner.cancel_action(
            guild_id,
            str(payload.get("actionRunId") or ""),
        )
    else:
        raise RuntimeError(
            "minecraft_world_delegation_action_invalid"
        )
    if not isinstance(result, dict):
        raise RuntimeError(
            "minecraft_world_delegation_result_invalid"
        )
    return {
        "schema": MINECRAFT_WORLD_LEASE_DELEGATION_RESULT_SCHEMA,
        "ok": True,
        "action": normalized_action,
        "result": dict(result),
        "leaseStatus": owner.status(),
    }


__all__ = [
    "MINECRAFT_WORLD_LEASE_DELEGATION_RESULT_SCHEMA",
    "MINECRAFT_WORLD_LEASE_DELEGATION_TOKEN_HEADER",
    "execute_minecraft_world_lease_delegation",
    "minecraft_world_lease_delegation_authorized",
    "minecraft_world_lease_delegation_error_code",
]
