from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

_runtime_import_error: str | None = None
try:
    from evelyn_core.autonomy_router import (  # noqa: E402
        ResolveRouteExecutorRuntimeDeps,
        resolve_route_executor_from_runtime,
    )
except ModuleNotFoundError as exc:
    _runtime_import_error = exc.name
except Exception as exc:  # noqa: BLE001
    _runtime_import_error = f"import:{exc.__class__.__name__}"

if _runtime_import_error == "numpy":
    _SKIP_REASON = "테스트 대상 런타임 의존성(numpy) 미설치로 실행 스킵"
elif _runtime_import_error is not None:
    _SKIP_REASON = f"런타임 import 실패: {_runtime_import_error}"
else:
    _SKIP_REASON = ""


class ResolveRouteExecutorRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if _SKIP_REASON:
            raise unittest.SkipTest(_SKIP_REASON)

    def test_none_guild_returns_none(self) -> None:
        calls: list[tuple] = []
        deps = ResolveRouteExecutorRuntimeDeps(
            get_autonomy_engine=lambda guild_id: calls.append(("get", guild_id)) or None,
            create_autonomy_engine=lambda guild_id: calls.append(("create", guild_id)) or types.SimpleNamespace(executor=types.SimpleNamespace(executors={})),
        )
        self.assertIsNone(resolve_route_executor_from_runtime(None, "minecraft", deps=deps))
        self.assertEqual(calls, [])

    def test_existing_engine_route_executor_returned(self) -> None:
        engine = types.SimpleNamespace(executor=types.SimpleNamespace(executors={"minecraft": object()}))
        deps = ResolveRouteExecutorRuntimeDeps(
            get_autonomy_engine=lambda guild_id: engine,
            create_autonomy_engine=lambda _guild_id: (_ for _ in ()).throw(RuntimeError("unexpected create")),
        )
        result = resolve_route_executor_from_runtime(11, "minecraft", deps=deps)
        self.assertIs(result, engine.executor.executors["minecraft"])

    def test_missing_engine_only_creates_for_minecraft(self) -> None:
        created: list[int] = []
        created_executor = object()
        created_engine = types.SimpleNamespace(
            executor=types.SimpleNamespace(executors={"minecraft": created_executor, "vision": object()}),
        )

        deps = ResolveRouteExecutorRuntimeDeps(
            get_autonomy_engine=lambda _guild_id: None,
            create_autonomy_engine=lambda guild_id: created.append(guild_id) or created_engine,
        )
        result = resolve_route_executor_from_runtime(13, "minecraft", deps=deps)
        self.assertEqual(created, [13])
        self.assertIs(result, created_executor)

    def test_missing_engine_non_minecraft_is_none(self) -> None:
        created: list[int] = []
        deps = ResolveRouteExecutorRuntimeDeps(
            get_autonomy_engine=lambda _guild_id: None,
            create_autonomy_engine=lambda guild_id: created.append(guild_id) or None,
        )
        self.assertIsNone(resolve_route_executor_from_runtime(13, "vision", deps=deps))
        self.assertEqual(created, [])


if __name__ == "__main__":
    unittest.main()
