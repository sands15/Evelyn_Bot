from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .conversation_memory_exposure import (
    capture_combined_memory_exposure,
    filter_conversation_history_for_memory_exposure,
)
from .conversation_memory_receipt import (
    memory_receipt_ref_from_receipt,
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from .memory_exposure import current_memory_exposure_position
from .text import clean_text


def runtime_session_key(*, session_key: str | None = None, guild_id: int | None = None) -> str | None:
    if session_key:
        return session_key
    if guild_id is None:
        return None
    return f"guild:{guild_id}:default"


def new_conversation_history(system_prompt: str) -> list[dict]:
    return [{"role": "system", "content": system_prompt}]


def build_topic_id(*texts: str) -> str:
    material = "\n".join(clean_text(text) for text in texts if clean_text(text))
    if not material:
        material = "idle"
    return hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:12]


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def is_casual_call_or_status_question(text: str) -> bool:
    cleaned = clean_text(text).lower()
    if not cleaned:
        return True
    stripped = re.sub(r"[\s,.!?~]+", "", cleaned)
    if stripped in {"evelyn", "이블린", "이브"}:
        return True
    return any(marker in cleaned for marker in ("뭐해", "뭐하고", "뭐 하는", "불러", "괜찮"))


@dataclass(slots=True)
class UserTextTurnStart:
    turn_id: str
    topic_id: str
    history: list[dict]


@dataclass(slots=True)
class AssistantTextTurnFinish:
    awaiting_user_reply: bool
    ttl_sec: float


@dataclass(slots=True)
class SessionStateStore:
    histories: dict[str, list[dict]]
    followup_targets: dict[str, dict[str, int]]
    active_until: dict[str, float]
    active_user_ids: dict[str, int]
    last_active_at: dict[str, float]
    awaiting_user_reply: dict[str, bool]
    last_speaker: dict[str, str]
    topic_ids: dict[str, str]
    turn_ids: dict[str, str]
    segment_counters: dict[str, int]
    last_turn_accepted_at: dict[str, float]
    last_stt_text: dict[str, str]
    partial_stt_text: dict[str, str]
    committed_stt_text: dict[str, str]
    bad_audio_counts: dict[str, int]

    @classmethod
    def create_empty(cls) -> "SessionStateStore":
        return cls(
            histories={},
            followup_targets={},
            active_until={},
            active_user_ids={},
            last_active_at={},
            awaiting_user_reply={},
            last_speaker={},
            topic_ids={},
            turn_ids={},
            segment_counters={},
            last_turn_accepted_at={},
            last_stt_text={},
            partial_stt_text={},
            committed_stt_text={},
            bad_audio_counts={},
        )

    def remember_followup_target(
        self,
        session_key: str,
        *,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        if channel_id is None and message_id is None:
            return
        existing = self.followup_targets.get(session_key, {}).copy()
        if channel_id is not None:
            existing["channel_id"] = channel_id
        if message_id is not None:
            existing["message_id"] = message_id
        self.followup_targets[session_key] = existing

    def current_turn_id(self, session_key: str | None) -> str | None:
        if not session_key:
            return None
        return self.turn_ids.get(session_key)

    def next_segment_id(self, session_key: str | None) -> int:
        if not session_key:
            return 1
        next_value = self.segment_counters.get(session_key, 0) + 1
        self.segment_counters[session_key] = next_value
        return next_value

    def start_new_turn(
        self,
        session_key: str | None,
        *,
        turn_id: str | None = None,
        now_monotonic: float | None = None,
    ) -> str:
        turn_id = turn_id or new_turn_id()
        if session_key:
            self.turn_ids[session_key] = turn_id
            self.last_turn_accepted_at[session_key] = time.monotonic() if now_monotonic is None else float(now_monotonic)
        return turn_id

    def snapshot(self, session_key: str | None) -> dict:
        if not session_key:
            return {}
        return {
            "active_until": self.active_until.get(session_key, 0.0),
            "awaiting_user_reply": self.awaiting_user_reply.get(session_key, False),
            "last_speaker": self.last_speaker.get(session_key, ""),
            "topic_id": self.topic_ids.get(session_key, ""),
            "turn_id": self.turn_ids.get(session_key, ""),
            "last_turn_accepted_at": self.last_turn_accepted_at.get(session_key, 0.0),
            "last_stt_text": self.last_stt_text.get(session_key, ""),
            "partial_stt_text": self.partial_stt_text.get(session_key, ""),
            "committed_stt_text": self.committed_stt_text.get(session_key, ""),
            "bad_audio_count": self.bad_audio_counts.get(session_key, 0),
        }

    def increment_bad_audio(self, session_key: str | None) -> int:
        if not session_key:
            return 0
        count = self.bad_audio_counts.get(session_key, 0) + 1
        self.bad_audio_counts[session_key] = count
        return count

    def reset_bad_audio(self, session_key: str | None) -> None:
        if not session_key:
            return
        self.bad_audio_counts[session_key] = 0

    def update_session_state(
        self,
        session_key: str | None,
        *,
        user_id: int | None = None,
        speaker: str | None = None,
        ttl_sec: float | None = None,
        awaiting_user_reply: bool | None = None,
        topic_id: str | None = None,
        answer_text: str | None = None,
        user_text: str | None = None,
        active_conversation_awaiting_reply_sec: float,
        now_monotonic: float | None = None,
    ) -> None:
        if not session_key:
            return
        now_mono = time.monotonic() if now_monotonic is None else float(now_monotonic)
        if ttl_sec is not None:
            self.active_until[session_key] = now_mono + ttl_sec
        self.last_active_at[session_key] = now_mono
        if user_id is not None:
            self.active_user_ids[session_key] = user_id
        if speaker is not None:
            self.last_speaker[session_key] = speaker
        if awaiting_user_reply is not None:
            self.awaiting_user_reply[session_key] = awaiting_user_reply
            if awaiting_user_reply and ttl_sec is None:
                self.active_until[session_key] = now_mono + active_conversation_awaiting_reply_sec
        if topic_id:
            self.topic_ids[session_key] = topic_id
        elif user_text or answer_text:
            self.topic_ids[session_key] = build_topic_id(user_text or "", answer_text or "")
        if session_key not in self.turn_ids:
            self.turn_ids[session_key] = new_turn_id()

    def mark_active(
        self,
        session_key: str,
        *,
        user_id: int | None = None,
        ttl_sec: float = 90.0,
        speaker: str = "assistant",
        awaiting_user_reply: bool | None = None,
        topic_id: str | None = None,
        answer_text: str | None = None,
        user_text: str | None = None,
        active_conversation_awaiting_reply_sec: float,
        now_monotonic: float | None = None,
    ) -> None:
        self.update_session_state(
            session_key,
            user_id=user_id,
            speaker=speaker,
            ttl_sec=ttl_sec,
            awaiting_user_reply=awaiting_user_reply,
            topic_id=topic_id,
            answer_text=answer_text,
            user_text=user_text,
            active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
            now_monotonic=now_monotonic,
        )

    def begin_user_only_turn(
        self,
        session_key: str,
        user_text: str,
        *,
        turn_id: str,
        system_prompt: str,
        max_history_items: int,
        user_id: int | None,
        ttl_sec: float,
        topic_id: str,
        active_conversation_awaiting_reply_sec: float,
        now_monotonic: float | None = None,
    ) -> str:
        history = self.get_conversation_history(
            system_prompt=system_prompt,
            session_key=session_key,
        )
        if self.current_turn_id(session_key) == turn_id:
            user_row = history[-1] if history else None
            if (
                isinstance(user_row, dict)
                and set(user_row) == {"role", "content"}
                and user_row.get("role") == "user"
                and clean_text(str(user_row.get("content") or ""))
                == clean_text(user_text)
            ):
                return turn_id
            raise ValueError("conversation_history_turn_mismatch")
        self.start_new_turn(
            session_key,
            turn_id=turn_id,
            now_monotonic=now_monotonic,
        )
        self.update_session_state(
            session_key,
            user_id=user_id,
            speaker="user",
            ttl_sec=ttl_sec,
            awaiting_user_reply=False,
            topic_id=topic_id,
            user_text=user_text,
            active_conversation_awaiting_reply_sec=(
                active_conversation_awaiting_reply_sec
            ),
            now_monotonic=now_monotonic,
        )
        self.append_history(
            session_key,
            user_text,
            None,
            system_prompt=system_prompt,
            max_history_items=max_history_items,
        )
        return turn_id

    def begin_user_text_turn(
        self,
        session_key: str,
        user_text: str,
        *,
        system_prompt: str,
        active_conversation_awaiting_reply_sec: float,
        max_history_items: int,
        guild_id: int | None = None,
        user_id: int | None = None,
        previous_topic_id: str = "",
        turn_id: str | None = None,
        now_monotonic: float | None = None,
    ) -> UserTextTurnStart:
        topic_id = build_topic_id(user_text, previous_topic_id)
        turn_id = self.start_new_turn(
            session_key,
            turn_id=turn_id,
            now_monotonic=now_monotonic,
        )
        self.update_session_state(
            session_key,
            user_id=user_id,
            speaker="user",
            awaiting_user_reply=False,
            topic_id=topic_id,
            user_text=user_text,
            active_conversation_awaiting_reply_sec=active_conversation_awaiting_reply_sec,
            now_monotonic=now_monotonic,
        )
        history = self.get_conversation_history(system_prompt=system_prompt, session_key=session_key, guild_id=guild_id)
        self.trim_history(
            system_prompt=system_prompt,
            max_history_items=max_history_items,
            session_key=session_key,
            guild_id=guild_id,
        )
        return UserTextTurnStart(turn_id=turn_id, topic_id=topic_id, history=history)

    def finish_assistant_text_turn(
        self,
        session_key: str,
        user_text: str,
        answer_text: str,
        *,
        system_prompt: str,
        max_history_items: int,
        guild_id: int | None = None,
        user_id: int | None = None,
        awaiting_user_reply: bool,
        normal_ttl_sec: float,
        question_ttl_sec: float,
        topic_id: str | None = None,
        now_monotonic: float | None = None,
        memory_receipt: Any = None,
    ) -> AssistantTextTurnFinish:
        ttl_sec = float(question_ttl_sec if awaiting_user_reply else normal_ttl_sec)
        self.append_history(
            session_key,
            user_text,
            answer_text,
            system_prompt=system_prompt,
            max_history_items=max_history_items,
            guild_id=guild_id,
            memory_receipt=memory_receipt,
        )
        self.mark_active(
            session_key,
            user_id=user_id,
            ttl_sec=ttl_sec,
            speaker="assistant",
            awaiting_user_reply=awaiting_user_reply,
            topic_id=topic_id,
            answer_text=answer_text,
            user_text=user_text,
            active_conversation_awaiting_reply_sec=question_ttl_sec,
            now_monotonic=now_monotonic,
        )
        return AssistantTextTurnFinish(awaiting_user_reply=bool(awaiting_user_reply), ttl_sec=ttl_sec)

    def record_command_assistant_turn(
        self,
        session_key: str,
        user_text: str,
        answer_text: str,
        *,
        system_prompt: str,
        max_history_items: int,
        guild_id: int | None,
        user_id: int | None,
        channel_id: int | None = None,
        message_id: int | None = None,
        awaiting_user_reply: bool = False,
        normal_ttl_sec: float,
        question_ttl_sec: float,
        now_monotonic: float | None = None,
    ) -> AssistantTextTurnFinish:
        self.remember_followup_target(session_key, channel_id=channel_id, message_id=message_id)
        return self.finish_assistant_text_turn(
            session_key,
            user_text,
            answer_text,
            system_prompt=system_prompt,
            max_history_items=max_history_items,
            guild_id=guild_id,
            user_id=user_id,
            awaiting_user_reply=awaiting_user_reply,
            normal_ttl_sec=normal_ttl_sec,
            question_ttl_sec=question_ttl_sec,
            topic_id=build_topic_id(user_text, answer_text),
            memory_receipt=not_used_memory_receipt_ref(),
            now_monotonic=now_monotonic,
        )

    def record_tool_assistant_turn(
        self,
        session_key: str,
        user_text: str,
        reply_text: str,
        *,
        tool_name: str,
        system_prompt: str,
        max_history_items: int,
        guild_id: int | None,
        ttl_sec: float,
        memory_receipt: Any = None,
        now_monotonic: float | None = None,
    ) -> AssistantTextTurnFinish:
        cleaned_tool = clean_text(tool_name)
        history_answer = f"도구 실행: {cleaned_tool}\n결과: {clean_text(reply_text)}"
        self.append_history(
            session_key,
            user_text,
            history_answer,
            system_prompt=system_prompt,
            max_history_items=max_history_items,
            guild_id=guild_id,
            memory_receipt=memory_receipt,
        )
        self.mark_active(
            session_key,
            ttl_sec=ttl_sec,
            speaker="assistant",
            awaiting_user_reply=False,
            topic_id=build_topic_id(user_text, cleaned_tool, reply_text),
            answer_text=reply_text,
            user_text=user_text,
            active_conversation_awaiting_reply_sec=ttl_sec,
            now_monotonic=now_monotonic,
        )
        return AssistantTextTurnFinish(awaiting_user_reply=False, ttl_sec=float(ttl_sec))

    def is_active_for_user(
        self,
        session_key: str,
        user_id: int | None = None,
        *,
        now_monotonic: float | None = None,
    ) -> bool:
        expires_at = self.active_until.get(session_key, 0.0)
        now_mono = time.monotonic() if now_monotonic is None else float(now_monotonic)
        if expires_at <= now_mono:
            return False
        remembered_user = self.active_user_ids.get(session_key)
        if remembered_user is not None and user_id is not None and remembered_user != user_id:
            return False
        return True

    def get_conversation_history(
        self,
        *,
        system_prompt: str,
        session_key: str | None = None,
        guild_id: int | None = None,
    ) -> list[dict]:
        resolved = runtime_session_key(session_key=session_key, guild_id=guild_id)
        if resolved is None:
            return new_conversation_history(system_prompt)
        return self.histories.setdefault(resolved, new_conversation_history(system_prompt))

    def trim_history(
        self,
        *,
        system_prompt: str,
        max_history_items: int,
        session_key: str | None = None,
        guild_id: int | None = None,
    ) -> None:
        history = self.get_conversation_history(system_prompt=system_prompt, session_key=session_key, guild_id=guild_id)
        if len(history) > 1 + max_history_items:
            del history[1:-max_history_items]

    def append_history(
        self,
        session_key: str | None,
        user_text: str,
        answer: str | None,
        *,
        system_prompt: str,
        max_history_items: int,
        guild_id: int | None = None,
        memory_receipt: Any = None,
        complete_turn_id: str | None = None,
    ) -> None:
        resolved = runtime_session_key(
            session_key=session_key,
            guild_id=guild_id,
        )
        history = self.get_conversation_history(
            system_prompt=system_prompt,
            session_key=session_key,
            guild_id=guild_id,
        )
        rows: list[dict[str, Any]] = []
        if complete_turn_id is not None:
            user_row = history[-1] if history else None
            if answer is None or resolved is None:
                raise ValueError("conversation_history_turn_mismatch")
            reply_receipt = (
                unattributed_memory_receipt_ref()
                if memory_receipt is None
                else memory_receipt_ref_from_receipt(memory_receipt)
            )
            exact_user_tail = bool(
                isinstance(user_row, dict)
                and set(user_row) == {"role", "content"}
                and user_row.get("role") == "user"
                and clean_text(str(user_row.get("content") or ""))
                == clean_text(user_text)
            )
            exact_pair_tail = bool(
                len(history) >= 2
                and isinstance(history[-2], dict)
                and isinstance(history[-1], dict)
                and set(history[-2]) == {"role", "content"}
                and set(history[-1])
                == {"role", "content", "memoryReceiptRef"}
                and history[-2].get("role") == "user"
                and clean_text(str(history[-2].get("content") or ""))
                == clean_text(user_text)
                and history[-1].get("role") == "assistant"
                and clean_text(str(history[-1].get("content") or ""))
                == clean_text(answer)
                and history[-1].get("memoryReceiptRef") == reply_receipt
            )
            if self.turn_ids.get(resolved) != complete_turn_id:
                raise ValueError("conversation_history_turn_mismatch")
            if exact_pair_tail:
                return
            if not exact_user_tail:
                raise ValueError("conversation_history_turn_mismatch")
        else:
            rows.append({"role": "user", "content": clean_text(user_text)})
        if answer is not None:
            rows.append(
                {
                    "role": "assistant",
                    "content": clean_text(answer),
                    "memoryReceiptRef": (
                        unattributed_memory_receipt_ref()
                        if memory_receipt is None
                        else memory_receipt_ref_from_receipt(
                            memory_receipt
                        )
                    ),
                }
            )
        history.extend(rows)
        self.trim_history(
            system_prompt=system_prompt,
            max_history_items=max_history_items,
            session_key=session_key,
            guild_id=guild_id,
        )

    def recent_assistant_reply_summary(
        self,
        *,
        system_prompt: str,
        memory_index_dir: Path,
        session_key: str | None = None,
        guild_id: int | None = None,
        limit: int = 1,
    ) -> str:
        history = self.get_conversation_history(
            system_prompt=system_prompt,
            session_key=session_key,
            guild_id=guild_id,
        )
        history_outcome = filter_conversation_history_for_memory_exposure(
            history,
            memory_index_dir=Path(memory_index_dir),
        )
        capture_combined_memory_exposure(
            current_memory_exposure_position(),
            history_outcome.memory_exposure_position,
        )
        replies: list[str] = []
        for item in reversed(history_outcome.messages):
            if not isinstance(item, dict) or item.get("role") != "assistant":
                continue
            content = clean_text(str(item.get("content") or ""))
            if not content:
                continue
            replies.append(content[:60])
            if len(replies) >= limit:
                break
        replies.reverse()
        return " / ".join(replies)

    def recent_history_for_router(
        self,
        *,
        system_prompt: str,
        session_key: str | None = None,
        guild_id: int | None = None,
        limit: int = 6,
        max_content_chars: int = 180,
    ) -> str:
        history = self.get_conversation_history(system_prompt=system_prompt, session_key=session_key, guild_id=guild_id)
        lines: list[str] = []
        for row in history[-limit:]:
            if not isinstance(row, dict):
                continue
            role = clean_text(str(row.get("role") or ""))
            content = clean_text(str(row.get("content") or ""))
            if role and content:
                lines.append(f"{role}: {content[:max_content_chars]}")
        return "\n".join(lines)

    def persona_state_hint_for_turn(
        self,
        user_text: str,
        *,
        system_prompt: str,
        memory_index_dir: Path,
        session_key: str | None = None,
        guild_id: int | None = None,
    ) -> str:
        if not is_casual_call_or_status_question(user_text):
            return ""
        recent = self.recent_assistant_reply_summary(
            system_prompt=system_prompt,
            memory_index_dir=memory_index_dir,
            session_key=session_key,
            guild_id=guild_id,
            limit=4,
        )
        guidance = [
            "호출/근황 질문. 실제 진행 중인 작업이나 사용자가 맡긴 일이 있으면 먼저 그것을 짧게 말해라.",
            "실제 작업이 없으면 Evelyn self model의 ambient_idle_activity label을 생활감 있는 현재 상태처럼 자연스럽게 사용해라.",
            "ambient 상태는 대화용 지속 상태다. 실제 화면을 봤다, 음악을 재생했다, 웹을 열었다, 물리적으로 조작했다처럼 도구 사용을 꾸며 말하지 마라.",
            "같은 상태나 같은 수습 문장을 반복하지 말고, 사용자가 이어서 물어볼 만한 작은 소재를 한 가지 남겨라.",
        ]
        if recent:
            guidance.append(f"최근 네 말: {recent}. 같은 표현을 반복하지 말고 이어지는 느낌으로 말해라.")
        return " ".join(guidance)


__all__ = [
    "SessionStateStore",
    "build_topic_id",
    "is_casual_call_or_status_question",
    "new_conversation_history",
    "new_turn_id",
    "runtime_session_key",
]
