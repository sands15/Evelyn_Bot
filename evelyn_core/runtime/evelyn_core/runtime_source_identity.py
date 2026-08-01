from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any


RUNTIME_SOURCE_IDENTITY_SCHEMA = "runtime_source_identity.v1"
RUNTIME_ROLE_ENV = "EVELYN_RUNTIME_ROLE"
IMAGE_SOURCE_REVISION_ENV = "EVELYN_IMAGE_SOURCE_REVISION"
EXPECTED_SOURCE_REVISION_ENV = "EVELYN_EXPECTED_SOURCE_REVISION"

CONTAINER_RUNTIME_ROLES = frozenset(
    {
        "bot_api",
        "control_page",
        "discord_bot",
    }
)

_REVISION_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def _environment_value(
    environ: Mapping[str, str],
    name: str,
) -> str:
    value = environ.get(name)
    return value if isinstance(value, str) else ""


def _valid_revision(value: str) -> bool:
    return bool(_REVISION_PATTERN.fullmatch(value))


def _identity_payload(
    *,
    role: str,
    mode: str,
    state: str,
    ready: bool,
    aligned: bool,
    verified: bool,
    image_revision: str | None,
    expected_revision: str | None,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "schema": RUNTIME_SOURCE_IDENTITY_SCHEMA,
        "role": role,
        "mode": mode,
        "state": state,
        "ready": ready,
        "aligned": aligned,
        "verified": verified,
        "imageSourceRevision": image_revision,
        "expectedSourceRevision": expected_revision,
        "reasonCode": reason_code,
    }


def runtime_source_identity(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the public, fail-closed source identity for this runtime.

    A host development process is the only mode allowed to omit the role and
    both revisions. Container roles must publish two valid, exactly equal Git
    revisions. Invalid environment values are never copied into the result.
    """

    values = os.environ if environ is None else environ
    raw_role = _environment_value(values, RUNTIME_ROLE_ENV)
    raw_image_revision = _environment_value(
        values,
        IMAGE_SOURCE_REVISION_ENV,
    )
    raw_expected_revision = _environment_value(
        values,
        EXPECTED_SOURCE_REVISION_ENV,
    )

    if not raw_role and not raw_image_revision and not raw_expected_revision:
        return _identity_payload(
            role="development",
            mode="development",
            state="development",
            ready=True,
            aligned=True,
            verified=False,
            image_revision=None,
            expected_revision=None,
            reason_code="development_source_identity",
        )

    image_revision = (
        raw_image_revision
        if _valid_revision(raw_image_revision)
        else None
    )
    expected_revision = (
        raw_expected_revision
        if _valid_revision(raw_expected_revision)
        else None
    )
    public_role = (
        raw_role
        if raw_role in CONTAINER_RUNTIME_ROLES
        else "unknown"
    )

    if not raw_role:
        reason_code = "runtime_role_missing"
    elif raw_role not in CONTAINER_RUNTIME_ROLES:
        reason_code = "runtime_role_invalid"
    elif not raw_image_revision or not raw_expected_revision:
        reason_code = "source_revision_missing"
    elif image_revision is None or expected_revision is None:
        reason_code = "source_revision_invalid"
    else:
        reason_code = ""

    if reason_code:
        return _identity_payload(
            role=public_role,
            mode="container",
            state="unverified",
            ready=False,
            aligned=False,
            verified=False,
            image_revision=None,
            expected_revision=None,
            reason_code=reason_code,
        )

    if image_revision != expected_revision:
        return _identity_payload(
            role=public_role,
            mode="container",
            state="mismatch",
            ready=False,
            aligned=False,
            verified=True,
            image_revision=image_revision,
            expected_revision=expected_revision,
            reason_code="source_revision_mismatch",
        )

    return _identity_payload(
        role=public_role,
        mode="container",
        state="aligned",
        ready=True,
        aligned=True,
        verified=True,
        image_revision=image_revision,
        expected_revision=expected_revision,
        reason_code="source_revision_aligned",
    )


def _compatible_identity_key(
    payload: Mapping[str, Any],
) -> tuple[str, str] | None:
    if payload.get("schema") != RUNTIME_SOURCE_IDENTITY_SCHEMA:
        return None
    if (
        payload.get("ready") is not True
        or payload.get("aligned") is not True
    ):
        return None

    mode = payload.get("mode")
    if mode == "development":
        if (
            payload.get("state") == "development"
            and payload.get("role") == "development"
            and payload.get("verified") is False
            and payload.get("imageSourceRevision") is None
            and payload.get("expectedSourceRevision") is None
            and payload.get("reasonCode")
            == "development_source_identity"
        ):
            return ("development", "")
        return None

    image_revision = payload.get("imageSourceRevision")
    expected_revision = payload.get("expectedSourceRevision")
    if (
        mode != "container"
        or payload.get("state") != "aligned"
        or payload.get("role") not in CONTAINER_RUNTIME_ROLES
        or payload.get("verified") is not True
        or payload.get("reasonCode") != "source_revision_aligned"
        or not isinstance(image_revision, str)
        or not isinstance(expected_revision, str)
        or not _valid_revision(image_revision)
        or not _valid_revision(expected_revision)
        or image_revision != expected_revision
    ):
        return None
    return ("container", image_revision)


def source_identities_compatible(
    local: Mapping[str, Any],
    remote: Mapping[str, Any],
) -> bool:
    """Return true only for two exact, independently valid identities."""

    if not isinstance(local, Mapping) or not isinstance(remote, Mapping):
        return False
    local_key = _compatible_identity_key(local)
    remote_key = _compatible_identity_key(remote)
    return local_key is not None and local_key == remote_key


__all__ = [
    "CONTAINER_RUNTIME_ROLES",
    "EXPECTED_SOURCE_REVISION_ENV",
    "IMAGE_SOURCE_REVISION_ENV",
    "RUNTIME_ROLE_ENV",
    "RUNTIME_SOURCE_IDENTITY_SCHEMA",
    "runtime_source_identity",
    "source_identities_compatible",
]
