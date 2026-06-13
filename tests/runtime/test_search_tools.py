from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.search_tools import (  # noqa: E402
    SearchResult,
    decode_duckduckgo_url,
    normalize_search_query,
    render_search_results_for_llm,
    strip_html_tags,
    strip_search_command_words,
    weather_location_from_query,
)


class SearchToolsTests(unittest.TestCase):
    def test_decode_duckduckgo_redirect_url(self) -> None:
        url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage"

        self.assertEqual(decode_duckduckgo_url(url), "https://example.com/page")

    def test_strip_html_tags_compacts_text(self) -> None:
        self.assertEqual(strip_html_tags("<b>Hello</b> &amp; <i>world</i>"), "Hello & world")

    def test_weather_query_adds_today_when_time_is_missing(self) -> None:
        self.assertEqual(normalize_search_query("날씨 알려줘"), "오늘 서울 날씨")
        self.assertEqual(normalize_search_query("오늘 날씨 알려줘"), "서울 오늘 날씨")

    def test_search_query_removes_command_tail(self) -> None:
        self.assertEqual(strip_search_command_words("OpenAI latest model news search"), "OpenAI latest model news")
        self.assertEqual(strip_search_command_words("OpenAI latest model news look up"), "OpenAI latest model news")
        self.assertEqual(strip_search_command_words("OpenAI 최신 모델 뉴스 검색해줘"), "OpenAI 최신 모델 뉴스")

    def test_weather_location_defaults_to_seoul(self) -> None:
        self.assertEqual(weather_location_from_query("오늘 날씨"), "서울")
        self.assertEqual(weather_location_from_query("오늘 부산 날씨"), "부산")

    def test_render_search_results_for_llm_includes_snippets_and_url_rule(self) -> None:
        rendered = render_search_results_for_llm(
            "weather today",
            [SearchResult(title="Weather", snippet="Rain later today.", url="https://example.test/weather")],
        )

        self.assertIn("Search tool result", rendered)
        self.assertIn("query=weather today", rendered)
        self.assertIn("Rain later today", rendered)
        self.assertIn("Do not print raw URLs", rendered)


if __name__ == "__main__":
    unittest.main()
