from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .continuity_commit_contract import (
    require_durable_continuity_receipt,
)
from .conversation_memory_receipt import (
    capture_conversation_memory_receipt_ref,
    memory_receipt_ref_from_metrics,
)
from .memory_exposure import (
    current_memory_exposure_position,
    memory_exposure_guard,
)
from .memory_deletion_journal import MemoryDeletionJournalIntegrityError
from .reply_memory_boundary import validate_reply_memory_boundary

@dataclass(frozen=True)
class ControlPageTextRuntimeDeps:
    memory_index_dir: Path
    effective_guild_id: Callable[[Any | None], int | None]
    session_key_for_guild: Callable[[int | None], str]
    get_session_lock: Callable[[str], Any]
    begin_user_text_turn: Callable[..., Any]
    turn_scope_factory: Callable[[str], Any]
    replace_room_turn_scope: Callable[[str, Any], Any]
    attach_current_task: Callable[[Any], Any]
    monotonic: Callable[[], float]
    resolve_pending_proactive_question_for_turn: Callable[..., dict]
    ask_llm_streaming: Callable[..., Awaitable[str]]
    clean_text: Callable[[str], str]
    strip_omnivoice_tags: Callable[[str], str]
    session_state_snapshot: Callable[[str], dict]
    maybe_append_proactive_question: Callable[..., tuple[str, bool]]
    finish_assistant_text_turn: Callable[..., None]
    commit_session_continuity: Callable[
        [str, str],
        Awaitable[dict[str, Any]],
    ]
    log_voice_bottleneck_summary: Callable[..., None]
    schedule_local_control_tts: Callable[..., None]
    format_display_text: Callable[..., str]
    fallback_answer_for: Callable[[str], str]
    detach_task: Callable[[Any, Any], None]
    clear_room_turn_scope: Callable[[str, Any], None]
    log: Callable[..., Any]


async def answer_control_page_text_from_runtime(
    guild: Any | None,
    user_text: str,
    *,
    deps: ControlPageTextRuntimeDeps,
) -> str:
    guild_id = deps.effective_guild_id(guild)
    session_key = deps.session_key_for_guild(guild_id)
    state_lock = deps.get_session_lock(session_key)
    async with state_lock:
        started_turn = deps.begin_user_text_turn(session_key, user_text, guild_id=guild_id)
        turn_id = started_turn.turn_id
        topic_id = started_turn.topic_id
    turn_scope = deps.turn_scope_factory(turn_id)
    deps.replace_room_turn_scope(session_key, turn_scope)
    turn_task = deps.attach_current_task(turn_scope)
    text_metrics: dict[str, Any] = {
        "started_at": deps.monotonic(),
        "meta": {
            "turn_id": turn_id,
            "source": "control_page",
            "session_key": session_key,
            "guild_id": guild_id,
            "topic_id": topic_id,
            "turn_type": "control_page_text",
            "selected_path": "control_page_local",
            "needs_tts": False,
        },
        "marks": {},
    }
    proactive_resolution = deps.resolve_pending_proactive_question_for_turn(
        guild_id,
        user_text,
        session_key=session_key,
        session_memory_key=session_key,
        metrics=text_metrics,
    )
    text_turn_summary_logged = False
    try:
        answer = await deps.ask_llm_streaming(
            user_text,
            guild_id=guild_id,
            session_key=session_key,
            source="control_page",
            debug_text=user_text,
            metrics=text_metrics,
            turn_scope=turn_scope,
        )
        vision_capture_error = deps.clean_text(
            str(text_metrics.get("meta", {}).get("vision_capture_error") or "")
        )
        if "black frame" in vision_capture_error.lower():
            answer = (
                "지금 화면 캡처가 검은 프레임으로 들어와서 실제 화면 분석은 못 했어. "
                "비전 모델 문제가 아니라 Windows 캡처 세션이 검은 이미지를 주는 상태야."
            )
        plain_answer = deps.strip_omnivoice_tags(answer) or answer
        awaiting_reply = bool(deps.session_state_snapshot(session_key).get("awaiting_user_reply"))
        proactive_asked = False
        if not proactive_resolution.get("resolved"):
            plain_answer, proactive_asked = deps.maybe_append_proactive_question(
                plain_answer,
                guild_id=guild_id,
                source="control_page",
                user_text=user_text,
                awaiting_user_reply=awaiting_reply,
                session_key=session_key,
                session_memory_key=session_key,
                metrics=text_metrics,
            )
        if proactive_asked:
            answer = plain_answer
            awaiting_reply = True
        response_receipt_ref = memory_receipt_ref_from_metrics(text_metrics)
        response_exposure, response_receipt_ref = (
            validate_reply_memory_boundary(
                memory_exposure_position=(
                    current_memory_exposure_position()
                ),
                memory_receipt=response_receipt_ref,
            )
        )
        capture_conversation_memory_receipt_ref(response_receipt_ref)
        with memory_exposure_guard(
            expected_position=response_exposure,
            required=response_exposure is not None,
            index_dir=deps.memory_index_dir,
        ):
            async with state_lock:
                deps.finish_assistant_text_turn(
                    session_key,
                    user_text,
                    plain_answer,
                    guild_id=guild_id,
                    awaiting_user_reply=awaiting_reply,
                    topic_id=topic_id,
                    memory_receipt=(
                        response_receipt_ref
                    ),
                )
                try:
                    continuity_status = (
                        await deps.commit_session_continuity(
                            session_key,
                            started_turn.turn_id,
                        )
                    )
                    continuity_receipt = (
                        require_durable_continuity_receipt(
                            continuity_status
                        )
                    )
                    text_metrics.setdefault("meta", {}).update(
                        {
                            "continuity_commit": "durable",
                            "continuity_generation": int(
                                continuity_receipt["generation"]
                            ),
                        }
                    )
                except MemoryDeletionJournalIntegrityError:
                    raise
                except Exception as exc:
                    text_metrics.setdefault("meta", {}).update(
                        {
                            "continuity_commit": "failed",
                            "continuity_error": (
                                "conversation_continuity_commit_failed"
                            ),
                        }
                    )
                    deps.log(
                        (
                            "[CONTROL PAGE] "
                            "continuity_commit_failed "
                            "errorType="
                        ),
                        type(exc).__name__,
                    )
        deps.log_voice_bottleneck_summary(
            text_metrics,
            label="text_turn",
            extra=(
                "control_page=true "
                f"chars={len(deps.format_display_text(answer, session_key=session_key).strip())}"
            ),
            event_name="text_turn_summary",
        )
        text_turn_summary_logged = True
        with memory_exposure_guard(
            expected_position=response_exposure,
            required=response_exposure is not None,
            index_dir=deps.memory_index_dir,
        ):
            deps.schedule_local_control_tts(
                plain_answer,
                turn_id=turn_id,
                session_key=session_key,
                turn_scope=turn_scope,
            )
        return deps.format_display_text(plain_answer, session_key=session_key).strip() or deps.fallback_answer_for(
            user_text
        )
    finally:
        if text_metrics and not text_turn_summary_logged:
            text_metrics.setdefault("meta", {})["error_layer"] = "control_page_text"
            text_metrics.setdefault("meta", {}).setdefault(
                "error",
                "control_page_text_aborted_before_summary",
            )
            deps.log_voice_bottleneck_summary(
                text_metrics,
                label="text_turn",
                extra="control_page=true error=true",
                event_name="text_turn_summary",
            )
        deps.detach_task(turn_scope, turn_task)
        deps.clear_room_turn_scope(session_key, turn_scope)
