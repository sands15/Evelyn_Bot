import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())


class FastCognitiveStateSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

    def test_fast_path_state_uses_current_user_text_for_summary(self) -> None:
        self.assertIn('"state_summary": cleaned,', self.main_py)
        self.assertNotIn('"state_summary": base.get("state_summary") or cleaned,', self.main_py)

    def test_fast_path_hint_does_not_reuse_stale_cognitive_hint(self) -> None:
        self.assertIn('"main_prompt_hint": hint,', self.main_py)
        self.assertNotIn('"main_prompt_hint": base.get("main_prompt_hint") or hint,', self.main_py)

    def test_conversation_state_does_not_surface_internal_response_hint(self) -> None:
        self.assertNotIn('state_lines.append(f"- 응답 힌트:', self.main_py)


if __name__ == "__main__":
    unittest.main()
