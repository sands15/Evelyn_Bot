from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


MINECRAFT_CONNECTED_OUTCOME = "minecraft_connected"
MINECRAFT_STOPPED_OUTCOME = "minecraft_stopped"


def minecraft_connection_confirmed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(
        payload.get(key) is True
        for key in (
            "connected",
            "minecraft_connected",
            "voyager_connected",
        )
    )


def minecraft_stop_confirmed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    running_keys = (
        "running",
        "loop_running",
        "minecraft_autonomy",
        "active",
    )
    connection_keys = (
        "connected",
        "minecraft_connected",
        "voyager_connected",
    )
    has_runtime_evidence = any(key in payload for key in running_keys)
    has_connection_evidence = any(key in payload for key in connection_keys)
    if not has_runtime_evidence or not has_connection_evidence:
        return False
    running = any(payload.get(key) is True for key in running_keys)
    connected = any(payload.get(key) is True for key in connection_keys)
    return not running and not connected


@dataclass(frozen=True)
class MinecraftModeCompositionDeps:
    get_client: Callable[[], Any]
    merge_status: Callable[..., dict[str, Any] | None]
    clean_text: Callable[[str], str]
    monotonic: Callable[[], float]
    sleep: Callable[[float], Awaitable[Any]]


class MinecraftModeComposition:
    def __init__(self, deps: MinecraftModeCompositionDeps) -> None:
        self.deps = deps

    async def wait_for_minecraft_ready(
        self,
        guild_id: int,
        *,
        timeout_sec: float = 12.0,
        poll_sec: float = 1.0,
    ) -> dict[str, Any]:
        _ = guild_id
        deps = self.deps
        deadline = deps.monotonic() + max(0.5, timeout_sec)
        last_observed: dict[str, Any] = {}
        client = deps.get_client()
        while deps.monotonic() < deadline:
            status = await client.status()
            observed = (
                status.get("observation")
                if isinstance(status, dict)
                and isinstance(status.get("observation"), dict)
                else status
            )
            if isinstance(observed, dict):
                last_observed = (
                    deps.merge_status(
                        status if isinstance(status, dict) else None,
                        observed,
                    )
                    or dict(observed)
                )
                if minecraft_connection_confirmed(status) or (
                    observed is not status
                    and minecraft_connection_confirmed(observed)
                ):
                    return last_observed
                last_error = deps.clean_text(
                    str(
                        (
                            status.get("last_error")
                            if isinstance(status, dict)
                            else None
                        )
                        or observed.get("last_error")
                        or ""
                    )
                )
                if last_error:
                    last_observed["wait_last_error"] = last_error
            await deps.sleep(max(0.1, poll_sec))
        return last_observed or {
            "connected": False,
            "active": False,
            "last_error": "timeout_waiting_for_voyager_service",
        }

    async def wait_for_minecraft_stopped(
        self,
        guild_id: int,
        *,
        initial_status: Any = None,
        timeout_sec: float = 8.0,
        poll_sec: float = 0.5,
    ) -> dict[str, Any]:
        _ = guild_id
        deps = self.deps
        client = deps.get_client()
        current = initial_status if isinstance(initial_status, dict) else None
        deadline = deps.monotonic() + max(0.5, timeout_sec)
        while True:
            if minecraft_stop_confirmed(current):
                return dict(current)
            if deps.monotonic() >= deadline:
                return dict(current or {})
            await deps.sleep(max(0.1, poll_sec))
            current = await client.status()

    async def enable_minecraft_mode(
        self,
        guild_id: int,
        goal: str | None = None,
    ) -> dict[str, Any]:
        client = self.deps.get_client()
        started = await client.start(goal=goal)
        observed = await self.wait_for_minecraft_ready(guild_id)
        merged = dict(observed) if isinstance(observed, dict) else {}
        merged["voyager_repo_present"] = (
            started.get("voyager_repo_present")
            if isinstance(started, dict)
            else None
        )
        if not minecraft_connection_confirmed(merged):
            raise RuntimeError("minecraft_start_unverified")
        merged["outcome_verified"] = True
        merged["outcome_code"] = MINECRAFT_CONNECTED_OUTCOME
        return merged

    async def disable_minecraft_mode(self, guild_id: int) -> dict[str, Any]:
        stopped = await self.deps.get_client().stop()
        observed = await self.wait_for_minecraft_stopped(
            guild_id,
            initial_status=stopped,
        )
        if not minecraft_stop_confirmed(observed):
            raise RuntimeError("minecraft_stop_unverified")
        observed["outcome_verified"] = True
        observed["outcome_code"] = MINECRAFT_STOPPED_OUTCOME
        return observed


__all__ = [
    "MINECRAFT_CONNECTED_OUTCOME",
    "MINECRAFT_STOPPED_OUTCOME",
    "MinecraftModeComposition",
    "MinecraftModeCompositionDeps",
    "minecraft_connection_confirmed",
    "minecraft_stop_confirmed",
]
