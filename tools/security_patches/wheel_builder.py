"""Build hash-pinned, narrowly patched wheels; never import their runtime code."""
from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile


MAX_WHEEL_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
DOWNLOAD_SECONDS = 60
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Unsafe wheel or patch path")
    parts = value.split("/")
    for part in parts:
        if (part in ("", ".", "..") or part.endswith((".", " "))
                or re.search(r'[\x00-\x1f\x7f<>:"|?*]', part)
                or part.casefold() == ".git"
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError("Unsafe wheel or patch path")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Pinned wheel downloads must not redirect")


def _download_wheel(url: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org"
            or not parsed.path.startswith("/packages/") or parsed.query or parsed.fragment):
        raise ValueError("Expected a fixed official PyPI wheel URL")
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    content = bytearray()
    with urllib.request.build_opener(_NoRedirect()).open(url, timeout=15) as response:
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("Pinned wheel download deadline exceeded")
            # read1 performs one underlying read: slow senders cannot extend a read indefinitely.
            chunk = response.read1(min(1024 * 1024, MAX_WHEEL_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_WHEEL_BYTES:
                raise ValueError("Pinned wheel exceeds size limit")
    return bytes(content)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest key")
        result[key] = value
    return result


def _manifest(package: str, project: Path):
    if package not in ("accelerate", "transformers"):
        raise ValueError("Unsupported security patch project")
    root = project.resolve().parent
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    entries = manifest["packages"]
    if not isinstance(entries, list) or len({item["name"] for item in entries}) != len(entries):
        raise ValueError("Expected unique package manifest entries")
    matches = [item for item in entries if item["name"] == package]
    if len(matches) != 1:
        raise ValueError("Missing package manifest")
    entry = matches[0]
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", entry["version"]):
        raise ValueError("Expected an exact upstream release version")
    if not _SHA256.fullmatch(entry["wheel_sha256"]):
        raise ValueError("Invalid wheel SHA256")
    if not isinstance(entry["files"], dict) or not entry["files"]:
        raise ValueError("Expected explicit reviewed patch files")
    for name, hashes in entry["files"].items():
        if not _path(name).startswith(package + "/"):
            raise ValueError("Patch must remain in the selected package")
        if not all(_SHA256.fullmatch(hashes[key]) for key in ("before", "after")):
            raise ValueError("Invalid source SHA256")
    if not isinstance(entry["advisories"], list) or not entry["advisories"] or not all(isinstance(item, str) and item for item in entry["advisories"]):
        raise ValueError("Expected advisory provenance")
    patch_path = root / _path(entry["patch"])
    if not patch_path.resolve().is_relative_to(root) or patch_path.is_symlink():
        raise ValueError("Patch escapes manifest root")
    patch_bytes = patch_path.read_bytes()
    if len(patch_bytes) > 4 * 1024 * 1024:
        raise ValueError("Patch exceeds size limit")
    return entry, patch_bytes


def _read_wheel(content: bytes):
    if len(content) > MAX_WHEEL_BYTES:
        raise ValueError("Pinned wheel exceeds size limit")
    files, seen = {}, set()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 50000 or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
            raise ValueError("Expanded wheel exceeds size limit")
        for info in entries:
            raw_name = info.orig_filename
            name = _path(raw_name[:-1] if raw_name.endswith("/") else raw_name)
            if name.casefold() in seen:
                raise ValueError("Duplicate wheel path")
            seen.add(name.casefold())
            mode = stat.S_IFMT(info.external_attr >> 16)
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
                raise ValueError("Wheel contains a link, special or encrypted entry")
            if info.is_dir():
                continue
            if mode == stat.S_IFDIR:
                raise ValueError("Invalid wheel file type")
            files[name] = (archive.read(info), info.external_attr)
    folded = {name.casefold() for name in files}
    if any(str(parent).casefold() in folded for name in files for parent in PurePosixPath(name).parents if str(parent) != "."):
        raise ValueError("Wheel file and directory paths overlap")
    return files


def _apply_patch(files, entry, patch_bytes):
    if re.search(rb"(?m)^(?:new file mode|deleted file mode|old mode|new mode|rename from|rename to|copy from|copy to|GIT binary patch)\b", patch_bytes):
        raise ValueError("Only in-place text patches are supported")
    expected = set(entry["files"])
    for name, hashes in entry["files"].items():
        if name not in files or hashlib.sha256(files[name][0]).hexdigest() != hashes["before"]:
            raise ValueError("Source file SHA256 mismatch")
    with tempfile.TemporaryDirectory(prefix="evelyn-security-patch-") as temporary:
        tree = Path(temporary).resolve()
        env = {name: value for name, value in os.environ.items() if name.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "TMPDIR"}}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_CONFIG_SYSTEM=os.devnull, GIT_ATTR_NOSYSTEM="1",
                   GIT_CEILING_DIRECTORIES=str(tree.parent))

        def git_apply(*args):
            result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "apply", *args, "-"], input=patch_bytes, cwd=tree, env=env, capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError("Reviewed patch does not apply exactly")
            return result.stdout

        stats = git_apply("--numstat", "-z").split(b"\0")
        paths = []
        for row in stats:
            if row:
                added, removed, name = row.decode("utf-8").split("\t", 2)
                if not added.isdecimal() or not removed.isdecimal():
                    raise ValueError("Binary patches are unsupported")
                paths.append(_path(name))
        if set(paths) != expected or len(paths) != len(expected):
            raise ValueError("Patch paths differ from reviewed manifest")
        for name in expected:
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files[name][0])
        git_apply("--whitespace=nowarn")
        actual = {path.relative_to(tree).as_posix() for path in tree.rglob("*") if path.is_file()}
        if actual != expected or any(path.is_symlink() for path in tree.rglob("*")):
            raise ValueError("Patch created unexpected files")
        for name, hashes in entry["files"].items():
            content = (tree / name).read_bytes()
            if hashlib.sha256(content).hexdigest() != hashes["after"]:
                raise ValueError("Patched file SHA256 mismatch")
            files[name] = (content, files[name][1])


def build_wheel(package: str, project_dir, wheel_directory, metadata_directory=None) -> str:
    if metadata_directory is not None:
        raise ValueError("This backend uses PEP 517 wheel-based metadata preparation")
    entry, patch_bytes = _manifest(package, Path(project_dir))
    content = _download_wheel(entry["wheel_url"])
    if hashlib.sha256(content).hexdigest() != entry["wheel_sha256"]:
        raise ValueError("Official wheel SHA256 mismatch")
    files = _read_wheel(content)
    version = entry["version"]
    old_info = f"{package}-{version}.dist-info"
    metadata_paths = [name for name in files if name.endswith(".dist-info/METADATA")]
    if metadata_paths != [old_info + "/METADATA"]:
        raise ValueError("Wheel distribution metadata differs from manifest")
    metadata = files[metadata_paths[0]][0]
    headers = BytesParser().parsebytes(metadata)
    if headers.get_all("Name") != [package] or headers.get_all("Version") != [version]:
        raise ValueError("Wheel name or version differs from manifest")
    for required in ("WHEEL", "RECORD"):
        if old_info + "/" + required not in files:
            raise ValueError("Wheel metadata is incomplete")
    if any(name.endswith(("/RECORD.jws", "/RECORD.p7s")) for name in files):
        raise ValueError("Cannot preserve upstream signatures after patching")
    filename = urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(entry["wheel_url"]).path).name)
    prefix = f"{package}-{version}-"
    if not filename.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", filename):
        raise ValueError("Wheel filename differs from manifest")
    _apply_patch(files, entry, patch_bytes)
    local_version = version + "+evelyn.1"
    new_info = f"{package}-{local_version}.dist-info"
    metadata, count = re.subn(rb"(?m)^Version: " + re.escape(version.encode()) + rb"(\r?)$", b"Version: " + local_version.encode() + rb"\1", metadata, count=1)
    if count != 1:
        raise ValueError("Unsupported metadata Version header")
    files[old_info + "/METADATA"] = (metadata, files[old_info + "/METADATA"][1])
    renamed = {}
    for name, value in files.items():
        for old, new in ((old_info + "/", new_info + "/"), (f"{package}-{version}.data/", f"{package}-{local_version}.data/")):
            if name.startswith(old):
                name = new + name[len(old):]
                break
        renamed[name] = value
    provenance = {
        "name": package, "upstream_version": version, "local_version": local_version,
        "wheel_sha256": entry["wheel_sha256"],
        "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
        "files": entry["files"],
    }
    marker = new_info + "/evelyn_security_patch.json"
    if marker in renamed:
        raise ValueError("Upstream wheel already contains a security patch marker")
    renamed[marker] = ((json.dumps(provenance, sort_keys=True, indent=2) + "\n").encode("utf-8"), 0o100644 << 16)
    record_name = new_info + "/RECORD"
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, (data, _mode) in sorted(renamed.items()):
        if name != record_name:
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
            writer.writerow((name, "sha256=" + digest, len(data)))
    writer.writerow((record_name, "", ""))
    renamed[record_name] = (record.getvalue().encode("utf-8"), files[old_info + "/RECORD"][1])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, (data, attributes) in sorted(renamed.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = attributes
            archive.writestr(info, data)
    output = Path(wheel_directory)
    output.mkdir(parents=True, exist_ok=True)
    result = f"{package}-{local_version}-" + filename[len(prefix):]
    with (output / result).open("xb") as handle:
        handle.write(stream.getvalue())
    return result
