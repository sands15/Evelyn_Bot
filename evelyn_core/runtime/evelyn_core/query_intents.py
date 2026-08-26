from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import re

from .text import clean_text


WEEKDAYS_KO = (
    "\uc6d4\uc694\uc77c",
    "\ud654\uc694\uc77c",
    "\uc218\uc694\uc77c",
    "\ubaa9\uc694\uc77c",
    "\uae08\uc694\uc77c",
    "\ud1a0\uc694\uc77c",
    "\uc77c\uc694\uc77c",
)

DATE_QUERY_MARKERS = (
    "\uc624\ub298 \ub0a0\uc9dc",
    "\ub0a0\uc9dc",
    "\uba70\uce60",
    "\uba87 \uc6d4 \uba87 \uc77c",
    "\uba87\uc6d4\uba87\uc77c",
    "\uc694\uc77c",
    "what date",
    "today's date",
    "what day",
)

TIME_QUERY_MARKERS = (
    "\uc9c0\uae08 \uba87 \uc2dc",
    "\uc9c0\uae08\uba87\uc2dc",
    "\ud604\uc7ac \uc2dc\uac04",
    "\ud604\uc7ac\uc2dc\uac04",
    "\uc9c0\uae08 \uc2dc\uac04",
    "\uc9c0\uae08\uc2dc\uac04",
    "\uba87 \uc2dc",
    "\uba87\uc2dc",
    "\uc2dc\uac04 \uc54c\ub824",
    "what time",
    "current time",
)

EXPLICIT_SEARCH_MARKERS = (
    "\uac80\uc0c9",
    "\uc678\ubd80 \uac80\uc0c9",
    "\uc778\ud130\ub137",
    "\uc6f9",
    "\uc0ac\uc774\ud2b8",
    "\ub9c1\ud06c",
    "\ucd9c\ucc98",
    "\uadfc\uac70",
    "\uacf5\uc2dd \ubb38\uc11c",
    "\uacf5\uc2dd\ubb38\uc11c",
    "search",
    "web",
    "internet",
    "source",
    "sources",
    "official docs",
)

VOLATILE_INFO_MARKERS = (
    "\ucd5c\uc2e0",
    "\ub274\uc2a4",
    "\ub0a0\uc528",
    "\uc608\ubcf4",
    "\uac15\uc218",
    "\uc6b0\uc0b0",
    "\ube44 \uc624",
    "\ube44 \uc640",
    "\ube44\uac00 \uc624",
    "\ube44\uac00 \uc62c",
    "\uac00\uaca9",
    "\uc8fc\uac00",
    "\ud658\uc728",
    "\uc694\uc998",
    "\ucd5c\uadfc",
    "\uc2e4\uc2dc\uac04",
    "\uc5c5\ub370\uc774\ud2b8",
    "\ubc84\uc804",
    "\ub9b4\ub9ac\uc988",
    "\ub2e4\uc6b4\ub85c\ub4dc",
    "\uc624\ub298 \ub274\uc2a4",
    "\uc624\ub298 \ub0a0\uc528",
    "\ub0b4\uc77c \ub0a0\uc528",
    "\uc774\ubc88 \uc8fc",
    "\uc774\ubc88\ub2ec",
    "latest",
    "news",
    "weather",
    "rain",
    "raining",
    "price",
    "stock",
    "exchange rate",
    "release",
    "version",
    "update",
    "download",
    "today's news",
    "forecast",
)

NEGATED_SEARCH_MARKERS = (
    "\uac80\uc0c9 \uc5c6\uc774",
    "\uac80\uc0c9\uc740 \ud558\uc9c0 \ub9d0\uace0",
    "\uac80\uc0c9\ud558\uc9c0 \ub9d0\uace0",
    "\uac80\uc0c9\ud558\uc9c0\ub9c8",
    "\uc778\ud130\ub137 \uc5c6\uc774",
    "\uc6f9 \uc5c6\uc774",
    "\ucc3e\uc9c0 \ub9d0\uace0",
    "\ucc3e\uc544\ubcf4\uc9c0 \ub9d0\uace0",
    "without search",
    "without searching",
    "no search",
    "don't search",
    "do not search",
    "without looking up",
)

WEATHER_MARKERS = (
    "\ub0a0\uc528",
    "\uc608\ubcf4",
    "\uac15\uc218",
    "\uc6b0\uc0b0",
    "\ube44 \uc624",
    "\ube44 \uc640",
    "\ube44\uac00 \uc624",
    "\ube44\uac00 \uc62c",
    "weather",
    "forecast",
    "rain",
    "raining",
)

KOREAN_LOCATION_RE = re.compile(
    r"([가-힣]{2,}(?:특별자치시|특별자치도|광역시|특별시|자치도|시|군|구|도))"
)
KOREAN_BARE_LOCATION_HINTS = (
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "세종",
    "제주",
    "수원",
    "성남",
    "고양",
    "용인",
    "청주",
    "전주",
    "천안",
)


def _normalized_pair(text: str) -> tuple[str, str]:
    normalized = clean_text(text).lower()
    compact = re.sub(r"\s+", "", normalized)
    return normalized, compact


def _has_negated_search_request(normalized: str, compact: str) -> bool:
    return any(marker in normalized for marker in NEGATED_SEARCH_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in NEGATED_SEARCH_MARKERS
    )


def is_weather_query(text: str) -> bool:
    normalized, compact = _normalized_pair(text)
    if not normalized:
        return False
    return any(marker in normalized for marker in WEATHER_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in WEATHER_MARKERS
    )


def extract_korean_location_hint(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    matches = [match.group(1) for match in KOREAN_LOCATION_RE.finditer(cleaned)]
    if not matches:
        bare_matches = [(cleaned.rfind(location), location) for location in KOREAN_BARE_LOCATION_HINTS if location in cleaned]
        if not bare_matches:
            return ""
        return max(bare_matches, key=lambda item: item[0])[1]
    return clean_text(matches[-1])


def resolve_recent_weather_location(recent_texts: list[str] | tuple[str, ...]) -> str:
    for text in reversed(list(recent_texts or [])):
        location = extract_korean_location_hint(text)
        if location:
            return location
    return ""


def classify_datetime_query(text: str) -> str | None:
    normalized, compact = _normalized_pair(text)
    if not normalized:
        return None

    asks_date = any(marker in normalized for marker in DATE_QUERY_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in DATE_QUERY_MARKERS
    )
    asks_time = any(marker in normalized for marker in TIME_QUERY_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in TIME_QUERY_MARKERS
    )

    if asks_date and asks_time:
        return "datetime"
    if asks_time:
        return "time"
    if asks_date:
        return "date"
    return None


EVELYN_LOCAL_TIMEZONE = ZoneInfo("Asia/Seoul")


def format_current_datetime_answer(kind: str | None, *, now: datetime | None = None) -> str:
    current = (
        now.astimezone(EVELYN_LOCAL_TIMEZONE)
        if now is not None
        else datetime.now(EVELYN_LOCAL_TIMEZONE)
    )
    weekday = WEEKDAYS_KO[current.weekday()]
    date_part = f"{current.year}\ub144 {current.month}\uc6d4 {current.day}\uc77c {weekday}"
    period = "\uc624\uc804" if current.hour < 12 else "\uc624\ud6c4"
    spoken_hour = current.hour % 12 or 12
    time_part = f"{period} {spoken_hour}\uc2dc {current.minute:02d}\ubd84"
    if kind == "date":
        return f"\uc624\ub298\uc740 {date_part}\uc774\uc57c."
    if kind == "time":
        return f"\uc9c0\uae08\uc740 {time_part}\uc774\uc57c."
    return f"\uc624\ub298\uc740 {date_part}\uc774\uace0, \uc9c0\uae08\uc740 {time_part}\uc774\uc57c."


def answer_current_datetime_query(text: str, *, now: datetime | None = None) -> str | None:
    kind = classify_datetime_query(text)
    if kind is None:
        return None
    return format_current_datetime_answer(kind, now=now)


def should_force_search_query(text: str) -> bool:
    normalized, compact = _normalized_pair(text)
    if not normalized:
        return False
    if _has_negated_search_request(normalized, compact):
        return False

    explicit_hit = any(marker in normalized for marker in EXPLICIT_SEARCH_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in EXPLICIT_SEARCH_MARKERS
    )
    if explicit_hit:
        return True

    volatile_hit = any(marker in normalized for marker in VOLATILE_INFO_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in VOLATILE_INFO_MARKERS
    )
    if not volatile_hit:
        return False

    return classify_datetime_query(normalized) is None
