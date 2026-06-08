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
    should_force_search_query,
)


class QueryIntentTests(unittest.TestCase):
    def test_datetime_query_is_answered_without_search(self) -> None:
        now = datetime(2026, 5, 29, 1, 4, tzinfo=timezone.utc)

        self.assertEqual(classify_datetime_query("\uc624\ub298 \ub0a0\uc9dc\uac00 \ubb50\uc57c?"), "date")
        answer = answer_current_datetime_query("\uc624\ub298 \ub0a0\uc9dc\uac00 \ubb50\uc57c?", now=now)

        self.assertIn("2026\ub144 5\uc6d4 29\uc77c", answer or "")
        self.assertFalse(should_force_search_query("\uc624\ub298 \ub0a0\uc9dc\uac00 \ubb50\uc57c?"))

    def test_search_request_is_forced_for_explicit_web_lookup(self) -> None:
        self.assertTrue(should_force_search_query("\uc774\uac70 \uc778\ud130\ub137\uc5d0\uc11c \uac80\uc0c9\ud574\uc918"))
        self.assertTrue(should_force_search_query("look up the latest release notes"))

    def test_volatile_current_info_is_forced_to_search(self) -> None:
        self.assertTrue(should_force_search_query("\uc624\ub298 \ub274\uc2a4 \uc54c\ub824\uc918"))
        self.assertTrue(should_force_search_query("\ud658\uc728 \ucd5c\uc2e0 \uac12 \ucc3e\uc544\ubd10"))

    def test_negated_search_request_does_not_force_search(self) -> None:
        self.assertFalse(should_force_search_query("\uac80\uc0c9 \uc5c6\uc774 \uc9e7\uac8c \ub2f5\ud574\uc918"))
        self.assertFalse(should_force_search_query("\ucd5c\uc2e0 \uc815\ubcf4\ub97c \ucc3e\uc9c0 \ub9d0\uace0 \ub0b4 \ubb38\uc7a5\ub9cc \uc815\ub9ac\ud574\uc918"))
        self.assertFalse(should_force_search_query("without search, answer from the current context"))


if __name__ == "__main__":
    unittest.main()
