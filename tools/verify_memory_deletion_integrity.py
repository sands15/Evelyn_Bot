from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


RESULT_SCHEMA = "memory.deletion.integrity.replica-verification.v1"
VERIFICATION_ERROR = "memory_deletion_integrity_replica_verification_failed"
REPLICA_MARKER = ".evelyn-memory-deletion-integrity-replica"
CHILD_TIMEOUT_SECONDS = 30


class _VerificationFailed(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(
        attributes
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _resolved_without_reparse(path: Path) -> Path:
    if not path.is_absolute():
        raise _VerificationFailed()
    current = path
    while True:
        if _is_reparse_point(current):
            raise _VerificationFailed()
        if current.parent == current:
            break
        current = current.parent
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise _VerificationFailed() from exc


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _overlaps(left: Path, right: Path) -> bool:
    return _within(left, right) or _within(right, left)


def _replica_marker_text() -> str:
    return json.dumps(
        {
            "schema": RESULT_SCHEMA,
            "disposableReplica": True,
            "contentFree": True,
        },
        sort_keys=True,
    )


def _validated_child_scratch(scratch_root: Path) -> Path:
    scratch = _resolved_without_reparse(scratch_root)
    marker = scratch / REPLICA_MARKER
    repo = REPO_ROOT.resolve(strict=True)
    try:
        marker_text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _VerificationFailed() from exc
    if (
        not scratch.is_dir()
        or _overlaps(scratch, repo)
        or _is_reparse_point(marker)
        or not marker.is_file()
        or marker_text != _replica_marker_text()
    ):
        raise _VerificationFailed()
    return scratch


def _validate_parent_inputs(
    scratch_root: Path,
    key_file: Path,
    anchor_dir: Path,
) -> tuple[Path, Path, Path]:
    scratch = _resolved_without_reparse(scratch_root)
    key = _resolved_without_reparse(key_file)
    anchor = _resolved_without_reparse(anchor_dir)
    repo = REPO_ROOT.resolve(strict=True)
    if (
        not scratch.is_dir()
        or not anchor.is_dir()
        or not key.is_file()
        or not 32 <= key.stat().st_size <= 8192
        or any(scratch.iterdir())
        or any(anchor.iterdir())
        or _overlaps(scratch, repo)
        or _overlaps(anchor, repo)
        or _within(key, repo)
        or _overlaps(scratch, anchor)
        or _within(key, scratch)
        or _within(key, anchor)
    ):
        raise _VerificationFailed()
    return scratch, key, anchor


def _child_environment(
    *,
    key_file: Path | None,
    anchor_dir: Path | None,
    bootstrap: bool,
) -> dict[str, str]:
    env = dict(os.environ)
    for name in (
        MEMORY_INTEGRITY_KEY_FILE_ENV,
        MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
        MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    ):
        env.pop(name, None)
    if key_file is not None and anchor_dir is not None:
        env[MEMORY_INTEGRITY_KEY_FILE_ENV] = str(key_file)
        env[MEMORY_INTEGRITY_ANCHOR_DIR_ENV] = str(anchor_dir)
        env[MEMORY_INTEGRITY_BOOTSTRAP_ENV] = (
            "true" if bootstrap else "false"
        )
    return env


def _run_child(
    phase: str,
    scratch_root: Path,
    *,
    key_file: Path | None,
    anchor_dir: Path | None,
    bootstrap: bool = False,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_phase",
        phase,
        "--scratch-root",
        str(scratch_root),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=_child_environment(
                key_file=key_file,
                anchor_dir=anchor_dir,
                bootstrap=bootstrap,
            ),
            capture_output=True,
            text=True,
            timeout=CHILD_TIMEOUT_SECONDS,
            check=False,
        )
        payload = json.loads(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        raise _VerificationFailed() from exc
    if (
        completed.returncode != 0
        or not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("contentFree") is not True
    ):
        raise _VerificationFailed()
    return payload


def _status_is_protected(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    return bool(
        isinstance(status, dict)
        and status.get("schema") == "memory.deletion.integrity.v1"
        and status.get("state") == "rollback_protected"
        and status.get("chainHeadState") == "current"
        and status.get("headAuthenticity") == "verified"
        and status.get("externalAnchorState") == "verified"
        and status.get("authenticityConfigured") is True
        and status.get("externalAnchorConfigured") is True
        and status.get("rollbackProtected") is True
        and status.get("contentFree") is True
    )


def _replace_bytes(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.replica-check.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _tombstone(number: int) -> dict[str, object]:
    return {
        "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
        "noteId": f"replica-integrity-canary-{number}",
        "noteType": "concept",
        "sourceType": "conversation-turn-log",
        "reason": "privacy_request",
        "deletedAt": "2026-08-08T00:00:00Z",
    }


def _child_main(phase: str, scratch_root: Path) -> int:
    try:
        scratch = _validated_child_scratch(scratch_root)
        index_dir = scratch / "memory_root" / "memory_index"
        if phase == "seed":
            event = journal.append_memory_deletion_tombstone(
                index_dir,
                _tombstone(1),
            )
            result = {
                "ok": True,
                "sequence": event.get("sequence"),
                "contentFree": True,
            }
        elif phase == "status":
            result = {
                "ok": True,
                "status": journal.memory_deletion_journal_status(
                    index_dir
                ),
                "contentFree": True,
            }
        elif phase == "append":
            event = journal.append_memory_deletion_tombstone(
                index_dir,
                _tombstone(2),
            )
            result = {
                "ok": True,
                "sequence": event.get("sequence"),
                "contentFree": True,
            }
        elif phase == "read":
            rows = journal.read_memory_deletion_tombstones(index_dir)
            result = {
                "ok": True,
                "rowCount": len(rows),
                "sequence": rows[-1].get("sequence") if rows else 0,
                "contentFree": True,
            }
        elif phase == "expect-integrity-failure":
            try:
                journal.memory_deletion_journal_status(index_dir)
            except journal.MemoryDeletionJournalIntegrityError as exc:
                if (
                    str(exc)
                    != journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
                ):
                    raise _VerificationFailed() from exc
                result = {
                    "ok": True,
                    "error": str(exc),
                    "contentFree": True,
                }
            else:
                raise _VerificationFailed()
        else:
            raise _VerificationFailed()
    except Exception:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": VERIFICATION_ERROR,
                    "contentFree": True,
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


def _parent_main(
    scratch_root: Path,
    key_file: Path,
    anchor_dir: Path,
) -> int:
    try:
        scratch, key, anchor = _validate_parent_inputs(
            scratch_root,
            key_file,
            anchor_dir,
        )
        marker = scratch / REPLICA_MARKER
        with marker.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(_replica_marker_text())

        seeded = _run_child(
            "seed",
            scratch,
            key_file=None,
            anchor_dir=None,
        )
        if seeded.get("sequence") != 1:
            raise _VerificationFailed()
        rejected_before_bootstrap = _run_child(
            "expect-integrity-failure",
            scratch,
            key_file=key,
            anchor_dir=anchor,
        )
        if (
            rejected_before_bootstrap.get("error")
            != journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
        ):
            raise _VerificationFailed()

        adopted = _run_child(
            "status",
            scratch,
            key_file=key,
            anchor_dir=anchor,
            bootstrap=True,
        )
        strict = _run_child(
            "status",
            scratch,
            key_file=key,
            anchor_dir=anchor,
        )
        if not _status_is_protected(adopted) or not _status_is_protected(
            strict
        ):
            raise _VerificationFailed()

        index_dir = scratch / "memory_root" / "memory_index"
        journal_path = (
            index_dir / journal.MEMORY_DELETE_TOMBSTONE_JOURNAL_NAME
        )
        head_path = (
            index_dir / journal.MEMORY_DELETE_TOMBSTONE_CHAIN_HEAD_NAME
        )
        anchor_path = (
            anchor
            / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_ANCHOR_NAME
        )
        witness_path = (
            anchor
            / journal.MEMORY_DELETE_TOMBSTONE_EXTERNAL_INITIALIZATION_NAME
        )
        signed_pair_one = (
            journal_path.read_bytes(),
            head_path.read_bytes(),
        )

        appended = _run_child(
            "append",
            scratch,
            key_file=key,
            anchor_dir=anchor,
        )
        if appended.get("sequence") != 2:
            raise _VerificationFailed()
        signed_pair_two = (
            journal_path.read_bytes(),
            head_path.read_bytes(),
        )
        protected_anchor = anchor_path.read_bytes()
        protected_witness = witness_path.read_bytes()

        try:
            _replace_bytes(journal_path, signed_pair_one[0])
            _replace_bytes(head_path, signed_pair_one[1])
            replay = _run_child(
                "expect-integrity-failure",
                scratch,
                key_file=key,
                anchor_dir=anchor,
            )
            if (
                replay.get("error")
                != journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
                or anchor_path.read_bytes() != protected_anchor
                or witness_path.read_bytes() != protected_witness
            ):
                raise _VerificationFailed()
        finally:
            _replace_bytes(journal_path, signed_pair_two[0])
            _replace_bytes(head_path, signed_pair_two[1])
        restored_status = _run_child(
            "status",
            scratch,
            key_file=key,
            anchor_dir=anchor,
        )
        restored_rows = _run_child(
            "read",
            scratch,
            key_file=key,
            anchor_dir=anchor,
        )
        if (
            not _status_is_protected(restored_status)
            or restored_rows.get("rowCount") != 2
            or restored_rows.get("sequence") != 2
        ):
            raise _VerificationFailed()

        result = {
            "schema": RESULT_SCHEMA,
            "ok": True,
            "replicaContractVerified": True,
            "pathIsolationVerified": True,
            "strictPreBootstrapRejected": True,
            "oneShotBootstrapVerified": True,
            "strictRestartVerified": True,
            "pastPairReplayRejected": True,
            "replicaRestored": True,
            "rollbackProtected": True,
            "sequence": 2,
            "replayError": (
                journal.MEMORY_DELETION_JOURNAL_INTEGRITY_ERROR
            ),
            "permissionState": "not_verified",
            "operationallyVerified": False,
            "contentFree": True,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "schema": RESULT_SCHEMA,
                    "ok": False,
                    "error": VERIFICATION_ERROR,
                    "replicaContractVerified": False,
                    "permissionState": "not_verified",
                    "operationallyVerified": False,
                    "contentFree": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify Evelyn deletion-anchor bootstrap and past-pair replay "
            "rejection in an empty disposable replica."
        )
    )
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--anchor-dir", type=Path)
    parser.add_argument(
        "--_phase",
        choices=(
            "seed",
            "status",
            "append",
            "read",
            "expect-integrity-failure",
        ),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args._phase:
        return _child_main(args._phase, args.scratch_root)
    if args.key_file is None or args.anchor_dir is None:
        print(
            json.dumps(
                {
                    "schema": RESULT_SCHEMA,
                    "ok": False,
                    "error": VERIFICATION_ERROR,
                    "replicaContractVerified": False,
                    "permissionState": "not_verified",
                    "operationallyVerified": False,
                    "contentFree": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    return _parent_main(
        args.scratch_root,
        args.key_file,
        args.anchor_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
