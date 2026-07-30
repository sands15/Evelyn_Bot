from __future__ import annotations

import unittest

from evelyn_core.windows_foreground_context import (
    WINDOWS_FOREGROUND_SCHEMA,
    read_windows_foreground_window,
)


class _FakeUser32:
    def __init__(self, title: str, class_name: str) -> None:
        self.title = title
        self.class_name = class_name

    def GetForegroundWindow(self) -> int:
        return 123

    def GetWindowTextLengthW(self, _handle: int) -> int:
        return len(self.title)

    def GetWindowTextW(self, _handle, buffer, _length) -> int:
        buffer.value = self.title
        return len(self.title)

    def GetClassNameW(self, _handle, buffer, _length) -> int:
        buffer.value = self.class_name
        return len(self.class_name)


class WindowsForegroundContextTests(unittest.TestCase):
    def test_reads_only_bounded_title_and_class(self) -> None:
        result = read_windows_foreground_window(
            user32=_FakeUser32(
                "Minecraft 26.2 - 싱글플레이",
                "GLFW30",
            )
        )

        self.assertEqual(result["schema"], WINDOWS_FOREGROUND_SCHEMA)
        self.assertTrue(result["available"])
        self.assertEqual(result["title"], "Minecraft 26.2 - 싱글플레이")
        self.assertEqual(result["className"], "GLFW30")
        self.assertEqual(
            set(result),
            {"schema", "available", "title", "className"},
        )

    def test_control_characters_are_removed_and_lengths_are_bounded(self) -> None:
        result = read_windows_foreground_window(
            user32=_FakeUser32(
                "A" * 300 + "\nsecret",
                "B" * 120,
            )
        )

        self.assertEqual(len(result["title"]), 240)
        self.assertEqual(len(result["className"]), 80)
        self.assertNotIn("\n", result["title"])


if __name__ == "__main__":
    unittest.main()
