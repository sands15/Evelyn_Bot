from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .minecraft_autonomy_readiness import (
    validate_minecraft_autonomy_readiness,
)


MINECRAFT_CONNECTED_OUTCOME = "minecraft_connected"
MINECRAFT_STOPPED_OUTCOME = "minecraft_stopped"


def _archive_parent_record_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ()
    parents = value.get("parentRecordIds")
    if parents is None:
        return ()
    if not isinstance(parents, list) or len(parents) != 1:
        raise RuntimeError("minecraft_archive_lineage_invalid")
    parent = parents[0]
    if not isinstance(parent, str) or not parent:
        raise RuntimeError("minecraft_archive_lineage_invalid")
    return (parent,)


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
    ready_timeout_sec: float = 60.0


class MinecraftModeComposition:
    def __init__(self, deps: MinecraftModeCompositionDeps) -> None:
        self.deps = deps

    @staticmethod
    def _runtime_ready(
        status: Any,
        observed: Any,
    ) -> bool:
        status_payload = status if isinstance(status, dict) else {}
        readiness, contract_state = (
            validate_minecraft_autonomy_readiness(status_payload)
        )
        if (
            str(status_payload.get("runtime") or "").strip().lower()
            == "mindcraft"
            or contract_state != "missing"
        ):
            return bool(
                contract_state == "valid"
                and readiness is not None
                and readiness.get("ready") is True
            )
        return bool(
            minecraft_connection_confirmed(status_payload)
            or (
                observed is not status
                and minecraft_connection_confirmed(observed)
            )
        )

    async def _wait_for_minecraft_ready(
        self,
        guild_id: int,
        *,
        timeout_sec: float | None = None,
        poll_sec: float = 1.0,
    ) -> tuple[dict[str, Any], bool]:
        _ = guild_id
        deps = self.deps
        wait_timeout_sec = (
            deps.ready_timeout_sec
            if timeout_sec is None
            else timeout_sec
        )
        deadline = deps.monotonic() + max(0.5, wait_timeout_sec)
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
                if self._runtime_ready(status, observed):
                    return last_observed, True
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
        return (
            last_observed
            or {
                "connected": False,
                "active": False,
                "last_error": "timeout_waiting_for_voyager_service",
            },
            False,
        )

    async def wait_for_minecraft_ready(
        self,
        guild_id: int,
        *,
        timeout_sec: float | None = None,
        poll_sec: float = 1.0,
    ) -> dict[str, Any]:
        observed, _ready = await self._wait_for_minecraft_ready(
            guild_id,
            timeout_sec=timeout_sec,
            poll_sec=poll_sec,
        )
        return observed

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
        *,
        world_lease: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self.deps.get_client()
        start_kwargs: dict[str, Any] = {"goal": goal}
        if world_lease:
            start_kwargs["world_lease"] = dict(world_lease)
        started = await client.start(**start_kwargs)
        observed, ready = await self._wait_for_minecraft_ready(
            guild_id
        )
        merged = dict(observed) if isinstance(observed, dict) else {}
        merged["voyager_repo_present"] = (
            started.get("voyager_repo_present")
            if isinstance(started, dict)
            else None
        )
        if not ready or not minecraft_connection_confirmed(merged):
            raise RuntimeError("minecraft_start_unverified")
        merged["outcome_verified"] = True
        merged["outcome_code"] = MINECRAFT_CONNECTED_OUTCOME
        parent_record_ids = _archive_parent_record_ids(world_lease)
        if parent_record_ids:
            await client.archive_lifecycle_result(
                guild_id=guild_id,
                parent_record_ids=parent_record_ids,
                operation="connect",
                outcome_code=MINECRAFT_CONNECTED_OUTCOME,
            )
        return merged

    async def disable_minecraft_mode(
        self,
        guild_id: int,
        *,
        parent_record_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        client = self.deps.get_client()
        stopped = await client.stop()
        observed = await self.wait_for_minecraft_stopped(
            guild_id,
            initial_status=stopped,
        )
        if not minecraft_stop_confirmed(observed):
            raise RuntimeError("minecraft_stop_unverified")
        observed["outcome_verified"] = True
        observed["outcome_code"] = MINECRAFT_STOPPED_OUTCOME
        if parent_record_ids:
            await client.archive_lifecycle_result(
                guild_id=guild_id,
                parent_record_ids=parent_record_ids,
                operation="disconnect",
                outcome_code=MINECRAFT_STOPPED_OUTCOME,
            )
        return observed


__all__ = [
    "MINECRAFT_CONNECTED_OUTCOME",
    "MINECRAFT_STOPPED_OUTCOME",
    "MinecraftModeComposition",
    "MinecraftModeCompositionDeps",
    "minecraft_connection_confirmed",
    "minecraft_stop_confirmed",
]
