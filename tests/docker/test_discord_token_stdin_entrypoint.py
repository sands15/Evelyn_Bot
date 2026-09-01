from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
ENTRYPOINT = REPO_ROOT / "docker" / "discord_token_stdin_entrypoint.py"
COMMAND_GUARD = REPO_ROOT / "docker" / "discord_command_registry_guard.py"


def load_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "evelyn_discord_token_stdin_entrypoint",
        ENTRYPOINT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_command_guard():
    name = "evelyn_discord_command_registry_guard"
    spec = importlib.util.spec_from_file_location(name, COMMAND_GUARD)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class DiscordTokenStdinEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_entrypoint()
        self.previous = os.environ.pop(self.module.TOKEN_ENV, None)

    def tearDown(self) -> None:
        os.environ.pop(self.module.TOKEN_ENV, None)
        if self.previous is not None:
            os.environ[self.module.TOKEN_ENV] = self.previous
        sys.modules.pop("evelyn_core.config", None)
        sys.modules.pop("evelyn_core.main_runtime_config", None)

    def test_strict_single_line_ascii_token(self) -> None:
        token = "A.b_C-9"
        self.assertEqual(
            self.module.read_token(io.BytesIO(token.encode("ascii") + b"\n")),
            token,
        )
        invalid = (
            b"",
            b"missing-newline",
            b"two\nlines\n",
            b"space token\n",
            b"carriage\r\n",
            b"\xff\n",
            b"x" * 513 + b"\n",
        )
        for raw in invalid:
            with self.subTest(raw=raw[:20]):
                with self.assertRaisesRegex(
                    self.module.TokenBootstrapError,
                    "^discord_token_stdin_invalid$",
                ):
                    self.module.read_token(io.BytesIO(raw))

    def test_token_is_stdin_only_and_scrubbed_before_bot_run(self) -> None:
        token = "live.secret_value-9"
        observed: dict[str, object] = {}
        config = types.ModuleType("evelyn_core.config")
        main_runtime_config = types.ModuleType("evelyn_core.main_runtime_config")
        sys.modules["evelyn_core.config"] = config
        sys.modules["evelyn_core.main_runtime_config"] = main_runtime_config

        class FakeBot:
            def run(self, supplied_token: str) -> None:
                observed["token"] = supplied_token
                observed["env"] = os.environ.get(self.module.TOKEN_ENV)
                observed["getenv"] = os.getenv(self.module.TOKEN_ENV)
                observed["config"] = getattr(config, self.module.TOKEN_ENV)
                observed["main_runtime_config"] = getattr(
                    main_runtime_config,
                    self.module.TOKEN_ENV,
                )

        fake_bot = FakeBot()
        fake_bot.module = self.module

        def fake_run_path(
            _path: str,
            *,
            run_name: str,
            globals_dict: dict[str, object],
        ) -> dict[str, object]:
            namespace = globals_dict
            namespace.update({"__name__": run_name, "os": os, "bot": fake_bot})
            exec(
                "DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')\n"
                "import sys\n"
                "sys.modules['evelyn_core.config'].DISCORD_BOT_TOKEN = "
                "DISCORD_BOT_TOKEN\n"
                "sys.modules['evelyn_core.main_runtime_config'].DISCORD_BOT_TOKEN = "
                "DISCORD_BOT_TOKEN\n"
                "bot.run(DISCORD_BOT_TOKEN)\n",
                namespace,
            )
            observed["main_global"] = namespace["DISCORD_BOT_TOKEN"]
            return namespace

        self.module.run_main(
            token,
            bot_class=FakeBot,
            path_runner=fake_run_path,
            main_path=Path("/not-executed/main.py"),
        )

        self.assertEqual(observed["token"], token)
        self.assertIsNone(observed["env"])
        self.assertIsNone(observed["getenv"])
        self.assertIsNone(observed["config"])
        self.assertIsNone(observed["main_runtime_config"])
        self.assertIsNone(observed["main_global"])
        self.assertNotIn(self.module.TOKEN_ENV, os.environ)

    def test_pre_bot_failure_scrubs_all_imported_and_main_copies(self) -> None:
        token = "pre.run_secret-9"
        config = types.ModuleType("evelyn_core.config")
        main_runtime_config = types.ModuleType("evelyn_core.main_runtime_config")
        sys.modules["evelyn_core.config"] = config
        sys.modules["evelyn_core.main_runtime_config"] = main_runtime_config
        observed: dict[str, object] = {}

        class FakeBot:
            def run(self, _token: str) -> None:
                raise AssertionError("Bot.run must not be reached")

        def fail_before_run(
            _path: str,
            *,
            run_name: str,
            globals_dict: dict[str, object],
        ) -> dict[str, object]:
            copied = os.getenv(self.module.TOKEN_ENV)
            globals_dict.update({"__name__": run_name, self.module.TOKEN_ENV: copied})
            config.DISCORD_BOT_TOKEN = copied
            main_runtime_config.DISCORD_BOT_TOKEN = copied
            observed["main"] = globals_dict
            raise RuntimeError("startup failed before Bot.run")

        fail_before_run.module = self.module
        with self.assertRaisesRegex(RuntimeError, "startup failed before Bot.run"):
            self.module.run_main(
                token,
                bot_class=FakeBot,
                path_runner=fail_before_run,
                main_path=Path("/not-executed/main.py"),
            )

        self.assertIsNone(config.DISCORD_BOT_TOKEN)
        self.assertIsNone(main_runtime_config.DISCORD_BOT_TOKEN)
        self.assertIsNone(observed["main"][self.module.TOKEN_ENV])
        self.assertIsNone(os.getenv(self.module.TOKEN_ENV))

    def test_refuses_environment_transport_and_never_embeds_secret(self) -> None:
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertNotIn('os.environ[TOKEN_ENV] =', source)
        self.assertNotIn('os.putenv(', source)
        self.assertIn('sys.stdin.buffer', source)
        self.assertIn('os.getenv = stdin_getenv', source)
        self.assertIn('_clear_imported_token(caller_globals)', source)

        os.environ[self.module.TOKEN_ENV] = "must-not-be-used"
        with self.assertRaisesRegex(
            self.module.TokenBootstrapError,
            "^discord_token_transport_invalid$",
        ):
            self.module.run_main("stdin-wins", bot_class=object)


class DiscordCommandRegistryGuardTests(unittest.TestCase):
    APP_ID = "98765432109876543"
    GUILD_ID = "12345678901234567"
    RUN_ID = "a" * 32

    class FakeApi:
        def __init__(self, guild=(), global_commands=()):
            self.guild = copy.deepcopy(list(guild))
            self.global_value = copy.deepcopy(list(global_commands))
            self.deleted: list[str] = []
            self.delete_hook = None
            self.baseline_once = None
            self.guild_calls = 0

        def application_id(self):
            return DiscordCommandRegistryGuardTests.APP_ID

        def guild_commands(self, _application_id, _guild_id):
            self.guild_calls += 1
            if self.guild_calls == 1 and self.baseline_once is not None:
                return copy.deepcopy(self.baseline_once)
            return copy.deepcopy(self.guild)

        def global_commands(self, _application_id):
            return copy.deepcopy(self.global_value)

        def delete_guild_command(self, application_id, guild_id, command_id):
            self.deleted.append(command_id)
            if self.delete_hook is not None:
                return self.delete_hook(application_id, guild_id, command_id)
            self.guild = [item for item in self.guild if item["id"] != command_id]

    def setUp(self) -> None:
        self.module = load_command_guard()

    def tearDown(self) -> None:
        sys.modules.pop("evelyn_discord_command_registry_guard", None)

    def _managed(self, count=5, *, start=10001):
        return [
            {
                "id": str(start + index),
                "type": 1,
                "name": name,
                "description": f"managed-{index}",
            }
            for index, name in enumerate(sorted(self.module.MANAGED_NAMES)[:count])
        ]

    def _write_ownership(self, path: Path, commands, *, recovery=False) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema": self.module.OWNERSHIP_SCHEMA,
                    "runId": self.RUN_ID,
                    "applicationId": self.APP_ID,
                    "guildId": self.GUILD_ID,
                    "recoveryRequired": recovery,
                    "commands": [
                        {
                            "id": command["id"],
                            "shapes": [self.module.command_shape(command)],
                        }
                        for command in commands
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _guard(self, api, ownership_path: Path):
        return self.module.RegistryGuard(
            api,
            application_id=self.APP_ID,
            guild_id=self.GUILD_ID,
            ownership_path=ownership_path,
            run_id=self.RUN_ID,
        )

    def _config(self, root: Path):
        return self.module.GuardConfig(
            guild_id=self.GUILD_ID,
            status_path=root / "status.json",
            cleanup_path=root / "cleanup.request",
            ownership_path=root / "ownership.json",
            run_id=self.RUN_ID,
            publish_timeout_sec=120,
            lifetime_sec=480,
        )

    def test_stdin_contract_includes_exact_ownership_file_and_run(self) -> None:
        payload = {
            "guildId": self.GUILD_ID,
            "statusPath": "/run/evelyn-command-guard/status.json",
            "cleanupPath": "/run/evelyn-command-guard/cleanup.request",
            "ownershipPath": "/run/evelyn-command-guard/ownership.json",
            "runId": self.RUN_ID,
            "publishTimeoutSec": 120,
            "lifetimeSec": 480,
        }
        token, config = self.module.read_startup(
            io.BytesIO(b"secret.token-1\n" + json.dumps(payload).encode() + b"\n")
        )
        self.assertEqual(token, "secret.token-1")
        self.assertEqual(config.ownership_path, self.module.OWNERSHIP_PATH)
        self.assertEqual(config.run_id, self.RUN_ID)

    def test_partial_one_through_four_and_exact_five_restore_ledger_owned_only(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            api = self.FakeApi(baseline)
            self._write_ownership(path, [])
            guard = self._guard(api, path)
            guard.capture_baseline()
            for count in range(1, 5):
                api.guild = copy.deepcopy(baseline + managed[:count])
                self._write_ownership(path, managed[:count])
                self.assertFalse(guard.capture_published())
            api.guild = copy.deepcopy(baseline + managed)
            self._write_ownership(path, managed)
            self.assertTrue(guard.capture_published())
            first = True

            def ambiguous_applied(_application_id, _guild_id, command_id):
                nonlocal first
                api.guild = [item for item in api.guild if item["id"] != command_id]
                if first:
                    first = False
                    raise self.module.GuardFailure("guard_discord_request_failed")

            api.delete_hook = ambiguous_applied
            guard.restore()

        self.assertEqual(set(api.deleted), {item["id"] for item in managed})
        self.assertEqual(self.module.canonical(api.guild), self.module.canonical(baseline))

    def test_baseline_managed_is_never_adopted_or_deleted(self) -> None:
        managed = self._managed(1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, [])
            api = self.FakeApi(managed)
            guard = self._guard(api, path)
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_baseline_managed_commands_present$",
            ):
                guard.capture_baseline()
        self.assertFalse(guard.baseline_captured)
        self.assertEqual(api.deleted, [])

    def test_guard_restart_adopts_same_run_v2_ids_before_baseline(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, managed)
            api = self.FakeApi(baseline + managed)
            guard = self._guard(api, path)

            guard.capture_baseline()
            self.assertTrue(guard.capture_published())
            guard.restore()

        self.assertEqual(set(api.deleted), {item["id"] for item in managed})
        self.assertEqual(self.module.canonical(api.guild), self.module.canonical(baseline))

    def test_invalid_late_ledger_entry_never_partially_claims_or_deletes(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed(2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, [])
            api = self.FakeApi(baseline)
            guard = self._guard(api, path)
            guard.capture_baseline()
            path.write_text(
                json.dumps(
                    {
                        "schema": self.module.OWNERSHIP_SCHEMA,
                        "runId": self.RUN_ID,
                        "applicationId": self.APP_ID,
                        "guildId": self.GUILD_ID,
                        "recoveryRequired": False,
                        "commands": [
                            {
                                "id": managed[0]["id"],
                                "shapes": [
                                    self.module.command_shape(managed[0])
                                ],
                            },
                            {
                                "id": "invalid",
                                "shapes": [
                                    self.module.command_shape(managed[1])
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            api.guild = copy.deepcopy(baseline + managed[:1])

            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_cleanup_verification_failed$",
            ):
                guard.restore()

        self.assertEqual(api.deleted, [])
        self.assertFalse(guard.tracked_managed)

    def test_guard_restart_cleans_exact_owned_transitional_temp(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        final = self._managed(1)[0]
        temp = {
            **final,
            "name": self.module._temporary_command_name(
                self.RUN_ID,
                final["name"],
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": self.module.OWNERSHIP_SCHEMA,
                        "runId": self.RUN_ID,
                        "applicationId": self.APP_ID,
                        "guildId": self.GUILD_ID,
                        "recoveryRequired": False,
                        "commands": [
                            {
                                "id": temp["id"],
                                "shapes": [
                                    self.module.command_shape(temp),
                                    self.module.command_shape(final),
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            api = self.FakeApi(baseline + [temp])
            guard = self._guard(api, path)

            guard.capture_baseline()
            self.assertFalse(guard.capture_published())
            guard.restore()

        self.assertEqual(api.deleted, [temp["id"]])
        self.assertEqual(self.module.canonical(api.guild), self.module.canonical(baseline))

    def test_ownership_shapes_may_only_narrow_from_temp_to_final(self) -> None:
        final = self._managed(1)[0]
        temp = {
            **final,
            "name": self.module._temporary_command_name(
                self.RUN_ID,
                final["name"],
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"

            def write(shapes) -> None:
                path.write_text(
                    json.dumps(
                        {
                            "schema": self.module.OWNERSHIP_SCHEMA,
                            "runId": self.RUN_ID,
                            "applicationId": self.APP_ID,
                            "guildId": self.GUILD_ID,
                            "recoveryRequired": False,
                            "commands": [
                                {"id": final["id"], "shapes": shapes}
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            write(
                [
                    self.module.command_shape(temp),
                    self.module.command_shape(final),
                ]
            )
            guard = self._guard(self.FakeApi([]), path)
            guard._read_ownership()
            write([self.module.command_shape(final)])
            guard._read_ownership()
            write(
                [
                    self.module.command_shape(temp),
                    self.module.command_shape(final),
                ]
            )
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_owned_shape_drift$",
            ):
                guard._read_ownership()

    def test_concurrent_same_name_without_returned_id_is_left_and_fails(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        owned = self._managed(1)[0]
        unowned = {**owned, "id": "19999"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, [])
            api = self.FakeApi(baseline)
            guard = self._guard(api, path)
            guard.capture_baseline()
            self._write_ownership(path, [owned], recovery=True)
            api.guild = copy.deepcopy(baseline + [owned, unowned])
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_publisher_recovery_required$",
            ):
                guard.capture_published()
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_cleanup_verification_failed$",
            ):
                guard.restore()
        self.assertEqual(api.deleted, [owned["id"]])
        self.assertIn(unowned, api.guild)

    def test_foreign_and_global_drift_cleanup_owned_then_fail(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed()
        for mutation in ("foreign", "global"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "ownership.json"
                self._write_ownership(path, [])
                api = self.FakeApi(baseline)
                guard = self._guard(api, path)
                guard.capture_baseline()
                self._write_ownership(path, managed)
                api.guild = copy.deepcopy(baseline + managed)
                self.assertTrue(guard.capture_published())
                if mutation == "foreign":
                    api.guild[0]["description"] = "changed"
                else:
                    api.global_value.append({"id": "90002", "type": 1, "name": "drift"})
                with self.assertRaisesRegex(
                    self.module.GuardFailure,
                    "^guard_cleanup_verification_failed$",
                ):
                    guard.restore()
                self.assertEqual(set(api.deleted), {item["id"] for item in managed})

    def test_owned_shape_drift_is_not_deleted(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed(2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, [])
            api = self.FakeApi(baseline)
            guard = self._guard(api, path)
            guard.capture_baseline()
            self._write_ownership(path, managed)
            api.guild = copy.deepcopy(baseline + managed)
            api.guild[-1]["description"] = "drifted"
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_managed_shape_drift$",
            ):
                guard.restore()
        self.assertEqual(api.deleted, [])

    def test_inter_delete_shape_race_stops_before_changed_id(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed(2)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, [])
            api = self.FakeApi(baseline)
            guard = self._guard(api, path)
            guard.capture_baseline()
            self._write_ownership(path, managed)
            api.guild = copy.deepcopy(baseline + managed)

            def mutate_next(_application_id, _guild_id, command_id):
                api.guild = [item for item in api.guild if item["id"] != command_id]
                api.guild[-1]["description"] = "concurrent drift"

            api.delete_hook = mutate_next
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_managed_shape_drift$",
            ):
                guard.restore()
        self.assertEqual(api.deleted, [managed[0]["id"]])
        self.assertIn(managed[1]["id"], {item["id"] for item in api.guild})

    def test_ambiguous_delete_not_applied_fails_and_keeps_command(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed(1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            self._write_ownership(path, [])
            api = self.FakeApi(baseline)
            guard = self._guard(api, path)
            guard.capture_baseline()
            self._write_ownership(path, managed)
            api.guild = copy.deepcopy(baseline + managed)

            def fail_delete(_application_id, _guild_id, _command_id):
                raise self.module.GuardFailure("guard_discord_request_failed")

            api.delete_hook = fail_delete
            with self.assertRaisesRegex(
                self.module.GuardFailure,
                "^guard_cleanup_delete_ambiguous$",
            ):
                guard.restore()
        self.assertIn(managed[0], api.guild)

    def test_cleanup_signal_before_poll_uses_ledger_not_command_name(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed(1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._config(root)
            self._write_ownership(config.ownership_path, managed)
            config.cleanup_path.touch()
            api = self.FakeApi(baseline + managed)
            api.baseline_once = baseline
            self.module.run_guard(
                "secret.token-1",
                config,
                api_factory=lambda _token: api,
                sleep=lambda _seconds: None,
                monotonic=lambda: 0.0,
            )
            status = json.loads(config.status_path.read_text(encoding="utf-8"))
        self.assertEqual(status["state"], "restored")
        self.assertEqual(api.deleted, [managed[0]["id"]])

    def test_status_oserror_and_keyboard_interrupt_still_cleanup_known_owned(self) -> None:
        baseline = [{"id": "90001", "type": 1, "name": "foreign"}]
        managed = self._managed(2)
        for failure in (OSError("status unavailable"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = self._config(root)
                self._write_ownership(config.ownership_path, managed)
                config.cleanup_path.touch()
                api = self.FakeApi(baseline + managed)
                api.baseline_once = baseline
                with patch.object(self.module, "write_status", side_effect=failure):
                    with self.assertRaises(type(failure)):
                        self.module.run_guard(
                            "secret.token-1",
                            config,
                            api_factory=lambda _token: api,
                            sleep=lambda _seconds: None,
                            monotonic=lambda: 0.0,
                        )
                self.assertEqual(set(api.deleted), {item["id"] for item in managed})
                self.assertEqual(self.module.canonical(api.guild), self.module.canonical(baseline))


if __name__ == "__main__":
    unittest.main()
