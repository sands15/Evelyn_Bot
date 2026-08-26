import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.query_intents import (  # noqa: E402
    answer_current_datetime_query,
    classify_datetime_query,
    extract_korean_location_hint,
    resolve_recent_weather_location,
    should_force_search_query,
)


class QueryIntentTests(unittest.TestCase):
    def test_datetime_query_is_answered_without_search(self) -> None:
        now = datetime(2026, 5, 29, 1, 4, tzinfo=timezone.utc)

        self.assertEqual(classify_datetime_query("\uc624\ub298 \ub0a0\uc9dc\uac00 \ubb50\uc57c?"), "date")
        answer = answer_current_datetime_query("\uc624\ub298 \ub0a0\uc9dc\uac00 \ubb50\uc57c?", now=now)

        self.assertIn("2026\ub144 5\uc6d4 29\uc77c", answer or "")
        self.assertFalse(should_force_search_query("\uc624\ub298 \ub0a0\uc9dc\uac00 \ubb50\uc57c?"))

    def test_datetime_query_uses_seoul_time_independent_of_host_timezone(self) -> None:
        now = datetime(2026, 7, 28, 7, 29, tzinfo=timezone.utc)

        answer = answer_current_datetime_query("\uc9c0\uae08 \uba87 \uc2dc\uc57c?", now=now)

        self.assertEqual(answer, "\uc9c0\uae08\uc740 \uc624\ud6c4 4\uc2dc 29\ubd84\uc774\uc57c.")

    def test_search_request_is_forced_for_explicit_web_lookup(self) -> None:
        self.assertTrue(should_force_search_query("\uc774\uac70 \uc778\ud130\ub137\uc5d0\uc11c \uac80\uc0c9\ud574\uc918"))
        self.assertTrue(should_force_search_query("look up the latest release notes"))

    def test_ambiguous_find_language_is_left_to_the_router(self) -> None:
        self.assertFalse(should_force_search_query("\uae30\uc5b5\uc5d0\uc11c \uadf8 \uc598\uae30 \ucc3e\uc544\uc918"))
        self.assertFalse(should_force_search_query("\uc804\uc5d0 \ub9d0\ud55c \ud30c\uc77c \ucc3e\uc544\ubd10"))
        self.assertFalse(should_force_search_query("find the note I mentioned"))

    def test_volatile_current_info_is_forced_to_search(self) -> None:
        self.assertTrue(should_force_search_query("\uc624\ub298 \ub274\uc2a4 \uc54c\ub824\uc918"))
        self.assertTrue(should_force_search_query("\ud658\uc728 \ucd5c\uc2e0 \uac12 \ucc3e\uc544\ubd10"))

    def test_weather_rain_question_is_forced_to_search(self) -> None:
        self.assertTrue(should_force_search_query("\uc624\ub298 \uad11\uc8fc\uad11\uc5ed\uc2dc\uc5d0 \ube44\uac00 \uc62c\uae4c?"))
        self.assertTrue(should_force_search_query("\ub0b4\uc77c \uc11c\uc6b8 \ube44 \uc640?"))
        self.assertTrue(should_force_search_query("\uc624\ub298 \ubd80\uc0b0 \uac15\uc218 \uc608\ubcf4 \uc54c\ub824\uc918"))
        self.assertTrue(should_force_search_query("\uadf8\ub7fc \uc624\ub298 \ub0a0\uc528 \uc54c\ub824\uc918"))
        self.assertTrue(should_force_search_query("\ub0a0\uc528 \uc54c\ub824\uc918"))

    def test_recent_korean_location_hint_can_ground_weather_followup(self) -> None:
        self.assertEqual(extract_korean_location_hint("\ud55c\uad6d, \uad11\uc8fc\uad11\uc5ed\uc2dc\uc57c \uae30\uc5b5\ud574"), "\uad11\uc8fc\uad11\uc5ed\uc2dc")
        self.assertEqual(extract_korean_location_hint("\uc11c\uc6b8 \ub0b4\uc77c \ub0a0\uc528 \uc54c\ub824\uc918"), "\uc11c\uc6b8")
        self.assertEqual(
            resolve_recent_weather_location([
                "\uc54c\uc558\uc5b4 \uad11\uc8fc\uad11\uc5ed\uc2dc\ub77c\uace0 \uae30\uc5b5\ud574\ub458\uac8c.",
            ]),
            "\uad11\uc8fc\uad11\uc5ed\uc2dc",
        )

    def test_negated_search_request_does_not_force_search(self) -> None:
        self.assertFalse(should_force_search_query("\uac80\uc0c9 \uc5c6\uc774 \uc9e7\uac8c \ub2f5\ud574\uc918"))
        self.assertFalse(should_force_search_query("\ucd5c\uc2e0 \uc815\ubcf4\ub97c \ucc3e\uc9c0 \ub9d0\uace0 \ub0b4 \ubb38\uc7a5\ub9cc \uc815\ub9ac\ud574\uc918"))
        self.assertFalse(should_force_search_query("without search, answer from the current context"))


if __name__ == "__main__":
    unittest.main()
