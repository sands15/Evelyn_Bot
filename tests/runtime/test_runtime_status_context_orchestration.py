from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_status_context import (  # noqa: E402
    RuntimeStatusContextDeps,
    RuntimeStatusContextState,
    build_runtime_status_context_from_runtime,
)


class RuntimeStatusContextOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now_value = 100.0
        self.probes: list[tuple[str, str, int]] = []
        self.down_labels: set[str] = set()
        self.services = {"summary": "all ready", "botApiReady": True}
        self.service_error: Exception | None = None
        self.gpu_result = ("gpu0 RTX used=1/10MB", False)
        self.recent_errors: list[str] = []

    async def probe(self, label: str, host: str, port: int) -> tuple[str, bool]:
        self.probes.append((label, host, port))
        return label, label not in self.down_labels

    async def get_services(self) -> dict:
        if self.service_error is not None:
            raise self.service_error
        return self.services

    def build_deps(self, *, enabled: bool = True, backend: str = "codex-gateway") -> RuntimeStatusContextDeps:
        return RuntimeStatusContextDeps(
            enabled=enabled,
            refresh_sec=30.0,
            control_page_host="127.0.0.1",
            control_page_port=8799,
            llm_server_url="http://127.0.0.1:9820/v1",
            router_llm_url="http://127.0.0.1:9821/v1",
            summary_llm_url="http://127.0.0.1:9822/v1",
            omnivoice_server_url="http://127.0.0.1:8880",
            minecraft_autonomy_service_port=3000,
            voyager_action_backend=backend,
            voyager_codex_gateway_port=3001,
            get_control_page_runtime_services=self.get_services,
            is_control_api_ready_from_runtime_services=lambda services: bool(services.get("botApiReady")),
            probe_runtime_tcp_service=self.probe,
            load_runtime_gpu_status=lambda: self.gpu_result,
            load_runtime_recent_errors=lambda: self.recent_errors,
            now=lambda: self.now_value,
        )

    async def test_disabled_context_returns_without_creating_lock(self) -> None:
        state = RuntimeStatusContextState()

        result = await build_runtime_status_context_from_runtime(deps=self.build_deps(enabled=False), state=state)

        self.assertEqual(result, "")
        self.assertIsNone(state.lock)
        self.assertEqual(self.probes, [])

    async def test_fresh_cache_returns_without_probe(self) -> None:
        state = RuntimeStatusContextState(cache={"text": "cached", "cached_at": 90.0})

        result = await build_runtime_status_context_from_runtime(deps=self.build_deps(), state=state)

        self.assertEqual(result, "cached")
        self.assertEqual(self.probes, [])

    async def test_builds_service_gpu_oom_and_historical_error_context(self) -> None:
        self.down_labels = {"tts"}
        self.services = {"summary": "partial readiness", "botApiReady": False, "botApiReason": "starting"}
        self.gpu_result = ("gpu0 RTX used=9500/10000MB", True)
        self.recent_errors = ["old OOM"]
        state = RuntimeStatusContextState()

        result = await build_runtime_status_context_from_runtime(deps=self.build_deps(), state=state)

        labels = [label for label, _host, _port in self.probes]
        self.assertEqual(labels, [
            "bot/control",
            "main_llm",
            "router_llm",
            "sub_llm",
            "tts",
            "voyager_service",
            "codex_gateway",
        ])
        self.assertIn("tts=down", result)
        self.assertIn("summary=partial readiness", result)
        self.assertIn("current_gpu_snapshot=gpu0 RTX", result)
        self.assertIn("current_oom_signal=yes", result)
        self.assertIn("gpu_near_full", result)
        self.assertIn("bot_api:starting", result)
        self.assertIn("recent_errors=old OOM", result)
        self.assertIn("recent_errors_are_historical=true", result)
        self.assertEqual(state.cache["text"], result)
        self.assertEqual(state.cache["cached_at"], 100.0)

    async def test_force_refresh_bypasses_fresh_cache(self) -> None:
        state = RuntimeStatusContextState(cache={"text": "stale-for-force", "cached_at": 99.0})

        result = await build_runtime_status_context_from_runtime(
            deps=self.build_deps(backend="direct"),
            state=state,
            force=True,
        )

        self.assertNotEqual(result, "stale-for-force")
        self.assertNotIn("codex_gateway", [label for label, _host, _port in self.probes])
        self.assertIn("current_oom_signal=no", result)

    async def test_runtime_services_failure_does_not_abort_probe_context(self) -> None:
        self.service_error = RuntimeError("service cache unavailable")
        state = RuntimeStatusContextState()

        result = await build_runtime_status_context_from_runtime(deps=self.build_deps(), state=state)

        self.assertIn("bot/control=up", result)
        self.assertNotIn("summary=", result)
        self.assertIn("recent_errors=none", result)

    def test_main_delegates_status_context_to_runtime_state(self) -> None:
        composition_path = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "response_context_composition.py"
        )
        source = composition_path.read_text(encoding="utf-8")
        start = source.index("async def build_runtime_status_context(")
        end = source.index("def skill_route_available(", start)
        function_source = source[start:end]

        self.assertIn("build_runtime_status_context_from_runtime(", function_source)
        self.assertIn("state=self.runtime_status_state", function_source)
        self.assertNotIn("asyncio.gather(", function_source)


if __name__ == "__main__":
    unittest.main()
