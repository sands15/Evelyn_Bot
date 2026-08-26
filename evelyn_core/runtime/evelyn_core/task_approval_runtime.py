from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

from .workspace_task_tools import (
    WORKSPACE_EDIT_ABSENT_SHA,
    WORKSPACE_EDIT_MAX_PREVIEW_BYTES,
    workspace_task_args_hash,
)


TASK_APPROVAL_PUBLIC_SCHEMA = "task_approval.public.v1"
TASK_APPROVAL_PREVIEW_SCHEMA = "task_approval.preview.v1"
TASK_APPROVAL_DEFAULT_TTL_SEC = 300.0
TASK_APPROVAL_PREVIEW_TTL_SEC = 30.0

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_DIRTY_STATES = frozenset(
    {"modified", "staged", "modified_and_staged", "untracked", "deleted"}
)
_STAGE_PREVIEW_KEYS = frozenset(
    {
        "stageId",
        "hostInstanceId",
        "path",
        "mode",
        "baseSha256",
        "candidateSha256",
        "diffSha256",
        "fullDiff",
        "diffTruncated",
        "gitStatus",
        "dirtyStatus",
        "tracked",
        "dirtyBaseAcknowledgementRequired",
        "bytes",
        "issuedAt",
        "expiresAt",
        "argsHash",
        "previewDigest",
    }
)
_HOST_RESULT_KEYS = frozenset(
    {
        "attempted",
        "executed",
        "observed",
        "verified",
        "outcome",
        "code",
        "summary",
        "evidence",
    }
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("task_approval_args_invalid")
        return MappingProxyType(
            {key: _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise TypeError("task_approval_args_invalid")


def _plain_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json_value(item) for item in value]
    return value


def _identifier(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized if _IDENTIFIER_RE.fullmatch(normalized) else ""


def _sha256(value: Any, *, allow_absent: bool = False) -> str:
    normalized = str(value or "")
    if allow_absent and normalized == WORKSPACE_EDIT_ABSENT_SHA:
        return normalized
    return normalized if _SHA256_RE.fullmatch(normalized) else ""


def _host_result_is_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _HOST_RESULT_KEYS:
        return False
    flags = tuple(
        value.get(name)
        for name in ("attempted", "executed", "observed", "verified")
    )
    if any(type(flag) is not bool for flag in flags):
        return False
    attempted, executed, _observed, verified = flags
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


def _uncertain_receipt(code: str = "workspace_edit_apply_outcome_unverified") -> dict[str, Any]:
    return {
        "attempted": True,
        "executed": False,
        "observed": False,
        "verified": False,
        "outcome": "outcome_unverified",
        "code": code,
        "summary": "Workspace edit outcome is unverified.",
        "evidence": {},
    }


@dataclass(frozen=True, slots=True)
class TaskApprovalRequest:
    task_id: str
    grant_id: str
    action_run_id: str
    step_id: int
    tool: str
    args_hash: str
    surface: str
    args: Mapping[str, Any] = field(default_factory=dict, repr=False)
    max_steps: int = 6
    grant_expires_at: float = 0.0

    def __post_init__(self) -> None:
        try:
            frozen_args = _freeze_json_value(self.args)
        except TypeError:
            frozen_args = MappingProxyType({})
        object.__setattr__(self, "args", frozen_args)

    def valid(self) -> bool:
        try:
            calculated_hash = workspace_task_args_hash(_plain_json_value(self.args))
        except (TypeError, ValueError):
            return False
        return bool(
            _identifier(self.task_id)
            and _identifier(self.grant_id)
            and _identifier(self.action_run_id)
            and type(self.step_id) is int
            and self.step_id > 0
            and self.tool == "workspace_edit"
            and _sha256(self.args_hash)
            and hmac.compare_digest(calculated_hash, self.args_hash)
            and _identifier(self.surface)
            and type(self.max_steps) is int
            and self.step_id <= self.max_steps <= 10
            and type(self.grant_expires_at) in {int, float}
            and math.isfinite(float(self.grant_expires_at))
            and float(self.grant_expires_at) > 0.0
        )


@dataclass(frozen=True, slots=True)
class TaskApprovalResolution:
    state: Literal[
        "approved",
        "cancelled",
        "expired",
        "unsupported",
        "uncertain",
    ]
    receipt: Any | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class TaskApprovalClaim:
    approval_id: str
    claim_id: str
    generation: str = field(repr=False)
    request: TaskApprovalRequest = field(repr=False)
    stage_id: str = ""
    host_instance_id: str = ""
    base_sha256: str = ""
    candidate_sha256: str = ""
    preview_digest: str = ""
    dirty_base_acknowledged: bool = False

    def to_host_claim(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "claimId": self.claim_id,
            "stageId": self.stage_id,
            "hostInstanceId": self.host_instance_id,
            "taskId": self.request.task_id,
            "grantId": self.request.grant_id,
            "grantExpiresAt": float(self.request.grant_expires_at),
            "actionRunId": self.request.action_run_id,
            "stepId": self.request.step_id,
            "surface": self.request.surface,
            "tool": "edit",
            "argsHash": self.request.args_hash,
            "baseSha256": self.base_sha256,
            "candidateSha256": self.candidate_sha256,
            "previewDigest": self.preview_digest,
            "dirtyBaseAcknowledged": self.dirty_base_acknowledged,
        }


@dataclass(slots=True)
class _PendingApproval:
    approval_id: str
    request: TaskApprovalRequest
    stage: dict[str, Any] = field(repr=False)
    owner_loop: asyncio.AbstractEventLoop = field(repr=False)
    future: asyncio.Future[TaskApprovalResolution] = field(repr=False)
    issued_at: float
    expires_at: float
    state: str = "awaiting_approval"
    token_digest: str = field(default="", repr=False)
    token_expires_at: float = 0.0
    claim: TaskApprovalClaim | None = field(default=None, repr=False)


def _validated_stage_preview(
    request: TaskApprovalRequest,
    value: Any,
    *,
    now: float,
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != _STAGE_PREVIEW_KEYS:
        return None
    full_diff = value.get("fullDiff")
    dirty_status = str(value.get("dirtyStatus") or "")
    dirty_required = value.get("dirtyBaseAcknowledgementRequired")
    try:
        issued_at = float(value.get("issuedAt"))
        expires_at = float(value.get("expiresAt"))
        candidate_bytes = int(value.get("bytes"))
    except (TypeError, ValueError):
        return None
    if (
        not _identifier(value.get("stageId"))
        or not _identifier(value.get("hostInstanceId"))
        or value.get("mode") not in {"create", "replace"}
        or not isinstance(value.get("path"), str)
        or not str(value["path"])
        or not _sha256(value.get("baseSha256"), allow_absent=True)
        or not _sha256(value.get("candidateSha256"))
        or not _sha256(value.get("diffSha256"))
        or not _sha256(value.get("previewDigest"))
        or not isinstance(full_diff, str)
        or not full_diff
        or len(full_diff.encode("utf-8")) > WORKSPACE_EDIT_MAX_PREVIEW_BYTES
        or value.get("diffTruncated") is not False
        or not isinstance(value.get("gitStatus"), str)
        or dirty_status
        not in {
            "clean",
            "modified",
            "staged",
            "modified_and_staged",
            "untracked",
            "deleted",
            "absent",
        }
        or type(value.get("tracked")) is not bool
        or type(dirty_required) is not bool
        or (dirty_status in _DIRTY_STATES) is not dirty_required
        or candidate_bytes < 0
        or issued_at > now + 5.0
        or expires_at <= now
        or expires_at <= issued_at
        or value.get("argsHash") != request.args_hash
        or hashlib.sha256(full_diff.encode("utf-8")).hexdigest()
        != value.get("diffSha256")
    ):
        return None
    if value["mode"] == "create":
        if (
            value["baseSha256"] != WORKSPACE_EDIT_ABSENT_SHA
            or dirty_status != "absent"
            or value["tracked"] is not False
        ):
            return None
    elif value["baseSha256"] == WORKSPACE_EDIT_ABSENT_SHA or dirty_status in {
        "absent",
        "deleted",
    }:
        return None
    unsigned = {key: item for key, item in value.items() if key != "previewDigest"}
    expected_preview_digest = hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
    if not hmac.compare_digest(expected_preview_digest, str(value["previewDigest"])):
        return None
    return dict(value)


class TaskApprovalManager:
    def __init__(
        self,
        *,
        now: Callable[[], float] = time.time,
        ttl_sec: float = TASK_APPROVAL_DEFAULT_TTL_SEC,
        preview_ttl_sec: float = TASK_APPROVAL_PREVIEW_TTL_SEC,
        generation: str | None = None,
    ) -> None:
        self.now = now
        self.ttl_sec = max(30.0, min(600.0, float(ttl_sec)))
        self.preview_ttl_sec = max(5.0, min(30.0, float(preview_ttl_sec)))
        self.generation = _identifier(generation) or f"approval-{secrets.token_hex(12)}"
        self._lock = threading.RLock()
        self._pending: _PendingApproval | None = None

    @staticmethod
    def _set_future_result(
        future: asyncio.Future[TaskApprovalResolution],
        resolution: TaskApprovalResolution,
    ) -> None:
        if not future.done():
            future.set_result(resolution)

    @staticmethod
    def _cancel_future(future: asyncio.Future[TaskApprovalResolution]) -> None:
        if not future.done():
            future.cancel()

    def _resolve_locked(
        self,
        pending: _PendingApproval,
        resolution: TaskApprovalResolution,
    ) -> None:
        if pending.future.done():
            return
        try:
            pending.owner_loop.call_soon_threadsafe(
                self._set_future_result,
                pending.future,
                resolution,
            )
        except RuntimeError:
            pass

    def _matches_locked(
        self,
        task_id: str,
        approval_id: str,
    ) -> _PendingApproval | None:
        pending = self._pending
        if pending is None:
            return None
        if pending.expires_at <= self.now() and pending.state != "resuming":
            self._resolve_locked(
                pending,
                TaskApprovalResolution(
                    "uncertain",
                    receipt=_uncertain_receipt(),
                )
                if pending.state in {"claimed", "resuming", "cancelling"}
                else TaskApprovalResolution("expired"),
            )
            self._pending = None
            return None
        return pending if (
            hmac.compare_digest(pending.request.task_id, str(task_id or ""))
            and hmac.compare_digest(pending.approval_id, str(approval_id or ""))
        ) else None

    async def wait(
        self,
        request: TaskApprovalRequest,
        safe_preview: Mapping[str, Any],
    ) -> TaskApprovalResolution:
        current = self.now()
        if not isinstance(request, TaskApprovalRequest) or not request.valid():
            return TaskApprovalResolution("unsupported")
        stage = _validated_stage_preview(request, dict(safe_preview), now=current)
        if stage is None:
            return TaskApprovalResolution("unsupported")
        loop = asyncio.get_running_loop()
        with self._lock:
            existing = self._pending
            if existing is not None:
                if existing.state == "resuming":
                    return TaskApprovalResolution("unsupported")
                if existing.expires_at > current and not existing.future.done():
                    return TaskApprovalResolution("unsupported")
                if not existing.future.done():
                    was_claimed = existing.state in {"claimed", "resuming", "cancelling"}
                    existing.state = "uncertain" if was_claimed else "expired"
                    self._resolve_locked(
                        existing,
                        TaskApprovalResolution(
                            "uncertain",
                            receipt=_uncertain_receipt(),
                        )
                        if was_claimed
                        else TaskApprovalResolution("expired"),
                    )
                if self._pending is existing:
                    self._pending = None
            pending = _PendingApproval(
                approval_id=f"approval-{secrets.token_hex(12)}",
                request=request,
                stage=stage,
                owner_loop=loop,
                future=loop.create_future(),
                issued_at=current,
                expires_at=min(
                    current + self.ttl_sec,
                    float(stage["expiresAt"]),
                    float(request.grant_expires_at),
                ),
            )
            self._pending = pending
        try:
            timeout = max(0.0, pending.expires_at - self.now())
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout=timeout)
        except TimeoutError:
            with self._lock:
                was_claimed = pending.state in {"claimed", "resuming", "cancelling"}
                pending.state = "uncertain" if was_claimed else "expired"
                resolution = (
                    TaskApprovalResolution("uncertain", receipt=_uncertain_receipt())
                    if was_claimed
                    else TaskApprovalResolution("expired")
                )
                self._resolve_locked(pending, resolution)
            return resolution
        except asyncio.CancelledError:
            with self._lock:
                pending.state = (
                    "uncertain"
                    if pending.state in {"claimed", "resuming", "cancelling"}
                    else "cancelled"
                )
                pending.token_digest = ""
                try:
                    pending.owner_loop.call_soon_threadsafe(
                        self._cancel_future,
                        pending.future,
                    )
                except RuntimeError:
                    pass
            raise
        finally:
            with self._lock:
                if self._pending is pending and pending.state != "resuming":
                    self._pending = None

    def public_snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = self._pending
            if pending is None or pending.expires_at <= self.now():
                return {}
            return {
                "schema": TASK_APPROVAL_PUBLIC_SCHEMA,
                "state": pending.state,
                "taskId": pending.request.task_id,
                "approvalId": pending.approval_id,
                "step": pending.request.step_id,
                "maxSteps": pending.request.max_steps,
                "tool": pending.request.tool,
                "effect": "UTF-8 파일 1개 create/replace",
                "expiresAt": pending.expires_at,
            }

    def task_cancel_barrier(self, task_id: str) -> str:
        with self._lock:
            pending = self._pending
            if (
                pending is None
                or not hmac.compare_digest(
                    pending.request.task_id,
                    str(task_id or ""),
                )
                or pending.state
                not in {"claimed", "resuming", "cancelling", "uncertain"}
            ):
                return ""
            return pending.state

    def release_task_cancel_barrier(self, task_id: str) -> bool:
        with self._lock:
            pending = self._pending
            if (
                pending is None
                or pending.state != "resuming"
                or not hmac.compare_digest(
                    pending.request.task_id,
                    str(task_id or ""),
                )
            ):
                return False
            self._pending = None
            return True

    def issue_preview(self, task_id: str, approval_id: str) -> dict[str, Any]:
        with self._lock:
            pending = self._matches_locked(task_id, approval_id)
            if pending is None or pending.state != "awaiting_approval":
                return {"ok": False, "error": "task_approval_not_found"}
            current = self.now()
            confirm_expires_at = min(
                current + self.preview_ttl_sec,
                pending.expires_at,
                float(pending.stage["expiresAt"]),
            )
            if confirm_expires_at <= current:
                return {"ok": False, "error": "task_approval_preview_denied"}
            token = secrets.token_urlsafe(32)
            pending.token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            pending.token_expires_at = confirm_expires_at
            stage = pending.stage
            preview = {
                "schema": TASK_APPROVAL_PREVIEW_SCHEMA,
                "taskId": pending.request.task_id,
                "approvalId": pending.approval_id,
                "step": pending.request.step_id,
                "maxSteps": pending.request.max_steps,
                "tool": pending.request.tool,
                "effect": "UTF-8 파일 1개 create/replace",
                "path": stage["path"],
                "mode": stage["mode"],
                "baseSha256": stage["baseSha256"],
                "candidateSha256": stage["candidateSha256"],
                "diffSha256": stage["diffSha256"],
                "previewDigest": stage["previewDigest"],
                "fullDiff": stage["fullDiff"],
                "diffTruncated": False,
                "gitStatus": stage["gitStatus"],
                "dirtyStatus": stage["dirtyStatus"],
                "tracked": stage["tracked"],
                "dirtyBaseAcknowledgementRequired": stage[
                    "dirtyBaseAcknowledgementRequired"
                ],
                "requiresExplicitConfirmation": True,
                "automaticRetry": False,
            }
            return {
                "ok": True,
                "taskId": pending.request.task_id,
                "approvalId": pending.approval_id,
                "preview": preview,
                "confirmToken": token,
                "confirmExpiresAt": confirm_expires_at,
            }

    def claim(
        self,
        task_id: str,
        approval_id: str,
        confirm_token: str,
        user_confirmed: bool,
        dirty_base_acknowledged: bool = False,
    ) -> TaskApprovalClaim | None:
        with self._lock:
            pending = self._matches_locked(task_id, approval_id)
            current = self.now()
            if (
                pending is None
                or pending.state != "awaiting_approval"
                or user_confirmed is not True
                or type(dirty_base_acknowledged) is not bool
                or not isinstance(confirm_token, str)
                or not 32 <= len(confirm_token) <= 256
                or not pending.token_digest
                or current >= pending.token_expires_at
                or not hmac.compare_digest(
                    hashlib.sha256(confirm_token.encode("utf-8")).hexdigest(),
                    pending.token_digest,
                )
                or (
                    pending.stage["dirtyBaseAcknowledgementRequired"]
                    is not dirty_base_acknowledged
                )
            ):
                return None
            pending.token_digest = ""
            pending.token_expires_at = 0.0
            pending.state = "claimed"
            pending.claim = TaskApprovalClaim(
                approval_id=pending.approval_id,
                claim_id=f"claim-{secrets.token_hex(12)}",
                generation=self.generation,
                request=pending.request,
                stage_id=str(pending.stage["stageId"]),
                host_instance_id=str(pending.stage["hostInstanceId"]),
                base_sha256=str(pending.stage["baseSha256"]),
                candidate_sha256=str(pending.stage["candidateSha256"]),
                preview_digest=str(pending.stage["previewDigest"]),
                dirty_base_acknowledged=dirty_base_acknowledged,
            )
            return pending.claim

    def complete(self, claim: TaskApprovalClaim, result: Any) -> bool:
        with self._lock:
            if not isinstance(claim, TaskApprovalClaim):
                return False
            pending = self._matches_locked(
                claim.request.task_id,
                claim.approval_id,
            )
            if (
                pending is None
                or pending.state != "claimed"
                or pending.claim is None
                or claim != pending.claim
                or claim.generation != self.generation
            ):
                return False
            pending.state = "resuming"
            receipt = dict(result) if _host_result_is_valid(result) else _uncertain_receipt()
            if receipt["outcome"] == "succeeded":
                evidence = receipt.get("evidence")
                if not (
                    isinstance(evidence, dict)
                    and hmac.compare_digest(
                        str(evidence.get("sha256") or ""),
                        claim.candidate_sha256,
                    )
                ):
                    receipt = _uncertain_receipt()
            resolution = TaskApprovalResolution(
                "uncertain" if receipt["outcome"] == "outcome_unverified" else "approved",
                receipt=receipt,
            )
            self._resolve_locked(pending, resolution)
            return True

    def prepare_cancel(
        self,
        task_id: str,
        approval_id: str,
    ) -> TaskApprovalClaim | None:
        with self._lock:
            pending = self._matches_locked(task_id, approval_id)
            if pending is None or pending.state not in {"awaiting_approval", "claimed"}:
                return None
            if pending.state == "claimed":
                if pending.claim is None:
                    return None
                claim = pending.claim
            else:
                claim = TaskApprovalClaim(
                    approval_id=pending.approval_id,
                    claim_id=f"claim-{secrets.token_hex(12)}",
                    generation=self.generation,
                    request=pending.request,
                    stage_id=str(pending.stage["stageId"]),
                    host_instance_id=str(pending.stage["hostInstanceId"]),
                    base_sha256=str(pending.stage["baseSha256"]),
                    candidate_sha256=str(pending.stage["candidateSha256"]),
                    preview_digest=str(pending.stage["previewDigest"]),
                    dirty_base_acknowledged=False,
                )
                pending.claim = claim
            pending.state = "cancelling"
            pending.token_digest = ""
            pending.token_expires_at = 0.0
            return claim

    def complete_cancel(self, claim: TaskApprovalClaim, result: Any) -> bool:
        with self._lock:
            pending = self._pending
            if (
                pending is None
                or pending.state != "cancelling"
                or pending.claim is None
                or not isinstance(claim, TaskApprovalClaim)
                or claim != pending.claim
                or claim.generation != self.generation
            ):
                return False
            receipt = dict(result) if _host_result_is_valid(result) else {}
            evidence = receipt.get("evidence")
            cancelled = bool(
                receipt.get("outcome") == "succeeded"
                and receipt.get("code") == "workspace_edit_stage_cancelled"
                and isinstance(evidence, dict)
                and hmac.compare_digest(
                    str(evidence.get("approvalId") or ""),
                    claim.approval_id,
                )
                and hmac.compare_digest(
                    str(evidence.get("stageId") or ""),
                    claim.stage_id,
                )
                and hmac.compare_digest(
                    str(evidence.get("hostInstanceId") or ""),
                    claim.host_instance_id,
                )
            )
            pending.state = "cancelled" if cancelled else "uncertain"
            self._resolve_locked(
                pending,
                TaskApprovalResolution(
                    "cancelled" if cancelled else "uncertain",
                    receipt=(
                        receipt
                        if cancelled
                        else _uncertain_receipt(
                            "workspace_edit_cancel_outcome_unverified"
                        )
                    ),
                ),
            )
            return True

    def cancel(self, task_id: str, approval_id: str) -> TaskApprovalClaim | None:
        with self._lock:
            pending = self._matches_locked(task_id, approval_id)
            if pending is None:
                return None
            if pending.state == "claimed" and pending.claim is not None:
                pending.state = "uncertain"
                pending.token_digest = ""
                pending.token_expires_at = 0.0
                self._resolve_locked(
                    pending,
                    TaskApprovalResolution(
                        "uncertain",
                        receipt=_uncertain_receipt(),
                    ),
                )
                return pending.claim
            if pending.state != "awaiting_approval":
                return None
            pending.state = "cancelled"
            pending.token_digest = ""
            pending.token_expires_at = 0.0
            claim = TaskApprovalClaim(
                approval_id=pending.approval_id,
                claim_id=f"claim-{secrets.token_hex(12)}",
                generation=self.generation,
                request=pending.request,
                stage_id=str(pending.stage["stageId"]),
                host_instance_id=str(pending.stage["hostInstanceId"]),
                base_sha256=str(pending.stage["baseSha256"]),
                candidate_sha256=str(pending.stage["candidateSha256"]),
                preview_digest=str(pending.stage["previewDigest"]),
                dirty_base_acknowledged=False,
            )
            pending.claim = claim
            self._resolve_locked(pending, TaskApprovalResolution("cancelled"))
            return claim


__all__ = [
    "TASK_APPROVAL_DEFAULT_TTL_SEC",
    "TASK_APPROVAL_PREVIEW_SCHEMA",
    "TASK_APPROVAL_PREVIEW_TTL_SEC",
    "TASK_APPROVAL_PUBLIC_SCHEMA",
    "TaskApprovalClaim",
    "TaskApprovalManager",
    "TaskApprovalRequest",
    "TaskApprovalResolution",
]
