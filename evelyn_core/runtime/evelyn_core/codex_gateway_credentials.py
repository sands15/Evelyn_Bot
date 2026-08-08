from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping


MAX_CREDENTIAL_FILE_BYTES = 1024 * 1024
EPHEMERAL_HOME_MARKER = ".evelyn-ephemeral-codex-home"


def _safe_file(source: Path, *, required: bool) -> bool:
    if not source.exists():
        if required:
            raise RuntimeError("codex_credentials_missing")
        return False
    if source.is_symlink() or not source.is_file():
        raise RuntimeError("codex_credentials_invalid_file")
    if source.stat().st_size > MAX_CREDENTIAL_FILE_BYTES:
        raise RuntimeError("codex_credentials_file_too_large")
    return True


def _copy_secret(source: Path, target: Path) -> None:
    temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def stage_codex_credentials(
    source_dir: Path,
    target_dir: Path,
) -> dict[str, Any]:
    source = Path(source_dir).expanduser().resolve()
    target = Path(target_dir).expanduser().resolve()
    if source == target:
        raise RuntimeError("codex_credentials_source_matches_target")
    auth_source = source / "auth.json"
    _safe_file(auth_source, required=True)
    try:
        auth_payload = json.loads(auth_source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("codex_credentials_auth_invalid") from exc
    if not isinstance(auth_payload, dict):
        raise RuntimeError("codex_credentials_auth_invalid")

    marker = target / EPHEMERAL_HOME_MARKER
    if target.exists() and not marker.is_file():
        try:
            has_existing_files = any(target.iterdir())
        except OSError as exc:
            raise RuntimeError("codex_credentials_target_unavailable") from exc
        if has_existing_files:
            raise RuntimeError("codex_credentials_target_not_ephemeral")
    target.mkdir(parents=True, exist_ok=True)
    try:
        target.chmod(0o700)
    except OSError:
        pass
    marker.write_text("ephemeral\n", encoding="utf-8")
    _copy_secret(auth_source, target / "auth.json")
    stale_config = target / "config.toml"
    if stale_config.exists() and stale_config.is_file():
        stale_config.unlink()
    return {
        "ready": True,
        "mode": "ephemeral-copy",
        "authPresent": True,
        "configPresent": False,
    }


def prepare_codex_credentials(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source = os.environ if environ is None else environ
    source_text = str(source.get("EVELYN_CODEX_CREDENTIALS_DIR") or "").strip()
    home_text = str(source.get("CODEX_HOME") or "").strip()
    if source_text:
        if not home_text:
            return {
                "ready": False,
                "mode": "ephemeral-copy",
                "errorCode": "codex_home_missing",
                "authPresent": False,
                "configPresent": False,
            }
        try:
            return stage_codex_credentials(Path(source_text), Path(home_text))
        except RuntimeError as exc:
            return {
                "ready": False,
                "mode": "ephemeral-copy",
                "errorCode": str(exc),
                "authPresent": False,
                "configPresent": False,
            }

    return {
        "ready": False,
        "mode": "unconfigured",
        "errorCode": "codex_credentials_unconfigured",
        "authPresent": False,
        "configPresent": False,
    }


__all__ = [
    "MAX_CREDENTIAL_FILE_BYTES",
    "prepare_codex_credentials",
    "stage_codex_credentials",
]
