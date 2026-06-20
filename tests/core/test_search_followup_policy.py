from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.search_followup_policy import (  # noqa: E402
    answer_promises_search,
    is_generic_search_followup_text,
    is_underspecified_weather_query,
    strip_search_answer_sources,
)


class SearchFollowupPolicyTests(unittest.TestCase):
    def test_answer_promises_search_detects_tags_and_promises_not_completed_answers(self) -> None:
        self.assertTrue(answer_promises_search("[찾기] 확인해볼게"))
        self.assertTrue(answer_promises_search("자료 찾아볼게"))
        self.assertTrue(answer_promises_search("I'll look up the exact release date."))
        self.assertFalse(answer_promises_search("찾아보니 결과는 이미 나왔어."))
        self.assertFalse(answer_promises_search("그냥 답할게."))

    def test_strip_search_answer_sources_removes_urls_and_source_lines(self) -> None:
        text = strip_search_answer_sources(
            "결과는 이거야 (https://example.com)\n"
            "출처: https://source.example\n"
            "참고 [www.example.org]"
        )

        self.assertEqual(text, "결과는 이거야")

    def test_search_followup_short_query_classifiers(self) -> None:
        self.assertTrue(is_generic_search_followup_text("찾아보고 말해줘"))
        self.assertFalse(is_generic_search_followup_text("서울 날씨 찾아봐"))
        self.assertTrue(is_underspecified_weather_query("오늘 날씨"))
        self.assertFalse(is_underspecified_weather_query("서울 오늘 날씨"))


if __name__ == "__main__":
    unittest.main()
