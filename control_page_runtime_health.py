from __future__ import annotations

from typing import Any

from evelyn_core.text import clean_text


def is_control_plane_service_ready_state(state: str | None) -> bool:
    value = clean_text(str(state or "")).lower()
    if not value:
        return False
    if "not " in value:
        return False
    if value in {
        "up",
        "ready",
        "running",
        "online",
        "healthy",
        "active",
        "alive",
        "ok",
        "okay",
    }:
        return True
    return False


def is_control_api_ready_from_runtime_services(runtime_services: dict[str, Any] | None) -> bool:
    if not isinstance(runtime_services, dict):
        return False
    return bool(
        runtime_services.get("botApiHttpReady")
        and is_control_plane_service_ready_state(clean_text(str(runtime_services.get("botApiState") or "")))
    )


def build_control_page_runtime_summary(
    *,
    bot_ready: bool,
    voyager_ready: bool,
    codex_required: bool,
    codex_ready: bool | None,
    bot_api_port_open: bool | None = None,
    bot_api_http_ready: bool | None = None,
    bot_api_state: str | None = None,
    bot_api_reason_code: str | None = None,
    bot_api_error: str | None = None,
) -> str:
    bot_api_state = clean_text(str(bot_api_state or ""))
    bot_api_reason_code = clean_text(str(bot_api_reason_code or ""))
    bot_api_error = clean_text(str(bot_api_error or ""))
    if bot_ready:
        bot_label = "bot ready"
    elif bot_api_port_open is False:
        bot_label = "bot down (8798 port closed)"
    elif bot_api_http_ready is False:
        if bot_api_reason_code.startswith("CP_BOT_HTTP_"):
            bot_label = f"bot down ({bot_api_reason_code.replace('CP_BOT_HTTP_', '8798 HTTP-')})"
        else:
            bot_label = "bot down (8798 HTTP contract fail)"
    elif bot_api_state:
        bot_label = "bot down (8798 contract fail)"
    elif bot_api_error:
        bot_label = "bot down (8798 contract fail)"
    else:
        bot_label = "bot down (8798 contract fail)"

    parts = [
        bot_label,
        "voyager ready" if voyager_ready else "minecraft idle",
    ]
    if codex_required:
        parts.append("codex ready" if codex_ready else "codex standby")
    return " | ".join(parts)


def build_control_page_runtime_health(*, services: dict[str, Any] | None) -> dict[str, Any]:
    service_map = dict(services or {})
    bot_ready = is_control_api_ready_from_runtime_services(service_map)
    runtime_status_stale = bool(service_map.get("runtimeStatusStale"))
    runtime_status_expired = bool(service_map.get("runtimeStatusExpired"))
    runtime_status_refreshing = bool(service_map.get("runtimeStatusRefreshing"))
    runtime_status_age_sec = service_map.get("runtimeStatusAgeSec")
    bot_api_port_open = bool(service_map.get("botApiPortOpen"))
    bot_api_http_ready = bool(service_map.get("botApiHttpReady"))
    bot_api_state = clean_text(str(service_map.get("botApiState") or "down"))
    bot_api_reason = clean_text(
        str(
            service_map.get("botApiReason")
            or service_map.get("botApiState")
            or service_map.get("botApiError")
            or ""
        )
    )
    bot_api_error = clean_text(str(service_map.get("botApiError") or ""))
    main_ready = bool(service_map.get("mainReady"))
    router_ready = bool(service_map.get("routerReady"))
    sub_ready = bool(service_map.get("subReady"))
    tts_ready = bool(service_map.get("ttsReady"))
    voyager_ready = bool(service_map.get("voyagerReady"))
    codex_required = bool(service_map.get("codexRequired"))
    codex_ready_raw = service_map.get("codexReady")
    codex_ready = bool(codex_ready_raw) if isinstance(codex_ready_raw, bool) else False

    services_status: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    has_error = False

    def add_service_status(
        *,
        service_id: str,
        label: str,
        state: str,
        required: bool,
    ) -> None:
        services_status.append(
            {
                "id": service_id,
                "label": label,
                "state": state,
                "required": bool(required),
            }
        )

    def add_diagnostic(
        *,
        code: str,
        severity: str,
        message: str,
        details: str = "",
        service_ids: list[str] | None = None,
    ) -> None:
        nonlocal has_error
        payload_service_ids = service_ids or []
        if severity == "error":
            has_error = True
        diagnostics.append(
            {
                "code": code,
                "severity": severity,
                "message": message,
                "details": details,
                "serviceIds": payload_service_ids,
                "suggestedActions": [],
            }
        )

    bot_api_health_state = "down"
    if bot_api_port_open and not bot_api_http_ready:
        bot_api_health_state = "partial"
    elif bot_ready:
        bot_api_health_state = "up"

    add_service_status(service_id="bot_api", label="Bot API", state=bot_api_health_state, required=True)
    if not bot_ready:
        if bot_api_port_open is False:
            code = "CP_UP_BOT_DOWN"
            code_message = "Bot API is down (8798 port closed)."
            severity = "error"
        elif not bot_api_http_ready:
            if bot_api_reason_code := bot_api_reason:
                code = bot_api_reason_code
            else:
                code = "CP_BOT_PROXY_ERROR"
            if code.startswith("CP_BOT_HTTP_"):
                code_message = "Bot API contract returned an HTTP error."
            else:
                code_message = "Bot API contract HTTP response was unavailable."
            if code in {"CP_BOT_PROXY_ERROR", "CP_BOT_PROXY_TIMEOUT"}:
                severity = "error"
            else:
                severity = "warning" if bot_api_state == "partial" else "error"
        elif bot_api_state:
            code = bot_api_reason or "CP_BOT_STATE_NOT_READY"
            code_message = "Bot API contract returned non-ready service state."
            severity = "error"
        else:
            code = "BOT_API_PARTIAL"
            code_message = "Bot API is not ready."
            severity = "warning"
        if code == "CP_BOT_PROXY_ERROR" and bot_api_error:
            code_message = f"Bot API proxy error: {bot_api_error}"
        if code == "CP_BOT_PROXY_TIMEOUT":
            code_message = "Bot API contract check timed out."
        elif code == "CP_BOT_STATE_NOT_DICT":
            code_message = "Bot API contract returned non-dict payload."
        add_diagnostic(
            code=code,
            severity=severity,
            message=code_message,
            details=bot_api_error or bot_api_state or code,
            service_ids=["bot_api"],
        )
    if runtime_status_expired:
        age_text = f"{runtime_status_age_sec}s" if isinstance(runtime_status_age_sec, (int, float)) else "unknown"
        add_diagnostic(
            code="CP_CONTROL_RUNTIME_EXPIRED",
            severity="error",
            message="Runtime service cache is expired and will be refreshed on next attempt.",
            details=f"age={age_text}, refreshing={runtime_status_refreshing}",
            service_ids=["bot_api", "main_llm", "router_llm", "sub_llm", "tts", "voyager"],
        )
    elif runtime_status_stale:
        age_text = f"{runtime_status_age_sec}s" if isinstance(runtime_status_age_sec, (int, float)) else "unknown"
        add_diagnostic(
            code="CP_CONTROL_RUNTIME_STALE",
            severity="warning",
            message="Runtime service cache is stale; refreshing in background.",
            details=f"age={age_text}, refreshing={runtime_status_refreshing}",
            service_ids=["bot_api", "main_llm", "router_llm", "sub_llm", "tts", "voyager"],
        )

    add_service_status(service_id="main_llm", label="Main LLM", state="up" if main_ready else "down", required=True)
    if not main_ready:
        add_diagnostic(
            code="CP_MAIN_LLM_DOWN",
            severity="error",
            message="Main LLM is not responding.",
            details="main_llm",
            service_ids=["main_llm"],
        )

    add_service_status(service_id="router_llm", label="Router LLM", state="up" if router_ready else "down", required=True)
    if not router_ready:
        add_diagnostic(
            code="CP_ROUTER_LLM_DOWN",
            severity="error",
            message="Router LLM is not responding.",
            details="router_llm",
            service_ids=["router_llm"],
        )

    add_service_status(service_id="sub_llm", label="Sub LLM", state="up" if sub_ready else "down", required=True)
    if not sub_ready:
        add_diagnostic(
            code="CP_SUB_LLM_DOWN",
            severity="error",
            message="Sub LLM is not responding.",
            details="sub_llm",
            service_ids=["sub_llm"],
        )

    add_service_status(service_id="tts", label="TTS", state="up" if tts_ready else "down", required=True)
    if not tts_ready:
        add_diagnostic(
            code="CP_TTS_DOWN",
            severity="error",
            message="TTS is not responding.",
            details="tts",
            service_ids=["tts"],
        )

    add_service_status(service_id="voyager", label="Voyager", state="up" if voyager_ready else "down", required=False)

    if codex_required:
        add_service_status(
            service_id="codex_gateway",
            label="Codex Gateway",
            state="up" if codex_ready else "down",
            required=True,
        )
        if not codex_ready:
            add_diagnostic(
                code="CP_CODEX_GATEWAY_DOWN",
                severity="error",
                message="Codex Gateway is not ready.",
                details="codex_gateway",
                service_ids=["codex_gateway"],
            )

    overall_state = "up"
    summary = "Control-Page and Bot API are responsive."
    if diagnostics:
        if has_error:
            overall_state = "down"
            summary = clean_text(diagnostics[0].get("message") or "Runtime health check reported errors.")
        else:
            overall_state = "degraded"
            summary = clean_text(diagnostics[0].get("message") or "Runtime has service warnings.")
        if len(diagnostics) > 1:
            summary = f"{summary} (+ {len(diagnostics) - 1} more issue(s))"

    return {
        "ok": bot_ready,
        "overallState": overall_state,
        "summary": clean_text(summary),
        "diagnostics": diagnostics,
        "services": services_status,
    }
