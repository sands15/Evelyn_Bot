from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
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
        and clean_text(str(user_row.get("content") or ""))
        == clean_text(user_text)
        and clean_text(str(assistant_row.get("content") or ""))
        == clean_text(assistant_text)
        and assistant_row.get("memoryReceiptRef") == memory_receipt_ref
    )


def _history_contains_exact_pair(
    history: list[dict[str, Any]],
    *,
    user_text: str,
    assistant_text: str,
    memory_receipt_ref: Any,
) -> bool:
    return any(
        _exact_history_tail(
            history[: index + 1],
            user_text=user_text,
            assistant_text=assistant_text,
            memory_receipt_ref=memory_receipt_ref,
        )
        for index in range(1, len(history))
    )


def _verified_checkpoint_status(
    status: Any,
    *,
    generation: int,
) -> bool:
    return bool(
        isinstance(status, dict)
        and status.get("rollbackProtected") is True
        and status.get("checkpointIntegrity") == "verified"
        and status.get("checkpointHeadState") == "current"
        and int(status.get("checkpointGeneration") or 0) == generation
        and int(status.get("persistedSessionCount") or 0) >= 1
        and (
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


def reconcile_recovered_delivery_succeeded(
    record: dict[str, Any],
    *,
    deps: ConversationIngressRestartDeps,
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
    memory_ref = record.get("memoryReceiptRef")
    if not all((scope, turn_id, user_text, assistant_text)):
        return None
    store = deps.session_state_store
    current_turn = str(store.current_turn_id(scope) or "")
    history = store.get_conversation_history(
        system_prompt=deps.system_prompt,
        session_key=scope,
    )
    pair_persisted = _history_contains_exact_pair(
        history,
        user_text=user_text,
        assistant_text=assistant_text,
        memory_receipt_ref=memory_ref,
    )
    if pair_persisted and current_turn and current_turn != turn_id:
        status = deps.session_continuity_checkpoint.status()
        generation = int(status.get("checkpointGeneration") or 0)
        return (
            generation
            if _verified_checkpoint_status(
                status,
                generation=generation,
            )
            else None
        )
    if not pair_persisted:
        if (
            history
            and history[-1].get("role") == "user"
            and clean_text(str(history[-1].get("content") or ""))
            == clean_text(user_text)
        ):
            return None
        store.start_new_turn(scope, turn_id=turn_id)
        guild_id, user_id = _scope_actor_ids(scope)
        store.finish_assistant_text_turn(
            scope,
            user_text,
            assistant_text,
            system_prompt=deps.system_prompt,
            max_history_items=deps.max_history_items,
            guild_id=guild_id,
            user_id=user_id,
            awaiting_user_reply=False,
            normal_ttl_sec=deps.normal_ttl_sec,
            question_ttl_sec=deps.question_ttl_sec,
            topic_id=build_topic_id(user_text, assistant_text),
            memory_receipt=memory_ref,
        )
    elif not current_turn:
        store.start_new_turn(scope, turn_id=turn_id)
    status = deps.session_continuity_checkpoint.commit_completed_turn(
        scope,
        turn_id,
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


__all__ = [
    "ConversationIngressRestartDeps",
    "reconcile_recovered_delivery_succeeded",
    "verify_recovered_terminal_commit",
]
