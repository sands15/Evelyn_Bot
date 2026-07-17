from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_settings_runtime import (  # noqa: E402
    DiscordSettingsRuntimeDeps,
    add_guild_channel_setting_from_runtime,
    get_guild_command_only_channel_ids_from_runtime,
    get_guild_command_prefix_from_runtime,
    get_guild_observe_channel_ids_from_runtime,
    resolve_command_prefix_from_runtime,
    normalize_command_prefix_from_runtime,
    remove_guild_channel_setting_from_runtime,
    save_guild_channel_list_from_runtime,
    save_guild_command_prefix_from_runtime,
)


class DiscordSettingsRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple] = []

    def test_main_binds_settings_builder_with_partial(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")

        self.assertNotIn("def build_discord_settings_runtime_deps(", source)
        self.assertIn("build_discord_settings_runtime_deps = partial(", source)
        self.assertIn("default_command_prefix=DEFAULT_COMMAND_PREFIX", source)

    def test_runtime_dispatches_to_payload_callables(self) -> None:
        deps = DiscordSettingsRuntimeDeps(
            default_command_prefix="?",
            prefix_cache={123: "?"},
            normalize_command_prefix_payload=lambda value, *, default_prefix: (
                self.calls.append(("normalize", value, default_prefix)),
                "NORM",
            )[1],
            get_guild_command_prefix_payload=lambda guild_id, *, prefix_cache, default_prefix: (
                self.calls.append(("get_prefix", guild_id, prefix_cache, default_prefix)),
                f"guild:{guild_id}",
            )[1],
            save_guild_command_prefix_payload=lambda guild_id, prefix, *, prefix_cache, default_prefix, now: (
                self.calls.append(("save_prefix", guild_id, prefix, now())),
                "SAVED",
            )[1],
            get_guild_observe_channel_ids_payload=lambda guild_id: (
                self.calls.append(("observe", guild_id)),
                [1, 2],
            )[1],
            get_guild_command_only_channel_ids_payload=lambda guild_id: (
                self.calls.append(("command_only", guild_id)),
                [3, 4],
            )[1],
            save_guild_channel_list_payload=lambda guild_id, key, channel_ids, *, now: (
                self.calls.append(("save_list", guild_id, key, list(channel_ids), now())),
                list(channel_ids),
            )[1],
            add_guild_channel_setting_payload=lambda guild_id, key, channel_id: (
                self.calls.append(("add", guild_id, key, channel_id)),
                [5, 6],
            )[1],
            remove_guild_channel_setting_payload=lambda guild_id, key, channel_id: (
                self.calls.append(("remove", guild_id, key, channel_id)),
                [6],
            )[1],
            now=lambda: 12.0,
        )

        self.assertEqual(normalize_command_prefix_from_runtime("!", deps=deps), "NORM")
        self.assertEqual(get_guild_command_prefix_from_runtime(11, deps=deps), "guild:11")
        self.assertEqual(save_guild_command_prefix_from_runtime(11, "!!", deps=deps), "SAVED")
        self.assertEqual(get_guild_observe_channel_ids_from_runtime(11, deps=deps), [1, 2])
        self.assertEqual(get_guild_command_only_channel_ids_from_runtime(None, deps=deps), [3, 4])
        self.assertEqual(save_guild_channel_list_from_runtime(11, "observe_channel_ids", [7, 8], deps=deps), [7, 8])
        self.assertEqual(add_guild_channel_setting_from_runtime(11, "observe_channel_ids", 9, deps=deps), [5, 6])
        self.assertEqual(remove_guild_channel_setting_from_runtime(11, "observe_channel_ids", 9, deps=deps), [6])

        self.assertIn(("normalize", "!", "?"), self.calls)
        self.assertIn(("get_prefix", 11, {123: "?"}, "?"), self.calls)
        self.assertIn(("save_prefix", 11, "!!", 12.0), self.calls)
        self.assertIn(("observe", 11), self.calls)
        self.assertIn(("command_only", None), self.calls)
        self.assertIn(("save_list", 11, "observe_channel_ids", [7, 8], 12.0), self.calls)
        self.assertIn(("add", 11, "observe_channel_ids", 9), self.calls)
        self.assertIn(("remove", 11, "observe_channel_ids", 9), self.calls)

    def test_resolve_command_prefix_from_runtime_uses_dependency(self) -> None:
        calls: list[int | None] = []

        def get_prefix(guild_id: int | None) -> str:
            calls.append(guild_id)
            return "!"

        self.assertEqual(resolve_command_prefix_from_runtime(123, get_guild_command_prefix=get_prefix), "!")
        self.assertEqual(calls, [123])


if __name__ == "__main__":
    unittest.main()
