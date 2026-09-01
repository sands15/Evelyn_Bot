from __future__ import annotations

import asyncio
import http.client
import json
import logging
import math
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, Callable


API_ROOT = "https://discord.com/api/v10"
SCHEMA = "discord_command_registry.live-validation.v1"
MANAGED_NAMES = frozenset({"기록열람", "기록삭제", "기록동의", "기록철회", "피드백제출"})
MAX_TOKEN_BYTES = 512
MAX_ACTION_BYTES = 4096
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_GUILD_NAME_BYTES = 400
MAX_GUILD_PAGES = 100
_ID = re.compile(r"[1-9]\d{16,19}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


class ValidationFailure(RuntimeError):
    pass


class ObservedHttpFailure(ValidationFailure):
    def __init__(self, status: int) -> None:
        super().__init__("http_failed")
        self.status = status


class ExclusiveLease:
    def __init__(self, path: Path) -> None:
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("lease_not_regular")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise ValidationFailure("input_invalid") from None
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = -1
        if descriptor < 0:
            return
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def discard_stale_ownership_temporary(action: dict[str, str]) -> None:
    ledger_path = Path(action["ledgerPath"])
    temporary = ledger_path.parent / f".{ledger_path.name}.{action['runId']}.tmp"
    try:
        mode = os.lstat(temporary).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValidationFailure("input_invalid")
    try:
        temporary.unlink()
    except OSError as exc:
        raise ValidationFailure("input_invalid") from exc


def _line(stream: BinaryIO, maximum: int, code: str) -> bytes:
    raw = stream.readline(maximum + 2)
    if not raw.endswith(b"\n") or len(raw) > maximum + 1 or b"\r" in raw:
        raise ValidationFailure(code)
    return raw[:-1]


def read_startup(stream: BinaryIO) -> tuple[str, dict[str, str]]:
    token_bytes = _line(stream, MAX_TOKEN_BYTES, "input_invalid")
    action_bytes = _line(stream, MAX_ACTION_BYTES, "input_invalid")
    if stream.read(1):
        raise ValidationFailure("input_invalid")
    if not token_bytes or any(byte < 0x21 or byte > 0x7E for byte in token_bytes):
        raise ValidationFailure("input_invalid")
    try:
        token = token_bytes.decode("ascii")
        action = json.loads(action_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("input_invalid") from exc
    base_properties = {
        "guildId",
        "runId",
        "ledgerPath",
        "leasePath",
        "mode",
    }
    if not isinstance(action, dict) or frozenset(action) not in {
        frozenset(base_properties),
        frozenset(base_properties | {"guildName"}),
    }:
        raise ValidationFailure("input_invalid")
    guild_id = str(action["guildId"])
    run_id = str(action["runId"])
    ledger_path = Path(str(action["ledgerPath"]))
    lease_path = Path(str(action["leasePath"]))
    mode = str(action["mode"])
    guild_name = action.get("guildName")
    if (
        (guild_id != "single" and _ID.fullmatch(guild_id) is None)
        or _RUN_ID.fullmatch(run_id) is None
        or mode not in {"validate", "recover"}
        or not ledger_path.is_absolute()
        or ledger_path.name != "ownership.json"
        or not lease_path.is_absolute()
        or lease_path.name != "lease.lock"
        or lease_path.parent != ledger_path.parent
        or (mode == "validate" and ledger_path.exists())
        or (mode == "recover" and not ledger_path.exists())
        or (
            guild_name is not None
            and (
                mode != "validate"
                or guild_id == "single"
                or not isinstance(guild_name, str)
                or not 1 <= len(guild_name) <= 100
                or any(
                    unicodedata.category(character) == "Cc"
                    for character in guild_name
                )
            )
        )
    ):
        raise ValidationFailure("input_invalid")
    try:
        parent_mode = os.lstat(ledger_path.parent).st_mode
        lease_mode = os.lstat(lease_path).st_mode
    except OSError as exc:
        raise ValidationFailure("input_invalid") from exc
    if (
        not stat.S_ISDIR(parent_mode)
        or stat.S_ISLNK(parent_mode)
        or not stat.S_ISREG(lease_mode)
        or stat.S_ISLNK(lease_mode)
        or lease_path.stat().st_size != 1
    ):
        raise ValidationFailure("input_invalid")
    parsed_action = {
        "guildId": guild_id,
        "runId": run_id,
        "ledgerPath": str(ledger_path),
        "leasePath": str(lease_path),
        "mode": mode,
    }
    if guild_name is not None:
        parsed_action["guildName"] = guild_name
    return token, parsed_action


def read_guild_name_startup(stream: BinaryIO) -> tuple[str, str]:
    token_bytes = _line(stream, MAX_TOKEN_BYTES, "input_invalid")
    name_bytes = _line(stream, MAX_GUILD_NAME_BYTES, "input_invalid")
    if stream.read(1):
        raise ValidationFailure("input_invalid")
    if not token_bytes or any(byte < 0x21 or byte > 0x7E for byte in token_bytes):
        raise ValidationFailure("input_invalid")
    try:
        token = token_bytes.decode("ascii")
        guild_name = name_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise ValidationFailure("input_invalid") from exc
    if (
        not 1 <= len(guild_name) <= 100
        or any(unicodedata.category(character) == "Cc" for character in guild_name)
    ):
        raise ValidationFailure("input_invalid")
    return token, guild_name


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def _canonical_registry(value: Any) -> bytes:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValidationFailure("snapshot_failed")
    encoded = [
        json.dumps(
            _canonical_value(item),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        for item in value
    ]
    return b"[" + b",".join(sorted(encoded)) + b"]"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class DiscordHttpOnly:
    def __init__(self, token: str) -> None:
        self._authorization = f"Bot {token}"
        self._opener = urllib.request.build_opener(_NoRedirect())
        self.protocol_anomaly = False

    def close(self) -> None:
        self._authorization = ""

    def _call(self, method: str, url: str, payload: Any = None) -> tuple[int, Any]:
        parsed = urllib.parse.urlsplit(url)
        if (
            method not in {"GET", "POST", "PATCH", "DELETE"}
            or parsed.scheme != "https"
            or parsed.hostname != "discord.com"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not parsed.path.startswith("/api/v10/")
        ):
            raise ValidationFailure("http_failed")
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        for _attempt in range(5):
            request = urllib.request.Request(
                url,
                data=body,
                method=method,
                headers={
                    "Authorization": self._authorization,
                    "Content-Type": "application/json",
                    "User-Agent": "EvelynCommandRegistryValidation/1",
                },
            )
            try:
                with self._opener.open(request, timeout=20) as response:
                    status = response.status
                    expected_status = {
                        "GET": 200,
                        "POST": 201,
                        "PATCH": 200,
                        "DELETE": 204,
                    }[method]
                    if status != expected_status:
                        self.protocol_anomaly = True
                        raise ObservedHttpFailure(status)
                    try:
                        raw = response.read(MAX_RESPONSE_BYTES + 1)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise ValueError("response_too_large")
                        return status, None if not raw else json.loads(raw.decode())
                    except (
                        OSError,
                        http.client.HTTPException,
                        ValueError,
                        UnicodeError,
                        json.JSONDecodeError,
                    ):
                        raise ObservedHttpFailure(status) from None
            except urllib.error.HTTPError as exc:
                try:
                    raw = exc.read(4096)
                except (OSError, http.client.HTTPException):
                    raise ValidationFailure("http_failed") from None
                if exc.code != 429:
                    raise ValidationFailure("http_failed") from None
                try:
                    delay = float(json.loads(raw.decode()).get("retry_after", 1))
                except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                    delay = 1
                if not math.isfinite(delay) or delay < 0 or delay > 3_600:
                    raise ValidationFailure("http_failed") from None
                time.sleep(max(0.1, delay))
            except ValidationFailure:
                raise
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise ValidationFailure("http_failed") from None
        raise ValidationFailure("http_failed")

    async def _async(self, method: str, url: str, payload: Any = None) -> tuple[int, Any]:
        return self._call(method, url, payload)

    async def application_id(self) -> int:
        _status, payload = await self._async("GET", f"{API_ROOT}/oauth2/applications/@me")
        value = str(payload.get("id") if isinstance(payload, dict) else "")
        if _ID.fullmatch(value) is None:
            raise ValidationFailure("http_failed")
        return int(value)

    async def guild_records(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        seen: set[str] = set()
        after = "0"
        for _page_number in range(MAX_GUILD_PAGES):
            _status, payload = await self._async(
                "GET", f"{API_ROOT}/users/@me/guilds?limit=200&after={after}"
            )
            if not isinstance(payload, list) or len(payload) > 200:
                raise ValidationFailure("http_failed")
            page: list[dict[str, str]] = []
            page_ids: list[int] = []
            previous_after = int(after)
            for item in payload:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not isinstance(item.get("name"), str)
                    or _ID.fullmatch(item["id"]) is None
                    or item["id"] in seen
                    or not 1 <= len(item["name"]) <= 100
                    or any(
                        unicodedata.category(character) == "Cc"
                        for character in item["name"]
                    )
                ):
                    raise ValidationFailure("http_failed")
                seen.add(item["id"])
                page_ids.append(int(item["id"]))
                page.append({"id": item["id"], "name": item["name"]})
            if (
                any(guild_id <= previous_after for guild_id in page_ids)
                or page_ids != sorted(page_ids)
            ):
                raise ValidationFailure("http_failed")
            result.extend(page)
            if len(payload) < 200:
                return result
            next_after = page[-1]["id"]
            if next_after == after:
                raise ValidationFailure("http_failed")
            after = next_after
        raise ValidationFailure("http_failed")

    async def guild_ids(self) -> list[int]:
        return [int(item["id"]) for item in await self.guild_records()]

    async def get_guild_commands(self, application_id: int, guild_id: int) -> Any:
        return (await self._async(
            "GET", f"{API_ROOT}/applications/{application_id}/guilds/{guild_id}/commands"
        ))[1]

    async def get_global_commands(self, application_id: int) -> Any:
        return (await self._async("GET", f"{API_ROOT}/applications/{application_id}/commands"))[1]

    async def request(self, route: Any, *, json: Any, raise_for_status: Any) -> Any:
        try:
            status, payload = await self._async(str(route.method), str(route.url), json)
        except ObservedHttpFailure as exc:
            await raise_for_status(SimpleNamespace(status=exc.status))
            raise ValidationFailure("http_failed") from None
        await raise_for_status(SimpleNamespace(status=status))
        return payload

    async def edit_guild_command(
        self, application_id: int, guild_id: int, command_id: int, payload: Any
    ) -> Any:
        return (await self._async(
            "PATCH",
            f"{API_ROOT}/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
            payload,
        ))[1]

    async def delete_guild_command(
        self, application_id: int, guild_id: int, command_id: int
    ) -> None:
        await self._async(
            "DELETE",
            f"{API_ROOT}/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
        )


def _build_composition(api: Any, application_id: int, action: dict[str, str]) -> Any:
    repo = Path(__file__).resolve().parents[1]
    runtime = repo / "evelyn_core" / "runtime"
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    import discord
    from discord.ext import commands
    from evelyn_core.discord_app_composition_runtime import DiscordAppComposition

    events = SimpleNamespace(
        recover_search_followups=None,
        conversation_participation_tracker=None,
        conversation_archive_enabled=True,
        conversation_archive_command_guild_id=int(action["guildId"]),
        conversation_archive_command_ownership=(action["ledgerPath"], action["runId"]),
    )
    command_deps = SimpleNamespace(
        conversation_archive_enabled=True,
        is_control_command_authorized=lambda _ctx: True,
    )
    composition = DiscordAppComposition(SimpleNamespace(events=events, commands=command_deps))
    bot = commands.Bot(
        command_prefix="!",
        intents=discord.Intents.none(),
        help_command=None,
        application_id=application_id,
    )
    composition.register(bot)
    bot.http.get_guild_commands = api.get_guild_commands
    bot.http.get_global_commands = api.get_global_commands
    bot.http.request = api.request
    bot.http.edit_guild_command = api.edit_guild_command
    bot.http.delete_guild_command = api.delete_guild_command
    return composition


async def _resolve_single_guild_action(
    api: Any,
    action: dict[str, str],
) -> dict[str, str]:
    if action["guildId"] != "single":
        return action
    guild_ids = sorted(set(await api.guild_ids()))
    if len(guild_ids) != 1:
        raise ValidationFailure("target_unavailable")
    return {**action, "guildId": str(guild_ids[0])}


async def resolve_exact_guild_name(
    token: str,
    guild_name: str,
    *,
    api_factory: Callable[[str], Any] = DiscordHttpOnly,
) -> str:
    api = api_factory(token)
    try:
        matches = [
            item["id"]
            for item in await api.guild_records()
            if item["name"] == guild_name
        ]
        if len(matches) != 1:
            raise ValidationFailure("target_unavailable")
        return matches[0]
    finally:
        close = getattr(api, "close", None)
        if callable(close):
            close()


async def assert_exact_named_target(api: Any, action: dict[str, str]) -> None:
    guild_name = action.get("guildName")
    if guild_name is None:
        return
    matches = [
        item["id"]
        for item in await api.guild_records()
        if item["name"] == guild_name
    ]
    if matches != [action["guildId"]]:
        raise ValidationFailure("target_unavailable")


async def _snapshot(api: Any, application_id: int, target_guild_id: int) -> dict[str, Any]:
    guild_ids = sorted(set(await api.guild_ids()))
    if target_guild_id not in guild_ids:
        raise ValidationFailure("target_unavailable")
    target = await api.get_guild_commands(application_id, target_guild_id)
    global_commands = await api.get_global_commands(application_id)
    other: dict[str, Any] = {}
    for guild_id in guild_ids:
        if guild_id == target_guild_id:
            continue
        other[str(guild_id)] = await api.get_guild_commands(application_id, guild_id)
    registries = [target, global_commands, *other.values()]
    if any(
        not isinstance(registry, list)
        or any(not isinstance(item, dict) for item in registry)
        for registry in registries
    ):
        raise ValidationFailure("snapshot_failed")
    return {"target": target, "global": global_commands, "other": other}


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, list) or isinstance(right, list):
        try:
            return _canonical_registry(left) == _canonical_registry(right)
        except ValidationFailure:
            return False
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    return left == right


async def _fallback_clear(composition: Any) -> None:
    bot, guild_id, _payloads, _shapes = composition._conversation_archive_command_context()
    current = list(await bot.http.get_guild_commands(bot.application_id, guild_id))
    by_id = {int(command.get("id") or 0): command for command in current}
    owned = composition._conversation_archive_owned_commands
    for command_id, allowed_shapes in owned.items():
        command = by_id.get(command_id)
        if command is not None and not composition._owned_application_command_shape_matches(
            command, allowed_shapes
        ):
            raise ValidationFailure("cleanup_drift")
    for command_id, allowed_shapes in tuple(owned.items()):
        fresh = list(await bot.http.get_guild_commands(bot.application_id, guild_id))
        command = next((item for item in fresh if int(item.get("id") or 0) == command_id), None)
        if command is None:
            continue
        if not composition._owned_application_command_shape_matches(command, allowed_shapes):
            raise ValidationFailure("cleanup_drift")
        try:
            await bot.http.delete_guild_command(bot.application_id, guild_id, command_id)
        except Exception:
            remaining = await bot.http.get_guild_commands(bot.application_id, guild_id)
            if any(int(item.get("id") or 0) == command_id for item in remaining):
                raise ValidationFailure("cleanup_failed") from None


async def run_validation(
    token: str,
    action: dict[str, str],
    *,
    api_factory: Callable[[str], Any] = DiscordHttpOnly,
    composition_factory: Callable[[Any, int, dict[str, str]], Any] = _build_composition,
) -> tuple[dict[str, Any], int]:
    api = api_factory(token)
    composition = None
    before = None
    published_verified = False
    restored_verified = False
    application_id: int | None = None
    failure = ""
    interrupted = False
    try:
        action = await _resolve_single_guild_action(api, action)
        application_id = await api.application_id()
        target_id = int(action["guildId"])
        before = await _snapshot(api, application_id, target_id)
        if any(str(item.get("name") or "") in MANAGED_NAMES for item in before["target"]):
            raise ValidationFailure("baseline_not_empty")
        composition = composition_factory(api, application_id, action)
        await assert_exact_named_target(api, action)
        await composition._publish_conversation_archive_application_commands()
        published = await _snapshot(api, application_id, target_id)
        owned_ids = set(composition._conversation_archive_owned_commands)
        managed = [item for item in published["target"] if str(item.get("name") or "") in MANAGED_NAMES]
        foreign = [item for item in published["target"] if str(item.get("name") or "") not in MANAGED_NAMES]
        if (
            len(managed) != 5
            or {str(item.get("name") or "") for item in managed} != MANAGED_NAMES
            or {int(item.get("id") or 0) for item in managed} != owned_ids
            or not _same(foreign, before["target"])
            or not _same(published["global"], before["global"])
            or not _same(published["other"], before["other"])
        ):
            raise ValidationFailure("published_snapshot_mismatch")
        published_verified = True
    except KeyboardInterrupt:
        interrupted = True
        failure = "interrupted"
    except BaseException:
        failure = "validation_failed"
    finally:
        if composition is not None:
            try:
                await composition._clear_conversation_archive_application_commands()
            except BaseException:
                failure = "cleanup_failed"
                try:
                    await _fallback_clear(composition)
                except BaseException:
                    failure = "cleanup_failed"
        if before is not None and application_id is not None:
            try:
                after = await _snapshot(api, application_id, int(action["guildId"]))
                restored_verified = _same(after, before)
            except BaseException:
                restored_verified = False
        if not restored_verified:
            failure = "restore_mismatch" if failure != "cleanup_failed" else failure
        close = getattr(api, "close", None)
        if callable(close):
            close()
    ok = published_verified and restored_verified and not failure
    if getattr(api, "protocol_anomaly", False):
        failure = failure or "validation_failed"
        ok = False
    result = {
        "schema": SCHEMA,
        "state": "passed" if ok else "failed",
        "contentFree": True,
        "publishedVerified": published_verified,
        "restoredVerified": restored_verified,
        "recoveryRequired": not restored_verified,
        "failure": "" if ok else failure or "validation_failed",
    }
    return result, 0 if ok else (130 if interrupted else 1)


async def run_recovery(
    token: str,
    action: dict[str, str],
    *,
    api_factory: Callable[[str], Any] = DiscordHttpOnly,
    composition_factory: Callable[[Any, int, dict[str, str]], Any] = _build_composition,
    sleep: Callable[[float], Any] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    quiescence_sec: float = 30.0,
    poll_interval_sec: float = 1.0,
    stable_polls_required: int = 3,
) -> tuple[dict[str, Any], int]:
    api = api_factory(token)
    restored_verified = False
    failure = ""
    try:
        if (
            quiescence_sec < 0
            or poll_interval_sec < 0
            or stable_polls_required < 1
        ):
            raise ValidationFailure("recovery_failed")
        action = await _resolve_single_guild_action(api, action)
        application_id = await api.application_id()
        target_id = int(action["guildId"])
        baseline_target = None
        baseline_global = None
        baseline_other = None
        quiet_deadline = monotonic() + quiescence_sec
        stable_polls = 0
        while True:
            current = await _snapshot(api, application_id, target_id)
            composition = composition_factory(api, application_id, action)
            bot, guild_id, _payloads, desired_shapes = (
                composition._conversation_archive_command_context()
            )
            composition._load_conversation_archive_command_ownership(
                bot,
                guild_id,
                current["target"],
                desired_shapes,
                adopt_stale_temporary=True,
            )
            owned_ids = set(composition._conversation_archive_owned_commands)
            foreign_target = [
                command
                for command in current["target"]
                if int(command.get("id") or 0) not in owned_ids
            ]
            if baseline_target is None:
                baseline_target = foreign_target
                baseline_global = current["global"]
                baseline_other = current["other"]
            elif (
                not _same(foreign_target, baseline_target)
                or not _same(current["global"], baseline_global)
                or not _same(current["other"], baseline_other)
            ):
                raise ValidationFailure("recovery_failed")
            if owned_ids:
                try:
                    await composition._clear_conversation_archive_application_commands()
                except BaseException:
                    failure = "cleanup_failed"
                    await _fallback_clear(composition)
                quiet_deadline = monotonic() + quiescence_sec
                stable_polls = 0
            else:
                stable_polls += 1
                if (
                    monotonic() >= quiet_deadline
                    and stable_polls >= stable_polls_required
                ):
                    restored_verified = True
                    break
            await sleep(poll_interval_sec)
    except BaseException:
        failure = failure or "recovery_failed"
    finally:
        close = getattr(api, "close", None)
        if callable(close):
            close()
    if getattr(api, "protocol_anomaly", False):
        failure = failure or "recovery_failed"
        restored_verified = False
    if not restored_verified:
        failure = failure or "recovery_failed"
    ok = restored_verified and not failure
    return {
        "schema": SCHEMA,
        "state": "passed" if ok else "failed",
        "contentFree": True,
        "publishedVerified": False,
        "restoredVerified": restored_verified,
        "recoveryRequired": not restored_verified,
        "failure": "" if ok else failure,
    }, 0 if ok else 1


def resolve_guild_name_main() -> int:
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    token = ""
    try:
        if "DISCORD_BOT_TOKEN" in os.environ:
            raise ValidationFailure("input_invalid")
        token, guild_name = read_guild_name_startup(sys.stdin.buffer)
        guild_id = asyncio.run(resolve_exact_guild_name(token, guild_name))
    except ValidationFailure as exc:
        return {
            "input_invalid": 64,
            "target_unavailable": 65,
            "http_failed": 69,
        }.get(str(exc), 70)
    except BaseException:
        return 70
    finally:
        token = ""
        os.environ.pop("DISCORD_BOT_TOKEN", None)
    sys.stdout.write(guild_id + "\n")
    return 0


def main() -> int:
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    token = ""
    lease: ExclusiveLease | None = None
    result = {
        "schema": SCHEMA,
        "state": "failed",
        "contentFree": True,
        "publishedVerified": False,
        "restoredVerified": False,
        "recoveryRequired": True,
        "failure": "input_invalid",
    }
    exit_code = 64
    try:
        if sys.argv[1:]:
            raise ValidationFailure("input_invalid")
        if "DISCORD_BOT_TOKEN" in os.environ:
            raise ValidationFailure("input_invalid")
        token, action = read_startup(sys.stdin.buffer)
        lease = ExclusiveLease(Path(action["leasePath"]))
        if action["mode"] == "recover":
            discard_stale_ownership_temporary(action)
        operation = run_validation if action["mode"] == "validate" else run_recovery
        result, exit_code = asyncio.run(operation(token, action))
    except KeyboardInterrupt:
        result["failure"] = "interrupted"
        exit_code = 130
    except BaseException:
        pass
    finally:
        token = ""
        if lease is not None:
            lease.close()
        os.environ.pop("DISCORD_BOT_TOKEN", None)
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return exit_code


if __name__ == "__main__":
    if sys.argv[1:] == ["--resolve-guild-name"]:
        raise SystemExit(resolve_guild_name_main())
    raise SystemExit(main())
