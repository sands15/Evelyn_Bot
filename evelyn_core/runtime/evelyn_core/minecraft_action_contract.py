from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping


if TYPE_CHECKING:
    from .autonomy import AutonomyExecutionContext


MINECRAFT_ACTION_REQUEST_SCHEMA = (
    "minecraft_autonomy.action-request.v1"
)
MINECRAFT_ACTION_DISPATCH_SCHEMA = (
    "minecraft_autonomy.action-dispatch.v1"
)
MINECRAFT_ACTION_RESULT_SCHEMA = (
    "minecraft_autonomy.action-result.v1"
)

_SAFE_ID = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9:_\-.]{0,127}\Z",
    re.ASCII,
)
_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "guildId",
        "actionKey",
        "actionRunId",
        "authorizationGrantId",
        "contractCode",
        "parameters",
    }
)
_BOUND_REQUEST_FIELDS = frozenset(
    {
        *_REQUEST_FIELDS,
        "goalRunId",
        "leaseId",
        "leaseProcessNonce",
    }
)
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "guildId",
        "actionKey",
        "actionRunId",
        "authorizationGrantId",
        "goalRunId",
        "leaseId",
        "leaseProcessNonce",
        "contractCode",
        "postconditionCode",
        "evidenceCode",
        "verified",
        "contentFree",
    }
)
_DISPATCH_FIELDS = frozenset(
    {
        "schema",
        "status",
        "guildId",
        "actionKey",
        "actionRunId",
        "authorizationGrantId",
        "goalRunId",
        "leaseId",
        "leaseProcessNonce",
        "contractCode",
        "accepted",
        "contentFree",
        "errorCode",
    }
)
_DISPATCH_STATES = frozenset(
    {"accepted", "running", "cancelled", "failed"}
)
_FORBIDDEN_RECURSIVE_KEYS = frozenset(
    {
        "argv",
        "chat",
        "code",
        "command",
        "coordinates",
        "goal",
        "inventory",
        "position",
        "rawarguments",
        "rawcommand",
        "rawgoal",
        "rawresult",
        "result",
        "transcript",
        "workingdirectory",
    }
)


class MinecraftActionContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MinecraftActionSpec:
    action_key: str
    contract_code: str
    postcondition_code: str
    evidence_code: str
    allowed_step_fields: frozenset[str]
    allowed_reasons: frozenset[str]


_ACTION_SPECS: dict[str, MinecraftActionSpec] = {
    "minecraft:find_food_source": MinecraftActionSpec(
        action_key="minecraft:find_food_source",
        contract_code="mindcraft_food_recovery.v1",
        postcondition_code="food_reserve_ready",
        evidence_code="minecraft_find_food_source_completed",
        allowed_step_fields=frozenset(
            {"domain", "action", "reason"}
        ),
        allowed_reasons=frozenset(
            {"", "low_health_no_food"}
        ),
    ),
}
MINECRAFT_ROUTE_ACTIONS = tuple(sorted(_ACTION_SPECS))
MINECRAFT_ACTION_SPECS: Mapping[str, MinecraftActionSpec] = (
    MappingProxyType(_ACTION_SPECS)
)


def _identifier(value: Any, *, code: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise MinecraftActionContractError(code)
    return text


def _guild_id(value: Any) -> int:
    if isinstance(value, bool):
        raise MinecraftActionContractError(
            "minecraft_action_guild_invalid"
        )
    try:
        guild_id = int(value)
    except (TypeError, ValueError) as exc:
        raise MinecraftActionContractError(
            "minecraft_action_guild_invalid"
        ) from exc
    if guild_id <= 0 or guild_id > ((1 << 64) - 1):
        raise MinecraftActionContractError(
            "minecraft_action_guild_invalid"
        )
    return guild_id


def _assert_content_free(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in _FORBIDDEN_RECURSIVE_KEYS:
                raise MinecraftActionContractError(
                    "minecraft_action_content_forbidden"
                )
            _assert_content_free(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_content_free(item)


def build_minecraft_action_request(
    step: Any,
    *,
    context: AutonomyExecutionContext | None,
) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise MinecraftActionContractError(
            "minecraft_action_step_invalid"
        )
    if context is None:
        raise MinecraftActionContractError(
            "minecraft_action_context_required"
        )
    action_key = _identifier(
        context.action_key,
        code="minecraft_action_key_invalid",
    )
    spec = _ACTION_SPECS.get(action_key)
    if spec is None:
        raise MinecraftActionContractError(
            "minecraft_action_unsupported"
        )
    if set(step) - spec.allowed_step_fields:
        raise MinecraftActionContractError(
            "minecraft_action_step_fields_invalid"
        )
    if str(step.get("domain") or "").strip() != "minecraft":
        raise MinecraftActionContractError(
            "minecraft_action_domain_invalid"
        )
    if (
        f"minecraft:{str(step.get('action') or '').strip()}"
        != action_key
    ):
        raise MinecraftActionContractError(
            "minecraft_action_key_mismatch"
        )
    reason = str(step.get("reason") or "").strip()
    if reason not in spec.allowed_reasons:
        raise MinecraftActionContractError(
            "minecraft_action_reason_invalid"
        )
    request = {
        "schema": MINECRAFT_ACTION_REQUEST_SCHEMA,
        "guildId": _guild_id(context.guild_id),
        "actionKey": action_key,
        "actionRunId": _identifier(
            context.action_run_id,
            code="minecraft_action_run_id_invalid",
        ),
        "authorizationGrantId": _identifier(
            context.authorization_grant_id,
            code="minecraft_action_grant_id_invalid",
        ),
        "contractCode": spec.contract_code,
        "parameters": {},
    }
    _assert_content_free(request)
    return request


def validate_minecraft_action_request(
    payload: Any,
    *,
    bound: bool,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MinecraftActionContractError(
            "minecraft_action_request_invalid"
        )
    expected_fields = (
        _BOUND_REQUEST_FIELDS if bound else _REQUEST_FIELDS
    )
    if set(payload) != expected_fields:
        raise MinecraftActionContractError(
            "minecraft_action_request_fields_invalid"
        )
    if payload.get("schema") != MINECRAFT_ACTION_REQUEST_SCHEMA:
        raise MinecraftActionContractError(
            "minecraft_action_request_schema_invalid"
        )
    action_key = _identifier(
        payload.get("actionKey"),
        code="minecraft_action_key_invalid",
    )
    spec = _ACTION_SPECS.get(action_key)
    if spec is None:
        raise MinecraftActionContractError(
            "minecraft_action_unsupported"
        )
    if payload.get("contractCode") != spec.contract_code:
        raise MinecraftActionContractError(
            "minecraft_action_contract_mismatch"
        )
    if payload.get("parameters") != {}:
        raise MinecraftActionContractError(
            "minecraft_action_parameters_invalid"
        )
    normalized = {
        "schema": MINECRAFT_ACTION_REQUEST_SCHEMA,
        "guildId": _guild_id(payload.get("guildId")),
        "actionKey": action_key,
        "actionRunId": _identifier(
            payload.get("actionRunId"),
            code="minecraft_action_run_id_invalid",
        ),
        "authorizationGrantId": _identifier(
            payload.get("authorizationGrantId"),
            code="minecraft_action_grant_id_invalid",
        ),
        "contractCode": spec.contract_code,
        "parameters": {},
    }
    if bound:
        normalized.update(
            {
                "goalRunId": _identifier(
                    payload.get("goalRunId"),
                    code="minecraft_goal_run_id_invalid",
                ),
                "leaseId": _identifier(
                    payload.get("leaseId"),
                    code="minecraft_action_lease_id_invalid",
                ),
                "leaseProcessNonce": _identifier(
                    payload.get("leaseProcessNonce"),
                    code=(
                        "minecraft_action_lease_process_invalid"
                    ),
                ),
            }
        )
    _assert_content_free(normalized)
    return normalized


def bind_minecraft_action_request(
    payload: Any,
    *,
    goal_run_id: str,
    lease_id: str,
    lease_process_nonce: str,
) -> dict[str, Any]:
    normalized = validate_minecraft_action_request(
        payload,
        bound=False,
    )
    normalized.update(
        {
            "goalRunId": _identifier(
                goal_run_id,
                code="minecraft_goal_run_id_invalid",
            ),
            "leaseId": _identifier(
                lease_id,
                code="minecraft_action_lease_id_invalid",
            ),
            "leaseProcessNonce": _identifier(
                lease_process_nonce,
                code="minecraft_action_lease_process_invalid",
            ),
        }
    )
    return validate_minecraft_action_request(
        normalized,
        bound=True,
    )


def validate_minecraft_action_result(
    payload: Any,
    *,
    expected_request: Any,
) -> dict[str, Any]:
    request = validate_minecraft_action_request(
        expected_request,
        bound=True,
    )
    if not isinstance(payload, dict) or set(payload) != _RESULT_FIELDS:
        raise MinecraftActionContractError(
            "minecraft_action_result_fields_invalid"
        )
    if payload.get("schema") != MINECRAFT_ACTION_RESULT_SCHEMA:
        raise MinecraftActionContractError(
            "minecraft_action_result_schema_invalid"
        )
    spec = _ACTION_SPECS[request["actionKey"]]
    exact = {
        "guildId": request["guildId"],
        "actionKey": request["actionKey"],
        "actionRunId": request["actionRunId"],
        "authorizationGrantId": request[
            "authorizationGrantId"
        ],
        "goalRunId": request["goalRunId"],
        "leaseId": request["leaseId"],
        "leaseProcessNonce": request["leaseProcessNonce"],
        "contractCode": spec.contract_code,
        "postconditionCode": spec.postcondition_code,
        "evidenceCode": spec.evidence_code,
        "status": "completed",
        "verified": True,
        "contentFree": True,
    }
    for key, expected in exact.items():
        if payload.get(key) != expected:
            raise MinecraftActionContractError(
                "minecraft_action_result_mismatch"
            )
    normalized = {
        "schema": MINECRAFT_ACTION_RESULT_SCHEMA,
        **exact,
    }
    _assert_content_free(normalized)
    return normalized


def validate_minecraft_action_dispatch(
    payload: Any,
    *,
    expected_request: Any,
) -> dict[str, Any]:
    request = validate_minecraft_action_request(
        expected_request,
        bound=True,
    )
    if not isinstance(payload, dict) or set(payload) != _DISPATCH_FIELDS:
        raise MinecraftActionContractError(
            "minecraft_action_dispatch_fields_invalid"
        )
    if payload.get("schema") != MINECRAFT_ACTION_DISPATCH_SCHEMA:
        raise MinecraftActionContractError(
            "minecraft_action_dispatch_schema_invalid"
        )
    status = str(payload.get("status") or "").strip()
    if status not in _DISPATCH_STATES:
        raise MinecraftActionContractError(
            "minecraft_action_dispatch_status_invalid"
        )
    accepted = payload.get("accepted")
    if not isinstance(accepted, bool) or accepted is not (
        status in {"accepted", "running"}
    ):
        raise MinecraftActionContractError(
            "minecraft_action_dispatch_acceptance_invalid"
        )
    if payload.get("contentFree") is not True:
        raise MinecraftActionContractError(
            "minecraft_action_dispatch_content_invalid"
        )
    error_code = str(payload.get("errorCode") or "").strip()
    if status in {"accepted", "running"}:
        if error_code:
            raise MinecraftActionContractError(
                "minecraft_action_dispatch_error_invalid"
            )
    else:
        _identifier(
            error_code,
            code="minecraft_action_dispatch_error_invalid",
        )
    exact = {
        "guildId": request["guildId"],
        "actionKey": request["actionKey"],
        "actionRunId": request["actionRunId"],
        "authorizationGrantId": request[
            "authorizationGrantId"
        ],
        "goalRunId": request["goalRunId"],
        "leaseId": request["leaseId"],
        "leaseProcessNonce": request["leaseProcessNonce"],
        "contractCode": request["contractCode"],
    }
    for key, expected in exact.items():
        if payload.get(key) != expected:
            raise MinecraftActionContractError(
                "minecraft_action_dispatch_mismatch"
            )
    normalized = {
        "schema": MINECRAFT_ACTION_DISPATCH_SCHEMA,
        "status": status,
        **exact,
        "accepted": accepted,
        "contentFree": True,
        "errorCode": error_code,
    }
    _assert_content_free(normalized)
    return normalized


__all__ = [
    "MINECRAFT_ACTION_DISPATCH_SCHEMA",
    "MINECRAFT_ACTION_REQUEST_SCHEMA",
    "MINECRAFT_ACTION_RESULT_SCHEMA",
    "MINECRAFT_ACTION_SPECS",
    "MINECRAFT_ROUTE_ACTIONS",
    "MinecraftActionContractError",
    "MinecraftActionSpec",
    "bind_minecraft_action_request",
    "build_minecraft_action_request",
    "validate_minecraft_action_request",
    "validate_minecraft_action_dispatch",
    "validate_minecraft_action_result",
]
