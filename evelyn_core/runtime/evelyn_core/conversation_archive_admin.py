from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import ntpath
import os
import re
import secrets
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit


ADMIN_ATTESTATION_SCHEMA = "conversation_archive.admin-host-attestation.v1"
ADMIN_ATTESTATION_PURPOSE = "conversation_archive.admin.control"
ADMIN_AUTH_STATE_SCHEMA = "conversation_archive.admin-auth-state.v1"
ADMIN_HOST_SESSION_SCHEMA = "conversation_archive.admin-host-session.v1"
ADMIN_HOST_SESSION_PURPOSE = "conversation_archive.admin.host-session"
ADMIN_PUBLIC_STATUS_SCHEMA = "conversation_archive.admin-status.v1"
ADMIN_CONTROL_CAPABILITY = "admin.control"
ADMIN_SESSION_COOKIE_NAME = "__Host-evelyn_archive_admin"
ADMIN_SESSION_COOKIE_ATTRIBUTES = (
    "Secure; HttpOnly; SameSite=Strict; Path=/"
)
OTP_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits
OTP_CODE_RE = re.compile(r"^[A-Za-z0-9]{4}$")

_ATTESTATION_DOMAIN = b"evelyn.conversation-archive.admin-host-attestation.v1\n"
_STATE_DOMAIN = b"evelyn.conversation-archive.admin-auth-state.v1\n"
_BINDING_DOMAIN = b"evelyn.conversation-archive.admin-binding.v1\n"
_OTP_DOMAIN = b"evelyn.conversation-archive.admin-otp.v1\n"
_SESSION_DOMAIN = b"evelyn.conversation-archive.admin-session.v1\n"
_SCOPE_DOMAIN = b"evelyn.conversation-archive.admin-rate-scope.v1\n"
_ATTESTATION_REPLAY_DOMAIN = (
    b"evelyn.conversation-archive.admin-attestation-replay.v1\n"
)
_HOST_SESSION_DOMAIN = b"evelyn.conversation-archive.admin-host-session.v1\n"
_HOST_SESSION_BINDING_DOMAIN = (
    b"evelyn.conversation-archive.admin-host-session-binding.v1\n"
)

_SID_RE = re.compile(r"^S-\d(?:-\d+)+$")
_DISCORD_USER_ID_RE = re.compile(r"^[1-9]\d{4,23}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ATTESTATION_KEYS = frozenset(
    {
        "schema",
        "purpose",
        "adminSid",
        "adminAccount",
        "registeredDiscordUserId",
        "hostId",
        "bootId",
        "bootstrapNonce",
        "issuedAt",
        "expiresAt",
        "elevated",
        "administratorMember",
        "primary",
        "replica",
        "anchor",
        "authAlgorithm",
        "authTag",
    }
)
_VOLUME_KEYS = frozenset(
    {
        "role",
        "driveLetter",
        "volumeId",
        "diskId",
        "driveType",
        "fileSystem",
        "healthStatus",
        "bitLockerProtectionStatus",
        "bitLockerVolumeStatus",
        "lockStatus",
        "ownerSid",
        "mountNonce",
        "archivePath",
        "pathExists",
        "pathHasReparsePoint",
        "daclProtected",
        "nonAdminWriteDenied",
    }
)
_HOST_SESSION_KEYS = frozenset(
    {
        "schema",
        "purpose",
        "adminSid",
        "hostId",
        "bootId",
        "bootstrapNonce",
        "state",
        "updatedAt",
        "expiresAt",
        "authAlgorithm",
        "authTag",
    }
)
_PUBLIC_ERROR_STATE = {
    "admin_host_attestation_invalid": "host_verification_failed",
    "admin_host_attestation_expired": "host_verification_failed",
    "admin_host_attestation_replayed": "host_verification_failed",
    "admin_identity_mismatch": "host_verification_failed",
    "admin_storage_preflight_failed": "storage_preflight_failed",
    "admin_auth_state_invalid": "authorization_unavailable",
    "admin_auth_rate_limited": "rate_limited",
    "admin_otp_delivery_failed": "authentication_failed",
    "admin_otp_invalid": "authentication_failed",
    "admin_otp_expired": "authentication_failed",
    "admin_session_invalid": "authentication_required",
    "admin_session_expired": "authentication_required",
    "admin_host_session_invalid": "host_verification_failed",
    "admin_loopback_required": "local_control_page_required",
}


class AdminSecurityError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        retry_after_sec: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_sec = retry_after_sec

    def public_projection(self) -> dict[str, Any]:
        projection: dict[str, Any] = {
            "schema": ADMIN_PUBLIC_STATUS_SCHEMA,
            "ok": False,
            "state": _PUBLIC_ERROR_STATE.get(
                self.code, "authorization_unavailable"
            ),
            "retryable": self.retryable,
        }
        if self.retry_after_sec is not None:
            projection["retryAfterSec"] = max(1, int(self.retry_after_sec))
        return projection


@dataclass(frozen=True, slots=True)
class LoopbackRequestEvidence:
    scheme: str
    host: str
    origin: str
    surface: str = "local_control_page"


@dataclass(frozen=True, slots=True, repr=False)
class OtpDelivery:
    challenge_id: str
    discord_user_id: str
    expires_at: int
    code: str = field(repr=False)

    def __repr__(self) -> str:
        return "OtpDelivery(<redacted>)"

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema": ADMIN_PUBLIC_STATUS_SCHEMA,
            "ok": True,
            "state": "otp_delivery_pending",
        }


@dataclass(frozen=True, slots=True, repr=False)
class AdminSessionGrant:
    token: str = field(repr=False)
    expires_at: int
    cookie_name: str = ADMIN_SESSION_COOKIE_NAME
    cookie_attributes: str = ADMIN_SESSION_COOKIE_ATTRIBUTES

    def __repr__(self) -> str:
        return "AdminSessionGrant(<redacted>)"

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema": ADMIN_PUBLIC_STATUS_SCHEMA,
            "ok": True,
            "state": "authenticated",
        }


@dataclass(frozen=True, slots=True)
class AdminSessionClaims:
    capability: str
    expires_at: int


def _require_secret_key(value: bytes | bytearray, *, code: str) -> bytes:
    key = bytes(value)
    if len(key) < 32:
        raise AdminSecurityError(code)
    return key


def _text(value: Any, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid_text")
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ValueError("invalid_text")
    return value


def _whole_second(value: Any) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or int(value) != float(value)
        or int(value) < 0
    ):
        raise ValueError("invalid_time")
    return int(value)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(key: bytes, domain: bytes, *values: str) -> str:
    message = bytearray(domain)
    for value in values:
        encoded = value.encode("utf-8")
        message.extend(str(len(encoded)).encode("ascii"))
        message.extend(b":")
        message.extend(encoded)
        message.extend(b"\n")
    return hmac.new(key, bytes(message), hashlib.sha256).hexdigest()


def _volume_attestation_lines(volume: dict[str, Any]) -> list[str]:
    return [
        _text(volume["role"], maximum=16),
        _text(volume["driveLetter"], maximum=3),
        _text(volume["volumeId"]),
        _text(volume["diskId"]),
        _text(volume["driveType"], maximum=16),
        _text(volume["fileSystem"], maximum=16),
        _text(volume["healthStatus"], maximum=32),
        _text(volume["bitLockerProtectionStatus"], maximum=32),
        _text(volume["bitLockerVolumeStatus"], maximum=32),
        _text(volume["lockStatus"], maximum=32),
        _text(volume["ownerSid"], maximum=184),
        _text(volume["mountNonce"], maximum=128),
        _text(volume["archivePath"]),
        "1" if volume["pathExists"] is True else "0",
        "1" if volume["pathHasReparsePoint"] is True else "0",
        "1" if volume["daclProtected"] is True else "0",
        "1" if volume["nonAdminWriteDenied"] is True else "0",
    ]


def _attestation_message(payload: dict[str, Any]) -> bytes:
    lines = [
        _text(payload["schema"], maximum=96),
        _text(payload["purpose"], maximum=96),
        _text(payload["adminSid"], maximum=184),
        _text(payload["adminAccount"], maximum=256),
        _text(payload["registeredDiscordUserId"], maximum=24),
        _text(payload["hostId"], maximum=255),
        _text(payload["bootId"], maximum=128),
        _text(payload["bootstrapNonce"], maximum=128),
        str(_whole_second(payload["issuedAt"])),
        str(_whole_second(payload["expiresAt"])),
        "1" if payload["elevated"] is True else "0",
        "1" if payload["administratorMember"] is True else "0",
        _text(payload["authAlgorithm"], maximum=32),
        *_volume_attestation_lines(payload["primary"]),
        *_volume_attestation_lines(payload["replica"]),
        *_volume_attestation_lines(payload["anchor"]),
    ]
    return _ATTESTATION_DOMAIN + "\n".join(lines).encode("utf-8") + b"\n"


def sign_host_attestation(
    payload: dict[str, Any], *, signing_key: bytes | bytearray
) -> dict[str, Any]:
    key = _require_secret_key(
        signing_key, code="admin_host_attestation_invalid"
    )
    unsigned = dict(payload)
    unsigned.pop("authTag", None)
    unsigned["authAlgorithm"] = "hmac-sha256"
    if set(unsigned) != _ATTESTATION_KEYS - {"authTag"}:
        raise AdminSecurityError("admin_host_attestation_invalid")
    if not all(
        isinstance(unsigned.get(name), dict)
        and set(unsigned[name]) == _VOLUME_KEYS
        for name in ("primary", "replica", "anchor")
    ):
        raise AdminSecurityError("admin_host_attestation_invalid")
    try:
        tag = hmac.new(key, _attestation_message(unsigned), hashlib.sha256).hexdigest()
    except (KeyError, TypeError, ValueError):
        raise AdminSecurityError("admin_host_attestation_invalid") from None
    return {**unsigned, "authTag": tag}


def _host_session_message(payload: dict[str, Any]) -> bytes:
    lines = [
        _text(payload["schema"], maximum=96),
        _text(payload["purpose"], maximum=96),
        _text(payload["adminSid"], maximum=184),
        _text(payload["hostId"], maximum=255),
        _text(payload["bootId"], maximum=128),
        _text(payload["bootstrapNonce"], maximum=128),
        _text(payload["state"], maximum=16),
        str(_whole_second(payload["updatedAt"])),
        str(_whole_second(payload["expiresAt"])),
        _text(payload["authAlgorithm"], maximum=32),
    ]
    return _HOST_SESSION_DOMAIN + "\n".join(lines).encode("utf-8") + b"\n"


def sign_host_session_marker(
    payload: dict[str, Any], *, signing_key: bytes | bytearray
) -> dict[str, Any]:
    key = _require_secret_key(
        signing_key, code="admin_host_session_invalid"
    )
    unsigned = dict(payload)
    unsigned.pop("authTag", None)
    unsigned["authAlgorithm"] = "hmac-sha256"
    if set(unsigned) != _HOST_SESSION_KEYS - {"authTag"}:
        raise AdminSecurityError("admin_host_session_invalid")
    try:
        tag = hmac.new(
            key, _host_session_message(unsigned), hashlib.sha256
        ).hexdigest()
    except (KeyError, TypeError, ValueError):
        raise AdminSecurityError("admin_host_session_invalid") from None
    return {**unsigned, "authTag": tag}


def verify_host_session_marker(
    payload: Any,
    *,
    signing_key: bytes | bytearray,
    expected_admin_sid: str,
    expected_host_id: str | None = None,
    now: int | float | None = None,
) -> dict[str, Any]:
    key = _require_secret_key(
        signing_key, code="admin_host_session_invalid"
    )
    current = int(time.time() if now is None else now)
    if (
        not isinstance(payload, dict)
        or set(payload) != _HOST_SESSION_KEYS
        or payload.get("schema") != ADMIN_HOST_SESSION_SCHEMA
        or payload.get("purpose") != ADMIN_HOST_SESSION_PURPOSE
        or payload.get("authAlgorithm") != "hmac-sha256"
        or not isinstance(payload.get("authTag"), str)
        or not _HEX_DIGEST_RE.fullmatch(payload["authTag"])
    ):
        raise AdminSecurityError("admin_host_session_invalid")
    try:
        expected_tag = sign_host_session_marker(
            payload, signing_key=key
        )["authTag"]
        admin_sid = _text(payload["adminSid"], maximum=184)
        host_id = _text(payload["hostId"], maximum=255)
        _text(payload["bootId"], maximum=128)
        nonce = _text(payload["bootstrapNonce"], maximum=128)
        state = _text(payload["state"], maximum=16)
        updated_at = _whole_second(payload["updatedAt"])
        expires_at = _whole_second(payload["expiresAt"])
    except (KeyError, TypeError, ValueError, AdminSecurityError):
        raise AdminSecurityError("admin_host_session_invalid") from None
    if not hmac.compare_digest(payload["authTag"], expected_tag):
        raise AdminSecurityError("admin_host_session_invalid")
    if (
        state != "active"
        or not _SID_RE.fullmatch(admin_sid)
        or not hmac.compare_digest(admin_sid, str(expected_admin_sid))
        or (expected_host_id is not None and host_id.casefold() != expected_host_id.casefold())
        or not _NONCE_RE.fullmatch(nonce)
        or updated_at > current + 5
        or updated_at <= current - 15
        or expires_at <= current
        or expires_at - updated_at > 330
    ):
        raise AdminSecurityError("admin_host_session_invalid")
    return dict(payload)


def _windows_path_equal(left: str, right: str) -> bool:
    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(
        ntpath.normpath(right)
    )


def verify_host_attestation(
    payload: Any,
    *,
    signing_key: bytes | bytearray,
    expected_admin_sid: str,
    expected_admin_account: str,
    expected_registered_discord_user_id: str,
    expected_primary_path: str = r"C:\ProgramData\Evelyn\private-audit",
    expected_replica_path: str = r"D:\EvelynBackup\private-audit",
    expected_anchor_path: str = r"C:\ProgramData\Evelyn\private-audit-anchor",
    expected_host_id: str | None = None,
    now: int | float | None = None,
) -> dict[str, Any]:
    key = _require_secret_key(
        signing_key, code="admin_host_attestation_invalid"
    )
    current = int(time.time() if now is None else now)
    if (
        not isinstance(payload, dict)
        or set(payload) != _ATTESTATION_KEYS
        or payload.get("schema") != ADMIN_ATTESTATION_SCHEMA
        or payload.get("purpose") != ADMIN_ATTESTATION_PURPOSE
        or payload.get("authAlgorithm") != "hmac-sha256"
        or not isinstance(payload.get("authTag"), str)
        or not _HEX_DIGEST_RE.fullmatch(payload["authTag"])
        or not isinstance(payload.get("primary"), dict)
        or not isinstance(payload.get("replica"), dict)
        or not isinstance(payload.get("anchor"), dict)
        or set(payload["primary"]) != _VOLUME_KEYS
        or set(payload["replica"]) != _VOLUME_KEYS
        or set(payload["anchor"]) != _VOLUME_KEYS
    ):
        raise AdminSecurityError("admin_host_attestation_invalid")
    try:
        expected_tag = sign_host_attestation(payload, signing_key=key)["authTag"]
        issued_at = _whole_second(payload["issuedAt"])
        expires_at = _whole_second(payload["expiresAt"])
        admin_sid = _text(payload["adminSid"], maximum=184)
        admin_account = _text(payload["adminAccount"], maximum=256)
        registered_discord_user_id = _text(
            payload["registeredDiscordUserId"], maximum=24
        )
        host_id = _text(payload["hostId"], maximum=255)
        _text(payload["bootId"], maximum=128)
        nonce = _text(payload["bootstrapNonce"], maximum=128)
    except (KeyError, TypeError, ValueError, AdminSecurityError):
        raise AdminSecurityError("admin_host_attestation_invalid") from None
    if not hmac.compare_digest(payload["authTag"], expected_tag):
        raise AdminSecurityError("admin_host_attestation_invalid")
    if issued_at > current + 5 or expires_at <= current or expires_at - issued_at > 90:
        raise AdminSecurityError("admin_host_attestation_expired")
    if (
        payload.get("elevated") is not True
        or payload.get("administratorMember") is not True
        or not _SID_RE.fullmatch(admin_sid)
        or not hmac.compare_digest(admin_sid, str(expected_admin_sid))
        or admin_account.casefold() != str(expected_admin_account).casefold()
        or not _DISCORD_USER_ID_RE.fullmatch(registered_discord_user_id)
        or not hmac.compare_digest(
            registered_discord_user_id,
            str(expected_registered_discord_user_id),
        )
        or (expected_host_id is not None and host_id.casefold() != expected_host_id.casefold())
        or not _NONCE_RE.fullmatch(nonce)
    ):
        raise AdminSecurityError("admin_identity_mismatch")

    primary = payload["primary"]
    replica = payload["replica"]
    anchor = payload["anchor"]
    try:
        volume_conditions = (
            primary["role"] == "primary",
            replica["role"] == "replica",
            anchor["role"] == "anchor",
            primary["driveLetter"].upper() == "C:",
            replica["driveLetter"].upper() == "D:",
            anchor["driveLetter"].upper() == "C:",
            _windows_path_equal(primary["archivePath"], expected_primary_path),
            _windows_path_equal(replica["archivePath"], expected_replica_path),
            _windows_path_equal(anchor["archivePath"], expected_anchor_path),
            primary["volumeId"].casefold() != replica["volumeId"].casefold(),
            primary["diskId"].casefold() != replica["diskId"].casefold(),
            primary["volumeId"].casefold() == anchor["volumeId"].casefold(),
            primary["diskId"].casefold() == anchor["diskId"].casefold(),
        )
        for volume in (primary, replica, anchor):
            _volume_attestation_lines(volume)
            volume_conditions += (
                volume["driveType"] == "Fixed",
                volume["fileSystem"].upper() == "NTFS",
                volume["healthStatus"] == "Healthy",
                volume["bitLockerProtectionStatus"] == "On",
                volume["bitLockerVolumeStatus"] == "FullyEncrypted",
                volume["lockStatus"] == "Unlocked",
                volume["ownerSid"]
                in {
                    admin_sid,
                    "S-1-5-18",
                    "S-1-5-32-544",
                },
                _NONCE_RE.fullmatch(volume["mountNonce"]) is not None,
                volume["pathExists"] is True,
                volume["pathHasReparsePoint"] is False,
                volume["daclProtected"] is True,
                volume["nonAdminWriteDenied"] is True,
            )
    except (KeyError, AttributeError, TypeError, ValueError):
        raise AdminSecurityError("admin_storage_preflight_failed") from None
    if not all(volume_conditions):
        raise AdminSecurityError("admin_storage_preflight_failed")
    return dict(payload)


def require_loopback_control_page(request: LoopbackRequestEvidence) -> None:
    if not isinstance(request, LoopbackRequestEvidence):
        raise AdminSecurityError("admin_loopback_required")
    try:
        origin = urlsplit(request.origin)
        if origin.username is not None or origin.password is not None:
            raise ValueError
        host_url = urlsplit(f"{request.scheme}://{request.host}")
        origin_ip = ipaddress.ip_address(str(origin.hostname))
        host_ip = ipaddress.ip_address(str(host_url.hostname))
        origin_port = origin.port or 443
        host_port = host_url.port or 443
    except (TypeError, ValueError):
        raise AdminSecurityError("admin_loopback_required") from None
    if (
        request.surface != "local_control_page"
        or request.scheme != "https"
        or origin.scheme != "https"
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or not origin_ip.is_loopback
        or not host_ip.is_loopback
        or origin_ip != host_ip
        or origin_port != host_port
    ):
        raise AdminSecurityError("admin_loopback_required")


class ConversationArchiveAdminAuth:
    def __init__(
        self,
        *,
        state_path: Path,
        authentication_key: bytes | bytearray,
        attestation_key: bytes | bytearray,
        expected_admin_sid: str,
        expected_admin_account: str,
        registered_discord_user_id: str,
        expected_primary_path: str = r"C:\ProgramData\Evelyn\private-audit",
        expected_replica_path: str = r"D:\EvelynBackup\private-audit",
        expected_anchor_path: str = r"C:\ProgramData\Evelyn\private-audit-anchor",
        expected_host_id: str | None = None,
        host_session_state_path: Path | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.authentication_key = _require_secret_key(
            authentication_key, code="admin_auth_state_invalid"
        )
        self.attestation_key = _require_secret_key(
            attestation_key, code="admin_host_attestation_invalid"
        )
        self.expected_admin_sid = _text(expected_admin_sid, maximum=184)
        self.expected_admin_account = _text(expected_admin_account, maximum=256)
        self.registered_discord_user_id = _text(
            registered_discord_user_id, maximum=32
        )
        if not self.registered_discord_user_id.isdecimal():
            raise AdminSecurityError("admin_identity_mismatch")
        self.expected_primary_path = expected_primary_path
        self.expected_replica_path = expected_replica_path
        self.expected_anchor_path = expected_anchor_path
        self.expected_host_id = expected_host_id
        self.host_session_state_path = (
            Path(host_session_state_path)
            if host_session_state_path is not None
            else None
        )
        self.now = now or time.time
        self._lock = threading.RLock()
        # Challenges and sessions remain on disk for tamper detection and
        # durable rate limits, but are usable only by this exact process.
        self._process_binding = secrets.token_urlsafe(32)

    def _empty_state(self) -> dict[str, Any]:
        return {
            "schema": ADMIN_AUTH_STATE_SCHEMA,
            "revision": 0,
            "challenges": {},
            "sessions": {},
            "issueEvents": [],
            "failureEvents": [],
            "usedAttestations": {},
        }

    def _state_tag(self, body: dict[str, Any]) -> str:
        return hmac.new(
            self.authentication_key,
            _STATE_DOMAIN + _canonical_json(body),
            hashlib.sha256,
        ).hexdigest()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            envelope = json.loads(self.state_path.read_text(encoding="utf-8"))
            body = envelope["body"]
            tag = envelope["authTag"]
            if (
                not isinstance(body, dict)
                or set(body) != set(self._empty_state())
                or body.get("schema") != ADMIN_AUTH_STATE_SCHEMA
                or not isinstance(tag, str)
                or not hmac.compare_digest(tag, self._state_tag(body))
                or not isinstance(body.get("revision"), int)
                or body["revision"] < 0
                or not all(
                    isinstance(body.get(name), expected)
                    for name, expected in (
                        ("challenges", dict),
                        ("sessions", dict),
                        ("issueEvents", list),
                        ("failureEvents", list),
                        ("usedAttestations", dict),
                    )
                )
            ):
                raise ValueError
            return body
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise AdminSecurityError("admin_auth_state_invalid") from None

    def _save_state(self, body: dict[str, Any]) -> None:
        body["revision"] = int(body["revision"]) + 1
        envelope = {"body": body, "authTag": self._state_tag(body)}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f".{self.state_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    envelope,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.state_path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise AdminSecurityError("admin_auth_state_invalid") from None

    def _scope_digest(self, host_id: str) -> str:
        return _digest(
            self.authentication_key,
            _SCOPE_DOMAIN,
            self.registered_discord_user_id,
            self.expected_admin_sid,
            host_id.casefold(),
        )

    def _binding_digest(self, attestation: dict[str, Any]) -> str:
        return _digest(
            self.authentication_key,
            _BINDING_DOMAIN,
            self.expected_admin_sid,
            attestation["hostId"].casefold(),
            attestation["bootId"],
            attestation["bootstrapNonce"],
            self.registered_discord_user_id,
            ADMIN_CONTROL_CAPABILITY,
        )

    def _host_session_binding(self, marker: dict[str, Any]) -> str:
        return _digest(
            self.authentication_key,
            _HOST_SESSION_BINDING_DOMAIN,
            marker["adminSid"],
            marker["hostId"].casefold(),
            marker["bootId"],
            marker["bootstrapNonce"],
        )

    def _load_host_session_marker(self, *, current: int) -> dict[str, Any] | None:
        path = self.host_session_state_path
        if path is None:
            return None
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError
            raw = path.read_bytes()
            if not raw or len(raw) > 64 * 1024:
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            raise AdminSecurityError("admin_host_session_invalid") from None
        return verify_host_session_marker(
            payload,
            signing_key=self.attestation_key,
            expected_admin_sid=self.expected_admin_sid,
            expected_host_id=self.expected_host_id,
            now=current,
        )

    def _require_host_session_binding(
        self, *, expected_binding: str, current: int
    ) -> None:
        marker = self._load_host_session_marker(current=current)
        if marker is None:
            return
        actual = self._host_session_binding(marker)
        if not hmac.compare_digest(actual, str(expected_binding)):
            raise AdminSecurityError("admin_host_session_invalid")

    @staticmethod
    def _prune_state(state: dict[str, Any], now: int) -> None:
        state["challenges"] = {
            key: value
            for key, value in state["challenges"].items()
            if isinstance(value, dict) and int(value.get("expiresAt", 0)) > now
        }
        state["sessions"] = {
            key: value
            for key, value in state["sessions"].items()
            if isinstance(value, dict)
            and int(value.get("expiresAt", 0)) > now
            and int(value.get("lastSeenAt", 0)) + 120 > now
        }
        state["issueEvents"] = [
            event
            for event in state["issueEvents"]
            if isinstance(event, list)
            and len(event) == 2
            and isinstance(event[0], int)
            and event[0] > now - 86400
        ]
        state["failureEvents"] = [
            event
            for event in state["failureEvents"]
            if isinstance(event, list)
            and len(event) == 2
            and isinstance(event[0], int)
            and event[0] > now - 600
        ]
        state["usedAttestations"] = {
            key: expiry
            for key, expiry in state["usedAttestations"].items()
            if isinstance(expiry, int) and expiry > now
        }

    @staticmethod
    def _retry_after(events: list[list[Any]], *, now: int, window: int) -> int:
        oldest = min(int(event[0]) for event in events)
        return max(1, oldest + window - now)

    def _enforce_issue_rate(
        self, state: dict[str, Any], *, scope: str, now: int
    ) -> None:
        scoped_10m = [
            event
            for event in state["issueEvents"]
            if event[1] == scope and event[0] > now - 600
        ]
        scoped_day = [
            event for event in state["issueEvents"] if event[1] == scope
        ]
        global_10m = [
            event for event in state["issueEvents"] if event[0] > now - 600
        ]
        if len(scoped_10m) >= 3:
            raise AdminSecurityError(
                "admin_auth_rate_limited",
                retryable=True,
                retry_after_sec=self._retry_after(
                    scoped_10m, now=now, window=600
                ),
            )
        if len(scoped_day) >= 10:
            raise AdminSecurityError(
                "admin_auth_rate_limited",
                retryable=True,
                retry_after_sec=self._retry_after(
                    scoped_day, now=now, window=86400
                ),
            )
        if len(global_10m) >= 10:
            raise AdminSecurityError(
                "admin_auth_rate_limited",
                retryable=True,
                retry_after_sec=self._retry_after(
                    global_10m, now=now, window=600
                ),
            )

    def _enforce_failure_rate(
        self, state: dict[str, Any], *, scope: str, now: int
    ) -> None:
        scoped = [
            event
            for event in state["failureEvents"]
            if event[1] == scope and event[0] > now - 600
        ]
        current = [
            event for event in state["failureEvents"] if event[0] > now - 600
        ]
        if len(scoped) >= 10 or len(current) >= 30:
            relevant = scoped if len(scoped) >= 10 else current
            raise AdminSecurityError(
                "admin_auth_rate_limited",
                retryable=True,
                retry_after_sec=self._retry_after(
                    relevant, now=now, window=600
                ),
            )

    def begin_admin_login(self, attestation: Any) -> OtpDelivery:
        current = int(self.now())
        verified = verify_host_attestation(
            attestation,
            signing_key=self.attestation_key,
            expected_admin_sid=self.expected_admin_sid,
            expected_admin_account=self.expected_admin_account,
            expected_registered_discord_user_id=(
                self.registered_discord_user_id
            ),
            expected_primary_path=self.expected_primary_path,
            expected_replica_path=self.expected_replica_path,
            expected_anchor_path=self.expected_anchor_path,
            expected_host_id=self.expected_host_id,
            now=current,
        )
        scope = self._scope_digest(verified["hostId"])
        binding = self._binding_digest(verified)
        host_session_marker = self._load_host_session_marker(current=current)
        host_session_binding = ""
        if host_session_marker is not None:
            for field in ("adminSid", "hostId", "bootId", "bootstrapNonce"):
                left = str(host_session_marker[field])
                right = str(verified[field])
                if field == "hostId":
                    left = left.casefold()
                    right = right.casefold()
                if not hmac.compare_digest(left, right):
                    raise AdminSecurityError("admin_host_session_invalid")
            host_session_binding = self._host_session_binding(
                host_session_marker
            )
        replay_id = _digest(
            self.authentication_key,
            _ATTESTATION_REPLAY_DOMAIN,
            verified["authTag"],
        )
        with self._lock:
            state = self._load_state()
            self._prune_state(state, current)
            if replay_id in state["usedAttestations"]:
                raise AdminSecurityError("admin_host_attestation_replayed")
            self._enforce_issue_rate(state, scope=scope, now=current)
            for challenge_id, challenge in list(state["challenges"].items()):
                if challenge.get("scopeDigest") == scope:
                    del state["challenges"][challenge_id]
            challenge_id = secrets.token_urlsafe(24)
            while challenge_id in state["challenges"]:
                challenge_id = secrets.token_urlsafe(24)
            code = "".join(secrets.choice(OTP_ALPHABET) for _ in range(4))
            if not OTP_CODE_RE.fullmatch(code):
                raise AdminSecurityError("admin_auth_state_invalid")
            expires_at = min(current + 60, int(verified["expiresAt"]))
            state["challenges"][challenge_id] = {
                "bindingDigest": binding,
                "hostSessionBinding": host_session_binding,
                "scopeDigest": scope,
                "processBinding": self._process_binding,
                "otpDigest": _digest(
                    self.authentication_key,
                    _OTP_DOMAIN,
                    challenge_id,
                    binding,
                    code,
                ),
                "expiresAt": expires_at,
                "attempts": 0,
            }
            state["issueEvents"].append([current, scope])
            state["usedAttestations"][replay_id] = int(verified["expiresAt"])
            self._save_state(state)
        return OtpDelivery(
            challenge_id=challenge_id,
            discord_user_id=self.registered_discord_user_id,
            expires_at=expires_at,
            code=code,
        )

    def discard_challenge(self, challenge_id: str) -> None:
        with self._lock:
            state = self._load_state()
            state["challenges"].pop(str(challenge_id), None)
            self._save_state(state)

    def complete_admin_login(
        self,
        *,
        challenge_id: str,
        code: str,
        request: LoopbackRequestEvidence,
    ) -> AdminSessionGrant:
        require_loopback_control_page(request)
        current = int(self.now())
        with self._lock:
            state = self._load_state()
            self._prune_state(state, current)
            challenge = state["challenges"].get(str(challenge_id))
            if (
                not isinstance(challenge, dict)
                or not hmac.compare_digest(
                    str(challenge.get("processBinding", "")),
                    self._process_binding,
                )
            ):
                state["challenges"].pop(str(challenge_id), None)
                self._save_state(state)
                raise AdminSecurityError("admin_otp_expired")
            self._require_host_session_binding(
                expected_binding=str(
                    challenge.get("hostSessionBinding", "")
                ),
                current=current,
            )
            scope = challenge.get("scopeDigest", "")
            self._enforce_failure_rate(state, scope=scope, now=current)
            candidate = (
                code
                if isinstance(code, str) and OTP_CODE_RE.fullmatch(code)
                else "\x00\x00\x00\x00"
            )
            expected = _digest(
                self.authentication_key,
                _OTP_DOMAIN,
                str(challenge_id),
                str(challenge.get("bindingDigest", "")),
                candidate,
            )
            valid = candidate == code and hmac.compare_digest(
                str(challenge.get("otpDigest", "")), expected
            )
            if not valid:
                challenge["attempts"] = int(challenge.get("attempts", 0)) + 1
                state["failureEvents"].append([current, scope])
                if challenge["attempts"] >= 3:
                    del state["challenges"][str(challenge_id)]
                self._save_state(state)
                raise AdminSecurityError("admin_otp_invalid")

            del state["challenges"][str(challenge_id)]
            token = secrets.token_urlsafe(32)
            token_digest = _digest(
                self.authentication_key, _SESSION_DOMAIN, token
            )
            expires_at = current + 300
            state["sessions"][token_digest] = {
                "capability": ADMIN_CONTROL_CAPABILITY,
                "bindingDigest": challenge["bindingDigest"],
                "scopeDigest": challenge["scopeDigest"],
                "hostSessionBinding": challenge.get(
                    "hostSessionBinding", ""
                ),
                "processBinding": self._process_binding,
                "issuedAt": current,
                "lastSeenAt": current,
                "expiresAt": expires_at,
            }
            self._save_state(state)
        return AdminSessionGrant(token=token, expires_at=expires_at)

    def require_admin_session(
        self,
        *,
        token: str,
        request: LoopbackRequestEvidence,
    ) -> AdminSessionClaims:
        require_loopback_control_page(request)
        current = int(self.now())
        candidate = token if isinstance(token, str) else ""
        token_digest = _digest(
            self.authentication_key, _SESSION_DOMAIN, candidate
        )
        with self._lock:
            state = self._load_state()
            session = state["sessions"].get(token_digest)
            if not isinstance(session, dict):
                raise AdminSecurityError("admin_session_invalid")
            self._require_host_session_binding(
                expected_binding=str(
                    session.get("hostSessionBinding", "")
                ),
                current=current,
            )
            if (
                not candidate
                or session.get("capability") != ADMIN_CONTROL_CAPABILITY
                or not hmac.compare_digest(
                    str(session.get("processBinding", "")),
                    self._process_binding,
                )
                or int(session.get("expiresAt", 0)) <= current
                or int(session.get("lastSeenAt", 0)) + 120 <= current
            ):
                state["sessions"].pop(token_digest, None)
                self._save_state(state)
                raise AdminSecurityError("admin_session_expired")
            session["lastSeenAt"] = current
            self._save_state(state)
            return AdminSessionClaims(
                capability=ADMIN_CONTROL_CAPABILITY,
                expires_at=int(session["expiresAt"]),
            )

    def register_step_up_issue(
        self,
        *,
        token: str,
        request: LoopbackRequestEvidence,
    ) -> None:
        """Count a step-up code in the same durable OTP rate bucket."""

        self.require_admin_session(token=token, request=request)
        current = int(self.now())
        token_digest = _digest(
            self.authentication_key,
            _SESSION_DOMAIN,
            token if isinstance(token, str) else "",
        )
        with self._lock:
            state = self._load_state()
            self._prune_state(state, current)
            session = state["sessions"].get(token_digest)
            if not isinstance(session, dict):
                raise AdminSecurityError("admin_session_invalid")
            self._require_host_session_binding(
                expected_binding=str(
                    session.get("hostSessionBinding", "")
                ),
                current=current,
            )
            scope = str(session.get("scopeDigest", ""))
            if not scope:
                raise AdminSecurityError("admin_session_invalid")
            self._enforce_issue_rate(state, scope=scope, now=current)
            state["issueEvents"].append([current, scope])
            self._save_state(state)

    def logout(self, token: str) -> None:
        token_digest = _digest(
            self.authentication_key,
            _SESSION_DOMAIN,
            token if isinstance(token, str) else "",
        )
        with self._lock:
            state = self._load_state()
            state["sessions"].pop(token_digest, None)
            self._save_state(state)

    def revoke_all(self) -> None:
        with self._lock:
            state = self._load_state()
            state["challenges"] = {}
            state["sessions"] = {}
            self._save_state(state)


__all__ = [
    "ADMIN_ATTESTATION_PURPOSE",
    "ADMIN_ATTESTATION_SCHEMA",
    "ADMIN_CONTROL_CAPABILITY",
    "ADMIN_HOST_SESSION_PURPOSE",
    "ADMIN_HOST_SESSION_SCHEMA",
    "ADMIN_PUBLIC_STATUS_SCHEMA",
    "ADMIN_SESSION_COOKIE_ATTRIBUTES",
    "ADMIN_SESSION_COOKIE_NAME",
    "AdminSecurityError",
    "AdminSessionClaims",
    "AdminSessionGrant",
    "ConversationArchiveAdminAuth",
    "LoopbackRequestEvidence",
    "OTP_ALPHABET",
    "OTP_CODE_RE",
    "OtpDelivery",
    "require_loopback_control_page",
    "sign_host_attestation",
    "sign_host_session_marker",
    "verify_host_attestation",
    "verify_host_session_marker",
]
