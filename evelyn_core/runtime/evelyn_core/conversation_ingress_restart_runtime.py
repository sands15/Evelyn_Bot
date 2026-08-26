from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .conversation_memory_receipt import (
    sanitize_memory_receipt_ref,
)
from .conversation_ingress_recovery import (
    normalize_final_conversation_text,
)
from .session_memory_state import build_topic_id
from .text import clean_text


_DISCORD_SCOPE = re.compile(
    r"^guild:(?P<guild>\d+):text:\d+(?::thread:\d+)?"
    r":user:(?P<user>\d+)$"
)


@dataclass(frozen=True)
class ConversationIngressRestartDeps:
    session_state_store: Any
    session_continuity_checkpoint: Any
    system_prompt: str
    max_history_items: int
    normal_ttl_sec: float
    question_ttl_sec: float
    log: Callable[..., Any]


def _scope_actor_ids(scope: str) -> tuple[int | None, int | None]:
    match = _DISCORD_SCOPE.fullmatch(scope)
    if match is None:
        return None, None
    return int(match.group("guild")), int(match.group("user"))


def _recovered_awaiting_user_reply(
    record: dict[str, Any],
) -> bool:
    return str(record.get("sourceDeliveryId") or "").startswith(
        "autonomy:ping:"
    )


def _exact_history_tail(
    history: list[dict[str, Any]],
    *,
    user_text: str,
    assistant_text: str,
    memory_receipt_ref: Any,
) -> bool:
    if len(history) < 2:
        return False
    user_row, assistant_row = history[-2:]
    return bool(
        isinstance(user_row, dict)
        and isinstance(assistant_row, dict)
        and user_row.get("role") == "user"
        and assistant_row.get("role") == "assistant"
        and normalize_final_conversation_text(user_row.get("content"))
        == normalize_final_conversation_text(user_text)
        and normalize_final_conversation_text(
            assistant_row.get("content")
        )
        == normalize_final_conversation_text(assistant_text)
        and assistant_row.get("memoryReceiptRef") == memory_receipt_ref
    )


def _exact_user_only_tail(
    history: list[dict[str, Any]],
    *,
    user_text: str,
) -> bool:
    if not history:
        return False
    user_row = history[-1]
    return bool(
        isinstance(user_row, dict)
        and set(user_row) == {"role", "content"}
        and user_row.get("role") == "user"
        and normalize_final_conversation_text(user_row.get("content"))
        == normalize_final_conversation_text(user_text)
    )


def _verified_checkpoint_status(
    status: Any,
    *,
    generation: int,
) -> bool:
    if not isinstance(status, dict):
        return False
    raw_generation = status.get("checkpointGeneration")
    persisted = status.get("persistedSessionCount")
    restored = status.get("restoredSessionCount")
    if (
        isinstance(raw_generation, bool)
        or not isinstance(raw_generation, int)
        or raw_generation != generation
        or isinstance(persisted, bool)
        or not isinstance(persisted, int)
        or persisted < 0
        or isinstance(restored, bool)
        or not isinstance(restored, int)
        or restored < 0
    ):
        return False
    return bool(
        status.get("rollbackProtected") is True
        and status.get("checkpointIntegrity") == "verified"
        and status.get("checkpointHeadState") == "current"
        and max(persisted, restored) >= 1
        and _checkpoint_authenticity_verified(status)
    )


def _checkpoint_authenticity_verified(status: dict[str, Any]) -> bool:
    return bool(
        (
            status.get("keyedAuthenticity") is not True
            or (
                status.get("checkpointHeadAuthenticity") == "verified"
                and status.get("tamperEvident") is True
            )
        )
        and (
            status.get("externalAnchorConfigured") is not True
            or status.get("externalReplayProtected") is True
        )
    )


def _verified_checkpoint_predecessor(
    status: Any,
    *,
    generation: int,
) -> bool:
    if not isinstance(status, dict):
        return False
    raw_generation = status.get("checkpointGeneration")
    persisted = status.get("persistedSessionCount")
    restored = status.get("restoredSessionCount")
    if (
        isinstance(raw_generation, bool)
        or not isinstance(raw_generation, int)
        or raw_generation != generation
        or isinstance(persisted, bool)
        or not isinstance(persisted, int)
        or persisted < 0
        or isinstance(restored, bool)
        or not isinstance(restored, int)
        or restored < 0
        or not _checkpoint_authenticity_verified(status)
    ):
        return False
    if (
        status.get("rollbackProtected") is True
        and status.get("checkpointIntegrity") == "verified"
        and status.get("checkpointHeadState") == "current"
        and max(persisted, restored) >= 1
    ):
        return True
    if (
        status.get("rollbackProtected") is True
        and status.get("checkpointIntegrity") == "empty"
        and status.get("checkpointHeadState") == "empty"
        and persisted == 0
        and restored == 0
    ):
        return True
    return bool(
        generation == 0
        and status.get("state") == "missing"
        and status.get("rollbackProtected") is False
        and status.get("checkpointIntegrity") == "empty"
        and status.get("checkpointHeadState") == "missing"
        and persisted == 0
        and restored == 0
    )


def _capture_session_state(store: Any, scope: str) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name in getattr(type(store), "__dataclass_fields__", {}):
        mapping = getattr(store, name, None)
        if isinstance(mapping, dict):
            snapshot[name] = (
                scope in mapping,
                deepcopy(mapping.get(scope)),
            )
    return snapshot


def _restore_session_state(
    store: Any,
    scope: str,
    snapshot: dict[str, Any],
) -> None:
    for name, raw_state in snapshot.items():
        mapping = getattr(store, name, None)
        if not isinstance(mapping, dict):
            continue
        present, value = raw_state
        if present:
            mapping[scope] = value
        else:
            mapping.pop(scope, None)


def reconcile_recovered_delivery_succeeded(
    record: dict[str, Any],
    *,
    deps: ConversationIngressRestartDeps,
    before_commit: Callable[[int], Any] | None = None,
) -> int | None:
    scope = str(record.get("scope") or "")
    snapshot = _capture_session_state(
        deps.session_state_store,
        scope,
    )
    try:
        return _reconcile_recovered_delivery_succeeded_attempt(
            record,
            deps=deps,
            before_commit=before_commit,
        )
    except Exception:
        _restore_session_state(
            deps.session_state_store,
            scope,
            snapshot,
        )
        raise


def _reconcile_recovered_delivery_succeeded_attempt(
    record: dict[str, Any],
    *,
    deps: ConversationIngressRestartDeps,
    before_commit: Callable[[int], Any] | None,
) -> int | None:
    """Persist an already-delivered Discord turn without rerunning delivery."""

    if (
        record.get("surface") != "discord_text"
        or record.get("phase") != "delivery_succeeded"
    ):
        return None
    scope = str(record.get("scope") or "")
    turn_id = str(record.get("turnId") or "")
    user_text = str(record.get("acceptedText") or "")
    assistant_text = str(record.get("assistantText") or "")
    memory_ref = sanitize_memory_receipt_ref(
        record.get("memoryReceiptRef")
    )
    if (
        not all((scope, turn_id, user_text, assistant_text))
        or memory_ref is None
    ):
        return None
    store = deps.session_state_store
    current_turn = str(store.current_turn_id(scope) or "")
    history = store.get_conversation_history(
        system_prompt=deps.system_prompt,
        session_key=scope,
    )
    pair_persisted = current_turn == turn_id and _exact_history_tail(
        history,
        user_text=user_text,
        assistant_text=assistant_text,
        memory_receipt_ref=memory_ref,
    )
    user_only_persisted = (
        current_turn == turn_id
        and _exact_user_only_tail(
            history,
            user_text=user_text,
        )
    )
    if (
        current_turn == turn_id
        and not pair_persisted
        and not user_only_persisted
    ):
        return None
    if user_only_persisted:
        history.append(
            {
                "role": "assistant",
                "content": clean_text(assistant_text),
                "memoryReceiptRef": memory_ref,
            }
        )
        store.trim_history(
            system_prompt=deps.system_prompt,
            max_history_items=deps.max_history_items,
            session_key=scope,
        )
    elif not pair_persisted:
        if (
            history
            and history[-1].get("role") == "user"
            and normalize_final_conversation_text(
                history[-1].get("content")
            )
            == normalize_final_conversation_text(user_text)
        ):
            return None
        store.start_new_turn(scope, turn_id=turn_id)
        guild_id, _ = _scope_actor_ids(scope)
        store.append_history(
            scope,
            user_text,
            assistant_text,
            system_prompt=deps.system_prompt,
            max_history_items=deps.max_history_items,
            guild_id=guild_id,
            memory_receipt=memory_ref,
        )
    guild_id, user_id = _scope_actor_ids(scope)
    store.update_session_state(
        scope,
        user_id=user_id,
        speaker="assistant",
        awaiting_user_reply=(
            _recovered_awaiting_user_reply(record)
        ),
        topic_id=build_topic_id(user_text, assistant_text),
        answer_text=assistant_text,
        user_text=user_text,
        active_conversation_awaiting_reply_sec=(
            deps.question_ttl_sec
        ),
    )
    status = deps.session_continuity_checkpoint.commit_completed_turn(
        scope,
        turn_id,
        before_commit=before_commit,
    )
    receipt = require_durable_continuity_receipt(status)
    return int(receipt["generation"])


def verify_recovered_terminal_commit(
    record: dict[str, Any],
    *,
    deps: ConversationIngressRestartDeps,
) -> bool:
    """Verify exact restored turn and generation before terminal completion."""

    if (
        record.get("surface") != "discord_text"
        or record.get("phase") != "terminal_committing"
    ):
        return False
    scope = str(record.get("scope") or "")
    turn_id = str(record.get("turnId") or "")
    generation = int(record.get("continuityGeneration") or 0)
    store = deps.session_state_store
    if str(store.current_turn_id(scope) or "") != turn_id:
        return False
    history = store.get_conversation_history(
        system_prompt=deps.system_prompt,
        session_key=scope,
    )
    if not _exact_history_tail(
        history,
        user_text=str(record.get("acceptedText") or ""),
        assistant_text=str(record.get("assistantText") or ""),
        memory_receipt_ref=record.get("memoryReceiptRef"),
    ):
        return False
    return _verified_checkpoint_status(
        deps.session_continuity_checkpoint.status(),
        generation=generation,
    )


def reconcile_recovered_terminal_commit(
    record: dict[str, Any],
    *,
    deps: ConversationIngressRestartDeps,
) -> bool:
    """Finish or recommit one exact terminal marker after restart."""

    raw_generation = record.get("continuityGeneration")
    if (
        record.get("surface") != "discord_text"
        or record.get("phase") != "terminal_committing"
        or isinstance(raw_generation, bool)
        or not isinstance(raw_generation, int)
        or raw_generation < 1
    ):
        return False
    if verify_recovered_terminal_commit(record, deps=deps):
        return True
    predecessor_status = deps.session_continuity_checkpoint.status()
    if not _verified_checkpoint_predecessor(
        predecessor_status,
        generation=raw_generation - 1,
    ):
        return False
    scope = str(record.get("scope") or "")
    store_snapshot = _capture_session_state(
        deps.session_state_store,
        scope,
    )
    committed_generation = reconcile_recovered_delivery_succeeded(
        {**record, "phase": "delivery_succeeded"},
        deps=deps,
    )
    reconciled = bool(
        committed_generation == raw_generation
        and verify_recovered_terminal_commit(record, deps=deps)
    )
    if not reconciled:
        _restore_session_state(
            deps.session_state_store,
            scope,
            store_snapshot,
        )
    return reconciled


__all__ = [
    "ConversationIngressRestartDeps",
    "reconcile_recovered_delivery_succeeded",
    "reconcile_recovered_terminal_commit",
    "verify_recovered_terminal_commit",
]
