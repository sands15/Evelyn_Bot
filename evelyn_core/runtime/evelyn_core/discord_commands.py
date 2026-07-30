from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .minecraft_mode_composition import (
    MINECRAFT_CONNECTED_OUTCOME,
    minecraft_connection_confirmed,
)
from .text import clean_text


def is_control_command_authorized_payload(
    *,
    author_id: int | None,
    is_administrator: bool,
    allowed_user_ids: Iterable[int],
) -> bool:
    return bool(author_id in set(allowed_user_ids) or is_administrator)


def control_command_check_failure_message() -> str:
    return "이 명령은 허용된 Discord ID이거나 서버 관리자 권한이 있어야 쓸 수 있어."


def build_status_command_text(
    *,
    model_name: str,
    router_model_name: str,
    summary_model_name: str,
    stt_model_name: str,
    voice_channel_name: str | None,
    listening: bool,
    voice_debug_save_audio: bool,
    opus_env_state: str | None,
    opus_runtime_value: Any,
    vad_enabled: bool,
    vad_provider: str,
) -> str:
    return "\n".join(
        [
            f"모델: {model_name}",
            f"라우터모델: {router_model_name}",
            f"서브모델: {summary_model_name}",
            f"STT: {stt_model_name}",
            f"음성채널: {voice_channel_name or '없음'}",
            f"리스닝: {'on' if listening else 'off'}",
            f"디버그 오디오 저장: {'on' if voice_debug_save_audio else 'off'}",
            f"OPUS_ERROR_TO_SILENCE(env): {opus_env_state if opus_env_state is not None else 'unset'}",
            f"OPUS_ERROR_TO_SILENCE(runtime): {opus_runtime_value}",
            f"VAD: {'on' if vad_enabled else 'off'} ({vad_provider})",
        ]
    )


def guild_only_command_message() -> str:
    return "이 명령은 길드에서만 쓸 수 있어."


def build_prefix_current_reply(current_prefix: str) -> str:
    return (
        f"현재 이 길드 명령어 시작 부호는 `{current_prefix}` 야. "
        f"바꾸려면 `{current_prefix}접두사 ?` 처럼 써줘. "
        f"기본값으로 돌리려면 `{current_prefix}접두사 기본`"
    )


def build_prefix_reset_reply(saved_prefix: str) -> str:
    return f"✅ 명령어 시작 부호를 기본값 `{saved_prefix}` 로 되돌렸어."


def build_prefix_saved_reply(saved_prefix: str) -> str:
    return f"✅ 이 길드 명령어 시작 부호를 `{saved_prefix}` 로 저장했어. 이제 `{saved_prefix}초기화`, `{saved_prefix}들어와` 처럼 쓰면 돼."


def normalize_channel_setting_action(action: str | None) -> str:
    return str(action or "목록").strip().lower()


def build_channel_setting_list_reply(
    *,
    label: str,
    channel_ids: Iterable[int],
    resolve_channel: Callable[[int], Any],
) -> str:
    names: list[str] = []
    for channel_id in channel_ids:
        target = resolve_channel(int(channel_id))
        names.append(getattr(target, "mention", None) if target is not None else f"#{channel_id}")
    return f"{label}: " + (", ".join(names) if names else "없음")


def build_observe_channel_usage(prefix: str = "!") -> str:
    return f"사용법: `{prefix}관찰채널 목록` / `{prefix}관찰채널 추가 #채널` / `{prefix}관찰채널 제거 #채널`"


def build_command_channel_usage(prefix: str = "!") -> str:
    return f"사용법: `{prefix}명령채널 목록` / `{prefix}명령채널 추가 #채널` / `{prefix}명령채널 제거 #채널`"


def build_help_command_text(*, prefix: str, control_authorized: bool) -> str:
    lines = [
        "📘 Evelyn 명령어",
        f"- {prefix}들어와 / {prefix}다시들어와 / {prefix}나가",
        f"- {prefix}이블린페이지",
        f"- {prefix}상태 / {prefix}접두사",
        f"- {prefix}자율시작 / {prefix}자율정지 / {prefix}자율상태",
        f"- {prefix}관찰채널 목록|추가 #채널|제거 #채널",
        f"- {prefix}명령채널 목록|추가 #채널|제거 #채널",
        f"- {prefix}초기화",
    ]
    if control_authorized:
        lines.append(f"- {prefix}재시작 / {prefix}종료")
    return "\n".join(lines)


def build_autonomy_status_command_text(
    state: Any,
    *,
    minecraft_enabled: bool,
    allowed_limit: int = 6,
) -> str:
    current_goal = getattr(state, "current_goal", None)
    current_plan = getattr(state, "current_plan", None)
    goal = getattr(current_goal, "summary", None) if current_goal else None
    plan = getattr(current_plan, "summary", None) if current_plan else None
    allowed_actions = list(getattr(state, "allowed_actions", []) or [])
    allowed = ", ".join(str(item) for item in allowed_actions[:allowed_limit])
    if len(allowed_actions) > allowed_limit:
        allowed += ", ..."
    return (
        f"🤖 자율상태\n"
        f"- status: {getattr(state, 'status', None)}\n"
        f"- safety: {getattr(state, 'safety_mode', None)}\n"
        f"- goal: {goal or '없음'}\n"
        f"- plan: {plan or '없음'}\n"
        f"- failures: {getattr(state, 'failure_count', 0)}\n"
        f"- last_error: {getattr(state, 'last_error', None) or '없음'}\n"
        f"- minecraft_autonomy: {'on' if minecraft_enabled else 'off'}\n"
        f"- allowed: {allowed or '없음'}"
    )


def build_minecraft_connect_reply(observed: dict[str, Any]) -> str:
    connected = (
        observed.get("outcome_verified") is True
        and observed.get("outcome_code") == MINECRAFT_CONNECTED_OUTCOME
        and minecraft_connection_confirmed(observed)
    )
    target = f"{observed.get('position')}" if observed.get("position") else "위치 미확인"
    stage = clean_text(str(observed.get("objective_stage") or "")) or "unknown"
    goal = clean_text(str(observed.get("objective_goal") or "")) or "progress_to_diamond"
    last_error = clean_text(str(observed.get("last_error") or observed.get("wait_last_error") or ""))
    if connected:
        return "✅ Voyager 기반 마인크래프트 자율 모드 시작 완료." + f"\n- goal: {goal}\n- stage: {stage}\n- position: {target}"
    detail = f" last_error={last_error}" if last_error else ""
    return "❌ 마인크래프트 접속 실패: Voyager 서비스는 올라왔지만 게임 연결 확인에 실패했어." + detail


def build_minecraft_status_command_text(status: dict[str, Any]) -> str:
    observed = status.get("observation") if isinstance(status.get("observation"), dict) else {}
    evaluation = status.get("voyager_evaluation") if isinstance(status.get("voyager_evaluation"), dict) else {}
    position = observed.get("position") if isinstance(observed, dict) else None
    hunger = observed.get("hunger") if isinstance(observed, dict) else None
    health = observed.get("health") if isinstance(observed, dict) else None
    hostiles = observed.get("hostiles_nearby") if isinstance(observed, dict) else None
    tech_tree = evaluation.get("tech_tree") if isinstance(evaluation.get("tech_tree"), dict) else {}
    skill_library = evaluation.get("skill_library") if isinstance(evaluation.get("skill_library"), dict) else {}
    lease_status = (
        status.get("world_lease")
        if isinstance(status.get("world_lease"), dict)
        else {}
    )
    lease = (
        lease_status.get("lease")
        if isinstance(lease_status.get("lease"), dict)
        else {}
    )
    return (
        "⛏️ 마인크래프트 상태\n"
        f"- service: voyager\n"
        f"- running: {'on' if status.get('running') else 'off'}\n"
        f"- connected: {'on' if status.get('connected') else 'off'}\n"
        f"- world_lease: {lease_status.get('state') or 'unknown'}\n"
        f"- lease_expires_at: {lease.get('expiresAt') if lease.get('expiresAt') is not None else 'none'}\n"
        f"- goal: {status.get('goal') or 'none'}\n"
        f"- stage: {status.get('stage') or 'unknown'}\n"
        f"- task: {status.get('current_task') or 'none'}\n"
        f"- task_stage: {status.get('current_task_stage') or 'unknown'}\n"
        f"- progress: {status.get('last_progress_message') or 'none'}\n"
        f"- eval_goal: {evaluation.get('goal') or status.get('goal') or 'none'}\n"
        f"- unique_items: {evaluation.get('unique_item_count') if evaluation.get('unique_item_count') is not None else 'unknown'}\n"
        f"- tech_tree: {tech_tree.get('highest_unlocked') or 'unknown'}\n"
        f"- travel_distance: {evaluation.get('travel_distance_blocks') if evaluation.get('travel_distance_blocks') is not None else 'unknown'}\n"
        f"- skill_library: {skill_library.get('size') if skill_library.get('size') is not None else 'unknown'}\n"
        f"- health: {health if health is not None else 'unknown'}\n"
        f"- hunger: {hunger if hunger is not None else 'unknown'}\n"
        f"- hostiles: {hostiles if hostiles is not None else 'unknown'}\n"
        f"- position: {position if position is not None else 'unknown'}"
    )


def build_minecraft_goal_missing_reply() -> str:
    return "목표를 같이 적어줘. 예: 마크목표 diamond 또는 마크목표 iron_pickaxe"


def build_minecraft_goal_updated_reply(goal_text: str, status: dict[str, Any]) -> str:
    verified = (
        status.get("outcome_verified") is True
        and status.get("outcome_code") == "minecraft_goal_confirmed"
        and clean_text(str(status.get("goal") or "")) == clean_text(goal_text)
    )
    if not verified:
        return "❌ 마인크래프트 목표 변경 결과를 확인하지 못했어."
    return f"🎯 마인크래프트 목표를 바꿨어.\n- goal: {goal_text}\n- stage: {status.get('stage') or 'unknown'}"


def build_reset_guild_memory_reply(*, guild_name: str, current_prefix: str) -> str:
    return f"🧹 {guild_name} 메모리와 대화 히스토리를 이 길드만 초기화했어. 명령어 시작 부호 `{current_prefix}` 설정은 유지했어."


__all__ = [
    "build_channel_setting_list_reply",
    "build_autonomy_status_command_text",
    "build_command_channel_usage",
    "build_help_command_text",
    "build_minecraft_connect_reply",
    "build_minecraft_goal_missing_reply",
    "build_minecraft_goal_updated_reply",
    "build_minecraft_status_command_text",
    "build_observe_channel_usage",
    "build_prefix_current_reply",
    "build_prefix_reset_reply",
    "build_prefix_saved_reply",
    "build_reset_guild_memory_reply",
    "build_status_command_text",
    "control_command_check_failure_message",
    "guild_only_command_message",
    "is_control_command_authorized_payload",
    "normalize_channel_setting_action",
]
