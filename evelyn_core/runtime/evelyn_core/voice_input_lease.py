from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from aiohttp import ClientSession, ClientTimeout

from .durable_artifact_process import DEFAULT_ARTIFACT_DEADLINE_SEC
from .paths import get_runtime_artifacts_root
from .runtime_artifact_io import (
    atomic_json_write,
    durable_artifact_process_scope,
    read_bounded_text,
)


VOICE_INPUT_LEASE_SCHEMA = "voice_input_lease.owner.v1"
VOICE_INPUT_LEASE_ENDPOINT = "/internal/voice-input-lease"
VOICE_INPUT_LEASE_AUTH_HEADER = "X-Evelyn-Voice-Input-Lease-Token"
VOICE_INPUT_LEASE_TOKEN_ENV = "EVELYN_VOICE_INPUT_LEASE_TOKEN"
VOICE_INPUT_RETIREMENT_CLAIM_SCHEMA = (
    "voice_input_lease.retirement-claim.v1"
)
VOICE_INPUT_RETIREMENT_RESULT_SCHEMA = (
    "voice_input_lease.retirement-result.v1"
)
VOICE_INPUT_RETIREMENT_CLAIM_TTL_SEC = 20.0
VOICE_INPUT_RETIREMENT_MAX_CLAIMS = 8
VOICE_INPUT_SOURCES = frozenset({"local_mic", "discord_voice"})
_INSTANCE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_RETIREMENT_CLAIM_ID_PATTERN = re.compile(
    r"voice-retire-[0-9a-f]{32}"
)
_PROCESS_INSTANCE_ID = secrets.token_hex(16)


class VoiceInputLeaseError(RuntimeError):
    def __init__(self, code: str, *, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class VoiceInputObservation:
    state: str
    instance_id: str = ""

    def __post_init__(self) -> None:
        if self.state not in {"active", "inactive", "unknown"}:
            raise ValueError("invalid_voice_input_observation")
        if self.instance_id and _INSTANCE_ID_PATTERN.fullmatch(self.instance_id) is None:
            raise ValueError("invalid_voice_input_instance_id")


def discord_voice_input_instance_id() -> str:
    return _PROCESS_INSTANCE_ID


def _valid_source(value: Any) -> str:
    source = str(value or "").strip()
    if source not in VOICE_INPUT_SOURCES:
        raise VoiceInputLeaseError("invalid_voice_input_source", status=400)
    return source


def _valid_instance_id(value: Any) -> str:
    instance_id = str(value or "").strip()
    if _INSTANCE_ID_PATTERN.fullmatch(instance_id) is None:
        raise VoiceInputLeaseError("invalid_voice_input_instance_id", status=400)
    return instance_id


class VoiceInputLeaseManager:
    """Single Bot API arbiter for physical voice-input ownership."""

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        now: Callable[[], float] = time.time,
        artifact_process: Any | None = None,
        artifact_deadline_sec: float = DEFAULT_ARTIFACT_DEADLINE_SEC,
    ) -> None:
        self.state_path = state_path or (
            get_runtime_artifacts_root() / "voice_input_lease" / "owner.json"
        )
        self.now = now
        self.artifact_process = artifact_process
        self.artifact_deadline_sec = max(
            0.1,
            float(artifact_deadline_sec),
        )
        self._lock = threading.RLock()
        self._phase = "bootstrap"
        self._source = ""
        self._instance_id = ""
        self._lease_id = ""
        self._last_released_source = ""
        self._last_released_instance_id = ""
        self._last_released_lease_id = ""
        self._retirement_claims: dict[str, dict[str, Any]] = {}
        self._persistence_failed = False
        self._public_snapshot = ("bootstrap", "")
        self._load()

    def _load(self) -> None:
        try:
            with durable_artifact_process_scope(
                self.artifact_process,
                timeout_sec=self.artifact_deadline_sec,
            ):
                raw = read_bounded_text(
                    self.state_path,
                    maximum_bytes=16_384,
                    missing_ok=True,
                )
            if raw is None:
                return
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("schema") != VOICE_INPUT_LEASE_SCHEMA:
                raise ValueError("invalid_schema")
            phase = payload.get("state")
            if phase not in {"unowned", "owned", "blocked"}:
                raise ValueError("invalid_state")
            source = str(payload.get("source") or "")
            instance_id = str(payload.get("instanceId") or "")
            lease_id = str(payload.get("leaseId") or "")
            if phase == "owned":
                _valid_source(source)
                _valid_instance_id(instance_id)
                if _INSTANCE_ID_PATTERN.fullmatch(lease_id) is None:
                    raise ValueError("invalid_lease_id")
            elif source or instance_id or lease_id:
                raise ValueError("unexpected_owner")
            self._phase = phase
            self._source = source
            self._instance_id = instance_id
            self._lease_id = lease_id
            last_source = str(payload.get("lastReleasedSource") or "")
            last_instance = str(payload.get("lastReleasedInstanceId") or "")
            last_lease = str(payload.get("lastReleasedLeaseId") or "")
            if last_source or last_instance or last_lease:
                _valid_source(last_source)
                _valid_instance_id(last_instance)
                if _INSTANCE_ID_PATTERN.fullmatch(last_lease) is None:
                    raise ValueError("invalid_last_release")
                self._last_released_source = last_source
                self._last_released_instance_id = last_instance
                self._last_released_lease_id = last_lease
            self._public_snapshot = (
                self._phase,
                self._source if self._phase == "owned" else "",
            )
        except OSError:
            # Unknown canonical state must never be overwritten after an I/O
            # failure. A process restart may reconcile it safely.
            self._persistence_failed = True
            self._phase = "blocked"
            self._source = ""
            self._instance_id = ""
            self._lease_id = ""
            self._public_snapshot = ("blocked", "")
        except (TypeError, ValueError, json.JSONDecodeError, VoiceInputLeaseError):
            self._phase = "blocked"
            self._source = ""
            self._instance_id = ""
            self._lease_id = ""
            self._public_snapshot = ("blocked", "")

    def _payload(self) -> dict[str, Any]:
        return {
            "schema": VOICE_INPUT_LEASE_SCHEMA,
            "state": self._phase,
            "source": self._source,
            "instanceId": self._instance_id,
            "leaseId": self._lease_id,
            "lastReleasedSource": self._last_released_source,
            "lastReleasedInstanceId": self._last_released_instance_id,
            "lastReleasedLeaseId": self._last_released_lease_id,
            "updatedAt": self.now(),
        }

    def _commit(self, payload: dict[str, Any]) -> None:
        if self._persistence_failed:
            raise VoiceInputLeaseError(
                "voice_input_lease_unavailable",
                status=503,
            )
        try:
            with durable_artifact_process_scope(
                self.artifact_process,
                timeout_sec=self.artifact_deadline_sec,
            ):
                atomic_json_write(
                    self.state_path,
                    payload,
                    durable=True,
                )
        except Exception:
            self._persistence_failed = True
            self._public_snapshot = ("blocked", "")
            raise
        self._phase = str(payload["state"])
        self._source = str(payload["source"])
        self._instance_id = str(payload["instanceId"])
        self._lease_id = str(payload["leaseId"])
        self._last_released_source = str(payload["lastReleasedSource"])
        self._last_released_instance_id = str(
            payload["lastReleasedInstanceId"]
        )
        self._last_released_lease_id = str(payload["lastReleasedLeaseId"])
        self._public_snapshot = (
            self._phase,
            self._source if self._phase == "owned" else "",
        )

    def _set_unowned(self) -> None:
        payload = self._payload()
        payload.update(
            {
                "state": "unowned",
                "source": "",
                "instanceId": "",
                "leaseId": "",
            }
        )
        if self._phase == "owned":
            payload.update(
                {
                    "lastReleasedSource": self._source,
                    "lastReleasedInstanceId": self._instance_id,
                    "lastReleasedLeaseId": self._lease_id,
                }
            )
        self._commit(payload)

    def _set_owned(self, source: str, instance_id: str) -> None:
        payload = self._payload()
        payload.update(
            {
                "state": "owned",
                "source": source,
                "instanceId": instance_id,
                "leaseId": secrets.token_hex(16),
            }
        )
        self._commit(payload)

    def _retire_exact_locked(
        self,
        source: str,
        instance_id: str,
        lease_id: str,
    ) -> dict[str, Any]:
        expected = (source, instance_id, lease_id)
        current = (self._source, self._instance_id, self._lease_id)
        if self._phase == "unowned" and expected == (
            self._last_released_source,
            self._last_released_instance_id,
            self._last_released_lease_id,
        ):
            return {
                "schema": VOICE_INPUT_RETIREMENT_RESULT_SCHEMA,
                "retired": False,
                "alreadyReleased": True,
            }
        if self._phase != "owned" or current != expected:
            raise VoiceInputLeaseError(
                "voice_input_lease_retirement_stale"
            )
        self._set_unowned()
        return {
            "schema": VOICE_INPUT_RETIREMENT_RESULT_SCHEMA,
            "retired": True,
            "alreadyReleased": False,
        }

    def retire_exact(
        self,
        source: Any,
        instance_id: Any,
        lease_id: Any,
    ) -> dict[str, Any]:
        normalized_source = _valid_source(source)
        normalized_instance = _valid_instance_id(instance_id)
        normalized_lease = str(lease_id or "").strip()
        if _INSTANCE_ID_PATTERN.fullmatch(normalized_lease) is None:
            raise VoiceInputLeaseError(
                "invalid_voice_input_lease_id",
                status=400,
            )
        with self._lock:
            if self._persistence_failed:
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            return self._retire_exact_locked(
                normalized_source,
                normalized_instance,
                normalized_lease,
            )

    def prepare_retirement(self, source: Any) -> dict[str, Any]:
        normalized_source = _valid_source(source)
        with self._lock:
            if self._persistence_failed:
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            current = float(self.now())
            if not math.isfinite(current) or current < 0.0:
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            self._retirement_claims = {
                claim_id: claim
                for claim_id, claim in self._retirement_claims.items()
                if float(claim["expiresAt"]) > current
            }
            if (
                self._phase != "owned"
                or self._source != normalized_source
            ):
                return {
                    "schema": VOICE_INPUT_RETIREMENT_CLAIM_SCHEMA,
                    "required": False,
                }
            if (
                len(self._retirement_claims)
                >= VOICE_INPUT_RETIREMENT_MAX_CLAIMS
            ):
                raise VoiceInputLeaseError(
                    "voice_input_lease_retirement_unavailable",
                    status=503,
                )
            claim_id = f"voice-retire-{secrets.token_hex(16)}"
            expires_at = (
                current + VOICE_INPUT_RETIREMENT_CLAIM_TTL_SEC
            )
            self._retirement_claims[claim_id] = {
                "source": self._source,
                "instanceId": self._instance_id,
                "leaseId": self._lease_id,
                "expiresAt": expires_at,
            }
            return {
                "schema": VOICE_INPUT_RETIREMENT_CLAIM_SCHEMA,
                "required": True,
                "claimId": claim_id,
                "expiresAt": expires_at,
            }

    def complete_retirement(self, claim_id: Any) -> dict[str, Any]:
        normalized_claim_id = str(claim_id or "").strip()
        if (
            _RETIREMENT_CLAIM_ID_PATTERN.fullmatch(
                normalized_claim_id
            )
            is None
        ):
            raise VoiceInputLeaseError(
                "voice_input_lease_retirement_claim_invalid",
                status=400,
            )
        with self._lock:
            claim = self._retirement_claims.pop(
                normalized_claim_id,
                None,
            )
            current = float(self.now())
            if (
                claim is None
                or not math.isfinite(current)
                or current < 0.0
                or float(claim["expiresAt"]) <= current
            ):
                raise VoiceInputLeaseError(
                    "voice_input_lease_retirement_claim_invalid",
                    status=409,
                )
            return self._retire_exact_locked(
                str(claim["source"]),
                str(claim["instanceId"]),
                str(claim["leaseId"]),
            )

    def _set_blocked(self) -> None:
        payload = self._payload()
        payload.update(
            {
                "state": "blocked",
                "source": "",
                "instanceId": "",
                "leaseId": "",
            }
        )
        self._commit(payload)

    @staticmethod
    def _observations(
        observations: Mapping[str, VoiceInputObservation],
    ) -> dict[str, VoiceInputObservation]:
        return {
            source: observations.get(source, VoiceInputObservation("unknown"))
            for source in VOICE_INPUT_SOURCES
        }

    def _observe_locked(
        self,
        observations: Mapping[str, VoiceInputObservation],
    ) -> None:
        observed = self._observations(observations)
        active = [source for source, value in observed.items() if value.state == "active"]
        if len(active) > 1:
            self._set_blocked()
            return

        if self._phase == "blocked":
            if not active and all(
                value.state == "inactive" for value in observed.values()
            ):
                self._set_unowned()
            return

        if self._phase == "bootstrap":
            if any(value.state == "unknown" for value in observed.values()):
                return
            if active:
                source = active[0]
                instance_id = observed[source].instance_id
                if not instance_id:
                    self._set_blocked()
                    return
                self._set_owned(source, instance_id)
                return
            self._set_unowned()
            return

        if self._phase == "owned":
            owner = observed[self._source]
            if active and active[0] != self._source:
                self._set_blocked()
                return
            if (
                owner.state == "inactive"
                and owner.instance_id
                and owner.instance_id != self._instance_id
                and all(
                    value.state == "inactive"
                    for peer, value in observed.items()
                    if peer != self._source
                )
            ):
                self._set_unowned()
            return

        if self._phase == "unowned":
            if any(value.state == "unknown" for value in observed.values()):
                return
            if active:
                source = active[0]
                instance_id = observed[source].instance_id
                if instance_id:
                    self._set_owned(source, instance_id)
                else:
                    self._set_blocked()

    def observe(
        self,
        observations: Mapping[str, VoiceInputObservation],
    ) -> dict[str, Any]:
        with self._lock:
            if self._persistence_failed:
                return self.public_status()
            self._observe_locked(observations)
            return self.public_status()

    def acquire(
        self,
        source: Any,
        instance_id: Any,
        *,
        observations: Mapping[str, VoiceInputObservation],
    ) -> dict[str, Any]:
        source = _valid_source(source)
        instance_id = _valid_instance_id(instance_id)
        with self._lock:
            if self._persistence_failed:
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            observed = self._observations(observations)
            if self._phase == "owned":
                owner = observed[self._source]
                replaced_inactive_owner = bool(
                    owner.state == "inactive"
                    and owner.instance_id
                    and owner.instance_id != self._instance_id
                )
                if replaced_inactive_owner and any(
                    value.state == "unknown"
                    for peer, value in observed.items()
                    if peer != self._source
                ):
                    raise VoiceInputLeaseError(
                        "voice_input_lease_unavailable",
                        status=503,
                    )
                if replaced_inactive_owner and all(
                    value.state == "inactive"
                    for peer, value in observed.items()
                    if peer != self._source
                ):
                    self._observe_locked(observed)
            if self._phase == "owned":
                if self._source == source and self._instance_id == instance_id:
                    peers = [
                        value
                        for peer, value in observed.items()
                        if peer != source
                    ]
                    if any(value.state == "active" for value in peers):
                        self._set_blocked()
                        raise VoiceInputLeaseError(
                            "voice_input_lease_conflict"
                        )
                    if any(value.state != "inactive" for value in peers):
                        raise VoiceInputLeaseError(
                            "voice_input_lease_unavailable",
                            status=503,
                        )
                    return {
                        "source": source,
                        "instanceId": instance_id,
                        "leaseId": self._lease_id,
                    }
                if any(
                    peer != self._source and value.state == "active"
                    for peer, value in observed.items()
                ):
                    self._set_blocked()
                raise VoiceInputLeaseError("voice_input_lease_conflict")
            self._observe_locked(observed)
            if self._phase == "blocked" or self._phase == "bootstrap":
                raise VoiceInputLeaseError("voice_input_lease_unavailable", status=503)
            if self._phase == "owned":
                if self._source == source and self._instance_id == instance_id:
                    return {
                        "source": source,
                        "instanceId": instance_id,
                        "leaseId": self._lease_id,
                    }
                raise VoiceInputLeaseError("voice_input_lease_conflict")
            if any(
                value.state != "inactive"
                for value in observed.values()
            ):
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            self._set_owned(source, instance_id)
            return {
                "source": source,
                "instanceId": instance_id,
                "leaseId": self._lease_id,
            }

    def release(
        self,
        source: Any,
        instance_id: Any,
        lease_id: Any,
    ) -> dict[str, Any]:
        source = _valid_source(source)
        instance_id = _valid_instance_id(instance_id)
        lease_id = str(lease_id or "").strip()
        if _INSTANCE_ID_PATTERN.fullmatch(lease_id) is None:
            raise VoiceInputLeaseError("invalid_voice_input_lease_id", status=400)
        with self._lock:
            if self._persistence_failed:
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            if self._phase == "unowned" and (
                source,
                instance_id,
                lease_id,
            ) == (
                self._last_released_source,
                self._last_released_instance_id,
                self._last_released_lease_id,
            ):
                return {"released": True}
            if self._phase != "owned" or (
                source,
                instance_id,
                lease_id,
            ) != (self._source, self._instance_id, self._lease_id):
                raise VoiceInputLeaseError("voice_input_lease_mismatch")
            self._set_unowned()
            return {"released": True}

    def release_if_inactive(
        self,
        source: Any,
        instance_id: Any,
        *,
        observations: Mapping[str, VoiceInputObservation],
    ) -> dict[str, Any]:
        source = _valid_source(source)
        instance_id = _valid_instance_id(instance_id)
        with self._lock:
            if self._persistence_failed:
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            if self._phase != "owned" or source != self._source:
                raise VoiceInputLeaseError("voice_input_lease_mismatch")
            observed = self._observations(observations)
            owner = observed[source]
            if any(
                peer != source and value.state == "active"
                for peer, value in observed.items()
            ):
                self._set_blocked()
                raise VoiceInputLeaseError("voice_input_lease_conflict")
            if not (
                owner.state == "inactive"
                and owner.instance_id == instance_id
                and all(
                    value.state == "inactive"
                    for peer, value in observed.items()
                    if peer != source
                )
            ):
                raise VoiceInputLeaseError(
                    "voice_input_lease_unavailable",
                    status=503,
                )
            self._set_unowned()
            return {"released": True}

    def public_status(self) -> dict[str, Any]:
        phase, source = self._public_snapshot
        return {
            "state": phase,
            "source": source,
        }


class DiscordVoiceInputLeaseClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        instance_id: str | None = None,
        timeout_sec: float = 3.0,
        release_retry_delay_sec: float = 0.25,
    ) -> None:
        host = os.getenv("CONTROL_PAGE_BOT_API_HOST", "bot_api").strip() or "bot_api"
        port = os.getenv("CONTROL_PAGE_BOT_API_PORT", "8798").strip() or "8798"
        self.base_url = (base_url or f"http://{host}:{port}").rstrip("/")
        self.token = (token if token is not None else os.getenv(VOICE_INPUT_LEASE_TOKEN_ENV, "")).strip()
        self.instance_id = _valid_instance_id(instance_id or _PROCESS_INSTANCE_ID)
        self.timeout_sec = max(0.2, float(timeout_sec))
        self.release_retry_delay_sec = max(
            0.01,
            float(release_retry_delay_sec),
        )
        self._lock = asyncio.Lock()
        self._listener_tokens: set[str] = set()
        self._lease_id = ""
        self._ambiguous_acquire = False
        self._release_retry_task: asyncio.Task[None] | None = None

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if len(self.token) < 32:
            raise VoiceInputLeaseError("voice_input_lease_unconfigured", status=503)
        timeout = ClientTimeout(total=self.timeout_sec)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}{VOICE_INPUT_LEASE_ENDPOINT}",
                json=payload,
                headers={VOICE_INPUT_LEASE_AUTH_HEADER: self.token},
            ) as response:
                try:
                    result = await response.json(content_type=None)
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise VoiceInputLeaseError("voice_input_lease_invalid_response", status=503) from None
                if response.status != 200 or not isinstance(result, dict) or result.get("ok") is not True:
                    code = str(result.get("error") or "voice_input_lease_unavailable") if isinstance(result, dict) else "voice_input_lease_unavailable"
                    raise VoiceInputLeaseError(code, status=response.status)
                return result

    @staticmethod
    def _acquired_lease_id(result: Any) -> str:
        lease_id = str(result.get("leaseId") or "") if isinstance(result, dict) else ""
        if _INSTANCE_ID_PATTERN.fullmatch(lease_id) is None:
            raise VoiceInputLeaseError(
                "voice_input_lease_invalid_response",
                status=503,
            )
        return lease_id

    @staticmethod
    async def _drain_shielded_request(
        task: asyncio.Task[dict[str, Any]],
    ) -> dict[str, Any]:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done():
                    return task.result()

    def _mark_ambiguous_acquire_locked(self) -> None:
        if self._listener_tokens:
            return
        self._lease_id = ""
        self._ambiguous_acquire = True
        self._ensure_release_retry_locked()

    @staticmethod
    def _acquire_outcome_is_ambiguous(error: BaseException) -> bool:
        return not isinstance(error, VoiceInputLeaseError) or (
            error.code == "voice_input_lease_invalid_response"
        )

    async def acquire(self) -> str:
        listener_token = secrets.token_hex(16)
        payload = {
            "action": "acquire",
            "source": "discord_voice",
            "instanceId": self.instance_id,
        }
        async with self._lock:
            request_task = asyncio.create_task(self._request(payload))
            try:
                result = await asyncio.shield(request_task)
                lease_id = self._acquired_lease_id(result)
            except asyncio.CancelledError as cancelled:
                try:
                    result = await self._drain_shielded_request(request_task)
                    lease_id = self._acquired_lease_id(result)
                except BaseException as error:
                    if self._acquire_outcome_is_ambiguous(error):
                        self._mark_ambiguous_acquire_locked()
                else:
                    self._lease_id = lease_id
                    self._ambiguous_acquire = False
                    if not self._listener_tokens:
                        try:
                            await self._release_last_listener_locked()
                        except asyncio.CancelledError:
                            self._ensure_release_retry_locked()
                        except Exception:
                            self._ensure_release_retry_locked()
                raise cancelled
            except Exception as error:
                if self._acquire_outcome_is_ambiguous(error):
                    self._mark_ambiguous_acquire_locked()
                raise
            self._lease_id = lease_id
            self._ambiguous_acquire = False
            self._listener_tokens.add(listener_token)
        return listener_token

    async def _release_last_listener_locked(self) -> None:
        result = await self._request(
            {
                "action": "release",
                "source": "discord_voice",
                "instanceId": self.instance_id,
                "leaseId": self._lease_id,
            }
        )
        if result.get("released") is not True:
            raise VoiceInputLeaseError(
                "voice_input_lease_release_failed",
                status=503,
            )
        self._lease_id = ""
        self._ambiguous_acquire = False

    def _ensure_release_retry_locked(self) -> None:
        if (
            self._release_retry_task is None
            or self._release_retry_task.done()
        ):
            self._release_retry_task = asyncio.create_task(
                self._retry_pending_release()
            )

    async def _retry_pending_release(self) -> None:
        delay = self.release_retry_delay_sec
        try:
            while True:
                await asyncio.sleep(delay)
                async with self._lock:
                    if self._listener_tokens:
                        return
                    try:
                        if self._ambiguous_acquire:
                            result = await self._request(
                                {
                                    "action": "acquire",
                                    "source": "discord_voice",
                                    "instanceId": self.instance_id,
                                }
                            )
                            self._lease_id = self._acquired_lease_id(result)
                            self._ambiguous_acquire = False
                        if not self._lease_id:
                            return
                        await self._release_last_listener_locked()
                    except Exception:
                        delay = min(delay * 2.0, 5.0)
                        continue
                    return
        finally:
            if self._release_retry_task is asyncio.current_task():
                self._release_retry_task = None

    async def release(self, listener_token: str) -> None:
        token = str(listener_token or "").strip()
        async with self._lock:
            if token not in self._listener_tokens:
                return
            self._listener_tokens.remove(token)
            if self._listener_tokens or not self._lease_id:
                return
            try:
                await self._release_last_listener_locked()
            except asyncio.CancelledError:
                self._ensure_release_retry_locked()
                raise
            except Exception:
                self._ensure_release_retry_locked()


_DISCORD_CLIENT: DiscordVoiceInputLeaseClient | None = None


def _discord_client() -> DiscordVoiceInputLeaseClient:
    global _DISCORD_CLIENT
    if _DISCORD_CLIENT is None:
        _DISCORD_CLIENT = DiscordVoiceInputLeaseClient()
    return _DISCORD_CLIENT


async def acquire_discord_voice_input_lease() -> str:
    return await _discord_client().acquire()


async def release_discord_voice_input_lease(listener_token: str) -> None:
    await _discord_client().release(listener_token)


__all__ = [
    "DiscordVoiceInputLeaseClient",
    "VOICE_INPUT_LEASE_AUTH_HEADER",
    "VOICE_INPUT_LEASE_ENDPOINT",
    "VOICE_INPUT_LEASE_SCHEMA",
    "VOICE_INPUT_LEASE_TOKEN_ENV",
    "VOICE_INPUT_RETIREMENT_CLAIM_SCHEMA",
    "VOICE_INPUT_RETIREMENT_RESULT_SCHEMA",
    "VoiceInputLeaseError",
    "VoiceInputLeaseManager",
    "VoiceInputObservation",
    "acquire_discord_voice_input_lease",
    "discord_voice_input_instance_id",
    "release_discord_voice_input_lease",
]
