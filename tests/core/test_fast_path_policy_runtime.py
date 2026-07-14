from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from unittest import TestCase

from evelyn_core.fast_path_policy import (
    FastPathPolicyRuntimeDeps,
    context_policy_for_fast_path_policy_from_runtime,
    deep_route_marker_count_from_runtime,
    fast_path_policy_from_runtime,
    has_negated_search_marker_from_runtime,
    is_control_page_source_from_runtime,
    is_obvious_continue_from_runtime,
    is_simple_directive_from_runtime,
    needs_search_or_deep_routing_from_runtime,
)


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


class FastPathPolicyRuntimeTests(TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        self.deps = FastPathPolicyRuntimeDeps(
            clean_text=_normalize,
            normalize_voice_text=_normalize,
            should_force_search_query=self._should_force,
            control_page_source_aliases=("control_page", "control-page", "local_control_page"),
            control_page_light_request_max_chars=180,
            fast_path_search_markers=("검색", "찾아", "최신", "뉴스", "시세", "가격", "환율"),
            fast_path_search_route_markers=("검색", "찾아봐", "찾아 봐", "찾아"),
            fast_path_negated_search_markers=("검색 없이", "do not search"),
            fast_path_directive_markers=("알려줘", "해줘"),
            fast_path_continue_markers=("그리고", "그 다음", "이어"),
            fast_path_deep_route_markers=("검색", "찾아봐", "최신", "뉴스"),
        )

    def _should_force(self, text: str) -> bool:
        self.calls.append(text)
        return "검색" in text

    def test_control_page_source(self) -> None:
        self.assertTrue(is_control_page_source_from_runtime("control-page", deps=self.deps))
        self.assertFalse(is_control_page_source_from_runtime("unknown", deps=self.deps))

    def test_deep_marker_count_ignores_search_markers(self) -> None:
        self.assertEqual(
            deep_route_marker_count_from_runtime("최신 뉴스 검색", ignore_search_markers=True, deps=self.deps),
            2,
        )
        self.assertEqual(
            deep_route_marker_count_from_runtime("최신 뉴스 검색", ignore_search_markers=False, deps=self.deps),
            3,
        )

    def test_negated_search_marker_and_routing(self) -> None:
        self.assertTrue(has_negated_search_marker_from_runtime("검색 없이 알려줘", deps=self.deps))
        self.assertFalse(has_negated_search_marker_from_runtime("검색해줘", deps=self.deps))

        self.assertTrue(needs_search_or_deep_routing_from_runtime("뉴스 가격 확인", source="text", deps=self.deps))
        self.assertFalse(needs_search_or_deep_routing_from_runtime("짧은 일반 인사", source="control-page", deps=self.deps))

    def test_simple_and_continue_checks(self) -> None:
        self.assertTrue(is_simple_directive_from_runtime("정리해줘", source="text", deps=self.deps))
        self.assertFalse(is_simple_directive_from_runtime("검색해줘", source="text", deps=self.deps))
        self.assertTrue(
            is_obvious_continue_from_runtime(
                "그리고",
                "voice",
                room_state={"reply_in_progress": True},
                deps=self.deps,
            )
        )
        self.assertFalse(
            is_obvious_continue_from_runtime(
                "그러니까 아주 긴 문장으로 이어서",
                "voice",
                room_state={"reply_in_progress": True},
                deps=self.deps,
            )
        )

    def test_fast_path_policy(self) -> None:
        self.assertEqual(
            fast_path_policy_from_runtime("알려줘", "text", deps=self.deps),
            {"route": "main_direct", "action": "answer", "reason_brief": "simple_directive"},
        )
        self.assertEqual(
            fast_path_policy_from_runtime("검색해줘", "text", deps=self.deps),
            {"route": "search_executor", "action": "search_then_answer", "reason_brief": "search_trigger"},
        )
        self.assertIsNone(fast_path_policy_from_runtime("control-page 페이지 조회", "control-page", deps=self.deps))
        self.assertIsNotNone(fast_path_policy_from_runtime("복잡한 질문인데 검색은 필요 없는 듯해", "text", deps=self.deps))

    def test_context_policy(self) -> None:
        policy = {"route": "search_executor", "action": "search_then_answer"}
        self.assertEqual(
            context_policy_for_fast_path_policy_from_runtime(policy, source="text", deps=self.deps)["needs_search"],
            True,
        )
        self.assertEqual(
            context_policy_for_fast_path_policy_from_runtime({"route": "main_direct", "action": "answer"}, source="voice", deps=self.deps)[
                "response_mode"
            ],
            "short",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
