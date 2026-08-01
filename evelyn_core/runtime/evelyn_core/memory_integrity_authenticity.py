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

from .runtime_artifact_io import atomic_json_write


MEMORY_INTEGRITY_KEY_FILE_ENV = "EVELYN_MEMORY_INTEGRITY_KEY_FILE"
MEMORY_INTEGRITY_ANCHOR_DIR_ENV = "EVELYN_MEMORY_INTEGRITY_ANCHOR_DIR"
MEMORY_INTEGRITY_BOOTSTRAP_ENV = "EVELYN_MEMORY_INTEGRITY_BOOTSTRAP"
MEMORY_INTEGRITY_HEAD_SCHEMA = (
    "memory.provenance.correction-chain-head.v2"
)
MEMORY_INTEGRITY_ANCHOR_SCHEMA = (
    "memory.provenance.correction-external-anchor.v1"
)
MEMORY_INTEGRITY_ALGORITHM = "hmac-sha256"
MEMORY_INTEGRITY_SCOPE = "memory.provenance.correction-journal"
MEMORY_INTEGRITY_MIN_KEY_BYTES = 32
MEMORY_INTEGRITY_MAX_KEY_BYTES = 4096
MEMORY_INTEGRITY_MAX_FILE_BYTES = 8192
MEMORY_INTEGRITY_ANCHOR_MAX_FILE_BYTES = 128 * 1024
MEMORY_INTEGRITY_ANCHOR_NAME = "memory-provenance-corrections.json"
MEMORY_INTEGRITY_GENESIS = "0" * 64

_HEAD_DOMAIN = b"evelyn.memory.provenance.correction-chain-head.v2\n"
_ANCHOR_DOMAIN = b"evelyn.memory.provenance.correction-external-anchor.v1\n"


class MemoryIntegrityAuthenticityError(ValueError):
    """A stable, content-free memory integrity failure."""

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


def _canonical_artifact_json(payload: Mapping[str, Any]) -> str:
    """Return the exact serialization emitted by ``atomic_json_write``."""

    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _strict_json_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate_json_key")
        payload[key] = value
    return payload


def _strict_json_loads(encoded: str) -> Any:
    return json.loads(
        encoded,
        object_pairs_hook=_strict_json_object,
    )


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _valid_hash(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        return ""
    lowered = value.lower()
    if value != lowered or not all(
        character in "0123456789abcdef" for character in lowered
    ):
        return ""
    return lowered


def _position(sequence: Any, event_hash: Any) -> tuple[int, str]:
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
    ):
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_anchor_position_invalid"
        )
    validated_hash = _valid_hash(event_hash)
    if not validated_hash:
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_anchor_position_invalid"
        )
    return sequence, validated_hash


def _decode_key_file(raw: bytes) -> bytes:
    if raw.startswith(b"base64:"):
        try:
            key = base64.b64decode(
                raw[len(b"base64:") :].strip(),
                validate=True,
            )
        except (ValueError, binascii.Error):
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_key_invalid"
            ) from None
    else:
        key = raw
    if not (
        MEMORY_INTEGRITY_MIN_KEY_BYTES
        <= len(key)
        <= MEMORY_INTEGRITY_MAX_KEY_BYTES
    ):
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_auth_key_invalid"
        )
    return key


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class MemoryIntegrityAuthenticity:
    key: bytes | None = field(default=None, repr=False)
    allow_unsigned_bootstrap: bool = False
    anchor_root: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.key is None:
            if self.anchor_root is not None:
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_without_key"
                )
            if self.allow_unsigned_bootstrap:
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_auth_bootstrap_without_key"
                )
            return
        if not (
            MEMORY_INTEGRITY_MIN_KEY_BYTES
            <= len(self.key)
            <= MEMORY_INTEGRITY_MAX_KEY_BYTES
        ):
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_key_invalid"
            )
        if self.anchor_root is not None:
            try:
                candidate = Path(self.anchor_root)
                if (
                    not candidate.is_absolute()
                    or candidate.is_symlink()
                    or not candidate.is_dir()
                ):
                    raise MemoryIntegrityAuthenticityError(
                        "memory_provenance_correction_anchor_directory_rejected"
                    )
                resolved = candidate.resolve(strict=True)
            except MemoryIntegrityAuthenticityError:
                raise
            except OSError:
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_unavailable"
                ) from None
            object.__setattr__(self, "anchor_root", resolved)

    @property
    def configured(self) -> bool:
        return self.key is not None

    @property
    def external_anchor_configured(self) -> bool:
        return self.anchor_root is not None

    @property
    def key_id(self) -> str:
        if self.key is None:
            return ""
        return hashlib.sha256(self.key).hexdigest()[:16]

    def _tag(self, payload: Mapping[str, Any], *, domain: bytes) -> str:
        if self.key is None:
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_key_required"
            )
        digest = hmac.new(self.key, digestmod=hashlib.sha256)
        digest.update(domain)
        digest.update(_canonical_json(payload))
        return digest.hexdigest()

    def sign_head(self, unsigned: Mapping[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return dict(unsigned)
        signed = {
            **dict(unsigned),
            "schema": MEMORY_INTEGRITY_HEAD_SCHEMA,
            "authAlgorithm": MEMORY_INTEGRITY_ALGORITHM,
            "authScope": MEMORY_INTEGRITY_SCOPE,
            "authKeyId": self.key_id,
        }
        signed["authTag"] = self._tag(signed, domain=_HEAD_DOMAIN)
        return signed

    def verify_head(self, payload: Mapping[str, Any]) -> None:
        if not self.configured:
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_key_required"
            )
        if (
            payload.get("schema") != MEMORY_INTEGRITY_HEAD_SCHEMA
            or payload.get("authAlgorithm") != MEMORY_INTEGRITY_ALGORITHM
            or payload.get("authScope") != MEMORY_INTEGRITY_SCOPE
            or payload.get("authKeyId") != self.key_id
        ):
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_failed"
            )
        supplied = _valid_hash(payload.get("authTag"))
        unsigned = {
            key: value for key, value in payload.items() if key != "authTag"
        }
        if not supplied or not hmac.compare_digest(
            supplied,
            self._tag(unsigned, domain=_HEAD_DOMAIN),
        ):
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_failed"
            )

    def _anchor_path(self) -> Path:
        if self.anchor_root is None:
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_anchor_unavailable"
            )
        return self.anchor_root / MEMORY_INTEGRITY_ANCHOR_NAME

    def _read_anchor(self) -> dict[str, Any] | None:
        if not self.external_anchor_configured:
            return None
        path = self._anchor_path()
        try:
            if not path.exists() and not path.is_symlink():
                return None
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > MEMORY_INTEGRITY_ANCHOR_MAX_FILE_BYTES
            ):
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_record_rejected"
                )
            raw = path.read_text(encoding="utf-8")
            payload = _strict_json_loads(raw)
            if (
                not isinstance(payload, dict)
                or raw != _canonical_artifact_json(payload)
            ):
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_record_rejected"
                )
            expected = {
                "schema",
                "sequence",
                "eventHash",
                "updatedAt",
                "contentFree",
                "authAlgorithm",
                "authScope",
                "authKeyId",
                "authTag",
            }
            if (
                set(payload) != expected
                or payload.get("schema") != MEMORY_INTEGRITY_ANCHOR_SCHEMA
                or payload.get("contentFree") is not True
                or payload.get("authAlgorithm") != MEMORY_INTEGRITY_ALGORITHM
                or payload.get("authScope") != MEMORY_INTEGRITY_SCOPE
                or payload.get("authKeyId") != self.key_id
            ):
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_record_rejected"
                )
            sequence, event_hash = _position(
                payload.get("sequence"), payload.get("eventHash")
            )
            updated_at = float(payload.get("updatedAt"))
            if not math.isfinite(updated_at) or updated_at < 0.0:
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_record_rejected"
                )
            supplied = _valid_hash(payload.get("authTag"))
            unsigned = {
                key: value
                for key, value in payload.items()
                if key != "authTag"
            }
            if not supplied or not hmac.compare_digest(
                supplied,
                self._tag(unsigned, domain=_ANCHOR_DOMAIN),
            ):
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_auth_failed"
                )
            return {
                "sequence": sequence,
                "eventHash": event_hash,
                "updatedAt": updated_at,
            }
        except MemoryIntegrityAuthenticityError:
            raise
        except (
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            OverflowError,
            RecursionError,
        ):
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_anchor_record_rejected"
            ) from None
        except OSError:
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_anchor_unavailable"
            ) from None

    def _write_anchor(self, sequence: int, event_hash: str) -> None:
        sequence, event_hash = _position(sequence, event_hash)
        path = self._anchor_path()
        payload = {
            "schema": MEMORY_INTEGRITY_ANCHOR_SCHEMA,
            "sequence": sequence,
            "eventHash": event_hash,
            "updatedAt": time.time(),
            "contentFree": True,
            "authAlgorithm": MEMORY_INTEGRITY_ALGORITHM,
            "authScope": MEMORY_INTEGRITY_SCOPE,
            "authKeyId": self.key_id,
        }
        payload["authTag"] = self._tag(payload, domain=_ANCHOR_DOMAIN)
        try:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_record_rejected"
                )
            atomic_json_write(path, payload, durable=True)
        except MemoryIntegrityAuthenticityError:
            raise
        except OSError:
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_anchor_unavailable"
            ) from None

    def external_anchor_position(self) -> tuple[int, str] | None:
        record = self._read_anchor()
        if record is None:
            return None
        return int(record["sequence"]), str(record["eventHash"])

    def inspect_anchor(
        self,
        *,
        sequence: int,
        event_hash: str,
        previous_hash: str,
    ) -> str:
        if not self.external_anchor_configured:
            return "unconfigured"
        sequence, event_hash = _position(sequence, event_hash)
        previous = _valid_hash(previous_hash)
        record = self._read_anchor()
        if record is None:
            if self.allow_unsigned_bootstrap:
                return "bootstrap_required"
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_anchor_bootstrap_required"
            )
        anchored = (int(record["sequence"]), str(record["eventHash"]))
        if anchored == (sequence, event_hash):
            return "verified"
        if anchored == (sequence - 1, previous):
            return "lagging"
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_anchor_replay_detected"
        )

    def reconcile_anchor(
        self,
        *,
        sequence: int,
        event_hash: str,
        previous_hash: str,
    ) -> str:
        state = self.inspect_anchor(
            sequence=sequence,
            event_hash=event_hash,
            previous_hash=previous_hash,
        )
        if state in {"unconfigured", "verified"}:
            return state
        if state == "bootstrap_required":
            self._write_anchor(sequence, event_hash)
            return "bootstrapped"
        if state == "lagging":
            self._write_anchor(sequence, event_hash)
            return "recovered"
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_anchor_replay_detected"
        )


def load_memory_integrity_authenticity(
    *,
    protected_root: Path,
    additional_protected_roots: Iterable[Path] = (),
    environ: Mapping[str, str] | None = None,
) -> MemoryIntegrityAuthenticity:
    values = os.environ if environ is None else environ
    raw_key_path = str(values.get(MEMORY_INTEGRITY_KEY_FILE_ENV) or "").strip()
    raw_anchor_root = str(
        values.get(MEMORY_INTEGRITY_ANCHOR_DIR_ENV) or ""
    ).strip()
    bootstrap = _enabled(values.get(MEMORY_INTEGRITY_BOOTSTRAP_ENV))
    if not raw_key_path:
        if raw_anchor_root:
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_anchor_without_key"
            )
        return MemoryIntegrityAuthenticity(
            allow_unsigned_bootstrap=bootstrap
        )
    key_path = Path(raw_key_path)
    if not key_path.is_absolute():
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_auth_key_path_invalid"
        )
    try:
        protected_roots = (
            Path(protected_root).resolve(),
            *(Path(root).resolve() for root in additional_protected_roots),
        )
        if key_path.is_symlink():
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_key_file_rejected"
            )
        resolved_key = key_path.resolve(strict=True)
        if (
            any(_is_within(resolved_key, root) for root in protected_roots)
            or not resolved_key.is_file()
            or resolved_key.stat().st_size > MEMORY_INTEGRITY_MAX_FILE_BYTES
        ):
            raise MemoryIntegrityAuthenticityError(
                "memory_provenance_correction_auth_key_file_rejected"
            )
        key = _decode_key_file(resolved_key.read_bytes())
        anchor_root: Path | None = None
        if raw_anchor_root:
            candidate = Path(raw_anchor_root)
            if not candidate.is_absolute() or candidate.is_symlink():
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_directory_rejected"
                )
            anchor_root = candidate.resolve(strict=True)
            if (
                not anchor_root.is_dir()
                or _is_within(resolved_key, anchor_root)
                or any(
                    _is_within(anchor_root, root)
                    for root in protected_roots
                )
            ):
                raise MemoryIntegrityAuthenticityError(
                    "memory_provenance_correction_anchor_directory_rejected"
                )
    except MemoryIntegrityAuthenticityError:
        raise
    except OSError:
        raise MemoryIntegrityAuthenticityError(
            "memory_provenance_correction_auth_key_unavailable"
        ) from None
    return MemoryIntegrityAuthenticity(
        key=key,
        allow_unsigned_bootstrap=bootstrap,
        anchor_root=anchor_root,
    )


__all__ = [
    "MEMORY_INTEGRITY_ALGORITHM",
    "MEMORY_INTEGRITY_ANCHOR_DIR_ENV",
    "MEMORY_INTEGRITY_ANCHOR_SCHEMA",
    "MEMORY_INTEGRITY_BOOTSTRAP_ENV",
    "MEMORY_INTEGRITY_HEAD_SCHEMA",
    "MEMORY_INTEGRITY_KEY_FILE_ENV",
    "MEMORY_INTEGRITY_SCOPE",
    "MemoryIntegrityAuthenticity",
    "MemoryIntegrityAuthenticityError",
    "load_memory_integrity_authenticity",
]
