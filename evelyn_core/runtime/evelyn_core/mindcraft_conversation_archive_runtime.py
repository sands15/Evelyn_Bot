from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen


_TRANSPORT_KEY_DOMAIN = b"evelyn.private-conversation-archive.transport-key.v1\n"
_TRANSPORT_PURPOSE = "minecraft"
_MAX_RESPONSE_BYTES = 1_048_576
_EVENT_SCHEMA = "conversation.archive.minecraft-result.v1"
_LIFECYCLE_EVENT_SCHEMA = (
    "conversation.archive.minecraft-lifecycle-result.v1"
)
_EVENT_FIELDS = frozenset(
    {
        "schema",
        "eventType",
        "mode",
        "surface",
        "recordType",
        "guildId",
        "parentRecordIds",
        "goalRunId",
        "actionRunId",
        "actionKey",
        "contractCode",
        "candidateSequence",
        "executionSequence",
        "observedAt",
        "evidenceCode",
        "postconditionCode",
        "verified",
        "succeeded",
        "worldChanged",
        "goalProgress",
        "idempotencyKey",
        "contentFree",
    }
)
_BODY_FIELDS = (
    "schema",
    "eventType",
    "goalRunId",
    "actionRunId",
    "actionKey",
    "contractCode",
    "candidateSequence",
    "executionSequence",
    "observedAt",
    "evidenceCode",
    "postconditionCode",
    "verified",
    "succeeded",
    "worldChanged",
    "goalProgress",
    "contentFree",
)
_LIFECYCLE_EVENT_FIELDS = frozenset(
    {
        "schema",
        "eventType",
        "mode",
        "surface",
        "recordType",
        "guildId",
        "parentRecordIds",
        "operation",
        "outcomeCode",
        "observedAt",
        "verified",
        "succeeded",
        "idempotencyKey",
        "contentFree",
    }
)
_LIFECYCLE_BODY_FIELDS = (
    "schema",
    "eventType",
    "operation",
    "outcomeCode",
    "observedAt",
    "verified",
    "succeeded",
    "contentFree",
)
_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
)


class MindcraftConversationArchiveError(RuntimeError):
    def __init__(self, code: str, *, status: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.status = int(status)


HttpRequest = Callable[
    [str, str, bytes, Mapping[str, str], float],
    tuple[int, bytes],
]


class MindcraftConversationArchiveClient:
    """Synchronous, purpose-limited archive adapter for verified world effects."""

    def __init__(
        self,
        *,
        base_url: str,
        master_key: bytes,
        http_request: HttpRequest | None = None,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[int], str] = secrets.token_hex,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        base = str(base_url or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("archive_base_url_invalid")
        key = bytes(master_key)
        if len(key) < 32:
            raise ValueError("archive_transport_key_too_short")
        if request_timeout_seconds <= 0:
            raise ValueError("archive_request_timeout_invalid")
        self._base_url = base
        self._master_key = key
        self._http_request = http_request or _urlopen_request
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._generation: str | None = None
        self._sequence = 0
        self._lock = threading.RLock()

    @classmethod
    def from_key_file(
        cls,
        *,
        base_url: str,
        key_file: str | Path,
        **kwargs: Any,
    ) -> "MindcraftConversationArchiveClient":
        path = Path(key_file)
        if path.is_symlink() or not path.is_file():
            raise ValueError("archive_transport_key_file_invalid")
        try:
            key = path.read_bytes()
        except OSError:
            raise ValueError("archive_transport_key_file_invalid") from None
        return cls(base_url=base_url, master_key=key, **kwargs)

    @property
    def generation(self) -> str | None:
        return self._generation

    def validate_ready(self) -> tuple[bool, str]:
        try:
            with self._lock:
                self._activate_generation()
                response = self._request(
                    "POST",
                    "/internal/conversation-archive/minecraft/ready",
                    {"generation": self._require_generation()},
                )
                if response.get("ok") is not True:
                    raise MindcraftConversationArchiveError(
                        "archive_ready_receipt_invalid"
                    )
                if response.get("ready") is not True:
                    return False, _safe_error_code(
                        response.get("state"),
                        "mindcraft_world_effect_archive_unavailable",
                    )
                return True, ""
        except MindcraftConversationArchiveError as exc:
            return False, exc.code
        except Exception:
            return False, "mindcraft_world_effect_archive_unavailable"

    def archive_verified_effect(self, event: Any) -> tuple[bool, str]:
        try:
            validated = _validate_event(event)
            with self._lock:
                self._activate_generation()
                sequence = self._sequence + 1
                record_id = self._stable_handle(
                    "record", str(validated["idempotencyKey"])
                )
                observed_at = float(validated["observedAt"])
                body_fields = (
                    _LIFECYCLE_BODY_FIELDS
                    if validated["schema"] == _LIFECYCLE_EVENT_SCHEMA
                    else _BODY_FIELDS
                )
                body = json.dumps(
                    {key: validated[key] for key in body_fields},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                response = self._request(
                    "POST",
                    "/internal/conversation-archive/minecraft/record",
                    {
                        "generation": self._require_generation(),
                        "sequence": sequence,
                        "idempotencyKey": validated["idempotencyKey"],
                        "recordId": record_id,
                        "startedAt": observed_at,
                        "endedAt": observed_at,
                        "parentRecordIds": list(validated["parentRecordIds"]),
                        "body": body,
                    },
                )
                if (
                    response.get("ok") is not True
                    or response.get("recordId") != record_id
                ):
                    raise MindcraftConversationArchiveError(
                        "archive_record_receipt_invalid"
                    )
                self._sequence = sequence
                return True, ""
        except MindcraftConversationArchiveError as exc:
            return False, exc.code
        except (TypeError, ValueError, OverflowError):
            return False, "mindcraft_world_effect_archive_event_invalid"
        except Exception:
            return False, "mindcraft_world_effect_archive_unavailable"

    def _activate_generation(self) -> None:
        response = self._request(
            "POST",
            "/internal/conversation-archive/minecraft/generation",
            {},
        )
        generation = response.get("generation")
        if response.get("ok") is not True or not _identifier_valid(
            generation, maximum=128
        ):
            raise MindcraftConversationArchiveError(
                "archive_generation_receipt_invalid"
            )
        if generation != self._generation:
            self._generation = str(generation)
            self._sequence = 0

    def _require_generation(self) -> str:
        if self._generation is None:
            raise MindcraftConversationArchiveError(
                "archive_generation_missing"
            )
        return self._generation

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        last_error: BaseException | None = None
        for _attempt in range(2):
            timestamp = str(int(self._clock()))
            nonce = str(self._nonce_factory(16))
            if len(nonce) != 32 or any(
                character not in "0123456789abcdef" for character in nonce
            ):
                raise MindcraftConversationArchiveError(
                    "archive_transport_nonce_invalid"
                )
            headers = self._signed_headers(
                method=method,
                path=path,
                body=body,
                timestamp=timestamp,
                nonce=nonce,
            )
            try:
                status, raw = self._http_request(
                    method,
                    f"{self._base_url}{path}",
                    body,
                    headers,
                    self._request_timeout_seconds,
                )
            except Exception as exc:
                last_error = exc
                continue
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise MindcraftConversationArchiveError(
                    "archive_response_too_large", status=status
                )
            try:
                decoded = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MindcraftConversationArchiveError(
                    "archive_response_invalid", status=status
                ) from exc
            if not isinstance(decoded, dict):
                raise MindcraftConversationArchiveError(
                    "archive_response_invalid", status=status
                )
            if status < 200 or status >= 300:
                raise MindcraftConversationArchiveError(
                    _safe_error_code(
                        decoded.get("error"), "archive_request_failed"
                    ),
                    status=status,
                )
            return decoded
        raise MindcraftConversationArchiveError(
            "archive_transport_unavailable"
        ) from last_error

    def _signed_headers(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        timestamp: str,
        nonce: str,
    ) -> dict[str, str]:
        key = hmac.new(
            self._master_key,
            _TRANSPORT_KEY_DOMAIN + _TRANSPORT_PURPOSE.encode("ascii"),
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                _TRANSPORT_PURPOSE,
                method.upper(),
                path,
                timestamp,
                nonce,
                hashlib.sha256(body).hexdigest(),
            )
        ).encode("utf-8")
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "X-Evelyn-Archive-Timestamp": timestamp,
            "X-Evelyn-Archive-Nonce": nonce,
            "X-Evelyn-Archive-Signature": hmac.new(
                key, canonical, hashlib.sha256
            ).hexdigest(),
        }

    def _stable_handle(self, domain: str, value: str) -> str:
        return hmac.new(
            self._master_key,
            f"evelyn.private-conversation-archive.{domain}.v1\n{value}".encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).hexdigest()[:32]


def _urlopen_request(
    method: str,
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    request = Request(
        url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            return int(response.status), raw
    except HTTPError as exc:
        return int(exc.code), exc.read(_MAX_RESPONSE_BYTES + 1)


def _validate_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("archive_event_fields_invalid")
    event = dict(value)
    if event.get("schema") == _LIFECYCLE_EVENT_SCHEMA:
        return _validate_lifecycle_event(event)
    if set(event) != _EVENT_FIELDS:
        raise ValueError("archive_event_fields_invalid")
    exact_values = {
        "schema": _EVENT_SCHEMA,
        "eventType": "minecraft_result",
        "mode": "discord_shared",
        "surface": "minecraft",
        "recordType": "minecraft_result",
    }
    if any(event.get(key) != expected for key, expected in exact_values.items()):
        raise ValueError("archive_event_contract_invalid")
    if any(
        event.get(key) is not True
        for key in (
            "verified",
            "succeeded",
            "worldChanged",
            "goalProgress",
            "contentFree",
        )
    ):
        raise ValueError("archive_event_unverified")
    for key, maximum in (
        ("guildId", 32),
        ("goalRunId", 128),
        ("actionRunId", 128),
        ("actionKey", 128),
        ("contractCode", 128),
        ("evidenceCode", 128),
        ("postconditionCode", 128),
        ("idempotencyKey", 256),
    ):
        if not _identifier_valid(event.get(key), maximum=maximum):
            raise ValueError("archive_event_identifier_invalid")
    parents = event.get("parentRecordIds")
    if (
        not isinstance(parents, list)
        or not 1 <= len(parents) <= 2
        or len(parents) != len(set(parents))
        or any(
            not _identifier_valid(parent, maximum=64)
            for parent in parents
        )
    ):
        raise ValueError("archive_event_lineage_invalid")
    for key in ("candidateSequence", "executionSequence"):
        if type(event.get(key)) is not int or event[key] <= 0:
            raise ValueError("archive_event_sequence_invalid")
    observed_at = event.get("observedAt")
    if (
        isinstance(observed_at, bool)
        or not isinstance(observed_at, (int, float))
        or not math.isfinite(float(observed_at))
        or float(observed_at) < 0
        or float(observed_at) > 100_000_000_000
    ):
        raise ValueError("archive_event_time_invalid")
    return event


def _validate_lifecycle_event(event: dict[str, Any]) -> dict[str, Any]:
    if set(event) != _LIFECYCLE_EVENT_FIELDS:
        raise ValueError("archive_event_fields_invalid")
    exact_values = {
        "schema": _LIFECYCLE_EVENT_SCHEMA,
        "eventType": "minecraft_result",
        "mode": "discord_shared",
        "surface": "minecraft",
        "recordType": "minecraft_result",
        "verified": True,
        "succeeded": True,
        "contentFree": True,
    }
    if any(event.get(key) != expected for key, expected in exact_values.items()):
        raise ValueError("archive_event_contract_invalid")
    outcomes = {
        "connect": "minecraft_connected",
        "goal": "minecraft_goal_confirmed",
        "disconnect": "minecraft_stopped",
    }
    if outcomes.get(event.get("operation")) != event.get("outcomeCode"):
        raise ValueError("archive_event_contract_invalid")
    for key, maximum in (
        ("guildId", 32),
        ("operation", 32),
        ("outcomeCode", 128),
        ("idempotencyKey", 256),
    ):
        if not _identifier_valid(event.get(key), maximum=maximum):
            raise ValueError("archive_event_identifier_invalid")
    parents = event.get("parentRecordIds")
    if (
        not isinstance(parents, list)
        or len(parents) != 1
        or not _identifier_valid(parents[0], maximum=64)
    ):
        raise ValueError("archive_event_lineage_invalid")
    observed_at = event.get("observedAt")
    if (
        isinstance(observed_at, bool)
        or not isinstance(observed_at, (int, float))
        or not math.isfinite(float(observed_at))
        or float(observed_at) < 0
        or float(observed_at) > 100_000_000_000
    ):
        raise ValueError("archive_event_time_invalid")
    return event


def _identifier_valid(value: Any, *, maximum: int) -> bool:
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value[0].isalnum()
        and value[0].isascii()
        and all(character in _ID_CHARACTERS for character in value)
    )


def _safe_error_code(value: Any, fallback: str) -> str:
    candidate = str(value or "")
    if _identifier_valid(candidate, maximum=128):
        return candidate
    return fallback


__all__ = [
    "MindcraftConversationArchiveClient",
    "MindcraftConversationArchiveError",
]
