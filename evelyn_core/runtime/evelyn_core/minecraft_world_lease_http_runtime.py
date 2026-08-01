from __future__ import annotations

from typing import Any, Awaitable, Callable

from .minecraft_action_contract import (
    validate_minecraft_action_request,
)


MinecraftRequest = Callable[
    [str, str, dict[str, Any] | None],
    Awaitable[tuple[dict[str, Any] | None, str]],
]


class MinecraftWorldLeaseHttpRuntime:
    """Thin non-spawning adapter for an already managed Minecraft service."""

    def __init__(
        self,
        *,
        request: MinecraftRequest,
        is_offline_error: Callable[[str], bool],
    ) -> None:
        self.request = request
        self.is_offline_error = is_offline_error

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result, error = await self.request(
            method,
            path,
            payload,
        )
        if result is not None:
            return dict(result)
        if self.is_offline_error(error):
            raise RuntimeError("minecraft_service_unavailable")
        raise RuntimeError("minecraft_service_request_failed")

    async def start(
        self,
        goal: str | None = None,
        *,
        world_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if goal:
            payload["goal"] = str(goal)
        if world_lease:
            payload["worldLease"] = dict(world_lease)
        return await self._request("POST", "/start", payload)

    async def stop(self) -> dict[str, Any]:
        try:
            return await self._request("POST", "/stop", {})
        except RuntimeError as exc:
            if str(exc) != "minecraft_service_unavailable":
                raise
            return {
                "service": "minecraft",
                "service_available": False,
                "running": False,
                "connected": False,
            }

    async def status(self) -> dict[str, Any]:
        try:
            return await self._request("GET", "/status")
        except RuntimeError as exc:
            if str(exc) != "minecraft_service_unavailable":
                raise
            return {
                "service": "minecraft",
                "service_available": False,
                "running": False,
                "connected": False,
                "last_error": "minecraft_service_unavailable",
            }

    async def set_goal(
        self,
        goal: str,
        *,
        world_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        goal_text = str(goal or "").strip()
        if not goal_text:
            raise RuntimeError("minecraft_goal_missing")
        payload: dict[str, Any] = {"goal": goal_text}
        if world_lease:
            payload["worldLease"] = dict(world_lease)
        status = await self._request("POST", "/goal", payload)
        echoed_goal = str(
            status.get("goal")
            or status.get("goal_override")
            or ""
        ).strip()
        if echoed_goal != goal_text:
            raise RuntimeError("minecraft_goal_unverified")
        status["outcome_verified"] = True
        status["outcome_code"] = "minecraft_goal_confirmed"
        return status

    async def dispatch_action(
        self,
        request: dict[str, Any],
        *,
        world_lease: dict[str, Any],
    ) -> dict[str, Any]:
        bound_request = validate_minecraft_action_request(
            request,
            bound=True,
        )
        proof = dict(world_lease or {})
        if (
            proof.get("leaseId") != bound_request["leaseId"]
            or proof.get("processNonce")
            != bound_request["leaseProcessNonce"]
        ):
            raise RuntimeError(
                "minecraft_world_authorization_required"
            )
        return await self._request(
            "POST",
            "/action",
            {
                "request": bound_request,
                "worldLease": proof,
            },
        )

    async def action_status(
        self,
        goal_run_id: str,
    ) -> dict[str, Any]:
        goal_id = str(goal_run_id or "").strip()
        if (
            not goal_id
            or len(goal_id) > 128
            or any(
                character
                not in (
                    "abcdefghijklmnopqrstuvwxyz"
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789:_-."
                )
                for character in goal_id
            )
        ):
            raise RuntimeError("minecraft_goal_run_id_invalid")
        return await self._request(
            "GET",
            f"/action/{goal_id}",
        )

    async def cancel_action(
        self,
        request: dict[str, Any],
        *,
        world_lease: dict[str, Any],
    ) -> dict[str, Any]:
        bound_request = validate_minecraft_action_request(
            request,
            bound=True,
        )
        proof = dict(world_lease or {})
        if (
            proof.get("leaseId") != bound_request["leaseId"]
            or proof.get("processNonce")
            != bound_request["leaseProcessNonce"]
        ):
            raise RuntimeError(
                "minecraft_world_authorization_required"
            )
        return await self._request(
            "POST",
            "/action/cancel",
            {
                "request": bound_request,
                "worldLease": proof,
            },
        )


__all__ = [
    "MinecraftRequest",
    "MinecraftWorldLeaseHttpRuntime",
]
