from __future__ import annotations

import copy
import importlib.util
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
PYTHON_RUNNER = REPO_ROOT / "tools" / "run_discord_command_registry_live_validation.py"
POWERSHELL_RUNNER = REPO_ROOT / "tools" / "run_discord_command_registry_live_validation.ps1"
TARGET_ID = 12345678901234567
OTHER_ID = 12345678901234568
APPLICATION_ID = 98765432109876543


def load_runner():
    name = "evelyn_command_registry_live_validation"
    spec = importlib.util.spec_from_file_location(name, PYTHON_RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeDiscordHttp:
    def __init__(self, _token: str, *, failure: str = "") -> None:
        self.failure = failure
        self.target = [
            {"id": "70000000000000001", "type": 1, "name": "foreign", "description": "keep"}
        ]
        self.other = [
            {"id": "70000000000000002", "type": 1, "name": "other", "description": "keep"}
        ]
        self.globals = [
            {"id": "70000000000000003", "type": 1, "name": "global", "description": "keep"}
        ]
        self.next_id = 80000000000000000
        self.deleted: list[int] = []
        self.closed = False

    async def application_id(self):
        return APPLICATION_ID

    async def guild_ids(self):
        return [TARGET_ID, OTHER_ID]

    async def get_guild_commands(self, _application_id, guild_id):
        return copy.deepcopy(self.target if guild_id == TARGET_ID else self.other)

    async def get_global_commands(self, _application_id):
        return copy.deepcopy(self.globals)

    async def request(self, _route, *, json, raise_for_status):
        command = {**copy.deepcopy(json), "id": str(self.next_id)}
        self.next_id += 1
        self.target.append(command)
        status = 200 if self.failure == "unknown" else 201
        await raise_for_status(SimpleNamespace(status=status))
        if self.failure == "response_loss":
            self.failure = ""
            raise OSError("response body lost")
        if self.failure == "interrupt":
            self.failure = ""
            raise KeyboardInterrupt()
        return copy.deepcopy(command)

    async def edit_guild_command(self, _application_id, _guild_id, command_id, payload):
        if self.failure == "edit_oserror":
            self.failure = ""
            raise OSError("edit unavailable")
        current = next(item for item in self.target if int(item["id"]) == command_id)
        replacement = {**copy.deepcopy(payload), "id": current["id"]}
        self.target[self.target.index(current)] = replacement
        return copy.deepcopy(replacement)

    async def delete_guild_command(self, _application_id, _guild_id, command_id):
        self.deleted.append(command_id)
        self.target = [item for item in self.target if int(item["id"]) != command_id]

    def close(self):
        self.closed = True


class DiscordCommandRegistryLiveValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_runner()

    def tearDown(self) -> None:
        sys.modules.pop("evelyn_command_registry_live_validation", None)

    def action(self, root: Path) -> dict[str, str]:
        lease_path = root / "lease.lock"
        lease_path.write_bytes(b"1")
        return {
            "guildId": str(TARGET_ID),
            "runId": "a" * 32,
            "ledgerPath": str(root / "ownership.json"),
            "leasePath": str(lease_path),
            "mode": "validate",
        }

    def test_strict_stdin_token_then_bounded_action_and_preexisting_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action = self.action(root)
            token, parsed = self.module.read_startup(
                io.BytesIO(b"secret.token-1\n" + json.dumps(action).encode() + b"\n")
            )
            self.assertEqual(token, "secret.token-1")
            self.assertEqual(parsed, action)
            named = {**action, "guildName": "테스트 서버"}
            token, parsed = self.module.read_startup(
                io.BytesIO(b"secret.token-1\n" + json.dumps(named).encode() + b"\n")
            )
            self.assertEqual(token, "secret.token-1")
            self.assertEqual(parsed, named)
            with self.assertRaisesRegex(self.module.ValidationFailure, "^input_invalid$"):
                self.module.read_startup(
                    io.BytesIO(
                        b"secret.token-1\n"
                        + json.dumps(action).encode()
                        + b"\nextra\n"
                    )
                )
            Path(action["ledgerPath"]).touch()
            with self.assertRaisesRegex(self.module.ValidationFailure, "^input_invalid$"):
                self.module.read_startup(
                    io.BytesIO(b"secret.token-1\n" + json.dumps(action).encode() + b"\n")
                )

        for raw in (b"missing-action\n", b"bad token\n{}\n", b"x\n{}\nextra\n"):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(self.module.ValidationFailure):
                    self.module.read_startup(io.BytesIO(raw))

    def test_exclusive_lease_blocks_concurrent_child_and_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lease_path = Path(temporary) / "lease.lock"
            lease_path.write_bytes(b"1")
            first = self.module.ExclusiveLease(lease_path)
            try:
                with self.assertRaisesRegex(self.module.ValidationFailure, "^input_invalid$"):
                    self.module.ExclusiveLease(lease_path)
            finally:
                first.close()
            second = self.module.ExclusiveLease(lease_path)
            second.close()

    def test_single_guild_resolution_requires_exactly_one_membership(self) -> None:
        action = {
            "guildId": "single",
            "runId": "a" * 32,
            "ledgerPath": "unused",
            "leasePath": "unused",
            "mode": "validate",
        }

        class MembershipApi:
            def __init__(self, values):
                self.values = values

            async def guild_ids(self):
                return self.values

        resolved = __import__("asyncio").run(
            self.module._resolve_single_guild_action(MembershipApi([TARGET_ID]), action)
        )
        self.assertEqual(resolved["guildId"], str(TARGET_ID))
        for memberships in ([], [TARGET_ID, OTHER_ID]):
            with self.subTest(memberships=len(memberships)):
                with self.assertRaisesRegex(
                    self.module.ValidationFailure,
                    "^target_unavailable$",
                ):
                    __import__("asyncio").run(
                        self.module._resolve_single_guild_action(
                            MembershipApi(memberships),
                            action,
                        )
                    )

    def test_exact_guild_name_resolution_requires_unique_ordinal_match(self) -> None:
        class MembershipApi:
            def __init__(self, records):
                self.records = records
                self.closed = False

            async def guild_records(self):
                return self.records

            def close(self):
                self.closed = True

        for records, name, expected in (
            ([{"id": str(TARGET_ID), "name": "테스트 서버"}], "테스트 서버", str(TARGET_ID)),
            ([{"id": str(TARGET_ID), "name": "Alpha"}], "alpha", None),
            (
                [
                    {"id": str(TARGET_ID), "name": "same"},
                    {"id": str(OTHER_ID), "name": "same"},
                ],
                "same",
                None,
            ),
        ):
            with self.subTest(name=name, count=len(records)):
                api = MembershipApi(records)
                if expected is None:
                    with self.assertRaisesRegex(
                        self.module.ValidationFailure,
                        "^target_unavailable$",
                    ):
                        __import__("asyncio").run(
                            self.module.resolve_exact_guild_name(
                                "secret", name, api_factory=lambda _token: api
                            )
                        )
                else:
                    self.assertEqual(
                        __import__("asyncio").run(
                            self.module.resolve_exact_guild_name(
                                "secret", name, api_factory=lambda _token: api
                            )
                        ),
                        expected,
                    )
                self.assertTrue(api.closed)

    def test_validate_child_rechecks_same_exact_named_target(self) -> None:
        action = {
            "guildId": str(TARGET_ID),
            "guildName": "테스트 서버",
        }

        class MembershipApi:
            def __init__(self, records):
                self.records = records

            async def guild_records(self):
                return self.records

        __import__("asyncio").run(
            self.module.assert_exact_named_target(
                MembershipApi([{"id": str(TARGET_ID), "name": "테스트 서버"}]),
                action,
            )
        )
        for records in (
            [{"id": str(TARGET_ID), "name": "renamed"}],
            [
                {"id": str(TARGET_ID), "name": "테스트 서버"},
                {"id": str(OTHER_ID), "name": "테스트 서버"},
            ],
        ):
            with self.subTest(records=len(records)):
                with self.assertRaisesRegex(
                    self.module.ValidationFailure,
                    "^target_unavailable$",
                ):
                    __import__("asyncio").run(
                        self.module.assert_exact_named_target(
                            MembershipApi(records), action
                        )
                    )

    def test_name_change_during_baseline_prevents_every_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            api = FakeDiscordHttp("secret")

            async def renamed_membership():
                return [{"id": str(TARGET_ID), "name": "renamed"}]

            api.guild_records = renamed_membership
            action = {**self.action(Path(temporary)), "guildName": "테스트 서버"}
            result, code = __import__("asyncio").run(
                self.module.run_validation(
                    "secret.token-1",
                    action,
                    api_factory=lambda _token: api,
                )
            )

        self.assertEqual(code, 1)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["publishedVerified"])
        self.assertTrue(result["restoredVerified"])
        self.assertFalse(result["recoveryRequired"])
        self.assertEqual(api.deleted, [])
        self.assertEqual(api.next_id, 80000000000000000)

    def test_guild_name_startup_is_bounded_utf8_and_control_free(self) -> None:
        token, name = self.module.read_guild_name_startup(
            io.BytesIO("secret.token-1\n테스트 서버\n".encode())
        )
        self.assertEqual((token, name), ("secret.token-1", "테스트 서버"))
        for raw in (
            b"secret.token-1\n\n",
            b"secret.token-1\nbad\x00name\n",
            b"secret.token-1\nname\nextra",
            b"secret.token-1\n" + b"a" * 401 + b"\n",
        ):
            with self.subTest(length=len(raw)):
                with self.assertRaisesRegex(
                    self.module.ValidationFailure,
                    "^input_invalid$",
                ):
                    self.module.read_guild_name_startup(io.BytesIO(raw))

    def test_recovery_discards_only_exact_regular_atomic_ledger_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action = self.action(root)
            atomic = root / f".ownership.json.{action['runId']}.tmp"
            atomic.write_text("partial", encoding="utf-8")
            self.module.discard_stale_ownership_temporary(action)
            self.assertFalse(atomic.exists())
            atomic.mkdir()
            with self.assertRaisesRegex(self.module.ValidationFailure, "^input_invalid$"):
                self.module.discard_stale_ownership_temporary(action)

    def test_actual_production_publish_and_clear_restore_every_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            api = FakeDiscordHttp("secret")
            before_target = copy.deepcopy(api.target)
            result, code = __import__("asyncio").run(
                self.module.run_validation(
                    "secret.token-1",
                    self.action(root),
                    api_factory=lambda _token: api,
                )
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "passed")
        self.assertTrue(result["publishedVerified"])
        self.assertTrue(result["restoredVerified"])
        self.assertFalse(result["recoveryRequired"])
        self.assertEqual(api.target, before_target)
        self.assertEqual(len(api.deleted), 5)
        self.assertTrue(api.closed)
        public = json.dumps(result, ensure_ascii=False)
        for forbidden in (
            "secret.token-1",
            str(TARGET_ID),
            "기록열람",
            "foreign",
            "hash",
            "body",
        ):
            self.assertNotIn(forbidden, public)

    def test_ctrl_c_oserror_and_201_response_loss_cleanup_exact_owned(self) -> None:
        for failure, expected_code in (
            ("interrupt", 130),
            ("edit_oserror", 1),
            ("response_loss", 1),
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                api = FakeDiscordHttp("secret", failure=failure)
                baseline = copy.deepcopy(api.target)
                result, code = __import__("asyncio").run(
                    self.module.run_validation(
                        "secret.token-1",
                        self.action(Path(temporary)),
                        api_factory=lambda _token: api,
                    )
                )
                self.assertEqual(code, expected_code)
                self.assertEqual(result["state"], "failed")
                self.assertTrue(result["restoredVerified"])
                self.assertFalse(result["recoveryRequired"])
                self.assertEqual(api.target, baseline)
                self.assertEqual(len(api.deleted), 1)

    def test_production_clear_failure_is_not_hidden_by_fallback_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            api = FakeDiscordHttp("secret")
            baseline = copy.deepcopy(api.target)

            def composition_factory(http, application_id, action):
                real = self.module._build_composition(http, application_id, action)

                class ClearFailureProxy:
                    def __getattr__(self, name):
                        return getattr(real, name)

                    async def _clear_conversation_archive_application_commands(self):
                        raise RuntimeError("production clear failed")

                return ClearFailureProxy()

            result, code = __import__("asyncio").run(
                self.module.run_validation(
                    "secret.token-1",
                    self.action(Path(temporary)),
                    api_factory=lambda _token: api,
                    composition_factory=composition_factory,
                )
            )

        self.assertEqual(code, 1)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["failure"], "cleanup_failed")
        self.assertTrue(result["restoredVerified"])
        self.assertFalse(result["recoveryRequired"])
        self.assertEqual(api.target, baseline)
        self.assertEqual(len(api.deleted), 5)

    def test_recovery_adopts_exact_run_temporary_and_preserves_foreign(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            action = self.action(Path(temporary))
            api = FakeDiscordHttp("secret")
            baseline = copy.deepcopy(api.target)
            composition = self.module._build_composition(api, APPLICATION_ID, action)
            bot, guild_id, payloads, desired_shapes = (
                composition._conversation_archive_command_context()
            )
            composition._persist_conversation_archive_command_ownership(bot, guild_id)
            temporary_payload, _temporary_shape = next(
                iter(
                    composition._temporary_conversation_archive_command_payloads(
                        payloads,
                        desired_shapes,
                        action["runId"],
                    ).values()
                )
            )
            temporary_id = 89999999999999999
            api.target.append({**copy.deepcopy(temporary_payload), "id": str(temporary_id)})
            action["mode"] = "recover"

            result, code = __import__("asyncio").run(
                self.module.run_recovery(
                    "secret.token-1",
                    action,
                    api_factory=lambda _token: api,
                    quiescence_sec=0,
                    poll_interval_sec=0,
                    stable_polls_required=2,
                )
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "passed")
        self.assertFalse(result["publishedVerified"])
        self.assertTrue(result["restoredVerified"])
        self.assertFalse(result["recoveryRequired"])
        self.assertEqual(api.target, baseline)
        self.assertEqual(api.deleted, [temporary_id])

    def test_recovery_quiescence_catches_late_server_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            action = self.action(Path(temporary))
            api = FakeDiscordHttp("secret")
            baseline = copy.deepcopy(api.target)
            composition = self.module._build_composition(api, APPLICATION_ID, action)
            bot, guild_id, payloads, desired_shapes = (
                composition._conversation_archive_command_context()
            )
            composition._persist_conversation_archive_command_ownership(bot, guild_id)
            temporary_payload, _temporary_shape = next(
                iter(
                    composition._temporary_conversation_archive_command_payloads(
                        payloads,
                        desired_shapes,
                        action["runId"],
                    ).values()
                )
            )
            temporary_id = 89999999999999998
            action["mode"] = "recover"
            sleeps = 0

            async def reveal_after_first_poll(_delay):
                nonlocal sleeps
                sleeps += 1
                if sleeps == 1:
                    api.target.append(
                        {**copy.deepcopy(temporary_payload), "id": str(temporary_id)}
                    )

            result, code = __import__("asyncio").run(
                self.module.run_recovery(
                    "secret.token-1",
                    action,
                    api_factory=lambda _token: api,
                    sleep=reveal_after_first_poll,
                    monotonic=lambda: 0.0,
                    quiescence_sec=0,
                    poll_interval_sec=0,
                    stable_polls_required=2,
                )
            )

        self.assertEqual(code, 0)
        self.assertEqual(result["state"], "passed")
        self.assertEqual(api.target, baseline)
        self.assertEqual(api.deleted, [temporary_id])

    def test_unknown_command_is_never_name_deleted_and_fails_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            api = FakeDiscordHttp("secret", failure="unknown")
            result, code = __import__("asyncio").run(
                self.module.run_validation(
                    "secret.token-1",
                    self.action(Path(temporary)),
                    api_factory=lambda _token: api,
                )
            )

        self.assertEqual(code, 1)
        self.assertEqual(result["state"], "failed")
        self.assertFalse(result["restoredVerified"])
        self.assertTrue(result["recoveryRequired"])
        self.assertEqual(api.deleted, [])
        self.assertEqual(len(api.target), 2)

    def test_adapter_reports_201_before_invalid_body_failure(self) -> None:
        api = self.module.DiscordHttpOnly("secret.token-1")
        api._async = AsyncMock(side_effect=self.module.ObservedHttpFailure(201))
        statuses: list[int] = []

        async def observe(response):
            statuses.append(response.status)

        with self.assertRaisesRegex(self.module.ValidationFailure, "^http_failed$"):
            __import__("asyncio").run(
                api.request(
                    SimpleNamespace(method="POST", url="https://invalid.local"),
                    json={},
                    raise_for_status=observe,
                )
            )
        self.assertEqual(statuses, [201])
        api.close()

    def test_adapter_preserves_201_on_real_incomplete_read(self) -> None:
        api = self.module.DiscordHttpOnly("secret.token-1")

        class IncompleteResponse:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum):
                raise http.client.IncompleteRead(b"{", 10)

        api._opener.open = lambda *_args, **_kwargs: IncompleteResponse()
        try:
            with self.assertRaises(self.module.ObservedHttpFailure) as caught:
                api._call("POST", "https://discord.com/api/v10/test", {})
        finally:
            api.close()
        self.assertEqual(caught.exception.status, 201)

    def test_registry_comparison_preserves_nested_option_order(self) -> None:
        left = [{"id": "1", "name": "x", "options": [{"name": "a"}, {"name": "b"}]}]
        reordered_registry = [
            {"id": "2", "name": "y"},
            {"id": "1", "name": "x", "options": [{"name": "a"}, {"name": "b"}]},
        ]
        self.assertTrue(self.module._same(left + [{"id": "2", "name": "y"}], reordered_registry))
        right = [{"id": "1", "name": "x", "options": [{"name": "b"}, {"name": "a"}]}]
        self.assertFalse(self.module._same(left, right))
        self.assertFalse(self.module._same([], {"not": "a registry"}))

    def test_http_adapter_rejects_redirects_and_non_discord_origins(self) -> None:
        api = self.module.DiscordHttpOnly("secret.token-1")
        with self.assertRaisesRegex(self.module.ValidationFailure, "^http_failed$"):
            api._call("GET", "https://example.invalid/api/v10/test")
        request = self.module.urllib.request.Request("https://discord.com/api/v10/test")
        self.assertIsNone(
            self.module._NoRedirect().redirect_request(
                request, None, 302, "Found", {}, "https://example.invalid/steal"
            )
        )
        api.close()

    def test_http_adapter_rejects_unexpected_success_status_per_method(self) -> None:
        class StatusResponse:
            def __init__(self, status):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _maximum):
                return b"[]"

        for method, status in (("GET", 202), ("POST", 200), ("PATCH", 201), ("DELETE", 200)):
            with self.subTest(method=method):
                api = self.module.DiscordHttpOnly("secret.token-1")
                api._opener.open = lambda *_args, **_kwargs: StatusResponse(status)
                with self.assertRaises(self.module.ObservedHttpFailure) as caught:
                    api._call(method, "https://discord.com/api/v10/test", {})
                self.assertEqual(caught.exception.status, status)
                self.assertTrue(api.protocol_anomaly)
                api.close()

    def test_guild_pagination_requires_complete_strict_forward_pages(self) -> None:
        base = 70000000000000000
        first_page = [
            {"id": str(base + index), "name": f"guild-{index}"}
            for index in range(200)
        ]
        api = self.module.DiscordHttpOnly("secret.token-1")
        api._async = AsyncMock(
            side_effect=[
                (200, first_page),
                (200, [{"id": str(base + 200), "name": "final"}]),
            ]
        )
        try:
            records = __import__("asyncio").run(api.guild_records())
        finally:
            api.close()
        self.assertEqual(len(records), 201)
        self.assertIn(f"after={base + 199}", api._async.await_args_list[1].args[1])

        malformed_pages = (
            first_page[:-2] + [first_page[-1], first_page[-2]],
            first_page,
        )
        for malformed in malformed_pages:
            with self.subTest(kind="unordered" if malformed is malformed_pages[0] else "backward"):
                api = self.module.DiscordHttpOnly("secret.token-1")
                if malformed is malformed_pages[0]:
                    responses = [(200, malformed)]
                else:
                    responses = [
                        (200, malformed),
                        (200, [{"id": str(base - 1), "name": "backward"}]),
                    ]
                api._async = AsyncMock(side_effect=responses)
                try:
                    with self.assertRaisesRegex(
                        self.module.ValidationFailure,
                        "^http_failed$",
                    ):
                        __import__("asyncio").run(api.guild_records())
                finally:
                    api.close()

    def test_source_contract_has_no_secret_transport_or_external_runtime(self) -> None:
        python_source = PYTHON_RUNNER.read_text(encoding="utf-8")
        powershell_source = POWERSHELL_RUNNER.read_text(encoding="utf-8")
        self.assertIn("_publish_conversation_archive_application_commands()", python_source)
        self.assertIn("_clear_conversation_archive_application_commands()", python_source)
        self.assertNotIn("asyncio.to_thread", python_source)
        self.assertNotIn("urllib.request.urlopen", python_source)
        self.assertIn("_NoRedirect", python_source)
        self.assertIn("self._authorization = \"\"", python_source)
        self.assertIn('if "DISCORD_BOT_TOKEN" in os.environ:', python_source)
        self.assertNotIn("traceback", python_source.lower())
        self.assertNotIn("bulk_", python_source)
        self.assertNotIn("delete_by_name", python_source)

        self.assertIn("Read-EvelynDiscordTokenCache", powershell_source)
        self.assertIn("Assert-EvelynDiscordTokenBytes", powershell_source)
        self.assertIn("$stream.Write($TokenBytes", powershell_source)
        self.assertIn("$stream.WriteByte(10)", powershell_source)
        self.assertIn("SetAccessRuleProtection($true, $false)", powershell_source)
        self.assertIn("[IO.FileSystemAclExtensions]::SetAccessControl(", powershell_source)
        self.assertNotIn("Set-Acl -LiteralPath", powershell_source)
        self.assertIn("S-1-5-18", powershell_source)
        self.assertIn("GetOwner([Security.Principal.SecurityIdentifier])", powershell_source)
        self.assertIn("GetAccessRules(", powershell_source)
        self.assertIn("ReparsePoint", powershell_source)
        self.assertIn("[Array]::Clear($tokenBytes", powershell_source)
        self.assertIn("$startInfo.ArgumentList.Add('-I')", powershell_source)
        self.assertIn("@(Compare-Object $expectedProperties $actualProperties).Count", powershell_source)
        self.assertIn("[IO.File]::Delete($child.FullName)", powershell_source)
        self.assertIn("[IO.Directory]::Delete($resolved, $false)", powershell_source)
        self.assertNotIn("Remove-Item -LiteralPath $resolved -Recurse", powershell_source)
        self.assertNotIn("$parsed | ConvertTo-Json", powershell_source)
        self.assertIn("command-registry-live-validation-v1", powershell_source)
        self.assertIn("$mutex.WaitOne(0)", powershell_source)
        self.assertIn("[IO.FileShare]::Delete", powershell_source)
        self.assertIn("[IO.FileShare]::None", powershell_source)
        self.assertIn("'state.lock'", powershell_source)
        self.assertIn("$stateLeaseStream.Lock(0, 1)", powershell_source)
        self.assertIn('"launcher_${launcherPhase}_failed"', powershell_source)
        self.assertIn("$leaseStream.Lock(0, 1)", powershell_source)
        self.assertIn("-Mode 'recover'", powershell_source)
        self.assertIn("[IO.Directory]::Move($staleRoot, $quarantine)", powershell_source)
        self.assertIn("^recovered-", powershell_source)
        self.assertIn("$preMutationStaging", powershell_source)
        self.assertNotIn("[IO.Path]::GetTempPath()", powershell_source)
        self.assertIn("$process.WaitForExit()", powershell_source)
        self.assertEqual(powershell_source.count("Select-Object -First 1"), 2)
        self.assertNotIn("$process.Kill(", powershell_source)
        self.assertNotIn("WaitForExit(180000)", powershell_source)
        self.assertIn("$GuildId = 'single'", powershell_source)
        self.assertIn("Read-BoundedTargetInput", powershell_source)
        self.assertIn("[Console]::OpenStandardInput()", powershell_source)
        self.assertIn("[Text.UTF8Encoding]::new($false, $true).GetString(", powershell_source)
        self.assertIn("[switch]$TargetGuildFromStdin", powershell_source)
        self.assertIn("[switch]$TargetGuildNameFromStdin", powershell_source)
        self.assertIn("--resolve-guild-name", powershell_source)
        self.assertIn("target_resolution_child_input", powershell_source)
        self.assertIn("target_resolution_child_target", powershell_source)
        self.assertIn("target_resolution_child_http", powershell_source)
        self.assertIn("target_resolution_output", powershell_source)
        self.assertIn("$TargetGuildFromStdin -and $TargetGuildNameFromStdin", powershell_source)
        self.assertLess(
            powershell_source.index("Resolve-ExactGuildIdByName $GuildName $tokenBytes"),
            powershell_source.index("foreach ($staleRootItem in $staleRoots)"),
        )
        self.assertIn("[string]$ledger.guildId -cne $GuildId", powershell_source)
        self.assertIn("-GuildName $GuildName", powershell_source)
        self.assertNotIn("[string]$GuildId =", powershell_source)
        self.assertIn("-cnotin @('passed', 'failed')", powershell_source)
        self.assertNotRegex(powershell_source, r"ArgumentList\.Add\([^\r\n]*(?:token|GuildId)")
        self.assertNotIn("docker", powershell_source.lower())
        self.assertNotIn("microphone", powershell_source.lower())

    def test_invalid_child_input_emits_only_fixed_content_free_schema(self) -> None:
        environment = os.environ.copy()
        environment.pop("DISCORD_BOT_TOKEN", None)
        completed = subprocess.run(
            [sys.executable, "-I", str(PYTHON_RUNNER)],
            input="do-not-echo\n{}\n",
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(
            set(payload),
            {
                "schema",
                "state",
                "contentFree",
                "publishedVerified",
                "restoredVerified",
                "recoveryRequired",
                "failure",
            },
        )
        self.assertNotIn("do-not-echo", completed.stdout)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(completed.returncode, 64)

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
    def test_explicit_target_rejects_invalid_stdin_before_live_work(self) -> None:
        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(POWERSHELL_RUNNER),
                "-RunLive",
                "-TargetGuildFromStdin",
            ],
            cwd=REPO_ROOT,
            input="invalid\n",
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["failure"], "launcher_target_failed")
        self.assertFalse(payload["publishedVerified"])
        self.assertTrue(payload["recoveryRequired"])
        self.assertEqual(completed.stderr, "")

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
    def test_target_modes_conflict_before_live_work(self) -> None:
        completed = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(POWERSHELL_RUNNER),
                "-RunLive",
                "-TargetGuildFromStdin",
                "-TargetGuildNameFromStdin",
            ],
            cwd=REPO_ROOT,
            input="not-used\n",
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(payload["failure"], "launcher_target_failed")
        self.assertFalse(payload["publishedVerified"])
        self.assertTrue(payload["recoveryRequired"])
        self.assertEqual(completed.stderr, "")

    @unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-only")
    def test_default_launcher_is_side_effect_free_and_content_free(self) -> None:
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(POWERSHELL_RUNNER)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["state"], "confirmation_required")
        self.assertTrue(payload["contentFree"])
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
