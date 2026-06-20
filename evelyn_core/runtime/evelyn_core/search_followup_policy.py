from __future__ import annotations

import re

from .response_output_policy import parse_response_action_tag
from .text import clean_text, strip_omnivoice_tags


def answer_promises_search(answer_text: str) -> bool:
    action, stripped = parse_response_action_tag(answer_text)
    if action == "search":
        return True
    text = clean_text(strip_omnivoice_tags(stripped)).lower()
    if not text:
        return False

    completed_markers = [
        "찾아봤",
        "검색해봤",
        "확인해봤",
        "알아봤",
        "찾아보니",
        "검색해보니",
        "결과는",
    ]
    if any(marker in text for marker in completed_markers):
        return False

    promise_markers = [
        "찾아볼게",
        "찾아볼께",
        "찾아보고",
        "찾아보고 올게",
        "찾아서 알려줄게",
        "찾아서 말해줄게",
        "검색해볼게",
        "검색해볼께",
        "검색해서 알려줄게",
        "검색해서 말해줄게",
        "확인해볼게",
        "확인해줄게",
        "확인해서 알려줄게",
        "확인해서 말해줄게",
        "알아볼게",
        "알아봐서 알려줄게",
        "알아봐서 말해줄게",
        "조사해볼게",
        "조사해서 알려줄게",
        "찾는 중",
        "찾아보고 있어",
        "자료 찾아볼게",
    ]
    promise_regexes = (
        r"(찾아|검색|확인|알아|조사).{0,8}(볼게|볼께|보고|해볼게|해볼께|해서)",
        r"(찾아서|검색해서|확인해서|알아봐서).{0,8}(알려줄게|말해줄게)",
        r"(찾는 중|찾아보고 있어|자료 찾아볼게)",
        r"(i'?ll|i will).{0,20}(search|look up|check|find)",
    )
    return any(marker in text for marker in promise_markers) or any(
        re.search(pattern, text, flags=re.I) for pattern in promise_regexes
    )


def strip_search_answer_sources(answer_text: str) -> str:
    text = str(answer_text or "")
    if not text:
        return ""

    kept_lines: list[str] = []
    source_prefixes = ("출처", "참고", "근거", "source", "sources", "reference", "references")
    for line in text.splitlines():
        cleaned_line = clean_text(line)
        if not cleaned_line:
            continue
        lowered = cleaned_line.lower()
        if any(lowered.startswith(prefix) for prefix in source_prefixes):
            continue
        kept_lines.append(cleaned_line)

    text = "\n".join(kept_lines)
    text = re.sub(r"\s*\([^)]*(?:https?://|www\.)[^)]*\)", "", text, flags=re.I)
    text = re.sub(r"\s*\[[^\]]*(?:https?://|www\.)[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r"https?://\S+|www\.\S+", "", text, flags=re.I)
    text = re.sub(r"\s*\((?:출처|참고|근거|source|sources|reference|references)\s*[:：]?[^\)]*\)", "", text, flags=re.I)
    text = re.sub(r"\s*\[(?:출처|참고|근거|source|sources|reference|references)\s*[:：]?[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r"(?:^|\s)(?:출처|참고|근거|source|sources|reference|references)\s*[:：]\s*[^.!?。！？\n]+", "", text, flags=re.I)
    return clean_text(text)


def is_generic_search_followup_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", clean_text(strip_omnivoice_tags(text)).lower())
    if not compact:
        return False
    return compact in {
        "검색",
        "검색해",
        "검색해봐",
        "찾아봐",
        "찾아줘",
        "찾아볼래",
        "확인해",
        "확인해봐",
        "알아봐",
        "알아봐줘",
        "찾아보고말해줘",
        "검색해서알려줘",
    }


def is_underspecified_weather_query(text: str) -> bool:
    compact = re.sub(r"\s+", "", clean_text(strip_omnivoice_tags(text)).lower())
    if not compact or "날씨" not in compact:
        return False
    return compact in {
        "날씨",
        "날씨알려줘",
        "날씨알려줘요",
        "날씨검색",
        "날씨검색해",
        "날씨검색해봐",
        "날씨찾아봐",
        "날씨찾아줘",
        "오늘날씨",
        "오늘날씨알려줘",
        "지금날씨",
        "지금날씨알려줘",
        "weather",
    }


__all__ = [
    "answer_promises_search",
    "is_generic_search_followup_text",
    "is_underspecified_weather_query",
    "strip_search_answer_sources",
]
