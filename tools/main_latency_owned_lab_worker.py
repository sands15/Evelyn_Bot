"""Own, measure, and tear down the fixed Main-latency Docker lab.

This process has no coordinator signing capability. It returns a content-free
unsigned measurement to the external runner, which validates and signs it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import secrets
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import main_latency_fixed_lab_adapter as adapter
import main_latency_lab_contract as lab
import optimize_main_latency as optimizer
import post_stt_latency_benchmark as benchmark


OWNER = lab.LAB_OWNER
COMPOSE_FILE = TOOLS_DIR.parent / "docker-compose.main-latency-lab.yml"
MODEL_RELATIVE = Path("models/gemma4-12b-batiai-iq4xs/google-gemma-4-12B-it-IQ4_XS.gguf")
RUNTIME_TEMPLATE_ARGS = (
    "--reasoning",
    "off",
    "--reasoning-budget",
    "0",
    "--reasoning-format",
    "none",
    "--jinja",
    "--no-mmproj",
)
CONTAINER_MODEL_PATH = "/llama/models/gemma4-12b-batiai-iq4xs/google-gemma-4-12B-it-IQ4_XS.gguf"
CONTAINER_SERVER_PATH = "/llama/build/bin/llama-server"
FIXED_IMAGES = (
    "evelyn-fast-control-main_llm:latest",
    "evelyn-fast-control-bot_api:latest",
    "evelyn-omnivoice-tts:recipe-e8151492550b",
)
EXPECTED_SERVICES = frozenset(
    {
        "main_llm_lab",
        "main_llm_gateway_lab",
        "tts_lab",
        "bot_api_lab",
        "lab_harness",
        "lab_focused_checks",
        "lab_privacy_checks",
    }
)
MAX_INPUT_BYTES = 1_048_576
MAX_COMMAND_OUTPUT_BYTES = 1_048_576
MAX_PROFILE_ENTRIES = 512
MAX_PROFILE_BYTES = 512 * 1024 * 1024
HASH_ID = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
SOURCE_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}\Z", re.ASCII)
EPOCH_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.ASCII,
)
VOLUME_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,127}\Z", re.ASCII)
PROJECT_NAME = re.compile(r"evelyn-main-latency-[0-9a-f]{16}\Z", re.ASCII)
OWNED_TEMP_NAME = re.compile(
    r"evelyn-latency-[0-9a-f]{8}-[A-Za-z0-9_-]+\Z", re.ASCII
)
OWNED_TEMP_MARKER = ".evelyn-owned-lab.json"
GPU_BASELINE_MARKER = ".evelyn-gpu0-baseline.json"
PROGRESS_CHECKPOINT = "aggregate-checkpoint.json"
PROGRESS_CHECKPOINT_SCHEMA = "evelyn.main-latency-progress.v1"
CLEANUP_STABLE_ZERO_OBSERVATIONS = 3
CLEANUP_MAX_ROUNDS = 30
CLEANUP_STABLE_INTERVAL_S = 1.0
SHORT_DIAGNOSTIC_SCHEMA = "evelyn.main-latency-short-diagnostic.v4"
SHORT_DIAGNOSTIC_RESIDENT_SAMPLES = 5
SHORT_DIAGNOSTIC_IDLE_SECONDS = 12.0
SHORT_DIAGNOSTIC_MAX_RUNTIME_S = 1800.0
WDDM_IDLE_OBSERVATIONS = 3
WDDM_IDLE_MAX_UTILIZATION = 10.0
WDDM_IDLE_MIN_FREE_RATIO = 0.75
WDDM_BASELINE_FREE_TOLERANCE_MIB = 256.0
WINDOWS_LX_SYMLINK_TAG = 0xA000001D
WINDOWS_LX_SYMLINK_KIND = 2
GPU_DEVICE_INSPECT_FORMAT = (
    '{{println (json .HostConfig.DeviceRequests)}}'
    '{{range .Config.Env}}{{if eq . "NVIDIA_VISIBLE_DEVICES=1"}}1{{end}}{{end}}'
)
BOT_STRICT_HEALTHCHECK_SOURCE = (
    "import json,urllib.request; "
    "p=json.loads(urllib.request.urlopen('http://127.0.0.1:8798/api/control-page/state',timeout=3).read()); "
    "r=p.get('runtime') or {}; s=r.get('services') or {}; w=r.get('mainWarmup') or {}; "
    "warm=w.get('ready') is True and (w.get('status') == 'not_managed' or "
    "(w.get('cacheProof') is True and w.get('promptAbiProductionMatch') is True and "
    "(w.get('promptAbiRequired') is not True or "
    "w.get('promptAbiExact') is True))); raise SystemExit(0 if s.get('mainReady') is True "
    "and s.get('sourceAligned') is True and warm else 1)"
)
_AUTHORIZED_COMMAND: tuple[str, ...] | None = None
_OWNED_ROOT: Path | None = None


class LabFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _content_id(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise LabFailure("lab_identity_preflight_failed") from None
    return f"sha256:{digest.hexdigest()}"


def _profile_tree_hash(root: Path) -> str:
    """Hash the exact mounted voice profile tree without exposing its contents."""

    records: list[dict[str, Any]] = []
    total_bytes = 0
    wavs = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            raise LabFailure("lab_identity_preflight_failed") from None
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink() or _is_reparse(path):
                raise LabFailure("lab_isolation_preflight_failed")
            try:
                relative = path.relative_to(root).as_posix()
                is_directory = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except (OSError, ValueError):
                raise LabFailure("lab_isolation_preflight_failed") from None
            if len(records) >= MAX_PROFILE_ENTRIES or not (is_directory or is_file):
                raise LabFailure("lab_identity_preflight_failed")
            if is_directory:
                _contained_leaf(root, path)
                records.append({"path": relative, "kind": "directory"})
                stack.append(path)
                continue
            leaf = _contained_leaf(root, path, file=True)
            try:
                size = leaf.stat().st_size
            except OSError:
                raise LabFailure("lab_identity_preflight_failed") from None
            total_bytes += size
            if size < 0 or total_bytes > MAX_PROFILE_BYTES:
                raise LabFailure("lab_identity_preflight_failed")
            if relative.lower().startswith("evelyn/") and leaf.suffix.lower() == ".wav":
                wavs += 1
            records.append(
                {
                    "path": relative,
                    "kind": "file",
                    "bytes": size,
                    "sha256": _file_hash(leaf),
                }
            )
    if wavs < 1:
        raise LabFailure("lab_isolation_preflight_failed")
    records.sort(key=lambda item: item["path"])
    return _content_id(
        {
            "schema": "evelyn.main-latency-voice-profile-tree.v1",
            "entries": records,
        }
    )


def _owned_artifact_bytes(root: Path, maximum: int) -> int:
    """Measure worker-owned host artifacts, rejecting links and special files."""

    total = 0
    entries_seen = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            raise LabFailure("runner_failed") from None
        for entry in entries:
            path = Path(entry.path)
            entries_seen += 1
            if entries_seen > 4096 or entry.is_symlink() or _is_reparse(path):
                raise LabFailure("runner_failed")
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    raise LabFailure("runner_failed") from None
                if total > maximum:
                    return total
            else:
                raise LabFailure("runner_failed")
    return total


def _decode_windows_lx_symlink(raw: bytes) -> PurePosixPath:
    if len(raw) < 13:
        raise LabFailure("lab_isolation_preflight_failed")
    tag, data_length, _reserved = struct.unpack_from("<IHH", raw)
    if tag != WINDOWS_LX_SYMLINK_TAG or data_length != len(raw) - 8:
        raise LabFailure("lab_isolation_preflight_failed")
    data = raw[8:]
    if int.from_bytes(data[:4], "little") != WINDOWS_LX_SYMLINK_KIND:
        raise LabFailure("lab_isolation_preflight_failed")
    try:
        target = PurePosixPath(data[4:].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LabFailure("lab_isolation_preflight_failed") from None
    if (
        target.is_absolute()
        or not target.parts
        or any(part in {"", ".", ".."} for part in target.parts)
        or "\\" in target.as_posix()
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    return target


def _read_windows_lx_symlink(path: Path) -> PurePosixPath:
    if os.name != "nt":
        raise LabFailure("lab_isolation_preflight_failed")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.CreateFileW(
        str(path),
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00200000 | 0x02000000,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise LabFailure("lab_isolation_preflight_failed")
    buffer = ctypes.create_string_buffer(16 * 1024)
    returned = wintypes.DWORD()
    try:
        if not kernel32.DeviceIoControl(
            handle,
            0x000900A8,
            None,
            0,
            buffer,
            len(buffer),
            ctypes.byref(returned),
            None,
        ):
            raise LabFailure("lab_isolation_preflight_failed")
    finally:
        kernel32.CloseHandle(handle)
    return _decode_windows_lx_symlink(buffer.raw[: returned.value])


def _resolve_shared_object(build_root: Path, path: Path) -> Path:
    current = path.absolute()
    seen: set[Path] = set()
    for _ in range(8):
        if current in seen:
            break
        seen.add(current)
        if current.is_symlink():
            try:
                current = current.resolve(strict=True)
            except OSError:
                break
            continue
        if _is_reparse(current):
            target = _read_windows_lx_symlink(current)
            current = Path(os.path.abspath(current.parent.joinpath(*target.parts)))
            try:
                current.relative_to(build_root)
            except ValueError:
                break
            continue
        return _contained_leaf(build_root, current, file=True)
    raise LabFailure("lab_isolation_preflight_failed")


def _server_build_identity(build_root: Path) -> str:
    """Pin the executable and every local shared-object name/target."""

    build_bin = _contained_leaf(build_root, build_root / "bin")
    server = _contained_leaf(build_root, build_bin / "llama-server", file=True)
    records: list[dict[str, str]] = [
        {"path": CONTAINER_SERVER_PATH, "sha256": _file_hash(server)}
    ]
    try:
        entries = sorted(os.scandir(build_bin), key=lambda item: item.name)
    except OSError:
        raise LabFailure("lab_identity_preflight_failed") from None
    for entry in entries:
        if ".so" not in entry.name:
            continue
        path = Path(entry.path)
        logical = f"/llama/build/bin/{entry.name}"
        if entry.is_symlink() or _is_reparse(path):
            target = _resolve_shared_object(build_root, path)
            records.append(
                {
                    "path": logical,
                    "target": "/llama/build/"
                    + target.relative_to(build_root).as_posix(),
                    "sha256": _file_hash(target),
                }
            )
        elif entry.is_file(follow_symlinks=False) and not _is_reparse(path):
            records.append({"path": logical, "sha256": _file_hash(path)})
        else:
            raise LabFailure("lab_isolation_preflight_failed")
        if len(records) > 256:
            raise LabFailure("lab_identity_preflight_failed")
    return _content_id(
        {
            "schema": "evelyn.main-latency-server-build.v1",
            "files": records,
        }
    )


def _runtime_argv(config: Mapping[str, Any]) -> tuple[str, ...]:
    swa_full_args = ("--swa-full",) if config["main.swaFull"] == 1 else ()
    return (
        CONTAINER_SERVER_PATH,
        "-m",
        CONTAINER_MODEL_PATH,
        "--host",
        "0.0.0.0",
        "--port",
        "9820",
        "--flash-attn",
        "on",
        "-ngl",
        "999",
        *swa_full_args,
        "-c",
        "8192",
        "-np",
        "1",
        "--batch-size",
        str(config["main.batch"]),
        "--ubatch-size",
        str(config["main.ubatch"]),
        "--cache-ram",
        str(config["main.cacheRamMiB"]),
        "--cache-prompt",
        "--cache-reuse",
        str(config["main.cacheReuse"]),
        "--metrics",
        "--repeat-last-n",
        "256",
        "--repeat-penalty",
        "1.10",
        "--presence-penalty",
        "0.00",
        "--frequency-penalty",
        "0.20",
        *RUNTIME_TEMPLATE_ARGS,
    )


def _runtime_identity(config: Mapping[str, Any]) -> str:
    cuda_graphs_enabled = config["main.cudaGraph"]
    values = (
        f"GGML_CUDA_GRAPHS_ENABLED={cuda_graphs_enabled}",
        "GGML_CUDA_DISABLE_GRAPHS="
        + ("absent" if cuda_graphs_enabled == 1 else "present"),
        f"GGML_CUDA_GRAPH_OPT={cuda_graphs_enabled}",
        *_runtime_argv(config),
    )
    return "sha256:" + hashlib.sha256(
        b"".join(value.encode("ascii") + b"\0" for value in values)
    ).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _contained_leaf(root: Path, leaf: Path, *, file: bool = False) -> Path:
    try:
        resolved = leaf.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise LabFailure("lab_isolation_preflight_failed") from None
    relative = resolved.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink() or _is_reparse(cursor):
            raise LabFailure("lab_isolation_preflight_failed")
    if (file and not resolved.is_file()) or (not file and not resolved.is_dir()):
        raise LabFailure("lab_isolation_preflight_failed")
    return resolved


def _operator_dir(name: str, markers: Sequence[tuple[str, bool]]) -> Path:
    raw = os.environ.get(name, "")
    if not raw or raw.startswith(("\\\\", "//")):
        raise LabFailure("lab_isolation_preflight_failed")
    declared = Path(raw)
    if not declared.is_absolute():
        raise LabFailure("lab_isolation_preflight_failed")
    try:
        resolved = declared.resolve(strict=True)
    except OSError:
        raise LabFailure("lab_isolation_preflight_failed") from None
    if resolved != declared.absolute() or not resolved.is_dir() or resolved.is_symlink() or _is_reparse(resolved):
        raise LabFailure("lab_isolation_preflight_failed")
    for relative, is_file in markers:
        _contained_leaf(resolved, resolved / relative, file=is_file)
    return resolved


def _operator_child_dir(
    root: Path,
    name: str,
    default: Path,
    markers: Sequence[tuple[str, bool]],
) -> Path:
    raw = os.environ.get(name, "")
    declared = Path(raw) if raw else default
    if not declared.is_absolute() or (raw and raw.startswith(("\\\\", "//"))):
        raise LabFailure("lab_isolation_preflight_failed")
    try:
        resolved = declared.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise LabFailure("lab_isolation_preflight_failed") from None
    if resolved != declared.absolute():
        raise LabFailure("lab_isolation_preflight_failed")
    resolved = _contained_leaf(root, resolved)
    for relative, is_file in markers:
        _contained_leaf(resolved, resolved / relative, file=is_file)
    return resolved


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _operator_paths() -> dict[str, Path]:
    llama = _operator_dir(
        "EVELYN_LLAMA_CPP_DIR",
        ((MODEL_RELATIVE.as_posix(), True),),
    )
    main_build = _operator_child_dir(
        llama,
        "EVELYN_MAIN_LLM_BUILD_DIR",
        llama / "build",
        (("bin/llama-server", True),),
    )
    omnivoice = _operator_dir(
        "EVELYN_OMNIVOICE_SERVER_DIR",
        (("hub", False),),
    )
    profiles = _operator_dir(
        "EVELYN_OMNIVOICE_PROFILES_DIR",
        (("evelyn", False),),
    )
    roots = (llama, omnivoice, profiles)
    if any(_overlaps(left, right) for index, left in enumerate(roots) for right in roots[index + 1 :]):
        raise LabFailure("lab_isolation_preflight_failed")
    return {
        "llama": llama,
        "main_build": main_build,
        "omnivoice": omnivoice,
        "profiles": profiles,
        "hub": _contained_leaf(omnivoice, omnivoice / "hub"),
        "model": _contained_leaf(llama, llama / MODEL_RELATIVE, file=True),
        "server": _contained_leaf(
            main_build,
            main_build / "bin/llama-server",
            file=True,
        ),
    }


def _fixed_executable(kind: str) -> Path:
    candidates: list[Path] = []
    if os.name == "nt":
        program_files = Path(os.environ.get("PROGRAMFILES", ""))
        system_root = Path(os.environ.get("SYSTEMROOT", os.environ.get("WINDIR", "")))
        if kind == "docker":
            candidates.append(program_files / "Docker/Docker/resources/bin/docker.exe")
        else:
            candidates.extend(
                (
                    system_root / "System32/nvidia-smi.exe",
                    program_files / "NVIDIA Corporation/NVSMI/nvidia-smi.exe",
                )
            )
    else:
        candidates.extend(
            (Path("/usr/bin") / kind, Path("/usr/local/bin") / kind)
        )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file() and not resolved.is_symlink() and not _is_reparse(resolved):
            return resolved
    raise LabFailure("lab_isolation_preflight_failed")


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "subprocess.Popen":
        raw_argv = args[1] if len(args) > 1 else ()
        if isinstance(raw_argv, (str, bytes)):
            command_line = os.fsdecode(raw_argv)
            executable = os.fsdecode(args[0]) if args and args[0] is not None else None
            if (
                _AUTHORIZED_COMMAND is not None
                and executable in {None, _AUTHORIZED_COMMAND[0]}
                and command_line == subprocess.list2cmdline(_AUTHORIZED_COMMAND)
            ):
                return
            raise RuntimeError("owned_lab_child_process_forbidden")
        argv = tuple(os.fsdecode(item) for item in raw_argv)
        executable = (
            argv[0]
            if args and args[0] is None and argv
            else os.fsdecode(args[0]) if args else ""
        )
        if _AUTHORIZED_COMMAND is not None and executable == _AUTHORIZED_COMMAND[0] and argv == _AUTHORIZED_COMMAND:
            return
        raise RuntimeError("owned_lab_child_process_forbidden")
    if event in {"os.system", "os.fork", "pty.spawn"} or event.startswith(
        ("os.spawn", "os.exec", "os.posix_spawn", "os.startfile")
    ):
        raise RuntimeError("owned_lab_child_process_forbidden")
    if event.startswith("socket.") and event not in {"socket.__new__"}:
        raise RuntimeError("owned_lab_host_network_forbidden")
    if event == "open" and len(args) >= 3:
        mode, flags = args[1], args[2]
        writable = (
            isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+"))
        ) or (
            isinstance(flags, int)
            and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC))
        )
        if writable:
            raw_path = os.fsdecode(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else ""
            try:
                target = Path(raw_path).resolve()
                owned = _OWNED_ROOT is not None and (target == _OWNED_ROOT or _OWNED_ROOT in target.parents)
            except (OSError, ValueError):
                owned = False
            if not owned and os.path.normcase(raw_path) != os.path.normcase(os.devnull):
                raise RuntimeError("owned_lab_filesystem_write_forbidden")


def _minimal_child_env(config_dir: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = {
        "DOCKER_CONFIG": str(config_dir),
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "COMPOSE_IGNORE_ORPHANS": "0",
    }
    for key in ("SYSTEMROOT", "WINDIR", "PROGRAMFILES"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if extra:
        env.update(extra)
    return env


def _run_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    config_dir: Path,
    extra_env: Mapping[str, str] | None = None,
    timeout: float = 60.0,
    check: bool = True,
) -> str:
    global _AUTHORIZED_COMMAND
    rendered = tuple(os.fspath(item) for item in command)
    if not rendered or not Path(rendered[0]).is_absolute():
        raise LabFailure("lab_isolation_preflight_failed")
    _AUTHORIZED_COMMAND = rendered
    try:
        completed = subprocess.run(
            rendered,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            close_fds=True,
            env=_minimal_child_env(config_dir, extra_env),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise LabFailure("runner_failed") from None
    finally:
        _AUTHORIZED_COMMAND = None
    if check and completed.returncode != 0:
        raise LabFailure("runner_failed")
    if len(completed.stdout) > MAX_COMMAND_OUTPUT_BYTES:
        raise LabFailure("runner_failed")
    try:
        return completed.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise LabFailure("runner_failed") from None


def _docker_command(docker: Path, *parts: str) -> tuple[str, ...]:
    return (str(docker), *parts)


def _project_name(run_id: str) -> str:
    if HASH_ID.fullmatch(run_id) is None:
        raise LabFailure("lab_isolation_preflight_failed")
    name = f"evelyn-main-latency-{run_id[7:23]}"
    if PROJECT_NAME.fullmatch(name) is None:
        raise LabFailure("lab_isolation_preflight_failed")
    return name


def _compose_command(docker: Path, project: str, *parts: str) -> tuple[str, ...]:
    return _docker_command(
        docker,
        "compose",
        "--ansi",
        "never",
        "--progress",
        "quiet",
        "--profile",
        "*",
        "-f",
        str(COMPOSE_FILE),
        "-p",
        project,
        *parts,
    )


def _base_compose_env(plan: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, str]:
    config = plan["baselineConfig"]
    identities = plan.get("identities")
    if (
        isinstance(identities, dict)
        and HASH_ID.fullmatch(str(identities.get("model", ""))) is not None
        and HASH_ID.fullmatch(str(identities.get("baseline", ""))) is not None
    ):
        model_identity = str(identities["model"])[7:]
        server_identity = str(identities["baseline"])[7:]
    else:
        model_identity = _file_hash(paths["model"])[7:]
        server_identity = _server_build_identity(paths["main_build"])[7:]
    return {
        "LAB_RUN_ID": plan["runId"],
        "LAB_GPU_ID": "0",
        "LAB_LLAMA_CPP_DIR": str(paths["llama"]),
        "LAB_MAIN_LLM_BUILD_DIR": str(paths["main_build"]),
        "LAB_OMNIVOICE_PROFILES_DIR": str(paths["profiles"]),
        "LAB_OMNIVOICE_HUB_DIR": str(paths["hub"]),
        "LAB_TOOLS_DIR": str(Path(__file__).resolve().parent),
        "LAB_MAIN_BATCH": str(config["main.batch"]),
        "LAB_MAIN_UBATCH": str(config["main.ubatch"]),
        "LAB_MAIN_CACHE_RAM_MIB": str(config["main.cacheRamMiB"]),
        "LAB_MAIN_CACHE_REUSE": str(config["main.cacheReuse"]),
        "LAB_MAIN_CUDA_GRAPH": str(config["main.cudaGraph"]),
        "LAB_MAIN_SWA_FULL": str(config["main.swaFull"]),
        "LAB_MODEL_IDENTITY": model_identity,
        "LAB_SERVER_IDENTITY": server_identity,
    }


def _config_env(base: Mapping[str, str], config: Mapping[str, Any]) -> dict[str, str]:
    result = dict(base)
    result.update(
        {
            "LAB_MAIN_BATCH": str(config["main.batch"]),
            "LAB_MAIN_UBATCH": str(config["main.ubatch"]),
            "LAB_MAIN_CACHE_RAM_MIB": str(config["main.cacheRamMiB"]),
            "LAB_MAIN_CACHE_REUSE": str(config["main.cacheReuse"]),
            "LAB_MAIN_CUDA_GRAPH": str(config["main.cudaGraph"]),
            "LAB_MAIN_SWA_FULL": str(config["main.swaFull"]),
        }
    )
    return result


def _parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise LabFailure("runner_failed") from None


def _validate_compose(
    config: Any,
    run_id: str,
    expected_env: Mapping[str, str],
) -> None:
    if not isinstance(config, dict):
        raise LabFailure("lab_isolation_preflight_failed")
    services = config.get("services")
    networks = config.get("networks")
    volumes = config.get("volumes")
    if (
        not isinstance(services, dict)
        or set(services) != EXPECTED_SERVICES
        or not isinstance(networks, dict)
        or set(networks) != {"lab_internal"}
        or not isinstance(volumes, dict)
        or set(volumes) != {"main_llm_epoch_lab"}
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    expected_labels = {
        "ai.evelyn.owner": OWNER,
        "ai.evelyn.run-id": run_id,
    }
    project = _project_name(run_id)
    network = networks["lab_internal"]
    volume = volumes["main_llm_epoch_lab"]
    if (
        network
        != {
            "name": f"{project}_lab_internal",
            "ipam": {},
            "internal": True,
            "labels": expected_labels,
        }
        or volume
        != {
            "name": f"{project}_main_llm_epoch_lab",
            "labels": expected_labels,
        }
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    expected_mounts = {
        "main_llm_lab": [
            {
                "type": "bind",
                "source": expected_env["LAB_LLAMA_CPP_DIR"],
                "target": "/llama",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": expected_env["LAB_MAIN_LLM_BUILD_DIR"],
                "target": "/llama/build",
                "read_only": True,
            },
            {
                "type": "volume",
                "source": "main_llm_epoch_lab",
                "target": "/main-llm-epoch",
            },
        ],
        "main_llm_gateway_lab": [
            {
                "type": "volume",
                "source": "main_llm_epoch_lab",
                "target": "/main-llm-epoch",
                "read_only": True,
            }
        ],
        "tts_lab": [
            {
                "type": "bind",
                "source": expected_env["LAB_OMNIVOICE_PROFILES_DIR"],
                "target": "/home/ubuntu/app/profiles",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": expected_env["LAB_OMNIVOICE_HUB_DIR"],
                "target": "/home/ubuntu/.cache/huggingface/hub",
                "read_only": True,
            },
        ],
        "bot_api_lab": [
            {
                "type": "volume",
                "source": "main_llm_epoch_lab",
                "target": "/main-llm-epoch",
                "read_only": True,
            }
        ],
        "lab_harness": [
            {
                "type": "bind",
                "source": expected_env["LAB_TOOLS_DIR"],
                "target": "/lab/tools",
                "read_only": True,
            }
        ],
        "lab_focused_checks": [],
        "lab_privacy_checks": [],
    }
    expected_images = {
        "main_llm_lab": expected_env["LAB_MAIN_LLM_IMAGE"],
        "tts_lab": expected_env["LAB_TTS_IMAGE"],
        **{
            name: expected_env["LAB_BOT_API_IMAGE"]
            for name in EXPECTED_SERVICES - {"main_llm_lab", "tts_lab"}
        },
    }
    for name, service in services.items():
        if not isinstance(service, dict) or any(
            key in service
            for key in (
                "build",
                "ports",
                "extra_hosts",
                "network_mode",
                "privileged",
                "ipc",
                "pid",
                "devices",
            )
        ):
            raise LabFailure("lab_isolation_preflight_failed")
        if (
            service.get("image") != expected_images[name]
            or service.get("pull_policy") != "never"
            or service.get("read_only") is not True
            or "ALL" not in service.get("cap_drop", [])
            or "no-new-privileges:true" not in service.get("security_opt", [])
        ):
            raise LabFailure("lab_isolation_preflight_failed")
        labels = service.get("labels", {})
        if labels != expected_labels:
            raise LabFailure("lab_isolation_preflight_failed")
        attached = service.get("networks", {})
        if not isinstance(attached, dict) or set(attached) != {"lab_internal"}:
            raise LabFailure("lab_isolation_preflight_failed")
        tmpfs = service.get("tmpfs")
        if not isinstance(tmpfs, list) or not tmpfs:
            raise LabFailure("lab_isolation_preflight_failed")
        for mount in tmpfs:
            if not isinstance(mount, str):
                raise LabFailure("lab_isolation_preflight_failed")
            target, separator, raw_options = mount.partition(":")
            options = set(raw_options.split(","))
            if (
                separator != ":"
                or not target.startswith("/")
                or ".." in PurePosixPath(target).parts
                or not {"rw", "noexec", "nosuid", "nodev"}.issubset(options)
                or not any(value.startswith("size=") for value in options)
            ):
                raise LabFailure("lab_isolation_preflight_failed")
        if name == "tts_lab" and sum(
            mount.partition(":")[0] == "/home/ubuntu/.cache/flashinfer"
            for mount in tmpfs
        ) != 1:
            raise LabFailure("lab_isolation_preflight_failed")
        if service.get("volumes", []) != expected_mounts[name]:
            raise LabFailure("lab_isolation_preflight_failed")
    bot_environment = services["bot_api_lab"].get("environment")
    bot_healthcheck = services["bot_api_lab"].get("healthcheck")
    bot_health_test = (
        bot_healthcheck.get("test")
        if isinstance(bot_healthcheck, dict)
        else None
    )
    if (
        not isinstance(bot_environment, dict)
        or bot_environment.get("EVELYN_EXPECTED_SOURCE_REVISION")
        != expected_env["LAB_BOT_SOURCE_REVISION"]
        or str(bot_environment.get("MAIN_LLM_PORT")) != "9819"
        or str(bot_environment.get("FAST_CONTROL_CONTINUITY_ENABLED")).lower()
        != "true"
        or str(bot_environment.get("CROSS_SURFACE_CONTINUITY_ENABLED")).lower()
        != "false"
        or bot_health_test
        != ["CMD", "python", "-c", BOT_STRICT_HEALTHCHECK_SOURCE]
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    main_environment = services["main_llm_lab"].get("environment")
    if (
        not isinstance(main_environment, dict)
        or main_environment.get("MAIN_LLM_MODEL_IDENTITY")
        != expected_env["LAB_MODEL_IDENTITY"]
        or main_environment.get("MAIN_LLM_SERVER_IDENTITY")
        != expected_env["LAB_SERVER_IDENTITY"]
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    harness_environment = services["lab_harness"].get("environment")
    if (
        not isinstance(harness_environment, dict)
        or not {
            "LAB_CHAT_URL",
            "LAB_STATE_URL",
            "LAB_TTS_URL",
            "LAB_MAIN_DIRECT_URL",
            "LAB_EXECUTION_MODE",
            "LAB_CONDITION",
            "LAB_PHASE",
            "LAB_SAMPLE_COUNT",
            "LAB_EQUIVALENCE_KEY_HEX",
        }.issubset(harness_environment)
        or harness_environment.get("LAB_CHAT_URL")
        != "http://bot_api_lab:8798/api/control-page/chat-stream"
        or harness_environment.get("LAB_STATE_URL")
        != "http://bot_api_lab:8798/api/control-page/state"
        or harness_environment.get("LAB_TTS_URL")
        != "http://tts_lab:8880/v1/audio/speech"
        or harness_environment.get("LAB_MAIN_DIRECT_URL")
        != "http://main_llm_gateway_lab:9819/v1/chat/completions"
        or harness_environment.get("LAB_EXECUTION_MODE") != "e2e"
    ):
        raise LabFailure("lab_isolation_preflight_failed")


def _image_metadata(
    docker: Path,
    config_dir: Path,
    references: Sequence[str] = FIXED_IMAGES,
) -> list[dict[str, Any]]:
    raw = _run_command(
        _docker_command(docker, "image", "inspect", *references),
        config_dir=config_dir,
    )
    value = _parse_json(raw)
    if not isinstance(value, list) or len(value) != len(references):
        raise LabFailure("lab_identity_preflight_failed")
    return value


def _pinned_image_env(images: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if len(images) != len(FIXED_IMAGES):
        raise LabFailure("lab_identity_preflight_failed")
    image_ids = [item.get("Id") for item in images]
    if any(not isinstance(value, str) or HASH_ID.fullmatch(value) is None for value in image_ids):
        raise LabFailure("lab_identity_preflight_failed")
    bot_config = images[1].get("Config")
    bot_env = bot_config.get("Env") if isinstance(bot_config, dict) else None
    revisions = [
        value.removeprefix("EVELYN_IMAGE_SOURCE_REVISION=")
        for value in bot_env
        if isinstance(value, str) and value.startswith("EVELYN_IMAGE_SOURCE_REVISION=")
    ] if isinstance(bot_env, list) else []
    if len(revisions) != 1 or SOURCE_REVISION.fullmatch(revisions[0]) is None:
        raise LabFailure("lab_identity_preflight_failed")
    return {
        "LAB_MAIN_LLM_IMAGE": image_ids[0],
        "LAB_BOT_API_IMAGE": image_ids[1],
        "LAB_TTS_IMAGE": image_ids[2],
        "LAB_BOT_SOURCE_REVISION": revisions[0],
    }


def _gpu_identity(nvidia_smi: Path, config_dir: Path) -> str:
    raw = _run_command(
        (
            nvidia_smi,
            "--id=0",
            "--query-gpu=name,uuid,driver_version,memory.total,compute_cap,driver_model.current",
            "--format=csv,noheader,nounits",
        ),
        config_dir=config_dir,
    ).strip()
    if not raw or len(raw.splitlines()) != 1:
        raise LabFailure("lab_identity_preflight_failed")
    return _content_id({"schema": "evelyn.main-latency-gpu-identity.v1", "probe": raw})


def _gpu_telemetry(nvidia_smi: Path, config_dir: Path) -> tuple[str, float, float, float]:
    raw = _run_command(
        (
            nvidia_smi,
            "--id=0",
            "--query-gpu=driver_model.current,utilization.gpu,memory.free,memory.total",
            "--format=csv,noheader,nounits",
        ),
        config_dir=config_dir,
    ).strip()
    fields = [field.strip() for field in raw.split(",")]
    if len(fields) != 4:
        raise LabFailure("runner_failed")
    try:
        utilization, free_mib, total_mib = map(float, fields[1:])
    except ValueError:
        raise LabFailure("runner_failed") from None
    if (
        not fields[0]
        or not all(math.isfinite(value) for value in (utilization, free_mib, total_mib))
        or not 0 <= utilization <= 100
        or total_mib <= 0
        or not 0 <= free_mib <= total_mib
    ):
        raise LabFailure("runner_failed")
    return fields[0], utilization, free_mib, total_mib


def _running_gpu0_container_ids(docker: Path, config_dir: Path) -> set[str]:
    ids = _run_command(
        _docker_command(docker, "ps", "--no-trunc", "-q"),
        config_dir=config_dir,
        timeout=15.0,
    ).split()
    if any(CONTAINER_ID.fullmatch(value) is None for value in ids):
        raise LabFailure("environment_drift")
    if not ids:
        return set()
    owners: set[str] = set()
    for container_id in ids:
        raw = _run_command(
            _docker_command(
                docker,
                "inspect",
                "--format",
                GPU_DEVICE_INSPECT_FORMAT,
                container_id,
            ),
            config_dir=config_dir,
            timeout=15.0,
        )
        encoded_requests, separator, gpu1_marker = raw.partition("\n")
        if separator != "\n" or gpu1_marker.strip() not in {"", "1"}:
            raise LabFailure("environment_drift")
        requests = _parse_json(encoded_requests) or []
        if not isinstance(requests, list):
            raise LabFailure("environment_drift")
        gpu_requests: list[Mapping[str, Any]] = []
        for request in requests:
            if not isinstance(request, dict):
                raise LabFailure("environment_drift")
            capabilities = request.get("Capabilities") or []
            if not isinstance(capabilities, list):
                raise LabFailure("environment_drift")
            if any(
                isinstance(group, list) and "gpu" in group
                for group in capabilities
            ):
                gpu_requests.append(request)
        if not gpu_requests:
            continue
        device_ids: list[str] = []
        unbounded = False
        for request in gpu_requests:
            count = request.get("Count")
            raw_device_ids = request.get("DeviceIDs")
            if isinstance(count, bool) or not isinstance(count, int):
                raise LabFailure("environment_drift")
            if raw_device_ids is None:
                raw_device_ids = []
            if not isinstance(raw_device_ids, list) or any(
                not isinstance(value, str) or not value for value in raw_device_ids
            ):
                raise LabFailure("environment_drift")
            device_ids.extend(raw_device_ids)
            unbounded = unbounded or (count != 0 and not raw_device_ids)
        if "0" in device_ids or any(not value.isdigit() for value in device_ids):
            owners.add(container_id)
        elif unbounded and gpu1_marker.strip() != "1":
            owners.add(container_id)
    return owners


def _gpu_idle(nvidia_smi: Path, config_dir: Path) -> bool:
    observations: list[tuple[float, float, float]] = []
    for index in range(WDDM_IDLE_OBSERVATIONS):
        model, utilization, free_mib, total_mib = _gpu_telemetry(
            nvidia_smi, config_dir
        )
        if model.casefold() != "wddm":
            if observations:
                return False
            observations = []
            break
        observations.append((utilization, free_mib, total_mib))
        if index + 1 < WDDM_IDLE_OBSERVATIONS:
            time.sleep(0.2)
    if observations:
        return all(
            utilization <= WDDM_IDLE_MAX_UTILIZATION
            and free_mib / total_mib >= WDDM_IDLE_MIN_FREE_RATIO
            for utilization, free_mib, total_mib in observations
        )
    raw = _run_command(
        (
            nvidia_smi,
            "--id=0",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ),
        config_dir=config_dir,
    )
    return not raw.strip()


def _production_absent(docker: Path, config_dir: Path) -> bool:
    for name in ("evelyn-main-llm", "evelyn-tts", "evelyn-bot-api"):
        raw = _run_command(
            _docker_command(docker, "ps", "-q", "--filter", f"name=^/{name}$"),
            config_dir=config_dir,
        )
        if raw.strip():
            return False
    return True


def _actual_identities(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
    images: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    image_ids = [item.get("Id") for item in images]
    if any(not isinstance(value, str) or not value.startswith("sha256:") for value in image_ids):
        raise LabFailure("lab_identity_preflight_failed")
    bot_labels = images[1].get("Config", {}).get("Labels") or {}
    source = _content_id(
        {
            "schema": "evelyn.main-latency-source-identity.v1",
            "botImage": image_ids[1],
            "sourceRevision": bot_labels.get("org.opencontainers.image.revision", ""),
        }
    )
    harness_files = (
        Path(adapter.__file__).resolve(),
        Path(__file__).resolve(),
        Path(__file__).resolve().with_name("main_latency_lab_harness.py"),
        Path(__file__).resolve().with_name("main_latency_campaign_lock.py"),
        Path(__file__).resolve().with_name("main_latency_external_runner.py"),
        Path(__file__).resolve().with_name("main_latency_optimizer_loop.py"),
        Path(__file__).resolve().with_name("main_latency_host_lifecycle.py"),
        Path(__file__).resolve().with_name("main_latency_host_state_probe.py"),
        Path(__file__).resolve().with_name("main_latency_finalist_verifier.py"),
        Path(lab.__file__).resolve(),
        Path(optimizer.__file__).resolve(),
        Path(benchmark.__file__).resolve(),
        Path(__file__).resolve().with_name("main_latency_finalist_driver.py"),
        COMPOSE_FILE.resolve(),
    )
    harness = _content_id(
        {
            "schema": "evelyn.main-latency-harness-identity.v1",
            "files": {path.name: _file_hash(path) for path in harness_files},
        }
    )
    corpus = _content_id(
        {
            "schema": "evelyn.main-latency-corpus.v1",
            "prompt": benchmark.DEFAULT_PROMPT,
            "source": "direct_api",
        }
    )
    baseline = _content_id(
        {
            "schema": "evelyn.main-latency-baseline-identity.v1",
            "config": plan["baselineConfig"],
            "images": image_ids,
            "server": _server_build_identity(paths["main_build"]),
            "voiceProfile": _profile_tree_hash(paths["profiles"]),
            "runtimeTemplate": _runtime_identity(plan["baselineConfig"]),
        }
    )
    return {
        "baseline": baseline,
        "source": source,
        "model": _file_hash(paths["model"]),
        "gpu": _gpu_identity(nvidia_smi, config_dir),
        "corpus": corpus,
        "harness": harness,
    }


def _identity_probe_state(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
) -> tuple[dict[str, str], dict[str, str]]:
    if (
        plan.get("network") != "owned_internal_only_external_egress_disabled"
        or plan.get("filesystem") != "owned_ephemeral_content_free_only"
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    _run_command(
        _docker_command(docker, "version", "--format", "{{.Server.Version}}"),
        config_dir=config_dir,
    )
    images = _image_metadata(docker, config_dir)
    image_env = _pinned_image_env(images)
    project = _project_name(plan["runId"])
    base_env = _base_compose_env(plan, paths)
    base_env.update(image_env)
    rendered = _parse_json(
        _run_command(
            _compose_command(docker, project, "config", "--format", "json"),
            config_dir=config_dir,
            extra_env=base_env,
        )
    )
    _validate_compose(rendered, plan["runId"], base_env)
    for resource_kind, resource_name in (
        ("network", f"{project}_lab_internal"),
        ("volume", f"{project}_main_llm_epoch_lab"),
    ):
        if _run_command(
            _docker_command(
                docker,
                resource_kind,
                "ls",
                "-q",
                "--filter",
                f"name=^{resource_name}$",
            ),
            config_dir=config_dir,
            timeout=15.0,
        ).strip():
            raise LabFailure("lab_isolation_preflight_failed")
    existing = _run_command(
        _compose_command(docker, project, "ps", "-aq"),
        config_dir=config_dir,
        extra_env=base_env,
    )
    if (
        existing.strip()
        or not _production_absent(docker, config_dir)
        or _running_gpu0_container_ids(docker, config_dir)
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    if not _gpu_idle(nvidia_smi, config_dir):
        raise LabFailure("lab_gpu_idle_preflight_failed")
    _capture_gpu_baseline(nvidia_smi, config_dir, plan["runId"])
    return (
        _actual_identities(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
            paths=paths,
            images=images,
        ),
        image_env,
    )


def _identity_probe(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
) -> dict[str, str]:
    identities, _ = _identity_probe_state(
        plan,
        docker=docker,
        nvidia_smi=nvidia_smi,
        config_dir=config_dir,
        paths=paths,
    )
    return identities


def _preflight(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
) -> dict[str, Any]:
    try:
        if _identity_probe(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
            paths=paths,
        ) != plan["identities"]:
            raise LabFailure("lab_identity_preflight_failed")
        return {"ready": True, "code": "ready"}
    except LabFailure as exc:
        code = exc.code if exc.code in lab.LAB_PREFLIGHT_FAILURE_CODES else "lab_isolation_preflight_failed"
        return {"ready": False, "code": code}


def _discover(
    baseline: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
) -> dict[str, str]:
    pseudo_plan = {
        "runId": _content_id(
            {
                "schema": "evelyn.main-latency-discovery-run.v1",
                "baselineConfig": dict(baseline),
            }
        ),
        "baselineConfig": dict(baseline),
        "network": "owned_internal_only_external_egress_disabled",
        "filesystem": "owned_ephemeral_content_free_only",
    }
    return _identity_probe(
        pseudo_plan,
        docker=docker,
        nvidia_smi=nvidia_smi,
        config_dir=config_dir,
        paths=paths,
    )


def _nearest(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise LabFailure("runner_failed")
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(len(ordered) * percentile) - 1)]


def _metrics(
    warm: Sequence[Mapping[str, Any]],
    restart_ready: Sequence[Mapping[str, Any]],
    restart_startup_to_ready_ms: Sequence[float],
) -> dict[str, float]:
    def values(key: str) -> list[float]:
        return [float(sample[key]) for sample in warm]

    return {
        "postSttMainWriteP95Ms": _nearest(values("postSttMainWriteMs"), 0.95),
        "rawFirstTokenP95Ms": _nearest(values("rawFirstTokenMs"), 0.95),
        "rawToSafeSpeechP95Ms": _nearest(values("rawToSafeSpeechMs"), 0.95),
        "safePrefixCommitP95Ms": _nearest(values("safePrefixCommitMs"), 0.95),
        "ttsFirstPcmP95Ms": _nearest(values("ttsFirstPcmMs"), 0.95),
        "firstSentenceCommitP50Ms": statistics.median(values("firstSentenceCommitMs")),
        "firstSentenceCommitP95Ms": _nearest(values("firstSentenceCommitMs"), 0.95),
        "warmAnswerFirstPcmP50Ms": statistics.median(values("answerFirstPcmMs")),
        "warmAnswerFirstPcmP95Ms": _nearest(values("answerFirstPcmMs"), 0.95),
        "warmAnswerFirstPcmP99Ms": _nearest(values("answerFirstPcmMs"), 0.99),
        "restartReadyAnswerFirstPcmP95Ms": _nearest(
            [float(row["answerFirstPcmMs"]) for row in restart_ready], 0.95
        ),
        "restartStartupToReadyP95Ms": _nearest(
            restart_startup_to_ready_ms, 0.95
        ),
        "gpuMinFreeMiB": min(
            float(row["gpuFreeMiB"]) for row in (*warm, *restart_ready)
        ),
    }


_PRIVATE_TIMING_SAMPLE_FIELDS = {
    "promptEvalMs": ("llmPromptEvalMs", 30_000.0),
    "promptCacheHitRatio": ("llmPromptCacheHitRatio", 1.0),
    "promptTokensProcessed": ("llmPromptTokensProcessed", 1_000_000.0),
    "promptTokensCached": ("llmPromptTokensCached", 1_000_000.0),
    "promptTokensTotal": ("llmPromptTokensTotal", 1_000_000.0),
    "queueMs": ("llmQueueMs", 30_000.0),
    "routeMs": ("routeStageMs", 30_000.0),
    "contextMs": ("contextStageMs", 30_000.0),
    "rawFirstTokenMs": ("rawFirstTokenMs", 30_000.0),
    "safePrefixCommitMs": ("safePrefixCommitMs", 30_000.0),
    "answerFirstPcmMs": ("answerFirstPcmMs", 30_000.0),
}


def _private_timing_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise LabFailure("runner_failed")
    result: dict[str, Any] = {}
    for output_name, (sample_name, maximum) in _PRIVATE_TIMING_SAMPLE_FIELDS.items():
        values: list[float] = []
        for row in rows:
            if sample_name not in row:
                continue
            value = row[sample_name]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
            ):
                raise LabFailure("runner_failed")
            try:
                number = float(value)
            except (OverflowError, ValueError):
                raise LabFailure("runner_failed") from None
            if not math.isfinite(number) or not 0 <= number <= maximum:
                raise LabFailure("runner_failed")
            values.append(number)
        if values and len(values) != len(rows):
            raise LabFailure("runner_failed")
        if values:
            result[output_name] = {
                "sampleCount": len(values),
                "p50": statistics.median(values),
                "p95": _nearest(values, 0.95),
            }
    if not adapter.PRIVATE_TIMING_REQUIRED_METRICS.issubset(result):
        raise LabFailure("runner_failed")
    return result


def _private_timing_diagnostics(
    *,
    baseline_after_activation: Sequence[Mapping[str, Any]],
    baseline_resident: Sequence[Mapping[str, Any]],
    candidate_after_activation: Sequence[Mapping[str, Any]],
    candidate_resident: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    value = {
        "schema": adapter.PRIVATE_TIMING_SCHEMA,
        "baseline": {
            "afterActivation": _private_timing_summary(baseline_after_activation),
            "resident": _private_timing_summary(baseline_resident),
        },
        "candidate": {
            "afterActivation": _private_timing_summary(candidate_after_activation),
            "resident": _private_timing_summary(candidate_resident),
        },
    }
    try:
        return adapter.normalize_private_timing_diagnostics(value)
    except ValueError:
        raise LabFailure("runner_failed") from None


def _short_diagnostic_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    normalized = optimizer.MainLatencyConfig.from_mapping(config).to_dict()
    return {
        "runId": _content_id(
            {
                "schema": "evelyn.main-latency-short-diagnostic-run.v1",
                "config": normalized,
            }
        ),
        "baselineConfig": normalized,
        "network": "owned_internal_only_external_egress_disabled",
        "filesystem": "owned_ephemeral_content_free_only",
        "bounds": lab.RUN_PROFILES["screening"].bounds_dict(),
    }


def _short_diagnostic_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result = _private_timing_summary(rows)
    result["firstTokenMs"] = result.pop("rawFirstTokenMs")
    for output_name, sample_name, maximum in (
        ("ttsFirstPcmMs", "ttsFirstPcmMs", 30_000.0),
        ("predictedTokens", "llmPredictedTokens", 1_000_000.0),
        ("predictedMs", "llmPredictedMs", 30_000.0),
        ("predictedTokensPerSec", "llmPredictedTokensPerSec", 1_000_000.0),
    ):
        values: list[float] = []
        for row in rows:
            if sample_name not in row:
                continue
            value = row[sample_name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LabFailure("runner_failed")
            number = float(value)
            if not math.isfinite(number) or not 0 <= number <= maximum:
                raise LabFailure("runner_failed")
            values.append(number)
        if values and len(values) != len(rows):
            raise LabFailure("runner_failed")
        if values:
            result[output_name] = {
                "sampleCount": len(values),
                "p50": statistics.median(values),
                "p95": _nearest(values, 0.95),
            }
    return result


_SHORT_DIAGNOSTIC_ORDERED_FIELDS = {
    "promptEvalMs": "llmPromptEvalMs",
    "promptCacheHitRatio": "llmPromptCacheHitRatio",
    "promptTokensProcessed": "llmPromptTokensProcessed",
    "promptTokensCached": "llmPromptTokensCached",
    "promptTokensTotal": "llmPromptTokensTotal",
    "firstTokenMs": "rawFirstTokenMs",
    "safePrefixCommitMs": "safePrefixCommitMs",
    "ttsFirstPcmMs": "ttsFirstPcmMs",
    "answerFirstPcmMs": "answerFirstPcmMs",
}


def _short_diagnostic_ordered_samples(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project only bounded content-free timing fields, preserving sample order."""

    return [
        {
            "ordinal": ordinal,
            **{
                output_name: row[sample_name]
                for output_name, sample_name in _SHORT_DIAGNOSTIC_ORDERED_FIELDS.items()
            },
        }
        for ordinal, row in enumerate(rows, 1)
    ]


def _statistics(
    baseline_blocks: Sequence[Sequence[Mapping[str, Any]]],
    candidate_blocks: Sequence[Sequence[Mapping[str, Any]]],
    *,
    run_id: str,
) -> dict[str, Any]:
    blocks = len(baseline_blocks)
    if blocks < 1 or len(candidate_blocks) != blocks:
        raise LabFailure("runner_failed")
    base_blocks = [
        tuple(float(row["answerFirstPcmMs"]) for row in block)
        for block in baseline_blocks
    ]
    candidate_values = [
        tuple(float(row["answerFirstPcmMs"]) for row in block)
        for block in candidate_blocks
    ]
    point = _nearest([value for block in candidate_values for value in block], 0.95) - _nearest(
        [value for block in base_blocks for value in block], 0.95
    )
    rng = random.Random(int(run_id[7:23], 16))
    deltas: list[float] = []
    for _ in range(2000):
        indexes = [rng.randrange(blocks) for _ in range(blocks)]
        sampled_base = [value for index in indexes for value in base_blocks[index]]
        sampled_candidate = [value for index in indexes for value in candidate_values[index]]
        deltas.append(_nearest(sampled_candidate, 0.95) - _nearest(sampled_base, 0.95))
    # Standardize the same p95 estimand used by the point delta.  Using the
    # mean paired delta here can legitimately have the opposite sign from the
    # aggregate p95 delta, which makes an otherwise valid measured receipt
    # fail the contract only after the full campaign has completed.
    paired = [
        _nearest(candidate_values[index], 0.95)
        - _nearest(base_blocks[index], 0.95)
        for index in range(blocks)
    ]
    deviation = statistics.pstdev(paired)
    effect = point / deviation if deviation else (-100.0 if point < 0 else (100.0 if point > 0 else 0.0))
    low = min(_nearest(deltas, 0.025), point)
    high = max(_nearest(deltas, 0.975), point)
    return {
        "schema": lab.STATISTICS_SCHEMA,
        "method": "paired-bootstrap-abba-v1",
        "bootstrapReplicates": 2000,
        "confidenceLevel": 0.95,
        "warmAnswerFirstPcmP95DeltaCiLowMs": max(-30000.0, low),
        "warmAnswerFirstPcmP95DeltaCiHighMs": min(30000.0, high),
        "warmAnswerFirstPcmP95EffectSize": max(-100.0, min(100.0, effect)),
    }


def _evenly_spaced_indexes(total: int, target: int) -> tuple[int, ...]:
    if type(total) is not int or type(target) is not int or not 1 <= target <= total:
        raise LabFailure("runner_failed")
    if target == 1:
        return (0,)
    return tuple(index * (total - 1) // (target - 1) for index in range(target))


def _write_progress_checkpoint(
    config_dir: Path,
    plan: Mapping[str, Any],
    *,
    sequence: int,
    phase: str,
    completed_blocks: int,
    baseline_warm: Sequence[Mapping[str, Any]],
    candidate_warm: Sequence[Mapping[str, Any]],
    restart_eligible_baseline: int,
    restart_eligible_candidate: int,
    soak_turns: int,
) -> None:
    if phase not in {"warm", "soak", "measured"}:
        raise LabFailure("runner_failed")

    def latency(rows: Sequence[Mapping[str, Any]], percentile: float) -> float:
        if not rows:
            return 0.0
        return round(
            _nearest([float(row["answerFirstPcmMs"]) for row in rows], percentile),
            3,
        )

    payload = {
        "schema": PROGRESS_CHECKPOINT_SCHEMA,
        "owner": OWNER,
        "runId": plan["runId"],
        "candidateId": plan["candidateId"],
        "sequence": sequence,
        "phase": phase,
        "abbaBlocksCompleted": completed_blocks,
        "abbaBlocksTotal": int(plan["samples"]["abbaBlocks"]),
        "warmBaseline": len(baseline_warm),
        "warmCandidate": len(candidate_warm),
        "restartEligibleBaseline": restart_eligible_baseline,
        "restartEligibleCandidate": restart_eligible_candidate,
        "restartReadyTarget": int(plan["samples"]["restartReadyPerCondition"]),
        "soakTurns": soak_turns,
        "soakTarget": int(plan["samples"]["soakTurns"]),
        "baselineWarmAnswerFirstPcmP50Ms": latency(baseline_warm, 0.50),
        "baselineWarmAnswerFirstPcmP95Ms": latency(baseline_warm, 0.95),
        "candidateWarmAnswerFirstPcmP50Ms": latency(candidate_warm, 0.50),
        "candidateWarmAnswerFirstPcmP95Ms": latency(candidate_warm, 0.95),
    }
    destination = config_dir / PROGRESS_CHECKPOINT
    temporary = config_dir / f"{PROGRESS_CHECKPOINT}.tmp"
    try:
        if temporary.exists():
            temporary.unlink()
        with temporary.open("xb") as stream:
            stream.write(_canonical_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError:
        raise LabFailure("runner_failed") from None


def _owned_temp_paths(
    run_id: str | None,
    *,
    current_config_dir: Path,
) -> tuple[list[Path], int]:
    root = Path(tempfile.gettempdir()).absolute()
    current = os.path.normcase(os.path.abspath(current_config_dir))
    prefix = f"evelyn-latency-{run_id[7:15]}-" if run_id is not None else None
    removable: list[Path] = []
    observed = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        raise LabFailure("runner_failed") from None
    for entry in entries:
        if (
            OWNED_TEMP_NAME.fullmatch(entry.name) is None
            or (prefix is not None and not entry.name.startswith(prefix))
            or os.path.normcase(os.path.abspath(entry.path)) == current
        ):
            continue
        observed += 1
        path = Path(entry.path)
        if (
            entry.is_symlink()
            or _is_reparse(path)
            or not entry.is_dir(follow_symlinks=False)
        ):
            continue
        marker = path / OWNED_TEMP_MARKER
        if marker.is_symlink() or _is_reparse(marker) or not marker.is_file():
            continue
        try:
            if marker.stat().st_size > 512:
                continue
            marker_raw = marker.read_bytes()
            marker_value = json.loads(marker_raw.decode("ascii"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            len(marker_raw) <= 512
            and isinstance(marker_value, dict)
            and set(marker_value) == {"schema", "owner", "runId"}
            and marker_value["schema"] == "evelyn.main-latency-owned-temp.v1"
            and marker_value["owner"] == OWNER
            and isinstance(marker_value["runId"], str)
            and HASH_ID.fullmatch(marker_value["runId"]) is not None
            and (run_id is None or marker_value["runId"] == run_id)
        ):
            removable.append(path)
    return removable, observed


def _write_owned_temp_marker(config_dir: Path, run_id: str) -> None:
    if HASH_ID.fullmatch(run_id) is None:
        raise LabFailure("lab_isolation_preflight_failed")
    marker = config_dir / OWNED_TEMP_MARKER
    payload = {
        "schema": "evelyn.main-latency-owned-temp.v1",
        "owner": OWNER,
        "runId": run_id,
    }
    try:
        with marker.open("xb") as stream:
            stream.write(_canonical_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise LabFailure("lab_isolation_preflight_failed") from None


def _create_owned_temp_dir(run_id: str) -> Path:
    if HASH_ID.fullmatch(run_id) is None:
        raise LabFailure("lab_isolation_preflight_failed")
    root = Path(tempfile.gettempdir()).absolute()
    staging: Path | None = None
    try:
        staging = Path(
            tempfile.mkdtemp(prefix="evelyn-latency-staging-", dir=root)
        )
        _write_owned_temp_marker(staging, run_id)
        if os.name != "nt":
            directory_fd = os.open(
                staging,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        final = root / (
            f"evelyn-latency-{run_id[7:15]}-{secrets.token_urlsafe(12)}"
        )
        if OWNED_TEMP_NAME.fullmatch(final.name) is None or final.exists():
            raise LabFailure("lab_isolation_preflight_failed")
        os.replace(staging, final)
        return final
    except (LabFailure, OSError):
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise LabFailure("lab_isolation_preflight_failed") from None


def _write_gpu_baseline_marker(
    config_dir: Path,
    run_id: str,
    baseline: tuple[str, float, float],
) -> None:
    driver_model, free_mib, total_mib = baseline
    if (
        HASH_ID.fullmatch(run_id) is None
        or not driver_model
        or not all(math.isfinite(value) for value in (free_mib, total_mib))
        or total_mib <= 0
        or not 0 <= free_mib <= total_mib
    ):
        raise LabFailure("runner_failed")
    marker = config_dir / GPU_BASELINE_MARKER
    payload = _canonical_bytes(
        {
            "schema": "evelyn.main-latency-gpu0-baseline.v1",
            "owner": OWNER,
            "runId": run_id,
            "driverModel": driver_model.casefold(),
            "freeMiB": round(free_mib, 3),
            "totalMiB": round(total_mib, 3),
        }
    )
    try:
        if marker.exists():
            if marker.is_symlink() or _is_reparse(marker) or marker.read_bytes() != payload:
                raise LabFailure("runner_failed")
            return
        temporary = config_dir / f"{GPU_BASELINE_MARKER}.tmp"
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, marker)
    except LabFailure:
        raise
    except OSError:
        raise LabFailure("runner_failed") from None


def _read_gpu_baseline_marker(
    config_dir: Path,
    run_id: str | None,
) -> tuple[str, str, float, float] | None:
    marker = config_dir / GPU_BASELINE_MARKER
    if not marker.exists():
        return None
    try:
        if marker.is_symlink() or _is_reparse(marker) or not marker.is_file():
            return None
        raw = marker.read_bytes()
        if len(raw) > 512:
            return None
        value = json.loads(raw.decode("ascii"))
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "schema",
                "owner",
                "runId",
                "driverModel",
                "freeMiB",
                "totalMiB",
            }
            or value.get("schema") != "evelyn.main-latency-gpu0-baseline.v1"
            or value.get("owner") != OWNER
            or not isinstance(value.get("runId"), str)
            or HASH_ID.fullmatch(value["runId"]) is None
            or (run_id is not None and value["runId"] != run_id)
            or not isinstance(value.get("driverModel"), str)
            or not value["driverModel"]
            or isinstance(value.get("freeMiB"), bool)
            or not isinstance(value.get("freeMiB"), (int, float))
            or isinstance(value.get("totalMiB"), bool)
            or not isinstance(value.get("totalMiB"), (int, float))
        ):
            return None
        free_mib = float(value["freeMiB"])
        total_mib = float(value["totalMiB"])
        if (
            not all(math.isfinite(number) for number in (free_mib, total_mib))
            or total_mib <= 0
            or not 0 <= free_mib <= total_mib
        ):
            return None
        return value["runId"], value["driverModel"].casefold(), free_mib, total_mib
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, OverflowError, ValueError):
        return None


def _capture_gpu_baseline(
    nvidia_smi: Path,
    config_dir: Path,
    run_id: str,
) -> tuple[str, float, float]:
    readings: list[tuple[str, float, float]] = []
    for index in range(WDDM_IDLE_OBSERVATIONS):
        model, utilization, free_mib, total_mib = _gpu_telemetry(
            nvidia_smi, config_dir
        )
        if utilization > WDDM_IDLE_MAX_UTILIZATION:
            raise LabFailure("lab_isolation_preflight_failed")
        readings.append((model.casefold(), free_mib, total_mib))
        if index + 1 < WDDM_IDLE_OBSERVATIONS:
            time.sleep(0.2)
    models = {reading[0] for reading in readings}
    totals = [reading[2] for reading in readings]
    frees = [reading[1] for reading in readings]
    if (
        len(models) != 1
        or max(totals) - min(totals) > 1.0
        or max(frees) - min(frees) > WDDM_BASELINE_FREE_TOLERANCE_MIB
    ):
        raise LabFailure("lab_isolation_preflight_failed")
    baseline = readings[0][0], min(frees), statistics.median(totals)
    _write_gpu_baseline_marker(config_dir, run_id, baseline)
    return baseline


def _cleanup_gpu_baseline(
    target_run_id: str | None,
    *,
    current_config_dir: Path,
    current_run_id: str,
) -> tuple[str, float, float] | None:
    candidates = [current_config_dir]
    prior, _ = _owned_temp_paths(
        target_run_id,
        current_config_dir=current_config_dir,
    )
    candidates.extend(prior)
    readings = [
        value
        for path in candidates
        if (value := _read_gpu_baseline_marker(path, target_run_id)) is not None
    ]
    if not readings:
        return None
    models = {value[1] for value in readings}
    totals = [value[3] for value in readings]
    if len(models) != 1 or max(totals) - min(totals) > 1.0:
        raise LabFailure("runner_failed")
    baseline = (
        readings[0][1],
        max(value[2] for value in readings),
        statistics.median(totals),
    )
    _write_gpu_baseline_marker(current_config_dir, current_run_id, baseline)
    return baseline


def _gpu_allocation_count(
    nvidia_smi: Path,
    *,
    config_dir: Path,
    owned_pids: set[int],
) -> int:
    if not owned_pids:
        return 0
    raw = _run_command(
        (
            nvidia_smi,
            "--id=0",
            "--query-compute-apps=pid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ),
        config_dir=config_dir,
        timeout=10.0,
    )
    rows = [line.strip() for line in raw.splitlines() if line.strip()]
    observed: set[int] = set()
    for row in rows:
        parts = [part.strip() for part in row.split(",")]
        if (
            len(parts) != 2
            or not parts[0].isdigit()
            or not parts[1].isdigit()
            or int(parts[0]) <= 0
        ):
            raise LabFailure("runner_failed")
        observed.add(int(parts[0]))
    return len(observed.intersection(owned_pids))


def _cleanup_container_pids(
    containers: Sequence[str],
    *,
    docker: Path,
    config_dir: Path,
) -> set[int]:
    result: set[int] = set()
    for container_id in containers:
        raw = _run_command(
            _docker_command(docker, "top", container_id, "-eo", "pid"),
            config_dir=config_dir,
            timeout=10.0,
        )
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) < 2 or lines[0].casefold() != "pid":
            raise LabFailure("runner_failed")
        for line in lines[1:]:
            if not line.isdigit() or int(line) <= 0:
                raise LabFailure("runner_failed")
            result.add(int(line))
    return result


def _cleanup(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    all_runs: bool = False,
) -> dict[str, Any]:
    target_run_id = None if all_runs else plan["runId"]
    filters = ["--filter", f"label=ai.evelyn.owner={OWNER}"]
    if target_run_id is not None:
        filters.extend(("--filter", f"label=ai.evelyn.run-id={target_run_id}"))

    def owned(kind: str) -> list[str]:
        parts = {
            "container": ("ps", "-aq", "--no-trunc"),
            "running_container": ("ps", "-q", "--no-trunc"),
            "volume": ("volume", "ls", "-q"),
            "network": ("network", "ls", "-q"),
        }[kind]
        values = _run_command(
            _docker_command(docker, *parts, *filters),
            config_dir=config_dir,
            timeout=10.0,
        ).split()
        pattern = VOLUME_NAME if kind == "volume" else CONTAINER_ID
        if any(pattern.fullmatch(value) is None for value in values):
            raise LabFailure("runner_failed")
        return values

    stable_zero = 0
    counts = (1, 1, 1)
    known_owned_pids: set[int] = set()
    gpu_baseline = _cleanup_gpu_baseline(
        target_run_id,
        current_config_dir=config_dir,
        current_run_id=plan["runId"],
    )
    driver_model = gpu_baseline[0] if gpu_baseline is not None else None
    saw_owned_runtime_resources = False
    round_index = 0
    tail_rounds_remaining = CLEANUP_STABLE_ZERO_OBSERVATIONS - 1
    while round_index < CLEANUP_MAX_ROUNDS or (
        0 < stable_zero < CLEANUP_STABLE_ZERO_OBSERVATIONS
        and tail_rounds_remaining > 0
    ):
        in_stability_tail = round_index >= CLEANUP_MAX_ROUNDS
        if in_stability_tail:
            tail_rounds_remaining -= 1
        current_model, utilization, free_mib, total_mib = _gpu_telemetry(
            nvidia_smi, config_dir
        )
        current_model = current_model.casefold()
        if driver_model is not None and current_model != driver_model:
            raise LabFailure("runner_failed")
        if driver_model is None:
            driver_model = current_model
        wddm_cleanup = driver_model == "wddm"
        containers = owned("container")
        running_containers = owned("running_container")
        if not set(running_containers).issubset(containers):
            raise LabFailure("runner_failed")
        current_owned_pids = (
            set()
            if wddm_cleanup
            else _cleanup_container_pids(
                running_containers,
                docker=docker,
                config_dir=config_dir,
            )
        )
        known_owned_pids.update(current_owned_pids)
        volumes = owned("volume")
        networks = owned("network")
        saw_owned_runtime_resources = saw_owned_runtime_resources or bool(
            containers or volumes or networks
        )
        temp_paths, temp_count = _owned_temp_paths(
            target_run_id,
            current_config_dir=config_dir,
        )
        if wddm_cleanup:
            gpu_allocations = (
                len(
                    set(running_containers).intersection(
                        _running_gpu0_container_ids(docker, config_dir)
                    )
                )
                if running_containers
                else 0
            )
            if not running_containers and gpu_allocations == 0:
                if (
                    gpu_baseline is None
                    and all_runs
                    and not saw_owned_runtime_resources
                    and not containers
                    and not volumes
                    and not networks
                    and temp_count == 0
                ):
                    gpu_baseline = (driver_model, free_mib, total_mib)
                    _write_gpu_baseline_marker(
                        config_dir, plan["runId"], gpu_baseline
                    )
        else:
            gpu_allocations = _gpu_allocation_count(
                nvidia_smi,
                config_dir=config_dir,
                owned_pids=known_owned_pids,
            )
        counts = (
            min(8, len(containers)),
            min(4, gpu_allocations),
            min(64, len(volumes) + len(networks) + temp_count),
        )
        if not any(counts):
            stable_zero += 1
            if stable_zero >= CLEANUP_STABLE_ZERO_OBSERVATIONS:
                break
        else:
            stable_zero = 0
            if containers:
                _run_command(
                    _docker_command(docker, "rm", "-f", *containers),
                    config_dir=config_dir,
                    timeout=20.0,
                    check=False,
                )
            if volumes:
                _run_command(
                    _docker_command(docker, "volume", "rm", "-f", *volumes),
                    config_dir=config_dir,
                    timeout=20.0,
                    check=False,
                )
            if networks:
                _run_command(
                    _docker_command(docker, "network", "rm", *networks),
                    config_dir=config_dir,
                    timeout=20.0,
                    check=False,
                )
            for path in temp_paths:
                try:
                    shutil.rmtree(path)
                except OSError:
                    pass
        round_index += 1
        if round_index < CLEANUP_MAX_ROUNDS or (
            0 < stable_zero < CLEANUP_STABLE_ZERO_OBSERVATIONS
            and tail_rounds_remaining > 0
        ):
            time.sleep(CLEANUP_STABLE_INTERVAL_S)

    clean = stable_zero >= CLEANUP_STABLE_ZERO_OBSERVATIONS
    return {
        "schema": lab.CLEANUP_SCHEMA,
        "runId": plan["runId"],
        "owner": OWNER,
        "status": "clean" if clean else "cleanup_required",
        "remainingProcesses": counts[0],
        "remainingGpuAllocations": counts[1],
        "remainingArtifacts": counts[2],
    }


def _harness_batch(
    plan: Mapping[str, Any],
    *,
    condition: str,
    phase: str,
    count: int,
    key_hex: str,
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
    deadline: float,
) -> tuple[list[dict[str, Any]], int, int, float | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LabFailure("timed_out")
    env = dict(compose_env)
    env.update(
        {
            "LAB_CONDITION": condition,
            "LAB_PHASE": phase,
            "LAB_SAMPLE_COUNT": str(count),
            "LAB_EQUIVALENCE_KEY_HEX": key_hex,
        }
    )
    raw = _run_command(
        _compose_command(docker, _project_name(plan["runId"]), "run", "--rm", "-T", "lab_harness"),
        config_dir=config_dir,
        extra_env=env,
        timeout=min(remaining, count * 185.0 + 180.0),
    )
    value = _parse_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema") != "evelyn.main-latency-lab-batch.v1"
        or value.get("condition") != condition
        or value.get("phase") != phase
        or value.get("sampleCount") != count
        or value.get("externalDefaultRoute") is not False
        or type(value.get("cacheProofChecks")) is not int
        or not 1 <= value["cacheProofChecks"] <= 10_000
        or type(value.get("cacheProofFailures")) is not int
        or not 0 <= value["cacheProofFailures"] <= value["cacheProofChecks"]
        or not isinstance(value.get("samples"), list)
        or len(value["samples"]) != count
    ):
        raise LabFailure("runner_failed")
    startup_to_ready_ms = value.get("startupToReadyMs")
    if startup_to_ready_ms is not None:
        raise LabFailure("runner_failed")
    return (
        value["samples"],
        value["cacheProofChecks"],
        value["cacheProofFailures"],
        startup_to_ready_ms,
    )


def _tts_harness_warmup(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
    deadline: float,
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LabFailure("timed_out")
    env = dict(compose_env)
    env["LAB_EXECUTION_MODE"] = "tts_warmup"
    raw = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "run",
            "--rm",
            "-T",
            "lab_harness",
        ),
        config_dir=config_dir,
        extra_env=env,
        timeout=min(remaining, 90.0),
    )
    value = _parse_json(raw)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "externalDefaultRoute",
            "requestCount",
            "fullDrain",
            "audioPresent",
        }
        or value.get("schema") != "evelyn.main-latency-tts-warmup-proof.v1"
        or value.get("externalDefaultRoute") is not False
        or type(value.get("requestCount")) is not int
        or value.get("requestCount") != 1
        or value.get("fullDrain") is not True
        or value.get("audioPresent") is not True
    ):
        raise LabFailure("runner_failed")


_DIRECT_SAMPLE_FIELDS = {
    "payloadProof",
    "rawFirstTokenMs",
    "promptEvalMs",
    "promptCacheHitRatio",
    "promptTokensProcessed",
    "promptTokensCached",
    "promptTokensTotal",
}


def _direct_harness_sample(
    plan: Mapping[str, Any],
    *,
    condition: str,
    phase: str,
    key_hex: str,
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
    deadline: float,
) -> tuple[dict[str, Any], int, int]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LabFailure("timed_out")
    env = dict(compose_env)
    env.update(
        {
            "LAB_EXECUTION_MODE": "direct_backend",
            "LAB_CONDITION": condition,
            "LAB_PHASE": phase,
            "LAB_SAMPLE_COUNT": "1",
            "LAB_EQUIVALENCE_KEY_HEX": key_hex,
        }
    )
    raw = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "run",
            "--rm",
            "-T",
            "lab_harness",
        ),
        config_dir=config_dir,
        extra_env=env,
        timeout=min(remaining, 365.0),
    )
    value = _parse_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema")
        != "evelyn.main-latency-direct-diagnostic-batch.v1"
        or value.get("condition") != condition
        or value.get("phase") != phase
        or value.get("sampleCount") != 1
        or value.get("externalDefaultRoute") is not False
        or type(value.get("cacheProofChecks")) is not int
        or not 1 <= value["cacheProofChecks"] <= 10
        or type(value.get("cacheProofFailures")) is not int
        or not 0
        <= value["cacheProofFailures"]
        <= value["cacheProofChecks"]
        or not isinstance(value.get("samples"), list)
        or len(value["samples"]) != 1
        or not isinstance(value["samples"][0], dict)
        or set(value["samples"][0]) != _DIRECT_SAMPLE_FIELDS
    ):
        raise LabFailure("runner_failed")
    sample = value["samples"][0]
    proof = sample.get("payloadProof")
    if not isinstance(proof, str) or re.fullmatch(r"[0-9a-f]{64}", proof) is None:
        raise LabFailure("runner_failed")
    for name, maximum, integral in (
        ("rawFirstTokenMs", 30_000.0, False),
        ("promptEvalMs", 30_000.0, False),
        ("promptCacheHitRatio", 1.0, False),
        ("promptTokensProcessed", 1_000_000.0, True),
        ("promptTokensCached", 1_000_000.0, True),
        ("promptTokensTotal", 1_000_000.0, True),
    ):
        raw_number = sample.get(name)
        if (
            isinstance(raw_number, bool)
            or not isinstance(raw_number, (int, float))
        ):
            raise LabFailure("runner_failed")
        number = float(raw_number)
        if (
            not math.isfinite(number)
            or not 0 <= number <= maximum
            or (integral and not number.is_integer())
        ):
            raise LabFailure("runner_failed")
    if (
        int(sample["promptTokensProcessed"])
        + int(sample["promptTokensCached"])
        != int(sample["promptTokensTotal"])
        or int(sample["promptTokensTotal"]) < 1
        or not math.isclose(
            float(sample["promptCacheHitRatio"]),
            int(sample["promptTokensCached"])
            / int(sample["promptTokensTotal"]),
            rel_tol=0.0,
            abs_tol=0.000051,
        )
    ):
        raise LabFailure("runner_failed")
    return (
        dict(sample),
        value["cacheProofChecks"],
        value["cacheProofFailures"],
    )


def _run_source_checks(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
    deadline: float,
) -> dict[str, int]:
    for service in ("lab_focused_checks", "lab_privacy_checks"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LabFailure("timed_out")
        _run_command(
            _compose_command(
                docker,
                _project_name(plan["runId"]),
                "run",
                "--rm",
                "-T",
                service,
            ),
            config_dir=config_dir,
            extra_env=compose_env,
            timeout=min(remaining, 300.0),
        )
    return {"focusedTestFailures": 0, "privacyTestFailures": 0}


def _activation_state(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
) -> tuple[str, str, str, str]:
    raw = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "exec",
            "-T",
            "main_llm_lab",
            "sh",
            "-c",
            "cat /main-llm-epoch/epoch /main-llm-epoch/identity /main-llm-epoch/server-identity /main-llm-epoch/runtime-template-identity",
        ),
        config_dir=config_dir,
        extra_env=compose_env,
        timeout=15.0,
    )
    lines = [line.strip() for line in raw.splitlines()]
    if (
        len(lines) != 4
        or EPOCH_ID.fullmatch(lines[0]) is None
        or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in lines[1:])
    ):
        raise LabFailure("environment_drift")
    return lines[0], lines[1], lines[2], lines[3]


def _activate(
    plan: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    initial: bool,
    docker: Path,
    config_dir: Path,
    base_env: Mapping[str, str],
    deadline: float,
    readiness_ms_out: list[float] | None = None,
) -> dict[str, str]:
    env = _config_env(base_env, config)
    project = _project_name(plan["runId"])
    previous_epoch = None
    if not initial:
        previous_epoch = _activation_state(
            plan,
            docker=docker,
            config_dir=config_dir,
            compose_env=env,
        )[0]
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LabFailure("timed_out")
    started = time.monotonic()
    parts = (
        ("up", "-d", "--wait", "main_llm_lab", "main_llm_gateway_lab", "tts_lab", "bot_api_lab")
        if initial
        else (
            "up",
            "-d",
            "--wait",
            "--force-recreate",
            "main_llm_lab",
            "main_llm_gateway_lab",
            "bot_api_lab",
        )
    )
    _run_command(
        _compose_command(docker, project, *parts),
        config_dir=config_dir,
        extra_env=env,
        timeout=min(remaining, 900.0),
    )
    readiness_ms = (time.monotonic() - started) * 1000.0
    epoch, model_identity, server_identity, runtime_identity = _activation_state(
        plan,
        docker=docker,
        config_dir=config_dir,
        compose_env=env,
    )
    if (
        (previous_epoch is not None and epoch == previous_epoch)
        or model_identity != env.get("LAB_MODEL_IDENTITY")
        or server_identity != env.get("LAB_SERVER_IDENTITY")
        or runtime_identity != _runtime_identity(config)[7:]
    ):
        raise LabFailure("environment_drift")
    if readiness_ms_out is not None:
        if readiness_ms_out or not math.isfinite(readiness_ms) or readiness_ms <= 0:
            raise LabFailure("runner_failed")
        readiness_ms_out.append(readiness_ms)
    return env


def _reset_bot(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
    deadline: float,
) -> None:
    """Reset per-session Bot state without reloading the resident Main model."""

    previous_state = _activation_state(
        plan,
        docker=docker,
        config_dir=config_dir,
        compose_env=compose_env,
    )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LabFailure("timed_out")
    _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "up",
            "-d",
            "--wait",
            "--no-deps",
            "--force-recreate",
            "bot_api_lab",
        ),
        config_dir=config_dir,
        extra_env=compose_env,
        timeout=min(remaining, 180.0),
    )
    if (
        _activation_state(
            plan,
            docker=docker,
            config_dir=config_dir,
            compose_env=compose_env,
        )
        != previous_state
    ):
        raise LabFailure("environment_drift")


def _container_observation(
    plan: Mapping[str, Any], docker: Path, config_dir: Path, compose_env: Mapping[str, str]
) -> tuple[int, int]:
    ids = _run_command(
        _compose_command(docker, _project_name(plan["runId"]), "ps", "-aq"),
        config_dir=config_dir,
        extra_env=compose_env,
    ).split()
    if not ids or any(CONTAINER_ID.fullmatch(value) is None for value in ids):
        raise LabFailure("runner_failed")
    states_raw = _run_command(
        _docker_command(docker, "inspect", "--format", "{{json .State}}", *ids),
        config_dir=config_dir,
    )
    try:
        states = [json.loads(line) for line in states_raw.splitlines() if line.strip()]
    except json.JSONDecodeError:
        raise LabFailure("runner_failed") from None
    if len(states) != len(ids) or any(
        not isinstance(state, dict)
        or type(state.get("OOMKilled")) is not bool
        or type(state.get("Running")) is not bool
        for state in states
    ):
        raise LabFailure("runner_failed")
    oom_count = sum(int(state["OOMKilled"]) for state in states)
    if oom_count or any(not state["Running"] for state in states):
        raise LabFailure("candidate_failed")
    total_bytes = 0
    peak_probe = (
        "if [ -r /sys/fs/cgroup/memory.peak ]; then cat /sys/fs/cgroup/memory.peak; "
        "elif [ -r /sys/fs/cgroup/memory/memory.max_usage_in_bytes ]; then "
        "cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes; else exit 1; fi"
    )
    for container_id in ids:
        raw = _run_command(
            _docker_command(docker, "exec", container_id, "sh", "-c", peak_probe),
            config_dir=config_dir,
            timeout=15.0,
        ).strip()
        if not raw.isdigit():
            raise LabFailure("runner_failed")
        total_bytes += int(raw)
    return math.ceil(total_bytes / (1024 * 1024)), oom_count


def _gpu_boundary_observation(
    plan: Mapping[str, Any],
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
) -> tuple[float, float]:
    """Fail closed unless every GPU compute PID belongs to this run's containers."""

    filters = (
        "--filter",
        f"label=ai.evelyn.owner={OWNER}",
        "--filter",
        f"label=ai.evelyn.run-id={plan['runId']}",
    )
    ids = _run_command(
        _docker_command(docker, "ps", "--no-trunc", "-q", *filters),
        config_dir=config_dir,
        timeout=15.0,
    ).split()
    if len(ids) != 4 or any(CONTAINER_ID.fullmatch(value) is None for value in ids):
        raise LabFailure("environment_drift")
    service_ids = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "ps",
            "-q",
            "main_llm_lab",
            "main_llm_gateway_lab",
            "tts_lab",
            "bot_api_lab",
        ),
        config_dir=config_dir,
        extra_env=compose_env,
        timeout=15.0,
    ).split()
    if len(service_ids) != 4 or set(service_ids) != set(ids):
        raise LabFailure("environment_drift")
    main_ids = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "ps",
            "-q",
            "main_llm_lab",
        ),
        config_dir=config_dir,
        extra_env=compose_env,
        timeout=15.0,
    ).split()
    if len(main_ids) != 1 or main_ids[0] not in ids:
        raise LabFailure("environment_drift")
    tts_ids = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "ps",
            "-q",
            "tts_lab",
        ),
        config_dir=config_dir,
        extra_env=compose_env,
        timeout=15.0,
    ).split()
    if len(tts_ids) != 1 or tts_ids[0] not in ids or tts_ids == main_ids:
        raise LabFailure("environment_drift")
    model, utilization, free_mib, _total_mib = _gpu_telemetry(
        nvidia_smi, config_dir
    )
    if model.casefold() == "wddm":
        if _running_gpu0_container_ids(docker, config_dir) != {
            main_ids[0],
            tts_ids[0],
        }:
            raise LabFailure("environment_drift")
    else:
        owned_pids: set[int] = set()
        for container_id in ids:
            raw = _run_command(
                _docker_command(docker, "top", container_id, "-eo", "pid"),
                config_dir=config_dir,
                timeout=15.0,
            )
            lines = [line.strip() for line in raw.splitlines() if line.strip()]
            if len(lines) < 2 or lines[0].casefold() != "pid":
                raise LabFailure("runner_failed")
            for line in lines[1:]:
                if not line.isdigit() or int(line) <= 0:
                    raise LabFailure("runner_failed")
                owned_pids.add(int(line))
        processes = _run_command(
            _docker_command(
                docker,
                "exec",
                main_ids[0],
                "nvidia-smi",
                "--id=0",
                "--query-compute-apps=pid,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ),
            config_dir=config_dir,
            timeout=15.0,
        )
        compute_pids: set[int] = set()
        for line in processes.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 2 or not fields[0].isdigit():
                raise LabFailure("runner_failed")
            try:
                used_mib = float(fields[1])
            except ValueError:
                raise LabFailure("runner_failed") from None
            if int(fields[0]) <= 0 or not math.isfinite(used_mib) or used_mib < 0:
                raise LabFailure("runner_failed")
            compute_pids.add(int(fields[0]))
        if not compute_pids or not compute_pids.issubset(owned_pids):
            raise LabFailure("environment_drift")
    if free_mib < float(plan["bounds"]["minGpuFreeMiB"]):
        raise LabFailure("candidate_failed")
    return utilization, free_mib


def _epoch_artifact_bytes(
    plan: Mapping[str, Any],
    docker: Path,
    config_dir: Path,
    compose_env: Mapping[str, str],
) -> int:
    ids = _run_command(
        _compose_command(
            docker,
            _project_name(plan["runId"]),
            "ps",
            "-q",
            "main_llm_lab",
        ),
        config_dir=config_dir,
        extra_env=compose_env,
        timeout=15.0,
    ).split()
    if len(ids) != 1 or CONTAINER_ID.fullmatch(ids[0]) is None:
        raise LabFailure("runner_failed")
    raw = _run_command(
        _docker_command(
            docker,
            "exec",
            ids[0],
            "find",
            "/main-llm-epoch",
            "-xdev",
            "-type",
            "f",
            "-printf",
            "%s\\n",
        ),
        config_dir=config_dir,
        timeout=15.0,
    )
    sizes = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(sizes) > 16 or any(not value.isdigit() for value in sizes):
        raise LabFailure("runner_failed")
    return sum(map(int, sizes))


_DIRECT_PUBLIC_FIELDS = {
    "promptEvalMs",
    "promptCacheHitRatio",
    "promptTokensProcessed",
    "promptTokensCached",
    "promptTokensTotal",
}


def _public_cleanup(cleanup: Mapping[str, Any]) -> dict[str, Any]:
    status = cleanup.get("status")
    counts = tuple(
        cleanup.get(name)
        for name in (
            "remainingProcesses",
            "remainingGpuAllocations",
            "remainingArtifacts",
        )
    )
    if (
        type(status) is not str
        or status not in {"clean", "cleanup_required"}
        or any(type(value) is not int or value < 0 for value in counts)
        or (status == "clean") != (sum(counts) == 0)
    ):
        return {
            "status": "cleanup_required",
            "remainingProcesses": 1,
            "remainingGpuAllocations": 1,
            "remainingArtifacts": 1,
        }
    return {
        "status": status,
        "remainingProcesses": counts[0],
        "remainingGpuAllocations": counts[1],
        "remainingArtifacts": counts[2],
    }


def _sample_validity_failures(rows: Sequence[Mapping[str, Any]]) -> int:
    fields = (
        "externalInterference",
        "safetyFailure",
        "qualityFailure",
        "orderViolation",
        "staleSpeech",
        "unsafePrefix",
        "errorEvents",
    )
    return sum(
        int(any(int(row.get(field, 0)) != 0 for field in fields))
        for row in rows
    )


def _direct_public_sample(sample: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "firstTokenMs": sample["rawFirstTokenMs"],
        **{name: sample[name] for name in _DIRECT_PUBLIC_FIELDS},
    }


def _run_short_diagnostic(
    config: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
) -> dict[str, Any]:
    """Measure one config without producing promotion or public-gate evidence."""

    plan = _short_diagnostic_plan(config)
    started = time.monotonic()
    deadline = started + SHORT_DIAGNOSTIC_MAX_RUNTIME_S
    groups: dict[str, list[dict[str, Any]]] = {
        "firstAfterWarmup": [],
        "resident": [],
        "afterIdle": [],
    }
    phases = ("cold", "capture", "resident", "afterIdle")
    direct: dict[str, dict[str, dict[str, Any]]] = {
        control: {phase: {} for phase in phases}
        for control in ("graphsOff", "graphsOn")
    }
    direct_internal: dict[str, dict[str, dict[str, Any]]] = {
        control: {} for control in direct
    }
    identity_state: dict[str, str] = {}
    cache_proof_checks = 0
    cache_proof_failures = 0
    sample_validity_failures = 0
    peak_ram = 0
    gpu_min_free_mib = math.inf
    gpu_max_utilization = 0.0
    status = "runner_failed"
    cleanup: Mapping[str, Any]

    try:
        identity_state, image_env = _identity_probe_state(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
            paths=paths,
        )
        plan["identities"] = identity_state
        base_env = _base_compose_env(plan, paths)
        base_env.update(image_env)
        _activate(
            plan,
            config=plan["baselineConfig"],
            initial=True,
            docker=docker,
            config_dir=config_dir,
            base_env=base_env,
            deadline=deadline,
        )
        key_hex = secrets.token_hex(32)
        graph_configs = {
            "graphsOff": {
                **plan["baselineConfig"],
                "main.cudaGraph": 0,
            },
            "graphsOn": {
                **plan["baselineConfig"],
                "main.cudaGraph": 1,
            },
        }
        for control, condition in (
            ("graphsOff", "baseline"),
            ("graphsOn", "candidate"),
        ):
            active_env = _activate(
                plan,
                config=graph_configs[control],
                initial=False,
                docker=docker,
                config_dir=config_dir,
                base_env=base_env,
                deadline=deadline,
            )
            for phase in phases:
                if phase == "afterIdle":
                    time.sleep(SHORT_DIAGNOSTIC_IDLE_SECONDS)
                sample, proof_checks, proof_failures = _direct_harness_sample(
                    plan,
                    condition=condition,
                    phase=phase,
                    key_hex=key_hex,
                    docker=docker,
                    config_dir=config_dir,
                    compose_env=active_env,
                    deadline=deadline,
                )
                direct_internal[control][phase] = sample
                direct[control][phase] = _direct_public_sample(sample)
                cache_proof_checks += proof_checks
                cache_proof_failures += proof_failures
            utilization, free_mib = _gpu_boundary_observation(
                plan,
                docker,
                nvidia_smi,
                config_dir,
                active_env,
            )
            gpu_min_free_mib = min(gpu_min_free_mib, free_mib)
            gpu_max_utilization = max(gpu_max_utilization, utilization)
            observed_ram, observed_oom = _container_observation(
                plan, docker, config_dir, active_env
            )
            if observed_oom:
                raise LabFailure("candidate_failed")
            peak_ram = max(peak_ram, observed_ram)

        # Keep the production Fast->Main->TTS sequence visible but separate
        # from the exact-payload backend observation above.
        active_env = _activate(
            plan,
            config=plan["baselineConfig"],
            initial=False,
            docker=docker,
            config_dir=config_dir,
            base_env=base_env,
            deadline=deadline,
        )
        _tts_harness_warmup(
            plan,
            docker=docker,
            config_dir=config_dir,
            compose_env=active_env,
            deadline=deadline,
        )
        for cohort, count in (
            ("firstAfterWarmup", 1),
            ("resident", SHORT_DIAGNOSTIC_RESIDENT_SAMPLES),
            ("afterIdle", 1),
        ):
            if cohort == "afterIdle":
                time.sleep(SHORT_DIAGNOSTIC_IDLE_SECONDS)
            utilization, free_mib = _gpu_boundary_observation(
                plan,
                docker,
                nvidia_smi,
                config_dir,
                active_env,
            )
            gpu_min_free_mib = min(gpu_min_free_mib, free_mib)
            gpu_max_utilization = max(gpu_max_utilization, utilization)
            measured, proof_checks, proof_failures, _ = _harness_batch(
                plan,
                condition="baseline",
                phase="warm",
                count=count,
                key_hex=key_hex,
                docker=docker,
                config_dir=config_dir,
                compose_env=active_env,
                deadline=deadline,
            )
            groups[cohort].extend(measured)
            sample_validity_failures += _sample_validity_failures(measured)
            cache_proof_checks += proof_checks
            cache_proof_failures += proof_failures
            observed_ram, observed_oom = _container_observation(
                plan, docker, config_dir, active_env
            )
            if observed_oom:
                raise LabFailure("candidate_failed")
            peak_ram = max(peak_ram, observed_ram)

        pinned_images = _image_metadata(
            docker,
            config_dir,
            (
                image_env["LAB_MAIN_LLM_IMAGE"],
                image_env["LAB_BOT_API_IMAGE"],
                image_env["LAB_TTS_IMAGE"],
            ),
        )
        if (
            _actual_identities(
                plan,
                docker=docker,
                nvidia_smi=nvidia_smi,
                config_dir=config_dir,
                paths=paths,
                images=pinned_images,
            )
            != identity_state
            or not _production_absent(docker, config_dir)
        ):
            raise LabFailure("environment_drift")
        direct_rows = [
            direct_internal[control][phase]
            for control in ("graphsOff", "graphsOn")
            for phase in phases
        ]
        payload_exact = len({row["payloadProof"] for row in direct_rows}) == 1
        prompt_totals_exact = (
            len({int(row["promptTokensTotal"]) for row in direct_rows}) == 1
        )
        resident_kv_exact = all(
            tuple(
                int(direct_internal[control][phase][name])
                for name in (
                    "promptTokensProcessed",
                    "promptTokensCached",
                    "promptTokensTotal",
                )
            )
            == tuple(
                int(direct_internal[control]["resident"][name])
                for name in (
                    "promptTokensProcessed",
                    "promptTokensCached",
                    "promptTokensTotal",
                )
            )
            for control in ("graphsOff", "graphsOn")
            for phase in ("resident", "afterIdle")
        )
        controls_comparable = all(
            tuple(
                int(direct_internal[control][phase][name])
                for name in (
                    "promptTokensProcessed",
                    "promptTokensCached",
                    "promptTokensTotal",
                )
            )
            == tuple(
                int(direct_internal["graphsOff"][phase][name])
                for name in (
                    "promptTokensProcessed",
                    "promptTokensCached",
                    "promptTokensTotal",
                )
            )
            for control in ("graphsOff", "graphsOn")
            for phase in phases
        )
        invariants = {
            "payloadExact": payload_exact,
            "promptTotalsExact": prompt_totals_exact,
            "residentKvExactAcrossIdle": resident_kv_exact,
            "controlsComparable": controls_comparable,
        }
        invariant_ok = all(invariants.values())
        if (
            cache_proof_failures
            or sample_validity_failures
            or not invariant_ok
        ):
            status = "invariant_failed"
        else:
            status = "completed"
    except LabFailure as exc:
        status = exc.code
        groups = {
            "firstAfterWarmup": [],
            "resident": [],
            "afterIdle": [],
        }
        direct = {
            control: {phase: {} for phase in phases}
            for control in ("graphsOff", "graphsOn")
        }
        invariants = {
            "payloadExact": False,
            "promptTotalsExact": False,
            "residentKvExactAcrossIdle": False,
            "controlsComparable": False,
        }
    finally:
        cleanup = _cleanup(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
        )

    if cleanup.get("status") != "clean" and status == "completed":
        status = "cleanup_required"
    return {
        "schema": SHORT_DIAGNOSTIC_SCHEMA,
        "status": status,
        "config": plan["baselineConfig"],
        "e2e": {
            "causal": False,
            "idleSeconds": SHORT_DIAGNOSTIC_IDLE_SECONDS,
            "samples": {name: len(rows) for name, rows in groups.items()},
            "measurements": {
                name: _short_diagnostic_summary(rows) if rows else {}
                for name, rows in groups.items()
            },
            "orderedSamples": {
                name: _short_diagnostic_ordered_samples(rows)
                for name, rows in groups.items()
            },
        },
        "backendObservation": {
            "causal": False,
            "idleSeconds": SHORT_DIAGNOSTIC_IDLE_SECONDS,
            "samplesPerControl": {
                phase: int(
                    bool(direct["graphsOff"][phase])
                    and bool(direct["graphsOn"][phase])
                )
                for phase in phases
            },
            "graphsOff": direct["graphsOff"],
            "graphsOn": direct["graphsOn"],
            "invariants": invariants,
        },
        "observations": {
            "cacheProofChecks": cache_proof_checks,
            "cacheProofFailures": cache_proof_failures,
            "gpuMinFreeMiB": (
                gpu_min_free_mib if math.isfinite(gpu_min_free_mib) else 0.0
            ),
            "gpuMaxUtilization": gpu_max_utilization,
            "peakHostRamMiB": peak_ram,
            "sampleValidityFailures": sample_validity_failures,
            "runtimeMs": max(1, math.ceil((time.monotonic() - started) * 1000)),
        },
        "cleanup": _public_cleanup(cleanup),
    }


def _run_lab(
    plan: Mapping[str, Any],
    *,
    docker: Path,
    nvidia_smi: Path,
    config_dir: Path,
    paths: Mapping[str, Path],
    _private_diagnostics_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _private_diagnostics_out is not None and _private_diagnostics_out:
        raise ValueError("lab_private_timing_invalid")
    started = time.monotonic()
    max_runtime_s = int(plan["bounds"]["maxRuntimeMs"]) / 1000.0
    # Reserve bounded time for exact-label cleanup and residual-resource proof.
    deadline = started + max(60.0, max_runtime_s - 120.0)
    base_env = _base_compose_env(plan, paths)
    cleanup = None
    status = "runner_failed"
    baseline_after_activation: list[dict[str, Any]] = []
    baseline_resident: list[dict[str, Any]] = []
    candidate_after_activation: list[dict[str, Any]] = []
    candidate_resident: list[dict[str, Any]] = []
    baseline_all_primes: list[dict[str, Any]] = []
    candidate_all_primes: list[dict[str, Any]] = []
    baseline_warm_blocks: list[tuple[dict[str, Any], ...]] = []
    candidate_warm_blocks: list[tuple[dict[str, Any], ...]] = []
    baseline_restart_ready: list[dict[str, Any]] = []
    candidate_restart_ready: list[dict[str, Any]] = []
    baseline_restart_startup_to_ready_ms: list[float] = []
    candidate_restart_startup_to_ready_ms: list[float] = []
    baseline_activation_readiness_ms: list[float] = []
    candidate_activation_readiness_ms: list[float] = []
    soak: list[dict[str, Any]] = []
    peak_ram = 0
    oom_count = 0
    artifact_bytes = 0
    source_checks: dict[str, int] | None = None
    cache_proof_checks = 0
    cache_proof_failures = 0
    gpu_boundary_checks = 0
    host_gpu_min_free_mib = math.inf
    host_gpu_max_utilization = 0.0
    progress_sequence = 0

    def observe_gpu_boundary(compose_env: Mapping[str, str]) -> None:
        nonlocal gpu_boundary_checks, host_gpu_min_free_mib, host_gpu_max_utilization
        utilization, free_mib = _gpu_boundary_observation(
            plan,
            docker,
            nvidia_smi,
            config_dir,
            compose_env,
        )
        gpu_boundary_checks += 1
        host_gpu_min_free_mib = min(host_gpu_min_free_mib, free_mib)
        host_gpu_max_utilization = max(host_gpu_max_utilization, utilization)
    try:
        actual_identities, image_env = _identity_probe_state(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
            paths=paths,
        )
        if actual_identities != plan["identities"]:
            status = "lab_identity_preflight_failed"
            raise LabFailure(status)
        base_env.update(image_env)
        key_hex = secrets.token_hex(32)
        blocks = int(plan["samples"]["abbaBlocks"])
        source_checks = _run_source_checks(
            plan,
            docker=docker,
            config_dir=config_dir,
            compose_env=base_env,
            deadline=deadline,
        )
        active_env = _activate(
            plan,
            config=plan["baselineConfig"],
            initial=True,
            docker=docker,
            config_dir=config_dir,
            base_env=base_env,
            deadline=deadline,
        )
        warm_target = int(plan["samples"]["warmPerCondition"])
        legs_per_condition = blocks * 2
        if (
            blocks < 1
            or warm_target < legs_per_condition
            or warm_target % legs_per_condition != 0
        ):
            raise LabFailure("runner_failed")
        resident_per_leg = warm_target // legs_per_condition
        # TTS survives Main/Bot recreates; this one proof is not sample evidence.
        tts_warmup_pending = True
        for block_index in range(blocks):
            observe_gpu_boundary(active_env)
            block_leg_rows: list[list[dict[str, Any]]] = []
            block_primes: list[dict[str, Any]] = []
            for condition, config in (
                ("baseline", plan["baselineConfig"]),
                ("candidate", plan["candidateConfig"]),
                ("candidate", plan["candidateConfig"]),
                ("baseline", plan["baselineConfig"]),
            ):
                # Every leg owns the same lifecycle. Recreating Main resets
                # backend state; recreating Bot resets CHAT_MESSAGES and its
                # ephemeral continuity store. Readiness then proves the new
                # epoch. Main then remains resident while Bot-only resets make
                # every measured turn own the same fixed one-turn predecessor.
                readiness_ms: list[float] = []
                active_env = _activate(
                    plan,
                    config=config,
                    initial=False,
                    docker=docker,
                    config_dir=config_dir,
                    base_env=base_env,
                    deadline=deadline,
                    readiness_ms_out=readiness_ms,
                )
                if (
                    len(readiness_ms) != 1
                    or readiness_ms[0]
                    > float(plan["bounds"]["maxRestartStartupMs"])
                ):
                    raise LabFailure("candidate_failed")
                if tts_warmup_pending:
                    _tts_harness_warmup(
                        plan,
                        docker=docker,
                        config_dir=config_dir,
                        compose_env=active_env,
                        deadline=deadline,
                    )
                    tts_warmup_pending = False
                leg_rows: list[dict[str, Any]] = []
                for resident_index in range(resident_per_leg):
                    if resident_index:
                        _reset_bot(
                            plan,
                            docker=docker,
                            config_dir=config_dir,
                            compose_env=active_env,
                            deadline=deadline,
                        )
                    warm_rows, proof_checks, proof_failures, harness_startup_ms = (
                        _harness_batch(
                            plan,
                            condition=condition,
                            phase="warm",
                            count=2,
                            key_hex=key_hex,
                            docker=docker,
                            config_dir=config_dir,
                            compose_env=active_env,
                            deadline=deadline,
                        )
                    )
                    if harness_startup_ms is not None or len(warm_rows) != 2:
                        raise LabFailure("runner_failed")
                    cache_proof_checks += proof_checks
                    cache_proof_failures += proof_failures
                    prime, row = warm_rows
                    if _sample_validity_failures((prime, row)):
                        raise LabFailure("candidate_failed")
                    block_primes.append(prime)
                    (
                        baseline_all_primes
                        if condition == "baseline"
                        else candidate_all_primes
                    ).append(prime)
                    if resident_index == 0:
                        (
                            baseline_after_activation
                            if condition == "baseline"
                            else candidate_after_activation
                        ).append(prime)
                        (
                            baseline_activation_readiness_ms
                            if condition == "baseline"
                            else candidate_activation_readiness_ms
                        ).append(readiness_ms[0])
                    leg_rows.append(row)
                    (
                        baseline_resident
                        if condition == "baseline"
                        else candidate_resident
                    ).append(row)
                block_leg_rows.append(leg_rows)
                observed_ram, observed_oom = _container_observation(
                    plan, docker, config_dir, active_env
                )
                peak_ram = max(peak_ram, observed_ram)
                oom_count += observed_oom
                if peak_ram > int(plan["bounds"]["maxHostRamMiB"]):
                    raise LabFailure("candidate_failed")
            prime_identity = {
                (
                    row["replyFingerprint"],
                    row["ttsInputFingerprint"],
                    int(row["replyChars"]),
                    int(row["ttsInputChars"]),
                    int(row["llmPromptTokensTotal"]),
                )
                for row in block_primes
            }
            measured_prompt_vectors = {
                tuple(int(row["llmPromptTokensTotal"]) for row in leg)
                for leg in block_leg_rows
            }
            if len(prime_identity) != 1 or len(measured_prompt_vectors) != 1:
                raise LabFailure("candidate_failed")
            baseline_block = tuple(block_leg_rows[0] + block_leg_rows[3])
            candidate_block = tuple(block_leg_rows[1] + block_leg_rows[2])
            baseline_warm_blocks.append(baseline_block)
            candidate_warm_blocks.append(candidate_block)
            progress_sequence += 1
            _write_progress_checkpoint(
                config_dir,
                plan,
                sequence=progress_sequence,
                phase="warm",
                completed_blocks=block_index + 1,
                baseline_warm=baseline_resident,
                candidate_warm=candidate_resident,
                restart_eligible_baseline=len(baseline_after_activation),
                restart_eligible_candidate=len(candidate_after_activation),
                soak_turns=0,
            )
            observe_gpu_boundary(active_env)

        if (
            len(
                {
                    (
                        row["replyFingerprint"],
                        row["ttsInputFingerprint"],
                        int(row["replyChars"]),
                        int(row["ttsInputChars"]),
                        int(row["llmPromptTokensTotal"]),
                    )
                    for row in (
                        *baseline_all_primes,
                        *candidate_all_primes,
                    )
                }
            )
            != 1
            or len(
                {
                    int(row["llmPromptTokensTotal"])
                    for row in (*baseline_resident, *candidate_resident)
                }
            )
            != 1
        ):
            raise LabFailure("candidate_failed")

        restart_ready_target = int(plan["samples"]["restartReadyPerCondition"])
        if min(
            len(baseline_after_activation), len(candidate_after_activation)
        ) < restart_ready_target:
            raise LabFailure("runner_failed")
        baseline_restart_indexes = _evenly_spaced_indexes(
            len(baseline_after_activation), restart_ready_target
        )
        candidate_restart_indexes = _evenly_spaced_indexes(
            len(candidate_after_activation), restart_ready_target
        )
        baseline_restart_ready.extend(
            baseline_after_activation[index] for index in baseline_restart_indexes
        )
        candidate_restart_ready.extend(
            candidate_after_activation[index] for index in candidate_restart_indexes
        )
        baseline_restart_startup_to_ready_ms.extend(
            baseline_activation_readiness_ms[index]
            for index in baseline_restart_indexes
        )
        candidate_restart_startup_to_ready_ms.extend(
            candidate_activation_readiness_ms[index]
            for index in candidate_restart_indexes
        )

        soak_target = int(plan["samples"]["soakTurns"])
        if soak_target:
            active_env = _activate(
                plan,
                config=plan["candidateConfig"],
                initial=False,
                docker=docker,
                config_dir=config_dir,
                base_env=base_env,
                deadline=deadline,
            )
            while len(soak) < soak_target:
                chunk = min(25, soak_target - len(soak))
                observe_gpu_boundary(active_env)
                batch, proof_checks, proof_failures, _ = _harness_batch(
                    plan,
                    condition="candidate",
                    phase="soak",
                    count=chunk,
                    key_hex=key_hex,
                    docker=docker,
                    config_dir=config_dir,
                    compose_env=active_env,
                    deadline=deadline,
                )
                soak.extend(batch)
                cache_proof_checks += proof_checks
                cache_proof_failures += proof_failures
                progress_sequence += 1
                _write_progress_checkpoint(
                    config_dir,
                    plan,
                    sequence=progress_sequence,
                    phase="soak",
                    completed_blocks=blocks,
                    baseline_warm=baseline_resident,
                    candidate_warm=candidate_resident,
                    restart_eligible_baseline=len(baseline_after_activation),
                    restart_eligible_candidate=len(candidate_after_activation),
                    soak_turns=len(soak),
                )
                observe_gpu_boundary(active_env)
            observed_ram, observed_oom = _container_observation(
                plan, docker, config_dir, active_env
            )
            peak_ram = max(peak_ram, observed_ram)
            oom_count += observed_oom
            if peak_ram > int(plan["bounds"]["maxHostRamMiB"]):
                raise LabFailure("candidate_failed")
        progress_sequence += 1
        _write_progress_checkpoint(
            config_dir,
            plan,
            sequence=progress_sequence,
            phase="measured",
            completed_blocks=blocks,
            baseline_warm=baseline_resident,
            candidate_warm=candidate_resident,
            restart_eligible_baseline=len(baseline_after_activation),
            restart_eligible_candidate=len(candidate_after_activation),
            soak_turns=len(soak),
        )
        pinned_images = _image_metadata(
            docker,
            config_dir,
            (
                image_env["LAB_MAIN_LLM_IMAGE"],
                image_env["LAB_BOT_API_IMAGE"],
                image_env["LAB_TTS_IMAGE"],
            ),
        )
        final_identities = _actual_identities(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
            paths=paths,
            images=pinned_images,
        )
        if final_identities != plan["identities"] or not _production_absent(docker, config_dir):
            status = "environment_drift"
            raise LabFailure(status)
        artifact_bytes = _owned_artifact_bytes(
            config_dir,
            int(plan["bounds"]["maxArtifactBytes"]),
        ) + _epoch_artifact_bytes(plan, docker, config_dir, active_env)
        if artifact_bytes > int(plan["bounds"]["maxArtifactBytes"]):
            raise LabFailure("candidate_failed")
        status = "completed"
    except LabFailure as exc:
        if exc.code in lab.RUN_STATUSES:
            status = exc.code
    finally:
        cleanup = _cleanup(
            plan,
            docker=docker,
            nvidia_smi=nvidia_smi,
            config_dir=config_dir,
        )

    runtime_ms = max(1, math.ceil((time.monotonic() - started) * 1000))
    if status == "completed" and runtime_ms > int(plan["bounds"]["maxRuntimeMs"]):
        status = "timed_out"
    if status != "completed":
        return _failure_receipt_raw(plan, status=status, cleanup=cleanup)

    expected_warm_count = int(plan["samples"]["warmPerCondition"])
    expected_activation_count = int(plan["samples"]["abbaBlocks"]) * 2
    expected_restart_count = int(plan["samples"]["restartReadyPerCondition"])
    if (
        any(
            len(rows) != expected_warm_count
            for rows in (
                baseline_all_primes,
                baseline_resident,
                candidate_all_primes,
                candidate_resident,
            )
        )
        or any(
            len(rows) != expected_activation_count
            for rows in (
                baseline_after_activation,
                candidate_after_activation,
                baseline_activation_readiness_ms,
                candidate_activation_readiness_ms,
            )
        )
        or any(
            len(rows) != expected_restart_count
            for rows in (
                baseline_restart_ready,
                candidate_restart_ready,
                baseline_restart_startup_to_ready_ms,
                candidate_restart_startup_to_ready_ms,
            )
        )
    ):
        raise LabFailure("runner_failed")
    baseline_metrics = _metrics(
        baseline_resident,
        baseline_restart_ready,
        baseline_restart_startup_to_ready_ms,
    )
    candidate_metrics = _metrics(
        candidate_resident,
        candidate_restart_ready,
        candidate_restart_startup_to_ready_ms,
    )
    baseline_metrics["gpuMinFreeMiB"] = min(
        baseline_metrics["gpuMinFreeMiB"],
        *(float(row["gpuFreeMiB"]) for row in baseline_all_primes),
    )
    candidate_metrics["gpuMinFreeMiB"] = min(
        candidate_metrics["gpuMinFreeMiB"],
        *(float(row["gpuFreeMiB"]) for row in candidate_all_primes),
    )
    if _private_diagnostics_out is not None:
        _private_diagnostics_out.update(
            _private_timing_diagnostics(
                baseline_after_activation=baseline_after_activation,
                baseline_resident=baseline_resident,
                candidate_after_activation=candidate_after_activation,
                candidate_resident=candidate_resident,
            )
        )
    comparisons = min(len(baseline_resident), len(candidate_resident))
    matches = sum(
        1
        for baseline_row, candidate_row in zip(
            baseline_resident, candidate_resident
        )
        if baseline_row["replyFingerprint"] == candidate_row["replyFingerprint"]
        and baseline_row["ttsInputFingerprint"] == candidate_row["ttsInputFingerprint"]
        and baseline_row["replyChars"] == candidate_row["replyChars"]
        and baseline_row["ttsInputChars"] == candidate_row["ttsInputChars"]
    )
    all_rows = (
        *baseline_resident,
        *candidate_resident,
        *baseline_all_primes,
        *candidate_all_primes,
        *soak,
    )
    if (
        source_checks is None
        or peak_ram <= 0
        or cache_proof_checks <= 0
        or gpu_boundary_checks <= 0
        or not math.isfinite(host_gpu_min_free_mib)
        or not 0 <= host_gpu_max_utilization <= 100
    ):
        raise LabFailure("runner_failed")
    checks = {
        **source_checks,
        "errorCount": sum(int(row["errorEvents"] > 0) for row in all_rows),
        "oomCount": oom_count,
        "malformedStreamCount": sum(int(row["sentenceEvents"] != 1) for row in all_rows),
        "staleSpeechCount": sum(int(row["staleSpeech"]) for row in all_rows),
        "duplicateSpeechCount": sum(int(row["sentenceEvents"] > 1) for row in all_rows),
        "unsafePrefixCount": sum(int(row["unsafePrefix"]) for row in all_rows),
        "cacheProofFailures": cache_proof_failures,
        "orderViolations": sum(int(row["orderViolation"]) for row in all_rows),
        "externalInterferenceSamples": sum(
            int(row["externalInterference"]) for row in all_rows
        ),
        "safetyFailures": sum(int(row["safetyFailure"]) for row in all_rows),
        "qualityFailures": sum(int(row["qualityFailure"]) for row in all_rows),
    }
    if set(checks) != set(lab.CHECK_FIELDS):
        raise LabFailure("runner_failed")
    baseline_metrics["gpuMinFreeMiB"] = min(
        baseline_metrics["gpuMinFreeMiB"], host_gpu_min_free_mib
    )
    candidate_metrics["gpuMinFreeMiB"] = min(
        candidate_metrics["gpuMinFreeMiB"], host_gpu_min_free_mib
    )
    return {
        "schema": lab.RUNNER_RECEIPT_SCHEMA,
        "runId": plan["runId"],
        "candidateId": plan["candidateId"],
        "identities": plan["identities"],
        "baselineConfig": plan["baselineConfig"],
        "candidateConfig": plan["candidateConfig"],
        "status": status,
        "samples": {
            "warmBaseline": len(baseline_resident),
            "warmCandidate": len(candidate_resident),
            "restartReadyBaseline": len(baseline_restart_ready),
            "restartReadyCandidate": len(candidate_restart_ready),
            "soakTurns": len(soak),
            "abbaBlocks": int(plan["samples"]["abbaBlocks"]),
        },
        "baselineMetrics": baseline_metrics,
        "candidateMetrics": candidate_metrics,
        "statistics": _statistics(
            baseline_warm_blocks,
            candidate_warm_blocks,
            run_id=plan["runId"],
        ),
        "checks": checks,
        "equivalence": {"comparisons": comparisons, "matches": matches},
        "resources": {
            "runtimeMs": max(1, runtime_ms),
            "artifactBytes": artifact_bytes,
            "peakHostRamMiB": peak_ram,
            "maxConcurrentRequests": 1,
        },
        "cleanup": cleanup,
    }


def _validate_plan_dict(raw: Any) -> dict[str, Any]:
    fields = {
        "schema",
        "runnerContract",
        "evaluatorContract",
        "authorityId",
        "candidateId",
        "identities",
        "baselineConfig",
        "candidateConfig",
        "changes",
        "profile",
        "attempt",
        "workload",
        "order",
        "isolation",
        "network",
        "filesystem",
        "lifecycle",
        "samples",
        "bounds",
        "runId",
    }
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("runner_plan_invalid")
    if (
        raw["schema"] != lab.RUNNER_PLAN_SCHEMA
        or raw["runnerContract"] != lab.RUNNER_CONTRACT_ID
        or raw["evaluatorContract"] != lab.EVALUATOR_CONTRACT_ID
        or raw["workload"] != "post_stt_latency_v3"
        or raw["order"] != "ABBA"
        or raw["isolation"] != "owned_lab"
        or raw["network"] != "owned_internal_only_external_egress_disabled"
        or raw["filesystem"] != "owned_ephemeral_content_free_only"
        or raw["lifecycle"] != "external_fixed_coordinator_only"
        or raw["profile"] not in lab.RUN_PROFILES
        or type(raw["attempt"]) is not int
        or not 1 <= raw["attempt"] <= 12
        or any(HASH_ID.fullmatch(raw.get(key, "")) is None for key in ("authorityId", "candidateId", "runId"))
    ):
        raise ValueError("runner_plan_invalid")
    identities = optimizer.IdentitySet.from_mapping(raw["identities"])
    baseline = optimizer.MainLatencyConfig.from_mapping(raw["baselineConfig"])
    candidate_config = optimizer.MainLatencyConfig.from_mapping(raw["candidateConfig"])
    root, _, _, _ = optimizer.bootstrap_ephemeral_fixed_coordinator(identities)
    candidate = optimizer.compile_candidate(
        {
            "schema": optimizer.PROPOSAL_SCHEMA,
            "identities": raw["identities"],
            "baselineConfig": raw["baselineConfig"],
            "changes": [
                {"key": change["key"], "value": change["to"]}
                for change in raw["changes"]
            ],
        },
        trust_root=root,
    )
    spec = lab.RUN_PROFILES[raw["profile"]]
    expected_samples = spec.samples_dict()
    expected_bounds = spec.bounds_dict()
    unsigned = dict(raw)
    run_id = unsigned.pop("runId")
    expected_run_id = lab._content_id(lab.RUNNER_PLAN_ID_SCHEMA, unsigned)
    if (
        baseline != candidate.baseline_config
        or candidate_config != candidate.candidate_config
        or candidate.candidate_id != raw["candidateId"]
        or raw["samples"] != expected_samples
        or raw["bounds"] != expected_bounds
        or run_id != expected_run_id
    ):
        raise ValueError("runner_plan_invalid")
    return dict(raw)


def _failure_receipt_raw(
    plan: Mapping[str, Any], *, status: str, cleanup: Mapping[str, Any]
) -> dict[str, Any]:
    if status not in lab.RUN_STATUSES:
        status = "runner_failed"
    return {
        "schema": lab.RUNNER_RECEIPT_SCHEMA,
        "runId": plan["runId"],
        "candidateId": plan["candidateId"],
        "identities": plan["identities"],
        "baselineConfig": plan["baselineConfig"],
        "candidateConfig": plan["candidateConfig"],
        "status": status,
        "samples": {key: 0 for key in lab.SAMPLE_FIELDS},
        "baselineMetrics": {key: 0.0 for key in lab.METRIC_FIELDS},
        "candidateMetrics": {key: 0.0 for key in lab.METRIC_FIELDS},
        "statistics": {
            "schema": lab.STATISTICS_SCHEMA,
            "method": "paired-bootstrap-abba-v1",
            "bootstrapReplicates": 1,
            "confidenceLevel": 0.95,
            "warmAnswerFirstPcmP95DeltaCiLowMs": 0.0,
            "warmAnswerFirstPcmP95DeltaCiHighMs": 0.0,
            "warmAnswerFirstPcmP95EffectSize": 0.0,
        },
        "checks": {key: 1 if key == "errorCount" else 0 for key in lab.CHECK_FIELDS},
        "equivalence": {"comparisons": 0, "matches": 0},
        "resources": {
            "runtimeMs": 0,
            "artifactBytes": 0,
            "peakHostRamMiB": 0,
            "maxConcurrentRequests": 0,
        },
        "cleanup": dict(cleanup),
    }


def _read_request() -> tuple[str, dict[str, Any]]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("lab_worker_request_invalid")
    value = json.loads(raw.decode("ascii"))
    if not isinstance(value, dict) or value.get("schema") != adapter.LAB_WORKER_REQUEST_SCHEMA:
        raise ValueError("lab_worker_request_invalid")
    mode = value.get("mode")
    if mode == "discover":
        if set(value) != {"schema", "mode", "baselineConfig"}:
            raise ValueError("lab_worker_request_invalid")
        baseline = optimizer.MainLatencyConfig.from_mapping(value["baselineConfig"])
        return mode, baseline.to_dict()
    if mode == "reconcile":
        if set(value) != {"schema", "mode"}:
            raise ValueError("lab_worker_request_invalid")
        return mode, {"runId": adapter.GLOBAL_RECONCILE_RUN_ID}
    if mode in {"short_diagnostic", "short_diagnostic_cleanup"}:
        if set(value) != {"schema", "mode", "config"}:
            raise ValueError("lab_worker_request_invalid")
        config = optimizer.MainLatencyConfig.from_mapping(value["config"])
        return mode, config.to_dict()
    if (
        mode not in {"preflight", "run", "cleanup"}
        or set(value) != {"schema", "mode", "plan"}
        or not isinstance(value["plan"], dict)
    ):
        raise ValueError("lab_worker_request_invalid")
    return mode, _validate_plan_dict(value["plan"])


def _result_has_verified_clean_cleanup(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if (
        value.get("status") == "clean"
        and value.get("remainingProcesses") == 0
        and value.get("remainingGpuAllocations") == 0
        and value.get("remainingArtifacts") == 0
    ):
        return True
    return any(
        _result_has_verified_clean_cleanup(child) for child in value.values()
    )


def main() -> int:
    global _OWNED_ROOT
    config_dir: Path | None = None
    preserve_config_dir = False
    cleanup_mode = False
    try:
        mode, payload = _read_request()
        cleanup_mode = mode in {
            "run",
            "cleanup",
            "reconcile",
            "short_diagnostic",
            "short_diagnostic_cleanup",
        }
        docker = _fixed_executable("docker")
        paths = (
            _operator_paths()
            if mode not in {"cleanup", "reconcile", "short_diagnostic_cleanup"}
            else None
        )
        nvidia_smi = _fixed_executable("nvidia-smi")
        if mode == "discover":
            marker_run_id = _content_id(
                {
                    "schema": "evelyn.main-latency-discovery-temp.v1",
                    "payload": payload,
                }
            )
        elif mode in {"short_diagnostic", "short_diagnostic_cleanup"}:
            marker_run_id = _short_diagnostic_plan(payload)["runId"]
        else:
            marker_run_id = payload["runId"]
        config_dir = _create_owned_temp_dir(marker_run_id)
        preserve_config_dir = cleanup_mode
        _OWNED_ROOT = config_dir.resolve()
        sys.addaudithook(_audit)
        if mode == "discover":
            assert paths is not None and nvidia_smi is not None
            try:
                result = {"identities": _discover(
                    payload,
                    docker=docker,
                    nvidia_smi=nvidia_smi,
                    config_dir=config_dir,
                    paths=paths,
                )}
            except LabFailure as exc:
                result = {
                    "errorCode": (
                        exc.code
                        if exc.code in lab.LAB_PREFLIGHT_FAILURE_CODES
                        else "lab_isolation_preflight_failed"
                    )
                }
        elif mode == "preflight":
            assert paths is not None and nvidia_smi is not None
            result = _preflight(
                payload,
                docker=docker,
                nvidia_smi=nvidia_smi,
                config_dir=config_dir,
                paths=paths,
            )
        elif mode == "run":
            assert paths is not None and nvidia_smi is not None
            timing_diagnostics: dict[str, Any] = {}
            result = {
                "receipt": _run_lab(
                    payload,
                    docker=docker,
                    nvidia_smi=nvidia_smi,
                    config_dir=config_dir,
                    paths=paths,
                    _private_diagnostics_out=timing_diagnostics,
                ),
                "timingDiagnostics": timing_diagnostics,
            }
        elif mode == "short_diagnostic":
            assert paths is not None and nvidia_smi is not None
            result = _run_short_diagnostic(
                payload,
                docker=docker,
                nvidia_smi=nvidia_smi,
                config_dir=config_dir,
                paths=paths,
            )
        elif mode == "short_diagnostic_cleanup":
            assert nvidia_smi is not None
            result = {
                "cleanup": _cleanup(
                    _short_diagnostic_plan(payload),
                    docker=docker,
                    nvidia_smi=nvidia_smi,
                    config_dir=config_dir,
                )
            }
        else:
            assert nvidia_smi is not None
            result = {"cleanup": _cleanup(
                payload,
                docker=docker,
                nvidia_smi=nvidia_smi,
                config_dir=config_dir,
                all_runs=mode == "reconcile",
            )}
        response = {
            "schema": adapter.LAB_WORKER_RESPONSE_SCHEMA,
            "mode": mode,
            "result": result,
        }
        preserve_config_dir = cleanup_mode and not _result_has_verified_clean_cleanup(
            result
        )
        sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")))
        return 0
    except (json.JSONDecodeError, LabFailure, OSError, TypeError, ValueError):
        return 2
    finally:
        if config_dir is not None and not preserve_config_dir:
            shutil.rmtree(config_dir)


if __name__ == "__main__":
    raise SystemExit(main())
