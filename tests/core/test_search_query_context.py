from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.search_query_context import (  # noqa: E402
    build_search_query_from_context,
    enrich_weather_search_query_from_context,
    recent_user_text_candidates,
    resolve_contextual_search_query,
)


class SearchQueryContextTests(unittest.TestCase):
    def test_recent_user_candidates_skips_current_text_and_non_user_rows(self) -> None:
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "서울 날씨 알려줘"},
            {"role": "assistant", "content": "응"},
            {"role": "user", "content": "검색해봐"},
        ]

        self.assertEqual(
            recent_user_text_candidates(messages, exclude_text="검색해봐"),
            ["서울 날씨 알려줘"],
        )

    def test_contextual_search_uses_previous_user_request_for_generic_followup(self) -> None:
        messages = [
            {"role": "user", "content": "RTX 5090 가격"},
            {"role": "assistant", "content": "대략 비싸"},
            {"role": "user", "content": "검색해봐"},
        ]

        self.assertEqual(resolve_contextual_search_query("검색해봐", messages=messages), "RTX 5090 가격")

    def test_weather_query_uses_recent_or_memory_location(self) -> None:
        messages = [{"role": "user", "content": "서울 내일 날씨"}]

        self.assertEqual(enrich_weather_search_query_from_context("오늘 날씨", messages=messages), "서울 오늘 날씨")
        self.assertEqual(
            enrich_weather_search_query_from_context("날씨", messages=[], memory_summary="사용자는 부산에 자주 간다"),
            "부산 날씨",
        )
        self.assertEqual(enrich_weather_search_query_from_context("서울 날씨", messages=[]), "서울 날씨")

    def test_build_search_query_from_context_falls_back_to_memory_for_short_query(self) -> None:
        self.assertEqual(
            build_search_query_from_context("검색", messages=[{"role": "user", "content": "오픈AI 최신 모델"}]),
            "오픈AI 최신 모델",
        )
        self.assertEqual(
            build_search_query_from_context("짧음", memory_summary="이블린 main.py 분리", has_memory_scope=True),
            "짧음 이블린 main.py 분리",
        )
        self.assertEqual(build_search_query_from_context("긴 검색어입니다", has_memory_scope=False), "긴 검색어입니다")


if __name__ == "__main__":
    unittest.main()
