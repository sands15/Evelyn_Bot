from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.memory as memory  # noqa: E402
from evelyn_core.discord_settings import (  # noqa: E402
    add_guild_channel_setting,
    get_guild_command_only_channel_ids,
    get_guild_command_prefix,
    get_guild_observe_channel_ids,
    normalize_channel_id_list,
    normalize_command_prefix,
    remove_guild_channel_setting,
    save_guild_channel_list,
    save_guild_command_prefix,
)


class TemporarySettingsRoot:
    def __init__(self) -> None:
        self.tmp = TemporaryDirectory()
        self.old_root = memory.GUILD_SETTINGS_ROOT

    def __enter__(self) -> Path:
        memory.GUILD_SETTINGS_ROOT = Path(self.tmp.name)
        return memory.GUILD_SETTINGS_ROOT

    def __exit__(self, exc_type, exc, tb) -> None:
        memory.GUILD_SETTINGS_ROOT = self.old_root
        self.tmp.cleanup()


class DiscordSettingsTests(unittest.TestCase):
    def test_normalize_command_prefix_rejects_spaces_and_long_values(self) -> None:
        self.assertEqual(normalize_command_prefix("", default_prefix="?"), "?")
        self.assertEqual(normalize_command_prefix(" ? "), "?")
        with self.assertRaises(ValueError):
            normalize_command_prefix("two words")
        with self.assertRaises(ValueError):
            normalize_command_prefix("abcdef")

    def test_command_prefix_is_saved_loaded_and_cached(self) -> None:
        cache: dict[int, str] = {}

        with TemporarySettingsRoot():
            self.assertEqual(get_guild_command_prefix(123, prefix_cache=cache, default_prefix="!"), "!")
            saved = save_guild_command_prefix(123, "?", prefix_cache=cache, now=lambda: 42.0)
            path = memory.guild_settings_path(123)
            stored = memory.read_json_file(path)

            self.assertEqual(saved, "?")
            self.assertEqual(cache[123], "?")
            self.assertEqual(stored["command_prefix"], "?")
            self.assertEqual(stored["updated_at"], 42)
            stored["command_prefix"] = "$"
            memory.write_json_file(path, stored)
            self.assertEqual(get_guild_command_prefix(123, prefix_cache=cache, default_prefix="!"), "?")

    def test_channel_id_settings_are_normalized_and_persisted(self) -> None:
        with TemporarySettingsRoot():
            self.assertEqual(normalize_channel_id_list([1, "2", "bad", 1, None]), [1, 2])
            self.assertEqual(save_guild_channel_list(123, "observe_channel_ids", [1, "2", 1], now=lambda: 99.0), [1, 2])
            self.assertEqual(get_guild_observe_channel_ids(123), [1, 2])
            self.assertEqual(add_guild_channel_setting(123, "observe_channel_ids", 3), [1, 2, 3])
            self.assertEqual(add_guild_channel_setting(123, "observe_channel_ids", 3), [1, 2, 3])
            self.assertEqual(remove_guild_channel_setting(123, "observe_channel_ids", 2), [1, 3])
            self.assertEqual(save_guild_channel_list(123, "command_only_channel_ids", [8, "9", 8]), [8, 9])
            self.assertEqual(get_guild_command_only_channel_ids(123), [8, 9])

    def test_none_guild_returns_defaults(self) -> None:
        self.assertEqual(get_guild_command_prefix(None, default_prefix="?"), "?")
        self.assertEqual(get_guild_observe_channel_ids(None), [])
        self.assertEqual(get_guild_command_only_channel_ids(None), [])


if __name__ == "__main__":
    unittest.main()
