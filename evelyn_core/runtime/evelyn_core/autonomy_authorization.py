from __future__ import annotations

import json
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .runtime_artifact_io import atomic_json_write


AUTONOMY_AUTHORIZATION_STATUS_SCHEMA = "autonomy_authorization.status.v1"
AUTONOMY_AUTHORIZATION_EVENT_SCHEMA = "autonomy_authorization.event.v1"
AUTONOMY_AUTHORIZATION_DECISION_SCHEMA = "autonomy_authorization.decision.v1"
DEFAULT_AUTHORIZATION_TTL_SEC = 60 * 60.0
MAX_AUTHORIZATION_TTL_SEC = 4 * 60 * 60.0
MIN_AUTHORIZATION_TTL_SEC = 60.0

ASSISTANT_AUTONOMY_ACTIONS: tuple[str, ...] = (
    "assistant:check_status",
    "assistant:refresh_cognitive_state",
    "assistant:summarize_notifications",
    "assistant:summarize_recent_context",
    "assistant:send_followup",
    "assistant:maybe_ping_user",
    "assistant:idle",
)

MINECRAFT_AUTONOMY_ACTIONS: tuple[str, ...] = (
    "minecraft:retreat",
    "minecraft:heal_or_regroup",
    "minecraft:find_food_source",
    "minecraft:consume_food",
    "minecraft:gather_logs",
    "minecraft:craft_basic_tools",
    "minecraft:gather_basic_resources",
    "minecraft:equip_shield",
    "minecraft:eat_if_low",
    "minecraft:avoid_hazard",
    "minecraft:exploration_guard",
    "minecraft:explore_interesting_block",
    "minecraft:enter_cave",
    "minecraft:exit_cave",
    "minecraft:navigate_stairs",
    "minecraft:place_block_nearby",
    "minecraft:generated_skill",
    "minecraft:melee_attack",
    "minecraft:craft_stone_tools",
    "minecraft:craft_furnace",
    "minecraft:smelt_iron_ingot",
    "minecraft:craft_torch",
    "minecraft:craft_chest",
    "minecraft:cook_food",
)

SUPPORTED_AUTONOMY_ACTIONS = frozenset(
    (*ASSISTANT_AUTONOMY_ACTIONS, *MINECRAFT_AUTONOMY_ACTIONS)
)
_ALLOWED_SOURCES = frozenset(
    {
        "discord_command",
        "control_page",
        "local_operator",
        "test",
    }
)
_ALLOWED_REASON_CODES = frozenset(
    {
        "explicit_autonomy_start",
        "explicit_autonomy_stop",
        "grant_replaced",
        "grant_expired",
        "process_restart",
        "start_failed",
        "manual_revoke",
        "action_authorized",
        "action_denied",
        "action_outcome",
    }
)
_ALLOWED_OUTCOME_STATUSES = frozenset(
    {
        "ok",
        "done",
        "completed",
        "blocked",
        "failed",
        "unknown",
        "unverified",
    }
)


def _finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_identifier(value: Any, *, limit: int = 80) -> str:
    text = str(value or "").strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-."
    if not text or any(character not in allowed for character in text):
        return ""
    return text[:limit]


def _safe_reason_code(value: Any, *, fallback: str) -> str:
    code = _safe_identifier(value)
    return code if code in _ALLOWED_REASON_CODES else fallback


def _safe_source(value: Any) -> str:
    source = _safe_identifier(value)
    return source if source in _ALLOWED_SOURCES else ""


def _safe_guild_id(value: Any) -> int | None:
    try:
        guild_id = int(value)
    except (TypeError, ValueError):
        return None
    return guild_id if guild_id >= 0 else None


def _safe_scopes(scopes: Iterable[Any]) -> tuple[str, ...]:
    selected = {
        str(scope).strip()
        for scope in scopes
        if str(scope).strip() in SUPPORTED_AUTONOMY_ACTIONS
    }
    return tuple(sorted(selected))


@dataclass(frozen=True)
class AutonomyAuthorizationGrant:
    grant_id: str
    guild_id: int
    issuer_ref: str
    source: str
    scopes: tuple[str, ...]
    issued_at: float
    expires_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "grantId": self.grant_id,
            "guildId": self.guild_id,
            "source": self.source,
            "scopes": list(self.scopes),
            "issuedAt": self.issued_at,
            "expiresAt": self.expires_at,
        }


class AutonomyAuthorizationManager:
    """Owns short-lived, non-restored authorization for autonomous actions."""

    def __init__(
        self,
        *,
        status_path: Path,
        events_dir: Path,
        now: Callable[[], float] = time.time,
        default_ttl_sec: float = DEFAULT_AUTHORIZATION_TTL_SEC,
        max_ttl_sec: float = MAX_AUTHORIZATION_TTL_SEC,
    ) -> None:
        self.status_path = Path(status_path)
        self.events_dir = Path(events_dir)
        self.now = now
        self.default_ttl_sec = max(
            MIN_AUTHORIZATION_TTL_SEC,
            float(default_ttl_sec),
        )
        self.max_ttl_sec = max(
            self.default_ttl_sec,
            float(max_ttl_sec),
        )
        self.process_nonce = secrets.token_hex(8)
        self._grants: dict[int, AutonomyAuthorizationGrant] = {}
        self._lock = threading.RLock()
        self._state = "not_initialized"
        self._last_event_at: float | None = None
        self._decision_count = 0
        self._denied_count = 0
        self._audit_ready = False

    def _append_event(
        self,
        event: str,
        *,
        guild_id: int | None = None,
        grant: AutonomyAuthorizationGrant | None = None,
        grant_id: str = "",
        action: str = "",
        reason_code: str = "",
        outcome_status: str = "",
        verified: bool | None = None,
        evidence_code: str = "",
        authorization_current: bool | None = None,
    ) -> bool:
        timestamp = self.now()
        record: dict[str, Any] = {
            "schema": AUTONOMY_AUTHORIZATION_EVENT_SCHEMA,
            "eventId": secrets.token_hex(12),
            "at": timestamp,
            "event": _safe_identifier(event),
            "processNonce": self.process_nonce,
            "guildId": guild_id,
            "grantId": (
                grant.grant_id
                if grant is not None
                else _safe_identifier(grant_id)
            ),
            "issuerRef": grant.issuer_ref if grant is not None else "",
            "source": grant.source if grant is not None else "",
            "scopes": list(grant.scopes) if grant is not None else [],
            "expiresAt": grant.expires_at if grant is not None else None,
            "action": (
                action
                if action in SUPPORTED_AUTONOMY_ACTIONS
                else ""
            ),
            "reasonCode": _safe_reason_code(
                reason_code,
                fallback="action_denied",
            ),
            "outcomeStatus": (
                outcome_status
                if outcome_status in _ALLOWED_OUTCOME_STATUSES
                else ""
            ),
            "verified": verified,
            "evidenceCode": _safe_identifier(evidence_code),
            "authorizationCurrent": authorization_current,
        }
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)
            event_path = self.events_dir / f"{time.strftime('%Y%m%d')}.jsonl"
            with event_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            self._last_event_at = timestamp
            self._audit_ready = True
            return True
        except OSError:
            self._audit_ready = False
            return False

    def _fail_closed_for_audit(self) -> None:
        self._grants.clear()
        self._state = "authorization_audit_unavailable"
        self._audit_ready = False
        self._write_status()

    def _status_payload(self) -> dict[str, Any]:
        from .autonomy_outcome_evidence import (
            AUTONOMY_OUTCOME_EVIDENCE_POLICY_SCHEMA,
        )

        timestamp = self.now()
        active = [
            grant.public_dict()
            for grant in sorted(
                self._grants.values(),
                key=lambda item: item.guild_id,
            )
            if grant.expires_at > timestamp
        ]
        return {
            "schema": AUTONOMY_AUTHORIZATION_STATUS_SCHEMA,
            "state": self._state,
            "updatedAt": timestamp,
            "processNonce": self.process_nonce,
            "activeGrantCount": len(active),
            "activeGrants": active,
            "decisionCount": self._decision_count,
            "deniedCount": self._denied_count,
            "lastEventAt": self._last_event_at,
            "auditReady": self._audit_ready,
            "policy": {
                "restoredAfterRestart": False,
                "defaultTtlSec": self.default_ttl_sec,
                "maxTtlSec": self.max_ttl_sec,
                "supportedScopeCount": len(SUPPORTED_AUTONOMY_ACTIONS),
                "issuerRefPublic": False,
                "rawArguments": False,
                "transcript": False,
                "outcomeEvidencePolicy": (
                    AUTONOMY_OUTCOME_EVIDENCE_POLICY_SCHEMA
                ),
                "strictActionEvidenceMatch": True,
                "retryExhaustionIsEvidence": False,
            },
        }

    def _write_status(self) -> None:
        try:
            atomic_json_write(self.status_path, self._status_payload())
        except OSError:
            return

    def initialize(self) -> dict[str, Any]:
        with self._lock:
            self._grants.clear()
            self._state = "authorization_required"
            if not self._append_event(
                "process_started",
                reason_code="process_restart",
            ):
                self._fail_closed_for_audit()
            self._write_status()
            return self._status_payload()

    def _prune_expired(self) -> bool:
        timestamp = self.now()
        expired = [
            guild_id
            for guild_id, grant in self._grants.items()
            if grant.expires_at <= timestamp
        ]
        for guild_id in expired:
            grant = self._grants.pop(guild_id)
            if not self._append_event(
                "grant_expired",
                guild_id=guild_id,
                grant=grant,
                reason_code="grant_expired",
            ):
                self._fail_closed_for_audit()
                return False
        if expired:
            self._state = (
                "ready"
                if self._grants
                else "authorization_required"
            )
            self._write_status()
        return True

    def grant(
        self,
        *,
        guild_id: int,
        issuer_ref: str,
        source: str,
        scopes: Iterable[str] = ASSISTANT_AUTONOMY_ACTIONS,
        ttl_sec: float | None = None,
    ) -> dict[str, Any]:
        resolved_guild_id = _safe_guild_id(guild_id)
        resolved_issuer = _safe_identifier(issuer_ref)
        resolved_source = _safe_source(source)
        resolved_scopes = _safe_scopes(scopes)
        if resolved_guild_id is None:
            return {
                "ok": False,
                "error": "authorization_guild_invalid",
            }
        if not resolved_issuer:
            return {
                "ok": False,
                "error": "authorization_issuer_invalid",
            }
        if not resolved_source:
            return {
                "ok": False,
                "error": "authorization_source_invalid",
            }
        if not resolved_scopes:
            return {
                "ok": False,
                "error": "authorization_scope_empty",
            }
        requested_ttl = (
            self.default_ttl_sec
            if ttl_sec is None
            else _finite_float(ttl_sec, self.default_ttl_sec)
        )
        effective_ttl = max(
            MIN_AUTHORIZATION_TTL_SEC,
            min(self.max_ttl_sec, requested_ttl),
        )
        with self._lock:
            if not self._prune_expired():
                return {
                    "ok": False,
                    "error": "authorization_audit_unavailable",
                }
            previous = self._grants.get(resolved_guild_id)
            if previous is not None:
                if not self._append_event(
                    "grant_revoked",
                    guild_id=resolved_guild_id,
                    grant=previous,
                    reason_code="grant_replaced",
                ):
                    self._fail_closed_for_audit()
                    return {
                        "ok": False,
                        "error": "authorization_audit_unavailable",
                    }
            issued_at = self.now()
            grant = AutonomyAuthorizationGrant(
                grant_id=secrets.token_urlsafe(18),
                guild_id=resolved_guild_id,
                issuer_ref=resolved_issuer,
                source=resolved_source,
                scopes=resolved_scopes,
                issued_at=issued_at,
                expires_at=issued_at + effective_ttl,
            )
            if not self._append_event(
                "grant_issued",
                guild_id=resolved_guild_id,
                grant=grant,
                reason_code="explicit_autonomy_start",
            ):
                self._fail_closed_for_audit()
                return {
                    "ok": False,
                    "error": "authorization_audit_unavailable",
                }
            self._grants[resolved_guild_id] = grant
            self._state = "ready"
            self._write_status()
            return {
                "ok": True,
                "grant": grant.public_dict(),
            }

    def revoke(
        self,
        guild_id: int,
        *,
        reason_code: str = "manual_revoke",
    ) -> dict[str, Any]:
        resolved_guild_id = _safe_guild_id(guild_id)
        if resolved_guild_id is None:
            return {
                "ok": False,
                "error": "authorization_guild_invalid",
            }
        with self._lock:
            grant = self._grants.pop(resolved_guild_id, None)
            if grant is not None:
                if not self._append_event(
                    "grant_revoked",
                    guild_id=resolved_guild_id,
                    grant=grant,
                    reason_code=_safe_reason_code(
                        reason_code,
                        fallback="manual_revoke",
                    ),
                ):
                    self._fail_closed_for_audit()
                    return {
                        "ok": False,
                        "revoked": True,
                        "error": "authorization_audit_unavailable",
                    }
            self._state = (
                "ready"
                if self._grants
                else "authorization_required"
            )
            self._write_status()
            return {
                "ok": True,
                "revoked": grant is not None,
            }

    def authorized_actions(self, guild_id: int) -> list[str]:
        resolved_guild_id = _safe_guild_id(guild_id)
        if resolved_guild_id is None:
            return []
        with self._lock:
            if not self._prune_expired():
                return []
            grant = self._grants.get(resolved_guild_id)
            return list(grant.scopes) if grant is not None else []

    def authorize(self, guild_id: int, action: str) -> dict[str, Any]:
        resolved_guild_id = _safe_guild_id(guild_id)
        resolved_action = str(action or "").strip()
        with self._lock:
            if not self._prune_expired():
                self._decision_count += 1
                self._denied_count += 1
                self._write_status()
                return {
                    "schema": AUTONOMY_AUTHORIZATION_DECISION_SCHEMA,
                    "allowed": False,
                    "code": "authorization_audit_unavailable",
                    "guildId": resolved_guild_id,
                    "action": (
                        resolved_action
                        if resolved_action in SUPPORTED_AUTONOMY_ACTIONS
                        else ""
                    ),
                    "grantId": "",
                    "expiresAt": None,
                }
            grant = (
                self._grants.get(resolved_guild_id)
                if resolved_guild_id is not None
                else None
            )
            if resolved_action not in SUPPORTED_AUTONOMY_ACTIONS:
                code = "authorization_action_unsupported"
                allowed = False
            elif grant is None:
                code = "authorization_required"
                allowed = False
            elif resolved_action not in grant.scopes:
                code = "authorization_scope_denied"
                allowed = False
            else:
                code = "authorized"
                allowed = True
            self._decision_count += 1
            if not allowed:
                self._denied_count += 1
            if not self._append_event(
                "action_authorized" if allowed else "action_denied",
                guild_id=resolved_guild_id,
                grant=grant,
                action=resolved_action,
                reason_code=(
                    "action_authorized"
                    if allowed
                    else "action_denied"
                ),
            ):
                if allowed:
                    self._denied_count += 1
                self._fail_closed_for_audit()
                return {
                    "schema": AUTONOMY_AUTHORIZATION_DECISION_SCHEMA,
                    "allowed": False,
                    "code": "authorization_audit_unavailable",
                    "guildId": resolved_guild_id,
                    "action": (
                        resolved_action
                        if resolved_action in SUPPORTED_AUTONOMY_ACTIONS
                        else ""
                    ),
                    "grantId": "",
                    "expiresAt": None,
                }
            self._write_status()
            return {
                "schema": AUTONOMY_AUTHORIZATION_DECISION_SCHEMA,
                "allowed": allowed,
                "code": code,
                "guildId": resolved_guild_id,
                "action": (
                    resolved_action
                    if resolved_action in SUPPORTED_AUTONOMY_ACTIONS
                    else ""
                ),
                "grantId": grant.grant_id if grant is not None else "",
                "expiresAt": grant.expires_at if grant is not None else None,
            }

    def record_outcome(
        self,
        guild_id: int,
        action: str,
        result: dict[str, Any],
    ) -> None:
        from .autonomy_outcome_evidence import (
            AUTONOMY_SUCCESS_STATUSES,
            autonomy_outcome_verified,
        )

        resolved_guild_id = _safe_guild_id(guild_id)
        resolved_action = str(action or "").strip()
        with self._lock:
            if not self._prune_expired():
                return
            grant = (
                self._grants.get(resolved_guild_id)
                if resolved_guild_id is not None
                else None
            )
            authorization_grant_id = _safe_identifier(
                result.get("_authorization_grant_id")
            )
            authorization_current = bool(
                grant is not None
                and (
                    not authorization_grant_id
                    or grant.grant_id == authorization_grant_id
                )
                and resolved_action in grant.scopes
            )
            event_grant = (
                grant
                if (
                    grant is not None
                    and (
                        not authorization_grant_id
                        or grant.grant_id == authorization_grant_id
                    )
                )
                else None
            )
            status = str(result.get("status") or "").strip().lower()
            verified = autonomy_outcome_verified(
                resolved_action,
                result,
            ) and authorization_current
            if (
                status in AUTONOMY_SUCCESS_STATUSES
                and not verified
            ):
                status = "unverified"
            if not self._append_event(
                "action_outcome",
                guild_id=resolved_guild_id,
                grant=event_grant,
                grant_id=authorization_grant_id,
                action=resolved_action,
                reason_code="action_outcome",
                outcome_status=(
                    status
                    if status in _ALLOWED_OUTCOME_STATUSES
                    else "unknown"
                ),
                verified=verified,
                evidence_code=str(result.get("evidence_code") or ""),
                authorization_current=authorization_current,
            ):
                self._fail_closed_for_audit()
                return
            self._write_status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._prune_expired()
            return self._status_payload()


__all__ = [
    "ASSISTANT_AUTONOMY_ACTIONS",
    "AUTONOMY_AUTHORIZATION_DECISION_SCHEMA",
    "AUTONOMY_AUTHORIZATION_EVENT_SCHEMA",
    "AUTONOMY_AUTHORIZATION_STATUS_SCHEMA",
    "AutonomyAuthorizationGrant",
    "AutonomyAuthorizationManager",
    "DEFAULT_AUTHORIZATION_TTL_SEC",
    "MAX_AUTHORIZATION_TTL_SEC",
    "MINECRAFT_AUTONOMY_ACTIONS",
    "SUPPORTED_AUTONOMY_ACTIONS",
]
