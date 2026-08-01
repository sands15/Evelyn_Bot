from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable


_LOCAL_OUTPUT_BLOCKER_MESSAGES = {
    "local_output_backend_unavailable": "로컬 오디오 출력 백엔드를 사용할 수 없습니다.",
    "local_output_device_unavailable": "선택한 로컬 출력 장치를 사용할 수 없습니다.",
    "local_output_format_unsupported": "선택한 로컬 출력 장치가 TTS PCM 형식을 지원하지 않습니다.",
    "local_output_readiness_unknown": "로컬 출력 장치 지원 여부가 검증되지 않았습니다.",
}


def _service_map(health: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    rows = (health or {}).get("services") if isinstance(health, dict) else None
    return {
        str(row.get("id") or ""): dict(row)
        for row in (rows or [])
        if isinstance(row, dict) and row.get("id")
    }


def _artifact_payload(service: dict[str, Any] | None) -> dict[str, Any]:
    for check in (service or {}).get("checks") or []:
        if isinstance(check, dict) and check.get("kind") == "artifact_json":
            payload = check.get("payload")
            return dict(payload) if isinstance(payload, dict) else {}
    return {}


def _dependency(service_id: str, service: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(service or {})
    return {
        "id": service_id,
        "label": row.get("label") or service_id,
        "state": row.get("state") or "unknown",
        "ready": bool(row.get("ready") or row.get("state") == "up"),
        "reason": row.get("reason") or "missing",
        "checkedAt": row.get("checkedAt"),
    }


def _repair_action(action_id: str, *, label: str, service_id: str) -> dict[str, Any]:
    return {
        "actionId": action_id,
        "serviceId": service_id,
        "label": label,
        "requiresConfirm": True,
    }


def _blocker(
    blockers: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    service_id: str,
) -> None:
    blockers.append({"code": code, "message": message, "serviceId": service_id})


def _warning(
    warnings: list[dict[str, Any]],
    code: str,
    message: str,
    *,
    service_id: str,
) -> None:
    warnings.append({"code": code, "message": message, "serviceId": service_id})


def _service_blockers(
    service_ids: Iterable[str],
    services: dict[str, dict[str, Any]],
    blockers: list[dict[str, Any]],
    repair_actions: list[dict[str, Any]],
) -> None:
    action_map = {
        "main_llm": ("start_main_llm", "Main LLM 시작"),
        "stt": ("start_stt", "STT 시작"),
        "tts": ("start_tts", "TTS 시작"),
    }
    for service_id in service_ids:
        service = services.get(service_id)
        if service and service.get("state") == "up":
            continue
        state = str((service or {}).get("state") or "unknown")
        _blocker(
            blockers,
            f"{service_id}_{state}",
            f"{service_id} 서비스가 준비되지 않았습니다.",
            service_id=service_id,
        )
        if service_id in action_map:
            action_id, label = action_map[service_id]
            repair_actions.append(
                _repair_action(action_id, label=label, service_id=service_id)
            )


def _capability_state(
    *,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> str:
    if blockers:
        if all(str(item.get("code") or "").endswith("_unknown") for item in blockers):
            return "unknown"
        return "unavailable"
    return "degraded" if warnings else "ready"


def build_voice_capabilities(health: dict[str, Any] | None) -> dict[str, Any]:
    services = _service_map(health)

    local_blockers: list[dict[str, Any]] = []
    local_warnings: list[dict[str, Any]] = []
    local_repairs: list[dict[str, Any]] = []
    _service_blockers(
        ("host_supervisor", "local_io_bridge", "main_llm", "stt", "tts"),
        services,
        local_blockers,
        local_repairs,
    )
    supervisor = services.get("host_supervisor")
    bridge = services.get("local_io_bridge")
    bridge_payload = _artifact_payload(bridge)
    mic = bridge_payload.get("mic") if isinstance(bridge_payload.get("mic"), dict) else {}
    if supervisor is None or supervisor.get("state") != "up":
        local_repairs.append(
            {
                "actionId": "start_host_supervisor_manual",
                "serviceId": "host_supervisor",
                "label": "Host Supervisor 수동 시작",
                "requiresConfirm": False,
                "manualCommand": "start_local.bat --background",
            }
        )
    elif bridge is None or bridge.get("state") != "up":
        local_repairs.append(
            _repair_action(
                "restart_local_bridge",
                label="Local I/O Bridge 재시작",
                service_id="local_io_bridge",
            )
        )
    if bridge and bridge.get("state") == "up":
        if not bool(bridge_payload.get("micEnabled", mic.get("enabled"))):
            _blocker(
                local_blockers,
                "local_mic_disabled",
                "로컬 마이크가 꺼져 있습니다.",
                service_id="local_io_bridge",
            )
        if not bool(mic.get("captureReady")):
            _blocker(
                local_blockers,
                "local_mic_capture_not_ready",
                "로컬 마이크 캡처가 준비되지 않았습니다.",
                service_id="local_io_bridge",
            )
        if not str(bridge_payload.get("outputDevice") or "").strip():
            _blocker(
                local_blockers,
                "local_output_device_missing",
                "로컬 출력 장치가 선택되지 않았습니다.",
                service_id="local_io_bridge",
            )
        if bridge_payload.get("outputReady") is not True:
            output_error_code = str(
                bridge_payload.get("outputErrorCode") or ""
            )
            if output_error_code not in _LOCAL_OUTPUT_BLOCKER_MESSAGES:
                output_error_code = "local_output_readiness_unknown"
            _blocker(
                local_blockers,
                output_error_code,
                _LOCAL_OUTPUT_BLOCKER_MESSAGES[output_error_code],
                service_id="local_io_bridge",
            )
        warmup = (
            bridge_payload.get("ttsWarmup")
            if isinstance(bridge_payload.get("ttsWarmup"), dict)
            else {}
        )
        if not warmup.get("enabled"):
            _blocker(
                local_blockers,
                "tts_warmup_disabled",
                "로컬 TTS warmup이 활성화되지 않았습니다.",
                service_id="local_io_bridge",
            )
        elif not warmup.get("done"):
            _blocker(
                local_blockers,
                "tts_warmup_incomplete",
                "로컬 TTS warmup이 완료되지 않았습니다.",
                service_id="local_io_bridge",
            )
        if bridge_payload.get("lastError") and not warmup.get("error"):
            _warning(
                local_warnings,
                "local_bridge_reported_error",
                "Local I/O Bridge가 최근 오류를 보고했습니다.",
                service_id="local_io_bridge",
            )
        if warmup.get("error"):
            _blocker(
                local_blockers,
                "tts_warmup_failed",
                "로컬 TTS warmup이 실패했습니다.",
                service_id="local_io_bridge",
            )

    discord_blockers: list[dict[str, Any]] = []
    discord_warnings: list[dict[str, Any]] = []
    discord_repairs: list[dict[str, Any]] = []
    _service_blockers(
        ("discord_bot", "main_llm", "stt", "tts"),
        services,
        discord_blockers,
        discord_repairs,
    )
    discord = services.get("discord_bot")
    discord_payload = _artifact_payload(discord)
    if discord is None or discord.get("state") != "up":
        discord_repairs.append(
            _repair_action(
                "start_discord_bot",
                label="Discord Bot 시작",
                service_id="discord_bot",
            )
        )
    else:
        discord_checks = (
            ("gatewayConnected", "discord_gateway_disconnected", "Discord gateway가 연결되지 않았습니다."),
            ("guildConnected", "discord_guild_missing", "연결된 Discord guild가 없습니다."),
            ("voiceConnected", "discord_voice_disconnected", "Discord 음성 채널에 연결되지 않았습니다."),
            ("listening", "discord_not_listening", "Discord 음성 수신이 listening 상태가 아닙니다."),
        )
        for key, code, message in discord_checks:
            if not bool(discord_payload.get(key)):
                _blocker(discord_blockers, code, message, service_id="discord_bot")
        if discord_payload.get("lastError"):
            _warning(
                discord_warnings,
                "discord_runtime_reported_error",
                "Discord runtime이 최근 오류를 보고했습니다.",
                service_id="discord_bot",
            )

    local_state = _capability_state(blockers=local_blockers, warnings=local_warnings)
    discord_state = _capability_state(
        blockers=discord_blockers,
        warnings=discord_warnings,
    )

    vision_blockers: list[dict[str, Any]] = []
    vision_warnings: list[dict[str, Any]] = []
    vision_repairs: list[dict[str, Any]] = []
    _service_blockers(
        ("host_supervisor", "local_io_bridge", "vision"),
        services,
        vision_blockers,
        vision_repairs,
    )
    if supervisor is None or supervisor.get("state") != "up":
        vision_repairs.append(
            {
                "actionId": "start_host_supervisor_manual",
                "serviceId": "host_supervisor",
                "label": "Host Supervisor 수동 시작",
                "requiresConfirm": False,
                "manualCommand": "start_local.bat --background",
            }
        )
    elif bridge is None or bridge.get("state") != "up":
        vision_repairs.append(
            _repair_action(
                "restart_local_bridge",
                label="Local I/O Bridge 재시작",
                service_id="local_io_bridge",
            )
        )
    if bridge and bridge.get("state") == "up":
        host_vision = (
            bridge_payload.get("hostVision")
            if isinstance(bridge_payload.get("hostVision"), dict)
            else {}
        )
        if host_vision.get("schema") != "host_vision.status.v1":
            _blocker(
                vision_blockers,
                "host_vision_status_missing",
                "Windows 화면 관찰 브리지 상태가 없습니다.",
                service_id="local_io_bridge",
            )
        elif str(host_vision.get("state") or "") != "running":
            _blocker(
                vision_blockers,
                "host_vision_bridge_not_running",
                "Windows 화면 관찰 브리지가 실행 중이 아닙니다.",
                service_id="local_io_bridge",
            )
        if not bool(host_vision.get("captureEnabled")):
            _blocker(
                vision_blockers,
                "host_vision_capture_disabled",
                "Windows 화면 캡처가 비활성화되어 있습니다.",
                service_id="local_io_bridge",
            )
        if host_vision.get("lastErrorCode"):
            _warning(
                vision_warnings,
                "host_vision_last_request_failed",
                "최근 화면 관찰 요청이 근거를 만들지 못했습니다.",
                service_id="local_io_bridge",
            )

    vision_state = _capability_state(
        blockers=vision_blockers,
        warnings=vision_warnings,
    )
    return {
        "voiceLocal": {
            "state": local_state,
            "ready": local_state in {"ready", "degraded"},
            "blockers": local_blockers,
            "warnings": local_warnings,
            "dependencies": [
                _dependency(service_id, services.get(service_id))
                for service_id in ("host_supervisor", "local_io_bridge", "main_llm", "stt", "tts")
            ],
            "repairActions": _dedupe_actions(local_repairs),
        },
        "voiceDiscord": {
            "state": discord_state,
            "ready": discord_state in {"ready", "degraded"},
            "blockers": discord_blockers,
            "warnings": discord_warnings,
            "dependencies": [
                _dependency(service_id, services.get(service_id))
                for service_id in ("discord_bot", "main_llm", "stt", "tts")
            ],
            "repairActions": _dedupe_actions(discord_repairs),
        },
        "screenVision": {
            "state": vision_state,
            "ready": vision_state in {"ready", "degraded"},
            "blockers": vision_blockers,
            "warnings": vision_warnings,
            "dependencies": [
                _dependency(service_id, services.get(service_id))
                for service_id in ("host_supervisor", "local_io_bridge", "vision")
            ],
            "repairActions": _dedupe_actions(vision_repairs),
        },
    }


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        action_id = str(action.get("actionId") or "")
        if not action_id or action_id in seen:
            continue
        seen.add(action_id)
        result.append(deepcopy(action))
    return result


def attach_voice_capabilities(health: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(health)
    result["capabilities"] = build_voice_capabilities(result)
    return result


__all__ = ["attach_voice_capabilities", "build_voice_capabilities"]
