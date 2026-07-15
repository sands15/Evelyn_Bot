from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_server_start_runtime import (  # noqa: E402
    ControlPageServerStartRuntimeDeps,
    start_control_page_server_from_runtime,
)


class FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeRouter:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, object]] = []

    def add_get(self, path: str, handler) -> None:
        self.routes.append(("GET", path, handler))

    def add_post(self, path: str, handler) -> None:
        self.routes.append(("POST", path, handler))

    def add_options(self, path: str, handler) -> None:
        self.routes.append(("OPTIONS", path, handler))


class FakeRunner:
    def __init__(self, app, *, access_log=None) -> None:
        self.app = app
        self.access_log = access_log
        self.setup_calls = 0
        self.cleanup_calls = 0
        self.setup_error: Exception | None = None

    async def setup(self) -> None:
        self.setup_calls += 1
        if self.setup_error is not None:
            raise self.setup_error

    async def cleanup(self) -> None:
        self.cleanup_calls += 1


class FakeSite:
    def __init__(self, runner, *, host: str, port: int) -> None:
        self.runner = runner
        self.host = host
        self.port = port
        self.start_calls = 0

    async def start(self) -> None:
        self.start_calls += 1


class FakeDocsDir:
    def __init__(self, exists: bool) -> None:
        self._exists = exists

    def exists(self) -> bool:
        return self._exists

    def __str__(self) -> str:
        return "docs"


class ControlPageServerStartRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.runner = None
        self.site = None
        self.lock = None
        self.apps: list[object] = []
        self.created_runner: FakeRunner | None = None
        self.marks: list[tuple[str, str, str]] = []
        self.logs: list[str] = []
        self.handler = object()

    def application_factory(self, **kwargs):
        app = SimpleNamespace(router=FakeRouter(), middlewares=kwargs.get("middlewares"))
        self.apps.append(app)
        return app

    def runner_factory(self, app, *, access_log=None):
        self.created_runner = FakeRunner(app, access_log=access_log)
        return self.created_runner

    def build_deps(self, *, enabled: bool = True, docs_exist: bool = True) -> ControlPageServerStartRuntimeDeps:
        return ControlPageServerStartRuntimeDeps(
            enabled=enabled,
            docs_dir=FakeDocsDir(docs_exist),
            host="127.0.0.1",
            port=8799,
            routes=(
                ("GET", "/health", self.handler),
                ("POST", "/chat", self.handler),
                ("OPTIONS", "/chat", self.handler),
            ),
            middleware="cors",
            get_runner=lambda: self.runner,
            set_runner=lambda runner: setattr(self, "runner", runner),
            set_site=lambda site: setattr(self, "site", site),
            get_start_lock=lambda: self.lock,
            set_start_lock=lambda lock: setattr(self, "lock", lock),
            lock_factory=FakeLock,
            application_factory=self.application_factory,
            app_runner_factory=self.runner_factory,
            tcp_site_factory=FakeSite,
            mark_startup_component=lambda *args: self.marks.append(args),
            local_url=lambda: "http://127.0.0.1:8799",
            log=self.logs.append,
        )

    async def test_disabled_or_existing_runner_returns_without_setup(self) -> None:
        await start_control_page_server_from_runtime(deps=self.build_deps(enabled=False))
        self.assertEqual(self.apps, [])

        self.runner = object()
        await start_control_page_server_from_runtime(deps=self.build_deps())
        self.assertEqual(self.apps, [])

    async def test_missing_docs_logs_and_does_not_create_application(self) -> None:
        await start_control_page_server_from_runtime(deps=self.build_deps(docs_exist=False))

        self.assertIsNotNone(self.lock)
        self.assertEqual(self.apps, [])
        self.assertEqual(self.logs, ["[CONTROL PAGE] docs_missing path=docs"])

    async def test_success_registers_routes_starts_site_and_publishes_state(self) -> None:
        await start_control_page_server_from_runtime(deps=self.build_deps())

        self.assertEqual(self.apps[0].middlewares, ["cors"])
        self.assertEqual(
            [(method, path) for method, path, _handler in self.apps[0].router.routes],
            [("GET", "/health"), ("POST", "/chat"), ("OPTIONS", "/chat")],
        )
        self.assertEqual(self.created_runner.setup_calls, 1)
        self.assertEqual(self.created_runner.access_log, None)
        self.assertEqual(self.site.host, "127.0.0.1")
        self.assertEqual(self.site.port, 8799)
        self.assertEqual(self.site.start_calls, 1)
        self.assertEqual(self.marks, [("control_api", "done", "http://127.0.0.1:8799")])

    async def test_setup_failure_cleans_runner_and_propagates(self) -> None:
        deps = self.build_deps()
        original_factory = deps.app_runner_factory

        def failing_factory(*args, **kwargs):
            runner = original_factory(*args, **kwargs)
            runner.setup_error = RuntimeError("bind failed")
            return runner

        deps = ControlPageServerStartRuntimeDeps(**{**deps.__dict__, "app_runner_factory": failing_factory})

        with self.assertRaisesRegex(RuntimeError, "bind failed"):
            await start_control_page_server_from_runtime(deps=deps)

        self.assertEqual(self.created_runner.cleanup_calls, 1)
        self.assertIsNone(self.runner)

    def test_main_delegates_control_page_server_start_to_runtime_module(self) -> None:
        source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def start_server(")
        end = source.index("class ControlPageHttpComposition", start)
        function_source = source[start:end]

        self.assertIn("start_control_page_server_from_runtime(", function_source)
        self.assertNotIn("web.AppRunner(", function_source)
        self.assertNotIn("app.router.add_get", function_source)


if __name__ == "__main__":
    unittest.main()
