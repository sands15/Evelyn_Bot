import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
import sys

if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.cognitive_followup_policy import (
    ShouldForceSearchFollowupRuntimeDeps,
    should_force_search_followup_from_runtime,
)  # noqa: E402


class CognitiveFollowupRuntimeTests(unittest.TestCase):
    def test_forced_followup_when_state_gates_to_search(self) -> None:
        deps = ShouldForceSearchFollowupRuntimeDeps(
            read_cached_cognitive_state_fn=lambda *_, **__: {"action": "ask"},
            apply_ask_gating_fn=lambda state, **__: {"action": "search_then_answer"},
            clean_text_fn=str.strip,
        )
        self.assertTrue(
            should_force_search_followup_from_runtime(
                11,
                room_key="room-1",
                person_key="person-1",
                session_memory_key="session-1",
                source="text",
                deps=deps,
            )
        )

    def test_not_forced_when_no_state(self) -> None:
        deps = ShouldForceSearchFollowupRuntimeDeps(
            read_cached_cognitive_state_fn=lambda *_, **__: None,
            apply_ask_gating_fn=lambda state, **__: {"action": "answer"},
            clean_text_fn=str.strip,
        )
        self.assertFalse(
            should_force_search_followup_from_runtime(
                11,
                source="text",
                deps=deps,
            )
        )

    def test_not_forced_when_action_not_search_then_answer(self) -> None:
        deps = ShouldForceSearchFollowupRuntimeDeps(
            read_cached_cognitive_state_fn=lambda *_, **__: {"action": "ask"},
            apply_ask_gating_fn=lambda state, **__: {"action": "answer"},
            clean_text_fn=str.strip,
        )
        self.assertFalse(
            should_force_search_followup_from_runtime(
                11,
                source="voice",
                deps=deps,
            )
        )


if __name__ == "__main__":
    unittest.main()
