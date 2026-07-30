from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable

from .paths import get_runtime_root


WINDOWS_UI_ACTION_RESULT_SCHEMA = "windows_ui_action.result.v1"
WINDOWS_UI_ACTION_RESULT_KEYS = frozenset(
    {
        "schema",
        "ok",
        "errorCode",
        "completedAt",
        "executed",
        "action",
        "elementId",
        "windowDigest",
    }
)
WINDOWS_UI_ACTION_MAX_RESPONSE_BYTES = 8192
WINDOWS_UI_ACTION_MAX_AGE_SEC = 5.0
_ELEMENT_ID_RE = re.compile(r"^[0-9a-f]{20}$")
_WINDOW_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{0,80}$")


class WindowsAccessibilityInvoker:
    """Invoke one foreground UIA Button through a fixed, bounded script."""

    def __init__(
        self,
        *,
        script_path: Path | None = None,
        powershell_path: Path | None = None,
        timeout_sec: float = 8.0,
        now: Callable[[], float] = time.time,
        run_process: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.script_path = Path(
            script_path
            or get_runtime_root()
            / "launchers"
            / "invoke_windows_accessibility_action.ps1"
        )
        self.powershell_path = Path(
            powershell_path
            or Path(
                os.environ.get("SystemRoot", r"C:\Windows"),
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe",
            )
        )
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.now = now
        self.run_process = run_process

    def invoke_sync(
        self,
        *,
        action: str,
        element_id: str,
        window_digest: str,
    ) -> dict[str, Any]:
        if action != "invoke":
            raise ValueError("windows_ui_action_not_allowed")
        if not _ELEMENT_ID_RE.fullmatch(str(element_id or "")):
            raise ValueError("windows_ui_action_element_id_invalid")
        if not _WINDOW_DIGEST_RE.fullmatch(str(window_digest or "")):
            raise ValueError("windows_ui_action_window_digest_invalid")
        if os.name != "nt":
            raise RuntimeError("windows_ui_action_requires_windows")
        if not self.script_path.is_file() or not self.powershell_path.is_file():
            raise RuntimeError("windows_ui_action_runtime_unavailable")
        completed = self.run_process(
            [
                str(self.powershell_path),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script_path),
                "-Action",
                action,
                "-ElementId",
                element_id,
                "-ExpectedWindowDigest",
                window_digest,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout = str(completed.stdout or "")
        if len(stdout.encode("utf-8", errors="replace")) > (
            WINDOWS_UI_ACTION_MAX_RESPONSE_BYTES
        ):
            raise RuntimeError("windows_ui_action_response_too_large")
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("windows_ui_action_empty_response")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("windows_ui_action_invalid_response") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != WINDOWS_UI_ACTION_RESULT_KEYS
            or payload.get("schema") != WINDOWS_UI_ACTION_RESULT_SCHEMA
            or type(payload.get("ok")) is not bool
            or type(payload.get("executed")) is not bool
            or payload.get("action") != action
            or payload.get("elementId") != element_id
            or payload.get("windowDigest") != window_digest
            or not _ERROR_CODE_RE.fullmatch(str(payload.get("errorCode") or ""))
        ):
            raise RuntimeError("windows_ui_action_invalid_response")
        completed_at = payload.get("completedAt")
        if (
            isinstance(completed_at, bool)
            or not isinstance(completed_at, (int, float))
            or not math.isfinite(float(completed_at))
            or float(completed_at) > self.now() + 2.0
            or self.now() - float(completed_at)
            > WINDOWS_UI_ACTION_MAX_AGE_SEC
        ):
            raise RuntimeError("windows_ui_action_stale_response")
        if int(completed.returncode or 0) != 0 and payload["ok"]:
            raise RuntimeError("windows_ui_action_contradictory_response")
        if payload["executed"] and not payload["ok"]:
            raise RuntimeError("windows_ui_action_contradictory_response")
        return {
            **payload,
            "completedAt": float(completed_at),
        }

    async def invoke(
        self,
        *,
        action: str,
        element_id: str,
        window_digest: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.invoke_sync,
            action=action,
            element_id=element_id,
            window_digest=window_digest,
        )


__all__ = [
    "WINDOWS_UI_ACTION_MAX_AGE_SEC",
    "WINDOWS_UI_ACTION_RESULT_KEYS",
    "WINDOWS_UI_ACTION_RESULT_SCHEMA",
    "WindowsAccessibilityInvoker",
]
