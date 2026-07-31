from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .paths import get_repo_root


VISION_REMOTE_MODEL_LOCK_SCHEMA = "vision.remote-model-lock.v1"
VISION_REMOTE_MODEL_VERIFICATION_SCHEMA = (
    "vision.remote-model-verification.v1"
)
FALCON_OCR_REPO_ID = "tiiuae/Falcon-OCR"
FALCON_OCR_REVISION = "42ec56b72a23984ac059e7c8a6d397a8529423fe"
FALCON_OCR_LOCK_RELATIVE_PATH = (
    Path("docker") / "falcon_ocr_snapshot.lock.json"
)
_LOCK_MAX_BYTES = 64 * 1024
_HASH_CHUNK_BYTES = 4 * 1024 * 1024
_ROLE_LIMITS = {
    "remote_code": 1024 * 1024,
    "configuration": 1024 * 1024,
    "tokenizer": 16 * 1024 * 1024,
    "weights": 2 * 1024 * 1024 * 1024,
}
_EXPECTED_FILES = {
    "attention.py": "remote_code",
    "configuration_falcon_ocr.py": "remote_code",
    "modeling_falcon_ocr.py": "remote_code",
    "processing_falcon_ocr.py": "remote_code",
    "rope.py": "remote_code",
    "config.json": "configuration",
    "model_args.json": "configuration",
    "special_tokens_map.json": "tokenizer",
    "tokenizer_config.json": "tokenizer",
    "tokenizer.json": "tokenizer",
    "model.safetensors": "weights",
}


class VisionRemoteModelLockError(RuntimeError):
    """A stable, content-free remote model verification failure."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class LockedModelFile:
    name: str
    role: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RemoteModelLock:
    repo_id: str
    revision: str
    files: tuple[LockedModelFile, ...]


def _valid_sha256(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64:
        return ""
    lowered = value.lower()
    if not all(character in "0123456789abcdef" for character in lowered):
        return ""
    return lowered


def _valid_revision(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 40:
        return ""
    lowered = value.lower()
    if not all(character in "0123456789abcdef" for character in lowered):
        return ""
    return lowered


def load_remote_model_lock(
    path: Path | None = None,
) -> RemoteModelLock:
    lock_path = path or (get_repo_root() / FALCON_OCR_LOCK_RELATIVE_PATH)
    try:
        if not lock_path.exists() and not lock_path.is_symlink():
            raise VisionRemoteModelLockError(
                "vision_remote_model_lock_unavailable"
            )
        if (
            lock_path.is_symlink()
            or not lock_path.is_file()
            or lock_path.stat().st_size > _LOCK_MAX_BYTES
        ):
            raise VisionRemoteModelLockError(
                "vision_remote_model_lock_rejected"
            )
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except VisionRemoteModelLockError:
        raise
    except FileNotFoundError:
        raise VisionRemoteModelLockError(
            "vision_remote_model_lock_unavailable"
        ) from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise VisionRemoteModelLockError(
            "vision_remote_model_lock_rejected"
        ) from None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "repoId", "revision", "files"}
        or payload.get("schema") != VISION_REMOTE_MODEL_LOCK_SCHEMA
        or payload.get("repoId") != FALCON_OCR_REPO_ID
        or _valid_revision(payload.get("revision"))
        != FALCON_OCR_REVISION
        or not isinstance(payload.get("files"), list)
    ):
        raise VisionRemoteModelLockError(
            "vision_remote_model_lock_rejected"
        )
    files: list[LockedModelFile] = []
    seen: set[str] = set()
    for item in payload["files"]:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "role",
            "size",
            "sha256",
        }:
            raise VisionRemoteModelLockError(
                "vision_remote_model_lock_rejected"
            )
        name = item.get("name")
        role = item.get("role")
        size = item.get("size")
        digest = _valid_sha256(item.get("sha256"))
        if (
            not isinstance(name, str)
            or name in seen
            or _EXPECTED_FILES.get(name) != role
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > _ROLE_LIMITS.get(str(role), 0)
            or not digest
        ):
            raise VisionRemoteModelLockError(
                "vision_remote_model_lock_rejected"
            )
        seen.add(name)
        files.append(
            LockedModelFile(
                name=name,
                role=str(role),
                size=size,
                sha256=digest,
            )
        )
    if seen != set(_EXPECTED_FILES):
        raise VisionRemoteModelLockError(
            "vision_remote_model_lock_rejected"
        )
    return RemoteModelLock(
        repo_id=FALCON_OCR_REPO_ID,
        revision=FALCON_OCR_REVISION,
        files=tuple(files),
    )


def validate_remote_model_configuration(
    lock: RemoteModelLock,
    *,
    repo_id: str,
    revision: str,
) -> None:
    if repo_id != lock.repo_id:
        raise VisionRemoteModelLockError(
            "vision_remote_model_repo_mismatch"
        )
    if _valid_revision(revision) != lock.revision:
        raise VisionRemoteModelLockError(
            "vision_remote_model_revision_mismatch"
        )


def _hash_locked_file(path: Path, expected: LockedModelFile) -> None:
    try:
        before = path.stat()
        if not path.is_file() or before.st_size != expected.size:
            raise VisionRemoteModelLockError(
                "vision_remote_model_integrity_failed"
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
        after = path.stat()
    except VisionRemoteModelLockError:
        raise
    except OSError:
        raise VisionRemoteModelLockError(
            "vision_remote_model_snapshot_unavailable"
        ) from None
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or digest.hexdigest() != expected.sha256
    ):
        raise VisionRemoteModelLockError(
            "vision_remote_model_integrity_failed"
        )


def verify_remote_model_snapshot(
    lock: RemoteModelLock,
    *,
    downloader: Callable[..., str | Path],
    local_files_only: bool,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    role_counts = {role: 0 for role in _ROLE_LIMITS}
    total_bytes = 0
    for expected in lock.files:
        kwargs: dict[str, Any] = {
            "revision": lock.revision,
            "local_files_only": bool(local_files_only),
        }
        if cache_dir is not None:
            kwargs["cache_dir"] = str(cache_dir)
        try:
            downloaded = downloader(
                lock.repo_id,
                expected.name,
                **kwargs,
            )
        except Exception:
            raise VisionRemoteModelLockError(
                "vision_remote_model_snapshot_unavailable"
            ) from None
        _hash_locked_file(Path(downloaded), expected)
        role_counts[expected.role] += 1
        total_bytes += expected.size
    return {
        "schema": VISION_REMOTE_MODEL_VERIFICATION_SCHEMA,
        "verified": True,
        "revisionPinned": True,
        "localFilesOnly": bool(local_files_only),
        "fileCount": len(lock.files),
        "remoteCodeFileCount": role_counts["remote_code"],
        "configurationFileCount": role_counts["configuration"],
        "tokenizerFileCount": role_counts["tokenizer"],
        "weightFileCount": role_counts["weights"],
        "verifiedBytes": total_bytes,
        "contentFree": True,
    }


def public_remote_model_status(
    *,
    configured: bool,
    local_files_only: bool,
    receipt: Mapping[str, Any] | None = None,
    failure_code: str = "",
) -> dict[str, Any]:
    verified = bool(
        receipt
        and receipt.get("schema")
        == VISION_REMOTE_MODEL_VERIFICATION_SCHEMA
        and receipt.get("verified") is True
        and receipt.get("contentFree") is True
    )
    allowed_failures = {
        "vision_remote_model_lock_unavailable",
        "vision_remote_model_lock_rejected",
        "vision_remote_model_repo_mismatch",
        "vision_remote_model_revision_mismatch",
        "vision_remote_model_snapshot_unavailable",
        "vision_remote_model_integrity_failed",
    }
    return {
        "schema": VISION_REMOTE_MODEL_VERIFICATION_SCHEMA,
        "configured": bool(configured),
        "verified": verified,
        "revisionPinned": bool(configured),
        "localFilesOnly": bool(local_files_only),
        "fileCount": int(receipt.get("fileCount") or 0)
        if verified and receipt
        else 0,
        "weightVerified": bool(
            verified and int(receipt.get("weightFileCount") or 0) == 1
        ),
        "failureCode": (
            failure_code if failure_code in allowed_failures else ""
        ),
        "contentFree": True,
    }


__all__ = [
    "FALCON_OCR_LOCK_RELATIVE_PATH",
    "FALCON_OCR_REPO_ID",
    "FALCON_OCR_REVISION",
    "LockedModelFile",
    "RemoteModelLock",
    "VISION_REMOTE_MODEL_LOCK_SCHEMA",
    "VISION_REMOTE_MODEL_VERIFICATION_SCHEMA",
    "VisionRemoteModelLockError",
    "load_remote_model_lock",
    "public_remote_model_status",
    "validate_remote_model_configuration",
    "verify_remote_model_snapshot",
]
