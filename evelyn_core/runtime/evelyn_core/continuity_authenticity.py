from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .runtime_artifact_io import (
    artifact_target_allowed,
    atomic_json_write,
    read_bounded_text,
)


CONTINUITY_AUTH_KEY_FILE_ENV = (
    "EVELYN_CONTINUITY_AUTH_KEY_FILE"
)
CONTINUITY_AUTH_BOOTSTRAP_ENV = (
    "EVELYN_CONTINUITY_AUTH_BOOTSTRAP"
)
CONTINUITY_AUTH_ANCHOR_DIR_ENV = (
    "EVELYN_CONTINUITY_AUTH_ANCHOR_DIR"
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
CONTINUITY_AUTH_ANCHOR_MAX_FILE_BYTES = 128 * 1024
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
CONTINUITY_AUTH_ARTIFACT_GUILD_REVOCATIONS = (
    "conversation_continuity.guild_revocations"
)
CONTINUITY_AUTH_ARTIFACT_FAST_ACTION_HEAD = (
    "fast_control.action_recovery_head"
)
CONTINUITY_AUTH_ANCHOR_SCHEMA = (
    "conversation_continuity.external-anchor.v1"
)
CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD = (
    "conversation_continuity.checkpoint_head"
)
CONTINUITY_AUTH_ANCHOR_SLOT_FAST_CONTROL_HEAD = (
    "fast_control_continuity.checkpoint_head"
)
CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS = (
    "conversation_continuity.guild_revocations"
)
CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD = (
    "fast_control.action_recovery_head"
)
_CONTINUITY_AUTH_ANCHOR_FILES = {
    CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD: (
        "conversation-continuity-checkpoint.json"
    ),
    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_CONTROL_HEAD: (
        "fast-control-continuity-checkpoint.json"
    ),
    CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS: (
        "conversation-continuity-guild-revocations.json"
    ),
    CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD: (
        "fast-control-action-recovery.json"
    ),
}
_CONTINUITY_AUTH_ANCHOR_DOMAIN = (
    b"evelyn.conversation-continuity.external-anchor.v1\n"
)
_CONTINUITY_AUTH_ARTIFACT_DOMAINS = {
    CONTINUITY_AUTH_ARTIFACT_GUILD_REVOCATIONS: (
        b"evelyn.conversation-continuity.guild-revocations.v2\n"
    ),
    CONTINUITY_AUTH_ARTIFACT_FAST_ACTION_HEAD: (
        b"evelyn.fast-control.action-recovery-head.v2\n"
    ),
}


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
    anchor_root: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.key is None:
            if self.anchor_root is not None:
                raise ContinuityAuthenticityError(
                    "continuity_anchor_without_key"
                )
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
        if self.anchor_root is not None:
            try:
                root = Path(self.anchor_root)
                if (
                    not root.is_absolute()
                    or root.is_symlink()
                    or not root.is_dir()
                ):
                    raise ContinuityAuthenticityError(
                        "continuity_anchor_directory_rejected"
                    )
                resolved = root.resolve(strict=True)
            except ContinuityAuthenticityError:
                raise
            except OSError:
                raise ContinuityAuthenticityError(
                    "continuity_anchor_unavailable"
                ) from None
            object.__setattr__(self, "anchor_root", resolved)

    @property
    def configured(self) -> bool:
        return self.key is not None

    @property
    def key_id(self) -> str:
        if self.key is None:
            return ""
        return hashlib.sha256(self.key).hexdigest()[:16]

    @property
    def external_anchor_configured(self) -> bool:
        return self.anchor_root is not None

    @staticmethod
    def checkpoint_anchor_slot(auth_scope: str) -> str:
        if auth_scope == CONTINUITY_AUTH_SCOPE_MAIN:
            return CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD
        if auth_scope == CONTINUITY_AUTH_SCOPE_FAST_CONTROL:
            return CONTINUITY_AUTH_ANCHOR_SLOT_FAST_CONTROL_HEAD
        raise ContinuityAuthenticityError(
            "continuity_auth_scope_invalid"
        )

    def _tag_for_domain(
        self,
        payload: Mapping[str, Any],
        *,
        domain: bytes,
    ) -> str:
        if self.key is None:
            raise ContinuityAuthenticityError(
                "continuity_auth_key_required"
            )
        digest = hmac.new(
            self.key,
            digestmod=hashlib.sha256,
        )
        digest.update(domain)
        digest.update(_canonical_json(payload))
        return digest.hexdigest()

    def _tag(self, unsigned_head: Mapping[str, Any]) -> str:
        return self._tag_for_domain(
            unsigned_head,
            domain=CONTINUITY_AUTH_DOMAIN,
        )

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

    def sign_scoped_artifact(
        self,
        payload: Mapping[str, Any],
        *,
        artifact_scope: str,
    ) -> dict[str, Any]:
        if self.key is None:
            return dict(payload)
        domain = _CONTINUITY_AUTH_ARTIFACT_DOMAINS.get(
            artifact_scope
        )
        if domain is None:
            raise ContinuityAuthenticityError(
                "continuity_auth_scope_invalid"
            )
        signed = {
            **dict(payload),
            "authAlgorithm": CONTINUITY_AUTH_ALGORITHM,
            "authScope": artifact_scope,
            "authKeyId": self.key_id,
        }
        signed["authTag"] = self._tag_for_domain(
            signed,
            domain=domain,
        )
        return signed

    def verify_scoped_artifact(
        self,
        payload: Mapping[str, Any],
        *,
        artifact_scope: str,
    ) -> None:
        if self.key is None:
            raise ContinuityAuthenticityError(
                "continuity_auth_key_required"
            )
        domain = _CONTINUITY_AUTH_ARTIFACT_DOMAINS.get(
            artifact_scope
        )
        if domain is None:
            raise ContinuityAuthenticityError(
                "continuity_auth_scope_invalid"
            )
        if (
            payload.get("authAlgorithm")
            != CONTINUITY_AUTH_ALGORITHM
            or payload.get("authScope") != artifact_scope
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
            self._tag_for_domain(
                unsigned,
                domain=domain,
            ),
        ):
            raise ContinuityAuthenticityError(
                "continuity_auth_failed"
            )

    @staticmethod
    def _anchor_position(
        generation: Any,
        artifact_hash: Any,
    ) -> tuple[int, str]:
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 0
        ):
            raise ContinuityAuthenticityError(
                "continuity_anchor_position_invalid"
            )
        validated_hash = _valid_sha256(artifact_hash)
        if not validated_hash:
            raise ContinuityAuthenticityError(
                "continuity_anchor_position_invalid"
            )
        return generation, validated_hash

    def _anchor_path(self, slot: str) -> Path:
        filename = _CONTINUITY_AUTH_ANCHOR_FILES.get(slot)
        if filename is None:
            raise ContinuityAuthenticityError(
                "continuity_anchor_slot_invalid"
            )
        if self.anchor_root is None:
            raise ContinuityAuthenticityError(
                "continuity_anchor_unavailable"
            )
        return self.anchor_root / filename

    def _anchor_tag(
        self,
        payload: Mapping[str, Any],
    ) -> str:
        return self._tag_for_domain(
            payload,
            domain=_CONTINUITY_AUTH_ANCHOR_DOMAIN,
        )

    def _read_external_anchor(
        self,
        slot: str,
    ) -> dict[str, Any] | None:
        if not self.external_anchor_configured:
            return None
        path = self._anchor_path(slot)
        try:
            raw_text = read_bounded_text(
                path,
                maximum_bytes=CONTINUITY_AUTH_ANCHOR_MAX_FILE_BYTES,
                missing_ok=True,
            )
            if raw_text is None:
                return None
            payload = json.loads(raw_text)
            expected_keys = {
                "schema",
                "slot",
                "generation",
                "artifactHash",
                "updatedAt",
                "contentFree",
                "authAlgorithm",
                "authScope",
                "authKeyId",
                "authTag",
            }
            if (
                not isinstance(payload, dict)
                or set(payload) != expected_keys
                or payload.get("schema")
                != CONTINUITY_AUTH_ANCHOR_SCHEMA
                or payload.get("slot") != slot
                or payload.get("authScope") != slot
                or payload.get("authAlgorithm")
                != CONTINUITY_AUTH_ALGORITHM
                or payload.get("authKeyId") != self.key_id
                or payload.get("contentFree") is not True
            ):
                raise ContinuityAuthenticityError(
                    "continuity_anchor_record_rejected"
                )
            generation, artifact_hash = self._anchor_position(
                payload.get("generation"),
                payload.get("artifactHash"),
            )
            try:
                updated_at = float(payload.get("updatedAt"))
            except (TypeError, ValueError):
                updated_at = -1.0
            if not math.isfinite(updated_at) or updated_at < 0.0:
                raise ContinuityAuthenticityError(
                    "continuity_anchor_record_rejected"
                )
            supplied = str(payload.get("authTag") or "").lower()
            unsigned = {
                key: value
                for key, value in payload.items()
                if key != "authTag"
            }
            if (
                len(supplied) != 64
                or not all(
                    character in "0123456789abcdef"
                    for character in supplied
                )
                or not hmac.compare_digest(
                    supplied,
                    self._anchor_tag(unsigned),
                )
            ):
                raise ContinuityAuthenticityError(
                    "continuity_anchor_auth_failed"
                )
            return {
                "generation": generation,
                "artifactHash": artifact_hash,
                "updatedAt": updated_at,
            }
        except ContinuityAuthenticityError:
            raise
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            raise ContinuityAuthenticityError(
                "continuity_anchor_record_rejected"
            ) from None
        except OSError:
            raise ContinuityAuthenticityError(
                "continuity_anchor_unavailable"
            ) from None

    def _write_external_anchor(
        self,
        slot: str,
        *,
        generation: int,
        artifact_hash: str,
        updated_at: float | None = None,
    ) -> None:
        generation, artifact_hash = self._anchor_position(
            generation,
            artifact_hash,
        )
        path = self._anchor_path(slot)
        timestamp = (
            time.time() if updated_at is None else float(updated_at)
        )
        if not math.isfinite(timestamp) or timestamp < 0.0:
            raise ContinuityAuthenticityError(
                "continuity_anchor_position_invalid"
            )
        payload = {
            "schema": CONTINUITY_AUTH_ANCHOR_SCHEMA,
            "slot": slot,
            "generation": generation,
            "artifactHash": artifact_hash,
            "updatedAt": timestamp,
            "contentFree": True,
            "authAlgorithm": CONTINUITY_AUTH_ALGORITHM,
            "authScope": slot,
            "authKeyId": self.key_id,
        }
        payload["authTag"] = self._anchor_tag(payload)
        try:
            if not artifact_target_allowed(path):
                raise ContinuityAuthenticityError(
                    "continuity_anchor_record_rejected"
                )
            atomic_json_write(path, payload, durable=True)
        except ContinuityAuthenticityError:
            raise
        except OSError:
            raise ContinuityAuthenticityError(
                "continuity_anchor_unavailable"
            ) from None

    def external_anchor_position(
        self,
        slot: str,
    ) -> tuple[int, str] | None:
        record = self._read_external_anchor(slot)
        if record is None:
            return None
        return (
            int(record["generation"]),
            str(record["artifactHash"]),
        )

    def verify_external_anchor(
        self,
        slot: str,
        *,
        generation: int,
        artifact_hash: str,
    ) -> str:
        if not self.external_anchor_configured:
            return "unconfigured"
        generation, artifact_hash = self._anchor_position(
            generation,
            artifact_hash,
        )
        record = self._read_external_anchor(slot)
        if record is None:
            raise ContinuityAuthenticityError(
                "continuity_anchor_bootstrap_required"
            )
        if (
            int(record["generation"]) != generation
            or str(record["artifactHash"]) != artifact_hash
        ):
            raise ContinuityAuthenticityError(
                "continuity_anchor_replay_detected"
            )
        return "verified"

    def reconcile_external_anchor(
        self,
        slot: str,
        *,
        generation: int,
        artifact_hash: str,
        previous_hash: str = "",
        allow_unlinked_one_step: bool = False,
        updated_at: float | None = None,
    ) -> str:
        if not self.external_anchor_configured:
            return "unconfigured"
        generation, artifact_hash = self._anchor_position(
            generation,
            artifact_hash,
        )
        previous = _valid_sha256(previous_hash)
        record = self._read_external_anchor(slot)
        if record is None:
            if not self.allow_unsigned_bootstrap:
                raise ContinuityAuthenticityError(
                    "continuity_anchor_bootstrap_required"
                )
            self._write_external_anchor(
                slot,
                generation=generation,
                artifact_hash=artifact_hash,
                updated_at=updated_at,
            )
            return "bootstrapped"
        anchored_generation = int(record["generation"])
        anchored_hash = str(record["artifactHash"])
        if (
            anchored_generation == generation
            and anchored_hash == artifact_hash
        ):
            return "verified"
        if (
            generation == anchored_generation + 1
            and (
                previous == anchored_hash
                or allow_unlinked_one_step
            )
        ):
            self._write_external_anchor(
                slot,
                generation=generation,
                artifact_hash=artifact_hash,
                updated_at=updated_at,
            )
            return "recovered"
        raise ContinuityAuthenticityError(
            "continuity_anchor_replay_detected"
        )

    def commit_external_anchor(
        self,
        slot: str,
        *,
        previous_generation: int,
        previous_hash: str,
        generation: int,
        artifact_hash: str,
        updated_at: float | None = None,
    ) -> str:
        if not self.external_anchor_configured:
            return "unconfigured"
        previous_generation, previous_hash = self._anchor_position(
            previous_generation,
            previous_hash,
        )
        generation, artifact_hash = self._anchor_position(
            generation,
            artifact_hash,
        )
        if generation != previous_generation + 1:
            raise ContinuityAuthenticityError(
                "continuity_anchor_position_invalid"
            )
        record = self._read_external_anchor(slot)
        if record is None:
            if not self.allow_unsigned_bootstrap:
                raise ContinuityAuthenticityError(
                    "continuity_anchor_bootstrap_required"
                )
        elif (
            int(record["generation"]) == generation
            and str(record["artifactHash"]) == artifact_hash
        ):
            return "verified"
        elif (
            int(record["generation"]) != previous_generation
            or str(record["artifactHash"]) != previous_hash
        ):
            raise ContinuityAuthenticityError(
                "continuity_anchor_replay_detected"
            )
        self._write_external_anchor(
            slot,
            generation=generation,
            artifact_hash=artifact_hash,
            updated_at=updated_at,
        )
        return "advanced"


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
    raw_anchor_root = str(
        values.get(CONTINUITY_AUTH_ANCHOR_DIR_ENV) or ""
    ).strip()
    bootstrap = _enabled(
        values.get(CONTINUITY_AUTH_BOOTSTRAP_ENV)
    )
    if not raw_path:
        if raw_anchor_root:
            raise ContinuityAuthenticityError(
                "continuity_anchor_without_key"
            )
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
        anchor_root: Path | None = None
        if raw_anchor_root:
            candidate = Path(raw_anchor_root)
            if not candidate.is_absolute() or candidate.is_symlink():
                raise ContinuityAuthenticityError(
                    "continuity_anchor_directory_rejected"
                )
            try:
                anchor_root = candidate.resolve(strict=True)
            except OSError:
                raise ContinuityAuthenticityError(
                    "continuity_anchor_unavailable"
                ) from None
            if (
                not anchor_root.is_dir()
                or any(
                    _is_within(anchor_root, root)
                    for root in protected_roots
                )
            ):
                raise ContinuityAuthenticityError(
                    "continuity_anchor_directory_rejected"
                )
    except ContinuityAuthenticityError:
        raise
    except OSError:
        raise ContinuityAuthenticityError(
            "continuity_auth_key_unavailable"
        ) from None
    return ContinuityAuthenticity(
        key=key,
        allow_unsigned_bootstrap=bootstrap,
        anchor_root=anchor_root,
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
    "CONTINUITY_AUTH_ANCHOR_DIR_ENV",
    "CONTINUITY_AUTH_ANCHOR_SCHEMA",
    "CONTINUITY_AUTH_ANCHOR_SLOT_FAST_ACTION_HEAD",
    "CONTINUITY_AUTH_ANCHOR_SLOT_FAST_CONTROL_HEAD",
    "CONTINUITY_AUTH_ANCHOR_SLOT_GUILD_REVOCATIONS",
    "CONTINUITY_AUTH_ANCHOR_SLOT_MAIN_HEAD",
    "CONTINUITY_AUTH_ALGORITHM",
    "CONTINUITY_AUTH_ARTIFACT_FAST_ACTION_HEAD",
    "CONTINUITY_AUTH_ARTIFACT_GUILD_REVOCATIONS",
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
