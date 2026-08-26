from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import get_runtime_artifacts_root


SUPERVISOR_STATUS_SCHEMA = "host_supervisor.status.v1"
SUPERVISOR_REQUEST_SCHEMA = "host_supervisor.request.v1"
SUPERVISOR_RESPONSE_SCHEMA = "host_supervisor.response.v1"
LOCAL_BRIDGE_RESTART_EXIT_CODE = 75
DISCORD_STOP_ATTESTATION_SCHEMA = (
    "host_supervisor.discord-stop-attestation.v1"
)
DISCORD_STOP_ATTESTATION_AUTH_DOMAIN = (
    b"evelyn.host-supervisor.discord-stop-attestation.v1\n"
)
DISCORD_STOP_ATTESTATION_AUTH_ENV = (
    "EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN"
)
DISCORD_STOP_ATTESTATION_AUTH_ALGORITHM = "hmac-sha256"
_ATTESTATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_DISCORD_RETIREMENT_CLAIM_ID = re.compile(
    r"^voice-retire-[0-9a-f]{32}$"
)
_STOPPED_CONTAINER_STATES = frozenset(
    {"absent", "created", "exited", "dead"}
)
_DISCORD_STOP_ATTESTATION_KEYS = frozenset(
    {
        "schema",
        "hostInstanceId",
        "requestId",
        "claimId",
        "service",
        "containerState",
        "stopped",
        "observedAt",
        "authAlgorithm",
        "authTag",
    }
)
ALLOWED_HOST_ACTIONS = frozenset(
    {
        "restart_local_bridge",
        "start_discord_bot",
        "stop_discord_bot",
        "start_main_llm",
        "start_voyager",
        "start_stt",
        "start_tts",
    }
)


def _valid_attestation_token(value: str | None) -> bool:
    size = len(str(value or "").strip().encode("utf-8"))
    return 32 <= size <= 512


def _canonical_attestation_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sign_discord_stop_attestation(
    *,
    host_instance_id: str,
    request_id: str,
    claim_id: str,
    container_state: str,
    observed_at: float,
    auth_token: str | None,
) -> dict[str, Any]:
    token = str(auth_token or "").strip()
    state = str(container_state or "").strip().lower()
    if (
        not _valid_attestation_token(token)
        or not _ATTESTATION_ID.fullmatch(str(host_instance_id or ""))
        or not _ATTESTATION_ID.fullmatch(str(request_id or ""))
        or not _DISCORD_RETIREMENT_CLAIM_ID.fullmatch(str(claim_id or ""))
        or state not in _STOPPED_CONTAINER_STATES
        or isinstance(observed_at, bool)
        or not isinstance(observed_at, (int, float))
        or not math.isfinite(float(observed_at))
        or float(observed_at) < 0.0
    ):
        raise ValueError("discord_stop_attestation_invalid")
    payload: dict[str, Any] = {
        "schema": DISCORD_STOP_ATTESTATION_SCHEMA,
        "hostInstanceId": str(host_instance_id),
        "requestId": str(request_id),
        "claimId": str(claim_id),
        "service": "discord_bot",
        "containerState": state,
        "stopped": True,
        "observedAt": float(observed_at),
        "authAlgorithm": DISCORD_STOP_ATTESTATION_AUTH_ALGORITHM,
    }
    digest = hmac.new(
        token.encode("utf-8"),
        DISCORD_STOP_ATTESTATION_AUTH_DOMAIN
        + _canonical_attestation_bytes(payload),
        hashlib.sha256,
    ).hexdigest()
    return {**payload, "authTag": digest}


def discord_stop_attestation_is_authentic(
    payload: Any,
    *,
    expected_host_instance_id: str,
    expected_request_id: str,
    expected_claim_id: str,
    auth_token: str | None,
    not_before: float,
    now: float | None = None,
) -> bool:
    token = str(auth_token or "").strip()
    current = time.time() if now is None else float(now)
    if (
        not _valid_attestation_token(token)
        or not isinstance(payload, dict)
        or set(payload) != _DISCORD_STOP_ATTESTATION_KEYS
        or payload.get("schema") != DISCORD_STOP_ATTESTATION_SCHEMA
        or payload.get("hostInstanceId") != expected_host_instance_id
        or payload.get("requestId") != expected_request_id
        or payload.get("claimId") != expected_claim_id
        or payload.get("service") != "discord_bot"
        or payload.get("containerState") not in _STOPPED_CONTAINER_STATES
        or payload.get("stopped") is not True
        or payload.get("authAlgorithm")
        != DISCORD_STOP_ATTESTATION_AUTH_ALGORITHM
        or not isinstance(payload.get("authTag"), str)
        or isinstance(payload.get("observedAt"), bool)
        or not isinstance(payload.get("observedAt"), (int, float))
        or not math.isfinite(float(payload["observedAt"]))
        or float(payload["observedAt"]) < float(not_before) - 1.0
        or float(payload["observedAt"]) > current + 1.0
    ):
        return False
    try:
        expected = sign_discord_stop_attestation(
            host_instance_id=expected_host_instance_id,
            request_id=expected_request_id,
            claim_id=expected_claim_id,
            container_state=str(payload["containerState"]),
            observed_at=float(payload["observedAt"]),
            auth_token=token,
        )["authTag"]
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(payload["authTag"], expected)


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
        attestation_auth_token: str | None = None,
    ) -> None:
        self.root = Path(root or get_runtime_artifacts_root()) / "host_supervisor"
        self.timeout_sec = max(0.1, float(timeout_sec))
        self.stale_after_sec = max(0.1, float(stale_after_sec))
        self.attestation_auth_token = str(
            os.getenv(DISCORD_STOP_ATTESTATION_AUTH_ENV, "")
            if attestation_auth_token is None
            else attestation_auth_token
        ).strip()

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

    def attest_discord_stopped(self, claim_id: str) -> dict[str, Any]:
        normalized_claim_id = str(claim_id or "").strip()
        status = self.status()
        host_instance_id = str(status.get("hostInstanceId") or "")
        if (
            not status.get("available")
            or status.get("workspaceMutationAuthReady") is not True
            or not _ATTESTATION_ID.fullmatch(host_instance_id)
            or not _DISCORD_RETIREMENT_CLAIM_ID.fullmatch(
                normalized_claim_id
            )
            or not _valid_attestation_token(
                self.attestation_auth_token
            )
        ):
            return {
                "ok": False,
                "error": "discord_stop_attestation_unavailable",
            }
        requested_at = time.time()
        response = self._request(
            "attest",
            action_id="stop_discord_bot",
            claim_id=normalized_claim_id,
        )
        request_id = str(response.get("requestId") or "")
        if (
            response.get("ok") is not True
            or not discord_stop_attestation_is_authentic(
                response.get("attestation"),
                expected_host_instance_id=host_instance_id,
                expected_request_id=request_id,
                expected_claim_id=normalized_claim_id,
                auth_token=self.attestation_auth_token,
                not_before=requested_at,
            )
        ):
            return {
                "ok": False,
                "error": "discord_stop_attestation_unverified",
            }
        return {
            "ok": True,
            "verified": True,
            "hostInstanceId": host_instance_id,
            "requestId": request_id,
            "claimId": normalized_claim_id,
        }

    def _request(
        self,
        operation: str,
        *,
        action_id: str,
        preview_token: str = "",
        claim_id: str = "",
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
        if operation == "attest":
            request["claimId"] = str(claim_id or "")
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
                if operation == "attest" and (
                    response.get("schema")
                    != SUPERVISOR_RESPONSE_SCHEMA
                    or response.get("requestId") != request_id
                ):
                    return {
                        "ok": False,
                        "error": "host_supervisor_response_invalid",
                        "requestId": request_id,
                        "actionId": normalized_action,
                    }
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
    "DISCORD_STOP_ATTESTATION_AUTH_DOMAIN",
    "DISCORD_STOP_ATTESTATION_AUTH_ENV",
    "DISCORD_STOP_ATTESTATION_SCHEMA",
    "HostSupervisorClient",
    "SUPERVISOR_REQUEST_SCHEMA",
    "SUPERVISOR_RESPONSE_SCHEMA",
    "SUPERVISOR_STATUS_SCHEMA",
    "discord_stop_attestation_is_authentic",
    "sign_discord_stop_attestation",
]
