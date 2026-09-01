from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .discord_ingress import (
    make_session_memory_key,
    make_text_session_key,
    make_voice_session_key,
)
from .memory_confirmation_contract import memory_owner_scope


EPHEMERAL_DELETE_AFTER_SECONDS = 180.0
INTERACTION_ACK_DEADLINE_SECONDS = 3.0


class IntervalKind(str, Enum):
    PRESENCE = "presence"
    ELIGIBLE = "eligible"


class DiscordArchiveRecordKind(str, Enum):
    USER_TEXT = "user_text"
    FINAL_STT = "final_stt"
    MINECRAFT_COMMAND = "minecraft_command"
    EVELYN_REPLY = "evelyn_reply"
    TASK_RESULT = "task_result"
    MINECRAFT_RESULT = "minecraft_result"


class DiscordInteractionContext(str, Enum):
    GUILD = "GUILD"
    BOT_DM = "BOT_DM"
    PRIVATE_CHANNEL = "PRIVATE_CHANNEL"


class EphemeralDeleteOutcome(str, Enum):
    REMOVED = "removed"
    TOKEN_EXPIRED = "token_expired"
    NOT_FOUND = "not_found"
    NOT_CONTROLLABLE = "not_controllable"


class RecordCommandRejected(ValueError):
    """Raised when a Discord record command cannot safely issue a self-view handle."""


class ConversationArchiveTransportError(RuntimeError):
    """The private archive did not return a verified successful response."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = status


@dataclass(frozen=True, slots=True)
class DiscordVoiceStateSnapshot:
    channel_id: int | None
    consent_current: bool
    gateway_current: bool = True
    self_mute: bool = False
    server_mute: bool = False
    stage_suppress: bool = False
    self_deaf: bool = False
    server_deaf: bool = False

    @property
    def present(self) -> bool:
        return self.gateway_current and self.channel_id is not None

    @property
    def eligible(self) -> bool:
        return self.present and self.consent_current and not any(
            (
                self.self_mute,
                self.server_mute,
                self.stage_suppress,
                self.self_deaf,
                self.server_deaf,
            )
        )

    @property
    def ineligible_reason(self) -> str | None:
        if not self.gateway_current:
            return "gateway_unknown"
        if self.channel_id is None:
            return "not_present"
        if not self.consent_current:
            return "consent_not_current"
        for name in (
            "self_mute",
            "server_mute",
            "stage_suppress",
            "self_deaf",
            "server_deaf",
        ):
            if getattr(self, name):
                return name
        return None


def voice_state_snapshot_from_discord(
    state: Any,
    *,
    consent_current: bool,
    gateway_current: bool = True,
) -> DiscordVoiceStateSnapshot:
    """Project the stable subset of discord.py VoiceState without importing it."""

    channel = getattr(state, "channel", None)
    raw_channel_id = getattr(channel, "id", None)
    channel_id = int(raw_channel_id) if raw_channel_id is not None else None
    return DiscordVoiceStateSnapshot(
        channel_id=channel_id,
        consent_current=bool(consent_current),
        gateway_current=bool(gateway_current),
        self_mute=bool(getattr(state, "self_mute", False)),
        server_mute=bool(getattr(state, "mute", False)),
        stage_suppress=bool(getattr(state, "suppress", False)),
        self_deaf=bool(getattr(state, "self_deaf", False)),
        server_deaf=bool(getattr(state, "deaf", False)),
    )


@dataclass(frozen=True, slots=True)
class ParticipationInterval:
    kind: IntervalKind
    guild_id: int
    channel_id: int
    user_id: int
    started_at: float
    ended_at: float | None = None

    def __post_init__(self) -> None:
        started_at = _require_timestamp(self.started_at, name="started_at")
        object.__setattr__(self, "started_at", started_at)
        if self.ended_at is not None:
            ended_at = _require_timestamp(self.ended_at, name="ended_at")
            object.__setattr__(self, "ended_at", ended_at)
            if ended_at < started_at:
                raise ValueError("interval_end_before_start")

    def contains(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        started_at: float,
        ended_at: float,
    ) -> bool:
        if self.kind is not IntervalKind.ELIGIBLE:
            return False
        if (self.guild_id, self.channel_id, self.user_id) != (
            guild_id,
            channel_id,
            user_id,
        ):
            return False
        return started_at >= self.started_at and (
            self.ended_at is None or ended_at <= self.ended_at
        )


@dataclass(frozen=True, slots=True)
class ParticipationUpdate:
    guild_id: int
    user_id: int
    observed_at: float
    snapshot: DiscordVoiceStateSnapshot
    owner_name: str = ""
    opened: tuple[ParticipationInterval, ...] = ()
    closed: tuple[ParticipationInterval, ...] = ()


@dataclass(slots=True)
class _TrackedParticipation:
    snapshot: DiscordVoiceStateSnapshot
    observed_at: float
    owner_name: str
    presence_started_at: float | None
    eligible_started_at: float | None


class DiscordParticipationTracker:
    """Turn ordered voice-state snapshots into half-open authorization intervals."""

    def __init__(self) -> None:
        self._states: dict[tuple[int, int], _TrackedParticipation] = {}

    def observe(
        self,
        *,
        guild_id: int,
        user_id: int,
        observed_at: float,
        snapshot: DiscordVoiceStateSnapshot,
        owner_name: str | None = None,
    ) -> ParticipationUpdate:
        observed_at = _require_timestamp(observed_at, name="observed_at")
        key = (int(guild_id), int(user_id))
        previous = self._states.get(key)
        if previous is not None and observed_at < previous.observed_at:
            raise ValueError("participation_event_out_of_order")

        opened: list[ParticipationInterval] = []
        closed: list[ParticipationInterval] = []
        presence_started_at: float | None = None
        eligible_started_at: float | None = None

        if previous is not None:
            old_channel_id = previous.snapshot.channel_id
            same_present_channel = (
                previous.snapshot.present
                and snapshot.present
                and old_channel_id == snapshot.channel_id
            )
            same_eligible_channel = (
                previous.snapshot.eligible
                and snapshot.eligible
                and old_channel_id == snapshot.channel_id
            )

            if same_eligible_channel:
                eligible_started_at = previous.eligible_started_at
            elif previous.snapshot.eligible:
                closed.append(
                    self._interval(
                        IntervalKind.ELIGIBLE,
                        key,
                        old_channel_id,
                        previous.eligible_started_at,
                        observed_at,
                    )
                )

            if same_present_channel:
                presence_started_at = previous.presence_started_at
            elif previous.snapshot.present:
                closed.append(
                    self._interval(
                        IntervalKind.PRESENCE,
                        key,
                        old_channel_id,
                        previous.presence_started_at,
                        observed_at,
                    )
                )

        if snapshot.present and presence_started_at is None:
            presence_started_at = observed_at
            opened.append(
                self._interval(
                    IntervalKind.PRESENCE,
                    key,
                    snapshot.channel_id,
                    observed_at,
                    None,
                )
            )
        if snapshot.eligible and eligible_started_at is None:
            eligible_started_at = observed_at
            opened.append(
                self._interval(
                    IntervalKind.ELIGIBLE,
                    key,
                    snapshot.channel_id,
                    observed_at,
                    None,
                )
            )

        resolved_owner_name = str(
            owner_name
            if owner_name is not None
            else (previous.owner_name if previous is not None else user_id)
        )
        self._states[key] = _TrackedParticipation(
            snapshot=snapshot,
            observed_at=observed_at,
            owner_name=resolved_owner_name,
            presence_started_at=presence_started_at,
            eligible_started_at=eligible_started_at,
        )
        return ParticipationUpdate(
            guild_id=key[0],
            user_id=key[1],
            observed_at=observed_at,
            snapshot=snapshot,
            owner_name=resolved_owner_name,
            opened=tuple(opened),
            closed=tuple(closed),
        )

    def mark_gateway_unknown(
        self,
        *,
        observed_at: float,
        guild_id: int | None = None,
    ) -> tuple[ParticipationUpdate, ...]:
        """Close eligible/presence intervals; reconnect must provide a fresh snapshot."""

        observed_at = _require_timestamp(observed_at, name="observed_at")
        keys = tuple(
            key
            for key in self._states
            if guild_id is None or key[0] == int(guild_id)
        )
        if any(observed_at < self._states[key].observed_at for key in keys):
            raise ValueError("participation_event_out_of_order")

        updates: list[ParticipationUpdate] = []
        for key in keys:
            tracked = self._states[key]
            updates.append(
                self.observe(
                    guild_id=key[0],
                    user_id=key[1],
                    observed_at=observed_at,
                    snapshot=replace(tracked.snapshot, gateway_current=False),
                    owner_name=tracked.owner_name,
                )
            )
        return tuple(updates)

    @staticmethod
    def _interval(
        kind: IntervalKind,
        key: tuple[int, int],
        channel_id: int | None,
        started_at: float | None,
        ended_at: float | None,
    ) -> ParticipationInterval:
        if channel_id is None or started_at is None:
            raise RuntimeError("participation_interval_state_inconsistent")
        return ParticipationInterval(
            kind=kind,
            guild_id=key[0],
            channel_id=channel_id,
            user_id=key[1],
            started_at=started_at,
            ended_at=ended_at,
        )


@dataclass(frozen=True, slots=True)
class DiscordArchiveCandidate:
    record_id: str
    guild_id: int
    channel_id: int
    kind: DiscordArchiveRecordKind
    started_at: float
    ended_at: float
    source_user_id: int | None = None
    parent_record_ids: tuple[str, ...] = ()
    body: str = ""

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("record_id_required")
        started_at = _require_timestamp(self.started_at, name="started_at")
        ended_at = _require_timestamp(self.ended_at, name="ended_at")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        if ended_at < started_at:
            raise ValueError("record_end_before_start")
        if len(set(self.parent_record_ids)) != len(self.parent_record_ids):
            raise ValueError("duplicate_parent_record_id")


def build_text_archive_candidate(
    *,
    record_id: str,
    guild_id: int,
    channel_id: int,
    user_id: int,
    authored_at: float,
    text: str,
) -> DiscordArchiveCandidate | None:
    """Build the exact authored chat root, even while voice-ineligible."""

    if not text:
        return None
    return DiscordArchiveCandidate(
        record_id=record_id,
        guild_id=int(guild_id),
        channel_id=int(channel_id),
        kind=DiscordArchiveRecordKind.USER_TEXT,
        started_at=authored_at,
        ended_at=authored_at,
        source_user_id=int(user_id),
        body=text,
    )


def build_voice_transcript_archive_candidate(
    *,
    stage: str,
    record_id: str,
    guild_id: int,
    channel_id: int,
    user_id: int,
    started_at: float,
    ended_at: float,
    text: str,
) -> DiscordArchiveCandidate | None:
    """Drop partial STT and construct only immutable final transcripts."""

    normalized_stage = str(stage).strip().lower()
    if normalized_stage == "partial":
        return None
    if normalized_stage != "final":
        raise ValueError("unsupported_transcript_stage")
    if not text:
        return None
    if ended_at <= started_at:
        raise ValueError("final_stt_interval_must_be_positive")
    return DiscordArchiveCandidate(
        record_id=record_id,
        guild_id=int(guild_id),
        channel_id=int(channel_id),
        kind=DiscordArchiveRecordKind.FINAL_STT,
        started_at=started_at,
        ended_at=ended_at,
        source_user_id=int(user_id),
        body=text,
    )


_DERIVED_SELF_VISIBLE_KINDS = frozenset(
    {
        DiscordArchiveRecordKind.EVELYN_REPLY,
        DiscordArchiveRecordKind.TASK_RESULT,
        DiscordArchiveRecordKind.MINECRAFT_RESULT,
    }
)


def select_self_scoped_records(
    records: Iterable[DiscordArchiveCandidate],
    *,
    caller_user_id: int,
    current_guild_id: int,
    eligible_intervals: Iterable[ParticipationInterval],
) -> tuple[DiscordArchiveCandidate, ...]:
    """Select exact caller roots and lineage-safe descendants, never time peers."""

    rows = tuple(records)
    record_ids = [row.record_id for row in rows]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("duplicate_record_id")

    caller_user_id = int(caller_user_id)
    current_guild_id = int(current_guild_id)
    intervals = tuple(eligible_intervals)
    selected_ids: set[str] = set()

    for row in rows:
        if row.guild_id != current_guild_id or row.source_user_id != caller_user_id:
            continue
        if row.kind in {
            DiscordArchiveRecordKind.USER_TEXT,
            DiscordArchiveRecordKind.MINECRAFT_COMMAND,
        }:
            selected_ids.add(row.record_id)
        elif row.kind is DiscordArchiveRecordKind.FINAL_STT and any(
            interval.contains(
                guild_id=row.guild_id,
                channel_id=row.channel_id,
                user_id=caller_user_id,
                started_at=row.started_at,
                ended_at=row.ended_at,
            )
            for interval in intervals
        ):
            selected_ids.add(row.record_id)

    changed = True
    while changed:
        changed = False
        for row in rows:
            if row.record_id in selected_ids or row.guild_id != current_guild_id:
                continue
            if row.kind not in _DERIVED_SELF_VISIBLE_KINDS or row.source_user_id is not None:
                continue
            if row.parent_record_ids and all(
                parent_id in selected_ids for parent_id in row.parent_record_ids
            ):
                selected_ids.add(row.record_id)
                changed = True

    return tuple(row for row in rows if row.record_id in selected_ids)


@dataclass(frozen=True, slots=True)
class RecordCommandPolicy:
    guild_id: int
    invoker_user_id: int
    audience_user_id: int
    context: DiscordInteractionContext = DiscordInteractionContext.GUILD
    capability: str = "memory.user_view"
    ephemeral: bool = True
    delete_after_seconds: float = EPHEMERAL_DELETE_AFTER_SECONDS
    ack_deadline_seconds: float = INTERACTION_ACK_DEADLINE_SECONDS
    opens_admin_session: bool = False
    dm_fallback: bool = False


def build_record_command_policy(
    *,
    context: DiscordInteractionContext | str,
    guild_id: int | None,
    invoker_user_id: int,
    requested_user_id: int | None = None,
) -> RecordCommandPolicy:
    """Bind one guild interaction to its exact caller without admin escalation."""

    try:
        context_value = (
            context
            if isinstance(context, DiscordInteractionContext)
            else DiscordInteractionContext(str(context).strip().upper())
        )
    except ValueError as exc:
        raise RecordCommandRejected("unsupported_interaction_context") from exc
    if context_value is not DiscordInteractionContext.GUILD or guild_id is None:
        raise RecordCommandRejected("guild_interaction_required")
    invoker_user_id = int(invoker_user_id)
    requested_user_id = (
        invoker_user_id if requested_user_id is None else int(requested_user_id)
    )
    if requested_user_id != invoker_user_id:
        raise RecordCommandRejected("cross_principal_scope_forbidden")
    return RecordCommandPolicy(
        guild_id=int(guild_id),
        invoker_user_id=invoker_user_id,
        audience_user_id=invoker_user_id,
    )


async def attempt_ephemeral_response_delete(
    delete_original_response: Callable[[], Awaitable[Any]],
    *,
    sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    classify_error: Callable[[Exception], EphemeralDeleteOutcome | str] | None = None,
) -> EphemeralDeleteOutcome:
    """Best-effort 180-second cleanup; the interaction token is not a session."""

    await sleep_fn(EPHEMERAL_DELETE_AFTER_SECONDS)
    try:
        result = await delete_original_response()
    except Exception as exc:  # Discord adapter supplies exact HTTP classification.
        if classify_error is None:
            return EphemeralDeleteOutcome.NOT_CONTROLLABLE
        try:
            return EphemeralDeleteOutcome(classify_error(exc))
        except Exception:
            return EphemeralDeleteOutcome.NOT_CONTROLLABLE
    if result is None:
        return EphemeralDeleteOutcome.REMOVED
    try:
        return EphemeralDeleteOutcome(result)
    except (TypeError, ValueError):
        return EphemeralDeleteOutcome.NOT_CONTROLLABLE


def classify_discord_ephemeral_delete_error(
    error: Exception,
) -> EphemeralDeleteOutcome:
    """Map Discord's content-free HTTP error code to the cleanup contract."""

    try:
        code = int(getattr(error, "code", 0) or 0)
        status = int(getattr(error, "status", 0) or 0)
    except (TypeError, ValueError):
        return EphemeralDeleteOutcome.NOT_CONTROLLABLE
    if code in {10015, 10062}:
        return EphemeralDeleteOutcome.TOKEN_EXPIRED
    if code in {10003, 10008}:
        return EphemeralDeleteOutcome.NOT_FOUND
    if status == 404:
        return EphemeralDeleteOutcome.NOT_FOUND
    return EphemeralDeleteOutcome.NOT_CONTROLLABLE


_TRANSPORT_KEY_DOMAIN = b"evelyn.private-conversation-archive.transport-key.v1\n"
_PURGE_LINEAGE_KEY_DOMAIN = (
    b"evelyn.private-conversation-archive.purge-lineage-key.v1\n"
)
_LINEAGE_DOMAIN = b"evelyn.private-conversation-archive.lineage.v1\n"
_LINEAGE_KINDS = frozenset(
    {"turn", "session", "memory_owner", "memory_note", "memory_evidence"}
)
_REMOTE_PURGE_SINKS = frozenset(
    {
        "continuity_checkpoint",
        "ingress_journal",
        "persona_state",
        "autonomy_state",
        "feedback_state",
        "outbound_retry",
        "prompt_tool_cache",
        "stt_buffer",
        "tts_buffer",
        "registered_exports",
    }
)
_TRANSPORT_PURPOSES = frozenset(
    {
        "ingest",
        "user-view-issue",
        "user-view",
        "otp-delivery",
        "purge-owner",
    }
)
_MAX_RESPONSE_BYTES = 1_048_576
DISCORD_FEEDBACK_CATEGORIES = frozenset(
    {
        "answer_quality",
        "context_selection",
        "task_routing",
        "tone_identity",
        "tool_failure",
        "permission_safety",
    }
)
DISCORD_FEEDBACK_SURFACES = frozenset({"discord", "voice"})
DISCORD_FEEDBACK_ENGINEERING_SCOPES = frozenset(
    {"none", "evaluator", "tool", "approval", "source"}
)


@dataclass(frozen=True, slots=True)
class DiscordArchiveRecordView:
    record_id: str
    started_at: datetime | None
    record_type: str
    body: str


@dataclass(frozen=True, slots=True)
class DiscordArchiveRecordPage:
    records: tuple[DiscordArchiveRecordView, ...]
    next_page_handle: str | None
    snapshot_generation: int


@dataclass(frozen=True, slots=True)
class DiscordDeletionPreview:
    preview_id: str
    counts_by_guild: dict[str, int]
    dependent_record_count: int
    interval_count: int
    all_guilds: bool


@dataclass(frozen=True, slots=True)
class DiscordDeletionResult:
    status: str
    affected_records: int
    dependent_records: int
    affected_intervals: int


@dataclass(frozen=True, slots=True)
class DiscordFeedbackCaptureResult:
    workflow_id: str
    category: str
    route: str
    state: str
    actionable: bool


@dataclass(frozen=True, slots=True)
class DiscordSharedSession:
    """One process-local, non-restorable Discord shared-mode binding."""

    operator_user_id: int
    guild_id: int
    text_channel_id: int
    voice_channel_id: int
    boot_generation: str
    lease_id: str
    opened_at_monotonic: float
    expires_at_monotonic: float


@dataclass(frozen=True, slots=True)
class _DiscordFeedbackTarget:
    task_id: str
    source_record_id: str
    guild_id: int
    channel_id: int
    user_id: int
    surface: str
    session_id: str
    shared_session: DiscordSharedSession


class DiscordSharedSessionRegistry:
    """Keep exact shared-mode currentness in memory for one Discord process."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        lease_factory: Callable[[int], str] = secrets.token_hex,
    ) -> None:
        ttl = _require_timestamp(ttl_seconds, name="shared_session_ttl_seconds")
        if ttl <= 0:
            raise ValueError("shared_session_ttl_seconds_invalid")
        self._ttl_seconds = ttl
        self._monotonic = monotonic
        self._lease_factory = lease_factory
        self._boot_generation: str | None = None
        self._sessions: dict[int, DiscordSharedSession] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    @property
    def boot_generation(self) -> str | None:
        return self._boot_generation

    def begin_generation(self, generation: str) -> None:
        normalized = str(generation or "")
        if not _archive_identifier_valid(normalized, maximum=128):
            raise ValueError("shared_session_generation_invalid")
        self._sessions.clear()
        self._boot_generation = normalized

    def open(
        self,
        *,
        operator_user_id: int,
        guild_id: int,
        text_channel_id: int,
        voice_channel_id: int,
    ) -> DiscordSharedSession:
        generation = self._boot_generation
        if generation is None:
            raise RuntimeError("shared_session_generation_missing")
        identifiers = (
            int(operator_user_id),
            int(guild_id),
            int(text_channel_id),
            int(voice_channel_id),
        )
        if any(value <= 0 for value in identifiers):
            raise ValueError("shared_session_binding_invalid")
        opened_at = _require_timestamp(
            self._monotonic(),
            name="shared_session_opened_at",
        )
        lease_id = str(self._lease_factory(16))
        if not _archive_identifier_valid(lease_id, maximum=128):
            raise ValueError("shared_session_lease_invalid")
        session = DiscordSharedSession(
            operator_user_id=identifiers[0],
            guild_id=identifiers[1],
            text_channel_id=identifiers[2],
            voice_channel_id=identifiers[3],
            boot_generation=generation,
            lease_id=lease_id,
            opened_at_monotonic=opened_at,
            expires_at_monotonic=opened_at + self._ttl_seconds,
        )
        self._sessions[session.guild_id] = session
        return session

    def current(
        self,
        *,
        guild_id: int,
        generation: str | None = None,
        operator_user_id: int | None = None,
        text_channel_id: int | None = None,
        voice_channel_id: int | None = None,
    ) -> DiscordSharedSession | None:
        guild = int(guild_id)
        session = self._sessions.get(guild)
        if session is None:
            return None
        now = _require_timestamp(self._monotonic(), name="shared_session_now")
        if now >= session.expires_at_monotonic:
            return None
        if generation is not None and session.boot_generation != str(generation):
            return None
        if operator_user_id is not None and session.operator_user_id != int(
            operator_user_id
        ):
            return None
        if text_channel_id is not None and session.text_channel_id != int(
            text_channel_id
        ):
            return None
        if voice_channel_id is not None and session.voice_channel_id != int(
            voice_channel_id
        ):
            return None
        return session

    def peek(self, *, guild_id: int) -> DiscordSharedSession | None:
        """Return an exact binding for its bounded shutdown path, even at expiry."""

        return self._sessions.get(int(guild_id))

    def close(
        self,
        *,
        guild_id: int,
        expected: DiscordSharedSession | None = None,
    ) -> DiscordSharedSession | None:
        guild = int(guild_id)
        session = self._sessions.get(guild)
        if session is None or (expected is not None and session is not expected):
            return None
        return self._sessions.pop(guild)

    def close_all(self) -> tuple[DiscordSharedSession, ...]:
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        return sessions

    def snapshot(self) -> tuple[DiscordSharedSession, ...]:
        return tuple(self._sessions.values())

    def seconds_until_expiry(self, session: DiscordSharedSession) -> float:
        if self.peek(guild_id=session.guild_id) is not session:
            return 0.0
        now = _require_timestamp(self._monotonic(), name="shared_session_now")
        return max(0.0, session.expires_at_monotonic - now)


class DiscordConversationArchiveClient:
    """Purpose-limited HTTP adapter; Discord never opens archive files."""

    def __init__(
        self,
        *,
        base_url: str,
        master_key: bytes,
        user_view_master_key: bytes,
        get_http_session: Callable[[], Awaitable[Any]],
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[int], str] = secrets.token_hex,
        generation_factory: Callable[[int], str] = secrets.token_hex,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        base = str(base_url or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("archive_base_url_invalid")
        key = bytes(master_key)
        user_view_key = bytes(user_view_master_key)
        if len(key) < 32:
            raise ValueError("archive_transport_key_too_short")
        if len(user_view_key) < 32 or hmac.compare_digest(key, user_view_key):
            raise ValueError("archive_user_view_key_invalid")
        if request_timeout_seconds <= 0:
            raise ValueError("archive_request_timeout_invalid")
        self._base_url = base
        self._master_key = key
        self._purge_lineage_key = hmac.new(
            key,
            _PURGE_LINEAGE_KEY_DOMAIN,
            hashlib.sha256,
        ).digest()
        self._user_view_master_key = user_view_key
        self._get_http_session = get_http_session
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._generation_factory = generation_factory
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._mutation_lock = asyncio.Lock()
        self._generation: str | None = None
        self._sequence = 0
        self._voice_parent_by_turn: dict[tuple[int, int, str], str] = {}
        self._consent: dict[tuple[int, int, int], bool] = {}
        self._last_channel: dict[tuple[int, int], int] = {}
        self._last_owner_name: dict[tuple[int, int], str] = {}

    @classmethod
    def from_key_file(
        cls,
        *,
        base_url: str,
        key_file: str | Path,
        user_view_key_file: str | Path,
        get_http_session: Callable[[], Awaitable[Any]],
        **kwargs: Any,
    ) -> "DiscordConversationArchiveClient":
        path = Path(key_file)
        user_view_path = Path(user_view_key_file)
        if (
            path.is_symlink()
            or not path.is_file()
            or user_view_path.is_symlink()
            or not user_view_path.is_file()
        ):
            raise ValueError("archive_transport_key_file_invalid")
        return cls(
            base_url=base_url,
            master_key=path.read_bytes(),
            user_view_master_key=user_view_path.read_bytes(),
            get_http_session=get_http_session,
            **kwargs,
        )

    @property
    def generation(self) -> str | None:
        return self._generation

    def purge_lineage_handle(self, kind: str, raw_value: str) -> str:
        """Match one raw owner-local lineage value without sharing archive keys."""

        if (
            not isinstance(kind, str)
            or kind not in _LINEAGE_KINDS
            or not isinstance(raw_value, str)
        ):
            raise ValueError("archive_lineage_value_invalid")
        value = raw_value.strip()
        if not value or len(value) > 256 or "\x00" in value:
            raise ValueError("archive_lineage_value_invalid")
        digest = hmac.new(self._purge_lineage_key, digestmod=hashlib.sha256)
        digest.update(_LINEAGE_DOMAIN)
        digest.update(kind.encode("ascii"))
        digest.update(b"\n")
        digest.update(
            json.dumps(
                {"value": value},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return digest.hexdigest()

    async def begin_generation(self, *, force: bool = False) -> str:
        async with self._mutation_lock:
            if self._generation is not None and not force:
                return self._generation
            return await self._activate_generation_locked(replace=True)

    async def open_shared_session_lease(
        self,
        *,
        operator_user_id: int,
        guild_id: int,
        text_channel_id: int,
        voice_channel_id: int,
        lease_id: str,
    ) -> None:
        operator = _positive_archive_integer(operator_user_id)
        guild = _positive_archive_integer(guild_id)
        text_channel = _positive_archive_integer(text_channel_id)
        voice_channel = _positive_archive_integer(voice_channel_id)
        lease = str(lease_id or "").strip()
        if not _archive_identifier_valid(lease, maximum=128):
            raise ConversationArchiveTransportError(
                "archive_shared_session_lease_invalid"
            )
        response = await self._mutate(
            "/internal/conversation-archive/shared-session/open",
            {
                "idempotencyKey": self._stable_handle(
                    "shared-session-open", f"{guild}:{lease}"
                ),
                "operatorUserId": str(operator),
                "guildId": str(guild),
                "textChannelId": str(text_channel),
                "voiceChannelId": str(voice_channel),
                "leaseId": lease,
            },
        )
        if response != {
            "ok": True,
            "state": "open",
            "guildId": str(guild),
            "leaseId": lease,
        }:
            raise ConversationArchiveTransportError(
                "archive_shared_session_lease_receipt_invalid"
            )

    async def close_shared_session_lease(
        self,
        *,
        guild_id: int,
        lease_id: str,
    ) -> None:
        guild = _positive_archive_integer(guild_id)
        lease = str(lease_id or "").strip()
        if not _archive_identifier_valid(lease, maximum=128):
            raise ConversationArchiveTransportError(
                "archive_shared_session_lease_invalid"
            )
        response = await self._mutate(
            "/internal/conversation-archive/shared-session/close",
            {
                "idempotencyKey": self._stable_handle(
                    "shared-session-close", f"{guild}:{lease}"
                ),
                "guildId": str(guild),
                "leaseId": lease,
            },
        )
        if response != {
            "ok": True,
            "state": "closed",
            "guildId": str(guild),
            "leaseId": lease,
        }:
            raise ConversationArchiveTransportError(
                "archive_shared_session_lease_receipt_invalid"
            )

    async def active_task_guidance(self) -> dict[str, Any]:
        response = await self._request(
            "POST",
            "/internal/conversation-archive/task-guidance",
            {},
            purpose="ingest",
        )
        fields = {
            "schema",
            "versionId",
            "guidance",
            "guidanceDigest",
            "sourceFree",
            "active",
            "canaryRunId",
        }
        binding = response.get("binding")
        if (
            set(response) != {"ok", "binding"}
            or response.get("ok") is not True
            or not isinstance(binding, dict)
            or set(binding) != fields
            or binding.get("schema")
            != "evelyn.task-planner-guidance-binding.v1"
            or not isinstance(binding.get("versionId"), str)
            or not binding["versionId"]
            or len(binding["versionId"]) > 128
            or not isinstance(binding.get("guidance"), str)
            or len(binding["guidance"]) > 8_000
            or "\x00" in binding["guidance"]
            or not isinstance(binding.get("guidanceDigest"), str)
            or len(binding["guidanceDigest"]) != 64
            or hashlib.sha256(binding["guidance"].encode("utf-8")).hexdigest()
            != binding["guidanceDigest"]
            or binding.get("sourceFree") is not True
            or binding.get("active") is not True
            or binding.get("canaryRunId") is not None
        ):
            raise ConversationArchiveTransportError(
                "archive_task_guidance_receipt_invalid"
            )
        return dict(binding)

    async def archive_user_text(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        owner_name: str,
        message_id: int,
        turn_id: str,
        authored_at: float,
        text: str,
    ) -> dict[str, Any]:
        source_key = f"discord-text:{int(guild_id)}:{int(message_id)}"
        record_id = self._stable_handle("record", source_key)
        session_key = make_text_session_key(guild_id, channel_id, user_id)
        return await self._append_record(
            idempotency_key=self._stable_handle("idempotency", source_key),
            record_id=record_id,
            guild_id=guild_id,
            channel_id=channel_id,
            kind=DiscordArchiveRecordKind.USER_TEXT.value,
            started_at=authored_at,
            ended_at=authored_at,
            source_user_id=user_id,
            owner_name=owner_name,
            parent_record_ids=(),
            lineage={
                "turn": (str(turn_id),),
                "session": (
                    session_key,
                    make_session_memory_key(session_key, user_id) or session_key,
                ),
                "memory_owner": (
                    memory_owner_scope(
                        guild_id=guild_id,
                        person_key=f"user:{int(user_id)}",
                    ),
                ),
                "memory_evidence": (f"turn:{turn_id}:user",),
            },
            body=text,
        )

    async def archive_final_transcript(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        owner_name: str,
        turn_id: str,
        segment_id: int,
        started_at: float,
        ended_at: float,
        text: str,
    ) -> dict[str, Any]:
        source_key = (
            f"discord-final-stt:{int(guild_id)}:{int(user_id)}:"
            f"{turn_id}:{int(segment_id)}"
        )
        record_id = self._stable_handle("record", source_key)
        session_key = make_voice_session_key(guild_id, channel_id, user_id)
        receipt = await self._append_record(
            idempotency_key=self._stable_handle("idempotency", source_key),
            record_id=record_id,
            guild_id=guild_id,
            channel_id=channel_id,
            kind=DiscordArchiveRecordKind.FINAL_STT.value,
            started_at=started_at,
            ended_at=ended_at,
            source_user_id=user_id,
            owner_name=owner_name,
            parent_record_ids=(),
            lineage={
                "turn": (str(turn_id),),
                "session": (
                    session_key,
                    make_session_memory_key(session_key, user_id) or session_key,
                ),
                "memory_owner": (
                    memory_owner_scope(
                        guild_id=guild_id,
                        person_key=f"user:{int(user_id)}",
                    ),
                ),
                "memory_evidence": (f"turn:{turn_id}:user",),
            },
            body=text,
        )
        key = (int(guild_id), int(user_id), str(turn_id))
        self._voice_parent_by_turn[key] = record_id
        while len(self._voice_parent_by_turn) > 4096:
            self._voice_parent_by_turn.pop(next(iter(self._voice_parent_by_turn)))
        return receipt

    async def archive_autonomy_grant(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        owner_name: str,
        message_id: int,
        grant_id: str,
        authored_at: float,
        text: str,
    ) -> dict[str, Any]:
        record_id = str(grant_id or "").strip()
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-."
        if (
            not record_id
            or len(record_id) > 64
            or any(character not in allowed for character in record_id)
        ):
            raise ConversationArchiveTransportError("archive_grant_id_invalid")
        return await self._append_record(
            idempotency_key=self._stable_handle(
                "idempotency",
                f"discord-autonomy-grant:{int(guild_id)}:{record_id}:{int(message_id)}",
            ),
            record_id=record_id,
            guild_id=guild_id,
            channel_id=channel_id,
            kind=DiscordArchiveRecordKind.MINECRAFT_COMMAND.value,
            started_at=authored_at,
            ended_at=authored_at,
            source_user_id=user_id,
            owner_name=owner_name,
            parent_record_ids=(),
            lineage={
                "session": (
                    make_text_session_key(guild_id, channel_id, user_id),
                ),
                "memory_owner": (
                    memory_owner_scope(
                        guild_id=guild_id,
                        person_key=f"user:{int(user_id)}",
                    ),
                ),
            },
            body=text,
        )

    async def archive_minecraft_command(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        owner_name: str,
        message_id: int,
        authored_at: float,
        text: str,
    ) -> dict[str, Any]:
        source_key = (
            f"discord-minecraft-command:{int(guild_id)}:{int(message_id)}"
        )
        record_id = self._stable_handle("record", source_key)
        return await self._append_record(
            idempotency_key=self._stable_handle("idempotency", source_key),
            record_id=record_id,
            guild_id=guild_id,
            channel_id=channel_id,
            kind=DiscordArchiveRecordKind.MINECRAFT_COMMAND.value,
            started_at=authored_at,
            ended_at=authored_at,
            source_user_id=user_id,
            owner_name=owner_name,
            parent_record_ids=(),
            lineage={
                "session": (
                    make_text_session_key(guild_id, channel_id, user_id),
                ),
                "memory_owner": (
                    memory_owner_scope(
                        guild_id=guild_id,
                        person_key=f"user:{int(user_id)}",
                    ),
                ),
            },
            body=text,
        )

    async def archive_assistant_text(
        self,
        *,
        guild_id: int,
        channel_id: int,
        turn_id: str,
        text: str,
        parent_record_id: str | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        voice_key: tuple[int, int, str] | None = None
        parent = str(parent_record_id or "").strip()
        if not parent and user_id is not None:
            voice_key = (int(guild_id), int(user_id), str(turn_id))
            parent = self._voice_parent_by_turn.get(voice_key, "")
        if not parent:
            raise ConversationArchiveTransportError("archive_reply_parent_missing")
        source_key = f"discord-reply:{int(guild_id)}:{turn_id}:{parent}"
        occurred_at = float(self._clock())
        receipt = await self._append_record(
            idempotency_key=self._stable_handle("idempotency", source_key),
            record_id=self._stable_handle("record", source_key),
            guild_id=guild_id,
            channel_id=channel_id,
            kind=DiscordArchiveRecordKind.EVELYN_REPLY.value,
            started_at=occurred_at,
            ended_at=occurred_at,
            source_user_id=None,
            owner_name=None,
            parent_record_ids=(parent,),
            lineage={
                "turn": (str(turn_id),),
                "memory_evidence": (f"turn:{turn_id}:assistant",),
            },
            body=text,
        )
        if voice_key is not None:
            self._voice_parent_by_turn.pop(voice_key, None)
        return receipt

    async def observe_participation(self, update: ParticipationUpdate) -> dict[str, Any]:
        snapshot = update.snapshot
        if snapshot.channel_id is not None:
            self._last_channel[(int(update.guild_id), int(update.user_id))] = int(
                snapshot.channel_id
            )
        self._last_owner_name[(int(update.guild_id), int(update.user_id))] = str(
            update.owner_name
        )
        natural = json.dumps(
            {
                "guild": int(update.guild_id),
                "user": int(update.user_id),
                "owner": update.owner_name,
                "observed": float(update.observed_at),
                "snapshot": self._snapshot_payload(snapshot),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return await self._mutate(
            "/internal/conversation-archive/voice-state",
            {
                "idempotencyKey": self._stable_handle("voice-state", natural),
                "guildId": str(update.guild_id),
                "userId": str(update.user_id),
                "ownerName": str(update.owner_name),
                "observedAt": float(update.observed_at),
                "snapshot": self._snapshot_payload(snapshot),
            },
        )

    def consent_current(
        self,
        *,
        guild_id: int,
        channel_id: int | None,
        user_id: int,
    ) -> bool:
        if channel_id is None:
            return False
        return self._consent.get(
            (int(guild_id), int(channel_id), int(user_id)),
            False,
        ) is True

    async def set_consent(
        self,
        *,
        guild_id: str | int,
        actor_external_id: str | int,
        owner_name: str,
        channel_id: str | int | None,
        consented: bool,
        self_mute: bool,
        server_mute: bool,
        stage_suppress: bool,
        self_deaf: bool,
        server_deaf: bool,
    ) -> dict[str, Any]:
        guild = int(guild_id)
        user = int(actor_external_id)
        channel = None if channel_id is None else int(channel_id)
        if channel is None:
            channel = self._last_channel.get((guild, user))
        if channel is None:
            raise ConversationArchiveTransportError("archive_consent_channel_unknown")
        resolved_owner_name = str(owner_name or "").strip()
        if not resolved_owner_name:
            resolved_owner_name = self._last_owner_name.get((guild, user), "")
        if not resolved_owner_name:
            raise ConversationArchiveTransportError("archive_owner_name_required")
        observed_at = float(self._clock())
        snapshot = DiscordVoiceStateSnapshot(
            channel_id=(None if channel_id is None else channel),
            consent_current=bool(consented),
            self_mute=bool(self_mute),
            server_mute=bool(server_mute),
            stage_suppress=bool(stage_suppress),
            self_deaf=bool(self_deaf),
            server_deaf=bool(server_deaf),
        )
        receipt = await self._mutate(
            "/internal/conversation-archive/consent",
            {
                "idempotencyKey": self._stable_handle(
                    "consent",
                    f"{guild}:{user}:{channel}:{observed_at}:{secrets.token_hex(8)}",
                ),
                "guildId": str(guild),
                "userId": str(user),
                "ownerName": resolved_owner_name,
                "observedAt": observed_at,
                "snapshot": self._snapshot_payload(snapshot),
            },
        )
        self._last_channel[(guild, user)] = channel
        self._last_owner_name[(guild, user)] = resolved_owner_name
        self._consent[(guild, channel, user)] = bool(consented)
        return receipt

    async def authorize_voice_capture(
        self,
        *,
        guild_id: int,
        channel_id: int,
        user_id: int,
        voice_ingress_epoch: int,
    ) -> bool:
        response = await self._request(
            "POST",
            "/internal/conversation-archive/voice-admission",
            {
                "guildId": str(guild_id),
                "channelId": str(channel_id),
                "userId": str(user_id),
            },
            purpose="ingest",
        )
        return response.get("allowed") is True

    async def capture_feedback(
        self,
        *,
        task_id: str,
        source_record_id: str,
        guild_id: int,
        request_channel_id: int,
        source_channel_id: int,
        user_id: int,
        owner_name: str,
        session_id: str,
        surface: str,
        category: str,
        correction: str,
        requested_change_scope: str,
        feedback_nonce: str,
        shared_session_lease_id: str,
    ) -> DiscordFeedbackCaptureResult:
        """Submit one bounded correction; this adapter exposes no promotion calls."""

        normalized_task = str(task_id or "").strip()
        normalized_source = str(source_record_id or "").strip()
        normalized_session = str(session_id or "").strip()
        normalized_owner = str(owner_name or "").strip()
        normalized_surface = str(surface or "").strip()
        normalized_category = str(category or "").strip()
        normalized_scope = str(requested_change_scope or "").strip()
        exact_correction = str(correction or "")
        normalized_nonce = str(feedback_nonce or "").strip()
        normalized_lease = str(shared_session_lease_id or "").strip()
        if (
            not _archive_identifier_valid(normalized_task, maximum=128)
            or not _archive_identifier_valid(normalized_source, maximum=64)
            or not _archive_identifier_valid(normalized_session, maximum=128)
            or not _archive_identifier_valid(normalized_nonce, maximum=128)
            or not _archive_identifier_valid(normalized_lease, maximum=128)
            or not normalized_owner
            or len(normalized_owner) > 80
            or "\x00" in normalized_owner
            or normalized_surface not in DISCORD_FEEDBACK_SURFACES
            or normalized_category not in DISCORD_FEEDBACK_CATEGORIES
            or normalized_scope not in DISCORD_FEEDBACK_ENGINEERING_SCOPES
            or not exact_correction.strip()
            or len(exact_correction) > 4_000
            or "\x00" in exact_correction
        ):
            raise ConversationArchiveTransportError(
                "archive_feedback_request_invalid"
            )
        guild = _positive_archive_integer(guild_id)
        request_channel = _positive_archive_integer(request_channel_id)
        channel = _positive_archive_integer(source_channel_id)
        caller = _positive_archive_integer(user_id)
        natural = (
            f"{guild}:{caller}:{normalized_source}:{normalized_nonce}"
        )
        response = await self._mutate(
            "/internal/conversation-archive/feedback/capture",
            {
                "idempotencyKey": self._stable_handle(
                    "feedback-idempotency", natural
                ),
                "taskId": normalized_task,
                "sourceRecordId": normalized_source,
                "category": normalized_category,
                "correction": exact_correction,
                "nonce": normalized_nonce,
                "callerUserId": str(caller),
                "ownerName": normalized_owner,
                "guildId": str(guild),
                "requestChannelId": str(request_channel),
                "sourceChannelId": str(channel),
                "sessionId": normalized_session,
                "surface": normalized_surface,
                "requestedChangeScope": normalized_scope,
                "sharedSessionLeaseId": normalized_lease,
            },
        )
        workflow = response.get("workflow")
        if (
            response.get("ok") is not True
            or not isinstance(workflow, dict)
            or workflow.get("schema") != "evelyn.feedback-workflow-public.v1"
            or workflow.get("contentFree") is not True
            or workflow.get("actionable") is not False
            or workflow.get("category") != normalized_category
            or workflow.get("route")
            not in {
                "review_only",
                "identity_review",
                "human_engineering_required",
            }
            or not hmac.compare_digest(
                str(workflow.get("sourceRecordId") or ""),
                normalized_source,
            )
            or not _archive_identifier_valid(
                workflow.get("workflowId"), maximum=128
            )
            or workflow.get("state") != workflow.get("route")
            or workflow.get("versionId") is not None
        ):
            raise ConversationArchiveTransportError(
                "archive_feedback_receipt_invalid"
            )
        return DiscordFeedbackCaptureResult(
            workflow_id=str(workflow["workflowId"]),
            category=normalized_category,
            route=str(workflow["route"]),
            state=str(workflow["state"]),
            actionable=False,
        )

    async def read_self(
        self,
        *,
        actor_external_id: str,
        guild_id: str,
        interaction_id: str,
        started_at: datetime | None,
        ended_at: datetime | None,
        page_handle: str | None = None,
    ) -> DiscordArchiveRecordPage:
        caller, guild, interaction = self._user_view_identity(
            actor_external_id=actor_external_id,
            guild_id=guild_id,
            interaction_id=interaction_id,
        )
        authorize_payload: dict[str, Any] = {
            "context": "GUILD",
            "interactionId": interaction,
            "callerUserId": caller,
            "guildId": guild,
            "action": "records",
        }
        if page_handle is None:
            if started_at is not None or ended_at is not None:
                authorize_payload.update(
                    {
                        "startedAt": _datetime_text(started_at),
                        "endedAt": _datetime_text(ended_at),
                    }
                )
        else:
            if started_at is not None or ended_at is not None:
                raise ConversationArchiveTransportError(
                    "archive_user_view_page_query_invalid"
                )
            authorize_payload["pageHandle"] = self._opaque_user_view_token(
                page_handle
            )
        handle = await self._authorize_user_view(authorize_payload)
        response = await self._request(
            "POST",
            "/internal/conversation-archive/self/records",
            {
                "context": "GUILD",
                "interactionId": interaction,
                "callerUserId": caller,
                "guildId": guild,
                "handle": handle,
            },
            purpose="user-view",
        )
        rows = response.get("records")
        snapshot_generation = response.get("snapshotGeneration")
        raw_next_page_handle = response.get("nextPageHandle")
        if (
            not isinstance(rows, list)
            or type(snapshot_generation) is not int
            or snapshot_generation < 0
        ):
            raise ConversationArchiveTransportError("archive_records_response_invalid")
        next_page_handle = (
            None
            if raw_next_page_handle is None
            else self._opaque_user_view_token(raw_next_page_handle)
        )
        return DiscordArchiveRecordPage(
            records=tuple(
                DiscordArchiveRecordView(
                    record_id=str(row.get("recordId") or ""),
                    started_at=_parse_datetime(
                        row.get("startedAt") or row.get("createdAt")
                    ),
                    record_type=str(
                        row.get("kind") or row.get("recordType") or "record"
                    ),
                    body=str(row.get("body") or ""),
                )
                for row in rows
                if isinstance(row, dict)
            ),
            next_page_handle=next_page_handle,
            snapshot_generation=snapshot_generation,
        )

    async def preview_user_deletion(
        self,
        *,
        actor_external_id: str,
        request_guild_id: str,
        interaction_id: str,
        started_at: datetime | None,
        ended_at: datetime | None,
    ) -> DiscordDeletionPreview:
        caller, guild, interaction = self._user_view_identity(
            actor_external_id=actor_external_id,
            guild_id=request_guild_id,
            interaction_id=interaction_id,
        )
        authorize_payload: dict[str, Any] = {
            "context": "GUILD",
            "interactionId": interaction,
            "callerUserId": caller,
            "guildId": guild,
            "action": "delete-preview",
        }
        if started_at is not None or ended_at is not None:
            authorize_payload.update(
                {
                    "startedAt": _datetime_text(started_at),
                    "endedAt": _datetime_text(ended_at),
                }
            )
        handle = await self._authorize_user_view(authorize_payload)
        response = await self._request(
            "POST",
            "/internal/conversation-archive/self/delete/preview",
            {
                "context": "GUILD",
                "interactionId": interaction,
                "callerUserId": caller,
                "guildId": guild,
                "handle": handle,
            },
            purpose="user-view",
        )
        counts = response.get("countsByGuild") or {}
        if not isinstance(counts, dict):
            raise ConversationArchiveTransportError("archive_preview_response_invalid")
        return DiscordDeletionPreview(
            preview_id=str(response.get("previewId") or ""),
            counts_by_guild={str(key): int(value) for key, value in counts.items()},
            dependent_record_count=int(response.get("dependentRecordCount") or 0),
            interval_count=int(response.get("intervalCount") or 0),
            all_guilds=response.get("allGuilds") is True,
        )

    async def apply_user_deletion(
        self,
        *,
        preview_id: str,
        actor_external_id: str,
        request_guild_id: str,
        interaction_id: str,
    ) -> DiscordDeletionResult:
        caller, guild, interaction = self._user_view_identity(
            actor_external_id=actor_external_id,
            guild_id=request_guild_id,
            interaction_id=interaction_id,
        )
        handle = await self._authorize_user_view(
            {
                "context": "GUILD",
                "interactionId": interaction,
                "callerUserId": caller,
                "guildId": guild,
                "action": "delete-apply",
                "previewId": self._opaque_user_view_token(preview_id),
            }
        )
        response = await self._request(
            "POST",
            "/internal/conversation-archive/self/delete/apply",
            {
                "context": "GUILD",
                "interactionId": interaction,
                "callerUserId": caller,
                "guildId": guild,
                "handle": handle,
            },
            purpose="user-view",
        )
        return DiscordDeletionResult(
            status=str(response.get("state") or ""),
            affected_records=int(response.get("affectedRecords") or 0),
            dependent_records=int(response.get("dependentRecords") or 0),
            affected_intervals=int(response.get("affectedIntervals") or 0),
        )

    async def _authorize_user_view(self, payload: dict[str, Any]) -> str:
        response = await self._request(
            "POST",
            "/internal/conversation-archive/self/authorize",
            payload,
            purpose="user-view-issue",
        )
        return self._opaque_user_view_token(response.get("handle"))

    @staticmethod
    def _user_view_identity(
        *,
        actor_external_id: str,
        guild_id: str,
        interaction_id: str,
    ) -> tuple[str, str, str]:
        values = tuple(str(value) for value in (
            actor_external_id,
            guild_id,
            interaction_id,
        ))
        if any(not value.isdecimal() or int(value) <= 0 for value in values):
            raise ConversationArchiveTransportError(
                "archive_user_view_identity_invalid"
            )
        return values  # type: ignore[return-value]

    @staticmethod
    def _opaque_user_view_token(value: Any) -> str:
        token = str(value or "")
        allowed = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        )
        if not 0 < len(token) <= 128 or any(
            character not in allowed for character in token
        ):
            raise ConversationArchiveTransportError(
                "archive_user_view_handle_invalid"
            )
        return token

    async def poll_otp_deliveries(self) -> tuple[dict[str, Any], ...]:
        response = await self._request(
            "POST",
            "/internal/conversation-archive/admin/otp-delivery/poll",
            {},
            purpose="otp-delivery",
        )
        deliveries = response.get("deliveries") or []
        if not isinstance(deliveries, list):
            raise ConversationArchiveTransportError("archive_otp_poll_invalid")
        return tuple(row for row in deliveries if isinstance(row, dict))

    async def acknowledge_otp_delivery(
        self,
        *,
        delivery_id: str,
        delivered: bool,
    ) -> None:
        await self._request(
            "POST",
            "/internal/conversation-archive/admin/otp-delivery/ack",
            {"deliveryId": str(delivery_id), "delivered": delivered is True},
            purpose="otp-delivery",
        )

    async def poll_purge_owner_work(self) -> tuple[dict[str, Any], ...]:
        response = await self._request(
            "POST",
            "/internal/conversation-archive/purge-owner/poll",
            {},
            purpose="purge-owner",
        )
        if (
            set(response) != {"ok", "workOrders", "contentFree"}
            or response.get("ok") is not True
            or response.get("contentFree") is not True
            or not isinstance(response.get("workOrders"), list)
            or len(response["workOrders"]) > 20
        ):
            raise ConversationArchiveTransportError(
                "archive_purge_owner_poll_invalid"
            )
        return tuple(
            _strict_purge_owner_work_order(row)
            for row in response["workOrders"]
        )

    async def acknowledge_purge_owner_receipt(
        self,
        *,
        request_id: str,
        deletion_generation: int,
        scope_digest: str,
        sink: str,
    ) -> dict[str, Any]:
        if (
            not _archive_identifier_valid(request_id, maximum=64)
            or type(deletion_generation) is not int
            or deletion_generation < 1
            or not _sha256_digest_valid(scope_digest)
            or not isinstance(sink, str)
            or sink not in _REMOTE_PURGE_SINKS
        ):
            raise ConversationArchiveTransportError(
                "archive_purge_owner_receipt_invalid"
            )
        response = await self._request(
            "POST",
            "/internal/conversation-archive/purge-owner/ack",
            {
                "requestId": request_id,
                "deletionGeneration": deletion_generation,
                "scopeDigest": scope_digest,
                "sink": sink,
                "contentFree": True,
                "complete": True,
                "remainingCopies": 0,
                "manualReviewCount": 0,
            },
            purpose="purge-owner",
        )
        if (
            set(response)
            != {"ok", "state", "archiveCompleted", "contentFree"}
            or response.get("ok") is not True
            or response.get("contentFree") is not True
            or type(response.get("archiveCompleted")) is not bool
            or not isinstance(response.get("state"), str)
            or response.get("state")
            not in {
                "local_fully_purged",
                "local_cleanup_pending",
                "manual_review",
            }
            or (
                response["archiveCompleted"]
                != (response["state"] == "local_fully_purged")
            )
        ):
            raise ConversationArchiveTransportError(
                "archive_purge_owner_receipt_invalid"
            )
        return response

    async def _append_record(
        self,
        *,
        idempotency_key: str,
        record_id: str,
        guild_id: int,
        channel_id: int,
        kind: str,
        started_at: float,
        ended_at: float,
        source_user_id: int | None,
        owner_name: str | None,
        parent_record_ids: tuple[str, ...],
        lineage: dict[str, tuple[str, ...]],
        body: str,
    ) -> dict[str, Any]:
        response = await self._mutate(
            "/internal/conversation-archive/record",
            {
                "idempotencyKey": idempotency_key,
                "recordId": record_id,
                "guildId": str(guild_id),
                "channelId": str(channel_id),
                "kind": str(kind),
                "startedAt": float(started_at),
                "endedAt": float(ended_at),
                "sourceUserId": None if source_user_id is None else str(source_user_id),
                "ownerName": None if owner_name is None else str(owner_name),
                "parentRecordIds": list(parent_record_ids),
                "lineage": {
                    kind: list(values)
                    for kind, values in sorted(lineage.items())
                },
                "body": str(body),
            },
        )
        if response.get("ok") is not True or not hmac.compare_digest(
            str(response.get("recordId") or ""),
            record_id,
        ):
            raise ConversationArchiveTransportError(
                "archive_record_receipt_invalid"
            )
        return response

    async def _mutate(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._mutation_lock:
            if self._generation is None:
                await self._activate_generation_locked(replace=True)
            assert self._generation is not None
            self._sequence += 1
            body = {
                **payload,
                "generation": self._generation,
                "sequence": self._sequence,
            }
            try:
                return await self._request("POST", path, body, purpose="ingest")
            except ConversationArchiveTransportError as exc:
                if exc.status != 409 or exc.code not in {
                    "archive_generation_stale",
                }:
                    raise
                await self._activate_generation_locked(replace=False)
                self._sequence = 1
                body["generation"] = self._generation
                body["sequence"] = self._sequence
                return await self._request("POST", path, body, purpose="ingest")

    async def _activate_generation_locked(self, *, replace: bool) -> str:
        generation = self._generation
        if replace or generation is None:
            generation = str(self._generation_factory(16))
        if not _archive_identifier_valid(generation, maximum=128):
            raise ConversationArchiveTransportError(
                "archive_generation_invalid"
            )
        response = await self._request(
            "POST",
            "/internal/conversation-archive/generation",
            {"generation": generation},
            purpose="ingest",
        )
        if (
            response.get("ok") is not True
            or response.get("generation") != generation
            or type(response.get("activated")) is not bool
        ):
            raise ConversationArchiveTransportError(
                "archive_generation_receipt_invalid"
            )
        self._generation = generation
        self._sequence = 0
        self._voice_parent_by_turn.clear()
        return generation

    async def _ensure_generation(self) -> str:
        if self._generation is not None:
            return self._generation
        return await self.begin_generation()

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        *,
        purpose: str,
    ) -> dict[str, Any]:
        if purpose not in _TRANSPORT_PURPOSES:
            raise ValueError("archive_transport_purpose_invalid")
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        last_error: BaseException | None = None
        for _attempt in range(2):
            timestamp = str(int(self._clock()))
            nonce = str(self._nonce_factory(16))
            headers = self._signed_headers(
                method=method,
                path=path,
                body=body,
                purpose=purpose,
                timestamp=timestamp,
                nonce=nonce,
            )
            try:
                session = await self._get_http_session()
                async with session.request(
                    method,
                    f"{self._base_url}{path}",
                    data=body,
                    headers=headers,
                    timeout=self._request_timeout_seconds,
                ) as response:
                    raw = await response.read()
                    if len(raw) > _MAX_RESPONSE_BYTES:
                        raise ConversationArchiveTransportError(
                            "archive_response_too_large",
                            status=int(response.status),
                        )
                    try:
                        decoded = json.loads(raw.decode("utf-8")) if raw else {}
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ConversationArchiveTransportError(
                            "archive_response_invalid",
                            status=int(response.status),
                        ) from exc
                    if not isinstance(decoded, dict):
                        raise ConversationArchiveTransportError(
                            "archive_response_invalid",
                            status=int(response.status),
                        )
                    if int(response.status) < 200 or int(response.status) >= 300:
                        raise ConversationArchiveTransportError(
                            str(decoded.get("error") or "archive_request_failed"),
                            status=int(response.status),
                        )
                    return decoded
            except ConversationArchiveTransportError:
                raise
            except Exception as exc:
                last_error = exc
        raise ConversationArchiveTransportError("archive_transport_unavailable") from last_error

    def _signed_headers(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        purpose: str,
        timestamp: str,
        nonce: str,
    ) -> dict[str, str]:
        master_key = (
            self._user_view_master_key
            if purpose in {"user-view-issue", "user-view"}
            else self._master_key
        )
        key = hmac.new(
            master_key,
            _TRANSPORT_KEY_DOMAIN + purpose.encode("ascii"),
            hashlib.sha256,
        ).digest()
        canonical = "\n".join(
            (
                purpose,
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

    @staticmethod
    def _snapshot_payload(snapshot: DiscordVoiceStateSnapshot) -> dict[str, Any]:
        return {
            "channelId": None if snapshot.channel_id is None else str(snapshot.channel_id),
            "present": snapshot.channel_id is not None,
            "consentCurrent": snapshot.consent_current is True,
            "gatewayKnown": snapshot.gateway_current is True,
            "selfMute": snapshot.self_mute is True,
            "serverMute": snapshot.server_mute is True,
            "suppressed": snapshot.stage_suppress is True,
            "selfDeaf": snapshot.self_deaf is True,
            "serverDeaf": snapshot.server_deaf is True,
        }


class DiscordSharedArchiveGate:
    """Apply one shared-session currentness check to every Discord ingest root."""

    def __init__(
        self,
        *,
        client: DiscordConversationArchiveClient,
        sessions: DiscordSharedSessionRegistry,
    ) -> None:
        self._client = client
        self.sessions = sessions
        self._consent_sessions: dict[
            tuple[int, int, int], DiscordSharedSession
        ] = {}
        self._feedback_targets: dict[
            tuple[int, int, str], _DiscordFeedbackTarget
        ] = {}
        self._pending_feedback_targets: dict[
            tuple[int, int, str], _DiscordFeedbackTarget
        ] = {}

    @property
    def generation(self) -> str | None:
        return self._client.generation

    async def begin_generation(self, *, force: bool = False) -> str:
        generation = await self._client.begin_generation(force=force)
        self.sessions.begin_generation(generation)
        self._consent_sessions.clear()
        self._feedback_targets.clear()
        self._pending_feedback_targets.clear()
        return generation

    async def open_shared_session_lease(
        self,
        session: DiscordSharedSession,
    ) -> None:
        if not self._session_is_current(session):
            self._inactive()
        await self._client.open_shared_session_lease(
            operator_user_id=session.operator_user_id,
            guild_id=session.guild_id,
            text_channel_id=session.text_channel_id,
            voice_channel_id=session.voice_channel_id,
            lease_id=session.lease_id,
        )
        if not self._session_is_current(session):
            try:
                await self._client.close_shared_session_lease(
                    guild_id=session.guild_id,
                    lease_id=session.lease_id,
                )
            except Exception:
                pass
            self._inactive()

    async def close_shared_session_lease(
        self,
        session: DiscordSharedSession,
    ) -> None:
        try:
            await self._client.close_shared_session_lease(
                guild_id=session.guild_id,
                lease_id=session.lease_id,
            )
        finally:
            self._discard_feedback_targets_for_session(session)

    async def archive_user_text(self, **payload: Any) -> dict[str, Any]:
        self._require_text(
            guild_id=payload.get("guild_id"),
            channel_id=payload.get("channel_id"),
        )
        return await self._client.archive_user_text(**payload)

    async def archive_final_transcript(self, **payload: Any) -> dict[str, Any]:
        self._require_voice(
            guild_id=payload.get("guild_id"),
            channel_id=payload.get("channel_id"),
        )
        return await self._client.archive_final_transcript(**payload)

    async def archive_assistant_text(self, **payload: Any) -> dict[str, Any]:
        guild_id = _positive_archive_integer(payload.get("guild_id"))
        channel_id = _positive_archive_integer(payload.get("channel_id"))
        session = self._require_guild(guild_id=guild_id)
        if channel_id not in {session.text_channel_id, session.voice_channel_id}:
            self._inactive()
        receipt = await self._client.archive_assistant_text(**payload)
        if not self._session_is_current(session):
            self._inactive()
        user_id_value = payload.get("user_id")
        turn_id = str(payload.get("turn_id") or "").strip()
        record_id = str(receipt.get("recordId") or "").strip()
        if (
            user_id_value is not None
            and _archive_identifier_valid(turn_id, maximum=128)
            and _archive_identifier_valid(record_id, maximum=64)
        ):
            user_id = _positive_archive_integer(user_id_value)
            surface = (
                "voice"
                if channel_id == session.voice_channel_id
                else "discord"
            )
            session_id = (
                make_voice_session_key(guild_id, channel_id, user_id)
                if surface == "voice"
                else make_text_session_key(guild_id, channel_id, user_id)
            )
            self._pending_feedback_targets[(guild_id, user_id, surface)] = (
                _DiscordFeedbackTarget(
                    task_id=turn_id,
                    source_record_id=record_id,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    surface=surface,
                    session_id=session_id,
                    shared_session=session,
                )
            )
        return receipt

    async def confirm_assistant_delivery(self, **payload: Any) -> bool:
        guild_id = _positive_archive_integer(payload.get("guild_id"))
        channel_id = _positive_archive_integer(payload.get("channel_id"))
        user_id = _positive_archive_integer(payload.get("user_id"))
        turn_id = str(payload.get("turn_id") or "").strip()
        if not _archive_identifier_valid(turn_id, maximum=128):
            return False
        session = self.sessions.current(
            guild_id=guild_id,
            generation=self.generation,
        )
        if session is None or channel_id not in {
            session.text_channel_id,
            session.voice_channel_id,
        }:
            return False
        surface = "voice" if channel_id == session.voice_channel_id else "discord"
        key = (guild_id, user_id, surface)
        target = self._pending_feedback_targets.get(key)
        if (
            target is None
            or target.shared_session is not session
            or target.task_id != turn_id
            or target.channel_id != channel_id
        ):
            return False
        if self._pending_feedback_targets.get(key) is not target:
            return False
        self._pending_feedback_targets.pop(key, None)
        self._feedback_targets[key] = target
        return True

    async def capture_feedback(self, **payload: Any) -> DiscordFeedbackCaptureResult:
        guild_id = _positive_archive_integer(payload.get("guild_id"))
        request_channel_id = _positive_archive_integer(
            payload.get("channel_id")
        )
        user_id = _positive_archive_integer(payload.get("user_id"))
        surface = str(payload.get("source_surface") or "").strip()
        session = self._require_text(
            guild_id=guild_id,
            channel_id=request_channel_id,
        )
        if surface not in DISCORD_FEEDBACK_SURFACES:
            raise ConversationArchiveTransportError(
                "archive_feedback_request_invalid"
            )
        target = self._feedback_targets.get((guild_id, user_id, surface))
        if target is None or target.shared_session is not session:
            raise ConversationArchiveTransportError(
                "archive_feedback_target_missing"
            )
        result = await self._client.capture_feedback(
            task_id=target.task_id,
            source_record_id=target.source_record_id,
            guild_id=target.guild_id,
            request_channel_id=request_channel_id,
            source_channel_id=target.channel_id,
            user_id=target.user_id,
            owner_name=payload.get("owner_name"),
            session_id=target.session_id,
            surface=target.surface,
            category=payload.get("category"),
            correction=payload.get("correction"),
            requested_change_scope=payload.get("requested_change_scope"),
            feedback_nonce=payload.get("feedback_nonce"),
            shared_session_lease_id=session.lease_id,
        )
        if not self._session_is_current(session):
            self._inactive()
        return result

    def purge_feedback_targets(
        self,
        target_matches: Callable[[dict[str, Any]], bool],
    ) -> tuple[int, int, int]:
        """Remove exact in-memory source mappings before feedback-state purge ACK."""

        mappings = (self._feedback_targets, self._pending_feedback_targets)
        if not callable(target_matches):
            return (0, sum(len(mapping) for mapping in mappings), 1)

        def projection(target: _DiscordFeedbackTarget) -> dict[str, Any]:
            return {
                "guild_id": target.guild_id,
                "turn_id": target.task_id,
                "session_key": target.session_id,
            }

        removed = 0
        try:
            for mapping in mappings:
                for key, target in tuple(mapping.items()):
                    if (
                        target_matches(projection(target)) is True
                        and mapping.get(key) is target
                    ):
                        mapping.pop(key, None)
                        removed += 1
            remaining = sum(
                target_matches(projection(target)) is True
                for mapping in mappings
                for target in mapping.values()
            )
        except Exception:
            return (removed, sum(len(mapping) for mapping in mappings), 1)
        return (removed, remaining, 0)

    def _discard_feedback_targets_for_session(
        self,
        session: DiscordSharedSession,
    ) -> None:
        for mapping in (self._feedback_targets, self._pending_feedback_targets):
            for key, target in tuple(mapping.items()):
                if target.shared_session is session and mapping.get(key) is target:
                    mapping.pop(key, None)

    async def archive_autonomy_grant(self, **payload: Any) -> dict[str, Any]:
        session = self._require_text(
            guild_id=payload.get("guild_id"),
            channel_id=payload.get("channel_id"),
        )
        if _positive_archive_integer(payload.get("user_id")) != session.operator_user_id:
            self._inactive()
        return await self._client.archive_autonomy_grant(**payload)

    async def archive_minecraft_command(self, **payload: Any) -> dict[str, Any]:
        session = self._require_text(
            guild_id=payload.get("guild_id"),
            channel_id=payload.get("channel_id"),
        )
        if _positive_archive_integer(payload.get("user_id")) != session.operator_user_id:
            self._inactive()
        receipt = await self._client.archive_minecraft_command(**payload)
        if not self._session_is_current(session):
            self._inactive()
        return receipt

    async def authorize_voice_capture(self, **payload: Any) -> bool:
        session = self._require_voice(
            guild_id=payload.get("guild_id"),
            channel_id=payload.get("channel_id"),
        )
        allowed = await self._client.authorize_voice_capture(**payload)
        return allowed is True and self._session_is_current(session)

    async def observe_participation(
        self,
        update: ParticipationUpdate,
    ) -> dict[str, Any]:
        guild_id = _positive_archive_integer(update.guild_id)
        session = self.sessions.current(
            guild_id=guild_id,
            generation=self.generation,
        )
        if session is None:
            expired = self.sessions.peek(guild_id=guild_id)
            if not self._is_exact_closure(update, expired):
                self._inactive()
            session = expired
        assert session is not None
        channel_ids = {
            int(row.channel_id)
            for row in (*update.opened, *update.closed)
        }
        if update.snapshot.channel_id is not None:
            channel_ids.add(int(update.snapshot.channel_id))
        if channel_ids and channel_ids != {session.voice_channel_id}:
            self._inactive()
        return await self._client.observe_participation(update)

    def consent_current(
        self,
        *,
        guild_id: int,
        channel_id: int | None,
        user_id: int,
    ) -> bool:
        if channel_id is None:
            return False
        try:
            session = self._require_voice(
                guild_id=guild_id,
                channel_id=channel_id,
            )
        except ConversationArchiveTransportError:
            return False
        key = (int(guild_id), int(channel_id), int(user_id))
        if self._consent_sessions.get(key) is not session:
            return False
        return self._client.consent_current(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
        )

    async def set_consent(self, **payload: Any) -> dict[str, Any]:
        guild_id = _positive_archive_integer(payload.get("guild_id"))
        session = self._require_guild(guild_id=guild_id)
        channel_id = payload.get("channel_id")
        consented = payload.get("consented") is True
        if channel_id is None:
            if consented:
                self._inactive()
        elif _positive_archive_integer(channel_id) != session.voice_channel_id:
            self._inactive()
        receipt = await self._client.set_consent(**payload)
        if not self._session_is_current(session):
            self._inactive()
        actor_user_id = _positive_archive_integer(
            payload.get("actor_external_id")
        )
        key = (guild_id, session.voice_channel_id, actor_user_id)
        if consented:
            self._consent_sessions[key] = session
        else:
            self._consent_sessions.pop(key, None)
        return receipt

    def _session_is_current(self, session: DiscordSharedSession) -> bool:
        return self.sessions.current(
            guild_id=session.guild_id,
            generation=self.generation,
        ) is session

    def _require_guild(self, *, guild_id: Any) -> DiscordSharedSession:
        guild = _positive_archive_integer(guild_id)
        generation = self.generation
        if generation is None:
            self._inactive()
        session = self.sessions.current(
            guild_id=guild,
            generation=generation,
        )
        if session is None:
            self._inactive()
        return session

    def _require_text(
        self,
        *,
        guild_id: Any,
        channel_id: Any,
    ) -> DiscordSharedSession:
        guild = _positive_archive_integer(guild_id)
        channel = _positive_archive_integer(channel_id)
        session = self._require_guild(guild_id=guild)
        if session.text_channel_id != channel:
            self._inactive()
        return session

    def _require_voice(
        self,
        *,
        guild_id: Any,
        channel_id: Any,
    ) -> DiscordSharedSession:
        guild = _positive_archive_integer(guild_id)
        channel = _positive_archive_integer(channel_id)
        session = self._require_guild(guild_id=guild)
        if session.voice_channel_id != channel:
            self._inactive()
        return session

    def _is_exact_closure(
        self,
        update: ParticipationUpdate,
        session: DiscordSharedSession | None,
    ) -> bool:
        if (
            session is None
            or session.boot_generation != self.generation
            or update.opened
            or update.snapshot.gateway_current is not False
            or update.snapshot.channel_id not in {
                None,
                session.voice_channel_id,
            }
            or not update.closed
        ):
            return False
        return all(
            row.guild_id == session.guild_id
            and row.channel_id == session.voice_channel_id
            and row.user_id == update.user_id
            for row in update.closed
        )

    @staticmethod
    def _inactive() -> None:
        raise ConversationArchiveTransportError("archive_shared_session_inactive")


def _positive_archive_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ConversationArchiveTransportError("archive_shared_session_inactive")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConversationArchiveTransportError(
            "archive_shared_session_inactive"
        ) from exc
    if result <= 0:
        raise ConversationArchiveTransportError("archive_shared_session_inactive")
    return result


def _archive_identifier_valid(value: Any, *, maximum: int) -> bool:
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-"
    )
    return bool(
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value[0].isascii()
        and value[0].isalnum()
        and all(character in allowed for character in value)
    )


def _sha256_digest_valid(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_purge_owner_work_order(value: Any) -> dict[str, Any]:
    fields = {
        "requestId",
        "deletionGeneration",
        "scopeDigest",
        "reason",
        "requestedAt",
        "scopeAll",
        "guildId",
        "startedAt",
        "endedAt",
        "lineageHandles",
        "lineageComplete",
        "remainingSinks",
        "contentFree",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ConversationArchiveTransportError(
            "archive_purge_owner_work_invalid"
        )
    try:
        requested_at = _parse_datetime(value["requestedAt"])
        started_at = _parse_datetime(value["startedAt"])
        ended_at = _parse_datetime(value["endedAt"])
    except ConversationArchiveTransportError:
        raise ConversationArchiveTransportError(
            "archive_purge_owner_work_invalid"
        ) from None
    guild_id = value["guildId"]
    raw_handles = value["lineageHandles"]
    raw_sinks = value["remainingSinks"]
    if (
        not _archive_identifier_valid(value["requestId"], maximum=64)
        or type(value["deletionGeneration"]) is not int
        or value["deletionGeneration"] < 1
        or not _sha256_digest_valid(value["scopeDigest"])
        or not isinstance(value["reason"], str)
        or value["reason"]
        not in {"user_requested", "admin_requested", "retention_expired"}
        or not isinstance(value["requestedAt"], str)
        or requested_at is None
        or type(value["scopeAll"]) is not bool
        or (
            guild_id is not None
            and (
                not isinstance(guild_id, str)
                or not guild_id.isdecimal()
                or int(guild_id) <= 0
            )
        )
        or (
            value["startedAt"] is not None
            and (not isinstance(value["startedAt"], str) or started_at is None)
        )
        or (
            value["endedAt"] is not None
            and (not isinstance(value["endedAt"], str) or ended_at is None)
        )
        or (started_at is None) != (ended_at is None)
        or (
            started_at is not None
            and ended_at is not None
            and ended_at <= started_at
        )
        or not isinstance(raw_handles, list)
        or len(raw_handles) > 96
        or type(value["lineageComplete"]) is not bool
        or (value["lineageComplete"] is True and not raw_handles)
        or not isinstance(raw_sinks, list)
        or not raw_sinks
        or any(not isinstance(sink, str) for sink in raw_sinks)
        or tuple(raw_sinks) != tuple(sorted(set(raw_sinks)))
        or any(sink not in _REMOTE_PURGE_SINKS for sink in raw_sinks)
        or value["contentFree"] is not True
    ):
        raise ConversationArchiveTransportError(
            "archive_purge_owner_work_invalid"
        )
    handles: list[tuple[str, str]] = []
    for item in raw_handles:
        if (
            not isinstance(item, dict)
            or set(item) != {"kind", "digest"}
            or not isinstance(item.get("kind"), str)
            or item.get("kind") not in _LINEAGE_KINDS
            or not _sha256_digest_valid(item.get("digest"))
        ):
            raise ConversationArchiveTransportError(
                "archive_purge_owner_work_invalid"
            )
        handles.append((item["kind"], item["digest"]))
    if tuple(handles) != tuple(sorted(set(handles))):
        raise ConversationArchiveTransportError(
            "archive_purge_owner_work_invalid"
        )
    return {
        **value,
        "lineageHandles": [dict(item) for item in raw_handles],
        "remainingSinks": list(raw_sinks),
    }


def _datetime_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("archive_datetime_timezone_required")
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConversationArchiveTransportError("archive_record_time_invalid") from exc
    if parsed.tzinfo is None:
        raise ConversationArchiveTransportError("archive_record_time_invalid")
    return parsed.astimezone(timezone.utc)


def _require_timestamp(value: float, *, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name}_must_be_finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name}_must_be_finite")
    return result


__all__ = [
    "ConversationArchiveTransportError",
    "DISCORD_FEEDBACK_CATEGORIES",
    "DISCORD_FEEDBACK_ENGINEERING_SCOPES",
    "DISCORD_FEEDBACK_SURFACES",
    "DiscordArchiveCandidate",
    "DiscordArchiveRecordKind",
    "DiscordArchiveRecordPage",
    "DiscordArchiveRecordView",
    "DiscordConversationArchiveClient",
    "DiscordDeletionPreview",
    "DiscordDeletionResult",
    "DiscordFeedbackCaptureResult",
    "DiscordInteractionContext",
    "DiscordParticipationTracker",
    "DiscordSharedArchiveGate",
    "DiscordSharedSession",
    "DiscordSharedSessionRegistry",
    "DiscordVoiceStateSnapshot",
    "EPHEMERAL_DELETE_AFTER_SECONDS",
    "EphemeralDeleteOutcome",
    "INTERACTION_ACK_DEADLINE_SECONDS",
    "IntervalKind",
    "ParticipationInterval",
    "ParticipationUpdate",
    "RecordCommandPolicy",
    "RecordCommandRejected",
    "attempt_ephemeral_response_delete",
    "classify_discord_ephemeral_delete_error",
    "build_record_command_policy",
    "build_text_archive_candidate",
    "build_voice_transcript_archive_candidate",
    "select_self_scoped_records",
    "voice_state_snapshot_from_discord",
]
