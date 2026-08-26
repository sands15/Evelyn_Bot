from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import subprocess
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .workspace_task_tools import (
    WORKSPACE_EDIT_ABSENT_SHA,
    WORKSPACE_TASK_MAX_OUTPUT_BYTES,
    _ALLOWED_ROOT_FILENAMES,
    _allowed_workspace_path,
    _clip_utf8,
    _external_tracked_path_allowed,
    _invalid_path_component,
    _is_link_like,
    _is_sensitive,
    _result,
    _sanitized_environment,
)


_IMAGE_ID_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_CONTAINER_ID_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_PRIVATE_COMPONENTS = frozenset({"private", ".private", "__pycache__"})
_SNAPSHOT_TEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cfg",
        ".cjs",
        ".css",
        ".example",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".lock",
        ".md",
        ".mjs",
        ".ps1",
        ".py",
        ".pyi",
        ".sh",
        ".svg",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_MAX_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 1024 * 1024
_MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
_MAX_SNAPSHOT_FILES = 2_048
_MAX_TEST_TARGETS = 8
_DOCKER_CONTROL_TIMEOUT_SEC = 3.0
_DOCKER_TEST_TIMEOUT_SEC = 20.0
_DOCKER_LOG_TIMEOUT_SEC = 2.0
_RUNNER_PATH = "/usr/local/bin/evelyn-workspace-test-runner"
_RUNNER_PROTOCOL = "evelyn-workspace-test-runner-v1"
_CANARY_SENTINEL = "evelyn-workspace-sandbox-canary-v2"
# Candidate code shares the runner's UID and can forge its child exit receipt.
# The protocol is therefore only an isolated observation used to inform human
# approval; it is never semantic-completion evidence.
_SEMANTIC_VERIFIED = False
_RUNNER_PASS_PATTERN = re.compile(
    rf"^{re.escape(_RUNNER_PROTOCOL)}:passed:([1-9][0-9]{{0,5}})$"
)
_RUNNER_FAIL_PATTERN = re.compile(
    rf"^{re.escape(_RUNNER_PROTOCOL)}:failed:([0-9]{{1,6}})$"
)
_SNAPSHOT_MARKER = ".evelyn-workspace-snapshot-owner"
_SNAPSHOT_MARKER_SCHEMA = "evelyn.workspace-test-snapshot.v1"
_MAX_SNAPSHOT_TREE_ENTRIES = 4_096


class WorkspaceSnapshotError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _snapshot_file_type_allowed(parts: tuple[str, ...]) -> bool:
    name = parts[-1].casefold() if parts else ""
    return bool(
        name in _ALLOWED_ROOT_FILENAMES
        or name in {"license", "makefile", "readme"}
        or name.startswith("dockerfile")
        or Path(name).suffix in _SNAPSHOT_TEXT_SUFFIXES
    )


def _normalize_relative_path(
    raw_path: Any,
    *,
    external_tracked_paths: frozenset[str],
    allow_untracked_external: bool = False,
) -> tuple[tuple[str, ...], str]:
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or len(raw_path) > 512
        or "\x00" in raw_path
    ):
        raise WorkspaceSnapshotError("workspace_snapshot_path_invalid")
    normalized = raw_path.replace("\\", "/")
    raw_parts = normalized.split("/")
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or normalized.startswith("-")
        or any(
            part in {"", ".", ".."} or _invalid_path_component(part)
            for part in raw_parts
        )
    ):
        raise WorkspaceSnapshotError("workspace_snapshot_path_invalid")
    parts = tuple(raw_parts)
    lowered = tuple(part.casefold() for part in parts)
    if (
        _is_sensitive(parts)
        or any(part in _PRIVATE_COMPONENTS for part in lowered)
        or not _allowed_workspace_path(parts)
        or (
            not allow_untracked_external
            and not _external_tracked_path_allowed(parts, external_tracked_paths)
        )
    ):
        raise WorkspaceSnapshotError("workspace_snapshot_path_denied")
    return parts, Path(*parts).as_posix()


def _safe_file_bytes(path: Path) -> bytes:
    try:
        if _is_link_like(path):
            raise WorkspaceSnapshotError("workspace_snapshot_link_denied")
        before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
            raise WorkspaceSnapshotError("workspace_snapshot_hardlink_denied")
        if int(before.st_size) > _MAX_SNAPSHOT_FILE_BYTES:
            raise WorkspaceSnapshotError("workspace_snapshot_file_too_large")
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            data = handle.read(_MAX_SNAPSHOT_FILE_BYTES + 1)
            opened_after = os.fstat(handle.fileno())
        identity_before = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_nlink),
            int(before.st_size),
            int(before.st_mtime_ns),
        )
        identity_opened_before = (
            int(opened_before.st_dev),
            int(opened_before.st_ino),
            int(opened_before.st_nlink),
            int(opened_before.st_size),
            int(opened_before.st_mtime_ns),
        )
        identity_opened_after = (
            int(opened_after.st_dev),
            int(opened_after.st_ino),
            int(opened_after.st_nlink),
            int(opened_after.st_size),
            int(opened_after.st_mtime_ns),
        )
        after = os.stat(path, follow_symlinks=False)
        identity_after = (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_nlink),
            int(after.st_size),
            int(after.st_mtime_ns),
        )
        if (
            len(data) > _MAX_SNAPSHOT_FILE_BYTES
            or len(data) != int(before.st_size)
            or not all(
                stat.S_ISREG(value.st_mode)
                for value in (before, opened_before, opened_after, after)
            )
            or identity_before != identity_opened_before
            or identity_before != identity_opened_after
            or identity_before != identity_after
            or _is_link_like(path)
        ):
            raise WorkspaceSnapshotError("workspace_snapshot_read_ambiguous")
        return data
    except WorkspaceSnapshotError:
        raise
    except OSError:
        raise WorkspaceSnapshotError("workspace_snapshot_read_unavailable") from None


def _assert_path_chain_safe(root: Path, parts: tuple[str, ...]) -> Path:
    current = root
    for part in parts:
        current = current / part
        if _is_link_like(current):
            raise WorkspaceSnapshotError("workspace_snapshot_link_denied")
    try:
        current.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        raise WorkspaceSnapshotError("workspace_snapshot_path_invalid") from None
    return current


def _collect_workspace_files(
    project_root: Path,
    *,
    external_tracked_paths: frozenset[str],
) -> dict[str, bytes]:
    if not isinstance(external_tracked_paths, frozenset) or any(
        not isinstance(value, str) for value in external_tracked_paths
    ):
        raise WorkspaceSnapshotError("workspace_snapshot_manifest_invalid")
    folded_manifest: set[str] = set()
    for value in external_tracked_paths:
        folded = value.casefold()
        if folded in folded_manifest:
            raise WorkspaceSnapshotError("workspace_snapshot_manifest_invalid")
        folded_manifest.add(folded)
    root_input = Path(project_root).absolute()
    try:
        if not root_input.is_dir() or _is_link_like(root_input):
            raise WorkspaceSnapshotError("workspace_snapshot_root_unsafe")
        root = root_input.resolve(strict=True)
    except OSError:
        raise WorkspaceSnapshotError("workspace_snapshot_root_unavailable") from None
    files: dict[str, bytes] = {}
    total_bytes = 0

    def add_file(path: Path, relative: str) -> None:
        nonlocal total_bytes
        data = _safe_file_bytes(path)
        total_bytes += len(data)
        if (
            len(files) >= _MAX_SNAPSHOT_FILES
            or total_bytes > _MAX_SNAPSHOT_BYTES
            or relative in files
        ):
            raise WorkspaceSnapshotError("workspace_snapshot_bounds_exceeded")
        files[relative] = data

    for raw_relative in sorted(external_tracked_paths):
        try:
            parts, relative = _normalize_relative_path(
                raw_relative,
                external_tracked_paths=external_tracked_paths,
            )
        except WorkspaceSnapshotError as exc:
            if exc.code == "workspace_snapshot_path_denied":
                continue
            raise
        if raw_relative != relative:
            raise WorkspaceSnapshotError("workspace_snapshot_manifest_invalid")
        path = _assert_path_chain_safe(root, parts)
        if not path.exists():
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            raise WorkspaceSnapshotError("workspace_snapshot_manifest_invalid")
        if _snapshot_file_type_allowed(parts):
            add_file(path, relative)
    if _is_link_like(root_input):
        raise WorkspaceSnapshotError("workspace_snapshot_root_unsafe")
    return files


def _tree_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256(b"evelyn.workspace-tree.v1\n")
    for relative in sorted(files):
        path_bytes = relative.encode("utf-8")
        data = files[relative]
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def workspace_tree_digest(
    project_root: Path,
    *,
    external_tracked_paths: frozenset[str],
    overlay_path: str | None = None,
    overlay_bytes: bytes | None = None,
) -> str:
    if (overlay_path is None) != (overlay_bytes is None) or not isinstance(
        external_tracked_paths, frozenset
    ):
        raise WorkspaceSnapshotError("workspace_snapshot_overlay_invalid")
    files = _collect_workspace_files(
        Path(project_root),
        external_tracked_paths=external_tracked_paths,
    )
    if overlay_path is not None:
        if not isinstance(overlay_bytes, bytes) or len(overlay_bytes) > _MAX_CANDIDATE_BYTES:
            raise WorkspaceSnapshotError("workspace_snapshot_overlay_invalid")
        parts, relative = _normalize_relative_path(
            overlay_path,
            external_tracked_paths=external_tracked_paths,
        )
        if not _snapshot_file_type_allowed(parts):
            raise WorkspaceSnapshotError("workspace_snapshot_overlay_invalid")
        files[relative] = overlay_bytes
    return _tree_digest(files)


def _prepare_stage_trees(
    project_root: Path,
    *,
    stage: dict[str, Any],
    workspace_tracked_paths: frozenset[str],
) -> tuple[dict[str, str], dict[str, bytes], dict[str, bytes]]:
    if (
        not isinstance(stage, dict)
        or stage.get("mode") not in {"create", "replace"}
        or not _SHA256_PATTERN.fullmatch(str(stage.get("candidateSha256") or ""))
    ):
        raise WorkspaceSnapshotError("workspace_test_candidate_invalid")
    candidate = stage.get("candidateBytes")
    if not isinstance(candidate, bytes) or len(candidate) > _MAX_CANDIDATE_BYTES:
        raise WorkspaceSnapshotError("workspace_test_candidate_invalid")
    candidate_sha = hashlib.sha256(candidate).hexdigest()
    if not hmac.compare_digest(candidate_sha, str(stage["candidateSha256"])):
        raise WorkspaceSnapshotError("workspace_test_candidate_invalid")
    candidate_parts, candidate_path = _normalize_relative_path(
        stage.get("path"),
        external_tracked_paths=workspace_tracked_paths,
        allow_untracked_external=True,
    )
    if not _snapshot_file_type_allowed(candidate_parts):
        raise WorkspaceSnapshotError("workspace_test_candidate_invalid")
    base_files = _collect_workspace_files(
        Path(project_root),
        external_tracked_paths=workspace_tracked_paths,
    )
    base = base_files.get(candidate_path)
    expected_base = str(stage.get("baseSha256") or "")
    live_candidate = _assert_path_chain_safe(
        Path(project_root).resolve(),
        candidate_parts,
    )
    if stage["mode"] == "create":
        valid_base = bool(
            base is None
            and not live_candidate.exists()
            and expected_base == WORKSPACE_EDIT_ABSENT_SHA
        )
    else:
        if base is None and live_candidate.is_file():
            base = _safe_file_bytes(live_candidate)
            if len(base) > _MAX_CANDIDATE_BYTES:
                raise WorkspaceSnapshotError("workspace_test_candidate_invalid")
            if (
                len(base_files) >= _MAX_SNAPSHOT_FILES
                or sum(len(value) for value in base_files.values()) + len(base)
                > _MAX_SNAPSHOT_BYTES
            ):
                raise WorkspaceSnapshotError("workspace_snapshot_bounds_exceeded")
            base_files[candidate_path] = base
        valid_base = bool(
            base is not None
            and _SHA256_PATTERN.fullmatch(expected_base)
            and hmac.compare_digest(hashlib.sha256(base).hexdigest(), expected_base)
        )
    if not valid_base:
        raise WorkspaceSnapshotError("workspace_test_candidate_stale")
    candidate_files = dict(base_files)
    candidate_files[candidate_path] = candidate
    evidence = {
        "candidatePath": candidate_path,
        "candidateSha256": candidate_sha,
        "baseTreeSha256": _tree_digest(base_files),
        "candidateTreeSha256": _tree_digest(candidate_files),
    }
    return evidence, base_files, candidate_files


def workspace_stage_tree_digests(
    project_root: Path,
    *,
    stage: dict[str, Any],
    workspace_tracked_paths: frozenset[str],
) -> dict[str, str]:
    evidence, _, _ = _prepare_stage_trees(
        Path(project_root),
        stage=stage,
        workspace_tracked_paths=workspace_tracked_paths,
    )
    return evidence


def _write_snapshot(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(mode=0o755)
    for relative in sorted(files):
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        try:
            with target.open("xb") as handle:
                handle.write(files[relative])
            os.chmod(target, 0o444)
        except OSError:
            raise WorkspaceSnapshotError("workspace_snapshot_copy_failed") from None


def _fixed_container_environment() -> tuple[str, ...]:
    return (
        "HOME=/tmp",
        "PYTHONPATH=/workspace/evelyn_core/runtime",
        "PYTHONUTF8=1",
        "PYTHONDONTWRITEBYTECODE=1",
        "NO_COLOR=1",
        "OPENAI_API_KEY=",
        "DISCORD_TOKEN=",
        "LOCAL_BRIDGE_STATUS_AUTH_TOKEN=",
        "EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN=",
        "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN=",
    )


def _project_scope(project_root: Path) -> str:
    normalized = str(Path(project_root).resolve()).replace("\\", "/").casefold()
    return hashlib.sha256(
        b"evelyn.workspace-test.project.v1\n" + normalized.encode("utf-8")
    ).hexdigest()


def _snapshot_marker_bytes(project_scope: str) -> bytes:
    return f"{_SNAPSHOT_MARKER_SCHEMA}\n{project_scope}\n".encode("ascii")


def _directory_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        if _is_link_like(path):
            return None
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    return (
        (int(metadata.st_dev), int(metadata.st_ino), int(metadata.st_mode))
        if stat.S_ISDIR(metadata.st_mode)
        else None
    )


def _file_identity(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        if _is_link_like(path):
            return None
        metadata = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    return (
        (
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_mode),
            int(metadata.st_nlink),
            int(metadata.st_size),
        )
        if stat.S_ISREG(metadata.st_mode) and int(metadata.st_nlink) == 1
        else None
    )


def _marker_matches(path: Path, expected: bytes) -> bool:
    before = _file_identity(path)
    if before is None or before[-1] != len(expected):
        return False
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            value = handle.read(len(expected) + 1)
    except OSError:
        return False
    opened_identity = (
        int(opened.st_dev),
        int(opened.st_ino),
        int(opened.st_mode),
        int(opened.st_nlink),
        int(opened.st_size),
    )
    return bool(
        opened_identity == before
        and _file_identity(path) == before
        and hmac.compare_digest(value, expected)
    )


def _write_snapshot_marker(path: Path, value: bytes) -> bool:
    try:
        with path.open("xb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
    except OSError:
        return False
    return _marker_matches(path, value)


def _purge_owned_snapshot_directory(path: Path, *, marker: bytes) -> bool:
    root_identity = _directory_identity(path)
    if root_identity is None:
        return False
    files: list[tuple[Path, tuple[int, int, int, int, int]]] = []
    directories: list[tuple[Path, tuple[int, int, int]]] = []
    entry_count = 0

    def collect(directory: Path, depth: int) -> bool:
        nonlocal entry_count
        if depth > 64:
            return False
        before = _directory_identity(directory)
        if before is None:
            return False
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            return False
        for entry in entries:
            entry_count += 1
            if entry_count > _MAX_SNAPSHOT_TREE_ENTRIES:
                return False
            child = Path(entry.path)
            file_identity = _file_identity(child)
            if file_identity is not None:
                files.append((child, file_identity))
                continue
            directory_identity = _directory_identity(child)
            if directory_identity is None or not collect(child, depth + 1):
                return False
            directories.append((child, directory_identity))
        return _directory_identity(directory) == before

    if not collect(path, 0):
        return False
    marker_path = path / _SNAPSHOT_MARKER
    marker_record = next(
        (record for record in files if record[0] == marker_path),
        None,
    )
    if files and (
        marker_record is None or not _marker_matches(marker_path, marker)
    ):
        return False
    if not files and directories:
        return False
    for target, identity in files:
        if target == marker_path:
            continue
        if _file_identity(target) != identity:
            return False
        try:
            os.chmod(target, 0o600)
            target.unlink()
        except OSError:
            return False
    for target, identity in directories:
        if _directory_identity(target) != identity:
            return False
        try:
            target.rmdir()
        except OSError:
            return False
    if marker_record is not None:
        if not _marker_matches(marker_path, marker):
            return False
        try:
            os.chmod(marker_path, 0o600)
            marker_path.unlink()
        except OSError:
            return False
    if _directory_identity(path) != root_identity:
        return False
    try:
        path.rmdir()
    except OSError:
        return False
    return not path.exists()


def reconcile_workspace_snapshot_root(
    project_root: Path,
    snapshot_root: Path,
) -> bool:
    root = Path(snapshot_root).absolute()
    project_scope = _project_scope(project_root)
    marker = _snapshot_marker_bytes(project_scope)
    try:
        if root.exists():
            if _directory_identity(root) is None or root.resolve() != root:
                return False
        else:
            root.mkdir(parents=True, mode=0o700)
            if _directory_identity(root) is None or root.resolve() != root:
                return False
        root_marker = root / _SNAPSHOT_MARKER
        if not root_marker.exists():
            if any(os.scandir(root)) or not _write_snapshot_marker(root_marker, marker):
                return False
        elif not _marker_matches(root_marker, marker):
            return False
        os.chmod(root, 0o700)
        prefix = f"evelyn-snapshot-{project_scope[:16]}-"
        pattern = re.compile(rf"^{re.escape(prefix)}[a-f0-9]{{8,64}}$")
        for entry in sorted(os.scandir(root), key=lambda item: item.name):
            if entry.name == _SNAPSHOT_MARKER:
                continue
            child = Path(entry.path)
            if (
                not pattern.fullmatch(entry.name)
                or _directory_identity(child) is None
                or not _purge_owned_snapshot_directory(child, marker=marker)
            ):
                return False
        return _directory_identity(root) is not None and _marker_matches(
            root_marker,
            marker,
        )
    except OSError:
        return False


def _create_owned_snapshot_directory(
    snapshot_root: Path,
    *,
    project_scope: str,
    suffix: str,
) -> Path:
    root = Path(snapshot_root).absolute()
    marker = _snapshot_marker_bytes(project_scope)
    if (
        _directory_identity(root) is None
        or root.resolve() != root
        or not _marker_matches(root / _SNAPSHOT_MARKER, marker)
    ):
        raise WorkspaceSnapshotError("workspace_snapshot_root_unavailable")
    target = root / f"evelyn-snapshot-{project_scope[:16]}-{suffix}"
    try:
        target.mkdir(mode=0o700)
    except OSError:
        raise WorkspaceSnapshotError("workspace_snapshot_copy_failed") from None
    if not _write_snapshot_marker(target / _SNAPSHOT_MARKER, marker):
        try:
            cleanup_verified = _purge_owned_snapshot_directory(
                target,
                marker=marker,
            )
        except Exception:
            cleanup_verified = False
        raise WorkspaceSnapshotError(
            "workspace_snapshot_copy_failed"
            if cleanup_verified
            else "workspace_test_snapshot_cleanup_unverified"
        )
    return target


def _docker_create_command(
    *,
    name: str,
    image_id: str,
    labels: tuple[str, ...],
    program_args: tuple[str, ...],
    snapshot_root: Path | None,
    project_scope: str,
) -> list[str]:
    command = [
        "docker",
        "create",
        "--name",
        name,
    ]
    for label in (
        "com.evelyn.workspace-test.owner=evelyn-host",
        f"com.evelyn.workspace-test.project={project_scope}",
        *labels,
    ):
        command.extend(("--label", label))
    command.extend(
        (
            "--network",
            "none",
            "--ipc",
            "none",
            "--read-only",
            "--user",
            "65534:65534",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1.0",
            "--ulimit",
            "nofile=256:256",
            "--ulimit",
            "core=0:0",
            "--stop-timeout",
            "1",
            "--tmpfs",
            "/app:rw,noexec,nosuid,nodev,size=1m,mode=755",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=64k",
            "--log-opt",
            "max-file=1",
        )
    )
    if snapshot_root is not None:
        command.extend(
            (
                "--mount",
                f"type=bind,source={snapshot_root},target=/workspace,readonly",
                "--workdir",
                "/workspace",
            )
        )
    else:
        command.extend(("--workdir", "/app"))
    for item in _fixed_container_environment():
        command.extend(("--env", item))
    command.extend(("--entrypoint", _RUNNER_PATH, image_id, *program_args))
    return command


def _run_command(
    run_command: Callable[..., Any],
    command: list[str],
    *,
    timeout: float,
) -> Any:
    return run_command(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
        env=_sanitized_environment(),
    )


def _cleanup_container(run_command: Callable[..., Any], container_id: str) -> bool:
    if not _CONTAINER_ID_PATTERN.fullmatch(container_id):
        return False
    try:
        completed = _run_command(
            run_command,
            ["docker", "rm", "-f", container_id],
            timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(
        int(getattr(completed, "returncode", -1)) == 0
        and str(getattr(completed, "stdout", "") or "").strip() == container_id
    )


def _resolve_created_container(
    run_command: Callable[..., Any],
    name: str,
    *,
    project_scope: str,
    role: str,
) -> str:
    try:
        completed = _run_command(
            run_command,
            [
                "docker",
                "container",
                "inspect",
                "--format",
                "{{.Id}} {{index .Config.Labels \"com.evelyn.workspace-test\"}} "
                "{{index .Config.Labels \"com.evelyn.workspace-test.owner\"}} "
                "{{index .Config.Labels \"com.evelyn.workspace-test.project\"}} "
                "{{index .Config.Labels \"com.evelyn.workspace-test.role\"}}",
                name,
            ],
            timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    fields = str(getattr(completed, "stdout", "") or "").strip().split()
    return (
        fields[0]
        if int(getattr(completed, "returncode", -1)) == 0
        and len(fields) == 5
        and _CONTAINER_ID_PATTERN.fullmatch(fields[0])
        and fields[1:] == ["1", "evelyn-host", project_scope, role]
        else ""
    )


def _reconcile_orphan_containers(
    run_command: Callable[..., Any],
    *,
    project_scope: str,
) -> bool:
    identifiers: list[str] = []
    for role in ("candidate", "canary"):
        try:
            completed = _run_command(
                run_command,
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--no-trunc",
                    "--filter",
                    "label=com.evelyn.workspace-test=1",
                    "--filter",
                    "label=com.evelyn.workspace-test.owner=evelyn-host",
                    "--filter",
                    f"label=com.evelyn.workspace-test.project={project_scope}",
                    "--filter",
                    f"label=com.evelyn.workspace-test.role={role}",
                ],
                timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        stdout = str(getattr(completed, "stdout", "") or "")
        if int(getattr(completed, "returncode", -1)) != 0 or len(stdout.encode("utf-8")) > 8192:
            return False
        identifiers.extend(line.strip() for line in stdout.splitlines() if line.strip())
    if len(identifiers) != len(set(identifiers)) or any(
        not _CONTAINER_ID_PATTERN.fullmatch(identifier) for identifier in identifiers
    ):
        return False
    cleanup_results = tuple(
        _cleanup_container(run_command, identifier) for identifier in identifiers
    )
    return all(cleanup_results)


def _run_container(
    *,
    run_command: Callable[..., Any],
    create_command: list[str],
    wait_timeout_sec: float,
    project_scope: str,
    role: str,
) -> dict[str, Any]:
    container_id = ""
    started = False
    result: dict[str, Any]
    try:
        name = create_command[create_command.index("--name") + 1]
    except (ValueError, IndexError):
        return {
            "state": "unverified",
            "code": "container_create_contract_invalid",
            "cleanupVerified": False,
        }
    try:
        created = _run_command(
            run_command,
            create_command,
            timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
        )
        raw_id = str(getattr(created, "stdout", "") or "").strip()
        if int(getattr(created, "returncode", -1)) != 0:
            container_id = (
                raw_id
                if _CONTAINER_ID_PATTERN.fullmatch(raw_id)
                else _resolve_created_container(
                    run_command,
                    name,
                    project_scope=project_scope,
                    role=role,
                )
            )
            result = {
                "state": "not_started",
                "code": "container_create_failed",
                "cleanupVerified": bool(container_id),
            }
        elif not _CONTAINER_ID_PATTERN.fullmatch(raw_id):
            container_id = _resolve_created_container(
                run_command,
                name,
                project_scope=project_scope,
                role=role,
            )
            result = {
                "state": "unverified",
                "code": "container_id_invalid",
                "cleanupVerified": bool(container_id),
            }
        else:
            container_id = raw_id
            started_result = _run_command(
                run_command,
                ["docker", "start", container_id],
                timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
            )
            if int(getattr(started_result, "returncode", -1)) != 0:
                result = {
                    "state": "not_started",
                    "code": "container_start_failed",
                    "cleanupVerified": True,
                }
            else:
                started = True
                waited = _run_command(
                    run_command,
                    ["docker", "wait", container_id],
                    timeout=wait_timeout_sec,
                )
                raw_exit = str(getattr(waited, "stdout", "") or "").strip()
                if (
                    int(getattr(waited, "returncode", -1)) != 0
                    or not re.fullmatch(r"[0-9]{1,3}", raw_exit)
                    or int(raw_exit) > 255
                ):
                    result = {
                        "state": "unverified",
                        "code": "container_wait_invalid",
                        "cleanupVerified": True,
                    }
                else:
                    logs = _run_command(
                        run_command,
                        ["docker", "logs", container_id],
                        timeout=_DOCKER_LOG_TIMEOUT_SEC,
                    )
                    if int(getattr(logs, "returncode", -1)) != 0:
                        result = {
                            "state": "unverified",
                            "code": "container_logs_unavailable",
                            "cleanupVerified": True,
                        }
                    else:
                        stdout, stdout_truncated = _clip_utf8(
                            getattr(logs, "stdout", ""),
                            WORKSPACE_TASK_MAX_OUTPUT_BYTES // 2,
                        )
                        stderr, stderr_truncated = _clip_utf8(
                            getattr(logs, "stderr", ""),
                            WORKSPACE_TASK_MAX_OUTPUT_BYTES // 2,
                        )
                        result = {
                            "state": "completed",
                            "code": "container_completed",
                            "exitCode": int(raw_exit),
                            "stdout": stdout,
                            "stderr": stderr,
                            "outputTruncated": bool(stdout_truncated or stderr_truncated),
                            "cleanupVerified": True,
                        }
    except subprocess.TimeoutExpired:
        if not container_id:
            container_id = _resolve_created_container(
                run_command,
                name,
                project_scope=project_scope,
                role=role,
            )
        result = {
            "state": "unverified" if started else "not_started",
            "code": "container_timeout",
            "cleanupVerified": bool(container_id),
        }
    except OSError:
        if not container_id:
            container_id = _resolve_created_container(
                run_command,
                name,
                project_scope=project_scope,
                role=role,
            )
        result = {
            "state": "unverified" if started else "not_started",
            "code": "container_runtime_unavailable",
            "cleanupVerified": bool(container_id),
        }
    finally:
        if container_id and not _cleanup_container(run_command, container_id):
            result["cleanupVerified"] = False
    return result


def attest_workspace_test_image(
    *,
    project_root: Path,
    image_reference: str,
    expected_image_id: str,
    run_command: Callable[..., Any] = subprocess.run,
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> dict[str, Any]:
    failed = {
        "ready": False,
        "imageId": "",
        "canaryVerified": False,
        "code": "workspace_test_image_not_attested",
    }
    if (
        not isinstance(image_reference, str)
        or not image_reference
        or len(image_reference) > 256
        or image_reference.startswith("-")
        or "\x00" in image_reference
        or not _IMAGE_ID_PATTERN.fullmatch(expected_image_id)
    ):
        return failed
    project_scope = _project_scope(project_root)
    if not _reconcile_orphan_containers(
        run_command,
        project_scope=project_scope,
    ):
        return failed
    try:
        inspected = _run_command(
            run_command,
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_reference],
            timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return failed
    inspected_id = str(getattr(inspected, "stdout", "") or "").strip()
    if (
        int(getattr(inspected, "returncode", -1)) != 0
        or not hmac.compare_digest(inspected_id, expected_image_id)
    ):
        return failed
    name_suffix = str(id_factory() or "")
    if not re.fullmatch(r"[a-f0-9]{8,64}", name_suffix):
        return failed
    lifecycle = _run_container(
        run_command=run_command,
        create_command=_docker_create_command(
            name=f"evelyn-workspace-canary-{name_suffix}",
            image_id=expected_image_id,
            labels=(
                "com.evelyn.workspace-test=1",
                "com.evelyn.workspace-test.role=canary",
            ),
            program_args=("canary", "--protocol", _RUNNER_PROTOCOL),
            snapshot_root=None,
            project_scope=project_scope,
        ),
        wait_timeout_sec=_DOCKER_CONTROL_TIMEOUT_SEC,
        project_scope=project_scope,
        role="canary",
    )
    canary_verified = bool(
        lifecycle.get("state") == "completed"
        and lifecycle.get("exitCode") == 0
        and str(lifecycle.get("stdout") or "").strip() == _CANARY_SENTINEL
        and not str(lifecycle.get("stderr") or "").strip()
        and lifecycle.get("cleanupVerified") is True
    )
    ready = canary_verified
    return {
        "ready": ready,
        "imageId": expected_image_id if ready else "",
        "canaryVerified": canary_verified,
        "semanticVerified": _SEMANTIC_VERIFIED,
        "code": (
            "workspace_test_image_attested"
            if ready
            else "workspace_test_image_not_attested"
        ),
    }


def attest_workspace_test_image_reference(
    *,
    project_root: Path,
    image_reference: str,
    run_command: Callable[..., Any] = subprocess.run,
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> dict[str, Any]:
    failed = {
        "ready": False,
        "imageId": "",
        "canaryVerified": False,
        "semanticVerified": _SEMANTIC_VERIFIED,
        "code": "workspace_test_image_not_attested",
    }
    if (
        not isinstance(image_reference, str)
        or not image_reference
        or len(image_reference) > 256
        or image_reference.startswith("-")
        or "\x00" in image_reference
    ):
        return failed
    try:
        inspected = _run_command(
            run_command,
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_reference],
            timeout=_DOCKER_CONTROL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return failed
    image_id = str(getattr(inspected, "stdout", "") or "").strip()
    if (
        int(getattr(inspected, "returncode", -1)) != 0
        or not _IMAGE_ID_PATTERN.fullmatch(image_id)
    ):
        return failed
    return attest_workspace_test_image(
        project_root=project_root,
        image_reference=image_reference,
        expected_image_id=image_id,
        run_command=run_command,
        id_factory=id_factory,
    )


class WorkspaceTestSandbox:
    def __init__(
        self,
        project_root: Path,
        *,
        image_id: str = "",
        attested_image_id: str = "",
        canary_verified: bool = False,
        run_command: Callable[..., Any] = subprocess.run,
        snapshot_root: Path | None = None,
        snapshot_reconciled: bool = False,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.project_root = Path(project_root)
        self.image_id = str(image_id or "")
        self.attested_image_id = str(attested_image_id or "")
        self.canary_verified = canary_verified is True
        self.semantic_verified = _SEMANTIC_VERIFIED
        self._run_command = run_command
        self.snapshot_root = (
            Path(snapshot_root).absolute() if snapshot_root is not None else None
        )
        self.snapshot_reconciled = snapshot_reconciled is True
        self._id_factory = id_factory
        self._cleanup_failed = threading.Event()

    def _snapshot_root_ready(self) -> bool:
        if self.snapshot_root is None or not self.snapshot_reconciled:
            return False
        project_scope = _project_scope(self.project_root)
        return bool(
            _directory_identity(self.snapshot_root) is not None
            and self.snapshot_root.resolve() == self.snapshot_root
            and _marker_matches(
                self.snapshot_root / _SNAPSHOT_MARKER,
                _snapshot_marker_bytes(project_scope),
            )
        )

    @property
    def ready(self) -> bool:
        return bool(
            _IMAGE_ID_PATTERN.fullmatch(self.image_id)
            and _IMAGE_ID_PATTERN.fullmatch(self.attested_image_id)
            and hmac.compare_digest(self.image_id, self.attested_image_id)
            and self.canary_verified
            and self._snapshot_root_ready()
            and not self._cleanup_failed.is_set()
        )

    @staticmethod
    def _valid_target(target: Any) -> str:
        if not isinstance(target, str) or len(target) > 512 or "\x00" in target:
            raise WorkspaceSnapshotError("workspace_test_target_invalid")
        normalized = target.replace("\\", "/")
        parts = normalized.split("/")
        filename = parts[-1] if parts else ""
        if (
            len(parts) < 2
            or parts[0] != "tests"
            or any(part in {"", ".", ".."} for part in parts)
            or normalized.startswith("-")
            or not filename.startswith("test_")
            or filename == "test_.py"
            or not filename.endswith(".py")
            or any(_invalid_path_component(part) for part in parts)
        ):
            raise WorkspaceSnapshotError("workspace_test_target_invalid")
        return normalized

    def run(
        self,
        *,
        stage: dict,
        args: dict,
        external_tracked_paths: frozenset[str],
    ) -> dict[str, Any]:
        if not self.ready:
            return _result(
                attempted=True,
                executed=False,
                observed=True,
                verified=True,
                outcome="blocked",
                code="workspace_test_sandbox_unavailable",
                summary="Workspace test sandbox is not attested.",
            )
        try:
            if (
                not isinstance(stage, dict)
                or not isinstance(args, dict)
                or set(args) != {"runner", "targets"}
                or args.get("runner") != "python_unittest"
                or not isinstance(args.get("targets"), list)
                or not 1 <= len(args["targets"]) <= _MAX_TEST_TARGETS
                or not isinstance(external_tracked_paths, frozenset)
                or stage.get("requiresSandboxTest") is not True
                or not _IDENTIFIER_PATTERN.fullmatch(str(stage.get("stageId") or ""))
                or stage.get("mode") not in {"create", "replace"}
                or not _SHA256_PATTERN.fullmatch(str(stage.get("candidateSha256") or ""))
            ):
                raise WorkspaceSnapshotError("workspace_test_request_invalid")
            tree_evidence, _, candidate_files = _prepare_stage_trees(
                self.project_root,
                stage=stage,
                workspace_tracked_paths=external_tracked_paths,
            )
            candidate_path = tree_evidence["candidatePath"]
            candidate_sha = tree_evidence["candidateSha256"]
            targets = tuple(self._valid_target(value) for value in args["targets"])
            if len(set(targets)) != len(targets):
                raise WorkspaceSnapshotError("workspace_test_target_invalid")
            candidate_stem = Path(candidate_path).stem.casefold()
            for target in targets:
                target_bytes = candidate_files.get(target)
                if target not in external_tracked_paths or target_bytes is None:
                    raise WorkspaceSnapshotError("workspace_test_target_untracked")
                target_text = target_bytes.decode("utf-8", errors="ignore").casefold()
                target_stem = Path(target).stem.casefold()
                if (
                    candidate_path != target
                    and candidate_stem not in target_stem
                    and not re.search(
                        rf"(?<![a-z0-9_]){re.escape(candidate_stem)}(?![a-z0-9_])",
                        target_text,
                    )
                ):
                    raise WorkspaceSnapshotError("workspace_test_target_unrelated")
            suffix = str(self._id_factory() or "")
            if not re.fullmatch(r"[a-f0-9]{8,64}", suffix):
                raise WorkspaceSnapshotError("workspace_test_request_invalid")
            project_scope = _project_scope(self.project_root)
            if self.snapshot_root is None:
                raise WorkspaceSnapshotError("workspace_snapshot_root_unavailable")
            owned_snapshot_root = _create_owned_snapshot_directory(
                self.snapshot_root,
                project_scope=project_scope,
                suffix=suffix,
            )
            result: dict[str, Any]
            cleanup_uncertain = False
            try:
                snapshot_root = owned_snapshot_root / "snapshot"
                _write_snapshot(snapshot_root, candidate_files)
                lifecycle = _run_container(
                    run_command=self._run_command,
                    create_command=_docker_create_command(
                        name=f"evelyn-workspace-test-{suffix}",
                        image_id=self.image_id,
                        labels=(
                            "com.evelyn.workspace-test=1",
                            "com.evelyn.workspace-test.role=candidate",
                            f"com.evelyn.workspace-test.stage-id={stage['stageId']}",
                            f"com.evelyn.workspace-test.candidate-sha256={candidate_sha}",
                        ),
                        program_args=(
                            "python-unittest",
                            "--protocol",
                            _RUNNER_PROTOCOL,
                            "--",
                            *targets,
                        ),
                        snapshot_root=snapshot_root,
                        project_scope=project_scope,
                    ),
                    wait_timeout_sec=_DOCKER_TEST_TIMEOUT_SEC,
                    project_scope=project_scope,
                    role="candidate",
                )
                evidence = {
                    "stageId": stage["stageId"],
                    "candidatePath": candidate_path,
                    "candidateSha256": candidate_sha,
                    "baseTreeSha256": tree_evidence["baseTreeSha256"],
                    "candidateTreeSha256": tree_evidence["candidateTreeSha256"],
                    "runner": "python_unittest",
                    "targets": list(targets),
                    "testsRun": 0,
                    "semanticVerified": self.semantic_verified,
                    "exitCode": lifecycle.get("exitCode"),
                    "stdout": str(lifecycle.get("stdout") or ""),
                    "stderr": str(lifecycle.get("stderr") or ""),
                    "outputTruncated": bool(lifecycle.get("outputTruncated")),
                }
                if lifecycle.get("cleanupVerified") is not True:
                    self._cleanup_failed.set()
                    result = _result(
                        attempted=True,
                        executed=True,
                        observed=False,
                        verified=False,
                        outcome="outcome_unverified",
                        code="workspace_test_cleanup_unverified",
                        summary="Workspace test cleanup is unverified.",
                        evidence=evidence,
                    )
                elif lifecycle.get("state") == "completed":
                    protocol = str(lifecycle.get("stdout") or "").strip()
                    exit_code = lifecycle.get("exitCode")
                    pass_match = _RUNNER_PASS_PATTERN.fullmatch(protocol)
                    fail_match = _RUNNER_FAIL_PATTERN.fullmatch(protocol)
                    evidence["testsRun"] = int(
                        (pass_match or fail_match).group(1)
                    ) if pass_match or fail_match else 0
                    passed = bool(
                        exit_code == 0
                        and pass_match
                        and not str(lifecycle.get("stderr") or "").strip()
                        and lifecycle.get("outputTruncated") is not True
                    )
                    failed = bool(
                        exit_code == 1
                        and fail_match
                        and lifecycle.get("outputTruncated") is not True
                    )
                    if passed or failed:
                        result = _result(
                            attempted=True,
                            executed=True,
                            observed=True,
                            verified=True,
                            outcome="succeeded" if passed else "failed",
                            code=(
                                "workspace_test_passed"
                                if passed
                                else "workspace_test_failed"
                            ),
                            summary=(
                                "The sandbox runner reported that the selected tests passed; semantic behavior remains unverified."
                                if passed
                                else "The sandbox runner reported that the selected tests failed."
                            ),
                            evidence=evidence,
                        )
                    else:
                        result = _result(
                            attempted=True,
                            executed=True,
                            observed=False,
                            verified=False,
                            outcome="outcome_unverified",
                            code="workspace_test_runner_protocol_invalid",
                            summary="Workspace test runner protocol is invalid.",
                            evidence=evidence,
                        )
                elif lifecycle.get("state") == "not_started":
                    result = _result(
                        attempted=True,
                        executed=False,
                        observed=True,
                        verified=True,
                        outcome="blocked",
                        code="workspace_test_sandbox_unavailable",
                        summary="Workspace test sandbox did not start.",
                        evidence=evidence,
                    )
                else:
                    result = _result(
                        attempted=True,
                        executed=True,
                        observed=False,
                        verified=False,
                        outcome="outcome_unverified",
                        code="workspace_test_outcome_unverified",
                        summary="Workspace test outcome is unverified.",
                        evidence=evidence,
                    )
            finally:
                try:
                    snapshot_cleaned = _purge_owned_snapshot_directory(
                        owned_snapshot_root,
                        marker=_snapshot_marker_bytes(project_scope),
                    )
                except Exception:
                    snapshot_cleaned = False
                if not snapshot_cleaned:
                    self._cleanup_failed.set()
                    cleanup_uncertain = True
            if cleanup_uncertain:
                return _result(
                    attempted=True,
                    executed=True,
                    observed=False,
                    verified=False,
                    outcome="outcome_unverified",
                    code="workspace_test_snapshot_cleanup_unverified",
                    summary="Workspace test snapshot cleanup is unverified.",
                )
            return result
        except WorkspaceSnapshotError as exc:
            if exc.code == "workspace_test_snapshot_cleanup_unverified":
                self._cleanup_failed.set()
                return _result(
                    attempted=True,
                    executed=False,
                    observed=False,
                    verified=False,
                    outcome="outcome_unverified",
                    code=exc.code,
                    summary="Workspace test snapshot cleanup is unverified.",
                )
            return _result(
                attempted=True,
                executed=False,
                observed=True,
                verified=True,
                outcome="blocked",
                code=exc.code,
                summary="Workspace test was blocked by the sandbox boundary.",
            )
        except Exception:
            return _result(
                attempted=True,
                executed=False,
                observed=False,
                verified=False,
                outcome="outcome_unverified",
                code="workspace_test_outcome_unverified",
                summary="Workspace test outcome is unverified.",
            )


class CapacityOneWorker:
    def __init__(self, operation: Callable[..., dict[str, Any]]) -> None:
        self._operation = operation
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="evelyn-workspace-sandbox",
        )
        self._lock = threading.Lock()
        self._job_id = ""
        self._future: Future[dict[str, Any]] | None = None
        self._closed = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._future is not None and not self._future.done())

    def submit(self, job_id: str, **kwargs: Any) -> bool:
        with self._lock:
            if (
                self._closed
                or not _IDENTIFIER_PATTERN.fullmatch(str(job_id or ""))
                or self._future is not None
            ):
                return False
            self._job_id = str(job_id)
            self._future = self._executor.submit(self._operation, **kwargs)
            return True

    def poll(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            if self._future is None or self._job_id != str(job_id) or not self._future.done():
                return None
            future = self._future
            self._future = None
            self._job_id = ""
        try:
            result = future.result()
        except Exception:
            return _result(
                attempted=True,
                executed=False,
                observed=False,
                verified=False,
                outcome="outcome_unverified",
                code="workspace_test_outcome_unverified",
                summary="Workspace test worker failed.",
            )
        return dict(result) if isinstance(result, dict) else None

    def close(self) -> bool:
        with self._lock:
            if self._future is not None and not self._future.done():
                return False
            self._future = None
            self._job_id = ""
            if self._closed:
                return True
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        return True


__all__ = [
    "CapacityOneWorker",
    "WorkspaceSnapshotError",
    "WorkspaceTestSandbox",
    "attest_workspace_test_image",
    "attest_workspace_test_image_reference",
    "reconcile_workspace_snapshot_root",
    "workspace_stage_tree_digests",
    "workspace_tree_digest",
]
