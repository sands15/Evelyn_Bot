from __future__ import annotations

import os
import secrets
from pathlib import Path

from .paths import get_runtime_artifacts_root


TOKEN_ENV = "VOYAGER_CODEX_GATEWAY_TOKEN"
TOKEN_FILE_ENV = "VOYAGER_CODEX_GATEWAY_TOKEN_FILE"
AUTHORIZATION_HEADER = "Authorization"
DEFAULT_TOKEN_FILE = get_runtime_artifacts_root() / "secrets" / "codex_gateway.token"


def gateway_token_path() -> Path:
    configured = str(os.getenv(TOKEN_FILE_ENV) or "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_TOKEN_FILE


def resolve_gateway_token(*, create: bool = False, token_path: Path | None = None) -> str:
    configured = str(os.getenv(TOKEN_ENV) or "").strip()
    if configured:
        return configured

    path = token_path or gateway_token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return existing
    if not create:
        raise RuntimeError(f"Codex gateway token is not available at {path}")

    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8").strip()
        if not existing:
            raise RuntimeError(f"Codex gateway token file is empty: {path}")
        return existing

    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(generated + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return generated


def gateway_auth_headers(*, create: bool = False, token_path: Path | None = None) -> dict[str, str]:
    token = resolve_gateway_token(create=create, token_path=token_path)
    return {AUTHORIZATION_HEADER: f"Bearer {token}"}


def gateway_request_authorized(authorization: str | None, expected_token: str) -> bool:
    scheme, separator, supplied = str(authorization or "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not supplied:
        return False
    return secrets.compare_digest(supplied.strip(), expected_token)
