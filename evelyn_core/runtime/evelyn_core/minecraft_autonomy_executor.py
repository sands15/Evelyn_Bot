from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .autonomy import AutonomyExecutionContext
from .minecraft_action_contract import (
    MINECRAFT_ACTION_RESULT_SCHEMA,
    MinecraftActionContractError,
    bind_minecraft_action_request,
    build_minecraft_action_request,
    validate_minecraft_action_dispatch,
    validate_minecraft_action_result,
)
from .minecraft_autonomy_readiness import (
    validate_minecraft_autonomy_readiness,
)
from .minecraft_mode_composition import (
    MINECRAFT_STOPPED_OUTCOME,
    minecraft_stop_confirmed,
)
from .minecraft_world_lease_contract import (
    validate_world_lease_status,
)


_ACTION_GATEWAY_SCHEMA = "mindcraft_action_gateway.readiness.v1"
_ACTION_GATEWAY_KEYS = frozenset(
    {
        "schema",
        "state",
        "ready",
        "acceptsNewAction",
        "active",
        "terminalStatus",
        "repeatActionReady",
        "contentFree",
    }
)


def _repeat_action_gateway_ready(runtime: Any) -> bool:
    if not isinstance(runtime, dict):
        return False
    gateway = runtime.get("action_gateway")
    if (
        not isinstance(gateway, dict)
        or set(gateway) != _ACTION_GATEWAY_KEYS
        or gateway.get("schema") != _ACTION_GATEWAY_SCHEMA
        or runtime.get("action_gateway_ready") is not True
        or gateway.get("state") != "terminal"
        or gateway.get("ready") is not True
        or gateway.get("acceptsNewAction") is not True
        or gateway.get("active") is not False
        or gateway.get("repeatActionReady") is not True
        or gateway.get("contentFree") is not True
        or gateway.get("terminalStatus")
        not in {"completed", "cancelled", "failed"}
    ):
        return False
    return True


@dataclass(frozen=True)
class MinecraftAutonomyExecutorDeps:
    get_world_lease_status: Callable[[], dict[str, Any]]
    get_runtime_status: Callable[[], Awaitable[dict[str, Any]]]
    execute_action: Callable[
        [int, dict[str, Any]],
        Awaitable[dict[str, Any]],
    ]
    cancel_action: Callable[[int, str], Awaitable[Any]] | None = None
    force_disconnect: Callable[[int], Awaitable[Any]] | None = None
    now: Callable[[], float] = time.time


def _lease_boundary(
    status: Any,
    *,
    guild_id: int,
    now: float,
) -> tuple[dict[str, Any] | None, str]:
    valid, error = validate_world_lease_status(
        status,
        now=now,
    )
    if not valid or not isinstance(status, dict):
        return None, error or "minecraft_world_authorization_required"
    lease = status.get("lease")
    if (
        not isinstance(lease, dict)
        or isinstance(lease.get("guildId"), bool)
        or lease.get("guildId") != guild_id
    ):
        return None, "minecraft_world_lease_owner_mismatch"
    lease_id = str(lease.get("leaseId") or "").strip()
    process_nonce = str(status.get("processNonce") or "").strip()
    if not lease_id or not process_nonce:
        return None, "minecraft_world_lease_status_invalid"
    return {
        "leaseId": lease_id,
        "leaseProcessNonce": process_nonce,
    }, ""


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "reason": str(reason or "minecraft_action_unavailable"),
        "verified": False,
    }


class MinecraftAutonomyExecutor:
    """One-shot, lease-bound Minecraft action executor.

    Construction and ``connect`` are observational only. World start/stop and
    lease mutations remain explicit operator flows.
    """

    def __init__(
        self,
        *,
        guild_id: int,
        deps: MinecraftAutonomyExecutorDeps,
    ) -> None:
        self.guild_id = int(guild_id)
        self.deps = deps
        self._connected = False
        self._inflight_action_run_id = ""
        self._inflight_action_request: dict[str, Any] | None = None
        self._inflight_lease: dict[str, Any] | None = None
        self._execute_lock = asyncio.Lock()

    async def _readiness_boundary(
        self,
        *,
        allow_repeat_gateway: bool = False,
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            runtime = await self.deps.get_runtime_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            return None, "minecraft_runtime_status_unavailable"
        if not isinstance(runtime, dict):
            return None, "minecraft_runtime_status_invalid"
        readiness, contract_state = (
            validate_minecraft_autonomy_readiness(runtime)
        )
        if contract_state != "valid" or readiness is None:
            return None, "minecraft_readiness_contract_invalid"
        if readiness.get("ready") is not True:
            if (
                allow_repeat_gateway
                and _repeat_action_gateway_ready(runtime)
            ):
                return runtime, ""
            blockers = readiness.get("blockers")
            return None, (
                str(blockers[0])
                if isinstance(blockers, list) and blockers
                else "minecraft_runtime_not_ready"
            )
        return runtime, ""

    def _lease(self) -> tuple[dict[str, Any] | None, str]:
        try:
            status = self.deps.get_world_lease_status()
        except Exception:
            return None, "minecraft_world_lease_status_unavailable"
        return _lease_boundary(
            status,
            guild_id=self.guild_id,
            now=self.deps.now(),
        )

    async def connect(self) -> None:
        lease, lease_error = self._lease()
        if lease is None:
            raise RuntimeError(lease_error)
        runtime, readiness_error = await self._readiness_boundary()
        if runtime is None:
            raise RuntimeError(readiness_error)
        self._connected = True

    async def _cancel_inflight_verified(
        self,
        action_run_id: str,
    ) -> bool:
        cancellation_requested = False
        request = self._inflight_action_request
        lease = self._inflight_lease
        if (
            self.deps.cancel_action is not None
            and isinstance(request, dict)
            and isinstance(lease, dict)
            and request.get("actionRunId") == action_run_id
        ):
            try:
                cleanup = asyncio.create_task(
                    self.deps.cancel_action(
                        self.guild_id,
                        action_run_id,
                    )
                )
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancellation_requested = True
                        continue
                    except Exception:
                        break
                cancelled = cleanup.result()
                expected = bind_minecraft_action_request(
                    request,
                    goal_run_id=str(
                        (
                            cancelled
                            if isinstance(cancelled, dict)
                            else {}
                        ).get("goalRunId")
                        or ""
                    ),
                    lease_id=str(lease.get("leaseId") or ""),
                    lease_process_nonce=str(
                        lease.get("leaseProcessNonce") or ""
                    ),
                )
                verified = validate_minecraft_action_dispatch(
                    cancelled,
                    expected_request=expected,
                )
                if verified["status"] != "cancelled":
                    raise RuntimeError(
                        "minecraft_action_cancel_unverified"
                    )
                return cancellation_requested
            except Exception:
                pass
            except asyncio.CancelledError:
                cancellation_requested = True

        if self.deps.force_disconnect is None:
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            )
        fallback = asyncio.create_task(
            self.deps.force_disconnect(self.guild_id)
        )
        while not fallback.done():
            try:
                await asyncio.shield(fallback)
            except asyncio.CancelledError:
                cancellation_requested = True
                continue
            except Exception:
                break
        try:
            stopped = fallback.result()
        except asyncio.CancelledError as exc:
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            ) from exc
        except Exception as exc:
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            ) from exc
        if (
            not isinstance(stopped, dict)
            or stopped.get("outcome_verified") is not True
            or stopped.get("outcome_code")
            != MINECRAFT_STOPPED_OUTCOME
            or not minecraft_stop_confirmed(stopped)
        ):
            raise RuntimeError(
                "minecraft_action_cancel_unverified"
            )
        return cancellation_requested

    def _clear_inflight(self) -> None:
        self._inflight_action_run_id = ""
        self._inflight_action_request = None
        self._inflight_lease = None

    async def disconnect(self) -> None:
        action_run_id = self._inflight_action_run_id
        cancellation_requested = False
        if action_run_id:
            cancellation_requested = (
                await self._cancel_inflight_verified(
                    action_run_id
                )
            )
            self._clear_inflight()
        self._connected = False
        if cancellation_requested:
            raise asyncio.CancelledError()

    async def observe(self) -> dict[str, Any]:
        lease, lease_error = self._lease()
        runtime, readiness_error = await self._readiness_boundary(
            allow_repeat_gateway=True,
        )
        if lease is None or runtime is None:
            return {
                "environment": "minecraft",
                "connected": False,
                "active": False,
                "ready": False,
                "blocker": lease_error or readiness_error,
            }
        observation = (
            runtime.get("observation")
            if isinstance(runtime.get("observation"), dict)
            else runtime
        )
        safe = {
            key: observation.get(key)
            for key in (
                "connected",
                "active",
                "position",
                "health",
                "hunger",
                "inventory",
                "hostiles_nearby",
                "nearest_hostile",
                "immediate_hazards",
                "updated_at",
            )
            if key in observation
        }
        safe.update(
            {
                "environment": "minecraft",
                "ready": True,
                "leaseBound": True,
            }
        )
        return safe

    async def execute_step(
        self,
        step: dict[str, Any],
        *,
        context: AutonomyExecutionContext | None = None,
    ) -> dict[str, Any]:
        if not self._connected:
            return _blocked("minecraft_executor_disabled")
        try:
            request = build_minecraft_action_request(
                step,
                context=context,
            )
        except MinecraftActionContractError as exc:
            return _blocked(exc.code)
        if context is None or context.guild_id != self.guild_id:
            return _blocked("minecraft_action_guild_mismatch")

        async with self._execute_lock:
            before_lease, lease_error = self._lease()
            if before_lease is None:
                return _blocked(lease_error)
            runtime, readiness_error = await self._readiness_boundary(
                allow_repeat_gateway=True,
            )
            if runtime is None:
                return _blocked(readiness_error)
            self._inflight_action_run_id = context.action_run_id
            self._inflight_action_request = dict(request)
            self._inflight_lease = dict(before_lease)
            try:
                result = await self.deps.execute_action(
                    self.guild_id,
                    request,
                )
            except asyncio.CancelledError:
                await self._cancel_inflight_verified(
                    context.action_run_id
                )
                self._clear_inflight()
                raise
            except Exception as exc:
                if str(exc) == "minecraft_action_cancel_unverified":
                    raise
                self._clear_inflight()
                return _blocked("minecraft_action_execution_failed")
            else:
                self._clear_inflight()

            after_lease, after_error = self._lease()
            if after_lease is None:
                return _blocked(after_error)
            if after_lease != before_lease:
                return _blocked("minecraft_world_lease_changed")
            if (
                not isinstance(result, dict)
                or result.get("schema")
                != MINECRAFT_ACTION_RESULT_SCHEMA
            ):
                return _blocked("minecraft_action_result_invalid")
            try:
                expected = bind_minecraft_action_request(
                    request,
                    goal_run_id=str(
                        result.get("goalRunId") or ""
                    ),
                    lease_id=before_lease["leaseId"],
                    lease_process_nonce=before_lease[
                        "leaseProcessNonce"
                    ],
                )
                verified = validate_minecraft_action_result(
                    result,
                    expected_request=expected,
                )
            except MinecraftActionContractError as exc:
                return _blocked(exc.code)
            return {
                "status": "ok",
                "reason": "explicit_postcondition_verified",
                "verified": True,
                "evidence_code": verified["evidenceCode"],
                "postcondition_code": verified[
                    "postconditionCode"
                ],
                "action_run_id": verified["actionRunId"],
                "goal_run_id": verified["goalRunId"],
            }


def build_minecraft_autonomy_executor_from_runtime(
    guild_id: int,
    *,
    get_world_lease_owner: Callable[[], Any],
    get_client: Callable[[], Any],
    now: Callable[[], float] = time.time,
) -> MinecraftAutonomyExecutor:
    """Build the executor without leaking transport callbacks into main.py."""

    return MinecraftAutonomyExecutor(
        guild_id=guild_id,
        deps=MinecraftAutonomyExecutorDeps(
            get_world_lease_status=(
                lambda: get_world_lease_owner().status()
            ),
            get_runtime_status=lambda: get_client().status(),
            execute_action=(
                lambda target_guild_id, request: (
                    get_world_lease_owner().execute_action(
                        target_guild_id,
                        request,
                    )
                )
            ),
            cancel_action=(
                lambda target_guild_id, action_run_id: (
                    get_world_lease_owner().cancel_action(
                        target_guild_id,
                        action_run_id,
                    )
                )
            ),
            force_disconnect=(
                lambda target_guild_id: (
                    get_world_lease_owner().disconnect(
                        target_guild_id
                    )
                )
            ),
            now=now,
        ),
    )


__all__ = [
    "MinecraftAutonomyExecutor",
    "MinecraftAutonomyExecutorDeps",
    "build_minecraft_autonomy_executor_from_runtime",
]
