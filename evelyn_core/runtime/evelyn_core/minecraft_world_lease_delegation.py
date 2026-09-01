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
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
    ):
        raise RuntimeError("minecraft_world_guild_invalid")
    return value


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


def _parent_record_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    value = payload.get("parentRecordIds")
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) != 1:
        raise RuntimeError("minecraft_archive_lineage_invalid")
    parent = value[0]
    allowed = (
        "abcdefghijklmnopqrstuvwxyz"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789_.:-"
    )
    if (
        not isinstance(parent, str)
        or not parent
        or parent != parent.strip()
        or len(parent) > 64
        or not parent[0].isalnum()
        or not parent[0].isascii()
        or any(character not in allowed for character in parent)
    ):
        raise RuntimeError("minecraft_archive_lineage_invalid")
    return (parent,)


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
        parent_record_ids = _parent_record_ids(payload)
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
            **(
                {"parent_record_ids": parent_record_ids}
                if parent_record_ids
                else {}
            ),
        )
    elif normalized_action == "disconnect":
        lease_id = payload.get("leaseId")
        if lease_id is not None and (
            not isinstance(lease_id, str)
            or not lease_id
            or lease_id != lease_id.strip()
        ):
            raise RuntimeError(
                "minecraft_world_disconnect_lease_invalid"
            )
        parent_record_ids = _parent_record_ids(payload)
        disconnect_options: dict[str, Any] = {}
        if lease_id is not None:
            disconnect_options["expected_lease_id"] = lease_id
        if parent_record_ids:
            disconnect_options["parent_record_ids"] = parent_record_ids
        result = await owner.disconnect(guild_id, **disconnect_options)
    elif normalized_action == "goal":
        goal = str(payload.get("goal") or "").strip()
        if not goal:
            raise RuntimeError("minecraft_goal_missing")
        lease_id = payload.get("leaseId")
        if (
            not isinstance(lease_id, str)
            or not lease_id
            or lease_id != lease_id.strip()
        ):
            raise RuntimeError(
                "minecraft_world_goal_lease_invalid"
            )
        parent_record_ids = _parent_record_ids(payload)
        goal_options: dict[str, Any] = {"expected_lease_id": lease_id}
        if parent_record_ids:
            goal_options["parent_record_ids"] = parent_record_ids
        result = await owner.set_goal(guild_id, goal, **goal_options)
    elif normalized_action == "action":
        if set(payload) != {"guildId", "leaseId", "request"}:
            raise RuntimeError(
                "minecraft_action_delegation_fields_invalid"
            )
        lease_id = payload.get("leaseId")
        if (
            not isinstance(lease_id, str)
            or not lease_id
            or lease_id != lease_id.strip()
        ):
            raise RuntimeError(
                "minecraft_action_delegation_lease_invalid"
            )
        request = payload.get("request")
        if not isinstance(request, dict):
            raise RuntimeError(
                "minecraft_action_request_invalid"
            )
        result = await owner.dispatch_action(
            guild_id,
            dict(request),
            expected_lease_id=lease_id,
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
        if set(payload) != {
            "guildId",
            "actionRunId",
            "leaseId",
        }:
            raise RuntimeError(
                "minecraft_action_cancel_fields_invalid"
            )
        lease_id = payload.get("leaseId")
        if (
            not isinstance(lease_id, str)
            or not lease_id
            or lease_id != lease_id.strip()
        ):
            raise RuntimeError(
                "minecraft_action_cancel_lease_invalid"
            )
        result = await owner.cancel_action(
            guild_id,
            str(payload.get("actionRunId") or ""),
            expected_lease_id=lease_id,
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
