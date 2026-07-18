from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

@dataclass(frozen=True)
class MinecraftModeCompositionDeps:
    get_client: Callable[[], Any]
    merge_status: Callable[..., dict[str, Any] | None]
    clean_text: Callable[[str], str]
    monotonic: Callable[[], float]
    sleep: Callable[[float], Awaitable[Any]]

class MinecraftModeComposition:
    def __init__(self, deps: MinecraftModeCompositionDeps) -> None:
        self.deps=deps

    async def wait_for_minecraft_ready(self, guild_id: int, *, timeout_sec: float=12.0, poll_sec: float=1.0) -> dict[str, Any]:
        _=guild_id; deps=self.deps; deadline=deps.monotonic()+max(0.5,timeout_sec); last_observed={}; client=deps.get_client()
        while deps.monotonic()<deadline:
            status=await client.status(); observed=status.get("observation") if isinstance(status.get("observation"),dict) else status
            if isinstance(observed,dict):
                last_observed=deps.merge_status(status,observed) or dict(observed)
                if status.get("connected") or observed.get("connected") or observed.get("active") or observed.get("position"):
                    return last_observed
                last_error=deps.clean_text(str(status.get("last_error") or observed.get("last_error") or ""))
                if last_error: last_observed["wait_last_error"]=last_error
            await deps.sleep(max(0.1,poll_sec))
        return last_observed or {"connected":False,"active":False,"last_error":"timeout_waiting_for_voyager_service"}

    async def enable_minecraft_mode(self, guild_id: int, goal: str | None=None) -> dict[str, Any]:
        client=self.deps.get_client(); started=await client.start(goal=goal); observed=await self.wait_for_minecraft_ready(guild_id)
        merged=self.deps.merge_status(started if isinstance(started,dict) else None,observed if isinstance(observed,dict) else None) or {}
        merged["voyager_repo_present"]=started.get("voyager_repo_present") if isinstance(started,dict) else None
        return merged

    async def disable_minecraft_mode(self, guild_id: int) -> None:
        _=guild_id; await self.deps.get_client().stop()
