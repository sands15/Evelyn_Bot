from __future__ import annotations

import argparse
import configparser
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = "cloud-source-manifest.json"

DENIED_PREFIXES = (
    ".git/",
    ".venv/",
    ".venv-host/",
    ".venv-lora/",
    ".venv-vision/",
    ".venv-voyager/",
    "archive/logs/",
    "archive/nested-git/",
    "archive/scratch/",
    "bot_memory/",
    "bot_profiles/voiceprints/",
    "debug_audio/",
    "external/voyager-upstream/",
    "guild_settings/",
    "logs/",
    "node_modules/",
    "omnivoice_profiles/",
    "recordings/",
    "runtime_artifacts/",
    "tmp/",
    "training/logs/",
    "training/outputs/",
    "venv/",
    "venv_pycord_test/",
)
DENIED_SEGMENTS = {".git", "__pycache__", "node_modules"}
DENIED_BASENAMES = {
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "service-account.json",
    "service_account.json",
}
DENIED_SUFFIXES = {
    ".ckpt",
    ".db",
    ".gguf",
    ".h5",
    ".hdf5",
    ".jks",
    ".joblib",
    ".key",
    ".mlmodel",
    ".npy",
    ".npz",
    ".onnx",
    ".p12",
    ".pem",
    ".pfx",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
    ".sqlite",
    ".sqlite3",
    ".tfstate",
    ".tflite",
}
AUDIO_SUFFIXES = {".flac", ".m4a", ".mp3", ".ogg", ".wav"}
ALLOWED_AUDIO_PATHS = {
    "assets/audio_cache/wake_call_default.wav",
    "tools/probes/sample_input.wav",
}

# These expressions intentionally target provider-specific, high-confidence
# credential shapes. Generic strings such as ``token=fake`` are common in tests
# and documentation and are not reliable evidence of a leaked credential.
SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")),
    ("openai_api_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("huggingface_token", re.compile(rb"\bhf_[A-Za-z0-9]{30,}\b")),
)


class CloudSourceExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    data: bytes
    mode: int


@dataclass(frozen=True)
class SubmodulePin:
    path: str
    url: str
    commit: str


def _run_git(repo: Path, *args: str, binary: bool = False) -> str | bytes:
    command = [
        "git",
        "-c",
        f"safe.directory={repo.resolve().as_posix()}",
        "-C",
        str(repo),
        *args,
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = (stderr or "git command failed").strip()
        raise CloudSourceExportError(detail)
    return completed.stdout


def _validate_archive_path(path: str) -> str:
    if not path or "\\" in path:
        raise CloudSourceExportError(f"unsafe archive path: {path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CloudSourceExportError(f"unsafe archive path: {path!r}")
    return pure.as_posix()


def path_policy_violation(path: str) -> str | None:
    normalized = _validate_archive_path(path)
    lowered = normalized.lower()
    pure = PurePosixPath(lowered)

    if lowered == MANIFEST_PATH:
        return "reserved_manifest_path"
    if any(lowered.startswith(prefix) for prefix in DENIED_PREFIXES):
        return "runtime_or_dependency_path"
    if any(part in DENIED_SEGMENTS or part.startswith(".venv") for part in pure.parts):
        return "runtime_or_dependency_segment"
    if pure.parts and pure.parts[0] == "training" and any(
        part in {"logs", "outputs"} for part in pure.parts[1:]
    ):
        return "runtime_or_dependency_path"

    basename = pure.name
    if basename in DENIED_BASENAMES:
        return "credential_file"
    if basename == ".env" or (basename.startswith(".env.") and basename != ".env.example"):
        return "environment_secret_file"

    suffix = pure.suffix
    if suffix in DENIED_SUFFIXES:
        return "secret_database_or_model_file"
    if suffix in AUDIO_SUFFIXES and lowered not in ALLOWED_AUDIO_PATHS:
        return "unapproved_audio_file"
    if basename.endswith(".log") or basename == ".evelyn_bot.lock":
        return "runtime_log_or_lock"
    return None


def secret_content_violation(data: bytes) -> str | None:
    for rule, pattern in SECRET_PATTERNS:
        if pattern.search(data):
            return rule
    return None


def _entries_from_tar(payload: bytes, *, prefix: str = "") -> list[ArchiveEntry]:
    entries: list[ArchiveEntry] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            relative = _validate_archive_path(member.name)
            target = _validate_archive_path(f"{prefix}/{relative}" if prefix else relative)
            if member.isfile():
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise CloudSourceExportError(f"could not read archived file: {target}")
                data = extracted.read()
                mode = 0o100000 | (member.mode & 0o777)
            elif member.issym():
                data = member.linkname.encode("utf-8")
                mode = 0o120000 | (member.mode & 0o777)
            else:
                raise CloudSourceExportError(f"unsupported archived file type: {target}")
            entries.append(ArchiveEntry(path=target, data=data, mode=mode))
    return entries


def _parse_submodule_config(entries: list[ArchiveEntry]) -> list[tuple[str, str]]:
    gitmodules = next((entry.data for entry in entries if entry.path == ".gitmodules"), None)
    if gitmodules is None:
        return []
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(gitmodules.decode("utf-8"))
    except (UnicodeDecodeError, configparser.Error) as exc:
        raise CloudSourceExportError(f"invalid committed .gitmodules: {exc}") from exc

    configured: list[tuple[str, str]] = []
    for section in parser.sections():
        if not section.startswith("submodule "):
            continue
        path = _validate_archive_path(parser.get(section, "path"))
        url = parser.get(section, "url").strip()
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise CloudSourceExportError(f"submodule {path} must use a credential-free HTTPS URL")
        configured.append((path, url))
    return sorted(configured)


def _resolve_submodule_pin(repo: Path, root_commit: str, path: str, url: str) -> SubmodulePin:
    raw = str(_run_git(repo, "ls-tree", root_commit, "--", path)).strip()
    match = re.fullmatch(r"160000 commit ([0-9a-f]{40})\t(.+)", raw)
    if match is None or match.group(2) != path:
        raise CloudSourceExportError(f"submodule {path} is not a pinned gitlink in {root_commit}")
    return SubmodulePin(path=path, url=url, commit=match.group(1))


def _load_submodule_entries(repo: Path, pin: SubmodulePin) -> list[ArchiveEntry]:
    checkout = repo / Path(*PurePosixPath(pin.path).parts)
    if not checkout.is_dir():
        raise CloudSourceExportError(
            f"submodule checkout is missing: {pin.path}; run git submodule update --init --recursive"
        )
    head = str(_run_git(checkout, "rev-parse", "HEAD")).strip()
    if head != pin.commit:
        raise CloudSourceExportError(
            f"submodule {pin.path} is at {head}, expected pinned commit {pin.commit}"
        )
    status = str(_run_git(checkout, "status", "--porcelain", "--untracked-files=normal"))
    if status.strip():
        raise CloudSourceExportError(f"submodule checkout is dirty: {pin.path}")
    nested = str(_run_git(checkout, "ls-tree", "-r", pin.commit))
    if any(line.startswith("160000 ") for line in nested.splitlines()):
        raise CloudSourceExportError(f"nested submodules are not supported: {pin.path}")
    payload = _run_git(checkout, "archive", "--format=tar", pin.commit, binary=True)
    assert isinstance(payload, bytes)
    entries = _entries_from_tar(payload, prefix=pin.path)
    if any(entry.path == f"{pin.path}/.gitmodules" for entry in entries):
        raise CloudSourceExportError(f"nested submodule configuration is not supported: {pin.path}")
    return entries


def _validate_entries(entries: list[ArchiveEntry]) -> None:
    seen: set[str] = set()
    violations: list[dict[str, str]] = []
    for entry in entries:
        if entry.path in seen:
            raise CloudSourceExportError(f"duplicate archive path: {entry.path}")
        seen.add(entry.path)
        path_rule = path_policy_violation(entry.path)
        if path_rule:
            violations.append({"path": entry.path, "rule": path_rule})
            continue
        secret_rule = secret_content_violation(entry.data)
        if secret_rule:
            violations.append({"path": entry.path, "rule": secret_rule})
    if violations:
        rendered = json.dumps(violations, ensure_ascii=False, sort_keys=True)
        raise CloudSourceExportError(f"cloud source policy rejected tracked content: {rendered}")


def _content_digest(entries: list[ArchiveEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.path):
        digest.update(entry.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(f"{entry.mode:o}".encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(entry.data).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def _build_manifest(root_commit: str, pins: list[SubmodulePin], entries: list[ArchiveEntry]) -> bytes:
    manifest = {
        "schema": "evelyn.cloud-source.v1",
        "root": {"commit": root_commit},
        "submodules": [
            {"path": pin.path, "url": pin.url, "commit": pin.commit}
            for pin in sorted(pins, key=lambda item: item.path)
        ],
        "content": {
            "fileCount": len(entries),
            "totalBytes": sum(len(entry.data) for entry in entries),
            "sha256": _content_digest(entries),
        },
    }
    return (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_zip(output: Path, entries: list[ArchiveEntry], manifest: bytes) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        with zipfile.ZipFile(
            temporary_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as bundle:
            all_entries = [*entries, ArchiveEntry(MANIFEST_PATH, manifest, 0o100644)]
            for entry in sorted(all_entries, key=lambda item: item.path):
                info = zipfile.ZipInfo(entry.path, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = entry.mode << 16
                bundle.writestr(info, entry.data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        temporary_path.replace(output)
        temporary_path = None
        return digest
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def export_cloud_source(
    *,
    repo: Path,
    output: Path,
    ref: str = "HEAD",
    allow_dirty: bool = False,
    force: bool = False,
) -> dict[str, object]:
    repo = repo.resolve()
    output = output.resolve()
    if output.exists() and not force:
        raise CloudSourceExportError(f"output already exists (use --force): {output}")
    if not (repo / ".git").exists():
        raise CloudSourceExportError(f"not a git worktree: {repo}")
    status = str(_run_git(repo, "status", "--porcelain", "--untracked-files=normal"))
    dirty = bool(status.strip())
    if dirty and not allow_dirty:
        raise CloudSourceExportError("worktree is dirty; commit changes or use --allow-dirty to export only the committed ref")

    root_commit = str(_run_git(repo, "rev-parse", f"{ref}^{{commit}}" )).strip()
    root_payload = _run_git(repo, "archive", "--format=tar", root_commit, binary=True)
    assert isinstance(root_payload, bytes)
    entries = _entries_from_tar(root_payload)

    pins = [
        _resolve_submodule_pin(repo, root_commit, path, url)
        for path, url in _parse_submodule_config(entries)
    ]
    for pin in pins:
        entries.extend(_load_submodule_entries(repo, pin))

    _validate_entries(entries)
    manifest = _build_manifest(root_commit, pins, entries)
    bundle_sha256 = _write_zip(output, entries, manifest)
    return {
        "schema": "evelyn.cloud-source-export.result.v1",
        "output": str(output),
        "bundleSha256": bundle_sha256,
        "rootCommit": root_commit,
        "submoduleCount": len(pins),
        "sourceFileCount": len(entries),
        "dirtyWorktreeExcluded": dirty,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a deterministic, secret-screened Evelyn source bundle for cloud transfer."
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="Git worktree to export.")
    parser.add_argument("--output", type=Path, required=True, help="Destination ZIP path.")
    parser.add_argument("--ref", default="HEAD", help="Committed ref to export (default: HEAD).")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit a dirty worktree; uncommitted changes are excluded from the bundle.",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing destination ZIP.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = export_cloud_source(
            repo=args.repo,
            output=args.output,
            ref=args.ref,
            allow_dirty=args.allow_dirty,
            force=args.force,
        )
    except CloudSourceExportError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
