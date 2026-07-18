from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .fast_path_policy import FastPathPolicyRuntimeDeps


@dataclass(frozen=True)
class FastPathPolicyCompositionDeps:
    clean_text: Callable[[str], str]
    normalize_voice_text: Callable[[str], str]
    should_force_search_query: Callable[[str], bool]


class FastPathPolicyComposition:
    """Owns Evelyn's fixed fast-path routing vocabulary and runtime wiring."""

    def __init__(self, deps: FastPathPolicyCompositionDeps) -> None:
        self.deps = deps

    def build_runtime_deps(self) -> FastPathPolicyRuntimeDeps:
        deps = self.deps
        return FastPathPolicyRuntimeDeps(
            clean_text=deps.clean_text,
            normalize_voice_text=deps.normalize_voice_text,
            should_force_search_query=deps.should_force_search_query,
            control_page_source_aliases=(
                "control_page",
                "control-page",
                "local_control_page",
            ),
            control_page_light_request_max_chars=180,
            fast_path_continue_markers=(
                "그리고",
                "근데",
                "아니",
                "아니야",
                "잠깐",
                "그거",
                "그건",
                "그 다음",
                "이어",
                "계속",
            ),
            fast_path_directive_markers=(
                "해줘",
                "말해줘",
                "알려줘",
                "정리해줘",
                "요약해줘",
                "설명해줘",
                "번역해줘",
                "고쳐줘",
                "수정해줘",
            ),
            fast_path_deep_route_markers=(
                "검색",
                "찾아봐",
                "찾아 봐",
                "최신",
                "뉴스",
                "시세",
                "가격",
                "환율",
                "주가",
                "비교",
                "분석",
                "판단",
                "기억",
                "아까",
                "방금",
                "전에",
                "이전",
                "이어",
                "계속",
                "요약",
                "정리",
            ),
            fast_path_negated_search_markers=(
                "검색 없이",
                "검색은 하지 말고",
                "검색하지 말고",
                "검색하지마",
                "인터넷 없이",
                "웹 없이",
                "찾지 말고",
                "찾아보지 말고",
                "without search",
                "without searching",
                "no search",
                "don't search",
                "do not search",
                "without looking up",
            ),
            fast_path_search_markers=(
                "검색",
                "찾아",
                "최신",
                "뉴스",
                "시세",
                "가격",
                "환율",
            ),
            fast_path_search_route_markers=("검색", "찾아봐", "찾아 봐", "찾아"),
        )


__all__ = ["FastPathPolicyComposition", "FastPathPolicyCompositionDeps"]
