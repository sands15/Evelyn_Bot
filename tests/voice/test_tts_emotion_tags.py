import unittest
import sys
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.text import clean_tts_text, strip_omnivoice_tags


class TtsEmotionTagTests(unittest.TestCase):
    def test_clean_tts_text_preserves_allowed_mid_sentence_tags(self):
        self.assertEqual(
            clean_tts_text("응 [laughter] 그건 좀 웃기다"),
            "응 [laughter] 그건 좀 웃기다",
        )

    def test_clean_tts_text_drops_unknown_tags(self):
        self.assertEqual(clean_tts_text("[angry] 응"), "응")

    def test_clean_tts_text_removes_question_oh_tag_before_tts(self):
        self.assertEqual(clean_tts_text("왜 불렀어?"), "왜 불렀어?")

    def test_clean_tts_text_removes_leading_oh_interjection_before_tts(self):
        self.assertEqual(clean_tts_text("오! 지금 확인할게."), "지금 확인할게.")

    def test_clean_tts_text_does_not_double_tag(self):
        self.assertEqual(clean_tts_text("[sigh] 하아 알겠어"), "[sigh] 하아 알겠어")

    def test_strip_omnivoice_tags_keeps_visible_text_plain(self):
        self.assertEqual(strip_omnivoice_tags("[question-oh] 왜 불렀어?"), "왜 불렀어?")


if __name__ == "__main__":
    unittest.main()
