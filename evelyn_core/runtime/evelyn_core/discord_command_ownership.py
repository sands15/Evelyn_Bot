from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Callable


_SCHEMA = "evelyn.discord-command-ownership.v2"
_MAX_BYTES = 256 * 1024


def validate_command_ownership_config(config: tuple[str, str]) -> tuple[Path, str] | None:
    path_text, run_id = config
    if not path_text and not run_id:
        return None
    if (
        not path_text
        or not run_id
        or not Path(path_text).is_absolute()
        or len(run_id) != 32
        or any(character not in "0123456789abcdef" for character in run_id)
    ):
        raise RuntimeError("archive_command_ownership_configuration_invalid")
    return Path(path_text), run_id


def write_command_ownership_ledger(
    config: tuple[str, str],
    *,
    application_id: int,
    guild_id: int,
    commands: dict[int, tuple[dict[str, Any], ...]],
    recovery_required: bool,
) -> None:
    validated = validate_command_ownership_config(config)
    if validated is None:
        return
    path, run_id = validated
    parent = path.parent
    parent_mode = os.lstat(parent).st_mode
    if stat.S_ISLNK(parent_mode) or not stat.S_ISDIR(parent_mode):
        raise RuntimeError("archive_command_ownership_parent_invalid")
    try:
        target_mode = os.lstat(path).st_mode
    except FileNotFoundError:
        target_mode = 0
    if target_mode and (stat.S_ISLNK(target_mode) or not stat.S_ISREG(target_mode)):
        raise RuntimeError("archive_command_ownership_target_invalid")
    payload = {
        "schema": _SCHEMA,
        "runId": run_id,
        "applicationId": str(application_id),
        "guildId": str(guild_id),
        "recoveryRequired": recovery_required is True,
        "commands": [
            {"id": str(command_id), "shapes": list(shapes)}
            for command_id, shapes in sorted(commands.items())
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    temporary = parent / f".{path.name}.{run_id}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
            if hasattr(os, "O_DIRECTORY"):
                directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_command_ownership_ledger(
    config: tuple[str, str],
    *,
    application_id: int,
    guild_id: int,
    normalize_shape: Callable[[Any], dict[str, Any]],
) -> tuple[dict[int, tuple[dict[str, Any], ...]], bool] | None:
    validated = validate_command_ownership_config(config)
    if validated is None:
        return None
    path, run_id = validated
    try:
        target_mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return {}, False
    if stat.S_ISLNK(target_mode) or not stat.S_ISREG(target_mode):
        raise RuntimeError("archive_command_ownership_target_invalid")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return {}, False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("archive_command_ownership_target_invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            raw = handle.read(_MAX_BYTES + 1)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    if len(raw) > _MAX_BYTES:
        raise RuntimeError("archive_command_ownership_ledger_invalid")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("archive_command_ownership_ledger_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "runId",
        "applicationId",
        "guildId",
        "recoveryRequired",
        "commands",
    }:
        raise RuntimeError("archive_command_ownership_ledger_invalid")
    entries = payload["commands"]
    if (
        payload["schema"] != _SCHEMA
        or payload["runId"] != run_id
        or payload["applicationId"] != str(application_id)
        or payload["guildId"] != str(guild_id)
        or not isinstance(payload["recoveryRequired"], bool)
        or not isinstance(entries, list)
        or len(entries) > 5
    ):
        raise RuntimeError("archive_command_ownership_ledger_invalid")
    commands: dict[int, tuple[dict[str, Any], ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "shapes"}:
            raise RuntimeError("archive_command_ownership_ledger_invalid")
        command_id_text = entry["id"]
        shapes = entry["shapes"]
        try:
            command_id = int(command_id_text)
        except (TypeError, ValueError):
            raise RuntimeError("archive_command_ownership_ledger_invalid") from None
        if (
            isinstance(command_id_text, bool)
            or str(command_id) != command_id_text
            or command_id <= 0
            or command_id in commands
            or not isinstance(shapes, list)
            or not 1 <= len(shapes) <= 2
        ):
            raise RuntimeError("archive_command_ownership_ledger_invalid")
        normalized = tuple(normalize_shape(shape) for shape in shapes)
        if any(shape != normalized[index] for index, shape in enumerate(shapes)):
            raise RuntimeError("archive_command_ownership_ledger_invalid")
        encoded_shapes = {
            json.dumps(shape, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for shape in normalized
        }
        if len(encoded_shapes) != len(normalized):
            raise RuntimeError("archive_command_ownership_ledger_invalid")
        commands[command_id] = normalized
    return commands, payload["recoveryRequired"]
