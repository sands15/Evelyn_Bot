from __future__ import annotations

import re
from types import MappingProxyType
from typing import Final


_PUBLIC_ERROR_CODE_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]{2,63}$"
)

PUBLIC_FAILURE_MESSAGES = MappingProxyType(
    {
        "operation_failed": (
            "❌ 작업을 완료하지 못했어. 잠깐 뒤에 다시 시도해줘."
        ),
        "text_turn_failed": (
            "❌ 응답을 전달하지 못했어. 잠깐 뒤에 다시 시도해줘."
        ),
        "control_page_chat_failed": (
            "응답을 만들지 못했어. 잠깐 뒤에 다시 시도해줘."
        ),
        "fast_control_chat_failed": (
            "응답을 만들지 못했어. 잠깐 뒤에 다시 시도해줘."
        ),
        "fast_control_stream_failed": (
            "응답 스트림이 중단됐어. 잠깐 뒤에 다시 시도해줘."
        ),
        "background_action_failed": (
            "작업 실행 중 오류가 나서 완료하지 못했어."
        ),
        "voice_connect_failed": (
            "❌ 음성 채널에 연결하지 못했어. 잠깐 뒤에 다시 시도해줘."
        ),
        "voice_reconnect_failed": (
            "❌ 음성 채널에 다시 연결하지 못했어. 잠깐 뒤에 다시 시도해줘."
        ),
        "autonomy_stop_failed": (
            "❌ 자율 행동을 완전히 멈췄는지 확인하지 못했어. "
            "승인은 폐기했으니 상태를 다시 확인해줘."
        ),
        "minecraft_connect_failed": (
            "❌ 마인크래프트 자율 모드 시작을 완료하지 못했어. "
            "연결과 자율 경로 상태를 다시 확인해줘."
        ),
        "minecraft_disconnect_failed": (
            "❌ 마인크래프트 연결을 종료하지 못했어. 현재 상태를 "
            "다시 확인해줘."
        ),
        "minecraft_status_failed": (
            "❌ 마인크래프트 상태를 확인하지 못했어. 잠깐 뒤에 "
            "다시 시도해줘."
        ),
        "minecraft_goal_failed": (
            "❌ 마인크래프트 목표를 변경하지 못했어. 현재 상태를 "
            "다시 확인해줘."
        ),
        "open_memory_vault_failed": (
            "Obsidian 메모리 vault를 열지 못했어."
        ),
        "repair_launch_failed": (
            "복구 작업을 시작하지 못했어. 상태를 다시 확인해줘."
        ),
        "local_restart_failed": (
            "로컬 재시작 작업을 시작하지 못했어."
        ),
        "local_shutdown_failed": (
            "로컬 종료 작업을 시작하지 못했어."
        ),
        "mic_control_failed": (
            "마이크 상태를 변경하지 못했어."
        ),
        "minecraft_snapshot_unavailable": (
            "마인크래프트 상태를 가져오지 못했어."
        ),
    }
)


def public_error_code(
    value: object,
    *,
    fallback: str = "operation_failed",
) -> str:
    candidate = str(value or "").strip().lower()
    safe_fallback = str(fallback or "").strip().lower()
    if not _PUBLIC_ERROR_CODE_RE.fullmatch(safe_fallback):
        safe_fallback = "operation_failed"
    if not _PUBLIC_ERROR_CODE_RE.fullmatch(candidate):
        return safe_fallback
    return candidate


def public_failure_message(code: object) -> str:
    """Return only allowlisted text and a stable, non-secret error code."""
    candidate = public_error_code(code)
    if candidate not in PUBLIC_FAILURE_MESSAGES:
        candidate = "operation_failed"
    return f"{PUBLIC_FAILURE_MESSAGES[candidate]} ({candidate})"


__all__ = [
    "PUBLIC_FAILURE_MESSAGES",
    "public_error_code",
    "public_failure_message",
]
