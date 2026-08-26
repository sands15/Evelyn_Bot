from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import math
import os
import re
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable

from .host_supervisor_client import SUPERVISOR_STATUS_SCHEMA
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write, read_bounded_json


WORKSPACE_TASK_REQUEST_SCHEMA = "host_supervisor.workspace-task.request.v2"
WORKSPACE_TASK_RESPONSE_SCHEMA = "host_supervisor.workspace-task.response.v2"
WORKSPACE_TASK_TOOL_NAMES = frozenset({"list", "search", "read", "edit", "test", "diff"})
# Ordinary authority on the shared host queue is deliberately read-only. A
# process that can write runtime artifacts is not, by itself, authorized to
# stage or execute code; edit/test require the separate sandbox-domain HMAC.
WORKSPACE_TASK_QUEUE_TOOL_NAMES = frozenset({"list", "search", "read", "diff"})
WORKSPACE_TASK_MAX_OUTPUT_BYTES = 16 * 1024
WORKSPACE_TASK_MAX_REQUEST_BYTES = 3 * 1024 * 1024
WORKSPACE_TASK_MAX_RESPONSE_BYTES = 128 * 1024
WORKSPACE_TASK_REQUEST_TTL_SEC = 30.0
WORKSPACE_SANDBOX_TEST_REQUEST_TTL_SEC = 45.0
WORKSPACE_SANDBOX_CLIENT_TIMEOUT_SEC = 38.0
WORKSPACE_TASK_ORPHAN_TTL_SEC = 60.0
WORKSPACE_TASK_COMMAND_TIMEOUT_SEC = 20.0
WORKSPACE_TASK_AUTH_ENV = "LOCAL_BRIDGE_STATUS_AUTH_TOKEN"
WORKSPACE_TASK_AUTH_ALGORITHM = "hmac-sha256"
WORKSPACE_TASK_REQUEST_AUTH_DOMAIN = b"evelyn.workspace-task.request.v2\n"
WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN = b"evelyn.workspace-task.response.v2\n"
WORKSPACE_SANDBOX_AUTH_ENV = "EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN"
WORKSPACE_SANDBOX_AUTH_DOMAIN = b"evelyn.workspace-sandbox.request.v1\n"
WORKSPACE_SANDBOX_RESPONSE_AUTH_DOMAIN = b"evelyn.workspace-sandbox.response.v1\n"
WORKSPACE_EDIT_ABSENT_SHA = "ABSENT"
WORKSPACE_EDIT_STAGE_TTL_SEC = 300.0
# The approval manager exposes one global pending mutation, so Host must not
# accumulate unclaimable candidates behind it.
WORKSPACE_EDIT_MAX_STAGES = 1
# Leave enough headroom below _result's half-response evidence bound for all
# binding metadata. Oversized diffs are rejected; approval never sees clipping.
WORKSPACE_EDIT_MAX_PREVIEW_BYTES = 48 * 1024
WORKSPACE_MUTATION_AUTH_ENV = "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN"
WORKSPACE_MUTATION_REQUEST_SCHEMA = "host_supervisor.workspace-mutation.request.v2"
WORKSPACE_MUTATION_RESPONSE_SCHEMA = "host_supervisor.workspace-mutation.response.v2"
WORKSPACE_MUTATION_REQUEST_AUTH_DOMAIN = b"evelyn.workspace-mutation.request.v2\n"
WORKSPACE_MUTATION_RESPONSE_AUTH_DOMAIN = b"evelyn.workspace-mutation.response.v2\n"
WORKSPACE_MUTATION_REQUEST_TTL_SEC = 20.0
WORKSPACE_MUTATION_MAX_REQUEST_BYTES = 64 * 1024
WORKSPACE_MUTATION_MAX_RESPONSE_BYTES = 128 * 1024

_MAX_FILE_BYTES = 1024 * 1024
_MAX_EDIT_BYTES = 1024 * 1024
_MAX_READ_CHUNK_BYTES = 2 * 1024
_MAX_READ_EVIDENCE_CHARS = 3_999
_MAX_LIST_ENTRIES = 64
_MAX_SEARCH_MATCHES = 32
_MAX_SEARCH_FILE_BYTES = 256 * 1024
_MAX_SEARCH_FILES = 4096
_MAX_SEARCH_DIRECTORIES = 4096
_MAX_TARGETS = 16
_STATUS_STALE_SEC = 4.0
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_COMPONENTS = frozenset(
    {
        ".git",
        ".ssh",
        ".codex",
        ".venv",
        "venv",
        "node_modules",
        "runtime_artifacts",
        "bot_memory",
        "memory_vault",
        "guild_settings",
        "bot_profiles",
        "logs",
        "debug_audio",
        "bots",
        "code_records",
        "experiments",
        "recordings",
        "results",
        "server_data",
        "omnivoice_profiles",
        "tmp",
        ".trash",
        "outputs",
        "wandb",
        ".obsidian",
        "credentials",
        "private",
        ".private",
        "secrets",
    }
)
_SENSITIVE_FILENAMES = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "keys.json",
        "saves.json",
        "scratch.js",
        "secrets.json",
        "token.json",
        "id_rsa",
        "id_ed25519",
        "99_project_inbox.md",
        "ref_text.txt",
    }
)
_SENSITIVE_SUFFIXES = frozenset(
    {
        ".pem",
        ".p12",
        ".pfx",
        ".key",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".jsonl",
        ".log",
        ".wav",
        ".mp3",
        ".flac",
    }
)
_ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})
_SENSITIVE_COMPONENT_PREFIXES = ("tmp-ms-profile-", "node_modules")
_SENSITIVE_FILENAME_SUFFIXES = ("-cache.json",)
_ALLOWED_ROOT_DIRECTORIES = frozenset(
    {
        ".github",
        "docker",
        "docs",
        "evelyn_core",
        "evelyn_voice",
        "external",
        "patches",
        "tests",
        "tools",
    }
)
_ALLOWED_ROOT_FILENAMES = frozenset(
    {
        ".env.example",
        ".gitignore",
        "agents.md",
        "docker-compose.fast-control.yml",
        "docker-compose.yml",
        "main.py",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "readme.md",
        "requirements.txt",
    }
)
_WINDOWS_RESERVED_STEMS = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_PROTECTED_AUTHORITY_FILENAMES = frozenset(
    {
        "task_loop_runtime.py",
        "task_approval_runtime.py",
        "workspace_task_tools.py",
        "workspace_test_sandbox.py",
        "workspace_test_runner.py",
        "host_supervisor.py",
        "host_supervisor_client.py",
        "runtime_artifact_io.py",
        "paths.py",
        "__init__.py",
        "fast_control_api.py",
        "fast_action_runtime.py",
        "control_page_server.py",
        "control_page_http.py",
        "ui_action_target.py",
        "autonomy_authorization.py",
        "autonomy_outcome_evidence.py",
        "agents.md",
        ".env.example",
        "main.py",
        "conftest.py",
        "docker-compose.yml",
        "docker-compose.fast-control.yml",
        "package.json",
        "package-lock.json",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "config.py",
        "runtime_config_schema.py",
        "main_runtime_config.py",
        "main_llm_runtime.py",
        "text.py",
        "memory_exposure.py",
        "turn_lifecycle.py",
        "runtime_health.py",
        "fast_context_contract.py",
        "search_tools.py",
        "voice_orchestration.py",
        "voice_route_execution.py",
        "cognitive_policy_state.py",
        "memory.py",
        "fast_path_policy.py",
        "tool_awareness_policy.py",
        "response_context_composition.py",
        "test_task_loop_runtime.py",
        "test_task_approval_runtime.py",
        "test_workspace_task_tools.py",
        "test_host_supervisor.py",
    }
)
_AUTHORITY_NAME_MARKERS = (
    "authorization",
    "authority",
    "permission",
    "approval",
    "grant_policy",
    "evaluator",
    "outcome_evidence",
    "control_page",
    "fast_control",
    "policy",
    "config",
    "router",
    "registry",
    "composition",
    "dependency",
)
_BASE_RESULT_KEYS = {
    "attempted",
    "executed",
    "observed",
    "verified",
    "outcome",
    "code",
    "summary",
    "evidence",
}
_REQUEST_KEYS = {
    "schema",
    "hostInstanceId",
    "requestId",
    "taskId",
    "grantId",
    "actionRunId",
    "stepId",
    "surface",
    "tool",
    "requiresSandboxTest",
    "candidateStageId",
    "sandboxAuthAlgorithm",
    "sandboxAuthTag",
    "args",
    "argsHash",
    "issuedAt",
    "expiresAt",
    "authAlgorithm",
    "authTag",
}
_RESPONSE_KEYS = {
    "schema",
    "hostInstanceId",
    "requestId",
    "taskId",
    "grantId",
    "actionRunId",
    "stepId",
    "surface",
    "tool",
    "requiresSandboxTest",
    "candidateStageId",
    "argsHash",
    "issuedAt",
    "expiresAt",
    "respondedAt",
    "result",
    "sandboxAuthAlgorithm",
    "sandboxAuthTag",
    "authAlgorithm",
    "authTag",
}
_ARG_KEYS = {
    "list": {"path", "recursive"},
    "search": {"path", "query"},
    "read": {"path"},
    "test": {"runner", "targets"},
    "diff": {"paths"},
}
_MUTATION_CLAIM_KEYS = {
    "approvalId",
    "claimId",
    "stageId",
    "hostInstanceId",
    "taskId",
    "grantId",
    "grantExpiresAt",
    "actionRunId",
    "stepId",
    "surface",
    "tool",
    "argsHash",
    "baseSha256",
    "candidateSha256",
    "previewDigest",
    "dirtyBaseAcknowledged",
}
_MUTATION_REQUEST_KEYS = {
    "schema",
    "operation",
    "requestId",
    *_MUTATION_CLAIM_KEYS,
    "issuedAt",
    "expiresAt",
    "authAlgorithm",
    "authTag",
}
_MUTATION_RESPONSE_KEYS = {
    "schema",
    "operation",
    "requestId",
    *_MUTATION_CLAIM_KEYS,
    "issuedAt",
    "expiresAt",
    "respondedAt",
    "result",
    "authAlgorithm",
    "authTag",
}


def _resolve_workspace_task_auth_token(value: str | None = None) -> str:
    return str(
        os.getenv(WORKSPACE_TASK_AUTH_ENV, "") if value is None else value
    ).strip()


def _valid_workspace_task_auth_token(value: str | None) -> bool:
    size = len(_resolve_workspace_task_auth_token(value).encode("utf-8"))
    return 32 <= size <= 512


def _resolve_workspace_sandbox_auth_token(value: str | None = None) -> str:
    return str(
        os.getenv(WORKSPACE_SANDBOX_AUTH_ENV, "") if value is None else value
    ).strip()


def _valid_workspace_sandbox_auth_token(value: str | None) -> bool:
    size = len(_resolve_workspace_sandbox_auth_token(value).encode("utf-8"))
    return 32 <= size <= 512


def _resolve_workspace_mutation_auth_token(value: str | None = None) -> str:
    return str(
        os.getenv(WORKSPACE_MUTATION_AUTH_ENV, "") if value is None else value
    ).strip()


def _valid_workspace_mutation_auth_token(value: str | None) -> bool:
    size = len(_resolve_workspace_mutation_auth_token(value).encode("utf-8"))
    return 32 <= size <= 512


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def workspace_task_args_hash(args: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(args)).hexdigest()


def _workspace_sandbox_authority_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "schema",
            "hostInstanceId",
            "requestId",
            "taskId",
            "grantId",
            "actionRunId",
            "stepId",
            "surface",
            "tool",
            "requiresSandboxTest",
            "candidateStageId",
            "argsHash",
            "issuedAt",
            "expiresAt",
        )
    }


def _sign_workspace_sandbox_authority(
    payload: dict[str, Any],
    *,
    auth_token: str | None,
) -> dict[str, Any]:
    signed = {
        **payload,
        "sandboxAuthAlgorithm": WORKSPACE_TASK_AUTH_ALGORITHM,
    }
    token = _resolve_workspace_sandbox_auth_token(auth_token).encode("utf-8")
    if not 32 <= len(token) <= 512:
        signed["sandboxAuthTag"] = ""
        return signed
    digest = hmac.new(token, digestmod=hashlib.sha256)
    digest.update(WORKSPACE_SANDBOX_AUTH_DOMAIN)
    digest.update(_canonical_json_bytes(_workspace_sandbox_authority_payload(signed)))
    signed["sandboxAuthTag"] = digest.hexdigest()
    return signed


def _workspace_sandbox_authority_is_authentic(
    payload: Any,
    *,
    auth_token: str | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    supplied = payload.get("sandboxAuthTag")
    if (
        payload.get("sandboxAuthAlgorithm") != WORKSPACE_TASK_AUTH_ALGORITHM
        or not isinstance(supplied, str)
        or not _SHA256_PATTERN.fullmatch(supplied)
    ):
        return False
    expected = _sign_workspace_sandbox_authority(
        payload,
        auth_token=auth_token,
    ).get("sandboxAuthTag")
    return bool(expected and hmac.compare_digest(supplied, str(expected)))


def workspace_sandbox_request_is_authentic(
    payload: Any,
    *,
    auth_token: str | None,
) -> bool:
    return _workspace_sandbox_authority_is_authentic(
        payload,
        auth_token=auth_token,
    )


def _sign_workspace_sandbox_response(
    payload: dict[str, Any],
    *,
    auth_token: str | None,
) -> dict[str, Any]:
    signed = {
        **payload,
        "sandboxAuthAlgorithm": WORKSPACE_TASK_AUTH_ALGORITHM,
    }
    token = _resolve_workspace_sandbox_auth_token(auth_token).encode("utf-8")
    if not 32 <= len(token) <= 512:
        signed["sandboxAuthTag"] = ""
        return signed
    unsigned = {
        key: value
        for key, value in signed.items()
        if key not in {"authAlgorithm", "authTag", "sandboxAuthTag"}
    }
    digest = hmac.new(token, digestmod=hashlib.sha256)
    digest.update(WORKSPACE_SANDBOX_RESPONSE_AUTH_DOMAIN)
    digest.update(_canonical_json_bytes(unsigned))
    signed["sandboxAuthTag"] = digest.hexdigest()
    return signed


def _workspace_sandbox_response_is_authentic(
    payload: Any,
    *,
    auth_token: str | None,
) -> bool:
    if not isinstance(payload, dict):
        return False
    supplied = payload.get("sandboxAuthTag")
    if (
        payload.get("sandboxAuthAlgorithm") != WORKSPACE_TASK_AUTH_ALGORITHM
        or not isinstance(supplied, str)
        or not _SHA256_PATTERN.fullmatch(supplied)
    ):
        return False
    expected = _sign_workspace_sandbox_response(
        payload,
        auth_token=auth_token,
    ).get("sandboxAuthTag")
    return bool(expected and hmac.compare_digest(supplied, str(expected)))


def _sign_workspace_task_payload(
    payload: dict[str, Any],
    *,
    auth_token: str | None,
    domain: bytes,
) -> dict[str, Any]:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"authAlgorithm", "authTag"}
    }
    signed = {**unsigned, "authAlgorithm": WORKSPACE_TASK_AUTH_ALGORITHM}
    token = _resolve_workspace_task_auth_token(auth_token).encode("utf-8")
    if not 32 <= len(token) <= 512:
        signed["authTag"] = ""
        return signed
    digest = hmac.new(token, digestmod=hashlib.sha256)
    digest.update(domain)
    digest.update(_canonical_json_bytes(signed))
    signed["authTag"] = digest.hexdigest()
    return signed


def _workspace_task_payload_is_authentic(
    payload: Any,
    *,
    auth_token: str | None,
    domain: bytes,
) -> bool:
    if not isinstance(payload, dict):
        return False
    supplied = payload.get("authTag")
    if (
        payload.get("authAlgorithm") != WORKSPACE_TASK_AUTH_ALGORITHM
        or not isinstance(supplied, str)
        or not _SHA256_PATTERN.fullmatch(supplied)
    ):
        return False
    expected = _sign_workspace_task_payload(
        payload,
        auth_token=auth_token,
        domain=domain,
    ).get("authTag")
    return bool(expected and hmac.compare_digest(supplied, str(expected)))


def _sign_workspace_mutation_payload(
    payload: dict[str, Any],
    *,
    auth_token: str | None,
    domain: bytes,
) -> dict[str, Any]:
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"authAlgorithm", "authTag"}
    }
    signed = {**unsigned, "authAlgorithm": WORKSPACE_TASK_AUTH_ALGORITHM}
    token = _resolve_workspace_mutation_auth_token(auth_token).encode("utf-8")
    if not 32 <= len(token) <= 512:
        signed["authTag"] = ""
        return signed
    digest = hmac.new(token, digestmod=hashlib.sha256)
    digest.update(domain)
    digest.update(_canonical_json_bytes(signed))
    signed["authTag"] = digest.hexdigest()
    return signed


def _workspace_mutation_payload_is_authentic(
    payload: Any,
    *,
    auth_token: str | None,
    domain: bytes,
) -> bool:
    if not isinstance(payload, dict):
        return False
    supplied = payload.get("authTag")
    if (
        payload.get("authAlgorithm") != WORKSPACE_TASK_AUTH_ALGORITHM
        or not isinstance(supplied, str)
        or not _SHA256_PATTERN.fullmatch(supplied)
    ):
        return False
    expected = _sign_workspace_mutation_payload(
        payload,
        auth_token=auth_token,
        domain=domain,
    ).get("authTag")
    return bool(expected and hmac.compare_digest(supplied, str(expected)))


def _valid_result_payload(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _BASE_RESULT_KEYS:
        return False
    flags = tuple(
        value.get(name)
        for name in ("attempted", "executed", "observed", "verified")
    )
    if any(type(flag) is not bool for flag in flags):
        return False
    attempted, executed, observed, verified = flags
    outcome = value.get("outcome")
    if outcome not in {"succeeded", "failed", "blocked", "outcome_unverified"}:
        return False
    if executed and not attempted:
        return False
    if outcome == "succeeded" and not all(flags):
        return False
    if outcome == "outcome_unverified" and verified:
        return False
    return bool(
        isinstance(value.get("code"), str)
        and isinstance(value.get("summary"), str)
        and isinstance(value.get("evidence"), dict)
    )


class _WorkspaceTaskError(RuntimeError):
    def __init__(self, code: str, *, outcome: str = "blocked") -> None:
        super().__init__(code)
        self.code = code
        self.outcome = outcome


def _clip_utf8(value: Any, maximum_bytes: int) -> tuple[str, bool]:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    limit = max(0, int(maximum_bytes))
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _result(
    *,
    attempted: bool,
    executed: bool,
    observed: bool,
    verified: bool,
    outcome: str,
    code: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_summary, _ = _clip_utf8(summary, 512)
    safe_evidence = dict(evidence or {})
    try:
        encoded = json.dumps(safe_evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError):
        safe_evidence = {}
        encoded = b"{}"
    if len(encoded) > WORKSPACE_TASK_MAX_RESPONSE_BYTES // 2:
        preview, _ = _clip_utf8(encoded.decode("utf-8", errors="replace"), WORKSPACE_TASK_MAX_OUTPUT_BYTES)
        safe_evidence = {"preview": preview, "truncated": True}
    return {
        "attempted": bool(attempted),
        "executed": bool(executed),
        "observed": bool(observed),
        "verified": bool(verified),
        "outcome": str(outcome or "failed"),
        "code": str(code or "workspace_task_failed"),
        "summary": safe_summary,
        "evidence": safe_evidence,
    }


def _blocked(code: str, summary: str = "Workspace task was blocked.", *, attempted: bool = False) -> dict[str, Any]:
    return _result(
        attempted=attempted,
        executed=False,
        observed=True,
        verified=True,
        outcome="blocked",
        code=code,
        summary=summary,
    )


def _unverified(code: str, summary: str) -> dict[str, Any]:
    return _result(
        attempted=True,
        executed=True,
        observed=False,
        verified=False,
        outcome="outcome_unverified",
        code=code,
        summary=summary,
    )


def _is_sensitive(parts: tuple[str, ...]) -> bool:
    lowered = tuple(part.casefold() for part in parts)
    if any(
        part in _SENSITIVE_COMPONENTS
        or part == "_tmp_ms_profiles"
        or part.startswith(_SENSITIVE_COMPONENT_PREFIXES)
        for part in lowered
    ):
        return True
    if not lowered:
        return False
    name = lowered[-1]
    if (
        len(lowered) >= 4
        and lowered[:3] == ("external", "mindcraft", "services")
        and lowered[3] == "viaproxy"
    ) or (
        len(lowered) >= 3
        and lowered[:2] == ("external", "mindcraft")
        and name.startswith(("andy_", "jill_"))
        and name.endswith(".json")
    ):
        return True
    if name in _SENSITIVE_FILENAMES:
        return True
    if name.startswith(".env.") and name not in _ENV_TEMPLATE_NAMES:
        return True
    return bool(
        Path(name).suffix in _SENSITIVE_SUFFIXES
        or name.endswith(_SENSITIVE_FILENAME_SUFFIXES)
    )


def _invalid_path_component(part: str) -> bool:
    stem = part.split(".", 1)[0].casefold()
    return bool(
        any(character in '<>:"|?*' or ord(character) < 32 for character in part)
        or part.endswith((" ", "."))
        or stem in _WINDOWS_RESERVED_STEMS
    )


def _allowed_workspace_path(parts: tuple[str, ...]) -> bool:
    if not parts:
        return True
    first = parts[0].casefold()
    return bool(
        first in _ALLOWED_ROOT_DIRECTORIES
        or (len(parts) == 1 and first in _ALLOWED_ROOT_FILENAMES)
    )


def _external_tracked_path_allowed(
    parts: tuple[str, ...],
    tracked_paths: frozenset[str] | None,
) -> bool:
    if not parts or parts[0].casefold() != "external":
        return True
    if len(parts) == 1:
        return True
    if tracked_paths is None:
        return False
    return Path(*parts).as_posix() in tracked_paths


def _is_single_link_regular_file(path: Path) -> bool:
    try:
        value = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return bool(stat.S_ISREG(value.st_mode) and int(value.st_nlink) == 1)


def _is_link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(callable(is_junction) and is_junction())
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _workspace_nondefault_stream_count(path: Path) -> int:
    """Return the NTFS named-stream count without exposing stream names."""

    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    class _WIN32_FIND_STREAM_DATA(ctypes.Structure):
        _fields_ = (
            ("StreamSize", ctypes.c_longlong),
            ("cStreamName", wintypes.WCHAR * 296),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = (
        wintypes.LPCWSTR,
        wintypes.INT,
        ctypes.POINTER(_WIN32_FIND_STREAM_DATA),
        wintypes.DWORD,
    )
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_WIN32_FIND_STREAM_DATA),
    )
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = (wintypes.HANDLE,)
    find_close.restype = wintypes.BOOL
    data = _WIN32_FIND_STREAM_DATA()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error == 38:  # ERROR_HANDLE_EOF
            return 0
        raise _WorkspaceTaskError("workspace_stream_inspection_unavailable")
    count = 0
    try:
        while True:
            if str(data.cStreamName).casefold() != "::$data":
                count += 1
            if find_next(handle, ctypes.byref(data)):
                continue
            if ctypes.get_last_error() != 38:  # ERROR_HANDLE_EOF
                raise _WorkspaceTaskError(
                    "workspace_stream_inspection_unavailable"
                )
            return count
    finally:
        find_close(handle)


def _workspace_recovery_artifacts(target: Path) -> tuple[Path, ...]:
    prefix = f".{target.name}.evelyn-"
    try:
        return tuple(
            child
            for child in target.parent.iterdir()
            if child.name.startswith(prefix) and child.name.endswith(".tmp")
        )
    except OSError:
        raise _WorkspaceTaskError("workspace_recovery_inspection_unavailable") from None


def _workspace_path(
    project_root: Path,
    raw_path: Any,
    *,
    external_tracked_paths: frozenset[str] | None = None,
    allow_untracked_external: bool = False,
) -> tuple[Path, str]:
    if not isinstance(raw_path, str) or not raw_path or len(raw_path) > 512 or "\x00" in raw_path:
        raise _WorkspaceTaskError("workspace_path_invalid")
    normalized = raw_path.replace("\\", "/")
    if normalized == ".":
        parts: tuple[str, ...] = ()
    else:
        raw_parts = normalized.split("/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part == ".." for part in raw_parts)
        ):
            raise _WorkspaceTaskError("workspace_path_outside_root")
        if normalized.startswith("-") or any(
            part in {"", "."} or _invalid_path_component(part)
            for part in raw_parts
        ):
            raise _WorkspaceTaskError("workspace_path_invalid")
        parts = tuple(raw_parts)
    if _is_sensitive(parts):
        raise _WorkspaceTaskError("workspace_sensitive_path_denied")
    if not _allowed_workspace_path(parts):
        raise _WorkspaceTaskError("workspace_path_not_allowed")
    if not allow_untracked_external and not _external_tracked_path_allowed(
        parts,
        external_tracked_paths,
    ):
        raise _WorkspaceTaskError("workspace_external_untracked_denied")

    root = Path(project_root).resolve()
    candidate = root.joinpath(*parts)
    current = root
    for part in parts:
        current = current / part
        if _is_link_like(current):
            raise _WorkspaceTaskError("workspace_symlink_denied")
    resolved = candidate.resolve(strict=False)
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        raise _WorkspaceTaskError("workspace_path_outside_root") from None
    relative_text = relative.as_posix() if relative.parts else "."
    if candidate.exists() and candidate.is_file() and not _is_single_link_regular_file(candidate):
        raise _WorkspaceTaskError("workspace_hardlink_denied")
    return resolved, relative_text


def _normalized_windows_path(value: str | Path) -> str:
    text = str(value)
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _open_pinned_windows_directory(path: Path) -> int:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x00000080,  # FILE_READ_ATTRIBUTES
        0x00000001,  # FILE_SHARE_READ; deny write/delete of this directory
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        raise OSError(ctypes.get_last_error(), "workspace directory pin failed")
    return int(handle)


def _pinned_windows_directory_info(handle: int) -> tuple[str, int]:
    import ctypes
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation))
    get_info.restype = wintypes.BOOL
    info = _ByHandleFileInformation()
    if not get_info(wintypes.HANDLE(handle), ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), "workspace directory query failed")

    get_path = kernel32.GetFinalPathNameByHandleW
    get_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32_768)
    length = int(get_path(wintypes.HANDLE(handle), buffer, len(buffer), 0))
    if length <= 0 or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "workspace directory path query failed")
    return _normalized_windows_path(buffer.value), int(info.dwFileAttributes)


def _close_pinned_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(handle))


@contextmanager
def _pinned_workspace_ancestors(project_root: Path, target: Path):
    if os.name != "nt":
        yield
        return
    root = Path(project_root).resolve()
    parent = Path(target).parent.absolute()
    try:
        relative_parent = parent.relative_to(root)
    except ValueError:
        raise _WorkspaceTaskError("workspace_path_outside_root") from None
    paths = [root]
    current = root
    for part in relative_parent.parts:
        current = current / part
        paths.append(current)
    handles: list[int] = []
    try:
        for expected in paths:
            handle = _open_pinned_windows_directory(expected)
            handles.append(handle)
            opened_path, attributes = _pinned_windows_directory_info(handle)
            if (
                opened_path != _normalized_windows_path(expected)
                or not attributes & 0x00000010  # FILE_ATTRIBUTE_DIRECTORY
                or attributes & 0x00000400  # FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise _WorkspaceTaskError("workspace_path_identity_changed")
        yield
    except _WorkspaceTaskError:
        raise
    except OSError:
        raise _WorkspaceTaskError("workspace_path_identity_changed") from None
    finally:
        for handle in reversed(handles):
            _close_pinned_windows_handle(handle)


def ensure_workspace_queue_directory(root: Path, directory: Path) -> bool:
    root_path = Path(root).absolute()
    directory_path = Path(directory).absolute()
    try:
        directory_path.relative_to(root_path)
        if (root_path.exists() and _is_link_like(root_path)) or (
            directory_path.exists() and _is_link_like(directory_path)
        ):
            return False
        directory_path.mkdir(parents=True, exist_ok=True)
        if _is_link_like(root_path) or _is_link_like(directory_path):
            return False
        if directory_path.resolve() != directory_path:
            return False
        os.chmod(directory_path, 0o700)
        return True
    except (OSError, ValueError):
        return False


def _read_text_file(
    path: Path,
    *,
    project_root: Path | None = None,
) -> tuple[bytes, str]:
    try:
        pin = (
            _pinned_workspace_ancestors(project_root, path)
            if project_root is not None
            else nullcontext()
        )
        with pin:
            before = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or int(before.st_nlink) != 1:
                raise _WorkspaceTaskError("workspace_hardlink_denied")
            if int(before.st_size) > _MAX_FILE_BYTES:
                raise _WorkspaceTaskError("workspace_file_too_large")
            with path.open("rb") as handle:
                opened_before = os.fstat(handle.fileno())
                raw = handle.read(_MAX_FILE_BYTES + 1)
                opened_after = os.fstat(handle.fileno())
            after = os.stat(path, follow_symlinks=False)
            exact_identity = (
                int(opened_before.st_dev),
                int(opened_before.st_ino),
                int(opened_before.st_mode),
            ) == (
                int(opened_after.st_dev),
                int(opened_after.st_ino),
                int(opened_after.st_mode),
            ) == (int(before.st_dev), int(before.st_ino), int(before.st_mode)) == (
                int(after.st_dev),
                int(after.st_ino),
                int(after.st_mode),
            )
            if not exact_identity:
                raise _WorkspaceTaskError("workspace_read_identity_changed")
            if (
                int(opened_before.st_nlink) != 1
                or int(opened_after.st_nlink) != 1
                or int(after.st_nlink) != 1
            ):
                raise _WorkspaceTaskError("workspace_hardlink_denied")
    except _WorkspaceTaskError:
        raise
    except OSError:
        raise _WorkspaceTaskError("workspace_read_failed", outcome="failed") from None
    if len(raw) > _MAX_FILE_BYTES:
        raise _WorkspaceTaskError("workspace_file_too_large")
    if b"\x00" in raw:
        raise _WorkspaceTaskError("workspace_binary_file_denied")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _WorkspaceTaskError("workspace_non_utf8_denied") from None


def _authority_edit_denied(relative_path: str) -> bool:
    filename = Path(relative_path).name.casefold()
    parts = tuple(part.casefold() for part in Path(relative_path).parts)
    return bool(
        filename in _PROTECTED_AUTHORITY_FILENAMES
        or (
            filename.endswith(".py")
            and (
                len(parts) == 1
                or parts[:-1] == ("evelyn_core", "runtime")
            )
        )
        or "/".join(parts) == "docs/99_project_inbox.md"
        or (parts and parts[0] == ".github")
        or "tests" in parts
        or (filename.startswith("test_") and filename.endswith(".py"))
        or filename.endswith("_test.py")
        or any(
            filename.endswith(f".test.{suffix}")
            for suffix in ("js", "mjs", "cjs", "ts")
        )
        or "launchers" in parts
        or "skills" in parts
        or filename.startswith("docker-compose")
        or filename.startswith("dockerfile")
        or Path(filename).suffix in {".bat", ".cmd", ".ps1", ".sh"}
        or (
            filename.startswith("requirements")
            and filename.endswith(".txt")
        )
        or filename in {
            "environment.yml",
            "environment.yaml",
            "poetry.lock",
            "uv.lock",
        }
        or (
            len(parts) >= 2
            and parts[0:2] == ("docs", "assets")
            and Path(filename).suffix in {".css", ".js"}
        )
        or "/".join(parts) == "docs/index.html"
        or any(marker in filename for marker in _AUTHORITY_NAME_MARKERS)
        or any(
            parts[index : index + 2] == ("skills", "task_loop")
            for index in range(len(parts) - 1)
        )
    )


def _iter_safe_files(
    project_root: Path,
    base: Path,
    *,
    external_tracked_paths: frozenset[str],
    incomplete: list[bool],
):
    stack = [base]
    visited = 0
    yielded = 0
    while stack:
        if visited >= _MAX_SEARCH_DIRECTORIES:
            incomplete[0] = True
            return
        current = stack.pop()
        visited += 1
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            incomplete[0] = True
            continue
        directories: list[Path] = []
        for child in children:
            try:
                relative = child.relative_to(project_root).parts
            except ValueError:
                incomplete[0] = True
                continue
            if (
                _is_sensitive(tuple(relative))
                or not _allowed_workspace_path(tuple(relative))
                or not _external_tracked_path_allowed(
                    tuple(relative),
                    external_tracked_paths,
                )
            ):
                continue
            try:
                child_stat = os.lstat(child)
                attributes = getattr(child_stat, "st_file_attributes", 0)
                is_junction = getattr(child, "is_junction", None)
                if (
                    stat.S_ISLNK(child_stat.st_mode)
                    or attributes
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    or (callable(is_junction) and is_junction())
                ):
                    continue
            except OSError:
                incomplete[0] = True
                continue
            if stat.S_ISDIR(child_stat.st_mode):
                directories.append(child)
            elif stat.S_ISREG(child_stat.st_mode):
                if int(child_stat.st_nlink) != 1:
                    incomplete[0] = True
                    continue
                if yielded >= _MAX_SEARCH_FILES:
                    incomplete[0] = True
                    return
                yielded += 1
                yield child
        stack.extend(reversed(directories))


def _execute_list(
    project_root: Path,
    args: dict[str, Any],
    *,
    external_tracked_paths: frozenset[str],
) -> dict[str, Any]:
    target, relative = _workspace_path(
        project_root,
        args["path"],
        external_tracked_paths=external_tracked_paths,
    )
    if not target.is_dir():
        raise _WorkspaceTaskError("workspace_directory_required")
    recursive = args["recursive"]
    if type(recursive) is not bool:
        raise _WorkspaceTaskError("workspace_args_invalid")
    entries: list[dict[str, Any]] = []
    truncated = False
    stack = [target]
    while stack:
        current = stack.pop()
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            raise _WorkspaceTaskError("workspace_list_failed", outcome="failed") from None
        directories: list[Path] = []
        for child in children:
            relative_parts = child.relative_to(project_root).parts
            if (
                _is_link_like(child)
                or _is_sensitive(tuple(relative_parts))
                or not _allowed_workspace_path(tuple(relative_parts))
                or not _external_tracked_path_allowed(
                    tuple(relative_parts),
                    external_tracked_paths,
                )
                or (child.is_file() and not _is_single_link_regular_file(child))
            ):
                continue
            entry = {
                "path": Path(*relative_parts).as_posix(),
                "type": "directory" if child.is_dir() else "file",
            }
            if child.is_file():
                try:
                    entry["bytes"] = int(child.stat().st_size)
                except OSError:
                    entry["bytes"] = None
            entries.append(entry)
            if len(entries) >= _MAX_LIST_ENTRIES:
                truncated = True
                break
            if recursive and child.is_dir():
                directories.append(child)
        if len(entries) >= _MAX_LIST_ENTRIES:
            break
        stack.extend(reversed(directories))
        if not recursive:
            break
    return _result(
        attempted=True,
        executed=True,
        observed=True,
        verified=True,
        outcome="succeeded",
        code="workspace_list_completed",
        summary="Workspace paths listed.",
        evidence={
            "path": relative,
            "recursive": recursive,
            "entries": entries,
            "truncated": truncated,
        },
    )


def _execute_search(
    project_root: Path,
    args: dict[str, Any],
    *,
    external_tracked_paths: frozenset[str],
) -> dict[str, Any]:
    query = args["query"]
    if not isinstance(query, str) or not query or len(query) > 256 or "\x00" in query or "\n" in query:
        raise _WorkspaceTaskError("workspace_query_invalid")
    target, relative = _workspace_path(
        project_root,
        args["path"],
        external_tracked_paths=external_tracked_paths,
    )
    scan_incomplete = [False]
    if target.is_file():
        files = (target,)
    elif target.is_dir():
        files = _iter_safe_files(
            project_root,
            target,
            external_tracked_paths=external_tracked_paths,
            incomplete=scan_incomplete,
        )
    else:
        raise _WorkspaceTaskError("workspace_path_not_found")
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    incomplete = False
    match_limit_reached = False
    for file_path in files:
        try:
            if file_path.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                incomplete = True
                continue
            _, text = _read_text_file(file_path, project_root=project_root)
        except (OSError, _WorkspaceTaskError):
            incomplete = True
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if needle not in line.casefold():
                continue
            clipped, line_truncated = _clip_utf8(line, 320)
            incomplete = incomplete or line_truncated
            matches.append(
                {
                    "path": file_path.relative_to(project_root).as_posix(),
                    "line": line_number,
                    "text": clipped,
                }
            )
            if len(matches) >= _MAX_SEARCH_MATCHES:
                incomplete = True
                match_limit_reached = True
                break
        if match_limit_reached:
            break
    return _result(
        attempted=True,
        executed=True,
        observed=True,
        verified=True,
        outcome="succeeded",
        code="workspace_search_completed",
        summary=f"Workspace search completed with {len(matches)} match(es).",
        evidence={
            "path": relative,
            "query": query,
            "matches": matches,
            "truncated": bool(incomplete or scan_incomplete[0]),
        },
    )


def _execute_read(
    project_root: Path,
    args: dict[str, Any],
    *,
    external_tracked_paths: frozenset[str],
) -> dict[str, Any]:
    target, relative = _workspace_path(
        project_root,
        args["path"],
        external_tracked_paths=external_tracked_paths,
    )
    if not target.is_file():
        raise _WorkspaceTaskError("workspace_file_required")
    raw, _text = _read_text_file(target, project_root=project_root)
    sha256 = hashlib.sha256(raw).hexdigest()
    offset = int(args.get("offset", 0))
    requested_length = int(args.get("length", _MAX_READ_CHUNK_BYTES))
    expected_sha256 = args.get("expectedSha256")
    if expected_sha256 is not None and expected_sha256 != sha256:
        raise _WorkspaceTaskError("workspace_read_sha256_mismatch")
    if offset > len(raw):
        raise _WorkspaceTaskError("workspace_read_offset_invalid")
    try:
        raw[:offset].decode("utf-8")
    except UnicodeDecodeError:
        raise _WorkspaceTaskError("workspace_read_offset_invalid") from None

    end = min(len(raw), offset + requested_length)
    evidence: dict[str, Any] | None = None
    while end >= offset:
        try:
            content = raw[offset:end].decode("utf-8")
        except UnicodeDecodeError:
            end -= 1
            continue
        candidate = {
            "path": relative,
            "sha256": sha256,
            "bytes": len(raw),
            "offset": offset,
            "length": end - offset,
            "nextOffset": end,
            "eof": end == len(raw),
            "content": content,
            "truncated": end != len(raw),
        }
        encoded = json.dumps(
            candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        transport_encoded = json.dumps(
            encoded,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if (
            len(encoded) <= _MAX_READ_EVIDENCE_CHARS
            and len(transport_encoded) <= _MAX_READ_EVIDENCE_CHARS
        ):
            evidence = candidate
            break
        end -= 1
    if evidence is None or (offset < len(raw) and evidence["length"] <= 0):
        raise _WorkspaceTaskError("workspace_read_chunk_unavailable")
    return _result(
        attempted=True,
        executed=True,
        observed=True,
        verified=True,
        outcome="succeeded",
        code="workspace_read_completed",
        summary="Workspace file chunk read.",
        evidence=evidence,
    )


def _validate_edit_args(args: dict[str, Any]) -> None:
    mode = args.get("mode")
    required = (
        {"mode", "path", "newText"}
        if mode == "create"
        else {"mode", "path", "oldText", "newText", "expectedSha256"}
        if mode == "replace"
        else set()
    )
    if not required or set(args) != required:
        raise _WorkspaceTaskError("workspace_args_invalid")
    if not isinstance(args.get("newText"), str):
        raise _WorkspaceTaskError("workspace_args_invalid")
    try:
        new_text_bytes = args["newText"].encode("utf-8")
        old_text_bytes = (
            args["oldText"].encode("utf-8")
            if mode == "replace" and isinstance(args.get("oldText"), str)
            else b""
        )
    except UnicodeEncodeError:
        raise _WorkspaceTaskError("workspace_args_invalid") from None
    if (mode == "create" and not new_text_bytes) or "\x00" in args["newText"] or (
        mode == "replace"
        and (
            not old_text_bytes
            or "\x00" in str(args.get("oldText") or "")
            or len(old_text_bytes) > _MAX_FILE_BYTES
        )
    ):
        raise _WorkspaceTaskError("workspace_args_invalid")
    if len(new_text_bytes) > _MAX_EDIT_BYTES:
        raise _WorkspaceTaskError("workspace_edit_too_large")


def _path_identity(path: Path) -> tuple[int, int, int]:
    try:
        value = os.lstat(path)
    except OSError:
        raise _WorkspaceTaskError("workspace_path_identity_unavailable") from None
    return int(value.st_dev), int(value.st_ino), int(value.st_mode)


def _git_target_status(
    project_root: Path,
    relative: str,
    run_command: Callable[..., Any],
) -> tuple[str, str, bool]:
    common = {
        "cwd": str(project_root),
        "capture_output": True,
        "text": True,
        "timeout": 5.0,
        "check": False,
        "shell": False,
        "env": _sanitized_environment(),
    }
    try:
        status_result = run_command(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-c",
                "core.fsmonitor=false",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=all",
                "--",
                relative,
            ],
            **common,
        )
        tracked_result = run_command(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            **common,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _WorkspaceTaskError("workspace_git_status_unavailable") from None
    if int(getattr(status_result, "returncode", -1)) != 0:
        raise _WorkspaceTaskError("workspace_git_status_unavailable")
    git_status = str(getattr(status_result, "stdout", "") or "").rstrip("\r\n")
    if len(git_status.encode("utf-8")) > 4096 or "\n" in git_status or "\r" in git_status:
        raise _WorkspaceTaskError("workspace_git_status_invalid")
    tracked = int(getattr(tracked_result, "returncode", -1)) == 0
    target = project_root / Path(relative)
    if not git_status:
        dirty_status = "clean" if target.exists() and tracked else "untracked" if target.exists() else "absent"
    elif git_status.startswith("?? "):
        dirty_status = "untracked"
    elif not target.exists():
        dirty_status = "deleted"
    else:
        index_state = git_status[0]
        worktree_state = git_status[1]
        dirty_status = (
            "modified_and_staged"
            if index_state != " " and worktree_state != " "
            else "staged"
            if index_state != " "
            else "modified"
        )
    return git_status, dirty_status, tracked


def _prepare_workspace_edit(
    project_root: Path,
    args: dict[str, Any],
) -> dict[str, Any]:
    target, _relative = _workspace_path(
        project_root,
        args.get("path"),
        allow_untracked_external=True,
    )
    with _pinned_workspace_ancestors(project_root, target):
        return _prepare_workspace_edit_pinned(project_root, args)


def _prepare_workspace_edit_pinned(
    project_root: Path,
    args: dict[str, Any],
) -> dict[str, Any]:
    _validate_edit_args(args)
    target, relative = _workspace_path(
        project_root,
        args["path"],
        allow_untracked_external=True,
    )
    if _authority_edit_denied(relative):
        raise _WorkspaceTaskError("workspace_authority_edit_denied")
    if _workspace_recovery_artifacts(target):
        raise _WorkspaceTaskError("workspace_edit_recovery_required")
    mode = args["mode"]
    if mode == "create":
        if target.exists():
            raise _WorkspaceTaskError("workspace_create_target_exists")
        if not target.parent.is_dir():
            raise _WorkspaceTaskError("workspace_parent_directory_required")
        before = b""
        before_text = ""
        base_sha = WORKSPACE_EDIT_ABSENT_SHA
        identity = None
    else:
        if not target.is_file():
            raise _WorkspaceTaskError("workspace_file_required")
        if _workspace_nondefault_stream_count(target):
            raise _WorkspaceTaskError("workspace_nondefault_stream_denied")
        old_text = args.get("oldText")
        expected_sha = args.get("expectedSha256")
        if (
            not isinstance(old_text, str)
            or not old_text
            or not isinstance(expected_sha, str)
            or not _SHA256_PATTERN.fullmatch(expected_sha)
        ):
            raise _WorkspaceTaskError("workspace_args_invalid")
        before, before_text = _read_text_file(target, project_root=project_root)
        base_sha = hashlib.sha256(before).hexdigest()
        if not hmac.compare_digest(base_sha, expected_sha):
            raise _WorkspaceTaskError("workspace_sha_mismatch")
        occurrences = before_text.count(old_text)
        if occurrences == 0:
            raise _WorkspaceTaskError("workspace_old_text_not_found")
        if occurrences != 1:
            raise _WorkspaceTaskError("workspace_old_text_ambiguous")
        identity = _path_identity(target)
    candidate_text = (
        args["newText"]
        if mode == "create"
        else before_text.replace(args["oldText"], args["newText"], 1)
    )
    candidate = candidate_text.encode("utf-8")
    if len(candidate) > _MAX_EDIT_BYTES:
        raise _WorkspaceTaskError("workspace_edit_too_large")
    if mode == "replace" and candidate == before:
        raise _WorkspaceTaskError("workspace_edit_no_change")
    return {
        "target": target,
        "relative": relative,
        "mode": mode,
        "before": before,
        "beforeText": before_text,
        "baseSha256": base_sha,
        "candidate": candidate,
        "candidateText": candidate_text,
        "candidateSha256": hashlib.sha256(candidate).hexdigest(),
        "targetIdentity": identity,
        "parentIdentity": _path_identity(target.parent),
    }


def expire_workspace_edit_stages(
    stages: dict[str, dict[str, Any]],
    *,
    current: float,
) -> None:
    for stage_id, record in tuple(stages.items()):
        if float(record.get("expiresAt") or 0.0) <= float(current):
            stages.pop(stage_id, None)


def _diff_lines(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n\\ No newline at end of file\n"
    return lines


def stage_workspace_edit(
    *,
    project_root: Path,
    args: dict[str, Any],
    task_id: str,
    grant_id: str,
    action_run_id: str,
    step_id: int,
    surface: str,
    host_instance_id: str,
    stages: dict[str, dict[str, Any]],
    requires_sandbox_test: bool = False,
    run_command: Callable[..., Any] = subprocess.run,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    current = float(now())
    if surface != "control_page" or type(requires_sandbox_test) is not bool:
        return _blocked("workspace_edit_surface_denied")
    expire_workspace_edit_stages(stages, current=current)
    if len(stages) >= WORKSPACE_EDIT_MAX_STAGES:
        return _blocked("workspace_edit_stage_capacity_reached", attempted=True)
    try:
        prepared = _prepare_workspace_edit(Path(project_root).resolve(), args)
        git_status, dirty_status, tracked = _git_target_status(
            Path(project_root).resolve(),
            prepared["relative"],
            run_command,
        )
        before_lines = _diff_lines(prepared["beforeText"])
        candidate_lines = _diff_lines(prepared["candidateText"])
        full_diff = "".join(
            difflib.unified_diff(
                before_lines,
                candidate_lines,
                fromfile=f"a/{prepared['relative']}",
                tofile=f"b/{prepared['relative']}",
                n=max(3, len(before_lines), len(candidate_lines)),
            )
        )
        if len(full_diff.encode("utf-8")) > WORKSPACE_EDIT_MAX_PREVIEW_BYTES:
            raise _WorkspaceTaskError("workspace_edit_preview_too_large")
        stage_id = f"stage-{uuid.uuid4().hex}"
        expires_at = current + WORKSPACE_EDIT_STAGE_TTL_SEC
        diff_sha = hashlib.sha256(full_diff.encode("utf-8")).hexdigest()
        args_hash = workspace_task_args_hash(args)
        dirty_required = dirty_status not in {"clean", "absent"}
        public = {
            "stageId": stage_id,
            "hostInstanceId": host_instance_id,
            "path": prepared["relative"],
            "mode": prepared["mode"],
            "baseSha256": prepared["baseSha256"],
            "candidateSha256": prepared["candidateSha256"],
            "diffSha256": diff_sha,
            "fullDiff": full_diff,
            "diffTruncated": False,
            "gitStatus": git_status,
            "dirtyStatus": dirty_status,
            "tracked": tracked,
            "dirtyBaseAcknowledgementRequired": dirty_required,
            "bytes": len(prepared["candidate"]),
            "issuedAt": current,
            "expiresAt": expires_at,
            "argsHash": args_hash,
        }
        public["previewDigest"] = hashlib.sha256(
            _canonical_json_bytes(public)
        ).hexdigest()
        if len(
            json.dumps(public, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ) > WORKSPACE_TASK_MAX_RESPONSE_BYTES // 2:
            raise _WorkspaceTaskError("workspace_edit_preview_too_large")
        stages[stage_id] = {
            **public,
            "taskId": task_id,
            "grantId": grant_id,
            "actionRunId": action_run_id,
            "stepId": step_id,
            "surface": surface,
            "tool": "edit",
            "requiresSandboxTest": requires_sandbox_test,
            "candidateBytes": prepared["candidate"],
            "targetIdentity": prepared["targetIdentity"],
            "parentIdentity": prepared["parentIdentity"],
            "testedBaseTreeSha256": "",
            "testedCandidateTreeSha256": "",
            "testedRunner": "",
            "testedTargets": (),
            "testedTestsRun": 0,
            "testedSemanticVerified": None,
            "testedAt": 0.0,
        }
        return _result(
            attempted=True,
            executed=True,
            observed=True,
            verified=True,
            outcome="succeeded",
            code="workspace_edit_staged",
            summary="Workspace edit staged; no file was changed.",
            evidence=public,
        )
    except _WorkspaceTaskError as exc:
        return _blocked(exc.code, attempted=True)
    except Exception:
        return _unverified(
            "workspace_edit_stage_outcome_unverified",
            "Workspace edit staging outcome is unverified.",
        )


def _sanitized_environment() -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "NO_COLOR": "1",
        }
    )
    return environment


def _tracked_manifest_paths(
    raw: bytes,
    *,
    prefix: tuple[str, ...],
    external_only: bool = True,
) -> set[str]:
    if len(raw) > 4 * 1024 * 1024 or (raw and not raw.endswith(b"\x00")):
        raise _WorkspaceTaskError("workspace_external_manifest_invalid")
    result: set[str] = set()
    seen_casefolded: set[str] = set()
    for encoded in raw.rstrip(b"\x00").split(b"\x00") if raw else ():
        try:
            value = encoded.decode("utf-8").replace("\\", "/")
        except UnicodeDecodeError:
            raise _WorkspaceTaskError("workspace_external_manifest_invalid") from None
        parts = tuple(value.split("/"))
        if (
            not parts
            or any(not part or part in {".", ".."} or _invalid_path_component(part) for part in parts)
        ):
            raise _WorkspaceTaskError("workspace_external_manifest_invalid")
        full = prefix + parts
        if (
            external_only
            and full[0].casefold() != "external"
        ) or (
            _is_sensitive(full)
            or not _allowed_workspace_path(full)
        ):
            continue
        if (
            not full
            or any(_invalid_path_component(part) for part in full)
        ):
            raise _WorkspaceTaskError("workspace_external_manifest_invalid")
        normalized = Path(*full).as_posix()
        folded = normalized.casefold()
        if folded in seen_casefolded:
            raise _WorkspaceTaskError("workspace_external_manifest_ambiguous")
        seen_casefolded.add(folded)
        for length in range(1, len(full) + 1):
            result.add(Path(*full[:length]).as_posix())
    return result


def _merge_tracked_manifest_paths(
    destination: set[str],
    incoming: set[str],
) -> None:
    folded = {path.casefold(): path for path in destination}
    for path in incoming:
        existing = folded.get(path.casefold())
        if existing is not None and existing != path:
            raise _WorkspaceTaskError("workspace_external_manifest_ambiguous")
        folded[path.casefold()] = path
        destination.add(path)


def build_external_tracked_manifest(
    project_root: Path,
    *,
    run_command: Callable[..., Any] = subprocess.run,
) -> frozenset[str]:
    """Capture a fail-closed positive manifest for the two vendored trees."""

    root = Path(project_root).resolve()
    sources: list[tuple[Path, tuple[str, ...], list[str]]] = []
    mindcraft_evelyn = root / "external" / "mindcraft_evelyn"
    if mindcraft_evelyn.is_dir() and not _is_link_like(mindcraft_evelyn):
        sources.append(
            (
                root,
                (),
                [
                    "git",
                    "-c",
                    "core.quotepath=false",
                    "-c",
                    "core.fsmonitor=false",
                    "ls-files",
                    "-z",
                    "--",
                    "external/mindcraft_evelyn",
                ],
            )
        )
    mindcraft = root / "external" / "mindcraft"
    if mindcraft.is_dir() and not _is_link_like(mindcraft):
        sources.append(
            (
                mindcraft,
                ("external", "mindcraft"),
                [
                    "git",
                    "-c",
                    f"safe.directory={mindcraft.as_posix()}",
                    "-c",
                    "core.quotepath=false",
                    "-c",
                    "core.fsmonitor=false",
                    "ls-files",
                    "-z",
                    "--",
                ],
            )
        )
    paths: set[str] = set()
    try:
        for cwd, prefix, command in sources:
            completed = run_command(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=False,
                timeout=2.0,
                check=False,
                shell=False,
                env=_sanitized_environment(),
            )
            stdout = getattr(completed, "stdout", b"")
            if int(getattr(completed, "returncode", -1)) != 0 or not isinstance(stdout, bytes):
                raise _WorkspaceTaskError("workspace_external_manifest_unavailable")
            _merge_tracked_manifest_paths(
                paths,
                _tracked_manifest_paths(stdout, prefix=prefix),
            )
    except (OSError, subprocess.TimeoutExpired, _WorkspaceTaskError):
        return frozenset()
    return frozenset(paths)


def build_workspace_tracked_manifest(
    project_root: Path,
    *,
    run_command: Callable[..., Any] = subprocess.run,
) -> frozenset[str]:
    """Freeze the safe, tracked workspace tree used by candidate tests."""

    root = Path(project_root).resolve()
    sources: list[tuple[Path, tuple[str, ...], list[str]]] = [
        (
            root,
            (),
            [
                "git",
                "-c",
                "core.quotepath=false",
                "-c",
                "core.fsmonitor=false",
                "ls-files",
                "-z",
                "--",
            ],
        )
    ]
    mindcraft = root / "external" / "mindcraft"
    if mindcraft.is_dir() and not _is_link_like(mindcraft):
        sources.append(
            (
                mindcraft,
                ("external", "mindcraft"),
                [
                    "git",
                    "-c",
                    f"safe.directory={mindcraft.as_posix()}",
                    "-c",
                    "core.quotepath=false",
                    "-c",
                    "core.fsmonitor=false",
                    "ls-files",
                    "-z",
                    "--",
                ],
            )
        )
    paths: set[str] = set()
    try:
        for cwd, prefix, command in sources:
            completed = run_command(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=False,
                timeout=2.0,
                check=False,
                shell=False,
                env=_sanitized_environment(),
            )
            stdout = getattr(completed, "stdout", b"")
            if (
                int(getattr(completed, "returncode", -1)) != 0
                or not isinstance(stdout, bytes)
            ):
                raise _WorkspaceTaskError("workspace_tracked_manifest_unavailable")
            _merge_tracked_manifest_paths(
                paths,
                _tracked_manifest_paths(
                    stdout,
                    prefix=prefix,
                    external_only=False,
                ),
            )
    except (OSError, subprocess.TimeoutExpired, _WorkspaceTaskError):
        return frozenset()
    return frozenset(paths)


def _command_evidence(completed: Any, *, key: str) -> dict[str, Any]:
    stdout, stdout_truncated = _clip_utf8(getattr(completed, "stdout", ""), WORKSPACE_TASK_MAX_OUTPUT_BYTES // 2)
    stderr, stderr_truncated = _clip_utf8(getattr(completed, "stderr", ""), WORKSPACE_TASK_MAX_OUTPUT_BYTES // 2)
    return {
        key: stdout,
        "stderr": stderr,
        "exitCode": int(getattr(completed, "returncode", -1)),
        "truncated": bool(stdout_truncated or stderr_truncated),
    }


def _execute_diff(
    project_root: Path,
    args: dict[str, Any],
    run_command: Callable[..., Any],
    timeout_sec: float,
    *,
    external_tracked_paths: frozenset[str],
) -> dict[str, Any]:
    values = args["paths"]
    if not isinstance(values, list) or not 1 <= len(values) <= _MAX_TARGETS:
        raise _WorkspaceTaskError("workspace_args_invalid")
    paths: list[str] = []
    for value in values:
        target, relative = _workspace_path(
            project_root,
            value,
            external_tracked_paths=external_tracked_paths,
        )
        if relative == "." or not target.is_file():
            raise _WorkspaceTaskError("workspace_diff_file_required")
        paths.append(relative)
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "diff",
        "HEAD",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--unified=3",
        "--",
        *(f":(literal){path}" for path in paths),
    ]
    tracking_timeout = min(2.0, timeout_sec / 2.0)
    diff_timeout = timeout_sec - tracking_timeout
    try:
        completed = run_command(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=diff_timeout,
            check=False,
            shell=False,
            env=_sanitized_environment(),
        )
    except subprocess.TimeoutExpired:
        return _unverified("workspace_diff_outcome_unverified", "Workspace diff outcome is unverified after timeout.")
    except OSError:
        return _result(
            attempted=True,
            executed=False,
            observed=True,
            verified=True,
            outcome="failed",
            code="workspace_git_unavailable",
            summary="Workspace diff tool is unavailable.",
        )
    succeeded = int(getattr(completed, "returncode", -1)) == 0
    evidence = _command_evidence(completed, key="diff")
    evidence["paths"] = paths
    tracked_paths_complete = False
    if succeeded:
        try:
            tracked = run_command(
                [
                    "git",
                    "-c",
                    "core.fsmonitor=false",
                    "ls-files",
                    "-z",
                    "--",
                    *(f":(literal){path}" for path in paths),
                ],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=tracking_timeout,
                check=False,
                shell=False,
                env=_sanitized_environment(),
            )
            tracked_stdout = getattr(tracked, "stdout", "")
            tracked_paths_complete = bool(
                int(getattr(tracked, "returncode", -1)) == 0
                and isinstance(tracked_stdout, str)
                and set(paths).issubset(
                    {item for item in tracked_stdout.split("\x00") if item}
                )
            )
        except (OSError, subprocess.TimeoutExpired):
            tracked_paths_complete = False
    evidence["truncated"] = bool(
        evidence.get("truncated") or not tracked_paths_complete
    )
    return _result(
        attempted=True,
        executed=True,
        observed=True,
        verified=True,
        outcome="succeeded" if succeeded else "failed",
        code="workspace_diff_completed" if succeeded else "workspace_diff_failed",
        summary="Workspace diff collected." if succeeded else "Workspace diff failed.",
        evidence=evidence,
    )


def _validate_args(tool: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise _WorkspaceTaskError("workspace_args_invalid")
    if tool == "edit":
        _validate_edit_args(args)
        return args
    if tool == "read":
        if set(args) == {"path"}:
            return args
        if set(args) != {"path", "offset", "length", "expectedSha256"}:
            raise _WorkspaceTaskError("workspace_args_invalid")
        offset = args.get("offset")
        length = args.get("length")
        expected_sha256 = args.get("expectedSha256")
        if (
            type(offset) is not int
            or not 0 <= offset <= _MAX_FILE_BYTES
            or type(length) is not int
            or not 1 <= length <= _MAX_READ_CHUNK_BYTES
            or not isinstance(expected_sha256, str)
            or not _SHA256_PATTERN.fullmatch(expected_sha256)
        ):
            raise _WorkspaceTaskError("workspace_args_invalid")
        return args
    expected = _ARG_KEYS.get(tool)
    if expected is None or set(args) != expected:
        raise _WorkspaceTaskError("workspace_args_invalid")
    return args


def execute_workspace_task_tool(
    *,
    project_root: Path,
    tool: str,
    args: dict[str, Any],
    run_command: Callable[..., Any] = subprocess.run,
    timeout_sec: float = WORKSPACE_TASK_COMMAND_TIMEOUT_SEC,
    external_tracked_paths: frozenset[str] | None = None,
) -> dict[str, Any]:
    normalized_tool = str(tool or "").strip()
    if normalized_tool not in WORKSPACE_TASK_TOOL_NAMES:
        return _blocked("workspace_tool_not_allowed")
    try:
        safe_args = _validate_args(normalized_tool, args)
        root = Path(project_root).resolve()
        tracked_paths = (
            build_external_tracked_manifest(root, run_command=run_command)
            if external_tracked_paths is None
            else frozenset(external_tracked_paths)
        )
        if normalized_tool == "list":
            return _execute_list(
                root,
                safe_args,
                external_tracked_paths=tracked_paths,
            )
        if normalized_tool == "search":
            return _execute_search(
                root,
                safe_args,
                external_tracked_paths=tracked_paths,
            )
        if normalized_tool == "read":
            return _execute_read(
                root,
                safe_args,
                external_tracked_paths=tracked_paths,
            )
        if normalized_tool == "edit":
            return _blocked("workspace_host_authorization_required", attempted=True)
        if normalized_tool == "test":
            return _blocked(
                "workspace_test_sandbox_required",
                "Workspace tests require an isolated sandbox.",
                attempted=True,
            )
        return _execute_diff(
            root,
            safe_args,
            run_command,
            max(
                0.1,
                min(float(timeout_sec), WORKSPACE_TASK_COMMAND_TIMEOUT_SEC),
            ),
            external_tracked_paths=tracked_paths,
        )
    except _WorkspaceTaskError as exc:
        return _blocked(exc.code, attempted=True)
    except Exception:
        return _unverified("workspace_task_outcome_unverified", "Workspace task outcome is unverified.")


def _valid_time(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _safe_identifier(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    return text if _IDENTIFIER_PATTERN.fullmatch(text) else ""


def _safe_sha256(value: Any, *, allow_absent: bool = False) -> str:
    text = value if isinstance(value, str) else ""
    if allow_absent and text == WORKSPACE_EDIT_ABSENT_SHA:
        return text
    return text if _SHA256_PATTERN.fullmatch(text) else ""


def _mutation_response_binding(request: Any) -> dict[str, Any]:
    value = request if isinstance(request, dict) else {}
    return {
        "schema": WORKSPACE_MUTATION_RESPONSE_SCHEMA,
        "operation": value.get("operation") if value.get("operation") in {"apply", "cancel"} else "",
        "requestId": _safe_identifier(value.get("requestId")),
        "approvalId": _safe_identifier(value.get("approvalId")),
        "claimId": _safe_identifier(value.get("claimId")),
        "stageId": _safe_identifier(value.get("stageId")),
        "hostInstanceId": _safe_identifier(value.get("hostInstanceId")),
        "taskId": _safe_identifier(value.get("taskId")),
        "grantId": _safe_identifier(value.get("grantId")),
        "grantExpiresAt": (
            value.get("grantExpiresAt")
            if _valid_time(value.get("grantExpiresAt"))
            else None
        ),
        "actionRunId": _safe_identifier(value.get("actionRunId")),
        "stepId": value.get("stepId") if type(value.get("stepId")) is int and value["stepId"] >= 0 else None,
        "surface": _safe_identifier(value.get("surface")),
        "tool": value.get("tool") if value.get("tool") == "edit" else "",
        "argsHash": _safe_sha256(value.get("argsHash")),
        "baseSha256": _safe_sha256(value.get("baseSha256"), allow_absent=True),
        "candidateSha256": _safe_sha256(value.get("candidateSha256")),
        "previewDigest": _safe_sha256(value.get("previewDigest")),
        "dirtyBaseAcknowledged": value.get("dirtyBaseAcknowledged") if type(value.get("dirtyBaseAcknowledged")) is bool else False,
        "issuedAt": value.get("issuedAt") if _valid_time(value.get("issuedAt")) else None,
        "expiresAt": value.get("expiresAt") if _valid_time(value.get("expiresAt")) else None,
    }


def _signed_workspace_mutation_response(
    request: Any,
    *,
    auth_token: str | None,
    responded_at: float,
    result: dict[str, Any],
) -> dict[str, Any]:
    return _sign_workspace_mutation_payload(
        {
            **_mutation_response_binding(request),
            "respondedAt": float(responded_at),
            "result": result,
        },
        auth_token=auth_token,
        domain=WORKSPACE_MUTATION_RESPONSE_AUTH_DOMAIN,
    )


def _stage_matches_mutation_request(
    stage: dict[str, Any],
    request: dict[str, Any],
) -> bool:
    return all(
        stage.get(key) == request.get(key)
        for key in (
            "stageId",
            "hostInstanceId",
            "taskId",
            "grantId",
            "actionRunId",
            "stepId",
            "surface",
            "tool",
            "argsHash",
            "baseSha256",
            "candidateSha256",
            "previewDigest",
        )
    )


def _workspace_file_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        if (
            _is_link_like(path)
            or not path.is_file()
            or not _is_single_link_regular_file(path)
        ):
            return None
        identity = _path_identity(path)
        if int(os.lstat(path).st_size) > _MAX_FILE_BYTES:
            return None
        with path.open("rb") as handle:
            raw = handle.read(_MAX_FILE_BYTES + 1)
        if (
            len(raw) > _MAX_FILE_BYTES
            or _path_identity(path) != identity
            or not _is_single_link_regular_file(path)
        ):
            return None
        return {
            "identity": identity,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "streamCount": _workspace_nondefault_stream_count(path),
            "bytes": raw,
        }
    except (OSError, _WorkspaceTaskError):
        return None


def _snapshot_matches(
    snapshot: dict[str, Any] | None,
    *,
    identity: tuple[int, int, int],
    sha256: str,
    stream_count: int = 0,
) -> bool:
    return bool(
        snapshot is not None
        and snapshot.get("identity") == identity
        and snapshot.get("sha256") == sha256
        and snapshot.get("streamCount") == stream_count
    )


def _workspace_exchange_path(target: Path, kind: str) -> Path:
    return target.with_name(
        f".{target.name}.evelyn-{kind}-{uuid.uuid4().hex}.tmp"
    )


def _write_durable_workspace_candidate(path: Path, candidate: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(candidate)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_replace_with_backup(
    target: Path,
    candidate: Path,
    backup: Path,
) -> None:
    """Atomically exchange a durable candidate while retaining displaced bytes."""

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
        replace_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        )
        replace_file.restype = wintypes.BOOL
        if not replace_file(
            str(target),
            str(candidate),
            str(backup),
            0x00000001,  # REPLACEFILE_WRITE_THROUGH
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    if os.name != "posix":
        raise OSError("workspace_conditional_replace_unavailable")

    import ctypes

    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise OSError("workspace_conditional_replace_unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if renameat2(
        -100,
        os.fsencode(candidate),
        -100,
        os.fsencode(target),
        0x00000002,  # RENAME_EXCHANGE
    ) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    os.replace(candidate, backup)
    descriptor = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_workspace_exchange_path(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not path.exists()


def _displaced_workspace_path(
    *,
    candidate_path: Path,
    backup_path: Path,
    candidate_snapshot: dict[str, Any],
) -> Path | None:
    if backup_path.exists():
        return backup_path
    candidate_after = _workspace_file_snapshot(candidate_path)
    if candidate_after is not None and not _snapshot_matches(
        candidate_after,
        identity=candidate_snapshot["identity"],
        sha256=candidate_snapshot["sha256"],
        stream_count=0,
    ):
        return candidate_path
    return None


def _restore_displaced_workspace_file(
    *,
    target: Path,
    displaced_path: Path,
    displaced_snapshot: dict[str, Any],
    candidate_snapshot: dict[str, Any],
) -> bool:
    rollback_path = _workspace_exchange_path(target, "rollback")
    exchange_failed = False
    try:
        _atomic_replace_with_backup(target, displaced_path, rollback_path)
    except OSError:
        exchange_failed = True
    restored = _workspace_file_snapshot(target)
    rolled_back = _workspace_file_snapshot(rollback_path)
    exact_restore = bool(
        _snapshot_matches(
            restored,
            identity=displaced_snapshot["identity"],
            sha256=displaced_snapshot["sha256"],
            stream_count=int(displaced_snapshot["streamCount"]),
        )
        and _snapshot_matches(
            rolled_back,
            identity=candidate_snapshot["identity"],
            sha256=candidate_snapshot["sha256"],
            stream_count=0,
        )
    )
    if exchange_failed or not exact_restore:
        return False
    return _remove_workspace_exchange_path(rollback_path)


def _conditional_replace_workspace_file(
    *,
    target: Path,
    candidate: bytes,
    expected_identity: tuple[int, int, int],
    expected_sha256: str,
) -> tuple[str, bytes | None]:
    candidate_path = _workspace_exchange_path(target, "candidate")
    backup_path = _workspace_exchange_path(target, "backup")
    try:
        _write_durable_workspace_candidate(candidate_path, candidate)
    except OSError:
        return (
            ("outcome_unverified", None)
            if _remove_workspace_exchange_path(candidate_path)
            else ("recovery_required", None)
        )
    candidate_snapshot = _workspace_file_snapshot(candidate_path)
    if candidate_snapshot is None or not _snapshot_matches(
        candidate_snapshot,
        identity=candidate_snapshot["identity"],
        sha256=hashlib.sha256(candidate).hexdigest(),
        stream_count=0,
    ):
        return (
            ("outcome_unverified", None)
            if _remove_workspace_exchange_path(candidate_path)
            else ("recovery_required", None)
        )
    before_exchange = _workspace_file_snapshot(target)
    if not _snapshot_matches(
        before_exchange,
        identity=expected_identity,
        sha256=expected_sha256,
        stream_count=0,
    ):
        return (
            ("base_changed", None)
            if _remove_workspace_exchange_path(candidate_path)
            else ("recovery_required", None)
        )

    exchange_failed = False
    try:
        _atomic_replace_with_backup(target, candidate_path, backup_path)
    except OSError:
        exchange_failed = True
    displaced_path = _displaced_workspace_path(
        candidate_path=candidate_path,
        backup_path=backup_path,
        candidate_snapshot=candidate_snapshot,
    )
    if displaced_path is None:
        current = _workspace_file_snapshot(target)
        cleanup_ok = _remove_workspace_exchange_path(candidate_path)
        if not cleanup_ok or not _snapshot_matches(
            current,
            identity=expected_identity,
            sha256=expected_sha256,
            stream_count=0,
        ):
            return "recovery_required", None
        return "outcome_unverified", None

    displaced_snapshot = _workspace_file_snapshot(displaced_path)
    if displaced_snapshot is None:
        return "recovery_required", None
    displaced_is_base = _snapshot_matches(
        displaced_snapshot,
        identity=expected_identity,
        sha256=expected_sha256,
        stream_count=0,
    )
    if not displaced_is_base:
        return (
            ("base_changed", None)
            if _restore_displaced_workspace_file(
                target=target,
                displaced_path=displaced_path,
                displaced_snapshot=displaced_snapshot,
                candidate_snapshot=candidate_snapshot,
            )
            else ("recovery_required", None)
        )

    current = _workspace_file_snapshot(target)
    candidate_is_current = _snapshot_matches(
        current,
        identity=candidate_snapshot["identity"],
        sha256=candidate_snapshot["sha256"],
        stream_count=0,
    )
    if exchange_failed:
        return "recovery_required", None
    if not candidate_is_current:
        if current is None or not _remove_workspace_exchange_path(displaced_path):
            return "recovery_required", None
        return "outcome_unverified", None
    if not _remove_workspace_exchange_path(displaced_path):
        return "recovery_required", None
    return "succeeded", bytes(current["bytes"])


def _apply_staged_workspace_edit(
    *,
    project_root: Path,
    stage: dict[str, Any],
    approval_id: str,
    run_command: Callable[..., Any],
    external_tracked_paths: frozenset[str],
) -> dict[str, Any]:
    try:
        target, _relative = _workspace_path(
            project_root,
            stage.get("path"),
            allow_untracked_external=True,
        )
        with _pinned_workspace_ancestors(project_root, target):
            return _apply_staged_workspace_edit_pinned(
                project_root=project_root,
                stage=stage,
                approval_id=approval_id,
                run_command=run_command,
                external_tracked_paths=external_tracked_paths,
            )
    except _WorkspaceTaskError as exc:
        if exc.code == "workspace_edit_recovery_required":
            return _unverified(
                exc.code,
                "Workspace edit recovery is required before another apply.",
            )
        return _blocked(exc.code, attempted=True)
    except Exception:
        return _unverified(
            "workspace_edit_apply_outcome_unverified",
            "Workspace edit outcome is unverified.",
        )


def _apply_staged_workspace_edit_pinned(
    *,
    project_root: Path,
    stage: dict[str, Any],
    approval_id: str,
    run_command: Callable[..., Any],
    external_tracked_paths: frozenset[str],
) -> dict[str, Any]:
    try:
        target, relative = _workspace_path(
            project_root,
            stage["path"],
            allow_untracked_external=True,
        )
        if relative != stage["path"] or _authority_edit_denied(relative):
            raise _WorkspaceTaskError("workspace_authority_edit_denied")
        if _workspace_recovery_artifacts(target):
            raise _WorkspaceTaskError("workspace_edit_recovery_required")
        if stage.get("requiresSandboxTest") is True:
            try:
                from .workspace_test_sandbox import workspace_stage_tree_digests

                current_tree_digests = workspace_stage_tree_digests(
                    project_root,
                    stage=stage,
                    workspace_tracked_paths=external_tracked_paths,
                )
            except Exception:
                return _unverified(
                    "workspace_test_tree_outcome_unverified",
                    "Workspace test tree could not be reverified.",
                )
            if not hmac.compare_digest(
                str(current_tree_digests.get("baseTreeSha256") or ""),
                str(stage.get("testedBaseTreeSha256") or ""),
            ) or not hmac.compare_digest(
                str(current_tree_digests.get("candidateTreeSha256") or ""),
                str(stage.get("testedCandidateTreeSha256") or ""),
            ):
                raise _WorkspaceTaskError("workspace_test_tree_stale")
        git_status, dirty_status, tracked = _git_target_status(
            project_root,
            relative,
            run_command,
        )
        if (
            git_status != stage["gitStatus"]
            or dirty_status != stage["dirtyStatus"]
            or tracked is not stage["tracked"]
        ):
            raise _WorkspaceTaskError("workspace_edit_base_status_changed")
        candidate = stage["candidateBytes"]
        if (
            not isinstance(candidate, bytes)
            or hashlib.sha256(candidate).hexdigest() != stage["candidateSha256"]
        ):
            raise _WorkspaceTaskError("workspace_edit_stage_invalid")
        if stage["mode"] == "create":
            if target.exists() or _path_identity(target.parent) != stage["parentIdentity"]:
                raise _WorkspaceTaskError("workspace_edit_base_changed")
        else:
            if (
                not target.is_file()
                or _path_identity(target) != stage["targetIdentity"]
                or _path_identity(target.parent) != stage["parentIdentity"]
            ):
                raise _WorkspaceTaskError("workspace_edit_base_changed")
            if _workspace_nondefault_stream_count(target):
                raise _WorkspaceTaskError("workspace_nondefault_stream_denied")
            raw, _ = _read_text_file(target, project_root=project_root)
            if not hmac.compare_digest(
                hashlib.sha256(raw).hexdigest(),
                stage["baseSha256"],
            ):
                raise _WorkspaceTaskError("workspace_edit_base_changed")
    except _WorkspaceTaskError as exc:
        if exc.code == "workspace_edit_recovery_required":
            return _unverified(
                exc.code,
                "Workspace edit recovery is required before another apply.",
            )
        return _blocked(exc.code, attempted=True)
    except Exception:
        return _unverified(
            "workspace_edit_apply_outcome_unverified",
            "Workspace edit outcome is unverified.",
        )

    if stage["mode"] == "create":
        temporary = _workspace_exchange_path(target, "candidate")
        create_code = ""
        try:
            _write_durable_workspace_candidate(temporary, candidate)
            os.link(temporary, target)
        except FileExistsError:
            create_code = "workspace_edit_base_changed"
        except OSError:
            create_code = "workspace_edit_apply_outcome_unverified"
        if not _remove_workspace_exchange_path(temporary):
            return _unverified(
                "workspace_edit_recovery_required",
                "Workspace edit recovery is required before another apply.",
            )
        if create_code == "workspace_edit_base_changed":
            return _blocked(create_code, attempted=True)
        if create_code:
            return _unverified(
                "workspace_edit_apply_outcome_unverified",
                "Workspace edit outcome is unverified.",
            )
    else:
        replace_state, observed = _conditional_replace_workspace_file(
            target=target,
            candidate=candidate,
            expected_identity=stage["targetIdentity"],
            expected_sha256=stage["baseSha256"],
        )
        if replace_state == "base_changed":
            return _blocked("workspace_edit_base_changed", attempted=True)
        if replace_state == "recovery_required":
            return _unverified(
                "workspace_edit_recovery_required",
                "Workspace edit recovery is required before another apply.",
            )
        if replace_state != "succeeded" or observed is None:
            return _unverified(
                "workspace_edit_apply_outcome_unverified",
                "Workspace edit outcome is unverified.",
            )
    try:
        observed = target.read_bytes()
    except OSError:
        return _unverified(
            "workspace_edit_apply_outcome_unverified",
            "Workspace edit outcome is unverified.",
        )
    observed_sha = hashlib.sha256(observed).hexdigest()
    if observed != candidate or not hmac.compare_digest(
        observed_sha,
        stage["candidateSha256"],
    ):
        return _unverified(
            "workspace_edit_apply_outcome_unverified",
            "Workspace edit outcome is unverified.",
        )
    return _result(
        attempted=True,
        executed=True,
        observed=True,
        verified=True,
        outcome="succeeded",
        code=(
            "workspace_create_completed"
            if stage["mode"] == "create"
            else "workspace_edit_completed"
        ),
        summary="Workspace file created." if stage["mode"] == "create" else "Workspace file edited.",
        evidence={
            "approvalId": approval_id,
            "stageId": stage["stageId"],
            "hostInstanceId": stage["hostInstanceId"],
            "path": relative,
            "mode": stage["mode"],
            "beforeSha256": stage["baseSha256"],
            "sha256": observed_sha,
            "bytes": len(observed),
            **(
                {"semanticVerified": False}
                if stage.get("requiresSandboxTest") is True
                else {}
            ),
        },
    )


def handle_workspace_mutation_request(
    request: dict[str, Any],
    *,
    project_root: Path,
    host_instance_id: str,
    host_started_at: float,
    stages: dict[str, dict[str, Any]],
    auth_token: str | None = None,
    request_filename: str = "",
    consumed_request_ids: dict[str, float] | None = None,
    run_command: Callable[..., Any] = subprocess.run,
    external_tracked_paths: frozenset[str] | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    current = float(now())

    def respond(result: dict[str, Any]) -> dict[str, Any]:
        return _signed_workspace_mutation_response(
            request,
            auth_token=auth_token,
            responded_at=current,
            result=result,
        )

    value = request if isinstance(request, dict) else {}
    request_id = str(value.get("requestId") or "")
    operation = str(value.get("operation") or "")
    invalid = bool(
        not isinstance(request, dict)
        or set(request) != _MUTATION_REQUEST_KEYS
        or request.get("schema") != WORKSPACE_MUTATION_REQUEST_SCHEMA
        or operation not in {"apply", "cancel"}
        or request.get("hostInstanceId") != host_instance_id
        or not _IDENTIFIER_PATTERN.fullmatch(host_instance_id)
        or not _IDENTIFIER_PATTERN.fullmatch(request_id)
        or any(
            not _IDENTIFIER_PATTERN.fullmatch(str(request.get(key) or ""))
            for key in (
                "approvalId",
                "claimId",
                "stageId",
                "taskId",
                "grantId",
                "actionRunId",
                "surface",
            )
        )
        or request.get("tool") != "edit"
        or request.get("surface") != "control_page"
        or type(request.get("stepId")) is not int
        or int(request.get("stepId")) < 0
        or not _valid_time(request.get("grantExpiresAt"))
        or float(request.get("grantExpiresAt")) <= 0.0
        or not _SHA256_PATTERN.fullmatch(str(request.get("argsHash") or ""))
        or str(request.get("baseSha256") or "") != WORKSPACE_EDIT_ABSENT_SHA
        and not _SHA256_PATTERN.fullmatch(str(request.get("baseSha256") or ""))
        or not _SHA256_PATTERN.fullmatch(str(request.get("candidateSha256") or ""))
        or not _SHA256_PATTERN.fullmatch(str(request.get("previewDigest") or ""))
        or type(request.get("dirtyBaseAcknowledged")) is not bool
        or not _valid_time(request.get("issuedAt"))
        or not _valid_time(request.get("expiresAt"))
        or request_filename != f"{request_id}.json"
    )
    if invalid:
        return respond(_blocked("workspace_mutation_request_invalid"))
    if not _valid_workspace_mutation_auth_token(auth_token):
        return respond(_blocked("workspace_mutation_auth_unavailable"))
    issued_at = float(request["issuedAt"])
    expires_at = float(request["expiresAt"])
    if (
        expires_at <= issued_at
        or expires_at - issued_at > WORKSPACE_MUTATION_REQUEST_TTL_SEC
        or issued_at > current + 5.0
    ):
        return respond(_blocked("workspace_mutation_time_invalid"))
    if issued_at < float(host_started_at):
        return respond(_blocked("workspace_mutation_pre_restart"))
    if current >= expires_at or expires_at - current <= 0.25:
        return respond(_blocked("workspace_mutation_expired"))
    if not _workspace_mutation_payload_is_authentic(
        request,
        auth_token=auth_token,
        domain=WORKSPACE_MUTATION_REQUEST_AUTH_DOMAIN,
    ):
        return respond(_blocked("workspace_mutation_auth_invalid"))
    if consumed_request_ids is not None:
        if request_id in consumed_request_ids:
            return respond(_blocked("workspace_mutation_replayed"))
        consumed_request_ids[request_id] = expires_at
    expire_workspace_edit_stages(stages, current=current)
    stage = stages.get(str(request["stageId"]))
    if stage is None:
        return respond(_blocked("workspace_edit_stage_unavailable"))
    if issued_at < float(stage["issuedAt"]):
        return respond(_blocked("workspace_mutation_pre_stage"))
    if not _stage_matches_mutation_request(stage, request):
        return respond(_blocked("workspace_edit_stage_binding_mismatch"))
    if (
        operation == "apply"
        and
        stage["dirtyBaseAcknowledgementRequired"]
        and request["dirtyBaseAcknowledged"] is not True
    ):
        return respond(_blocked("workspace_dirty_base_acknowledgement_required"))
    if operation == "apply" and stage.get("requiresSandboxTest") is True:
        if (
            stage.get("testedRunner") != "python_unittest"
            or not stage.get("testedTargets")
            or type(stage.get("testedTestsRun")) is not int
            or int(stage["testedTestsRun"]) < 1
            or stage.get("testedSemanticVerified") is not False
            or not _SHA256_PATTERN.fullmatch(
                str(stage.get("testedBaseTreeSha256") or "")
            )
            or not _SHA256_PATTERN.fullmatch(
                str(stage.get("testedCandidateTreeSha256") or "")
            )
        ):
            return respond(_blocked("workspace_sandbox_test_required", attempted=True))
    if (
        operation == "apply"
        and float(request["grantExpiresAt"]) - float(now()) <= 0.25
    ):
        return respond(_blocked("task_grant_expired"))
    stages.pop(stage["stageId"], None)
    if operation == "cancel":
        return respond(
            _result(
                attempted=True,
                executed=True,
                observed=True,
                verified=True,
                outcome="succeeded",
                code="workspace_edit_stage_cancelled",
                summary="Workspace edit stage cancelled; no file was changed.",
                evidence={
                    "approvalId": request["approvalId"],
                    "stageId": stage["stageId"],
                    "hostInstanceId": host_instance_id,
                },
            )
        )
    return respond(
        _apply_staged_workspace_edit(
            project_root=Path(project_root).resolve(),
            stage=stage,
            approval_id=str(request["approvalId"]),
            run_command=run_command,
            external_tracked_paths=frozenset(external_tracked_paths or ()),
        )
    )


def _response_binding(request: Any, host_instance_id: str) -> dict[str, Any]:
    value = request if isinstance(request, dict) else {}
    return {
        "schema": WORKSPACE_TASK_RESPONSE_SCHEMA,
        "hostInstanceId": host_instance_id,
        "requestId": _safe_identifier(value.get("requestId")),
        "taskId": _safe_identifier(value.get("taskId")),
        "grantId": _safe_identifier(value.get("grantId")),
        "actionRunId": _safe_identifier(value.get("actionRunId")),
        "stepId": value.get("stepId") if type(value.get("stepId")) is int and value["stepId"] >= 0 else None,
        "surface": _safe_identifier(value.get("surface")),
        "tool": value.get("tool") if value.get("tool") in WORKSPACE_TASK_TOOL_NAMES else "",
        "requiresSandboxTest": (
            value.get("requiresSandboxTest")
            if type(value.get("requiresSandboxTest")) is bool
            else False
        ),
        "candidateStageId": _safe_identifier(value.get("candidateStageId")),
        "argsHash": _safe_sha256(value.get("argsHash")),
        "issuedAt": value.get("issuedAt") if _valid_time(value.get("issuedAt")) else None,
        "expiresAt": value.get("expiresAt") if _valid_time(value.get("expiresAt")) else None,
    }


def _signed_workspace_task_response(
    request: Any,
    *,
    host_instance_id: str,
    auth_token: str | None,
    sandbox_auth_token: str | None,
    responded_at: float,
    result: dict[str, Any],
) -> dict[str, Any]:
    response = {
        **_response_binding(request, host_instance_id),
        "respondedAt": float(responded_at),
        "result": result,
        "sandboxAuthAlgorithm": "",
        "sandboxAuthTag": "",
    }
    if response["tool"] in {"edit", "test"}:
        response = _sign_workspace_sandbox_response(
            response,
            auth_token=sandbox_auth_token,
        )
    return _sign_workspace_task_payload(
        response,
        auth_token=auth_token,
        domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
    )


def handle_workspace_task_request(
    request: dict[str, Any],
    *,
    project_root: Path,
    host_instance_id: str,
    host_started_at: float,
    auth_token: str | None = None,
    sandbox_auth_token: str | None = None,
    request_filename: str = "",
    consumed_request_ids: dict[str, float] | None = None,
    staged_edits: dict[str, dict[str, Any]] | None = None,
    workspace_test_executor: Callable[..., dict[str, Any]] | None = None,
    sandbox_ready: bool = False,
    external_tracked_paths: frozenset[str] | None = None,
    run_command: Callable[..., Any] = subprocess.run,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    current = float(now())

    def respond(result: dict[str, Any]) -> dict[str, Any]:
        return _signed_workspace_task_response(
            request,
            host_instance_id=host_instance_id,
            auth_token=auth_token,
            sandbox_auth_token=sandbox_auth_token,
            responded_at=float(now()),
            result=result,
        )

    value = request if isinstance(request, dict) else {}
    request_id = str(value.get("requestId") or "")
    task_id = str(value.get("taskId") or "")
    grant_id = str(value.get("grantId") or "")
    action_run_id = str(value.get("actionRunId") or "")
    surface = str(value.get("surface") or "")
    step_id = value.get("stepId")
    tool = str(value.get("tool") or "")
    requires_sandbox_test = value.get("requiresSandboxTest")
    candidate_stage_id = str(value.get("candidateStageId") or "")
    invalid = bool(
        not isinstance(request, dict)
        or set(request) != _REQUEST_KEYS
        or request.get("schema") != WORKSPACE_TASK_REQUEST_SCHEMA
        or request.get("hostInstanceId") != host_instance_id
        or not _IDENTIFIER_PATTERN.fullmatch(host_instance_id)
        or not _IDENTIFIER_PATTERN.fullmatch(request_id)
        or not _IDENTIFIER_PATTERN.fullmatch(task_id)
        or not _IDENTIFIER_PATTERN.fullmatch(grant_id)
        or not _IDENTIFIER_PATTERN.fullmatch(action_run_id)
        or not _IDENTIFIER_PATTERN.fullmatch(surface)
        or type(step_id) is not int
        or step_id < 0
        or tool not in WORKSPACE_TASK_TOOL_NAMES
        or type(requires_sandbox_test) is not bool
        or not isinstance(value.get("candidateStageId"), str)
        or not isinstance(value.get("sandboxAuthAlgorithm"), str)
        or not isinstance(value.get("sandboxAuthTag"), str)
        or not isinstance(request.get("args"), dict)
        or not _SHA256_PATTERN.fullmatch(str(request.get("argsHash") or ""))
        or not _valid_time(request.get("issuedAt"))
        or not _valid_time(request.get("expiresAt"))
        or request_filename != f"{request_id}.json"
    )
    if invalid:
        return respond(_blocked("workspace_request_invalid"))
    if not _valid_workspace_task_auth_token(auth_token):
        return respond(_blocked("workspace_request_auth_unavailable"))
    issued_at = float(request["issuedAt"])
    expires_at = float(request["expiresAt"])
    if (
        expires_at <= issued_at
        or expires_at - issued_at
        > (
            WORKSPACE_SANDBOX_TEST_REQUEST_TTL_SEC
            if tool == "test"
            else WORKSPACE_TASK_REQUEST_TTL_SEC
        )
        or issued_at > current + 5.0
    ):
        return respond(_blocked("workspace_request_time_invalid"))
    if issued_at < float(host_started_at):
        return respond(_blocked("workspace_request_pre_restart"))
    if current >= expires_at or expires_at - current <= 0.25:
        return respond(_blocked("workspace_request_expired"))
    try:
        expected_args_hash = workspace_task_args_hash(request["args"])
    except (TypeError, ValueError):
        return respond(_blocked("workspace_request_invalid"))
    if not hmac.compare_digest(expected_args_hash, request["argsHash"]):
        return respond(_blocked("workspace_request_args_mismatch"))
    if not _workspace_task_payload_is_authentic(
        request,
        auth_token=auth_token,
        domain=WORKSPACE_TASK_REQUEST_AUTH_DOMAIN,
    ):
        return respond(_blocked("workspace_request_auth_invalid"))
    sandbox_authority_required = tool in {"edit", "test"}
    if sandbox_authority_required and not _valid_workspace_sandbox_auth_token(
        sandbox_auth_token
    ):
        return respond(_blocked("workspace_sandbox_auth_unavailable"))
    if sandbox_authority_required and not _workspace_sandbox_authority_is_authentic(
        request,
        auth_token=sandbox_auth_token,
    ):
        return respond(_blocked("workspace_sandbox_auth_invalid"))
    if tool == "edit":
        if candidate_stage_id:
            return respond(_blocked("workspace_request_invalid"))
    elif tool == "test":
        if (
            requires_sandbox_test is not True
            or not _IDENTIFIER_PATTERN.fullmatch(candidate_stage_id)
        ):
            return respond(_blocked("workspace_request_invalid"))
    elif requires_sandbox_test is not False or candidate_stage_id:
        return respond(_blocked("workspace_request_invalid"))
    if consumed_request_ids is not None:
        if request_id in consumed_request_ids:
            return respond(_blocked("workspace_request_replayed"))
        consumed_request_ids[request_id] = expires_at
    if tool == "edit":
        if requires_sandbox_test is True and sandbox_ready is not True:
            return respond(
                _blocked(
                    "workspace_test_sandbox_unavailable",
                    "Workspace test sandbox is unavailable.",
                    attempted=True,
                )
            )
        if staged_edits is None:
            return respond(_blocked("workspace_host_authorization_required"))
        return respond(
            stage_workspace_edit(
                project_root=project_root,
                args=request["args"],
                task_id=task_id,
                grant_id=grant_id,
                action_run_id=action_run_id,
                step_id=step_id,
                surface=surface,
                host_instance_id=host_instance_id,
                stages=staged_edits,
                requires_sandbox_test=requires_sandbox_test,
                run_command=run_command,
                now=now,
            )
        )
    if tool == "test":
        if staged_edits is None:
            return respond(_blocked("workspace_test_sandbox_required", attempted=True))
        expire_workspace_edit_stages(staged_edits, current=current)
        stage = staged_edits.get(candidate_stage_id)
        if stage is None:
            return respond(_blocked("workspace_edit_stage_unavailable", attempted=True))
        if not all(
            stage.get(key) == expected
            for key, expected in (
                ("hostInstanceId", host_instance_id),
                ("taskId", task_id),
                ("grantId", grant_id),
                ("surface", surface),
            )
        ):
            return respond(_blocked("workspace_test_stage_binding_mismatch", attempted=True))
        if request["args"] == {"runner": "discard", "targets": []}:
            if stage.get("requiresSandboxTest") is False and (
                stage.get("actionRunId") != action_run_id
                or stage.get("stepId") != step_id
            ):
                return respond(
                    _blocked("workspace_test_stage_binding_mismatch", attempted=True)
                )
            staged_edits.pop(candidate_stage_id, None)
            return respond(
                _result(
                    attempted=True,
                    executed=True,
                    observed=True,
                    verified=True,
                    outcome="succeeded",
                    code="workspace_edit_stage_cancelled",
                    summary="Workspace edit stage was discarded; no file was changed.",
                    evidence={
                        "stageId": candidate_stage_id,
                        "hostInstanceId": host_instance_id,
                    },
                )
            )
        if stage.get("requiresSandboxTest") is not True:
            return respond(
                _blocked("workspace_test_stage_binding_mismatch", attempted=True)
            )
        if workspace_test_executor is None:
            staged_edits.pop(candidate_stage_id, None)
            return respond(_blocked("workspace_test_sandbox_required", attempted=True))
        try:
            result = workspace_test_executor(
                stage=stage,
                args=request["args"],
                external_tracked_paths=frozenset(external_tracked_paths or ()),
            )
        except Exception:
            result = _unverified(
                "workspace_test_outcome_unverified",
                "Workspace test outcome is unverified.",
            )
        if not _valid_result_payload(result):
            result = _unverified(
                "workspace_test_outcome_unverified",
                "Workspace test outcome is unverified.",
            )
        evidence = result.get("evidence") if isinstance(result, dict) else None
        passed = bool(
            result.get("outcome") == "succeeded"
            and result.get("code") == "workspace_test_passed"
            and isinstance(evidence, dict)
            and evidence.get("stageId") == candidate_stage_id
            and evidence.get("candidatePath") == stage.get("path")
            and evidence.get("candidateSha256") == stage.get("candidateSha256")
            and evidence.get("runner") == "python_unittest"
            and isinstance(evidence.get("targets"), list)
            and bool(evidence.get("targets"))
            and evidence.get("targets") == request["args"].get("targets")
            and request["args"].get("runner") == "python_unittest"
            and type(evidence.get("testsRun")) is int
            and 1 <= int(evidence["testsRun"]) <= 999_999
            and type(evidence.get("exitCode")) is int
            and int(evidence["exitCode"]) == 0
            and evidence.get("semanticVerified") is False
            and all(
                isinstance(target, str) and target
                for target in evidence.get("targets", ())
            )
            and _SHA256_PATTERN.fullmatch(str(evidence.get("baseTreeSha256") or ""))
            and _SHA256_PATTERN.fullmatch(str(evidence.get("candidateTreeSha256") or ""))
        )
        if passed:
            stage["testedBaseTreeSha256"] = str(evidence["baseTreeSha256"])
            stage["testedCandidateTreeSha256"] = str(evidence["candidateTreeSha256"])
            stage["testedRunner"] = str(evidence.get("runner") or "")
            stage["testedTargets"] = tuple(evidence.get("targets") or ())
            stage["testedTestsRun"] = int(evidence["testsRun"])
            stage["testedSemanticVerified"] = False
            stage["testedAt"] = current
        else:
            staged_edits.pop(candidate_stage_id, None)
            if (
                result.get("outcome") == "succeeded"
                and result.get("code") == "workspace_test_passed"
            ):
                result = _unverified(
                    "workspace_test_binding_invalid",
                    "Workspace test binding is invalid.",
                )
        return respond(result)
    if tool not in WORKSPACE_TASK_QUEUE_TOOL_NAMES:
        return respond(_blocked("workspace_host_authorization_required"))
    return respond(
        execute_workspace_task_tool(
            project_root=project_root,
            tool=tool,
            args=request["args"],
            timeout_sec=max(
                0.1,
                min(
                    WORKSPACE_TASK_COMMAND_TIMEOUT_SEC,
                    expires_at - current - 0.25,
                ),
            ),
            external_tracked_paths=frozenset(external_tracked_paths or ()),
        )
    )


class WorkspaceTaskHostClient:
    def __init__(
        self,
        root: Path | None = None,
        timeout_sec: float = WORKSPACE_SANDBOX_CLIENT_TIMEOUT_SEC,
        auth_token: str | None = None,
        sandbox_auth_token: str | None = None,
    ) -> None:
        self.root = Path(root or get_runtime_artifacts_root()) / "host_supervisor"
        self.timeout_sec = max(
            0.1,
            min(float(timeout_sec), WORKSPACE_SANDBOX_TEST_REQUEST_TTL_SEC),
        )
        self.auth_token = _resolve_workspace_task_auth_token(auth_token)
        self.sandbox_auth_token = _resolve_workspace_sandbox_auth_token(
            sandbox_auth_token
        )

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @property
    def requests_dir(self) -> Path:
        return self.root / "requests"

    @property
    def responses_dir(self) -> Path:
        return self.root / "responses"

    def _available_status(self) -> dict[str, Any] | None:
        if (
            not _valid_workspace_task_auth_token(self.auth_token)
            or (self.root.exists() and _is_link_like(self.root))
        ):
            return None
        try:
            payload = read_bounded_json(self.status_path, maximum_bytes=WORKSPACE_TASK_MAX_RESPONSE_BYTES)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        heartbeat = payload.get("heartbeatAt") if isinstance(payload, dict) else None
        host_instance_id = (
            str(payload.get("hostInstanceId") or "")
            if isinstance(payload, dict)
            else ""
        )
        return payload if (
            isinstance(payload, dict)
            and payload.get("schema") == SUPERVISOR_STATUS_SCHEMA
            and payload.get("workspaceTaskAuthReady") is True
            and _IDENTIFIER_PATTERN.fullmatch(host_instance_id)
            and _valid_time(heartbeat)
            and 0.0 <= time.time() - float(heartbeat) <= _STATUS_STALE_SEC
        ) else None

    def _available_host_instance(self) -> str:
        payload = self._available_status()
        return str(payload.get("hostInstanceId") or "") if payload else ""

    def available(self, *, require_sandbox: bool = False) -> bool:
        if require_sandbox:
            return self.sandbox_available()
        return bool(self._available_host_instance())

    def sandbox_available(self) -> bool:
        payload = self._available_status()
        return bool(
            payload
            and self._sandbox_auth_available(payload)
            and payload.get("workspaceSandboxReady") is True
        )

    def _sandbox_auth_available(self, payload: dict[str, Any]) -> bool:
        return bool(
            _valid_workspace_sandbox_auth_token(self.sandbox_auth_token)
            and payload.get("workspaceSandboxAuthReady") is True
        )

    def execute(
        self,
        task_id: str,
        step_id: int,
        tool: str,
        args: dict,
        *,
        grant_id: str,
        action_run_id: str,
        surface: str,
        requires_sandbox_test: bool = False,
        candidate_stage_id: str = "",
    ) -> dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        normalized_grant_id = str(grant_id or "").strip()
        normalized_action_run_id = str(action_run_id or "").strip()
        normalized_surface = str(surface or "").strip()
        normalized_tool = str(tool or "").strip()
        normalized_candidate_stage_id = str(candidate_stage_id or "").strip()
        if (
            not _IDENTIFIER_PATTERN.fullmatch(normalized_task_id)
            or not _IDENTIFIER_PATTERN.fullmatch(normalized_grant_id)
            or not _IDENTIFIER_PATTERN.fullmatch(normalized_action_run_id)
            or not _IDENTIFIER_PATTERN.fullmatch(normalized_surface)
            or type(step_id) is not int
            or step_id < 0
            or normalized_tool not in WORKSPACE_TASK_TOOL_NAMES
            or not isinstance(args, dict)
            or type(requires_sandbox_test) is not bool
        ):
            return _blocked("workspace_request_invalid")
        if normalized_tool == "edit":
            if normalized_candidate_stage_id:
                return _blocked("workspace_request_invalid")
        elif normalized_tool == "test":
            if (
                requires_sandbox_test is not True
                or not _IDENTIFIER_PATTERN.fullmatch(
                    normalized_candidate_stage_id
                )
            ):
                return _blocked("workspace_request_invalid")
        elif requires_sandbox_test or normalized_candidate_stage_id:
            return _blocked("workspace_request_invalid")
        if (
            normalized_tool not in WORKSPACE_TASK_QUEUE_TOOL_NAMES
            and normalized_tool not in {"edit", "test"}
        ):
            return _blocked("workspace_host_authorization_required")
        status = self._available_status()
        if status is None:
            return _blocked("host_supervisor_unavailable")
        if normalized_tool in {"edit", "test"} and not self._sandbox_auth_available(status):
            return _blocked("workspace_sandbox_auth_unavailable")
        host_instance_id = str(status.get("hostInstanceId") or "")
        request_id = uuid.uuid4().hex
        issued_at = time.time()
        operation_timeout_sec = min(
            self.timeout_sec,
            WORKSPACE_SANDBOX_TEST_REQUEST_TTL_SEC
            if normalized_tool == "test"
            else WORKSPACE_TASK_REQUEST_TTL_SEC,
        )
        try:
            safe_args = dict(args)
            args_hash = workspace_task_args_hash(safe_args)
            request = _sign_workspace_sandbox_authority(
                {
                    "schema": WORKSPACE_TASK_REQUEST_SCHEMA,
                    "hostInstanceId": host_instance_id,
                    "requestId": request_id,
                    "taskId": normalized_task_id,
                    "grantId": normalized_grant_id,
                    "actionRunId": normalized_action_run_id,
                    "stepId": step_id,
                    "surface": normalized_surface,
                    "tool": normalized_tool,
                    "requiresSandboxTest": requires_sandbox_test,
                    "candidateStageId": normalized_candidate_stage_id,
                    "args": safe_args,
                    "argsHash": args_hash,
                    "issuedAt": issued_at,
                    "expiresAt": issued_at + operation_timeout_sec,
                },
                auth_token=self.sandbox_auth_token,
            )
            request = _sign_workspace_task_payload(
                request,
                auth_token=self.auth_token,
                domain=WORKSPACE_TASK_REQUEST_AUTH_DOMAIN,
            )
            encoded_request = json.dumps(
                request,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return _blocked("workspace_request_invalid")
        if len(encoded_request) > WORKSPACE_TASK_MAX_REQUEST_BYTES:
            return _blocked("workspace_request_too_large")
        if not all(
            ensure_workspace_queue_directory(self.root, directory)
            for directory in (self.requests_dir, self.responses_dir)
        ):
            return _blocked("workspace_queue_path_unsafe")
        request_path = self.requests_dir / f"{request_id}.json"
        response_path = self.responses_dir / f"{request_id}.json"
        try:
            atomic_json_write(request_path, request)
        except (OSError, TypeError, ValueError):
            return _blocked("workspace_queue_write_failed")
        deadline = time.monotonic() + operation_timeout_sec
        while time.monotonic() < deadline:
            try:
                response = read_bounded_json(response_path, maximum_bytes=WORKSPACE_TASK_MAX_RESPONSE_BYTES)
            except FileNotFoundError:
                response = None
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                for queue_path in (request_path, response_path):
                    try:
                        queue_path.unlink()
                    except OSError:
                        pass
                return _unverified("workspace_response_invalid", "Workspace task response is invalid.")
            if response is not None:
                try:
                    response_path.unlink()
                except OSError:
                    pass
                try:
                    request_path.unlink()
                except OSError:
                    pass
                if (
                    isinstance(response, dict)
                    and isinstance(response.get("schema"), str)
                    and response["schema"].startswith(
                        "host_supervisor.workspace-task.response."
                    )
                    and response["schema"] != WORKSPACE_TASK_RESPONSE_SCHEMA
                ):
                    return _blocked(
                        "workspace_host_protocol_mismatch",
                        "Workspace Host protocol version does not match Core.",
                    )
                result = response.get("result") if isinstance(response, dict) else None
                if (
                    not isinstance(response, dict)
                    or set(response) != _RESPONSE_KEYS
                    or response.get("schema") != WORKSPACE_TASK_RESPONSE_SCHEMA
                    or response.get("hostInstanceId") != host_instance_id
                    or response.get("requestId") != request_id
                    or response.get("taskId") != normalized_task_id
                    or response.get("grantId") != normalized_grant_id
                    or response.get("actionRunId") != normalized_action_run_id
                    or response.get("stepId") != step_id
                    or response.get("surface") != normalized_surface
                    or response.get("tool") != normalized_tool
                    or response.get("requiresSandboxTest") is not requires_sandbox_test
                    or response.get("candidateStageId") != normalized_candidate_stage_id
                    or response.get("argsHash") != args_hash
                    or response.get("issuedAt") != request["issuedAt"]
                    or response.get("expiresAt") != request["expiresAt"]
                    or time.time() >= float(request["expiresAt"])
                    or not _valid_time(response.get("respondedAt"))
                    or float(response["respondedAt"]) < float(request["issuedAt"])
                    or float(response["respondedAt"]) > float(request["expiresAt"])
                    or not _workspace_task_payload_is_authentic(
                        response,
                        auth_token=self.auth_token,
                        domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
                    )
                    or (
                        normalized_tool in {"edit", "test"}
                        and not _workspace_sandbox_response_is_authentic(
                            response,
                            auth_token=self.sandbox_auth_token,
                        )
                    )
                    or not _valid_result_payload(result)
                ):
                    return _unverified("workspace_response_invalid", "Workspace task response is invalid.")
                return dict(result)
            time.sleep(0.05)
        try:
            request_path.unlink()
        except OSError:
            pass
        return _unverified("workspace_task_outcome_unverified", "Workspace task outcome is unverified after timeout.")

    def stage_edit(
        self,
        task_id: str,
        step_id: int,
        args: dict,
        *,
        grant_id: str,
        action_run_id: str,
        surface: str,
        requires_sandbox_test: bool = False,
    ) -> dict[str, Any]:
        if requires_sandbox_test is True and not self.sandbox_available():
            return _blocked(
                "workspace_test_sandbox_unavailable",
                "Workspace test sandbox is unavailable.",
            )
        return self.execute(
            task_id,
            step_id,
            "edit",
            args,
            grant_id=grant_id,
            action_run_id=action_run_id,
            surface=surface,
            requires_sandbox_test=requires_sandbox_test,
        )

    def test_staged_candidate(
        self,
        task_id: str,
        step_id: int,
        args: dict,
        *,
        stage_id: str,
        grant_id: str,
        action_run_id: str,
        surface: str,
    ) -> dict[str, Any]:
        return self.execute(
            task_id,
            step_id,
            "test",
            args,
            grant_id=grant_id,
            action_run_id=action_run_id,
            surface=surface,
            requires_sandbox_test=True,
            candidate_stage_id=stage_id,
        )

    def discard_staged_candidate(
        self,
        task_id: str,
        step_id: int,
        *,
        stage_id: str,
        grant_id: str,
        action_run_id: str,
        surface: str,
    ) -> dict[str, Any]:
        return self.test_staged_candidate(
            task_id,
            step_id,
            {"runner": "discard", "targets": []},
            stage_id=stage_id,
            grant_id=grant_id,
            action_run_id=action_run_id,
            surface=surface,
        )


class WorkspaceMutationHostClient:
    def __init__(
        self,
        root: Path | None = None,
        timeout_sec: float = 15.0,
        auth_token: str | None = None,
    ) -> None:
        self.root = Path(root or get_runtime_artifacts_root()) / "host_supervisor"
        self.timeout_sec = max(
            0.1,
            min(float(timeout_sec), WORKSPACE_MUTATION_REQUEST_TTL_SEC),
        )
        self.auth_token = _resolve_workspace_mutation_auth_token(auth_token)

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @property
    def requests_dir(self) -> Path:
        return self.root / "mutation_requests"

    @property
    def responses_dir(self) -> Path:
        return self.root / "mutation_responses"

    def _available_host_instance(self) -> str:
        if (
            not _valid_workspace_mutation_auth_token(self.auth_token)
            or (self.root.exists() and _is_link_like(self.root))
        ):
            return ""
        try:
            payload = read_bounded_json(
                self.status_path,
                maximum_bytes=WORKSPACE_MUTATION_MAX_RESPONSE_BYTES,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return ""
        heartbeat = payload.get("heartbeatAt") if isinstance(payload, dict) else None
        host_instance_id = (
            str(payload.get("hostInstanceId") or "")
            if isinstance(payload, dict)
            else ""
        )
        return host_instance_id if (
            isinstance(payload, dict)
            and payload.get("schema") == SUPERVISOR_STATUS_SCHEMA
            and payload.get("workspaceMutationAuthReady") is True
            and _IDENTIFIER_PATTERN.fullmatch(host_instance_id)
            and _valid_time(heartbeat)
            and 0.0 <= time.time() - float(heartbeat) <= _STATUS_STALE_SEC
        ) else ""

    def available(self) -> bool:
        return bool(self._available_host_instance())

    @staticmethod
    def _valid_claim(claim: Any) -> bool:
        return bool(
            isinstance(claim, dict)
            and set(claim) == _MUTATION_CLAIM_KEYS
            and all(
                _IDENTIFIER_PATTERN.fullmatch(str(claim.get(key) or ""))
                for key in (
                    "approvalId",
                    "claimId",
                    "stageId",
                    "hostInstanceId",
                    "taskId",
                    "grantId",
                    "actionRunId",
                    "surface",
                )
            )
            and claim.get("tool") == "edit"
            and claim.get("surface") == "control_page"
            and type(claim.get("stepId")) is int
            and int(claim["stepId"]) >= 0
            and _valid_time(claim.get("grantExpiresAt"))
            and float(claim["grantExpiresAt"]) > 0.0
            and _SHA256_PATTERN.fullmatch(str(claim.get("argsHash") or ""))
            and (
                claim.get("baseSha256") == WORKSPACE_EDIT_ABSENT_SHA
                or _SHA256_PATTERN.fullmatch(str(claim.get("baseSha256") or ""))
            )
            and _SHA256_PATTERN.fullmatch(str(claim.get("candidateSha256") or ""))
            and _SHA256_PATTERN.fullmatch(str(claim.get("previewDigest") or ""))
            and type(claim.get("dirtyBaseAcknowledged")) is bool
        )

    def _submit(self, operation: str, claim: dict[str, Any]) -> dict[str, Any]:
        if operation not in {"apply", "cancel"} or not self._valid_claim(claim):
            return _blocked("workspace_mutation_request_invalid")
        host_instance_id = self._available_host_instance()
        if not host_instance_id:
            return _blocked("host_supervisor_unavailable")
        if claim["hostInstanceId"] != host_instance_id:
            return _blocked("workspace_edit_stage_pre_restart")
        request_id = uuid.uuid4().hex
        issued_at = time.time()
        grant_expires_at = float(claim["grantExpiresAt"])
        if operation == "apply" and grant_expires_at - issued_at <= 0.25:
            return _blocked("task_grant_expired")
        expires_at = issued_at + self.timeout_sec
        if operation == "apply":
            expires_at = min(expires_at, grant_expires_at)
        request = _sign_workspace_mutation_payload(
            {
                "schema": WORKSPACE_MUTATION_REQUEST_SCHEMA,
                "operation": operation,
                "requestId": request_id,
                **dict(claim),
                "issuedAt": issued_at,
                "expiresAt": expires_at,
            },
            auth_token=self.auth_token,
            domain=WORKSPACE_MUTATION_REQUEST_AUTH_DOMAIN,
        )
        try:
            encoded = _canonical_json_bytes(request)
        except (TypeError, ValueError):
            return _blocked("workspace_mutation_request_invalid")
        if len(encoded) > WORKSPACE_MUTATION_MAX_REQUEST_BYTES:
            return _blocked("workspace_mutation_request_too_large")
        if not all(
            ensure_workspace_queue_directory(self.root, directory)
            for directory in (self.requests_dir, self.responses_dir)
        ):
            return _blocked("workspace_mutation_queue_path_unsafe")
        request_path = self.requests_dir / f"{request_id}.json"
        response_path = self.responses_dir / f"{request_id}.json"
        try:
            atomic_json_write(request_path, request)
        except (OSError, TypeError, ValueError):
            return _blocked("workspace_mutation_queue_write_failed")
        deadline = time.monotonic() + max(0.0, expires_at - issued_at)
        while time.monotonic() < deadline:
            try:
                response = read_bounded_json(
                    response_path,
                    maximum_bytes=WORKSPACE_MUTATION_MAX_RESPONSE_BYTES,
                )
            except FileNotFoundError:
                response = None
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                for queue_path in (request_path, response_path):
                    try:
                        queue_path.unlink()
                    except OSError:
                        pass
                return _unverified(
                    "workspace_mutation_response_invalid",
                    "Workspace mutation response is invalid.",
                )
            if response is not None:
                try:
                    response_path.unlink()
                except OSError:
                    pass
                try:
                    request_path.unlink()
                except OSError:
                    pass
                result = response.get("result") if isinstance(response, dict) else None
                if (
                    not isinstance(response, dict)
                    or set(response) != _MUTATION_RESPONSE_KEYS
                    or response.get("schema") != WORKSPACE_MUTATION_RESPONSE_SCHEMA
                    or any(
                        response.get(key) != request.get(key)
                        for key in (
                            "operation",
                            "requestId",
                            *_MUTATION_CLAIM_KEYS,
                            "issuedAt",
                            "expiresAt",
                        )
                    )
                    or time.time() >= float(request["expiresAt"])
                    or not _valid_time(response.get("respondedAt"))
                    or float(response["respondedAt"]) < float(request["issuedAt"])
                    or float(response["respondedAt"]) > float(request["expiresAt"])
                    or not _workspace_mutation_payload_is_authentic(
                        response,
                        auth_token=self.auth_token,
                        domain=WORKSPACE_MUTATION_RESPONSE_AUTH_DOMAIN,
                    )
                    or not _valid_result_payload(result)
                ):
                    return _unverified(
                        "workspace_mutation_response_invalid",
                        "Workspace mutation response is invalid.",
                    )
                return dict(result)
            time.sleep(0.05)
        try:
            request_path.unlink()
        except OSError:
            pass
        return _unverified(
            "workspace_edit_apply_outcome_unverified"
            if operation == "apply"
            else "workspace_edit_cancel_outcome_unverified",
            "Workspace edit outcome is unverified.",
        )

    def apply(self, claim: dict[str, Any]) -> dict[str, Any]:
        return self._submit("apply", claim)

    def cancel(self, claim: dict[str, Any]) -> dict[str, Any]:
        return self._submit("cancel", claim)

    def cancel_stage(
        self,
        stage: dict[str, Any],
        *,
        task_id: str,
        grant_id: str,
        action_run_id: str,
        step_id: int,
        surface: str = "control_page",
    ) -> dict[str, Any]:
        value = stage if isinstance(stage, dict) else {}
        claim = {
            "approvalId": f"discard-{uuid.uuid4().hex}",
            "claimId": f"discard-{uuid.uuid4().hex}",
            "stageId": value.get("stageId"),
            "hostInstanceId": value.get("hostInstanceId"),
            "taskId": task_id,
            "grantId": grant_id,
            "grantExpiresAt": time.time() + self.timeout_sec,
            "actionRunId": action_run_id,
            "stepId": step_id,
            "surface": surface,
            "tool": "edit",
            "argsHash": value.get("argsHash"),
            "baseSha256": value.get("baseSha256"),
            "candidateSha256": value.get("candidateSha256"),
            "previewDigest": value.get("previewDigest"),
            "dirtyBaseAcknowledged": False,
        }
        return self.cancel(claim)


__all__ = [
    "WORKSPACE_EDIT_ABSENT_SHA",
    "WORKSPACE_EDIT_MAX_PREVIEW_BYTES",
    "WORKSPACE_EDIT_STAGE_TTL_SEC",
    "WORKSPACE_MUTATION_AUTH_ENV",
    "WORKSPACE_MUTATION_MAX_REQUEST_BYTES",
    "WORKSPACE_MUTATION_MAX_RESPONSE_BYTES",
    "WORKSPACE_MUTATION_REQUEST_AUTH_DOMAIN",
    "WORKSPACE_MUTATION_REQUEST_SCHEMA",
    "WORKSPACE_MUTATION_RESPONSE_AUTH_DOMAIN",
    "WORKSPACE_MUTATION_RESPONSE_SCHEMA",
    "WORKSPACE_SANDBOX_AUTH_DOMAIN",
    "WORKSPACE_SANDBOX_AUTH_ENV",
    "WORKSPACE_SANDBOX_RESPONSE_AUTH_DOMAIN",
    "WORKSPACE_SANDBOX_TEST_REQUEST_TTL_SEC",
    "WORKSPACE_SANDBOX_CLIENT_TIMEOUT_SEC",
    "WORKSPACE_TASK_AUTH_ALGORITHM",
    "WORKSPACE_TASK_AUTH_ENV",
    "WORKSPACE_TASK_COMMAND_TIMEOUT_SEC",
    "WORKSPACE_TASK_MAX_OUTPUT_BYTES",
    "WORKSPACE_TASK_MAX_REQUEST_BYTES",
    "WORKSPACE_TASK_ORPHAN_TTL_SEC",
    "WORKSPACE_TASK_REQUEST_SCHEMA",
    "WORKSPACE_TASK_REQUEST_AUTH_DOMAIN",
    "WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN",
    "WORKSPACE_TASK_RESPONSE_SCHEMA",
    "WORKSPACE_TASK_TOOL_NAMES",
    "WORKSPACE_TASK_QUEUE_TOOL_NAMES",
    "WorkspaceMutationHostClient",
    "WorkspaceTaskHostClient",
    "build_external_tracked_manifest",
    "build_workspace_tracked_manifest",
    "expire_workspace_edit_stages",
    "execute_workspace_task_tool",
    "ensure_workspace_queue_directory",
    "handle_workspace_task_request",
    "handle_workspace_mutation_request",
    "stage_workspace_edit",
    "workspace_task_args_hash",
    "workspace_sandbox_request_is_authentic",
]
