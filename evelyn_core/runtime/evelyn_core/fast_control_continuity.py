from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from .continuity_authenticity import (
    CONTINUITY_AUTH_SCOPE_FAST_CONTROL,
    ContinuityAuthenticity,
)
from .session_continuity import SessionContinuityCheckpoint
from .session_memory_state import (
    SessionStateStore,
    build_topic_id,
)
from .text import clean_text


FAST_CONTROL_CONTINUITY_STATUS_SCHEMA = (
    "fast_control.continuity-status.v1"
)
FAST_CONTROL_SESSION_KEY = "fast-control:control-page:owner"
FAST_CONTROL_SYSTEM_PROMPT = (
    "Evelyn fast-control short-lived conversation continuity"
)
DEFAULT_FAST_CONTROL_MAX_AGE_SEC = 30 * 60.0
DEFAULT_FAST_CONTROL_MAX_HISTORY_ITEMS = 40


class FastControlContinuityOwner:
    """Own a bounded checkpoint for the standalone Bot API conversation."""

    def __init__(
        self,
        *,
        artifacts_root: Path,
        enabled: bool,
        max_age_sec: float = DEFAULT_FAST_CONTROL_MAX_AGE_SEC,
        max_history_items: int = (
            DEFAULT_FAST_CONTROL_MAX_HISTORY_ITEMS
        ),
        wall_time: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        authenticity: ContinuityAuthenticity | None = None,
        log: Callable[..., Any] = print,
    ) -> None:
        self.enabled = bool(enabled)
        self.artifacts_root = Path(artifacts_root)
        self.max_age_sec = max(1.0, float(max_age_sec))
        self.max_history_items = max(
            2,
            int(max_history_items),
        )
        self.wall_time = wall_time
        self.monotonic = monotonic
        self.authenticity = (
            authenticity or ContinuityAuthenticity()
        )
        self.log = log
        self._lock = threading.RLock()
        self.store = SessionStateStore.create_empty()
        self.checkpoint: SessionContinuityCheckpoint | None = None
        self.restore_status: dict[str, Any] = {
            "state": "disabled",
        }
        if not self.enabled:
            return
        root = self.artifacts_root / "fast_control_continuity"
        self.checkpoint = SessionContinuityCheckpoint(
            store=self.store,
            checkpoint_path=root / "active.json",
            status_path=root / "status.json",
            system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
            max_age_sec=self.max_age_sec,
            max_sessions=1,
            max_history_items=self.max_history_items,
            wall_time=self.wall_time,
            monotonic=self.monotonic,
            authenticity=self.authenticity,
            authenticity_scope=(
                CONTINUITY_AUTH_SCOPE_FAST_CONTROL
            ),
            log=self.log,
        )
        self.restore_status = self.checkpoint.restore()

    def restored_chat_messages(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        history = self.store.histories.get(
            FAST_CONTROL_SESSION_KEY,
            [],
        )
        restored_at = float(
            self.restore_status.get("lastRestoredAt")
            or self.wall_time()
        )
        messages: list[dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = clean_text(item.get("role")).lower()
            content = clean_text(item.get("content"))
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append(
                {
                    "role": role,
                    "author": (
                        "정훈"
                        if role == "user"
                        else "Evelyn"
                    ),
                    "text": content,
                    "at": restored_at,
                    "source": (
                        "fast_control_continuity_restore"
                    ),
                }
            )
        return messages[-self.max_history_items :]

    def _require_checkpoint(
        self,
    ) -> SessionContinuityCheckpoint:
        if not self.enabled or self.checkpoint is None:
            raise RuntimeError("fast_control_continuity_disabled")
        return self.checkpoint

    def record_completed_turn(
        self,
        user_text: str,
        assistant_text: str,
        *,
        before_commit: Callable[[int], Any] | None = None,
    ) -> dict[str, Any]:
        cleaned_user = clean_text(user_text)
        cleaned_assistant = clean_text(assistant_text)
        if not cleaned_user or not cleaned_assistant:
            raise ValueError("fast_control_turn_empty")
        with self._lock:
            checkpoint = self._require_checkpoint()
            current_generation = max(
                0,
                int(
                    checkpoint.status().get(
                        "checkpointGeneration"
                    )
                    or 0
                ),
            )
            if before_commit is not None:
                before_commit(current_generation + 1)
            turn_id = self.store.start_new_turn(
                FAST_CONTROL_SESSION_KEY,
                now_monotonic=self.monotonic(),
            )
            self.store.finish_assistant_text_turn(
                FAST_CONTROL_SESSION_KEY,
                cleaned_user,
                cleaned_assistant,
                system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                max_history_items=self.max_history_items,
                awaiting_user_reply=False,
                normal_ttl_sec=self.max_age_sec,
                question_ttl_sec=self.max_age_sec,
                topic_id=build_topic_id(
                    cleaned_user,
                    cleaned_assistant,
                ),
                now_monotonic=self.monotonic(),
            )
            return checkpoint.commit_completed_turn(
                FAST_CONTROL_SESSION_KEY,
                turn_id,
            )

    def record_assistant_followup(
        self,
        assistant_text: str,
        *,
        before_commit: Callable[[int], Any] | None = None,
    ) -> dict[str, Any]:
        cleaned_assistant = clean_text(assistant_text)
        if not cleaned_assistant:
            raise ValueError("fast_control_followup_empty")
        with self._lock:
            checkpoint = self._require_checkpoint()
            current_generation = max(
                0,
                int(
                    checkpoint.status().get(
                        "checkpointGeneration"
                    )
                    or 0
                ),
            )
            if before_commit is not None:
                before_commit(current_generation + 1)
            turn_id = self.store.start_new_turn(
                FAST_CONTROL_SESSION_KEY,
                now_monotonic=self.monotonic(),
            )
            history = self.store.get_conversation_history(
                system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                session_key=FAST_CONTROL_SESSION_KEY,
            )
            history.append(
                {
                    "role": "assistant",
                    "content": cleaned_assistant,
                }
            )
            self.store.trim_history(
                system_prompt=FAST_CONTROL_SYSTEM_PROMPT,
                max_history_items=self.max_history_items,
                session_key=FAST_CONTROL_SESSION_KEY,
            )
            self.store.mark_active(
                FAST_CONTROL_SESSION_KEY,
                ttl_sec=self.max_age_sec,
                speaker="assistant",
                awaiting_user_reply=False,
                topic_id=build_topic_id(cleaned_assistant),
                answer_text=cleaned_assistant,
                active_conversation_awaiting_reply_sec=(
                    self.max_age_sec
                ),
                now_monotonic=self.monotonic(),
            )
            return checkpoint.commit_completed_turn(
                FAST_CONTROL_SESSION_KEY,
                turn_id,
            )

    def status(self) -> dict[str, Any]:
        if not self.enabled or self.checkpoint is None:
            return {
                "schema": FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
                "enabled": False,
                "state": "disabled",
                "durableReady": False,
                "generation": 0,
                "persistedSessionCount": 0,
                "messageCount": 0,
                "lastErrorCode": "",
                "keyedAuthenticity": False,
                "tamperEvident": False,
                "policy": {"contentFree": True},
            }
        raw = self.checkpoint.status()
        return {
            "schema": FAST_CONTROL_CONTINUITY_STATUS_SCHEMA,
            "enabled": True,
            "state": clean_text(raw.get("state")) or "unknown",
            "durableReady": bool(
                raw.get("rollbackProtected") is True
                and raw.get("checkpointIntegrity") == "verified"
                and raw.get("checkpointHeadState") == "current"
                and (
                    raw.get("keyedAuthenticity") is not True
                    or raw.get("tamperEvident") is True
                )
            ),
            "generation": max(
                0,
                int(raw.get("checkpointGeneration") or 0),
            ),
            "persistedSessionCount": max(
                0,
                int(raw.get("persistedSessionCount") or 0),
            ),
            "messageCount": len(
                self.restored_chat_messages()
            ),
            "lastErrorCode": clean_text(
                raw.get("lastErrorCode")
            ),
            "keyedAuthenticity": bool(
                raw.get("keyedAuthenticity")
            ),
            "tamperEvident": bool(
                raw.get("tamperEvident")
            ),
            "policy": {"contentFree": True},
        }


__all__ = [
    "DEFAULT_FAST_CONTROL_MAX_AGE_SEC",
    "DEFAULT_FAST_CONTROL_MAX_HISTORY_ITEMS",
    "FAST_CONTROL_CONTINUITY_STATUS_SCHEMA",
    "FAST_CONTROL_SESSION_KEY",
    "FastControlContinuityOwner",
]
