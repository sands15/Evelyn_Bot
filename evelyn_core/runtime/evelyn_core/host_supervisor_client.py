from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import get_runtime_artifacts_root


SUPERVISOR_STATUS_SCHEMA = "host_supervisor.status.v1"
SUPERVISOR_REQUEST_SCHEMA = "host_supervisor.request.v1"
SUPERVISOR_RESPONSE_SCHEMA = "host_supervisor.response.v1"
ALLOWED_HOST_ACTIONS = frozenset(
    {
        "restart_local_bridge",
        "start_discord_bot",
        "start_main_llm",
        "start_stt",
        "start_tts",
    }
)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


class HostSupervisorClient:
    def __init__(
        self,
        *,
        root: Path | None = None,
        timeout_sec: float = 3.0,
        stale_after_sec: float = 4.0,
    ) -> None:
        self.root = Path(root or get_runtime_artifacts_root()) / "host_supervisor"
        self.timeout_sec = max(0.1, float(timeout_sec))
        self.stale_after_sec = max(0.1, float(stale_after_sec))

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    @property
    def requests_dir(self) -> Path:
        return self.root / "requests"

    @property
    def responses_dir(self) -> Path:
        return self.root / "responses"

    def status(self) -> dict[str, Any]:
        payload = _read_json(self.status_path)
        if not payload or payload.get("schema") != SUPERVISOR_STATUS_SCHEMA:
            return {
                "available": False,
                "error": "host_supervisor_unavailable",
                "manualCommand": "start_local.bat --background",
            }
        heartbeat_at = payload.get("heartbeatAt")
        age_sec = (
            max(0.0, time.time() - float(heartbeat_at))
            if isinstance(heartbeat_at, (int, float))
            else float("inf")
        )
        return {
            **payload,
            "available": age_sec <= self.stale_after_sec,
            "ageSec": round(age_sec, 2),
            "error": None if age_sec <= self.stale_after_sec else "host_supervisor_unavailable",
            "manualCommand": "start_local.bat --background",
        }

    def available(self) -> bool:
        return bool(self.status().get("available"))

    def preview(self, action_id: str) -> dict[str, Any]:
        return self._request("preview", action_id=action_id)

    def apply(self, action_id: str, preview_token: str) -> dict[str, Any]:
        return self._request(
            "apply",
            action_id=action_id,
            preview_token=preview_token,
        )

    def _request(
        self,
        operation: str,
        *,
        action_id: str,
        preview_token: str = "",
    ) -> dict[str, Any]:
        normalized_action = str(action_id or "").strip()
        if normalized_action not in ALLOWED_HOST_ACTIONS:
            return {
                "ok": False,
                "error": "unsupported_host_action",
                "actionId": normalized_action,
            }
        status = self.status()
        if not status.get("available"):
            return {
                "ok": False,
                "error": "host_supervisor_unavailable",
                "actionId": normalized_action,
                "manualCommand": "start_local.bat --background",
            }
        request_id = uuid.uuid4().hex
        request = {
            "schema": SUPERVISOR_REQUEST_SCHEMA,
            "requestId": request_id,
            "operation": operation,
            "actionId": normalized_action,
            "previewToken": str(preview_token or ""),
            "requestedAt": time.time(),
        }
        request_path = self.requests_dir / f"{request_id}.json"
        response_path = self.responses_dir / f"{request_id}.json"
        _atomic_json_write(request_path, request)
        response_timeout_sec = (
            max(self.timeout_sec, 125.0)
            if operation == "apply"
            else self.timeout_sec
        )
        deadline = time.monotonic() + response_timeout_sec
        while time.monotonic() < deadline:
            response = _read_json(response_path)
            if response is not None:
                try:
                    response_path.unlink()
                except OSError:
                    pass
                return response
            time.sleep(0.05)
        return {
            "ok": False,
            "error": "host_supervisor_timeout",
            "requestId": request_id,
            "actionId": normalized_action,
        }


__all__ = [
    "ALLOWED_HOST_ACTIONS",
    "HostSupervisorClient",
    "SUPERVISOR_REQUEST_SCHEMA",
    "SUPERVISOR_RESPONSE_SCHEMA",
    "SUPERVISOR_STATUS_SCHEMA",
]
