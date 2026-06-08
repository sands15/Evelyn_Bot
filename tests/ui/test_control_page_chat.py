from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
CONTROL_PAGE = REPO_ROOT / "docs" / "index.html"


class ControlPageChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = CONTROL_PAGE.read_text(encoding="utf-8")

    def test_chat_log_renders_state_messages(self) -> None:
        self.assertIn('id="chatLog"', self.html)
        self.assertIn("function renderChatMessages(messages)", self.html)
        self.assertIn("renderChatMessages(state.chat.messages)", self.html)

    def test_send_uses_conversation_log_instead_of_single_bubble_only(self) -> None:
        self.assertIn("appendChatMessage({", self.html)
        self.assertNotIn('lastBubble.querySelector(".caption").textContent = text;', self.html)

    def test_chat_surface_uses_transparent_bubbles_and_themed_scrollbars(self) -> None:
        self.assertIn("--chat-panel: transparent;", self.html)
        self.assertIn("--chat-input:", self.html)
        self.assertIn("--scrollbar-track: transparent;", self.html)
        self.assertIn("--scrollbar-thumb:", self.html)
        self.assertIn("--stage-bg: var(--bg);", self.html)
        self.assertIn("background: var(--chat-panel);", self.html)
        self.assertIn("box-shadow: none;", self.html)
        self.assertIn("backdrop-filter: none;", self.html)
        self.assertIn("background: var(--chat-input);", self.html)
        self.assertIn("scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);", self.html)
        self.assertIn(".chat-log::-webkit-scrollbar-thumb", self.html)

    def test_chat_log_can_grow_toward_page_top(self) -> None:
        self.assertIn("max-height: min(620px, calc(100vh - 214px));", self.html)
        self.assertIn("max-height: calc(100svh - 274px);", self.html)
        self.assertNotIn("max-height: min(300px, 34vh);", self.html)


if __name__ == "__main__":
    unittest.main()
