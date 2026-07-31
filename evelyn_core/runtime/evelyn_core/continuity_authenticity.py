from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


CONTINUITY_AUTH_KEY_FILE_ENV = (
    "EVELYN_CONTINUITY_AUTH_KEY_FILE"
)
CONTINUITY_AUTH_BOOTSTRAP_ENV = (
    "EVELYN_CONTINUITY_AUTH_BOOTSTRAP"
)
CONTINUITY_HEAD_SCHEMA_V1 = (
    "conversation_continuity.checkpoint-head.v1"
)
CONTINUITY_HEAD_SCHEMA_V2 = (
    "conversation_continuity.checkpoint-head.v2"
)
CONTINUITY_AUTH_ALGORITHM = "hmac-sha256"
CONTINUITY_AUTH_DOMAIN = (
    b"evelyn.conversation-continuity.checkpoint-head.v2\n"
)
CONTINUITY_AUTH_MIN_KEY_BYTES = 32
CONTINUITY_AUTH_MAX_KEY_BYTES = 4096
CONTINUITY_AUTH_MAX_FILE_BYTES = 8192
CONTINUITY_CHAIN_GENESIS = "0" * 64
CONTINUITY_AUTH_SCOPE_MAIN = "conversation_continuity"
CONTINUITY_AUTH_SCOPE_FAST_CONTROL = (
    "fast_control_continuity"
)
CONTINUITY_AUTH_SCOPES = frozenset(
    {
        CONTINUITY_AUTH_SCOPE_MAIN,
        CONTINUITY_AUTH_SCOPE_FAST_CONTROL,
    }
)


class ContinuityAuthenticityError(ValueError):
    """A stable, content-free continuity authentication failure."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _valid_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        return ""
    lowered = value.lower()
    if not all(character in "0123456789abcdef" for character in lowered):
        return ""
    return lowered


def _decode_key_file(raw: bytes) -> bytes:
    if raw.startswith(b"base64:"):
        encoded = raw[len(b"base64:") :].strip()
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ContinuityAuthenticityError(
                "continuity_auth_key_invalid"
            ) from None
    else:
        key = raw
    if not (
        CONTINUITY_AUTH_MIN_KEY_BYTES
        <= len(key)
        <= CONTINUITY_AUTH_MAX_KEY_BYTES
    ):
        raise ContinuityAuthenticityError(
            "continuity_auth_key_invalid"
        )
    return key


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class ContinuityAuthenticity:
    key: bytes | None = field(default=None, repr=False)
    allow_unsigned_bootstrap: bool = False

    def __post_init__(self) -> None:
        if self.key is None:
            if self.allow_unsigned_bootstrap:
                raise ContinuityAuthenticityError(
                    "continuity_auth_bootstrap_without_key"
                )
            return
        if not (
            CONTINUITY_AUTH_MIN_KEY_BYTES
            <= len(self.key)
            <= CONTINUITY_AUTH_MAX_KEY_BYTES
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_key_invalid"
            )

    @property
    def configured(self) -> bool:
        return self.key is not None

    @property
    def key_id(self) -> str:
        if self.key is None:
            return ""
        return hashlib.sha256(self.key).hexdigest()[:16]

    def _tag(self, unsigned_head: Mapping[str, Any]) -> str:
        if self.key is None:
            raise ContinuityAuthenticityError(
                "continuity_auth_key_required"
            )
        digest = hmac.new(
            self.key,
            digestmod=hashlib.sha256,
        )
        digest.update(CONTINUITY_AUTH_DOMAIN)
        digest.update(_canonical_json(unsigned_head))
        return digest.hexdigest()

    def sign_head(
        self,
        unsigned_head: Mapping[str, Any],
        *,
        auth_scope: str,
    ) -> dict[str, Any]:
        if self.key is None:
            return dict(unsigned_head)
        if auth_scope not in CONTINUITY_AUTH_SCOPES:
            raise ContinuityAuthenticityError(
                "continuity_auth_scope_invalid"
            )
        signed = {
            **dict(unsigned_head),
            "schema": CONTINUITY_HEAD_SCHEMA_V2,
            "authAlgorithm": CONTINUITY_AUTH_ALGORITHM,
            "authScope": auth_scope,
            "authKeyId": self.key_id,
        }
        signed["authTag"] = self._tag(signed)
        return signed

    def verify_signed_head(
        self,
        payload: Mapping[str, Any],
    ) -> None:
        if self.key is None:
            raise ContinuityAuthenticityError(
                "continuity_auth_key_required"
            )
        if (
            payload.get("authAlgorithm")
            != CONTINUITY_AUTH_ALGORITHM
            or payload.get("authKeyId") != self.key_id
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_failed"
            )
        supplied = str(payload.get("authTag") or "").lower()
        if (
            len(supplied) != 64
            or not all(
                character in "0123456789abcdef"
                for character in supplied
            )
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_failed"
            )
        unsigned = {
            key: value
            for key, value in payload.items()
            if key != "authTag"
        }
        if not hmac.compare_digest(
            supplied,
            self._tag(unsigned),
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_failed"
            )


def load_continuity_authenticity(
    *,
    protected_root: Path,
    additional_protected_roots: Iterable[Path] = (),
    environ: Mapping[str, str] | None = None,
) -> ContinuityAuthenticity:
    values = os.environ if environ is None else environ
    raw_path = str(
        values.get(CONTINUITY_AUTH_KEY_FILE_ENV) or ""
    ).strip()
    bootstrap = _enabled(
        values.get(CONTINUITY_AUTH_BOOTSTRAP_ENV)
    )
    if not raw_path:
        return ContinuityAuthenticity(
            allow_unsigned_bootstrap=bootstrap,
        )
    key_path = Path(raw_path)
    if not key_path.is_absolute():
        raise ContinuityAuthenticityError(
            "continuity_auth_key_path_invalid"
        )
    try:
        protected_roots = (
            Path(protected_root).resolve(),
            *(
                Path(root).resolve()
                for root in additional_protected_roots
            ),
        )
        if key_path.is_symlink():
            raise ContinuityAuthenticityError(
                "continuity_auth_key_file_rejected"
            )
        resolved = key_path.resolve(strict=True)
        if (
            any(
                _is_within(resolved, root)
                for root in protected_roots
            )
            or not resolved.is_file()
            or resolved.stat().st_size
            > CONTINUITY_AUTH_MAX_FILE_BYTES
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_key_file_rejected"
            )
        key = _decode_key_file(resolved.read_bytes())
    except ContinuityAuthenticityError:
        raise
    except OSError:
        raise ContinuityAuthenticityError(
            "continuity_auth_key_unavailable"
        ) from None
    return ContinuityAuthenticity(
        key=key,
        allow_unsigned_bootstrap=bootstrap,
    )


def build_continuity_head(
    *,
    state: str,
    generation: int,
    checkpoint_hash: str,
    updated_at: float,
    authenticity: ContinuityAuthenticity,
    auth_scope: str,
) -> dict[str, Any]:
    unsigned = {
        "schema": CONTINUITY_HEAD_SCHEMA_V1,
        "state": str(state),
        "generation": int(generation),
        "checkpointHash": str(checkpoint_hash),
        "updatedAt": float(updated_at),
        "contentFree": True,
    }
    return authenticity.sign_head(
        unsigned,
        auth_scope=auth_scope,
    )


def validate_continuity_head(
    payload: Mapping[str, Any],
    *,
    authenticity: ContinuityAuthenticity,
    auth_scope: str,
    permit_unsigned_bootstrap: bool = False,
) -> tuple[dict[str, Any], str]:
    schema = payload.get("schema")
    v1_keys = {
        "schema",
        "state",
        "generation",
        "checkpointHash",
        "updatedAt",
        "contentFree",
    }
    v2_keys = {
        *v1_keys,
        "authAlgorithm",
        "authScope",
        "authKeyId",
        "authTag",
    }
    expected_keys = (
        v2_keys
        if schema == CONTINUITY_HEAD_SCHEMA_V2
        else v1_keys
    )
    generation = payload.get("generation")
    state = str(payload.get("state") or "")
    checkpoint_hash = _valid_sha256(
        payload.get("checkpointHash")
    )
    try:
        updated_at = float(payload.get("updatedAt"))
    except (TypeError, ValueError):
        updated_at = -1.0
    if (
        set(payload) != expected_keys
        or schema
        not in {
            CONTINUITY_HEAD_SCHEMA_V1,
            CONTINUITY_HEAD_SCHEMA_V2,
        }
        or state not in {"active", "empty"}
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 0
        or not checkpoint_hash
        or not math.isfinite(updated_at)
        or updated_at < 0.0
        or payload.get("contentFree") is not True
        or (
            state == "empty"
            and checkpoint_hash != CONTINUITY_CHAIN_GENESIS
        )
    ):
        raise ValueError("checkpoint_head_rejected")
    if schema == CONTINUITY_HEAD_SCHEMA_V2:
        if payload.get("authScope") != auth_scope:
            raise ContinuityAuthenticityError(
                "continuity_auth_failed"
            )
        authenticity.verify_signed_head(payload)
        auth_state = "verified"
    elif authenticity.configured:
        if not (
            permit_unsigned_bootstrap
            and authenticity.allow_unsigned_bootstrap
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_bootstrap_required"
            )
        auth_state = "bootstrap_required"
    else:
        auth_state = "unconfigured"
    normalized = {
        **dict(payload),
        "state": state,
        "generation": generation,
        "checkpointHash": checkpoint_hash,
        "updatedAt": updated_at,
    }
    return normalized, auth_state


__all__ = [
    "CONTINUITY_AUTH_ALGORITHM",
    "CONTINUITY_AUTH_BOOTSTRAP_ENV",
    "CONTINUITY_AUTH_KEY_FILE_ENV",
    "CONTINUITY_AUTH_SCOPE_FAST_CONTROL",
    "CONTINUITY_AUTH_SCOPE_MAIN",
    "CONTINUITY_HEAD_SCHEMA_V1",
    "CONTINUITY_HEAD_SCHEMA_V2",
    "ContinuityAuthenticity",
    "ContinuityAuthenticityError",
    "build_continuity_head",
    "load_continuity_authenticity",
    "validate_continuity_head",
]
