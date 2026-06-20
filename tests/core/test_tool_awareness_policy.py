import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.tool_awareness_policy import build_tool_awareness_context  # noqa: E402


class _RouteDecision:
    def __init__(self, *, needs_search: bool = False, action: str = "") -> None:
        self.needs_search = needs_search
        self.action = action


def _route_available(route_name: str, *, source: str) -> bool:
    return route_name == "search_executor" and source in {"text", "voice", "control_page"}


class ToolAwarenessPolicyTests(unittest.TestCase):
    def test_search_marker_adds_search_tool_when_route_is_available(self) -> None:
        context = build_tool_awareness_context(
            "오늘 날씨 찾아봐",
            source="text",
            route_available=_route_available,
        )

        self.assertIn("TOOL_AWARENESS: Runtime, not memory", context)
        self.assertIn("- search: use for current info, weather, prices, news", context)
        self.assertIn("do not give only a promise", context)

    def test_route_decision_can_request_search_without_text_marker(self) -> None:
        context = build_tool_awareness_context(
            "그거 해줘",
            route_decision=_RouteDecision(action="search_then_answer"),
            route_available=_route_available,
        )

        self.assertIn("- search: use for current info", context)

    def test_runtime_and_minecraft_markers_do_not_require_search_route(self) -> None:
        context = build_tool_awareness_context(
            "이블린 서버 상태랑 마크 인벤 확인",
            route_available=lambda *args, **kwargs: False,
        )

        self.assertIn("runtime.status", context)
        self.assertIn("minecraft.status", context)

    def test_returns_empty_when_no_tool_is_relevant_or_available(self) -> None:
        self.assertEqual(build_tool_awareness_context("그냥 안녕"), "")


if __name__ == "__main__":
    unittest.main()
