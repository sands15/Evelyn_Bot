import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_pipeline import classify_dialogue_turn, selected_path_for_turn  # noqa: E402


class DialogueTurnClassifierTests(unittest.TestCase):
    def test_wake_only_turn_uses_cached_audio_path(self) -> None:
        turn_type = classify_dialogue_turn("이블린", wake_only_turn=True)

        self.assertEqual(turn_type, "wake_call")
        self.assertEqual(selected_path_for_turn(turn_type, wake_only_turn=True), "cached_audio_fast_path")

    def test_dialogue_ux_regression_sample_turn_types(self) -> None:
        samples = {
            "듣고 있어?": "casual_check",
            "괜찮아?": "casual_check",
            "응": "short_confirm",
            "지금 마크에서 뭐 하고 있어?": "runtime_status",
            "나무 캐자": "minecraft_command",
            "그거 말고 철 찾자": "minecraft_command",
            "이거 인터넷에서 검색해줘": "knowledge_or_search",
            "좀 자연스럽게 말해봐": "conversation",
        }

        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(classify_dialogue_turn(text), expected)

    def test_selected_path_matches_turn_type(self) -> None:
        self.assertEqual(selected_path_for_turn("casual_check"), "light_dialogue_path")
        self.assertEqual(selected_path_for_turn("runtime_status"), "runtime_status_path")
        self.assertEqual(selected_path_for_turn("minecraft_command"), "minecraft_action_path")
        self.assertEqual(selected_path_for_turn("knowledge_or_search"), "search_or_long_answer_path")
        self.assertEqual(selected_path_for_turn("conversation"), "main_conversation_path")


if __name__ == "__main__":
    unittest.main()
