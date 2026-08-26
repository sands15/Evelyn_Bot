from __future__ import annotations

import sys
import unittest
import types
from pathlib import Path
from types import SimpleNamespace

from typing import Callable

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

try:
    import aiohttp as _aiohttp  # noqa: F401
except ImportError:
    class _DummyClientTimeout:
        def __init__(self, **_kwargs: object) -> None:
            self.params = _kwargs

    sys.modules["aiohttp"] = types.SimpleNamespace(ClientTimeout=_DummyClientTimeout)

try:
    import numpy as _numpy  # noqa: F401
except ImportError:
    class _DummyNdArray:
        pass

    sys.modules["numpy"] = types.SimpleNamespace(ndarray=_DummyNdArray)

from evelyn_core.voice_response_runtime import (  # noqa: E402
    MainResponseGuidanceRuntimeDeps,
    build_main_response_guidance_from_runtime,
)


def _build_deps(
    *,
    tool_awareness_context: str = "",
    persona_hint: str = "",
    recent_summary: str = "",
    route_available_result: bool = True,
    question_feature_enabled: bool = True,
    minecraft_summary: str = "마인크래프트 상태 없음",
    apply_ask: Callable | None = None,
) -> MainResponseGuidanceRuntimeDeps:
    return MainResponseGuidanceRuntimeDeps(
        clean_text=lambda value: value.strip(),
        apply_ask_gating=(
            apply_ask
            if apply_ask is not None
            else (lambda cognitive_state, source: {"action": "answer", "user_intent": "check"})
        ),
        persona_state_hint_for_turn=lambda user_text, **kwargs: persona_hint,
        recent_assistant_reply_summary=lambda **kwargs: recent_summary,
        build_tool_awareness_context=lambda user_text, **kwargs: tool_awareness_context,
        route_available=lambda route_name, source: route_available_result and route_name == "evelyn.gpt",
        format_minecraft_state_summary=lambda state: minecraft_summary,
        question_feature_enabled=question_feature_enabled,
    )


class VoiceResponseRuntimeTests(unittest.TestCase):
    def test_build_main_response_guidance_includes_runtime_context_and_minecraft_status(self) -> None:
        deps = _build_deps(
            persona_hint="user_friendly",
            recent_summary="이전에 한 말",
            tool_awareness_context="tool-aware",
            route_available_result=True,
        )
        result = build_main_response_guidance_from_runtime(
            {"user_intent": "search"},
            user_text="안녕",
            session_key="session-1",
            guild_id=7,
            minecraft_state={"status": "idle"},
            runtime_status_context="gpu_ok",
            route_decision=SimpleNamespace(
                ask_mode="ask",
                max_question_count=0,
                question_hint="hint",
                question_reason="reason",
            ),
            deps=deps,
        )

        self.assertIn("응답 규칙", result)
        self.assertIn("user_friendly", result)
        self.assertNotIn("최근 네 말: 이전에 한 말", result)
        self.assertIn("현재 Evelyn 런타임 상태 요약: gpu_ok", result)
        self.assertIn("현재 마인크래프트 실시간 상태: 마인크래프트 상태 없음", result)
        self.assertIn("tool-aware", result)
        self.assertIn("답변 끝에 새 질문을 덧붙이지 마라.", result)

    def test_build_main_response_guidance_adds_question_hint(self) -> None:
        route = SimpleNamespace(
            ask_mode="ask",
            max_question_count=1,
            question_hint="다음 단계가 뭐야",
            question_reason="의도 확인",
        )
        deps = _build_deps(
            question_feature_enabled=True,
            tool_awareness_context="",
            persona_hint="",
            recent_summary="",
            minecraft_summary="",
            apply_ask=lambda cognitive_state, source: {"action": "ask", "user_intent": None},
        )
        result = build_main_response_guidance_from_runtime(
            {"user_intent": "ask"},
            user_text="다음으로 뭐하지",
            route_decision=route,
            deps=deps,
        )

        self.assertIn("짧게 확인 질문만 해라.", result)
        self.assertIn("질문 방향: 다음 단계가 뭐야", result)
        self.assertIn("질문이 필요한 이유: 의도 확인", result)
        self.assertNotIn("답변 끝에 새 질문을 덧붙이지 마라.", result)


if __name__ == "__main__":
    unittest.main()
