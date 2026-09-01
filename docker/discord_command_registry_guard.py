from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable


API_ROOT = "https://discord.com/api/v10"
MANAGED_NAMES = frozenset(
    {"기록열람", "기록삭제", "기록동의", "기록철회", "피드백제출"}
)
MAX_TOKEN_BYTES = 512
MAX_CONFIG_BYTES = 4096
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OWNERSHIP_BYTES = 256 * 1024
STATUS_SCHEMA = "discord_command_registry.guard-status.v1"
OWNERSHIP_SCHEMA = "evelyn.discord-command-ownership.v2"
STATUS_PATH = Path("/run/evelyn-command-guard/status.json")
CLEANUP_PATH = Path("/run/evelyn-command-guard/cleanup.request")
OWNERSHIP_PATH = Path("/run/evelyn-command-guard/ownership.json")
_ID = re.compile(r"[1-9]\d{4,23}\Z")
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


class GuardFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class GuardConfig:
    guild_id: str
    status_path: Path
    cleanup_path: Path
    ownership_path: Path
    run_id: str
    publish_timeout_sec: int
    lifetime_sec: int


def _read_line(stream: BinaryIO, maximum: int, code: str) -> bytes:
    raw = stream.readline(maximum + 2)
    if not raw.endswith(b"\n") or len(raw) > maximum + 1:
        raise GuardFailure(code)
    return raw[:-1]


def read_startup(stream: BinaryIO) -> tuple[str, GuardConfig]:
    token_bytes = _read_line(stream, MAX_TOKEN_BYTES, "guard_token_invalid")
    if not token_bytes or any(byte < 0x21 or byte > 0x7E for byte in token_bytes):
        raise GuardFailure("guard_token_invalid")
    config_bytes = _read_line(stream, MAX_CONFIG_BYTES, "guard_config_invalid")
    try:
        token = token_bytes.decode("ascii")
        payload = json.loads(config_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GuardFailure("guard_config_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "guildId",
        "statusPath",
        "cleanupPath",
        "ownershipPath",
        "runId",
        "publishTimeoutSec",
        "lifetimeSec",
    }:
        raise GuardFailure("guard_config_invalid")
    guild_id = str(payload["guildId"])
    status_path = Path(str(payload["statusPath"]))
    cleanup_path = Path(str(payload["cleanupPath"]))
    ownership_path = Path(str(payload["ownershipPath"]))
    run_id = str(payload["runId"])
    publish_timeout = payload["publishTimeoutSec"]
    lifetime = payload["lifetimeSec"]
    if (
        _ID.fullmatch(guild_id) is None
        or isinstance(publish_timeout, bool)
        or not isinstance(publish_timeout, int)
        or not 30 <= publish_timeout <= 120
        or isinstance(lifetime, bool)
        or not isinstance(lifetime, int)
        or not publish_timeout <= lifetime <= 1_500
        or status_path != STATUS_PATH
        or cleanup_path != CLEANUP_PATH
        or ownership_path != OWNERSHIP_PATH
        or _RUN_ID.fullmatch(run_id) is None
    ):
        raise GuardFailure("guard_config_invalid")
    return token, GuardConfig(
        guild_id=guild_id,
        status_path=status_path,
        cleanup_path=cleanup_path,
        ownership_path=ownership_path,
        run_id=run_id,
        publish_timeout_sec=publish_timeout,
        lifetime_sec=lifetime,
    )


def canonical(commands: list[dict[str, Any]]) -> bytes:
    if not isinstance(commands, list) or any(not isinstance(item, dict) for item in commands):
        raise GuardFailure("guard_registry_invalid")
    ordered = sorted(
        commands,
        key=lambda item: (
            int(item.get("type", 0) or 0),
            str(item.get("name") or ""),
            str(item.get("id") or ""),
        ),
    )
    return json.dumps(
        ordered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(commands: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical(commands)).hexdigest()


def _managed(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in commands if str(item.get("name") or "") in MANAGED_NAMES]


def _foreign(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in commands if str(item.get("name") or "") not in MANAGED_NAMES]


def _temporary_command_name(run_id: str, final_name: str) -> str:
    return hashlib.sha256(f"{run_id}:{final_name}".encode("utf-8")).hexdigest()[:32]


def _choice_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GuardFailure("guard_owned_shape_invalid")
    return {
        "name": str(payload.get("name") or ""),
        "value": payload.get("value"),
        "name_localizations": dict(payload.get("name_localizations") or {}),
    }


def _option_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GuardFailure("guard_owned_shape_invalid")
    return {
        "type": int(payload.get("type") or 0),
        "name": str(payload.get("name") or ""),
        "description": str(payload.get("description") or ""),
        "required": payload.get("required") is True,
        "choices": [_choice_shape(value) for value in payload.get("choices") or ()],
        "options": [_option_shape(value) for value in payload.get("options") or ()],
        "channel_types": sorted(int(value) for value in payload.get("channel_types") or ()),
        "min_value": payload.get("min_value"),
        "max_value": payload.get("max_value"),
        "min_length": payload.get("min_length"),
        "max_length": payload.get("max_length"),
        "autocomplete": payload.get("autocomplete") is True,
        "name_localizations": dict(payload.get("name_localizations") or {}),
        "description_localizations": dict(payload.get("description_localizations") or {}),
    }


def command_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise GuardFailure("guard_owned_shape_invalid")
    permissions = payload.get("default_member_permissions")
    if permissions is not None:
        permissions = str(permissions)
    return {
        "type": int(payload.get("type") or 0),
        "name": str(payload.get("name") or ""),
        "description": str(payload.get("description") or ""),
        "options": [_option_shape(value) for value in payload.get("options") or ()],
        "contexts": sorted(int(value) for value in payload.get("contexts") or [0]),
        "integration_types": sorted(
            int(value) for value in payload.get("integration_types") or [0]
        ),
        "default_member_permissions": permissions,
        "dm_permission": payload.get("dm_permission", True) is True,
        "nsfw": payload.get("nsfw", False) is True,
        "name_localizations": dict(payload.get("name_localizations") or {}),
        "description_localizations": dict(payload.get("description_localizations") or {}),
    }


class DiscordApi:
    def __init__(self, token: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._authorization = f"Bot {token}"
        self._sleep = sleep

    def request(self, method: str, path: str) -> Any:
        for _attempt in range(5):
            request = urllib.request.Request(
                API_ROOT + path,
                method=method,
                headers={
                    "Authorization": self._authorization,
                    "User-Agent": "EvelynCommandRegistryGuard/1",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise GuardFailure("guard_response_too_large")
                    return None if not raw else json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raw = exc.read(4096)
                if exc.code != 429:
                    raise GuardFailure("guard_discord_request_failed") from None
                try:
                    retry_after = float(json.loads(raw.decode("utf-8")).get("retry_after", 1.0))
                except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                    retry_after = 1.0
                self._sleep(min(5.0, max(0.1, retry_after)))
            except (OSError, TimeoutError, UnicodeError, json.JSONDecodeError):
                raise GuardFailure("guard_discord_request_failed") from None
        raise GuardFailure("guard_discord_rate_limited")

    def application_id(self) -> str:
        payload = self.request("GET", "/oauth2/applications/@me")
        application_id = str(payload.get("id") if isinstance(payload, dict) else "")
        if _ID.fullmatch(application_id) is None:
            raise GuardFailure("guard_application_invalid")
        return application_id

    def guild_commands(self, application_id: str, guild_id: str) -> list[dict[str, Any]]:
        payload = self.request(
            "GET",
            f"/applications/{application_id}/guilds/{guild_id}/commands",
        )
        if not isinstance(payload, list):
            raise GuardFailure("guard_registry_invalid")
        return payload

    def global_commands(self, application_id: str) -> list[dict[str, Any]]:
        payload = self.request("GET", f"/applications/{application_id}/commands")
        if not isinstance(payload, list):
            raise GuardFailure("guard_registry_invalid")
        return payload

    def delete_guild_command(
        self,
        application_id: str,
        guild_id: str,
        command_id: str,
    ) -> None:
        if _ID.fullmatch(command_id) is None:
            raise GuardFailure("guard_command_identity_invalid")
        self.request(
            "DELETE",
            f"/applications/{application_id}/guilds/{guild_id}/commands/{command_id}",
        )


class RegistryGuard:
    def __init__(
        self,
        api: Any,
        *,
        application_id: str,
        guild_id: str,
        ownership_path: Path,
        run_id: str,
    ) -> None:
        self.api = api
        self.application_id = application_id
        self.guild_id = guild_id
        self.ownership_path = ownership_path
        self.run_id = run_id
        self.baseline_guild: list[dict[str, Any]] = []
        self.baseline_global: list[dict[str, Any]] = []
        self.post_managed: list[dict[str, Any]] = []
        self.tracked_managed: dict[str, tuple[dict[str, Any], ...]] = {}
        self.current_owned: dict[str, tuple[dict[str, Any], ...]] = {}
        self.baseline_captured = False
        self.recovery_required = False

    def registries(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            self.api.guild_commands(self.application_id, self.guild_id),
            self.api.global_commands(self.application_id),
        )

    def _read_ownership(self) -> dict[str, tuple[dict[str, Any], ...]]:
        try:
            target_mode = os.lstat(self.ownership_path).st_mode
        except FileNotFoundError:
            self.current_owned = {}
            return {}
        if stat.S_ISLNK(target_mode) or not stat.S_ISREG(target_mode):
            raise GuardFailure("guard_ownership_invalid")
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.ownership_path, flags)
        except FileNotFoundError:
            self.current_owned = {}
            return {}
        except OSError as exc:
            raise GuardFailure("guard_ownership_unreadable") from exc
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise GuardFailure("guard_ownership_invalid")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                raw = handle.read(MAX_OWNERSHIP_BYTES + 1)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        if len(raw) > MAX_OWNERSHIP_BYTES:
            raise GuardFailure("guard_ownership_invalid")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GuardFailure("guard_ownership_invalid") from exc
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "runId",
            "applicationId",
            "guildId",
            "recoveryRequired",
            "commands",
        }:
            raise GuardFailure("guard_ownership_invalid")
        commands = payload["commands"]
        if (
            payload["schema"] != OWNERSHIP_SCHEMA
            or payload["runId"] != self.run_id
            or payload["applicationId"] != self.application_id
            or payload["guildId"] != self.guild_id
            or not isinstance(payload["recoveryRequired"], bool)
            or not isinstance(commands, list)
            or len(commands) > len(MANAGED_NAMES)
        ):
            raise GuardFailure("guard_ownership_invalid")
        candidate: dict[str, tuple[dict[str, Any], ...]] = {}
        final_names: set[str] = set()
        for entry in commands:
            if not isinstance(entry, dict) or set(entry) != {"id", "shapes"}:
                raise GuardFailure("guard_ownership_invalid")
            command_id = str(entry["id"])
            shapes = entry["shapes"]
            if (
                _ID.fullmatch(command_id) is None
                or command_id in candidate
                or not isinstance(shapes, list)
                or not 1 <= len(shapes) <= 2
            ):
                raise GuardFailure("guard_ownership_invalid")
            normalized = tuple(command_shape(shape) for shape in shapes)
            if any(shape != normalized[index] for index, shape in enumerate(shapes)):
                raise GuardFailure("guard_ownership_invalid")
            if len({canonical([shape]) for shape in normalized}) != len(normalized):
                raise GuardFailure("guard_ownership_invalid")
            managed = [
                shape
                for shape in normalized
                if str(shape.get("name") or "") in MANAGED_NAMES
            ]
            if len(managed) != 1:
                raise GuardFailure("guard_ownership_invalid")
            final_name = str(managed[0]["name"])
            if final_name in final_names:
                raise GuardFailure("guard_ownership_invalid")
            candidate[command_id] = normalized
            final_names.add(final_name)
            tracked = self.tracked_managed.get(command_id)
            if tracked is not None and any(shape not in tracked for shape in normalized):
                raise GuardFailure("guard_owned_shape_drift")
        updated = dict(self.tracked_managed)
        updated.update(candidate)
        self.tracked_managed = updated
        self.current_owned = candidate
        self.recovery_required = (
            self.recovery_required or payload["recoveryRequired"]
        )
        return candidate

    def capture_baseline(self) -> None:
        guild, global_commands = self.registries()
        self._read_ownership()
        _owned, unowned, foreign = self._partition_current(guild)
        if unowned:
            raise GuardFailure("guard_baseline_managed_commands_present")
        self.baseline_guild = foreign
        self.baseline_global = global_commands
        self.baseline_captured = True

    def _partition_current(
        self,
        commands: list[dict[str, Any]],
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        owned: list[dict[str, Any]] = []
        unowned_managed: list[dict[str, Any]] = []
        foreign: list[dict[str, Any]] = []
        ids: set[str] = set()
        for command in commands:
            command_id = str(command.get("id") or "")
            if _ID.fullmatch(command_id) is None or command_id in ids:
                raise GuardFailure("guard_command_identity_invalid")
            ids.add(command_id)
            expected = self.tracked_managed.get(command_id)
            if expected is None:
                if str(command.get("name") or "") in MANAGED_NAMES:
                    unowned_managed.append(command)
                else:
                    foreign.append(command)
            elif command_shape(command) not in expected:
                raise GuardFailure("guard_managed_shape_drift")
            else:
                owned.append(command)
        return owned, unowned_managed, foreign

    def capture_published(self) -> bool:
        current_owned = self._read_ownership()
        guild, global_commands = self.registries()
        owned, unowned, foreign = self._partition_current(guild)
        if (
            canonical(foreign) != canonical(self.baseline_guild)
            or canonical(global_commands) != canonical(self.baseline_global)
        ):
            raise GuardFailure("guard_registry_foreign_drift")
        if self.recovery_required:
            raise GuardFailure("guard_publisher_recovery_required")
        final_owned = [
            item
            for item in owned
            if str(item.get("name") or "") in MANAGED_NAMES
        ]
        names = {str(item.get("name") or "") for item in final_owned}
        if (
            unowned
            or len(final_owned) != len(MANAGED_NAMES)
            or len(owned) != len(final_owned)
            or names != MANAGED_NAMES
            or {str(item.get("id") or "") for item in owned} != set(current_owned)
        ):
            return False
        self.post_managed = list(owned)
        return True

    def restore(self) -> None:
        ledger_failure = ""
        try:
            self._read_ownership()
        except GuardFailure as exc:
            ledger_failure = str(exc)
        guild, global_commands = self.registries()
        try:
            owned, unowned, foreign = self._partition_current(guild)
        except GuardFailure:
            raise
        foreign_drift = (
            canonical(foreign) != canonical(self.baseline_guild)
            or canonical(global_commands) != canonical(self.baseline_global)
        )
        for command in sorted(owned, key=lambda item: str(item.get("id") or "")):
            command_id = str(command["id"])
            guild, global_commands = self.registries()
            fresh = next(
                (item for item in guild if str(item.get("id") or "") == command_id),
                None,
            )
            if fresh is None:
                continue
            if command_shape(fresh) not in self.tracked_managed[command_id]:
                raise GuardFailure("guard_managed_shape_drift")
            try:
                self.api.delete_guild_command(
                    self.application_id,
                    self.guild_id,
                    command_id,
                )
            except GuardFailure:
                guild, global_commands = self.registries()
                if any(str(item.get("id") or "") == command_id for item in guild):
                    raise GuardFailure("guard_cleanup_delete_ambiguous") from None
                continue
            guild, global_commands = self.registries()
            if any(str(item.get("id") or "") == command_id for item in guild):
                raise GuardFailure("guard_cleanup_delete_not_applied")
        final_guild, final_global = self.registries()
        if (
            ledger_failure
            or unowned
            or foreign_drift
            or canonical(final_guild) != canonical(self.baseline_guild)
            or canonical(final_global) != canonical(self.baseline_global)
        ):
            raise GuardFailure("guard_cleanup_verification_failed")


def write_status(
    config: GuardConfig,
    state: str,
    *,
    guard: RegistryGuard | None = None,
    failure: str = "",
) -> None:
    config.status_path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    payload = {
        "schema": STATUS_SCHEMA,
        "state": state,
        "baselineGuildDigest": _digest(guard.baseline_guild) if guard else "",
        "baselineGlobalDigest": _digest(guard.baseline_global) if guard else "",
        "managedCount": len(guard.tracked_managed) if guard else 0,
        "failure": failure,
        "contentFree": True,
        "updatedAt": time.time(),
    }
    temporary = config.status_path.with_name(f".{config.status_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, config.status_path)


def run_guard(
    token: str,
    config: GuardConfig,
    *,
    api_factory: Callable[[str], Any] = DiscordApi,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    api = api_factory(token)
    guard = RegistryGuard(
        api,
        application_id=api.application_id(),
        guild_id=config.guild_id,
        ownership_path=config.ownership_path,
        run_id=config.run_id,
    )
    publish_deadline = monotonic() + config.publish_timeout_sec
    lifetime_deadline = monotonic() + config.lifetime_sec
    validation_failure = ""
    pending_exception: BaseException | None = None
    try:
        guard.capture_baseline()
        write_status(config, "baseline_ready", guard=guard)
        while not guard.post_managed:
            if guard.capture_published():
                write_status(config, "published_ready", guard=guard)
                break
            if config.cleanup_path.exists():
                break
            if monotonic() >= publish_deadline:
                raise GuardFailure("guard_publish_timeout")
            sleep(0.5)
    except BaseException as exc:
        pending_exception = exc
        validation_failure = str(exc) if isinstance(exc, GuardFailure) else "guard_unexpected_failure"
        try:
            write_status(config, "failed", guard=guard, failure=validation_failure)
        except BaseException:
            pass
        if not guard.baseline_captured:
            raise

    try:
        while not config.cleanup_path.exists():
            if monotonic() >= lifetime_deadline:
                if not validation_failure:
                    validation_failure = "guard_cleanup_signal_timeout"
                    try:
                        write_status(config, "failed", guard=guard, failure=validation_failure)
                    except BaseException:
                        pass
                break
            try:
                guard.capture_published()
            except GuardFailure as exc:
                if not validation_failure:
                    validation_failure = str(exc)
            sleep(0.25)
    except BaseException as exc:
        if pending_exception is None:
            pending_exception = exc
            validation_failure = (
                str(exc) if isinstance(exc, GuardFailure) else "guard_unexpected_failure"
            )
    cleanup_failure: BaseException | None = None
    try:
        guard.restore()
    except BaseException as exc:
        cleanup_failure = exc
        try:
            write_status(
                config,
                "failed",
                guard=guard,
                failure=str(exc) if isinstance(exc, GuardFailure) else "guard_cleanup_unexpected",
            )
        except BaseException:
            pass
    if cleanup_failure is None:
        try:
            write_status(config, "restored", guard=guard, failure=validation_failure)
        except BaseException as exc:
            cleanup_failure = exc
    if cleanup_failure is not None:
        raise cleanup_failure
    if pending_exception is not None and not isinstance(pending_exception, GuardFailure):
        raise pending_exception


def main() -> int:
    config: GuardConfig | None = None
    guard: RegistryGuard | None = None
    try:
        if "DISCORD_BOT_TOKEN" in os.environ:
            raise GuardFailure("guard_token_transport_invalid")
        token, config = read_startup(sys.stdin.buffer)
        run_guard(token, config)
        return 0
    except GuardFailure as exc:
        if config is not None and not config.status_path.exists():
            try:
                write_status(config, "failed", guard=guard, failure=str(exc))
            except OSError:
                pass
        print(str(exc), file=sys.stderr)
        return 64
    except BaseException:
        if config is not None:
            try:
                write_status(config, "failed", guard=guard, failure="guard_unexpected_failure")
            except OSError:
                pass
        print("guard_unexpected_failure", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
