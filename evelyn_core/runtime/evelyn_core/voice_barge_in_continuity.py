from __future__ import annotations

import time
from typing import Any, Callable
from dataclasses import dataclass


VOICE_BARGE_IN_REASON_CODE = {
    "RESUME_SUCCESS": "BRI_RESUME_SUCCESS",
    "PREEMPTED": "BRI_PREEMPTED",
    "FALSE_TRIGGER": "BRI_FALSE_TRIGGER",
    "TIMEOUT": "BRI_TIMEOUT",
    "RECONNECT_FAILURE": "BRI_RECONNECT_FAILURE",
    "INTERRUPT_CUT": "BRI_INTERRUPT_CUT",
}
VOICE_BARGE_IN_REASON_LABEL = {
    "BRI_RESUME_SUCCESS": "완료",
    "BRI_PREEMPTED": "끊김",
    "BRI_FALSE_TRIGGER": "오탐",
    "BRI_TIMEOUT": "지연",
    "BRI_RECONNECT_FAILURE": "재연결 실패",
    "BRI_INTERRUPT_CUT": "끊김",
}
VOICE_BARGE_IN_RESET_CONFIRM_KEYWORD = "confirm"
VOICE_BARGE_IN_EVENT_START = "start"
VOICE_BARGE_IN_EVENT_FINISH = "finish"


@dataclass(frozen=True)
class VoiceBargeInContinuityRuntimeDeps:
    tracker: "VoiceBargeInContinuityTracker"
    command_status: Callable[[bool], str]


def parse_barge_in_reason_label_from_runtime(
    raw_reason_code: str,
    *,
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> str:
    return deps.tracker.parse_reason_label(raw_reason_code)


def format_voice_barge_in_continuity_summary_from_runtime(
    continuity: dict[str, Any],
    *,
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> str:
    return deps.tracker.format_summary(continuity)


def format_voice_barge_in_continuity_detail_lines_from_runtime(
    continuity: dict[str, Any],
    *,
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> list[str]:
    return deps.tracker.format_detail_lines(continuity, command_status=deps.command_status)


def start_voice_barge_in_continuity_probe_from_runtime(
    metrics: dict,
    *,
    source: str,
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> None:
    deps.tracker.start_probe(metrics, source=source)


def build_voice_barge_in_continuity_snapshot_from_runtime(
    *,
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> dict[str, Any]:
    return deps.tracker.snapshot()


def reset_voice_barge_in_continuity_probe_from_runtime(
    *,
    reason: str = "",
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> None:
    deps.tracker.reset(reason=reason)


def mark_voice_barge_in_continuity_probe_from_runtime(
    metrics: dict,
    *,
    success: bool,
    reason: str,
    queued_sentence_count: int = 0,
    reason_code: str | None = None,
    reason_label: str | None = None,
    event: str = VOICE_BARGE_IN_EVENT_FINISH,
    deps: VoiceBargeInContinuityRuntimeDeps,
) -> None:
    deps.tracker.mark_probe(
        metrics,
        success=success,
        reason=reason,
        queued_sentence_count=queued_sentence_count,
        reason_code=reason_code,
        reason_label=reason_label,
        event=event,
    )

CleanText = Callable[[str], str]
CommandStatus = Callable[[bool], str]
LogEnabled = Callable[[], bool]
EventLogger = Callable[..., None]


def _default_clean_text(value: str) -> str:
    return str(value or "").strip()


def _default_command_status(value: bool) -> str:
    return "켜짐" if value else "꺼짐"


def _default_log_enabled() -> bool:
    return False


def _default_event_logger(*_args: Any, **_kwargs: Any) -> None:
    return None


class VoiceBargeInContinuityTracker:
    def __init__(
        self,
        *,
        target_count: int = 5,
        history_limit: int = 5,
        clean_text: CleanText | None = None,
        log_enabled: LogEnabled | None = None,
        event_logger: EventLogger | None = None,
    ) -> None:
        self.target_count = max(0, int(target_count))
        self.history_limit = max(1, int(history_limit))
        self.clean_text = clean_text or _default_clean_text
        self.log_enabled = log_enabled or _default_log_enabled
        self.event_logger = event_logger or _default_event_logger
        self.state = self._new_state()

    def _new_state(self) -> dict[str, Any]:
        return {
            "attempt_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "current_success_streak": 0,
            "max_success_streak": 0,
            "current_failure_streak": 0,
            "max_failure_streak": 0,
            "target_reached": False,
            "target_reached_at": None,
            "target_reached_turn_id": None,
            "last_turn_id": None,
            "last_source": None,
            "last_sequence_id": 0,
            "last_event": None,
            "last_reason": None,
            "last_reason_code": None,
            "last_reason_label": None,
            "last_queued_sentence_count": 0,
            "last_merged": False,
            "last_updated_at": None,
            "recent_attempts": [],
        }

    def parse_reason_label(self, raw_reason_code: str) -> str:
        code = self.clean_text(str(raw_reason_code))
        return VOICE_BARGE_IN_REASON_LABEL.get(code, "끊김")

    def classify_reason(
        self,
        *,
        success: bool,
        reason: str,
        queued_sentence_count: int = 0,
    ) -> tuple[str, str]:
        normalized = self.clean_text(str(reason)).lower()
        if success:
            if queued_sentence_count <= 0:
                return (
                    VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"],
                    VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"]],
                )
            return (
                VOICE_BARGE_IN_REASON_CODE["RESUME_SUCCESS"],
                VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["RESUME_SUCCESS"]],
            )
        if "timeout" in normalized or "timed out" in normalized:
            return (
                VOICE_BARGE_IN_REASON_CODE["TIMEOUT"],
                VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["TIMEOUT"]],
            )
        if "reconnect" in normalized or "broken" in normalized or "connection" in normalized or "disconnect" in normalized:
            return (
                VOICE_BARGE_IN_REASON_CODE["RECONNECT_FAILURE"],
                VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["RECONNECT_FAILURE"]],
            )
        if "preempt" in normalized or "interruption preempt" in normalized:
            return (
                VOICE_BARGE_IN_REASON_CODE["PREEMPTED"],
                VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["PREEMPTED"]],
            )
        return (
            VOICE_BARGE_IN_REASON_CODE["INTERRUPT_CUT"],
            VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["INTERRUPT_CUT"]],
        )

    def _append_attempt_history(self, payload: dict[str, Any]) -> None:
        history = self.state.get("recent_attempts")
        if not isinstance(history, list):
            history = []
            self.state["recent_attempts"] = history
        history.append(payload)
        if len(history) > self.history_limit:
            del history[:-self.history_limit]

    def format_attempt_history_lines(self, state: dict[str, Any]) -> list[str]:
        history = state.get("recentAttempts", state.get("recent_attempts"))
        if not isinstance(history, list) or not history:
            return []
        lines: list[str] = []
        for item in history[-self.history_limit:]:
            if not isinstance(item, dict):
                continue
            index = item.get("attempt")
            event = self.clean_text(str(item.get("event") or "")).strip()
            status = self.clean_text(str(item.get("status") or "")).strip()
            reason_code = self.clean_text(str(item.get("reason_code") or item.get("reasonCode") or "")).strip()
            label = self.clean_text(str(item.get("reason_label") or item.get("reasonLabel") or ""))
            if index is None:
                continue
            code = reason_code or "-"
            evt = event or "-"
            stat = status or "-"
            lines.append(f"{index}:event={evt}:status={stat}:code={code}:label={label or '-'}")
        return lines

    def format_summary(self, continuity: dict[str, Any]) -> str:
        target_count = int(continuity.get("targetCount", 0) or 0)
        if target_count <= 0:
            return "미설정"
        current_streak = int(continuity.get("currentSuccessStreak", 0) or 0)
        success_count = int(continuity.get("successCount", 0) or 0)
        failure_count = int(continuity.get("failureCount", 0) or 0)
        attempt_count = int(continuity.get("attemptCount", 0) or 0)
        success_rate = (float(success_count) / attempt_count * 100.0) if attempt_count > 0 else 0.0
        status_text = "완료" if bool(continuity.get("targetReached", False)) else "진행"
        last_reason = self.clean_text(str(continuity.get("lastReasonLabel") or continuity.get("lastReason") or "없음"))
        last_reason_code = self.clean_text(str(continuity.get("lastReasonCode") or "-"))
        return (
            f"{status_text} {current_streak}/{target_count} · "
            f"성공률 {success_rate:.1f}% · "
            f"시도 {attempt_count}(성공 {success_count}/실패 {failure_count}) · "
            f"마지막 {last_reason}({last_reason_code})"
        )

    def format_detail_lines(
        self,
        continuity: dict[str, Any],
        *,
        command_status: CommandStatus | None = None,
        now: float | None = None,
    ) -> list[str]:
        status_text = command_status or _default_command_status
        target_count = int(continuity.get("targetCount", 0) or 0)
        current_streak = int(continuity.get("currentSuccessStreak", 0) or 0)
        max_success_streak = int(continuity.get("maxSuccessStreak", 0) or 0)
        max_failure_streak = int(continuity.get("maxFailureStreak", 0) or 0)
        remaining = max(0, target_count - current_streak)
        last_updated_at = continuity.get("lastUpdatedAt")
        if isinstance(last_updated_at, (int, float)):
            current_time = time.time() if now is None else float(now)
            continuity_age = round(max(0.0, current_time - float(last_updated_at)), 1)
            continuity_last_update = f"{continuity_age:.1f}s ago"
        else:
            continuity_last_update = "-"
        recent_attempts = self.format_attempt_history_lines(continuity)
        recent_attempts_text = " / ".join(recent_attempts) if recent_attempts else "없음"
        return [
            f"- 요약: {self.format_summary(continuity)}",
            f"- 남은 연속 성공: {remaining}",
            f"- 최대 연속: 성공 {max_success_streak}, 실패 {max_failure_streak}",
            f"- 마지막상태: event={continuity.get('lastEvent', 'none')} source={continuity.get('lastSource', 'none')} "
            f"merged={status_text(bool(continuity.get('lastMerged', False)))} 문장={continuity.get('lastQueuedSentenceCount', 0)}",
            f"- 최근 연속기록({self.history_limit}회): {recent_attempts_text}",
            f"- 최근업데이트: {continuity_last_update}",
        ]

    def start_probe(self, metrics: dict[str, Any], *, source: str) -> None:
        meta = metrics.setdefault("meta", {})
        had_active_probe = bool(meta.get("barge_in_probe_active"))
        if had_active_probe:
            self.mark_probe(
                metrics,
                success=False,
                reason="preempted_interrupt",
                reason_code=VOICE_BARGE_IN_REASON_CODE["PREEMPTED"],
                event=VOICE_BARGE_IN_EVENT_START,
            )
        current_sequence_id = int(self.state.get("last_sequence_id", 0)) + 1
        self.state["last_sequence_id"] = current_sequence_id
        metrics["meta"]["barge_in_probe_sequence"] = current_sequence_id
        meta["barge_in_probe_active"] = True
        meta["barge_in_probe_source"] = source
        meta["barge_in_probe_started_at"] = time.monotonic()

    def _build_merge_stats(self, meta: dict[str, Any]) -> dict[str, Any]:
        merge_meta = meta.get("barge_in_utterance_merge")
        if not isinstance(merge_meta, dict):
            return {
                "applied": False,
                "deltaSec": None,
                "source": None,
                "reason": None,
            }
        return {
            "applied": bool(merge_meta.get("applied", False)),
            "deltaSec": merge_meta.get("delta_sec"),
            "source": merge_meta.get("source"),
            "reason": merge_meta.get("reason"),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "targetCount": self.target_count,
            "attemptCount": self.state.get("attempt_count", 0),
            "successCount": self.state.get("success_count", 0),
            "failureCount": self.state.get("failure_count", 0),
            "currentSuccessStreak": self.state.get("current_success_streak", 0),
            "maxSuccessStreak": self.state.get("max_success_streak", 0),
            "currentFailureStreak": self.state.get("current_failure_streak", 0),
            "maxFailureStreak": self.state.get("max_failure_streak", 0),
            "targetReached": bool(self.state.get("target_reached", False)),
            "targetReachedAt": self.state.get("target_reached_at"),
            "targetReachedTurnId": self.state.get("target_reached_turn_id"),
            "lastTurnId": self.state.get("last_turn_id"),
            "lastSource": self.state.get("last_source"),
            "lastSequenceId": self.state.get("last_sequence_id"),
            "lastReason": self.state.get("last_reason"),
            "lastReasonCode": self.state.get("last_reason_code"),
            "lastReasonLabel": self.state.get("last_reason_label"),
            "lastEvent": self.state.get("last_event"),
            "lastQueuedSentenceCount": self.state.get("last_queued_sentence_count", 0),
            "lastMerged": bool(self.state.get("last_merged", False)),
            "lastUpdatedAt": self.state.get("last_updated_at"),
            "recentAttempts": list(self.state.get("recent_attempts", [])),
        }

    def reset(self, *, reason: str = "") -> None:
        now = time.time()
        self.state = self._new_state()
        self.state["last_reason"] = f"reset:{self.clean_text(reason) or 'manual_reset'}"
        self.state["last_reason_code"] = "BRI_RESET"
        self.state["last_reason_label"] = "리셋"
        self.state["last_event"] = "reset"
        self.state["last_updated_at"] = now
        if self.log_enabled():
            print(f"[VOICE BARGE-IN CONTINUITY] reset reason={self.state['last_reason']}")

    def mark_probe(
        self,
        metrics: dict[str, Any],
        *,
        success: bool,
        reason: str,
        queued_sentence_count: int = 0,
        reason_code: str | None = None,
        reason_label: str | None = None,
        event: str = VOICE_BARGE_IN_EVENT_FINISH,
    ) -> None:
        meta = metrics.setdefault("meta", {})
        if not meta.get("barge_in_probe_active"):
            return

        now = time.time()
        classified_code, classified_label = self.classify_reason(
            success=success,
            reason=reason,
            queued_sentence_count=queued_sentence_count,
        )
        final_reason_code = reason_code or classified_code
        final_reason_label = reason_label or classified_label
        sequence_id = int(meta.get("barge_in_probe_sequence", self.state.get("last_sequence_id", 0)))
        self.state["attempt_count"] = int(self.state.get("attempt_count", 0)) + 1
        self.state["last_turn_id"] = meta.get("turn_id")
        self.state["last_source"] = meta.get("barge_in_probe_source")
        self.state["last_reason"] = reason
        self.state["last_reason_code"] = final_reason_code
        self.state["last_reason_label"] = final_reason_label
        self.state["last_event"] = event
        self.state["last_queued_sentence_count"] = int(queued_sentence_count)
        self.state["last_sequence_id"] = sequence_id
        self.state["last_updated_at"] = now
        self.state["last_merged"] = bool(self._build_merge_stats(meta).get("applied", False))

        if success:
            self.state["success_count"] = int(self.state.get("success_count", 0)) + 1
            self.state["current_success_streak"] = int(self.state.get("current_success_streak", 0)) + 1
            self.state["current_failure_streak"] = 0
            self.state["max_success_streak"] = max(
                int(self.state.get("max_success_streak", 0)),
                int(self.state["current_success_streak"]),
            )
        else:
            self.state["failure_count"] = int(self.state.get("failure_count", 0)) + 1
            self.state["current_failure_streak"] = int(self.state.get("current_failure_streak", 0)) + 1
            self.state["current_success_streak"] = 0
            self.state["max_failure_streak"] = max(
                int(self.state.get("max_failure_streak", 0)),
                int(self.state["current_failure_streak"]),
            )

        if (
            not self.state.get("target_reached")
            and int(self.state.get("current_success_streak", 0)) >= self.target_count
        ):
            self.state["target_reached"] = True
            self.state["target_reached_turn_id"] = meta.get("turn_id")
            self.state["target_reached_at"] = now

        if self.log_enabled():
            print(
                "[BARGE IN CONTINUITY] "
                f"event={event} "
                f"status={'success' if success else 'failure'} "
                f"code={final_reason_code} "
                f"label={final_reason_label} "
                f"source={self.state.get('last_source')} "
                f"sequence={sequence_id} "
                f"attempt={self.state.get('attempt_count')} "
                f"turn={meta.get('turn_id')} "
                f"queue={queued_sentence_count} "
                f"streak={self.state.get('current_success_streak')}/{self.state.get('max_success_streak')} "
                f"target_reached={self.state.get('target_reached')} "
                f"target_count={self.target_count} "
                f"merged={self.state.get('last_merged')}"
            )

        self._append_attempt_history(
            {
                "attempt": self.state.get("attempt_count"),
                "status": "success" if success else "failure",
                "event": event,
                "reason": reason,
                "reason_code": final_reason_code,
                "reason_label": final_reason_label,
                "sequence": sequence_id,
            },
        )

        self.event_logger(
            "barge_in_continuity",
            turn_id=meta.get("turn_id"),
            session_key=meta.get("session_key"),
            guild_id=meta.get("guild_id"),
            validation_session_id=meta.get("validation_session_id"),
            validation_step_id=meta.get("validation_step_id"),
            validation_transcript_match=meta.get("validation_transcript_match"),
            event=event,
            status="success" if success else "failure",
            reason=reason,
            reason_code=final_reason_code,
            reason_label=final_reason_label,
            source=self.state.get("last_source"),
            attempt=self.state.get("attempt_count"),
            sequence=sequence_id,
            current_success_streak=self.state.get("current_success_streak"),
            max_success_streak=self.state.get("max_success_streak"),
            current_failure_streak=self.state.get("current_failure_streak"),
            max_failure_streak=self.state.get("max_failure_streak"),
            queued_sentence_count=queued_sentence_count,
            merge_applied=self.state.get("last_merged"),
            target_reached=self.state.get("target_reached"),
            target_count=self.target_count,
        )
        meta["barge_in_probe_active"] = False
        meta["barge_in_probe_started_at"] = None


__all__ = [
    "VoiceBargeInContinuityRuntimeDeps",
    "build_voice_barge_in_continuity_snapshot_from_runtime",
    "format_voice_barge_in_continuity_detail_lines_from_runtime",
    "format_voice_barge_in_continuity_summary_from_runtime",
    "VOICE_BARGE_IN_EVENT_FINISH",
    "VOICE_BARGE_IN_EVENT_START",
    "VOICE_BARGE_IN_REASON_CODE",
    "VOICE_BARGE_IN_REASON_LABEL",
    "VOICE_BARGE_IN_RESET_CONFIRM_KEYWORD",
    "mark_voice_barge_in_continuity_probe_from_runtime",
    "parse_barge_in_reason_label_from_runtime",
    "VoiceBargeInContinuityTracker",
    "reset_voice_barge_in_continuity_probe_from_runtime",
    "start_voice_barge_in_continuity_probe_from_runtime",
]
