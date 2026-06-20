from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Callable

from .observability_metrics import summarize_question_metrics_payload
from .proactive_questions import (
    evaluate_proactive_question_gate,
    mark_question_asked,
    resolve_pending_question_answer,
    select_question_to_ask,
    should_offer_proactive_question,
)
from .text import clean_text
from .voice_pipeline import RouteDecision


QUESTION_ASK_MODES = {
    "none",
    "clarify",
    "soft_followup",
    "preference_probe",
    "topic_continue",
    "idle_checkin",
}


def default_question_metrics() -> dict[str, Any]:
    return {
        "turn_count": 0,
        "added_count": 0,
        "removed_count": 0,
        "cooldown_hit_count": 0,
        "final_question_count": 0,
        "ask_modes": {},
    }


def default_session_question_state() -> dict[str, Any]:
    return {"turn_index": 0, "question_turns": [], "frustration_until": 0.0}


def normalize_question_policy_mapping(value: dict[str, Any] | None, *, default_source: str = "none") -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    ask_mode = clean_text(str(data.get("ask_mode") or "none")).lower()
    if ask_mode not in QUESTION_ASK_MODES:
        ask_mode = "none"
    try:
        max_question_count = int(data.get("max_question_count") or 0)
    except (TypeError, ValueError):
        max_question_count = 0
    if ask_mode == "none":
        max_question_count = 0
    else:
        max_question_count = max(0, min(1, max_question_count or 1))
    question_source = clean_text(str(data.get("question_source") or default_source or "none")).lower() or "none"
    return {
        "ask_mode": ask_mode,
        "max_question_count": max_question_count,
        "question_hint": clean_text(str(data.get("question_hint") or "")) or None,
        "question_reason": clean_text(str(data.get("question_reason") or "")) or None,
        "question_source": question_source,
    }


def extract_question_policy_from_route_meta(route_meta: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(route_meta, dict):
        return normalize_question_policy_mapping(None)
    return normalize_question_policy_mapping(
        {
            "ask_mode": route_meta.get("ask_mode"),
            "max_question_count": route_meta.get("max_question_count"),
            "question_hint": route_meta.get("question_hint"),
            "question_reason": route_meta.get("question_reason"),
            "question_source": route_meta.get("question_source") or route_meta.get("source"),
        },
        default_source=clean_text(str(route_meta.get("source") or "none")),
    )


def user_wants_direct_answer(text: str) -> bool:
    cleaned = clean_text(text).lower()
    if not cleaned:
        return False
    direct_markers = (
        "정답만",
        "답만",
        "질문하지",
        "묻지 말",
        "되묻지",
        "그냥 답",
        "바로 답",
        "짧게 답",
        "한 문장",
        "완료 보고",
        "보고만",
        "여부만",
        "only answer",
        "just answer",
        "no question",
        "don't ask",
        "do not ask",
    )
    return any(marker in cleaned for marker in direct_markers)


def user_frustration_with_questions(text: str) -> bool:
    cleaned = clean_text(text).lower()
    markers = (
        "질문 그만",
        "그만 물어",
        "왜 자꾸 물어",
        "귀찮",
        "짜증",
        "답답",
        "stop asking",
        "too many questions",
    )
    return any(marker in cleaned for marker in markers)


def is_continuable_technical_topic(text: str) -> bool:
    cleaned = clean_text(text).lower()
    if not cleaned or user_wants_direct_answer(cleaned):
        return False
    markers = (
        "설계",
        "구조",
        "구현",
        "리스크",
        "영향",
        "파이프라인",
        "router",
        "라우터",
        "llm",
        "hot path",
        "핫패스",
        "metric",
        "메트릭",
        "control page",
        "컨트롤",
        "디스코드",
        "로컬모드",
        "다음 단계",
        "어떻게",
        "왜",
    )
    return any(marker in cleaned for marker in markers) and len(cleaned) >= 18


@dataclass(slots=True)
class QuestionPolicyState:
    question_metrics: dict[str, Any]
    session_question_state: dict[str, dict[str, Any]]
    log_turn_event: Callable[..., Any]
    question_feature_enabled: bool
    min_turn_gap: int
    min_seconds_gap: float
    max_per_10_turns: int
    disable_after_frustration_sec: float

    def normalize_policy_mapping(self, value: dict[str, Any] | None, *, default_source: str = "none") -> dict[str, Any]:
        return normalize_question_policy_mapping(value, default_source=default_source)

    def extract_policy_from_route_meta(self, route_meta: dict[str, Any] | None) -> dict[str, Any]:
        return extract_question_policy_from_route_meta(route_meta)

    def question_cooldown_hit(self, session_key: str | None, *, now: float | None = None) -> bool:
        if not session_key:
            return False
        state = self.session_question_state.setdefault(session_key, default_session_question_state())
        current_time = time.monotonic() if now is None else float(now)
        if current_time < float(state.get("frustration_until", 0.0) or 0.0):
            return True
        question_turns = [int(item) for item in state.get("question_turns", []) if isinstance(item, int)]
        current_turn = int(state.get("turn_index", 0))
        if question_turns and current_turn - question_turns[-1] < self.min_turn_gap:
            return True
        last_question_at = float(state.get("last_question_at", 0.0) or 0.0)
        if last_question_at and current_time - last_question_at < self.min_seconds_gap:
            return True
        recent = [turn for turn in question_turns if current_turn - turn < 10]
        return self.max_per_10_turns > 0 and len(recent) >= self.max_per_10_turns

    def apply_fast_path_policy(
        self,
        route_decision: RouteDecision,
        *,
        user_text: str,
        session_key: str | None,
        route_meta_question_policy: dict[str, Any] | None = None,
    ) -> tuple[RouteDecision, bool]:
        if not self.question_feature_enabled:
            policy = normalize_question_policy_mapping({"question_reason": "question_feature_disabled"})
            return replace(route_decision, **policy), False

        route_policy = normalize_question_policy_mapping(route_meta_question_policy, default_source="router")
        if route_policy["ask_mode"] != "none":
            return replace(route_decision, **route_policy), False

        state = self.session_question_state.setdefault(session_key or "global", default_session_question_state())
        state["turn_index"] = int(state.get("turn_index", 0)) + 1
        if user_frustration_with_questions(user_text):
            state["frustration_until"] = time.monotonic() + self.disable_after_frustration_sec

        if user_wants_direct_answer(user_text):
            policy = normalize_question_policy_mapping(
                {"ask_mode": "none", "question_reason": "direct_answer_requested", "question_source": "fast_path"}
            )
            return replace(route_decision, **policy), False

        cooldown = self.question_cooldown_hit(session_key or "global")
        if cooldown:
            policy = normalize_question_policy_mapping(
                {"ask_mode": "none", "question_reason": "question_cooldown", "question_source": "fast_path"}
            )
            return replace(route_decision, **policy), True

        if route_decision.action in {"ask", "wait"}:
            policy = normalize_question_policy_mapping(
                {
                    "ask_mode": "clarify",
                    "max_question_count": 1,
                    "question_hint": route_decision.prompt_text,
                    "question_reason": "route_action_requires_clarification",
                    "question_source": "fast_path",
                }
            )
            return replace(route_decision, **policy), False

        if route_decision.route in {"search_executor"} or route_decision.needs_search:
            policy = normalize_question_policy_mapping(
                {"ask_mode": "none", "question_reason": "search_or_action_turn", "question_source": "fast_path"}
            )
            return replace(route_decision, **policy), False

        if is_continuable_technical_topic(user_text):
            policy = normalize_question_policy_mapping(
                {
                    "ask_mode": "topic_continue",
                    "max_question_count": 1,
                    "question_hint": "Ask lightly what subsystem or next step the user wants to tune next.",
                    "question_reason": "fast_path_topic_continue",
                    "question_source": "fast_path",
                }
            )
            return replace(route_decision, **policy), False

        policy = normalize_question_policy_mapping(
            {"ask_mode": "none", "question_reason": "default_no_question", "question_source": "fast_path"}
        )
        return replace(route_decision, **policy), False

    def record_question_trace(
        self,
        *,
        route_decision: RouteDecision,
        answer: str,
        shape_meta: dict[str, Any],
        metrics: dict | None,
        cooldown_hit: bool = False,
    ) -> None:
        final_question_count = int(shape_meta.get("question_count_after", 0) or 0)
        question_added = final_question_count > 0
        question_removed = bool(shape_meta.get("question_removed"))
        self.question_metrics["turn_count"] = int(self.question_metrics.get("turn_count", 0)) + 1
        if question_added:
            self.question_metrics["added_count"] = int(self.question_metrics.get("added_count", 0)) + 1
        if question_removed:
            self.question_metrics["removed_count"] = int(self.question_metrics.get("removed_count", 0)) + 1
        if cooldown_hit:
            self.question_metrics["cooldown_hit_count"] = int(self.question_metrics.get("cooldown_hit_count", 0)) + 1
        self.question_metrics["final_question_count"] = int(self.question_metrics.get("final_question_count", 0)) + final_question_count
        ask_modes = self.question_metrics.setdefault("ask_modes", {})
        ask_modes[route_decision.ask_mode] = int(ask_modes.get(route_decision.ask_mode, 0)) + 1

        meta = (metrics or {}).get("meta") if isinstance(metrics, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        session_key = clean_text(str(meta.get("session_key") or ""))
        if question_added and session_key:
            state = self.session_question_state.setdefault(session_key, default_session_question_state())
            turn_index = int(state.get("turn_index", 0))
            turns = [int(item) for item in state.get("question_turns", []) if isinstance(item, int)]
            turns.append(turn_index)
            state["question_turns"] = turns[-10:]
            state["last_question_at"] = time.monotonic()

        self.log_turn_event(
            "question_trace",
            turn_id=meta.get("turn_id"),
            session_key=meta.get("session_key"),
            source=meta.get("source"),
            guild_id=meta.get("guild_id"),
            ask_mode=route_decision.ask_mode,
            question_source=route_decision.question_source,
            question_reason=route_decision.question_reason,
            question_added=question_added,
            question_removed=question_removed,
            question_cooldown_hit=bool(cooldown_hit),
            question_count=final_question_count,
            hot_path=True,
            answer_chars=len(clean_text(answer)),
        )

    def summarize_question_metrics(self) -> dict[str, Any]:
        return summarize_question_metrics_payload(self.question_metrics)

    def proactive_scope_candidates(
        self,
        *,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
    ) -> list[tuple[str, str | None]]:
        scopes: list[tuple[str, str | None]] = []
        if session_memory_key:
            scopes.append(("session", session_memory_key))
        if person_key:
            scopes.append(("person", person_key))
        if room_key:
            scopes.append(("room", room_key))
        scopes.append(("guild", None))
        return scopes

    def record_session_question_asked(self, session_key: str | None, *, now: float | None = None) -> None:
        key = session_key or "global"
        state = self.session_question_state.setdefault(key, default_session_question_state())
        turn_index = int(state.get("turn_index", 0))
        turns = [int(item) for item in state.get("question_turns", []) if isinstance(item, int)]
        turns.append(turn_index)
        state["question_turns"] = turns[-10:]
        state["last_question_at"] = time.monotonic() if now is None else float(now)

    def resolve_pending_proactive_question_for_turn(
        self,
        guild_id: int | None,
        user_text: str,
        *,
        session_key: str | None = None,
        session_memory_key: str | None = None,
        metrics: dict | None = None,
    ) -> dict[str, Any]:
        if guild_id is None:
            return {"resolved": False, "reason": "no_guild"}
        session_scope_key = session_memory_key or session_key
        if not session_scope_key:
            return {"resolved": False, "reason": "no_session_scope"}
        result = resolve_pending_question_answer(
            guild_id,
            user_text,
            session_scope_key=session_scope_key,
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["proactive_question_resolution"] = result
        return result

    def select_and_mark_proactive_question(
        self,
        *,
        guild_id: int | None,
        source: str,
        user_text: str,
        answer_text: str = "",
        awaiting_user_reply: bool = False,
        room_key: str | None = None,
        person_key: str | None = None,
        session_key: str | None = None,
        session_memory_key: str | None = None,
        runtime_block_reason: str = "",
        metrics: dict | None = None,
    ) -> dict[str, Any] | None:
        if guild_id is None:
            return None
        session_scope_key = session_memory_key or session_key
        cooldown_key = session_key or session_scope_key or "global"
        session_cooldown = self.question_cooldown_hit(cooldown_key)
        base_gate = evaluate_proactive_question_gate(
            guild_id=guild_id,
            source=source,
            user_text=user_text,
            answer_text=answer_text,
            awaiting_user_reply=awaiting_user_reply,
            session_scope_key=session_scope_key,
            session_cooldown_hit=session_cooldown,
            runtime_block_reason=runtime_block_reason,
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["proactive_question_gate"] = base_gate.as_dict()
        if not base_gate.allowed:
            return None

        for scope_type, scope_key in self.proactive_scope_candidates(
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        ):
            candidate = select_question_to_ask(
                guild_id,
                scope_type=scope_type,
                scope_key=scope_key,
                session_scope_key=session_scope_key,
            )
            if not candidate:
                continue
            ask_text = clean_text(str(candidate.get("ask_text") or ""))
            question_id = clean_text(str(candidate.get("id") or ""))
            if not ask_text or not question_id:
                continue
            candidate_gate = evaluate_proactive_question_gate(
                guild_id=guild_id,
                source=source,
                user_text=user_text,
                answer_text=answer_text,
                awaiting_user_reply=awaiting_user_reply,
                session_scope_key=session_scope_key,
                session_cooldown_hit=False,
                candidate_text=ask_text,
            )
            if metrics is not None:
                metrics.setdefault("meta", {})["proactive_question_candidate_gate"] = candidate_gate.as_dict()
            if not candidate_gate.allowed:
                continue
            marked = mark_question_asked(
                guild_id,
                question_id,
                scope_type=scope_type,
                scope_key=scope_key,
                session_scope_key=session_scope_key,
                asked_text=ask_text,
            )
            if not marked:
                continue
            self.record_session_question_asked(cooldown_key)
            result = {
                "id": question_id,
                "ask_text": ask_text,
                "scope_type": scope_type,
                "scope_key": scope_key,
                "source": source,
            }
            if metrics is not None:
                metrics.setdefault("meta", {})["proactive_question_asked"] = result
            return result
        if metrics is not None:
            metrics.setdefault("meta", {})["proactive_question_no_candidate"] = True
        return None

    def maybe_append_proactive_question(
        self,
        answer_text: str,
        *,
        guild_id: int | None,
        source: str,
        user_text: str,
        awaiting_user_reply: bool,
        room_key: str | None = None,
        person_key: str | None = None,
        session_key: str | None = None,
        session_memory_key: str | None = None,
        metrics: dict | None = None,
    ) -> tuple[str, bool]:
        answer = (answer_text or "").strip()
        if guild_id is None:
            return answer, False
        if source in {"text", "control_page"} and not should_offer_proactive_question(
            source=source,
            user_text=user_text,
            answer_text=answer,
            awaiting_user_reply=awaiting_user_reply,
        ):
            return answer, False
        marked = self.select_and_mark_proactive_question(
            guild_id=guild_id,
            source=source,
            user_text=user_text,
            answer_text=answer,
            awaiting_user_reply=awaiting_user_reply,
            room_key=room_key,
            person_key=person_key,
            session_key=session_key,
            session_memory_key=session_memory_key,
            metrics=metrics,
        )
        if marked:
            return clean_text(f"{answer}\n\n{marked['ask_text']}"), True
        return answer, False


__all__ = [
    "QUESTION_ASK_MODES",
    "QuestionPolicyState",
    "default_question_metrics",
    "default_session_question_state",
    "extract_question_policy_from_route_meta",
    "is_continuable_technical_topic",
    "normalize_question_policy_mapping",
    "user_frustration_with_questions",
    "user_wants_direct_answer",
]
