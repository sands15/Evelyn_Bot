from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.response_context_composition import (
    ResponseContextComposition,
    ResponseContextCompositionDeps,
)


class ResponseContextCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, **overrides):
        values = dict(
            runtime_status_enabled=False,
            runtime_status_refresh_sec=30.0,
            control_page_host="127.0.0.1",
            control_page_port=8799,
            llm_server_url="http://127.0.0.1:8000",
            router_llm_url="http://127.0.0.1:8001",
            summary_llm_url="http://127.0.0.1:8002",
            omnivoice_server_url="http://127.0.0.1:8880",
            minecraft_autonomy_service_port=9820,
            voyager_action_backend="codex-gateway",
            voyager_codex_gateway_port=9822,
            get_control_page_runtime_services=AsyncMock(return_value={}),
            is_control_api_ready_from_runtime_services=Mock(return_value=True),
            probe_runtime_tcp_service=AsyncMock(return_value=("service", True)),
            load_runtime_gpu_status=Mock(return_value=("gpu=ok", True)),
            load_runtime_recent_errors=Mock(return_value=[]),
            now=Mock(return_value=100.0),
            clean_text=lambda value: str(value).strip(),
            apply_ask_gating=lambda state, **_kwargs: state or {"action": "answer"},
            persona_state_hint_for_turn=Mock(return_value=""),
            recent_assistant_reply_summary=Mock(return_value=""),
            build_tool_awareness_context=Mock(return_value=""),
            skill_registry=SimpleNamespace(find_by_route=Mock(return_value=object())),
            format_minecraft_state_summary=Mock(return_value=""),
            question_feature_enabled=True,
        )
        values.update(overrides)
        return ResponseContextComposition(ResponseContextCompositionDeps(**values))

    async def test_disabled_runtime_status_returns_empty_text(self) -> None:
        composition = self.build()
        self.assertEqual(await composition.build_runtime_status_context(), "")

    def test_skill_route_available_contains_registry_failure(self) -> None:
        registry = SimpleNamespace(find_by_route=Mock(side_effect=RuntimeError("boom")))
        composition = self.build(skill_registry=registry)
        self.assertFalse(composition.skill_route_available("search", source="text"))

    def test_guidance_uses_composition_runtime_deps(self) -> None:
        composition = self.build()
        result = composition.build_main_response_guidance(
            {"action": "answer"}, source="text", user_text="hello"
        )
        self.assertIn("응답 규칙", result)

    def test_main_uses_explicit_bindings(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("response_context_composition = ResponseContextComposition(", source)
        self.assertIn(
            "build_runtime_status_context = response_context_composition.build_runtime_status_context",
            source,
        )
        self.assertIn(
            "build_main_response_guidance = response_context_composition.build_main_response_guidance",
            source,
        )


if __name__ == "__main__":
    unittest.main()
