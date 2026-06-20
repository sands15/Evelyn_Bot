import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.route_fallback_policy import (  # noqa: E402
    classify_llm_route_fallback,
    normalize_route_name,
    should_force_voice_context_route,
)


class RouteFallbackPolicyTests(unittest.TestCase):
    def test_normalizes_known_route_aliases(self) -> None:
        self.assertEqual(normalize_route_name("fresh-sub"), "sub_wait")
        self.assertEqual(normalize_route_name("memory_context"), "sub_hint")
        self.assertEqual(normalize_route_name("unknown"), "main_direct")

    def test_voice_context_force_uses_memory_and_continuation_markers(self) -> None:
        self.assertFalse(should_force_voice_context_route(""))
        self.assertFalse(should_force_voice_context_route("안녕"))
        self.assertTrue(should_force_voice_context_route("아까 하던 거 계속"))
        self.assertTrue(should_force_voice_context_route("우리가 먹기로 한 거 뭐였지"))

    def test_fallback_route_keeps_short_text_direct_and_contextual_text_hint_or_wait(self) -> None:
        self.assertEqual(classify_llm_route_fallback("안녕", source="text"), "main_direct")
        self.assertEqual(
            classify_llm_route_fallback("아까 얘기한 내용 기준으로 지금 답변을 차분하게 구성해줘", source="text"),
            "sub_hint",
        )
        self.assertEqual(
            classify_llm_route_fallback(
                "아까 전에 말했던 내용이랑 지금 상황을 비교해서 왜 그렇게 판단했는지 자세히 설명해줘",
                source="text",
            ),
            "sub_wait",
        )
        self.assertEqual(classify_llm_route_fallback("그거 해줘", source="voice"), "main_direct")
        self.assertEqual(classify_llm_route_fallback("아까 말했던 거 이어서", source="voice"), "sub_wait")


if __name__ == "__main__":
    unittest.main()
