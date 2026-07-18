from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ControlPageServerStartRuntimeDeps:
    enabled: bool
    docs_dir: Any
    host: str
    port: int
    routes: tuple[tuple[str, str, Any], ...]
    middleware: Any
    get_runner: Callable[[], Any | None]
    set_runner: Callable[[Any], None]
    set_site: Callable[[Any], None]
    get_start_lock: Callable[[], Any | None]
    set_start_lock: Callable[[Any], None]
    lock_factory: Callable[[], Any]
    application_factory: Callable[..., Any]
    app_runner_factory: Callable[..., Any]
    tcp_site_factory: Callable[..., Any]
    mark_startup_component: Callable[[str, str, str], Any]
    local_url: Callable[[], str]
    log: Callable[[str], Any]


def _register_routes(app: Any, routes: tuple[tuple[str, str, Any], ...]) -> None:
    registrars = {
        "GET": app.router.add_get,
        "POST": app.router.add_post,
        "OPTIONS": app.router.add_options,
    }
    for method, path, handler in routes:
        try:
            registrar = registrars[method]
        except KeyError as exc:
            raise ValueError(f"unsupported control page route method: {method}") from exc
        registrar(path, handler)


async def start_control_page_server_from_runtime(*, deps: ControlPageServerStartRuntimeDeps) -> None:
    if not deps.enabled:
        return
    if deps.get_runner() is not None:
        return
    lock = deps.get_start_lock()
    if lock is None:
        lock = deps.lock_factory()
        deps.set_start_lock(lock)
    async with lock:
        if deps.get_runner() is not None:
            return
        if not deps.docs_dir.exists():
            deps.log(f"[CONTROL PAGE] docs_missing path={deps.docs_dir}")
            return
        app = deps.application_factory(middlewares=[deps.middleware])
        _register_routes(app, deps.routes)
        runner = deps.app_runner_factory(app, access_log=None)
        try:
            await runner.setup()
            site = deps.tcp_site_factory(runner, host=deps.host, port=deps.port)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        deps.set_runner(runner)
        deps.set_site(site)
        local_url = deps.local_url()
        deps.mark_startup_component("control_api", "done", local_url)
        deps.log(f"[CONTROL PAGE] live url={local_url}")


__all__ = ["ControlPageServerStartRuntimeDeps", "start_control_page_server_from_runtime"]
